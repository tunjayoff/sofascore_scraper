"""
API v1: oturum, durum, bağlantı denetimi ve sink'ler (plan maddesi P21; docs/design/02-services.md bölüm 6,
docs/design/05-web-ui.md 7.2 ve 7.3: G1, G3, G10, G11). Tümü çevrimdışı: SofaScore'a giden istek sahtedir.

  * `/api/v1/auth*`: eski oturum yollarının halefleri; belirteçsiz yanıt verir, aynı cookie'yi kurar, başarısız
    giriş sınırını eski girişle paylaşır;
  * `/status`: canlı servisin durumu, veri özeti (katalogdan), tutulan kilitler, eski düzendeki maç sayısı,
    isteğe bağlı yetenekler; depo açılamazsa yine yanıt verir; FX-13'ten beri dizinin yolu, v3'ü sayan disk
    toplamı, son taşıma ve sink'lerin özeti (G20, G21, G22);
  * `POST /status/check`: tek istek, istemcinin API köküyle; başarısızlık bir sonuçtur (200, `ok: false`);
  * `/sinks`: yapılandırmadaki sink'ler, konumları, gecikmeleri, bırakılan olaylar; adresler maskeli.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List

import pytest
from fastapi.testclient import TestClient

import store_fixtures as sf
from sofascore_scraper import redact
from sofascore_scraper.config import SinkSpec
from sofascore_scraper.exceptions import APIError, NetworkError, RateLimitError, ResourceNotFoundError
from sofascore_scraper.services.status import StatusService
from sofascore_scraper.store import FollowSpec, SchemaTooNew, StreamEvent, open_store
from sofascore_scraper.web import deps, security
from sofascore_scraper.web.api.v1 import meta
from sofascore_scraper.web.app import app

client = TestClient(app)
# Sahte bir erişim belirteci, parçalardan kurulur (gizli değer tarayıcısı düz metin bir belirteç görmesin)
ACCESS = "-".join(("fake", "access", "value", "for", "tests", "only"))


@pytest.fixture(autouse=True)
def _fresh_limiter() -> Iterator[None]:
    deps.attempt_limiter.reset()
    yield
    deps.attempt_limiter.reset()


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv(security.TOKEN_ENV, ACCESS)
    monkeypatch.setattr(security, "_startup_token", ACCESS)
    redact.refresh()
    yield ACCESS
    monkeypatch.undo()
    redact.refresh()


@pytest.fixture
def no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "_startup_token", "")


@pytest.fixture
def canonical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sf.LegacyFixture:
    fixture = sf.build_fixture("canonical", tmp_path / "data")
    monkeypatch.setenv("DATA_DIR", str(fixture.data_dir))
    return fixture


def data(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


def error(response: Any, status: int, code: str) -> Dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code, body
    return body


# --- oturum ------------------------------------------------------------------------------------------


def test_without_a_token_the_session_routes_say_so_and_set_no_cookie(no_token: None) -> None:
    assert data(client.get("/api/v1/auth")) == {"required": False, "authenticated": True}
    r = client.post("/api/v1/auth/login", json={"token": "anything"})
    assert data(r) == {"required": False, "authenticated": True}
    assert "set-cookie" not in r.headers
    assert data(client.post("/api/v1/auth/logout")) == {"required": False, "authenticated": True}


def test_the_session_routes_answer_without_the_token(token: str) -> None:
    anonymous = TestClient(app)
    assert data(anonymous.get("/api/v1/auth")) == {"required": True, "authenticated": False}
    bearer = {"authorization": f"Bearer {token}"}
    assert data(anonymous.get("/api/v1/auth", headers=bearer)) == {"required": True, "authenticated": True}
    assert anonymous.get("/api/v1/status").status_code == 401


def test_login_sets_the_session_cookie(token: str) -> None:
    browser = TestClient(app)
    r = browser.post("/api/v1/auth/login", json={"token": f"  {token} "})
    assert data(r) == {"required": True, "authenticated": True}
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{security.SESSION_COOKIE}={security.session_value(token)};")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and token not in cookie
    assert browser.get("/api/v1/status").status_code == 200
    assert data(browser.post("/api/v1/auth/logout")) == {"required": True, "authenticated": False}
    assert browser.get("/api/v1/status").status_code == 401


def test_the_cookie_is_secure_behind_tls(token: str) -> None:
    r = TestClient(app).post("/api/v1/auth/login", json={"token": token}, headers={"x-forwarded-proto": "https"})
    assert "Secure" in r.headers["set-cookie"]


def test_a_wrong_token_is_unauthorized_with_a_reason(token: str) -> None:
    r = TestClient(app).post("/api/v1/auth/login", json={"token": "nope"})
    body = error(r, 401, "unauthorized")
    assert body["details"] == {"reason": "invalid_token"} and "set-cookie" not in r.headers
    assert r.headers["www-authenticate"] == "Bearer" and r.headers["x-request-id"] == body["request_id"]


def test_failed_logins_share_the_limit_with_wrong_bearer_tokens(token: str) -> None:
    c = TestClient(app)
    for _ in range(4):
        assert c.post("/api/v1/auth/login", json={"token": "nope"}).status_code == 401
    wrong = {"authorization": "Bearer nope"}
    assert c.get("/api/v1/status", headers=wrong).status_code == 401  # fifth: the lock starts
    locked = c.post("/api/v1/auth/login", json={"token": token})  # the right token is not evaluated
    body = error(locked, 401, "unauthorized")
    assert body["details"]["reason"] == "too_many_attempts" and int(locked.headers["retry-after"]) >= 1
    assert "set-cookie" not in locked.headers
    deps.attempt_limiter.reset()
    assert c.post("/api/v1/auth/login", json={"token": token}).status_code == 200


def test_login_cannot_be_triggered_by_another_site(token: str) -> None:
    r = TestClient(app).post("/api/v1/auth/login", json={"token": token}, headers={"sec-fetch-site": "cross-site"})
    error(r, 403, "forbidden_origin")
    assert "set-cookie" not in r.headers


def test_the_token_is_never_part_of_a_session_response(token: str) -> None:
    c = TestClient(app)
    for r in (c.post("/api/v1/auth/login", json={"token": token}), c.get("/api/v1/auth"),
              c.post("/api/v1/auth/login", json={"token": token + "x"})):
        assert token not in r.text


# --- durum -------------------------------------------------------------------------------------------


def test_status_has_the_data_summary_of_the_catalog(canonical: sf.LegacyFixture) -> None:
    status = data(client.get("/api/v1/status"))
    store = open_store(canonical.data_dir)
    expected = StatusService(store).summary(tournament_ids=tuple(deps.config_manager().get_leagues()))
    summary = status["summary"]
    assert (summary["matches"], summary["details"], summary["seasons"]) == (
        expected.matches, expected.details, expected.seasons)
    assert summary["only_finished"] is expected.only_finished and summary["catalog_rebuild_reason"] is None
    by_id = {t["tournament_id"]: t for t in summary["tournaments"]}
    assert set(by_id) == {t.tournament_id for t in expected.tournaments}
    pl = by_id[sf.PL.id]
    counts = expected.tournament(sf.PL.id)
    assert (pl["matches"], pl["details"], pl["coverage"]) == (counts.matches, counts.details, counts.coverage)
    assert pl["name"] == sf.PL.name and pl["last_update_utc"].endswith("Z")
    assert summary["legacy_events"] == store.info(sizes=False).events_by_layout.get("legacy", 0) > 0
    assert summary["disk"]["total"] == expected.disk.total and summary["disk"]["entries"]
    assert status["storage_error"] is None


def test_status_has_the_path_a_disk_total_with_the_v3_tree_and_the_last_migration(
        canonical: sf.LegacyFixture) -> None:
    """05-web-ui.md G20 ve G21, ST-23'ün notu (FX-13): dizinin yolu, v3'ü sayan disk toplamı, son taşıma."""
    from sofascore_scraper.services.status import forget_sizes

    summary = data(client.get("/api/v1/status"))["summary"]
    assert summary["data_dir"] == str(canonical.data_dir)
    assert summary["last_migration"] is None
    before = summary["disk"]
    assert before["v3"] == before["entries"].get("v3", 0)

    report = open_store(canonical.data_dir).migrate.run()
    forget_sizes()
    summary = data(client.get("/api/v1/status"))["summary"]
    run = summary["last_migration"]
    assert run["id"] == report.run_id and run["finished_at_utc"].endswith("Z") and run["events_failed"] == 0
    assert run["events_done"] > 0 and run["delete_legacy"] is False
    disk = summary["disk"]
    assert disk["v3"] == disk["entries"]["v3"] > 0
    assert disk["total"] == sum(disk[k] for k in ("seasons", "matches", "details", "datasets", "v3", "changes"))
    assert disk["total"] > before["total"]


def test_a_folder_written_by_3_0_has_a_disk_total(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G21: `summary.disk.total` bir 3.0 dizininde 0 değildir (önceden yalnızca 2.x ağaçlarını sayıyordu)."""
    from sofascore_scraper.services.status import forget_sizes

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "fresh"))
    store = open_store(tmp_path / "fresh")
    (Path(store.data_dir) / "v3" / "events").mkdir(parents=True, exist_ok=True)
    (Path(store.data_dir) / "v3" / "events" / "probe.bin").write_bytes(b"x" * 1000)
    forget_sizes()
    disk = data(client.get("/api/v1/status"))["summary"]["disk"]
    assert (disk["seasons"], disk["matches"], disk["details"]) == (0, 0, 0)
    assert disk["v3"] >= 1000 and disk["total"] >= 1000


def test_status_lists_followed_tournaments_without_matches(canonical: sf.LegacyFixture,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    # FX-19: `followed` takip tablosundandır (her kaynak); API'den eklenen takip de sayılır
    open_store(canonical.data_dir).follows.add(FollowSpec(kind="tournament", entity_id=424242, name="Nowhere League"))
    by_id = {t["tournament_id"]: t for t in data(client.get("/api/v1/status"))["summary"]["tournaments"]}
    assert by_id[424242] == {
        "tournament_id": 424242, "name": "Nowhere League", "followed": True, "matches": 0, "details": 0,
        "events": 0, "finished": 0, "seasons": 0, "seasons_with_events": 0, "coverage": 0.0,
        "last_update_utc": None, "finished_details": 0,  # FX-26: bitmiş ve detaylı maçlar, takiplerin kapsamı
    }


def test_status_names_the_holders_of_the_leases(canonical: sf.LegacyFixture) -> None:
    store = open_store(canonical.data_dir)
    assert data(client.get("/api/v1/status"))["leases"] == []
    with store.lease("sinks", purpose="test dispatcher"), store.lease("watcher:tennis", purpose="watch"):
        leases = data(client.get("/api/v1/status"))["leases"]
    assert [(lease["name"], lease["purpose"]) for lease in leases] == [
        ("sinks", "test dispatcher"), ("watcher:tennis", "watch")]
    assert all(lease["pid"] and lease["host"] and lease["since_utc"].endswith("Z") for lease in leases)


def test_status_shows_the_state_of_the_live_service(canonical: sf.LegacyFixture) -> None:
    store = open_store(canonical.data_dir)
    idle = data(client.get("/api/v1/status"))["live"]
    assert idle == {"running": False, "pid": None, "host": None, "source": None, "sports": [],
                    "heartbeat_at": None, "blocked": False, "leaders": {}, "last_switch": None}
    store.runtime.set("live", {"source": "page", "sports": ["football"], "heartbeat_at": 1790856000.5,
                               "blocked": False, "leaders": {"football": "page"},
                               "last_switch": {"sport": "football", "to": "page"}})
    ended = data(client.get("/api/v1/status"))["live"]
    assert (ended["running"], ended["source"], ended["heartbeat_at"]) == (False, None, 1790856000.5)
    with store.lease("live", purpose="watch"):
        running = data(client.get("/api/v1/status"))["live"]
    assert running["running"] is True and running["pid"] and running["source"] == "page"
    assert running["sports"] == ["football"] and running["leaders"] == {"football": "page"}
    assert running["last_switch"] == {"sport": "football", "to": "page"}


def test_status_reports_the_optional_features(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(meta, "_installed", lambda module: module == "pyarrow")
    assert data(client.get("/api/v1/status"))["capabilities"] == {"parquet": True, "sse": False, "scheduler": False}
    monkeypatch.setattr(meta, "_installed", lambda module: module == "sse_starlette")
    assert data(client.get("/api/v1/status"))["capabilities"] == {"parquet": False, "sse": True, "scheduler": False}


def test_status_answers_when_the_store_cannot_be_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> Any:
        raise SchemaTooNew("written by a newer version")

    monkeypatch.setattr(deps, "store", refuse)
    status = data(client.get("/api/v1/status"))
    assert (status["summary"], status["live"], status["leases"]) == (None, None, [])
    assert status["storage_error"] == "storage_error" and status["version"]


def test_status_does_not_hide_a_bug(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(deps, "store", boom)
    error(client.get("/api/v1/status"), 500, "internal")


# --- bağlantı denetimi ---------------------------------------------------------------------------------


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> List[Any]:
    """`make_api_request`in yerine: istenen adresleri kaydeder, `outcome[0]`ı döndürür ya da fırlatır."""
    calls: List[Any] = []
    outcome: List[Any] = [{"events": [{"id": 1}, {"id": 2}]}]

    def fake(url: str, **kwargs: Any) -> Any:
        calls.append((url, kwargs))
        if isinstance(outcome[0], BaseException):
            raise outcome[0]
        return outcome[0]

    monkeypatch.setattr("sofascore_scraper.utils.make_api_request", fake)
    return [calls, outcome]


def test_the_connection_check_sends_one_request_through_the_client(upstream: List[Any]) -> None:
    from sofascore_scraper.client import api_url, endpoints

    calls, _outcome = upstream
    check = data(client.post("/api/v1/status/check", json={"target": "sofascore"}))
    assert (check["ok"], check["reason"], check["events_count"]) == (True, None, 2)
    assert check["checked_at_utc"].endswith("Z") and check["bridge"]["state"] in ("ok", "degraded", "blocked")
    assert calls == [(api_url(endpoints.live_events("football")), {"max_retries": 1, "timeout": 10, "raise_errors": True})]


@pytest.mark.parametrize("failure,reason", [
    (APIError("forbidden", status_code=403), "blocked"),
    (RateLimitError(wait_time=5), "rate_limited"),
    (NetworkError("timeout"), "network"),
    (ResourceNotFoundError("gone"), "upstream"),
    (APIError("bad gateway", status_code=502), "upstream"),
    ({"unexpected": True}, "upstream"),
])
def test_a_failed_connection_check_is_a_result_with_a_reason(upstream: List[Any], failure: Any, reason: str) -> None:
    upstream[1][0] = failure
    check = data(client.post("/api/v1/status/check", json={"target": "sofascore"}))
    assert (check["ok"], check["reason"], check["events_count"]) == (False, reason, None)
    assert check["message"]


def test_the_connection_check_needs_its_body(upstream: List[Any]) -> None:
    error(client.post("/api/v1/status/check"), 422, "invalid_request")
    error(client.post("/api/v1/status/check", json={"target": "elsewhere"}), 422, "invalid_request")
    assert upstream[0] == []


# --- sink'ler ----------------------------------------------------------------------------------------


def _with_sinks(monkeypatch: pytest.MonkeyPatch, *specs: SinkSpec) -> None:
    settings = SimpleNamespace(settings=SimpleNamespace(sinks=tuple(specs)))
    monkeypatch.setattr(deps, "loaded_settings", lambda: settings)


def test_without_sinks_the_list_is_empty() -> None:
    assert client.get("/api/v1/sinks").json() == {"data": [], "page": {"limit": 0, "next_cursor": None}}


def test_sinks_show_their_position_lag_and_errors(canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    secret_url = "https://" + "user:" + "pw" + "@hooks.example.org/path/" + "abc123"
    _with_sinks(
        monkeypatch,
        SinkSpec(name="hook", type="webhook", url=secret_url, events=("live.*",)),
        SinkSpec(name="file", type="file", path="/var/log/events.jsonl"),
        SinkSpec(name="out", type="stdout"),
    )
    store = open_store(canonical.data_dir)
    seqs = store.streams.append("live", [StreamEvent(type="live.score_changed", data={"n": i}, ts=1000.0 + i)
                                         for i in range(5)])
    store.streams.append("system", [StreamEvent(type="system.sink_dropped", data={"sink": "hook", "count": 3})])
    head = store.streams.head().last_seq
    store.streams.set_cursor("hook", seqs[1], error="HTTP 500")
    store.streams.set_cursor("file", head)

    items = {item["name"]: item for item in data(client.get("/api/v1/sinks"))}

    assert list(items) == ["hook", "file", "out"]
    hook = items["hook"]
    assert hook["target"] == "https://***@hooks.example.org/***" and "pw" not in str(hook)
    assert (hook["type"], hook["events"], hook["state"], hook["served"]) == ("webhook", ["live.*"], "error", False)
    assert (hook["cursor"], hook["head_seq"], hook["lag_events"]) == (seqs[1], head, head - seqs[1])
    assert hook["lag_seconds"] > 0 and hook["last_error"] == "HTTP 500" and hook["dropped"] == 3
    assert hook["last_delivered_at_utc"].endswith("Z")
    assert (items["file"]["state"], items["file"]["lag_events"], items["file"]["lag_seconds"]) == ("ok", 0, None)
    assert items["file"]["target"] == "/var/log/events.jsonl" and items["file"]["dropped"] == 0
    out = items["out"]
    assert (out["state"], out["cursor"], out["target"], out["last_delivered_at_utc"]) == ("pending", 0, None, None)
    assert out["lag_events"] == head

    with store.lease("sinks", purpose="dispatcher"):
        assert all(item["served"] for item in data(client.get("/api/v1/sinks")))


def test_status_has_the_sinks_at_a_glance(canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """05-web-ui.md G22 (FX-13): sink'lerin özeti `/status`ta (raydaki nokta ve sağlık hapı için)."""
    assert data(client.get("/api/v1/status"))["sinks"] == {
        "configured": 0, "ok": 0, "error": 0, "pending": 0, "served": False, "max_lag_events": 0,
        "max_lag_seconds": None}
    store = open_store(canonical.data_dir)
    seqs = store.streams.append("live", [StreamEvent(type="live.score_changed", data={"n": i}, ts=1000.0 + i)
                                         for i in range(4)])
    head = store.streams.head().last_seq
    store.streams.set_cursor("hook", seqs[0], error="HTTP 500")
    store.streams.set_cursor("file", head)
    _with_sinks(monkeypatch, SinkSpec(name="hook", type="webhook", url="https://hooks.example.org/x"),
                SinkSpec(name="file", type="file", path="/var/log/events.jsonl"), SinkSpec(name="out", type="stdout"))

    sinks = data(client.get("/api/v1/status"))["sinks"]

    assert {k: sinks[k] for k in ("configured", "ok", "error", "pending", "served")} == {
        "configured": 3, "ok": 1, "error": 1, "pending": 1, "served": False}
    assert sinks["max_lag_events"] == head and sinks["max_lag_seconds"] > 0
    with store.lease("sinks", purpose="dispatcher"):
        assert data(client.get("/api/v1/status"))["sinks"]["served"] is True


def test_the_sink_status_service_names_what_the_dispatcher_writes() -> None:
    from sofascore_scraper.services import sink_status
    from sofascore_scraper.sinks import SINK_DROPPED
    from sofascore_scraper.sinks.dispatcher import LEASE_NAME

    assert (sink_status.DROPPED_TYPE, sink_status.SINKS_LEASE) == (SINK_DROPPED, LEASE_NAME)


def test_status_states_the_schema_version() -> None:
    from sofascore_scraper.schema import SCHEMA_VERSION

    assert data(client.get("/api/v1/status"))["schema_version"] == SCHEMA_VERSION == 1
