"""GET /api/sports: kayıt defterinin (src/sports.py) salt okunur görünümü."""
from fastapi.testclient import TestClient

from src import sports
from src.web.app import app

REGISTERED = ("football", "basketball", "tennis")
COMMON_KEYS = ("statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents")


def test_api_lists_sports_and_their_slices():
    r = TestClient(app).get("/api/sports")
    assert r.status_code == 200
    body = r.json()
    assert [s["slug"] for s in body] == list(REGISTERED)
    by_slug = {s["slug"]: s for s in body}
    assert by_slug["football"] == {
        "slug": "football",
        "name": "Football",
        "i18n_key": "sport.football",
        "score_family": "football",
        "slices": [
            {"key": k, "path": sports.get_slice(k).path, "required": True, "default_enabled": True}
            for k in COMMON_KEYS
        ],
    }
    assert by_slug["basketball"]["score_family"] == "periods"
    assert [s["key"] for s in by_slug["basketball"]["slices"]] == list(COMMON_KEYS)
    assert by_slug["tennis"]["score_family"] == "sets"
    assert by_slug["tennis"]["slices"][-1] == {
        "key": "point_by_point", "path": "/event/{event_id}/point-by-point", "required": False, "default_enabled": True,
    }
    assert [s["key"] for s in by_slug["tennis"]["slices"]] == list(COMMON_KEYS) + ["point_by_point"]


def test_api_sports_follows_the_registry(monkeypatch):
    monkeypatch.setattr(sports, "SPORTS", sports.SPORTS[:1])
    assert [s["slug"] for s in TestClient(app).get("/api/sports").json()] == ["football"]


def test_api_sports_is_read_only():
    client = TestClient(app)
    assert client.post("/api/sports", json={}).status_code == 405
    assert client.delete("/api/sports").status_code == 405
