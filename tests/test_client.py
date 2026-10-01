"""
src/client: istek katmanının yeni yeri ve üzerindeki Client yüzü (plan maddesi P05).

Ağ yok: istekler tests/fakes/sofascore.py'deki sahte taşıyıcıya gider; istek katmanının kendisi gerçektir.
"""
from __future__ import annotations

import ast
import asyncio
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.utils as utils
from characterization import pin_default_settings
from fakes.sofascore import REQUEST_LAYER, REQUEST_LAYER_MODULES, SITE_ROOT, FakeResponse, FakeSofaScore
from src import breaker as request_breaker
from src import bridge_health
from src.breaker import CircuitBreaker
from src.bridge_health import BridgeHealth
from src.client import (
    Cancelled,
    Client,
    ClientSettings,
    FetchCancelled,
    RequestContext,
    context,
    endpoints,
    request_context,
    transport,
)
from src.client.transport import RequestTrace
from src.config_manager import ConfigManager
from src.match_data_fetcher import MatchDataFetcher
from src.match_fetcher import MatchFetcher
from src.season_fetcher import SeasonFetcher
from src.slices import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, SLICE_SKIPPED, Outcome
from src.sports import DETAIL_SLICES
from src.watcher import MatchWatcher

EVENT = {"id": 1, "status": {"type": "finished"}}


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    world = FakeSofaScore()
    world.add_event(EVENT, {"statistics": {"statistics": [{"period": "ALL"}]}})
    with world:
        yield world


# --- taşıma: src/utils.py ince bir yeniden dışa aktarım ----------------------------------------------

MOVED_TO_TRANSPORT = (
    "make_api_request", "make_api_request_async", "_request_sync", "_request_async", "_request_semaphore",
    "_request_semaphores", "create_session_async", "WarmableAsyncSession", "_warmup_session",
    "get_request_headers", "get_sofascore_hash", "get_sofa_captcha_token", "_get_runtime_request_config",
    "_get_proxy_config", "_parse_retry_after_seconds", "_sleep", "_asleep", "_throttle", "_athrottle",
    "_browser_first", "_mark_browser_first", "_browser_result", "_is_transient_status", "_full_url", "_retry_wait",
    "IMPERSONATE_PROFILES", "_ACCEPT_LANGUAGES", "BROWSER_FIRST_SECONDS", "API_BASE_URL", "JsonResponse",
    "AsyncSession", "cffi_requests",
)
MOVED_TO_CONTEXT = (
    "FetchCancelled", "_cancel_check", "set_cancel_check", "raise_if_cancelled", "_wait_notifier",
    "set_wait_notifier", "_notify_wait",
)


@pytest.mark.parametrize("name", MOVED_TO_TRANSPORT)
def test_utils_re_exports_the_transport_objects(name: str) -> None:
    assert getattr(utils, name) is getattr(transport, name)


@pytest.mark.parametrize("name", MOVED_TO_CONTEXT)
def test_utils_re_exports_the_context_objects(name: str) -> None:
    assert getattr(utils, name) is getattr(context, name)


def test_utils_keeps_no_request_code_of_its_own() -> None:
    """Gövde taşındı: src/utils.py'de tanımlanan işlevler yalnızca istek katmanı dışındakilerdir."""
    import inspect

    defined_here = {
        name for name, value in vars(utils).items()
        if inspect.isfunction(value) and value.__module__ == "src.utils" and not name.startswith("__")
    }
    assert defined_here == {"ensure_directory", "_forwarding_table"}


def test_the_fake_transport_patches_the_module_that_holds_the_body() -> None:
    assert "src.client.transport" in REQUEST_LAYER_MODULES


def test_an_assignment_on_utils_reaches_the_moved_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """`utils._sleep = sahte` eskiden gövdeyi etkilerdi; gövde taşındıktan sonra da etkilemeli."""
    slept = []
    monkeypatch.setattr(utils, "_sleep", slept.append)

    assert transport._sleep == slept.append
    transport._throttle()  # bütçe kapalı (REQUEST_RATE_LIMIT=0): beklemez, ama sahteyi görür
    with patch.object(utils.throttle, "reserve", return_value=1.5):
        transport._throttle()
    assert slept == [1.5]

    monkeypatch.undo()
    assert transport._sleep is utils._sleep


def test_mock_patch_on_utils_reaches_the_moved_body_and_is_undone() -> None:
    original = transport._get_proxy_config
    with patch.object(utils, "_get_proxy_config", return_value=(True, "socks5://127.0.0.1:1")):
        assert transport._get_proxy_config() == (True, "socks5://127.0.0.1:1")
    assert transport._get_proxy_config is original and utils._get_proxy_config is original

    with patch("src.utils.raise_if_cancelled") as fake_check:
        transport._sleep(0)
    assert fake_check.called
    assert transport.raise_if_cancelled is context.raise_if_cancelled is utils.raise_if_cancelled


def test_browser_first_state_has_a_single_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_browser_first_until` gövdede yeniden bağlanır: utils kopya tutmaz, okuma ve yazma sahibine gider."""
    assert "_browser_first_until" not in vars(utils)
    monkeypatch.setattr(utils, "_browser_first_until", 0.0)
    assert not utils._browser_first()

    transport._mark_browser_first()
    assert utils._browser_first() and utils._browser_first_until == transport._browser_first_until > 0

    monkeypatch.undo()
    assert transport._browser_first_until == 0.0  # conftest her testten önce sıfırlar; geri alma da sahibine yazılır


def test_names_that_stayed_in_utils_are_not_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(utils, "FETCH_ONLY_FINISHED", False)
    assert not hasattr(transport, "FETCH_ONLY_FINISHED") and not hasattr(context, "FETCH_ONLY_FINISHED")
    assert not hasattr(utils, "no_such_name")


def test_legacy_entry_points_still_run_the_real_request_layer(fake: FakeSofaScore) -> None:
    assert utils.make_api_request("/event/1")["event"]["id"] == 1
    assert utils.make_api_request("/event/2") is None
    assert [r.label for r in fake.requests] == ["sync /event/1 200", "sync /event/2 404"]


# --- köprü sağlığı: on_health_change ---------------------------------------------------------------

THRESHOLDS = {"degraded_after": 3, "blocked_after": 5, "blocked_min_seconds": 0.0}


def _bridge_health(**kwargs: Any) -> BridgeHealth:
    return BridgeHealth(clock=lambda: 1_790_000_000.0, thresholds_fn=lambda: dict(THRESHOLDS), **kwargs)


def _fail(health: Any, count: int) -> None:
    for _ in range(count):
        health.record_failure(bridge_health.KIND_CHALLENGE, "HTTP 403")


def test_on_health_change_gets_the_snapshot_of_every_transition_once() -> None:
    seen: List[Any] = []
    health = _bridge_health(on_health_change=seen.append)

    _fail(health, 2)
    assert seen == []  # eşiğin altında durum değişmez
    _fail(health, 5)
    health.record_success()
    health.record_success()  # zaten ok: geçiş yok

    assert [(s["state"], s["consecutive_failures"]) for s in seen] == [("degraded", 3), ("blocked", 5), ("ok", 0)]
    assert seen[-1] == health.snapshot()
    json.dumps(seen)  # çağıran görüntüyü olduğu gibi saklayabilir


def test_health_callbacks_can_be_added_and_removed() -> None:
    first: List[Any] = []
    second: List[Any] = []
    on_first, on_second = first.append, second.append
    health = _bridge_health()
    health.add_on_health_change(on_first)
    health.add_on_health_change(on_first)  # iki kez eklenen bir kez çağrılır
    health.add_on_health_change(on_second)

    _fail(health, 3)
    health.remove_on_health_change(on_second)
    health.remove_on_health_change(on_second)  # olmayanı kaldırmak hata değil
    health.record_success()

    assert [s["state"] for s in first] == ["degraded", "ok"]
    assert [s["state"] for s in second] == ["degraded"]


def test_a_failing_health_callback_breaks_neither_recording_nor_the_others() -> None:
    seen: List[Any] = []
    listened: List[str] = []

    def boom(snapshot: Any) -> None:
        raise RuntimeError("cannot store the snapshot")

    health = _bridge_health(on_health_change=boom)
    health.add_on_health_change(seen.append)
    health.add_listener(lambda snap, previous: listened.append(f"{previous}>{snap['state']}"))

    _fail(health, 3)

    assert health.state == "degraded"
    assert [s["state"] for s in seen] == ["degraded"] and listened == ["ok>degraded"]


def test_process_wide_health_reports_through_the_module_functions() -> None:
    seen: List[Any] = []
    on_change = seen.append
    bridge_health.add_on_health_change(on_change)
    try:
        for _ in range(int(bridge_health.thresholds()["degraded_after"])):
            bridge_health.record_failure(bridge_health.KIND_FORBIDDEN, "HTTP 403")
        assert [s["state"] for s in seen] == ["degraded"]
        bridge_health.remove_on_health_change(on_change)
        bridge_health.record_success()
        assert [s["state"] for s in seen] == ["degraded"]
    finally:
        bridge_health.remove_on_health_change(on_change)


# --- Client: sonuç eşlemesi ------------------------------------------------------------------------

NOW = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.timezone.utc)
CHALLENGE_BODY = '{"error": {"code": 403, "reason": "challenge"}}'


@pytest.fixture(autouse=True)
def _clean_slate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    pin_default_settings(monkeypatch)  # MAX_RETRIES=3 vb.: kabuktan sızan ayar sonucu değiştirmesin
    # Başka bir testin bıraktığı iptal kontrolü, bekleme bildirimi ya da devre kesici bu dosyaya sızmasın
    with request_context():
        yield


@pytest.fixture
def client() -> Iterator[Client]:
    made = Client(clock=lambda: NOW)
    yield made
    made.close()


def _get(client: Client, path: str, how: str, **kwargs: Any) -> Outcome:
    """Aynı isteği iki yoldan biriyle atar: "sync" (get_sync) ya da "async" (get, kendi döngüsünde)."""
    if how == "sync":
        return client.get_sync(path, **kwargs)

    async def run() -> Outcome:
        async with client:
            return await client.get(path, **kwargs)

    return asyncio.run(run())


def _api_requests(fake: FakeSofaScore) -> List[str]:
    """Oturum ısınması dışındaki istekler: "<yol> <sonuç>"."""
    return [f"{r.path} {r.outcome}" for r in fake.requests if r.path != SITE_ROOT]


BOTH = pytest.mark.parametrize("how", ["sync", "async"])


@BOTH
def test_an_answer_with_data_is_ok(fake: FakeSofaScore, client: Client, how: str) -> None:
    outcome = _get(client, endpoints.event(1), how)

    assert outcome == Outcome(
        SLICE_OK, data={"event": EVENT}, reason=None, http_status=200, fetched_at=NOW, via="curl", meta=None
    )
    assert _api_requests(fake) == ["/event/1 200"]


@BOTH
def test_404_is_a_definite_empty_and_is_not_retried(fake: FakeSofaScore, client: Client, how: str) -> None:
    outcome = _get(client, endpoints.event_slice("lineups", 1), how)

    assert (outcome.status, outcome.reason, outcome.http_status, outcome.data) == (SLICE_EMPTY, "404", 404, None)
    assert (outcome.via, outcome.fetched_at) == ("curl", NOW)
    assert _api_requests(fake) == ["/event/1/lineups 404"]


@BOTH
@pytest.mark.parametrize("body", [{}, []])
def test_an_answer_without_content_is_empty_and_keeps_the_body(
    fake: FakeSofaScore, client: Client, how: str, body: Any
) -> None:
    fake.add("/event/1/h2h", body)

    outcome = _get(client, "/event/1/h2h", how)

    assert (outcome.status, outcome.reason, outcome.data, outcome.http_status) == (SLICE_EMPTY, "empty", body, 200)


@BOTH
@pytest.mark.parametrize(
    ("status", "reason"),
    [(403, "403"), (429, "429"), (500, "5xx"), (503, "5xx"), (502, "5xx"), (400, "other")],
)
def test_http_errors_become_failed_outcomes(
    fake: FakeSofaScore, client: Client, how: str, status: int, reason: str
) -> None:
    fake.fail("/event/1", status)

    outcome = _get(client, "/event/1", how)

    assert (outcome.status, outcome.reason, outcome.http_status) == (SLICE_FAILED, reason, status)
    assert outcome.failed and outcome.data is None and (outcome.via, outcome.fetched_at) == ("curl", NOW)
    # Kalıcı 4xx yeniden denenmez; gerisi MAX_RETRIES (3) kez denenir
    assert _api_requests(fake) == [f"/event/1 {status}"] * (1 if status == 400 else 3)


@BOTH
def test_timeouts_and_connection_errors_become_failed_outcomes(fake: FakeSofaScore, client: Client, how: str) -> None:
    fake.timeout("/event/1")
    fake.disconnect("/event/2")

    timed_out = _get(client, "/event/1", how)
    refused = _get(client, "/event/2", how)

    assert (timed_out.status, timed_out.reason, timed_out.http_status, timed_out.via) == (SLICE_FAILED, "timeout", None, None)
    assert (refused.status, refused.reason, refused.http_status, refused.via) == (SLICE_FAILED, "network", None, None)


@BOTH
def test_the_outcome_describes_the_last_attempt_not_an_earlier_one(
    fake: FakeSofaScore, client: Client, how: str
) -> None:
    fake.fail("/event/1", 500, times=1)
    fake.timeout("/event/1")

    outcome = _get(client, "/event/1", how)

    assert _api_requests(fake) == ["/event/1 500", "/event/1 timeout", "/event/1 timeout"]
    assert (outcome.reason, outcome.http_status, outcome.via) == ("timeout", None, None)  # 500'ün izi kalmaz


@BOTH
def test_an_unparsable_answer_is_a_failed_outcome(fake: FakeSofaScore, client: Client, how: str) -> None:
    fake.fail("/event/1", 200, body="<html>not json</html>")

    outcome = _get(client, "/event/1", how, retries=1)

    assert (outcome.status, outcome.reason) == (SLICE_FAILED, "parse")


@BOTH
def test_an_answer_without_data_and_without_an_error_is_failed_not_missing(
    fake: FakeSofaScore, client: Client, how: str
) -> None:
    """İstek katmanı hata fırlatmadan None dönerse (gövdesi `null` olan 200) sonuç "yok" değil, başarısızlıktır."""
    fake.add("/event/1/incidents", None)

    outcome = _get(client, "/event/1/incidents", how)

    assert (outcome.status, outcome.reason, outcome.http_status, outcome.data) == (SLICE_FAILED, "other", 200, None)


def test_the_silent_none_at_the_end_of_the_async_body_is_failed(fake: FakeSofaScore, client: Client) -> None:
    async def silent(*args: Any, **kwargs: Any) -> None:
        return None

    with patch.object(transport, "_request_async", silent):
        outcome = _get(client, "/event/1", "async")

    assert (outcome.status, outcome.reason, outcome.http_status, outcome.via) == (SLICE_FAILED, "other", None, None)


# --- Client: deneme sayısı, zaman aşımı, adres -------------------------------------------------------

@BOTH
def test_retries_come_from_the_call_then_the_settings_then_the_configuration(fake: FakeSofaScore, how: str) -> None:
    fake.fail("/event/*", 500)

    _get(Client(), "/event/1", how)
    _get(Client(ClientSettings(retries=2)), "/event/2", how)
    _get(Client(ClientSettings(retries=2)), "/event/3", how, retries=1)

    assert _api_requests(fake) == ["/event/1 500"] * 3 + ["/event/2 500"] * 2 + ["/event/3 500"]


def test_timeout_reaches_curl_on_both_paths(client: Client) -> None:
    seen: Dict[str, Any] = {}

    def sync_get(url: str, **kwargs: Any) -> FakeResponse:
        seen["sync"] = kwargs["timeout"]
        return FakeResponse(200, '{"a": 1}')

    class Session:
        cookies: Dict[str, str] = {}

        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "Session":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> FakeResponse:
            if url != SITE_ROOT:
                seen.setdefault("async", []).append(kwargs["timeout"])
            return FakeResponse(200, '{"a": 1}')

    no_wait = AsyncMock()
    with patch.object(transport.cffi_requests, "get", sync_get), patch.object(transport, "AsyncSession", Session), \
            patch.object(transport, "_sleep", lambda s: None), patch.object(transport, "_asleep", no_wait), \
            patch.object(transport.asyncio, "sleep", no_wait):
        assert client.get_sync("/a", timeout=2.5).status == SLICE_OK
        assert _get(client, "/a", "async", timeout=4).status == SLICE_OK
        assert _get(Client(ClientSettings(timeout_seconds=7)), "/a", "async").status == SLICE_OK
        assert _get(client, "/a", "async").status == SLICE_OK  # verilmezse REQUEST_TIMEOUT ayarı

    assert seen == {"sync": 2.5, "async": [4, 7, transport._cm.get_request_timeout()]}


def test_the_base_url_is_applied_in_one_place() -> None:
    client = Client(ClientSettings(base_url=" https://api.sofascore.com/api/v1/ "))
    called: List[str] = []

    def sync_get(url: str, **kwargs: Any) -> FakeResponse:
        called.append(url)
        return FakeResponse(200, '{"a": 1}')

    assert client.settings.base_url == "https://api.sofascore.com/api/v1"
    assert client.url("/event/1") == "https://api.sofascore.com/api/v1/event/1"
    with patch.object(transport.cffi_requests, "get", sync_get), patch.object(transport, "_sleep", lambda s: None):
        assert client.get_sync(endpoints.seasons(17)).data == {"a": 1}
    assert called == ["https://api.sofascore.com/api/v1/unique-tournament/17/seasons"]


def test_default_settings_follow_the_configured_api_base_url() -> None:
    assert ClientSettings.from_environment().base_url == transport.base_url() == utils.API_BASE_URL
    assert Client().url("/event/1") == transport.api_url("/event/1") == transport._full_url("/event/1")


@pytest.mark.parametrize("path", ["event/1", "https://www.sofascore.com/api/v1/event/1", ""])
def test_paths_must_be_relative_to_the_api_root(client: Client, path: str) -> None:
    with pytest.raises(ValueError):
        client.get_sync(path)
    with pytest.raises(ValueError):
        asyncio.run(client.get(path))


@pytest.mark.parametrize("base_url", ["", "www.sofascore.com/api/v1", "ftp://www.sofascore.com"])
def test_settings_reject_a_base_url_that_is_not_http(base_url: str) -> None:
    with pytest.raises(ValueError):
        ClientSettings(base_url=base_url)


# --- Client: oturum ----------------------------------------------------------------------------------

def test_async_requests_share_one_warmed_session_per_loop_and_aclose_closes_it(
    fake: FakeSofaScore, client: Client
) -> None:
    async def run() -> List[Outcome]:
        first = await asyncio.gather(client.get("/event/1"), client.get("/event/1/statistics"))
        second = await client.get("/event/1")
        await client.aclose()
        await client.aclose()  # açık oturum yokken: sorun değil
        return [*first, second]

    outcomes = asyncio.run(run())

    assert [o.status for o in outcomes] == [SLICE_OK] * 3
    assert len(fake.sessions) == 1
    assert fake.canonical_log() == [
        {"concurrent": sorted([
            f"async {SITE_ROOT} 200", "async /event/1 200", "async /event/1 200", "async /event/1/statistics 200",
        ])}
    ]  # tek ısınma isteği; "concurrent" bloğu oturumun kapandığını da gösterir


def test_each_event_loop_gets_its_own_session(fake: FakeSofaScore, client: Client) -> None:
    _get(client, "/event/1", "async")
    _get(client, "/event/1", "async")

    assert len(fake.sessions) == 2
    assert [r.path for r in fake.requests].count(SITE_ROOT) == 2


def test_the_sync_path_opens_no_session(fake: FakeSofaScore, client: Client) -> None:
    client.get_sync("/event/1")

    assert fake.sessions == [] and [r.label for r in fake.requests] == ["sync /event/1 200"]


# --- Client: istek bağlamı (iptal, bekleme bildirimi, devre kesici) ----------------------------------

@BOTH
def test_an_open_breaker_is_skipped_and_sends_nothing(fake: FakeSofaScore, client: Client, how: str) -> None:
    breaker = CircuitBreaker(consecutive=1)
    breaker.record("403")
    assert breaker.tripped

    with request_context(breaker=breaker):
        outcome = _get(client, "/event/1", how)

    assert outcome == Outcome(SLICE_SKIPPED, reason="breaker", fetched_at=NOW)
    assert not outcome.failed
    assert fake.requests == [] and fake.sessions == []  # ısınma isteği de atılmaz
    assert breaker.attempts == 1  # gönderilmeyen istek kesiciye sayılmaz


@BOTH
def test_requests_are_reported_to_the_breaker_once(fake: FakeSofaScore, client: Client, how: str) -> None:
    fake.fail("/event/2", 403)
    breaker = CircuitBreaker(consecutive=100)

    with request_context(breaker=breaker):
        _get(client, "/event/1", how)
        _get(client, "/event/2", how)  # üç deneme, tek istek
        _get(client, "/event/3", how)  # 404: yanıt geldi

    assert (breaker.attempts, breaker.failures, breaker.counts()) == (3, 1, {"403": 1, "404": 1})


@BOTH
def test_the_breaker_trips_through_the_client_and_later_requests_are_skipped(
    fake: FakeSofaScore, client: Client, how: str
) -> None:
    fake.fail("/event/*", 429)
    breaker = CircuitBreaker(consecutive=2)

    with request_context(breaker=breaker):
        statuses = [_get(client, f"/event/{n}", how, retries=1).status for n in (1, 2, 3)]

    assert statuses == [SLICE_FAILED, SLICE_FAILED, SLICE_SKIPPED]
    assert _api_requests(fake) == ["/event/1 429", "/event/2 429"]


@BOTH
def test_a_cancelled_job_raises_and_sends_nothing(fake: FakeSofaScore, client: Client, how: str) -> None:
    with request_context(cancel=lambda: True):
        with pytest.raises(Cancelled):
            _get(client, "/event/1", how)

    assert fake.requests == [] and fake.sessions == []
    assert Cancelled is FetchCancelled is utils.FetchCancelled


@BOTH
def test_long_waits_are_reported_to_on_wait(fake: FakeSofaScore, client: Client, how: str) -> None:
    fake.fail("/event/1", 429, times=1, headers={"Retry-After": "7"})
    waits: List[Any] = []

    with request_context(on_wait=lambda reason, seconds: waits.append((reason, seconds))):
        outcome = _get(client, "/event/1", how)

    assert outcome.status == SLICE_OK and waits == [("rate_limit", 7.0)]
    assert 7.0 in fake.slept(REQUEST_LAYER)


def test_request_context_sets_exactly_what_it_is_given_and_restores_on_exit() -> None:
    outer_breaker, inner_breaker = CircuitBreaker(), CircuitBreaker()
    waits: List[str] = []
    assert request_breaker.current() is None

    with request_context(cancel=lambda: False, on_wait=lambda r, s: waits.append(r), breaker=outer_breaker) as outer:
        assert isinstance(outer, RequestContext) and outer.breaker is outer_breaker
        assert request_breaker.current() is outer_breaker
        context.raise_if_cancelled()
        context._notify_wait("outer", 1)

        with request_context(cancel=lambda: True, breaker=inner_breaker) as inner:
            assert (inner.on_wait, inner.breaker) == (None, inner_breaker)
            assert request_breaker.current() is inner_breaker
            context._notify_wait("inner", 1)  # bildirim verilmedi: dışarıdaki çağrılmaz
            with pytest.raises(Cancelled):
                context.raise_if_cancelled()

        with request_context():  # None "yok" demektir: işin kesicisine sayılmayan istekler
            assert request_breaker.current() is None
            context.raise_if_cancelled()

        assert request_breaker.current() is outer_breaker
        context._notify_wait("outer again", 1)

    assert waits == ["outer", "outer again"]
    assert request_breaker.current() is None
    assert context._cancel_check.get() is None and context._wait_notifier.get() is None


def test_request_context_restores_after_an_error() -> None:
    with pytest.raises(RuntimeError):
        with request_context(cancel=lambda: True, breaker=CircuitBreaker()):
            raise RuntimeError("job failed")

    assert request_breaker.current() is None and context._cancel_check.get() is None


# --- Client: tarayıcı köprüsü ve sağlık ---------------------------------------------------------------

def test_an_answer_fetched_by_the_bridge_says_so(fake: FakeSofaScore, client: Client) -> None:
    fake.fail("/event/1", 403, body=CHALLENGE_BODY)

    with patch("src.challenge_solver.fetch_api_via_browser_sync", return_value={"event": EVENT}):
        through_bridge = client.get_sync("/event/1")
        browser_first = client.get_sync("/event/1")  # "önce tarayıcı" modu: curl denenmez
    with patch("src.challenge_solver.fetch_api_via_browser", AsyncMock(return_value={"__404__": True})):
        missing = _get(client, "/event/1", "async")

    assert (through_bridge.status, through_bridge.via, through_bridge.http_status) == (SLICE_OK, "bridge", None)
    assert (browser_first.status, browser_first.via) == (SLICE_OK, "bridge")
    assert (missing.status, missing.reason, missing.http_status, missing.via) == (SLICE_EMPTY, "404", 404, "bridge")
    assert _api_requests(fake) == ["/event/1 403"]


def test_a_refused_request_that_the_bridge_cannot_rescue_is_failed_via_curl(
    fake: FakeSofaScore, client: Client
) -> None:
    fake.fail("/event/1", 403, body=CHALLENGE_BODY)

    with patch("src.challenge_solver.fetch_api_via_browser_sync", return_value=None):
        outcome = client.get_sync("/event/1", retries=1)

    assert (outcome.status, outcome.reason, outcome.http_status, outcome.via) == (SLICE_FAILED, "403", 403, "curl")


def test_the_client_reports_health_transitions_and_stops_after_close() -> None:
    seen: List[Any] = []
    health = _bridge_health()
    client = Client(health=health, on_health_change=seen.append)

    _fail(health, 3)
    assert [s["state"] for s in seen] == ["degraded"]
    assert client.health() == health.snapshot() == seen[-1]

    client.close()
    client.close()  # iki kez kapatmak sorun değil
    health.record_success()
    assert [s["state"] for s in seen] == ["degraded"]


def test_the_default_health_source_is_the_process_wide_bridge_health() -> None:
    seen: List[Any] = []
    client = Client(on_health_change=seen.append)
    try:
        assert client.health() == bridge_health.snapshot()
        for _ in range(int(bridge_health.thresholds()["degraded_after"])):
            bridge_health.record_failure(bridge_health.KIND_CHALLENGE, "HTTP 403")
        assert [s["state"] for s in seen] == ["degraded"] and client.health()["state"] == "degraded"
    finally:
        client.close()


def test_the_client_writes_nothing_under_the_data_directory(
    fake: FakeSofaScore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.chdir(tmp_path)
    fake.fail("/event/2", 403)
    health = _bridge_health()
    client = Client(health=health, on_health_change=lambda snapshot: None)

    client.get_sync("/event/1")
    client.get_sync("/event/2")
    _get(client, "/event/1", "async")
    _fail(health, 5)
    client.close()

    assert not data_dir.exists() and list(tmp_path.iterdir()) == []


# --- eski giriş noktaları: yeni isteğe bağlı parametreler ------------------------------------------------

def test_legacy_entry_points_fill_a_trace_when_given_one(fake: FakeSofaScore) -> None:
    sync_trace, async_trace, missing_trace = RequestTrace(), RequestTrace(), RequestTrace()

    async def run() -> Any:
        async with utils.create_session_async() as session:
            return await utils.make_api_request_async(session, "/event/1", timeout=3, trace=async_trace)

    assert utils.make_api_request("/event/1", trace=sync_trace)["event"]["id"] == 1
    assert asyncio.run(run())["event"]["id"] == 1
    assert utils.make_api_request("/event/404", trace=missing_trace) is None

    assert (sync_trace.via, sync_trace.http_status) == ("curl", 200)
    assert (async_trace.via, async_trace.http_status) == ("curl", 200)
    assert (missing_trace.via, missing_trace.http_status) == ("curl", 404)


# --- uç noktalar -------------------------------------------------------------------------------------

def test_endpoints_build_the_paths_the_fetchers_build_today() -> None:
    assert endpoints.event(123) == "/event/123"
    assert endpoints.live_events("football") == "/sport/football/events/live"
    assert endpoints.seasons(17) == "/unique-tournament/17/seasons"
    assert endpoints.rounds(17, 61627) == "/unique-tournament/17/season/61627/rounds"
    assert endpoints.season_events_page(17, 61627, "last", 0) == "/unique-tournament/17/season/61627/events/last/0"
    assert endpoints.season_events_page(17, 61627, "next", 3) == "/unique-tournament/17/season/61627/events/next/3"
    for slug in (None, "final", "round-of-16"):
        assert endpoints.round_events(17, 61627, 5, slug) == MatchFetcher.build_round_events_url(17, 61627, 5, slug)
    assert endpoints.round_events(17, 61627, 5, "final").endswith("/events/round/5/slug/final")


def test_event_slice_paths_come_from_the_slice_table() -> None:
    for spec in DETAIL_SLICES:
        assert endpoints.event_slice(spec.key, 42) == spec.url("", 42)
    assert endpoints.event_slice("team_streaks", 42) == "/event/42/team-streaks"
    with pytest.raises(KeyError):
        endpoints.event_slice("no_such_slice", 42)


def test_season_event_pages_are_last_or_next() -> None:
    with pytest.raises(ValueError):
        endpoints.season_events_page(17, 61627, "previous", 0)


@pytest.mark.parametrize(
    ("query", "path"),
    [
        ("premier", "/search/unique-tournaments/premier"),
        ("süper lig", "/search/unique-tournaments/s%C3%BCper%20lig"),
        ("a/b?c=d#e", "/search/unique-tournaments/a%2Fb%3Fc%3Dd%23e"),
    ],
)
def test_the_search_query_is_one_encoded_path_segment(query: str, path: str) -> None:
    assert endpoints.search_unique_tournaments(query) == path


def test_every_endpoint_is_relative_to_the_api_root() -> None:
    templates = [value for name, value in vars(endpoints).items() if name.isupper() and isinstance(value, str)]
    paths = [template for template in templates if template != endpoints.DEFAULT_BASE_URL]

    assert len(paths) >= 8 and all(path.startswith("/") and "sofascore.com" not in path for path in paths)
    assert endpoints.DEFAULT_BASE_URL == "https://www.sofascore.com/api/v1"


# --- API_BASE_URL her isteğe uygulanır ---------------------------------------------------------------

DEFAULT_BASE = "https://www.sofascore.com/api/v1"
OTHER_BASE = "https://api.sofascore.com/api/v1"
SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture
def sent_urls(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    """curl'e giden tam adresleri toplar; her isteğe küçük bir sezon / maç yanıtı döner."""
    urls: List[str] = []

    def sync_get(url: str, **kwargs: Any) -> FakeResponse:
        urls.append(url)
        return FakeResponse(200, json.dumps({"seasons": [{"id": 1, "name": "24/25", "year": "24/25"}], "event": EVENT}))

    monkeypatch.setattr(transport.cffi_requests, "get", sync_get)
    monkeypatch.setattr(utils, "_sleep", lambda seconds: None)
    return urls


def _fetchers(tmp_path: Path) -> Any:
    config = ConfigManager()
    seasons = SeasonFetcher(config, str(tmp_path / "data"))
    return seasons, MatchFetcher(config, seasons, str(tmp_path / "data")), MatchDataFetcher(config, str(tmp_path / "data"))


def test_the_default_api_base_is_unchanged(tmp_path: Path, sent_urls: List[str]) -> None:
    seasons, matches, details = _fetchers(tmp_path)

    assert utils.API_BASE_URL == transport.base_url() == DEFAULT_BASE
    assert {seasons.base_url, matches.base_url, details.base_url} == {DEFAULT_BASE}

    seasons.fetch_seasons_checked(17)
    details._fetch_match_basic("1")
    MatchWatcher._default_fetch("/sport/football/events/live")
    utils.make_api_request("/event/1/statistics")

    assert sent_urls == [
        f"{DEFAULT_BASE}/unique-tournament/17/seasons",
        f"{DEFAULT_BASE}/event/1",
        f"{DEFAULT_BASE}/sport/football/events/live",
        f"{DEFAULT_BASE}/event/1/statistics",
    ]


def test_a_configured_api_base_reaches_every_fetcher_and_the_watcher(
    tmp_path: Path, sent_urls: List[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eskiden yalnızca göreli yollar API_BASE_URL'i kullanıyordu; sezon, detay ve izleyici istekleri sabit adrese gidiyordu."""
    monkeypatch.setattr(utils, "API_BASE_URL", OTHER_BASE)
    seasons, matches, details = _fetchers(tmp_path)

    assert {seasons.base_url, matches.base_url, details.base_url} == {OTHER_BASE}

    seasons.fetch_seasons_checked(17)
    details._fetch_match_basic("1")
    details._fetch_slice_endpoint("1", "statistics")
    MatchWatcher._default_fetch("/sport/football/events/live")
    utils.make_api_request("/event/1/h2h")
    Client().get_sync(endpoints.event(1))

    assert sent_urls == [
        f"{OTHER_BASE}/unique-tournament/17/seasons",
        f"{OTHER_BASE}/event/1",
        f"{OTHER_BASE}/event/1/statistics",
        f"{OTHER_BASE}/sport/football/events/live",
        f"{OTHER_BASE}/event/1/h2h",
        f"{OTHER_BASE}/event/1",
    ]


def test_an_absolute_url_given_by_the_caller_is_sent_as_it_is(sent_urls: List[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(utils, "API_BASE_URL", OTHER_BASE)

    utils.make_api_request(f"{DEFAULT_BASE}/event/1")

    assert sent_urls == [f"{DEFAULT_BASE}/event/1"]


@pytest.mark.parametrize("base", [DEFAULT_BASE, OTHER_BASE])
def test_the_bridge_fallback_gets_the_full_address(
    fake: FakeSofaScore, monkeypatch: pytest.MonkeyPatch, base: str
) -> None:
    """Köprü göreli yolu kendi sabit köküyle tamamlar; ayarlı kök ona da ulaşsın diye tam adres verilir."""
    monkeypatch.setattr(utils, "API_BASE_URL", base)
    fake.fail("/event/*", 403, body=CHALLENGE_BODY)
    sync_bridge = MagicMock(return_value={"ok": 1})
    async_bridge = AsyncMock(return_value={"ok": 1})

    async def fetch_async(path: str) -> Any:
        async with utils.create_session_async() as session:
            return await utils.make_api_request_async(session, path)

    with patch("src.challenge_solver.fetch_api_via_browser_sync", sync_bridge), \
            patch("src.challenge_solver.fetch_api_via_browser", async_bridge):
        assert utils.make_api_request("/event/1") == {"ok": 1}  # 403 challenge → köprü
        assert utils.make_api_request("/event/2") == {"ok": 1}  # "önce tarayıcı" modu
        monkeypatch.setattr(utils, "_browser_first_until", 0.0)
        assert asyncio.run(fetch_async("/event/3")) == {"ok": 1}
        assert asyncio.run(fetch_async("/event/4")) == {"ok": 1}

    assert [call.args for call in sync_bridge.call_args_list] == [(f"{base}/event/1",), (f"{base}/event/2",)]
    assert [call.args for call in async_bridge.await_args_list] == [(f"{base}/event/3",), (f"{base}/event/4",)]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, DEFAULT_BASE),
        ("", DEFAULT_BASE),  # .env'de boş bırakılan satır: eskiden göreli yollar köksüz kalıyordu
        ("   ", DEFAULT_BASE),
        (OTHER_BASE, OTHER_BASE),
        (OTHER_BASE + "/", OTHER_BASE),  # yollar "/" ile başlar: çift "/" olmasın
        (f"  {OTHER_BASE}  ", OTHER_BASE),
    ],
)
def test_the_api_base_setting_is_normalised(monkeypatch: pytest.MonkeyPatch, value: Any, expected: str) -> None:
    if value is None:
        monkeypatch.delenv("API_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("API_BASE_URL", value)

    assert transport._configured_base_url() == expected


def test_no_module_of_the_request_path_hard_codes_the_api_base() -> None:
    """API kökü yalnızca src/client/endpoints.py'de yazılıdır; aşağıdakiler bu işin dışında kalan bilinen yerlerdir."""
    known_elsewhere = {
        "config_manager.py",  # get_api_base_url'in varsayılanı
        "config/settings.py",  # ayar modelindeki varsayılan
        "challenge_solver.py",  # tarayıcı köprüsü: göreli yolların kökü ve yoklama adresi
        "web/routes/leagues.py",  # lig arama
    }
    hard_coded = {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "sofascore.com/api/v1" in path.read_text(encoding="utf-8")
    }

    assert "client/endpoints.py" in hard_coded
    assert hard_coded - {"client/endpoints.py"} <= known_elsewhere


# --- katman ve içe aktarma sırası ---------------------------------------------------------------------

def _imports_of(path: Path) -> List[str]:
    found: List[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.extend([node.module, *(f"{node.module}.{alias.name}" for alias in node.names)])
    return found


def test_the_client_package_imports_neither_the_store_nor_a_face() -> None:
    """docs/design/02-services.md 2.1: src/client ve src/store birbirini içe aktarmaz; istemci yüzleri de bilmez."""
    forbidden = ("src.store", "src.web", "src.ui", "src.services", "src.jobs", "src.utils", "src.fsutil")
    problems = [
        f"{path.name}: {module}"
        for path in sorted((SRC / "client").glob("*.py"))
        for module in _imports_of(path)
        if any(module == name or module.startswith(name + ".") for name in forbidden)
    ]

    assert problems == []


@pytest.mark.parametrize("module", ["src.client.transport", "src.client.context", "src.client.endpoints", "src.utils"])
def test_any_of_the_modules_can_be_the_first_one_imported(module: str) -> None:
    """src.utils ↔ src.client arasında içe aktarma döngüsü yok: hangisi önce yüklenirse yüklensin çalışır."""
    code = (
        f"import {module}\n"
        "import src.utils as utils\n"
        "from src.client import transport\n"
        "assert utils.make_api_request is transport.make_api_request\n"
        "assert type(utils).__name__ == '_UtilsModule'\n"
        "print('ok')\n"
    )

    result = subprocess.run([sys.executable, "-c", code], cwd=SRC.parent, capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("ok")
