"""
src/store/sqlite.py: catalog.db ve state.db'nin ortak bağlantı katmanı (docs/design/01-storage.md bölüm 3.2;
plan maddesi FX-3).

ST-06 (catalog.py) ve ST-09 (state.py) paralel yazıldı ve her biri bağlantı kurallarının kendi kopyasını
taşıyordu. Buradaki testler ortak kodu doğrudan dener ve iki modülün gerçekten aynı kodu kullandığını
sabitler. İki modülün kendi davranış testleri yerinde durur (tests/test_store_catalog.py,
tests/test_store_state.py); onların "aynı anda ilk açılış" testleri de artık bu kodu çalıştırır.

Ağ yok. İki süreçli test çocukları `subprocess` ile başlatır.
"""
from __future__ import annotations

import contextlib
import errno
import gc
import logging
import os
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import warnings
import weakref
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import pytest

from src.exceptions import StorageError
from src.store import CatalogCorrupt, StoreBusy, StoreError, catalog, files
from src.store import sqlite as sq
from src.store import state as state_mod
from src.store.catalog import Catalog
from src.store.indexer import BUILD_SUFFIX, CatalogAdmin
from src.store.state import StateDb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pragma(conn: sqlite3.Connection, name: str):
    return conn.execute(f"PRAGMA {name}").fetchone()[0]


def _sqlite_error(cls, message: str, code: Optional[int] = None) -> sqlite3.Error:
    exc = cls(message)
    if code is not None:
        exc.sqlite_errorcode = code
    return exc


# --- tek kopya ----------------------------------------------------------------------------------------

def test_catalog_and_state_share_one_implementation():
    """İki modülün eski adları ortak modüldeki nesnelerin kendisidir; ayrı kopya kalmadı."""
    for name in ("check_sqlite_version", "is_busy_error"):
        assert getattr(catalog, name) is getattr(state_mod, name) is getattr(sq, name), name
    for name in ("configure", "to_store_error"):
        assert getattr(catalog, name) is getattr(sq, name), name
    assert state_mod.split_statements is sq.split_statements
    assert catalog._set_journal_mode is sq.set_journal_mode
    for name in ("MIN_SQLITE", "BUSY_TIMEOUT_MS", "JOURNAL_SIZE_LIMIT"):
        assert getattr(catalog, name) == getattr(state_mod, name) == getattr(sq, name), name
    assert (sq.MIN_SQLITE, sq.BUSY_TIMEOUT_MS, sq.JOURNAL_SIZE_LIMIT) == ((3, 24, 0), 5000, 64 * 1024 * 1024)
    assert state_mod._WAL_RETRY_PAUSE == sq.WAL_RETRY_PAUSE


def test_both_stores_hand_out_the_self_closing_connection(tmp_path):
    state = StateDb(tmp_path / "state.db")
    with Catalog(catalog.catalog_path(tmp_path)) as cat:
        try:
            assert type(state.connection()) is sq.Connection and type(cat.connection()) is sq.Connection
            assert isinstance(state._connections, sq.ThreadConnections)
            assert isinstance(cat._connections, sq.ThreadConnections)
        finally:
            state.close()


# --- sürüm denetimi -----------------------------------------------------------------------------------

def test_minimum_sqlite_version():
    sq.check_sqlite_version()
    sq.check_sqlite_version((3, 24, 0))
    sq.check_sqlite_version([3, 53, 4])  # dizi de olur
    for old, text in (((3, 23, 1), "3.23.1"), ((3, 8, 7), "3.8.7"), ((2, 99, 0), "2.99.0")):
        with pytest.raises(StoreError, match="3.24") as refused:
            sq.check_sqlite_version(old)
        assert text in str(refused.value) and refused.value.detail == text
        assert refused.value.fatal is False


# --- hata çevirisi ------------------------------------------------------------------------------------

@pytest.mark.parametrize("exc, busy", [
    (_sqlite_error(sqlite3.OperationalError, "database is locked", 5), True),  # SQLITE_BUSY
    (_sqlite_error(sqlite3.OperationalError, "database is locked", 261), True),  # BUSY_RECOVERY
    (_sqlite_error(sqlite3.OperationalError, "database is locked", 517), True),  # BUSY_SNAPSHOT
    (_sqlite_error(sqlite3.OperationalError, "database is locked"), True),  # Python 3.10: kod yok
    (_sqlite_error(sqlite3.OperationalError, "Database Is Locked"), True),
    (_sqlite_error(sqlite3.OperationalError, "database table is locked", 6), False),  # SQLITE_LOCKED
    (_sqlite_error(sqlite3.OperationalError, "database table is locked: events"), False),
    (_sqlite_error(sqlite3.OperationalError, "database schema is locked: main", 6), False),
    (_sqlite_error(sqlite3.OperationalError, "disk I/O error", 10), False),
    (_sqlite_error(sqlite3.OperationalError, "no such table: busy"), False),
    (_sqlite_error(sqlite3.IntegrityError, "database is locked"), False),
    (_sqlite_error(sqlite3.DatabaseError, "database is locked", 5), False),
    (RuntimeError("database is locked"), False),
    (ValueError("database is locked"), False),
], ids=range(14))
def test_is_busy_error(exc, busy):
    assert sq.is_busy_error(exc) is busy


@pytest.mark.parametrize("exc, kind, errno_code, fatal", [
    (_sqlite_error(sqlite3.OperationalError, "database is locked", 5), StoreBusy, None, False),
    (_sqlite_error(sqlite3.OperationalError, "database is locked"), StoreBusy, None, False),
    (_sqlite_error(sqlite3.OperationalError, "database table is locked", 6), StoreError, None, False),
    (_sqlite_error(sqlite3.OperationalError, "database or disk is full", 13), StoreError, errno.ENOSPC, True),
    (_sqlite_error(sqlite3.OperationalError, "attempt to write a readonly database"), StoreError, errno.EROFS, True),
    (_sqlite_error(sqlite3.DatabaseError, "file is not a database", 26), CatalogCorrupt, None, False),
    (_sqlite_error(sqlite3.DatabaseError, "database disk image is malformed"), CatalogCorrupt, None, False),
    (_sqlite_error(sqlite3.IntegrityError, "UNIQUE constraint failed: meta.key", 1555), StoreError, None, False),
], ids=range(8))
def test_to_store_error(exc, kind, errno_code, fatal, tmp_path):
    for path, expected in ((tmp_path / "state.db", str(tmp_path / "state.db")), (None, None)):
        error = sq.to_store_error(exc, path)
        assert type(error) is kind and isinstance(error, StorageError)
        assert (error.errno, error.fatal, error.path) == (errno_code, fatal, expected)
        assert error.detail == str(exc) and str(exc) in str(error)
        assert (str(expected) in str(error)) if expected else not str(error).endswith(": None")


def test_to_store_error_names_an_error_without_text():
    error = sq.to_store_error(sqlite3.OperationalError(""))
    assert type(error) is StoreError and error.detail == "OperationalError"


# --- connect ve Connection ----------------------------------------------------------------------------

def test_connect_gives_an_unconfigured_connection_in_autocommit_mode(tmp_path):
    conn = sq.connect(tmp_path / "db.sqlite")
    try:
        assert type(conn) is sq.Connection
        assert conn.isolation_level is None and not conn.in_transaction
        assert conn.execute("SELECT 7 AS n").fetchone()["n"] == 7  # satırlar adla okunur
        assert _pragma(conn, "journal_mode") == "delete"  # PRAGMA'lar configure'un işi
        assert _pragma(conn, "busy_timeout") == sq.BUSY_TIMEOUT_MS  # sqlite3.connect(timeout=)
    finally:
        conn.close()
    short = sq.connect(str(tmp_path / "db.sqlite"), busy_timeout_ms=1234)
    try:
        assert _pragma(short, "busy_timeout") == 1234
    finally:
        short.close()


def test_connect_does_not_translate_sqlite_errors(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        sq.connect(tmp_path / "no" / "such" / "dir" / "db.sqlite")


def test_connection_can_be_closed_from_another_thread(tmp_path):
    conn = sq.connect(tmp_path / "db.sqlite")
    thread = threading.Thread(target=conn.close)
    thread.start()
    thread.join(5)
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
    sq.close_quietly(conn)  # ikinci kez kapatmak zararsız


def test_a_dropped_connection_closes_itself_without_a_resource_warning(tmp_path):
    """Python 3.13+ kapatılmamış bağlantı için ResourceWarning verir; alt sınıf çöpe giderken kendini kapatır."""
    conn = sq.connect(tmp_path / "db.sqlite")
    conn.execute("CREATE TABLE t (x)")
    ref = weakref.ref(conn)

    class Holder:
        pass

    holder = Holder()
    holder.conn, holder.cycle = conn, holder  # döngüsel çöp: sonlandırıcı sırası belirsiz
    del conn, holder
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        gc.collect()
    assert ref() is None


# --- configure ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("synchronous, value", [("NORMAL", 1), ("FULL", 2)])
def test_configure_applies_the_documented_pragmas(tmp_path, synchronous, value):
    conn = sq.connect(tmp_path / "db.sqlite")
    try:
        assert sq.configure(conn, synchronous=synchronous) == "wal"
        assert _pragma(conn, "journal_mode") == "wal"
        assert _pragma(conn, "busy_timeout") == 5000
        assert _pragma(conn, "foreign_keys") == 1
        assert _pragma(conn, "temp_store") == 2  # MEMORY
        assert _pragma(conn, "journal_size_limit") == 67108864
        assert _pragma(conn, "synchronous") == value
        assert not conn.in_transaction
    finally:
        conn.close()


def test_configure_defaults_and_rejects_other_synchronous_values(tmp_path):
    conn = sq.connect(tmp_path / "db.sqlite")
    try:
        with pytest.raises(ValueError):
            sq.configure(conn, synchronous="OFF")
        assert _pragma(conn, "journal_mode") == "delete"  # reddedilen çağrı hiçbir şey değiştirmedi
        assert sq.configure(conn, busy_timeout_ms=1234) == "wal"
        assert _pragma(conn, "synchronous") == 1 and _pragma(conn, "busy_timeout") == 1234
    finally:
        conn.close()


def test_configure_in_memory_reports_memory():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        assert sq.configure(conn) == "memory"
        assert _pragma(conn, "foreign_keys") == 1
    finally:
        conn.close()


def test_configure_uses_the_callers_wal_request_and_falls_back_to_delete(tmp_path):
    """state.py WAL isteğini kendi yöntemiyle yapar (StoreBusy'ye çevirir); "wal" dışındaki yanıt DELETE'e düşer."""
    asked: List[sqlite3.Connection] = []

    def refuses(conn: sqlite3.Connection) -> str:
        asked.append(conn)
        return "delete"  # ağ dosya sistemi: WAL kurulamadı

    conn = sq.connect(tmp_path / "db.sqlite")
    try:
        assert sq.configure(conn, synchronous="FULL", request_wal=refuses) == "delete"
        assert asked == [conn]
        assert _pragma(conn, "journal_mode") == "delete"
        assert _pragma(conn, "foreign_keys") == 1 and _pragma(conn, "synchronous") == 2  # diğerleri yine uygulanır
    finally:
        conn.close()

    conn = sq.connect(tmp_path / "other.sqlite")
    try:
        assert sq.configure(conn, request_wal=lambda c: sq.set_journal_mode(c, "WAL")) == "wal"
    finally:
        conn.close()


def test_state_db_routes_its_wal_request_through_configure(tmp_path, monkeypatch):
    calls: List[str] = []
    original = StateDb._request_wal

    def spy(self, conn):
        calls.append("wal")
        return original(self, conn)

    monkeypatch.setattr(StateDb, "_request_wal", spy)
    state = StateDb(tmp_path / "state.db")
    try:
        assert calls == ["wal"] and state.journal_mode == "wal"
        assert _pragma(state.connection(), "synchronous") == 2
    finally:
        state.close()


# --- WAL'a geçişin yeniden denenmesi ------------------------------------------------------------------

class _BusyThenOk:
    """`PRAGMA journal_mode` çağrısında `failures` kez hata veren sahte bağlantı."""

    def __init__(self, failures: int, error: Optional[Exception] = None, answer: Optional[tuple] = ("wal",)):
        self.failures = failures
        self.error = error or sqlite3.OperationalError("database is locked")
        self.answer = answer
        self.calls = 0
        self.statements: List[str] = []

    def execute(self, sql: str):
        self.calls += 1
        self.statements.append(sql)
        if self.calls <= self.failures:
            raise self.error
        return self

    def fetchone(self):
        return self.answer


def test_journal_mode_switch_is_retried_with_a_growing_pause(monkeypatch):
    pauses: List[float] = []
    monkeypatch.setattr(sq.time, "sleep", pauses.append)
    conn = _BusyThenOk(failures=7)

    assert sq.set_journal_mode(conn, "WAL", 5000) == "wal"
    assert conn.calls == 8 and set(conn.statements) == {"PRAGMA journal_mode = WAL"}
    assert pauses == [0.005, 0.01, 0.02, 0.04, 0.08, 0.1, 0.1]  # iki katına çıkar, 0.1 saniyede durur

    pauses.clear()
    assert sq.set_journal_mode(_BusyThenOk(failures=2), "DELETE", 5000, pause=0.001) == "wal"
    assert pauses == [0.001, 0.002]


def test_journal_mode_switch_gives_up_at_the_deadline_and_never_retries_other_errors(monkeypatch):
    monkeypatch.setattr(sq.time, "sleep", lambda seconds: None)
    stuck = _BusyThenOk(failures=10 ** 9)
    started = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        sq.set_journal_mode(stuck, "WAL", 50)  # süre sınırı busy_timeout'tur
    assert time.monotonic() - started < 5 and stuck.calls > 1

    broken = _BusyThenOk(failures=1, error=sqlite3.OperationalError("disk I/O error"))
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        sq.set_journal_mode(broken, "WAL", 5000)
    assert broken.calls == 1

    locked_table = _BusyThenOk(failures=1, error=_sqlite_error(sqlite3.OperationalError, "database table is locked", 6))
    with pytest.raises(sqlite3.OperationalError, match="table is locked"):
        sq.set_journal_mode(locked_table, "WAL", 5000)  # SQLITE_LOCKED beklemekle geçmez
    assert locked_table.calls == 1


def test_journal_mode_answer_is_lower_case_and_empty_when_sqlite_says_nothing():
    assert sq.set_journal_mode(_BusyThenOk(0, answer=("WAL",)), "WAL") == "wal"
    assert sq.set_journal_mode(_BusyThenOk(0, answer=None), "WAL") == ""
    assert sq.set_journal_mode(_BusyThenOk(0, answer=(None,)), "WAL") == ""


def test_state_db_turns_the_expired_wal_wait_into_store_busy(tmp_path, monkeypatch):
    state = StateDb(tmp_path / "state.db")
    try:
        monkeypatch.setattr(state_mod, "BUSY_TIMEOUT_MS", 20)
        monkeypatch.setattr(state_mod, "_WAL_RETRY_PAUSE", 0.001)
        with pytest.raises(StoreBusy) as busy:
            state._request_wal(_BusyThenOk(failures=10 ** 9))
        assert busy.value.path == str(tmp_path / "state.db") and busy.value.detail == "database is locked"
        assert isinstance(busy.value.__cause__, sqlite3.OperationalError)
    finally:
        state.close()


# --- aynı anda ilk açılış -----------------------------------------------------------------------------

@pytest.mark.parametrize("synchronous", ["NORMAL", "FULL"])
@pytest.mark.parametrize("round_no", range(10))
def test_new_file_configured_by_several_connections_at_once(tmp_path, round_no, synchronous):
    """
    Yeni bir dosyayı aynı anda açanların hiçbiri hata almaz: WAL'a geçiş yarışında SQLite beklemeden
    SQLITE_BUSY döner, `configure` yeniden dener. ST-06 ve ST-09 bu hatayı ayrı ayrı bulmuştu.
    """
    path = tmp_path / "new.db"
    count = 8
    barrier = threading.Barrier(count)
    results: list = []

    def run() -> None:
        try:
            barrier.wait(10)
            conn = sq.connect(path)
            try:
                results.append(sq.configure(conn, synchronous=synchronous))
            finally:
                conn.close()
        except BaseException as exc:  # noqa: BLE001 - hata da bir sonuçtur, aşağıda denetlenir
            results.append(exc)

    threads = [threading.Thread(target=run) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert results == ["wal"] * count, results


@pytest.mark.parametrize("round_no", range(5))
def test_new_data_directory_opened_by_both_stores_at_once(tmp_path, round_no):
    """Altı iş parçacığı aynı anda state.db'yi ve ona ATTACH eden catalog.db'yi ilk kez açar."""
    state_path = tmp_path / ".meta" / "state.db"
    state_path.parent.mkdir()
    catalog_path = catalog.catalog_path(tmp_path)
    count = 6
    barrier = threading.Barrier(count)
    errors: List[BaseException] = []
    modes: List[str] = []

    def run() -> None:
        try:
            barrier.wait(10)
            state = StateDb(state_path)
            try:
                with Catalog(catalog_path, attach={"state": state_path}) as cat:
                    cat.prepare()
                    modes.append(state.journal_mode + "/" + cat.journal_mode)
            finally:
                state.close()
        except BaseException as exc:  # noqa: BLE001 - test iş parçacığı: hatayı ana iş parçacığına taşı
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)

    assert errors == []
    assert modes == ["wal/wal"] * count
    with Catalog(catalog_path, attach={"state": state_path}) as cat:
        assert cat.inspect().schema_version == catalog.CATALOG_SCHEMA
        assert cat.connection().execute("SELECT count(*) FROM state.meta").fetchone()[0] == 0


_CHILD = """
import sys, time
from src.store import sqlite as sq
path, start_at, synchronous = sys.argv[1], float(sys.argv[2]), sys.argv[3]
time.sleep(max(0.0, start_at - time.time()))
conn = sq.connect(path)
try:
    print(sq.configure(conn, synchronous=synchronous))
finally:
    conn.close()
"""


def test_new_file_configured_by_several_processes_at_once(tmp_path):
    """Aynı yarış süreçler arasında: çocuklar ortak bir başlangıç anını bekler, hepsi "wal" yazıp temiz çıkar."""
    path = str(tmp_path / "new.db")
    start_at = time.time() + 1.0
    children = [
        subprocess.Popen([sys.executable, "-c", _CHILD, path, repr(start_at), ("NORMAL", "FULL")[n % 2]], cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for n in range(4)
    ]
    outputs = [child.communicate(timeout=120) for child in children]

    assert [child.returncode for child in children] == [0] * 4, [err for _out, err in outputs]
    assert [out.strip() for out, _err in outputs] == ["wal"] * 4


# --- yeni veritabanı dosyasının izni (karar S15; plan maddesi FX-11) ----------------------------------

WINDOWS = os.name == "nt"
posix_modes = pytest.mark.skipif(WINDOWS, reason="POSIX dosya izinleri Windows'ta yok")
UMASKS = [0o022, 0o077, 0o002, 0o027]


def _mode(path) -> int:
    """rwx bitleri; setgid'li bir üst dizinden miras kalabilen özel bitler sayılmaz."""
    return stat.S_IMODE(os.stat(path).st_mode) & 0o777


@contextlib.contextmanager
def _umask(value: int) -> Iterator[None]:
    """Bloğu verilen umask ile çalıştırır, sonra eskisini geri koyar (umask süreç geneli bir ayardır)."""
    previous = os.umask(value)
    try:
        yield
    finally:
        os.umask(previous)


def _recording_creations(monkeypatch) -> List[Tuple[str, int, bool, bool]]:
    """
    `os.open` ile **oluşturulan** her dosyayı kaydeder: (ad, istenen izin, O_EXCL var mı, O_TRUNC var mı).
    Var olan dosyayı açan ya da başarısız olan çağrılar listeye girmez.
    """
    created: List[Tuple[str, int, bool, bool]] = []
    real_open = os.open

    def recording_open(path, flags, mode=0o777, **kwargs):
        existed = os.path.lexists(path)
        fd = real_open(path, flags, mode, **kwargs)
        if not existed and flags & os.O_CREAT:
            created.append((os.path.basename(os.fspath(path)), mode, bool(flags & os.O_EXCL), bool(flags & os.O_TRUNC)))
        return fd

    monkeypatch.setattr(sq.os, "open", recording_open)
    return created


def test_create_database_file_makes_an_empty_file_once_and_never_truncates(tmp_path):
    path = tmp_path / "new.db"

    assert sq.create_database_file(path) is True
    assert path.read_bytes() == b""  # boş dosya SQLite için boş bir veritabanıdır
    assert sq.create_database_file(str(path)) is False  # zaten var

    conn = sq.connect(path)
    try:
        conn.execute("CREATE TABLE t (n INTEGER)")
        conn.execute("INSERT INTO t VALUES (7)")
    finally:
        conn.close()
    size = path.stat().st_size
    assert size > 0
    assert sq.create_database_file(path) is False and path.stat().st_size == size  # dokunulmadı
    again = sq.connect(path)  # connect de var olan dosyayı kesmez
    try:
        assert again.execute("SELECT n FROM t").fetchone()["n"] == 7
    finally:
        again.close()


@pytest.mark.parametrize("name", [":memory:", "", "file:memdb1?mode=memory&cache=shared", "file:other.db"])
def test_create_database_file_leaves_memory_temporary_and_uri_databases_alone(tmp_path, monkeypatch, name):
    monkeypatch.chdir(tmp_path)

    assert sq.create_database_file(name) is False
    assert os.listdir(tmp_path) == []  # o adla bir dosya da oluşmadı


def test_connect_to_an_in_memory_database_creates_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    conn = sq.connect(":memory:")
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()
    assert os.listdir(tmp_path) == []


def test_create_database_file_reports_nothing_when_it_cannot_create(tmp_path):
    """Hata SQLite'a bırakılır: aynı yolu açan `connect` eskisi gibi sqlite3.OperationalError verir."""
    missing_dir = tmp_path / "no" / "such" / "dir" / "db.sqlite"
    assert sq.create_database_file(missing_dir) is False

    a_directory = tmp_path / "dir.db"
    a_directory.mkdir()
    assert sq.create_database_file(a_directory) is False and a_directory.is_dir()

    with pytest.raises(sqlite3.OperationalError):
        sq.connect(missing_dir)


@pytest.mark.skipif(WINDOWS, reason="sembolik bağ oluşturmak Windows'ta yetki ister")
def test_a_dangling_symlink_is_left_to_sqlite(tmp_path):
    """O_EXCL sembolik bağı izlemez: bağ kesilmez, yerine dosya konmaz; hedefi eskisi gibi SQLite oluşturur."""
    target = tmp_path / "elsewhere" / "real.db"
    target.parent.mkdir()
    link = tmp_path / "linked.db"
    os.symlink(target, link)

    assert sq.create_database_file(link) is False
    assert link.is_symlink() and not target.exists()
    conn = sq.connect(link)
    try:
        conn.execute("CREATE TABLE t (n INTEGER)")
    finally:
        conn.close()
    assert link.is_symlink() and target.stat().st_size > 0


def test_one_of_several_racing_creators_creates_the_file(tmp_path):
    path = tmp_path / "raced.db"
    count = 8
    barrier = threading.Barrier(count)
    results: list = []

    def run() -> None:
        barrier.wait(10)
        results.append(sq.create_database_file(path))

    threads = [threading.Thread(target=run) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert sorted(results) == [False] * (count - 1) + [True]
    assert path.read_bytes() == b""


_WRITING_CHILD = """
import sys, time
from src.store import sqlite as sq
path, start_at, number = sys.argv[1], float(sys.argv[2]), int(sys.argv[3])
time.sleep(max(0.0, start_at - time.time()))
conn = sq.connect(path)
try:
    sq.configure(conn)
    sq.begin_immediate(conn, path)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (number INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO seen VALUES (?)", (number,))
    conn.execute("COMMIT")
finally:
    conn.close()
"""


def test_processes_that_create_the_file_at_once_do_not_truncate_each_others_file(tmp_path):
    """Dosyayı önceden oluşturmak yarışa bir kesme eklemez: her sürecin yazdığı satır yerinde kalır."""
    path = str(tmp_path / "new.db")
    start_at = time.time() + 1.0
    children = [
        subprocess.Popen([sys.executable, "-c", _WRITING_CHILD, path, repr(start_at), str(n)], cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for n in range(4)
    ]
    outputs = [child.communicate(timeout=120) for child in children]

    assert [child.returncode for child in children] == [0] * 4, [err for _out, err in outputs]
    conn = sqlite3.connect(path)
    try:
        assert [row[0] for row in conn.execute("SELECT number FROM seen ORDER BY number")] == [0, 1, 2, 3]
    finally:
        conn.close()


def test_every_new_database_file_is_created_with_the_store_file_mode(tmp_path, monkeypatch):
    """
    Bir veritabanı dosyasının oluştuğu her yer: `connect`, state.db'nin ilk açılışı (dosyayı kimlik
    yoklaması yaratır, `connect` değil), geçiş öncesi kopya, catalog.db ve onun yeniden yaratma dosyası.
    Hepsi dosyayı SQLite'tan önce, `files.STORE_FILE_MODE` ile, yalnızca yoksa (O_EXCL) ve kesmeden açar.
    """
    assert files.STORE_FILE_MODE == 0o666
    created = _recording_creations(monkeypatch)
    expected = (0o666, True, False)

    sq.connect(tmp_path / "plain.db").close()
    assert created == [("plain.db", *expected)]
    sq.connect(tmp_path / "plain.db").close()  # var olan dosya: yeni kayıt yok
    assert created == [("plain.db", *expected)]
    del created[:]

    state = StateDb(tmp_path / "state.db")
    try:
        assert created == [("state.db", *expected)]
        del created[:]
        version = state.schema_version
        state._backup(state.connection(), version)
        backup = os.path.basename(state.backup_path(version))
        assert len(created) == 1 and created[0][1:] == expected
        assert created[0][0].startswith(backup + ".") and created[0][0].endswith(".tmp")
        assert os.path.isfile(state.backup_path(version))
        del created[:]

        with Catalog(catalog.catalog_path(tmp_path)) as cat:
            cat.prepare()
            assert created == [("catalog.db", *expected)]
            del created[:]
            assert CatalogAdmin(tmp_path, cat).rebuild(mode="recreate").completed
            assert created == [("catalog.db" + BUILD_SUFFIX, *expected)]
    finally:
        state.close()


@posix_modes
@pytest.mark.parametrize("synchronous", ["NORMAL", "FULL"])
def test_a_new_database_and_its_wal_files_follow_the_umask(tmp_path, synchronous):
    """SQLite `-wal` ve `-shm` dosyalarına veritabanı dosyasının iznini verir: 002 → 0664, 077 → 0600."""
    for mask in UMASKS:
        path = tmp_path / f"new-{mask:03o}.db"
        with _umask(mask):
            conn = sq.connect(path)
            try:
                assert sq.configure(conn, synchronous=synchronous) == "wal"
                conn.execute("CREATE TABLE t (n INTEGER)")
                names = [str(path), f"{path}-wal", f"{path}-shm"]  # bağlantı açıkken üçü de var
                assert [_mode(name) for name in names] == [0o666 & ~mask] * 3, f"umask {mask:03o}"
            finally:
                conn.close()


@posix_modes
def test_a_database_in_delete_mode_gives_its_journal_the_same_mode(tmp_path):
    """WAL açılamayan dosya sistemindeki `-journal` dosyası da veritabanının iznini alır."""
    path = tmp_path / "new.db"
    with _umask(0o002):
        conn = sq.connect(path)
        try:
            assert sq.configure(conn, request_wal=lambda _conn: "delete") == "delete"
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE t (n INTEGER)")
            assert (_mode(path), _mode(f"{path}-journal")) == (0o664, 0o664)
            conn.execute("COMMIT")
        finally:
            conn.close()


@posix_modes
@pytest.mark.parametrize("old_mode", [0o644, 0o600, 0o664])
def test_an_existing_database_file_keeps_its_mode(tmp_path, old_mode):
    """İzin yalnızca dosya oluşturulurken verilir: önceki sürümün 0644 ile yarattığı dosyaya chmod yapılmaz."""
    path = tmp_path / "old.db"
    with _umask(0o022):
        sqlite3.connect(path).close()  # bu değişiklikten önceki gibi: dosyayı SQLite yaratır
    assert _mode(path) == 0o644
    os.chmod(path, old_mode)

    for mask in UMASKS:
        with _umask(mask):
            conn = sq.connect(path)
            try:
                assert sq.configure(conn) == "wal"
                conn.execute("CREATE TABLE IF NOT EXISTS t (n INTEGER)")
                conn.execute("INSERT INTO t VALUES (1)")
                # Yan dosyalar umask'i değil veritabanı dosyasını izler
                assert [_mode(name) for name in (path, f"{path}-wal", f"{path}-shm")] == [old_mode] * 3
            finally:
                conn.close()
    assert _mode(path) == old_mode


# --- iş parçacığı başına bağlantı ---------------------------------------------------------------------

def _in_thread(target) -> None:
    thread = threading.Thread(target=target)
    thread.start()
    thread.join(10)
    assert not thread.is_alive()


def test_thread_connections_give_each_thread_its_own(tmp_path):
    path = tmp_path / "db.sqlite"
    registry = sq.ThreadConnections()
    assert registry.current() is None and len(registry) == 0

    main = sq.connect(path)
    registry.adopt(main)
    assert registry.current() is main and len(registry) == 1

    seen: list = []
    ready, release = threading.Event(), threading.Event()

    def worker() -> None:
        seen.append(registry.current())  # başka iş parçacığının bağlantısı görünmez
        registry.adopt(sq.connect(path))
        seen.append(registry.current() is not main and registry.current() is not None)
        ready.set()
        release.wait(10)

    thread = threading.Thread(target=worker)
    thread.start()
    assert ready.wait(10)
    assert seen == [None, True] and len(registry) == 2
    release.set()
    thread.join(10)
    gc.collect()

    # İş parçacığı bitince bağlantısı da gider: kayıt onu yalnızca zayıf başvuruyla izler
    assert len(registry) == 1 and registry.current() is main
    registry.close_all()


def test_close_finished_closes_a_dead_threads_connection_that_is_still_referenced(tmp_path):
    path = tmp_path / "db.sqlite"
    registry = sq.ThreadConnections()
    main = sq.connect(path)
    registry.adopt(main)
    kept: List[sq.Connection] = []

    def worker() -> None:
        conn = sq.connect(path)
        registry.adopt(conn)
        kept.append(conn)

    _in_thread(worker)
    assert len(registry) == 2
    assert kept[0].execute("SELECT 1").fetchone()[0] == 1  # başkası tuttuğu için hâlâ açık

    registry.close_finished()

    with pytest.raises(sqlite3.ProgrammingError):
        kept[0].execute("SELECT 1")
    assert len(registry) == 1 and main.execute("SELECT 1").fetchone()[0] == 1  # yaşayan iş parçacığınınki durur
    registry.close_finished()  # yapacak iş yokken zararsız
    registry.close_all()


def test_close_all_closes_every_thread_and_forgets_the_bindings(tmp_path):
    path = tmp_path / "db.sqlite"
    registry = sq.ThreadConnections()
    main = sq.connect(path)
    registry.adopt(main)
    other: List[sq.Connection] = []
    after_close: list = []
    ready, closed, done = threading.Event(), threading.Event(), threading.Event()

    def worker() -> None:
        conn = sq.connect(path)
        registry.adopt(conn)
        other.append(conn)
        ready.set()
        closed.wait(10)
        after_close.append(registry.current())  # kapatılan bağlantı geri verilmez
        done.set()

    thread = threading.Thread(target=worker)
    thread.start()
    assert ready.wait(10)

    registry.close_all()
    closed.set()
    assert done.wait(10)
    thread.join(10)

    for conn in (main, other[0]):
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
    assert after_close == [None] and registry.current() is None and len(registry) == 0
    registry.close_all()  # ikinci kez çağırmak zararsız

    again = sq.connect(path)  # kayıt yeniden kullanılabilir (Catalog.close() sonrası yeniden açılış)
    registry.adopt(again)
    assert registry.current() is again and len(registry) == 1
    registry.close_all()


def test_release_untracks_without_closing(tmp_path):
    registry = sq.ThreadConnections()
    assert registry.release() is None
    conn = sq.connect(tmp_path / "db.sqlite")
    registry.adopt(conn)

    assert registry.release() is conn
    assert registry.current() is None and len(registry) == 0
    registry.close_all()  # artık izlenmiyor: kapatmaz
    assert conn.execute("SELECT 1").fetchone()[0] == 1
    conn.close()


def test_a_dropped_registry_closes_its_connections_without_a_resource_warning(tmp_path):
    registry = sq.ThreadConnections()
    registry.adopt(sq.connect(tmp_path / "db.sqlite"))
    ref = weakref.ref(registry.current())
    registry.cycle = registry  # type: ignore[attr-defined]
    del registry
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        gc.collect()
    assert ref() is None


def test_a_dropped_catalog_closes_its_connections_without_a_resource_warning(tmp_path):
    """state.py'deki kendini kapatan bağlantı artık katalogda da var (kapatılmadan bırakılan Catalog)."""
    cat = Catalog(catalog.catalog_path(tmp_path))
    cat.prepare()
    ref = weakref.ref(cat.connection())
    cat.cycle = cat  # type: ignore[attr-defined]
    del cat
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        gc.collect()
    assert ref() is None


def test_catalog_does_not_keep_a_finished_threads_connection_alive(tmp_path):
    with Catalog(catalog.catalog_path(tmp_path)) as cat:
        cat.prepare()
        refs: list = []
        _in_thread(lambda: refs.append(weakref.ref(cat.connection())))
        gc.collect()
        assert refs[0]() is None and len(cat._connections) == 1
        assert cat.connection().execute("SELECT 1").fetchone()[0] == 1


# --- işlemler -----------------------------------------------------------------------------------------

def test_begin_immediate_raises_store_busy_when_the_lock_is_held(tmp_path):
    path = tmp_path / "db.sqlite"
    holder = sq.connect(path)
    waiter = sq.connect(path, busy_timeout_ms=50)
    try:
        sq.configure(holder)
        sq.configure(waiter, busy_timeout_ms=50)
        holder.execute("CREATE TABLE t (x)")
        sq.begin_immediate(holder, path)
        assert holder.in_transaction

        with pytest.raises(StoreBusy) as busy:
            sq.begin_immediate(waiter, path)
        assert busy.value.path == str(path) and busy.value.fatal is False
        assert isinstance(busy.value, StorageError) and isinstance(busy.value.__cause__, sqlite3.OperationalError)
        assert not waiter.in_transaction
        assert waiter.execute("SELECT count(*) FROM t").fetchone()[0] == 0  # okuma, yazar varken de çalışır (WAL)

        holder.execute("COMMIT")
        sq.begin_immediate(waiter)  # yol vermek zorunlu değil
        waiter.execute("INSERT INTO t VALUES (1)")
        waiter.execute("COMMIT")
    finally:
        holder.close()
        waiter.close()


def test_begin_immediate_leaves_other_sqlite_errors_alone(tmp_path):
    conn = sq.connect(tmp_path / "db.sqlite")
    try:
        sq.begin_immediate(conn)
        with pytest.raises(sqlite3.OperationalError, match="within a transaction"):
            sq.begin_immediate(conn)  # StoreBusy değil: meşguliyet dışındaki hata olduğu gibi çıkar
    finally:
        conn.close()


def test_rollback_only_when_a_transaction_is_open(tmp_path):
    conn = sq.connect(tmp_path / "db.sqlite")
    try:
        conn.execute("CREATE TABLE t (x)")
        statements: List[str] = []
        conn.set_trace_callback(statements.append)
        sq.rollback(conn)  # işlem yok: hiçbir şey çalıştırmaz
        assert statements == []

        sq.begin_immediate(conn)
        conn.execute("INSERT INTO t VALUES (1)")
        sq.rollback(conn)
        assert not conn.in_transaction and statements[-1] == "ROLLBACK"
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 0
    finally:
        conn.close()


def test_state_and_catalog_report_a_held_write_lock_the_same_way(tmp_path, monkeypatch):
    """İki dosyada da yazma kilidi alınamazsa aynı çeviriden geçen StoreBusy çıkar."""
    monkeypatch.setattr(state_mod, "BUSY_TIMEOUT_MS", 50)
    state = StateDb(tmp_path / "state.db")
    cat = Catalog(catalog.catalog_path(tmp_path), busy_timeout_ms=50)
    cat.prepare()
    holders = [sqlite3.connect(p, isolation_level=None) for p in (state.path, cat.path)]
    try:
        for holder in holders:
            holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(StoreBusy) as state_busy:
            state.meta_set("k", "v")
        with pytest.raises(StoreBusy) as catalog_busy:
            with cat.write():
                pass
        assert (state_busy.value.path, catalog_busy.value.path) == (state.path, cat.path)
        assert state_busy.value.detail == catalog_busy.value.detail == "database is locked"
        assert str(state_busy.value).replace(state.path, "") == str(catalog_busy.value).replace(cat.path, "")
    finally:
        for holder in holders:
            holder.close()
        cat.close()
        state.close()


# --- WAL uyarısı --------------------------------------------------------------------------------------

def test_wal_fallback_is_reported_once_per_file(tmp_path, caplog):
    log = logging.getLogger("tests.store.sqlite")
    first, second = tmp_path / "data" / ".meta" / "state.db", tmp_path / "data" / ".meta" / "catalog.db"
    with caplog.at_level(logging.WARNING, logger=log.name):
        assert sq.warn_no_wal(log, first, "delete") is True
        assert sq.warn_no_wal(log, str(first), "delete") is False  # aynı dosya: yinelenmez
        assert sq.warn_no_wal(log, second, "delete") is True

    messages = [r.getMessage() for r in caplog.records if r.name == log.name]
    assert len(messages) == 2
    assert "state.db could not be switched to WAL" in messages[0] and str(first) in messages[0]
    assert "DELETE journal mode" in messages[0] and "one process" in messages[0]
    assert messages[1].startswith("catalog.db ") and str(second) in messages[1]
    assert all(r.levelno == logging.WARNING for r in caplog.records if r.name == log.name)


# --- SQL betikleri ------------------------------------------------------------------------------------

def test_split_statements_keeps_strings_comments_and_trigger_bodies_whole():
    script = (
        "-- baş; yorum 'tırnak'\n"
        "CREATE TABLE a (x TEXT DEFAULT 'p;q'); -- son; yorum\n"
        "/* blok; yorum */\n"
        "CREATE TRIGGER t AFTER INSERT ON a BEGIN\n  UPDATE a SET x = 'r;s';\n  DELETE FROM a WHERE x IS NULL;\nEND;\n"
        ";\n"
        "INSERT INTO a VALUES ('z'); INSERT INTO a VALUES ('--y'); /* aynı satırda iki deyim */\n"
        "\n"
        "CREATE INDEX i\n  ON a(x);\n"
        "-- kuyruk\n"
    )

    statements = sq.split_statements(script)

    assert len(statements) == 5
    assert statements[0].startswith("-- baş; yorum") and statements[0].endswith("-- son; yorum")
    assert statements[1].startswith("/* blok; yorum */\nCREATE TRIGGER t") and statements[1].endswith("IS NULL;\nEND;")
    assert statements[2] == "INSERT INTO a VALUES ('z');"
    assert statements[3] == "INSERT INTO a VALUES ('--y');"
    assert statements[4] == "/* aynı satırda iki deyim */\n\nCREATE INDEX i\n  ON a(x);"

    conn = sqlite3.connect(":memory:")
    try:
        for statement in statements:
            conn.execute(statement)
        assert sorted(r[0] for r in conn.execute("SELECT x FROM a")) == ["r;s", "r;s"]
    finally:
        conn.close()


def test_split_statements_empty_and_unfinished_scripts():
    assert sq.split_statements("") == []
    assert sq.split_statements("-- yalnızca yorum\n/* ve; bir blok */\n") == []
    assert sq.split_statements(";;\n ; -- boş deyimler\n") == []
    assert sq.split_statements("SELECT 1;") == ["SELECT 1;"]
    with pytest.raises(ValueError, match="CREATE TABLE b"):
        sq.split_statements("CREATE TABLE a (x);\nCREATE TABLE b (y)")
    with pytest.raises(ValueError):
        sq.split_statements("CREATE TABLE a (x TEXT DEFAULT 'açık kalan dize;);\n")


def test_split_statements_error_type_per_module():
    """Ortak bölücü ValueError verir; catalog.py onu StoreError'a çevirir (state.py geçişi okurken çevirir)."""
    unfinished = "CREATE TABLE a (x);\nCREATE TABLE b ("
    with pytest.raises(ValueError):
        state_mod.split_statements(unfinished)
    with pytest.raises(StoreError, match="CREATE TABLE b") as refused:
        catalog.split_statements(unfinished)
    assert isinstance(refused.value.__cause__, ValueError)
    assert catalog.split_statements("CREATE TABLE a (x); -- c\n") == sq.split_statements("CREATE TABLE a (x); -- c\n")


def test_shipped_scripts_split_into_the_statements_they_contain():
    """Gerçek betikler: her deyim tek başına çalışır ve yorumlar dışında tek bir SQL deyimidir."""
    schema = Path(catalog.SCHEMA_FILE).read_text(encoding="utf-8")
    migration = (Path(state_mod.MIGRATIONS_DIR) / "0001_initial.sql").read_text(encoding="utf-8")
    for script, creates in ((schema, 34), (migration, 16)):
        statements = sq.split_statements(script)
        assert len(statements) == creates
        conn = sqlite3.connect(":memory:")
        try:
            for statement in statements:
                assert sq.strip_comments(statement).upper().startswith("CREATE ")
                assert sq.strip_comments(statement).count(";") == 1
                conn.execute(statement)
            made = conn.execute("SELECT count(*) FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")
            assert made.fetchone()[0] == creates
        finally:
            conn.close()
    assert tuple(sq.split_statements(schema)) == catalog.schema_statements()
    assert sq.split_statements(migration) == state_mod.load_migrations()[0].statements()


def test_strip_comments():
    assert sq.strip_comments("  -- a\nALTER TABLE t ADD c; /* b\n c */ -- d\n") == "ALTER TABLE t ADD c;"
    assert sq.strip_comments("-- yalnızca yorum") == "" and sq.strip_comments("/* x */ ; -- y") == ";"


# --- katmanlama ---------------------------------------------------------------------------------------

def test_module_loads_only_the_error_classes():
    """Ortak modül Store'un geri kalanını (katalog, state, spor kayıt defteri) yüklemez."""
    import json

    code = (
        "import sys, json; import src.store.sqlite; "
        "print(json.dumps(sorted(m for m in sys.modules if m == 'src' or m.startswith('src.'))))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)

    assert set(json.loads(out.stdout)) == {"src", "src.exceptions", "src.store", "src.store.errors", "src.store.sqlite"}
