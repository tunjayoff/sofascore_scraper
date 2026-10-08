"""League sport storage and inference (temp dirs only, no network)."""
from __future__ import annotations

import json
import os
import tempfile

from sofascore_scraper.web import league_sports


def _cfg(root: str) -> str:
    os.makedirs(os.path.join(root, "config"), exist_ok=True)
    return os.path.join(root, "config", "leagues.txt")


def test_normalize_sport():
    assert league_sports.normalize_sport("Football") == "football"
    assert league_sports.normalize_sport("basketball") == "basketball"
    assert league_sports.normalize_sport("Tennis") == "tennis"
    assert league_sports.normalize_sport("ice-hockey") == "ice-hockey"  # SP-1
    assert league_sports.normalize_sport("volleyball") == "volleyball"  # SP-2
    assert league_sports.normalize_sport("waterpolo") is None
    assert league_sports.normalize_sport(None) is None


def test_set_load_and_forget():
    root = tempfile.mkdtemp()
    cfg = _cfg(root)
    league_sports.set_sport(cfg, 132, "basketball")
    league_sports.set_sport(cfg, 17, "football")
    assert league_sports.load(cfg) == {132: "basketball", 17: "football"}
    league_sports.set_sport(cfg, 17, None)
    assert league_sports.load(cfg) == {132: "basketball"}


def test_infer_from_downloaded_match_without_writing():
    root = tempfile.mkdtemp()
    cfg = _cfg(root)
    data = os.path.join(root, "data")
    match_dir = os.path.join(data, "match_details", "132_NBA", "season_NBA_25_26", "14441992")
    os.makedirs(match_dir)
    with open(os.path.join(match_dir, "basic.json"), "w", encoding="utf-8") as f:
        json.dump({"id": 14441992, "tournament": {"uniqueTournament": {"id": 132},
                                                  "category": {"sport": {"name": "Basketball", "slug": "basketball"}}}}, f)

    out = league_sports.sports_for(cfg, data, [132, 17])
    assert out == {132: "basketball", 17: None}
    # a read writes nothing: the sidecar keeps only what the user set
    assert league_sports.load(cfg) == {}


def test_stored_sport_wins_over_inference():
    root = tempfile.mkdtemp()
    cfg = _cfg(root)
    league_sports.set_sport(cfg, 132, "football")  # user's explicit choice
    data = os.path.join(root, "data")
    match_dir = os.path.join(data, "match_details", "132_X", "s", "1")
    os.makedirs(match_dir)
    with open(os.path.join(match_dir, "basic.json"), "w", encoding="utf-8") as f:
        json.dump({"tournament": {"category": {"sport": {"slug": "basketball"}}}}, f)
    assert league_sports.sports_for(cfg, data, [132]) == {132: "football"}

