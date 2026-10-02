"""İş yöneticisi: web ve komut satırı için tek iş yürütücüsü, süreçler arasında (docs/design/02-services.md 2.8).

Bir iş onu yaratan süreçte çalışır; arka planda çalışan bir servis ya da kuyruk yoktur. Yönetici iş deposunun
(`src.store.JobStore`) üzerinde durur ve SQL içermez:

  * `start`   `writer` kilidini alır ve işin satırını yazar. Kilit başka bir süreçteyse (ya da bu süreçteki
              başka bir işte) JobRunningError / DataOperationRunningError.
  * `run`     başlatılmış işi çağıranın thread'inde yürütür: gövdeye bir `JobHandle` verir, iptal bayrağını
              saniyede bir satırdan okur, beş saniyede bir kalp atışı yazar, bitişte durumu satıra yazar ve
              kilidi bırakır. Kilit, işin son depo erişiminden (`job.finished` akış olayı, bitmiş işin geri
              okunması) sonra bırakılır: o ana kadar veri klasörü değişimi JobRunningError alır.
  * `submit`  ikisi birden: `background=True` ise kendi thread'inde (web, zamanlayıcı), değilse çağıranın
              thread'inde (komut satırı, kitaplık).

Canlılık satır + kilittir: satırı "running" olup `writer` kilidi boşta olan iş, onu fark eden tarafından
`interrupted` yapılır (`reap_stale`; okuma yöntemleri bunu kendiliğinden yapar). İptal satır üzerinden
istenir, bu yüzden `cancel` her süreçten çalışır.

İşin olay günlüğü (`job_events`): aşama, günlük satırı, başarısız öğe ve devre kesici olaylarının hepsi,
ilerleme olaylarının ise saniyede en çok ikisi yazılır; iş başına en yeni 2.000 olay tutulur. İşin başladığı
ve bittiği ayrıca `job` akışına (`job.started`, `job.finished`) eklenir; sink'ler oradan okur.

state.db'nin meşgul olması (StoreBusy: başka bir yazar kilidi 5 saniyeden uzun tuttu) bir işi bitirmez:
  * olay ve akış yazmaları en iyi çabadır; yazılamayan olay atlanır (uyarı loglanır, yeniden denenmez);
  * ilerleme ve günlük satırı yazmaları da atlanır: yansı güncellenmiştir ve bir sonraki yazma satırı tamamlar;
  * işin bitişi FINAL_WRITE_ATTEMPTS kez denenir; yazılamazsa hata çağırana çıkar ve satırı sonraki okuyan
    `interrupted` yapar.
Satıra yapılan yazmaların diğer hataları (dolu disk, bozuk dosya) bugünkü gibi fırlar.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence

from src.errors import to_platform_error
from src.jobs.model import (
    ErrorInfo,
    Job,
    JobEvent,
    JobEventType,
    JobKind,
    JobState,
    Origin,
    OriginFace,
    breaker_error,
    job_event_from_record,
    job_from_record,
    new_job_id,
    terminal_state,
)
from src.jobs.progress import JobProgress
from src.redact import redact_text
from src.store import JobStore, StoreBusy

logger = logging.getLogger("Jobs")

CANCEL_POLL_SECONDS = 1.0  # işi çalıştıran süreç iptal bayrağını satırdan bu aralıkla okur
HEARTBEAT_SECONDS = 5.0  # `heartbeat_at` bu aralıkla yazılır (yalnızca gösterim için)
PROGRESS_EVENT_SECONDS = 0.5  # ilerleme olayları: saniyede en çok iki tane
EVENTS_POLL_SECONDS = 0.25  # `events(follow=True)` yeni olayları bu aralıkla yoklar
FINAL_WRITE_ATTEMPTS = 3  # işin bitişi, state.db meşgulse (StoreBusy) bu kadar kez denenir

STREAM_JOB_STARTED = "job.started"
STREAM_JOB_FINISHED = "job.finished"

# Bitiş durumu → canlı yansıdaki ve iş günlüğündeki ("[Completed] ...") eski durum metni
_LEGACY_STATUS: Mapping[JobState, str] = {
    JobState.SUCCEEDED: "Completed",
    JobState.PARTIAL: "Completed",
    JobState.FAILED: "Failed",
    JobState.CANCELLED: "Cancelled",
    JobState.INTERRUPTED: "Interrupted",
}
_DEFAULT_MESSAGE: Mapping[JobState, str] = {
    JobState.SUCCEEDED: "Finished",
    JobState.PARTIAL: "Finished with failed items",
    JobState.FAILED: "Failed",
    JobState.CANCELLED: "Cancelled",
    JobState.INTERRUPTED: "Interrupted",
}

JobBody = Callable[["JobHandle"], Any]


def local_origin(face: OriginFace) -> Origin:
    """Bu süreçten başlatılan işin kaynağı: yüz, pid ve makine adı."""
    return Origin(face=face, pid=os.getpid(), host=socket.gethostname())


@dataclass(frozen=True)
class JobOutcome:
    """
    İş gövdesinin döndürebileceği sonuç; işin nasıl biteceğini söyler. Gövde başka bir değer döndürürse (ya da
    hiçbir şey döndürmezse) durum, bitiş durumu kuralıyla ilerlemeden çıkarılır.

    state    bitiş durumu; None: kural (iptal istendiyse cancelled; devre kesildiyse ya da başarısız öğe
             varsa partial; değilse succeeded). İptal istenmişse succeeded / partial de cancelled olur.
    result   işin sonuç özeti (`result_json`)
    error    işi durduran ya da yarım bırakan hata; verilmezse devre kesicinin durdurduğu iş kendi kodunu alır
    message  kartın görev satırı ve iş günlüğünün son satırı (serbest metin; eski istemciler için)
    code     `message`in çeviri anahtarı: istemci metni bundan ve `params`tan üretir
    percent  bitişteki yüzde; None: başarıda 100, diğerlerinde ilerlemenin kaldığı yer
    """

    state: Optional[JobState] = None
    result: Optional[Mapping[str, Any]] = None
    error: Optional[ErrorInfo] = None
    message: Optional[str] = None
    code: Optional[str] = None
    params: Mapping[str, Any] = field(default_factory=dict)
    percent: Optional[int] = None


class JobNotActive(RuntimeError):
    """`run`, bu süreçte başlatılmamış ya da artık çalışmayan bir iş için çağrıldı."""


class JobHandle:
    """
    Servisin gördüğü iş (src.services.sync.JobHandle protokolü): iptal sorusu, ilerleme ve iş günlüğü.

    Her çağrı işin satırına yazılır ve olay günlüğüne düşer. `progress`, işin aşamalarıyla kurulmuş bir
    JobProgress'tir; yayınladığı her değişiklik satıra gider, olaylara ise seyreltilerek.
    """

    def __init__(self, store: JobStore, job_id: str, phases: Sequence[str], *,
                 on_change: Optional[Callable[[], Any]] = None, on_log: Optional[Callable[[str], Any]] = None,
                 progress_interval: float = PROGRESS_EVENT_SECONDS) -> None:
        self.id = job_id
        self._store = store
        self._on_change = on_change
        self._on_log = on_log
        self._progress_interval = max(0.0, float(progress_interval))
        self._events_lock = threading.Lock()
        self._last_detail: Mapping[str, Any] = {}
        self._pending_progress: Optional[Dict[str, Any]] = None
        self._last_progress_at = 0.0
        self._cancel_noted = False
        self._event_failures = 0
        self._busy_writes = 0
        self.progress = JobProgress(list(phases), self._publish_progress)

    # --- servisin kullandığı yüz ---------------------------------------------------------------------

    def cancelled(self) -> bool:
        """İptal istendi mi. İstek bağlamına da kurulur: beklemeler ve yeniden denemeler de buna bakar."""
        return self._store.cancel_requested(self.id)

    def log(self, message: str, **fields: Any) -> None:
        """
        İş günlüğüne ve kartın görev satırına bir satır yazar; yüzdeye dokunmaz. `fields` olayın verisine
        eklenir: `code` verilirse istemci metni koddan üretir (ör. code="fetch_zero_matches").
        """
        self._write(current_task=message, append_log=f"[Running] {message}")
        self.event(JobEventType.LOG, {"message": message, **fields})
        if self._on_log is not None:
            self._on_log(message)
        self._changed()

    def publish(self, fields: Mapping[str, Any]) -> None:
        """JobProgress'in taşımadığı iş alanlarını satıra yazar (bugün yalnızca `schedule_empty_seasons`)."""
        self._write(**dict(fields))
        self._changed()

    def _write(self, **fields: Any) -> None:
        """
        İşin satırına (ve yansıya) yazar. state.db meşgulse yazma atlanır: yansı güncellenmiştir, bir sonraki
        yazma satırı tamamlar; iş bu yüzden durmaz. Diğer hatalar çağırana çıkar.
        """
        try:
            self._store.update(job_id=self.id, **fields)
        except StoreBusy as e:
            self._busy_writes += 1
            log = logger.warning if self._busy_writes == 1 else logger.debug
            log("Job row could not be written, the state database is busy (job %s): %s", self.id, e)

    # --- olaylar -------------------------------------------------------------------------------------

    def event(self, type: str, data: Optional[Mapping[str, Any]] = None) -> None:
        """Olay günlüğüne bir olay ekler. Yazılamazsa iş sürer: ilk hata uyarı, sonrakiler ayıklama düzeyinde."""
        try:
            self._store.append_event(self.id, str(type), data or {})
        except Exception as e:  # olay günlüğü işi durdurmaz (kilit zaman aşımı, dolu disk)
            self._event_failures += 1
            log = logger.warning if self._event_failures == 1 else logger.debug
            log("Job event could not be stored (job %s, %s): %s", self.id, type, e)

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def _publish_progress(self, fields: Dict[str, Any]) -> None:
        """JobProgress'in her yayını: satıra hemen, olay günlüğüne seyreltilerek."""
        self._write(**fields)
        detail = fields.get("detail")
        if isinstance(detail, Mapping):
            self._events_from(detail, fields.get("progress"))
        self._changed()

    def _events_from(self, detail: Mapping[str, Any], percent: Any) -> None:
        """Ardışık iki ilerleme görüntüsünün farkından olaylar: aşama, devre kesici, başarısız öğe, ilerleme."""
        with self._events_lock:
            previous, self._last_detail = self._last_detail, dict(detail)
            due: List[tuple] = []
            if detail.get("phase") != previous.get("phase") and detail.get("phase") is not None:
                due.append((JobEventType.PHASE, {
                    "phase": detail.get("phase"), "phase_index": detail.get("phase_index"),
                    "phase_count": detail.get("phase_count"), "total": detail.get("total"),
                }))
            if detail.get("breaker") and not previous.get("breaker"):
                due.append((JobEventType.BREAKER, {"reason": detail.get("breaker")}))
            failed_before = int(previous.get("failed_count") or 0)
            failed_now = int(detail.get("failed_count") or 0)
            if failed_now > failed_before:
                listed = list(detail.get("failed") or [])
                new_entries = listed[len(list(previous.get("failed") or [])):]
                for entry in new_entries:
                    due.append((JobEventType.FAILED, dict(entry) if isinstance(entry, Mapping) else {"item": entry}))
                if not new_entries:  # liste sınırına (MAX_FAILED_LISTED) ulaşıldı: yalnızca sayı bilinir
                    due.append((JobEventType.FAILED, {"failed_count": failed_now}))
            # Seyreltme yalnızca ilerleme içindir: son yazılandan bu yana aralık dolmadıysa görüntü bekler ve
            # yerini bir sonraki alır; bekleyeni işin saati ya da bitişi yazar
            progress = {**detail, "percent": percent}
            now = time.monotonic()
            if now - self._last_progress_at >= self._progress_interval:
                due.append((JobEventType.PROGRESS, progress))
                self._pending_progress = None
                self._last_progress_at = now
            else:
                self._pending_progress = progress
        for type_, data in due:
            self.event(type_, data)

    def flush_progress(self, *, force: bool = False) -> None:
        """
        Seyreltme yüzünden bekleyen son ilerleme olayını yazar. İşin saati aralık dolduysa yazar (saniyede en
        çok iki ilerleme olayı kuralı bozulmaz); bitiş `force=True` ile her durumda yazar.
        """
        with self._events_lock:
            now = time.monotonic()
            if self._pending_progress is None or (
                    not force and now - self._last_progress_at < self._progress_interval):
                return
            pending, self._pending_progress = self._pending_progress, None
            self._last_progress_at = now
        self.event(JobEventType.PROGRESS, pending)

    def note_cancel(self) -> None:
        """İptal isteği fark edildi: olay günlüğüne bir kez yazılır."""
        with self._events_lock:
            if self._cancel_noted:
                return
            self._cancel_noted = True
        self.event(JobEventType.CANCEL_REQUESTED, {})
        self._changed()


class _Ticker(threading.Thread):
    """Çalışan işin saati: iptal bayrağını satırdan okur, kalp atışını yazar, bekleyen ilerleme olayını yazar."""

    def __init__(self, store: JobStore, handle: JobHandle, *, cancel_poll: float, heartbeat: float) -> None:
        super().__init__(name=f"job-ticker-{handle.id[:8]}", daemon=True)
        self._store = store
        self._handle = handle
        self._cancel_poll = max(0.01, float(cancel_poll))
        self._heartbeat = max(0.01, float(heartbeat))
        self._stopped = threading.Event()

    def run(self) -> None:
        last_heartbeat = time.monotonic()
        while not self._stopped.wait(self._cancel_poll):
            try:
                if self._store.poll_cancel(self._handle.id):
                    self._handle.note_cancel()
                if time.monotonic() - last_heartbeat >= self._heartbeat:
                    last_heartbeat = time.monotonic()
                    self._store.heartbeat(self._handle.id)
                self._handle.flush_progress()
            except Exception as e:  # saat işi durdurmaz; bir sonraki turda yeniden dener
                logger.debug("Job ticker round failed (job %s): %s", self._handle.id, e)

    def stop(self) -> None:
        self._stopped.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=self._cancel_poll + 5.0)


class JobManager:
    """Bir veri dizininin işleri: başlatma, yürütme, okuma, iptal. Durum taşımaz; her şey iş deposundadır."""

    def __init__(self, store: JobStore, *, cancel_poll: float = CANCEL_POLL_SECONDS,
                 heartbeat: float = HEARTBEAT_SECONDS, progress_interval: float = PROGRESS_EVENT_SECONDS) -> None:
        self._store = store
        self._cancel_poll = cancel_poll
        self._heartbeat = heartbeat
        self._progress_interval = progress_interval

    @property
    def store(self) -> JobStore:
        return self._store

    # --- başlatma ve yürütme -------------------------------------------------------------------------

    def start(self, kind: JobKind, spec: Mapping[str, Any], *, origin: Origin, wait_for_lease: float = 0.0,
              payload: Optional[Mapping[str, Any]] = None, lease_purpose: Optional[str] = None) -> Job:
        """
        `writer` kilidini alır ve işi "running" olarak kaydeder; işi yürütmez (`run`).

        payload        eski API'nin gösterdiği istek gövdesi (kartın başlığı ondan üretilir); verilmezse `spec`
        lease_purpose  kilidin amacı: aynı dizini isteyen başka bir süreç kullanıcıya bunu söyler (verilmezse "job")

        Kilit `wait_for_lease` saniye içinde alınamazsa JobRunningError / DataOperationRunningError; asıl
        LeaseHeld (sahibin bilgisiyle) hatanın `__cause__` alanındadır.
        """
        job_id = new_job_id()
        origin_data = {"face": origin.face, "pid": origin.pid, "host": origin.host}
        kind_value = str(JobKind(kind))
        # replace_running=False: bu süreçte çalışan bir işin üzerine ikinci iş açılmaz (JobRunningError)
        created: Dict[str, Any] = {"job_id": job_id, "kind": kind_value, "origin": origin_data, "spec": dict(spec),
                                   "wait": wait_for_lease, "replace_running": False}
        if lease_purpose is not None:
            created["purpose"] = lease_purpose
        self._store.create_running(dict(payload) if payload is not None else dict(spec), **created)
        self._append(job_id, JobEventType.STARTED, {"kind": kind_value, "origin": origin_data})
        self._announce(STREAM_JOB_STARTED, {"job_id": job_id, "kind": kind_value, "origin": origin_data})
        job = self.get(job_id)
        assert job is not None
        return job

    def run(self, job_id: str, fn: JobBody, *, phases: Sequence[str] = (),
            on_change: Optional[Callable[[], Any]] = None,
            on_log: Optional[Callable[[str], Any]] = None) -> Job:
        """
        Bu süreçte başlatılmış işi çağıranın thread'inde yürütür ve bitmiş işi döndürür.

        `fn(handle)` bir JobOutcome döndürebilir. Hata fırlatırsa iş `failed` olarak kaydedilir (Ctrl+C:
        `cancelled`), kilit bırakılır ve hata çağırana yeniden fırlatılır.

        phases     işin JobProgress aşamaları (`handle.progress` bunlarla kurulur)
        on_change  işin satırına her yazmadan sonra çağrılır (web: canlı yansıyı tazeler)
        on_log     her iş günlüğü satırıyla çağrılır (komut satırı: satırı log'a da yazar)
        """
        store = self._store
        snap = store.snapshot()
        if snap.get("job_id") != job_id or not snap.get("is_running"):
            raise JobNotActive(f"job {job_id} is not the running job of this process")
        record = store.get_record(job_id) or {}
        kind = str(record.get("kind") or JobKind.FETCH.value)
        handle = JobHandle(store, job_id, phases, on_change=on_change, on_log=on_log,
                           progress_interval=self._progress_interval)
        ticker = _Ticker(store, handle, cancel_poll=self._cancel_poll, heartbeat=self._heartbeat)
        ticker.start()
        # Bitiş bloğu (`_job_finishing`): işin bütün son depo erişimleri (bitiş satırı, `job.finished` akış
        # olayı, bitmiş işin geri okunması) `writer` kilidi bırakılmadan önce biter. Blok sürdükçe veri klasörü
        # değişimi JobRunningError alır ve deponun kapatılması bekler; state.db bağlantısı bu thread'in
        # altından kapatılamaz
        try:
            returned = fn(handle)
        except BaseException as exc:
            ticker.stop()
            with store._job_finishing():
                try:
                    self._finish(handle, kind, self._failure(handle, exc))
                except Exception as e:  # asıl hata çağırana gitmeli; kayıt yazılamadıysa satırı sonraki okuyan süpürür
                    logger.error("Job %s failed and its end could not be recorded: %s", job_id, e)
            raise
        ticker.stop()
        with store._job_finishing():
            self._finish(handle, kind, returned if isinstance(returned, JobOutcome) else JobOutcome())
            job = self.get(job_id)
        assert job is not None
        return job

    def submit(self, kind: JobKind, spec: Mapping[str, Any], fn: JobBody, *, origin: Origin, background: bool,
               wait_for_lease: float = 0.0, phases: Sequence[str] = (),
               payload: Optional[Mapping[str, Any]] = None, lease_purpose: Optional[str] = None,
               on_change: Optional[Callable[[], Any]] = None,
               on_log: Optional[Callable[[str], Any]] = None) -> Job:
        """
        İşi başlatır ve yürütür. background=True: iş kendi thread'inde çalışır ve başlamış iş hemen döner
        (web, zamanlayıcı); False: çağıranın thread'inde çalışır ve bitmiş iş döner (komut satırı, kitaplık;
        gövdenin hatası çağırana fırlar).

        Kilit `wait_for_lease` saniye içinde alınamazsa JobRunningError / DataOperationRunningError.
        """
        job = self.start(kind, spec, origin=origin, wait_for_lease=wait_for_lease, payload=payload,
                         lease_purpose=lease_purpose)
        if not background:
            return self.run(job.id, fn, phases=phases, on_change=on_change, on_log=on_log)

        def target() -> None:
            try:
                self.run(job.id, fn, phases=phases, on_change=on_change, on_log=on_log)
            except BaseException as e:  # arka plan thread'i: hata iş kaydındadır, burada yalnızca loglanır
                logger.error("Background job %s failed: %s: %s", job.id, type(e).__name__, redact_text(str(e)))

        threading.Thread(target=target, name=f"job-{job.kind}", daemon=True).start()
        return job

    # --- bitiş ---------------------------------------------------------------------------------------

    @staticmethod
    def _failure(handle: JobHandle, exc: BaseException) -> JobOutcome:
        """Gövdeden çıkan hata → bitiş. Ctrl+C iptaldir; diğer her şey hata tablosundaki koduyla `failed`."""
        if isinstance(exc, KeyboardInterrupt):
            return JobOutcome(state=JobState.CANCELLED, message="Cancelled")
        platform_error = to_platform_error(exc)
        # Hata metni iş kaydına yazılır ve API'den okunur: bir istek hatası proxy adresini parolasıyla
        # taşıyabilir, bu yüzden log satırları gibi maskelenir
        message = redact_text(platform_error.message)
        return JobOutcome(
            state=JobState.FAILED,
            error=ErrorInfo(code=platform_error.code, message=message, details=platform_error.details),
            message=f"Error: {redact_text(str(exc)) or type(exc).__name__}",
        )

    def _finish(self, handle: JobHandle, kind: str, outcome: JobOutcome) -> None:
        store = self._store
        summary = handle.progress.result()
        breaker = summary.get("breaker")
        # Başka bir sürecin son saniyede yazdığı iptal isteği de sayılır
        try:
            store.poll_cancel(handle.id)
        except Exception as e:
            logger.debug("Cancel flag could not be read at the end of job %s: %s", handle.id, e)
        cancel_requested = handle.cancelled()
        if cancel_requested:
            handle.note_cancel()

        state = outcome.state
        if state is None:
            state = terminal_state(cancelled=cancel_requested, breaker=bool(breaker),
                                   failed_items=int(summary.get("failed_count") or 0))
        elif cancel_requested and state in (JobState.SUCCEEDED, JobState.PARTIAL):
            state = JobState.CANCELLED  # iptal isteğinden sonra biten iş iptal edilmiş sayılır
        error = outcome.error
        if error is None and state is JobState.PARTIAL and breaker:
            error = breaker_error(str(breaker))
        message = outcome.message if outcome.message is not None else _DEFAULT_MESSAGE[state]
        percent = outcome.percent
        if percent is None:
            percent = 100 if state in (JobState.SUCCEEDED, JobState.PARTIAL) else handle.progress.percent()
        error_data = None if error is None else {"code": error.code, "message": error.message,
                                                 "details": dict(error.details) if error.details else None}

        handle.flush_progress(force=True)
        finished: Dict[str, Any] = {"state": state.value, "message": message}
        if outcome.code:
            finished["code"] = outcome.code
            finished["params"] = dict(outcome.params)
        if error_data is not None:
            finished["error"] = error_data
        # `finished` olayı satır bitmeden önce yazılır: olayları izleyen (`events(follow=True)`), işin bittiğini
        # satırdan görüp son kez okuduğunda bu olayı da bulur
        handle.event(JobEventType.FINISHED, finished)

        legacy_status = _LEGACY_STATUS[state]
        log_line = f"[{legacy_status}] {message}"
        for attempt in range(1, FINAL_WRITE_ATTEMPTS + 1):
            try:
                store.update(
                    job_id=handle.id,
                    status=legacy_status,
                    progress=int(percent),
                    current_task=message,
                    # Yinelenen denemede satır yansıya bir kez daha eklenmez
                    append_log=log_line if attempt == 1 else None,
                    result=dict(outcome.result) if outcome.result is not None else None,
                    state=state.value,
                    error=error_data,
                    finished=True,
                )
                break
            except StoreBusy as e:
                if attempt == FINAL_WRITE_ATTEMPTS:
                    raise
                logger.warning("The end of job %s could not be written, the state database is busy; "
                               "trying again (%d/%d): %s", handle.id, attempt, FINAL_WRITE_ATTEMPTS, e)
        handle._changed()
        self._announce(STREAM_JOB_FINISHED, {
            "job_id": handle.id,
            "kind": kind,
            "state": state.value,
            "counts": {key: summary.get(key) for key in
                       ("details_done", "details_total", "failed_count", "refreshed", "refresh_changed")},
            "error_code": error.code if error is not None else None,
        })

    # --- okuma ve iptal ------------------------------------------------------------------------------

    def get(self, job_id: str) -> Optional[Job]:
        """Kimliği verilen iş (hangi süreç çalıştırmış olursa olsun) ya da None."""
        record = self._store.get_record(job_id)
        return job_from_record(record) if record is not None else None

    def list(self, *, limit: int = 20, kinds: Optional[Iterable[JobKind]] = None,
             states: Optional[Iterable[JobState]] = None) -> List[Job]:
        """İşler, en yeni başlayan önce; `kinds` ve `states` verilirse yalnızca onlar."""
        wanted_kinds = None if kinds is None else {JobKind(k) for k in kinds}
        wanted_states = None if states is None else {JobState(s) for s in states}
        found: List[Job] = []
        for record in self._store.list_records():
            job = job_from_record(record)
            if wanted_kinds is not None and job.kind not in wanted_kinds:
                continue
            if wanted_states is not None and job.state not in wanted_states:
                continue
            found.append(job)
            if len(found) >= max(1, int(limit)):
                break
        return found

    def active(self) -> Optional[Job]:
        """
        Veri dizininde şu an çalışan iş (bu süreçte ya da başka bir süreçte) ya da None. Bitmiş iş "çalışan iş"
        olarak dönmez; sahibi ölmüş bir satır da (önce `interrupted` yapılır).
        """
        record = self._store.active_record()
        return job_from_record(record) if record is not None else None

    def cancel(self, job_id: str) -> bool:
        """
        İşin iptalini ister; iş hangi süreçte çalışırsa çalışsın. İş çalışıyorsa True. İşi çalıştıran süreç
        isteği en geç CANCEL_POLL_SECONDS içinde görür; istekler ve beklemeler saniyenin kesri içinde durur.
        """
        return self._store.cancel(job_id)

    def events(self, job_id: str, *, after: int = 0, follow: bool = False,
               poll: float = EVENTS_POLL_SECONDS) -> Iterator[JobEvent]:
        """
        İşin olayları, sıra numarası `after`dan büyük olanlar. follow=True: iş bitene kadar yeni olayları da
        verir (başka bir sürecin işi için de; satır `poll` saniyede bir yoklanır).
        """
        position = int(after)
        while True:
            batch = self._store.read_events(job_id, after=position)
            for record in batch:
                position = int(record["seq"])
                yield job_event_from_record(record)
            if batch:
                continue
            if not follow:
                return
            job = self.get(job_id)
            if job is None or job.state.terminal:
                # İş bitti: bitişten hemen önce yazılmış olaylar için son bir okuma
                for record in self._store.read_events(job_id, after=position):
                    position = int(record["seq"])
                    yield job_event_from_record(record)
                return
            time.sleep(max(0.01, float(poll)))

    def reap_stale(self) -> int:
        """Sahibi ölmüş işleri (satır "running", `writer` kilidi boşta) `interrupted` yapar; sayısını döndürür."""
        return self._store.reap_stale()

    # --- yardımcılar ---------------------------------------------------------------------------------

    def _append(self, job_id: str, type: str, data: Mapping[str, Any]) -> None:
        try:
            self._store.append_event(job_id, str(type), data)
        except Exception as e:
            logger.warning("Job event could not be stored (job %s, %s): %s", job_id, type, e)

    def _announce(self, type: str, data: Mapping[str, Any]) -> None:
        """`job` akışına bir olay ekler; yazılamazsa (ör. StoreBusy) iş sürer ve yeniden denenmez."""
        try:
            self._store.announce(type, data)
        except Exception as e:
            logger.warning("Stream event %s could not be stored (job %s): %s", type, data.get("job_id"), e)


__all__ = [
    "CANCEL_POLL_SECONDS",
    "HEARTBEAT_SECONDS",
    "JobBody",
    "JobHandle",
    "JobManager",
    "JobNotActive",
    "JobOutcome",
    "local_origin",
]
