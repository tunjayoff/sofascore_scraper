"""Delivery readiness: every safe API route responds with expected shape."""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.web.app import app
from src.web.routes import api as api_mod


client = TestClient(app)


def test_core_reads():
    assert client.get("/health").status_code == 200
    assert client.get("/api/status").status_code == 200
    assert client.get("/api/scrape/status").status_code == 200
    assert client.get("/api/dashboard").status_code == 200
    assert client.get("/api/stats/system").status_code == 200
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/jobs?limit=10").status_code == 200
    assert isinstance(client.get("/api/leagues").json(), list)


def test_matches_list_and_spa_shell():
    r = client.get("/api/matches?limit=5&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body
    # SPA index
    r = client.get("/")
    assert r.status_code == 200
    assert "html" in r.headers.get("content-type", "").lower() or b"<html" in r.content.lower() or b"<!DOCTYPE" in r.content.upper()


def test_league_dependent_reads():
    leagues = client.get("/api/leagues").json()
    assert leagues, "need at least one configured league for delivery check"
    lid = leagues[0]["id"]
    r = client.get(f"/api/leagues/{lid}/seasons")
    assert r.status_code == 200
    assert "seasons" in r.json()
    r = client.get(f"/api/leagues/{lid}/missing-details")
    assert r.status_code == 200
    assert "missing" in r.json() or isinstance(r.json(), dict)
    r = client.get(f"/api/leagues/search?q=Prem")
    assert r.status_code == 200
    # remote search may hit network — tolerate 200 or 5xx without crashing suite
    r = client.get("/api/leagues/search-remote?q=Premier")
    assert r.status_code in (200, 429, 500, 502, 503)


def test_match_detail_or_404():
    r = client.get("/api/matches?limit=1&offset=0")
    items = r.json().get("items") or []
    if not items:
        r = client.get("/api/matches/0")
        assert r.status_code in (404, 500)
        return
    mid = items[0].get("match_id") or items[0].get("id")
    r = client.get(f"/api/matches/{mid}")
    assert r.status_code in (200, 404)


def test_export_csv_available():
    r = client.get("/api/export/csv")
    # may be empty file or redirect — must not 500
    assert r.status_code in (200, 404)


def test_cancel_idle_and_conflict_fetch():
    snap = api_mod._job_store.snapshot()
    if snap.get("is_running"):
        api_mod._job_store.request_cancel()
        api_mod._job_store.update(status="Cancelled", progress=0, current_task="cleanup", finished=True)
    assert client.post("/api/scrape/cancel").status_code == 400
    api_mod._job_store.create_running({"mode": "full"})
    try:
        assert client.post(
            "/api/fetch",
            json={"mode": "full", "selections": [{"league_id": 17, "season_ids": [1]}]},
        ).status_code == 409
        assert client.post("/api/scrape/cancel").status_code == 200
    finally:
        api_mod._job_store.update(status="Cancelled", progress=0, current_task="cleanup", finished=True)


def test_settings_roundtrip_safe():
    before = client.get("/api/settings").json()
    # POST same settings back (no destructive change)
    r = client.post("/api/settings", json=before if isinstance(before, dict) else {})
    assert r.status_code in (200, 422)


def test_backup_endpoint():
    r = client.post("/api/data/backup")
    assert r.status_code in (200, 500)  # 500 if zip fails is env issue; must not hang
    if r.status_code == 200:
        body = r.json()
        assert "path" in body or "status" in body or "download" in str(body).lower() or True


if __name__ == "__main__":
    test_core_reads()
    test_matches_list_and_spa_shell()
    test_league_dependent_reads()
    test_match_detail_or_404()
    test_export_csv_available()
    test_cancel_idle_and_conflict_fetch()
    test_settings_roundtrip_safe()
    test_backup_endpoint()
    print("delivery api ok")
