"""
Planlama (plan maddesi P12; src/services/planning.py): bir maçın ihtiyacı `compute_need` ile, katalogdaki
durumundan (`EventState`).

Üç küme:
  * tablo testleri: elle kurulan durumlarla her kural ve sınırı (dilim durumu, eşik, düzen, yenileme politikası,
    seçim, evre);
  * G-01 dünyası (tests/characterization/fixtures/fetch/world.json): sahte API'den indirilen maçların ihtiyacı,
    maç maç ve bir tablo halinde;
  * eşitlik: rastgele dilim, işaret ve gözlem durumlarında `planning.event_needs` ve `refresh_due_events`,
    RD-3'ün katalog sorgularıyla (`QueryService.detail_needs`, `refresh_due`) aynı kararları verir.
Ağ yok.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import random
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

import pytest

import store_fixtures as sf
from src.services import planning
from src.services.planning import WorkItem, compute_need, order_by_need, phase_of, refresh_due, work_item
from src.services.query import NEED_FULL, NEED_NONE, NEED_REFILL, NEED_REFRESH, QueryService, RefreshPolicy
from src.sports import SliceSelection, slices_for
from src.store import EventRow, EventState, Ref, Scope, SliceInfo, open_store
from test_store_read_api import HOUR, NOW, _random_details

COMMON = tuple(detail.key for detail in slices_for(None, required_only=True))
START = 1_700_000_000
POLICY = RefreshPolicy(now=START + 10 * HOUR, window_s=72 * HOUR, min_interval_s=6 * HOUR)
OFF = RefreshPolicy(now=START + 10 * HOUR, window_s=0, min_interval_s=0, include_unobserved=True)

_ROW_DEFAULTS: Dict[str, Any] = {field.name: None for field in dataclasses.fields(EventRow)}
_ROW_DEFAULTS.update(
    id=1, sport="football", start_ts=START, status_class="completed", status_regressed=False, stale=False,
    row_source="payload", has_event_payload=True, layout="v3", path="v3/events/0/0/1", first_seen_at=START,
    updated_at=START,
)


def row(**changes: Any) -> EventRow:
    return EventRow(**{**_ROW_DEFAULTS, **changes})


def info(key: str, state: str = "ok", *, empty: int = 0, unverified: int = 0, sub: str = "",
         event_id: int = 1) -> SliceInfo:
    return SliceInfo(ref=Ref.event(event_id), key=key, sub=sub, state=state, has_payload=state == "ok",
                     fetched_at=None, checked_at=None, empty_count=empty, unverified_empty_count=unverified,
                     error=None, stored_bytes=None, raw_bytes=None, history_count=0)


def complete(*, sport: str = "football", **changes: Any) -> EventState:
    """Bütün beklenen dilimleri `ok` olan, gözlemi olmayan (eski) bir kayıt."""
    return EventState(row(sport=sport, **changes), tuple(info(key) for key in COMMON))


def with_slice(state: EventState, replacement: SliceInfo) -> EventState:
    kept = tuple(s for s in state.slices if (s.key, s.sub) != (replacement.key, replacement.sub))
    return EventState(state.event, kept + (replacement,))


def without_slice(state: EventState, key: str) -> EventState:
    return EventState(state.event, tuple(s for s in state.slices if s.key != key))


# --- kurallar -----------------------------------------------------------------------------------------

def test_an_unknown_event_and_one_known_from_a_listing_only_need_a_full_fetch() -> None:
    assert compute_need(None, None, POLICY) == NEED_FULL
    listing = EventState(row(row_source="listing", has_event_payload=False, layout=None, path=None), ())
    assert compute_need(listing, None, POLICY) == NEED_FULL
    assert compute_need(listing, None, OFF) == NEED_FULL


def test_a_complete_record_needs_nothing() -> None:
    assert compute_need(complete(), None, POLICY) == NEED_NONE


SLICE_CASES = [
    # (dilim satırı, ihtiyaç): eşik 2
    (None, NEED_REFILL),  # satırı yok (not_requested)
    (info("lineups", "empty"), NEED_REFILL),
    (info("lineups", "empty", empty=1), NEED_REFILL),
    (info("lineups", "empty", empty=2), NEED_NONE),  # kesin iki "veri yok": artık beklenmez
    (info("lineups", "empty", empty=1, unverified=1), NEED_NONE),  # doğrulanmamış sayımlar da toplanır
    (info("lineups", "empty", unverified=3), NEED_NONE),
    (info("lineups", "error"), NEED_REFILL),
    (info("lineups", "error", empty=2), NEED_NONE),
    (info("lineups", "ok", empty=5), NEED_NONE),
    (info("lineups", "ok", sub="home"), NEED_REFILL),  # alt anahtarlı satır, ana dilimin yerini tutmaz
]


@pytest.mark.parametrize("replacement,expected", SLICE_CASES)
def test_a_missing_required_slice_needs_a_refill(replacement: Optional[SliceInfo], expected: str) -> None:
    state = without_slice(complete(), "lineups")
    if replacement is not None:
        state = with_slice(state, replacement)
    assert compute_need(state, None, POLICY) == expected
    assert compute_need(state, None, OFF) == expected


def test_the_threshold_is_the_callers() -> None:
    state = with_slice(complete(), info("lineups", "empty", empty=2))
    assert compute_need(state, None, POLICY, threshold=3) == NEED_REFILL
    assert compute_need(state, None, POLICY, threshold=2) == NEED_NONE


def test_an_optional_slice_is_not_awaited() -> None:
    tennis = complete(sport="tennis")
    assert "point_by_point" not in {s.key for s in tennis.slices}
    assert compute_need(tennis, None, POLICY) == NEED_NONE
    assert compute_need(complete(sport="handball"), None, POLICY) == NEED_NONE
    assert compute_need(complete(sport=""), None, POLICY) == NEED_NONE


def test_the_layout_filter_counts_only_records_of_that_layout() -> None:
    v3 = complete()
    assert compute_need(v3, None, POLICY, layout="legacy") == NEED_FULL
    legacy = complete(layout="legacy", path="match_details/17_PL/season_24_25/1")
    assert compute_need(legacy, None, POLICY, layout="legacy") == NEED_NONE
    no_path = complete(layout="legacy", path=None)
    assert compute_need(no_path, None, POLICY, layout="legacy") == NEED_FULL
    assert compute_need(no_path, None, POLICY) == NEED_NONE


REFRESH_CASES = [
    # (observed_at, observed_gap, include_unobserved, window_s, ihtiyaç)
    (START + 2 * HOUR, 2 * HOUR, False, 72 * HOUR, NEED_REFRESH),
    (START + 4 * HOUR, 4 * HOUR, False, 72 * HOUR, NEED_REFRESH),  # tam sınırda: now - min_interval
    (START + 4 * HOUR + 1, 4 * HOUR + 1, False, 72 * HOUR, NEED_NONE),  # son gözlem çok yeni
    (START + 2 * HOUR, 72 * HOUR, False, 72 * HOUR, NEED_NONE),  # pencere kapandıktan sonra gözlenmiş
    (START + 2 * HOUR, 72 * HOUR - 1, False, 72 * HOUR, NEED_REFRESH),
    (START + 2 * HOUR, None, False, 72 * HOUR, NEED_NONE),
    (START + 2 * HOUR, 2 * HOUR, False, 0, NEED_NONE),  # politika kapalı
    (None, None, False, 72 * HOUR, NEED_NONE),  # gözlemi yok
    (None, None, True, 72 * HOUR, NEED_REFRESH),  # --refresh-legacy
    (None, None, True, 0, NEED_NONE),
]


@pytest.mark.parametrize("observed_at,gap,unobserved,window_s,expected", REFRESH_CASES)
def test_a_provisional_record_is_refreshed_when_due(observed_at: Optional[int], gap: Optional[int],
                                                    unobserved: bool, window_s: int, expected: str) -> None:
    policy = RefreshPolicy(now=START + 10 * HOUR, window_s=window_s, min_interval_s=6 * HOUR,
                           include_unobserved=unobserved)
    state = complete(observed_at=observed_at, observed_gap=gap)
    assert compute_need(state, None, policy) == expected
    assert refresh_due(state.event, policy) is (expected == NEED_REFRESH)
    # eksik dilim yenilemeden önce gelir
    assert compute_need(without_slice(state, "h2h"), None, policy) == NEED_REFILL


def test_a_listing_row_is_never_due_for_a_refresh() -> None:
    policy = dataclasses.replace(POLICY, include_unobserved=True)
    assert not refresh_due(row(has_event_payload=False, observed_at=None), policy)


def test_the_selection_decides_which_slices_are_awaited() -> None:
    state = without_slice(complete(), "lineups")
    assert compute_need(state, None, POLICY) == NEED_REFILL
    assert compute_need(state, SliceSelection(disable=("lineups",)), POLICY) == NEED_NONE
    assert compute_need(state, ["statistics", "h2h"], POLICY) == NEED_NONE
    assert compute_need(state, ["core"], POLICY) == NEED_REFILL
    # tenisin isteğe bağlı dilimi seçilse de tamlık hesabına girmez
    assert compute_need(complete(sport="tennis"), ["core", "point_by_point"], POLICY) == NEED_NONE


@pytest.mark.parametrize("status_class,phase", [
    ("not_started", "pre"), ("void", "pre"), ("live", "live"), ("completed", "post"),
    ("decided_without_play", "post"), ("unknown", None), (None, None), ("", None),
])
def test_phase_of_status_class(status_class: Optional[str], phase: Optional[str]) -> None:
    assert phase_of(status_class) == phase


@pytest.mark.parametrize("status_class", ["not_started", "void", "unknown", "completed"])
def test_statuses_outside_the_live_phase_follow_the_refill_and_refresh_rules(status_class: str) -> None:
    """Başlamamış ve ertelenmiş maç da: kayıt defterinin bugünkü dilimleri ön maç evresinde de istenebilir (P13)."""
    state = complete(status_class=status_class)
    assert compute_need(state, None, POLICY) == NEED_NONE
    assert compute_need(without_slice(state, "incidents"), None, POLICY) == NEED_REFILL


def test_a_live_record_needs_nothing_it_belongs_to_the_live_service() -> None:
    """Tasarım tablosunun canlı satırı (P13): eksik dilim ya da yenileme zamanı onu indirmeye sokmaz."""
    live = complete(status_class="live")
    assert compute_need(without_slice(live, "incidents"), None, POLICY) == NEED_NONE
    due = complete(status_class="live", observed_at=START + 2 * HOUR, observed_gap=2 * HOUR)
    assert compute_need(due, None, POLICY) == NEED_NONE


def test_a_not_started_record_without_a_pre_match_slice_needs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tasarım tablosunun "başlamamış" satırı (P13): seçimde ön maç evresinde var olabilen dilim yoksa liste yeter."""
    import src.sports as sports

    post_only = tuple(dataclasses.replace(spec, phases=frozenset({"post"})) for spec in sports.DETAIL_SLICES)
    monkeypatch.setattr(sports, "DETAIL_SLICES", post_only)
    for status_class in ("not_started", "void"):
        assert compute_need(without_slice(complete(status_class=status_class), "incidents"), None, POLICY) == NEED_NONE
    assert compute_need(without_slice(complete(), "incidents"), None, POLICY) == NEED_REFILL


@pytest.mark.parametrize("status_class", ["not_started", "void", "live", "unknown", "completed"])
def test_a_stale_record_is_refreshed_first(status_class: str) -> None:
    """Tasarım tablosunun "bayat" satırı (P13): daha yeni bir liste kaydı farklı gösteriyorsa önce /event okunur."""
    stale = complete(status_class=status_class, stale=True)
    assert compute_need(stale, None, POLICY) == NEED_REFRESH
    assert compute_need(without_slice(stale, "incidents"), None, POLICY) == NEED_REFRESH
    assert work_item(1, stale, NEED_REFRESH).reason == "a newer listing differs from the stored record"


def test_work_items() -> None:
    state = without_slice(without_slice(complete(id=7), "h2h"), "statistics")
    assert work_item(7, state, NEED_REFILL) == WorkItem(
        Ref.event(7), "refill", (("statistics", ""), ("h2h", "")), "football", "missing slices: statistics, h2h")
    assert work_item(7, None, NEED_FULL) == WorkItem(Ref.event(7), "full", (), None, "unknown event")
    listing = EventState(row(id=7, has_event_payload=False), ())
    assert work_item(7, listing, NEED_FULL).reason == "known from a listing only"
    assert work_item(7, complete(id=7), NEED_REFRESH).need == "refresh"
    assert work_item(7, complete(id=7), NEED_NONE) is None
    with pytest.raises(ValueError):
        work_item(7, complete(id=7), "later")


def test_order_by_need_puts_refreshes_last() -> None:
    needs = {"1": NEED_REFRESH, "2": NEED_FULL, "3": NEED_NONE, "4": NEED_REFILL, "5": NEED_REFRESH}
    assert order_by_need([1, 2, 3, 4, 5], needs) == ([2, 4, 1, 5], 2)
    assert order_by_need(["5", "4"], needs) == (["4", "5"], 1)
    assert order_by_need([], needs) == ([], 0)


def test_compute_need_is_pure() -> None:
    """Saate ve depoya bakmaz: aynı argümanlar her zaman aynı kararı verir."""
    state = complete(observed_at=START + 2 * HOUR, observed_gap=2 * HOUR)
    first = compute_need(state, None, POLICY)
    assert all(compute_need(state, None, POLICY) == first for _ in range(3))
    assert compute_need(state, None, dataclasses.replace(POLICY, now=START + 5 * HOUR)) == NEED_NONE


# --- G-01 dünyası -------------------------------------------------------------------------------------

@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Tuple[Any, Path]]:
    from characterization import WORLD, pin_default_settings
    from fakes.sofascore import FakeSofaScore

    pin_default_settings(monkeypatch)
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    with FakeSofaScore.from_file(WORLD) as fake:
        yield fake, data_dir


WORLD_IDS = (9100001, 9100002, 9100003, 9100004, 9100010, 9200001, 9300001)


def _world_needs(data_dir: Path, policy: RefreshPolicy) -> Dict[int, str]:
    store = open_store(data_dir)
    states = {state.event.id: state for state in store.events.states()}
    needs = {event_id: compute_need(states.get(event_id), None, policy) for event_id in WORLD_IDS}
    assert planning.event_needs(store, WORLD_IDS, policy) == needs
    assert QueryService(store).detail_needs(WORLD_IDS, policy) == needs
    return needs


def test_needs_of_the_g01_world(world: Tuple[Any, Path]) -> None:
    import detail_records
    from src.web.routes.common import config_manager
    from src.match_data_fetcher import MatchDataFetcher

    fake, data_dir = world
    policy = RefreshPolicy.current()
    md = MatchDataFetcher(config_manager, data_dir=str(data_dir))
    md.fetch_matches_batch(list(WORLD_IDS))
    after_one_run = _world_needs(data_dir, policy)
    assert after_one_run == {
        9100001: NEED_NONE,  # tam
        9100002: NEED_REFILL,  # pregame-form 404 ve boş lineups: bir kez "veri yok", eşik 2
        9100003: NEED_NONE,
        9100004: after_one_run[9100004],  # başlamamış: aşağıda
        9100010: NEED_NONE,  # sayfalı sezonun maçı
        9200001: NEED_REFILL,  # tenis: yalnızca h2h, statistics ve point-by-point var; dört dilim bir kez 404
        9300001: after_one_run[9300001],  # oynanıyor: aşağıda
    }
    # Başlamamış ve oynanan maç: "yalnızca bitmiş maçlar" açıkken detayı indirilmez; kayıtları yoktur
    assert after_one_run[9100004] == NEED_FULL and after_one_run[9300001] == NEED_FULL

    md.fetch_matches_batch([9100002, 9200001])  # ikinci "veri yok": o dilimler artık beklenmez
    detail_records.drop_slices(data_dir, 9100001, "statistics")
    start = fake.event(9100010)["startTimestamp"]
    detail_records.set_observed_at(data_dir, 9100010, dt.datetime.fromtimestamp(start + 2 * 3600, dt.timezone.utc))
    assert _world_needs(data_dir, RefreshPolicy.current()) == {
        9100001: NEED_REFILL,
        9100002: NEED_NONE,
        9100003: NEED_NONE,
        9100004: NEED_FULL,
        9100010: NEED_REFRESH,
        9200001: NEED_NONE,
        9300001: NEED_FULL,
    }
    assert [row.id for row in planning.refresh_due_events(open_store(data_dir), RefreshPolicy.current())] == [9100010]
    assert md.refresh_due_ids() == ["9100010"]
    assert md.pending_detail_ids([str(i) for i in WORLD_IDS]) == ["9100001", "9100004", "9300001", "9100010"]


# --- eşitlik: RD-3'ün katalog sorguları -----------------------------------------------------------------

@pytest.mark.parametrize("seed", range(8))
def test_needs_equal_the_catalog_queries_of_rd3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seed: int) -> None:
    rng = random.Random(5000 + seed)
    window_h, min_interval_h = rng.choice([(72, 6), (1, 0), (24, 12), (200, 1)])
    builder = sf._Builder("random", tmp_path / "data", (sf.PL, sf.FA_CUP, sf.NBA, sf.WIMBLEDON, sf.LALIGA))
    for detail in _random_details(rng, window_h * HOUR, min_interval_h * HOUR):
        builder.detail(detail)
    fx = builder.fixture
    store = open_store(fx.data_dir)
    service = QueryService(store)
    ids = sorted({record.event_id for record in fx.details} | {1, 2})
    # P13 tasarım tablosunun canlı ve bayat satırlarını uyguladı; RD-3'ün SQL kopyası (src/services/query.py) bunları
    # bilmez. Eşitlik öteki kayıtlarda aranır; canlı kayıtta planlamanın kararı ayrıca sabitlenir.
    states = list(store.events.states(Scope(event_ids=tuple(ids))))
    live = {state.event.id for state in states
            if state.event.has_event_payload and phase_of(state.event.status_class) == "live"}
    assert not any(state.event.stale for state in states)
    shared = [event_id for event_id in ids if event_id not in live]
    for include_unobserved in (False, True):
        policy = RefreshPolicy(now=float(NOW), window_s=window_h * HOUR, min_interval_s=min_interval_h * HOUR,
                               include_unobserved=include_unobserved)
        for threshold in (1, 2, 3):
            for layout in (None, "legacy", "v3"):
                needs = planning.event_needs(store, ids, policy, threshold=threshold, layout=layout)
                assert {event_id: needs[event_id] for event_id in shared} == \
                    service.detail_needs(shared, policy, threshold=threshold, layout=layout)
                assert all(needs[event_id] in (NEED_NONE, NEED_FULL) for event_id in live)
                assert [r.id for r in planning.refresh_due_events(store, policy, threshold=threshold, layout=layout)] \
                    == [r.id for r in service.refresh_due(policy, threshold=threshold, layout=layout)
                        if r.id not in live]
        tournaments = (sf.PL.id,)
        assert [r.id for r in planning.refresh_due_events(store, policy, tournament_ids=tournaments)] == \
            [r.id for r in service.refresh_due(policy, tournament_ids=tournaments) if r.id not in live]


def test_identifiers_that_are_not_event_ids_are_left_out(tmp_path: Path) -> None:
    fx = sf.build_fixture("canonical", tmp_path / "data")
    store = open_store(fx.data_dir)
    stored = fx.detail_ids[0]
    needs = planning.event_needs(store, ["0" + str(stored), "abc", True, 2 ** 64, str(stored), stored], POLICY)
    assert list(needs) == [stored]
