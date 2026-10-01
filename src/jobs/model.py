"""İş modeli: web ve CLI için tek bir Job tanımı (docs/design/02-services.md, bölüm 2.8).

Saf veri: SQL, dosya erişimi ve süreç bilgisi (pid, host) okuma yok. Satırları okuyup yazmak depo
katmanının, işi yürütmek JobManager'ın görevi; ikisi de bu türleri kullanır.

Bugünkü iş deposu (`src/web/jobs.py`) durumu düz metin olarak saklar: running, queued, completed,
failed, cancelled, interrupted. `job_state_from_status` bu metinleri JobState'e çevirir.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal, Mapping, Optional


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
