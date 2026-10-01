"""
Talimat 05: SofaScore'un tüm sporları için pasif keşif tarayıcısı.

Tek bir BrowserBridge tarayıcısı açık kalır; komutlar bir kontrol klasöründen okunur, böylece
keşif adım adım yönlendirilir (tarayıcı ve challenge çözümü her adımda yeniden açılmaz):

    python scripts/explore_all_sports.py serve CTL_DIR          # tarayıcıyı açar, komut bekler
    python scripts/explore_all_sports.py send CTL_DIR '{"op": "goto", "url": "...", "sport": "..."}'

Komutlar (her biri `page_type` etiketi alır: home, sport, date, live, event, event-tab:<ad>, team,
player, tournament, search, ...; hangi sayfanın/sekmenin hangi isteği tetiklediği buradan okunur):
    goto   {url, sport, page_type, dwell?, settle?}  sayfayı açar; trafik durulana kadar (settle sn
                                                    sessizlik, en fazla dwell sn) bekler
    click  {text? | selector?, sport, page_type, ...}  sekmeye/düğmeye tıklar, sonra goto gibi bekler
    fill   {selector, text, sport, page_type, ...}     bir alana yazar (arama), sonra bekler
    links  {pattern?}                                  sayfadaki <a href> listesi (regex süzgeçli)
    text   {limit?}                                    görünen metin (sekme adlarını sayfadan okumak için)
    stats  {} / quit {}
Her gezinti komutu, o adımda ilk kez görülen pattern'leri (`new_patterns`) döndürür: keşifte derinleşme
ölçütü "yeni pattern çıkıyor mu".

Kurallar (talimat):
  - API keşfi pasif: sayfanın attığı tüm XHR/fetch/EventSource/WebSocket trafiği (request, response,
    requestfailed, websocket olayları; üçüncü taraf alan adları dahil) kaydedilir. Bu script kendisi
    hiçbir API isteği atmaz; bilinen uç noktalar yalnızca sonradan karşılaştırma tabanıdır.
  - SofaScore alan adlarına giden her XHR/fetch/EventSource isteği `page.route` içinde
    scripts/_research_common.py kilit dosyasından sıra alır: diğer araştırma süreçleriyle birlikte
    toplamda ≤ 1 istek/sn; bütçeye bunlar sayılır.
  - Sayfa gezintisi/tıklama ≥ 5 sn arayla. Yalnızca görsel, medya ve font istekleri engellenir.
  - Bütçe: 5.000 API isteği ya da 6 saat; aşılınca API istekleri iptal edilir.
  - Kullanıcı içeriği (yorum, oy, profil) sayfalarına gidilmez; WebSocket kareleri kısaltılarak saklanır.

Çıktı: research/all_sports/ (requests.jsonl, samples/, events/, ws.jsonl, pages.jsonl).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import glob
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault(
    "SOFASCORE_BROWSER_PROFILE", os.path.expanduser("~/.cache/sofascore_research/chrome_all_sports")
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _research_common as rc  # noqa: E402
from _all_sports_patterns import SOFA_HOST, api_pattern  # noqa: E402

ROOT = rc.ROOT
import src.challenge_solver as cs  # noqa: E402

OUT = os.path.join(ROOT, "research", "all_sports")
MAX_API_REQUESTS = 5000
MAX_SECONDS = 6 * 3600
PAGE_GAP = 5.0
SAMPLES_PER_PATTERN = 3
FULL_SAMPLE_BYTES = 20_000
QUIET_URL = "https://www.sofascore.com/robots.txt"
BLOCKED_TYPES = ("image", "media", "font")
TRAFFIC_TYPES = ("xhr", "fetch", "eventsource", "websocket")


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


def _origin(req: Any) -> str:
    """İsteği yapan: sayfa çerçevesinin URL'si ya da service worker."""
    try:
        return req.frame.url[:200]
    except Exception:
        return "service-worker"


def is_explorer_drop(failure: str) -> bool:
    """Bu script'in route.abort'u: önceki sürüm varsayılan koddu (ERR_FAILED), şimdi ERR_BLOCKED_BY_CLIENT."""
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


class Explorer:
    def __init__(self) -> None:
        self.bridge = cs.BrowserBridge.get_instance()
        self.page: Any = None
        self.sport = "unknown"
        self.page_url = ""
        self.api_count = 0
        self.blocked = 0
        self.started = time.time()
        self.last_nav = 0.0
        self.last_api = 0.0
        self.samples: Dict[str, int] = {}
        self.ws_frames: Dict[str, int] = {}
        self.ws_subjects: Dict[str, Dict[str, Any]] = {}
        self.page_type = ""
        self.active = False  # yalnızca bir komut sürerken SofaScore isteklerine izin verilir
        self.idle_dropped = 0
        self.known: set = set()
        self.new_patterns: List[str] = []
        os.makedirs(OUT, exist_ok=True)
        self._load_state()

    # --- bütçe ---------------------------------------------------------------------------

    def _load_state(self) -> None:
        path = os.path.join(OUT, "_state.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                s = json.load(f)
            self.started = s.get("started", self.started)
            self.api_count = s.get("api_requests", 0)
        req_log = os.path.join(OUT, "requests.jsonl")
        logged = 0
        if os.path.exists(req_log):
            with open(req_log, encoding="utf-8") as f:
                for line in f:
                    row = json.loads(line)
                    if row.get("status") is None and is_explorer_drop(row.get("failure") or ""):
                        continue
                    self.known.add(api_pattern(row["url"]))
                    if SOFA_HOST.search(row.get("host") or urlparse(row["url"]).hostname or ""):
                        logged += 1
        # Bütçe: kayıtlı SofaScore satırları en az gönderilen istek kadardır (yarıda kalan komut durumu yazmamış olabilir)
        self.api_count = max(self.api_count, logged)
        for p in glob.glob(os.path.join(OUT, "samples", "*", "*.json")):
            key = os.path.basename(os.path.dirname(p)) + "|" + os.path.basename(p).rsplit("__", 1)[0]
            self.samples[key] = self.samples.get(key, 0) + 1

    def _save_state(self) -> None:
        rc.write_json(
            os.path.join(OUT, "_state.json"),
            {"started": self.started, "api_requests": self.api_count, "updated": utc_now()},
        )

    def over_budget(self) -> bool:
        return self.api_count >= MAX_API_REQUESTS or time.time() - self.started >= MAX_SECONDS

    def stats(self) -> Dict[str, Any]:
        return {
            "api_requests": self.api_count,
            "blocked": self.blocked,
            "idle_dropped": self.idle_dropped,
            "elapsed_min": round((time.time() - self.started) / 60, 1),
            "over_budget": self.over_budget(),
        }

    # --- kayıt -----------------------------------------------------------------------------

    def _append(self, name: str, row: Dict[str, Any]) -> None:
        with open(os.path.join(OUT, name), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    async def on_route(self, route: Any) -> None:
        try:
            await self._route(route)
        except Exception as e:  # istek kilit beklerken iptal edildiyse (sayfa değişti) continue/abort hata verir
            self._append("pages.jsonl", {"ts": round(time.time(), 3), "op": "route-error", "url": route.request.url[:200],
                                         "error": str(e)[:200]})

    async def _route(self, route: Any) -> None:
        req = route.request
        host = urlparse(req.url).hostname or ""
        if req.resource_type in BLOCKED_TYPES:
            self.blocked += 1
            await route.abort()
            return
        if req.resource_type in TRAFFIC_TYPES and SOFA_HOST.search(host):
            # Komutlar arasında sayfa kendi kendine sorgulamaya devam eder (canlı yenileme, tembel yükleme):
            # bu istekler gönderilmez; bütçe yalnızca yönlendirilen adımlara harcanır.
            if not self.active:
                self.idle_dropped += 1
                await route.abort("blockedbyclient")
                return
            if self.over_budget():
                self.blocked += 1
                await route.abort("blockedbyclient")
                return
            await asyncio.to_thread(rc._wait_rate_slot)
            if not self.active:  # sıra beklerken komut bitti: gönderme
                self.idle_dropped += 1
                await route.abort("blockedbyclient")
                return
            self.api_count += 1
            self.last_api = time.time()
            if self.api_count % 10 == 0:
                self._save_state()
        await route.continue_()

    def _note_pattern(self, pattern: str) -> None:
        if pattern not in self.known:
            self.known.add(pattern)
            self.new_patterns.append(pattern)

    def on_request_failed(self, req: Any) -> None:
        if req.resource_type not in TRAFFIC_TYPES or is_explorer_drop(str(req.failure or "")):
            return  # bu script'in gönderilmeden iptal ettiği istek: trafik değil
        pattern = api_pattern(req.url)
        self._note_pattern(pattern)
        self._append("requests.jsonl", {
            "ts": round(time.time(), 3), "sport": self.sport, "page": self.page_url, "page_type": self.page_type,
            "url": req.url, "host": urlparse(req.url).hostname, "method": req.method,
            "resource_type": req.resource_type, "origin": _origin(req), "pattern": pattern, "status": None,
            "failure": str(req.failure)[:200] if req.failure else None, "cache_control": None,
            "response_keys": [], "sample_file": None,
        })

    async def on_response(self, resp: Any) -> None:
        req = resp.request
        if req.resource_type not in TRAFFIC_TYPES:
            return
        url = resp.url
        if SOFA_HOST.search(urlparse(url).hostname or ""):
            self.last_api = time.time()
        pattern = api_pattern(url)
        self._note_pattern(pattern)
        headers = await resp.all_headers() if hasattr(resp, "all_headers") else resp.headers
        body: Any = None
        try:
            body = json.loads(await resp.body())
        except Exception:
            body = None
        sample_file = None
        key = f"{self.sport}|{slug(pattern)}"
        if body is not None and self.samples.get(key, 0) < SAMPLES_PER_PATTERN:
            n = self.samples.get(key, 0) + 1
            self.samples[key] = n
            raw = json.dumps(body, ensure_ascii=False)
            trimmed = len(raw.encode("utf-8")) > FULL_SAMPLE_BYTES
            rel = os.path.join("samples", self.sport, f"{slug(pattern)}__{n}.json")
            rc.write_json(
                os.path.join(OUT, rel),
                {
                    "url": url,
                    "status": resp.status,
                    "fetched_at_utc": utc_now(),
                    "page": self.page_url,
                    "cache_control": headers.get("cache-control"),
                    "trimmed": trimmed,
                    "body": trim(body) if trimmed else body,
                },
            )
            sample_file = f"research/all_sports/{rel}"
        self._append(
            "requests.jsonl",
            {
                "ts": round(time.time(), 3),
                "sport": self.sport,
                "page": self.page_url,
                "page_type": self.page_type,
                "url": url,
                "host": urlparse(url).hostname,
                "method": req.method,
                "resource_type": req.resource_type,
                "origin": _origin(req),
                "pattern": pattern,
                "status": resp.status,
                "age": headers.get("age"),
                "cache_control": headers.get("cache-control"),
                "response_keys": response_keys(body),
                "sample_file": sample_file,
            },
        )
        if body is not None:
            found: List[Dict[str, Any]] = []
            find_events(body, found)
            if found:
                os.makedirs(os.path.join(OUT, "events"), exist_ok=True)
                with open(os.path.join(OUT, "events", f"{self.sport}.jsonl"), "a", encoding="utf-8") as f:
                    for e in found:
                        row = compact_event(e)
                        row["source_pattern"] = pattern
                        row["seen_at"] = round(time.time())
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def on_websocket(self, ws: Any) -> None:
        """
        Push kanalı (ws.sofascore.com:9222 NATS). Alınan: INFO (ilk), her konudan ilk 3 MSG ve `status.*`
        taşıyan tüm MSG'ler; konu başına sayaç + anahtar kümesi ws_subjects.json'da. Gönderilen: SUB/UNSUB
        hepsi; CONNECT'in kimlik alanları maskelenir (gizli bilgi saklanmaz).
        """
        opened = {"ts": round(time.time(), 3), "sport": self.sport, "page": self.page_url,
                  "page_type": self.page_type, "url": ws.url}
        self._append("ws.jsonl", {"kind": "open", **opened})
        self._note_pattern("ws:" + api_pattern(ws.url))
        self.ws_frames[ws.url] = 0

        def raw_of(payload: Any) -> bytes:
            return payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)

        def received(payload: Any) -> None:
            self.ws_frames[ws.url] = self.ws_frames.get(ws.url, 0) + 1
            for kind, subject, body in nats_messages(raw_of(payload)):
                base = {"ts": round(time.time(), 3), "sport": self.sport, "page_type": self.page_type, "url": ws.url}
                if kind == "INFO":
                    if self.ws_frames[ws.url] <= 2:
                        self._append("ws.jsonl", {"kind": "recv", "op": "INFO", **base, "body": body[:1500]})
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
                                              "body": obj if obj is not None else body[:1500]})

        def sent(payload: Any) -> None:
            for line in raw_of(payload).decode("utf-8", errors="replace").split("\r\n"):
                # Yalnızca komut satırları: PUB/HPUB gövdeleri (sitenin analitik olayları) saklanmaz
                if not line.startswith(("SUB ", "UNSUB ", "CONNECT ", "PUB ", "HPUB ")):
                    continue
                if line.startswith("CONNECT "):
                    try:
                        opts = json.loads(line[8:])
                        for k in list(opts):
                            if k in ("auth_token", "jwt", "sig", "nkey", "pass", "user", "token"):
                                opts[k] = "<redacted>"
                        line = "CONNECT " + json.dumps(opts)
                    except ValueError:
                        line = "CONNECT <unparsed, redacted>"
                self._append("ws.jsonl", {"kind": "sent", "ts": round(time.time(), 3), "sport": self.sport,
                                          "page_type": self.page_type, "page": self.page_url, "line": line[:500]})

        def closed(_: Any = None) -> None:
            self._append("ws.jsonl", {"kind": "close", "ts": round(time.time(), 3), "url": ws.url,
                                      "frames": self.ws_frames.get(ws.url, 0)})

        ws.on("framereceived", received)
        ws.on("framesent", sent)
        ws.on("close", closed)

    # --- komutlar --------------------------------------------------------------------------

    async def start(self) -> None:
        # Köprü açılışta ana sayfayı (ve captcha çözümünden sonra yönlendirmeyi) yükler; bu, route kurulmadan
        # olur ve sitenin onlarca isteği kilitsiz ve sayılmadan gider. Bu süreçte iki adres de API çağırmayan
        # bir sayfaya çevrilir (src/ değişmez; yalnızca bu sürecin modül değişkenleri).
        cs.HOME_URL = QUIET_URL
        cs.CAPTCHA_URL = "https://www.sofascore.com/captcha.html?redirectUrl=" + quote(QUIET_URL, safe="")
        await self.bridge.ensure_ready()
        ctx = self.bridge.context
        self.page = self.bridge.page
        # Bağlamda keşif sayfasından başka sayfa kalmasın (köprünün açılış sayfaları da trafik üretir)
        for pg in list(ctx.pages):
            if pg is not self.page:
                self._append("pages.jsonl", {"ts": round(time.time(), 3), "op": "close-extra-page", "url": pg.url})
                await pg.close()
        await ctx.route("**/*", self.on_route)
        # Bağlam düzeyinde: service worker dahil bütün trafik; her satır isteği yapan çerçeveyi yazar
        ctx.on("response", lambda r: asyncio.ensure_future(self.on_response(r)))
        ctx.on("requestfailed", self.on_request_failed)
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

    async def goto(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        self._begin(cmd)
        before = self.api_count
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
            token = await self.bridge._solve_on_captcha_page()
            self._append("pages.jsonl", {"ts": round(time.time(), 3), "op": "challenge", "url": cmd["url"],
                                         "solved": bool(token)})
            await asyncio.sleep(PAGE_GAP)
        res = {"url": self.page.url, "title": await self.page.title(), "page_requests": self.api_count - before,
               "seconds": round(time.time() - t0, 1), "error": err, "new_patterns": list(self.new_patterns),
               **self.stats()}
        self._append("pages.jsonl", {"ts": round(t0, 3), "op": "goto", "sport": self.sport,
                                     "page_type": self.page_type, "requested": cmd["url"], **res})
        self._save_state()
        return res

    async def click(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        self._begin(cmd)
        before = self.api_count
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
        res = {"url": self.page.url, "page_requests": self.api_count - before, "seconds": round(time.time() - t0, 1),
               "error": err, "new_patterns": list(self.new_patterns), **self.stats()}
        self._append("pages.jsonl", {"ts": round(t0, 3), "op": "click", "sport": self.sport,
                                     "page_type": self.page_type, "target": cmd.get("text") or cmd.get("selector"), **res})
        self._save_state()
        return res

    async def fill(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        self._begin(cmd)
        before = self.api_count
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
        res = {"url": self.page.url, "page_requests": self.api_count - before, "seconds": round(time.time() - t0, 1),
               "error": err, "new_patterns": list(self.new_patterns), **self.stats()}
        self._append("pages.jsonl", {"ts": round(t0, 3), "op": "fill", "sport": self.sport,
                                     "page_type": self.page_type, "target": cmd["selector"], "text": cmd["text"], **res})
        self._save_state()
        return res

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
        return {"pages": [pg.url for pg in self.bridge.context.pages]}

    async def text(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Görünen metin (sekme adlarını bulmak için), kısaltılmış."""
        body = await self.page.evaluate("() => document.body.innerText")
        return {"text": body[: int(cmd.get("limit", 3000))]}


async def serve(ctl: str) -> None:
    os.makedirs(ctl, exist_ok=True)
    ex = Explorer()
    await ex.start()
    print("hazır", utc_now(), ex.stats(), flush=True)
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
            elif op in ("goto", "click", "fill", "links", "text", "pages", "tabs", "listen"):
                res = await getattr(ex, op)(cmd)
            elif op == "stats":
                res = ex.stats()
            else:
                res = {"error": f"bilinmeyen op {op}"}
        except Exception as e:
            res = {"error": f"{e.__class__.__name__}: {str(e)[:300]}"}
        ex.active = False
        rc.write_json(os.path.join(OUT, "ws_subjects.json"), ex.ws_subjects)
        rc.write_json(path.replace("cmd_", "res_"), res)
        if op == "quit":
            break
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


def main(argv: Optional[List[str]] = None) -> None:
    argv = argv or sys.argv[1:]
    if argv[0] == "serve":
        asyncio.run(serve(argv[1]))
    elif argv[0] == "send":
        send(argv[1], argv[2])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
