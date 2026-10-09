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

from sofascore_scraper.client import bridge as cs
from sofascore_scraper import throttle
from sofascore_scraper.client import bridge as bridge_module
from sofascore_scraper.client import request_context, transport
from sofascore_scraper.client.context import FetchCancelled



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
    from sofascore_scraper.client import transport

    seen = []

    async def api_fetch(url: str, cache_mode: str) -> dict:
        seen.append(url)
        return {"status": 200, "ok": True, "data": {"a": 1}}

    monkeypatch.setattr(transport, "API_BASE_URL", "https://api.example.invalid/api/v1")
    bridge = _bridge()
    bridge._api_fetch = api_fetch
    assert asyncio.run(bridge.fetch_json("/event/1")) == {"a": 1}
    assert seen == ["https://api.example.invalid/api/v1/event/1"]


# --- async yol (FX-18, plan bölüm 15 satır 61, 91 ve 92) -----------------------------------------------
# P13'ten beri her indirme köprüye async yoldan (_run_on_background_loop) gider. Eskiden bu yolda çağıranın
# iptal kontrolüne bakan yoktu: durdurulan iş ortak çözümü (90 sn'ye kadar) ya da sayfanın fetch()'ini (20 sn'ye
# kadar) beklerdi. Köprünün içinde zamanı gelmiş sırası olan istek de durdurmadan sonra gönderilirdi.

_OK = {"status": 200, "ok": True, "data": {"a": 1}, "text": None}
_SLACK = 2.0  # yavaş makine payı
CFG = {"max_retries": 2, "request_timeout": 5, "wait_time_min": 0, "wait_time_max": 0}


def _hanging_bridge(release: threading.Event, evaluated: list) -> cs.BrowserBridge:
    """Sayfanın fetch()'i `release` gelene kadar sürer; gönderilen her istek `evaluated`a yazılır."""

    async def evaluate(script: str, arg: object = None) -> dict:
        evaluated.append(arg)
        while not release.is_set():
            await asyncio.sleep(0.05)
        return _OK

    bridge = _bridge()
    bridge.evaluate = evaluate
    return bridge


def _count_reservations(monkeypatch: pytest.MonkeyPatch) -> list:
    """throttle.reserve sahtesi: her sıra hemen gelir (bütçe sınır değil); ayırmalar sayılır."""
    calls: list = []

    def reserve() -> float:
        calls.append(1)
        return 0.0

    monkeypatch.setattr(throttle, "reserve", reserve)
    return calls


def _browser_first(monkeypatch: pytest.MonkeyPatch, bridge: cs.BrowserBridge, config: dict) -> None:
    """İstek katmanı önce-tarayıcı modunda ve verilen köprüyle çalışır."""
    monkeypatch.setattr(cs.BrowserBridge, "get_instance", classmethod(lambda cls, *a, **k: bridge))
    monkeypatch.setattr(transport, "_browser_first_until", time.monotonic() + 60)
    monkeypatch.setattr(transport, "_get_runtime_request_config", lambda: config)
    monkeypatch.setattr(transport, "_get_proxy_config", lambda: (False, ""))


def test_a_cancelled_async_caller_stops_waiting_for_a_page_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    _count_reservations(monkeypatch)
    release, evaluated = threading.Event(), []
    bridge = _hanging_bridge(release, evaluated)
    stop_at = time.monotonic() + 0.1
    try:
        with request_context(cancel=lambda: time.monotonic() >= stop_at):
            started = time.monotonic()
            with pytest.raises(FetchCancelled):
                asyncio.run(cs._run_on_background_loop(bridge.fetch_json("/event/1"), 10.0, cancellable=True))
            elapsed = time.monotonic() - started
    finally:
        release.set()
    assert elapsed < 0.1 + 3 * cs._CANCEL_CHECK_SECONDS + _SLACK  # 10 sn değil
    assert len(evaluated) == 1  # durdurmadan önce gönderilmişti


def test_a_stopped_download_on_the_async_bridge_path_returns_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uçtan uca: önce-tarayıcı modundaki indirme isteği, sayfanın fetch()'i sürerken durdurulunca hemen biter."""
    _count_reservations(monkeypatch)
    release, evaluated = threading.Event(), []
    _browser_first(monkeypatch, _hanging_bridge(release, evaluated), CFG)
    session = AsyncMock()
    stop_at = time.monotonic() + 0.1
    try:
        with request_context(cancel=lambda: time.monotonic() >= stop_at):
            started = time.monotonic()
            with pytest.raises(FetchCancelled):
                asyncio.run(transport.make_api_request_async(session, "/event/1"))
            elapsed = time.monotonic() - started
    finally:
        release.set()
    assert elapsed < 0.1 + 3 * cs._CANCEL_CHECK_SECONDS + _SLACK  # REQUEST_TIMEOUT (120 sn) değil
    assert len(evaluated) == 1 and session.get.await_count == 0  # curl'e de düşmedi


def test_without_the_flag_the_async_caller_still_waits_for_the_result() -> None:
    async def slow() -> str:
        await asyncio.sleep(0.3)
        return "done"

    with request_context(cancel=lambda: True):
        assert asyncio.run(cs._run_on_background_loop(slow(), 5.0)) == "done"


def test_a_cancelled_async_caller_still_gets_an_answer_that_arrives_at_once() -> None:
    """İptalden sonraki kısa süre içinde gelen yanıt atılmaz (sync yoldaki _run_sync ile aynı kural)."""

    async def answer_soon() -> str:
        await asyncio.sleep(cs._CANCEL_CHECK_SECONDS + 0.1)
        return "data"

    with request_context(cancel=lambda: True):
        assert asyncio.run(cs._run_on_background_loop(answer_soon(), 5.0, cancellable=True)) == "data"


def test_a_cancelled_async_caller_leaves_the_shared_solve_running_for_the_others() -> None:
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
            asyncio.run(cs._run_on_background_loop(bridge.solve_challenge(), 10.0, cancellable=True))
    # Öteki bekleyen (iptal edilmemiş iş) aynı çözümün sonucunu alır
    release.set()
    assert asyncio.run(cs._run_on_background_loop(bridge.solve_challenge(), 10.0, cancellable=True)) == "jwt"
    assert finished.is_set()


@pytest.mark.parametrize("sync", [True, False])
def test_no_bridge_request_is_sent_after_a_stop_even_when_its_slot_is_due(
        monkeypatch: pytest.MonkeyPatch, sync: bool) -> None:
    """
    Satır 92: köprü bütçeden sıra ayırmadan önce iptale bakar. Bütçe sınır değilken (sıra hemen gelir) durdurulmuş
    işin köprüdeki isteği sıra ayırmaz ve gönderilmez. Eskiden bu istek giderdi.
    """
    reservations = _count_reservations(monkeypatch)
    release, evaluated = threading.Event(), []
    release.set()
    bridge = _hanging_bridge(release, evaluated)
    with request_context(cancel=lambda: True):
        with pytest.raises(FetchCancelled):
            if sync:
                cs._run_sync(bridge.fetch_json("/event/1"), 5.0, cancellable=True)
            else:
                asyncio.run(cs._run_on_background_loop(bridge.fetch_json("/event/1"), 5.0, cancellable=True))
    assert evaluated == [] and reservations == []


def test_the_shared_solve_still_takes_its_slot_after_the_starter_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ortak çözümün doğrulama isteği tek bir çağıranın iptaline bakmaz: sırasını ayırır ve gider."""
    reservations = _count_reservations(monkeypatch)
    bridge = _bridge()

    async def solve() -> str:
        await cs._wait_for_slot()  # _api_unlocked'ın doğrulama isteği
        return "jwt"

    bridge._solve_challenge = solve
    with request_context(cancel=lambda: True):
        assert cs._run_sync(bridge.solve_challenge(), 5.0) == "jwt"
    assert reservations == [1]


@pytest.mark.parametrize("sync", [True, False])
def test_a_browser_first_answer_that_arrives_during_a_stop_is_returned(
        monkeypatch: pytest.MonkeyPatch, sync: bool) -> None:
    """
    Satır 91: köprünün yanıtından sonraki bekleme iptalle kesilirse veri atılmaz, curl yolundaki gibi döndürülür;
    iptal bir sonraki istekte işlenir. Eskiden bu bekleme FetchCancelled fırlatıyor, yanıt kayboluyordu.
    """
    answered: list = []

    async def evaluate(script: str, arg: object = None) -> dict:
        answered.append(arg)
        return _OK

    _count_reservations(monkeypatch)
    bridge = _bridge()
    bridge.evaluate = evaluate
    _browser_first(monkeypatch, bridge, {**CFG, "wait_time_min": 30})
    session = AsyncMock()

    def request(path: str) -> object:
        if sync:
            return transport.make_api_request(path)
        return asyncio.run(transport.make_api_request_async(session, path))

    with request_context(cancel=lambda: bool(answered)):
        started = time.monotonic()
        assert request("/event/1") == {"a": 1}
        assert time.monotonic() - started < 1.0 + _SLACK  # 30 sn'lik bekleme kesildi
        with pytest.raises(FetchCancelled):  # iptal bir sonraki istekte işlenir
            request("/event/2")
    assert len(answered) == 1 and session.get.await_count == 0
