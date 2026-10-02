"""
Yoklama kaynağı (docs/design/02-services.md bölüm 8.2, `poll`): bir spor için canlı listeyi her turda okur ve
gereken maç sayfalarını ister. Gözlem üretir, olay üretmez: her görülen maç izleyicinin `observe`'üne verilir.

Tur, 2.x izleyicisinin turudur (`MatchWatcher.tick`, parametrelerin dayanağı docs/status-matrix/README.md):

  * `events/live` CDN `s-maxage=5`; düdük → finished medyan 20 sn, maks 302 sn → 30 sn aralık.
  * Listeden düşen canlı maç bitişin en erken sinyalidir → hemen /event/{id}.
  * Bitişe yakın maçın sayfası EVENT_INTERVAL_SECONDS'ta bir okunur; takılı maçınki ve başlaması gerekip
    listede görünmeyeninki STUCK_INTERVAL_SECONDS'ta bir.
  * Hız bütçesi: bitişe yakın maç çoksa sayfa aralığı EVENT_INTERVAL_SLOW_SECONDS'a çıkar; turda en çok
    `max_event_polls` sayfa.

Kaynak durumu tutmaz; izleyicinin (`Tracker`) durum sözlüğünü okur ve `last_event_poll` alanını yazar.
İstek işlevi (`get`) hatasında None döndürür; servisin istek işlevi engellenmeyi (`Blocked`) fırlatabilir, bu
tur o zaman yarıda kalır ve gözetici bekler.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol

from src.services.live.reducer import VIA_EVENT, VIA_LIST
from src.status import StatusClass

logger = logging.getLogger(__name__)

LIST_INTERVAL_SECONDS = 30
EVENT_INTERVAL_SECONDS = 30
EVENT_INTERVAL_SLOW_SECONDS = 60  # hız bütçesi aşılınca
STUCK_INTERVAL_SECONDS = 300
EVENT_PAGES_PER_MINUTE = 40  # liste 2/dk + maç sayfaları ≤ 40/dk → < 1 istek/sn
DEFAULT_MAX_EVENT_POLLS = 20

Fetch = Callable[[str], Optional[Dict[str, Any]]]


class Tracker(Protocol):
    """Kaynağın beslediği izleyici: bir sporun durum sözlüğü ve kapsamı."""

    state: Dict[str, Dict[str, Any]]

    def listed(self, event: Mapping[str, Any]) -> bool:
        """Canlı listedeki maç kapsamda mı (maç id'si, turnuva ya da takım)."""

    def in_scope(self, eid: str, s: Mapping[str, Any]) -> bool:
        """Durumda kalan maç hâlâ izleniyor mu."""

    def observe(self, event: Mapping[str, Any], via: str) -> None:
        """Bir gözlem: durum güncellenir, olaylar yazılır."""


def active_ids(tracker: Tracker) -> List[str]:
    """İzlenmeye devam eden maçlar: kapsamda, bitmemiş ve iptal/erteleme ile düşmemiş."""
    return [k for k, s in tracker.state.items() if not s.get("done") and tracker.in_scope(k, s)]


class PollSource:
    """
    sport            izlenen spor (canlı liste yolu `/sport/<spor>/events/live`)
    get              API yolu → yanıt (None: yok ya da başarısız)
    clock            saat (epoch saniye)
    max_event_polls  turda en çok kaç maç sayfası (her turda okunur: ayar değişebilir)
    """

    name = "poll"

    def __init__(self, sport: str, get: Fetch, *, clock: Callable[[], float],
                 max_event_polls: Callable[[], int] = lambda: DEFAULT_MAX_EVENT_POLLS) -> None:
        self.sport = sport
        self._get = get
        self._clock = clock
        self._max_event_polls = max_event_polls
        self.event_interval = EVENT_INTERVAL_SECONDS
        self._slow_warned = False

    def start(self, tracker: Tracker, event_ids: Iterable[int]) -> None:
        """Verilen maçların başlangıç durumu (olay üretmez; durumda zaten olan maç okunmaz)."""
        for eid in sorted(event_ids):
            if str(eid) in tracker.state:
                continue
            data = self._get(f"/event/{eid}")
            event = (data or {}).get("event")
            if event:
                s = tracker.state.setdefault(str(eid), {"class": None, "done": False})
                s["class"] = None
                s["last_event_poll"] = self._clock()
                tracker.observe(event, VIA_EVENT)

    def _event_poll_due(self, s: Mapping[str, Any], now: float) -> bool:
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

    def tick(self, tracker: Tracker) -> None:
        """Bir tur: canlı liste + gereken maç sayfaları."""
        data = self._get(f"/sport/{self.sport}/events/live")
        live_events = (data or {}).get("events") if data is not None else None
        in_list: Dict[str, Mapping[str, Any]] = {}
        for ev in live_events or []:
            if tracker.listed(ev):
                in_list[str(ev.get("id"))] = ev

        for eid, ev in in_list.items():
            if eid not in tracker.state:
                tracker.state[eid] = {"class": None, "done": False}
            if not tracker.state[eid].get("done"):
                tracker.observe(ev, VIA_LIST)

        now = self._clock()
        # Listeden düşen canlı maç: bitişin en erken sinyali → hemen maç sayfası
        dropped = [] if live_events is None else [
            k for k in active_ids(tracker)
            if tracker.state[k].get("class") == StatusClass.LIVE.value and k not in in_list
        ]
        for eid in dropped:
            self.poll_event(tracker, eid)

        active = active_ids(tracker)
        candidates = [k for k in active if k not in dropped and self._event_poll_due(tracker.state[k], now)]
        near = [k for k in active if tracker.state[k].get("near_end")]
        cap = max(1, int(self._max_event_polls()))
        per_minute = len(near) * 60 / EVENT_INTERVAL_SECONDS
        if per_minute > EVENT_PAGES_PER_MINUTE or len(near) > cap:
            if self.event_interval != EVENT_INTERVAL_SLOW_SECONDS or not self._slow_warned:
                logger.warning(
                    "Watcher rate budget (%s): %d events near their end; event page interval %d -> %d s",
                    self.sport, len(near), EVENT_INTERVAL_SECONDS, EVENT_INTERVAL_SLOW_SECONDS,
                )
                self._slow_warned = True
            self.event_interval = EVENT_INTERVAL_SLOW_SECONDS
        else:
            self.event_interval = EVENT_INTERVAL_SECONDS
            self._slow_warned = False
        candidates.sort(key=lambda k: tracker.state[k].get("last_event_poll") or 0)
        for eid in candidates[:cap]:
            self.poll_event(tracker, eid)

    def poll_event(self, tracker: Tracker, eid: str) -> Optional[Mapping[str, Any]]:
        """Maç sayfasını okur ve gözlem olarak verir; maç nesnesini döndürür (yoksa None)."""
        data = self._get(f"/event/{eid}")
        tracker.state[eid]["last_event_poll"] = self._clock()
        event = (data or {}).get("event")
        if event:
            tracker.observe(event, VIA_EVENT)
        return event


__all__ = [
    "EVENT_INTERVAL_SECONDS",
    "EVENT_INTERVAL_SLOW_SECONDS",
    "EVENT_PAGES_PER_MINUTE",
    "LIST_INTERVAL_SECONDS",
    "STUCK_INTERVAL_SECONDS",
    "PollSource",
    "Tracker",
    "active_ids",
]
