"""
Durum ve ayar uçlarının küçük kusurları (plan maddesi FX-2). Tümü çevrimdışı.

  * GET /api/status uygulamanın sürümünü verir (koda yazılmış "1.0.0" değil).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.version import __version__, read_version
from src.web.app import app
from src.web.routes import scrape as scrape_mod

client = TestClient(app)


# --- GET /api/status ---------------------------------------------------------------------------


def test_status_reports_the_application_version():
    body = client.get("/api/status").json()
    assert body["version"] == __version__ == read_version()
    # /health ve OpenAPI belgesiyle aynı değer
    assert body["version"] == client.get("/health").json()["version"] == app.version
    assert list(body) == ["version", "leagues_count", "language"]


def test_status_version_is_not_a_constant_of_the_route(monkeypatch):
    monkeypatch.setattr(scrape_mod, "__version__", "9.8.7-test")
    assert client.get("/api/status").json()["version"] == "9.8.7-test"
