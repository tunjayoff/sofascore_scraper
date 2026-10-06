"""
API v1 işlerinin FX-13 eklemeleri (docs/design/05-web-ui.md 7.3: G12, G14, G15, G16, G23; plan maddesi FX-13).
Tümü çevrimdışı: işin gövdesi (servis çağrısı) sahtedir.

  * `sync` + `only: "seasons"`: yalnızca sezon listeleri (G15);
  * `sync` + `follows`: adı verilen turnuva takipleri; bilinmeyen takip 404, takım takibi 400 (G23);
  * `fetch` + `event_ids`: turnuvası bilinmese de maçlar (G16), `ssc fetch event` gibi;
  * hedef alanları birlikte verilemez; yanlış türdeki alan 400; reddedilen istek iş kaydı bırakmaz;
  * `GET /jobs?origin=` (G14) ve `?target=` (G12);
  * sync işinin sonucu `failed_listings`i taşır (P14'ün notu);
  * eşitleme işine API'den eklenen bir takip girer (FX-13'ün testi: takip, bir sonraki eşitlemenin planındadır).
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List

import conftest
import pytest
from fastapi.testclient import TestClient

import store_fixtures as sf
from src.jobs.manager import JobManager, JobOutcome
from src.jobs.model import JobKind, Origin
from src.services import sync as sync_service
from src.store import FollowSpec, JobStore, Store, default_db_path, open_store
from src.web import deps
from src.web.api.v1 import jobs as jobs_v1
from src.web.app import app

client = TestClient(app)


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


def error(response: Any, status: int, code: str) -> Dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code, body
    return body


def ended(job_id: str) -> Dict[str, Any]:
    return wait_for(lambda: (lambda j: j if j["finished_at"] else None)(data(client.get(f"/api/v1/jobs/{job_id}"))))


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
    yield job_store
    conftest.join_job_threads(before)
    job_store.close()


@pytest.fixture
def specs(monkeypatch: pytest.MonkeyPatch) -> List[Any]:
    """İşin gövdesi: servis yerine belirtimi kaydeder ve hemen biter."""
    seen: List[Any] = []

    def run(handle: Any, spec: Any) -> Any:
        seen.append(spec)
        return JobOutcome()

    monkeypatch.setattr(jobs_v1, "_run_sync", run)
    return seen


def start(body: Dict[str, Any]) -> Dict[str, Any]:
    job = data(client.post("/api/v1/jobs", json=body), 202)
    ended(job["id"])
    return job


# --- sezon listesi işi (G15) ---------------------------------------------------------------------------


def test_a_season_list_job_reads_season_lists_only(jobs: JobStore, specs: List[Any]) -> None:
    job = start({"kind": "sync", "spec": {"league_id": 17, "only": "seasons"}})

    (spec,) = specs
    assert (spec.mode, spec.league_id, spec.job_phases) == ("seasons", 17, ("seasons",))
    assert job["kind"] == "sync"
    assert job["spec"] == {"mode": "seasons", "league_id": 17, "selections": []}


# --- adı verilen takipler (G23) ------------------------------------------------------------------------


def test_a_sync_of_named_follows(jobs: JobStore, store: Store, specs: List[Any]) -> None:
    store.follows.add(FollowSpec(kind="tournament", entity_id=4242, name="New Cup", seasons="current"), origin="api")
    store.follows.add(FollowSpec(kind="team", entity_id=42, name="A team"), origin="api")

    job = start({"kind": "sync", "spec": {"follows": ["tournament:4242", "tournament:4242"]}})

    (spec,) = specs
    assert isinstance(spec, sync_service.FollowsSyncSpec)
    assert (spec.mode, spec.follows, spec.league_id, spec.selections) == ("full", ("tournament:4242",), None, ())
    assert job["spec"]["follows"] == ["tournament:4242"]

    missing = error(client.post("/api/v1/jobs", json={"kind": "sync", "spec": {"follows": ["tournament:5"]}}),
                    404, "not_found")
    assert missing["details"] == {"follows": ["tournament:5"]}
    # FX-19: takım, oyuncu ve maç takipleri de eşitlenir (önceden 400 `unsupported`)
    team = start({"kind": "sync", "spec": {"follows": ["team:42"]}})
    assert specs[-1].follows == ("team:42",) and team["spec"]["follows"] == ["team:42"]
    seasons = start({"kind": "sync", "spec": {"follows": ["tournament:4242"], "only": "seasons"}})
    assert specs[-1].mode == "seasons" and seasons["spec"]["follows"] == ["tournament:4242"]
    assert len(data(client.get("/api/v1/jobs"))) == 3


# --- maç kimlikleriyle getirme (G16) -------------------------------------------------------------------


def test_a_fetch_of_events_by_id_alone(jobs: JobStore, specs: List[Any]) -> None:
    known = sf.NBA_A.eid or 16484334
    job = start({"kind": "fetch", "spec": {"event_ids": [999999999, known, known]}})

    (spec,) = specs
    assert spec.mode == "details" and spec.league_id is None
    assert [(s.league_id, s.match_ids) for s in spec.selections] == [(0, (999999999,)), (sf.NBA.id, (known,))]
    assert job["spec"]["event_ids"] == [999999999, known]
    assert job["kind"] == "fetch"


def test_a_refresh_of_events_by_id(jobs: JobStore, specs: List[Any]) -> None:
    """G23: `refresh` + `event_ids`: yalnızca bu maçların /event'i (Fetch again)."""
    known = sf.NBA_A.eid or 16484334
    job = start({"kind": "refresh", "spec": {"event_ids": [known, 999999999]}})

    (spec,) = specs
    assert spec.mode == "refresh" and spec.league_id is None
    assert [(s.league_id, s.match_ids) for s in spec.selections] == [(0, (999999999,)), (sf.NBA.id, (known,))]
    assert job["spec"]["event_ids"] == [known, 999999999]
    assert data(client.get("/api/v1/jobs?target=event:999999999"))[0]["id"] == job["id"]
    refused = error(client.post("/api/v1/jobs", json={"kind": "refresh", "spec": {"event_ids": [1], "league_id": 2}}),
                    400, "invalid_request")
    assert refused["details"]["fields"] == ["league_id", "event_ids"]


@pytest.mark.parametrize("request_body,fields", [
    ({"kind": "fetch", "spec": {"event_ids": [1], "league_id": 17}}, ["league_id", "event_ids"]),
    ({"kind": "sync", "spec": {"follows": ["tournament:17"], "league_id": 17}}, ["league_id", "follows"]),
    ({"kind": "sync", "spec": {"event_ids": [1]}}, ["event_ids"]),
    ({"kind": "fetch", "spec": {"only": "seasons"}}, ["only"]),
    ({"kind": "fetch", "spec": {"follows": ["tournament:17"]}}, ["follows"]),
    ({"kind": "sync", "spec": {"only": "seasons", "selections": [{"league_id": 17}]}}, ["selections", "only"]),
])
def test_target_fields_that_do_not_go_together(jobs: JobStore, specs: List[Any], request_body: Dict[str, Any],
                                               fields: List[str]) -> None:
    refused = error(client.post("/api/v1/jobs", json=request_body), 400, "invalid_request")
    assert refused["details"]["fields"] == fields
    assert data(client.get("/api/v1/jobs")) == [] and specs == []


@pytest.mark.parametrize("request_body", [
    {"kind": "sync", "spec": {"follows": ["league:17"]}},
    {"kind": "sync", "spec": {"only": "listing"}},
    {"kind": "fetch", "spec": {"event_ids": [0]}},
])
def test_malformed_target_fields(jobs: JobStore, request_body: Dict[str, Any]) -> None:
    error(client.post("/api/v1/jobs", json=request_body), 422, "invalid_request")


# --- süzgeçler (G12, G14) ------------------------------------------------------------------------------


def test_jobs_by_origin_and_by_target(jobs: JobStore, store: Store, specs: List[Any]) -> None:
    manager = JobManager(jobs)
    cli_job = manager.submit(JobKind.SYNC, {"mode": "full", "league_id": 17}, lambda handle: JobOutcome(),
                             origin=Origin(face="cli", pid=1, host="box"), background=False)
    store.follows.add(FollowSpec(kind="tournament", entity_id=4242, name="New Cup"), origin="api")
    by_follow = start({"kind": "sync", "spec": {"follows": ["tournament:4242"]}})
    by_event = start({"kind": "fetch", "spec": {"event_ids": [123]}})
    by_selection = start({"kind": "fetch", "spec": {"selections": [{"league_id": 8, "match_ids": [77]}]}})
    everything = start({"kind": "sync"})

    def ids(query: str) -> List[str]:
        return [job["id"] for job in data(client.get(f"/api/v1/jobs?{query}"))]

    assert ids("origin=cli") == [cli_job.id]
    assert set(ids("origin=api")) == {by_follow["id"], by_event["id"], by_selection["id"], everything["id"]}
    assert ids("target=tournament:17") == [cli_job.id]
    assert ids("target=tournament:4242") == [by_follow["id"]]
    assert ids("target=event:123") == [by_event["id"]]
    assert ids("target=event:77") == [by_selection["id"]] and ids("target=tournament:8") == [by_selection["id"]]
    assert ids("target=tournament:4242&origin=cli") == []
    assert ids("target=team:1") == []  # FX-19: takım ve oyuncu takipleri de hedeftir
    error(client.get("/api/v1/jobs?target=league:1"), 422, "invalid_request")
    assert jobs_v1.job_targets({"mode": "full"}) == []


# --- sonuç: failed_listings ----------------------------------------------------------------------------


def test_the_sync_result_carries_the_failed_listings(jobs: JobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    def run(self: Any, spec: Any, *, handle: Any = None) -> Any:
        return sync_service.SyncResult(
            state="partial", schedule_empty_seasons=0, breaker=None, progress={},
            failed_listings=(sync_service.FailedListing("seasons", 17, None, "403"),))

    monkeypatch.setattr(sync_service.SyncService, "run", run)
    job = data(client.post("/api/v1/jobs", json={"kind": "sync", "spec": {"league_id": 17}}), 202)
    done = ended(job["id"])
    assert done["state"] == "partial"
    assert done["result"]["failed_listings"] == [
        {"kind": "seasons", "league_id": 17, "season_id": None, "reason": "403"}]


# --- bir takip API'den eklenir, sonraki eşitleme onu indirir -------------------------------------------


def test_a_follow_added_through_the_api_is_in_the_next_sync(jobs: JobStore, store: Store,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """FX-13'ün davranış değişikliği: `POST /follows` ile eklenen turnuva, bir sonraki `sync` işinde indirilir."""
    seen: List[Any] = []

    def run(self: Any, spec: Any, *, handle: Any = None) -> Any:
        seen.append(sorted(sync_service.sync_targets(self._ctx)))
        return sync_service.SyncResult(state="succeeded", schedule_empty_seasons=0, breaker=None, progress={})

    monkeypatch.setattr(sync_service.SyncService, "run", run)
    # Yapılandırma dosyası varken yeni turnuva takibi bir `api` satırıdır (leagues.txt'e yazılmaz): FX-13'ten önce
    # indirmeler yalnızca leagues.txt'i okuduğu için bu takip hiç indirilmezdi
    loaded = deps.loaded_settings()
    monkeypatch.setattr(deps, "loaded_settings",
                        lambda: SimpleNamespace(settings=loaded.settings, config_file="/etc/sofascore.toml"))
    added = data(client.post("/api/v1/follows", json={"kind": "tournament", "entity_id": 4242, "name": "New Cup"}),
                 201)
    assert added["origin"] == "api"
    ended(data(client.post("/api/v1/jobs", json={"kind": "sync"}), 202)["id"])
    assert seen and 4242 in seen[0]
