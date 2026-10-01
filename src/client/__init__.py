"""
SofaScore istemcisi: SofaScore'a istek atan tek katman (docs/design/02-services.md 2.4).

  Client           get / get_sync: API yolu → tipli sonuç (src.slices.Outcome). SofaScore'dan kaynaklanan hiçbir
                   durumda fırlatmaz; yalnızca iptal (Cancelled) ve programlama hataları dışarı çıkar.
  request_context  iptal kontrolü, bekleme bildirimi ve devre kesiciyi bir blok için kurar
  endpoints        her URL şablonu (yollar API köküne göredir)
  transport        istek gövdesi: yeniden deneme, geri çekilme, ortak istek bütçesi, tarayıcı köprüsüne düşüş.
                   Eski giriş noktaları (make_api_request, make_api_request_async) da oradadır.
  context          iptal kontrolü ve bekleme bildirimi (ContextVar)

Client bugünkü istek katmanının üzerinde bir yüzdür: aynı gövdeyi çağırır, aynı devre kesiciye bildirir, aynı
ortak bütçeden sıra alır. Farkı sonucun biçimidir (hata fırlatmak ya da None yerine Outcome) ve API kökünün
tek yerde eklenmesidir. DATA_DIR altına hiçbir şey yazmaz: köprü sağlığındaki her geçiş `on_health_change` ile
çağırana bildirilir, saklamak çağıranın işidir.

Bu paket src.store'u içe aktarmaz.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import threading
import weakref
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

from src import bridge_health
from src.bridge_health import BridgeHealthSnapshot, HealthChangeCallback
from src.breaker import BREAKER_OPEN, OTHER
from src.client import context, endpoints, transport
from src.client.context import Cancelled, FetchCancelled, RequestContext, request_context
from src.client.transport import RequestTrace, api_url, base_url
from src.exceptions import CircuitOpenError, SofaScoreScraperError
from src.slices import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, SLICE_SKIPPED, Outcome

__all__ = [
    "Cancelled",
    "Client",
    "ClientSettings",
    "FetchCancelled",
    "HealthSource",
    "RequestContext",
    "api_url",
    "base_url",
    "context",
    "endpoints",
    "request_context",
    "transport",
]

# İçinde veri olmayan 200 yanıtının nedeni (404'ünki "404"tür)
EMPTY_BODY = "empty"


@dataclass(frozen=True)
class ClientSettings:
    """
    İstemcinin ayarları.

    base_url         API kökü; her yola yalnızca Client.url'de eklenir
    retries          istek başına deneme sayısı; None: MAX_RETRIES ayarı (her istekte okunur)
    timeout_seconds  istek zaman aşımı; None: REQUEST_TIMEOUT ayarı (her istekte okunur)

    Proxy, istek sonrası bekleme, eşzamanlı istek sınırı ve ortak istek bütçesi henüz buradan geçmez: istek
    katmanı onları bugünkü gibi ConfigManager'dan ve src/throttle.py'den okur.
    """

    base_url: str = endpoints.DEFAULT_BASE_URL
    retries: Optional[int] = None
    timeout_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        normalized = self.base_url.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError(f"base_url must be an absolute http(s) URL: {self.base_url!r}")
        object.__setattr__(self, "base_url", normalized)

    @classmethod
    def from_environment(cls) -> "ClientSettings":
        """Sürecin geçerli ayarları: API kökü `API_BASE_URL`'den (src.client.transport.base_url)."""
        return cls(base_url=base_url())


class HealthSource(Protocol):
    """Köprü sağlığının okunduğu yer: bir BridgeHealth ya da (sürecin tek köprüsü için) src.bridge_health modülü."""

    def snapshot(self) -> BridgeHealthSnapshot: ...

    def add_on_health_change(self, fn: HealthChangeCallback) -> None: ...

    def remove_on_health_change(self, fn: HealthChangeCallback) -> None: ...


class _LoopSession:
    """Bir asyncio döngüsünün oturumu (curl_cffi oturumu açıldığı döngüye bağlıdır)."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.opener: Optional[transport.WarmableAsyncSession] = None
        self.session: Any = None


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Client:
    """
    SofaScore API istemcisi.

        client = Client(on_health_change=save_snapshot)
        with request_context(cancel=job.cancelled, on_wait=job.waiting, breaker=job.breaker):
            outcome = await client.get(endpoints.event(123))

    Sonuçlar (Outcome.status / reason):
      ok       yanıt geldi ve içinde veri var
      empty    kesin "yok": HTTP 404 ("404") ya da içi boş bir 200 gövdesi ("empty")
      failed   istek başarısız: "403" | "429" | "5xx" | "timeout" | "network" | "parse" | "other"
      skipped  istek gönderilmedi: işin devre kesicisi açık ("breaker")
    """

    def __init__(
        self,
        settings: Optional[ClientSettings] = None,
        *,
        health: Optional[HealthSource] = None,
        on_health_change: Optional[HealthChangeCallback] = None,
        clock: Callable[[], dt.datetime] = _utc_now,
    ) -> None:
        self._settings = settings if settings is not None else ClientSettings.from_environment()
        self._health: HealthSource = health if health is not None else bridge_health
        self._on_health_change = on_health_change
        self._clock = clock
        self._sessions: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopSession]" = (
            weakref.WeakKeyDictionary()
        )
        self._sessions_lock = threading.Lock()
        if on_health_change is not None:
            self._health.add_on_health_change(on_health_change)

    @property
    def settings(self) -> ClientSettings:
        return self._settings

    # --- adres ---------------------------------------------------------------------------------------

    def url(self, path: str) -> str:
        """API köküne göre yol → tam adres. API kökü yalnızca burada eklenir; yollar src.client.endpoints'ten gelir."""
        if not path.startswith("/"):
            raise ValueError(f"path must be relative to the API root and start with '/': {path!r}")
        return self._settings.base_url + path

    # --- istekler ------------------------------------------------------------------------------------

    async def get(self, path: str, *, retries: Optional[int] = None, timeout: Optional[float] = None) -> Outcome:
        """
        Asenkron GET. Oturum, çağrının yapıldığı döngüde ilk istekte açılır (ısınma isteğiyle) ve o döngüde
        yeniden kullanılır; döngü bitmeden `aclose()` ile (ya da `async with client:`) kapatılır.
        """
        url = self.url(path)
        trace = RequestTrace()
        try:
            # Açık devrede ya da iptal edilmiş işte oturum da açılmaz: ısınma isteği de bir istektir
            context.raise_if_cancelled()
            transport.breaker.check(url)
            session = await self._session()
            data = await transport.make_api_request_async(
                session, url, self._retries(retries), timeout=self._timeout(timeout), trace=trace
            )
        except SofaScoreScraperError as exc:
            return self._error_outcome(exc, trace)
        return self._answer_outcome(data, trace)

    def get_sync(self, path: str, *, retries: Optional[int] = None, timeout: Optional[float] = None) -> Outcome:
        """Senkron GET (tekil çağıranlar: doctor, durum, lig arama). Oturum açmaz; her istek kendi bağlantısıdır."""
        url = self.url(path)
        trace = RequestTrace()
        try:
            data = transport.make_api_request(
                url, self._retries(retries), self._timeout(timeout), raise_on_failure=True, trace=trace
            )
        except SofaScoreScraperError as exc:
            return self._error_outcome(exc, trace)
        return self._answer_outcome(data, trace)

    def _retries(self, retries: Optional[int]) -> Optional[int]:
        return retries if retries is not None else self._settings.retries

    def _timeout(self, timeout: Optional[float]) -> Optional[float]:
        return timeout if timeout is not None else self._settings.timeout_seconds

    def _error_outcome(self, exc: SofaScoreScraperError, trace: RequestTrace) -> Outcome:
        """İsteği bitiren tipli hata → sonuç. İstek katmanı hatayı devre kesiciye zaten bildirdi."""
        now = self._clock()
        if isinstance(exc, CircuitOpenError):
            return Outcome(SLICE_SKIPPED, reason=BREAKER_OPEN, fetched_at=now)
        return dataclasses.replace(Outcome.from_error(exc), fetched_at=now, via=trace.via)

    def _answer_outcome(self, data: Any, trace: RequestTrace) -> Outcome:
        """Hata fırlatmadan dönen istek → sonuç."""
        now, via = self._clock(), trace.via
        if data is None:
            # İstek katmanı hata fırlatmadan veri de döndürmedi (gövdesi `null` olan yanıt): sessizce "yok"
            # sayılmaz, başarısızlıktır
            return Outcome(SLICE_FAILED, reason=OTHER, http_status=trace.http_status, fetched_at=now, via=via)
        if not data:
            return Outcome(
                SLICE_EMPTY, data=data, reason=EMPTY_BODY, http_status=trace.http_status, fetched_at=now, via=via
            )
        return Outcome(SLICE_OK, data=data, http_status=trace.http_status, fetched_at=now, via=via)

    # --- oturum --------------------------------------------------------------------------------------

    async def _session(self) -> Any:
        loop = asyncio.get_running_loop()
        with self._sessions_lock:
            state = self._sessions.get(loop)
            if state is None:
                state = self._sessions[loop] = _LoopSession()
        async with state.lock:
            if state.session is None:
                opener = transport.create_session_async()
                state.session = await opener.__aenter__()
                state.opener = opener
        return state.session

    async def aclose(self) -> None:
        """
        Çağrının yapıldığı döngüde açılan oturumu kapatır. İstemci kullanılabilir kalır: sonraki `get` yeni bir
        oturum açar. Her asyncio döngüsü (her iş) kendi oturumunu kendi bitmeden kapatmalıdır.
        """
        loop = asyncio.get_running_loop()
        with self._sessions_lock:
            state = self._sessions.pop(loop, None)
        if state is None:
            return
        async with state.lock:
            opener, state.opener, state.session = state.opener, None, None
        if opener is not None:
            await opener.__aexit__(None, None, None)

    async def __aenter__(self) -> "Client":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    # --- köprü sağlığı -------------------------------------------------------------------------------

    def health(self) -> BridgeHealthSnapshot:
        """Tarayıcı köprüsünün son bilinen durumu (bu süreçte)."""
        return self._health.snapshot()

    def close(self) -> None:
        """İstemci bırakılırken: `on_health_change` geri çağrısını sağlık kaynağından ayırır."""
        if self._on_health_change is not None:
            self._health.remove_on_health_change(self._on_health_change)
            self._on_health_change = None

