"""extract_scores: tests/fixtures/status altındaki gerçek SofaScore yanıtları."""
import dataclasses
import json
from pathlib import Path

import pytest

from sofascore_scraper.status import (BasketballScores, CricketScores, FightScores, FootballScores, InningsScores, Pair,
                        PeriodsScores, SetsScores, StatusClass, TennisScores, classify_status, extract_scores)

FIXTURES = Path(__file__).parent / "fixtures" / "status"


def _load(rel: str) -> dict:
    return json.loads((FIXTURES / f"{rel}.json").read_text(encoding="utf-8"))


def _values(sheet) -> list:
    """ScoreSheet içindeki tüm skor değerleri (düz liste)."""
    out = []

    def walk(v):
        if isinstance(v, Pair):
            out.extend([v.home, v.away])
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for f in dataclasses.fields(sheet):
        if f.name not in ("winner_code", "aggregated_winner_code", "raw_change_ts", "raw_changed_fields"):
            walk(getattr(sheet, f.name))
    return out


# --- futbol -----------------------------------------------------------------------------

def test_football_aet():
    s = extract_scores(_load("football/F1_aet__17148332"), "football")
    assert isinstance(s, FootballScores)
    assert s.ft90 == Pair(1, 1)
    assert s.aet == Pair(2, 1)
    assert s.ht == Pair(0, 0)
    assert s.penalties is None
    assert s.winner_code == 1
    assert s.settleable


def test_football_penalties_never_use_current():
    event = _load("football/F2_penalties__16950622")
    assert (event["homeScore"]["current"], event["awayScore"]["current"]) == (10, 9)
    s = extract_scores(event, "football")
    assert s.ft90 == Pair(3, 3)
    # Uzatma anahtarı yok (overtime / extra1 / extra2): doğrudan penaltılara gidilmiş, uzatma skoru yok (FX-23)
    assert s.aet is None
    assert s.penalties == Pair(7, 6)
    assert s.winner_code == 1
    assert 10 not in _values(s) and 9 not in _values(s)


def test_football_penalties_without_extra_time_have_no_after_extra_time():
    """F10 (FX-23): kod 120 "AP" tek başına uzatma demek değil; UEFA Süper Kupası 2025 doğrudan penaltıya gitti."""
    event = json.loads((FIXTURES.parent / "fx23" / "football_ap_without_extra_time__13960989.json")
                       .read_text(encoding="utf-8"))
    s = extract_scores(event)
    assert isinstance(s, FootballScores)
    assert s.status_class is StatusClass.COMPLETED
    assert s.ht == Pair(0, 1)
    assert s.ft90 == Pair(2, 2)
    assert s.aet is None
    assert s.penalties == Pair(4, 3)
    assert 6 not in _values(s) and 5 not in _values(s)  # current (penaltılar dahil) okunmaz


def test_football_penalties_after_extra_time_keep_after_extra_time():
    """Uzatma oynanıp 0-0 bitti, sonra penaltılar: extra1 / extra2 / overtime gelir, uzatma skoru dolu kalır."""
    s = extract_scores(_load("football/F2_penalties__17090707"), "football")
    assert s.ft90 == Pair(1, 1)
    assert s.aet == Pair(1, 1)
    assert s.penalties == Pair(5, 4)


@pytest.mark.parametrize(("key", "code", "expected"), [
    ("overtime", 120, True), ("extra1", 120, True), ("extra2", 120, True), (None, 120, False),
    (None, 110, True), ("overtime", 100, False),
])
def test_football_extra_time_needs_an_extra_time_key_on_code_120(key, code, expected):
    home = {"display": 1, "normaltime": 1, "penalties": 4}
    away = {"display": 1, "normaltime": 1, "penalties": 3}
    if key:
        home[key], away[key] = 0, 0
    event = {"status": {"code": code, "type": "finished"}, "homeScore": home, "awayScore": away}
    s = extract_scores(event, "football")
    assert s.aet == (Pair(1, 1) if expected else None)


def test_football_cup_draw_with_aggregate():
    s = extract_scores(_load("football/F6_cup_draw_aggregate__16872361"), "football")
    assert s.winner_code == 3
    assert s.ft90 == Pair(1, 1)
    assert s.aggregated == Pair(1, 2)
    assert s.aggregated_winner_code == 2
    assert s.aet is None  # code 100


def test_football_regular(caplog):
    with caplog.at_level("WARNING", logger="sofascore_scraper.status"):
        s = extract_scores(_load("football/B6_finished_regular__16837335"), "football")
    assert s.ht == Pair(0, 1)
    assert s.ft90 == Pair(3, 1)
    assert s.aet is None
    assert s.winner_code == 1
    assert "display" not in caplog.text


# --- basketbol --------------------------------------------------------------------------

def test_basketball_overtime():
    s = extract_scores(_load("basketball/K1_overtime_finished__16346148"), "basketball")
    assert isinstance(s, BasketballScores)
    assert s.format == "quarters"
    assert s.regulation == Pair(92, 92)
    assert s.overtime == Pair(8, 9)
    assert s.final == Pair(100, 101)
    assert s.winner_code == 2
    assert s.settleable


def test_basketball_two_halves():
    s = extract_scores(_load("basketball/K3_two_halves__16694075"), "basketball")
    assert s.format == "halves"
    assert set(s.periods) == {"period2", "period4"}
    assert s.periods["period2"].home + s.periods["period4"].home == 81
    assert s.final == Pair(81, 75)


def test_basketball_four_quarters():
    s = extract_scores(_load("basketball/K3_four_quarters__16484334"), "basketball")
    assert s.format == "quarters"
    assert sum(p.home for p in s.periods.values()) == s.regulation.home == 99
    assert sum(p.away for p in s.periods.values()) == s.regulation.away == 116


@pytest.mark.parametrize("rel", ["basketball/A_inprogress-13-1st-quarter__17157547", "basketball/B9_abandoned__17100305"])
def test_basketball_partial_game_is_quarters(rel):
    assert extract_scores(_load(rel), "basketball").format == "quarters"


def test_basketball_abandoned_extracts_but_not_settleable():
    s = extract_scores(_load("basketball/B9_abandoned__17060394"), "basketball")
    assert s.status_class is StatusClass.VOID
    assert s.final == Pair(59, 57)
    assert not s.settleable


# --- tenis ------------------------------------------------------------------------------

def test_tennis_finished():
    s = extract_scores(_load("tennis/T1_finished__17204710"), "tennis")
    assert isinstance(s, TennisScores)
    assert s.sets_won == Pair(2, 0)
    assert s.games == [Pair(6, 4), Pair(6, 3)]
    assert not (s.retired or s.walkover or s.match_tiebreak)
    assert s.settleable


def test_tennis_retired():
    event = _load("tennis/T2_retired__17081861")
    assert "normaltime" not in event["homeScore"]
    s = extract_scores(event, "tennis")
    assert s.sets_won == Pair(1, 1)
    assert s.games == [Pair(6, 7), Pair(6, 2), Pair(1, 0)]
    assert s.tiebreaks == {1: Pair(6, 8)}
    assert s.retired and not s.walkover
    assert s.winner_code == 1
    assert s.status_class is StatusClass.DECIDED_WITHOUT_PLAY
    assert not s.settleable


def test_tennis_walkover():
    s = extract_scores(_load("tennis/T3_walkover__17058663"), "tennis")
    assert s.sets_won is None and s.games == []
    assert s.walkover and not s.retired
    assert s.winner_code == 1
    assert not s.settleable


def test_tennis_match_tiebreak_heuristic():
    s = extract_scores(_load("tennis/T8_match_tiebreak__17078471"), "tennis")
    assert s.games[-1] == Pair(10, 4)
    assert s.match_tiebreak


# --- periyot tabanlı sporlar (plan maddesi SP-1; research/all_sports örnekleri) ---------------------------

def test_basketball_keeps_its_own_sheet_without_the_new_fields():
    s = extract_scores(_load("basketball/K1_overtime_finished__16346148"), "basketball")
    assert type(s) is BasketballScores
    assert not hasattr(s, "penalties")


def test_handball_shootout():
    """Hentbol AP: normal süre, uzatma ve penaltılar ayrı; `current` penaltıları içerir (22 + 5 + 4 = 31)."""
    s = extract_scores(_load("handball/A_finished-120-ap__15251094"), "handball")
    assert isinstance(s, PeriodsScores)
    assert s.format == "halves"
    assert s.periods == {"period1": Pair(11, 11), "period2": Pair(11, 11)}
    assert s.regulation == Pair(22, 22)
    assert s.overtime == Pair(5, 5)
    assert s.penalties == Pair(4, 3)
    assert s.final == Pair(31, 30)
    assert s.aggregated is None and s.aggregated_winner_code is None
    assert s.winner_code == 1 and s.settleable


def test_handball_aggregate():
    s = extract_scores(_load("handball/S1_aggregated__15986085"), "handball")
    assert s.aggregated == Pair(80, 65)
    assert s.aggregated_winner_code is None  # örnekte alan yok
    assert s.final == Pair(45, 37) and s.penalties is None


def test_ice_hockey_overtime_in_thirds():
    s = extract_scores(_load("ice-hockey/A_finished-110-aet__16356203"), "ice-hockey")
    assert s.format == "thirds"
    assert list(s.periods) == ["period1", "period2", "period3"]
    assert s.regulation == Pair(5, 5) and s.overtime == Pair(1, 0) and s.final == Pair(6, 5)


def test_american_football_overtime_in_quarters():
    s = extract_scores(_load("american-football/A_finished-110-aet__13899141"), "american-football")
    assert s.format == "quarters"
    assert sum(p.home for p in s.periods.values()) == s.regulation.home == 20
    assert s.overtime == Pair(3, 0) and s.final == Pair(23, 20)


@pytest.mark.parametrize("rel,fmt", [
    ("aussie-rules/A_finished-100-ended__12869496", "quarters"),
    ("floorball/A_finished-100-ended__16952186", "thirds"),
    ("rugby/A_finished-100-ended__16237238", "halves"),
    ("minifootball/A_finished-100-ended__17218801", "halves"),
    ("futsal/S2_halves_overtime__17221485", "halves"),
])
def test_periods_add_up_to_regulation(rel, fmt):
    sport = rel.split("/")[0]
    s = extract_scores(_load(rel), sport)
    assert s.format == fmt
    assert len(s.periods) == {"quarters": 4, "halves": 2, "thirds": 3}[fmt]
    assert sum(p.home for p in s.periods.values()) == s.regulation.home
    assert sum(p.away for p in s.periods.values()) == s.regulation.away


@pytest.mark.parametrize("rel", ["futsal/A_finished-100-ended__16129341", "futsal/A_finished-110-aet__17121445",
                                 "floorball/S3_normaltime_only__17049250"])
def test_headline_only_scores_have_no_format(rel):
    """Bazı futsal ve florbol turnuvaları periyot skoru vermez: bölünüş yok, sonuç yine okunur."""
    s = extract_scores(_load(rel), rel.split("/")[0])
    assert s.format is None and s.periods == {}
    assert s.final is not None


@pytest.mark.parametrize("rel", ["handball/A_notstarted-0-not-started__16419100",
                                 "ice-hockey/A_canceled-70-canceled__16341235"])
def test_period_sport_without_a_score(rel):
    s = extract_scores(_load(rel), rel.split("/")[0])
    assert isinstance(s, PeriodsScores)
    assert s.format is None and s.final is None and s.penalties is None and s.aggregated is None


def test_new_sports_are_no_longer_unsupported(caplog):
    with caplog.at_level("WARNING", logger="sofascore_scraper.status"):
        extract_scores(_load("rugby/A_finished-100-ended__16237238"), "rugby")
        extract_scores(_load("volleyball/A_finished-100-ended__16506696"), "volleyball")
        extract_scores(_load("rugby/A_finished-100-ended__16237238"), "waterpolo")
    assert caplog.text.count("unsupported sport") == 1 and "'waterpolo'" in caplog.text


# --- set tabanlı sporlar (plan maddesi SP-2; research/all_sports örnekleri) ---------------------------------

def test_tennis_keeps_its_own_sheet():
    s = extract_scores(_load("tennis/T2_retired__17081861"), "tennis")
    assert type(s) is TennisScores and s.retired


def test_volleyball_five_sets_of_points():
    s = extract_scores(_load("volleyball/S4_five_sets__16885517"), "volleyball")
    assert type(s) is SetsScores
    assert s.format == "points"
    assert s.sets_won == Pair(3, 2)
    assert s.sets == {1: Pair(25, 20), 2: Pair(13, 25), 3: Pair(17, 25), 4: Pair(25, 23), 5: Pair(15, 12)}
    assert s.tiebreaks == {}
    assert not s.match_tiebreak  # sayıyla sayılan sette 15 puan match tie-break değildir
    assert s.winner_code == 1 and s.settleable


@pytest.mark.parametrize("rel,sets_won,sets", [
    ("volleyball/A_finished-100-ended__16506696", Pair(3, 0), {1: Pair(25, 14), 2: Pair(25, 23), 3: Pair(25, 23)}),
    ("badminton/A_finished-100-ended__17185944", Pair(2, 1), {1: Pair(11, 21), 2: Pair(21, 16), 3: Pair(21, 19)}),
    ("table-tennis/A_finished-100-ended__17214760", Pair(3, 1),
     {1: Pair(13, 11), 2: Pair(8, 11), 3: Pair(12, 10), 4: Pair(11, 7)}),
    ("padel/A_finished-100-ended__17213163", Pair(2, 0), {1: Pair(6, 3), 2: Pair(6, 3)}),
])
def test_sets_won_follow_the_set_scores(rel, sets_won, sets):
    s = extract_scores(_load(rel), rel.split("/")[0])
    assert s.sets_won == sets_won and s.sets == sets
    won = Pair(sum(p.home > p.away for p in sets.values()), sum(p.away > p.home for p in sets.values()))
    assert won == sets_won
    assert not s.match_tiebreak


def test_padel_tiebreaks_like_tennis():
    s = extract_scores(_load("padel/S5_tiebreaks__17213167"), "padel")
    assert s.format == "games"
    assert s.sets == {1: Pair(7, 6), 2: Pair(5, 7), 3: Pair(6, 7)}
    assert s.tiebreaks == {1: Pair(7, 4), 3: Pair(6, 8)}
    assert s.sets_won == Pair(1, 2) and s.winner_code == 2
    assert not s.match_tiebreak  # 3. set 6:7 normal set


def test_padel_match_tiebreak_heuristic():
    event = _load("padel/S5_tiebreaks__17213167")
    event["homeScore"] = {"current": 1, "period1": 6, "period2": 3, "period3": 10}
    event["awayScore"] = {"current": 2, "period1": 4, "period2": 6, "period3": 8}
    assert extract_scores(event, "padel").match_tiebreak


def test_table_tennis_live_payload_keeps_the_set_number():
    """Canlı listedeki yük yalnızca o anki seti taşıyabiliyor: set numarası korunur, boşluk doldurulmaz."""
    event = _load("table-tennis/A_inprogress-11-4th-set__17220207")
    s = extract_scores(event, "table-tennis")
    assert s.sets == {4: Pair(9, 6)}
    assert s.sets_won == Pair(1, 2)
    assert s.status_class is StatusClass.LIVE


@pytest.mark.parametrize("code", [11, 12])
def test_fourth_and_fifth_set_codes_are_live_without_a_type(code):
    event = _load("table-tennis/A_inprogress-11-4th-set__17220207")
    event["status"] = {"code": code}
    assert classify_status(event) is StatusClass.LIVE


def test_seven_sets_are_read():
    """Masa tenisinde 7 setlik maç: kayıtlı yüklerde görülmedi, period6-7 okunur."""
    event = _load("table-tennis/A_inprogress-12-5th-set__17225596")
    event["homeScore"] = dict(event["homeScore"], period6=11, period7=11, current=4)
    event["awayScore"] = dict(event["awayScore"], period6=9, period7=5, current=3)
    s = extract_scores(event, "table-tennis")
    assert sorted(s.sets) == [1, 2, 3, 4, 5, 6, 7] and s.sets[7] == Pair(11, 5)


@pytest.mark.parametrize("rel,frames", [("snooker/A_finished-100-ended__17218595", Pair(0, 5)),
                                        ("snooker/A_inprogress-20-started__17220573", Pair(2, 2))])
def test_snooker_counts_frames_and_has_no_sets(rel, frames):
    """Snooker: `current` kazanılan frame; period1 `current`'ı tekrarlar, set sayılmaz."""
    event = _load(rel)
    assert event["homeScore"]["period1"] == event["homeScore"]["current"]
    s = extract_scores(event, "snooker")
    assert s.format == "frames"
    assert s.sets_won == frames and s.sets == {} and s.tiebreaks == {}
    assert not s.match_tiebreak


@pytest.mark.parametrize("rel", ["badminton/A_finished-91-walkover__17197207",
                                 "table-tennis/A_finished-91-walkover__17220159",
                                 "padel/A_finished-91-walkover__17213165"])
def test_walkover_is_told_by_the_status_not_by_the_score(rel):
    """04-schema-v1.md karar 10: tenis dışındaki set sporlarının çizelgesinde retired / walkover bayrağı yok."""
    s = extract_scores(_load(rel), rel.split("/")[0])
    assert s.status_class is StatusClass.DECIDED_WITHOUT_PLAY
    assert not hasattr(s, "walkover") and not hasattr(s, "retired")
    assert s.sets_won is None and s.sets == {}
    assert not s.settleable


@pytest.mark.parametrize("rel", ["volleyball/A_notstarted-0-not-started__16450376",
                                 "badminton/A_canceled-70-canceled__17185946"])
def test_set_sport_without_a_score(rel):
    s = extract_scores(_load(rel), rel.split("/")[0])
    assert isinstance(s, SetsScores)
    assert s.sets_won is None and s.sets == {} and not s.match_tiebreak


# --- B sınıfı sporlar (plan maddesi SP-3; research/all_sports örnekleri) ----------------------------------

def test_baseball_innings_hits_and_errors():
    """MLB: `innings.inningN.run` ile `periodN` aynı değerler; isabet ve hata toplamı `inningsBaseball`'da."""
    s = extract_scores(_load("baseball/S1_innings_and_periods__16288374"), "baseball")
    assert isinstance(s, InningsScores)
    assert sorted(s.innings) == list(range(1, 10))
    assert s.innings[9] == Pair(0, 4) and s.innings[1] == Pair(0, 1)
    assert s.regulation == Pair(0, 6) and s.extra_innings is None
    assert (s.hits, s.errors) == (Pair(4, 9), Pair(1, 2))
    assert sum(p.away for p in s.innings.values()) == 6  # inning'lerin toplamı başlık skoru


def test_baseball_extra_innings_and_trimmed_innings_fall_back_to_periods():
    """
    Araştırma kaydı `innings`'i iki girdiye kırptı (`__trimmed_keys__`); eksik inning'ler `periodN`'den okunur.
    Kod 110 "AET" uzatma inning'leridir: `overtime` yalnızca uzatmada atılan sayı.
    """
    event = _load("baseball/A_finished-110-aet__9861550")
    assert "__trimmed_keys__" in event["homeScore"]["innings"]
    s = extract_scores(event, "baseball")
    assert sorted(s.innings) == list(range(1, 8))
    assert s.innings[6] == Pair(1, 2)
    assert s.regulation == Pair(3, 3) and s.extra_innings == Pair(3, 2)
    assert s.hits == Pair(10, 10) and s.errors is None  # errors da kırpılmış
    assert s.status_class is StatusClass.COMPLETED


def test_baseball_innings_win_over_periods():
    event = {"status": {"type": "inprogress", "code": 28},
             "homeScore": {"current": 5, "period1": 9, "innings": {"inning1": {"run": 5}, "inning2": {"run": None}}},
             "awayScore": {"current": 0, "period2": 0, "innings": {"inning1": {"run": 0}}}}
    s = extract_scores(event, "baseball")
    assert s.innings == {1: Pair(5, 0), 2: Pair(None, 0)}


def test_cricket_innings_of_each_side():
    s = extract_scores(_load("cricket/C1_two_innings_each__16894534"), "cricket")
    assert isinstance(s, CricketScores)
    assert s.home_innings == {1: {"runs": 103, "wickets": 10, "overs": 22.3},
                              2: {"runs": 110, "wickets": 9, "overs": 24.2}}
    assert s.away_innings[2] == {"runs": 332, "wickets": 10, "overs": 63.2}
    # SofaScore'un `current` değeri tarafın innings'lerinin toplamı
    event = _load("cricket/C1_two_innings_each__16894534")
    assert event["homeScore"]["current"] == sum(i["runs"] for i in s.home_innings.values())


def test_cricket_end_of_day_keeps_the_score_and_is_live():
    s = extract_scores(_load("cricket/A_willcontinue-141-end-of-day-1__16586046"), "cricket")
    assert s.status_class is StatusClass.LIVE and not s.settleable
    assert s.home_innings == {1: {"runs": 21, "wickets": 2, "overs": 7}}
    assert s.away_innings == {1: {"runs": 237, "wickets": 10, "overs": 68.1}}


@pytest.mark.parametrize("rel,unit,sets_won,sets", [
    ("darts/D1_sets__17099318", "legs", Pair(0, 3), {1: Pair(0, 3), 2: Pair(1, 3), 3: Pair(2, 3)}),
    ("darts/D1_sets__17099319", "legs", Pair(3, 1), {1: Pair(3, 0), 2: Pair(2, 3), 3: Pair(3, 1), 4: Pair(3, 1)}),
    # setsiz maç: `current` kazanılan leg; canlı yükte period1 `current`'ı tekrarlar ve bir set değildir
    ("darts/D2_legs_only__17180772", "legs_won", Pair(4, 2), {}),
    ("darts/A_inprogress-20-started__17225298", "legs_won", Pair(1, 3), {}),
    # tek setlik maç (bestOfSets 1, bestOfLegs 7): `current` kazanılan leg'dir, kazanılan set değil (B3)
    ("darts/D3_single_set__17278936", "legs_won", Pair(1, 4), {}),
])
def test_darts_sets_or_legs_by_best_of_sets(rel, unit, sets_won, sets):
    s = extract_scores(_load(rel), "darts")
    assert isinstance(s, SetsScores)
    assert (s.format, s.sets_won, s.sets, s.tiebreaks, s.match_tiebreak) == (unit, sets_won, sets, {}, False)


@pytest.mark.parametrize("best_of_sets", [None, 0, 1, False, True, "5"])
def test_darts_without_more_than_one_set_counts_legs(best_of_sets):
    event = dict(_load("darts/D1_sets__17099318"), bestOfSets=best_of_sets)
    assert extract_scores(event, "darts").format == "legs_won"


def test_darts_single_set_live_payload_counts_legs_not_one_set():
    """Canlı tek setlik maçta period1 `current`'ı tekrarlar (o setin leg'leri): set listesine girmez."""
    event = dict(_load("darts/A_inprogress-20-started__17225298"), bestOfSets=1)
    s = extract_scores(event, "darts")
    assert (s.format, s.sets_won, s.sets) == ("legs_won", Pair(1, 3), {})
    assert extract_scores(dict(event, bestOfSets=3), "darts").sets == {1: Pair(1, 3)}


@pytest.mark.parametrize("rel,sets_won,games,live", [
    # canlı: periodN oyunu kimin aldığı (1 / 0); o an oynanan ve oynanmamış oyunlar 0-0 gelir, sayılmaz
    ("esports/A_inprogress-30-pause__17200404", Pair(1, 1), {1: Pair(1, 0), 2: Pair(0, 1)}, True),
    ("esports/A_inprogress-1002-second-game__17223320", Pair(1, 0), {1: Pair(1, 0)}, True),
    ("esports/A_inprogress-1001-first-game__17060161", Pair(0, 0), {}, True),
    # bitmiş CS2 serisi: periodN haritanın raunt skoru (esports-games dilimindeki oyunların `display`'i)
    ("esports/E1_finished_map_rounds__17264020", Pair(1, 2), {1: Pair(7, 13), 2: Pair(13, 9), 3: Pair(11, 13)}, False),
])
def test_esports_counts_games_won_and_keeps_each_game(rel, sets_won, games, live):
    event = _load(rel)
    s = extract_scores(event, "esports")
    assert (s.format, s.sets_won, s.sets, s.tiebreaks, s.match_tiebreak) == ("games_won", sets_won, games, {}, False)
    assert (s.status_class is StatusClass.LIVE) == live


def test_esports_game_scores_match_the_games_slice():
    """Bitmiş serinin periodN'i /event/{id}/esports-games dilimindeki oyunların skoruyla aynıdır."""
    root = Path(__file__).resolve().parents[1] / "research" / "all_sports" / "samples" / "esports"
    games = json.loads((root / "event-id-esports-games__2.json").read_text(encoding="utf-8"))["body"]["games"]
    s = extract_scores(_load("esports/E1_finished_map_rounds__17264020"), "esports")
    assert [Pair(g["homeScore"]["display"], g["awayScore"]["display"]) for g in games] == list(s.sets.values())
    assert [g["winnerCode"] for g in games] == [1 if p.home > p.away else 2 for p in s.sets.values()]


@pytest.mark.parametrize("rel,method,final_round,winner", [
    ("mma/M1_unanimous_decision__16822910", "UD", 5, 1),
    ("mma/M2_split_decision__11366500", "SD", 3, 2),
    ("mma/M3_tko__16678840", "TKO", 1, 2),
    ("mma/M4_submission__16875874", "SUB", 3, 1),
    ("mma/A_notstarted-0-not-started__16679037", None, None, None),
])
def test_mma_result_without_a_score(rel, method, final_round, winner):
    event = _load(rel)
    assert event["homeScore"] == event["awayScore"] == {}
    s = extract_scores(event, "mma")
    assert isinstance(s, FightScores)
    assert (s.method, s.final_round, s.winner_code) == (method, final_round, winner)
    assert _values(s) == []


def test_fight_ignores_values_of_the_wrong_type():
    s = extract_scores({"status": {"type": "finished", "code": 100}, "winType": 3, "finalRound": True}, "mma")
    assert (s.method, s.final_round) == (None, None)


def test_class_b_sports_are_no_longer_unsupported(caplog):
    with caplog.at_level("WARNING", logger="sofascore_scraper.status"):
        for path in sorted(FIXTURES.glob("*/*.json")):
            if path.parent.name in ("baseball", "cricket", "esports", "darts", "mma"):
                extract_scores(json.loads(path.read_text(encoding="utf-8")), path.parent.name)
    assert caplog.text == ""


# --- ortak ------------------------------------------------------------------------------

def test_sport_from_event_and_parameter_wins():
    event = dict(_load("football/B6_finished_regular__16837335"))
    event["tournament"] = {"category": {"sport": {"slug": "football"}}}
    assert isinstance(extract_scores(event), FootballScores)
    assert isinstance(extract_scores(event, "basketball"), BasketballScores)


def test_raw_changes_are_carried():
    event = _load("basketball/K6_score_changed_after_finished__17006262")
    s = extract_scores(event, "basketball")
    assert s.raw_change_ts == event["changes"]["changeTimestamp"]
    assert s.raw_changed_fields == event["changes"]["changes"]


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*/*.json")), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_every_fixture_extracts(path):
    s = extract_scores(json.loads(path.read_text(encoding="utf-8")), path.parent.name)
    assert s.sport == path.parent.name
    assert s.settleable == (s.status_class is StatusClass.COMPLETED)
