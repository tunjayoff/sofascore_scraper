"""
GET /api/v1/sports: kayıt defterinin (sofascore_scraper/sports.py) salt okunur görünümü. 2.x'in `/api/sports` yolu
3.1'de kalktı (P30); buradaki denetimler maç detayı dilimlerinin o yolun gösterdiği dört alanıdır.
"""
from typing import Any, Dict, List

from fastapi.testclient import TestClient

from sofascore_scraper import sports
from sofascore_scraper.web.app import app

REGISTERED = ("football", "basketball", "tennis", "american-football", "aussie-rules", "ice-hockey", "handball",
              "rugby", "futsal", "minifootball", "floorball",  # SP-1: + sekiz periyot sporu
              "volleyball", "badminton", "table-tennis", "padel", "snooker",  # SP-2: + beş set sporu
              "baseball", "cricket", "esports", "darts", "mma")  # SP-3: + beş B sınıfı spor
COMMON_KEYS = ("statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents")
FIELDS = ("key", "path", "required", "default_enabled")


def _sports() -> List[Dict[str, Any]]:
    """v1'in sporları; dilimler yalnızca maç detayı dilimleri ve dört alanıyla."""
    r = TestClient(app).get("/api/v1/sports")
    assert r.status_code == 200
    detail_keys = {s.key for s in sports.DETAIL_SLICES}
    return [
        {**{k: sport[k] for k in ("slug", "name", "i18n_key", "score_family")},
         "slices": [{k: s[k] for k in FIELDS} for s in sport["slices"] if s["key"] in detail_keys]}
        for sport in r.json()["data"]
    ]


def test_api_lists_sports_and_their_slices():
    body = _sports()
    assert [s["slug"] for s in body] == list(REGISTERED)
    by_slug = {s["slug"]: s for s in body}
    assert by_slug["football"] == {
        "slug": "football",
        "name": "Football",
        "i18n_key": "sport.football",
        "score_family": "football",
        "slices": [
            # FX-16: pregame_form istenir ama tamlığa girmez
            {"key": k, "path": sports.get_slice(k).path, "required": k != "pregame_form", "default_enabled": True}
            for k in COMMON_KEYS
        ],
    }
    assert by_slug["basketball"]["score_family"] == "periods"
    assert [s["key"] for s in by_slug["basketball"]["slices"]] == list(COMMON_KEYS)
    assert by_slug["tennis"]["score_family"] == "sets"
    assert by_slug["tennis"]["slices"][-1] == {
        "key": "point_by_point", "path": "/event/{event_id}/point-by-point", "required": True, "default_enabled": True,
    }
    # FX-16: teniste kadro ve olaylar istenmez
    assert [s["key"] for s in by_slug["tennis"]["slices"]] == [
        k for k in COMMON_KEYS if k not in ("lineups", "incidents")] + ["point_by_point"]
    # SP-3: e-sporun oyunları kendi dilimi; yeni skor aileleri
    assert by_slug["esports"]["slices"][-1] == {
        "key": "esports_games", "path": "/event/{event_id}/esports-games", "required": True, "default_enabled": True,
    }
    assert [by_slug[s]["score_family"] for s in ("baseball", "cricket", "esports", "darts", "mma")] == [
        "innings", "cricket", "sets", "sets", "fight"]


def test_api_sports_follows_the_registry(monkeypatch):
    monkeypatch.setattr(sports, "SPORTS", sports.SPORTS[:1])
    assert [s["slug"] for s in _sports()] == ["football"]


def test_api_sports_is_read_only():
    client = TestClient(app)
    assert client.post("/api/v1/sports", json={}).status_code == 405
    assert client.delete("/api/v1/sports").status_code == 405


def test_api_says_which_sports_are_individual():
    """B1 (e2e F31): `individual` kayıt defterinden; web arayüzü bireysel sporların listesini artık kendisi tutmaz."""
    r = TestClient(app).get("/api/v1/sports")
    flags = {s["slug"]: s["individual"] for s in r.json()["data"]}
    assert {slug for slug, on in flags.items() if on} == {
        "tennis", "badminton", "table-tennis", "padel", "snooker", "darts", "mma"}
    assert flags == {spec.slug: spec.individual for spec in sports.SPORTS}
