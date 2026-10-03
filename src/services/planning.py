"""
Planlama: bir maçın neye ihtiyacı olduğu (docs/design/02-services.md 3.2).

Kural tek yerdedir ve saftır: `compute_need` bir maçın katalogdaki durumuna (`EventState`: maç satırı ve dilim
satırları), dilim seçimine ve yenileme politikasına bakar; dosya okumaz, depoya sormaz, saate bakmaz (an
politikadadır). Depodan durumları okuyan sarmalayıcılar (`event_needs`, `refresh_due_events`) ve sıralama
(`order_by_need`) da buradadır; src/match_data_fetcher.py'deki planlayıcılar bunlara devreder.

İhtiyaçlar, öncelik sırasıyla (tasarım tablosu; ilk uyan kural kazanır):

  full     kayıt yok: maç katalogda yok ya da olay yükü yok (yalnızca bir listeden biliniyor)
  refresh  daha yeni bir liste kaydı bayatlamış saydı (`stale`: durum, skor ya da başlangıç zamanı değişmiş);
           önce /event yeniden okunur (eksik dilimler bir sonraki planda)
  none     maç oynanıyor (canlı): canlı servisin işidir
  none     maç başlamamış ya da ertelenmiş / iptal ve seçimde ön maç evresinde var olabilen dilim yok: listeler
           kaydı güncel tutar. Kayıt defterinin bugünkü dilimleri her evrede istenebildiği için (P12'nin
           varsayılanları) bu kural varsayılan seçimle uygulanmaz
  refill   olay yükü var; seçilmiş, maçın sporuna uyan, evresinde var olabilen ve tamlık hesabına giren bir
           dilim eksik: satırı yok ya da `ok` değil ve kesin + doğrulanmamış "veri yok" sayısı eşiğin altında
  refresh  dilimler tam, kayıt geçici ve yenileme zamanı gelmiş (src/refresh.py; Store.events.refresh_candidates
           ile aynı koşul)
  none     tamam

`refill` iş biriminin dilimleri, maçın eksik olan bütün seçili dilimleridir: tamlık hesabına girmeyen (isteğe
bağlı) dilimler de (ör. tenisin point_by_point'i), maç zaten yeniden okunuyorsa birlikte istenir.

Liste iş birimleri (`listing`, plan maddesi P14): bir turnuvanın sezon listesi (`season_list_item`: sahibi
turnuva, dilimi `seasons`) ve bir sezonun maç programı (`schedule_item`: sahibi sezon, dilimi `schedule`; tur
ya da olay sayfaları). Yürütülmeleri src/services/listing.py'dedir.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple, Union

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
from src.sports import SliceSelection, select_slices
from src.status import StatusClass
from src.store import Ref, Scope

if TYPE_CHECKING:
    from src.store import EventRow, EventState, SliceInfo, Store

Need = Literal["full", "refill", "refresh", "none"]
WorkNeed = Literal["listing", "full", "refill", "refresh", "owner"]
Selection = Union[SliceSelection, Iterable[str], None]

NEEDS: Tuple[str, ...] = (NEED_FULL, NEED_REFILL, NEED_REFRESH, NEED_NONE)
NEED_LISTING = "listing"

# Liste iş birimlerinin dilim anahtarları (Store'daki adlarıyla: src/store/entities.py KEY_SEASONS, KEY_SCHEDULE)
LISTING_SEASONS = "seasons"
LISTING_SCHEDULE = "schedule"
_SLICE_OK = "ok"

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


def expected_slice_keys(sport: Optional[str], selection: Selection = None, *,
                        phase: Optional[str] = None) -> Tuple[str, ...]:
    """
    Bu spordaki bir maçın tamlık için beklediği dilimler, tablo sırasıyla (`select_slices`, bu sporda tamlık
    hesabına girenler: `SliceSpec.counts_in`).
    """
    return tuple(spec.key for spec in select_slices("event", sport or None, selection, phase=phase)
                 if spec.counts_in(sport or None))


def slice_missing(info: "SliceInfo", threshold: int = DEFAULT_EMPTY_THRESHOLD) -> bool:
    """Dilim eksik mi: `ok` değil ve yeterince kesin "veri yok" yanıtı almamış (Store.events.missing ile aynı)."""
    return info.state != _SLICE_OK and info.empty_count + info.unverified_empty_count < threshold


def missing_slice_keys(state: "EventState", selection: Selection = None, *,
                       threshold: int = DEFAULT_EMPTY_THRESHOLD) -> Tuple[str, ...]:
    """Olay yükü saklanan maçın eksik beklenen dilimleri, tablo sırasıyla. Alt anahtarı olmayan satıra bakılır."""
    row = state.event
    keys = expected_slice_keys(row.sport, selection, phase=phase_of(row.status_class))
    return tuple(key for key in keys if slice_missing(state.slice(key), threshold))


def wanted_slice_keys(state: "EventState", selection: Selection = None, *,
                      threshold: int = DEFAULT_EMPTY_THRESHOLD) -> Tuple[str, ...]:
    """
    Olay yükü saklanan maçta yeniden istenecek dilimler, tablo sırasıyla: seçilmiş, sporuna uyan ve evresinde
    var olabilen her dilim (tamlık hesabına girmeyenler dahil) `ok` değilse ve yeterince kesin "veri yok"
    yanıtı almamışsa. `missing_slice_keys` bunların tamlık hesabına girenleridir.
    """
    row = state.event
    specs = select_slices("event", row.sport or None, selection, phase=phase_of(row.status_class))
    return tuple(spec.key for spec in specs if slice_missing(state.slice(spec.key), threshold))


def refresh_due(row: "EventRow", policy: RefreshPolicy) -> bool:
    """
    Kayıt geçici ve yenileme zamanı gelmiş mi (Store.events.refresh_candidates'in koşulu, tek satır için): olay
    yükü ve gözlemi var, gözlem başlangıçtan `window_s` geçmeden yapılmış ve son gözlemin üzerinden en az
    `min_interval_s` geçmiş. include_unobserved: gözlemi olmayan kayıt da. window_s <= 0 politikayı kapatır.
    """
    if policy.window_s <= 0 or not row.has_event_payload:
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
    defterinin varsayılanları). policy: yenileme politikası ve an. threshold: bu kadar kesin "veri yok"
    yanıtından sonra dilim artık beklenmez. layout: verilirse yalnızca o düzende saklanan olay yükü kayıt sayılır.
    """
    if state is None or not _is_record(state.event, layout):
        return NEED_FULL
    row = state.event
    if row.stale:
        return NEED_REFRESH
    phase = phase_of(row.status_class)
    if phase == "live":
        return NEED_NONE
    if phase == "pre" and not select_slices("event", row.sport or None, selection, phase="pre"):
        return NEED_NONE
    if missing_slice_keys(state, selection, threshold=threshold):
        return NEED_REFILL
    if refresh_due(row, policy):
        return NEED_REFRESH
    return NEED_NONE


def work_item(event_id: int, state: Optional["EventState"], need: str, selection: Selection = None, *,
              threshold: int = DEFAULT_EMPTY_THRESHOLD) -> Optional[WorkItem]:
    """İhtiyacın iş birimi; `none` için None."""
    sport = state.event.sport if state is not None else None
    if need == NEED_NONE:
        return None
    if need == NEED_FULL:
        reason = "unknown event" if state is None else (
            "known from a listing only" if not state.event.has_event_payload else "not stored in this layout")
        return WorkItem(Ref.event(event_id), "full", (), sport, reason)
    if need == NEED_REFILL and state is not None:
        missing = wanted_slice_keys(state, selection, threshold=threshold)
        return WorkItem(Ref.event(event_id), "refill", tuple((key, "") for key in missing), sport,
                        "missing slices: " + ", ".join(missing))
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

def event_needs(store: "Store", event_ids: Iterable[Any], policy: RefreshPolicy, *, selection: Selection = None,
                threshold: int = DEFAULT_EMPTY_THRESHOLD, layout: Optional[str] = None) -> Dict[int, str]:
    """
    Her maçın ihtiyacı, kimlik → ihtiyaç (`compute_need`); kimlik olamayan değerler sonuçta yer almaz. Durumlar
    500'lük parçalarla okunur (`Store.events.states`: parça başına iki sorgu); hiçbir yük okunmaz.
    """
    needs: Dict[int, str] = {}
    for chunk in _chunks(_event_ids(event_ids)):
        states = {state.event.id: state for state in store.events.states(Scope(event_ids=tuple(chunk)))}
        for event_id in chunk:
            needs[event_id] = compute_need(states.get(event_id), selection, policy, threshold=threshold, layout=layout)
    return needs


def refresh_due_events(store: "Store", policy: RefreshPolicy, *, tournament_ids: Sequence[int] = (),
                       selection: Selection = None, threshold: int = DEFAULT_EMPTY_THRESHOLD,
                       layout: Optional[str] = None) -> List["EventRow"]:
    """
    İhtiyacı `refresh` olan kayıtlar, kimlik sırasıyla. Adaylar katalogdan (Store.events.refresh_candidates;
    dizinli sorgu), karar `compute_need`'den. tournament_ids: boş = süzgeç yok; maçın turnuvasına bakılır.
    """
    candidates = store.events.refresh_candidates(
        now=policy.now, window_s=policy.window_s, min_interval_s=policy.min_interval_s,
        scope=Scope(tournament_ids=tuple(tournament_ids)), include_unobserved=policy.include_unobserved)
    rows: List["EventRow"] = []
    for chunk in _chunks(candidates):
        for state in store.events.states(Scope(event_ids=tuple(chunk))):
            if compute_need(state, selection, policy, threshold=threshold, layout=layout) == NEED_REFRESH:
                rows.append(state.event)
    return sorted(rows, key=lambda row: row.id)


def _canonical(value: Any) -> Optional[int]:
    """Kimlik olarak yazılabilen değer → sayı (`_event_ids`'in kuralı); değilse None."""
    found = _event_ids([value])
    return found[0] if found else None


def plan_items(store: "Store", event_ids: Iterable[Any], policy: RefreshPolicy, *, selection: Selection = None,
               threshold: int = DEFAULT_EMPTY_THRESHOLD, layout: Optional[str] = None,
               needs: Optional[Dict[int, str]] = None) -> List[WorkItem]:
    """
    Maç kimliklerinin iş birimleri, `order_by_need` sırasıyla (önce full ve refill verildiği sırayla, sonra
    refresh); ihtiyacı `none` olanlar düşer. Durumlar `event_needs` gibi parça parça okunur. needs verilirse
    (kimlik → ihtiyaç; ör. işin önbelleğinden) karar o olur, durum yalnızca refill dilimleri için okunur.
    """
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
        item = work_item(event_id, state, need, selection, threshold=threshold)
        if item is not None:
            items.append(item)
    return items


__all__ = ["LISTING_SCHEDULE", "LISTING_SEASONS", "NEEDS", "NEED_LISTING", "Need", "Selection", "WorkItem",
           "WorkNeed", "compute_need", "event_needs", "expected_slice_keys", "missing_slice_keys", "order_by_need",
           "phase_of", "plan_items", "refresh_due", "refresh_due_events", "schedule_item", "season_list_item",
           "slice_missing", "wanted_slice_keys", "work_item"]
