"""
İşin kilidi (docs/design/02-services.md 2.8, kilit tablosu; plan maddesi P21): indirmeler ve yedek `writer`,
temizleme ve katalog yeniden kurulumu `maintenance` tutar.

  * bakım işi sürerken başka iş başlamaz (bu süreçte `job_running`, başka bir süreçte `data_operation_running`);
  * başka bir sürecin depo kopyası bakım işinin satırını bayat saymaz (kilidi tutulduğu sürece);
  * bir depo çalışan işinin kilidini başka türden bir iş için yeniden kullanmaz;
  * iş kilidi yalnızca `writer` ya da `maintenance` olabilir; `JobManager.start(lease=...)` onu geçirir.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sofascore_scraper.jobs.manager import JobManager, local_origin
from sofascore_scraper.jobs.model import JobKind
from sofascore_scraper.store import DataOperationRunningError, JobRunningError, JobStore, default_db_path, open_store


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    open_store(path)
    return path


def test_a_maintenance_job_holds_the_maintenance_lease(data_dir: Path) -> None:
    jobs = JobStore(default_db_path(str(data_dir)))
    other = JobStore(default_db_path(str(data_dir)))
    try:
        jobs.create_running({}, kind="clear", purpose="clear", lease="maintenance", replace_running=False)
        store = open_store(data_dir)
        holder = store.lease_holder("maintenance")
        assert holder is not None and holder.purpose == "clear" and store.lease_holder("writer") is None
        with pytest.raises(JobRunningError):
            jobs.create_running({}, kind="sync", replace_running=False)
        with pytest.raises(DataOperationRunningError):
            other.create_running({}, kind="sync", replace_running=False)
        assert other.reap_stale() == 0
        jobs.update(status="Completed", finished=True)
        assert store.lease_holder("maintenance") is None
        other.create_running({}, kind="sync", replace_running=False)
        other.update(status="Completed", finished=True)
    finally:
        jobs.close()
        other.close()


def test_a_running_job_does_not_lend_its_lease_to_a_job_of_another_kind(data_dir: Path) -> None:
    jobs = JobStore(default_db_path(str(data_dir)))
    try:
        jobs.create_running({}, kind="sync", replace_running=True)
        with pytest.raises(JobRunningError):
            jobs.create_running({}, kind="clear", purpose="clear", lease="maintenance", replace_running=True)
        jobs.update(status="Completed", finished=True)
        with pytest.raises(ValueError):
            jobs.create_running({}, kind="sync", lease="live")
    finally:
        jobs.close()


def test_the_job_manager_passes_the_lease(data_dir: Path) -> None:
    jobs = JobStore(default_db_path(str(data_dir)))
    try:
        manager = JobManager(jobs, cancel_poll=0.02, heartbeat=0.05)
        seen = []

        def body(handle: object) -> None:
            seen.append(open_store(data_dir).lease_holder("maintenance"))

        job = manager.submit(JobKind.REBUILD, {"mode": "auto"}, body, origin=local_origin("api"), background=False,
                             lease="maintenance", lease_purpose="rebuild")
        assert job.state.value == "succeeded"
        assert seen[0] is not None and seen[0].purpose == "rebuild"
        assert open_store(data_dir).lease_holder("maintenance") is None
    finally:
        jobs.close()
