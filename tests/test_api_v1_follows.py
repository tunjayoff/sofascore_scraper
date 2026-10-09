"""
API v1: takipler ve turnuva araması (plan maddesi P21; docs/design/02-services.md 2.7, 4.3 ve 6;
docs/design/01-storage.md 2.3 "Follows"). Tümü çevrimdışı: SofaScore'un araması sahtedir.

  * liste, tek takip, ekleme, değiştirme, kaldırma; kimlik `<kind>:<id>`;
  * kaynak kuralları: leagues.txt'in takibi (yapılandırma dosyası yok) dosyaya yazılır ve yalnızca ad ve spor
    tutar; yapılandırma dosyasının takibi değiştirilemez (409 `follow_managed`); yapılandırma dosyası varken yeni
    turnuva takibi ve her zaman takım, oyuncu, maç takipleri `api` satırıdır;
  * yinelenen takip 409 `follow_exists`; iş sürerken kaldırma 409;
  * yapılandırma dosyasının devraldığı `api` satırı dosyadan çıkınca geri gelmez (ST-17, karar P21);
  * `POST /tournaments/search`: tek istek, istemcinin API köküyle; sonuçların biçimi; engelleme ve ağ hatası.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List

import pytest
from fastapi.testclient import TestClient

from sofascore_scraper.config_manager import ConfigManager
from sofascore_scraper.exceptions import APIError, NetworkError, RateLimitError
from sofascore_scraper.store import FollowSpec, JobRunningError, Store, apply_follows, open_store
from sofascore_scraper.web import deps
from sofascore_scraper.web.app import app

client = TestClient(app)


@pytest.fixture
def leagues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """
    Sürecin ConfigManager'ı geçici bir lig dosyasına bakar (Premier League, sporu kayıtlı) ve veri dizini boş bir
    depodur; ayna o depoya yazar. Test bitince yönetici kendi dosyasına döner.
    """
    config = tmp_path / "config"
    config.mkdir()
    (config / "leagues.txt").write_text("# leagues\nPremier League: 17\n", encoding="utf-8")
    (config / "league_sports.json").write_text(json.dumps({"17": "football"}), encoding="utf-8")
    data = tmp_path / "data"
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(data))
    open_store(data)
    manager = ConfigManager()
    monkeypatch.setattr(manager, "league_config_path", str(config / "leagues.txt"))
    monkeypatch.setattr(manager, "_leagues_mtime", None)
    manager.mirror_follows()
    yield config / "leagues.txt"
    monkeypatch.undo()
    manager._leagues_mtime = None
    manager.get_leagues()


@pytest.fixture
def store(leagues: Path) -> Store:
    return open_store(leagues.parent.parent / "data")


@pytest.fixture
def config_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bir yapılandırma dosyası etkinmiş gibi: yeni turnuva takipleri `api` satırı olur."""
    real = deps.loaded_settings

    def with_file() -> Any:
        loaded = real()
        return SimpleNamespace(settings=loaded.settings, config_file="/etc/sofascore.toml")

    monkeypatch.setattr(deps, "loaded_settings", with_file)


def data(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


def error(response: Any, status: int, code: str) -> Dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code, body
    return body


def file_lines(path: Path) -> List[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]


def sidecar(path: Path) -> Dict[str, str]:
    return json.loads((path.parent / "league_sports.json").read_text(encoding="utf-8"))


# --- okuma -------------------------------------------------------------------------------------------


def test_the_follows_of_the_league_file_are_listed(leagues: Path) -> None:
    body = client.get("/api/v1/follows").json()
    assert body["page"] == {"limit": 1, "next_cursor": None}
    follow = body["data"][0]
    assert {k: follow[k] for k in ("id", "kind", "entity_id", "name", "sport", "seasons", "slices", "live",
                                   "enabled", "origin", "writable")} == {
        "id": "tournament:17", "kind": "tournament", "entity_id": 17, "name": "Premier League", "sport": "football",
        "seasons": "all", "slices": None, "live": False, "enabled": True, "origin": "legacy",
        "writable": ["sport", "origin"],
    }
    assert follow["created_at_utc"].endswith("Z")
    assert data(client.get("/api/v1/follows/tournament:17")) == follow
    error(client.get("/api/v1/follows/tournament:18"), 404, "not_found")
    error(client.get("/api/v1/follows/league:17"), 422, "invalid_request")


def test_a_league_file_edited_by_hand_is_seen(leagues: Path) -> None:
    with leagues.open("a", encoding="utf-8") as f:
        f.write("LaLiga: 8\n")
    assert [f["id"] for f in data(client.get("/api/v1/follows"))] == ["tournament:17", "tournament:8"]


def test_list_filters(leagues: Path, store: Store) -> None:
    store.follows.add(FollowSpec(kind="team", entity_id=42, name="Arsenal"), origin="api")
    assert [f["id"] for f in data(client.get("/api/v1/follows", params={"kind": "team"}))] == ["team:42"]
    assert [f["id"] for f in data(client.get("/api/v1/follows", params={"origin": "legacy"}))] == ["tournament:17"]
    assert [f["id"] for f in data(client.get("/api/v1/follows", params={"q": "ARSE"}))] == ["team:42"]
    assert data(client.get("/api/v1/follows", params={"enabled": "false"})) == []


def test_follows_by_sport(leagues: Path, store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """05-web-ui.md G18 (FX-13): kaydedilen spor, yoksa turnuvanın katalogdaki sporu; bilinmeyen spor 400."""
    store.follows.add(FollowSpec(kind="team", entity_id=42, name="Lakers", sport="basketball"), origin="api")
    store.follows.add(FollowSpec(kind="tournament", entity_id=8, name="LaLiga"), origin="api")
    store.follows.add(FollowSpec(kind="tournament", entity_id=132, name="NBA"), origin="api")
    monkeypatch.setattr(store.entities, "sport_of_tournament", lambda tid: "basketball" if tid == 132 else None)

    def ids(**params: Any) -> List[str]:
        return [f["id"] for f in data(client.get("/api/v1/follows", params=params))]

    assert ids(sport="basketball") == ["team:42", "tournament:132"]
    assert ids(sport="football") == ["tournament:17"]
    assert ids(sport="basketball", kind="team") == ["team:42"]
    assert ids(sport="tennis") == []
    error(client.get("/api/v1/follows", params={"sport": "curling-on-mars"}), 400, "invalid_request")


def test_the_sport_falls_back_to_what_the_catalog_knows(leagues: Path, monkeypatch: pytest.MonkeyPatch,
                                                        store: Store) -> None:
    (leagues.parent / "league_sports.json").write_text("{}", encoding="utf-8")
    ConfigManager().mirror_follows()
    monkeypatch.setattr(store.entities, "sport_of_tournament", lambda tid: "football" if tid == 17 else None)
    assert data(client.get("/api/v1/follows/tournament:17"))["sport"] == "football"


# --- ekleme ------------------------------------------------------------------------------------------


def test_a_tournament_follow_is_kept_in_the_follows_table_without_a_config_file(leagues: Path) -> None:
    """FX-19: leagues.txt'e yazılmaz (sonra kilitli görünürdü); her alanı yazılabilir bir `api` satırıdır."""
    r = client.post("/api/v1/follows", json={"entity_id": 8, "name": "LaLiga", "sport": "Football"})
    follow = data(r, 201)
    assert r.headers["location"] == "/api/v1/follows/tournament:8"
    assert (follow["id"], follow["origin"], follow["sport"], follow["writable"]) == (
        "tournament:8", "api", "football", ["name", "sport", "seasons", "slices", "live", "enabled"])
    assert file_lines(leagues) == ["Premier League: 17"]
    assert sidecar(leagues) == {"17": "football"}


def test_a_web_follow_takes_and_changes_every_field_without_a_config_file(leagues: Path) -> None:
    follow = data(client.post("/api/v1/follows", json={"entity_id": 8, "name": "LaLiga", "live": True,
                                                       "seasons": "current"}), 201)
    assert (follow["origin"], follow["live"], follow["seasons"]) == ("api", True, "current")
    changed = data(client.patch("/api/v1/follows/tournament:8", json={"enabled": False, "seasons": [61643]}))
    assert (changed["enabled"], changed["seasons"]) == (False, [61643])
    assert file_lines(leagues) == ["Premier League: 17"]


def test_a_followed_entity_or_name_is_a_conflict(leagues: Path) -> None:
    error(client.post("/api/v1/follows", json={"entity_id": 17, "name": "Another"}), 409, "follow_exists")
    error(client.post("/api/v1/follows", json={"entity_id": 99, "name": "Premier League"}), 409, "follow_exists")
    assert file_lines(leagues) == ["Premier League: 17"]


@pytest.mark.parametrize("body,status", [
    ({"entity_id": 8, "name": "La:Liga"}, 400),
    ({"entity_id": 8, "name": "../x"}, 400),
    ({"entity_id": 8, "name": "..."}, 400),
    ({"entity_id": 8, "name": "LaLiga", "sport": "curling"}, 400),
    ({"entity_id": 0, "name": "LaLiga"}, 422),
    ({"entity_id": 8, "name": "LaLiga", "kind": "league"}, 422),
    ({"entity_id": 8, "name": "LaLiga", "extra": 1}, 422),
    ({"name": "LaLiga"}, 422),
])
def test_invalid_new_follows_are_refused(leagues: Path, body: Dict[str, Any], status: int) -> None:
    error(client.post("/api/v1/follows", json=body), status, "invalid_request")
    assert file_lines(leagues) == ["Premier League: 17"]


def test_teams_players_and_events_are_kept_in_the_follows_table(leagues: Path) -> None:
    team = data(client.post("/api/v1/follows", json={"kind": "team", "entity_id": 42, "name": "Arsenal",
                                                     "sport": "football", "live": True, "seasons": "last:2"}), 201)
    assert (team["origin"], team["live"], team["seasons"]) == ("api", True, "last:2")
    assert team["writable"] == ["name", "sport", "seasons", "slices", "live", "enabled"]
    event = data(client.post("/api/v1/follows", json={"kind": "event", "entity_id": 900, "name": "A v B",
                                                      "seasons": [3, 2, 3]}), 201)
    assert event["seasons"] == [3, 2]
    assert file_lines(leagues) == ["Premier League: 17"]


def test_with_a_config_file_a_new_tournament_follow_is_an_api_row(leagues: Path, config_file: None) -> None:
    follow = data(client.post("/api/v1/follows", json={"entity_id": 8, "name": "LaLiga", "live": True}), 201)
    assert (follow["origin"], follow["live"]) == ("api", True)
    assert file_lines(leagues) == ["Premier League: 17"]


# --- değiştirme ve kaldırma -----------------------------------------------------------------------------


def test_the_sport_of_a_league_file_follow_changes_in_its_sidecar(leagues: Path) -> None:
    assert data(client.patch("/api/v1/follows/tournament:17", json={"sport": "basketball"}))["sport"] == "basketball"
    assert sidecar(leagues) == {"17": "basketball"}
    assert data(client.patch("/api/v1/follows/tournament:17", json={"sport": None}))["sport"] is None
    assert sidecar(leagues) == {}
    body = error(client.patch("/api/v1/follows/tournament:17", json={"live": True}), 400, "invalid_request")
    assert body["details"]["unsupported"] == ["live"]
    # Değişmeyen değer reddedilmez (arayüz formun bütün alanlarını gönderebilir)
    assert data(client.patch("/api/v1/follows/tournament:17", json={"name": "Premier League", "enabled": True}))


def test_an_api_follow_changes_every_field(leagues: Path, store: Store) -> None:
    store.follows.add(FollowSpec(kind="team", entity_id=42, name="Arsenal"), origin="api")
    changed = data(client.patch("/api/v1/follows/team:42", json={
        "name": "Arsenal FC", "sport": "football", "seasons": [1], "live": True, "enabled": False}))
    assert {k: changed[k] for k in ("name", "sport", "seasons", "live", "enabled")} == {
        "name": "Arsenal FC", "sport": "football", "seasons": [1], "live": True, "enabled": False}
    error(client.patch("/api/v1/follows/team:42", json={"seasons": "sometimes"}), 400, "invalid_request")
    error(client.patch("/api/v1/follows/team:42", json={"origin": "config"}), 422, "invalid_request")
    error(client.patch("/api/v1/follows/team:43", json={"live": True}), 404, "not_found")


def test_a_follow_of_the_config_file_is_read_only_here(leagues: Path, store: Store) -> None:
    apply_follows(store.data_dir, [FollowSpec(kind="tournament", entity_id=35, name="Bundesliga")], origin="config")
    follow = data(client.get("/api/v1/follows/tournament:35"))
    assert (follow["origin"], follow["writable"]) == ("config", [])
    error(client.patch("/api/v1/follows/tournament:35", json={"live": True}), 409, "follow_managed")
    error(client.delete("/api/v1/follows/tournament:35"), 409, "follow_managed")


def test_removing_a_league_file_follow_edits_both_files(leagues: Path) -> None:
    with leagues.open("a", encoding="utf-8") as f:
        f.write("LaLiga: 8\n")
    data(client.patch("/api/v1/follows/tournament:8", json={"sport": "football"}))
    assert sidecar(leagues) == {"17": "football", "8": "football"}
    removed = data(client.delete("/api/v1/follows/tournament:8"))
    assert (removed["id"], removed["origin"]) == ("tournament:8", "legacy")
    assert file_lines(leagues) == ["Premier League: 17"] and sidecar(leagues) == {"17": "football"}
    error(client.get("/api/v1/follows/tournament:8"), 404, "not_found")
    error(client.delete("/api/v1/follows/tournament:8"), 404, "not_found")


def test_a_league_file_follow_is_moved_into_the_follows_table(leagues: Path, store: Store) -> None:
    """FX-19: PATCH `origin: "api"` taşır; dosyadan çıkar, her alan yazılır, sonraki ayna geri getirmez."""
    moved = data(client.patch("/api/v1/follows/tournament:17", json={"origin": "api", "live": True,
                                                                      "seasons": "last:2"}))
    assert (moved["origin"], moved["live"], moved["seasons"], moved["sport"]) == ("api", True, "last:2", "football")
    assert moved["writable"] == ["name", "sport", "seasons", "slices", "live", "enabled"]
    assert file_lines(leagues) == [] and sidecar(leagues) == {}
    row = store.follows.get("tournament", 17)
    assert row is not None and (row.origin, row.position, row.sport) == ("api", 0, "football")
    # Elle düzenlenen dosya (başka bir lig) aynayı yeniler; taşınan satır yerinde kalır
    with leagues.open("a", encoding="utf-8") as f:
        f.write("LaLiga: 8\n")
    assert [(f["id"], f["origin"]) for f in data(client.get("/api/v1/follows"))] == [
        ("tournament:17", "api"), ("tournament:8", "legacy")]
    # Kaldırınca geri gelmez
    assert data(client.delete("/api/v1/follows/tournament:17"))["origin"] == "api"
    assert [f["id"] for f in data(client.get("/api/v1/follows"))] == ["tournament:8"]


def test_moving_a_follow_only_into_the_follows_table(leagues: Path, store: Store) -> None:
    store.follows.add(FollowSpec(kind="team", entity_id=42, name="Arsenal"), origin="api")
    assert data(client.patch("/api/v1/follows/team:42", json={"origin": "api"}))["origin"] == "api"
    error(client.patch("/api/v1/follows/tournament:17", json={"origin": "legacy"}), 422, "invalid_request")
    apply_follows(store.data_dir, [FollowSpec(kind="tournament", entity_id=35, name="Bundesliga")], origin="config")
    error(client.patch("/api/v1/follows/tournament:35", json={"origin": "api"}), 409, "follow_managed")
    assert file_lines(leagues) == ["Premier League: 17"]


def test_removing_an_api_follow(leagues: Path, store: Store) -> None:
    store.follows.add(FollowSpec(kind="player", entity_id=7, name="A Player"), origin="api")
    assert data(client.delete("/api/v1/follows/player:7"))["origin"] == "api"
    assert store.follows.get("player", 7) is None


def test_a_follow_is_not_removed_while_a_job_runs(leagues: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Busy:
        def exclusive(self, operation: str) -> Any:
            assert operation == "follow_delete"
            raise JobRunningError()

    monkeypatch.setattr(deps, "job_store", lambda: Busy())
    error(client.delete("/api/v1/follows/tournament:17"), 409, "job_running")
    assert file_lines(leagues) == ["Premier League: 17"]


def test_an_api_row_taken_over_by_the_config_file_does_not_come_back(leagues: Path, store: Store) -> None:
    """ST-17'nin açık bıraktığı durum, karar P21: dosya kazanır; dosyadan çıkan takip API'den yeniden eklenir."""
    store.follows.add(FollowSpec(kind="tournament", entity_id=35, name="Bundesliga", live=True), origin="api")
    apply_follows(store.data_dir, [FollowSpec(kind="tournament", entity_id=35, name="Bundesliga")], origin="config")
    assert store.follows.get("tournament", 35).origin == "config"
    apply_follows(store.data_dir, [], origin="config")
    assert store.follows.get("tournament", 35) is None
    error(client.get("/api/v1/follows/tournament:35"), 404, "not_found")


# --- SofaScore'da turnuva araması ---------------------------------------------------------------------


def _hit(tid: int, name: str, sport: str = "Football", slug: str = "football") -> Dict[str, Any]:
    return {"entity": {"id": tid, "name": name, "slug": name.lower().replace(" ", "-"),
                       "category": {"id": tid * 10, "name": "England", "slug": "england", "alpha2": "EN",
                                    "sport": {"name": sport, "slug": slug}}}}


@pytest.fixture
def search(monkeypatch: pytest.MonkeyPatch) -> List[Any]:
    calls: List[Any] = []
    outcome: List[Any] = [{"uniqueTournaments": [_hit(17, "Premier League"), _hit(132, "NBA", "Basketball",
                                                                                       "basketball")]}]

    def fake(url: str, **kwargs: Any) -> Any:
        calls.append((url, kwargs))
        if isinstance(outcome[0], BaseException):
            raise outcome[0]
        return outcome[0]

    monkeypatch.setattr("sofascore_scraper.client.transport.make_api_request", fake)
    return [calls, outcome]


def test_the_tournament_search_asks_sofascore_once(leagues: Path, search: List[Any]) -> None:
    from sofascore_scraper.client import api_url, endpoints

    hits = data(client.post("/api/v1/tournaments/search", json={"q": "premier"}))
    assert hits[0] == {"kind": "tournament", "id": 17, "name": "Premier League", "slug": "premier-league",
                       "sport": "football",
                       "category": {"id": 170, "name": "England", "slug": "england", "country_code": "EN"},
                       "country": {"code": "EN", "name": None}, "team": None, "followed": True,
                       "gender": None, "national": None}
    assert (hits[1]["sport"], hits[1]["followed"]) == ("basketball", False)
    assert search[0] == [(api_url(endpoints.search_unique_tournaments("premier")),
                          {"max_retries": 1, "timeout": 10, "raise_errors": True})]
    assert [h["id"] for h in data(client.post("/api/v1/tournaments/search", json={"q": "x y", "sport": "basketball"}))] == [132]


def test_the_search_returns_at_most_twenty_hits(leagues: Path, search: List[Any]) -> None:
    search[1][0] = {"results": [_hit(1000 + i, f"Cup {i}") for i in range(30)] + [{"entity": "junk"}, 5]}
    assert len(data(client.post("/api/v1/tournaments/search", json={"q": "cup"}))) == 20


@pytest.mark.parametrize("failure,status,code,reason", [
    (RateLimitError(wait_time=5), 503, "rate_limited", "rate_limited"),
    (APIError("forbidden", status_code=403), 503, "blocked", "blocked"),
    (NetworkError("timeout"), 502, "upstream_error", "network"),
    (APIError("bad gateway", status_code=502), 502, "upstream_error", "upstream"),
    ({"unexpected": True}, 502, "upstream_error", "upstream"),
])
def test_a_failed_search_is_a_typed_error(leagues: Path, search: List[Any], failure: Any, status: int, code: str,
                                          reason: str) -> None:
    search[1][0] = failure
    body = error(client.post("/api/v1/tournaments/search", json={"q": "premier"}), status, code)
    assert body["details"] == {"reason": reason}


def test_the_search_needs_its_text(leagues: Path, search: List[Any]) -> None:
    error(client.post("/api/v1/tournaments/search", json={"q": "p"}), 422, "invalid_request")
    error(client.post("/api/v1/tournaments/search", json={}), 422, "invalid_request")
    error(client.post("/api/v1/tournaments/search", json={"q": "premier", "sport": "curling"}), 400, "invalid_request")
    assert search[0] == []
