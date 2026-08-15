"""UI contract: matches list exposes clickable match_id for detail route."""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.web.app import app


def test_matches_items_have_match_id_for_navigation():
    client = TestClient(app)
    r = client.get("/api/matches?limit=5&offset=0")
    assert r.status_code == 200
    items = r.json().get("items") or []
    if not items:
        return
    for row in items:
        mid = row.get("match_id")
        assert mid is not None and str(mid).strip() != ""
        detail = client.get(f"/api/matches/{mid}")
        assert detail.status_code in (200, 404)


def test_spa_client_routes_served():
    client = TestClient(app)
    for path in ("/", "/matches", "/advanced/jobs", "/advanced/settings", "/advanced/leagues", "/advanced/stats"):
        r = client.get(path)
        assert r.status_code == 200, path
