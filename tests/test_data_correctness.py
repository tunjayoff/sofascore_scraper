"""Veri hattı doğruluğu: tur tazeliği, 'bitti' tanımı, eksik dilimler, sezon seçimi, web özetleri."""
from __future__ import annotations

import asyncio
import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import conftest
from src.match_data_fetcher import UNAVAILABLE_AFTER_ATTEMPTS, MatchDataFetcher
from src.match_fetcher import MatchFetcher
from src.paths import league_dir_name, season_dir_name, seasons_file, summary_paths
from src.web.app import app

client = TestClient(app)


def _event(mid, status_type="finished", desc="Ended", code=100):
    return {"id": mid, "status": {"type": status_type, "description": desc, "code": code}}


def _fetcher(tmp_path) -> MatchFetcher:
    config = MagicMock()
    config.get_leagues.return_value = {17: "Premier League"}
    config.get_league_by_id.return_value = "Premier League"
    return MatchFetcher(config, MagicMock(), data_dir=str(tmp_path))


# --- tur önbelleği ---------------------------------------------------------------

def _round_file(tmp_path, data, age_seconds=0):
    path = tmp_path / "round_1.json"
    path.write_text(json.dumps(data))
    if age_seconds:
        t = time.time() - age_seconds
        os.utime(path, (t, t))
    return str(path)


def test_complete_round_is_reused(tmp_path):
    f = _fetcher(tmp_path)
    path = _round_file(tmp_path, {"events": [_event(1)], "_complete": True}, age_seconds=10 * 86400)
    assert f._load_cached_round(path) is not None


def test_incomplete_round_is_refetched_after_ttl(tmp_path):
    f = _fetcher(tmp_path)
    data = {"events": [_event(1), _event(2, "notstarted", "Not started", 0)], "_complete": False}
    fresh = _round_file(tmp_path, data)
    assert f._load_cached_round(fresh) is not None
    old = _round_file(tmp_path, data, age_seconds=MatchFetcher.ROUND_CACHE_TTL_SECONDS + 60)
    assert f._load_cached_round(old) is None


def test_legacy_round_file_is_refetched_once(tmp_path):
    """Eski sürüm yalnız bitmiş maçları süzüp yazıyordu; _complete yok → yeniden çek."""
    f = _fetcher(tmp_path)
    assert f._load_cached_round(_round_file(tmp_path, {"events": [_event(1)]})) is None


def test_round_saves_raw_payload_and_returns_only_finished(tmp_path):
    f = _fetcher(tmp_path)
    payload = {"events": [_event(1), _event(2, "inprogress", "1st half", 6)]}
    with patch("src.utils.make_api_request_async", new=AsyncMock(return_value=payload)), \
            patch("src.utils.FETCH_ONLY_FINISHED", True):
        result = asyncio.run(
            f._fetch_and_save_round(asyncio.Semaphore(2), None, 17, 1, 1, str(tmp_path))
        )
    saved = json.loads((tmp_path / "round_1.json").read_text())
    assert [e["id"] for e in saved["events"]] == [1, 2]
    assert saved["_complete"] is False
    assert [e["id"] for e in result["events"]] == [1]
    assert result["round"] == 1


@pytest.mark.parametrize("status", [
    {"type": "finished", "description": "AET", "code": 110},
    {"type": "finished", "description": "AP", "code": 120},
    {"type": "finished", "description": "Ended", "code": 100},
])
def test_finished_includes_extra_time_and_penalties(status):
    assert MatchFetcher._is_finished_event({"status": status})


# --- maç detayları ------------------------------------------------------------------

def _detail_fetcher(tmp_path) -> MatchDataFetcher:
    return MatchDataFetcher(MagicMock(), data_dir=str(tmp_path))


def _basic(mid=42, sport="tennis", desc="Ended"):
    return {
        "id": mid,
        "status": {"type": "finished", "description": desc, "code": 100},
        "tournament": {"uniqueTournament": {"id": 2361, "name": "Wimbledon, Men"},
                       "category": {"sport": {"slug": sport}}},
        "season": {"id": 1, "name": "Wimbledon Men Singles 2025"},
    }


def test_slice_missing_twice_is_no_longer_expected(tmp_path):
    f = _detail_fetcher(tmp_path)
    data = {"basic": _basic(), "statistics": {"statistics": [{"period": "ALL", "groups": [{"x": 1}]}]},
            "lineups": None, "incidents": None, "team_streaks": None, "pregame_form": None, "h2h": None}
    f._save_match_data("42", data)
    assert f._needs_detail_fetch("42") == "refill"
    for _ in range(UNAVAILABLE_AFTER_ATTEMPTS - 1):
        f._save_match_data("42", data)
    assert f._needs_detail_fetch("42") == "none"


def test_sync_fetch_accepts_aet(tmp_path):
    f = _detail_fetcher(tmp_path)
    with patch.object(f, "_fetch_match_basic", return_value=_basic(sport="football", desc="AET")):
        for name in ("_fetch_match_statistics", "_fetch_team_streaks", "_fetch_pregame_form",
                     "_fetch_h2h", "_fetch_lineups", "_fetch_incidents"):
            setattr(f, name, MagicMock(return_value=None))
        assert f.fetch_match_data(42) is not None


def test_summary_files_sorted_numerically_and_limited(tmp_path):
    for sid in (9999, 10000, 500):
        (tmp_path / f"{sid}_S_summary.csv").write_text("match_id\n")
    files = MatchDataFetcher._season_summary_files(str(tmp_path), None, 2)
    assert [os.path.basename(p).split("_")[0] for p in files] == ["10000", "9999"]
    only = MatchDataFetcher._season_summary_files(str(tmp_path), [500], 0)
    assert [os.path.basename(p) for p in only] == ["500_S_summary.csv"]


# --- yol düzeni -----------------------------------------------------------------

def test_path_helpers_match_existing_layout():
    assert league_dir_name(17, "Premier League") == "17_Premier_League"
    assert league_dir_name(2361, "Wimbledon, Men") == "2361_Wimbledon,_Men"
    assert season_dir_name(96668, "Premier League 26/27") == "96668_Premier_League_26_27"
    j, c = summary_paths("d", 17, "Premier League", 96668, "Premier League 26/27")
    assert j == os.path.join("d", "matches", "17_Premier_League", "96668_Premier_League_26_27_summary.json")
    assert c.endswith("_summary.csv")
    # Config'de olmayan lig: kaydeden ve okuyan aynı adı kullanır
    assert seasons_file("d", 5, None) == os.path.join("d", "seasons", "5_League_5_seasons.json")


# --- web özetleri ---------------------------------------------------------------

def test_dashboard_counts_seasons():
    card = client.get("/api/dashboard").json()["leagues"][0]
    assert card["id"] == conftest.LEAGUE_ID
    assert card["seasons"] == 1


def test_missing_details_respects_season_filter():
    lid = conftest.LEAGUE_ID
    all_ = client.get(f"/api/leagues/{lid}/missing-details").json()
    assert all_["missing_count"] == 1 and all_["truncated"] is False
    same = client.get(f"/api/leagues/{lid}/missing-details?season_id={conftest.SEASON_ID}").json()
    assert same["missing_count"] == 1
    other = client.get(f"/api/leagues/{lid}/missing-details?season_id=1").json()
    assert other["total_matches"] == 0


def test_csv_export_filters_by_league():
    processed = os.path.join(conftest.DATA_DIR, "match_details", "processed")
    os.makedirs(processed, exist_ok=True)
    path = os.path.join(processed, "all_matches_1.csv")
    with open(path, "w") as f:
        f.write("match_id,league_folder\n1,17_Premier_League\n2,8_LaLiga\n3,170_Other\n")
    try:
        body = client.get("/api/export/csv?league_id=17").text.strip().splitlines()
        assert body == ["match_id,league_folder", "1,17_Premier_League"]
        assert len(client.get("/api/export/csv").text.strip().splitlines()) == 4
    finally:
        os.remove(path)


def test_single_fetch_conflicts_with_running_job():
    from src.web.routes import api as api_mod

    api_mod._job_store.create_running({"mode": "full"})
    try:
        assert client.post(f"/api/matches/{conftest.MATCH_IDS[1]}/fetch").status_code == 409
    finally:
        api_mod._job_store.update(status="Cancelled", progress=0, current_task="cleanup", finished=True)
