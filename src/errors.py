"""
Platform hataları ve hata tablosu: üç yüz (CLI, HTTP API, Python kitaplığı) için tek kaynak
(docs/design/02-services.md bölüm 2.6).

Tablo her hata kodu için sınıfı, CLI çıkış kodunu ve HTTP durumunu verir. `describe errors` bu tabloyu
yazdırır; CLI çıkış kodunu, API v1 (P20) HTTP durumunu buradan okur. Kodlar sözleşmenin parçasıdır:
eklenebilir, anlamı değiştirilemez.

İki tür sınıf vardır:

  * `PlatformError` ve alt sınıfları: bu modülde tanımlanır; servisler ve yüzler bunları fırlatır.
  * devralınan sınıflar: bu modülden önce var olan ve yerinde kalan istisnalar. `ConfigError` ve
    `StorageError` (src/exceptions.py; her `StoreError` bir `StorageError`dır), Store'un `LeaseHeld`,
    `FollowExists` ve `FollowManaged` sınıfları, iş deposunun `JobRunningError` ve
    `DataOperationRunningError` sınıfları. `to_platform_error` bunları tablodaki koda çevirir.

İstek katmanının istisnaları (src/exceptions.py: APIError, RateLimitError, ...) istemcinin içinde kalır;
tabloya girmez.

Bu modül yalnızca standart kütüphaneyi ve src/exceptions.py'yi içe aktarır: `--version` ve `doctor`
paketler kurulmadan da çalışır, Store ise bu modülü içe aktaramaz (katman kuralı). Store sınıfları bu
yüzden içe aktarılmaz; yüklüyse `sys.modules` üzerinden tanınır.

İletiler (`message`) İngilizcedir ve yerelleştirilmez (02-services.md 4.4).
"""
from __future__ import annotations

import signal
import sys
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Dict, Iterator, Mapping, Optional, Tuple, Type

from src.exceptions import ConfigError, StorageError


@dataclass(frozen=True)
class ErrorSpec:
    """
    Hata tablosunun bir satırı.

    error_class  kodu taşıyan sınıfın adı; None: bir istisna sınıfı yok (yalnızca HTTP katmanında üretilir,
                 bir sonuç durumudur ya da beklenmeyen hatadır)
    exit_code    CLI çıkış kodu; None: CLI'de oluşmaz
    exit_codes   kodun alabildiği bütün çıkış kodları (`cancelled`: 130 ve 143)
    http_status  HTTP durumları; boş: HTTP'de oluşmaz
    raised       False: fırlatılmaz, bir işin sonuç durumudur (`partial`)
    """

    code: str
    error_class: Optional[str]
    exit_code: Optional[int]
    http_status: Tuple[int, ...]
    meaning: str
    exit_codes: Tuple[int, ...] = ()
    raised: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "class": self.error_class,
            "exit_code": self.exit_code,
            "exit_codes": list(self.exit_codes or ((self.exit_code,) if self.exit_code is not None else ())),
            "http_status": list(self.http_status),
            "raised": self.raised,
            "meaning": self.meaning,
        }


EXIT_SIGINT = 128 + int(signal.SIGINT)    # 130
EXIT_SIGTERM = 128 + int(signal.SIGTERM)  # 143

# Sıra tasarım belgesindeki tablonun sırasıdır (02-services.md 2.6).
ERROR_TABLE: Tuple[ErrorSpec, ...] = (
    ErrorSpec("invalid_request", "UsageError", 2, (400, 422), "bad arguments or body"),
    ErrorSpec("config_invalid", "ConfigError", 2, (500,), "config file or environment value rejected (HTTP: at start-up)"),
    ErrorSpec("confirmation_required", "UsageError", 2, (400,), "destructive action without --yes / confirm: true"),
    ErrorSpec("unauthorized", None, None, (401,), "access token missing or wrong"),
    ErrorSpec("forbidden_origin", None, None, (403,), "cross-origin write"),
    ErrorSpec("not_found", "NotFoundError", 1, (404,), "unknown id or slice"),
    ErrorSpec("follow_managed", "ConflictError", 1, (409,), "the follow comes from the config file"),
    ErrorSpec("follow_exists", "ConflictError", 1, (409,), "duplicate follow"),
    ErrorSpec("job_running", "JobRunningError", 6, (409,), "a job holds the writer lease in this or another process"),
    ErrorSpec(
        "data_operation_running", "ConflictError", 6, (409,), "backup, clear, restore, migrate or rebuild in progress",
    ),
    ErrorSpec(
        "instance_running", "InstanceRunningError", 6, (409,),
        "the live service or the sink dispatcher is already running on this data directory",
    ),
    ErrorSpec("blocked", "UpstreamBlockedError", 4, (503,), "SofaScore refuses us (breaker reason 403 or bridge blocked)"),
    ErrorSpec("rate_limited", "UpstreamBlockedError", 4, (503,), "breaker reason 429"),
    ErrorSpec("upstream_error", "UpstreamError", 4, (502,), "breaker reason 5xx or other"),
    ErrorSpec("partial", None, 3, (200,), "finished with failed items (a result state, not raised)", raised=False),
    ErrorSpec(
        "storage_error", "StorageError", 5, (507,),
        "disk full, permission, corrupt store, store busy, schema too new (every StoreError)",
    ),
    ErrorSpec("not_supported", "NotSupportedError", 2, (501,), "e.g. Parquet without pyarrow; write on a read-only platform"),
    ErrorSpec(
        "cancelled", "Cancelled", EXIT_SIGINT, (), "signal or cancel request (130: SIGINT, 143: SIGTERM)",
        exit_codes=(EXIT_SIGINT, EXIT_SIGTERM),
    ),
    ErrorSpec("internal", None, 1, (500,), "bug"),
)

ERRORS: Mapping[str, ErrorSpec] = MappingProxyType({spec.code: spec for spec in ERROR_TABLE})
INTERNAL = "internal"


def error_spec(code: str) -> ErrorSpec:
    """Kodun tablo satırı; bilinmeyen kod `internal` satırıdır (tabloda olmayan kod bir hatadır, çökme nedeni değil)."""
    return ERRORS.get(code, ERRORS[INTERNAL])


# --- sınıflar ----------------------------------------------------------------------------------------


class PlatformError(Exception):
    """
    Tablodaki bir kodu taşıyan hata: `PlatformError(code, message, details=None)`.

    `codes`: sınıfın taşıyabildiği kodlar (ilki varsayılan). Taban sınıfta boştur: tablodaki her kodu
    taşıyabilir (sınıfı olmayan `internal` gibi kodlar için).
    """

    codes: ClassVar[Tuple[str, ...]] = ()

    def __init__(self, code: str, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        if code not in ERRORS:
            raise ValueError(f"unknown error code: {code!r}")
        allowed = type(self).codes
        if allowed and code not in allowed:
            raise ValueError(f"{type(self).__name__} cannot carry the code {code!r} (allowed: {', '.join(allowed)})")
        self.code = code
        self.message = message
        self.details: Optional[Dict[str, Any]] = dict(details) if details else None
        super().__init__(message)

    @property
    def spec(self) -> ErrorSpec:
        return ERRORS[self.code]

    def to_dict(self) -> Dict[str, Any]:
        """`{"code", "message", "details"}`: CLI zarfındaki ve API yanıtındaki `error` nesnesi."""
        return {"code": self.code, "message": self.message, "details": self.details}


class _CodedError(PlatformError):
    """Kodu sınıfından gelen hatalar: `Sınıf(message, details=None, code=None)`."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None, *, code: Optional[str] = None) -> None:
        super().__init__(code or type(self).codes[0], message, details)


class UsageError(_CodedError):
    """Geçersiz argüman ya da gövde; `--yes` olmadan istenen yıkıcı işlem (`confirmation_required`)."""

    codes = ("invalid_request", "confirmation_required")


class NotFoundError(_CodedError):
    """Bilinmeyen kimlik ya da dilim."""

    codes = ("not_found",)


class ConflictError(_CodedError):
    """İstek şu anki durumla çakışıyor: takip zaten var ya da dosyadan yönetiliyor, bir veri işlemi sürüyor."""

    codes = ("follow_exists", "follow_managed", "data_operation_running")


class InstanceRunningError(_CodedError):
    """Bu veri dizininde canlı servis ya da sink dağıtıcısı zaten çalışıyor."""

    codes = ("instance_running",)


class UpstreamError(_CodedError):
    """SofaScore hata vermeye devam ediyor; devre kesici işi durdurdu."""

    codes = ("upstream_error",)


class UpstreamBlockedError(UpstreamError):
    """SofaScore bizi engelliyor (`blocked`) ya da yavaşlatıyor (`rate_limited`)."""

    codes = ("blocked", "rate_limited")


class NotSupportedError(_CodedError):
    """İstenen şey bu kurulumda yapılamaz (ör. `pyarrow` olmadan Parquet)."""

    codes = ("not_supported",)


class Cancelled(_CodedError):
    """İşlem bir sinyalle ya da iptal isteğiyle durduruldu. `signal_number`: SIGINT (130) ya da SIGTERM (143)."""

    codes = ("cancelled",)

    def __init__(self, message: str = "cancelled", details: Optional[Mapping[str, Any]] = None, *,
                 signal_number: int = int(signal.SIGINT)) -> None:
        self.signal_number = int(signal_number)
        super().__init__(message, details)

    @property
    def exit_code(self) -> int:
        return 128 + self.signal_number


# --- devralınan sınıflar -----------------------------------------------------------------------------

# Kilit adından koda (src/store/lease.py): `writer` bir iştir, `maintenance` bir veri işlemidir, diğerleri
# (live, watcher:<spor>, sinks) çalışan bir servistir.
_LEASE_WRITER = "writer"
_LEASE_MAINTENANCE = "maintenance"
# Veri işlemleri `writer` kilidini "op:<ad>" amacıyla tutar (src/store/jobs.py)
_OPERATION_PREFIX = "op:"


def _loaded(module: str, name: str) -> Optional[type]:
    """Yüklenmiş bir modüldeki sınıf; modül yüklü değilse None (o sınıfın bir örneği de var olamaz)."""
    found = getattr(sys.modules.get(module), name, None)
    return found if isinstance(found, type) else None


def _iso_utc(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch)))
    except (OverflowError, OSError, ValueError):
        return None


def lease_error_code(name: str, purpose: str = "") -> str:
    """Tutulan kilidin adından ve amacından hata kodu: `job_running`, `data_operation_running`, `instance_running`."""
    if (purpose or "").startswith(_OPERATION_PREFIX) or name == _LEASE_MAINTENANCE:
        return "data_operation_running"
    if name == _LEASE_WRITER:
        return "job_running"
    return "instance_running"


def _from_lease(exc: BaseException) -> PlatformError:
    name = str(getattr(exc, "name", "") or "")
    purpose = str(getattr(exc, "purpose", "") or "")
    code = lease_error_code(name, purpose)
    holder = {
        "lease": name or None,
        "pid": getattr(exc, "pid", None),
        "host": getattr(exc, "host", None),
        "purpose": purpose or None,
        "since": _iso_utc(getattr(exc, "started_at", None)),
    }
    what = {
        "job_running": "A job is already running on this data directory.",
        "data_operation_running": "A data operation is in progress on this data directory.",
        "instance_running": "Another instance is already running on this data directory.",
    }[code]
    return PlatformError(code, what, {"holder": holder})


def _from_storage(exc: StorageError) -> PlatformError:
    detail = str(getattr(exc, "detail", "") or "")
    path = getattr(exc, "path", None)
    message = f"storage error: {detail}" if detail else (str(exc) or type(exc).__name__)
    if detail and path:
        message += f" ({path})"
    return PlatformError("storage_error", message, {
        "class": type(exc).__name__, "path": path, "errno": getattr(exc, "errno", None), "reason": detail or None,
    })


def to_platform_error(exc: BaseException) -> PlatformError:
    """
    Herhangi bir istisnayı tablodaki bir koda bağlar. `PlatformError` olduğu gibi döner; devralınan sınıflar
    kodlarına çevrilir; Ctrl+C `cancelled`, tanınmayan her şey `internal` olur.
    """
    if isinstance(exc, PlatformError):
        return exc
    if isinstance(exc, KeyboardInterrupt):
        return Cancelled("cancelled by the user (SIGINT)")
    if isinstance(exc, ConfigError):
        return PlatformError("config_invalid", str(exc))

    store = "src.store.errors"
    lease_held = _loaded(store, "LeaseHeld")
    if lease_held is not None and isinstance(exc, lease_held):
        return _from_lease(exc)
    for name, code in (("FollowExists", "follow_exists"), ("FollowManaged", "follow_managed")):
        cls = _loaded(store, name)
        if cls is not None and isinstance(exc, cls):
            return ConflictError(str(exc), code=code)
    if isinstance(exc, StorageError):
        return _from_storage(exc)

    # İş deposunun çakışma hataları kendi kodlarını taşır (src/store/jobs.py: JobRunningError, DataOperationRunningError)
    conflict = _loaded("src.store.jobs", "JobStoreConflict")
    code = getattr(exc, "code", None)
    if conflict is not None and isinstance(exc, conflict) and isinstance(code, str) and code in ERRORS:
        return PlatformError(code, str(exc))

    text = str(exc)
    return PlatformError(INTERNAL, f"{type(exc).__name__}: {text}" if text else type(exc).__name__)


# --- tablo ile sınıfların eşleşmesi --------------------------------------------------------------------


def platform_error_classes() -> Tuple[Type[PlatformError], ...]:
    """Kod taşıyan bütün `PlatformError` alt sınıfları (bu modülün dışında tanımlananlar dahil)."""

    def walk(cls: Type[PlatformError]) -> Iterator[Type[PlatformError]]:
        for sub in cls.__subclasses__():
            if sub.codes and not sub.__name__.startswith("_"):
                yield sub
            yield from walk(sub)

    return tuple(dict.fromkeys(walk(PlatformError)))


# Tabloda adı geçen ama bu modülde tanımlanmayan sınıflar ve tanımlandıkları modül
ADOPTED_CLASSES: Mapping[str, str] = MappingProxyType({
    "ConfigError": "src.exceptions",
    "StorageError": "src.exceptions",
    "JobRunningError": "src.store",
})


__all__ = [
    "ADOPTED_CLASSES",
    "ERRORS",
    "ERROR_TABLE",
    "EXIT_SIGINT",
    "EXIT_SIGTERM",
    "INTERNAL",
    "Cancelled",
    "ConfigError",
    "ConflictError",
    "ErrorSpec",
    "InstanceRunningError",
    "NotFoundError",
    "NotSupportedError",
    "PlatformError",
    "StorageError",
    "UpstreamBlockedError",
    "UpstreamError",
    "UsageError",
    "error_spec",
    "lease_error_code",
    "platform_error_classes",
    "to_platform_error",
]
