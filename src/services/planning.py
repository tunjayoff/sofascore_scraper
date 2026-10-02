"""
Planlama: bir maçın neye ihtiyacı olduğu (docs/design/02-services.md 3.2).

Kural tek yerdedir ve saftır: `compute_need` bir maçın katalogdaki durumuna (`EventState`: maç satırı ve dilim
satırları), dilim seçimine ve yenileme politikasına bakar; dosya okumaz, depoya sormaz, saate bakmaz (an
politikadadır). Depodan durumları okuyan sarmalayıcılar (`event_needs`, `refresh_due_events`) ve sıralama
(`order_by_need`) da buradadır; src/match_data_fetcher.py'deki planlayıcılar bunlara devreder.

İhtiyaçlar (bugünkü kural; RD-3'ün `QueryService.detail_needs` ile aynı kararlar):

  full     kayıt yok: maç katalogda yok ya da olay yükü yok (yalnızca bir listeden biliniyor)
  refill   olay yükü var; seçilmiş, maçın sporuna uyan, evresinde var olabilen ve tamlık hesabına giren bir
           dilim eksik: satırı yok ya da `ok` değil ve kesin + doğrulanmamış "veri yok" sayısı eşiğin altında
  refresh  dilimler tam, kayıt geçici ve yenileme zamanı gelmiş (src/refresh.py; Store.events.refresh_candidates
           ile aynı koşul)
  none     tamam

Tasarım tablosunun üç satırı henüz uygulanmaz, çünkü bugünkü davranışı değiştirirler (P13 ve dalga 4):
bayatlamış (`stale`) kaydın önce yenilenmesi, canlı maçın canlı servise bırakılması ve başlamamış maçın ön maç
dilimi seçilmemişse beklenmemesi. Bugün bu maçlar da yukarıdaki dört kurala göre planlanır.
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
    """Bu spordaki bir maçın tamlık için beklediği dilimler, tablo sırasıyla (`select_slices`, `required` olanlar)."""
    return tuple(spec.key for spec in select_slices("event", sport or None, selection, phase=phase)
                 if spec.counts_for_completeness)


def slice_missing(info: "SliceInfo", threshold: int = DEFAULT_EMPTY_THRESHOLD) -> bool:
    """Dilim eksik mi: `ok` değil ve yeterince kesin "veri yok" yanıtı almamış (Store.events.missing ile aynı)."""
    return info.state != _SLICE_OK and info.empty_count + info.unverified_empty_count < threshold


def missing_slice_keys(state: "EventState", selection: Selection = None, *,
                       threshold: int = DEFAULT_EMPTY_THRESHOLD) -> Tuple[str, ...]:
    """Olay yükü saklanan maçın eksik beklenen dilimleri, tablo sırasıyla. Alt anahtarı olmayan satıra bakılır."""
    row = state.event
    keys = expected_slice_keys(row.sport, selection, phase=phase_of(row.status_class))
    return tuple(key for key in keys if slice_missing(state.slice(key), threshold))


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
    Bir maçın ihtiyacı (modül belgesindeki dört kural). Saftır: yalnızca argümanlarına bakar.

    state: maçın katalogdaki durumu; None = katalog maçı bilmiyor. selection: dilim seçimi (None = kayıt
    defterinin varsayılanları). policy: yenileme politikası ve an. threshold: bu kadar kesin "veri yok"
    yanıtından sonra dilim artık beklenmez. layout: verilirse yalnızca o düzende saklanan olay yükü kayıt sayılır.
    """
    if state is None or not _is_record(state.event, layout):
        return NEED_FULL
    if missing_slice_keys(state, selection, threshold=threshold):
        return NEED_REFILL
    if refresh_due(state.event, policy):
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
        missing = missing_slice_keys(state, selection, threshold=threshold)
        return WorkItem(Ref.event(event_id), "refill", tuple((key, "") for key in missing), sport,
                        "missing slices: " + ", ".join(missing))
    if need == NEED_REFRESH:
        return WorkItem(Ref.event(event_id), "refresh", (), sport, "provisional record due for a refresh")
    raise ValueError(f"need must be one of {', '.join(NEEDS)}, got {need!r}")


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


__all__ = ["NEEDS", "Need", "Selection", "WorkItem", "WorkNeed", "compute_need", "event_needs",
           "expected_slice_keys", "missing_slice_keys", "order_by_need", "phase_of", "refresh_due",
           "refresh_due_events", "slice_missing", "work_item"]
