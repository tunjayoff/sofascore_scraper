"""Kayıt defterini (sofascore_scraper/sports.py) okuyan modüller: lig sporları, skor çıkarımı, izleyici, indirici."""
import contextlib
import dataclasses
from unittest.mock import MagicMock, patch

import pytest

from sofascore_scraper import sports
from sofascore_scraper.services.live import reducer
from sofascore_scraper.match_data_fetcher import DETAIL_SLICE_KEYS, REQUIRED_FILES, MatchDataFetcher
from sofascore_scraper.sports import DetailSlice
from sofascore_scraper.status import BasketballScores, FootballScores, ScoreSheet, TennisScores, extract_scores
from sofascore_scraper.web import league_sports

REGISTERED = ("football", "basketball", "tennis", "american-football", "aussie-rules", "ice-hockey", "handball",
              "rugby", "futsal", "minifootball", "floorball",  # SP-1: + sekiz periyot sporu
              "volleyball", "badminton", "table-tennis", "padel", "snooker",  # SP-2: + beş set sporu
              "baseball", "cricket", "esports", "darts", "mma")  # SP-3: + beş B sınıfı spor
COMMON_KEYS = ("statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents")


# --- lig sporları (eski adlar) ------------------------------------------------------------

def test_league_sports_names_come_from_the_registry():
    assert league_sports.SPORTS == sports.sport_slugs() == REGISTERED
    for raw in ("Football", "basketball", " Tennis ", "Soccer", "ice-hockey", "table-tennis", "", None):
        assert league_sports.normalize_sport(raw) == sports.normalize_sport(raw)


# --- skor ailesi → extract_scores -------------------------------------------------------

@pytest.mark.parametrize("sport,cls", [("football", FootballScores), ("basketball", BasketballScores),
                                       ("tennis", TennisScores)])
def test_extract_scores_class_follows_the_score_family(sport, cls):
    event = {"id": 1, "status": {"code": 100, "type": "finished"}, "homeScore": {"current": 1},
             "awayScore": {"current": 0}}
    assert type(extract_scores(event, sport)) is cls
    by_name = dict(event, tournament={"category": {"sport": {"name": sport.capitalize()}}})
    assert type(extract_scores(by_name)) is cls


@pytest.mark.parametrize("sport", ["waterpolo", "beach-volley", "", None])
def test_extract_scores_for_unregistered_sport_is_a_bare_sheet(sport, caplog):
    event = {"id": 1, "status": {"code": 100, "type": "finished"}, "homeScore": {"current": 30},
             "awayScore": {"current": 28}, "winnerCode": 1}
    with caplog.at_level("WARNING"):
        sheet = extract_scores(event, sport)
    assert type(sheet) is ScoreSheet
    assert sheet.sport == (sport or None)
    assert sheet.winner_code == 1
    assert "unsupported sport" in caplog.text


# --- canlı indirgeyici (2.x izleyicisinin kuralları) -------------------------------------

def test_stuck_thresholds_keep_their_values():
    assert sports.DEFAULT_STUCK_AFTER_SECONDS == 4 * 3600
    assert sports.watcher_params("tennis").stuck_after_seconds == 6 * 3600


def test_every_registered_near_end_rule_is_implemented():
    for spec in sports.SPORTS:
        # "never": kural yok (sofascore_scraper/sports.py NearEndRule); SP-1'de saat verisi olmayan sporlar
        assert spec.watcher.near_end_rule == "never" or spec.watcher.near_end_rule in reducer.NEAR_END_RULES


def test_near_end_is_false_for_unregistered_sport():
    live_last_period = {"status": {"code": 7, "type": "inprogress"}, "time": {"injuryTime2": 4}}
    assert reducer.near_end(live_last_period, "football", 0.0) is True
    assert reducer.near_end(live_last_period, "waterpolo", 0.0) is False
    assert reducer.near_end(live_last_period, "futsal", 0.0) is False  # kayıtlı, kuralı "never"
    assert reducer.near_end(live_last_period, "Football", 0.0) is False  # slug tam eşleşir


def _stuck_after(tmp_path, sport: str, hours: float) -> bool:
    start = 1_790_000_000
    event = {"id": 9, "startTimestamp": start, "status": {"code": 20, "type": "inprogress"},
             "time": {"currentPeriodStartTimestamp": start + 3600}}
    now = start + hours * 3600
    state, _events = reducer.reduce(None, reducer.Observation(event=event, via="live", at=now), sport)
    return bool(state["stuck"])


@pytest.mark.parametrize("sport,hours,expected", [
    ("football", 3.9, False), ("football", 4.1, True),
    ("basketball", 3.9, False), ("basketball", 4.1, True),
    ("handball", 3.9, False), ("handball", 4.1, True),  # kayıtlı değil: varsayılan, startTimestamp'ten
    ("tennis", 6.9, False), ("tennis", 7.1, True),  # oyun başlangıcı (start + 1 sa) + 6 sa
])
def test_stuck_threshold_comes_from_the_registry(tmp_path, sport, hours, expected):
    assert _stuck_after(tmp_path, sport, hours) is expected


# --- indirici: dilimler tablodan --------------------------------------------------------

def test_fetcher_constants_derive_from_the_slice_table():
    # spora bağlı olmayan `required` dilimler: spora özel dilimler (dartın point_by_point'i, kriketin innings'i)
    # eski düzenin dosya listesine girmez
    assert DETAIL_SLICE_KEYS == tuple(s.key for s in sports.DETAIL_SLICES
                                      if s.required and s.sports is None) == COMMON_KEYS
    assert REQUIRED_FILES == ["basic.json"] + [f"{k}.json" for k in COMMON_KEYS]


def _basic(sport: str) -> dict:
    return {
        "id": 42,
        "tournament": {"name": "Cup", "uniqueTournament": {"id": 77, "name": "Cup"},
                       "category": {"sport": {"slug": sport}}},
        "season": {"id": 5, "name": "Cup 26", "year": "26"},
        "status": {"code": 100, "description": "Ended", "type": "finished"},
        "startTimestamp": 1790000000,
    }


@contextlib.contextmanager
def _api(sport: str, calls: list):
    """
    İstek katmanının sahtesi (boru hattının istemcisi sofascore_scraper.client.transport'u çağırır): /event/42 maçı, dilimler boş
    nesne; oturum ısınmasız. `calls` /event/42'den sonraki yol parçalarıyla dolar.
    """
    basic = _basic(sport)

    async def fake(session, url, max_retries=None, **kw):
        calls.append(url.rsplit("/event/42", 1)[1])
        return {"event": basic} if url.endswith("/event/42") else {}

    @contextlib.asynccontextmanager
    async def session():
        yield MagicMock()

    with patch("sofascore_scraper.utils.make_api_request_async", new=fake), patch("sofascore_scraper.utils.create_session_async", session):
        yield


def _batch_urls(tmp_path, sport: str) -> list:
    """Toplu indirmenin bir maç için istediği yollar: önce /event/42 (""), sonra dilimler (eşzamanlı: sıralı)."""
    f = MatchDataFetcher(MagicMock(), data_dir=str(tmp_path))
    calls: list = []
    with _api(sport, calls):
        f.fetch_matches_batch(["42"])
    return calls[:1] + sorted(calls[1:])


def test_disabled_slice_is_neither_requested_nor_expected(tmp_path, monkeypatch):
    """Dilimi kapatan ayar ileride eklenecek; tablo kapalı dediğinde indirici onu istemez ve eksik saymaz."""
    table = tuple(dataclasses.replace(s, default_enabled=s.key != "lineups") for s in sports.DETAIL_SLICES)
    monkeypatch.setattr(sports, "DETAIL_SLICES", table)

    assert "lineups" not in [s.key for s in sports.slices_for("football")]
    assert _batch_urls(tmp_path, "football") == ["", "/h2h", "/incidents", "/pregame-form", "/statistics",
                                                  "/team-streaks"]
    f = MatchDataFetcher(MagicMock(), data_dir=str(tmp_path))
    # FX-16: futbolda pregame_form istenir ama beklenmez
    assert f._expected_slice_keys(42, "football") == ["statistics", "team_streaks", "h2h", "incidents"]


def test_sport_specific_required_slice_is_fetched_by_every_path(tmp_path, monkeypatch):
    """Tabloya eklenen yeni bir dilim yalnızca kendi sporunda, üç giriş noktasında da istenir (tek boru hattı)."""
    extra = DetailSlice("innings", "/event/{event_id}/innings", sports=frozenset({"basketball"}))
    monkeypatch.setattr(sports, "DETAIL_SLICES", sports.DETAIL_SLICES + (extra,))

    assert "/innings" in _batch_urls(tmp_path / "a", "basketball")
    assert "/innings" not in _batch_urls(tmp_path / "b", "football")

    for sport, wanted in (("basketball", True), ("football", False)):
        f = MatchDataFetcher(MagicMock(), data_dir=str(tmp_path / f"single-{sport}"))
        calls: list = []
        with _api(sport, calls):
            data = f.fetch_match_data("42")
        assert ("/innings" in calls) is wanted
        assert ("innings" in data) is wanted
        assert ("innings" in f._expected_slice_keys(42, sport)) is wanted

        calls.clear()
        with _api(sport, calls):
            f.refill_missing_match_slices("42")
        assert ("/innings" in calls) is wanted
