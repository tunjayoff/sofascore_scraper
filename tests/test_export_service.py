"""
Dışa aktarma servisi (plan maddesi EX-1): `legacy-wide-csv` profili depodan okur.

Denetlenenler:

  1. Satır kuralı (`legacy_wide_row`): eski `process_match_for_csv`'nin kuralı; diziliş sütunları SofaScore'un
     gönderdiği metinden dolar (FX-7).
  2. Hangi maçlar: detayı (olay yükü) saklanan her maç bir kez, başlangıç zamanı sırasıyla; iki düzen de.
  3. `league_folder` / `season_folder`: eski düzende dizin adları, düz kayıtta yok, v3'te eski yazıcının adları.
  4. Akışa ve dosyaya yazma, lig süzgeci (birleşik satırlar, pandas yok), boş seçim, desteklenmeyen biçim.
  5. Yüzler: MatchDataFetcher'ın eski girişleri servisi çağırır (2.x'in `/api/export/csv` yolu 3.1'de kalktı, P30).
  6. Web işinin sonunda CSV aşaması yoktur (karar D9).
"""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

import store_fixtures as sf
from sofascore_scraper.errors import NotSupportedError
from sofascore_scraper.services import export as export_module
from sofascore_scraper.services import sync as sync_module
from sofascore_scraper.services.export import (
    PRIORITY_COLUMNS,
    ExportService,
    ExportSpec,
    LegacyTable,
    legacy_columns,
    legacy_folders,
    legacy_wide_row,
)
from sofascore_scraper.slices import SLICE_OK, Outcome
from sofascore_scraper.store import open_store
from sofascore_scraper.web.app import app

client = TestClient(app)

ARS = sf.event_id(sf.PL_ARS)  # legacy: lig dizininde ve bayat düz kopyada
LEE = sf.event_id(sf.PL_LEE)  # legacy: yalnızca düz dizin
BRE = sf.event_id(sf.PL_BRE)  # legacy: yalnızca birleşik dosya
NEW = sf.event_id(sf.PL_NEW)  # legacy: basic.json'sız dizin (olay yükü yok)
V3_EVENT = 990001


def _fixture(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sf.LegacyFixture:
    fixture = sf.build_fixture(name, tmp_path / "data")
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(fixture.data_dir))
    return fixture


def _table(text: str) -> List[List[str]]:
    return list(csv.reader(io.StringIO(text, newline="")))


def _tree(root: Path) -> Dict[str, int]:
    """Veri dizinindeki dosyalar (`.meta/` dışında): yol → boyut."""
    out: Dict[str, int] = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and not rel.startswith(".meta/"):
            out[rel] = path.stat().st_size
    return out


# --- 1. satır kuralı ---------------------------------------------------------------------------------

def _match_data() -> Dict[str, Any]:
    return {
        "basic": {
            "tournament": {"uniqueTournament": {"id": 17, "name": "Premier League"}},
            "season": {"id": 76986, "name": "Premier League 25/26", "year": "25/26"},
            "roundInfo": {"round": 38},
            "homeTeam": {"id": 1, "name": "Home"}, "awayTeam": {"id": 2, "name": "Away"},
            "homeScore": {"period1": 1, "normaltime": 2}, "awayScore": {"period1": 0, "normaltime": 1},
            "startTimestamp": 1779595200, "venue": {"name": "Ground"}, "referee": {"name": "Ref"},
            "status": {"description": "Ended"},
        },
        "statistics": {"statistics": [
            {"period": "1ST", "groups": [{"statisticsItems": [{"key": "ballPossession", "homeValue": 1, "awayValue": 2}]}]},
            {"period": "ALL", "groups": [{"statisticsItems": [{"key": "ballPossession", "homeValue": 55, "awayValue": 45},
                                                              {"homeValue": 9}]}]},
        ]},
        "team_streaks": {"general": [{"team": "home", "name": "Wins", "value": "3"},
                                     {"team": "both", "name": "Goals", "value": "2"}]},
        "pregame_form": {"homeTeam": {"position": 3, "value": "9", "avgRating": "6.8", "form": ["W", "D"]},
                         "awayTeam": {"position": 11}},
        "h2h": {"teamDuel": {"homeWins": 4, "awayWins": 1, "draws": 2}},
        "lineups": {"confirmed": True,
                    "home": {"players": [{"substitute": False}, {"substitute": True}], "formation": "4-3-3"},
                    "away": {"players": [{"substitute": False}], "formation": {"name": "4-4-2"}}},
    }


def test_the_row_rule_is_the_one_of_the_old_export() -> None:
    row = legacy_wide_row("14025001", _match_data(), "17_Premier_League", "Premier_League_25_26")

    assert row["match_id"] == "14025001" and row["league_folder"] == "17_Premier_League"
    assert (row["tournament_id"], row["round"], row["home_score_ft"], row["match_date"]) == (17, 38, 2, 1779595200)
    assert (row["home_ballPossession"], row["away_ballPossession"]) == (55, 45)  # yalnızca "ALL" dönemi
    assert row["home_streak_wins"] == "3" and row["home_streak_wins_continued"] is False
    assert not any(key.startswith("both_") for key in row)
    assert (row["home_form"], row["away_form"], row["away_points"]) == ("W_D", None, None)
    assert (row["h2h_home_wins"], row["h2h_away_wins"], row["h2h_draws"]) == (4, 1, 2)
    assert (row["lineups_confirmed"], row["home_starting_xi_count"], row["home_substitutes_count"]) == (True, 1, 1)
    # SofaScore dizilişi metin olarak gönderir; eski kodun beklediği nesne biçimi de okunur (FX-7)
    assert row["home_formation"] == "4-3-3" and row["away_formation"] == "4-4-2"


@pytest.mark.parametrize("formation, expected", [
    ("4-2-3-1", "4-2-3-1"),  # SofaScore'un gönderdiği biçim
    ({"name": "3-5-2"}, "3-5-2"),  # eski kodun beklediği biçim
    ("", None), (None, None), (442, None), ({}, None),
])
def test_the_formation_columns_hold_the_formation_text(formation: Any, expected: Any) -> None:
    lineup = {"players": [], "formation": formation}
    row = legacy_wide_row("1", {"lineups": {"home": lineup, "away": {"players": []}}})

    assert row["home_formation"] == expected and row["away_formation"] is None


def test_a_row_without_folders_has_no_folder_keys() -> None:
    row = legacy_wide_row("1", {"basic": {}})

    assert "league_folder" not in row and "season_folder" not in row
    assert row["tournament_id"] is None and list(row)[:2] == ["match_id", "tournament_id"]


def test_columns_put_the_priority_columns_first_and_sort_the_rest() -> None:
    rows = [{"venue": 1, "match_id": 1, "away_x": 1}, {"match_date": 2, "home_y": 2, "league_folder": 2}]

    assert legacy_columns(rows) == ("match_id", "league_folder", "match_date", "away_x", "home_y", "venue")
    assert legacy_columns([]) == ()
    assert PRIORITY_COLUMNS[0] == "match_id"


# --- 2. hangi maçlar ----------------------------------------------------------------------------------

def test_every_event_with_a_stored_payload_is_one_row_in_start_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture("legacy", tmp_path, monkeypatch)
    store = open_store(fixture.data_dir)

    table = ExportService(store).legacy_table()

    ids = [int(row["match_id"]) for row in table.rows]
    with_payload = sorted(e for e in fixture.detail_ids if (row := store.events.get(e)) and row.has_event_payload)
    assert sorted(ids) == with_payload and len(ids) == len(set(ids))  # düz ve lig kopyası olan maç bir kez
    assert ARS in ids and LEE in ids and BRE in ids and NEW not in ids  # birleşik dosyalı dizin de bir maçtır
    starts = [store.events.get(e).start_ts or 0 for e in ids]  # type: ignore[union-attr]
    assert starts == sorted(starts)


def test_the_selection_filters_by_tournament_and_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _fixture("canonical", tmp_path, monkeypatch)
    service = ExportService(open_store(fixture.data_dir))

    pl = service.legacy_table(ExportSpec(tournament_ids=(17,)))
    one = service.legacy_table(ExportSpec(event_ids=(ARS,)))

    assert pl.rows and {row["tournament_id"] for row in pl.rows} == {17}
    assert [row["match_id"] for row in one.rows] == [str(ARS)]
    assert service.legacy_table(ExportSpec(event_ids=(1,))).rows == ()


def test_an_unreadable_slice_drops_only_that_slice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _fixture("canonical", tmp_path, monkeypatch)
    store = open_store(fixture.data_dir)
    path = store.events.get(ARS).legacy_path or store.events.get(ARS).path  # type: ignore[union-attr]
    (fixture.data_dir / str(path) / "statistics.json").write_bytes(b"{")

    row = next(r for r in ExportService(store).legacy_table().rows if r["match_id"] == str(ARS))

    assert row["home_team_name"] == "Arsenal" and "home_ballPossession" not in row


# --- 3. lig ve sezon dizinleri ------------------------------------------------------------------------

@pytest.mark.parametrize("path, expected", [
    ("match_details/17_Premier_League/season_Premier_League_26_27/16837335", ("17_Premier_League", "Premier_League_26_27")),
    ("match_details/_no_tournament/football/15500001", ("_no_tournament", "football")),
    ("match_details/16837335", (None, None)),
])
def test_legacy_folders_are_the_directory_names(path: str, expected: Any) -> None:
    assert legacy_folders(SimpleNamespace(layout="legacy", path=path), {}) == expected  # type: ignore[arg-type]


def test_a_v3_event_gets_the_names_of_the_legacy_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(tmp_path))
    store = open_store(tmp_path)
    basic = sf.basic_payload(sf.PL_ARS)
    basic["id"] = V3_EVENT
    store.events.put(V3_EVENT, {"event": Outcome(SLICE_OK, basic)})
    friendly = {"id": V3_EVENT + 1, "season": {"name": "Friendly Games 2026"}, "startTimestamp": 1,
                "tournament": {"category": {"sport": {"slug": "football"}}}, "status": {"type": "finished"}}
    store.events.put(V3_EVENT + 1, {"event": Outcome(SLICE_OK, friendly)})

    rows = {row["match_id"]: row for row in ExportService(store).legacy_table().rows}

    assert store.events.get(V3_EVENT).layout == "v3"  # type: ignore[union-attr]
    assert (rows[str(V3_EVENT)]["league_folder"], rows[str(V3_EVENT)]["season_folder"]) == (
        "17_Premier_League", "Premier_League_26_27")
    assert (rows[str(V3_EVENT + 1)]["league_folder"], rows[str(V3_EVENT + 1)]["season_folder"]) == (
        "_no_tournament", "football")


# --- 4. yazma -----------------------------------------------------------------------------------------

def test_export_writes_to_a_stream_and_to_a_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _fixture("canonical", tmp_path, monkeypatch)
    service = ExportService(open_store(fixture.data_dir))
    out = io.StringIO()

    streamed = service.export(ExportSpec(), out)
    written = service.export(ExportSpec(), tmp_path / "out.csv")

    text = out.getvalue()
    assert streamed.path is None and streamed.bytes == len(text.encode("utf-8"))
    assert (tmp_path / "out.csv").read_bytes() == text.encode("utf-8") and written.path == str(tmp_path / "out.csv")
    table = _table(text)
    assert tuple(table[0]) == streamed.columns and len(table) - 1 == streamed.rows > 0
    assert text.endswith("\r\n")


def test_the_league_download_holds_the_rows_of_the_whole_export(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Birleşik tablonun sütunları kalır; satırlar birleşik dışa aktarmadakilerle bayt bayt aynıdır. Eskiden tablo
    pandas'tan geçiyor, boşluklu tamsayı sütunu `1.0` oluyordu (FX-7).
    """
    fixture = _fixture("canonical", tmp_path, monkeypatch)
    service = ExportService(open_store(fixture.data_dir))

    whole = service.prepare()
    league = service.prepare(ExportSpec(league_id=17))
    unknown = service.prepare(ExportSpec(league_id=999))

    text = "".join(league.chunks())
    table = _table(text)
    assert tuple(table[0]) == whole.columns == league.columns
    assert {row[1].split("_")[0] for row in table[1:]} == {"17"} and league.rows == len(table) - 1 > 0
    assert league.available == whole.rows == unknown.available and unknown.rows == 0
    assert table[1:] == [row for row in _table("".join(whole.chunks()))[1:] if row[1].startswith("17_")]
    gaps = table[0].index("home_starting_xi_count")
    assert "" in [row[gaps] for row in _table("".join(whole.chunks()))[1:]]  # sütunda boşluk var
    assert not any(cell.endswith(".0") for row in table[1:] for cell in row)
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")


def test_the_league_filter_keeps_every_row_when_no_row_has_a_league_folder() -> None:
    """pandas'lı kodun davranışı korunur: `league_folder` sütunu hiç yoksa (yalnızca düz kayıtlar) süzgeç yoktur."""
    flat = LegacyTable(("match_id", "round"), ({"match_id": "1", "round": 3}, {"match_id": "2", "round": None}))
    mixed = LegacyTable(("match_id", "league_folder"), ({"match_id": "1", "league_folder": "17_Premier_League"},
                                                        {"match_id": "2"},
                                                        {"match_id": "3", "league_folder": "170_Other"}))

    assert export_module._league_rows(flat, 17) == flat.rows
    assert [row["match_id"] for row in export_module._league_rows(mixed, 17)] == ["1"]


def test_an_empty_data_dir_is_an_empty_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(tmp_path))
    service = ExportService(open_store(tmp_path))

    assert service.prepare().rows == 0 and service.prepare(ExportSpec(league_id=17)).available == 0
    assert service.write_legacy_csv(str(tmp_path)) is None and service.write_legacy_csv_by_league(str(tmp_path)) == []
    assert not list(tmp_path.glob("*.csv"))


@pytest.mark.parametrize("spec", [ExportSpec(format="parquet"), ExportSpec(dataset="odds"), ExportSpec(profile=None)])
def test_other_datasets_and_formats_are_not_supported(tmp_path: Path, spec: ExportSpec) -> None:
    with pytest.raises(NotSupportedError):
        ExportService(open_store(tmp_path)).prepare(spec)


def test_files_by_league_hold_their_own_columns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _fixture("canonical", tmp_path, monkeypatch)
    out = tmp_path / "out"
    out.mkdir()

    results = ExportService(open_store(fixture.data_dir)).write_legacy_csv_by_league(str(out), now=1790000000)

    names = sorted(os.path.basename(r.path or "") for r in results)
    assert "17_Premier_League_1790000000.csv" in names and "132_NBA_1790000000.csv" in names
    nba = _table((out / "132_NBA_1790000000.csv").read_text(encoding="utf-8"))
    assert "home_rebounds" in nba[0] and "home_aces" not in nba[0]


# --- 5. yüzler ------------------------------------------------------------------------------------------

def test_the_legacy_csv_files_of_the_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Birleşik dosya, seçilen maçlar ve lig başına dosyalar (eski `create_csv_dataset` yönlendirmesinin işi)."""
    fixture = _fixture("canonical", tmp_path, monkeypatch)
    service = ExportService(open_store(fixture.data_dir))
    out = tmp_path / "out"
    out.mkdir()

    combined = service.write_legacy_csv(str(out))
    one = service.write_legacy_csv(str(out), ExportSpec(event_ids=(ARS,)))
    by_league = service.write_legacy_csv_by_league(str(out))

    assert combined is not None and combined.path and os.path.dirname(combined.path) == str(out)
    assert os.path.basename(combined.path).startswith("all_matches_") and os.path.isfile(combined.path)
    assert one is not None and [r[0] for r in _table(Path(one.path).read_text(encoding="utf-8"))[1:]] == [str(ARS)]
    assert len([r for r in by_league if r.path]) > 1
    assert service.write_legacy_csv(str(out), ExportSpec(event_ids=(1,))) is None
    assert service.write_legacy_csv_by_league(str(out), ExportSpec(event_ids=(1,))) == []


# --- 6. web işinde CSV aşaması yok ------------------------------------------------------------------

def test_a_sync_job_has_no_export_phase() -> None:
    assert not hasattr(sync_module, "export_all_csv") and not hasattr(export_module, "export_all_csv")
    for spec in (sync_module.SyncSpec(), sync_module.SyncSpec(mode="details"),
                 sync_module.SyncSpec(), sync_module.SyncSpec(mode="refresh")):
        assert "export" not in spec.job_phases
