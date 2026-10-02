"""
Durum servisi: veri dizininin özeti (docs/design/02-services.md 2.7; plan maddesi RD-4).

`StatusService.summary()` gösterge panelinin ve istatistik ekranlarının sayılarını **katalogdan** verir; dosya
ağacını gezmez. Sayımlar `Store.events.summary` ve `Store.entities.seasons`'tan, disk kullanımı
`Store.info`'dan gelir (ağacı gezen Store'dur). Tasarımdaki diğer işler (health, coverage, doctor, tanılama
paketi) onları getiren plan maddeleriyle eklenir.

Sayım kuralları (katalogdaki her maç ve her sezon listesi bir kez sayılır):

  * matches   turnuvanın katalogdaki maçları. "Yalnızca bitmiş maçlar" ayarı açıkken (FETCH_ONLY_FINISHED,
              varsayılan) bitmiş olanlar (durum sınıfı completed / decided_without_play) ile detayı indirilmiş
              olanların birleşimi: programda görünen ama henüz bitmemiş, detayı da olmayan maç sayılmaz. Ayar
              kapalıyken bütün maçlar. Bugünkü yazıcıların sezon özetine koyduğu satırların karşılığıdır;
              fark, kuralın yazma anında değil okuma anında uygulanmasıdır.
  * details   `/event/{id}` yükü saklanan maçlar; dizinin nerede durduğuna bakılmaz (lig/sezon dizini, kimliksiz
              lig dizini, düz dizin, `_no_tournament/`, yalnızca birleşik dosya). Hep `matches`'in alt kümesidir.
  * seasons   turnuvanın sezon listesindeki sezonlar (liste dosyası hangi adla durursa dursun bir kez).
              Toplam, katalogdaki bütün sezon listelerinin toplamıdır (yapılandırılmamış ligler dahil).
  * seasons_with_events  maçı bilinen farklı sezon sayısı (programı ya da detayı indirilmiş sezonlar).
  * last_update  turnuvanın dizinlenen dosyalarındaki en yeni değişiklik (epoch saniye); hiç detayı yoksa None.

Benzersiz turnuvası olmayan maçlar (`_no_tournament/`) `tournament_id=None` satırında toplanır ve genel
toplamlara girer.

Disk kullanımı: `Store.info().bytes` veri dizininin üst düzey girdilerini verir. Ağacın gezilmesi büyük
dizinlerde pahalıdır; sonuç depo başına `SIZES_MAX_AGE` saniye saklanır ve katalog değiştiğinde (indirme,
temizleme: satır sayıları ya da en yeni `updated_at` değişir) hemen yeniden ölçülür. Kataloğun görmediği
değişiklikler (CSV dışa aktarımı, yedekler, elle silinen dosyalar) en geç o süre sonunda görünür.

Servis yazdırmaz, kilit almaz ve dosya sistemine dokunmaz.
"""
from __future__ import annotations

import os
import threading
import time
import weakref
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from src.status import StatusClass
from src.store import EventQuery, Store, TournamentSummary

SIZES_MAX_AGE = 60.0  # ölçülen disk kullanımının saklandığı süre (saniye); 0: her çağrıda yeniden ölçülür

AREA_SEASONS = "seasons"
AREA_MATCHES = "matches"
AREA_DETAILS = "match_details"
AREA_DATASETS = "datasets"

_FINISHED = frozenset({StatusClass.COMPLETED.value, StatusClass.DECIDED_WITHOUT_PLAY.value})
_NOT_FINISHED: Tuple[str, ...] = tuple(member.value for member in StatusClass if member.value not in _FINISHED)


def only_finished_setting() -> bool:
    """
    "Yalnızca bitmiş maçlar" ayarının o anki değeri. Yazıcılarla aynı kaynaktan ve aynı kuralla okunur
    (src/utils.py: FETCH_ONLY_FINISHED, varsayılan açık); çağrı anında okunur.
    """
    return os.getenv("FETCH_ONLY_FINISHED", "true").lower() == "true"


@dataclass(frozen=True)
class TournamentCounts:
    """Bir turnuvanın sayıları (kurallar modül belgesinde). tournament_id None: benzersiz turnuvası olmayan maçlar."""

    tournament_id: Optional[int]
    matches: int = 0
    details: int = 0
    events: int = 0  # katalogdaki bütün satırlar (henüz bitmemiş program satırları dahil)
    finished: int = 0
    seasons: int = 0
    seasons_with_events: int = 0
    last_update: Optional[int] = None

    @property
    def coverage(self) -> float:
        """Detayı indirilmiş maçların yüzdesi (bir ondalık); maç yoksa 0."""
        return round(self.details / self.matches * 100, 1) if self.matches else 0.0


@dataclass(frozen=True)
class DiskUsage:
    """
    Veri dizininin disk kullanımı (bayt). entries: `Store.info().bytes`, yani her üst düzey girdi (`.meta`,
    `backups`, `reports`, ... dahil). measured_at: ölçümün yapıldığı an (epoch saniye); saklanan bir ölçüm
    `SIZES_MAX_AGE` saniyeye kadar eski olabilir.
    """

    entries: Mapping[str, int] = field(default_factory=dict)
    measured_at: float = 0.0

    @property
    def seasons(self) -> int:
        return int(self.entries.get(AREA_SEASONS, 0))

    @property
    def matches(self) -> int:
        return int(self.entries.get(AREA_MATCHES, 0))

    @property
    def details(self) -> int:
        return int(self.entries.get(AREA_DETAILS, 0))

    @property
    def datasets(self) -> int:
        return int(self.entries.get(AREA_DATASETS, 0))

    @property
    def total(self) -> int:
        """İndirilen veri ve ondan üretilen veri setleri: seasons + matches + details + datasets."""
        return self.seasons + self.matches + self.details + self.datasets


@dataclass(frozen=True)
class DataSummary:
    """
    `StatusService.summary()` sonucu.

    matches / details  bütün katalog (yapılandırılmamış ligler ve turnuvasız maçlar dahil)
    seasons            katalogdaki bütün sezon listelerinin sezon sayısı
    tournaments        maçı olan her turnuva ve ayrıca istenenler; turnuvasız maçların satırı (None) en başta
    disk               disk kullanımı; `sizes=False` ile istenmediyse None
    only_finished      `matches`'in hangi kuralla sayıldığı
    catalog_rebuild_reason  None: katalog kullanılabilir. Doluysa katalog dosyaları anlatmıyor (yeniden
                       kurulmalı) ve sayılar eksik ya da sıfır olabilir.
    """

    data_dir: str
    only_finished: bool
    matches: int = 0
    details: int = 0
    seasons: int = 0
    tournaments: Tuple[TournamentCounts, ...] = ()
    disk: Optional[DiskUsage] = None
    catalog_rebuild_reason: Optional[str] = None

    def tournament(self, tournament_id: Optional[int]) -> TournamentCounts:
        """Turnuvanın sayıları; özetin bilmediği turnuva için hepsi sıfır."""
        for counts in self.tournaments:
            if counts.tournament_id == tournament_id:
                return counts
        return TournamentCounts(tournament_id)


# --- disk kullanımı: depo başına saklanan son ölçüm ---------------------------------------------------

Fingerprint = Tuple[Tuple[Tuple[str, int], ...], Optional[int]]


@dataclass(frozen=True)
class _Sizes:
    usage: DiskUsage
    fingerprint: Fingerprint  # ölçüm anındaki katalog: satır sayıları ve en yeni `updated_at`
    taken: float  # `_now()`


_sizes: "weakref.WeakKeyDictionary[Store, _Sizes]" = weakref.WeakKeyDictionary()
_sizes_lock = threading.Lock()


def _now() -> float:
    """Ölçüm yaşının saati (testler bunu değiştirir; `time.monotonic`'in kendisini değil)."""
    return time.monotonic()


def forget_sizes(store: Optional[Store] = None) -> None:
    """Saklanan disk ölçümünü siler (store=None: hepsini); sonraki özet yeniden ölçer."""
    with _sizes_lock:
        if store is None:
            _sizes.clear()
        else:
            _sizes.pop(store, None)


class StatusService:
    """Veri dizininin durumu; yalnızca okur."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def summary(self, *, tournament_ids: Iterable[int] = (), only_finished: Optional[bool] = None,
                sizes: bool = True, sizes_max_age: float = SIZES_MAX_AGE) -> DataSummary:
        """
        Veri dizininin özeti: sayımlar, turnuva dökümü ve disk kullanımı.

        tournament_ids  maçı olmasa da dökümde yer alacak turnuvalar (yapılandırılmış ligler)
        only_finished   None: ayarın o anki değeri (`only_finished_setting`); True / False: `matches` kuralı
        sizes           False: disk kullanımı ölçülmez (`disk` None)
        sizes_max_age   saklanan ölçümün kullanılabileceği süre (saniye); 0: yeniden ölç

        Depolama hatası (StoreError) çağırana çıkar.
        """
        store = self._store
        requested = tuple(dict.fromkeys(tournament_ids))
        finished_only = only_finished_setting() if only_finished is None else bool(only_finished)
        info = store.info(sizes=False)
        table_rows = info.rows.get("catalog", {})
        totals: List[TournamentSummary] = store.events.summary() if table_rows else []

        known: Dict[Optional[int], TournamentSummary] = {row.tournament_id: row for row in totals}
        matches = self._match_counts(totals, finished_only)
        listed, season_total = self._season_counts(table_rows, known, requested)

        wanted: List[Optional[int]] = [row.tournament_id for row in totals]
        wanted += [tid for tid in requested if tid not in known]
        tournaments = tuple(
            self._counts(tid, known.get(tid), matches.get(tid, 0), listed.get(tid, 0)) for tid in wanted)

        newest = max((row.updated_at for row in totals if row.updated_at is not None), default=None)
        fingerprint: Fingerprint = (tuple(sorted((str(k), int(v)) for k, v in table_rows.items())), newest)
        return DataSummary(
            data_dir=str(store.data_dir),
            only_finished=finished_only,
            matches=sum(matches.values()),
            details=sum(row.with_payload for row in totals),
            seasons=season_total,
            tournaments=tournaments,
            disk=self._disk(fingerprint, sizes_max_age) if sizes else None,
            catalog_rebuild_reason=info.catalog_rebuild_reason,
        )

    # --- sayımlar ---------------------------------------------------------------------------------------

    @staticmethod
    def _counts(tournament_id: Optional[int], row: Optional[TournamentSummary], matches: int,
                listed: int) -> TournamentCounts:
        if row is None:
            return TournamentCounts(tournament_id, seasons=listed)
        return TournamentCounts(
            tournament_id=tournament_id,
            matches=matches,
            details=row.with_payload,
            events=row.events,
            finished=row.finished,
            seasons=listed,
            seasons_with_events=row.seasons,
            last_update=row.updated_at if row.with_payload else None,
        )

    def _match_counts(self, totals: List[TournamentSummary], finished_only: bool) -> Dict[Optional[int], int]:
        """
        Turnuva başına `matches`. Ayar kapalıyken bütün maçlar; açıkken bitmiş olanlar ve, bitmemiş olduğu
        halde detayı indirilmiş olanlar. İkinciler katalogdan tek geçişte okunur ve turnuvalarına dağıtılır
        (turnuvasız maçlar dahil); böyle bir maçı olabilecek turnuva yoksa hiç sorulmaz. Sayıları azdır:
        ayar açıkken bitmemiş bir maçın detayı ancak tek maç indirmesiyle ya da ayar kapalıyken yazılır.
        """
        if not finished_only:
            return {row.tournament_id: row.events for row in totals}
        unfinished: Dict[Optional[int], int] = {}
        if any(row.with_payload and row.events > row.finished for row in totals):
            for event in self._store.events.iter(EventQuery(status_classes=_NOT_FINISHED, has_details=True)):
                unfinished[event.tournament_id] = unfinished.get(event.tournament_id, 0) + 1
        # Özet ve bu geçiş ayrı anlık görüntülerden okur; arada yazan olduysa sayı turnuvanın sınırını aşmasın
        return {row.tournament_id: row.finished + min(unfinished.get(row.tournament_id, 0), row.events - row.finished)
                for row in totals}

    def _season_counts(self, table_rows: Mapping[str, int], known: Mapping[Optional[int], Any],
                       tournament_ids: Iterable[int]) -> Tuple[Dict[Optional[int], int], int]:
        """
        (turnuva → sezon listesindeki sezon sayısı, bütün sezon listelerinin toplamı).

        Toplam için bütün sezon listelerini saymak gerekir; yalnızca sezon listesi olan (maçı da takibi de
        olmayan) bir turnuvanın kimliği ise okuma API'sinden bulunamaz. Bu yüzden toplam tersinden hesaplanır:
        `seasons` tablosunun satır sayısı eksi listede olmayan sezonlar. Listede olmayan sezon bir maçtan ya da
        program sayfasından gelir, yani turnuvasının maçı ya da turnuva satırı vardır; onlar tek tek sorulur.
        """
        if not table_rows:
            return {}, 0
        entities = self._store.entities
        candidates: Dict[int, None] = {tid: None for tid in known if tid is not None}
        candidates.update((tid, None) for tid in tournament_ids)
        tournament_rows = int(table_rows.get("tournaments", 0))
        if tournament_rows:
            candidates.update((row.id, None) for row in entities.tournaments(limit=tournament_rows))
        listed: Dict[Optional[int], int] = {}
        unlisted = 0
        for tournament_id in candidates:
            seasons = entities.seasons(tournament_id)
            in_list = sum(1 for season in seasons if season.listed)
            listed[tournament_id] = in_list
            unlisted += len(seasons) - in_list
        return listed, max(int(table_rows.get("seasons", 0)) - unlisted, 0)

    # --- disk kullanımı ---------------------------------------------------------------------------------

    def _disk(self, fingerprint: Fingerprint, max_age: float) -> DiskUsage:
        store = self._store
        now = _now()
        with _sizes_lock:
            kept = _sizes.get(store)
        if kept is not None and kept.fingerprint == fingerprint and now - kept.taken < max_age:
            return kept.usage
        usage = DiskUsage(entries=dict(store.info(sizes=True).bytes), measured_at=time.time())
        with _sizes_lock:
            _sizes[store] = _Sizes(usage, fingerprint, now)
        return usage


__all__ = [
    "AREA_DATASETS",
    "AREA_DETAILS",
    "AREA_MATCHES",
    "AREA_SEASONS",
    "SIZES_MAX_AGE",
    "DataSummary",
    "DiskUsage",
    "StatusService",
    "TournamentCounts",
    "forget_sizes",
    "only_finished_setting",
]
