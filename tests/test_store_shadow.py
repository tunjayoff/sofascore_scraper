"""
Gölge kip (plan maddesi ST-11; docs/design/01-storage.md bölüm 3.4 ve 3.5): depo açılışında kataloğun kurulması
ve uzlaştırılması, eski düzen yazıcılarının kancaları ve kataloğu yeniden kurulmuş haliyle karşılaştıran
denetim.

Ölçüt hep aynıdır: bir yazıcı dosyalarını yazıp kancasını çağırdıktan sonra katalog, aynı ağacın sıfırdan
kurulmuş haline eşittir (`CatalogAdmin.diff_from_rebuild() == []`). Aynı denetim, `STORE_SHADOW_CHECK=1` ile
bütün test paketinde her testin sonunda çalışır (tests/conftest.py); buradaki testler her kancayı tek tek ve
denetimin kendisini sınar. Tümü çevrimdışıdır: ağ istekleri yamalanır.

Maç detaylarının indiricisi ST-21'den beri eski düzene yazmaz: maçları `Store.events.put` ile v3'e yazar ve yazma
kataloğu kendisi günceller (kanca yok). `shadow_event` kancasının kendisini sınayan testler eski düzen yazıcısının
dondurulmuş kopyasını kullanır (tests/legacy_writer.py: dosyalar, ardından kanca).

İmzalar mtime'a bakar: elle değiştirilen dizinlerin mtime'ı `bump` ile açıkça ileri alınır.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

import legacy_writer
import src.store
import store_fixtures as sf
from src.exceptions import StorageError
from src.match_data_fetcher import SCORE_CHANGES_FILE, UNAVAILABLE_FILE, MatchDataFetcher
from src.match_fetcher import MatchFetcher
from src.season_fetcher import SeasonFetcher
from src.services import stats as stats_service
from src.slices import SLICE_EMPTY, SLICE_OK, SliceOutcome
from src.store import (
    CatalogAdmin,
    EventQuery,
    FollowSpec,
    JobStore,
    LeaseHeld,
    RebuildReport,
    ReconcileReport,
    Ref,
    Scope,
    Store,
    StoreError,
    open_store,
)
from src.store import api as api_mod
from src.store import indexer, layout
from src.store.catalog import CATALOG_SCHEMA, Catalog
from src.store.derive import DERIVE_VERSION
from test_store_indexer_listings import bump

ROOT = Path(__file__).resolve().parent.parent

ARS = sf.event_id(sf.PL_ARS)  # kesin kayıt, gözlemi var
LIV = sf.event_id(sf.PL_LIV)  # geçici kayıt: yenilenir
BRE = sf.event_id(sf.PL_BRE)  # eski sürümün saydığı "yok" işaretleri (lineups, incidents: 2)
NO_DETAIL = sf.event_id(sf.PL_NO_DETAIL)  # yalnızca listede (round_2)
PL = sf.PL.id
PL_SEASON = sf.PL_2627.id


@pytest.fixture
def canonical(tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture("canonical", tmp_path / "data")


@pytest.fixture
def old_forms(tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture("legacy", tmp_path / "data")


@pytest.fixture
def no_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uygulamadaki hal: denetim kipi kapalı (kancalar hata yutmaz mı, not tutulmaz mı)."""
    monkeypatch.delenv(api_mod.SHADOW_CHECK_ENV)


@pytest.fixture
def every_open_reconciles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Karar S17'nin sınırı kapalı: her açılış eski düzen maç dizinlerini tarar (uzlaştırmanın kendisini sınayanlar)."""
    monkeypatch.setenv(api_mod.OPEN_RECONCILE_ENV, "0")


def differences(store: Store) -> List[str]:
    """Deponun kataloğu ile aynı ağacın sıfırdan kurulmuş hali arasındaki farklar."""
    return store.catalog.diff_from_rebuild()


@contextlib.contextmanager
def serving(event: Dict[str, Any]) -> Any:
    """Sahte SofaScore (tests/fakes/sofascore.py): yalnızca bu maçın /event'i; yenileme boru hattından geçer (P13)."""
    from fakes.sofascore import FakeSofaScore

    fake = FakeSofaScore()
    fake.add_event(copy.deepcopy(event))
    with fake:
        yield fake


def fetcher_of(data_dir: Path) -> MatchDataFetcher:
    return MatchDataFetcher(config_manager=MagicMock(), data_dir=str(data_dir))


def legacy_save(data_dir: Path, event_id: int, match_data: Dict[str, Any], outcomes: Any = None) -> str:
    """Eski düzen yazıcısı (dondurulmuş kopya): dosyalar, ardından kanca (`shadow_event`)."""
    return legacy_writer.save_legacy(data_dir, event_id, match_data, outcomes)


def match_dir(fx: sf.LegacyFixture, event_id: int) -> Path:
    record = next(d for d in fx.details if d.event_id == event_id)
    return fx.data_dir.joinpath(*record.path.split("/"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def messages(caplog: pytest.LogCaptureFixture, *names: str, level: int = logging.DEBUG) -> List[str]:
    """Deponun (ve dizinleyicinin) yazdığı log satırları; `level` ve üstü."""
    wanted = names or ("Store", indexer.logger.name)
    return [r.getMessage() for r in caplog.records if r.name in wanted and r.levelno >= level]


def warnings_of(caplog: pytest.LogCaptureFixture) -> List[str]:
    return messages(caplog, "Store", level=logging.WARNING)


# --- açılış: kurma ve uzlaştırma ------------------------------------------------------------------------

def test_open_builds_the_catalog_from_the_files(canonical: sf.LegacyFixture, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        store = open_store(canonical.data_dir)

    info = store.info(sizes=False)
    assert (info.catalog_rebuild_reason, info.derive_version) == (None, DERIVE_VERSION)
    assert info.events_by_layout == {"legacy": len(canonical.detail_ids), "listing": 12}
    assert isinstance(store.catalog, CatalogAdmin) and store.catalog.catalog is store._catalog
    assert store.events.get(ARS).has_event_payload and store.events.get(NO_DETAIL).row_source == "listing"
    assert differences(store) == []
    built = [m for m in messages(caplog) if m.startswith("Catalog built from the files in")]
    assert len(built) == 1 and built[0].endswith(
        f": {len(canonical.detail_ids)} events with details, 12 listed only, 0 problems")
    assert store.lease_holder("maintenance") is None  # kurulumun kilidi bırakıldı

    # İkinci açılış: katalog kullanılabilir, yeniden kurulmaz; değişen dosya yok, log da yok
    built_at = info.last_rebuild
    store.close()
    caplog.clear()
    with caplog.at_level(logging.INFO):
        again = open_store(canonical.data_dir)
    assert again.info(sizes=False).last_rebuild == built_at and messages(caplog) == []


def test_open_of_an_empty_directory_logs_nothing_about_the_catalog(tmp_path: Path,
                                                                  caplog: pytest.LogCaptureFixture) -> None:
    """Boş dizinde kurulum satırı yazılmaz: komut satırı çıktısı her yeni dizinde bir satır uzamasın."""
    with caplog.at_level(logging.INFO):
        store = open_store(tmp_path / "data")

    assert store.info(sizes=False).catalog_rebuild_reason is None
    assert [m for m in messages(caplog) if "atalog" in m] == []


def test_open_reconciles_files_that_changed_behind_the_catalog(canonical: sf.LegacyFixture, no_check: None,
                                                               caplog: pytest.LogCaptureFixture,
                                                               every_open_reconciles: None) -> None:
    """
    Uygulamadaki hal (denetim kipi kapalı): açılış imzalarla uzlaştırır, yalnızca değişen dizinleri okur.
    Özet satırı DEBUG düzeyindedir: her açılışta çalışan uzlaştırma komut çıktısına satır eklemez.
    """
    data = canonical.data_dir
    open_store(data).close()
    removed = match_dir(canonical, ARS)
    for path in sorted(removed.iterdir()):
        path.unlink()
    removed.rmdir()
    bump(removed.parent)
    (data / "seasons" / "8_LaLiga_seasons.json").unlink()
    bump(data / "seasons")
    caplog.clear()

    with caplog.at_level(logging.DEBUG):
        store = open_store(data)

    assert store.events.get(ARS).row_source == "listing"  # dizini gitti, hâlâ listeleniyor
    assert store.entities.slices(Ref.tournament(sf.LALIGA.id)) == []
    assert differences(store) == []
    assert messages(caplog, level=logging.INFO) == []
    reconciled = [m for m in messages(caplog) if m.startswith("Catalog reconciled: ")]
    assert len(reconciled) == 1 and reconciled[0].startswith(
        "Catalog reconciled: 0 events re-indexed, 1 removed, 0 pending writes, 1 season listings, "
        "season lists rewritten, change log unchanged, 0 problems, ")


@pytest.mark.parametrize("name", sf.FIXTURE_NAMES)
def test_open_equals_a_rebuild_on_every_fixture_and_a_second_reconcile_keeps_it(name: str, tmp_path: Path) -> None:
    """
    Karşılaştırma satırlar üzerindendir: en yeni kopyası okunamayan maç her uzlaştırmada yeniden okunur
    (`ReconcileReport.changed` hep True kalır) ama katalog yeniden kurulmuş haline eşit kalır.
    """
    fx = sf.build_fixture(name, tmp_path / "data")
    store = open_store(fx.data_dir)

    assert differences(store) == []
    report = store.catalog.reconcile()
    assert isinstance(report, ReconcileReport) and differences(store) == []
    assert (report.events_removed, report.seasons, report.season_lists, report.changes) == (0, [], None, None)


def test_open_rebuilds_rows_of_another_derive_version(canonical: sf.LegacyFixture) -> None:
    data = canonical.data_dir
    first = open_store(data)
    with first._catalog.write() as conn:
        conn.execute("UPDATE meta SET value = '0' WHERE key = 'derive_version'")
        conn.execute("DELETE FROM events WHERE id = ?", (ARS,))
    first.close()

    store = open_store(data)

    assert store.info(sizes=False).catalog_rebuild_reason is None
    assert store._catalog.get_meta(indexer.META_BUILD_MODE) == "in_place"
    assert store.events.get(ARS) is not None and differences(store) == []


def _foreign_schema(data: Path) -> None:
    conn = sqlite3.connect(data / ".meta" / "catalog.db")
    conn.execute(f"PRAGMA user_version = {CATALOG_SCHEMA + 1}")
    conn.close()


def test_open_recreates_a_catalog_of_another_schema(canonical: sf.LegacyFixture) -> None:
    data = canonical.data_dir
    open_store(data).close()
    _foreign_schema(data)

    store = open_store(data)

    assert store.info(sizes=False).catalog_rebuild_reason is None
    assert store._catalog.get_meta(indexer.META_BUILD_MODE) == "recreate"
    assert differences(store) == [] and not (data / ".meta" / "catalog.db.build").exists()


def test_a_catalog_that_needs_recreating_waits_while_the_directory_is_in_use(
        canonical: sf.LegacyFixture, caplog: pytest.LogCaptureFixture) -> None:
    """Dosyanın yerine yenisi konamaz: çalışan yazarın o sırada yazdığı satırlar kaybolurdu."""
    data = canonical.data_dir
    first = open_store(data)
    writer = first.lease("writer", purpose="job")
    first.close()
    _foreign_schema(data)

    with caplog.at_level(logging.WARNING):
        store = open_store(data)  # açılış düşmez
        fetcher_of(data)._save_match_data(str(NO_DETAIL), {"basic": sf.basic_payload(sf.PL_NO_DETAIL)})
    assert store.info(sizes=False).catalog_rebuild_reason == "schema_version"
    waiting = warnings_of(caplog)  # bir kez: kanca yeniden uyarmaz
    assert len(waiting) == 1 and "has to be recreated (schema_version)" in waiting[0]
    assert "the data directory is in use" in waiting[0]
    assert api_mod.shadow_check() == []  # kancası çalışamayan dizin karşılaştırılmaz

    writer.release()
    store._catalog_retry_at = 0.0  # bekleme süresi doldu
    fetcher_of(data)._save_match_data(str(NO_DETAIL), {"basic": sf.basic_payload(sf.PL_NO_DETAIL)})
    assert store.info(sizes=False).catalog_rebuild_reason is None
    assert store.events.get(NO_DETAIL).has_event_payload and differences(store) == []


def test_first_open_under_a_writer_lease_builds_in_place_without_the_maintenance_lease(
        canonical: sf.LegacyFixture) -> None:
    """
    Web işi yazar kilidini alır, depoyu sonra açar (src/web/fetch_job.py); web kurulumunun dizininde o ana
    kadar yalnızca state.db vardır. `maintenance` alınamaz, katalog yine kurulur: yerinde, tek yazma işleminde.
    """
    data = canonical.data_dir
    open_store(data, sync_catalog=False).close()
    for path in sorted((data / ".meta").glob("catalog.db*")):
        path.unlink()
    writer = api_mod.LeaseManager.for_data_dir(data).acquire("writer", purpose="job")
    try:
        store = open_store(data)
        assert store.info(sizes=False).catalog_rebuild_reason is None
        assert store._catalog.get_meta(indexer.META_BUILD_MODE) == "in_place" and differences(store) == []
        assert [lease.name for lease in store.info(sizes=False).leases] == ["writer"]
    finally:
        writer.release()


def test_an_unclean_writer_makes_the_open_check_the_file_and_the_v3_events(
        canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch, every_open_reconciles: None) -> None:
    data = canonical.data_dir
    first = open_store(data)
    writer = first.lease("writer", purpose="job")
    marker = Path(first._leases.unclean_marker)
    first.close()
    calls: List[Dict[str, Any]] = []
    reconcile = CatalogAdmin.reconcile

    def spy(self: CatalogAdmin, **kwargs: Any) -> ReconcileReport:
        calls.append(kwargs)
        return reconcile(self, **kwargs)

    monkeypatch.setattr(CatalogAdmin, "reconcile", spy)
    assert marker.exists()
    open_store(data).close()
    assert calls[-1]["v3"] is False  # yazar hâlâ çalışıyor: işaret onun

    writer.release()
    assert not marker.exists()
    open_store(data).close()
    assert calls[-1]["v3"] is False  # temiz bırakıldı

    marker.write_text("")  # yazar ölmüş olsaydı işaret kalırdı
    open_store(data).close()
    assert calls[-1]["v3"] is True

    monkeypatch.setattr(Catalog, "quick_check", lambda self: ["row 3 missing from index events_start"])
    store = open_store(data)
    assert store._catalog.get_meta(indexer.META_BUILD_MODE) == "recreate"  # bozuk dosya yeniden yaratıldı
    monkeypatch.undo()
    assert differences(store) == []


def test_sync_catalog_false_leaves_the_catalog_as_found(canonical: sf.LegacyFixture) -> None:
    data = canonical.data_dir
    store = open_store(data, sync_catalog=False)
    assert store.info(sizes=False).catalog_rebuild_reason == "derive_version"
    assert store.events.count(EventQuery()) == 0

    assert open_store(data) is store  # eşitlenmiş depo isteyen sonraki çağrı kataloğu kurar
    assert store.info(sizes=False).catalog_rebuild_reason is None and differences(store) == []


def test_a_catalog_that_cannot_be_synced_does_not_fail_the_open(
        canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
        every_open_reconciles: None) -> None:
    data = canonical.data_dir
    open_store(data).close()

    def full(self: CatalogAdmin, **kwargs: Any) -> ReconcileReport:
        raise StoreError("disk dolu", path=str(data), errno_code=28)

    monkeypatch.setattr(CatalogAdmin, "reconcile", full)
    with caplog.at_level(logging.WARNING):
        store = open_store(data)
        store.runtime.set("bridge_health", {"ok": True})  # depo kullanılabilir
        legacy_save(data, NO_DETAIL, {"basic": sf.basic_payload(sf.PL_NO_DETAIL)})
    assert len(warnings_of(caplog)) == 1 and "could not be brought up to date" in warnings_of(caplog)[0]
    assert store.events.get(NO_DETAIL).row_source == "listing"  # kanca kataloğa yazmadı
    assert api_mod.shadow_check() == []

    monkeypatch.undo()
    store._catalog_retry_at = 0.0
    legacy_save(data, NO_DETAIL, {"basic": sf.basic_payload(sf.PL_NO_DETAIL)})
    assert store.events.get(NO_DETAIL).has_event_payload and differences(store) == []


def test_season_list_named_after_the_league_is_resolved_through_the_follows(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "seasons").mkdir(parents=True)
    (data / "seasons" / "Premier_League_seasons.json").write_bytes(sf.dump_json(
        {"seasons": [{"id": s.id, "name": s.name, "year": s.year} for s in (sf.PL_2627, sf.PL_2526)]}))
    store = open_store(data)
    assert store.entities.seasons(PL) == []  # takip yok: dosyanın turnuvası bilinmiyor

    store.follows.add(FollowSpec("tournament", PL, "Premier League"))
    store.close()
    store = open_store(data)

    assert [season.id for season in store.entities.seasons(PL)] == [sf.PL_2627.id, sf.PL_2526.id]
    assert differences(store) == []


def test_store_has_the_job_store(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")

    assert isinstance(store.jobs, JobStore) and store.jobs is JobStore.for_store(store)
    store.close()
    with pytest.raises(StoreError):
        _ = store.jobs


# --- maç detayları: indiricinin yazması (Store, v3) ve eski düzen yazıcısının kancası --------------------

def test_saving_a_match_indexes_it(canonical: sf.LegacyFixture) -> None:
    """İndirici Store'a yazar (ST-21): maç v3'e gider ve katalog aynı işlemde güncellenir, kanca gerekmez."""
    data = canonical.data_dir
    store = open_store(data)
    before = store.events.get(NO_DETAIL)
    assert (before.row_source, before.has_event_payload, before.listed_in) == ("listing", False, "round_2")
    basic = sf.basic_payload(sf.PL_NO_DETAIL)

    fetcher_of(data)._save_match_data(str(NO_DETAIL), {
        "basic": basic, "statistics": sf.slice_payload("statistics", basic), "lineups": None})

    row = store.events.get(NO_DETAIL)
    assert (row.row_source, row.has_event_payload, row.layout, row.listed_in) == ("event", True, "v3", "round_2")
    assert (row.path, row.legacy_path) == (None, None)
    assert not (data / "match_details" / sf.league_dir(sf.PL) / f"season_{sf.safe_name(sf.PL_2627.name)}"
                / str(NO_DETAIL)).exists()  # eski düzene yazılmadı
    assert store.events.slice(NO_DETAIL, "statistics").state == "ok"
    assert store.events.payload(NO_DETAIL, "statistics") == sf.slice_payload("statistics", basic)
    assert differences(store) == []


def test_saving_into_a_directory_that_was_never_opened_builds_the_catalog(tmp_path: Path) -> None:
    """İndirici, deposu hiç açılmamış bir dizine yazar: yazma depoyu açar, katalog maçı gösterir."""
    data = tmp_path / "data"
    basic = sf.basic_payload(sf.NBA_A)

    fetcher_of(data)._save_match_data(str(sf.event_id(sf.NBA_A)), {"basic": basic})

    assert (data / ".meta" / "catalog.db").is_file()
    store = open_store(data)
    row = store.events.get(sf.event_id(sf.NBA_A))
    assert row.has_event_payload and row.sport == "basketball" and differences(store) == []


def test_a_match_without_a_tournament_is_indexed_where_the_writer_put_it(tmp_path: Path) -> None:
    """Eski düzen yazıcısı turnuvası olmayan maçı `_no_tournament/` altına yazar; kanca onu orada dizinler."""
    data = tmp_path / "data"
    event_id = sf.event_id(sf.FRIENDLY_A)

    legacy_save(data, event_id, {"basic": sf.basic_payload(sf.FRIENDLY_A)})

    store = open_store(data)
    assert store.events.get(event_id).path == f"match_details/_no_tournament/football/{event_id}"
    assert differences(store) == []
    # İndirici aynı maçı v3'e yazar (yeri kimlikten türer); eski dizin yerinde kalır
    fetcher_of(data)._save_match_data(str(event_id), {"basic": sf.basic_payload(sf.FRIENDLY_A)})
    row = store.events.get(event_id)
    assert (row.layout, row.legacy_path) == ("v3", f"match_details/_no_tournament/football/{event_id}")
    assert differences(store) == []


def test_saves_from_several_threads_are_all_indexed(tmp_path: Path) -> None:
    """Detay indirme eşzamanlı yazar: yazmalar aynı anda gelir; ilki depoyu açar, hepsi dizinlenir."""
    data = tmp_path / "data"
    events = [sf.PL_ARS, sf.PL_LIV, sf.PL_BRE, sf.PL_NO_DETAIL, sf.NBA_A, sf.NBA_B]
    fetcher = fetcher_of(data)
    start = threading.Barrier(len(events))
    errors: List[BaseException] = []

    def save(ev: sf.Ev) -> None:
        try:
            start.wait(timeout=10)
            for _ in range(3):
                basic = sf.basic_payload(ev)
                fetcher._save_match_data(str(sf.event_id(ev)), {
                    "basic": basic, "statistics": sf.slice_payload("statistics", basic)})
        except BaseException as exc:  # iş parçacığındaki hata testi düşürmeli
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(ev,)) for ev in events]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == [] and not any(thread.is_alive() for thread in threads)
    store = open_store(data)
    assert store.info(sizes=False).events_by_layout == {"v3": len(events)}
    assert differences(store) == []


def test_marker_updates_are_indexed_with_the_save(canonical: sf.LegacyFixture) -> None:
    """Bitmiş maçta kesin "yok" yanıtı sayılır (manifestteki işaret): dilim satırı onu gösterir."""
    data = canonical.data_dir
    store = open_store(data)
    basic = sf.basic_payload(sf.PL_NO_DETAIL)
    fetcher = fetcher_of(data)
    outcomes = {"lineups": SliceOutcome(SLICE_EMPTY, http_status=404)}

    fetcher._save_match_data(str(NO_DETAIL), {"basic": basic, "lineups": None}, outcomes)
    first = store.events.slice(NO_DETAIL, "lineups")
    assert (first.state, first.empty_count, first.settled_empty()) == ("empty", 1, False)

    fetcher._save_match_data(str(NO_DETAIL), {"basic": basic, "lineups": None}, outcomes)
    second = store.events.slice(NO_DETAIL, "lineups")
    assert (second.empty_count, second.unverified_empty_count, second.settled_empty()) == (2, 0, True)
    assert differences(store) == []


def test_a_failed_save_leaves_the_catalog_equal_to_the_disk(canonical: sf.LegacyFixture,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Yarıda kalan yazma kataloğu diskten ayırmaz: yeni maç hazırlık dizininde kurulur, disk dolunca hiçbir şey
    yayımlanmaz (kalıcı StorageError); var olan maçta yazma yarıda kalırsa işaret kalır ve sonraki yazma ya da
    açılış maçı dosyalardan toparlar.
    """
    from src.store import files as store_files

    data = canonical.data_dir
    store = open_store(data)
    basic = sf.basic_payload(sf.PL_NO_DETAIL)
    real = store_files.write_bytes

    def disk_full_on_the_statistics(path: Any, payload: bytes, **kwargs: Any) -> None:
        if os.path.basename(os.fspath(path)).startswith("statistics"):
            raise store_files._store_error(OSError(28, "No space left on device", os.fspath(path)), path)
        real(path, payload, **kwargs)

    monkeypatch.setattr(store_files, "write_bytes", disk_full_on_the_statistics)
    with pytest.raises(StorageError) as info:
        fetcher_of(data)._save_match_data(str(NO_DETAIL), {
            "basic": basic, "statistics": sf.slice_payload("statistics", basic)})
    assert info.value.fatal

    assert not store.events.get(NO_DETAIL).has_event_payload
    assert differences(store) == []
    monkeypatch.undo()
    fetcher_of(data)._save_match_data(str(NO_DETAIL), {
        "basic": basic, "statistics": sf.slice_payload("statistics", basic)})
    assert store.events.slice(NO_DETAIL, "statistics").state == "ok" and differences(store) == []


def test_marker_reset_is_indexed(canonical: sf.LegacyFixture) -> None:
    """
    --recheck-unavailable: eski sürümün saydığı "yok" işaretleri silinir, dilimler yeniden beklenir. İşi Store yapar:
    maç v3'e yükseltilir, eski dizinindeki işaret dosyasına dokunulmaz.
    """
    data = canonical.data_dir
    store = open_store(data)
    before = store.events.slice(BRE, "lineups")
    assert (before.state, before.unverified_empty_count, before.settled_empty()) == ("empty", 2, True)

    result = fetcher_of(data).reset_unavailable_markers()

    assert result["matches"] >= 1 and (match_dir(canonical, BRE) / UNAVAILABLE_FILE).exists()
    assert store.events.get(BRE).layout == "v3"
    assert store.events.slice(BRE, "lineups").state == "not_requested"
    assert differences(store) == []
    # İkinci çalıştırma hiçbir şeyi değiştirmez: yazma da kanca da yok
    signature = store.events.get(BRE).sig
    assert fetcher_of(data).reset_unavailable_markers()["matches"] == 0
    assert store.events.get(BRE).sig == signature and differences(store) == []


def test_refresh_without_a_change_indexes_the_new_observation(canonical: sf.LegacyFixture) -> None:
    data = canonical.data_dir
    store = open_store(data)
    before = store.events.get(LIV)
    old = read_json(match_dir(canonical, LIV) / "basic.json")
    fetcher = fetcher_of(data)

    with serving(old):
        assert fetcher.refresh_match(str(LIV)) is not None

    after = store.events.get(LIV)
    assert not fetcher.last_refresh_changed and store.changes.last_seq() == len(sf.SCORE_CHANGES)
    assert after.observed_at > before.observed_at and after.sig != before.sig
    assert (after.home_score, after.away_score) == (before.home_score, before.away_score)
    assert differences(store) == []


def test_refresh_with_a_change_indexes_the_payload_and_the_change_log(canonical: sf.LegacyFixture) -> None:
    data = canonical.data_dir
    store = open_store(data)
    before = store.events.get(LIV)
    old = read_json(match_dir(canonical, LIV) / "basic.json")
    new = copy.deepcopy(old)
    new["homeScore"]["current"] = old["homeScore"]["current"] + 1
    fetcher = fetcher_of(data)

    with serving(new):
        fetcher.refresh_match(str(LIV))

    after = store.events.get(LIV)
    assert fetcher.last_refresh_changed and after.home_score_current == before.home_score_current + 1
    assert store.changes.last_seq() == len(sf.SCORE_CHANGES) + 1
    newest = store.changes.list(event_id=LIV)[-1]
    assert newest.seq == store.changes.last_seq() and "homeScore.current" in newest.fields
    assert newest.segment.startswith("changes/")  # Store'un günlüğü; eski dosyaya satır eklenmez
    assert len((data / SCORE_CHANGES_FILE).read_text(encoding="utf-8").splitlines()) == len(sf.SCORE_CHANGES)
    assert after.layout == "v3" and after.change_ts is not None
    assert differences(store) == []


def test_first_payload_of_a_tournament_fills_the_sport_of_its_summary_rows(tmp_path: Path) -> None:
    """
    Özet CSV'sinden gelen liste satırları sporunu turnuvanın satırından alır. Turnuvayı ilk olay yükü
    getirir: kanca o sezonu da yeniden dizinler (yeniden kurma bu sezonları maçlardan sonra yazar).
    """
    data = tmp_path / "data"
    league, season = sf.league_dir(sf.NBA), sf.season_dir(sf.NBA_2627)
    rows = [sf.summary_row("last_0", sf.event_payload(ev)) for ev in (sf.NBA_A, sf.NBA_B)]
    (data / "matches" / league).mkdir(parents=True)
    (data / "matches" / league / f"{season}_summary.csv").write_bytes(sf.dump_csv(sf.SUMMARY_COLUMNS, rows))
    store = open_store(data)
    assert store.events.get(sf.event_id(sf.NBA_B)).sport == ""

    fetcher_of(data)._save_match_data(str(sf.event_id(sf.NBA_A)), {"basic": sf.basic_payload(sf.NBA_A)})

    assert store.events.get(sf.event_id(sf.NBA_B)).sport == "basketball"
    assert differences(store) == []


# --- kancalar: program, sezon özeti, sezon listesi ------------------------------------------------------

@contextlib.asynccontextmanager
async def _no_session() -> Any:
    yield None


def _schedule_fetcher(data: Path) -> MatchFetcher:
    config = MagicMock()
    config.get_leagues.return_value = {PL: sf.PL.name}
    config.get_league_by_id.return_value = sf.PL.name
    config.get_max_concurrent.return_value = 2
    seasons = MagicMock()
    seasons.get_season_name.return_value = sf.PL_2627.name
    return MatchFetcher(config, seasons, data_dir=str(data))


def _rounds_api(rounds: Dict[int, List[sf.Ev]]) -> Any:
    base = f"/unique-tournament/{PL}/season/{PL_SEASON}"

    async def api(session: Any, url: str, max_retries: Optional[int] = None) -> Dict[str, Any]:
        if url == f"{base}/rounds":
            return {"rounds": [{"round": number} for number in sorted(rounds)]}
        number = int(url.rsplit("/", 1)[1])
        return {"events": [sf.event_payload(ev) for ev in rounds[number]]}

    return api


def test_fetching_a_season_schedule_indexes_rounds(tmp_path: Path) -> None:
    data = tmp_path / "data"
    rounds = {1: [sf.PL_ARS, sf.PL_LIV], 2: [sf.PL_NO_DETAIL, sf.PL_NOT_STARTED]}
    fetcher = _schedule_fetcher(data)

    with patch("src.utils.make_api_request_async", new=_rounds_api(rounds)), \
            patch("src.utils.create_session_async", new=_no_session), patch("src.utils.FETCH_ONLY_FINISHED", True):
        assert fetcher.fetch_all_matches_for_season(PL, PL_SEASON) is True

    store = open_store(data)
    # ST-22: turlar v3 sezon dizinine yazılır; eski düzen dizini ve sezon özeti dosyaları yazılmaz
    season_dir = data / "v3" / "tournaments" / str(PL) / "seasons" / str(PL_SEASON) / "schedule"
    assert sorted(p.name for p in season_dir.iterdir()) == ["round_1.json.gz", "round_2.json.gz"]
    assert not (data / "matches" / sf.league_dir(sf.PL)).exists()  # özet CSV'si de yazılmaz (karar S4)
    listed = store.events.list(EventQuery(scope=Scope(season_ids=[PL_SEASON])), with_total=True)
    assert listed.total == 4 and {row.listed_in for row in listed.items} == {"round_1", "round_2"}
    assert [info.sub for info in store.entities.slices(Ref.season(PL, PL_SEASON))] == ["round_1", "round_2"]
    roots = {row[0] for row in store._catalog.connection().execute("SELECT path FROM legacy_roots")}
    assert roots == set()  # eski düzende dosya yok; v3'ün imzası yok
    assert differences(store) == []


def test_fetching_event_pages_indexes_them(tmp_path: Path) -> None:
    """Tur listesi olmayan turnuva: program `events/last` ve `events/next` sayfalarından gelir."""
    data = tmp_path / "data"
    base = f"/unique-tournament/{PL}/season/{PL_SEASON}"
    pages = {f"{base}/events/last/0": [sf.PL_ARS, sf.PL_LIV], f"{base}/events/next/0": [sf.PL_NOT_STARTED]}

    async def api(session: Any, url: str, max_retries: Optional[int] = None) -> Dict[str, Any]:
        if url == f"{base}/rounds":
            return {"rounds": []}
        return {"events": [sf.event_payload(ev) for ev in pages.get(url, [])], "hasNextPage": False}

    with patch("src.utils.make_api_request_async", new=api), \
            patch("src.utils.create_session_async", new=_no_session), patch("src.utils.FETCH_ONLY_FINISHED", False):
        assert _schedule_fetcher(data).fetch_all_matches_for_season(PL, PL_SEASON) is True

    store = open_store(data)
    assert [info.sub for info in store.entities.slices(Ref.season(PL, PL_SEASON))] == ["last_0", "next_0"]
    listed = store.events.list(EventQuery(scope=Scope(season_ids=[PL_SEASON])), with_total=True)
    assert listed.total == 3 and {row.listed_in for row in listed.items} == {"last_0", "next_0"}
    assert differences(store) == []


def test_a_schedule_fetch_that_is_cut_short_still_indexes_the_saved_rounds(tmp_path: Path) -> None:
    """Kanca `finally` içindedir: iptal edilen ya da hata veren çekim, yazdığı turları katalogda bırakır."""
    data = tmp_path / "data"
    fetcher = _schedule_fetcher(data)
    real = fetcher.fetch_all_rounds_async

    async def cancelled(*args: Any, **kwargs: Any) -> Any:
        await real(*args, **kwargs)
        raise asyncio.CancelledError()

    with patch("src.utils.make_api_request_async", new=_rounds_api({1: [sf.PL_ARS]})), \
            patch("src.utils.create_session_async", new=_no_session), patch("src.utils.FETCH_ONLY_FINISHED", True), \
            patch.object(fetcher, "fetch_all_rounds_async", new=cancelled):
        with pytest.raises(asyncio.CancelledError):
            fetcher.fetch_all_rounds_for_season(PL, PL_SEASON)

    store = open_store(data)
    assert store.events.get(ARS).listed_in == "round_1" and differences(store) == []


def test_saving_a_season_list_indexes_it(canonical: sf.LegacyFixture) -> None:
    data = canonical.data_dir
    store = open_store(data)
    assert store.entities.seasons(sf.BUNDESLIGA.id) == []
    config = MagicMock()
    config.get_league_by_id.return_value = sf.BUNDESLIGA.name
    fetcher = SeasonFetcher(config, data_dir=str(data))

    fetcher._save_seasons_json(sf.BUNDESLIGA.id, {"seasons": [
        {"id": 77001, "name": "Bundesliga 26/27", "year": "26/27"},
        {"id": 77000, "name": "Bundesliga 25/26", "year": "25/26"}]})

    seasons = store.entities.seasons(sf.BUNDESLIGA.id)
    assert [(s.id, s.listed, s.position) for s in seasons] == [(77001, True, 0), (77000, True, 1)]
    assert store.entities.payload(Ref.tournament(sf.BUNDESLIGA.id), "seasons")["seasons"][0]["id"] == 77001
    assert differences(store) == []


# --- kancalar: temizleme --------------------------------------------------------------------------------

@pytest.mark.parametrize("scope, left", [
    ("all", {"legacy": 0, "listing": 0}),
    ("match_details", {"legacy": 0, "listing": 35}),
    ("matches", {"legacy": 23, "listing": 0}),
])
def test_clear_rebuilds_the_catalog(canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch, scope: str,
                                    left: Dict[str, int]) -> None:
    """Temizlemeden sonra katalog kalan dosyalardan yeniden kurulur: silinen turnuvaların satırları da gider."""
    from src.web.routes import data as data_routes

    data = canonical.data_dir
    store = open_store(data)
    monkeypatch.setattr(data_routes.config_manager, "get_data_dir", lambda: str(data))

    assert data_routes._clear_data_sync(scope)["status"] == "success"

    info = store.info(sizes=False)
    assert {name: info.events_by_layout.get(name, 0) for name in left} == left
    assert differences(store) == []
    if scope == "all":
        assert {table: count for table, count in info.rows["catalog"].items() if count and table != "meta"} == {
            "changes": 2, "legacy_roots": 1}  # score_changes.jsonl temizlenmez
    else:
        # Uzlaştırma varlık satırlarını silmez; temizleme yeniden kurar: tablolar bir yeniden kurmanınkiyle aynı
        fresh = Catalog(data.parent / "fresh.db")
        with fresh:
            CatalogAdmin(data, fresh, league_names=store.follows.leagues).rebuild()
            for table in indexer.DIFF_GROWING_TABLES:
                query = f"SELECT count(*) FROM {table}"
                assert (store._catalog.connection().execute(query).fetchone()[0]
                        == fresh.connection().execute(query).fetchone()[0]), table


# --- Store.clear (ST-19) ---------------------------------------------------------------------------------

def test_store_clear_removes_the_v3_and_the_legacy_form_and_rebuilds_the_catalog(canonical: sf.LegacyFixture) -> None:
    data = canonical.data_dir
    store = open_store(data)
    store.events.put(NO_DETAIL, {"event": SliceOutcome(SLICE_OK, sf.basic_payload(sf.PL_NO_DETAIL))})
    assert (data / layout.EVENTS_DIR).is_dir() and store.events.get(NO_DETAIL).has_event_payload

    report = store.clear("events")

    assert report == api_mod.ClearReport(scopes=("events",), cleared=("match_details",), v3_events=True,
                                         catalog_rebuilt=True)
    assert not (data / layout.EVENTS_DIR).exists() and not any((data / "match_details").iterdir())
    assert store.events.get(ARS).row_source == "listing" and store.events.get(NO_DETAIL).row_source == "listing"
    assert (data / "score_changes.jsonl").exists() and (data / ".meta" / "state.db").exists()
    assert store.lease_holder("maintenance") is None  # kendi aldığı kilidi bıraktı
    assert differences(store) == []


def test_store_clear_takes_several_scopes_and_removes_the_entity_rows(canonical: sf.LegacyFixture) -> None:
    store = open_store(canonical.data_dir)
    assert store.entities.tournaments()

    report = store.clear(["seasons", "schedules", "events"])

    assert report.cleared == ("match_details", "matches", "seasons")  # bugünkü web sırası
    assert store.entities.tournaments() == [] and store.events.count(EventQuery()) == 0
    assert differences(store) == []


def test_store_clear_runs_under_the_maintenance_lease(canonical: sf.LegacyFixture) -> None:
    """Bu süreç `maintenance`'ı tutuyorsa onun altında çalışır; başkası dizini kullanıyorsa reddedilir, silmez."""
    data = canonical.data_dir
    store = open_store(data)
    other = api_mod.LeaseManager.for_data_dir(data)

    with other.acquire("writer", purpose="job"):
        with pytest.raises(LeaseHeld):
            store.clear("all")
    assert any((data / "match_details").iterdir())

    jobs = JobStore.for_store(store)
    with jobs.exclusive("clear"):  # web'in yolu: kilidi iş deposu tutar
        assert store.clear("schedules").cleared == ("matches",)
        assert store.lease_holder("maintenance") is not None
    assert store.lease_holder("maintenance") is None


def test_store_clear_rejects_unknown_scopes_and_read_only_stores(canonical: sf.LegacyFixture) -> None:
    store = open_store(canonical.data_dir)
    for scope in ("match_details", "everything", ()):
        with pytest.raises(ValueError):
            store.clear(scope)
    with pytest.raises(StoreError):
        open_store(canonical.data_dir, readonly=True).clear("all")
    assert any((canonical.data_dir / "match_details").iterdir())


def test_a_store_clear_that_fails_half_way_still_rebuilds_the_catalog(canonical: sf.LegacyFixture,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """maç detayları silindikten sonra matches/ silinemez: hata çıkar, katalog diskte kalanı anlatır."""
    data = canonical.data_dir
    store = open_store(data)
    remove_tree = api_mod.files.remove_tree

    def failing(path: Any) -> bool:
        if os.path.basename(os.fspath(path)) == "matches":
            raise StoreError("disk error", path=os.fspath(path))
        return remove_tree(path)

    monkeypatch.setattr(api_mod.files, "remove_tree", failing)
    with pytest.raises(StoreError, match="disk error"):
        store.clear("all")

    assert not any((data / "match_details").iterdir()) and any((data / "matches").iterdir())
    assert store.events.get(ARS).row_source == "listing"
    assert store.lease_holder("maintenance") is None
    assert differences(store) == []


def test_catalog_current_follows_the_sync(canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    store = open_store(canonical.data_dir)
    assert store.catalog_current

    def busy(self: CatalogAdmin, *args: Any, **kwargs: Any) -> Any:
        raise src.store.StoreBusy("catalog.db kilitli", path=str(canonical.data_dir))

    monkeypatch.setattr(CatalogAdmin, "index_event", busy)
    legacy_save(canonical.data_dir, NO_DETAIL, {"basic": sf.basic_payload(sf.PL_NO_DETAIL)})
    assert not store.catalog_current  # kanca yazamadı: okuyucular kataloğu güncel saymamalı
    monkeypatch.undo()
    store._catalog_retry_at = 0.0
    legacy_save(canonical.data_dir, NO_DETAIL, {"basic": sf.basic_payload(sf.PL_NO_DETAIL)})
    assert store.catalog_current and differences(store) == []
    store.close()
    assert not store.catalog_current


# --- karar S17: açılıştaki uzlaştırmanın sınırı ----------------------------------------------------------

def _spy_reconcile(monkeypatch: pytest.MonkeyPatch) -> Dict[str, int]:
    calls = {"reconcile": 0, "sync_listings": 0}
    reconcile, sync_listings = CatalogAdmin.reconcile, CatalogAdmin.sync_listings

    def spy_reconcile(self: CatalogAdmin, **kwargs: Any) -> ReconcileReport:
        calls["reconcile"] += 1
        return reconcile(self, **kwargs)

    def spy_sync(self: CatalogAdmin, *args: Any, **kwargs: Any) -> ReconcileReport:
        calls["sync_listings"] += 1
        return sync_listings(self, *args, **kwargs)

    monkeypatch.setattr(CatalogAdmin, "reconcile", spy_reconcile)
    monkeypatch.setattr(CatalogAdmin, "sync_listings", spy_sync)
    return calls


def test_an_open_soon_after_another_skips_the_pass_over_the_event_directories(
        canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch, no_check: None) -> None:
    """
    Bir açılış tam taradıktan sonraki dakika içinde açılan depo maç dizinlerini taramaz; liste yarısı yine
    çalışır. Kancasız (elle, 2.x süreciyle) değişen maç dizini o süre dolana kadar görülmez; süre dolunca ya
    da `catalog reconcile` ile görülür.
    """
    data = canonical.data_dir
    open_store(data).close()  # kurulum: zaman damgası yazılır
    calls = _spy_reconcile(monkeypatch)

    removed = match_dir(canonical, ARS)
    for path in sorted(removed.iterdir()):
        path.unlink()
    removed.rmdir()
    bump(removed.parent)
    (data / "seasons" / "8_LaLiga_seasons.json").unlink()
    bump(data / "seasons")

    store = open_store(data)
    assert calls == {"reconcile": 0, "sync_listings": 1}
    assert store.entities.slices(Ref.tournament(sf.LALIGA.id)) == []  # liste değişikliği görüldü
    assert store.events.get(ARS).has_event_payload  # maç dizini taranmadı: silindiği görülmedi
    store.close()

    now = time.time()
    monkeypatch.setattr(api_mod.time, "time", lambda: now + api_mod.OPEN_RECONCILE_SECONDS)  # süre doldu
    store = open_store(data)
    assert calls["reconcile"] == 1 and store.events.get(ARS).row_source == "listing"
    assert differences(store) == []


def test_catalog_reconcile_always_runs_the_pass(canonical: sf.LegacyFixture, no_check: None) -> None:
    data = canonical.data_dir
    open_store(data).close()
    shutil.rmtree(match_dir(canonical, ARS))
    bump(match_dir(canonical, ARS).parent)

    store = open_store(data)
    assert store.events.get(ARS).has_event_payload
    report = store.catalog.reconcile()
    assert report.events_removed == 1 and store.events.get(ARS).row_source == "listing"
    assert differences(store) == []


@pytest.mark.parametrize("case", ["pending_write", "unclean_writer", "copied_directory", "window_zero", "no_stamp",
                                  "clock_went_back"])
def test_the_pass_runs_whenever_the_shortcut_is_not_safe(canonical: sf.LegacyFixture, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch, no_check: None,
                                                         case: str) -> None:
    data = canonical.data_dir
    store = open_store(data)
    if case == "pending_write":
        with store._catalog.write() as conn:
            conn.execute("INSERT INTO pending_writes (kind, entity_id, started_at) VALUES ('event', ?, 0)", (ARS,))
    if case == "no_stamp":
        with store._catalog.write() as conn:
            conn.execute("DELETE FROM meta WHERE key = ?", (api_mod.META_OPEN_RECONCILED,))
    store.close()
    if case == "unclean_writer":
        Path(api_mod.LeaseManager.for_data_dir(data).unclean_marker).write_text("")
    if case == "copied_directory":
        shutil.copytree(data, tmp_path / "copy")
        data = tmp_path / "copy"
    if case == "window_zero":
        monkeypatch.setenv(api_mod.OPEN_RECONCILE_ENV, "0")
    if case == "clock_went_back":
        now = time.time()
        monkeypatch.setattr(api_mod.time, "time", lambda: now - 3600)
    calls = _spy_reconcile(monkeypatch)

    store = open_store(data)

    assert calls == {"reconcile": 1, "sync_listings": 0}
    assert differences(store) == []


def test_the_window_can_be_set_through_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for raw, seconds in (("", 60.0), ("5", 5.0), ("-3", 0.0), ("soon", 60.0)):
        monkeypatch.setenv(api_mod.OPEN_RECONCILE_ENV, raw)
        assert api_mod._open_reconcile_seconds() == seconds


# --- iki gerçek süreç: birinde yazar, ötekinde açılış ----------------------------------------------------

_WRITER_SCRIPT = """
import os, sys
from src.store import open_store, shadow_event
data, staged, target, event_id = sys.argv[1:5]
store = open_store(data)
lease = store.lease("writer", purpose="job")
os.replace(staged, target)  # 2.x yazıcısı gibi: dosyalar yerinde, ardından kanca
shadow_event(data, event_id, target)
print("written", flush=True)
sys.stdin.readline()
lease.release()
print("released", flush=True)
"""


def test_an_open_in_another_process_sees_what_a_writer_process_hooked(canonical: sf.LegacyFixture,
                                                                      tmp_path: Path) -> None:
    """
    Bir süreç `writer` kilidini tutup bir maç dizini yazar ve kancasını çağırır; aynı dizini başka bir süreç
    (bu test) açar. Açılış kilitten dolayı düşmez ve, karar S17'nin sınırıyla maç dizinlerini taramasa da,
    yazılanı kataloğun kendisinden görür.
    """
    import subprocess
    import sys

    data = canonical.data_dir
    target = match_dir(canonical, ARS)
    staged = tmp_path / "staged"
    shutil.move(str(target), str(staged))
    bump(target.parent)
    open_store(data).close()  # kurulum: maç yalnızca listede; zaman damgası yazılır

    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop(api_mod.SHADOW_CHECK_ENV, None)
    child = subprocess.Popen([sys.executable, "-c", _WRITER_SCRIPT, str(data), str(staged), str(target), str(ARS)],
                             cwd=str(ROOT), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None and child.stdin is not None
        assert child.stdout.readline().strip() == "written", child.stderr.read() if child.stderr else ""

        store = open_store(data)
        assert store.lease_holder("writer") is not None  # öteki süreç hâlâ yazar
        assert store.events.get(ARS).has_event_payload
        with pytest.raises(LeaseHeld):
            store.clear("all")  # yazar çalışırken temizleme reddedilir
        store.close()

        child.stdin.write("\n")
        child.stdin.flush()
        assert child.stdout.readline().strip() == "released"
        assert child.wait(timeout=30) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=30)

    store = open_store(data)
    assert store.lease_holder("writer") is None and store.events.get(ARS).has_event_payload
    assert differences(store) == []


# --- kancalar: hata halinde ------------------------------------------------------------------------------

def test_a_catalog_that_cannot_be_written_does_not_fail_the_save(
        canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    data = canonical.data_dir
    store = open_store(data)
    index_event = CatalogAdmin.index_event

    def busy(self: CatalogAdmin, event_id: int, **kwargs: Any) -> Optional[str]:
        raise src.store.StoreBusy("catalog.db kilitli", path=self.catalog.path)

    monkeypatch.setattr(CatalogAdmin, "index_event", busy)
    with caplog.at_level(logging.WARNING):
        legacy_save(data, NO_DETAIL, {"basic": sf.basic_payload(sf.PL_NO_DETAIL)})  # hata yok
        assert store.events.get(NO_DETAIL).row_source == "listing"  # kanca kataloğa yazamadı
        # Sonraki kanca önce baştan uzlaştırır (kaçan yazma kataloğa girer), sonra kendi maçında yine düşer
        legacy_save(data, ARS, {"basic": sf.basic_payload(sf.PL_ARS)})
    warned = warnings_of(caplog)
    assert len(warned) == 1 and "was not updated after a write to event" in warned[0]  # veri dizini başına bir uyarı
    assert str(NO_DETAIL) in warned[0]
    assert store.events.get(NO_DETAIL).has_event_payload
    assert api_mod.shadow_check() == []  # kancası başarısız olan dizin karşılaştırılmaz

    # Katalog yeniden yazılabiliyor: sonraki kanca kataloğu eşitler; yeni bir hata yeniden uyarır
    monkeypatch.setattr(CatalogAdmin, "index_event", index_event)
    legacy_save(data, ARS, {"basic": sf.basic_payload(sf.PL_ARS)})
    assert differences(store) == [] and api_mod._shadow_warned == {}


def test_a_catalog_that_stays_busy_fails_the_save_of_the_downloader(
        canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """
    İndiricinin yazması Store'dadır (ST-21): katalog yazmanın kilididir. Kilit alınamazsa yazma birkaç kez,
    artan beklemeyle yeniden denenir, sonra maç başarısız olur (kalıcı olmayan StorageError: iş sürer); diske
    yarım bir şey yazılmaz. Kilit açılınca sonraki yazma başarılır.
    """
    import src.match_data_fetcher as fetcher_mod
    from src.store import events as events_mod

    data = canonical.data_dir
    store = open_store(data)
    entity_write = events_mod.EventStore._entity_write
    waits: List[float] = []

    def busy(self: Any, event_id: int, body: Any) -> Any:
        raise src.store.StoreBusy("catalog.db kilitli", path=str(data))

    monkeypatch.setattr(events_mod.EventStore, "_entity_write", busy)
    monkeypatch.setattr(fetcher_mod.time, "sleep", waits.append)
    with caplog.at_level(logging.WARNING), pytest.raises(StorageError) as info:
        fetcher_of(data)._save_match_data(str(NO_DETAIL), {"basic": sf.basic_payload(sf.PL_NO_DETAIL)})
    assert isinstance(info.value, src.store.StoreBusy) and not info.value.fatal
    assert waits == [0.5, 1.0, 2.0]  # STORE_BUSY_ATTEMPTS = 4 deneme
    assert len(messages(caplog, "MatchDataFetcher", level=logging.WARNING)) == 3
    assert store.events.get(NO_DETAIL).row_source == "listing" and differences(store) == []

    monkeypatch.setattr(events_mod.EventStore, "_entity_write", entity_write)
    fetcher_of(data)._save_match_data(str(NO_DETAIL), {"basic": sf.basic_payload(sf.PL_NO_DETAIL)})
    assert store.events.get(NO_DETAIL).has_event_payload and differences(store) == []


def test_a_store_that_cannot_be_opened_does_not_fail_the_save(tmp_path: Path,
                                                              caplog: pytest.LogCaptureFixture) -> None:
    data = tmp_path / "data"
    open_store(data).close()
    schema = data / ".meta" / "schema.json"
    schema.write_text(json.dumps({**read_json(schema), "layout_version": 99, "min_reader_layout": 99}))

    with caplog.at_level(logging.WARNING):
        legacy_save(data, ARS, {"basic": sf.basic_payload(sf.PL_ARS)})
        legacy_save(data, LIV, {"basic": sf.basic_payload(sf.PL_LIV)})

    assert len(list(data.rglob("basic.json"))) == 2  # dosyalar yazıldı
    assert len(warnings_of(caplog)) == 1 and "was not updated after a write to event" in warnings_of(caplog)[0]
    assert api_mod.shadow_check() == []  # kancası depoyu açamayan dizin karşılaştırılmaz

    # İndirici Store'a yazar: daha yeni bir sürümün dizinine yazmaz, maç başarısız olur (dosya yazılmaz)
    with pytest.raises(src.store.SchemaTooNew):
        fetcher_of(data)._save_match_data(str(NO_DETAIL), {"basic": sf.basic_payload(sf.PL_NO_DETAIL)})
    assert not (data / "v3").exists()


def test_an_unexpected_indexer_error_is_logged_not_raised_outside_the_check_mode(
        canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
        no_check: None) -> None:
    data = canonical.data_dir
    open_store(data)

    def broken(self: CatalogAdmin, event_id: int, **kwargs: Any) -> Optional[str]:
        raise RuntimeError("dizinleyicide hata")

    monkeypatch.setattr(CatalogAdmin, "index_event", broken)
    with caplog.at_level(logging.ERROR):
        legacy_save(data, NO_DETAIL, {"basic": sf.basic_payload(sf.PL_NO_DETAIL)})

    assert [m for m in messages(caplog, "Store") if m.startswith("Unexpected error while updating the catalog")]
    assert api_mod._shadow_notes == {}  # denetim kipi kapalıyken not tutulmaz


def test_an_unexpected_indexer_error_fails_the_writer_in_the_check_mode(
        canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    data = canonical.data_dir
    open_store(data)

    def broken(self: CatalogAdmin, event_id: int, **kwargs: Any) -> Optional[str]:
        raise RuntimeError("dizinleyicide hata")

    monkeypatch.setattr(CatalogAdmin, "index_event", broken)
    with pytest.raises(RuntimeError, match="dizinleyicide hata"):
        api_mod.shadow_event(data, NO_DETAIL)
    assert api_mod.shadow_check() == []  # kanca düştü: dizin karşılaştırılmaz

    # İndiricinin Store'a yazması: dizinleyicinin hatası yazmayı düşürür (kalıcı olmayan StorageError); yazma
    # yarım kaldığı için işaret durur, sonraki açılış maçı dosyalardan toparlar
    with pytest.raises(StorageError, match="dizinleyicide hata"):
        fetcher_of(data)._save_match_data(str(NO_DETAIL), {"basic": sf.basic_payload(sf.PL_NO_DETAIL)})
    monkeypatch.undo()
    for store in list(api_mod._registry.values()):
        store.close()
    store = open_store(data)
    assert store.events.get(NO_DETAIL).has_event_payload and differences(store) == []


def test_an_id_that_is_not_a_match_id_is_ignored() -> None:
    with patch.object(api_mod, "open_store") as opened:
        api_mod.shadow_event("/nowhere", "0123", "/nowhere/match_details/0123")
        api_mod.shadow_event("/nowhere", "abc")
    opened.assert_not_called()


# --- denetim: test paketinin her testin sonunda çalıştırdığı karşılaştırma -------------------------------

def _menu(data_dir: Path, monkeypatch: pytest.MonkeyPatch, *replies: str) -> Any:
    """Terminal menüsünün ayarlar işleyicisi; soruları sırayla yanıtlanır."""
    from src.ui import settings_ui

    pending = list(replies)
    monkeypatch.setattr(settings_ui, "input", lambda prompt="": pending.pop(0), raising=False)
    colors = {name: "" for name in ("SUBTITLE", "WARNING", "INFO", "SUCCESS")}
    return settings_ui.SettingsMenuHandler(MagicMock(), str(data_dir), colors)


def test_the_check_reports_a_write_that_no_hook_follows(canonical: sf.LegacyFixture, tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Store'un dışında ağaçlara yazan ürün kodu kancasını çağırmalıdır. ST-21'den beri böyle yazan bir indirici
    yok; terminal menüsünün geri yüklemesi `match_details/`'i kopyalar ve ardından `shadow_cleared`'ı çağırır.
    """
    data = tmp_path / "restored"
    shutil.copytree(canonical.data_dir / "match_details", tmp_path / "backup" / "data" / "match_details")
    open_store(data)
    monkeypatch.setattr(src.store, "shadow_cleared", lambda *args, **kwargs: None)  # kancası unutulmuş yazıcı

    _menu(data, monkeypatch, str(tmp_path / "backup"), "4").restore_data()

    found = [line for line in api_mod.shadow_check() if line.startswith(str(data))]
    assert len(found) == 1 and "written without a shadow hook afterwards" in found[0]
    assert "match_details" in found[0]
    assert api_mod.shadow_check() == []  # notlar silindi


def test_the_menus_clear_goes_through_the_store_and_needs_no_hook(canonical: sf.LegacyFixture,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Silme de bir yazmadır: ağaçları silen ürün kodunun ardından kanca çağrılmazsa denetim bunu bildirir. Terminal
    menüsünün temizlemesi ST-21'den beri ağaçları kendisi silmez, `MaintenanceService.clear` (`Store.clear`)
    siler ve kataloğu kendisi yeniden kurar: kanca (`shadow_cleared`) devre dışıyken de denetim temizdir.
    """
    from src.ui import settings_ui

    data = canonical.data_dir
    store = open_store(data)
    replies = ["3", "y"]  # maç detayları, onay
    monkeypatch.setattr(settings_ui, "input", lambda prompt="": replies.pop(0), raising=False)
    monkeypatch.setattr(src.store, "shadow_cleared", lambda *args, **kwargs: None)

    colors = {name: "" for name in ("SUBTITLE", "WARNING", "INFO", "SUCCESS")}
    settings_ui.SettingsMenuHandler(MagicMock(), str(data), colors)._clear_selected_data()
    assert replies == [] and not any((data / "match_details").iterdir())

    assert api_mod.shadow_check() == []
    assert store.catalog.diff_from_rebuild() == []


def test_a_clear_through_the_store_needs_no_hook(canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """`Store.clear` siler ve kataloğu kendisi yeniden kurar: dışarıdaki kanca (`shadow_cleared`) gerekmez."""
    data = canonical.data_dir
    store = open_store(data)
    monkeypatch.setattr(src.store, "shadow_cleared", lambda *args, **kwargs: None)

    report = store.clear("events")

    assert report.cleared == ("match_details",) and report.catalog_rebuilt
    assert api_mod.shadow_check() == []


def test_the_check_reports_a_catalog_that_differs_from_a_rebuild(canonical: sf.LegacyFixture, tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """Kanca çağrıldı ama kataloğu yanlış güncelledi (burada: hiç güncellemedi): denetim farkı bildirir."""
    data = tmp_path / "restored"
    shutil.copytree(canonical.data_dir / "match_details", tmp_path / "backup" / "data" / "match_details")
    open_store(data)
    monkeypatch.setattr(CatalogAdmin, "rebuild", lambda self, **kwargs: None)  # kanca yanlış dizinliyor

    _menu(data, monkeypatch, str(tmp_path / "backup"), "4").restore_data()
    monkeypatch.undo()  # denetimin karşılaştırması gerçek yeniden kurmayla yapılır

    found = [line for line in api_mod.shadow_check() if line.startswith(str(data))]
    assert any(f"events[{ARS}]" in line for line in found)
    assert any(f"event_slices[{ARS}, 'event', '']: missing in the catalog" in line for line in found)


def test_what_the_test_writes_itself_is_reconciled_before_the_next_product_write(canonical: sf.LegacyFixture) -> None:
    """
    Test, depo açıkken dosyaları kendisi değiştirir (imzayı değiştirmeden, yerinde): ürün kodu dizine bir daha
    dokunduğunda katalog imzalara bakmadan uzlaştırılır ve ardından yazılanın kancası gerçekten sınanır.
    """
    data = canonical.data_dir
    store = open_store(data)
    basic_file = match_dir(canonical, ARS) / "basic.json"
    edited = read_json(basic_file)
    edited["homeScore"]["current"] = 9
    stamp = basic_file.stat().st_mtime_ns
    basic_file.write_bytes(sf.dump_json(edited))  # yerinde: dizinin imzası aynı kalır
    os.utime(basic_file, ns=(stamp, stamp))
    assert store.events.get(ARS).home_score_current != 9 and api_mod.shadow_unsynced()

    # Ürün kodu dizine dokunur (Store'un dışında: istatistik menüsünün disk boyutları dosya ağacını gezer)
    stats_service.league_stats(str(data), PL)

    assert not api_mod.shadow_unsynced() and store.events.get(ARS).home_score_current == 9
    assert differences(store) == [] and api_mod.shadow_check() == []


def test_a_directory_the_test_edits_last_is_not_compared(canonical: sf.LegacyFixture) -> None:
    data = canonical.data_dir
    open_store(data)
    legacy_save(data, NO_DETAIL, {"basic": sf.basic_payload(sf.PL_NO_DETAIL)})
    (match_dir(canonical, ARS) / "statistics.json").unlink()  # son ürün çağrısından sonra, kancasız

    assert api_mod.shadow_check() == []
    # Bir sonraki açılış kataloğu imzalara güvenmeden uzlaştırır
    for store in list(api_mod._registry.values()):
        store.close()
    store = open_store(data)
    assert store.events.slice(ARS, "statistics").state == "not_requested" and differences(store) == []


# --- diff_from_rebuild: karşılaştırmanın kuralı ---------------------------------------------------------

def test_entity_tables_may_hold_more_than_a_rebuild_but_never_less(old_forms: sf.LegacyFixture) -> None:
    """
    Uzlaştırma varlık satırlarını silmez (bölüm 3.5): dosyaları silinen maçın yarışmacıları katalogda kalır ve
    fark sayılmaz. Yeniden kurmanın yazdığı bir varlık satırının eksik olması ise farktır.
    """
    data = old_forms.data_dir
    store = open_store(data)
    friendly = match_dir(old_forms, sf.event_id(sf.FRIENDLY_A))
    participants = store.info(sizes=False).rows["catalog"]["participants"]
    for path in sorted(friendly.iterdir()):
        path.unlink()
    friendly.rmdir()
    bump(friendly.parent)

    assert store.catalog.reconcile().events_removed == 1
    assert store.info(sizes=False).rows["catalog"]["participants"] == participants  # Ajax ve Celtic kaldı
    with Catalog(data.parent / "fresh.db") as fresh:
        CatalogAdmin(data, fresh, league_names=store.follows.leagues).rebuild()
        rebuilt = fresh.connection().execute("SELECT count(*) FROM participants").fetchone()[0]
        tournament, season = fresh.connection().execute(
            "SELECT tournament_id, id FROM seasons WHERE tournament_id IS NOT NULL ORDER BY id LIMIT 1").fetchone()
    assert rebuilt == participants - 2 and differences(store) == []

    with store._catalog.write() as conn:
        conn.execute("DELETE FROM tournaments WHERE id = ?", (tournament,))
        conn.execute("UPDATE seasons SET name = 'başka' WHERE id = ?", (season,))  # değer farkı serbest
    assert differences(store) == [f"tournaments[{tournament}]: missing in the catalog"]


def test_other_tables_are_compared_row_by_row(canonical: sf.LegacyFixture) -> None:
    store = open_store(canonical.data_dir)
    with store._catalog.write() as conn:
        conn.execute("UPDATE events SET stale = 1 WHERE id = ?", (ARS,))
        conn.execute("DELETE FROM event_slices WHERE event_id = ? AND key = 'statistics'", (ARS,))
        conn.execute("INSERT INTO changes (seq, ts, event_id, status_regressed, fields, row_json, segment) "
                     "VALUES (99, 1, 1, 0, '', '{}', 'x')")
        conn.execute("UPDATE legacy_roots SET scanned_at = 1")  # taramanın saati karşılaştırılmaz
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'built_at'")  # meta da

    assert differences(store) == [
        f"events[{ARS}]: stale: 1 (rebuild: 0)",
        f"event_slices[{ARS}, 'statistics', '']: missing in the catalog",
        "changes[99]: not in a rebuild",
    ]
    assert not list(canonical.data_dir.joinpath(".meta").glob("*.check"))  # veri dizinine bir şey yazılmadı


def test_a_catalog_that_is_not_usable_cannot_be_compared(canonical: sf.LegacyFixture) -> None:
    store = open_store(canonical.data_dir, sync_catalog=False)
    assert differences(store) == ["catalog: not usable (derive_version)"]


def test_sync_listings_looks_only_at_the_kinds_it_is_given(canonical: sf.LegacyFixture) -> None:
    data = canonical.data_dir
    store = open_store(data)
    (data / "seasons" / "8_LaLiga_seasons.json").unlink()
    bump(data / "seasons")
    with open(data / SCORE_CHANGES_FILE, "ab") as f:
        f.write(sf.dump_jsonl([{**sf.SCORE_CHANGES[0], "event_id": 7}]))

    only_changes = store.catalog.sync_listings(indexer.LISTING_CHANGES)
    assert (only_changes.changes, only_changes.season_lists, only_changes.events_checked) == (3, None, 0)
    assert store.entities.slices(Ref.tournament(sf.LALIGA.id)) != []  # sezon listelerine bakılmadı

    lists = store.catalog.sync_listings(indexer.LISTING_SEASON_LISTS)
    assert (lists.changes, lists.season_lists) == (None, 4)
    assert not store.catalog.sync_listings().changed and differences(store) == []
    assert isinstance(store.catalog.rebuild(), RebuildReport)


def test_layout_names_used_by_the_check_are_the_legacy_roots() -> None:
    """Denetimin "dizinlenen kökler" listesi, okuyucunun köklerinin kendisidir (biri eklenirse burada görülür)."""
    from src.store import legacy

    assert set(api_mod._SHADOW_ROOT_DIRS) == {legacy.DETAILS_DIR, legacy.MATCHES_DIR, legacy.SEASONS_DIR}
    assert set(api_mod._SHADOW_ROOT_FILES) == {legacy.CHANGES_FILE, legacy.SEASONS_CSV}
    assert api_mod._SHADOW_UNINDEXED == (f"{legacy.DETAILS_DIR}/{legacy.PROCESSED_DIR}",)
    assert layout.META_DIR == ".meta"
