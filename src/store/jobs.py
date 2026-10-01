"""
İş deposu: indirme işlerinin geçmişi (state.db'nin `jobs` tablosu) ve çalışan işin bellek içi yansısı.

2.x'te src/web/jobs.py'de, `.meta/jobs.db` üzerinde duruyordu; yöntemler ve hata sınıfları aynen taşındı
(docs/design/01-storage.md bölüm 2.3 ve 3.1). src/web/jobs.py artık buradaki adları yeniden dışa açar.

Dosya değişti, davranış değişmedi:
  * Satırlar `.meta/state.db`'ye yazılır. `.meta/jobs.db`'ye 3.x dokunmaz: satırları bir kez, salt okunur
    içe aktarılır (`meta.imported_jobs_db` kaydı) ve dosya yerinde kalır; aynı dizinde başlatılan bir 2.x
    süreci geçmişini bulmaya devam eder.
  * Dışarıya verilen satırlar bugünkü 18 sütundur; state.db'nin yeni sütunları (`kind`, `owner`) iş
    yöneticisi gelene kadar yalnızca varsayılan değerleriyle durur.

Süreçler arası kural (bölüm 6.1): çalışan iş `writer` kilidini, veri işlemleri (`exclusive`) `maintenance`
kilidini (yedek: `writer`) tutar. Aynı veri dizinini yazan ikinci bir süreç, bu süreçteki ikinci bir iş
gibi JobRunningError / DataOperationRunningError alır. Yansı (`snapshot`) hâlâ süreç içidir.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from src.store import layout
from src.store.errors import LeaseHeld
from src.store.lease import MAINTENANCE, WRITER, Lease, LeaseManager
from src.store.state import APPLICATION_ID, BUSY_TIMEOUT_MS, StateDb

logger = logging.getLogger("Store")

META_IMPORTED_JOBS_DB = "imported_jobs_db"
_LEGACY_DB_NAME = os.path.basename(layout.LEGACY_JOBS_DB)
_LOCKS_DIR_NAME = os.path.basename(layout.LOCKS_DIR)

# Kilidin `purpose` alanı: çakışan süreç hangi hatayı vereceğini buradan anlar (`conflict_from_lease`)
JOB_PURPOSE = "job"
OPERATION_PREFIX = "op:"  # "op:clear", "op:backup", ...
# Veri dizinini değiştirmeyen, yalnızca tutarlı bir kopya isteyen işlemler `writer` alır; diğerleri
# (silme, lig silme, DATA_DIR değişimi) `maintenance` (docs/design/01-storage.md bölüm 6.1)
WRITER_OPERATIONS = frozenset({"backup"})

# 2.x jobs.db'nin sütunları, o sırayla. Okuma yöntemleri yalnızca bunları döndürür.
JOB_COLUMNS: Tuple[str, ...] = (
    "id",
    "status",
    "progress",
    "current_task",
    "payload_json",
    "log_json",
    "result_json",
    "started_at",
    "finished_at",
    "cancel_requested",
    "matches_total",
    "matches_done",
    "matches_failed",
    "schedule_empty_seasons",
    "circuit_breaker_triggered",
    "circuit_breaker_reason",
    "eta_seconds",
    "current_batch",
)
_SELECT_JOBS = f"SELECT {', '.join(JOB_COLUMNS)} FROM jobs"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_db_path(data_dir: str) -> str:
    """İş geçmişini tutan dosya: `<data_dir>/.meta/state.db`. `.meta` dizinini oluşturur."""
    path = layout.resolve(data_dir, layout.STATE_DB)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


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


def conflict_from_lease(held: LeaseHeld) -> JobStoreConflict:
    """
    LeaseHeld → web katmanının 409'a çevirdiği hata. Kilidi bir veri işlemi tutuyorsa (amaç "op:<ad>")
    DataOperationRunningError, `writer` başka bir amaçla tutuluyorsa (web işi, CLI indirmesi) JobRunningError.
    """
    purpose = held.purpose or ""
    if purpose.startswith(OPERATION_PREFIX):
        return DataOperationRunningError(purpose[len(OPERATION_PREFIX):])
    if held.name == WRITER:
        return JobRunningError()
    return DataOperationRunningError(purpose or held.name or MAINTENANCE)


def locks_dir_for(db_path: str) -> str:
    """İş deposunun kilit dizini: veritabanının yanındaki `locks/` (`.meta/state.db` için `.meta/locks`)."""
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), _LOCKS_DIR_NAME)


def _read_legacy_rows(legacy_path: str) -> Tuple[List[str], List[Tuple[Any, ...]]]:
    """2.x jobs.db'yi salt okunur açar; ortak sütunları ve satırları döndürür. Dosyaya hiçbir şey yazılmaz."""
    uri = f"{Path(legacy_path).resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000)
    try:
        if int(conn.execute("PRAGMA application_id").fetchone()[0]) == APPLICATION_ID:
            return [], []  # bu bir state.db (elle adlandırılmış); 2.x geçmişi değil
        present = {str(row[1]) for row in conn.execute("PRAGMA table_info(jobs)")}
        columns = [c for c in JOB_COLUMNS if c in present]
        if "id" not in columns or "status" not in columns:
            return [], []
        rows = conn.execute(f"SELECT {', '.join(columns)} FROM jobs").fetchall()
        return columns, [tuple(row) for row in rows]
    finally:
        conn.close()


def import_legacy_jobs(state: StateDb, legacy_path: str) -> Optional[int]:
    """
    2.x iş geçmişini (`.meta/jobs.db`) state.db'ye bir kez aktarır ve bunu `meta` tablosuna kaydeder.

    Aktarılan satır sayısını döndürür; aktarım daha önce yapıldıysa ya da eski dosya şu an okunamıyorsa
    None (okunamayan dosya kaydedilmez, sonraki açılışta yeniden denenir). Eski dosya yoksa bu da kaydedilir:
    aktarım state.db'nin ilk kuruluşuna aittir, sonradan beliren bir jobs.db içe alınmaz. Aynı kimlikli
    satır state.db'de varsa olduğu gibi kalır.
    """
    if state.meta_get(META_IMPORTED_JOBS_DB) is not None:
        return None
    found = os.path.isfile(legacy_path)
    columns: List[str] = []
    rows: List[Tuple[Any, ...]] = []
    if found:
        try:
            columns, rows = _read_legacy_rows(legacy_path)
        except sqlite3.Error as e:
            logger.warning("Eski iş geçmişi okunamadı, sonraki açılışta yeniden denenecek: %s: %s", legacy_path, e)
            return None
    with state.write() as conn:
        # Kilit alındıktan sonra yeniden bak: aynı anda açılan başka bir süreç aktarmış olabilir
        if conn.execute("SELECT 1 FROM meta WHERE key = ?", (META_IMPORTED_JOBS_DB,)).fetchone():
            return None
        before = conn.total_changes
        if rows:
            marks = ", ".join("?" for _ in columns)
            conn.executemany(f"INSERT OR IGNORE INTO jobs ({', '.join(columns)}) VALUES ({marks})", rows)
        imported = conn.total_changes - before
        record = {"file": _LEGACY_DB_NAME, "found": found, "rows": len(rows), "imported": imported, "at": _utc_now()}
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (META_IMPORTED_JOBS_DB, json.dumps(record)))
    if found:
        logger.info("Eski iş geçmişi içe aktarıldı: %s satır (%s)", imported, legacy_path)
    return imported


class JobStore:
    """Thread-safe job persistence + in-memory mirror for SSE."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._active_id: Optional[str] = None
        # Süren veri işleminin adı (clear, backup, ...); doluyken yeni iş başlatılamaz
        self._exclusive: Optional[str] = None
        self._mirror: Dict[str, Any] = self._idle_mirror()
        # Çalışan işin `writer` kilidi: create_running alır, iş bitince bırakılır
        self._writer: Optional[Lease] = None
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._state, self._leases = self._open(db_path)
        try:
            self.mark_stale_running_interrupted()
        except BaseException:
            self._state.close()
            raise

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

    @staticmethod
    def _open(db_path: str) -> Tuple[StateDb, LeaseManager]:
        """
        state.db'yi açar (gerekirse kurar; geçişler `maintenance` kilidi altında uygulanır), yanındaki 2.x
        jobs.db'yi bir kez içe aktarır ve dizinin kilit yöneticisini döndürür.
        """
        leases = LeaseManager(locks_dir_for(db_path))
        state = StateDb(db_path, migration_guard=leases.migration_guard)
        try:
            legacy = os.path.join(os.path.dirname(os.path.abspath(db_path)), _LEGACY_DB_NAME)
            if os.path.normcase(legacy) != os.path.normcase(os.path.abspath(db_path)):
                import_legacy_jobs(state, legacy)
        except BaseException:
            state.close()
            raise
        leases.state = state
        return state, leases

    def close(self) -> None:
        """Kilidi bırakır ve veritabanı bağlantılarını kapatır (testler ve DATA_DIR değişimi için)."""
        with self._lock:
            self._release_writer()
            self._state.close()

    def _take_writer(self) -> bool:
        """
        `writer` kilidini alır; depo zaten tutuyorsa hiçbir şey yapmaz ve False döner. Kilit başka bir
        süreçte (ya da bu süreçteki başka bir depoda) ise JobRunningError / DataOperationRunningError.
        """
        if self._writer is not None and self._writer.held:
            return False
        try:
            self._writer = self._leases.acquire(WRITER, purpose=JOB_PURPOSE)
        except LeaseHeld as held:
            raise conflict_from_lease(held) from held
        return True

    def _release_writer(self) -> None:
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.release()

    def writer_busy(self) -> bool:
        """
        Veri dizinine şu an yazan biri var mı: bu deponun çalışan işi ya da `writer` kilidini tutan başka
        bir süreç (indirme işi, CLI çalıştırması, yedek). Kilidi almaz; tek maç indirme gibi kısa yazmalar
        başlamadan önce sorar.
        """
        with self._lock:
            return bool(self._mirror.get("is_running")) or self._leases.holder(WRITER) is not None

    def mark_stale_running_interrupted(self) -> int:
        """On process start: any running/queued job becomes interrupted."""
        with self._lock:
            now = _utc_now()
            with self._state.write() as conn:
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
                n = cur.rowcount or 0
            self._active_id = None
            self._mirror = self._idle_mirror()
            self._release_writer()
            return n

    @contextlib.contextmanager
    def exclusive(self, operation: str) -> Iterator[None]:
        """
        Veri dizinine dokunan işlemler (silme, yedek, DATA_DIR değişimi, lig silme) için tek kişilik
        yuva. İş çalışıyorsa JobRunningError, başka bir işlem sürüyorsa DataOperationRunningError
        fırlatır. Kontrol ile yuvanın alınması aynı kilit altında olduğundan, işlem sürerken
        create_running de reddedilir: "iş yok" kontrolünden sonra araya iş giremez.

        Yuva süreçler arasında da geçerlidir: işlem süresince veri dizininin `maintenance` kilidi
        (yedekte `writer`) tutulur; kilit başka bir süreçteyse aynı iki hata fırlatılır.
        """
        with self._lock:
            if self._mirror.get("is_running"):
                raise JobRunningError()
            if self._exclusive is not None:
                raise DataOperationRunningError(self._exclusive)
            name = WRITER if operation in WRITER_OPERATIONS else MAINTENANCE
            try:
                lease = self._leases.acquire(name, purpose=OPERATION_PREFIX + operation)
            except LeaseHeld as held:
                raise conflict_from_lease(held) from held
            self._exclusive = operation
        try:
            yield
        finally:
            with self._lock:
                self._exclusive = None
                lease.release()

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
            previous_path, previous_state, previous_leases = self.db_path, self._state, self._leases
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
            new_state, new_leases = self._open(db_path)
            self.db_path, self._state, self._leases = db_path, new_state, new_leases
            try:
                # Bu süreçte çalışan iş yok: yeni dizindeki "running" satırları eski bir çöküşten kalmadır.
                # Yansı da boşa döner; önceki dizinin son işi yeni dizinin işi gibi görünmez.
                self.mark_stale_running_interrupted()
            except BaseException:
                self.db_path, self._state, self._leases = previous_path, previous_state, previous_leases
                new_state.close()
                raise
            previous_state.close()
            return True

    def create_running(self, payload: Any) -> str:
        job_id = str(uuid.uuid4())
        now = _utc_now()
        payload_json = json.dumps(payload, default=str)
        with self._lock:
            if self._exclusive is not None:
                # Silme/yedek sürerken başlayan iş, silinen dizine yazar ya da yarım yedeğe girer
                raise DataOperationRunningError(self._exclusive)
            # Başka bir süreç bu dizine yazıyorsa (web işi, CLI indirmesi, veri işlemi) iş başlamaz
            taken = self._take_writer()
            try:
                with self._state.write() as conn:
                    conn.execute(
                        """
                        INSERT INTO jobs (id, status, progress, current_task, payload_json, log_json, started_at)
                        VALUES (?, 'running', 0, 'Starting…', ?, '[]', ?)
                        """,
                        (job_id, payload_json, now),
                    )
            except BaseException:
                if taken:
                    self._release_writer()
                raise
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
            with self._state.write() as conn:
                conn.execute(
                    "UPDATE jobs SET cancel_requested = 1, current_task = ? WHERE id = ?",
                    ("Cancellation requested...", self._active_id),
                )
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

            # İş bittiyse `writer` kilidi satır yazıldıktan sonra bırakılır; yazma hata verse de bırakılır,
            # çünkü yansı artık "çalışmıyor" diyor
            done = contextlib.ExitStack()
            if finished:
                done.callback(self._release_writer)
            with done, self._state.write() as conn:
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
            rows = self._state.connection().execute(
                f"{_SELECT_JOBS} ORDER BY COALESCE(started_at, '') DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._state.connection().execute(f"{_SELECT_JOBS} WHERE id = ?", (job_id,)).fetchone()
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


__all__ = [
    "DataOperationRunningError",
    "JobRunningError",
    "JobStore",
    "JobStoreConflict",
    "default_db_path",
    "get_job_store",
    "import_legacy_jobs",
]
