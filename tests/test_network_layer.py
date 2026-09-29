"""İstek katmanı: tipli hatalar, boşa bekleme yok, istek başına başlık/limit; köprü ve devre kesici."""
from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.challenge_solver as cs
import src.utils as utils
from src.exceptions import APIError, NetworkError, RateLimitError
from src.match_data_fetcher import MatchDataFetcher
from src.utils import FetchCancelled

CFG = {"max_retries": 3, "request_timeout": 5, "wait_time_min": 0, "wait_time_max": 0}


class Resp:
    def __init__(self, code, body=None, text=""):
        self.status_code = code
        self.reason = "x"
        self.headers = {}
        self._body = body
        self.text = text

    def json(self):
        return self._body


@contextlib.contextmanager
def _patched(sleeper="_asleep"):
    sleeps = []

    async def fake_asleep(sec):
        sleeps.append(sec)

    def fake_sleep(sec):
        sleeps.append(sec)

    with patch.object(utils, "_get_runtime_request_config", return_value=CFG), \
            patch.object(utils, "_get_proxy_config", return_value=(False, "")), \
            patch.object(utils, "_asleep", side_effect=fake_asleep), \
            patch.object(utils, "_sleep", side_effect=fake_sleep):
        yield sleeps


def _session(*responses):
    s = MagicMock()
    s.get = AsyncMock(side_effect=list(responses))
    return s


def _run(coro):
    return asyncio.run(coro)


# --- async istekler ---------------------------------------------------------------

def test_async_403_raises_after_last_attempt_without_final_sleep():
    session = _session(*[Resp(403, text="blocked")] * 3)
    with _patched() as sleeps, pytest.raises(APIError) as ei:
        _run(utils.make_api_request_async(session, "/x"))
    assert ei.value.status_code == 403
    assert session.get.call_count == 3
    assert len(sleeps) == 2  # son denemeden sonra beklenmez


def test_async_429_raises_rate_limit_error():
    session = _session(*[Resp(429)] * 3)
    with _patched(), pytest.raises(RateLimitError) as ei:
        _run(utils.make_api_request_async(session, "/x"))
    assert ei.value.status_code == 429


def test_async_permanent_4xx_is_not_retried():
    session = _session(Resp(400))
    with _patched() as sleeps, pytest.raises(APIError) as ei:
        _run(utils.make_api_request_async(session, "/x"))
    assert ei.value.status_code == 400
    assert session.get.call_count == 1 and sleeps == []


def test_async_5xx_retried_then_succeeds():
    session = _session(Resp(502), Resp(200, {"ok": 1}))
    with _patched():
        assert _run(utils.make_api_request_async(session, "/x")) == {"ok": 1}


def test_async_network_error_raises_network_error():
    s = MagicMock()
    s.get = AsyncMock(side_effect=RuntimeError("curl: (7) Failed to connect"))
    with _patched(), pytest.raises(NetworkError):
        _run(utils.make_api_request_async(s, "/x"))


def test_async_sends_fresh_headers_per_request():
    session = _session(Resp(200, {}), Resp(200, {}))
    with _patched(), patch.object(utils, "get_sofascore_hash", side_effect=["aaaaaa", "bbbbbb"]):
        _run(utils.make_api_request_async(session, "/a"))
        _run(utils.make_api_request_async(session, "/b"))
    sent = [c.kwargs["headers"]["X-Requested-With"] for c in session.get.call_args_list]
    assert sent == ["aaaaaa", "bbbbbb"]


def test_async_requests_respect_max_concurrent():
    state = {"now": 0, "peak": 0}

    async def slow_get(url, **kw):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        return Resp(200, {})

    session = MagicMock()
    session.get = slow_get

    async def many():
        await asyncio.gather(*[utils.make_api_request_async(session, f"/{i}") for i in range(12)])

    with _patched(), patch.object(utils._cm, "get_max_concurrent", return_value=3):
        _run(many())
    assert state["peak"] == 3


# --- senkron istek ---------------------------------------------------------------

def test_sync_403_returns_none_without_final_sleep():
    with _patched() as sleeps, patch.object(utils.cffi_requests, "get", return_value=Resp(403, text="no")) as get:
        assert utils.make_api_request("/x") is None
    assert get.call_count == 3 and len(sleeps) == 2


def test_sync_permanent_4xx_not_retried():
    with _patched() as sleeps, patch.object(utils.cffi_requests, "get", return_value=Resp(401)) as get:
        assert utils.make_api_request("/x") is None
    assert get.call_count == 1 and sleeps == []


# --- devre kesici ve iptal -------------------------------------------------------

@contextlib.asynccontextmanager
async def _fake_session():
    yield MagicMock()


def _detail_fetcher(tmp_path, threshold=3):
    cfg = MagicMock()
    cfg.get_rate_limit_threshold_consecutive.return_value = threshold
    cfg.get_rate_limit_threshold_ratio.return_value = 0.99
    cfg.get_server_error_threshold_consecutive.return_value = 100
    return MatchDataFetcher(cfg, data_dir=str(tmp_path))


def test_breaker_trips_on_repeated_403(tmp_path, monkeypatch):
    monkeypatch.delenv("IGNORE_RATE_LIMIT", raising=False)
    f = _detail_fetcher(tmp_path)
    calls = []

    async def always_403(session, mid):
        calls.append(mid)
        raise APIError("blocked", status_code=403)

    f._fetch_match_data_async = always_403
    with patch("src.utils.create_session_async", _fake_session), \
            patch("src.match_data_fetcher.asyncio.sleep", new=AsyncMock()):
        _run(f.fetch_matches_batch_async(list(range(1, 40)), max_concurrent=1))
    assert f.rate_limit_breaker_triggered is True
    assert f.last_status_counts.get("403", 0) >= 3
    assert len(calls) < 39  # tüm maçları denemeden durdu


def test_cancel_propagates_and_leaves_no_pending_tasks(tmp_path):
    f = _detail_fetcher(tmp_path, threshold=1000)
    started = []

    async def fetch(session, mid):
        started.append(mid)
        if mid == 1:
            raise FetchCancelled()
        await asyncio.sleep(10)

    f._fetch_match_data_async = fetch

    async def run():
        with pytest.raises(FetchCancelled):
            await f.fetch_matches_batch_async([1, 2, 3], max_concurrent=5)
        others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert not [t for t in others if not t.done()]

    with patch("src.utils.create_session_async", _fake_session):
        _run(run())


# --- BrowserBridge ---------------------------------------------------------------

def test_background_loop_is_created_once_under_concurrency():
    seen = []
    barrier = threading.Barrier(8)

    def grab():
        barrier.wait()
        seen.append(cs._get_background_loop())

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(lp) for lp in seen}) == 1


def test_background_call_times_out():
    async def slow():
        await asyncio.sleep(5)

    async def run():
        start = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await cs._run_on_background_loop(slow(), timeout=0.1)
        return time.monotonic() - start

    assert _run(run()) < 2


def test_concurrent_challenges_share_one_solve():
    bridge = cs.BrowserBridge.__new__(cs.BrowserBridge)
    bridge.token = None
    bridge._token_at = 0.0
    bridge._solve_task = None
    bridge.ensure_ready = AsyncMock()
    solves = []

    async def solve():
        solves.append(1)
        await asyncio.sleep(0.05)
        bridge.token, bridge._token_at = "jwt", time.time()
        return "jwt"

    bridge._solve_challenge = solve

    async def run():
        return await asyncio.gather(*[bridge.solve_challenge() for _ in range(6)])

    assert _run(run()) == ["jwt"] * 6
    assert len(solves) == 1


def test_failed_launch_cleans_up_and_backs_off(tmp_path):
    bridge = cs.BrowserBridge(profile_dir=str(tmp_path))
    bridge._launch = AsyncMock(side_effect=RuntimeError("no chrome"))
    bridge.close = AsyncMock()

    async def run():
        with pytest.raises(RuntimeError, match="no chrome"):
            await bridge.ensure_ready()
        with pytest.raises(RuntimeError, match="yakın zamanda"):
            await bridge.ensure_ready()

    _run(run())
    assert bridge._launch.call_count == 1
    assert bridge.close.await_count >= 2  # önce eski oturum, sonra yarım başlatma temizliği
