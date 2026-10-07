"""
İşin bitiş penceresi: iş satırını bitirdikten sonra da depoyu kullanır (`job.finished` akış olayı, bitmiş işin
geri okunması). Bu son erişimler bitene kadar depo onun altından kapatılamaz ve taşınamaz:

  * veri klasörü değişimi (POST /api/settings, `JobStore.rebind`) 409 job_running ile reddedilir;
  * `JobStore.close()` işin bitişini bekler;
  * aynı süreçte hemen ardından başlatılan yeni iş reddedilmez, bitişin sonunu bekler.

Pencere, `job.finished` akış yazmasının içinde bir Event'te bekletilerek açık tutulur. İlk iki senaryo ayrı bir
süreçte çalışır: düzeltme bozulursa bağlantı iş thread'inin altından kapanır ve Python 3.14'te süreç çökebilir
(sqlite3_last_insert_rowid'de segfault); çöküş test oturumunu öldürmez, testi düşürür. Tümü çevrimdışı.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest

from sofascore_scraper.jobs.manager import STREAM_JOB_FINISHED, JobManager, local_origin
from sofascore_scraper.jobs.model import JobKind, JobState
from sofascore_scraper.store import JobStore
from sofascore_scraper.store.jobs import default_db_path
from sofascore_scraper.store.streams import StreamLog

ROOT = Path(__file__).resolve().parents[1]

# Alt süreçte ortak kısım: `job.finished` akış yazması bir Event'te bekler; "Jobs" logunun uyarı ve hataları
# toplanır (yazılamayan akış olayı, arka plan işinin hatası)
_PRELUDE = r'''
import json, logging, os, sqlite3, sys, threading
sys.path.insert(0, sys.argv[2]); sys.path.insert(0, os.path.join(sys.argv[2], "tests"))
import conftest  # geçici DATA_DIR, .env ve config; sofascore_scraper.* bundan sonra yüklenir
from sofascore_scraper.jobs.manager import STREAM_JOB_FINISHED, JobManager, local_origin
from sofascore_scraper.jobs.model import JobKind
from sofascore_scraper.store.streams import StreamLog

tmp = sys.argv[1]
entered, release = threading.Event(), threading.Event()
_append = StreamLog.append

def append(self, stream, events):
    if any(event.type == STREAM_JOB_FINISHED for event in events):
        entered.set()
        release.wait(60)
    return _append(self, stream, events)

StreamLog.append = append
problems = []

class Collect(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.WARNING:
            problems.append(record.getMessage())

logging.getLogger("Jobs").addHandler(Collect())

def finished_events(db_path, job_id):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT payload_json FROM stream_events WHERE stream = 'job' AND type = ?",
                            (STREAM_JOB_FINISHED,)).fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows if json.loads(row[0]).get("job_id") == job_id]

def join_jobs():
    for thread in threading.enumerate():
        if thread.name.startswith("job-") and thread is not threading.current_thread():
            thread.join(60)
'''

_SETTINGS_ROUTE = r'''
from pathlib import Path
from fastapi.testclient import TestClient
from sofascore_scraper.web import deps
from sofascore_scraper.web.app import app
from sofascore_scraper.web.api import legacy as api_mod
from sofascore_scraper.web.api import legacy as settings_mod

settings_mod._REPO_ROOT = Path(tmp)  # doğrulayıcı tmp altındaki klasörü kabul eder
store = deps.job_store()
old_db = store.db_path
manager = JobManager(store, cancel_poll=0.02, heartbeat=0.05)
job = manager.submit(JobKind.FETCH, {"mode": "full"}, lambda handle: None, origin=local_origin("api"),
                     background=True)
assert entered.wait(60), "the job never reached its job.finished stream write"
client = TestClient(app)
moved = os.path.join(tmp, "moved")
during = client.post("/api/settings", json={"data_dir": moved})
release.set()
join_jobs()
after = client.post("/api/settings", json={"data_dir": moved})
print(json.dumps({
    "during_status": during.status_code,
    "during_code": (during.json().get("detail") or {}).get("code") if during.status_code != 200 else None,
    "after_status": after.status_code,
    "finished_events": len(finished_events(old_db, job.id)),
    "problems": problems,
}))
'''

_JOB_STORE_CLOSE = r'''
from sofascore_scraper.store import JobStore
from sofascore_scraper.store.jobs import default_db_path

db = default_db_path(os.path.join(tmp, "data"))
store = JobStore(db)
manager = JobManager(store, cancel_poll=0.02, heartbeat=0.05)
results = {}
job = manager.submit(JobKind.FETCH, {"mode": "full"}, lambda handle: None, origin=local_origin("library"),
                     background=True)
assert entered.wait(60), "the job never reached its job.finished stream write"
closer = threading.Thread(target=store.close, name="closer")
closer.start()
closer.join(0.5)
results["close_waited"] = closer.is_alive()
release.set()
closer.join(60)
join_jobs()
results["closed"] = not closer.is_alive()
results["finished_events"] = len(finished_events(db, job.id))
results["problems"] = problems
print(json.dumps(results))
'''


def _run(scenario: str, tmp_path: Path) -> Dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONFAULTHANDLER"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", _PRELUDE + textwrap.dedent(scenario), str(tmp_path), str(ROOT)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180,
    )
    # Çöküş (ör. -11, SIGSEGV) ya da yakalanmamış hata burada görünür
    assert proc.returncode == 0, f"exit {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr[-4000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_a_data_folder_change_is_refused_until_the_finishing_job_is_done(tmp_path: Path) -> None:
    """Ayarlardan veri klasörü değişimi, satırı bitmiş ama akış olayını yazmakta olan işe 409 alır."""
    result = _run(_SETTINGS_ROUTE, tmp_path)
    assert (result["during_status"], result["during_code"]) == (409, "job_running"), result
    # İş eski dizinde sonuna kadar bitti: akış olayı yazıldı, geri okuma başarılı, hiçbir uyarı yok
    assert result["finished_events"] == 1, result
    assert result["problems"] == [], result
    # İş gerçekten bitince değişim yapılabilir
    assert result["after_status"] == 200, result


def test_closing_the_job_store_waits_for_the_finishing_job(tmp_path: Path) -> None:
    """`JobStore.close()`, işin son depo erişimleri bitene kadar bekler; sonra kapatır."""
    result = _run(_JOB_STORE_CLOSE, tmp_path)
    assert result["close_waited"] is True, result
    assert result["closed"] is True, result
    assert result["finished_events"] == 1, result
    assert result["problems"] == [], result


@pytest.fixture
def store(tmp_path: Path) -> Iterator[JobStore]:
    job_store = JobStore(default_db_path(str(tmp_path / "data")))
    try:
        yield job_store
    finally:
        job_store.close()


def test_a_job_started_while_the_previous_one_finishes_waits_instead_of_failing(
        store: JobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Önceki işin satırı bitmişse yeni iş 409 almaz: bitişin sonunu bekler ve başlar (işi bitmiş görüp hemen yenisini
    başlatan istemci bugünkü gibi çalışır).
    """
    entered, release = threading.Event(), threading.Event()
    original = StreamLog.append

    def append(self: StreamLog, stream: str, events: Any) -> Any:
        if any(event.type == STREAM_JOB_FINISHED for event in events):
            entered.set()
            release.wait(30)
        return original(self, stream, events)

    monkeypatch.setattr(StreamLog, "append", append)
    manager = JobManager(store, cancel_poll=0.02, heartbeat=0.05)
    first = manager.submit(JobKind.FETCH, {}, lambda handle: None, origin=local_origin("api"), background=True)
    assert entered.wait(30)
    first_row = manager.get(first.id)
    assert first_row is not None and first_row.state is JobState.SUCCEEDED  # satır bitti, son erişimler sürüyor

    started: Dict[str, Any] = {}
    starter = threading.Thread(target=lambda: started.update(job=manager.start(
        JobKind.FETCH, {}, origin=local_origin("api"))))
    starter.start()
    starter.join(0.3)
    assert starter.is_alive() and not started  # bitişin sonunu bekliyor
    release.set()
    starter.join(30)
    second = started["job"]
    assert second.state is JobState.RUNNING and store.snapshot()["job_id"] == second.id
    # Önceki işin kilidi bırakılırken yeni işinki bırakılmadı: yeni iş kilidi hâlâ tutuyor
    assert store.writer_busy()
    for thread in threading.enumerate():
        if thread.name.startswith("job-") and thread is not threading.current_thread():
            thread.join(30)
    manager.run(second.id, lambda handle: None)
    assert not store.writer_busy()
