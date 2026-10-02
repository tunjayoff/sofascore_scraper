"""
src/store/api.py: `open_store`, süreç içi kayıt defteri, `.meta/schema.json` ve `Store.info`
(docs/design/01-storage.md bölüm 2.3 ve 7.1; plan maddesi ST-10).

Açılış bir 2.x dizinine yalnızca `.meta/` altındaki üç şeyi ekler (schema.json, state.db, catalog.db) ve
başka hiçbir dosyaya dokunmaz; daha yeni bir düzenle yazılmış dizin yazmak için açılmaz. Tümü çevrimdışı.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict

import pytest

import src.store
import store_fixtures as sf
from src.store import (
    LeaseHeld,
    PayloadCorrupt,
    SchemaTooNew,
    Store,
    StoreError,
    StoreInfo,
    open_store,
)
from src.store import api as api_mod
from src.store import layout
from src.store.catalog import CATALOG_SCHEMA
from src.store.derive import DERIVE_VERSION
from src.store.manifest import MANIFEST_FORMAT
from src.version import __version__ as APP_VERSION

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LEGACY_JOBS_DDL = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
    current_task TEXT NOT NULL DEFAULT '', payload_json TEXT, log_json TEXT NOT NULL DEFAULT '[]',
    result_json TEXT, started_at TEXT, finished_at TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0
)
"""


@pytest.fixture(autouse=True)
def _close_stores():
    """Testin açtığı depolar kayıt defterinde kalmasın (sonraki test aynı yolu yeniden açabilir)."""
    yield
    for store in list(api_mod._registry.values()):
        store.close()


@pytest.fixture
def data_dir(tmp_path) -> Path:
    return tmp_path / "data"


def _schema_path(data_dir: Path) -> Path:
    return data_dir / ".meta" / "schema.json"


def _read_schema(data_dir: Path) -> Dict[str, Any]:
    return json.loads(_schema_path(data_dir).read_text(encoding="utf-8"))


def _edit_schema(data_dir: Path, **changes: Any) -> None:
    schema = _read_schema(data_dir)
    schema.update(changes)
    _schema_path(data_dir).write_text(json.dumps(schema), encoding="utf-8")


def _tree(root: Path) -> Dict[str, str]:
    """`.meta/` dışındaki her dosya → içerik özeti (dizinler de, boş olsalar bile, listede)."""
    found: Dict[str, str] = {}
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not (Path(current) == root and d == ".meta"))
        rel = Path(current).relative_to(root).as_posix()
        found[rel + "/"] = ""
        for name in names:
            path = Path(current) / name
            found[f"{rel}/{name}"] = hashlib.sha256(path.read_bytes()).hexdigest() + f":{path.stat().st_mtime_ns}"
    return found


# --- açılış --------------------------------------------------------------------------------------

def test_open_creates_the_meta_files_of_a_new_directory(data_dir):
    store = open_store(data_dir)
    assert isinstance(store, Store) and store.data_dir == data_dir and store.readonly is False
    names = set(os.listdir(data_dir / ".meta"))
    assert {"schema.json", "state.db", "catalog.db"} <= names
    assert names - {"schema.json", "state.db", "state.db-wal", "state.db-shm", "catalog.db", "catalog.db-wal",
                    "catalog.db-shm", "locks"} == set()
    assert os.listdir(data_dir) == [".meta"]


def test_schema_json_has_the_fields_of_the_design(data_dir):
    store = open_store(data_dir)
    schema = _read_schema(data_dir)
    assert list(schema) == [
        "store_id", "layout_version", "min_reader_layout", "manifest_format", "state_schema", "catalog_schema",
        "derive_version", "created_by", "created_at", "last_writer",
    ]
    assert len(schema["store_id"]) == 32 and schema["store_id"] == store.store_id
    assert (schema["layout_version"], schema["min_reader_layout"]) == (3, 3) == (
        api_mod.LAYOUT_VERSION, api_mod.MIN_READER_LAYOUT)
    assert schema["manifest_format"] == MANIFEST_FORMAT
    assert (schema["state_schema"], schema["catalog_schema"], schema["derive_version"]) == (
        2, CATALOG_SCHEMA, DERIVE_VERSION)  # state şeması: geçiş 0002 (iş yöneticisi)
    assert schema["created_by"] == APP_VERSION and schema["created_at"].endswith("+00:00")
    assert schema["last_writer"] == {"app_version": APP_VERSION, "at": schema["created_at"]}


@pytest.mark.parametrize("fixture", sf.FIXTURE_NAMES)
def test_opening_a_2x_directory_adds_only_the_meta_files(tmp_path, fixture):
    """Saf 2.x dizini: açılış `.meta/` altına üç dosyayı ekler, başka hiçbir dosyayı ya da dizini değiştirmez."""
    root = tmp_path / fixture
    sf.build_fixture(fixture, root)
    before = _tree(root)
    store = open_store(root)
    with store.lease("writer"):
        store.info()
    store.close()
    assert _tree(root) == before
    assert {"schema.json", "state.db", "catalog.db"} <= set(os.listdir(root / ".meta"))
    assert not (root / "v3").exists()


def test_legacy_job_history_is_imported_on_open_and_the_old_file_is_untouched(data_dir):
    legacy = data_dir / ".meta" / "jobs.db"
    legacy.parent.mkdir(parents=True)
    conn = sqlite3.connect(legacy)
    conn.execute(LEGACY_JOBS_DDL)
    conn.execute("INSERT INTO jobs (id, status, started_at) VALUES ('old-1', 'completed', '2026-09-01T10:00:00+00:00')")
    conn.commit()
    conn.close()
    digest = hashlib.sha256(legacy.read_bytes()).hexdigest()

    store = open_store(data_dir)
    assert store.info(sizes=False).rows["state"]["jobs"] == 1
    store.close()
    assert open_store(data_dir).info(sizes=False).rows["state"]["jobs"] == 1  # ikinci açılış yeniden aktarmaz
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == digest


def test_create_false_refuses_a_directory_that_is_not_a_store(data_dir, tmp_path):
    with pytest.raises(StoreError):
        open_store(data_dir, create=False)
    assert not data_dir.exists()
    (tmp_path / "plain" / "matches").mkdir(parents=True)
    with pytest.raises(StoreError):
        open_store(tmp_path / "plain", create=False)
    assert os.listdir(tmp_path / "plain") == ["matches"]

    open_store(data_dir).close()
    again = open_store(data_dir, create=False)
    assert again.info(sizes=False).state_schema == 2


def test_unusable_directory_raises_a_store_error(tmp_path):
    (tmp_path / "file").write_text("x", encoding="utf-8")
    with pytest.raises(StoreError):
        open_store(tmp_path / "file" / "data")
    garbage = tmp_path / "data" / ".meta" / "state.db"
    garbage.parent.mkdir(parents=True)
    garbage.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(StoreError) as broken:
        open_store(tmp_path / "data")
    assert broken.value.path == str(garbage)


# --- kayıt defteri -------------------------------------------------------------------------------

def test_one_store_per_directory_per_process(data_dir, tmp_path, monkeypatch):
    store = open_store(data_dir)
    assert open_store(str(data_dir)) is store
    assert open_store(data_dir / ".." / "data") is store  # aynı dizinin başka yazılışı
    monkeypatch.chdir(tmp_path)
    assert open_store("data") is store

    other = open_store(tmp_path / "other")
    assert other is not store and other.store_id != store.store_id

    readonly = open_store(data_dir, readonly=True)
    assert readonly is not store and readonly.readonly and readonly.store_id == store.store_id
    assert open_store(data_dir, readonly=True) is readonly


def test_default_directory_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "from-env"))
    assert open_store().data_dir == tmp_path / "from-env"
    monkeypatch.delenv("DATA_DIR")
    monkeypatch.chdir(tmp_path)
    assert os.path.samefile(open_store().data_dir, tmp_path / "data")


def test_close_removes_the_store_from_the_registry(data_dir):
    store = open_store(data_dir)
    store_id = store.store_id
    store.close()
    store.close()  # yinelenen kapatma zararsız
    assert store.closed
    for call in (store.info, lambda: store.lease("writer"), lambda: store.lease_holder("writer")):
        with pytest.raises(StoreError):
            call()
    reopened = open_store(data_dir)
    assert reopened is not store and reopened.store_id == store_id


def test_a_lease_outlives_the_store_object_that_gave_it(data_dir):
    store = open_store(data_dir)
    lease = store.lease("writer")
    store.close()
    with pytest.raises(LeaseHeld):
        open_store(data_dir).lease("writer")
    lease.release()
    with open_store(data_dir).lease("writer"):
        pass


def test_threads_get_the_same_store(data_dir):
    found, errors = [], []

    def worker():
        try:
            found.append(open_store(data_dir))
        except Exception as e:  # noqa: BLE001 - iş parçacığındaki hata teste taşınır
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == [] and len(found) == 8 and len({id(s) for s in found}) == 1


def test_processes_opening_a_new_directory_agree_on_the_store_id(data_dir):
    script = "import sys\nfrom src.store import open_store\nprint(open_store(sys.argv[1]).store_id)\n"
    procs = [
        subprocess.Popen([sys.executable, "-c", script, str(data_dir)], cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(4)
    ]
    results = [p.communicate(timeout=120) for p in procs]
    assert [p.returncode for p in procs] == [0, 0, 0, 0], [err for _out, err in results]
    assert {out.strip() for out, _err in results} == {_read_schema(data_dir)["store_id"]}


def test_a_writer_in_another_process_is_seen_through_the_facade(data_dir):
    script = (
        "import sys\nfrom src.store import open_store\n"
        "lease = open_store(sys.argv[1]).lease('writer', purpose='headless')\n"
        "print('ready', flush=True)\nsys.stdin.readline()\nlease.release()\n"
    )
    store = open_store(data_dir)
    proc = subprocess.Popen([sys.executable, "-c", script, str(data_dir)], cwd=ROOT, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "ready"
        with pytest.raises(LeaseHeld) as refused:
            store.lease("writer", purpose="web job")
        assert (refused.value.name, refused.value.pid, refused.value.purpose) == ("writer", proc.pid, "headless")
        assert [(i.name, i.pid) for i in store.info(sizes=False).leases] == [("writer", proc.pid)]
        with store.lease("watcher:tennis"):  # izleyicinin kilidi ayrıdır
            pass
    finally:
        _out, err = proc.communicate("\n", timeout=60)
    assert proc.returncode == 0, err
    with store.lease("writer", wait=20):
        pass


# --- schema.json: güncelleme ve sürüm denetimi ---------------------------------------------------

def test_reopening_does_not_rewrite_schema_json(data_dir):
    open_store(data_dir).close()
    before = (_schema_path(data_dir).read_bytes(), _schema_path(data_dir).stat().st_mtime_ns)
    open_store(data_dir).close()
    open_store(data_dir, readonly=True).close()
    assert (_schema_path(data_dir).read_bytes(), _schema_path(data_dir).stat().st_mtime_ns) == before


def test_a_newer_app_version_updates_last_writer_and_keeps_the_rest(data_dir, monkeypatch):
    open_store(data_dir).close()
    _edit_schema(data_dir, extra_field={"kept": True}, derive_version=0)
    created = _read_schema(data_dir)

    monkeypatch.setattr(api_mod, "APP_VERSION", "9.9.9")
    open_store(data_dir, readonly=True).close()  # salt okunur açılış dosyayı değiştirmez
    assert _read_schema(data_dir) == created

    store = open_store(data_dir)
    schema = _read_schema(data_dir)
    assert schema["last_writer"]["app_version"] == "9.9.9" and schema["derive_version"] == DERIVE_VERSION
    for key in ("store_id", "created_by", "created_at", "extra_field"):
        assert schema[key] == created[key]
    assert store.info(sizes=False).last_writer == schema["last_writer"]
    assert store.info(sizes=False).created_by == APP_VERSION


def test_a_newer_layout_is_not_opened_for_writing(data_dir):
    open_store(data_dir).close()
    _edit_schema(data_dir, layout_version=4)
    before = _schema_path(data_dir).read_bytes()
    with pytest.raises(SchemaTooNew) as too_new:
        open_store(data_dir)
    assert (too_new.value.component, too_new.value.found, too_new.value.supported) == ("layout", 4, 3)
    assert too_new.value.path == str(_schema_path(data_dir))

    readonly = open_store(data_dir, readonly=True)  # min_reader_layout hâlâ 3: okunabilir
    assert readonly.info(sizes=False).layout_version == 4
    assert _schema_path(data_dir).read_bytes() == before


def test_a_directory_that_needs_a_newer_reader_is_not_opened_at_all(data_dir):
    open_store(data_dir).close()
    _edit_schema(data_dir, layout_version=4, min_reader_layout=4)
    for readonly in (False, True):
        with pytest.raises(SchemaTooNew) as too_new:
            open_store(data_dir, readonly=readonly)
        assert (too_new.value.component, too_new.value.found) == ("layout", 4)


def test_a_newer_layout_is_refused_before_anything_is_created(data_dir):
    _schema_path(data_dir).parent.mkdir(parents=True)
    _schema_path(data_dir).write_text(
        json.dumps({"store_id": "abc", "layout_version": 4, "min_reader_layout": 3}), encoding="utf-8")
    with pytest.raises(SchemaTooNew):
        open_store(data_dir)
    assert os.listdir(data_dir / ".meta") == ["schema.json"]


def test_a_newer_state_schema_raises_schema_too_new(data_dir):
    open_store(data_dir).close()
    conn = sqlite3.connect(data_dir / ".meta" / "state.db")
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(SchemaTooNew) as too_new:
        open_store(data_dir)
    assert (too_new.value.component, too_new.value.found) == ("state", 99)


@pytest.mark.parametrize("content", [
    b"{not json", b"[]", b"\xff\xfe", b'{"layout_version": 3, "min_reader_layout": 3}',
    b'{"store_id": "", "layout_version": 3, "min_reader_layout": 3}',
    b'{"store_id": "x", "layout_version": "3", "min_reader_layout": 3}',
    b'{"store_id": "x", "layout_version": 3, "min_reader_layout": true}',
])
def test_a_damaged_schema_json_is_reported_and_left_alone(data_dir, content):
    _schema_path(data_dir).parent.mkdir(parents=True)
    _schema_path(data_dir).write_bytes(content)
    with pytest.raises(PayloadCorrupt) as corrupt:
        open_store(data_dir)
    assert corrupt.value.path == str(_schema_path(data_dir))
    assert _schema_path(data_dir).read_bytes() == content
    assert os.listdir(data_dir / ".meta") == ["schema.json"]


# --- info ----------------------------------------------------------------------------------------

def test_info_reports_versions_rows_bytes_and_leases(data_dir):
    store = open_store(data_dir)
    (data_dir / "matches" / "17_Premier_League").mkdir(parents=True)
    (data_dir / "matches" / "17_Premier_League" / "round_1.json").write_bytes(b"x" * 1000)
    (data_dir / "note.txt").write_bytes(b"y" * 10)

    info = store.info()
    assert isinstance(info, StoreInfo)
    assert (info.data_dir, info.readonly, info.store_id) == (str(data_dir), False, store.store_id)
    assert (info.layout_version, info.min_reader_layout, info.manifest_format) == (3, 3, MANIFEST_FORMAT)
    assert (info.state_schema, info.catalog_schema) == (2, CATALOG_SCHEMA)
    assert info.journal_modes == {"state": "wal", "catalog": "wal"}
    assert set(info.rows) == {"state", "catalog"}
    assert {"jobs", "leases", "follows", "stream_events", "runtime", "meta"} <= set(info.rows["state"])
    assert {"events", "event_slices", "tournaments", "seasons", "meta"} <= set(info.rows["catalog"])
    assert info.rows["catalog"]["events"] == 0 and info.events_by_layout == {} and info.leases == ()
    assert info.bytes["matches"] == 1000 and info.bytes["note.txt"] == 10 and info.bytes[".meta"] > 0
    assert set(info.bytes) == {".meta", "matches", "note.txt"}
    assert store.info(sizes=False).bytes == {}

    with store.lease("writer", purpose="job") as lease:
        assert store.info(sizes=False).leases == (lease.info(),)
        assert store.lease_holder("writer") == lease.info() and store.lease_holder("live") is None


def test_info_follows_the_catalog(data_dir):
    store = open_store(data_dir)
    # Yeni kurulan katalog boş bir şemadır: dizinleyici doldurana kadar "yeniden kurulmalı" görünür
    info = store.info(sizes=False)
    assert (info.catalog_rebuild_reason, info.derive_version, info.last_rebuild) == ("derive_version", None, None)

    catalog = store._catalog
    with catalog.write() as conn:
        for event_id, layout_name in ((1, "v3"), (2, "legacy"), (3, "legacy"), (4, None)):
            conn.execute(
                "INSERT INTO events (id, status_class, row_source, layout, first_seen_at, updated_at) "
                "VALUES (?, 'completed', 'event', ?, 1, 1)", (event_id, layout_name))
        catalog.set_meta(api_mod.META_BUILT_AT, "1790856000")
        catalog.stamp_derive_version()
    info = store.info(sizes=False)
    assert (info.catalog_rebuild_reason, info.derive_version) == (None, DERIVE_VERSION)
    assert info.events_by_layout == {"legacy": 2, "listing": 1, "v3": 1}
    assert info.rows["catalog"]["events"] == 4 and info.last_rebuild == "1790856000"


def test_info_copes_with_a_catalog_of_another_schema(data_dir):
    open_store(data_dir).close()
    conn = sqlite3.connect(data_dir / ".meta" / "catalog.db")
    conn.execute(f"PRAGMA user_version = {CATALOG_SCHEMA + 1}")
    conn.close()
    info = open_store(data_dir).info(sizes=False)
    assert info.catalog_rebuild_reason == "schema_version" and info.catalog_schema == CATALOG_SCHEMA + 1
    assert info.rows["catalog"] == {} and info.rows["state"]["jobs"] == 0


def test_runtime_facts_are_reachable_through_the_store(data_dir):
    store = open_store(data_dir)
    store.runtime.set("bridge_health", {"ok": True})
    fact = open_store(data_dir, readonly=True).runtime.get("bridge_health")
    assert fact is not None and fact.value == {"ok": True} and fact.pid == os.getpid()


# --- paket kökü ----------------------------------------------------------------------------------

def test_facade_is_exported_from_the_package_root():
    for name in ("open_store", "Store", "StoreInfo", "Lease", "LeaseInfo", "JobStore", "JobStoreConflict",
                 "JobRunningError", "DataOperationRunningError", "default_db_path", "get_job_store"):
        assert name in src.store.__all__ and name in dir(src.store)
    from src.store import jobs, lease

    assert src.store.open_store is api_mod.open_store and src.store.Lease is lease.Lease
    assert src.store.JobStore is jobs.JobStore and src.store.get_job_store is jobs.get_job_store
    with pytest.raises(AttributeError):
        _ = src.store.no_such_name


def test_importing_a_submodule_does_not_load_the_facade():
    """`src.fsutil` yalnızca `src.store.files`'ı ister: kök, SQLite'lı cepheyi kendiliğinden yüklememeli."""
    code = (
        "import sys, json; import src.store.files; "
        "print(json.dumps(sorted(m for m in sys.modules if m == 'src' or m.startswith('src.'))))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    loaded = set(json.loads(out.stdout))
    assert "src.store.files" in loaded
    assert not loaded & {"src.store.api", "src.store.jobs", "src.store.lease", "src.store.state",
                         "src.store.catalog", "src.store.derive", "src.sports", "src.status", "src.version"}


def test_store_paths_match_the_layout(data_dir):
    store = open_store(data_dir)
    assert store._leases.locks_dir == layout.resolve(str(data_dir), layout.LOCKS_DIR)
    assert store._state.path == layout.resolve(str(data_dir), layout.STATE_DB)
    assert store._catalog.path == layout.resolve(str(data_dir), layout.CATALOG_DB)
