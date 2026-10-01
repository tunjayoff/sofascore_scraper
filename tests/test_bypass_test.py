"""
POST /api/bypass/test: Ayarlar → Bağlantı testi düğmesinin çağırdığı tek canlı istek.

Sonuç her zaman 200 ile ve tipli bir nedenle döner (blocked / browser / network / upstream);
tarayıcı başlatılamadığında 500 değil. Bütün testler çevrimdışıdır: köprünün isteği sahtedir,
gerçek tarayıcı conftest tarafından engellenir.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import src.challenge_solver as cs
import src.utils as utils
from src import bridge_health

HEALTH_KEYS = {
    "state", "consecutive_failures", "last_success_at", "last_failure_at", "failing_since", "changed_at",
    "last_error", "thresholds",
}


@pytest.fixture
def client():
    from src.web.app import app

    return TestClient(app)


def _bridge_fetch(result=None, *, raises=None, record=None):
    """fetch_api_via_browser sahtesi: isteğe bağlı olarak köprünün yazacağı sağlık kaydını da yazar."""
    async def fake(path):
        assert path == "/sport/football/events/live"
        if record == "success":
            bridge_health.record_success()
        elif record:
            bridge_health.record_failure(record, "test")
        if raises:
            raise raises
        return result

    return patch.object(cs, "fetch_api_via_browser", AsyncMock(side_effect=fake))


def test_bypass_test_success(client):
    with _bridge_fetch({"events": [{"id": 1}, {"id": 2}]}, record="success") as fetch:
        body = client.post("/api/bypass/test").json()
    assert fetch.await_count == 1  # tek istek
    assert body["success"] is True and body["reason"] is None and body["events_count"] == 2
    assert body["message"] == "BrowserBridge connection verified successfully."
    assert set(body["health"]) == HEALTH_KEYS
    assert body["health"]["state"] == "ok" and body["health"]["last_success_at"]
    assert body["browser_ready"] is False and body["has_token"] is False and body["is_valid"] is False


def test_bypass_test_success_with_no_live_events_is_still_a_success(client):
    with _bridge_fetch({"events": []}):
        body = client.post("/api/bypass/test").json()
    assert body["success"] is True and body["events_count"] == 0


@pytest.mark.parametrize("kind", ["challenge", "forbidden"])
def test_bypass_test_reports_blocked_when_the_bridge_recorded_a_refusal(client, kind):
    with _bridge_fetch(None, record=kind):
        r = client.post("/api/bypass/test")
    body = r.json()
    assert r.status_code == 200
    assert body["success"] is False and body["reason"] == "blocked" and "events_count" not in body
    assert body["message"] == "SofaScore refused the request (HTTP 403)."
    assert body["health"]["last_error"]["kind"] == kind and body["health"]["consecutive_failures"] == 1


def test_bypass_test_reports_network_when_no_answer_came_back(client):
    with _bridge_fetch(None):
        body = client.post("/api/bypass/test").json()
    assert body["success"] is False and body["reason"] == "network"
    with _bridge_fetch(raises=asyncio.TimeoutError()):
        body = client.post("/api/bypass/test").json()
    assert body["success"] is False and body["reason"] == "network"


def test_bypass_test_reports_an_unexpected_answer(client):
    with _bridge_fetch({"__404__": True}):
        body = client.post("/api/bypass/test").json()
    assert body["success"] is False and body["reason"] == "upstream"


def test_bypass_test_reports_a_browser_that_cannot_start_instead_of_failing_with_500(client):
    """Sahte yok: conftest gerçek tarayıcıyı engeller, köprü başlatma hatasını sağlık durumuna yazar."""
    r = client.post("/api/bypass/test")
    body = r.json()
    assert r.status_code == 200
    assert body["success"] is False and body["reason"] == "browser" and body["browser_ready"] is False
    assert body["health"]["last_error"]["kind"] == "browser"


def test_bypass_test_reports_an_open_browser_page(client):
    class Page:
        def is_closed(self):
            return False

    with _bridge_fetch({"events": []}), patch.object(cs.BrowserBridge.get_instance(), "page", Page()):
        assert client.post("/api/bypass/test").json()["browser_ready"] is True


def test_bypass_status_does_not_send_a_request(client):
    with _bridge_fetch({"events": []}) as fetch, patch.object(utils.cffi_requests, "get") as get:
        assert client.get("/api/bypass/status").status_code == 200
    assert fetch.await_count == 0 and get.call_count == 0
