"""
CatalogAdmin.reconcile: kataloğun, arkasından değişen dosyalarla yeniden eşitlenmesi (plan maddesi ST-08;
docs/design/01-storage.md bölüm 3.5).

Ölçüt hep aynıdır: uzlaştırmadan sonra katalog, aynı ağacın sıfırdan kurulmuş kataloğuna eşittir. Varlık
tablolarında (turnuva, sezon, yarışmacı, spor, kategori) uzlaştırma satır silmez ve var olan satırı
değiştirmez; dosyalar silinen senaryolarda o tablolar karşılaştırmanın dışında tutulur (`facts`).

İmzalar mtime'a bakar ve dosya sistemlerinin saat çözünürlüğü kabadır: ağacı değiştiren yardımcılar
(`write`, `remove`) dizinin mtime'ını açıkça ileri alır.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Tuple

import pytest

import store_fixtures as sf
from src.store import codec, entities, files, indexer, layout, manifest, open_store
from src.store.catalog import Catalog
from src.store.errors import StoreBusy, StoreError
from src.store.indexer import CatalogAdmin
from src.store.legacy import LegacyReader
from test_store_indexer_listings import (
    ARS,
    BASE,
    LEE,
    LIV,
    NOW,
    PL_DETAILS,
    PL_SEASON,
    STALE_CASES,
    UTC,
    bump,
    detail,
    event_row,
    facts,
    listed,
    listing_rows,
    page,
    read_json,
    rebuilt,
    remove,
    rows,
    schedule_slices,
    snapshot,
    undated,
    utc,
    write,
    write_v3,
)

NEW = sf.event_id(sf.PL_NEW)  # canonical: statistics dilimi eksik, hata işareti var
NO_DETAIL = sf.event_id(sf.PL_NO_DETAIL)  # canonical: yalnızca listede
FUTURE = sf.event_id(sf.PL_FUTURE)


@pytest.fixture
def make_admin() -> Iterator[Callable[..., CatalogAdmin]]:
    opened: List[CatalogAdmin] = []

    def make(data_dir: Path, **kwargs: Any) -> CatalogAdmin:
        admin = CatalogAdmin(data_dir, clock=lambda: float(NOW), **kwargs)
        opened.append(admin)
        return admin

    yield make
    for admin in opened:
        admin.close()


@pytest.fixture(params=sf.FIXTURE_NAMES)
def fx(request: pytest.FixtureRequest, tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture(request.param, tmp_path / "data")


@pytest.fixture
def canonical(tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture("canonical", tmp_path / "data")


@pytest.fixture
def old_forms(tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture("legacy", tmp_path / "data")


def tree_state(root: Path, skip: Tuple[str, ...] = (".meta",)) -> Dict[str, Tuple[str, int]]:
    """Ağacın tamamı: göreli yol → (içerik özeti ya da "dir", mtime_ns)."""
    out: Dict[str, Tuple[str, int]] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(skip):
            continue
        out[rel] = ("dir" if path.is_dir() else codec.sha256_hex(path.read_bytes()), path.stat().st_mtime_ns)
    return out


def summary(report: indexer.ReconcileReport) -> Dict[str, Any]:
    return {"events": (report.events_indexed, report.events_removed), "seasons": report.seasons,
            "season_lists": report.season_lists, "changes": report.changes}


UNCHANGED = {"events": (0, 0), "seasons": [], "season_lists": None, "changes": None}


# --- değişmeyen ağaç --------------------------------------------------------------------------------------

def test_reconcile_right_after_a_rebuild_changes_nothing_and_reads_no_payload(
        fx: sf.LegacyFixture, make_admin, monkeypatch: pytest.MonkeyPatch) -> None:
    admin = make_admin(fx.data_dir, league_names=fx.leagues)
    admin.rebuild()
    before = snapshot(admin.catalog)
    state = tree_state(fx.data_dir)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("uzlaştırma, imzası değişmeyen bir dosyayı okudu")

    # sezon listeleri küçüktür ve her taramada okunur; öteki her şey yalnızca imzası değişince okunur
    for name in ("read_event", "read_schedule", "read_summary_rows", "change_log", "read_payload"):
        monkeypatch.setattr(LegacyReader, name, forbidden)
    report = admin.reconcile()

    assert not report.changed and summary(report) == UNCHANGED
    assert (report.deep, report.v3, report.verify, report.pending, report.pending_skipped) == (False, False, None, 0, 0)
    assert report.events_checked == len(fx.detail_ids) - (1 if fx.name == "legacy" else 0)  # basic.json'sız dizin
    assert snapshot(admin.catalog) == before
    assert tree_state(fx.data_dir) == state  # veri dosyalarına dokunulmaz
    assert not admin.reconcile().changed and admin.verify().ok


# --- eklenen dosyalar -------------------------------------------------------------------------------------

def test_reconcile_picks_up_files_added_to_legacy_directories_after_the_build(
        canonical: sf.LegacyFixture, make_admin, tmp_path: Path) -> None:
    data = canonical.data_dir
    admin = make_admin(data, league_names=canonical.leagues)
    admin.rebuild()
    before = snapshot(admin.catalog)

    # sezon dizinine yeni bir tur dosyası: yeni bir maç ve zaten listelenen bir maçın daha yeni hali
    brand_new = sf.Ev(sf.PL_ARS.case, sf.PL, sf.PL_2627, "Luton Town", "Stoke City", round=4, eid=17300001)
    page(data, f"{PL_SEASON}/round_4.json", [listed(brand_new), listed(sf.PL_FUTURE, winnerCode=1)], BASE + 100,
         _complete=False)
    # maç dizinine yeni bir dilim dosyası (ve hata işareti silinir)
    write(data, f"{PL_DETAILS}/{NEW}/statistics.json",
          sf.slice_payload("statistics", sf.basic_payload(sf.PL_NEW)), BASE + 50)
    # yalnızca listede duran maçın dizini; hiçbir listede geçmeyen yeni bir maç
    detail(data, sf.PL_NO_DETAIL, slices=("statistics",), mtime=BASE + 50)
    detail(data, sf.FRIENDLY_A, "match_details/_no_tournament/football/15500001", mtime=BASE + 50)
    # var olan ligin altında yeni bir sezon dizini; yeni bir lig dizini
    old_season = sf.Ev(sf.PL_LIV.case, sf.PL, sf.PL_2425, "Luton Town", "Stoke City", round=1, eid=13000001)
    page(data, "matches/17_Premier_League/61627_Premier_League_24_25/round_1.json", [listed(old_season)],
         BASE + 10, _complete=True)
    season = sf.Season(98001, "Bundesliga 26/27", "26/27")
    german = sf.Ev(sf.PL_ARS.case, sf.BUNDESLIGA, season, "Bayern München", "Borussia Dortmund", eid=17400001)
    page(data, "matches/35_Bundesliga/98001_Bundesliga_26_27/events_last_0.json", [listed(german)], BASE + 20)
    # yeni bir sezon listesi ve değişen bir sezon listesi
    write(data, "seasons/35_Bundesliga_seasons.json", {"seasons": [{"id": 98001, "name": season.name, "year": "26/27"}]})
    write(data, "seasons/17_Premier_League_seasons.json", {"seasons": [
        {"id": 99999, "name": "Premier League 27/28", "year": "27/28"},
        {"id": 96668, "name": "Premier League 26/27", "year": "26/27"}]}, BASE + 30)
    # değişiklik günlüğüne eklenen satır
    with open(data / "score_changes.jsonl", "ab") as f:
        f.write(json.dumps({**sf.SCORE_CHANGES[0], "event_id": NEW}).encode() + b"\n")

    report = admin.reconcile()

    assert report.changed and summary(report) == {
        "events": (3, 0), "seasons": [(17, 61627), (17, 96668), (35, 98001)], "season_lists": 6, "changes": 3}
    assert report.events_checked == len(canonical.detail_ids) + 2 and report.problems == []
    cat = admin.catalog
    assert snapshot(cat) != before
    # Varlık tabloları dahil; yalnızca "ilk yazan kazanır" satırlarının zamanı farklı olabilir (turnuva 17'nin
    # satırı uzlaştırmada eski listeden kalır, yeniden kurmada sıradaki ilk sezonun listesinden gelir)
    fresh = rebuilt(data, tmp_path, league_names=canonical.leagues)
    assert facts(snapshot(cat)) == facts(fresh) and undated(snapshot(cat)) == undated(fresh)

    found = listing_rows(cat)
    assert {17300001, 13000001, 17400001, FUTURE} <= set(found) and NO_DETAIL not in found
    future = found[FUTURE]
    assert (future["listed_in"], future["winner_code"], future["first_seen_at"], future["updated_at"]) == (
        "round_4", 1, BASE, BASE + 100)
    assert set(schedule_slices(cat, 96668)) == {"round_1", "round_2", "round_3", "round_4"}
    promoted = event_row(cat, NO_DETAIL)
    assert (promoted["row_source"], promoted["layout"], promoted["listed_in"], promoted["stale"]) == (
        "event", "legacy", "round_2", 0)
    statistics = rows(cat, "event_slices", f"WHERE event_id = {NEW} AND key = 'statistics'")[0]
    assert (statistics["state"], statistics["error_reason"], statistics["fetched_at"]) == ("ok", "403", BASE + 50)
    assert event_row(cat, 15500001)["listed_in"] is None
    assert {r["id"]: r["name"] for r in rows(cat, "tournaments", "WHERE id = 35")} == {35: "Bundesliga"}
    seasons = {r["id"]: (r["listed"], r["position"]) for r in rows(cat, "seasons", "WHERE tournament_id = 17")}
    assert seasons == {99999: (1, 0), 96668: (1, 1), 76986: (0, None), 61627: (0, None)}
    assert [(r["seq"], r["event_id"]) for r in rows(cat, "changes")] == [(1, 17099711), (2, 17060394), (3, NEW)]

    again = admin.reconcile()
    assert not again.changed and admin.verify().ok and admin.verify(deep=True).ok


def test_reconcile_follows_removed_files(canonical: sf.LegacyFixture, make_admin, tmp_path: Path) -> None:
    data = canonical.data_dir
    admin = make_admin(data, league_names=canonical.leagues)
    admin.rebuild()
    cup = sf.event_id(sf.CUP_PEN)

    remove(data, f"{PL_SEASON}/round_2.json")  # sayfa: yalnızca orada listelenen maçlar
    remove(data, f"{PL_DETAILS}/{ARS}")  # listelenen maçın dizini: liste satırına döner
    remove(data, f"{PL_DETAILS}/{NEW}")  # artık hiçbir sayfada listelenmeyen maçın dizini: satırı kalmaz
    remove(data, "matches/19_FA_Cup/97110_FA_Cup_26_27")  # sezon dizini (özeti lig dizininde duruyor)
    remove(data, "matches/132_NBA")  # lig dizini, özetleriyle birlikte
    remove(data, "seasons/2361_Wimbledon,_Men_seasons.json")
    remove(data, "score_changes.jsonl")

    report = admin.reconcile()

    assert summary(report) == {
        "events": (0, 2), "seasons": [(17, 96668), (19, 97110), (132, 80229)], "season_lists": 4, "changes": 0}
    cat = admin.catalog
    fresh = rebuilt(data, tmp_path, league_names=canonical.leagues)
    assert facts(snapshot(cat)) == facts(fresh)

    assert event_row(cat, NEW) is None and event_row(cat, NO_DETAIL) is None
    ars = event_row(cat, ARS)
    assert (ars["row_source"], ars["layout"], ars["listed_in"]) == ("listing", None, "round_1")
    gone = event_row(cat, sf.event_id(sf.PL_AVL))  # olay yükü var, sayfası kalmadı
    assert (gone["row_source"], gone["listed_in"], gone["stale"]) == ("event", None, 0)
    # sayfaları silinen kupa sezonu özet CSV'sinden dizinlenir: olay yükü olan maç tur sütunundan listed_in alır
    assert (event_row(cat, cup)["row_source"], event_row(cat, cup)["listed_in"]) == ("event", "round_28")
    second = event_row(cat, sf.event_id(sf.CUP_PEN_2))
    assert (second["row_source"], second["listed_in"], second["home_id"], second["home_score_current"]) == (
        "listing", "round_28", None, 6)
    assert schedule_slices(cat, 97110) == {} and schedule_slices(cat, 80229) == {}
    void = event_row(cat, sf.event_id(sf.NBA_VOID))  # lig dizini gitti: bayrak da düşer
    assert (void["listed_in"], void["stale"]) == (None, 0)
    assert event_row(cat, sf.event_id(sf.NBA_OT)) is None
    assert rows(cat, "changes") == [] and not rows(cat, "entity_slices", "WHERE entity_id = 2361")
    assert not [r for r in rows(cat, "legacy_roots")
                if r["path"].startswith(("matches/132_NBA", "matches/19_FA_Cup/97110"))
                or r["path"] in ("score_changes.jsonl", "seasons/2361_Wimbledon,_Men_seasons.json")]

    # varlık satırları silinmez: listeden çıkan sezon `listed = 0` olur, turnuva ve yarışmacılar durur
    wimbledon = {r["id"]: (r["listed"], r["position"]) for r in rows(cat, "seasons", "WHERE tournament_id = 2361")}
    assert wimbledon == {79116: (0, None), 63966: (0, None)}
    assert {r["id"] for r in rows(cat, "tournaments")} == {8, 17, 19, 132, 2361}
    for table in ("tournaments", "seasons", "participants", "sports"):
        key = "slug" if table == "sports" else "id"
        assert {r[key] for r in fresh[table]} <= {r[key] for r in rows(cat, table)}, table
    assert not admin.reconcile().changed and admin.verify(deep=True).ok


# --- değişen listeler -------------------------------------------------------------------------------------

@pytest.mark.parametrize("case", ["same", "status_code", "score_period2", "score_new_field_null", "winner_code",
                                  "team_name"])
def test_changed_page_sets_stale_like_a_rebuild(tmp_path: Path, make_admin, case: str) -> None:
    edit, differs = STALE_CASES[case]
    data = tmp_path / "data"
    page(data, f"{PL_SEASON}/round_1.json", [listed(sf.PL_ARS), listed(sf.PL_LIV)], _complete=False)
    detail(data, sf.PL_ARS)
    detail(data, sf.PL_LEE)  # hiçbir sayfada yok
    admin = make_admin(data)
    admin.rebuild()
    assert (event_row(admin.catalog, ARS)["stale"], event_row(admin.catalog, ARS)["listed_in"]) == (0, "round_1")

    changed = listed(sf.PL_ARS)
    edit(changed)
    page(data, f"{PL_SEASON}/round_1.json", [changed, listed(sf.PL_LIV, winnerCode=3), listed(sf.PL_LEE)],
         BASE + 60, _complete=True)
    report = admin.reconcile()

    assert summary(report) == {**UNCHANGED, "seasons": [(17, 96668)]}
    assert snapshot(admin.catalog) == rebuilt(data, tmp_path)
    row = event_row(admin.catalog, ARS)
    assert (row["stale"], row["listed_in"], row["row_source"]) == (int(differs), "round_1", "event")
    assert (event_row(admin.catalog, LIV)["winner_code"], event_row(admin.catalog, LIV)["updated_at"]) == (3, BASE + 60)
    assert (event_row(admin.catalog, LEE)["listed_in"], event_row(admin.catalog, LEE)["stale"]) == ("round_1", 0)
    assert schedule_slices(admin.catalog, 96668)["round_1"]["meta_json"] == '{"complete":true}'

    # olay yükü yeniden okunur (liste sonrasında): bayrak düşer
    base = f"{PL_DETAILS}/{ARS}"
    write(data, f"{base}/observation.json", sf.observation_payload(sf.basic_payload(sf.PL_ARS), utc(BASE + 600)))
    report = admin.reconcile()
    assert summary(report) == {**UNCHANGED, "events": (1, 0)}
    assert event_row(admin.catalog, ARS)["stale"] == 0 and snapshot(admin.catalog) == rebuilt(data, tmp_path)


def test_stale_of_a_v3_event_and_unreadable_payloads(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    at = dt.datetime.fromtimestamp(BASE, UTC)
    page(data, f"{PL_SEASON}/round_1.json", [listed(sf.PL_ARS), listed(sf.PL_LIV)], _complete=True)
    write_v3(data, sf.basic_payload(sf.PL_ARS), at=at)
    detail(data, sf.PL_LIV)
    admin = make_admin(data)
    admin.rebuild()

    page(data, f"{PL_SEASON}/round_1.json", [listed(sf.PL_ARS, winnerCode=2), listed(sf.PL_LIV, winnerCode=2)],
         BASE + 60, _complete=True)
    assert admin.reconcile().seasons == [(17, 96668)]
    assert [event_row(admin.catalog, i)["stale"] for i in (ARS, LIV)] == [1, 1]
    assert snapshot(admin.catalog) == rebuilt(data, tmp_path)

    # saklanan olay yükü okunamıyorsa karşılaştırılamaz: bayrak konmaz (maçın kendisini doğrulama bildirir)
    Path(layout.resolve(data, layout.slice_path(layout.event_dir(ARS), "event"))).write_bytes(b"broken")
    (data / PL_DETAILS / str(LIV) / "basic.json").write_bytes(b"{")
    bump(data / PL_SEASON)
    assert admin.reconcile().seasons == [(17, 96668)]
    assert [event_row(admin.catalog, i)["stale"] for i in (ARS, LIV)] == [0, 0]


def test_file_rewritten_in_place_is_seen_through_its_own_mtime(canonical: sf.LegacyFixture, make_admin,
                                                               tmp_path: Path) -> None:
    data = canonical.data_dir
    admin = make_admin(data, league_names=canonical.leagues)
    admin.rebuild()
    season = data / PL_SEASON
    target = season / "round_3.json"
    stamp = season.stat()

    def rewrite(content: Dict[str, Any], mtime_ns: int) -> None:
        target.write_bytes(sf.dump_json(content))  # yerinde yazma: dizinin girdileri değişmez
        os.utime(target, ns=(mtime_ns, mtime_ns))
        os.utime(season, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))  # dizinin zamanı da aynı kalsın

    # dosyanın zamanı dizininkinden yeni: imza değişir
    body = read_json(data, f"{PL_SEASON}/round_3.json")
    body["events"][0]["winnerCode"] = 1
    rewrite(body, stamp.st_mtime_ns + 10 ** 9)
    assert admin.reconcile().seasons == [(17, 96668)]
    assert event_row(admin.catalog, FUTURE)["winner_code"] == 1
    assert snapshot(admin.catalog) == rebuilt(data, tmp_path, league_names=canonical.leagues)

    # zamanı ve boyutu aynı kalan düzenleme imzayla görülemez (kaba imzanın sınırı); deep görür
    body["events"][0]["winnerCode"] = 2
    rewrite(body, stamp.st_mtime_ns + 10 ** 9)
    assert not admin.reconcile().changed and event_row(admin.catalog, FUTURE)["winner_code"] == 1
    deep = admin.reconcile(deep=True)
    assert event_row(admin.catalog, FUTURE)["winner_code"] == 2
    assert deep.changed and deep.v3 and deep.verify is not None and deep.verify.deep and deep.verify.ok
    assert deep.events_indexed == deep.events_checked == len(canonical.detail_ids)
    assert deep.seasons == [(8, 97532), (17, 76986), (17, 96668), (19, 97110), (132, 80229), (2361, 79116)]
    assert (deep.season_lists, deep.changes) == (5, 2)
    assert snapshot(admin.catalog) == rebuilt(data, tmp_path, league_names=canonical.leagues)


# --- özet CSV'leri ----------------------------------------------------------------------------------------

def test_summary_only_season_follows_its_league_directory(old_forms: sf.LegacyFixture, make_admin,
                                                          tmp_path: Path) -> None:
    data = old_forms.data_dir
    names = old_forms.leagues
    admin = make_admin(data, league_names=names)
    admin.rebuild()
    old_a, old_b = sf.event_id(sf.PL_OLD_A), sf.event_id(sf.PL_OLD_B)
    rel = "matches/17_Premier_League/76986_Premier_League_25_26_matches.csv"

    # özet yeniden yazılır (lig dizinindeki dosya): yalnızca o ligin sayfasız sezonları yeniden dizinlenir
    events = [sf.event_payload(sf.PL_OLD_A)]
    write(data, rel, sf.dump_csv(sf.SUMMARY_COLUMNS, [sf.summary_row(38, {**events[0], "homeScore": {"current": 9}})]),
          BASE + 40)
    report = admin.reconcile()
    assert summary(report) == {**UNCHANGED, "seasons": [(17, 76986)]}
    assert event_row(admin.catalog, old_a)["home_score_current"] == 9 and event_row(admin.catalog, old_b) is None
    assert facts(snapshot(admin.catalog)) == facts(rebuilt(data, tmp_path, league_names=names))

    # sezonun tur dosyası gelir: satırlar artık sayfadan
    season_dir = "matches/17_Premier_League/76986_Premier_League_25_26"
    page(data, f"{season_dir}/round_38.json", [listed(sf.PL_OLD_B)], BASE + 50, _complete=True)
    report = admin.reconcile()
    assert report.seasons == [(17, 76986)]
    assert event_row(admin.catalog, old_a) is None
    row = event_row(admin.catalog, old_b)
    assert (row["listed_in"], row["status_type"], row["home_id"] is not None) == ("round_38", "finished", True)
    assert facts(snapshot(admin.catalog)) == facts(rebuilt(data, tmp_path, league_names=names))

    # tur dosyası gider: yeniden özetten
    remove(data, season_dir)
    assert admin.reconcile().seasons == [(17, 76986)]
    assert event_row(admin.catalog, old_a)["status_type"] is None and event_row(admin.catalog, old_b) is None
    assert facts(snapshot(admin.catalog)) == facts(rebuilt(data, tmp_path, league_names=names))

    # özet de gider: sezonun katalogda payı kalmaz
    remove(data, rel)
    assert admin.reconcile().seasons == [(17, 76986)]
    assert event_row(admin.catalog, old_a) is None
    assert facts(snapshot(admin.catalog)) == facts(rebuilt(data, tmp_path, league_names=names))
    assert not admin.reconcile().changed


def test_summary_rows_take_the_sport_of_the_tournament_once_it_is_known(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    rel = "matches/17_Premier_League/76986_Premier_League_25_26_summary.csv"
    write(data, rel, sf.dump_csv(sf.SUMMARY_COLUMNS, [sf.summary_row(38, sf.event_payload(sf.PL_OLD_A))]))
    admin = make_admin(data)
    admin.rebuild()
    old_a = sf.event_id(sf.PL_OLD_A)
    row = event_row(admin.catalog, old_a)
    assert (row["sport"], row["scores_json"]) == ("", None)  # CSV sporu söylemez, turnuva da bilinmiyor

    # turnuvanın ilk olay yükü gelir: özetten gelen sezon, dosyaları değişmediği halde, yeniden dizinlenir
    base = detail(data, sf.PL_ARS)
    report = admin.reconcile()
    assert summary(report) == {**UNCHANGED, "events": (1, 0), "seasons": [(17, 76986)]}
    row = event_row(admin.catalog, old_a)
    assert row["sport"] == "football" and row["scores_json"] is not None
    assert snapshot(admin.catalog) == rebuilt(data, tmp_path)
    assert not admin.reconcile().changed

    # Bilinen sınır: varlık satırları silinmez. Turnuvanın tek kaynağı gidince uzlaştırma sporu bilmeye
    # devam eder; sıfırdan kurulum bilemez.
    remove(data, base)
    assert summary(admin.reconcile()) == {**UNCHANGED, "events": (0, 1)}
    assert event_row(admin.catalog, old_a)["sport"] == "football"
    fresh = {r["id"]: r["sport"] for r in rebuilt(data, tmp_path)["events"]}
    assert fresh == {old_a: ""}


def test_league_names_decide_which_season_list_is_indexed(old_forms: sf.LegacyFixture, tmp_path: Path) -> None:
    data = old_forms.data_dir
    names: Dict[int, str] = {}
    admin = CatalogAdmin(data, clock=lambda: float(NOW), league_names=lambda: names)
    try:
        admin.rebuild()
        slices = lambda: {r["entity_id"]: r["path"]  # noqa: E731
                          for r in rows(admin.catalog, "entity_slices", "WHERE kind = 'tournament'")}
        assert slices()[8] == "league_seasons.csv"

        # dosyalar değişmedi ama eşleme değişti: adında kimlik olmayan dosyanın turnuvası artık bulunuyor
        names[8] = "LaLiga"
        report = admin.reconcile()
        assert summary(report) == {**UNCHANGED, "season_lists": 3}
        assert "unresolved_tournament" not in {p.kind for p in report.problems}
        assert slices()[8] == "seasons/LaLiga_seasons.json"
        assert snapshot(admin.catalog) == rebuilt(data, tmp_path, league_names=names)
        assert not admin.reconcile().changed
    finally:
        admin.close()


# --- maç dizinleri ----------------------------------------------------------------------------------------

def test_pending_writes_are_reindexed_and_cleared(canonical: sf.LegacyFixture, make_admin, tmp_path: Path) -> None:
    data = canonical.data_dir
    admin = make_admin(data, league_names=canonical.leagues)
    admin.rebuild()
    cat = admin.catalog
    with cat.write():
        cat.upsert("pending_writes", [
            {"kind": "event", "entity_id": ARS, "started_at": NOW},
            {"kind": "event", "entity_id": 424242, "started_at": NOW},  # yazma hiç başlamamış: dizini yok
            {"kind": "season", "entity_id": 96668, "started_at": NOW}])
        # katalog, yarım kalmış yazmadan önceki hali söylüyor
        cat.connection().execute("UPDATE events SET winner_code = 2, sig = 'stale' WHERE id = ?", (ARS,))

    report = admin.reconcile()

    assert (report.pending, report.pending_skipped, report.changed) == (2, 1, True)
    assert rows(cat, "pending_writes") == [{"kind": "season", "entity_id": 96668, "started_at": NOW}]
    assert event_row(cat, ARS)["winner_code"] == 1 and event_row(cat, 424242) is None
    with cat.write() as conn:
        conn.execute("DELETE FROM pending_writes")
    assert snapshot(cat) == rebuilt(data, tmp_path, league_names=canonical.leagues)


def test_v3_manifests_are_compared_only_when_asked(canonical: sf.LegacyFixture, make_admin, tmp_path: Path) -> None:
    data = canonical.data_dir
    names = canonical.leagues
    at = dt.datetime.fromtimestamp(BASE + 500, UTC)
    write_v3(data, sf.basic_payload(sf.PL_ARS), at=at)  # eski dizini de duruyor: v3 geçerli
    admin = make_admin(data, league_names=names)
    admin.rebuild()
    cat = admin.catalog
    assert (event_row(cat, ARS)["layout"], event_row(cat, ARS)["legacy_path"]) == ("v3", f"{PL_DETAILS}/{ARS}")

    # kataloğun arkasından: bir v3 maçı değişir, yeni bir v3 maçı gelir
    changed = write_v3(data, {**sf.basic_payload(sf.PL_ARS), "winnerCode": 3}, at=at + dt.timedelta(minutes=5))
    bump(Path(layout.resolve(data, layout.manifest_path(changed))))  # manifestin boyutu aynı kalabilir
    write_v3(data, sf.basic_payload(sf.PL_NO_DETAIL), at=at)
    quick = admin.reconcile()
    assert not quick.changed and event_row(cat, ARS)["winner_code"] == 1  # v3 ağacına bakılmadı

    report = admin.reconcile(v3=True)
    assert report.v3 and summary(report) == {**UNCHANGED, "events": (2, 0)}
    assert report.events_checked == len(canonical.detail_ids) + 1
    assert event_row(cat, ARS)["winner_code"] == 3
    assert (event_row(cat, NO_DETAIL)["layout"], event_row(cat, NO_DETAIL)["listed_in"]) == ("v3", "round_2")
    assert snapshot(cat) == rebuilt(data, tmp_path, league_names=names)
    assert not admin.reconcile(v3=True).changed

    # eski kopya silinir (taşıma): v3 taranmadan da görülür, çünkü eski düzen dizinleri hep taranır
    remove(data, f"{PL_DETAILS}/{ARS}")
    assert summary(admin.reconcile()) == {**UNCHANGED, "events": (1, 0)}
    assert event_row(cat, ARS)["legacy_path"] is None

    # v3 dizini kaybolur: yalnızca v3 taramasıyla; maç listelendiği için liste satırına döner
    files.remove_tree(layout.resolve(data, layout.event_dir(ARS)))
    assert not admin.reconcile().changed
    report = admin.reconcile(v3=True)
    assert summary(report) == {**UNCHANGED, "events": (0, 1), "seasons": [(17, 96668)]}
    assert event_row(cat, ARS)["row_source"] == "listing"
    assert facts(snapshot(cat)) == facts(rebuilt(data, tmp_path, league_names=names))

    # manifesti okunamayan v3 dizini bildirilir; eski kopyası varsa o dizinlenir
    liv_rel = write_v3(data, sf.basic_payload(sf.PL_LIV), at=at)
    assert admin.reconcile(v3=True).events_indexed == 1 and event_row(cat, LIV)["layout"] == "v3"
    Path(layout.resolve(data, layout.manifest_path(liv_rel))).write_bytes(b"{")
    report = admin.reconcile(v3=True)
    assert [(p.layout, p.path, p.kind) for p in report.problems] == [("v3", liv_rel, "corrupt")]
    assert (event_row(cat, LIV)["layout"], event_row(cat, LIV)["path"]) == ("legacy", f"{PL_DETAILS}/{LIV}")
    assert manifest.MANIFEST_FORMAT == 1


def test_changed_and_duplicated_event_directories(old_forms: sf.LegacyFixture, make_admin, tmp_path: Path) -> None:
    data = old_forms.data_dir
    names = old_forms.leagues
    admin = make_admin(data, league_names=names)
    admin.rebuild()
    cat = admin.catalog
    flat = f"match_details/{ARS}"  # fixture: aynı maçın düz dizinde kalmış bayat kopyası
    assert event_row(cat, ARS)["path"] == f"{PL_DETAILS}/{ARS}"

    # bayat kopya yenilenir: geçerli dizin değişir
    write(data, f"{flat}/basic.json", {**sf.basic_payload(sf.PL_ARS), "winnerCode": 3}, BASE + 300)
    # basic.json'sız dizine olay yükü gelir; okunamayan dilim dosyası düzelir
    write(data, f"{PL_DETAILS}/17018572/basic.json", sf.basic_payload(sf.PL_NEW), BASE + 10)
    write(data, f"{PL_DETAILS}/17185003/statistics.json",
          sf.slice_payload("statistics", sf.basic_payload(sf.PL_AVL)), BASE + 10)
    report = admin.reconcile()

    assert summary(report) == {**UNCHANGED, "events": (3, 0)} and report.problems == []
    assert (event_row(cat, ARS)["path"], event_row(cat, ARS)["winner_code"]) == (flat, 3)
    assert (event_row(cat, 17018572)["row_source"], event_row(cat, 17018572)["listed_in"]) == ("event", "round_2")
    assert rows(cat, "event_slices", "WHERE event_id = 17185003 AND key = 'statistics'")[0]["state"] == "ok"
    assert snapshot(cat) == rebuilt(data, tmp_path, league_names=names)

    # geçerli kopya okunamaz hale gelir: sıradaki kopya dizinlenir; maç her uzlaştırmada yeniden okunur
    write(data, f"{flat}/basic.json", b"{", BASE + 400)
    report = admin.reconcile()
    assert summary(report) == {**UNCHANGED, "events": (1, 0)}
    assert [(p.path, p.kind) for p in report.problems] == [(flat, "corrupt")]
    assert event_row(cat, ARS)["path"] == f"{PL_DETAILS}/{ARS}"
    assert admin.reconcile().events_indexed == 1  # imzayla "değişmedi" denemez: en yeni kopya hâlâ okunamıyor
    assert facts(snapshot(cat)) == facts(rebuilt(data, tmp_path, league_names=names))


def test_listed_event_whose_payload_names_another_season_is_not_attached(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    page(data, f"{PL_SEASON}/round_1.json", [listed(sf.PL_ARS)], _complete=True)
    detail(data, sf.PL_ARS)
    detail(data, sf.PL_LIV, season={"id": 5, "name": "Other", "year": "20/21"})
    admin = make_admin(data)
    admin.rebuild()

    page(data, f"{PL_SEASON}/round_1.json", [listed(sf.PL_ARS), listed(sf.PL_LIV, winnerCode=3)], BASE + 60,
         _complete=True)
    report = admin.reconcile()

    assert [(p.path, p.kind) for p in report.problems] == [(f"{PL_SEASON}/round_1.json", "season_mismatch")]
    liv = event_row(admin.catalog, LIV)
    assert (liv["season_id"], liv["listed_in"], liv["stale"]) == (5, None, 0)
    assert event_row(admin.catalog, ARS)["listed_in"] == "round_1"
    assert facts(snapshot(admin.catalog)) == facts(rebuilt(data, tmp_path))


# --- rastgele değişiklik dizileri ---------------------------------------------------------------------------

_POOL = (sf.PL_ARS, sf.PL_LIV, sf.PL_LEE, sf.PL_BRE, sf.PL_AVL, sf.PL_NEW, sf.PL_NO_DETAIL, sf.PL_NOT_STARTED,
         sf.PL_FUTURE)
_OLD_POOL = (sf.PL_OLD_A, sf.PL_OLD_B)
_SEASON_DIRS = (
    (PL_SEASON, _POOL),
    ("matches/17_Premier_League/96668_Season_96668", _POOL),  # aynı sezonun ikinci dizini
    ("matches/17_Premier_League/76986_Premier_League_25_26", _OLD_POOL),
)
_PAGE_NAMES = ("round_1.json", "round_2.json", "round_3_final.json", "events_last_0.json", "events_next_0.json")


def _mutate(rng: random.Random, data: Path, clock: int) -> None:
    """Ağaçta rastgele bir değişiklik: 2.x yazıcılarının, terminal arayüzünün ya da kullanıcının yapabileceği türden."""
    op = rng.choice(["page", "page", "page", "rm_page", "rm_season", "detail", "detail", "flat", "rm_detail",
                     "observe", "seasons", "rm_seasons", "summary", "rm_summary", "changes"])
    details = sorted(p.parent for p in data.glob("match_details/**/basic.json"))
    if op == "page":
        directory, pool = rng.choice(_SEASON_DIRS)
        events = [listed(ev, winnerCode=rng.choice([1, 2, 3])) if rng.random() < 0.5 else listed(ev)
                  for ev in rng.sample(pool, rng.randint(0, min(5, len(pool))))]
        name = rng.choice(_PAGE_NAMES)
        extra = {"_complete": rng.choice([True, False])} if name.startswith("round") and rng.random() < 0.8 else {}
        page(data, f"{directory}/{name}", events, clock, **extra)
    elif op == "rm_page":
        found = sorted((data / rng.choice(_SEASON_DIRS)[0]).glob("*.json"))
        if found:
            remove(data, rng.choice(found).relative_to(data).as_posix())
    elif op == "rm_season":
        directory = rng.choice(_SEASON_DIRS)[0]
        if (data / directory).exists() and rng.random() < 0.4:
            remove(data, directory)
    elif op in ("detail", "flat"):
        ev = rng.choice(_POOL + _OLD_POOL)
        season = "season_Premier_League_26_27" if ev in _POOL else "season_Premier_League_25_26"
        base = (f"match_details/{sf.event_id(ev)}" if op == "flat"
                else f"match_details/17_Premier_League/{season}/{sf.event_id(ev)}")
        edits = {"winnerCode": rng.choice([1, 2, 3])} if rng.random() < 0.5 else {}
        detail(data, ev, base, slices=("statistics",) if rng.random() < 0.5 else (), mtime=clock, **edits)
    elif op == "rm_detail" and details:
        remove(data, rng.choice(details).relative_to(data).as_posix())
    elif op == "observe" and details:
        rel = rng.choice(details).relative_to(data).as_posix()
        write(data, f"{rel}/observation.json",
              {"observed_at_utc": utc(clock + rng.choice([-100, 0, 100])), "change_ts": 1}, clock)
    elif op == "seasons":
        chosen = rng.sample([sf.PL_2627, sf.PL_2526, sf.PL_2425], rng.randint(0, 3))
        name = rng.choice(["17_Premier_League_seasons.json", "17_seasons.json", "Premier League_seasons.json"])
        write(data, f"seasons/{name}", {"seasons": [{"id": s.id, "name": s.name, "year": s.year} for s in chosen]},
              clock)
    elif op == "rm_seasons":
        found = sorted(data.glob("seasons/*.json"))
        if found:
            remove(data, rng.choice(found).relative_to(data).as_posix())
    elif op == "summary":
        season, pool = rng.choice([(sf.PL_2627, _POOL), (sf.PL_2526, _OLD_POOL)])
        lines = [sf.summary_row(rng.choice([1, 2, "last_0"]), sf.event_payload(ev))
                 for ev in rng.sample(pool, rng.randint(1, 2))]
        name = f"{sf.season_dir(season)}{rng.choice(['_summary', '_matches'])}.csv"
        write(data, f"matches/17_Premier_League/{name}", sf.dump_csv(sf.SUMMARY_COLUMNS, lines), clock)
    elif op == "rm_summary":
        found = sorted(data.glob("matches/17_Premier_League/*.csv"))
        if found:
            remove(data, rng.choice(found).relative_to(data).as_posix())
    elif op == "changes":
        with open(data / "score_changes.jsonl", "ab") as f:
            f.write(sf.dump_jsonl([{**sf.SCORE_CHANGES[0], "event_id": rng.randint(1, 9)}]))


@pytest.mark.parametrize("seed", range(6))
def test_random_changes_reconcile_to_the_rows_of_a_rebuild(tmp_path: Path, make_admin, seed: int) -> None:
    """
    Her adımda ağaçta birkaç rastgele değişiklik yapılır (zamanları eşit olanlar dahil); uzlaştırmadan sonra
    maçlar, dilimler, yarışmacı bağları, program dilimleri, değişiklik günlüğü ve kökler sıfırdan kurulmuş
    katalogdakilerle satır satır aynıdır ve ikinci bir uzlaştırma yapacak iş bulamaz.
    """
    rng = random.Random(seed)
    data = tmp_path / "data"
    names = {17: "Premier League"}
    # Turnuvanın sporu hep bilinsin: hiç silinmeyen bir sezon sayfası (varlık satırları birikimlidir, bkz.
    # test_summary_rows_take_the_sport_of_the_tournament_once_it_is_known)
    keep = sf.Ev(sf.PL_ARS.case, sf.PL, sf.PL_2425, "Keep A", "Keep B", round=1, eid=11000001)
    page(data, "matches/17_Premier_League/61627_Premier_League_24_25/round_1.json", [listed(keep)], BASE - 500)
    admin = make_admin(data, league_names=names)
    admin.rebuild()
    clock = BASE
    for step in range(12):
        for _ in range(rng.randint(1, 3)):
            clock += rng.choice([0, 1, 30])
            _mutate(rng, data, clock)
        admin.reconcile()
        assert facts(snapshot(admin.catalog)) == facts(rebuilt(data, tmp_path, league_names=names)), step
        again = admin.reconcile()
        assert (again.seasons, again.season_lists, again.changes, again.events_removed) == ([], None, None, 0), step
        assert admin.verify().ok, step


# --- hata ve kilit ----------------------------------------------------------------------------------------

def test_reconcile_needs_a_usable_catalog(canonical: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(canonical.data_dir)
    with pytest.raises(StoreError, match="önce yeniden kurulmalı"):
        admin.reconcile()  # katalog dosyası yok
    assert not os.path.exists(admin.catalog.path)
    admin.catalog.prepare()  # şema var, satırlar kurulmadı
    with pytest.raises(StoreError, match="derive_version"):
        admin.reconcile()
    assert admin.ensure() is not None and not admin.reconcile().changed


def test_reconcile_is_one_transaction(canonical: sf.LegacyFixture, make_admin, monkeypatch: pytest.MonkeyPatch) -> None:
    data = canonical.data_dir
    admin = make_admin(data, league_names=canonical.leagues)
    admin.rebuild()
    before = snapshot(admin.catalog)
    remove(data, f"{PL_DETAILS}/{ARS}")
    page(data, f"{PL_SEASON}/round_4.json", [listed(sf.PL_FUTURE)], BASE + 9, _complete=False)

    with Catalog(admin.catalog.path) as writer, Catalog(admin.catalog.path, busy_timeout_ms=50) as impatient:
        with writer.write():  # başka bir süreç yazıyor
            with CatalogAdmin(data, impatient) as blocked:
                with pytest.raises(StoreBusy):
                    blocked.reconcile()
    assert snapshot(admin.catalog) == before

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("disk gitti")

    monkeypatch.setattr(entities, "apply_season", broken)  # maçlar yeniden dizinlendikten sonra hata
    with pytest.raises(RuntimeError):
        admin.reconcile()
    assert snapshot(admin.catalog) == before  # yarım uzlaştırma geri alınır
    monkeypatch.undo()
    assert admin.reconcile().changed and snapshot(admin.catalog) != before


def test_reconcile_on_the_shared_catalog_of_the_store_facade(canonical: sf.LegacyFixture) -> None:
    """Cephenin açtığı katalog (state.db ATTACH edilmiş) üzerinde: kurulum `maintenance` kilidiyle, sonra uzlaştırma."""
    data = canonical.data_dir
    store = open_store(data)
    try:
        assert store.info(sizes=False).catalog_rebuild_reason == "derive_version"  # şema var, satırlar yok
        admin = CatalogAdmin(data, store._catalog, clock=lambda: float(NOW), league_names=canonical.leagues)
        with store.lease("maintenance", purpose="op:rebuild"):
            built = admin.ensure()
        assert built is not None and built.mode == "in_place" and admin.ensure() is None
        info = store.info(sizes=False)
        assert (info.catalog_rebuild_reason, info.last_rebuild) == (None, str(NOW))
        assert info.events_by_layout == {"legacy": len(canonical.detail_ids), "listing": 12}
        assert (info.rows["catalog"]["entity_slices"], info.rows["catalog"]["changes"],
                info.rows["catalog"]["legacy_roots"]) == (16, 2, 17)

        assert not admin.reconcile().changed
        page(data, f"{PL_SEASON}/round_4.json", [listed(sf.PL_FUTURE), listed(sf.PL_NO_DETAIL)], BASE + 9,
             _complete=False)
        remove(data, "matches/132_NBA/80229_NBA_26_27/events_last_0.json")
        report = admin.reconcile()
        assert report.seasons == [(17, 96668), (132, 80229)]
        admin.close()  # paylaşılan kataloğu kapatmaz
        assert store.info(sizes=False).events_by_layout == {"legacy": len(canonical.detail_ids), "listing": 11}
        # state.db'nin istatistikleri değişmedi (akış okuma planı, bkz. tests/test_store_query_plans.py)
        assert store._catalog.connection().execute(
            "SELECT count(*) FROM state.sqlite_master WHERE name = 'sqlite_stat1'").fetchone()[0] == 0
    finally:
        store.close()


def test_reconcile_logs_in_english_only_when_something_changed(canonical: sf.LegacyFixture, make_admin,
                                                               caplog: pytest.LogCaptureFixture) -> None:
    admin = make_admin(canonical.data_dir, league_names=canonical.leagues)
    admin.rebuild()
    caplog.clear()
    with caplog.at_level("INFO", logger=indexer.logger.name):
        admin.reconcile()
        assert [r for r in caplog.records if r.name == indexer.logger.name] == []
        page(canonical.data_dir, f"{PL_SEASON}/round_4.json", [listed(sf.PL_FUTURE)], BASE + 9, _complete=False)
        admin.reconcile()
    messages = [r.getMessage() for r in caplog.records if r.name == indexer.logger.name]
    assert len(messages) == 1 and messages[0].startswith("Catalog reconciled: 0 events re-indexed, 0 removed")
    assert "1 season listings" in messages[0]
