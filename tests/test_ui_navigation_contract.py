"""UI contract: the web app's client-side routes are served by the SPA fallback."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sofascore_scraper.web.app import FRONTEND_DIST, app


@pytest.mark.skipif(not FRONTEND_DIST.is_dir(), reason="frontend/dist yok (npm run build)")
def test_spa_client_routes_served():
    client = TestClient(app)
    for path in ("/", "/download", "/matches", "/match/1", "/activity", "/settings", "/advanced/jobs"):
        r = client.get(path)
        assert r.status_code == 200, path
