"""
SofaScore'da ada göre arama: turnuva, takım ve oyuncu (plan maddesi FX-19; `POST /api/v1/tournaments/search`).

  * yalnızca turnuva (varsayılan): `/search/unique-tournaments/{q}`, FX-13'teki gibi tek istek;
  * takım ya da oyuncu istenince `/search/all?q=...&page=0` (docs/all-sports/endpoints.csv), yine tek istek; sonuçlar
    `kind` ile tiplenir, sporu, ülkesi, oyuncunun takımı ve "zaten takipte" bilgisiyle;
  * 404 "bulunamadı"dır (boş liste), öteki hatalar FX-13'teki gibi tipli hatadır.

Ağ yok: istekler tests/fakes/sofascore.py'nin sahte taşıyıcısına gider. `/search/all`'ın yanıtı
tests/fixtures/fx19/search_all.json'dır (research/all_sports/samples/football/search-all__1.json; kısaltılmış
örnekte yalnızca takım ve oyuncu var, turnuva sonucu sentetiktir).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest
from fastapi.testclient import TestClient

from characterization import WORLD, pin_default_settings
from fakes.sofascore import FakeSofaScore
from src.client import endpoints
from src.config import loader
from src.services.follows import ConfigLeagues, FollowsService
from src.store import FollowSpec, Store, open_store
from src.web.app import app

FIXTURES = Path(__file__).parent / "fixtures" / "fx19"
client = TestClient(app)


def fixture(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# `/search/all`'ın turnuva sonucu: örnekte yok, turnuva aramasının varlık biçimiyle (deneysel)
TOURNAMENT_RESULT = {
    "entity": {"id": 52, "name": "Trendyol Süper Lig", "slug": "trendyol-super-lig",
               "category": {"id": 46, "name": "Türkiye", "slug": "turkey", "alpha2": "TR",
                            "sport": {"id": 1, "slug": "football", "name": "Football"}}},
    "score": 1000.0, "type": "uniqueTournament",
}


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    pin_default_settings(monkeypatch)
    loader.reset()
    yield
    monkeypatch.undo()
    loader.reset()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return open_store(tmp_path / "data")


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        body = copy.deepcopy(fixture("search_all")["body"])
        body["results"].append(TOURNAMENT_RESULT)
        world.add(endpoints.search_all("galatasaray"), body)
        yield world


def data(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


def api_paths(fake: FakeSofaScore) -> List[str]:
    return [path for path in fake.paths() if path.startswith("/")]


def test_the_search_path_is_the_general_search_of_the_catalog() -> None:
    assert endpoints.search_all("galatasaray") == fixture("search_all")["path"]
    assert endpoints.search_all("süper lig", 2) == "/search/all?q=s%C3%BCper%20lig&page=2"
    assert endpoints.search_all("a&b=c") == "/search/all?q=a%26b%3Dc&page=0"
    with pytest.raises(ValueError):
        endpoints.search_all("x", -1)


def test_teams_and_players_are_found_by_name_with_one_request(store: Store, fake: FakeSofaScore) -> None:
    store.follows.add(FollowSpec(kind="player", entity_id=822471, name="Victor Osimhen"))
    hits = data(client.post("/api/v1/tournaments/search", json={"q": "galatasaray", "kinds": ["team", "player"]}))
    assert api_paths(fake) == ["/search/all?q=galatasaray&page=0"]
    assert [(h["kind"], h["id"], h["name"]) for h in hits] == [
        ("team", 3061, "Galatasaray"), ("player", 822471, "Victor Osimhen"), ("player", 851284, "Rafael Leão")]
    team, player = hits[0], hits[1]
    assert (team["sport"], team["country"], team["team"], team["followed"]) == (
        "football", {"code": "TR", "name": "Türkiye"}, None, False)
    assert team["category"] == {"id": None, "name": None, "slug": None, "country_code": "TR"}
    assert (player["sport"], player["country"], player["team"], player["followed"]) == (
        "football", {"code": "NG", "name": "Nigeria"}, {"id": 3061, "name": "Galatasaray"}, True)


def test_every_kind_together_and_by_sport(store: Store, fake: FakeSofaScore) -> None:
    body = {"q": "galatasaray", "kinds": ["tournament", "team", "player"]}
    hits = data(client.post("/api/v1/tournaments/search", json=body))
    assert [h["kind"] for h in hits] == ["team", "player", "player", "tournament"]
    tournament = hits[-1]
    assert (tournament["id"], tournament["sport"], tournament["category"]["name"], tournament["country"]) == (
        52, "football", "Türkiye", {"code": "TR", "name": None})
    assert data(client.post("/api/v1/tournaments/search", json={**body, "sport": "basketball"})) == []
    assert data(client.post("/api/v1/tournaments/search", json={**body, "kinds": ["team"]}))[0]["id"] == 3061
    # tek istek: aynı metnin genel araması sunucuda saklanır (FX-20); süzgeç ve türler yanıttan seçilir
    assert api_paths(fake) == ["/search/all?q=galatasaray&page=0"]


def test_tournaments_alone_keep_the_tournament_search(store: Store, fake: FakeSofaScore) -> None:
    fake.add(endpoints.search_unique_tournaments("premier"), {"uniqueTournaments": [
        {"id": 17, "name": "Premier League", "slug": "premier-league",
         "category": {"id": 1, "name": "England", "slug": "england", "alpha2": "EN",
                      "sport": {"name": "Football", "slug": "football"}}}]})
    hits = data(client.post("/api/v1/tournaments/search", json={"q": "premier"}))
    assert [(h["kind"], h["id"]) for h in hits] == [("tournament", 17)]
    assert api_paths(fake) == ["/search/unique-tournaments/premier"]


def test_not_found_is_an_empty_list(store: Store, fake: FakeSofaScore) -> None:
    assert data(client.post("/api/v1/tournaments/search", json={"q": "nothing here"})) == []
    assert data(client.post("/api/v1/tournaments/search", json={"q": "nothing here", "kinds": ["player"]})) == []


def test_a_refused_search_is_a_typed_error(store: Store, fake: FakeSofaScore) -> None:
    fake.fail("/search/all*", 403)
    response = client.post("/api/v1/tournaments/search", json={"q": "galatasaray", "kinds": ["team"]})
    assert response.status_code == 503 and response.json()["error"]["code"] == "blocked"


@pytest.mark.parametrize("body", [
    {"q": "galatasaray", "kinds": []},
    {"q": "galatasaray", "kinds": ["event"]},
    {"q": "galatasaray", "kinds": ["team", "team", "team", "team"]},
])
def test_invalid_kinds_are_refused(store: Store, fake: FakeSofaScore, body: Dict[str, Any]) -> None:
    response = client.post("/api/v1/tournaments/search", json=body)
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_request"
    assert api_paths(fake) == []


def test_the_service_names_unknown_kinds(store: Store) -> None:
    from src.errors import UsageError

    service = FollowsService(store, ConfigLeagues())
    with pytest.raises(UsageError) as found:
        service.search("galatasaray", kinds=("manager",))
    assert found.value.details == {"field": "kinds", "kinds": ["manager"]}
