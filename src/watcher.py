"""
Canlı izleyici: olay üretir, sonuçlandırmaz.

Parametrelerin dayanağı docs/status-matrix/README.md:
  - `events/live` CDN `s-maxage=5`; düdük → finished medyan 20 sn, maks 302 sn → 30 sn aralık.
  - Listeden düşmek bitişin en erken sinyali → hemen /event/{id}.
  - Askıya alınan maç aynı id ile ertesi güne kayabiliyor (B9/T6, B13) → takılı maç eşiği başlangıç + 4 sa.
Kesinlik 03-A pencere kuralındadır (src/refresh.py); izleyici yalnızca ilk COMPLETED'i `provisional` olarak bildirir.

Diske yalnızca Store üzerinden dokunulur (docs/design/01-storage.md, bölüm 2.3):
  - Son bilinen durum `store.watch`tadır (state.db), izleyici adı = spor: her spor için ayrı süreç birbirini
    ezmez ve yeniden başlatmada aynı geçiş iki kez olay yapılmaz. 2.x'in data/watch_state_{sport}.json
    dosyası ilk çalıştırmada bir kez içe alınır.
  - Her olay sıra numarasıyla `live` akışına eklenir (`store.streams`) ve isteğe bağlı `on_event` ile verilir.
  - Canlı servis izleyicinin yerini alana kadar (plan maddesi P23) 2.x dosyaları da yazılmaya devam eder:
    olay satırları data/watch_events.jsonl'a eklenir, durumun bir kopyası data/watch_state_{sport}.json'a yazılır.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

from src import throttle
from src.refresh import refresh_window_hours
from src.sports import DEFAULT_STUCK_AFTER_SECONDS, watcher_params
from src.status import StatusClass, classify_status, extract_scores
from src.store import StreamEvent, open_store

logger = logging.getLogger(__name__)

LIST_INTERVAL_SECONDS = 30
EVENT_INTERVAL_SECONDS = 30
EVENT_INTERVAL_SLOW_SECONDS = 60  # hız bütçesi aşılınca
STUCK_INTERVAL_SECONDS = 300
# Takılı maç eşiği spora göre değişir: değerler ve gerekçeleri src/sports.py'de (WatcherParams).
# Bu iki ad eski import'lar için duruyor; izleyici eşiği watcher_params(sport) ile okur.
STUCK_AFTER_SECONDS = DEFAULT_STUCK_AFTER_SECONDS
STUCK_AFTER_SECONDS_TENNIS = watcher_params("tennis").stuck_after_seconds
EVENT_PAGES_PER_MINUTE = 40  # liste 2/dk + maç sayfaları ≤ 40/dk → < 1 istek/sn
MIN_REQUEST_SPACING_SECONDS = 1.0
# İzleyicilerin ortak şeridi (src/throttle.py): aynı anda çalışan tüm --watch süreçleri bu aralığı paylaşır
THROTTLE_LANE = "watch"
WATCH_EVENTS_FILE = "watch_events.jsonl"
WATCH_STATE_FILE = "watch_state_{sport}.json"
# Olayların eklendiği akış; tür adı akış adıyla öneklenir ("status_changed" → "live.status_changed").
# İzleyici yoklamayla çalışır: zarfın kaynağı hep "poll"dur (hangi isteğin gördüğü olayın kendi `source` alanında).
LIVE_STREAM = "live"
STREAM_SOURCE = "poll"

_TERMINAL = (StatusClass.COMPLETED, StatusClass.DECIDED_WITHOUT_PLAY)


def max_event_polls() -> int:
    """WATCH_MAX_EVENT_POLLS: aynı turda en fazla kaç maç sayfası (varsayılan 20)."""
    try:
        return max(1, int(os.getenv("WATCH_MAX_EVENT_POLLS", "20")))
    except ValueError:
        return 20


def _utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


def _near_end_football_minute(event: Dict[str, Any], now: float) -> bool:
    """2. yarı ≥ 80. dk ya da injuryTime2 görüldü; uzatma/penaltı kodları."""
    code = (event.get("status") or {}).get("code")
    t = event.get("time") or {}
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


def _near_end_played_ratio(event: Dict[str, Any], now: float) -> bool:
    """Son periyot (4. çeyrek ya da 2. yarı, uzatma) ve played ≥ %90 (played yoksa yalnız periyot)."""
    code = (event.get("status") or {}).get("code")
    t = event.get("time") or {}
    regulation = (t.get("periodLength") or 0) * (t.get("totalPeriodCount") or 0)
    played = t.get("played")
    if played is not None and regulation:
        # %90 ancak son periyotta (4. çeyrek / 2. yarı) ya da uzatmada aşılır
        return played >= 0.9 * regulation
    # Saat verisi yok (K7: çoğu alt lig): yalnızca periyot kodu; 16, uzatma ve bilinmeyen canlı kodlar
    return code not in (13, 14, 15, 30, 31)


def _near_end_last_set(event: Dict[str, Any], now: float) -> bool:
    """Son set (defaultPeriodCount, yoksa 3)."""
    code = (event.get("status") or {}).get("code")
    sets = event.get("defaultPeriodCount") or 3
    return isinstance(code, int) and 8 <= code <= 12 and code - 7 >= sets


# Kural adı → kural; hangi sporun hangisini kullandığı src/sports.py'de (WatcherParams.near_end_rule).
# Tabloda olmayan ad ("never") ve kayıtlı olmayan spor: bitişe yakın sayılmaz.
_NEAR_END_RULES: Dict[str, Callable[[Dict[str, Any], float], bool]] = {
    "football_minute": _near_end_football_minute,
    "played_ratio": _near_end_played_ratio,
    "last_set": _near_end_last_set,
}


def near_end(event: Dict[str, Any], sport: str, now: float) -> bool:
    """
    Bitişe yakın mı (maç sayfası da izlenir). Kuralı sporun kayıt defteri girdisi seçer:
      futbol: 2. yarı ≥ 80. dk ya da injuryTime2 görüldü; uzatma/penaltı kodları
      basketbol: son periyot (4. çeyrek ya da 2. yarı, uzatma) ve played ≥ %90 (played yoksa yalnız periyot)
      tenis: son set (defaultPeriodCount, yoksa 3)
    """
    rule = _NEAR_END_RULES.get(watcher_params(sport).near_end_rule)
    return rule(event, now) if rule else False


def play_start(event: Dict[str, Any]) -> Optional[float]:
    """
    Gerçek oyun başlangıcı (takılı maç süresi buradan sayılan sporlarda; bugün yalnızca tenis)
    = currentPeriodStartTimestamp − biten setlerin süreleri (time.periodN, sn);
    set süreleri yoksa currentPeriodStartTimestamp. Zaman bilgisi yoksa None (çağıran startTimestamp'e düşer).
    time.periodN oyun süresidir, yağmur arası gibi duraklamalar dahil değildir; bu yüzden hesaplanan başlangıç
    gerçek olandan geç çıkabilir ve stuck olayı biraz geç tetiklenir (zararsız yönde hata).
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
        # İstek aralığı izleyicinin özel kuralı değil, ortak bütçenin "watch" şeridi: spor başına ayrı
        # süreçler toplamda da ≥ 1 sn aralıkla istek atar (issue #16). Kendi fetch'i verilen izleyici
        # (testler, gömülü kullanım) gerçek istek atmadığı için şeridi süreç içinde tutar.
        self._throttle = throttle.lane(
            THROTTLE_LANE, MIN_REQUEST_SPACING_SECONDS, shared=fetch_json is None, clock=clock, sleep=sleep
        )
        self.requests = 0
        self.event_interval = EVENT_INTERVAL_SECONDS
        self._slow_warned = False
        self._stop = False
        # 2.x dosyalarının yolları (gösterim için; dosyalara Store yazar)
        self.events_path = os.path.join(data_dir, WATCH_EVENTS_FILE)
        self.state_path = os.path.join(data_dir, WATCH_STATE_FILE.format(sport=sport))
        self._store = open_store(data_dir)
        self._saved: Dict[str, str] = {}  # son kayıttaki durum (maç → JSON): yalnızca değişen satırlar yazılır
        self.state: Dict[str, Dict[str, Any]] = self._load_state()

    # --- ağ ---------------------------------------------------------------------------

    @staticmethod
    def _default_fetch(path: str) -> Optional[Dict[str, Any]]:
        from src.client import api_url
        from src.exceptions import ResourceNotFoundError
        from src.utils import make_api_request

        try:
            return make_api_request(api_url(path))
        except ResourceNotFoundError:
            return None

    def _get(self, path: str) -> Optional[Dict[str, Any]]:
        self._throttle.wait()
        self.requests += 1
        try:
            return self._fetch(path)
        except Exception as e:  # tek istek hatası izlemeyi durdurmasın
            logger.warning(f"İzleyici isteği başarısız: {path}: {e}")
            return None

    # --- durum ------------------------------------------------------------------------

    def _load_state(self) -> Dict[str, Dict[str, Any]]:
        """Durumu Store'dan okur; 2.x durum dosyası varsa önce (bir kez) içe alınır."""
        self._store.watch.import_legacy(self.sport)
        state = self._store.watch.load(self.sport)
        self._saved = self._snapshot(state)
        return state

    @staticmethod
    def _snapshot(state: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
        return {eid: json.dumps(s, ensure_ascii=False, sort_keys=True) for eid, s in state.items()}

    def _save_state(self) -> None:
        snapshot = self._snapshot(self.state)
        changed = [eid for eid, text in snapshot.items() if self._saved.get(eid) != text]
        changed += [eid for eid in self._saved if eid not in snapshot]
        self._store.watch.save(self.sport, self.state, changed=changed)
        self._saved = snapshot
        self._store.watch.mirror_legacy_state(self.sport, self.state)  # P23'e kadar: 2.x dosyası da güncel kalır

    def _emit(self, event: Dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False)
        stored = json.loads(line)  # dosyadakiyle aynı biçim (Pair → liste, enum → metin)
        eid = stored["event_id"]
        tournament_id = (self.state.get(str(eid)) or {}).get("tournament_id")
        # Akış satırı: tür ve maç zarfın sütunlarıdır, olayın kalan alanları veridir. Yinelenme anahtarı
        # verilmez: aynı geçişi iki kez üretmemek izleyicinin durumunun işidir.
        self._store.streams.append(LIVE_STREAM, [StreamEvent(
            type=f"{LIVE_STREAM}.{stored['type']}",
            data={k: v for k, v in stored.items() if k not in ("type", "event_id")},
            event_id=eid,
            sport=self.sport,
            tournament_id=tournament_id if type(tournament_id) is int else None,
            source=STREAM_SOURCE,
        )])
        self._store.watch.append_legacy_events([line])  # P23'e kadar: watch_events.jsonl da yazılır
        if self.on_event:
            self.on_event(stored)

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

        params = watcher_params(self.sport)
        if params.stuck_from_play_start:
            s["play_start"] = play_start(event) or s.get("play_start")
            start = s.get("play_start") or s.get("start_ts")
        else:
            start = s.get("start_ts")
        limit = params.stuck_after_seconds
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
