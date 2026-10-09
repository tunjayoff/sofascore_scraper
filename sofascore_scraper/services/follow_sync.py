"""
Takım, oyuncu ve maç takiplerinin eşitlemesi (plan maddesi FX-19; sahibin kararı 2026-10-06: bu takipler 3.0.0'da
maçlarını indirir). Eşitleme servisi (sofascore_scraper/services/sync.py) bunu iki adımda çağırır: listeler maç listesi
aşamasında, maçlar detay aşamasında.

Liste (`list_follow`):

  takım   `/team/{id}/events/next/0` (gelecek maçlar, tek sayfa) ve `/team/{id}/events/last/{n}` (oynanmış maçlar,
          n = 0'dan geriye)
  oyuncu  yalnızca `/player/{id}/events/last/{n}`: oyuncunun `next` sayfası uç nokta kataloğunda yok
  maç     liste yok: takibin kendisi maçtır

Uç noktalar docs/all-sports/endpoints.csv'dendir, yanıt biçimi research/all_sports örneklerinden
(tests/fixtures/fx19): `{"events": [...], "hasNextPage": bool}`, maç nesnesi sezon programındakiyle aynı biçimde.
Listenin yanıtı saklanmaz (Store'da takımın ya da oyuncunun program dilimi yok): her eşitlemede yeniden okunur ve
hangi maçların indirileceğini söyler; maçlar maç olarak saklanır. Oyuncu listesinin yalnızca maç kimlikleri
saklanır (aşağıda).

Oyuncu takibinin maçları (B2; bulgu F23, 05-web-ui.md G40). Saklanan bir maç kimin oynadığını söylemez (takımın
maçı ise `event_participants`ten bulunur): bu yüzden oyuncu listesinin maç kimlikleri, yanıtın kendisi değil
yalnızca kimlikler, state.db'nin çalışma zamanı bilgilerine yazılır (`store.runtime`, anahtar
`follow_events:player:<kimlik>`; `remember_listing`). Durum ekranı oyuncunun kapsamını ve maç listesini bundan
sayar (`follow_scope`): son okunan listenin penceresindeki maçlar. Dışa aktarmanın oyuncu süzgeci de önce bunu
okur, sonra saklanan kadroları (sofascore_scraper/services/export.py `participant_events`). Tam okunan liste öncekinin yerine geçer;
yarım kalan (başarısız sayfa, durdurulan iş) öncekiyle birleşir. Yeni sürüme geçişten sonra oyuncunun ilk
eşitlemesine kadar liste yoktur ve maçları sayılmaz.

Pencere (`window_of`): takım ve oyuncu takibinin `seasons` seçimi bir zaman penceresidir (bir takımın birden çok
turnuvası ve sezonu vardır, "güncel sezon" turnuvaya göre değişir):

  "all"       sayfa sınırına kadar (MAX_LAST_PAGES geriye sayfa)
  "current"   başlangıcı son 365 gün içinde olan maçlar
  "last:N"    son N × 365 gün
  kimlikler   yalnızca bu sezonların maçları (sayfa sınırına kadar)

Geriye sayfa okuma şu durumlardan birinde durur: SofaScore başka sayfa yok der (`hasNextPage` false ya da 404),
sayfa sınırına gelindi, ya da sayfanın en eski maçı pencerenin başından eski. Gelecek maçlar (takımın `next`
sayfası) zaman penceresine takılmaz, sezon kimlikleri verildiyse onlara takılır.

İhtiyaç (`follow_need`): planlamanın kuralı (sofascore_scraper/services/planning.py `compute_need`), iki ekle:

  * listede bitmiş görünen ama katalogda bitmemiş (ya da hiç olmayan) maç `full`: takımın listesi katalogda
    olmadığı için maç `stale` işareti almaz;
  * katalogda olmayan ve listede bitmemiş görünen maç yalnızca `/event` ile okunur (tek istek, dilim istenmez):
    maç görünür olur; bitince listede bitmiş görünür ve bir sonraki eşitlemede indirilir.

Maç takibinde liste yoktur: katalogda yoksa `full`; bitmemiş kaydın başlangıcı geçtiyse `full` (yeniden okunur);
öteki durumlarda `compute_need`.

Maçlar getirme boru hattıyla (sofascore_scraper/services/pipeline.py) indirilir, veri seçimi takiplerinkidir (P27
`SelectionPolicy`: en dar takip kazanır; oyuncunun listesinden gelen maç, onu kapsayan daha dar bir takip yoksa
oyuncu takibinin seçimini alır, `SelectionPolicy.with_follow_events`). İstekler işin istek bağlamındadır: ortak
istek bütçesi, işin devre kesicisi ve iptal sorusu.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from sofascore_scraper.client import endpoints
from sofascore_scraper.logger import get_logger
from sofascore_scraper.services import planning
from sofascore_scraper.services.planning import SETTLED_CLASSES, SelectionPolicy, WorkItem
from sofascore_scraper.services.query import DEFAULT_EMPTY_THRESHOLD, NEED_FULL, NEED_REFRESH
from sofascore_scraper.slices import SLICE_EMPTY, SLICE_OK, Outcome
from sofascore_scraper.sports import event_sport_slug
from sofascore_scraper.status import StatusClass, classify_status
from sofascore_scraper.store import Ref, Scope

if TYPE_CHECKING:
    from sofascore_scraper.services.pipeline import ItemResult, PipelineSummary
    from sofascore_scraper.services.query import RefreshPolicy
    from sofascore_scraper.store import EventState, Follow, Store

logger = get_logger("FollowSync")

TEAM = "team"
PLAYER = "player"
EVENT = "event"
LISTED_KINDS: Tuple[str, ...] = (TEAM, PLAYER)
SYNCED_KINDS: Tuple[str, ...] = (TEAM, PLAYER, EVENT)

# Geriye okunan en çok sayfa (SofaScore sayfa başına yaklaşık 30 maç verir; canlı doğrulamada denetlenecek)
MAX_LAST_PAGES = 5
YEAR_S = 365 * 24 * 3600
_LAST_N = re.compile(r"^last:([1-9][0-9]*)$")
_FINISHED = (StatusClass.COMPLETED.value, StatusClass.DECIDED_WITHOUT_PLAY.value)
# Yalnızca `/event` okunan maçın iş biriminin nedeni (`refill`, dilimsiz)
EVENT_ONLY = "event_only"
EVENT_ONLY_REASON = "listed by a follow and not ended: the event only"

Getter = Callable[[str], Outcome]
Seasons = Union[str, Sequence[int]]

# Oyuncu listesinin maç kimliklerinin çalışma zamanı anahtarı (modül belgesi) ve saklanan en çok kimlik
LISTED_PREFIX = "follow_events:"
MAX_REMEMBERED = 2000


@dataclass(frozen=True)
class Window:
    """Takım ve oyuncu takibinin penceresi: en erken başlangıç (epoch), sezonlar, geriye sayfa sınırı."""

    since: Optional[float] = None
    season_ids: Tuple[int, ...] = ()
    pages: int = MAX_LAST_PAGES

    def keeps(self, event: "ListedEvent", *, upcoming: bool = False) -> bool:
        if self.season_ids and event.season_id not in self.season_ids:
            return False
        if upcoming or self.since is None:
            return True
        return event.start_ts is None or event.start_ts >= self.since


def window_of(seasons: Seasons, *, now: float) -> Window:
    """Takibin `seasons` seçimi → pencere (modül belgesindeki tablo); tanınmayan değer "all" sayılır."""
    if not isinstance(seasons, str):
        return Window(season_ids=tuple(int(sid) for sid in seasons))
    if seasons == "current":
        return Window(since=now - YEAR_S)
    found = _LAST_N.match(seasons)
    if found:
        return Window(since=now - int(found.group(1)) * YEAR_S)
    return Window()


@dataclass(frozen=True)
class ListedEvent:
    """Bir takibin listesindeki maç (listenin maç nesnesinden)."""

    id: int
    status_class: str
    start_ts: Optional[int] = None
    season_id: Optional[int] = None
    tournament_id: Optional[int] = None
    sport: Optional[str] = None

    @property
    def finished(self) -> bool:
        return self.status_class in _FINISHED


@dataclass
class FollowListing:
    """Bir takibin listesi: pencereye giren maçlar, istek sayısı, başarısızsa nedeni (istek katmanının nedeni)."""

    follow: "Follow"
    events: List[ListedEvent] = field(default_factory=list)
    requests: int = 0
    failed: Optional[str] = None


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def listed_event(raw: Any) -> Optional[ListedEvent]:
    """Listenin bir maç nesnesi → ListedEvent; kimliği olmayan (ör. kısaltılmış örnek) nesne None."""
    if not isinstance(raw, Mapping):
        return None
    event_id = _int(raw.get("id"))
    if event_id is None or event_id <= 0:
        return None
    tournament = raw.get("tournament") if isinstance(raw.get("tournament"), Mapping) else {}
    unique = tournament.get("uniqueTournament") if isinstance(tournament.get("uniqueTournament"), Mapping) else {}
    season = raw.get("season") if isinstance(raw.get("season"), Mapping) else {}
    return ListedEvent(
        id=event_id, status_class=classify_status(dict(raw)).value, start_ts=_int(raw.get("startTimestamp")),
        season_id=_int(season.get("id")), tournament_id=_int(unique.get("id")),
        sport=event_sport_slug(dict(raw)) or None,
    )


def parse_page(body: Any) -> Tuple[List[ListedEvent], bool]:
    """Bir liste sayfası → (maçlar, başka sayfa var mı). Beklenmeyen biçim ValueError."""
    if not isinstance(body, Mapping) or not isinstance(body.get("events"), list):
        raise ValueError("a follow's match list has no `events` list")
    events = [found for found in (listed_event(raw) for raw in body["events"]) if found is not None]
    return events, bool(body.get("hasNextPage"))


def list_follow(follow: "Follow", get: Getter, *, now: Optional[float] = None,
                cancelled: Optional[Callable[[], bool]] = None) -> FollowListing:
    """
    Takım ya da oyuncu takibinin listesi (modül belgesi). get(yol) → Outcome (istemcinin `get_sync`'i; işin istek
    bağlamında). İptal edilirse o ana kadar okunanlar döner. Bir sayfa alınamazsa okuma durur, `failed` nedeni
    taşır ve önceki sayfaların maçları yine döner.
    """
    if follow.kind not in LISTED_KINDS:
        raise ValueError(f"only team and player follows have a match list, not {follow.kind}")
    window = window_of(follow.seasons, now=time.time() if now is None else now)
    listing = FollowListing(follow)
    seen: Dict[int, None] = {}

    def page(path: str, *, upcoming: bool) -> Optional[Tuple[List[ListedEvent], bool]]:
        listing.requests += 1
        outcome = get(path)
        if outcome.status == SLICE_EMPTY:
            return [], False  # 404 ya da boş gövde: sayfa yok (gelecek maçı olmayan takım, listenin sonu)
        if outcome.status != SLICE_OK:
            listing.failed = str(outcome.reason or outcome.status)
            logger.warning("The match list of follow %s:%s could not be read (%s): %s", follow.kind,
                           follow.entity_id, listing.failed, path)
            return None
        try:
            events, more = parse_page(outcome.data)
        except ValueError:
            listing.failed = "parse"
            logger.warning("The match list of follow %s:%s has an unexpected shape: %s", follow.kind,
                           follow.entity_id, path)
            return None
        for event in events:
            if event.id not in seen and window.keeps(event, upcoming=upcoming):
                seen[event.id] = None
                listing.events.append(event)
        return events, more

    if follow.kind == TEAM:
        if page(endpoints.team_events_page(follow.entity_id, "next", 0), upcoming=True) is None:
            return listing
    for number in range(window.pages):
        if cancelled is not None and cancelled():
            break
        path = (endpoints.team_events_page(follow.entity_id, "last", number) if follow.kind == TEAM
                else endpoints.player_events_page(follow.entity_id, number))
        found = page(path, upcoming=False)
        if found is None:
            break
        events, more = found
        starts = [event.start_ts for event in events if event.start_ts is not None]
        if not more or not events or (window.since is not None and starts and min(starts) < window.since):
            break
    return listing


# --- plan ---------------------------------------------------------------------------------------------------


def follow_need(state: Optional["EventState"], listed: Optional[ListedEvent], selection: Any,
                refresh: "RefreshPolicy", *, threshold: int) -> str:
    """
    Bir takibin maçının ihtiyacı (modül belgesi): `planning.compute_need`, iki ekle. listed: takibin listesindeki
    hali (maç takibinde None). `EVENT_ONLY`: yalnızca `/event`.
    """
    row = state.event if state is not None else None
    if row is None:
        return NEED_FULL if listed is None or listed.finished else EVENT_ONLY
    if listed is not None and listed.finished and (row.status_class not in _FINISHED or not row.has_event_payload):
        return NEED_FULL
    if (listed is None and row.status_class not in SETTLED_CLASSES and row.start_ts is not None
            and row.start_ts <= refresh.now):
        return NEED_FULL  # maç takibi: başlaması gereken maç yeniden okunur
    return planning.compute_need(state, selection, refresh, threshold=threshold)


def _states(store: "Store", ids: Sequence[int]) -> Dict[int, "EventState"]:
    found: Dict[int, "EventState"] = {}
    for start in range(0, len(ids), 500):
        chunk = tuple(ids[start:start + 500])
        found.update((state.event.id, state) for state in store.events.states(Scope(event_ids=chunk)))
    return found


def plan_items(store: "Store", listings: Iterable[FollowListing], event_follows: Iterable["Follow"],
               policy: SelectionPolicy, refresh: "RefreshPolicy", *,
               threshold: int = DEFAULT_EMPTY_THRESHOLD) -> Tuple[List[WorkItem], SelectionPolicy]:
    """
    Takiplerin maçlarının iş birimleri (önce `full` ve `refill`, sonra `refresh`; `none` düşer) ve onları
    çözecek politika (oyuncu takiplerinin maçları `with_follow_events` ile).
    """
    listed: Dict[int, ListedEvent] = {}
    for listing in listings:
        for event in listing.events:
            listed.setdefault(event.id, event)
        if listing.follow.kind == PLAYER:
            policy = policy.with_follow_events(PLAYER, listing.follow.entity_id, (e.id for e in listing.events))
    ids = list(dict.fromkeys([*listed, *(int(follow.entity_id) for follow in event_follows)]))
    states = _states(store, ids)
    first: List[WorkItem] = []
    later: List[WorkItem] = []
    for event_id in ids:
        state, event = states.get(event_id), listed.get(event_id)
        need = follow_need(state, event, policy, refresh, threshold=threshold)
        if need == EVENT_ONLY:
            first.append(WorkItem(Ref.event(event_id), "refill", (), event.sport if event else None,
                                  EVENT_ONLY_REASON))
            continue
        item = planning.work_item(event_id, state, need, policy, threshold=threshold, now=refresh.now,
                                  confirm_after_s=refresh.confirm_after_s)
        if item is not None:
            (later if item.need == NEED_REFRESH else first).append(item)
    return first + later, policy


def listed_key(kind: str, entity_id: int) -> str:
    """Takibin liste kimliklerinin `store.runtime` anahtarı: "follow_events:player:<kimlik>"."""
    return f"{LISTED_PREFIX}{kind}:{int(entity_id)}"


def listed_events(store: "Store", kind: str, entity_id: int) -> Optional[Tuple[int, ...]]:
    """Takibin son listesinin saklanan maç kimlikleri; liste hiç okunmadıysa (ya da okunamıyorsa) None."""
    from sofascore_scraper.store import StoreError

    try:
        fact = store.runtime.get(listed_key(kind, entity_id))
    except StoreError as e:  # durum bilgisidir: okunamaması yalnızca sayımı eksik bırakır
        logger.debug("The stored match list of %s:%s could not be read: %s", kind, entity_id, e)
        return None
    events = fact.value.get("events") if fact is not None else None
    if not isinstance(events, list):
        return None
    return tuple(dict.fromkeys(value for value in events if _int(value) is not None and value > 0))


def remember_listing(store: "Store", listing: FollowListing, *, complete: bool = True) -> None:
    """
    Oyuncu takibinin listesindeki maç kimliklerini saklar (modül belgesi). complete False (liste yarım kaldı:
    başarısız sayfa ya da durdurulan iş): öncekilerle birleşir. Takım ve maç takiplerinde bir şey yapmaz.
    Yazılamazsa uyarır; eşitleme sürer.
    """
    from sofascore_scraper.store import StoreError

    follow = listing.follow
    if follow.kind != PLAYER:
        return
    ids = [event.id for event in listing.events]
    if not complete or listing.failed is not None:
        ids += list(listed_events(store, PLAYER, follow.entity_id) or ())
    ids = list(dict.fromkeys(ids))[:MAX_REMEMBERED]
    try:
        store.runtime.set(listed_key(PLAYER, follow.entity_id), {
            "events": ids, "listed_at": int(time.time()), "complete": bool(complete and listing.failed is None),
        })
    except StoreError as e:
        logger.warning("The match list of follow %s:%s could not be stored (%s); its matches are not counted",
                       follow.kind, follow.entity_id, e)


def follow_scope(store: "Store", kind: str, entity_id: int) -> Optional[Scope]:
    """
    Bir takibin saklanan maçlarının kapsamı (durum ekranının kapsamı ve maç listesi, B2): turnuvanın maçları,
    takımın maçları (`event_participants`), maçın kendisi, oyuncunun son listesinin maçları. Oyuncunun listesi
    yoksa ya da boşsa None (sayılacak maç yok).
    """
    entity_id = int(entity_id)
    if kind == "tournament":
        return Scope(tournament_ids=(entity_id,))
    if kind == TEAM:
        return Scope(participant_ids=(entity_id,))
    if kind == EVENT:
        return Scope(event_ids=(entity_id,))
    if kind == PLAYER:
        ids = listed_events(store, PLAYER, entity_id)
        return Scope(event_ids=ids) if ids else None
    raise ValueError(f"unknown follow kind: {kind!r}")


def run_items(store: "Store", items: Sequence[WorkItem], policy: SelectionPolicy, *, concurrency: int,
              cancelled: Optional[Callable[[], bool]] = None,
              on_result: Optional[Callable[["ItemResult"], None]] = None) -> "PipelineSummary":
    """İş birimlerini getirme boru hattıyla, verilen politikayla yürütür (çağıranın istek bağlamında)."""
    from sofascore_scraper.services.pipeline import FetchPipeline

    return FetchPipeline(store, concurrency=concurrency, selection=policy).run_sync(
        items, cancelled=cancelled, on_result=on_result)


__all__ = [
    "EVENT_ONLY",
    "EVENT_ONLY_REASON",
    "FollowListing",
    "LISTED_KINDS",
    "LISTED_PREFIX",
    "ListedEvent",
    "follow_scope",
    "listed_events",
    "listed_key",
    "remember_listing",
    "MAX_LAST_PAGES",
    "SYNCED_KINDS",
    "Window",
    "follow_need",
    "list_follow",
    "listed_event",
    "parse_page",
    "plan_items",
    "run_items",
    "window_of",
]
