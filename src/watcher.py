"""
Canlı izleyici: olay üretir, sonuçlandırmaz.

Parametrelerin dayanağı docs/status-matrix/README.md:
  - `events/live` CDN `s-maxage=5`; düdük → finished medyan 20 sn, maks 302 sn → 30 sn aralık.
  - Listeden düşmek bitişin en erken sinyali → hemen /event/{id}.
  - Askıya alınan maç aynı id ile ertesi güne kayabiliyor (B9/T6, B13) → takılı maç eşiği başlangıç + 4 sa.
Kesinlik 03-A pencere kuralındadır (src/refresh.py); izleyici yalnızca ilk COMPLETED'i `provisional` olarak bildirir.

Olaylar data/watch_events.jsonl'a yazılır ve isteğe bağlı `on_event` ile verilir. Son bilinen durum
data/watch_state_{sport}.json'da (spor başına ayrı dosya: her spor için ayrı süreç birbirini ezmez);
yeniden başlatmada aynı geçiş iki kez olay yapılmaz.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.fsutil import atomic_write_json
from src.refresh import refresh_window_hours
from src.status import StatusClass, classify_status, extract_scores

logger = logging.getLogger(__name__)

BASE_URL = "https://www.sofascore.com/api/v1"
LIST_INTERVAL_SECONDS = 30
EVENT_INTERVAL_SECONDS = 30
EVENT_INTERVAL_SLOW_SECONDS = 60  # hız bütçesi aşılınca
STUCK_INTERVAL_SECONDS = 300
STUCK_AFTER_SECONDS = 4 * 3600
# Tenis: startTimestamp planlanan saattir (aynı kortta sıra, yağmur); retro verisinde bitmiş 60 maçın 5'i
# başlangıçtan > 4 sa sonra bitti (maks 5,46 sa). Ölçü gerçek oyun başlangıcı, eşik 6 sa.
STUCK_AFTER_SECONDS_TENNIS = 6 * 3600
EVENT_PAGES_PER_MINUTE = 40  # liste 2/dk + maç sayfaları ≤ 40/dk → < 1 istek/sn
MIN_REQUEST_SPACING_SECONDS = 1.0
WATCH_EVENTS_FILE = "watch_events.jsonl"
WATCH_STATE_FILE = "watch_state_{sport}.json"

_TERMINAL = (StatusClass.COMPLETED, StatusClass.DECIDED_WITHOUT_PLAY)


def max_event_polls() -> int:
    """WATCH_MAX_EVENT_POLLS: aynı turda en fazla kaç maç sayfası (varsayılan 20)."""
    try:
        return max(1, int(os.getenv("WATCH_MAX_EVENT_POLLS", "20")))
    except ValueError:
        return 20


def _utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


def near_end(event: Dict[str, Any], sport: str, now: float) -> bool:
    """
    Bitişe yakın mı (maç sayfası da izlenir):
      futbol: 2. yarı ≥ 80. dk ya da injuryTime2 görüldü; uzatma/penaltı kodları
      basketbol: son periyot (4. çeyrek ya da 2. yarı, uzatma) ve played ≥ %90 (played yoksa yalnız periyot)
      tenis: son set (defaultPeriodCount, yoksa 3)
    """
    status = event.get("status") or {}
    code = status.get("code")
    t = event.get("time") or {}
    if sport == "football":
        if code in (6, 31):
            return False
        if code == 7:
            if t.get("injuryTime2") is not None:
                return True
            start = t.get("currentPeriodStartTimestamp")
            if not start:
                return False
            minute = ((t.get("initial") or 2700) + now - start) / 60
            return minute >= 80
        if code == 20:  # periyot bilgisi yok: başlangıçtan 95 dk (80 + devre arası)
            start = event.get("startTimestamp") or now
            return now - start >= 95 * 60
        return True  # uzatma, penaltılar ve bilinmeyen canlı kodlar
    if sport == "basketball":
        regulation = (t.get("periodLength") or 0) * (t.get("totalPeriodCount") or 0)
        played = t.get("played")
        if played is not None and regulation:
            # %90 ancak son periyotta (4. çeyrek / 2. yarı) ya da uzatmada aşılır
            return played >= 0.9 * regulation
        # Saat verisi yok (K7: çoğu alt lig): yalnızca periyot kodu; 16, uzatma ve bilinmeyen canlı kodlar
        return code not in (13, 14, 15, 30, 31)
    if sport == "tennis":
        sets = event.get("defaultPeriodCount") or 3
        return isinstance(code, int) and 8 <= code <= 12 and code - 7 >= sets
    return False


def play_start(event: Dict[str, Any]) -> Optional[float]:
    """
    Tenis: gerçek oyun başlangıcı = currentPeriodStartTimestamp − biten setlerin süreleri (time.periodN, sn);
    set süreleri yoksa currentPeriodStartTimestamp. Zaman bilgisi yoksa None (çağıran startTimestamp'e düşer).
    """
    t = event.get("time") or {}
    current = t.get("currentPeriodStartTimestamp")
    if not isinstance(current, (int, float)) or not current:
        return None
    done = sum(v for k, v in t.items() if k.startswith("period") and k[6:].isdigit() and isinstance(v, (int, float)))
    return current - done


def _score_key(event: Dict[str, Any]) -> List[Any]:
    """Canlı skor karşılaştırması: display (futbolda current penaltıları içerir), yoksa current."""
    h, a = event.get("homeScore") or {}, event.get("awayScore") or {}
    return [h.get("display", h.get("current")), a.get("display", a.get("current"))]


class MatchWatcher:
    def __init__(
        self,
        sport: str,
        event_ids: Optional[Iterable[int]] = None,
        league_ids: Optional[Iterable[int]] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        data_dir: str = "data",
        fetch_json: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not event_ids and not league_ids:
            raise ValueError("event_ids ya da league_ids gerekli")
        self.sport = sport
        self.event_ids = {int(e) for e in event_ids or []}
        self.league_ids = {int(x) for x in league_ids or []}
        self.on_event = on_event
        self.data_dir = data_dir
        self._fetch = fetch_json or self._default_fetch
        self._clock = clock
        self._sleep = sleep
        self._last_request = 0.0
        self.requests = 0
        self.event_interval = EVENT_INTERVAL_SECONDS
        self._slow_warned = False
        self._stop = False
        self.events_path = os.path.join(data_dir, WATCH_EVENTS_FILE)
        self.state_path = os.path.join(data_dir, WATCH_STATE_FILE.format(sport=sport))
        self.state: Dict[str, Dict[str, Any]] = self._load_state()

    # --- ağ ---------------------------------------------------------------------------

    @staticmethod
    def _default_fetch(path: str) -> Optional[Dict[str, Any]]:
        from src.exceptions import ResourceNotFoundError
        from src.utils import make_api_request

        try:
            return make_api_request(f"{BASE_URL}{path}")
        except ResourceNotFoundError:
            return None

    def _get(self, path: str) -> Optional[Dict[str, Any]]:
        wait = MIN_REQUEST_SPACING_SECONDS - (self._clock() - self._last_request)
        if wait > 0:
            self._sleep(wait)
        self._last_request = self._clock()
        self.requests += 1
        try:
            return self._fetch(path)
        except Exception as e:  # tek istek hatası izlemeyi durdurmasın
            logger.warning(f"İzleyici isteği başarısız: {path}: {e}")
            return None

    # --- durum ------------------------------------------------------------------------

    def _load_state(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        atomic_write_json(self.state_path, self.state)

    def _emit(self, event: Dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False)
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if self.on_event:
            self.on_event(json.loads(line))  # dosyadakiyle aynı biçim (Pair → liste, enum → metin)

    def _in_scope(self, eid: str, s: Dict[str, Any]) -> bool:
        """State dosyasında önceki koşulardan kalan başka maçlar izlenmez."""
        return int(eid) in self.event_ids or s.get("tournament_id") in self.league_ids

    def active_ids(self) -> List[str]:
        """İzlenmeye devam eden maçlar: kapsamda, bitmemiş ve iptal/erteleme ile düşmemiş."""
        return [k for k, s in self.state.items() if not s.get("done") and self._in_scope(k, s)]

    def _observe(self, event: Dict[str, Any], source: str) -> None:
        """Bir maçın yeni görüntüsü: sınıf değişimi, canlı skor, takılı maç."""
        eid = str(event.get("id"))
        now = self._clock()
        cls = classify_status(event)
        s = self.state.setdefault(eid, {"class": None, "done": False})
        prev = s.get("class")
        prev_start = s.get("start_ts")
        s["start_ts"] = event.get("startTimestamp", prev_start)
        s["tournament_id"] = ((event.get("tournament") or {}).get("uniqueTournament") or {}).get("id") or s.get("tournament_id")

        if prev is not None and prev != cls.value:
            scores = dataclasses.asdict(extract_scores(event, self.sport))
            scores["status_class"] = cls.value
            ev = {
                "type": "status_changed",
                "event_id": int(eid),
                "from": prev,
                "to": cls.value,
                "at_utc": _utc(now),
                "change_ts": (event.get("changes") or {}).get("changeTimestamp"),
                "source": source,
                "scores": scores,
            }
            if cls is StatusClass.COMPLETED and not s.get("completed_emitted"):
                # 03-A: başlangıç + REFRESH_WINDOW_HOURS dolmadıysa sonuç geçici
                window = refresh_window_hours()
                start = s.get("start_ts")
                closed = bool(window) and isinstance(start, (int, float)) and now >= start + window * 3600
                ev["provisional"] = not closed
                s["completed_emitted"] = True
            self._emit(ev)
        elif cls is StatusClass.LIVE and prev == StatusClass.LIVE.value:
            key = _score_key(event)
            if s.get("score") is not None and key != s.get("score"):
                self._emit({
                    "type": "score_changed",
                    "event_id": int(eid),
                    "at_utc": _utc(now),
                    "from": s.get("score"),
                    "to": key,
                    "change_ts": (event.get("changes") or {}).get("changeTimestamp"),
                    "source": source,
                })
        s["class"] = cls.value
        s["score"] = _score_key(event)
        s["near_end"] = cls is StatusClass.LIVE and near_end(event, self.sport, now)

        if cls in _TERMINAL:
            s["done"] = True
        elif cls is StatusClass.VOID:
            # Ertesi güne taşınmış maç aynı id ile izlenmeye devam eder (B13/T6)
            moved = isinstance(s["start_ts"], (int, float)) and s["start_ts"] > now
            s["done"] = not moved
        else:
            s["done"] = False

        if self.sport == "tennis":
            s["play_start"] = play_start(event) or s.get("play_start")
            start = s.get("play_start") or s.get("start_ts")
            limit = STUCK_AFTER_SECONDS_TENNIS
        else:
            start, limit = s.get("start_ts"), STUCK_AFTER_SECONDS
        stuck_now = (
            cls in (StatusClass.LIVE, StatusClass.NOT_STARTED)
            and isinstance(start, (int, float))
            and now > start + limit
        )
        if stuck_now and not s.get("stuck"):
            self._emit({"type": "stuck", "event_id": int(eid), "at_utc": _utc(now), "start_ts": start,
                        "status_class": cls.value})
        if prev_start is not None and s["start_ts"] != prev_start:
            s["stuck"] = False  # yeni başlangıç: eşik yeniden sayılır
        else:
            s["stuck"] = stuck_now or bool(s.get("stuck") and cls in (StatusClass.LIVE, StatusClass.NOT_STARTED))

    # --- tur --------------------------------------------------------------------------

    def start(self) -> None:
        """event_ids modunda her maçın başlangıç durumu (olay üretmez; state'te yoksa)."""
        for eid in sorted(self.event_ids):
            if str(eid) in self.state:
                continue
            data = self._get(f"/event/{eid}")
            event = (data or {}).get("event")
            if event:
                s = self.state.setdefault(str(eid), {"class": None, "done": False})
                s["class"] = None
                s["last_event_poll"] = self._clock()
                self._observe(event, "event")
        self._save_state()

    def _event_poll_due(self, s: Dict[str, Any], now: float) -> bool:
        last = s.get("last_event_poll") or 0
        if s.get("stuck"):
            return now - last >= STUCK_INTERVAL_SECONDS
        if s.get("near_end"):
            return now - last >= self.event_interval
        if s.get("class") == StatusClass.NOT_STARTED.value:
            start = s.get("start_ts")
            # başlaması gereken ama listede görünmeyen maç (ör. erteleme): 5 dk'da bir
            return isinstance(start, (int, float)) and now >= start and now - last >= STUCK_INTERVAL_SECONDS
        return False

    def tick(self) -> None:
        """Bir tur: canlı liste + gereken maç sayfaları."""
        data = self._get(f"/sport/{self.sport}/events/live")
        live_events = (data or {}).get("events") if data is not None else None
        in_list: Dict[str, Dict[str, Any]] = {}
        for ev in live_events or []:
            eid = str(ev.get("id"))
            ut = ((ev.get("tournament") or {}).get("uniqueTournament") or {}).get("id")
            if int(eid) in self.event_ids or (ut is not None and ut in self.league_ids):
                in_list[eid] = ev

        for eid, ev in in_list.items():
            if eid not in self.state:
                self.state[eid] = {"class": None, "done": False}
            if not self.state[eid].get("done"):
                self._observe(ev, "live")

        now = self._clock()
        # Listeden düşen canlı maç: bitişin en erken sinyali → hemen maç sayfası
        dropped = [] if live_events is None else [
            k for k in self.active_ids() if self.state[k].get("class") == StatusClass.LIVE.value and k not in in_list
        ]
        for eid in dropped:
            self._poll_event(eid)

        candidates = [k for k in self.active_ids() if k not in dropped and self._event_poll_due(self.state[k], now)]
        near = [k for k in self.active_ids() if self.state[k].get("near_end")]
        cap = max_event_polls()
        per_minute = len(near) * 60 / EVENT_INTERVAL_SECONDS
        if per_minute > EVENT_PAGES_PER_MINUTE or len(near) > cap:
            if self.event_interval != EVENT_INTERVAL_SLOW_SECONDS or not self._slow_warned:
                logger.warning(
                    f"İzleyici hız bütçesi: {len(near)} maç bitişe yakın; maç sayfası aralığı "
                    f"{EVENT_INTERVAL_SECONDS} → {EVENT_INTERVAL_SLOW_SECONDS} sn"
                )
                self._slow_warned = True
            self.event_interval = EVENT_INTERVAL_SLOW_SECONDS
        else:
            self.event_interval = EVENT_INTERVAL_SECONDS
            self._slow_warned = False
        candidates.sort(key=lambda k: self.state[k].get("last_event_poll") or 0)
        for eid in candidates[:cap]:
            self._poll_event(eid)
        self._save_state()

    def _poll_event(self, eid: str) -> None:
        data = self._get(f"/event/{eid}")
        self.state[eid]["last_event_poll"] = self._clock()
        event = (data or {}).get("event")
        if event:
            self._observe(event, "event")

    def stop(self) -> None:
        self._stop = True

    def run(self, until_seconds: Optional[float] = None) -> None:
        """Ctrl+C / stop() / süre dolana ya da event_ids modunda tüm maçlar bitene kadar."""
        started = self._clock()
        self.start()
        try:
            while not self._stop:
                t0 = self._clock()
                self.tick()
                if self.event_ids and not self.league_ids and not self.active_ids():
                    logger.info("İzlenen tüm maçlar bitti")
                    break
                if until_seconds is not None and self._clock() - started >= until_seconds:
                    break
                self._sleep(max(0.0, LIST_INTERVAL_SECONDS - (self._clock() - t0)))
        finally:
            self._save_state()
