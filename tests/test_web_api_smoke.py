"""API smoke: leagues, seasons, cancel contract (TestClient, no live SofaScore)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from sofascore_scraper.web import deps
from sofascore_scraper.web.app import app


def test_health_and_leagues_and_cancel_idle():
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200

    r = client.get("/api/leagues")
    assert r.status_code == 200
    leagues = r.json()
    assert isinstance(leagues, list)

    r = client.get("/api/scrape/status")
    assert r.status_code == 200
    body = r.json()
    assert "is_running" in body
    assert "cancel_requested" in body

    if not body.get("is_running"):
        r = client.post("/api/scrape/cancel")
        assert r.status_code == 400

    r = client.get("/api/jobs?limit=5")
    assert r.status_code == 200
    assert "jobs" in r.json()


def test_seasons_endpoint_shape():
    client = TestClient(app)
    leagues = client.get("/api/leagues").json()
    if not leagues:
        return
    lid = leagues[0]["id"]
    r = client.get(f"/api/leagues/{lid}/seasons")
    assert r.status_code == 200
    data = r.json()
    assert "seasons" in data
    assert isinstance(data["seasons"], list)


def test_cancel_while_job_marked_running():
    """Cancel succeeds when JobStore has an active running job."""
    client = TestClient(app)
    # Finish any leftover
    snap = deps.job_store().snapshot()
    if snap.get("is_running"):
        deps.job_store().request_cancel()
        deps.job_store().update(status="Cancelled", progress=0, current_task="cleanup", finished=True)

    deps.job_store().create_running({"mode": "full", "test": True})
    try:
        r = client.post("/api/fetch", json={"mode": "full", "selections": [{"league_id": 17, "season_ids": [1]}]})
        assert r.status_code == 409

        r = client.post("/api/scrape/cancel")
        assert r.status_code == 200, r.text
        assert deps.job_store().cancel_requested() is True
        status = client.get("/api/scrape/status").json()
        assert status.get("cancel_requested") is True
    finally:
        deps.job_store().update(status="Cancelled", progress=0, current_task="test cleanup", finished=True)


if __name__ == "__main__":
    test_health_and_leagues_and_cancel_idle()
    test_seasons_endpoint_shape()
    test_cancel_while_job_marked_running()
    print("api smoke ok")
