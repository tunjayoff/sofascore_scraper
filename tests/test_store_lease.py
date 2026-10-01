"""
src/store/lease.py ve iş deposunun kilitlerle çalışması (docs/design/01-storage.md bölüm 6.1; plan maddesi ST-10).

Kilit tablosu (kim kimi dışlar), sahip bilgisi, temiz kapanmama işareti, bekleme ve iki süreçli durumlar:
ikinci yazar sahibin bilgisiyle reddedilir, süreç öldürülünce kilit boşalır, `maintenance` yazarı ve
izleyiciyi dışlar (ve tersi). İş deposunun hata sınıfları ve arayüzü değişmez; yalnızca artık başka bir
sürecin işini de görür. Tümü çevrimdışı; Linux, macOS ve Windows'ta çalışır.
"""
from __future__ import annotations

import contextlib
import errno
import gc
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterator, Optional

import pytest

from src.store import LayoutError, LeaseHeld, StoreError, layout
from src.store import jobs as jobs_mod
from src.store import lease as lease_mod
from src.store.jobs import DataOperationRunningError, JobRunningError, JobStore, default_db_path
from src.store.lease import EXCLUSIVE, SHARED, Lease, LeaseInfo, LeaseManager, lock_plan
from src.store.state import StateDb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NAMES = ["writer", "watcher:tennis", "watcher:football", "live", "sinks", "maintenance"]
# Bölüm 6.1'deki tablo: aynı anda tutulamayan çiftler (sırasız)
CONFLICTS = {
    frozenset(pair)
    for pair in [
        ("writer", "writer"), ("writer", "maintenance"),
        ("watcher:tennis", "watcher:tennis"), ("watcher:football", "watcher:football"),
        ("watcher:tennis", "live"), ("watcher:football", "live"),
        ("watcher:tennis", "maintenance"), ("watcher:football", "maintenance"),
        ("live", "live"), ("live", "maintenance"),
        ("sinks", "sinks"),
        ("maintenance", "maintenance"),
    ]
}

# Başka bir süreç: veri dizininin kilidini alır, "ready" yazar ve stdin'den bir satır gelene kadar tutar
HOLDER = """
import sys
from src.store.jobs import default_db_path
from src.store.lease import LeaseManager
from src.store.state import StateDb
state = StateDb(default_db_path(sys.argv[1]))
lease = LeaseManager.for_data_dir(sys.argv[1], state).acquire(sys.argv[2], purpose=sys.argv[3])
print("ready", flush=True)
sys.stdin.readline()
lease.release()
state.close()
"""

# İş deposunu kullanan başka bir süreç: iş başlatır ya da bir veri işlemi yuvasını tutar
JOB_HOLDER = """
import sys
from src.store.jobs import JobStore, default_db_path
store = JobStore(default_db_path(sys.argv[1]))
if sys.argv[2] == "job":
    store.create_running({"mode": "full"})
    print("ready", flush=True)
    sys.stdin.readline()
    store.update(status="Completed", finished=True)
else:
    with store.exclusive(sys.argv[2]):
        print("ready", flush=True)
        sys.stdin.readline()
"""


@contextlib.contextmanager
def other_process(script: str, *args: object) -> Iterator[subprocess.Popen]:
    """Betiği ayrı bir süreçte başlatır ve "ready" yazmasını bekler; blok bitince süreç temiz kapanır."""
    proc = subprocess.Popen(
        [sys.executable, "-c", script, *map(str, args)],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        line = proc.stdout.readline()
        if line.strip() != "ready":
            proc.kill()
            pytest.fail(f"yardımcı süreç başlayamadı: {proc.communicate()[1]}")
        yield proc
    finally:
        if proc.poll() is None:
            try:
                _out, err = proc.communicate("\n", timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
                _out, err = proc.communicate()
            assert proc.returncode == 0, err
        else:
            proc.communicate()


def wait_until_free(manager: LeaseManager, name: str, timeout: float = 20.0) -> None:
    """Öldürülen sürecin kilidini işletim sistemi bırakana kadar bekler (Windows'ta hemen olmayabilir)."""
    deadline = time.monotonic() + timeout
    while manager.holder(name) is not None:
        assert time.monotonic() < deadline, f"{name} kilidi {timeout} sn içinde boşalmadı"
        time.sleep(0.05)


@pytest.fixture
def data_dir(tmp_path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def manager(data_dir) -> Iterator[LeaseManager]:
    state = StateDb(default_db_path(str(data_dir)))
    yield LeaseManager.for_data_dir(data_dir, state)
    state.close()


# --- kilit tablosu -------------------------------------------------------------------------------

def test_lock_plan_is_the_table_of_the_design():
    assert lock_plan("writer") == (("maintenance", SHARED), ("writer", EXCLUSIVE))
    assert lock_plan("watcher:tennis") == (("maintenance", SHARED), ("live", SHARED), ("watcher:tennis", EXCLUSIVE))
    assert lock_plan("live") == (("maintenance", SHARED), ("live", EXCLUSIVE))
    assert lock_plan("sinks") == (("sinks", EXCLUSIVE),)
    assert lock_plan("maintenance") == (("maintenance", EXCLUSIVE),)


@pytest.mark.parametrize("name", ["", "job", "watcher", "writer:x", "live:tennis", "maintenance:1", "Writer", "a/b", None])
def test_unknown_lease_names_are_refused(manager, name):
    with pytest.raises(LayoutError):
        manager.acquire(name)
    assert not os.path.isdir(manager.locks_dir) or os.listdir(manager.locks_dir) == []


def test_lock_files_live_under_meta_locks(manager, data_dir):
    with manager.acquire("watcher:tennis"), manager.acquire("writer"), manager.acquire("sinks"):
        assert manager.locks_dir == str(data_dir / ".meta" / "locks")
        assert sorted(os.listdir(manager.locks_dir)) == [
            "live.lock", "maintenance.lock", "sinks.lock", "unclean", "watcher-tennis.lock", "writer.lock"]
    # Kilit dosyaları kalır (silinmez); işaret temiz bırakılışta gider
    assert "unclean" not in os.listdir(manager.locks_dir)
    assert "writer.lock" in os.listdir(manager.locks_dir)


@pytest.mark.parametrize("held", NAMES)
@pytest.mark.parametrize("wanted", NAMES)
def test_exclusion_table(manager, held, wanted):
    """Tablodaki her sıralı çift: `held` tutulurken `wanted` ya alınır ya da engelleyenin adıyla reddedilir."""
    with manager.acquire(held, purpose="first"):
        if frozenset((held, wanted)) in CONFLICTS:
            with pytest.raises(LeaseHeld) as refused:
                manager.acquire(wanted)
            assert refused.value.name == held
            assert refused.value.purpose == "first"
        else:
            with manager.acquire(wanted) as second:
                assert second.held
    # İlki bırakılınca ikincisi her durumda alınabilir; reddedilen deneme geride kilit bırakmamıştır
    with manager.acquire(wanted):
        pass
    assert manager.holders() == []


def test_two_watchers_and_a_writer_block_maintenance_until_all_are_gone(manager):
    tennis, football, writer = (manager.acquire(n) for n in ("watcher:tennis", "watcher:football", "writer"))
    for lease in (tennis, football, writer):
        with pytest.raises(LeaseHeld):
            manager.acquire("maintenance")
        lease.release()
    with manager.acquire("maintenance"):
        pass


# --- sahip bilgisi -------------------------------------------------------------------------------

def test_lease_held_names_the_holder(manager):
    before = time.time()
    with manager.acquire("writer", purpose="job") as lease:
        with pytest.raises(LeaseHeld) as refused:
            manager.acquire("writer", purpose="cli")
    held = refused.value
    assert (held.name, held.pid, held.purpose) == ("writer", os.getpid(), "job")
    assert held.host == lease.host and held.host
    assert int(before) <= held.started_at <= time.time()
    assert held.path == manager.lock_file("writer")
    assert not held.fatal  # işi durduran disk hatası değil
    assert str(os.getpid()) in str(held) and "job" in str(held)


def test_holder_rows_follow_the_lease(manager):
    def rows():
        return [tuple(r) for r in manager.state.connection().execute("SELECT name, pid, purpose FROM leases")]

    lease = manager.acquire("watcher:tennis", purpose="watch")
    assert rows() == [("watcher:tennis", os.getpid(), "watch")]
    assert manager.holder("watcher:tennis") == lease.info()
    assert manager.holders() == [lease.info()]
    lease.release()
    lease.release()  # yinelenen bırakma zararsız
    assert rows() == [] and manager.holder("watcher:tennis") is None and not lease.held


def test_a_stale_row_is_not_reported_as_a_holder(manager):
    """Satır yalnızca bilgidir: sahibi ölmüş bir kilidin satırı dursa da kilit boştur, kararı işletim sistemi verir."""
    with manager.state.write() as conn:
        conn.execute("INSERT INTO leases VALUES ('writer', 'gone', 4242, 'elsewhere', 'job', 1, 1)")
    assert manager.holder("writer") is None and manager.holders() == []
    with manager.acquire("writer", purpose="mine") as lease:
        assert manager.holder("writer") == lease.info()  # satırın üzerine yazıldı


def test_leases_work_without_a_state_db(tmp_path):
    bare = LeaseManager(tmp_path / "locks")
    with bare.acquire("writer"):
        with pytest.raises(LeaseHeld) as refused:
            bare.acquire("maintenance")
        assert refused.value.name == "writer" and refused.value.pid is None and refused.value.started_at is None
        assert bare.holders() == [LeaseInfo("writer")]
    assert bare.holders() == []


def test_a_closed_state_db_does_not_break_the_lease(manager, caplog):
    manager.state.close()
    with manager.acquire("writer") as lease:
        assert lease.held
        with pytest.raises(LeaseHeld):
            manager.acquire("writer")
    assert "Kilit sahibi bilgisi yazılamadı" in caplog.text


def test_a_file_system_without_locks_degrades_to_single_process_mode(manager, monkeypatch, caplog):
    """Kilit desteklemeyen dosya sistemi (ENOLCK): kilit yine verilir, bir kez uyarılır; 2.x gibi çalışmaya devam eder."""
    def no_locks(fd, mode):
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(lease_mod, "_os_lock", no_locks)
    monkeypatch.setattr(lease_mod, "_unsupported_warned", set())
    with manager.acquire("writer") as first, manager.acquire("writer") as second:
        assert first.held and second.held  # dışlama yok: tek süreç kipi
    assert not first.held and not os.path.exists(manager.unclean_marker)
    assert caplog.text.count("Dosya sistemi kilit desteklemiyor") == 1


def test_an_unexpected_lock_error_is_a_store_error(manager, monkeypatch):
    def broken(fd, mode):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(lease_mod, "_os_lock", broken)
    with pytest.raises(StoreError) as failed:
        manager.acquire("writer")
    assert not isinstance(failed.value, LeaseHeld) and failed.value.errno == errno.EIO


def test_a_lock_directory_that_cannot_be_created_is_a_store_error(tmp_path):
    (tmp_path / "locks").write_text("x", encoding="utf-8")
    with pytest.raises(StoreError) as failed:
        LeaseManager(tmp_path / "locks").acquire("writer")
    assert not isinstance(failed.value, LeaseHeld)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork yalnızca POSIX'te var")
# Çocuk yalnızca dosya tanıtıcılarını kapatıp os._exit ile çıkar; başka iş parçacığının kilidine dokunmaz
@pytest.mark.filterwarnings("ignore:.*multi-threaded.*fork.*:DeprecationWarning")
def test_a_forked_copy_does_not_drop_the_parents_lease(manager):
    """flock açık dosya tanımına bağlıdır: çocuk süreçteki kopya kilidi açsaydı üst sürecin kilidi de düşerdi."""
    lease = manager.acquire("writer", purpose="job")
    pid = os.fork()
    if pid == 0:  # çocuk: kopyayı bırakır ve hiçbir temizlik yapmadan çıkar
        try:
            lease.release()
        finally:
            os._exit(0)
    assert os.waitpid(pid, 0)[1] == 0
    assert lease.held and os.path.exists(manager.unclean_marker)
    assert manager.holder("writer") == lease.info()  # satır da yerinde
    with pytest.raises(LeaseHeld):
        manager.acquire("writer")
    lease.release()
    assert manager.holder("writer") is None


# --- temiz kapanmama işareti ---------------------------------------------------------------------

def test_unclean_marker_is_set_by_the_writer_and_removed_on_a_clean_release(manager):
    marker = Path(manager.unclean_marker)
    assert marker == Path(manager.locks_dir) / os.path.basename(layout.UNCLEAN_MARKER)
    with manager.acquire("maintenance"), manager.acquire("sinks"):
        assert not marker.exists()  # yalnızca writer işaret koyar
    with manager.acquire("writer") as lease:
        assert marker.is_file() and lease.unclean is False
    assert not marker.exists()


def test_an_abandoned_writer_leaves_the_marker_for_the_next_one(manager):
    abandoned = manager.acquire("writer")
    del abandoned  # bırakılmadan çöpe giden kilit: dosyalar kapanır, işaret kalır
    gc.collect()
    with manager.acquire("writer") as lease:
        assert lease.unclean is True
    with manager.acquire("writer") as lease:
        assert lease.unclean is False


# --- bekleme -------------------------------------------------------------------------------------

def test_wait_gets_the_lease_when_the_holder_lets_go(manager):
    first = manager.acquire("writer")
    timer = threading.Timer(0.3, first.release)
    timer.start()
    try:
        started = time.monotonic()
        with manager.acquire("writer", wait=20) as second:
            assert second.held and time.monotonic() - started < 15
    finally:
        timer.join()


def test_wait_gives_up_after_the_deadline(manager):
    with manager.acquire("maintenance"):
        started = time.monotonic()
        with pytest.raises(LeaseHeld):
            manager.acquire("writer", wait=0.3)
        assert time.monotonic() - started >= 0.25  # saatin çözünürlüğü kaba olabilir (Windows)


def test_leases_from_many_threads_admit_one_writer_at_a_time(manager):
    inside, most, errors, guard = [0], [0], [], threading.Lock()

    def worker():
        try:
            for _ in range(8):
                with manager.acquire("writer", wait=60):
                    with guard:
                        inside[0] += 1
                        most[0] = max(most[0], inside[0])
                    with guard:
                        inside[0] -= 1
        except Exception as e:  # noqa: BLE001 - iş parçacığındaki hata teste taşınır
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert errors == [] and most[0] == 1


# --- iki süreç -----------------------------------------------------------------------------------

def test_second_writer_in_another_process_is_refused_with_holder_info(manager, data_dir):
    with other_process(HOLDER, data_dir, "writer", "headless") as proc:
        with pytest.raises(LeaseHeld) as refused:
            manager.acquire("writer", purpose="web job")
        held = refused.value
        assert (held.name, held.pid, held.purpose) == ("writer", proc.pid, "headless")
        assert held.host and held.started_at is not None
        assert manager.holder("writer").pid == proc.pid
        assert [(i.name, i.pid) for i in manager.holders()] == [("writer", proc.pid)]
        with manager.acquire("watcher:tennis"):  # yazar izleyiciyi engellemez
            pass
    with manager.acquire("writer") as lease:  # süreç kilidi bırakıp çıktı
        assert lease.unclean is False
    assert manager.holder("writer") is None


def test_second_watcher_for_the_same_sport_is_refused_across_processes(manager, data_dir):
    with other_process(HOLDER, data_dir, "watcher:tennis", "watch") as proc:
        with pytest.raises(LeaseHeld) as refused:
            manager.acquire("watcher:tennis")
        assert (refused.value.name, refused.value.pid) == ("watcher:tennis", proc.pid)
        with manager.acquire("watcher:football"), manager.acquire("writer"):
            pass


def test_lease_is_free_after_the_holder_is_killed(manager, data_dir):
    with other_process(HOLDER, data_dir, "writer", "job") as proc:
        with pytest.raises(LeaseHeld):
            manager.acquire("writer")
        proc.kill()  # POSIX'te SIGKILL, Windows'ta TerminateProcess: süreç hiçbir şeyi temizleyemez
        proc.wait(60)
        wait_until_free(manager, "writer")
        with manager.acquire("writer", wait=20) as lease:
            assert lease.unclean is True  # önceki yazar temiz kapanmadı
            assert manager.holder("writer").pid == os.getpid()
        with manager.acquire("maintenance"):
            pass


@pytest.mark.parametrize("child, parent", [
    ("maintenance", "writer"), ("maintenance", "watcher:tennis"), ("maintenance", "live"),
    ("writer", "maintenance"), ("watcher:tennis", "maintenance"), ("live", "maintenance"),
    ("live", "watcher:tennis"), ("watcher:tennis", "live"),
])
def test_exclusion_across_processes(manager, data_dir, child, parent):
    with other_process(HOLDER, data_dir, child, "other") as proc:
        with pytest.raises(LeaseHeld) as refused:
            manager.acquire(parent)
        assert (refused.value.name, refused.value.pid, refused.value.purpose) == (child, proc.pid, "other")
        with manager.acquire("sinks"):  # sinks hiçbirinden etkilenmez
            pass
    with manager.acquire(parent):
        pass


def test_lease_is_a_context_manager_and_reports_itself(manager):
    lease = manager.acquire("sinks", purpose="dispatch")
    assert isinstance(lease, Lease) and "sinks" in repr(lease)
    with lease as same:
        assert same is lease and lease.held
    assert not lease.held


# --- iş deposu: aynı arayüz, süreçler arası kilit ------------------------------------------------

def _holder(db_path: str, name: str) -> Optional[LeaseInfo]:
    return LeaseManager(jobs_mod.locks_dir_for(db_path)).holder(name)


def test_job_store_locks_live_next_to_its_database(data_dir):
    db = default_db_path(str(data_dir))
    assert jobs_mod.locks_dir_for(db) == layout.resolve(str(data_dir), layout.LOCKS_DIR)


def test_a_running_job_holds_the_writer_lease_until_it_finishes(tmp_path):
    db = str(tmp_path / "jobs.db")
    jobs = JobStore(db)
    assert _holder(db, "writer") is None  # depoyu açmak kilit almaz
    jobs.create_running({"mode": "full"})
    assert _holder(db, "writer") is not None
    second = jobs.create_running({"mode": "details"})  # aynı depo kendi kilidini yeniden kullanır (bugünkü davranış)
    assert jobs.snapshot()["job_id"] == second
    jobs.update(progress=50)
    assert _holder(db, "writer") is not None
    jobs.update(status="Completed", finished=True)
    assert _holder(db, "writer") is None
    jobs.close()


def test_sweep_and_close_release_the_writer_lease(tmp_path):
    db = str(tmp_path / "jobs.db")
    jobs = JobStore(db)
    jobs.create_running({})
    assert jobs.mark_stale_running_interrupted() == 1
    assert _holder(db, "writer") is None and jobs.snapshot()["is_running"] is False
    jobs.create_running({})
    jobs.close()
    assert _holder(db, "writer") is None


def test_a_second_job_store_on_the_same_directory_sees_the_running_job(tmp_path):
    db = str(tmp_path / "jobs.db")
    first, second = JobStore(db), JobStore(db)
    first.create_running({"mode": "full"})
    with pytest.raises(JobRunningError) as running:
        second.create_running({"mode": "full"})
    assert running.value.code == "job_running"
    with pytest.raises(JobRunningError):
        with second.exclusive("clear"):
            pytest.fail("başka bir depo yazarken veri işlemi başlamamalı")
    with pytest.raises(JobRunningError):
        with second.exclusive("backup"):
            pytest.fail("başka bir depo yazarken yedek başlamamalı")
    assert second.snapshot()["is_running"] is False and len(second.list_jobs()) == 1
    first.update(status="Completed", finished=True)
    job = second.create_running({"mode": "full"})
    second.update(status="Completed", finished=True)
    assert second.get_job(job)["status"] == "completed"
    first.close()
    second.close()


@pytest.mark.parametrize("operation, lease_name", [
    ("clear", "maintenance"), ("league_delete", "maintenance"), ("data_dir_change", "maintenance"),
    ("backup", "writer"),
])
def test_a_data_operation_holds_its_lease_and_blocks_other_job_stores(tmp_path, operation, lease_name):
    db = str(tmp_path / "jobs.db")
    first, second = JobStore(db), JobStore(db)
    with first.exclusive(operation):
        holder = first._leases.holder(lease_name)
        assert holder is not None and (holder.pid, holder.purpose) == (os.getpid(), f"op:{operation}")
        with pytest.raises(DataOperationRunningError) as busy:
            second.create_running({})
        assert busy.value.code == "data_operation_running" and busy.value.operation == operation
        with pytest.raises(DataOperationRunningError) as busy:
            with second.exclusive("clear"):
                pytest.fail("iki veri işlemi aynı anda çalışmamalı")
        assert busy.value.operation == operation
    assert _holder(db, lease_name) is None
    with second.exclusive("clear"):
        pass
    first.close()
    second.close()


def test_writer_busy_sees_this_store_and_other_holders(tmp_path):
    db = str(tmp_path / "jobs.db")
    first, second = JobStore(db), JobStore(db)
    assert first.writer_busy() is False and second.writer_busy() is False
    first.create_running({})
    assert first.writer_busy() is True and second.writer_busy() is True
    first.update(status="Completed", finished=True)
    assert second.writer_busy() is False
    with first.exclusive("backup"):  # yedek de yazar kilidini tutar
        assert second.writer_busy() is True
    with first.exclusive("clear"):  # `maintenance` yazar değildir; onu create_running ve exclusive reddeder
        assert second.writer_busy() is False
    first.close()
    second.close()


def test_an_operation_that_fails_releases_its_lease(tmp_path):
    db = str(tmp_path / "jobs.db")
    jobs = JobStore(db)
    with pytest.raises(ValueError):
        with jobs.exclusive("clear"):
            raise ValueError("boom")
    assert _holder(db, "maintenance") is None
    jobs.close()


def test_conflict_mapping_from_the_held_lease():
    def conflict(name, purpose):
        return jobs_mod.conflict_from_lease(LeaseHeld(name=name, purpose=purpose))

    assert isinstance(conflict("writer", "job"), JobRunningError)
    assert isinstance(conflict("writer", ""), JobRunningError)  # sahip bilgisi yok: yazan biri var
    assert isinstance(conflict("writer", "headless"), JobRunningError)
    assert conflict("writer", "op:backup").operation == "backup"
    assert conflict("maintenance", "op:clear").operation == "clear"
    assert conflict("maintenance", "").operation == "maintenance"
    assert conflict("watcher:tennis", "").operation == "watcher:tennis"
    assert conflict("live", "serve").operation == "serve"


def test_rebind_moves_the_leases_to_the_new_directory(tmp_path):
    a, b = str(tmp_path / "a" / "jobs.db"), str(tmp_path / "b" / "jobs.db")
    jobs = JobStore(a)
    with jobs.exclusive("data_dir_change"):
        assert _holder(a, "maintenance") is not None
        assert jobs.rebind(b) is True
    assert _holder(a, "maintenance") is None  # eski dizinin kilidi, depo taşındıktan sonra da bırakılır
    jobs.create_running({})
    assert _holder(b, "writer") is not None and _holder(a, "writer") is None
    jobs.update(status="Completed", finished=True)
    jobs.close()


def test_an_abandoned_job_store_frees_its_lease(tmp_path):
    db = str(tmp_path / "jobs.db")
    JobStore(db).create_running({})
    gc.collect()
    assert _holder(db, "writer") is None


def test_job_in_another_process_blocks_jobs_and_operations_here(data_dir):
    jobs = JobStore(default_db_path(str(data_dir)))
    with other_process(JOB_HOLDER, data_dir, "job"):
        assert jobs.writer_busy() is True
        with pytest.raises(JobRunningError):
            jobs.create_running({"mode": "full"})
        with pytest.raises(JobRunningError):
            with jobs.exclusive("clear"):
                pytest.fail("başka bir süreç yazarken silme başlamamalı")
        assert jobs.snapshot()["is_running"] is False
    jobs.create_running({"mode": "full"})
    jobs.update(status="Completed", finished=True)
    jobs.close()


@pytest.mark.parametrize("operation", ["clear", "backup"])
def test_data_operation_in_another_process_blocks_jobs_here(data_dir, operation):
    jobs = JobStore(default_db_path(str(data_dir)))
    with other_process(JOB_HOLDER, data_dir, operation):
        with pytest.raises(DataOperationRunningError) as busy:
            jobs.create_running({"mode": "full"})
        assert busy.value.operation == operation
        with pytest.raises(DataOperationRunningError) as busy:
            with jobs.exclusive("league_delete"):
                pytest.fail("başka bir süreçte veri işlemi sürerken ikincisi başlamamalı")
        assert busy.value.operation == operation
    with jobs.exclusive("league_delete"):
        pass
    jobs.close()


def test_a_killed_job_process_leaves_an_interrupted_row_and_a_free_lease(data_dir):
    db = default_db_path(str(data_dir))
    with other_process(JOB_HOLDER, data_dir, "job") as proc:
        proc.kill()
        proc.wait(60)
    wait_until_free(LeaseManager(jobs_mod.locks_dir_for(db)), "writer")
    jobs = JobStore(db)  # açılış: çöken sürecin "running" satırı interrupted olur
    assert [j["status"] for j in jobs.list_jobs()] == ["interrupted"]
    jobs.create_running({"mode": "full"})
    jobs.update(status="Completed", finished=True)
    jobs.close()


# --- state.db geçişleri `maintenance` kilidi altında ---------------------------------------------

def test_state_migrations_run_inside_the_guard_and_only_when_needed(tmp_path):
    calls = []

    @contextlib.contextmanager
    def guard():
        calls.append("enter")
        yield
        calls.append("exit")

    path = tmp_path / "state.db"
    StateDb(path, migration_guard=guard).close()
    assert calls == ["enter", "exit"]
    StateDb(path, migration_guard=guard).close()  # güncel dosya: kilit istenmez
    assert calls == ["enter", "exit"]


def test_a_new_state_db_is_not_created_while_someone_writes_the_directory(tmp_path, monkeypatch):
    """Geçiş (ilk kuruluş dahil) `maintenance` ister; yazan biri varken beklenir, sonra LeaseHeld."""
    monkeypatch.setattr(lease_mod, "MIGRATION_WAIT", 0.2)
    db = default_db_path(str(tmp_path / "data"))
    writer = LeaseManager(jobs_mod.locks_dir_for(db)).acquire("writer", purpose="job")
    with pytest.raises(LeaseHeld) as refused:
        JobStore(db)
    assert refused.value.name == "writer"
    writer.release()
    jobs = JobStore(db)
    assert jobs.list_jobs() == [] and _holder(db, "maintenance") is None
    jobs.close()


def test_concurrent_first_opens_wait_for_the_migrating_process(tmp_path):
    """Aynı anda açılan depolar: biri geçişi yapar, diğerleri kilidi bekleyip hazır dosyayı bulur."""
    db = default_db_path(str(tmp_path / "data"))
    opened, errors = [], []

    def worker():
        try:
            opened.append(JobStore(db))
        except Exception as e:  # noqa: BLE001 - iş parçacığındaki hata teste taşınır
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == [] and len(opened) == 6
    for jobs in opened:
        jobs.close()


# --- web API: başka bir süreç yazarken 409 job_running -------------------------------------------

def test_web_api_answers_409_job_running_while_another_process_holds_the_writer_lease(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.web import fetch_job
    from src.web.app import app
    from src.web.routes import api as api_mod

    jobs = api_mod._job_store
    monkeypatch.setattr(fetch_job, "run_fetch_job", lambda job_id, payload: None)
    # Silme reddedilmezse ortak test verisine değil bu boş dizine dokunsun
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "scratch"))
    if jobs.snapshot().get("is_running"):
        jobs.update(status="Cancelled", finished=True)
    client = TestClient(app)
    web_data_dir = os.path.dirname(os.path.dirname(jobs.db_path))

    def assert_job_running(response):
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "job_running"

    with other_process(HOLDER, web_data_dir, "writer", "headless"):
        assert_job_running(client.post("/api/fetch", json={"mode": "full", "league_id": 17}))
        assert jobs.snapshot()["is_running"] is False
        assert_job_running(client.post("/api/data/clear", json={"scope": "matches"}))
        assert_job_running(client.post("/api/data/backup"))
    try:
        assert client.post("/api/fetch", json={"mode": "full", "league_id": 17}).status_code == 200
    finally:
        if jobs.snapshot().get("is_running"):
            jobs.update(status="Cancelled", progress=0, current_task="cleanup", finished=True)
