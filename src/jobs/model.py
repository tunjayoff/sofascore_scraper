"""İş modeli: web ve CLI için tek bir Job tanımı (docs/design/02-services.md, bölüm 2.8).

Saf veri: SQL, dosya erişimi ve süreç bilgisi (pid, host) okuma yok. Satırları okuyup yazmak depo
katmanının, işi yürütmek JobManager'ın görevi; ikisi de bu türleri kullanır.

İş deposu (`src/store/jobs.py`) durumu düz metin olarak saklar: running, queued, completed, partial,
failed, cancelled, interrupted. `job_state_from_status` bu metinleri JobState'e çevirir; `job_from_record`
deponun tam kaydını (`JobStore.get_record`) bir Job'a çevirir.

Bu modülde ayrıca: işin olay günlüğündeki bir satır (`JobEvent`) ve olay türleri, sıralanabilir iş kimliği
(`new_job_id`), bitiş durumu kuralı (`terminal_state`) ve devre kesici nedeninden hata koduna eşleme.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal, Mapping, Optional, Tuple


class _ValueEnum(str, Enum):
    """Metne çevrildiğinde (str, format, f-string) değerini veren enum; Python 3.10 ve sonrası aynı davranır."""

    def __str__(self) -> str:
        return str(self.value)


class JobKind(_ValueEnum):
    """İşin ne yaptığı. Bugünkü web işi `FETCH` türündedir."""

    SYNC = "sync"
    FETCH = "fetch"
    REFRESH = "refresh"
    EXPORT = "export"
    BACKUP = "backup"
    RESTORE = "restore"
    CLEAR = "clear"
    MIGRATE = "migrate"
    REBUILD = "rebuild"


class JobState(_ValueEnum):
    """İşin durumu. `QUEUED` ve `RUNNING` dışındakiler son durumdur."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def terminal(self) -> bool:
        """İş bitti mi (başarılı, yarım, hatalı, iptal ya da yarıda kesilmiş)."""
        return self not in _ACTIVE_STATES


_ACTIVE_STATES = frozenset({JobState.QUEUED, JobState.RUNNING})

# İşi hangi yüz başlattı
OriginFace = Literal["cli", "api", "scheduler", "library"]


@dataclass(frozen=True)
class Origin:
    """İşi başlatan yüz ve süreç. pid/host'u çağıran doldurur; bu modül süreç bilgisi okumaz."""

    face: OriginFace
    pid: Optional[int] = None
    host: Optional[str] = None


@dataclass(frozen=True)
class ErrorInfo:
    """İşi durduran hata: hata tablosundaki kod (02-services.md 2.6) ve okunur mesaj."""

    code: str
    message: str
    details: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class Job:
    """Bir işin anlık görüntüsü.

    `progress`, JobProgress.detail() çıktısıdır. `created_at`, `started_at` ve `finished_at`
    bugünkü satırlardaki gibi ISO-8601 UTC metnidir; `heartbeat_at` epoch milisaniyedir.
    """

    id: str
    kind: JobKind
    state: JobState
    origin: Origin
    spec: Mapping[str, Any]
    progress: Optional[Mapping[str, Any]] = None
    result: Optional[Mapping[str, Any]] = None
    error: Optional[ErrorInfo] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    heartbeat_at: Optional[int] = None
    cancel_requested: bool = False


# Bugünkü `jobs.status` sütununun alabildiği değerler (src/web/jobs.py) ve karşılıkları.
# Canlı yansıdaki büyük harfli biçimler (Running, Completed, Failed, Cancelled) da aynı tabloya düşer.
LEGACY_STATUS_TO_STATE: Mapping[str, JobState] = MappingProxyType(
    {
        "queued": JobState.QUEUED,
        "running": JobState.RUNNING,
        "completed": JobState.SUCCEEDED,
        "failed": JobState.FAILED,
        "cancelled": JobState.CANCELLED,
        "interrupted": JobState.INTERRUPTED,
    }
)


def job_state_from_status(status: Optional[str], *, breaker_triggered: bool = False) -> Optional[JobState]:
    """Bugünkü durum metnini JobState'e çevirir; büyük/küçük harf ve kenar boşlukları önemsizdir.

    `breaker_triggered`: satırın `circuit_breaker_triggered` değeri. Devre kesicinin durdurduğu iş
    bugün "completed" olarak yazılır; yeni modelde bu `PARTIAL`'dır. Diğer durumları değiştirmez.

    Yeni durum adları (succeeded, partial, ...) kendilerine çevrilir, yani işlev kendi çıktısıyla
    yeniden çağrılabilir. İş olmadığını söyleyen "Idle" ve tanınmayan metinler için None döner.
    """
    if not isinstance(status, str):
        return None
    key = status.strip().lower()
    state = LEGACY_STATUS_TO_STATE.get(key)
    if state is None:
        try:
            state = JobState(key)
        except ValueError:
            return None
    if state is JobState.SUCCEEDED and breaker_triggered:
        return JobState.PARTIAL
    return state


def terminal_state(*, failed: bool = False, cancelled: bool = False, breaker: bool = False,
                   failed_items: int = 0) -> JobState:
    """Bitiş durumu kuralı (02-services.md 2.8).

    Ölümcül bir hata işi durdurduysa `FAILED`; değilse iptal edildiyse `CANCELLED`; değilse devre kesici
    durdurduysa ya da en az bir öğe başarısızsa `PARTIAL`; değilse `SUCCEEDED`.
    """
    if failed:
        return JobState.FAILED
    if cancelled:
        return JobState.CANCELLED
    if breaker or failed_items > 0:
        return JobState.PARTIAL
    return JobState.SUCCEEDED


# Devre kesicinin nedeni (src/breaker.py: "403" | "429" | "5xx" | "other") → hata tablosundaki kod (src/errors.py)
BREAKER_ERROR_CODES: Mapping[str, str] = MappingProxyType({"403": "blocked", "429": "rate_limited"})
BREAKER_ERROR_DEFAULT = "upstream_error"


def breaker_error(reason: str) -> ErrorInfo:
    """Devre kesicinin durdurduğu işin hatası: kod nedenden gelir, metni istemci üretir."""
    code = BREAKER_ERROR_CODES.get(str(reason), BREAKER_ERROR_DEFAULT)
    return ErrorInfo(code=code, message=f"stopped by the circuit breaker ({reason})", details={"reason": str(reason)})


# --- iş olayları ---------------------------------------------------------------------


class JobEventType(_ValueEnum):
    """İşin olay günlüğündeki (`job_events`) olay türleri."""

    STARTED = "started"                    # {kind, origin}
    PHASE = "phase"                        # {phase, phase_index, phase_count, total}
    PROGRESS = "progress"                  # JobProgress.detail() + {percent}; saniyede en çok iki tane
    LOG = "log"                            # {message, ...alanlar}; `code` varsa metni istemci üretir
    FAILED = "failed"                      # bir öğe başarısız: {match_id, league_id?}
    BREAKER = "breaker"                    # devre kesildi: {reason}
    CANCEL_REQUESTED = "cancel_requested"  # iptal istendi (herhangi bir süreçten)
    FINISHED = "finished"                  # {state, error?, message?, code?}


@dataclass(frozen=True)
class JobEvent:
    """İşin olay günlüğünden bir satır. `seq` iş başına 1'den başlar; `ts_ms` epoch milisaniyedir."""

    job_id: str
    seq: int
    ts_ms: int
    type: str
    data: Mapping[str, Any] = field(default_factory=dict)


def job_event_from_record(record: Mapping[str, Any]) -> JobEvent:
    """`JobStore.read_events` satırı → JobEvent."""
    data = record.get("data")
    return JobEvent(
        job_id=str(record["job_id"]), seq=int(record["seq"]), ts_ms=int(record["ts_ms"]),
        type=str(record["type"]), data=data if isinstance(data, Mapping) else {},
    )


# --- iş kimliği ----------------------------------------------------------------------

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_id_lock = threading.Lock()
_last_id: Tuple[int, int] = (0, 0)  # (milisaniye, rastgele kısım): aynı milisaniyede üretilen kimlikler de artar


def new_job_id(now_ms: Optional[int] = None) -> str:
    """Sıralanabilir iş kimliği (ULID): 48 bit zaman (epoch ms) + 80 bit rastgele, 26 karakter Crockford base32.

    Metin sırası yaratılış sırasıdır: kimliğe göre sıralanan işler başlama sırasıyla gelir. Aynı süreçte aynı
    milisaniyede üretilen kimlikler de artar (rastgele kısım bir artırılır). `now_ms` yalnızca testler içindir.
    """
    global _last_id
    ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    with _id_lock:
        last_ms, last_random = _last_id
        if ms <= last_ms:
            ms, random_part = last_ms, (last_random + 1) & ((1 << 80) - 1)
        else:
            random_part = secrets.randbits(80)
        _last_id = (ms, random_part)
    value = ((ms & ((1 << 48) - 1)) << 80) | random_part
    return "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


# --- depo kaydından Job'a --------------------------------------------------------------

_FACES = frozenset({"cli", "api", "scheduler", "library"})


def _origin_from(raw: Any) -> Origin:
    """`origin_json` → Origin. Geçiş 0002'den önce yazılan satırlarda alan yoktur: o işleri web başlattı."""
    if not isinstance(raw, Mapping) or raw.get("face") not in _FACES:
        return Origin(face="api")
    pid, host = raw.get("pid"), raw.get("host")
    return Origin(
        face=raw["face"],
        pid=pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
        host=host if isinstance(host, str) else None,
    )


def _error_from(raw: Any) -> Optional[ErrorInfo]:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("code"), str):
        return None
    details = raw.get("details")
    return ErrorInfo(code=raw["code"], message=str(raw.get("message") or ""),
                     details=details if isinstance(details, Mapping) else None)


def job_from_record(record: Mapping[str, Any]) -> Job:
    """İş deposunun tam kaydı (`JobStore.get_record` / `list_records`) → Job.

    Durum adları okurken normalleştirilir: `completed` satırı SUCCEEDED'dır, devre kesici bayrağı duruyorsa
    PARTIAL (plan kararı D15: eski satırlarda yalnızca bu bayrağa bakılır). Tanınmayan bir durum metni
    FAILED sayılır: neyle bittiği bilinmeyen iş başarılı gösterilmez. `created_at` sütunu olmayan (geçiş
    0002'den önce yazılmış) satırlar `started_at` değerini verir; `spec` yoksa istek gövdesi (`payload`) okunur.
    """
    state = job_state_from_status(record.get("status"), breaker_triggered=bool(record.get("circuit_breaker_triggered")))
    try:
        kind = JobKind(str(record.get("kind") or JobKind.FETCH.value))
    except ValueError:
        kind = JobKind.FETCH
    spec = record.get("spec")
    if not isinstance(spec, Mapping):
        spec = record.get("payload") if isinstance(record.get("payload"), Mapping) else {}
    detail, result = record.get("detail"), record.get("result")
    heartbeat = record.get("heartbeat_at")
    return Job(
        id=str(record["id"]),
        kind=kind,
        state=state if state is not None else JobState.FAILED,
        origin=_origin_from(record.get("origin")),
        spec=spec,
        progress=detail if isinstance(detail, Mapping) else None,
        result=result if isinstance(result, Mapping) else None,
        error=_error_from(record.get("error")),
        created_at=record.get("created_at") or record.get("started_at"),
        started_at=record.get("started_at"),
        finished_at=record.get("finished_at"),
        heartbeat_at=int(heartbeat) if isinstance(heartbeat, (int, float)) and not isinstance(heartbeat, bool) else None,
        cancel_requested=bool(record.get("cancel_requested")),
    )
