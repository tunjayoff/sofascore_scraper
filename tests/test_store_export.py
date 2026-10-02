"""
Dışa aktarma: `Exporter.raw` (ağaç ve JSONL) ve `Exporter.rows` (JSONL, CSV, SQLite, Parquet) (plan maddesi
ST-25; docs/design/01-storage.md bölüm 4.5).

Ölçütler:

  * Ham ağacın her dosyası, saklanan yükle aynı nesneye ayrıştırılır; v3 dosyası saklanan baytların açılmış
    halidir.
  * Bir maçın eski düzen ve v3 kopyası aynı JSONL satırlarını (ve aynı ağacı) verir.
  * Büyük bir yük kümesinin dışa aktarılması bellekte bir yükten fazlasını tutmaz.
  * Dışa aktarma tek bir okuma görüntüsüdür; görüntüden sonra kaybolan ya da bozuk yük atlanır ve bildirilir.
  * Satır yazıcıları: CSV, JSONL ve SQLite gidiş-dönüş; Parquet yalnızca `pyarrow` kuruluysa denenir, paket
    yokken verilen hata her zaman denenir.

Ağ yok.
"""
from __future__ import annotations

import builtins
import csv
import datetime as dt
import gzip
import io
import json
import os
import sqlite3
import threading
import tracemalloc
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Tuple

import pytest

import store_fixtures as sf
from src.store import (
    EventQuery,
    Exporter,
    ExportReport,
    ExportSkip,
    LayoutError,
    Scope,
    Store,
    StoreError,
    open_store,
)
from src.store import codec, layout
from src.store import events as events_mod
from src.store import export as export_mod
from src.store.legacy import LegacyReader
from test_store_put import ARS, LIV, at, basic_of, ok

UTC = dt.timezone.utc


# --- yardımcılar --------------------------------------------------------------------------------------

def payload_slices(store: Store) -> List[Tuple[int, str, str]]:
    """Katalogda yükü olan bütün maç dilimleri: (maç, anahtar, alt anahtar)."""
    return [(int(r[0]), str(r[1]), str(r[2])) for r in store._catalog.connection().execute(
        "SELECT event_id, key, sub FROM event_slices WHERE has_payload = 1 ORDER BY event_id, key, sub")]


def tree_files(root: Path) -> Dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def tree_name(event_id: int, key: str, sub: str = "") -> str:
    return f"events/{event_id}/{key}/{sub}.json" if sub else f"events/{event_id}/{key}.json"


def lines_of(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def promote_all(store: Store, data_dir: Path) -> List[int]:
    """Eski düzendeki her maçı v3'e yükseltir (tests/test_store_promotion.py'deki gibi); kimlikleri döndürür."""
    reader = LegacyReader(data_dir)
    conn = store._catalog.connection()
    found = [(int(r[0]), str(r[1])) for r in conn.execute(
        "SELECT id, path FROM events WHERE layout = 'legacy' ORDER BY id")]
    for event_id, path in found:
        event = reader.read_event(reader.event_dir_at(path), payloads=True)
        with store._catalog.write():
            events_mod.promote_legacy(data_dir, event, label="put")
            assert store.catalog.index_event(event_id) == "v3"
    return [event_id for event_id, _ in found]


def leftovers(directory: Path) -> List[str]:
    """Hedefin yanında kalan gizli geçici adlar."""
    return sorted(name for name in os.listdir(directory) if name.startswith("."))


def staging_entries(store: Store) -> List[str]:
    try:
        return sorted(os.listdir(layout.resolve(store.data_dir, layout.TMP_DIR)))
    except FileNotFoundError:
        return []


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return open_store(tmp_path / "data")


@pytest.fixture(params=["canonical", "legacy"])
def fx(request: pytest.FixtureRequest, tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture(request.param, tmp_path / "data")


@pytest.fixture
def v3_store(store: Store) -> Store:
    """İki v3 maçı: biri alt anahtarlı dilimle."""
    basic = basic_of()
    store.events.put(ARS, {"event": ok(basic, at()), "statistics": ok(sf.slice_payload("statistics", basic), at(1)),
                           ("odds_all", "1"): ok({"markets": [{"id": 1, "name": "Şampiyon"}]}, at(2))})
    other = basic_of(sf.PL_LIV)
    store.events.put(LIV, {"event": ok(other, at(3))})
    return store


# --- ham ağaç -----------------------------------------------------------------------------------------

def test_the_exporter_is_exported_from_the_store_root(store: Store) -> None:
    assert isinstance(store.export, Exporter)
    assert Exporter.__module__ == "src.store.export"
    assert ExportReport.__module__ == ExportSkip.__module__ == "src.store.export"


def test_every_file_of_a_tree_export_parses_to_the_stored_payload(fx: sf.LegacyFixture, tmp_path: Path) -> None:
    """Eski düzen ağacı: her dosya katalogdaki bir yüklü dilimdir ve saklanan yüke ayrıştırılır."""
    store = open_store(fx.data_dir)
    assert isinstance(store.export, Exporter)
    expected = payload_slices(store)
    assert len(expected) > 20

    report = store.export.raw(EventQuery(), tmp_path / "raw")

    found = tree_files(tmp_path / "raw")
    assert sorted(found) == sorted(tree_name(*item) for item in expected)
    for event_id, key, sub in expected:
        data = found[tree_name(event_id, key, sub)]
        assert json.loads(data) == store.events.payload(event_id, key, sub), (event_id, key)
        assert data == codec.canonical_bytes(store.events.payload(event_id, key, sub))
    assert (report.fmt, report.dest, report.items, report.skipped) == ("tree", str(tmp_path / "raw"),
                                                                       len(expected), ())
    assert report.events == len({event_id for event_id, _, _ in expected})
    assert report.bytes == sum(len(data) for data in found.values())
    assert leftovers(tmp_path) == []


def test_a_v3_tree_holds_the_stored_bytes_and_sub_slices_get_a_directory(v3_store: Store, tmp_path: Path) -> None:
    report = v3_store.export.raw(EventQuery(), tmp_path / "raw")

    found = tree_files(tmp_path / "raw")
    assert sorted(found) == sorted([tree_name(ARS, "event"), tree_name(ARS, "statistics"),
                                    tree_name(ARS, "odds_all", "1"), tree_name(LIV, "event")])
    for event_id, key, sub in payload_slices(v3_store):
        stored = layout.resolve(v3_store.data_dir, layout.slice_path(layout.event_dir(event_id), key, sub))
        assert found[tree_name(event_id, key, sub)] == codec.read_raw(stored)  # açılmış baytların kendisi
        assert json.loads(found[tree_name(event_id, key, sub)]) == v3_store.events.payload(event_id, key, sub)
    assert "Şampiyon" in found[tree_name(ARS, "odds_all", "1")].decode("utf-8")  # ensure_ascii=False korunur
    assert (report.events, report.items) == (2, 4)


def test_pretty_re_indents_each_file(v3_store: Store, tmp_path: Path) -> None:
    v3_store.export.raw(EventQuery(), tmp_path / "raw", pretty=True)

    for event_id, key, sub in payload_slices(v3_store):
        text = (tmp_path / "raw" / tree_name(event_id, key, sub)).read_text(encoding="utf-8")
        value = v3_store.events.payload(event_id, key, sub)
        assert json.loads(text) == value
        assert text == json.dumps(value, ensure_ascii=False, indent=2)


def test_keys_and_the_query_choose_what_is_exported(v3_store: Store, tmp_path: Path) -> None:
    report = v3_store.export.raw(EventQuery(), tmp_path / "events", keys=["event"])
    assert sorted(tree_files(tmp_path / "events")) == sorted([tree_name(ARS, "event"), tree_name(LIV, "event")])
    assert report.items == 2

    report = v3_store.export.raw(EventQuery(scope=Scope(event_ids=(ARS,))), tmp_path / "odds.jsonl", fmt="jsonl",
                                 keys=["odds_all"])
    assert [(line["event_id"], line["key"], line["sub"]) for line in lines_of(tmp_path / "odds.jsonl")] == [
        (ARS, "odds_all", "1")]
    assert (report.events, report.items) == (1, 1)

    report = v3_store.export.raw(EventQuery(scope=Scope(event_ids=(999,))), tmp_path / "none.jsonl", fmt="jsonl")
    assert (tmp_path / "none.jsonl").read_bytes() == b"" and (report.events, report.items) == (0, 0)


def test_events_follow_the_order_of_the_query(fx: sf.LegacyFixture, tmp_path: Path) -> None:
    store = open_store(fx.data_dir)
    for sort in ("start_asc", "start_desc"):
        target = tmp_path / f"{sort}.jsonl"
        store.export.raw(EventQuery(sort=sort), target, fmt="jsonl", keys=["event"])
        exported = [line["event_id"] for line in lines_of(target)]
        listed = [row.id for row in store.events.iter(EventQuery(sort=sort, has_details=True))]
        assert exported == listed


def test_paging_through_many_events_keeps_every_slice(store: Store, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_mod, "EVENT_BATCH", 3)
    ids = [ARS + n for n in range(8)]
    for n, event_id in enumerate(ids):
        store.events.put(event_id, {"event": ok(basic_of(event_id=event_id, startTimestamp=1_790_000_000 + n))})

    report = store.export.raw(EventQuery(sort="start_asc"), tmp_path / "all.jsonl", fmt="jsonl")

    assert [line["event_id"] for line in lines_of(tmp_path / "all.jsonl")] == ids
    assert (report.events, report.items) == (8, 8)


# --- JSONL ---------------------------------------------------------------------------------------------

def test_jsonl_lines_carry_the_payload_and_the_catalog_time(v3_store: Store, tmp_path: Path) -> None:
    report = v3_store.export.raw(EventQuery(), tmp_path / "raw.jsonl", fmt="jsonl")

    raw_lines = (tmp_path / "raw.jsonl").read_bytes().split(b"\n")
    assert raw_lines[-1] == b""
    lines = [json.loads(line) for line in raw_lines[:-1]]
    assert len(lines) == report.items == 4
    for line in lines:
        assert list(line) == ["event_id", "key", "sub", "fetched_at", "payload"]
        info = v3_store.events.slice(line["event_id"], line["key"], line["sub"])
        assert line["fetched_at"] == info.fetched_at.isoformat()
        assert line["payload"] == v3_store.events.payload(line["event_id"], line["key"], line["sub"])
    # yük satıra saklanan baytlarla eklenir
    first = raw_lines[0]
    stored = v3_store.events.payload(lines[0]["event_id"], lines[0]["key"], lines[0]["sub"], raw=True)
    assert first.endswith(b',"payload":' + stored + b"}")
    assert report.bytes == (tmp_path / "raw.jsonl").stat().st_size


def test_legacy_and_v3_copies_of_an_event_give_identical_output(fx: sf.LegacyFixture, tmp_path: Path) -> None:
    store = open_store(fx.data_dir)
    before_lines = store.export.raw(EventQuery(), tmp_path / "legacy.jsonl", fmt="jsonl")
    store.export.raw(EventQuery(), tmp_path / "legacy_tree")
    store.export.raw(EventQuery(), tmp_path / "legacy_pretty", pretty=True)

    promoted = promote_all(store, fx.data_dir)
    assert len(promoted) >= 9
    assert store._catalog.connection().execute("SELECT count(*) FROM events WHERE layout = 'legacy'").fetchone()[0] == 0

    after_lines = store.export.raw(EventQuery(), tmp_path / "v3.jsonl", fmt="jsonl")
    store.export.raw(EventQuery(), tmp_path / "v3_tree")
    store.export.raw(EventQuery(), tmp_path / "v3_pretty", pretty=True)

    assert (tmp_path / "v3.jsonl").read_bytes() == (tmp_path / "legacy.jsonl").read_bytes()
    assert before_lines.items == after_lines.items > 20
    assert tree_files(tmp_path / "v3_tree") == tree_files(tmp_path / "legacy_tree")
    assert tree_files(tmp_path / "v3_pretty") == tree_files(tmp_path / "legacy_pretty")


# --- bellek --------------------------------------------------------------------------------------------

PAYLOAD_CHARS = 2_000_000
PAYLOAD_COUNT = 12


@pytest.fixture
def big_store(store: Store) -> Store:
    for n in range(PAYLOAD_COUNT):
        event_id = ARS + n
        blob = {"blob": [chr(0x41 + n % 26) * PAYLOAD_CHARS]}
        store.events.put(event_id, {"event": ok(basic_of(event_id=event_id)), "blob": ok(blob)})
    return store


@pytest.mark.parametrize("fmt", ["tree", "jsonl"])
def test_a_large_export_never_holds_more_than_one_payload(big_store: Store, tmp_path: Path, fmt: str) -> None:
    target = tmp_path / ("out.jsonl" if fmt == "jsonl" else "out")
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        report = big_store.export.raw(EventQuery(), target, fmt=fmt)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert report.items == 2 * PAYLOAD_COUNT
    total = report.bytes
    assert total > PAYLOAD_COUNT * PAYLOAD_CHARS
    # bir yükün açılmış hali en çok bir kez (JSONL) bellekte; ağaç kipi parça parça kopyalar
    assert peak < 0.5 * PAYLOAD_CHARS, (fmt, peak)


def test_a_large_legacy_export_holds_one_payload_at_a_time(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    store = open_store(data_dir)
    match_dir = data_dir / "match_details" / "17_Premier_League" / "season_PL_26_27"
    for n in range(6):
        event_id = ARS + n
        target = match_dir / str(event_id)
        target.mkdir(parents=True)
        basic = basic_of(event_id=event_id)
        stats = {**sf.slice_payload("statistics", basic), "pad": "x" * PAYLOAD_CHARS}
        (target / "basic.json").write_text(json.dumps(basic, indent=2), encoding="utf-8")
        (target / "statistics.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    store.close()
    store = open_store(data_dir)
    assert len(payload_slices(store)) == 12

    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        report = store.export.raw(EventQuery(), tmp_path / "out.jsonl", fmt="jsonl")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert report.items == 12 and report.bytes > 6 * PAYLOAD_CHARS
    assert peak < 3.5 * PAYLOAD_CHARS, peak  # dosya + ayrıştırılmış yük + kurallı baytlar: tek bir yük


# --- anlık görüntü ve hatalar ---------------------------------------------------------------------------

def test_the_export_is_one_snapshot_of_the_catalog(v3_store: Store, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """Dışa aktarma başladıktan sonra yazılan maç çıktıda yoktur; silinen yük atlanır ve bildirilir."""
    late = ARS + 5000
    original = export_mod._RawOut.write_line
    calls: List[int] = []

    def write_line(self: Any, event_id: int, *args: Any, **kwargs: Any) -> None:
        if not calls:  # ilk satır yazılırken başka bir thread yeni bir maç yazar ve LIV'in yükünü siler
            def writer() -> None:
                v3_store.events.put(late, {"event": ok(basic_of(event_id=late))})
            thread = threading.Thread(target=writer)
            thread.start()
            thread.join()
            os.remove(layout.resolve(v3_store.data_dir, layout.slice_path(layout.event_dir(LIV), "event")))
        calls.append(event_id)
        original(self, event_id, *args, **kwargs)

    monkeypatch.setattr(export_mod._RawOut, "write_line", write_line)
    report = v3_store.export.raw(EventQuery(sort="start_asc"), tmp_path / "snap.jsonl", fmt="jsonl")

    exported = {line["event_id"] for line in lines_of(tmp_path / "snap.jsonl")}
    assert late not in exported and v3_store.events.get(late) is not None
    assert exported == {ARS}
    assert report.skipped == (ExportSkip(LIV, "event", "", "missing", report.skipped[0].detail),)
    assert report.events == 1


def test_a_corrupt_payload_is_skipped_and_reported(v3_store: Store, tmp_path: Path,
                                                   caplog: pytest.LogCaptureFixture) -> None:
    path = Path(layout.resolve(v3_store.data_dir, layout.slice_path(layout.event_dir(ARS), "statistics")))
    path.write_bytes(gzip.compress(b'{"statistics":')[:-6])  # yarım gzip akışı

    for fmt, target in (("tree", tmp_path / "raw"), ("jsonl", tmp_path / "raw.jsonl")):
        with caplog.at_level("WARNING", logger="src.store.export"):
            report = v3_store.export.raw(EventQuery(), target, fmt=fmt)
        assert [(s.event_id, s.key, s.sub, s.reason) for s in report.skipped] == [(ARS, "statistics", "", "corrupt")]
        assert report.items == 3
    assert not (tmp_path / "raw" / tree_name(ARS, "statistics")).exists()  # yarım dosya kalmaz
    assert "Export skipped event" in caplog.text


def test_an_empty_payload_file_is_corrupt(v3_store: Store, tmp_path: Path) -> None:
    path = Path(layout.resolve(v3_store.data_dir, layout.slice_path(layout.event_dir(LIV), "event")))
    path.write_bytes(gzip.compress(b"", mtime=0))
    report = v3_store.export.raw(EventQuery(), tmp_path / "raw")
    assert [(s.event_id, s.reason) for s in report.skipped] == [(LIV, "corrupt")]
    assert not (tmp_path / "raw" / tree_name(LIV, "event")).exists()


def test_an_existing_target_is_kept_unless_overwrite_is_given(v3_store: Store, tmp_path: Path) -> None:
    target = tmp_path / "raw.jsonl"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(StoreError, match="already exists"):
        v3_store.export.raw(EventQuery(), target, fmt="jsonl")
    assert target.read_text(encoding="utf-8") == "old"

    v3_store.export.raw(EventQuery(), target, fmt="jsonl", overwrite=True)
    assert len(lines_of(target)) == 4

    directory = tmp_path / "tree"
    (directory / "stale").mkdir(parents=True)
    with pytest.raises(StoreError):
        v3_store.export.raw(EventQuery(), directory)
    v3_store.export.raw(EventQuery(), directory, overwrite=True)
    assert not (directory / "stale").exists() and len(tree_files(directory)) == 4

    empty = tmp_path / "empty"
    empty.mkdir()
    v3_store.export.raw(EventQuery(), empty)  # boş dizin dolu sayılmaz
    assert len(tree_files(empty)) == 4
    assert leftovers(tmp_path) == []


def test_a_failed_export_leaves_no_output(v3_store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise StoreError("disk full")

    monkeypatch.setattr(export_mod._RawOut, "stream_file", boom)
    monkeypatch.setattr(export_mod._RawOut, "write_line", boom)
    for fmt, name in (("tree", "raw"), ("jsonl", "raw.jsonl")):
        with pytest.raises(StoreError, match="disk full"):
            v3_store.export.raw(EventQuery(), tmp_path / name, fmt=fmt)
        assert not (tmp_path / name).exists()
    assert leftovers(tmp_path) == []

    target = tmp_path / "kept.jsonl"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(StoreError):
        v3_store.export.raw(EventQuery(), target, fmt="jsonl", overwrite=True)
    assert target.read_text(encoding="utf-8") == "old"


def test_raw_rejects_bad_arguments(v3_store: Store, tmp_path: Path) -> None:
    calls = [
        lambda: v3_store.export.raw(EventQuery(), tmp_path / "x", fmt="csv"),
        lambda: v3_store.export.raw(EventQuery(), tmp_path / "x", fmt="jsonl", pretty=True),
        lambda: v3_store.export.raw(EventQuery(), tmp_path / "x", keys="event"),
        lambda: v3_store.export.raw(EventQuery(sort="random"), tmp_path / "x"),
        lambda: v3_store.export.raw({"sport": "football"}, tmp_path / "x"),  # type: ignore[arg-type]
        lambda: v3_store.export.raw(EventQuery(), 42),  # type: ignore[arg-type]
    ]
    for call in calls:
        with pytest.raises(ValueError):
            call()
    with pytest.raises(LayoutError):
        v3_store.export.raw(EventQuery(), tmp_path / "x", keys=["Event"])
    assert not (tmp_path / "x").exists()


def test_a_closed_store_cannot_export(v3_store: Store, tmp_path: Path) -> None:
    v3_store.close()
    with pytest.raises(StoreError):
        v3_store.export.raw(EventQuery(), tmp_path / "x")
    assert not (tmp_path / "x").exists()


# --- satır yazıcıları -----------------------------------------------------------------------------------

COLUMNS = ["id", "name", "score", "ratio", "finished", "extra", "kickoff", "note"]
KICKOFF = dt.datetime(2026, 10, 1, 19, 45, tzinfo=UTC)


def sample_rows() -> Iterator[Mapping[str, Any]]:
    """Bir kez okunabilen bir akış (liste değil): yazıcılar satırları bir kez gezmelidir."""
    yield {"id": 1, "name": "Arsenal, \"The Gunners\"", "score": 3, "ratio": 0.5, "finished": True,
           "extra": {"stage": "Ön eleme", "legs": [1, 2]}, "kickoff": KICKOFF, "note": "çok\nsatırlı"}
    yield {"id": 2, "name": "Chelsea", "score": None, "ratio": 1.25, "finished": False, "extra": None,
           "kickoff": None, "ignored": "x"}
    yield {"id": 3, "name": "", "score": 0, "ratio": 2.0, "finished": None, "extra": [], "kickoff": KICKOFF}


def test_csv_round_trip(store: Store, tmp_path: Path) -> None:
    report = store.export.rows(sample_rows(), COLUMNS, tmp_path / "rows.csv", "csv")

    raw = (tmp_path / "rows.csv").read_bytes()
    assert b"\r\n" not in raw and raw.decode("utf-8").startswith(",".join(COLUMNS) + "\n")
    with open(tmp_path / "rows.csv", newline="", encoding="utf-8") as f:
        found = list(csv.DictReader(f))
    assert found == [
        {"id": "1", "name": "Arsenal, \"The Gunners\"", "score": "3", "ratio": "0.5", "finished": "true",
         "extra": '{"stage":"Ön eleme","legs":[1,2]}', "kickoff": "2026-10-01T19:45:00+00:00", "note": "çok\nsatırlı"},
        {"id": "2", "name": "Chelsea", "score": "", "ratio": "1.25", "finished": "false", "extra": "", "kickoff": "",
         "note": ""},
        {"id": "3", "name": "", "score": "0", "ratio": "2.0", "finished": "", "extra": "[]",
         "kickoff": "2026-10-01T19:45:00+00:00", "note": ""},
    ]
    assert (report.fmt, report.items, report.events, report.bytes) == ("csv", 3, 0, len(raw))


def test_jsonl_round_trip(store: Store, tmp_path: Path) -> None:
    report = store.export.rows(sample_rows(), COLUMNS, tmp_path / "rows.jsonl", "jsonl")

    found = lines_of(tmp_path / "rows.jsonl")
    expected = [{name: row.get(name) for name in COLUMNS} for row in sample_rows()]
    for row in expected:
        row["kickoff"] = row["kickoff"].isoformat() if row["kickoff"] else None
    assert found == expected
    assert all(list(line) == COLUMNS for line in found)
    assert report.items == 3


def test_sqlite_round_trip(store: Store, tmp_path: Path) -> None:
    report = store.export.rows(sample_rows(), COLUMNS, tmp_path / "rows.sqlite", "sqlite", table="matches")

    conn = sqlite3.connect(tmp_path / "rows.sqlite")
    try:
        assert [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")] == ["matches"]
        cursor = conn.execute("SELECT * FROM matches ORDER BY rowid")
        assert [c[0] for c in cursor.description] == COLUMNS
        found = cursor.fetchall()
    finally:
        conn.close()
    assert found == [
        (1, "Arsenal, \"The Gunners\"", 3, 0.5, 1, '{"stage":"Ön eleme","legs":[1,2]}', "2026-10-01T19:45:00+00:00",
         "çok\nsatırlı"),
        (2, "Chelsea", None, 1.25, 0, None, None, None),
        (3, "", 0, 2.0, None, "[]", "2026-10-01T19:45:00+00:00", None),
    ]
    assert (report.items, report.bytes) == (3, (tmp_path / "rows.sqlite").stat().st_size)


def test_sqlite_quotes_odd_names_and_writes_many_rows(store: Store, tmp_path: Path) -> None:
    columns = ['select', 'a "quoted" name', "boşluk ve ş"]
    rows = ({"select": n, 'a "quoted" name': str(n), "boşluk ve ş": 2 ** 70 if n == 0 else n} for n in range(2500))
    report = store.export.rows(rows, columns, tmp_path / "odd.sqlite", "sqlite", table='my "table"')

    conn = sqlite3.connect(tmp_path / "odd.sqlite")
    try:
        assert conn.execute('SELECT count(*) FROM "my ""table"""').fetchone()[0] == 2500
        assert conn.execute('SELECT "boşluk ve ş" FROM "my ""table""" WHERE "select" = 0').fetchone()[0] == str(2 ** 70)
    finally:
        conn.close()
    assert report.items == 2500


@pytest.mark.parametrize("fmt", ["csv", "jsonl", "sqlite"])
def test_rows_can_be_written_to_a_stream(store: Store, tmp_path: Path, fmt: str) -> None:
    on_disk = store.export.rows(sample_rows(), COLUMNS, tmp_path / f"rows.{fmt}", fmt)
    stream = io.BytesIO()

    report = store.export.rows(sample_rows(), COLUMNS, stream, fmt)

    assert not stream.closed
    assert (report.dest, report.items) == ("", on_disk.items)
    assert report.bytes == len(stream.getvalue())
    if fmt == "sqlite":  # aynı içerik (dosya baytları sayfa düzenine bağlı olabilir)
        (tmp_path / "copy.sqlite").write_bytes(stream.getvalue())
        a, b = sqlite3.connect(tmp_path / "copy.sqlite"), sqlite3.connect(tmp_path / f"rows.{fmt}")
        try:
            assert a.execute("SELECT * FROM rows").fetchall() == b.execute("SELECT * FROM rows").fetchall()
        finally:
            a.close()
            b.close()
    else:
        assert stream.getvalue() == (tmp_path / f"rows.{fmt}").read_bytes()
    assert staging_entries(store) == []  # hazırlık (SQLite, Parquet) silindi


def test_rows_with_no_rows_still_write_the_shape(store: Store, tmp_path: Path) -> None:
    assert store.export.rows([], ["a", "b"], tmp_path / "e.csv", "csv").items == 0
    assert (tmp_path / "e.csv").read_text(encoding="utf-8") == "a,b\n"
    store.export.rows([], ["a", "b"], tmp_path / "e.jsonl", "jsonl")
    assert (tmp_path / "e.jsonl").read_bytes() == b""
    store.export.rows([], ["a", "b"], tmp_path / "e.sqlite", "sqlite")
    conn = sqlite3.connect(tmp_path / "e.sqlite")
    try:
        assert [r[1] for r in conn.execute("PRAGMA table_info(rows)")] == ["a", "b"]
    finally:
        conn.close()


def test_rows_reject_bad_arguments_before_writing(store: Store, tmp_path: Path) -> None:
    calls = [
        lambda: store.export.rows([], ["a"], tmp_path / "x", "xlsx"),
        lambda: store.export.rows([], [], tmp_path / "x", "csv"),
        lambda: store.export.rows([], "ab", tmp_path / "x", "csv"),
        lambda: store.export.rows([], ["a", "a"], tmp_path / "x", "csv"),
        lambda: store.export.rows([], ["a", ""], tmp_path / "x", "csv"),
        lambda: store.export.rows([], ["a", "A"], tmp_path / "x", "sqlite"),  # SQLite adları büyük/küçük ayırmaz
        lambda: store.export.rows([], ["a"], tmp_path / "x", "sqlite", table=""),
        lambda: store.export.rows([], ["a"], tmp_path / "x", "sqlite", table="sqlite_master"),
    ]
    for call in calls:
        with pytest.raises(ValueError):
            call()
    assert not (tmp_path / "x").exists()
    assert store.export.rows([], ["a", "A"], tmp_path / "x.csv", "csv").items == 0  # CSV'de ayrı adlar


@pytest.mark.parametrize("fmt", ["csv", "jsonl", "sqlite"])
def test_a_bad_row_fails_the_export_and_leaves_no_file(store: Store, tmp_path: Path, fmt: str) -> None:
    rows = [{"a": 1}, {"a": object()} if fmt != "csv" else "not a mapping"]
    with pytest.raises((StoreError, ValueError)):
        store.export.rows(rows, ["a"], tmp_path / f"bad.{fmt}", fmt)  # type: ignore[arg-type]
    assert not (tmp_path / f"bad.{fmt}").exists()
    assert leftovers(tmp_path) == []


def test_an_existing_rows_target_needs_overwrite(store: Store, tmp_path: Path) -> None:
    target = tmp_path / "rows.csv"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(StoreError, match="already exists"):
        store.export.rows([{"a": 1}], ["a"], target, "csv")
    assert target.read_text(encoding="utf-8") == "old"
    store.export.rows([{"a": 1}], ["a"], target, "csv", overwrite=True)
    assert target.read_text(encoding="utf-8") == "a\n1\n"


# --- Parquet -------------------------------------------------------------------------------------------

def test_parquet_without_pyarrow_names_the_package(store: Store, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def no_pyarrow(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pyarrow" or name.startswith("pyarrow."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pyarrow)
    consumed: List[int] = []

    def rows() -> Iterator[Mapping[str, Any]]:
        consumed.append(1)
        yield {"a": 1}

    for dest in (tmp_path / "rows.parquet", io.BytesIO()):
        with pytest.raises(StoreError) as caught:
            store.export.rows(rows(), ["a"], dest, "parquet")
        assert "pyarrow" in str(caught.value) and caught.value.detail == "pyarrow"
    assert consumed == []  # hiçbir satır okunmadı
    assert not (tmp_path / "rows.parquet").exists() and leftovers(tmp_path) == []
    assert staging_entries(store) == []


def test_parquet_round_trip(store: Store, tmp_path: Path) -> None:
    pq = pytest.importorskip("pyarrow.parquet")
    report = store.export.rows(sample_rows(), COLUMNS, tmp_path / "rows.parquet", "parquet")

    table = pq.read_table(tmp_path / "rows.parquet")
    assert table.column_names == COLUMNS
    assert {name: str(table.schema.field(name).type) for name in COLUMNS} == {
        "id": "int64", "name": "string", "score": "int64", "ratio": "double", "finished": "bool",
        "extra": "string", "kickoff": "string", "note": "string"}
    assert table.to_pylist() == [
        {"id": 1, "name": "Arsenal, \"The Gunners\"", "score": 3, "ratio": 0.5, "finished": True,
         "extra": '{"stage":"Ön eleme","legs":[1,2]}', "kickoff": "2026-10-01T19:45:00+00:00", "note": "çok\nsatırlı"},
        {"id": 2, "name": "Chelsea", "score": None, "ratio": 1.25, "finished": False, "extra": None, "kickoff": None,
         "note": None},
        {"id": 3, "name": "", "score": 0, "ratio": 2.0, "finished": None, "extra": "[]",
         "kickoff": "2026-10-01T19:45:00+00:00", "note": None},
    ]
    assert report.items == 3

    stream = io.BytesIO()
    assert store.export.rows(sample_rows(), COLUMNS, stream, "parquet").bytes == len(stream.getvalue())
    stream.seek(0)
    assert pq.read_table(stream).to_pylist() == table.to_pylist()
    assert staging_entries(store) == []


def test_parquet_types_come_from_the_first_batch(store: Store, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    pq = pytest.importorskip("pyarrow.parquet")
    monkeypatch.setattr(export_mod, "PARQUET_BATCH", 2)
    rows = [{"n": 1, "x": 1}, {"n": 2, "x": 2.5}, {"n": 3, "x": 4}, {"n": None, "x": None}]
    store.export.rows(rows, ["n", "x"], tmp_path / "ok.parquet", "parquet")
    assert pq.read_table(tmp_path / "ok.parquet").to_pylist() == [
        {"n": 1, "x": 1.0}, {"n": 2, "x": 2.5}, {"n": 3, "x": 4.0}, {"n": None, "x": None}]

    with pytest.raises(StoreError, match="'n'"):
        store.export.rows([{"n": 1}, {"n": 2}, {"n": "three"}], ["n"], tmp_path / "bad.parquet", "parquet")
    assert not (tmp_path / "bad.parquet").exists() and leftovers(tmp_path) == []

    store.export.rows([], ["a"], tmp_path / "empty.parquet", "parquet")
    assert pq.read_table(tmp_path / "empty.parquet").column_names == ["a"]


def test_the_report_is_a_plain_value() -> None:
    report = ExportReport(fmt="csv", dest="", events=0, items=0, bytes=0)
    assert report.skipped == () and isinstance(report, ExportReport)
    assert export_mod.ROW_FORMATS == ("jsonl", "csv", "parquet", "sqlite")
    assert export_mod.RAW_FORMATS == ("tree", "jsonl")
