"""404 must not be retried — blocks cancel and wastes minutes on missing slices."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import src.utils as utils


def test_sync_request_does_not_retry_404():
    calls = {"n": 0}

    class FakeResp:
        status_code = 404
        reason = "Not Found"
        headers = {}

        def json(self):
            return {}

    def fake_get(*args, **kwargs):
        calls["n"] += 1
        return FakeResp()

    with patch.object(utils.cffi_requests, "get", side_effect=fake_get), patch.object(
        utils.time, "sleep"
    ) as sleep:
        out = utils.make_api_request("https://www.sofascore.com/api/v1/event/1/pregame-form")
        assert out is None
        assert calls["n"] == 1
        # No backoff sleeps for 404
        assert sleep.call_count == 0


def test_sync_request_retries_transient_5xx():
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, code):
            self.status_code = code
            self.reason = "Err"
            self.headers = {}

        def json(self):
            return {"ok": True}

    def fake_get(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            return FakeResp(500)
        return FakeResp(200)

    with patch.object(utils.cffi_requests, "get", side_effect=fake_get), patch.object(
        utils.time, "sleep"
    ), patch.object(utils, "_get_runtime_request_config", return_value={
        "max_retries": 3,
        "request_timeout": 5,
        "wait_time_min": 0,
        "wait_time_max": 0,
    }), patch.object(utils, "_get_proxy_config", return_value=(False, None)):
        out = utils.make_api_request("https://example.com/x")
        assert out == {"ok": True}
        assert calls["n"] == 2
