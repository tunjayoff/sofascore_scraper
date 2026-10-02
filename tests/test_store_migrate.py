"""
Eski düzenden v3'e taşıma: `Store.migrate` (plan maddesi ST-23;
docs/design/01-storage.md bölüm 5.4).

Ölçütler (planın ST-23 maddesi):

  * Kuru çalıştırma ağacın özetini değiştirmez ve sayıları, ardından gelen gerçek çalıştırmanın yaptığına eşittir.
  * Tam bir çalıştırmadan sonra mantıksal döküm (tests/store_dump.py) aynıdır, katalog yeniden kurulmuş haline
    eşittir, derin doğrulama temizdir ve tanınmayan dosyalar `_extra/` altındadır.
  * Doğrulaması başarısız olan maçın eski dizini yerinde kalır; çalışma sürer.
  * Altı adımın her birinde kesilen çalışma, ikinci bir çalışmayla kesilmemiş çalışmanın sonucunu verir.
  * `delete_legacy` yalnızca doğrulanan kopyaları siler.
  * Yalnızca özet CSV'si olan sezon bildirilir ve yerinde kalır.

Ayrıca: değişiklik günlüğünün taşınmış kopyası (src/store/changes.py), kilitler, `migration_runs`, `limit` ile
sürdürme, turnuva seçimi. Servis ve komutlar: tests/test_cli_migrate.py.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest

import store_dump
import store_fixtures as sf
from src.store import FollowSpec, LeaseHeld, MigrationPlan, Store, StoreError, open_store
from src.store import changes as changes_mod
from src.store import layout, manifest
from src.store import migrate as migrate_mod
from src.store.migrate import EVENT_STEPS, Migrator
from test_store_put import ARS, LIV, at, basic_of, consistent, ok, pending

ROOT = Path(__file__).resolve().parents[1]

CANONICAL_EVENTS = 23  # canonical fabrikasının ayrıntısı olan maçları
CANONICAL_PAGES = 11
CANONICAL_LISTS = 5


# --- yardımcılar --------------------------------------------------------------------------------------

def tree_hash(root: Path, *, meta: bool = False, mask_times: bool = False) -> Dict[str, str]:
    """
    Ağacın tamamı: göreli yol → içerik özeti ("dir" dizinler için). `.meta/` yalnızca istenirse (WAL hariç).
    mask_times: manifestlerin `created_at` / `updated_at` alanları çıkarılır (maç dışı varlıklarda yazma anıdır).
    """
    out: Dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel.split("/")[0] == ".meta" and (not meta or rel.endswith(("-wal", "-shm"))):
            continue
        if path.is_dir():
            out[rel] = "dir"
            continue
        data = path.read_bytes()
        if mask_times and path.name == layout.MANIFEST_NAME:
            found = json.loads(data)
            found.pop("created_at", None)
            found.pop("updated_at", None)
            data = json.dumps(found, sort_keys=True).encode()
        out[rel] = hashlib.sha256(data).hexdigest()
    return out


def mtimes(root: Path, prefix: str) -> Dict[str, int]:
    return {p.relative_to(root).as_posix(): p.stat().st_mtime_ns for p in sorted((root / prefix).rglob("*"))}


def build(name: str, root: Path, *, follows: bool = True) -> Tuple[sf.LegacyFixture, Store]:
    """Fabrika dizini ve deposu. follows=True: yapılandırmadaki ligler takip tablosunda (uygulamanın aynası)."""
    fx = sf.build_fixture(name, root)
    store = open_store(fx.data_dir)
    if follows:
        for league_id, league_name in fx.leagues.items():
            store.follows.add(FollowSpec("tournament", league_id, league_name))
        store.catalog.reconcile()
    return fx, store


def reopen(store: Store) -> Store:
    data_dir = store.data_dir
    store.close()
    return open_store(data_dir)


def staging_entries(store: Store) -> List[str]:
    out = []
    for rel in (layout.TMP_DIR, layout.TRASH_DIR):
        with contextlib.suppress(FileNotFoundError):
            out += [f"{rel}/{name}" for name in os.listdir(layout.resolve(store.data_dir, rel))]
    return out


def layouts(store: Store) -> Dict[str, int]:
    return store.info(sizes=False).events_by_layout


def legacy_dir(fx: sf.LegacyFixture, event_id: int) -> Path:
    found = [d for d in fx.details if d.event_id == event_id]
    return fx.data_dir.joinpath(*found[0].path.split("/"))


def run_rows(store: Store) -> List[Dict[str, Any]]:
    conn = store._state.connection()
    return [dict(zip([c[0] for c in conn.execute("SELECT * FROM migration_runs").description], row, strict=True))
            for row in conn.execute("SELECT * FROM migration_runs ORDER BY id")]


class Crash(BaseException):
    """Süreç ölümü: hiçbir `except Exception` bloğu yakalamaz, temizlik çalışmaz."""


@pytest.fixture
def canonical(tmp_path: Path) -> Tuple[sf.LegacyFixture, Store]:
    return build("canonical", tmp_path / "data")


@pytest.fixture
def old_forms(tmp_path: Path) -> Tuple[sf.LegacyFixture, Store]:
    return build("legacy", tmp_path / "data")


# --- kuru çalıştırma ------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["canonical", "legacy"])
@pytest.mark.parametrize("options", [{}, {"delete_legacy": True, "purge_derived": True}])
def test_a_dry_run_changes_nothing_and_its_counts_are_what_the_run_then_does(
        name: str, options: Dict[str, bool], tmp_path: Path) -> None:
    fx, store = build(name, tmp_path / "data")
    before = tree_hash(fx.data_dir, meta=True)
    plan = store.migrate.plan(**options)
    assert tree_hash(fx.data_dir, meta=True) == before
    assert isinstance(plan, MigrationPlan) and plan.events > 0 and not plan.stopped
    assert plan.bytes_before > plan.bytes_after > 0

    report = store.migrate.run(**options)
    assert report.ok, (report.failed, report.conflicts, report.legacy_kept)
    assert (report.events, report.schedule_pages, report.schedule_seasons, report.season_lists, report.change_log,
            report.change_log_lines) == (plan.events, plan.schedule_pages, plan.schedule_seasons, plan.season_lists,
                                         plan.change_log, plan.change_log_lines)
    assert report.extra_files == plan.extra_files
    assert report.unconvertible == plan.unconvertible
    assert report.derived_removed == plan.derived_files
    if options.get("delete_legacy"):
        assert report.legacy_copies == plan.legacy_copies
    assert report.bytes_before == plan.bytes_before
    # Bütün maçlar örnekte (en çok 200): tahmin, yazılanın boyutuna yakındır
    assert abs(report.bytes_after - plan.bytes_after) < 0.2 * report.bytes_after
    consistent(store)


def test_the_size_estimate_samples_and_exact_compresses_everything(canonical: Tuple[sf.LegacyFixture, Store],
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    _fx, store = canonical
    monkeypatch.setattr(migrate_mod, "SAMPLE_EVENTS", 5)
    sampled = store.migrate.plan()
    exact = store.migrate.plan(exact=True)
    assert (sampled.sampled, sampled.exact) == (5, False)
    assert (exact.sampled, exact.exact) == (CANONICAL_EVENTS, True)
    assert sampled.bytes_before == exact.bytes_before
    assert sampled.bytes_after > 0 and exact.bytes_after > 0


# --- tam çalıştırma ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["canonical", "legacy"])
def test_a_full_run_keeps_the_logical_dump_and_the_legacy_tree(name: str, tmp_path: Path) -> None:
    fx, store = build(name, tmp_path / "data")
    dump_before = store_dump.dump(fx.data_dir, fx.leagues)
    legacy_before = {k: v for k, v in tree_hash(fx.data_dir).items() if not k.startswith(("v3", "changes"))}
    times_before = mtimes(fx.data_dir, "match_details")

    report = store.migrate.run()
    assert report.ok and report.events > 0 and not report.stopped
    assert store_dump.diff(dump_before, store_dump.dump(fx.data_dir, fx.leagues)) == []
    assert {k: v for k, v in tree_hash(fx.data_dir).items() if not k.startswith(("v3", "changes"))} == legacy_before
    assert mtimes(fx.data_dir, "match_details") == times_before
    assert layouts(store).get("legacy", 0) == 0
    consistent(store)
    assert staging_entries(store) == []

    # Her v3 maçı nereden geldiğini söyler; katalog eski kopyayı `legacy_path` olarak bilir
    for detail in fx.details:
        found = store.events.get(detail.event_id)
        if found is None or found.layout != "v3":
            continue
        meta = manifest.read_manifest(layout.resolve(fx.data_dir, layout.manifest_path(layout.event_dir(detail.event_id))))
        assert meta.migrated_from is not None and found.legacy_path is not None

    again = store.migrate.run()  # bitmiş bir maç iki kez dönüştürülmez
    assert (again.events, again.schedule_pages, again.season_lists) == (0, 0, 0)
    assert again.change_log in (changes_mod.COPY_NONE, changes_mod.COPY_UNCHANGED)


def test_unknown_files_are_copied_unchanged_into_extra(tmp_path: Path) -> None:
    fx = sf.build_fixture("canonical", tmp_path / "data")
    directory = legacy_dir(fx, ARS)
    (directory / "notes.txt").write_bytes(b"kept as it is\r\n")
    (directory / "more").mkdir()
    (directory / "more" / "deep.bin").write_bytes(bytes(range(256)))
    (directory / ".statistics.json.1234abcd.tmp").write_bytes(b"{")  # yarıda kalmış yazma: taşınmaz
    store = open_store(fx.data_dir)

    report = store.migrate.run()
    rel = layout.event_dir(ARS)
    extra = Path(layout.resolve(fx.data_dir, rel)) / layout.EXTRA_DIR_NAME
    assert sorted(p.relative_to(extra).as_posix() for p in extra.rglob("*") if p.is_file()) == ["more/deep.bin",
                                                                                              "notes.txt"]
    assert (extra / "notes.txt").read_bytes() == b"kept as it is\r\n"
    assert (extra / "more" / "deep.bin").read_bytes() == bytes(range(256))
    legacy_rel = directory.relative_to(fx.data_dir).as_posix()
    assert sorted(report.extra_files) == [f"{legacy_rel}/more/deep.bin", f"{legacy_rel}/notes.txt"]
    consistent(store)  # `_extra/` manifestin dışındadır ama deep verify (I9) onu bildirmez


def test_a_combined_file_with_unknown_keys_is_kept_in_extra(tmp_path: Path) -> None:
    data = tmp_path / "data"
    event = basic_of(event_id=15900001)
    combined = {"basic": event, "statistics": sf.slice_payload("statistics", event), "custom_note": {"by": "user"}}
    target = data / "match_details" / "15900001"
    target.mkdir(parents=True)
    (target / "15900001.json").write_bytes(sf.dump_json(combined))
    store = open_store(data)

    report = store.migrate.run()
    assert report.ok and report.events == 1
    copied = Path(layout.resolve(data, layout.event_dir(15900001))) / layout.EXTRA_DIR_NAME / "15900001.json"
    assert copied.read_bytes() == sf.dump_json(combined)
    assert store.events.payload(15900001, "statistics") == combined["statistics"]
    consistent(store)


# --- doğrulama hatası --------------------------------------------------------------------------------------

def test_an_event_that_fails_verification_keeps_its_legacy_directory(canonical: Tuple[sf.LegacyFixture, Store],
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    fx, store = canonical
    original = migrate_mod._read_back
    verifying: List[int] = []
    real_verify = Migrator._verify_staged

    def verify(self: Migrator, event: Any, found: Any, staged: str, extras: Any) -> None:
        verifying.append(event.event_id)
        return real_verify(self, event, found, staged, extras)

    def broken(path: str) -> bytes:
        """Geri okuma, bir maçın hazırlığında bozuk bayt verir (disk ya da sıkıştırma hatası)."""
        return b'{"id": 1}' if verifying and verifying[-1] == ARS else original(path)

    monkeypatch.setattr(Migrator, "_verify_staged", verify)
    monkeypatch.setattr(migrate_mod, "_read_back", broken)
    directory = legacy_dir(fx, ARS)
    before = tree_hash(directory)

    report = store.migrate.run()
    assert [(issue.kind, issue.key) for issue in report.failed] == [("failed", str(ARS))]
    assert "read-back" in report.failed[0].detail or "does not read back" in report.failed[0].detail
    assert report.events == CANONICAL_EVENTS - 1 and not report.ok
    assert tree_hash(directory) == before
    assert not Path(layout.resolve(fx.data_dir, layout.event_dir(ARS))).exists()
    assert store.events.get(ARS).layout == "legacy"
    assert staging_entries(store) == [] and pending(store) == []
    consistent(store)

    monkeypatch.setattr(migrate_mod, "_read_back", original)
    second = store.migrate.run()
    assert second.ok and second.events == 1 and store.events.get(ARS).layout == "v3"


def test_a_legacy_directory_that_cannot_be_read_is_reported_and_left_in_place(
        canonical: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = canonical
    directory = legacy_dir(fx, LIV)
    (directory / "basic.json").write_bytes(b"{not json")
    store.catalog.reconcile(deep=True)  # testin elle bozduğunu katalog görsün (imza zamanı aynı kalabilir)
    rel = directory.relative_to(fx.data_dir).as_posix()

    plan = store.migrate.plan(delete_legacy=True)
    assert [(i.kind, i.key, i.path) for i in plan.unconvertible if i.path == rel] == [("unrecognised", "corrupt", rel)]
    report = store.migrate.run(delete_legacy=True)
    assert report.events == CANONICAL_EVENTS - 1 and report.unconvertible == plan.unconvertible
    assert (directory / "basic.json").read_bytes() == b"{not json"  # maç değil: taşınmaz, silinmez
    consistent(store)


# --- kesilen çalışma -----------------------------------------------------------------------------------------

def _crashing(step: str) -> Any:
    def checkpoint(self: Migrator, reached: str) -> None:
        if reached == step:
            raise Crash(step)
    return checkpoint


@pytest.mark.parametrize("step", EVENT_STEPS)
def test_a_run_killed_at_any_step_is_finished_by_the_next_run(step: str, tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_fx, clean = build("canonical", tmp_path / "clean")
    assert clean.migrate.run(delete_legacy=True, purge_derived=True).ok
    expected = tree_hash(clean.data_dir, mask_times=True)

    fx, store = build("canonical", tmp_path / "data")
    with monkeypatch.context() as patch:
        patch.setattr(Migrator, "_checkpoint", _crashing(step))
        with pytest.raises(Crash):
            store.migrate.run(delete_legacy=True, purge_derived=True)
    store = reopen(store)  # yeni süreç: açılış yarım yazmaları toparlar
    report = store.migrate.run(delete_legacy=True, purge_derived=True)
    assert report.ok, (report.failed, report.conflicts, report.legacy_kept)

    assert tree_hash(fx.data_dir, mask_times=True) == expected
    assert staging_entries(store) == []
    assert layouts(store) == {"v3": CANONICAL_EVENTS, "listing": layouts(clean)["listing"]}
    consistent(store)
    assert [row["finished_at"] is not None for row in run_rows(store)] == [False, True]


def test_limit_and_should_stop_end_the_run_between_two_events(canonical: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = canonical
    dump_before = store_dump.dump(fx.data_dir, fx.leagues)
    plan = store.migrate.plan(limit=5)
    assert (plan.events, plan.stopped, plan.schedule_pages, plan.season_lists) == (5, True, 0, 0)

    first = store.migrate.run(limit=5)
    assert (first.events, first.stopped, first.schedule_pages, first.change_log) == (5, True, 0, "none")
    assert layouts(store)["legacy"] == CANONICAL_EVENTS - 5
    consistent(store)

    calls = iter(range(100))
    second = store.migrate.run(should_stop=lambda: next(calls) >= 3)
    assert (second.events, second.stopped) == (3, True)

    rest = store.migrate.run()
    assert rest.events == CANONICAL_EVENTS - 8 and not rest.stopped
    assert rest.schedule_pages == CANONICAL_PAGES and rest.season_lists == CANONICAL_LISTS
    assert store_dump.diff(dump_before, store_dump.dump(fx.data_dir, fx.leagues)) == []
    consistent(store)


def test_a_tournament_scope_converts_only_that_tournament(canonical: Tuple[sf.LegacyFixture, Store]) -> None:
    _fx, store = canonical
    nba = [row.id for row in store.events.list(_query(sf.NBA.id)).items if row.layout == "legacy"]
    report = store.migrate.run(tournaments=[sf.NBA.id])
    assert report.events == len(nba) > 0
    assert all(store.events.get(event_id).layout == "v3" for event_id in nba)
    assert layouts(store)["legacy"] == CANONICAL_EVENTS - len(nba)
    assert report.change_log == "none"  # günlük seçimsiz bir çalışmanın işi
    assert _list_layout(store, sf.NBA.id) == "v3" and _list_layout(store, sf.PL.id) == "legacy"
    consistent(store)


def _list_layout(store: Store, tournament_id: int) -> str:
    row = store._catalog.connection().execute(
        "SELECT layout FROM entity_slices WHERE kind = 'tournament' AND entity_id = ? AND key = 'seasons'",
        (tournament_id,)).fetchone()
    return str(row[0])


def _query(tournament_id: int) -> Any:
    from src.store import EventQuery, Scope

    return EventQuery(scope=Scope(tournament_ids=(tournament_id,)), limit=500)


# --- eski kopyaların silinmesi ---------------------------------------------------------------------------

def test_delete_legacy_removes_verified_copies_and_empty_folders(canonical: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = canonical
    dump_before = store_dump.dump(fx.data_dir, fx.leagues)
    assert store.migrate.run().ok
    report = store.migrate.run(delete_legacy=True)
    assert report.ok and report.events == 0
    assert report.legacy_copies == CANONICAL_EVENTS
    assert report.schedule_files_deleted == CANONICAL_PAGES and report.season_list_files_deleted == CANONICAL_LISTS
    assert report.change_log_deleted and not (fx.data_dir / "score_changes.jsonl").exists()
    assert store_dump.diff(dump_before, store_dump.dump(fx.data_dir, fx.leagues)) == []
    assert all(store.events.get(d.event_id).legacy_path is None for d in fx.details)
    # Boşalan lig ve sezon dizinleri gider; köklerin kendisi kalır
    assert [p.name for p in (fx.data_dir / "match_details").iterdir()] == []
    assert (fx.data_dir / "seasons").is_dir() and list((fx.data_dir / "seasons").iterdir()) == []
    leftover = sorted(p.relative_to(fx.data_dir).as_posix() for p in (fx.data_dir / "matches").rglob("*") if p.is_file())
    assert leftover and all(name.endswith(("_summary.csv", "_summary.json")) for name in leftover)  # türetilmiş
    consistent(store)
    assert staging_entries(store) == []


def test_delete_legacy_keeps_a_copy_that_is_newer_than_its_v3_copy(canonical: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = canonical
    assert store.migrate.run().ok
    stats = legacy_dir(fx, ARS) / "statistics.json"
    stats.write_bytes(sf.dump_json({"statistics": [{"period": "ALL", "groups": [{"name": "edited by hand"}]}]}))
    future = 4_000_000_000
    os.utime(stats, (future, future))  # eski kopya v3'tekinden yeni ve farklı: veri kaybı olurdu
    store.catalog.reconcile(deep=True)

    report = store.migrate.run(delete_legacy=True)
    assert [(issue.kind, issue.key) for issue in report.legacy_kept] == [("kept", str(ARS))]
    assert "statistics" in report.legacy_kept[0].detail
    assert legacy_dir(fx, ARS).is_dir() and report.legacy_copies == CANONICAL_EVENTS - 1
    assert not report.ok
    consistent(store)


def test_a_promoted_event_gets_its_extra_files_before_its_legacy_copy_is_deleted(tmp_path: Path) -> None:
    fx = sf.build_fixture("canonical", tmp_path / "data")
    (legacy_dir(fx, ARS) / "notes.txt").write_bytes(b"user notes")
    store = open_store(fx.data_dir)
    result = store.events.put(ARS, {"event": ok(basic_of(homeScore={"current": 9}), at(60))})
    assert result.promoted
    extra = Path(layout.resolve(fx.data_dir, layout.event_dir(ARS))) / layout.EXTRA_DIR_NAME
    assert not extra.exists()  # yükseltme `_extra/` kopyalamaz

    report = store.migrate.run(delete_legacy=True)
    assert report.ok, report.legacy_kept
    assert (extra / "notes.txt").read_bytes() == b"user notes"
    assert not legacy_dir(fx, ARS).exists()
    assert store.events.payload(ARS)["homeScore"] == {"current": 9}  # v3'teki daha yeni yük kalır
    consistent(store)


def test_delete_legacy_keeps_a_v3_copy_that_does_not_match_its_manifest(
        canonical: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = canonical
    assert store.migrate.run().ok
    payload = Path(layout.resolve(fx.data_dir, layout.slice_path(layout.event_dir(LIV), "statistics")))
    payload.write_bytes(b"not gzip")

    report = store.migrate.run(delete_legacy=True)
    assert [(issue.kind, issue.key) for issue in report.legacy_kept] == [("kept", str(LIV))]
    assert legacy_dir(fx, LIV).is_dir()


def test_duplicates_of_an_event_are_all_removed_once_verified(old_forms: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = old_forms
    dump_before = store_dump.dump(fx.data_dir, fx.leagues)
    report = store.migrate.run(delete_legacy=True)
    assert report.ok, report.legacy_kept
    # Aynı maçın iki kopyası da (ikisi de doğrulandı) gider; olay yükü olmayan dizin maç değildir
    assert report.legacy_copies == len(fx.details) - 1
    assert store_dump.diff(dump_before, store_dump.dump(fx.data_dir, fx.leagues)) == []
    remaining = sorted(p.relative_to(fx.data_dir).as_posix() for p in (fx.data_dir / "match_details").rglob("*")
                       if p.is_file())
    assert all("17018572" in name for name in remaining)  # olay yükü olmayan dizin: maç değil, yerinde kalır
    assert any(issue.kind == "unrecognised" and "17018572" in issue.path for issue in report.unconvertible)
    consistent(store)


# --- özetler ve türetilmiş dosyalar -------------------------------------------------------------------------

def test_seasons_with_summary_files_only_are_reported_and_left_alone(old_forms: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = old_forms
    summary = fx.data_dir / "matches" / "17_Premier_League" / "76986_Premier_League_25_26_matches.csv"
    before = summary.read_bytes()
    plan = store.migrate.plan(purge_derived=True)
    assert [(i.kind, i.key, i.path) for i in plan.unconvertible if i.kind == "unconvertible"] == [
        ("unconvertible", "17/76986", "matches/17_Premier_League/76986_Premier_League_25_26_matches.csv")]
    assert summary.relative_to(fx.data_dir).as_posix() not in plan.derived_files

    report = store.migrate.run(delete_legacy=True, purge_derived=True)
    assert report.ok
    assert summary.read_bytes() == before
    assert [s.id for s in store.entities.seasons(sf.PL.id)]  # sezon okunmaya devam eder
    assert store.events.count(_query(sf.PL.id)) > 0
    consistent(store)


def test_purge_derived_removes_summaries_of_paged_seasons_and_processed(
        canonical: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = canonical
    processed = fx.data_dir / "match_details" / "processed"
    processed.mkdir()
    (processed / "all_matches_1.csv").write_bytes(b"a,b\r\n")
    store.catalog.reconcile()
    summaries = sorted(p.relative_to(fx.data_dir).as_posix() for p in (fx.data_dir / "matches").rglob("*_summary.*"))

    plan = store.migrate.plan(purge_derived=True)
    assert plan.derived_files == sorted(summaries + ["match_details/processed/all_matches_1.csv"])
    report = store.migrate.run(purge_derived=True)
    assert report.derived_removed == plan.derived_files
    assert not any((fx.data_dir / "matches").rglob("*_summary.*"))
    assert processed.is_dir() and list(processed.iterdir()) == []
    consistent(store)


def test_the_name_only_season_list_needs_the_follows(tmp_path: Path) -> None:
    """Adında turnuva kimliği olmayan liste takiplerdeki adla çözülür; çözülemezse bildirilir ve yerinde kalır."""
    fx, store = build("legacy", tmp_path / "data", follows=False)
    report = store.migrate.run(delete_legacy=True)
    assert [(i.kind, i.path) for i in report.unconvertible if i.kind == "unresolved"] == [
        ("unresolved", "seasons/LaLiga_seasons.json")]
    assert (fx.data_dir / "seasons" / "LaLiga_seasons.json").is_file()


# --- değişiklik günlüğü -----------------------------------------------------------------------------------

def test_the_change_log_is_copied_with_its_sequence_numbers(canonical: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = canonical
    legacy_file = fx.data_dir / "score_changes.jsonl"
    rows_before = [(row.seq, row.row) for row in store.changes.list()]
    assert rows_before

    report = store.migrate.run()
    copy = fx.data_dir / "changes" / "0000-legacy.jsonl"
    assert report.change_log == changes_mod.COPY_CREATED and copy.read_bytes() == legacy_file.read_bytes()
    assert [(row.seq, row.row) for row in store.changes.list()] == rows_before
    assert {row.segment for row in store.changes.list()} == {changes_mod.LEGACY_COPY}
    consistent(store)

    # 2.x eski dosyaya satır eklediyse (aynı dizinde iki sürüm desteklenmez ama olur) sonraki çalışma kopyayı uzatır
    with open(legacy_file, "ab") as f:
        f.write(json.dumps({"ts_utc": "2026-10-01T09:00:00Z", "event_id": LIV}).encode() + b"\n")
    store.catalog.reconcile()
    assert store.migrate.plan().change_log == changes_mod.COPY_EXTENDED
    assert store.migrate.run().change_log == changes_mod.COPY_EXTENDED
    assert copy.read_bytes() == legacy_file.read_bytes()
    assert store.changes.last_seq() == rows_before[-1][0] + 1
    consistent(store)

    # Yeni satır sıradan devam eder; kopya numarası satır numarası olan bir dosyadır (I7 boşluk saymaz)
    seq = store.changes.append({"ts_utc": "2026-10-02T10:00:00Z", "event_id": ARS, "changed": {"x": [1, 2]}})
    assert seq == rows_before[-1][0] + 2
    consistent(store)

    deleted = store.migrate.run(delete_legacy=True)
    assert deleted.change_log == changes_mod.COPY_UNCHANGED and deleted.change_log_deleted
    assert not legacy_file.exists() and len(store.changes.list()) == len(rows_before) + 2
    consistent(store)


def test_a_change_log_copy_that_differs_is_a_conflict(canonical: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = canonical
    copy = fx.data_dir / "changes" / "0000-legacy.jsonl"
    copy.parent.mkdir()
    copy.write_bytes(b'{"ts_utc": "2026-01-01T00:00:00Z", "event_id": 1}\n')
    store.catalog.reconcile()
    report = store.migrate.run(delete_legacy=True)
    assert report.change_log == changes_mod.COPY_CONFLICT and not report.change_log_deleted
    assert [(i.kind, i.key) for i in report.conflicts] == [("conflict", "change_log")]
    assert (fx.data_dir / "score_changes.jsonl").is_file()


def test_segments_prefer_the_copy_of_the_legacy_file(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "changes").mkdir(parents=True)
    line = b'{"ts_utc": "2026-01-01T00:00:00Z", "event_id": 1}\n'
    (data / "score_changes.jsonl").write_bytes(line)
    assert changes_mod.segments(data) == [changes_mod.LEGACY_SEGMENT]
    (data / "changes" / "0000-legacy.jsonl").write_bytes(line)
    (data / "changes" / "2026-01.jsonl").write_bytes(b"")
    problems: List[Any] = []
    assert changes_mod.segments(data, problems) == [changes_mod.LEGACY_COPY, "changes/2026-01.jsonl"]
    assert problems == []  # kopya artık tanınmayan bir ad değil
    assert [line.seq for line in changes_mod.read_segment(data, changes_mod.LEGACY_COPY)] == [1]


# --- kilitler ve kayıtlar ------------------------------------------------------------------------------------

HOLDER = """
import sys
from src.store import open_store
lease = open_store(sys.argv[1]).lease(sys.argv[2], purpose=sys.argv[3])
print("ready", flush=True)
sys.stdin.readline()
lease.release()
"""


@contextlib.contextmanager
def other_process(data_dir: Path, lease: str, purpose: str) -> Iterator[subprocess.Popen]:
    """Kilidi tutan ayrı bir süreç (tests/test_single_match_blocked.py ile aynı yardımcı)."""
    proc = subprocess.Popen([sys.executable, "-c", HOLDER, str(data_dir), lease, purpose], cwd=ROOT,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None
        if proc.stdout.readline().strip() != "ready":
            proc.kill()
            pytest.fail(f"helper process did not start: {proc.communicate()[1]}")
        yield proc
    finally:
        if proc.poll() is None:
            _out, err = proc.communicate("\n", timeout=60)
            assert proc.returncode == 0, err
        else:
            proc.communicate()


@pytest.mark.parametrize("lease,purpose", [("live", "live"), ("watcher:football", "watch"), ("writer", "job")])
def test_a_run_is_refused_while_a_writer_live_service_or_watcher_runs(
        lease: str, purpose: str, canonical: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = canonical
    before = tree_hash(fx.data_dir)
    with other_process(fx.data_dir, lease, purpose):
        with pytest.raises(LeaseHeld):
            store.migrate.run()
        assert store.migrate.plan().events == CANONICAL_EVENTS  # kuru çalıştırma kilit istemez
    assert tree_hash(fx.data_dir) == before
    assert store.lease_holder("writer") is None and store.lease_holder("live") is None
    assert store.migrate.run().ok  # kilit bırakılınca çalışır


def test_each_run_leaves_a_row_in_migration_runs(canonical: Tuple[sf.LegacyFixture, Store]) -> None:
    _fx, store = canonical
    store.migrate.plan()
    assert run_rows(store) == []  # kuru çalıştırma hiçbir şey yazmaz
    report = store.migrate.run()
    rows = run_rows(store)
    assert len(rows) == 1 and rows[0]["id"] == report.run_id
    row = rows[0]
    assert (row["dry_run"], row["delete_legacy"], row["events_done"], row["events_failed"]) == (
        0, 0, CANONICAL_EVENTS, 0)
    assert row["bytes_before"] == report.bytes_before and row["bytes_after"] == report.bytes_after
    summary = json.loads(row["report_json"])
    assert summary["events"] == CANONICAL_EVENTS and summary["failed_count"] == 0
    assert row["finished_at"] >= row["started_at"]


def test_a_readonly_store_is_not_migrated(canonical: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, _store = canonical
    with pytest.raises(StoreError):
        open_store(fx.data_dir, readonly=True).migrate.run()


def test_arguments_are_checked(canonical: Tuple[sf.LegacyFixture, Store]) -> None:
    _fx, store = canonical
    for bad in ({"limit": 0}, {"limit": True}, {"tournaments": ["17"]}, {"tournaments": [-1]}):
        with pytest.raises(ValueError):
            store.migrate.plan(**bad)
        with pytest.raises(ValueError):
            store.migrate.run(**bad)
