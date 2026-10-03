"""
API v1'in hata modeli, istek kimliği ve erişim belirteci (plan maddesi P20; docs/design/02-services.md 2.6 ve 6).

  * hata tablosundaki her kod, tablodaki HTTP durumuyla ve `{"error": {...}}` gövdesiyle döner;
  * devralınan istisnalar (StorageError, LeaseHeld, iş deposunun çakışmaları, ConfigError) kodlarına çevrilir;
  * çatının kendi hataları (bilinmeyen yol, yanlış yöntem, doğrulama) v1 yollarında aynı modeldedir;
  * eski yolların hata gövdeleri değişmez;
  * belirteç kapalıyken v1 açıktır; açıkken `unauthorized` (401) döner, eski yollar `auth_required` kalır;
  * belirteç Settings'ten okunur (`[server] token_env`);
  * art arda gelen yanlış belirteçler istemci başına sınırlanır.
"""
from __future__ import annotations

import errno
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest
from fastapi.testclient import TestClient

from src import redact
from src.errors import (
    ERROR_TABLE,
    ERRORS,
    ConflictError,
    NotFoundError,
    PlatformError,
    UsageError,
)
from src.exceptions import ConfigError, StorageError
from src.store import DataOperationRunningError, JobRunningError, LeaseHeld, SchemaTooNew
from src.web import deps, errors, security
from src.web.api import is_v1
from src.web.app import LOGIN_PATH, app
from src.web.deps import AttemptLimiter

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "t0ken-Zx9-correct-horse-battery-staple"
# Bir v1 rotası: gövdesi `deps.job_manager()` çağırır, testler onu istenen hatayı fırlatacak biçimde değiştirir
PROBE = "/api/v1/jobs/probe"

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_limiter() -> Iterator[None]:
    """Deneme sayacı süreç genelidir: her test boş sayaçla başlar ve arkasında kilit bırakmaz."""
    deps.attempt_limiter.reset()
    yield
    deps.attempt_limiter.reset()


@pytest.fixture
def raising(monkeypatch: pytest.MonkeyPatch) -> Any:
    """PROBE rotasının fırlatacağı istisnayı ayarlar."""

    def arrange(exc: BaseException) -> None:
        def boom() -> Any:
            raise exc

        monkeypatch.setattr(deps, "job_manager", boom)

    return arrange


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Uygulama bu erişim belirteciyle başlamış gibi."""
    monkeypatch.setenv(security.TOKEN_ENV, TOKEN)
    monkeypatch.setattr(security, "_startup_token", TOKEN)
    redact.refresh()
    yield TOKEN
    monkeypatch.undo()
    redact.refresh()


def _error(response: Any) -> Dict[str, Any]:
    body = response.json()
    assert list(body) == ["error"], body
    assert list(body["error"]) == ["code", "message", "details", "request_id"]
    assert response.headers["x-request-id"] == body["error"]["request_id"]
    return body["error"]


# --- hata tablosu ------------------------------------------------------------------------------------

HTTP_CODES = [spec.code for spec in ERROR_TABLE if spec.http_status and spec.raised]


@pytest.mark.parametrize("code", HTTP_CODES)
def test_every_code_of_the_error_table_is_answered_with_its_status(code: str, raising: Any) -> None:
    raising(PlatformError(code, f"message of {code}", {"n": 1}))

    r = client.get(PROBE)

    spec = ERRORS[code]
    assert r.status_code == spec.http_status[0]
    assert r.headers["content-type"] == "application/json"
    error = _error(r)
    assert (error["code"], error["message"], error["details"]) == (code, f"message of {code}", {"n": 1})


def test_the_table_has_a_status_for_every_code_but_the_one_http_cannot_carry() -> None:
    without = [spec.code for spec in ERROR_TABLE if not spec.http_status]
    assert without == ["cancelled"]
    assert errors.http_status(PlatformError("cancelled", "stopped")) == 500
    assert ERRORS["partial"].http_status == (200,) and not ERRORS["partial"].raised


def test_invalid_request_is_400_for_a_refusal_and_422_for_a_rejected_value(raising: Any) -> None:
    raising(UsageError("cannot be done", {"locked": ["client.rate"]}))
    refused = client.get(PROBE)
    assert refused.status_code == 400 and _error(refused)["code"] == "invalid_request"

    raising(errors.ValidationFailed("not valid", {"errors": [{"loc": ["body", "x"], "message": "bad", "type": "t"}]}))
    rejected = client.get(PROBE)
    assert rejected.status_code == 422
    assert _error(rejected) == {
        "code": "invalid_request", "message": "not valid",
        "details": {"errors": [{"loc": ["body", "x"], "message": "bad", "type": "t"}]},
        "request_id": rejected.headers["x-request-id"],
    }


def test_error_responses_documents_the_statuses_of_the_table() -> None:
    documented = errors.error_responses("job_running", "data_operation_running", "not_found", "invalid_request")
    assert list(documented) == [400, 404, 409, 422]
    assert documented[409] == {"model": errors.ApiErrorResponse, "description": "job_running, data_operation_running"}
    assert errors.validation_response() == {422: {"model": errors.ApiErrorResponse, "description": "invalid_request"}}


# --- devralınan istisnalar -----------------------------------------------------------------------------


def test_a_platform_error_subclass_keeps_its_code_and_details(raising: Any) -> None:
    raising(ConflictError("the follow comes from the config file", {"follow": 3}, code="follow_managed"))
    r = client.get(PROBE)
    assert r.status_code == 409
    assert _error(r)["code"] == "follow_managed" and _error(r)["details"] == {"follow": 3}

    raising(NotFoundError("no such thing"))
    assert client.get(PROBE).status_code == 404


def test_a_storage_error_with_an_os_reason_gets_an_english_message(raising: Any) -> None:
    raising(StorageError.from_exception(OSError(errno.ENOSPC, "No space left on device"), "/data/match_details/17"))

    r = client.get(PROBE)

    assert r.status_code == 507
    error = _error(r)
    assert error["code"] == "storage_error"
    assert error["message"] == "storage error: No space left on device (/data/match_details/17)"
    assert error["details"] == {
        "class": "StorageError", "path": "/data/match_details/17", "errno": errno.ENOSPC,
        "reason": "No space left on device",
    }


def test_a_store_error_without_a_reason_keeps_the_stores_text_in_the_details(raising: Any) -> None:
    """Deponun iletisi Türkçedir; v1'in `message` alanı İngilizce kalır ve deponun metni ayrıntıya gider."""
    raising(SchemaTooNew("state.db bu koddan yeni (şema 9)", path="/data/.meta/state.db"))

    error = _error(client.get(PROBE))

    assert error["code"] == "storage_error" and error["message"] == errors.STORAGE_MESSAGE
    assert error["details"]["class"] == "SchemaTooNew" and error["details"]["path"] == "/data/.meta/state.db"
    assert error["details"]["store_message"] == "state.db bu koddan yeni (şema 9)"
    assert error["message"].isascii()


@pytest.mark.parametrize("name,purpose,code", [
    ("writer", "job", "job_running"),
    ("writer", "op:backup", "data_operation_running"),
    ("maintenance", "op:clear", "data_operation_running"),
    ("live", "", "instance_running"),
])
def test_a_held_lease_is_a_conflict_with_the_holder_in_the_details(name: str, purpose: str, code: str, raising: Any) -> None:
    raising(LeaseHeld(name=name, pid=4242, host="box", purpose=purpose, started_at=0.0))

    r = client.get(PROBE)

    assert r.status_code == 409
    error = _error(r)
    assert error["code"] == code and error["message"].isascii()
    assert error["details"] == {"holder": {
        "lease": name, "pid": 4242, "host": "box", "purpose": purpose or None, "since": "1970-01-01T00:00:00Z",
    }}


def test_a_job_store_conflict_is_a_conflict_and_names_the_holder_when_another_process_has_the_lease(
    raising: Any,
) -> None:
    raising(JobRunningError())
    r = client.get(PROBE)
    assert r.status_code == 409
    assert _error(r)["code"] == "job_running" and _error(r)["details"] is None

    raising(DataOperationRunningError("clear"))
    r = client.get(PROBE)
    assert r.status_code == 409 and _error(r)["code"] == "data_operation_running"
    assert "clear" in _error(r)["message"]

    conflict = JobRunningError()
    conflict.__cause__ = LeaseHeld(name="writer", pid=7, host="other", purpose="headless", started_at=60.0)
    raising(conflict)
    error = _error(client.get(PROBE))
    assert error["code"] == "job_running"
    assert error["details"]["holder"] == {
        "lease": "writer", "pid": 7, "host": "other", "purpose": "headless", "since": "1970-01-01T00:01:00Z",
    }


def test_a_config_error_is_config_invalid(raising: Any) -> None:
    raising(ConfigError("sofascore.toml: [client] rate: expected a number or \"off\", got 'fast'"))
    r = client.get(PROBE)
    assert r.status_code == 500
    assert _error(r)["code"] == "config_invalid" and "expected a number" in _error(r)["message"]


def test_an_unexpected_error_is_internal_and_its_text_stays_in_the_log(
    raising: Any, caplog: pytest.LogCaptureFixture
) -> None:
    raising(RuntimeError("connect to http://user:hunter2@proxy.example:8080 failed"))

    with caplog.at_level(logging.ERROR, logger="WebAPI"):
        r = client.get(PROBE, headers={"x-request-id": "trace-77"})

    assert r.status_code == 500
    error = _error(r)
    assert error == {"code": "internal", "message": errors.INTERNAL_MESSAGE, "details": None, "request_id": "trace-77"}
    assert "hunter2" not in r.text and "RuntimeError" not in r.text
    logged = [record.getMessage() for record in caplog.records if record.name == "WebAPI"]
    assert len(logged) == 1
    assert "trace-77" in logged[0] and "RuntimeError" in logged[0] and PROBE in logged[0]
    # Güvenlik başlıkları hata yanıtında da durur
    assert r.headers["x-content-type-options"] == "nosniff" and "content-security-policy" in r.headers


def test_an_internal_platform_error_keeps_its_message(raising: Any) -> None:
    raising(PlatformError("internal", "the catalog answered with two rows for one id"))
    error = _error(client.get(PROBE))
    assert error["message"] == "the catalog answered with two rows for one id"


# --- çatının hataları --------------------------------------------------------------------------------


def test_an_unknown_v1_path_is_not_found_in_the_error_model() -> None:
    for path in ("/api/v1/nope", "/api/v1", "/api/v1/jobs/1/2/3"):
        r = client.get(path)
        assert r.status_code == 404, path
        assert _error(r)["code"] == "not_found"


def test_a_wrong_method_keeps_its_status_and_is_an_invalid_request() -> None:
    r = client.delete("/api/v1/health")
    assert r.status_code == 405
    assert _error(r)["code"] == "invalid_request"


def test_a_rejected_parameter_is_422_and_does_not_echo_the_input() -> None:
    r = client.get("/api/v1/jobs", params={"limit": "0", "state": "sleeping"})

    assert r.status_code == 422
    error = _error(r)
    assert error["code"] == "invalid_request" and error["message"] == "The request is not valid."
    found = {tuple(item["loc"]): item for item in error["details"]["errors"]}
    assert set(found) == {("query", "limit"), ("query", "state", "0")}
    assert all(set(item) == {"loc", "message", "type"} for item in found.values())
    assert "sleeping" not in json.dumps([item["loc"] for item in found.values()])


def test_a_rejected_body_does_not_return_what_was_sent() -> None:
    secret = "http://user:hunter2@proxy.example:8080"
    r = client.post("/api/v1/jobs", json={"kind": "sync", "spec": {"league_id": secret}})
    assert r.status_code == 422
    assert _error(r)["code"] == "invalid_request"
    assert "hunter2" not in r.text


# --- istek kimliği -----------------------------------------------------------------------------------


def test_every_v1_response_carries_a_request_id() -> None:
    first, second = client.get("/api/v1/health"), client.get("/api/v1/health")
    ids = [first.headers["x-request-id"], second.headers["x-request-id"]]
    assert all(len(i) == 16 and int(i, 16) >= 0 for i in ids) and ids[0] != ids[1]


def test_a_request_id_sent_by_the_client_is_used_when_it_is_safe() -> None:
    assert client.get("/api/v1/health", headers={"x-request-id": "abc.DEF-123_x"}).headers["x-request-id"] == "abc.DEF-123_x"
    for unsafe in ("a b", "x" * 65, "<script>", "id;drop"):
        got = client.get("/api/v1/health", headers={"x-request-id": unsafe}).headers["x-request-id"]
        assert got != unsafe and len(got) == 16


def test_legacy_routes_get_no_request_id() -> None:
    for path in ("/api/status", "/health", "/api/nope"):
        assert "x-request-id" not in client.get(path).headers


# --- eski yollar değişmedi ---------------------------------------------------------------------------


def test_legacy_error_bodies_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.get("/api/nope").json() == {"detail": "Not Found"}
    assert client.get("/api/jobs/unknown").json() == {"detail": "Job not found"}
    validation = client.get("/api/jobs", params={"limit": 0})
    assert validation.status_code == 422 and isinstance(validation.json()["detail"], list)

    from src.web.routes import scrape

    def conflict(job_id: str) -> Any:
        raise JobRunningError()

    monkeypatch.setattr(scrape._job_store, "get_job", conflict)
    r = client.get("/api/jobs/x")
    assert r.status_code == 409
    assert r.json() == {"detail": {"code": "job_running", "message": str(JobRunningError())}}


def test_an_unexpected_error_of_a_legacy_route_is_not_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.web.routes import scrape

    def boom(job_id: str) -> Any:
        raise RuntimeError("legacy boom")

    monkeypatch.setattr(scrape._job_store, "get_job", boom)
    with pytest.raises(RuntimeError, match="legacy boom"):
        client.get("/api/jobs/x")
    r = TestClient(app, raise_server_exceptions=False).get("/api/jobs/x")
    assert r.status_code == 500 and r.text == "Internal Server Error"


def test_a_platform_error_of_a_legacy_route_is_not_given_the_v1_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.web.routes import scrape

    def boom(job_id: str) -> Any:
        raise NotFoundError("gone")

    monkeypatch.setattr(scrape._job_store, "get_job", boom)
    r = TestClient(app, raise_server_exceptions=False).get("/api/jobs/x")
    assert r.status_code == 500 and "error" not in r.text


# --- kaynak denetimi ---------------------------------------------------------------------------------


def test_a_cross_origin_write_to_v1_is_forbidden_origin() -> None:
    for headers in ({"sec-fetch-site": "cross-site"}, {"origin": "https://evil.example"}):
        r = client.post("/api/v1/jobs/x/cancel", headers=headers)
        assert r.status_code == 403
        error = _error(r)
        assert (error["code"], error["message"]) == ("forbidden_origin", "Cross-origin request rejected.")
    # Eski yolların gövdesi aynı
    legacy = client.post("/api/scrape/cancel", headers={"sec-fetch-site": "cross-site"})
    assert legacy.json() == {"detail": "Cross-origin request rejected"}
    # Okuma reddedilmez
    assert client.get("/api/v1/health", headers={"sec-fetch-site": "cross-site"}).status_code == 200


# --- erişim belirteci --------------------------------------------------------------------------------

V1_READS = ("/api/v1/health", "/api/v1/status", "/api/v1/sports", "/api/v1/jobs", "/api/v1/settings")


def test_without_a_token_v1_is_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "_startup_token", "")
    for path in V1_READS:
        assert client.get(path).status_code == 200, path
    assert client.get("/api/v1/status").json()["data"]["auth_required"] is False


def test_with_a_token_v1_answers_unauthorized(token: str) -> None:
    anonymous = TestClient(app)
    for path in (*V1_READS, "/api/v1/nope", "/api/v1/jobs/x/events"):
        r = anonymous.get(path)
        assert r.status_code == 401, path
        assert r.headers["www-authenticate"] == "Bearer"
        error = _error(r)
        assert (error["code"], error["message"], error["details"]) == ("unauthorized", "An access token is required.", None)
    assert anonymous.post("/api/v1/jobs", json={"kind": "sync"}).status_code == 401
    # Eski yollar kendi kodunu korur
    assert anonymous.get("/api/status").json() == {
        "detail": {"code": "auth_required", "message": "An access token is required."},
    }
    # /health açık kalır ve belirteçsiz çağırana yalnızca "ayakta" der
    assert anonymous.get("/health").json() == {"status": "ok"}


def test_with_a_token_v1_accepts_the_bearer_header_and_the_session_cookie(token: str) -> None:
    bearer = {"authorization": f"Bearer {token}"}
    for path in V1_READS:
        assert TestClient(app).get(path, headers=bearer).status_code == 200, path
    assert TestClient(app).get("/api/v1/status", headers=bearer).json()["data"]["auth_required"] is True

    browser = TestClient(app)
    assert browser.post(LOGIN_PATH, json={"token": token}).status_code == 200
    assert browser.get("/api/v1/jobs").status_code == 200
    # SSE başlık gönderemez: olay akışı da cookie ile açılır (bilinmeyen iş 404, 401 değil)
    assert browser.get("/api/v1/jobs/unknown/events").status_code == 404

    wrong = TestClient(app).get("/api/v1/jobs", headers={"authorization": "Bearer not-the-token"})
    assert wrong.status_code == 401 and _error(wrong)["code"] == "unauthorized"
    assert token not in wrong.text


def test_the_token_is_never_part_of_a_v1_response(token: str) -> None:
    bearer = {"authorization": f"Bearer {token}"}
    c = TestClient(app)
    for path in V1_READS:
        assert token not in c.get(path, headers=bearer).text, path
    rows = {row["key"]: row for row in c.get("/api/v1/settings", headers=bearer).json()["data"]["settings"]}
    assert rows["server.token"]["value"] == "***" and rows["server.token"]["secret"] is True


# --- her v1 rotası eski rotalarla aynı korumanın arkasında -------------------------------------------
#
# PR #43'ün korumaları (src/web/security.py) yola değil `/api` önekine bağlıdır; aşağıdaki testler bunu v1
# belgesindeki her işlem için tek tek doğrular. Yeni bir v1 rotası eklendiğinde kendiliğinden listeye girer.

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
SECURITY_HEADERS = (
    "x-content-type-options", "x-frame-options", "referrer-policy", "cross-origin-resource-policy",
    "content-security-policy",
)
# Denetimi geçseydi bir şey değiştirecek gövdeler: ret, isteğin içeriğinden bağımsızdır
WRITE_BODIES: Dict[str, Dict[str, Any]] = {
    "/api/v1/jobs": {"kind": "sync"},
    "/api/v1/settings": {"values": {"client.retries": 9}},
    "/api/v1/status/check": {"target": "sofascore"},
    "/api/v1/auth/login": {"token": "not-the-token"},
}


def _v1_operations() -> List[Tuple[str, str]]:
    """(yöntem, örnek yol) çiftleri: OpenAPI belgesindeki her v1 işlemi, yol parametreleri doldurulmuş."""
    out: List[Tuple[str, str]] = []
    # Belge, FastAPI sürümünden bağımsız olarak kayıtlı her yolu ve yöntemi verir (`app.routes` vermez)
    for template, operations in app.openapi()["paths"].items():
        if not is_v1(template):
            continue
        path = re.sub(r"\{[a-z_]+\}", "x", template)
        out += [(method.upper(), path) for method in operations if method in HTTP_METHODS]
    assert len(out) >= 10, out
    return sorted(out)


V1_OPERATIONS = _v1_operations()
V1_WRITES = [(method, path) for method, path in V1_OPERATIONS if method in security.UNSAFE_METHODS]
_IDS = [f"{method} {path}" for method, path in V1_OPERATIONS]
_WRITE_IDS = [f"{method} {path}" for method, path in V1_WRITES]
# Oturum yolları belirteç olmadan da yanıt verir (security.AUTH_OPEN_PATHS); geri kalan her v1 işlemi onu ister
V1_PROTECTED = [(method, path) for method, path in V1_OPERATIONS if path not in security.AUTH_OPEN_PATHS]
_PROTECTED_IDS = [f"{method} {path}" for method, path in V1_PROTECTED]


@pytest.fixture
def reached(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    """Bir v1 rotasının gövdesine girildiyse adını kaydeder (rotalar bağlamlarını `deps` üzerinden alır)."""
    calls: List[str] = []
    for name in ("job_manager", "job_store", "loaded_settings", "config_manager"):
        real = getattr(deps, name)

        def recorded(*args: Any, _name: str = name, _real: Any = real, **kwargs: Any) -> Any:
            calls.append(_name)
            return _real(*args, **kwargs)

        monkeypatch.setattr(deps, name, recorded)
    return calls


def test_the_v1_operation_list_has_reads_and_writes() -> None:
    assert ("GET", "/api/v1/health") in V1_OPERATIONS and ("GET", "/api/v1/jobs/x/events") in V1_OPERATIONS
    assert V1_WRITES == [
        ("PATCH", "/api/v1/settings"), ("POST", "/api/v1/auth/login"), ("POST", "/api/v1/auth/logout"),
        ("POST", "/api/v1/jobs"), ("POST", "/api/v1/jobs/x/cancel"), ("POST", "/api/v1/status/check"),
    ]
    assert [path for _method, path in V1_OPERATIONS if path in security.AUTH_OPEN_PATHS] == [
        "/api/v1/auth", "/api/v1/auth/login", "/api/v1/auth/logout",
    ]


def test_a_state_changing_route_that_runs_is_seen_by_the_probe(reached: List[str]) -> None:
    """Aşağıdaki `reached == []` iddialarının karşılığı: reddedilmeyen bir yazma isteği kayda geçer."""
    assert client.post("/api/v1/jobs/x/cancel").status_code == 404
    assert reached == ["job_manager", "job_store"]
    del reached[:]
    assert client.patch("/api/v1/settings", json={"values": {"server.host": "0.0.0.0"}}).status_code == 400
    assert "loaded_settings" not in reached  # salt okunur anahtar: ayarlara bakılmadan reddedilir
    assert client.patch("/api/v1/settings", json={"values": {"client.retries": "x"}}).status_code == 422
    assert "loaded_settings" in reached


@pytest.mark.parametrize("method,path", V1_PROTECTED, ids=_PROTECTED_IDS)
def test_with_a_token_every_v1_route_needs_it(token: str, reached: List[str], method: str, path: str) -> None:
    """Okuma, yazma ve olay akışı: belirteç ayarlıysa hiçbiri onsuz yanıt vermez ve rotaya girilmez."""
    credentials: Tuple[Dict[str, str], ...] = (
        {},
        {"authorization": "Bearer not-the-token"},
        {"authorization": f"Basic {token}"},
        {"cookie": f"{security.SESSION_COOKIE}=forged"},
    )
    for headers in credentials:
        r = TestClient(app).request(method, path, headers=headers, json=WRITE_BODIES.get(path))
        assert r.status_code == 401, headers
        assert r.headers["www-authenticate"] == "Bearer"
        error = _error(r)
        assert (error["code"], error["message"], error["details"]) == ("unauthorized", "An access token is required.", None)
        assert token not in r.text
        for name in SECURITY_HEADERS:
            assert name in r.headers, name
    # Belirteç adres satırında taşınmaz
    assert TestClient(app).request(method, path, params={"token": token, "access_token": token}).status_code == 401
    assert reached == []


@pytest.mark.parametrize("method,path", V1_OPERATIONS, ids=_IDS)
def test_with_the_token_every_v1_route_is_reached(token: str, method: str, path: str) -> None:
    """Doğru belirteçle (başlık ya da oturum cookie'si) ret gelmez; gövdesiz istek yalnızca doğrulamada düşer."""
    browser = TestClient(app)
    assert browser.post(LOGIN_PATH, json={"token": token}).status_code == 200
    for response in (
        TestClient(app).request(method, path, headers={"authorization": f"Bearer {token}"}),
        browser.request(method, path),
    ):
        assert response.status_code in (200, 404, 422), response.text
        assert response.status_code != 404 or _error(response)["code"] == "not_found"


@pytest.mark.parametrize("method,path", V1_OPERATIONS, ids=_IDS)
def test_every_v1_route_is_behind_the_host_check(token: str, reached: List[str], method: str, path: str) -> None:
    """Bilinmeyen Host (DNS rebinding) her şeyden önce reddedilir: belirteç doğru olsa da, kaynak aynı olsa da."""
    for headers in (
        {"host": "evil.example"},
        {"host": "evil.example", "authorization": f"Bearer {token}"},
        {"host": "evil.example", "authorization": f"Bearer {token}", "sec-fetch-site": "same-origin"},
    ):
        r = TestClient(app).request(method, path, headers=headers, json=WRITE_BODIES.get(path))
        assert (r.status_code, r.text) == (400, "Invalid host header"), headers
    assert reached == []


def test_the_host_check_covers_v1_without_a_token_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "_startup_token", "")
    for method, path in V1_OPERATIONS:
        r = client.request(method, path, headers={"host": "192.168.1.5:8000"}, json=WRITE_BODIES.get(path))
        assert (r.status_code, r.text) == (400, "Invalid host header"), (method, path)
    assert client.get("/api/v1/health", headers={"host": "localhost:8000"}).status_code == 200


@pytest.mark.parametrize("method,path", V1_WRITES, ids=_WRITE_IDS)
def test_every_state_changing_v1_route_is_behind_the_origin_check(
    token: str, reached: List[str], method: str, path: str,
) -> None:
    """
    İş başlatma, iptal ve ayar yazma başka bir siteden tetiklenemez: oturumu açık bir tarayıcıdan (cookie
    kendiliğinden gider) ya da belirteci taşıyan bir istekten gelse de. Rotaya girilmez.
    """
    browser = TestClient(app)
    assert browser.post(LOGIN_PATH, json={"token": token}).status_code == 200
    bearer = {"authorization": f"Bearer {token}"}
    cross_site: Tuple[Dict[str, str], ...] = (
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site", "origin": "http://testserver:3000"},
        {"origin": "https://evil.example"},
        {"origin": "null"},
    )
    for headers in cross_site:
        for r in (
            browser.request(method, path, headers=headers, json=WRITE_BODIES.get(path)),
            TestClient(app).request(method, path, headers={**headers, **bearer}, json=WRITE_BODIES.get(path)),
            TestClient(app).request(method, path, headers=headers, json=WRITE_BODIES.get(path)),
        ):
            assert r.status_code == 403, headers
            error = _error(r)
            assert (error["code"], error["message"]) == ("forbidden_origin", "Cross-origin request rejected.")
    assert reached == []


@pytest.mark.parametrize("method,path", V1_WRITES, ids=_WRITE_IDS)
def test_the_origin_check_lets_the_app_and_programs_write_to_v1(method: str, path: str) -> None:
    """
    Aynı kaynaktan gelen tarayıcı isteği ve Origin göndermeyen program reddedilmez (gövdesiz: 404 ya da 422;
    gövdesi olmayan çıkış 200).
    """
    expected = (200,) if path == "/api/v1/auth/logout" else (404, 422)
    for headers in ({}, {"sec-fetch-site": "same-origin", "origin": "http://testserver"}, {"sec-fetch-site": "none"}):
        assert client.request(method, path, headers=headers).status_code in expected, headers


def test_v1_responses_carry_the_response_headers_of_the_legacy_routes() -> None:
    """PR #43'ün yanıt başlıkları: başarılı, hatalı ve reddedilen her v1 yanıtında, eski rotalardaki değerlerle."""
    legacy = client.get("/api/status")
    responses = {
        "ok": client.get("/api/v1/health"),
        "not found": client.get("/api/v1/nope"),
        "validation": client.get("/api/v1/jobs", params={"limit": 0}),
        "wrong method": client.delete("/api/v1/health"),
        "cross-origin": client.post("/api/v1/jobs/x/cancel", headers={"sec-fetch-site": "cross-site"}),
        "event stream": client.get("/api/v1/jobs/x/events"),
    }
    for what, r in responses.items():
        for name in SECURITY_HEADERS:
            assert r.headers[name] == legacy.headers[name], (what, name)


# --- belirteç Settings'ten okunur --------------------------------------------------------------------

_TOKEN_ENV_PROBE = """
import json
from fastapi.testclient import TestClient
from src.web import security
from src.web.app import app
from src.config import active_settings
client = TestClient(app)
print(json.dumps({
    "security": security.api_token(),
    "settings": active_settings().server.token,
    "anonymous": client.get("/api/v1/health").status_code,
    "bearer": client.get("/api/v1/health", headers={"authorization": "Bearer " + security.api_token()}).status_code,
    "legacy": client.get("/api/status").status_code,
}))
"""


def _run_app(tmp_path: Path, config: str, extra_env: Dict[str, str]) -> subprocess.CompletedProcess:
    """Uygulamayı ayrı bir süreçte, kendi yapılandırma dosyası ve dizinleriyle yükler."""
    config_file = tmp_path / "sofascore.toml"
    config_file.write_text(config, encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / ".env").write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in (security.TOKEN_ENV, "MY_SCRAPER_TOKEN")}
    env.update(
        SOFASCORE_CONFIG=str(config_file), DATA_DIR=str(tmp_path / "data"),
        SOFASCORE_CONFIG_DIR=str(tmp_path / "config"), SOFASCORE_ENV_FILE=str(tmp_path / ".env"),
        LOG_DIR=str(tmp_path / "logs"), SOFASCORE_BROWSER_PROFILE=str(tmp_path / "profile"),
        SOFASCORE_ALLOWED_HOSTS="testserver", **extra_env,
    )
    return subprocess.run(
        [sys.executable, "-c", _TOKEN_ENV_PROBE], cwd=str(ROOT), env=env, capture_output=True, text=True,
        timeout=120, check=False,
    )


def test_the_token_named_by_token_env_protects_the_api(tmp_path: Path) -> None:
    """`[server] token_env` başka bir değişkenin adını verir: belirteç oradan okunur ve /api onu ister."""
    done = _run_app(tmp_path, '[server]\ntoken_env = "MY_SCRAPER_TOKEN"\n', {"MY_SCRAPER_TOKEN": TOKEN})

    assert done.returncode == 0, done.stderr[-2000:]
    seen = json.loads(done.stdout.strip().splitlines()[-1])
    assert seen == {"security": TOKEN, "settings": TOKEN, "anonymous": 401, "bearer": 200, "legacy": 401}
    assert TOKEN not in done.stdout.replace(json.dumps(seen), "") and TOKEN not in done.stderr


def test_a_token_env_that_names_an_unset_variable_stops_the_start(tmp_path: Path) -> None:
    """Yazım hatası korumayı sessizce kapatmaz: uygulama yüklenmez ve neden söylenir."""
    done = _run_app(tmp_path, '[server]\ntoken_env = "MY_SCRAPER_TOKEN"\n', {})

    assert done.returncode != 0
    assert "MY_SCRAPER_TOKEN is not set" in done.stderr


def test_the_default_token_variable_still_works_without_a_config_file(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.web import app as app_module

    monkeypatch.setenv(security.TOKEN_ENV, f"  {TOKEN} ")
    monkeypatch.setattr(security, "_startup_token", None)  # süreç yeni başlıyor
    assert app_module._token_from_settings() == TOKEN
    assert security.api_token() == TOKEN == deps.server_token()


# --- başarısız deneme sınırı -------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limiter_locks_after_the_free_attempts_and_doubles_each_time() -> None:
    clock = _Clock()
    limiter = AttemptLimiter(free_attempts=3, base_lock=30, max_lock=100, forget_after=600, clock=clock)

    assert [limiter.failure("a"), limiter.failure("a")] == [0.0, 0.0]
    assert limiter.retry_after("a") == 0
    assert limiter.failure("a") == 30 and limiter.retry_after("a") == 30
    clock.now += 29
    assert limiter.retry_after("a") == 1
    clock.now += 1
    assert limiter.retry_after("a") == 0
    assert limiter.failure("a") == 60          # kilit bittikten sonraki ilk yanlış deneme: iki katı
    clock.now += 60
    assert limiter.failure("a") == 100         # üst sınır
    assert limiter.retry_after("b") == 0       # istemci başınadır
    # Sayaç ne kadar büyürse büyüsün süre üst sınırda kalır (üs taşmaz)
    limiter._clients["a"].failures = 10_000
    clock.now += 100
    assert limiter.failure("a") == 100


def test_limiter_forgets_after_a_success_and_after_a_quiet_period() -> None:
    clock = _Clock()
    limiter = AttemptLimiter(free_attempts=3, base_lock=30, forget_after=600, clock=clock)

    limiter.failure("a")
    limiter.failure("a")
    limiter.success("a")
    assert [limiter.failure("a"), limiter.failure("a")] == [0.0, 0.0]   # sayaç sıfırdan başladı

    clock.now += 601
    assert limiter.failure("a") == 0.0                                    # sessiz geçen süre sayacı unutturur
    limiter.reset()
    assert limiter.retry_after("a") == 0


def test_limiter_keeps_a_bounded_number_of_clients() -> None:
    clock = _Clock()
    limiter = AttemptLimiter(free_attempts=2, max_clients=3, forget_after=600, clock=clock)
    for n in range(10):
        clock.now += 1
        limiter.failure(f"client-{n}")
    assert len(limiter._clients) == 3 and "client-9" in limiter._clients and "client-0" not in limiter._clients


def test_presents_bearer_and_client_key() -> None:
    assert deps.presents_bearer({"authorization": "Bearer abc"}) and deps.presents_bearer({"authorization": "bearer  abc"})
    for headers in ({}, {"authorization": "Bearer "}, {"authorization": "Basic abc"}, {"authorization": "abc"}):
        assert not deps.presents_bearer(headers)
    assert deps.client_key(None) == "unknown"


def test_repeated_wrong_logins_are_refused_for_a_while(token: str, caplog: pytest.LogCaptureFixture) -> None:
    c = TestClient(app)
    free = deps.attempt_limiter.free_attempts
    with caplog.at_level(logging.WARNING, logger="WebApp"):
        for attempt in range(free):
            r = c.post(LOGIN_PATH, json={"token": f"guess-{attempt}"})
            assert r.status_code == 401 and r.json()["detail"]["code"] == "invalid_token"

        locked = c.post(LOGIN_PATH, json={"token": "guess-again"})

    assert locked.status_code == 429
    detail = locked.json()["detail"]
    assert detail["code"] == "too_many_attempts" and detail["retry_after"] == int(locked.headers["retry-after"])
    assert 0 < detail["retry_after"] <= deps.attempt_limiter.base_lock
    # Kilit sürerken doğru belirteç de değerlendirilmez: cookie kurulmaz
    right = c.post(LOGIN_PATH, json={"token": token})
    assert right.status_code == 429 and "set-cookie" not in right.headers
    warnings = [r.getMessage() for r in caplog.records if "Too many failed access-token attempts" in r.getMessage()]
    assert len(warnings) == 1 and "testclient" in warnings[0]
    assert all(token not in r.getMessage() and "guess-" not in r.getMessage() for r in caplog.records)


def test_the_lock_ends_and_a_right_token_clears_the_count(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    limiter = AttemptLimiter(free_attempts=2, base_lock=30, clock=clock)
    monkeypatch.setattr(deps, "attempt_limiter", limiter)
    c = TestClient(app)

    for _ in range(2):
        assert c.post(LOGIN_PATH, json={"token": "wrong"}).status_code == 401
    assert c.post(LOGIN_PATH, json={"token": token}).status_code == 429
    clock.now += 31
    assert c.post(LOGIN_PATH, json={"token": token}).status_code == 200
    # Sayaç sıfırlandı: yeniden iki yanlış deneme hakkı var
    anonymous = TestClient(app)
    assert anonymous.post(LOGIN_PATH, json={"token": "wrong"}).status_code == 401
    assert anonymous.post(LOGIN_PATH, json={"token": token}).status_code == 200


def test_wrong_bearer_tokens_count_and_lock_bearer_requests(token: str) -> None:
    c = TestClient(app)
    free = deps.attempt_limiter.free_attempts
    for attempt in range(free):
        assert c.get("/api/status", headers={"authorization": f"Bearer guess-{attempt}"}).status_code == 401

    right = {"authorization": f"Bearer {token}"}
    legacy = c.get("/api/status", headers=right)
    assert legacy.status_code == 429 and legacy.json()["detail"]["code"] == "too_many_attempts"
    assert int(legacy.headers["retry-after"]) > 0

    v1 = c.get("/api/v1/status", headers=right)
    assert v1.status_code == 401 and v1.headers["www-authenticate"] == "Bearer"
    error = _error(v1)
    assert error["code"] == "unauthorized"
    assert error["details"] == {"reason": "too_many_attempts", "retry_after": int(v1.headers["retry-after"])}
    # Giriş de aynı sayaca bağlıdır
    assert c.post(LOGIN_PATH, json={"token": token}).status_code == 429


def test_requests_without_credentials_and_sessions_are_not_counted_or_locked(token: str) -> None:
    browser = TestClient(app)
    assert browser.post(LOGIN_PATH, json={"token": token}).status_code == 200

    anonymous = TestClient(app)
    for _ in range(deps.attempt_limiter.free_attempts * 3):
        assert anonymous.get("/api/v1/jobs").status_code == 401          # kimlik bilgisi yok: tahmin değil
    assert deps.attempt_limiter.retry_after("testclient") == 0

    for attempt in range(deps.attempt_limiter.free_attempts):
        anonymous.get("/api/v1/jobs", headers={"authorization": f"Bearer guess-{attempt}"})
    assert deps.attempt_limiter.retry_after("testclient") > 0
    # Kilit sürerken oturum cookie'si taşıyan istekler çalışmaya devam eder
    assert browser.get("/api/v1/jobs").status_code == 200
    assert browser.get("/api/status").status_code == 200


def test_without_a_token_nothing_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "_startup_token", "")
    c = TestClient(app)
    for _ in range(deps.attempt_limiter.free_attempts + 2):
        assert c.post(LOGIN_PATH, json={"token": "anything"}).status_code == 200
        assert c.get("/api/v1/health", headers={"authorization": "Bearer whatever"}).status_code == 200
    assert deps.attempt_limiter._clients == {}


def test_validation_details_never_carry_the_input() -> None:
    raw: List[Dict[str, Any]] = [{"loc": ("body", "values", 0), "msg": "bad", "type": "x", "input": "secret", "ctx": {"e": 1}}]
    assert errors.validation_details(raw) == {"errors": [{"loc": ["body", "values", "0"], "message": "bad", "type": "x"}]}
