"""
Maç dışı dilimler (plan maddesi P28; docs/design/02-services.md 3.1 ve 3.2): sahibi sezon, takım, oyuncu ya da
spor olan dilimler varsayılan olarak kapalıdır; seçilince planlayıcı maç başına değil sahip başına bir iş birimi
üretir, boru hattı sahibin dilimlerini tek bir `store.entities.put` ile yazar, `max_age`'den eski dilim yeniden
okunur (bitmiş sezonda okunmaz).

Ağ yok: istekler tests/fakes/sofascore.py'nin sahte taşıyıcısına gider (G-01'in dünyası). Yanıt gövdeleri
tests/fixtures/p28/ altında, research/all_sports örneklerinden.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator, List

import pytest
from fastapi.testclient import TestClient

from characterization import WORLD, pin_default_settings
from fakes.sofascore import FakeSofaScore
from sofascore_scraper import sports
from sofascore_scraper.client import endpoints
from sofascore_scraper.config import loader
from sofascore_scraper.schema import mappers
from sofascore_scraper.services import planning
from sofascore_scraper.services.export import ExportService
from sofascore_scraper.services.pipeline import FetchPipeline, run_extras
from sofascore_scraper.services.query import RefreshPolicy
from sofascore_scraper.store import FollowSpec, Ref, Store, open_store
from sofascore_scraper.web.app import app

FIXTURES = Path(__file__).parent / "fixtures" / "p28"
LEAGUE, SEASON = 17, 61627  # dört maçı var, biri başlamamış: etkin sezon
OLD_SEASON = 52186  # tek maçı 2024'te bitti: biten sezon
TENNIS_LEAGUE, TENNIS_SEASON = 2361, 59346  # maçı 9200001: 275923 - 206570
SEASON_EVENTS = [9100001, 9100002, 9100003, 9100004]
OWNER_KEYS = ("standings", "season_info", "cuptrees", "top_players", "top_teams", "season_odds", "team_rankings",
              "player_statistics", "rankings")

client = TestClient(app)


def body(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["body"]


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    pin_default_settings(monkeypatch)
    loader.reset()
    yield
    monkeypatch.undo()
    loader.reset()


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        yield world


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return open_store(tmp_path / "data")


def download(store: Store, ids: List[int]) -> None:
    items = planning.plan_items(store, ids, RefreshPolicy.current(), selection=None)
    FetchPipeline(store, concurrency=1, selection=None).run_sync(items)


def api_paths(fake: FakeSofaScore) -> List[str]:
    return [path for path in fake.paths() if path.startswith("/")]


def policy_at(now: float) -> RefreshPolicy:
    return RefreshPolicy(now=now, window_s=72 * 3600, min_interval_s=3600)


def season_routes(fake: FakeSofaScore, season: int = SEASON) -> None:
    fake.add(f"/unique-tournament/{LEAGUE}/season/{season}/standings/total", body("standings_total"))
    fake.add(f"/unique-tournament/{LEAGUE}/season/{season}/info", body("season_info"))


STANDINGS = planning.SelectionPolicy(defaults=("core", "standings", "season"))


# --- kayıt defteri ----------------------------------------------------------------------------------------


def test_owner_slices_are_registered_off_by_default() -> None:
    owned = [spec for spec in sports.registered_slices() if spec.owner != "event"]
    assert [spec.key for spec in owned] == list(OWNER_KEYS)
    assert {spec.owner for spec in owned} == {"season", "team", "player", "sport"}
    for spec in owned:
        assert spec.default_enabled is False and spec.required is False and spec.max_age is not None
        assert spec.group != "core"
    # Yeni grup adları var olan bir dilim anahtarıyla çakışmaz (o anahtarı yazan seçim yeni dilimleri seçmesin)
    detail_keys = {spec.key for spec in sports.DETAIL_SLICES}
    assert not {"season", "leaders", "players"} & detail_keys
    assert [spec.key for spec in owned if spec.experimental] == ["season_odds", "player_statistics", "rankings"]
    # Varsayılan seçim hiçbir sahipte maç dışı dilim seçmez
    for owner in ("season", "team", "player", "sport"):
        for sport in (*sports.sport_slugs(), None):
            assert sports.select_slices(owner, sport) == ()
            assert sports.select_slices(owner, sport, ["core"]) == ()
    assert [s.key for s in sports.select_slices("season", "football", ["standings"])] == ["standings"]
    assert [s.key for s in sports.select_slices("season", "basketball", ["leaders"])] == []  # yalnızca futbol


def test_owner_paths() -> None:
    assert endpoints.owner_slice("standings", "home", tournament_id=17, season_id=5) == \
        "/unique-tournament/17/season/5/standings/home"
    assert endpoints.owner_slice("season_odds", "1", season_id=5) == "/odds/season/5/provider/1/all"
    assert endpoints.owner_slice("team_rankings", team_id=42) == "/team/42/rankings"
    assert endpoints.owner_slice("player_statistics", player_id=7) == "/player/7/statistics/seasons"
    assert endpoints.owner_slice("rankings", "5") == "/rankings/5"
    with pytest.raises(KeyError):
        endpoints.owner_slice("standings", "total", tournament_id=17)  # sezon eksik
    with pytest.raises(KeyError):
        endpoints.owner_slice("statistics", event_id=1)
    with pytest.raises(ValueError):
        endpoints.owner_slice("standings", tournament_id=17, season_id=5)


# --- varsayılan: hiçbir şey okunmaz ve istenmez ---------------------------------------------------------------


class _Untouchable:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the store was read: {name}")


def test_nothing_is_planned_or_read_by_default() -> None:
    policy = planning.SelectionPolicy()
    assert planning.owner_items(_Untouchable(), policy_at(time.time()), seasons=[(LEAGUE, SEASON)],  # type: ignore[arg-type]
                                selection=policy) == []
    assert planning.prematch_items(_Untouchable(), policy_at(time.time()), tournament_ids=[LEAGUE],  # type: ignore[arg-type]
                                   selection=policy) == []
    assert planning.extras_selected(policy) is False
    assert planning.extras_selected(planning.SelectionPolicy(defaults=("core", "standings"))) is True
    assert planning.extras_selected(planning.SelectionPolicy(sports={"tennis": (("rankings",), ())})) is True
    assert planning.extras_selected(planning.SelectionPolicy(
        follows={("player", 7): {"include": ("player_statistics",)}})) is True


# --- sahip başına bir iş birimi ---------------------------------------------------------------------------------


def test_one_item_per_season_not_per_event(fake: FakeSofaScore, store: Store) -> None:
    download(store, SEASON_EVENTS)
    items = planning.owner_items(store, policy_at(time.time()), seasons=[(LEAGUE, SEASON), (LEAGUE, SEASON)],
                                 selection=STANDINGS)
    assert [(item.owner, item.need, item.sport) for item in items] == [(Ref.season(LEAGUE, SEASON), "owner", "football")]
    assert items[0].slices == (("standings", "total"), ("standings", "home"), ("season_info", ""), ("cuptrees", ""))


def test_the_pipeline_stores_season_data_and_max_age_refetches(fake: FakeSofaScore, store: Store) -> None:
    download(store, SEASON_EVENTS)
    season_routes(fake)
    fake.reset_log()
    now = time.time()
    items = planning.owner_items(store, policy_at(now), seasons=[(LEAGUE, SEASON)], selection=STANDINGS)
    summary = FetchPipeline(store, concurrency=1, selection=STANDINGS).run_sync(items)
    assert summary.ok == 1
    assert sorted(api_paths(fake)) == sorted([
        "/unique-tournament/17/season/61627/standings/total", "/unique-tournament/17/season/61627/standings/home",
        "/unique-tournament/17/season/61627/info", "/unique-tournament/17/season/61627/cuptrees"])
    ref = Ref.season(LEAGUE, SEASON)
    found = {(info.key, info.sub): info for info in store.entities.slices(ref)}
    assert found[("standings", "total")].state == "ok" and found[("season_info", "")].state == "ok"
    assert found[("standings", "home")].state == "empty" and found[("cuptrees", "")].empty_count == 1
    assert store.entities.payload(ref, "standings", "total") == body("standings_total")

    # Taze: yalnızca bir kez 404 alanlar (eşik iki); ikinci 404'ten sonra hiçbiri
    [again] = planning.owner_items(store, policy_at(now + 60), seasons=[(LEAGUE, SEASON)], selection=STANDINGS)
    assert again.slices == (("standings", "home"), ("cuptrees", ""))
    FetchPipeline(store, concurrency=1, selection=STANDINGS).run_sync([again])
    assert planning.owner_items(store, policy_at(now + 60), seasons=[(LEAGUE, SEASON)], selection=STANDINGS) == []
    # Etkin sezonda max_age dolunca (puan durumu 6 sa, sezon bilgisi 7 gün)
    [stale] = planning.owner_items(store, policy_at(now + 7 * 3600), seasons=[(LEAGUE, SEASON)], selection=STANDINGS)
    assert stale.slices == (("standings", "total"),)
    [week] = planning.owner_items(store, policy_at(now + 8 * 86400), seasons=[(LEAGUE, SEASON)], selection=STANDINGS)
    assert week.slices == (("standings", "total"), ("season_info", ""))


def test_a_finished_season_is_read_once(fake: FakeSofaScore, store: Store) -> None:
    download(store, [9100010])
    season_routes(fake, OLD_SEASON)
    now = time.time()
    items = planning.owner_items(store, policy_at(now), seasons=[(LEAGUE, OLD_SEASON)], selection=STANDINGS)
    FetchPipeline(store, concurrency=1, selection=STANDINGS).run_sync(items)
    later = planning.owner_items(store, policy_at(now + 30 * 86400), seasons=[(LEAGUE, OLD_SEASON)],
                                 selection=STANDINGS)
    assert [item.slices for item in later] == [(("standings", "home"), ("cuptrees", ""))]  # yalnızca ikinci 404


def test_a_tournament_follow_selects_its_seasons_data() -> None:
    policy = planning.SelectionPolicy(follows={("tournament", LEAGUE): {"include": ("standings",)}})
    assert [s.key for s in sports.select_slices("season", "football", policy.for_owner("season", SEASON, "football",
                                                                                      tournament_id=LEAGUE))] == [
        "standings"]
    assert sports.select_slices("season", "football", policy.for_owner("season", 1, "football", tournament_id=8)) == ()


def test_teams_players_and_sports_are_owners_of_their_own(fake: FakeSofaScore, store: Store) -> None:
    download(store, [9200001])
    store.follows.add(FollowSpec(kind="player", entity_id=823984, name="A player", sport="football"))
    fake.add("/team/275923/rankings", body("team_rankings"))
    policy = planning.SelectionPolicy(defaults=("core", "rankings", "players"))
    items = planning.owner_items(store, policy_at(time.time()), seasons=[(TENNIS_LEAGUE, TENNIS_SEASON)],
                                 selection=policy)
    owners = [(item.owner.kind, item.owner.id, item.slices) for item in items]
    sport = store.entities.sport("tennis")
    assert sport is not None and sport.id == 5
    assert owners == [
        ("team", 206570, (("team_rankings", ""),)),
        ("team", 275923, (("team_rankings", ""),)),
        ("player", 823984, (("player_statistics", ""),)),
        ("sport", 5, (("rankings", "5"),)),
    ]
    fake.reset_log()
    summary = FetchPipeline(store, concurrency=1, selection=policy).run_sync(items)
    assert sorted(api_paths(fake)) == ["/player/823984/statistics/seasons", "/rankings/5", "/team/206570/rankings",
                                      "/team/275923/rankings"]
    assert summary.total == 4
    assert store.entities.slice(Ref.team(275923), "team_rankings").state == "ok"
    assert store.entities.slice(Ref.team(206570), "team_rankings").state == "empty"


def test_run_extras_covers_the_sync_scope(fake: FakeSofaScore, store: Store) -> None:
    download(store, SEASON_EVENTS)
    season_routes(fake)
    assert run_extras(store, seasons=[(LEAGUE, SEASON)], tournament_ids=[LEAGUE],
                      selection=planning.SelectionPolicy()) is None
    fake.reset_log()
    summary = run_extras(store, seasons=[(LEAGUE, SEASON)], tournament_ids=[LEAGUE], selection=STANDINGS)
    assert summary is not None and summary.total == 1 and summary.ok == 1
    assert len(api_paths(fake)) == 4


# --- şema, API ve dışa aktarma ------------------------------------------------------------------------------


def test_standings_map_to_rows() -> None:
    rows = mappers.standings_rows(LEAGUE, SEASON, "total", body("standings_total"), fetched_at=1_790_000_000)
    # Örneğin son satırı araştırma aracının kısaltma işaretidir ({"__trimmed__": n}): tablo satırı değildir
    assert len(rows) == len(body("standings_total")["standings"][0]["rows"]) - 1 == 3
    first = rows[0].to_dict()
    assert {k: first[k] for k in ("table", "group_name", "position", "participant_id", "participant_name", "matches",
                                  "wins", "draws", "losses", "scores_for", "scores_against", "points")} == {
        "table": "total", "group_name": "Premier League 26/27", "position": 1, "participant_id": 17,
        "participant_name": "Manchester City", "matches": 5, "wins": 5, "draws": 0, "losses": 0, "scores_for": 13,
        "scores_against": 5, "points": 15}
    assert [row.position for row in rows] == sorted(row.position for row in rows)
    odd = mappers.standings_rows(LEAGUE, SEASON, "total", {"standings": ["x", {"rows": ["y", {"team": 5},
                                                                                     {"team": 5, "position": 2}]}]})
    assert [(row.position, row.participant_id) for row in odd] == [(2, None)]
    assert mappers.standings_rows(LEAGUE, SEASON, "total", None) == []


def test_the_api_lists_season_slices_and_standings(fake: FakeSofaScore, store: Store) -> None:
    download(store, SEASON_EVENTS)
    season_routes(fake)
    items = planning.owner_items(store, policy_at(time.time()), seasons=[(LEAGUE, SEASON)], selection=STANDINGS)
    FetchPipeline(store, concurrency=1, selection=STANDINGS).run_sync(items)

    listed = client.get(f"/api/v1/seasons/{SEASON}/slices").json()["data"]
    keys = [(s["key"], s["sub"]) for s in listed]
    assert ("standings", "total") in keys and ("season_info", None) in keys
    assert all(s["payload"] is None for s in listed)
    table = client.get(f"/api/v1/seasons/{SEASON}/standings").json()["data"]
    assert len(table) == 3
    assert table[0]["participant_name"] == "Manchester City" and table[0]["table"] == "total"
    assert client.get(f"/api/v1/seasons/{SEASON}/standings", params={"table": "home"}).json()["data"] == []
    assert client.get(f"/api/v1/seasons/{SEASON}/standings", params={"table": "away"}).status_code == 422
    assert client.get("/api/v1/seasons/1/standings").status_code == 404
    assert client.get("/api/v1/seasons/1/slices").status_code == 404
    # Var olan tek dilim rotası maç dışı dilimi yüküyle verir
    one = client.get(f"/api/v1/seasons/{SEASON}/slices/standings", params={"sub": "total"}).json()["data"]
    assert one["payload"] == body("standings_total")

    rows = [record.to_dict() for record in ExportService(store).records("standings")]
    assert [row["position"] for row in rows] == [row["position"] for row in table]


def test_a_sync_runs_the_selected_extras_after_the_details(fake: FakeSofaScore, store: Store, tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from sofascore_scraper.config_manager import ConfigManager
    from sofascore_scraper.services.sync import SyncService, SyncSpec
    from test_sync_service import FakeDetails, RecordingHandle

    download(store, SEASON_EVENTS + [9100010])
    season_routes(fake)
    config = ConfigManager()
    monkeypatch.setattr(config, "get_leagues", lambda: {LEAGUE: "Premier League"})
    ctx = SimpleNamespace(config=config, data_dir=str(store.data_dir), store=store, season_fetcher=None,
                          match_fetcher=None, match_data_fetcher=FakeDetails())
    spec = SyncSpec(mode="details", league_id=LEAGUE)

    # Varsayılan yapılandırma: P28 aşaması hiçbir şey istemez
    fake.reset_log()
    SyncService(ctx).run(spec, handle=RecordingHandle(spec))
    assert api_paths(fake) == []

    path = tmp_path / "sofascore.toml"
    path.write_text('[defaults]\nslices = ["core", "standings"]\n', encoding="utf-8")
    monkeypatch.setenv(loader.CONFIG_ENV, str(path))
    loader.reset()
    handle = RecordingHandle(spec)
    result = SyncService(ctx).run(spec, handle=handle)
    # Lig sezonsuz verildi: en yeni sezonu
    assert sorted(api_paths(fake)) == ["/unique-tournament/17/season/61627/standings/home",
                                      "/unique-tournament/17/season/61627/standings/total"]
    assert "Odds and non-match data: 1 stored, 0 failed." in handle.lines
    assert result.state == "succeeded"
