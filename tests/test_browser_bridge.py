"""
BrowserBridge ve Turnstile çözücü testleri.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.challenge_solver import (
    BrowserBridge,
    apply_token_to_headers,
    fetch_api_via_browser,
    get_cached_token,
    _is_token_valid,
)
from src.utils import make_api_request, make_api_request_async


def test_apply_token_to_headers_adds_cookie_and_header():
    headers = {"Accept": "*/*"}
    token = "test_jwt_token_123"
    result = apply_token_to_headers(headers, token=token)
    assert result["X-Captcha"] == token
    assert "sofa_captcha=test_jwt_token_123" in result["Cookie"]


def test_apply_token_to_headers_preserves_existing_cookies():
    headers = {"Cookie": "existing=val"}
    token = "test_jwt_token_123"
    result = apply_token_to_headers(headers, token=token)
    assert result["Cookie"] == "existing=val; sofa_captcha=test_jwt_token_123"


@pytest.mark.asyncio
async def test_browser_bridge_fetch_json_handles_403_and_retries():
    bridge = BrowserBridge(profile_dir="/tmp/test_bridge_profile")
    bridge.ensure_ready = AsyncMock()
    bridge.solve_challenge = AsyncMock(return_value="new_token_jwt")

    # Mock page.evaluate
    # First call returns 403 challenge, second call returns 200 ok
    call_count = 0

    async def fake_evaluate(script, args=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"status": 403, "ok": False, "data": None, "text": '{"error":{"reason":"challenge"}}'}
        return {"status": 200, "ok": True, "data": {"seasons": [{"id": 17}]}, "text": None}

    mock_page = MagicMock()
    mock_page.evaluate = AsyncMock(side_effect=fake_evaluate)
    mock_page.is_closed = MagicMock(return_value=False)
    bridge.page = mock_page

    result = await bridge.fetch_json("/unique-tournament/17/seasons")
    assert result == {"seasons": [{"id": 17}]}
    assert bridge.solve_challenge.await_count == 1


def test_make_api_request_falls_back_to_browser_bridge_on_challenge():
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = '{"error": {"code": 403, "reason": "challenge"}}'
    mock_response.headers = {}

    expected_data = {"seasons": [{"id": 1, "name": "Test Season"}]}

    with patch("src.utils.cffi_requests.get", return_value=mock_response), \
         patch("src.challenge_solver.fetch_api_via_browser_sync", return_value=expected_data):
        res = make_api_request("/unique-tournament/17/seasons")
        assert res == expected_data


@pytest.mark.asyncio
async def test_make_api_request_async_falls_back_to_browser_bridge_on_challenge():
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = '{"error": {"code": 403, "reason": "challenge"}}'
    mock_response.headers = {}

    mock_session = MagicMock()
    mock_session.get = AsyncMock(return_value=mock_response)
    mock_session.headers = {}

    expected_data = {"events": [{"id": 12345}]}

    with patch("src.challenge_solver.fetch_api_via_browser", new_callable=AsyncMock, return_value=expected_data):
        res = await make_api_request_async(mock_session, "/sport/football/events/live")
        assert res == expected_data


def test_browser_calls_share_one_event_loop_across_callers():
    """Async çağrılar çağıranın döngüsünde değil, sync yol ile aynı arka plan döngüsünde çalışmalı."""
    import asyncio
    from src.challenge_solver import fetch_api_via_browser_sync

    loops = []

    class FakeBridge:
        async def fetch_json(self, path_or_url):
            loops.append(asyncio.get_running_loop())
            return {"ok": path_or_url}

    with patch.object(BrowserBridge, "get_instance", return_value=FakeBridge()):
        # asyncio.run her seferinde geçici bir döngü açıp kapatır (sezon çekme akışı gibi)
        assert asyncio.run(fetch_api_via_browser("/a")) == {"ok": "/a"}
        assert asyncio.run(fetch_api_via_browser("/b")) == {"ok": "/b"}
        assert fetch_api_via_browser_sync("/c") == {"ok": "/c"}

    assert len(loops) == 3
    assert loops[0] is loops[1] is loops[2]
    assert loops[0].is_running()
