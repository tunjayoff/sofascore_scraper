"""UI contract: matches list exposes clickable match_id for detail route."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sofascore_scraper.web.app import FRONTEND_DIST, app


def test_matches_items_have_match_id_for_navigation():
    client = TestClient(app)
    r = client.get("/api/matches?limit=5&offset=0")
    assert r.status_code == 200
    items = r.json().get("items") or []
    assert items, "conftest seeds two matches"
    for row in items:
        mid = row.get("match_id")
        assert mid is not None and str(mid).strip() != ""
        detail = client.get(f"/api/matches/{mid}")
        assert detail.status_code in (200, 404)


@pytest.mark.skipif(not FRONTEND_DIST.is_dir(), reason="frontend/dist yok (npm run build)")
def test_spa_client_routes_served():
    client = TestClient(app)
    for path in ("/", "/download", "/matches", "/match/1", "/activity", "/settings", "/advanced/jobs"):
        r = client.get(path)
        assert r.status_code == 200, path
