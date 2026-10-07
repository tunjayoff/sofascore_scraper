"""
Store hata sınıfları (docs/design/01-storage.md, bölüm 2.3).

StoreError, bugünkü StorageError'ın alt sınıfıdır: `fatal` özelliği aynen çalışır, bu yüzden
"disk dolu → işi durdur" diyen çağıranlar (sofascore_scraper/services/pipeline.py, sofascore_scraper/services/sync.py)
Store hatalarını da aynı `except StorageError` ile yakalar.

Bütün sınıflar tabanın kurucu imzasını (message, path, errno_code, detail) korur; sınıfa özgü
alanlar yalnızca anahtar sözcükle verilir. Böylece `from_exception` her alt sınıfta çalışır.
"""
from __future__ import annotations

from typing import Optional

from sofascore_scraper.exceptions import StorageError


class StoreError(StorageError):
    """Store'un fırlattığı her hatanın tabanı."""

    default_message = "A storage error occurred"

    def __init__(self, message: Optional[str] = None, path: Optional[str] = None,
                 errno_code: Optional[int] = None, detail: str = ""):
        super().__init__(message or self.default_message, path=path, errno_code=errno_code, detail=detail)

    @classmethod
    def from_exception(cls, exc: BaseException, path: Optional[str] = None, *,
                       reading: bool = False) -> "StoreError":
        """
        OSError (ya da serileştirme hatası) → StoreError; yol ve errno korunur, `fatal` errno'dan
        hesaplanır. reading=True: hata bir okuma sırasında oluştu (ileti buna göre yazılır).
        """
        code = exc.errno if isinstance(exc, OSError) else None
        where = path or (getattr(exc, "filename", None) if isinstance(exc, OSError) else None)
        detail = (exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)) or type(exc).__name__
        message = f"Data could not be read from disk ({detail})" if reading else f"Data could not be written to disk ({detail})"
        if where:
            message += f": {where}"
        return cls(message, path=where, errno_code=code, detail=detail)


class LeaseHeld(StoreError):
    """İstenen kilit (lease) başka bir sahipte. Alanlar `leases` tablosundan okunan sahip bilgisidir."""

    default_message = "The data directory is used by another process"

    def __init__(self, message: Optional[str] = None, path: Optional[str] = None,
                 errno_code: Optional[int] = None, detail: str = "", *,
                 name: str = "", pid: Optional[int] = None, host: Optional[str] = None,
                 purpose: str = "", started_at: Optional[float] = None):
        self.name = name
        self.pid = pid
        self.host = host
        self.purpose = purpose
        self.started_at = started_at  # epoch saniye
        super().__init__(message, path=path, errno_code=errno_code, detail=detail)


class StoreBusy(StoreError):
    """Yazma kilidi `busy_timeout` içinde alınamadı (başka bir süreç yazıyor)."""

    default_message = "The store is busy: the write lock could not be taken"


class UnknownEvent(StoreError):
    """Katalogda olmayan bir maça, `event` yükü olmadan yazma istendi."""

    default_message = "Unknown match"

    def __init__(self, message: Optional[str] = None, path: Optional[str] = None,
                 errno_code: Optional[int] = None, detail: str = "", *, event_id: Optional[int] = None):
        self.event_id = event_id
        if message is None and event_id is not None:
            message = f"{self.default_message}: {event_id}"
        super().__init__(message, path=path, errno_code=errno_code, detail=detail)


class PayloadMissing(StoreError):
    """Kaydı olan bir yük dosyası diskte yok."""

    default_message = "Payload file not found"


class PayloadCorrupt(StoreError):
    """Yük ya da manifest dosyası okunamıyor: yarım, bozuk sıkıştırma ya da geçersiz JSON."""

    default_message = "Payload file is corrupt"


class CatalogCorrupt(StoreError):
    """catalog.db açılamıyor ya da tutarlılık denetimini geçemiyor."""

    default_message = "The catalog is corrupt"


class SchemaTooNew(StoreError):
    """Veri dizini (düzen, manifest ya da state şeması) bu sürümün bildiğinden daha yeni."""

    default_message = "The data directory was written by a newer version"

    def __init__(self, message: Optional[str] = None, path: Optional[str] = None,
                 errno_code: Optional[int] = None, detail: str = "", *,
                 component: str = "", found: Optional[int] = None, supported: Optional[int] = None):
        self.component = component  # "layout" | "manifest" | "state" ...
        self.found = found
        self.supported = supported
        if message is None and found is not None:
            message = f"{self.default_message} ({component or 'schema'}: {found}, supported: {supported})"
            if path:
                message += f": {path}"
        super().__init__(message, path=path, errno_code=errno_code, detail=detail)


class LayoutError(StoreError):
    """v3 düzeninin kurallarına uymayan ad, yol ya da manifest (geçersiz dilim anahtarı, bilinmeyen sonek...)."""

    default_message = "Invalid store layout"


class FollowExists(StoreError):
    """Aynı kimlikle ya da aynı turnuva adıyla bir takip kaydı zaten var."""

    default_message = "The follow already exists"


class FollowManaged(StoreError):
    """Takip kaydı yapılandırma dosyasından geliyor; API üzerinden değiştirilemez."""

    default_message = "The follow is managed by the configuration file"


__all__ = [
    "StoreError",
    "LeaseHeld",
    "StoreBusy",
    "UnknownEvent",
    "PayloadMissing",
    "PayloadCorrupt",
    "CatalogCorrupt",
    "SchemaTooNew",
    "LayoutError",
    "FollowExists",
    "FollowManaged",
]
