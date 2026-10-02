"""
src/store/catalog.py ve src/store/schema/catalog.sql: katalog şeması, bağlantılar, sürüm denetimi,
BEGIN IMMEDIATE yardımcısı (docs/design/01-storage.md, bölüm 3.2, 3.3, 6.2, 6.3, 7.2).

Ağ yok. İki süreçli testler çocuğu `subprocess` ile başlatır ve boru üzerinden satır satır konuşur;
Linux, macOS ve Windows'ta aynı biçimde çalışır.
"""
from __future__ import annotations

import errno
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import store_fixtures as sf
from src.exceptions import StorageError
from src.store import CatalogCorrupt, StoreBusy, StoreError, catalog, derive, layout
from src.store.catalog import Catalog

ROOT = Path(__file__).resolve().parent.parent
DESIGN = ROOT / "docs" / "design" / "01-storage.md"
STATUS_FIXTURES = sorted((Path(__file__).parent / "fixtures" / "status").glob("*/*.json"))

TABLES = ["meta", "sports", "categories", "tournaments", "seasons", "participants", "players", "events",
          "event_participants", "event_slices", "entity_slices", "slice_history", "changes", "pending_writes",
          "legacy_roots"]
INDEXES = {
    "tournaments_sport", "seasons_tournament", "participants_name", "players_name",
    "events_tournament_season", "events_sport_start", "events_start", "events_season_round", "events_live",
    "events_unsettled", "events_unobserved", "events_legacy", "events_stale", "events_open", "events_updated",
    "event_participants_event", "event_slices_not_ok", "changes_event", "changes_ts",
}
# `events` tablosunun yükten türetilmeyen sütunları: manifestten, dosya konumundan ve listelerden gelir
EVENT_STORAGE_COLUMNS = {"status_regressed", "stale", "listed_in", "layout", "path", "legacy_path", "sig",
                         "first_seen_at", "updated_at"}


@pytest.fixture
def cat(tmp_path):
    with Catalog(catalog.catalog_path(tmp_path)) as c:
        c.prepare()
        yield c


def _event(event_id: int, **columns) -> dict:
    return {"id": event_id, "status_class": "completed", "row_source": "event", "first_seen_at": 1,
            "updated_at": 1, **columns}


def _slice(event_id: int, key: str = "statistics", **columns) -> dict:
    return {"event_id": event_id, "key": key, "state": "ok", "has_payload": 1, **columns}


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _pragma(conn: sqlite3.Connection, name: str):
    return conn.execute(f"PRAGMA {name}").fetchone()[0]


# --- şema -------------------------------------------------------------------------------------------

def _normalise_ddl(sql: str) -> list[str]:
    """Yorumlar ve boşluk farkları atılmış deyimler (belgedeki blok ile dosya böyle karşılaştırılır)."""
    without_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [re.sub(r"\s+", " ", s).strip() for s in without_comments.split(";") if s.strip()]


def test_schema_file_is_the_ddl_printed_in_the_design():
    """Bölüm 3.3'teki katalog DDL'i ile src/store/schema/catalog.sql aynı deyimlerden oluşur."""
    if not DESIGN.is_file():
        pytest.skip("tasarım belgesi bu dağıtımda yok")
    text = DESIGN.read_text(encoding="utf-8")
    start = text.index("```sql", text.index("### 3.3 DDL")) + len("```sql")
    printed = text[start:text.index("```", start)]
    shipped = Path(catalog.SCHEMA_FILE).read_text(encoding="utf-8")

    assert _normalise_ddl(shipped) == _normalise_ddl(printed)
    assert len(_normalise_ddl(shipped)) == len(TABLES) + len(INDEXES) == len(catalog.schema_statements())


def test_prepare_creates_every_table_and_index(cat):
    conn = cat.connection()
    indexes = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'")}

    assert cat.tables() == TABLES
    assert indexes == INDEXES
    assert _pragma(conn, "application_id") == catalog.APPLICATION_ID == 0x53464331
    assert _pragma(conn, "user_version") == catalog.CATALOG_SCHEMA == 1
    assert cat.quick_check() == []
    assert cat.path.endswith(os.path.join(".meta", "catalog.db")) and os.path.isabs(cat.path)
    assert layout.CATALOG_DB == ".meta/catalog.db"


def test_event_columns_are_the_derived_ones_plus_the_storage_ones(cat):
    columns = [r[1] for r in cat.connection().execute("PRAGMA table_info(events)")]

    assert set(columns) == set(derive.EVENT_DERIVED_COLUMNS) | EVENT_STORAGE_COLUMNS
    assert [c for c in columns if c in derive.EVENT_DERIVED_COLUMNS] == list(derive.EVENT_DERIVED_COLUMNS)


def test_split_statements_ignores_semicolons_in_comments_and_strings():
    script = "-- baş; yorum 'tırnak'\nCREATE TABLE a (x TEXT DEFAULT ';'); -- son; yorum\n\nCREATE INDEX i\n  ON a(x);\n-- kuyruk\n"

    statements = catalog.split_statements(script)

    assert len(statements) == 2
    assert statements[0].endswith("-- son; yorum") and statements[1].startswith("CREATE INDEX i")
    with pytest.raises(StoreError):
        catalog.split_statements("CREATE TABLE a (x);\nCREATE TABLE b (")


# --- bağlantı ayarları (bölüm 3.2) --------------------------------------------------------------------

def test_every_connection_gets_the_documented_pragmas(cat):
    conn = cat.connection()

    assert _pragma(conn, "journal_mode") == "wal" == cat.journal_mode
    assert _pragma(conn, "busy_timeout") == 5000 == catalog.BUSY_TIMEOUT_MS
    assert _pragma(conn, "foreign_keys") == 1
    assert _pragma(conn, "temp_store") == 2  # MEMORY
    assert _pragma(conn, "journal_size_limit") == 67108864 == catalog.JOURNAL_SIZE_LIMIT
    assert _pragma(conn, "synchronous") == 1  # NORMAL
    assert conn.isolation_level is None and not conn.in_transaction
    assert conn.execute("SELECT 7 AS n").fetchone()["n"] == 7  # satırlar adla okunur


def test_configure_full_synchronous_is_for_the_state_db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "state.db"), isolation_level=None)
    try:
        assert catalog.configure(conn, synchronous="FULL", busy_timeout_ms=1234) == "wal"
        assert _pragma(conn, "synchronous") == 2
        assert _pragma(conn, "busy_timeout") == 1234
        with pytest.raises(ValueError):
            catalog.configure(conn, synchronous="OFF")
    finally:
        conn.close()


def test_wal_is_persistent_in_the_file(tmp_path):
    path = catalog.catalog_path(tmp_path)
    with Catalog(path) as c:
        c.prepare()
    plain = sqlite3.connect(path)
    try:
        assert _pragma(plain, "journal_mode") == "wal"
    finally:
        plain.close()


@pytest.mark.skipif(sys.platform == "win32", reason="unix-dotfile VFS yalnızca POSIX'te var")
def test_wal_fallback_on_a_file_system_without_shared_memory(tmp_path):
    """unix-dotfile VFS paylaşılan belleği desteklemez (ağ dosya sistemi gibi): WAL açılamaz, DELETE kalır."""
    try:
        conn = sqlite3.connect(f"file:{tmp_path / 'nfs.db'}?vfs=unix-dotfile", uri=True, isolation_level=None)
    except sqlite3.Error:
        pytest.skip("bu SQLite derlemesinde unix-dotfile VFS yok")
    try:
        assert catalog.configure(conn) == "delete"
        assert _pragma(conn, "journal_mode") == "delete"
        assert _pragma(conn, "foreign_keys") == 1 and _pragma(conn, "synchronous") == 1  # diğerleri yine uygulanır
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
        assert conn.execute("SELECT x FROM t").fetchone()[0] == 1
    finally:
        conn.close()


def test_catalog_reports_the_fallback_and_warns_once(tmp_path, monkeypatch, caplog):
    def no_wal(conn, **kwargs):  # WAL'a geçemeyen dosya sistemi: kip "delete" kalır
        conn.execute("PRAGMA busy_timeout = 5000")
        return "delete"

    monkeypatch.setattr(catalog, "configure", no_wal)
    with caplog.at_level(logging.WARNING, logger="src.store.catalog"), Catalog(catalog.catalog_path(tmp_path)) as c:
        c.prepare()
        assert c.journal_mode == "delete"
        thread = threading.Thread(target=c.connection)  # ikinci bağlantı: uyarı yinelenmez
        thread.start()
        thread.join()
        with c.write():
            c.upsert("events", [_event(1)])
        assert _count(c.connection(), "events") == 1

    warnings = [r for r in caplog.records if "WAL" in r.getMessage()]
    assert len(warnings) == 1 and c.path in warnings[0].getMessage()


def test_minimum_sqlite_version_is_checked(monkeypatch, tmp_path):
    catalog.check_sqlite_version((3, 24, 0))
    catalog.check_sqlite_version((3, 53, 4))
    catalog.check_sqlite_version()
    for old in ((3, 23, 1), (3, 8, 7), (2, 99, 0)):
        with pytest.raises(StoreError, match="3.24"):
            catalog.check_sqlite_version(old)
    assert catalog.MIN_SQLITE == (3, 24, 0)

    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 22, 0))
    with pytest.raises(StoreError, match="3.22.0"):
        Catalog(catalog.catalog_path(tmp_path))
    assert not os.path.exists(catalog.catalog_path(tmp_path))


# --- sürümler: user_version ve derive_version (bölüm 7.2) ---------------------------------------------

def test_missing_catalog_is_created_and_asks_to_be_built(tmp_path):
    path = catalog.catalog_path(tmp_path)
    with Catalog(path) as c:
        assert c.inspect() == catalog.CatalogState(exists=False, rebuild_reason="missing")
        assert not os.path.exists(path)  # inspect dosya yaratmaz

        found = c.prepare()

        assert found.rebuild_reason == catalog.REBUILD_MISSING and not found.usable and not found.schema_ok
        # Şema var ama satırlar henüz kurulmadı: yarıda kalan kurulum kendini kullanılabilir göstermez
        state = c.inspect()
        assert (state.exists, state.application_id, state.schema_version, state.derive_version) == (
            True, catalog.APPLICATION_ID, 1, None)
        assert state.rebuild_reason == catalog.REBUILD_DERIVE and state.schema_ok and not state.usable

        with c.write():
            c.stamp_derive_version()

        assert c.prepare() == catalog.CatalogState(
            exists=True, application_id=catalog.APPLICATION_ID, schema_version=catalog.CATALOG_SCHEMA,
            derive_version=catalog.DERIVE_VERSION, rebuild_reason=None)
        assert c.prepare().usable and c.get_meta("derive_version") == str(derive.DERIVE_VERSION)
    assert catalog.DERIVE_VERSION is derive.DERIVE_VERSION


def test_prepare_without_create_leaves_the_directory_alone(tmp_path):
    path = catalog.catalog_path(tmp_path)
    with Catalog(path) as c:
        assert c.prepare(create=False).rebuild_reason == "missing"
    assert not os.path.exists(os.path.dirname(path))


def test_empty_file_counts_as_missing(tmp_path):
    path = catalog.catalog_path(tmp_path)
    os.makedirs(os.path.dirname(path))
    open(path, "wb").close()
    with Catalog(path) as c:
        assert c.prepare() == catalog.CatalogState(exists=True, application_id=0, schema_version=0,
                                                   rebuild_reason="missing")
        assert c.tables() == TABLES


def _built_catalog(tmp_path) -> str:
    path = catalog.catalog_path(tmp_path)
    with Catalog(path) as c:
        c.prepare()
        with c.write():
            c.upsert("events", [_event(1)])
            c.stamp_derive_version()
    return path


def _edit(path: str, *statements: str) -> None:
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        for statement in statements:
            conn.execute(statement)
    finally:
        conn.close()


@pytest.mark.parametrize("statement, reason, schema_ok", [
    ("PRAGMA user_version = 0", "schema_version", False),
    ("PRAGMA user_version = 2", "schema_version", False),  # daha yeni dosya da yeniden kurulur: sürüm düşürmek güvenli
    ("PRAGMA application_id = 0", "application_id", False),
    (f"PRAGMA application_id = {0x53465331}", "application_id", False),  # state.db'nin imzası
    ("UPDATE meta SET value = '0' WHERE key = 'derive_version'", "derive_version", True),
    (f"UPDATE meta SET value = '{derive.DERIVE_VERSION + 1}' WHERE key = 'derive_version'", "derive_version", True),
    ("UPDATE meta SET value = 'x' WHERE key = 'derive_version'", "derive_version", True),
    ("DELETE FROM meta", "derive_version", True),
], ids=["older-schema", "newer-schema", "no-app-id", "state-db", "older-derive", "newer-derive", "bad-derive",
        "never-built"])
def test_version_mismatch_asks_for_a_rebuild_and_touches_nothing(tmp_path, statement, reason, schema_ok):
    path = _built_catalog(tmp_path)
    _edit(path, statement)
    before = Path(path).read_bytes()

    with Catalog(path) as c:
        state = c.prepare()

    assert (state.rebuild_reason, state.schema_ok, state.usable, state.exists) == (reason, schema_ok, False, True)
    assert Path(path).read_bytes() == before  # yeniden kurma dizinleyicinin işi; burada dosyaya dokunulmaz


def test_foreign_and_corrupt_files(tmp_path):
    path = catalog.catalog_path(tmp_path)
    os.makedirs(os.path.dirname(path))
    _edit(path, "CREATE TABLE notes (x)")  # imzasız, başka bir SQLite dosyası
    with Catalog(path) as c:
        assert c.prepare().rebuild_reason == catalog.REBUILD_APPLICATION_ID
    plain = sqlite3.connect(path)
    try:
        assert _pragma(plain, "journal_mode") == "delete"  # inspect yabancı dosyanın kipini değiştirmedi
    finally:
        plain.close()
    with Catalog(path) as c:
        assert c.create_schema() is False  # boş olmayan dosyaya şema yazılmaz
        assert c.tables() == ["notes"]

    Path(path).write_bytes(b"this is not a database, " * 200)
    with Catalog(path) as c:
        state = c.prepare()
        assert state.rebuild_reason == catalog.REBUILD_CORRUPT and state.detail and not state.schema_ok
        with pytest.raises(CatalogCorrupt) as caught:
            c.connection()
        assert caught.value.path == path and isinstance(caught.value, StorageError) and not caught.value.fatal
        assert c.quick_check() != []


def _race(path: str, action, count: int = 6) -> list:
    """`count` iş parçacığı, her biri kendi Catalog nesnesiyle, aynı anda `action(catalog)` çağırır."""
    results: list = []
    barrier = threading.Barrier(count)

    def run():
        with Catalog(path) as c:
            barrier.wait()
            try:
                results.append(action(c))
            except Exception as exc:  # hata da bir sonuçtur, aşağıda denetlenir
                results.append(exc)

    threads = [threading.Thread(target=run) for _ in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_schema_creation_is_atomic_and_happens_once(tmp_path):
    path = catalog.catalog_path(tmp_path)

    results = _race(path, lambda c: c.create_schema(), count=4)

    assert sorted(results) == [False, False, False, True]
    with Catalog(path) as c:
        assert c.tables() == TABLES and c.inspect().schema_version == 1


@pytest.mark.parametrize("round_no", range(10))
def test_new_data_directory_opened_by_several_at_once(tmp_path, round_no):
    """
    Yeni bir veri dizinini aynı anda açanlar: hiçbiri hata almaz (WAL'a geçiş yarışında SQLite beklemeden
    SQLITE_BUSY döner; `configure` yeniden dener) ve hiçbiri yarım şema görmez ("missing" ya da tam şema).
    """
    path = catalog.catalog_path(tmp_path)

    results = _race(path, lambda c: c.prepare().rebuild_reason)

    assert set(results) <= {catalog.REBUILD_MISSING, catalog.REBUILD_DERIVE}, results
    assert catalog.REBUILD_MISSING in results
    with Catalog(path) as c:
        assert c.tables() == TABLES and c.journal_mode == "wal"


class _BusyThenOk:
    """`PRAGMA journal_mode` çağrısında `failures` kez SQLITE_BUSY veren sahte bağlantı."""

    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0

    def execute(self, sql: str):
        self.calls += 1
        if self.calls <= self.failures:
            raise sqlite3.OperationalError("database is locked")
        return self

    def fetchone(self):
        return ("wal",)


def test_journal_mode_switch_is_retried_while_busy(monkeypatch):
    monkeypatch.setattr(catalog.time, "sleep", lambda seconds: None)
    conn = _BusyThenOk(failures=3)

    assert catalog._set_journal_mode(conn, "WAL", 5000) == "wal"
    assert conn.calls == 4

    stuck = _BusyThenOk(failures=10 ** 9)
    started = time.monotonic()
    with pytest.raises(sqlite3.OperationalError):
        catalog._set_journal_mode(stuck, "WAL", 50)  # süre sınırı busy_timeout'tur
    assert time.monotonic() - started < 5 and stuck.calls > 1

    class Broken(_BusyThenOk):
        def execute(self, sql: str):
            raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        catalog._set_journal_mode(Broken(0), "WAL", 5000)  # meşguliyet dışındaki hata yeniden denenmez


def test_schema_creation_rolls_back_as_a_whole(tmp_path, monkeypatch):
    statements = catalog.schema_statements()
    monkeypatch.setattr(catalog, "schema_statements", lambda: statements[:5] + ("CREATE TABLE broken (",))
    with Catalog(catalog.catalog_path(tmp_path)) as c:
        with pytest.raises(StoreError):
            c.prepare()
        assert c.tables() == []
        assert c.inspect().rebuild_reason == "missing"  # yarım şema kalmadı
        monkeypatch.undo()
        assert c.prepare().rebuild_reason == "missing" and c.tables() == TABLES


# --- iş parçacığı başına bağlantı ---------------------------------------------------------------------

def test_one_connection_per_thread(cat):
    seen: dict[str, sqlite3.Connection] = {}

    def worker(name: str):
        seen[name] = cat.connection()
        assert cat.connection() is seen[name]
        with cat.write():
            cat.upsert("events", [_event(len(seen) + 100)])

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
        t.join()

    main = cat.connection()
    assert cat.connection() is main
    assert len({id(main), id(seen["a"]), id(seen["b"])}) == 3
    assert _count(main, "events") == 2  # başka iş parçacıklarının yazdıkları görünür


def test_connections_of_finished_threads_are_closed_when_another_one_opens(cat):
    seen: list[sqlite3.Connection] = []
    first = threading.Thread(target=lambda: seen.append(cat.connection()))
    first.start()
    first.join()
    assert seen[0].execute("SELECT 1").fetchone()[0] == 1  # henüz açık

    second = threading.Thread(target=lambda: seen.append(cat.connection()))
    second.start()
    second.join()

    with pytest.raises(sqlite3.ProgrammingError):
        seen[0].execute("SELECT 1")
    assert cat.connection().execute("SELECT 1").fetchone()[0] == 1


def test_close_closes_every_thread_and_later_use_reopens(cat):
    main = cat.connection()
    other: list[sqlite3.Connection] = []
    thread = threading.Thread(target=lambda: other.append(cat.connection()))
    thread.start()
    thread.join()

    cat.close()

    for conn in (main, other[0]):
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
    again = cat.connection()
    assert again is not main and _pragma(again, "foreign_keys") == 1 and cat.tables() == TABLES


# --- yazma işlemi: BEGIN IMMEDIATE --------------------------------------------------------------------

def test_write_commits_and_rolls_back(cat):
    with cat.write() as conn:
        assert conn is cat.connection() and conn.in_transaction
        cat.upsert("events", [_event(1)])
    assert not cat.connection().in_transaction and _count(cat.connection(), "events") == 1

    with pytest.raises(RuntimeError, match="boom"):
        with cat.write():
            cat.upsert("events", [_event(2)])
            assert _count(cat.connection(), "events") == 2
            raise RuntimeError("boom")

    assert not cat.connection().in_transaction and _count(cat.connection(), "events") == 1
    with cat.write():  # hatadan sonra yeni işlem açılabiliyor
        cat.upsert("events", [_event(3)])
    assert _count(cat.connection(), "events") == 2


def test_write_starts_with_begin_immediate(cat):
    statements: list[str] = []
    cat.connection().set_trace_callback(statements.append)
    with cat.write():
        pass
    cat.connection().set_trace_callback(None)

    assert statements == ["BEGIN IMMEDIATE", "COMMIT"]


def test_nested_write_is_a_savepoint(cat):
    with cat.write():
        cat.upsert("events", [_event(1)])
        with pytest.raises(RuntimeError):
            with cat.write():
                cat.upsert("events", [_event(2)])
                raise RuntimeError("inner")
        assert _count(cat.connection(), "events") == 1  # yalnızca içteki blok geri alındı
        with cat.write():
            cat.upsert("events", [_event(3)])
            with cat.write():
                cat.upsert("events", [_event(4)])

    assert [r[0] for r in cat.connection().execute("SELECT id FROM events ORDER BY id")] == [1, 3, 4]


def test_sqlite_errors_inside_a_write_become_store_errors(cat):
    with pytest.raises(StoreError) as caught:
        with cat.write() as conn:
            cat.upsert("events", [_event(1)])
            conn.execute("INSERT INTO events (id) VALUES (2)")  # NOT NULL sütunlar eksik

    assert isinstance(caught.value.__cause__, sqlite3.IntegrityError)
    assert not caught.value.fatal and caught.value.path == cat.path
    assert _count(cat.connection(), "events") == 0 and not cat.connection().in_transaction


def test_write_cannot_start_inside_a_read_transaction(cat):
    with cat.read():
        with pytest.raises(StoreError, match="okuma"):
            with cat.write():
                pass  # pragma: no cover
    with cat.write():
        with cat.read() as conn:  # yazma işleminin içindeki okuma ona katılır
            assert conn.in_transaction
        assert cat.connection().in_transaction
        cat.upsert("events", [_event(1)])
    assert _count(cat.connection(), "events") == 1


def test_second_writer_gets_store_busy_after_the_timeout(tmp_path, cat):
    with Catalog(cat.path, busy_timeout_ms=150) as other:
        with cat.write():
            cat.upsert("events", [_event(1)])
            started = time.monotonic()
            with pytest.raises(StoreBusy) as caught:
                with other.write():
                    pass  # pragma: no cover
            waited = time.monotonic() - started
            # Okuyucu beklemez ve yazarın henüz bitirmediği satırı görmez
            assert _count(other.connection(), "events") == 0

        assert waited < 5.0  # 5 sn'lik varsayılan değil, bu bağlantının 150 ms'lik süresi geçerli
        assert isinstance(caught.value, StorageError) and not caught.value.fatal and caught.value.path == cat.path
        with other.write():  # kilit bırakılınca yazabiliyor
            other.upsert("events", [_event(2)])
        assert _count(cat.connection(), "events") == 2


def test_writer_waits_for_a_lock_that_is_released_in_time(cat):
    with Catalog(cat.path, busy_timeout_ms=5000) as other:
        entered = threading.Event()

        def hold():
            with cat.write():
                cat.upsert("events", [_event(1)])
                entered.set()
                time.sleep(0.3)

        holder = threading.Thread(target=hold)
        holder.start()
        assert entered.wait(5)
        with other.write():  # 0,3 sn bekler, hata almaz
            other.upsert("events", [_event(2)])
        holder.join()
        assert _count(other.connection(), "events") == 2


def test_read_transaction_is_one_snapshot(cat):
    with cat.write():
        cat.upsert("events", [_event(1)])
    with Catalog(cat.path) as reader:
        with reader.read() as conn:
            assert _count(conn, "events") == 1
            with cat.write():
                cat.upsert("events", [_event(2)])
                cat.upsert("event_slices", [_slice(2)])
            assert _count(conn, "events") == 1 and _count(conn, "event_slices") == 0
        assert not reader.connection().in_transaction
        assert _count(reader.connection(), "events") == 2 and _count(reader.connection(), "event_slices") == 1


# --- iki süreç: BEGIN IMMEDIATE içindeki yazar ve başka süreçteki okuyucu ------------------------------

CHILD = r'''
import sys
from src.store import StoreBusy
from src.store.catalog import Catalog

cat = Catalog(sys.argv[1], busy_timeout_ms=200)
snapshot = None
for line in sys.stdin:
    command = line.strip()
    if command == "count":
        conn = cat.connection()
        row = conn.execute(
            "SELECT (SELECT count(*) FROM events), (SELECT count(*) FROM event_slices), "
            "(SELECT count(*) FROM events e WHERE NOT EXISTS (SELECT 1 FROM event_slices s WHERE s.event_id = e.id))"
        ).fetchone()
        reply = f"{row[0]} {row[1]} {row[2]}"
    elif command == "begin":
        snapshot = cat.read()
        snapshot.__enter__()
        reply = "ok"
    elif command == "end":
        snapshot.__exit__(None, None, None)
        reply = "ok"
    elif command == "write":
        try:
            with cat.write():
                cat.upsert("events", [{"id": 999, "status_class": "live", "row_source": "event",
                                       "first_seen_at": 1, "updated_at": 1}])
                cat.upsert("event_slices", [{"event_id": 999, "key": "statistics", "state": "ok"}])
            reply = "written"
        except StoreBusy:
            reply = "busy"
    elif command == "mode":
        reply = cat.journal_mode
    else:
        break
    print(reply, flush=True)
cat.close()
'''


class _Child:
    def __init__(self, tmp_path, db_path: str):
        script = tmp_path / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
        self.proc = subprocess.Popen([sys.executable, str(script), db_path], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=ROOT)

    def ask(self, command: str) -> str:
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()
        reply = self.proc.stdout.readline().strip()
        if not reply:
            raise AssertionError(f"çocuk süreç yanıt vermedi: {self.proc.stderr.read() if self.proc.stderr else ''}")
        return reply

    def stop(self) -> None:
        try:
            self.proc.communicate("quit\n", timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover
            self.proc.kill()
            self.proc.communicate()


@pytest.fixture
def child(tmp_path, cat):
    process = _Child(tmp_path, cat.path)
    try:
        yield process
    finally:
        process.stop()


def _pairs(first: int, last: int) -> tuple[list[dict], list[dict]]:
    ids = range(first, last + 1)
    return [_event(i) for i in ids], [_slice(i) for i in ids]


def test_writer_and_reader_in_another_process_see_consistent_snapshots(cat, child):
    """
    Yazar her olayı dilimiyle aynı işlemde yazar. Başka süreçteki okuyucu hiçbir an dilimsiz olay görmez:
    işlem bitmeden eski hali, bittikten sonra yeni hali görür; açık okuma işlemi boyunca görüntü değişmez.
    """
    events, slices = _pairs(1, 10)
    with cat.write():
        cat.upsert("events", events)
        cat.upsert("event_slices", slices)
    assert child.ask("mode") == "wal"
    assert child.ask("count") == "10 10 0"

    events, slices = _pairs(11, 15)
    with cat.write():
        cat.upsert("events", events)
        # İşlemin ortası: olaylar yazıldı, dilimleri henüz değil. Okuyucu beklemez ve bunu görmez.
        assert child.ask("count") == "10 10 0"
        assert child.ask("write") == "busy"  # ikinci yazar kilidi alamaz: StoreBusy
        assert child.ask("begin") == "ok"
        assert child.ask("count") == "10 10 0"  # okuyucunun anlık görüntüsü burada alındı
        cat.upsert("event_slices", slices)

    # Yazar işlemi bitirdi; okuyucunun açık işlemi hâlâ eski görüntüde
    assert _count(cat.connection(), "events") == 15
    assert child.ask("count") == "10 10 0"
    assert child.ask("end") == "ok"
    assert child.ask("count") == "15 15 0"

    assert child.ask("write") == "written"
    assert _count(cat.connection(), "events") == 16 and _count(cat.connection(), "event_slices") == 16


def test_rolled_back_write_is_never_visible_to_another_process(cat, child):
    with pytest.raises(RuntimeError):
        with cat.write():
            cat.upsert("events", [_event(1)])
            assert child.ask("count") == "0 0 0"
            raise RuntimeError("yarıda kaldı")

    assert child.ask("count") == "0 0 0"


# --- yerine konan dosya (bölüm 6.3) -------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="Windows'ta açık veritabanı dosyasının yerine yenisi konamaz")
def test_connection_reopens_after_the_file_was_replaced(tmp_path, monkeypatch):
    path = catalog.catalog_path(tmp_path / "data")
    build = path + ".build"
    with Catalog(path) as c:
        c.prepare()
        with c.write():
            c.upsert("events", [_event(1)])
        old = c.connection()
        assert _count(old, "events") == 1

        with Catalog(build) as fresh:  # başka bir sürecin yeniden kurduğu katalog
            fresh.prepare()
            with fresh.write():
                fresh.upsert("events", [_event(10), _event(11)])
        os.replace(build, path)
        for stale in catalog.sidecar_paths(path):
            if os.path.exists(stale):
                os.remove(stale)

        # Denetim en çok saniyede bir yapılır: o ana kadar eski (silinmiş) dosya okunur
        assert c.connection() is old and _count(c.connection(), "events") == 1
        monkeypatch.setattr(catalog, "REOPEN_CHECK_SECONDS", 0.0)
        renewed = c.connection()

        assert renewed is not old
        assert [r[0] for r in renewed.execute("SELECT id FROM events ORDER BY id")] == [10, 11]
        assert c.connection() is renewed  # dosya aynı kaldıkça bağlantı da aynı
        with c.write():
            c.upsert("events", [_event(12)])
        assert _count(c.connection(), "events") == 3


def test_connection_is_not_reopened_in_the_middle_of_a_transaction(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "REOPEN_CHECK_SECONDS", 0.0)
    replaced = {"value": False}
    real = catalog._file_identity
    monkeypatch.setattr(catalog, "_file_identity", lambda p: (1, 2) if replaced["value"] else real(p))
    with Catalog(catalog.catalog_path(tmp_path)) as c:
        c.prepare()
        conn = c.connection()
        with c.write():
            replaced["value"] = True
            assert c.connection() is conn  # işlem sürerken bağlantı değişmez
            c.upsert("events", [_event(1)])
        if real(c.path) is not None:  # dosya sistemi inode veriyorsa işlemden sonra yeniden açılır
            assert c.connection() is not conn


# --- upsert, meta, clear ------------------------------------------------------------------------------

def test_upsert_inserts_updates_given_columns_and_keeps_the_rest(cat):
    with cat.write():
        assert cat.upsert("events", [_event(1, sport="football", listed_in="round_3", stale=1, home_score=1)]) == 1
        assert cat.upsert("events", [_event(1, home_score=2, updated_at=9), _event(2)]) == 2

    rows = {r["id"]: dict(r) for r in cat.connection().execute("SELECT * FROM events")}
    assert (rows[1]["home_score"], rows[1]["updated_at"]) == (2, 9)
    assert (rows[1]["sport"], rows[1]["listed_in"], rows[1]["stale"]) == ("football", "round_3", 1)  # verilmedi: kaldı
    assert (rows[2]["sport"], rows[2]["stale"], rows[2]["has_event_payload"], rows[2]["listed_in"]) == ("", 0, 0, None)


def test_upsert_ignore_only_inserts_missing_rows(cat):
    with cat.write():
        cat.upsert("tournaments", [{"id": 17, "name": "Premier League", "updated_at": 1}])
        written = cat.upsert("tournaments", [{"id": 17, "name": "eski ad", "updated_at": 2},
                                             {"id": 8, "name": "LaLiga", "updated_at": 2}], on_conflict="ignore")

    assert written == 1
    assert [tuple(r) for r in cat.connection().execute("SELECT id, name, updated_at FROM tournaments ORDER BY id")] == [
        (8, "LaLiga", 2), (17, "Premier League", 1)]


def test_upsert_handles_composite_keys_and_rows_with_only_key_columns(cat):
    with cat.write():
        cat.upsert("event_slices", [_slice(1, "statistics"), _slice(1, "lineups", state="empty", has_payload=0)])
        cat.upsert("event_slices", [_slice(1, "lineups", state="error", has_payload=0, error_reason="403")])
        cat.upsert("pending_writes", [{"kind": "event", "entity_id": 1, "started_at": 5}])
        cat.upsert("pending_writes", [{"kind": "event", "entity_id": 1, "started_at": 6}])
        assert cat.upsert("event_participants", [{"participant_id": 3, "start_ts": 0, "event_id": 1, "side": 1}]) == 1

    conn = cat.connection()
    assert [tuple(r) for r in conn.execute("SELECT key, sub, state, error_reason FROM event_slices ORDER BY key")] == [
        ("lineups", "", "error", "403"), ("statistics", "", "ok", None)]
    assert conn.execute("SELECT started_at FROM pending_writes").fetchall()[0][0] == 6


def test_upsert_rejects_bad_rows_and_calls_outside_a_write(cat):
    with pytest.raises(StoreError, match="write"):
        cat.upsert("events", [_event(1)])
    with pytest.raises(StoreError, match="write"):
        cat.set_meta("k", "v")
    with pytest.raises(StoreError, match="write"):
        cat.clear()
    with cat.write():
        with pytest.raises(StoreError, match="nope"):
            cat.upsert("events", [_event(1, nope=1)])
        with pytest.raises(StoreError, match="event_id"):
            cat.upsert("event_slices", [{"key": "statistics", "state": "ok"}])  # `sub` varsayılanlı, `event_id` değil
        with pytest.raises(StoreError, match="'id'"):
            cat.upsert("events", [{"status_class": "live", "row_source": "event", "first_seen_at": 1, "updated_at": 1}])
        with pytest.raises(StoreError, match="tablo"):
            cat.upsert("no_such_table", [{"id": 1}])
        with pytest.raises(ValueError):
            cat.upsert("events", [_event(1)], on_conflict="replace")
        with pytest.raises(StoreError):  # CHECK kısıtı
            cat.upsert("event_slices", [_slice(1, state="not_requested")])
        assert cat.upsert("events", []) == 0
    assert _count(cat.connection(), "events") == 0


def test_meta_and_clear(cat):
    assert cat.get_meta("built_at") is None
    with cat.write():
        cat.set_meta("built_at", "1")
        cat.set_meta("built_at", "2")
        cat.upsert("events", [_event(1)])
        cat.upsert("event_slices", [_slice(1)])
    assert cat.get_meta("built_at") == "2"

    with pytest.raises(RuntimeError):
        with cat.write():
            cat.clear()
            assert _count(cat.connection(), "events") == 0
            raise RuntimeError("yeniden kurma yarıda kaldı")
    assert _count(cat.connection(), "events") == 1 and cat.get_meta("built_at") == "2"  # eski katalog duruyor

    with cat.write():
        cat.clear()
    assert all(_count(cat.connection(), t) == 0 for t in TABLES) and cat.tables() == TABLES


# --- türetilen satırlar şemaya oturur -----------------------------------------------------------------

def test_every_derived_row_fits_the_schema_and_reads_back_unchanged(cat):
    payloads = []
    for number, path in enumerate(STATUS_FIXTURES):
        event = json.loads(path.read_text(encoding="utf-8"))
        event["id"] = 1_000_000 + number  # aynı maç birkaç yanıtta geçiyor: her yanıt kendi satırını alsın
        payloads.append((event, path.parent.name))
    payloads += [(sf.basic_payload(ev), None) for name, ev in sorted(vars(sf).items()) if isinstance(ev, sf.Ev)]

    rows: dict[int, dict] = {}
    with cat.write():
        for event, sport in payloads:
            row = derive.event_row(event, "event", 1790856000, sport=sport)
            rows[row["id"]] = row
            cat.upsert("events", [{**row, "layout": "legacy", "first_seen_at": 1, "updated_at": 2}])
            cat.upsert("event_participants", derive.event_participant_rows(row))
            entities = derive.event_entity_rows(event, updated_at=2, sport=sport)
            for table, found in (("sports", entities.sport), ("categories", entities.category),
                                 ("tournaments", entities.tournament), ("seasons", entities.season)):
                if found is not None:
                    cat.upsert(table, [found], on_conflict="ignore")
            cat.upsert("participants", entities.participants)
        cat.upsert("seasons", derive.season_list_rows(
            {"seasons": [{"id": sf.PL_2627.id, "name": sf.PL_2627.name, "year": sf.PL_2627.year},
                         {"id": sf.PL_2526.id, "name": sf.PL_2526.name, "year": sf.PL_2526.year}]},
            tournament_id=sf.PL.id, updated_at=3))

    conn = cat.connection()
    stored = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM events")}
    assert len(stored) == len(rows) == len(payloads) > 190
    for event_id, row in rows.items():
        assert {k: stored[event_id][k] for k in derive.EVENT_DERIVED_COLUMNS} == row
        assert (stored[event_id]["status_regressed"], stored[event_id]["stale"]) == (0, 0)
    # Sezon listesi `listed` / `position` yazar; olaydan gelen satır onları varsayılanda bırakmıştı
    seasons = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM seasons WHERE tournament_id = ?", (sf.PL.id,))}
    assert (seasons[sf.PL_2627.id]["listed"], seasons[sf.PL_2627.id]["position"]) == (1, 0)
    assert (seasons[sf.PL_2526.id]["listed"], seasons[sf.PL_2526.id]["position"]) == (1, 1)
    assert seasons[sf.PL_2627.id]["sort_key"] == 2026.0
    assert _count(conn, "tournaments") >= 5 and _count(conn, "participants") > 20
    assert conn.execute("SELECT count(*) FROM event_participants WHERE side NOT IN (1, 2)").fetchone()[0] == 0
    assert cat.quick_check() == []


# --- ATTACH -------------------------------------------------------------------------------------------

def test_attached_database_is_available_on_every_thread(tmp_path):
    state = tmp_path / "state.db"
    _edit(str(state), "CREATE TABLE follows (kind TEXT, entity_id INTEGER, enabled INTEGER)",
          "INSERT INTO follows VALUES ('tournament', 17, 1), ('tournament', 8, 0)")
    query = ("SELECT e.id FROM state.follows f JOIN events e ON e.tournament_id = f.entity_id "
             "WHERE f.kind = 'tournament' AND f.enabled = 1 ORDER BY e.id")
    with Catalog(catalog.catalog_path(tmp_path), attach={"state": state}) as c:
        c.prepare()
        with c.write():
            c.upsert("events", [_event(1, tournament_id=17), _event(2, tournament_id=8), _event(3, tournament_id=17)])
        found: list[list[int]] = []
        thread = threading.Thread(target=lambda: found.append([r[0] for r in c.connection().execute(query)]))
        thread.start()
        thread.join()

        assert found == [[1, 3]] == [[r[0] for r in c.connection().execute(query)]]

    with Catalog(catalog.catalog_path(tmp_path), attach={"state": tmp_path / "missing.db"}) as c:
        with pytest.raises(StoreError, match="ATTACH"):
            c.connection()
    assert not (tmp_path / "missing.db").exists()
    for bad in ("main", "temp", "State", "a b", "x;y", ""):
        with pytest.raises(ValueError):
            Catalog(catalog.catalog_path(tmp_path), attach={bad: state})


# --- hata çevirisi ------------------------------------------------------------------------------------

def _sqlite_error(cls, message: str, code: int | None = None) -> sqlite3.Error:
    exc = cls(message)
    if code is not None:
        exc.sqlite_errorcode = code
    return exc


@pytest.mark.parametrize("exc, kind, errno_code, fatal", [
    (_sqlite_error(sqlite3.OperationalError, "database is locked", 5), StoreBusy, None, False),
    (_sqlite_error(sqlite3.OperationalError, "database is locked", 517), StoreBusy, None, False),  # BUSY_SNAPSHOT
    (_sqlite_error(sqlite3.OperationalError, "database is locked"), StoreBusy, None, False),  # Python 3.10
    (_sqlite_error(sqlite3.OperationalError, "database table is locked", 6), StoreError, None, False),
    (_sqlite_error(sqlite3.OperationalError, "database or disk is full", 13), StoreError, errno.ENOSPC, True),
    (_sqlite_error(sqlite3.OperationalError, "database or disk is full"), StoreError, errno.ENOSPC, True),
    (_sqlite_error(sqlite3.OperationalError, "attempt to write a readonly database", 8), StoreError, errno.EROFS, True),
    (_sqlite_error(sqlite3.OperationalError, "attempt to write a readonly database"), StoreError, errno.EROFS, True),
    (_sqlite_error(sqlite3.DatabaseError, "file is not a database", 26), CatalogCorrupt, None, False),
    (_sqlite_error(sqlite3.DatabaseError, "file is not a database"), CatalogCorrupt, None, False),
    (_sqlite_error(sqlite3.DatabaseError, "database disk image is malformed", 11), CatalogCorrupt, None, False),
    (_sqlite_error(sqlite3.IntegrityError, "UNIQUE constraint failed: events.id", 1555), StoreError, None, False),
    (_sqlite_error(sqlite3.OperationalError, "no such table: x", 1), StoreError, None, False),
], ids=range(13))
def test_sqlite_errors_map_to_store_errors(exc, kind, errno_code, fatal):
    error = catalog.to_store_error(exc, "/data/.meta/catalog.db")

    assert type(error) is kind and isinstance(error, StorageError)
    assert (error.errno, error.fatal, error.path) == (errno_code, fatal, "/data/.meta/catalog.db")
    assert str(exc) in str(error) and error.detail == str(exc)
    assert catalog.is_busy_error(exc) is (kind is StoreBusy)


def test_is_busy_error_only_for_sqlite_operational_errors():
    assert not catalog.is_busy_error(RuntimeError("database is locked"))
    assert not catalog.is_busy_error(sqlite3.IntegrityError("database is locked"))


def test_paths():
    path = catalog.catalog_path(os.path.join("some", "data"))

    assert path == os.path.join("some", "data", ".meta", "catalog.db")
    assert catalog.sidecar_paths(path) == (path + "-wal", path + "-shm")
    assert os.path.isfile(catalog.SCHEMA_FILE)


# --- katmanlama ---------------------------------------------------------------------------------------

def test_module_imports_only_what_the_store_may_import():
    """Bölüm 2.1: Store yalnızca src.sports, src.status, src.slices, src.exceptions ve src.version'ı içe aktarabilir."""
    code = (
        "import sys, json; import src.store.catalog; "
        "print(json.dumps(sorted(m for m in sys.modules if m == 'src' or m.startswith('src.'))))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)

    assert set(json.loads(out.stdout)) == {
        "src", "src.exceptions", "src.sports", "src.status", "src.store", "src.store.catalog", "src.store.derive",
        "src.store.errors", "src.store.layout", "src.store.sqlite"}
