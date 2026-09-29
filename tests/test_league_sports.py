"""League sport storage/inference and the multi-league match filter (temp dirs only, no network)."""
from __future__ import annotations

import json
import os
import tempfile

import pandas as pd

from src.web import league_sports
from src.web.routes.api import _filter_matches_df_by_league, _parse_league_ids


def _cfg(root: str) -> str:
    os.makedirs(os.path.join(root, "config"), exist_ok=True)
    return os.path.join(root, "config", "leagues.txt")


def test_normalize_sport():
    assert league_sports.normalize_sport("Football") == "football"
    assert league_sports.normalize_sport("basketball") == "basketball"
    assert league_sports.normalize_sport("Tennis") == "tennis"
    assert league_sports.normalize_sport("ice-hockey") is None
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
        json.dump({"tournament": {"category": {"sport": {"name": "Basketball", "slug": "basketball"}}}}, f)

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


def test_filter_matches_by_several_leagues():
    df = pd.DataFrame(
        {
            "league_folder": ["17_Premier_League", "8_LaLiga", "132_NBA", "242_MLS"],
            "match_id": [1, 2, 3, 4],
        }
    )
    assert list(_filter_matches_df_by_league(df, "17")["match_id"]) == [1]
    assert list(_filter_matches_df_by_league(df, "17,132")["match_id"]) == [1, 3]
    assert list(_filter_matches_df_by_league(df, None)["match_id"]) == [1, 2, 3, 4]
    # "1" must not match "17_..." or "132_..."
    assert list(_filter_matches_df_by_league(df, "1")["match_id"]) == []


def test_match_list_uses_summaries_even_when_export_csv_is_stale():
    """A stopped job never rewrites the export CSV; its league's matches must still be listed."""
    from src.web.routes.api import _build_schedule_matches_dataframe

    root = tempfile.mkdtemp()
    # export CSV knows only one Premier League match (details)
    processed = os.path.join(root, "match_details", "processed")
    os.makedirs(processed)
    pd.DataFrame({"match_id": [1], "league_folder": ["17_Premier_League"], "home_team_name": ["A"]}).to_csv(
        os.path.join(processed, "all_matches_1.csv"), index=False
    )
    # summaries: PL has 2 matches, LaLiga (stopped job) has 2
    for folder, fname, ids in (
        ("17_Premier_League", "61627_PL_24_25_summary.csv", [1, 2]),
        ("8_LaLiga", "77559_LaLiga_25_26_summary.csv", [3, 4]),
    ):
        d = os.path.join(root, "matches", folder)
        os.makedirs(d)
        pd.DataFrame(
            {"round": [1, 1], "match_id": ids, "home_team": ["H", "H"], "away_team": ["A", "A"], "match_date": ["2025-08-22T20:00:00"] * 2}
        ).to_csv(os.path.join(d, fname), index=False)

    assert sorted(_build_schedule_matches_dataframe(root, "8", None, None)["match_id"]) == [3, 4]
    assert len(_build_schedule_matches_dataframe(root, None, None, None)) == 4
    assert len(_build_schedule_matches_dataframe(root, "17", None, 61627)) == 2
    assert len(_build_schedule_matches_dataframe(root, "17", None, 99)) == 0


def test_match_list_falls_back_to_export_csv_without_summaries():
    from src.web.routes.api import _build_schedule_matches_dataframe

    root = tempfile.mkdtemp()
    processed = os.path.join(root, "match_details", "processed")
    os.makedirs(processed)
    pd.DataFrame({"match_id": [1, 2], "league_folder": ["17_Premier_League", "8_LaLiga"]}).to_csv(
        os.path.join(processed, "all_matches_1.csv"), index=False
    )
    assert list(_build_schedule_matches_dataframe(root, "8", None, None)["match_id"]) == [2]


def test_match_rows_say_whether_details_exist():
    from src.web.routes.api import _get_matches_sync

    root = tempfile.mkdtemp()
    d = os.path.join(root, "matches", "8_LaLiga")
    os.makedirs(d)
    pd.DataFrame(
        {"round": [1, 1, 2], "match_id": [11, 12, 13], "home_team": ["H"] * 3, "away_team": ["A"] * 3,
         "match_date": ["2025-08-22T20:00:00", "2025-08-23T20:00:00", "2025-08-30T20:00:00"]}
    ).to_csv(os.path.join(d, "77559_LaLiga_25_26_summary.csv"), index=False)
    detail = os.path.join(root, "match_details", "8_LaLiga", "season_LaLiga_25_26", "12")
    os.makedirs(detail)
    open(os.path.join(detail, "basic.json"), "w").write("{}")

    r = _get_matches_sync(root, 50, 0, "asc", "8", None, None)
    assert {i["match_id"]: i["has_details"] for i in r.items} == {11: False, 12: True, 13: False}
    assert [i["match_id"] for i in _get_matches_sync(root, 50, 0, "asc", "8", None, None, "present").items] == [12]
    missing = _get_matches_sync(root, 50, 0, "asc", "8", None, None, "missing")
    assert missing.total == 2 and [i["match_id"] for i in missing.items] == [11, 13]
