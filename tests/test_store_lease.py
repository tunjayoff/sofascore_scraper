"""
src/store/lease.py: süreçler arası kilitler (docs/design/01-storage.md bölüm 6.1; plan maddesi ST-10).

Kilit tablosu (kim kimi dışlar), sahip bilgisi, temiz kapanmama işareti, bekleme ve iki süreçli durumlar:
ikinci yazar sahibin bilgisiyle reddedilir, süreç öldürülünce kilit boşalır, `maintenance` yazarı ve
izleyiciyi dışlar (ve tersi). Tümü çevrimdışı; Linux, macOS ve Windows'ta çalışır.
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
from typing import Iterator

import pytest

from src.store import LayoutError, LeaseHeld, StoreError, layout
from src.store import lease as lease_mod
from src.store.jobs import default_db_path
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
