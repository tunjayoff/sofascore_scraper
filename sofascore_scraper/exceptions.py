"""
SofaScore Scraper uygulaması için özel hata sınıfları.

İletiler İngilizcedir (FX-26, V2): API ve CLI hatalarında, işin `error.message`ında ve günlükte kullanıcıya
ulaşırlar; kullanıcıya gösterilen metin kodlarla ve yerel dosyalarla çevrilir.
"""

import errno
from typing import Optional


class SofaScoreScraperError(Exception):
    """Uygulama için temel hata sınıfı."""

    def __init__(self, message: str = "An error occurred in SofaScore Scraper"):
        self.message = message
        super().__init__(self.message)


class ConfigError(SofaScoreScraperError):
    """Yapılandırma hatası için özel sınıf."""

    def __init__(self, message: str = "Configuration error"):
        super().__init__(message)


class APIError(SofaScoreScraperError):
    """API isteklerinde oluşan hatalar için özel sınıf."""

    def __init__(self, message: str = "The API request failed", status_code: Optional[int] = None):
        self.status_code = status_code
        status_info = f" (status code {status_code})" if status_code else ""
        super().__init__(message + status_info)


class RateLimitError(APIError):
    """Rate limiting hatası için özel sınıf."""

    def __init__(self, wait_time: Optional[int] = None, status_code: int = 429, url: str = ""):
        self.wait_time = wait_time
        message = "Rate limited by the API"
        if url:
            message += f": {url}"
        if wait_time:
            message += f", waiting {wait_time} s"
        super().__init__(message, status_code)

class ResourceNotFoundError(APIError):
    """İstenen kaynak bulunamadığında (404) oluşan hata."""

    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, 404)


class DataNotFoundError(SofaScoreScraperError):
    """Veri bulunamadığında oluşan hatalar için özel sınıf."""

    def __init__(self, data_type: str = "Data", identifier: Optional[str] = None):
        message = f"{data_type} not found"
        if identifier:
            message += f": {identifier}"
        super().__init__(message)


class DataParsingError(SofaScoreScraperError):
    """Veri ayrıştırma hatası için özel sınıf."""

    def __init__(self, message: str = "The data could not be parsed"):
        super().__init__(message)


class NetworkError(SofaScoreScraperError):
    """Ağ hatası için özel sınıf."""

    def __init__(self, message: str = "A network error occurred"):
        super().__init__(message)


class ValidationError(SofaScoreScraperError):
    """Veri doğrulama hatası için özel sınıf."""

    def __init__(self, field: Optional[str] = None, message: str = "validation error"):
        if field:
            message = f"{field}: {message}"
        super().__init__(message)


class CircuitOpenError(SofaScoreScraperError):
    """Devre kesici açık (sofascore_scraper/breaker.py): istek SofaScore'a hiç gönderilmedi."""

    def __init__(self, url: str = ""):
        message = "Circuit breaker open, request not sent"
        if url:
            message += f": {url}"
        super().__init__(message)


# Bir sonraki yazmada da aynen tekrarlanacak depolama hataları: disk/kota dolu, izin yok, salt okunur
_FATAL_STORAGE_ERRNOS = frozenset(
    code
    for code in (
        errno.ENOSPC,
        getattr(errno, "EDQUOT", None),
        errno.EACCES,
        errno.EPERM,
        errno.EROFS,
    )
    if code is not None
)


class StorageError(SofaScoreScraperError):
    """
    Veri diske yazılamadı.

    fatal: hata bu maça özgü değil (disk dolu, izin yok, salt okunur dosya sistemi); sonraki her
    yazma da başarısız olur, bu yüzden iş durdurulmalıdır. fatal değilse yalnızca o maç başarısızdır.
    """

    def __init__(self, message: str = "Data could not be written to disk", path: Optional[str] = None,
                 errno_code: Optional[int] = None, detail: str = ""):
        self.path = path
        self.errno = errno_code
        self.detail = detail  # işletim sisteminin nedeni ("No space left on device")
        super().__init__(message)

    @property
    def fatal(self) -> bool:
        return self.errno in _FATAL_STORAGE_ERRNOS

    @classmethod
    def from_exception(cls, exc: BaseException, path: Optional[str] = None) -> "StorageError":
        """OSError (ya da serileştirme hatası) → StorageError; yol ve errno korunur."""
        code = exc.errno if isinstance(exc, OSError) else None
        where = path or (getattr(exc, "filename", None) if isinstance(exc, OSError) else None)
        detail = (exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)) or type(exc).__name__
        message = f"Data could not be written to disk ({detail})"
        if where:
            message += f": {where}"
        return cls(message, path=where, errno_code=code, detail=detail)
