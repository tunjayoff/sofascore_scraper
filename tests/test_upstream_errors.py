"""
Engellenen / başarısız SofaScore isteği "Sonuç yok" ya da sebepsiz bir başarı olarak görünmemeli.

İstek katmanı tipli hatalar fırlatır ve web katmanı onları tipli bir nedene çevirir
(sofascore_scraper/web/upstream.py): blocked / browser / rate_limited / network / not_found / upstream.
API v1'in arama ve bağlantı denetimi yolları bu nedenleri kullanır (tests/test_api_v1_follows.py,
test_api_v1_status.py); 2.x'in `/api` lig arama ve sezon yenileme yolları 3.1'de kalktı (P30). Bütün testler
çevrimdışıdır: istek katmanı (curl ya da make_api_request) sahtedir, gerçek tarayıcı conftest tarafından engellenir.
"""
from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest

from sofascore_scraper.client import bridge as cs
from sofascore_scraper.client import transport
from sofascore_scraper import bridge_health
from sofascore_scraper.exceptions import (
    APIError,
    DataParsingError,
    NetworkError,
    RateLimitError,
    ResourceNotFoundError,
)
from sofascore_scraper.web import upstream

CFG = {"max_retries": 3, "request_timeout": 5, "wait_time_min": 0, "wait_time_max": 0}
CHALLENGE = '{"error":{"code":403,"reason":"challenge"}}'


class Resp:
    def __init__(self, code, body=None, text=""):
        self.status_code = code
        self.reason = "x"
        self.headers = {}
        self._body = body
        self.text = text

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@contextlib.contextmanager
def _curl(*responses, **kw):
    """curl katmanını sahteler: sırayla verilen yanıtlar (ya da fırlatılacak hatalar), uyku yok."""
    effect = kw.get("side_effect") or list(responses)
    with patch.object(transport, "_get_runtime_request_config", return_value=CFG), \
            patch.object(transport, "_get_proxy_config", return_value=(False, "")), \
            patch.object(transport, "_sleep"), \
            patch.object(transport.cffi_requests, "get", side_effect=effect) as get:
        yield get


# --- istek katmanı: raise_errors -------------------------------------------------

def test_sync_request_keeps_returning_none_by_default():
    with _curl(side_effect=lambda *a, **k: Resp(403, text="no")):
        assert transport.make_api_request("/x") is None


def test_sync_request_raises_403_as_api_error_after_the_last_attempt():
    with _curl(side_effect=lambda *a, **k: Resp(403, text="no")) as get, pytest.raises(APIError) as ei:
        transport.make_api_request("/x", raise_errors=True)
    assert ei.value.status_code == 403
    assert get.call_count == 3  # yeniden denemeler aynı: yalnızca vazgeçme biçimi değişir


@pytest.mark.parametrize("code", [429, 503])
def test_sync_request_raises_rate_limit_error(code):
    with _curl(side_effect=lambda *a, **k: Resp(code)), pytest.raises(RateLimitError) as ei:
        transport.make_api_request("/x", max_retries=1, raise_errors=True)
    assert ei.value.status_code == code


def test_sync_request_raises_not_found_without_retrying():
    with _curl(Resp(404)) as get, pytest.raises(ResourceNotFoundError):
        transport.make_api_request("/x", raise_errors=True)
    assert get.call_count == 1


def test_sync_request_raises_other_http_errors_with_their_status():
    with _curl(side_effect=lambda *a, **k: Resp(500)), pytest.raises(APIError) as ei:
        transport.make_api_request("/x", max_retries=2, raise_errors=True)
    assert ei.value.status_code == 500 and not isinstance(ei.value, (RateLimitError, ResourceNotFoundError))


def test_sync_request_raises_network_error_when_the_connection_fails():
    boom = ConnectionError("curl: (7) Failed to connect to www.sofascore.com port 443")
    with _curl(side_effect=boom) as get, pytest.raises(NetworkError):
        transport.make_api_request("/x", raise_errors=True)
    assert get.call_count == 3


def test_sync_request_raises_parsing_error_for_a_body_that_is_not_json():
    with _curl(side_effect=lambda *a, **k: Resp(200, ValueError("no json"))), pytest.raises(DataParsingError):
        transport.make_api_request("/x", max_retries=1, raise_errors=True)


def test_sync_request_reports_a_404_seen_through_the_bridge_as_not_found():
    with _curl(Resp(403, text=CHALLENGE)), \
            patch.object(cs, "fetch_api_via_browser_sync", return_value={"__404__": True}), \
            pytest.raises(ResourceNotFoundError):
        transport.make_api_request("/x", raise_errors=True)
    # raise_errors olmadan eski sözleşme: None
    with _curl(Resp(403, text=CHALLENGE)), \
            patch.object(cs, "fetch_api_via_browser_sync", return_value={"__404__": True}):
        assert transport.make_api_request("/x") is None


# --- nedenlerin sözlüğü ----------------------------------------------------------

@pytest.mark.parametrize(
    "exc, reason",
    [
        (APIError("HTTP 403", status_code=403), "blocked"),
        (RateLimitError(status_code=429), "rate_limited"),
        (RateLimitError(status_code=503), "rate_limited"),
        (NetworkError("no route"), "network"),
        (ResourceNotFoundError(), "not_found"),
        (APIError("HTTP 500", status_code=500), "upstream"),
        (DataParsingError("bad json"), "upstream"),
    ],
)
def test_reason_for_each_error_type(exc, reason):
    assert upstream.reason_for(exc, bridge_health.snapshot()) == reason


def test_a_403_is_a_browser_problem_only_when_the_browser_failed_during_this_request():
    blocked = APIError("HTTP 403", status_code=403)
    before = bridge_health.snapshot()
    bridge_health.record_failure(bridge_health.KIND_CHALLENGE, "HTTP 403")
    assert upstream.reason_for(blocked, before) == "blocked"

    before = bridge_health.snapshot()
    bridge_health.record_failure(bridge_health.KIND_BROWSER, "RuntimeError: no chromium")
    assert upstream.reason_for(blocked, before) == "browser"
    # Eski bir tarayıcı hatası, bu istekte yeni bir şey olmadıysa nedeni değiştirmez
    assert upstream.reason_for(blocked, bridge_health.snapshot()) == "blocked"
