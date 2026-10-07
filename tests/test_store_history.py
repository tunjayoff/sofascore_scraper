"""
Dilim geçmişi: `EventStore.put(keep_history=...)`, geçmiş dosyaları, `slice_history` dizini ve yeniden kurulması,
`HistoryStore` (index, snapshots, snapshot, prune), yarım kuyruk ve doğrulamanın I8 kuralı (plan maddesi ST-26;
docs/design/01-storage.md bölüm 2.3, 3.4 adım 7, 3.6 ve 4.2).

Ölçütler: değişen N ve değişmeyen M yük N üye verir; dosyanın tamamı geçerli, çok üyeli bir gzip akışıdır;
n. üye bayt aralığından tek başına açılır; son üyenin içinde kesilmiş dosyada ilk N-1 üye okunur ve sonraki
ekleme dosyayı onarır; yeniden kurma `slice_history`'yi aynen verir; budama en yeni anlık görüntüyü tutar.
Ağ yok.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import os
import sys
import zlib
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

import sofascore_scraper.store
import store_fixtures as sf
from sofascore_scraper.slices import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, Outcome
from sofascore_scraper.store import (
    HistoryStore,
    LayoutError,
    Ref,
    Snapshot,
    SnapshotInfo,
    Store,
    StoreError,
    open_store,
)
from sofascore_scraper.store import codec, history, layout, manifest, verify
from sofascore_scraper.store import events as events_mod

UTC = dt.timezone.utc
T0 = dt.datetime.fromtimestamp(sf.FIXTURE_NOW, UTC)  # 2026-10-01T12:00:00Z
ARS = sf.event_id(sf.PL_ARS)
ODDS = "odds_all"


def at(minutes: float = 0) -> dt.datetime:
    return T0 + dt.timedelta(minutes=minutes)


def ok(data: Any, when: Optional[dt.datetime] = None) -> Outcome:
    return Outcome(SLICE_OK, data, fetched_at=when)


def odds(price: float, market: str = "1x2") -> Dict[str, Any]:
    return {"markets": [{"marketName": market, "choices": [{"name": "1", "fractionalValue": str(price)}]}]}


def basic() -> Dict[str, Any]:
    return sf.basic_payload(sf.PL_ARS)


def history_file(store: Store, key: str = ODDS, sub: str = "1", event_id: int = ARS) -> Path:
    return Path(layout.resolve(store.data_dir, layout.history_path(layout.event_dir(event_id), key, sub)))


def rows(store: Store) -> List[Tuple[Any, ...]]:
    return [tuple(r) for r in store._catalog.connection().execute(
        "SELECT kind, entity_id, key, sub, n, fetched_at, sha256, offset, length FROM slice_history "
        "ORDER BY kind, entity_id, key, sub, n")]


def mark_of(store: Store, name: str = f"{ODDS}/1", event_id: int = ARS) -> Optional[manifest.HistoryMark]:
    found = manifest.read_manifest(
        layout.resolve(store.data_dir, layout.manifest_path(layout.event_dir(event_id))))
    return found.slices[name].history


def consistent(store: Store) -> None:
    """Katalog yeniden kurulmuş haline eşit; hızlı ve derin doğrulama (I8 dahil) temiz; yarım yazma yok."""
    assert store.catalog.diff_from_rebuild() == []
    for deep in (False, True):
        report = store.catalog.verify(deep=deep)
        assert "I8" in report.checked
        assert report.ok, [(i.invariant, i.kind, i.detail, i.path) for i in report.open_issues]
    assert store._catalog.connection().execute("SELECT count(*) FROM pending_writes").fetchone()[0] == 0


def put_odds(store: Store, price: float, minutes: float, *, sub: str = "1", keep: Any = (ODDS,),
             event: bool = False) -> events_mod.PutResult:
    outcomes: Dict[Any, Outcome] = {(ODDS, sub): ok(odds(price), at(minutes))}
    if event:
        outcomes["event"] = ok(basic(), at(minutes))
    return store.events.put(ARS, outcomes, keep_history=keep)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return open_store(tmp_path / "data")


@pytest.fixture
def written(store: Store) -> Store:
    """Beş anlık görüntülü bir geçmiş: 1.0 (dakika 0), 2.0, 3.0, 2.0, 4.0 (dakika 1-4)."""
    put_odds(store, 1.0, 0, event=True)
    for minute, price in enumerate((2.0, 3.0, 2.0, 4.0), start=1):
        put_odds(store, price, minute)
    return store


# --- yazma --------------------------------------------------------------------------------------------

def test_n_changed_and_m_unchanged_payloads_give_n_members(store: Store) -> None:
    result = put_odds(store, 1.0, 0, event=True)
    assert result.history == (f"{ODDS}/1",) and result.created
    prices = [1.0, 1.5, 1.5, 1.5, 2.0, 2.0, 1.0]  # değişen: 1.5, 2.0, 1.0 → üye sayısı 1 + 3
    appended = []
    for minute, price in enumerate(prices[1:], start=1):
        appended.append(put_odds(store, price, minute).history)
    assert appended == [(f"{ODDS}/1",), (), (), (f"{ODDS}/1",), (), (f"{ODDS}/1",)]

    index = store.history.index(Ref.event(ARS), ODDS, "1")
    assert [info.n for info in index] == [1, 2, 3, 4]
    assert [info.sha256 for info in index] == [codec.encode(odds(p)).sha256 for p in (1.0, 1.5, 2.0, 1.0)]
    assert [info.fetched_at for info in index] == [at(0), at(1), at(4), at(6)]
    assert all(isinstance(info, SnapshotInfo) for info in index)
    mark = mark_of(store)
    assert mark is not None and (mark.count, mark.last_sha256) == (4, index[-1].sha256)
    assert store.events.slice(ARS, ODDS, "1").history_count == 4
    consistent(store)


def test_the_whole_file_is_a_valid_multi_member_gzip_stream(written: Store) -> None:
    data = history_file(written).read_bytes()
    lines = gzip.decompress(data).decode("utf-8").splitlines()  # zcat'in gördüğü: JSON Lines
    assert len(lines) == 5
    parsed = [json.loads(line) for line in lines]
    assert [p["payload"] for p in parsed] == [odds(p) for p in (1.0, 2.0, 3.0, 2.0, 4.0)]
    assert [list(p) for p in parsed] == [["fetched_at", "sha256", "payload"]] * 5
    for item in parsed:  # özet, yük dosyasındaki kurallı baytlarınkiyle aynı
        assert item["sha256"] == codec.sha256_hex(codec.canonical_bytes(item["payload"]))
    assert parsed[0]["fetched_at"] == "2026-10-01T12:00:00+00:00"
    # Her üye kendi başına bir gzip akışıdır: zlib üye üye ilerler ve dosyanın sonunda biter
    pos, members = 0, 0
    while pos < len(data):
        inflater = zlib.decompressobj(31)
        inflater.decompress(data[pos:])
        assert inflater.eof
        pos = len(data) - len(inflater.unused_data)
        members += 1
    assert members == 5


def test_member_n_is_read_by_offset_and_length_alone(written: Store) -> None:
    data = history_file(written).read_bytes()
    found = rows(written)
    assert [r[4] for r in found] == [1, 2, 3, 4, 5]
    assert found[0][7] == 0 and sum(r[8] for r in found) == len(data)
    for (_kind, _id, _key, _sub, n, fetched, digest, offset, length) in found:
        line = json.loads(gzip.decompress(data[offset:offset + length]))
        assert line["sha256"] == digest and fetched == int(dt.datetime.fromisoformat(line["fetched_at"]).timestamp())

    snap = written.history.snapshot(Ref.event(ARS), ODDS, "1", 3)
    assert isinstance(snap, Snapshot) and (snap.n, snap.payload, snap.fetched_at) == (3, odds(3.0), at(2))
    raw = written.history.snapshot(Ref.event(ARS), ODDS, "1", 3, raw=True)
    assert raw is not None and raw.payload == codec.canonical_bytes(odds(3.0))
    assert written.history.snapshot(Ref.event(ARS), ODDS, "1", 6) is None
    assert written.history.snapshot(Ref.event(ARS), ODDS, "2", 1) is None
    with pytest.raises(ValueError):
        written.history.snapshot(Ref.event(ARS), ODDS, "1", 0)


def test_snapshots_in_order_with_since_and_raw(written: Store) -> None:
    ref = Ref.event(ARS)
    every = list(written.history.snapshots(ref, ODDS, "1"))
    assert [(s.n, s.payload) for s in every] == [(n, odds(p)) for n, p in enumerate((1.0, 2.0, 3.0, 2.0, 4.0), 1)]
    since = list(written.history.snapshots(ref, ODDS, "1", since=at(2).timestamp() + 0.5))
    assert [s.n for s in since] == [4, 5]
    assert [s.n for s in written.history.snapshots(ref, ODDS, "1", since=at(2).timestamp())] == [3, 4, 5]
    raw = list(written.history.snapshots(ref, ODDS, "1", raw=True))
    assert [s.payload for s in raw] == [codec.canonical_bytes(s.payload) for s in every]
    assert list(written.history.snapshots(ref, "odds_featured")) == []
    with pytest.raises(ValueError):
        list(written.history.snapshots(ref, ODDS, "1", since="yesterday"))  # type: ignore[arg-type]


def test_keep_history_covers_every_sub_of_a_key_and_the_slice_without_a_sub(store: Store) -> None:
    store.events.put(ARS, {"event": ok(basic(), at()), (ODDS, "1"): ok(odds(1.0), at()),
                           (ODDS, "2"): ok(odds(1.1), at()), "winning_odds": ok({"home": 1}, at()),
                           "statistics": ok({"statistics": [1]}, at())},
                     keep_history=[ODDS, "winning_odds"])
    assert history_file(store, ODDS, "1").exists() and history_file(store, ODDS, "2").exists()
    assert history_file(store, "winning_odds", "").name == "_.jsonl.gz" and history_file(store, "winning_odds", "").exists()
    assert not history_file(store, "statistics", "").exists()
    assert {(r[2], r[3]) for r in rows(store)} == {(ODDS, "1"), (ODDS, "2"), ("winning_odds", "")}
    assert [s.payload for s in store.history.snapshots(Ref.event(ARS), "winning_odds")] == [{"home": 1}]
    consistent(store)


def test_without_keep_history_nothing_is_kept_and_the_first_kept_put_takes_a_snapshot(store: Store) -> None:
    put_odds(store, 1.0, 0, keep=(), event=True)
    assert not history_file(store).exists() and rows(store) == []
    result = put_odds(store, 1.0, 1)  # yük aynı: dosya yazılmaz, ama geçmiş boş olduğu için ilk anlık görüntü
    assert result.written == () and result.history == (f"{ODDS}/1",)
    assert [s.fetched_at for s in store.history.snapshots(Ref.event(ARS), ODDS, "1")] == [at(1)]
    consistent(store)


def test_only_outcomes_with_data_are_kept(store: Store) -> None:
    put_odds(store, 1.0, 0, event=True)
    store.events.put(ARS, {(ODDS, "1"): Outcome(SLICE_EMPTY, None, reason="404", fetched_at=at(1)),
                           (ODDS, "2"): Outcome(SLICE_FAILED, None, reason="429", fetched_at=at(1))},
                     keep_history=[ODDS])
    assert [info.n for info in store.history.index(Ref.event(ARS), ODDS, "1")] == [1]
    assert not history_file(store, ODDS, "2").exists()
    hollow = store.events.put(ARS, {(ODDS, "1"): Outcome(SLICE_EMPTY, {}, reason="empty", fetched_at=at(2))},
                              keep_history=[ODDS])  # boş bir 200 gövdesi de bir yüktür
    assert hollow.history == (f"{ODDS}/1",)
    consistent(store)


def test_keep_history_is_validated_before_anything_is_written(store: Store) -> None:
    put_odds(store, 1.0, 0, event=True)
    before = history_file(store).read_bytes()
    with pytest.raises(ValueError):
        put_odds(store, 2.0, 1, keep=ODDS)  # bir dize, anahtar kümesi değil
    with pytest.raises(LayoutError):
        put_odds(store, 2.0, 1, keep=["Odds"])
    with pytest.raises(ValueError):
        put_odds(store, 2.0, 1, keep=None)
    assert history_file(store).read_bytes() == before
    assert store.events.payload(ARS, ODDS, "1") == odds(1.0)


def test_history_files_follow_the_umask(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX file modes")
    previous = os.umask(0o027)
    try:
        store = open_store(tmp_path / "data")
        put_odds(store, 1.0, 0, event=True)
        put_odds(store, 2.0, 1)
        assert history_file(store).stat().st_mode & 0o777 == 0o640
    finally:
        os.umask(previous)


def test_a_legacy_event_is_promoted_and_then_keeps_its_history(tmp_path: Path) -> None:
    fixture = sf.build_fixture("canonical", tmp_path / "data")
    store = open_store(fixture.data_dir)
    result = store.events.put(ARS, {(ODDS, "1"): ok(odds(1.0), at())}, keep_history=[ODDS])
    assert result.promoted and result.history == (f"{ODDS}/1",)
    assert [s.payload for s in store.history.snapshots(Ref.event(ARS), ODDS, "1")] == [odds(1.0)]
    consistent(store)


# --- yarım kuyruk ve yarım yazma ----------------------------------------------------------------------

def test_a_file_cut_inside_the_last_member_reads_n_minus_one_and_the_next_append_repairs_it(
        written: Store, caplog: pytest.LogCaptureFixture) -> None:
    path = history_file(written)
    last = rows(written)[-1]
    path.write_bytes(path.read_bytes()[: last[7] + last[8] // 2])  # son üyenin ortasında kesilmiş

    scan = history.scan_file(path)
    assert len(scan.members) == 4 and scan.torn and scan.good_end == last[7]
    written.catalog.rebuild()
    assert [info.n for info in written.history.index(Ref.event(ARS), ODDS, "1")] == [1, 2, 3, 4]
    assert [s.payload for s in written.history.snapshots(Ref.event(ARS), ODDS, "1")] == [
        odds(p) for p in (1.0, 2.0, 3.0, 2.0)]

    with caplog.at_level(logging.WARNING, logger="sofascore_scraper.store.history"):
        result = put_odds(written, 5.0, 10)
    assert result.history == (f"{ODDS}/1",)
    assert any("removed before the next append" in r.getMessage() for r in caplog.records)
    data = path.read_bytes()
    assert [json.loads(line)["payload"] for line in gzip.decompress(data).splitlines()] == [
        odds(p) for p in (1.0, 2.0, 3.0, 2.0, 5.0)]
    assert not history.scan_bytes(data).torn
    mark = mark_of(written)
    assert mark is not None and mark.count == 5
    consistent(written)


def test_an_append_interrupted_before_the_manifest_is_healed_by_the_next_put(
        written: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    real = manifest.write_manifest

    def disk_full(*args: Any, **kwargs: Any) -> None:
        raise StoreError("disk dolu", errno_code=28)

    monkeypatch.setattr(manifest, "write_manifest", disk_full)
    with pytest.raises(StoreError, match="disk dolu"):
        put_odds(written, 9.0, 10)
    monkeypatch.setattr(manifest, "write_manifest", real)
    assert len(history.scan_file(history_file(written)).members) == 6  # üye eklendi, manifest yazılamadı
    assert [info.n for info in written.history.index(Ref.event(ARS), ODDS, "1")] == [1, 2, 3, 4, 5]

    result = put_odds(written, 9.0, 11)  # aynı yük: yarım kalan yazma toparlanır, yeni üye eklenmez
    assert result.history == ()
    mark = mark_of(written)
    assert mark is not None and mark.count == 6 and mark.last_sha256 == codec.encode(odds(9.0)).sha256
    assert [info.n for info in written.history.index(Ref.event(ARS), ODDS, "1")] == [1, 2, 3, 4, 5, 6]
    put_odds(written, 10.0, 12)
    assert [info.n for info in written.history.index(Ref.event(ARS), ODDS, "1")] == list(range(1, 8))
    consistent(written)


def test_a_crash_after_the_append_is_healed_on_the_next_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = open_store(tmp_path / "data")
    put_odds(store, 1.0, 0, event=True)

    class Crash(Exception):
        pass

    def checkpoint(self: Any, step: str) -> None:
        if step == f"{events_mod.STEP_HISTORY}:{ODDS}/1":
            raise Crash(step)

    monkeypatch.setattr(events_mod.EventStore, "_checkpoint", checkpoint)
    with pytest.raises(Crash):
        put_odds(store, 2.0, 1)
    monkeypatch.undo()
    store.close()

    reopened = open_store(tmp_path / "data")  # açılıştaki uzlaştırma yarım yazmayı toparlar
    assert [s.payload for s in reopened.history.snapshots(Ref.event(ARS), ODDS, "1")] == [odds(1.0), odds(2.0)]
    mark = mark_of(reopened)
    assert mark is not None and mark.count == 2
    assert reopened.events.payload(ARS, ODDS, "1") == odds(2.0)
    consistent(reopened)


# --- dizin ve doğrulama -------------------------------------------------------------------------------

def test_a_rebuild_reproduces_slice_history(written: Store) -> None:
    put_odds(written, 1.0, 5, sub="2")
    before = rows(written)
    assert len(before) == 6
    assert written.catalog.diff_from_rebuild() == []
    for mode in ("in_place", "recreate"):
        written.catalog.rebuild(mode=mode)
        assert rows(written) == before
    written._catalog.connection().execute("DELETE FROM slice_history")  # katalog dışından silinen satırlar
    assert written.catalog.reconcile(deep=True).events_indexed >= 1
    assert rows(written) == before


def test_verify_reports_and_repairs_history_faults(written: Store) -> None:
    path = history_file(written)
    data = bytearray(path.read_bytes())
    third = rows(written)[2]
    data[third[7] + third[8] // 2] ^= 0xFF  # üçüncü üyenin ortasında bir bayt
    path.write_bytes(bytes(data))

    quick = written.catalog.verify()
    assert quick.ok  # hızlı kip dosya okumaz: boyut ve numaralar tutuyor
    deep = written.catalog.verify(deep=True)
    assert ("I8", verify.KIND_HISTORY_MEMBER) in [(i.invariant, i.kind) for i in deep.open_issues]
    assert ("I8", verify.KIND_HISTORY_ROWS) in [(i.invariant, i.kind) for i in deep.open_issues]
    repaired = written.catalog.verify(deep=True, repair=True)
    assert all(i.repaired for i in repaired.issues if i.invariant == "I8")
    assert [r[4] for r in rows(written)] == [1, 2]  # bozuk üyeden sonrası okunamaz: satırları kalmaz
    assert written.catalog.verify(deep=True).ok

    path.unlink()
    quick = written.catalog.verify()
    assert [(i.invariant, i.kind, i.event_id) for i in quick.open_issues] == [
        ("I8", verify.KIND_HISTORY_FILE, ARS)]
    written.catalog.verify(repair=True)
    assert rows(written) == [] and written.catalog.verify(deep=True).ok


def test_the_history_directory_is_no_unknown_file(written: Store) -> None:
    directory = layout.event_dir(ARS)
    assert all(not name.startswith("_history") for name in verify.directory_files(str(written.data_dir), directory))
    report = written.catalog.verify(deep=True)
    assert not [i for i in report.issues if i.invariant == "I9"]


def test_delete_removes_the_history_rows(written: Store) -> None:
    assert written.events.delete(ARS)
    assert rows(written) == []
    consistent(written)


# --- budama -------------------------------------------------------------------------------------------

def test_prune_keeps_the_newest_snapshot_and_renumbers(written: Store) -> None:
    put_odds(written, 7.0, 0, sub="2")  # tek anlık görüntülü, eski bir geçmiş
    ref = Ref.event(ARS)
    with written.lease("writer"):
        assert written.history.prune(older_than=at(3).timestamp()) == 3
        assert [(s.n, s.payload) for s in written.history.snapshots(ref, ODDS, "1")] == [(1, odds(2.0)), (2, odds(4.0))]
        assert [s.payload for s in written.history.snapshots(ref, ODDS, "2")] == [odds(7.0)]
        mark = mark_of(written)
        assert mark is not None and (mark.count, mark.last_sha256) == (2, codec.encode(odds(4.0)).sha256)
        assert written.events.slice(ARS, ODDS, "1").history_count == 2
        consistent(written)

        assert written.history.prune(older_than=at(100).timestamp()) == 1  # en yenisi her zaman kalır
        assert [s.payload for s in written.history.snapshots(ref, ODDS, "1")] == [odds(4.0)]
        assert written.history.prune(older_than=at(100).timestamp()) == 0
        assert written.history.prune(Ref.tournament(17), older_than=at(100).timestamp()) == 0
    data = history_file(written).read_bytes()
    assert gzip.decompress(data).count(b"\n") == 1
    put_odds(written, 8.0, 101)  # budanmış dosyaya ekleme numarayı sürdürür
    assert [info.n for info in written.history.index(ref, ODDS, "1")] == [1, 2]
    consistent(written)


def test_prune_of_one_event_and_without_the_writer_lease(written: Store) -> None:
    with pytest.raises(StoreError, match="writer"):
        written.history.prune(older_than=at(100).timestamp())
    with written.lease("writer"):
        assert written.history.prune(Ref.event(ARS + 1), older_than=at(100).timestamp()) == 0
        assert written.history.prune(Ref.event(ARS), older_than=at(100).timestamp()) == 4
        with pytest.raises(ValueError):
            written.history.prune(older_than=True)  # type: ignore[arg-type]
    assert len(written.history.index(Ref.event(ARS), ODDS, "1")) == 1


# --- API ----------------------------------------------------------------------------------------------

def test_the_history_api_is_exported_and_attached_to_the_store(store: Store) -> None:
    assert isinstance(store.history, HistoryStore)
    for name in ("HistoryStore", "Snapshot", "SnapshotInfo"):
        assert name in sofascore_scraper.store.__all__ and getattr(sofascore_scraper.store, name) is getattr(history, name)


def test_reads_validate_their_arguments_and_know_other_owners(store: Store) -> None:
    with pytest.raises(ValueError):
        store.history.index(("event", ARS), ODDS)  # type: ignore[arg-type]
    with pytest.raises(LayoutError):
        store.history.index(Ref.event(ARS), "Odds")
    with pytest.raises(LayoutError):
        store.history.index(Ref.event(ARS), ODDS, "_")
    assert store.history.index(Ref.season(17, 52186), "standings", "total") == []
    assert store.history.index(Ref("season", 52186), "standings") == []  # sezonun turnuvası verilmeden


def test_entity_rows_are_read_from_the_entity_directory(store: Store) -> None:
    """Maç olmayan varlıkların geçmişi ST-22 ile yazılır; okuma yolu bugün de dizinden ve katalogdan geçer."""
    directory = layout.season_dir(17, 52186)
    path = Path(layout.resolve(store.data_dir, layout.history_path(directory, "standings", "total")))
    path.parent.mkdir(parents=True)
    members = [history.encode_member(codec.canonical_bytes({"rows": [n]}), codec.sha256_hex(
        codec.canonical_bytes({"rows": [n]})), at(n)) for n in (1, 2)]
    path.write_bytes(b"".join(members))
    found = history.history_rows(str(store.data_dir), "season", 52186, directory)
    assert [(r["n"], r["offset"], r["length"]) for r in found] == [
        (1, 0, len(members[0])), (2, len(members[0]), len(members[1]))]
    with store._catalog.write() as conn:
        store._catalog.upsert("slice_history", found)
        conn.execute("INSERT INTO seasons (id, tournament_id, updated_at) VALUES (52186, 17, 0)")
    by_lookup = list(store.history.snapshots(Ref("season", 52186), "standings", "total"))
    assert [s.payload for s in by_lookup] == [{"rows": [1]}, {"rows": [2]}]
    assert store.history.snapshot(Ref.season(17, 52186), "standings", "total", 2).payload == {"rows": [2]}


# --- dosya biçimi -------------------------------------------------------------------------------------

def test_members_are_deterministic_and_lines_are_checked() -> None:
    raw = codec.canonical_bytes(odds(1.0))
    digest = codec.sha256_hex(raw)
    member = history.encode_member(raw, digest, at())
    assert member == history.encode_member(raw, digest, at())  # gzip başlığında zaman yok
    assert history.parse_line(gzip.decompress(member)) == (at(), digest, raw)
    with pytest.raises(ValueError):
        history.parse_line(gzip.decompress(history.encode_member(raw, "0" * 64, at())))  # özet yüke uymuyor
    foreign = (json.dumps({"payload": odds(1.0), "sha256": digest, "fetched_at": "2026-10-01T12:00:00Z"}) + "\n")
    assert history.parse_line(foreign.encode()) == (at(), digest, raw)  # başka bir araçla yazılmış satır
    assert history.scan_bytes(b"").members == () and history.scan_bytes(b"not gzip").good_end == 0


def iter_cut_points(data: bytes) -> Iterator[int]:
    yield from range(0, len(data), max(1, len(data) // 25))


def test_every_cut_of_a_file_keeps_exactly_the_complete_members(written: Store) -> None:
    data = history_file(written).read_bytes()
    ends = [r[7] + r[8] for r in rows(written)]
    for cut in iter_cut_points(data):
        scan = history.scan_bytes(data[:cut])
        complete = sum(1 for end in ends if end <= cut)
        assert len(scan.members) == complete and scan.good_end == (ends[complete - 1] if complete else 0)
