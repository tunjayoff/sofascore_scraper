"""
İş deposu: işlerin geçmişi (state.db'nin `jobs` ve `job_events` tabloları) ve çalışan işin bellek içi yansısı.

2.x'te sofascore_scraper/web/jobs.py'de, `.meta/jobs.db` üzerinde duruyordu; yöntemler ve hata sınıfları aynen taşındı
(docs/design/01-storage.md bölüm 2.3 ve 3.1). Eski içe aktarma yolu (sofascore_scraper/web/jobs.py) 3.1'de kalktı (P30).

  * Satırlar `.meta/state.db`'ye yazılır. `.meta/jobs.db`'ye 3.x dokunmaz: satırları bir kez, salt okunur
    içe aktarılır (`meta.imported_jobs_db` kaydı) ve dosya yerinde kalır; aynı dizinde başlatılan bir 2.x
    süreci geçmişini bulmaya devam eder.
  * `list_jobs` / `get_job` bugünkü 18 sütunu, bugünkü durum adlarıyla verir (eski `/api/jobs` biçimi).
    İş yöneticisinin (sofascore_scraper/jobs/manager.py) okuduğu tam kayıt `get_record` / `list_records`tadır.

Süreçler arası kurallar (bölüm 6.1 ve docs/design/02-services.md 2.8):
  * Çalışan iş `writer` kilidini, veri işlemleri (`exclusive`) `maintenance` kilidini (yedek: `writer`) tutar.
    Aynı veri dizinini yazan ikinci bir süreç, bu süreçteki ikinci bir iş gibi JobRunningError /
    DataOperationRunningError alır.
  * Canlılık = satır + kilit: satırı "running" olan bir iş, `writer` kilidi tutuluyorsa çalışıyordur. Kilit
    boştayken "running" kalan satır çökmüş bir süreçten kalmadır; onu fark eden `interrupted` yapar
    (`reap_stale`). Açılıştaki koşulsuz süpürmenin yerini bu alır: ikinci bir süreç başlarken birincinin
    çalışan işini kesilmiş saymaz.
  * İptal satır üzerinden istenir (`cancel`): işi çalıştıran süreç bayrağı `poll_cancel` ile okur.
  * Yansı (`snapshot`) hâlâ süreç içidir: yalnızca bu deponun başlattığı işi gösterir.

Satırdaki durum adları: running, completed (başarılı), partial (devre kesici durdurdu ya da öğe başarısız),
failed, cancelled, interrupted. `partial` bu adımla yazılmaya başlandı; eski API onu `completed` olarak
gösterir. Daha önce yazılmış satırlar oldukları gibi kalır (plan kararı D15).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple, cast

from sofascore_scraper.store import files, layout
from sofascore_scraper.store.errors import LeaseHeld, StoreError
from sofascore_scraper.store.lease import EXPORT, MAINTENANCE, WRITER, Lease, LeaseManager
from sofascore_scraper.store.state import APPLICATION_ID, BUSY_TIMEOUT_MS, StateDb
from sofascore_scraper.store.streams import JOB_STREAM, StreamEvent, StreamLog

if TYPE_CHECKING:
    from sofascore_scraper.store.api import Store

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
# Bir işin tutabileceği kilitler (docs/design/02-services.md 2.8): indirmeler ve yedek `writer`, temizleme ve
# katalog yeniden kurulumu `maintenance`, web'in dışa aktarma işi `export` (FX-23, F14: indirme sürerken de
# çalışır; işini ayrı bir iş deposu yürütür, çünkü bir depo aynı anda tek iş çalıştırır)
JOB_LEASES: Tuple[str, ...] = (WRITER, MAINTENANCE, EXPORT)
EXPORT_KIND = "export"  # `export` kilidiyle çalışan işin türü (jobs.kind)

# Saklama (bölüm 9.3): iş yaratılırken en yeni bu kadar satır kalır; iş başına en yeni bu kadar olay
JOB_HISTORY_LIMIT = 500
JOB_EVENTS_LIMIT = 2000
_EVENT_PRUNE_EVERY = 100  # olay budaması her olayda değil, bu kadar olayda bir çalışır
_LEASE_WAIT_POLL = 0.1  # saniye: `create_running(wait=...)` kilidi bu aralıkla yeniden dener
# saniye: bitmekte olan işin son depo erişimleri (akış olayı, geri okuma) için `close` ve `create_running` en çok
# bu kadar bekler. Erişimler milisaniyelerdir; state.db meşgulse her biri 5 saniyeye kadar sürebilir
_FINISH_WAIT_SECONDS = 30.0

DEFAULT_KIND = "fetch"

# `jobs.status` değerleri
STATUS_RUNNING = "running"
STATUS_QUEUED = "queued"
STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_INTERRUPTED = "interrupted"
_ACTIVE_WHERE = "status IN ('running', 'queued')"
# Bitiş durumu adı (iş modelinin adları dahil) → satıra yazılan değer. Başarı, eski adıyla `completed` yazılır.
_TERMINAL_STATUS: Mapping[str, str] = {
    "completed": STATUS_COMPLETED,
    "succeeded": STATUS_COMPLETED,
    "partial": STATUS_PARTIAL,
    "failed": STATUS_FAILED,
    "cancelled": STATUS_CANCELLED,
    "interrupted": STATUS_INTERRUPTED,
}
# Satırdaki değer → canlı yansıdaki (büyük harfli) durum; yarım biten iş yansıda da "Completed" görünür
_MIRROR_STATUS: Mapping[str, str] = {
    STATUS_RUNNING: "Running",
    STATUS_COMPLETED: "Completed",
    STATUS_PARTIAL: "Completed",
    STATUS_FAILED: "Failed",
    STATUS_CANCELLED: "Cancelled",
    STATUS_INTERRUPTED: "Interrupted",
}
# Satırdaki değer → eski API'nin (`list_jobs`, `get_job`) gösterdiği değer
_LEGACY_STATUS: Mapping[str, str] = {STATUS_PARTIAL: STATUS_COMPLETED, "succeeded": STATUS_COMPLETED}

# İş olayı türleri (`job_events.type`); iş yöneticisi yazar, depo yalnızca ikisini kendisi kullanır
EVENT_PROGRESS = "progress"
EVENT_FINISHED = "finished"

# 2.x jobs.db'nin sütunları, o sırayla. `list_jobs` ve `get_job` yalnızca bunları döndürür.
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
# state.db'nin eklediği sütunlar (geçiş 0001: kind, owner; geçiş 0002: diğerleri)
RECORD_COLUMNS: Tuple[str, ...] = JOB_COLUMNS + (
    "kind", "owner", "origin_json", "spec_json", "error_json", "heartbeat_at", "created_at",
)
# Tam kayıt: satır ve işin son `progress` olayı (JobProgress.detail(); başka sürecin işi için de okunur)
_SELECT_RECORDS = (
    f"SELECT {', '.join(RECORD_COLUMNS)}, "
    "(SELECT e.data_json FROM job_events e WHERE e.job_id = jobs.id AND e.type = 'progress' "
    "ORDER BY e.seq DESC LIMIT 1) AS detail_json FROM jobs"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _json_or_none(value: Optional[Mapping[str, Any]]) -> Optional[str]:
    return None if value is None else json.dumps(dict(value), ensure_ascii=False, default=str)


def _loads(raw: Any, default: Any) -> Any:
    """Bir JSON sütununu çözer; boş ya da okunamayan değer `default` olur."""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _insert_event(conn: sqlite3.Connection, job_id: str, type: str, data: Mapping[str, Any]) -> int:
    """Açık bir yazma işleminde `job_events`'e bir satır ekler; sıra numarasını döndürür ve eski olayları budar."""
    seq = int(conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM job_events WHERE job_id = ?", (job_id,)).fetchone()[0])
    conn.execute(
        "INSERT INTO job_events (job_id, seq, ts_ms, type, data_json) VALUES (?, ?, ?, ?, ?)",
        (job_id, seq, _now_ms(), str(type), json.dumps(dict(data), ensure_ascii=False, separators=(",", ":"), default=str)),
    )
    if seq % _EVENT_PRUNE_EVERY == 0 and seq > JOB_EVENTS_LIMIT:
        conn.execute("DELETE FROM job_events WHERE job_id = ? AND seq <= ?", (job_id, seq - JOB_EVENTS_LIMIT))
    return seq


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


class InstanceRunningConflict(DataOperationRunningError):
    """
    Veri işlemini bir canlı servis (`live`) ya da 2.x izleyicisi (`watcher:<spor>`) engelliyor (P23). Eski
    çağıranlar DataOperationRunningError yakaladığı için onun alt sınıfıdır; kodu `instance_running`dir.
    """

    code = "instance_running"

    def __init__(self, operation: str, lease: str = "") -> None:
        super().__init__(operation)
        self.lease = lease or operation
        self.args = (f"A live watcher (lease {self.lease}) is running on this data directory; stop it first, "
                     f"then try again.",)


def conflict_from_lease(held: LeaseHeld) -> JobStoreConflict:
    """
    LeaseHeld → web katmanının 409'a çevirdiği hata. Kilidi bir veri işlemi tutuyorsa (amaç "op:<ad>")
    DataOperationRunningError, `writer` başka bir amaçla tutuluyorsa (web işi, CLI indirmesi) JobRunningError,
    canlı servis ya da izleyici tutuyorsa InstanceRunningConflict (`instance_running`).
    Asıl LeaseHeld (sahibin pid, makine, amaç ve başlangıç bilgisiyle) hatanın `__cause__` alanında kalır.
    """
    purpose = held.purpose or ""
    if purpose.startswith(OPERATION_PREFIX):
        return DataOperationRunningError(purpose[len(OPERATION_PREFIX):])
    if held.name == WRITER:
        return JobRunningError()
    if held.name == "live" or held.name.startswith("watcher:"):
        return InstanceRunningConflict(purpose or held.name, held.name)
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
            logger.warning("The legacy job history could not be read; it is tried again at the next open: %s: %s",
                           legacy_path, e)
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
        logger.info("Legacy job history imported: %s rows (%s)", imported, legacy_path)
    return imported


class _StateHolder:
    """`StreamLog` deponun yalnızca `_state` özniteliğini okur; yol üzerinden açılan iş deposu ona bunu verir."""

    def __init__(self, state: StateDb) -> None:
        self._state = state


class JobStore:
    """Thread-safe job persistence + in-memory mirror for SSE."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        state, leases = self._open(db_path)
        self._setup(db_path, state, leases, owns_state=True)
        try:
            self.reap_stale()
        except BaseException:
            self._state.close()
            raise

    def _setup(self, db_path: str, state: StateDb, leases: LeaseManager, *, owns_state: bool,
               streams: Optional[StreamLog] = None) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._active_id: Optional[str] = None
        # Süren veri işleminin adı (clear, backup, ...); doluyken yeni iş başlatılamaz
        self._exclusive: Optional[str] = None
        self._mirror: Dict[str, Any] = self._idle_mirror()
        # Çalışan işin `writer` kilidi: create_running alır, iş bitince bırakılır
        self._writer: Optional[Lease] = None
        # Bitmekte olan iş (`_job_finishing`): satırı bitmiştir ama thread'i depoyu hâlâ kullanır. Bu sürede
        # `writer` kilidi tutulur, `rebind` / `exclusive` JobRunningError verir, `close` ve `create_running` bekler
        self._finishing: Optional[threading.Thread] = None
        self._release_deferred = False
        self._finish_done = threading.Condition(self._lock)
        self._state, self._leases = state, leases
        # False: bağlantı bir Store'undur (`for_store`); depo onu kapatmaz
        self._owns_state = owns_state
        self._streams = streams if streams is not None else StreamLog(cast("Store", _StateHolder(state)))

    @classmethod
    def for_store(cls, store: "Store") -> "JobStore":
        """
        Açık bir Store'un iş deposu: onun state.db bağlantısını ve kilit yöneticisini kullanır, kendi dosyasını
        açmaz. Aynı Store için hep aynı nesne döner (çalışan işin yansısı ve `writer` kilidi nesnededir).
        Store kapatılınca bu depo da kullanılamaz; yeniden açılan Store yeni bir depo alır.
        """
        if store.closed:
            raise StoreError(f"The store is closed: {store.data_dir}", path=str(store.data_dir))
        with _by_store_lock:
            jobs = _by_store.get(store)
            if jobs is None:
                jobs = cls.__new__(cls)
                jobs._setup(store._state.path, store._state, store._leases, owns_state=False, streams=store.streams)
                jobs.reap_stale()
                _by_store[store] = jobs
            return jobs

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
        """
        Kilidi bırakır ve veritabanı bağlantılarını kapatır (testler ve DATA_DIR değişimi için). Bitmekte olan bir
        iş varsa önce onun son depo erişimlerinin bitmesini bekler (en çok _FINISH_WAIT_SECONDS): bağlantı, onu
        kullanan iş thread'inin altından kapatılmaz.
        """
        with self._lock:
            if not self._wait_for_finish():
                logger.warning("Closing the job store while a finishing job still uses it (waited %.0f s)",
                               _FINISH_WAIT_SECONDS)
            self._release_writer()
            if self._owns_state:
                self._state.close()

    @contextlib.contextmanager
    def _job_finishing(self) -> Iterator[None]:
        """
        İşin bitişi (iş yöneticisi kullanır, sofascore_scraper/jobs/manager.py): blok içinde `update(finished=True)` satırı ve
        yansıyı bitirir, ama `writer` kilidi blok bitince bırakılır. İş satırı bitirdikten sonra da depoyu
        kullanır (`job.finished` akış olayı, bitmiş işin geri okunması); blok sürdükçe iş bitmiş sayılmaz:
        `rebind` ve `exclusive` JobRunningError verir, `close` ve aynı depoda yeni iş açan `create_running`
        bloğun bitmesini bekler. Böylece veri klasörü değişimi ya da kapatma, state.db bağlantısını onu kullanan
        iş thread'inin altından kapatamaz.
        """
        with self._lock:
            self._finishing = threading.current_thread()
            self._release_deferred = False
        try:
            yield
        finally:
            with self._lock:
                self._finishing = None
                if self._release_deferred:
                    self._release_deferred = False
                    self._release_writer()
                self._finish_done.notify_all()

    @classmethod
    def wait_for_finishing_job(cls, store: "Store") -> bool:
        """
        Store'un iş deposunda (`Store.jobs`) bitmekte olan bir iş varsa (`_job_finishing`), onun son depo
        erişimleri bitene kadar bekler (en çok _FINISH_WAIT_SECONDS). `Store.close()` state.db'yi kapatmadan önce
        çağırır: bağlantı, onu kullanan iş thread'inin altından kapatılmaz. İş deposu hiç kurulmadıysa, bekleyecek
        iş yoksa ya da çağıran o işin kendi thread'iyse hemen True döner; süre dolduysa uyarı yazar ve False
        döner. Ortasında olan (satırı henüz bitmemiş) bir işi beklemez: o, `close_all`'ın sözleşmesinin
        dışındadır (docs/design/01-storage.md, bölüm 3.2).
        """
        with _by_store_lock:
            jobs = _by_store.get(store)
        if jobs is None:
            return True
        with jobs._lock:
            if jobs._wait_for_finish():
                return True
        logger.warning("Closing the store %s while a finishing job still uses it (waited %.0f s)",
                       store.data_dir, _FINISH_WAIT_SECONDS)
        return False

    def _wait_for_finish(self) -> bool:
        """
        Bitmekte olan işin bloğu bitene kadar bekler (kilit tutularak çağrılır; beklerken kilit bırakılır).
        Bekleyecek iş yoksa ya da çağıran o işin kendi thread'iyse (kendini bekleyemez) hemen True döner. Süre
        dolduysa False.
        """
        if self._finishing is None or self._finishing is threading.current_thread():
            return True
        return self._finish_done.wait_for(lambda: self._finishing is None, timeout=_FINISH_WAIT_SECONDS)

    def _release_finished_writer(self) -> None:
        """`update(finished=True)`ın kilit bırakması: iş bitiş bloğundaysa (`_job_finishing`) blok sonuna kalır."""
        if self._finishing is not None:
            self._release_deferred = True
        else:
            self._release_writer()

    def _take_writer(self, purpose: str = JOB_PURPOSE, name: str = WRITER) -> bool:
        """
        İşin kilidini (`writer`, ya da bakım işinde `maintenance`) alır; depo onu zaten tutuyorsa hiçbir şey
        yapmaz ve False döner. Depo başka bir kilit tutuyorsa (bu süreçte başka türden bir iş) ya da kilit başka
        bir süreçte (ya da bu süreçteki başka bir depoda) ise JobRunningError / DataOperationRunningError.
        """
        if self._writer is not None and self._writer.held:
            if self._writer.name != name:
                raise JobRunningError()
            return False
        try:
            self._writer = self._leases.acquire(name, purpose=purpose)
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

    # --- bayat satırlar ---------------------------------------------------------------------

    @staticmethod
    def _interrupt(conn: sqlite3.Connection, job_ids: Optional[Sequence[str]], now: str) -> List[str]:
        """Verilen (None: bütün) çalışan/sıradaki satırları `interrupted` yapar; değişen kimlikleri döndürür."""
        if job_ids is None:
            job_ids = [str(row[0]) for row in conn.execute(f"SELECT id FROM jobs WHERE {_ACTIVE_WHERE}")]
        changed: List[str] = []
        for job_id in job_ids:
            cursor = conn.execute(
                f"""
                UPDATE jobs
                SET status = 'interrupted',
                    finished_at = ?,
                    current_task = COALESCE(NULLIF(current_task, ''), 'Interrupted by server restart')
                WHERE id = ? AND {_ACTIVE_WHERE}
                """,
                (now, job_id),
            )
            if cursor.rowcount:
                changed.append(job_id)
                _insert_event(conn, job_id, EVENT_FINISHED, {"state": STATUS_INTERRUPTED})
        return changed

    def reap_stale(self) -> int:
        """
        Sahibi ölmüş işleri `interrupted` yapar ve sayısını döndürür: satırı "running" (ya da "queued") olup
        `writer` kilidi boşta olan işler (docs/design/02-services.md 2.8, "Liveness"). Kilidi başka bir süreç
        tutuyorsa hiçbir satıra dokunulmaz: o satır onun çalışan işi olabilir. Bu deponun kendi çalışan işi
        hiçbir zaman süpürülmez; depo `writer` kilidini kendisi tutuyorsa diğer bütün çalışan satırlar bayattır.

        Fark eden herkes çağırır: depo açılırken, iş başlarken ve iş geçmişi okunurken. Çalışan satır yoksa
        tek bir SELECT'tir.
        """
        with self._lock:
            rows = self._state.connection().execute(f"SELECT id, kind FROM jobs WHERE {_ACTIVE_WHERE}").fetchall()
            stale = [str(row[0]) for row in rows if str(row[0]) != self._active_id]
            if not stale:
                return 0
            # `export` kilidi `writer` ile birlikte tutulabilir: onu tutan dışa aktarma işi yaşıyordur (FX-23)
            if self._leases.holder(EXPORT) is not None:
                exports = {str(row[0]) for row in rows if row[1] == EXPORT_KIND}
                stale = [job_id for job_id in stale if job_id not in exports]
                if not stale:
                    return 0
            # Dışa aktarma deposunun kilidi (`export`) yazma kilidi değildir: öteki işlerin satırlarını bayat saydırmaz
            holds_writer = self._writer is not None and self._writer.held and self._writer.name != EXPORT
            # Bakım işi (`maintenance` kilidi) de bir işin sahibidir: kilit başka bir süreçteyse satır onun olabilir
            if not holds_writer and (self._leases.holder(WRITER) is not None
                                     or self._leases.holder(MAINTENANCE) is not None):
                return 0
            with self._state.write() as conn:
                reaped = self._interrupt(conn, stale, _utc_now())
            if reaped:
                logger.warning("Job(s) marked interrupted, the process that ran them is gone: %s", ", ".join(reaped))
            return len(reaped)

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
            # Satırı bitmiş ama son depo erişimleri süren iş de (`_job_finishing`) çalışıyor sayılır
            if self._mirror.get("is_running") or self._finishing is not None:
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
        Çalışan iş varken yapılamaz; satırı bitmiş ama son depo erişimleri (akış olayı, geri okuma) süren iş de
        çalışıyor sayılır (JobRunningError): eski bağlantı onu kullanan iş thread'inin altından kapatılmaz. Yol
        aynıysa hiçbir şey yapmaz ve False döner.
        """
        with self._lock:
            if self._mirror.get("is_running") or self._finishing is not None:
                raise JobRunningError()
            if os.path.abspath(db_path) == os.path.abspath(self.db_path):
                return False
            previous = (self.db_path, self._state, self._leases, self._owns_state, self._streams, self._active_id,
                        self._mirror)
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
            new_state, new_leases = self._open(db_path)
            self.db_path, self._state, self._leases, self._owns_state = db_path, new_state, new_leases, True
            self._streams = StreamLog(cast("Store", _StateHolder(new_state)))
            # Yansı boşa döner: önceki dizinin son işi yeni dizinin işi gibi görünmez
            self._active_id = None
            self._mirror = self._idle_mirror()
            try:
                # Bu süreçte çalışan iş yok: yeni dizinde kilidi boşta olan "running" satırları eski bir
                # çöküşten kalmadır. Kilidi başka bir süreç tutuyorsa satır onun işidir ve yerinde kalır.
                self.reap_stale()
            except BaseException:
                (self.db_path, self._state, self._leases, self._owns_state, self._streams, self._active_id,
                 self._mirror) = previous
                new_state.close()
                raise
            if previous[3]:
                previous[1].close()
            return True

    # --- iş yaratma -------------------------------------------------------------------------

    def create_running(self, payload: Any, *, job_id: Optional[str] = None, kind: str = DEFAULT_KIND,
                       origin: Optional[Mapping[str, Any]] = None, spec: Optional[Mapping[str, Any]] = None,
                       wait: float = 0.0, purpose: str = JOB_PURPOSE, replace_running: bool = True,
                       lease: str = WRITER) -> str:
        """
        İşin kilidini (`lease`: `writer`, bakım işinde `maintenance`) alır ve işin satırını "running" olarak yazar;
        işin kimliğini döndürür.

        job_id   verilmezse uuid4 üretilir (iş yöneticisi sıralanabilir bir kimlik verir)
        kind     işin türü (`jobs.kind`)
        origin   işi başlatan yüz ve süreç: {"face", "pid", "host"}
        spec     servis belirtimi; `payload` ise eski API'nin gösterdiği istek gövdesidir
        wait     kilit başkasındaysa bu kadar saniye beklenir, sonra JobRunningError / DataOperationRunningError
        purpose  kilidin amacı: reddedilen süreç kullanıcıya bunu söyler (ör. "headless", "refresh"). "op:" ile
                 başlayamaz: o önek veri işlemlerinindir ve çakışan süreç ona göre hata seçer
        replace_running  True (bugünkü davranış): bu deponun çalışan işi varken de yeni iş açılır, kilit yeniden
                 kullanılır ve önceki iş sahipsiz kalır. False: o durumda JobRunningError (iş yöneticisi böyle çağırır)
        lease    JOB_LEASES'ten biri. `maintenance` başka her işi ve veri işlemini dışlar (temizleme, katalog)

        Satır yazılırken geçmiş budanır: en yeni JOB_HISTORY_LIMIT satır kalır (bölüm 9.3).
        """
        if not purpose or purpose.startswith(OPERATION_PREFIX):
            raise ValueError(f"not a job lease purpose: {purpose!r}")
        if lease not in JOB_LEASES:
            raise ValueError(f"not a job lease: {lease!r}")
        deadline = time.monotonic() + max(0.0, float(wait))
        while True:
            try:
                return self._create_running(payload, job_id, kind, origin, spec, purpose, replace_running, lease)
            except JobStoreConflict:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(_LEASE_WAIT_POLL, remaining))

    def _create_running(self, payload: Any, job_id: Optional[str], kind: str, origin: Optional[Mapping[str, Any]],
                        spec: Optional[Mapping[str, Any]], purpose: str, replace_running: bool,
                        lease: str = WRITER) -> str:
        job_id = job_id or str(uuid.uuid4())
        now = _utc_now()
        payload_json = json.dumps(payload, default=str)
        with self._lock:
            # Önceki iş satırını bitirdi ama kilidi hâlâ tutuyor (`_job_finishing`): yeni iş kilidi onunla
            # paylaşmaz, bitişin sonunu bekler. Bekleme dolarsa ya da çağıran o işin kendi thread'iyse reddedilir.
            # Beklerken kilit bırakıldığından aşağıdaki kontroller beklemeden sonra yapılır
            if not self._wait_for_finish() or self._finishing is not None:
                raise JobRunningError()
            if self._exclusive is not None:
                # Silme/yedek sürerken başlayan iş, silinen dizine yazar ya da yarım yedeğe girer
                raise DataOperationRunningError(self._exclusive)
            if not replace_running and self._active_id is not None and self._mirror.get("is_running"):
                raise JobRunningError()
            # Başka bir süreç bu dizine yazıyorsa (web işi, CLI indirmesi, veri işlemi) iş başlamaz
            taken = self._take_writer(purpose, lease)
            try:
                # Kilit bizde: kendi çalışan işimiz dışındaki her "running" satırı bayattır
                self.reap_stale()
                with self._state.write() as conn:
                    conn.execute(
                        """
                        INSERT INTO jobs (id, status, progress, current_task, payload_json, log_json, started_at,
                                          kind, owner, origin_json, spec_json, created_at, heartbeat_at)
                        VALUES (?, 'running', 0, 'Starting…', ?, '[]', ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            job_id, payload_json, now, str(kind),
                            self._writer.holder if self._writer is not None else None,
                            _json_or_none(origin), _json_or_none(spec), now, _now_ms(),
                        ),
                    )
                    self._prune_history(conn)
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

    @staticmethod
    def _prune_history(conn: sqlite3.Connection) -> int:
        """En yeni JOB_HISTORY_LIMIT satırın dışındaki bitmiş işleri ve olaylarını siler (iş yaratılırken)."""
        cursor = conn.execute(
            f"""
            DELETE FROM jobs
            WHERE NOT ({_ACTIVE_WHERE}) AND id NOT IN (
                SELECT id FROM jobs ORDER BY COALESCE(started_at, created_at, '') DESC, rowid DESC LIMIT ?
            )
            """,
            (JOB_HISTORY_LIMIT,),
        )
        removed = cursor.rowcount or 0
        if removed:
            conn.execute("DELETE FROM job_events WHERE job_id NOT IN (SELECT id FROM jobs)")
        return removed

    # --- iptal ve kalp atışı ----------------------------------------------------------------

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

    def cancel(self, job_id: str) -> bool:
        """
        Kimliği verilen işin iptalini ister; iş hangi süreçte çalışırsa çalışsın. Bu deponun kendi işiyse
        `request_cancel` ile aynıdır; başka bir sürecin işiyse yalnızca satırdaki bayrak yazılır ve o süreç
        bayrağı en geç bir saniye içinde okur (`poll_cancel`). İş çalışmıyorsa (bitmiş, bayat, bilinmiyor) False.
        """
        with self._lock:
            if job_id == self._active_id and self._mirror.get("is_running"):
                return self.request_cancel()
            self.reap_stale()
            with self._state.write() as conn:
                cursor = conn.execute(
                    f"UPDATE jobs SET cancel_requested = 1 WHERE id = ? AND {_ACTIVE_WHERE}", (job_id,)
                )
                return bool(cursor.rowcount)

    def cancel_requested(self, job_id: Optional[str] = None) -> bool:
        """Çalışan işin iptali istendi mi (yansıdan). `job_id` verilirse yalnızca o iş bu deponun çalışan işiyse."""
        with self._lock:
            if job_id is not None and job_id != self._active_id:
                return False
            return bool(self._mirror.get("cancel_requested"))

    def poll_cancel(self, job_id: Optional[str] = None) -> bool:
        """
        Çalışan işin satırındaki iptal bayrağını okur ve yansıya taşır: başka bir sürecin `cancel` çağrısı
        böyle görülür. İptal istenmişse True. İşi çalıştıran süreç saniyede bir çağırır.
        """
        with self._lock:
            active = self._active_id
            if not active or (job_id is not None and job_id != active) or not self._mirror.get("is_running"):
                return False
            if self._mirror.get("cancel_requested"):
                return True
            row = self._state.connection().execute(
                "SELECT cancel_requested FROM jobs WHERE id = ?", (active,)
            ).fetchone()
            if row is None or not row[0]:
                return False
            self._mirror["cancel_requested"] = True
            self._mirror["current_task"] = "Cancellation requested..."
            with self._state.write() as conn:
                conn.execute("UPDATE jobs SET current_task = ? WHERE id = ?", ("Cancellation requested...", active))
            return True

    def heartbeat(self, job_id: Optional[str] = None) -> bool:
        """Çalışan işin `heartbeat_at` alanını şimdiye çeker (yalnızca gösterim için). İş bu deponun değilse False."""
        with self._lock:
            active = self._active_id
            if not active or (job_id is not None and job_id != active):
                return False
            with self._state.write() as conn:
                conn.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ?", (_now_ms(), active))
            return True

    # --- ilerleme ve bitiş ------------------------------------------------------------------

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
        job_id: Optional[str] = None,
        state: Optional[str] = None,
        error: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """
        Çalışan işin alanlarını yansıya ve satıra yazar; `finished=True` işi bitirir ve `writer` kilidini bırakır.

        job_id  verilirse ve bu deponun çalışan işi o değilse hiçbir şey yazılmaz (bitmiş bir işin geç kalan
                yazması sonraki işe karışmaz)
        state   bitiş durumu: completed/succeeded, partial, failed, cancelled, interrupted. Verilmezse `status`
                ve yansıdan türetilir (bitiş durumu kuralı, 02-services.md 2.8): hata → failed; iptal istendiyse
                → cancelled; devre kesildiyse ya da başarısız öğe varsa → partial; değilse completed.
        error   işi durduran hata: {"code", "message", "details"} (`error_json`)
        """
        with self._lock:
            active = self._active_id
            if not active or (job_id is not None and job_id != active):
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

            db_status = STATUS_RUNNING
            if finished:
                # Başka bir sürecin son anda yazdığı iptal isteği de sayılır
                row = self._state.connection().execute(
                    "SELECT cancel_requested FROM jobs WHERE id = ?", (active,)
                ).fetchone()
                if row is not None and row[0]:
                    m["cancel_requested"] = True
                db_status = self._terminal_status(m, state)
                m["status"] = _MIRROR_STATUS[db_status]
                m["is_running"] = False
                m["finished_at"] = _utc_now()

            # İş bittiyse `writer` kilidi satır yazıldıktan sonra bırakılır; yazma hata verse de bırakılır,
            # çünkü yansı artık "çalışmıyor" diyor. İş yöneticisinin bitiş bloğunda (`_job_finishing`) bırakma
            # blok sonuna kalır
            done = contextlib.ExitStack()
            if finished:
                done.callback(self._release_finished_writer)
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
                        cancel_requested = MAX(cancel_requested, ?),
                        matches_total = ?,
                        matches_done = ?,
                        matches_failed = ?,
                        schedule_empty_seasons = ?,
                        circuit_breaker_triggered = ?,
                        circuit_breaker_reason = ?,
                        eta_seconds = ?,
                        current_batch = ?,
                        error_json = COALESCE(?, error_json)
                    WHERE id = ?
                    """,
                    (
                        db_status,
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
                        _json_or_none(error),
                        active,
                    ),
                )

            if finished:
                self._active_id = None

    @staticmethod
    def _terminal_status(mirror: Mapping[str, Any], state: Optional[str]) -> str:
        """Biten işin satıra yazılacak durumu. `state` verilmişse odur; yoksa yansıdan türetilir."""
        if state is not None:
            explicit = _TERMINAL_STATUS.get(str(state).strip().lower())
            if explicit is None:
                raise ValueError(f"unknown terminal job state: {state!r}")
            return explicit
        text = str(mirror.get("status") or "").strip().lower()
        if text == "failed":
            return STATUS_FAILED
        if text == "cancelled" or mirror.get("cancel_requested"):
            return STATUS_CANCELLED
        if text in ("running", "completed", "succeeded", "partial"):
            incomplete = bool(mirror.get("circuit_breaker_triggered")) or int(mirror.get("matches_failed") or 0) > 0
            return STATUS_PARTIAL if incomplete or text == "partial" else STATUS_COMPLETED
        if text == "interrupted":
            return STATUS_INTERRUPTED
        # Tanınmayan bir bitiş metni satıra olduğu gibi yazılmaz: neyle bittiği bilinmeyen iş başarılı sayılmaz
        logger.warning("Job finished with an unknown status %r; stored as failed", mirror.get("status"))
        return STATUS_FAILED

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._mirror)

    # --- olaylar ----------------------------------------------------------------------------

    def append_event(self, job_id: str, type: str, data: Optional[Mapping[str, Any]] = None) -> int:
        """
        İşin olay günlüğüne (`job_events`) bir olay ekler ve sıra numarasını döndürür (iş başına 1'den başlar).
        İş başına en yeni JOB_EVENTS_LIMIT olay tutulur.
        """
        with self._state.write() as conn:
            return _insert_event(conn, job_id, type, data or {})

    def read_events(self, job_id: str, *, after: int = 0, limit: int = 500) -> List[Dict[str, Any]]:
        """`after`dan büyük sıra numaralı olaylar, sırayla: {"job_id", "seq", "ts_ms", "type", "data"}."""
        rows = self._state.connection().execute(
            "SELECT job_id, seq, ts_ms, type, data_json FROM job_events WHERE job_id = ? AND seq > ? "
            "ORDER BY seq LIMIT ?",
            (job_id, int(after), max(1, int(limit))),
        ).fetchall()
        return [
            {"job_id": str(row[0]), "seq": int(row[1]), "ts_ms": int(row[2]), "type": str(row[3]),
             "data": _loads(row[4], {})}
            for row in rows
        ]

    def announce(self, type: str, data: Mapping[str, Any]) -> Optional[int]:
        """`job` akışına bir olay ekler (`job.started`, `job.finished`; sink'ler okur). Sıra numarasını döndürür."""
        return self._streams.append(JOB_STREAM, [StreamEvent(type=type, data=data, source="job")])[0]

    # --- okuma ------------------------------------------------------------------------------

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
        # Eski API'nin durum adları: yarım biten iş de "completed" görünür (bayrakları satırdadır)
        d["status"] = _LEGACY_STATUS.get(d.get("status"), d.get("status"))
        d["cancel_requested"] = bool(d.get("cancel_requested"))
        d["circuit_breaker_triggered"] = bool(d.get("circuit_breaker_triggered"))
        d["is_running"] = d.get("status") == "running"
        return d

    def list_jobs(self, limit: int = 20) -> List[Dict[str, Any]]:
        limit = max(1, min(100, int(limit)))
        with self._lock:
            self.reap_stale()
            rows = self._state.connection().execute(
                # Aynı saniyede başlayan işlerde sonra yazılan önce gelir
                f"{_SELECT_JOBS} ORDER BY COALESCE(started_at, '') DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self.reap_stale()
            row = self._state.connection().execute(f"{_SELECT_JOBS} WHERE id = ?", (job_id,)).fetchone()
            return self._row_to_dict(row) if row else None

    def _record(self, row: sqlite3.Row) -> Dict[str, Any]:
        """İş yöneticisinin okuduğu tam kayıt: satırın bütün sütunları, JSON alanları çözülmüş olarak."""
        d = dict(row)
        record: Dict[str, Any] = {
            key: d[key] for key in (
                "id", "status", "kind", "owner", "progress", "current_task", "started_at", "finished_at",
                "created_at", "heartbeat_at", "matches_total", "matches_done", "matches_failed",
                "schedule_empty_seasons", "circuit_breaker_reason", "eta_seconds", "current_batch",
            )
        }
        record["cancel_requested"] = bool(d["cancel_requested"])
        record["circuit_breaker_triggered"] = bool(d["circuit_breaker_triggered"])
        record["payload"] = _loads(d["payload_json"], None)
        record["log"] = _loads(d["log_json"], [])
        record["result"] = _loads(d["result_json"], None)
        record["origin"] = _loads(d["origin_json"], None)
        record["spec"] = _loads(d["spec_json"], None)
        record["error"] = _loads(d["error_json"], None)
        # Ayrıntılı ilerleme: bu sürecin çalışan işiyse canlı yansı, değilse işin son `progress` olayı
        live = d["id"] == self._active_id and bool(self._mirror.get("is_running"))
        record["live"] = live
        record["detail"] = self._mirror.get("detail") if live else _loads(d["detail_json"], None)
        return record

    def get_record(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Bir işin tam kaydı (bkz. `_record`); iş yoksa None. Okumadan önce bayat satırlar süpürülür."""
        with self._lock:
            self.reap_stale()
            row = self._state.connection().execute(f"{_SELECT_RECORDS} WHERE id = ?", (job_id,)).fetchone()
            return self._record(row) if row else None

    def list_records(self, limit: int = JOB_HISTORY_LIMIT) -> List[Dict[str, Any]]:
        """İşlerin tam kayıtları, en yeni başlayan önce."""
        with self._lock:
            self.reap_stale()
            rows = self._state.connection().execute(
                f"{_SELECT_RECORDS} ORDER BY COALESCE(started_at, created_at, '') DESC, rowid DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
            return [self._record(row) for row in rows]

    def active_record(self) -> Optional[Dict[str, Any]]:
        """
        Veri dizininde şu an çalışan iş (hangi süreçte olursa olsun) ya da None. Bayat satırlar önce
        süpürüldüğü için kalan "running" satırı ya bu deponun işidir ya da `writer` kilidini tutan sürecin.
        """
        with self._lock:
            self.reap_stale()
            row = self._state.connection().execute(
                f"{_SELECT_RECORDS} WHERE status = 'running' "
                "ORDER BY COALESCE(started_at, created_at, '') DESC, rowid DESC LIMIT 1"
            ).fetchone()
            return self._record(row) if row else None


# Store başına tek iş deposu (`JobStore.for_store`); Store çöpe gidince kaydı da gider
_by_store: "weakref.WeakKeyDictionary[Store, JobStore]" = weakref.WeakKeyDictionary()
_by_store_lock = threading.Lock()

_store: Optional[JobStore] = None
_store_lock = threading.Lock()


def get_job_store(data_dir: Optional[str] = None) -> JobStore:
    global _store
    with _store_lock:
        if _store is None:
            root = data_dir or files.default_data_dir()
            if not os.path.isabs(root):
                root = os.path.abspath(root)
            _store = JobStore(default_db_path(root))
        return _store


__all__ = [
    "JOB_EVENTS_LIMIT",
    "JOB_HISTORY_LIMIT",
    "DataOperationRunningError",
    "JobRunningError",
    "JobStore",
    "JobStoreConflict",
    "default_db_path",
    "get_job_store",
    "import_legacy_jobs",
]
