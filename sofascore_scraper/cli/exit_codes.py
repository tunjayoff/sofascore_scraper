"""
CLI çıkış kodları (docs/design/02-services.md bölüm 4.5). Kodlar sözleşmenin parçasıdır.

    0        başarı; SIGTERM/SIGINT ile durdurulan bir servis de 0 ile çıkar
    1        genel hata; başarısız denetimi olan `doctor`
    2        kullanım ya da yapılandırma hatası
    3        kısmi başarı: iş bitti, bazı öğeler başarısız
    4        SofaScore engelliyor ya da hata vermeye devam ediyor; devre kesici işi durdurdu
    5        depolama hatası
    6        kilidi başka bir kopya tutuyor
    130/143  tek seferlik komut SIGINT / SIGTERM ile iptal edildi

Bir hata kodunun çıkış kodu hata tablosundan (sofascore_scraper/errors.py) okunur; burada ikinci bir eşleme yoktur.
Birden çok durum geçerliyse öncelik: 5 > 4 > 6 > 3 > 1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Tuple, Union

from sofascore_scraper.errors import ERROR_TABLE, EXIT_SIGINT, EXIT_SIGTERM, Cancelled, PlatformError, error_spec

OK = 0
GENERAL_ERROR = 1
USAGE_ERROR = 2
PARTIAL = 3
UPSTREAM = 4
STORAGE = 5
INSTANCE_RUNNING = 6
CANCELLED_SIGINT = EXIT_SIGINT    # 130
CANCELLED_SIGTERM = EXIT_SIGTERM  # 143

# Birden çok durum geçerliyse hangisi bildirilir (bölüm 4.5)
PRECEDENCE: Tuple[int, ...] = (STORAGE, UPSTREAM, INSTANCE_RUNNING, PARTIAL, GENERAL_ERROR)


@dataclass(frozen=True)
class ExitCodeSpec:
    code: int
    name: str
    meaning: str

    @property
    def error_codes(self) -> Tuple[str, ...]:
        """Bu çıkış koduna düşen hata kodları (hata tablosundan)."""
        return tuple(
            spec.code for spec in ERROR_TABLE
            if self.code in (spec.exit_codes or ((spec.exit_code,) if spec.exit_code is not None else ()))
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "name": self.name, "meaning": self.meaning, "error_codes": list(self.error_codes)}


EXIT_CODES: Tuple[ExitCodeSpec, ...] = (
    ExitCodeSpec(OK, "ok", "success; also a service stopped by SIGTERM or SIGINT"),
    ExitCodeSpec(GENERAL_ERROR, "error", "general error; doctor with a failed check; status --check on an unhealthy store"),
    ExitCodeSpec(USAGE_ERROR, "usage", "usage or configuration error"),
    ExitCodeSpec(PARTIAL, "partial", "partial success: finished, some items failed"),
    ExitCodeSpec(UPSTREAM, "upstream", "SofaScore blocks or keeps failing; the breaker stopped the job"),
    ExitCodeSpec(STORAGE, "storage", "storage error"),
    ExitCodeSpec(INSTANCE_RUNNING, "instance_running", "another instance holds the lease"),
    ExitCodeSpec(CANCELLED_SIGINT, "cancelled_sigint", "one-shot command cancelled by SIGINT"),
    ExitCodeSpec(CANCELLED_SIGTERM, "cancelled_sigterm", "one-shot command cancelled by SIGTERM"),
)


def exit_code_for(error: Union[PlatformError, str]) -> int:
    """Bir hatanın (ya da hata kodunun) çıkış kodu. CLI'de karşılığı olmayan ve bilinmeyen kodlar 1'dir."""
    if isinstance(error, Cancelled):
        return error.exit_code
    code = error.code if isinstance(error, PlatformError) else str(error)
    found = error_spec(code).exit_code
    return GENERAL_ERROR if found is None else found


def combine(codes: Iterable[int]) -> int:
    """
    Birden çok çıkış kodundan bildirilecek olan: öncelik sırasındaki (5 > 4 > 6 > 3 > 1) ilk kod. Sırada
    olmayan sıfırdan farklı kodlar (2, 130, 143) yalnızca sıradakilerden hiçbiri yoksa, verildikleri sırayla
    sayılır; hepsi 0 ise 0.
    """
    seen = [int(code) for code in codes]
    for code in PRECEDENCE:
        if code in seen:
            return code
    return next((code for code in seen if code != OK), OK)


__all__ = [
    "CANCELLED_SIGINT",
    "CANCELLED_SIGTERM",
    "EXIT_CODES",
    "GENERAL_ERROR",
    "INSTANCE_RUNNING",
    "OK",
    "PARTIAL",
    "PRECEDENCE",
    "STORAGE",
    "UPSTREAM",
    "USAGE_ERROR",
    "ExitCodeSpec",
    "combine",
    "exit_code_for",
]
