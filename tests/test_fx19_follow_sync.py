"""
Takım, oyuncu ve maç takipleri maçlarını indirir (plan maddesi FX-19; src/services/follow_sync.py ve
src/services/sync.py):

  * takımın listesi `next/0` ve `last/n` sayfaları, oyuncunun yalnızca `last/n`; pencere takibin `seasons`'ından
    (current = 365 gün, last:N, all = sayfa sınırı, sezon kimlikleri); okuma `hasNextPage`, sınır ya da pencereyle
    durur;
  * ihtiyaç: bitmiş ve eksik maç `full`; bilinmeyen ve bitmemiş maç yalnızca `/event`; tamam olan maç istenmez;
    maç takibinin başlaması gereken maçı yeniden okunur;
  * veri seçimi takiplerinkidir: takımın seçimi (P27, maçın takımı), oyuncunun seçimi listesinden gelen maçlarda;
  * eşitleme: adı verilen takipler (`FollowsSyncSpec`), hedefsiz eşitlemede her etkin takip; okunamayan liste
    `failed_listings`'te ve iş `partial`; iptal ve devre kesici;
  * `POST /api/v1/jobs` ile takım takibinin eşitlemesi uçtan uca (gerçek bağlam, sahte SofaScore);
  * `ssc sync --follow` ve `--dry-run`.

Ağ yok: istekler tests/fakes/sofascore.py'nin sahte taşıyıcısına gider (G-01'in dünyası). Liste sayfaları dünyanın
maç nesnelerinden kurulur; biçimleri tests/fixtures/fx19 (research/all_sports örnekleri) ile aynıdır.
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional

import pytest
from fastapi.testclient import TestClient

import conftest
from characterization import WORLD, pin_default_settings
from fakes.sofascore import FakeSofaScore
from src.client import Client, endpoints
from src.config import loader
from src.jobs.progress import JobProgress
from src.services import follow_sync, planning
from src.services.query import RefreshPolicy
from src.services.sync import FollowsSyncSpec, SyncService, SyncSpec
from src.slices import Outcome
from src.store import EventQuery, Follow, FollowSpec, JobStore, Ref, Scope, Store, default_db_path, open_store
from src.web import deps
from src.web.app import app
import test_cli_skeleton as skeleton
from test_fx13_sync_follows import FakeConfig, FakeDetails, FakeSchedule, FakeSeasons

FIXTURES = Path(__file__).parent / "fixtures" / "fx19"
TEAM = 42  # dünyada: 9100001 (bitti), 9100004 (2030, başlamadı), 9100010 (bitti, 2024), 9300001 (oynanıyor)
PLAYER = 7
NOW = 1725800000.0  # 2024-09-08; 9100010 (2024-05-19) bir yıldan yeni, 9100001 (2024-08-24) de
client = TestClient(app)
cli = skeleton.cli  # komut satırı fikstürü


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    pin_default_settings(monkeypatch)
    loader.reset()
    yield
    monkeypatch.undo()
    loader.reset()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setenv("DATA_DIR", str(path))
    return open_store(path)


def world_event(fake: FakeSofaScore, event_id: int) -> Dict[str, Any]:
    return copy.deepcopy(fake.event(event_id))


def page(fake: FakeSofaScore, ids: List[int], more: bool) -> Dict[str, Any]:
    return {"events": [world_event(fake, eid) for eid in ids], "hasNextPage": more}


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        world.add(endpoints.team_events_page(TEAM, "next", 0), page(world, [9100004], False))
        world.add(endpoints.team_events_page(TEAM, "last", 0), page(world, [9300001, 9100001], True))
        world.add(endpoints.team_events_page(TEAM, "last", 1), page(world, [9100010], False))
        world.add(endpoints.player_events_page(PLAYER, 0), page(world, [9100002, 9100003], False))
        yield world


def api_paths(fake: FakeSofaScore) -> List[str]:
    return [path for path in fake.paths() if path.startswith("/")]


def follow(store: Store, kind: str, entity_id: int, name: str, **fields: Any) -> Follow:
    return store.follows.add(FollowSpec(kind=kind, entity_id=entity_id, name=name, **fields), origin="api")


class Handle:
    id = "job-fx19"

    def __init__(self, spec: SyncSpec, *, cancel_after: Optional[int] = None) -> None:
        self.progress = JobProgress(list(spec.job_phases), lambda fields: None)
        self.lines: List[Dict[str, Any]] = []
        self.cancel_after = cancel_after
        self.asked = 0

    def cancelled(self) -> bool:
        self.asked += 1
        return self.cancel_after is not None and self.asked > self.cancel_after

    def log(self, message: str, **fields: Any) -> None:
        self.lines.append({"message": message, **fields})

    def publish(self, fields: Any) -> None:
        pass

    def codes(self) -> List[str]:
        return [line.get("code") for line in self.lines]


def context(store: Store) -> SimpleNamespace:
    config = FakeConfig()
    config.get_max_concurrent = lambda: 1  # type: ignore[attr-defined]
    return SimpleNamespace(config=config, data_dir=str(store.data_dir), store=store, season_fetcher=FakeSeasons(),
                           match_fetcher=FakeSchedule(), match_data_fetcher=FakeDetails())


def run(store: Store, spec: SyncSpec, **handle_options: Any) -> Any:
    handle = Handle(spec, **handle_options)
    result = SyncService(context(store)).run(spec, handle=handle)  # type: ignore[arg-type]
    return result, handle


def slice_paths(fake: FakeSofaScore, event_id: int) -> List[str]:
    return [path for path in api_paths(fake) if path.startswith(f"/event/{event_id}/")]


# --- uç noktalar ve liste biçimi -----------------------------------------------------------------------------


def test_the_list_paths_are_those_of_the_catalog() -> None:
    assert endpoints.team_events_page(4819, "last", 0) == json.loads(
        (FIXTURES / "team_events_last.json").read_text(encoding="utf-8"))["path"]
    assert endpoints.team_events_page(4422, "next", 0) == json.loads(
        (FIXTURES / "team_events_next.json").read_text(encoding="utf-8"))["path"]
    assert endpoints.player_events_page(823984, 0) == json.loads(
        (FIXTURES / "player_events_last.json").read_text(encoding="utf-8"))["path"]
    with pytest.raises(ValueError):
        endpoints.team_events_page(1, "previous", 0)


def test_the_research_samples_parse() -> None:
    body = json.loads((FIXTURES / "team_events_next.json").read_text(encoding="utf-8"))["body"]
    events, more = follow_sync.parse_page(body)
    assert [(e.id, e.status_class, e.tournament_id, e.season_id, e.sport) for e in events] == [
        (16183704, "not_started", 9464, 94366, "american-football"),
        (16183734, "not_started", 9464, 94366, "american-football"),
        (16183773, "not_started", 9464, 94366, "american-football")]
    assert more is False
    last = json.loads((FIXTURES / "team_events_last.json").read_text(encoding="utf-8"))["body"]
    assert [e.status_class for e in follow_sync.parse_page(last)[0]] == ["completed", "completed"]
    # Kısaltılmış örnekte oyuncu listesinin kimlikleri yok: kimliksiz maç atlanır
    player = json.loads((FIXTURES / "player_events_last.json").read_text(encoding="utf-8"))["body"]
    assert follow_sync.parse_page(player) == ([], True)
    with pytest.raises(ValueError):
        follow_sync.parse_page({"results": []})


@pytest.mark.parametrize("seasons, since, season_ids", [
    ("all", None, ()), ("current", NOW - follow_sync.YEAR_S, ()), ("last:3", NOW - 3 * follow_sync.YEAR_S, ()),
    ((61627, 5), None, (61627, 5)), ("odd", None, ()),
])
def test_the_window_of_a_follow(seasons: Any, since: Optional[float], season_ids: Any) -> None:
    window = follow_sync.window_of(seasons, now=NOW)
    assert (window.since, window.season_ids, window.pages) == (since, season_ids, follow_sync.MAX_LAST_PAGES)


# --- liste ---------------------------------------------------------------------------------------------------


def _row(kind: str, entity_id: int, seasons: Any = "all") -> Follow:
    return Follow(id=1, kind=kind, entity_id=entity_id, name="x", sport=None, seasons=seasons, slices=None,
                  live=False, enabled=True, origin="api", position=0, created_at=0, updated_at=0)


def test_a_team_list_reads_the_next_page_and_the_last_pages_back(fake: FakeSofaScore) -> None:
    listing = follow_sync.list_follow(_row("team", TEAM), Client().get_sync, now=NOW)
    assert api_paths(fake) == ["/team/42/events/next/0", "/team/42/events/last/0", "/team/42/events/last/1"]
    assert [e.id for e in listing.events] == [9100004, 9300001, 9100001, 9100010]
    assert (listing.requests, listing.failed) == (3, None)


def test_the_window_stops_the_reading_and_filters(fake: FakeSofaScore) -> None:
    # current: 9100010 (2024-05) son 365 günde; 2026'dan bakınca değil: ilk sayfanın en eskisi pencereden eski
    later = 1724500000.0 + follow_sync.YEAR_S + 10
    listing = follow_sync.list_follow(_row("team", TEAM, "current"), Client().get_sync, now=later)
    assert api_paths(fake) == ["/team/42/events/next/0", "/team/42/events/last/0"]
    assert [e.id for e in listing.events] == [9100004, 9300001]
    fake.reset_log()
    only = follow_sync.list_follow(_row("team", TEAM, (52186,)), Client().get_sync, now=NOW)
    assert [e.id for e in only.events] == [9100010]


def test_a_player_list_reads_the_last_pages_only(fake: FakeSofaScore) -> None:
    listing = follow_sync.list_follow(_row("player", PLAYER), Client().get_sync, now=NOW)
    assert api_paths(fake) == ["/player/7/events/last/0"]
    assert [e.id for e in listing.events] == [9100002, 9100003]
    with pytest.raises(ValueError):
        follow_sync.list_follow(_row("event", 1), Client().get_sync)


def test_the_page_limit_holds() -> None:
    asked: List[str] = []

    def get(path: str) -> Outcome:
        asked.append(path)
        number = int(path.rsplit("/", 1)[1])
        return Outcome("ok", data={"events": [{"id": 1000 + number, "status": {"type": "finished", "code": 100},
                                               "startTimestamp": 1}], "hasNextPage": True})

    listing = follow_sync.list_follow(_row("player", PLAYER), get, now=NOW)
    assert len(asked) == follow_sync.MAX_LAST_PAGES and len(listing.events) == follow_sync.MAX_LAST_PAGES


def test_no_upcoming_page_and_a_failed_page(fake: FakeSofaScore) -> None:
    fake.remove(endpoints.team_events_page(TEAM, "next", 0))  # 404: gelecek maç yok
    fake.fail("/team/42/events/last/1", 403)
    listing = follow_sync.list_follow(_row("team", TEAM), Client().get_sync, now=NOW)
    assert [e.id for e in listing.events] == [9300001, 9100001]
    assert listing.failed == "403"


# --- ihtiyaç -------------------------------------------------------------------------------------------------


def test_what_each_match_of_a_follow_needs(store: Store, fake: FakeSofaScore) -> None:
    from src.services.pipeline import FetchPipeline

    policy = RefreshPolicy(now=NOW, window_s=0, min_interval_s=0)
    finished = follow_sync.ListedEvent(9100001, "completed")
    upcoming = follow_sync.ListedEvent(9100004, "not_started")
    assert follow_sync.follow_need(None, finished, None, policy, threshold=2) == "full"
    assert follow_sync.follow_need(None, upcoming, None, policy, threshold=2) == follow_sync.EVENT_ONLY
    assert follow_sync.follow_need(None, None, None, policy, threshold=2) == "full"  # maç takibi
    FetchPipeline(store, concurrency=1, selection=None).run_sync(
        [planning.WorkItem(Ref.event(9100004), "refill", (), None, "test")])
    (state,) = list(store.events.states(Scope(event_ids=(9100004,))))
    assert follow_sync.follow_need(state, upcoming, None, policy, threshold=2) == "none"
    # Liste bitti diyor, kayıt başlamadı diyor: yeniden okunur
    assert follow_sync.follow_need(state, follow_sync.ListedEvent(9100004, "completed"), None, policy,
                                   threshold=2) == "full"
    # Maç takibi: başlaması gereken maç yeniden okunur, gelecekteki okunmaz
    assert follow_sync.follow_need(state, None, None, RefreshPolicy(now=1900000001.0, window_s=0, min_interval_s=0),
                                   threshold=2) == "full"
    assert follow_sync.follow_need(state, None, None, policy, threshold=2) == "none"


# --- eşitleme ------------------------------------------------------------------------------------------------


def test_a_team_follow_downloads_its_matches(store: Store, fake: FakeSofaScore) -> None:
    follow(store, "team", TEAM, "Team 42", sport="football")
    follow(store, "tournament", 4242, "Elsewhere")  # adı verilmedi: indirilmez
    result, handle = run(store, FollowsSyncSpec(mode="full", follows=("team:42",)))

    assert result.state == "succeeded" and result.failed_listings == ()
    stored = {row.id: row for row in store.events.iter(EventQuery())}
    assert set(stored) == {9100001, 9100004, 9100010, 9300001}
    assert all(row.has_event_payload for row in stored.values())
    # Bitmiş maçlar dilimleriyle; başlamamış maç yalnızca /event; oynanan maç (bilinmeyen, listede bitmemiş) de
    assert slice_paths(fake, 9100001) and slice_paths(fake, 9100010)
    assert slice_paths(fake, 9100004) == [] and slice_paths(fake, 9300001) == []
    assert "sync_follow_listing" in handle.codes() and "sync_follow_details" in handle.codes()
    assert handle.progress.result()["details_total"] == 4 and handle.progress.result()["details_done"] == 4
    assert not any(path.startswith("/unique-tournament/4242") for path in api_paths(fake))

    # İkinci eşitleme: listeler yeniden okunur; tamam olan maç istenmez
    fake.reset_log()
    run(store, FollowsSyncSpec(mode="full", follows=("team:42",)))
    assert [p for p in api_paths(fake) if not p.startswith("/team/")] == []


def test_a_player_follow_downloads_with_its_own_selection(store: Store, fake: FakeSofaScore) -> None:
    follow(store, "player", PLAYER, "A player", slices={"include": ["statistics"]})
    run(store, FollowsSyncSpec(mode="full", follows=("player:7",)))
    assert slice_paths(fake, 9100002) == ["/event/9100002/statistics"]
    assert slice_paths(fake, 9100003) == ["/event/9100003/statistics"]


def test_a_narrower_follow_wins_over_the_player(store: Store, fake: FakeSofaScore) -> None:
    follow(store, "player", PLAYER, "A player", slices={"include": ["statistics"]})
    follow(store, "event", 9100003, "A match", slices={"include": ["lineups"]}, enabled=False)
    follow(store, "team", 44, "Team 44", slices={"include": ["incidents"]}, enabled=False)
    run(store, FollowsSyncSpec(mode="full", follows=("player:7",)))
    # 9100002: 44 ev sahibi takımın (kapalı takip seçim vermez: yalnızca etkin satırlar politikadadır)
    assert slice_paths(fake, 9100002) == ["/event/9100002/statistics"]
    store.follows.update("team", 44, enabled=True)
    store.events.delete(9100002)
    fake.reset_log()
    run(store, FollowsSyncSpec(mode="full", follows=("player:7",)))
    assert slice_paths(fake, 9100002) == ["/event/9100002/incidents"]


def test_an_event_follow_downloads_that_match(store: Store, fake: FakeSofaScore) -> None:
    follow(store, "event", 9200001, "A tennis match")
    result, handle = run(store, FollowsSyncSpec(mode="full", follows=("event:9200001",)))
    assert result.state == "succeeded"
    assert api_paths(fake)[0] == "/event/9200001" and slice_paths(fake, 9200001)
    assert store.events.get(9200001).has_event_payload


def test_a_sync_without_a_target_takes_every_enabled_follow(store: Store, fake: FakeSofaScore) -> None:
    follow(store, "team", TEAM, "Team 42")
    follow(store, "player", PLAYER, "A player", enabled=False)
    follow(store, "event", 9200001, "A tennis match")
    run(store, SyncSpec(mode="full"))
    paths = api_paths(fake)
    assert "/team/42/events/next/0" in paths and "/event/9200001" in paths
    assert not any(path.startswith("/player/") for path in paths)
    # Tek lig ya da sezon listeleri: takım, oyuncu ve maç takipleri yok
    fake.reset_log()
    run(store, SyncSpec(mode="full", league_id=17))
    run(store, SyncSpec(mode="seasons"))
    run(store, SyncSpec(mode="details"))
    assert not any(path.startswith(("/team/", "/event/9200001")) for path in api_paths(fake))


def test_an_unknown_or_disabled_named_follow_is_skipped(store: Store, fake: FakeSofaScore) -> None:
    follow(store, "player", PLAYER, "A player", enabled=False)
    result, handle = run(store, FollowsSyncSpec(mode="full", follows=("player:7", "team:5")))
    assert result.state == "succeeded" and api_paths(fake) == []
    skipped = [line["params"]["follow"] for line in handle.lines if line.get("code") == "sync_follow_skipped"]
    assert skipped == ["player:7", "team:5"]


def test_a_failed_list_makes_the_job_partial(store: Store, fake: FakeSofaScore) -> None:
    follow(store, "team", TEAM, "Team 42")
    fake.fail("/team/42/events/last/0", 403)
    result, handle = run(store, FollowsSyncSpec(mode="full", follows=("team:42",)))
    assert result.state == "partial"
    assert [(f.kind, f.league_id, f.reason) for f in result.failed_listings] == [("team_events", TEAM, "403")]
    assert "sync_follow_listing_failed" in handle.codes()
    assert store.events.get(9100004) is not None  # gelecek sayfası okundu


def test_a_cancelled_sync_stops(store: Store, fake: FakeSofaScore) -> None:
    follow(store, "team", TEAM, "Team 42")
    follow(store, "player", PLAYER, "A player")
    result, _handle = run(store, FollowsSyncSpec(mode="full", follows=("team:42", "player:7")),
                          cancel_after=3)
    assert result.state == "cancelled"
    assert not any(path.startswith("/event/") for path in api_paths(fake))


def test_the_breaker_stops_the_follows(store: Store, fake: FakeSofaScore, monkeypatch: pytest.MonkeyPatch) -> None:
    follow(store, "team", TEAM, "Team 42")
    fake.fail("/event/*", 429)
    monkeypatch.setattr(FakeConfig, "get_rate_limit_threshold_consecutive", lambda self: 2)
    result, handle = run(store, FollowsSyncSpec(mode="full", follows=("team:42",)))
    assert result.state == "partial" and result.breaker == "429"
    assert "sync_breaker_stopped" in handle.codes()


# --- API: uçtan uca ------------------------------------------------------------------------------------------


@pytest.fixture
def jobs(store: Store, monkeypatch: pytest.MonkeyPatch) -> Iterator[JobStore]:
    before = conftest.job_threads()
    job_store = JobStore(default_db_path(str(store.data_dir)))
    monkeypatch.setattr(deps, "job_store", lambda: job_store)
    yield job_store
    conftest.join_job_threads(before)
    job_store.close()


def test_a_team_follow_added_through_the_api_is_downloaded_by_a_sync_job(store: Store, fake: FakeSofaScore,
                                                                         jobs: JobStore) -> None:
    created = client.post("/api/v1/follows", json={"kind": "team", "entity_id": TEAM, "name": "Team 42",
                                                   "sport": "football"})
    assert created.status_code == 201, created.text
    started = client.post("/api/v1/jobs", json={"kind": "sync", "spec": {"follows": ["team:42"]}})
    assert started.status_code == 202, started.text
    job_id = started.json()["data"]["id"]
    deadline = time.monotonic() + 30
    while True:
        job = client.get(f"/api/v1/jobs/{job_id}").json()["data"]
        if job["finished_at"]:
            break
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert job["state"] == "succeeded", job
    assert job["result"]["details_done"] == 4
    assert {row.id for row in store.events.iter(EventQuery())} == {9100001, 9100004, 9100010, 9300001}
    target = client.get("/api/v1/jobs", params={"target": "team:42"}).json()["data"]
    assert [j["id"] for j in target] == [job_id]


# --- komut satırı --------------------------------------------------------------------------------------------


def test_ssc_sync_follow_and_its_dry_run(store: Store, fake: FakeSofaScore, cli: Any) -> None:
    follow(store, "team", TEAM, "Team 42")
    follow(store, "event", 9200001, "A tennis match")
    data_dir = str(store.data_dir)
    planned = cli("--data-dir", data_dir, "sync", "--follow", "team:42", "--follow", "event:9200001", "--dry-run",
                  "--json")
    assert planned.exit_code == 0, planned.stderr
    assert planned.data["follows"] == {"team": 1, "player": 0, "event": 1}
    assert planned.data["events"]["full"] == 1 and planned.data["tournaments"] == []
    assert api_paths(fake) == []
    done = cli("--data-dir", data_dir, "sync", "--follow", "team:42", "--json")
    assert done.exit_code == 0, done.stderr
    assert store.events.get(9100001) is not None and store.events.get(9200001) is None
    refused = cli("--data-dir", data_dir, "sync", "--follow", "team:42", "--tournament", "17", "--json")
    assert (refused.exit_code, refused.error["code"]) == (2, "invalid_request")
    assert cli("--data-dir", data_dir, "sync", "--follow", "club:1", "--json").exit_code == 2


def test_a_player_follow_selection_is_the_last_link_of_the_chain() -> None:
    policy = planning.SelectionPolicy(follows={("player", 7): {"include": ["statistics"]},
                                               ("team", 44): {"include": ["incidents"]}})
    assert policy.with_follow_events("player", 8, [1]) is policy  # seçim vermeyen takip
    chained = policy.with_follow_events("player", 7, [1, 2])
    assert chained.follow_for(event_id=1) == {"include": ["statistics"]}
    assert chained.follow_for(event_id=2, team_ids=(44,)) == {"include": ["incidents"]}
    assert chained.follow_for(event_id=3) is None and policy.follow_for(event_id=1) is None
