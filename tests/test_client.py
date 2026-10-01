"""
src/client: istek katmanının yeni yeri ve üzerindeki Client yüzü (plan maddesi P05).

Ağ yok: istekler tests/fakes/sofascore.py'deki sahte taşıyıcıya gider; istek katmanının kendisi gerçektir.
"""
from __future__ import annotations

from typing import Iterator
from unittest.mock import patch

import pytest

import src.utils as utils
from fakes.sofascore import REQUEST_LAYER_MODULES, FakeSofaScore
from src.client import context, transport

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
