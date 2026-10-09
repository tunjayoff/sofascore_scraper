"""
Takımın kaydı (B1, e2e F5 / F26 / F32): aynı adlı erkek ve kadın takımları ya da kulüp ve milli takım ayırt
edilebilsin diye

  * `GET /api/v1/teams/{team_id}` yarışmacının katalog kaydını (şema v1 Participant: cinsiyet, milli takım,
    ülke kodu, sporu) ve bir takım takibinin onu adlandırıp adlandırmadığını verir; katalogda yoksa 404;
  * `GET /api/v1/catalog/suggest`'in takım önerileri katalogdaki cinsiyeti ve milli takım bilgisini taşır;
  * SofaScore'un kulübü olmayan oyuncuya verdiği yer tutucu takım ("No team") arama sonucunda takım değildir.

Hiçbir istek SofaScore'a gitmez: kayıtlar sahte yüklerle depoya yazılır.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest
from fastapi.testclient import TestClient

import conftest
import store_fixtures as sf
from characterization import pin_default_settings
from sofascore_scraper.config import loader
from sofascore_scraper.services.follows import _search_hit
from sofascore_scraper.services.query import QueryService
from sofascore_scraper.slices import SLICE_OK, Outcome
from sofascore_scraper.store import FollowSpec, Store, open_store
from sofascore_scraper.web.app import app

client = TestClient(app)
CASE = "football/A_finished-100-ended__16837335"
MEN, WOMEN, NATIONAL, PLAIN = 36456, 36460, 4700, 9101


def _team(team_id: int, name: str, **extra: Any) -> Dict[str, Any]:
    return {"id": team_id, "name": name, "slug": name.lower().replace(" ", "-"), **extra}


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    pin_default_settings(monkeypatch)
    loader.reset()
    yield
    monkeypatch.undo()
    loader.reset()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conftest.STORE_BOUNDARY.add_data_dir(str(data_dir))
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(data_dir))
    found = open_store(data_dir)
    turkey = {"alpha2": "TR", "name": "Turkey"}
    teams = [
        (9600001, _team(MEN, "Fenerbahçe", gender="M", national=False, country=turkey, type=0,
                        sport={"slug": "volleyball", "name": "Volleyball"}),
         _team(PLAIN, "Galatasaray")),
        (9600002, _team(WOMEN, "Fenerbahçe", gender="F", national=False, country=turkey, type=0,
                        sport={"slug": "volleyball", "name": "Volleyball"}),
         _team(PLAIN + 1, "Eczacıbaşı")),
        (9600003, _team(NATIONAL, "Türkiye", gender="M", national=True, country=turkey, type=0),
         _team(PLAIN + 2, "Spain")),
    ]
    for eid, home, away in teams:
        payload = sf.basic_payload(sf.Ev(CASE, sf.PL, sf.PL_2526, "Home", "Away", eid=eid))
        payload["homeTeam"], payload["awayTeam"] = copy.deepcopy(home), copy.deepcopy(away)
        found.events.put(eid, {"event": Outcome(SLICE_OK, data=payload)})
    return found


def data(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


def test_the_team_record_tells_same_named_teams_apart(store: Store) -> None:
    women = data(client.get(f"/api/v1/teams/{WOMEN}"))
    assert women == {"id": WOMEN, "sport": "volleyball", "type": "team", "name": "Fenerbahçe", "short_name": None,
                     "slug": "fenerbahçe", "name_code": None, "country_code": "TR", "gender": "F", "national": False,
                     "followed": False}
    men = data(client.get(f"/api/v1/teams/{MEN}"))
    assert (men["name"], men["gender"], men["sport"]) == ("Fenerbahçe", "M", "volleyball")
    national = data(client.get(f"/api/v1/teams/{NATIONAL}"))
    assert (national["national"], national["country_code"], national["sport"]) == (True, "TR", "football")
    # SofaScore'un söylemediği alanlar null
    plain = data(client.get(f"/api/v1/teams/{PLAIN}"))
    assert (plain["gender"], plain["national"], plain["country_code"], plain["type"]) == (None, None, None, None)


def test_the_team_record_says_whether_a_team_follow_names_it(store: Store) -> None:
    store.follows.add(FollowSpec(kind="team", entity_id=WOMEN, name="Fenerbahçe"), origin="api")
    assert data(client.get(f"/api/v1/teams/{WOMEN}"))["followed"] is True
    assert data(client.get(f"/api/v1/teams/{MEN}"))["followed"] is False
    # aynı kimlikli bir oyuncu takibi takım takibi değildir
    store.follows.add(FollowSpec(kind="player", entity_id=MEN, name="Someone"), origin="api")
    assert data(client.get(f"/api/v1/teams/{MEN}"))["followed"] is False


def test_an_unknown_team_is_not_found(store: Store) -> None:
    r = client.get("/api/v1/teams/123456")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found" and r.json()["error"]["details"] == {"team_id": 123456}
    assert client.get("/api/v1/teams/0").status_code == 422
    assert QueryService(store).team(0) is None and QueryService(store).team(True) is None  # type: ignore[arg-type]


def test_catalog_suggestions_carry_gender_and_national(store: Store) -> None:
    hits = data(client.get("/api/v1/catalog/suggest", params={"q": "fener"}))
    assert sorted((h["id"], h["gender"], h["national"]) for h in hits) == [(MEN, "M", False), (WOMEN, "F", False)]
    national = data(client.get("/api/v1/catalog/suggest", params={"q": "türkiye"}))
    assert [(h["kind"], h["id"], h["national"], h["country"]) for h in national] == [
        ("team", NATIONAL, True, {"code": "TR", "name": None})]
    unknown = data(client.get("/api/v1/catalog/suggest", params={"q": "galata"}))
    assert [(h["gender"], h["national"]) for h in unknown] == [(None, None)]


def test_sofascore_s_placeholder_team_is_no_team() -> None:
    def player(team: Dict[str, Any]) -> Any:
        found = _search_hit({"type": "player", "entity": {"id": 99001, "name": "Luca Icardi", "team": team}},
                            set(), typed=True)
        assert found is not None
        return found

    for name in ("No team", "no team", "No-team", " No Team "):
        bare = player({"id": 288217, "name": name, "sport": {"slug": "football", "name": "Football"}})
        assert (bare.team_id, bare.team_name) == (None, None)
        # takımın sporu yine sonucun sporudur
        assert bare.sport == "football"
    club = player({"id": 3061, "name": "Galatasaray"})
    assert (club.team_id, club.team_name) == (3061, "Galatasaray")
