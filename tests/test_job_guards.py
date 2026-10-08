"""
İş deposunun korumaları (JobStore): veri işlemi yuvası, iş sürerken alınamayan yuva, veri dizini değişiminde
deponun taşınması (`rebind`). Tümü çevrimdışı.

Aynı kuralların HTTP yüzü API v1'dedir (tests/test_api_v1_data_jobs.py, test_api_v1_settings.py,
test_api_v1_follows.py); 2.x'in `/api` yollarındaki kopyaları 3.1'de yollarla birlikte kalktı (P30).
"""
from __future__ import annotations

import sqlite3

import pytest

from sofascore_scraper.web.jobs import (
    DataOperationRunningError,
    JobRunningError,
    JobStore,
)


# --- JobStore ---


def test_exclusive_slot_rules(tmp_path):
    s = JobStore(str(tmp_path / "jobs.db"))
    with s.exclusive("clear"):
        with pytest.raises(DataOperationRunningError) as busy:
            s.create_running({})
        assert busy.value.code == "data_operation_running" and busy.value.operation == "clear"
        with pytest.raises(DataOperationRunningError):
            with s.exclusive("backup"):
                pass
    assert s.snapshot()["is_running"] is False

    # Gövde hata verse de yuva bırakılır
    with pytest.raises(ValueError):
        with s.exclusive("clear"):
            raise ValueError("boom")
    s.create_running({})
    with pytest.raises(JobRunningError) as running:
        with s.exclusive("clear"):
            pytest.fail("iş çalışırken yuva alınmamalı")
    assert running.value.code == "job_running"
    # Reddedilen deneme yuvayı kilitli bırakmaz
    s.update(status="Completed", finished=True)
    with s.exclusive("clear"):
        pass


def test_rebind_switches_database_and_resets_the_mirror(tmp_path):
    a, b = str(tmp_path / "a" / "jobs.db"), str(tmp_path / "b" / "jobs.db")
    s = JobStore(a)
    first = s.create_running({"n": 1})
    with pytest.raises(JobRunningError):
        s.rebind(b)
    assert s.db_path == a
    s.update(status="Completed", finished=True)

    assert s.rebind(a) is False  # aynı yol: yansıya dokunulmaz
    assert s.snapshot()["job_id"] == first

    assert s.rebind(b) is True
    assert s.db_path == b and s.list_jobs() == []
    assert s.snapshot()["job_id"] is None and s.snapshot()["status"] == "Idle"
    second = s.create_running({"n": 2})
    s.update(status="Completed", finished=True)
    assert [j["id"] for j in s.list_jobs()] == [second]

    assert s.rebind(a) is True
    assert [j["id"] for j in s.list_jobs()] == [first]


def test_rebind_marks_stale_running_rows_in_the_target(tmp_path):
    """Hedef dizinde çökmüş bir sunucudan kalan "running" satırı, açılıştaki gibi interrupted olur."""
    b = str(tmp_path / "b" / "jobs.db")
    crashed = JobStore(b)
    stale = crashed.create_running({})
    crashed.close()  # süreç öldü: `writer` kilidi düştü, satır "running" kaldı
    s = JobStore(str(tmp_path / "a" / "jobs.db"))
    assert s.rebind(b) is True
    assert s.get_job(stale)["status"] == "interrupted"
    assert s.snapshot()["is_running"] is False


def test_rebind_keeps_the_old_database_when_the_new_one_fails(tmp_path):
    a = str(tmp_path / "a" / "jobs.db")
    s = JobStore(a)
    job = s.create_running({})
    s.update(status="Completed", finished=True)
    (tmp_path / "file").write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        s.rebind(str(tmp_path / "file" / "jobs.db"))
    not_a_db = tmp_path / "c" / "jobs.db"
    not_a_db.parent.mkdir()
    not_a_db.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(sqlite3.Error):
        s.rebind(str(not_a_db))
    assert s.db_path == a
    assert [j["id"] for j in s.list_jobs()] == [job]
