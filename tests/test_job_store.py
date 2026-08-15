"""JobStore unit checks (stdlib assert)."""
from __future__ import annotations

import os
import tempfile

from src.web.jobs import JobStore


def test_job_lifecycle_and_interrupt(tmp_path=None):
    root = tmp_path if tmp_path is not None else tempfile.mkdtemp()
    if hasattr(root, "__fspath__"):
        root = str(root)
    db = os.path.join(root, "jobs.db")
    store = JobStore(db)
    jid = store.create_running({"mode": "full", "selections": []})
    assert store.snapshot()["is_running"] is True
    assert store.snapshot()["job_id"] == jid
    store.update(status="Running", progress=40, current_task="Working", append_log="[Running] Working")
    assert store.snapshot()["progress"] == 40
    store.update(status="Completed", progress=100, current_task="Done", finished=True)
    assert store.snapshot()["is_running"] is False
    jobs = store.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["id"] == jid

    # Simulate crash mid-run
    store2 = JobStore(db)  # marks interrupted — but no running left
    jid2 = store2.create_running({"mode": "details"})
    store2.update(progress=10, current_task="mid")
    # New process
    store3 = JobStore(db)
    assert store3.snapshot()["is_running"] is False
    found = store3.get_job(jid2)
    assert found is not None
    assert found["status"] == "interrupted"


def test_cancel_flag():
    root = tempfile.mkdtemp()
    store = JobStore(os.path.join(root, "j.db"))
    store.create_running({})
    assert store.request_cancel() is True
    assert store.cancel_requested() is True
    assert store.request_cancel() is True  # still running until finished


if __name__ == "__main__":
    test_job_lifecycle_and_interrupt()
    test_cancel_flag()
    print("jobs ok")
