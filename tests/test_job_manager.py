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

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

import pytest

from src.store import JobRunningError, JobStore, LeaseHeld, StoreError, open_store
from src.store import jobs as store_jobs
from src.store import state as state_mod
from src.store.jobs import default_db_path
from src.store.state import StateDb


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
    jobs = JobStore(default_db_path(str(data_dir)))
    yield jobs
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
