"""SQLite-backed scrape job store (one active job at a time)."""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_db_path(data_dir: str) -> str:
    meta = os.path.join(data_dir, ".meta")
    os.makedirs(meta, exist_ok=True)
    return os.path.join(meta, "jobs.db")


class JobStoreConflict(RuntimeError):
    """İstenen işlem şu anki iş durumuyla çakışıyor; web katmanı bunu 409'a çevirir (bkz. app.py)."""

    code = "conflict"


class JobRunningError(JobStoreConflict):
    """Bir indirme işi çalışırken veri dizinine dokunan işlem istendi."""

    code = "job_running"

    def __init__(self) -> None:
        super().__init__("A download job is running; stop it or wait until it finishes, then try again.")


class DataOperationRunningError(JobStoreConflict):
    """Yedekleme/silme gibi bir veri işlemi sürerken iş başlatmak ya da ikinci bir işlem istendi."""

    code = "data_operation_running"

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"Another data operation ({operation}) is in progress; try again when it finishes.")


class JobStore:
    """Thread-safe job persistence + in-memory mirror for SSE."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._active_id: Optional[str] = None
        # Süren veri işleminin adı (clear, backup, ...); doluyken yeni iş başlatılamaz
        self._exclusive: Optional[str] = None
        self._mirror: Dict[str, Any] = self._idle_mirror()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()
        self.mark_stale_running_interrupted()

    @staticmethod
    def _idle_mirror() -> Dict[str, Any]:
        return {
            "job_id": None,
            "is_running": False,
            "status": "Idle",
            "progress": 0,
            "current_task": "",
            "current_batch": "",
            "matches_total": 0,
            "matches_done": 0,
            "matches_failed": 0,
            "schedule_empty_seasons": 0,
            "circuit_breaker_triggered": False,
            "circuit_breaker_reason": None,
            "eta_seconds": None,
            "started_at": None,
            "finished_at": None,
            "cancel_requested": False,
            "log": [],
            "payload": None,
            "result": None,
            # JobProgress.detail(): aşama, sayaç, hatalar, bekleme (yalnızca canlı yansıda)
            "detail": None,
        }

    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        # `with sqlite3.connect()` yalnızca commit eder, kapatmaz: bağlantıyı burada kapat
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        progress INTEGER NOT NULL DEFAULT 0,
                        current_task TEXT NOT NULL DEFAULT '',
                        payload_json TEXT,
                        log_json TEXT NOT NULL DEFAULT '[]',
                        result_json TEXT,
                        started_at TEXT,
                        finished_at TEXT,
                        cancel_requested INTEGER NOT NULL DEFAULT 0,
                        matches_total INTEGER NOT NULL DEFAULT 0,
                        matches_done INTEGER NOT NULL DEFAULT 0,
                        matches_failed INTEGER NOT NULL DEFAULT 0,
                        schedule_empty_seasons INTEGER NOT NULL DEFAULT 0,
                        circuit_breaker_triggered INTEGER NOT NULL DEFAULT 0,
                        circuit_breaker_reason TEXT,
                        eta_seconds REAL,
                        current_batch TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                conn.commit()

    def mark_stale_running_interrupted(self) -> int:
        """On process start: any running/queued job becomes interrupted."""
        with self._lock:
            now = _utc_now()
            with self._conn() as conn:
                cur = conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'interrupted',
                        finished_at = ?,
                        current_task = COALESCE(NULLIF(current_task, ''), 'Interrupted by server restart')
                    WHERE status IN ('running', 'queued')
                    """,
                    (now,),
                )
                conn.commit()
                n = cur.rowcount or 0
            self._active_id = None
            self._mirror = self._idle_mirror()
            return n

    @contextlib.contextmanager
    def exclusive(self, operation: str) -> Iterator[None]:
        """
        Veri dizinine dokunan işlemler (silme, yedek, DATA_DIR değişimi, lig silme) için tek kişilik
        yuva. İş çalışıyorsa JobRunningError, başka bir işlem sürüyorsa DataOperationRunningError
        fırlatır. Kontrol ile yuvanın alınması aynı kilit altında olduğundan, işlem sürerken
        create_running de reddedilir: "iş yok" kontrolünden sonra araya iş giremez.
        """
        with self._lock:
            if self._mirror.get("is_running"):
                raise JobRunningError()
            if self._exclusive is not None:
                raise DataOperationRunningError(self._exclusive)
            self._exclusive = operation
        try:
            yield
        finally:
            with self._lock:
                self._exclusive = None

    def rebind(self, db_path: str) -> bool:
        """
        Depoyu başka bir veritabanı dosyasına taşır (DATA_DIR değişince iş geçmişi yeni dizini izler).
        Çalışan iş varken yapılamaz. Yol aynıysa hiçbir şey yapmaz ve False döner.
        """
        with self._lock:
            if self._mirror.get("is_running"):
                raise JobRunningError()
            if os.path.abspath(db_path) == os.path.abspath(self.db_path):
                return False
            previous = self.db_path
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
            self.db_path = db_path
            try:
                self._init_db()
                # Bu süreçte çalışan iş yok: yeni dizindeki "running" satırları eski bir çöküşten kalmadır.
                # Yansı da boşa döner; önceki dizinin son işi yeni dizinin işi gibi görünmez.
                self.mark_stale_running_interrupted()
            except Exception:
                self.db_path = previous
                raise
            return True

    def create_running(self, payload: Any) -> str:
        job_id = str(uuid.uuid4())
        now = _utc_now()
        payload_json = json.dumps(payload, default=str)
        with self._lock:
            if self._exclusive is not None:
                # Silme/yedek sürerken başlayan iş, silinen dizine yazar ya da yarım yedeğe girer
                raise DataOperationRunningError(self._exclusive)
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO jobs (id, status, progress, current_task, payload_json, log_json, started_at)
                    VALUES (?, 'running', 0, 'Starting…', ?, '[]', ?)
                    """,
                    (job_id, payload_json, now),
                )
                conn.commit()
            self._active_id = job_id
            self._mirror = self._idle_mirror()
            self._mirror.update(
                {
                    "job_id": job_id,
                    "is_running": True,
                    "status": "Running",
                    "current_task": "Starting…",
                    "started_at": now,
                    "payload": payload if isinstance(payload, dict) else json.loads(payload_json),
                    "log": [],
                }
            )
        return job_id

    def request_cancel(self) -> bool:
        with self._lock:
            if not self._mirror.get("is_running") or not self._active_id:
                return False
            self._mirror["cancel_requested"] = True
            self._mirror["current_task"] = "Cancellation requested..."
            with self._conn() as conn:
                conn.execute(
                    "UPDATE jobs SET cancel_requested = 1, current_task = ? WHERE id = ?",
                    ("Cancellation requested...", self._active_id),
                )
                conn.commit()
            return True

    def cancel_requested(self) -> bool:
        with self._lock:
            return bool(self._mirror.get("cancel_requested"))

    def update(
        self,
        *,
        status: Optional[str] = None,
        progress: Optional[int] = None,
        current_task: Optional[str] = None,
        append_log: Optional[str] = None,
        matches_total: Optional[int] = None,
        matches_done: Optional[int] = None,
        matches_failed: Optional[int] = None,
        schedule_empty_seasons: Optional[int] = None,
        circuit_breaker_triggered: Optional[bool] = None,
        circuit_breaker_reason: Optional[str] = None,
        eta_seconds: Optional[float] = None,
        current_batch: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        detail: Optional[Dict[str, Any]] = None,
        finished: bool = False,
    ) -> None:
        with self._lock:
            job_id = self._active_id
            if not job_id:
                return
            m = self._mirror
            if status is not None:
                m["status"] = status
            if progress is not None:
                m["progress"] = int(progress)
            if current_task is not None:
                m["current_task"] = current_task
            if append_log:
                log = list(m.get("log") or [])
                log.append(append_log)
                if len(log) > 50:
                    log = log[-50:]
                m["log"] = log
            if matches_total is not None:
                m["matches_total"] = matches_total
            if matches_done is not None:
                m["matches_done"] = matches_done
            if matches_failed is not None:
                m["matches_failed"] = matches_failed
            if schedule_empty_seasons is not None:
                m["schedule_empty_seasons"] = schedule_empty_seasons
            if circuit_breaker_triggered is not None:
                m["circuit_breaker_triggered"] = circuit_breaker_triggered
            if circuit_breaker_reason is not None:
                m["circuit_breaker_reason"] = circuit_breaker_reason
            if eta_seconds is not None:
                m["eta_seconds"] = eta_seconds
            if current_batch is not None:
                m["current_batch"] = current_batch
            if result is not None:
                m["result"] = result
            if detail is not None:
                m["detail"] = detail

            db_status = m["status"]
            if finished:
                m["is_running"] = False
                m["finished_at"] = _utc_now()
                if db_status == "Running":
                    db_status = "completed"
                    m["status"] = "Completed"
                elif db_status == "Failed":
                    db_status = "failed"
                elif db_status == "Cancelled" or m.get("cancel_requested"):
                    db_status = "cancelled"
                    m["status"] = "Cancelled"
                else:
                    # Completed / Failed already humanized
                    low = str(db_status).lower()
                    if low == "completed":
                        db_status = "completed"
                    elif low == "failed":
                        db_status = "failed"
                    elif low == "cancelled":
                        db_status = "cancelled"

            with self._conn() as conn:
                conn.execute(
                    """
                    UPDATE jobs SET
                        status = ?,
                        progress = ?,
                        current_task = ?,
                        log_json = ?,
                        result_json = ?,
                        finished_at = ?,
                        cancel_requested = ?,
                        matches_total = ?,
                        matches_done = ?,
                        matches_failed = ?,
                        schedule_empty_seasons = ?,
                        circuit_breaker_triggered = ?,
                        circuit_breaker_reason = ?,
                        eta_seconds = ?,
                        current_batch = ?
                    WHERE id = ?
                    """,
                    (
                        db_status if finished else "running",
                        int(m["progress"]),
                        m["current_task"],
                        json.dumps(m.get("log") or []),
                        json.dumps(m.get("result")) if m.get("result") is not None else None,
                        m.get("finished_at"),
                        1 if m.get("cancel_requested") else 0,
                        int(m.get("matches_total") or 0),
                        int(m.get("matches_done") or 0),
                        int(m.get("matches_failed") or 0),
                        int(m.get("schedule_empty_seasons") or 0),
                        1 if m.get("circuit_breaker_triggered") else 0,
                        m.get("circuit_breaker_reason"),
                        m.get("eta_seconds"),
                        m.get("current_batch") or "",
                        job_id,
                    ),
                )
                conn.commit()

            if finished:
                self._active_id = None

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._mirror)

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        for key in ("payload_json", "log_json", "result_json"):
            raw = d.pop(key, None)
            name = key.replace("_json", "")
            if raw:
                try:
                    d[name] = json.loads(raw)
                except json.JSONDecodeError:
                    d[name] = raw
            else:
                d[name] = [] if name == "log" else None
        d["cancel_requested"] = bool(d.get("cancel_requested"))
        d["circuit_breaker_triggered"] = bool(d.get("circuit_breaker_triggered"))
        d["is_running"] = d.get("status") == "running"
        return d

    def list_jobs(self, limit: int = 20) -> List[Dict[str, Any]]:
        limit = max(1, min(100, int(limit)))
        with self._lock:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM jobs ORDER BY COALESCE(started_at, '') DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return self._row_to_dict(row) if row else None


_store: Optional[JobStore] = None
_store_lock = threading.Lock()


def get_job_store(data_dir: Optional[str] = None) -> JobStore:
    global _store
    with _store_lock:
        if _store is None:
            root = data_dir or os.getenv("DATA_DIR", "data")
            if not os.path.isabs(root):
                root = os.path.abspath(root)
            _store = JobStore(default_db_path(root))
        return _store
