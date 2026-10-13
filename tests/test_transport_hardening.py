"""
İstek katmanının (sofascore_scraper/client/transport.py) c projesinden geri taşınan üretim düzeltmeleri (S1).

c projesi (sofascore_site/rest.py) bu katmanı buradan taşıdı ve üretim benzeri koşularda (1-5) düzeltti; düzeltmeler
burada aynı yöntemle çalışır. Ağ yok: curl ve köprü sahtedir.
"""
from __future__ import annotations

import datetime as dt
from email.utils import format_datetime

import pytest

from sofascore_scraper.client import transport


# --- B11: Retry-After ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, 9),
        ("", 9),
        ("7", 7),
        ("0", 1),
        ("120", 120),
        ("121", 120),  # eskiden 121 sn beklenirdi
        ("3600", 120),  # eskiden bir saat
        ("soon", 9),
    ],
)
def test_an_integer_retry_after_is_capped_like_the_date_form(header, expected):
    assert transport._parse_retry_after_seconds(header, 9) == expected


def test_a_date_retry_after_stays_capped():
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
    assert transport._parse_retry_after_seconds(format_datetime(later, usegmt=True), 9) == 120
    earlier = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
    assert transport._parse_retry_after_seconds(format_datetime(earlier, usegmt=True), 9) == 1


# --- B2: curl'ün ya da köprünün her yanıtı köprünün başarısız çözüm serisini bitirir ----------------------


class _Streak:
    """Etkin köprünün yerine: seriyi bitiren çağrıları sayar."""

    def __init__(self) -> None:
        self.resets = 0

    def reset_solve_streak(self) -> None:
        self.resets += 1


class _Resp:
    def __init__(self, status: int, data=None, text: str = "") -> None:
        self.status_code = status
        self._data = data
        self.text = text
        self.reason = "x"
        self.headers: dict = {}

    def json(self):
        return self._data


_CFG = {"max_retries": 1, "request_timeout": 5, "wait_time_min": 0, "wait_time_max": 0}


def _patched_curl(monkeypatch, *responses):
    from unittest.mock import MagicMock

    get = MagicMock(side_effect=list(responses))
    monkeypatch.setattr(transport.cffi_requests, "get", get)
    monkeypatch.setattr(transport, "_get_runtime_request_config", lambda: dict(_CFG))
    monkeypatch.setattr(transport, "_get_proxy_config", lambda: (False, ""))
    monkeypatch.setattr(transport.throttle, "reserve", lambda: 0.0)
    return get


def test_a_curl_answer_ends_the_bridges_run_of_failed_solves(monkeypatch):
    from sofascore_scraper.client import bridge as cs

    streak = _Streak()
    monkeypatch.setattr(cs.BrowserBridge, "_instance", streak)
    _patched_curl(monkeypatch, _Resp(200, {"a": 1}), _Resp(404), _Resp(500))
    assert transport.make_api_request("/event/1") == {"a": 1}
    assert streak.resets == 1
    assert transport.make_api_request("/event/2") is None  # 404: bir yanıt
    assert streak.resets == 2
    assert transport.make_api_request("/event/3") is None  # 500: yanıt değil
    assert streak.resets == 2


def test_without_a_bridge_an_answer_needs_none(monkeypatch):
    from sofascore_scraper.client import bridge as cs

    monkeypatch.setattr(cs.BrowserBridge, "_instance", None)
    _patched_curl(monkeypatch, _Resp(200, {"a": 1}))
    assert transport.make_api_request("/event/1") == {"a": 1}
    assert cs.BrowserBridge._instance is None  # köprü kurulmadı


# --- B1: curl'ün taşıdığı token'ın reddi de köprüye gider -------------------------------------------------

JWT_REFUSED = '{"error": {"code": 403, "reason": "jwt:time-out-of-bounds" }}'


def test_a_curl_403_rejecting_the_token_goes_to_the_browser(monkeypatch):
    from unittest.mock import MagicMock, patch

    _patched_curl(monkeypatch, _Resp(403, text=JWT_REFUSED))
    browser = MagicMock(return_value={"a": 1})
    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", browser):
        assert transport.make_api_request("/event/1") == {"a": 1}
    assert browser.call_count == 1


def test_an_async_curl_403_rejecting_the_token_goes_to_the_browser(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.setattr(transport, "_get_runtime_request_config", lambda: dict(_CFG))
    monkeypatch.setattr(transport, "_get_proxy_config", lambda: (False, ""))
    monkeypatch.setattr(transport.throttle, "reserve", lambda: 0.0)
    session = MagicMock()
    session.get = AsyncMock(return_value=_Resp(403, text=JWT_REFUSED))
    browser = AsyncMock(return_value={"a": 1})
    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser", browser):
        assert asyncio.run(transport.make_api_request_async(session, "/event/1")) == {"a": 1}
    assert browser.await_count == 1


# --- B4: kapı. Çözüm soğurken hiçbir şey gönderilmez; uyarıdan sonra aynı anda tek yoklama (c: 8b0e05d, 68cfba4,
# 51c23a1, ba6678f) ------------------------------------------------------------------------------------------------

CHALLENGE = '{"error": {"code": 403, "reason": "challenge"}}'


class _FakeBridge:
    """Etkin köprünün yerine: soğuma süresi ve seriyi bitiren çağrılar."""

    def __init__(self) -> None:
        self.cooldown = 0.0
        self.resets = 0
        self.solve_failures = 0

    def solve_cooldown_left(self) -> float:
        return self.cooldown

    def reset_solve_streak(self) -> None:
        self.resets += 1


@pytest.fixture
def fake_bridge(monkeypatch):
    from sofascore_scraper.client import bridge as cs

    fb = _FakeBridge()
    monkeypatch.setattr(cs.BrowserBridge, "_instance", fb)
    return fb


def _job_breaker():
    from sofascore_scraper.breaker import CircuitBreaker

    return CircuitBreaker(consecutive=1000, ratio=1.0, server_errors=1000, bridge_blocked=lambda *_: False)


def test_nothing_is_sent_while_a_solve_cools_down(monkeypatch, fake_bridge):
    from unittest.mock import MagicMock, patch

    from sofascore_scraper import bridge_health
    from sofascore_scraper.client import request_context
    from sofascore_scraper.exceptions import APIError

    get = _patched_curl(monkeypatch)
    browser = MagicMock(return_value={"a": 1})
    fake_bridge.cooldown = 120.0
    job = _job_breaker()
    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", browser), request_context(breaker=job):
        with pytest.raises(APIError) as caught:
            transport.make_api_request("/event/1", raise_errors=True)
    assert caught.value.status_code == 403 and "not sent" in str(caught.value)
    assert get.call_count == 0 and browser.call_count == 0
    assert transport.gate_status()["challenge_skipped"] == 1
    assert job.counts() == {"403": 1}  # gönderilmiş gibi sayılır
    assert bridge_health.snapshot()["last_error"]["kind"] == bridge_health.KIND_CHALLENGE
    assert bridge_health.requests()["challenge_skipped"] == 1


def test_a_request_under_way_stops_retrying_once_the_solve_fails(monkeypatch, fake_bridge):
    from unittest.mock import patch

    from sofascore_scraper.exceptions import APIError

    get = _patched_curl(monkeypatch, _Resp(403, text=CHALLENGE), _Resp(200, {"a": 1}))
    monkeypatch.setattr(transport, "_get_runtime_request_config",
                        lambda: {**_CFG, "max_retries": 3})
    sleeps = []
    monkeypatch.setattr(transport, "_sleep", sleeps.append)

    def browser(url):  # bu isteğin çözümü başarısız oldu: bekleme başladı
        fake_bridge.cooldown = 180.0
        return None

    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", side_effect=browser):
        with pytest.raises(APIError) as caught:
            transport.make_api_request("/event/1", raise_errors=True)
    assert "not sent" in str(caught.value)
    assert get.call_count == 1 and sleeps == []  # geri çekilme ve ikinci deneme yok
    status = transport.gate_status()
    assert (status["challenges"], status["challenge_failures"], status["challenge_failed_requests"]) == (1, 1, 1)


def test_three_failed_requests_hold_the_gate_until_one_probe_is_answered(monkeypatch, fake_bridge, caplog):
    from unittest.mock import patch

    from sofascore_scraper.exceptions import APIError

    get = _patched_curl(monkeypatch, *[_Resp(403, text=CHALLENGE)] * 3)
    browser_calls = []
    nested = []

    def browser(url):
        browser_calls.append(url)
        if len(browser_calls) == 4:
            # Yoklama dışarıdayken gelen başka bir istek gönderilmez
            with pytest.raises(APIError, match="not sent"):
                transport.make_api_request("/event/other", raise_errors=True)
            nested.append(1)
            return {"ok": 1}
        return None

    with caplog.at_level("INFO"), \
            patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", side_effect=browser):
        for i in range(3):
            assert transport.make_api_request(f"/event/{i}") is None
        errors = [r for r in caplog.records if "keeps failing" in r.getMessage()]
        assert len(errors) == 1 and errors[0].levelname == "ERROR"
        # Kapı tutuluyor: yoklama tek istek, tarayıcının (curl'e hiç uğramaz)
        assert transport.make_api_request("/event/probe") == {"ok": 1}
    assert get.call_count == 3 and nested == [1]
    assert "SofaScore answers again after 4 request(s) failed on the challenge" in caplog.text
    assert transport.gate_status()["consecutive_challenge_failed_requests"] == 0
    assert fake_bridge.resets >= 1


def test_a_probe_meeting_a_403_no_solve_answers_spaces_the_next(monkeypatch, fake_bridge):
    from unittest.mock import patch

    from sofascore_scraper.client import bridge as cs
    from sofascore_scraper.exceptions import APIError

    _patched_curl(monkeypatch, _Resp(403, text="<html>blocked</html>"))
    transport._gate.consecutive_challenge_failed_requests = transport.CHALLENGE_FAILURES_ALERT

    def browser(url):
        out = cs._outcome.get()
        out.status, out.text = 403, "<html>blocked</html>"  # sayfa çözümün yanıtlamadığı bir 403 aldı
        return None

    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", side_effect=browser):
        assert transport.make_api_request("/event/probe") is None
        assert transport._probe_paused_s() > 25
        with pytest.raises(APIError, match="not sent"):
            transport.make_api_request("/event/next", raise_errors=True)
        transport._gate.probe_not_before = 0.0  # 30 sn geçti
        transport.make_api_request("/event/next")
    assert transport._gate.unsolvable_probes == 2
    assert 55 < transport._probe_paused_s() <= 60  # art arda iki katı


def test_an_async_request_in_a_cooldown_is_not_sent(monkeypatch, fake_bridge):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from sofascore_scraper.exceptions import APIError

    monkeypatch.setattr(transport, "_get_runtime_request_config", lambda: dict(_CFG))
    monkeypatch.setattr(transport, "_get_proxy_config", lambda: (False, ""))
    session = MagicMock()
    session.get = AsyncMock(return_value=_Resp(200, {"a": 1}))
    browser = AsyncMock(return_value={"a": 1})
    fake_bridge.cooldown = 60.0
    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser", browser):
        with pytest.raises(APIError, match="not sent"):
            asyncio.run(transport.make_api_request_async(session, "/event/1"))
        monkeypatch.setattr(transport, "_browser_first_until", float("inf"))
        with pytest.raises(APIError, match="not sent"):
            asyncio.run(transport.make_api_request_async(session, "/event/2"))
    assert session.get.await_count == 0 and browser.await_count == 0


def test_an_async_request_stops_retrying_once_the_solve_fails(monkeypatch, fake_bridge):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from sofascore_scraper.exceptions import APIError

    monkeypatch.setattr(transport, "_get_runtime_request_config", lambda: {**_CFG, "max_retries": 3})
    monkeypatch.setattr(transport, "_get_proxy_config", lambda: (False, ""))
    monkeypatch.setattr(transport.throttle, "reserve", lambda: 0.0)
    session = MagicMock()
    session.get = AsyncMock(return_value=_Resp(403, text=CHALLENGE))

    async def browser(url):
        fake_bridge.cooldown = 180.0
        return None

    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser", side_effect=browser):
        with pytest.raises(APIError, match="not sent"):
            asyncio.run(transport.make_api_request_async(session, "/event/1"))
    assert session.get.await_count == 1


def test_the_bridge_reports_an_unsent_fetch_and_the_request_layer_does_not_count_it_twice(monkeypatch, fake_bridge):
    from unittest.mock import patch

    from sofascore_scraper import bridge_health
    from sofascore_scraper.client import bridge as cs
    from sofascore_scraper.exceptions import APIError

    monkeypatch.setattr(transport, "_browser_first_until", float("inf"))
    _patched_curl(monkeypatch)

    def browser(url):  # köprü: sıra geldiğinde çözüm soğuyordu, gönderilmedi
        bridge_health.record_failure(bridge_health.KIND_CHALLENGE, "HTTP 403: unsent")
        out = cs._outcome.get()
        out.status, out.text, out.sent = 403, cs._UNSENT_CHALLENGE, False
        return None

    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", side_effect=browser):
        with pytest.raises(APIError, match="not sent"):
            transport.make_api_request("/event/1", raise_errors=True)
    assert bridge_health.snapshot()["consecutive_failures"] == 1
    assert transport.gate_status()["challenge_skipped"] == 1


# --- A4: ulaşılamayan site ağın başarısızlığıdır, challenge'ın değil ------------------------------------


def test_a_cooldown_after_an_unreached_solve_holds_requests_as_the_networks(monkeypatch, fake_bridge):
    from unittest.mock import MagicMock, patch

    from sofascore_scraper import bridge_health
    from sofascore_scraper.exceptions import NetworkError

    fake_bridge.cooldown = 30.0
    fake_bridge.solve_unreached = True
    get = _patched_curl(monkeypatch)
    browser = MagicMock(return_value={"a": 1})
    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", browser):
        with pytest.raises(NetworkError, match="could not reach"):
            transport.make_api_request("/event/1", raise_errors=True)
    assert get.call_count == 0 and browser.call_count == 0
    status = transport.gate_status()
    assert (status["transport_skipped"], status["challenge_skipped"]) == (1, 0)
    assert status["transport_failed_requests"] == 1 and status["challenge_failed_requests"] == 0
    assert bridge_health.snapshot()["consecutive_failures"] == 0  # challenge sayılmadı


def test_three_requests_without_any_answer_are_one_error_and_an_answer_ends_it(monkeypatch, fake_bridge, caplog):
    from unittest.mock import MagicMock, patch

    _patched_curl(monkeypatch, *[RuntimeError("curl: (6) Could not resolve host")] * 3, _Resp(500))
    browser = MagicMock(return_value=None)
    with caplog.at_level("INFO"), patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", browser):
        for i in range(3):
            assert transport.make_api_request(f"/event/{i}") is None
        errors = [r for r in caplog.records if "cannot be reached" in r.getMessage()]
        assert len(errors) == 1 and errors[0].levelname == "ERROR"
        assert transport.gate_status()["consecutive_transport_failed_requests"] == 3
        assert transport.make_api_request("/event/3") is None  # 500: bir yanıttır
    assert "SofaScore answers again after 3 request(s) got no answer" in caplog.text
    assert transport.gate_status()["consecutive_transport_failed_requests"] == 0
    from sofascore_scraper import bridge_health

    assert bridge_health.requests()["transport_failed_requests"] == 3


def test_a_browser_that_reached_nothing_after_curls_challenge_is_a_transport_skip_in_the_cooldown(
    monkeypatch, fake_bridge
):
    from unittest.mock import patch

    from sofascore_scraper.client import bridge as cs
    from sofascore_scraper.exceptions import NetworkError

    _patched_curl(monkeypatch, _Resp(403, text=CHALLENGE))
    monkeypatch.setattr(transport, "_get_runtime_request_config", lambda: {**_CFG, "max_retries": 3})

    def browser(url):  # sayfa da ulaşamadı, çözümü siteye ulaşmadı: 30 sn
        out = cs._outcome.get()
        out.status, out.sent = 0, False
        fake_bridge.cooldown, fake_bridge.solve_unreached = 30.0, True
        return None

    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", side_effect=browser):
        with pytest.raises(NetworkError, match="could not reach"):
            transport.make_api_request("/event/1", raise_errors=True)
    assert transport.gate_status()["transport_skipped"] == 1


# --- B5: tarayıcının çözümle yanıtlanmayan 403'ü isteği bitirir (c: 51c23a1) ------------------------------


def _page_403(text: str):
    from sofascore_scraper.client import bridge as cs

    def browser(url):
        out = cs._outcome.get()
        out.status, out.text = 403, text
        return None

    return browser


def test_in_browser_first_a_page_403_ends_the_request_without_curl(monkeypatch, fake_bridge):
    from unittest.mock import patch

    from sofascore_scraper.exceptions import APIError

    get = _patched_curl(monkeypatch)
    monkeypatch.setattr(transport, "_browser_first_until", float("inf"))
    for text in (JWT_REFUSED, "<html>blocked</html>"):
        with patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", side_effect=_page_403(text)):
            with pytest.raises(APIError) as caught:
                transport.make_api_request("/event/1", raise_errors=True)
        assert caught.value.status_code == 403 and "not sent" not in str(caught.value)
    assert get.call_count == 0  # eskiden curl'e düşerdi: okuma başına 7 istek
    assert transport.gate_status()["challenge_failed_requests"] == 2


def test_after_curls_challenge_a_page_403_no_solve_answers_ends_the_request(monkeypatch, fake_bridge, caplog):
    from unittest.mock import patch

    from sofascore_scraper.exceptions import APIError

    get = _patched_curl(monkeypatch, *[_Resp(403, text=CHALLENGE)] * 3)
    monkeypatch.setattr(transport, "_get_runtime_request_config", lambda: {**_CFG, "max_retries": 3})
    sleeps = []
    monkeypatch.setattr(transport, "_sleep", sleeps.append)
    with caplog.at_level("WARNING"), \
            patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync",
                  side_effect=_page_403("<html>blocked</html>")):
        with pytest.raises(APIError):
            transport.make_api_request("/event/1", raise_errors=True)
    assert get.call_count == 1 and sleeps == []
    assert "403 no solve answers" in caplog.text


def test_after_curls_challenge_a_page_challenge_is_retried_as_before(monkeypatch, fake_bridge):
    from unittest.mock import patch

    get = _patched_curl(monkeypatch, _Resp(403, text=CHALLENGE), _Resp(200, {"a": 1}))
    monkeypatch.setattr(transport, "_get_runtime_request_config", lambda: {**_CFG, "max_retries": 3})
    monkeypatch.setattr(transport, "_sleep", lambda s: None)
    # Çözümün yanıtlayacağı türden bir 403 (çözüm bu kez olmadı, bekleme yok): geri çekilip yeniden denenir
    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", side_effect=_page_403(CHALLENGE)):
        assert transport.make_api_request("/event/1") == {"a": 1}
    assert get.call_count == 2


def test_an_async_page_403_in_browser_first_ends_the_request(monkeypatch, fake_bridge):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from sofascore_scraper.client import bridge as cs
    from sofascore_scraper.exceptions import APIError

    monkeypatch.setattr(transport, "_get_runtime_request_config", lambda: dict(_CFG))
    monkeypatch.setattr(transport, "_get_proxy_config", lambda: (False, ""))
    monkeypatch.setattr(transport, "_browser_first_until", float("inf"))
    session = MagicMock()
    session.get = AsyncMock(return_value=_Resp(200, {"a": 1}))

    async def browser(url):
        out = cs._outcome.get()
        out.status, out.text = 403, JWT_REFUSED
        return None

    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser", side_effect=browser):
        with pytest.raises(APIError):
            asyncio.run(transport.make_api_request_async(session, "/event/1"))
    assert session.get.await_count == 0


# --- B6: önce-tarayıcı bitince tek curl denemesi; taşıma hatası ve tarayıcının 404'ü pencereyi açar -------------
# (c: e8d1986, 09b3830)


def test_as_browser_first_lapses_one_request_tries_curl_and_the_others_stay_on_the_browser(monkeypatch, fake_bridge):
    from unittest.mock import patch

    _patched_curl(monkeypatch, _Resp(403, text=CHALLENGE), _Resp(403, text=CHALLENGE))
    browser_urls = []
    nested = []

    def browser(url):
        browser_urls.append(url)
        n = len(browser_urls)
        if n == 2:
            # curl'ü deneyen istek dışarıdayken gelen istek tarayıcıda kalır
            nested.append(transport._browser_first())
            assert transport.make_api_request("/event/other") == {"a": 3}
        return {"a": n}

    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", side_effect=browser):
        assert transport.make_api_request("/event/1") == {"a": 1}  # curl challenge → tarayıcı, pencere açılır
        assert transport._curl_challenged
        monkeypatch.setattr(transport, "_browser_first_until", 0.0)  # pencere bitti
        assert transport.make_api_request("/event/2") == {"a": 2}  # tek istek curl'ü dener: yine challenge
    assert nested == [True]
    assert transport.cffi_requests.get.call_count == 2
    assert browser_urls[-1].endswith("/event/other")
    assert transport._browser_first() and not transport._curl_probe_out


def test_any_other_curl_answer_ends_the_curl_probe_hold(monkeypatch, fake_bridge):
    from unittest.mock import MagicMock, patch

    _patched_curl(monkeypatch, _Resp(403, text=CHALLENGE), _Resp(200, {"curl": 1}))
    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", MagicMock(return_value={"a": 1})):
        transport.make_api_request("/event/1")
        monkeypatch.setattr(transport, "_browser_first_until", 0.0)
        assert transport.make_api_request("/event/2") == {"curl": 1}
    assert not transport._curl_challenged and not transport._browser_first()


def test_a_transport_error_opens_browser_first_when_the_browser_answered_lately(monkeypatch, fake_bridge):
    import time as _time
    from unittest.mock import MagicMock, patch

    get = _patched_curl(monkeypatch, RuntimeError("curl: (28) Resolving timed out"))
    monkeypatch.setattr(transport, "_get_runtime_request_config", lambda: {**_CFG, "max_retries": 3})
    sleeps = []
    monkeypatch.setattr(transport, "_sleep", sleeps.append)
    monkeypatch.setattr(transport, "_browser_answered_at", _time.monotonic() - 60)
    browser = MagicMock(return_value={"a": 1})
    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", browser):
        assert transport.make_api_request("/event/1") == {"a": 1}
    assert get.call_count == 1 and browser.call_count == 1
    assert sleeps == [0.0] or all(s == 0 for s in sleeps)  # geri çekilme yok (yalnızca başarı sonrası bekleme)
    assert transport._browser_first() and transport._browser_first_transport
    assert not transport._curl_challenged  # taşıma hatasının penceresi curl'ün challenge'ı hakkında bir şey söylemez


def test_without_a_recent_browser_answer_a_transport_error_backs_off_as_before(monkeypatch, fake_bridge):
    from unittest.mock import MagicMock, patch

    get = _patched_curl(monkeypatch, RuntimeError("curl: (7) Failed to connect"), _Resp(200, {"a": 1}))
    monkeypatch.setattr(transport, "_get_runtime_request_config", lambda: {**_CFG, "max_retries": 3})
    sleeps = []
    monkeypatch.setattr(transport, "_sleep", sleeps.append)
    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", MagicMock(return_value=None)) as b:
        assert transport.make_api_request("/event/1") == {"a": 1}
    assert get.call_count == 2 and b.call_count == 0 and 3 in sleeps
    assert not transport._browser_first()


def test_a_browser_that_cannot_reach_sofascore_closes_a_transport_window(monkeypatch, fake_bridge):
    import time as _time
    from unittest.mock import MagicMock, patch

    _patched_curl(monkeypatch, _Resp(200, {"curl": 1}))
    monkeypatch.setattr(transport, "_browser_answered_at", _time.monotonic())
    transport._mark_browser_first("curl cannot reach SofaScore", transport=True)
    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser_sync", MagicMock(return_value=None)):
        assert transport.make_api_request("/event/1") == {"curl": 1}
    assert not transport._browser_first()


def test_an_async_browser_404_after_curls_challenge_opens_browser_first(monkeypatch, fake_bridge):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from sofascore_scraper.exceptions import ResourceNotFoundError

    monkeypatch.setattr(transport, "_get_runtime_request_config", lambda: dict(_CFG))
    monkeypatch.setattr(transport, "_get_proxy_config", lambda: (False, ""))
    monkeypatch.setattr(transport.throttle, "reserve", lambda: 0.0)
    session = MagicMock()
    session.get = AsyncMock(return_value=_Resp(403, text=CHALLENGE))
    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser", AsyncMock(return_value={"__404__": True})):
        with pytest.raises(ResourceNotFoundError):
            asyncio.run(transport.make_api_request_async(session, "/event/1/statistics"))
    assert transport._browser_first()  # eskiden açmazdı: her sonraki okuma önce curl'ü denerdi


def test_an_async_bridge_error_in_browser_first_falls_back_to_curl(monkeypatch, fake_bridge):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.setattr(transport, "_get_runtime_request_config", lambda: dict(_CFG))
    monkeypatch.setattr(transport, "_get_proxy_config", lambda: (False, ""))
    monkeypatch.setattr(transport.throttle, "reserve", lambda: 0.0)
    monkeypatch.setattr(transport, "_browser_first_until", float("inf"))
    session = MagicMock()
    session.get = AsyncMock(return_value=_Resp(200, {"curl": 1}))
    with patch("sofascore_scraper.client.bridge.fetch_api_via_browser", AsyncMock(side_effect=RuntimeError("x"))):
        assert asyncio.run(transport.make_api_request_async(session, "/event/1")) == {"curl": 1}
