"""
S6: tarayıcıyla push dinlemenin (`page` kaynağı, sofascore_scraper/services/live/push_source.py) ve yerleşik köprünün
(sofascore_scraper/client/bridge.py) belleğini düşürme yollarını ölçen araç. Her çağrı TEK bir yapılandırmayı
belirli bir süre çalıştırır; yapılandırmalar sırayla scripts/push_memory_matrix.py ile koşturulur.

    python scripts/push_memory_probe.py --config baseline --minutes 30
    python scripts/push_memory_probe.py --config shared-browser+multi-sport --minutes 30
    python scripts/push_memory_probe.py --config flags --flags renderer-limit=1,no-site-isolation --minutes 30
    python scripts/push_memory_probe.py --config engine-headless-shell --minutes 30
    ~/.cache/s6-engines/camoufox-venv/bin/python scripts/push_memory_probe.py --config engine-camoufox --minutes 30
    python scripts/push_memory_probe.py --config engine-lightpanda --minutes 5
    python scripts/push_memory_probe.py --config flags --dry-run      # plan (bayraklar, düzen) yazılır, tarayıcı açılmaz

Yapılandırmalar (`--config`, `+` ile ya da tekrarlanarak birleştirilir):

  baseline              ürünün `page` kaynağı olduğu gibi: yerleşik köprü (sessiz sekme) + ayrı `<profil>-live`
                        tarayıcı, spor başına bir sekme, bağlam düzeyinde ürünün istek kuralları (route_action).
  shared-browser        spor sekmeleri yerleşik köprünün tarayıcısında (API okumaları ve dinleme tek Chrome'da);
                        sekme başına marjinal maliyet. Kurallar bağlam düzeyinde ama sayfaya göre: köprünün kendi
                        sayfası ve challenge sayfaları dokunulmadan geçer (fallback), servis çalışanı istekleri
                        yükleme penceresi dışındaymış gibi işlenir.
  multi-sport           1, 2 ve 3 spor sekmesi (futbol, basketbol, tenis) sırayla; ek spor başına marjinal maliyet.
  flags                 Chromium bayrakları (`--flags`): renderer-limit=N, no-site-isolation, no-bfcache, lite-mode,
                        max-old-space=N, disable-gpu, no-images, small-viewport. Scrapling'in gizlilik bayrakları
                        korunur (aşağıya bakın); her tarayıcının challenge çözümü kapı olarak kaydedilir.
  block-third-party     daha sıkı istek kuralları: SofaScore ve Cloudflare challenge adresi dışındaki her şey ve
                        SofaScore'un yazı tipi/CSS/medya/görsel/beacon/manifest istekleri kesilir.
  live-filter           spor sayfası yerine canlı filtre (sayfa açılır, "Canlı" düğmesine basılır): sport.* de
                        geliyor mu, daha hafif mi.
  reload-every          sekme her `--reload-minutes` dakikada yeniden yüklenir: geri kazanılan bellek ve yeniden
                        yükleme sırasındaki kare boşluğu (yeniden bağlanmaya kadar geçen süre).
  gc-pressure           her `--gc-minutes` dakikada her sayfada CDP HeapProfiler.collectGarbage ve
                        Memory.simulatePressureNotification(critical).
  hidden                sekme gizli/arka planda işaretlenir (`--hidden-mode`: pencere küçültme ve/veya sayfanın
                        visibilityState'i); sayfa DONDURULMAZ, karelerin sürdüğü doğrulanır. Tarayıcının gördüğü
                        gerçek görünürlük her örnekte yazılır (çevrimdışı testte headless Chromium'da pencere
                        küçültme onu "visible" bıraktı; sayfanın kendi JS'i "hidden" görür).
  engine-headless-shell Chromium'un hafif `chrome-headless-shell` ikilisi (bugün Scrapling channel="chromium" verir:
                        tam Chromium, yeni headless kipi).
  engine-camoufox       Camoufox (Firefox tabanlı). Ayrı sanal ortamda çalışır (playwright<1.63 ister); Scrapling'in
                        Turnstile çözücüsü Camoufox bağlamında kullanılır.
  engine-lightpanda     Lightpanda (CDP sunucusu): yalnızca yapılabilirlik. Scrapling cdp_url ile bağlanır; olmazsa
                        düz Playwright connect_over_cdp denenir (çevrimdışı testte patchright new_context'te düştü,
                        Playwright bağlandı). Lightpanda bağlam başına tek sayfa açar: ayrı canlı tarayıcıda o
                        tarayıcının sayfası sekme olur. Sayfa WebSocket'i açar ama CDP'de WebSocket olayı vermez:
                        kareler sayfa içi sayaçla (`--page-counter`, burada varsayılan) da sayılır; yalnızca sayfada
                        görülen kare, ürünün dinleyemeyeceği anlamına gelir ve başarısızlık olarak yazılır. Gizlilik
                        beklentisi yok.

Ölçülenler (her `--interval` saniyede bir, samples.jsonl): tarayıcı süreçlerinin PSS/USS/RSS'i
(/proc/<pid>/smaps_rollup; RSS eski README rakamlarıyla karşılaştırma için), süreç ve renderer sayısı, grup başına
(köprü / canlı tarayıcı / sürücü), bu Python süreci; sekme başına konu konu gelen MSG kareleri, sekmenin JS yığını;
sayfaların SofaScore API istekleri (gönderilen, kesilen). Olaylar (events.jsonl): tarayıcı açılışı, challenge ve API
okuma kapıları, NATS el sıkışması/kopma/yeniden bağlanma, yeniden yükleme, GC, gizleme. Özet (summary.json):
evre başına ve kararlı evrede medyan/tepe PSS, 10 dk'da büyüme, dakikada kare, marjinal maliyetler, kapılar ve durum.

Başarısızlık: bir sekmede `sport.{spor}` karesi hiç gelmezse ya da `--max-silence` saniyeden uzun hiç MSG gelmezse,
challenge çözülemezse ya da API okuması başarısızsa yapılandırma "failed" olarak yazılır (kare kaybeden yapılandırma
başarısızdır). Kurulu olmayan motor "skipped" yazılır.

Hız: bu süreçten çıkan her SofaScore isteği (sayfaların kendi API istekleri ve köprünün okumaları) araştırma
betiklerinin ortak kilidinden (scripts/_research_common.py, ≤ 1 istek/sn) sıra alır; köprünün _wait_for_slot'u bu
kilide çevrilir (ürünün kısıtlayıcı dosyası ~/.cache/sofascore_scraper'a hiç yazılmaz). Sayfaların yükleme
penceresinden sonraki API istekleri ürün gibi kesilir.

Gizlilik (tests/test_research_privacy.py): NATS INFO gövdesi okunmaz (ürünün NatsReader'ı atar), giden kareler (CONNECT
kimlik bilgisi) dinlenmez, WebSocket adresi, proxy ve sunucu adresleri yazılmaz; hata metinleri IP ve URL kimlik
bilgisi maskelenerek kısaltılır. Kayıtlar research/push_memory/<run-id>/ altına yazılır.

Profiller: her koşu kendi geçici profillerini açar (`--profile-root`, varsayılan
~/.cache/sofascore_research/push_memory_probe/<run-id>/{bridge,bridge-live}) ve sonunda siler (`--keep-profiles`
hariç). Uygulamanın profili kullanılmaz. Aynı anda tek prob çalışır (profil kökünde kilit).

Ek motorlar yalnızca ~/.cache/s6-engines altına kurulur (scripts/push_memory_engines.sh, sürümler sabit):
chrome-headless-shell (patchright'ın Chromium sürümüyle aynı, PLAYWRIGHT_BROWSERS_PATH ile), Camoufox (ayrı sanal
ortam), Lightpanda (tek ikili; telemetrisi LIGHTPANDA_DISABLE_TELEMETRY ile kapatılır).

Gizlilik bayrakları hakkında (Scrapling 0.4.15): oturumun `extra_flags` seçeneği verilirse Scrapling kendi gizlilik
bayraklarını (STEALTH_ARGS, WebRTC politikası) bu listeyle DEĞİŞTİRİR (`config.extra_flags or extra_flags`). Prob bu
yüzden `extra_flags` kullanmaz: oturum ürünün argümanlarıyla kurulur, bayraklar başlatmadan önce oturumun
`_browser_options["args"]` listesine birleştirilir. `--disable-features`, `--js-flags` ve `--blink-settings` tek
anahtara birleştirilir (Chromium aynı anahtarın son değerini alır; ayrı yazılsa Scrapling'inkiler düşerdi).
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import dataclasses
import datetime as dt
import glob
import json
import os
import re
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

# Uygulamanın profili hiçbir yolda kullanılmasın: köprü modülü varsayılan profili içe aktarılırken okur
PROFILE_ROOT_DEFAULT = os.path.expanduser("~/.cache/sofascore_research/push_memory_probe")
os.environ.setdefault("SOFASCORE_LOG__LEVEL", "WARNING")
os.environ.setdefault("SOFASCORE_CLIENT__BROWSER_PROFILE", os.path.join(PROFILE_ROOT_DEFAULT, "unused"))

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
import _research_common as rc  # noqa: E402
from _all_sports_patterns import api_pattern  # noqa: E402
from explore_all_sports import redact  # noqa: E402

from sofascore_scraper.client import bridge as cs  # noqa: E402
from sofascore_scraper.services.live import push_source as ps  # noqa: E402

ROOT = rc.ROOT
OUT_ROOT_DEFAULT = os.path.join(ROOT, "research", "push_memory")
ENGINES_DIR_DEFAULT = os.path.expanduser("~/.cache/s6-engines")

# Testler bunları yerel sahte siteye çevirir (köprünün HOME_URL / CAPTCHA_URL'si gibi)
SITE = "https://www.sofascore.com"
API_BASE = SITE + "/api/v1"
COOKIE_ORIGIN = SITE
LIVE_FILTER_SELECTOR = 'button:has-text("Canlı")'

SPORTS_DEFAULT = ("football",)
SPORTS_MULTI = ("football", "basketball", "tennis")

# Sabit sürümler (scripts/push_memory_engines.sh aynı sürümleri kurar)
ENGINE_PINS = {
    "chromium": "patchright 1.63.0 + Scrapling 0.4.15 (channel=chromium: full Chromium, new headless)",
    "headless-shell": "chromium_headless_shell-1243 (patchright 1.63.0 revision)",
    "camoufox": "camoufox 0.5.8 (python) + browser v156.0.1-beta.36, playwright 1.62.0",
    "lightpanda": "lightpanda 1.0.0",
}
LIGHTPANDA_VERSION = "1.0.0"

CONFIGS = (
    "baseline", "shared-browser", "multi-sport", "flags", "block-third-party", "live-filter", "reload-every",
    "gc-pressure", "hidden", "engine-headless-shell", "engine-camoufox", "engine-lightpanda",
)
ENGINE_CONFIGS = {"engine-headless-shell": "headless-shell", "engine-camoufox": "camoufox",
                  "engine-lightpanda": "lightpanda"}
CHROMIUM_ENGINES = ("chromium", "headless-shell")

STATUS_OK, STATUS_FAILED, STATUS_SKIPPED, STATUS_ERROR = "ok", "failed", "skipped", "error"

# --- 1. bellek: /proc ---------------------------------------------------------------------------------------------

KB = 1024


@dataclasses.dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    argv: Tuple[str, ...]


def read_rollup(pid: int, proc_root: str = "/proc") -> Optional[Dict[str, int]]:
    """
    /proc/<pid>/smaps_rollup → kB: rss, pss, uss (Private_Clean + Private_Dirty + Private_Hugetlb), swap, swap_pss.
    Süreç bu arada bittiyse ya da okunamıyorsa None.
    """
    try:
        with open(os.path.join(proc_root, str(pid), "smaps_rollup"), encoding="ascii", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    fields: Dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if len(parts) >= 1 and parts[0].isdigit():
            fields[name.strip()] = int(parts[0])
    if "Rss" not in fields:
        return None
    return {
        "rss": fields.get("Rss", 0),
        "pss": fields.get("Pss", 0),
        "uss": fields.get("Private_Clean", 0) + fields.get("Private_Dirty", 0) + fields.get("Private_Hugetlb", 0),
        "swap": fields.get("Swap", 0),
        "swap_pss": fields.get("SwapPss", 0),
    }


def read_proc(pid: int, proc_root: str = "/proc") -> Optional[Proc]:
    try:
        with open(os.path.join(proc_root, str(pid), "stat"), encoding="utf-8", errors="replace") as f:
            stat = f.read()
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as f:
            raw = f.read()
    except OSError:
        return None
    try:
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
    except (IndexError, ValueError):
        return None
    argv = tuple(a.decode("utf-8", errors="replace") for a in raw.split(b"\0") if a)
    if len(argv) == 1 and " -" in argv[0]:
        # Chromium süreç başlığını yeniden yazar (setproctitle): argümanlar tek dizgide, boşlukla ayrılmış
        argv = tuple(a for a in argv[0].split(" ") if a)
    return Proc(pid, ppid, argv)


def process_table(proc_root: str = "/proc") -> Dict[int, Proc]:
    out: Dict[int, Proc] = {}
    try:
        names = os.listdir(proc_root)
    except OSError:
        return out
    for name in names:
        if name.isdigit():
            proc = read_proc(int(name), proc_root)
            if proc is not None:
                out[proc.pid] = proc
    return out


def descendants(table: Dict[int, Proc], root: int) -> List[int]:
    children: Dict[int, List[int]] = collections.defaultdict(list)
    for proc in table.values():
        children[proc.ppid].append(proc.pid)
    out, stack = [], list(children.get(root, ()))
    while stack:
        pid = stack.pop()
        out.append(pid)
        stack.extend(children.get(pid, ()))
    return sorted(out)


def process_kind(argv: Sequence[str]) -> str:
    """Chromium `--type=...`, Firefox `-contentproc ... tab`, Playwright sürücüsü (node), Lightpanda ya da tarayıcı kökü."""
    joined = " ".join(argv)
    for arg in argv:
        if arg.startswith("--type="):
            kind = arg[len("--type="):]
            return "renderer" if kind == "renderer" else kind
    if "-contentproc" in argv:
        return "renderer" if "tab" in argv[-2:] else "content-other"
    exe = os.path.basename(argv[0]) if argv else ""
    if exe == "node" or exe.startswith("node") or "run-driver" in joined:
        return "driver"
    if exe.startswith("lightpanda"):
        return "browser"
    return "browser"


def _owns(argv: Sequence[str], marker: str) -> bool:
    """Profil yolu argümanın kendisi ya da `--user-data-dir=<yol>` gibi bir anahtarın değeri mi (alt dizgi değil)."""
    return any(a == marker or a.endswith("=" + marker) for a in argv)


class MemorySampler:
    """
    Bu Python sürecinin alt ağacı → grup başına bellek. Grup, süreç ya da bir atası profil yoluyla (`markers`:
    yol → etiket) ya da süreç numarasıyla (`pid_labels`, ör. Lightpanda) eşleşince belirlenir; eşleşmeyen node
    süreçleri "driver", geri kalanı "other". Tarayıcı toplamı driver ve other dışındaki gruplardır.
    """

    def __init__(self, root_pid: int, markers: Optional[Dict[str, str]] = None, *, proc_root: str = "/proc",
                 pid_labels: Optional[Dict[int, str]] = None) -> None:
        self.root_pid = root_pid
        self.markers = dict(markers or {})
        self.pid_labels = dict(pid_labels or {})
        self.proc_root = proc_root

    def _label(self, table: Dict[int, Proc], pid: int) -> str:
        seen = set()
        cur: Optional[int] = pid
        while cur is not None and cur != self.root_pid and cur not in seen:
            seen.add(cur)
            if cur in self.pid_labels:
                return self.pid_labels[cur]
            proc = table.get(cur)
            if proc is None:
                break
            for marker, label in self.markers.items():
                if _owns(proc.argv, marker):
                    return label
            cur = proc.ppid
        proc = table.get(pid)
        return "driver" if proc is not None and process_kind(proc.argv) == "driver" else "other"

    def sample(self) -> Dict[str, Any]:
        table = process_table(self.proc_root)
        groups: Dict[str, Dict[str, Any]] = {}
        for pid in descendants(table, self.root_pid):
            mem = read_rollup(pid, self.proc_root)
            if mem is None:
                continue  # bu arada bitti
            label = self._label(table, pid)
            g = groups.setdefault(label, {"pss_kb": 0, "uss_kb": 0, "rss_kb": 0, "swap_pss_kb": 0, "processes": 0,
                                          "renderers": 0, "kinds": collections.Counter()})
            g["pss_kb"] += mem["pss"]
            g["uss_kb"] += mem["uss"]
            g["rss_kb"] += mem["rss"]
            g["swap_pss_kb"] += mem["swap_pss"]
            g["processes"] += 1
            kind = process_kind(table[pid].argv)
            g["kinds"][kind] += 1
            if kind == "renderer":
                g["renderers"] += 1
        out_groups = {label: _mb_group(g) for label, g in sorted(groups.items())}
        browser = [g for label, g in groups.items() if label not in ("driver", "other")]
        total = _mb_group({
            "pss_kb": sum(g["pss_kb"] for g in browser), "uss_kb": sum(g["uss_kb"] for g in browser),
            "rss_kb": sum(g["rss_kb"] for g in browser), "swap_pss_kb": sum(g["swap_pss_kb"] for g in browser),
            "processes": sum(g["processes"] for g in browser), "renderers": sum(g["renderers"] for g in browser),
            "kinds": sum((g["kinds"] for g in browser), collections.Counter()),
        })
        own = read_rollup(self.root_pid, self.proc_root) or {"pss": 0, "uss": 0, "rss": 0}
        return {"groups": out_groups, "total": total,
                "python": {"pss_mb": _mb(own["pss"]), "uss_mb": _mb(own["uss"]), "rss_mb": _mb(own["rss"])}}


def _mb(kb: float) -> float:
    return round(kb / KB, 1)


def _mb_group(g: Dict[str, Any]) -> Dict[str, Any]:
    return {"pss_mb": _mb(g["pss_kb"]), "uss_mb": _mb(g["uss_kb"]), "rss_mb": _mb(g["rss_kb"]),
            "swap_pss_mb": _mb(g["swap_pss_kb"]), "processes": g["processes"], "renderers": g["renderers"],
            "kinds": dict(sorted(g["kinds"].items()))}


# --- 2. bayraklar ve başlatma planı ---------------------------------------------------------------------------------

FLAG_NAMES = ("renderer-limit", "no-site-isolation", "no-bfcache", "lite-mode", "max-old-space", "disable-gpu",
              "no-images", "small-viewport")
FLAGS_DEFAULT = "renderer-limit=1,no-site-isolation,no-bfcache,lite-mode,max-old-space=512,disable-gpu,no-images," \
                "small-viewport"
SMALL_VIEWPORT = {"width": 400, "height": 300}
# Birden çok değeri tek anahtarda birleşen Chromium anahtarları ve ayırıcıları
MERGED_SWITCHES = {"--disable-features": ",", "--enable-features": ",", "--blink-settings": ",", "--js-flags": " "}
# `hidden --hidden-allow-backgrounding`: arka plana atmayı kapatan anahtarlar (Scrapling ve Playwright ikisi de koyar)
BACKGROUNDING_SWITCHES = ("--disable-renderer-backgrounding", "--disable-background-timer-throttling",
                          "--disable-backgrounding-occluded-windows")


def parse_flags(spec: str) -> Dict[str, Optional[int]]:
    """'renderer-limit=1,no-images' → {"renderer-limit": 1, "no-images": None}; bilinmeyen ad ValueError."""
    out: Dict[str, Optional[int]] = {}
    for item in (s.strip() for s in spec.split(",")):
        if not item:
            continue
        name, _, value = item.partition("=")
        if name not in FLAG_NAMES:
            raise ValueError(f"unknown flag preset {name!r} (known: {', '.join(FLAG_NAMES)})")
        if name in ("renderer-limit", "max-old-space"):
            if not value.isdigit() or int(value) < 1:
                raise ValueError(f"{name} needs a positive number, e.g. {name}=2")
            out[name] = int(value)
        elif value:
            raise ValueError(f"{name} takes no value")
        else:
            out[name] = None
    return out


def chromium_switches(flags: Dict[str, Optional[int]]) -> List[str]:
    """Bayrak ön ayarları → Chromium anahtarları (birleştirilecek olanlar ayrı ayrı; merge_switches birleştirir)."""
    out: List[str] = []
    if "renderer-limit" in flags:
        out.append(f"--renderer-process-limit={flags['renderer-limit']}")
    if "no-site-isolation" in flags:
        # "site-per-process" yaygın yazımdır (Puppeteer belgeleri); özelliğin adı SitePerProcess. İkisi de verilir.
        out += ["--disable-site-isolation-trials", "--disable-features=IsolateOrigins,site-per-process,SitePerProcess"]
    if "no-bfcache" in flags:
        out.append("--disable-features=BackForwardCache")
    js = []
    if "lite-mode" in flags:
        js.append("--lite-mode")
    if "max-old-space" in flags:
        js.append(f"--max-old-space-size={flags['max-old-space']}")
    if js:
        out.append("--js-flags=" + " ".join(js))
    if "disable-gpu" in flags:
        out.append("--disable-gpu")
    if "no-images" in flags:
        out.append("--blink-settings=imagesEnabled=false")
    return out


def _switch(arg: str) -> Tuple[str, Optional[str]]:
    name, eq, value = arg.partition("=")
    return name, (value if eq else None)


def merge_switches(base: Iterable[str], extra: Iterable[str], remove: Iterable[str] = ()) -> List[str]:
    """
    Taban anahtarlar + ekler. MERGED_SWITCHES'taki anahtarların değerleri tek anahtarda birleşir (tabanın değerleri
    önce, tekrar yok); başka bir anahtar aynı adla gelirse değeri değişir; `remove`daki adlar düşer. Sıra korunur.
    """
    removed = set(remove)
    out: List[str] = []
    index: Dict[str, int] = {}
    for arg in list(base) + list(extra):
        name, value = _switch(arg)
        if name in removed:
            continue
        if name in index:
            pos = index[name]
            if name in MERGED_SWITCHES and value is not None:
                sep = MERGED_SWITCHES[name]
                _, old = _switch(out[pos])
                parts = [p for p in (old or "").split(sep) if p]
                parts += [p for p in value.split(sep) if p and p not in parts]
                out[pos] = f"{name}={sep.join(parts)}"
            else:
                out[pos] = arg
            continue
        index[name] = len(out)
        out.append(arg)
    return out


@dataclasses.dataclass
class Plan:
    """Bir koşunun çözümlenmiş yapılandırması."""

    configs: List[str]
    engine: str = "chromium"
    layout: str = "separate"  # separate: köprü + ayrı canlı tarayıcı; shared: sekmeler köprünün tarayıcısında
    with_bridge: bool = True
    sports: List[str] = dataclasses.field(default_factory=lambda: list(SPORTS_DEFAULT))
    rules: str = "product"  # product: route_action; strict: block-third-party
    page_kind: str = "sport"  # sport | live-filter
    flags: Dict[str, Optional[int]] = dataclasses.field(default_factory=dict)
    flags_target: str = "all"  # all | live | bridge
    reload_minutes: Optional[float] = None
    gc_minutes: Optional[float] = None
    hidden_mode: Optional[str] = None  # window | js | both
    hidden_allow_backgrounding: bool = False

    @property
    def switches(self) -> List[str]:
        return chromium_switches(self.flags)

    @property
    def viewport(self) -> Optional[Dict[str, int]]:
        return dict(SMALL_VIEWPORT) if "small-viewport" in self.flags else None

    @property
    def remove_switches(self) -> List[str]:
        return list(BACKGROUNDING_SWITCHES) if self.hidden_allow_backgrounding else []

    def flags_apply_to(self, label: str) -> bool:
        return self.flags_target == "all" or self.flags_target == label

    def describe(self) -> Dict[str, Any]:
        return {"configs": self.configs, "engine": self.engine, "layout": self.layout, "with_bridge": self.with_bridge,
                "sports": self.sports, "rules": self.rules, "page_kind": self.page_kind, "flags": self.flags,
                "flags_target": self.flags_target, "switches": self.switches, "viewport": self.viewport,
                "remove_switches": self.remove_switches, "reload_minutes": self.reload_minutes,
                "gc_minutes": self.gc_minutes, "hidden_mode": self.hidden_mode, "engine_pin": ENGINE_PINS[self.engine]}


def split_configs(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        for name in (v.strip() for v in re.split(r"[+,]", value)):
            if name and name not in out:
                out.append(name)
    return out


def build_plan(args: argparse.Namespace) -> Plan:
    """`--config` adları ve seçenekler → Plan. Birleşemeyen seçimler ValueError."""
    configs = split_configs(args.config or ["baseline"])
    unknown = [c for c in configs if c not in CONFIGS]
    if unknown:
        raise ValueError(f"unknown config(s): {', '.join(unknown)} (known: {', '.join(CONFIGS)})")
    engines = [ENGINE_CONFIGS[c] for c in configs if c in ENGINE_CONFIGS]
    if len(engines) > 1:
        raise ValueError("choose one engine-* config per run")
    plan = Plan(configs=configs, engine=engines[0] if engines else "chromium")
    if "shared-browser" in configs:
        plan.layout = "shared"
    plan.with_bridge = plan.layout == "shared" or not args.no_bridge
    if args.sports:
        plan.sports = [s.strip() for s in args.sports.split(",") if s.strip()]
    elif "multi-sport" in configs:
        plan.sports = list(SPORTS_MULTI)
    if "block-third-party" in configs:
        plan.rules = "strict"
    if "live-filter" in configs:
        plan.page_kind = "live-filter"
    if "flags" in configs:
        plan.flags = parse_flags(args.flags if args.flags is not None else FLAGS_DEFAULT)
        plan.flags_target = args.flags_target
    elif args.flags:
        raise ValueError("--flags needs --config flags")
    if "reload-every" in configs:
        plan.reload_minutes = args.reload_minutes
    if "gc-pressure" in configs:
        plan.gc_minutes = args.gc_minutes
    if "hidden" in configs:
        plan.hidden_mode = args.hidden_mode
        plan.hidden_allow_backgrounding = args.hidden_allow_backgrounding
    if plan.engine not in CHROMIUM_ENGINES:
        chromium_only = [c for c in ("flags", "gc-pressure") if c in configs]
        if plan.hidden_mode in ("window", "both"):
            chromium_only.append("hidden (window mode; use --hidden-mode js)")
        if plan.hidden_allow_backgrounding:
            chromium_only.append("--hidden-allow-backgrounding")
        if chromium_only:
            raise ValueError(f"{', '.join(chromium_only)} need a Chromium engine, not {plan.engine}")
    return plan


def apply_to_session(session: Any, plan: Plan, label: str) -> Dict[str, Any]:
    """
    Kurulmuş ama başlatılmamış bir Scrapling oturumuna planı uygular (yalnızca Chromium motorları). Oturumun gizlilik
    bayrakları korunur; bayraklar birleştirilir. Ne yapıldığını (yol ve kimlik bilgisi olmadan) döndürür.
    """
    opts = getattr(session, "_browser_options", None)
    ctx = getattr(session, "_context_options", None)
    applied: Dict[str, Any] = {}
    if not isinstance(opts, dict) or not opts:  # cdp_url ile bağlanan oturumun başlatma seçeneği yoktur
        return applied
    extra = plan.switches if plan.flags_apply_to(label) else []
    remove = plan.remove_switches
    if extra or remove:
        opts["args"] = merge_switches(opts.get("args") or (), extra, remove)
    if remove:
        ignored = list(opts.get("ignore_default_args") or ())
        opts["ignore_default_args"] = ignored + [s for s in remove if s not in ignored]
    if plan.viewport and plan.flags_apply_to(label) and isinstance(ctx, dict):
        ctx["viewport"] = dict(plan.viewport)
        applied["viewport"] = dict(plan.viewport)
    if plan.engine == "headless-shell":
        opts["channel"] = "chromium-headless-shell"
        exe = find_headless_shell()
        if exe:
            opts["executable_path"] = exe
        applied["channel"] = "chromium-headless-shell"
    applied["args"] = [a for a in opts.get("args") or () if not a.startswith("--user-data-dir")]
    applied["channel"] = opts.get("channel")
    applied["headless"] = opts.get("headless")
    return applied


# --- 3. motorlar --------------------------------------------------------------------------------------------------


def _browsers_paths() -> List[str]:
    paths = []
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        paths.append(env)
    paths.append(os.path.join(ENGINES_DIR_DEFAULT, "ms-playwright"))
    paths.append(os.path.expanduser("~/.cache/ms-playwright"))
    return paths


def _patchright_revision() -> Optional[str]:
    try:
        import patchright

        path = os.path.join(os.path.dirname(patchright.__file__), "driver", "package", "browsers.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return next((b["revision"] for b in data["browsers"] if b["name"] == "chromium-headless-shell"), None)
    except Exception:
        return None


def find_headless_shell() -> Optional[str]:
    """patchright'ın Chromium sürümüyle eşleşen chrome-headless-shell ikilisi (önce ~/.cache/s6-engines)."""
    revision = _patchright_revision()
    for base in _browsers_paths():
        for path in sorted(glob.glob(os.path.join(base, "chromium_headless_shell-*", "chrome-headless-shell-*",
                                                  "chrome-headless-shell"))):
            if revision is None or f"chromium_headless_shell-{revision}{os.sep}" in path:
                if os.access(path, os.X_OK):
                    return path
    return None


def find_lightpanda(explicit: Optional[str] = None) -> Optional[str]:
    candidates = [explicit] if explicit else [
        os.path.join(ENGINES_DIR_DEFAULT, f"lightpanda-{LIGHTPANDA_VERSION}", "lightpanda"), shutil.which("lightpanda")]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def import_camoufox(cache_dir: str) -> Any:
    """
    Camoufox kurulu dizinini platformdirs'ten içe aktarılırken okur: XDG_CACHE_HOME yalnızca içe aktarma süresince
    `cache_dir`'e çevrilir (Camoufox'un ikilisi ~/.cache/s6-engines altında kalır). Kurulu değilse ImportError.
    """
    saved = os.environ.get("XDG_CACHE_HOME")
    os.environ["XDG_CACHE_HOME"] = cache_dir
    try:
        import camoufox.async_api  # noqa: F401
        from camoufox import pkgman
    finally:
        if saved is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = saved
    return pkgman


def engine_available(engine: str, args: argparse.Namespace) -> Tuple[bool, str]:
    """(kurulu mu, açıklama). Kurulu olmayan motor koşuyu "skipped" yapar; hiçbir şey indirilmez."""
    if engine == "chromium":
        try:
            import patchright  # noqa: F401
        except ImportError:
            return False, "patchright is not installed"
        return True, ENGINE_PINS["chromium"]
    if engine == "headless-shell":
        exe = find_headless_shell()
        if exe is None:
            return False, ("chrome-headless-shell is not installed for patchright revision "
                           f"{_patchright_revision()} (scripts/push_memory_engines.sh headless-shell)")
        return True, os.path.basename(os.path.dirname(os.path.dirname(exe)))
    if engine == "camoufox":
        try:
            pkgman = import_camoufox(args.camoufox_cache)
        except ImportError as e:
            return False, f"camoufox is not importable in this Python ({type(e).__name__}); run the probe with " \
                          "~/.cache/s6-engines/camoufox-venv/bin/python"
        try:
            path = pkgman.camoufox_path(download_if_missing=False)
            version = pkgman.installed_verstr()
        except Exception as e:
            return False, f"the Camoufox browser is not fetched ({type(e).__name__})"
        return (True, f"camoufox {version}") if path else (False, "the Camoufox browser is not fetched")
    if engine == "lightpanda":
        exe = find_lightpanda(args.lightpanda)
        if exe is None:
            return False, f"lightpanda is not installed (scripts/push_memory_engines.sh lightpanda, {LIGHTPANDA_VERSION})"
        return True, f"lightpanda at {os.path.basename(exe)}"
    return False, f"unknown engine {engine}"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def proxy_url(settings: Optional[Dict[str, str]]) -> Optional[str]:
    """Köprünün proxy ayarı (`_proxy_settings`) → URL (kimlik bilgisiyle; yalnızca süreç argümanı, diske yazılmaz)."""
    if not settings:
        return None
    parts = urlsplit(settings["server"])
    auth = ""
    if settings.get("username"):
        auth = settings["username"] + (":" + settings["password"] if settings.get("password") else "") + "@"
    return f"{parts.scheme}://{auth}{parts.netloc}"


class LightpandaServer:
    """`lightpanda serve` (yalnızca 127.0.0.1). Telemetri kapalı; çıktısı (adres taşıyabilir) diske yazılmaz."""

    def __init__(self, binary: str, proxy: Optional[str] = None, cookie_jar: Optional[str] = None) -> None:
        self.binary = binary
        self.proxy = proxy
        self.cookie_jar = cookie_jar  # profil dizininde: süreci prob kökünden tanıtır (matris sürücüsü)
        self.port = 0
        self.proc: Optional[subprocess.Popen] = None

    @property
    def pid(self) -> Optional[int]:
        return self.proc.pid if self.proc else None

    @property
    def endpoint(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def command(self) -> List[str]:
        cmd = [self.binary, "serve", "--host", "127.0.0.1", "--port", str(self.port), "--load-resources", "iframe"]
        if self.proxy:
            cmd += ["--http-proxy", self.proxy]
        if self.cookie_jar:
            cmd += ["--cookie-jar", self.cookie_jar]
        return cmd

    async def start(self, timeout: float = 20.0) -> None:
        self.port = _free_port()
        env = dict(os.environ, LIGHTPANDA_DISABLE_TELEMETRY="true")
        self.proc = subprocess.Popen(self.command(), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"lightpanda exited with code {self.proc.returncode}")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except OSError:
                await asyncio.sleep(0.2)
        raise RuntimeError("lightpanda did not open its CDP port")

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.proc = None


class PlainCdpSession:
    """
    Lightpanda için ikinci yol: düz Playwright connect_over_cdp (patchright'ın ek CDP çağrıları olmadan). Köprünün
    beklediği yüzey: start, context, fetch (captcha sayfası; Turnstile çözücüsü yok, yalnızca cookie beklenir), close.
    """

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self.playwright: Any = None
        self.browser: Any = None
        self.context: Any = None

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self.playwright = await async_playwright().start()
        try:
            self.browser = await self.playwright.chromium.connect_over_cdp(self.endpoint)
            self.context = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        except BaseException:
            await self.close()
            raise

    async def fetch(self, url: str, **_: Any) -> None:
        page = await self.context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(10)
        finally:
            with contextlib.suppress(Exception):
                await page.close()

    async def close(self) -> None:
        for closer in (getattr(self.browser, "close", None), getattr(self.playwright, "stop", None)):
            if closer is not None:
                with contextlib.suppress(Exception):
                    await closer()
        self.browser = self.playwright = self.context = None


def camoufox_session_class(base: Any, plan: Plan, cache_dir: str) -> Any:
    """
    Scrapling oturumu, ama tarayıcı Camoufox: `start` Camoufox'un kalıcı bağlamını açar; `fetch` ve Turnstile
    çözücüsü (Scrapling'in `_cloudflare_solver`'ı) aynı kalır. Playwright (patchright değil) kullanılır.
    """
    import_camoufox(cache_dir)
    from camoufox.async_api import AsyncNewBrowser
    from playwright.async_api import async_playwright

    class CamoufoxSession(base):
        async def start(self) -> None:
            if self.playwright:
                raise RuntimeError("Session has been already started")
            self.playwright = await async_playwright().start()
            try:
                options: Dict[str, Any] = {
                    "persistent_context": True, "user_data_dir": self._user_data_dir, "headless": True,
                    "block_webrtc": True, "disable_coop": True, "i_know_what_im_doing": True,
                }
                proxy = (self._context_options or {}).get("proxy")
                if proxy:
                    options["proxy"] = proxy
                if plan.viewport:
                    options["window"] = (plan.viewport["width"], plan.viewport["height"])
                self.context = await AsyncNewBrowser(self.playwright, **options)
                self._is_alive = True
            except BaseException:
                await self.playwright.stop()
                self.playwright = None
                raise

    return CamoufoxSession


class ProbeBridge(cs.BrowserBridge):
    """
    Köprünün kendisi (ensure_ready, challenge çözümü, fetch); başlatma ürünün `_launch`'ıdır, yalnızca oturum sınıfı
    plana göre sarılır: Chromium'da bayraklar başlatmadan önce oturuma eklenir, Lightpanda'da `cdp_url` verilir,
    Camoufox'ta oturumun `start`'ı Camoufox'u açar. Cookie'nin okunduğu adres COOKIE_ORIGIN'dir (testler çevirir).
    Sağlık sinyali yazılmaz.
    """

    def __init__(self, profile_dir: str, home_url: Optional[str], plan: Plan, label: str,
                 args: argparse.Namespace) -> None:
        super().__init__(profile_dir=profile_dir, home_url=home_url, report_health=False)
        self.plan = plan
        self.label = label
        self.args = args
        self.applied: Dict[str, Any] = {}
        self.lightpanda: Optional[LightpandaServer] = None
        self.cdp_driver = args.cdp_driver

    async def _token_from_context(self) -> Optional[str]:
        try:
            cookies = await self.context.cookies(COOKIE_ORIGIN)
        except Exception:
            return None
        return next((c["value"] for c in cookies if c.get("name") == "sofa_captcha" and c.get("value")), None)

    def _session_factory(self) -> Callable[..., Any]:
        base = self._real_session_class()
        plan = self.plan
        cls = camoufox_session_class(base, plan, self.args.camoufox_cache) if plan.engine == "camoufox" else base

        def make(**kwargs: Any) -> Any:
            if plan.engine == "lightpanda":
                assert self.lightpanda is not None
                if self.cdp_driver == "playwright":
                    return PlainCdpSession(self.lightpanda.endpoint)
                kwargs.pop("proxy", None)  # Lightpanda'nın proxy'si sunucu düzeyinde
                kwargs.pop("user_data_dir", None)
                kwargs["cdp_url"] = self.lightpanda.endpoint
            session = cls(**kwargs)
            if plan.engine in CHROMIUM_ENGINES:
                self.applied = apply_to_session(session, plan, self.label)
            return session

        return make

    _real_session_class = staticmethod(cs._session_class)

    async def _launch(self) -> None:
        if self.plan.engine == "lightpanda" and self.lightpanda is None:
            exe = find_lightpanda(self.args.lightpanda)
            if exe is None:
                raise RuntimeError("lightpanda is not installed")
            self.lightpanda = LightpandaServer(exe, proxy_url(cs._proxy_settings()),
                                               os.path.join(self.profile_dir, "lightpanda-cookies.json"))
            await self.lightpanda.start()
        original = cs._session_class
        cs._session_class = self._session_factory
        try:
            await super()._launch()
        finally:
            cs._session_class = original

    async def close(self) -> None:
        await super().close()
        if self.lightpanda is not None:
            self.lightpanda.stop()
            self.lightpanda = None


# --- 4. kareler ---------------------------------------------------------------------------------------------------


def subject_kind(subject: str) -> str:
    """event.123 → event.{id}; sport.football olduğu gibi; odds.1.2 → odds.{id}.{id}."""
    return re.sub(r"\d+", "{id}", subject)


class TabFrames:
    """
    Bir sekmenin GELEN NATS kareleri, ürünün NatsReader'ıyla (INFO gövdesi okunmaz, giden kare dinlenmez, adres
    yazılmaz). İlk işlemi INFO olmayan WebSocket NATS sayılmaz. Konu başına aralık ve toplam sayılar, el sıkışma,
    kopma, yeniden bağlanma ve en uzun sessizlik (iki MSG arası) tutulur.
    """

    def __init__(self, tab: str, sport: str, emit: Callable[..., None], clock: Callable[[], float] = time.time) -> None:
        self.tab = tab
        self.sport = sport
        self.emit = emit
        self.clock = clock
        self._readers: Dict[int, ps.NatsReader] = {}
        self._nats: Dict[int, bool] = {}
        self._open: set = set()
        self._next = 0
        self.interval: collections.Counter = collections.Counter()
        self.total: collections.Counter = collections.Counter()
        self.event_ids_interval: set = set()
        self.event_ids_total: set = set()
        self.pings = 0
        self.handshakes = 0
        self.reconnects = 0
        self.closes = 0
        self.errors: collections.Counter = collections.Counter()
        self.first_open_at: Optional[float] = None
        self.last_msg_at: Optional[float] = None
        self.last_close_at: Optional[float] = None
        self.longest_silence = 0.0
        self.reload_started: Optional[float] = None
        self.reload_gaps: List[float] = []
        self.page_total: collections.Counter = collections.Counter()  # sayfa içi sayaç (yalnızca --page-counter)
        self._page_last: Dict[str, int] = {}

    def page_counts(self, cumulative: Dict[str, Any]) -> Dict[str, int]:
        """Sayfa içi sayacın birikimli değerleri → bu aralıktaki artış (sayfa yeniden yüklenince sayaç sıfırlanır)."""
        delta: Dict[str, int] = {}
        for key, value in cumulative.items():
            if not isinstance(value, int):
                continue
            prev = self._page_last.get(key, 0)
            inc = value - prev if value >= prev else value
            if inc:
                delta[key] = inc
                self.page_total[key] += inc
        self._page_last = {k: v for k, v in cumulative.items() if isinstance(v, int)}
        return dict(sorted(delta.items()))

    def on_websocket(self, ws: Any) -> None:
        self._next += 1
        conn = self._next
        self._readers[conn] = ps.NatsReader()
        ws.on("framereceived", lambda payload: self.received(conn, payload))
        ws.on("close", lambda *_: self.closed(conn))

    def received(self, conn: int, payload: Any) -> None:
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
        now = self.clock()
        for op in ops:
            if op.kind == "INFO" and conn not in self._open:
                self._handshake(conn, now)
            elif op.kind in ("PING", "PONG"):
                self.pings += 1
            elif op.kind == "-ERR":
                self.errors[op.text] += 1
                self.emit("nats-error", tab=self.tab, text=op.text)
            elif op.kind == "MSG":
                self._msg(op.subject, now)
        if reader.broken:
            self.emit("nats-unreadable", tab=self.tab)
            self.closed(conn)
            self._nats[conn] = False

    def _handshake(self, conn: int, now: float) -> None:
        self._open.add(conn)
        self.handshakes += 1
        fields: Dict[str, Any] = {"tab": self.tab, "conn": conn, "handshakes": self.handshakes}
        if self.first_open_at is None:
            self.first_open_at = now
            self.last_msg_at = now  # sessizlik ilk el sıkışmadan sayılır
        else:
            self.reconnects += 1
            fields["reconnect"] = True
            if self.last_close_at is not None and len(self._open) == 1:
                fields["gap_s"] = round(now - self.last_close_at, 2)
        if self.reload_started is not None:
            gap = round(now - self.reload_started, 2)
            self.reload_gaps.append(gap)
            fields["after_reload_s"] = gap
            self.reload_started = None
        self.emit("nats-open", **fields)

    def _msg(self, subject: str, now: float) -> None:
        kind = subject_kind(subject)
        self.interval[kind] += 1
        self.total[kind] += 1
        if subject.startswith(ps.SUBJECT_EVENT):
            self.event_ids_interval.add(subject)
            self.event_ids_total.add(subject)
        if self.last_msg_at is not None:
            self.longest_silence = max(self.longest_silence, now - self.last_msg_at)
        self.last_msg_at = now

    def closed(self, conn: int) -> None:
        self._readers.pop(conn, None)
        if conn in self._open:
            self._open.discard(conn)
            self.closes += 1
            self.last_close_at = self.clock()
            self.emit("nats-close", tab=self.tab, conn=conn, open_left=len(self._open))

    @property
    def is_open(self) -> bool:
        return bool(self._open)

    def take_interval(self) -> Dict[str, Any]:
        out = {"subjects": dict(sorted(self.interval.items())), "event_subjects": len(self.event_ids_interval),
               "open": self.is_open}
        self.interval.clear()
        self.event_ids_interval.clear()
        return out

    def silence_now(self) -> float:
        if self.last_msg_at is None:
            return 0.0
        return max(self.longest_silence, self.clock() - self.last_msg_at)

    def totals(self) -> Dict[str, Any]:
        return {"subjects": dict(sorted(self.total.items())), "event_subjects": len(self.event_ids_total),
                "handshakes": self.handshakes, "reconnects": self.reconnects, "closes": self.closes,
                "pings": self.pings, "errors": dict(self.errors), "longest_silence_s": round(self.silence_now(), 1),
                "reload_gaps_s": self.reload_gaps, "websockets_seen": self._next,
                "page_counter": dict(sorted(self.page_total.items()))}


# --- 5. istekler ---------------------------------------------------------------------------------------------------

STRICT_BLOCKED_TYPES = ("image", "media", "font", "stylesheet", "texttrack", "beacon", "object", "imageset",
                        "csp_report", "manifest", "ping")


def strict_action(url: str, resource_type: str, since_load: float) -> str:
    """
    block-third-party: ürünün kuralı + SofaScore'un stil/medya/beacon/manifest istekleri de kesilir. Challenge
    adresine (Cloudflare) ürünle aynı davranılır (görsel/medya/yazı tipi hariç geçer): çözüm bozulmasın.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme in ("data", "blob"):
        return ps.ROUTE_CONTINUE
    if ps.FIRST_PARTY_HOST.search(host) and resource_type in STRICT_BLOCKED_TYPES:
        return ps.ROUTE_ABORT
    return ps.route_action(url, resource_type, since_load)


def block_reason(url: str, resource_type: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if not ps.FIRST_PARTY_HOST.search(host):
        return "third-party"
    if resource_type in ps.API_TYPES:
        return "api-after-load-window"
    return f"type:{resource_type}"


class SlotLog:
    """Ortak kilitten alınan her sıra: (an, kaynak). Hız denetimi özetten yapılır."""

    def __init__(self) -> None:
        self.slots: List[Tuple[float, str]] = []
        self.interval: collections.Counter = collections.Counter()

    async def take(self, source: str) -> None:
        await asyncio.to_thread(rc._wait_rate_slot)
        self.slots.append((time.time(), source))
        self.interval[source] += 1

    def take_interval(self) -> Dict[str, int]:
        out = dict(self.interval)
        self.interval.clear()
        return out


class RequestGate:
    """
    Sayfaların istekleri. `tabs`: kuralların uygulandığı sayfalar (id → sekme). Bağlamın başka sayfaları (köprünün
    kendi sayfası, Scrapling'in challenge sayfaları) `all_pages` değilse dokunulmadan geçer (fallback). Servis
    çalışanı isteği (sayfasız) yükleme penceresi dışındaymış gibi işlenir (ürün gibi).
    """

    def __init__(self, slots: SlotLog, rules: str = "product", *, all_pages: bool = True) -> None:
        self.slots = slots
        self.rule = strict_action if rules == "strict" else ps.route_action
        self.all_pages = all_pages
        self.loaded_at: Dict[int, float] = {}
        self.tabs: Dict[int, str] = {}
        self.sent: collections.Counter = collections.Counter()
        self.sent_interval: collections.Counter = collections.Counter()
        self.blocked: collections.Counter = collections.Counter()
        self.blocked_interval = 0
        self.patterns: collections.Counter = collections.Counter()
        self.errors = 0

    def loaded(self, page: Any) -> None:
        self.loaded_at[id(page)] = time.time()

    async def handle(self, route: Any) -> None:
        try:
            request = route.request
            try:
                page = request.frame.page
            except Exception:
                page = None  # servis çalışanı
            if page is not None and not self.all_pages and id(page) not in self.tabs:
                await route.fallback()
                return
            since = time.time() - self.loaded_at.get(id(page), 0.0) if page is not None \
                else ps.LOAD_WINDOW_SECONDS + 1
            action = self.rule(request.url, request.resource_type, since)
            tab = self.tabs.get(id(page), "sw" if page is None else "other")
            if action == ps.ROUTE_ABORT:
                reason = block_reason(request.url, request.resource_type)
                self.blocked[reason] += 1
                self.blocked_interval += 1
                await route.abort("blockedbyclient")
                return
            if action == ps.ROUTE_THROTTLE:
                await self.slots.take("page")
                self.sent[tab] += 1
                self.sent_interval[tab] += 1
                self.patterns[api_pattern(request.url, ps.FIRST_PARTY_HOST)] += 1
            await route.continue_()
        except Exception:
            self.errors += 1
            await ps.release_route(route)

    def take_interval(self) -> Dict[str, Any]:
        out = {"page_sent": dict(self.sent_interval), "page_blocked": self.blocked_interval}
        self.sent_interval.clear()
        self.blocked_interval = 0
        return out


# --- 6. kayıt ve özet ------------------------------------------------------------------------------------------------

ERROR_TEXT_MAX = 240


def clean_text(text: Any) -> str:
    """Hata metni: URL kimlik bilgisi ve bilinen gizli değerler maskelenir, IP'ler `redact`'la; kısaltılır."""
    from sofascore_scraper import redact as product_redact

    value = product_redact.redact_text(str(text))
    value = re.sub(r"wss?://[^\s'\"]+", "<websocket-url>", value)
    return value[:ERROR_TEXT_MAX]


def error_text(e: BaseException) -> str:
    return f"{type(e).__name__}: {clean_text(e)}"


class Recorder:
    """research/push_memory/<run-id>/ altına JSONL; her satır yazılmadan `redact`'tan geçer (IP ve konum)."""

    def __init__(self, out_dir: str) -> None:
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.events: List[Dict[str, Any]] = []
        self.samples: List[Dict[str, Any]] = []

    def _write(self, name: str, row: Dict[str, Any]) -> None:
        with open(os.path.join(self.out_dir, name), "a", encoding="utf-8") as f:
            f.write(json.dumps(redact(row), ensure_ascii=False, sort_keys=True) + "\n")

    def event(self, kind: str, **fields: Any) -> None:
        row = {"ts": round(time.time(), 3), "event": kind, **fields}
        self.events.append(row)
        self._write("events.jsonl", row)

    def sample(self, row: Dict[str, Any]) -> None:
        self.samples.append(row)
        self._write("samples.jsonl", row)

    def write_json(self, name: str, data: Any) -> None:
        rc.write_json(os.path.join(self.out_dir, name), redact(data))


def _median(values: Sequence[float]) -> Optional[float]:
    return round(statistics.median(values), 1) if values else None


def _peak(values: Sequence[float]) -> Optional[float]:
    return round(max(values), 1) if values else None


def slope_per_10min(points: Sequence[Tuple[float, float]]) -> Optional[float]:
    """En küçük kareler eğimi (MB/sn) × 600. En az 3 nokta ve 60 sn aralık ister."""
    if len(points) < 3:
        return None
    xs = [p[0] for p in points]
    if max(xs) - min(xs) < 60:
        return None
    mx = sum(xs) / len(xs)
    my = sum(p[1] for p in points) / len(points)
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return round(sum((x - mx) * (y - my) for x, y in points) / den * 600, 1)


def _phase_stats(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    pss = [r["total"]["pss_mb"] for r in rows]
    uss = [r["total"]["uss_mb"] for r in rows]
    rss = [r["total"]["rss_mb"] for r in rows]
    out: Dict[str, Any] = {
        "samples": len(rows), "pss_mb_median": _median(pss), "pss_mb_peak": _peak(pss),
        "uss_mb_median": _median(uss), "uss_mb_peak": _peak(uss), "rss_mb_median": _median(rss),
        "rss_mb_peak": _peak(rss),
        "processes_median": _median([r["total"]["processes"] for r in rows]),
        "renderers_max": max((r["total"]["renderers"] for r in rows), default=None),
        "python_pss_mb_median": _median([r["python"]["pss_mb"] for r in rows]),
    }
    groups = sorted({g for r in rows for g in r["groups"]})
    out["groups_pss_mb_median"] = {g: _median([r["groups"][g]["pss_mb"] for r in rows if g in r["groups"]])
                                   for g in groups}
    return out


def summarize(samples: Sequence[Dict[str, Any]], events: Sequence[Dict[str, Any]], tabs: Dict[str, Dict[str, Any]],
              plan: Plan, slots: Sequence[Tuple[float, str]], gate: Dict[str, Any], *, max_silence: float,
              requests: Dict[str, Any], started: float, ended: float) -> Dict[str, Any]:
    """Örnekler ve olaylar → özet ve durum (ok / failed: nedenleriyle)."""
    phases: List[str] = []
    for r in samples:
        if r["phase"] not in phases:
            phases.append(r["phase"])
    by_phase = {p: _phase_stats([r for r in samples if r["phase"] == p]) for p in phases}
    marginals = []
    measured = [p for p in phases if p.startswith(("bridge", "live-idle", "tabs-"))]  # açılış evreleri hariç
    for prev, cur in zip(measured, measured[1:], strict=False):
        a, b = by_phase[prev]["pss_mb_median"], by_phase[cur]["pss_mb_median"]
        if a is not None and b is not None:
            marginals.append({"from": prev, "to": cur, "pss_mb": round(b - a, 1),
                              "uss_mb": round((by_phase[cur]["uss_mb_median"] or 0)
                                              - (by_phase[prev]["uss_mb_median"] or 0), 1)})
    steady_phase = phases[-1] if phases else None
    steady = [r for r in samples if r["phase"] == steady_phase]
    steady_minutes = (steady[-1]["t"] - steady[0]["t"]) / 60 if len(steady) > 1 else 0.0
    steady_stats = _phase_stats(steady) if steady else {}
    steady_stats["growth_pss_mb_per_10min"] = slope_per_10min([(r["t"], r["total"]["pss_mb"]) for r in steady])
    steady_stats["growth_uss_mb_per_10min"] = slope_per_10min([(r["t"], r["total"]["uss_mb"]) for r in steady])
    steady_stats["minutes"] = round(steady_minutes, 1)

    reasons: List[str] = []
    frames: Dict[str, Any] = {}
    for tab, info in tabs.items():
        totals = info["totals"]
        sport_subject = f"{ps.SUBJECT_SPORT}{info['sport']}"
        open_minutes = max((ended - info["opened_at"]) / 60, 1e-9) if info.get("opened_at") else 0.0
        steady_counts: collections.Counter = collections.Counter()
        for r in steady:
            steady_counts.update((r.get("frames") or {}).get(tab, {}).get("subjects", {}))
        frames[tab] = {
            **totals, "sport": info["sport"],
            "per_min_open": {k: round(v / open_minutes, 2) for k, v in totals["subjects"].items()} if open_minutes else {},
            "per_min_steady": {k: round(v / steady_minutes, 2) for k, v in sorted(steady_counts.items())}
            if steady_minutes else {},
        }
        if not totals["subjects"].get(sport_subject):
            if (totals.get("page_counter") or {}).get(sport_subject):
                reasons.append(f"{sport_subject} frames reached the page of tab {tab} but not the browser's "
                               f"WebSocket events (the product cannot listen this way)")
            else:
                reasons.append(f"no {sport_subject} frames on tab {tab}")
        if totals["longest_silence_s"] > max_silence:
            reasons.append(f"tab {tab} was silent for {totals['longest_silence_s']} s (> {max_silence} s)")
        if info.get("open_error"):
            reasons.append(f"tab {tab} could not open: {info['open_error']}")
    if not tabs:
        reasons.append("no tab was opened")

    for name, result in gate.get("turnstile", {}).items():
        if not result.get("ok"):
            reasons.append(f"challenge not solved on {name}")
    for name, results in gate.get("api", {}).items():
        if not results or not all(r.get("status") == 200 for r in results):
            reasons.append(f"API read failed on {name}: {[r.get('status') for r in results]}")

    times = sorted(t for t, _ in slots)
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    max_10s = 0
    j = 0
    for i, t in enumerate(times):
        while times[j] < t - 10:
            j += 1
        max_10s = max(max_10s, i - j + 1)
    duration = max(ended - started, 1e-9)
    api = {"slots_total": len(times), "by_source": dict(collections.Counter(s for _, s in slots)),
           "rate_per_s": round(len(times) / duration, 3), "max_in_10s": max_10s,
           "min_gap_s": round(min(gaps), 3) if gaps else None, **requests}
    if gaps and min(gaps) < rc.MIN_REQUEST_GAP * 0.9:
        reasons.append(f"requests closer than the shared gap: {min(gaps):.3f} s")

    return {
        "status": STATUS_FAILED if reasons else STATUS_OK, "reasons": reasons, "plan": plan.describe(),
        "duration_min": round(duration / 60, 1), "phases": by_phase, "marginal": marginals,
        "steady_phase": steady_phase, "steady": steady_stats, "frames": frames, "gates": gate, "api": api,
        "reloads": [e for e in events if e.get("event") == "reload"][-5:],
        "gc_rounds": sum(1 for e in events if e.get("event") == "gc"),
    }


# --- 7. koşu -------------------------------------------------------------------------------------------------------

HIDE_JS = """() => {
    const hide = (proto) => {
        Object.defineProperty(proto, 'visibilityState', { configurable: true, get() { return 'hidden'; } });
        Object.defineProperty(proto, 'hidden', { configurable: true, get() { return true; } });
    };
    hide(Document.prototype);
    document.dispatchEvent(new Event('visibilitychange'));
    return document.visibilityState;
}"""
VISIBILITY_JS = "() => document.visibilityState"
JS_HEAP_JS = "() => (performance && performance.memory) ? performance.memory.usedJSHeapSize : null"
# Sayfa içi kare sayacı (yalnızca `--page-counter`; Lightpanda'da varsayılan): Lightpanda sayfanın WebSocket'ini açar
# ama CDP'de WebSocket olayı vermez, ürünün dinleme yolu kareleri göremez. Sayaç yalnızca gelen MSG satırlarının
# konusunu ({id} ile) ve INFO sayısını tutar; gövde, INFO içeriği ve adres okunmaz.
PAGE_COUNTER_JS = r"""(() => {
    if (window.__s6ws) return;
    const counts = {}; window.__s6ws = counts;
    const count = (text) => {
        for (const line of String(text).split('\r\n')) {
            if (line.startsWith('MSG ') || line.startsWith('HMSG ')) {
                const s = (line.split(' ')[1] || '').replace(/\d+/g, '{id}');
                counts[s] = (counts[s] || 0) + 1;
            } else if (line.startsWith('INFO ')) { counts.__info = (counts.__info || 0) + 1; }
        }
    };
    const Orig = window.WebSocket;
    if (!Orig) return;
    const Wrapped = function (...args) {
        const ws = new Orig(...args);
        ws.addEventListener('message', (m) => {
            const d = m.data;
            if (typeof d === 'string') count(d);
            else if (d instanceof ArrayBuffer) count(new TextDecoder().decode(d));
            else if (d && d.text) d.text().then(count, () => null);
        });
        return ws;
    };
    Wrapped.prototype = Orig.prototype;
    for (const k of ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED']) Wrapped[k] = Orig[k];
    window.WebSocket = Wrapped;
})()"""
PAGE_COUNTS_JS = "() => window.__s6ws ? Object.assign({}, window.__s6ws) : null"


async def eval_main(page: Any, script: str) -> Any:
    """Sayfanın kendi dünyasında (patchright varsayılanı yalıtılmış dünyadır); Playwright'ta parametre yoktur."""
    try:
        return await page.evaluate(script, isolated_context=False)
    except TypeError:
        return await page.evaluate(script)


@dataclasses.dataclass
class Tab:
    name: str
    sport: str
    host: str  # sekmenin açıldığı tarayıcının etiketi
    page: Any = None
    frames: Optional[TabFrames] = None
    opened_at: Optional[float] = None
    open_error: Optional[str] = None
    cdp: Any = None


class Probe:
    def __init__(self, args: argparse.Namespace, plan: Plan, out_dir: str, *, proc_root: str = "/proc") -> None:
        self.args = args
        self.plan = plan
        self.rec = Recorder(out_dir)
        self.proc_root = proc_root
        self.slots = SlotLog()
        self.bridges: Dict[str, ProbeBridge] = {}
        self.gates: Dict[str, Dict[str, Any]] = {"turnstile": {}, "api": {}}
        self.tabs: List[Tab] = []
        self.gate_by_host: Dict[str, RequestGate] = {}
        self.phase = "start"
        self.started = time.time()
        self.profile_dir = os.path.join(args.profile_root, args.run_id)
        self.sampler = MemorySampler(os.getpid(), proc_root=proc_root)
        self.page_counter = args.page_counter or plan.engine == "lightpanda"
        self._stop = asyncio.Event()

    # -- tarayıcılar

    def _profile(self, label: str) -> str:
        base = os.path.join(self.profile_dir, "bridge")
        return base if label == "bridge" else base + "-live"  # ürün gibi `<profil>-live`

    async def launch(self, label: str) -> Optional[ProbeBridge]:
        profile = self._profile(label)
        b = ProbeBridge(profile, ps.quiet_url(), self.plan, label, self.args)
        self.bridges[label] = b
        self.sampler.markers[profile] = label
        t0 = time.time()
        try:
            await b.ensure_ready()
        except Exception as e:
            if self.plan.engine == "lightpanda" and b.cdp_driver == "scrapling":
                self.rec.event("launch-failed", browser=label, driver="scrapling", error=error_text(e),
                               seconds=round(time.time() - t0, 1))
                b.cdp_driver = "playwright"
                b._launch_failed_at = 0.0
                t0 = time.time()
                try:
                    await b.ensure_ready()
                except Exception as e2:
                    self.rec.event("launch-failed", browser=label, driver="playwright", error=error_text(e2),
                                   seconds=round(time.time() - t0, 1))
                    self.gates["turnstile"][label] = {"ok": False, "error": error_text(e2)}
                    return None
            else:
                self.rec.event("launch-failed", browser=label, error=error_text(e), seconds=round(time.time() - t0, 1))
                self.gates["turnstile"][label] = {"ok": False, "error": error_text(e)}
                return None
        if b.lightpanda is not None and b.lightpanda.pid:
            self.sampler.pid_labels[b.lightpanda.pid] = label
        token = await b._token_from_context()
        self.gates["turnstile"][label] = {"ok": bool(token), "seconds": round(time.time() - t0, 1)}
        self.rec.event("launch", browser=label, engine=self.plan.engine, seconds=round(time.time() - t0, 1),
                       token=bool(token), driver=b.cdp_driver if self.plan.engine == "lightpanda" else None,
                       applied=b.applied, switches=self.root_switches(profile), version=await self._version(b))
        return b

    @staticmethod
    async def _version(b: ProbeBridge) -> Optional[str]:
        try:
            browser = getattr(b.context, "browser", None)
            return browser.version if browser is not None else None
        except Exception:
            return None

    def root_switches(self, profile: str) -> Dict[str, Any]:
        """Tarayıcı kök sürecinin etkin anahtarları (yalnızca ölçümü ilgilendirenler; yol yok)."""
        keep = ("--disable-features", "--enable-features", "--js-flags", "--blink-settings", "--renderer-process-limit",
                "--disable-site-isolation-trials", "--disable-gpu", "--headless", "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows")
        table = process_table(self.proc_root)
        for proc in table.values():
            if _owns(proc.argv, profile) and process_kind(proc.argv) == "browser":
                switches: Dict[str, Any] = {"executable": os.path.basename(proc.argv[0]) if proc.argv else None}
                for arg in proc.argv[1:]:
                    name, value = _switch(arg)
                    if name in keep:
                        switches.setdefault(name, []).append(value)
                return switches
        return {}

    async def api_read(self, label: str, sport: str, when: str) -> None:
        """Kapı: köprünün sayfasından bir API okuması (ortak kilitten sıra alır); 403 challenge'ta çöz ve yinele."""
        b = self.bridges.get(label)
        if b is None or b.page is None:
            return
        url = f"{API_BASE}/sport/{sport}/events/live"
        result: Dict[str, Any] = {"when": when, "sport": sport}
        try:
            res = await b._api_fetch(url, "no-store")
            if res.get("status") == 403 and "challenge" in (res.get("text") or ""):
                result["challenge"] = True
                await b.solve_challenge()
                res = await b._api_fetch(url, "no-store")
            result["status"] = res.get("status")
            data = res.get("data")
            if isinstance(data, dict) and isinstance(data.get("events"), list):
                result["live_events"] = len(data["events"])
        except Exception as e:
            result["status"] = None
            result["error"] = error_text(e)
        self.gates["api"].setdefault(label, []).append(result)
        self.rec.event("api-read", browser=label, **result)

    # -- sekmeler

    def _gate_for(self, host: str) -> RequestGate:
        if host not in self.gate_by_host:
            # Ayrı canlı tarayıcı: ürün gibi bağlamın her isteği; paylaşılan köprü: yalnızca sekmeler (ve sayfasızlar)
            self.gate_by_host[host] = RequestGate(self.slots, self.plan.rules, all_pages=host != "bridge")
        return self.gate_by_host[host]

    async def route_host(self, host: str) -> None:
        b = self.bridges[host]
        await b.context.route("**/*", self._gate_for(host).handle)

    async def open_tab(self, sport: str, host: str) -> Tab:
        b = self.bridges[host]
        gate = self._gate_for(host)
        tab = Tab(name=sport if self.plan.page_kind == "sport" else f"{sport}-live", sport=sport, host=host)
        tab.frames = TabFrames(tab.name, sport, self.rec.event)
        self.tabs.append(tab)
        t0 = time.time()
        try:
            try:
                page = await b.context.new_page()
            except Exception as e:
                # Lightpanda bağlam başına tek sayfa açar (Target.createTarget: TargetAlreadyLoaded): ayrı canlı
                # tarayıcıda o tarayıcının kendi (sessiz) sayfası sekme olur; köprünün sayfası API için kalmalı
                if self.plan.engine != "lightpanda" or host == "bridge" or b.page is None \
                        or any(t.page is b.page for t in self.tabs):
                    raise
                self.rec.event("tab-reuses-page", tab=tab.name, browser=host, reason=error_text(e))
                page = b.page
            tab.page = page
            gate.tabs[id(page)] = tab.name
            page.on("websocket", tab.frames.on_websocket)
            page.on("crash", lambda *_: self.rec.event("page-crash", tab=tab.name))
            page.on("close", lambda *_: self.rec.event("page-close", tab=tab.name))
            url = ps.sport_page_url(sport)
            if self.page_counter:
                try:
                    await page.add_init_script(PAGE_COUNTER_JS)
                except Exception as e:
                    self.rec.event("page-counter", tab=tab.name, ok=False, error=error_text(e))
            gate.loaded(page)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            if "captcha.html" in (page.url or ""):
                self.rec.event("challenge-on-open", tab=tab.name)
                await b.solve_challenge()
                gate.loaded(page)
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            tab.opened_at = time.time()
            self.rec.event("tab-open", tab=tab.name, sport=sport, browser=host, page_kind=self.plan.page_kind,
                           seconds=round(time.time() - t0, 1))
            if self.plan.page_kind == "live-filter":
                await self.live_filter(tab, gate)
            if self.plan.hidden_mode:
                await self.hide(tab)
        except Exception as e:
            tab.open_error = error_text(e)
            self.rec.event("tab-open-failed", tab=tab.name, sport=sport, browser=host, error=tab.open_error)
        return tab

    async def live_filter(self, tab: Tab, gate: RequestGate) -> None:
        await asyncio.sleep(self.args.live_filter_delay)
        try:
            gate.loaded(tab.page)  # filtrenin istekleri yeni bir yükleme penceresinde (bütçeden sıra alır)
            await tab.page.locator(self.args.live_filter_selector).first.click(timeout=10000)
            self.rec.event("live-filter", tab=tab.name, ok=True)
        except Exception as e:
            self.rec.event("live-filter", tab=tab.name, ok=False, error=error_text(e))

    async def _cdp(self, tab_or_page: Any, host: str) -> Any:
        b = self.bridges[host]
        if isinstance(tab_or_page, Tab):
            if tab_or_page.cdp is None:
                tab_or_page.cdp = await b.context.new_cdp_session(tab_or_page.page)
            return tab_or_page.cdp
        return await b.context.new_cdp_session(tab_or_page)

    async def hide(self, tab: Tab) -> None:
        """Sekmeyi gizli işaretler (dondurmaz): pencere küçültme (CDP Browser) ve/veya sayfanın visibilityState'i."""
        result: Dict[str, Any] = {"tab": tab.name, "mode": self.plan.hidden_mode}
        if self.plan.hidden_mode in ("window", "both"):
            try:
                cdp = await self._cdp(tab, tab.host)
                win = await cdp.send("Browser.getWindowForTarget")
                await cdp.send("Browser.setWindowBounds",
                               {"windowId": win["windowId"], "bounds": {"windowState": "minimized"}})
                result["window"] = "minimized"
            except Exception as e:
                result["window_error"] = error_text(e)
        if self.plan.hidden_mode in ("js", "both"):
            try:
                result["page_visibility"] = await eval_main(tab.page, HIDE_JS)
            except Exception as e:
                result["js_error"] = error_text(e)
        try:
            result["real_visibility"] = await tab.page.evaluate(VISIBILITY_JS)
        except Exception as e:
            result["real_visibility_error"] = error_text(e)
        self.rec.event("hidden", **result)

    # -- dönemsel işler

    async def reload_all(self) -> None:
        for tab in self.tabs:
            if tab.page is None or tab.page.is_closed():
                continue
            gate = self._gate_for(tab.host)
            t0 = time.time()
            tab.frames.reload_started = t0
            try:
                gate.loaded(tab.page)
                await tab.page.reload(wait_until="domcontentloaded", timeout=60000)
                self.rec.event("reload", tab=tab.name, seconds=round(time.time() - t0, 1))
                if self.plan.page_kind == "live-filter":
                    await self.live_filter(tab, gate)
                if self.plan.hidden_mode in ("js", "both"):
                    await self.hide(tab)
            except Exception as e:
                self.rec.event("reload", tab=tab.name, error=error_text(e))

    async def gc_all(self) -> None:
        pages: List[Tuple[str, Any, str]] = [(t.name, t, t.host) for t in self.tabs
                                             if t.page is not None and not t.page.is_closed()]
        for label, b in self.bridges.items():
            if b.page is not None and not b.page.is_closed():
                pages.append((f"{label}-page", b.page, label))
        ok, errors = 0, []
        for name, target, host in pages:
            try:
                cdp = await self._cdp(target, host)
                await cdp.send("HeapProfiler.collectGarbage")
                await cdp.send("Memory.simulatePressureNotification", {"level": "critical"})
                if not isinstance(target, Tab):
                    with contextlib.suppress(Exception):
                        await cdp.detach()
                ok += 1
            except Exception as e:
                errors.append(f"{name}: {error_text(e)}")
        self.rec.event("gc", pages=len(pages), ok=ok, errors=errors)

    async def periodic(self) -> None:
        jobs = []
        if self.plan.reload_minutes:
            jobs.append([self.plan.reload_minutes * 60, self.reload_all, None])
        if self.plan.gc_minutes:
            jobs.append([self.plan.gc_minutes * 60, self.gc_all, None])
        if not jobs:
            return
        for job in jobs:
            job[2] = time.time() + job[0]
        while not self._stop.is_set():
            await asyncio.sleep(0.5)
            for job in jobs:
                if time.time() >= job[2]:
                    job[2] = time.time() + job[0]
                    await job[1]()

    # -- örnekleme

    async def tab_metrics(self, tab: Tab) -> Dict[str, Any]:
        out: Dict[str, Any] = tab.frames.take_interval() if tab.frames else {}
        if tab.page is None or tab.page.is_closed():
            out["closed"] = True
            return out
        if self.page_counter and tab.frames is not None:
            with contextlib.suppress(Exception):
                counts = await asyncio.wait_for(eval_main(tab.page, PAGE_COUNTS_JS), 5)
                if isinstance(counts, dict):
                    out["page_subjects"] = tab.frames.page_counts(counts)
        try:
            heap = await asyncio.wait_for(tab.page.evaluate(JS_HEAP_JS), 5)
            out["js_heap_mb"] = round(heap / KB / KB, 1) if heap else None
        except Exception:
            out["js_heap_mb"] = None
        if self.plan.hidden_mode:
            with contextlib.suppress(Exception):
                out["visibility"] = await asyncio.wait_for(tab.page.evaluate(VISIBILITY_JS), 5)
        return out

    async def sample(self) -> None:
        mem = await asyncio.to_thread(self.sampler.sample)
        frames = {}
        for tab in self.tabs:
            frames[tab.name] = await self.tab_metrics(tab)
        requests: Dict[str, Any] = {"slots": self.slots.take_interval()}
        for host, gate in self.gate_by_host.items():
            requests[host] = gate.take_interval()
        self.rec.sample({"ts": round(time.time(), 3), "t": round(time.time() - self.started, 1), "phase": self.phase,
                         **mem, "frames": frames, "requests": requests})

    async def sampler_loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            try:
                await self.sample()
            except Exception as e:
                self.rec.event("sample-error", error=error_text(e))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), max(0.1, self.args.interval - (time.time() - t0)))

    def set_phase(self, name: str) -> None:
        self.phase = name
        self.rec.event("phase", phase=name)

    # -- akış

    async def run(self) -> Dict[str, Any]:
        args, plan = self.args, self.plan
        self.rec.event("start", run_id=args.run_id, utc=rc.utc_now(), plan=plan.describe(), minutes=args.minutes,
                       interval=args.interval, warmup=args.warmup, tab_gap=args.tab_gap,
                       versions=engine_versions(plan.engine, args))
        ok, detail = engine_available(plan.engine, args)
        if not ok:
            self.rec.event("skipped", engine=plan.engine, reason=detail)
            summary = {"run_id": args.run_id, "status": STATUS_SKIPPED, "reasons": [detail], "plan": plan.describe()}
            self.rec.write_json("summary.json", summary)
            return summary
        self.rec.event("engine", engine=plan.engine, detail=detail)
        original_slot = cs._wait_for_slot
        cs._wait_for_slot = lambda: self.slots.take("bridge")
        samp = asyncio.ensure_future(self.sampler_loop())
        periodic: Optional["asyncio.Future[None]"] = None
        try:
            if plan.with_bridge:
                self.set_phase("launch-bridge")
                if await self.launch("bridge") is None:
                    raise _Abort("the bridge browser did not start")
                await self.api_read("bridge", plan.sports[0], "start")
                self.set_phase("bridge")
                await self._wait(args.warmup)
            host = "bridge"
            if plan.layout == "separate":
                host = "live"
                self.set_phase("launch-live")
                if await self.launch("live") is None:
                    raise _Abort("the live browser did not start")
                if not plan.with_bridge:
                    await self.api_read("live", plan.sports[0], "start")
                self.set_phase("live-idle")
                await self._wait(args.warmup)
            await self.route_host(host)
            for i, sport in enumerate(plan.sports, 1):
                self.set_phase(f"opening-{i}")
                await self.open_tab(sport, host)
                self.set_phase(f"tabs-{i}")
                if i < len(plan.sports):
                    await self._wait(args.tab_gap)
            periodic = asyncio.ensure_future(self.periodic())
            await self._wait(args.minutes * 60)
            if plan.with_bridge:
                await self.api_read("bridge", plan.sports[0], "end")
        except _Abort as e:
            self.rec.event("abort", reason=str(e))
        except asyncio.CancelledError:  # SIGTERM (matris sürücüsünün zaman aşımı): kapat ve özeti yaz
            self.rec.event("abort", reason="terminated")
        except Exception as e:
            self.rec.event("error", error=error_text(e))
        finally:
            self._stop.set()
            for task in (samp, periodic):
                if task is not None:
                    with contextlib.suppress(Exception):
                        await task
            with contextlib.suppress(Exception):
                await self.sample_final()
            await self.shutdown()
            cs._wait_for_slot = original_slot
        return self.finish()

    async def sample_final(self) -> None:
        self._stop.clear()
        await self.sample()
        self._stop.set()

    async def _wait(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            await asyncio.sleep(min(1.0, end - time.time()))

    async def shutdown(self) -> None:
        for tab in self.tabs:
            if tab.page is not None:
                with contextlib.suppress(Exception):
                    await tab.page.close()
        for label in ("live", "bridge"):
            b = self.bridges.get(label)
            if b is None:
                continue
            with contextlib.suppress(Exception):
                await ps.stop_routing(b.context)
            with contextlib.suppress(Exception):
                await b.close()
        self.rec.event("end", utc=rc.utc_now())

    def finish(self) -> Dict[str, Any]:
        tabs = {t.name: {"sport": t.sport, "browser": t.host, "opened_at": t.opened_at, "open_error": t.open_error,
                         "totals": t.frames.totals() if t.frames else {}} for t in self.tabs}
        requests = {"page_sent": {}, "page_blocked": {}, "page_patterns": {}, "route_errors": 0}
        for gate in self.gate_by_host.values():
            for k, v in gate.sent.items():
                requests["page_sent"][k] = requests["page_sent"].get(k, 0) + v
            for k, v in gate.blocked.items():
                requests["page_blocked"][k] = requests["page_blocked"].get(k, 0) + v
            for k, v in gate.patterns.most_common(30):
                requests["page_patterns"][k] = requests["page_patterns"].get(k, 0) + v
            requests["route_errors"] += gate.errors
        summary = summarize(self.rec.samples, self.rec.events, tabs, self.plan, self.slots.slots, self.gates,
                            max_silence=self.args.max_silence, requests=requests, started=self.started,
                            ended=time.time())
        if any(e["event"] in ("abort", "error") for e in self.rec.events):
            summary["status"] = STATUS_FAILED if summary["status"] != STATUS_ERROR else summary["status"]
            summary["reasons"] = [e.get("reason") or e.get("error") for e in self.rec.events
                                  if e["event"] in ("abort", "error")] + summary["reasons"]
        summary["run_id"] = self.args.run_id
        self.rec.write_json("summary.json", summary)
        return summary


class _Abort(Exception):
    pass


def engine_versions(engine: str, args: argparse.Namespace) -> Dict[str, Any]:
    out: Dict[str, Any] = {"python": sys.version.split()[0], "pin": ENGINE_PINS.get(engine)}
    for module in ("scrapling", "patchright", "playwright"):
        try:
            from importlib.metadata import version

            out[module] = version(module)
        except Exception:
            out[module] = None
    if engine == "camoufox":
        try:
            from importlib.metadata import version

            out["camoufox"] = version("camoufox")
        except Exception:
            out["camoufox"] = None
    if engine == "lightpanda":
        exe = find_lightpanda(args.lightpanda)
        if exe:
            try:
                out["lightpanda"] = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=10,
                                                   env=dict(os.environ, LIGHTPANDA_DISABLE_TELEMETRY="true")
                                                   ).stdout.strip()[:40]
            except Exception:
                out["lightpanda"] = None
    return out


# --- 8. komut satırı ---------------------------------------------------------------------------------------------


@contextlib.contextmanager
def single_probe(profile_root: str):
    """Aynı anda tek prob: iki koşunun tarayıcıları aynı anda açılmaz (kilit süreç ölünce kendiliğinden kalkar)."""
    import fcntl

    os.makedirs(profile_root, mode=0o700, exist_ok=True)
    with open(os.path.join(profile_root, ".probe.lock"), "a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another push_memory_probe run holds the lock; runs never overlap") from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def default_run_id(configs: Sequence[str]) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%MZ")
    return f"{stamp}-{'+'.join(configs)}"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Measure the memory of browser-based push listening (one config per run).")
    ap.add_argument("--config", action="append", help=f"config name(s), joined by + or repeated: {', '.join(CONFIGS)}")
    ap.add_argument("--minutes", type=float, default=30.0, help="steady phase after the last tab opened (default 30)")
    ap.add_argument("--interval", type=float, default=10.0, help="seconds between samples (default 10)")
    ap.add_argument("--warmup", type=float, default=60.0, help="seconds per idle phase before the tabs (default 60)")
    ap.add_argument("--tab-gap", type=float, default=120.0, help="seconds between two sport tabs (default 120)")
    ap.add_argument("--sports", help="comma separated sports (default football; multi-sport: football,basketball,tennis)")
    ap.add_argument("--no-bridge", action="store_true", help="baseline without the resident bridge browser")
    ap.add_argument("--flags", help=f"flag presets for --config flags (default: {FLAGS_DEFAULT})")
    ap.add_argument("--flags-target", choices=("all", "live", "bridge"), default="all",
                    help="which browsers get the flags (default all; the challenge gate is checked on each)")
    ap.add_argument("--reload-minutes", type=float, default=10.0, help="reload-every period (default 10)")
    ap.add_argument("--gc-minutes", type=float, default=5.0, help="gc-pressure period (default 5)")
    ap.add_argument("--hidden-mode", choices=("window", "js", "both"), default="both")
    ap.add_argument("--hidden-allow-backgrounding", action="store_true",
                    help="hidden: drop the switches that keep background renderers at full priority")
    ap.add_argument("--live-filter-selector", default=LIVE_FILTER_SELECTOR)
    ap.add_argument("--live-filter-delay", type=float, default=10.0)
    ap.add_argument("--max-silence", type=float, default=300.0,
                    help="a tab without any MSG frame for longer than this fails the run (default 300 s)")
    ap.add_argument("--cdp-driver", choices=("scrapling", "playwright"), default="scrapling",
                    help="lightpanda: connect through Scrapling (patchright) or plain Playwright")
    ap.add_argument("--page-counter", action="store_true",
                    help="also count MSG subjects inside the page (WebSocket wrapper); on by default for lightpanda")
    ap.add_argument("--lightpanda", help="lightpanda binary (default ~/.cache/s6-engines/lightpanda-1.0.0/lightpanda)")
    ap.add_argument("--camoufox-cache", default=os.path.join(ENGINES_DIR_DEFAULT, "xdg"),
                    help="XDG cache dir holding the fetched Camoufox browser")
    ap.add_argument("--out-root", default=OUT_ROOT_DEFAULT)
    ap.add_argument("--run-id")
    ap.add_argument("--profile-root", default=PROFILE_ROOT_DEFAULT)
    ap.add_argument("--keep-profiles", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the resolved plan and launch nothing")
    return ap


def prepare(argv: Optional[Sequence[str]] = None) -> Tuple[argparse.Namespace, Plan]:
    ap = parser()
    args = ap.parse_args(argv)
    try:
        plan = build_plan(args)
    except ValueError as e:
        ap.error(str(e))
    args.run_id = args.run_id or default_run_id(plan.configs)
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", args.run_id):
        ap.error("--run-id may only hold letters, digits and . _ + -")
    return args, plan


async def run_probe(args: argparse.Namespace, plan: Plan, *, proc_root: str = "/proc") -> Dict[str, Any]:
    out_dir = os.path.join(args.out_root, args.run_id)
    probe = Probe(args, plan, out_dir, proc_root=proc_root)
    try:
        return await probe.run()
    finally:
        if not args.keep_profiles:
            shutil.rmtree(probe.profile_dir, ignore_errors=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args, plan = prepare(argv)
    if args.dry_run:
        print(json.dumps({"run_id": args.run_id, "plan": plan.describe(),
                          "engine_available": engine_available(plan.engine, args)}, indent=1))
        return 0
    with single_probe(args.profile_root):
        loop_summary = asyncio.run(_run_with_signals(args, plan))
    print(json.dumps({k: loop_summary.get(k) for k in ("run_id", "status", "reasons", "steady")}, indent=1,
                     ensure_ascii=False))
    return {STATUS_OK: 0, STATUS_SKIPPED: 0, STATUS_FAILED: 1}.get(loop_summary.get("status"), 2)


async def _run_with_signals(args: argparse.Namespace, plan: Plan) -> Dict[str, Any]:
    """SIGTERM (matris sürücüsü zaman aşımı) koşuyu düzgün kapatır: tarayıcılar kapanır, özet yazılır."""
    task = asyncio.ensure_future(run_probe(args, plan))
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        return await task
    except asyncio.CancelledError:
        return {"run_id": args.run_id, "status": STATUS_ERROR, "reasons": ["terminated"]}


if __name__ == "__main__":
    sys.exit(main())
