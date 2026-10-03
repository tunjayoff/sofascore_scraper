"""
src/store/lease.py ve iş deposunun kilitlerle çalışması (docs/design/01-storage.md bölüm 6.1; plan maddesi ST-10).

Kilit tablosu (kim kimi dışlar), sahip bilgisi, temiz kapanmama işareti, bekleme ve iki süreçli durumlar:
ikinci yazar sahibin bilgisiyle reddedilir, süreç öldürülünce kilit boşalır, `maintenance` yazarı ve
izleyiciyi dışlar (ve tersi). İş deposunun hata sınıfları ve arayüzü değişmez; yalnızca artık başka bir
sürecin işini de görür. Tümü çevrimdışı; Linux, macOS ve Windows'ta çalışır.
"""
from __future__ import annotations

import ast
import contextlib
import errno
import gc
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

from src.web import deps
from src.store import LayoutError, LeaseHeld, StoreError, files, layout, open_store
from src.store import jobs as jobs_mod
from src.store import lease as lease_mod
from src.store import sqlite as sqlite_mod
from src.store import state as state_mod
from src.store.catalog import catalog_path
from src.store.jobs import DataOperationRunningError, JobRunningError, JobStore, default_db_path
from src.store.lease import EXCLUSIVE, SHARED, Lease, LeaseInfo, LeaseManager, lock_plan
from src.store.state import MIGRATIONS_DIR, StateDb, load_migrations

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
    assert "Could not record the holder of the lease (writer): " in caplog.text


def test_a_holder_row_that_cannot_be_removed_is_only_a_debug_line(manager, caplog):
    lease = manager.acquire("sinks")
    manager.state.close()
    with caplog.at_level(logging.DEBUG, logger="Store"):
        lease.release()

    assert not lease.held and manager.holder("sinks") is None
    logged = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "Store"]
    assert len(logged) == 1 and logged[0][0] == logging.DEBUG
    assert logged[0][1].startswith("Could not remove the holder record of the lease (sinks): ")


def test_a_file_system_without_locks_degrades_to_single_process_mode(manager, monkeypatch, caplog):
    """Kilit desteklemeyen dosya sistemi (ENOLCK): kilit yine verilir, bir kez uyarılır; 2.x gibi çalışmaya devam eder."""
    def no_locks(fd, mode):
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(lease_mod, "_os_lock", no_locks)
    monkeypatch.setattr(lease_mod, "_unsupported_warned", set())
    with manager.acquire("writer") as first, manager.acquire("writer") as second:
        assert first.held and second.held  # dışlama yok: tek süreç kipi
    assert not first.held and not os.path.exists(manager.unclean_marker)
    logged = [r.getMessage() for r in caplog.records if r.name == "Store" and r.levelno == logging.WARNING]
    assert logged == [
        "The file system does not support file locks (No locks available); leases cannot keep other processes "
        f"out. Only one process at a time may use this data directory: {manager.locks_dir}"
    ]


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


# --- kilit dosyalarının izni (karar S15; plan maddesi FX-8) ---------------------------------------

WINDOWS = os.name == "nt"
posix_modes = pytest.mark.skipif(WINDOWS, reason="POSIX dosya izinleri Windows'ta yok")
UMASKS = [0o022, 0o077, 0o002, 0o027]


def _mode(path) -> int:
    """rwx bitleri; setgid'li bir üst dizinden miras kalabilen özel bitler sayılmaz."""
    return stat.S_IMODE(os.stat(path).st_mode) & 0o777


def _lock_files(manager: LeaseManager) -> List[str]:
    return sorted(
        os.path.join(manager.locks_dir, name) for name in os.listdir(manager.locks_dir) if name.endswith(".lock")
    )


@pytest.fixture(params=UMASKS, ids=lambda m: f"umask-{m:03o}")
def umask(request) -> Iterator[int]:
    """Testi verilen umask ile çalıştırır, sonra eskisini geri koyar (umask süreç geneli bir ayardır)."""
    previous = os.umask(request.param)
    try:
        yield request.param
    finally:
        os.umask(previous)


@posix_modes
def test_new_lock_files_get_the_mode_of_the_umask(tmp_path, umask):
    """Store'un öteki dosyaları gibi: 022 → 0644, 002 → 0664 (aynı gruptan ikinci hesap kilidi alabilir)."""
    manager = LeaseManager(tmp_path / "data" / ".meta" / "locks")

    with manager.acquire("watcher:tennis"), manager.acquire("writer"), manager.acquire("sinks"):
        pass

    created = _lock_files(manager)
    assert [os.path.basename(path) for path in created] == [
        "live.lock", "maintenance.lock", "sinks.lock", "watcher-tennis.lock", "writer.lock"]
    assert {_mode(path) for path in created} == {0o666 & ~umask}
    assert _mode(manager.locks_dir) == 0o777 & ~umask


@posix_modes
def test_lock_files_of_an_opened_data_directory_follow_the_umask(tmp_path, umask):
    """İlk açılış: state.db geçişi `maintenance` kilidini alır; o dosya ve iş deposunun yazar kilidi de umask'e uyar."""
    jobs = JobStore(default_db_path(str(tmp_path / "data")))
    try:
        jobs.create_running({"mode": "full"})
        manager = LeaseManager(jobs_mod.locks_dir_for(jobs.db_path))
        assert [os.path.basename(path) for path in _lock_files(manager)] == ["maintenance.lock", "writer.lock"]
        assert {_mode(path) for path in _lock_files(manager)} == {0o666 & ~umask}
    finally:
        jobs.close()


@posix_modes
@pytest.mark.parametrize("old_mode", [0o644, 0o600, 0o666])
def test_an_existing_lock_file_keeps_its_mode(tmp_path, umask, old_mode):
    """İzin yalnızca dosya oluşturulurken verilir: önceki sürümün 0644 ile oluşturduğu dosyaya chmod yapılmaz."""
    manager = LeaseManager(tmp_path / "locks")
    os.makedirs(manager.locks_dir)
    for name in ("writer", "maintenance"):
        with open(manager.lock_file(name), "wb"):
            pass
        os.chmod(manager.lock_file(name), old_mode)

    with manager.acquire("writer"):
        assert manager.holder("writer") is not None  # yoklama da dosyayı açar

    assert {_mode(path) for path in _lock_files(manager)} == {old_mode}


def test_every_open_of_a_lock_file_asks_for_the_store_file_mode(tmp_path, monkeypatch):
    """Kilidi alan açılış da, sahibini yoklayan açılış da (dosyayı o an oluşturabilir) aynı izni ister."""
    manager = LeaseManager(tmp_path / "locks")
    opened = []
    real_open = os.open

    def recording_open(path, flags, mode=0o777, **kwargs):
        if os.fspath(path).endswith(".lock"):
            opened.append((os.path.basename(path), mode))
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(lease_mod.os, "open", recording_open)
    with manager.acquire("writer"):
        assert manager.holder("writer") is not None
    assert manager.holder("writer") is None

    assert files.STORE_FILE_MODE == 0o666
    assert opened == [
        ("maintenance.lock", 0o666), ("writer.lock", 0o666),  # acquire
        ("writer.lock", 0o666), ("writer.lock", 0o666),  # iki yoklama
    ]


@posix_modes
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root her dosyayı açabilir")
@pytest.mark.parametrize("name, unwritable", [("writer", "writer"), ("writer", "maintenance"), ("sinks", "sinks")])
def test_a_lock_file_the_caller_cannot_open_for_writing_is_a_store_error_that_names_it(tmp_path, name, unwritable):
    """
    Başka bir hesabın 0644 ile oluşturduğu kilit dosyası (FX-8 öncesi, ya da umask 022): grup üyesi onu
    yazmak için açamaz. Kilit verilmez; hata LeaseHeld değildir ve dosyanın yolunu taşır.
    """
    manager = LeaseManager(tmp_path / "locks")
    with manager.acquire(name):
        pass
    path = manager.lock_file(unwritable)
    os.chmod(path, 0o444)  # sahibi için de salt okunur: başka hesabın 0644 dosyasının grup tarafındaki hali

    with pytest.raises(StoreError) as failed:
        manager.acquire(name)

    assert not isinstance(failed.value, LeaseHeld)
    assert failed.value.path == path and failed.value.errno == errno.EACCES
    assert path in str(failed.value)
    assert manager.holders() == []  # plandaki öteki dosyaların kilidi bırakıldı
    os.chmod(path, 0o644)
    with manager.acquire("maintenance"), manager.acquire("sinks"):
        pass


# --- veritabanı dosyalarının izni ve paylaşılan veri dizini (karar S15; plan maddesi FX-11) ----------

not_root = pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root her dosyayı açabilir")
SHARED_UMASK = 0o002  # iki hesabın paylaştığı dizinde beklenen umask: grup da yazar


@contextlib.contextmanager
def umask_of(value: int) -> Iterator[None]:
    """Bloğu verilen umask ile çalıştırır (çocuk süreçler de onu devralır), sonra eskisini geri koyar."""
    previous = os.umask(value)
    try:
        yield
    finally:
        os.umask(previous)


def _tree(root) -> List[str]:
    """Kökün kendisi ve altındaki her dizin ve dosya."""
    found = [os.fspath(root)]
    for directory, dirnames, filenames in os.walk(root):
        found.extend(os.path.join(directory, name) for name in dirnames + filenames)
    return sorted(found)


def _database_files(data_dir) -> List[str]:
    return [layout.resolve(data_dir, layout.STATE_DB), catalog_path(data_dir)]


def _state_db_at_schema_version_1(tmp_path) -> Path:
    """Yalnızca 0001_initial uygulanmış bir state.db: sonraki açılış onu kopyalar ve yükseltir."""
    first_only = tmp_path / "migrations-first-only"
    first_only.mkdir()
    shutil.copy(os.path.join(MIGRATIONS_DIR, "0001_initial.sql"), first_only)
    path = tmp_path / "state.db"
    StateDb(path, migrations_dir=first_only).close()
    return path


@posix_modes
def test_every_file_of_a_fresh_data_directory_follows_the_umask(tmp_path, umask):
    """
    state.db, catalog.db ve onların `-wal`/`-shm` dosyaları da: 002 altında her dosya 0664, her dizin 0775.
    FX-8'e kadar kilit dosyaları, bu maddeye kadar iki veritabanı dosyası umask ne olursa olsun 0644'tü.
    """
    data = tmp_path / "data"
    store = open_store(data)
    try:
        store.jobs.create_running({"mode": "full"})  # state.db'ye yazı, yazar kilidi ve işareti
        paths = _tree(data)  # depo açıkken: `-wal` ve `-shm` dosyaları da duruyor
        file_modes = {os.path.relpath(path, data): _mode(path) for path in paths if os.path.isfile(path)}
        dir_modes = {os.path.relpath(path, data): _mode(path) for path in paths if os.path.isdir(path)}
        store.jobs.update(status="Completed", finished=True)
    finally:
        store.close()

    databases = [os.path.join(".meta", name + suffix) for name in ("state.db", "catalog.db") for suffix in ("", "-wal", "-shm")]
    assert set(databases) <= set(file_modes)
    assert os.path.join(".meta", "locks", "writer.lock") in file_modes and os.path.join(".meta", "schema.json") in file_modes
    assert {name: mode for name, mode in file_modes.items() if mode != 0o666 & ~umask} == {}
    assert {name: mode for name, mode in dir_modes.items() if mode != 0o777 & ~umask} == {}


@posix_modes
@pytest.mark.parametrize("old_mode", [0o644, 0o600])
def test_existing_database_files_keep_their_mode_when_the_store_opens(tmp_path, umask, old_mode):
    """Var olan dosyaya chmod yapılmaz; `-wal` ve `-shm` dosyaları da umask'i değil o dosyanın iznini alır."""
    data = tmp_path / "data"
    open_store(data).close()
    for path in _database_files(data):
        os.chmod(path, old_mode)

    store = open_store(data)
    try:
        store.jobs.create_running({"mode": "full"})
        assert store.catalog.rebuild(mode="in_place").completed  # catalog.db'ye yazı
        with_sidecars = [path + suffix for path in _database_files(data) for suffix in ("", "-wal", "-shm")]
        assert {os.path.basename(path): _mode(path) for path in with_sidecars} == {
            os.path.basename(path): old_mode for path in with_sidecars}
        store.jobs.update(status="Completed", finished=True)
    finally:
        store.close()

    assert [_mode(path) for path in _database_files(data)] == [old_mode, old_mode]


@posix_modes
def test_the_copy_before_a_migration_follows_the_umask_not_the_mode_of_state_db(tmp_path, umask):
    path = _state_db_at_schema_version_1(tmp_path)
    os.chmod(path, 0o600)

    StateDb(path).close()  # 0002 ve sonrası: önce state.db.bak-v1

    assert _mode(f"{path}.bak-v1") == 0o666 & ~umask
    assert _mode(path) == 0o600  # yükseltilen dosyanın kendisi iznini korur


@posix_modes
def test_a_recreated_catalog_is_a_new_file_and_follows_the_umask(tmp_path, umask):
    """
    catalog.db türetilmiştir: yeniden yaratılınca (`catalog.db.build`, sonra yerine konur) eski dosyanın
    iznini taşımaz. Eski bir dizinde catalog.db'yi silmek de bu yüzden bir onarımdır; state.db silinemez.
    """
    data = tmp_path / "data"
    store = open_store(data)
    try:
        os.chmod(catalog_path(data), 0o600)
        assert store.catalog.rebuild(mode="recreate").completed
        assert _mode(catalog_path(data)) == 0o666 & ~umask
    finally:
        store.close()

    os.remove(catalog_path(data))
    open_store(data).close()  # eksik katalog açılışta yeniden kurulur
    assert _mode(catalog_path(data)) == 0o666 & ~umask


# Aynı gruptan ikinci hesap, tek hesapla: dosyaya grubun gözüyle bakılır. Bir dosyanın grup bitleri sahip
# bitlerinin yerine konur; böylece dosyanın sahibi olan test süreci tam o grup üyesinin yapabildiğini yapabilir
# (0644 → sahibi için de salt okunur, 0664 → okunur ve yazılır). FX-8 kilit dosyalarını böyle denemişti.
SECOND_ACCOUNT = """
import json, logging, os, sys
logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(name)s: %(message)s")
from src.store import open_store
seen = {}
store = open_store(sys.argv[1])
try:
    with store.lease("writer", purpose="job"):
        holder = store.lease_holder("writer")
        seen["holder_row"] = holder is not None and holder.pid == os.getpid()
    try:
        store.jobs.create_running({"mode": "full"})
        store.jobs.update(status="Completed", finished=True)
        seen["job_row"] = "written"
    except Exception as e:
        seen["job_row"] = f"{type(e).__name__}: {e}"
    try:
        seen["catalog"] = "written" if store.catalog.rebuild(mode="in_place").completed else "stopped"
    except Exception as e:
        seen["catalog"] = f"{type(e).__name__}: {e}"
finally:
    store.close()
print(json.dumps(seen))
"""
EVERYTHING_WRITTEN = {"holder_row": True, "job_row": "written", "catalog": "written"}


def _as_the_group_sees_it(root) -> None:
    for path in _tree(root):
        mode = _mode(path)
        os.chmod(path, ((mode & 0o070) << 3) | (mode & 0o077))


def _second_account(data_dir) -> Tuple[Dict[str, Any], str]:
    """Veri dizinini ayrı bir süreçte açar, kilit alır, bir iş satırı ve kataloğu yazar: (ne oldu, uyarılar)."""
    done = subprocess.run([sys.executable, "-c", SECOND_ACCOUNT, str(data_dir)], cwd=ROOT, capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout), done.stderr


@posix_modes
@not_root
@pytest.mark.parametrize("first_still_open", [False, True], ids=["first-closed", "first-open"])
def test_a_second_account_of_the_group_can_write_a_fresh_shared_directory(tmp_path, first_still_open):
    """
    Karar S15'in amacı: umask 002 ile kurulan dizini aynı gruptan ikinci hesap da yazar. İlk hesap dizini
    kurar ve bir iş çalıştırır; ikinci hesap kilidi alır, sahip satırını, bir iş satırını ve kataloğu yazar.
    İlk hesabın deposu açıkken de: `-wal` ve `-shm` dosyaları o sırada durur ve ikisini de açabilmelidir.
    """
    data = tmp_path / "data"
    with umask_of(SHARED_UMASK):
        first = open_store(data)
        try:
            first.jobs.create_running({"mode": "full"})
            first.jobs.update(status="Completed", finished=True)
            if not first_still_open:
                first.close()
            _as_the_group_sees_it(data)
            seen, warnings = _second_account(data)
        finally:
            first.close()

    assert seen == EVERYTHING_WRITTEN
    assert "Store:" not in warnings, warnings


@posix_modes
@not_root
def test_a_directory_created_before_needs_group_write_on_its_database_files(tmp_path):
    """
    Var olan dosya iznini korur: bu değişiklikten önce kurulmuş bir dizinde state.db ve catalog.db 0644'tür.
    İkinci hesap orada kilidi alır ama hiçbir şey yazamaz (FX-8'in bulgusu). Onarım, kimse dizini
    kullanmazken `chmod g+w .meta/state.db* .meta/catalog.db*` vermektir; ondan sonra her yazı yerine ulaşır.

    Yıldız gereklidir: yazamayan bağlantı kapanırken `-wal` ve `-shm` dosyalarını silemez, onlar da veritabanı
    dosyasının o günkü izniyle (grup yazamaz) kalır ve yalnızca iki dosyanın izni düzeltilirse yazmayı yine
    engeller.
    """
    data = tmp_path / "data"
    with umask_of(SHARED_UMASK):
        first = open_store(data)
        first.jobs.create_running({"mode": "full"})
        first.jobs.update(status="Completed", finished=True)
        first.close()
        for path in _database_files(data):
            os.chmod(path, 0o644)  # SQLite'ın kendi yarattığı dosya: 0644 eksi umask
        real_modes = {path: _mode(path) for path in _tree(data)}

        _as_the_group_sees_it(data)
        seen, warnings = _second_account(data)

        assert seen["holder_row"] is False
        assert "readonly database" in seen["job_row"] and "readonly database" in seen["catalog"]
        assert "Store: Could not record the holder of the lease (writer): attempt to write a readonly database" in warnings

        for path, mode in real_modes.items():
            os.chmod(path, mode)
        meta_dir = layout.resolve(data, layout.META_DIR)
        for name in os.listdir(meta_dir):
            if name.startswith(("state.db", "catalog.db")):  # chmod g+w state.db* catalog.db*
                os.chmod(os.path.join(meta_dir, name), _mode(os.path.join(meta_dir, name)) | stat.S_IWGRP)
        _as_the_group_sees_it(data)
        seen, warnings = _second_account(data)

    assert seen == EVERYTHING_WRITTEN
    assert "Store:" not in warnings, warnings


# --- temiz kapanmama işareti ---------------------------------------------------------------------

def test_unclean_marker_is_set_by_the_writer_and_removed_on_a_clean_release(manager):
    marker = Path(manager.unclean_marker)
    assert marker == Path(manager.locks_dir) / os.path.basename(layout.UNCLEAN_MARKER)
    with manager.acquire("maintenance"), manager.acquire("sinks"):
        assert not marker.exists()  # yalnızca writer işaret koyar
    with manager.acquire("writer") as lease:
        assert marker.is_file() and lease.unclean is False
    assert not marker.exists()


def test_a_marker_that_cannot_be_written_is_a_warning_and_the_lease_is_still_granted(manager, monkeypatch, caplog):
    def read_only(path, *args, **kwargs):
        raise OSError(errno.EROFS, "Read-only file system", path)

    monkeypatch.setattr(lease_mod, "open", read_only, raising=False)  # yalnızca bu modülün `open` çağrısı
    with manager.acquire("writer") as lease:
        assert lease.held and lease.unclean is False
        assert not os.path.exists(manager.unclean_marker)

    logged = [r.getMessage() for r in caplog.records if r.name == "Store" and r.levelno == logging.WARNING]
    assert len(logged) == 1
    assert logged[0].startswith(f"Could not write the unclean-shutdown marker: {manager.unclean_marker}: ")
    assert "Read-only file system" in logged[0]


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


def test_a_migration_is_logged_in_english_once_per_script(tmp_path, caplog):
    """Log iletileri İngilizcedir (plan kural 8); satır CLI golden'larında da geçer."""
    def logged() -> List[str]:
        return [r.getMessage() for r in caplog.records if r.name == "Store" and r.levelno == logging.INFO]

    path = tmp_path / "state.db"
    with caplog.at_level(logging.INFO, logger="Store"):
        StateDb(path).close()
        first_open = logged()
        StateDb(path).close()  # güncel dosya: yeni satır yok

    names = [migration.name for migration in load_migrations()]
    assert names[0] == "0001_initial"
    assert first_open == [f"state.db migration applied: {name}" for name in names]
    assert logged() == first_open


def test_the_copy_before_a_migration_is_logged_in_english(tmp_path, caplog):
    """Şema sürümü 1'de kalmış her veri dizini bu satırı bir kez yazar: 0002 geçişinden önceki kopya."""
    path = _state_db_at_schema_version_1(tmp_path)
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="Store"):
        StateDb(path).close()
        StateDb(path).close()  # güncel dosya: yeni kopya ve yeni satır yok

    later = [migration.name for migration in load_migrations()][1:]
    assert later, "paketin geçiş dizininde 0001'den sonrası olmalı"
    assert [r.getMessage() for r in caplog.records if r.name == "Store" and r.levelno == logging.INFO] == [
        f"state.db copied before migration (schema version 1): {path}.bak-v1",
        *[f"state.db migration applied: {name}" for name in later],
    ]
    assert os.path.isfile(f"{path}.bak-v1")


def _log_message_literals(module_file: str) -> List[str]:
    """Modüldeki `logger.<düzey>(...)` ve `log.<düzey>(...)` çağrılarının ileti metinleri (sabit dize parçaları)."""
    with open(module_file, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    found: List[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args):
            continue
        target = node.func.value
        if not (isinstance(target, ast.Name) and target.id in ("logger", "log")):
            continue
        if node.func.attr not in ("debug", "info", "warning", "error", "exception", "critical"):
            continue
        parts = [n.value for n in ast.walk(node.args[0]) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        found.append("".join(parts))
    return found


def _store_modules() -> List[str]:
    """src/store paketinin bütün modül dosyaları (alt paketler dahil), pakete göre göreli ve sıralı."""
    root = Path(lease_mod.__file__).parent
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*.py"))


@pytest.mark.parametrize("module", _store_modules())
def test_log_messages_of_the_store_modules_are_english(module):
    """
    Plan kural 8: log iletileri İngilizcedir. src/store'un hiçbir modülünde Türkçe satır kalmadı; ileti
    metninde ASCII dışı harf (ı, ş, ğ, ç, ö, ü) olması Türkçe bir satırın geri geldiğini gösterir. StoreError
    ve LeaseHeld metinleri log satırı değildir, burada denetlenmez.
    """
    messages = _log_message_literals(str(Path(lease_mod.__file__).parent / module))

    assert [message for message in messages if not message.isascii()] == []


@pytest.mark.parametrize("module", [lease_mod, state_mod, sqlite_mod, jobs_mod], ids=lambda module: module.__name__)
def test_the_log_scan_finds_the_calls(module):
    """Boş liste, taramanın log çağrılarını bulamadığını gösterirdi: bu modüllerin hepsinin log çağrısı var."""
    assert _log_message_literals(module.__file__)


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

    from src.web.api import legacy as fetch_job
    from src.web.app import app

    jobs = deps.job_store()
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
