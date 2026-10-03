"""
Durum servisi ve istatistik aktarıcısı (plan maddesi RD-4): sayımlar katalogdan, disk kullanımı Store'dan.

Üç şey sınanır:

  * `StatusService.summary()`'nin sayım kuralları, `tests/store_fixtures.py`'nin veri dizinlerinde elle
    denetlenmiş beklentilerle;
  * eski sayımlardan (dosya ağacını gezen `src/services/stats.py`) farklar: aşağıdaki `old_stats` o kodun
    sayımlarını yeniden üretir ve `CORRECTIONS` tablosu her farkı nedeniyle birlikte tutar. Tabloda olmayan
    hiçbir sayı değişmemiştir;
  * disk kullanımının saklanması ve aktarıcının (`src/services/stats.py`) bugünkü yanıt anahtarları.

GET /api/dashboard ve /api/stats/system yanıtlarının tamamı `tests/golden/readers/` altında sabittir
(tests/characterization/test_reader_goldens.py).
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

import conftest
import store_fixtures as sf
from src.services import stats as stats_service
from src.services import status as status_module
from src.services.status import DataSummary, DiskUsage, StatusService, TournamentCounts
from src.store import Store, open_store
from src.web import deps
from src.web.api import legacy as data_routes

NOT_STARTED_CASE = "football/A_notstarted-0-not-started__17184998"


# --- ortam ----------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _default_setting(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Ayar kabuktan gelmesin (varsayılan: yalnızca bitmiş maçlar); saklanan disk ölçümleri testler arasında taşınmasın."""
    monkeypatch.delenv("FETCH_ONLY_FINISHED", raising=False)
    status_module.forget_sizes()
    yield
    status_module.forget_sizes()


def build(name: str, tmp_path: Path) -> sf.LegacyFixture:
    fixture = sf.build_fixture(name, tmp_path / "data")
    conftest.STORE_BOUNDARY.add_data_dir(str(fixture.data_dir))
    return fixture


@pytest.fixture(params=sf.FIXTURE_NAMES)
def fx(request: pytest.FixtureRequest, tmp_path: Path) -> sf.LegacyFixture:
    return build(request.param, tmp_path)


@pytest.fixture
def canonical(tmp_path: Path) -> sf.LegacyFixture:
    return build("canonical", tmp_path)


@pytest.fixture
def old_forms(tmp_path: Path) -> sf.LegacyFixture:
    return build("legacy", tmp_path)


def summarise(fixture: sf.LegacyFixture, **kwargs: Any) -> DataSummary:
    return StatusService(open_store(fixture.data_dir)).summary(tournament_ids=tuple(fixture.leagues), **kwargs)


def tree_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.is_dir() else 0


def write_orphan_detail(data_dir: Path, event_id: int, case: str = NOT_STARTED_CASE) -> None:
    """`_no_tournament/football/<id>/basic.json`: benzersiz turnuvası olmayan bir maçın detayı."""
    ev = sf.Ev(case, sf.FRIENDLY, sf.NO_SEASON, "Home Town", "Away United", eid=event_id)
    target = data_dir / "match_details" / "_no_tournament" / "football" / str(event_id)
    target.mkdir(parents=True)
    (target / "basic.json").write_bytes(sf.dump_json(sf.basic_payload(ev)))


# --- eski sayımlar: dosya ağacını gezen src/services/stats.py (RD-4'ten önce) --------------------------


def _csv_rows(path: str) -> int:
    with open(path, "r", encoding="utf-8") as f:
        return max(0, sum(1 for _ in f) - 1)


def _season_list_len(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except ValueError:
        return 0
    if isinstance(data, dict):
        data = data.get("seasons", [])
    return len(data) if isinstance(data, list) else 0


def _summary_files(top: str) -> List[str]:
    return [os.path.join(root, name) for root, _, files in os.walk(top) for name in files
            if name.endswith(("_summary.csv", "_matches.csv"))]


def old_stats(data_dir: Path, leagues: Dict[int, str]) -> Dict[str, Any]:
    """
    Eski sayımlar, düz bir sözlük olarak: maç = özet CSV'lerinin satır toplamı, detay = `season_*` altındaki
    `basic.json` sayısı, sezon = sezon listesi dosyalarının uzunluğu (lig kartında en uzunu, toplamda hepsi).
    """
    root = str(data_dir)
    out: Dict[str, Any] = {
        "seasons": sum(_season_list_len(p) for p in glob.glob(os.path.join(root, "seasons", "*_seasons.json"))),
        "matches": sum(_csv_rows(p) for p in _summary_files(os.path.join(root, "matches"))),
        "details": len(glob.glob(os.path.join(root, "match_details", "*", "season_*", "*", "basic.json"))),
    }
    for league_id, name in leagues.items():
        lists = glob.glob(os.path.join(root, "seasons", f"{league_id}_*_seasons.json"))
        summaries = [p for top in glob.glob(os.path.join(root, "matches", f"{league_id}_*"))
                     for p in _summary_files(top)]
        detail_dirs = glob.glob(os.path.join(root, "match_details", f"{league_id}_*"))
        if not detail_dirs:
            detail_dirs = glob.glob(os.path.join(root, "match_details", name.replace(" ", "_")))
        details = sum(len(glob.glob(os.path.join(top, "season_*", "*", "basic.json"))) for top in detail_dirs)
        matches = sum(_csv_rows(p) for p in summaries)
        out[f"{league_id}.seasons"] = max((_season_list_len(p) for p in lists), default=0)
        out[f"{league_id}.seasons_fetched"] = len({os.path.basename(p).split("_", 1)[0] for p in summaries})
        out[f"{league_id}.matches"] = matches
        out[f"{league_id}.details"] = details
        out[f"{league_id}.coverage"] = round(details / matches * 100, 1) if matches else 0
        out[f"{league_id}.has_update"] = bool(details)
    return out


def new_stats(data_dir: Path, leagues: Dict[int, str]) -> Dict[str, Any]:
    system = stats_service.system_stats(str(data_dir), leagues)
    out: Dict[str, Any] = {key: system[key] for key in ("seasons", "matches", "details")}
    for entry in system["league_breakdown"]:
        for key in ("seasons", "seasons_fetched", "matches", "details", "coverage"):
            out[f"{entry['id']}.{key}"] = entry[key]
        out[f"{entry['id']}.has_update"] = entry["last_update"] is not None
    return out


# (eski, yeni) çiftleri; her satırın nedeni yanında. Tabloda olmayan sayı iki sayımda aynıdır.
CORRECTIONS: Dict[str, Dict[str, Tuple[Any, Any]]] = {
    "canonical": {
        # LaLiga 26/27 FETCH_ONLY_FINISHED=false ile yazılmış bir sezondur: özetinde 5 satır var (1 bitmiş, 1
        # başlamamış, 3 ertelenmiş / iptal). Ayar artık okurken uygulanır (varsayılan: açık): bitmiş maç ve
        # detayı indirilmiş başlamamış maç sayılır, ötekiler sayılmaz.
        "8.matches": (5, 2),
        "8.coverage": (40.0, 100.0),
        "matches": (32, 29),
    },
    "legacy": {
        # Premier League 26/27'nin iki özet dosyası var: iki maç iki kez sayılıyordu (10 satır, 8 maç)
        "17.matches": (10, 8),
        # iki düz dizin (`match_details/<id>/`, biri yalnızca birleşik dosya) detay sayılmıyordu
        "17.details": (3, 5),
        "17.coverage": (30.0, 62.5),
        # LaLiga 25/26'nın iki özet dosyası var: aynı iki maç iki kez sayılıyordu
        "8.matches": (4, 2),
        "8.coverage": (25.0, 50.0),
        # `LaLiga_seasons.json` adında lig kimliği yok: kart 0 gösteriyordu. İki özet dosyası iki ayrı sezon
        # kimliği taşıyor; maçlar tek sezonun
        "8.seasons": (0, 1),
        "8.seasons_fetched": (2, 1),
        # toplamlar: Premier League'in iki sezon listesi dosyası ayrı ayrı toplanıyordu (3 + 3 + 1 + 1);
        # `_no_tournament/` altındaki üç maç ve iki düz dizin detay sayılmıyordu; maç toplamı çift sayılan
        # dört satırı içeriyor, `_no_tournament/` maçlarını içermiyordu (14 - 4 + 3)
        "seasons": (8, 5),
        "details": (4, 9),
        "matches": (14, 13),
    },
    "processed_only": {
        # program dosyası hiç yok (özet de yok): detayı indirilmiş iki maç "maç" sayılmıyordu
        "17.matches": (0, 2),
        "17.coverage": (0, 100.0),
        "matches": (0, 2),
        # terminal arayüzünün "sezon sayısı": özet dosyası olan sezonlardı, şimdi maçı bilinen sezonlar
        "17.seasons_fetched": (0, 1),
    },
    "empty": {},
}


def test_counts_differ_from_the_file_walk_only_where_documented(fx: sf.LegacyFixture) -> None:
    old = old_stats(fx.data_dir, fx.leagues)
    new = new_stats(fx.data_dir, fx.leagues)
    assert set(old) == set(new)
    assert {key: (old[key], new[key]) for key in old if old[key] != new[key]} == CORRECTIONS[fx.name]


# --- sayım kuralları -------------------------------------------------------------------------------


def counts(summary: DataSummary, tournament_id: Optional[int]) -> Tuple[int, int, int, float]:
    row = summary.tournament(tournament_id)
    return row.matches, row.details, row.seasons, row.coverage


def test_summary_of_the_current_layout(canonical: sf.LegacyFixture) -> None:
    summary = summarise(canonical)
    assert (summary.matches, summary.details, summary.seasons) == (29, 23, 11)
    assert summary.only_finished is True and summary.catalog_rebuild_reason is None
    assert summary.data_dir == str(canonical.data_dir)
    assert counts(summary, 17) == (12, 10, 3, 83.3)
    assert counts(summary, 19) == (3, 2, 2, 66.7)
    assert counts(summary, 132) == (6, 5, 2, 83.3)
    assert counts(summary, 2361) == (6, 4, 2, 66.7)
    assert counts(summary, 8) == (2, 2, 2, 100.0)
    # maçı olmayan yapılandırılmış lig de dökümdedir; bilinmeyen turnuva sıfırdır
    assert summary.tournament(35) == TournamentCounts(35)
    assert summary.tournament(999) == TournamentCounts(999)
    assert [row.tournament_id for row in summary.tournaments] == [8, 17, 19, 132, 2361, 35]
    premier = summary.tournament(17)
    assert (premier.events, premier.finished, premier.seasons_with_events) == (15, 12, 2)
    assert premier.last_update == sf.BASE_MTIME and summary.tournament(35).last_update is None


def test_summary_of_old_forms(old_forms: sf.LegacyFixture) -> None:
    summary = summarise(old_forms)
    assert (summary.matches, summary.details, summary.seasons) == (13, 9, 5)
    assert counts(summary, 17) == (8, 5, 3, 62.5)
    assert counts(summary, 8) == (2, 1, 1, 50.0)
    assert counts(summary, 2361) == (0, 0, 1, 0.0)
    # benzersiz turnuvası olmayan maçlar tek satırda toplanır ve en başta gelir
    assert counts(summary, None) == (3, 3, 0, 100.0)
    assert [row.tournament_id for row in summary.tournaments] == [None, 8, 17, 2361]


def test_summary_of_an_empty_directory(tmp_path: Path) -> None:
    summary = summarise(build("empty", tmp_path))
    assert (summary.matches, summary.details, summary.seasons) == (0, 0, 0)
    assert summary.tournaments == (TournamentCounts(17),)
    assert summary.disk is not None and summary.disk.total == 0


def test_only_finished_rule(canonical: sf.LegacyFixture) -> None:
    """
    Ayar açıkken: bitmiş maçlar ve detayı indirilmiş bitmemiş maçlar. Premier League'de programda görünen iki
    başlamamış ve bir ertelenmiş maç sayılmaz (12 / 15). NBA'de listede bitmiş görünen, detayı ise iptal diyen
    maç detayı olduğu için sayılır (5 bitmiş + 1). Ayar kapalıyken katalogdaki bütün maçlar.
    """
    filtered = summarise(canonical, only_finished=True, sizes=False)
    assert [filtered.tournament(tid).matches for tid in (17, 132, 8)] == [12, 6, 2]
    nba = filtered.tournament(132)
    assert (nba.events, nba.finished, nba.details) == (6, 5, 5)
    everything = summarise(canonical, only_finished=False, sizes=False)
    assert [everything.tournament(tid).matches for tid in (17, 132, 8)] == [15, 6, 5]
    assert everything.only_finished is False and everything.matches == 35
    for summary in (filtered, everything):
        assert all(row.details <= row.matches for row in summary.tournaments)
        assert summary.matches == sum(row.matches for row in summary.tournaments)
        assert summary.details == sum(row.details for row in summary.tournaments) == 23


@pytest.mark.parametrize("value, expected", [(None, True), ("true", True), ("TRUE", True), ("false", False),
                                             ("False", False), ("0", False)])
def test_the_setting_is_read_at_call_time(canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch,
                                          value: Optional[str], expected: bool) -> None:
    """Varsayılan kural FETCH_ONLY_FINISHED'dan gelir; yazıcılarla aynı okuma (src/utils.py)."""
    if value is not None:
        monkeypatch.setenv("FETCH_ONLY_FINISHED", value)
    assert status_module.only_finished_setting() is expected
    summary = summarise(canonical, sizes=False)
    assert summary.only_finished is expected
    assert summary.tournament(17).matches == (12 if expected else 15)


def test_unfinished_matches_without_a_tournament(canonical: sf.LegacyFixture) -> None:
    """
    Turnuvasız maçların "bitmemiş ama detayı var" sayısı, bütün katalogdaki sayıdan turnuvalarınki düşülerek
    bulunur: NBA'deki ve LaLiga'daki birer maç ona karışmaz.
    """
    write_orphan_detail(canonical.data_dir, 15900001)
    write_orphan_detail(canonical.data_dir, 15900002, "football/A_finished-100-ended__16837335")
    summary = summarise(canonical, sizes=False)
    orphans = summary.tournament(None)
    assert (orphans.events, orphans.finished, orphans.details, orphans.matches) == (2, 1, 2, 2)
    assert summary.tournaments[0] is orphans
    assert (summary.matches, summary.details) == (29 + 2, 23 + 2)
    assert [summary.tournament(tid).matches for tid in (132, 8)] == [6, 2]


def test_a_match_with_details_and_no_schedule_is_a_match(tmp_path: Path) -> None:
    """Eski sayım yalnızca özet satırlarını sayıyordu: programı indirilmemiş bir maçın detayı kapsamı bozuyordu."""
    summary = summarise(build("processed_only", tmp_path))
    assert counts(summary, 17) == (2, 2, 0, 100.0)
    assert summary.tournament(17).seasons_with_events == 1


# --- sezonlar --------------------------------------------------------------------------------------


def test_every_season_list_counts_once(old_forms: sf.LegacyFixture) -> None:
    seasons_dir = old_forms.data_dir / "seasons"
    assert sorted(p.name for p in seasons_dir.iterdir()) == [
        "17_Premier_League_seasons.json", "17_seasons.json", "2361_Wimbledon, Men_seasons.json",
        "LaLiga_seasons.json"]
    summary = summarise(old_forms, sizes=False)
    assert [summary.tournament(tid).seasons for tid in (17, 8, 2361)] == [3, 1, 1]
    assert summary.seasons == 5


def test_season_lists_of_leagues_that_are_not_configured_count_in_the_total(canonical: sf.LegacyFixture) -> None:
    """
    Yapılandırmadan çıkarılmış, maçı da olmayan bir ligin sezon listesi dosyası durur: toplam onu da sayar
    (eski sayım `seasons/` altındaki her dosyayı topluyordu). Lig dökümde ancak istenirse yer alır.
    """
    listed = [{"name": f"Serie A {year}", "year": year, "id": 50000 + i}
              for i, year in enumerate(("26/27", "25/26", "24/25", "23/24"))]
    (canonical.data_dir / "seasons" / "23_Serie_A_seasons.json").write_bytes(sf.dump_json({"seasons": listed}))
    store = open_store(canonical.data_dir)
    summary = StatusService(store).summary(tournament_ids=tuple(canonical.leagues), sizes=False)
    assert summary.seasons == 11 + 4
    assert 23 not in [row.tournament_id for row in summary.tournaments]
    assert StatusService(store).summary(tournament_ids=(23,), sizes=False).tournament(23).seasons == 4


def test_a_season_that_no_list_names_is_not_counted(tmp_path: Path) -> None:
    """Sezon listesi olmayan dizin: maçların sezonu katalogda bir satırdır, ama "sezon listesi" sayımına girmez."""
    fixture = build("processed_only", tmp_path)
    store = open_store(fixture.data_dir)
    assert [(season.id, season.listed) for season in store.entities.seasons(17)] == [(96668, False)]
    summary = StatusService(store).summary(tournament_ids=(17,), sizes=False)
    assert summary.seasons == 0 and summary.tournament(17).seasons == 0
    assert store.info(sizes=False).rows["catalog"]["seasons"] == 1


def test_every_unlisted_season_belongs_to_a_tournament_the_summary_asks_about(fx: sf.LegacyFixture) -> None:
    """
    Sezon toplamı "bütün sezon satırları eksi listede olmayanlar" diye hesaplanır; bu, listede olmayan her
    sezonun turnuvasının maçı ya da turnuva satırı olmasına dayanır.
    """
    store = open_store(fx.data_dir)
    asked = {row.tournament_id for row in store.events.summary()} | {row.id for row in store.entities.tournaments(limit=500)}
    with store._catalog.read() as conn:  # type: ignore[union-attr]
        rows = conn.execute("SELECT tournament_id, listed FROM seasons").fetchall()
    assert all(tournament_id in asked for tournament_id, listed in rows if not listed)
    summary = StatusService(store).summary(sizes=False)
    assert summary.seasons == sum(1 for _, listed in rows if listed)


# --- katalogla birlikte değişir ----------------------------------------------------------------------


def test_summary_follows_the_catalog(canonical: sf.LegacyFixture) -> None:
    store = open_store(canonical.data_dir)
    service = StatusService(store)
    before = service.summary(sizes=False)
    write_orphan_detail(canonical.data_dir, 15900003, "football/A_finished-100-ended__16837335")
    assert service.summary(sizes=False) == before  # katalog henüz bilmiyor: servis dosyalara bakmaz
    store.catalog.reconcile(deep=True)  # imzalara güvenmeden: dizin mtime'ı aynı ana düşebilir
    after = service.summary(sizes=False)
    assert (after.matches, after.details) == (before.matches + 1, before.details + 1)
    assert counts(after, None) == (1, 1, 0, 100.0)


def test_the_dashboard_follows_a_clear_at_once(canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Web'den temizleme kataloğu yeniden kurar (kancası var): sayılar ve disk kullanımı, saklanan ölçümün süresi
    dolmadan değişir. Detaylar silinince maçlar programdaki halleriyle kalır.
    """
    data_dir, leagues = str(canonical.data_dir), canonical.leagues
    monkeypatch.setattr(deps.config_manager(), "get_data_dir", lambda: data_dir)
    before = data_routes._build_dashboard_sync(data_dir, leagues)
    assert before["totals"] == {"leagues": 6, "matches": 29, "details": 23}
    assert before["disk_usage"]["details"] > 0

    assert data_routes._clear_data_sync("match_details") == {"status": "success", "cleared": ["match_details"]}
    after = data_routes._build_dashboard_sync(data_dir, leagues)
    assert after["totals"]["details"] == 0 and after["disk_usage"]["details"] == 0
    assert after["disk_usage"]["matches"] == before["disk_usage"]["matches"] > 0
    assert all(card["details"] == 0 and card["last_update"] is None for card in after["leagues"])
    # program dosyaları duruyor: bitmiş görünen maçlar sayılmaya devam eder (LaLiga'da yalnızca bitmiş olan)
    assert {card["id"]: card["matches"] for card in after["leagues"]} == {17: 12, 19: 3, 132: 6, 2361: 6, 8: 1, 35: 0}

    assert data_routes._clear_data_sync("all")["status"] == "success"
    empty = data_routes._compute_system_stats_sync(data_dir, leagues)
    assert (empty["seasons"], empty["matches"], empty["details"], empty["league_breakdown"]) == (0, 0, 0, [])
    assert empty["disk_usage"]["total"] == 0


def test_a_catalog_that_was_not_built_reports_why(canonical: sf.LegacyFixture) -> None:
    store = open_store(canonical.data_dir, sync_catalog=False)
    summary = StatusService(store).summary(tournament_ids=(17,))
    assert summary.catalog_rebuild_reason == "derive_version"
    assert (summary.matches, summary.details, summary.seasons) == (0, 0, 0)
    assert summary.tournaments == (TournamentCounts(17),)
    assert summary.disk is not None and summary.disk.details > 0  # disk kullanımı kataloğa bağlı değildir


# --- disk kullanımı ----------------------------------------------------------------------------------


def test_disk_usage_is_the_size_of_the_top_level_entries(fx: sf.LegacyFixture) -> None:
    disk = summarise(fx).disk
    assert disk is not None
    data_dir = fx.data_dir
    assert disk.seasons == tree_bytes(data_dir / "seasons")
    assert disk.matches == tree_bytes(data_dir / "matches")
    assert disk.details == tree_bytes(data_dir / "match_details")
    assert disk.datasets == tree_bytes(data_dir / "datasets")
    assert disk.total == disk.seasons + disk.matches + disk.details + disk.datasets
    # Store her üst düzey girdiyi verir; toplam yalnızca dört veri alanıdır
    assert ".meta" in disk.entries and disk.entries[".meta"] > 0
    assert set(disk.entries) == {p.name for p in data_dir.iterdir()}


def test_disk_total_includes_datasets(old_forms: sf.LegacyFixture) -> None:
    """Gösterge panelinin toplamı, listelemediği `datasets/` dizinini de içerir (bugünkü davranış; altın dosyada sabit)."""
    summary = summarise(old_forms)
    data_dir = old_forms.data_dir
    areas = {"seasons": tree_bytes(data_dir / "seasons"), "matches": tree_bytes(data_dir / "matches"),
             "details": tree_bytes(data_dir / "match_details"), "datasets": tree_bytes(data_dir / "datasets")}
    assert all(size > 0 for size in areas.values())
    total = sum(areas.values())
    usage = stats_service.disk_usage(summary)
    assert usage == {**areas, "total": total, "formatted_total": stats_service.format_size(total)}
    dashboard = data_routes._build_dashboard_sync(str(data_dir), old_forms.leagues)
    assert dashboard["disk_usage"] == {key: usage[key] for key in ("seasons", "matches", "details", "total",
                                                                  "formatted_total")}
    assert dashboard["disk_usage"]["total"] > sum(dashboard["disk_usage"][key] for key in ("seasons", "matches", "details"))


class InfoCalls:
    """`Store.info`'yu sarar: ağacı gezen (`sizes=True`) çağrıları sayar."""

    def __init__(self, store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
        self.walks = 0
        original = store.info

        def info(*, sizes: bool = True) -> Any:
            self.walks += int(sizes)
            return original(sizes=sizes)

        monkeypatch.setattr(store, "info", info)


def test_disk_usage_is_measured_once_while_the_catalog_is_unchanged(canonical: sf.LegacyFixture,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    store = open_store(canonical.data_dir)
    calls = InfoCalls(store, monkeypatch)
    service = StatusService(store)
    first = service.summary().disk
    assert calls.walks == 1 and first is not None
    # kataloğun görmediği bir dosya (dışa aktarma): saklanan ölçüm kullanılır, sayı süre dolana kadar eskidir
    (canonical.data_dir / "datasets").mkdir(exist_ok=True)
    (canonical.data_dir / "datasets" / "export.csv").write_bytes(b"x" * 1000)
    assert service.summary().disk is first and StatusService(store).summary().disk is first
    assert calls.walks == 1
    assert service.summary(sizes=False).disk is None and calls.walks == 1


def test_disk_usage_is_measured_again_after_its_age(canonical: sf.LegacyFixture,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    store = open_store(canonical.data_dir)
    calls = InfoCalls(store, monkeypatch)
    service = StatusService(store)
    clock = [1000.0]
    monkeypatch.setattr(status_module, "_now", lambda: clock[0])
    first = service.summary().disk
    (canonical.data_dir / "datasets").mkdir(exist_ok=True)
    (canonical.data_dir / "datasets" / "export.csv").write_bytes(b"x" * 1000)
    clock[0] += status_module.SIZES_MAX_AGE - 1
    assert service.summary().disk is first
    clock[0] += 1
    second = service.summary().disk
    assert calls.walks == 2 and first is not None and second is not None
    assert second.datasets == first.datasets + 1000 and second.total == first.total + 1000
    # sizes_max_age=0: her çağrıda ölçülür
    assert service.summary(sizes_max_age=0).disk is not second and calls.walks == 3


def test_disk_usage_is_measured_again_when_the_catalog_changes(canonical: sf.LegacyFixture,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """İndirme ve temizleme kataloğu değiştirir (satır sayıları, en yeni `updated_at`): ölçüm beklemeden yenilenir."""
    store = open_store(canonical.data_dir)
    calls = InfoCalls(store, monkeypatch)
    service = StatusService(store)
    first = service.summary().disk
    write_orphan_detail(canonical.data_dir, 15900004)
    store.catalog.reconcile(deep=True)  # imzalara güvenmeden: dizin mtime'ı aynı ana düşebilir
    second = service.summary().disk
    assert calls.walks == 2 and first is not None and second is not None
    assert second.details > first.details
    assert service.summary().disk is second and calls.walks == 2


def test_forget_sizes(canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    store = open_store(canonical.data_dir)
    calls = InfoCalls(store, monkeypatch)
    service = StatusService(store)
    service.summary()
    status_module.forget_sizes(store)
    service.summary()
    assert calls.walks == 2
    status_module.forget_sizes()
    service.summary()
    assert calls.walks == 3


def test_disk_usage_of_an_unmeasured_summary() -> None:
    assert DiskUsage().total == 0
    summary = DataSummary(data_dir="x", only_finished=True)
    assert stats_service.disk_usage(summary) == {"seasons": 0, "matches": 0, "details": 0, "datasets": 0,
                                                 "total": 0, "formatted_total": "0.0 B"}


# --- aktarıcı: bugünkü anahtarlar --------------------------------------------------------------------


def test_league_stats_keeps_its_keys(canonical: sf.LegacyFixture) -> None:
    stats = stats_service.league_stats(str(canonical.data_dir), 17, "Premier League")
    assert list(stats) == ["id", "name", "seasons", "seasons_fetched", "matches", "details", "coverage",
                           "last_update", "disk"]
    assert stats == {
        "id": 17, "name": "Premier League", "seasons": 3, "seasons_fetched": 2, "matches": 12, "details": 10,
        "coverage": 83.3, "last_update": dt.datetime.fromtimestamp(sf.BASE_MTIME).isoformat(),
        "disk": stats["disk"],
    }
    # lig başına disk boyutları lig dizinlerinden ölçülür (Store bu dökümü vermez)
    data_dir = canonical.data_dir
    assert stats["disk"] == {
        "seasons": (data_dir / "seasons" / "17_Premier_League_seasons.json").stat().st_size,
        "matches": tree_bytes(data_dir / "matches" / "17_Premier_League"),
        "details": tree_bytes(data_dir / "match_details" / "17_Premier_League"),
        "total": stats["disk"]["seasons"] + stats["disk"]["matches"] + stats["disk"]["details"],
    }
    assert stats["disk"]["details"] > 0


def test_league_stats_of_a_league_without_data(canonical: sf.LegacyFixture) -> None:
    stats = stats_service.league_stats(str(canonical.data_dir), 35, "Bundesliga")
    assert stats == {"id": 35, "name": "Bundesliga", "seasons": 0, "seasons_fetched": 0, "matches": 0, "details": 0,
                     "coverage": 0, "last_update": None, "disk": {"seasons": 0, "matches": 0, "details": 0, "total": 0}}
    assert type(stats["coverage"]) is int  # JSON'da `0`, `0.0` değil (altın dosyalar türü de karşılaştırır)


def test_league_disk_finds_the_directory_without_an_id(old_forms: sf.LegacyFixture) -> None:
    stats = stats_service.league_stats(str(old_forms.data_dir), 8, "LaLiga")
    assert stats["disk"]["details"] == tree_bytes(old_forms.data_dir / "match_details" / "LaLiga") > 0
    assert stats["disk"]["seasons"] == 0  # `LaLiga_seasons.json`: adında kimlik yok, lig boyutuna girmez


def test_system_stats_keeps_its_keys(canonical: sf.LegacyFixture) -> None:
    leagues = canonical.leagues
    system = stats_service.system_stats(str(canonical.data_dir), leagues)
    assert list(system) == ["leagues", "seasons", "matches", "details", "league_breakdown", "disk_usage"]
    assert (system["leagues"], system["seasons"], system["matches"], system["details"]) == (6, 11, 29, 23)
    assert [entry["id"] for entry in system["league_breakdown"]] == list(leagues)
    for entry in system["league_breakdown"]:
        assert entry == stats_service.league_stats(str(canonical.data_dir), entry["id"], entry["name"])
    assert system["disk_usage"] == stats_service.disk_usage(stats_service.data_summary(str(canonical.data_dir)))
    assert list(system["disk_usage"]) == ["seasons", "matches", "details", "datasets", "total", "formatted_total"]
    assert system["disk_usage"]["total"] == sum(
        tree_bytes(canonical.data_dir / name) for name in ("seasons", "matches", "match_details", "datasets"))


def test_the_routes_do_not_walk_the_tree(canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """Web yanıtları yalnızca özetten kurulur: lig dizinlerini gezen işlevler çağrılmaz."""

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the web routes must not walk the league directories")

    for name in ("dir_size", "_league_dirs", "league_stats", "system_stats"):
        monkeypatch.setattr(stats_service, name, forbidden)
    data_dir, leagues = str(canonical.data_dir), canonical.leagues
    dashboard = data_routes._build_dashboard_sync(data_dir, leagues)
    assert dashboard["totals"] == {"leagues": 6, "matches": 29, "details": 23}
    assert [card["id"] for card in dashboard["leagues"]] == list(leagues)
    assert set(dashboard["leagues"][0]) == {"id", "name", "seasons", "matches", "details", "coverage", "last_update"}
    system = data_routes._compute_system_stats_sync(data_dir, leagues)
    # döküm: maçı ya da detayı olan ligler, maç sayısına göre (eşitlikte yapılandırma sırası)
    assert [(b["id"], b["matches"]) for b in system["league_breakdown"]] == [
        (17, 12), (132, 6), (2361, 6), (19, 3), (8, 2)]
    assert set(system["league_breakdown"][0]) == {"id", "name", "matches", "details", "coverage"}
