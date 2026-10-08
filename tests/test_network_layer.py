"""İstek katmanı: tipli hatalar, boşa bekleme yok, istek başına başlık/limit; köprü ve devre kesici."""
from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sofascore_scraper.client import bridge as cs
from sofascore_scraper.client import transport
from sofascore_scraper.exceptions import APIError, NetworkError, RateLimitError
from sofascore_scraper.services.detail_phase import DetailPhase
from sofascore_scraper.store import open_store
from sofascore_scraper.client.context import FetchCancelled

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

    with patch.object(transport, "_get_runtime_request_config", return_value=CFG), \
            patch.object(transport, "_get_proxy_config", return_value=(False, "")), \
            patch.object(transport, "_asleep", side_effect=fake_asleep), \
            patch.object(transport, "_sleep", side_effect=fake_sleep):
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
        _run(transport.make_api_request_async(session, "/x"))
    assert ei.value.status_code == 403
    assert session.get.call_count == 3
    assert len(sleeps) == 2  # son denemeden sonra beklenmez


def test_async_429_raises_rate_limit_error():
    session = _session(*[Resp(429)] * 3)
    with _patched(), pytest.raises(RateLimitError) as ei:
        _run(transport.make_api_request_async(session, "/x"))
    assert ei.value.status_code == 429


def test_async_permanent_4xx_is_not_retried():
    session = _session(Resp(400))
    with _patched() as sleeps, pytest.raises(APIError) as ei:
        _run(transport.make_api_request_async(session, "/x"))
    assert ei.value.status_code == 400
    assert session.get.call_count == 1 and sleeps == []


def test_async_5xx_retried_then_succeeds():
    session = _session(Resp(502), Resp(200, {"ok": 1}))
    with _patched():
        assert _run(transport.make_api_request_async(session, "/x")) == {"ok": 1}


def test_async_network_error_raises_network_error():
    s = MagicMock()
    s.get = AsyncMock(side_effect=RuntimeError("curl: (7) Failed to connect"))
    with _patched(), pytest.raises(NetworkError):
        _run(transport.make_api_request_async(s, "/x"))


def test_async_sends_fresh_headers_per_request():
    session = _session(Resp(200, {}), Resp(200, {}))
    with _patched(), patch.object(transport, "get_sofascore_hash", side_effect=["aaaaaa", "bbbbbb"]):
        _run(transport.make_api_request_async(session, "/a"))
        _run(transport.make_api_request_async(session, "/b"))
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
        await asyncio.gather(*[transport.make_api_request_async(session, f"/{i}") for i in range(12)])

    with _patched(), patch.object(transport._cm, "get_max_concurrent", return_value=3):
        _run(many())
    assert state["peak"] == 3


# --- senkron istek ---------------------------------------------------------------

def test_sync_403_returns_none_without_final_sleep():
    with _patched() as sleeps, patch.object(transport.cffi_requests, "get", return_value=Resp(403, text="no")) as get:
        assert transport.make_api_request("/x") is None
    assert get.call_count == 3 and len(sleeps) == 2


def test_sync_permanent_4xx_not_retried():
    with _patched() as sleeps, patch.object(transport.cffi_requests, "get", return_value=Resp(401)) as get:
        assert transport.make_api_request("/x") is None
    assert get.call_count == 1 and sleeps == []


# --- devre kesici ve iptal -------------------------------------------------------

@contextlib.asynccontextmanager
async def _fake_session():
    yield MagicMock()


def _detail_fetcher(tmp_path, threshold=3, concurrency=1) -> DetailPhase:
    cfg = MagicMock()
    cfg.get_max_concurrent.return_value = concurrency
    cfg.get_rate_limit_threshold_consecutive.return_value = threshold
    cfg.get_rate_limit_threshold_ratio.return_value = 0.99
    cfg.get_server_error_threshold_consecutive.return_value = 100
    return DetailPhase(open_store(str(tmp_path)), cfg)


def _event(mid) -> dict:
    return {"id": int(mid), "tournament": {"uniqueTournament": {"id": 77, "name": "Cup"},
                                           "category": {"sport": {"slug": "football"}}},
            "status": {"code": 100, "description": "Ended", "type": "finished"}, "startTimestamp": 1790000000}


def _api(answer):
    """
    İstek katmanının (make_api_request_async) sahtesi; boru hattının istemcisi onu çağırır. `answer(mid, url)` bir
    gövde döndürür ya da fırlatır. Gerçek katman gibi isteğin sonucunu işin devre kesicisine bildirir.
    """
    from sofascore_scraper import breaker

    async def fake(session, url, max_retries=None, **_kw):
        mid = url.split("/event/", 1)[1].split("/", 1)[0]
        try:
            data = await answer(mid, url)
        except Exception as e:
            breaker.report_exception(e)
            raise
        breaker.report_ok()
        return data

    return patch("sofascore_scraper.client.transport.make_api_request_async", new=fake)


def test_breaker_trips_on_repeated_403(tmp_path, monkeypatch):
    monkeypatch.delenv("IGNORE_RATE_LIMIT", raising=False)
    f = _detail_fetcher(tmp_path)
    calls = []

    async def always_403(mid, url):
        calls.append(mid)
        raise APIError("blocked", status_code=403)

    with _api(always_403), patch("sofascore_scraper.client.transport.create_session_async", _fake_session):
        f.fetch_selected(list(range(1, 40)))
    assert f.breaker_tripped is True
    assert f.status_counts.get("403", 0) >= 3
    assert len(calls) < 39  # tüm maçları denemeden durdu


def test_no_fixed_pause_in_a_bulk_download(tmp_path):
    """100'lük batch'ler ve aralarındaki 1 sn'lik bekleme kalktı: hızı ortak istek bütçesi belirler."""
    f = _detail_fetcher(tmp_path, threshold=1000, concurrency=10)

    async def answer(mid, url):
        return {"event": _event(mid)} if url.endswith(f"/event/{mid}") else {}

    with _api(answer), patch("sofascore_scraper.client.transport.create_session_async", _fake_session), \
            patch("sofascore_scraper.services.pipeline.asyncio.sleep", new=AsyncMock()) as sleep:
        assert f.fetch_selected(list(range(1, 251))) == 250
    sleep.assert_not_awaited()


def test_a_failed_event_request_is_not_retried_per_match(tmp_path, monkeypatch):
    """
    Maç başına ek deneme döngüsü (1 sn, 2 sn + rastgele aralarla üç deneme) P13'te kalktı: yeniden denemeyi yalnızca
    istek katmanı yapar (MAX_RETRIES, kendi geri çekilmesiyle; tests/characterization/test_pipeline_divergence.py).
    """
    monkeypatch.delenv("IGNORE_RATE_LIMIT", raising=False)
    f = _detail_fetcher(tmp_path, threshold=1000)
    calls = []

    async def always_500(mid, url):
        calls.append(url)
        raise APIError("boom", status_code=500)

    with _api(always_500), patch("sofascore_scraper.client.transport.create_session_async", _fake_session), \
            patch("sofascore_scraper.services.pipeline.asyncio.sleep", new=AsyncMock()) as sleep:
        assert f.fetch_selected([1]) == 0
    assert len(calls) == 1
    sleep.assert_not_awaited()


def test_cancel_propagates_and_leaves_no_pending_tasks(tmp_path):
    from sofascore_scraper.services.pipeline import FetchPipeline
    from sofascore_scraper.services.planning import WorkItem
    from sofascore_scraper.store import Ref

    store = open_store(str(tmp_path))
    started = []

    async def answer(mid, url):
        started.append(mid)
        if mid == "1":
            raise FetchCancelled()
        await asyncio.sleep(10)

    async def run():
        with pytest.raises(FetchCancelled):
            await FetchPipeline(store, concurrency=5).run(
                [WorkItem(Ref.event(i), "full", (), None, "full") for i in (1, 2, 3)])
        others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert not [t for t in others if not t.done()]

    with _api(answer), patch("sofascore_scraper.client.transport.create_session_async", _fake_session):
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
    bridge._solve_failed_at = 0.0
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
        with pytest.raises(RuntimeError, match="tried recently"):
            await bridge.ensure_ready()

    _run(run())
    assert bridge._launch.call_count == 1
    assert bridge.close.await_count >= 2  # önce eski oturum, sonra yarım başlatma temizliği


def test_failed_solve_is_not_retried_for_a_while():
    bridge = cs.BrowserBridge.__new__(cs.BrowserBridge)
    bridge.token = None
    bridge._token_at = 0.0
    bridge._solve_task = None
    bridge._solve_failed_at = 0.0
    bridge.ensure_ready = AsyncMock()
    bridge._solve_on_captcha_page = AsyncMock(return_value=None)

    async def run():
        assert await bridge.solve_challenge() is None
        assert await bridge.solve_challenge() is None  # geri çekilme: yeniden denemez

    _run(run())
    assert bridge._solve_on_captcha_page.await_count == 1


def test_browser_first_mode_skips_curl(monkeypatch):
    """curl challenge'a takılıp tarayıcı başardıysa sonraki istek doğrudan tarayıcıya gider."""
    session = _session(Resp(403, text="challenge"))
    browser = AsyncMock(return_value={"ok": 1})
    monkeypatch.setattr(transport, "_browser_first_until", 0.0)
    with _patched(), patch("sofascore_scraper.client.bridge.fetch_api_via_browser", browser):
        assert _run(transport.make_api_request_async(session, "/a")) == {"ok": 1}
        assert transport._browser_first()
        assert _run(transport.make_api_request_async(session, "/b")) == {"ok": 1}
    assert session.get.call_count == 1  # ikinci istek curl'e hiç gitmedi
    assert browser.await_count == 2


def test_evaluate_retries_after_page_navigation():
    bridge = cs.BrowserBridge.__new__(cs.BrowserBridge)
    page = MagicMock()
    page.is_closed.return_value = False
    page.url = "https://www.sofascore.com/tr"
    page.wait_for_load_state = AsyncMock()
    page.evaluate = AsyncMock(side_effect=[Exception("Page.evaluate: Execution context was destroyed, most likely because of a navigation"), {"ok": 1}])
    bridge.page = page
    assert _run(bridge.evaluate("js", [1])) == {"ok": 1}
    assert page.evaluate.await_count == 2
    page.wait_for_load_state.assert_awaited_once()


def test_evaluate_does_not_swallow_other_errors():
    bridge = cs.BrowserBridge.__new__(cs.BrowserBridge)
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=ValueError("boom"))
    bridge.page = page
    with pytest.raises(ValueError):
        _run(bridge.evaluate("js"))


@pytest.mark.parametrize("url,mode", [
    ("https://www.sofascore.com/api/v1/event/123", "no-store"),
    ("https://www.sofascore.com/api/v1/sport/football/events/live", "no-store"),
    ("https://www.sofascore.com/api/v1/unique-tournament/17/season/76986/events/round/3", "no-store"),
    ("https://www.sofascore.com/api/v1/unique-tournament/17/season/76986/events/last/0", "no-store"),
    ("https://www.sofascore.com/api/v1/unique-tournament/17/seasons", "default"),
    ("https://www.sofascore.com/api/v1/unique-tournament/17/season/76986/rounds", "default"),
    ("https://www.sofascore.com/api/v1/unique-tournament/17/seasons?x=1", "default"),
])
def test_cache_mode_for(url, mode):
    assert cs.cache_mode_for(url) == mode


def test_fetch_json_sends_cache_mode_to_page():
    bridge = cs.BrowserBridge.__new__(cs.BrowserBridge)
    bridge.token = None
    bridge.ensure_ready = AsyncMock()
    bridge.evaluate = AsyncMock(return_value={"ok": True, "status": 200, "data": {"x": 1}})
    _run(bridge.fetch_json("/event/1"))
    _run(bridge.fetch_json("/unique-tournament/17/seasons"))
    modes = [c.args[1][4] for c in bridge.evaluate.await_args_list]
    assert modes == ["no-store", "default"]
