"""
`ssc migrate`, `ssc catalog rebuild|verify|reconcile` ve `MaintenanceService.migrate` / `rebuild_catalog` /
`verify_catalog` / `reconcile_catalog` (plan maddesi ST-23; docs/design/02-services.md bölüm 2.7 ve 4.1).

Taşımanın kendisi tests/test_store_migrate.py'de sınanır; burada servisin kuralları (onay, kilitler) ve komutların
zarfları, metinleri ve çıkış kodları. `catalog` komutları eski `scripts/catalog_tool.py`'nin yerini alır; o
betiğin testleri (tests/test_store_indexer.py ve tests/test_store_shadow.py'deki dört test) buraya taşındı.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Tuple

import pytest

import store_fixtures as sf
import test_cli_skeleton as skeleton
from sofascore_scraper.services.maintenance import MaintenanceService
from sofascore_scraper.store import FollowSpec, LeaseHeld, MigrationPlan, MigrationReport, Store, open_store
from sofascore_scraper.store import files
from sofascore_scraper.store import migrate as migrate_mod
from sofascore_scraper.store.migrate import Migrator
from test_cli_skeleton import CliRunner
from test_store_migrate import (
    CANONICAL_EVENTS,
    CANONICAL_PAGES,
    ROOT,
    build,
    layouts,
    other_process,
    tree_hash,
)
from test_store_put import LIV, consistent

cli = skeleton.cli


@pytest.fixture
def canonical(tmp_path: Path) -> Tuple[sf.LegacyFixture, Store]:
    return build("canonical", tmp_path / "data")


@pytest.fixture
def old_forms(tmp_path: Path) -> Tuple[sf.LegacyFixture, Store]:
    return build("legacy", tmp_path / "data")


# --- bakım servisi: taşıma --------------------------------------------------------------------------------

def test_the_service_needs_a_confirmation_for_what_deletes(canonical: Tuple[sf.LegacyFixture, Store]) -> None:
    _fx, store = canonical
    service = MaintenanceService(store=store)
    with pytest.raises(ValueError, match="confirm"):
        service.migrate(delete_legacy=True)
    with pytest.raises(ValueError, match="confirm"):
        service.migrate(purge_derived=True)
    with pytest.raises(ValueError, match="dry-run"):
        service.migrate(exact=True)
    assert isinstance(service.migrate(dry_run=True, delete_legacy=True), MigrationPlan)
    assert isinstance(service.migrate(), MigrationReport)
    assert service.migrate(delete_legacy=True, confirm=True).legacy_copies == CANONICAL_EVENTS


# --- bakım servisi: katalog -------------------------------------------------------------------------------

def test_the_service_rebuilds_verifies_and_reconciles_under_the_maintenance_lease(
        canonical: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = canonical
    service = MaintenanceService(store=store)
    assert service.rebuild_catalog().completed
    assert service.verify_catalog(deep=True).ok
    assert service.reconcile_catalog(deep=True).verify.ok
    with pytest.raises(ValueError):
        service.rebuild_catalog(mode="sideways")
    with other_process(fx.data_dir, "writer", "job"):
        for call in (service.rebuild_catalog, lambda: service.verify_catalog(repair=True),
                     lambda: service.reconcile_catalog(deep=True)):
            with pytest.raises(LeaseHeld):
                call()
        assert service.verify_catalog().ok  # hızlı doğrulama ve uzlaştırma kilit istemez
        assert not service.reconcile_catalog().changed
    with store.lease("maintenance", purpose="op:test"):
        assert service.rebuild_catalog(mode="in_place").completed  # bu süreç tutuyorsa onun altında


# --- CLI: migrate ------------------------------------------------------------------------------------------

def test_cli_migrate_dry_run_then_run(canonical: Tuple[sf.LegacyFixture, Store], cli: CliRunner) -> None:
    fx, store = canonical
    before = tree_hash(fx.data_dir)
    dry = cli("--data-dir", fx.data_dir, "--json", "migrate", "--dry-run", "--delete-legacy")
    assert dry.exit_code == 0, dry.stdout
    data = dry.data
    assert data["dry_run"] is True and data["events"] == CANONICAL_EVENTS
    assert data["legacy_copies"] == CANONICAL_EVENTS and data["schedule_pages"] == CANONICAL_PAGES
    assert tree_hash(fx.data_dir) == before

    text = cli("--data-dir", fx.data_dir, "migrate", "--dry-run", "--exact")
    assert text.exit_code == 0 and "Dry run, nothing was changed" in text.stdout
    assert "computed from every match" in text.stdout

    ran = cli("--data-dir", fx.data_dir, "--json", "migrate")
    assert ran.exit_code == 0, ran.stdout
    assert ran.data["events"] == CANONICAL_EVENTS and ran.data["ok"] is True and ran.data["dry_run"] is False
    store = open_store(fx.data_dir)
    assert layouts(store).get("legacy", 0) == 0
    consistent(store)

    deleted = cli("--data-dir", fx.data_dir, "migrate", "--delete-legacy", "--yes")
    assert deleted.exit_code == 0, deleted.stdout + deleted.stderr
    assert f"Old copies deleted: {CANONICAL_EVENTS} match folders" in deleted.stdout


@pytest.mark.parametrize("argv,code,error", [
    (("migrate", "--delete-legacy"), 2, "confirmation_required"),
    (("migrate", "--purge-derived"), 2, "confirmation_required"),
    (("migrate", "--exact"), 2, "invalid_request"),
    (("migrate", "--limit", "0"), 2, "invalid_request"),
    (("migrate", "--tournament", "x"), 2, "invalid_request"),
])
def test_cli_migrate_usage_errors(argv: Tuple[str, ...], code: int, error: str, tmp_path: Path,
                                  cli: CliRunner) -> None:
    result = cli("--data-dir", tmp_path / "data", "--json", *argv)
    assert result.exit_code == code and result.error["code"] == error
    assert not (tmp_path / "data").exists()  # hiçbir şey yaratılmadı


def test_cli_migrate_with_a_failed_event_is_a_partial_success(canonical: Tuple[sf.LegacyFixture, Store],
                                                              cli: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, _store = canonical
    real_verify = Migrator._verify_staged

    def verify(self: Migrator, event: Any, found: Any, staged: str, extras: Any) -> None:
        if event.event_id == LIV:
            raise migrate_mod._Rejected(f"Event {LIV}: injected read-back mismatch", path=staged)
        return real_verify(self, event, found, staged, extras)

    monkeypatch.setattr(Migrator, "_verify_staged", verify)
    result = cli("--data-dir", fx.data_dir, "migrate")
    assert result.exit_code == 3, result.stdout + result.stderr
    assert f"Not converted, the old copy is kept (1):\n  {LIV}:" in result.stdout
    as_json = cli("--data-dir", fx.data_dir, "--json", "migrate")
    assert as_json.exit_code == 3 and as_json.data["ok"] is False and len(as_json.data["failed"]) == 1


def test_cli_migrate_mirrors_the_configured_leagues_first(tmp_path: Path, cli: CliRunner) -> None:
    """`<ad>_seasons.json` yapılandırmadaki lig adıyla çözülür (komut ligleri takip tablosuna yansıtır)."""
    data = tmp_path / "data"
    (data / "seasons").mkdir(parents=True)
    (data / "seasons" / "Premier_League_seasons.json").write_bytes(sf.dump_json(
        {"seasons": [{"id": sf.PL_2627.id, "name": sf.PL_2627.name, "year": sf.PL_2627.year}]}))
    result = cli("--data-dir", data, "--json", "migrate")  # tests/conftest.py: leagues.txt "Premier League: 17"
    assert result.exit_code == 0, result.stdout
    assert result.data["season_lists"] == 1 and result.data["unconvertible"] == []
    assert [s.id for s in open_store(data).entities.seasons(sf.PL.id)] == [sf.PL_2627.id]
    # Taşıma yalnızca var olan eski dizinleri okur: bağlam 2.x'in alt dizinlerini artık kurmaz (ST-28, P30)
    assert not (data / "match_details").exists() and not (data / "datasets").exists()


def test_cli_migrate_reads_old_folders_that_exist_and_creates_none(canonical: Tuple[sf.LegacyFixture, Store],
                                                                   tmp_path: Path, cli: CliRunner) -> None:
    """
    Bağlam `match_details/` ve `datasets/` kurmaz (ST-28, P30): var olan eski ağaç yine okunur ve taşınır; eski
    dizini olmayan bir veri dizininde taşıma boş geçer ve eski dizin kurmaz.
    """
    fx, _store = canonical
    assert (Path(fx.data_dir) / "match_details").is_dir()  # eski ağaç önceden vardı
    ran = cli("--data-dir", fx.data_dir, "--json", "migrate")
    assert ran.exit_code == 0 and ran.data["events"] == CANONICAL_EVENTS, ran.stdout
    assert layouts(open_store(fx.data_dir)).get("legacy", 0) == 0

    empty = tmp_path / "empty"
    result = cli("--data-dir", empty, "--json", "migrate")
    assert result.exit_code == 0 and result.data["events"] == 0, result.stdout
    assert not (empty / "match_details").exists() and not (empty / "datasets").exists()


# --- CLI: catalog (eski scripts/catalog_tool.py'nin testleri) ---------------------------------------------

def test_cli_catalog_rebuild_verify_and_reconcile(old_forms: Tuple[sf.LegacyFixture, Store], cli: CliRunner) -> None:
    fx, store = old_forms
    data = fx.data_dir
    store.close()

    built = cli("--data-dir", data, "--json", "catalog", "rebuild")
    assert built.exit_code == 0, built.stdout
    assert (built.data["completed"], built.data["events"]) == (True, 9)
    assert {p["kind"] for p in built.data["problems"]} == {"no_event_payload", "corrupt"}
    assert len(built.data["superseded"]) == 1

    text = cli("--data-dir", data, "catalog", "rebuild", "--mode", "in_place")
    assert text.exit_code == 0 and "Catalog rebuilt (in_place): 9 matches" in text.stdout
    assert "Copies that are not used (1):" in text.stdout

    checked = cli("--data-dir", data, "--json", "catalog", "verify", "--deep")
    assert checked.exit_code == 0 and checked.data["ok"] is True and checked.data["events_read"] == 9

    files.remove_tree(data / "match_details" / "16867839")
    broken = cli("--data-dir", data, "catalog", "verify")
    assert broken.exit_code == 1
    assert ("I1 no_event_directory (a catalog row without a valid event directory) match 16867839" in broken.stdout
            and "1 inconsistencies are left" in broken.stdout)
    # Kod ve ayrıntı İngilizce kalır (Store'un metni); açıklama uygulama dilindedir (B2)
    assert "a catalog row but no valid event directory (" in broken.stdout
    turkish = cli("--data-dir", data, "--lang", "tr", "catalog", "verify")
    assert "I1 no_event_directory (katalogda satırı olan maçın geçerli bir dizini yok) maç 16867839" in turkish.stdout
    assert "a catalog row but no valid event directory (" in turkish.stdout
    issue = cli("--data-dir", data, "--json", "catalog", "verify").data["issues"][0]
    assert issue["kind"] == "no_event_directory" and issue["detail"].startswith("a catalog row but no valid event")
    repaired = cli("--data-dir", data, "catalog", "verify", "--repair")
    assert repaired.exit_code == 0 and "[repaired]" in repaired.stdout
    assert cli("--data-dir", data, "catalog", "verify").exit_code == 0

    files.remove_tree(data / "match_details" / "17018554")  # düz (L3) kayıt; başka kopyası yok
    reconciled = cli("--data-dir", data, "--json", "catalog", "reconcile")
    assert reconciled.exit_code == 0 and reconciled.data["changed"] is True
    deep = cli("--data-dir", data, "catalog", "reconcile", "--deep")
    assert deep.exit_code == 0 and "The catalog matches the files." in deep.stdout

    usage = cli("--data-dir", data, "--json", "catalog", "rebuild", "--mode", "sideways")
    assert usage.exit_code == 2 and usage.error["code"] == "invalid_request"


def test_cli_catalog_reconcile_uses_the_follows_and_the_lease(tmp_path: Path, cli: CliRunner) -> None:
    data = tmp_path / "data"
    (data / "seasons").mkdir(parents=True)
    (data / "seasons" / "Premier_League_seasons.json").write_bytes(sf.dump_json(
        {"seasons": [{"id": sf.PL_2627.id, "name": sf.PL_2627.name, "year": sf.PL_2627.year}]}))
    store = open_store(data)
    store.follows.add(FollowSpec("tournament", sf.PL.id, "Premier League"))
    assert store.entities.seasons(sf.PL.id) == []  # açılışta takip yoktu
    store.close()

    # Komut depoyu açar ama açılıştaki uzlaştırmayı çalıştırmaz: değişikliği komutun kendisi bulur
    report = cli("--data-dir", data, "--json", "catalog", "reconcile")
    assert report.exit_code == 0
    assert (report.data["changed"], report.data["season_lists"], report.data["problems"]) == (True, 1, [])

    with other_process(data, "writer", "job"):
        refused = cli("--data-dir", data, "--json", "catalog", "rebuild")
        assert refused.exit_code == 6 and refused.error["code"] == "job_running"
        assert cli("--data-dir", data, "catalog", "reconcile").exit_code == 0  # hızlı uzlaştırma kilit istemez
    assert cli("--data-dir", data, "catalog", "rebuild").exit_code == 0
    assert [s.id for s in open_store(data).entities.seasons(sf.PL.id)] == [sf.PL_2627.id]


def test_cli_catalog_runs_as_a_subprocess(tmp_path: Path) -> None:
    box = skeleton.Sandbox.create(tmp_path / "box")
    fx = sf.build_fixture("canonical", box.root / "data")
    out = box.run("--json", "catalog", "rebuild")
    assert out.exit_code == 0, out.stderr
    assert json.loads(out.stdout)["data"]["events"] == len(fx.detail_ids)
    dry = box.run("--json", "migrate", "--dry-run")
    assert dry.exit_code == 0, dry.stderr
    assert json.loads(dry.stdout)["data"]["events"] == len(fx.detail_ids)


def test_the_removed_scripts_are_gone() -> None:
    assert not (ROOT / "scripts" / "catalog_tool.py").exists()
    assert not (ROOT / "scripts" / "migrate_match_details.py").exists()



@pytest.mark.parametrize("lease,purpose,code", [("writer", "job", "job_running"), ("live", "live", "instance_running"),
                                                ("watcher:football", "watch", "instance_running")])
def test_cli_migrate_is_refused_while_another_process_writes_or_watches(
        lease: str, purpose: str, code: str, canonical: Tuple[sf.LegacyFixture, Store], cli: CliRunner) -> None:
    fx, _store = canonical
    before = tree_hash(fx.data_dir)
    with other_process(fx.data_dir, lease, purpose):
        refused = cli("--data-dir", fx.data_dir, "--json", "migrate")
        assert refused.exit_code == 6 and refused.error["code"] == code, refused.stdout
        dry = cli("--data-dir", fx.data_dir, "--json", "migrate", "--dry-run")
        assert dry.exit_code == 0 and dry.data["events"] == CANONICAL_EVENTS
    assert tree_hash(fx.data_dir) == before
