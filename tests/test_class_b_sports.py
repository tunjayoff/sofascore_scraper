"""
B sınıfı sporlar (plan maddesi SP-3): beyzbol, kriket, e-spor, dart ve MMA uçtan uca, Store'dan şemaya.

  * E-sporun `/event/{id}/esports-games` yanıtı maçın bir dilimidir: içindeki oyun nesneleri (kendi `id`,
    `status`, `winnerCode` ve skorlarıyla) maç olarak dizinlenmez.
  * Dilim yalnızca e-sporda ve maç başlayınca istenir.
  * Kriketin gün sonu (`willcontinue`) katalogda canlıdır; maçın kesinliği açık kalır.

Yükler research/all_sports'tan alınmış gerçek yanıtlardır (tests/golden/schema/inputs, tests/fixtures/status).
Ağ yok.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict

from sofascore_scraper import schema, sports
from sofascore_scraper.slices import SLICE_OK, Outcome
from sofascore_scraper.store import EventQuery, Store, open_store
from sofascore_scraper.store.derive import event_row

INPUTS = Path(__file__).parent / "golden" / "schema" / "inputs"
FIXTURES = Path(__file__).parent / "fixtures" / "status"
T0 = dt.datetime(2026, 10, 1, 12, 57, 6, tzinfo=dt.timezone.utc)

# research/all_sports/samples/esports/event-id-esports-games__1.json (aynı maçın, 17223320, oyunları)
GAMES: Dict[str, Any] = {"games": [
    {"length": 2230, "status": {"code": 100, "description": "Ended", "type": "finished"}, "winnerCode": 1,
     "map": {"name": "Ancient", "id": 23}, "homeScore": {"display": 13, "period1": 11, "period2": 2},
     "awayScore": {"display": 7, "period1": 1, "period2": 6}, "homeTeamStartingSide": 5,
     "hasCompleteStatistics": True, "id": 588243, "startTimestamp": 1790853309},
    {"status": {"code": 20, "description": "Started", "type": "inprogress"}, "winnerCode": 0,
     "map": {"name": "Mirage", "id": 20}, "homeScore": {}, "awayScore": {}, "hasCompleteStatistics": False,
     "id": 588244, "startTimestamp": 1790856306},
]}


def _input(name: str) -> Dict[str, Any]:
    return json.loads((INPUTS / name).read_text(encoding="utf-8"))["event"]


def _consistent(store: Store) -> None:
    assert store.catalog.diff_from_rebuild() == []
    assert store.catalog.verify(deep=True).ok


def test_esports_games_are_a_slice_of_the_match_not_events(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    match = _input("esports__L_second_game__17223320.json")

    store.events.put(match["id"], {"event": Outcome(SLICE_OK, match, fetched_at=T0),
                                   "esports_games": Outcome(SLICE_OK, GAMES, fetched_at=T0)})

    assert [row.id for row in store.events.iter(EventQuery())] == [17223320]
    assert store.events.get(588243) is None and store.events.get(588244) is None
    assert store.events.payload(17223320, "esports_games") == GAMES
    info = store.events.slice(17223320, "esports_games")
    assert (info.state, info.has_payload) == ("ok", True)
    record = schema.event_from_row(store.events.get(17223320)).to_dict()
    assert record["sport"] == "esports" and record["status"]["class"] == "live"
    # canlı yükte periodN oyunu kimin aldığıdır (1 / 0); süren 2. oyun 0-0 gelir ve listede yoktur (B3)
    assert record["score"] == {"family": "sets", "home": 1, "away": 0, "format": "games_won",
                               "sets_won": {"home": 1, "away": 0},
                               "sets": [{"number": 1, "home": 1, "away": 0, "tiebreak": None}],
                               "match_tiebreak": False}
    assert schema.slice_from_info(info).to_dict()["key"] == "esports_games"
    _consistent(store)


def test_esports_games_slice_is_requested_only_for_esports_once_the_match_started() -> None:
    def keys(sport: str, phase: str) -> tuple:
        return tuple(s.key for s in sports.select_slices("event", sport, phase=phase))

    assert "esports_games" not in keys("esports", "pre")
    assert keys("esports", "live")[-1] == keys("esports", "post")[-1] == "esports_games"
    assert all("esports_games" not in keys(sport, "post") for sport in sports.sport_slugs() if sport != "esports")
    # FX-31: bitmiş ve canlı maçta veriyle geldi, tamlık hesabına girer (tests/test_sport_slices.py); spora özel
    # olduğu için eski maç detayı yanıtında yoktur
    assert "esports_games" in {s.key for s in sports.slices_for("esports", required_only=True)}
    assert sports.get_slice("esports_games").sports == frozenset({"esports"})


def test_cricket_end_of_day_is_live_and_open_in_the_catalog(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    payload = json.loads((FIXTURES / "cricket" / "A_willcontinue-141-end-of-day-1__16586046.json").read_text(
        encoding="utf-8"))
    payload = {k: v for k, v in payload.items() if k not in ("case_id", "event_id", "fetched_at_utc", "source_file")}
    payload.update(id=16586046, tournament={"name": "Irani Cup", "category": {
        "id": 1, "name": "India", "slug": "india", "sport": {"name": "Cricket", "slug": "cricket", "id": 62}},
        "uniqueTournament": {"id": 19260, "name": "Irani Trophy", "slug": "irani-trophy"}})

    store.events.put(16586046, {"event": Outcome(SLICE_OK, payload, fetched_at=T0)})

    row = store.events.get(16586046)
    assert (row.sport, row.status_type, row.status_class) == ("cricket", "willcontinue", "live")
    record = schema.event_from_row(row).to_dict()
    assert record["quality"]["settlement"] == "open"
    assert [(i["side"], i["runs"], i["wickets"], i["overs"]) for i in record["score"]["innings"]] == [
        ("home", 21, 2, 7), ("away", 237, 10, 68.1)]
    assert event_row(payload, "event", T0.timestamp())["status_class"] == "live"
    _consistent(store)


def test_darts_and_mma_sides_are_persons(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    for name in ("darts__D1_sets__17099318.json", "mma__M1_unanimous_decision__16822910.json"):
        event = _input(name)
        store.events.put(event["id"], {"event": Outcome(SLICE_OK, event, fetched_at=T0)})
    people = {row.sport: row.type for row in store.entities.participants()}
    assert people == {"darts": 1, "mma": 1}
    assert {schema.participant_from_row(row).type for row in store.entities.participants()} == {"player"}
    mma = schema.event_from_row(store.events.get(16822910)).to_dict()
    assert (mma["winner"], mma["score"]["method"], mma["score"]["final_round"]) == ("home", "UD", 5)
    _consistent(store)
