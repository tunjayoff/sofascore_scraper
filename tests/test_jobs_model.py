"""sofascore_scraper/jobs paketi: iş modeli, durum eşlemesi, taşınan ilerleme modülü ve katman kuralı."""
from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path
from typing import List, Optional

import pytest

import sofascore_scraper.jobs as jobs_pkg
import sofascore_scraper.jobs.progress as new_progress
from sofascore_scraper.jobs.model import (
    LEGACY_STATUS_TO_STATE,
    ErrorInfo,
    Job,
    JobKind,
    JobState,
    Origin,
    job_state_from_status,
)
from sofascore_scraper.store import JobStore

JOBS_DIR = Path(jobs_pkg.__file__).parent


# --- taşıma ve eski yol ------------------------------------------------------------


def test_the_old_import_paths_are_gone():
    """`sofascore_scraper.web.progress` ve `sofascore_scraper.web.jobs` 3.1'de kalktı (P30)."""
    web = Path(jobs_pkg.__file__).parent.parent / "web"
    assert not (web / "progress.py").exists() and not (web / "jobs.py").exists()


def test_job_progress_lives_in_the_jobs_package():
    assert new_progress.JobProgress.__module__ == "sofascore_scraper.jobs.progress"
    assert jobs_pkg.JobProgress is new_progress.JobProgress
    assert jobs_pkg.PHASE_WEIGHTS is new_progress.PHASE_WEIGHTS
    assert jobs_pkg.MAX_FAILED_LISTED == new_progress.MAX_FAILED_LISTED


# --- katman kuralı (docs/design/02-services.md 2.1, madde 5) -------------------------


def _imported_modules(path: Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def test_jobs_package_imports_no_face_and_no_sql():
    # Dosya erişimi içe aktarmalardan güvenle anlaşılamaz (os, pid okumak için de gerekir); burada
    # yalnızca yüz modülleri ve sqlite3 yasaklanır.
    forbidden_prefixes = ("sofascore_scraper.web", "sofascore_scraper.ui", "sofascore_scraper.cli", "sofascore_scraper.SofaScoreUi")
    forbidden_modules = {"sqlite3"}
    files = sorted(JOBS_DIR.glob("*.py"))
    assert files, "sofascore_scraper/jobs is empty"
    for path in files:
        for module in _imported_modules(path):
            assert not module.startswith(forbidden_prefixes), f"{path.name} imports {module}"
            assert module.split(".")[0] not in forbidden_modules, f"{path.name} imports {module}"


# --- enum'lar ----------------------------------------------------------------------


def test_job_kinds_and_states_match_the_design():
    assert [k.value for k in JobKind] == [
        "sync", "fetch", "refresh", "export", "backup", "restore", "clear", "migrate", "rebuild",
    ]
    assert [s.value for s in JobState] == [
        "queued", "running", "succeeded", "partial", "failed", "cancelled", "interrupted",
    ]


@pytest.mark.parametrize("member", [*JobKind, *JobState])
def test_enum_members_are_plain_strings_everywhere(member):
    # str, format ve JSON aynı değeri verir (Python 3.10 ile 3.11+ arasında fark kalmaz)
    assert member == member.value
    assert str(member) == member.value
    assert f"{member}" == member.value
    assert "{:>12}".format(member) == member.value.rjust(12)
    assert json.dumps({"x": member}) == json.dumps({"x": member.value})
    assert type(member)(member.value) is member


def test_only_queued_and_running_are_not_terminal():
    assert {s for s in JobState if not s.terminal} == {JobState.QUEUED, JobState.RUNNING}


# --- bugünkü durum metinlerinden JobState'e -----------------------------------------


def test_legacy_table_covers_exactly_todays_status_strings():
    assert set(LEGACY_STATUS_TO_STATE) == {"running", "queued", "completed", "failed", "cancelled", "interrupted"}
    with pytest.raises(TypeError):
        LEGACY_STATUS_TO_STATE["completed"] = JobState.FAILED  # type: ignore[index]


@pytest.mark.parametrize(
    "status, expected",
    [
        ("running", JobState.RUNNING),
        ("queued", JobState.QUEUED),
        ("completed", JobState.SUCCEEDED),
        ("failed", JobState.FAILED),
        ("cancelled", JobState.CANCELLED),
        ("interrupted", JobState.INTERRUPTED),
        # canlı yansıdaki biçimler (JobStore.snapshot()["status"])
        ("Running", JobState.RUNNING),
        ("Completed", JobState.SUCCEEDED),
        ("Failed", JobState.FAILED),
        ("Cancelled", JobState.CANCELLED),
        ("  COMPLETED \n", JobState.SUCCEEDED),
    ],
)
def test_todays_status_strings_map_to_states(status, expected):
    assert job_state_from_status(status) is expected


@pytest.mark.parametrize("status", ["Idle", "idle", "", "   ", "done", "Completed!", None, 1, b"running"])
def test_idle_and_unknown_statuses_map_to_none(status):
    assert job_state_from_status(status) is None
    assert job_state_from_status(status, breaker_triggered=True) is None


def test_breaker_stop_turns_completed_into_partial_and_nothing_else():
    assert job_state_from_status("completed", breaker_triggered=True) is JobState.PARTIAL
    assert job_state_from_status("Completed", breaker_triggered=True) is JobState.PARTIAL
    for status, state in LEGACY_STATUS_TO_STATE.items():
        if status == "completed":
            continue
        assert job_state_from_status(status, breaker_triggered=True) is state


@pytest.mark.parametrize("state", list(JobState))
def test_new_state_names_map_to_themselves(state):
    assert job_state_from_status(state.value) is state
    assert job_state_from_status(state) is state
    assert job_state_from_status(state.value.upper()) is state


def test_every_state_is_reachable_from_todays_rows():
    reached = {job_state_from_status(s, breaker_triggered=b) for s in LEGACY_STATUS_TO_STATE for b in (False, True)}
    assert reached == set(JobState)


def _row_state(store: JobStore, job_id: str) -> Optional[JobState]:
    row = store.get_job(job_id)
    assert row is not None
    return job_state_from_status(row["status"], breaker_triggered=row["circuit_breaker_triggered"])


def test_rows_written_by_todays_job_store_map_as_designed(tmp_path):
    """Eşleme, JobStore'un gerçekten yazdığı satırlar üzerinde sınanır (sofascore_scraper/store/jobs.py)."""
    db = str(tmp_path / "jobs.db")
    store = JobStore(db)

    running = store.create_running({"mode": "full"})
    assert _row_state(store, running) is JobState.RUNNING
    assert job_state_from_status(store.snapshot()["status"]) is JobState.RUNNING
    store.update(status="Completed", progress=100, finished=True)
    assert _row_state(store, running) is JobState.SUCCEEDED
    assert job_state_from_status(store.snapshot()["status"]) is JobState.SUCCEEDED

    # Devre kesici durdurdu: bugün "completed" yazılır (fetch_job.py), yeni modelde partial
    stopped = store.create_running({"mode": "full"})
    store.update(circuit_breaker_triggered=True, circuit_breaker_reason="429")
    store.update(status="Completed", progress=100, finished=True)
    assert store.get_job(stopped)["status"] == "completed"
    assert _row_state(store, stopped) is JobState.PARTIAL

    failed = store.create_running({"mode": "full"})
    store.update(circuit_breaker_triggered=True, circuit_breaker_reason="403")
    store.update(status="Failed", progress=10, finished=True)
    assert _row_state(store, failed) is JobState.FAILED

    cancelled = store.create_running({"mode": "full"})
    assert store.request_cancel() is True
    store.update(status="Cancelled", progress=10, finished=True)
    assert _row_state(store, cancelled) is JobState.CANCELLED
    assert job_state_from_status(store.snapshot()["status"]) is JobState.CANCELLED

    crashed = store.create_running({"mode": "details"})
    store.close()  # süreç öldü: `writer` kilidi düştü, satır "running" kaldı
    reopened = JobStore(db)  # süreç yeniden başladı: kilidi boşta olan çalışan satır interrupted olur
    assert _row_state(reopened, crashed) is JobState.INTERRUPTED
    assert job_state_from_status(reopened.snapshot()["status"]) is None  # Idle: iş yok

    states = {
        job_state_from_status(r["status"], breaker_triggered=r["circuit_breaker_triggered"])
        for r in reopened.list_jobs()
    }
    assert states == {
        JobState.SUCCEEDED, JobState.PARTIAL, JobState.FAILED, JobState.CANCELLED, JobState.INTERRUPTED,
    }


# --- Job ---------------------------------------------------------------------------


def test_job_fields_follow_the_design_order():
    assert [f.name for f in dataclasses.fields(Job)] == [
        "id", "kind", "state", "origin", "spec", "progress", "result", "error",
        "created_at", "started_at", "finished_at", "heartbeat_at", "cancel_requested",
    ]


def test_job_defaults_and_immutability():
    job = Job(
        id="01J0000000000000000000000", kind=JobKind.FETCH, state=JobState.RUNNING,
        origin=Origin(face="api"), spec={"mode": "full"},
    )
    assert job.progress is None and job.result is None and job.error is None
    assert job.created_at is None and job.started_at is None and job.finished_at is None
    assert job.heartbeat_at is None
    assert job.cancel_requested is False
    assert job.origin == Origin(face="api", pid=None, host=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        job.state = JobState.FAILED  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        job.origin.face = "cli"  # type: ignore[misc]

    finished = dataclasses.replace(
        job, state=JobState.FAILED, error=ErrorInfo(code="storage_error", message="disk full"),
        finished_at="2026-10-01T12:00:00+00:00",
    )
    assert finished.state.terminal and not job.state.terminal
    assert finished.error == ErrorInfo(code="storage_error", message="disk full", details=None)
    assert job.state is JobState.RUNNING


def test_job_serialises_to_plain_json():
    job = Job(
        id="j1", kind=JobKind.SYNC, state=JobState.PARTIAL, origin=Origin(face="cli", pid=42, host="box"),
        spec={"follows": "all"}, result={"failed_count": 2},
        error=ErrorInfo(code="rate_limited", message="429", details={"reason": "429"}),
        started_at="2026-10-01T12:00:00+00:00", heartbeat_at=1790856000000, cancel_requested=True,
    )
    data = json.loads(json.dumps(dataclasses.asdict(job)))
    assert data["kind"] == "sync" and data["state"] == "partial"
    assert data["origin"] == {"face": "cli", "pid": 42, "host": "box"}
    assert data["error"] == {"code": "rate_limited", "message": "429", "details": {"reason": "429"}}
    assert data["cancel_requested"] is True and data["heartbeat_at"] == 1790856000000


def test_package_exports_the_model():
    names = (
        "Job", "JobKind", "JobState", "Origin", "OriginFace", "ErrorInfo",
        "job_state_from_status", "LEGACY_STATUS_TO_STATE", "JobProgress",
    )
    for name in names:
        assert name in jobs_pkg.__all__
        assert getattr(jobs_pkg, name) is not None
