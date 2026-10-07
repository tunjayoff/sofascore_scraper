"""
Eski düzendeki bir maça yazma: v3'e yükseltme (plan maddesi ST-20; docs/design/01-storage.md bölüm 5.3 ve
5.4 adım 3) ve hazırlık alanının sahiplerine göre temizlenmesi (karar S16, bölüm 9.3).

Yükseltmenin ölçütleri:

  * Eski düzen ağacı bayt bayt ve zaman zaman aynı kalır: hiçbir dosyasına dokunulmaz, hiçbiri silinmez.
  * Yükseltilen maçın mantıksal dökümü (tests/store_dump.py) değişmez; katalog satırlarında yalnızca depolama
    sütunları (`layout`, `path`, `legacy_path`, `sig`) değişir.
  * Katalog, ağacın sıfırdan kurulmuş haline eşittir ve derin doğrulama temizdir.
"""
from __future__ import annotations

import datetime as dt
import gc
import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

import store_dump
import store_fixtures as sf
from sofascore_scraper.store import EventQuery, Store, StoreError, open_store
from sofascore_scraper.store import codec, derive, files, layout, manifest
from sofascore_scraper.store import events as events_mod
from sofascore_scraper.store import lease as lease_mod
from sofascore_scraper.store.legacy import LegacyReader
from sofascore_scraper.store.lease import LeaseManager
from test_store_put import (
    ARS,
    LIV,
    at,
    basic_of,
    consistent,
    event_file,
    failed,
    gone,
    manifest_of,
    ok,
    pending,
    staging,
)

UTC = dt.timezone.utc
BRE = sf.event_id(sf.PL_BRE)
AVL = sf.event_id(sf.PL_AVL)

STORAGE_COLUMNS = ("layout", "path", "legacy_path", "sig")


# --- yardımcılar --------------------------------------------------------------------------------------

def tree_state(root: Path, skip: Tuple[str, ...] = (".meta", "v3", "changes")) -> Dict[str, Tuple[str, int]]:
    """Eski düzen ağacının tamamı: göreli yol → (içerik özeti ya da "dir", mtime_ns)."""
    out: Dict[str, Tuple[str, int]] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel.split("/", 1)[0] in skip:
            continue
        digest = "dir" if path.is_dir() else codec.sha256_hex(path.read_bytes())
        out[rel] = (digest, path.stat().st_mtime_ns)
    return out


def catalog_rows(store: Store, event_id: int) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    conn = store._catalog.connection()
    event = dict(conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())
    slices = {"/".join(p for p in (r["key"], r["sub"]) if p): dict(r) for r in conn.execute(
        "SELECT * FROM event_slices WHERE event_id = ?", (event_id,))}
    return event, slices


def legacy_ids(store: Store) -> List[int]:
    return [int(r[0]) for r in store._catalog.connection().execute(
        "SELECT id FROM events WHERE layout = 'legacy' ORDER BY id")]


@pytest.fixture(params=["canonical", "legacy"])
def fx(request: pytest.FixtureRequest, tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture(request.param, tmp_path / "data")


@pytest.fixture
def canonical(tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture("canonical", tmp_path / "data")


@pytest.fixture
def old_forms(tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture("legacy", tmp_path / "data")


# --- yükseltmenin kendisi -----------------------------------------------------------------------------

def test_promotion_alone_changes_only_the_storage_columns(fx: sf.LegacyFixture) -> None:
    """Her eski düzen biçimindeki her maç: yükseltme, katalogdaki hiçbir bilgiyi ve dökümü değiştirmez."""
    store = open_store(fx.data_dir)
    reader = LegacyReader(fx.data_dir)
    ids = legacy_ids(store)
    assert len(ids) >= 9
    before_tree = tree_state(fx.data_dir)
    before_dump = store_dump.dump(fx.data_dir, fx.leagues)
    before_rows = {event_id: catalog_rows(store, event_id) for event_id in ids}

    for event_id in ids:
        event = reader.read_event(reader.event_dir_at(before_rows[event_id][0]["path"]), payloads=True)
        with store._catalog.write():
            found = events_mod.promote_legacy(fx.data_dir, event, label="put")
            assert store.catalog.index_event(event_id) == "v3"
        assert found.migrated_from == event.path and manifest_of(store, event_id) == found

    assert tree_state(fx.data_dir) == before_tree  # eski ağaç bayt bayt ve zaman zaman aynı
    assert store_dump.diff(before_dump, store_dump.dump(fx.data_dir, fx.leagues)) == []
    for event_id in ids:
        old_event, old_slices = before_rows[event_id]
        new_event, new_slices = catalog_rows(store, event_id)
        for name in old_event:
            if name not in STORAGE_COLUMNS:
                assert new_event[name] == old_event[name], (event_id, name)
        assert (new_event["layout"], new_event["path"], new_event["legacy_path"]) == ("v3", None, old_event["path"])
        assert set(new_slices) == set(old_slices)
        for key, old in old_slices.items():
            new = new_slices[key]
            if old["has_payload"]:  # eski satırda sıkıştırılmamış boyut bilinmez, saklanan boyut dosyanınkidir
                assert new["raw_bytes"] is not None and new["stored_bytes"] is not None
            assert {k: v for k, v in new.items() if k not in ("raw_bytes", "stored_bytes")} == {
                k: v for k, v in old.items() if k not in ("raw_bytes", "stored_bytes")}, (event_id, key)
    assert staging(store) == []
    consistent(store)
    deep = store.catalog.verify(deep=True)
    assert {s.event_id for s in deep.superseded if s.by == "v3"} == set(ids)


def test_put_promotes_a_legacy_event_and_then_applies_the_write(canonical: sf.LegacyFixture) -> None:
    store = open_store(canonical.data_dir)
    before_tree = tree_state(canonical.data_dir)
    before = store_dump.dump(canonical.data_dir, canonical.leagues)["events"][str(ARS)]
    legacy_row = store.events.get(ARS)
    stats = {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": [{"key": "new"}]}]}]}
    store.events._clock = lambda: at(30)  # manifestin `updated_at`'i yazmanın yapıldığı andır

    result = store.events.put(ARS, {"statistics": ok(stats, at()), "lineups": gone(at())})

    assert result == events_mod.PutResult(created=False, event_written=False, superseded=False,
                                          written=("statistics",), change_seq=None, promoted=True)
    assert tree_state(canonical.data_dir) == before_tree
    row = store.events.get(ARS)
    assert (row.layout, row.path, row.legacy_path) == ("v3", None, legacy_row.path)
    assert (row.first_seen_at, row.observed_at, row.listed_in) == (
        legacy_row.first_seen_at, legacy_row.observed_at, legacy_row.listed_in)
    found = manifest_of(store, ARS)
    assert found.migrated_from == legacy_row.path and found.updated_at == at(30)
    assert store.events.get(ARS).updated_at == int(at(30).timestamp())
    assert found.created_at == dt.datetime.fromtimestamp(legacy_row.first_seen_at, UTC)
    names = sorted(p.name for p in event_file(store, ARS).parent.iterdir())
    assert names == sorted(["manifest.json", "event.json.gz"] + [f"{k}.json.gz" for k in sf.REQUIRED_SLICES])
    # okuma artık v3'ten: eski dosya sonradan değişse de saklanan yük aynıdır
    assert store.events.payload(ARS, "statistics") == stats
    after = store_dump.dump(canonical.data_dir, canonical.leagues)["events"][str(ARS)]
    changed = store_dump.diff(before, after)
    assert len(changed) == 2 and all(".statistics.sha256" in c or ".lineups.empty_count" in c for c in changed)

    again = store.events.put(ARS, {"incidents": failed("5xx", 503)})
    assert not again.promoted and store.events.get(ARS).legacy_path == legacy_row.path
    assert tree_state(canonical.data_dir) == before_tree
    consistent(store)


def test_promotion_keeps_markers_counters_and_the_sticky_flag(canonical: sf.LegacyFixture) -> None:
    store = open_store(canonical.data_dir)
    void = sf.event_id(sf.NBA_VOID)  # canonical: sonradan iptal, observation.json'da status_regressed
    kept: Dict[int, Any] = {}
    for event_id in (BRE, void, sf.event_id(sf.WIM_RET)):
        kept[event_id] = store_dump.dump(canonical.data_dir)["events"][str(event_id)]
        payload = store.events.payload(event_id)
        observed = store.events.get(event_id).observed_at
        when = dt.datetime.fromtimestamp(observed, UTC) if observed is not None else None
        # aynı yük, aynı gözlem anı: yalnızca yükseltme (gözlemi olmayan maç bir gözlem kazanır)
        assert store.events.put(event_id, {"event": ok(payload, when)}).promoted

    for event_id, before in kept.items():
        after = store_dump.dump_v3_event(canonical.data_dir, event_id)
        assert after["slices"] == before["slices"], event_id
        if before["observation"] is not None:
            assert after["observation"] == before["observation"], event_id
    assert store.events.get(void).status_regressed
    assert any(s.unverified_empty_count for s in store.events.slices(BRE))
    consistent(store)


def test_old_forms_are_promoted_from_the_copy_the_catalog_prefers(old_forms: sf.LegacyFixture) -> None:
    store = open_store(old_forms.data_dir)
    before_tree = tree_state(old_forms.data_dir)
    rows = {r.id: r for r in store.events.iter(EventQuery(has_details=True))}
    stats_of = {e: store.events.payload(e, "statistics") for e in rows}

    # ARS iki yerde durur (lig dizini ve bayat düz kopya); LIV birleşik dosyalı; BRE yalnızca birleşik dosya;
    # AVL'nin statistics dosyası yarım
    for event_id in (ARS, LIV, BRE, AVL):
        result = store.events.put(event_id, {"lineups": gone(at())})
        assert result.promoted, event_id
        assert store.events.get(event_id).legacy_path == rows[event_id].path
        assert manifest_of(store, event_id).migrated_from == rows[event_id].path

    assert tree_state(old_forms.data_dir) == before_tree
    assert rows[ARS].path.count("/") == 3  # lig dizinindeki kopya; düz kopya kullanılmadı
    assert store.events.payload(ARS, "statistics") == stats_of[ARS]
    for event_id in (LIV, BRE):  # birleşik dosyadaki dilimler ayrı dosyalara çıktı
        assert store.events.payload(event_id, "statistics") == stats_of[event_id] is not None
        assert event_file(store, event_id, "statistics").is_file()
    broken = store.events.slice(AVL, "statistics")
    assert (broken.state, broken.has_payload, broken.error.reason) == ("error", False, "corrupt")
    assert not event_file(store, AVL, "statistics").exists()
    # bozuk dilim yeniden çekilince düzelir
    assert store.events.put(AVL, {"statistics": ok({"statistics": [1]})}).written == ("statistics",)
    assert store.events.slice(AVL, "statistics").error is None
    consistent(store)


def test_extra_files_stay_in_the_legacy_directory(canonical: sf.LegacyFixture) -> None:
    """Dilim, gözlem ya da işaret dosyası olmayan girdiler kopyalanmaz: v3 dizininde manifestin bilmediği dosya olmaz."""
    store = open_store(canonical.data_dir)
    directory = Path(canonical.data_dir) / store.events.get(ARS).path
    (directory / "notes.txt").write_bytes(b"kept where it is")
    (directory / ".basic.json.ab12cd34.tmp").write_bytes(b"{")
    (directory / "odds.json").write_bytes(b'{"markets": []}')  # kayıt defterinde olmayan dilim
    store.close()
    store = open_store(canonical.data_dir)

    assert store.events.put(ARS, {"lineups": gone()}).promoted

    names = sorted(p.name for p in event_file(store, ARS).parent.iterdir())
    assert names == sorted(["manifest.json", "event.json.gz"] + [f"{k}.json.gz" for k in sf.REQUIRED_SLICES])
    assert (directory / "notes.txt").read_bytes() == b"kept where it is" and (directory / "odds.json").exists()
    assert store.catalog.verify(deep=True).ok


def test_unusual_legacy_values_do_not_block_a_promotion(tmp_path: Path) -> None:
    data = tmp_path / "data"
    fixture = sf.build_fixture("canonical", data)
    directory = data / next(d.path for d in fixture.details if d.event_id == ARS)
    # Python'un json modülü NaN'i yazar ve okur; NaN kendine eşit değildir
    (directory / "statistics.json").write_text('{"statistics": [{"period": "ALL", "value": NaN, "groups": '
                                               '[{"statisticsItems": [{"key": "x"}]}]}]}', encoding="utf-8")
    (directory / "observation.json").write_text(json.dumps(
        {"observed_at_utc": "last tuesday", "change_ts": -5, "status_regressed": 1}), encoding="utf-8")
    (directory / "_slice_status.json").write_text(json.dumps(
        {"lineups": {"error": {"reason": "", "status": -1, "at": "2026-09-29T18:00:00", "count": 0}}}),
        encoding="utf-8")
    store = open_store(data)

    assert store.events.put(ARS, {"incidents": gone()}).promoted

    found = manifest_of(store, ARS)
    assert math.isnan(store.events.payload(ARS, "statistics")["statistics"][0]["value"])
    assert (found.observation.observed_at, found.observation.change_ts, found.observation.status_regressed) == (
        None, None, True)
    error = found.slices["lineups"].error
    assert (error.reason, error.status, error.count) == ("other", None, 1)
    consistent(store)


def test_a_promotion_that_cannot_be_verified_leaves_everything_as_it_was(canonical: sf.LegacyFixture,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Bölüm 5.4, adım 3: geri okunan dosya eski yükle aynı değilse hazırlık dizini silinir, eski dizin durur."""
    store = open_store(canonical.data_dir)
    before_tree = tree_state(canonical.data_dir)
    before_row = store.events.get(ARS)
    real = codec.read_raw

    def flipped(path: Any) -> bytes:
        raw = real(path)
        return raw.replace(b"Arsenal", b"Arsenel") if os.path.basename(path) == "event.json.gz" else raw

    monkeypatch.setattr(codec, "read_raw", flipped)
    with pytest.raises(StoreError, match="doğrulanamadı") as raised:
        store.events.put(ARS, {"lineups": gone()})
    assert not raised.value.fatal

    monkeypatch.setattr(codec, "read_raw", real)
    assert store.events.get(ARS) == before_row and before_row.layout == "legacy"
    assert tree_state(canonical.data_dir) == before_tree and staging(store) == [] and pending(store) == []
    assert not os.path.exists(layout.resolve(canonical.data_dir, layout.V3_DIR))
    # aynı çağrı sonradan başarır
    assert store.events.put(ARS, {"lineups": gone()}).promoted
    consistent(store)


def test_manifest_from_legacy_needs_the_payloads(canonical: sf.LegacyFixture) -> None:
    reader = LegacyReader(canonical.data_dir)
    candidate = next(c for c in reader.event_dirs() if c.name == str(ARS))
    with pytest.raises(ValueError):
        events_mod.manifest_from_legacy(reader.read_event(candidate, payloads=False))
    with pytest.raises(ValueError):
        events_mod.promote_legacy(canonical.data_dir, reader.read_event(candidate, payloads=False), label="put")
    found, encoded = events_mod.manifest_from_legacy(reader.read_event(candidate, payloads=True))
    assert manifest.validate(found) == [] and set(encoded) == {"event", *sf.REQUIRED_SLICES}
    assert derive.epoch_seconds(found.slices["event"].fetched_at) == sf.BASE_MTIME
    assert not os.path.exists(layout.resolve(canonical.data_dir, layout.META_DIR))  # hiçbir şey yazmaz


# --- hazırlık dizinleri: sahibinin adı (karar S16) ------------------------------------------------------

def test_staging_entries_carry_the_name_of_their_holder(canonical: sf.LegacyFixture,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    store = open_store(canonical.data_dir)
    labels: List[str] = []
    real = files.new_staging_dir

    def spy(data_dir: Any, label: str = "") -> str:
        labels.append(label)
        path = real(data_dir, label)
        assert os.path.basename(path).startswith(f"{label}.") and files.staging_holder(os.path.basename(path)) == label
        return path

    monkeypatch.setattr(files, "new_staging_dir", spy)
    ids = legacy_ids(store)

    store.events.put(ids[0], {"lineups": gone()})  # kilitsiz
    with store.lease("writer", purpose="job"):
        store.events.put(ids[1], {"lineups": gone()})  # yükseltme
        store.events.put(9_000_001, {"event": ok(basic_of(event_id=9_000_001))})  # yeni maç
    with store.lease("live", purpose="watch"):
        store.events.put(ids[2], {"lineups": gone()})
    with store.lease("watcher:tennis"):
        store.events.put(ids[3], {"lineups": gone()})
    # yazar kilidini aynı dizinin başka bir yöneticisi (iş deposununki) almış olabilir
    other = LeaseManager.for_data_dir(store.data_dir)
    held = other.acquire("writer")
    try:
        store.events.put(ids[4], {"lineups": gone()})
    finally:
        held.release()
    store.events.put(ids[5], {"lineups": gone()})

    assert labels == ["put", "writer", "writer", "live", "put", "writer", "put"]
    assert staging(store) == []
    consistent(store)


# --- hazırlık alanının temizlenmesi: bir kilit yalnızca kendi girdilerini siler -------------------------

def tmp_entries(data_dir: Any) -> List[str]:
    try:
        return sorted(os.listdir(layout.resolve(data_dir, layout.TMP_DIR)))
    except FileNotFoundError:
        return []


def test_held_here_follows_the_leases_of_this_process(tmp_path: Path) -> None:
    first = LeaseManager.for_data_dir(tmp_path / "data")
    second = LeaseManager.for_data_dir(tmp_path / "data")
    elsewhere = LeaseManager.for_data_dir(tmp_path / "other")
    assert first.data_dir == os.path.abspath(tmp_path / "data")
    assert LeaseManager(tmp_path / "locks").data_dir is None  # bir veri dizininin kilit dizini değil

    lease = first.acquire("writer")
    assert first.held_here("writer") and second.held_here("writer") and not elsewhere.held_here("writer")
    assert not first.held_here("live") and not first.held_here("maintenance")
    lease.release()
    assert not first.held_here("writer") and not second.held_here("writer")

    dropped = second.acquire("live")
    assert first.held_here("live")
    del dropped  # bırakılmadan çöpe giden kilit de düşer
    gc.collect()
    assert not first.held_here("live")


def _aged(path: str, seconds: float) -> str:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))
    return os.path.basename(path)


def test_a_lease_purges_only_the_staging_entries_of_its_own_holder(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    data_dir = store.data_dir
    day = lease_mod.STAGING_KEEP_SECONDS

    def populate() -> Dict[str, str]:
        made = {
            "writer": os.path.basename(files.new_staging_dir(data_dir, "writer")),
            "writer_old": _aged(files.new_staging_dir(data_dir, "writer"), 3 * day),
            "live": os.path.basename(files.new_staging_dir(data_dir, "live")),
            "live_old": _aged(files.new_staging_dir(data_dir, "live"), 3 * day),
            "export": os.path.basename(files.new_staging_dir(data_dir, "export")),
            "export_old": _aged(files.new_staging_dir(data_dir, "export"), day + 60),
            "put": os.path.basename(files.new_staging_dir(data_dir, "put")),
            "put_old": _aged(files.new_staging_dir(data_dir, "put"), day + 60),
            "plain": os.path.basename(files.new_staging_dir(data_dir)),
            "plain_old": _aged(files.new_staging_dir(data_dir), 2 * day),
        }
        files.write_bytes(os.path.join(layout.resolve(data_dir, layout.TMP_DIR), made["writer"], "x.json.gz"), b"x")
        return made

    def left(made: Dict[str, str]) -> List[str]:
        present = set(tmp_entries(data_dir))
        return sorted(label for label, name in made.items() if name in present)

    made = populate()
    trash = layout.resolve(data_dir, layout.TRASH_DIR)
    os.makedirs(os.path.join(trash, "16837335.abc"))
    everything = sorted(made)

    for name in ("maintenance", "sinks", "watcher:tennis"):  # bu kilitler hiçbir şeyi silmez
        with store.lease(name):
            assert left(made) == everything
    assert os.listdir(trash) == ["16837335.abc"]

    with store.lease("live"):
        assert left(made) == sorted(set(everything) - {"live", "live_old"})
        assert os.listdir(trash) == ["16837335.abc"]

    made = populate()
    with store.lease("writer", purpose="job"):
        # kendi girdileri (yaşına bakılmadan), kilide bağlı olmayan eski girdiler ve çöp; canlı servisinkiler durur
        assert left(made) == ["export", "live", "live_old", "plain", "put"]
        assert os.listdir(trash) == []
    assert files.purge_staging(data_dir) >= 5 and tmp_entries(data_dir) == []


def test_purge_staging_filters_by_holder_and_age(tmp_path: Path) -> None:
    data = tmp_path / "data"
    assert files.purge_staging(data, "writer") == 0  # .meta henüz yok
    names = {label: os.path.basename(files.new_staging_dir(data, label)) for label in ("writer", "live", "a.b", "")}
    old = _aged(files.new_staging_dir(data, "export"), 100)
    tmp = Path(layout.resolve(data, layout.TMP_DIR))
    (tmp / "writer").write_bytes(b"x")  # noktasız ad: etiketi yok

    assert [files.staging_holder(n) for n in (names["writer"], names["a.b"], names[""], "writer")] == [
        "writer", "a.b", "", ""]
    assert files.purge_staging(data, "export", older_than=3600) == 0  # yeterince eski değil
    assert files.purge_staging(data, older_than=50, skip=("export",)) == 0
    assert files.purge_staging(data, "export", older_than=50) == 1 and not (tmp / old).exists()
    assert files.purge_staging(data, "writer") == 1
    assert sorted(os.listdir(tmp)) == sorted([names["live"], names["a.b"], names[""], "writer"])
    assert files.purge_staging(data, skip=("live", "a.b")) == 2
    assert sorted(os.listdir(tmp)) == sorted([names["live"], names["a.b"]])
    assert files.purge_staging(data) == 2 and os.listdir(tmp) == []


def test_a_failing_purge_does_not_refuse_the_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   caplog: pytest.LogCaptureFixture) -> None:
    store = open_store(tmp_path / "data")

    def refuse(*args: Any, **kwargs: Any) -> int:
        raise StoreError("izin yok", errno_code=13)

    monkeypatch.setattr(files, "purge_staging", refuse)
    with caplog.at_level(logging.WARNING):
        with store.lease("writer") as lease:
            assert lease.held and store.lease_holder("writer") is not None
    assert [r.getMessage() for r in caplog.records if r.name == "Store" and r.levelno >= logging.WARNING] == [
        "Leftover staging entries could not be removed (writer): izin yok"]
