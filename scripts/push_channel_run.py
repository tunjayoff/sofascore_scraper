"""
Talimat 06: SofaScore push kanalı (NATS, ws.sofascore.com:9222) dayanıklılık testi.

Sitenin kendi spor sayfaları ayrı sekmelerde açık tutulur; her sekmenin kendi açtığı NATS bağlantısının
kareleri kaydedilir. Kendi NATS bağlantısı açılmaz, SUB denenmez, kimlik bilgisi kullanılmaz.

    python scripts/push_channel_run.py --out data/research_index/push_run_2026-10-01 --hours 3.5

Ölçülenler (docs/push-channel/README.md):
  1. kapsam: yoklama tabanı /sport/{sport}/events/live (spor başına dakikada 1 istek) ile push'un durum karşılaştırması
  2. dayanıklılık: kopmalar, sitenin kendiliğinden yeniden bağlanması ve aboneliği, PING/PONG aralığı
  3. kimlik bilgisi sabit mi: CONNECT user/pass yalnızca bellekte, rastgele tuzla HMAC; diske yalnızca
     "kimlik grubu" numarası yazılır (aynı/farklı). Tuz, hash ve değerler hiçbir yere yazılmaz.
  4. gecikme: finished karelerinde alınma anı − changes.changeTimestamp; yoklamanın aynı geçişi görme anı
  5. abonelik modeli: sayfa türü → SUB konuları
  6. maliyet: Chrome süreçlerinin RSS/CPU'su ve sekmelerin JS yığını, 5 dk'da bir

Hız: bu süreçten çıkan her SofaScore isteği (sayfaların kendi istekleri dahil) scripts/_research_common.py
kilidiyle toplamda ≤ 1 istek/sn. Görsel/medya/font istekleri engellenir.

Gizlilik: yanıt gövdesi saklanmaz (yalnızca yoklama yanıtından olayların id/durum/turnuva alanları).
INFO karelerinden yalnızca version/auth_required/tls_required yazılır.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

os.environ.setdefault("SOFASCORE_LOG__LEVEL", "WARNING")
os.environ.setdefault("SOFASCORE_CLIENT__BROWSER_PROFILE", os.path.expanduser("~/.cache/sofascore_research/chrome_push_run"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _research_common as rc  # noqa: E402
from _all_sports_patterns import SOFA_HOST, api_pattern  # noqa: E402
from explore_all_sports import nats_messages  # noqa: E402

from sofascore_scraper.client import bridge as cs  # noqa: E402

QUIET_URL = "https://www.sofascore.com/robots.txt"
API = "https://www.sofascore.com/api/v1"
SPORT_URL = {"football": "https://www.sofascore.com/tr/football", "tennis": "https://www.sofascore.com/tr/tennis",
             "basketball": "https://www.sofascore.com/tr/basketball"}
BLOCKED_TYPES = ("image", "media", "font")
TRAFFIC_TYPES = ("xhr", "fetch", "eventsource")
INFO_KEEP = ("version", "auth_required", "tls_required")
LOAD_WINDOW = 45.0  # sn: sayfa açılışından/yeniden yüklemeden sonra sayfanın API isteklerine izin verilen süre


def utc(ts: Optional[float] = None) -> str:
    return dt.datetime.fromtimestamp(ts or time.time(), dt.timezone.utc).isoformat(timespec="seconds")


class Recorder:
    def __init__(self, out: str) -> None:
        self.out = out
        os.makedirs(out, exist_ok=True)
        self._salt = secrets.token_bytes(32)  # yalnızca bellekte
        self._cred_groups: Dict[str, int] = {}  # hmac → grup no; yalnızca bellekte
        self.conn_seq = 0
        self.api_count = 0
        self.api_by_pattern: collections.Counter = collections.Counter()
        self.msg_counts: collections.Counter = collections.Counter()  # (tab, subject-kind, dakika)
        self.ws_alive: Dict[str, int] = {}
        self.loaded_at: Dict[Any, float] = {}  # sayfa → son yükleme anı
        self.poll_page: Any = None  # yoklama isteklerinin çıktığı sayfa (sırayı poll_live alır)
        self.idle_dropped: collections.Counter = collections.Counter()

    def write(self, name: str, row: Dict[str, Any]) -> None:
        with open(os.path.join(self.out, name), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def cred_group(self, user: str, password: str) -> int:
        digest = hmac.new(self._salt, f"{user}\x00{password}".encode(), hashlib.sha256).hexdigest()
        if digest not in self._cred_groups:
            self._cred_groups[digest] = len(self._cred_groups) + 1
        return self._cred_groups[digest]

    def attach(self, page: Any, tab: str) -> None:
        page.on("websocket", lambda ws: self.on_ws(ws, tab))

    def on_ws(self, ws: Any, tab: str) -> None:
        if "ws.sofascore.com" not in ws.url:
            self.write("ws_events.jsonl", {"ts": time.time(), "tab": tab, "event": "open-other",
                                           "host": urlparse(ws.url).hostname})
            return
        self.conn_seq += 1
        conn = self.conn_seq
        self.ws_alive[tab] = conn
        self.write("ws_events.jsonl", {"ts": time.time(), "tab": tab, "conn": conn, "event": "open"})
        info_done = [False]

        def recv(payload: Any) -> None:
            raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
            now = time.time()
            for cmd, subject, body in nats_messages(raw):
                if cmd == "PING" or cmd == "PONG":
                    self.write("pingpong.jsonl", {"ts": now, "tab": tab, "conn": conn, "dir": "recv", "cmd": cmd})
                elif cmd == "INFO" and not info_done[0]:
                    info_done[0] = True
                    vals = {}
                    for k in INFO_KEEP:
                        m = re.search(r'"' + k + r'"\s*:\s*("([^"]*)"|true|false)', body)
                        vals[k] = (m.group(2) if m.group(2) is not None else m.group(1) == "true") if m else None
                    self.write("ws_events.jsonl", {"ts": now, "tab": tab, "conn": conn, "event": "info", **vals})
                elif cmd in ("-ERR", "+OK"):
                    self.write("ws_events.jsonl", {"ts": now, "tab": tab, "conn": conn, "event": cmd,
                                                   "text": body[:200]})
                elif cmd in ("MSG", "HMSG"):
                    kind = re.sub(r"\d+", "{id}", subject)
                    self.msg_counts[(tab, kind, int(now // 60))] += 1
                    try:
                        obj = json.loads(body)
                    except ValueError:
                        continue
                    if isinstance(obj, dict) and any(k.startswith("status.") for k in obj):
                        self.write("status_frames.jsonl", {
                            "ts": round(now, 3), "tab": tab, "conn": conn, "subject": subject, "id": obj.get("id"),
                            "status_code": obj.get("status.code"), "status_type": obj.get("status.type"),
                            "status_description": obj.get("status.description"),
                            "change_ts": obj.get("changes.changeTimestamp"), "winnerCode": obj.get("winnerCode"),
                            "keys": sorted(obj.keys())})

        def sent(payload: Any) -> None:
            raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
            now = time.time()
            for line in raw.decode("utf-8", errors="replace").split("\r\n"):
                if line.startswith("CONNECT "):
                    try:
                        opts = json.loads(line[8:])
                    except ValueError:
                        opts = {}
                    group = self.cred_group(str(opts.get("user")), str(opts.get("pass")))
                    # Yalnızca grup numarası ve kimlik dışı seçenekler; user/pass hiçbir biçimde yazılmaz
                    self.write("ws_events.jsonl", {"ts": now, "tab": tab, "conn": conn, "event": "connect",
                                                   "cred_group": group, "has_user": "user" in opts,
                                                   "lang": opts.get("lang"), "version": opts.get("version")})
                elif line.startswith(("SUB ", "UNSUB ")):
                    parts = line.split(" ")
                    self.write("subs.jsonl", {"ts": now, "tab": tab, "conn": conn, "op": parts[0],
                                              "subject": parts[1] if len(parts) > 1 else ""})
                elif line in ("PING", "PONG"):
                    self.write("pingpong.jsonl", {"ts": now, "tab": tab, "conn": conn, "dir": "sent", "cmd": line})

        def closed(_: Any = None) -> None:
            if self.ws_alive.get(tab) == conn:
                self.ws_alive.pop(tab, None)
            self.write("ws_events.jsonl", {"ts": time.time(), "tab": tab, "conn": conn, "event": "close"})

        ws.on("framereceived", recv)
        ws.on("framesent", sent)
        ws.on("close", closed)
        ws.on("socketerror", lambda e: self.write("ws_events.jsonl", {"ts": time.time(), "tab": tab, "conn": conn,
                                                                      "event": "socketerror", "text": str(e)[:200]}))

    async def route(self, route: Any) -> None:
        try:
            req = route.request
            host = urlparse(req.url).hostname or ""
            if req.resource_type in BLOCKED_TYPES:
                await route.abort()
                return
            if req.resource_type in TRAFFIC_TYPES and SOFA_HOST.search(host):
                try:
                    page = req.frame.page
                except Exception:
                    page = None  # service worker
                if page is not None and page is self.poll_page:
                    await route.continue_()  # yoklama: sırayı poll_live aldı
                    return
                pat = api_pattern(req.url)
                # Yükleme penceresinden sonra sayfanın tekrar eden API istekleri gönderilmez (push bağlantısı
                # etkilenmez). /token/ ve /config/ geçer: yeniden bağlanma bunlara ihtiyaç duyarsa bozulmasın.
                loaded = self.loaded_at.get(page, 0.0)
                if time.time() - loaded > LOAD_WINDOW and not pat.startswith(("/token/", "/config/")):
                    self.idle_dropped[pat] += 1
                    await route.abort("blockedbyclient")
                    return
                await asyncio.to_thread(rc._wait_rate_slot)
                self.api_count += 1
                self.api_by_pattern[pat] += 1
            await route.continue_()
        except Exception:
            pass

    def flush_counts(self) -> None:
        rows = [{"tab": t, "subject": k, "minute": m, "count": c} for (t, k, m), c in sorted(self.msg_counts.items())]
        with open(os.path.join(self.out, "msg_counts.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f)
        with open(os.path.join(self.out, "api_counts.json"), "w", encoding="utf-8") as f:
            json.dump({"total": self.api_count, "by_pattern": dict(self.api_by_pattern.most_common()),
                       "idle_dropped": dict(self.idle_dropped.most_common())}, f, indent=1)


async def solve(bridge: Any) -> None:
    """Köprünün captcha çözücüsü; yeni token köprüye de kaydedilir (yoklama başlığı güncel kalsın)."""
    token = await bridge._solve_on_captcha_page()
    if token:
        bridge._set_token(token)


async def _fetch_live(bridge: Any, sport: str) -> Dict[str, Any]:
    xreq = hashlib.sha256(str(int(time.time()) // 1800).encode()).hexdigest()[:6]
    xcap = await bridge._token_from_context()  # sayfaların kullandığı güncel cookie değeri
    return await bridge.evaluate(rc._FETCH_WITH_HEADERS_JS, [f"{API}/sport/{sport}/events/live", xreq, xcap, 20000])


async def poll_live(bridge: Any, rec: Recorder, sport: str) -> None:
    """Yoklama tabanı: /sport/{sport}/events/live; olay başına id, durum, turnuva (gövde saklanmaz)."""
    t0 = time.time()
    try:
        await asyncio.to_thread(rc._wait_rate_slot)
        rec.api_count += 1
        rec.api_by_pattern["poll:/sport/{sport}/events/live"] += 1
        r = await _fetch_live(bridge, sport)
        if r and r.get("status") == 403:
            rec.write("run.jsonl", {"ts": time.time(), "event": "poll-403", "sport": sport})
            await solve(bridge)
            await asyncio.to_thread(rc._wait_rate_slot)
            rec.api_count += 1
            rec.api_by_pattern["poll:/sport/{sport}/events/live"] += 1
            r = await _fetch_live(bridge, sport)
    except Exception as e:
        rec.write("polls.jsonl", {"ts": t0, "sport": sport, "error": str(e)[:200]})
        return
    events = ((r or {}).get("body") or {}).get("events") or []
    rec.write("polls.jsonl", {
        "ts": t0, "done": time.time(), "sport": sport, "status": (r or {}).get("status"),
        "age": ((r or {}).get("headers") or {}).get("age"),
        "events": [{"id": e.get("id"), "code": (e.get("status") or {}).get("code"),
                    "type": (e.get("status") or {}).get("type"),
                    "ut": ((e.get("tournament") or {}).get("uniqueTournament") or {}).get("id"),
                    "ut_name": ((e.get("tournament") or {}).get("uniqueTournament") or {}).get("name")
                    or (e.get("tournament") or {}).get("name"),
                    "change_ts": (e.get("changes") or {}).get("changeTimestamp")} for e in events],
    })


def chrome_metrics(profile: str) -> Dict[str, Any]:
    """Bu sürecin Chrome'u: komut satırında profil klasörü geçen süreçler (ve alt süreçleri)."""
    out = subprocess.run(["ps", "-eo", "pid,ppid,rss,pcpu,args", "--no-headers"], capture_output=True, text=True).stdout
    procs = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) == 5:
            procs.append((int(parts[0]), int(parts[1]), int(parts[2]), float(parts[3]), parts[4]))
    roots = {p[0] for p in procs if profile in p[4]}
    members, changed = set(roots), True
    while changed:
        changed = False
        for pid, ppid, *_ in procs:
            if ppid in members and pid not in members:
                members.add(pid)
                changed = True
    mine = [p for p in procs if p[0] in members]
    return {"processes": len(mine), "rss_mb": round(sum(p[2] for p in mine) / 1024, 1),
            "cpu_pct_sum": round(sum(p[3] for p in mine), 1),
            "renderers": len([p for p in mine if "--type=renderer" in p[4]])}


async def goto_checked(page: Any, url: str, rec: Recorder, bridge: Any, tab: str) -> None:
    """Sayfayı açar; API 403 alıp captcha.html'e düştüyse köprünün çözücüsüyle çözüp bir kez daha açar."""
    rec.loaded_at[page] = time.time()
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(8)
    if "captcha.html" in page.url:
        rec.write("run.jsonl", {"ts": time.time(), "event": "challenge-on-open", "tab": tab})
        await solve(bridge)
        rec.loaded_at[page] = time.time()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)


async def open_tab(ctx: Any, rec: Recorder, sport: str, tab: str, bridge: Any) -> Any:
    page = await ctx.new_page()
    rec.attach(page, tab)
    await goto_checked(page, SPORT_URL[sport], rec, bridge, tab)
    return page


async def cred_probe_new_context(rec: Recorder, out: str) -> None:
    """Yeni tarayıcı bağlamı: ayrı profil klasörüyle ikinci bir köprü; futbol sayfası, CONNECT, kapat."""
    other = cs.BrowserBridge(profile_dir=os.path.expanduser("~/.cache/sofascore_research/chrome_push_probe"))
    try:
        await other.ensure_ready()
        await other.context.route("**/*", rec.route)
        page = await other.context.new_page()
        rec.attach(page, "probe-new-context")
        await goto_checked(page, SPORT_URL["football"], rec, other, "probe-new-context")
        await asyncio.sleep(25)
    except Exception as e:
        rec.write("ws_events.jsonl", {"ts": time.time(), "tab": "probe-new-context", "event": "probe-error",
                                      "text": str(e)[:200]})
    finally:
        await other.close()


async def main_async(args: argparse.Namespace) -> None:
    cs.HOME_URL = QUIET_URL
    cs.CAPTCHA_URL = "https://www.sofascore.com/captcha.html?redirectUrl=" + "https%3A%2F%2Fwww.sofascore.com%2Frobots.txt"
    rec = Recorder(args.out)
    bridge = cs.BrowserBridge.get_instance()
    await bridge.ensure_ready()
    ctx = bridge.context
    await ctx.route("**/*", rec.route)
    rec.poll_page = bridge.page
    started = time.time()
    rec.write("run.jsonl", {"ts": started, "event": "start", "utc": utc(started), "sports": args.sports,
                            "hours": args.hours})
    tabs: Dict[str, Any] = {}
    # Profilde eski bir token kalmış olabilir: sekmelerden önce bir yoklama, 403'te challenge çözülür
    await poll_live(bridge, rec, args.sports[0])
    for sport in args.sports:
        tabs[sport] = await open_tab(ctx, rec, sport, sport, bridge)
        await asyncio.sleep(6)

    # 5. abonelik modeli: futbol canlı filtresi ve bir maç sayfası (kısa, ayrı sekmede)
    async def subscription_probe() -> None:
        page = await open_tab(ctx, rec, "football", "probe-live-filter", bridge)
        await asyncio.sleep(12)
        rec.loaded_at[page] = time.time()
        try:
            await page.locator('button:has-text("Canlı")').first.dispatch_event("click", timeout=5000)
        except Exception:
            pass
        await asyncio.sleep(15)
        href = await page.evaluate("() => (document.querySelector('a[href*=\"/football/match/\"]')||{}).getAttribute?.('href') || null")
        rec.write("run.jsonl", {"ts": time.time(), "event": "probe-match", "href": href})
        await page.close()
        if href:
            match = await ctx.new_page()
            rec.attach(match, "probe-match")
            await goto_checked(match, "https://www.sofascore.com" + href, rec, bridge, "probe-match")
            await asyncio.sleep(20)
            await match.close()

    await subscription_probe()

    end = started + args.hours * 3600
    next_poll = {s: time.time() for s in args.sports}
    next_metrics = time.time()
    reload_done = newtab_done = context_done = False
    while time.time() < end:
        now = time.time()
        for sport in args.sports:
            if now >= next_poll[sport]:
                next_poll[sport] = now + args.poll
                await poll_live(bridge, rec, sport)
        if now >= next_metrics:
            next_metrics = now + 300
            heaps = {}
            for sport, page in tabs.items():
                try:
                    heaps[sport] = await page.evaluate(
                        "() => performance.memory ? Math.round(performance.memory.usedJSHeapSize/1048576) : null")
                except Exception as e:
                    heaps[sport] = f"error: {str(e)[:60]}"
            rec.write("metrics.jsonl", {"ts": now, **chrome_metrics(os.environ["SOFASCORE_CLIENT__BROWSER_PROFILE"]),
                                        "js_heap_mb": heaps, "api_requests": rec.api_count,
                                        "ws_alive": dict(rec.ws_alive),
                                        "urls": {s: p.url for s, p in tabs.items()}})
            rec.flush_counts()
            # Sekme captcha'ya düştüyse: köprünün çözücüsü + sayfaya dönüş (doğal kopma sayılmaz; işaretlenir)
            for sport, page in tabs.items():
                if "captcha.html" in page.url:
                    rec.write("run.jsonl", {"ts": time.time(), "event": "challenge", "tab": sport})
                    await solve(bridge)
                    rec.loaded_at[page] = time.time()
                    await page.goto(SPORT_URL[sport], wait_until="domcontentloaded", timeout=60000)
        elapsed = now - started
        # 3. kimlik bilgisi karşılaştırmaları: yeniden yükleme, yeni sekme, yeni tarayıcı bağlamı
        if not reload_done and elapsed > args.hours * 3600 * 0.30:
            reload_done = True
            rec.write("run.jsonl", {"ts": time.time(), "event": "cred-test-reload", "tab": args.sports[0]})
            rec.loaded_at[tabs[args.sports[0]]] = time.time()
            await tabs[args.sports[0]].reload(wait_until="domcontentloaded", timeout=60000)
        if not newtab_done and elapsed > args.hours * 3600 * 0.45:
            newtab_done = True
            rec.write("run.jsonl", {"ts": time.time(), "event": "cred-test-new-tab"})
            p = await open_tab(ctx, rec, "football", "probe-new-tab", bridge)
            await asyncio.sleep(25)
            await p.close()
        if not context_done and elapsed > args.hours * 3600 * 0.60:
            context_done = True
            rec.write("run.jsonl", {"ts": time.time(), "event": "cred-test-new-context"})
            await cred_probe_new_context(rec, args.out)
        await asyncio.sleep(1)

    # test sonu: yeni sekmede bir CONNECT daha (başlangıçla karşılaştırma)
    rec.write("run.jsonl", {"ts": time.time(), "event": "cred-test-end"})
    p = await open_tab(ctx, rec, "football", "probe-end", bridge)
    await asyncio.sleep(25)
    await p.close()
    rec.flush_counts()
    rec.write("run.jsonl", {"ts": time.time(), "event": "end", "utc": utc(), "api_requests": rec.api_count})
    await bridge.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours", type=float, default=3.5)
    ap.add_argument("--poll", type=float, default=60.0)
    ap.add_argument("--sports", default="football,tennis,basketball")
    args = ap.parse_args()
    args.sports = [s for s in args.sports.split(",") if s]
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
