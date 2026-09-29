"""Structured job progress: phase weights, job-wide counters, ETA, waits, and the fetch job flow."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from src.web.jobs import JobStore
from src.web.progress import MAX_FAILED_LISTED, JobProgress


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make(phases: List[str]):
    published: List[Dict[str, Any]] = []
    clock = Clock()
    p = JobProgress(phases, published.append, clock=clock, wall_clock=clock)
    return p, published, clock


def test_percent_follows_phase_weights_and_never_hits_100():
    p, _, _ = make(["seasons", "matches", "details", "export"])
    assert p.percent() == 0
    p.start_phase("seasons", 2)
    p.advance(1)
    assert p.percent() == 2  # half of 5
    p.start_phase("details", 10)
    assert p.percent() == 30  # seasons 5 + matches 25
    p.advance(5)
    assert p.percent() == 62  # 30 + 65 * 0.5
    p.start_phase("export", 1)
    p.advance(1)
    assert p.percent() == 99


def test_weights_are_rescaled_when_phases_are_skipped():
    p, _, _ = make(["details", "export"])
    p.start_phase("details", 4)
    p.advance(2)
    # details is 65 of 70 → 92.86 %, half of it
    assert p.percent() == 46


def test_detail_counters_are_published_as_matches_fields():
    p, published, _ = make(["details", "export"])
    p.start_phase("details", 0)
    p.set_total(7)
    p.advance(3)
    last = published[-1]
    assert (last["matches_done"], last["matches_total"]) == (3, 7)
    assert last["detail"]["phase"] == "details"
    assert last["detail"]["phase_index"] == 1 and last["detail"]["phase_count"] == 2
    # Counters stay after the export phase starts, so the finished card can report them
    p.start_phase("export", 1)
    assert (published[-1]["matches_done"], published[-1]["matches_total"]) == (3, 7)


def test_eta_needs_some_progress_and_time():
    p, _, clock = make(["details"])
    p.start_phase("details", 100)
    p.advance(10)
    assert p.eta_seconds() is None  # too early
    clock.t += 20
    assert p.eta_seconds() == 180.0  # 2 s per match, 90 left
    p.advance(100)
    assert p.eta_seconds() is None


def test_wait_keeps_the_longer_one_and_clears_once_past():
    p, _, clock = make(["details"])
    p.start_phase("details", 10)
    p.wait("forbidden", 40)
    p.wait("rate_limit", 5)
    assert p.detail()["wait"] == {"reason": "forbidden", "until": 1040.0}
    clock.t += 41
    p.advance(1)
    assert p.detail()["wait"] is None


def test_failed_list_is_capped_but_count_is_not():
    p, _, _ = make(["details"])
    p.start_phase("details", 100)
    for i in range(MAX_FAILED_LISTED + 5):
        p.add_failed(str(i), league_id=17)
    d = p.detail()
    assert d["failed_count"] == MAX_FAILED_LISTED + 5
    assert len(d["failed"]) == MAX_FAILED_LISTED
    assert d["failed"][0] == {"match_id": "0", "league_id": 17}


def test_breaker_is_published():
    p, published, _ = make(["details"])
    p.start_phase("details", 1)
    p.breaker("403")
    assert published[-1]["circuit_breaker_triggered"] is True
    assert published[-1]["circuit_breaker_reason"] == "403"
    assert p.result()["breaker"] == "403"


def test_unknown_phase_is_rejected():
    with pytest.raises(ValueError):
        JobProgress(["download"], lambda f: None)


def test_job_store_keeps_detail_in_the_mirror(tmp_path):
    store = JobStore(str(tmp_path / "jobs.db"))
    store.create_running({})
    assert store.snapshot()["detail"] is None
    store.update(detail={"phase": "details"}, matches_failed=2)
    assert store.snapshot()["detail"] == {"phase": "details"}
    store.update(status="Completed", finished=True)
    assert store.list_jobs()[0]["matches_failed"] == 2


# --- run_fetch_job with a fake UI -----------------------------------------------------


class FakeMatchData:
    def __init__(self, pending: Dict[str, List[str]], failing: set, breaker_after: str = "") -> None:
        self.pending = pending
        self.failing = failing
        self.breaker_after = breaker_after
        self.rate_limit_breaker_triggered = False
        self.last_status_counts: Dict[str, int] = {}
        self.fetched: List[List[str]] = []

    def begin_job_cache(self) -> None:
        pass

    def end_job_cache(self) -> None:
        pass

    def collect_detail_match_ids(self, league_id=None, max_seasons=0, only_season_ids=None):
        return list(self.pending.get(league_id, [])) + ["done-" + str(league_id)]

    def pending_detail_ids(self, ids):
        return [i for i in ids if not i.startswith("done-")]

    def fetch_detail_ids(self, ids, progress_callback=None, should_cancel=None, failed_callback=None):
        self.fetched.append(list(ids))
        progress_callback(0, len(ids), "")
        for n, mid in enumerate(ids, start=1):
            if mid in self.failing:
                failed_callback(mid)
            progress_callback(n, len(ids), "")
        if self.breaker_after and self.breaker_after in ids:
            self.rate_limit_breaker_triggered = True
            self.last_status_counts = {"403": 9, "404": 20, "429": 1}
        return len(ids)


def fake_ui(md: FakeMatchData, seasons: Dict[int, List[Dict[str, Any]]]):
    return SimpleNamespace(
        season_fetcher=SimpleNamespace(
            fetch_seasons_for_league=lambda lid: seasons.get(lid, []),
            get_seasons_for_league=lambda lid: seasons.get(lid, []),
            resolve_season_id=lambda lid, sid: sid,
        ),
        match_fetcher=SimpleNamespace(fetch_matches_for_season=lambda lid, sid: True),
        match_data_fetcher=md,
        export_all_to_csv=lambda: None,
    )


@pytest.fixture
def job_env(tmp_path, monkeypatch):
    import src.web.fetch_job as fj

    store = JobStore(str(tmp_path / "jobs.db"))
    snaps: List[Dict[str, Any]] = []
    monkeypatch.setattr(fj, "_job_store", store)
    monkeypatch.setattr(fj, "_refresh_scraper_state", lambda: snaps.append(store.snapshot()) or snaps[-1])
    monkeypatch.setattr(fj.config_manager, "get_leagues", lambda: {17: "Premier League", 8: "LaLiga"})
    return fj, store, snaps


def run(fj, store, monkeypatch, ui, payload):
    from src.web.routes.scrape import FetchRequest

    monkeypatch.setattr(fj, "SimpleSofaScoreUI", lambda config_manager: ui)
    req = FetchRequest(**payload)
    job_id = store.create_running(req.model_dump())
    fj.run_fetch_job(job_id, req)
    return store.snapshot()


def test_selection_job_counts_details_across_leagues(job_env, monkeypatch):
    fj, store, snaps = job_env
    md = FakeMatchData({"17": ["a", "b", "c"], "8": ["d", "e"]}, failing={"d"})
    seasons = {17: [{"id": 1, "name": "PL 24/25"}], 8: [{"id": 2, "name": "LaLiga 24/25"}]}
    final = run(
        fj, store, monkeypatch, fake_ui(md, seasons),
        {"mode": "full", "selections": [{"league_id": 17, "season_ids": [1]}, {"league_id": 8, "season_ids": [2]}]},
    )

    assert final["status"] == "Completed" and final["progress"] == 100
    # One job-wide counter: never goes backwards when the next league starts
    detail_done = [s["matches_done"] for s in snaps if (s.get("detail") or {}).get("phase") == "details"]
    assert detail_done == sorted(detail_done)
    assert final["matches_done"] == 5 and final["matches_total"] == 5
    assert final["matches_failed"] == 1
    assert final["result"]["failed"] == [{"match_id": "d", "league_id": 8}]
    assert final["detail"]["phases"] == ["seasons", "matches", "details", "export"]

    # The matches phase names the league and season being worked on
    ctx = [s["detail"] for s in snaps if (s.get("detail") or {}).get("phase") == "matches"]
    assert {"league_id": 8, "league_name": "LaLiga", "season_name": "LaLiga 24/25"}.items() <= ctx[-1].items()

    # Progress never goes backwards either
    pcts = [s["progress"] for s in snaps if s.get("is_running")]
    assert pcts == sorted(pcts)


def test_details_only_job_skips_season_phases(job_env, monkeypatch):
    fj, store, _ = job_env
    md = FakeMatchData({"17": ["a"]}, failing=set())
    final = run(fj, store, monkeypatch, fake_ui(md, {}), {"mode": "details", "league_id": 17})
    assert final["detail"]["phases"] == ["details", "export"]
    assert md.fetched == [["a"]]


def test_breaker_stops_remaining_leagues_and_is_reported(job_env, monkeypatch):
    fj, store, _ = job_env
    md = FakeMatchData({"17": ["a"], "8": ["b"]}, failing=set(), breaker_after="b")
    seasons = {17: [{"id": 1}], 8: [{"id": 2}]}
    final = run(
        fj, store, monkeypatch, fake_ui(md, seasons),
        {"mode": "full", "selections": [{"league_id": 8, "season_ids": [2]}, {"league_id": 17, "season_ids": [1]}]},
    )
    assert md.fetched == [["b"]]  # league 17 never started
    assert final["circuit_breaker_triggered"] is True
    assert final["circuit_breaker_reason"] == "403"
    assert final["status"] == "Completed"


def test_wait_notifier_reaches_the_card(job_env, monkeypatch):
    fj, store, _ = job_env
    from src import utils

    class WaitingMD(FakeMatchData):
        def fetch_detail_ids(self, ids, **kw):
            utils._notify_wait("rate_limit", 30)
            return super().fetch_detail_ids(ids, **kw)

    md = WaitingMD({"17": ["a"]}, failing=set())
    seen: List[Any] = []
    orig = store.update

    def spy(**kw):
        if kw.get("detail") and kw["detail"].get("wait"):
            seen.append(kw["detail"]["wait"]["reason"])
        orig(**kw)

    monkeypatch.setattr(store, "update", spy)
    run(fj, store, monkeypatch, fake_ui(md, {}), {"mode": "details", "league_id": 17})
    assert seen and seen[0] == "rate_limit"
    # The notifier is per job: nothing is left behind for other requests
    assert utils._wait_notifier.get() is None
