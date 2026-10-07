"""
Push kaynağı `direct` (docs/design/02-services.md bölüm 8.2 ve 8.3; plan maddesi P31): hafif bir istemci push
sunucusuna (NATS over WebSocket) kendisi bağlanır ve izlenen her spor için `sport.{spor}` konusuna abone olur.
Bir spor sayfası açık tutmaz: yaklaşık 0,2 GB bellek, sayfanın 1,8–2,6 GB'ı yerine.

    köprü sayfası (bir kez) ─ CONNECT karesi ─→ kimlik bilgisi (yalnızca bellekte)
    DirectConnection (tek bağlantı, tek thread) ─ MSG ─→ spor başına PushFeed ─→ servis: LastKnown, hakem,
        indirgeyici (`page` kaynağıyla aynı yol; push_source.py)

**Açık seçim.** Bu kaynak yalnızca yapılandırma kelimesi kelimesine `direct` dediğinde kullanılır
(`--source direct`, `[live] source = "direct"`, `SOFASCORE_LIVE__SOURCE=direct`). Varsayılan `page`'dir; hiçbir
kod yolu (yedek, "auto" değeri, hata işleyici) bu kaynağı kendiliğinden seçmez: servis bağlantıyı yalnızca
istenen kaynak `direct` ise kurar (supervisor.LiveService). Yedek her zaman ve yalnızca yoklamadır.

Kullanıcıya her yerde söylenen dört uyarı (settings.LIVE_DIRECT_WARNING): sitenin kendi istemci kimlik bilgisini
sitenin istemcisi dışında kullanır; kimlik bilgisi ya da sunucu değişirse haber vermeden bozulabilir; IP
adresinin engellenmesine yol açabilir; kullanım şartları açısından gri alandır ve kullanıcı bilerek seçer.

Kurallar (sahip kararları, 2026-10-01):

  * **Kimlik bilgisi yalnızca bellektedir.** Köprünün (canlı profil, `<profil>-live`) açtığı spor sayfasının
    KENDİ bağlantısının `CONNECT` karesinden okunur. Diske, state.db'ye, log satırına, akış olayına, tanılama
    paketine yazılmaz; `sofascore_scraper/redact.py`'ye (add_runtime_secret) verilir ki bir yerde görünürse maskelensin. Sunucu
    reddederse (yetki hatası, el sıkışmada 401/403) yeni bir sayfadan yeniden okunur; art arda başarısızlıklardan
    sonra kaynak sağlıksız sayılır, servis yoklamayla sürer ve deneme seyrek aralıklarla devam eder.
  * **Hatta asgari davranış.** Tek bağlantı; yalnızca abone olur (SUB/UNSUB), hiç yayın (PUB) yapmaz; joker konu
    (`*`, `>`) yoktur; sitenin istemcisi gibi 120 sn'de bir PING atar ve sunucunun PING'ine PONG ile yanıt verir.
    Bağlantı yaklaşık 30 dakikada bir düşer: artan aralıklarla yeniden bağlanır, yeniden abone olur; her açılış
    hakeme bir yoklama turu yaptırır.
  * **Sunucu bilgisi yazılmaz.** Sunucunun adresi sayfanın bağlantısından okunur, kodda yoktur ve log'a
    yazılmaz; `INFO` gövdesi (istemci IP'si, sunucu bilgileri) okunmaz; `-ERR` satırından yalnızca bilinen NATS
    hata metni alınır (push_source.NatsReader).
  * **Proxy.** Yapılandırılmış bir proxy varken bu istemci doğrudan bağlanmaz (proxy'yi atlayıp gerçek IP
    adresini göstermemek için): kaynak kullanılamaz sayılır ve servis yoklamayla sürer.

Ölçülenler ve ölçülmeyenler (docs/push-channel/README.md bölüm 7): tek akşam, tek bölge, tek bağlantı, tek konu
`sport.football`, yaklaşık 38 dakika, bir yeniden bağlanma; TLS parmak izi taklidi gerekmedi. Birden çok konu,
saatlerce süren çalışma, birden çok spor ve kimlik bilgisinin ne sıklıkta değiştiği ölçülmedi.

Testler gerçek sunucuya ve tarayıcıya bağlanmaz: süreç içi sahte bir NATS sunucusu ve kayıtlı, kimlik bilgisi
içermeyen kareler kullanılır (tests/test_live_direct_source.py, tests/test_live_direct_optin.py).
"""
from __future__ import annotations

import base64
import collections
import hashlib
import json
import logging
import os
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Protocol, Tuple
from urllib.parse import urlsplit

from sofascore_scraper.services.live.arbiter import PING_INTERVAL_SECONDS
from sofascore_scraper.services.live.push_source import (
    NatsReader,
    PushFeed,
    Signal,
    quiet_url,
    route_action,
    sport_page_url,
)

logger = logging.getLogger(__name__)

SOURCE_DIRECT = "direct"
SUBJECT_PREFIX = "sport."

CONNECT_TIMEOUT_SECONDS = 20.0
RECV_POLL_SECONDS = 0.5  # bağlantı thread'inin durdurma ve PING zamanına bakma aralığı
STALE_FACTOR = 2.5  # bu kadar PING aralığı hiçbir şey gelmezse bağlantı ölü sayılır
AUTH_WAIT_SECONDS = 30.0  # CONNECT + PING'den sonra PONG (kabul) bu kadar beklenir
STABLE_SESSION_SECONDS = 60.0  # bundan uzun süren oturum ardışık başarısızlık sayacını sıfırlar
RECONNECT_FIRST_SECONDS = 2.0
RECONNECT_MAX_SECONDS = 300.0
READ_RETRY_FIRST_SECONDS = 60.0  # kimlik bilgisinin yeniden okunması tarayıcı açar: daha seyrek
READ_RETRY_MAX_SECONDS = 1800.0
UNHEALTHY_AFTER = 5  # art arda bu kadar başarısız denemeden sonra kaynak sağlıksız sayılır
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
MAX_HANDSHAKE_BYTES = 16 * 1024
READ_SECONDS = 240.0  # kimlik bilgisinin okunması: tarayıcı açılışı (150 sn) + sayfanın bağlanması
CAPTURE_SECONDS = 60.0  # sayfa yüklendikten sonra kendi bağlantısının CONNECT karesi bu kadar beklenir

# Sunucu bu hatalarla kimlik bilgisini reddeder: yeni bir sayfadan yeniden okunur
AUTH_ERRORS = (
    "Authorization Violation", "Authorization Timeout", "User Authentication Expired",
    "Account Authentication Expired", "User Authentication Revoked",
)
# CONNECT seçeneklerinden gizli sayılmayanlar (istemcinin adı, dili, sürümü); geri kalan her metin değer
# kimlik bilgisi sayılır ve maskelenir
NON_SECRET_OPTIONS = frozenset({"lang", "version", "name", "protocol"})

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# --- kimlik bilgisi --------------------------------------------------------------------------------


class CredentialUnavailable(Exception):
    """Kimlik bilgisi okunamadı (tarayıcı açılmadı, sayfa bağlanmadı, CONNECT görülmedi)."""


@dataclass(frozen=True)
class Credential:
    """
    Sayfanın kendi bağlantısından okunan bağlantı bilgisi; yalnızca bellekte. `repr` hiçbir alanı göstermez:
    yanlışlıkla log'a yazılan bir nesne de bir şey sızdırmaz.

    url      sayfanın push bağlantısının adresi (sunucu bilgisi: yazılmaz)
    options  sayfanın gönderdiği CONNECT seçenekleri, olduğu gibi (kimlik bilgisi içinde)
    origin   bağlantıyı açan sayfanın kökeni (`https://host`); el sıkışmada Origin başlığı
    """

    url: str = field(repr=False)
    options: Mapping[str, Any] = field(repr=False)
    origin: str = field(repr=False, default="")

    def __repr__(self) -> str:
        return "Credential(***)"

    __str__ = __repr__


def parse_connect(payload: Any) -> Optional[Dict[str, Any]]:
    """Giden bir karedeki `CONNECT {...}` satırının seçenekleri; yoksa ya da JSON nesnesi değilse None."""
    if isinstance(payload, (bytes, bytearray, memoryview)):
        text = bytes(payload).decode("utf-8", errors="replace")
    elif isinstance(payload, str):
        text = payload
    else:
        return None
    for line in text.split("\r\n"):
        if line[:8].upper() == "CONNECT ":
            try:
                options = json.loads(line[8:])
            except ValueError:
                return None
            return options if isinstance(options, dict) else None
    return None


def _secret_strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [s for v in value.values() for s in _secret_strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _secret_strings(v)]
    return []


def remember(credential: Credential) -> None:
    """Kimlik bilgisinin metin değerlerini ve adresi sofascore_scraper.redact'e verir: görünürlerse her yerde `***` olur."""
    from sofascore_scraper import redact

    for key, value in credential.options.items():
        if key not in NON_SECRET_OPTIONS:
            for text in _secret_strings(value):
                redact.add_runtime_secret(text)
    redact.add_runtime_secret(credential.url)


class CredentialReader(Protocol):
    """Kimlik bilgisini okuyan taraf: gerçek tarayıcı (BrowserCredentialReader) ya da testlerin sahtesi."""

    def read(self, sport: str) -> Credential:
        """Bu sporun sayfasını açar, kendi bağlantısının CONNECT karesini okur; olmazsa CredentialUnavailable."""

    def close(self) -> None:
        """Tarayıcıyı kapatır."""


class BrowserCredentialReader:
    """
    Kimlik bilgisini gerçek tarayıcıdan okur: canlı profille ayrı bir köprü örneği (sofascore_scraper/client/bridge.py,
    `BrowserBridge`, profil `<profil>-live`, karar D10), sporun sayfası, sayfanın açtığı NATS bağlantısının
    (ilk gelen karesi `INFO`) giden `CONNECT` karesi. Okuma bitince sayfa ve tarayıcı kapanır: tarayıcı yalnızca
    okuma sürerken açıktır. Sayfanın istekleri `page` kaynağındaki gibi kesilir ya da bütçeden sıra alır.
    """

    def __init__(self, profile_dir: Optional[str] = None) -> None:
        from sofascore_scraper.services.live.push_source import live_profile_dir

        self.profile_dir = profile_dir or live_profile_dir()
        self._bridge: Any = None
        self._loaded_at = 0.0
        self.reads = 0

    def _get_bridge(self) -> Any:
        from sofascore_scraper.client import bridge

        if self._bridge is None:
            # Sağlık sinyali yazılmaz: kimlik bilgisini okuyan tarayıcı indirmelerin köprüsünün sağlığını belirlemez
            self._bridge = bridge.BrowserBridge(profile_dir=self.profile_dir, home_url=quiet_url(), report_health=False)
        return self._bridge

    def read(self, sport: str) -> Credential:
        from sofascore_scraper.client import bridge

        self.reads += 1
        try:
            return bridge._run_sync(self._read(sport), READ_SECONDS)
        except CredentialUnavailable:
            raise
        except Exception as e:
            raise CredentialUnavailable(type(e).__name__) from None
        finally:
            self.close()

    async def _read(self, sport: str) -> Credential:
        import asyncio

        b = self._get_bridge()
        await b.ensure_ready()
        await b.context.route("**/*", self._route)
        page = await b.context.new_page()
        found: Dict[str, Any] = {}
        done = asyncio.Event()

        def on_websocket(ws: Any) -> None:
            state = {"nats": None}

            def received(payload: Any) -> None:
                if state["nats"] is None:
                    head = bytes(payload[:5]) if isinstance(payload, (bytes, bytearray)) else str(payload)[:5].encode()
                    state["nats"] = head.upper() == b"INFO "

            def sent(payload: Any) -> None:
                if state["nats"] and "options" not in found:
                    options = parse_connect(payload)
                    if options is not None:
                        found["options"], found["url"] = options, str(getattr(ws, "url", "") or "")
                        done.set()

            ws.on("framereceived", received)
            ws.on("framesent", sent)

        page.on("websocket", on_websocket)
        url = sport_page_url(sport)
        try:
            self._loaded_at = time.time()
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            if "captcha.html" in (page.url or ""):
                await b.solve_challenge()
                self._loaded_at = time.time()
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                await asyncio.wait_for(done.wait(), CAPTURE_SECONDS)
            except asyncio.TimeoutError:
                raise CredentialUnavailable("the page opened no push connection") from None
        finally:
            try:
                await page.close()
            except Exception as e:
                logger.debug("The credential page could not be closed (%s)", type(e).__name__)
        parts = urlsplit(url)
        if not found.get("url", "").startswith(("ws://", "wss://")):
            raise CredentialUnavailable("the push connection has no WebSocket address")
        credential = Credential(url=found["url"], options=dict(found["options"]),
                                origin=f"{parts.scheme}://{parts.netloc}")
        remember(credential)
        return credential

    async def _route(self, route: Any) -> None:
        from sofascore_scraper.client import bridge
        from sofascore_scraper.services.live.push_source import ROUTE_ABORT, ROUTE_THROTTLE

        try:
            request = route.request
            action = route_action(request.url, request.resource_type, time.time() - self._loaded_at)
            if action == ROUTE_ABORT:
                await route.abort("blockedbyclient")
                return
            if action == ROUTE_THROTTLE:
                await bridge._wait_for_slot()
            await route.continue_()
        except Exception as e:  # sayfa kapanırken gelen istek: önemsiz
            logger.debug("A credential page request could not be routed (%s)", type(e).__name__)

    def close(self) -> None:
        from sofascore_scraper.client import bridge

        if self._bridge is None:
            return
        browser, self._bridge = self._bridge, None
        try:
            bridge._run_sync(browser.close(), 15.0)
        except Exception as e:
            logger.debug("The credential browser could not be closed (%s)", type(e).__name__)


# --- WebSocket (RFC 6455, istemci tarafı; yalnızca standart kütüphane) ---------------------------------


class WebSocketError(Exception):
    """Bağlantı kurulamadı ya da protokol bozuk; metin sunucu adresini içermez."""


class HandshakeRejected(WebSocketError):
    """Sunucu el sıkışmayı 101 dışında bir durumla yanıtladı (401/403: kimlik bilgisi ya da adres reddedildi)."""

    def __init__(self, status: int) -> None:
        super().__init__(f"handshake answered {status}")
        self.status = status


class ConnectionClosed(WebSocketError):
    """Sunucu bağlantıyı kapattı."""


def open_socket(host: str, port: int, tls: bool, timeout: float) -> socket.socket:
    """Soket katmanı: TCP bağlantısı ve (wss) sertifikası doğrulanan TLS. Testler bunu sahteyle değiştirir."""
    raw = socket.create_connection((host, port), timeout=timeout)
    if not tls:
        return raw
    context = ssl.create_default_context()
    try:
        return context.wrap_socket(raw, server_hostname=host)
    except BaseException:
        raw.close()
        raise


class WebSocket:
    """Tek bir WebSocket bağlantısı. Yalnızca bağlantı thread'i kullanır."""

    def __init__(self, sock: Any, buffered: bytes = b"") -> None:
        self._sock = sock
        self._buf = bytearray(buffered)
        self._parts: List[bytes] = []
        self._closed = False

    @classmethod
    def connect(cls, url: str, *, origin: str = "", timeout: float = CONNECT_TIMEOUT_SECONDS) -> "WebSocket":
        parts = urlsplit(url)
        if parts.scheme not in ("ws", "wss") or not parts.hostname:
            raise WebSocketError("not a WebSocket address")
        tls = parts.scheme == "wss"
        port = parts.port or (443 if tls else 80)
        sock = open_socket(parts.hostname, port, tls, timeout)
        try:
            sock.settimeout(timeout)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            host = parts.netloc.rpartition("@")[2]
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            lines = [f"GET {path} HTTP/1.1", f"Host: {host}", "Upgrade: websocket", "Connection: Upgrade",
                     f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
            if origin:
                lines.append(f"Origin: {origin}")
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
            head = bytearray()
            while b"\r\n\r\n" not in head:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionClosed("closed during the handshake")
                head.extend(chunk)
                if len(head) > MAX_HANDSHAKE_BYTES:
                    raise WebSocketError("handshake answer too long")
            header, _, rest = bytes(head).partition(b"\r\n\r\n")
            status_line, *header_lines = header.decode("latin-1").split("\r\n")
            fields = status_line.split(" ")
            status = int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else 0
            if status != 101:
                raise HandshakeRejected(status)
            headers = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in header_lines)}
            expected = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()).decode("ascii")
            if headers.get("sec-websocket-accept") != expected:
                raise WebSocketError("handshake answer does not match")
            return cls(sock, rest)
        except BaseException:
            sock.close()
            raise

    def send_text(self, text: str) -> None:
        self._send(0x1, text.encode("utf-8"))

    def _send(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header = bytes([0x80 | opcode, 0x80 | n])
        elif n < 1 << 16:
            header = bytes([0x80 | opcode, 0x80 | 126]) + n.to_bytes(2, "big")
        else:
            header = bytes([0x80 | opcode, 0x80 | 127]) + n.to_bytes(8, "big")
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(header + mask + masked)

    def _frame(self) -> Optional[Tuple[bool, int, bytes]]:
        """Arabellekte tam bir kare varsa (fin, opcode, gövde); yoksa None."""
        buf = self._buf
        if len(buf) < 2:
            return None
        fin, opcode = bool(buf[0] & 0x80), buf[0] & 0x0F
        masked, n, pos = bool(buf[1] & 0x80), buf[1] & 0x7F, 2
        if n == 126:
            if len(buf) < 4:
                return None
            n, pos = int.from_bytes(buf[2:4], "big"), 4
        elif n == 127:
            if len(buf) < 10:
                return None
            n, pos = int.from_bytes(buf[2:10], "big"), 10
        if n > MAX_MESSAGE_BYTES:
            raise WebSocketError("message too large")
        mask = b""
        if masked:
            if len(buf) < pos + 4:
                return None
            mask, pos = bytes(buf[pos:pos + 4]), pos + 4
        if len(buf) < pos + n:
            return None
        payload = bytes(buf[pos:pos + n])
        del buf[:pos + n]
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return fin, opcode, payload

    def recv(self, timeout: float) -> Optional[bytes]:
        """Bir veri mesajı (metin ya da ikili, bayt); süre dolarsa None. Kapanışta ConnectionClosed."""
        deadline = time.monotonic() + timeout
        while True:
            frame = self._frame()
            if frame is not None:
                fin, opcode, payload = frame
                if opcode == 0x8:
                    self._closed = True
                    raise ConnectionClosed("closed by the server")
                if opcode == 0x9:
                    self._send(0xA, payload)
                    continue
                if opcode == 0xA:
                    continue
                if opcode in (0x0, 0x1, 0x2):
                    self._parts.append(payload)
                    if sum(len(p) for p in self._parts) > MAX_MESSAGE_BYTES:
                        raise WebSocketError("message too large")
                    if fin:
                        message, self._parts = b"".join(self._parts), []
                        return message
                    continue
                raise WebSocketError("unknown frame type")
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            self._sock.settimeout(left)
            try:
                chunk = self._sock.recv(65536)
            except (socket.timeout, TimeoutError):
                return None
            except ssl.SSLWantReadError:
                return None
            if not chunk:
                self._closed = True
                raise ConnectionClosed("connection dropped")
            self._buf.extend(chunk)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._send(0x8, (1000).to_bytes(2, "big"))
            except OSError:
                pass
        try:
            self._sock.close()
        except OSError:
            pass


# --- bağlantı ------------------------------------------------------------------------------------------


Connector = Callable[[Credential], Any]


def default_connect(credential: Credential) -> WebSocket:
    return WebSocket.connect(credential.url, origin=credential.origin)


def proxy_in_use() -> bool:
    """Yapılandırılmış bir proxy var mı (istemci onu atlayıp doğrudan bağlanmamalı)."""
    try:
        from sofascore_scraper.client.transport import _get_proxy_config

        use_proxy, proxy_url = _get_proxy_config()
    except Exception:
        return False
    return bool(use_proxy and proxy_url)


def _wire_msg(subject: str, sid: int, payload: bytes) -> bytes:
    return b"MSG %s %d %d\r\n" % (subject.encode("utf-8"), sid, len(payload)) + payload + b"\r\n"


_ANNOUNCE = b"INFO {}\r\n"  # akışa bağlantının açıldığını bildirir; sunucunun INFO gövdesi hiçbir yere geçmez


class _Outcome:
    DROPPED = "dropped"  # bağlantı düştü (olağan ~30 dk döngüsü ya da ağ)
    REJECTED = "rejected"  # kimlik bilgisi reddedildi: yeniden okunur
    FAILED = "failed"  # bağlanılamadı ya da protokol bozuk
    STOPPED = "stopped"


class DirectConnection:
    """
    Servisin tek push bağlantısı. Kendi thread'inde çalışır: kimlik bilgisini okur (gerekirse), bağlanır, her
    abone sporun `sport.{spor}` konusuna abone olur ve gelenleri o sporun PushFeed'ine NATS satırları olarak
    yazar (PushFeed bunları `page` kaynağındaki gibi işaretlere çevirir). Yalnızca SUB, UNSUB, PING, PONG ve
    CONNECT gönderir.

    reader         kimlik bilgisini okuyan (BrowserCredentialReader)
    connect        Credential → WebSocket benzeri (send_text, recv(timeout), close); varsayılan gerçek istemci
    blocked        bağlanmayı engelleyen bir durum varsa nedeni (varsayılan: yapılandırılmış proxy)
    """

    def __init__(self, reader: CredentialReader, *, connect: Optional[Connector] = None,
                 blocked: Optional[Callable[[], Optional[str]]] = None,
                 ping_interval: float = PING_INTERVAL_SECONDS, recv_poll: float = RECV_POLL_SECONDS,
                 auth_wait: float = AUTH_WAIT_SECONDS, stable_after: float = STABLE_SESSION_SECONDS,
                 reconnect_first: float = RECONNECT_FIRST_SECONDS, reconnect_max: float = RECONNECT_MAX_SECONDS,
                 read_retry_first: float = READ_RETRY_FIRST_SECONDS, read_retry_max: float = READ_RETRY_MAX_SECONDS,
                 unhealthy_after: int = UNHEALTHY_AFTER, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._reader = reader
        self._connect = connect or default_connect
        self._blocked = blocked if blocked is not None else (lambda: "a proxy is configured" if proxy_in_use() else None)
        self.ping_interval = float(ping_interval)
        self._recv_poll = float(recv_poll)
        self._auth_wait = float(auth_wait)
        self._stable_after = float(stable_after)
        self._reconnect = (float(reconnect_first), float(reconnect_max))
        self._read_retry = (float(read_retry_first), float(read_retry_max))
        self._unhealthy_after = max(1, int(unhealthy_after))
        self._now = monotonic
        self._lock = threading.Lock()
        self._feeds: Dict[str, PushFeed] = {}
        self._changes: Deque[Tuple[str, str]] = collections.deque()  # ("sub" | "unsub", spor)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._credential: Optional[Credential] = None
        # Sayaçlar (durum ve testler için; hiçbiri kimlik bilgisi ya da adres içermez)
        self.sessions = 0  # kurulan WebSocket bağlantıları
        self.opens = 0  # kimlik bilgisi kabul edilen (PONG gelen) oturumlar
        self.messages = 0  # akışlara verilen MSG'ler
        self.failures = 0  # ardışık başarısız deneme
        self.rejections = 0
        self.credential_reads = 0
        self.unhealthy = False
        self.unavailable: Optional[str] = None
        # Gönderilen son işlemlerin türü ve konusu ("SUB sport.football"); kimlik bilgisi yok
        self.sent: Deque[str] = collections.deque(maxlen=200)

    # --- servisin thread'inden ---------------------------------------------------------------------

    def subscribe(self, sport: str, feed: PushFeed) -> None:
        with self._lock:
            if sport in self._feeds:
                return
            self._feeds[sport] = feed
            self._changes.append(("sub", sport))

    def unsubscribe(self, sport: str) -> None:
        with self._lock:
            if self._feeds.pop(sport, None) is not None:
                self._changes.append(("unsub", sport))

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="live-direct", daemon=True)
            self._thread.start()

    def close(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                logger.warning("The direct push connection did not stop in time; leaving it behind")
        self._credential = None
        try:
            self._reader.close()
        except Exception as e:
            logger.debug("The credential reader could not be closed (%s)", type(e).__name__)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --- bağlantı thread'i -------------------------------------------------------------------------

    def _feeds_now(self) -> Dict[str, PushFeed]:
        with self._lock:
            return dict(self._feeds)

    def _gone(self, reason: str) -> None:
        for feed in self._feeds_now().values():
            feed.gone(reason)

    def _wait(self, seconds: float) -> None:
        self._stop.wait(max(0.0, seconds))

    def _failed(self, reason: str, *, reading: bool = False) -> None:
        self.failures += 1
        first, cap = self._read_retry if reading else self._reconnect
        delay = min(cap, first * 2 ** (self.failures - 1))
        if self.failures >= self._unhealthy_after and not self.unhealthy:
            self.unhealthy = True
            logger.warning("The direct push source is unhealthy after %d failed attempts (%s); polling covers "
                           "every sport and the source retries every %.0f s at most", self.failures, reason, cap)
        else:
            logger.warning("The direct push connection is not available (%s); polling covers it, retrying in %.0f s",
                           reason, delay)
        self._gone(f"direct: {reason}")
        self._wait(delay)

    def _run(self) -> None:
        reason = self._blocked()
        if reason:
            self.unavailable = reason
            self.unhealthy = True
            logger.error("The direct push source cannot be used: %s, and it never connects around it; polling "
                         "covers every sport", reason)
            self._gone(f"direct: {reason}")
            return
        while not self._stop.is_set():
            sports = sorted(self._feeds_now())
            if not sports:
                self._wait(self._recv_poll)
                continue
            if self._credential is None:
                self.credential_reads += 1
                try:
                    self._credential = self._reader.read(sports[0])
                except CredentialUnavailable as e:
                    self._failed(f"the credential could not be read: {e}", reading=True)
                    continue
                except Exception as e:  # okuyucunun beklenmeyen hatası: aynı yol
                    self._failed(f"the credential could not be read: {type(e).__name__}", reading=True)
                    continue
                remember(self._credential)
            outcome, reason, lasted = self._session(self._credential)
            if outcome == _Outcome.STOPPED:
                break
            if outcome == _Outcome.DROPPED and lasted >= self._stable_after:
                self.failures = 0
                self.unhealthy = False
                logger.info("The direct push connection dropped (%s); reconnecting", reason)
                self._wait(self._reconnect[0])
                continue
            if outcome == _Outcome.REJECTED:
                self.rejections += 1
                self._credential = None
                self._failed(f"the server rejected the credential ({reason}); reading it again", reading=True)
                continue
            self._failed(reason)

    def _session(self, credential: Credential) -> Tuple[str, str, float]:
        """Bir bağlantı oturumu; (sonuç, neden, süre). Neden kısa ve sabittir: adres ya da kimlik bilgisi içermez."""
        started = self._now()
        try:
            ws = self._connect(credential)
        except HandshakeRejected as e:
            if e.status in (401, 403):
                return _Outcome.REJECTED, f"handshake {e.status}", 0.0
            return _Outcome.FAILED, f"handshake {e.status}", 0.0
        except (OSError, WebSocketError) as e:
            return _Outcome.FAILED, type(e).__name__, 0.0
        self.sessions += 1
        reader = NatsReader()
        sids: Dict[str, int] = {}  # spor → abonelik numarası (bu oturumda)
        conns: Dict[str, int] = {}  # spor → PushFeed'deki bağlantı numarası (oturum kabul edildikten sonra)
        next_sid = 0
        got_info = ready = False
        last_rx = last_ping = self._now()
        stale_after = STALE_FACTOR * self.ping_interval

        def send(line: str, what: str) -> None:
            ws.send_text(line + "\r\n")
            self.sent.append(what)

        def sub(sport: str) -> None:
            nonlocal next_sid
            subject = f"{SUBJECT_PREFIX}{sport}"
            if any(c in subject for c in "*> \t") or not sport:
                logger.warning("Refusing to subscribe to a subject that is not one sport (%r)", sport)
                return
            next_sid += 1
            sids[sport] = next_sid
            send(f"SUB {subject} {next_sid}", f"SUB {subject}")

        def announce(sport: str) -> None:
            feed = self._feeds_now().get(sport)
            if feed is not None and sport not in conns:
                conns[sport] = feed.connection()
                feed.received(conns[sport], _ANNOUNCE)

        def become_ready() -> None:
            # İlk PONG (ya da ondan önce gelen ilk MSG) sunucunun CONNECT'i kabul ettiğini gösterir. Ardışık başarısızlık
            # sayacı burada sıfırlanmaz: kabul edip hemen düşüren bir sunucuya sık sık yeniden bağlanılmasın; sayaç
            # ancak oturum STABLE_SESSION_SECONDS sürerse sıfırlanır (_run)
            nonlocal ready
            ready = True
            logger.info("The direct push connection is open (%d subject(s))", len(sids))
            for sport in sorted(sids):
                announce(sport)
            self.opens += 1

        def to_feeds(data: bytes) -> None:
            feeds = self._feeds_now()
            for sport, conn in list(conns.items()):
                feed = feeds.get(sport)
                if feed is not None:
                    feed.received(conn, data)

        def finish(outcome: str, why: str) -> Tuple[str, str, float]:
            feeds = self._feeds_now()
            for sport, conn in conns.items():
                feed = feeds.get(sport)
                if feed is not None:
                    feed.closed(conn)
            try:
                ws.close()
            except Exception:
                pass
            return outcome, why, self._now() - started

        try:
            while not self._stop.is_set():
                now = self._now()
                if got_info:
                    while True:
                        with self._lock:
                            change = self._changes.popleft() if self._changes else None
                        if change is None:
                            break
                        kind, sport = change
                        if kind == "sub" and sport not in sids:
                            sub(sport)
                            if ready:
                                announce(sport)
                        elif kind == "unsub" and sport in sids:
                            send(f"UNSUB {sids.pop(sport)}", f"UNSUB {SUBJECT_PREFIX}{sport}")
                            conns.pop(sport, None)  # akış servis tarafından kapatıldı
                    if not ready and now - started > self._auth_wait:
                        return finish(_Outcome.FAILED, "no answer to CONNECT")
                    if ready and now - last_ping >= self.ping_interval:
                        send("PING", "PING")
                        last_ping = now
                if now - last_rx > stale_after:
                    return finish(_Outcome.DROPPED, "stale connection")
                data = ws.recv(self._recv_poll)
                if data is None:
                    continue
                last_rx = self._now()
                for op in reader.feed(data):
                    if not got_info:
                        if op.kind != "INFO":
                            return finish(_Outcome.FAILED, "not a push server")
                        got_info = True
                        with self._lock:
                            self._changes.clear()  # bu oturumda abonelikler baştan gönderilir
                        options = json.dumps(dict(credential.options), separators=(",", ":"), ensure_ascii=False)
                        send(f"CONNECT {options}", "CONNECT")
                        for sport in sorted(self._feeds_now()):
                            sub(sport)
                        send("PING", "PING")  # PONG gelirse kimlik bilgisi kabul edildi
                        last_ping = self._now()
                    elif op.kind == "PING":
                        send("PONG", "PONG")
                        to_feeds(b"PING\r\n")
                    elif op.kind == "PONG":
                        if not ready:
                            become_ready()
                        else:
                            to_feeds(b"PONG\r\n")
                    elif op.kind == "-ERR":
                        if op.text in AUTH_ERRORS:
                            return finish(_Outcome.REJECTED, op.text)
                        logger.warning("The push server reported an error on the direct connection (%s)", op.text)
                        to_feeds(f"-ERR '{op.text}'\r\n".encode("utf-8"))
                    elif op.kind == "MSG":
                        if not ready:  # SUB'un mesajı PING'in PONG'undan önce gelebilir: CONNECT kabul edildi
                            become_ready()
                        sport = op.subject[len(SUBJECT_PREFIX):] if op.subject.startswith(SUBJECT_PREFIX) else ""
                        conn = conns.get(sport)
                        feed = self._feeds_now().get(sport)
                        if conn is not None and feed is not None:
                            feed.received(conn, _wire_msg(op.subject, sids.get(sport, 0), op.payload))
                            self.messages += 1
                if reader.broken:
                    return finish(_Outcome.FAILED, "unreadable stream")
        except ConnectionClosed:
            return finish(_Outcome.DROPPED, "closed by the server")
        except (OSError, WebSocketError) as e:
            return finish(_Outcome.DROPPED, type(e).__name__)
        return finish(_Outcome.STOPPED, "stopped")


# --- kaynak --------------------------------------------------------------------------------------------


class DirectSource:
    """
    Bir sporun `direct` kaynağı: ortak bağlantıya abone olur ve işaretleri servise verir (PageSource ile aynı
    arayüz: `feed`, `ensure_open`, `drain`, `close`). Durum tutmaz; birleştirme ve kapsam servisindir.
    """

    name = SOURCE_DIRECT

    def __init__(self, sport: str, connection: DirectConnection, *, clock: Callable[[], float] = time.time) -> None:
        self.sport = sport
        self._connection = connection
        self.feed = PushFeed(sport, clock)
        self._subscribed = False

    @property
    def failures(self) -> int:
        return self._connection.failures

    def ensure_open(self) -> None:
        if not self._subscribed:
            self._connection.subscribe(self.sport, self.feed)
            self._subscribed = True
        self._connection.start()

    def drain(self) -> List[Signal]:
        return self.feed.drain()

    def close(self) -> None:
        if self._subscribed:
            self._connection.unsubscribe(self.sport)
            self._subscribed = False


__all__ = [
    "AUTH_ERRORS",
    "SOURCE_DIRECT",
    "BrowserCredentialReader",
    "ConnectionClosed",
    "Credential",
    "CredentialReader",
    "CredentialUnavailable",
    "DirectConnection",
    "DirectSource",
    "HandshakeRejected",
    "WebSocket",
    "WebSocketError",
    "open_socket",
    "parse_connect",
    "proxy_in_use",
    "remember",
]
