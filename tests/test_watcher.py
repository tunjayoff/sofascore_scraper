"""MatchWatcher: sahte events/live ve /event yanıtlarıyla (fixture'lardan kurgulanmış); gerçek ağ yok."""
import copy
import datetime as dt
import json
from pathlib import Path

import pytest

from src.watcher import (EVENT_INTERVAL_SLOW_SECONDS, MatchWatcher, STUCK_INTERVAL_SECONDS, WATCH_EVENTS_FILE,
                         near_end, play_start)

FIXTURES = Path(__file__).parent / "fixtures" / "status"


def _fx(rel: str, eid=None) -> dict:
    ev = json.loads((FIXTURES / f"{rel}.json").read_text(encoding="utf-8"))
    ev["id"] = eid if eid is not None else ev["event_id"]
    return ev


def _now_of(rel: str) -> float:
    return dt.datetime.fromisoformat(_fx(rel)["fetched_at_utc"]).timestamp()


class FakeApi:
    """Tur tur yanıtlar: live[i] = o turdaki canlı liste; events[id] = /event yanıtı (değiştirilebilir)."""

    def __init__(self, sport, live_rounds, events):
        self.sport = sport
        self.live_rounds = list(live_rounds)
        self.events = events
        self.round = 0
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if path == f"/sport/{self.sport}/events/live":
            rnd = self.live_rounds[min(self.round, len(self.live_rounds) - 1)]
            self.round += 1
            return {"events": rnd}
        eid = int(path.rsplit("/", 1)[-1])
        ev = self.events.get(eid)
        return {"event": ev} if ev else None


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _watcher(tmp_path, api, clock, **kw):
    return MatchWatcher(api.sport, data_dir=str(tmp_path), fetch_json=api, clock=clock, sleep=clock.sleep, **kw)


def _events(tmp_path):
    p = Path(tmp_path) / WATCH_EVENTS_FILE
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


# --- LIVE → listeden düştü → /event finished --------------------------------------------

def _finish_scenario(tmp_path):
    live = _fx("football/A_inprogress-7-2nd-half__17018572", eid=500)
    done = _fx("football/A_finished-100-ended__17099711", eid=500)
    done["startTimestamp"] = live["startTimestamp"]
    api = FakeApi("football", [[live], [live], []], {500: live})
    clock = Clock(_now_of("football/A_inprogress-7-2nd-half__17018572"))
    return api, clock, live, done


def test_drop_from_live_list_yields_one_provisional_completion(tmp_path):
    api, clock, live, done = _finish_scenario(tmp_path)
    w = _watcher(tmp_path, api, clock, event_ids=[500])
    w.start()  # başlangıç durumu: LIVE, olay yok
    w.tick()  # tur 1: listede
    w.tick()  # tur 2: listede, bitişe yakın → maç sayfası da okunur (hâlâ canlı)
    api.events[500] = done
    w.tick()  # tur 3: listeden düştü → /event: finished

    evs = _events(tmp_path)
    assert [e["type"] for e in evs] == ["status_changed"]
    ev = evs[0]
    assert (ev["event_id"], ev["from"], ev["to"], ev["provisional"]) == (500, "live", "completed", True)
    assert ev["change_ts"] == done["changes"]["changeTimestamp"]
    assert ev["scores"]["ft90"] == [done["homeScore"]["normaltime"], done["awayScore"]["normaltime"]]
    assert w.active_ids() == []


def test_restart_from_state_does_not_repeat_the_event(tmp_path):
    api, clock, live, done = _finish_scenario(tmp_path)
    w = _watcher(tmp_path, api, clock, event_ids=[500])
    w.start()
    w.tick()
    # yeniden başlatma: son bilinen sınıf (LIVE) dosyadan
    w2 = _watcher(tmp_path, api, clock, event_ids=[500])
    assert w2.state["500"]["class"] == "live"
    w2.start()  # state'te olduğu için /event başlangıç okuması yok
    api.events[500] = done
    api.live_rounds = [[]]
    w2.tick()
    assert [e["type"] for e in _events(tmp_path)] == ["status_changed"]
    # bir kez daha yeniden başlat ve tur at: olay tekrar üretilmez
    w3 = _watcher(tmp_path, api, clock, event_ids=[500])
    w3.start()
    w3.tick()
    assert len(_events(tmp_path)) == 1


def test_run_stops_when_all_event_ids_are_done(tmp_path):
    api, clock, live, done = _finish_scenario(tmp_path)
    api.events[500] = done
    api.live_rounds = [[]]
    w = _watcher(tmp_path, api, clock, event_ids=[500])
    w.start()  # doğrudan finished: olay yok (önceki sınıf bilinmiyor)
    w.run()
    assert _events(tmp_path) == []


# --- hız bütçesi --------------------------------------------------------------------------

def test_rate_budget_slows_event_pages_and_warns(tmp_path, caplog):
    base = _fx("football/A_inprogress-7-2nd-half__17018572")
    matches = []
    for i in range(60):
        ev = copy.deepcopy(base)
        ev["id"] = 1000 + i
        ev["tournament"] = {"uniqueTournament": {"id": 17}}
        matches.append(ev)
    api = FakeApi("football", [matches], {m["id"]: m for m in matches})
    clock = Clock(_now_of("football/A_inprogress-7-2nd-half__17018572"))
    w = _watcher(tmp_path, api, clock, league_ids=[17])
    per_tick = []
    with caplog.at_level("WARNING", logger="src.watcher"):
        for _ in range(3):
            before = len(api.calls)
            w.tick()
            per_tick.append(sum(1 for c in api.calls[before:] if c.startswith("/event/")))
    assert w.event_interval == EVENT_INTERVAL_SLOW_SECONDS
    assert "hız bütçesi" in caplog.text
    assert all(n <= 20 for n in per_tick) and sum(per_tick) > 0  # turda en fazla WATCH_MAX_EVENT_POLLS
    # liste 2/dk + maç sayfaları: 30 sn'lik turlarda en fazla 20 → dakikada ≤ 42 istek (< 1/sn)
    assert max(per_tick) * 2 + 2 <= 42


def test_requests_are_spaced_at_least_one_second(tmp_path):
    api, clock, live, done = _finish_scenario(tmp_path)
    stamps = []
    orig = api.__call__

    def spy(path):
        stamps.append(clock())
        return orig(path)

    w = MatchWatcher("football", event_ids=[500], data_dir=str(tmp_path), fetch_json=spy, clock=clock, sleep=clock.sleep)
    w.start()
    w.tick()
    w.tick()
    assert all(b - a >= 1.0 for a, b in zip(stamps, stamps[1:], strict=False))


# --- bitişe yakın eşiği ---------------------------------------------------------------------

@pytest.mark.parametrize("rel,sport,expected", [
    ("football/A_inprogress-7-2nd-half__17018572", "football", True),  # 2. yarı, ~83. dk
    ("football/A_inprogress-7-2nd-half__17184988", "football", None),
    ("football/A_inprogress-6-1st-half__17018554", "football", False),
    ("football/A_inprogress-31-halftime__17018588", "football", False),
    ("basketball/A_inprogress-16-4th-quarter__17203938", "basketball", True),  # played 2249/2400
    ("basketball/A_inprogress-15-3rd-quarter__17157547", "basketball", False),
    ("basketball/A_inprogress-13-1st-quarter__17203938", "basketball", False),
    ("tennis/A_inprogress-10-3rd-set__17202152", "tennis", True),
    ("tennis/A_inprogress-9-2nd-set__17208186", "tennis", False),
    ("tennis/A_inprogress-8-1st-set__17194803", "tennis", False),
])
def test_near_end_thresholds(rel, sport, expected):
    ev = _fx(rel)
    now = _now_of(rel)
    got = near_end(ev, sport, now)
    if expected is None:  # dakikaya göre: fixture'daki time bloğundan hesaplanan
        t = ev["time"]
        minute = ((t.get("initial") or 2700) + now - t["currentPeriodStartTimestamp"]) / 60
        expected = minute >= 80 or t.get("injuryTime2") is not None
    assert got is expected


def test_football_injury_time_two_is_near_end():
    ev = _fx("football/A_inprogress-7-2nd-half__17018572")
    ev["time"]["injuryTime2"] = 4
    assert near_end(ev, "football", ev["time"]["currentPeriodStartTimestamp"] + 60)


# --- takılı maç, VOID, canlı skor --------------------------------------------------------------

def test_stuck_match_is_reported_once_and_polled_every_five_minutes(tmp_path):
    ns = _fx("football/A_notstarted-0-not-started__17184998", eid=700)
    api = FakeApi("football", [[]], {700: ns})
    clock = Clock(ns["startTimestamp"] + 5 * 3600)
    w = _watcher(tmp_path, api, clock, event_ids=[700])
    w.start()
    evs = _events(tmp_path)
    assert [e["type"] for e in evs] == ["stuck"]
    before = len(api.calls)
    w.tick()  # son okumadan 5 dk geçmedi: yalnızca liste
    assert api.calls[before:] == ["/sport/football/events/live"]
    clock.t += STUCK_INTERVAL_SECONDS
    w.tick()
    assert "/event/700" in api.calls[before + 1:]
    assert [e["type"] for e in _events(tmp_path)] == ["stuck"]


def test_tennis_play_start_from_set_durations():
    ev = _fx("tennis/A_inprogress-10-3rd-set__17202152")
    t = ev["time"]
    assert play_start(ev) == t["currentPeriodStartTimestamp"] - t["period1"] - t["period2"]
    assert play_start(_fx("tennis/A_notstarted-0-not-started__17204702")) is None


@pytest.mark.parametrize("hours_since_play_start,expected", [(5, []), (7, ["stuck"])])
def test_tennis_stuck_uses_real_play_start_and_six_hours(tmp_path, hours_since_play_start, expected):
    ev = _fx("tennis/A_inprogress-10-3rd-set__17202152", eid=810)
    now = play_start(ev) + hours_since_play_start * 3600
    ev["startTimestamp"] = int(now - 9 * 3600)  # planlanan saat çok daha erken: ölçü değil
    api = FakeApi("tennis", [[ev]], {810: ev})
    w = _watcher(tmp_path, api, Clock(now), event_ids=[810])
    w.start()
    assert [e["type"] for e in _events(tmp_path)] == expected


def test_tennis_without_time_falls_back_to_start_timestamp(tmp_path):
    ns = _fx("tennis/A_notstarted-0-not-started__17204702", eid=820)
    api = FakeApi("tennis", [[]], {820: ns})
    w = _watcher(tmp_path, api, Clock(ns["startTimestamp"] + 5 * 3600), event_ids=[820])
    w.start()
    assert _events(tmp_path) == []  # 6 sa dolmadı
    w2 = _watcher(tmp_path / "b", api, Clock(ns["startTimestamp"] + 6.5 * 3600), event_ids=[820])
    w2.start()
    assert [e["type"] for e in _events(tmp_path / "b")] == ["stuck"]


def test_void_with_moved_start_stays_tracked(tmp_path):
    live = _fx("tennis/A_inprogress-9-2nd-set__17208186", eid=800)
    susp = _fx("tennis/B9_suspended__17208583", eid=800)
    clock = Clock(_now_of("tennis/A_inprogress-9-2nd-set__17208186"))
    susp["startTimestamp"] = int(clock.t + 20 * 3600)  # ertesi güne taşındı (B13)
    api = FakeApi("tennis", [[live], []], {800: live})
    w = _watcher(tmp_path, api, clock, event_ids=[800])
    w.start()
    api.events[800] = susp
    w.tick()
    w.tick()  # listeden düştü → /event: suspended, başlangıç ileri
    ev = _events(tmp_path)[-1]
    assert (ev["type"], ev["from"], ev["to"]) == ("status_changed", "live", "void")
    assert w.active_ids() == ["800"]
    assert w.state["800"]["start_ts"] == susp["startTimestamp"]


def test_live_score_change_event(tmp_path):
    live = _fx("basketball/A_inprogress-15-3rd-quarter__17157547", eid=900)
    later = copy.deepcopy(live)
    later["homeScore"]["display"] = (later["homeScore"].get("display") or 0) + 2
    api = FakeApi("basketball", [[live], [later]], {900: live})
    clock = Clock(_now_of("basketball/A_inprogress-15-3rd-quarter__17157547"))
    w = _watcher(tmp_path, api, clock, event_ids=[900])
    w.start()
    w.tick()
    w.tick()
    evs = _events(tmp_path)
    assert [e["type"] for e in evs] == ["score_changed"]
    assert evs[0]["to"][0] == evs[0]["from"][0] + 2


def test_callback_receives_events(tmp_path):
    api, clock, live, done = _finish_scenario(tmp_path)
    got = []
    w = _watcher(tmp_path, api, clock, event_ids=[500], on_event=got.append)
    w.start()
    api.events[500] = done
    api.live_rounds = [[]]
    w.tick()
    assert got == _events(tmp_path)


def test_requires_ids():
    with pytest.raises(ValueError):
        MatchWatcher("football")
