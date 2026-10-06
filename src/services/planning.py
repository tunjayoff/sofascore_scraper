"""
Planlama: bir maçın neye ihtiyacı olduğu (docs/design/02-services.md 3.2).

Kural tek yerdedir ve saftır: `compute_need` bir maçın katalogdaki durumuna (`EventState`: maç satırı ve dilim
satırları), dilim seçimine ve yenileme politikasına bakar; dosya okumaz, depoya sormaz, saate bakmaz (an
politikadadır). Depodan durumları okuyan sarmalayıcılar (`event_needs`, `refresh_due_events`) ve sıralama
(`order_by_need`) da buradadır; src/match_data_fetcher.py'deki planlayıcılar bunlara devreder.

İhtiyaçlar, öncelik sırasıyla (tasarım tablosu; ilk uyan kural kazanır):

  full     kayıt yok: maç katalogda yok, ya da olay yükü yok (yalnızca bir listeden biliniyor) ve listedeki durumu
           bitmiş ya da bilinmiyor
  none     yalnızca bir listeden biliniyor ve başlamamış, oynanıyor ya da ertelenmiş / iptal: listeler satırı
           güncel tutar; maç bitince bir sonraki liste satırı bitmiş gösterir ve maç `full` olur (plan maddesi
           ST-27: her durumdaki maç saklanır, detayı bitince indirilir)
  refresh  daha yeni bir liste kaydı bayatlamış saydı (`stale`: durum, skor ya da başlangıç zamanı değişmiş);
           önce /event yeniden okunur (eksik dilimler bir sonraki planda)
  none     kayıt açık (oynanıyor, başlamamış ya da durumu bilinmiyor): canlı servisin ve listelerin işidir;
           liste maçı bitmiş gösterince kayıt bayatlar ve yenilenir
  none     kayıt ertelenmiş / iptal (void) ve yenileme zamanı gelmemiş: dilim beklenmez
  refill   olay yükü var ve maç bitmiş; seçilmiş, maçın sporuna uyan, evresinde var olabilen ve tamlık hesabına
           giren bir dilim eksik: satırı yok ya da `ok` değil ve kesin + doğrulanmamış "veri yok" sayısı eşiğin
           altında
  refresh  kayıt kapanmış bir durumda (bitmiş ya da void), geçici ve yenileme zamanı gelmiş (src/refresh.py;
           Store.events.refresh_candidates'in `status_classes=SETTLED_CLASSES` ile koşulu)
  none     tamam

`refill` iş biriminin dilimleri, maçın eksik olan bütün seçili dilimleridir: tamlık hesabına girmeyen (isteğe
bağlı) dilimler de (ör. tenisin point_by_point'i), maç zaten yeniden okunuyorsa birlikte istenir.

Dilim seçimi (plan maddesi P27, 02-services.md 3.1). Seçilmeyen dilim hiç istenmez, eksik de sayılmaz;
tamlık yalnızca seçilmiş ve maçın sporunda tamlık hesabına giren dilimlere bakar. Seçim maç maç çözülür
(`SelectionPolicy`): maçı kapsayan takibin `slices`'ı → `[slices.<spor>]` → `[defaults] slices` → kayıt
defterinin `default_enabled`'ı. Depodan okuyan sarmalayıcıların ve boru hattının varsayılanı `CONFIGURED`dır:
etkin ayarlar ve takip tablosu (`configured_policy`). Varsayılan yapılandırmada (`[defaults] slices =
["core"]`) seçim bugünkü kayıt defterinin seçimiyle aynıdır.

Liste iş birimleri (`listing`, plan maddesi P14): bir turnuvanın sezon listesi (`season_list_item`: sahibi
turnuva, dilimi `seasons`) ve bir sezonun maç programı (`schedule_item`: sahibi sezon, dilimi `schedule`; tur
ya da olay sayfaları). Yürütülmeleri src/services/listing.py'dedir.
"""
from __future__ import annotations

import dataclasses
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple, Union

from src.services.query import (
    DEFAULT_EMPTY_THRESHOLD,
    NEED_FULL,
    NEED_NONE,
    NEED_REFILL,
    NEED_REFRESH,
    RefreshPolicy,
    _chunks,
    _event_ids,
)
from src import sports as sports_registry
from src.sports import SliceSelection, select_slices
from src.status import StatusClass
from src.store import Ref, Scope, StoreError

if TYPE_CHECKING:
    from src.store import EventRow, EventState, SliceInfo, Store

Need = Literal["full", "refill", "refresh", "none"]
WorkNeed = Literal["listing", "full", "refill", "refresh", "owner"]
# Dilim seçimi: None (kayıt defterinin varsayılanları), ad listesi, SliceSelection, SelectionPolicy (maç maç
# çözülür) ya da CONFIGURED (etkin ayarların politikası)
Selection = Union[SliceSelection, "SelectionPolicy", Iterable[str], None]

logger = logging.getLogger(__name__)

NEEDS: Tuple[str, ...] = (NEED_FULL, NEED_REFILL, NEED_REFRESH, NEED_NONE)
NEED_LISTING = "listing"

# Liste iş birimlerinin dilim anahtarları (Store'daki adlarıyla: src/store/entities.py KEY_SEASONS, KEY_SCHEDULE)
LISTING_SEASONS = "seasons"
LISTING_SCHEDULE = "schedule"
_SLICE_OK = "ok"

# Kapanmış durumlar (docs/design/01-storage.md 8.3): yenileme politikası yalnızca bunlara bakar. Açık olanlar
# (başlamamış, oynanıyor, bilinmiyor) listelerin ve canlı servisin işidir.
SETTLED_CLASSES: Tuple[str, ...] = (StatusClass.COMPLETED.value, StatusClass.DECIDED_WITHOUT_PLAY.value,
                                    StatusClass.VOID.value)
# Detayı indirilen durumlar: oynanıp biten ya da oynanmadan karara bağlanan maç
_FINISHED_CLASSES = (StatusClass.COMPLETED.value, StatusClass.DECIDED_WITHOUT_PLAY.value)
# Yalnızca bir listeden bilinen ve henüz indirilmeyen maçın durumları: maç bitince liste satırı değişir
_WAITING_CLASSES = (StatusClass.NOT_STARTED.value, StatusClass.LIVE.value, StatusClass.VOID.value)

# Durum sınıfından maçın evresi (dilimin `phases`'ı ile karşılaştırılır); bilinmeyen durumda evre yoktur.
# Ertelenen ya da iptal edilen maç (void) tasarım tablosunda başlamamış maçla aynı satırdadır.
_PHASES: Dict[str, str] = {
    StatusClass.NOT_STARTED.value: "pre",
    StatusClass.VOID.value: "pre",
    StatusClass.LIVE.value: "live",
    StatusClass.COMPLETED.value: "post",
    StatusClass.DECIDED_WITHOUT_PLAY.value: "post",
}


@dataclass(frozen=True)
class SelectionPolicy:
    """
    Yapılandırılmış dilim seçimi (02-services.md 3.1, plan maddesi P27): bir maçın seçimi sporuna ve onu
    kapsayan takibe göre çözülür (`for_event`; sports.resolve_selection).

    defaults     [defaults] slices (None: kayıt defterinin `default_enabled`'ı)
    sports       spor → ([slices.<spor>] enable, disable)
    follows      (tür, kimlik) → takibin `slices`'ı (None olmayanlar): config [[follow]] ve takip tablosu
                 (API ile eklenenler, origin api)

    Bir maçı birden çok takip kapsıyorsa en dar olan kazanır: maç takibi, turnuva takibi, ev sahibi takımın
    takibi, konuk takımın takibi, sonra maçı getiren oyuncu takibi (`via_events`, FX-19: maçın yükü oyuncuyu
    söylemez; eşitleme oyuncunun listesinden getirdiği maçları `with_follow_events` ile bildirir). Takip seçimi
    vermiyorsa sporun seçimi geçerlidir.
    """

    defaults: Optional[Tuple[str, ...]] = None
    sports: Mapping[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = field(default_factory=dict)
    follows: Mapping[Tuple[str, int], Mapping[str, Any]] = field(default_factory=dict)
    # [client] odds_provider: sağlayıcı alt anahtarlı dilimlerin (bahis oranları) alt anahtarı (P28)
    provider: str = sports_registry.DEFAULT_ODDS_PROVIDER
    # [client] odds_country: oranların kaydına yazılan ülke (büyük harf); boş = yazılmaz. Kullanıcı verir, makineden
    # türetilmez (sahibin kararı, 2026-10-06; plan maddesi FX-15)
    country: str = ""
    # maç → onu getiren takibin seçimi (oyuncu takibi; FX-19); en son bakılır
    via_events: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Any, follows: Iterable[Any] = (), *,
                      defaults_given: bool = True) -> "SelectionPolicy":
        """
        src.config Settings'ten (ve isteğe bağlı takip satırlarından: src.store Follow ya da config
        FollowSpec) kurar. Takip satırları ayarların [[follow]]'larından sonra uygulanır (aynı takipte satır
        kazanır). Adlar denetlenir: bilinmeyen ad UnknownSliceName.

        defaults_given: [defaults] slices bir katmanda (dosya, ortam, overrides.json ...) verildi mi. Verilmediyse
        taban kayıt defterinin `default_enabled`'ıdır (zincirin son halkası): ayarın varsayılanı ["core"] olsa da
        varsayılanı kapalı bir core dilimi seçilmez. Verildiyse yazılan adlar geçerlidir.
        """
        chosen: Dict[Tuple[str, int], Mapping[str, Any]] = {}
        for spec in (*tuple(getattr(settings, "follows", ()) or ()), *tuple(follows)):
            key = (str(spec.kind), int(spec.entity_id))
            if spec.slices is None:
                chosen.pop(key, None)
            else:
                chosen[key] = dict(sports_registry.follow_slices(spec.slices) or {})
        per_sport = {sport: (tuple(override.enable), tuple(override.disable))
                     for sport, override in (getattr(settings, "slices", None) or {}).items()}
        defaults = tuple(settings.defaults.slices) if defaults_given else None
        provider = getattr(getattr(settings, "client", None), "odds_provider", None)
        country = str(getattr(getattr(settings, "client", None), "odds_country", "") or "").strip().upper()
        policy = cls(defaults=defaults, sports=per_sport, follows=chosen,
                     provider=str(provider) if provider is not None else sports_registry.DEFAULT_ODDS_PROVIDER,
                     country=country)
        policy.check()
        return policy

    def check(self) -> None:
        """Bütün adlar kayıt defterinde mi; değilse UnknownSliceName."""
        names = list(self.defaults or ())
        for enable, disable in self.sports.values():
            names += [*enable, *disable]
        for follow in self.follows.values():
            names += [name for values in follow.values() for name in values]
        sports_registry.check_slice_names(names)

    def follow_for(self, *, event_id: Optional[int] = None, tournament_id: Optional[int] = None,
                   team_ids: Iterable[Optional[int]] = ()) -> Optional[Mapping[str, Any]]:
        """Maçı kapsayan ve seçim veren en dar takibin `slices`'ı; yoksa None."""
        candidates = [("event", event_id), ("tournament", tournament_id), *(("team", team) for team in team_ids)]
        for kind, entity in candidates:
            if entity is not None:
                found = self.follows.get((kind, int(entity)))
                if found is not None:
                    return found
        return self.via_events.get(int(event_id)) if event_id is not None else None

    def with_follow_events(self, kind: str, entity_id: int, event_ids: Iterable[int]) -> "SelectionPolicy":
        """
        Bir takibin (oyuncu) listesinden gelen maçlar o takibin seçimini alır, maçı daha dar bir takip
        kapsamıyorsa (`follow_for`'un son halkası; FX-19). Takip seçim vermiyorsa politika aynen döner.
        """
        chosen = self.follows.get((kind, int(entity_id)))
        if chosen is None:
            return self
        merged: Dict[int, Mapping[str, Any]] = dict(self.via_events)
        for event_id in event_ids:
            merged.setdefault(int(event_id), chosen)
        return dataclasses.replace(self, via_events=merged)

    def for_sport(self, sport: Optional[str]) -> SliceSelection:
        """Takipsiz bir maçın (ya da sporun varsayılanının) seçimi."""
        enable, disable = self.sports.get(sport or "", ((), ()))
        return sports_registry.resolve_selection(self.defaults, sport_enable=enable, sport_disable=disable)

    def for_event(self, sport: Optional[str], *, event_id: Optional[int] = None,
                  tournament_id: Optional[int] = None, team_ids: Iterable[Optional[int]] = ()) -> SliceSelection:
        """Bir maçın seçimi: kapsayan takibin seçimi, sporun farkı ve varsayılanlar (`resolve_selection`)."""
        enable, disable = self.sports.get(sport or "", ((), ()))
        follow = self.follow_for(event_id=event_id, tournament_id=tournament_id, team_ids=team_ids)
        return sports_registry.resolve_selection(self.defaults, sport_enable=enable, sport_disable=disable,
                                                 follow=follow)

    def for_owner(self, kind: str, owner_id: int, sport: Optional[str], *,
                  tournament_id: Optional[int] = None) -> SliceSelection:
        """
        Maç dışı bir sahibin (sezon, takım, oyuncu, spor; P28) seçimi: sahibin kendi takibi (takım, oyuncu), yoksa
        turnuvasının takibi (sezon), sonra sporun farkı ve varsayılanlar. Sporun sahibi yalnızca sporun seçimini
        alır.
        """
        enable, disable = self.sports.get(sport or "", ((), ()))
        follow: Optional[Mapping[str, Any]] = None
        if kind in ("team", "player"):
            follow = self.follows.get((kind, int(owner_id)))
        if follow is None and tournament_id is not None:
            follow = self.follows.get(("tournament", int(tournament_id)))
        return sports_registry.resolve_selection(self.defaults, sport_enable=enable, sport_disable=disable,
                                                 follow=follow)

    def selections(self) -> Tuple[SliceSelection, ...]:
        """
        Politikanın verebileceği bütün seçimler (`extras_selected` için): spora özel farkı olan her sporun ve
        öteki sporların varsayılanı, ve her takibin bu sporlardaki seçimi.
        """
        sports_given: Tuple[Optional[str], ...] = (*self.sports, None)
        found = [self.for_sport(sport) for sport in sports_given]
        for (kind, entity_id), _follow in self.follows.items():
            for sport in sports_given:
                if kind == "event":
                    found.append(self.for_event(sport, event_id=entity_id))
                elif kind == "tournament":
                    found.append(self.for_event(sport, tournament_id=entity_id))
                else:
                    found.append(self.for_owner(kind, entity_id, sport))
        return tuple(found)

    def for_row(self, row: "EventRow") -> SliceSelection:
        """Katalogdaki bir maç satırının seçimi."""
        return self.for_event(row.sport or None, event_id=row.id, tournament_id=row.tournament_id,
                              team_ids=(row.home_id, row.away_id))

    def for_payload(self, event_id: int, sport: Optional[str], payload: Mapping[str, Any]) -> SliceSelection:
        """`/event` yükünden bir maçın seçimi (turnuva: uniqueTournament, takımlar: homeTeam / awayTeam)."""
        tournament = payload.get("tournament") if isinstance(payload.get("tournament"), Mapping) else {}
        unique = tournament.get("uniqueTournament") if isinstance(tournament.get("uniqueTournament"), Mapping) else {}
        teams = [team.get("id") for team in (payload.get("homeTeam"), payload.get("awayTeam"))
                 if isinstance(team, Mapping)]
        return self.for_event(sport, event_id=event_id, tournament_id=_int_or_none(unique.get("id")),
                              team_ids=[_int_or_none(team) for team in teams])


def _int_or_none(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class _Configured:
    """Seçim verilmediğinde yapılandırmanın seçimi (`configured_policy`). Tek örneği: CONFIGURED."""

    _instance: Optional["_Configured"] = None

    def __new__(cls) -> "_Configured":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "CONFIGURED"


# Depodan okuyan sarmalayıcıların ve boru hattının varsayılanı: etkin ayarların ve takip tablosunun seçimi
CONFIGURED: Any = _Configured()

_policy_cache: Dict[str, Any] = {}


def configured_policy(store: Optional["Store"] = None) -> SelectionPolicy:
    """
    Etkin ayarların seçimi (src.config.active: [defaults] slices, [slices.<spor>], [[follow]]); store verilirse
    takip tablosunun satırlarıyla (API'den eklenen takiplerin seçimi). Ayarlar değişmedikçe ve tablo okunmadıkça
    önbellekten.
    """
    from src.config import active

    loaded = active()
    defaults_given = loaded.source("defaults.slices").layer != "default"
    if store is None:
        cached = _policy_cache.get("settings")
        if cached is not None and cached[0] is loaded:
            return cached[1]
        policy = SelectionPolicy.from_settings(loaded.settings, defaults_given=defaults_given)
        _policy_cache["settings"] = (loaded, policy)
        return policy
    return SelectionPolicy.from_settings(loaded.settings, _follow_rows(store), defaults_given=defaults_given)


def _follow_rows(store: "Store") -> Tuple[Any, ...]:
    """Takip tablosunun etkin satırları; tablo okunamıyorsa (eski ya da sahte depo) boş."""
    follows = getattr(store, "follows", None)
    if follows is None:
        return ()
    try:
        return tuple(follows.list(enabled=True))
    except (StoreError, sqlite3.Error, OSError) as e:
        logger.warning("Follows could not be read for the slice selection (%s); the configured defaults apply", e)
        return ()


def resolve_policy(selection: Any, store: Optional["Store"] = None) -> Any:
    """CONFIGURED → `configured_policy(store)`; başka her değer olduğu gibi (None, ad listesi, seçim, politika)."""
    return configured_policy(store) if selection is CONFIGURED else selection


def selection_for(selection: Any, *, sport: Optional[str], row: Optional["EventRow"] = None) -> Any:
    """
    Bir maçın `select_slices`'a verilecek seçimi: politika ise maçın satırına (yoksa sporuna) göre çözülür,
    CONFIGURED ise yapılandırmanın politikası; öteki değerler (None, ad listesi, SliceSelection) olduğu gibi.
    """
    selection = resolve_policy(selection)
    if isinstance(selection, SelectionPolicy):
        return selection.for_row(row) if row is not None else selection.for_sport(sport)
    return selection


@dataclass(frozen=True)
class WorkItem:
    """
    Bir iş birimi (02-services.md 3.2): kimin için (`owner`), ne gerekiyor (`need`), hangi dilimler
    ((anahtar, alt anahtar) çiftleri; `refresh` ve `full` için boş: tam çekim sporu öğrenince dilimleri seçer),
    sporu ve `--dry-run`'ın göstereceği neden.
    """

    owner: Ref
    need: WorkNeed
    slices: Tuple[Tuple[str, str], ...]
    sport: Optional[str]
    reason: str


def phase_of(status_class: Optional[str]) -> Optional[str]:
    """Maçın evresi: başlamamış ya da ertelenmiş / iptal → pre, oynanıyor → live, bitmiş → post; bilinmiyorsa None."""
    return _PHASES.get(status_class) if status_class else None


def expected_slice_keys(sport: Optional[str], selection: Selection = CONFIGURED, *,
                        phase: Optional[str] = None, row: Optional["EventRow"] = None) -> Tuple[str, ...]:
    """
    Bu spordaki bir maçın tamlık için beklediği dilimler, tablo sırasıyla: seçilmiş olanlardan bu sporda tamlık
    hesabına girenler (`select_slices`, `SliceSpec.counts_in`). selection: CONFIGURED = yapılandırmanın seçimi
    (row verilirse maçı kapsayan takibinki, yoksa sporunki); None = kayıt defterinin varsayılanları.
    """
    chosen = selection_for(selection, sport=sport or None, row=row)
    return tuple(spec.key for spec in select_slices("event", sport or None, chosen, phase=phase)
                 if spec.counts_in(sport or None))


def slice_missing(info: "SliceInfo", threshold: int = DEFAULT_EMPTY_THRESHOLD) -> bool:
    """Dilim eksik mi: `ok` değil ve yeterince kesin "veri yok" yanıtı almamış (Store.events.missing ile aynı)."""
    return info.state != _SLICE_OK and info.empty_count + info.unverified_empty_count < threshold


def missing_slice_keys(state: "EventState", selection: Selection = CONFIGURED, *,
                       threshold: int = DEFAULT_EMPTY_THRESHOLD) -> Tuple[str, ...]:
    """Olay yükü saklanan maçın eksik beklenen dilimleri, tablo sırasıyla. Alt anahtarı olmayan satıra bakılır."""
    row = state.event
    keys = expected_slice_keys(row.sport, selection, phase=phase_of(row.status_class), row=row)
    return tuple(key for key in keys if slice_missing(state.slice(key), threshold))


def wanted_slice_keys(state: "EventState", selection: Selection = CONFIGURED, *,
                      threshold: int = DEFAULT_EMPTY_THRESHOLD) -> Tuple[str, ...]:
    """
    Olay yükü saklanan maçta yeniden istenecek dilimler, tablo sırasıyla: seçilmiş, sporuna uyan ve evresinde
    var olabilen her dilim (tamlık hesabına girmeyenler dahil) `ok` değilse ve yeterince kesin "veri yok"
    yanıtı almamışsa. `missing_slice_keys` bunların tamlık hesabına girenleridir.
    """
    row = state.event
    chosen = selection_for(selection, sport=row.sport or None, row=row)
    specs = select_slices("event", row.sport or None, chosen, phase=phase_of(row.status_class))
    return tuple(spec.key for spec in specs
                 if not is_timed(spec) and slice_missing(state.slice(spec.key), threshold))


# -- zamanlı dilimler (plan maddesi P28): bahis oranları -------------------------------------------------
#
# `max_age`'i olan maç dilimleri (bahis oranları) eksik olmalarına göre değil zamana göre istenir:
#
#   başlamamış maç      dilim hiç okunmadıysa ya da son okuması `max_age`'den eskiyse, maç başlayana kadar her
#                       eşitlemede (başlangıç zamanı geçmişse artık istenmez)
#   bitmiş maç          bir kez daha: son okuma maçın başlangıcından önceyse ya da hiç okunmadıysa
#   oynanıyor, void     hiç (canlı servisin işi; ertelenen ya da iptal edilen maçın oranı beklenmez)
#
# Kesin "veri yok" eşiğine ulaşan dilim (bitmiş maçta iki boş yanıt) artık istenmez. Varsayılan seçimde zamanlı
# dilim yoktur (hepsinin `default_enabled`'ı False): bu kurallar ancak bir seçim oranları adlandırınca çalışır.


# Başlamamış maçın oranları ancak başlangıcına bu kadar kala istenir: sezonun bütün fikstürü her eşitlemede
# istenmesin (bitmemiş maçta "veri yok" yanıtı sayılmaz, yani 404 alan dilim bir sonraki eşitlemede yine istenirdi)
PREMATCH_WINDOW_S = 7 * 86400


def is_timed(spec: Any) -> bool:
    """Maç dilimi zamana göre mi istenir (`max_age`'i var: bahis oranları)."""
    return spec.owner == "event" and spec.max_age is not None


def _provider(selection: Any) -> str:
    return selection.provider if isinstance(selection, SelectionPolicy) else sports_registry.DEFAULT_ODDS_PROVIDER


def _epoch(moment: Any) -> Optional[float]:
    return moment.timestamp() if moment is not None else None


def timed_slice_due(info: "SliceInfo", spec: Any, row: "EventRow", now: float, *,
                    threshold: int = DEFAULT_EMPTY_THRESHOLD) -> bool:
    """Zamanlı bir dilimin (alt anahtarıyla) okunma zamanı geldi mi (yukarıdaki kurallar). Saftır."""
    if info.settled_empty(threshold):
        return False
    fetched = _epoch(info.fetched_at) if info.state == _SLICE_OK or info.has_payload else None
    if row.status_class == StatusClass.NOT_STARTED.value:
        if row.start_ts is None or not now < row.start_ts <= now + PREMATCH_WINDOW_S:
            return False
        return fetched is None or now - fetched >= spec.max_age.total_seconds()
    if row.status_class in _FINISHED_CLASSES:
        return fetched is None or (row.start_ts is not None and fetched < row.start_ts)
    return False


def timed_slices_due(state: "EventState", selection: Selection = CONFIGURED, *, now: float,
                     threshold: int = DEFAULT_EMPTY_THRESHOLD) -> Tuple[Tuple[str, str], ...]:
    """Maçın okunma zamanı gelen zamanlı dilimleri, (anahtar, alt anahtar) çiftleri, tablo sırasıyla."""
    row = state.event
    if row.status_class != StatusClass.NOT_STARTED.value and row.status_class not in _FINISHED_CLASSES:
        return ()
    chosen = selection_for(selection, sport=row.sport or None, row=row)
    provider = _provider(resolve_policy(selection))
    due: List[Tuple[str, str]] = []
    for spec in select_slices("event", row.sport or None, chosen, phase=phase_of(row.status_class)):
        if not is_timed(spec):
            continue
        for sub in spec.sub_keys(provider):
            if timed_slice_due(state.slice(spec.key, sub), spec, row, now, threshold=threshold):
                due.append((spec.key, sub))
    return tuple(due)


def refresh_due(row: "EventRow", policy: RefreshPolicy) -> bool:
    """
    Kayıt geçici ve yenileme zamanı gelmiş mi (Store.events.refresh_candidates'in `status_classes=SETTLED_CLASSES`
    ile koşulu, tek satır için): olay yükü ve gözlemi var, durumu kapanmış (bitmiş ya da void), gözlem
    başlangıçtan `window_s` geçmeden yapılmış ve son gözlemin üzerinden en az `min_interval_s` geçmiş. include_unobserved: gözlemi olmayan kayıt da. window_s <= 0 politikayı kapatır.
    """
    if policy.window_s <= 0 or not row.has_event_payload or row.status_class not in SETTLED_CLASSES:
        return False
    if row.observed_at is None:
        return policy.include_unobserved
    return (row.observed_gap is not None and row.observed_gap < policy.window_s
            and row.observed_at <= policy.now - policy.min_interval_s)


def _is_record(row: "EventRow", layout: Optional[str]) -> bool:
    """Olay yükü saklanan maç; layout verilirse yalnızca o düzende (ve yolu bilinen) kayıt."""
    return row.has_event_payload and (layout is None or (row.layout == layout and bool(row.path)))


def compute_need(state: Optional["EventState"], selection: Selection, policy: RefreshPolicy, *,
                 threshold: int = DEFAULT_EMPTY_THRESHOLD, layout: Optional[str] = None) -> Need:
    """
    Bir maçın ihtiyacı (modül belgesindeki kurallar, öncelik sırasıyla). Saftır: yalnızca argümanlarına bakar.

    state: maçın katalogdaki durumu; None = katalog maçı bilmiyor. selection: dilim seçimi (None = kayıt
    defterinin varsayılanları; SelectionPolicy = maçın satırına göre çözülür; CONFIGURED = etkin ayarların
    politikası). Yalnızca seçilmiş ve bu sporda tamlık hesabına giren dilimler eksik sayılır. policy: yenileme politikası ve an. threshold: bu kadar kesin "veri yok"
    yanıtından sonra dilim artık beklenmez. layout: verilirse yalnızca o düzende saklanan olay yükü kayıt sayılır.
    """
    if state is None:
        return NEED_FULL
    row = state.event
    if not row.has_event_payload:
        if row.status_class in _WAITING_CLASSES:
            # Başlamamış maçın oranları (P28): olay yükü henüz yoksa da yalnızca o dilimler istenir
            return NEED_REFILL if timed_slices_due(state, selection, now=policy.now, threshold=threshold) \
                else NEED_NONE
        return NEED_FULL
    if not _is_record(row, layout):
        return NEED_FULL
    if row.stale:
        return NEED_REFRESH
    if row.status_class not in SETTLED_CLASSES:
        return NEED_REFILL if timed_slices_due(state, selection, now=policy.now, threshold=threshold) \
            else NEED_NONE
    if row.status_class in _FINISHED_CLASSES and (
            missing_slice_keys(state, selection, threshold=threshold)
            or timed_slices_due(state, selection, now=policy.now, threshold=threshold)):
        return NEED_REFILL
    if refresh_due(row, policy):
        return NEED_REFRESH
    return NEED_NONE


def work_item(event_id: int, state: Optional["EventState"], need: str, selection: Selection = CONFIGURED, *,
              threshold: int = DEFAULT_EMPTY_THRESHOLD, now: Optional[float] = None) -> Optional[WorkItem]:
    """
    İhtiyacın iş birimi; `none` için None. `refill`'in dilimleri: bitmiş maçta eksik seçili dilimler, sonra
    zamanı gelen zamanlı dilimler (bahis oranları, alt anahtarlarıyla; `now` verildiyse); bitmemiş maçta yalnızca
    zamanlı dilimler.
    """
    sport = state.event.sport if state is not None else None
    if need == NEED_NONE:
        return None
    if need == NEED_FULL:
        reason = "unknown event" if state is None else (
            "known from a listing only" if not state.event.has_event_payload else "not stored in this layout")
        return WorkItem(Ref.event(event_id), "full", (), sport, reason)
    if need == NEED_REFILL and state is not None:
        finished = state.event.status_class in _FINISHED_CLASSES and state.event.has_event_payload
        missing = wanted_slice_keys(state, selection, threshold=threshold) if finished else ()
        timed = timed_slices_due(state, selection, now=now, threshold=threshold) if now is not None else ()
        pairs = tuple((key, "") for key in missing) + timed
        reason = "missing slices: " + ", ".join(missing) if missing else ""
        if timed:
            reason = "; ".join(filter(None, (reason, "due: " + ", ".join(f"{key}/{sub}" if sub else key
                                                                         for key, sub in timed))))
        return WorkItem(Ref.event(event_id), "refill", pairs, sport, reason)
    if need == NEED_REFRESH:
        reason = ("a newer listing differs from the stored record" if state is not None and state.event.stale
                  else "provisional record due for a refresh")
        return WorkItem(Ref.event(event_id), "refresh", (), sport, reason)
    raise ValueError(f"need must be one of {', '.join(NEEDS)}, got {need!r}")


def season_list_item(tournament_id: int, *, reason: str = "season list") -> WorkItem:
    """Bir turnuvanın sezon listesinin iş birimi (`GET /unique-tournament/{id}/seasons`)."""
    return WorkItem(Ref.tournament(int(tournament_id)), NEED_LISTING, ((LISTING_SEASONS, ""),), None, reason)


def schedule_item(tournament_id: int, season_id: int, *, reason: str = "season schedule") -> WorkItem:
    """Bir sezonun maç programının iş birimi: tur listesi, sonra turlar ya da `events/last` / `events/next` sayfaları."""
    return WorkItem(Ref.season(int(tournament_id), int(season_id)), NEED_LISTING, ((LISTING_SCHEDULE, ""),), None,
                    reason)


# -- maç dışı sahipler (plan maddesi P28) ----------------------------------------------------------------
#
# Sahibi sezon, takım, oyuncu ya da spor olan dilimler (src/sports.py OWNER_SLICES) sahip başına tek bir iş
# biriminde istenir (`need` "owner"), maç başına değil. Sahipler eşitlemenin kapsamından çıkar: kapsamdaki
# sezonlar, o sezonların maçlarında görülen takımlar, takip edilen oyuncular ve sezonların sporları. Seçim sahip
# başına çözülür (`SelectionPolicy.for_owner`: sahibin takibi, yoksa turnuvanın takibi, sonra spor ve
# varsayılanlar). Dilim hiç okunmadıysa istenir; okunduysa `max_age`'den eskiyse ve sahip hâlâ etkinse yeniden.
# Bir sezon etkindir: kapanmamış bir maçı ya da son SEASON_ACTIVE_S içinde başlamış bir maçı varsa; biten bir
# sezonun dilimleri bir kez okunur. Takım, oyuncu ve spor her zaman etkindir.

NEED_OWNER = "owner"
SEASON_ACTIVE_S = 7 * 86400


def owner_slice_due(info: "SliceInfo", spec: Any, now: float, *, active: bool,
                    threshold: int = DEFAULT_EMPTY_THRESHOLD) -> bool:
    """Maç dışı bir dilimin okunma zamanı geldi mi (yukarıdaki kural). Saftır."""
    if info.settled_empty(threshold):
        return False
    fetched = _epoch(info.fetched_at) if info.state == _SLICE_OK or info.has_payload else None
    if fetched is None:
        return True
    return active and spec.max_age is not None and now - fetched >= spec.max_age.total_seconds()


def owner_item(ref: Ref, specs: Iterable[Any], infos: Iterable["SliceInfo"], now: float, *, sport: Optional[str],
               provider: str = sports_registry.DEFAULT_ODDS_PROVIDER, active: bool = True,
               threshold: int = DEFAULT_EMPTY_THRESHOLD) -> Optional[WorkItem]:
    """Bir sahibin iş birimi: seçilmiş dilimlerinden okunma zamanı gelenler; hiçbiri yoksa None. Saftır."""
    known = {(info.key, info.sub): info for info in infos}
    due: List[Tuple[str, str]] = []
    for spec in specs:
        for sub in spec.sub_keys(provider):
            info = known.get((spec.key, sub))  # satırı yok: hiç istenmedi
            if info is None or owner_slice_due(info, spec, now, active=active, threshold=threshold):
                due.append((spec.key, sub))
    if not due:
        return None
    return WorkItem(ref, NEED_OWNER, tuple(due), sport,
                    f"{ref.kind} data due: " + ", ".join(f"{key}/{sub}" if sub else key for key, sub in due))


def _all_selections(selection: Any) -> Tuple[Any, ...]:
    return selection.selections() if isinstance(selection, SelectionPolicy) else (selection,)


def _selects(selection: Any, owners: Iterable[str], keep: Any) -> bool:
    """Seçimlerden biri sahibi `owners`'tan olan ve `keep`'e uyan bir dilim seçiyor mu (spora bakılmadan)."""
    owners = tuple(owners)
    return any(spec.owner in owners and keep(spec)
               for picked in _all_selections(selection) for spec in sports_registry.chosen_slices(picked))


def extras_selected(selection: Selection) -> bool:
    """
    Seçim zamanlı bir maç dilimini (bahis oranları) ya da maç dışı bir dilimi seçiyor mu: hayırsa eşitlemenin
    P28 aşaması hiçbir şey okumaz ve istemez. CONFIGURED burada yalnızca ayarların politikasıdır.
    """
    selection = resolve_policy(selection)
    return _selects(selection, ("event",), is_timed) or _selects(
        selection, (owner for owner in sports_registry.OWNERS if owner != "event"), lambda spec: True)


def _season_active(store: "Store", season_id: int, now: float) -> bool:
    from src.store import EventQuery

    scope = Scope(season_ids=(season_id,))
    open_classes = tuple(value.value for value in StatusClass if value.value not in SETTLED_CLASSES)
    return (store.events.count(EventQuery(scope=scope, status_classes=open_classes)) > 0
            or store.events.count(EventQuery(scope=scope, start_from=now - SEASON_ACTIVE_S)) > 0)


def owner_items(store: "Store", policy: RefreshPolicy, *, seasons: Sequence[Tuple[int, int]] = (),
                selection: Selection = CONFIGURED, threshold: int = DEFAULT_EMPTY_THRESHOLD) -> List[WorkItem]:
    """
    Maç dışı sahiplerin iş birimleri, sahip başına bir tane: önce sezonlar (verildiği sırayla), sonra o
    sezonların maçlarındaki takımlar (kimlik sırasıyla), takip edilen oyuncular ve sezonların sporları.
    seasons: (turnuva, sezon) çiftleri. Hiçbir maç dışı dilim seçilmemişse depoya sormadan boş liste.
    """
    chosen = resolve_policy(selection, store)
    if not _selects(chosen, (owner for owner in sports_registry.OWNERS if owner != "event"), lambda spec: True):
        return []
    provider = _provider(chosen)
    now = policy.now

    def owner_selection(kind: str, owner_id: int, sport: Optional[str], tournament_id: Optional[int] = None) -> Any:
        if isinstance(chosen, SelectionPolicy):
            return chosen.for_owner(kind, owner_id, sport, tournament_id=tournament_id)
        return chosen

    def add(ref: Ref, specs: Tuple[Any, ...], sport: Optional[str], active: bool = True) -> None:
        if not specs:
            return
        item = owner_item(ref, specs, store.entities.slices(ref), now, sport=sport, provider=provider,
                          active=active, threshold=threshold)
        if item is not None:
            items.append(item)

    items: List[WorkItem] = []
    team_follows = isinstance(chosen, SelectionPolicy) and any(kind == "team" for kind, _ in chosen.follows)
    teams: Dict[int, Tuple[Optional[str], int]] = {}
    sports_seen: Dict[str, None] = {}
    for tournament_id, season_id in dict.fromkeys((int(t), int(s)) for t, s in seasons):
        sport = store.entities.sport_of_tournament(tournament_id)
        if sport:
            sports_seen[sport] = None
        specs = select_slices("season", sport, owner_selection("season", season_id, sport, tournament_id))
        if specs:
            add(Ref.season(tournament_id, season_id), specs, sport, _season_active(store, season_id, now))
        if team_follows or select_slices("team", sport, owner_selection("team", 0, sport, tournament_id)):
            for state in store.events.states(Scope(season_ids=(season_id,))):
                for team in (state.event.home_id, state.event.away_id):
                    if team is not None:
                        teams.setdefault(int(team), (sport, tournament_id))
    for team_id in sorted(teams):
        sport, tournament_id = teams[team_id]
        add(Ref.team(team_id), select_slices("team", sport, owner_selection("team", team_id, sport, tournament_id)),
            sport)
    for follow in _follow_rows(store):
        if follow.kind == "player":
            sport = follow.sport or None
            player_id = int(follow.entity_id)
            add(Ref.player(player_id), select_slices("player", sport, owner_selection("player", player_id, sport)),
                sport)
    for sport in sports_seen:
        specs = select_slices("sport", sport, owner_selection("sport", 0, sport))
        found = store.entities.sport(sport) if specs else None
        if found is not None and found.id is not None:
            add(Ref.sport(int(found.id)), specs, sport)
    return items


def prematch_items(store: "Store", policy: RefreshPolicy, *, tournament_ids: Sequence[int] = (),
                   selection: Selection = CONFIGURED, threshold: int = DEFAULT_EMPTY_THRESHOLD) -> List[WorkItem]:
    """
    Başlamamış maçların zamanlı dilimlerinin (bahis oranları) iş birimleri: kapsamdaki turnuvalarda başlangıcına
    PREMATCH_WINDOW_S'ten az kalan maçlar, kimlik sırasıyla (`refill`, yalnızca o dilimler). Zamanlı dilim
    seçilmemişse depoya sormadan boş liste.
    """
    chosen = resolve_policy(selection, store)
    if not _selects(chosen, ("event",), is_timed):
        return []
    from src.store import EventQuery

    query = EventQuery(scope=Scope(tournament_ids=tuple(tournament_ids)),
                       status_classes=(StatusClass.NOT_STARTED.value,), start_from=policy.now,
                       start_to=policy.now + PREMATCH_WINDOW_S)
    ids = sorted({row.id for row in store.events.iter(query)})
    items: List[WorkItem] = []
    for chunk in _chunks(ids):
        for state in store.events.states(Scope(event_ids=tuple(chunk))):
            if state.event.stale:
                continue
            due = timed_slices_due(state, chosen, now=policy.now, threshold=threshold)
            if due:
                items.append(WorkItem(Ref.event(state.event.id), "refill", due, state.event.sport or None,
                                      "pre-match: " + ", ".join(f"{key}/{sub}" if sub else key for key, sub in due)))
    return items


def order_by_need(ids: Sequence[Any], needs: Dict[str, str]) -> Tuple[List[Any], int]:
    """
    İşlenecek kimlikler: önce full ve refill (verildiği sırayla), sonra refresh; `none` düşer. İkinci değer
    yenilenecek maç sayısı. needs: str(kimlik) → ihtiyaç.
    """
    first: List[Any] = []
    refresh: List[Any] = []
    for value in ids:
        need = needs[str(value)]
        if need == NEED_REFRESH:
            refresh.append(value)
        elif need != NEED_NONE:
            first.append(value)
    return first + refresh, len(refresh)


# -- depodan okuyan sarmalayıcılar ---------------------------------------------------------------------

def event_needs(store: "Store", event_ids: Iterable[Any], policy: RefreshPolicy, *, selection: Selection = CONFIGURED,
                threshold: int = DEFAULT_EMPTY_THRESHOLD, layout: Optional[str] = None) -> Dict[int, str]:
    """
    Her maçın ihtiyacı, kimlik → ihtiyaç (`compute_need`); kimlik olamayan değerler sonuçta yer almaz. Durumlar
    500'lük parçalarla okunur (`Store.events.states`: parça başına iki sorgu); hiçbir yük okunmaz. selection:
    CONFIGURED = yapılandırmanın ve takip tablosunun seçimi (`configured_policy`), maç maç çözülür.
    """
    selection = resolve_policy(selection, store)
    needs: Dict[int, str] = {}
    for chunk in _chunks(_event_ids(event_ids)):
        states = {state.event.id: state for state in store.events.states(Scope(event_ids=tuple(chunk)))}
        for event_id in chunk:
            needs[event_id] = compute_need(states.get(event_id), selection, policy, threshold=threshold, layout=layout)
    return needs


def refresh_due_events(store: "Store", policy: RefreshPolicy, *, tournament_ids: Sequence[int] = (),
                       selection: Selection = CONFIGURED, threshold: int = DEFAULT_EMPTY_THRESHOLD,
                       layout: Optional[str] = None) -> List["EventRow"]:
    """
    İhtiyacı `refresh` olan kayıtlar: önce daha yeni bir listenin çeliştiği (bayat) kayıtlar
    (`Store.events.stale`), sonra kapanmış durumdaki (`SETTLED_CLASSES`) geçici kayıtlardan zamanı gelenler
    (`Store.events.refresh_candidates`; dizinli sorgu); iki grup da kimlik sırasıyla (docs/design/01-storage.md
    8.2 kural 3 ve 8.3). Karar `compute_need`'den. tournament_ids: boş = süzgeç yok; maçın turnuvasına bakılır.
    """
    selection = resolve_policy(selection, store)
    scope = Scope(tournament_ids=tuple(tournament_ids))
    stale = store.events.stale(scope)
    due = store.events.refresh_candidates(
        now=policy.now, window_s=policy.window_s, min_interval_s=policy.min_interval_s, scope=scope,
        status_classes=SETTLED_CLASSES, include_unobserved=policy.include_unobserved)
    rows: Dict[int, "EventRow"] = {}
    for chunk in _chunks(sorted(set(stale) | set(due))):
        for state in store.events.states(Scope(event_ids=tuple(chunk))):
            if compute_need(state, selection, policy, threshold=threshold, layout=layout) == NEED_REFRESH:
                rows[state.event.id] = state.event
    return sorted(rows.values(), key=lambda row: (not row.stale, row.id))


def _canonical(value: Any) -> Optional[int]:
    """Kimlik olarak yazılabilen değer → sayı (`_event_ids`'in kuralı); değilse None."""
    found = _event_ids([value])
    return found[0] if found else None


def plan_items(store: "Store", event_ids: Iterable[Any], policy: RefreshPolicy, *, selection: Selection = CONFIGURED,
               threshold: int = DEFAULT_EMPTY_THRESHOLD, layout: Optional[str] = None,
               needs: Optional[Dict[int, str]] = None) -> List[WorkItem]:
    """
    Maç kimliklerinin iş birimleri, `order_by_need` sırasıyla (önce full ve refill verildiği sırayla, sonra
    refresh); ihtiyacı `none` olanlar düşer. Durumlar `event_needs` gibi parça parça okunur. needs verilirse
    (kimlik → ihtiyaç; ör. işin önbelleğinden) karar o olur, durum yalnızca refill dilimleri için okunur.
    """
    selection = resolve_policy(selection, store)
    wanted = set(_event_ids(event_ids))
    ids = [number for number in dict.fromkeys(_canonical(value) for value in event_ids) if number in wanted]
    states: Dict[int, "EventState"] = {}
    for chunk in _chunks(sorted(ids)):
        states.update((state.event.id, state) for state in store.events.states(Scope(event_ids=tuple(chunk))))
    decided: Dict[str, str] = {}
    for event_id in ids:
        given = needs.get(event_id) if needs is not None else None
        decided[str(event_id)] = given if given is not None else compute_need(
            states.get(event_id), selection, policy, threshold=threshold, layout=layout)
    ordered, _ = order_by_need(ids, decided)
    items: List[WorkItem] = []
    for event_id in ordered:
        state = states.get(event_id)
        need = decided[str(event_id)]
        if need == NEED_REFILL and state is None:
            need = NEED_FULL  # önbellekteki karar eskimiş: kayıt artık yok
        item = work_item(event_id, state, need, selection, threshold=threshold, now=policy.now)
        if item is not None:
            items.append(item)
    return items


__all__ = ["CONFIGURED", "LISTING_SCHEDULE", "LISTING_SEASONS", "NEEDS", "NEED_LISTING", "Need", "SETTLED_CLASSES",
           "Selection", "SelectionPolicy", "WorkItem", "configured_policy", "resolve_policy", "selection_for", "WorkNeed", "compute_need", "event_needs", "expected_slice_keys", "missing_slice_keys", "order_by_need",
           "phase_of", "plan_items", "refresh_due", "refresh_due_events", "schedule_item", "season_list_item",
           "slice_missing", "wanted_slice_keys", "work_item",
           "NEED_OWNER", "PREMATCH_WINDOW_S", "SEASON_ACTIVE_S", "extras_selected", "is_timed", "owner_item",
           "owner_items", "owner_slice_due", "prematch_items", "timed_slice_due", "timed_slices_due"]
