"""extract_scores: tests/fixtures/status altındaki gerçek SofaScore yanıtları."""
import dataclasses
import json
from pathlib import Path

import pytest

from src.status import (BasketballScores, FootballScores, Pair, PeriodsScores, StatusClass, TennisScores,
                        extract_scores)

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
    assert s.aet == Pair(3, 3)
    assert s.penalties == Pair(7, 6)
    assert s.winner_code == 1
    assert 10 not in _values(s) and 9 not in _values(s)


def test_football_cup_draw_with_aggregate():
    s = extract_scores(_load("football/F6_cup_draw_aggregate__16872361"), "football")
    assert s.winner_code == 3
    assert s.ft90 == Pair(1, 1)
    assert s.aggregated == Pair(1, 2)
    assert s.aggregated_winner_code == 2
    assert s.aet is None  # code 100


def test_football_regular(caplog):
    with caplog.at_level("WARNING", logger="src.status"):
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
    with caplog.at_level("WARNING", logger="src.status"):
        extract_scores(_load("rugby/A_finished-100-ended__16237238"), "rugby")
        extract_scores(_load("rugby/A_finished-100-ended__16237238"), "volleyball")
    assert caplog.text.count("desteklenmeyen spor") == 1 and "'volleyball'" in caplog.text


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
