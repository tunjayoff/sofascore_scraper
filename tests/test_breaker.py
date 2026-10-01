"""
Devre kesici (src/breaker.py): kuralları, köprü sağlığıyla eşgüdümü ve istek katmanıyla bağı.

İş başına tek kesici vardır; istek katmanı (src/utils.py) her isteğin SON halini ona bildirir ve
kesici açıkken yeni istek göndermez. Aşamaların kesiciye bakışı tests/test_breaker_phases.py'de.

Gerçek ağ yok: curl taşıyıcısı (cffi_requests.get / AsyncSession.get) sahte.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.utils as utils
from src import breaker as request_breaker
from src import bridge_health
from src.breaker import CircuitBreaker
from src.exceptions import (APIError, CircuitOpenError, DataParsingError, NetworkError, RateLimitError,
                            ResourceNotFoundError)

CFG = {"max_retries": 3, "request_timeout": 5, "wait_time_min": 0, "wait_time_max": 0}


class Resp:
    def __init__(self, code: int, body: Any = None, text: str = ""):
        self.status_code = code
        self.reason = "x"
        self.headers: Dict[str, str] = {}
        self._body = body
        self.text = text

    def json(self):
        return self._body


@contextlib.contextmanager
def _request_layer():
    """Gerçek istek katmanı, beklemesiz ve proxy'siz."""
    async def no_asleep(_sec):
        return None

    with patch.object(utils, "_get_runtime_request_config", return_value=CFG), \
            patch.object(utils, "_get_proxy_config", return_value=(False, "")), \
            patch.object(utils, "_asleep", side_effect=no_asleep), \
            patch.object(utils, "_sleep", side_effect=lambda _sec: None):
        yield


@contextlib.contextmanager
def _active(breaker: CircuitBreaker):
    token = request_breaker.activate(breaker)
    try:
        yield breaker
    finally:
        request_breaker.deactivate(token)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("IGNORE_RATE_LIMIT", raising=False)


def _never_blocked(_consecutive: int, _since: float) -> bool:
    return False


def _breaker(consecutive=3, ratio=2.0, server_errors=1000) -> CircuitBreaker:
    return CircuitBreaker(consecutive, ratio, server_errors, bridge_blocked=_never_blocked)


# --- kesicinin kuralları -------------------------------------------------------------------

def test_trips_after_consecutive_failures():
    b = _breaker(consecutive=3)
    b.record("403")
    b.record("429")
    assert not b.tripped
    b.record("timeout")
    assert b.tripped


@pytest.mark.parametrize("answer", ["ok", "404"])
def test_an_answer_from_the_site_ends_the_streak(answer):
    """404 de yanıttır: site bizi engellemiyor, kaynak yok."""
    b = _breaker(consecutive=3)
    for _ in range(10):
        b.record("403")
        b.record("403")
        b.record(answer)
    assert not b.tripped and b.consecutive_failures == 0


def test_ratio_rule_needs_more_than_fifty_requests():
    b = _breaker(consecutive=1000, ratio=0.9)
    for n in range(50):
        b.record("ok" if n % 20 == 0 else "429")  # %94 başarısız, ama henüz 50 istek
    assert not b.tripped
    b.record("429")
    assert b.tripped and b.reason() == "429"


def test_server_error_rule_counts_only_consecutive_5xx():
    b = _breaker(consecutive=1000, server_errors=3)
    b.record("5xx")
    b.record("5xx")
    b.record("403")  # 5xx serisini böler
    b.record("5xx")
    b.record("5xx")
    assert not b.tripped
    b.record("5xx")
    assert b.tripped and b.reason() == "5xx"


def test_ignore_rate_limit_disables_the_breaker(monkeypatch):
    monkeypatch.setenv("IGNORE_RATE_LIMIT", "true")
    b = _breaker(consecutive=2)
    for _ in range(10):
        b.record("403")
    assert not b.tripped and b.failures == 10


def test_reason_is_the_most_common_block_like_failure():
    b = _breaker(consecutive=1000)
    for kind in ("403", "429", "429", "timeout", "timeout", "timeout"):
        b.record(kind)
    assert b.reason() == "429"
    assert _breaker().reason() == "other"


def test_thresholds_are_read_from_config_and_fall_back_to_defaults():
    cfg = MagicMock()
    cfg.get_rate_limit_threshold_consecutive.return_value = 2
    cfg.get_rate_limit_threshold_ratio.return_value = 0.9
    cfg.get_server_error_threshold_consecutive.return_value = 50
    b = CircuitBreaker.from_config(cfg)
    b.record("403")
    b.record("403")
    assert b.tripped

    unconfigured = CircuitBreaker.from_config(MagicMock())  # sayı dönmeyen sahte ayar: varsayılan 20
    for _ in range(19):
        unconfigured.record("403")
    assert not unconfigured.tripped
    unconfigured.record("403")
    assert unconfigured.tripped


def test_the_same_exception_is_counted_once():
    """İstek katmanı hatayı bildirir; aynı hata üst katmana ulaşınca yeniden sayılmaz."""
    b = _breaker(consecutive=3)
    err = APIError("blocked", status_code=403)
    b.record_exception(err)
    b.record_exception(err)
    b.record_exception(err)
    assert b.failures == 1 and not b.tripped


@pytest.mark.parametrize("error,kind", [
    (ResourceNotFoundError("x"), "404"),
    (APIError("x", status_code=403), "403"),
    (RateLimitError(status_code=429, url="/x"), "429"),
    (RateLimitError(status_code=503, url="/x"), "5xx"),
    (APIError("x", status_code=502), "5xx"),
    (NetworkError("curl: (28) Operation timed out"), "timeout"),
    (NetworkError("curl: (7) Failed to connect"), "network"),
    (DataParsingError("bad json"), "parse"),
    (CircuitOpenError("/x"), "breaker"),
    (ValueError("boom"), "other"),
])
def test_failure_kind(error, kind):
    assert request_breaker.failure_kind(error) == kind


def test_open_breaker_is_not_fed_by_its_own_refusals():
    b = _breaker(consecutive=1)
    b.record("403")
    attempts = b.attempts
    b.record_exception(CircuitOpenError("/x"))
    assert b.attempts == attempts


# --- köprü sağlığıyla eşgüdüm --------------------------------------------------------------

def _block_bridge(monkeypatch, failures: int = 10) -> None:
    monkeypatch.setenv("BRIDGE_BLOCKED_MIN_SECONDS", "0")
    for _ in range(failures):
        bridge_health.record_failure(bridge_health.KIND_CHALLENGE, "HTTP 403")
    assert bridge_health.snapshot()["state"] == bridge_health.BLOCKED


def test_blocked_bridge_trips_the_breaker_early(monkeypatch):
    """Köprü bu iş sırasında "blocked" olduysa ve işin son istekleri 403 ile bittiyse 20 istek beklenmez."""
    b = CircuitBreaker(20, 2.0, 1000)
    _block_bridge(monkeypatch)
    b.record("403")
    b.record("403")
    assert not b.tripped
    b.record("403")  # köprünün "degraded" eşiği (3) kadar art arda başarısız istek
    assert b.tripped and b.reason() == "403"


def test_blocked_bridge_does_not_stop_a_job_whose_requests_succeed(monkeypatch):
    b = CircuitBreaker(20, 2.0, 1000)
    _block_bridge(monkeypatch)
    for _ in range(30):
        b.record("ok")
        b.record("403")  # tek tük 403 (ör. kısıtlı kaynak): seri oluşmuyor
    assert not b.tripped


def test_bridge_blocked_before_the_job_does_not_trip_early(monkeypatch):
    """Köprü durumu kendi kendine düzelmez: işten önce kalmış bir "blocked" tek başına işi durdurmaz."""
    _block_bridge(monkeypatch)
    b = CircuitBreaker(20, 2.0, 1000, clock=lambda: time.time() + 60)  # iş, köprünün son reddinden sonra başladı
    for _ in range(10):
        b.record("403")
    assert not b.tripped
    for _ in range(10):
        b.record("403")
    assert b.tripped  # kendi eşiği (art arda 20) yine geçerli


def test_blocked_bridge_does_not_trip_on_other_failures(monkeypatch):
    b = CircuitBreaker(20, 2.0, 1000)
    _block_bridge(monkeypatch)
    for _ in range(5):
        b.record("timeout")
    assert not b.tripped


# --- istek katmanı kesiciyi besler ve ona uyar ---------------------------------------------

def test_sync_request_reports_its_final_outcome():
    with _request_layer(), _active(_breaker(consecutive=100)) as b:
        with patch.object(utils.cffi_requests, "get", return_value=Resp(200, {"ok": 1})):
            assert utils.make_api_request("/a") == {"ok": 1}
        assert (b.attempts, b.failures) == (1, 0)
        with patch.object(utils.cffi_requests, "get", return_value=Resp(403, text="no")) as get:
            assert utils.make_api_request("/b") is None
        # Üç deneme tek istek sayılır: kesici isteğin SON halini görür
        assert get.call_count == 3 and (b.attempts, b.failures) == (2, 1)
        with patch.object(utils.cffi_requests, "get", return_value=Resp(404)):
            assert utils.make_api_request("/c") is None
        assert (b.attempts, b.failures, b.consecutive_failures) == (3, 1, 0)
        assert b.counts() == {"403": 1, "404": 1}


@pytest.mark.parametrize("response,error", [
    (Resp(404), ResourceNotFoundError),
    (Resp(429), RateLimitError),
    (Resp(403, text="no"), APIError),
    (Resp(500), APIError),
])
def test_sync_request_can_raise_typed_errors(response, error):
    with _request_layer(), patch.object(utils.cffi_requests, "get", return_value=response):
        with pytest.raises(error):
            utils.make_api_request("/x", raise_on_failure=True)
        assert utils.make_api_request("/x") is None  # varsayılan davranış değişmedi


def test_sync_request_raises_network_and_parsing_errors():
    with _request_layer(), patch.object(utils.cffi_requests, "get", side_effect=RuntimeError("curl: (7) boom")):
        with pytest.raises(NetworkError):
            utils.make_api_request("/x", raise_on_failure=True)

    bad = Resp(200)
    bad.json = MagicMock(side_effect=ValueError("Expecting value"))
    with _request_layer(), patch.object(utils.cffi_requests, "get", return_value=bad):
        with pytest.raises(DataParsingError):
            utils.make_api_request("/x", raise_on_failure=True)


def test_open_breaker_sends_no_sync_request_and_reserves_no_budget():
    b = _breaker(consecutive=1)
    b.record("403")
    with _request_layer(), _active(b), \
            patch.object(utils.cffi_requests, "get", return_value=Resp(200, {"ok": 1})) as get, \
            patch.object(utils.throttle, "reserve", return_value=0.0) as reserve:
        assert utils.make_api_request("/x") is None
        with pytest.raises(CircuitOpenError):
            utils.make_api_request("/x", raise_on_failure=True)
    assert get.call_count == 0 and reserve.call_count == 0


def test_breaker_opening_mid_request_stops_the_retries():
    b = _breaker(consecutive=1)

    def get(*_a, **_kw):
        b.record("403")  # başka bir istek bu sırada devreyi kesti
        return Resp(403, text="no")

    with _request_layer(), _active(b), patch.object(utils.cffi_requests, "get", side_effect=get) as mock:
        assert utils.make_api_request("/x") is None
    assert mock.call_count == 1  # 3 deneme hakkı vardı; ikinci deneme gönderilmedi


def test_async_request_reports_its_final_outcome():
    async def run():
        b = _breaker(consecutive=100)
        with _active(b):
            session = MagicMock()
            session.get = AsyncMock(side_effect=[Resp(200, {"ok": 1}), Resp(429), Resp(404)])
            assert await utils.make_api_request_async(session, "/a") == {"ok": 1}
            with pytest.raises(RateLimitError):
                await utils.make_api_request_async(session, "/b", max_retries=1)
            with pytest.raises(ResourceNotFoundError):
                await utils.make_api_request_async(session, "/c")
        return b

    with _request_layer():
        b = asyncio.run(run())
    assert (b.attempts, b.failures, b.consecutive_failures) == (3, 1, 0)
    assert b.counts() == {"429": 1, "404": 1}


def test_open_breaker_sends_no_async_request():
    async def run():
        b = _breaker(consecutive=1)
        b.record("403")
        session = MagicMock()
        session.get = AsyncMock(return_value=Resp(200, {"ok": 1}))
        with _active(b), patch.object(utils.throttle, "reserve", return_value=0.0) as reserve:
            with pytest.raises(CircuitOpenError):
                await utils.make_api_request_async(session, "/x")
        return session.get.await_count, reserve.call_count

    with _request_layer():
        assert asyncio.run(run()) == (0, 0)


def test_requests_outside_a_job_are_not_counted():
    """Kesici işin bağlamındadır: tekil istekler (lig arama, tek maç çekme) onu görmez."""
    assert request_breaker.current() is None
    with _request_layer(), patch.object(utils.cffi_requests, "get", return_value=Resp(403, text="no")):
        for _ in range(30):
            assert utils.make_api_request("/x") is None


def test_scope_reuses_the_job_breaker():
    cfg = MagicMock()
    with request_breaker.scope(cfg) as outer:
        with request_breaker.scope(cfg) as inner:
            assert inner is outer
    assert request_breaker.current() is None
