"""Stop must reach the request layer: no new request after cancel, and back-off waits end at once."""
from __future__ import annotations

import asyncio
import contextvars
import threading
import time
from unittest.mock import patch

import pytest

from sofascore_scraper.client import transport
from sofascore_scraper.client.transport import _asleep, _sleep
from sofascore_scraper.client.context import FetchCancelled, raise_if_cancelled, set_cancel_check


def _in_fresh_context(fn):
    """Run fn in its own context, like the web job thread, so checks don't leak between tests."""
    return contextvars.copy_context().run(fn)


def test_sleep_ends_as_soon_as_cancelled():
    flag = {"stop": False}

    def body():
        set_cancel_check(lambda: flag["stop"])
        threading.Timer(0.3, lambda: flag.update(stop=True)).start()
        t0 = time.monotonic()
        with pytest.raises(FetchCancelled):
            _sleep(30)
        return time.monotonic() - t0

    assert _in_fresh_context(body) < 2


def test_async_sleep_ends_as_soon_as_cancelled():
    flag = {"stop": False}

    def body():
        set_cancel_check(lambda: flag["stop"])

        async def run():
            asyncio.get_running_loop().call_later(0.3, lambda: flag.update(stop=True))
            t0 = time.monotonic()
            with pytest.raises(FetchCancelled):
                await _asleep(30)
            return time.monotonic() - t0

        return asyncio.run(run())

    assert _in_fresh_context(body) < 2


def test_no_request_is_sent_after_cancel():
    def body():
        set_cancel_check(lambda: True)
        with patch.object(transport.cffi_requests, "get") as get:
            with pytest.raises(FetchCancelled):
                transport.make_api_request("https://www.sofascore.com/api/v1/event/1")
            get.assert_not_called()

    _in_fresh_context(body)


def test_403_backoff_is_cut_short_by_cancel():
    """A 403 used to sleep up to 120 s per attempt with no way to stop it."""
    flag = {"stop": False}

    class Forbidden:
        status_code = 403
        reason = "Forbidden"
        headers = {}
        text = '{"error": {"code": 403, "reason": "Forbidden"}}'

    def fake_get(*a, **k):
        threading.Timer(0.3, lambda: flag.update(stop=True)).start()
        return Forbidden()

    def body():
        set_cancel_check(lambda: flag["stop"])
        with patch.object(transport.cffi_requests, "get", side_effect=fake_get) as get:
            t0 = time.monotonic()
            with pytest.raises(FetchCancelled):
                transport.make_api_request("https://www.sofascore.com/api/v1/event/1")
            assert get.call_count == 1
            return time.monotonic() - t0

    assert _in_fresh_context(body) < 2


def test_broad_except_does_not_swallow_cancel():
    """The fetchers wrap requests in `except Exception`; cancel must pass through them."""

    def fetcher_like():
        try:
            raise_if_cancelled()
        except Exception:
            return "swallowed"
        return "not cancelled"

    def body():
        set_cancel_check(lambda: True)
        with pytest.raises(FetchCancelled):
            fetcher_like()

    _in_fresh_context(body)


def test_other_threads_are_not_affected():
    """Only the job's own context is cancelled; a parallel web request keeps working."""
    seen = {}

    def other():
        raise_if_cancelled()
        seen["ok"] = True

    def body():
        set_cancel_check(lambda: True)
        th = threading.Thread(target=other)
        th.start()
        th.join()

    _in_fresh_context(body)
    assert seen.get("ok") is True


def test_cancel_propagates_through_to_thread():
    """asyncio.to_thread copies the context, so detail refills in worker threads stop too."""

    def body():
        set_cancel_check(lambda: True)

        async def run():
            await asyncio.to_thread(raise_if_cancelled)

        with pytest.raises(FetchCancelled):
            asyncio.run(run())

    _in_fresh_context(body)
