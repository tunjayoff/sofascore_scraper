"""
İstek devre kesicisi: SofaScore bizi engellediğinde (ya da sürekli hata verdiğinde) işi durdurur.

Sayaçlar önceden toplu detay indirmenin içindeydi ve bir maçın yalnızca ilk isteğini (/event)
görüyordu. Alt dilim istekleri, yenileme (refresh) yolu, CLI'ın --refresh-only döngüsü ve sezon /
maç programı aşamaları kesiciyi ne besliyor ne de ona bakıyordu: engellenmiş bir IP'den kalan her
maç için istek atılmaya (ve her biri yeniden deneme + geri çekilme yakmaya) devam ediliyordu.

Şimdi iş başına tek bir kesici var:
  - İstek katmanı (src/utils.py: make_api_request / make_api_request_async) her isteğin SON halini
    bildirir (yeniden denemeler bittikten sonra): başarı, 404 ya da başarısızlık türü.
  - Kesici açıkken istek katmanı yeni istek göndermez (CircuitOpenError); ortak istek bütçesinden
    (src/throttle.py) sıra da ayrılmaz. Aşamalar (sezonlar, maç programı, detaylar, yenileme)
    `tripped`'e bakıp döngülerini keser ve kullanıcıya nedenini söyler.
  - Kesici, işin bağlamında (ContextVar) durur: iptal kontrolü ve bekleme bildirimi gibi yalnızca
    o işin thread'ini, onun asyncio.run / asyncio.to_thread çağrılarını etkiler. Aynı anda gelen
    diğer web istekleri (lig arama, tek maç çekme) etkilenmez.

Eşikler ve .env anahtarları aynıdır (ConfigManager): RATE_LIMIT_THRESHOLD_CONSECUTIVE (20),
RATE_LIMIT_THRESHOLD_RATIO (0,9; 50 denemeden sonra), SERVER_ERROR_THRESHOLD_CONSECUTIVE (50).
Birim artık "maç denemesi" değil "istek"tir. IGNORE_RATE_LIMIT=true kesiciyi kapatır.

Tarayıcı köprüsünün sağlık durumu (src/bridge_health.py) kopyalanmaz, okunur: köprü "blocked"
diyorsa (art arda reddedilen challenge/403, en az bir yeniden çözüm denemesi dahil), bu iş
sırasında da reddedildiyse ve işin son istekleri 403 ile bittiyse, 20 isteği beklemeden devre kesilir.
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import os
import threading
import time
from collections import Counter
from typing import Any, Callable, Dict, Iterator, Optional

from src.exceptions import (
    APIError,
    CircuitOpenError,
    DataParsingError,
    NetworkError,
    ResourceNotFoundError,
)
from src.logger import get_logger

logger = get_logger("Breaker")

# İstek sonucu türleri
OK = "ok"
NOT_FOUND = "404"  # site yanıt verdi, kaynak yok: engelleme değil
FORBIDDEN = "403"
RATE_LIMITED = "429"
SERVER_ERROR = "5xx"
TIMEOUT = "timeout"
NETWORK = "network"
PARSE = "parse"
OTHER = "other"
BREAKER_OPEN = "breaker"  # istek gönderilmedi; kesiciye sayılmaz

DEFAULT_CONSECUTIVE = 20
DEFAULT_RATIO = 0.9
DEFAULT_SERVER_ERRORS = 50
# Oran kuralına ancak bu kadar istekten sonra bakılır (ilk birkaç isteğin oranı yanıltıcı)
RATIO_MIN_ATTEMPTS = 50

_REPORTED_ATTR = "_breaker_reported"


def failure_kind(exc: BaseException) -> str:
    """Bir istek hatasının türü: "404", "403", "429", "5xx", "timeout", "network", "parse", "breaker", "other"."""
    if isinstance(exc, CircuitOpenError):
        return BREAKER_OPEN
    if isinstance(exc, ResourceNotFoundError):
        return NOT_FOUND
    code = getattr(exc, "status_code", None)
    if code in (403, 404, 429):
        return str(code)
    if isinstance(code, int) and code >= 500:
        return SERVER_ERROR
    if isinstance(exc, DataParsingError):
        return PARSE
    text = str(exc).lower()
    if "timeout" in text or "timed out" in text:
        return TIMEOUT
    if isinstance(exc, NetworkError):
        return NETWORK
    return OTHER


def http_status(exc: BaseException) -> Optional[int]:
    """Hatanın HTTP durum kodu (varsa)."""
    code = getattr(exc, "status_code", None)
    return code if isinstance(exc, APIError) and isinstance(code, int) else None


def _number(getter: Callable[[], Any], default: float) -> float:
    try:
        value = getter()
    except Exception:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return value


def _ignore_rate_limit() -> bool:
    return os.getenv("IGNORE_RATE_LIMIT", "false").lower() == "true"


def _bridge_blocked_after(consecutive_failures: int, since: float) -> bool:
    """
    Köprü "blocked" diyor, `since`'ten (işin başlangıcı) beri en az bir kez reddedildi ve bu işin
    son istekleri de (köprünün "degraded" eşiği kadar) art arda başarısız.

    Köprü durumu yalnızca köprüden geçen isteklerle değişir, kendi kendine düzelmez: curl istekleri
    yeniden başarılı olduğunda köprü hiç çağrılmaz ve eski bir "blocked" öylece kalır. Bu yüzden
    durumun bu iş sırasında tazelenmiş olması ve kendi serimiz de aranır.
    """
    try:
        from src import bridge_health

        snap = bridge_health.snapshot()
        if snap["state"] != bridge_health.BLOCKED or not snap.get("last_failure_at"):
            return False
        # Görüntüdeki zaman saniyeye yuvarlanmış: karşılaştırma da saniye duyarlığında
        if dt.datetime.fromisoformat(snap["last_failure_at"]).timestamp() < int(since):
            return False
        return consecutive_failures >= int(snap["thresholds"]["degraded_after"])
    except Exception:
        return False


class CircuitBreaker:
    """Bir işin (web işi, headless çalıştırma, --refresh-only) tüm isteklerini sayar. Thread-safe."""

    def __init__(
        self,
        consecutive: "float | Callable[[], Any]" = DEFAULT_CONSECUTIVE,
        ratio: "float | Callable[[], Any]" = DEFAULT_RATIO,
        server_errors: "float | Callable[[], Any]" = DEFAULT_SERVER_ERRORS,
        *,
        bridge_blocked: Callable[[int, float], bool] = _bridge_blocked_after,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._consecutive_threshold = consecutive
        self._ratio_threshold = ratio
        self._server_error_threshold = server_errors
        self._bridge_blocked = bridge_blocked
        self.started_at = clock()
        self._lock = threading.Lock()
        self.attempts = 0
        self.failures = 0
        self.consecutive_failures = 0
        self.consecutive_server_errors = 0
        self._counts: Counter = Counter()
        self._tripped_by: Optional[str] = None

    @classmethod
    def from_config(cls, config_manager: Any) -> "CircuitBreaker":
        """Eşikler her başarısızlıkta ConfigManager'dan okunur (ayarlar iş sürerken değişebilir)."""
        return cls(
            consecutive=config_manager.get_rate_limit_threshold_consecutive,
            ratio=config_manager.get_rate_limit_threshold_ratio,
            server_errors=config_manager.get_server_error_threshold_consecutive,
        )

    @staticmethod
    def _value(threshold: "float | Callable[[], Any]", default: float) -> float:
        return _number(threshold, default) if callable(threshold) else threshold

    # -- olaylar --

    def record(self, kind: str) -> None:
        """Bir isteğin son hali. OK ve 404 seriyi bitirir; BREAKER_OPEN sayılmaz."""
        if kind == BREAKER_OPEN:
            return
        with self._lock:
            self.attempts += 1
            if kind in (OK, NOT_FOUND):
                self.consecutive_failures = 0
                self.consecutive_server_errors = 0
                if kind == NOT_FOUND:
                    self._counts[kind] += 1
                return
            self._counts[kind] += 1
            self.failures += 1
            self.consecutive_failures += 1
            self.consecutive_server_errors = self.consecutive_server_errors + 1 if kind == SERVER_ERROR else 0
            if self._tripped_by is not None or _ignore_rate_limit():
                return
            cause = self._trip_cause(kind)
            if cause is None:
                return
            self._tripped_by = cause
            summary = ", ".join(f"{n}x {k}" for k, n in self._counts.most_common())
            attempts, failures = self.attempts, self.failures
        logger.warning(
            f"Devre kesici açıldı ({cause}): {attempts} istekten {failures} başarısız ({summary}). "
            "Bu iş SofaScore'a yeni istek göndermeyecek."
        )

    def _trip_cause(self, kind: str) -> Optional[str]:
        """Kilit altında. Devreyi kesen kural ya da None."""
        if self.consecutive_failures >= self._value(self._consecutive_threshold, DEFAULT_CONSECUTIVE):
            return f"art arda {self.consecutive_failures} başarısız istek"
        if self.consecutive_server_errors >= self._value(self._server_error_threshold, DEFAULT_SERVER_ERRORS):
            return f"art arda {self.consecutive_server_errors} sunucu hatası"
        if self.attempts > RATIO_MIN_ATTEMPTS and (self.failures / self.attempts) >= self._value(
            self._ratio_threshold, DEFAULT_RATIO
        ):
            return f"başarısız istek oranı {self.failures}/{self.attempts}"
        if kind == FORBIDDEN and self._bridge_blocked(self.consecutive_failures, self.started_at):
            return "tarayıcı köprüsü 'blocked' ve istekler 403 ile reddediliyor"
        return None

    def record_exception(self, exc: BaseException) -> None:
        """
        Bir isteği bitiren hatayı sayar. Aynı hata nesnesi bir kez sayılır: istek katmanı bildirdiyse
        üst katmanların (ör. maç döngüsü) aynı hatayı yeniden bildirmesi sayacı değiştirmez.
        """
        if getattr(exc, _REPORTED_ATTR, False):
            return
        try:
            setattr(exc, _REPORTED_ATTR, True)
        except Exception:  # __slots__'lu hata: işaretlenemez, yine de sayılır
            pass
        self.record(failure_kind(exc))

    # -- okuma --

    @property
    def tripped(self) -> bool:
        return self._tripped_by is not None

    def counts(self) -> Dict[str, int]:
        """Tür başına sayım: başarısızlıklar ve 404'ler (başarılar hariç)."""
        with self._lock:
            return dict(self._counts)

    def reason(self) -> str:
        """Devreyi kesen hatanın türü: en sık görülen 403 / 429 / 5xx, yoksa "other"."""
        with self._lock:
            relevant = Counter(
                {k: v for k, v in self._counts.items() if k in (FORBIDDEN, RATE_LIMITED, SERVER_ERROR)}
            )
        return relevant.most_common(1)[0][0] if relevant else OTHER


# ---- bağlam: işin kesicisi --------------------------------------------------------------

_current: "contextvars.ContextVar[Optional[CircuitBreaker]]" = contextvars.ContextVar(
    "request_circuit_breaker", default=None
)


def current() -> Optional[CircuitBreaker]:
    """Bu bağlamdaki (işteki) kesici; yoksa None (tekil istekler sayılmaz)."""
    return _current.get()


def activate(breaker: Optional[CircuitBreaker]) -> "contextvars.Token":
    """Bu bağlamdaki isteklerin kesicisini ayarlar."""
    return _current.set(breaker)


def deactivate(token: "contextvars.Token") -> None:
    _current.reset(token)


@contextlib.contextmanager
def scope(config_manager: Any) -> Iterator[CircuitBreaker]:
    """
    İşin kesicisi: bağlamda zaten varsa (çağıran kurmuş) o kullanılır; yoksa bu blok için yenisi
    kurulur. Böylece iç içe çağrılar (iş → aşama → batch) aynı sayaçları paylaşır.
    """
    existing = current()
    if existing is not None:
        yield existing
        return
    breaker = CircuitBreaker.from_config(config_manager)
    token = activate(breaker)
    try:
        yield breaker
    finally:
        deactivate(token)


# ---- istek katmanının çağırdıkları ------------------------------------------------------

def check(url: str = "") -> None:
    """Kesici açıksa CircuitOpenError: istek gönderilmez."""
    breaker = current()
    if breaker is not None and breaker.tripped:
        raise CircuitOpenError(url)


def report_ok() -> None:
    _connection_answered()
    breaker = current()
    if breaker is not None:
        breaker.record(OK)


def report_exception(exc: BaseException) -> None:
    kind = failure_kind(exc)
    if kind == NOT_FOUND:
        _connection_answered()  # 404: SofaScore yanıt verdi
    elif kind != BREAKER_OPEN:  # devre açıkken istek gönderilmedi: bağlantı hakkında bir şey söylemez
        _connection_unanswered(kind, http_status(exc))
    breaker = current()
    if breaker is not None:
        breaker.record_exception(exc)


def _connection_answered() -> None:
    """Sürecin bağlantı durumu (src/bridge_health.py `ConnectionState`; FX-19): son istek yanıt aldı."""
    from src import bridge_health

    bridge_health.record_answer()


def _connection_unanswered(kind: str, status: Optional[int]) -> None:
    from src import bridge_health

    bridge_health.record_unanswered(kind, status)
