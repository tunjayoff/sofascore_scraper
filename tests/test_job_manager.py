"""
İş yöneticisi (plan maddesi P11; docs/design/02-services.md 2.8, 01-storage.md 3.3 ve 9.3). Tümü çevrimdışı.

  * state geçişi 0002 ve iş deposunun yeni yöntemleri: sahip, kaynak, belirtim, kalp atışı, kimlikle iptal,
    olay günlüğü, saklama, `reap_stale`, yarım biten işin `partial` yazılması;
  * iş modeli: sıralanabilir kimlik, olay, depo kaydından Job'a çeviri, bitiş durumu kuralı;
  * JobManager: `start` / `run` / `submit`, tutamaç, seyreltilmiş olaylar, `job` akışı, iptal, kilit çakışması;
  * servis bağlamı: depoya ve iş yöneticisine giden yol, köprü sağlığının çalışma zamanı bilgilerine yazılması;
  * web işi ve komut satırı işi iş yöneticisinden geçer.

İki süreçle sınanan davranışlar tests/test_jobs_cross_process.py'dedir.
"""
from __future__ import annotations

import contextlib
import errno
import json
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional

import conftest
import pytest

from sofascore_scraper.web import deps
from sofascore_scraper import bridge_health
from sofascore_scraper.exceptions import StorageError
from sofascore_scraper.jobs import manager as manager_mod
from sofascore_scraper.jobs.manager import JobHandle, JobManager, JobNotActive, JobOutcome, local_origin
from sofascore_scraper.jobs.model import (
    ErrorInfo,
    Job,
    JobEvent,
    JobEventType,
    JobKind,
    JobState,
    Origin,
    breaker_error,
    job_from_record,
    new_job_id,
    terminal_state,
)
from sofascore_scraper.store import (
    DataOperationRunningError,
    JobRunningError,
    JobStore,
    LeaseHeld,
    StoreBusy,
    StoreError,
    open_store,
)
from sofascore_scraper.store import jobs as store_jobs
from sofascore_scraper.store import state as state_mod
from sofascore_scraper.store.jobs import default_db_path
from sofascore_scraper.store.state import StateDb


def raw(db_path: Any, sql: str, *args: Any) -> list:
    """Dosyayı depodan bağımsız, düz bir bağlantıyla sorgular."""
    conn = sqlite3.connect(os.fspath(db_path))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def wait_for(condition: Any, timeout: float = 20.0, what: str = "condition") -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        assert time.monotonic() < deadline, f"{what} did not happen within {timeout} s"
        time.sleep(0.01)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def store(data_dir: Path) -> Iterator[JobStore]:
    before = conftest.job_threads()
    jobs = JobStore(default_db_path(str(data_dir)))
    yield jobs
    # Arka plandaki iş, satırı bittikten sonra da depoya yazar: bağlantılar onun altından kapatılmaz
    conftest.join_job_threads(before)
    jobs.close()


# === state geçişi 0002 ===============================================================================


def test_migration_0002_upgrades_a_version_1_file_and_keeps_its_rows(tmp_path: Path) -> None:
    only_first = tmp_path / "migrations"
    only_first.mkdir()
    shutil.copy(os.path.join(state_mod.MIGRATIONS_DIR, "0001_initial.sql"), only_first)
    path = tmp_path / "state.db"
    v1 = StateDb(path, migrations_dir=only_first)
    with v1.write() as conn:
        conn.execute("INSERT INTO jobs (id, status, started_at) VALUES ('old', 'completed', '2026-09-01T10:00:00+00:00')")
    v1.close()

    upgraded = StateDb(path)
    try:
        assert upgraded.schema_version == 2
        columns = [row[1] for row in upgraded.connection().execute("PRAGMA table_info(jobs)")]
        assert columns[-5:] == ["origin_json", "spec_json", "error_json", "heartbeat_at", "created_at"]
        events = [row[1] for row in upgraded.connection().execute("PRAGMA table_info(job_events)")]
        assert events == ["job_id", "seq", "ts_ms", "type", "data_json"]
        row = upgraded.connection().execute(
            "SELECT status, origin_json, spec_json, error_json, heartbeat_at, created_at FROM jobs WHERE id = 'old'"
        ).fetchone()
        assert tuple(row) == ("completed", None, None, None, None, None)
    finally:
        upgraded.close()
    assert os.path.isfile(f"{path}.bak-v1")  # geçişten önceki dosyanın kopyası

    # Eski satır iş deposundan okunur: yeni alanları boştur
    jobs = JobStore(str(path))
    try:
        record = jobs.get_record("old")
        assert (record["status"], record["kind"], record["owner"]) == ("completed", "fetch", None)
        assert record["origin"] is None and record["spec"] is None and record["created_at"] is None
        assert jobs.get_job("old")["status"] == "completed"
    finally:
        jobs.close()


# === iş deposu ========================================================================================


def test_create_running_records_kind_owner_origin_spec_and_times(store: JobStore) -> None:
    job_id = store.create_running(
        {"mode": "full"}, job_id="J1", kind="sync", origin={"face": "cli", "pid": 7, "host": "box"},
        spec={"mode": "full", "league_id": 17},
    )
    assert job_id == "J1"
    (kind, owner, origin, spec, created, started, heartbeat), = raw(
        store.db_path, "SELECT kind, owner, origin_json, spec_json, created_at, started_at, heartbeat_at FROM jobs")
    assert kind == "sync" and json.loads(origin) == {"face": "cli", "pid": 7, "host": "box"}
    assert json.loads(spec) == {"mode": "full", "league_id": 17}
    assert created == started and created.endswith("+00:00")
    assert abs(heartbeat / 1000 - time.time()) < 60
    # owner: işi çalıştıran sürecin `writer` kilidi sahibi kimliği
    (holder,), = raw(store.db_path, "SELECT holder FROM leases WHERE name = 'writer'")
    assert owner == holder

    record = store.get_record("J1")
    assert record["live"] is True and record["status"] == "running" and record["payload"] == {"mode": "full"}
    assert record["origin"] == {"face": "cli", "pid": 7, "host": "box"} and record["error"] is None
    store.update(finished=True)
    assert store.get_record("J1")["live"] is False


def test_create_running_defaults_are_todays(store: JobStore) -> None:
    job_id = store.create_running({"mode": "details"})
    assert len(job_id) == 36  # kimlik verilmezse uuid4 (eski çağıranlar)
    (kind, origin, spec), = raw(store.db_path, "SELECT kind, origin_json, spec_json FROM jobs")
    assert (kind, origin, spec) == ("fetch", None, None)
    assert store._leases.holder("writer").purpose == "job"
    store.update(finished=True)


def test_lease_purpose_is_passed_on_and_cannot_be_an_operation(store: JobStore) -> None:
    store.create_running({}, purpose="headless")
    assert store._leases.holder("writer").purpose == "headless"
    store.update(finished=True)
    with pytest.raises(ValueError):
        store.create_running({}, purpose="op:backup")
    assert store.snapshot()["is_running"] is False and store._leases.holder("writer") is None


@pytest.mark.parametrize("fields, stored", [
    ({}, "completed"),
    ({"circuit_breaker_triggered": True, "circuit_breaker_reason": "403"}, "partial"),
    ({"matches_failed": 2}, "partial"),
])
def test_a_job_with_a_breaker_stop_or_failed_items_is_stored_as_partial(
    store: JobStore, fields: Dict[str, Any], stored: str
) -> None:
    job_id = store.create_running({})
    if fields:
        store.update(**fields)
    store.update(status="Completed", progress=100, finished=True)

    assert raw(store.db_path, "SELECT status FROM jobs WHERE id = ?", job_id) == [(stored,)]
    # Eski API ve canlı yansı bugünkü adı gösterir
    assert store.get_job(job_id)["status"] == "completed" and store.list_jobs()[0]["status"] == "completed"
    assert store.snapshot()["status"] == "Completed" and store.snapshot()["is_running"] is False
    assert store.get_record(job_id)["status"] == stored


@pytest.mark.parametrize("status", [None, "Running", "Completed"])
def test_a_job_that_finishes_after_a_cancel_request_is_stored_as_cancelled(
    store: JobStore, status: Optional[str]
) -> None:
    job_id = store.create_running({})
    assert store.request_cancel() is True
    store.update(status=status, finished=True) if status else store.update(finished=True)
    assert raw(store.db_path, "SELECT status FROM jobs WHERE id = ?", job_id) == [("cancelled",)]
    assert store.snapshot()["status"] == "Cancelled"


def test_a_failed_job_stays_failed_after_a_cancel_request(store: JobStore) -> None:
    job_id = store.create_running({})
    store.request_cancel()
    store.update(status="Failed", finished=True)
    assert store.get_job(job_id)["status"] == "failed"


def test_an_unknown_final_status_is_not_stored_verbatim(store: JobStore, caplog: pytest.LogCaptureFixture) -> None:
    job_id = store.create_running({})
    store.update(status="Exploded", finished=True)
    assert store.get_job(job_id)["status"] == "failed" and store.snapshot()["status"] == "Failed"
    assert any("unknown status" in record.getMessage() for record in caplog.records)


def test_explicit_state_and_error_are_written(store: JobStore) -> None:
    job_id = store.create_running({})
    store.update(status="Completed", state="partial", finished=True,
                 error={"code": "blocked", "message": "stopped", "details": {"reason": "403"}})
    record = store.get_record(job_id)
    assert record["status"] == "partial"
    assert record["error"] == {"code": "blocked", "message": "stopped", "details": {"reason": "403"}}

    succeeded = store.create_running({})
    store.update(state="succeeded", finished=True)  # modelin adı: satıra eski adıyla yazılır
    assert store.get_record(succeeded)["status"] == "completed"

    other = store.create_running({})
    with pytest.raises(ValueError):
        store.update(state="nonsense", finished=True)
    assert store.snapshot()["job_id"] == other
    store.update(finished=True)


def test_an_update_for_another_job_id_is_ignored(store: JobStore) -> None:
    first = store.create_running({})
    store.update(finished=True)
    second = store.create_running({})
    store.update(job_id=first, progress=99, status="Failed", finished=True)  # geç kalan yazma
    assert store.snapshot()["job_id"] == second and store.snapshot()["is_running"] is True
    assert store.snapshot()["progress"] == 0
    store.update(job_id=second, progress=5)
    assert store.snapshot()["progress"] == 5
    store.update(finished=True)


def test_cancel_by_id_reaches_a_job_run_by_another_store(store: JobStore) -> None:
    other = JobStore(store.db_path)  # aynı dizini açmış ikinci bir depo (başka bir süreç gibi)
    try:
        job_id = store.create_running({})
        assert other.cancel("no-such-job") is False
        assert other.cancel(job_id) is True
        assert store.cancel_requested() is False  # sahibi bayrağı henüz okumadı
        # Sahibinin ilerleme yazması bayrağı silmez
        store.update(progress=10, current_task="working")
        assert raw(store.db_path, "SELECT cancel_requested FROM jobs WHERE id = ?", job_id) == [(1,)]
        assert store.poll_cancel(job_id) is True
        assert store.cancel_requested() is True and store.cancel_requested(job_id) is True
        assert store.cancel_requested("another") is False
        assert store.snapshot()["current_task"] == "Cancellation requested..."
        store.update(finished=True)
        assert store.get_job(job_id)["status"] == "cancelled"
        assert other.cancel(job_id) is False  # bitmiş iş iptal edilemez
        assert store.poll_cancel() is False
    finally:
        other.close()


def test_a_cancel_written_just_before_the_end_still_counts(store: JobStore) -> None:
    other = JobStore(store.db_path)
    try:
        job_id = store.create_running({})
        assert other.cancel(job_id) is True
        store.update(status="Completed", finished=True)  # sahibi bayrağı hiç okumadan bitirdi
        assert store.get_job(job_id)["status"] == "cancelled"
    finally:
        other.close()


def test_cancel_of_the_own_job_is_request_cancel(store: JobStore) -> None:
    job_id = store.create_running({})
    assert store.cancel(job_id) is True
    assert store.cancel_requested() is True and store.snapshot()["current_task"] == "Cancellation requested..."
    store.update(finished=True)


def test_heartbeat_moves_the_rows_heartbeat(store: JobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    assert store.heartbeat() is False  # çalışan iş yok
    job_id = store.create_running({})
    monkeypatch.setattr(store_jobs, "_now_ms", lambda: 1_790_000_000_123)
    assert store.heartbeat(job_id) is True
    assert raw(store.db_path, "SELECT heartbeat_at FROM jobs WHERE id = ?", job_id) == [(1_790_000_000_123,)]
    assert store.heartbeat("another") is False
    store.update(finished=True)


def test_events_are_numbered_per_job_and_read_in_order(store: JobStore) -> None:
    assert store.append_event("a", "log", {"message": "one"}) == 1
    assert store.append_event("a", "log", {"message": "iki ✓"}) == 2
    assert store.append_event("b", "phase") == 1
    events = store.read_events("a")
    assert [(e["seq"], e["type"], e["data"]) for e in events] == [
        (1, "log", {"message": "one"}), (2, "log", {"message": "iki ✓"})]
    assert all(e["job_id"] == "a" and e["ts_ms"] > 0 for e in events)
    assert [e["seq"] for e in store.read_events("a", after=1)] == [2]
    assert [e["seq"] for e in store.read_events("a", limit=1)] == [1]
    assert store.read_events("b")[0]["data"] == {} and store.read_events("none") == []


def test_only_the_newest_events_of_a_job_are_kept(store: JobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_jobs, "JOB_EVENTS_LIMIT", 30)
    monkeypatch.setattr(store_jobs, "_EVENT_PRUNE_EVERY", 10)
    for n in range(1, 96):
        store.append_event("a", "log", {"n": n})
    store.append_event("b", "log", {})
    kept = [e["seq"] for e in store.read_events("a", limit=1000)]
    # Budama on olayda bir çalışır: en yeni 30 ile 39 arası olay kalır, numaralar yeniden kullanılmaz
    assert kept == list(range(61, 96))
    assert store.append_event("a", "log", {}) == 96
    assert len(store.read_events("b")) == 1


def test_history_keeps_the_newest_rows_and_drops_their_events(store: JobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_jobs, "JOB_HISTORY_LIMIT", 3)
    ticks = iter(range(100))
    monkeypatch.setattr(store_jobs, "_utc_now", lambda: f"2026-10-02T12:00:{next(ticks):02d}+00:00")
    ids = []
    for _ in range(5):
        ids.append(store.create_running({}))
        store.append_event(ids[-1], "log", {})
        store.update(finished=True)

    # Budama iş yaratılırken çalışır: beşinci iş yaratıldığında en yeni üç satır kalır (kendisi dahil)
    assert [row[0] for row in raw(store.db_path, "SELECT id FROM jobs ORDER BY started_at")] == ids[2:]
    assert {row[0] for row in raw(store.db_path, "SELECT DISTINCT job_id FROM job_events")} == set(ids[2:])
    assert [job["id"] for job in store.list_jobs()] == ids[:1:-1]


def test_default_retention_is_the_designs(store: JobStore) -> None:
    assert (store_jobs.JOB_HISTORY_LIMIT, store_jobs.JOB_EVENTS_LIMIT) == (500, 2000)


# --- canlılık: satır + kilit -------------------------------------------------------------------------


def test_opening_a_second_store_does_not_interrupt_the_running_job(store: JobStore) -> None:
    """Eski koşulsuz süpürme, ikinci bir sunucu başlarken birincinin çalışan işini `interrupted` yapardı."""
    job_id = store.create_running({})
    second = JobStore(store.db_path)
    try:
        assert second.reap_stale() == 0
        assert second.get_job(job_id)["status"] == "running" and second.list_jobs()[0]["is_running"] is True
        assert second.snapshot()["is_running"] is False  # yansı süreç içidir
        assert second.active_record()["id"] == job_id and second.active_record()["live"] is False
        with pytest.raises(JobRunningError):
            second.create_running({})
    finally:
        second.close()
    store.update(finished=True)
    assert store.get_job(job_id)["status"] == "completed"


def test_a_running_row_is_reaped_once_its_lease_is_free(store: JobStore, caplog: pytest.LogCaptureFixture) -> None:
    crashed = JobStore(store.db_path)
    job_id = crashed.create_running({})
    crashed.update(progress=40, current_task="Fetching")
    assert store.reap_stale() == 0  # kilit tutuluyor: iş çalışıyor
    crashed.close()  # süreç öldü: kilit düştü, satır "running" kaldı

    assert raw(store.db_path, "SELECT status FROM jobs WHERE id = ?", job_id) == [("running",)]
    assert store.reap_stale() == 1 and store.reap_stale() == 0
    row = store.get_job(job_id)
    assert row["status"] == "interrupted" and row["finished_at"] and row["current_task"] == "Fetching"
    assert [(e["type"], e["data"]) for e in store.read_events(job_id)] == [("finished", {"state": "interrupted"})]
    assert any(job_id in record.getMessage() for record in caplog.records)


def test_reading_the_history_reaps_stale_rows(store: JobStore) -> None:
    crashed = JobStore(store.db_path)
    job_id = crashed.create_running({})
    crashed.close()
    assert store.list_jobs()[0]["status"] == "interrupted"  # okuyan fark eder
    assert store.active_record() is None and store.cancel(job_id) is False


def test_the_own_running_job_is_never_reaped_and_an_orphan_is(store: JobStore) -> None:
    first = store.create_running({})
    assert store.reap_stale() == 0 and store.get_job(first)["status"] == "running"
    # Aynı depo, işini bitirmeden ikinci bir iş başlatırsa (bugünkü davranış: kilit yeniden kullanılır) birincisi
    # sahipsiz kalır; kilit bu depoda olduğu için bayat sayılır
    second = store.create_running({})
    assert store.get_job(first)["status"] == "interrupted" and store.get_job(second)["status"] == "running"
    store.update(finished=True)


def test_legacy_sweep_still_interrupts_everything(store: JobStore) -> None:
    job_id = store.create_running({})
    assert store.mark_stale_running_interrupted() == 1
    assert store.get_job(job_id)["status"] == "interrupted" and store.snapshot()["status"] == "Idle"
    assert store._leases.holder("writer") is None


# --- Store'a bağlı iş deposu -------------------------------------------------------------------------


def test_for_store_shares_the_stores_database_and_is_cached(data_dir: Path) -> None:
    opened = open_store(data_dir)
    jobs = JobStore.for_store(opened)
    assert JobStore.for_store(opened) is jobs
    assert jobs.db_path == str(data_dir / ".meta" / "state.db")

    job_id = jobs.create_running({"mode": "full"})
    assert opened.lease_holder("writer").purpose == "job"
    with pytest.raises(LeaseHeld):
        opened.lease("writer", purpose="headless")
    jobs.announce("job.started", {"job_id": job_id})
    assert [event.type for event in opened.streams.read(streams=["job"]).events] == ["job.started"]
    jobs.update(finished=True)
    assert opened.lease_holder("writer") is None

    jobs.close()  # deponun bağlantısı Store'undur: iş deposu onu kapatmaz
    assert opened.info(sizes=False).rows["state"]["jobs"] == 1

    opened.close()
    with pytest.raises(StoreError):
        JobStore.for_store(opened)
    reopened = open_store(data_dir)
    assert JobStore.for_store(reopened) is not jobs
    assert [job["id"] for job in JobStore.for_store(reopened).list_jobs()] == [job_id]


def test_announce_appends_to_the_job_stream_of_a_path_store(store: JobStore, data_dir: Path) -> None:
    assert store.announce("job.finished", {"job_id": "x", "state": "partial"}) is not None
    rows = raw(store.db_path, "SELECT stream, type, source, payload_json FROM stream_events")
    assert [(r[0], r[1], r[2], json.loads(r[3])) for r in rows] == [
        ("job", "job.finished", "job", {"job_id": "x", "state": "partial"})]


# === iş modeli =======================================================================================


def test_job_ids_sort_by_creation_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from sofascore_scraper.jobs import model as model_mod

    monkeypatch.setattr(model_mod, "_last_id", (0, 0))  # süreçte daha önce üretilen kimliklerden bağımsız
    first, second = new_job_id(now_ms=1_790_000_000_000), new_job_id(now_ms=1_790_000_000_001)
    assert len(first) == 26 and first < second
    assert set(first) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    assert first[:10] != second[:10] and first[:8] == second[:8]  # ilk on karakter zamandır (milisaniye)

    burst = [new_job_id(now_ms=1_790_000_000_002) for _ in range(2000)]  # aynı milisaniyede üretilenler de artar
    assert burst == sorted(burst) and len(set(burst)) == len(burst) and burst[0] > second
    assert {job_id[:10] for job_id in burst} == {burst[0][:10]}
    earlier = new_job_id(now_ms=1)  # saat geri gitse de kimlikler azalmaz
    assert earlier > burst[-1]
    assert new_job_id() > earlier  # gerçek saat


def test_job_event_types() -> None:
    assert [str(member) for member in JobEventType] == [
        "started", "phase", "progress", "log", "failed", "breaker", "cancel_requested", "finished"]
    # Deponun kendisinin yazdığı iki tür modeldekiyle aynıdır
    assert (store_jobs.EVENT_PROGRESS, store_jobs.EVENT_FINISHED) == (JobEventType.PROGRESS, JobEventType.FINISHED)
    event = JobEvent(job_id="j", seq=1, ts_ms=5, type="log")
    assert event.data == {}


def test_terminal_state_rule() -> None:
    assert terminal_state() is JobState.SUCCEEDED
    assert terminal_state(failed_items=1) is JobState.PARTIAL and terminal_state(breaker=True) is JobState.PARTIAL
    assert terminal_state(cancelled=True, breaker=True, failed_items=3) is JobState.CANCELLED
    assert terminal_state(failed=True, cancelled=True, breaker=True) is JobState.FAILED


@pytest.mark.parametrize("reason, code", [("403", "blocked"), ("429", "rate_limited"), ("5xx", "upstream_error"),
                                          ("other", "upstream_error")])
def test_breaker_reason_maps_to_an_error_code_of_the_table(reason: str, code: str) -> None:
    from sofascore_scraper.errors import ERRORS

    error = breaker_error(reason)
    assert error.code == code and code in ERRORS and error.details == {"reason": reason}


def test_job_from_record_normalises_old_rows() -> None:
    base = {"id": "j", "kind": "fetch", "started_at": "2026-10-01T12:00:00+00:00", "payload": {"mode": "full"}}
    # D15: eski `completed` satırı, başarısız maçı olsa da olduğu gibi okunur; yalnızca devre kesici bayrağına bakılır
    assert job_from_record({**base, "status": "completed", "matches_failed": 3}).state is JobState.SUCCEEDED
    assert job_from_record({**base, "status": "completed", "circuit_breaker_triggered": True}).state is JobState.PARTIAL
    assert job_from_record({**base, "status": "partial"}).state is JobState.PARTIAL
    assert job_from_record({**base, "status": "interrupted"}).state is JobState.INTERRUPTED
    assert job_from_record({**base, "status": "Exploded"}).state is JobState.FAILED  # eski kodun olduğu gibi yazdığı metin

    old = job_from_record({**base, "status": "running"})
    assert old == Job(id="j", kind=JobKind.FETCH, state=JobState.RUNNING, origin=Origin(face="api"),
                      spec={"mode": "full"}, created_at=base["started_at"], started_at=base["started_at"])

    new = job_from_record({
        **base, "status": "failed", "kind": "refresh", "origin": {"face": "cli", "pid": 9, "host": "box"},
        "spec": {"mode": "refresh"}, "detail": {"phase": "details"}, "result": {"failed_count": 0},
        "error": {"code": "storage_error", "message": "disk full", "details": {"path": "/x"}},
        "created_at": "2026-10-01T11:59:59+00:00", "finished_at": "2026-10-01T12:01:00+00:00",
        "heartbeat_at": 1790000000000, "cancel_requested": 1,
    })
    assert (new.kind, new.origin) == (JobKind.REFRESH, Origin(face="cli", pid=9, host="box"))
    assert new.spec == {"mode": "refresh"} and new.progress == {"phase": "details"}
    assert new.error == ErrorInfo("storage_error", "disk full", {"path": "/x"})
    assert (new.created_at, new.heartbeat_at, new.cancel_requested) == ("2026-10-01T11:59:59+00:00", 1790000000000, True)
    assert job_from_record({**base, "status": "running", "kind": "unknown-kind"}).kind is JobKind.FETCH


# === JobManager ======================================================================================

WEB = Origin(face="api", pid=1, host="box")


@pytest.fixture
def manager(store: JobStore) -> JobManager:
    # Saat hızlı çalışır: iptal bayrağı ve kalp atışı testte saniyeler beklemez
    return JobManager(store, cancel_poll=0.02, heartbeat=0.05, progress_interval=0.5)


def event_types(manager: JobManager, job_id: str) -> List[str]:
    return [event.type for event in manager.events(job_id)]


def test_a_row_written_before_migration_0002_reads_as_a_job(store: JobStore, manager: JobManager) -> None:
    """Eski satır: kaynak web'dir, created_at yerine started_at, belirtim yerine istek gövdesi okunur."""
    with store._state.write() as conn:
        conn.execute("INSERT INTO jobs (id, status, started_at, payload_json) VALUES "
                     "('old', 'completed', '2026-09-01T10:00:00+00:00', '{\"mode\": \"full\"}')")
    job = manager.get("old")
    assert job is not None and job.state is JobState.SUCCEEDED and job.origin == Origin(face="api")
    assert job.created_at == job.started_at == "2026-09-01T10:00:00+00:00" and job.spec == {"mode": "full"}
    assert job.heartbeat_at is None and job.error is None and job.progress is None


def test_submit_inline_runs_the_job_and_returns_it_finished(manager: JobManager, store: JobStore) -> None:
    seen: Dict[str, Any] = {}

    def body(handle: JobHandle) -> None:
        seen["id"], seen["cancelled"] = handle.id, handle.cancelled()
        seen["holder"] = store._leases.holder("writer").purpose
        seen["running"] = manager.active()
        handle.progress.start_phase("details", 2)
        handle.log("Fetching match details", code="details_started", league_id=17)
        handle.progress.advance(1)
        handle.progress.advance(2)
        handle.publish({"schedule_empty_seasons": 1})

    job = manager.submit(JobKind.SYNC, {"mode": "details", "league_id": 17}, body, origin=WEB, background=False,
                         phases=("details",), payload={"league_id": 17, "mode": "details", "selections": None},
                         lease_purpose="headless")

    assert job.id == seen["id"] and len(job.id) == 26 and seen["cancelled"] is False
    assert seen["holder"] == "headless" and seen["running"].id == job.id and seen["running"].state is JobState.RUNNING
    assert (job.kind, job.state, job.origin) == (JobKind.SYNC, JobState.SUCCEEDED, WEB)
    assert job.spec == {"mode": "details", "league_id": 17} and job.error is None and job.result is None
    assert job.created_at == job.started_at and job.finished_at and job.heartbeat_at is not None
    assert job.progress["phase"] == "details" and (job.progress["done"], job.progress["total"]) == (2, 2)
    assert manager.get(job.id) == job and manager.active() is None
    assert store._leases.holder("writer") is None

    row = store.get_job(job.id)  # eski biçim: web arayüzünün iş geçmişi bunu gösterir
    assert row["status"] == "completed" and row["progress"] == 100 and row["schedule_empty_seasons"] == 1
    assert row["payload"] == {"league_id": 17, "mode": "details", "selections": None}
    assert row["log"] == ["[Running] Fetching match details", "[Completed] Finished"]
    assert (row["matches_done"], row["matches_total"], row["current_task"]) == (2, 2, "Finished")

    events = list(manager.events(job.id))
    assert all(isinstance(event, JobEvent) and event.job_id == job.id for event in events)
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    types = [event.type for event in events]
    assert types[0] == "started" and types[-1] == "finished" and types.count("phase") == 1 and "progress" in types
    by_type = {event.type: event.data for event in events}
    assert by_type["started"] == {"kind": "sync", "origin": {"face": "api", "pid": 1, "host": "box"}}
    assert by_type["phase"] == {"phase": "details", "phase_index": 1, "phase_count": 1, "total": 2}
    assert by_type["log"] == {"message": "Fetching match details", "code": "details_started", "league_id": 17}
    assert by_type["finished"] == {"state": "succeeded", "message": "Finished"}
    assert list(manager.events(job.id, after=len(events) - 1)) == events[-1:]

    stream = raw(store.db_path, "SELECT type, source, payload_json FROM stream_events WHERE stream = 'job' ORDER BY seq")
    assert [(r[0], r[1]) for r in stream] == [("job.started", "job"), ("job.finished", "job")]
    assert json.loads(stream[0][2]) == {"job_id": job.id, "kind": "sync",
                                        "origin": {"face": "api", "pid": 1, "host": "box"}}
    finished = json.loads(stream[1][2])
    assert (finished["job_id"], finished["kind"], finished["state"], finished["error_code"]) == (
        job.id, "sync", "succeeded", None)
    assert finished["counts"] == {"details_done": 2, "details_total": 2, "failed_count": 0, "refreshed": 0,
                                  "refresh_changed": 0}


def test_failed_items_make_the_job_partial(manager: JobManager, store: JobStore) -> None:
    def body(handle: JobHandle) -> None:
        handle.progress.start_phase("details", 2)
        handle.progress.add_failed("9100001", league_id=17)
        handle.progress.advance(2)

    job = manager.submit(JobKind.FETCH, {}, body, origin=WEB, background=False, phases=("details",))

    assert job.state is JobState.PARTIAL and job.error is None
    assert raw(store.db_path, "SELECT status FROM jobs WHERE id = ?", job.id) == [("partial",)]
    assert store.get_job(job.id)["status"] == "completed"  # eski API
    failed = [event.data for event in manager.events(job.id) if event.type == "failed"]
    assert failed == [{"match_id": "9100001", "league_id": 17}]
    assert store.get_job(job.id)["log"][-1] == "[Completed] Finished with failed items"


def test_a_breaker_stop_is_partial_with_the_error_code_of_its_reason(manager: JobManager, store: JobStore) -> None:
    def body(handle: JobHandle) -> JobOutcome:
        handle.progress.start_phase("details", 9)
        handle.progress.breaker("403")
        return JobOutcome(result={"breaker": "403"}, message="stopped (translated)", code="fetch_stopped_by_breaker",
                          params={"reason": "403"})

    job = manager.submit(JobKind.FETCH, {}, body, origin=WEB, background=False, phases=("details",))

    assert job.state is JobState.PARTIAL and job.result == {"breaker": "403"}
    assert job.error == ErrorInfo("blocked", "stopped by the circuit breaker (403)", {"reason": "403"})
    row = store.get_job(job.id)
    assert (row["status"], row["circuit_breaker_triggered"], row["circuit_breaker_reason"]) == ("completed", True, "403")
    assert row["current_task"] == "stopped (translated)" and row["progress"] == 100
    events = {event.type: event.data for event in manager.events(job.id)}
    assert events["breaker"] == {"reason": "403"}
    # Kartın metnini istemci koddan üretir: serbest metin yalnızca eski istemciler içindir
    assert events["finished"] == {
        "state": "partial", "message": "stopped (translated)", "code": "fetch_stopped_by_breaker",
        "params": {"reason": "403"},
        "error": {"code": "blocked", "message": "stopped by the circuit breaker (403)", "details": {"reason": "403"}},
    }
    (payload,), = raw(store.db_path, "SELECT payload_json FROM stream_events WHERE type = 'job.finished'")
    assert json.loads(payload)["error_code"] == "blocked"


def test_an_exception_fails_the_job_releases_the_lease_and_is_raised(manager: JobManager, store: JobStore) -> None:
    def body(handle: JobHandle) -> None:
        handle.progress.start_phase("details", 4)
        handle.progress.advance(1)
        raise StorageError.from_exception(OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)), "/data/match_details/x")

    with pytest.raises(StorageError):
        manager.submit(JobKind.FETCH, {}, body, origin=WEB, background=False, phases=("details",))

    job = manager.list()[0]
    assert job.state is JobState.FAILED and job.error is not None and job.error.code == "storage_error"
    assert job.error.details["path"] == "/data/match_details/x"
    row = store.get_job(job.id)
    assert row["status"] == "failed" and row["progress"] == 25 and row["current_task"].startswith("Error: ")
    assert store._leases.holder("writer") is None and store.snapshot()["is_running"] is False
    assert event_types(manager, job.id)[-1] == "finished"
    manager.submit(JobKind.FETCH, {}, lambda handle: None, origin=WEB, background=False)  # yeni iş başlayabilir


def test_an_unexpected_error_is_internal_and_its_secrets_are_masked(
    manager: JobManager, store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sofascore_scraper import redact

    monkeypatch.setenv("USE_PROXY", "true")
    monkeypatch.setenv("PROXY_URL", "http://user:hunter2secret@proxy.example:8080")
    redact.refresh()
    try:
        def body(handle: JobHandle) -> None:
            raise RuntimeError("CONNECT tunnel failed via http://user:hunter2secret@proxy.example:8080")

        with pytest.raises(RuntimeError):
            manager.submit(JobKind.FETCH, {}, body, origin=WEB, background=False)
    finally:
        monkeypatch.undo()
        redact.refresh()

    job = manager.list()[0]
    assert job.state is JobState.FAILED and job.error.code == "internal"
    stored = json.dumps(raw(store.db_path, "SELECT error_json, current_task, log_json FROM jobs")
                        + raw(store.db_path, "SELECT data_json FROM job_events"))
    assert "hunter2secret" not in stored and "proxy.example" in stored


def test_ctrl_c_ends_the_job_as_cancelled(manager: JobManager, store: JobStore) -> None:
    def body(handle: JobHandle) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        manager.submit(JobKind.SYNC, {}, body, origin=local_origin("cli"), background=False)

    job = manager.list()[0]
    assert job.state is JobState.CANCELLED and job.error is None
    assert job.origin.pid == os.getpid() and job.origin.host and job.origin.face == "cli"
    assert store._leases.holder("writer") is None


def test_background_job_is_cancelled_through_its_row(manager: JobManager, store: JobStore) -> None:
    entered = threading.Event()

    def body(handle: JobHandle) -> None:
        handle.progress.start_phase("details", 100)
        entered.set()
        deadline = time.monotonic() + 30
        while not handle.cancelled() and time.monotonic() < deadline:
            time.sleep(0.005)

    job = manager.submit(JobKind.FETCH, {}, body, origin=WEB, background=True, phases=("details",))
    assert job.state is JobState.RUNNING and entered.wait(10)
    assert manager.active().id == job.id
    with pytest.raises(JobRunningError):
        manager.submit(JobKind.FETCH, {}, lambda handle: None, origin=WEB, background=False)

    # İptal satıra başka bir depodan yazılır (başka bir süreç gibi): işin saati bayrağı okur
    other = JobStore(store.db_path)
    try:
        assert JobManager(other).cancel(job.id) is True
    finally:
        other.close()

    finished = wait_for(lambda: (j := manager.get(job.id)) and j.state.terminal and j, what="job end")
    assert finished.state is JobState.CANCELLED and finished.cancel_requested is True
    types = event_types(manager, job.id)
    assert types.count("cancel_requested") == 1 and types[-1] == "finished"
    assert store.get_job(job.id)["log"][-1] == "[Cancelled] Cancelled"
    assert manager.cancel(job.id) is False and manager.active() is None
    wait_for(lambda: store._leases.holder("writer") is None, what="lease release")


def test_a_job_that_finishes_after_a_cancel_request_is_cancelled(manager: JobManager, store: JobStore) -> None:
    def body(handle: JobHandle) -> JobOutcome:
        assert store.request_cancel() is True  # iptal istendi ama gövde bakmadan bitirdi
        return JobOutcome(state=JobState.SUCCEEDED, message="done anyway")

    job = manager.submit(JobKind.FETCH, {}, body, origin=WEB, background=False)
    assert job.state is JobState.CANCELLED
    assert event_types(manager, job.id) == ["started", "cancel_requested", "finished"]


def test_background_failure_is_recorded_not_raised(manager: JobManager, store: JobStore) -> None:
    def body(handle: JobHandle) -> None:
        raise ValueError("boom")

    job = manager.submit(JobKind.FETCH, {}, body, origin=WEB, background=True)
    finished = wait_for(lambda: (j := manager.get(job.id)) and j.state.terminal and j, what="job end")
    assert finished.state is JobState.FAILED and finished.error.code == "internal" and "boom" in finished.error.message


def test_submit_waits_for_the_lease_when_asked(manager: JobManager, store: JobStore) -> None:
    other = JobStore(store.db_path)
    try:
        other.create_running({})
        started = time.monotonic()
        with pytest.raises(JobRunningError) as refused:
            manager.submit(JobKind.FETCH, {}, lambda handle: None, origin=WEB, background=False, wait_for_lease=0.3)
        assert time.monotonic() - started >= 0.25
        assert isinstance(refused.value.__cause__, LeaseHeld) and refused.value.__cause__.pid == os.getpid()
        assert manager.list() and all(job.origin != WEB for job in manager.list())  # reddedilen iş kaydedilmedi

        threading.Timer(0.2, lambda: other.update(finished=True)).start()
        job = manager.submit(JobKind.FETCH, {}, lambda handle: None, origin=WEB, background=False, wait_for_lease=20)
        assert job.state is JobState.SUCCEEDED
    finally:
        other.close()


def test_a_data_operation_refuses_a_job(manager: JobManager, store: JobStore) -> None:
    with store.exclusive("clear"):
        with pytest.raises(DataOperationRunningError):
            manager.submit(JobKind.FETCH, {}, lambda handle: None, origin=WEB, background=False)
    assert manager.list() == []


def test_the_ticker_writes_heartbeats(manager: JobManager, store: JobStore) -> None:
    beats: List[int] = []

    def body(handle: JobHandle) -> None:
        first = manager.get(handle.id).heartbeat_at
        beats.append(first)
        wait_for(lambda: manager.get(handle.id).heartbeat_at > first, what="heartbeat")
        beats.append(manager.get(handle.id).heartbeat_at)

    manager.submit(JobKind.FETCH, {}, body, origin=WEB, background=False)
    assert beats[1] > beats[0]
    assert (manager_mod.CANCEL_POLL_SECONDS, manager_mod.HEARTBEAT_SECONDS) == (1.0, 5.0)  # tasarımdaki aralıklar


def test_progress_events_are_coalesced_and_the_last_one_is_kept(store: JobStore) -> None:
    manager = JobManager(store, cancel_poll=0.01, heartbeat=5, progress_interval=60)

    def body(handle: JobHandle) -> None:
        handle.progress.start_phase("details", 500)
        for n in range(1, 501):
            if n % 100 == 0:
                handle.progress.add_failed(str(n), league_id=17)
            handle.progress.advance(n)
        time.sleep(0.1)  # işin saati birkaç tur atar: aralık dolmadan bekleyen ilerlemeyi yazmaz

    job = manager.submit(JobKind.FETCH, {}, body, origin=WEB, background=False, phases=("details",))

    events = list(manager.events(job.id))
    progress = [event.data for event in events if event.type == "progress"]
    # İlk ilerleme hemen, bekleyen son ilerleme bitişte: 505 yayından iki ilerleme olayı. Diğer olayların hepsi yazılır.
    assert [(p["done"], p["percent"], p["failed_count"]) for p in progress] == [(0, 0, 0), (500, 99, 5)]
    assert [event.data["match_id"] for event in events if event.type == "failed"] == ["100", "200", "300", "400", "500"]
    assert job.progress["done"] == 500 and store.get_job(job.id)["matches_done"] == 500


def test_the_ticker_writes_a_waiting_progress_event_once_the_interval_has_passed(store: JobStore) -> None:
    manager = JobManager(store, cancel_poll=0.01, heartbeat=5, progress_interval=0.05)
    seen: List[int] = []

    def body(handle: JobHandle) -> None:
        handle.progress.start_phase("details", 10)
        handle.progress.advance(4)  # aralık dolmadı: bekler
        wait_for(lambda: [e for e in manager.events(handle.id) if e.type == "progress" and e.data["done"] == 4],
                 what="the waiting progress event")
        seen.append(len([e for e in manager.events(handle.id) if e.type == "progress"]))

    manager.submit(JobKind.FETCH, {}, body, origin=WEB, background=False, phases=("details",))
    assert seen == [2]


def test_events_can_be_followed_until_the_job_ends(manager: JobManager, store: JobStore) -> None:
    release = threading.Event()

    def body(handle: JobHandle) -> None:
        handle.log("first")
        assert release.wait(20)
        handle.log("second")

    job = manager.submit(JobKind.FETCH, {}, body, origin=WEB, background=True)
    follower = JobManager(store)
    seen: List[str] = []

    def follow() -> None:
        for event in follower.events(job.id, follow=True, poll=0.01):
            seen.append(event.type if event.type != "log" else event.data["message"])
            if event.data.get("message") == "first":
                release.set()

    thread = threading.Thread(target=follow)
    thread.start()
    thread.join(20)
    assert not thread.is_alive()
    assert seen == ["started", "first", "second", "finished"]
    assert list(follower.events("no-such-job", follow=True)) == []


def test_list_filters_by_kind_and_state_newest_first(manager: JobManager, store: JobStore) -> None:
    def failing(handle: JobHandle) -> None:
        handle.progress.start_phase("details", 1)
        handle.progress.add_failed("1")

    done = manager.submit(JobKind.SYNC, {}, lambda handle: None, origin=WEB, background=False)
    partial = manager.submit(JobKind.FETCH, {}, failing, origin=WEB, background=False, phases=("details",))
    refreshed = manager.submit(JobKind.REFRESH, {}, lambda handle: None, origin=WEB, background=False)

    assert [job.id for job in manager.list()] == [refreshed.id, partial.id, done.id]
    assert [job.id for job in manager.list(limit=2)] == [refreshed.id, partial.id]
    assert [job.id for job in manager.list(kinds=[JobKind.SYNC, JobKind.REFRESH])] == [refreshed.id, done.id]
    assert [job.id for job in manager.list(states=[JobState.PARTIAL])] == [partial.id]
    assert manager.list(states=[JobState.RUNNING]) == [] and manager.get("no-such-job") is None


def test_run_refuses_a_job_that_is_not_running_here(manager: JobManager, store: JobStore) -> None:
    with pytest.raises(JobNotActive):
        manager.run("no-such-job", lambda handle: None)
    job = manager.start(JobKind.FETCH, {}, origin=WEB)
    store.update(status="Cancelled", finished=True)
    with pytest.raises(JobNotActive):
        manager.run(job.id, lambda handle: None)


def test_start_then_run_is_the_same_as_submit(manager: JobManager, store: JobStore) -> None:
    changes: List[str] = []
    logged: List[str] = []
    job = manager.start(JobKind.FETCH, {"mode": "full"}, origin=WEB)
    assert job.state is JobState.RUNNING and store.snapshot()["job_id"] == job.id

    finished = manager.run(job.id, lambda handle: handle.log("working"), phases=(),
                           on_change=lambda: changes.append(store.snapshot()["status"]), on_log=logged.append)

    assert finished.state is JobState.SUCCEEDED and logged == ["working"]
    assert changes[0] == "Running" and changes[-1] == "Completed"


def test_a_failing_event_write_does_not_stop_the_job(
    manager: JobManager, store: JobStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(job_id: str, type: str, data: Any = None) -> int:
        raise StoreError("database is locked")

    monkeypatch.setattr(store, "append_event", broken)
    monkeypatch.setattr(store, "announce", lambda type, data: (_ for _ in ()).throw(StoreError("busy")))

    job = manager.submit(JobKind.FETCH, {}, lambda handle: handle.log("still works"), origin=WEB, background=False)

    assert job.state is JobState.SUCCEEDED and store.get_job(job.id)["log"][0] == "[Running] still works"
    assert any("could not be stored" in record.getMessage() for record in caplog.records)


def _busy_updates(store: JobStore, monkeypatch: pytest.MonkeyPatch, fail: Any) -> List[Dict[str, Any]]:
    """
    `fail(alanlar)` True döndürdüğü `store.update` çağrılarında state.db yazma kilidini vermez: gerçekte olduğu
    gibi StoreBusy yazma işlemi başlarken, yansı güncellendikten sonra fırlar.
    """
    calls: List[Dict[str, Any]] = []
    busy = {"on": False}
    real_update, real_write = store.update, store._state.write

    @contextlib.contextmanager
    def write() -> Iterator[Any]:
        if busy["on"]:
            raise StoreBusy("state.db meşgul", detail="database is locked")
        with real_write() as conn:
            yield conn

    def update(**fields: Any) -> None:
        calls.append(fields)
        busy["on"] = bool(fail(fields))
        try:
            real_update(**fields)
        finally:
            busy["on"] = False

    monkeypatch.setattr(store._state, "write", write)
    monkeypatch.setattr(store, "update", update)
    return calls


def test_a_busy_state_database_does_not_end_the_job(
    manager: JobManager, store: JobStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Başka bir yazar kilidi 5 saniyeden uzun tutarsa (StoreBusy) ilerleme yazması atlanır, iş sürer."""
    calls = _busy_updates(store, monkeypatch, lambda fields: not fields.get("finished") and len(calls) % 2 == 1)

    def body(handle: JobHandle) -> None:
        handle.progress.start_phase("details", 4)
        handle.log("working")
        for n in range(1, 5):
            handle.progress.advance(n)
        handle.publish({"schedule_empty_seasons": 2})

    job = manager.submit(JobKind.FETCH, {}, body, origin=WEB, background=False, phases=("details",))

    assert job.state is JobState.SUCCEEDED and len(calls) >= 7
    row = store.get_job(job.id)  # atlanan yazmaları sonraki yazma tamamladı: satır eksiksiz
    assert (row["matches_done"], row["matches_total"], row["schedule_empty_seasons"]) == (4, 4, 2)
    assert row["log"] == ["[Running] working", "[Completed] Finished"]
    busy = [record for record in caplog.records if "state database is busy" in record.getMessage()]
    assert len([record for record in busy if record.levelname == "WARNING"]) == 1  # bir kez uyarı, sonrası ayıklama


def test_the_end_of_a_job_is_retried_when_the_state_database_is_busy(
    manager: JobManager, store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    finals: List[int] = []

    def fail(fields: Dict[str, Any]) -> bool:
        if fields.get("finished"):
            finals.append(1)
            return len(finals) < 3
        return False

    _busy_updates(store, monkeypatch, fail)
    job = manager.submit(JobKind.FETCH, {}, lambda handle: handle.log("working"), origin=WEB, background=False)

    assert len(finals) == 3 and job.state is JobState.SUCCEEDED
    assert store.get_job(job.id)["log"] == ["[Running] working", "[Completed] Finished"]  # son satır bir kez
    assert store._leases.holder("writer") is None and store.snapshot()["is_running"] is False


def test_a_job_whose_end_cannot_be_written_is_reaped_later(
    manager: JobManager, store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _busy_updates(store, monkeypatch, lambda fields: bool(fields.get("finished")))
    with pytest.raises(StoreBusy):
        manager.submit(JobKind.FETCH, {}, lambda handle: None, origin=WEB, background=False)
    monkeypatch.undo()

    # Kilit bırakıldı, satır "running" kaldı: dizini açan başka bir depo onu kesilmiş sayar
    assert store._leases.holder("writer") is None and store.snapshot()["is_running"] is False
    other = JobStore(store.db_path)
    try:
        (row,) = other.list_jobs()
        assert row["status"] == "interrupted"
    finally:
        other.close()
    assert manager.submit(JobKind.FETCH, {}, lambda handle: None, origin=WEB, background=False).state.terminal


# === servis bağlamı ==================================================================================


@pytest.fixture
def context(data_dir: Path) -> Any:
    from sofascore_scraper.config_manager import ConfigManager
    from sofascore_scraper.services.context import build_context

    return build_context(ConfigManager(), data_dir=str(data_dir))


def test_the_context_opens_the_store_only_when_it_is_asked_for(context: Any, data_dir: Path) -> None:
    from sofascore_scraper.client import Client
    from sofascore_scraper.store import api

    assert not (data_dir / ".meta").exists() and api._registry == {}
    assert isinstance(context.client, Client)

    opened = context.store
    assert opened is open_store(data_dir) and context.store is opened
    assert sorted(os.listdir(data_dir / ".meta"))[:1] == ["catalog.db"] and (data_dir / ".meta" / "schema.json").is_file()

    jobs = context.jobs
    assert isinstance(jobs, JobManager) and jobs.store is JobStore.for_store(opened) is context.jobs.store
    job = jobs.submit(JobKind.SYNC, {}, lambda handle: None, origin=local_origin("cli"), background=False)
    assert context.jobs.get(job.id).state is JobState.SUCCEEDED


def test_bridge_health_changes_reach_the_runtime_facts_of_the_store(context: Any, data_dir: Path) -> None:
    from sofascore_scraper.config_manager import ConfigManager
    from sofascore_scraper.services import context as context_mod
    from sofascore_scraper.services.context import build_context

    for _ in range(3):  # bağlam her işte kurulur: geri çağrı yine tek kez eklenir
        build_context(ConfigManager(), data_dir=str(data_dir))
    assert bridge_health._health._change_callbacks.count(context_mod._record_bridge_health) == 1

    for _ in range(int(bridge_health.thresholds()["degraded_after"])):
        bridge_health.record_failure(bridge_health.KIND_FORBIDDEN, "HTTP 403")
    fact = open_store(data_dir).runtime.get("bridge_health")
    assert fact is not None and fact.pid == os.getpid()
    assert fact.value["state"] == bridge_health.DEGRADED and fact.value == bridge_health.snapshot()

    bridge_health.record_success()
    assert open_store(data_dir).runtime.get("bridge_health").value["state"] == bridge_health.OK


def test_a_health_change_that_cannot_be_stored_does_not_break_the_request(
    context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sofascore_scraper.services import context as context_mod

    def refuse(data_dir: Any) -> Any:
        raise StoreError("state.db is newer than this code")

    monkeypatch.setattr(context_mod, "open_store", refuse)
    for _ in range(int(bridge_health.thresholds()["degraded_after"])):
        bridge_health.record_failure(bridge_health.KIND_FORBIDDEN, "HTTP 403")
    assert bridge_health.snapshot()["state"] == bridge_health.DEGRADED


# === web işi =========================================================================================


@pytest.fixture
def web(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Web işini kendi thread'i olmadan, geçici bir iş deposu ve sahte bir servis bağlamıyla çalıştırır."""
    import sofascore_scraper.web.api.legacy as fj
    from sofascore_scraper.web.api.legacy import FetchRequest

    jobs = JobStore(str(tmp_path / "web" / ".meta" / "state.db"))
    monkeypatch.setattr(deps, "job_store", lambda: jobs)
    monkeypatch.setattr(deps, "refresh_job_mirror", lambda: jobs.snapshot())

    class Details:
        rate_limit_breaker_triggered = False
        last_status_counts: Dict[str, int] = {}
        refresh_listener = None
        breaker_on: Optional[str] = None
        failing: List[str] = []

        def begin_job_cache(self) -> None: ...
        def end_job_cache(self) -> None: ...

        def collect_detail_match_ids(self, league_id: Any = None, max_seasons: int = 0, only_season_ids: Any = None) -> List[str]:
            return ["a", "b", "c"]

        def pending_detail_ids(self, ids: List[str]) -> List[str]:
            return list(ids)

        def fetch_detail_ids(self, ids: List[str], progress_callback: Any = None, should_cancel: Any = None,
                             failed_callback: Any = None) -> int:
            for n, match_id in enumerate(ids, start=1):
                if match_id in self.failing:
                    failed_callback(match_id)
                progress_callback(n, len(ids), "")
            if self.breaker_on:
                self.rate_limit_breaker_triggered = True
                self.last_status_counts = {self.breaker_on: 9}
            return len(ids)

    details = Details()
    ctx = SimpleNamespace(config=deps.config_manager(), match_data_fetcher=details)
    monkeypatch.setattr(fj, "build_context", lambda config_manager: ctx)

    def run(**payload: Any) -> Job:
        request = FetchRequest(**payload)
        import dataclasses

        job = fj.job_manager().start(JobKind.FETCH, dataclasses.asdict(fj._spec_from_payload(request)),
                                     origin=local_origin("api"), payload=request.model_dump())
        fj.run_fetch_job(job.id, request)
        return fj.job_manager().get(job.id)

    yield SimpleNamespace(run=run, details=details, jobs=jobs, fj=fj)
    jobs.close()


def test_web_job_is_recorded_with_its_kind_origin_and_spec(web: Any) -> None:
    job = web.run(mode="details", league_id=17)

    assert (job.kind, job.state) == (JobKind.FETCH, JobState.SUCCEEDED)
    assert job.origin == Origin(face="api", pid=os.getpid(), host=job.origin.host) and job.origin.host
    assert job.spec == {"mode": "details", "league_id": 17, "selections": []}
    assert job.result["details_done"] == 3 and job.result["schedule_empty_seasons"] == 0
    row = web.jobs.get_job(job.id)
    assert row["payload"] == {"league_id": 17, "mode": "details", "selections": None}
    assert row["log"][0] == "[Running] Starting fetch for 17"
    assert row["log"][-1] == "[Completed] Background Task Completed Successfully."
    types = [event.type for event in web.fj.job_manager().events(job.id)]
    assert types[0] == "started" and types[-1] == "finished" and types.count("phase") == 1  # details (EX-1: export yok)


def test_web_job_stopped_by_the_breaker_is_partial_and_still_renders_completed(web: Any) -> None:
    web.details.breaker_on = "429"
    job = web.run(mode="details", league_id=17)

    assert job.state is JobState.PARTIAL and job.error.code == "rate_limited"
    assert raw(web.jobs.db_path, "SELECT status FROM jobs WHERE id = ?", job.id) == [("partial",)]
    row = web.jobs.get_job(job.id)
    assert (row["status"], row["circuit_breaker_triggered"]) == ("completed", True)
    assert web.jobs.snapshot()["status"] == "Completed"
    finished = [event.data for event in web.fj.job_manager().events(job.id) if event.type == "finished"][0]
    # Sunucunun diline çevrilmiş kart metni yerine kod: istemci metni kendisi üretir
    assert (finished["code"], finished["params"]) == ("fetch_stopped_by_breaker", {"reason": "429"})
    assert finished["message"] == row["current_task"]


def test_web_job_with_a_failed_match_is_partial(web: Any) -> None:
    web.details.failing = ["b"]
    job = web.run(mode="details", league_id=17)
    assert job.state is JobState.PARTIAL and job.error is None and job.result["failed_count"] == 1
    assert web.jobs.get_job(job.id)["status"] == "completed"


def test_web_job_that_cannot_write_fails_with_the_storage_code(web: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def full_disk(ids: List[str], **kwargs: Any) -> int:
        raise StorageError.from_exception(OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)), "/data/match_details/17")

    monkeypatch.setattr(web.details, "fetch_detail_ids", full_disk)
    job = web.run(mode="details", league_id=17)

    assert job.state is JobState.FAILED and job.error.code == "storage_error"
    assert job.result["error"] == "storage" and job.result["error_path"] == "/data/match_details/17"
    finished = [event.data for event in web.fj.job_manager().events(job.id) if event.type == "finished"][0]
    assert finished["code"] == "storage_error_abort" and finished["params"]["path"] == "/data/match_details/17"


def test_a_web_job_makes_its_data_directory_a_full_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Gerçek bağlamla: iş, deposunu açar; `.meta/` altında schema.json ve catalog.db de oluşur."""
    import sofascore_scraper.web.api.legacy as fj
    from sofascore_scraper.web.api.legacy import FetchRequest

    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    jobs = JobStore(default_db_path(str(data_dir)))  # web sunucusunun iş deposu: yalnızca state.db kurar
    monkeypatch.setattr(deps, "job_store", lambda: jobs)
    monkeypatch.setattr(deps, "refresh_job_mirror", lambda: jobs.snapshot())
    try:
        with pytest.raises(StoreError):
            open_store(data_dir, create=False)  # henüz bir depo değil: schema.json yok

        fj.run_fetch_job(jobs.create_running({"mode": "details"}), FetchRequest(mode="details", league_id=17))

        assert jobs.snapshot()["status"] == "Completed"
        assert {"schema.json", "state.db", "catalog.db"} <= set(os.listdir(data_dir / ".meta"))
        assert open_store(data_dir, create=False).info(sizes=False).rows["state"]["jobs"] == 1

        # Depo açılamazsa iş yine çalışır
        class NoStore:
            def __init__(self, ctx: Any) -> None:
                self.config, self.match_data_fetcher = ctx.config, ctx.match_data_fetcher

            @property
            def store(self) -> Any:
                raise StoreError("state.db bu koddan yeni")

        real = fj.build_context
        monkeypatch.setattr(fj, "build_context", lambda config_manager: NoStore(real(config_manager)))
        fj.run_fetch_job(jobs.create_running({"mode": "details"}), FetchRequest(mode="details", league_id=17))
        assert jobs.snapshot()["status"] == "Completed"
        assert any("could not be opened" in record.getMessage() for record in caplog.records)
    finally:
        jobs.close()


def test_a_job_finished_before_its_thread_started_is_left_alone(web: Any, caplog: pytest.LogCaptureFixture) -> None:
    from sofascore_scraper.web.api.legacy import FetchRequest

    job_id = web.jobs.create_running({})
    web.jobs.update(status="Cancelled", finished=True)
    web.fj.run_fetch_job(job_id, FetchRequest(mode="details", league_id=17))
    assert web.jobs.get_job(job_id)["status"] == "cancelled"
    assert any("no longer the running job" in record.getMessage() for record in caplog.records)


def test_api_fetch_starts_a_job_through_the_manager_and_cancel_reaches_another_stores_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from sofascore_scraper.web.api import legacy as fetch_job
    from sofascore_scraper.web.app import app

    jobs = deps.job_store()
    monkeypatch.setattr(fetch_job, "run_fetch_job", lambda job_id, payload: None)
    if jobs.snapshot().get("is_running"):
        jobs.update(status="Cancelled", finished=True)
    client = TestClient(app)

    started = client.post("/api/fetch", json={"mode": "full", "league_id": 17})
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    try:
        record = jobs.get_record(job_id)
        assert len(job_id) == 26 and record["kind"] == "fetch" and record["origin"]["face"] == "api"
        assert record["spec"] == {"mode": "full", "league_id": 17, "selections": []}
        assert record["payload"] == {"league_id": 17, "mode": "full", "selections": None}
    finally:
        jobs.update(status="Cancelled", finished=True)

    # Bu süreçte çalışan iş yokken iptal: veri dizininde başka bir deponun (sürecin) çalışan işi iptal edilir
    assert client.post("/api/scrape/cancel").status_code == 400
    other = JobStore(jobs.db_path)
    try:
        foreign = other.create_running({"mode": "full"}, kind="sync", origin={"face": "cli", "pid": 1, "host": "x"})
        listed = client.get("/api/jobs").json()["jobs"][0]
        assert (listed["id"], listed["status"], listed["is_running"]) == (foreign, "running", True)
        assert client.get("/api/scrape/status").json()["is_running"] is False  # yansı süreç içidir
        cancelled = client.post("/api/scrape/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelling"
        assert other.poll_cancel(foreign) is True
        other.update(finished=True)
        assert client.get(f"/api/jobs/{foreign}").json()["status"] == "cancelled"
    finally:
        other.close()


# === komut satırı ====================================================================================


@pytest.fixture
def cli(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """`main.py <argv> --data-dir <geçici dizin>`i bu süreçte çalıştırır; SyncService.run sahtedir."""
    import main as cli_main
    from sofascore_scraper.services.sync import RefreshCounts, SyncResult, SyncService

    monkeypatch.setenv("DATA_DIR", os.environ["DATA_DIR"])  # main --data-dir ortamı değiştirir: test sonunda geri al
    state: Dict[str, Any] = {"breaker": None, "error": None, "failed": 0, "cancel": False, "seen": []}

    def run(self: SyncService, spec: Any, *, handle: Any = None) -> SyncResult:
        state["seen"].append((spec, handle.id, JobManager(JobStore.for_store(open_store(data_dir))).active()))
        handle.progress.start_phase("details", 2)
        handle.log("Checking which matches need details...")
        for n in range(state["failed"]):
            handle.progress.add_failed(str(n))
        if state["breaker"]:
            handle.progress.breaker(state["breaker"])
        if state["error"] is not None:
            raise state["error"]
        if state["cancel"]:
            # İptal, veri dizinini açmış başka bir depodan gelir (web sunucusu gibi); işin saati bayrağı okur
            web_store = JobStore(default_db_path(str(data_dir)))
            try:
                assert web_store.cancel(handle.id) is True
            finally:
                web_store.close()
            wait_for(handle.cancelled, what="cancel flag")
            return SyncResult(state="cancelled", schedule_empty_seasons=0, breaker=None,
                              progress=handle.progress.result())
        handle.progress.advance(2)
        progress = handle.progress.result()
        return SyncResult(
            state="partial" if progress["breaker"] or progress["failed_count"] else "succeeded",
            schedule_empty_seasons=0, breaker=progress["breaker"], progress=progress,
            refresh=RefreshCounts(due=2, refreshed=2) if spec.mode == "refresh" else None,
        )

    monkeypatch.setattr(SyncService, "run", run)

    def call(*argv: str) -> int:
        monkeypatch.setattr("sys.argv", ["main.py", *argv, "--data-dir", str(data_dir)])
        return cli_main.main()

    def jobs() -> List[Job]:
        return JobManager(JobStore.for_store(open_store(data_dir))).list()

    return SimpleNamespace(call=call, state=state, jobs=jobs)


@pytest.mark.parametrize("argv, kind, spec", [
    (["--headless", "--update-all", "--league-id", "17"], JobKind.SYNC,
     {"league_id": 17, "selections": [], "follows": [], "only": None, "event_ids": []}),
    (["--refresh-only"], JobKind.REFRESH, {"league_id": None, "event_ids": []}),
])
def test_cli_runs_appear_in_the_job_history(cli: Any, data_dir: Path, argv: List[str], kind: JobKind, spec: Dict[str, Any]) -> None:
    assert cli.call(*argv) == 0

    (job,) = cli.jobs()
    (seen_spec, handle_id, running), = cli.state["seen"]
    assert handle_id == job.id and running.id == job.id and running.state is JobState.RUNNING
    assert (job.kind, job.state, job.spec) == (kind, JobState.SUCCEEDED, spec)
    assert job.origin == Origin(face="cli", pid=os.getpid(), host=job.origin.host) and job.origin.host
    assert job.result["details_total"] == 2 and job.finished_at

    # Web sunucusunun iş deposu aynı satırı eski biçimde gösterir (kartın başlığı istek gövdesinden üretilir)
    web_store = JobStore(default_db_path(str(data_dir)))
    try:
        (row,) = web_store.list_jobs()
        assert (row["id"], row["status"], row["is_running"]) == (job.id, "completed", False)
        mode = {JobKind.SYNC: "full", JobKind.REFRESH: "refresh"}[kind]  # the legacy card keeps the service's mode
        assert row["payload"] == {"league_id": spec["league_id"], "mode": mode, "selections": None}
        assert row["log"][0] == "[Running] Checking which matches need details..."
    finally:
        web_store.close()


def test_cli_job_stopped_by_the_breaker_is_partial_and_exits_with_4(cli: Any) -> None:
    cli.state["breaker"] = "403"
    assert cli.call("--headless", "--update-all") == 4  # P19: devre kesici 4 (önce 2)
    (job,) = cli.jobs()
    assert job.state is JobState.PARTIAL and job.error.code == "blocked"


def test_cli_job_with_a_storage_error_is_failed_and_exits_with_5(cli: Any, capsys: pytest.CaptureFixture[str]) -> None:
    cli.state["error"] = StorageError.from_exception(OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)), "/data/x")
    assert cli.call("--headless", "--update-all") == 5  # P19: depolama hatası 5 (önce 1)
    (job,) = cli.jobs()
    assert job.state is JobState.FAILED and job.error.code == "storage_error"
    assert "/data/x" in capsys.readouterr().err


def test_cli_job_is_refused_with_exit_code_6_while_another_job_runs(
    cli: Any, data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    web_store = JobStore(default_db_path(str(data_dir)))
    try:
        web_store.create_running({"mode": "full"})
        assert cli.call("--headless", "--update-all") == 6
        assert cli.call("--refresh-only") == 6
        err = capsys.readouterr().err
        assert err.count(f"Held by process {os.getpid()} on ") == 2 and "(job)" in err
        assert cli.state["seen"] == [] and len(cli.jobs()) == 1  # yalnızca web işi kayıtlı
        web_store.update(finished=True)
        assert cli.call("--refresh-only") == 0
    finally:
        web_store.close()


@pytest.mark.parametrize("argv", [["--headless", "--update-all"], ["--refresh-only"]])
def test_cli_job_cancelled_from_another_store_ends_like_ctrl_c(
    cli: Any, capsys: pytest.CaptureFixture[str], argv: List[str]
) -> None:
    cli.state["cancel"] = True
    assert cli.call(*argv) == 130  # P19: iptal edilen iş 130 (önce 0)
    (job,) = cli.jobs()
    assert job.state is JobState.CANCELLED and job.cancel_requested is True
    captured = capsys.readouterr()
    assert f"Cancelled: job {job.id} stopped" in captured.err
    assert "Download finished" not in captured.out and "Refresh:" not in captured.out
