"""
Push kaynağı `page` (docs/design/02-services.md bölüm 8.2; plan maddesi P24): izlenen her spor için gerçek bir
tarayıcı sayfası açık tutulur ve SofaScore'un kendi sayfasının açtığı push bağlantısının (NATS over WebSocket)
kareleri dinlenir.

    sayfa (spor başına) ─ websocket kareleri ─→ NatsReader ─→ PushFeed (thread'ler arası kuyruk)
        ─→ servis: LastKnown ile son bilinen maç nesnesine birleştirme ─→ gözlem ─→ indirgeyici

Kurallar (sahip kararları, 2026-10-01):

  * **Kimlik bilgisi yok.** Kaynak kendi bağlantısını açmaz, abonelik göndermez ve bağlantının kimlik bilgisini
    hiç okumaz: yalnızca sayfaya GELEN kareler dinlenir (`framereceived`); giden kareler (`CONNECT` kimlik
    bilgisini taşır) için dinleyici kurulmaz. `INFO` karesinin gövdesi (istemci IP'si, sunucu bilgileri)
    okunmaz, saklanmaz, yazılmaz: yalnızca "bağlantı kuruldu" işaretidir.
  * **Kare bir maç nesnesi değildir.** Kare yalnızca değişen alanları noktalı yollarla taşır
    (`{"status.code": 100, "homeScore.current": 2, "id": 1}`; docs/all-sports/README.md, "Push kanalı"). Servis
    maç başına son bilinen nesneyi tutar (LastKnown), onu yoklamanın gördükleriyle (canlı liste ve maç sayfası)
    açılışta ve her yeniden bağlanmadan sonra tohumlar ve her kareyi onun üzerine yazar (`merge_frame`).
    İndirgeyici boşlukları tolere eder: kopma sırasında kaçan kare bir sonraki yoklama turuyla iyileşir.
  * **Konular.** Spor sayfası `sport.{spor}`'a abone olur (sporun tamamı); `event.{id}` konuları da okunur,
    `odds.*` ve başka konular atlanır. Hangi maçın izlendiğine servis kapsamla karar verir.
  * **Sayfanın kendi istekleri** ortak istek bütçesinden sıra alır ya da kesilir: yükleme penceresinden
    (LOAD_WINDOW_SECONDS) sonra sayfanın tekrarlayan API istekleri gönderilmez (push bağlantısı etkilenmez);
    görseller, medya, yazı tipleri ve SofaScore dışındaki her adres (reklam, analitik) engellenir; yalnızca
    challenge sayfasının adresi geçer. Bu, ölçülen hafif sayfa yapılandırmasıdır (docs/push-channel/README.md,
    bölüm 6: 1,8–2,6 GB RSS).
  * **Profil.** Sayfalar köprünün profilini değil kendi profilini kullanır (`<profil>-live`, karar D10): işler
    başka süreçlerde köprü profilini kullanmaya devam eder. Dizin 0700 ile açılır (sofascore_scraper/private_files.py).

Tarayıcının kendisi `BrowserPageOpener`'dadır; servis ve testler `PageOpener` arayüzünü kullanır. Testler
gerçek tarayıcı açmaz: kayıtlı kareleri sahte bir sayfadan verir (tests/test_live_push_source.py).
"""
from __future__ import annotations

import collections
import copy
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Protocol, Tuple, Union
from urllib.parse import urlsplit

from sofascore_scraper.status import StatusClass, classify_status

logger = logging.getLogger(__name__)

SOURCE_PAGE = "page"
VIA_PUSH = "push"  # gözlemi push karesi gösterdi (2.x olayının `source` alanı)

SUBJECT_SPORT = "sport."
SUBJECT_EVENT = "event."

MAX_PENDING_BYTES = 4 * 1024 * 1024  # tamamlanmamış NATS işlemi bundan büyükse bağlantı bozuk sayılır
MAX_QUEUE = 20000  # servis okumazsa kuyrukta en çok bu kadar işaret; taşarsa eskiler atılır ve boşluk bildirilir
LAST_KNOWN_CAP = 5000  # spor başına en çok bu kadar maçın son bilinen nesnesi
OPEN_RETRY_FIRST_SECONDS = 5.0
OPEN_RETRY_MAX_SECONDS = 300.0

# Sayfanın istekleri (ölçülen hafif yapılandırma)
FIRST_PARTY_HOST = re.compile(r"(^|\.)sofascore\.(com|app|net|io)$")
ALLOWED_THIRD_PARTY = ("challenges.cloudflare.com",)
BLOCKED_TYPES = ("image", "media", "font")
API_TYPES = ("xhr", "fetch", "eventsource")
LOAD_WINDOW_SECONDS = 45.0
RECONNECT_PATHS = ("/token/", "/config/")  # yeniden bağlanma bunlara gerekirse bozulmasın

ROUTE_ABORT = "abort"
ROUTE_THROTTLE = "throttle"
ROUTE_CONTINUE = "continue"

Payload = Union[str, bytes, bytearray, memoryview]


# --- NATS metin protokolü ------------------------------------------------------------------------------


@dataclass(frozen=True)
class NatsOp:
    """
    Sunucudan gelen bir NATS işlemi.

    kind     "INFO", "MSG" (HMSG de MSG olarak verilir), "PING", "PONG", "+OK", "-ERR" ya da "OTHER"
    subject  MSG'nin konusu
    payload  MSG'nin gövdesi (bayt); INFO'nun gövdesi hiçbir zaman tutulmaz
    text     -ERR'in bilinen NATS hata metni (NATS_ERRORS) ya da "other"
    """

    kind: str
    subject: str = ""
    payload: bytes = b""
    text: str = ""


# NATS sunucusunun bilinen hata metinleri. -ERR satırından yalnızca bunlardan biri alınır; satırın geri kalanı
# (ne olursa olsun) hiçbir yere yazılmaz.
NATS_ERRORS = (
    "Unknown Protocol Operation", "Attempted To Connect To Route Port", "Authorization Violation",
    "Authorization Timeout", "Invalid Client Protocol", "Maximum Control Line Exceeded", "Parser Error",
    "Secure Connection - TLS Required", "Stale Connection", "Maximum Connections Exceeded", "Slow Consumer",
    "Maximum Payload Violation", "Invalid Subject", "Permissions Violation", "User Authentication Expired",
    "Account Authentication Expired", "User Authentication Revoked",
)


def _error_text(raw: str) -> str:
    text = raw.strip().strip("'").lower()
    return next((known for known in NATS_ERRORS if text.startswith(known.lower())), "other")


class NatsReader:
    """
    Bir bağlantının sunucu yönündeki akışı → NATS işlemleri. WebSocket kare sınırları işlem sınırlarıyla
    çakışmayabilir (bir karede birden çok işlem, bir işlem iki karede): arta kalan baytlar tutulur. MSG gövdesi
    başlıktaki bayt uzunluğuyla okunur. Bozuk bir başlık (uzunluğu sayı değil) o satırı atlar.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.broken = False

    def feed(self, data: Payload) -> List[NatsOp]:
        if self.broken:
            return []
        chunk = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        self._buf.extend(chunk)
        ops: List[NatsOp] = []
        while True:
            end = self._buf.find(b"\r\n")
            if end < 0:
                break
            line = bytes(self._buf[:end]).decode("utf-8", errors="replace")
            parts = line.split(" ")
            cmd = parts[0].upper()
            if cmd in ("MSG", "HMSG"):
                sizes = parts[-2:] if cmd == "HMSG" else parts[-1:]
                if len(parts) < (5 if cmd == "HMSG" else 4) or not all(s.isdigit() for s in sizes):
                    del self._buf[:end + 2]
                    continue
                total = int(sizes[-1])
                header = int(sizes[0]) if cmd == "HMSG" else 0
                start = end + 2
                if len(self._buf) < start + total + 2:
                    break  # gövde sonraki karede
                body = bytes(self._buf[start + header:start + total])
                del self._buf[:start + total + 2]
                ops.append(NatsOp("MSG", subject=parts[1], payload=body))
                continue
            del self._buf[:end + 2]
            if cmd == "INFO":
                ops.append(NatsOp("INFO"))  # gövde (istemci IP'si, sunucu bilgileri) atılır
            elif cmd in ("PING", "PONG", "+OK"):
                ops.append(NatsOp(cmd))
            elif cmd == "-ERR":
                ops.append(NatsOp("-ERR", text=_error_text(line[4:])))
            elif cmd:
                ops.append(NatsOp("OTHER"))
        if len(self._buf) > MAX_PENDING_BYTES:
            self.broken = True
            self._buf.clear()
        return ops


def frame_body(op: NatsOp) -> Optional[Dict[str, Any]]:
    """MSG gövdesi → kare (noktalı yol → değer); JSON nesnesi değilse ya da tam sayı `id` yoksa None."""
    try:
        body = json.loads(op.payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    eid = body.get("id")
    if type(eid) is not int:
        return None
    return body


def subject_wanted(subject: str, sport: str) -> bool:
    """Spor sayfasının konusu ya da bir maçın konusu; oran (`odds.*`) ve başka konular hayır."""
    if subject == f"{SUBJECT_SPORT}{sport}":
        return True
    return subject.startswith(SUBJECT_EVENT) and subject[len(SUBJECT_EVENT):].isdigit()


# --- birleştirme -----------------------------------------------------------------------------------


def merge_frame(base: Mapping[str, Any], frame: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Son bilinen maç nesnesi + kare → yeni nesne (verilen nesne değişmez). Her noktalı yol iç içe sözlüğe
    yazılır; ara düğüm yoksa ya da sözlük değilse oluşturulur. `null` değer de yazılır (`lastPeriod: null`).

    Durum: kare yalnız `status.code` taşıyorsa (`status.type` değişmediği için yok) ve nesnedeki eski tür bu
    kodla çelişiyorsa (kopma sırasında tür değişikliği kaçtı) eski tür silinir; sınıf koddan çıkar
    (sofascore_scraper/status.py: önce tür, yoksa kod).
    """
    out: Dict[str, Any] = copy.deepcopy(dict(base))
    for path, value in frame.items():
        parts = str(path).split(".")
        node: Dict[str, Any] = out
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = copy.deepcopy(value)
    status = out.get("status")
    if "status.code" in frame and "status.type" not in frame and isinstance(status, dict) and status.get("type"):
        by_code = classify_status({"status": {"code": status.get("code")}})
        if by_code is not StatusClass.UNKNOWN and by_code is not classify_status(out):
            del status["type"]
    return out


def change_ts(event: Mapping[str, Any]) -> Optional[int]:
    value = (event.get("changes") or {}).get("changeTimestamp") if isinstance(event.get("changes"), dict) else None
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class LastKnown:
    """
    Bir sporun maç başına son bilinen nesnesi. Yoklamanın gördükleri (`seed`) ve push kareleri (`apply`) yazar.

    Eski gözlem yeniyi ezmez: nesne en son push karesinden geldiyse ve yoklamanın getirdiği nesnenin
    `changes.changeTimestamp`'i ondan küçükse (CDN önbelleği birkaç saniye eski yanıt verebilir), yoklamanınki
    tutulmaz ve `seed` False döndürür; servis o gözlemi indirgeyiciye vermez.
    """

    def __init__(self, cap: int = LAST_KNOWN_CAP) -> None:
        self._events: "collections.OrderedDict[int, Tuple[Dict[str, Any], Optional[int]]]" = collections.OrderedDict()
        self._cap = cap

    def __contains__(self, eid: object) -> bool:
        return eid in self._events

    def __len__(self) -> int:
        return len(self._events)

    def get(self, eid: int) -> Optional[Dict[str, Any]]:
        entry = self._events.get(eid)
        return entry[0] if entry else None

    def _put(self, eid: int, event: Dict[str, Any], push_ts: Optional[int]) -> None:
        self._events[eid] = (event, push_ts)
        self._events.move_to_end(eid)
        while len(self._events) > self._cap:
            self._events.popitem(last=False)

    def seed(self, event: Mapping[str, Any]) -> bool:
        try:
            eid = int(str(event.get("id")))
        except ValueError:
            return True
        entry = self._events.get(eid)
        if entry is not None and entry[1] is not None:
            incoming = change_ts(event)
            if incoming is not None and incoming < entry[1]:
                return False
        self._put(eid, copy.deepcopy(dict(event)), None)
        return True

    def apply(self, eid: int, frame: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """Kareyi son bilinen nesneye yazar ve yeni nesneyi döndürür; maç bilinmiyorsa None."""
        base = self.get(eid)
        if base is None:
            return None
        merged = merge_frame(base, frame)
        self._put(eid, merged, change_ts(merged) or 0)
        return copy.deepcopy(merged)


# --- sayfadan servise: işaret kuyruğu --------------------------------------------------------------


SIGNAL_OPEN = "open"  # sayfanın NATS bağlantısı kuruldu (INFO geldi)
SIGNAL_CLOSE = "close"  # açık NATS bağlantısı kalmadı
SIGNAL_FRAME = "frame"  # (konu, kare)
SIGNAL_PING = "ping"  # PING ya da PONG: bağlantı canlı
SIGNAL_ERROR = "error"  # -ERR (kısa metin)
SIGNAL_GONE = "gone"  # sayfa kapandı, çöktü ya da açılamadı (neden)
SIGNAL_GAP = "gap"  # kuyruk taştı: kare kaybedildi


@dataclass(frozen=True)
class Signal:
    kind: str
    at: float
    subject: str = ""
    frame: Optional[Dict[str, Any]] = None
    text: str = ""


class PushFeed:
    """
    Bir spor sayfasının push işaretleri. Tarayıcının thread'i yazar (`connection`, `received`, `closed`,
    `gone`), servisin thread'i okur (`drain`). Bağlantı NATS mı, içeriğinden anlaşılır: ilk karesi `INFO`
    olmayan WebSocket (sayfadaki başka sağlayıcılar) yok sayılır; adres hiç okunmaz.
    """

    def __init__(self, sport: str, clock: Callable[[], float] = time.time, *, max_queue: int = MAX_QUEUE) -> None:
        self.sport = sport
        self._clock = clock
        self._lock = threading.Lock()
        self._queue: Deque[Signal] = collections.deque()
        self._max_queue = max_queue
        self._readers: Dict[int, NatsReader] = {}
        self._nats: Dict[int, bool] = {}  # bağlantı → NATS mı (None: henüz bilinmiyor)
        self._open: set = set()
        self._next = 0
        self.dropped = 0
        self.frames = 0

    def _push(self, signal: Signal) -> None:
        if len(self._queue) >= self._max_queue:
            self._queue.popleft()
            self.dropped += 1
            if not self._queue or self._queue[-1].kind != SIGNAL_GAP:
                self._queue.append(Signal(SIGNAL_GAP, signal.at))
        self._queue.append(signal)

    def connection(self) -> int:
        """Sayfada yeni bir WebSocket açıldı; numarası döner."""
        with self._lock:
            self._next += 1
            self._readers[self._next] = NatsReader()
            return self._next

    def received(self, conn: int, payload: Payload) -> None:
        now = self._clock()
        with self._lock:
            reader = self._readers.get(conn)
            if reader is None or self._nats.get(conn) is False:
                return
            ops = reader.feed(payload)
            if conn not in self._nats:
                if not ops:
                    return
                self._nats[conn] = ops[0].kind == "INFO"
                if not self._nats[conn]:
                    self._readers.pop(conn, None)
                    return
            for op in ops:
                if op.kind == "INFO":
                    if conn not in self._open:
                        self._open.add(conn)
                        self._push(Signal(SIGNAL_OPEN, now))
                elif op.kind in ("PING", "PONG"):
                    self._push(Signal(SIGNAL_PING, now))
                elif op.kind == "-ERR":
                    self._push(Signal(SIGNAL_ERROR, now, text=op.text))
                elif op.kind == "MSG" and subject_wanted(op.subject, self.sport):
                    body = frame_body(op)
                    if body is not None:
                        self.frames += 1
                        self._push(Signal(SIGNAL_FRAME, now, subject=op.subject, frame=body))
            if reader.broken:
                logger.warning("The push connection of the %s page sent an unreadable stream; ignoring it", self.sport)
                self._close_locked(conn, now)
                self._nats[conn] = False

    def _close_locked(self, conn: int, now: float) -> None:
        self._readers.pop(conn, None)
        if conn in self._open:
            self._open.discard(conn)
            if not self._open:
                self._push(Signal(SIGNAL_CLOSE, now))

    def closed(self, conn: int) -> None:
        with self._lock:
            self._close_locked(conn, self._clock())

    def gone(self, reason: str) -> None:
        """Sayfa kapandı, çöktü ya da açılamadı: açık bağlantılar da biter."""
        now = self._clock()
        with self._lock:
            for conn in list(self._open):
                self._close_locked(conn, now)
            self._readers.clear()
            self._push(Signal(SIGNAL_GONE, now, text=reason))

    def drain(self) -> List[Signal]:
        with self._lock:
            items = list(self._queue)
            self._queue.clear()
        return items


# --- sayfa: arayüz ve kaynak ---------------------------------------------------------------------------


class PageHandle(Protocol):
    """Açılan bir spor sayfası. `failed`: açılamadıysa nedeni."""

    @property
    def ready(self) -> bool: ...

    @property
    def failed(self) -> Optional[str]: ...

    def close(self) -> None: ...


class PageOpener(Protocol):
    """Spor sayfalarını açan taraf: gerçek tarayıcı (BrowserPageOpener) ya da testlerin sahtesi."""

    def open(self, sport: str, feed: PushFeed) -> PageHandle:
        """Sayfayı açmaya başlar (beklemez); kareler `feed`'e yazılır, kapanma `feed.gone` ile bildirilir."""

    def close(self) -> None:
        """Bütün sayfaları ve tarayıcıyı kapatır."""


class PageSource:
    """
    Bir sporun `page` kaynağı: sayfayı açar, kapanınca ya da açılamazsa artan aralıklarla (5 sn → 5 dk) yeniden
    açar ve işaretleri servise verir. Durum tutmaz; birleştirme ve kapsam servisindir.
    """

    name = SOURCE_PAGE

    def __init__(self, sport: str, opener: PageOpener, *, clock: Callable[[], float] = time.time,
                 feed_clock: Optional[Callable[[], float]] = None) -> None:
        self.sport = sport
        self._opener = opener
        self._clock = clock
        self.feed = PushFeed(sport, feed_clock or clock)
        self._handle: Optional[PageHandle] = None
        self.failures = 0
        self.retry_at = 0.0
        self.opens = 0

    def ensure_open(self) -> None:
        """Sayfa yoksa (ilk kez ya da kapandıktan sonra) ve bekleme süresi dolduysa açmaya başlar."""
        if self._handle is not None:
            return
        now = self._clock()
        if now < self.retry_at:
            return
        self.opens += 1
        try:
            self._handle = self._opener.open(self.sport, self.feed)
        except Exception as e:  # açma hatası: yeniden denenir, servis yoklamayla sürer
            self._failed(f"{type(e).__name__}")
            self.feed.gone(f"open failed: {type(e).__name__}")

    def _failed(self, reason: str) -> None:
        self.failures += 1
        delay = min(OPEN_RETRY_MAX_SECONDS, OPEN_RETRY_FIRST_SECONDS * 2 ** (self.failures - 1))
        self.retry_at = self._clock() + delay
        logger.warning("The live page of %s is not available (%s); polling covers it, retrying in %.0f s",
                       self.sport, reason, delay)

    def drain(self) -> List[Signal]:
        signals = self.feed.drain()
        for signal in signals:
            if signal.kind == SIGNAL_OPEN:
                self.failures = 0
            elif signal.kind == SIGNAL_GONE and self._handle is not None:
                handle, self._handle = self._handle, None
                self._failed(signal.text or "closed")
                try:
                    handle.close()
                except Exception as e:
                    logger.debug("Closing the %s page failed (%s)", self.sport, type(e).__name__)
        return signals

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except Exception as e:
                logger.debug("Closing the %s page failed (%s)", self.sport, type(e).__name__)


# --- sayfanın istekleri ----------------------------------------------------------------------------


def route_action(url: str, resource_type: str, since_load: float) -> str:
    """
    Sayfanın bir isteğine ne yapılır (ölçülen hafif yapılandırma):

      * SofaScore dışındaki adresler (reklam, analitik, üçüncü taraf) ve görsel, medya, yazı tipi: kesilir;
        yalnızca challenge sayfasının adresi geçer;
      * SofaScore'a giden xhr/fetch: yükleme penceresinde ortak bütçeden sıra alır; pencereden sonra kesilir
        (yeniden bağlanmanın gerekebileceği `/token/` ve `/config/` hariç);
      * geri kalan (sayfanın kendisi, betikler, stiller): geçer.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme in ("data", "blob"):
        return ROUTE_CONTINUE
    first_party = bool(FIRST_PARTY_HOST.search(host))
    if not first_party:
        return ROUTE_CONTINUE if host.endswith(ALLOWED_THIRD_PARTY) and resource_type not in BLOCKED_TYPES \
            else ROUTE_ABORT
    if resource_type in BLOCKED_TYPES:
        return ROUTE_ABORT
    if resource_type in API_TYPES:
        if since_load > LOAD_WINDOW_SECONDS and not any(p in parts.path for p in RECONNECT_PATHS):
            return ROUTE_ABORT
        return ROUTE_THROTTLE
    return ROUTE_CONTINUE


def live_profile_dir() -> str:
    """Canlı sayfaların profili: köprü profilinin yanında `<profil>-live` (karar D10)."""
    from sofascore_scraper.paths import browser_profile_dir

    return browser_profile_dir().rstrip("/\\") + "-live"


def sport_page_url(sport: str) -> str:
    """Sporun sayfası (yalnızca o `sport.{spor}` konusuna abone olur): köprünün ana sayfasının altında."""
    from sofascore_scraper.client import bridge

    return f"{bridge.HOME_URL.rstrip('/')}/{sport}"


def quiet_url() -> str:
    """Tarayıcının kendi sekmesinin beklediği, API çağırmayan sayfa (ölçümdeki boş sekme: robots.txt)."""
    from sofascore_scraper.client import bridge

    parts = urlsplit(bridge.HOME_URL)
    return f"{parts.scheme}://{parts.netloc}/robots.txt"


def attach(page: Any, feed: PushFeed) -> None:
    """
    Playwright sayfasına dinleyicileri bağlar: her WebSocket'in GELEN kareleri ve kapanışı, sayfanın kapanması
    ve çökmesi. Giden kareler (`framesent`) dinlenmez: kimlik bilgisi orada, kaynak onu hiç görmez.
    """

    def on_websocket(ws: Any) -> None:
        conn = feed.connection()
        ws.on("framereceived", lambda payload: feed.received(conn, payload))
        ws.on("close", lambda *_: feed.closed(conn))

    page.on("websocket", on_websocket)
    page.on("close", lambda *_: feed.gone("page closed"))
    page.on("crash", lambda *_: feed.gone("page crashed"))


@dataclass
class _BrowserPage:
    """BrowserPageOpener'ın verdiği tutamaç."""

    opener: "BrowserPageOpener"
    sport: str
    page: Any = None
    ready: bool = False
    failed: Optional[str] = None
    future: Any = field(default=None, repr=False)

    def close(self) -> None:
        if self.future is not None and not self.future.done():
            self.future.cancel()
        self.opener._close_page(self)


class BrowserPageOpener:
    """
    Gerçek tarayıcı: canlı profille ayrı bir köprü örneği (sofascore_scraper/client/bridge.py, `BrowserBridge`) ve spor başına
    bir sayfa. Köprü açılışı (Scrapling StealthySession, challenge çözümü, proxy) aynen kullanılır; köprünün
    kendi sekmesi API çağırmayan bir sayfada bekler. Bütün Playwright çağrıları köprünün arka plan döngüsünde
    çalışır; `open` beklemez.
    """

    STARTUP_SECONDS = 210.0  # tarayıcı açılışı (150 sn) + sayfanın yüklenmesi

    def __init__(self, profile_dir: Optional[str] = None) -> None:
        self.profile_dir = profile_dir or live_profile_dir()
        self._bridge: Any = None
        self._routed = False
        self._loaded_at: Dict[int, float] = {}
        self._pages: List[_BrowserPage] = []

    def _get_bridge(self) -> Any:
        from sofascore_scraper.client import bridge

        if self._bridge is None:
            # Sağlık sinyali yazılmaz: canlı sayfanın tarayıcısı indirmelerin köprüsünün sağlığını belirlemez
            self._bridge = bridge.BrowserBridge(profile_dir=self.profile_dir, home_url=quiet_url(), report_health=False)
        return self._bridge

    def open(self, sport: str, feed: PushFeed) -> PageHandle:
        from sofascore_scraper.client import bridge

        handle = _BrowserPage(self, sport)
        self._pages.append(handle)
        handle.future, _ = bridge._submit(self._open(handle, feed))
        return handle

    async def _open(self, handle: _BrowserPage, feed: PushFeed) -> None:
        import asyncio

        try:
            await asyncio.wait_for(self._open_page(handle, feed), self.STARTUP_SECONDS)
        except asyncio.CancelledError:
            raise
        except BaseException as e:
            handle.failed = type(e).__name__
            feed.gone(f"open failed: {type(e).__name__}")

    async def _open_page(self, handle: _BrowserPage, feed: PushFeed) -> None:
        b = self._get_bridge()
        await b.ensure_ready()
        if not self._routed:
            await b.context.route("**/*", self._route)
            self._routed = True
        page = await b.context.new_page()
        handle.page = page
        attach(page, feed)
        url = sport_page_url(handle.sport)
        self._loaded_at[id(page)] = time.time()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if "captcha.html" in (page.url or ""):
            await b.solve_challenge()
            self._loaded_at[id(page)] = time.time()
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        handle.ready = True
        logger.info("The live page of %s is open", handle.sport)

    async def _route(self, route: Any) -> None:
        from sofascore_scraper.client import bridge

        try:
            request = route.request
            try:
                page = request.frame.page
            except Exception:
                page = None  # service worker
            since = time.time() - self._loaded_at.get(id(page), 0.0) if page is not None else LOAD_WINDOW_SECONDS + 1
            action = route_action(request.url, request.resource_type, since)
            if action == ROUTE_ABORT:
                await route.abort("blockedbyclient")
                return
            if action == ROUTE_THROTTLE:
                await bridge._wait_for_slot()
            await route.continue_()
        except Exception as e:  # sayfa kapanırken gelen istek: önemsiz
            logger.debug("A live page request could not be routed (%s)", type(e).__name__)

    def _close_page(self, handle: _BrowserPage) -> None:
        from sofascore_scraper.client import bridge

        page, handle.page = handle.page, None
        if handle in self._pages:
            self._pages.remove(handle)
        if page is None:
            return
        self._loaded_at.pop(id(page), None)

        async def close() -> None:
            await page.close()

        try:
            bridge._run_sync(close(), 15.0)
        except Exception as e:
            logger.debug("The %s page could not be closed (%s)", handle.sport, type(e).__name__)

    def close(self) -> None:
        from sofascore_scraper.client import bridge

        for handle in list(self._pages):
            handle.close()
        if self._bridge is not None:
            try:
                bridge._run_sync(self._bridge.close(), 15.0)
            except Exception as e:
                logger.debug("The live browser could not be closed (%s)", type(e).__name__)
            self._bridge = None
            self._routed = False


__all__ = [
    "ALLOWED_THIRD_PARTY",
    "LOAD_WINDOW_SECONDS",
    "SOURCE_PAGE",
    "VIA_PUSH",
    "BrowserPageOpener",
    "LastKnown",
    "NatsOp",
    "NatsReader",
    "PageHandle",
    "PageOpener",
    "PageSource",
    "PushFeed",
    "Signal",
    "attach",
    "change_ts",
    "frame_body",
    "live_profile_dir",
    "merge_frame",
    "route_action",
    "sport_page_url",
    "subject_wanted",
]
