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
