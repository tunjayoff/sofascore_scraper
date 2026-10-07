"""
Canlı servis (docs/design/02-services.md bölüm 8.1): `ssc watch`'ın çalıştırdığı, gözetimli tek süreç.

    kapsam (takipler, live=true) ─→ spor başına kaynak: PageSource (`page`, varsayılan) + PollSource (hep) ─→ gözlem
        ─→ indirgeyici (reducer.reduce) ─→ store.streams.append("live") ─→ sink'ler, `ssc events`
        ─→ bitiş: tek /event isteği ─→ store.events.observe ─→ change.recorded

  * **Tek kopya.** Servis `live` kilidini tutar (01-storage.md 6.1): aynı veri dizininde ikinci bir servis
    ya da 2.x izleyicisi (`watcher:<spor>`) başlamaz (LeaseHeld; CLI'de çıkış kodu 6). `writer` almaz: indirme
    işleri ile birlikte çalışır.
  * **Kendi istek bağlamı.** İstekler bir işin devre kesicisine sayılmaz (`request_context(breaker=None)`) ve
    ortak bütçenin `watch` şeridinden sıra alır (istekler arası ≥ 1 sn).
  * **Durum** `store.watch`tadır, izleyici adı spor adıdır (2.x izleyicisiyle aynı): yeniden başlatma aynı
    geçişi yeniden olay yapmaz ve `--watch` ile `ssc watch` birbirinin bıraktığı yerden sürer (kilitler
    ikisinin aynı anda çalışmasını engeller). 2.x'in `watch_state_<spor>.json` dosyası bir kez içe alınır.
  * **Olaylar** `live` akışına yinelenme anahtarıyla eklenir (reducer.stream_event): çöken servisin yeniden
    başlarken ürettiği aynı geçiş ikinci kez saklanmaz. Depo meşgulse (StoreBusy: başka bir yazar 5 sn'lik
    bekleme süresini aştı) ekleme artan aralıklarla yeniden denenir; servis bunun yüzünden bitmez.
  * **Bitiş onayı.** Sonuçlanan maçın (completed, decided_without_play) yükü bir `/event/{id}` isteğiyle
    (gözlem zaten maç sayfasından geldiyse istek yapılmaz) `store.events.observe` ile saklanır; değişiklik
    günlüğüne satır yazıldıysa `change.recorded` eklenir.
  * **Gözetim.** Çöken kaynak artan aralıklarla (5 sn → 5 dk) yeniden kurulur. SofaScore istekleri
    engellerse (429/403, açık devre kesici) servis durmaz: `system.blocked` yazar, artan aralıklarla
    (1 → 10 dk) bekler ve ilk başarılı turda `system.recovered` yazar.
  * **Bakım.** `live` kilidini tutarken olay günlüğünü saatte bir budar (sink dağıtıcısıyla aynı sınırlar:
    7 gün, 1.000.000 satır); dağıtıcı yalnızca `sinks` kilidini tutarken budar.
  * **Durum bilgisi.** Her turda `store.runtime`'a ("live") kaynağı, sporları, sayaçları ve kalp atışını yazar;
    `live_status(store)` bunu kilidin sahibiyle birlikte okur (`ssc status`, `/api/v1/status` için).

  * **Kaynaklar ve hakem (P24).** `page` seçiliyse her spor için bir sayfa açılır (push_source.PageSource) ve
    kareleri saniyede bir okunur; her kare o maçın son bilinen nesnesine (push_source.LastKnown, yoklamanın
    gördükleriyle tohumlanır) yazılıp gözlem olarak indirgeyiciye verilir. Yoklama hep vardır: hakem
    (arbiter.SportArbiter) push sağlıklıyken onu yavaş güvenlik aralığına çeker, push sessiz ya da kopuksa
    `poll_interval`'a döndürür, her (yeniden) bağlanmadan sonra bir tur yaptırır ve her değişiklikte
    `system.live_source_changed` yazılır. Sayfa açılamaz ya da çökerse servis yoklamayla sürer ve sayfa artan
    aralıklarla yeniden açılır; `direct`'e asla düşülmez. Yoklamanın getirdiği, push'tan gelen son nesneden
    eski bir nesne (CDN önbelleği) indirgeyiciye verilmez: eski gözlem yeniyi ezmez.
    Push'un gösterdiği ama yoklamanın hiç listelemediği bir maç (ölçüm: biten maçların bir kısmı) kapsamdaysa
    durumu karesinden ve tek bir /event isteğinden kurulur (dakikada en çok UNKNOWN_LOOKUPS_PER_MINUTE).

  * **`direct` (P31, açık seçim).** Yalnızca istenen kaynak kelimesi kelimesine `direct` ise servis tek bir push
    bağlantısı kurar (direct_source.DirectConnection; kimlik bilgisi köprü sayfasının kendi bağlantısından,
    yalnızca bellekte) ve her sporu `sport.{spor}` konusuyla ona bağlar (direct_source.DirectSource). Kareler
    `page` kaynağıyla aynı yoldan geçer (LastKnown, hakem, indirgeyici). Hiçbir yedek, hata yolu ya da varsayılan
    `direct`'i seçmez; `direct` de çalışmazsa servis yoklamayla sürer. Her başlangıçta dört uyarı log'a yazılır.

Saat ve bekleme dışarıdan verilir: testler sahte saatle gerçek zaman beklemeden sınar.
"""
from __future__ import annotations

import collections
import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypeVar,
)

from sofascore_scraper.services.live import reducer
from sofascore_scraper.services.live.arbiter import SportArbiter
from sofascore_scraper.services.live.poll_source import LIST_INTERVAL_SECONDS, PollSource, active_ids
from sofascore_scraper.services.live.push_source import (
    SIGNAL_CLOSE,
    SIGNAL_ERROR,
    SIGNAL_FRAME,
    SIGNAL_GAP,
    SIGNAL_GONE,
    SIGNAL_OPEN,
    SIGNAL_PING,
    SOURCE_PAGE,
    VIA_PUSH,
    LastKnown,
    PageOpener,
    PageSource,
    Signal,
)
from sofascore_scraper.status import StatusClass, classify_status

logger = logging.getLogger(__name__)

LIVE_LEASE = "live"
LEASE_PURPOSE = "watch"
RUNTIME_KEY = "live"
WATCH_THROTTLE_LANE = "watch"  # sofascore_scraper/watcher.py ile aynı şerit: izleyiciler toplamda ≥ 1 sn aralıkla istek atar
MIN_REQUEST_SPACING_SECONDS = 1.0
DEFAULT_SPORT = "football"  # sporu belirtilmemiş takip (yapılandırmanın varsayılanı)

SOURCE_POLL = "poll"
SOURCE_DIRECT = "direct"
SOURCES: Tuple[str, ...] = (SOURCE_PAGE, SOURCE_DIRECT, SOURCE_POLL)
AVAILABLE_SOURCES: Tuple[str, ...] = (SOURCE_PAGE, SOURCE_DIRECT, SOURCE_POLL)
PUSH_SOURCES: Tuple[str, ...] = (SOURCE_PAGE, SOURCE_DIRECT)

BUSY_RETRY_FIRST_SECONDS = 0.5
BUSY_RETRY_MAX_SECONDS = 30.0
BUSY_ATTEMPTS_WHILE_STOPPING = 3
SOURCE_RESTART_FIRST_SECONDS = 5.0
SOURCE_RESTART_MAX_SECONDS = 300.0
BLOCKED_FIRST_SECONDS = 60.0
BLOCKED_MAX_SECONDS = 600.0
SCOPE_RELOAD_SECONDS = 60.0
PRUNE_INTERVAL_SECONDS = 3600.0
PUSH_DRAIN_SECONDS = 1.0  # push kareleri bu aralıkla okunur (gecikmenin üst sınırına eklenir)
HEARTBEAT_SECONDS = 30.0  # push varken kalp atışı en çok bu aralıkla yazılır (yoklama turunda her zaman)
UNKNOWN_LOOKUPS_PER_MINUTE = 6  # yoklamanın hiç görmediği maç için /event isteği, spor başına
# Takip edilen tek maç: kaydı başlamamış ve başlangıcı bundan daha uzaktaysa izleme başında okunmaz (FX-23, F35).
# Başladığında canlı listede görünür ve oradan izlenir; aylar sonraki maç her başlangıçta bir istek yakmasın.
FOLLOW_START_WINDOW_SECONDS = 6 * 3600.0
CONFIRM_RETRY_SECONDS = 20.0  # push bitişinin onayı: maç sayfası henüz bitmiş göstermiyorsa yeniden
CONFIRM_ATTEMPTS = 4

SYSTEM_BLOCKED = "system.blocked"
SYSTEM_RECOVERED = "system.recovered"
SYSTEM_SOURCE_CHANGED = "system.live_source_changed"
CHANGE_RECORDED = "change.recorded"

T = TypeVar("T")
Fetch = Callable[[str], Optional[Dict[str, Any]]]


class Blocked(Exception):
    """SofaScore istekleri engelliyor (429, 403, açık devre kesici): tur yarıda kalır, servis bekler."""


class _Stop:
    """`threading.Event` benzeri, hiç kurulmayan durdurma belirteci (yalnızca `sleep` verilen çağıranlar için)."""

    def __init__(self, sleep: Callable[[float], None]) -> None:
        self._sleep = sleep

    def is_set(self) -> bool:
        return False

    def wait(self, timeout: Optional[float] = None) -> bool:
        self._sleep(max(0.0, float(timeout or 0.0)))
        return False


# --- meşgul depo -----------------------------------------------------------------------------------


def retrying(fn: Callable[[], T], *, what: str, stop: Any = None, sleep: Callable[[float], None] = time.sleep) -> T:
    """
    `fn()`'i StoreBusy verdikçe artan aralıklarla (0,5 sn → 30 sn) yeniden dener. Başka hatalar olduğu gibi
    çıkar. Durdurma istendiyse (`stop.is_set()`) en çok BUSY_ATTEMPTS_WHILE_STOPPING deneme daha yapılır ve
    son StoreBusy fırlatılır: kapanış sonsuza dek beklemez.
    """
    from sofascore_scraper.store import StoreBusy

    waiter = stop if stop is not None else _Stop(sleep)
    delay = BUSY_RETRY_FIRST_SECONDS
    attempts_left = BUSY_ATTEMPTS_WHILE_STOPPING
    warned = False
    while True:
        try:
            result = fn()
        except StoreBusy:
            if waiter.is_set():
                attempts_left -= 1
                if attempts_left <= 0:
                    raise
            if not warned:
                logger.warning("The store is busy (another process is writing); retrying %s", what)
                warned = True
            waiter.wait(delay)
            delay = min(BUSY_RETRY_MAX_SECONDS, delay * 2)
            continue
        if warned:
            logger.info("The store accepted %s again", what)
        return result


def append_retrying(store: Any, stream: str, events: Sequence[Any], *, stop: Any = None,
                    sleep: Callable[[float], None] = time.sleep) -> List[Optional[int]]:
    """`store.streams.append`, StoreBusy'de yeniden denenerek (`retrying`)."""
    return retrying(lambda: store.streams.append(stream, events), what=f"an append to the {stream} stream",
                    stop=stop, sleep=sleep)


# --- kapsam ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SportScope:
    """Bir sporda izlenenler: maç id'leri, turnuvalar (unique tournament) ve takımlar."""

    sport: str
    event_ids: FrozenSet[int] = frozenset()
    tournament_ids: FrozenSet[int] = frozenset()
    team_ids: FrozenSet[int] = frozenset()

    @property
    def only_events(self) -> bool:
        """Yalnızca maç id'leri: hepsi bitince izleme kendiliğinden biter."""
        return bool(self.event_ids) and not self.tournament_ids and not self.team_ids

    def listed(self, event: Mapping[str, Any]) -> bool:
        try:
            eid = int(str(event.get("id")))
        except ValueError:
            return False
        if eid in self.event_ids:
            return True
        ut = reducer.tournament_of(event)
        if ut is not None and ut in self.tournament_ids:
            return True
        teams = {(event.get(side) or {}).get("id") for side in ("homeTeam", "awayTeam")}
        return bool(self.team_ids & teams)


@dataclass(frozen=True)
class LiveScope:
    """Servisin kapsamı: spor başına bir SportScope. `from_follows`: takiplerden okundu (yeniden okunur)."""

    sports: Tuple[SportScope, ...] = ()
    from_follows: bool = False
    skipped: Tuple[str, ...] = ()  # izlenemeyen takipler ("player:123"): kullanıcıya söylenir

    @property
    def empty(self) -> bool:
        return not any(s.event_ids or s.tournament_ids or s.team_ids for s in self.sports)

    def sport(self, name: str) -> Optional[SportScope]:
        return next((s for s in self.sports if s.sport == name), None)


def explicit_scope(sports: Iterable[str], *, event_ids: Iterable[int] = (),
                   tournament_ids: Iterable[int] = ()) -> LiveScope:
    """Komut satırından verilen kapsam: her spora aynı maç ve turnuva id'leri."""
    events, tournaments = frozenset(int(e) for e in event_ids), frozenset(int(t) for t in tournament_ids)
    return LiveScope(sports=tuple(SportScope(sport, events, tournaments) for sport in dict.fromkeys(sports)))


def scope_from_follows(follows: Iterable[Any]) -> LiveScope:
    """
    `live=true` ve etkin takiplerden kapsam: turnuva → o sporun turnuvası, maç → maç id'si, takım → canlı
    listede o takımın maçları. Oyuncu takibi canlı listeden izlenemez: atlanır ve `skipped`'te söylenir.
    Sporu belirtilmemiş takip futbol sayılır (yapılandırmanın varsayılanı).
    """
    by_sport: Dict[str, Dict[str, Set[int]]] = {}
    skipped: List[str] = []
    for follow in follows:
        if not getattr(follow, "live", False) or not getattr(follow, "enabled", True):
            continue
        sport = (getattr(follow, "sport", None) or DEFAULT_SPORT).lower()
        kind = getattr(follow, "kind", "")
        bucket = by_sport.setdefault(sport, {"event": set(), "tournament": set(), "team": set()})
        if kind in bucket:
            bucket[kind].add(int(follow.entity_id))
        else:
            skipped.append(f"{kind}:{follow.entity_id}")
    sports = tuple(
        SportScope(sport, frozenset(b["event"]), frozenset(b["tournament"]), frozenset(b["team"]))
        for sport, b in sorted(by_sport.items()) if b["event"] or b["tournament"] or b["team"]
    )
    return LiveScope(sports=sports, from_follows=True, skipped=tuple(skipped))


# --- spor başına izleyici --------------------------------------------------------------------------


class _Tracker:
    """Bir sporun durumu ve kapsamı; kaynağın beslediği `Tracker` (poll_source)."""

    def __init__(self, service: "LiveService", scope: SportScope, state: Dict[str, Dict[str, Any]]) -> None:
        self.service = service
        self.scope = scope
        self.state = state
        self.matched: Set[str] = set()  # bu çalışmada kapsama girdiği canlı listede görülen maçlar (takım takibi)
        self.saved: Dict[str, str] = reducer_snapshot(state)
        # Push varken: maç başına son bilinen nesne (kapsam dışındakiler de: kareleri kapsama girebilir),
        # kapsam dışı olduğu /event ile anlaşılan maçlar ve o isteklerin zamanları
        self.known = LastKnown()
        self.outside: Set[int] = set()
        self.lookups: Deque[float] = collections.deque()

    @property
    def sport(self) -> str:
        return self.scope.sport

    def listed(self, event: Mapping[str, Any]) -> bool:
        if self.service._push:
            self.known.seed(event)  # canlı listenin her maçı: push karesi kapsamdaki bir maçı gösterebilir
        if self.scope.listed(event):
            self.matched.add(str(event.get("id")))
            return True
        return False

    def in_scope(self, eid: str, s: Mapping[str, Any]) -> bool:
        return (int(eid) in self.scope.event_ids or s.get("tournament_id") in self.scope.tournament_ids
                or eid in self.matched)

    def observe(self, event: Mapping[str, Any], via: str) -> None:
        self.service._observe(self, event, via)


def reducer_snapshot(state: Mapping[str, Mapping[str, Any]]) -> Dict[str, str]:
    import json

    return {eid: json.dumps(s, ensure_ascii=False, sort_keys=True) for eid, s in state.items()}


@dataclass
class _Slot:
    """
    Bir sporun kaynakları ve gözetim bilgisi. `source` yoklamadır; `page` push kaynağıdır: `page` seçiliyse
    PageSource, `direct` seçiliyse DirectSource (aynı arayüz: feed, ensure_open, drain, close), yoksa None.
    """

    tracker: _Tracker
    source: Any
    arbiter: SportArbiter
    page: Optional[Any] = None
    started: bool = False
    failures: int = 0
    retry_at: float = 0.0


@dataclass
class LiveReport:
    """Servisin sayaçları (`status()` ve komutun özeti)."""

    source: str = SOURCE_POLL
    sports: Tuple[str, ...] = ()
    rounds: int = 0
    requests: int = 0
    events: int = 0
    confirmed: int = 0
    source_restarts: int = 0
    blocked: bool = False
    started_at: Optional[float] = None
    heartbeat_at: Optional[float] = None
    finished: bool = False  # izlenen maçların hepsi bitti
    leaders: Dict[str, str] = field(default_factory=dict)  # spor → önde olan kaynak ("page", "direct", "poll")
    push_frames: int = 0  # indirgeyiciye verilen push kareleri
    source_switches: int = 0
    last_switch: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source, "sports": list(self.sports), "rounds": self.rounds, "requests": self.requests,
            "events": self.events, "confirmed": self.confirmed, "source_restarts": self.source_restarts,
            "blocked": self.blocked, "started_at": self.started_at, "heartbeat_at": self.heartbeat_at,
            "finished": self.finished, "leaders": dict(sorted(self.leaders.items())),
            "push_frames": self.push_frames, "source_switches": self.source_switches,
            "last_switch": self.last_switch,
        }


SourceFactory = Callable[[str, Fetch, Callable[[], float]], Any]


def poll_source_factory(max_event_polls: Callable[[], int]) -> SourceFactory:
    def make(sport: str, get: Fetch, clock: Callable[[], float]) -> PollSource:
        return PollSource(sport, get, clock=clock, max_event_polls=max_event_polls)

    return make


# --- servis ------------------------------------------------------------------------------------------


class LiveService:
    """
    store            açık Store (yazılabilir)
    scope            izlenecekler; None: takiplerden (`live=true`), SCOPE_RELOAD_SECONDS'ta bir yeniden okunur
    fetch            API yolu → yanıt; None: gerçek istek katmanı (`watch` şeridiyle). 404 → None,
                     engellenme → Blocked fırlatmalıdır
    clock            saat (epoch saniye)
    sleep            istek şeridinin beklemesi (gerçek istek yokken süreç içi)
    poll_interval    canlı listenin okunma aralığı ([live] poll_interval_seconds)
    max_event_polls  turda en çok kaç maç sayfası ([live] max_event_polls)
    source_factory   (spor, get, saat) → kaynak; varsayılan PollSource
    confirm          bitişte maçın yükünü sakla (store.events.observe)
    requested_source "page", "direct" ya da "poll"; `page` sayfaları açar, `direct` push sunucusuna kendisi
                     bağlanır (yalnızca bu değer kelimesi kelimesine verildiyse), `poll` yalnızca yoklar
    page_opener      sayfaları açan (push_source.PageOpener); None: gerçek tarayıcı (canlı profil), yalnızca
                     `page` seçiliyse ve ilk spor eklenince kurulur
    direct_connection `direct`'in bağlantısı (direct_source.DirectConnection); None: gerçek bağlantı ve kimlik
                     bilgisi okuyucusu, yalnızca `direct` seçiliyse ve ilk spor eklenince kurulur
    """

    def __init__(self, store: Any, scope: Optional[LiveScope] = None, *, fetch: Optional[Fetch] = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 poll_interval: float = LIST_INTERVAL_SECONDS, max_event_polls: int = 20,
                 source_factory: Optional[SourceFactory] = None, confirm: bool = True,
                 requested_source: str = SOURCE_POLL, page_opener: Optional[PageOpener] = None,
                 direct_connection: Optional[Any] = None) -> None:
        from sofascore_scraper import throttle

        self._store = store
        self._scope = scope
        self._fetch = fetch or _default_fetch
        self._clock = clock
        self._sleep = sleep
        self._poll_interval = max(1.0, float(poll_interval))
        self._source_factory = source_factory or poll_source_factory(lambda: max(1, int(max_event_polls)))
        self._confirm = confirm
        self._throttle = throttle.lane(WATCH_THROTTLE_LANE, MIN_REQUEST_SPACING_SECONDS, shared=fetch is None,
                                       clock=clock, sleep=sleep)
        self._slots: Dict[str, _Slot] = {}
        self._stop: Any = None
        self._blocked_rounds = 0
        self._blocked_since: Optional[float] = None
        self._last_prune: Optional[float] = None
        self._last_scope_read = 0.0
        self._blocked_retry_at = 0.0
        self._last_heartbeat: Optional[float] = None
        self.report = LiveReport(requested_source_note(requested_source))
        # Push kaynağı: yalnızca istenen kaynak `page` ya da kelimesi kelimesine `direct` ise (yedek değil)
        self._push_source: Optional[str] = self.report.source if self.report.source in PUSH_SOURCES else None
        self._push = self._push_source is not None
        self._pending_confirms: Dict[Tuple[str, int], Tuple[int, float]] = {}
        self._page_opener = page_opener
        self._direct_connection = direct_connection if self._push_source == SOURCE_DIRECT else None

    # --- istekler ---------------------------------------------------------------------------

    def _get(self, path: str) -> Optional[Dict[str, Any]]:
        self._throttle.wait()
        self.report.requests += 1
        try:
            return self._fetch(path)
        except Blocked:
            raise
        except Exception as e:  # tek istek hatası izlemeyi durdurmasın
            logger.warning("Live request failed: %s: %s", path, type(e).__name__)
            return None

    # --- açılış ve kapanış ------------------------------------------------------------------

    def _read_scope(self) -> LiveScope:
        if self._scope is not None and not self._scope.from_follows:
            return self._scope
        return scope_from_follows(self._store.follows.list(enabled=True))

    def _apply_scope(self, scope: LiveScope) -> None:
        for sport_scope in scope.sports:
            slot = self._slots.get(sport_scope.sport)
            if slot is not None:
                slot.tracker.scope = sport_scope
                continue
            self._store.watch.import_legacy(sport_scope.sport)
            state = self._store.watch.load(sport_scope.sport)
            tracker = _Tracker(self, sport_scope, state)
            sport = sport_scope.sport
            arbiter = SportArbiter(sport, poll_interval=self._poll_interval, push=self._push,
                                   push_name=self._push_source or SOURCE_PAGE)
            page = self._push_for(sport)
            self._slots[sport] = _Slot(tracker, self._source_factory(sport, self._get, self._clock), arbiter, page)
            self.report.leaders[sport] = arbiter.leader
        for sport in [s for s in self._slots if scope.sport(s) is None]:
            self._save(self._slots[sport].tracker)
            removed = self._slots.pop(sport)  # takip kaldırıldı: o spor artık izlenmez
            if removed.page is not None:
                removed.page.close()
            self.report.leaders.pop(sport, None)
        self._scope = scope
        self.report.sports = tuple(sorted(self._slots))

    def _push_for(self, sport: str) -> Optional[Any]:
        """Sporun push kaynağı: `page` → PageSource, `direct` → DirectSource; yalnızca yoklamada None."""
        if self._push_source == SOURCE_PAGE:
            return PageSource(sport, self._opener(), clock=self._clock)
        if self._push_source == SOURCE_DIRECT:
            from sofascore_scraper.services.live.direct_source import DirectSource

            return DirectSource(sport, self._direct(), clock=self._clock)
        return None

    def _direct(self) -> Any:
        """`direct`'in tek bağlantısı. Yalnızca istenen kaynak `direct` iken kurulur; başka hiçbir yol kurmaz."""
        if self._push_source != SOURCE_DIRECT:
            raise RuntimeError("the direct push connection is only built when the source is \"direct\"")
        if self._direct_connection is None:
            from sofascore_scraper.services.live.direct_source import BrowserCredentialReader, DirectConnection

            self._direct_connection = DirectConnection(BrowserCredentialReader())
        return self._direct_connection

    def _opener(self) -> PageOpener:
        if self._page_opener is None:
            from sofascore_scraper.services.live.push_source import BrowserPageOpener

            self._page_opener = BrowserPageOpener()
        return self._page_opener

    def _save(self, tracker: _Tracker) -> None:
        snapshot = reducer_snapshot(tracker.state)
        changed = [eid for eid, text in snapshot.items() if tracker.saved.get(eid) != text]
        changed += [eid for eid in tracker.saved if eid not in snapshot]
        if not changed:
            return
        retrying(lambda: self._store.watch.save(tracker.sport, tracker.state, changed=changed),
                 what="the watch state", stop=self._stop, sleep=self._sleep)
        tracker.saved = snapshot

    # --- çalışma ----------------------------------------------------------------------------

    def run(self, stop: Any, *, until_seconds: Optional[float] = None) -> LiveReport:
        """
        `stop.is_set()` olana, süre dolana ya da yalnızca maç id'leri izleniyorsa hepsi bitene kadar çalışır.
        `stop`: `is_set()` ve `wait(timeout)` olan nesne (threading.Event). Kilit başkasındaysa LeaseHeld.
        """
        from sofascore_scraper.client import request_context

        lease = self._store.lease(LIVE_LEASE, purpose=LEASE_PURPOSE)
        self._stop = stop
        started = self._clock()
        self.report.started_at = started
        try:
            with request_context(breaker=None):
                self._apply_scope(self._read_scope())
                self._last_scope_read = started
                logger.info("Live service started: source %s, sports %s", self.report.source,
                            ", ".join(self.report.sports) or "none")
                if self._push_source == SOURCE_DIRECT:
                    from sofascore_scraper.config.settings import LIVE_DIRECT_WARNING

                    logger.warning("The live source is \"direct\", chosen explicitly: %s.", LIVE_DIRECT_WARNING)
                while not stop.is_set():
                    round_started = self._clock()
                    polled = self._round()
                    if polled or not self._push or self._heartbeat_due():
                        self._heartbeat("running")
                    self._prune_if_due()
                    if self._all_done():
                        logger.info("All watched events have finished")
                        self.report.finished = True
                        break
                    if until_seconds is not None and self._clock() - started >= until_seconds:
                        break
                    self._reload_scope_if_due()
                    stop.wait(self._next_wait(round_started))
        finally:
            for slot in self._slots.values():
                try:
                    self._save(slot.tracker)
                except Exception as e:
                    logger.error("The watch state of %s could not be saved (%s)", slot.tracker.sport,
                                 type(e).__name__)
            self._close_pages()
            try:
                self._heartbeat("stopped")
            except Exception as e:
                logger.debug("The live status could not be written (%s)", type(e).__name__)
            lease.release()
            logger.info("Live service stopped after %d round(s), %d request(s), %d event(s)",
                        self.report.rounds, self.report.requests, self.report.events)
        return self.report

    def _close_pages(self) -> None:
        if not self._push:
            return
        for slot in self._slots.values():
            if slot.page is not None:
                slot.page.close()
        if self._direct_connection is not None:
            try:
                self._direct_connection.close()
            except Exception as e:
                logger.warning("The direct push connection could not be closed (%s)", type(e).__name__)
        if self._page_opener is not None:
            try:
                self._page_opener.close()
            except Exception as e:
                logger.warning("The live browser could not be closed (%s)", type(e).__name__)

    def _heartbeat_due(self) -> bool:
        return self._last_heartbeat is None or self._clock() - self._last_heartbeat >= HEARTBEAT_SECONDS

    def _round(self) -> bool:
        """
        Bir tur. Yalnızca yoklama: her spor için kaynağın turu (ilk turda başlangıç okumaları), gözetim altında.
        Push varken: her sporun kareleri okunur, hakem güncellenir ve yoklama yalnızca zamanı gelen sporlarda
        yapılır. Bir yoklama turu yapıldıysa True.
        """
        if not self._push:
            self._poll_round(sorted(self._slots))
            return True
        now = self._clock()
        due: List[str] = []
        for sport in sorted(self._slots):
            slot = self._slots.get(sport)
            if slot is None:
                continue
            try:
                self._drain_push(slot)
            except Blocked as e:  # bilinmeyen maçın ya da bitişin isteği engellendi
                self._save(slot.tracker)
                self._on_blocked(str(e))
            self._switch_if_needed(slot)
            if slot.arbiter.poll_due(now) and slot.retry_at <= now:
                due.append(sport)
        if self._pending_confirms and (self._blocked_since is None or now >= self._blocked_retry_at):
            try:
                self._retry_confirms()
            except Blocked as e:
                self._on_blocked(str(e))
        if not due or (self._blocked_since is not None and now < self._blocked_retry_at):
            return False
        return self._poll_round(due)

    def _poll_round(self, sports: Sequence[str]) -> bool:
        """Verilen sporların yoklama turu; engellenmede yarıda kalır (False)."""
        now = self._clock()
        for sport in sports:
            slot = self._slots.get(sport)
            if slot is None or slot.retry_at > now:
                continue
            try:
                if not slot.started:
                    slot.source.start(slot.tracker, self._start_ids(slot.tracker))
                    slot.started = True
                slot.source.tick(slot.tracker)
                slot.failures = 0
            except Blocked as e:
                self._save(slot.tracker)
                self._on_blocked(str(e))
                return False
            except Exception as e:
                slot.failures += 1
                delay = min(SOURCE_RESTART_MAX_SECONDS, SOURCE_RESTART_FIRST_SECONDS * 2 ** (slot.failures - 1))
                slot.retry_at = self._clock() + delay
                slot.source = self._source_factory(sport, self._get, self._clock)
                slot.started = False
                self.report.source_restarts += 1
                logger.error("The %s source of %s failed (%s); restarting it in %.0f s",
                             SOURCE_POLL, sport, type(e).__name__, delay, exc_info=True)
            slot.arbiter.polled(self._clock())
            self._save(slot.tracker)
        self.report.rounds += 1
        if self._blocked_since is not None:
            self._on_recovered()
        return True

    # --- push ----------------------------------------------------------------------------------

    def _drain_push(self, slot: _Slot) -> None:
        """Sayfanın işaretlerini okur: hakemi günceller, kareleri gözleme çevirir; sayfa yoksa açar."""
        page = slot.page
        if page is None:
            return
        page.ensure_open()
        applied = False
        for signal in page.drain():
            applied = self._push_signal(slot, signal) or applied
        if applied:
            self._save(slot.tracker)

    def _push_signal(self, slot: _Slot, signal: Signal) -> bool:
        arbiter, sport = slot.arbiter, slot.tracker.sport
        if signal.kind == SIGNAL_OPEN:
            logger.info("The push connection of %s is open (%s source)", sport, self._push_source)
            arbiter.opened(signal.at)
        elif signal.kind == SIGNAL_CLOSE:
            logger.info("The push connection of %s closed (%s source); it reconnects by itself", sport,
                        self._push_source)
            arbiter.closed(signal.at)
        elif signal.kind == SIGNAL_PING:
            arbiter.ping(signal.at)
        elif signal.kind == SIGNAL_GONE:
            arbiter.failed(signal.text or "closed")
        elif signal.kind == SIGNAL_GAP:
            logger.warning("Push frames of %s were dropped (the service fell behind); polling once", sport)
            arbiter.gap()
        elif signal.kind == SIGNAL_ERROR:
            logger.info("The push server reported an error for %s (%s)", sport, signal.text)
        elif signal.kind == SIGNAL_FRAME and signal.frame is not None:
            arbiter.frame(signal.at)
            return self._push_frame(slot.tracker, signal.frame)
        return False

    def _push_frame(self, tracker: _Tracker, frame: Mapping[str, Any]) -> bool:
        """Bir kare → gözlem (son bilinen nesneye birleştirilerek). Kapsam dışı ya da bilinmeyen maç: hayır."""
        eid = int(frame["id"])
        key = str(eid)
        s = tracker.state.get(key)
        if s is not None:
            if s.get("done") or not tracker.in_scope(key, s):
                return False
            merged = tracker.known.apply(eid, frame)
            if merged is None:  # durum var ama nesne yok (yeniden başlatma): ilk yoklama turu tohumlar
                return False
            self.report.push_frames += 1
            self._observe(tracker, merged, VIA_PUSH)
            return True
        base = tracker.known.get(eid)
        if base is not None:
            if not tracker.listed(base):
                return False
            # Yoklamanın gördüğü önceki hal: durum ondan kurulur, kare geçişi gösterir
            tracker.state[key] = {"class": None, "done": False}
            self._observe(tracker, base, reducer.VIA_LIST)
            merged = tracker.known.apply(eid, frame)
            if merged is not None and not tracker.state[key].get("done"):
                self.report.push_frames += 1
                self._observe(tracker, merged, VIA_PUSH)
            return True
        return self._lookup_unknown(tracker, eid, frame)

    def _lookup_unknown(self, tracker: _Tracker, eid: int, frame: Mapping[str, Any]) -> bool:
        """
        Yoklamanın hiç görmediği maçın durum karesi: kapsam turnuva ya da takım içeriyorsa tek bir /event isteği
        (spor başına dakikada en çok UNKNOWN_LOOKUPS_PER_MINUTE). Kapsamdaysa durumu o yükten kurulur (önceki
        hali bilinmediği için geçiş olayı üretilmez, yoklamanın ilk görüşü gibi); değilse bir daha sorulmaz.
        """
        scope = tracker.scope
        if not (scope.tournament_ids or scope.team_ids) or eid in tracker.outside:
            return False
        if "status.code" not in frame and "status.type" not in frame:
            return False
        now = self._clock()
        while tracker.lookups and now - tracker.lookups[0] >= 60.0:
            tracker.lookups.popleft()
        if len(tracker.lookups) >= UNKNOWN_LOOKUPS_PER_MINUTE:
            return False
        tracker.lookups.append(now)
        data = self._get(f"/event/{eid}")
        event = (data or {}).get("event")
        if not event:
            tracker.outside.add(eid)
            return False
        tracker.known.seed(event)
        if not tracker.listed(event):
            tracker.outside.add(eid)
            return False
        tracker.state[str(eid)] = {"class": None, "done": False}
        self._observe(tracker, event, reducer.VIA_EVENT)
        return True

    def _switch_if_needed(self, slot: _Slot) -> None:
        switch = slot.arbiter.update(self._clock())
        if switch is None:
            return
        self.report.leaders[switch.sport] = switch.to_source
        self.report.source_switches += 1
        self.report.last_switch = {**switch.to_data(), "at": switch.at}
        logger.info("Live source of %s: %s -> %s (%s)", switch.sport, switch.from_source, switch.to_source,
                    switch.reason)
        self._system(SYSTEM_SOURCE_CHANGED, switch.to_data())

    def _start_ids(self, tracker: _Tracker) -> List[int]:
        """
        İzleme başında maç sayfası okunacak maçlar. Komut satırından verilen maçların hepsi (izleme onlar bitince
        biter). Takipten gelen maçlardan, kaydı "başlamadı" diyen ve başlangıcı FOLLOW_START_WINDOW_SECONDS'tan
        uzak olanlar okunmaz: başladığında canlı listeden görülür. Kaydı olmayan maç bir kez okunur (başlangıcı
        bilinmiyor); sonra izleyicinin durumunda kalır ve yeniden okunmaz.
        """
        ids = sorted(tracker.scope.event_ids)
        if self._scope is None or not self._scope.from_follows:
            return ids
        horizon = self._clock() + FOLLOW_START_WINDOW_SECONDS
        near: List[int] = []
        later: List[int] = []
        for eid in ids:
            if str(eid) in tracker.state:
                near.append(eid)  # kaynak durumda olanı zaten okumaz
                continue
            try:
                row = self._store.events.get(eid)
            except Exception as e:  # katalog okunamadı: eskisi gibi okunur
                logger.debug("Event %s could not be looked up before the start read: %s", eid, e)
                row = None
            start_ts = getattr(row, "start_ts", None)
            if (row is not None and getattr(row, "status_class", None) == StatusClass.NOT_STARTED.value
                    and isinstance(start_ts, (int, float)) and start_ts > horizon):
                later.append(eid)
            else:
                near.append(eid)
        if later:
            logger.info("Not reading %d followed event(s) at start, they begin more than %.0f h from now: %s",
                        len(later), FOLLOW_START_WINDOW_SECONDS / 3600, ", ".join(map(str, later)))
        return near

    def _all_done(self) -> bool:
        if not self._slots or self._scope is None or self._scope.from_follows:
            return False
        return all(slot.tracker.scope.only_events and not active_ids(slot.tracker)
                   for slot in self._slots.values())

    def _next_wait(self, round_started: float) -> float:
        if self._push:
            return PUSH_DRAIN_SECONDS
        if self._blocked_since is not None:
            return min(BLOCKED_MAX_SECONDS, BLOCKED_FIRST_SECONDS * 2 ** max(0, self._blocked_rounds - 1))
        wait = max(0.0, self._poll_interval - (self._clock() - round_started))
        retries = [slot.retry_at - self._clock() for slot in self._slots.values() if slot.retry_at > self._clock()]
        return max(0.0, min([wait, *retries]))

    def _reload_scope_if_due(self) -> None:
        if self._scope is None or not self._scope.from_follows:
            return
        now = self._clock()
        if now - self._last_scope_read < SCOPE_RELOAD_SECONDS:
            return
        self._last_scope_read = now
        try:
            self._apply_scope(self._read_scope())
        except Exception as e:
            logger.warning("The live scope could not be read again (%s); keeping the current one", type(e).__name__)

    # --- gözlem ve olaylar ------------------------------------------------------------------

    def _observe(self, tracker: _Tracker, event: Mapping[str, Any], via: str) -> None:
        eid = str(event.get("id"))
        if self._push and via != VIA_PUSH and not tracker.known.seed(event):
            logger.debug("Skipping an older %s observation of event %s (push has a newer one)", via, eid)
            return
        obs = reducer.Observation(event=event, via=via, at=self._clock())
        tracker.state[eid], emitted = reducer.reduce(tracker.state.get(eid), obs, tracker.sport)
        tournament_id = tracker.state[eid].get("tournament_id")
        source = (self._push_source or SOURCE_PAGE) if via == VIA_PUSH else SOURCE_POLL
        for item in emitted:
            stream_event = reducer.stream_event(item, event, tracker.sport, tournament_id=tournament_id,
                                                source=source)
            seqs = append_retrying(self._store, reducer.LIVE_STREAM, [stream_event], stop=self._stop,
                                   sleep=self._sleep)
            if seqs and seqs[0] is not None:
                self.report.events += 1
            if (self._confirm and item["type"] == reducer.STATUS_CHANGED
                    and item.get("to") in reducer.TERMINAL_CLASSES):
                self._confirm_terminal(tracker, int(eid), event, via, source)

    def _confirm_terminal(self, tracker: _Tracker, event_id: int, event: Mapping[str, Any], via: str,
                          source: str = SOURCE_POLL) -> None:
        """Sonuçlanan maçın yükünü saklar: maç sayfasından gelmediyse tek bir /event isteğiyle."""
        from sofascore_scraper.store import StoreError, StreamEvent

        payload: Optional[Mapping[str, Any]] = event if via == reducer.VIA_EVENT else None
        if payload is None:
            data = self._get(f"/event/{event_id}")
            payload = (data or {}).get("event")
        if not payload:
            logger.info("Event %s finished but its page could not be read; a later sync stores it", event_id)
            return
        if source in PUSH_SOURCES and classify_status(dict(payload)).value not in reducer.TERMINAL_CLASSES:
            # Push bitişi bir saniye içinde gösterir; maç sayfası (CDN) bir süre eski hali verebilir. Eski yük
            # saklanmaz: istek CONFIRM_RETRY_SECONDS sonra (en çok CONFIRM_ATTEMPTS kez) yinelenir.
            self._defer_confirm(tracker.sport, event_id)
            return
        self._pending_confirms.pop((tracker.sport, event_id), None)
        observed_at = dt.datetime.fromtimestamp(self._clock(), dt.timezone.utc)
        on_change = _change_rule(tracker.sport)
        try:
            result = retrying(lambda: self._store.events.observe(event_id, payload, observed_at=observed_at,
                                                                 on_event_change=on_change),
                              what=f"the payload of event {event_id}", stop=self._stop, sleep=self._sleep)
        except StoreError as e:
            logger.warning("The payload of finished event %s could not be stored (%s)", event_id, type(e).__name__)
            return
        self.report.confirmed += 1
        if result.change_seq is not None:
            append_retrying(self._store, "change", [StreamEvent(
                type=CHANGE_RECORDED, data={"change_seq": result.change_seq}, event_id=event_id,
                sport=tracker.sport, source=source, dedup_key=f"change:{result.change_seq}",
            )], stop=self._stop, sleep=self._sleep)

    def _defer_confirm(self, sport: str, event_id: int) -> None:
        attempts, _ = self._pending_confirms.get((sport, event_id), (0, 0.0))
        if attempts + 1 >= CONFIRM_ATTEMPTS:
            self._pending_confirms.pop((sport, event_id), None)
            logger.info("The page of event %s still shows it unfinished; a later sync stores it", event_id)
            return
        self._pending_confirms[(sport, event_id)] = (attempts + 1, self._clock() + CONFIRM_RETRY_SECONDS)

    def _retry_confirms(self) -> None:
        now = self._clock()
        for (sport, event_id), (_, due) in sorted(self._pending_confirms.items()):
            slot = self._slots.get(sport)
            if slot is None:
                self._pending_confirms.pop((sport, event_id), None)
            elif due <= now:
                self._confirm_terminal(slot.tracker, event_id, {}, VIA_PUSH, self._push_source or SOURCE_PAGE)

    # --- engellenme ---------------------------------------------------------------------------

    def _system(self, type_: str, data: Mapping[str, Any]) -> None:
        from sofascore_scraper.store import StreamEvent

        append_retrying(self._store, "system", [StreamEvent(type=type_, data=dict(data), source="system")],
                        stop=self._stop, sleep=self._sleep)

    def _on_blocked(self, reason: str) -> None:
        self._blocked_rounds += 1
        wait = min(BLOCKED_MAX_SECONDS, BLOCKED_FIRST_SECONDS * 2 ** (self._blocked_rounds - 1))
        self._blocked_retry_at = self._clock() + wait
        if self._blocked_since is None:
            self._blocked_since = self._clock()
            self.report.blocked = True
            logger.warning("SofaScore is refusing requests; the live service pauses for %.0f s", wait)
            self._system(SYSTEM_BLOCKED, {"source": SOURCE_POLL, "retry_in_s": wait, "reason": reason[:200]})

    def _on_recovered(self) -> None:
        since = self._blocked_since or self._clock()
        self._blocked_since = None
        self._blocked_rounds = 0
        self.report.blocked = False
        logger.info("SofaScore answers again; the live service continues")
        self._system(SYSTEM_RECOVERED, {"source": SOURCE_POLL, "blocked_for_s": round(self._clock() - since, 1)})

    # --- bakım ve durum -----------------------------------------------------------------------

    def _prune_if_due(self) -> None:
        from sofascore_scraper.store import DEFAULT_PRUNE_MAX_AGE_SECONDS, DEFAULT_PRUNE_MAX_ROWS, StoreError

        now = self._clock()
        if self._last_prune is not None and now - self._last_prune < PRUNE_INTERVAL_SECONDS:
            return
        self._last_prune = now
        try:
            removed = self._store.streams.prune(max_age_s=DEFAULT_PRUNE_MAX_AGE_SECONDS, max_rows=DEFAULT_PRUNE_MAX_ROWS)
        except StoreError as e:
            logger.warning("The event log could not be pruned (%s)", type(e).__name__)
            return
        if removed:
            logger.info("Pruned %d old event(s) from the event log", removed)

    def _heartbeat(self, state: str) -> None:
        from sofascore_scraper.store import StoreError

        self.report.heartbeat_at = self._last_heartbeat = self._clock()
        try:
            self._store.runtime.set(RUNTIME_KEY, {"state": state, **self.report.to_dict()})
        except StoreError as e:  # durum bilgisi yazılamadı: izleme sürer
            logger.debug("The live status could not be written (%s)", type(e).__name__)

    def status(self) -> Dict[str, Any]:
        return self.report.to_dict()


def _change_rule(sport: str) -> Callable[[Optional[Mapping[str, Any]], Mapping[str, Any]], Optional[Dict[str, Any]]]:
    """
    Bitiş onayının değişiklik günlüğü kuralı: yenilemeninkiyle aynı (sofascore_scraper/refresh.py). Saklanan yük zaten
    sonuçlanmış bir maçınsa ve yeni yük ondan farklıysa bir satır; ilk saklanan yük ya da sonuçlanmamış bir
    maçın eski yükü değişiklik değildir (günlük, sonuçlanmış maçların düzeltmelerini tutar).
    """
    from sofascore_scraper.refresh import change_row, diff_basic
    from sofascore_scraper.status import classify_status

    def on_change(old: Optional[Mapping[str, Any]], new: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        if not old or classify_status(dict(old)).value not in reducer.TERMINAL_CLASSES:
            return None
        changed = diff_basic(dict(old), dict(new))
        return change_row(dict(old), dict(new), changed, sport) if changed else None

    return on_change


def requested_source_note(requested: str) -> str:
    """Kullanılan kaynak: istenen (`page`, `direct`, `poll`); bilinmeyen bir değer `poll` olur, asla `direct`."""
    return requested if requested in AVAILABLE_SOURCES else SOURCE_POLL


def _default_fetch(path: str) -> Optional[Dict[str, Any]]:
    """Gerçek istek: 404 → None; 429, 403 ve açık devre kesici → Blocked; başka hata olduğu gibi."""
    from sofascore_scraper.client import api_url
    from sofascore_scraper.exceptions import APIError, CircuitOpenError, RateLimitError, ResourceNotFoundError
    from sofascore_scraper.utils import make_api_request

    try:
        return make_api_request(api_url(path), raise_on_failure=True)
    except ResourceNotFoundError:
        return None
    except (RateLimitError, CircuitOpenError) as e:
        raise Blocked(type(e).__name__) from e
    except APIError as e:
        if getattr(e, "status_code", None) == 403:
            raise Blocked("APIError 403") from e
        raise


def live_status(store: Any) -> Dict[str, Any]:
    """
    Veri dizinindeki canlı servisin durumu: kilidi tutan var mı, hangi kaynak önde, son kalp atışı. Kilit
    tutulmuyorsa `running` False'tur; son çalışmanın bilgisi (`last`) yine verilir.
    """
    holder = store.lease_holder(LIVE_LEASE)
    fact = store.runtime.get(RUNTIME_KEY)
    value: Mapping[str, Any] = fact.value if fact is not None else {}
    running = holder is not None
    return {
        "running": running,
        "pid": holder.pid if holder is not None else None,
        "host": holder.host if holder is not None else None,
        "source": value.get("source") if running else None,
        "sports": list(value.get("sports") or []) if running else [],
        "heartbeat_at": value.get("heartbeat_at"),
        "blocked": bool(value.get("blocked")) if running else False,
        "leaders": dict(value.get("leaders") or {}) if running else {},
        "last_switch": value.get("last_switch") if running else None,
        "last": dict(value) if value else None,
    }


__all__ = [
    "AVAILABLE_SOURCES",
    "LEASE_PURPOSE",
    "LIVE_LEASE",
    "PUSH_SOURCES",
    "RUNTIME_KEY",
    "SOURCES",
    "Blocked",
    "LiveReport",
    "LiveScope",
    "LiveService",
    "SportScope",
    "append_retrying",
    "explicit_scope",
    "live_status",
    "poll_source_factory",
    "retrying",
    "scope_from_follows",
]
