"""
Webhook sink'i ve sözleşmesi (docs/design/02-services.md bölüm 5.3).

İstek:

    POST <url>
    Content-Type: application/json
    X-Sofascore-Delivery: <UUID; aynı toplu gönderimin yeniden denemelerinde aynı kalır>
    X-Sofascore-Seq-First: <ilk sıra numarası>
    X-Sofascore-Seq-Last: <son sıra numarası>
    Idempotency-Key: <stream id>:<ilk>-<son>
    X-Sofascore-Signature: t=<unix saniye>,v1=<hex>      # v1 = HMAC-SHA256(secret, "<t>." + gövde)

    {"schema":"sofascore.webhook/1","sink":"ops","events":[zarf, ...]}

  * Toplu gönderim 1-100 olaydır (varsayılan 20) ve en geç 1 saniye sonra gönderilir.
  * Başarı her 2xx yanıtıdır. 410 sink'i süreç yeniden başlayana kadar kapatır. Diğer yanıtlar (yönlendirmeler
    dahil: izlenmez) ve ağ hataları artan aralıklarla yeniden denenir; `Retry-After` başlığı dikkate alınır.
  * Teslim ileri atlamaz: baştaki toplu gönderim sink'i bekletir. `max_age`'den (varsayılan 24 saat) eski
    olaylar bırakılır ve aralık `system.sink_dropped` olayıyla kaydedilir (dağıtıcı yapar).
  * Alıcı yinelenenleri `seq` ile ayıklar: bir çökmeden sonra aynı olaylar yeniden gelebilir.
  * İmza her denemede o anın zaman damgasıyla yeniden üretilir; alıcı `verify_signature` ile doğrulayabilir
    ve eski zaman damgalarını reddedebilir. İmzasız gönderim ancak `allow_unsigned = true` ile (karar D12).
  * Adres kullanıcı adı ve parola taşıyorsa (`https://kullanıcı:parola@host/...`) bunlar adresten çıkarılır ve
    `Authorization: Basic` başlığı olarak gönderilir.

Gizli değerler: imza anahtarı ve adres (yolu ya da sorgusu belirteç taşıyabilir) hiçbir log satırına, hata
metnine ya da akış olayına girmez. Hata metinleri yalnızca durum kodunu ve hata sınıfını taşır; yine de
üretilen her metinden adres ve anahtar ayıklanır.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import http.client
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple, Union

from src.sinks.base import (
    BaseSink,
    Clock,
    Envelope,
    EventFilter,
    FatalSinkError,
    RetryableSinkError,
    SystemClock,
    encode,
)
from src.version import __version__

WEBHOOK_SCHEMA = "sofascore.webhook/1"

HEADER_DELIVERY = "X-Sofascore-Delivery"
HEADER_SEQ_FIRST = "X-Sofascore-Seq-First"
HEADER_SEQ_LAST = "X-Sofascore-Seq-Last"
HEADER_IDEMPOTENCY = "Idempotency-Key"
HEADER_SIGNATURE = "X-Sofascore-Signature"

MIN_BATCH_SIZE = 1
MAX_BATCH_SIZE = 100
DEFAULT_WEBHOOK_BATCH = 20
DEFAULT_FLUSH_SECONDS = 1.0
DEFAULT_MAX_AGE_SECONDS = 24 * 3600.0
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_SIGNATURE_TOLERANCE = 300.0  # alıcı için önerilen: bundan eski zaman damgası reddedilir

_MASK = "***"
_MIN_PRIVATE_LENGTH = 4
_MAX_ERROR_LENGTH = 200


@dataclass(frozen=True)
class WebhookResponse:
    status: int
    retry_after: Optional[float] = None


# (adres, gövde, başlıklar, zaman aşımı) -> yanıt. Ağ hatası istisna olarak çıkar.
Transport = Callable[[str, bytes, Mapping[str, str], float], WebhookResponse]


# --- imza ----------------------------------------------------------------------------------------


def _key(secret: Union[str, bytes]) -> bytes:
    return secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)


def sign(secret: Union[str, bytes], timestamp: int, body: bytes) -> str:
    """`v1`: HMAC-SHA256(secret, "<t>." + gövde), onaltılık."""
    return hmac.new(_key(secret), str(int(timestamp)).encode("ascii") + b"." + body, hashlib.sha256).hexdigest()


def signature_header(secret: Union[str, bytes], timestamp: int, body: bytes) -> str:
    return f"t={int(timestamp)},v1={sign(secret, timestamp, body)}"


def verify_signature(secret: Union[str, bytes], header: Optional[str], body: bytes, *,
                     tolerance: Optional[float] = DEFAULT_SIGNATURE_TOLERANCE,
                     now: Optional[float] = None) -> bool:
    """
    Alıcı tarafı: `X-Sofascore-Signature` başlığı bu gövde için geçerli mi. `tolerance` saniyeden eski (ya da
    o kadar ilerideki) zaman damgası reddedilir; None: zaman denetlenmez. Karşılaştırma sabit zamanlıdır.
    """
    if not header:
        return False
    parts = dict(piece.strip().split("=", 1) for piece in header.split(",") if "=" in piece)
    try:
        timestamp = int(parts["t"])
        given = parts["v1"]
    except (KeyError, ValueError):
        return False
    if tolerance is not None:
        current = time.time() if now is None else float(now)
        if abs(current - timestamp) > tolerance:
            return False
    return hmac.compare_digest(sign(secret, timestamp, body), given)


# --- taşıma --------------------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Yönlendirme izlenmez: imzalı gövde, yapılandırmada yazmayan bir adrese gönderilmez."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _retry_after(headers: Any) -> Optional[float]:
    try:
        value = float(headers.get("Retry-After"))
    except (TypeError, ValueError, AttributeError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def urllib_transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> WebhookResponse:
    """Standart kütüphaneyle POST. HTTP hata yanıtları da yanıt olarak döner; ağ hatası istisnadır."""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            response.read(4096)
            return WebhookResponse(int(response.status), _retry_after(response.headers))
    except urllib.error.HTTPError as e:
        try:
            return WebhookResponse(int(e.code), _retry_after(e.headers))
        finally:
            with contextlib.suppress(Exception):
                e.close()


def _describe(exc: BaseException) -> str:
    """Ağ hatasının kısa adı: sınıf ve işletim sisteminin iletisi. İstisnanın tam metni kullanılmaz."""
    cause: Any = exc
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, BaseException):
        cause = exc.reason
    detail = getattr(cause, "strerror", None)
    if not detail and isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, str):
        detail = exc.reason
    name = type(cause).__name__
    return f"{name}: {detail}" if detail else name


# --- sink ----------------------------------------------------------------------------------------


class WebhookSink(BaseSink):
    """
    url              http:// ya da https:// adresi
    secret           imza anahtarı; None: imzasız (çağıran `allow_unsigned`'ı denetlemiş olmalı)
    batch_size       1-100
    linger_seconds   toplu gönderim dolmadıysa en çok bu kadar beklenir
    max_age_seconds  bundan eski, teslim edilemeyen olaylar bırakılır; None: hiçbir zaman
    timeout_seconds  bir isteğin zaman aşımı
    """

    def __init__(self, name: str, url: str, events: Optional[EventFilter] = None, *,
                 secret: Union[str, bytes, None] = None, batch_size: int = DEFAULT_WEBHOOK_BATCH,
                 linger_seconds: float = DEFAULT_FLUSH_SECONDS,
                 max_age_seconds: Optional[float] = DEFAULT_MAX_AGE_SECONDS,
                 timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, clock: Optional[Clock] = None,
                 transport: Optional[Transport] = None) -> None:
        super().__init__(name, events)
        parts = urllib.parse.urlsplit(url)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            raise ValueError("a webhook needs an http:// or https:// address")
        if not MIN_BATCH_SIZE <= int(batch_size) <= MAX_BATCH_SIZE:
            raise ValueError(f"batch_size must be between {MIN_BATCH_SIZE} and {MAX_BATCH_SIZE}")
        if secret is not None and not secret:
            raise ValueError("the webhook secret is empty")
        self._url = url
        self._authorization: Optional[str] = None
        if parts.username is not None:
            # urllib adresteki kullanıcı bilgisini tanımaz: adresten çıkarılır, Basic başlığı olur
            credentials = f"{urllib.parse.unquote(parts.username)}:{urllib.parse.unquote(parts.password or '')}"
            self._authorization = "Basic " + base64.b64encode(credentials.encode("utf-8")).decode("ascii")
            self._url = urllib.parse.urlunsplit(parts._replace(netloc=parts.netloc.rpartition("@")[2]))
        self._secret: Optional[bytes] = _key(secret) if secret is not None else None
        self.host = parts.hostname  # loglarda görünen tek adres parçası
        self.batch_size = int(batch_size)
        self.linger_seconds = max(0.0, float(linger_seconds))
        self.max_age_seconds = float(max_age_seconds) if max_age_seconds is not None else None
        self.timeout_seconds = float(timeout_seconds)
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._transport: Transport = transport if transport is not None else urllib_transport
        self._attempt: Optional[Tuple[int, int, str]] = None  # (ilk, son, teslim kimliği): yeniden denemeler için
        # Hata metinlerinden ayıklanacak parçalar: adresin tamamı, yolu ve sorgusu, imza anahtarı
        pieces: List[str] = [url, self._url, parts.query, parts.path if len(parts.path) > 1 else ""]
        if self._authorization is not None:
            pieces.append(self._authorization.split(" ", 1)[1])
        if parts.username:
            pieces.append(parts.username)
        if parts.password:
            pieces.append(parts.password)
        if self._secret is not None:
            pieces.append(self._secret.decode("utf-8", "replace"))
        # Çok kısa parçalar aranmaz: iki harflik bir yol her hata metnindeki aynı iki harfi maskelerdi
        self._private = tuple(sorted({piece for piece in pieces if len(piece) >= _MIN_PRIVATE_LENGTH},
                                     key=len, reverse=True))

    @property
    def signed(self) -> bool:
        return self._secret is not None

    def _safe(self, text: str) -> str:
        for piece in self._private:
            text = text.replace(piece, _MASK)
        return text[:_MAX_ERROR_LENGTH]

    def request(self, batch: Sequence[Envelope]) -> Tuple[bytes, Mapping[str, str]]:
        """Bir toplu gönderimin gövdesi ve başlıkları (imza o anın zaman damgasıyla)."""
        first, last = batch[0].seq, batch[-1].seq
        if self._attempt is None or self._attempt[:2] != (first, last):
            self._attempt = (first, last, str(uuid.uuid4()))
        body = encode({
            "schema": WEBHOOK_SCHEMA,
            "sink": self.name,
            "events": [env.to_dict() for env in batch],
        }).encode("ascii")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"sofascore-scraper/{__version__}",
            HEADER_DELIVERY: self._attempt[2],
            HEADER_SEQ_FIRST: str(first),
            HEADER_SEQ_LAST: str(last),
            HEADER_IDEMPOTENCY: f"{batch[0].stream_id}:{first}-{last}",
        }
        if self._authorization is not None:
            headers["Authorization"] = self._authorization
        if self._secret is not None:
            headers[HEADER_SIGNATURE] = signature_header(self._secret, int(self._clock.time()), body)
        return body, headers

    def deliver(self, batch: Sequence[Envelope]) -> None:
        if not batch:
            return
        body, headers = self.request(batch)
        try:
            response = self._transport(self._url, body, headers, self.timeout_seconds)
        except (OSError, http.client.HTTPException) as e:
            raise RetryableSinkError(self._safe(f"network error ({_describe(e)})")) from None
        status = int(response.status)
        if 200 <= status < 300:
            self._attempt = None
            return
        if status == 410:
            raise FatalSinkError("the receiver answered 410 Gone")
        raise RetryableSinkError(f"the receiver answered HTTP {status}", retry_after=response.retry_after)

    def __repr__(self) -> str:
        return f"<WebhookSink {self.name!r} host={self.host!r} signed={self.signed}>"


__all__ = [
    "DEFAULT_FLUSH_SECONDS",
    "DEFAULT_MAX_AGE_SECONDS",
    "DEFAULT_SIGNATURE_TOLERANCE",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_WEBHOOK_BATCH",
    "HEADER_DELIVERY",
    "HEADER_IDEMPOTENCY",
    "HEADER_SEQ_FIRST",
    "HEADER_SEQ_LAST",
    "HEADER_SIGNATURE",
    "MAX_BATCH_SIZE",
    "MIN_BATCH_SIZE",
    "WEBHOOK_SCHEMA",
    "Transport",
    "WebhookResponse",
    "WebhookSink",
    "sign",
    "signature_header",
    "verify_signature",
]
