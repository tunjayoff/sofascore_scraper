"""
Listeler: sezon listeleri ve sezon programları tipli iş birimleri olarak (sofascore_scraper/services/listing.py, plan maddesi P14).

Ağ yok: istekler tests/fakes/sofascore.py'deki sahte taşıyıcıya gider; istek katmanı, getirme boru hattı ve Store
gerçektir. Hatalar sahte taşıyıcıyla enjekte edilir. Akışların goldenları tests/characterization/test_fetch_flows.py'de.
"""
from __future__ import annotations

import errno
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

from characterization import WORLD, pin_default_settings
from fakes.sofascore import SITE_ROOT, FakeSofaScore
from sofascore_scraper import breaker as request_breaker
from sofascore_scraper.client.context import request_context
from sofascore_scraper.exceptions import StorageError
from sofascore_scraper.services import listing, planning
from sofascore_scraper.services.listing import ListingResult, ListingService, ScheduleLister, ScheduleRun
from sofascore_scraper.services.pipeline import FetchPipeline
from sofascore_scraper.slices import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, SLICE_SKIPPED, Outcome
from sofascore_scraper.store import Ref, open_store

LEAGUE = 17
WEEKS = 61627  # haftalık turlar: 1. tur 9100001 + 9100002 (bitti), 2. tur 9100003 (bitti) + 9100004 (başlamadı)
PAGES = 52186  # events/last/0 (9100010), events/next/0 404
BASE = f"/unique-tournament/{LEAGUE}"


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    pin_default_settings(monkeypatch)


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        yield world


@pytest.fixture
def store(tmp_path: Path) -> Any:
    return open_store(tmp_path / "data")


def _service(store: Any, **kwargs: Any) -> ListingService:
    kwargs.setdefault("only_finished", True)
    return ListingService(store, **kwargs)


def _api_paths(fake: FakeSofaScore) -> List[str]:
    return [path for path in fake.paths() if path != SITE_ROOT]


# --- saf kurallar -------------------------------------------------------------------------------------------

def test_week_based_rounds_are_sequential_league_weeks() -> None:
    assert listing.is_week_based_rounds([{"round": n} for n in range(1, 39)])
    assert not listing.is_week_based_rounds([{"round": 227, "slug": "western-conference-semifinals"}])
    assert not listing.is_week_based_rounds([])
    assert not listing.is_week_based_rounds([{"name": "no number"}])


def test_round_specs_keep_the_order_and_the_cup_slugs() -> None:
    rounds = [{"round": 2, "slug": "week-2"}, {"round": 1}, {"round": 99}, {"round": True}, {"round": "3"}]
    assert listing.round_specs(rounds) == [(2, "week-2"), (1, None)]
    assert listing.round_specs([{"round": 99}], max_round=3) == [(1, None), (2, None), (3, None)]


@pytest.mark.parametrize("args, sub", [
    (("round", 12), "round_12"), (("round", 1, "Final"), "round_1_final"),
    (("round", 3, "quarter finals"), "round_3_quarter-finals"), (("last", 0), "last_0"), (("next", 2), "next_2"),
])
def test_schedule_subs_follow_the_legacy_file_names(args: Tuple[Any, ...], sub: str) -> None:
    assert listing.schedule_sub(*args) == sub


def test_an_unknown_schedule_page_kind_is_refused() -> None:
    with pytest.raises(ValueError):
        listing.schedule_sub("week", 1)


def _event(event_id: int, status_type: str = "finished", code: int = 100) -> Dict[str, Any]:
    return {"id": event_id, "status": {"type": status_type, "code": code, "description": "x"}}


def test_kept_page_follows_the_finished_only_switch() -> None:
    page = {"events": [_event(1), _event(2, "notstarted", 0)], "roundInfo": {"round": 4}}
    kept = listing.kept_page(page, only_finished=True)
    assert kept is not None and [e["id"] for e in kept["events"]] == [1] and kept["round"] == 4
    assert listing.kept_page(page, only_finished=False) == page
    assert listing.kept_page({"events": [_event(2, "notstarted", 0)]}, only_finished=True) is None
    assert listing.kept_page({"events": []}, only_finished=False) is None
    assert listing.kept_page(None, only_finished=True) is None


def test_a_round_is_complete_when_every_match_is_over_or_cancelled() -> None:
    assert listing.round_is_complete({"events": [_event(1), _event(2, "canceled", 70)]})
    assert not listing.round_is_complete({"events": [_event(1), _event(2, "notstarted", 0)]})
    assert not listing.round_is_complete({"events": []})


def test_round_result_names_the_round_and_drops_the_legacy_flag() -> None:
    result = listing.round_result({"events": [_event(1)], "_complete": True}, 7, only_finished=True)
    assert result == {"events": [_event(1)], "round": 7}
    assert listing.round_result({"events": [], "hasNextPage": False}, 7, only_finished=True) is None


@pytest.mark.parametrize("year, value", [
    ("24/25", 2024.0), ("2024/2025", 2024.0), ("2024", 2024.0), ("98/99", 1998.0), ("99/00", 2000.0), ("", 0.0),
    ("0", 0.0), ("ab/cd", 0.0), ("abcd", 0.0), (None, 0.0),
])
def test_sortable_year(year: Any, value: float) -> None:
    assert listing.sortable_year(year) == value


def test_resolve_season_id_maps_a_retired_id_onto_the_preferred_season() -> None:
    seasons = [{"id": 97436, "year": "26/27"}, {"id": 77806, "year": "25/26"}]
    assert listing.preferred_season_id(seasons) == 77806
    assert listing.resolve_season_id(seasons, 77806, lambda: 0) == 77806
    assert listing.resolve_season_id(seasons, 77559, lambda: listing.preferred_season_id(seasons)) == 77806
    assert listing.resolve_season_id([], 77559, lambda: 1) == 77559
    assert listing.preferred_season_id([]) == 0


def test_season_list_of_needs_a_list() -> None:
    assert listing.season_list_of({"seasons": []}) == []
    for bad in (None, {}, {"seasons": None}, [], {"error": {"code": 500}}):
        assert listing.season_list_of(bad) is None


def test_planning_builds_the_two_listing_items() -> None:
    seasons = planning.season_list_item(LEAGUE)
    schedule = planning.schedule_item(LEAGUE, WEEKS)
    assert (seasons.owner, seasons.need, seasons.slices) == (Ref.tournament(LEAGUE), "listing", (("seasons", ""),))
    assert (schedule.owner, schedule.need, schedule.slices) == (Ref.season(LEAGUE, WEEKS), "listing",
                                                                (("schedule", ""),))


# --- sezon listesi ------------------------------------------------------------------------------------------

def test_a_season_list_is_fetched_once_and_stored(fake: FakeSofaScore, store: Any) -> None:
    result = _service(store).season_list(LEAGUE)

    assert result.ok and [s["id"] for s in result.seasons or []] == [WEEKS, PAGES]
    assert _api_paths(fake) == [f"{BASE}/seasons"]
    assert len(fake.sessions) == 1  # çalıştırmanın tek ısıtılmış oturumu
    assert store.entities.slice(Ref.tournament(LEAGUE), "seasons").state == "ok"
    assert [s.id for s in store.entities.seasons(LEAGUE)] == [WEEKS, PAGES]


@pytest.mark.parametrize("status, reason", [(403, "403"), (429, "429"), (500, "5xx")])
def test_a_refused_season_list_is_a_failed_item(fake: FakeSofaScore, store: Any, status: int, reason: str) -> None:
    fake.fail(f"{BASE}/seasons", status)

    result = _service(store).season_list(LEAGUE)

    assert result.failed and result.reason == reason
    assert result.failures == ((f"{BASE}/seasons", reason),)
    assert fake.count(f"{BASE}/seasons") == 3  # istek katmanının denemeleri (MAX_RETRIES)
    assert store.entities.slice(Ref.tournament(LEAGUE), "seasons").state == "not_requested"


def test_an_unknown_tournament_is_a_failed_item_not_an_empty_list(fake: FakeSofaScore, store: Any) -> None:
    result = _service(store).season_list(424242)

    assert result.failed and result.reason == "not_found"
    assert store.entities.slice(Ref.tournament(424242), "seasons").state == "not_requested"


@pytest.mark.parametrize("body", [{"error": {"code": 500}}, {"seasons": None}, ["not", "a", "list"]])
def test_a_season_list_of_an_unexpected_shape_is_a_failed_item(fake: FakeSofaScore, store: Any, body: Any) -> None:
    fake.add(f"{BASE}/seasons", body)

    result = _service(store).season_list(LEAGUE)

    assert result.failed and result.reason == "parse"
    assert store.entities.slice(Ref.tournament(LEAGUE), "seasons").state == "not_requested"


def test_an_empty_season_list_is_stored_as_empty(fake: FakeSofaScore, store: Any) -> None:
    fake.add(f"{BASE}/seasons", {"seasons": []})

    result = _service(store).season_list(LEAGUE)

    assert result.ok and result.seasons == []
    assert store.entities.slice(Ref.tournament(LEAGUE), "seasons").state == "empty"


def test_a_fresh_season_list_is_not_requested_again(fake: FakeSofaScore, store: Any) -> None:
    service = _service(store)
    service.season_list(LEAGUE)
    fake.reset_log()

    again = service.season_list(LEAGUE, max_age=60)

    assert again.skipped and again.fresh and again.reason == "fresh"
    assert fake.requests == [] and fake.sessions == []  # istek yok, oturum da açılmaz

    later = _service(store, clock=lambda: time.time() + 120).season_list(LEAGUE, max_age=60)
    assert later.ok and _api_paths(fake) == [f"{BASE}/seasons"]


def test_an_open_breaker_skips_the_season_list(fake: FakeSofaScore, store: Any) -> None:
    breaker = request_breaker.CircuitBreaker(consecutive=lambda: 1, ratio=lambda: 1.0)
    fake.fail("/event/1", 403)
    with request_context(breaker=breaker):
        from sofascore_scraper.client import Client

        Client().get_sync("/event/1")
        assert breaker.tripped
        fake.reset_log()
        result = _service(store).season_list(LEAGUE)

    assert result.skipped and result.reason == "breaker"
    assert fake.requests == []


def test_a_storage_error_fails_the_season_list_and_a_fatal_one_stops_the_run(
    fake: FakeSofaScore, store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def busy(*_args: Any, **_kwargs: Any) -> Any:
        raise StorageError("locked", path=str(store.data_dir))

    monkeypatch.setattr(store.entities, "put", busy)
    result = _service(store).season_list(LEAGUE)
    assert result.failed and result.reason == "storage" and result.seasons is not None

    def full(*_args: Any, **_kwargs: Any) -> Any:
        raise StorageError("disk full", path=str(store.data_dir), errno_code=errno.ENOSPC, detail="disk full")

    monkeypatch.setattr(store.entities, "put", full)
    with pytest.raises(StorageError):
        _service(store).season_list(LEAGUE)


# --- program ------------------------------------------------------------------------------------------------

def _pages(store: Any, season_id: int) -> Dict[str, Any]:
    return {info.sub: dict(info.meta) for info in store.entities.slices(Ref.season(LEAGUE, season_id))
            if info.key == "schedule"}


def test_a_week_based_schedule_stores_every_round_with_its_complete_flag(fake: FakeSofaScore, store: Any) -> None:
    result = _service(store).schedule(LEAGUE, WEEKS)

    assert result.ok and result.has_matches and result.pages == 2
    assert [chunk["round"] for chunk in result.chunks] == [1, 2]
    assert [e["id"] for e in result.chunks[1]["events"]] == [9100003]  # yalnızca bitmiş maçlar özette
    assert result.events == (9100001, 9100002, 9100003, 9100004)
    assert result.finished == (9100001, 9100002, 9100003)
    assert _pages(store, WEEKS) == {"round_1": {"complete": True}, "round_2": {"complete": False}}
    assert sorted(_api_paths(fake)) == [f"{BASE}/season/{WEEKS}/events/round/1", f"{BASE}/season/{WEEKS}/events/round/2",
                                        f"{BASE}/season/{WEEKS}/rounds"]
    assert len(fake.sessions) == 1


def test_a_schedule_without_week_rounds_uses_the_event_pages(fake: FakeSofaScore, store: Any) -> None:
    result = _service(store).schedule(LEAGUE, PAGES)

    assert result.ok and result.has_matches  # events/next/0'ın 404'ü sayfaların sonudur, hata değil
    assert _pages(store, PAGES) == {"last_0": {"filtered": True}}
    assert _api_paths(fake) == [f"{BASE}/season/{PAGES}/rounds", f"{BASE}/season/{PAGES}/events/last/0",
                                f"{BASE}/season/{PAGES}/events/next/0"]


def test_a_failed_round_is_a_failed_item_and_the_other_rounds_are_kept(fake: FakeSofaScore, store: Any) -> None:
    fake.fail(f"{BASE}/season/{WEEKS}/events/round/2", 500)

    result = _service(store).schedule(LEAGUE, WEEKS)

    assert result.failed and result.reason == "5xx"
    assert result.failures == ((f"{BASE}/season/{WEEKS}/events/round/2", "5xx"),)
    assert _pages(store, WEEKS) == {"round_1": {"complete": True}}
    assert result.has_matches  # 1. turun maçları listelendi; iş yine de başarısız bir birimdir

    fake.clear_faults()
    fake.reset_log()
    again = _service(store).schedule(LEAGUE, WEEKS)
    assert again.ok
    assert sorted(_api_paths(fake)) == [f"{BASE}/season/{WEEKS}/events/round/2", f"{BASE}/season/{WEEKS}/rounds"]


def test_a_failed_rounds_list_is_a_failed_item_and_the_event_pages_are_tried(fake: FakeSofaScore, store: Any) -> None:
    fake.fail(f"{BASE}/season/{WEEKS}/rounds", 403)

    result = _service(store).schedule(LEAGUE, WEEKS)

    assert result.failed and result.reason == "403"
    assert fake.count(f"{BASE}/season/{WEEKS}/rounds") == 1  # tur listesinin tek denemesi (eski kodla aynı)
    assert f"{BASE}/season/{WEEKS}/events/last/0" in fake.paths()


def test_a_malformed_round_is_a_failed_item(fake: FakeSofaScore, store: Any) -> None:
    fake.add(f"{BASE}/season/{WEEKS}/events/round/1", ["not", "a", "round"])

    result = _service(store).schedule(LEAGUE, WEEKS)

    assert result.failed and result.reason == "parse"
    assert _pages(store, WEEKS) == {"round_2": {"complete": False}}


def test_a_failed_event_page_is_a_failed_item(fake: FakeSofaScore, store: Any) -> None:
    fake.timeout(f"{BASE}/season/{PAGES}/events/last/0")

    result = _service(store).schedule(LEAGUE, PAGES)

    assert result.failed and result.reason == "timeout" and not result.has_matches
    assert fake.count(f"{BASE}/season/{PAGES}/events/last/0") == 2  # olay sayfasının iki denemesi (eski kodla aynı)


def test_an_unknown_season_lists_nothing_and_is_not_a_failure(fake: FakeSofaScore, store: Any) -> None:
    result = _service(store).schedule(LEAGUE, 1)

    assert result.ok and not result.has_matches and result.pages == 0


def test_a_complete_round_is_not_requested_again_and_an_incomplete_one_after_its_ttl(
    fake: FakeSofaScore, store: Any
) -> None:
    _service(store).schedule(LEAGUE, WEEKS)
    fake.reset_log()

    _service(store).schedule(LEAGUE, WEEKS)
    assert _api_paths(fake) == [f"{BASE}/season/{WEEKS}/rounds"]  # iki tur da önbellekte

    fake.reset_log()
    later = _service(store, clock=lambda: time.time() + listing.ROUND_CACHE_TTL_SECONDS + 1)
    result = later.schedule(LEAGUE, WEEKS)
    assert result.ok and result.events == (9100001, 9100002, 9100003, 9100004)
    assert sorted(_api_paths(fake)) == [f"{BASE}/season/{WEEKS}/events/round/2", f"{BASE}/season/{WEEKS}/rounds"]


def test_a_fresh_schedule_is_not_requested_again(fake: FakeSofaScore, store: Any) -> None:
    _service(store).schedule(LEAGUE, WEEKS)
    _service(store).schedule(LEAGUE, PAGES)
    fake.reset_log()

    weeks = _service(store).schedule(LEAGUE, WEEKS, max_age=60)
    pages = _service(store).schedule(LEAGUE, PAGES, max_age=60)

    assert weeks.fresh and weeks.has_matches and pages.fresh and pages.has_matches
    assert fake.requests == [] and fake.sessions == []

    later = _service(store, clock=lambda: time.time() + 120)
    assert not later.schedule(LEAGUE, PAGES, max_age=60).fresh
    assert _api_paths(fake) == [f"{BASE}/season/{PAGES}/rounds", f"{BASE}/season/{PAGES}/events/last/0",
                                f"{BASE}/season/{PAGES}/events/next/0"]


def test_schedule_freshness_rule(store: Any) -> None:
    now = time.time()
    assert not listing.schedule_is_fresh(store, LEAGUE, WEEKS, 60, now=now)  # sayfası yok

    lister = ScheduleLister(store, only_finished=True)
    import datetime as dt

    def save(sub: str, age: float, complete: bool) -> None:
        at = dt.datetime.fromtimestamp(now - age, dt.timezone.utc)
        lister.save_page(LEAGUE, WEEKS, sub, {"events": [_event(1)]}, meta={"complete": complete}, fetched_at=at)

    save("round_1", 3600, True)
    assert not listing.schedule_is_fresh(store, LEAGUE, WEEKS, 60, now=now)  # en yeni sayfa eski
    save("round_2", 10, False)
    assert listing.schedule_is_fresh(store, LEAGUE, WEEKS, 60, now=now)  # eski olan tamamlanmış
    save("round_3", 3600, False)
    assert not listing.schedule_is_fresh(store, LEAGUE, WEEKS, 60, now=now)  # eski ve tamamlanmamış sayfa


def test_an_open_breaker_skips_the_schedule(fake: FakeSofaScore, store: Any) -> None:
    breaker = request_breaker.CircuitBreaker(consecutive=lambda: 1, ratio=lambda: 1.0)
    fake.fail(f"{BASE}/season/{WEEKS}/rounds", 403)
    with request_context(breaker=breaker):
        result = _service(store).schedule(LEAGUE, WEEKS)
    # Tur listesinin 403'ü devreyi kesti: o istek başarısız, sonrakiler gönderilmedi
    assert result.failed and result.reason == "403"
    assert _api_paths(fake) == [f"{BASE}/season/{WEEKS}/rounds"]

    fake.reset_log()
    with request_context(breaker=breaker):
        skipped = _service(store).schedule(LEAGUE, PAGES)
    assert skipped.skipped and skipped.reason == "breaker" and fake.requests == []


def test_a_listing_that_reveals_finished_matches_enqueues_their_detail_items(fake: FakeSofaScore, store: Any) -> None:
    """Liste aynı çalıştırmada bitmiş ve eksik maçların detay birimlerini getirir; başlamamış maç gelmez."""
    service = _service(store, enqueue_events=True)

    summary = service.run([planning.schedule_item(LEAGUE, WEEKS)])

    assert [(r.item.owner.kind, r.item.need, r.status) for r in summary.results] == [
        ("season", "listing", "ok"), ("event", "full", "ok"), ("event", "full", "ok"), ("event", "full", "ok")]
    assert [r.item.owner.id for r in summary.results[1:]] == [9100001, 9100002, 9100003]
    assert summary.total == 4 and len(fake.sessions) == 1  # listeler ve detaylar tek oturumda
    assert store.events.get(9100001).has_event_payload and store.events.get(9100004) is not None
    assert not store.events.get(9100004).has_event_payload

    # 9100002'nin iki dilimi bir kez "veri yok" yanıtı aldı: eşiğe kadar bir kez daha istenir, sonra bir şey kalmaz
    again = service.run([planning.schedule_item(LEAGUE, WEEKS)])
    assert [(r.item.need, r.item.owner.id) for r in again.results] == [("listing", WEEKS), ("refill", 9100002)]
    third = service.run([planning.schedule_item(LEAGUE, WEEKS)])
    assert [r.item.need for r in third.results] == ["listing"]


def test_a_listing_item_needs_a_listing_handler(fake: FakeSofaScore, store: Any) -> None:
    with pytest.raises(ValueError):
        FetchPipeline(store).run_sync([planning.season_list_item(LEAGUE)])


def test_a_cancelled_run_reports_the_listing_as_cancelled(fake: FakeSofaScore, store: Any) -> None:
    summary = _service(store).run([planning.season_list_item(LEAGUE)], cancelled=lambda: True)
    assert summary.cancelled and summary.results == [] and fake.requests == []

    class Cancelled(ListingService):
        def run(self, items: Any, **kwargs: Any) -> Any:
            return super().run(items, cancelled=lambda: True)

    result = Cancelled(store, only_finished=True).season_list(LEAGUE)
    assert result.skipped and result.reason == "cancelled"


def test_listing_result_describes_its_owner() -> None:
    seasons = ListingResult.for_item(planning.season_list_item(LEAGUE))
    schedule = ListingResult.for_item(planning.schedule_item(LEAGUE, WEEKS), status="failed", reason="403")
    assert (seasons.kind, seasons.tournament_id, seasons.season_id, seasons.describe()) == (
        "seasons", LEAGUE, None, "league 17")
    assert schedule.describe() == "league 17, season 61627" and schedule.failed and not schedule.has_matches


# --- program kuralları, enjekte edilen istek ve yazmayla -------------------------------------------------------

class _Api:
    """Enjekte edilen istek işlevi: yol → sonuç; istenen yollar kaydedilir."""

    def __init__(self, answers: Dict[str, Outcome]) -> None:
        self.answers = answers
        self.calls: List[Tuple[str, Optional[int]]] = []

    async def __call__(self, path: str, retries: Optional[int] = None) -> Outcome:
        self.calls.append((path, retries))
        return self.answers.get(path, Outcome(SLICE_EMPTY, reason="404", http_status=404))


async def _inline(fn: Any) -> Any:
    return fn()


def _ok(data: Any) -> Outcome:
    return Outcome(SLICE_OK, data=data)


def _list(store: Any, api: _Api, season_id: int = 1, **kwargs: Any) -> ListingResult:
    import asyncio

    kwargs.setdefault("only_finished", True)
    return asyncio.run(ScheduleLister(store, **kwargs).list(LEAGUE, season_id, api, _inline))


def test_cup_rounds_are_requested_with_their_slugs(store: Any) -> None:
    base = f"{BASE}/season/1"
    api = _Api({f"{base}/rounds": _ok({"rounds": [{"round": 1, "slug": "week-1"}]}),
                f"{base}/events/round/1/slug/week-1": _ok({"events": [_event(9)]})})

    result = _list(store, api)

    assert result.ok and [chunk["round"] for chunk in result.chunks] == [1]
    assert [path for path, _ in api.calls] == [f"{base}/rounds", f"{base}/events/round/1/slug/week-1"]
    assert _pages(store, 1) == {"round_1_week-1": {"complete": True}}


def test_the_retry_counts_of_the_schedule_requests(store: Any) -> None:
    base = f"{BASE}/season/1"
    api = _Api({f"{base}/rounds": _ok({"rounds": [{"round": 1}]}), f"{base}/events/round/1": _ok({"events": []})})

    _list(store, api)

    assert api.calls == [(f"{base}/rounds", listing.ROUNDS_RETRIES), (f"{base}/events/round/1", None),
                         (f"{base}/events/last/0", listing.EVENT_PAGE_RETRIES),
                         (f"{base}/events/next/0", listing.EVENT_PAGE_RETRIES)]


def test_event_pages_are_deduplicated_and_paged(store: Any) -> None:
    base = f"{BASE}/season/1"
    api = _Api({
        f"{base}/events/last/0": _ok({"events": [_event(1), _event(2)], "hasNextPage": True}),
        f"{base}/events/last/1": _ok({"events": [_event(2), _event(3)], "hasNextPage": False}),
    })

    result = _list(store, api)

    assert result.ok and sorted(e["id"] for chunk in result.chunks for e in chunk["events"]) == [1, 2, 3]
    assert [chunk["round"] for chunk in result.chunks] == ["last_0", "last_1"]
    assert _pages(store, 1) == {"last_0": {"filtered": True}, "last_1": {"filtered": True}}


def test_event_pages_keep_unfinished_matches(store: Any) -> None:
    """ST-27: sayfa her durumdaki maçıyla saklanır; "yalnızca bitmiş maçlar" yalnızca sonucun `chunks`'ını süzer."""
    base = f"{BASE}/season/1"
    api = _Api({f"{base}/events/next/0": _ok({"events": [_event(5, "notstarted", 0)], "hasNextPage": False})})

    result = _list(store, api)
    assert not result.has_matches and result.chunks == []  # bitmiş maç yok: eski sonuç
    assert _pages(store, 1) == {"next_0": {"filtered": True}}
    assert store.events.get(5).status_class == "not_started"  # type: ignore[union-attr]
    result = _list(store, api, only_finished=False)
    assert result.has_matches and _pages(store, 1) == {"next_0": {"filtered": True}}


def test_empty_rounds_are_never_stored(store: Any) -> None:
    """ST-27: SAVE_EMPTY_ROUNDS emekli; eski çağıranların argümanı kabul edilir ve bir şey değiştirmez."""
    base = f"{BASE}/season/1"
    api = _Api({f"{base}/rounds": _ok({"rounds": [{"round": 1}]}),
                f"{base}/events/round/1": _ok({"events": [], "hasNextPage": False})})

    _list(store, api)
    assert _pages(store, 1) == {}
    _list(store, api, save_empty_rounds=True)
    assert _pages(store, 1) == {}


def test_rounds_that_list_only_unfinished_matches_fall_back_to_the_event_pages(store: Any) -> None:
    base = f"{BASE}/season/1"
    api = _Api({f"{base}/rounds": _ok({"rounds": [{"round": 1}]}),
                f"{base}/events/round/1": _ok({"events": [_event(5, "notstarted", 0)]}),
                f"{base}/events/last/0": _ok({"events": [_event(4)], "hasNextPage": False})})

    result = _list(store, api)

    assert result.ok and [chunk["round"] for chunk in result.chunks] == ["last_0"]
    assert result.events == (4, 5) and result.finished == (4,)


def test_a_skipped_request_makes_the_schedule_skipped(store: Any) -> None:
    base = f"{BASE}/season/1"
    api = _Api({f"{base}/rounds": Outcome(SLICE_SKIPPED, reason="breaker")})

    result = _list(store, api)

    assert result.skipped and result.reason == "breaker"
    assert [path for path, _ in api.calls] == [f"{base}/rounds"]  # devre açıkken sayfalara geçilmez


def test_the_most_frequent_failure_names_the_failed_schedule(store: Any) -> None:
    base = f"{BASE}/season/1"
    failed = Outcome(SLICE_FAILED, reason="5xx", http_status=500)
    api = _Api({f"{base}/rounds": _ok({"rounds": [{"round": n} for n in (1, 2, 3)]}),
                f"{base}/events/round/1": failed, f"{base}/events/round/2": failed,
                f"{base}/events/round/3": Outcome(SLICE_FAILED, reason="timeout"),
                f"{base}/events/last/0": Outcome(SLICE_FAILED, reason="timeout")})

    result = _list(store, api)

    assert result.failed and result.reason == "5xx"  # 2 × 5xx, 2 × timeout: eşitlikte ilk görülen sayılır
    assert len(result.failures) == 4


def test_the_schedule_run_records_the_answers() -> None:
    run = ScheduleRun()
    assert run.answered("/a", Outcome(SLICE_OK, data={}))
    assert not run.answered("/b", Outcome(SLICE_FAILED, reason="403"))
    assert not run.answered("/c", Outcome(SLICE_SKIPPED, reason="breaker"))
    run.saw({"events": [{"id": 1}, {"id": True}, "x", {"id": 1}]})
    assert [path for path, _ in run.failures] == ["/b"] and run.skipped and list(run.events) == [1]
