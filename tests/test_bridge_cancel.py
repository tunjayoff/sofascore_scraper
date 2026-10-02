"""
Köprünün sync yolunda iptal (plan bölüm 15, satır 61; P24): ortak challenge çözümünü ya da sayfanın fetch()'ini
bekleyen çağıran, işi durdurulunca beklemeyi bırakır; ortak çözüm öteki bekleyenler için sürer.

Gerçek tarayıcı yok: köprü nesnesi `__new__` ile kurulur, sayfa ve çözüm sahte coroutine'lerdir.
"""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import AsyncMock

import pytest

import src.challenge_solver as cs
from src.client import bridge as bridge_module
from src.client import request_context
from src.client.context import FetchCancelled



def _bridge() -> cs.BrowserBridge:
    bridge = cs.BrowserBridge.__new__(cs.BrowserBridge)
    bridge.token, bridge._token_at = None, 0.0
    bridge._solve_task, bridge._solve_wait, bridge._solve_failed_at = None, None, 0.0
    bridge.ensure_ready = AsyncMock()
    return bridge


def test_the_old_module_name_is_the_bridge_module() -> None:
    assert cs is bridge_module
    assert cs.BrowserBridge is bridge_module.BrowserBridge


def test_a_cancelled_caller_stops_waiting_for_a_page_fetch() -> None:
    release = threading.Event()

    async def hanging_fetch(path: str) -> None:
        while not release.is_set():  # sayfanın fetch()'i: 20 sn'ye kadar sürebilir
            await asyncio.sleep(0.05)

    bridge = _bridge()
    bridge.fetch_json = hanging_fetch
    stop_at = time.monotonic() + 0.1
    try:
        with request_context(cancel=lambda: time.monotonic() >= stop_at):
            started = time.monotonic()
            with pytest.raises(FetchCancelled):
                cs._run_sync(bridge.fetch_json("/event/1"), 10.0, cancellable=True)
            assert time.monotonic() - started < 0.1 + 4 * cs._CANCEL_CHECK_SECONDS + 2.0  # 10 sn değil
    finally:
        release.set()


def test_without_the_flag_the_caller_still_waits_for_the_result() -> None:
    async def slow() -> str:
        await asyncio.sleep(0.3)
        return "done"

    with request_context(cancel=lambda: True):
        assert cs._run_sync(slow(), 5.0) == "done"


def test_a_cancelled_caller_leaves_the_shared_solve_running_for_the_others() -> None:
    finished = threading.Event()
    release = threading.Event()
    bridge = _bridge()

    async def solve() -> str:
        while not release.is_set():
            await asyncio.sleep(0.05)
        finished.set()
        return "jwt"

    bridge._solve_challenge = solve
    stop_at = time.monotonic() + 0.1
    with request_context(cancel=lambda: time.monotonic() >= stop_at):
        with pytest.raises(FetchCancelled):
            cs._run_sync(bridge.solve_challenge(), 10.0, cancellable=True)
    # Öteki bekleyen (iptal edilmemiş iş) aynı çözümün sonucunu alır
    release.set()
    assert cs._run_sync(bridge.solve_challenge(), 10.0, cancellable=True) == "jwt"
    assert finished.is_set()


def test_the_sync_bridge_call_raises_the_cancel_instead_of_returning_none(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    async def hanging_fetch(path: str) -> None:
        while not release.is_set():
            await asyncio.sleep(0.05)

    bridge = _bridge()
    bridge.fetch_json = hanging_fetch
    monkeypatch.setattr(cs.BrowserBridge, "get_instance", classmethod(lambda cls, *a, **k: bridge))
    stop_at = time.monotonic() + 0.1
    try:
        with request_context(cancel=lambda: time.monotonic() >= stop_at):
            with pytest.raises(FetchCancelled):
                cs.fetch_api_via_browser_sync("/event/1", timeout=10.0)
    finally:
        release.set()


def test_relative_paths_use_the_configured_api_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """Köprü kendi API kökünü tutmaz (plan bölüm 15, satır 57): göreli yol transport'un köküne eklenir."""
    from src.client import transport

    seen = []

    async def api_fetch(url: str, cache_mode: str) -> dict:
        seen.append(url)
        return {"status": 200, "ok": True, "data": {"a": 1}}

    monkeypatch.setattr(transport, "API_BASE_URL", "https://api.example.invalid/api/v1")
    bridge = _bridge()
    bridge._api_fetch = api_fetch
    assert asyncio.run(bridge.fetch_json("/event/1")) == {"a": 1}
    assert seen == ["https://api.example.invalid/api/v1/event/1"]
