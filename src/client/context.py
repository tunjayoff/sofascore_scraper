"""
İstek bağlamı: bir işin isteklerine eşlik eden iptal kontrolü, bekleme bildirimi ve devre kesici.

Üçü de ContextVar'dır: yalnızca o işin thread'ini ve onun asyncio.run / asyncio.to_thread çağrılarını
etkiler; aynı anda gelen diğer istekler (lig arama, tek maç çekme) etkilenmez. İptal kontrolü ile bekleme
bildirimi src/utils.py'den buraya taşındı (eski adlar orada duruyor); devre kesicinin ContextVar'ı
src/breaker.py'de kalır, buradan yalnızca kurulur.

`request_context` üçünü tek blokta kurar ve çıkışta geri alır (docs/design/02-services.md 2.4).
"""
from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

from src import breaker as request_breaker
from src.breaker import CircuitBreaker
from src.logger import get_logger

# Günlükçü adı taşınmadan önceki gibi: log satırlarındaki ad değişmesin
logger = get_logger("Utils")

CancelCheck = Callable[[], bool]
WaitNotifier = Callable[[str, float], None]


# ---- İptal (web arka plan işi) ----

class FetchCancelled(BaseException):
    """
    Çalışan iş iptal edildiğinde istek katmanında fırlatılır.
    asyncio.CancelledError gibi bilerek BaseException: fetcher'lardaki geniş
    `except Exception` blokları bunu yutup bir sonraki istekle devam etmesin.
    """


# Tasarımdaki adı (docs/design/02-services.md 2.4); aynı sınıf
Cancelled = FetchCancelled


# İş başına ayarlanır (`request_context`: iş yöneticisinin işleri). ContextVar olduğu için yalnızca o işin
# thread'ini ve onun asyncio.run / asyncio.to_thread çağrılarını etkiler; aynı anda
# gelen diğer web istekleri (lig arama, tek maç çekme) etkilenmez.
_cancel_check: "contextvars.ContextVar[Optional[CancelCheck]]" = contextvars.ContextVar(
    "fetch_cancel_check", default=None
)


def set_cancel_check(fn: Optional[CancelCheck]) -> "contextvars.Token":
    """Bu bağlamdaki istekler için iptal kontrolünü ayarlar."""
    return _cancel_check.set(fn)


def raise_if_cancelled() -> None:
    """İş iptal edildiyse FetchCancelled fırlatır."""
    fn = _cancel_check.get()
    if fn is not None and fn():
        raise FetchCancelled()


# Uzun beklemeleri (429/403 geri çekilmesi) işin ilerleme kartına bildirmek için; iptal
# kontrolü gibi iş başına ContextVar'dır.
_wait_notifier: "contextvars.ContextVar[Optional[WaitNotifier]]" = contextvars.ContextVar(
    "fetch_wait_notifier", default=None
)


def set_wait_notifier(fn: Optional[WaitNotifier]) -> "contextvars.Token":
    """Bu bağlamdaki uzun beklemeler için (reason, seconds) bildirimini ayarlar."""
    return _wait_notifier.set(fn)


def _notify_wait(reason: str, seconds: float) -> None:
    fn = _wait_notifier.get()
    if fn is None:
        return
    try:
        fn(reason, float(seconds))
    except Exception:  # bildirim isteği asla bozmamalı
        logger.debug("wait notifier failed", exc_info=True)


# ---- Üçü birden ----

@dataclass(frozen=True)
class RequestContext:
    """Bir `request_context` bloğunda geçerli olan değerler."""

    cancel: Optional[CancelCheck]
    on_wait: Optional[WaitNotifier]
    breaker: Optional[CircuitBreaker]


@contextlib.contextmanager
def request_context(
    *,
    cancel: Optional[CancelCheck] = None,
    on_wait: Optional[WaitNotifier] = None,
    breaker: Optional[CircuitBreaker] = None,
) -> Iterator[RequestContext]:
    """
    Blok içindeki istekler tam olarak verilen iptal kontrolü, bekleme bildirimi ve devre kesiciyle çalışır.

    None "yok" demektir, "dışarıdakini koru" değil: canlı izleme gibi bir işin içinden açılan ama o işin
    kesicisine sayılmaması gereken istekler `breaker=None` ile ayrılır. Çıkışta üçü de önceki değerine döner.
    """
    cancel_token = _cancel_check.set(cancel)
    wait_token = _wait_notifier.set(on_wait)
    breaker_token = request_breaker.activate(breaker)
    try:
        yield RequestContext(cancel=cancel, on_wait=on_wait, breaker=breaker)
    finally:
        request_breaker.deactivate(breaker_token)
        _wait_notifier.reset(wait_token)
        _cancel_check.reset(cancel_token)
