"""
İndirme işlerinin kaydedilen belirtimi (plan maddesi FX-20; sofascore_scraper/services/job_spec.py).

  * kayıt istek gövdesinin alanlarıyla yazılır (`only`, `event_ids`; servisin `mode`u değil), kimlikle seçilen maçlar
    `event_ids` olarak, yanında turnuvalarına ayrılmış `selections` (`target=tournament:` süzgeci, G12);
  * eski kayıtlar (`mode`, turnuva başına `selections` ve `event_ids`) okunurken gövde biçimine çevrilir: API onları
    öyle verir, zamanlayıcı kendi eski işini yine tanır;
  * kayıt, adını verdiği takiplerin ve liglerin adlarını taşır (`names`): iş sayfası "Takım #42" demez.

Tümü çevrimdışı; işin gövdesi (servis çağrısı) sahtedir.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Iterator

import conftest
import pytest
from fastapi.testclient import TestClient

import store_fixtures as sf
from sofascore_scraper.jobs.manager import JobManager, JobOutcome
from sofascore_scraper.jobs.model import JobKind, Origin
from sofascore_scraper.services import job_spec
from sofascore_scraper.services.sync import FollowsSyncSpec, SyncSelection, SyncSpec
from sofascore_scraper.store import FollowSpec, JobStore, Store, default_db_path, open_store
from sofascore_scraper.web import deps
from sofascore_scraper.web.api.v1 import jobs as jobs_v1
from sofascore_scraper.web.app import app

client = TestClient(app)
PL = sf.PL.id
ARS = sf.event_id(sf.PL_ARS)
EMPTY = {"league_id": None, "selections": [], "follows": [], "only": None, "event_ids": []}


def wait_for(condition: Any, timeout: float = 20.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def data(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    fixture = sf.build_fixture("canonical", tmp_path / "data")
    monkeypatch.setenv("DATA_DIR", str(fixture.data_dir))
    return open_store(fixture.data_dir)


@pytest.fixture
def jobs(store: Store, monkeypatch: pytest.MonkeyPatch) -> Iterator[JobStore]:
    before = conftest.job_threads()
    job_store = JobStore(default_db_path(str(store.data_dir)))
    monkeypatch.setattr(deps, "job_store", lambda: job_store)
    monkeypatch.setattr(jobs_v1, "_run_sync", lambda handle, spec: JobOutcome())
    yield job_store
    conftest.join_job_threads(before)
    job_store.close()


def start(body: Dict[str, Any]) -> Dict[str, Any]:
    job = data(client.post("/api/v1/jobs", json=body), 202)
    wait_for(lambda: data(client.get(f"/api/v1/jobs/{job['id']}"))["finished_at"])
    return job


# --- kayıt: gövdenin alanları -------------------------------------------------------------------------


@pytest.mark.parametrize("kind, spec, event_ids, expected", [
    ("sync", SyncSpec(mode="full"), (), EMPTY),
    ("sync", SyncSpec(mode="full", league_id=17), (), {**EMPTY, "league_id": 17}),
    ("sync", SyncSpec(mode="seasons", league_id=17), (), {**EMPTY, "league_id": 17, "only": "seasons"}),
    ("sync", SyncSpec(mode="details"), (), {**EMPTY, "only": "events"}),  # ssc sync --only events
    ("sync", FollowsSyncSpec(mode="full", follows=("team:42", "tournament:17")), (),
     {**EMPTY, "follows": ["team:42", "tournament:17"]}),
    ("sync", SyncSpec(mode="full", selections=(SyncSelection(league_id=17, season_ids=(1, 2)),)), (),
     {**EMPTY, "selections": [{"league_id": 17, "season_ids": [1, 2], "match_ids": []}]}),
    ("fetch", SyncSpec(mode="details", league_id=17), (), {**EMPTY, "league_id": 17}),
    # a fetch by event ids: recorded as such, with the leagues the server sorted them into (target filter, G12)
    ("fetch", SyncSpec(mode="details", selections=(SyncSelection(league_id=17, match_ids=(5, 6)),
                                                   SyncSelection(league_id=0, match_ids=(9,)))),
     (5, 6, 9, 5), {**EMPTY, "event_ids": [5, 6, 9], "selections": [
         {"league_id": 17, "season_ids": [], "match_ids": [5, 6]}, {"league_id": 0, "season_ids": [], "match_ids": [9]}]}),
    ("refresh", SyncSpec(mode="refresh"), (), {"league_id": None, "event_ids": []}),
    ("refresh", SyncSpec(mode="refresh", league_id=17), (), {"league_id": 17, "event_ids": []}),
    ("refresh", SyncSpec(mode="refresh", selections=(SyncSelection(league_id=17, match_ids=(5,)),)), (5,),
     {"league_id": None, "event_ids": [5], "selections": [{"league_id": 17, "season_ids": [], "match_ids": [5]}]}),
])
def test_the_record_has_the_fields_of_the_request_body(kind: str, spec: SyncSpec, event_ids: Any,
                                                        expected: Dict[str, Any]) -> None:
    recorded = job_spec.record(kind, spec, event_ids=event_ids)
    assert recorded == expected
    assert "mode" not in recorded
    assert job_spec.body(kind, recorded) == expected  # already the body: unchanged
    with_names = job_spec.record(kind, spec, event_ids=event_ids, names={"team:42": "Arsenal"})
    assert with_names == {**expected, "names": {"team:42": "Arsenal"}}


# --- eski kayıtlar ----------------------------------------------------------------------------------


@pytest.mark.parametrize("kind, old, expected", [
    ("sync", {"mode": "full", "league_id": 17, "selections": []}, {**EMPTY, "league_id": 17}),
    ("sync", {"mode": "full", "league_id": 17}, {**EMPTY, "league_id": 17}),
    ("sync", {"mode": "seasons", "league_id": None, "selections": [], "follows": ["tournament:17"]},
     {**EMPTY, "only": "seasons", "follows": ["tournament:17"]}),
    ("sync", {"mode": "details", "league_id": None, "selections": []}, {**EMPTY, "only": "events"}),
    ("sync", {"mode": "full", "league_id": None, "selections": [{"league_id": 17, "season_ids": [1], "match_ids": None}]},
     {**EMPTY, "selections": [{"league_id": 17, "season_ids": [1], "match_ids": []}]}),
    # FX-13's fetch by event ids: per-tournament selections plus event_ids
    ("fetch", {"mode": "details", "league_id": None, "selections": [
        {"league_id": 17, "season_ids": [], "match_ids": [5]}, {"league_id": 0, "season_ids": [], "match_ids": [9]}],
        "event_ids": [5, 9]}, {**EMPTY, "event_ids": [5, 9], "selections": [
            {"league_id": 17, "season_ids": [], "match_ids": [5]}, {"league_id": 0, "season_ids": [], "match_ids": [9]}]}),
    # `ssc fetch event` before FX-20: selections only (still a valid body)
    ("fetch", {"mode": "details", "league_id": None, "selections": [{"league_id": 17, "season_ids": [], "match_ids": [5]}]},
     {**EMPTY, "selections": [{"league_id": 17, "season_ids": [], "match_ids": [5]}]}),
    ("refresh", {"mode": "refresh", "league_id": None, "selections": []}, {"league_id": None, "event_ids": []}),
    ("refresh", {"mode": "refresh", "league_id": None, "selections": [{"league_id": 17, "season_ids": [], "match_ids": [5]}],
                 "event_ids": [5]}, {"league_id": None, "event_ids": [5], "selections": [
                     {"league_id": 17, "season_ids": [], "match_ids": [5]}]}),
    ("refresh", {"mode": "refresh", "league_id": None, "selections": [{"league_id": 17, "season_ids": [], "match_ids": [5]}]},
     {"league_id": None, "event_ids": [5], "selections": [{"league_id": 17, "season_ids": [], "match_ids": [5]}]}),
])
def test_an_old_record_is_read_in_the_shape_of_the_body(kind: str, old: Dict[str, Any],
                                                         expected: Dict[str, Any]) -> None:
    assert job_spec.body(kind, old) == expected
    assert job_spec.same(kind, old, expected)


def test_other_kinds_and_names_are_kept() -> None:
    clear = {"scope": "all", "confirm": True, "tournament_id": 17, "season_id": None, "names": {"tournament:17": "PL"}}
    assert job_spec.body("clear", clear) == clear
    assert job_spec.body("sync", {"mode": "full", "names": {"tournament:17": "PL"}})["names"] == {"tournament:17": "PL"}
    assert job_spec.same("sync", {**EMPTY, "names": {"team:42": "Arsenal"}}, {**EMPTY, "names": {"team:42": "Gunners"}})
    assert not job_spec.same("sync", EMPTY, {**EMPTY, "league_id": 17})


def test_the_names_of_follows_and_leagues(store: Store) -> None:
    store.follows.add(FollowSpec(kind="team", entity_id=42, name="Arsenal"), origin="api")
    store.follows.add(FollowSpec(kind="player", entity_id=7, name="Bukayo Saka"), origin="api")
    store.follows.add(FollowSpec(kind="tournament", entity_id=sf.LALIGA.id, name="La Liga (mine)"), origin="api")
    targets = ["team:42", "player:7", f"tournament:{sf.LALIGA.id}", f"tournament:{PL}", "team:999", "event:5", "x"]
    assert job_spec.names(store, targets) == {
        "team:42": "Arsenal", "player:7": "Bukayo Saka", f"tournament:{sf.LALIGA.id}": "La Liga (mine)",
        f"tournament:{PL}": "Premier League",  # not followed: the catalog's name
    }
    assert job_spec.targets({"league_id": 8, "selections": [{"league_id": 17}], "follows": ["team:42", "event:5"]}) == [
        "tournament:8", "tournament:17", "team:42"]


def test_an_unreadable_store_names_nothing(caplog: pytest.LogCaptureFixture) -> None:
    class Broken:
        @property
        def follows(self) -> Any:
            raise OSError("gone")

    assert job_spec.names(Broken(), ["team:42"]) == {}  # type: ignore[arg-type]
    assert "could not be read" in caplog.text


# --- API --------------------------------------------------------------------------------------------


def test_a_team_job_carries_the_follows_name(store: Store, jobs: JobStore) -> None:
    store.follows.add(FollowSpec(kind="team", entity_id=42, name="Arsenal", sport="football"), origin="api")
    job = start({"kind": "sync", "spec": {"follows": ["team:42"]}})
    assert job["spec"] == {**EMPTY, "follows": ["team:42"], "names": {"team:42": "Arsenal"}}
    # the name stays when the follow is gone
    store.follows.remove("team", 42)
    assert data(client.get(f"/api/v1/jobs/{job['id']}"))["spec"]["names"] == {"team:42": "Arsenal"}
    assert [j["id"] for j in data(client.get("/api/v1/jobs", params={"target": "team:42"}))] == [job["id"]]


def test_a_fetch_and_a_refresh_by_event_ids_are_recorded_as_such(store: Store, jobs: JobStore) -> None:
    fetch = start({"kind": "fetch", "spec": {"event_ids": [ARS, 9100003]}})
    # an event the catalog does not know is under league 0
    assert fetch["spec"] == {**EMPTY, "event_ids": [ARS, 9100003], "selections": [
        {"league_id": 0, "season_ids": [], "match_ids": [9100003]}, {"league_id": PL, "season_ids": [], "match_ids": [ARS]}],
        "names": {f"tournament:{PL}": "Premier League"}}
    refresh = start({"kind": "refresh", "spec": {"event_ids": [ARS]}})
    assert refresh["spec"] == {"league_id": None, "event_ids": [ARS], "selections": [
        {"league_id": PL, "season_ids": [], "match_ids": [ARS]}], "names": {f"tournament:{PL}": "Premier League"}}
    found = data(client.get("/api/v1/jobs", params={"target": f"event:{ARS}"}))
    assert [j["id"] for j in found] == [refresh["id"], fetch["id"]]
    # the league's jobs still list the download of its matches (G12, the follow page's Jobs tab)
    found = data(client.get("/api/v1/jobs", params={"target": f"tournament:{PL}"}))
    assert [j["id"] for j in found] == [refresh["id"], fetch["id"]]


def test_an_old_record_is_given_in_the_new_shape(store: Store, jobs: JobStore) -> None:
    manager = JobManager(jobs)
    old = manager.submit(JobKind.FETCH, {"mode": "details", "league_id": None, "selections": [
        {"league_id": PL, "season_ids": [], "match_ids": [5]}], "event_ids": [5]},
        lambda handle: JobOutcome(), origin=Origin(face="cli", pid=1, host="box"), background=False)
    assert data(client.get(f"/api/v1/jobs/{old.id}"))["spec"] == {**EMPTY, "event_ids": [5], "selections": [
        {"league_id": PL, "season_ids": [], "match_ids": [5]}]}
    assert [j["id"] for j in data(client.get("/api/v1/jobs", params={"target": "event:5"}))] == [old.id]


def test_a_league_clear_carries_the_leagues_name(store: Store, jobs: JobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs_v1, "_clear_body", lambda spec: (lambda handle: JobOutcome()))
    job = data(client.post("/api/v1/jobs", json={"kind": "clear", "spec": {"confirm": True, "tournament_id": PL}}), 202)
    assert job["spec"]["names"] == {f"tournament:{PL}": "Premier League"}
    wait_for(lambda: data(client.get(f"/api/v1/jobs/{job['id']}"))["finished_at"])
