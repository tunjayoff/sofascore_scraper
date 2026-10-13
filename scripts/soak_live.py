"""
S2: canlı push kaynağının saatler süren ölçüm kaydı (soak). c projesi (bu kodu taşıyacak oran motoru) için uygulamanın
canlı yolunu olduğu gibi ölçer: push kaynağı (varsayılan `direct`: sofascore_scraper/services/live/direct_source.py;
`--source page` ile sayfa dinleme: push_source.py), hakem (arbiter.py), yoklama yedeği (poll_source.py) ve maç sayfası
okumaları için köprü (normal istemci, ortak istek bütçesi). Yeniden yazım yok: servisin kendi parçaları
(LiveService, DirectConnection/DirectSource ya da PageSource/BrowserPageOpener, SportArbiter, PollSource) çalışır;
script yalnızca onların kenarlarına kayıt kancaları takar.

    python scripts/soak_live.py --sports football,basketball,tennis --hours 6 [--source direct|page] [--run-id ID]
                                [--max-requests N] [--rate 1.0] [--list-share 0.5] [--priority-ids 1,2]
                                [--kill-every DAKİKA] [--kill-at DAKİKA] [--compare-credential-sports] [--out KLASÖR]

**`direct` açık seçimdir** (docs/deploy/watch.md, "The `direct` source"): sitenin kendi istemci kimlik bilgisini
sitenin istemcisi dışında kullanır; kimlik bilgisi ya da sunucu değişirse haber vermeden bozulabilir; IP adresinin
engellenmesine yol açabilir; kullanım şartları açısından gri alandır. Script yalnızca bilerek çalıştırılır.
Uygulamanın gönderdiği `direct` istemcisi bu koşuda ilk kez canlıda ölçülür: 2026-10-01'deki deney aiohttp ile
yapılmıştı, uygulamanınki standart kütüphaneyle yazılmış WebSocket istemcisidir (summary.json `notes`).

Nasıl kurulur:
  * Servis (`RecordingLiveService`, LiveService'in alt sınıfı) seçilen push kaynağıyla ve her sporun **bütün** canlı
    maçlarını kapsayan bir kapsamla (AllScope) çalışır: hakem, push bağlantısı ve yoklama kaynağının turları (canlı
    liste; düşen, bitişe yakın, takılı, gecikmiş maçların sayfaları) bugünkü gibidir. Servisin bitiş onayı kapalıdır
    (`confirm=False`): bitişin `/event/{id}` okumasını kaydedici yapar (aşağıda), iki kez okunmasın.
  * Push önde: hakemin güvenlik yoklaması (liste 120 sn'de bir ve her (yeniden) bağlanmada bir tur) ve ilgili her
    değişiklikte (durum sınıfı, skor, yeni canlı maç, bitiş, listeden düşme) bir `/event/{id}` okuması, c'nin yapacağı
    gibi. Okuma değişikliği henüz göstermiyorsa (CDN) servisin bitiş onayı gibi CONFIRM_RETRY_SECONDS sonra, en çok
    CONFIRM_ATTEMPTS kez yinelenir. Gecikme = tetikleyen kare ya da liste değişikliği → değişikliği gösteren ilk okuma.
  * Yoklama önde (push kopuk, sessiz ya da öldürüldü; sahibin c için kuralı): canlı liste 30 sn'de bir (hakemin
    `poll_interval`'ı) ve yoklama kaynağının kendi okumaları, ARTI her canlı maçın sayfası dakikada en az bir kez
    (FallbackReads): önce öncelikli id'ler (`--priority-ids`, varsayılan boş: "c fikstürüne bağlı" sinyali burada
    yok), sonra bitişe yakınlar, sonra en uzun süredir okunmayanlar. Dakikanın payı `60 × --rate × (1 −
    --list-share)` okumadır; sığmayan okuma atlanır ve sayılır (fallback_skipped), bir sonraki dakikaya taşınmaz.
    Dakika başına fallback.jsonl: spor başına canlı maç, yapılan okuma, atlanan, kapsama (dakika içinde okunan payı).
  * Kancalar: DirectConnection'ın alt sınıfı (oturum başı/sonu, yeniden deneme), bağlantı işlevinin sarmalayıcısı
    (el sıkışma, hat üstü işlemler; öldürme), sayfa açıcının sarmalayıcısı (`page`; öldürme = sayfayı kapatma), her
    sporun PushFeed'ini saran musluk (MSG kareleri; `page`'de PING/PONG/INFO/-ERR de), kimlik bilgisi okuyucusunun
    sarmalayıcısı, servisin `_system`'i (hakemin Switch'leri) ve istek işlevi.
  * Bütçe koşu başınadır (explore_all_sports.py gibi): `--max-requests` REST okumalarını (liste ve maç sayfası) sayar;
    varsayılan `--hours × 3600 × --rate` (ortalama ≤ 1 istek/sn). Bitince REST okuması yapılmaz (servisin isteği None
    alır, kaydedicinin okuması atlanır); push kaydı sürer. Her REST okuması `--rate` hızında sıra alır, sonra
    araştırma script'lerinin ortak kilidinden (scripts/_research_common.py: süreçler arası ≤ 1 istek/sn).
  * `--kill-every DAKİKA` / `--kill-at DAKİKA`: `direct`'te bağlantının soketi zorla kapatılır (shutdown), `page`'de
    sporların sayfaları kapatılır. Kaynak bunu olağan bir kopma gibi görür; hakem yoklamaya geçer, bağlantı ya da sayfa
    yeniden açılır, hakem push'a döner. Hepsi kayıtlıdır (connection.jsonl `kill`, leader.jsonl).
  * Bellek (resources.jsonl, 30 sn'de bir; kimlik bilgisi okunurken 5 sn'de bir): bu Python süreci, ana köprünün
    tarayıcısı (REST okumaları; ilk okumada açılır, koşu boyunca açık kalır) ve canlı profilin tarayıcısı (`page`
    sayfaları ya da `direct`'in kimlik bilgisi okuyucusu) ayrı ayrı, toplamı ve zirvesi. Tarayıcı süreçleri profil
    dizinine göre ayrılır; dizin yazılmaz.

Çıktı: `research/soak/<run-id>/` (ya da `--out`): frames.jsonl, connection.jsonl, leader.jsonl, reads.jsonl,
fallback.jsonl, resources.jsonl, summary.json (10 dakikada bir ve sonda). Servisin deposu (state.db) çıktı klasörüne
değil `--data-dir`'e (varsayılan ~/.cache/sofascore_research/soak_store/<run-id>) yazılır; ortak bütçenin dosyası
`SOFASCORE_CLIENT__THROTTLE_DIR` verilmezse ~/.cache/sofascore_research/throttle. Tarayıcı profili
~/.cache/sofascore_research/chrome_soak (canlı sayfalar ve kimlik bilgisi okuyucusu `<profil>-live`).

GİZLİLİK (kesin kural): push kimlik bilgisi, CONNECT gövdesi, sunucu adresleri/URL'leri, INFO gövdesi ve client_ip
hiçbir yere yazılmaz. Hat üstünde yalnızca işlemin adı ve konusu tutulur; INFO'dan yalnızca geldiği an; -ERR'den
yalnızca bilinen NATS hata metni (push_source.NatsReader). `page` kaynağı giden kareleri hiç dinlemez. Kimlik
bilgilerinin karşılaştırılması bellekte, rastgele tuzlu HMAC ile yapılır; ne kimlik bilgisi ne özeti yazılır,
yalnızca "same"/"different". Yazılan her satır sofascore_scraper/redact.py'den (çalışma anında öğrenilen kimlik bilgisi
dahil) ve araştırma maskesinden (explore_all_sports.redact: IPv4/IPv6, istemci konumu) geçer. tests/test_soak_live.py
ve tests/test_research_privacy.py kayıtları tarar.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import datetime as dt
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import signal
import socket
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

# 3.1'in okuduğu adlar (FX-32). Köprü profilini ilk içe aktarılışında okur: bu satırlar sofascore_scraper'dan önce
# çalışmalı. Araştırma uygulamanın profilini paylaşmaz.
os.environ.setdefault("SOFASCORE_LOG__LEVEL", "WARNING")
os.environ.setdefault(
    "SOFASCORE_CLIENT__BROWSER_PROFILE", os.path.expanduser("~/.cache/sofascore_research/chrome_soak")
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _research_common as rc  # noqa: E402
from explore_all_sports import redact as research_redact  # noqa: E402

from sofascore_scraper import redact as app_redact  # noqa: E402
from sofascore_scraper.services.live import direct_source as ds  # noqa: E402
from sofascore_scraper.services.live import supervisor as sv  # noqa: E402
from sofascore_scraper.services.live.arbiter import LEADER_POLL, SILENCE_SECONDS  # noqa: E402
from sofascore_scraper.services.live.push_source import NatsOp, NatsReader, PushFeed, frame_body  # noqa: E402
from sofascore_scraper.services.live.reducer import TERMINAL_CLASSES  # noqa: E402
from sofascore_scraper.status import StatusClass, classify_code, classify_status  # noqa: E402

ROOT = rc.ROOT
OUT = os.path.join(ROOT, "research", "soak")
STORE_ROOT = os.path.expanduser("~/.cache/sofascore_research/soak_store")
THROTTLE_DIR = os.path.expanduser("~/.cache/sofascore_research/throttle")
DEFAULT_SPORTS = ("football", "basketball", "tennis")
SOURCES = ("direct", "page")
DEFAULT_HOURS = 6.0
DEFAULT_RATE = 1.0  # istek/sn (bütçenin hızı)
DEFAULT_LIST_SHARE = 0.5  # hızın keşfe (liste ve yoklama kaynağının okumaları) ayrılan payı
FALLBACK_WINDOW_SECONDS = 60.0  # yoklama öndeyken her canlı maçın sayfası bu sürede en az bir kez
RESOURCE_SECONDS = 30.0
READING_RESOURCE_SECONDS = 5.0  # kimlik bilgisi okunurken (ikinci Chromium açık) örnekleme aralığı
SUMMARY_SECONDS = 600.0
MAX_PENDING_READS = 1000
TOP_GAPS = 5

FILES = ("frames", "connection", "leader", "reads", "fallback", "resources")
LIVE = StatusClass.LIVE.value
# Karede değeri yazılan alanlar (geri kalanların yalnızca yolu yazılır)
STATUS_VALUE_KEYS = ("status.code", "status.type")
SCORE_PREFIXES = ("homeScore.", "awayScore.")
CHANGE_TS = "changes.changeTimestamp"
# Tetikleyici türleri (bir okuma birden çok türü taşıyabilir)
KIND_FINISH, KIND_STATUS, KIND_NEW, KIND_DROPPED, KIND_SCORE = "finish", "status", "new_live", "dropped", "score"
CLIENT_NOTE = ("First live run of the shipped direct client: the standard-library WebSocket client in "
               "sofascore_scraper/services/live/direct_source.py. The probe measured on 2026-10-01 "
               "(scripts/push_direct_probe.py) used aiohttp.")
SCOPE_NOTE = ("The live service ran with the chosen push source and a scope of every live match of each sport: the "
              "arbiter, the push connection and the poll source's rounds are the service's own. Its confirmation "
              "read was off; the recorder reads /event/{id} once per relevant change instead, through the service's "
              "fetch, and while polling leads it reads every live match's page at least once a minute (fallback).")
PAGE_NOTE = ("Source 'page': the live browser listens to the push connection of SofaScore's own sport pages "
             "(push_source.py); a kill closes the pages.")
LIST_PATH = re.compile(r"^/sport/([a-z0-9-]+)/events/live$")
EVENT_PATH = re.compile(r"^/event/(\d+)$")


# --- maskeleme ve kayıt ----------------------------------------------------------------------------------------


def scrub(obj: Any) -> Any:
    """Yazılacak her şey: uygulamanın maskesi (bilinen ve çalışma anında öğrenilen gizli değerler) + araştırmanınki."""
    return research_redact(app_redact.redact_obj(obj))


def utc(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


def describe(values: Sequence[float]) -> Dict[str, Any]:
    """n, medyan, p90, en küçük, en büyük (3 basamak)."""
    vals = sorted(float(v) for v in values)
    if not vals:
        return {"n": 0}
    p90 = vals[min(len(vals) - 1, int(round(0.9 * (len(vals) - 1))))]
    return {"n": len(vals), "median": round(statistics.median(vals), 3), "p90": round(p90, 3),
            "max": round(vals[-1], 3), "min": round(vals[0], 3)}


class Recorder:
    """Koşunun JSONL dosyaları. Her satır `ts` (epoch) ve `mono` (monotonik saat) taşır ve maskeden geçer."""

    def __init__(self, out_dir: str, *, wall: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic) -> None:
        os.makedirs(out_dir, exist_ok=True)
        self.out_dir = out_dir
        self._wall = wall
        self._mono = mono
        self._lock = threading.Lock()
        self._files = {name: open(os.path.join(out_dir, f"{name}.jsonl"), "a", encoding="utf-8") for name in FILES}
        self.lines: Dict[str, int] = {name: 0 for name in FILES}

    def write(self, name: str, row: Mapping[str, Any], *, ts: Optional[float] = None,
              mono: Optional[float] = None) -> None:
        ts = self._wall() if ts is None else ts
        mono = self._mono() if mono is None else mono
        line = json.dumps(scrub({"ts": round(ts, 3), "mono": round(mono, 3), **row}), ensure_ascii=False)
        with self._lock:
            f = self._files.get(name)
            if f is None or f.closed:
                return
            f.write(line + "\n")
            f.flush()
            self.lines[name] += 1

    def write_summary(self, summary: Mapping[str, Any]) -> None:
        rc.write_json(os.path.join(self.out_dir, "summary.json"), scrub(summary))

    def close(self) -> None:
        with self._lock:
            for f in self._files.values():
                f.close()


# --- değişiklik izleyici: hangi değişiklik bir maç sayfası okuması ister ---------------------------------------


@dataclass
class EventState:
    code: Any = None
    stype: Optional[str] = None
    cls: Optional[str] = None
    home: Any = None
    away: Any = None
    ts: Optional[int] = None  # changes.changeTimestamp

    def expect(self) -> Dict[str, Any]:
        return {"class": self.cls, "home": self.home, "away": self.away}


def _class_of(code: Any, stype: Optional[str]) -> Optional[str]:
    if stype:
        status: Dict[str, Any] = {"type": stype}
        if code is not None:
            status["code"] = code
        return classify_status({"status": status}).value
    if code is not None:
        return classify_code(code).value
    return None


def _ts(value: Any) -> Optional[int]:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _score(event: Mapping[str, Any], side: str) -> Any:
    node = event.get(side)
    return node.get("current") if isinstance(node, dict) else None


class ChangeTracker:
    """
    Spor ve maç başına son bilinen durum sınıfı, skor ve değişiklik anı. Push karesi ya da canlı liste ilgili bir
    değişiklik gösterirse tetikleyici türlerini verir. Sporun ilk listesi tohumdur (tetikleyici yok). Liste, son
    bilinen değişiklik anından eskiyse (CDN) o maç için yok sayılır: eski gözlem yeniyi ezmez (LastKnown gibi).
    Okunan maç sayfası da durumu günceller (tetikleyicisiz): okunmuş bir değişiklik yeniden okuma istemez.
    """

    def __init__(self) -> None:
        self._events: Dict[Tuple[str, int], EventState] = {}
        self._listed: Dict[str, Set[int]] = {}

    def state(self, sport: str, eid: int) -> Optional[EventState]:
        return self._events.get((sport, eid))

    @staticmethod
    def _kinds(new: bool, prev_cls: Optional[str], cls: Optional[str], score_changed: bool,
               had_score: bool) -> Set[str]:
        kinds: Set[str] = set()
        if cls is not None and cls != prev_cls:
            if new or prev_cls is None:
                if cls in TERMINAL_CLASSES:
                    kinds.add(KIND_FINISH)
                elif cls == LIVE:
                    kinds.add(KIND_NEW)
            else:
                kinds.add(KIND_FINISH if cls in TERMINAL_CLASSES else KIND_STATUS)
        if score_changed:
            if (new or not had_score) and cls in (None, LIVE):
                if not kinds:
                    kinds.add(KIND_NEW)
            elif not new and had_score:
                kinds.add(KIND_SCORE)
        return kinds

    def frame(self, sport: str, body: Mapping[str, Any]) -> Tuple[Set[str], Dict[str, Any], Optional[str]]:
        """Bir push karesi → (tetikleyiciler, beklenen durum, karenin gösterdiği sınıf)."""
        eid = int(body["id"])
        st = self._events.get((sport, eid))
        new = st is None
        st = st or EventState()
        prev_cls, had_score = st.cls, st.home is not None or st.away is not None
        code = body.get("status.code", st.code)
        stype = st.stype
        if "status.type" in body:
            stype = body.get("status.type")
        elif "status.code" in body and stype:
            # merge_frame gibi: yalnız kod geldiyse ve eski tür onunla çelişiyorsa eski tür düşer
            by_code = classify_code(code)
            if by_code is not StatusClass.UNKNOWN and by_code.value != _class_of(code, stype):
                stype = None
        status_seen = "status.code" in body or "status.type" in body
        cls = _class_of(code, stype) if status_seen else st.cls
        home = body.get("homeScore.current", st.home)
        away = body.get("awayScore.current", st.away)
        score_changed = ("homeScore.current" in body or "awayScore.current" in body) and (home, away) != (st.home, st.away)
        kinds = self._kinds(new, prev_cls, cls if status_seen else None, score_changed, had_score)
        st.code, st.stype, st.cls, st.home, st.away = code, stype, cls, home, away
        st.ts = _ts(body.get(CHANGE_TS)) or st.ts
        self._events[(sport, eid)] = st
        return kinds, st.expect(), cls if status_seen else None

    def _apply_event(self, sport: str, ev: Mapping[str, Any], *, triggers: bool) -> Optional[Tuple[int, Set[str]]]:
        try:
            eid = int(str(ev.get("id")))
        except ValueError:
            return None
        st = self._events.get((sport, eid))
        new = st is None
        st = st or EventState()
        changes = ev.get("changes")
        ts = _ts(changes.get("changeTimestamp")) if isinstance(changes, dict) else None
        if not new and st.ts is not None and ts is not None and ts < st.ts:
            return eid, set()  # CDN'in eski yanıtı
        status = ev.get("status") if isinstance(ev.get("status"), dict) else {}
        cls = classify_status(dict(ev)).value if status else None
        home, away = _score(ev, "homeScore"), _score(ev, "awayScore")
        had_score = st.home is not None or st.away is not None
        score_changed = (home, away) != (st.home, st.away) and (home is not None or away is not None)
        kinds = self._kinds(new, st.cls, cls, score_changed, had_score) if triggers else set()
        st.code, st.stype, st.cls = status.get("code"), status.get("type"), cls
        st.home, st.away = home, away
        st.ts = ts or st.ts
        self._events[(sport, eid)] = st
        return eid, kinds

    def listing(self, sport: str, events: Iterable[Mapping[str, Any]]) -> List[Tuple[int, Set[str], Dict[str, Any]]]:
        """Bir canlı liste → [(maç, tetikleyiciler, beklenen durum)]; sporun ilk listesi tohumdur."""
        first = sport not in self._listed
        out: List[Tuple[int, Set[str], Dict[str, Any]]] = []
        ids: Set[int] = set()
        for ev in events:
            applied = self._apply_event(sport, ev, triggers=not first)
            if applied is None:
                continue
            eid, kinds = applied
            ids.add(eid)
            if kinds:
                out.append((eid, kinds, self._events[(sport, eid)].expect()))
        if not first:
            for eid in sorted(self._listed[sport] - ids):
                st = self._events.get((sport, eid))
                if st is not None and st.cls == LIVE:
                    out.append((eid, {KIND_DROPPED}, st.expect()))
        self._listed[sport] = ids
        return out

    def observe_event(self, sport: str, event: Mapping[str, Any]) -> None:
        """Okunan maç sayfası: durum güncellenir, tetikleyici üretilmez."""
        self._apply_event(sport, event, triggers=False)


def reflects(kinds: Set[str], expect: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
    """Maç sayfası tetikleyen değişikliği gösteriyor mu (CDN eski yanıt verebilir)."""
    cls = classify_status(dict(event)).value
    if kinds == {KIND_DROPPED}:
        return cls != LIVE
    wanted = expect.get("class")
    if KIND_FINISH in kinds and cls not in TERMINAL_CLASSES:
        return False
    if kinds & {KIND_STATUS, KIND_NEW} and wanted is not None and cls != wanted and not (
            KIND_FINISH in kinds and cls in TERMINAL_CLASSES):
        return False
    if KIND_SCORE in kinds and (_score(event, "homeScore"), _score(event, "awayScore")) != (
            expect.get("home"), expect.get("away")):
        return False
    return True


# --- REST okumaları: bütçe, hız, durum kodu ---------------------------------------------------------------------


@dataclass
class Outcome:
    status: Any
    seconds: float
    data: Optional[Dict[str, Any]]
    waited: float = 0.0
    blocked: Optional[BaseException] = None
    refused: bool = False  # bütçe bitti: ağa çıkılmadı


def status_of(exc: Optional[BaseException]) -> Optional[Any]:
    """Servisin istek işlevinin hatasından HTTP durumu (biliniyorsa)."""
    if exc is None:
        return None
    from sofascore_scraper.exceptions import CircuitOpenError, RateLimitError, ResourceNotFoundError

    if isinstance(exc, ResourceNotFoundError):
        return 404
    if isinstance(exc, CircuitOpenError):
        return "circuit_open"
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    if isinstance(exc, RateLimitError):
        return 429
    return None


class Pacer:
    """Süreç içi hız: ardışık REST okumaları arasında en az 1/rate sn (thread'ler arası sıra)."""

    def __init__(self, rate: float, *, mono: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.interval = 1.0 / rate if rate > 0 else 0.0
        self._mono = mono
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = self._mono()
            at = max(now, self._next)
            self._next = at + self.interval
        if at > now:
            self._sleep(at - now)


def default_gate(rate: float) -> Callable[[], None]:
    pacer = Pacer(rate)

    def gate() -> None:
        pacer.wait()
        rc._wait_rate_slot()  # araştırma süreçlerinin ortak kilidi (≤ 1 istek/sn)

    return gate


class ReadClient:
    """
    Kaydedicinin ve servisin REST okumaları: koşu bütçesi, sıra (`gate`), süre ve durum kodu. `base` servisin gerçek
    istek işlevidir (supervisor._default_fetch: 404 → None, engellenme → Blocked).
    """

    def __init__(self, base: Callable[[str], Optional[Dict[str, Any]]], *, budget: int,
                 gate: Callable[[], None], mono: Callable[[], float] = time.monotonic) -> None:
        self._base = base
        self.budget = max(0, int(budget))
        self._gate = gate
        self._mono = mono
        self._lock = threading.Lock()
        self.used = 0
        self.refused = 0

    @property
    def exhausted(self) -> bool:
        return self.used >= self.budget

    def request(self, path: str) -> Outcome:
        with self._lock:
            if self.used >= self.budget:
                self.refused += 1
                return Outcome("budget", 0.0, None, refused=True)
            self.used += 1
        t0 = self._mono()
        self._gate()
        t1 = self._mono()
        try:
            data = self._base(path)
        except sv.Blocked as e:
            return Outcome(status_of(e.__cause__) or "blocked", self._mono() - t1, None, t1 - t0, blocked=e)
        except Exception as e:  # tek isteğin hatası: kayda geçer, kaydedici sürer
            return Outcome(status_of(e) or type(e).__name__, self._mono() - t1, None, t1 - t0)
        return Outcome(200 if data is not None else 404, self._mono() - t1, data, t1 - t0)


@dataclass
class Pending:
    sport: str
    eid: int
    kinds: Set[str]
    sources: Set[str]
    first_ts: float
    first_mono: float
    expect: Dict[str, Any]
    due: float
    attempts: int = 0


class EventReads:
    """
    Kaydedicinin değişiklikle tetiklenen maç sayfası okumaları (kendi thread'i). Aynı maçın bekleyen okuması
    birleştirilir: en erken tetikleme anı kalır, beklenen durum en sonuncusudur. Değişikliği göstermeyen okuma `retry`
    sn sonra, en çok `attempts` kez yinelenir; engellenmede artan aralıklarla beklenir. Bekleyen okuma sayısı sınırlıdır.
    """

    def __init__(self, soak: "Soak", *, retry: float = sv.CONFIRM_RETRY_SECONDS, attempts: int = sv.CONFIRM_ATTEMPTS,
                 max_pending: int = MAX_PENDING_READS, blocked_first: float = sv.BLOCKED_FIRST_SECONDS,
                 blocked_max: float = sv.BLOCKED_MAX_SECONDS) -> None:
        self._soak = soak
        self.retry = retry
        self.attempts = max(1, attempts)
        self._max = max_pending
        self._blocked = (blocked_first, blocked_max)
        self._blocked_rounds = 0
        self._cond = threading.Condition()
        self._pending: "collections.OrderedDict[int, Pending]" = collections.OrderedDict()
        self.overflow = 0

    def want(self, p: Pending) -> None:
        with self._cond:
            old = self._pending.get(p.eid)
            if old is not None:
                old.kinds |= p.kinds
                old.sources |= p.sources
                old.expect = p.expect
                old.due = min(old.due, p.due)  # yinelemeyi bekleyen okuma: yeni değişiklikle hemen
            elif len(self._pending) >= self._max:
                self.overflow += 1
                return
            else:
                self._pending[p.eid] = p
            self._cond.notify()

    def pending(self) -> int:
        with self._cond:
            return len(self._pending)

    def _next(self, stop: threading.Event) -> Optional[Pending]:
        with self._cond:
            while not stop.is_set():
                now = self._soak.mono()
                if self._pending:
                    item = min(self._pending.values(), key=lambda p: (p.due, p.first_mono))
                    if item.due <= now:
                        return self._pending.pop(item.eid)
                    self._cond.wait(min(0.5, item.due - now))
                else:
                    self._cond.wait(0.5)
        return None

    def run(self, stop: threading.Event) -> None:
        from sofascore_scraper.client import request_context

        with request_context(breaker=None):
            while not stop.is_set():
                item = self._next(stop)
                if item is not None:
                    self._read(item)

    def wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def _read(self, p: Pending) -> None:
        soak = self._soak
        out = soak.client.request(f"/event/{p.eid}")
        done_ts, done_mono = soak.wall(), soak.mono()
        if out.refused:
            soak.on_event_skipped(p)
            return
        p.attempts += 1
        event = (out.data or {}).get("event") if out.data is not None else None
        ok = bool(event) and reflects(p.kinds, p.expect, event)
        final = ok or (out.blocked is None and p.attempts >= self.attempts)
        retry_in: Optional[float] = None
        if out.blocked is not None:
            self._blocked_rounds += 1
            first, cap = self._blocked
            retry_in = min(cap, first * 2 ** (self._blocked_rounds - 1))
            final = False
        else:
            self._blocked_rounds = 0
            if not final:
                retry_in = self.retry
        latency = done_mono - p.first_mono if ok else None
        soak.on_event_read(p, out, event, ok=ok, final=final, latency=latency, retry_in=retry_in,
                           ts=done_ts, mono=done_mono)
        if retry_in is not None:
            p.due = done_mono + retry_in
            self.want(p)


class FallbackReads:
    """
    Yoklama öndeyken (sahibin c için kuralı) her canlı maçın sayfası dakikada en az bir kez: dakikanın başında o
    sporların son canlı listesindeki maçlar sıralanır (öncelikli id'ler, bitişe yakınlar, en uzun süredir okunmayanlar)
    ve dakikanın payı (`cap`) kadar okunur. Dakika içinde başka bir yoldan (değişiklik okuması, yoklama kaynağı)
    okunmuş maç yeniden okunmaz, kapsanmış sayılır. Sığmayan okuma atlanır ve sayılır; bir sonraki dakikaya taşınmaz.
    """

    def __init__(self, soak: "Soak", *, rate: float, share: float, window: float = FALLBACK_WINDOW_SECONDS,
                 priority: Iterable[int] = ()) -> None:
        self._soak = soak
        self.window = float(window)
        self.cap = max(0, int(math.floor(self.window * rate * (1.0 - share) + 1e-9)))
        self.priority: FrozenSet[int] = frozenset(int(p) for p in priority)

    def run(self, stop: threading.Event) -> None:
        from sofascore_scraper.client import request_context

        with request_context(breaker=None):
            while not stop.is_set():
                sports = self._soak.poll_led_sports()
                if not sports:
                    stop.wait(min(1.0, self.window / 4))
                    continue
                self._minute(sports, stop)

    def _rank(self, sport: str, eid: int) -> Tuple[int, int, float]:
        last = self._soak.last_read.get((sport, eid))
        return (0 if eid in self.priority else 1, 0 if self._soak.near_end(sport, eid) else 1,
                -1.0 if last is None else last)

    def _minute(self, sports: Sequence[str], stop: threading.Event) -> None:
        soak = self._soak
        start_ts, start = soak.wall(), soak.mono()
        end = start + self.window
        live = {sport: soak.latest_list(sport) for sport in sports}
        ranked = sorted(((s, e) for s in sports for e in live[s]), key=lambda c: self._rank(*c))
        made: Dict[str, int] = collections.defaultdict(int)
        attempted = 0
        cut = None
        for sport, eid in ranked:
            if stop.is_set():
                cut = "stopped"
                break
            if sport not in soak.poll_led_sports():
                continue  # push yeniden önde: bu sporun dakikalık okumaları durur
            if soak.read_since(sport, eid, start):
                continue  # bu dakika başka bir yoldan okundu
            if attempted >= self.cap:
                cut = "share"
                break
            if soak.mono() >= end:
                cut = "minute"
                break
            out = soak.fallback_read(sport, eid, window_start_ts=start_ts)
            attempted += 1
            made[sport] += 1
            if out.refused or out.blocked is not None:
                cut = "budget" if out.refused else "blocked"
                break
        # Dakikanın sonuna kadar beklenir; push bütün bu sporlarda yeniden önde olursa dakika erken biter
        while not stop.is_set() and soak.mono() < end and set(sports) & set(soak.poll_led_sports()):
            stop.wait(min(0.5, max(0.0, end - soak.mono())))
        led = set(soak.poll_led_sports())
        early = soak.mono() < end - 1e-3
        for sport in sports:
            ids = live[sport]
            covered = sum(1 for eid in ids if soak.read_since(sport, eid, start))
            # yarım dakika (durduruldu ya da push döndü) atlama oranına sayılmaz
            soak.on_fallback_minute(sport, start_ts, live=len(ids), made=made[sport], covered=covered,
                                    priority=sum(1 for eid in ids if eid in self.priority), cap=self.cap, cut=cut,
                                    partial=early or sport not in led)


# --- push: musluk, soket, okuyucu, bağlantı, sayfa açıcı -------------------------------------------------------


class TapSession:
    """`page` kaynağında sayfanın bir WebSocket'i (musluğun gördüğü kadarıyla)."""

    def __init__(self, session: int, handshake_mono: float) -> None:
        self.session = session
        self.handshake_mono = handshake_mono  # sayfada WebSocket'in açıldığı an
        self.info_seen = False
        self.killed: Optional[str] = None


class FeedTap:
    """
    Bir sporun PushFeed'i ile kaynak arasındaki musluk: her şey olduğu gibi akışa geçer (servis aynı işaretleri görür);
    MSG kareleri alındıkları an kaydedilir. `direct`'te bağlantının hat üstü olaylarını RecordingSocket kaydeder;
    `page`'de (`page=True`) INFO, PING, PONG ve -ERR de buradan kaydedilir. Kaynağın akışta kullandığı dört işlemi
    taşır (connection, received, closed, gone).
    """

    def __init__(self, feed: PushFeed, soak: "Soak", *, page: bool = False) -> None:
        self._feed = feed
        self._soak = soak
        self._page = page
        self.sport = feed.sport
        self._readers: Dict[int, NatsReader] = {}
        self._sessions: Dict[int, TapSession] = {}

    def connection(self) -> int:
        conn = self._feed.connection()
        self._readers[conn] = NatsReader()
        if self._page:
            self._sessions[conn] = self._soak.on_page_socket(self.sport)
        else:
            self._soak.on_sport_open(self.sport)
        return conn

    def received(self, conn: int, payload: Any) -> None:
        ts, mono = self._soak.wall(), self._soak.mono()
        self._feed.received(conn, payload)
        reader = self._readers.get(conn)
        if reader is None:
            return
        for op in reader.feed(payload):
            if op.kind == "MSG":
                body = frame_body(op)
                if body is not None:
                    self._soak.on_frame(self.sport, op.subject, body, ts, mono)
            elif self._page:
                session = self._sessions.get(conn)
                if session is None:
                    continue
                if op.kind == "INFO" and not session.info_seen:
                    self._soak.on_wire(session, op)
                    self._soak.on_sport_open(self.sport, session=session.session)
                elif session.info_seen or op.kind != "INFO":
                    self._soak.on_wire(session, op)

    def closed(self, conn: int) -> None:
        self._feed.closed(conn)
        self._readers.pop(conn, None)
        session = self._sessions.pop(conn, None)
        if self._page:
            if session is not None and session.info_seen:
                self._soak.on_page_socket_closed(self.sport, session)
        else:
            self._soak.on_sport_closed(self.sport, "closed")

    def gone(self, reason: str) -> None:
        self._feed.gone(reason)
        self._readers.clear()
        self._sessions.clear()
        self._soak.on_sport_closed(self.sport, reason, gone=True)


class RecordingSocket:
    """
    Bağlantı işlevinin döndürdüğü WebSocket'in sarmalayıcısı: hat üstü işlemlerin adları ve zamanları kaydedilir
    (CONNECT gövdesi, INFO gövdesi, adres asla). `kill` soketi kapatır: istemci olağan bir kopma görür.
    """

    def __init__(self, ws: Any, soak: "Soak", session: int, handshake_mono: float) -> None:
        self._ws = ws
        self._soak = soak
        self.session = session
        self.handshake_mono = handshake_mono
        self._reader = NatsReader()
        self.info_seen = False
        self.killed: Optional[str] = None

    def send_text(self, text: str) -> None:
        self._ws.send_text(text)
        for line in text.split("\r\n"):
            if line:
                self._soak.on_sent(self, line)

    def recv(self, timeout: float) -> Optional[bytes]:
        data = self._ws.recv(timeout)
        if data is not None:
            for op in self._reader.feed(data):
                self._soak.on_wire(self, op)
        return data

    def close(self) -> None:
        self._ws.close()

    def kill(self, cause: str) -> None:
        self.killed = cause
        sock = getattr(self._ws, "_sock", None)
        try:
            if sock is not None:
                sock.shutdown(socket.SHUT_RDWR)
            else:
                self._ws.close()
        except OSError:
            pass


_SALT = secrets.token_bytes(32)  # yalnızca bellek: kimlik bilgilerinin karşılaştırılması


def _digest(value: Any) -> bytes:
    """Bellekte karşılaştırma için tuzlu HMAC; ne girdisi ne kendisi hiçbir yere yazılır."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hmac.new(_SALT, text.encode("utf-8"), hashlib.sha256).digest()


def _same(a: Optional[bytes], b: Optional[bytes]) -> Optional[str]:
    if a is None or b is None:
        return None
    return "same" if hmac.compare_digest(a, b) else "different"


class RecordingReader:
    """
    Kimlik bilgisi okuyucusunun sarmalayıcısı: hangi sporun sayfasından, ne kadar sürede, başarılı mı, bir öncekiyle
    aynı mı (yalnızca "same"/"different"). Okuma sürerken kaynak örneklemesi 5 sn'ye iner (ikinci Chromium).
    `compare_sports` verilirse ilk başarılı okumadan sonra diğer sporların sayfalarından da okunur ve karşılaştırılır.
    """

    def __init__(self, inner: Any, soak: "Soak", *, compare_factory: Optional[Callable[[], Any]] = None,
                 compare_sports: Sequence[str] = ()) -> None:
        self._inner = inner
        self._soak = soak
        self._compare_factory = compare_factory
        self._compare_sports = tuple(compare_sports)
        self._lock = threading.Lock()  # okumalar sırayla: aynı canlı profil
        self._previous: Optional[Tuple[bytes, bytes]] = None
        self._first: Optional[Tuple[str, bytes, bytes]] = None
        self.reads = 0
        self.compare_thread: Optional[threading.Thread] = None

    def _read_with(self, reader: Any, sport: str, purpose: str) -> Any:
        self.reads += 1
        token = self._soak.on_credential_start(sport, self.reads, purpose)
        try:
            credential = reader.read(sport)
        except ds.CredentialUnavailable as e:
            self._soak.on_credential_end(token, ok=False, error=str(e)[:200])
            raise
        except Exception as e:
            self._soak.on_credential_end(token, ok=False, error=type(e).__name__)
            raise
        digests = (_digest(dict(credential.options)), _digest(credential.url))
        extra: Dict[str, Any] = {}
        if purpose == "connect":
            prev = self._previous
            # Anahtar adları sofascore_scraper/redact.py'nin gizli saydığı adlara (…CREDENTIAL…) benzemez: değer maskelenmesin
            extra = {"connect_vs_previous": _same(prev[0] if prev else None, digests[0]),
                     "address_vs_previous": _same(prev[1] if prev else None, digests[1])}
            self._previous = digests
            if self._first is None:
                self._first = (sport, digests[0], digests[1])
        self._soak.on_credential_end(token, ok=True, **extra)
        return credential if purpose == "connect" else digests

    def read(self, sport: str) -> Any:
        with self._lock:
            credential = self._read_with(self._inner, sport, "connect")
        others = [s for s in self._compare_sports if s != sport]
        if others and self._compare_factory is not None and self.compare_thread is None:
            self.compare_thread = threading.Thread(target=self._compare, args=(others,), name="soak-compare",
                                                   daemon=True)
            self.compare_thread.start()
        return credential

    def _compare(self, others: Sequence[str]) -> None:
        assert self._first is not None and self._compare_factory is not None
        base_sport, base_options, base_address = self._first
        for other in others:
            if self._soak.stopping:
                return
            reader = self._compare_factory()
            try:
                with self._lock:
                    digests = self._read_with(reader, other, "compare")
            except Exception:
                self._soak.on_credential_compare(base_sport, other, None, None)
                continue
            finally:
                try:
                    reader.close()
                except Exception:
                    pass
            self._soak.on_credential_compare(base_sport, other, _same(base_options, digests[0]),
                                             _same(base_address, digests[1]))

    def close(self) -> None:
        self._inner.close()


class RecordingConnection(ds.DirectConnection):
    """Uygulamanın DirectConnection'ı; oturum başı/sonu, yeniden deneme ve akışlar kaydedilir, bağlantı öldürülebilir."""

    def __init__(self, reader: Any, soak: "Soak", **options: Any) -> None:
        self._soak = soak
        inner = options.pop("connect", None) or ds.default_connect
        self.current: Optional[RecordingSocket] = None
        self._session_no = 0

        def connect(credential: Any) -> RecordingSocket:
            t0 = soak.mono()
            try:
                ws = inner(credential)
            except ds.HandshakeRejected as e:
                soak.on_handshake(self._session_no, soak.mono() - t0, status=e.status)
                raise
            except (OSError, ds.WebSocketError) as e:
                soak.on_handshake(self._session_no, soak.mono() - t0, error=type(e).__name__)
                raise
            soak.on_handshake(self._session_no, soak.mono() - t0, status=101)
            sock = RecordingSocket(ws, soak, self._session_no, soak.mono())
            self.current = sock
            return sock

        super().__init__(reader, connect=connect, **options)

    def subscribe(self, sport: str, feed: PushFeed) -> None:
        super().subscribe(sport, FeedTap(feed, self._soak))  # type: ignore[arg-type]

    def _session(self, credential: Any) -> Tuple[str, str, float]:
        self._session_no += 1
        n = self._session_no
        self._soak.on_session_start(n)
        outcome, reason, lasted = super()._session(credential)
        sock, self.current = self.current, None
        self._soak.on_session_end(n, outcome, reason, lasted, killed=sock.killed if sock is not None else None)
        return outcome, reason, lasted

    def _failed(self, reason: str, *, reading: bool = False) -> None:
        self._soak.on_retry(reason, failures=self.failures + 1, reading=reading,
                            unhealthy=self.unhealthy or self.failures + 1 >= self._unhealthy_after)
        super()._failed(reason, reading=reading)

    def kill(self, cause: str) -> bool:
        sock = self.current
        if sock is None:
            return False
        sock.kill(cause)
        return True


class RecordingOpener:
    """`page` kaynağının sayfa açıcısı (BrowserPageOpener) sarmalanır: akışa musluk takılır, sayfalar kapatılabilir."""

    def __init__(self, inner: Any, soak: "Soak") -> None:
        self._inner = inner
        self._soak = soak
        self._handles: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def open(self, sport: str, feed: PushFeed) -> Any:
        self._soak.recorder.write("connection", {"event": "page_open", "sport": sport})
        handle = self._inner.open(sport, FeedTap(feed, self._soak, page=True))
        with self._lock:
            self._handles[sport] = handle
        return handle

    def kill(self) -> List[str]:
        """Açık sayfaların hepsini kapatır (sayfa kapanışı akışa `gone` olarak düşer, PageSource yeniden açar)."""
        with self._lock:
            handles, self._handles = dict(self._handles), {}
        for handle in handles.values():
            try:
                handle.close()
            except Exception:
                pass
        return sorted(handles)

    def close(self) -> None:
        self._inner.close()


class AllScope(sv.SportScope):
    """Sporun bütün canlı maçları kapsamda (servisin yoklama kaynağı her canlı maçı bugünkü kurallarla izler)."""

    def listed(self, event: Mapping[str, Any]) -> bool:
        return True


class RecordingLiveService(sv.LiveService):
    """LiveService; her sistem olayı (hakemin Switch'i, engellenme) kaydediciye de verilir."""

    def __init__(self, *args: Any, soak: "Soak", **kwargs: Any) -> None:
        self._soak_hook = soak
        super().__init__(*args, **kwargs)

    def _system(self, type_: str, data: Mapping[str, Any]) -> None:
        super()._system(type_, data)
        self._soak_hook.on_system(type_, dict(data))


# --- kaynaklar --------------------------------------------------------------------------------------------------


def _rss_kb(pid: int) -> Optional[int]:
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return 0


def _process_table() -> Dict[int, int]:
    """pid → ppid (procfs)."""
    table: Dict[int, int] = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return table
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", encoding="utf-8") as f:
                stat = f.read()
            table[int(name)] = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
    return table


def _profile_of(pid: int) -> Optional[str]:
    """Tarayıcının ana sürecinin `--user-data-dir`'i (yalnızca sınıflandırmada kullanılır, yazılmaz)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            args = f.read().split(b"\0")
    except OSError:
        return None
    for arg in args:
        if arg.startswith(b"--user-data-dir="):
            return arg.split(b"=", 1)[1].decode("utf-8", "replace").rstrip("/")
    return None


def classify_browser(profile: Optional[str]) -> str:
    """Profil dizini → "live" (canlı profil `<profil>-live`: sayfalar ya da kimlik bilgisi okuyucusu) ya da "bridge"."""
    if profile is None:
        return "other"
    return "live" if profile.endswith("-live") else "bridge"


def sample_resources() -> Dict[str, Any]:
    """
    Bu sürecin RSS'i, alt süreçlerinin tarayıcıya göre RSS'i (ana köprü, canlı profil, diğer: Playwright sürücüsü),
    toplam ve açık soket sayısı. Bir alt süreç, kendisinin ya da en yakın atasının `--user-data-dir`'ine göre ayrılır.
    """
    pid = os.getpid()
    self_kb = _rss_kb(pid)
    if self_kb is None:  # /proc yok (Linux dışı)
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # Linux'ta kB, macOS'ta bayt; zirve
        mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
        return {"rss_self_mb": round(mb, 1), "rss_bridge_mb": None, "rss_live_mb": None,
                "rss_other_mb": None, "rss_total_mb": None, "processes": None, "sockets": None, "procfs": False}
    table = _process_table()
    children: Dict[int, List[int]] = collections.defaultdict(list)
    for child, parent in table.items():
        children[parent].append(child)
    kb = {"bridge": 0, "live": 0, "other": 0}
    procs = {"bridge": 0, "live": 0, "other": 0}
    todo: List[Tuple[int, Optional[str]]] = [(c, None) for c in children.get(pid, [])]
    while todo:
        p, inherited = todo.pop()
        kind = inherited
        if kind is None:
            profile = _profile_of(p)
            kind = classify_browser(profile) if profile is not None else None
        bucket = kind or "other"
        kb[bucket] += _rss_kb(p) or 0
        procs[bucket] += 1
        todo.extend((c, kind) for c in children.get(p, []))
    try:
        sockets = sum(1 for fd in os.listdir("/proc/self/fd")
                      if os.readlink(f"/proc/self/fd/{fd}").startswith("socket:"))
    except OSError:
        sockets = None
    total = self_kb + sum(kb.values())
    return {"rss_self_mb": round(self_kb / 1024, 1), "rss_bridge_mb": round(kb["bridge"] / 1024, 1),
            "rss_live_mb": round(kb["live"] / 1024, 1), "rss_other_mb": round(kb["other"] / 1024, 1),
            "rss_total_mb": round(total / 1024, 1), "processes": procs, "sockets": sockets}


# --- zaman çizelgeleri ------------------------------------------------------------------------------------------


def step_positive(points: Sequence[Tuple[float, int]], start: float, end: float) -> List[Tuple[float, float]]:
    """(an, canlı maç sayısı) noktalarından sayının > 0 olduğu aralıklar (bir nokta bir sonrakine kadar geçerli)."""
    out: List[Tuple[float, float]] = []
    pts = sorted(p for p in points if p[0] <= end)
    for i, (t, n) in enumerate(pts):
        s = max(t, start)
        e = min(pts[i + 1][0] if i + 1 < len(pts) else end, end)
        if n > 0 and e > s:
            if out and abs(out[-1][1] - s) < 1e-9:
                out[-1] = (out[-1][0], e)
            else:
                out.append((s, e))
    return out


def overlap(intervals: Sequence[Tuple[float, float]], a: float, b: float) -> float:
    return sum(max(0.0, min(e, b) - max(s, a)) for s, e in intervals)


def quiet_gaps(frames: Sequence[float], positive: Sequence[Tuple[float, float]], start: float,
               end: float) -> List[Tuple[float, float]]:
    """MSG karesi gelmeyen ve canlı listenin > 0 maç gösterdiği kesintisiz aralıklar (uzundan kısaya)."""
    bounds = [start] + sorted(t for t in frames if start <= t <= end) + [end]
    out: List[Tuple[float, float]] = []
    for a, b in zip(bounds, bounds[1:], strict=False):
        for s, e in positive:
            lo, hi = max(a, s), min(b, e)
            if hi > lo:
                out.append((lo, hi))
    out.sort(key=lambda iv: iv[1] - iv[0], reverse=True)
    return out


def leader_intervals(timeline: Sequence[Tuple[float, str]], start: float, end: float,
                     leader: str) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    pts = sorted(timeline)
    for i, (t, who) in enumerate(pts):
        s, e = max(t, start), min(pts[i + 1][0] if i + 1 < len(pts) else end, end)
        if who == leader and e > s:
            out.append((s, e))
    return out


def complement(intervals: Sequence[Tuple[float, float]], start: float, end: float) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    cursor = start
    for s, e in sorted(intervals):
        if s > cursor:
            out.append((cursor, min(s, end)))
        cursor = max(cursor, e)
    if cursor < end:
        out.append((cursor, end))
    return [(s, e) for s, e in out if e > s]


def count_between(times: Sequence[float], a: float, b: float) -> int:
    return bisect.bisect_right(times, b) - bisect.bisect_left(times, a)


def longest_without(times: Sequence[float], a: float, b: float) -> float:
    bounds = [a] + [t for t in times if a <= t <= b] + [b]
    return max((y - x for x, y in zip(bounds, bounds[1:], strict=False)), default=b - a)


class KillSchedule:
    """`--kill-every` (dakika, tekrar) ve `--kill-at` (dakika, bir kez); `due(geçen sn)` → neden ya da None."""

    def __init__(self, every_minutes: Optional[float] = None, at_minutes: Optional[float] = None) -> None:
        self.every = every_minutes * 60.0 if every_minutes else None
        self.at = at_minutes * 60.0 if at_minutes is not None else None
        self._next_every = self.every
        self._at_done = False

    def due(self, elapsed: float) -> Optional[str]:
        if self.at is not None and not self._at_done and elapsed >= self.at:
            self._at_done = True
            return "kill-at"
        if self._next_every is not None and self.every is not None and elapsed >= self._next_every:
            while self._next_every <= elapsed:
                self._next_every += self.every
            return "kill-every"
        return None


def default_budget(hours: float, rate: float = DEFAULT_RATE) -> int:
    """Koşunun REST bütçesi: süre × hız (ortalama ≤ 1 istek/sn)."""
    return max(1, int(hours * 3600 * min(1.0, rate)))


# --- koşu -------------------------------------------------------------------------------------------------------


@dataclass
class SportStats:
    frames: List[float] = field(default_factory=list)  # MSG karelerinin alındığı an (epoch)
    status_frames: int = 0
    score_frames: int = 0
    finish_frames: int = 0
    events: Set[int] = field(default_factory=set)
    frame_lag: List[float] = field(default_factory=list)  # alındı − changeTimestamp, her kare
    finish_lag: List[float] = field(default_factory=list)  # aynısı, bitiş kareleri
    triggers: Dict[str, int] = field(default_factory=lambda: collections.defaultdict(int))
    read_latency: Dict[str, List[float]] = field(default_factory=lambda: collections.defaultdict(list))
    not_reflected: int = 0
    reads_ok: int = 0
    skipped_budget: int = 0
    live_counts: List[Tuple[float, int]] = field(default_factory=list)
    leader: List[Tuple[float, str]] = field(default_factory=list)
    first_push_at: Optional[float] = None
    open_intervals: List[List[Any]] = field(default_factory=list)  # [başlangıç, bitiş, kapanış nedeni]
    open_since: Optional[float] = None
    fallback: List[Dict[str, Any]] = field(default_factory=list)  # dakika satırları


class Soak:
    """
    Bir koşu: kaydedici, servis (kendi thread'inde), değişiklik okumaları, yedek dakika okumaları ve kaynak örneklemesi.
    `start` ve `stop` testlerden de çağrılır; `run` gerçek koşunun döngüsüdür (öldürme takvimi, 10 dakikalık özet).

    Testler gerçek okuyucu, bağlantı, sayfa açıcı ve istek işlevi yerine sahtelerini verir (`reader`,
    `connection_options`, `page_opener`, `base_fetch`, `gate`, `compare_factory`); varsayılanlar uygulamanın gerçek
    parçalarıdır.
    """

    def __init__(self, out_dir: str, sports: Sequence[str], *, run_id: str, data_dir: str,
                 budget: int, source: str = "direct", hours: Optional[float] = None,
                 kill_every: Optional[float] = None, kill_at: Optional[float] = None, poll_interval: float = 30.0,
                 rate: float = DEFAULT_RATE, list_share: float = DEFAULT_LIST_SHARE,
                 priority_ids: Iterable[int] = (), fallback_window: float = FALLBACK_WINDOW_SECONDS,
                 base_fetch: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
                 gate: Optional[Callable[[], None]] = None, reader: Any = None, page_opener: Any = None,
                 compare_sports: bool = False, compare_factory: Optional[Callable[[], Any]] = None,
                 connection_options: Optional[Dict[str, Any]] = None,
                 resource_seconds: float = RESOURCE_SECONDS, reading_resource_seconds: float = READING_RESOURCE_SECONDS,
                 event_retry_seconds: float = sv.CONFIRM_RETRY_SECONDS, store: Any = None,
                 args_note: Optional[Dict[str, Any]] = None) -> None:
        from sofascore_scraper.store import open_store

        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")
        self.wall = time.time
        self.mono = time.monotonic
        self.run_id = run_id
        self.source = source
        self.push_name = source  # hakemin push önderken yazdığı ad
        self.sports = tuple(dict.fromkeys(sports))
        self.hours = hours
        self.recorder = Recorder(out_dir, wall=self.wall, mono=self.mono)
        self.kills = KillSchedule(kill_every, kill_at)
        self.args_note = dict(args_note or {})
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.stopping = False
        self.client = ReadClient(base_fetch or sv._default_fetch, budget=budget,
                                 gate=gate if gate is not None else default_gate(rate), mono=self.mono)
        self.tracker = ChangeTracker()
        self.reads = EventReads(self, retry=event_retry_seconds)
        self.fallback = FallbackReads(self, rate=rate, share=list_share, window=fallback_window,
                                      priority=priority_ids)
        self.stats: Dict[str, SportStats] = {s: SportStats() for s in self.sports}
        self.last_read: Dict[Tuple[str, int], float] = {}  # son başarılı maç sayfası okuması (mono), her yoldan
        self._latest_list: Dict[str, List[int]] = {}
        self._resource_seconds = resource_seconds
        self._reading_resource_seconds = reading_resource_seconds
        self._wake_sampler = threading.Event()
        self._budget_noted = False
        # bağlantı
        self.session_no = 0
        self.sessions: List[Dict[str, Any]] = []
        self._session: Dict[str, Any] = {}
        self._conn_open = False
        self._kill_cause: Dict[str, str] = {}
        self.ping_sent: List[float] = []
        self.pongs: List[float] = []
        self.server_pings: List[float] = []
        self.rtt: List[float] = []
        self.handshake_to_info: List[float] = []
        self.handshake_seconds: List[float] = []
        self.errors: Dict[str, int] = collections.defaultdict(int)
        self.drops: Dict[str, int] = collections.defaultdict(int)
        self.kill_log: List[Dict[str, Any]] = []
        self.retries = 0
        self.credential_reads: List[Dict[str, Any]] = []
        self.compares: List[Dict[str, Any]] = []
        self._reading: Optional[Dict[str, Any]] = None
        self.peak_rss_mb: Dict[str, float] = {"self": 0.0, "bridge": 0.0, "live": 0.0, "total": 0.0}
        self.last_resources: Optional[Dict[str, Any]] = None
        self.requests: Dict[str, int] = collections.defaultdict(int)
        self.request_latency: Dict[str, List[float]] = collections.defaultdict(list)
        self.switches: List[Dict[str, Any]] = []
        self.blocked: List[Dict[str, Any]] = []
        self.service_error: Optional[str] = None
        # bileşenler
        self.reader: Optional[RecordingReader] = None
        self.connection: Optional[RecordingConnection] = None
        self.opener: Optional[RecordingOpener] = None
        if source == "direct":
            inner_reader = reader if reader is not None else ds.BrowserCredentialReader()
            factory = compare_factory or (ds.BrowserCredentialReader if reader is None else None)
            self.reader = RecordingReader(inner_reader, self, compare_factory=factory,
                                          compare_sports=self.sports if compare_sports else ())
            self.connection = RecordingConnection(self.reader, self, **dict(connection_options or {}))
        else:
            from sofascore_scraper.services.live.push_source import BrowserPageOpener

            self.opener = RecordingOpener(page_opener if page_opener is not None else BrowserPageOpener(), self)
        self.store = store if store is not None else open_store(data_dir)
        scope = sv.LiveScope(sports=tuple(AllScope(s) for s in self.sports))
        self.service = RecordingLiveService(
            self.store, scope, fetch=self._service_fetch, poll_interval=poll_interval, confirm=False,
            requested_source=source, direct_connection=self.connection, page_opener=self.opener, soak=self)
        self.started_ts: Optional[float] = None
        self.started_mono: Optional[float] = None
        self.ended_ts: Optional[float] = None
        self._threads: List[threading.Thread] = []
        self._last_summary = 0.0

    # --- yaşam döngüsü ------------------------------------------------------------------------------------

    def start(self) -> None:
        self.started_ts, self.started_mono = self.wall(), self.mono()
        self._last_summary = self.started_mono
        for sport in self.sports:
            self.stats[sport].leader.append((self.started_ts, LEADER_POLL))
            self.recorder.write("leader", {"event": "start", "sport": sport, "leader": LEADER_POLL})
        self.recorder.write("connection", {"event": "run_start", "source": self.source, "sports": list(self.sports),
                                           "run_id": self.run_id})
        self._spawn("soak-service", self._run_service)
        self._spawn("soak-event-reads", lambda: self.reads.run(self._stop))
        self._spawn("soak-fallback", lambda: self.fallback.run(self._stop))
        self._spawn("soak-resources", self._sample_loop)

    def _spawn(self, name: str, target: Callable[[], None]) -> None:
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def _run_service(self) -> None:
        try:
            self.service.run(self._stop)
        except Exception as e:  # servis düştü: kayda geçer, koşu biter
            self.service_error = type(e).__name__
            self.recorder.write("connection", {"event": "service_error", "error": type(e).__name__})
            self._stop.set()

    @property
    def running(self) -> bool:
        return not self._stop.is_set()

    def stop(self, timeout: float = 30.0) -> None:
        self.stopping = True
        self._stop.set()
        self.reads.wake()
        self._wake_sampler.set()
        for t in self._threads:
            t.join(timeout)
        thread = self.reader.compare_thread if self.reader is not None else None
        if thread is not None:
            thread.join(timeout)
        self.ended_ts = self.wall()
        for sport in self.sports:
            self._close_sport(sport, self.ended_ts, "run end")
        self.recorder.write("connection", {"event": "run_end"})
        self.write_summary(final=True)
        self.recorder.close()
        try:
            self.store.close()
        except Exception:
            pass

    def run(self, hours: float, *, tick: float = 1.0) -> None:
        """Gerçek koşu: süre dolana ya da durdurulana kadar; öldürme takvimi ve 10 dakikalık özet."""
        self.start()
        deadline = self.mono() + hours * 3600.0
        try:
            while self.running and self.mono() < deadline:
                self._stop.wait(tick)
                cause = self.kills.due(self.mono() - (self.started_mono or self.mono()))
                if cause:
                    self.kill(cause)
                if self.mono() - self._last_summary >= SUMMARY_SECONDS:
                    self.write_summary(final=False)
        finally:
            self.stop()

    def request_stop(self) -> None:
        self._stop.set()

    def kill(self, cause: str) -> bool:
        """`direct`: bağlantının soketini kapatır; `page`: sporların sayfalarını kapatır."""
        sports: List[str] = []
        if self.connection is not None:
            done = self.connection.kill(cause)
        else:
            assert self.opener is not None
            with self._lock:
                for sport in self.sports:
                    self._kill_cause[sport] = cause
            sports = self.opener.kill()
            done = bool(sports)
        with self._lock:
            self.kill_log.append({"ts": self.wall(), "cause": cause, "done": done})
        row: Dict[str, Any] = {"event": "kill", "cause": cause, "done": done, "session": self.session_no}
        if self.opener is not None:
            row["pages"] = sports
        self.recorder.write("connection", row)
        return done

    # --- push ------------------------------------------------------------------------------------------------

    def on_frame(self, sport: str, subject: str, body: Mapping[str, Any], ts: float, mono: float) -> None:
        with self._lock:
            kinds, expect, cls = self.tracker.frame(sport, body)
            st = self.stats.setdefault(sport, SportStats())
            st.frames.append(ts)
            st.events.add(int(body["id"]))
            change = _ts(body.get(CHANGE_TS))
            lag = ts - change if change is not None else None
            if lag is not None:
                st.frame_lag.append(lag)
            if "status.code" in body or "status.type" in body:
                st.status_frames += 1
            if any(str(k).startswith(SCORE_PREFIXES) for k in body):
                st.score_frames += 1
            if cls in TERMINAL_CLASSES:
                st.finish_frames += 1
                if lag is not None and KIND_FINISH in kinds:
                    st.finish_lag.append(lag)
            for kind in kinds:
                st.triggers[kind] += 1
        values = {k: v for k, v in body.items() if k in STATUS_VALUE_KEYS or str(k).startswith(SCORE_PREFIXES)}
        row = {"sport": sport, "event_id": int(body["id"]),
               "subject": subject.split(".", 1)[0] if subject.startswith(("sport.", "event.")) else "other",
               "paths": sorted(str(k) for k in body if k != "id"), "values": values,
               "change_ts": change, "lag_s": round(lag, 3) if lag is not None else None,
               "triggers": sorted(kinds)}
        self.recorder.write("frames", row, ts=ts, mono=mono)
        if kinds:
            self.reads.want(Pending(sport, int(body["id"]), set(kinds), {"frame"}, ts, mono, expect, mono))

    def on_sport_open(self, sport: str, *, session: Optional[int] = None) -> None:
        ts = self.wall()
        session = self.session_no if session is None else session
        with self._lock:
            st = self.stats.setdefault(sport, SportStats())
            if st.open_since is None:
                st.open_since = ts
            first = self.connection is not None and not self._conn_open
            if first:
                self._conn_open = True
            hs = self._session.get("handshake_mono")
        if first:
            since = round(self.mono() - hs, 3) if hs is not None else None
            self.recorder.write("connection", {"event": "open", "session": session, "handshake_to_open_s": since})
        self.recorder.write("connection", {"event": "sport_open", "sport": sport, "session": session})

    def _close_sport(self, sport: str, ts: float, reason: str) -> None:
        with self._lock:
            st = self.stats.setdefault(sport, SportStats())
            if st.open_since is not None:
                st.open_intervals.append([st.open_since, ts, reason])
                st.open_since = None

    def on_sport_closed(self, sport: str, reason: str, *, gone: bool = False) -> None:
        with self._lock:
            killed = self._kill_cause.pop(sport, None) if self.opener is not None else None
        why = reason + (f" ({killed})" if killed else "")
        self._close_sport(sport, self.wall(), why)
        self.recorder.write("connection", {"event": "page_gone" if gone and self.opener is not None else "sport_closed",
                                           "sport": sport, "reason": why[:200], "session": self.session_no})

    # `page`: sayfanın her WebSocket'i bir oturumdur
    def on_page_socket(self, sport: str) -> TapSession:
        with self._lock:
            self.session_no += 1
            n = self.session_no
        return TapSession(n, self.mono())

    def on_page_socket_closed(self, sport: str, session: TapSession) -> None:
        ts = self.wall()
        with self._lock:
            killed = self._kill_cause.pop(sport, None)
            self.sessions.append({"n": session.session, "sport": sport, "outcome": "closed", "reason": "closed",
                                  "lasted": self.mono() - session.handshake_mono, "killed": killed, "end_ts": ts})
            self.drops["closed" + (f" ({killed})" if killed else "")] += 1
        self._close_sport(sport, ts, "websocket closed" + (f" ({killed})" if killed else ""))
        self.recorder.write("connection", {"event": "session_end", "sport": sport, "session": session.session,
                                           "outcome": "closed", "killed": killed})

    # --- bağlantı (`direct`) ------------------------------------------------------------------------------------

    def on_session_start(self, n: int) -> None:
        with self._lock:
            self.session_no = n
            self._session = {"n": n, "start_mono": self.mono(), "msgs": 0, "subs": 0}
        self.recorder.write("connection", {"event": "session_start", "session": n})

    def on_handshake(self, n: int, seconds: float, *, status: Optional[int] = None,
                     error: Optional[str] = None) -> None:
        with self._lock:
            if status == 101:
                self.handshake_seconds.append(seconds)
                self._session["handshake_mono"] = self.mono()
        self.recorder.write("connection", {"event": "handshake", "session": n, "status": status, "error": error,
                                           "seconds": round(seconds, 3)})

    def on_sent(self, sock: RecordingSocket, line: str) -> None:
        verb, _, rest = line.partition(" ")
        verb = verb.upper()
        row: Dict[str, Any] = {"session": sock.session}
        if verb == "CONNECT":
            row["event"] = "connect_sent"  # gövde (kimlik bilgisi) asla
        elif verb == "SUB":
            subject = rest.split(" ")[0]
            with self._lock:
                self._session["subs"] = self._session.get("subs", 0) + 1
                resub = any(s.get("subs") for s in self.sessions)
            row.update(event="subscribe", subject=subject if subject.startswith(ds.SUBJECT_PREFIX) else "other",
                       resubscribe=resub)
        elif verb == "UNSUB":
            row["event"] = "unsubscribe"
        elif verb == "PING":
            with self._lock:
                self.ping_sent.append(self.wall())
                self._session["last_ping_mono"] = self.mono()
            row["event"] = "ping_sent"
        elif verb == "PONG":
            row["event"] = "pong_sent"
        else:
            row["event"] = "sent_other"
        self.recorder.write("connection", row)

    def on_wire(self, sock: Any, op: NatsOp) -> None:
        """Sunucudan gelen işlem (MSG dışında): yalnızca türü, zamanı ve -ERR'in bilinen metni."""
        ts, mono = self.wall(), self.mono()
        if op.kind == "MSG":
            with self._lock:
                self._session["msgs"] = self._session.get("msgs", 0) + 1
            return
        row: Dict[str, Any] = {"session": sock.session}
        if op.kind == "INFO":
            if sock.info_seen:
                return
            sock.info_seen = True
            since = mono - sock.handshake_mono
            with self._lock:
                self.handshake_to_info.append(since)
            row.update(event="info", handshake_to_info_s=round(since, 3))  # gövde asla
        elif op.kind == "PING":
            with self._lock:
                gap = ts - self.server_pings[-1] if self.server_pings else None
                self.server_pings.append(ts)
            row.update(event="server_ping", gap_s=round(gap, 3) if gap is not None else None)
        elif op.kind == "PONG":
            with self._lock:
                gap = ts - self.pongs[-1] if self.pongs else None
                self.pongs.append(ts)
                sent = self._session.get("last_ping_mono") if self.connection is not None else None
                rtt = mono - sent if sent is not None else None
                if rtt is not None:
                    self.rtt.append(rtt)
            row.update(event="pong", rtt_s=round(rtt, 3) if rtt is not None else None,
                       gap_s=round(gap, 3) if gap is not None else None)
        elif op.kind == "-ERR":
            with self._lock:
                self.errors[op.text] += 1
            row.update(event="error", text=op.text)  # bilinen NATS metni ya da "other"
        else:
            row.update(event="received_other", op="+OK" if op.kind == "+OK" else "OTHER")
        self.recorder.write("connection", row, ts=ts, mono=mono)

    def on_session_end(self, n: int, outcome: str, reason: str, lasted: float, *, killed: Optional[str]) -> None:
        ts = self.wall()
        why = f"{outcome}: {reason}" + (f" ({killed})" if killed else "")
        with self._lock:
            self._conn_open = False
            session = dict(self._session)
            session.update(outcome=outcome, reason=reason, lasted=lasted, killed=killed, end_ts=ts)
            self.sessions.append(session)
            self.drops[why] += 1
            # musluk sporları oturum bitmeden kapattı ("closed"); pencerenin nedeni oturumunki olsun
            for sport in self.sports:
                st = self.stats[sport]
                if st.open_since is not None:
                    st.open_intervals.append([st.open_since, ts, why])
                    st.open_since = None
                elif st.open_intervals and st.open_intervals[-1][2] == "closed" and ts - st.open_intervals[-1][1] < 5:
                    st.open_intervals[-1][2] = why
        self.recorder.write("connection", {"event": "session_end", "session": n, "outcome": outcome,
                                           "reason": reason[:200], "lasted_s": round(lasted, 3), "killed": killed,
                                           "messages": session.get("msgs", 0)})

    def on_retry(self, reason: str, *, failures: int, reading: bool, unhealthy: bool) -> None:
        with self._lock:
            self.retries += 1
        self.recorder.write("connection", {"event": "retry_wait", "reason": reason[:200], "failures": failures,
                                           "reading": reading, "unhealthy": unhealthy})

    def on_credential_start(self, sport: str, n: int, purpose: str) -> Dict[str, Any]:
        token = {"sport": sport, "n": n, "purpose": purpose, "start_mono": self.mono(), "peak_mb": 0.0,
                 "samples": 0}
        with self._lock:
            self._reading = token
        self.recorder.write("connection", {"event": "credential_read_start", "sport": sport, "read": n,
                                           "purpose": purpose})
        self._wake_sampler.set()
        return token

    def on_credential_end(self, token: Dict[str, Any], *, ok: bool, error: Optional[str] = None,
                          **extra: Any) -> None:
        self._sample(token)  # okuma bitmeden son bir ölçüm (tarayıcı kapanmadan önceki zirve kaçmasın)
        with self._lock:
            self._reading = None
            entry = {"sport": token["sport"], "read": token["n"], "purpose": token["purpose"], "ok": ok,
                     "error": error, "seconds": round(self.mono() - token["start_mono"], 3),
                     "peak_rss_total_mb": token["peak_mb"], "samples": token["samples"], **extra}
            self.credential_reads.append(entry)
        self.recorder.write("connection", {"event": "credential_read", **entry})

    def on_credential_compare(self, base_sport: str, other: str, options: Optional[str],
                              address: Optional[str]) -> None:
        entry = {"read_from": base_sport, "compared_with": other, "connect_options": options or "unavailable",
                 "address": address or "unavailable"}
        with self._lock:
            self.compares.append(entry)
        self.recorder.write("connection", {"event": "credential_compare", **entry})

    def on_system(self, type_: str, data: Dict[str, Any]) -> None:
        ts = self.wall()
        if type_ == sv.SYSTEM_SOURCE_CHANGED:
            sport, to = str(data.get("sport")), str(data.get("to"))
            fallback = "start" if to == LEADER_POLL else "stop" if data.get("from") == LEADER_POLL else None
            with self._lock:
                st = self.stats.setdefault(sport, SportStats())
                st.leader.append((ts, to))
                if to == self.push_name and st.first_push_at is None:
                    st.first_push_at = ts
                self.switches.append({"ts": ts, **data})
            self.recorder.write("leader", {"event": "switch", "sport": sport, "from": data.get("from"), "to": to,
                                           "reason": data.get("reason"), "fallback": fallback})
        elif type_ in (sv.SYSTEM_BLOCKED, sv.SYSTEM_RECOVERED):
            with self._lock:
                self.blocked.append({"ts": ts, "type": type_})
            self.recorder.write("leader", {"event": type_.split(".", 1)[1],
                                           **{k: data.get(k) for k in ("retry_in_s", "blocked_for_s")}})

    def poll_led_sports(self) -> List[str]:
        with self._lock:
            return [s for s in self.sports if self.stats[s].leader and self.stats[s].leader[-1][1] == LEADER_POLL]

    # --- REST ---------------------------------------------------------------------------------------------------

    def _note_budget(self) -> None:
        with self._lock:
            if self._budget_noted:
                return
            self._budget_noted = True
        self.recorder.write("reads", {"kind": "budget_exhausted", "used": self.client.used,
                                      "budget": self.client.budget})

    def _count(self, by: str, kind: str, out: Outcome) -> None:
        with self._lock:
            self.requests[f"{by}:{kind}:{out.status}"] += 1
            self.request_latency[kind].append(out.seconds)

    def _event_seen(self, sport: Optional[str], eid: int, event: Optional[Mapping[str, Any]]) -> None:
        if not event:
            return
        with self._lock:
            sports = [sport] if sport else [s for s in self.sports if self.tracker.state(s, eid) is not None]
            for s in sports:
                self.last_read[(s, eid)] = self.mono()
                self.tracker.observe_event(s, event)

    def _service_fetch(self, path: str) -> Optional[Dict[str, Any]]:
        """Servisin istek işlevi: bütçe, sıra, kayıt; canlı liste ve maç sayfaları kaydediciye de verilir."""
        trigger_ts = self.wall()
        out = self.client.request(path)
        m, e = LIST_PATH.match(path), EVENT_PATH.match(path)
        kind = "list" if m else "event" if e else "other"
        if out.refused:
            self._note_budget()
            return None
        row: Dict[str, Any] = {"kind": kind, "by": "service", "status": out.status, "latency_s": round(out.seconds, 3),
                               "waited_s": round(out.waited, 3)}
        if m:
            row["sport"] = m.group(1)
        if e:
            row["event_id"] = int(e.group(1))
        events: Optional[List[Any]] = None
        if m and out.data is not None:
            events = [ev for ev in (out.data.get("events") or []) if isinstance(ev, dict)]
            row["live_count"] = len(events)
        self._count("service", kind, out)
        self.recorder.write("reads", row)
        if m and events is not None:
            self._on_list(m.group(1), events, trigger_ts)
        if e and out.data is not None:
            self._event_seen(None, int(e.group(1)), out.data.get("event"))
        if out.blocked is not None:
            raise out.blocked
        return out.data

    def _on_list(self, sport: str, events: List[Dict[str, Any]], ts: float) -> None:
        mono = self.mono()
        with self._lock:
            changes = self.tracker.listing(sport, events)
            st = self.stats.setdefault(sport, SportStats())
            st.live_counts.append((self.wall(), len(events)))
            ids: List[int] = []
            for ev in events:
                try:
                    ids.append(int(str(ev.get("id"))))
                except ValueError:
                    continue
            self._latest_list[sport] = ids
            for _, kinds, _ in changes:
                for kind in kinds:
                    st.triggers[kind] += 1
        for eid, kinds, expect in changes:
            self.reads.want(Pending(sport, eid, set(kinds), {"list"}, ts, mono, expect, mono))

    def latest_list(self, sport: str) -> List[int]:
        with self._lock:
            return list(self._latest_list.get(sport, []))

    def read_since(self, sport: str, eid: int, since: float) -> bool:
        with self._lock:
            last = self.last_read.get((sport, eid))
        return last is not None and last >= since

    def near_end(self, sport: str, eid: int) -> bool:
        """Servisin yoklama kaynağının işareti (indirgeyicinin `near_end`'i); okunamazsa False."""
        try:
            slot = self.service._slots.get(sport)
            return bool(slot is not None and (slot.tracker.state.get(str(eid)) or {}).get("near_end"))
        except Exception:
            return False

    def on_event_read(self, p: Pending, out: Outcome, event: Optional[Mapping[str, Any]], *, ok: bool, final: bool,
                      latency: Optional[float], retry_in: Optional[float], ts: float, mono: float) -> None:
        cls = classify_status(dict(event)).value if event else None
        self._count("recorder", "event", out)
        self._event_seen(p.sport, p.eid, event)
        with self._lock:
            st = self.stats.setdefault(p.sport, SportStats())
            if ok and latency is not None:
                st.reads_ok += 1
                for kind in p.kinds:
                    st.read_latency[kind].append(latency)
            elif final:
                st.not_reflected += 1
        self.recorder.write("reads", {
            "kind": "event", "by": "recorder", "sport": p.sport, "event_id": p.eid, "status": out.status,
            "latency_s": round(out.seconds, 3), "waited_s": round(out.waited, 3), "attempt": p.attempts,
            "trigger": {"kinds": sorted(p.kinds), "sources": sorted(p.sources), "ts": round(p.first_ts, 3)},
            "read_class": cls, "reflects": ok, "trigger_to_read_s": round(latency, 3) if latency is not None else None,
            "retry_in_s": retry_in, "final": final,
        }, ts=ts, mono=mono)

    def on_event_skipped(self, p: Pending) -> None:
        self._note_budget()
        with self._lock:
            self.stats.setdefault(p.sport, SportStats()).skipped_budget += 1

    def fallback_read(self, sport: str, eid: int, *, window_start_ts: float) -> Outcome:
        out = self.client.request(f"/event/{eid}")
        if out.refused:
            self._note_budget()
            return out
        event = (out.data or {}).get("event") if out.data is not None else None
        self._count("fallback", "event", out)
        self._event_seen(sport, eid, event)
        self.recorder.write("reads", {
            "kind": "event", "by": "fallback", "sport": sport, "event_id": eid, "status": out.status,
            "latency_s": round(out.seconds, 3), "waited_s": round(out.waited, 3),
            "read_class": classify_status(dict(event)).value if event else None,
            "minute": round(window_start_ts, 3)})
        return out

    def on_fallback_minute(self, sport: str, start_ts: float, *, live: int, made: int, covered: int, priority: int,
                           cap: int, cut: Optional[str], partial: bool) -> None:
        skipped = max(0, live - covered)
        row = {"sport": sport, "minute": round(start_ts, 3), "live": live, "reads_made": made, "covered": covered,
               "skipped": skipped, "coverage": round(covered / live, 4) if live else None, "priority_live": priority,
               "cap": cap, "cut": cut, "partial": partial}
        with self._lock:
            self.stats.setdefault(sport, SportStats()).fallback.append(row)
        self.recorder.write("fallback", row)

    # --- kaynaklar ---------------------------------------------------------------------------------------------

    def _sample(self, token: Optional[Dict[str, Any]] = None) -> None:
        sample = sample_resources()
        with self._lock:
            reading = token or self._reading
            total = sample.get("rss_total_mb") or sample.get("rss_self_mb") or 0.0
            if reading is not None:
                reading["peak_mb"] = round(max(reading["peak_mb"], total), 1)
                reading["samples"] += 1
            for key, field_name in (("self", "rss_self_mb"), ("bridge", "rss_bridge_mb"), ("live", "rss_live_mb"),
                                    ("total", "rss_total_mb")):
                self.peak_rss_mb[key] = max(self.peak_rss_mb[key], sample.get(field_name) or 0.0)
            self.last_resources = sample
            open_now = any(st.open_since is not None for st in self.stats.values())
        self.recorder.write("resources", {**sample, "credential_read": reading is not None, "push_open": open_now})

    def _sample_loop(self) -> None:
        while not self._stop.is_set():
            self._sample()
            with self._lock:
                interval = self._reading_resource_seconds if self._reading is not None else self._resource_seconds
            self._wake_sampler.wait(interval)
            self._wake_sampler.clear()

    # --- özet ----------------------------------------------------------------------------------------------------

    @staticmethod
    def _windows(intervals: Sequence[Sequence[Any]], start: float, end: float) -> List[Dict[str, Any]]:
        """Açık aralıkların tümleyeni: kesinti pencereleri ve nedenleri (pencereden önce kapanan aralığın nedeni)."""
        ends = {round(float(iv[1]), 6): str(iv[2]) for iv in intervals}
        out = []
        for s, e in complement([(float(iv[0]), float(iv[1])) for iv in intervals], start, end):
            cause = "start" if abs(s - start) < 1e-6 else ends.get(round(s, 6), "unknown")
            out.append({"start": utc(s), "end": utc(e), "seconds": round(e - s, 3), "cause": cause,
                        "until_run_end": e >= end - 1e-6})
        return out

    @staticmethod
    def _fallback_summary(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        full = [r for r in rows if not r.get("partial")]
        live = sum(r["live"] for r in full)
        skipped = sum(r["skipped"] for r in full)
        scored = [r for r in full if r["live"]]
        worst = min(scored, key=lambda r: (r["coverage"], -r["skipped"]), default=None)
        return {"minutes": len(full), "live_total": live, "covered_total": sum(r["covered"] for r in full),
                "skipped_total": skipped, "reads_made": sum(r["reads_made"] for r in full),
                "skip_rate": round(skipped / live, 4) if live else None,
                "worst_minute": None if worst is None else {
                    "at": utc(worst["minute"]), "sport": worst["sport"], "live": worst["live"],
                    "covered": worst["covered"], "skipped": worst["skipped"], "coverage": worst["coverage"],
                    "cut": worst["cut"]}}

    def summary(self, *, final: bool) -> Dict[str, Any]:
        with self._lock:
            start = self.started_ts or self.wall()
            end = self.ended_ts if (final and self.ended_ts) else self.wall()
            duration = max(1e-9, end - start)
            pongs, server_pings, client_pings = sorted(self.pongs), sorted(self.server_pings), sorted(self.ping_sent)
            signs = sorted(pongs + server_pings)
            all_open: List[List[Any]] = []
            sports: Dict[str, Any] = {}
            fallback_rows: List[Mapping[str, Any]] = []
            for sport, st in sorted(self.stats.items()):
                opens = [list(iv) for iv in st.open_intervals]
                if st.open_since is not None:
                    opens.append([st.open_since, end, "open"])
                all_open.extend(opens)
                open_iv = [(float(a), float(b)) for a, b, _ in opens]
                push = leader_intervals(st.leader, start, end, self.push_name)
                positive = step_positive(st.live_counts, start, end)
                gaps = []
                for s, e in quiet_gaps(st.frames, positive, start, end)[:TOP_GAPS]:
                    length = max(1e-9, e - s)
                    before = [n for t, n in st.live_counts if t <= s][-1:]
                    during = [n for t, n in st.live_counts if s - 1e-6 <= t <= e]
                    gaps.append({
                        "seconds": round(e - s, 3), "start": utc(s), "end": utc(e),
                        "live_count_max": max(before + during or [0]),
                        "leader_push_share": round(overlap(push, s, e) / length, 3),
                        "connection_open_share": round(overlap(open_iv, s, e) / length, 3),
                        "pongs": count_between(pongs, s, e), "server_pings": count_between(server_pings, s, e),
                        "client_pings": count_between(client_pings, s, e),
                        "longest_without_ping_or_pong_s": round(longest_without(signs, s, e), 3),
                    })
                fallback_rows.extend(st.fallback)
                sports[sport] = {
                    "frames": len(st.frames), "status_frames": st.status_frames, "score_frames": st.score_frames,
                    "finish_frames": st.finish_frames, "distinct_events": len(st.events),
                    "triggers": dict(sorted(st.triggers.items())),
                    "event_reads_reflecting": st.reads_ok, "event_reads_not_reflected": st.not_reflected,
                    "event_reads_skipped_budget": st.skipped_budget,
                    "frame_lag_s": describe(st.frame_lag),
                    "finish_push_lag_s": describe(st.finish_lag),
                    "trigger_to_event_read_s": {k: describe(v) for k, v in sorted(st.read_latency.items())},
                    "live_list_reads": len(st.live_counts),
                    "live_count_max": max((n for _, n in st.live_counts), default=0),
                    "fallback_time_share": round((duration - overlap(push, start, end)) / duration, 4),
                    "time_to_first_push_s": round(st.first_push_at - start, 3) if st.first_push_at else None,
                    "downtime_windows": self._windows(opens, start, end),
                    "longest_msg_gaps_while_live": gaps,
                    "fallback": self._fallback_summary(st.fallback),
                }
            conn_windows = self._windows(all_open, start, end)
            reads = self.credential_reads
            notes = [SCOPE_NOTE,
                     "Push lag = receive time - changes.changeTimestamp (SofaScore's change time); the local clock's "
                     "offset is part of it.",
                     "longest_msg_gaps_while_live: intervals with no MSG frame of that sport while its last live list "
                     "showed more than 0 matches. leader_push_share 1.0 means the arbiter judged push healthy for the "
                     f"whole gap (a PING or PONG within {SILENCE_SECONDS:.0f} s keeps it healthy without frames).",
                     "fallback: while polling leads, every live match's /event/{id} at least once a minute within "
                     f"{self.fallback.cap} reads a minute (rate x (1 - list share)); a match not read within its minute "
                     "is skipped and counted, never carried over.",
                     "RSS: bridge = the main bridge's browser (REST reads), live = the live profile's browser (page "
                     "source pages or the direct credential reader), other = other children (Playwright driver)."]
            notes.insert(0, CLIENT_NOTE if self.source == "direct" else PAGE_NOTE)
            return {
                "run_id": self.run_id, "source": self.source, "final": final, "written_at": utc(self.wall()),
                "started": utc(start), "ended": utc(end) if final else None, "duration_s": round(end - start, 3),
                "sports": list(self.sports), "args": self.args_note, "notes": notes,
                "sports_detail": sports,
                "fallback": self._fallback_summary(fallback_rows),
                "connection": {
                    "sessions": len(self.sessions),
                    "opens": self.connection.opens if self.connection is not None else None,
                    "credential_reads": reads, "credential_compares": self.compares,
                    "credential_read_peak_rss_total_mb": max((c["peak_rss_total_mb"] for c in reads), default=None),
                    "rejections": self.connection.rejections if self.connection is not None else None,
                    "retries": self.retries,
                    "unhealthy": self.connection.unhealthy if self.connection is not None else None,
                    "session_ends": dict(sorted(self.drops.items())),
                    "session_seconds": describe([s["lasted"] for s in self.sessions if s.get("outcome") != "stopped"]),
                    "handshake_s": describe(self.handshake_seconds),
                    "handshake_to_info_s": describe(self.handshake_to_info),
                    "pong_rtt_s": describe(self.rtt),
                    "pong_gap_s": describe([b - a for a, b in zip(pongs, pongs[1:], strict=False)]),
                    "server_ping_gap_s": describe([b - a for a, b in zip(server_pings, server_pings[1:],
                                                                         strict=False)]),
                    "errors": dict(sorted(self.errors.items())),
                    "kills": [{"at": utc(k["ts"]), "cause": k["cause"], "done": k["done"]} for k in self.kill_log],
                    "downtime_windows": conn_windows,
                    "downtime_s": round(sum(w["seconds"] for w in conn_windows), 3),
                },
                "leader_switches": [{"at": utc(s["ts"]), "sport": s.get("sport"), "from": s.get("from"),
                                     "to": s.get("to"), "reason": s.get("reason")} for s in self.switches],
                "blocked": [{"at": utc(b["ts"]), "type": b["type"]} for b in self.blocked],
                "requests": {"used": self.client.used, "budget": self.client.budget, "refused": self.client.refused,
                             "by_who_kind_status": dict(sorted(self.requests.items())),
                             "latency_s": {k: describe(v) for k, v in sorted(self.request_latency.items())},
                             "pending_event_reads": self.reads.pending(), "dropped_overflow": self.reads.overflow},
                "resources": {"peak_rss_mb": dict(self.peak_rss_mb), "last": self.last_resources},
                "service": {"error": self.service_error, **{k: v for k, v in self.service.report.to_dict().items()
                                                            if k in ("rounds", "requests", "push_frames",
                                                                     "source_switches", "leaders")}},
            }

    def write_summary(self, *, final: bool) -> None:
        self._last_summary = self.mono()
        self.recorder.write_summary(self.summary(final=final))


# --- komut satırı ----------------------------------------------------------------------------------------------


def _sports(value: str) -> List[str]:
    sports = [s.strip().lower() for s in value.split(",") if s.strip()]
    if not sports or not all(re.fullmatch(r"[a-z0-9-]+", s) for s in sports):
        raise argparse.ArgumentTypeError("a comma-separated list of sport slugs")
    return list(dict.fromkeys(sports))


def _ids(value: str) -> List[int]:
    try:
        return [int(v) for v in value.split(",") if v.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("a comma-separated list of event ids")


def _share(value: str) -> float:
    share = float(value)
    if not 0.0 <= share < 1.0:
        raise argparse.ArgumentTypeError("a share from 0 up to (not including) 1")
    return share


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="soak_live.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sports", type=_sports, default=list(DEFAULT_SPORTS),
                   help="comma-separated sports (default football,basketball,tennis)")
    p.add_argument("--source", choices=SOURCES, default="direct", help="push source to measure (default direct)")
    p.add_argument("--hours", type=float, default=DEFAULT_HOURS, help=f"run length (default {DEFAULT_HOURS:g})")
    p.add_argument("--run-id", default=None, help="id of the run (default: UTC timestamp); a used id is refused")
    p.add_argument("--max-requests", type=int, default=None,
                   help="budget of REST reads (lists and event pages) for this run (default --hours x 3600 x --rate)")
    p.add_argument("--rate", type=float, default=DEFAULT_RATE, help="REST reads per second (default 1)")
    p.add_argument("--list-share", type=_share, default=DEFAULT_LIST_SHARE,
                   help="share of the rate kept for discovery and list reads while polling leads (default 0.5)")
    p.add_argument("--priority-ids", type=_ids, default=[], metavar="IDS",
                   help="event ids read first by the per-minute fallback (default none)")
    p.add_argument("--kill-every", type=float, default=None, metavar="MINUTES",
                   help="force-close the push connection (direct) or the pages (page) every MINUTES")
    p.add_argument("--kill-at", type=float, default=None, metavar="MINUTES",
                   help="force-close it once, MINUTES after the start")
    p.add_argument("--compare-credential-sports", action="store_true",
                   help="direct: also read the credential from the other sports' pages; records same/different only")
    p.add_argument("--poll-interval", type=float, default=30.0, help="list interval while polling leads (default 30)")
    p.add_argument("--out", default=OUT, help="output root (default research/soak); the run writes <out>/<run-id>/")
    p.add_argument("--data-dir", default=None,
                   help=f"the live service's store (default {STORE_ROOT}/<run-id>); never inside --out")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.hours <= 0 or args.rate <= 0:
        print("error: --hours and --rate must be positive", file=sys.stderr)
        return 2
    run_id = args.run_id or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id):
        print("error: --run-id may contain letters, digits, '.', '_' and '-' only", file=sys.stderr)
        return 2
    out_dir = os.path.join(args.out, run_id)
    if os.path.isdir(out_dir) and os.listdir(out_dir):
        print(f"error: run {run_id!r} already has output in {out_dir}; choose another --run-id", file=sys.stderr)
        return 2
    data_dir = args.data_dir or os.path.join(STORE_ROOT, run_id)
    if os.path.abspath(data_dir).startswith(os.path.abspath(out_dir) + os.sep):
        print("error: --data-dir must not be inside the run's output folder", file=sys.stderr)
        return 2
    if args.compare_credential_sports and args.source != "direct":
        print("error: --compare-credential-sports needs --source direct", file=sys.stderr)
        return 2
    budget = args.max_requests if args.max_requests is not None else default_budget(args.hours, args.rate)
    os.environ.setdefault("SOFASCORE_CLIENT__THROTTLE_DIR", THROTTLE_DIR)
    os.environ["SOFASCORE_STORAGE__DATA_DIR"] = data_dir
    os.makedirs(data_dir, exist_ok=True)
    note = {"source": args.source, "hours": args.hours, "max_requests": budget, "rate": args.rate,
            "list_share": args.list_share, "priority_ids": len(args.priority_ids),
            "kill_every_min": args.kill_every, "kill_at_min": args.kill_at,
            "compare_credential_sports": args.compare_credential_sports, "poll_interval_s": args.poll_interval}
    soak = Soak(out_dir, args.sports, run_id=run_id, data_dir=data_dir, budget=budget, source=args.source,
                hours=args.hours, kill_every=args.kill_every, kill_at=args.kill_at, poll_interval=args.poll_interval,
                rate=args.rate, list_share=args.list_share, priority_ids=args.priority_ids,
                compare_sports=args.compare_credential_sports, args_note=note)

    def on_signal(signum: int, frame: Any) -> None:
        soak.request_stop()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    print(f"soak {run_id}: source {args.source}, sports {', '.join(args.sports)}, {args.hours:g} h, budget {budget} "
          f"REST reads at {args.rate:g}/s; output {out_dir}", flush=True)
    soak.run(args.hours)
    print(f"soak {run_id}: done; summary {os.path.join(out_dir, 'summary.json')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
