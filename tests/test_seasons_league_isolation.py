"""Seasons for league A must not look like league B (UI download panel contract)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.web.app import app


def test_seasons_are_league_specific():
    client = TestClient(app)
    pl = client.get("/api/leagues/17/seasons")
    mls = client.get("/api/leagues/242/seasons")
    assert pl.status_code == 200
    assert mls.status_code == 200
    pl_names = " ".join(str(s.get("name") or s.get("year") or "") for s in (pl.json().get("seasons") or [])[:5]).lower()
    mls_names = " ".join(str(s.get("name") or s.get("year") or "") for s in (mls.json().get("seasons") or [])[:5]).lower()
    if pl_names:
        assert "premier" in pl_names or "26/27" in pl_names or "25/26" in pl_names
    if mls_names:
        assert "mls" in mls_names
        assert "premier" not in mls_names
