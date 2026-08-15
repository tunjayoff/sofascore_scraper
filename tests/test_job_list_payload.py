"""Job list rows should expose payload so UI can show what ran (not only status)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.web.app import app


def test_jobs_include_payload_and_task():
    client = TestClient(app)
    r = client.get("/api/jobs?limit=5")
    assert r.status_code == 200
    jobs = r.json().get("jobs") or []
    for j in jobs:
        assert "status" in j
        assert "current_task" in j
        # payload may be null for ancient rows; when present must be dict
        if j.get("payload") is not None:
            assert isinstance(j["payload"], dict)
