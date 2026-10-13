"""
S6: bellek ölçüm aracı (scripts/push_memory_probe.py) ve sıralı koşturucusu (scripts/push_memory_matrix.py).

İki katman, hiçbiri SofaScore'a ya da ağa gitmez:
  - Tarayıcısız birim testleri: sahte /proc ağacında PSS/USS okuyucu ve süreç grupları, bayrakların birleştirilmesi
    (Scrapling'in gizlilik bayrakları korunur), yapılandırma planı, istek kuralları ve sıra, NATS karelerinin
    sayılması, özet ve başarısızlık nedenleri, gizlilik, motor algılama, matris sürücüsünün sırası.
  - `browser` testleri (CI'nin "browser bridge (offline)" işi): gerçek patchright + Chromium yerel sahte siteye
    karşı. Tarayıcının vekil sunucusu sahte sitedir: `.test` alan adları dışarıda çözülmez, https tüneli açılmaz;
    tek tünel sahte push sunucusunadır (NATS over WebSocket, 127.0.0.1). Sahte INFO karesi gerçek gibi istemci
    adresi ve sunucu adresleri taşır: hiçbiri kayda geçmemelidir.
"""
from __future__ import annotations

import argparse
import contextlib
import base64
import hashlib
import json
import os
import re
import select
import socket
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import pytest

pytest.importorskip("fcntl")  # araştırma betiklerinin ortak kilidi (Linux/macOS)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")


def _import_scripts():
    """Betikler içe aktarılırken ortam değişkeni koyar: test sürecinin ortamı değişmesin."""
    saved = dict(os.environ)
    sys.path.insert(0, SCRIPTS)
    try:
        import _research_common
        import push_memory_matrix
        import push_memory_probe
    finally:
        sys.path.remove(SCRIPTS)
        os.environ.clear()
        os.environ.update(saved)
    return push_memory_probe, push_memory_matrix, _research_common


probe, matrix, rc = _import_scripts()
ps = probe.ps
cs = probe.cs

from tests.test_research_privacy import scan  # noqa: E402

# conftest, `browser` olmayan testlerde köprünün başlatmasını keser; ürünün gerçek başlatması burada saklanır
PRODUCT_LAUNCH = cs.BrowserBridge._launch

FAKE_HOST = re.compile(r"(^|\.)fakescore\.test$")
SITE = "http://www.fakescore.test"
PUSH_HOST = "push.fakescore.test"
PUBLIC_CLIENT_IP = "93.184.216.34"  # sahte INFO'nun "istemci adresi": kayıtlarda asla görünmemeli
PUBLIC_SERVER_IP = "93.184.216.35"
SECRET_USER = "secret-user-value"


def args_for(*argv: str) -> argparse.Namespace:
    return probe.parser().parse_args(list(argv))


@pytest.fixture
def rate_file(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "_RATE_FILE", str(tmp_path / "ratelimit"))
    monkeypatch.setattr(rc, "MIN_REQUEST_GAP", 0.05)
    return tmp_path / "ratelimit"


# --- /proc okuyucu --------------------------------------------------------------------------------------------------

ROLLUP = """55d0c0000000-7ffd5a1ff000 ---p 00000000 00:00 0                          [rollup]
Rss:              {rss} kB
Pss:              {pss} kB
Pss_Dirty:            0 kB
Shared_Clean:         0 kB
Shared_Dirty:         0 kB
Private_Clean:    {pc} kB
Private_Dirty:    {pd} kB
Referenced:           0 kB
Anonymous:            0 kB
Private_Hugetlb:      0 kB
Swap:                 0 kB
SwapPss:              {swap} kB
"""


def fake_proc(root, pid: int, ppid: int, argv: List[str], rss: int, pss: int, private: int,
              *, smaps: bool = True) -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "stat").write_text(f"{pid} (some (odd) name) S {ppid} 1 1 0 -1 4194560 0 0 0 0\n")
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    if smaps:
        (d / "smaps_rollup").write_text(ROLLUP.format(rss=rss, pss=pss, pc=private // 2, pd=private - private // 2,
                                                      swap=3))


@pytest.fixture
def proc_tree(tmp_path):
    """Python (100) → node sürücüsü (101) → köprü Chrome'u (110) ve canlı Chrome (120), alt süreçleriyle."""
    root = tmp_path / "proc"
    bridge, live = "/p/run/bridge", "/p/run/bridge-live"
    fake_proc(root, 100, 1, ["python", "probe.py"], 50_000, 40_000, 30_000)
    fake_proc(root, 101, 100, ["/x/node", "/x/cli.js", "run-driver"], 60_000, 50_000, 45_000)
    fake_proc(root, 110, 101, ["/c/chrome", f"--user-data-dir={bridge}", "--headless"], 200_000, 120_000, 90_000)
    fake_proc(root, 111, 110, ["/c/chrome", "--type=zygote"], 30_000, 10_000, 5_000)
    fake_proc(root, 112, 111, ["/c/chrome --type=renderer --lang=tr "], 150_000, 100_000, 80_000)  # setproctitle
    fake_proc(root, 113, 110, ["/c/chrome", "--type=gpu-process"], 80_000, 40_000, 30_000)
    fake_proc(root, 120, 101, ["/c/chrome", f"--user-data-dir={live}"], 210_000, 130_000, 95_000)
    fake_proc(root, 121, 120, ["/c/chrome", "--type=zygote"], 30_000, 10_000, 5_000)
    fake_proc(root, 122, 121, ["/c/chrome", "--type=renderer"], 400_000, 300_000, 280_000)
    fake_proc(root, 123, 121, ["/c/chrome", "--type=renderer"], 100_000, 60_000, 50_000)
    fake_proc(root, 124, 121, ["/c/chrome", "--type=renderer"], 1, 1, 1, smaps=False)  # bu arada bitti
    fake_proc(root, 130, 100, ["/lp/lightpanda", "serve"], 90_000, 80_000, 70_000)
    fake_proc(root, 999, 1, ["/c/chrome", f"--user-data-dir={live}"], 999_999, 999_999, 999_999)  # başka ağaç
    return root, bridge, live


def test_rollup_reader_gives_rss_pss_and_uss(proc_tree):
    root, _, _ = proc_tree
    assert probe.read_rollup(122, str(root)) == {"rss": 400_000, "pss": 300_000, "uss": 280_000, "swap": 0,
                                                 "swap_pss": 3}
    assert probe.read_rollup(124, str(root)) is None
    assert probe.read_rollup(4242, str(root)) is None
    assert probe.read_proc(110, str(root)).ppid == 101  # parantezli süreç adı ppid'i bozmaz


def test_sampler_groups_processes_by_profile_and_counts_renderers(proc_tree):
    root, bridge, live = proc_tree
    sampler = probe.MemorySampler(100, {bridge: "bridge", live: "live"}, proc_root=str(root), pid_labels={130: "lp"})
    s = sampler.sample()
    g = s["groups"]
    assert set(g) == {"bridge", "live", "driver", "lp"}  # "/bridge" yolu "/bridge-live" ile karışmaz; 999 dahil değil
    assert g["bridge"]["processes"] == 4 and g["bridge"]["renderers"] == 1
    assert g["bridge"]["pss_mb"] == round(270_000 / 1024, 1)
    assert g["live"]["processes"] == 4 and g["live"]["renderers"] == 2  # bitmiş 124 sayılmaz
    assert g["live"]["uss_mb"] == round(430_000 / 1024, 1)
    assert g["driver"]["pss_mb"] == round(50_000 / 1024, 1)
    total = s["total"]  # tarayıcılar: sürücü ve diğerleri hariç
    assert total["pss_mb"] == round((270_000 + 500_000 + 80_000) / 1024, 1)
    assert total["rss_mb"] == round((460_000 + 740_000 + 90_000) / 1024, 1)
    assert total["kinds"] == {"browser": 3, "gpu-process": 1, "renderer": 3, "zygote": 2}
    assert s["python"] == {"pss_mb": round(40_000 / 1024, 1), "uss_mb": round(30_000 / 1024, 1),
                           "rss_mb": round(50_000 / 1024, 1)}


def test_process_kinds_cover_firefox_and_the_driver():
    assert probe.process_kind(["/f/camoufox-bin", "-contentproc", "-isForBrowser", "{x}", "123", "tab"]) == "renderer"
    assert probe.process_kind(["/f/camoufox-bin", "-contentproc", "{x}", "123", "rdd"]) == "content-other"
    assert probe.process_kind(["/f/camoufox-bin", "-profile", "/p"]) == "browser"
    assert probe.process_kind(["/usr/bin/node", "cli.js", "run-driver"]) == "driver"
    assert probe.process_kind(["/c/chrome", "--type=utility", "--utility-sub-type=network"]) == "utility"


# --- bayraklar ve plan ---------------------------------------------------------------------------------------------

def test_flag_presets_parse_and_reject_unknown_names():
    assert probe.parse_flags("renderer-limit=2, no-images") == {"renderer-limit": 2, "no-images": None}
    for bad in ("renderer-limit", "renderer-limit=0", "no-images=1", "turbo"):
        with pytest.raises(ValueError):
            probe.parse_flags(bad)


def test_switches_merge_into_the_existing_feature_js_and_blink_switches():
    base = ["--no-first-run", "--disable-features=AudioServiceOutOfProcess,TranslateUI",
            "--blink-settings=primaryHoverType=2,availableHoverTypes=2", "--disable-renderer-backgrounding",
            "--renderer-process-limit=4"]
    extra = probe.chromium_switches(probe.parse_flags(probe.FLAGS_DEFAULT))
    merged = probe.merge_switches(base, extra, remove=["--disable-renderer-backgrounding"])
    names = [probe._switch(a)[0] for a in merged]
    assert len(names) == len(set(names))  # her anahtar bir kez
    assert "--disable-features=AudioServiceOutOfProcess,TranslateUI,IsolateOrigins,site-per-process,SitePerProcess," \
           "BackForwardCache" in merged
    assert "--blink-settings=primaryHoverType=2,availableHoverTypes=2,imagesEnabled=false" in merged
    assert "--js-flags=--lite-mode --max-old-space-size=512" in merged
    assert "--renderer-process-limit=1" in merged and "--renderer-process-limit=4" not in merged
    assert "--disable-renderer-backgrounding" not in merged
    assert merged[0] == "--no-first-run"


def _plan(*argv: str) -> Any:
    return probe.build_plan(args_for(*argv))


def test_plan_resolution_and_rejected_combinations():
    p = _plan("--config", "shared-browser+multi-sport", "--no-bridge")
    assert p.layout == "shared" and p.with_bridge  # paylaşılan düzen köprüsüz olamaz
    assert p.sports == ["football", "basketball", "tennis"]
    p = _plan("--config", "baseline", "--config", "block-third-party,live-filter", "--sports", "tennis")
    assert (p.layout, p.rules, p.page_kind, p.sports) == ("separate", "strict", "live-filter", ["tennis"])
    assert _plan("--config", "flags").flags == probe.parse_flags(probe.FLAGS_DEFAULT)
    assert _plan("--config", "hidden", "--hidden-mode", "js").hidden_mode == "js"
    assert _plan("--config", "engine-camoufox+hidden", "--hidden-mode", "js").engine == "camoufox"
    for bad in (["--config", "engine-camoufox+engine-lightpanda"], ["--config", "engine-camoufox+flags"],
                ["--config", "engine-lightpanda+gc-pressure"], ["--config", "engine-camoufox+hidden"],
                ["--flags", "disable-gpu"], ["--config", "nope"]):
        with pytest.raises(ValueError):
            _plan(*bad)


def _product_session(tmp_path) -> Any:
    """Ürünün köprüsünün kurduğu oturum (başlatılmaz): bridge.py `_launch` ile aynı argümanlar."""
    return cs._session_class()(headless=True, solve_cloudflare=True, user_data_dir=str(tmp_path / "p"), proxy=None,
                               block_webrtc=True, timeout=60000, max_pages=2, retries=1)


def test_flags_reach_the_session_without_dropping_scraplings_stealth_switches(tmp_path):
    from scrapling.engines.constants import STEALTH_ARGS

    plain = _product_session(tmp_path)
    session = _product_session(tmp_path)
    applied = probe.apply_to_session(session, _plan("--config", "flags"), "bridge")
    args = list(session._browser_options["args"])
    for switch in STEALTH_ARGS:  # birleşen anahtarlar dışında her gizlilik bayrağı aynen
        name, _ = probe._switch(switch)
        if name not in probe.MERGED_SWITCHES:
            assert switch in args, switch
    assert "--webrtc-ip-handling-policy=disable_non_proxied_udp" in args
    features = next(a for a in args if a.startswith("--disable-features="))
    assert "AudioServiceOutOfProcess" in features and "SitePerProcess" in features and "BackForwardCache" in features
    blink = next(a for a in args if a.startswith("--blink-settings="))
    assert "primaryPointerType=4" in blink and "imagesEnabled=false" in blink
    assert "--renderer-process-limit=1" in args and "--disable-gpu" in args
    assert session._context_options["viewport"] == {"width": 400, "height": 300}
    assert session._context_options["screen"] == plain._context_options["screen"]  # ekran parmak izi aynı
    assert applied["viewport"] == {"width": 400, "height": 300} and applied["channel"] == "chromium"
    assert not [a for a in applied["args"] if "user-data-dir" in a]


def test_baseline_leaves_the_product_session_untouched(tmp_path):
    plain, session = _product_session(tmp_path), _product_session(tmp_path)
    probe.apply_to_session(session, _plan("--config", "baseline"), "bridge")
    assert session._browser_options == plain._browser_options
    assert session._context_options == plain._context_options
    assert plain._browser_options["channel"] == "chromium"  # bugünkü seçim: tam Chromium, yeni headless


def test_flags_target_and_headless_shell_channel(tmp_path, monkeypatch):
    plan = _plan("--config", "flags+engine-headless-shell", "--flags", "disable-gpu", "--flags-target", "live")
    monkeypatch.setattr(probe, "find_headless_shell", lambda: "/s6/chrome-headless-shell")
    bridge_session, live_session = _product_session(tmp_path), _product_session(tmp_path)
    probe.apply_to_session(bridge_session, plan, "bridge")
    probe.apply_to_session(live_session, plan, "live")
    assert "--disable-gpu" not in bridge_session._browser_options["args"]
    assert "--disable-gpu" in live_session._browser_options["args"]
    for s in (bridge_session, live_session):
        assert s._browser_options["channel"] == "chromium-headless-shell"
        assert s._browser_options["executable_path"] == "/s6/chrome-headless-shell"


def test_hidden_backgrounding_is_dropped_from_scrapling_and_playwright_defaults(tmp_path):
    session = _product_session(tmp_path)
    probe.apply_to_session(session, _plan("--config", "hidden", "--hidden-allow-backgrounding"), "live")
    assert not set(probe.BACKGROUNDING_SWITCHES) & set(session._browser_options["args"])
    assert set(probe.BACKGROUNDING_SWITCHES) <= set(session._browser_options["ignore_default_args"])
    assert "--enable-automation" in session._browser_options["ignore_default_args"]  # Scrapling'inkiler kalır


class _RecordingSession:
    made: List[Dict[str, Any]] = []

    def __init__(self, **kwargs):
        type(self).made.append(kwargs)
        self._browser_options = {"args": ["--a"], "channel": "chromium", "headless": True}
        self._context_options = {}

    async def start(self):
        raise RuntimeError("stop here")

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_the_probe_bridge_launches_through_the_product_launch(tmp_path, monkeypatch):
    """Ürünün `_launch`'ı çalışır; yalnızca oturum sınıfı plana göre sarılır (Lightpanda: cdp_url, proxy yok)."""
    monkeypatch.setattr(cs.BrowserBridge, "_launch", PRODUCT_LAUNCH)
    monkeypatch.setattr(probe.ProbeBridge, "_real_session_class", staticmethod(lambda: _RecordingSession))
    monkeypatch.setattr(cs, "_proxy_settings", lambda: {"server": "http://127.0.0.1:3128"})
    _RecordingSession.made = []
    args = args_for("--config", "flags", "--flags", "disable-gpu")
    b = probe.ProbeBridge(str(tmp_path / "bridge"), None, probe.build_plan(args), "bridge", args)
    with pytest.raises(RuntimeError, match="stop here"):
        await b._launch()
    made = _RecordingSession.made[-1]
    assert made == {"headless": True, "solve_cloudflare": True, "user_data_dir": str(tmp_path / "bridge"),
                    "proxy": {"server": "http://127.0.0.1:3128"}, "block_webrtc": True, "timeout": 60000,
                    "max_pages": 2, "retries": 1}  # S1-B9: yükleme başına tek gezinme
    assert b.applied["args"] == ["--a", "--disable-gpu"]
    assert cs._session_class is not b._session_factory  # geri alındı

    class FakeServer:
        endpoint = "ws://127.0.0.1:9"
        pid = None

    lp_args = args_for("--config", "engine-lightpanda")
    lp = probe.ProbeBridge(str(tmp_path / "lp"), None, probe.build_plan(lp_args), "bridge", lp_args)
    lp.lightpanda = FakeServer()
    with pytest.raises(RuntimeError, match="stop here"):
        await lp._launch()
    made = _RecordingSession.made[-1]
    assert made["cdp_url"] == "ws://127.0.0.1:9" and "proxy" not in made and "user_data_dir" not in made
    lp.cdp_driver = "playwright"
    assert isinstance(lp._session_factory()(headless=True), probe.PlainCdpSession)


# --- istekler ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url, kind, since, product, strict", [
    ("https://www.sofascore.com/tr/football", "document", 0, "continue", "continue"),
    ("https://www.sofascore.com/static/app.css", "stylesheet", 0, "continue", "abort"),
    ("https://www.sofascore.com/manifest.json", "manifest", 0, "continue", "abort"),
    ("https://www.sofascore.com/api/v1/sport/football/events/live", "fetch", 1, "throttle", "throttle"),
    ("https://www.sofascore.com/api/v1/sport/football/events/live", "fetch", 99, "abort", "abort"),
    ("https://www.sofascore.com/api/v1/token/refresh", "fetch", 99, "throttle", "throttle"),
    ("https://img.sofascore.com/team/1/image", "image", 0, "abort", "abort"),
    ("https://challenges.cloudflare.com/turnstile/v0/api.js", "script", 0, "continue", "continue"),
    ("https://challenges.cloudflare.com/x.css", "stylesheet", 0, "continue", "continue"),
    ("https://ads.example.net/x.js", "script", 0, "abort", "abort"),
    ("data:text/plain,x", "image", 0, "continue", "continue"),
])
def test_product_and_strict_rules(url, kind, since, product, strict):
    assert ps.route_action(url, kind, since) == product
    assert probe.strict_action(url, kind, since) == strict


class FakeRequest:
    def __init__(self, url, kind, page):
        self.url = url
        self.resource_type = kind
        self._page = page

    @property
    def frame(self):
        if self._page is None:
            raise RuntimeError("service worker")
        return type("Frame", (), {"page": self._page})()


class FakeRoute:
    def __init__(self, url, kind="fetch", page=None):
        self.request = FakeRequest(url, kind, page)
        self.calls: List[str] = []

    async def continue_(self):
        self.calls.append("continue")

    async def abort(self, *_):
        self.calls.append("abort")

    async def fallback(self):
        self.calls.append("fallback")


@pytest.mark.asyncio
async def test_shared_gate_leaves_the_bridge_page_alone_and_throttles_tabs(rate_file):
    slots = probe.SlotLog()
    gate = probe.RequestGate(slots, "product", all_pages=False)
    tab, bridge_page = object(), object()
    gate.tabs[id(tab)] = "football"
    gate.loaded(tab)
    api = "https://www.sofascore.com/api/v1/sport/football/events/live"
    routes = [FakeRoute(api, page=bridge_page), FakeRoute(api, page=tab), FakeRoute(api, page=tab),
              FakeRoute(api, page=None), FakeRoute("https://ads.example.net/a.js", "script", page=tab),
              FakeRoute("https://www.sofascore.com/a.css", "stylesheet", page=tab)]
    for r in routes:
        await gate.handle(r)
    assert [r.calls for r in routes] == [["fallback"], ["continue"], ["continue"], ["abort"], ["abort"],
                                         ["continue"]]
    assert gate.sent == {"football": 2} and slots.interval == {"page": 2}
    assert gate.blocked == {"api-after-load-window": 1, "third-party": 1}
    assert gate.patterns == {"/sport/{sport}/events/live": 2}
    assert len(slots.slots) == 2 and slots.slots[1][0] - slots.slots[0][0] >= rc.MIN_REQUEST_GAP * 0.9
    assert gate.take_interval() == {"page_sent": {"football": 2}, "page_blocked": 2}
    assert gate.take_interval() == {"page_sent": {}, "page_blocked": 0}


@pytest.mark.asyncio
async def test_separate_gate_routes_every_page_and_hands_back_on_errors(rate_file):
    gate = probe.RequestGate(probe.SlotLog(), "strict", all_pages=True)
    other = object()
    r = FakeRoute("https://www.sofascore.com/a.css", "stylesheet", page=other)
    await gate.handle(r)
    assert r.calls == ["abort"] and gate.blocked == {"type:stylesheet": 1}

    class Broken(FakeRoute):
        async def continue_(self):
            raise RuntimeError("Target page, context or browser has been closed")

    b = Broken("https://www.sofascore.com/tr", "document", page=other)
    await gate.handle(b)
    assert b.calls == ["fallback"] and gate.errors == 1


# --- kareler ---------------------------------------------------------------------------------------------------------

def nats_msg(subject: str, body: Dict[str, Any], sid: int = 1) -> bytes:
    payload = json.dumps(body).encode()
    return b"MSG %s %d %d\r\n" % (subject.encode(), sid, len(payload)) + payload + b"\r\n"


INFO = ('INFO {"server_id":"N1","version":"2.10.0","client_ip":"%s","connect_urls":["%s:9222"],"host":"%s"}\r\n'
        % (PUBLIC_CLIENT_IP, PUBLIC_SERVER_IP, PUBLIC_SERVER_IP)).encode()


class FakeWs:
    def __init__(self):
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler


def test_frames_are_counted_per_subject_with_handshakes_reconnects_and_silence():
    now = [1000.0]
    events: List[Any] = []
    tf = probe.TabFrames("football", "football", lambda kind, **f: events.append((kind, f)), clock=lambda: now[0])
    other, ws = FakeWs(), FakeWs()
    tf.on_websocket(other)
    tf.on_websocket(ws)
    other.handlers["framereceived"]("hello, not nats")  # sayfanın başka WebSocket'i
    other.handlers["framereceived"](nats_msg("sport.football", {"id": 9}))
    first = nats_msg("sport.football", {"id": 1, "homeScore.current": 1})
    ws.handlers["framereceived"](INFO + first[:20])  # MSG iki WebSocket karesine bölünmüş
    now[0] += 5
    ws.handlers["framereceived"](first[20:] + nats_msg("event.123", {"id": 123}) + nats_msg("odds.1.2", {"id": 1})
                                 + b"PING\r\n-ERR 'Slow Consumer'\r\n")
    assert tf.take_interval() == {"subjects": {"event.{id}": 1, "odds.{id}.{id}": 1, "sport.football": 1},
                                  "event_subjects": 1, "open": True}
    assert tf.take_interval()["subjects"] == {}
    now[0] += 40
    ws.handlers["close"]()
    tf.reload_started = now[0] - 1
    now[0] += 3
    ws2 = FakeWs()
    tf.on_websocket(ws2)
    ws2.handlers["framereceived"](INFO)
    now[0] += 2
    ws2.handlers["framereceived"](nats_msg("sport.football", {"id": 2}))
    totals = tf.totals()
    assert totals["subjects"] == {"event.{id}": 1, "odds.{id}.{id}": 1, "sport.football": 2}
    assert (totals["handshakes"], totals["reconnects"], totals["closes"], totals["pings"]) == (2, 1, 1, 1)
    assert totals["errors"] == {"Slow Consumer": 1}
    assert totals["longest_silence_s"] == 45.0  # son MSG (t=1005) → yeniden bağlanmadan sonraki MSG (t=1050)
    assert totals["reload_gaps_s"] == [4.0]
    opens = [f for k, f in events if k == "nats-open"]
    assert opens[1]["reconnect"] and opens[1]["gap_s"] == 3.0 and opens[1]["after_reload_s"] == 4.0
    assert ("nats-error", {"tab": "football", "text": "Slow Consumer"}) in events
    assert PUBLIC_CLIENT_IP not in json.dumps(events)


# --- özet ------------------------------------------------------------------------------------------------------------

def _row(t, phase, pss, uss=None, frames=None):
    total = {"pss_mb": pss, "uss_mb": uss if uss is not None else pss * 0.8, "rss_mb": pss * 1.5, "processes": 10,
             "renderers": 4}
    return {"t": t, "phase": phase, "total": total, "groups": {"live": total}, "python": {"pss_mb": 80.0},
            "frames": frames or {}}


def _summary(samples, tabs, gates=None, slots=(), max_silence=300.0, events=()):
    return probe.summarize(samples, list(events), tabs, _plan("--config", "baseline"), list(slots),
                           gates or {"turnstile": {"bridge": {"ok": True}}, "api": {"bridge": [{"status": 200}]}},
                           max_silence=max_silence, requests={}, started=0.0, ended=samples[-1]["t"])


def _tab_info(subjects, silence=10.0, opened_at=60.0):
    return {"football": {"sport": "football", "opened_at": opened_at, "open_error": None,
                         "totals": {"subjects": subjects, "longest_silence_s": silence, "handshakes": 1,
                                    "reconnects": 0}}}


def test_summary_phases_marginals_growth_and_frame_rates():
    samples = [_row(t, "launch-bridge", 50) for t in (0, 10)]
    samples += [_row(t, "bridge", 1000 + t % 3) for t in range(20, 60, 10)]
    samples += [_row(t, "opening-1", 1500) for t in (60,)]
    frames = {"football": {"subjects": {"sport.football": 30, "event.{id}": 10}}}
    samples += [_row(t, "tabs-1", 1800 + (t - 70) * 0.1, frames=frames) for t in range(70, 670, 10)]
    s = _summary(samples, _tab_info({"sport.football": 1800, "event.{id}": 600}))
    assert s["status"] == "ok" and s["reasons"] == []
    assert list(s["phases"]) == ["launch-bridge", "bridge", "opening-1", "tabs-1"]
    assert s["phases"]["bridge"]["pss_mb_median"] == 1001.5
    assert s["marginal"] == [{"from": "bridge", "to": "tabs-1", "pss_mb": round(s["phases"]["tabs-1"]["pss_mb_median"]
                                                                                 - 1001.5, 1),
                              "uss_mb": round(s["phases"]["tabs-1"]["uss_mb_median"]
                                              - s["phases"]["bridge"]["uss_mb_median"], 1)}]
    assert s["steady_phase"] == "tabs-1" and s["steady"]["growth_pss_mb_per_10min"] == 60.0  # 0,1 MB/sn
    assert s["steady"]["pss_mb_peak"] == round(1800 + 590 * 0.1, 1)
    assert s["frames"]["football"]["per_min_steady"]["sport.football"] == round(30 * 60 / (590 / 60), 2)
    assert s["frames"]["football"]["per_min_open"]["sport.football"] == round(1800 / (600 / 60), 2)


def test_summary_marks_lost_frames_silence_failed_gates_and_fast_requests():
    samples = [_row(t, "tabs-1", 1000) for t in range(0, 100, 10)]
    gates = {"turnstile": {"bridge": {"ok": True}, "live": {"ok": False}},
             "api": {"bridge": [{"status": 200}, {"status": 403}]}}
    s = _summary(samples, _tab_info({"event.{id}": 5}, silence=400.0), gates=gates,
                 slots=[(1.0, "page"), (1.2, "page")])
    assert s["status"] == "failed"
    assert s["reasons"] == ["no sport.football frames on tab football",
                            "tab football was silent for 400.0 s (> 300.0 s)", "challenge not solved on live",
                            "API read failed on bridge: [200, 403]", "requests closer than the shared gap: 0.200 s"]
    assert s["api"]["max_in_10s"] == 2 and s["api"]["min_gap_s"] == 0.2


def test_slope_needs_enough_points():
    assert probe.slope_per_10min([(0, 1), (10, 2)]) is None
    assert probe.slope_per_10min([(0, 1), (10, 2), (20, 3)]) is None  # 60 sn'den kısa
    assert probe.slope_per_10min([(0, 0), (60, 6), (120, 12)]) == 60.0


# --- gizlilik ve kayıt -------------------------------------------------------------------------------------------------

def test_recorder_and_error_texts_keep_no_addresses_or_credentials(tmp_path):
    rec = probe.Recorder(str(tmp_path / "research" / "run"))
    err = RuntimeError(f"net::ERR_PROXY at http://user:pw@{PUBLIC_SERVER_IP}:3128 ws://ws.sofascore.com:9222/x "
                       f"from {PUBLIC_CLIENT_IP}")
    rec.event("launch-failed", error=probe.error_text(err), info={"client_ip": PUBLIC_CLIENT_IP, "ip": "x"})
    rec.sample({"t": 1, "note": f"[2001:4860:4860::8888] {PUBLIC_CLIENT_IP}"})
    rec.write_json("summary.json", {"reasons": [PUBLIC_SERVER_IP]})
    text = "".join(p.read_text() for p in (tmp_path / "research" / "run").iterdir())
    assert PUBLIC_CLIENT_IP not in text and PUBLIC_SERVER_IP not in text and "pw@" not in text
    assert "ws.sofascore.com" not in text and "<websocket-url>" in text
    assert scan(str(tmp_path / "research")) == {"ipv4": [], "ipv6": [], "geo": []}


# --- motorlar --------------------------------------------------------------------------------------------------------

def test_engine_detection_never_downloads(tmp_path, monkeypatch):
    shell = tmp_path / "ms" / "chromium_headless_shell-1243" / "chrome-headless-shell-linux64" / "chrome-headless-shell"
    shell.parent.mkdir(parents=True)
    shell.write_text("#!/bin/sh\n")
    shell.chmod(0o755)
    old = tmp_path / "ms" / "chromium_headless_shell-1100" / "chrome-headless-shell-linux64" / "chrome-headless-shell"
    old.parent.mkdir(parents=True)
    old.write_text("#!/bin/sh\n")
    old.chmod(0o755)
    monkeypatch.setattr(probe, "_browsers_paths", lambda: [str(tmp_path / "ms")])
    monkeypatch.setattr(probe, "_patchright_revision", lambda: "1243")
    assert probe.find_headless_shell() == str(shell)
    monkeypatch.setattr(probe, "_patchright_revision", lambda: "9999")
    ok, why = probe.engine_available("headless-shell", args_for())
    assert not ok and "9999" in why
    ok, why = probe.engine_available("lightpanda", args_for("--lightpanda", str(tmp_path / "missing")))
    assert not ok and "not installed" in why

    def no_camoufox(_):
        raise ImportError("No module named 'camoufox'")

    monkeypatch.setattr(probe, "import_camoufox", no_camoufox)
    ok, why = probe.engine_available("camoufox", args_for())
    assert not ok and "camoufox-venv" in why


def test_camoufox_import_points_its_cache_at_the_engines_dir_only_while_importing(tmp_path, monkeypatch):
    seen = {}

    class FakeFinder:
        def find_spec(self, name, path=None, target=None):
            if name == "camoufox" or name.startswith("camoufox."):
                seen.setdefault("xdg", os.environ.get("XDG_CACHE_HOME"))
                raise ImportError("not here")
            return None

    monkeypatch.setenv("XDG_CACHE_HOME", "/original")
    monkeypatch.setattr(sys, "meta_path", [FakeFinder(), *sys.meta_path])
    for name in [m for m in sys.modules if m.startswith("camoufox")]:
        monkeypatch.delitem(sys.modules, name)
    with pytest.raises(ImportError):
        probe.import_camoufox(str(tmp_path / "xdg"))
    assert seen["xdg"] == str(tmp_path / "xdg") and os.environ["XDG_CACHE_HOME"] == "/original"


FAKE_LIGHTPANDA = """#!{python}
import json, os, socket, sys, time
args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
with open({record!r}, "w") as f:
    json.dump({{"argv": args, "telemetry": os.environ.get("LIGHTPANDA_DISABLE_TELEMETRY")}}, f)
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", port)); s.listen(4)
time.sleep(60)
"""


@pytest.mark.asyncio
async def test_lightpanda_server_runs_locally_without_telemetry(tmp_path):
    record = tmp_path / "record.json"
    exe = tmp_path / "lightpanda"
    exe.write_text(FAKE_LIGHTPANDA.format(python=sys.executable, record=str(record)))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    server = probe.LightpandaServer(str(exe), "http://u:p@127.0.0.1:3128", str(tmp_path / "jar.json"))
    await server.start()
    try:
        assert server.endpoint == f"ws://127.0.0.1:{server.port}" and server.pid
        seen = json.loads(record.read_text())
        assert seen["telemetry"] == "true"
        assert seen["argv"][:5] == ["serve", "--host", "127.0.0.1", "--port", str(server.port)]
        assert ["--load-resources", "iframe"] == seen["argv"][5:7]
        assert seen["argv"][7:] == ["--http-proxy", "http://u:p@127.0.0.1:3128", "--cookie-jar", str(tmp_path / "jar.json")]
    finally:
        server.stop()
    assert server.pid is None
    assert probe.proxy_url({"server": "http://h:1", "username": "u", "password": "p"}) == "http://u:p@h:1"
    assert probe.proxy_url(None) is None


@pytest.mark.asyncio
async def test_a_missing_engine_is_recorded_as_skipped_without_a_browser(tmp_path):
    args, plan = probe.prepare(["--config", "engine-lightpanda", "--lightpanda", str(tmp_path / "none"),
                                "--out-root", str(tmp_path / "out"), "--profile-root", str(tmp_path / "profiles"),
                                "--run-id", "skip-1"])
    summary = await probe.run_probe(args, plan)
    assert summary["status"] == "skipped" and "not installed" in summary["reasons"][0]
    out = tmp_path / "out" / "skip-1"
    assert json.loads((out / "summary.json").read_text())["status"] == "skipped"
    events = [json.loads(line)["event"] for line in (out / "events.jsonl").read_text().splitlines()]
    assert events == ["start", "skipped"]
    assert not (tmp_path / "profiles" / "skip-1").exists()


def test_only_one_probe_runs_at_a_time(tmp_path):
    with probe.single_probe(str(tmp_path)):
        with pytest.raises(SystemExit, match="another"):
            with probe.single_probe(str(tmp_path)):
                pass
    with probe.single_probe(str(tmp_path)):
        pass


def test_cli_dry_run_and_run_id_validation(capsys, tmp_path):
    assert probe.main(["--config", "flags", "--dry-run", "--run-id", "x1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["run_id"] == "x1" and out["plan"]["switches"][0] == "--renderer-process-limit=1"
    with pytest.raises(SystemExit):
        probe.prepare(["--run-id", "../escape"])


# --- matris sürücüsü ---------------------------------------------------------------------------------------------------

def _matrix_args(tmp_path, *extra):
    return matrix.parser().parse_args(["--out-root", str(tmp_path / "out"), "--profile-root", str(tmp_path / "prof"),
                                       "--matrix-id", "m1", "--minutes", "2", "--cooldown", "7", "--settle", "0",
                                       "--camoufox-python", str(tmp_path / "cf-python"), *extra])


def test_matrix_runs_configs_one_after_another_with_a_cool_down(tmp_path):
    (tmp_path / "cf-python").write_text("")
    calls: List[Any] = []

    def runner(cmd, timeout):
        calls.append(("run", cmd, timeout))
        run_id = cmd[cmd.index("--run-id") + 1]
        os.makedirs(tmp_path / "out" / run_id)
        (tmp_path / "out" / run_id / "summary.json").write_text(json.dumps(
            {"status": "ok", "reasons": [], "steady": {"pss_mb_median": 1234.5}}))
        return 0

    rows = matrix.run_matrix(_matrix_args(tmp_path, "--configs", "baseline; engine-camoufox ;flags+hidden"),
                             ["--flags", "disable-gpu"], runner=runner, sleep=lambda s: calls.append(("sleep", s)),
                             proc_root=str(tmp_path / "noproc"))
    assert [c[0] for c in calls] == ["run", "sleep", "run", "sleep", "run"]  # son koşudan sonra dinlenme yok
    assert all(c[1] == 7 for c in calls if c[0] == "sleep")
    runs = [c[1] for c in calls if c[0] == "run"]
    assert runs[0][0] == sys.executable and runs[1][0] == str(tmp_path / "cf-python")
    assert runs[2][runs[2].index("--config") + 1] == "flags+hidden" and runs[2][-2:] == ["--flags", "disable-gpu"]
    assert all(c[2] == (2 + matrix.OVERHEAD_MINUTES) * 60 for c in calls if c[0] == "run")
    assert [r["run_id"] for r in rows] == ["m1-01-baseline", "m1-02-engine-camoufox", "m1-03-flags+hidden"]
    assert rows[0]["steady_pss_mb_median"] == 1234.5 and rows[0]["status"] == "ok"
    log = (tmp_path / "out" / "matrix-m1.jsonl").read_text().splitlines()
    assert len(log) == 3


def test_matrix_dry_run_launches_nothing(tmp_path, capsys):
    rows = matrix.run_matrix(_matrix_args(tmp_path, "--dry-run"), [], runner=None, sleep=None)
    assert [r["config"] for r in rows] == list(matrix.DEFAULT_MATRIX)
    assert len(capsys.readouterr().out.splitlines()) == len(matrix.DEFAULT_MATRIX)
    assert not (tmp_path / "out").exists()


def test_matrix_finds_leftover_probe_browsers_only(tmp_path):
    root = "/home/u/.cache/sofascore_research/push_memory_probe"
    assert matrix.is_probe_browser(["/c/chrome", f"--user-data-dir={root}/r1/bridge"], root)
    assert matrix.is_probe_browser(["/f/camoufox-bin", "-profile", f"{root}/r1/bridge-live"], root)
    assert matrix.is_probe_browser(["/lp/lightpanda", "serve", "--cookie-jar", f"{root}/r1/bridge/j.json"], root)
    assert not matrix.is_probe_browser(["/v/bin/python3", "probe.py", "--profile-root", root], root)
    assert not matrix.is_probe_browser(["/c/chrome", "--user-data-dir=/home/u/.cache/sofascore_scraper/x"], root)
    proc = tmp_path / "proc"
    fake_proc(proc, 501, 1, ["/c/chrome", f"--user-data-dir={root}/r1/bridge"], 1, 1, 1)
    fake_proc(proc, 502, 1, ["/c/chrome", "--user-data-dir=/elsewhere"], 1, 1, 1)
    assert matrix.leftover_browsers(root, str(proc)) == [501]


# --- gerçek tarayıcı: yerel sahte site + sahte push sunucusu (ağa çıkmaz) -----------------------------------------

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

SPORT_PAGE = """<!doctype html><html><head><title>sport</title>
<link rel="stylesheet" href="/static/app.css"><script src="http://ads.thirdparty.test/ads.js"></script></head>
<body><button id="live">Canlı</button><img src="http://img.fakescore.test/team/1.png"><img src="/img/2.png">
<script>
const sport = location.pathname.split('/').pop();
for (let i = 0; i < 3; i++) fetch('/api/v1/sport/' + sport + '/events/live?i=' + i).catch(() => null);
setInterval(() => fetch('/api/v1/sport/' + sport + '/poll').catch(() => null), 700);
document.getElementById('live').onclick = () => fetch('/api/v1/sport/' + sport + '/events/live?filter=1');
function connect() {
  const ws = new WebSocket('ws://%(push)s/v1');
  ws.onopen = () => ws.send('CONNECT {"user":"%(user)s","pass":"p"}\\r\\nSUB sport.' + sport + ' 1\\r\\n'
                            + 'SUB event.123 2\\r\\nPING\\r\\n');
  ws.onclose = () => setTimeout(connect, 300);
}
connect();
</script></body></html>"""


def ws_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        return head + bytes([n]) + payload
    if n < 1 << 16:
        return head + bytes([126]) + n.to_bytes(2, "big") + payload
    return head + bytes([127]) + n.to_bytes(8, "big") + payload


def _recv_exact(sock, n: int) -> Optional[bytes]:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def read_client_frame(sock) -> Optional[tuple]:
    head = _recv_exact(sock, 2)
    if head is None:
        return None
    opcode, n = head[0] & 0x0F, head[1] & 0x7F
    if n == 126:
        n = int.from_bytes(_recv_exact(sock, 2) or b"\0\0", "big")
    elif n == 127:
        n = int.from_bytes(_recv_exact(sock, 8) or b"\0" * 8, "big")
    mask = _recv_exact(sock, 4) if head[1] & 0x80 else b"\0\0\0\0"
    payload = _recv_exact(sock, n) if n else b""
    if mask is None or payload is None:
        return None
    return opcode, bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


class FakePush:
    """
    NATS over WebSocket (127.0.0.1): INFO (istemci ve sunucu adresleriyle), SUB'lanan konulara her `period` saniyede
    MSG, ayrıca abone olunmamış odds.1; PING'e PONG. İlk bağlantı `drop_after` saniye sonra düşürülür (sayfa
    yeniden bağlanır).
    """

    def __init__(self, period: float = 0.15, drop_after: float = 2.5) -> None:
        self.period = period
        self.drop_after = drop_after
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(16)
        self.port = self.listener.getsockname()[1]
        self.lock = threading.Lock()
        self.connections = 0
        self.connects = 0
        self.closed = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while not self.closed:
            try:
                sock, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(sock,), daemon=True).start()

    def _handle(self, sock) -> None:
        wlock = threading.Lock()
        subs: Dict[str, int] = {}
        done = threading.Event()
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = sock.recv(4096)
                if not chunk:
                    return
                head += chunk
            headers = {k.strip().lower(): v.strip() for k, _, v in
                       (h.partition(":") for h in head.decode("latin-1").split("\r\n")[1:] if h)}
            accept = base64.b64encode(hashlib.sha1((headers["sec-websocket-key"] + GUID).encode()).digest()).decode()
            sock.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            with self.lock:
                self.connections += 1
                number = self.connections
            sock.sendall(ws_frame(INFO))

            def send_loop() -> None:
                started, n = time.monotonic(), 0
                while not done.is_set():
                    time.sleep(self.period)
                    n += 1
                    if number == 1 and time.monotonic() - started > self.drop_after:
                        with contextlib.suppress(OSError):
                            sock.shutdown(socket.SHUT_RDWR)
                        return
                    out = b""
                    for subject, sid in list(subs.items()):
                        out += nats_msg(subject, {"id": 1000 + n, "status.code": 6, "homeScore.current": n}, sid)
                    out += nats_msg("odds.1", {"id": 1}, 9)
                    try:
                        with wlock:
                            sock.sendall(ws_frame(out))
                    except OSError:
                        return

            threading.Thread(target=send_loop, daemon=True).start()
            while True:
                frame = read_client_frame(sock)
                if frame is None or frame[0] == 0x8:
                    return
                for line in frame[1].decode("utf-8", errors="replace").split("\r\n"):
                    verb, _, rest = line.partition(" ")
                    if verb == "CONNECT":
                        with self.lock:
                            self.connects += 1  # kimlik bilgisi saklanmaz
                    elif verb == "SUB":
                        subject, sid = rest.split(" ")
                        subs[subject] = int(sid)
                    elif verb == "PING":
                        with wlock:
                            sock.sendall(ws_frame(b"PONG\r\n"))
        except OSError:
            return
        finally:
            done.set()
            with contextlib.suppress(OSError):
                sock.close()

    def close(self) -> None:
        self.closed = True
        self.listener.close()


class FakeSite:
    def __init__(self, push: FakePush) -> None:
        self.push = push
        self.lock = threading.Lock()
        self.hits: List[tuple] = []  # (an, ana makine, yol)
        self.outbound: List[str] = []  # .test dışına gitmek isteyen her şey (reddedilir, hiçbiri makineden çıkmaz)
        self.tunnels: List[str] = []
        self.issued = 0


class _Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    rbufsize = 0  # tünel: CONNECT'in hemen ardından gelen baytlar okuma tamponunda kalmasın
    site: FakeSite

    def log_message(self, *args) -> None:
        pass

    def do_CONNECT(self) -> None:
        host = self.path.rsplit(":", 1)[0]
        with self.site.lock:
            self.site.tunnels.append(host)
        if host != PUSH_HOST:
            if not host.endswith(".test"):
                with self.site.lock:
                    self.site.outbound.append(host)
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            return
        upstream = socket.create_connection(("127.0.0.1", self.site.push.port))
        client = self.connection
        client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        try:
            while True:
                ready, _, _ = select.select([client, upstream], [], [], 30)
                if not ready:
                    break
                for s in ready:
                    data = s.recv(65536)
                    if not data:
                        raise OSError("closed")
                    (upstream if s is client else client).sendall(data)
        except OSError:
            pass
        finally:
            upstream.close()
            self.close_connection = True

    def _send(self, status: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        with contextlib.suppress(OSError):
            self.wfile.write(body)

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        host, path = parts.hostname or "", parts.path
        with self.site.lock:
            self.site.hits.append((time.monotonic(), host, path))
        if not host.endswith(".test"):
            with self.site.lock:
                self.site.outbound.append(host)
            self._send(502, b"offline", "text/plain")
            return
        if host != "www.fakescore.test":
            self._send(200, b"/* third party */", "application/javascript")
        elif path == "/captcha.html":
            with self.site.lock:
                self.site.issued += 1
                token = f"token-{self.site.issued}"
            self._send(200, b"<!doctype html><title>captcha</title>", "text/html",
                       {"Set-Cookie": f"sofa_captcha={token}; Path=/; Max-Age=3600"})
        elif path.startswith("/tr/"):
            body = SPORT_PAGE % {"push": PUSH_HOST, "user": SECRET_USER}
            self._send(200, body.encode(), "text/html; charset=utf-8")
        elif path.startswith("/api/"):
            self._send(200, json.dumps({"events": [{"id": 1}, {"id": 2}], "ip": PUBLIC_CLIENT_IP}).encode(),
                       "application/json")
        elif path.endswith(".css"):
            self._send(200, b"body{}", "text/css")
        else:
            self._send(200, b"User-agent: *", "text/plain")

    do_POST = do_GET


@pytest.fixture
def offline_site(monkeypatch, tmp_path, rate_file):
    push = FakePush()
    site = FakeSite(push)
    handler = type("Proxy", (_Proxy,), {"site": site})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("SOFASCORE_CLIENT__USE_PROXY", "true")
    monkeypatch.setenv("SOFASCORE_CLIENT__PROXY", origin)
    monkeypatch.delenv("SOFASCORE_CLIENT__BROWSER_HEADED", raising=False)
    monkeypatch.setattr(cs, "HOME_URL", SITE + "/tr")
    monkeypatch.setattr(cs, "CAPTCHA_URL", SITE + "/captcha.html")
    monkeypatch.setattr(cs, "_PROBE_URL", SITE + "/api/v1/probe")
    monkeypatch.setattr(cs, "_cached_token", None)
    monkeypatch.setattr(cs, "_cached_at", 0)
    monkeypatch.setattr(probe, "API_BASE", SITE + "/api/v1")
    monkeypatch.setattr(probe, "COOKIE_ORIGIN", SITE)
    monkeypatch.setattr(ps, "FIRST_PARTY_HOST", FAKE_HOST)
    monkeypatch.setattr(ps, "LOAD_WINDOW_SECONDS", 3.0)
    monkeypatch.setattr(rc, "MIN_REQUEST_GAP", 0.3)
    try:
        yield site
    finally:
        server.shutdown()
        server.server_close()
        push.close()
    # Chromium'un kendi arka plan istekleri (Google hizmetleri) vekilde reddedildi; hiçbiri SofaScore'a yönelmedi
    assert not [h for h in site.outbound if "sofascore" in h], site.outbound
    assert not [h for h in site.tunnels if h.endswith(".test") and h != PUSH_HOST], site.tunnels


def _browser_args(tmp_path, *extra: str):
    return probe.prepare(["--out-root", str(tmp_path / "research"), "--profile-root", str(tmp_path / "profiles"),
                          "--interval", "1", "--warmup", "2", "--tab-gap", "2", "--max-silence", "20",
                          "--live-filter-delay", "1", *extra])


def _read(out, name):
    return [json.loads(line) for line in (out / name).read_text().splitlines()]


def _assert_private(tmp_path, out):
    text = "".join(p.read_text() for p in out.iterdir())
    for secret in (PUBLIC_CLIENT_IP, PUBLIC_SERVER_IP, SECRET_USER, PUSH_HOST, "CONNECT"):
        assert secret not in text, secret
    assert scan(str(tmp_path / "research")) == {"ipv4": [], "ipv6": [], "geo": []}


@pytest.mark.browser
@pytest.mark.asyncio
async def test_baseline_offline_measures_memory_counts_frames_and_throttles(offline_site, tmp_path):
    pytest.importorskip("patchright.async_api")
    args, plan = _browser_args(tmp_path, "--config", "baseline", "--minutes", "0.25", "--run-id", "base-1")
    summary = await probe.run_probe(args, plan)
    out = tmp_path / "research" / "base-1"
    assert summary["status"] == "ok", summary["reasons"]
    assert summary["gates"]["turnstile"]["bridge"]["ok"] and summary["gates"]["turnstile"]["live"]["ok"]
    assert [r["status"] for r in summary["gates"]["api"]["bridge"]] == [200, 200]
    frames = summary["frames"]["football"]
    assert frames["subjects"]["sport.football"] > 10 and frames["subjects"]["event.{id}"] > 10
    assert frames["subjects"]["odds.{id}"] > 0
    assert frames["handshakes"] >= 2 and frames["reconnects"] >= 1  # sunucu ilk bağlantıyı düşürdü
    assert [p for p in summary["phases"] if not p.startswith("opening")] == ["launch-bridge", "bridge", "launch-live",
                                                                             "live-idle", "tabs-1"]
    assert [m["to"] for m in summary["marginal"]] == ["live-idle", "tabs-1"]
    steady = summary["steady"]
    assert steady["pss_mb_median"] > 50 and steady["uss_mb_median"] > 0 and steady["renderers_max"] >= 1
    samples = _read(out, "samples.jsonl")
    last = samples[-1]
    assert {"bridge", "live"} <= set(last["groups"]) and last["python"]["pss_mb"] > 0
    assert last["total"]["pss_mb"] <= last["total"]["rss_mb"]
    # Sayfanın API istekleri: yükleme penceresinde sıra alarak gitti, sonra kesildi; aralar ortak kilit kadar
    api = summary["api"]
    assert api["page_sent"]["football"] >= 3 and api["page_blocked"]["api-after-load-window"] >= 1
    assert api["page_blocked"]["third-party"] >= 1 and api["page_blocked"]["type:image"] >= 1
    assert api["min_gap_s"] >= 0.3 * 0.9 and api["by_source"]["bridge"] == 2
    hits = sorted(t for t, host, path in offline_site.hits if path.startswith("/api/v1/sport/"))
    assert len(hits) == api["slots_total"]  # sayılan = giden
    assert min(b - a for a, b in zip(hits, hits[1:], strict=False)) >= 0.3 * 0.8
    others = [h for h in offline_site.hits if h[1].endswith(".test") and h[1] != "www.fakescore.test"]
    assert not others and not [h for h in offline_site.hits if h[2].startswith("/img/")]  # üçüncü taraf, görsel yok
    launch = [e for e in _read(out, "events.jsonl") if e["event"] == "launch"]
    assert [e["browser"] for e in launch] == ["bridge", "live"] and launch[0]["switches"]["executable"]
    _assert_private(tmp_path, out)
    assert not (tmp_path / "profiles" / "base-1").exists()


@pytest.mark.browser
@pytest.mark.asyncio
async def test_shared_browser_with_flags_hidden_gc_reload_and_strict_rules_offline(offline_site, tmp_path):
    pytest.importorskip("patchright.async_api")
    args, plan = _browser_args(
        tmp_path, "--config", "shared-browser+multi-sport+flags+hidden+gc-pressure+reload-every+block-third-party"
        "+live-filter", "--sports", "football,tennis", "--minutes", "0.3", "--reload-minutes", "0.12",
        "--gc-minutes", "0.1", "--run-id", "shared-1")
    summary = await probe.run_probe(args, plan)
    out = tmp_path / "research" / "shared-1"
    events = _read(out, "events.jsonl")
    assert summary["status"] == "ok", summary["reasons"]
    assert set(summary["frames"]) == {"football-live", "tennis-live"}
    assert all(f["subjects"].get(f"sport.{f['sport']}", 0) > 5 for f in summary["frames"].values())
    assert list(summary["phases"])[-3:] == ["tabs-1", "opening-2", "tabs-2"]
    assert [m["to"] for m in summary["marginal"]] == ["tabs-1", "tabs-2"]
    assert "bridge" in summary["phases"]["tabs-2"]["groups_pss_mb_median"]
    assert "live" not in summary["phases"]["tabs-2"]["groups_pss_mb_median"]  # tek tarayıcı
    # Bayraklar köke ulaştı, Scrapling'in gizlilik bayraklarıyla birleşti; challenge ve API okuması yine çalıştı
    launch = next(e for e in events if e["event"] == "launch")
    sw = launch["switches"]
    assert sw["--renderer-process-limit"] == ["1"] and sw["--disable-gpu"] == [None]
    # Playwright'ın kendi listesi önde; Scrapling'inki bizimkilerle tek anahtarda, en sonda (Chromium sonuncuyu alır)
    assert len(sw["--disable-features"]) == 2
    assert {"AudioServiceOutOfProcess", "SitePerProcess", "BackForwardCache"} <= set(
        sw["--disable-features"][-1].split(","))
    # Playwright headless'ta kendi --blink-settings'ini de koyar (aynı fare ayarları); bizimki sonda, birleşik
    assert "imagesEnabled=false" in sw["--blink-settings"][-1] and "primaryHoverType=2" in sw["--blink-settings"][-1]
    assert len(sw["--js-flags"]) == 1 and "--lite-mode" in sw["--js-flags"][0]
    assert "--disable-blink-features=AutomationControlled" in launch["applied"]["args"]
    assert launch["applied"]["viewport"] == {"width": 400, "height": 300}
    assert summary["gates"]["turnstile"]["bridge"]["ok"]
    assert [r["status"] for r in summary["gates"]["api"]["bridge"]] == [200, 200]  # kurallar köprünün okumasına dokunmaz
    # Canlı filtre, gizleme (dondurmadan), GC ve yeniden yükleme
    assert [e["ok"] for e in events if e["event"] == "live-filter"][:2] == [True, True]
    hidden = [e for e in events if e["event"] == "hidden"]
    assert hidden and all(e["page_visibility"] == "hidden" for e in hidden)
    assert all("window" in e or "window_error" in e for e in hidden)
    gc = [e for e in events if e["event"] == "gc"]
    assert gc and gc[0]["ok"] == gc[0]["pages"] == 3 and not gc[0]["errors"]  # iki sekme + köprünün sayfası
    assert [e for e in events if e["event"] == "reload" and "error" not in e]
    assert any(f["reload_gaps_s"] for f in summary["frames"].values())
    assert summary["api"]["page_blocked"].get("type:stylesheet", 0) >= 1
    _assert_private(tmp_path, out)


@pytest.mark.browser
@pytest.mark.asyncio
async def test_headless_shell_engine_offline(offline_site, tmp_path):
    pytest.importorskip("patchright.async_api")
    if probe.find_headless_shell() is None:
        pytest.skip("chrome-headless-shell is not installed")
    args, plan = _browser_args(tmp_path, "--config", "engine-headless-shell+shared-browser", "--minutes", "0.15",
                               "--run-id", "shell-1")
    summary = await probe.run_probe(args, plan)
    assert summary["status"] == "ok", summary["reasons"]
    launch = next(e for e in _read(tmp_path / "research" / "shell-1", "events.jsonl") if e["event"] == "launch")
    assert launch["switches"]["executable"] == "chrome-headless-shell"
    assert launch["applied"]["channel"] == "chromium-headless-shell"


def _camoufox_ready() -> bool:
    return probe.engine_available("camoufox", args_for())[0]


@pytest.mark.browser
@pytest.mark.asyncio
async def test_camoufox_engine_offline(offline_site, tmp_path):
    """Yalnızca Camoufox'un sanal ortamında koşar (~/.cache/s6-engines/camoufox-venv); CI'de atlanır."""
    if not _camoufox_ready():
        pytest.skip("camoufox is not installed in this Python")
    args, plan = _browser_args(tmp_path, "--config", "engine-camoufox+shared-browser+hidden", "--hidden-mode", "js",
                               "--minutes", "0.2", "--run-id", "camoufox-1")
    summary = await probe.run_probe(args, plan)
    out = tmp_path / "research" / "camoufox-1"
    assert summary["status"] == "ok", summary["reasons"]
    launch = next(e for e in _read(out, "events.jsonl") if e["event"] == "launch")
    assert launch["engine"] == "camoufox" and launch["switches"]["executable"].startswith("camoufox")
    assert summary["frames"]["football"]["subjects"]["sport.football"] > 5
    assert summary["steady"]["pss_mb_median"] > 50 and summary["steady"]["renderers_max"] >= 1
    _assert_private(tmp_path, out)


@pytest.mark.browser
@pytest.mark.asyncio
async def test_lightpanda_feasibility_offline(offline_site, tmp_path):
    """Yapılabilirlik: koşu biter, her adım (sürücü, challenge, sekme, kareler) kaydedilir; sonuç beklenmez."""
    if probe.find_lightpanda() is None:
        pytest.skip("lightpanda is not installed")
    args, plan = _browser_args(tmp_path, "--config", "engine-lightpanda", "--minutes", "0.15",
                               "--run-id", "lp-1")
    summary = await probe.run_probe(args, plan)
    out = tmp_path / "research" / "lp-1"
    events = _read(out, "events.jsonl")
    assert summary["status"] in ("ok", "failed") and summary["plan"]["engine"] == "lightpanda"
    assert [e for e in events if e["event"] in ("launch", "launch-failed")]
    assert events[-1]["event"] == "end"
    _assert_private(tmp_path, out)
