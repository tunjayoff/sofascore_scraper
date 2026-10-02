"""
Eski `main.py --watch` takma adının izleyicisi: tek spor, yoklama, olay üretir, sonuçlandırmaz.

Canlı izlemenin kendisi `src/services/live`'dadır (plan maddesi P23; docs/design/02-services.md bölüm 8):
indirgeyici (`reducer.reduce`) ve yoklama kaynağı (`PollSource`) oradadır ve `ssc watch` aynı parçaları
kullanır. Bu modül yalnızca takma adın 2.x davranışını korur ve bir sürüm sonra kalkar (P30):

  * Olaylar 2.x biçimindedir: her biri stdout'a bir satır (`on_event`) ve data/watch_events.jsonl'a bir satır.
  * Aynı olay `live` akışına da eklenir (`ssc events`, sink'ler); akıştaki biçim canlı servisinkidir
    (`reducer.stream_event`: `data` ve yinelenme anahtarı).
  * Son bilinen durum `store.watch`tadır (state.db), izleyici adı = spor. 2.x'in data/watch_state_{sport}.json
    dosyası ilk çalıştırmada bir kez içe alınır ve artık yazılmaz. Canlı servis aynı izleyici adını kullanır:
    ikisi aynı anda çalışamaz (`live` ve `watcher:<spor>` kilitleri birbirini dışlar), biri bıraktığı yerden
    öbürü sürer.
  * Depo meşgulse (StoreBusy) olay eklemesi artan aralıklarla yeniden denenir; izleme bitmez.

Diske yalnızca Store üzerinden dokunulur (docs/design/01-storage.md, bölüm 2.3).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from src import throttle
from src.services.live import poll_source, reducer
from src.services.live.poll_source import (
    EVENT_INTERVAL_SECONDS,
    EVENT_INTERVAL_SLOW_SECONDS,
    EVENT_PAGES_PER_MINUTE,
    LIST_INTERVAL_SECONDS,
    STUCK_INTERVAL_SECONDS,
    PollSource,
)
from src.services.live.reducer import NEAR_END_RULES as _NEAR_END_RULES
from src.services.live.reducer import near_end, play_start
from src.services.live.supervisor import append_retrying
from src.sports import DEFAULT_STUCK_AFTER_SECONDS, watcher_params
from src.store import open_store

logger = logging.getLogger(__name__)

# Takılı maç eşiği spora göre değişir: değerler ve gerekçeleri src/sports.py'de (WatcherParams).
# Bu iki ad eski import'lar için duruyor; izleyici eşiği watcher_params(sport) ile okur.
STUCK_AFTER_SECONDS = DEFAULT_STUCK_AFTER_SECONDS
STUCK_AFTER_SECONDS_TENNIS = watcher_params("tennis").stuck_after_seconds
MIN_REQUEST_SPACING_SECONDS = 1.0
# İzleyicilerin ortak şeridi (src/throttle.py): aynı anda çalışan tüm --watch süreçleri bu aralığı paylaşır
THROTTLE_LANE = "watch"
WATCH_EVENTS_FILE = "watch_events.jsonl"
WATCH_STATE_FILE = "watch_state_{sport}.json"
# Olayların eklendiği akış; tür adı akış adıyla öneklenir ("status_changed" → "live.status_changed").
# İzleyici yoklamayla çalışır: zarfın kaynağı hep "poll"dur.
LIVE_STREAM = reducer.LIVE_STREAM
STREAM_SOURCE = "poll"


def max_event_polls() -> int:
    """WATCH_MAX_EVENT_POLLS: aynı turda en fazla kaç maç sayfası (varsayılan 20)."""
    try:
        return max(1, int(os.getenv("WATCH_MAX_EVENT_POLLS", "20")))
    except ValueError:
        return 20


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
        self._stop = False
        self._source = PollSource(sport, self._get, clock=clock, max_event_polls=max_event_polls)
        # 2.x dosyasının yolu (gösterim için; dosyaya Store yazar). Durum dosyası artık yazılmaz.
        self.events_path = os.path.join(data_dir, WATCH_EVENTS_FILE)
        self.state_path = os.path.join(data_dir, WATCH_STATE_FILE.format(sport=sport))
        self._store = open_store(data_dir)
        self._saved: Dict[str, str] = {}  # son kayıttaki durum (maç → JSON): yalnızca değişen satırlar yazılır
        self.state: Dict[str, Dict[str, Any]] = self._load_state()

    @property
    def event_interval(self) -> int:
        """Bitişe yakın maçların sayfa aralığı (hız bütçesi aşılınca yavaşlar)."""
        return self._source.event_interval

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
            logger.warning("Watcher request failed: %s: %s", path, e)
            return None

    # --- durum ------------------------------------------------------------------------

    def _load_state(self) -> Dict[str, Dict[str, Any]]:
        """Durumu Store'dan okur; 2.x durum dosyası varsa önce (bir kez) içe alınır."""
        self._store.watch.import_legacy(self.sport)
        state = self._store.watch.load(self.sport)
        self._saved = self._snapshot(state)
        return state

    @staticmethod
    def _snapshot(state: Mapping[str, Mapping[str, Any]]) -> Dict[str, str]:
        return {eid: json.dumps(s, ensure_ascii=False, sort_keys=True) for eid, s in state.items()}

    def _save_state(self) -> None:
        snapshot = self._snapshot(self.state)
        changed = [eid for eid, text in snapshot.items() if self._saved.get(eid) != text]
        changed += [eid for eid in self._saved if eid not in snapshot]
        self._store.watch.save(self.sport, self.state, changed=changed)
        self._saved = snapshot

    def _emit(self, item: Dict[str, Any], event: Mapping[str, Any]) -> None:
        """2.x olayı: `live` akışına (yinelenme anahtarıyla), watch_events.jsonl'a ve geri çağrıya."""
        eid = item["event_id"]
        tournament_id = (self.state.get(str(eid)) or {}).get("tournament_id")
        stream_event = reducer.stream_event(item, event, self.sport, tournament_id=tournament_id,
                                            source=STREAM_SOURCE)
        append_retrying(self._store, LIVE_STREAM, [stream_event], sleep=self._sleep)
        self._store.watch.append_legacy_events([json.dumps(item, ensure_ascii=False)])
        if self.on_event:
            self.on_event(item)

    # --- izleyici (PollSource'un beslediği) -------------------------------------------

    def listed(self, event: Mapping[str, Any]) -> bool:
        ut = reducer.tournament_of(event)
        return int(str(event.get("id"))) in self.event_ids or (ut is not None and ut in self.league_ids)

    def in_scope(self, eid: str, s: Mapping[str, Any]) -> bool:
        """State dosyasında önceki koşulardan kalan başka maçlar izlenmez."""
        return int(eid) in self.event_ids or s.get("tournament_id") in self.league_ids

    def observe(self, event: Mapping[str, Any], via: str) -> None:
        self._observe(event, via)

    def active_ids(self) -> List[str]:
        """İzlenmeye devam eden maçlar: kapsamda, bitmemiş ve iptal/erteleme ile düşmemiş."""
        return poll_source.active_ids(self)

    def _observe(self, event: Mapping[str, Any], source: str) -> None:
        """Bir maçın yeni görüntüsü: sınıf değişimi, canlı skor, takılı maç (src/services/live/reducer.py)."""
        eid = str(event.get("id"))
        obs = reducer.Observation(event=event, via=source, at=self._clock())
        self.state[eid], emitted = reducer.reduce(self.state.get(eid), obs, self.sport)
        for item in emitted:
            self._emit(item, event)

    # --- tur --------------------------------------------------------------------------

    def start(self) -> None:
        """event_ids modunda her maçın başlangıç durumu (olay üretmez; state'te yoksa)."""
        self._source.start(self, self.event_ids)
        self._save_state()

    def tick(self) -> None:
        """Bir tur: canlı liste + gereken maç sayfaları."""
        self._source.tick(self)
        self._save_state()

    def _poll_event(self, eid: str) -> None:
        self._source.poll_event(self, eid)

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
                    logger.info("All watched events have finished")
                    break
                if until_seconds is not None and self._clock() - started >= until_seconds:
                    break
                self._sleep(max(0.0, LIST_INTERVAL_SECONDS - (self._clock() - t0)))
        finally:
            self._save_state()


__all__ = [
    "EVENT_INTERVAL_SECONDS",
    "EVENT_INTERVAL_SLOW_SECONDS",
    "EVENT_PAGES_PER_MINUTE",
    "LIST_INTERVAL_SECONDS",
    "LIVE_STREAM",
    "MIN_REQUEST_SPACING_SECONDS",
    "STREAM_SOURCE",
    "STUCK_AFTER_SECONDS",
    "STUCK_AFTER_SECONDS_TENNIS",
    "STUCK_INTERVAL_SECONDS",
    "THROTTLE_LANE",
    "WATCH_EVENTS_FILE",
    "WATCH_STATE_FILE",
    "MatchWatcher",
    "_NEAR_END_RULES",
    "max_event_polls",
    "near_end",
    "play_start",
]
