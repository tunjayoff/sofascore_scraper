"""
Takip başına kapsam ve oyuncu takibinin maçları (B2; uçtan uca test bulgusu F23, docs/design/05-web-ui.md G40):

  * oyuncu takibinin eşitlemesi listesinin maç kimliklerini saklar (`store.runtime`, `follow_sync.remember_listing`):
    tam okunan liste öncekinin yerine geçer, yarım kalan öncekiyle birleşir;
  * `/api/v1/status` `summary.follows[]`: her takibin saklanan, bitmiş ve bitmiş olup ayrıntısı saklanan maçları ve
    kapsamı (turnuva, takım, oyuncu, tek maç; FX-26'nın ortak kuralı); listesi henüz okunmamış oyuncu `counted`
    yanlış;
  * `GET /api/v1/events?follow=player:7`: oyuncunun maçları; öteki süzgeçlerle VE.

Ağ yok: FX-19'un sahte dünyası (tests/test_fx19_follow_sync.py; oyuncu 7'nin listesinde 9100002 ve 9100003).
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi.testclient import TestClient

from fakes.sofascore import FakeSofaScore
from sofascore_scraper.services import follow_sync
from sofascore_scraper.services.sync import FollowsSyncSpec
from sofascore_scraper.store import Store
from sofascore_scraper.web.app import app

import test_fx19_follow_sync as fx19

PLAYER, TEAM, follow, run = fx19.PLAYER, fx19.TEAM, fx19.follow, fx19.run
_settings, fake, store = fx19._settings, fx19.fake, fx19.store
client = TestClient(app)


def follows_summary() -> Dict[str, Dict[str, Any]]:
    response = client.get("/api/v1/status")
    assert response.status_code == 200, response.text
    return {row["follow_id"]: row for row in response.json()["data"]["summary"]["follows"]}


def test_a_player_sync_keeps_the_events_of_its_list(store: Store, fake: FakeSofaScore) -> None:
    follow(store, "player", PLAYER, "A player")
    assert follow_sync.listed_events(store, "player", PLAYER) is None
    before = follows_summary()["player:7"]
    assert (before["counted"], before["events"], before["coverage"]) == (False, 0, 0.0)

    run(store, FollowsSyncSpec(mode="full", follows=("player:7",)))

    assert follow_sync.listed_events(store, "player", PLAYER) == (9100002, 9100003)
    after = follows_summary()["player:7"]
    assert after == {"follow_id": "player:7", "kind": "player", "entity_id": PLAYER, "events": 2, "finished": 2,
                     "finished_details": 2, "coverage": 100.0, "counted": True}


def test_a_partial_list_is_added_to_the_previous_one(store: Store) -> None:
    row = follow(store, "player", PLAYER, "A player")
    whole = follow_sync.FollowListing(row, events=[follow_sync.ListedEvent(1, "completed"),
                                                   follow_sync.ListedEvent(2, "completed")])
    follow_sync.remember_listing(store, whole)
    cut = follow_sync.FollowListing(row, events=[follow_sync.ListedEvent(3, "completed")], failed="5xx")
    follow_sync.remember_listing(store, cut)
    assert follow_sync.listed_events(store, "player", PLAYER) == (3, 1, 2)
    stopped = follow_sync.FollowListing(row, events=[follow_sync.ListedEvent(4, "completed")])
    follow_sync.remember_listing(store, stopped, complete=False)  # durdurulan iş
    assert follow_sync.listed_events(store, "player", PLAYER) == (4, 3, 1, 2)
    follow_sync.remember_listing(store, whole)  # tam liste öncekinin yerine geçer
    assert follow_sync.listed_events(store, "player", PLAYER) == (1, 2)
    # Takım listesi saklanmaz: takımın maçları kataloğun katılımcılarından sayılır
    team = follow(store, "team", TEAM, "Team 42")
    follow_sync.remember_listing(store, follow_sync.FollowListing(team, events=whole.events))
    assert follow_sync.listed_events(store, "team", TEAM) is None


def test_every_kind_of_follow_is_counted_by_the_same_rule(store: Store, fake: FakeSofaScore) -> None:
    """
    FX-26'nın kuralı: yalnızca bitmiş maçlar sayılır. Takımın dört maçının ikisi bitmiş (9100004 başlamadı, 9300001
    oynanıyor); tek maç takibi kendisidir; lig takibinin sayıları özetin satırındandır.
    """
    follow(store, "team", TEAM, "Team 42")
    follow(store, "event", 9100001, "A match")
    follow(store, "event", 9999999, "Not stored")
    follow(store, "tournament", 17, "Premier League")
    run(store, FollowsSyncSpec(mode="full", follows=("team:42",)))

    rows = follows_summary()
    team = rows["team:42"]
    assert (team["events"], team["finished"], team["finished_details"], team["coverage"]) == (4, 2, 2, 100.0)
    assert (rows["event:9100001"]["finished"], rows["event:9100001"]["coverage"]) == (1, 100.0)
    assert (rows["event:9999999"]["events"], rows["event:9999999"]["counted"]) == (0, True)
    league = rows["tournament:17"]
    summary = client.get("/api/v1/status").json()["data"]["summary"]
    row17 = next(t for t in summary["tournaments"] if t["tournament_id"] == 17)
    assert (league["events"], league["finished"], league["finished_details"]) == (
        row17["events"], row17["finished"], row17["finished_details"])
    # Takip listesinin sırası
    assert list(rows) == [f"{r.kind}:{r.entity_id}" for r in store.follows.list()]


def test_the_events_of_a_follow(store: Store, fake: FakeSofaScore) -> None:
    follow(store, "player", PLAYER, "A player")
    follow(store, "team", TEAM, "Team 42")
    run(store, FollowsSyncSpec(mode="full", follows=("player:7", "team:42")))

    def ids(**params: Any) -> list:
        response = client.get("/api/v1/events", params=params)
        assert response.status_code == 200, response.text
        return sorted(row["id"] for row in response.json()["data"])

    assert ids(follow="player:7") == [9100002, 9100003]
    assert ids(follow="player:8") == []  # listesi okunmamış oyuncu
    assert ids(follow="team:42") == [9100001, 9100004, 9100010, 9300001]
    assert ids(follow="team:42", status="completed") == [9100001, 9100010]
    assert ids(follow="event:9100003") == [9100003]
    # Süzgeçler VE ile birleşir
    assert ids(follow="team:42", participant=44) == []
    assert ids(follow="tournament:17", tournament=17) == ids(tournament=17)
    assert ids(follow="tournament:17", tournament=8) == []
    bad = client.get("/api/v1/events", params={"follow": "club:1"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_request"
