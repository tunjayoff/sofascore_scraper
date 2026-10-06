"""
Bir ligin saklanan verisinin silinmesi (plan maddesi FX-19; src/store/purge.py, MaintenanceService.clear_tournament):

  * Store: turnuvanın (ya da sezonunun) maçları iki düzende de, programları, turnuvanın tamamında sezon listesi;
    öteki turnuvalar, takipler ve değişiklik günlüğü kalır; katalog yeniden kurulur; `maintenance` kilidi;
  * `POST /api/v1/jobs` `clear` + `tournament_id` (+ `season_id`): denetimler ve sonuç;
  * `DELETE /api/v1/follows/{id}?delete_data=true`: takip kaldırılır, verisi bir `clear` işiyle silinir.

Ağ yok. Veri dizini store_fixtures'ın "canonical" eski düzen ağacıdır; v3 düzeni sahte SofaScore'dan indirilen bir
maçla (G-01 dünyası: 9100001, turnuva 17, sezon 61627) eklenir.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Set

import pytest
from fastapi.testclient import TestClient

import conftest
import store_fixtures as sf
from characterization import WORLD, pin_default_settings
from fakes.sofascore import FakeSofaScore
from src.config import loader
from src.services import planning
from src.services.maintenance import MaintenanceService
from src.services.pipeline import FetchPipeline
from src.services.query import RefreshPolicy
from src.store import EventQuery, FollowSpec, JobStore, Scope, Store, apply_follows, default_db_path, open_store
from src.store import layout
from src.web import deps
from src.web.app import app

client = TestClient(app)
PL, LALIGA = 17, 8
V3_EVENT, V3_SEASON = 9100001, 61627


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    pin_default_settings(monkeypatch)
    loader.reset()
    yield
    monkeypatch.undo()
    loader.reset()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    fixture = sf.build_fixture("canonical", tmp_path / "data")
    monkeypatch.setenv("DATA_DIR", str(fixture.data_dir))
    opened = open_store(fixture.data_dir)
    with FakeSofaScore.from_file(WORLD):
        items = planning.plan_items(opened, [V3_EVENT], RefreshPolicy.current(), selection=None)
        FetchPipeline(opened, concurrency=1, selection=None).run_sync(items)
    return opened


@pytest.fixture
def jobs(store: Store, monkeypatch: pytest.MonkeyPatch) -> Iterator[JobStore]:
    before = conftest.job_threads()
    job_store = JobStore(default_db_path(str(store.data_dir)))
    monkeypatch.setattr(deps, "job_store", lambda: job_store)
    yield job_store
    conftest.join_job_threads(before)
    job_store.close()


def events_of(store: Store, tournament_id: int) -> Set[int]:
    return {row.id for row in store.events.iter(EventQuery(scope=Scope(tournament_ids=(tournament_id,))))}


def legacy_trees(store: Store, tournament_id: int) -> Set[str]:
    found: Set[str] = set()
    for tree in ("matches", "match_details"):
        root = os.path.join(store.data_dir, tree)
        if os.path.isdir(root):
            found.update(f"{tree}/{name}" for name in os.listdir(root) if name.startswith(f"{tournament_id}_"))
    return found


def data(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


def error(response: Any, status: int, code: str) -> Dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code, body
    return body


def ended(job_id: str) -> Dict[str, Any]:
    deadline = time.monotonic() + 30
    while True:
        job = data(client.get(f"/api/v1/jobs/{job_id}"))
        if job["finished_at"]:
            return job
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


# --- Store ---------------------------------------------------------------------------------------------------


def test_a_season_and_then_a_whole_tournament_are_cleared(store: Store) -> None:
    pl_events, laliga_events = events_of(store, PL), events_of(store, LALIGA)
    assert V3_EVENT in pl_events and len(pl_events) > 1 and laliga_events
    v3_event = layout.resolve(store.data_dir, layout.event_dir(V3_EVENT))
    v3_season = layout.resolve(store.data_dir, layout.season_dir(PL, V3_SEASON))
    assert os.path.isdir(v3_event)
    store.follows.add(FollowSpec(kind="tournament", entity_id=PL, name="Premier League"))

    season = store.purge.tournament(PL, season_id=V3_SEASON)
    assert (season.tournament_id, season.season_id, season.events, season.catalog_rebuilt) == (PL, V3_SEASON, 1,
                                                                                              True)
    assert not os.path.exists(v3_event) and not os.path.exists(v3_season)
    assert events_of(store, PL) == pl_events - {V3_EVENT}
    assert legacy_trees(store, PL)  # öteki sezonların eski ağacı yerinde

    whole = MaintenanceService(store=store).clear_tournament(PL, confirm=True)
    assert 0 < whole.events < len(pl_events) and whole.listings >= 1  # liste satırlarının dizini yok
    assert events_of(store, PL) == set() and legacy_trees(store, PL) == set()
    assert store.entities.seasons(PL) == []
    # Öteki turnuva, takip ve kilitler yerinde
    assert events_of(store, LALIGA) == laliga_events and legacy_trees(store, LALIGA)
    assert store.follows.get("tournament", PL) is not None
    assert store.lease_holder("maintenance") is None


def test_the_clear_runs_under_the_maintenance_lease(store: Store) -> None:
    with store.lease("maintenance", purpose="test") as held:
        report = store.purge.tournament(LALIGA)  # bu süreç tutuyor: onun altında çalışır
        assert report.events > 0 and store.lease_holder("maintenance") is not None
    assert held is not None and store.lease_holder("maintenance") is None


@pytest.mark.parametrize("tournament_id, season_id", [(0, None), (-1, None), (True, None), (17, 0)])
def test_invalid_ids_are_refused(store: Store, tournament_id: Any, season_id: Any) -> None:
    with pytest.raises(ValueError):
        store.purge.tournament(tournament_id, season_id=season_id)
    with pytest.raises(ValueError):
        MaintenanceService(store=store).clear_tournament(PL, confirm=False)


# --- API: clear işi ------------------------------------------------------------------------------------------


def test_a_clear_job_with_a_tournament(store: Store, jobs: JobStore) -> None:
    job = data(client.post("/api/v1/jobs", json={"kind": "clear", "spec": {
        "confirm": True, "tournament_id": PL, "season_id": V3_SEASON}}), 202)
    done = ended(job["id"])
    assert done["state"] == "succeeded", done
    assert done["result"]["clear"] == {"scopes": ["tournament"], "tournament_id": PL, "season_id": V3_SEASON,
                                       "events": 1, "event_dirs": 1, "listings": 0, "catalog_rebuilt": True}
    assert done["spec"]["tournament_id"] == PL
    assert [j["id"] for j in data(client.get("/api/v1/jobs", params={"target": f"tournament:{PL}"}))] == [job["id"]]
    assert V3_EVENT not in events_of(store, PL)


@pytest.mark.parametrize("spec, code", [
    ({"tournament_id": PL}, "confirmation_required"),
    ({"confirm": True, "season_id": V3_SEASON}, "invalid_request"),
    ({"confirm": True, "tournament_id": PL, "scope": "events"}, "invalid_request"),
])
def test_a_tournament_clear_is_checked_before_it_starts(store: Store, jobs: JobStore, spec: Dict[str, Any],
                                                        code: str) -> None:
    error(client.post("/api/v1/jobs", json={"kind": "clear", "spec": spec}), 400, code)
    assert data(client.get("/api/v1/jobs")) == []


# --- API: takip kaldırma ve verisi ---------------------------------------------------------------------------


def test_removing_a_follow_with_its_data(store: Store, jobs: JobStore) -> None:
    store.follows.add(FollowSpec(kind="tournament", entity_id=PL, name="Premier League"))
    answer = client.delete(f"/api/v1/follows/tournament:{PL}", params={"delete_data": "true"})
    body = answer.json()
    assert answer.status_code == 200, body
    assert body["data"]["id"] == f"tournament:{PL}" and body["data"]["clear_job"]["kind"] == "clear"
    assert store.follows.get("tournament", PL) is None  # yanıt geldiğinde takip yok
    done = ended(body["data"]["clear_job"]["id"])
    assert done["state"] == "succeeded" and done["result"]["clear"]["tournament_id"] == PL
    assert events_of(store, PL) == set() and events_of(store, LALIGA)


def test_removing_a_follow_without_its_data_keeps_it(store: Store, jobs: JobStore) -> None:
    store.follows.add(FollowSpec(kind="tournament", entity_id=PL, name="Premier League"))
    body = client.delete(f"/api/v1/follows/tournament:{PL}").json()
    assert body["data"]["clear_job"] is None and events_of(store, PL)


def test_only_a_tournament_follow_takes_its_data_along(store: Store, jobs: JobStore) -> None:
    store.follows.add(FollowSpec(kind="team", entity_id=42, name="A team"))
    body = error(client.delete("/api/v1/follows/team:42", params={"delete_data": "true"}), 400, "invalid_request")
    assert body["details"] == {"field": "delete_data", "kind": "team"}
    apply_follows(store.data_dir, [FollowSpec(kind="tournament", entity_id=35, name="Bundesliga")], origin="config")
    error(client.delete("/api/v1/follows/tournament:35", params={"delete_data": "true"}), 409, "follow_managed")
    error(client.delete("/api/v1/follows/tournament:5", params={"delete_data": "true"}), 404, "not_found")
    assert store.follows.get("team", 42) is not None and data(client.get("/api/v1/jobs")) == []


def test_a_follow_and_its_data_stay_while_another_process_writes(store: Store, jobs: JobStore) -> None:
    store.follows.add(FollowSpec(kind="tournament", entity_id=PL, name="Premier League"))
    with store.lease("writer", purpose="job"):
        error(client.delete(f"/api/v1/follows/tournament:{PL}", params={"delete_data": "true"}), 409, "job_running")
    assert store.follows.get("tournament", PL) is not None and events_of(store, PL)
