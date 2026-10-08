"""
Her durumdaki maç saklanır; bayatlamış kayıt yenilenir (plan maddesi ST-27; docs/design/01-storage.md 8.2 kural 3
ve 8.3, docs/design/02-services.md 3.2).

Ağ yok: istekler tests/fakes/sofascore.py'deki sahte taşıyıcıya gider (G-01'in dünyası); istek katmanı, liste
servisi, planlayıcı, boru hattı ve Store gerçektir. Sınananlar:

  * liste sayfası her durumdaki maçıyla saklanır: katalogda başlamamış, oynanan, ertelenmiş ve bitmiş maç vardır;
  * "yalnızca bitmiş maçlar" (FETCH_ONLY_FINISHED) okurken uygulanır: açıkken listeler, eskiden süzülerek yazılmış
    bir sezonla aynıdır; kapalıyken fikstürler de görünür;
  * indirmeler ayardan bağımsızdır: bitmiş maç başına istek sayısı değişmez, bitmemiş maç için istek yoktur;
  * bayat akış: skoru farklı daha yeni bir liste kaydı bayatlatır, yenileme /event'i yeniden okur, değişiklik
    günlüğüne satır yazılır ve bayraklar kalkar.
"""
from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest

import detail_records
from characterization import WORLD, pin_default_settings
from fakes.sofascore import SITE_ROOT, FakeSofaScore
from sofascore_scraper import utils
from sofascore_scraper.services import planning
from sofascore_scraper.services.listing import ListingService, ScheduleLister
from sofascore_scraper.services.pipeline import FetchPipeline, PipelineSummary
from sofascore_scraper.services.query import QueryService, RefreshPolicy
from sofascore_scraper.services.status import StatusService
from sofascore_scraper.sports import select_slices
from sofascore_scraper.store import Ref, Store, open_store

LEAGUE = 17
ROUND_SEASON = 61627  # haftalık turlar: 9100001-9100003 bitmiş, 9100004 başlamamış
PAGED_SEASON = 52186  # tur listesi haftalık değil: `events/last` ve `events/next` sayfaları
PAGED_FINISHED = 9100010
NOT_STARTED, POSTPONED, LIVE = 9100020, 9100021, 9100022
OPEN_IN_ROUNDS = 9100004
FINISHED_IN_ROUNDS = (9100001, 9100002, 9100003)
FINISHED_IDS = FINISHED_IN_ROUNDS + (PAGED_FINISHED,)
OPEN_IDS = (OPEN_IN_ROUNDS, NOT_STARTED, POSTPONED, LIVE)

_STATUS = {
    NOT_STARTED: ({"code": 0, "description": "Not started", "type": "notstarted"}, None),
    POSTPONED: ({"code": 60, "description": "Postponed", "type": "postponed"}, None),
    LIVE: ({"code": 6, "description": "1st half", "type": "inprogress"}, 1),
}


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    pin_default_settings(monkeypatch)
    monkeypatch.delenv("FETCH_ONLY_FINISHED", raising=False)


def _listed(event: Dict[str, Any], event_id: int) -> Dict[str, Any]:
    """Sezon sayfasındaki bitmiş maçtan türetilmiş, `event_id` durumundaki bir liste nesnesi."""
    status, score = _STATUS[event_id]
    found = copy.deepcopy(event)
    found.update(id=event_id, slug=f"match-{event_id}", status=status, startTimestamp=event["startTimestamp"] + 7 * 86400)
    found.pop("winnerCode", None)
    found["changes"] = {"changeTimestamp": 0}
    for side in ("homeScore", "awayScore"):
        found[side] = {} if score is None else {"current": score, "display": score, "period1": score}
    return found


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        base = f"/unique-tournament/{LEAGUE}/season/{PAGED_SEASON}/events"
        finished = world.routes[f"{base}/last/0"]["events"][0]
        world.add(f"{base}/next/0", {"events": [_listed(finished, event_id) for event_id in (NOT_STARTED, POSTPONED,
                                                                                               LIVE)],
                                     "hasNextPage": False})
        yield world


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return open_store(tmp_path / "data")


def _list(store: Store, season_id: int, *, only_finished: bool = True) -> Any:
    return ListingService(store, only_finished=only_finished, concurrency=1).schedule(LEAGUE, season_id)


def _download(store: Store, *, only_finished: bool = True) -> PipelineSummary:
    """Eşitlemenin detay aşaması: listelerde geçen maçlar, planlayıcının kararı, boru hattı."""
    candidates = QueryService(store).detail_candidates(only_finished=only_finished)
    ids = [event_id for found in candidates.values() for event_id in found]
    items = planning.plan_items(store, ids, RefreshPolicy.current())
    return FetchPipeline(store, concurrency=1).run_sync(items)


def _event_requests(fake: FakeSofaScore) -> Dict[int, List[str]]:
    """Maç başına istenen yollar (/event ve dilimleri), kimlik → sıralı yollar."""
    found: Dict[int, List[str]] = {}
    for request in fake.requests:
        parts = request.path.split("/")
        if request.path != SITE_ROOT and len(parts) >= 3 and parts[1] == "event":
            found.setdefault(int(parts[2]), []).append(request.path)
    return {event_id: sorted(paths) for event_id, paths in sorted(found.items())}


# --- saklama --------------------------------------------------------------------------------------------

def test_a_listing_stores_matches_of_every_status(fake: FakeSofaScore, store: Store) -> None:
    result = _list(store, PAGED_SEASON)

    statuses = {event_id: store.events.get(event_id).status_class  # type: ignore[union-attr]
                for event_id in (PAGED_FINISHED, NOT_STARTED, POSTPONED, LIVE)}
    assert statuses == {PAGED_FINISHED: "completed", NOT_STARTED: "not_started", POSTPONED: "void", LIVE: "live"}
    assert all(not store.events.get(event_id).has_event_payload for event_id in statuses)  # type: ignore[union-attr]
    # Sonucun `chunks`'ı eskisi gibi bitmiş maçları sayar (eski çağıranın "maç listelendi mi" sorusu)
    assert result.ok and [[event["id"] for event in chunk["events"]] for chunk in result.chunks] == [[PAGED_FINISHED]]
    assert result.events == (PAGED_FINISHED, NOT_STARTED, POSTPONED, LIVE) and result.finished == (PAGED_FINISHED,)


def test_the_setting_filters_when_reading(fake: FakeSofaScore, store: Store, tmp_path: Path) -> None:
    """
    Açıkken okuyucular eskiden süzülerek yazılmış (yalnızca bitmiş maçlı sayfa) bir sezonla aynı satırları
    verir; kapalıyken fikstürler de görünür.
    """
    _list(store, PAGED_SEASON)
    old = open_store(tmp_path / "old")
    page = store.entities.payload(Ref.season(LEAGUE, PAGED_SEASON), "schedule", "last_0")
    ScheduleLister(old, only_finished=True).save_page(LEAGUE, PAGED_SEASON, "last_0", page, meta={"filtered": True})

    def listed(of: Store, only_finished: bool) -> List[int]:
        rows = QueryService(of).listed_events(tournament_ids=(LEAGUE,), season_ids=(PAGED_SEASON,),
                                              only_finished=only_finished)
        return [row.id for row in rows]

    for only_finished in (True, False):
        new_rows = listed(store, only_finished)
        new_count = StatusService(store).summary(only_finished=only_finished, sizes=False).matches
        if only_finished:
            assert new_rows == listed(old, True) == [PAGED_FINISHED]
            assert new_count == StatusService(old).summary(only_finished=True, sizes=False).matches == 1
        else:
            assert new_rows == [PAGED_FINISHED, NOT_STARTED, POSTPONED, LIVE]
            assert new_count == 4


# --- indirme --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("only_finished", [True, False])
def test_downloads_fetch_finished_matches_only_whatever_the_setting(
        fake: FakeSofaScore, store: Store, monkeypatch: pytest.MonkeyPatch, only_finished: bool) -> None:
    """
    Bitmiş maç başına istekler değişmez (/event ve seçili dilimler); yalnızca listeden bilinen bitmemiş maç
    indirilmez (maç bitince liste satırı değişir). Ayar kapalıyken de: ayar yalnızca okurken uygulanır.
    """
    monkeypatch.setenv("FETCH_ONLY_FINISHED", "true" if only_finished else "false")
    monkeypatch.setattr(utils, "FETCH_ONLY_FINISHED", only_finished)
    _list(store, ROUND_SEASON, only_finished=only_finished)
    _list(store, PAGED_SEASON, only_finished=only_finished)
    fake.reset_log()

    summary = _download(store, only_finished=only_finished)

    keys = [spec.key.replace("_", "-") for spec in select_slices("event", "football", phase="post")]
    expected = {event_id: sorted([f"/event/{event_id}"] + [f"/event/{event_id}/{key}" for key in keys])
                for event_id in FINISHED_IDS}
    assert _event_requests(fake) == expected
    assert summary.failed == 0 and summary.skipped == {}
    for event_id in OPEN_IDS:
        row = store.events.get(event_id)
        assert row is not None and not row.has_event_payload, event_id
    # İkinci çalıştırmada bitmemiş maçlar yine istenmez (9100002'nin "veri yok" dilimleri eşiğin altında: refill)
    fake.reset_log()
    _download(store, only_finished=only_finished)
    assert set(_event_requests(fake)) == {9100002}


def test_a_match_known_from_a_listing_is_downloaded_once_it_has_finished(fake: FakeSofaScore, store: Store) -> None:
    _list(store, PAGED_SEASON)
    _download(store)
    assert not store.events.get(NOT_STARTED).has_event_payload  # type: ignore[union-attr]

    base = f"/unique-tournament/{LEAGUE}/season/{PAGED_SEASON}/events"
    page = fake.routes[f"{base}/next/0"]
    finished = copy.deepcopy(fake.routes[f"{base}/last/0"]["events"][0])
    finished.update(id=NOT_STARTED, slug=f"match-{NOT_STARTED}")
    page["events"][0] = finished
    fake.add(f"{base}/next/0", page)
    fake.add_event(dict(finished))
    _list(store, PAGED_SEASON)
    assert store.events.get(NOT_STARTED).status_class == "completed"  # type: ignore[union-attr]
    fake.reset_log()

    _download(store)

    assert list(_event_requests(fake)) == [NOT_STARTED]
    assert store.events.get(NOT_STARTED).has_event_payload  # type: ignore[union-attr]


# --- bayat kayıt ----------------------------------------------------------------------------------------

def test_a_newer_listing_with_another_score_makes_the_record_stale_and_a_refresh_reads_it_again(
        fake: FakeSofaScore, store: Store) -> None:
    _list(store, PAGED_SEASON)
    _list(store, ROUND_SEASON)
    _download(store)
    assert store.events.stale() == []
    stored_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    detail_records.set_observed_at(store.data_dir, PAGED_FINISHED, stored_at)
    # 9100001 geçici bir kayıt: başlangıçtan 2 saat sonra gözlenmiş, son gözlemi eski
    start = fake.event(9100001)["startTimestamp"]
    detail_records.set_observed_at(store.data_dir, 9100001,
                                   dt.datetime.fromtimestamp(start + 2 * 3600, dt.timezone.utc))

    # SofaScore skoru düzeltti: liste de /event de yeni skoru gösteriyor
    corrected = copy.deepcopy(fake.event(PAGED_FINISHED))
    corrected["homeScore"] = dict(corrected["homeScore"], current=3, display=3, period2=3, normaltime=3)
    fake.add_event(corrected)
    base = f"/unique-tournament/{LEAGUE}/season/{PAGED_SEASON}/events"
    page = fake.routes[f"{base}/last/0"]
    page["events"][0]["homeScore"] = dict(corrected["homeScore"])
    fake.add(f"{base}/last/0", page)
    _list(store, PAGED_SEASON)

    assert store.events.stale() == [PAGED_FINISHED]
    policy = RefreshPolicy.current()
    assert [row.id for row in planning.refresh_due_events(store, policy)] == [PAGED_FINISHED, 9100001]
    assert planning.event_needs(store, [PAGED_FINISHED], policy) == {PAGED_FINISHED: "refresh"}
    [item] = planning.plan_items(store, [PAGED_FINISHED], policy)
    assert item.reason == "a newer listing differs from the stored record"
    last_seq = store.changes.last_seq()
    fake.reset_log()

    summary = FetchPipeline(store, concurrency=1).run_sync([item])

    assert summary.ok == 1 and _event_requests(fake) == {PAGED_FINISHED: [f"/event/{PAGED_FINISHED}"]}
    [change] = store.changes.list(after_seq=last_seq)
    assert change.event_id == PAGED_FINISHED and "homeScore.current" in change.fields
    assert change.row["changed"]["homeScore.current"] == [2, 3]
    row = store.events.get(PAGED_FINISHED)
    assert row is not None and not row.stale and row.home_score == 3
    assert store.events.stale() == []
    assert planning.event_needs(store, [PAGED_FINISHED], RefreshPolicy.current()) == {PAGED_FINISHED: "none"}


def test_the_refresh_policy_looks_at_settled_records_only(fake: FakeSofaScore, store: Store) -> None:
    """01-storage.md 8.3: bitmemiş bir duruma dönen kayıt açıktır; yenileme politikası ona bakmaz."""
    _list(store, ROUND_SEASON)
    _download(store)
    start = fake.event(9100001)["startTimestamp"]
    detail_records.set_observed_at(store.data_dir, 9100001,
                                   dt.datetime.fromtimestamp(start + 2 * 3600, dt.timezone.utc))
    policy = RefreshPolicy.current()
    assert [row.id for row in planning.refresh_due_events(store, policy)] == [9100001]

    live = copy.deepcopy(fake.event(9100001))
    live["status"] = {"code": 6, "description": "1st half", "type": "inprogress"}
    fake.add_event(live)
    [item] = planning.plan_items(store, [9100001], policy)
    FetchPipeline(store, concurrency=1).run_sync([item])
    assert store.events.get(9100001).status_class == "live"  # type: ignore[union-attr]

    # Pencere geniş, en az aralık sıfır: durum süzülmezse kayıt aday olurdu
    wide = RefreshPolicy(now=policy.now + 1, window_s=20 * 365 * 86400, min_interval_s=0)
    assert 9100001 in store.events.refresh_candidates(now=wide.now, window_s=wide.window_s, min_interval_s=0)
    assert not store.events.get(9100001).stale  # type: ignore[union-attr]
    assert 9100001 not in [row.id for row in planning.refresh_due_events(store, wide)]
    assert planning.event_needs(store, [9100001], wide) == {9100001: "none"}
