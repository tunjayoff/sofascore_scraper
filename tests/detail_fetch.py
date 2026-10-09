"""
Testlerin tek maç yolları: 2.x'in MatchDataFetcher'ında yalnızca testlerin çağırdığı giriş noktaları (tek maç,
refill, tek maç yenileme, saklanan detayın eski sözlük biçimi, bir maçın ihtiyacı, v3 yazıcısı), getirme boru
hattıyla (sofascore_scraper/services/pipeline.py) ve Store'la. Sınıf 3.1'de kalktı (plan maddeleri FX-15 ve P30);
ürünün detay aşaması sofascore_scraper/services/detail_phase.py'dedir.

`Details` bir veri dizininin deposuyla çalışır; her çağrı katalogdan okur, önbellek tutmaz. `save_match_data`
eski `_save_match_data`'dır: bir maçı v3 düzenine `Store.events.put` ile yazar (testlerin kayıt kurma aracı;
eski düzenin yazıcısı tests/legacy_writer.py'dedir).
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from sofascore_scraper.services import pipeline, planning
from sofascore_scraper.services.detail_phase import UNAVAILABLE_AFTER_ATTEMPTS, canonical_id
from sofascore_scraper.services.planning import WorkItem
from sofascore_scraper.services.query import QueryService, RefreshPolicy
from sofascore_scraper.slices import SLICE_EMPTY, SLICE_OK, SliceOutcome, match_detail_slice_present
from sofascore_scraper.sports import DETAIL_SLICES, event_sport_slug, slices_for
from sofascore_scraper.status import OBSERVATION_KEY, observation_record
from sofascore_scraper.store import EventRow, Ref, Scope, Store, open_store

_LEGACY_LAYOUT = "legacy"

# Eski maç detayı sözlüğündeki dilimler, sırasıyla: dilim tablosunun spora bağlı olmayan `required` satırları
LEGACY_DETAIL_KEYS: Tuple[str, ...] = tuple(d.key for d in DETAIL_SLICES if d.required and d.sports is None)


@dataclass
class SingleFetchReport:
    """Tek maç çekiminde SofaScore'a giden isteklerin sonucu (/event ve bu çağrıda istenen dilimler)."""

    event: Optional[SliceOutcome] = None
    slices: Dict[str, SliceOutcome] = field(default_factory=dict)

    def upstream_failure(self) -> Optional[SliceOutcome]:
        """Çekimi SofaScore tarafı engellediyse o başarısız sonuç (`pipeline.upstream_failure`), yoksa None."""
        return pipeline.upstream_failure(self.event, self.slices)


def _parse_utc(value: Any) -> Optional[dt.datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=dt.timezone.utc)


def _stored_observation(row: EventRow) -> Optional[Dict[str, Any]]:
    """Katalog satırından gözlemin karşılığı; kayıt gözlemsizse None (`status_regressed` yalnızca doğruysa)."""
    observed: Optional[str] = None
    if row.observed_at is not None:
        try:
            observed = dt.datetime.fromtimestamp(row.observed_at, dt.timezone.utc).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            observed = None
    if observed is None and not row.status_regressed:
        return None
    observation: Dict[str, Any] = {"observed_at_utc": observed, "change_ts": row.change_ts}
    if row.status_regressed:
        observation["status_regressed"] = True
    return observation


def slice_outcomes(match_data: Mapping[str, Any], outcomes: Mapping[str, SliceOutcome],
                   counted: Tuple[str, ...]) -> Tuple[Dict[str, SliceOutcome], Tuple[str, ...]]:
    """
    `match_data`'daki dilimlerin `put` sonuçları ve "yok" yanıtı sayılacak dilimler (eski `_slice_outcomes`):
    verisi olan dilim `ok`; sayaçları tutulan dilim sonucu verilmişse o sonuç; öteki gövdeli dilim sayılmadan `empty`.
    """
    found: Dict[str, SliceOutcome] = {}
    counts: List[str] = []
    for key, data in match_data.items():
        if key in ("basic", OBSERVATION_KEY):
            continue
        outcome = outcomes.get(key)
        if data is not None and match_detail_slice_present(key, dict(match_data)):
            found[key] = SliceOutcome(SLICE_OK, data=data)
        elif key in counted and outcome is not None:
            if outcome.status != SLICE_EMPTY:
                found[key] = outcome
            else:
                found[key] = SliceOutcome(SLICE_EMPTY, data=data, reason=outcome.reason,
                                          http_status=outcome.http_status)
                counts.append(key)
        elif data is not None:
            found[key] = SliceOutcome(SLICE_EMPTY, data=data, reason="empty")
    return found, tuple(counts)


def save_match_data(store: Store, match_id: Any, match_data: Mapping[str, Any],
                    outcomes: Optional[Mapping[str, SliceOutcome]] = None) -> Any:
    """
    Maçı v3 düzenine yazar (`Store.events.put`; eski düzende duran maç önce yükseltilir). match_data: `basic`
    (/event yükü), varsa gözlem ve dilimler. outcomes: bu kayıtta istenen dilimlerin sonuçları (bitmiş maçta sporun
    `required` dilimlerinin kesin "yok" yanıtları sayılır). Kurallı olmayan kimlikte StorageError. PutResult döner.
    """
    from sofascore_scraper.exceptions import StorageError

    mid = str(match_id)
    basic = match_data.get("basic") or {}
    event_id = canonical_id(mid)
    if event_id is None:
        raise StorageError(f"Match id is not a canonical event id: {mid!r}", detail="invalid match id")
    sport = event_sport_slug(dict(basic)) or ""
    counted = tuple(d.key for d in slices_for(sport, required_only=True)) if pipeline.is_finished(basic) else ()
    observation = match_data.get(OBSERVATION_KEY)
    observed_at = _parse_utc(observation.get("observed_at_utc")) if isinstance(observation, dict) else None
    found, counts = slice_outcomes(match_data, outcomes or {}, counted)
    put: Dict[str, SliceOutcome] = {}
    if basic:
        put["event"] = SliceOutcome(SLICE_OK, data=basic, fetched_at=observed_at)
    put.update(found)
    return pipeline.put_retrying(store, event_id, "the match details",
                                 lambda: store.events.put(event_id, put, count_empties=counts if counts else False))


def match_data_of(result: pipeline.ItemResult) -> Dict[str, Any]:
    """Boru hattı sonucunun eski sözlük biçimi: `basic`, gözlem (yanıtın alındığı an) ve istenen dilimler."""
    payload = result.payload or {}
    fetched_at = result.event.fetched_at if result.event is not None else None
    data: Dict[str, Any] = {"basic": payload, OBSERVATION_KEY: observation_record(payload, fetched_at)}
    data.update((key, outcome.data) for key, outcome in result.slices.items())
    return data


def legacy_detail(store: Store, event_id: int) -> Optional[Dict[str, Any]]:
    """
    Bir maçın saklanan detayı, 2.x'in `GET /api/matches/{id}` biçiminde (eski `QueryService.match_detail_legacy`):
    önce `basic` (/event yükü), ardından yükü olan eski dilimler, tablo sırasıyla; içeriği JSON `null` olan dilim
    None değeriyle yer alır. Maç bilinmiyorsa ya da olay yükü yoksa None. Katalog "yük var" derken okunamayan
    dilim dosyası yalnızca o dilimi düşürür.
    """
    from sofascore_scraper.store import PayloadCorrupt, PayloadMissing

    if not -(2 ** 63) <= event_id <= 2 ** 63 - 1:
        return None
    wanted = ("event", *LEGACY_DETAIL_KEYS)
    try:
        found = store.events.payloads(event_id, wanted)
    except (PayloadMissing, PayloadCorrupt):
        found = {}
        for key in wanted:
            try:
                found.update(store.events.payloads(event_id, (key,)))
            except (PayloadMissing, PayloadCorrupt):
                pass
    if "event" not in found:
        return None
    result: Dict[str, Any] = {"basic": found["event"]}
    result.update((key, found[key]) for key in LEGACY_DETAIL_KEYS if key in found)
    return result


class Details:
    """Bir veri dizininin tek maç yolları (boru hattıyla). data_dir ya da store verilir; depo ilk kullanımda açılır."""

    def __init__(self, data_dir: Union[str, os.PathLike, None] = None, *, store: Optional[Store] = None) -> None:
        if store is None and data_dir is None:
            raise ValueError("data_dir or store is required")
        self._store = store
        self.data_dir = str(store.data_dir) if store is not None else os.fspath(data_dir)  # type: ignore[arg-type]

    @property
    def store(self) -> Store:
        """Veri dizininin deposu; ilk kullanımda açılır (kurulmadan önce yazılan eski kayıtlar kataloğa girer)."""
        if self._store is None:
            self._store = open_store(self.data_dir)
        return self._store

    # --- okuma ---

    def row(self, match_id: Any) -> Optional[EventRow]:
        """Olay yükü saklanan maçın katalog satırı; yoksa (ya da kurallı kimlik değilse) None."""
        event_id = canonical_id(match_id)
        if event_id is None:
            return None
        row = self.store.events.get(event_id)
        if row is None or not row.has_event_payload:
            return None
        if row.layout == _LEGACY_LAYOUT and not row.path:
            return None
        return row

    def location(self, match_id: Any) -> Optional[Tuple[Optional[str], Optional[str], str]]:
        """Kaydın yeri: eski düzende (lig dizini, sezon dizini, maç dizini), v3'te (None, None, v3 dizini)."""
        row = self.row(match_id)
        if row is None:
            return None
        if row.layout != _LEGACY_LAYOUT:
            return (None, None, os.path.join(self.data_dir, "v3", "events", str(row.id // 1_000_000),
                                             f"{(row.id // 1000) % 1000:03d}", str(row.id)))
        details = os.path.join(self.data_dir, "match_details")
        parts = str(row.path).strip("/").split("/")
        if parts[0] != "match_details":
            return None
        if len(parts) == 2:
            return (None, None, os.path.join(details, parts[1]))
        if len(parts) == 4:
            return (parts[1], parts[2], os.path.join(details, *parts[1:]))
        return None

    def stored(self, match_id: Any) -> Dict[str, Any]:
        """
        Kayıtlı maçın eski sözlüğü: `basic`, yükü olan eski dilimler (tablo sırasıyla) ve varsa `observation`;
        kayıt yoksa boş sözlük. Okunamayan dilim dosyası yalnızca o dilimi düşürür.
        """
        row = self.row(match_id)
        if row is None:
            return {}
        result = legacy_detail(self.store, row.id)
        if result is None:
            return {}
        observation = _stored_observation(row)
        if observation is not None:
            result[OBSERVATION_KEY] = observation
        return result

    def need(self, match_id: Any) -> str:
        """Maçın ihtiyacı (`full` / `refill` / `refresh` / `none`), katalogdan (`planning.event_needs`)."""
        event_id = canonical_id(match_id)
        if event_id is None:
            return "full"
        QueryService(self.store).require_current()
        needs = planning.event_needs(self.store, [event_id], RefreshPolicy.current(),
                                     threshold=UNAVAILABLE_AFTER_ATTEMPTS)
        return needs.get(event_id, "full")

    def expected_keys(self, event_id: int, sport: Optional[str]) -> List[str]:
        """Beklenen dilimler: sporun `required` dilimleri, yeterince "yok" yanıtı almış olanlar hariç."""
        settled = {info.key for info in self.store.events.slices(event_id)
                   if not info.sub and info.settled_empty(UNAVAILABLE_AFTER_ATTEMPTS)}
        return [key for key in planning.expected_slice_keys(sport) if key not in settled]

    # --- yazma ---

    def save(self, match_id: Any, match_data: Mapping[str, Any],
             outcomes: Optional[Mapping[str, SliceOutcome]] = None) -> Any:
        return save_match_data(self.store, match_id, match_data, outcomes)

    def _run_one(self, item: WorkItem) -> pipeline.ItemResult:
        found: List[pipeline.ItemResult] = []
        pipeline.FetchPipeline(self.store, concurrency=1).run_sync([item], on_result=found.append)
        if not found:
            return pipeline.ItemResult(item, pipeline.ITEM_SKIPPED, pipeline.SKIP_CANCELLED)
        return found[0]

    def _single(self, item: WorkItem, report: Optional[SingleFetchReport]) -> Optional[Dict[str, Any]]:
        result = self._run_one(item)
        if report is not None:
            report.event = result.event
            report.slices.update(result.slices)
        if result.error is not None:
            raise result.error
        return match_data_of(result) if result.ok else None

    def fetch(self, match_id: Any, *, report: Optional[SingleFetchReport] = None) -> Optional[Dict[str, Any]]:
        """Bir maçın bütün detayları: boru hattının `full` birimi. Alınamadıysa ya da yazılamadıysa None."""
        event_id = canonical_id(match_id)
        if event_id is None:
            return None
        return self._single(WorkItem(Ref.event(event_id), "full", (), None, "full"), report)

    def refill(self, match_id: Any, *, report: Optional[SingleFetchReport] = None) -> Optional[Dict[str, Any]]:
        """Saklanan maçın eksik dilimleri: boru hattının `refill` birimi. Kayıt yoksa None."""
        row = self.row(match_id)
        if row is None:
            return None
        states = list(self.store.events.states(Scope(event_ids=(row.id,))))
        item = planning.work_item(row.id, states[0] if states else None, "refill",
                                  threshold=UNAVAILABLE_AFTER_ATTEMPTS)
        assert item is not None
        return self._single(item, report)

    def refresh(self, match_id: Any) -> Optional[Dict[str, Any]]:
        """Geçici kaydı yeniler (boru hattının `refresh` birimi); kayıt yoksa ya da /event alınamadıysa None."""
        row = self.row(match_id)
        if row is None:
            return None
        result = self._run_one(WorkItem(Ref.event(row.id), "refresh", (), row.sport, "refresh"))
        if result.error is not None:
            raise result.error
        if not result.ok:
            return None
        return self.stored(match_id) or None


__all__ = ["Details", "LEGACY_DETAIL_KEYS", "legacy_detail", "SingleFetchReport", "match_data_of", "save_match_data",
           "slice_outcomes"]
