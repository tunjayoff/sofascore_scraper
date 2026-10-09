"""
İşler iki süreç arasında (plan maddesi P11; docs/design/02-services.md 2.8). Tümü çevrimdışı.

Yardımcı süreç aynı veri dizininde iş yöneticisiyle bir iş çalıştırır (komut satırından başlatılmış bir indirme
gibi) ve iptal edilene ya da kendisine bir satır yazılana kadar sürdürür. Sınananlar:

  * kilit çekişmesi: iş çalışırken ikinci süreç iş başlatamaz ve sahibini öğrenir;
  * başka bir süreçten iptal: bayrak satıra yazılır, işi çalıştıran süreç onu okur ve işi `cancelled` bitirir;
  * bayat satır yalnızca kilit boştayken süpürülür: sahibi yaşayan iş `interrupted` yapılmaz, öldürülen yapılır;
  * kalp atışı: işi çalıştıran süreç `heartbeat_at` alanını ilerletir;
  * web sunucusu başka bir sürecin işini geçmişinde görür, yeni iş başlatmaz ve onu iptal edebilir.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator, List, Optional, Tuple

import pytest

from sofascore_scraper.web import deps
from sofascore_scraper.jobs.manager import JobManager, local_origin
from sofascore_scraper.jobs.model import JobKind, JobState
from sofascore_scraper.store import JobRunningError, JobStore, LeaseHeld
from sofascore_scraper.store.jobs import default_db_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Başka bir süreç: veri dizininde bir iş başlatır, "ready <iş kimliği>" yazar ve iptal edilene ya da stdin'den bir
# satır gelene kadar çalışır; bitince "done <durum>" yazar. argv: veri dizini, iptal yoklama aralığı, kalp atışı
RUNNER = """
import sys
import threading
import time

from sofascore_scraper.jobs.manager import JobManager, local_origin
from sofascore_scraper.jobs.model import JobKind
from sofascore_scraper.store import JobStore, default_db_path

store = JobStore(default_db_path(sys.argv[1]))
manager = JobManager(store, cancel_poll=float(sys.argv[2]), heartbeat=float(sys.argv[3]))
released = threading.Event()
threading.Thread(target=lambda: (sys.stdin.readline(), released.set()), daemon=True).start()


def body(handle):
    handle.progress.start_phase("details", 10)
    handle.progress.advance(3)
    handle.log("Fetching match details: league 17 (10 matches)")
    print("ready " + handle.id, flush=True)
    while not handle.cancelled() and not released.is_set():
        time.sleep(0.01)


job = manager.submit(
    JobKind.SYNC, {"mode": "full", "league_id": 17}, body, origin=local_origin("cli"), background=False,
    phases=("details",), payload={"league_id": 17, "mode": "full", "selections": None}, lease_purpose="headless",
)
print("done " + job.state.value, flush=True)
store.close()
"""


class Runner:
    """Çalışan yardımcı süreç ve başlattığı işin kimliği."""

    def __init__(self, proc: subprocess.Popen, job_id: str) -> None:
        self.proc = proc
        self.job_id = job_id
        self.pid = proc.pid

    def finish(self, timeout: float = 60.0) -> Tuple[str, str]:
        """Sürece bir satır yazar (iş biter) ve çıkışını bekler; (stdout, stderr) döner."""
        try:
            out, err = self.proc.communicate("\n", timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            out, err = self.proc.communicate()
        return out, err

    def wait(self, timeout: float = 60.0) -> Tuple[str, str]:
        """Sürecin kendiliğinden çıkmasını bekler (iptal edildi); stdin'e yazmaz."""
        deadline = time.monotonic() + timeout
        while self.proc.poll() is None:
            assert time.monotonic() < deadline, "helper process did not exit"
            time.sleep(0.02)
        out = self.proc.stdout.read() if self.proc.stdout else ""
        err = self.proc.stderr.read() if self.proc.stderr else ""
        return out, err


@contextlib.contextmanager
def running_job(data_dir: Any, *, cancel_poll: float = 0.05, heartbeat: float = 5.0) -> Iterator[Runner]:
    """Yardımcı süreci başlatır ve işi çalışır hale gelene kadar bekler; blok bitince süreç kapanır."""
    proc = subprocess.Popen(
        [sys.executable, "-c", RUNNER, str(data_dir), str(cancel_poll), str(heartbeat)],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    line = proc.stdout.readline().strip() if proc.stdout else ""
    if not line.startswith("ready "):
        proc.kill()
        pytest.fail(f"helper process did not start a job: {line!r} {proc.communicate()[1]}")
    runner = Runner(proc, line.split(" ", 1)[1])
    try:
        yield runner
    finally:
        if proc.poll() is None:
            runner.finish()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.close()
        proc.wait(60)


def wait_for(condition: Any, timeout: float = 30.0, what: str = "condition") -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        assert time.monotonic() < deadline, f"{what} did not happen within {timeout} s"
        time.sleep(0.02)


def row_status(db_path: str, job_id: str) -> Optional[str]:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return None if row is None else str(row[0])
    finally:
        conn.close()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def store(data_dir: Path) -> Iterator[JobStore]:
    """Bu sürecin iş deposu (web sunucusununki gibi); yardımcı süreçten önce açılır."""
    jobs = JobStore(default_db_path(str(data_dir)))
    yield jobs
    jobs.close()


@pytest.fixture
def manager(store: JobStore) -> JobManager:
    return JobManager(store, cancel_poll=0.05)


# --- kilit çekişmesi ---------------------------------------------------------------------------------


def test_a_job_of_another_process_refuses_a_second_job_and_names_its_owner(
    manager: JobManager, store: JobStore, data_dir: Path
) -> None:
    with running_job(data_dir) as runner:
        with pytest.raises(JobRunningError) as refused:
            manager.submit(JobKind.FETCH, {}, lambda handle: None, origin=local_origin("api"), background=False)
        held = refused.value.__cause__
        assert isinstance(held, LeaseHeld)
        assert (held.name, held.pid, held.purpose) == ("writer", runner.pid, "headless") and held.host

        # Başka sürecin işi bu süreçten okunur: durum, kaynak, belirtim ve ilerleme
        job = manager.active()
        assert job is not None and job.id == runner.job_id and job.state is JobState.RUNNING
        assert (job.kind, job.origin.face, job.origin.pid) == (JobKind.SYNC, "cli", runner.pid)
        assert job.spec == {"mode": "full", "league_id": 17}
        # Ayrıntılı ilerleme olay günlüğünden gelir: seyreltildiği için son değer bir saat turu (burada 50 ms) gecikebilir
        progress = wait_for(lambda: (p := manager.active().progress) and p["done"] == 3 and p, what="progress event")
        assert (progress["phase"], progress["total"]) == ("details", 10)
        assert store.snapshot()["is_running"] is False  # yansı süreç içidir
        (row,) = store.list_jobs()
        assert (row["id"], row["status"], row["is_running"]) == (runner.job_id, "running", True)
        assert row["log"] == ["[Running] Fetching match details: league 17 (10 matches)"]
        assert [job.id for job in manager.list()] == [runner.job_id]

        out, err = runner.finish()
        assert runner.proc.returncode == 0, err
        assert out.strip() == "done succeeded"

    finished = manager.get(runner.job_id)
    assert finished.state is JobState.SUCCEEDED and manager.active() is None
    # Kilit bırakıldı: bu süreç artık iş başlatabilir
    own = manager.submit(JobKind.FETCH, {}, lambda handle: None, origin=local_origin("api"), background=False)
    assert own.state is JobState.SUCCEEDED and [job.id for job in manager.list()] == [own.id, runner.job_id]


def test_a_job_waits_for_the_lease_of_another_process(manager: JobManager, data_dir: Path) -> None:
    with running_job(data_dir) as runner:
        import threading

        def release() -> None:
            runner.proc.stdin.write("\n")
            runner.proc.stdin.flush()

        threading.Timer(0.3, release).start()
        job = manager.submit(JobKind.FETCH, {}, lambda handle: None, origin=local_origin("api"), background=False,
                             wait_for_lease=30)
        assert job.state is JobState.SUCCEEDED
        runner.wait()
        assert runner.proc.returncode == 0


# --- başka bir süreçten iptal ------------------------------------------------------------------------


def test_cancel_from_another_process_stops_the_job(manager: JobManager, store: JobStore, data_dir: Path) -> None:
    with running_job(data_dir, cancel_poll=0.05) as runner:
        assert manager.cancel("no-such-job") is False
        assert manager.cancel(runner.job_id) is True

        out, err = runner.wait()  # süreç bayrağı satırdan okur ve kendiliğinden biter
        assert runner.proc.returncode == 0, err
        assert out.strip() == "done cancelled"

    job = manager.get(runner.job_id)
    assert job.state is JobState.CANCELLED and job.cancel_requested is True and job.finished_at
    types = [event.type for event in manager.events(runner.job_id)]
    assert types.count("cancel_requested") == 1 and types[-1] == "finished"
    assert store.get_job(runner.job_id)["log"][-1] == "[Cancelled] Cancelled"
    assert manager.cancel(runner.job_id) is False and store._leases.holder("writer") is None


# --- bayat satır yalnızca kilit boştayken süpürülür ---------------------------------------------------


def test_a_stale_row_is_reaped_only_when_the_lease_is_free(manager: JobManager, store: JobStore, data_dir: Path) -> None:
    with running_job(data_dir) as runner:
        # Sahibi yaşıyor: ne `reap_stale` ne de dizini açan yeni bir depo işi kesilmiş sayar
        assert manager.reap_stale() == 0
        fresh = JobStore(store.db_path)
        try:
            assert fresh.get_job(runner.job_id)["status"] == "running"
        finally:
            fresh.close()
        assert row_status(store.db_path, runner.job_id) == "running"

        runner.proc.kill()  # POSIX'te SIGKILL, Windows'ta TerminateProcess: süreç hiçbir şeyi temizleyemez
        runner.proc.wait(60)
        wait_for(lambda: store._leases.holder("writer") is None, what="lease release after the kill")
        assert row_status(store.db_path, runner.job_id) == "running"  # satır hâlâ "running" diyor

        assert manager.reap_stale() == 1
        job = manager.get(runner.job_id)
        assert job.state is JobState.INTERRUPTED and job.finished_at
        assert [event.type for event in manager.events(runner.job_id)][-1] == "finished"
        assert manager.active() is None and manager.reap_stale() == 0

    own = manager.submit(JobKind.FETCH, {}, lambda handle: None, origin=local_origin("api"), background=False)
    assert own.state is JobState.SUCCEEDED


def test_starting_a_job_reaps_the_row_of_a_killed_process(manager: JobManager, store: JobStore, data_dir: Path) -> None:
    with running_job(data_dir) as runner:
        runner.proc.kill()
        runner.proc.wait(60)
        wait_for(lambda: store._leases.holder("writer") is None, what="lease release after the kill")
        seen: List[Any] = []
        manager.submit(JobKind.FETCH, {}, lambda handle: seen.append(row_status(store.db_path, runner.job_id)),
                       origin=local_origin("api"), background=False)
        assert seen == ["interrupted"]


# --- kalp atışı --------------------------------------------------------------------------------------


def test_the_running_process_moves_the_heartbeat(manager: JobManager, data_dir: Path) -> None:
    with running_job(data_dir, cancel_poll=0.02, heartbeat=0.05) as runner:
        first = manager.get(runner.job_id).heartbeat_at
        assert first is not None
        second = wait_for(lambda: (beat := manager.get(runner.job_id).heartbeat_at) > first and beat, what="heartbeat")
        wait_for(lambda: manager.get(runner.job_id).heartbeat_at > second, what="second heartbeat")
        assert abs(second / 1000 - time.time()) < 120


# --- web sunucusu ve başka bir sürecin işi ------------------------------------------------------------


def test_the_web_api_sees_and_cancels_a_job_of_another_process(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from sofascore_scraper.web.app import app

    jobs = deps.job_store()
    if jobs.snapshot().get("is_running"):
        jobs.update(status="Cancelled", finished=True)
    client = TestClient(app)
    web_data_dir = os.path.dirname(os.path.dirname(jobs.db_path))

    with running_job(web_data_dir, cancel_poll=0.05) as runner:
        # Komut satırından başlatılmış gibi bir iş web arayüzünün iş geçmişinde görünür
        listed = client.get("/api/v1/jobs", params={"limit": 1}).json()["data"][0]
        assert (listed["id"], listed["state"]) == (runner.job_id, "running")
        assert client.get(f"/api/v1/jobs/{runner.job_id}").json()["data"]["state"] == "running"

        refused = client.post("/api/v1/jobs", json={"kind": "sync", "spec": {"league_id": 17}})
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "job_running"
        assert jobs.snapshot()["is_running"] is False

        cancelled = client.post(f"/api/v1/jobs/{runner.job_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        out, err = runner.wait()
        assert runner.proc.returncode == 0, err
        assert out.strip() == "done cancelled"

    assert client.get(f"/api/v1/jobs/{runner.job_id}").json()["data"]["state"] == "cancelled"
