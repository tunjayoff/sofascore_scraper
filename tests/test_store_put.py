"""
v3 yazıcısı: `EventStore.put` / `observe` / `reset_empty_markers` / `delete`, değişiklik günlüğü parçaları ve
artımlı dizini, doğrulamanın I7 kuralı, yarım yazmaların toparlanması (plan maddesi ST-20;
docs/design/01-storage.md bölüm 2.3, 3.4-3.6, 4.4, 6.2 ve 8.5).

Ölçütler:

  * Bir yazmadan sonra katalog, aynı ağacın sıfırdan kurulmuş haline eşittir (`diff_from_rebuild() == []`) ve
    hızlı / derin doğrulama temizdir (`consistent`).
  * Dilim durumu kuralları eski düzen yazıcısıyla (ST-21'e kadarki `MatchDataFetcher._save_match_data` ve
    içindeki `_update_slice_markers`; dondurulmuş kopyası tests/legacy_writer.py) aynı sonucu verir: aynı sonuç
    dizisi bir yanda eski düzen dizinine, öte yanda `put` ile v3'e uygulanır ve iki maçın mantıksal dökümü
    (tests/store_dump.py) karşılaştırılır.

Ağ yok. Yükseltme tests/test_store_promotion.py'de, süreç ölümü tests/test_store_crash.py'de, eşzamanlılık
tests/test_store_concurrency.py'dedir; ortak yardımcılar buradan içe aktarılır.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import os
import random
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import pytest

import sofascore_scraper.store
import store_dump
import store_fixtures as sf
import legacy_writer
from sofascore_scraper.slices import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, SLICE_SKIPPED, Outcome
from sofascore_scraper.sports import event_sport_slug, slices_for
from sofascore_scraper.status import observation_record
from sofascore_scraper.store import (
    EventQuery,
    LayoutError,
    PutResult,
    Ref,
    Scope,
    Store,
    StoreError,
    UnknownEvent,
    open_store,
)
from sofascore_scraper.store import changes as changes_mod
from sofascore_scraper.store import codec, entities, files, indexer, layout, manifest, verify
from sofascore_scraper.store import events as events_mod
from sofascore_scraper.store.legacy import LegacyProblem

UTC = dt.timezone.utc
NOW = sf.FIXTURE_NOW
T0 = dt.datetime.fromtimestamp(NOW, UTC)  # 2026-10-01T12:00:00Z

ARS = sf.event_id(sf.PL_ARS)  # canonical: kesin kayıt, gözlemi var, bütün dilimleri dolu
LIV = sf.event_id(sf.PL_LIV)
NO_DETAIL = sf.event_id(sf.PL_NO_DETAIL)  # canonical: yalnızca listede (round_2)
SLICE_KEYS = sf.REQUIRED_SLICES
STALE_NS = 10 ** 12  # elle verilen eski bir dosya zamanı (her dosya sisteminin tutabildiği bir değer)


# --- yardımcılar --------------------------------------------------------------------------------------

def at(seconds: float = 0) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


def ok(data: Any, when: Optional[dt.datetime] = None, **kw: Any) -> Outcome:
    return Outcome(SLICE_OK, data, fetched_at=when, **kw)


def gone(when: Optional[dt.datetime] = None) -> Outcome:
    """Kesin "veri yok": 404."""
    return Outcome(SLICE_EMPTY, None, reason="404", http_status=404, fetched_at=when)


def hollow(data: Any, when: Optional[dt.datetime] = None) -> Outcome:
    """İçinde veri olmayan 200 gövdesi."""
    return Outcome(SLICE_EMPTY, data, reason="empty", http_status=200, fetched_at=when)


def failed(reason: str = "429", status: Optional[int] = 429, when: Optional[dt.datetime] = None) -> Outcome:
    return Outcome(SLICE_FAILED, None, reason=reason, http_status=status, fetched_at=when)


def basic_of(ev: sf.Ev = sf.PL_ARS, event_id: Optional[int] = None, **edits: Any) -> Dict[str, Any]:
    event = sf.basic_payload(ev)
    if event_id is not None:
        event["id"] = event_id
    event.update(edits)
    return event


def consistent(store: Store) -> None:
    """Katalog yeniden kurulmuş haline eşit; hızlı ve derin doğrulama temiz; yarım yazma işareti yok."""
    assert store.catalog.diff_from_rebuild() == []
    for deep in (False, True):
        report = store.catalog.verify(deep=deep)
        assert report.ok, [(i.invariant, i.kind, i.detail, i.path) for i in report.open_issues]
    assert pending(store) == []


def pending(store: Store) -> List[Tuple[str, int]]:
    return [(str(r[0]), int(r[1])) for r in store._catalog.connection().execute(
        "SELECT kind, entity_id FROM pending_writes ORDER BY kind, entity_id")]


def event_file(store: Store, event_id: int, name: str = "event") -> Path:
    key, sub = layout.split_slice_name(name)
    return Path(layout.resolve(store.data_dir, layout.slice_path(layout.event_dir(event_id), key, sub)))


def manifest_of(store: Store, event_id: int) -> manifest.Manifest:
    return manifest.read_manifest(
        layout.resolve(store.data_dir, layout.manifest_path(layout.event_dir(event_id))))


def staging(store: Store) -> List[str]:
    try:
        return sorted(os.listdir(layout.resolve(store.data_dir, layout.TMP_DIR)))
    except FileNotFoundError:
        return []


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return open_store(tmp_path / "data")


@pytest.fixture
def canonical(tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture("canonical", tmp_path / "data")


@pytest.fixture
def canon(canonical: sf.LegacyFixture) -> Store:
    return open_store(canonical.data_dir)


# --- put: yeni maç, doğrulama -------------------------------------------------------------------------

def test_put_creates_a_v3_event_and_the_catalog_equals_a_rebuild(store: Store) -> None:
    basic = basic_of()
    stats = sf.slice_payload("statistics", basic)

    result = store.events.put(ARS, {"event": ok(basic, at()), "statistics": ok(stats, at(1))})

    assert result == PutResult(created=True, event_written=True, superseded=False, written=("event", "statistics"),
                               change_seq=None, promoted=False)
    directory = Path(layout.resolve(store.data_dir, layout.event_dir(ARS)))
    assert sorted(p.name for p in directory.iterdir()) == ["event.json.gz", "manifest.json", "statistics.json.gz"]
    row = store.events.get(ARS)
    assert (row.layout, row.path, row.legacy_path, row.has_event_payload, row.row_source) == (
        "v3", None, None, True, "event")
    assert row.observed_at == NOW and row.home_name == "Arsenal"
    # imza manifest dosyasınınkidir: hızlı doğrulama hiçbir maçı yeniden okumaz
    assert row.sig == indexer.file_signature(directory / "manifest.json")
    assert store.catalog.verify().events_read == 0
    assert store.events.payload(ARS) == basic and store.events.payload(ARS, "statistics") == stats
    assert store.events.payloads(ARS) == {"event": basic, "statistics": stats}
    info = store.events.slice(ARS, "statistics")
    assert (info.state, info.has_payload, info.fetched_at, info.checked_at) == ("ok", True, at(1), at(1))
    found = manifest_of(store, ARS)
    assert found.observation.observed_at == at() and found.migrated_from is None
    assert found.slices["event"].sha256 == codec.encode(basic).sha256
    assert staging(store) == []
    consistent(store)


def test_put_validates_keys_and_outcomes_before_anything_is_written(store: Store) -> None:
    basic = basic_of()
    for key in ("Statistics", "9lives", "", "con"):
        with pytest.raises(LayoutError):
            store.events.put(ARS, {"event": ok(basic), key: ok({"a": 1})})
    for sub in ("A", "_", "a/b", "x" * 81):  # büyük harf küçültülmez, reddedilir (karar S13)
        with pytest.raises(LayoutError):
            store.events.put(ARS, {"event": ok(basic), ("odds_all", sub): ok({"a": 1})})
    bad_calls: List[Callable[[], Any]] = [
        lambda: store.events.put(ARS, {"event": gone()}),  # olay sonucu ok olmalı
        lambda: store.events.put(ARS, {"event": failed()}),
        lambda: store.events.put(ARS, {"event": ok({**basic, "id": ARS + 1})}),  # yükteki id başka
        lambda: store.events.put(ARS, {"event": ok({**basic, "id": True})}),
        lambda: store.events.put(ARS, {"event": ok([basic])}),
        lambda: store.events.put(ARS, {"event": ok(basic), "statistics": Outcome(SLICE_OK, None)}),
        lambda: store.events.put(ARS, {"event": ok(basic), "statistics": {"statistics": []}}),  # Outcome değil
        lambda: store.events.put(ARS, {"event": ok(basic), "statistics": Outcome("done", {})}),  # type: ignore[arg-type]
        lambda: store.events.put(ARS, {"event": ok(basic), "statistics": ok({}), ("statistics", ""): ok({})}),
        lambda: store.events.put(ARS, {"event": ok(basic), ("a", "b", "c"): ok({})}),  # type: ignore[dict-item]
        lambda: store.events.put(ARS, [("event", ok(basic))]),  # type: ignore[arg-type]
        lambda: store.events.put(ARS, {"event": ok(basic)}, count_empties="statistics"),
        lambda: store.events.put(ARS, {"event": ok(basic, "yesterday")}),  # type: ignore[arg-type]
        lambda: store.events.put(True, {"event": ok(basic)}),
        lambda: store.events.put(-1, {"event": ok(basic)}),
        lambda: store.events.put(ARS, {"event": ok(basic)}, on_event_change="diff"),  # type: ignore[arg-type]
    ]
    for call in bad_calls:
        with pytest.raises(ValueError):
            call()

    assert store.events.get(ARS) is None and pending(store) == [] and staging(store) == []
    assert not os.path.exists(layout.resolve(store.data_dir, layout.V3_DIR))


def test_put_without_an_event_outcome_needs_a_stored_event_payload(canon: Store) -> None:
    stats = {"statistics": []}
    listed = canon.events.get(NO_DETAIL)
    assert listed.row_source == "listing" and not listed.has_event_payload

    for event_id in (424242, NO_DETAIL):  # katalogda hiç yok; yalnızca bir listeden biliniyor
        with pytest.raises(UnknownEvent) as raised:
            canon.events.put(event_id, {"statistics": ok(stats)})
        assert raised.value.event_id == event_id and isinstance(raised.value, StoreError)
        with pytest.raises(UnknownEvent):
            canon.events.put(event_id, {"statistics": gone()})
        with pytest.raises(UnknownEvent):
            canon.events.put(event_id, {}, status_regressed=True)

    assert canon.events.get(NO_DETAIL) == listed and canon.events.get(424242) is None
    assert pending(canon) == [] and staging(canon) == []
    assert not os.path.exists(layout.resolve(canon.data_dir, layout.V3_DIR))


def test_skipped_outcomes_and_an_empty_call_touch_nothing(canon: Store) -> None:
    nothing = PutResult(created=False, event_written=False, superseded=False, written=(), change_seq=None,
                        promoted=False)
    before = canon.events.get(ARS)

    assert canon.events.put(ARS, {}) == nothing
    assert canon.events.put(ARS, {"statistics": Outcome(SLICE_SKIPPED, reason="not_due"),
                                  "event": Outcome(SLICE_SKIPPED, reason="cancelled"),
                                  # bugünkü çağıranlar açık devre kesiciyi failed / breaker olarak bildirir
                                  "lineups": failed("breaker", None)}) == nothing
    assert canon.events.put(424242, {"statistics": Outcome(SLICE_SKIPPED)}) == nothing  # bilinmeyen maç da
    assert canon.events.put(ARS, {}, status_regressed=False) == nothing

    assert canon.events.get(ARS) == before and before.layout == "legacy"  # yükseltilmedi
    assert not os.path.exists(layout.resolve(canon.data_dir, layout.V3_DIR)) and staging(canon) == []


def test_a_readonly_or_closed_store_refuses_to_write(canonical: sf.LegacyFixture) -> None:
    basic = basic_of()
    open_store(canonical.data_dir).close()
    readonly = open_store(canonical.data_dir, readonly=True)
    calls: List[Callable[[Store], Any]] = [
        lambda s: s.events.put(ARS, {"event": ok(basic)}),
        lambda s: s.events.observe(ARS, basic),
        lambda s: s.events.reset_empty_markers(),
        lambda s: s.events.delete(ARS),
        lambda s: s.changes.append({"ts_utc": "2026-10-01T12:00:00+00:00", "event_id": ARS}),
    ]
    for call in calls:
        with pytest.raises(StoreError, match="read-only"):
            call(readonly)
    assert readonly.events.get(ARS).layout == "legacy" and readonly.events.payload(ARS) is not None
    readonly.close()
    for call in calls:
        with pytest.raises(StoreError, match="closed"):
            call(readonly)
    assert not os.path.exists(layout.resolve(canonical.data_dir, layout.V3_DIR))
    assert not os.path.exists(layout.resolve(canonical.data_dir, layout.CHANGES_DIR))


def test_the_write_api_is_exported_from_the_package_root() -> None:
    assert "PutResult" in sofascore_scraper.store.__all__ and sofascore_scraper.store.PutResult is events_mod.PutResult
    for name in ("put", "observe", "reset_empty_markers", "delete"):
        assert callable(getattr(sofascore_scraper.store.EventStore, name))
    assert callable(sofascore_scraper.store.ChangeLog.append)


# --- dilim durumu kuralları ---------------------------------------------------------------------------

def test_an_identical_ok_payload_writes_no_file_and_moves_the_times(store: Store) -> None:
    basic = basic_of()
    stats = sf.slice_payload("statistics", basic)
    store.events.put(ARS, {"event": ok(basic, at()), "statistics": ok(stats, at())})
    path = event_file(store, ARS, "statistics")
    os.utime(path, ns=(STALE_NS, STALE_NS))  # yeniden yazılırsa mtime değişir

    result = store.events.put(ARS, {"statistics": ok(json.loads(json.dumps(stats)), at(60))})

    assert result.written == () and not result.event_written and path.stat().st_mtime_ns == STALE_NS
    info = store.events.slice(ARS, "statistics")
    assert (info.state, info.fetched_at, info.checked_at) == ("ok", at(60), at(60))
    consistent(store)

    # farklı yük: dosya değişir
    changed = store.events.put(ARS, {"statistics": ok({**stats, "extra": 1}, at(120))})
    assert changed.written == ("statistics",) and path.stat().st_mtime_ns != STALE_NS
    assert store.events.payload(ARS, "statistics")["extra"] == 1
    consistent(store)


def test_a_payload_file_removed_behind_the_store_is_written_again(store: Store) -> None:
    """`has_payload` yalnızca dosya yerindeyken doğrudur: özet aynı olsa da eksik dosya yeniden yazılır."""
    basic = basic_of()
    stats = sf.slice_payload("statistics", basic)
    store.events.put(ARS, {"event": ok(basic), "statistics": ok(stats)})
    event_file(store, ARS, "statistics").unlink()

    assert store.events.put(ARS, {"statistics": ok(stats)}).written == ("statistics",)
    assert store.events.payload(ARS, "statistics") == stats
    consistent(store)


STEPS = ("ok", "ok2", "404", "hollow", "429", "breaker", "skip")


def _step_outcome(step: str, key: str, basic: Mapping[str, Any]) -> Tuple[Any, Optional[Outcome]]:
    """Adımın (bugünkü yazıcıya verilecek yük, sonuç) çifti; "skip": dilim bu kayıtta istenmedi."""
    if step == "ok":
        data = sf.slice_payload(key, dict(basic))
        return data, ok(data)
    if step == "ok2":
        data = {**sf.slice_payload(key, dict(basic)), "revision": 2} if key != "statistics" else {
            "statistics": sf.slice_payload(key, dict(basic))["statistics"][:1]}
        return data, ok(data)
    if step == "404":
        return None, gone()
    if step == "hollow":
        data = sf.slice_payload(key, dict(basic), empty=True)
        return data, hollow(data)
    if step == "429":
        return None, failed()
    if step == "breaker":
        return None, failed("breaker", None)
    return None, None


class _Twin:
    """Aynı maçı bir yanda eski düzen yazıcısıyla (tests/legacy_writer.py) eski düzene, öte yanda `put` ile v3'e yazar."""

    def __init__(self, tmp_path: Path) -> None:
        self.legacy_dir = tmp_path / "legacy"
        self.store = open_store(tmp_path / "v3")
        self.clock = 0

    def apply(self, event_id: int, steps: Mapping[str, str]) -> None:
        self.clock += 1
        when = at(self.clock)
        basic = basic_of(event_id=event_id)
        match_data: Dict[str, Any] = {"basic": basic, "observation": observation_record(basic, observed_at=when)}
        legacy_outcomes: Dict[str, Outcome] = {}
        outcomes: Dict[str, Outcome] = {"event": ok(basic, when)}
        for key, step in steps.items():
            data, outcome = _step_outcome(step, key, basic)
            if outcome is None:
                continue
            match_data[key] = data
            legacy_outcomes[key] = outcome
            outcomes[key] = outcome
        legacy_writer.save_legacy(self.legacy_dir, event_id, match_data, legacy_outcomes)
        self.store.events.put(event_id, outcomes)

    def dumps(self, event_id: int) -> Tuple[Any, Any]:
        basic = basic_of(event_id=event_id)
        directory = os.path.relpath(legacy_writer.legacy_match_dir(self.legacy_dir, str(event_id), basic)[2],
                                    self.legacy_dir)
        return (store_dump.dump_legacy_event(self.legacy_dir, directory.replace(os.sep, "/")),
                store_dump.dump_v3_event(self.store.data_dir, event_id))


# Bir dilimin önceki durumları: onu üreten en kısa sonuç dizisi ve `put`'tan sonra katalogda görünen hali
# (durum, yükü var mı, doğrulanmış "veri yok" sayısı, hata kaydı var mı)
PREVIOUS_STATES: Dict[str, Tuple[Tuple[str, ...], Tuple[str, bool, int, bool]]] = {
    "never requested": ((), ("not_requested", False, 0, False)),
    "ok": (("ok",), ("ok", True, 0, False)),
    "ok, then an empty answer": (("ok", "404"), ("ok", True, 1, False)),
    "ok, then an error": (("ok", "429"), ("ok", True, 0, True)),
    "ok, then an empty answer and an error": (("ok", "404", "429"), ("ok", True, 1, True)),
    "empty once": (("404",), ("empty", False, 1, False)),
    "empty twice (settled)": (("404", "404"), ("empty", False, 2, False)),
    "empty body stored": (("hollow",), ("empty", True, 1, False)),
    "error": (("429",), ("error", False, 0, True)),
    "error twice": (("429", "429"), ("error", False, 0, True)),
    "error after an empty answer": (("404", "429"), ("error", False, 1, True)),
    "error over a stored empty body": (("hollow", "429"), ("error", True, 1, True)),
}


def test_every_state_and_outcome_pair_matches_todays_marker_rules(tmp_path: Path) -> None:
    """
    Durum geçiş tablosu: her (önceki durum, sonuç) çifti için (12 x 7), önceki durumu üreten dizinin ve
    ardından gelen sonucun her adımından sonra `put`'un bıraktığı dilim durumu, sayaçlar, hata kaydı ve yük
    özeti `_update_slice_markers`'ın aynı dizi için bıraktığıyla aynıdır.
    """
    twin = _Twin(tmp_path)
    key = "statistics"
    pairs = list(itertools.product(PREVIOUS_STATES.items(), STEPS))
    for number, ((label, (prefix, expected)), outcome) in enumerate(pairs):
        event_id = 5_000_000 + number
        for position, step in enumerate((*prefix, outcome)):
            if position == len(prefix):  # önceki durum gerçekten adındaki durum
                info = twin.store.events.slice(event_id, key)
                assert (info.state, info.has_payload, info.empty_count, info.error is not None) == expected, label
            twin.apply(event_id, {key: step})
            legacy, v3 = twin.dumps(event_id)
            assert legacy is not None and store_dump.diff(legacy, v3) == [], (label, outcome, position)
    assert len(pairs) == 84
    consistent(twin.store)


@pytest.mark.parametrize("seed", range(4))
def test_random_outcome_sequences_match_todays_marker_rules(tmp_path: Path, seed: int) -> None:
    """Daha uzun diziler, bir kayıtta birden çok dilim: tablodaki önceki durumların ötesi."""
    rng = random.Random(seed)
    twin = _Twin(tmp_path)
    event_id = 6_000_000 + seed
    # Eski yazıcı işaretleri yalnızca maçın sporunda tamlığa giren dilimlere yazar; FX-16'dan beri futbolun
    # pregame_form'u onlardan değildir
    keys = [spec.key for spec in slices_for(event_sport_slug(basic_of()), required_only=True)]
    for position in range(12):
        steps = {key: rng.choice(STEPS) for key in rng.sample(keys, rng.randint(1, len(keys)))}
        twin.apply(event_id, steps)
        legacy, v3 = twin.dumps(event_id)
        assert store_dump.diff(legacy, v3) == [], (seed, position, steps)
    consistent(twin.store)


def test_slice_states_are_stored_as_the_design_table_says(store: Store) -> None:
    basic = basic_of()
    body = sf.slice_payload("lineups", basic, empty=True)
    store.events.put(ARS, {
        "event": ok(basic, at()),
        "statistics": gone(at(1)),
        "lineups": hollow(body, at(2)),
        "incidents": failed("5xx", 503, at(3)),
        "h2h": Outcome(SLICE_EMPTY, None, fetched_at=at(4)),  # nedeni verilmemiş kesin "yok"
    })

    states = {s.key: s for s in store.events.slices(ARS)}
    assert [(k, s.state, s.has_payload, s.empty_count) for k, s in sorted(states.items())] == [
        ("event", "ok", True, 0), ("h2h", "empty", False, 1), ("incidents", "error", False, 0),
        ("lineups", "empty", True, 1), ("statistics", "empty", False, 1)]
    error = states["incidents"].error
    assert (error.reason, error.http_status, error.at, error.count) == ("5xx", 503, at(3), 1)
    assert states["statistics"].checked_at == at(1) and states["statistics"].fetched_at is None
    assert store.events.payload(ARS, "lineups") == body and store.events.payload(ARS, "statistics") is None
    found = manifest_of(store, ARS)
    assert (found.slices["statistics"].empty.reason, found.slices["lineups"].empty.reason,
            found.slices["h2h"].empty.reason) == ("404", "empty", "404")
    assert found.slices["statistics"].empty.at == at(1)

    # ikinci hata sayacı artırır; "veri yok" sayacına dokunmaz; ikinci 404 dilimi beklenmez yapar
    store.events.put(ARS, {"incidents": failed("timeout", None, at(5)), "statistics": gone(at(6))})
    incidents, statistics = store.events.slice(ARS, "incidents"), store.events.slice(ARS, "statistics")
    assert (incidents.error.reason, incidents.error.http_status, incidents.error.count) == ("timeout", None, 2)
    assert statistics.empty_count == 2 and statistics.settled_empty() and not incidents.settled_empty()
    missing = {row.event_id: row.missing_keys for row in store.events.missing(None, {"": list(SLICE_KEYS)},
                                                                              status_classes=())}
    assert set(missing[ARS]) == {"h2h", "incidents", "lineups", "team_streaks", "pregame_form"}
    consistent(store)


def test_count_empties_decides_which_empty_answers_are_counted(store: Store) -> None:
    basic = basic_of()
    store.events.put(ARS, {"event": ok(basic), "statistics": failed(), "lineups": failed(), "h2h": gone()},
                     count_empties=False)
    states = {s.key: s for s in store.events.slices(ARS)}
    assert (states["h2h"].state, states["h2h"].empty_count) == ("empty", 0)  # kayıt açılır, sayılmaz

    store.events.put(ARS, {"statistics": gone(), "lineups": gone(), "h2h": hollow({"teamDuel": None})},
                     count_empties=("lineups",))

    states = {s.key: s for s in store.events.slices(ARS)}
    # iki biçimde de önceki hata silinir; sayaç yalnızca kapsanan anahtarda artar
    assert [(k, states[k].state, states[k].empty_count, states[k].error) for k in ("statistics", "lineups", "h2h")] == [
        ("statistics", "empty", 0, None), ("lineups", "empty", 1, None), ("h2h", "empty", 0, None)]
    assert states["h2h"].has_payload
    assert manifest_of(store, ARS).slices["statistics"].empty is None
    consistent(store)


def test_slices_with_a_sub_and_meta_are_stored_next_to_the_event(store: Store) -> None:
    basic = basic_of()
    odds = {"markets": [{"marketId": 1, "choices": [{"name": "1", "fractionalValue": "11/10"}]}]}
    result = store.events.put(ARS, {
        "event": ok(basic), ("odds_all", "1"): ok(odds, meta={"provider": 1}), ("odds_all", "22"): gone(),
        ("point_by_point", ""): ok({"pointByPoint": [1]})})

    assert result.written == ("event", "odds_all/1", "point_by_point")
    assert event_file(store, ARS, "odds_all/1").name == "1.json.gz"
    assert event_file(store, ARS, "odds_all/1").parent.name == "odds_all"
    assert store.events.payload(ARS, "odds_all", "1") == odds
    assert set(store.events.payloads(ARS)) == {"event", "odds_all/1", "point_by_point"}
    info = store.events.slice(ARS, "odds_all", "1")
    assert (info.state, dict(info.meta)) == ("ok", {"provider": 1})
    assert store.events.slice(ARS, "odds_all", "22").state == "empty"
    # yük yeniden saklanınca meta sonucun getirdiğidir (verilmediyse silinir)
    store.events.put(ARS, {("odds_all", "1"): ok({**odds, "v": 2})})
    assert dict(store.events.slice(ARS, "odds_all", "1").meta) == {}
    consistent(store)


# --- olay yükü: gözlem, eskimiş sonuç, değişiklik günlüğü ----------------------------------------------

def test_an_older_event_observation_is_superseded(store: Store) -> None:
    basic = basic_of()
    older = basic_of(winnerCode=3)
    store.events.observe(ARS, basic, observed_at=at(100))
    calls: List[Any] = []

    result = store.events.put(
        ARS, {"event": ok(older, at(99)), "statistics": ok({"statistics": [1]}, at(99))},
        on_event_change=lambda old, new: calls.append((old, new)))

    assert result.superseded and not result.event_written and result.written == ("statistics",)
    assert calls == [] and store.events.payload(ARS) == basic  # daha yeni gözlem yerinde kaldı
    assert manifest_of(store, ARS).observation.observed_at == at(100)
    assert store.events.slice(ARS, "statistics").state == "ok"  # öteki sonuçlar uygulandı
    # aynı ana ait gözlem eskimiş değildir
    assert not store.events.observe(ARS, older, observed_at=at(100)).superseded
    assert store.events.payload(ARS)["winnerCode"] == 3
    consistent(store)


def test_observe_with_an_identical_payload_only_moves_the_observation(store: Store) -> None:
    basic = basic_of()
    store.events.observe(ARS, basic, observed_at=at())
    path = event_file(store, ARS)
    os.utime(path, ns=(STALE_NS, STALE_NS))
    start = store.events.get(ARS).start_ts

    result = store.events.observe(ARS, dict(basic), observed_at=at(3600), on_event_change=lambda old, new: 1 / 0)

    assert result == PutResult(created=False, event_written=False, superseded=False, written=(), change_seq=None,
                               promoted=False)
    assert path.stat().st_mtime_ns == STALE_NS
    row = store.events.get(ARS)
    assert (row.observed_at, row.observed_gap) == (NOW + 3600, NOW + 3600 - start)
    assert manifest_of(store, ARS).observation.change_ts == basic["changes"]["changeTimestamp"]
    consistent(store)


def change_of(old: Optional[Mapping[str, Any]], new: Mapping[str, Any], *, ts: str = "2026-10-01T12:00:05+00:00",
              **extra: Any) -> Dict[str, Any]:
    """`sofascore_scraper/refresh.change_row` biçiminde küçük bir satır."""
    return {"ts_utc": ts, "event_id": new["id"], "sport": "football",
            "tournament": {"id": 17, "name": "Premier League"},
            "changed": {"winnerCode": [(old or {}).get("winnerCode"), new.get("winnerCode")]}, **extra}


def test_on_event_change_runs_inside_the_write_and_its_row_reaches_the_change_log(store: Store) -> None:
    basic = basic_of()
    seen: List[Tuple[Any, Any, List[Tuple[str, int]]]] = []

    def on_change(old: Optional[Mapping[str, Any]], new: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        seen.append((old, new, pending(store)))  # kritik bölümün içinde: işaret duruyor
        return change_of(old, new) if old is not None else None

    created = store.events.observe(ARS, basic, observed_at=at(), on_event_change=on_change)
    assert seen == [(None, basic, [("event", ARS)])] and created.change_seq is None  # yeni maç: satır yok

    changed = basic_of(winnerCode=2)
    result = store.events.observe(ARS, changed, observed_at=at(5), on_event_change=on_change)

    assert result.event_written and result.change_seq == 1
    assert (seen[1][0], seen[1][1]) == (basic, changed)  # o anda saklanan yük ile yenisi
    segment = Path(store.data_dir) / "changes" / "2026-10.jsonl"
    line = segment.read_bytes()
    assert line.endswith(b"\n") and b"\r" not in line and line.count(b"\n") == 1
    assert json.loads(line) == {**change_of(basic, changed), "seq": 1}
    (row,) = store.changes.list()
    assert (row.seq, row.event_id, row.segment, row.fields, row.tournament_id, row.status_regressed) == (
        1, ARS, "changes/2026-10.jsonl", ("winnerCode",), 17, False)
    assert row.row["seq"] == 1 and store.changes.last_seq() == 1
    assert store.events.payload(ARS) == changed

    # yük değişmediyse çağrılmaz
    store.events.observe(ARS, changed, observed_at=at(6), on_event_change=on_change)
    assert len(seen) == 2 and store.changes.last_seq() == 1
    consistent(store)


def test_a_failing_or_invalid_change_callback_leaves_the_event_as_it_was(canon: Store) -> None:
    """Geri çağrı diske dokunulmadan önce çalışır: hata verirse eski düzendeki maç yükseltilmez bile."""
    stored = canon.events.payload(ARS)
    changed = {**stored, "winnerCode": 3}

    def boom(old: Any, new: Any) -> None:
        raise RuntimeError("diff failed")

    with pytest.raises(RuntimeError, match="diff failed"):
        canon.events.observe(ARS, changed, on_event_change=boom)
    for row in ({"event_id": ARS}, {"ts_utc": "x", "event_id": ARS}, {"ts_utc": "2026-10-01T12:00:00Z"}, ["row"]):
        with pytest.raises(ValueError, match="on_event_change"):
            canon.events.observe(ARS, changed, on_event_change=lambda old, new, row=row: row)

    assert canon.events.payload(ARS) == stored and canon.events.get(ARS).layout == "legacy"
    assert pending(canon) == [] and staging(canon) == []
    assert not os.path.exists(layout.resolve(canon.data_dir, layout.V3_DIR))
    assert canon.changes.last_seq() == len(sf.SCORE_CHANGES)


def test_status_regressed_is_sticky(store: Store) -> None:
    basic = basic_of()
    store.events.observe(ARS, basic, observed_at=at())
    assert not store.events.get(ARS).status_regressed

    void = basic_of(status={"code": 70, "description": "Canceled", "type": "canceled"})
    store.events.observe(ARS, void, observed_at=at(10),
                         on_event_change=lambda old, new: change_of(old, new, status_regressed=True))
    assert store.events.get(ARS).status_regressed and store.changes.list()[0].status_regressed

    # sonraki gözlemler bayrağı silmez; False da silmez
    store.events.observe(ARS, basic, observed_at=at(20), status_regressed=False)
    assert manifest_of(store, ARS).observation.status_regressed and store.events.get(ARS).status_regressed
    consistent(store)

    # bağımsız değişkenle, yük değişmeden
    other = ARS + 1
    store.events.observe(other, basic_of(event_id=other), observed_at=at())
    assert store.events.put(other, {}, status_regressed=True).written == ()
    assert store.events.get(other).status_regressed
    consistent(store)


def test_a_newer_event_payload_clears_the_stale_flag(canon: Store) -> None:
    """Bölüm 8.2, kural 3: liste olay yükünden yeni ve farklıysa `stale`; daha yeni bir yük bayrağı kaldırır."""
    listed = sf.basic_payload(sf.PL_NO_DETAIL)
    behind = {**listed, "homeScore": {**listed["homeScore"], "current": 0, "display": 0}}
    page_time = dt.datetime.fromtimestamp(sf.BASE_MTIME, UTC)
    already = canon.events.stale()  # dizinde zaten bayat olan başka bir maç var

    canon.events.observe(NO_DETAIL, behind, observed_at=page_time - dt.timedelta(hours=1))
    row = canon.events.get(NO_DETAIL)
    assert (row.stale, row.listed_in, row.row_source, row.layout) == (True, "round_2", "event", "v3")
    assert canon.events.stale() == sorted([*already, NO_DETAIL])

    canon.events.observe(NO_DETAIL, listed, observed_at=page_time + dt.timedelta(hours=1))
    assert not canon.events.get(NO_DETAIL).stale and canon.events.stale() == already
    consistent(canon)


# --- reset_empty_markers ------------------------------------------------------------------------------

def test_reset_empty_markers_gives_todays_counts_and_todays_result(tmp_path: Path) -> None:
    """Aynı ağaçta bugünkü `reset_unavailable_markers` ile aynı sayımlar ve aynı mantıksal döküm."""
    for include_confirmed in (False, True):
        old = sf.build_fixture("canonical", tmp_path / f"old-{include_confirmed}")
        new = sf.build_fixture("canonical", tmp_path / f"new-{include_confirmed}")
        store = open_store(new.data_dir)
        slices_before = {e: {s.key: s.state for s in store.events.slices(e)} for e in new.detail_ids}

        expected = legacy_writer.reset_legacy_markers(old.data_dir, include_confirmed=include_confirmed)
        result = store.events.reset_empty_markers(include_confirmed=include_confirmed)

        assert result == expected and result["matches"] > 0 and result["slices"] > result["matches"]
        dump_old, dump_new = store_dump.dump(old.data_dir, old.leagues), store_dump.dump(new.data_dir, new.leagues)
        assert store_dump.diff(dump_old["events"], dump_new["events"]) == []
        # sayaçları değişmeyen maç eski düzende kaldı; değişen yükseltildi
        layouts = {e: store.events.get(e).layout for e in new.detail_ids}
        assert set(layouts.values()) == {"legacy", "v3"} and layouts[ARS] == "legacy"
        # sayacı sıfırlanan, yükü olmayan dilim "istenmedi" olur
        reopened = [(e, k) for e in new.detail_ids for k, state in slices_before[e].items()
                    if state == "empty" and store.events.slice(e, k).state == "not_requested"]
        assert reopened
        consistent(store)

        again = store.events.reset_empty_markers(include_confirmed=include_confirmed)
        assert (again["matches"], again["slices"]) == (0, 0)  # işlem tekrarlanabilir
        assert again == legacy_writer.reset_legacy_markers(old.data_dir, include_confirmed=include_confirmed)
        store.close()


def test_reset_empty_markers_respects_the_scope_and_the_threshold(store: Store) -> None:
    first, second = ARS, sf.event_id(sf.NBA_A)
    store.events.put(first, {"event": ok(basic_of()), "lineups": gone(), "incidents": hollow({"incidents": []})})
    store.events.put(second, {"event": ok(sf.basic_payload(sf.NBA_A)), "lineups": gone()})
    for _ in range(2):
        store.events.put(first, {"lineups": gone(), "incidents": hollow({"incidents": []})})
    assert store.events.slice(first, "lineups").empty_count == 3

    # doğrulanmış sayaçlar varsayılan olarak kalır
    assert store.events.reset_empty_markers() == {"matches": 0, "slices": 0, "scanned": 2}
    scoped = store.events.reset_empty_markers(Scope(tournament_ids=[sf.NBA.id]), include_confirmed=True)
    assert scoped == {"matches": 0, "slices": 0, "scanned": 1}  # 1 sayım eşiğin (2) altındaydı: yeniden açılan yok
    assert store.events.slice(second, "lineups").state == "not_requested"
    assert store.events.slice(first, "lineups").empty_count == 3

    result = store.events.reset_empty_markers(Scope(event_ids=[first]), include_confirmed=True, threshold=3)
    assert result == {"matches": 1, "slices": 2, "scanned": 1}
    assert store.events.slice(first, "lineups").state == "not_requested"
    kept = store.events.slice(first, "incidents")  # yükü olan dilimin kaydı kalır, sayacı sıfırlanır
    assert (kept.state, kept.has_payload, kept.empty_count) == ("empty", True, 0)
    assert store.events.reset_empty_markers(include_confirmed=True) == {"matches": 0, "slices": 0, "scanned": 0}
    with pytest.raises(ValueError):
        store.events.reset_empty_markers(threshold=-1)
    consistent(store)


# --- delete -------------------------------------------------------------------------------------------

def test_delete_removes_the_directory_and_the_rows(store: Store) -> None:
    basic = basic_of()
    store.events.put(ARS, {"event": ok(basic), "statistics": ok({"statistics": [1]})})
    directory = Path(layout.resolve(store.data_dir, layout.event_dir(ARS)))

    assert store.events.delete(ARS) is True
    assert not directory.exists() and store.events.get(ARS) is None and store.events.slices(ARS) == []
    assert store.events.payload(ARS) is None
    assert os.listdir(layout.resolve(store.data_dir, layout.TRASH_DIR)) == []
    assert store.events.delete(ARS) is False and store.events.delete(424242) is False
    consistent(store)
    # silinen maç yeniden yazılabilir
    assert store.events.put(ARS, {"event": ok(basic)}).created
    consistent(store)


def test_delete_removes_both_layouts_and_a_listed_event_becomes_a_listing_row(canon: Store) -> None:
    legacy_dir = Path(canon.data_dir) / canon.events.get(ARS).path
    canon.events.put(ARS, {"statistics": ok({"statistics": [1]})})  # yükseltilir: iki kopya
    promoted = canon.events.get(ARS)
    assert promoted.layout == "v3" and legacy_dir.is_dir() and promoted.listed_in == "round_1"

    assert canon.events.delete(ARS) is True

    assert not legacy_dir.exists() and not os.path.exists(layout.resolve(canon.data_dir, layout.event_dir(ARS)))
    row = canon.events.get(ARS)  # tur dosyasında hâlâ listeleniyor
    assert (row.row_source, row.has_event_payload, row.layout, row.listed_in) == ("listing", False, None, "round_1")
    assert canon.events.slices(ARS) == []
    # yalnızca eski düzende duran maç
    other_dir = Path(canon.data_dir) / canon.events.get(LIV).path
    assert canon.events.delete(LIV) is True and not other_dir.exists()
    assert canon.events.delete(NO_DETAIL) is False  # dizini olmayan liste satırı
    assert canon.events.get(NO_DETAIL).row_source == "listing"
    consistent(canon)


# --- değişiklik günlüğü -------------------------------------------------------------------------------

def line_of(event_id: int, ts: str = "2026-10-01T12:00:00+00:00", **extra: Any) -> Dict[str, Any]:
    return {"ts_utc": ts, "event_id": event_id, "sport": "football", "changed": {"winnerCode": [1, 2]}, **extra}


def test_append_continues_after_the_legacy_lines_and_a_rebuild_reproduces_the_seq(canon: Store) -> None:
    legacy_lines = len(sf.SCORE_CHANGES)
    assert canon.changes.last_seq() == legacy_lines

    first = canon.changes.append(line_of(ARS, "2026-09-30T23:59:59+00:00"))
    second = canon.changes.append(line_of(LIV, "2026-10-01T00:00:00+00:00", status_regressed=True))
    third = canon.changes.append(line_of(ARS, "2026-10-01T02:00:00+03:00"))  # UTC'de eylülün son günü

    assert (first, second, third) == (legacy_lines + 1, legacy_lines + 2, legacy_lines + 3)
    september = (Path(canon.data_dir) / "changes" / "2026-09.jsonl").read_bytes().split(b"\n")
    october = (Path(canon.data_dir) / "changes" / "2026-10.jsonl").read_bytes().split(b"\n")
    assert [json.loads(line)["seq"] for line in september[:-1]] == [first, third] and september[-1] == b""
    assert [json.loads(line)["seq"] for line in october[:-1]] == [second]
    assert json.loads(september[0]) == {**line_of(ARS, "2026-09-30T23:59:59+00:00"), "seq": first}
    rows = canon.changes.list(after_seq=legacy_lines)
    assert [(r.seq, r.segment, r.event_id, r.status_regressed) for r in rows] == [
        (first, "changes/2026-09.jsonl", ARS, False), (second, "changes/2026-10.jsonl", LIV, True),
        (third, "changes/2026-09.jsonl", ARS, False)]
    assert [r.seq for r in canon.changes.list(event_id=ARS)][-2:] == [first, third]
    assert (Path(canon.data_dir) / "score_changes.jsonl").read_bytes() == sf.dump_jsonl(sf.SCORE_CHANGES)

    before = canon._catalog.connection().execute("SELECT * FROM changes ORDER BY seq").fetchall()
    report = canon.catalog.rebuild()
    assert report.changes == legacy_lines + 3
    assert [tuple(r) for r in canon._catalog.connection().execute("SELECT * FROM changes ORDER BY seq")] == [
        tuple(r) for r in before]
    dumped = store_dump.dump(canon.data_dir)["changes"]
    assert [c["seq"] for c in dumped] == list(range(1, legacy_lines + 4))
    assert dumped[-1]["row"] == line_of(ARS, "2026-10-01T02:00:00+03:00")  # dökümde `seq` satırdan çıkarılır
    consistent(canon)


def test_append_validates_the_row(store: Store) -> None:
    for row in ({"event_id": 1}, {"ts_utc": "2026-10-01T12:00:00Z"}, {"ts_utc": "soon", "event_id": 1},
                {"ts_utc": "2026-10-01T12:00:00Z", "event_id": "1"}, {"ts_utc": "2026-10-01T12:00:00Z", "event_id": True},
                ["ts_utc"]):
        with pytest.raises(ValueError):
            store.changes.append(row)  # type: ignore[arg-type]
    with pytest.raises(StoreError):
        store.changes.append({**line_of(1), "payload": object()})  # JSON'a çevrilemez
    assert store.changes.last_seq() == 0 and not os.path.exists(layout.resolve(store.data_dir, layout.CHANGES_DIR))
    # satırdaki `seq` yok sayılır: numarayı Store verir
    assert store.changes.append({**line_of(1), "seq": 99}) == 1 and store.changes.list()[0].row["seq"] == 1


def test_a_torn_last_line_is_skipped_and_the_next_append_starts_on_a_fresh_line(store: Store) -> None:
    store.changes.append(line_of(1))
    segment = Path(store.data_dir) / "changes" / "2026-10.jsonl"
    with open(segment, "ab") as f:
        f.write(b'{"ts_utc": "2026-10-01T12:00:00+00:00", "event_id": 2, "se')  # süreç yazarken öldü

    problems: List[LegacyProblem] = []
    with store._catalog.write():
        assert changes_mod.sync(store._catalog, store.data_dir, problems) == 1
    assert [(p.path, p.kind) for p in problems] == [("changes/2026-10.jsonl", "torn_line")]

    assert store.changes.append(line_of(3)) == 2  # yarım satırın numarası hiç verilmemişti
    lines = segment.read_bytes().split(b"\n")
    assert len(lines) == 4 and lines[-1] == b"" and lines[1].endswith(b'"se')
    assert [json.loads(lines[n])["seq"] for n in (0, 2)] == [1, 2]
    report = store.catalog.rebuild()
    assert report.changes == 2 and [(p.path, p.kind) for p in report.problems] == [
        ("changes/2026-10.jsonl", "corrupt")]
    consistent(store)


def test_a_line_written_before_a_crash_is_indexed_and_its_seq_is_not_reused(store: Store) -> None:
    """Satır dosyaya yazılıp katalog işlemi tamamlanmadan süreç ölürse (bölüm 8.5) kuyruk sonradan dizinlenir."""
    store.changes.append(line_of(1))
    segment = Path(store.data_dir) / "changes" / "2026-10.jsonl"
    with open(segment, "ab") as f:
        f.write(json.dumps({**line_of(2), "seq": 2}).encode() + b"\n")
    assert store.changes.last_seq() == 1 and not store.catalog.verify().ok

    assert store.changes.append(line_of(3)) == 3
    assert [(r.seq, r.event_id) for r in store.changes.list()] == [(1, 1), (2, 2), (3, 3)]
    consistent(store)

    # açılıştaki uzlaştırma da kuyruğu dizinler
    with open(segment, "ab") as f:
        f.write(json.dumps({**line_of(4), "seq": 4}).encode() + b"\n")
    data_dir = store.data_dir
    store.close()
    again = open_store(data_dir)
    assert again.changes.last_seq() == 4
    consistent(again)


def test_an_append_to_the_legacy_file_indexes_only_its_tail(canon: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """ST-11'in bulgusu: her eklemede bütün dosya yeniden dizinleniyordu. Artık yalnızca kuyruk okunur."""
    path = Path(canon.data_dir) / "score_changes.jsonl"
    size = path.stat().st_size
    reads: List[Tuple[str, int]] = []
    real = changes_mod._read

    def spy(file_path: str, start: int, end: Optional[int] = None) -> Any:
        if end is None:
            reads.append((os.path.basename(file_path), start))
        return real(file_path, start, end)

    monkeypatch.setattr(changes_mod, "_read", spy)
    with open(path, "ab") as f:
        f.write(json.dumps(line_of(LIV)).encode() + b"\r\n\n" + json.dumps(line_of(ARS)).encode() + b"\n")

    report = canon.catalog.sync_listings(indexer.LISTING_CHANGES)

    assert reads == [("score_changes.jsonl", size)] and report.changes == 4
    # satır numaraları kaldığı yerden sürer; boş satır numara harcar
    assert [(r.seq, r.event_id) for r in canon.changes.list(after_seq=2)] == [(3, LIV), (5, ARS)]
    assert canon.catalog.sync_listings(indexer.LISTING_CHANGES).changes is None and len(reads) == 1
    assert canon.catalog.reconcile().changes is None
    assert canon.catalog.diff_from_rebuild() == []


def test_a_catalog_without_the_indexed_lengths_is_indexed_once_from_the_start(canon: Store) -> None:
    """Bu kaydı tutmayan bir sürümün kurduğu katalog (yükseltmeden sonraki ilk açılış): satırlar yerinde, kayıt yok."""
    canon.changes.append(line_of(ARS))
    before = [tuple(r) for r in canon._catalog.connection().execute("SELECT * FROM changes ORDER BY seq")]
    with canon._catalog.write() as conn:
        conn.execute("DELETE FROM meta WHERE key = ?", (changes_mod.META_INDEXED,))

    report = canon.catalog.reconcile()

    assert report.changes == 3 and report.problems == []  # satırlar "iki kez verilmiş numara" diye bildirilmez
    assert [tuple(r) for r in canon._catalog.connection().execute("SELECT * FROM changes ORDER BY seq")] == before
    assert set(changes_mod._load_marks(canon._catalog)) == {"score_changes.jsonl", "changes/2026-10.jsonl"}
    assert canon.catalog.reconcile().changes is None
    # okunamayan kayıt da aynı yoldan geçer
    with canon._catalog.write():
        canon._catalog.set_meta(changes_mod.META_INDEXED, "{ not json")
    assert canon.catalog.reconcile().changes == 3 and canon.changes.append(line_of(LIV)) == 4
    consistent(canon)


def test_a_rewritten_or_removed_log_file_is_indexed_from_the_start(canon: Store) -> None:
    path = Path(canon.data_dir) / "score_changes.jsonl"
    canon.changes.append(line_of(ARS))  # v3 satırı: seq 3
    lines = path.read_bytes().split(b"\n")

    path.write_bytes(lines[1] + b"\n")  # ilk satır silindi: dosya kısaldı, numaralar kaydı
    report = canon.catalog.reconcile()
    assert report.changes == 2
    assert [(r.seq, r.segment) for r in canon.changes.list()] == [(1, "score_changes.jsonl"), (3, "changes/2026-10.jsonl")]
    assert canon.catalog.diff_from_rebuild() == []

    # aynı uzunlukta ama başka içerik: uzamış gibi görünen dosyanın başı değişmiş
    path.write_bytes(lines[0] + b"\n" + lines[1] + b"\n")
    canon.catalog.reconcile()
    marks = changes_mod._load_marks(canon._catalog)
    path.write_bytes(lines[1] + b"\n" + lines[0] + b"\n" + lines[0] + b"\n")
    assert path.stat().st_size > marks["score_changes.jsonl"].offset
    problems = canon.catalog.reconcile().problems
    assert [(r.seq, r.event_id) for r in canon.changes.list()][:2] == [(1, 17060394), (2, 17099711)]
    # eski dosyanın üçüncü satırı v3 satırının numarasını alır: dizinleme sırasında önce gelen geçerlidir
    assert canon.changes.list()[2].segment == "score_changes.jsonl"
    assert ("changes/2026-10.jsonl", "duplicate_seq") in [(p.path, p.kind) for p in problems]
    assert canon.catalog.diff_from_rebuild() == []

    path.unlink()
    files.remove_tree(Path(canon.data_dir) / "changes")
    assert canon.catalog.reconcile().changes == 0 and canon.changes.list() == []
    assert canon.catalog.reconcile().changes is None
    assert canon.catalog.diff_from_rebuild() == []


def test_a_seq_given_twice_keeps_the_first_line_in_scan_order(store: Store) -> None:
    """Kuyruğu dizinlemek ile baştan dizinlemek aynı satırları verir, numara çakışsa da."""
    store.changes.append(line_of(1, "2026-10-01T12:00:00+00:00"))  # seq 1, ekim
    store.changes.append(line_of(2, "2026-09-01T12:00:00+00:00"))  # seq 2, eylül
    september = Path(store.data_dir) / "changes" / "2026-09.jsonl"
    october = Path(store.data_dir) / "changes" / "2026-10.jsonl"

    # elle eklenen çakışmalar: ekim parçasında seq 2 (eylüldeki önce gelir), eylül parçasında seq 1 (o önce gelir)
    with open(october, "ab") as f:
        f.write(json.dumps({**line_of(10), "seq": 2}).encode() + b"\n")
    with open(september, "ab") as f:
        f.write(json.dumps({**line_of(20, "2026-09-02T00:00:00+00:00"), "seq": 1}).encode() + b"\n")
        f.write(json.dumps({**line_of(21, "2026-09-02T00:00:00+00:00"), "seq": 0}).encode() + b"\n")  # seq yok sayılır
        f.write(json.dumps(line_of(22, "2026-09-02T00:00:00+00:00")).encode() + b"\n")

    report = store.catalog.reconcile()

    assert [(r.seq, r.event_id, r.segment) for r in store.changes.list()] == [
        (1, 20, "changes/2026-09.jsonl"), (2, 2, "changes/2026-09.jsonl")]
    assert sorted((p.path, p.kind) for p in report.problems) == [
        ("changes/2026-09.jsonl", "malformed"), ("changes/2026-09.jsonl", "malformed"),
        ("changes/2026-10.jsonl", "duplicate_seq"), ("changes/2026-10.jsonl", "duplicate_seq")]
    assert store.catalog.diff_from_rebuild() == []
    rebuilt = store.catalog.rebuild()
    assert sorted(p.kind for p in rebuilt.problems) == ["duplicate_seq", "duplicate_seq", "malformed", "malformed"]
    assert [(r.seq, r.event_id) for r in store.changes.list()] == [(1, 20), (2, 2)]


def test_files_in_the_changes_directory_that_are_no_segment_are_reported(store: Store) -> None:
    store.changes.append(line_of(1))
    directory = Path(store.data_dir) / "changes"
    (directory / "2026-13.jsonl").write_bytes(b"{}\n")
    (directory / "notes.txt").write_bytes(b"x")
    (directory / ".2026-10.jsonl.swp").write_bytes(b"x")

    report = store.catalog.rebuild()

    assert report.changes == 1
    assert sorted((p.path, p.kind) for p in report.problems) == [
        ("changes/2026-13.jsonl", "unknown_name"), ("changes/notes.txt", "unknown_name")]
    assert changes_mod.segments(store.data_dir) == ["changes/2026-10.jsonl"]
    assert changes_mod.is_segment("2026-10.jsonl") and not changes_mod.is_segment("2026-00.jsonl")


# --- doğrulama: I7 ------------------------------------------------------------------------------------

def issues(report: verify.VerifyReport) -> List[Tuple[str, str, Optional[str], bool]]:
    return [(i.invariant, i.kind, i.path, i.repaired) for i in report.issues]


def test_verify_checks_the_change_log_of_v3_segments(canon: Store) -> None:
    for n in range(3):
        canon.changes.append(line_of(ARS + n))
    segment = "changes/2026-10.jsonl"
    path = Path(canon.data_dir) / "changes" / "2026-10.jsonl"
    assert "I7" in verify.QUICK_CHECKS and canon.catalog.verify().checked == verify.QUICK_CHECKS
    assert canon.catalog.verify().ok and canon.catalog.verify(deep=True).ok

    # 1) dizinlenmemiş kuyruk: hızlı kip dosya boyutundan görür, onarım dizinler
    with open(path, "ab") as f:
        f.write(json.dumps({**line_of(LIV), "seq": 6}).encode() + b"\n")
    assert issues(canon.catalog.verify()) == [("I7", "seq_unindexed", segment, False)]
    assert issues(canon.catalog.verify(repair=True)) == [("I7", "seq_unindexed", segment, True)]
    assert canon.changes.last_seq() == 6 and canon.catalog.verify(deep=True).ok

    # 2) dizindeki satır dosyadakinden farklı: yalnızca derin kip okur
    with canon._catalog.write() as conn:
        conn.execute("UPDATE changes SET row_json = '{}' WHERE seq = 4")
        conn.execute("DELETE FROM changes WHERE seq = 5")
        conn.execute("INSERT INTO changes (seq, ts, event_id, fields, row_json, segment) VALUES (7, 1, 1, '', '{}', ?)",
                     (segment,))
    assert canon.catalog.verify().ok is False  # 5 eksik, 7 fazla: numaralar ardışık değil
    deep = canon.catalog.verify(deep=True)
    assert issues(deep) == [("I7", "seq_gap", None, False), ("I7", "seq_mismatch", segment, False)]
    assert "dizinde yok: 5" in deep.issues[1].detail and "dosyada yok: 7" in deep.issues[1].detail
    assert "satır farklı: 4" in deep.issues[1].detail
    repaired = canon.catalog.verify(deep=True, repair=True)
    assert issues(repaired) == [("I7", "seq_gap", None, True), ("I7", "seq_mismatch", segment, True)]
    assert repaired.ok and canon.catalog.verify(deep=True).ok and canon.catalog.diff_from_rebuild() == []

    # 3) dosyaların kendisindeki boşluk onarılamaz
    kept = [line for line in path.read_bytes().split(b"\n") if line and json.loads(line)["seq"] != 4]
    path.write_bytes(b"\n".join(kept) + b"\n")
    canon.catalog.reconcile()
    report = canon.catalog.verify(repair=True)
    assert issues(report) == [("I7", "seq_gap", None, False)] and not report.ok
    assert report.issues[0].detail.endswith("3-6 aralığında 3 satır")

    # 4) parçası silinmiş satırlar
    path.unlink()
    assert ("I7", "seq_unindexed", segment, False) in issues(canon.catalog.verify())
    assert canon.catalog.verify(repair=True).ok and canon.changes.last_seq() == len(sf.SCORE_CHANGES)


def test_the_legacy_log_keeps_its_gaps_without_an_i7_issue(tmp_path: Path) -> None:
    """Eski dosyada boş ve bozuk satırlar numara harcar: boşluk meşrudur, I7 ona uygulanmaz."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "score_changes.jsonl").write_bytes(
        json.dumps(line_of(1)).encode() + b"\n\n{ not json\n" + json.dumps(line_of(4)).encode() + b"\n")
    store = open_store(data)

    assert [r.seq for r in store.changes.list()] == [1, 4]
    assert store.catalog.verify().ok and store.catalog.verify(deep=True).ok
    assert store.changes.append(line_of(5)) == 5  # v3 numaraları en büyük numaradan sürer
    consistent(store)


# --- dizinleyici: yarım yazmalar ve v3 varlık dizinleri ---------------------------------------------------

def test_heal_brings_the_manifest_up_to_date_with_newer_payload_files(store: Store) -> None:
    """Bölüm 4.4: yük dosyası manifestten önce yazılır; arada ölen süreçten sonra dosyaya güvenilir."""
    basic = basic_of()
    stats = sf.slice_payload("statistics", basic)
    store.events.put(ARS, {"event": ok(basic, at()), "statistics": ok(stats, at()), "lineups": gone(at()),
                           "incidents": failed("5xx", 503, at())})
    before = manifest_of(store, ARS)
    newer = basic_of(winnerCode=3)
    newer["changes"] = {"changeTimestamp": 1790000123}
    lineups = sf.slice_payload("lineups", basic)
    codec.write_payload(event_file(store, ARS), newer)  # manifest yazılmadan önce ölen yazma
    codec.write_payload(event_file(store, ARS, "lineups"), lineups)
    codec.write_payload(event_file(store, ARS, "incidents"), {"incidents": []})
    codec.write_payload(event_file(store, ARS, "odds_all/1"), {"markets": [1]})
    leftover = event_file(store, ARS).with_name(".statistics.json.gz.ab12cd34.tmp")
    leftover.write_bytes(b"half")
    (event_file(store, ARS).parent / "notes.txt").write_bytes(b"x")  # dilim adı değil: dokunulmaz
    assert not store.catalog.verify(deep=True).ok

    with store._catalog.write():
        assert indexer.heal_v3_event(store.data_dir, ARS) is True
        assert indexer.heal_v3_event(store.data_dir, ARS) is False  # ikinci kez yapacak iş yok

    found = manifest_of(store, ARS)
    assert not leftover.exists()
    assert found.slices["statistics"] == before.slices["statistics"]  # değişmeyen dilime dokunulmadı
    event = found.slices["event"]
    assert (event.state, event.sha256) == ("ok", codec.encode(newer).sha256) and event.fetched_at > at()
    assert (found.observation.observed_at, found.observation.change_ts) == (event.fetched_at, 1790000123)
    assert (found.slices["lineups"].state, found.slices["lineups"].empty) == ("ok", None)
    assert (found.slices["incidents"].state, found.slices["incidents"].error) == ("empty", None)
    assert found.slices["odds_all/1"].state == "ok" and found.updated_at >= event.fetched_at
    store.catalog.index_event(ARS)
    assert store.events.payload(ARS)["winnerCode"] == 3
    report = store.catalog.verify(deep=True)
    assert [(i.invariant, i.kind) for i in report.issues] == [("I9", "unknown_file")]
    assert store.catalog.diff_from_rebuild() == []


def test_heal_leaves_what_it_cannot_judge(store: Store) -> None:
    basic = basic_of()
    store.events.put(ARS, {"event": ok(basic), "statistics": ok({"statistics": [1]})})
    with store._catalog.write():
        assert indexer.heal_v3_event(store.data_dir, 424242) is False  # dizini yok
        event_file(store, ARS, "statistics").write_bytes(b"not gzip")  # okunamayan yük: doğrulamanın işi
        codec.write_payload(event_file(store, ARS), {**basic, "id": ARS + 1})  # başka maçın yükü
        assert indexer.heal_v3_event(store.data_dir, ARS) is False
        path = Path(layout.resolve(store.data_dir, layout.manifest_path(layout.event_dir(ARS))))
        path.write_bytes(b"{")
        assert indexer.heal_v3_event(store.data_dir, ARS) is False and path.read_bytes() == b"{"


def test_pending_event_markers_are_healed_and_cleared_on_open(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    basic = basic_of()
    store.events.put(ARS, {"event": ok(basic, at())})
    stats = sf.slice_payload("statistics", basic)
    codec.write_payload(event_file(store, ARS, "statistics"), stats)
    with store._catalog.write() as conn:
        conn.execute("INSERT INTO pending_writes (kind, entity_id, started_at) VALUES ('event', ?, 1)", (ARS,))
        conn.execute("INSERT INTO pending_writes (kind, entity_id, started_at) VALUES ('event', 424242, 1)")
    assert [(i.invariant, i.kind) for i in store.catalog.verify().issues] == [("I6", "pending_write")] * 2
    store.close()

    again = open_store(tmp_path / "data")

    assert pending(again) == [] and again.events.payload(ARS, "statistics") == stats
    consistent(again)


def test_verify_repair_recovers_a_pending_event(store: Store) -> None:
    basic = basic_of()
    store.events.put(ARS, {"event": ok(basic)})
    codec.write_payload(event_file(store, ARS, "lineups"), sf.slice_payload("lineups", basic))
    with store._catalog.write() as conn:
        conn.execute("INSERT INTO pending_writes (kind, entity_id, started_at) VALUES ('event', ?, 1)", (ARS,))

    report = store.catalog.verify(deep=True, repair=True)

    # manifestin adını vermediği dosya (I9) onarımdan önce görülür; yarım yazmanın toparlanması onu kaydeder
    assert [(i.invariant, i.kind, i.repaired) for i in report.issues] == [
        ("I9", "unknown_file", False), ("I6", "pending_write", True)]
    assert store.events.slice(ARS, "lineups").state == "ok"
    consistent(store)


def test_the_next_put_recovers_an_unfinished_write_of_the_same_event(store: Store,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Manifest yazılamadan biten bir `put`: işaret kalır; aynı maça bir sonraki yazma önce dizini toparlar."""
    basic = basic_of()
    store.events.put(ARS, {"event": ok(basic, at())})
    stats = sf.slice_payload("statistics", basic)
    real = manifest.write_manifest

    def disk_full(*args: Any, **kwargs: Any) -> None:
        raise StoreError("disk dolu", errno_code=28)

    monkeypatch.setattr(manifest, "write_manifest", disk_full)
    with pytest.raises(StoreError, match="disk dolu"):
        store.events.put(ARS, {"statistics": ok(stats, at(1)), "lineups": gone(at(1))})
    assert pending(store) == [("event", ARS)]  # diske dokunuldu: işaret kaldı
    assert store.events.slice(ARS, "statistics").state == "not_requested"  # katalog geri alındı
    assert event_file(store, ARS, "statistics").exists()

    monkeypatch.setattr(manifest, "write_manifest", real)
    result = store.events.put(ARS, {"lineups": gone(at(2))})

    assert result.written == () and pending(store) == []
    assert store.events.slice(ARS, "statistics").state == "ok"  # yarım kalan yazmanın dosyası kaydedildi
    assert store.events.payload(ARS, "statistics") == stats
    assert store.events.slice(ARS, "lineups").empty_count == 1
    consistent(store)


def test_a_marker_removed_between_the_two_transactions_is_set_again(store: Store,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """
    İşaret ile yazma kilidi arasında başka bir sürecin açılıştaki uzlaştırması işareti silebilir. `put` bunu
    kilidi aldıktan sonra görür ve işareti yeniden koyar: dosyalar hiçbir zaman işaretsiz yazılmaz.
    """
    basic = basic_of()
    steps: List[Tuple[str, List[Tuple[str, int]]]] = []
    intruded: List[int] = []

    def checkpoint(step: str) -> None:
        if step == events_mod.STEP_MARKER and not intruded:
            intruded.append(store.catalog.reconcile().pending)  # "başka süreç": işareti yeniden dizinler ve siler
        steps.append((step, pending(store)))

    monkeypatch.setattr(store.events, "_checkpoint", checkpoint)
    result = store.events.put(ARS, {"event": ok(basic)})

    assert result.created and intruded == [1]
    assert [name for name, _ in steps] == ["marker", "marker", "locked", "staged", "published", "indexed", "commit",
                                           "done"]
    assert steps[0][1] == [] and steps[1][1] == [("event", ARS)]
    assert all(marked == [("event", ARS)] for name, marked in steps[1:5])
    assert steps[-1][1] == []
    consistent(store)

    # işaret her seferinde siliniyorsa yazma vazgeçer ve hiçbir şey yazmaz
    monkeypatch.setattr(store.events, "_checkpoint",
                        lambda step: store.catalog.reconcile() if step == events_mod.STEP_MARKER else None)
    with pytest.raises(sofascore_scraper.store.StoreBusy):
        store.events.put(ARS + 1, {"event": ok(basic_of(event_id=ARS + 1))})
    assert store.events.get(ARS + 1) is None and staging(store) == []


def test_a_corrupt_event_payload_can_be_written_anew(store: Store) -> None:
    """Olay yükü bozuk işaretlenen dizin geçerli bir maç değildir; `put` üzerine yeni yük yazabilmelidir."""
    basic = basic_of()
    stats = sf.slice_payload("statistics", basic)
    store.events.put(ARS, {"event": ok(basic, at()), "statistics": ok(stats, at())})
    event_file(store, ARS).write_bytes(b"\x1f\x8b broken")
    report = store.catalog.verify(deep=True, repair=True)
    assert ("I2", "payload") in [(i.invariant, i.kind) for i in report.issues]
    assert store.events.get(ARS) is None  # v3'ten başka kopyası yok: maç katalogdan düştü
    with pytest.raises(UnknownEvent):
        store.events.put(ARS, {"lineups": gone()})

    result = store.events.put(ARS, {"event": ok(basic, at(10))})

    assert result.event_written and not result.created
    found = manifest_of(store, ARS)
    assert (found.slices["event"].state, found.slices["event"].error) == ("ok", None)
    assert store.events.payload(ARS) == basic and store.events.payload(ARS, "statistics") == stats
    consistent(store)


def test_an_unreadable_manifest_is_written_anew_and_keeps_the_readable_payloads(store: Store) -> None:
    basic = basic_of()
    stats = sf.slice_payload("statistics", basic)
    store.events.put(ARS, {"event": ok(basic, at()), "statistics": ok(stats, at()), "lineups": gone(at())})
    path = Path(layout.resolve(store.data_dir, layout.manifest_path(layout.event_dir(ARS))))
    path.write_bytes(b"")  # elektrik kesintisinden sonra boş kalmış dosya

    with pytest.raises(sofascore_scraper.store.PayloadCorrupt):
        store.events.put(ARS, {"lineups": gone()})  # olay yükü gelmeden üzerine yazılmaz
    changed = basic_of(winnerCode=2)
    result = store.events.put(ARS, {"event": ok(changed, at(10))})

    assert result.event_written and not result.created and not result.promoted
    assert store.events.payload(ARS) == changed and store.events.payload(ARS, "statistics") == stats
    assert store.events.slice(ARS, "lineups").state == "not_requested"  # sayaçlar manifestteydi: kayboldu
    consistent(store)

    # manifesti hiç olmayan dizin de böyledir
    path.unlink()
    assert store.events.put(ARS, {"event": ok(basic, at(20))}).event_written
    assert store.events.payload(ARS, "statistics") == stats
    consistent(store)


def test_a_manifest_of_a_newer_format_is_not_overwritten(store: Store) -> None:
    basic = basic_of()
    store.events.put(ARS, {"event": ok(basic)})
    path = Path(layout.resolve(store.data_dir, layout.manifest_path(layout.event_dir(ARS))))
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "format": 2}), encoding="utf-8")
    newer = path.read_bytes()

    with pytest.raises(sofascore_scraper.store.SchemaTooNew):
        store.events.put(ARS, {"event": ok(basic_of(winnerCode=2))})
    assert path.read_bytes() == newer and pending(store) == []


def test_the_v3_entity_indexer_is_called_before_the_legacy_lists_and_for_pending_rows(
        canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    v3 varlık dizinlerinin taraması entities.py'nin işidir ve onları yazan adımla gelir (ST-22); dizinleyici
    iki giriş noktasını adıyla arar. Burada yerlerine sahteleri konur: çağrının yeri ve bağımsız değişkenleri.
    """
    calls: List[Tuple[Any, ...]] = []
    real_lists = entities.apply_season_lists

    def scan(cat: Any, data_dir: str, *, fresh: bool, problems: List[LegacyProblem]) -> None:
        calls.append(("scan", os.path.basename(data_dir), fresh))
        problems.append(LegacyProblem("v3/tournaments/17", "corrupt", "manifest"))

    def index(cat: Any, data_dir: str, kind: str, entity_id: int, *, problems: List[LegacyProblem]) -> None:
        calls.append(("index", kind, entity_id))

    def lists(*args: Any, **kwargs: Any) -> int:
        calls.append(("legacy_lists",))
        return real_lists(*args, **kwargs)

    store = open_store(canonical.data_dir)
    # ST-22 iki giriş noktasını ekledi (EntityStore.put ile birlikte)
    assert callable(getattr(entities, indexer.V3_ENTITY_SCAN)) and callable(getattr(entities, indexer.V3_ENTITY_INDEX))
    with store._catalog.write():
        store._catalog.upsert("pending_writes", [{"kind": "season", "entity_id": 96668, "started_at": 1},
                                                 {"kind": "tournament", "entity_id": 17, "started_at": 1}])

    monkeypatch.setattr(entities, indexer.V3_ENTITY_SCAN, scan, raising=False)
    monkeypatch.setattr(entities, indexer.V3_ENTITY_INDEX, index, raising=False)
    monkeypatch.setattr(entities, "apply_season_lists", lists)

    report = store.catalog.reconcile()
    assert (report.pending, report.pending_skipped) == (2, 0) and pending(store) == []
    assert calls == [("index", "season", 96668), ("index", "tournament", 17)]

    calls.clear()
    rebuilt = store.catalog.rebuild()
    assert calls[:2] == [("scan", "data", True), ("legacy_lists",)]
    assert [(p.layout, p.path, p.kind) for p in rebuilt.problems] == [("v3", "v3/tournaments/17", "corrupt")]


# --- yeniden kurma eşdeğerliği: rastgele yazma dizileri ---------------------------------------------------

@pytest.mark.parametrize("seed", range(4))
def test_random_write_sequences_leave_a_catalog_equal_to_a_rebuild(tmp_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    fixture = sf.build_fixture("legacy" if seed % 2 else "canonical", tmp_path / "data")
    store = open_store(fixture.data_dir)
    known = [e.id for e in store.events.iter(EventQuery(has_details=True))]
    fresh = [sf.PL_NO_DETAIL, sf.PL_FUTURE, sf.NBA_RECENT, sf.WIM_LIVE]
    clock = 0

    for step in range(30):
        clock += rng.randint(1, 900)
        when = at(clock)
        op = rng.choice(["put", "put", "put", "observe", "observe", "new", "reset", "delete"])
        if op == "new" and fresh:
            ev = fresh.pop()
            payload = sf.basic_payload(ev)
            store.events.put(payload["id"], {"event": ok(payload, when)})
            known.append(payload["id"])
        elif op == "reset":
            store.events.reset_empty_markers(include_confirmed=rng.random() < 0.3)
        elif op == "delete" and len(known) > 3:
            store.events.delete(known.pop(rng.randrange(len(known))))
        elif known:
            event_id = rng.choice(known)
            stored = store.events.payload(event_id)
            if op == "observe":
                payload = dict(stored)
                if rng.random() < 0.7:
                    payload["winnerCode"] = rng.randint(1, 3)
                store.events.observe(
                    event_id, payload, observed_at=when,
                    on_event_change=lambda old, new, when=when: change_of(
                        old, new, ts=when.isoformat(), status_regressed=rng.random() < 0.2))
            else:
                outcomes: Dict[Any, Outcome] = {}
                for key in rng.sample(SLICE_KEYS + ("point_by_point",), rng.randint(1, 4)):
                    data, outcome = _step_outcome(rng.choice(STEPS), key, stored)
                    if outcome is not None:
                        outcomes[key] = outcome
                if rng.random() < 0.2:
                    outcomes[("odds_all", str(rng.randint(1, 3)))] = ok({"markets": [rng.random()]})
                store.events.put(event_id, outcomes, count_empties=rng.choice([True, True, False, ("lineups",)]))
        if step % 5 == 4:  # her beş yazmada bir (yeniden kurma bütün ağacı okur); sonda bir kez daha
            assert store.catalog.diff_from_rebuild() == [], (seed, step, op)

    consistent(store)
    snapshot = [tuple(r) for r in store._catalog.connection().execute(
        "SELECT id, layout, sig, updated_at FROM events ORDER BY id")]
    data_dir = store.data_dir
    store.close()
    again = open_store(data_dir)  # açılıştaki uzlaştırma hiçbir şey bulmaz
    assert [tuple(r) for r in again._catalog.connection().execute(
        "SELECT id, layout, sig, updated_at FROM events ORDER BY id")] == snapshot
    assert not again.catalog.reconcile(v3=True).changed
    consistent(again)


# --- mantıksal döküm: v3 ağacı --------------------------------------------------------------------------

def _write_entity(data_dir: Path, rel: str, kind: str, entity_id: int, payloads: Mapping[str, Any],
                  metas: Optional[Mapping[str, Any]] = None) -> None:
    found = manifest.Manifest(kind=kind, id=entity_id, created_at=T0, updated_at=T0)
    for name, payload in payloads.items():
        key, sub = layout.split_slice_name(name)
        encoded = codec.write_payload(layout.resolve(data_dir, layout.slice_path(rel, key, sub)), payload)
        found.slices[name] = manifest.SliceEntry(
            state="ok", fetched_at=T0, stored_bytes=encoded.stored_bytes, raw_bytes=encoded.raw_bytes,
            sha256=encoded.sha256, meta=dict((metas or {}).get(name) or {}) or None)
    manifest.write_manifest(layout.resolve(data_dir, layout.manifest_path(rel)), found)


def test_the_logical_dump_covers_v3_trees_and_prefers_them_over_legacy(canonical: sf.LegacyFixture) -> None:
    data_dir = canonical.data_dir
    legacy_only = store_dump.dump(data_dir, canonical.leagues)
    assert store_dump.dump_v3(data_dir) == {"events": {}, "schedules": {}, "season_lists": {}, "changes": []}
    assert legacy_only == store_dump.dump_legacy(data_dir, canonical.leagues)

    store = open_store(data_dir)
    basic = store.events.payload(ARS)
    stats = {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": [{"key": "x"}]}]}]}
    store.events.put(ARS, {"statistics": ok(stats), ("odds_all", "1"): ok({"markets": []}), "lineups": gone(),
                           "incidents": failed("5xx", 503)})
    store.changes.append(line_of(ARS))
    seasons = {"seasons": [{"id": 1}, {"id": 2}, {"id": 3}]}
    page = {"events": [basic]}
    _write_entity(data_dir, layout.tournament_dir(17), "tournament", 17, {"seasons": seasons})
    _write_entity(data_dir, layout.season_dir(17, 96668), "season", 96668,
                  {"schedule/round_1": page, "schedule/last_0": page, "standings/total": {"standings": []}},
                  {"schedule/round_1": {"complete": True}})

    v3 = store_dump.dump_v3(data_dir)
    merged = store_dump.dump(data_dir, canonical.leagues)

    assert list(v3["events"]) == [str(ARS)]
    slices = v3["events"][str(ARS)]["slices"]
    assert slices["statistics"]["sha256"] == store_dump.payload_hash(stats)
    assert slices["odds_all/1"]["state"] == "ok" and slices["lineups"]["empty_count"] == 1
    assert slices["incidents"] == {"state": "ok", "sha256": slices["incidents"]["sha256"], "empty_count": 0,
                                   "unverified_empty_count": 0, "error": {"reason": "5xx", "status": 503, "count": 1}}
    assert v3["season_lists"] == {"17": {"sha256": store_dump.payload_hash(seasons), "seasons": 3}}
    assert v3["schedules"] == {"17/96668": {
        "round_1": {"sha256": store_dump.payload_hash(page), "meta": {"complete": True}},
        "last_0": {"sha256": store_dump.payload_hash(page), "meta": {}}}}
    assert v3["changes"] == [{"seq": 3, "row": line_of(ARS)}]
    # birleşim: v3'teki kopya geçerli, gerisi eski düzenden
    assert merged["events"][str(ARS)] == v3["events"][str(ARS)] != legacy_only["events"][str(ARS)]
    assert merged["events"][str(LIV)] == legacy_only["events"][str(LIV)]
    assert list(merged["events"]) == sorted(merged["events"], key=int)
    assert merged["season_lists"]["17"]["seasons"] == 3 and merged["season_lists"]["19"] == legacy_only["season_lists"]["19"]
    assert merged["schedules"]["17/96668"]["round_1"]["sha256"] == store_dump.payload_hash(page)
    assert merged["schedules"]["17/96668"]["round_2"] == legacy_only["schedules"]["17/96668"]["round_2"]
    assert [c["seq"] for c in merged["changes"]] == [1, 2, 3]
    # manifestle uyuşmayan dosya dökümde görünür: özet dosyadan alınır
    codec.write_payload(event_file(store, ARS, "statistics"), {"statistics": []})
    assert store_dump.dump_v3_event(data_dir, ARS)["slices"]["statistics"]["sha256"] != slices["statistics"]["sha256"]
    assert store_dump.dump_v3_event(data_dir, 424242) is None
    assert store_dump.dump_legacy_event(data_dir, "match_details/nope") is None


def test_references_and_read_api_see_a_written_event(store: Store) -> None:
    """Yazılan maç okuma API'sinin her yerinden görünür (liste, durumlar, eksikler, turnuva ve yarışmacılar)."""
    basic = basic_of()
    store.events.put(ARS, {"event": ok(basic, at()), "statistics": ok(sf.slice_payload("statistics", basic), at())})

    assert [e.id for e in store.events.list(EventQuery()).items] == [ARS]
    (state,) = list(store.events.states(Scope(event_ids=[ARS])))
    assert state.slice("statistics").state == "ok" and state.slice("lineups").state == "not_requested"
    assert store.entities.tournament(17).name == "Premier League"
    assert {p.name for p in store.entities.participants()} == {"Arsenal", "Chelsea"}
    assert Ref.event(ARS).kind == "event"
    assert store.events.refresh_candidates(now=NOW + 86400, window_s=10 ** 9, min_interval_s=0) == [ARS]
