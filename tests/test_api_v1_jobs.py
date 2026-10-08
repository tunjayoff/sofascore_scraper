"""
API v1: işler (plan maddesi P20; docs/design/02-services.md bölüm 6 ve 2.8). Tümü çevrimdışı.

  * liste (süzgeç, imleçle sayfalama), tek iş, iptal: iş yöneticisinden okunur; başka bir sürecin işi de görünür
    ve iptal edilebilir;
  * başlatma: iş arka planda çalışır, 202 ve `Location` döner; "tek iş" kuralı 409 ile, kilidin sahibi
    `details`te; servisi olmayan türler 501;
  * olay akışı (SSE): `id` / `event` / `data`, `after` ve `Last-Event-ID` ile sürdürme, saklanmayan aralık için
    `stream.gap`, canlı tutma satırı, iş bitince akışın kapanması;
  * meta rotaları: sağlık, durum, sporlar.

Testler geçici bir iş deposuyla çalışır (`deps.job_store()` yerine konur); eski arayüzle birlikte çalışmayı
sınayanlar sürecin gerçek deposunu kullanır ve onu boşta bırakır.
"""
from __future__ import annotations

import asyncio
import errno
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional, Tuple

import conftest
import pytest

import sync_fakes
from fastapi.testclient import TestClient

from sofascore_scraper import sports
from sofascore_scraper.exceptions import StorageError
from sofascore_scraper.jobs.manager import JobManager, JobOutcome, local_origin
from sofascore_scraper.jobs.model import ErrorInfo, JobKind, JobState, Origin
from sofascore_scraper.store import JobStore
from sofascore_scraper.version import __version__
from sofascore_scraper.web import deps, sse
from sofascore_scraper.web.api.v1 import jobs as jobs_v1
from sofascore_scraper.web.app import app
client = TestClient(app)


def wait_for(condition: Any, timeout: float = 20.0, what: str = "condition") -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        assert time.monotonic() < deadline, f"{what} did not happen within {timeout} s"
        time.sleep(0.01)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[JobStore]:
    """v1 rotalarının gördüğü iş deposu: geçici bir dizinde, boş."""
    before = conftest.job_threads()
    jobs = JobStore(str(tmp_path / "data" / ".meta" / "state.db"))
    monkeypatch.setattr(deps, "job_store", lambda: jobs)
    yield jobs
    # `POST /api/v1/jobs` işi arka planda yürütür; iş, satırı bittikten sonra da depoya yazar
    conftest.join_job_threads(before)
    if jobs.snapshot().get("is_running"):
        jobs.update(status="Cancelled", finished=True)
    monkeypatch.undo()
    deps.refresh_job_mirror()
    jobs.close()


@pytest.fixture
def manager(store: JobStore) -> JobManager:
    return JobManager(store, cancel_poll=0.02, heartbeat=0.05, progress_interval=0.0)


def finished_job(manager: JobManager, kind: JobKind = JobKind.SYNC, outcome: Optional[JobOutcome] = None,
                 spec: Optional[Dict[str, Any]] = None, face: str = "cli") -> str:
    """Bitmiş bir iş yaratır (çağıranın thread'inde çalıştırır) ve kimliğini döndürür."""
    job = manager.submit(
        kind, spec or {"mode": "full"}, lambda handle: outcome or JobOutcome(),
        origin=Origin(face=face, pid=11, host="box"), background=False,  # type: ignore[arg-type]
    )
    return job.id


def data(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


def error(response: Any, status: int, code: str) -> Dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code, body
    return body


# --- liste -------------------------------------------------------------------------------------------


def test_an_empty_history_is_an_empty_page(store: JobStore) -> None:
    assert client.get("/api/v1/jobs").json() == {"data": [], "page": {"limit": 50, "next_cursor": None}}


def test_jobs_are_listed_newest_first_with_the_fields_of_the_job_model(manager: JobManager) -> None:
    first = finished_job(manager, JobKind.SYNC, spec={"mode": "full", "league_id": 17})
    second = finished_job(manager, JobKind.REFRESH, JobOutcome(
        state=JobState.PARTIAL, result={"refreshed": 3},
        error=ErrorInfo(code="rate_limited", message="stopped by the circuit breaker (429)", details={"reason": "429"}),
    ), face="api")

    body = client.get("/api/v1/jobs").json()

    assert [job["id"] for job in body["data"]] == [second, first]
    assert body["page"] == {"limit": 50, "next_cursor": None}
    newest, oldest = body["data"]
    assert list(newest) == [
        "id", "kind", "state", "origin", "spec", "progress", "result", "error", "created_at", "started_at",
        "finished_at", "heartbeat_at", "cancel_requested",
    ]
    assert (newest["kind"], newest["state"]) == ("refresh", "partial")
    assert newest["origin"] == {"face": "api", "pid": 11, "host": "box"}
    assert newest["result"] == {"refreshed": 3}
    assert newest["error"] == {
        "code": "rate_limited", "message": "stopped by the circuit breaker (429)", "details": {"reason": "429"},
    }
    assert (oldest["kind"], oldest["state"], oldest["error"]) == ("sync", "succeeded", None)
    # a record of before FX-20 (the service's spec) is given in the shape of the request body
    assert oldest["spec"] == {"league_id": 17, "selections": [], "follows": [], "only": None, "event_ids": []}
    assert oldest["cancel_requested"] is False
    assert oldest["created_at"] and oldest["started_at"] and oldest["finished_at"]


def test_the_list_is_paged_with_a_cursor(manager: JobManager) -> None:
    ids = [finished_job(manager) for _ in range(5)][::-1]  # en yeni önce

    first = client.get("/api/v1/jobs", params={"limit": 2}).json()
    assert [job["id"] for job in first["data"]] == ids[:2]
    assert first["page"] == {"limit": 2, "next_cursor": ids[1]}

    second = client.get("/api/v1/jobs", params={"limit": 2, "cursor": first["page"]["next_cursor"]}).json()
    assert [job["id"] for job in second["data"]] == ids[2:4] and second["page"]["next_cursor"] == ids[3]

    last = client.get("/api/v1/jobs", params={"limit": 2, "cursor": second["page"]["next_cursor"]}).json()
    assert [job["id"] for job in last["data"]] == ids[4:] and last["page"]["next_cursor"] is None

    exact = client.get("/api/v1/jobs", params={"limit": 5}).json()
    assert len(exact["data"]) == 5 and exact["page"]["next_cursor"] is None


def test_an_unknown_cursor_is_refused(manager: JobManager) -> None:
    finished_job(manager)
    body = error(client.get("/api/v1/jobs", params={"cursor": "01HZZZZZZZZZZZZZZZZZZZZZZZ"}), 400, "invalid_request")
    assert body["details"] == {"cursor": "01HZZZZZZZZZZZZZZZZZZZZZZZ"}


def test_the_list_filters_by_state_and_kind(manager: JobManager) -> None:
    sync_ok = finished_job(manager, JobKind.SYNC)
    refresh_ok = finished_job(manager, JobKind.REFRESH)
    sync_cancelled = finished_job(manager, JobKind.SYNC, JobOutcome(state=JobState.CANCELLED))
    fetch_failed = finished_job(manager, JobKind.FETCH, JobOutcome(
        state=JobState.FAILED, error=ErrorInfo(code="storage_error", message="disk full"),
    ))

    def ids(**params: Any) -> List[str]:
        return [job["id"] for job in client.get("/api/v1/jobs", params=params).json()["data"]]

    assert ids(state="succeeded") == [refresh_ok, sync_ok]
    assert ids(state=["cancelled", "failed"]) == [fetch_failed, sync_cancelled]
    assert ids(kind="sync") == [sync_cancelled, sync_ok]
    assert ids(kind="sync", state="cancelled") == [sync_cancelled]
    assert ids(state="running") == []
    # Sayfalama süzülmüş liste üzerindedir
    page = client.get("/api/v1/jobs", params={"kind": "sync", "limit": 1}).json()
    assert [job["id"] for job in page["data"]] == [sync_cancelled] and page["page"]["next_cursor"] == sync_cancelled
    error(client.get("/api/v1/jobs", params={"kind": "nap"}), 422, "invalid_request")
    error(client.get("/api/v1/jobs", params={"limit": 201}), 422, "invalid_request")


# --- tek iş ------------------------------------------------------------------------------------------


def test_get_returns_one_job_and_404_for_an_unknown_id(manager: JobManager) -> None:
    job_id = finished_job(manager)

    job = data(client.get(f"/api/v1/jobs/{job_id}"))
    assert (job["id"], job["state"]) == (job_id, "succeeded")

    body = error(client.get("/api/v1/jobs/nope"), 404, "not_found")
    assert body["details"] == {"job_id": "nope"}


def test_a_job_of_another_process_is_visible_while_it_runs(store: JobStore) -> None:
    other = JobStore(store.db_path)  # aynı veri dizininde başka bir süreç gibi: kendi kilit yöneticisi
    try:
        job_id = other.create_running({"mode": "full"}, kind="sync", origin={"face": "cli", "pid": 1, "host": "x"},
                                      spec={"mode": "full", "league_id": None})

        job = data(client.get(f"/api/v1/jobs/{job_id}"))
        assert (job["state"], job["kind"], job["origin"]["face"]) == ("running", "sync", "cli")
        assert [j["id"] for j in client.get("/api/v1/jobs", params={"state": "running"}).json()["data"]] == [job_id]
        assert data(client.get("/api/v1/status"))["active_job"]["id"] == job_id
    finally:
        other.update(status="Cancelled", finished=True)
        other.close()
    assert data(client.get("/api/v1/status"))["active_job"] is None


# --- iptal -------------------------------------------------------------------------------------------


def test_cancel_reaches_a_job_run_by_another_process(store: JobStore) -> None:
    other = JobStore(store.db_path)
    try:
        job_id = other.create_running({"mode": "full"}, kind="sync")

        job = data(client.post(f"/api/v1/jobs/{job_id}/cancel"))

        assert job["state"] == "running" and job["cancel_requested"] is True
        assert other.poll_cancel(job_id) is True  # işi çalıştıran süreç bayrağı satırdan okur
    finally:
        other.update(status="Cancelled", finished=True)
        other.close()
    assert data(client.get(f"/api/v1/jobs/{job_id}"))["state"] == "cancelled"


def test_cancel_of_a_finished_job_returns_it_unchanged_and_an_unknown_job_is_404(manager: JobManager) -> None:
    job_id = finished_job(manager)
    job = data(client.post(f"/api/v1/jobs/{job_id}/cancel"))
    assert job["state"] == "succeeded" and job["cancel_requested"] is False
    error(client.post("/api/v1/jobs/nope/cancel"), 404, "not_found")


def test_cancel_cannot_be_triggered_by_another_site(manager: JobManager) -> None:
    job_id = finished_job(manager)
    error(client.post(f"/api/v1/jobs/{job_id}/cancel", headers={"sec-fetch-site": "cross-site"}), 403, "forbidden_origin")


# --- başlatma ----------------------------------------------------------------------------------------


@pytest.fixture
def body(monkeypatch: pytest.MonkeyPatch) -> Any:
    """İşin gövdesini (servis çağrısını) testin denetimine alır: gövde `release` gelene kadar bekler."""
    seen: Dict[str, Any] = {"specs": [], "release": threading.Event(), "outcome": None, "raise": None}

    def run(handle: Any, spec: Any) -> Any:
        seen["specs"].append(spec)
        handle.log("working")
        assert seen["release"].wait(20), "the test never released the job"
        if seen["raise"] is not None:
            raise seen["raise"]
        return seen["outcome"] or JobOutcome(result={"details_done": 2})

    monkeypatch.setattr(jobs_v1, "_run_sync", run)
    yield SimpleNamespace(**seen, state=seen)
    seen["release"].set()


def _ended(job_id: str) -> Dict[str, Any]:
    """İş bitene kadar bekler ve bitmiş işi döndürür."""
    return wait_for(
        lambda: (lambda job: job if job["finished_at"] else None)(data(client.get(f"/api/v1/jobs/{job_id}"))),
        what="the job to end",
    )


def test_start_runs_a_sync_job_in_the_background(store: JobStore, body: Any) -> None:
    response = client.post("/api/v1/jobs", json={"kind": "sync", "spec": {"league_id": 17}})

    job = data(response, 202)
    assert response.headers["location"] == f"/api/v1/jobs/{job['id']}"
    assert (job["kind"], job["state"], job["finished_at"]) == ("sync", "running", None)
    assert job["origin"]["face"] == "api" and job["origin"]["pid"] == os.getpid()
    # CSV aşaması istenmez: dışa aktarma ayrı bir iş türüdür. Belirtim gövdenin alanlarıyla, ligin adıyla (FX-20)
    assert job["spec"] == {"league_id": 17, "selections": [], "follows": [], "only": None, "event_ids": [], "names": {"tournament:17": "Premier League"}}
    assert len(job["id"]) == 26

    body.release.set()
    ended = _ended(job["id"])
    assert (ended["state"], ended["result"], ended["error"]) == ("succeeded", {"details_done": 2}, None)
    (spec,) = body.specs
    assert (spec.mode, spec.league_id, spec.job_phases) == ("full", 17, ("seasons", "matches", "details"))
    # 2.x arayüzünün iş kartı başlığı (`mode`lu `payload`) 3.1'de yazılmaz (P30)
    assert "mode" not in store.get_job(job["id"])["payload"]


@pytest.mark.parametrize("request_body,expected", [
    ({"kind": "sync"}, ("sync", "full", None, ())),
    ({"kind": "fetch", "spec": {"league_id": 8}}, ("fetch", "details", 8, ())),
    ({"kind": "refresh"}, ("refresh", "refresh", None, ())),
    ({"kind": "refresh", "spec": {"league_id": 17}}, ("refresh", "refresh", 17, ())),
    (
        {"kind": "sync", "spec": {"selections": [{"league_id": 17, "season_ids": [1, 2]}, {"league_id": 8}]}},
        ("sync", "full", None, ((17, (1, 2), ()), (8, (), ()))),
    ),
    (
        {"kind": "fetch", "spec": {"selections": [{"league_id": 17, "match_ids": [9000001]}]}},
        ("fetch", "details", None, ((17, (), (9000001,)),)),
    ),
])
def test_each_kind_maps_to_the_service_spec(store: JobStore, body: Any, request_body: Dict[str, Any],
                                            expected: Tuple[Any, ...]) -> None:
    body.release.set()
    job = data(client.post("/api/v1/jobs", json=request_body), 202)
    _ended(job["id"])

    (spec,) = body.specs
    selections = tuple((s.league_id, s.season_ids, s.match_ids) for s in spec.selections)
    assert (job["kind"], spec.mode, spec.league_id, selections) == expected


def test_only_one_job_runs_at_a_time(store: JobStore, body: Any) -> None:
    first = data(client.post("/api/v1/jobs", json={"kind": "sync"}), 202)

    refused = error(client.post("/api/v1/jobs", json={"kind": "refresh"}), 409, "job_running")
    assert refused["message"].isascii()
    assert [j["id"] for j in client.get("/api/v1/jobs").json()["data"]] == [first["id"]]

    body.release.set()
    _ended(first["id"])
    second = data(client.post("/api/v1/jobs", json={"kind": "refresh"}), 202)
    assert _ended(second["id"])["state"] == "succeeded"


def test_a_job_of_another_process_refuses_the_start_and_names_its_holder(store: JobStore, body: Any) -> None:
    other = JobStore(store.db_path)
    try:
        other.create_running({"mode": "full"}, kind="sync", purpose="headless")

        refused = error(client.post("/api/v1/jobs", json={"kind": "sync"}), 409, "job_running")

        holder = refused["details"]["holder"]
        assert (holder["lease"], holder["purpose"], holder["pid"]) == ("writer", "headless", os.getpid())
        assert body.specs == []
    finally:
        other.update(status="Cancelled", finished=True)
        other.close()


def test_a_data_operation_refuses_the_start(store: JobStore, body: Any) -> None:
    with store.exclusive("clear"):
        refused = error(client.post("/api/v1/jobs", json={"kind": "sync"}), 409, "data_operation_running")
    assert "clear" in refused["message"] and body.specs == []


# P21: export, backup, clear, rebuild ve restore başlar (tests/test_api_v1_data_jobs.py; gerçek geri yükleme FX-13);
# servisi olmayan kaldı: `pyarrow` kurulu olmayan sunucuda Parquet dışa aktarması (SC-2)
@pytest.mark.parametrize("request_body,details", [
    ({"kind": "export", "spec": {"format": "parquet"}}, {"dataset": "events", "format": "parquet", "schema": "normalized"}),
])
def test_kinds_without_a_service_are_not_supported_yet(store: JobStore, request_body: Dict[str, Any],
                                                       details: Dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sofascore_scraper.services.export.parquet_available", lambda: False)
    refused = error(client.post("/api/v1/jobs", json=request_body), 501, "not_supported")
    assert refused["details"] == details
    assert client.get("/api/v1/jobs").json()["data"] == []


@pytest.mark.parametrize("request_body", [
    {},
    {"kind": "nap"},
    {"kind": "restore"},
    {"kind": "sync", "spec": {"league": 17}},
    {"kind": "sync", "spec": {"league_id": 0}},
    {"kind": "sync", "extra": 1},
    {"kind": "refresh", "spec": {"selections": []}},
    {"kind": "sync", "spec": {"selections": [{"season_ids": [1]}]}},
])
def test_a_body_the_contract_does_not_allow_is_rejected(store: JobStore, request_body: Dict[str, Any]) -> None:
    error(client.post("/api/v1/jobs", json=request_body), 422, "invalid_request")
    assert client.get("/api/v1/jobs").json()["data"] == []


def test_start_cannot_be_triggered_by_another_site(store: JobStore, body: Any) -> None:
    error(client.post("/api/v1/jobs", json={"kind": "sync"}, headers={"origin": "https://evil.example"}), 403, "forbidden_origin")
    assert body.specs == [] and client.get("/api/v1/jobs").json()["data"] == []


def test_cancel_stops_a_job_started_here(store: JobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    def run(handle: Any, spec: Any) -> Any:
        wait_for(handle.cancelled, what="the cancel request")
        return JobOutcome(state=JobState.CANCELLED)

    monkeypatch.setattr(jobs_v1, "_run_sync", run)
    job = data(client.post("/api/v1/jobs", json={"kind": "sync"}), 202)

    cancelled = data(client.post(f"/api/v1/jobs/{job['id']}/cancel"))
    assert cancelled["cancel_requested"] is True

    assert _ended(job["id"])["state"] == "cancelled"


def test_a_body_that_raises_fails_the_job_with_the_code_of_the_error(store: JobStore, body: Any) -> None:
    body.state["raise"] = StorageError.from_exception(OSError(errno.ENOSPC, "No space left on device"), "/data/x")
    body.release.set()

    job = data(client.post("/api/v1/jobs", json={"kind": "fetch"}), 202)
    ended = _ended(job["id"])

    assert ended["state"] == "failed"
    assert ended["error"]["code"] == "storage_error" and "No space left on device" in ended["error"]["message"]
    # Depo boşta: yeni iş başlatılabilir
    body.state["raise"] = None
    assert client.post("/api/v1/jobs", json={"kind": "sync"}).status_code == 202


# --- gerçek gövde: servisle ----------------------------------------------------------------------------


class _Details:
    """SyncService'in detay aşamasının sahtesi (tests/test_job_manager.py'deki ile aynı yüz)."""

    breaker_tripped = False
    status_counts: Dict[str, int] = {}
    refresh_listener = None
    breaker_on: Optional[str] = None
    failing: List[str] = []

    def candidates(self, league_id: Any = None, *, only_season_ids: Any = None) -> List[str]:
        return ["a", "b", "c"]

    def pending(self, ids: List[str]) -> List[str]:
        return list(ids)

    def fetch(self, ids: List[str], *, progress: Any = None, cancelled: Any = None,
              failed: Any = None) -> int:
        for n, match_id in enumerate(ids, start=1):
            if match_id in self.failing:
                failed(match_id)
            progress(n, len(ids), "")
        if self.breaker_on:
            self.breaker_tripped = True
            self.status_counts = {self.breaker_on: 9}
        return len(ids)


@pytest.fixture
def service(store: JobStore, monkeypatch: pytest.MonkeyPatch) -> _Details:
    """Gerçek iş gövdesi (`_run_sync`), sahte bir servis bağlamıyla: istek atılmaz, dosya yazılmaz."""
    details = _Details()
    ctx = SimpleNamespace(config=deps.config_manager(), details=details)
    sync_fakes.install(monkeypatch)
    monkeypatch.setattr("sofascore_scraper.services.context.build_context", lambda config_manager: ctx)
    return details


def test_a_fetch_job_runs_the_service_and_writes_no_csv(store: JobStore, service: _Details) -> None:
    job = data(client.post("/api/v1/jobs", json={"kind": "fetch", "spec": {"league_id": 17}}), 202)
    ended = _ended(job["id"])

    assert ended["state"] == "succeeded" and ended["error"] is None
    assert ended["result"]["details_done"] == 3 and ended["result"]["schedule_empty_seasons"] == 0
    types = [event.type for event in JobManager(store).events(job["id"])]
    assert types[0] == "started" and types[-1] == "finished" and "phase" in types


def test_a_breaker_stop_ends_the_job_partial_with_a_code_and_an_english_message(store: JobStore, service: _Details) -> None:
    service.breaker_on = "429"
    job = data(client.post("/api/v1/jobs", json={"kind": "fetch"}), 202)
    ended = _ended(job["id"])

    assert ended["state"] == "partial" and ended["error"]["code"] == "rate_limited"
    finished = [e.data for e in JobManager(store).events(job["id"]) if e.type == "finished"][0]
    assert (finished["code"], finished["params"]) == ("fetch_stopped_by_breaker", {"reason": "429"})
    assert finished["message"] == "Stopped by the circuit breaker (429)"


def test_a_failed_item_ends_the_job_partial(store: JobStore, service: _Details) -> None:
    service.failing = ["b"]
    ended = _ended(data(client.post("/api/v1/jobs", json={"kind": "fetch"}), 202)["id"])
    assert ended["state"] == "partial" and ended["error"] is None and ended["result"]["failed_count"] == 1


# --- olay akışı --------------------------------------------------------------------------------------


def parse_sse(text: str) -> List[Dict[str, Any]]:
    """SSE gövdesi → iletiler: {"id", "event", "data"} ya da {"comment"}."""
    messages: List[Dict[str, Any]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        message: Dict[str, Any] = {}
        for line in block.split("\n"):
            if line.startswith(":"):
                message["comment"] = line[1:].strip()
            else:
                name, _, value = line.partition(": ")
                message[name] = json.loads(value) if name == "data" else value
        messages.append(message)
    return messages


def events_of(response: Any) -> List[Dict[str, Any]]:
    assert response.status_code == 200, response.text
    return [m for m in parse_sse(response.text) if "event" in m]


@pytest.fixture
def logged_job(manager: JobManager) -> str:
    """Olay günlüğü bilinen, bitmiş bir iş: started, log ×3, finished."""

    def run(handle: Any) -> JobOutcome:
        for n in (1, 2, 3):
            handle.log(f"step {n}", code="step", n=n)
        return JobOutcome(result={"ok": True})

    return manager.submit(JobKind.SYNC, {"mode": "full"}, run, origin=local_origin("cli"), background=False).id


def test_the_stream_of_a_finished_job_sends_every_event_and_closes(logged_job: str) -> None:
    response = client.get(f"/api/v1/jobs/{logged_job}/events")

    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store" and response.headers["x-accel-buffering"] == "no"
    assert "x-request-id" in response.headers
    messages = parse_sse(response.text)
    assert messages[0] == {"comment": "stream open"}
    events = [m for m in messages if "event" in m]
    assert [e["event"] for e in events] == ["started", "log", "log", "log", "finished"]
    assert [e["id"] for e in events] == ["1", "2", "3", "4", "5"]
    for event in events:
        assert list(event["data"]) == ["job_id", "seq", "ts_ms", "type", "data"]
        assert event["data"]["job_id"] == logged_job and event["data"]["type"] == event["event"]
        assert str(event["data"]["seq"]) == event["id"]
    assert events[1]["data"]["data"] == {"message": "step 1", "code": "step", "n": 1}
    assert events[-1]["data"]["data"]["state"] == "succeeded"


def test_the_stream_resumes_after_a_sequence_number(logged_job: str) -> None:
    url = f"/api/v1/jobs/{logged_job}/events"

    assert [e["id"] for e in events_of(client.get(url, params={"after": 3}))] == ["4", "5"]
    assert [e["id"] for e in events_of(client.get(url, headers={"last-event-id": "4"}))] == ["5"]
    # Tarayıcı yeniden bağlanırken aynı adresi Last-Event-ID ile ister: başlık adresteki `after`ın önüne geçer
    assert [e["id"] for e in events_of(client.get(url, params={"after": 1}, headers={"last-event-id": "4"}))] == ["5"]
    assert events_of(client.get(url, params={"after": 5})) == []
    assert events_of(client.get(url, params={"after": 99})) == []


def test_a_resume_position_that_is_not_a_number_is_rejected(logged_job: str) -> None:
    url = f"/api/v1/jobs/{logged_job}/events"
    body = error(client.get(url, headers={"last-event-id": "abc"}), 422, "invalid_request")
    assert body["details"]["errors"][0]["loc"] == ["header", "Last-Event-ID"]
    error(client.get(url, params={"after": -1}), 422, "invalid_request")
    error(client.get(url, params={"after": "x"}), 422, "invalid_request")


def test_the_stream_of_an_unknown_job_is_404(store: JobStore) -> None:
    error(client.get("/api/v1/jobs/nope/events"), 404, "not_found")


def test_a_position_older_than_the_retained_events_gets_one_gap_event(
    manager: JobManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sofascore_scraper.store import jobs as store_jobs

    monkeypatch.setattr(store_jobs, "JOB_EVENTS_LIMIT", 5)
    monkeypatch.setattr(store_jobs, "_EVENT_PRUNE_EVERY", 1)

    def run(handle: Any) -> JobOutcome:
        for n in range(20):
            handle.log(f"step {n}")
        return JobOutcome()

    job_id = manager.submit(JobKind.SYNC, {}, run, origin=local_origin("cli"), background=False).id
    retained = [event.seq for event in manager.events(job_id)]
    oldest = retained[0]
    assert oldest > 3 and len(retained) <= 5
    url = f"/api/v1/jobs/{job_id}/events"

    response = client.get(url, params={"after": 2})

    assert parse_sse(response.text) == [
        {"event": "stream.gap", "data": {"job_id": job_id, "after": 2, "oldest_seq": oldest}},
    ]
    # Aralık yoksa (istenen yer saklanan en eski olayın hemen öncesi ya da sonrası) akış sürer
    assert [int(e["id"]) for e in events_of(client.get(url, params={"after": oldest - 1}))] == retained
    assert [int(e["id"]) for e in events_of(client.get(url, params={"after": oldest}))] == retained[1:]
    # Yeni bağlantı (sürdürme değil) saklanan en eski olaydan başlar
    assert [int(e["id"]) for e in events_of(client.get(url))] == retained


def test_the_stream_follows_a_running_job_until_it_ends(
    store: JobStore, body: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = data(client.post("/api/v1/jobs", json={"kind": "sync"}), 202)
    wait_for(lambda: len(list(JobManager(store).events(job["id"]))) >= 2, what="the first events")
    real, idle_rounds = sse._read, []

    def read(manager: JobManager, job_id: str, after: int) -> List[Any]:
        found = real(manager, job_id, after)
        if not found:
            idle_rounds.append(after)
            if len(idle_rounds) == 3:
                body.release.set()  # akış üç tur boyunca açık ve boştaydı; iş ancak şimdi bitebilir
        return found

    monkeypatch.setattr(sse, "_read", read)

    events = events_of(client.get(f"/api/v1/jobs/{job['id']}/events"))

    assert len(idle_rounds) >= 3
    assert events[0]["event"] == "started" and events[-1]["event"] == "finished"
    assert [int(e["id"]) for e in events] == sorted(int(e["id"]) for e in events)
    assert events[-1]["data"]["data"]["state"] == "succeeded"
    # Bitişten sonra yazılan olaylar akış açıkken geldi: ilk okumada yoktular
    assert int(events[-1]["id"]) > idle_rounds[0]


@pytest.fixture
def waiting_job(manager: JobManager) -> Iterator[Tuple[str, threading.Event]]:
    """Çalışan ve `release` gelene kadar bekleyen bir iş (başka bir thread'de); olay günlüğünde yalnızca `started`."""
    release = threading.Event()
    thread = threading.Thread(
        target=lambda: manager.submit(
            JobKind.SYNC, {}, lambda handle: release.wait(20) and None, origin=local_origin("cli"), background=False,
        ),
        daemon=True,
    )
    thread.start()
    job_id = wait_for(lambda: (manager.active() or SimpleNamespace(id=None)).id, what="the job to start")
    yield job_id, release
    release.set()
    thread.join(20)


def serve(response: Any, on_chunk: Any) -> List[bytes]:
    """
    Yanıtı bir ASGI çağrısı olarak çalıştırır ve gövde parçalarını toplar. `on_chunk(parça, disconnect)` her
    parçada çağrılır; `disconnect()` istemcinin koptuğunu bildirir.
    """
    chunks: List[bytes] = []

    async def run() -> None:
        gone = asyncio.Event()

        async def receive() -> Dict[str, Any]:
            await gone.wait()
            return {"type": "http.disconnect"}

        async def send(message: Dict[str, Any]) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                chunks.append(message["body"])
                on_chunk(message["body"], gone.set)

        scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}
        await asyncio.wait_for(response(scope, receive, send), 20)

    asyncio.run(run())
    return chunks


def test_a_comment_line_keeps_an_idle_stream_alive(manager: JobManager, waiting_job: Tuple[str, threading.Event]) -> None:
    job_id, release = waiting_job
    seen: List[bytes] = []

    def on_chunk(chunk: bytes, disconnect: Any) -> None:
        if chunk.startswith(b": keep-alive"):
            seen.append(chunk)
            if len(seen) == 2:
                release.set()  # iş, akış iki kez "canlıyım" dedikten sonra biter

    chunks = serve(sse.job_event_response(manager, job_id, poll=0.01, keepalive=0.03), on_chunk)

    messages = parse_sse(b"".join(chunks).decode("utf-8"))
    assert messages[0] == {"comment": "stream open"}
    assert sum(1 for m in messages if m.get("comment") == "keep-alive") >= 2
    assert [m["event"] for m in messages if "event" in m] == ["started", "finished"]


def test_the_stream_stops_when_the_client_is_gone(manager: JobManager, waiting_job: Tuple[str, threading.Event]) -> None:
    job_id, release = waiting_job

    def on_chunk(chunk: bytes, disconnect: Any) -> None:
        if b"event: started" in chunk:
            disconnect()

    chunks = serve(sse.job_event_response(manager, job_id, poll=0.01), on_chunk)

    # Yanıt, iş hâlâ çalışırken bitti
    assert not release.is_set() and manager.get(job_id).state is JobState.RUNNING
    assert [m["event"] for m in parse_sse(b"".join(chunks).decode("utf-8")) if "event" in m] == ["started"]


def test_the_stream_ends_when_the_server_shuts_down(manager: JobManager, waiting_job: Tuple[str, threading.Event]) -> None:
    """Açık bir akış sunucunun kapanmasını bekletmez: kapanış işareti gelince akış biter (iş sürse de)."""
    from sse_starlette.sse import AppStatus

    job_id, release = waiting_job

    def on_chunk(chunk: bytes, disconnect: Any) -> None:
        if b"event: started" in chunk:
            AppStatus.should_exit = True  # uvicorn'un çıkış sinyalinde olan budur

    try:
        chunks = serve(sse.job_event_response(manager, job_id, poll=0.01), on_chunk)
    finally:
        AppStatus.should_exit = False

    assert not release.is_set() and manager.get(job_id).state is JobState.RUNNING
    assert [m["event"] for m in parse_sse(b"".join(chunks).decode("utf-8")) if "event" in m] == ["started"]


def test_without_sse_starlette_the_stream_is_not_supported(
    logged_job: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "sse_starlette.sse", None)  # içe aktarma ImportError verir
    body = error(client.get(f"/api/v1/jobs/{logged_job}/events"), 501, "not_supported")
    assert body["details"] == {"package": "sse-starlette"}


def test_sse_framing() -> None:
    assert sse.format_event("log", {"message": "satır\nsonu", "n": 1}, event_id=7) == (
        'id: 7\nevent: log\ndata: {"message":"satır\\nsonu","n":1}\n\n'.encode("utf-8")
    )
    assert sse.format_event("stream.gap", {"oldest_seq": 3}) == b'event: stream.gap\ndata: {"oldest_seq":3}\n\n'
    assert sse.format_comment("stream open") == b": stream open\n\n"
    assert sse.keep_alive().encode() == b": keep-alive\r\n\r\n"
    assert (sse.KEEPALIVE_SECONDS, sse.GAP_EVENT, sse.MEDIA_TYPE) == (15.0, "stream.gap", "text/event-stream")


# --- meta: sağlık, durum, sporlar ----------------------------------------------------------------------


def test_health_reports_the_version_the_bridge_and_the_request_budget(store: JobStore) -> None:
    from sofascore_scraper import bridge_health, throttle

    health = data(client.get("/api/v1/health"))

    assert list(health) == ["status", "version", "api_version", "bridge", "connection", "throttle"]
    assert (health["status"], health["version"], health["api_version"]) == ("ok", __version__, "v1")
    assert health["bridge"] == bridge_health.public_snapshot() and health["throttle"] == throttle.status()


def test_status_reports_the_running_job(store: JobStore, body: Any) -> None:
    idle = data(client.get("/api/v1/status"))
    assert list(idle) == [
        "version", "api_version", "schema_version", "auth_required", "bridge", "connection", "throttle", "active_job", "live", "summary", "leases", "sinks", "schedule",
        "capabilities", "storage_error",
    ]
    assert (idle["version"], idle["api_version"], idle["auth_required"], idle["active_job"]) == (__version__, "v1", False, None)

    job = data(client.post("/api/v1/jobs", json={"kind": "sync"}), 202)
    assert data(client.get("/api/v1/status"))["active_job"]["id"] == job["id"]
    body.release.set()
    _ended(job["id"])
    assert data(client.get("/api/v1/status"))["active_job"] is None


def test_sports_are_the_registry() -> None:
    body = client.get("/api/v1/sports").json()

    assert [sport["slug"] for sport in body["data"]] == list(sports.sport_slugs())
    assert body["page"] == {"limit": len(sports.SPORTS), "next_cursor": None}
    football = data(client.get("/api/v1/sports/football"))
    assert football == body["data"][0]
    assert list(football) == ["slug", "name", "i18n_key", "score_family", "slices"]
    # P28: kayıt defterinin bütün dilimleri (oranlar ve maç dışı dilimler dahil; sahibi `owner`'da)
    assert [s["key"] for s in football["slices"]] == [s.key for s in sports.registered_slices()
                                                      if s.applies_to("football")]
    assert list(football["slices"][0]) == ["key", "path", "required", "default_enabled", "selected", "group", "owner",
                                           "phases", "keep_history", "max_age_seconds"]
    missing = error(client.get("/api/v1/sports/quidditch"), 404, "not_found")
    assert missing["details"] == {"slug": "quidditch"}


def test_v1_reads_do_not_depend_on_the_legacy_refresh(store: JobStore) -> None:
    """deps, iş deposunu çağrı anında okur: testin koyduğu depo v1'in gördüğü depodur."""
    assert deps.job_store() is store and deps.job_manager().store is store
