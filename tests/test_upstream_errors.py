"""
Engellenen / başarısız SofaScore isteği "Sonuç yok" ya da sebepsiz bir başarı olarak görünmemeli.

Lig arama ve sezon yenileme tipli bir neden döndürür (src/web/upstream.py):
blocked / browser / rate_limited / network / not_found / upstream. Boş liste yalnızca istek
gerçekten başarılı olup hiçbir şey bulunamadığında döner. Bütün testler çevrimdışıdır: istek
katmanı (curl ya da make_api_request) sahtedir, gerçek tarayıcı conftest tarafından engellenir.
"""
from __future__ import annotations

import contextlib
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import src.challenge_solver as cs
import src.season_fetcher as season_fetcher_mod
import src.utils as utils
from conftest import DATA_DIR, LEAGUE_ID, SEASON_ID, SEASON_NAME
from src import bridge_health
from src.exceptions import (
    APIError,
    DataParsingError,
    NetworkError,
    RateLimitError,
    ResourceNotFoundError,
)
from src.season_fetcher import SeasonFetcher
from src.web import upstream
from src.web.routes import api as api_mod

CFG = {"max_retries": 3, "request_timeout": 5, "wait_time_min": 0, "wait_time_max": 0}
CHALLENGE = '{"error":{"code":403,"reason":"challenge"}}'
SEEDED_SEASONS = [{"id": SEASON_ID, "name": SEASON_NAME, "year": "26/27"}]


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
    with patch.object(utils, "_get_runtime_request_config", return_value=CFG), \
            patch.object(utils, "_get_proxy_config", return_value=(False, "")), \
            patch.object(utils, "_sleep"), \
            patch.object(utils.cffi_requests, "get", side_effect=effect) as get:
        yield get


@pytest.fixture
def client():
    from src.web.app import app

    return TestClient(app)


def _detail(r):
    return r.json()["detail"]


# --- istek katmanı: raise_errors -------------------------------------------------

def test_sync_request_keeps_returning_none_by_default():
    with _curl(side_effect=lambda *a, **k: Resp(403, text="no")):
        assert utils.make_api_request("/x") is None


def test_sync_request_raises_403_as_api_error_after_the_last_attempt():
    with _curl(side_effect=lambda *a, **k: Resp(403, text="no")) as get, pytest.raises(APIError) as ei:
        utils.make_api_request("/x", raise_errors=True)
    assert ei.value.status_code == 403
    assert get.call_count == 3  # yeniden denemeler aynı: yalnızca vazgeçme biçimi değişir


@pytest.mark.parametrize("code", [429, 503])
def test_sync_request_raises_rate_limit_error(code):
    with _curl(side_effect=lambda *a, **k: Resp(code)), pytest.raises(RateLimitError) as ei:
        utils.make_api_request("/x", max_retries=1, raise_errors=True)
    assert ei.value.status_code == code


def test_sync_request_raises_not_found_without_retrying():
    with _curl(Resp(404)) as get, pytest.raises(ResourceNotFoundError):
        utils.make_api_request("/x", raise_errors=True)
    assert get.call_count == 1


def test_sync_request_raises_other_http_errors_with_their_status():
    with _curl(side_effect=lambda *a, **k: Resp(500)), pytest.raises(APIError) as ei:
        utils.make_api_request("/x", max_retries=2, raise_errors=True)
    assert ei.value.status_code == 500 and not isinstance(ei.value, (RateLimitError, ResourceNotFoundError))


def test_sync_request_raises_network_error_when_the_connection_fails():
    boom = ConnectionError("curl: (7) Failed to connect to www.sofascore.com port 443")
    with _curl(side_effect=boom) as get, pytest.raises(NetworkError):
        utils.make_api_request("/x", raise_errors=True)
    assert get.call_count == 3


def test_sync_request_raises_parsing_error_for_a_body_that_is_not_json():
    with _curl(side_effect=lambda *a, **k: Resp(200, ValueError("no json"))), pytest.raises(DataParsingError):
        utils.make_api_request("/x", max_retries=1, raise_errors=True)


def test_sync_request_reports_a_404_seen_through_the_bridge_as_not_found():
    with _curl(Resp(403, text=CHALLENGE)), \
            patch.object(cs, "fetch_api_via_browser_sync", return_value={"__404__": True}), \
            pytest.raises(ResourceNotFoundError):
        utils.make_api_request("/x", raise_errors=True)
    # raise_errors olmadan eski sözleşme: None
    with _curl(Resp(403, text=CHALLENGE)), \
            patch.object(cs, "fetch_api_via_browser_sync", return_value={"__404__": True}):
        assert utils.make_api_request("/x") is None


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


# --- lig arama --------------------------------------------------------------------

SEARCH = "/api/leagues/search-remote?q=premier"


def test_search_returns_results(client):
    data = {"results": [{"entity": {
        "id": 17, "name": "Premier League", "slug": "premier-league",
        "category": {"name": "England", "sport": {"name": "Football"}},
    }}]}
    with patch.object(utils, "make_api_request", return_value=data) as req:
        r = client.get(SEARCH)
    assert r.status_code == 200
    assert r.json() == [{"id": 17, "name": "Premier League", "country": "England", "slug": "premier-league", "sport": "Football"}]
    # Etkileşimli arama: tek deneme, kısa zaman aşımı, tipli hata
    assert req.call_args.kwargs == {"max_retries": 1, "timeout": 10, "raise_errors": True}


def test_search_with_nothing_found_is_an_empty_success(client):
    with patch.object(utils, "make_api_request", return_value={"results": []}):
        r = client.get(SEARCH)
    assert r.status_code == 200 and r.json() == []


@pytest.mark.parametrize(
    "exc, status, reason",
    [
        (APIError("HTTP 403 Forbidden", status_code=403), 502, "blocked"),
        (RateLimitError(status_code=429), 503, "rate_limited"),
        (NetworkError("İstek başarısız"), 502, "network"),
        (APIError("HTTP 500", status_code=500), 502, "upstream"),
        (DataParsingError("bad json"), 502, "upstream"),
        # Arama uç noktası sonuç yokken boş liste döndürür; 404 "sonuç yok" sayılmaz
        (ResourceNotFoundError(), 502, "upstream"),
    ],
)
def test_search_failure_is_a_typed_error_not_an_empty_list(client, exc, status, reason):
    with patch.object(utils, "make_api_request", side_effect=exc):
        r = client.get(SEARCH)
    assert r.status_code == status
    assert _detail(r)["reason"] == reason
    assert _detail(r)["message"]


@pytest.mark.parametrize("data", [None, {}, {"error": {"code": 403}}, {"results": None}, ["x"]])
def test_search_answer_without_a_result_list_is_not_reported_as_no_results(client, data):
    with patch.object(utils, "make_api_request", return_value=data):
        r = client.get(SEARCH)
    assert r.status_code == 502 and _detail(r)["reason"] == "upstream"


def test_search_blocked_end_to_end_through_the_request_layer(client):
    """curl 403 alır, challenge sunulmaz: SofaScore reddediyor."""
    with _curl(Resp(403, text="Access denied")) as get:
        r = client.get(SEARCH)
    assert get.call_count == 1  # etkileşimli arama yeniden denemez
    assert r.status_code == 502 and _detail(r)["reason"] == "blocked"


def test_search_names_the_browser_when_the_challenge_cannot_even_be_attempted(client):
    """curl challenge alır, gömülü tarayıcı başlatılamaz (conftest gerçek tarayıcıyı engeller)."""
    with _curl(Resp(403, text=CHALLENGE)):
        r = client.get(SEARCH)
    assert r.status_code == 502 and _detail(r)["reason"] == "browser"
    assert bridge_health.snapshot()["last_error"]["kind"] == "browser"


def test_search_network_failure_end_to_end(client):
    with _curl(side_effect=ConnectionError("curl: (6) Could not resolve host: www.sofascore.com")):
        r = client.get(SEARCH)
    assert r.status_code == 502 and _detail(r)["reason"] == "network"


# --- sezon yenileme ---------------------------------------------------------------

REFRESH = f"/api/leagues/{LEAGUE_ID}/seasons/refresh"


def _seasons_on_disk():
    path = os.path.join(DATA_DIR, "seasons", f"{LEAGUE_ID}_Premier_League_seasons.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)["seasons"]


def test_refresh_returns_the_seasons_sofascore_sent(client):
    with patch.object(season_fetcher_mod, "make_api_request", return_value={"seasons": SEEDED_SEASONS}) as req:
        r = client.post(REFRESH)
    assert r.status_code == 200
    assert r.json() == {"status": "success", "seasons": SEEDED_SEASONS}
    assert req.call_args.kwargs == {"max_retries": 2, "raise_errors": True}
    assert _seasons_on_disk() == SEEDED_SEASONS


def test_refresh_with_no_seasons_at_sofascore_is_an_empty_success(client):
    with patch.object(season_fetcher_mod, "make_api_request", return_value={"seasons": []}), \
            patch.object(SeasonFetcher, "_save_seasons_json"):
        r = client.post("/api/leagues/424242/seasons/refresh")
    assert r.status_code == 200 and r.json() == {"status": "success", "seasons": []}


@pytest.mark.parametrize(
    "exc, status, reason",
    [
        (APIError("HTTP 403 Forbidden", status_code=403), 502, "blocked"),
        (RateLimitError(status_code=429), 503, "rate_limited"),
        (NetworkError("İstek başarısız"), 502, "network"),
        (ResourceNotFoundError(), 404, "not_found"),
        (APIError("HTTP 500", status_code=500), 502, "upstream"),
    ],
)
def test_refresh_failure_is_not_reported_as_success(client, exc, status, reason):
    """Diskte eski bir sezon listesi olsa bile: yenileme başarısızsa başarı denmez."""
    assert _seasons_on_disk() == SEEDED_SEASONS
    with patch.object(season_fetcher_mod, "make_api_request", side_effect=exc):
        r = client.post(REFRESH)
    assert r.status_code == status
    assert _detail(r)["reason"] == reason
    assert _seasons_on_disk() == SEEDED_SEASONS  # kayıtlı liste bozulmadı
    # Kayıtlı liste hâlâ okunabilir
    assert client.get(f"/api/leagues/{LEAGUE_ID}/seasons").json() == {"seasons": SEEDED_SEASONS, "fetched": True}


@pytest.mark.parametrize("data", [None, {}, {"seasons": None}, {"error": {"code": 500}}])
def test_refresh_answer_without_a_season_list_is_an_upstream_error(client, data):
    with patch.object(season_fetcher_mod, "make_api_request", return_value=data):
        r = client.post(REFRESH)
    assert r.status_code == 502 and _detail(r)["reason"] == "upstream"
    assert _seasons_on_disk() == SEEDED_SEASONS


def test_refresh_blocked_end_to_end_through_the_request_layer(client):
    with _curl(side_effect=lambda *a, **k: Resp(403, text="Access denied")) as get:
        r = client.post(REFRESH)
    assert get.call_count == 2  # etkileşimli yenileme: en çok iki deneme
    assert r.status_code == 502 and _detail(r)["reason"] == "blocked"


def test_legacy_season_fetch_still_returns_an_empty_list_on_failure():
    """CLI ve arka plan işi fetch_seasons_for_league'den liste bekler; hata fırlatmamalı."""
    fetcher = SeasonFetcher(api_mod.config_manager, DATA_DIR)
    with patch.object(season_fetcher_mod, "make_api_request", side_effect=APIError("HTTP 403", status_code=403)):
        assert fetcher.fetch_seasons_for_league(LEAGUE_ID) == []
        with pytest.raises(APIError):
            fetcher.fetch_seasons_checked(LEAGUE_ID)
    with patch.object(season_fetcher_mod, "make_api_request", return_value={"seasons": SEEDED_SEASONS}) as req:
        assert fetcher.fetch_seasons_for_league(LEAGUE_ID) == SEEDED_SEASONS
    assert req.call_args.kwargs == {"max_retries": None, "raise_errors": True}  # yapılandırılan deneme sayısı
