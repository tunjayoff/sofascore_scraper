"""API match detail must expose SofaScore section payloads used by the UI."""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.web.app import app

REQUIRED_TOP = ("basic", "statistics", "incidents", "lineups", "h2h", "pregame_form", "team_streaks")


def test_match_detail_sections_present_when_data_exists():
    client = TestClient(app)
    listing = client.get("/api/matches?limit=5")
    assert listing.status_code == 200
    items = listing.json().get("items") or []
    if not items:
        return

    mid = items[0]["match_id"]
    r = client.get(f"/api/matches/{mid}")
    if r.status_code == 404:
        return
    assert r.status_code == 200
    data = r.json()
    for key in REQUIRED_TOP:
        assert key in data, f"missing section {key}"

    basic = data.get("basic") or {}
    assert basic.get("homeTeam") or basic.get("awayTeam")

    stats = data.get("statistics") or {}
    periods = stats.get("statistics") if isinstance(stats, dict) else stats
    assert isinstance(periods, list)

    incs = data.get("incidents") or {}
    events = incs.get("incidents") if isinstance(incs, dict) else incs
    assert isinstance(events, list)

    lineups = data.get("lineups") or {}
    if lineups.get("home"):
        assert "players" in (lineups.get("home") or {})
