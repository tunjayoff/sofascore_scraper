"""
Talimat 05: SofaScore'un tüm sporları için pasif keşif tarayıcısı.

Tek bir BrowserBridge tarayıcısı açık kalır; komutlar bir kontrol klasöründen okunur, böylece
keşif adım adım yönlendirilir (tarayıcı ve challenge çözümü her adımda yeniden açılmaz):

    python scripts/explore_all_sports.py serve CTL_DIR [--new-run] [--max-requests N] [--max-hours H]
    python scripts/explore_all_sports.py send CTL_DIR '{"op": "goto", "url": "...", "sport": "..."}'

Bütçe koşu (run) başınadır. `--new-run` yeni bir koşu açar (kimliği `--run-id` ya da UTC zaman damgası): sayaçlar
sıfırdan başlar, önceki koşunun durumu `_state.json` içinde `previous_runs` altında saklanır; kayıt dosyaları
(requests.jsonl, pages.jsonl, ws.jsonl, events/, samples/) silinmez, yeni her satır `run_id` taşır. `--new-run`
olmadan son koşu sürdürülür (yarıda kalmış bir koşu); bütçesi bitmiş bir koşu sürdürülmek istenirse script
tarayıcıyı açmadan çıkar.

Komutlar (her biri `page_type` etiketi alır: home, sport, date, live, event, event-tab:<ad>, team,
player, tournament, search, ...; hangi sayfanın/sekmenin hangi isteği tetiklediği buradan okunur):
    goto   {url, sport, page_type, dwell?, settle?}  sayfayı açar; trafik durulana kadar (settle sn
                                                    sessizlik, en fazla dwell sn) bekler
    click  {text? | selector?, sport, page_type, ...}  sekmeye/düğmeye tıklar, sonra goto gibi bekler
    fill   {selector, text, sport, page_type, ...}     bir alana yazar (arama), sonra bekler
    links  {pattern?}                                  sayfadaki <a href> listesi (regex süzgeçli)
    text   {limit?}                                    görünen metin (sekme adlarını sayfadan okumak için)
    probe  {hold?}                                     kesici öz denetimi (bütçe harcamaz, ağa çıkmaz; aşağıda)
    stats  {} / quit {}
Her gezinti komutu, o adımda ilk kez görülen pattern'leri (`new_patterns`) döndürür: keşifte derinleşme
ölçütü "yeni pattern çıkıyor mu". Adım sayaçları da döner: `page_requests` (gönderilen), `step_gone` (sırada
beklerken sayfası değiştiği için ölen, gönderilmeyen) ve `step_intercept_errors` (yanıtlanamayan); ikincisi 0
değilse adım bozuktur.

Öz denetim (`probe`, `serve` açılışta da bir kez çalıştırır ve sonucu "ready" satırına yazar): sayfadan
`https://explorer-probe.invalid/` adresine bir fetch atılır; kesici onu `hold` sn (varsayılan 2) tutar ve yerelde
yanıtlar. `.invalid` hiçbir zaman çözülmez: kesici çalışmasa bile istek makineden çıkmaz. `ok: false` ise kesici
bekletilen isteği yanıtlayamıyor demektir; bütçe harcanmadan durulur.

Kurallar (talimat):
  - API keşfi pasif: sayfanın attığı tüm XHR/fetch/EventSource/WebSocket trafiği (response, requestfailed,
    websocket olayları; üçüncü taraf alan adları dahil) kaydedilir. Bu script kendisi hiçbir API isteği atmaz;
    bilinen uç noktalar yalnızca sonradan karşılaştırma tabanıdır.
  - SofaScore alan adlarına giden her XHR/fetch/EventSource isteği keşif sayfasının CDP oturumunda (Fetch alanı)
    durdurulur ve scripts/_research_common.py kilit dosyasından sıra alınca gönderilir: diğer araştırma
    süreçleriyle birlikte toplamda ≤ 1 istek/sn; bütçeye bunlar sayılır. Service worker atlanır
    (Network.setBypassServiceWorker): sayfanın her isteği ağa doğrudan, yani bu kesiciden geçer.
    FX-29: önceden `context.route` kullanılıyordu. Playwright sürücüsü aynı türden 10.000'i aşan nesnenin en
    eski 1.000'ini toplar (dispatcher.ts, maxDispatchersForBucket); engellenen görsellerin yeniden deneme seli
    saniyede yüzlerce Route yaratınca kilitte sıra bekleyen Route'lar toplanıyor, istek ne gönderiliyor ne
    kaydediliyordu ("The object has been collected to prevent unbounded heap growth"). Bekleyen CDP isteği
    yalnızca bir kimlik dizgisidir: toplanacak bir nesne yoktur.
    FX-29b: sayfa sırada istek varken yeni bir belgeye geçerse (SofaScore `/football/...` adresini ~0,8 sn sonra
    istemci tarafında `/tr/football/...` adresine yeniden yükler) eski belgenin bekleyen istekleri Chromium'da
    sessizce ölür: sayfaya da Network olaylarına da hata düşmez, sonradan verilen continueRequest "Invalid
    InterceptionId" ile döner, istek hiç gönderilmez. Eskiden her ölü istek yine de sırasını (1 sn) yiyordu ve
    yeni belgenin istekleri adım bitene kadar onların arkasında kalıp düşürülüyordu (2026-10-09 canlı: 1 gönderim,
    29 hata). Şimdi sıradaki isteğin Chromium'da hâlâ durduğu sıra alınmadan hemen önce ve sıradan sonra yan
    etkisiz bir çağrıyla (Fetch.getResponseBody: canlı istekte "Can only get response body...", ölüde "Invalid
    InterceptionId") denetlenir; ölü istek "gone" sayılır, sıra almaz, yanıtlanmaz. Nedeni olaylardan yazılır
    (Page.frameNavigated ile yeni loaderId, çerçevenin kalkması, Network.loadingFailed). Chromium aynı isteği
    yeniden durdurursa (aynı networkId) sırası korunur, bir kez gönderilir. Adım bittiğinde sıradakiler de sıra
    almadan düşürülür.
  - Sayfa gezintisi/tıklama ≥ 5 sn arayla. Görseller ağa gitmeden 1x1 boş GIF ile yanıtlanır (hata alan görsel
    yeniden denemesiyle istek seli yaratmasın), medya ve font istekleri iptal edilir.
  - Bütçe (koşu başına): varsayılan 5.000 API isteği ya da 6 saat; aşılınca API istekleri iptal edilir.
  - Kullanıcı içeriği (yorum, oy, profil) sayfalarına gidilmez; WebSocket kareleri kısaltılarak saklanır.
  - Maskeleme: NATS INFO karesindeki client_ip ve CONNECT'in kimlik alanları yazılmadan maskelenir; üçüncü taraf
    adresleri (imzalı token taşıyabilir) yalnızca alan adı + ilk yol parçasıyla yazılır, gövdeleri saklanmaz.
    FX-29c: diske yazılan her şey (örnekler, olaylar, istek/sayfa/ws satırları) `redact`'tan geçer: istemci konumu
    anahtarlarının (GEO_KEYS: ip, city, region_code, f, ...) değeri her derinlikte `<redacted>` olur (stadyum
    bilgisi `venue` altı hariç) ve her dizgideki IPv4/IPv6 adresi maskelenir. Sayfanın çağırdığı
    `/api/v1/country/alpha2` istemcinin IP'sini, şehrini ve TLS parmak izini döndürür; NATS INFO'nun connect_urls
    ve host alanları push sunucularının adresleridir. tests/test_research_privacy.py kayıtlı veriyi tarar.

Çıktı: research/all_sports/ (requests.jsonl, samples/, events/, ws.jsonl, pages.jsonl, _state.json).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import glob
import ipaddress
import json
import os
import re
import sys
import time
from collections import OrderedDict
from typing import Any, Coroutine, Dict, List, Optional, Pattern
from urllib.parse import parse_qs, quote, urlparse

os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault(
    "SOFASCORE_BROWSER_PROFILE", os.path.expanduser("~/.cache/sofascore_research/chrome_all_sports")
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _research_common as rc  # noqa: E402
from _all_sports_patterns import SOFA_HOST, api_pattern  # noqa: E402

ROOT = rc.ROOT
import sofascore_scraper.challenge_solver as cs  # noqa: E402

OUT = os.path.join(ROOT, "research", "all_sports")
MAX_API_REQUESTS = 5000
MAX_SECONDS = 6 * 3600
PAGE_GAP = 5.0
SAMPLES_PER_PATTERN = 3
FULL_SAMPLE_BYTES = 20_000
QUIET_URL = "https://www.sofascore.com/robots.txt"
BLOCKED_TYPES = ("image", "media", "font")
TRAFFIC_TYPES = ("xhr", "fetch", "eventsource", "websocket")
# Kesicinin durdurduğu CDP kaynak türleri → Playwright'ın resource_type adları. Belge, script, stil ve
# WebSocket durdurulmaz (eski route da onları sıraya sokmuyordu).
CDP_TYPES = {"Image": "image", "Media": "media", "Font": "font", "XHR": "xhr", "Fetch": "fetch",
             "EventSource": "eventsource"}
BLANK_GIF = "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=="  # 1x1 saydam GIF (base64)
REDACTED = "<redacted>"
CONNECT_SECRET_KEYS = ("auth_token", "jwt", "sig", "nkey", "pass", "user", "token")
INFO_SECRET_KEYS = ("client_ip",)
# FX-29c: istemcinin konumunu taşıyan anahtarlar. Sayfa `/api/v1/country/alpha2` çağırır; gövdesi istemcinin IP'si,
# şehri, bölgesi ve TLS parmak izini (`f`) taşır. Bu anahtarların değeri her derinlikte maskelenir, anahtar kalır.
GEO_KEYS = frozenset({"ip", "city", "region_code", "f", "client_ip", "postal", "latitude", "longitude", "lat", "lon",
                      "geo"})
# Maskelenmeyen alt ağaçlar: `venue` stadyumun herkese açık bilgisidir (city {name, country}, venueCoordinates),
# izleyicinin konumu değildir; araştırma verisi ve fikstürler bunlara dayanır.
GEO_KEEP_UNDER = frozenset({"venue"})
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
IPV4_RE = re.compile(rf"(?<!\d)(?<!\d\.){_OCTET}(?:\.{_OCTET}){{3}}(?!\d|\.\d)")
# IPv6 adayı: en az iki ':' içeren onaltılık dizi (sonda gömülü IPv4 olabilir); ipaddress ile doğrulanır, böylece
# saat (12:30:45) gibi dizgiler maskelenmez
IPV6_CANDIDATE_RE = re.compile(r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}(?:(?:\d{1,3}\.){3}\d{1,3}|[0-9A-Fa-f]{1,4})?"
                               r"(?:%[\w.-]+)?(?![\w:])")

# Kesicinin kararları: gönder (sayılmaz), gönder (API, sayıldı), boş görsel, iptal (engel), iptal (boşta),
# yanıtlanmaz (istek beklerken öldü), öz denetim (yerelde yanıtlanır)
SEND, SEND_API, BLANK, BLOCK, DROP, GONE, PROBE = "send", "send-api", "blank", "block", "drop", "gone", "probe"
# Öz denetim isteğinin adresi: .invalid hiçbir zaman çözülmez (RFC 6761), kesici çalışmasa da istek makineden çıkmaz
PROBE_HOST = "explorer-probe.invalid"
PROBE_JS = """async (url) => {
    try {
        const r = await fetch(url, {cache: "no-store"});
        return "ok:" + await r.text();
    } catch (e) {
        return "error:" + e;
    }
}"""
# Yanıt satırı için ham başlık ve gövde okumasının süre sınırı (sn)
RESPONSE_READ_TIMEOUT = 15.0
# Network.requestWillBeSent'ten (çerçeve, belge) bilgisi tutulan, henüz durdurulmamış istek sayısı üst sınırı
ORIGINS_KEPT = 2000


class Held:
    """Sıra bekleyen SofaScore isteği. Chromium aynı isteği yeniden durdurursa `rid` en yeni kimliktir."""

    __slots__ = ("cdp", "rid", "url", "network_id", "frame_id", "loader_id", "paused_at", "gone")

    def __init__(self, cdp: Any, rid: str, url: str, network_id: Optional[str], frame_id: Optional[str],
                 loader_id: Optional[str]) -> None:
        self.cdp = cdp
        self.rid = rid
        self.url = url
        self.network_id = network_id
        self.frame_id = frame_id
        self.loader_id = loader_id
        self.paused_at = time.time()
        self.gone: Optional[str] = None  # olayların bildirdiği ölüm nedeni; karar _alive denetimidir


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:120] or "root"


def trim(o: Any, list_keep: int = 3) -> Any:
    """Büyük yanıt: listeler ilk `list_keep` öğeye kırpılır, kırpıldığı `__trimmed__` ile işaretlenir."""
    if isinstance(o, dict):
        return {k: trim(v, list_keep) for k, v in o.items()}
    if isinstance(o, list):
        kept = [trim(v, list_keep) for v in o[:list_keep]]
        if len(o) > list_keep:
            kept.append({"__trimmed__": len(o) - list_keep})
        return kept
    return o


def response_keys(body: Any) -> List[str]:
    if isinstance(body, dict):
        return sorted(body.keys())
    if isinstance(body, list):
        return ["<list>"]
    return []


def find_events(o: Any, out: List[Dict[str, Any]]) -> None:
    """Yanıttaki etkinlik nesneleri: status{type|code} + id + (startTimestamp | homeScore)."""
    if isinstance(o, dict):
        st = o.get("status")
        if isinstance(st, dict) and ("code" in st or "type" in st) and "id" in o and (
            "startTimestamp" in o or "homeScore" in o
        ):
            out.append(o)
        for v in o.values():
            find_events(v, out)
    elif isinstance(o, list):
        for v in o:
            find_events(v, out)


def compact_event(e: Dict[str, Any]) -> Dict[str, Any]:
    t = e.get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    cat = t.get("category") or {}
    return {
        "id": e.get("id"),
        "sport": ((cat.get("sport") or {}).get("slug")) or (e.get("sport") or {}).get("slug"),
        "unique_tournament": {"id": ut.get("id"), "name": ut.get("name")},
        "tournament": t.get("name"),
        "category": cat.get("name"),
        "status": e.get("status"),
        "homeScore": e.get("homeScore"),
        "awayScore": e.get("awayScore"),
        "time": e.get("time"),
        "winnerCode": e.get("winnerCode"),
        "changes": e.get("changes"),
        "startTimestamp": e.get("startTimestamp"),
        "defaultPeriodCount": e.get("defaultPeriodCount"),
        "defaultPeriodLength": e.get("defaultPeriodLength"),
        "top_keys": sorted(e.keys()),
    }


def safe_url(url: str, sofa_host: Pattern[str] = SOFA_HOST) -> str:
    """
    Kayda yazılacak adres. SofaScore adresi olduğu gibi; üçüncü taraf adresi yalnızca şema + alan adı + ilk yol
    parçası (Sportradar LMT gibi adresler imzalı token taşır; sorgu ve kalan yol yazılmaz). Alan adı olmayan
    (data:, blob:, about:) adreslerden yalnızca şema.
    """
    u = urlparse(url or "")
    host = u.hostname or ""
    if not host:
        return f"{u.scheme}:" if u.scheme else ""
    if sofa_host.search(host):
        return url
    first = u.path.strip("/").split("/")[0]
    return f"{u.scheme}://{host}/{first}"


def _origin(req: Any, sofa_host: Pattern[str] = SOFA_HOST) -> str:
    """İsteği yapan: sayfa çerçevesinin URL'si (üçüncü taraf çerçevede maskeli) ya da service worker."""
    try:
        return safe_url(req.frame.url, sofa_host)[:200]
    except Exception:
        return "service-worker"


def is_explorer_drop(failure: str) -> bool:
    """Bu script'in iptali: ilk sürüm varsayılan koddu (ERR_FAILED), sonra ERR_BLOCKED_BY_CLIENT (eski kayıtlar için)."""
    return "ERR_BLOCKED_BY_CLIENT" in failure or "ERR_FAILED" in failure


def nats_messages(raw: bytes):
    """NATS metin protokolü: (komut, konu, gövde) üçlüleri. MSG/HMSG gövdesi bayt uzunluğuyla okunur."""
    i = 0
    while i < len(raw):
        j = raw.find(b"\r\n", i)
        if j < 0:
            break
        line = raw[i:j].decode("utf-8", errors="replace")
        i = j + 2
        parts = line.split(" ")
        cmd = parts[0]
        if cmd in ("MSG", "HMSG") and len(parts) >= 4 and parts[-1].isdigit():
            size = int(parts[-1])
            body = raw[i:i + size].decode("utf-8", errors="replace")
            i += size + 2
            yield cmd, parts[1], body
        elif cmd == "INFO":
            yield cmd, "", line[5:]
        else:
            yield cmd, "", line


def _mask_v6(m: "re.Match[str]") -> str:
    text = m.group()
    if not re.search(r"[0-9A-Fa-f]", text):
        return text
    try:
        ipaddress.IPv6Address(text.split("%", 1)[0])
    except ValueError:
        return text
    return REDACTED


def mask_ips(text: str) -> str:
    """Metindeki her IPv4/IPv6 adresi (port ve köşeli parantez kalır) `<redacted>` olur."""
    if ":" in text:
        text = IPV6_CANDIDATE_RE.sub(_mask_v6, text)
    if "." in text:
        text = IPV4_RE.sub(REDACTED, text)
    return text


def redact(o: Any, _keep_geo: bool = False) -> Any:
    """
    FX-29c: diske yazılacak her şey (örnek gövdesi, olay, istek/sayfa/ws satırı) buradan geçer. GEO_KEYS'teki
    anahtarların değeri her derinlikte `<redacted>` olur (anahtar kalır; None dokunulmaz); `venue` altı hariç.
    Her dizgideki (anahtarlar dahil) IP adresleri maskelenir.
    """
    if isinstance(o, dict):
        out: Dict[Any, Any] = {}
        for k, v in o.items():
            key = mask_ips(k) if isinstance(k, str) else k
            if k in GEO_KEYS and not _keep_geo and v is not None:
                out[key] = REDACTED
            else:
                out[key] = redact(v, _keep_geo or k in GEO_KEEP_UNDER)
        return out
    if isinstance(o, list):
        return [redact(v, _keep_geo) for v in o]
    if isinstance(o, str):
        return mask_ips(o)
    return o


def mask_info(body: str) -> str:
    """
    NATS INFO gövdesi: istemcinin IP adresi (client_ip) ve her alandaki IPv4/IPv6 adresi (connect_urls, host, ip ...;
    push sunucularının adresleri) maskelenir, kalanı korunur; çözülemeyen gövde hiç yazılmaz.
    """
    try:
        info = json.loads(body)
    except ValueError:
        return "<unparsed, redacted>"
    if not isinstance(info, dict):
        return "<unparsed, redacted>"
    for k in INFO_SECRET_KEYS:
        if k in info:
            info[k] = REDACTED
    return json.dumps(redact(info))


def mask_connect(line: str) -> str:
    """NATS CONNECT satırı: kimlik alanları (kullanıcı, parola, token, imza) maskelenir."""
    try:
        opts = json.loads(line[len("CONNECT "):])
    except ValueError:
        return "CONNECT <unparsed, redacted>"
    if not isinstance(opts, dict):
        return "CONNECT <unparsed, redacted>"
    for k in list(opts):
        if k in CONNECT_SECRET_KEYS:
            opts[k] = REDACTED
    return "CONNECT " + json.dumps(opts)


class Explorer:
    def __init__(self, out: str = OUT, *, new_run: bool = False, run_id: Optional[str] = None,
                 max_requests: int = MAX_API_REQUESTS, max_seconds: float = MAX_SECONDS,
                 sofa_host: Pattern[str] = SOFA_HOST, quiet_url: str = QUIET_URL, bridge: Any = None) -> None:
        """
        out          kayıt klasörü
        new_run      yeni koşu: bütçe sıfırdan, önceki koşunun durumu previous_runs'a
        run_id       yeni koşunun kimliği (yoksa UTC zaman damgası); sürdürülen koşuda verilirse onunla aynı olmalı
        sofa_host    SofaScore sayılan alan adları (testler sahte alan adı verir)
        quiet_url    köprünün açılışta ve captcha sonrasında açtığı, API çağırmayan sayfa
        bridge       BrowserBridge (testler sahtesini verir)
        """
        self.out = out
        self.bridge = bridge if bridge is not None else cs.BrowserBridge.get_instance()
        self.sofa_host = sofa_host
        self.quiet_url = quiet_url
        self.max_requests = max_requests
        self.max_seconds = max_seconds
        self.page: Any = None
        self.cdp: Any = None
        self.sport = "unknown"
        self.page_url = ""
        self.api_count = 0
        self.blocked = 0
        self.idle_dropped = 0
        self.intercept_errors = 0
        self.gone = 0  # sıra beklerken ölen (sayfası değişen ya da sayfada iptal edilen) istekler: gönderilmedi
        self.restarts = 0  # Chromium'un aynı isteği yeniden durdurması (aynı networkId): sırası korundu
        # Sıra bekleyen istekler (networkId → Held); çerçevelerin güncel belgesi (frameId → loaderId); yerine
        # yenisi gelmiş belgeler (loaderId); henüz durdurulmamış isteklerin (çerçeve, belge) bilgisi
        self._waiting: Dict[str, Held] = {}
        self._documents: Dict[str, str] = {}
        self._replaced: "OrderedDict[str, None]" = OrderedDict()
        self._origins: "OrderedDict[str, tuple]" = OrderedDict()
        self._probes: Dict[int, Dict[str, Any]] = {}
        self._step_base = (0, 0, 0)
        self.started = time.time()
        self.last_nav = 0.0
        self.last_api = 0.0
        self.samples: Dict[str, int] = {}
        self.ws_frames: Dict[str, int] = {}
        self.ws_subjects: Dict[str, Dict[str, Any]] = {}
        self.page_type = ""
        self.active = False  # yalnızca bir komut sürerken SofaScore isteklerine izin verilir
        self.known: set = set()
        self.new_patterns: List[str] = []
        self.run_id: Optional[str] = None
        self.previous_runs: List[Dict[str, Any]] = []
        # Olay işleyicilerinden açılan görevler: döngü görevlere yalnızca zayıf başvuru tutar, burada güçlü tutulur
        self._tasks: set = set()
        # Bu süreçteki istekler kilit dosyasına teker teker gider (sıra FIFO, iş parçacığı havuzu dolmaz)
        self._slot_lock = asyncio.Lock()
        self._solving = False
        os.makedirs(out, exist_ok=True)
        self._load_state(new_run, run_id)

    # --- bütçe ---------------------------------------------------------------------------

    def _state_path(self) -> str:
        return os.path.join(self.out, "_state.json")

    def _load_state(self, new_run: bool, run_id: Optional[str]) -> None:
        prev: Dict[str, Any] = {}
        if os.path.exists(self._state_path()):
            with open(self._state_path(), encoding="utf-8") as f:
                prev = json.load(f)
        self.previous_runs = list(prev.pop("previous_runs", None) or [])
        fresh = new_run or not prev
        if fresh:
            # Yeni koşu: önceki koşunun durumu olduğu gibi saklanır (2026-10-01 koşusunun kimliği yoktur)
            if prev:
                self.previous_runs.append(prev)
            self.run_id = run_id or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            if self.run_id in {r.get("run_id") for r in self.previous_runs}:
                raise ValueError(f"run id {self.run_id!r} was used by an earlier run")
            self.started = time.time()
            self.api_count = 0
        else:
            self.run_id = prev.get("run_id")
            if run_id is not None and run_id != self.run_id:
                raise ValueError(f"the current run is {self.run_id!r}, not {run_id!r}; pass --new-run to start a run")
            self.started = prev.get("started", self.started)
            self.api_count = prev.get("api_requests", 0)
        req_log = os.path.join(self.out, "requests.jsonl")
        logged = 0
        if os.path.exists(req_log):
            with open(req_log, encoding="utf-8") as f:
                for line in f:
                    row = json.loads(line)
                    # İlk sürümün kendi iptalleri ERR_FAILED ile yazılmıştı (run_id'siz satırlar); yeni satırlarda
                    # ERR_FAILED gerçek bir ağ hatasıdır
                    if row.get("status") is None and "run_id" not in row and is_explorer_drop(row.get("failure") or ""):
                        continue
                    self.known.add(api_pattern(row["url"], self.sofa_host))
                    if row.get("run_id") != self.run_id:
                        continue
                    if self.sofa_host.search(row.get("host") or urlparse(row["url"]).hostname or ""):
                        logged += 1
        # Bütçe: bu koşunun kayıtlı SofaScore satırları en az gönderilen istek kadardır (yarıda kalan komut durumu
        # yazmamış olabilir)
        self.api_count = max(self.api_count, logged)
        for p in glob.glob(os.path.join(self.out, "samples", "*", "*.json")):
            key = os.path.basename(os.path.dirname(p)) + "|" + os.path.basename(p).rsplit("__", 1)[0]
            self.samples[key] = self.samples.get(key, 0) + 1
        if fresh:  # yeni koşu hemen yazılır; sürdürülen koşunun durumu ilk komutta (bütçesi bitmişse hiç) yazılır
            self._save_state()

    def _save_state(self) -> None:
        rc.write_json(self._state_path(), {
            "run_id": self.run_id,
            "started": self.started,
            "api_requests": self.api_count,
            "max_requests": self.max_requests,
            "max_seconds": self.max_seconds,
            "updated": utc_now(),
            "previous_runs": self.previous_runs,
        })

    def over_budget(self) -> bool:
        return self.api_count >= self.max_requests or time.time() - self.started >= self.max_seconds

    def stats(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "api_requests": self.api_count,
            "blocked": self.blocked,
            "idle_dropped": self.idle_dropped,
            "intercept_errors": self.intercept_errors,
            "gone": self.gone,
            "restarts": self.restarts,
            "elapsed_min": round((time.time() - self.started) / 60, 1),
            "over_budget": self.over_budget(),
        }

    # --- kayıt -----------------------------------------------------------------------------

    def _append(self, name: str, row: Dict[str, Any]) -> None:
        with open(os.path.join(self.out, name), "a", encoding="utf-8") as f:
            f.write(json.dumps(redact({**row, "run_id": self.run_id}), ensure_ascii=False) + "\n")

    def _safe(self, url: str) -> str:
        return safe_url(url, self.sofa_host)

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> "asyncio.Future[Any]":
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # --- kesici (CDP Fetch) ------------------------------------------------------------------

    async def intercept(self, page: Any) -> None:
        """
        Sayfanın görsel/medya/font ve XHR/fetch/EventSource isteklerini CDP Fetch alanında durdurur; her biri
        on_paused'da en fazla bir kez yanıtlanır (devam, boş görsel ya da iptal; beklerken ölen istek yanıtlanmaz).
        Network.enable service worker'ı atlamak (setBypassServiceWorker onsuz etkisizdir) ve sıradaki isteğin
        hangi belgeden geldiğini (loaderId) ya da sayfada iptal edildiğini görmek için; Page.enable belge
        değişimini (frameNavigated) görmek için. Tamponlar küçük tutulur.
        """
        cdp = await page.context.new_cdp_session(page)
        cdp.on("Fetch.requestPaused", lambda ev: self._spawn(self.on_paused(cdp, ev)))
        cdp.on("Network.requestWillBeSent", self.on_request_will_be_sent)
        cdp.on("Network.loadingFailed", self.on_loading_failed)
        cdp.on("Page.frameNavigated", self.on_frame_navigated)
        cdp.on("Page.frameDetached", self.on_frame_detached)
        await cdp.send("Network.enable", {"maxTotalBufferSize": 1_000_000, "maxResourceBufferSize": 100_000})
        await cdp.send("Network.setBypassServiceWorker", {"bypass": True})
        await cdp.send("Page.enable")
        await cdp.send("Fetch.enable", {"patterns": [
            {"urlPattern": "*", "resourceType": t, "requestStage": "Request"} for t in CDP_TYPES
        ]})
        self.cdp = cdp

    # Sıradaki isteğin yaşamı. Chromium, belgesi değişen (yeni belge, kalkan çerçeve) isteği sessizce bırakır:
    # ne sayfaya ne Network olaylarına hata düşer, continueRequest yalnızca "Invalid InterceptionId" döner. Karar
    # sıra alınmadan hemen önce ve sıradan sonra verilir (_alive): ölü istek sıra almaz, yanıtlanmaz, gönderilmez.
    # Olaylar (belge değişimi, çerçevenin kalkması, sayfanın iptali) yalnızca ölümün nedenini kaydetmek içindir.

    def _is_api(self, url: str, resource_type: str) -> bool:
        return resource_type in TRAFFIC_TYPES and bool(self.sofa_host.search(urlparse(url).hostname or ""))

    def on_request_will_be_sent(self, ev: Dict[str, Any]) -> None:
        if CDP_TYPES.get(ev.get("type") or "") not in TRAFFIC_TYPES:
            return  # görsel seli vb.: tutulmaz
        nid = ev.get("requestId")
        origin = (ev.get("frameId"), ev.get("loaderId"))
        held = self._waiting.get(nid) if nid else None
        if held is not None:
            held.frame_id, held.loader_id = origin
            self._check_document(held)
        elif nid and ev.get("redirectResponse") is None:
            self._origins[nid] = origin
            while len(self._origins) > ORIGINS_KEPT:
                self._origins.popitem(last=False)

    def on_loading_failed(self, ev: Dict[str, Any]) -> None:
        nid = ev.get("requestId")
        self._origins.pop(nid, None)
        held = self._waiting.get(nid) if nid else None
        if held is not None:  # sayfa isteği sıradayken iptal etti
            reason = ev.get("errorText") or "failed"
            if ev.get("blockedReason"):
                reason += f" ({ev['blockedReason']})"
            self._mark_gone(held, f"failed while queued: {reason}")

    def on_frame_navigated(self, ev: Dict[str, Any]) -> None:
        frame = ev.get("frame") or {}
        fid, lid = frame.get("id"), frame.get("loaderId")
        if not fid or not lid:
            return
        old = self._documents.get(fid)
        self._documents[fid] = lid
        if old and old != lid:
            self._replaced[old] = None
            while len(self._replaced) > ORIGINS_KEPT:
                self._replaced.popitem(last=False)
            for held in list(self._waiting.values()):
                self._check_document(held)

    def on_frame_detached(self, ev: Dict[str, Any]) -> None:
        fid = ev.get("frameId")
        self._documents.pop(fid, None)
        for held in list(self._waiting.values()):
            if held.frame_id == fid:
                self._mark_gone(held, "its frame was detached")

    def _check_document(self, held: Held) -> None:
        # Yalnızca yerine yenisi gelmiş belge "eski"dir: henüz frameNavigated'ı işlenmemiş yeni belgenin isteği
        # (sıralama yarışı) bilinmeyen loaderId taşır ve beklemeye devam eder
        if held.loader_id and held.loader_id in self._replaced:
            self._mark_gone(held, "the page loaded a new document")

    def _mark_gone(self, held: Held, reason: str) -> None:
        if held.gone is None:
            held.gone = reason

    @staticmethod
    async def _alive(cdp: Any, rid: str) -> bool:
        """
        Bekletilen istek Chromium'da hâlâ duruyor mu? Request aşamasındaki canlı istekte Fetch.getResponseBody yan
        etkisiz bir hata verir ("Can only get response body on HeadersReceived pattern matched requests"), ölmüş
        istekte "Invalid InterceptionId". Başka her durumda canlı sayılır (gönderim denenir, eski davranış).
        """
        try:
            await cdp.send("Fetch.getResponseBody", {"requestId": rid})
        except Exception as e:
            return "Invalid InterceptionId" not in str(e)
        return True

    def _log_gone(self, held: Held, slot_used: bool) -> None:
        self.gone += 1
        self._append("pages.jsonl", {"ts": round(time.time(), 3), "op": "request-gone", "url": self._safe(held.url)[:200],
                                     "reason": held.gone, "waited_s": round(time.time() - held.paused_at, 2),
                                     "slot_used": slot_used})

    async def _take_slot(self, held: Optional[Held] = None) -> str:
        """
        Kilitten sıra alır (bu süreçteki istekler FIFO): "slot". Sırası gelen istek bu arada öldüyse ("gone") ya da
        komut bittiyse ("idle") sıra alınmaz: ölü ya da düşürülecek istek kimsenin aralığını yemez. Sıra beklenirken
        ölen istek "gone-after-slot" (sıra kullanıldı, istek gönderilmez).
        """
        async with self._slot_lock:
            if held is not None:
                if not await self._alive(held.cdp, held.rid):
                    self._mark_gone(held, "dropped by Chromium before its turn")
                    return "gone"
                if not self.active:
                    return "idle"
            await asyncio.to_thread(rc._wait_rate_slot)
        if held is not None and not await self._alive(held.cdp, held.rid):
            self._mark_gone(held, "dropped by Chromium while it waited for its slot")
            return "gone-after-slot"
        return "slot"

    async def gate(self, url: str, resource_type: str, held: Optional[Held] = None) -> str:
        """Durdurulan isteğin kararı. SofaScore API isteği ancak kilitten sıra alınca ve sayılınca gönderilir."""
        if resource_type in BLOCKED_TYPES:
            self.blocked += 1
            return BLANK if resource_type == "image" else BLOCK
        host = urlparse(url).hostname or ""
        if host == PROBE_HOST:
            return PROBE
        if resource_type not in TRAFFIC_TYPES or not self.sofa_host.search(host):
            return SEND
        # Komutlar arasında sayfa kendi kendine sorgulamaya devam eder (canlı yenileme, tembel yükleme):
        # bu istekler gönderilmez; bütçe yalnızca yönlendirilen adımlara harcanır.
        if not self.active:
            self.idle_dropped += 1
            return DROP
        if self.over_budget():
            self.blocked += 1
            return BLOCK
        took = await self._take_slot(held)
        if took in ("gone", "gone-after-slot"):  # sıra beklerken sayfası değişti ya da sayfa iptal etti: gönderilmez
            self._log_gone(held, slot_used=took == "gone-after-slot")
            return GONE
        if took != "slot" or not self.active:  # sıra beklerken komut bitti: gönderme
            self.idle_dropped += 1
            return DROP
        if self.over_budget():  # sırada bekleyenler bütçeyi aşmasın
            self.blocked += 1
            return BLOCK
        self.api_count += 1
        self.last_api = time.time()
        if self.api_count % 10 == 0:
            self._save_state()
        return SEND_API

    async def on_paused(self, cdp: Any, ev: Dict[str, Any]) -> None:
        """Fetch.requestPaused: karar verilir ve istek yanıtlanır. Hata da olsa istek askıda bırakılmaz."""
        rid = ev["requestId"]
        url = (ev.get("request") or {}).get("url") or ""
        resource_type = CDP_TYPES.get(ev.get("resourceType") or "", "other")
        nid = ev.get("networkId")
        waiting = self._waiting.get(nid) if nid else None
        if waiting is not None:
            # Chromium sıradaki isteği yeniden durdurdu (aynı istek, yeni kimlik; eskisi geçersiz): sırası korunur,
            # sırası gelince en yeni kimlik yanıtlanır; ikinci bir sıra ya da sayım yok
            waiting.rid = rid
            self.restarts += 1
            return
        held = None
        if not ev.get("redirectedRequestId") and self._is_api(url, resource_type):
            # networkId'siz istek de olur (sayfanın denetçisi görmemiş: kapanan belgenin son istekleri gibi);
            # canlılık denetimi onun için de yapılır, yalnızca yeniden durdurma eşleştirmesi networkId ister
            frame_id, loader_id = self._origins.pop(nid, (ev.get("frameId"), None)) if nid else (ev.get("frameId"), None)
            held = Held(cdp, rid, url, nid, frame_id or ev.get("frameId"), loader_id)
            if nid:
                self._waiting[nid] = held
            self._check_document(held)
        try:
            verdict = await self.gate(url, resource_type, held)
        except Exception as e:  # karar verilemedi (kilit dosyası vb.): istek gönderilmez
            verdict = BLOCK
            self._intercept_error(url, "gate", e)
        finally:
            if held is not None and nid and self._waiting.get(nid) is held:
                del self._waiting[nid]
        if verdict == GONE:
            return
        if held is not None:
            rid = held.rid
        if verdict == PROBE:
            await self._answer_probe(cdp, rid, url)
            return
        try:
            if verdict in (SEND, SEND_API):
                await cdp.send("Fetch.continueRequest", {"requestId": rid})
            elif verdict == BLANK:
                await cdp.send("Fetch.fulfillRequest", {
                    "requestId": rid, "responseCode": 200, "body": BLANK_GIF,
                    "responseHeaders": [{"name": "Content-Type", "value": "image/gif"},
                                        {"name": "Cache-Control", "value": "no-store"}],
                })
            else:
                await cdp.send("Fetch.failRequest", {"requestId": rid, "errorReason": "BlockedByClient"})
        except Exception as e:  # istek bu arada iptal edildi (sayfa değişti) ya da oturum kapandı
            if held is not None and verdict in (DROP, BLOCK) and "Invalid InterceptionId" in str(e):
                # Zaten gönderilmeyecek istek o arada ölmüş: kesici hatası değil
                self._mark_gone(held, "dropped by Chromium before it was answered")
                self._log_gone(held, slot_used=False)
                return
            if verdict == SEND_API:
                self.api_count -= 1  # gönderilemedi: bütçeden düşülür (sıra yine de kullanıldı)
            self._intercept_error(url, verdict, e)

    async def _answer_probe(self, cdp: Any, rid: str, url: str) -> None:
        """Öz denetim isteği: `hold` sn tutulur, yerelde yanıtlanır (ağa gitmez, sayılmaz, adımdan bağımsız)."""
        try:
            n = int((parse_qs(urlparse(url).query).get("n") or ["0"])[0])
        except ValueError:
            n = 0
        state = self._probes.setdefault(n, {"hold": 0.0})
        state["paused"] = True
        await asyncio.sleep(float(state.get("hold") or 0.0))
        state["held_s"] = round(float(state.get("hold") or 0.0), 2)
        try:
            await cdp.send("Fetch.fulfillRequest", {
                "requestId": rid, "responseCode": 200,
                "body": base64.b64encode(json.dumps({"probe": n}).encode()).decode(),
                "responseHeaders": [{"name": "Content-Type", "value": "application/json"},
                                    {"name": "Access-Control-Allow-Origin", "value": "*"},
                                    {"name": "Cache-Control", "value": "no-store"}],
            })
            state["answered"] = True
        except Exception as e:
            state["answered"] = False
            state["error"] = f"{e.__class__.__name__}: {str(e)[:200]}"

    def _intercept_error(self, url: str, stage: str, e: Exception) -> None:
        self.intercept_errors += 1
        self._append("pages.jsonl", {"ts": round(time.time(), 3), "op": "intercept-error", "stage": stage,
                                     "url": self._safe(url)[:200], "error": f"{e.__class__.__name__}: {str(e)[:200]}"})

    def on_page(self, pg: Any) -> None:
        """Keşif sayfası dışında açılan sayfa (açılır pencere) kesicisizdir: kapatılır. Captcha çözümü hariç."""
        if pg is self.page or self._solving:
            return
        self._spawn(self._close_page(pg, "close-popup"))

    async def _close_page(self, pg: Any, op: str) -> None:
        self._append("pages.jsonl", {"ts": round(time.time(), 3), "op": op, "url": self._safe(pg.url)})
        try:
            await pg.close()
        except Exception:
            pass

    async def _solve_challenge(self) -> Optional[str]:
        """
        Captcha çözümü köprünün (Scrapling'in) kendi sayfasında olur; bu sayfada kesici yoktur. Çözümün tek
        SofaScore isteği (/api/v1/token/captcha) için önce sıra alınır ve bütçeye sayılır; bitince bir sıra daha
        alınır ki sonraki istek çözümden en az MIN_REQUEST_GAP sonra gitsin.
        """
        await self._take_slot()
        self.api_count += 1
        self._solving = True
        try:
            return await self.bridge._solve_on_captcha_page()
        finally:
            self._solving = False
            await self._take_slot()

    def _note_pattern(self, pattern: str) -> None:
        if pattern not in self.known:
            self.known.add(pattern)
            self.new_patterns.append(pattern)

    def on_request_failed(self, req: Any) -> None:
        failure = str(req.failure or "")
        if req.resource_type not in TRAFFIC_TYPES or "ERR_BLOCKED_BY_CLIENT" in failure:
            return  # bu script'in gönderilmeden iptal ettiği istek: trafik değil
        url = req.url
        pattern = api_pattern(url, self.sofa_host)
        self._note_pattern(pattern)
        self._append("requests.jsonl", {
            "ts": round(time.time(), 3), "sport": self.sport, "page": self.page_url, "page_type": self.page_type,
            "url": self._safe(url), "host": urlparse(url).hostname, "method": req.method,
            "resource_type": req.resource_type, "origin": _origin(req, self.sofa_host), "pattern": pattern,
            "status": None, "failure": failure[:200] or None, "cache_control": None,
            "response_keys": [], "sample_file": None,
        })

    async def on_response(self, resp: Any) -> None:
        """
        Yanıt satırı her durumda yazılır: adres, kod ve başlıklar nesnenin ilk verisindedir; gövde okunamazsa
        (nesne toplanmış, sayfa kapanmış) satır `body_error` ile yazılır. Gövde yalnızca SofaScore yanıtlarında okunur.
        """
        req = resp.request
        if req.resource_type not in TRAFFIC_TYPES:
            return
        url = resp.url
        host = urlparse(url).hostname or ""
        sofa = bool(self.sofa_host.search(host))
        if sofa:
            self.last_api = time.time()
        pattern = api_pattern(url, self.sofa_host)
        self._note_pattern(pattern)
        headers: Dict[str, str] = dict(resp.headers or {})
        body: Any = None
        body_error: Optional[str] = None
        if sofa:
            # Okumalar süre sınırlıdır: sayfa yanıt gelirken başka belgeye geçerse gövde/ham başlık hiç gelmeyebilir
            # ve bekleyen okuma satırı sonsuza dek yazdırmazdı
            try:
                headers = await asyncio.wait_for(resp.all_headers(), RESPONSE_READ_TIMEOUT)
            except Exception:
                pass
            try:
                body = json.loads(await asyncio.wait_for(resp.body(), RESPONSE_READ_TIMEOUT))
            except ValueError:  # JSON değil
                body = None
            except Exception as e:
                body_error = f"{e.__class__.__name__}: {str(e)[:160]}"
        if body is not None:
            # FX-29c: örnekten, olaylardan ve satırdan önce (ör. country/alpha2 istemcinin IP'sini ve şehrini taşır)
            body = redact(body)
        sample_file = None
        try:
            sample_file = self._sample(url, resp.status, headers, pattern, body)
        except OSError as e:
            body_error = body_error or f"sample: {e}"
        row = {
            "ts": round(time.time(), 3),
            "sport": self.sport,
            "page": self.page_url,
            "page_type": self.page_type,
            "url": self._safe(url),
            "host": host,
            "method": req.method,
            "resource_type": req.resource_type,
            "origin": _origin(req, self.sofa_host),
            "pattern": pattern,
            "status": resp.status,
            "age": headers.get("age"),
            "cache_control": headers.get("cache-control"),
            "response_keys": response_keys(body),
            "sample_file": sample_file,
        }
        if body_error:
            row["body_error"] = body_error
        self._append("requests.jsonl", row)
        if body is not None:
            found: List[Dict[str, Any]] = []
            find_events(body, found)
            if found:
                os.makedirs(os.path.join(self.out, "events"), exist_ok=True)
                with open(os.path.join(self.out, "events", f"{self.sport}.jsonl"), "a", encoding="utf-8") as f:
                    for e in found:
                        row = compact_event(e)
                        row["source_pattern"] = pattern
                        row["seen_at"] = round(time.time())
                        row["run_id"] = self.run_id
                        f.write(json.dumps(redact(row), ensure_ascii=False) + "\n")

    def _sample(self, url: str, status: int, headers: Dict[str, str], pattern: str, body: Any) -> Optional[str]:
        """Pattern başına en fazla SAMPLES_PER_PATTERN örnek (yalnızca SofaScore gövdeleri okunur)."""
        key = f"{self.sport}|{slug(pattern)}"
        if body is None or self.samples.get(key, 0) >= SAMPLES_PER_PATTERN:
            return None
        n = self.samples.get(key, 0) + 1
        self.samples[key] = n
        raw = json.dumps(body, ensure_ascii=False)
        trimmed = len(raw.encode("utf-8")) > FULL_SAMPLE_BYTES
        rel = os.path.join("samples", self.sport, f"{slug(pattern)}__{n}.json")
        rc.write_json(
            os.path.join(self.out, rel),
            redact({
                "url": url,
                "status": status,
                "fetched_at_utc": utc_now(),
                "page": self.page_url,
                "cache_control": headers.get("cache-control"),
                "trimmed": trimmed,
                "run_id": self.run_id,
                "body": trim(body) if trimmed else body,
            }),
        )
        return os.path.relpath(os.path.join(self.out, rel), ROOT).replace(os.sep, "/")

    def on_websocket(self, ws: Any) -> None:
        """
        Push kanalı (ws.sofascore.com:9222 NATS). Alınan: INFO (ilk; client_ip ve tüm IP adresleri maskeli), her
        konudan ilk 3 MSG ve `status.*` taşıyan tüm MSG'ler; konu başına sayaç + anahtar kümesi ws_subjects.json'da.
        Gönderilen: SUB/UNSUB hepsi; CONNECT'in kimlik alanları maskelenir; PUB gövdeleri (analitik) saklanmaz.
        Saklanan her kare metninde IP adresleri maskelenir (FX-29c).
        """
        ws_url = self._safe(ws.url)
        opened = {"ts": round(time.time(), 3), "sport": self.sport, "page": self.page_url,
                  "page_type": self.page_type, "url": ws_url}
        self._append("ws.jsonl", {"kind": "open", **opened})
        self._note_pattern("ws:" + api_pattern(ws.url, self.sofa_host))
        self.ws_frames[ws_url] = 0

        def raw_of(payload: Any) -> bytes:
            return payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)

        def received(payload: Any) -> None:
            self.ws_frames[ws_url] = self.ws_frames.get(ws_url, 0) + 1
            for kind, subject, body in nats_messages(raw_of(payload)):
                base = {"ts": round(time.time(), 3), "sport": self.sport, "page_type": self.page_type, "url": ws_url}
                if kind == "INFO":
                    if self.ws_frames[ws_url] <= 2:
                        self._append("ws.jsonl", {"kind": "recv", "op": "INFO", **base, "body": mask_info(body)[:1500]})
                    continue
                if kind not in ("MSG", "HMSG"):
                    continue
                st = self.ws_subjects.setdefault(subject, {"count": 0, "keys": {}, "first_ts": base["ts"]})
                st["count"] += 1
                try:
                    obj = json.loads(body)
                except ValueError:
                    obj = None
                if isinstance(obj, dict):
                    for k in obj:
                        st["keys"][k] = st["keys"].get(k, 0) + 1
                has_status = isinstance(obj, dict) and any(k.startswith("status.") for k in obj)
                if st["count"] <= 3 or has_status:
                    self._append("ws.jsonl", {"kind": "recv", "op": kind, "subject": subject, **base,
                                              "body": redact(obj) if obj is not None else mask_ips(body)[:1500]})

        def sent(payload: Any) -> None:
            for line in raw_of(payload).decode("utf-8", errors="replace").split("\r\n"):
                # Yalnızca komut satırları: PUB/HPUB gövdeleri (sitenin analitik olayları) saklanmaz
                if not line.startswith(("SUB ", "UNSUB ", "CONNECT ", "PUB ", "HPUB ")):
                    continue
                if line.startswith("CONNECT "):
                    line = mask_connect(line)
                line = mask_ips(line)
                self._append("ws.jsonl", {"kind": "sent", "ts": round(time.time(), 3), "sport": self.sport,
                                          "page_type": self.page_type, "page": self.page_url, "line": line[:500]})

        def closed(_: Any = None) -> None:
            self._append("ws.jsonl", {"kind": "close", "ts": round(time.time(), 3), "url": ws_url,
                                      "frames": self.ws_frames.get(ws_url, 0)})

        ws.on("framereceived", received)
        ws.on("framesent", sent)
        ws.on("close", closed)

    # --- komutlar --------------------------------------------------------------------------

    async def start(self) -> None:
        # Köprü açılışta ana sayfayı (ve captcha çözümünden sonra yönlendirmeyi) yükler; bu, kesici kurulmadan
        # olur ve sitenin onlarca isteği kilitsiz ve sayılmadan gider. Bu süreçte iki adres de API çağırmayan
        # bir sayfaya çevrilir (sofascore_scraper/ değişmez; yalnızca bu sürecin modül değişkenleri).
        cs.HOME_URL = self.quiet_url
        cs.CAPTCHA_URL = "https://www.sofascore.com/captcha.html?redirectUrl=" + quote(self.quiet_url, safe="")
        await self.bridge.ensure_ready()
        ctx = self.bridge.context
        self.page = self.bridge.page
        # Bağlamda keşif sayfasından başka sayfa kalmasın (köprünün açılış sayfaları da trafik üretir; kesici
        # yalnızca keşif sayfasındadır)
        for pg in list(ctx.pages):
            if pg is not self.page:
                await self._close_page(pg, "close-extra-page")
        await self.intercept(self.page)
        # Bağlam düzeyinde kayıt: her satır isteği yapan çerçeveyi yazar
        ctx.on("response", lambda r: self._spawn(self.on_response(r)))
        ctx.on("requestfailed", self.on_request_failed)
        ctx.on("page", self.on_page)
        self.page.on("websocket", self.on_websocket)

    async def _gap(self) -> None:
        wait = self.last_nav + PAGE_GAP - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        self.last_nav = time.time()

    async def _settle(self, settle: float, dwell: float) -> None:
        t0 = time.time()
        await asyncio.sleep(min(settle, dwell))
        while time.time() - t0 < dwell and time.time() - self.last_api < settle:
            await asyncio.sleep(1)

    def _begin(self, cmd: Dict[str, Any]) -> None:
        self.active = True
        self.sport = cmd.get("sport") or self.sport
        self.page_type = cmd.get("page_type") or self.page_type
        self.new_patterns = []
        self._step_base = (self.api_count, self.gone, self.intercept_errors)

    def _step(self) -> Dict[str, int]:
        """Bu adımın sayaçları: gönderilen, sıra beklerken ölen (gönderilmeyen), yanıtlanamayan istekler."""
        sent, gone, errors = self._step_base
        return {"page_requests": self.api_count - sent, "step_gone": self.gone - gone,
                "step_intercept_errors": self.intercept_errors - errors}

    async def goto(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        self._begin(cmd)
        await self._gap()
        self.page_url = cmd["url"]
        t0 = time.time()
        err = None
        for attempt in range(2):
            try:
                await self.page.goto(cmd["url"], wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                err = str(e)[:300]
            await self._settle(float(cmd.get("settle", 6)), float(cmd.get("dwell", 90)))
            if "captcha.html" not in self.page.url or attempt == 1:
                break
            # Site API 403 alınca captcha.html'e yönlendirir: köprünün çözücüsü (Turnstile), sonra bir kez daha
            token = await self._solve_challenge()
            self._append("pages.jsonl", {"ts": round(time.time(), 3), "op": "challenge", "url": cmd["url"],
                                         "solved": bool(token)})
            await asyncio.sleep(PAGE_GAP)
        res = {"url": self._safe(self.page.url), "title": await self.page.title(),
               **self._step(), "seconds": round(time.time() - t0, 1), "error": err,
               "new_patterns": list(self.new_patterns), **self.stats()}
        self._append("pages.jsonl", {"ts": round(t0, 3), "op": "goto", "sport": self.sport,
                                     "page_type": self.page_type, "requested": cmd["url"], **res})
        self._save_state()
        return res

    async def click(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        self._begin(cmd)
        await self._gap()
        t0 = time.time()
        err = None
        try:
            if cmd.get("selector"):
                target = self.page.locator(cmd["selector"]).first
            else:
                target = self.page.get_by_text(cmd["text"], exact=bool(cmd.get("exact", True))).first
            if cmd.get("dispatch"):
                # Üstte katman (çerez/reklam) varken: DOM click olayı doğrudan öğeye
                await target.dispatch_event("click", timeout=4000)
            else:
                await target.click(timeout=10000)
        except Exception as e:
            err = str(e)[:300]
        if err is None:  # tıklanamadıysa beklemek yalnızca sayfanın arka plan isteklerine bütçe harcar
            await self._settle(float(cmd.get("settle", 6)), float(cmd.get("dwell", 60)))
        res = {"url": self._safe(self.page.url), **self._step(),
               "seconds": round(time.time() - t0, 1), "error": err, "new_patterns": list(self.new_patterns),
               **self.stats()}
        self._append("pages.jsonl", {"ts": round(t0, 3), "op": "click", "sport": self.sport,
                                     "page_type": self.page_type, "target": cmd.get("text") or cmd.get("selector"), **res})
        self._save_state()
        return res

    async def fill(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        self._begin(cmd)
        await self._gap()
        t0 = time.time()
        err = None
        try:
            box = self.page.locator(cmd["selector"]).first
            await box.click(timeout=10000)
            await box.press_sequentially(cmd["text"], delay=150)
        except Exception as e:
            err = str(e)[:300]
        await self._settle(float(cmd.get("settle", 6)), float(cmd.get("dwell", 60)))
        res = {"url": self._safe(self.page.url), **self._step(),
               "seconds": round(time.time() - t0, 1), "error": err, "new_patterns": list(self.new_patterns),
               **self.stats()}
        self._append("pages.jsonl", {"ts": round(t0, 3), "op": "fill", "sport": self.sport,
                                     "page_type": self.page_type, "target": cmd["selector"], "text": cmd["text"], **res})
        self._save_state()
        return res

    async def probe(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """
        Kesici öz denetimi: sayfa `https://explorer-probe.invalid/` adresine fetch atar; kesici onu `hold` sn tutar
        ve yerelde yanıtlar. Bütçe harcamaz, adımdan bağımsızdır; `.invalid` çözülmediği için istek makineden çıkmaz.
        `ok`: istek durduruldu, bekletildi, yanıtlandı ve sayfa yanıtı okudu.
        """
        hold = max(0.0, min(float(cmd.get("hold", 2.0)), 30.0))
        n = max(self._probes, default=0) + 1
        state = self._probes[n] = {"hold": hold}
        url = f"https://{PROBE_HOST}/probe?n={n}"
        t0 = time.time()
        try:
            seen = await asyncio.wait_for(self.page.evaluate(PROBE_JS, url), hold + 15)
        except Exception as e:
            seen = f"evaluate failed: {e.__class__.__name__}: {str(e)[:200]}"
        expected = "ok:" + json.dumps({"probe": n})
        res = {"ok": bool(state.get("paused")) and state.get("answered") is True and seen == expected,
               "paused": bool(state.get("paused")), "answered": state.get("answered"), "held_s": state.get("held_s"),
               "error": state.get("error"), "page_saw": str(seen)[:200], "seconds": round(time.time() - t0, 1),
               "page": self._safe(self.page.url)}
        self._append("pages.jsonl", {"ts": round(t0, 3), "op": "probe", **res})
        return {**res, **self.stats()}

    async def links(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        anchors = await self.page.evaluate(
            "() => [...document.querySelectorAll('a[href]')].map(a => [a.getAttribute('href'), (a.innerText||'').trim().slice(0,80)])"
        )
        rx = re.compile(cmd.get("pattern") or ".")
        seen, out = set(), []
        for href, text in anchors:
            if href and rx.search(href) and href not in seen:
                seen.add(href)
                out.append([href, text])
        return {"count": len(out), "links": out[: int(cmd.get("limit", 400))]}

    async def listen(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """
        Boşta bekleme: SofaScore API istekleri gönderilmeden iptal edilir (bütçe harcanmaz), sayfanın kendi
        açtığı NATS bağlantısı kare almaya devam eder. Push gecikmesi ölçümü için; yeni bağlantı açılmaz.
        """
        self.active = False
        self.page_type = cmd.get("page_type") or self.page_type
        before = {k: v["count"] for k, v in self.ws_subjects.items()}
        await asyncio.sleep(float(cmd.get("seconds", 300)))
        got = {k: v["count"] - before.get(k, 0) for k, v in self.ws_subjects.items() if v["count"] - before.get(k, 0)}
        self._append("pages.jsonl", {"ts": round(time.time(), 3), "op": "listen", "sport": self.sport,
                                     "page_type": self.page_type, "seconds": cmd.get("seconds", 300), "msgs": got})
        return {"msgs": got, **self.stats()}

    async def tabs(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Sayfadaki sekmeler (role=tab): adları sayfadan okunur, listeden değil."""
        found = await self.page.evaluate(
            """() => [...document.querySelectorAll('[role=tab]')].map(e => ({
                text: (e.innerText || '').trim().slice(0, 60),
                selected: e.getAttribute('aria-selected'),
                id: e.getAttribute('data-tabid') || e.id || null,
                href: e.getAttribute('href')}))"""
        )
        return {"count": len(found), "tabs": found}

    async def pages(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        return {"pages": [self._safe(pg.url) for pg in self.bridge.context.pages]}

    async def text(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Görünen metin (sekme adlarını bulmak için), kısaltılmış."""
        body = await self.page.evaluate("() => document.body.innerText")
        return {"text": body[: int(cmd.get("limit", 3000))]}

    def ws_subjects_path(self) -> str:
        """Koşu başına ayrı dosya: yeni koşu önceki koşunun konu sayaçlarının üzerine yazmaz."""
        name = "ws_subjects.json" if self.run_id is None else f"ws_subjects_{self.run_id}.json"
        return os.path.join(self.out, name)


async def serve(ctl: str, ex: Explorer) -> None:
    os.makedirs(ctl, exist_ok=True)
    await ex.start()
    # Öz denetim: kesici bekletilen isteği yanıtlayabiliyor mu (bütçe harcamaz, ağa çıkmaz)
    check = await ex.probe({"hold": 1.0})
    self_check = {k: check[k] for k in ("ok", "paused", "answered", "error", "page_saw")}
    print("ready", utc_now(), {**ex.stats(), "self_check": self_check}, flush=True)
    if not check["ok"]:
        print("warning: the interception self-check failed; do not spend budget before `probe` passes", flush=True)
    while True:
        cmds = sorted(glob.glob(os.path.join(ctl, "cmd_*.json")))
        if not cmds:
            await asyncio.sleep(0.5)
            continue
        path = cmds[0]
        with open(path, encoding="utf-8") as f:
            cmd = json.load(f)
        os.rename(path, path + ".taken")
        op = cmd.get("op")
        try:
            if op == "quit":
                res: Dict[str, Any] = {"ok": True, **ex.stats()}
            elif op in ("goto", "click", "fill", "links", "text", "pages", "tabs", "listen", "probe"):
                res = await getattr(ex, op)(cmd)
            elif op == "stats":
                res = ex.stats()
            else:
                res = {"error": f"unknown op {op}"}
        except Exception as e:
            res = {"error": f"{e.__class__.__name__}: {str(e)[:300]}"}
        ex.active = False
        rc.write_json(ex.ws_subjects_path(), ex.ws_subjects)
        rc.write_json(path.replace("cmd_", "res_"), res)
        if op == "quit":
            break
    ex._save_state()
    await ex.bridge.close()


def send(ctl: str, raw: str, timeout: float = 900) -> None:
    os.makedirs(ctl, exist_ok=True)
    name = f"cmd_{time.time_ns()}.json"
    tmp = os.path.join(ctl, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(raw)
    os.replace(tmp, os.path.join(ctl, name))
    res = os.path.join(ctl, name.replace("cmd_", "res_"))
    t0 = time.time()
    while not os.path.exists(res):
        if time.time() - t0 > timeout:
            print(json.dumps({"error": "timeout"}))
            return
        time.sleep(0.5)
    with open(res, encoding="utf-8") as f:
        print(f.read())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="explore_all_sports.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_serve = sub.add_parser("serve", help="open the browser and execute commands from CTL_DIR")
    p_serve.add_argument("ctl", metavar="CTL_DIR")
    p_serve.add_argument("--new-run", action="store_true",
                         help="start a new run with a fresh budget; the previous run's state is kept in _state.json")
    p_serve.add_argument("--run-id", default=None, help="id of the new run (default: UTC timestamp)")
    p_serve.add_argument("--max-requests", type=int, default=MAX_API_REQUESTS,
                         help=f"SofaScore API requests per run (default {MAX_API_REQUESTS})")
    p_serve.add_argument("--max-hours", type=float, default=MAX_SECONDS / 3600,
                         help=f"hours per run (default {MAX_SECONDS / 3600:g})")
    p_serve.add_argument("--out", default=OUT, help="output folder (default research/all_sports)")
    p_send = sub.add_parser("send", help="send one JSON command to a running serve and print its result")
    p_send.add_argument("ctl", metavar="CTL_DIR")
    p_send.add_argument("command", metavar="JSON")
    p_send.add_argument("--timeout", type=float, default=900)
    return parser


def explorer_from_args(args: argparse.Namespace, bridge: Any = None) -> Explorer:
    """serve'ün argümanlarından keşif nesnesi; bütçesi bitmiş koşu sürdürülmez (tarayıcı açılmadan çıkılır)."""
    try:
        ex = Explorer(args.out, new_run=args.new_run, run_id=args.run_id, max_requests=args.max_requests,
                      max_seconds=args.max_hours * 3600, bridge=bridge)
    except ValueError as e:
        raise SystemExit(f"error: {e}") from None
    if ex.over_budget():
        raise SystemExit(
            f"error: run {ex.run_id or '(2026-10-01, no id)'} has used its budget ({ex.api_count} API requests, "
            f"{(time.time() - ex.started) / 3600:.1f} h of {args.max_hours:g} h); start a new run with --new-run"
        )
    return ex


def main(argv: Optional[List[str]] = None) -> None:
    args = _parser().parse_args(argv)
    if args.cmd == "serve":
        asyncio.run(serve(args.ctl, explorer_from_args(args)))
    else:
        send(args.ctl, args.command, args.timeout)


if __name__ == "__main__":
    main()
