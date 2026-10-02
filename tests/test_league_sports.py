"""League sport storage/inference and the multi-league match filter (temp dirs only, no network)."""
from __future__ import annotations

import json
import os
import tempfile

import pandas as pd

from src.web import league_sports
from src.web.routes.api import _get_matches_sync, _parse_league_ids


def _cfg(root: str) -> str:
    os.makedirs(os.path.join(root, "config"), exist_ok=True)
    return os.path.join(root, "config", "leagues.txt")


def test_normalize_sport():
    assert league_sports.normalize_sport("Football") == "football"
    assert league_sports.normalize_sport("basketball") == "basketball"
    assert league_sports.normalize_sport("Tennis") == "tennis"
    assert league_sports.normalize_sport("ice-hockey") == "ice-hockey"  # SP-1
    assert league_sports.normalize_sport("volleyball") is None
    assert league_sports.normalize_sport(None) is None


def test_set_load_and_forget():
    root = tempfile.mkdtemp()
    cfg = _cfg(root)
    league_sports.set_sport(cfg, 132, "basketball")
    league_sports.set_sport(cfg, 17, "football")
    assert league_sports.load(cfg) == {132: "basketball", 17: "football"}
    league_sports.set_sport(cfg, 17, None)
    assert league_sports.load(cfg) == {132: "basketball"}


def test_infer_from_downloaded_match_and_remember():
    root = tempfile.mkdtemp()
    cfg = _cfg(root)
    data = os.path.join(root, "data")
    match_dir = os.path.join(data, "match_details", "132_NBA", "season_NBA_25_26", "14441992")
    os.makedirs(match_dir)
    with open(os.path.join(match_dir, "basic.json"), "w", encoding="utf-8") as f:
        json.dump({"id": 14441992, "tournament": {"uniqueTournament": {"id": 132},
                                                  "category": {"sport": {"name": "Basketball", "slug": "basketball"}}}}, f)

    out = league_sports.resolve_all(cfg, data, [132, 17])
    assert out == {132: "basketball", 17: None}
    # learned sports are stored, so the next read needs no file scan
    assert league_sports.load(cfg) == {132: "basketball"}


def test_stored_sport_wins_over_inference():
    root = tempfile.mkdtemp()
    cfg = _cfg(root)
    league_sports.set_sport(cfg, 132, "football")  # user's explicit choice
    data = os.path.join(root, "data")
    match_dir = os.path.join(data, "match_details", "132_X", "s", "1")
    os.makedirs(match_dir)
    with open(os.path.join(match_dir, "basic.json"), "w", encoding="utf-8") as f:
        json.dump({"tournament": {"category": {"sport": {"slug": "basketball"}}}}, f)
    assert league_sports.resolve_all(cfg, data, [132]) == {132: "football"}


def test_parse_league_ids():
    assert _parse_league_ids(None) is None
    assert _parse_league_ids("") is None
    assert _parse_league_ids("17") == {17}
    assert _parse_league_ids("17, 8,132") == {17, 8, 132}
    assert _parse_league_ids("x") is None


def _summary(root: str, folder: str, fname: str, ids, dates=None) -> None:
    """A season summary as the match fetcher writes it (ten columns; `status` says the match is finished)."""
    d = os.path.join(root, "matches", folder)
    os.makedirs(d, exist_ok=True)
    pd.DataFrame(
        {"round": [1] * len(ids), "match_id": ids, "home_team": ["H"] * len(ids), "away_team": ["A"] * len(ids),
         "match_date": dates or ["2025-08-22T20:00:00"] * len(ids), "status": ["Ended"] * len(ids)}
    ).to_csv(os.path.join(d, fname), index=False)


def _ids(root: str, league_id, season_id=None, details=None):
    return sorted(i["match_id"] for i in _get_matches_sync(root, 50, 0, "asc", league_id, None, season_id, details).items)


def test_filter_matches_by_several_leagues():
    root = tempfile.mkdtemp()
    _summary(root, "17_Premier_League", "61627_PL_24_25_summary.csv", [1])
    _summary(root, "8_LaLiga", "77559_LaLiga_25_26_summary.csv", [2])
    _summary(root, "132_NBA", "65360_NBA_24_25_summary.csv", [3])
    _summary(root, "242_MLS", "70158_MLS_2025_summary.csv", [4])
    assert _ids(root, "17") == [1]
    assert _ids(root, "17,132") == [1, 3]
    assert _ids(root, None) == [1, 2, 3, 4]
    # "1" must not match "17_..." or "132_..."
    assert _ids(root, "1") == []


def test_match_list_uses_summaries_even_when_export_csv_is_stale():
    """A stopped job never rewrites the export CSV; its league's matches must still be listed."""
    root = tempfile.mkdtemp()
    # export CSV knows only one Premier League match (details)
    processed = os.path.join(root, "match_details", "processed")
    os.makedirs(processed)
    pd.DataFrame({"match_id": [1], "league_folder": ["17_Premier_League"], "home_team_name": ["A"]}).to_csv(
        os.path.join(processed, "all_matches_1.csv"), index=False
    )
    # summaries: PL has 2 matches, LaLiga (stopped job) has 2
    _summary(root, "17_Premier_League", "61627_PL_24_25_summary.csv", [1, 2])
    _summary(root, "8_LaLiga", "77559_LaLiga_25_26_summary.csv", [3, 4])

    assert _ids(root, "8") == [3, 4]
    assert _ids(root, None) == [1, 2, 3, 4]
    assert _ids(root, "17", 61627) == [1, 2]
    assert _ids(root, "17", 99) == []


def test_match_list_does_not_fall_back_to_the_export_csv():
    """The list comes from the data folder's index, which does not index exports (design decision S14)."""
    root = tempfile.mkdtemp()
    processed = os.path.join(root, "match_details", "processed")
    os.makedirs(processed)
    pd.DataFrame({"match_id": [1, 2], "league_folder": ["17_Premier_League", "8_LaLiga"]}).to_csv(
        os.path.join(processed, "all_matches_1.csv"), index=False
    )
    assert _ids(root, "8") == []
    assert _ids(root, None) == []


def test_match_rows_say_whether_details_exist():
    root = tempfile.mkdtemp()
    _summary(root, "8_LaLiga", "77559_LaLiga_25_26_summary.csv", [11, 12, 13],
             ["2025-08-22T20:00:00", "2025-08-23T20:00:00", "2025-08-30T20:00:00"])
    detail = os.path.join(root, "match_details", "8_LaLiga", "season_LaLiga_25_26", "12")
    os.makedirs(detail)
    with open(os.path.join(detail, "basic.json"), "w", encoding="utf-8") as f:
        json.dump({"id": 12, "tournament": {"uniqueTournament": {"id": 8}}, "season": {"id": 77559},
                   "status": {"type": "finished", "description": "Ended"}}, f)

    r = _get_matches_sync(root, 50, 0, "asc", "8", None, None)
    assert {i["match_id"]: i["has_details"] for i in r.items} == {11: False, 12: True, 13: False}
    assert _ids(root, "8", None, "present") == [12]
    missing = _get_matches_sync(root, 50, 0, "asc", "8", None, None, "missing")
    assert missing.total == 2 and [i["match_id"] for i in missing.items] == [11, 13]
