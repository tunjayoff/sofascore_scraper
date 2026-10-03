"""Veri hattı doğruluğu: tur tazeliği, 'bitti' tanımı, eksik dilimler, sezon seçimi, web özetleri."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import conftest
from src.match_data_fetcher import (SLICE_EMPTY, SLICE_FAILED, UNAVAILABLE_AFTER_ATTEMPTS, MatchDataFetcher,
                                    SliceOutcome)
from src.match_fetcher import MatchFetcher
from src.paths import league_dir_name, season_dir_name, seasons_file, summary_paths
from src.slices import SLICE_OK, Outcome
from src.store import Ref, open_store, shadow_schedules
from src.web import deps
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
# Tur sayfası Store'dadır (ST-22): önbellek kararı dilimin katalogdaki kaydından (meta.complete, fetched_at) gelir.

SEASON = 96668


def _store_round(tmp_path, data, *, complete, age_seconds=0):
    fetched = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    open_store(str(tmp_path)).entities.put(
        Ref.season(17, SEASON), {("schedule", "round_1"): Outcome(SLICE_OK, data, fetched_at=fetched,
                                                                  meta={"complete": complete})})


def _legacy_round_file(tmp_path, data):
    """Eski sürümün yazdığı tur dosyası (matches/<lig>/<sezon>/round_1.json)."""
    season_dir = tmp_path / "matches" / "17_Premier_League" / f"{SEASON}_Premier_League_26_27"
    season_dir.mkdir(parents=True, exist_ok=True)
    (season_dir / "round_1.json").write_text(json.dumps(data))
    shadow_schedules(str(tmp_path))  # testin kendi yazdığı eski dosya kataloğa girer


def test_complete_round_is_reused(tmp_path):
    f = _fetcher(tmp_path)
    _store_round(tmp_path, {"events": [_event(1)]}, complete=True, age_seconds=10 * 86400)
    assert f._load_cached_round(17, SEASON, "round_1") == {"events": [_event(1)]}


def test_incomplete_round_is_refetched_after_ttl(tmp_path):
    f = _fetcher(tmp_path)
    data = {"events": [_event(1), _event(2, "notstarted", "Not started", 0)]}
    _store_round(tmp_path, data, complete=False)
    assert f._load_cached_round(17, SEASON, "round_1") is not None
    _store_round(tmp_path, data, complete=False, age_seconds=MatchFetcher.ROUND_CACHE_TTL_SECONDS + 60)
    assert f._load_cached_round(17, SEASON, "round_1") is None


def test_legacy_round_file_is_refetched_once(tmp_path):
    """Eski sürüm yalnız bitmiş maçları süzüp yazıyordu; _complete yok → yeniden çek."""
    f = _fetcher(tmp_path)
    _legacy_round_file(tmp_path, {"events": [_event(1)]})
    assert f._load_cached_round(17, SEASON, "round_1") is None


def test_complete_legacy_round_file_is_reused(tmp_path):
    """`_complete` taşıyan eski tur dosyası da önbellektir (yükü `_complete` anahtarı olmadan gelir)."""
    f = _fetcher(tmp_path)
    _legacy_round_file(tmp_path, {"events": [_event(1)], "_complete": True})
    assert f._load_cached_round(17, SEASON, "round_1") == {"events": [_event(1)]}


def test_round_saves_raw_payload_and_returns_only_finished(tmp_path):
    f = _fetcher(tmp_path)
    payload = {"events": [_event(1), _event(2, "inprogress", "1st half", 6)]}
    with patch("src.utils.make_api_request_async", new=AsyncMock(return_value=payload)), \
            patch("src.utils.FETCH_ONLY_FINISHED", True):
        result = asyncio.run(
            f._fetch_and_save_round(asyncio.Semaphore(2), None, 17, SEASON, 1)
        )
    store = open_store(str(tmp_path))
    saved = store.entities.payload(Ref.season(17, SEASON), "schedule", "round_1")
    assert [e["id"] for e in saved["events"]] == [1, 2] and "_complete" not in saved
    assert store.entities.slice(Ref.season(17, SEASON), "schedule", "round_1").meta == {"complete": False}
    assert not (tmp_path / "matches" / "17_Premier_League").exists()
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


_EMPTY_SLICES = ("lineups", "incidents", "team_streaks", "pregame_form", "h2h")


def _partial_match():
    return {"basic": _basic(), "statistics": {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": [{"x": 1}]}]}]},
            **{key: None for key in _EMPTY_SLICES}}


def test_slice_confirmed_empty_twice_is_no_longer_expected(tmp_path):
    """İki kesin "yok" yanıtı (404 ya da içinde veri olmayan 200) dilimi o maç için beklenmez yapar."""
    f = _detail_fetcher(tmp_path)
    data = _partial_match()
    confirmed = {key: SliceOutcome(SLICE_EMPTY, reason="404", http_status=404) for key in _EMPTY_SLICES}
    f._save_match_data("42", data, confirmed)
    assert f._needs_detail_fetch("42") == "refill"
    for _ in range(UNAVAILABLE_AFTER_ATTEMPTS - 1):
        f._save_match_data("42", data, confirmed)
    assert f._needs_detail_fetch("42") == "none"


@pytest.mark.parametrize("outcomes", [
    None,  # sonucu bilinmeyen boş dilim: istenip istenmediği belli değil
    {key: SliceOutcome(SLICE_FAILED, reason="429", http_status=429) for key in _EMPTY_SLICES},
], ids=["unknown", "failed"])
def test_slice_missing_without_a_definitive_answer_stays_expected(tmp_path, outcomes):
    """Eski kural her boş dilimi sayıyordu; başarısız istek kaç kez olursa olsun "yok" sayılmaz."""
    f = _detail_fetcher(tmp_path)
    for _ in range(UNAVAILABLE_AFTER_ATTEMPTS + 3):
        f._save_match_data("42", _partial_match(), outcomes)
    assert f._needs_detail_fetch("42") == "refill"


def test_sync_fetch_accepts_aet(tmp_path):
    f = _detail_fetcher(tmp_path)
    from fakes.sofascore import FakeSofaScore

    fake = FakeSofaScore()  # P13: tek maç da boru hattından; dilimler 404
    fake.add_event(_basic(sport="football", desc="AET"))
    with fake:
        assert f.fetch_match_data(42) is not None


# --- kapsam raporu (P15: katalogdan, dosya yazılmaz) -------------------------------------------------

def _files(root) -> set:
    return {os.path.relpath(os.path.join(d, n), root) for d, dirs, names in os.walk(root)
            for n in names if ".meta" not in d.split(os.sep)}


def test_file_report_comes_from_the_catalog_and_writes_nothing(tmp_path, capsys):
    f = _detail_fetcher(tmp_path)
    f._save_match_data("42", _partial_match(), {key: SliceOutcome(SLICE_EMPTY, reason="404", http_status=404)
                                                 for key in _EMPTY_SLICES})
    for _ in range(UNAVAILABLE_AFTER_ATTEMPTS):  # istatistik yeterince kez kesin "yok": artık eksik sayılmaz
        f._save_match_data("43", {**_partial_match(), "basic": _basic(43), "statistics": None},
                           {"statistics": SliceOutcome(SLICE_EMPTY, reason="404", http_status=404)})
    capsys.readouterr()
    before = _files(tmp_path)

    report = f.generate_file_report()
    assert capsys.readouterr().out == ""
    assert _files(tmp_path) == before
    assert not os.path.exists(os.path.join(f.processed_dir, "match_files_stats.json"))
    assert set(report) == {"league_stats", "overall_stats"}
    # 42: tek kesin "yok" yetmez, beş dilim eksik; 43: istatistik beklenmez, beş dilim eksik
    missing = {f"{key}.json": 2 for key in ("team_streaks", "pregame_form", "h2h", "lineups", "incidents")}
    assert report["overall_stats"] == {"total_matches": 2, "matches_with_all_files": 0, "completion_rate": 0.0,
                                       "missing_files": missing}
    (league, stats), = report["league_stats"].items()
    assert league == "2361_Wimbledon,_Men"
    assert stats["total_matches"] == 2 and stats["complete_matches"] == 0
    assert set(stats["seasons"]) == {"season_1"}
    assert stats["seasons"]["season_1"]["missing_files"] == stats["missing_files"]


def test_file_report_of_another_folder(tmp_path):
    here, other = tmp_path / "here", tmp_path / "other"
    _detail_fetcher(other)._save_match_data("42", _partial_match())
    f = _detail_fetcher(here)
    assert f.generate_file_report()["overall_stats"]["total_matches"] == 0
    for path in (other, other / "match_details", str(other) + os.sep):
        assert f.generate_file_report(str(path))["overall_stats"]["total_matches"] == 1
    assert f.generate_file_report(str(here / "match_details"))["overall_stats"]["total_matches"] == 0
    stray = tmp_path / "stray"
    stray.mkdir()
    assert f.generate_file_report(str(stray)) == {}
    assert os.listdir(stray) == []  # rastgele dizinde depo kurulmaz


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
    """EX-1: dışa aktarma saklanan maçlardan istekte üretilir; `processed/` altındaki bayat bir dosya sunulmaz."""
    processed = os.path.join(conftest.DATA_DIR, "match_details", "processed")
    os.makedirs(processed, exist_ok=True)
    path = os.path.join(processed, "all_matches_1.csv")
    with open(path, "w") as f:
        f.write("match_id,league_folder\n1,17_Premier_League\n2,8_LaLiga\n3,170_Other\n")
    try:
        whole = client.get("/api/export/csv").text.strip().splitlines()
        league = client.get(f"/api/export/csv?league_id={conftest.LEAGUE_ID}").text.strip().splitlines()
        other = client.get("/api/export/csv?league_id=8")
        assert whole[0].startswith("match_id,") and len(whole) > 1
        assert "1,17_Premier_League" not in whole and "2,8_LaLiga" not in whole
        assert league[0] == whole[0] and len(league) > 1
        assert all(line.split(",")[1].startswith(f"{conftest.LEAGUE_ID}_") for line in league[1:])
        assert other.status_code == 404
        assert os.listdir(processed) == ["all_matches_1.csv"]
    finally:
        os.remove(path)


def test_single_fetch_conflicts_with_running_job():

    deps.job_store().create_running({"mode": "full"})
    try:
        assert client.post(f"/api/matches/{conftest.MATCH_IDS[1]}/fetch").status_code == 409
    finally:
        deps.job_store().update(status="Cancelled", progress=0, current_task="cleanup", finished=True)
