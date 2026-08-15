"""Stale SofaScore season ids must resolve onto the refreshed list."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.season_fetcher import SeasonFetcher


def _fetcher_with_seasons(seasons):
    config = MagicMock()
    sf = SeasonFetcher(config, data_dir="data")
    sf.league_seasons = {238: seasons}
    sf.get_seasons_for_league = lambda lid: sf.league_seasons.get(lid, [])  # type: ignore
    return sf


def test_resolve_keeps_valid_id():
    seasons = [
        {"id": 97436, "year": "26/27", "name": "Liga Portugal 26/27"},
        {"id": 77806, "year": "25/26", "name": "Liga Portugal 25/26"},
    ]
    sf = _fetcher_with_seasons(seasons)
    assert sf.resolve_season_id(238, 77806) == 77806


def test_resolve_replaces_stale_id():
    seasons = [
        {"id": 97436, "year": "26/27", "name": "Liga Portugal 26/27"},
        {"id": 77806, "year": "25/26", "name": "Liga Portugal 25/26"},
    ]
    sf = _fetcher_with_seasons(seasons)
    # Old id no longer published by SofaScore → 2nd-newest (25/26)
    assert sf.resolve_season_id(238, 77559) == 77806


def test_previous_season_for_stale_id():
    from src.match_fetcher import MatchFetcher

    seasons = [
        {"id": 97436, "year": "26/27", "name": "Liga Portugal 26/27"},
        {"id": 77806, "year": "25/26", "name": "Liga Portugal 25/26"},
    ]
    config = MagicMock()
    config.get_leagues.return_value = {238: "Liga Portugal"}
    sf = _fetcher_with_seasons(seasons)
    sf.preferred_download_season_id = lambda lid: 77806  # type: ignore
    sf._get_sortable_year_value = SeasonFetcher._get_sortable_year_value.__get__(sf, SeasonFetcher)
    fetcher = MatchFetcher(config, sf, data_dir="/tmp")
    prev = fetcher._previous_season(238, 77559)
    assert prev is not None
    assert prev["id"] == 77806
