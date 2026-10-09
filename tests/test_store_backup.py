"""
Yedek biçim 2, doğrulama, geri yükleme ve saklama süreleri (plan maddesi ST-24; docs/design/01-storage.md
bölüm 9).

  * Yedekle, boş bir dizine geri yükle: mantıksal döküm (tests/store_dump.py), dosyaların içeriği, takipler,
    iş geçmişi, değişiklik günlüğü ve olay günlüğü aynıdır; katalog dosyalardan kurulur ve denetimden geçer.
  * `..` ya da mutlak yollu üyesi olan yedek, hiçbir şey yazılmadan reddedilir.
  * Boş olmayan dizine `force` olmadan geri yükleme reddedilir; `force` eski veriyi çöpe taşır ve sonra siler.
  * Biçim 1 (2.x ve ST-19 zip'i) okunabilir bir eski düzen ağacı olarak geri yüklenir.
  * Bir izleyici yazarken alınan yedek, uzlaştırmadan sonra denetimden geçen bir dizine geri yüklenir.
  * `StreamLog.prune` bölüm 9.3'ün varsayılanlarını taşır; `StreamLog.cursors` sink konumlarını listeler.
  * Servis ve `ssc backup` komutları Store'un hatalarını kodlu hatalara çevirir.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest

import store_dump
import store_fixtures as sf
import test_cli_skeleton as skeleton
from sofascore_scraper.errors import NotFoundError, UsageError
from sofascore_scraper.services.backup import BackupService
from sofascore_scraper.slices import SLICE_OK, Outcome
from sofascore_scraper.store import (
    BackupInvalid,
    BackupNotFound,
    FollowSpec,
    Ref,
    RestoreRefused,
    SchemaTooNew,
    Store,
    StreamEvent,
    open_store,
)
from sofascore_scraper.store import backup as backup_mod
from sofascore_scraper.store import streams as streams_mod
from test_cli_skeleton import CliRunner

cli = skeleton.cli

DAY = 24 * 3600.0
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
V3_EVENT = 990001
STAMP = datetime(2026, 10, 2, 21, 0, 0)


# --- yardımcılar ----------------------------------------------------------------------------------------

def file_hashes(data_dir: Path) -> Dict[str, str]:
    """
    Veri dizininin yedeğe giren veri dosyaları (v3, eski düzen ağaçları, iki değişiklik günlüğü) → içeriğin
    sha256'sı. `.gz` dosyaları açılmış içerikleriyle karşılaştırılır: aynı yük, sıkıştırılmış baytları farklı
    da olsa eşittir (ST-03). 2.x izleyicisinin kökteki dosyaları (`watch_*.json*`) yedeğe girmez.
    """
    found: Dict[str, str] = {}
    for path in sorted(data_dir.rglob("*")):
        rel = path.relative_to(data_dir).as_posix()
        if not path.is_file() or rel.split("/", 1)[0] not in backup_mod.DATA_ENTRIES:
            continue
        data = path.read_bytes()
        found[rel] = hashlib.sha256(gzip.decompress(data) if rel.endswith(".gz") else data).hexdigest()
    return found


def state_rows(store: Store) -> Dict[str, Any]:
    """Geri yüklemenin taşıması gereken durum: takipler, işler, değişiklik günlüğü, olaylar, sink konumları."""
    follows = [(f.kind, f.entity_id, f.name, f.origin, f.enabled) for f in store.follows.list()]
    jobs = [(row["id"], row["kind"], row["status"]) for row in store.jobs.list_records()]
    changes = [(row.seq, row.event_id) for row in store.changes.list(limit=10_000)]
    events = [(r.seq, r.stream, r.type) for r in store.streams.read(limit=10_000).events]
    cursors = [(c.sink, c.seq, c.last_error) for c in store.streams.cursors()]
    return {"follows": follows, "jobs": jobs, "changes": changes, "events": events, "cursors": cursors}


def rich_store(root: Path) -> Tuple[Store, sf.LegacyFixture]:
    """
    Her biçimden veri taşıyan bir dizin: eski düzen ağaçları ve `score_changes.jsonl` (canonical), v3 maçı,
    sezon listesi ve program sayfası, v3 değişiklik günlüğü, API'den eklenmiş takip, bitmiş bir iş, olay
    günlüğü ve bir sink konumu.
    """
    fixture = sf.build_fixture("canonical", root / "data")
    store = open_store(fixture.data_dir)
    basic = sf.basic_payload(sf.PL_ARS)
    basic["id"] = V3_EVENT
    store.events.put(V3_EVENT, {"event": Outcome(SLICE_OK, basic, fetched_at=NOW)})
    store.entities.put(Ref.tournament(sf.PL.id), {"seasons": Outcome(SLICE_OK, {"seasons": [
        {"name": sf.PL_2627.name, "year": sf.PL_2627.year, "editor": False, "id": sf.PL_2627.id}]}, fetched_at=NOW)})
    store.entities.put(Ref.season(sf.PL.id, sf.PL_2627.id), {("schedule", "round_1"): Outcome(
        SLICE_OK, {"events": [sf.event_payload(sf.PL_ARS)], "hasNextPage": False}, fetched_at=NOW)})
    store.changes.append({"ts_utc": "2026-10-02T12:00:00+00:00", "event_id": V3_EVENT, "home_score": 1,
                          "away_score": 0})
    store.follows.add(FollowSpec(kind="team", entity_id=42, name="Arsenal", sport="football"))
    store.jobs.create_running({"mode": "test"}, kind="sync")
    store.jobs.update(status="Completed", finished=True)
    store.streams.append("job", [StreamEvent(type="job.finished", data={"n": 1})])
    store.streams.set_cursor("hook", 1, error="HTTP 500")
    return store, fixture


def backup_all(store: Store, scope: str = "all", **kwargs: Any) -> backup_mod.BackupInfo:
    with store.lease("writer", purpose="op:backup"):
        return store.backup.create(scope, now=STAMP, **kwargs)


def into(path: str, data_dir: Path) -> str:
    """Yedeği hedef dizinin `backups/` klasörüne kopyalar (geri yükleme yalnızca oradan alır); adı döndürür."""
    (data_dir / "backups").mkdir(parents=True, exist_ok=True)
    shutil.copy(path, data_dir / "backups")
    return os.path.basename(path)


def write_zip(data_dir: Path, name: str, members: Dict[str, bytes]) -> str:
    (data_dir / "backups").mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(data_dir / "backups" / name, "w") as zf:
        for member, data in members.items():
            zf.writestr(member, data)
    return name


def manifest(**overrides: Any) -> bytes:
    document = {"format": 2, "layout_version": 3, "state_schema": 1, "scope": "data", "counts": {}}
    document.update(overrides)
    return json.dumps(document).encode("utf-8")


def snapshot(data_dir: Path) -> List[str]:
    return sorted(p.relative_to(data_dir).as_posix() for p in data_dir.rglob("*") if ".meta" not in p.parts)


# --- biçim 2: üyeler ------------------------------------------------------------------------------------

def test_the_archive_holds_state_v3_legacy_trees_and_both_change_logs(tmp_path: Path) -> None:
    store, _ = rich_store(tmp_path)
    info = backup_all(store)

    assert (info.name, info.format, info.scope) == ("backup_all_20261002_210000.zip", 2, "all")
    with zipfile.ZipFile(info.path) as zf:
        names = set(zf.namelist())
        document = json.loads(zf.read("backup.json"))
        stored = {i.filename: i.compress_type for i in zf.infolist()}
        state_copy = tmp_path / "state.db"
        state_copy.write_bytes(zf.read(".meta/state.db"))
    assert {"backup.json", ".meta/schema.json", ".meta/state.db", "score_changes.jsonl"} <= names
    assert any(n.startswith("v3/events/") for n in names) and any(n.startswith("v3/tournaments/") for n in names)
    assert any(n.startswith("changes/") for n in names)
    assert {n.split("/", 1)[0] for n in names} >= {"seasons", "matches", "match_details"}
    assert not any(n.startswith((".meta/catalog", ".meta/locks", ".meta/tmp", "backups/")) for n in names)
    # Zaten sıkıştırılmış yükler yeniden sıkıştırılmaz
    assert all(kind == zipfile.ZIP_STORED for n, kind in stored.items() if n.endswith(".gz"))
    assert stored["score_changes.jsonl"] == zipfile.ZIP_DEFLATED
    assert document["format"] == 2 and document["scope"] == "all" and document["with_env"] is False
    assert document["layout_version"] == 3 and document["state_schema"] == store._state.latest_version
    assert document["store_id"] == store.store_id
    counts = document["counts"]
    assert counts["v3_events"] == 1 and counts["api_follows"] == 1 and counts["jobs"] == 1
    assert counts["sink_cursors"] == 1 and counts["legacy_events"] > 0
    # state.db tek dosyalık, tutarlı bir kopyadır; o anki kilit sahipleri (writer, op:backup) taşınmaz
    conn = sqlite3.connect(state_copy)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("SELECT count(*) FROM leases").fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


@pytest.mark.parametrize("scope, present, absent", [
    ("state", (".meta/state.db", "config/leagues.txt"), ("v3/", "match_details/", "changes/")),
    ("data", ("v3/events/", "match_details/", "changes/", "score_changes.jsonl"), (".meta/state.db", "config/")),
])
def test_each_scope_takes_its_members(tmp_path: Path, scope: str, present: Tuple[str, ...],
                                      absent: Tuple[str, ...]) -> None:
    store, _ = rich_store(tmp_path)
    leagues = tmp_path / "leagues.txt"
    leagues.write_text("17: Premier League\n", encoding="utf-8")
    info = backup_all(store, scope, config_files=[str(leagues)])
    with zipfile.ZipFile(info.path) as zf:
        names = zf.namelist()
    assert "backup.json" in names
    for prefix in present:
        assert any(n.startswith(prefix) for n in names), prefix
    for prefix in absent:
        assert not any(n.startswith(prefix) for n in names), prefix


@pytest.mark.skipif(os.name != "posix", reason="POSIX izin bitleri")
def test_the_env_file_goes_in_only_on_request_under_config(tmp_path: Path) -> None:
    store, _ = rich_store(tmp_path)
    env = tmp_path / "secret.env"
    env.write_text("PROXY_URL=http://example.invalid:1\n", encoding="utf-8")

    plain = backup_all(store, "state")
    with_env = store.backup.create("state", env_file=str(env), now=datetime(2026, 10, 2, 21, 0, 1))

    assert not plain.with_env and with_env.with_env and with_env.name == "backup_state_with_env_20261002_210001.zip"
    assert os.stat(with_env.path).st_mode & 0o777 == 0o600
    with zipfile.ZipFile(with_env.path) as zf:
        assert zf.read("config/.env").startswith(b"PROXY_URL=")
        assert json.loads(zf.read("backup.json"))["with_env"] is True


# --- geri yükleme -----------------------------------------------------------------------------------------

def test_backup_then_restore_into_an_empty_directory_gives_an_equal_store(tmp_path: Path) -> None:
    store, fixture = rich_store(tmp_path / "source")
    info = backup_all(store)
    before_dump = store_dump.dump(fixture.data_dir, fixture.leagues)
    before_files, before_state = file_hashes(fixture.data_dir), state_rows(store)
    before_events = sorted(row.id for row in store.events.iter(_all_events()))

    target_dir = tmp_path / "target" / "data"
    name = into(info.path, target_dir)
    target = open_store(target_dir)
    old_stream_id = target.streams.head().stream_id
    report = target.backup.restore(name)

    assert report.format == 2 and not report.dry_run and report.occupied == () and report.replaced == ()
    assert report.restored == ("v3", "changes", "seasons", "matches", "match_details", "score_changes.jsonl",
                               ".meta/state.db")
    assert report.catalog_rebuilt and report.verify_ok is True and report.verify_issues == 0
    assert store_dump.diff(before_dump, store_dump.dump(target_dir, fixture.leagues)) == []
    assert file_hashes(target_dir) == before_files
    assert state_rows(target) == before_state
    assert sorted(row.id for row in target.events.iter(_all_events())) == before_events
    assert target.catalog.diff_from_rebuild() == []
    # Olay günlüğü yeni bir kimlik alır: tüketicinin sakladığı konum bu günlükte anlamsızdır
    assert target.streams.head().stream_id not in (old_stream_id, store.streams.head().stream_id)
    assert not any((target_dir / ".meta" / "tmp").iterdir()) and not any((target_dir / ".meta" / "trash").iterdir())
    # Geri yükleme kilidi bıraktı ve kendi kilit satırını bırakmadı
    assert target.lease_holder("maintenance") is None
    target.close()
    reopened = open_store(target_dir)
    assert state_rows(reopened) == before_state


def _all_events() -> Any:
    from sofascore_scraper.store import EventQuery

    return EventQuery()


def test_a_dry_run_writes_nothing_and_says_what_would_happen(tmp_path: Path) -> None:
    store, _ = rich_store(tmp_path / "source")
    info = backup_all(store)
    target_dir = tmp_path / "target" / "data"
    name = into(info.path, target_dir)
    target = open_store(target_dir)
    before = snapshot(target_dir)

    report = target.backup.restore(name, dry_run=True)

    assert report.dry_run and report.occupied == () and report.verify_ok is None and not report.catalog_rebuilt
    assert ".meta/state.db" in report.restored and report.counts["v3_events"] == 1
    assert snapshot(target_dir) == before and target.follows.list() == []
    # Kendi verisi olan bir dizinde deneme çalıştırması hata vermez, neyin yolda olduğunu söyler
    occupied = store.backup.restore(info.name, dry_run=True)
    assert set(occupied.occupied) >= {"v3", "match_details", "follows"} and "v3" in occupied.replaced


def test_restore_into_a_directory_with_data_is_refused_without_force(tmp_path: Path) -> None:
    source, _ = rich_store(tmp_path / "source")
    info = backup_all(source, "data")
    target, target_fixture = rich_store(tmp_path / "target")
    name = into(info.path, target_fixture.data_dir)
    before = file_hashes(target_fixture.data_dir)

    with pytest.raises(RestoreRefused) as caught:
        target.backup.restore(name)

    assert set(caught.value.reasons) >= {"v3", "seasons", "matches", "match_details", "follows"}
    assert file_hashes(target_fixture.data_dir) == before
    assert target.lease_holder("maintenance") is None


def test_force_moves_the_old_data_aside_and_never_mixes(tmp_path: Path) -> None:
    source_fixture = sf.build_fixture("processed_only", tmp_path / "source" / "data")
    source = open_store(source_fixture.data_dir)
    info = backup_all(source, "data")
    expected = file_hashes(source_fixture.data_dir)
    target, target_fixture = rich_store(tmp_path / "target")
    name = into(info.path, target_fixture.data_dir)
    follows = target.follows.list()

    report = target.backup.restore(name, force=True)

    assert report.force and "v3" in report.replaced and "seasons" in report.replaced
    assert report.verify_ok is True
    # Hedefin v3 ağacı, eski ağaçları ve değişiklik günlükleri gitti; yalnızca yedekteki veri kaldı
    assert file_hashes(target_fixture.data_dir) == expected
    assert target.events.get(V3_EVENT) is None
    # Yedekte state.db yok: hedefin durumu (takipler) kalır
    assert target.follows.list() == follows
    assert not any((target_fixture.data_dir / ".meta" / "trash").iterdir())
    assert target.catalog.diff_from_rebuild() == []


def test_a_failed_step_rolls_the_restore_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, _ = rich_store(tmp_path / "source")
    info = backup_all(source)
    target, target_fixture = rich_store(tmp_path / "target")
    name = into(info.path, target_fixture.data_dir)
    before_files, before_state = file_hashes(target_fixture.data_dir), state_rows(target)
    real = backup_mod.BackupManager._load_state
    calls: List[str] = []

    def failing(self: backup_mod.BackupManager, path: str) -> None:
        calls.append(path)
        if len(calls) == 1:
            real(self, path)  # yedekteki state.db yazıldı, sonra bir hata
            raise OSError(28, "No space left on device")
        real(self, path)  # geri alma: önceki state.db geri yazılır

    monkeypatch.setattr(backup_mod.BackupManager, "_load_state", failing)
    with pytest.raises(Exception) as caught:
        target.backup.restore(name, force=True)

    assert getattr(caught.value, "errno", None) == 28
    assert file_hashes(target_fixture.data_dir) == before_files
    assert state_rows(target) == before_state
    assert target.lease_holder("maintenance") is None


@pytest.mark.parametrize("member", [
    "../outside.txt",
    "v3/../../outside.txt",
    "/etc/outside.txt",
    "C:/outside.txt",
    "v3\\..\\outside.txt",
    "v3/events/./x.json",
])
def test_an_archive_with_unsafe_member_paths_is_rejected_before_anything_is_written(tmp_path: Path,
                                                                                    member: str) -> None:
    data_dir = tmp_path / "data"
    store = open_store(data_dir)
    name = write_zip(data_dir, "backup_data_20261002_210000.zip", {
        "backup.json": manifest(), "v3/events/0/990/990001/manifest.json": b"{}", member: b"evil"})
    before = snapshot(tmp_path)

    with pytest.raises(BackupInvalid):
        store.backup.restore(name)
    with pytest.raises(BackupInvalid):
        store.backup.restore(name, dry_run=True)

    assert snapshot(tmp_path) == before
    assert not (data_dir / "v3").exists() and not (data_dir / ".meta" / "tmp").exists()
    check = store.backup.verify(name)
    assert not check.ok and any("path" in problem for problem in check.problems)


def test_unknown_members_and_formats_are_rejected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    store = open_store(data_dir)
    unknown = write_zip(data_dir, "backup_data_20261002_210000.zip", {"backup.json": manifest(), "notes.txt": b"x"})
    future = write_zip(data_dir, "backup_data_20261002_210001.zip", {"backup.json": manifest(format=3)})
    broken = write_zip(data_dir, "backup_data_20261002_210002.zip", {"backup.json": b"{not json"})
    for name in (unknown, future, broken):
        with pytest.raises(BackupInvalid):
            store.backup.restore(name)
    (data_dir / "backups" / "backup_all_20261002_210003.zip").write_bytes(b"not a zip")
    with pytest.raises(BackupInvalid):
        store.backup.restore("backup_all_20261002_210003.zip")
    assert store.backup.verify("backup_all_20261002_210003.zip").format is None


def test_an_archive_from_a_newer_version_is_refused(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    store = open_store(data_dir)
    layout_name = write_zip(data_dir, "backup_data_20261002_210000.zip", {"backup.json": manifest(layout_version=99)})
    state_name = write_zip(data_dir, "backup_data_20261002_210001.zip", {"backup.json": manifest(state_schema=99)})

    for name in (layout_name, state_name):
        with pytest.raises(SchemaTooNew):
            store.backup.restore(name)
        assert not store.backup.verify(name).ok


def test_only_names_in_the_backups_folder_are_accepted(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    outside = tmp_path / "backup_all_20261002_210000.zip"
    outside.write_bytes(b"")
    for name in ("backup_all_20261002_210000.zip", "../backup_all_20261002_210000.zip", str(outside), "x.zip"):
        with pytest.raises(BackupNotFound):
            store.backup.restore(name)
        with pytest.raises(BackupNotFound):
            store.backup.verify(name)


# --- biçim 1 (2.x) --------------------------------------------------------------------------------------

def format1_zip(fixture: sf.LegacyFixture, target_dir: Path, name: str = "backup_all_20260920_090000.zip") -> str:
    """2.x / ST-19 zip'i: ayar dosyaları ve `.env` kökte, veri ağaçları `<veri dizininin adı>/<ağaç>/...`."""
    data_dir = fixture.data_dir
    (target_dir / "backups").mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target_dir / "backups" / name, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("leagues.txt", "17: Premier League\n")
        zf.writestr(".env", "PROXY_URL=http://example.invalid:1\n")
        for tree in ("seasons", "matches", "match_details"):
            for path in sorted((data_dir / tree).rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(data_dir.parent).as_posix())
    return name


def test_a_format_1_archive_restores_to_a_readable_legacy_tree(tmp_path: Path) -> None:
    fixture = sf.build_fixture("canonical", tmp_path / "source" / "data")
    source = open_store(fixture.data_dir)
    expected_events = sorted(row.id for row in source.events.iter(_all_events()))
    expected_dump = store_dump.dump(fixture.data_dir, fixture.leagues)
    target_dir = tmp_path / "target" / "data"
    name = format1_zip(fixture, target_dir)
    target = open_store(target_dir)
    assert {info.name: info.format for info in target.backup.list()} == {name: 1}

    check = target.backup.verify(name)
    report = target.backup.restore(name)

    assert check.ok and check.format == 1 and check.scope == "all"
    assert report.format == 1 and report.restored == ("seasons", "matches", "match_details")
    assert set(report.skipped) == {"leagues.txt", ".env"}  # ayarlar geri yüklenmez
    assert report.counts["legacy_events"] > 0 and report.counts["files"] == check.members - 2
    assert report.verify_ok is True
    legacy = {p: h for p, h in file_hashes(fixture.data_dir).items() if p.split("/", 1)[0] in report.restored}
    assert file_hashes(target_dir) == legacy
    assert sorted(row.id for row in target.events.iter(_all_events())) == expected_events
    restored_dump = store_dump.dump(target_dir, fixture.leagues)
    assert store_dump.diff(expected_dump["events"], restored_dump["events"]) == []


def test_a_format_1_archive_with_two_roots_is_rejected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    store = open_store(data_dir)
    name = write_zip(data_dir, "backup_all_20261002_210000.zip", {
        "data/seasons/a.json": b"{}", "other/seasons/b.json": b"{}"})
    with pytest.raises(BackupInvalid):
        store.backup.restore(name)


# --- yazan bir izleyici varken --------------------------------------------------------------------------

def test_a_backup_taken_while_a_watcher_writes_restores_to_a_directory_that_passes_verify(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _ = rich_store(tmp_path / "source")
    real_write = zipfile.ZipFile.write
    written: List[str] = []

    def write_while_watching(self: zipfile.ZipFile, filename: Any, arcname: Any = None, *args: Any,
                             **kwargs: Any) -> None:
        written.append(str(arcname))
        if str(arcname).startswith("v3/events/") and len(written) < 1000:
            written.extend(["x"] * 1000)  # bir kez: canlı servis yazmaya devam ediyor
            live = sf.basic_payload(sf.PL_LIV)
            live["id"] = V3_EVENT + 1
            store.events.put(V3_EVENT + 1, {"event": Outcome(SLICE_OK, live, fetched_at=NOW)})
            store.events.put(V3_EVENT, {"event": Outcome(SLICE_OK, {**sf.basic_payload(sf.PL_ARS), "id": V3_EVENT,
                                                                     "homeScore": {"current": 9}},
                                                         fetched_at=NOW)})
        real_write(self, filename, arcname, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", write_while_watching)
    with store.lease("live"):  # canlı servis `live` tutar; yedeği `writer` ile alan süreç onu dışlamaz
        info = backup_all(store)
    monkeypatch.setattr(zipfile.ZipFile, "write", real_write)

    target_dir = tmp_path / "target" / "data"
    name = into(info.path, target_dir)
    target = open_store(target_dir)
    report = target.backup.restore(name)
    target.catalog.reconcile(v3=True)

    assert report.catalog_rebuilt
    assert target.catalog.verify().ok
    assert target.events.get(V3_EVENT) is not None


# --- saklama ----------------------------------------------------------------------------------------------

def test_prune_keeps_everything_by_default_and_removes_by_count_or_age(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    made = [store.backup.create("state", now=datetime(2026, 9, day, 12, 0, 0)) for day in (1, 10, 20, 30)]
    assert store.backup.prune() == [] and len(store.backup.list()) == 4

    assert [i.name for i in store.backup.prune(keep=3)] == [made[0].name]
    removed = store.backup.prune(max_age_days=15, now=datetime(2026, 10, 2))
    assert [i.name for i in removed] == [made[1].name]
    assert [i.name for i in store.backup.list()] == [made[3].name, made[2].name]
    with pytest.raises(ValueError):
        store.backup.prune(keep=-1)
    with pytest.raises(ValueError):
        store.backup.prune(max_age_days=-1)


def test_stream_prune_defaults_are_seven_days_and_a_million_rows(tmp_path: Path) -> None:
    assert streams_mod.DEFAULT_PRUNE_MAX_AGE_SECONDS == 7 * DAY and streams_mod.DEFAULT_PRUNE_MAX_ROWS == 1_000_000
    store = open_store(tmp_path / "data")
    now = time.time()
    store.streams.append("live", [StreamEvent(type="live.x", ts=now - 8 * DAY), StreamEvent(type="live.x", ts=now)])

    assert store.streams.prune() == 1
    assert [r.type for r in store.streams.read().events] == ["live.x"]
    assert store.streams.prune(max_age_s=None, max_rows=None) == 0


def test_cursors_list_every_sink_with_its_last_error(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    assert store.streams.cursors() == []
    store.streams.set_cursor("webhook", 7, error="HTTP 503")
    store.streams.set_cursor("file", 3)

    found = store.streams.cursors()

    assert [(c.sink, c.seq, c.last_error) for c in found] == [("file", 3, None), ("webhook", 7, "HTTP 503")]
    assert all(abs(c.updated_at - time.time()) < 60 for c in found)


# --- servis -----------------------------------------------------------------------------------------------

def test_the_service_maps_store_errors_to_coded_errors(tmp_path: Path) -> None:
    source, _ = rich_store(tmp_path / "source")
    info = backup_all(source)
    service = BackupService(source)

    with pytest.raises(NotFoundError):
        service.restore("backup_all_20000101_000000.zip")
    with pytest.raises(NotFoundError):
        service.verify("nothing.zip")
    with pytest.raises(UsageError) as refused:
        service.restore(info.name)
    assert refused.value.code == "confirmation_required" and "v3" in refused.value.details["occupied"]
    write_zip(source.data_dir, "backup_data_20261002_210009.zip", {"backup.json": manifest(), "../x": b""})
    with pytest.raises(UsageError) as invalid:
        service.restore("backup_data_20261002_210009.zip")
    assert invalid.value.code == "invalid_request"
    assert service.verify(info.name).ok
    assert [i.name for i in service.prune(keep=2)] == []
    assert [i.name for i in service.prune(keep=1)] == [info.name]  # yalnızca en yeni (biçimsiz olan) kalır


# --- ssc backup -------------------------------------------------------------------------------------------

@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    config = tmp_path / "config"
    config.mkdir()
    (config / "leagues.txt").write_text("17: Premier League\n", encoding="utf-8")
    (config / "league_sports.json").write_text('{"17": "football"}\n', encoding="utf-8")
    env = tmp_path / "test.env"
    env.write_text("SOFASCORE_CLIENT__MAX_CONCURRENT=5\n", encoding="utf-8")
    monkeypatch.setenv("SOFASCORE_CONFIG_DIR", str(config))
    monkeypatch.setenv("SOFASCORE_ENV_FILE", str(env))
    monkeypatch.setenv("SOFASCORE_CONFIG", "none")
    yield tmp_path


def test_the_backup_commands_create_list_verify_and_restore(cli: CliRunner, configured: Path) -> None:
    source = sf.build_fixture("canonical", configured / "source" / "data").data_dir
    created = cli("backup", "create", "--data-dir", source, "--json")
    assert created.exit_code == 0, created.stdout + created.stderr
    data = created.data
    assert data["scope"] == "all" and data["format"] == 2 and not data["with_env"]
    with zipfile.ZipFile(data["path"]) as zf:
        assert {"config/leagues.txt", "config/league_sports.json"} <= set(zf.namelist())

    listed = cli("backup", "list", "--data-dir", source, "--json").data
    assert [b["name"] for b in listed["backups"]] == [data["name"]]
    assert cli("backup", "verify", data["name"], "--data-dir", source, "--json").data["ok"] is True

    target = configured / "target" / "data"
    into(data["path"], target)
    unconfirmed = cli("backup", "restore", data["name"], "--data-dir", target, "--json")
    assert unconfirmed.exit_code == 2 and unconfirmed.error["code"] == "confirmation_required"
    plan = cli("backup", "restore", data["name"], "--data-dir", target, "--dry-run", "--json").data
    assert plan["dry_run"] and plan["occupied"] == [] and "match_details" in plan["restored"]
    done = cli("backup", "restore", data["name"], "--data-dir", target, "--yes", "--json")
    assert done.exit_code == 0, done.stdout + done.stderr
    assert done.data["verify_ok"] is True and set(done.data["skipped"]) == {"config/leagues.txt",
                                                                            "config/league_sports.json"}
    again = cli("backup", "restore", data["name"], "--data-dir", target, "--yes", "--json")
    assert again.exit_code == 2 and again.error["code"] == "confirmation_required"
    assert cli("backup", "restore", data["name"], "--data-dir", target, "--yes", "--force").exit_code == 0
    missing = cli("backup", "verify", "backup_all_20000101_000000.zip", "--data-dir", target, "--json")
    assert missing.exit_code == 1 and missing.error["code"] == "not_found"


def test_backup_create_takes_the_writer_lease_and_includes_secrets_only_on_request(cli: CliRunner,
                                                                                   configured: Path) -> None:
    data_dir = configured / "data"
    store = open_store(data_dir)
    with store.lease("writer", purpose="sync"):
        for scope in ("all", "state", "data"):  # her kapsam veri ya da state.db yazar: hepsi kilidi bekler
            busy = cli("backup", "create", "--scope", scope, "--data-dir", data_dir, "--json")
            assert busy.exit_code == 6 and busy.error["code"] == "job_running"
    retired = cli("backup", "create", "--scope", "config", "--data-dir", data_dir, "--json")
    assert retired.exit_code == 2  # 2.x'in kapsamları 3.1'de kalktı
    secret = cli("backup", "create", "--scope", "state", "--include-secrets", "--data-dir", data_dir, "--json")
    assert secret.exit_code == 0 and secret.data["with_env"]
    assert [w["code"] for w in secret.json["warnings"]] == ["backup_with_secrets"]
    text = cli("backup", "list", "--data-dir", data_dir)
    assert text.exit_code == 0 and ".env" in text.stdout and "format 2" in text.stdout


def test_backup_verify_reports_problems_with_exit_code_1(cli: CliRunner, configured: Path) -> None:
    data_dir = configured / "data"
    open_store(data_dir)
    name = write_zip(data_dir, "backup_data_20261002_210000.zip", {"backup.json": manifest(), "../x": b""})
    run = cli("backup", "verify", name, "--data-dir", data_dir, "--json")
    assert run.exit_code == 1 and run.json["ok"] is True and run.json["data"]["ok"] is False
