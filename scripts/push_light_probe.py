"""
Talimat 06 ek ölçüm: "sayfayı dinlemenin" üretim maliyeti (ana koşudan sonra, ayrı profil).

    python scripts/push_light_probe.py --out data/research_index/push_light_2026-10-01 --minutes 35

0) Taban: köprünün tek sekmesi robots.txt'de beklerken Chrome RSS'i ve süreç sayısı.
A) Tek futbol spor sekmesi; görsel/medya/font ve tüm üçüncü taraf istekleri (reklam, analitik, video, harici
   betikler) engelli. NATS bağlantısı ve sport.football aboneliği kuruluyor mu; Chrome RSS, süreç/renderer
   sayısı, aralık CPU'su (/proc'tan), sayfanın çerçeve (iframe) sayısı ve alan adları. 30. dakikadaki yeniden
   bağlanmayı da görmek için varsayılan 35 dk.
B) Hafif sayfa adayları sırayla ayrı sekmede (40 sn): push bağlantısı açılıyor mu, hangi konulara abone olunuyor.
   Yalnızca gözlem; abonelik zorlanmaz, kendi bağlantı açılmaz.

Kayıt biçimi push_channel_run.py ile aynı (Recorder); kimlik bilgisi yalnızca bellekte tuzlu HMAC.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from typing import Any, Dict
from urllib.parse import urlparse

os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("SOFASCORE_BROWSER_PROFILE", os.path.expanduser("~/.cache/sofascore_research/chrome_push_light"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import push_channel_run as pcr  # noqa: E402
from _all_sports_patterns import SOFA_HOST  # noqa: E402

import src.challenge_solver as cs  # noqa: E402

ALLOWED_THIRD = ("challenges.cloudflare.com",)
CANDIDATES = [
    ("favorites", "https://www.sofascore.com/tr/favorites"),
    ("news", "https://www.sofascore.com/tr/news"),
    ("tournament", "https://www.sofascore.com/tr/football/tournament/england/premier-league/17"),
    ("trending", "https://www.sofascore.com/tr/trending"),
]


def cpu_ticks(pids) -> int:
    total = 0
    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat") as f:
                parts = f.read().rsplit(")", 1)[1].split()
            total += int(parts[11]) + int(parts[12])  # utime + stime
        except (OSError, IndexError, ValueError):
            pass
    return total


def member_pids(profile: str):
    import subprocess

    out = subprocess.run(["ps", "-eo", "pid,ppid,args", "--no-headers"], capture_output=True, text=True).stdout
    procs = []
    for line in out.splitlines():
        p = line.split(None, 2)
        if len(p) == 3:
            procs.append((int(p[0]), int(p[1]), p[2]))
    members = {p[0] for p in procs if profile in p[2]}
    changed = True
    while changed:
        changed = False
        for pid, ppid, _ in procs:
            if ppid in members and pid not in members:
                members.add(pid)
                changed = True
    return members


class LightRecorder(pcr.Recorder):
    def __init__(self, out: str) -> None:
        super().__init__(out)
        self.third_blocked = 0

    async def route(self, route: Any) -> None:
        try:
            host = urlparse(route.request.url).hostname or ""
            if not SOFA_HOST.search(host) and not host.endswith(ALLOWED_THIRD):
                self.third_blocked += 1
                await route.abort("blockedbyclient")
                return
        except Exception:
            pass
        await super().route(route)


async def sample(rec: LightRecorder, page: Any, profile: str, label: str) -> None:
    pids = member_pids(profile)
    t1 = cpu_ticks(pids)
    await asyncio.sleep(10)
    t2 = cpu_ticks(pids)
    cpu_pct = round((t2 - t1) / os.sysconf("SC_CLK_TCK") / 10 * 100, 1)
    frames = page.frames if page and not page.is_closed() else []
    hosts: Dict[str, int] = {}
    for fr in frames:
        h = urlparse(fr.url).hostname or fr.url[:20]
        hosts[h] = hosts.get(h, 0) + 1
    rec.write("metrics.jsonl", {"ts": time.time(), "label": label, **pcr.chrome_metrics(profile),
                                "cpu_pct_10s": cpu_pct, "frames": len(frames), "frame_hosts": hosts,
                                "ws_alive": dict(rec.ws_alive), "third_blocked": rec.third_blocked,
                                "api_requests": rec.api_count})


async def main_async(args: argparse.Namespace) -> None:
    cs.HOME_URL = pcr.QUIET_URL
    cs.CAPTCHA_URL = "https://www.sofascore.com/captcha.html?redirectUrl=https%3A%2F%2Fwww.sofascore.com%2Frobots.txt"
    profile = os.environ["SOFASCORE_BROWSER_PROFILE"]
    rec = LightRecorder(args.out)
    bridge = cs.BrowserBridge.get_instance()
    await bridge.ensure_ready()
    await bridge.context.route("**/*", rec.route)
    rec.poll_page = bridge.page
    start = time.time()
    rec.write("run.jsonl", {"ts": start, "event": "start", "utc": pcr.utc(start), "minutes": args.minutes})
    # Taban: köprünün tek sekmesi API çağırmayan robots.txt'de beklerken (sunucuda "tek boş sekme" maliyeti)
    await asyncio.sleep(50)
    await sample(rec, bridge.page, profile, "baseline-robots-1min")
    await pcr.poll_live(bridge, rec, "football")  # eski token: önce çöz
    page = await pcr.open_tab(bridge.context, rec, "football", "light-football", bridge)
    page.on("framenavigated", lambda fr: fr == page.main_frame and rec.write(
        "run.jsonl", {"ts": time.time(), "event": "main-frame-navigated", "url": fr.url[:120]}))
    marks = sorted({1, 5, 10, 15, 20, 25, 28, 30, 31, 32, 33, 35, args.minutes})
    for m in marks:
        if m > args.minutes:
            break
        wait = start + m * 60 - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        await sample(rec, page, profile, f"football-{m}min")
    await page.close()

    for name, url in CANDIDATES:
        tab = f"candidate-{name}"
        cand = await bridge.context.new_page()
        rec.attach(cand, tab)
        await pcr.goto_checked(cand, url, rec, bridge, tab)
        await asyncio.sleep(30)
        await sample(rec, cand, profile, tab)
        await cand.close()
        await asyncio.sleep(5)
    rec.flush_counts()
    rec.write("run.jsonl", {"ts": time.time(), "event": "end", "utc": pcr.utc(), "api_requests": rec.api_count,
                            "third_blocked": rec.third_blocked})
    await bridge.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--minutes", type=float, default=35)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
