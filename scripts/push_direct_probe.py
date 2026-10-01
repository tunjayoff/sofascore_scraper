"""
Talimat 07: doğrudan bağlantı deneyi (Tuncay tarafından doğrudan onaylandı, manuel modda).

Tarayıcı sekmesi olmadan, hafif bir Python istemcisiyle (aiohttp WebSocket; TLS parmak izi taklidi YOK)
SofaScore push kanalını (NATS-over-WS, wss://ws.sofascore.com:9222) dinlemek üretimde çalışır mı, bellek
maliyeti ne — onu ölçer.

    python scripts/push_direct_probe.py --out data/research_index/push_direct_2026-10-01 --minutes 38

Kimlik bilgisi: köprünün açtığı futbol sayfasının KENDİ CONNECT karesinden (framesent) okunur ve yalnızca
bellekte tutulur. Diske/loga/kayda/commit'e yazılmaz; her yerde maskelenir. JS paketinden çıkarma yok.
Bu, her anonim ziyaretçinin tarayıcısına gönderilen paylaşımlı istemci jetonudur; kimsenin hesabı değil.

Davranış sayfanınkiyle aynı ve asgari: TEK bağlantı, TEK konu (sport.football), yalnız dinleme. PUB yok,
başka konu/wildcard yok. Sunucu PING'ine PONG; 120 sn'de bir kendi PING. Kopunca bir kez yeniden bağlan;
ikinci kopmada ya da herhangi bir -ERR'de hemen dur.

Karşılaştırma tabanı (aynı anda, ortak <= 1 istek/sn kilidi): dakikada bir /sport/football/events/live ve
tek hafif (engelli) futbol sekmesi. Ölçülenler: bağlantı kabul edildi mi; sport.football mesaj kümesi
sayfanınkiyle aynı mı; bitiş gecikmesi; istemci sürecinin RSS/CPU'su; kopma/yeniden bağlanma; sunucu
hata/uyarı kareleri.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import ssl
import sys
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("SOFASCORE_BROWSER_PROFILE", os.path.expanduser("~/.cache/sofascore_research/chrome_push_direct"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _research_common as rc  # noqa: E402
import push_channel_run as pcr  # noqa: E402
from _all_sports_patterns import SOFA_HOST  # noqa: E402
from explore_all_sports import nats_messages  # noqa: E402

import src.challenge_solver as cs  # noqa: E402

WS_URL = "wss://ws.sofascore.com:9222/"
TOPIC = "sport.football"
PING_INTERVAL = 120.0
ALLOWED_THIRD = ("challenges.cloudflare.com",)
_SALT = secrets.token_bytes(32)  # yalnızca bellek
_cred_groups: Dict[str, int] = {}


def cred_group(user: str, password: str) -> int:
    d = hmac.new(_SALT, f"{user}\x00{password}".encode(), hashlib.sha256).hexdigest()
    if d not in _cred_groups:
        _cred_groups[d] = len(_cred_groups) + 1
    return _cred_groups[d]


def self_rss_mb() -> float:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return -1.0


def self_cpu_ticks() -> int:
    try:
        with open("/proc/self/stat") as f:
            p = f.read().rsplit(")", 1)[1].split()
        return int(p[11]) + int(p[12])
    except (OSError, IndexError, ValueError):
        return 0


class Probe:
    def __init__(self, out: str) -> None:
        self.out = out
        os.makedirs(out, exist_ok=True)
        self.creds: Optional[Dict[str, Any]] = None  # bellekte; asla yazılmaz
        self.ua = ""
        self.direct_frames: List[Dict[str, Any]] = []
        self.page_frames: List[Dict[str, Any]] = []
        self.api_count = 0

    def write(self, name: str, row: Dict[str, Any]) -> None:
        with open(os.path.join(self.out, name), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # --- sayfa tarafi: CONNECT'ten kimlik + sport.football kareleri ---------------------------------

    def attach_page(self, page: Any) -> None:
        page.on("websocket", self._on_page_ws)

    def _on_page_ws(self, ws: Any) -> None:
        if "ws.sofascore.com" not in ws.url:
            return

        def sent(payload: Any) -> None:
            raw = payload.encode() if isinstance(payload, str) else bytes(payload)
            for line in raw.decode("utf-8", errors="replace").split("\r\n"):
                if line.startswith("CONNECT ") and self.creds is None:
                    try:
                        opts = json.loads(line[8:])
                    except ValueError:
                        return
                    # user/pass yalnizca bellekte; kayda kimlik disi alanlar + grup no
                    self.creds = opts
                    self.write("direct.jsonl", {"ts": time.time(), "event": "captured-connect",
                                                "cred_group": cred_group(str(opts.get("user")), str(opts.get("pass"))),
                                                "fields": sorted(k for k in opts if k not in ("user", "pass")),
                                                "lang": opts.get("lang"), "version": opts.get("version")})

        def recv(payload: Any) -> None:
            raw = payload.encode() if isinstance(payload, str) else bytes(payload)
            now = time.time()
            for cmd, subject, body in nats_messages(raw):
                if cmd in ("MSG", "HMSG") and subject == TOPIC:
                    try:
                        obj = json.loads(body)
                    except ValueError:
                        continue
                    self.page_frames.append({"ts": now, "id": obj.get("id"),
                                             "status_type": obj.get("status.type"),
                                             "status_code": obj.get("status.code"),
                                             "change_ts": obj.get("changes.changeTimestamp"),
                                             "has_status": any(k.startswith("status.") for k in obj)})

        ws.on("framesent", sent)
        ws.on("framereceived", recv)

    # --- yoklama tabani --------------------------------------------------------------------------

    async def poll(self, bridge: Any) -> None:
        t0 = time.time()
        try:
            await asyncio.to_thread(rc._wait_rate_slot)
            self.api_count += 1
            xreq = hashlib.sha256(str(int(time.time()) // 1800).encode()).hexdigest()[:6]
            xcap = await bridge._token_from_context()
            r = await bridge.evaluate(rc._FETCH_WITH_HEADERS_JS,
                                      ["https://www.sofascore.com/api/v1/sport/football/events/live", xreq, xcap, 20000])
        except Exception as e:
            self.write("polls.jsonl", {"ts": t0, "error": str(e)[:200]})
            return
        events = ((r or {}).get("body") or {}).get("events") or []
        self.write("polls.jsonl", {"ts": t0, "done": time.time(), "status": (r or {}).get("status"),
                                   "events": [{"id": e.get("id"), "code": (e.get("status") or {}).get("code"),
                                               "type": (e.get("status") or {}).get("type"),
                                               "change_ts": (e.get("changes") or {}).get("changeTimestamp")}
                                              for e in events]})

    # --- dogrudan istemci ------------------------------------------------------------------------

    def connect_line(self) -> bytes:
        """Sayfanin CONNECT'indeki alanlarin aynisi + bellekteki kimlik. Fazladan alan yok."""
        c = dict(self.creds or {})
        return b"CONNECT " + json.dumps(c, separators=(",", ":")).encode() + b"\r\n"

    async def run_direct(self, stop_at: float) -> None:
        reconnects = 0
        conn_seq = 0
        while time.time() < stop_at:
            conn_seq += 1
            self.write("direct.jsonl", {"ts": time.time(), "event": "dial", "conn": conn_seq, "url": WS_URL})
            try:
                ctx = ssl.create_default_context()
                async with aiohttp.ClientSession(headers={"User-Agent": self.ua,
                                                           "Origin": "https://www.sofascore.com"}) as sess:
                    async with sess.ws_connect(WS_URL, ssl=ctx, heartbeat=None, max_msg_size=4 * 1024 * 1024) as ws:
                        stop = await self._direct_session(ws, conn_seq, stop_at)
                if stop in ("fatal", "ended"):
                    return
                reconnects += 1
                if reconnects > 1:
                    self.write("direct.jsonl", {"ts": time.time(), "event": "stop", "reason": "second-drop"})
                    return
                self.write("direct.jsonl", {"ts": time.time(), "event": "reconnect-after-drop", "conn": conn_seq})
                await asyncio.sleep(2)
            except Exception as e:
                self.write("direct.jsonl", {"ts": time.time(), "event": "dial-error", "conn": conn_seq,
                                            "text": str(e)[:200]})
                return  # el sikisma reddi: dur, yeniden deneme yok

    async def _direct_session(self, ws: Any, conn: int, stop_at: float) -> str:
        info_seen = False
        subscribed = False
        last_ping = time.time()

        async def heartbeat() -> None:
            nonlocal last_ping
            while not ws.closed:
                await asyncio.sleep(5)
                if time.time() - last_ping >= PING_INTERVAL:
                    last_ping = time.time()
                    await ws.send_bytes(b"PING\r\n")
                    self.write("direct.jsonl", {"ts": time.time(), "event": "ping-sent", "conn": conn})
                if time.time() >= stop_at:
                    await ws.close()
                    return

        hb = asyncio.ensure_future(heartbeat())
        result = "dropped"
        try:
            async for msg in ws:
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
                raw = msg.data if isinstance(msg.data, bytes) else str(msg.data).encode()
                now = time.time()
                for cmd, subject, body in nats_messages(raw):
                    if cmd == "INFO" and not info_seen:
                        info_seen = True
                        vals = {}
                        for k in ("version", "auth_required", "tls_required"):
                            m = re.search(r'"' + k + r'"\s*:\s*("([^"]*)"|true|false)', body)
                            vals[k] = (m.group(2) if m.group(2) is not None else m.group(1) == "true") if m else None
                        self.write("direct.jsonl", {"ts": now, "event": "info", "conn": conn, **vals})
                        await ws.send_bytes(self.connect_line())
                        await ws.send_bytes(f"SUB {TOPIC} 1\r\n".encode())
                        await ws.send_bytes(b"PING\r\n")
                        self.write("direct.jsonl", {"ts": now, "event": "sent-connect-sub", "conn": conn})
                        subscribed = True
                    elif cmd == "PING":
                        await ws.send_bytes(b"PONG\r\n")
                    elif cmd == "-ERR":
                        self.write("direct.jsonl", {"ts": now, "event": "server-err", "conn": conn, "text": body[:200]})
                        result = "fatal"
                        break
                    elif cmd == "+OK":
                        self.write("direct.jsonl", {"ts": now, "event": "server-ok", "conn": conn})
                    elif cmd in ("MSG", "HMSG") and subject == TOPIC:
                        try:
                            obj = json.loads(body)
                        except ValueError:
                            continue
                        self.direct_frames.append({"ts": now, "conn": conn, "id": obj.get("id"),
                                                   "status_type": obj.get("status.type"),
                                                   "status_code": obj.get("status.code"),
                                                   "change_ts": obj.get("changes.changeTimestamp"),
                                                   "has_status": any(k.startswith("status.") for k in obj)})
                if time.time() >= stop_at:
                    result = "ended"
                    break
        finally:
            hb.cancel()
        if not subscribed and result == "dropped":
            result = "fatal"  # INFO gelmeden koptu
        self.write("direct.jsonl", {"ts": time.time(), "event": "session-end", "conn": conn, "result": result,
                                    "subscribed": subscribed, "direct_frames_so_far": len(self.direct_frames)})
        return result


async def route(probe: Probe, route_obj: Any) -> None:
    try:
        host = urlparse(route_obj.request.url).hostname or ""
        if route_obj.request.resource_type in ("image", "media", "font"):
            await route_obj.abort()
            return
        if not SOFA_HOST.search(host) and not host.endswith(ALLOWED_THIRD):
            await route_obj.abort("blockedbyclient")
            return
        await route_obj.continue_()
    except Exception:
        pass


async def main_async(args: argparse.Namespace) -> None:
    cs.HOME_URL = pcr.QUIET_URL
    cs.CAPTCHA_URL = "https://www.sofascore.com/captcha.html?redirectUrl=https%3A%2F%2Fwww.sofascore.com%2Frobots.txt"
    probe = Probe(args.out)
    bridge = cs.BrowserBridge.get_instance()
    await bridge.ensure_ready()
    await bridge.context.route("**/*", lambda r: asyncio.ensure_future(route(probe, r)))
    probe.ua = await bridge.page.evaluate("() => navigator.userAgent")
    start = time.time()
    probe.write("run.jsonl", {"ts": start, "event": "start", "utc": pcr.utc(start), "minutes": args.minutes})

    # 1) Hafif futbol sekmesi: CONNECT'i yakala (kimlik) + sport.football tabani
    await probe.poll(bridge)  # eski token cozumu
    page = await bridge.context.new_page()
    probe.attach_page(page)
    probe.write("run.jsonl", {"ts": time.time(), "event": "open-page"})
    await page.goto("https://www.sofascore.com/tr/football", wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(8)
    if "captcha.html" in page.url:
        await bridge._solve_on_captcha_page()
        await page.goto("https://www.sofascore.com/tr/football", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(8)
    for _ in range(20):
        if probe.creds is not None:
            break
        await asyncio.sleep(1)
    if probe.creds is None:
        probe.write("run.jsonl", {"ts": time.time(), "event": "abort", "reason": "no CONNECT captured"})
        await bridge.close()
        return

    stop_at = start + args.minutes * 60
    cpu0, t0 = self_cpu_ticks(), time.time()

    async def metrics() -> None:
        while time.time() < stop_at:
            await asyncio.sleep(300)
            ct, dt = self_cpu_ticks(), time.time()
            probe.write("metrics.jsonl", {"ts": time.time(), "client_rss_mb": self_rss_mb(),
                                          "client_cpu_pct_since_start": round((ct - cpu0) / os.sysconf("SC_CLK_TCK")
                                                                              / (dt - t0) * 100, 2),
                                          "direct_frames": len(probe.direct_frames),
                                          "page_frames": len(probe.page_frames), "api_requests": probe.api_count})

    async def poller() -> None:
        while time.time() < stop_at:
            await probe.poll(bridge)
            await asyncio.sleep(args.poll)

    await asyncio.gather(probe.run_direct(stop_at), poller(), metrics())
    probe.write("metrics.jsonl", {"ts": time.time(), "client_rss_mb": self_rss_mb(),
                                  "direct_frames": len(probe.direct_frames), "page_frames": len(probe.page_frames),
                                  "api_requests": probe.api_count, "final": True})
    for name, frames in (("direct_status_frames.jsonl", probe.direct_frames),
                         ("page_status_frames.jsonl", probe.page_frames)):
        with open(os.path.join(args.out, name), "w", encoding="utf-8") as f:
            for fr in frames:
                f.write(json.dumps(fr, separators=(",", ":")) + "\n")
    probe.write("run.jsonl", {"ts": time.time(), "event": "end", "utc": pcr.utc(),
                              "direct_frames": len(probe.direct_frames), "page_frames": len(probe.page_frames),
                              "api_requests": probe.api_count})
    await bridge.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--minutes", type=float, default=38)
    ap.add_argument("--poll", type=float, default=60.0)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
