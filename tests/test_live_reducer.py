"""
Canlı indirgeyicinin goldenları (plan maddesi P23; docs/design/02-services.md bölüm 8.1).

Senaryolar tests/test_watcher.py'deki durumlardan ve iki boşluk durumundan (ara kareler görülmeden
canlı → bitti; birden çok adım sıçrayan skor) kurulur. Her adım bir gözlemdir: (maç nesnesi, hangi istek
gösterdi, an). Golden her adımda üretilen 2.x olaylarını ve adımdan sonraki maç durumunu tutar.

Golden önce bugünkü izleyiciyle (`MatchWatcher._observe`) yazıldı; indirgeyici ondan çıkarıldığında aynı
golden ona karşı da koşar.

Yeniden üretmek: `REGEN_LIVE_GOLDENS=1 python -m pytest tests/test_live_reducer.py` (fark gözden geçirilmeden
commit edilmez: golden değiştiyse davranış değişmiştir).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "status"
GOLDEN = Path(__file__).parent / "golden" / "live" / "reducer.json"
REGEN_ENV = "REGEN_LIVE_GOLDENS"
WINDOW_HOURS = "48"

Step = Tuple[Dict[str, Any], str, float]  # (maç nesnesi, "live" | "event", an)


def fx(rel: str, eid: int) -> Dict[str, Any]:
    event = json.loads((FIXTURES / f"{rel}.json").read_text(encoding="utf-8"))
    event["id"] = eid
    return event


def fetched(rel: str) -> float:
    return dt.datetime.fromisoformat(fx(rel, 0)["fetched_at_utc"]).timestamp()


FB_LIVE = "football/A_inprogress-7-2nd-half__17018572"
FB_FIRST_HALF = "football/A_inprogress-6-1st-half__17018554"
FB_HALFTIME = "football/A_inprogress-31-halftime__17018588"
FB_DONE = "football/A_finished-100-ended__17099711"
FB_NOT_STARTED = "football/A_notstarted-0-not-started__17184998"
BB_LIVE = "basketball/A_inprogress-15-3rd-quarter__17157547"
TN_LIVE = "tennis/A_inprogress-9-2nd-set__17208186"
TN_LATE = "tennis/A_inprogress-10-3rd-set__17202152"
TN_SUSPENDED = "tennis/B9_suspended__17208583"
TN_NOT_STARTED = "tennis/A_notstarted-0-not-started__17204702"


def _finish_after_drop() -> Tuple[str, List[Step]]:
    live = fx(FB_LIVE, 500)
    done = fx(FB_DONE, 500)
    done["startTimestamp"] = live["startTimestamp"]
    t0 = fetched(FB_LIVE)
    return "football", [(live, "event", t0), (live, "live", t0 + 30), (live, "live", t0 + 60),
                        (done, "event", t0 + 90)]


def _completed_seen_twice() -> Tuple[str, List[Step]]:
    sport, steps = _finish_after_drop()
    done, _, at = steps[-1]
    return sport, [*steps, (done, "event", at + 30)]


def _completed_after_the_window() -> Tuple[str, List[Step]]:
    live = fx(FB_LIVE, 501)
    done = fx(FB_DONE, 501)
    done["startTimestamp"] = live["startTimestamp"]
    late = live["startTimestamp"] + 49 * 3600
    return "football", [(live, "live", late - 60), (done, "event", late)]


def _gap_live_to_completed() -> Tuple[str, List[Step]]:
    """Boşluk: birinci yarıda görülen maç bir sonraki gözlemde bitmiş ve skor birden çok adım ilerlemiş."""
    first = fx(FB_FIRST_HALF, 510)
    done = fx(FB_DONE, 510)
    done["startTimestamp"] = first["startTimestamp"]
    done["homeScore"]["display"] = done["homeScore"]["current"] = (first["homeScore"].get("current") or 0) + 3
    t0 = fetched(FB_FIRST_HALF)
    return "football", [(first, "live", t0), (done, "event", t0 + 3 * 3600)]


def _gap_not_started_to_completed() -> Tuple[str, List[Step]]:
    """Boşluk: başlamamış görülen maç bir sonraki gözlemde bitmiş (canlı hali hiç görülmedi)."""
    not_started = fx(FB_NOT_STARTED, 520)
    done = fx(FB_DONE, 520)
    done["startTimestamp"] = not_started["startTimestamp"]
    start = not_started["startTimestamp"]
    return "football", [(not_started, "event", start - 600), (done, "event", start + 2 * 3600)]


def _halftime_and_goal() -> Tuple[str, List[Step]]:
    first = fx(FB_FIRST_HALF, 530)
    halftime = fx(FB_HALFTIME, 530)
    second = fx(FB_LIVE, 530)
    for later in (halftime, second):
        later["startTimestamp"] = first["startTimestamp"]
    goal = copy.deepcopy(second)
    goal["homeScore"]["display"] = goal["homeScore"]["current"] = (second["homeScore"].get("current") or 0) + 1
    goal["changes"]["changeTimestamp"] = (second["changes"].get("changeTimestamp") or 0) + 60
    t0 = fetched(FB_FIRST_HALF)
    return "football", [(first, "live", t0), (halftime, "live", t0 + 1800), (second, "live", t0 + 3600),
                        (goal, "live", t0 + 3630)]


def _basketball_score_change() -> Tuple[str, List[Step]]:
    live = fx(BB_LIVE, 900)
    later = copy.deepcopy(live)
    later["homeScore"]["display"] = (later["homeScore"].get("display") or 0) + 2
    t0 = fetched(BB_LIVE)
    return "basketball", [(live, "event", t0), (live, "live", t0 + 30), (later, "live", t0 + 60)]


def _basketball_score_jump() -> Tuple[str, List[Step]]:
    """Boşluk: iki gözlem arasında birden çok sayı: tek bir skor olayı, aradaki adımlar olmadan."""
    live = fx(BB_LIVE, 901)
    later = copy.deepcopy(live)
    later["homeScore"]["display"] = (later["homeScore"].get("display") or 0) + 7
    later["awayScore"]["display"] = (later["awayScore"].get("display") or 0) + 5
    t0 = fetched(BB_LIVE)
    return "basketball", [(live, "live", t0), (later, "live", t0 + 300)]


def _stuck_not_started() -> Tuple[str, List[Step]]:
    ns = fx(FB_NOT_STARTED, 700)
    start = ns["startTimestamp"]
    return "football", [(ns, "event", start + 5 * 3600), (ns, "event", start + 5 * 3600 + 300)]


def _stuck_cleared_by_a_new_start() -> Tuple[str, List[Step]]:
    ns = fx(FB_NOT_STARTED, 701)
    start = ns["startTimestamp"]
    moved = copy.deepcopy(ns)
    moved["startTimestamp"] = start + 24 * 3600
    return "football", [(ns, "event", start + 5 * 3600), (moved, "event", start + 5 * 3600 + 300)]


def _tennis_stuck_from_play_start() -> Tuple[str, List[Step]]:
    from src.watcher import play_start

    ev = fx(TN_LATE, 810)
    begin = play_start(ev)
    assert begin is not None
    ev["startTimestamp"] = int(begin - 2 * 3600)
    return "tennis", [(ev, "event", begin + 5 * 3600), (ev, "live", begin + 7 * 3600)]


def _tennis_without_time() -> Tuple[str, List[Step]]:
    ns = fx(TN_NOT_STARTED, 820)
    start = ns["startTimestamp"]
    return "tennis", [(ns, "event", start + 5 * 3600), (ns, "event", start + 6.5 * 3600)]


def _void_with_a_moved_start() -> Tuple[str, List[Step]]:
    live = fx(TN_LIVE, 800)
    suspended = fx(TN_SUSPENDED, 800)
    t0 = fetched(TN_LIVE)
    suspended["startTimestamp"] = int(t0 + 20 * 3600)
    return "tennis", [(live, "event", t0), (live, "live", t0 + 30), (suspended, "event", t0 + 60)]


def _void_without_a_new_start() -> Tuple[str, List[Step]]:
    live = fx(TN_LIVE, 802)
    suspended = fx(TN_SUSPENDED, 802)
    t0 = fetched(TN_LIVE)
    suspended["startTimestamp"] = live["startTimestamp"]
    return "tennis", [(live, "live", t0), (suspended, "event", t0 + 60)]


SCENARIOS: Dict[str, Callable[[], Tuple[str, List[Step]]]] = {
    "finish_after_drop": _finish_after_drop,
    "completed_seen_twice": _completed_seen_twice,
    "completed_after_the_window": _completed_after_the_window,
    "gap_live_to_completed": _gap_live_to_completed,
    "gap_not_started_to_completed": _gap_not_started_to_completed,
    "halftime_and_goal": _halftime_and_goal,
    "basketball_score_change": _basketball_score_change,
    "basketball_score_jump": _basketball_score_jump,
    "stuck_not_started": _stuck_not_started,
    "stuck_cleared_by_a_new_start": _stuck_cleared_by_a_new_start,
    "tennis_stuck_from_play_start": _tennis_stuck_from_play_start,
    "tennis_without_time": _tennis_without_time,
    "void_with_a_moved_start": _void_with_a_moved_start,
    "void_without_a_new_start": _void_without_a_new_start,
}


def plain(value: Any) -> Any:
    """JSON'a yazılıp okunmuş hali (Pair → liste, enum → metin): goldenla aynı biçim."""
    return json.loads(json.dumps(value, ensure_ascii=False))


def run_watcher(tmp_path: Path, sport: str, steps: List[Step]) -> List[Dict[str, Any]]:
    """Bugünkü izleyiciyle: her adımda `_observe`, üretilen olaylar geri çağrıdan."""
    from src.watcher import MatchWatcher

    now = [steps[0][2]]
    emitted: List[Dict[str, Any]] = []
    eid = steps[0][0]["id"]
    watcher = MatchWatcher(sport, event_ids=[eid], data_dir=str(tmp_path), fetch_json=lambda path: None,
                           clock=lambda: now[0], sleep=lambda s: None, on_event=emitted.append)
    out = []
    for event, via, at in steps:
        now[0] = at
        before = len(emitted)
        watcher._observe(copy.deepcopy(event), via)
        out.append({"events": plain(emitted[before:]), "state": plain(watcher.state[str(eid)])})
    return out


def scenario_results(tmp_path: Path, runner: Callable[[Path, str, List[Step]], List[Dict[str, Any]]]
                     ) -> Dict[str, Any]:
    results = {}
    for name, build in SCENARIOS.items():
        sport, steps = build()
        results[name] = {"sport": sport, "steps": runner(tmp_path / name, sport, steps)}
    return results


@pytest.fixture
def fixed_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFRESH_WINDOW_HOURS", WINDOW_HOURS)


def check_golden(actual: Dict[str, Any]) -> None:
    text = json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if os.environ.get(REGEN_ENV):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8", newline="\n")
        return
    assert GOLDEN.exists(), f"golden missing: {GOLDEN} (run with {REGEN_ENV}=1 and review the result)"
    assert json.loads(text) == json.loads(GOLDEN.read_text(encoding="utf-8")), \
        f"{GOLDEN.name} differs; if the change is intended, regenerate with {REGEN_ENV}=1"


def test_the_watcher_matches_the_golden(tmp_path: Path, fixed_window: None) -> None:
    check_golden(scenario_results(tmp_path, run_watcher))


def test_every_scenario_emits_what_its_name_says(tmp_path: Path, fixed_window: None) -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    def types(name: str) -> List[List[str]]:
        return [[event["type"] for event in step["events"]] for step in golden[name]["steps"]]

    assert types("finish_after_drop") == [[], [], [], ["status_changed"]]
    assert types("completed_seen_twice")[-1] == []  # aynı sonuç ikinci kez olay değil
    assert golden["completed_after_the_window"]["steps"][1]["events"][0]["provisional"] is False
    assert golden["finish_after_drop"]["steps"][3]["events"][0]["provisional"] is True
    gap = golden["gap_live_to_completed"]["steps"][1]["events"]
    assert [(e["type"], e["from"], e["to"]) for e in gap] == [("status_changed", "live", "completed")]
    assert [(e["type"], e["from"], e["to"]) for e in golden["gap_not_started_to_completed"]["steps"][1]["events"]] \
        == [("status_changed", "not_started", "completed")]
    jump = golden["basketball_score_jump"]["steps"][1]["events"]
    assert len(jump) == 1 and jump[0]["type"] == "score_changed"
    assert [jump[0]["to"][i] - jump[0]["from"][i] for i in (0, 1)] == [7, 5]
    assert types("stuck_not_started") == [["stuck"], []]
    assert types("tennis_stuck_from_play_start") == [[], ["stuck"]]
    assert types("tennis_without_time") == [[], ["stuck"]]
    assert golden["void_with_a_moved_start"]["steps"][-1]["state"]["done"] is False
    assert golden["void_without_a_new_start"]["steps"][-1]["state"]["done"] is True
