"""
src/store/state.py: state.db, geçiş çalıştırıcısı ve RuntimeFacts
(docs/design/01-storage.md bölüm 3.1-3.3 ve 7.3; plan maddesi ST-09).

Şema, bağlantı ayarları, geçişler (başarısız geçiş, sahte ikinci geçiş, daha yeni dosya). Tümü çevrimdışı.
"""
from __future__ import annotations

import gc
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import warnings
import weakref
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.exceptions import StorageError
from src.store import SchemaTooNew, StoreBusy, StoreError
from src.store import state as state_mod
from src.store.state import APPLICATION_ID, RuntimeFacts, StateDb, load_migrations, split_statements

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TABLES = {
    "meta", "follows", "jobs", "stream_events", "sink_cursors", "watch_state", "runtime", "leases", "migration_runs",
}
INDEXES = {
    "follows_tournament_name", "follows_position", "jobs_started", "stream_events_stream", "stream_events_event",
    "stream_events_ts", "stream_events_dedup",
}

# 2.x'in jobs.db'yi kurduğu DDL (bu PR'dan önceki src/web/jobs.py:105-124), olduğu gibi
LEGACY_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    current_task TEXT NOT NULL DEFAULT '',
    payload_json TEXT,
    log_json TEXT NOT NULL DEFAULT '[]',
    result_json TEXT,
    started_at TEXT,
    finished_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    matches_total INTEGER NOT NULL DEFAULT 0,
    matches_done INTEGER NOT NULL DEFAULT 0,
    matches_failed INTEGER NOT NULL DEFAULT 0,
    schedule_empty_seasons INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_triggered INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_reason TEXT,
    eta_seconds REAL,
    current_batch TEXT NOT NULL DEFAULT ''
)
"""

LEGACY_ROWS: List[Dict[str, Any]] = [
    {
        "id": "job-old-1", "status": "completed", "progress": 100, "current_task": "Done",
        "payload_json": json.dumps({"mode": "full", "selections": [{"league_id": 17}]}),
        "log_json": json.dumps(["[Running] Sezonlar", "[Completed] Done"]),
        "result_json": json.dumps({"failed_count": 0}),
        "started_at": "2026-09-01T10:00:00+00:00", "finished_at": "2026-09-01T10:05:00+00:00",
        "matches_total": 380, "matches_done": 380, "eta_seconds": 0.0, "current_batch": "Premier League",
    },
    {
        "id": "job-old-2", "status": "failed", "progress": 40, "current_task": "SofaScore engelliyor",
        "payload_json": json.dumps({"mode": "details"}),
        "started_at": "2026-09-02T10:00:00+00:00", "finished_at": "2026-09-02T10:01:00+00:00",
        "matches_total": 10, "matches_done": 4, "matches_failed": 6,
        "circuit_breaker_triggered": 1, "circuit_breaker_reason": "403",
    },
    {
        "id": "job-old-3", "status": "running", "progress": 10, "current_task": "",
        "payload_json": json.dumps({"mode": "full"}),
        "started_at": "2026-09-03T10:00:00+00:00", "cancel_requested": 1,
    },
]


def _sha256(path: Any) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _make_legacy_db(path: Any, rows: List[Dict[str, Any]] = LEGACY_ROWS, ddl: str = LEGACY_JOBS_DDL) -> str:
    """2.x'in yazdığı biçimde bir jobs.db kurar (geri alma günlüğü kipi, user_version 0)."""
    path = os.fspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(ddl)
        for row in rows:
            columns = ", ".join(row)
            conn.execute(f"INSERT INTO jobs ({columns}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
        conn.commit()
    finally:
        conn.close()
    return path


def _raw(path: Any, sql: str, *args: Any) -> list:
    """Dosyayı Store'dan bağımsız, düz bir bağlantıyla sorgular."""
    conn = sqlite3.connect(os.fspath(path))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _migrations_with(tmp_path: Path, extra: Dict[str, str]) -> str:
    """Gerçek geçişlerin kopyası + `extra` içindeki betikler; StateDb'ye `migrations_dir` olarak verilir."""
    target = tmp_path / f"migrations-{len(os.listdir(tmp_path))}"
    shutil.copytree(state_mod.MIGRATIONS_DIR, target)
    for name, sql in extra.items():
        (target / name).write_text(sql, encoding="utf-8")
    return str(target)


@pytest.fixture
def db(tmp_path):
    state = StateDb(tmp_path / "state.db")
    yield state
    state.close()


# --- şema ve bağlantı ayarları -----------------------------------------------------------------

def test_new_file_gets_the_schema_of_the_design(db, tmp_path):
    assert db.schema_version == db.latest_version == 1
    path = tmp_path / "state.db"
    assert _raw(path, "PRAGMA application_id")[0][0] == APPLICATION_ID == 0x53465331
    assert {r[0] for r in _raw(path, "SELECT name FROM sqlite_master WHERE type = 'table'")} - {"sqlite_sequence"} == TABLES
    indexes = {r[0] for r in _raw(path, "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}
    assert indexes == INDEXES
    assert not os.path.exists(db.backup_path(0))  # yeni dosyada kopyalanacak bir şey yok


def test_connection_settings(db):
    conn = db.connection()
    assert db.journal_mode == "wal"
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL: state.db'yi hiçbir şey onaramaz
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.execute("PRAGMA temp_store").fetchone()[0] == 2  # MEMORY
    assert conn.execute("PRAGMA journal_size_limit").fetchone()[0] == 64 * 1024 * 1024
    assert conn.in_transaction is False


def test_wal_fallback_uses_delete_mode_and_warns(tmp_path, caplog, monkeypatch):
    # Ağ dosya sistemi: WAL isteği "wal" dışında bir şey döndürür
    monkeypatch.setattr(StateDb, "_request_wal", lambda self, conn: "delete")
    with caplog.at_level(logging.WARNING, logger="Store"):
        state = StateDb(tmp_path / "state.db")
        try:
            assert state.journal_mode == "delete"
            assert state.connection().execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            state.meta_set("k", "v")
            assert state.meta_get("k") == "v"
            second = threading.Thread(target=state.connection)  # ikinci bağlantı yeniden uyarmaz
            second.start()
            second.join(5)
        finally:
            state.close()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "WAL" in r.getMessage()]
    assert len(warnings) == 1


def test_old_sqlite_is_refused():
    state_mod.check_sqlite_version()
    state_mod.check_sqlite_version((3, 24, 0))
    with pytest.raises(StoreError, match="3.24"):
        state_mod.check_sqlite_version((3, 23, 1))


def test_one_connection_per_thread_and_close_closes_all(db):
    main = db.connection()
    assert db.connection() is main
    seen: List[sqlite3.Connection] = []
    ready, release = threading.Event(), threading.Event()

    def worker() -> None:
        seen.append(db.connection())
        seen.append(db.connection())
        ready.set()
        release.wait(5)

    thread = threading.Thread(target=worker)
    thread.start()
    assert ready.wait(5)
    assert seen[0] is seen[1] and seen[0] is not main

    db.close()
    for conn in (main, seen[0]):
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
    with pytest.raises(StoreError, match="kapatılmış"):
        db.connection()
    release.set()
    thread.join(5)
    db.close()  # ikinci kez çağırmak zararsız


def test_a_finished_threads_connection_is_not_kept_alive(db):
    db.connection()
    ready, release = threading.Event(), threading.Event()

    def worker() -> None:
        db.connection()
        ready.set()
        release.wait(5)

    thread = threading.Thread(target=worker)
    thread.start()
    assert ready.wait(5)
    assert len(db._connections) == 2
    release.set()
    thread.join(5)
    gc.collect()
    # İş parçacığı bitince bağlantısı da gider (kapanır); depo onu yalnızca zayıf başvuruyla izler
    assert len(db._connections) == 1
    assert db.connection().execute("SELECT 1").fetchone()[0] == 1


def test_a_dropped_store_closes_its_connections_without_a_resource_warning(tmp_path):
    """Kapatılmadan bırakılan depo (döngüsel çöp dahil) ResourceWarning üretmez: bağlantı kendini kapatır."""
    state = StateDb(tmp_path / "state.db")
    conn = weakref.ref(state.connection())
    state.cycle = state  # type: ignore[attr-defined]
    del state
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        gc.collect()
    assert conn() is None


# --- yazma işlemleri ---------------------------------------------------------------------------

def test_write_commits_and_rolls_back(db):
    with db.write() as conn:
        assert conn.in_transaction
        conn.execute("INSERT INTO meta (key, value) VALUES ('a', '1')")
    assert db.meta_get("a") == "1"

    with pytest.raises(RuntimeError):
        with db.write() as conn:
            conn.execute("INSERT INTO meta (key, value) VALUES ('b', '2')")
            raise RuntimeError("boom")
    assert db.meta_get("b") is None
    assert db.connection().in_transaction is False


def test_nested_write_joins_the_outer_transaction(db):
    with pytest.raises(RuntimeError):
        with db.write():
            db.meta_set("inner", "1")  # kendi write()'ı dıştaki işleme katılır, erken COMMIT atmaz
            assert db.connection().in_transaction
            raise RuntimeError("boom")
    assert db.meta_get("inner") is None


def test_write_raises_store_busy_when_another_writer_holds_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "BUSY_TIMEOUT_MS", 50)
    state = StateDb(tmp_path / "state.db")
    other = sqlite3.connect(tmp_path / "state.db", isolation_level=None)
    try:
        other.execute("BEGIN IMMEDIATE")
        with pytest.raises(StoreBusy) as busy:
            state.meta_set("k", "v")
        assert isinstance(busy.value, StorageError) and busy.value.fatal is False
        assert busy.value.path == str(tmp_path / "state.db")
        assert state.meta_get("k") is None  # okuma, yazar varken de çalışır (WAL)
        other.execute("ROLLBACK")
        state.meta_set("k", "v")
        assert state.meta_get("k") == "v"
    finally:
        other.close()
        state.close()


def test_open_raises_store_busy_when_the_file_stays_locked(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "BUSY_TIMEOUT_MS", 50)
    path = tmp_path / "state.db"
    other = sqlite3.connect(path, isolation_level=None)
    try:
        other.execute("CREATE TABLE t (x)")  # geri alma günlüğü kipinde bir dosya
        other.execute("BEGIN EXCLUSIVE")
        with pytest.raises(StoreBusy):
            StateDb(path)
    finally:
        other.close()


def test_wal_request_waits_for_a_lock_and_then_gives_up(db, monkeypatch):
    """Kip değişimi kilidi beklemeden "locked" verir: bekleme _request_wal'dadır, süre dolunca StoreBusy."""

    class Locked:
        def __init__(self, failures: int, error: Exception) -> None:
            self.failures, self.error, self.calls = failures, error, 0

        def execute(self, sql: str):
            self.calls += 1
            if self.calls <= self.failures:
                raise self.error
            return db.connection().execute("SELECT 'wal'")

    monkeypatch.setattr(state_mod, "_WAL_RETRY_PAUSE", 0.001)
    briefly = Locked(3, sqlite3.OperationalError("database is locked"))
    assert db._request_wal(briefly) == "wal" and briefly.calls == 4

    monkeypatch.setattr(state_mod, "BUSY_TIMEOUT_MS", 20)
    forever = Locked(10**9, sqlite3.OperationalError("database is locked"))
    with pytest.raises(StoreBusy):
        db._request_wal(forever)
    assert forever.calls > 1

    broken = Locked(1, sqlite3.OperationalError("disk I/O error"))
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        db._request_wal(broken)
    assert broken.calls == 1
    assert state_mod.is_busy_error(ValueError("database is locked")) is False


def test_meta_set_overwrites(db):
    assert db.meta_get("stream_id") is None
    db.meta_set("stream_id", "one")
    db.meta_set("stream_id", "two")
    assert db.meta_get("stream_id") == "two"


# --- geçiş çalıştırıcısı -----------------------------------------------------------------------

def test_reopening_a_current_file_changes_nothing(tmp_path):
    path = tmp_path / "state.db"
    first = StateDb(path)
    first.meta_set("k", "v")
    first.close()
    second = StateDb(path)
    try:
        assert second.schema_version == 1 and second.meta_get("k") == "v"
    finally:
        second.close()
    assert [n for n in os.listdir(tmp_path) if ".bak-v" in n] == []


def test_dummy_second_migration_upgrades_and_keeps_a_copy(tmp_path):
    path = tmp_path / "state.db"
    v1 = StateDb(path)
    v1.meta_set("kept", "yes")
    v1.close()

    migrations = _migrations_with(tmp_path, {
        "0002_dummy.sql": (
            "-- sahte ikinci geçiş\n"
            "ALTER TABLE jobs ADD COLUMN origin_json TEXT;\n"
            "CREATE TABLE IF NOT EXISTS dummy (id INTEGER PRIMARY KEY, note TEXT NOT NULL DEFAULT 'a;b');\n"
            "INSERT INTO dummy (note) VALUES ('x;y');\n"
        ),
    })
    v2 = StateDb(path, migrations_dir=migrations)
    try:
        assert v2.latest_version == 2 and v2.schema_version == 2
        assert v2.meta_get("kept") == "yes"
        assert "origin_json" in [r[1] for r in v2.connection().execute("PRAGMA table_info(jobs)")]
        assert [r[0] for r in v2.connection().execute("SELECT note FROM dummy")] == ["x;y"]
        backup = v2.backup_path(1)
    finally:
        v2.close()

    # Kopya, geçişten önceki dosyadır: sürüm 1, veriler yerinde, yeni tablo ve sütun yok
    assert backup == f"{path}.bak-v1" and os.path.isfile(backup)
    assert _raw(backup, "PRAGMA user_version")[0][0] == 1
    assert _raw(backup, "SELECT value FROM meta WHERE key = 'kept'") == [("yes",)]
    assert _raw(backup, "SELECT name FROM sqlite_master WHERE name = 'dummy'") == []
    assert [n for n in os.listdir(tmp_path) if n.endswith(".tmp")] == []

    # Yeniden açmak geçişi yinelemez (INSERT bir kez çalıştı)
    again = StateDb(path, migrations_dir=migrations)
    try:
        assert again.connection().execute("SELECT COUNT(*) FROM dummy").fetchone()[0] == 1
    finally:
        again.close()


def test_copy_taken_after_someone_else_migrated_is_discarded(db, tmp_path):
    """Kopya yalnızca gerçekten eski sürümdeyse yerine konur (iki süreç aynı anda yükseltirken)."""
    db._backup(db.connection(), 0)  # dosya sürüm 1: "sürüm 0'ın kopyası" olamaz
    assert [n for n in os.listdir(tmp_path) if ".bak-v" in n] == []
    db._backup(db.connection(), 1)
    assert [n for n in os.listdir(tmp_path) if ".bak-v" in n] == ["state.db.bak-v1"]


def test_new_file_runs_every_migration_without_a_copy(tmp_path):
    migrations = _migrations_with(tmp_path, {"0002_dummy.sql": "CREATE TABLE dummy (id INTEGER PRIMARY KEY);"})
    state = StateDb(tmp_path / "state.db", migrations_dir=migrations)
    try:
        assert state.schema_version == 2
    finally:
        state.close()
    assert [n for n in os.listdir(tmp_path) if ".bak-v" in n] == []


def test_failing_migration_rolls_back_and_names_the_script(tmp_path):
    path = tmp_path / "state.db"
    v1 = StateDb(path)
    v1.meta_set("kept", "yes")
    v1.close()

    broken = _migrations_with(tmp_path, {
        "0002_broken.sql": (
            "CREATE TABLE half_done (id INTEGER PRIMARY KEY);\n"
            "INSERT INTO meta (key, value) VALUES ('from_0002', '1');\n"
            "ALTER TABLE no_such_table ADD COLUMN x TEXT;\n"
        ),
    })
    with pytest.raises(StoreError) as failed:
        StateDb(path, migrations_dir=broken)
    assert "0002_broken" in str(failed.value)
    assert failed.value.path == os.path.join(broken, "0002_broken.sql")
    assert "no_such_table" in failed.value.detail
    assert not isinstance(failed.value, SchemaTooNew)

    # Hiçbir şey yarım kalmadı: sürüm 1, betiğin ilk iki deyimi de geri alındı
    assert _raw(path, "PRAGMA user_version")[0][0] == 1
    assert _raw(path, "SELECT name FROM sqlite_master WHERE name = 'half_done'") == []
    assert _raw(path, "SELECT key, value FROM meta") == [("kept", "yes")]
    assert os.path.isfile(f"{path}.bak-v1")  # kopya geçişten önce alındı

    # Eski kod dosyayı kullanmaya devam eder; düzeltilmiş betikle geçiş tamamlanır
    still_v1 = StateDb(path)
    still_v1.meta_set("after_failure", "1")
    still_v1.close()
    fixed = _migrations_with(tmp_path, {"0002_fixed.sql": "CREATE TABLE half_done (id INTEGER PRIMARY KEY);"})
    v2 = StateDb(path, migrations_dir=fixed)
    try:
        assert v2.schema_version == 2 and v2.meta_get("after_failure") == "1"
    finally:
        v2.close()
    # Sürüm başına tek kopya: ikinci denemenin kopyası ilkinin yerini aldı
    assert _raw(f"{path}.bak-v1", "SELECT value FROM meta WHERE key = 'after_failure'") == [("1",)]


def test_failing_first_migration_leaves_an_empty_file(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_initial.sql").write_text(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);\nCREATE TABLE meta (again TEXT);\n",
        encoding="utf-8",
    )
    path = tmp_path / "state.db"
    with pytest.raises(StoreError, match="0001_initial"):
        StateDb(path, migrations_dir=migrations)
    assert _raw(path, "PRAGMA user_version")[0][0] == 0
    assert _raw(path, "PRAGMA application_id")[0][0] == 0
    assert _raw(path, "SELECT COUNT(*) FROM sqlite_master")[0][0] == 0
    # Dosya hâlâ "boş" sayılır: gerçek geçişlerle açılabilir
    StateDb(path).close()
    assert _raw(path, "PRAGMA user_version")[0][0] == 1


def test_two_statements_without_separator_fail_as_one_migration_error(tmp_path):
    migrations = _migrations_with(tmp_path, {"0002_bad.sql": "CREATE TABLE a (x TEXT)\nCREATE TABLE b (y TEXT);\n"})
    with pytest.raises(StoreError, match="0002_bad"):
        StateDb(tmp_path / "state.db", migrations_dir=migrations)
    assert _raw(tmp_path / "state.db", "PRAGMA user_version")[0][0] == 1  # 0001 kendi işleminde kaydedildi


def test_migration_is_safe_to_run_again_after_its_statements_were_applied(tmp_path):
    """Betik çalışmış ama sürüm yükselmemiş bir dosya (bölüm 7.3): ADD COLUMN ve CREATE yeniden hata vermez."""
    path = tmp_path / "state.db"
    StateDb(path).close()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE jobs ADD COLUMN origin_json TEXT")
    conn.execute("CREATE TABLE job_events (job_id TEXT NOT NULL, seq INTEGER NOT NULL, PRIMARY KEY (job_id, seq))")
    conn.commit()
    conn.close()

    migrations = _migrations_with(tmp_path, {
        "0002_job_manager.sql": (
            "ALTER TABLE jobs ADD COLUMN origin_json TEXT;       -- zaten var: atlanır\n"
            "alter table \"jobs\" add heartbeat_at INTEGER;\n"
            "CREATE TABLE IF NOT EXISTS job_events (job_id TEXT NOT NULL, seq INTEGER NOT NULL, PRIMARY KEY (job_id, seq));\n"
        ),
    })
    state = StateDb(path, migrations_dir=migrations)
    try:
        columns = [r[1] for r in state.connection().execute("PRAGMA table_info(jobs)")]
        assert columns.count("origin_json") == 1 and "heartbeat_at" in columns
        assert state.schema_version == 2
    finally:
        state.close()


def test_newer_file_raises_schema_too_new_and_is_not_touched(tmp_path):
    path = tmp_path / "state.db"
    newer = _migrations_with(tmp_path, {"0002_future.sql": "CREATE TABLE future (id INTEGER PRIMARY KEY);"})
    StateDb(path, migrations_dir=newer).close()
    before = _sha256(path)

    with pytest.raises(SchemaTooNew) as too_new:
        StateDb(path)
    error = too_new.value
    assert (error.component, error.found, error.supported) == ("state", 2, 1)
    assert error.path == str(path) and isinstance(error, StoreError)
    assert _sha256(path) == before
    assert [n for n in os.listdir(tmp_path) if ".bak-v" in n] == []


def test_foreign_database_is_refused_and_not_modified(tmp_path):
    """Yanlışlıkla verilen başka bir SQLite dosyası (ör. 2.x jobs.db) state.db'ye çevrilmez."""
    path = _make_legacy_db(tmp_path / "data" / ".meta" / "jobs.db")
    before = _sha256(path)
    with pytest.raises(StoreError, match="state.db değil"):
        StateDb(path)
    assert _sha256(path) == before
    assert sorted(os.listdir(os.path.dirname(path))) == ["jobs.db"]  # -wal / -shm de oluşmadı

    catalog = tmp_path / "catalog.db"
    conn = sqlite3.connect(catalog)
    conn.execute("PRAGMA application_id = 0x53464331")
    conn.commit()
    conn.close()
    with pytest.raises(StoreError, match="state.db değil"):
        StateDb(catalog)


def test_garbage_file_raises_a_sqlite_error(tmp_path):
    path = tmp_path / "state.db"
    path.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        StateDb(path)
    assert path.read_bytes() == b"this is not sqlite" * 100


def test_concurrent_first_opens_all_succeed(tmp_path):
    path = tmp_path / "state.db"
    errors: List[BaseException] = []
    opened: List[StateDb] = []
    start = threading.Barrier(6)

    def open_it() -> None:
        try:
            start.wait(5)
            opened.append(StateDb(path))
        except BaseException as e:  # noqa: BLE001 - test iş parçacığı: hatayı ana iş parçacığına taşı
            errors.append(e)

    threads = [threading.Thread(target=open_it) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    try:
        assert errors == []
        assert len(opened) == 6
        assert _raw(path, "PRAGMA user_version")[0][0] == 1
    finally:
        for state in opened:
            state.close()


def test_shipped_migrations_are_numbered_without_gaps():
    migrations = load_migrations()
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))
    assert migrations[0].name == "0001_initial"
    assert all(m.statements() for m in migrations)


def test_load_migrations_rejects_gaps_and_ignores_other_files(tmp_path):
    (tmp_path / "0001_first.sql").write_text("CREATE TABLE a (x);", encoding="utf-8")
    (tmp_path / "README.md").write_text("not a migration", encoding="utf-8")
    (tmp_path / "0002_Second.sql").write_text("büyük harf: kalıba uymaz", encoding="utf-8")
    assert [(m.version, m.name) for m in load_migrations(tmp_path)] == [(1, "0001_first")]

    (tmp_path / "0003_third.sql").write_text("CREATE TABLE c (x);", encoding="utf-8")
    with pytest.raises(StoreError, match="0002"):
        load_migrations(tmp_path)
    with pytest.raises(StoreError):
        load_migrations(tmp_path / "missing")


def test_split_statements():
    script = (
        "-- baş yorum; noktalı virgüllü\n"
        "CREATE TABLE a (x TEXT DEFAULT 'p;q');  -- satır sonu yorumu\n"
        "/* blok; yorum */\n"
        "CREATE TRIGGER t AFTER INSERT ON a BEGIN\n  UPDATE a SET x = 'r;s';\n  DELETE FROM a WHERE x IS NULL;\nEND;\n"
        ";\n"
        "INSERT INTO a VALUES ('z');\n"
        "-- son yorum\n"
    )
    statements = split_statements(script)
    assert len(statements) == 3
    assert "CREATE TABLE a" in statements[0] and "'p;q'" in statements[0]
    assert "CREATE TRIGGER t" in statements[1] and statements[1].endswith("IS NULL;\nEND;")
    assert statements[2].endswith("INSERT INTO a VALUES ('z');")

    conn = sqlite3.connect(":memory:")
    for statement in statements:
        conn.execute(statement)
    assert conn.execute("SELECT x FROM a").fetchall() == [("r;s",)]
    conn.close()

    assert split_statements("") == [] and split_statements("-- yalnızca yorum\n") == []
    with pytest.raises(ValueError):
        split_statements("CREATE TABLE a (x);\nCREATE TABLE b (y)")


# --- RuntimeFacts ------------------------------------------------------------------------------

def test_runtime_facts_round_trip(db, monkeypatch):
    facts = RuntimeFacts(db)
    assert facts.get("bridge_health") is None

    monkeypatch.setattr(state_mod.time, "time", lambda: 1_790_000_000.9)
    facts.set("bridge_health", {"state": "ok", "ad": "köprü", "nested": {"n": [1, 2]}})
    fact = facts.get("bridge_health")
    assert fact is not None
    assert fact.value == {"state": "ok", "ad": "köprü", "nested": {"n": [1, 2]}}
    assert fact.pid == os.getpid()
    assert fact.updated_at == 1_790_000_000.0 and isinstance(fact.updated_at, float)

    monkeypatch.setattr(state_mod.time, "time", lambda: 1_790_000_060.0)
    facts.set("bridge_health", {"state": "blocked"})
    fact = facts.get("bridge_health")
    assert fact is not None and fact.value == {"state": "blocked"} and fact.updated_at == 1_790_000_060.0
    assert db.connection().execute("SELECT COUNT(*) FROM runtime").fetchone()[0] == 1


def test_runtime_facts_reject_values_that_are_not_json(db):
    facts = RuntimeFacts(db)
    with pytest.raises(StoreError) as bad:
        facts.set("k", {"x": object()})
    assert bad.value.fatal is False
    assert facts.get("k") is None


def test_runtime_facts_ignore_an_unreadable_row(db):
    with db.write() as conn:
        conn.execute("INSERT INTO runtime (key, value_json, pid, updated_at) VALUES ('bad', '{not json', NULL, 1)")
        conn.execute("INSERT INTO runtime (key, value_json, pid, updated_at) VALUES ('list', '[1]', NULL, 1)")
    facts = RuntimeFacts(db)
    assert facts.get("bad") is None and facts.get("list") is None
    facts.set("bad", {"ok": True})
    assert facts.get("bad").value == {"ok": True}


def test_runtime_facts_are_visible_to_another_process(db, tmp_path):
    """Başka bir süreç aynı dosyayı açar (geçiş yinelenmez), bir bilgi yazar; bu süreç onu pid'iyle görür."""
    RuntimeFacts(db).set("parent", {"n": 1})
    script = (
        "import sys\n"
        "from src.store.state import RuntimeFacts, StateDb\n"
        "state = StateDb(sys.argv[1])\n"
        "facts = RuntimeFacts(state)\n"
        "assert facts.get('parent').value == {'n': 1}\n"
        "facts.set('child', {'hello': 'world'})\n"
        "state.close()\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "state.db")],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr
    fact = RuntimeFacts(db).get("child")
    assert fact is not None and fact.value == {"hello": "world"}
    assert fact.pid not in (None, os.getpid())
