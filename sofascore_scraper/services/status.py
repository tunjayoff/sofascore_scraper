"""
Durum servisi: veri dizininin özeti (docs/design/02-services.md 2.7; plan maddesi RD-4).

`StatusService.summary()` gösterge panelinin ve istatistik ekranlarının sayılarını **katalogdan** verir; dosya
ağacını gezmez. Sayımlar `Store.events.summary` ve `Store.entities.seasons`'tan, disk kullanımı
`Store.info`'dan gelir (ağacı gezen Store'dur). `StatusService.coverage()` detayı saklanan maçların dilim
tamlığını yine katalogdan hesaplar (plan maddesi P15; eski dosya analizi raporunun yerine). Tasarımdaki diğer işler
(health, doctor, tanılama paketi) onları getiren plan maddeleriyle eklenir.

Sayım kuralları (katalogdaki her maç ve her sezon listesi bir kez sayılır):

  * matches   turnuvanın katalogdaki maçları. "Yalnızca bitmiş maçlar" ayarı açıkken (fetch.only_finished,
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

Kapsam (coverage) kuralları:

  * Yalnızca `/event/{id}` yükü saklanan maçlar sayılır (`details`); yalnızca bir listeden bilinen maç girmez.
    Takiplerin sayfalarındaki tamlık (`season_counts`, `TournamentCounts.finished_details`) ise yalnızca bitmiş
    maçları sayar: henüz oynanmamış maçın istatistiği, kadrosu olamaz, gelecek fikstür "eksik" değildir (FX-26,
    canlı doğrulama M12). Lig, takım, oyuncu ve maç takipleri ön yüzde aynı kuralla sayılır.
    Terminal menüsünün eski dosya raporu (`generate_file_report`, FX-15'te kalktı) `basic.json`'ı olmayan bir dizini de maç
    sayıyordu ve yalnızca `match_details/<lig>/season_*/` altındaki dizinlere bakıyordu; düz, `_no_tournament/`
    ve v3 düzenindeki kayıtlar da artık sayılır.
  * Beklenen dilimler maçın sporuna ve evresine göredir (sofascore_scraper/services/planning.py `expected_slice_keys`,
    tamlık hesabına girenler). Bir dilim eksiktir, planlayıcı onu yeniden isteyecekse
    (`planning.missing_slice_keys`): satırı yok ya da `ok` değil ve yeterince kesin "veri yok" yanıtı almamış.
    Yeterince denenip hep boş gelen dilim (ör. SofaScore'un o maç için hiç vermediği kadro) eksik sayılmaz; bitmiş maçta son yanıtı
    "veri yok" olan dilim de (`planning.unresolved_slice_keys`, FX-23): planlayıcı onu doğrulamak için bir kez
    daha ister, ama ilk indirmeden sonra tamlık "%0" görünmez.
  * Tam maç: eksik dilimi olmayan maç. Oran yüzdedir, iki ondalık (eski raporla aynı yuvarlama).

Rapor hiçbir yere yazılmaz: istendiğinde hesaplanır (eskiden `match_details/processed/` altına JSON ve CSV
yazılıyordu).

Zamanlayıcı (`schedule_status`, plan maddesi P29): bu süreçte çalışan uygulama içi zamanlayıcının
(sofascore_scraper/jobs/scheduler.py, `ssc serve --scheduler`) görevleri ve sonraki çalışmaları. Zamanlayıcı yalnızca onu
barındıran süreçte görünür: başka bir süreçten (ör. `ssc status`) ya da `serve --dev`in alt sürecinden
sorulduğunda `enabled` yanlıştır ve liste boştur.

Servis yazdırmaz, kilit almaz ve dosya sistemine dokunmaz.
"""
from __future__ import annotations

import threading
import time
import weakref
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from sofascore_scraper.services import planning
from sofascore_scraper.status import StatusClass
from sofascore_scraper.store import EventQuery, Scope, Store, TournamentSummary

SIZES_MAX_AGE = 60.0  # ölçülen disk kullanımının saklandığı süre (saniye); 0: her çağrıda yeniden ölçülür

AREA_SEASONS = "seasons"
AREA_MATCHES = "matches"
AREA_DETAILS = "match_details"
AREA_DATASETS = "datasets"
AREA_V3 = "v3"  # 3.0 düzeni: maçlar, turnuvalar, takımlar, oyuncular, sporlar (sofascore_scraper/store/layout.py)
AREA_CHANGES = "changes"  # değişiklik günlüğü (aylık parçalar)

_FINISHED = frozenset({StatusClass.COMPLETED.value, StatusClass.DECIDED_WITHOUT_PLAY.value})
_NOT_FINISHED: Tuple[str, ...] = tuple(member.value for member in StatusClass if member.value not in _FINISHED)


def only_finished_setting() -> bool:
    """
    "Yalnızca bitmiş maçlar" ayarının o anki değeri (`fetch.only_finished`, varsayılan açık), ayar yükleyicisinden
    çağrı anında (plan maddesi RD-4). Anlamı FX-26'nınkidir: bir lig indirmesinde hangi maçların detayının
    indirileceğine (QueryService.detail_candidates) ve genel bakış sayılarına karar verir.
    """
    from sofascore_scraper.config import loader

    return bool(loader.active_settings().fetch.only_finished)


@dataclass(frozen=True)
class TournamentCounts:
    """Bir turnuvanın sayıları (kurallar modül belgesinde). tournament_id None: benzersiz turnuvası olmayan maçlar."""

    tournament_id: Optional[int]
    matches: int = 0
    details: int = 0
    events: int = 0  # katalogdaki bütün satırlar (henüz bitmemiş program satırları dahil)
    finished: int = 0
    seasons: int = 0
    finished_details: int = 0  # bitmiş maçlardan `/event/{id}` yükü saklananlar (FX-26: takiplerin ortak kuralı)
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
    def v3(self) -> int:
        return int(self.entries.get(AREA_V3, 0))

    @property
    def changes(self) -> int:
        return int(self.entries.get(AREA_CHANGES, 0))

    @property
    def total(self) -> int:
        """
        İndirilen veri iki düzende, değişiklik günlüğü ve üretilen veri setleri: seasons + matches + details +
        datasets + v3 + changes (plan maddesi FX-13: 3.0'ın yazdığı bir dizinde toplam önceden 0'dı). `.meta`,
        yedekler ve dışa aktarmalar girmez; hepsi `entries`tedir.
        """
        return self.seasons + self.matches + self.details + self.datasets + self.v3 + self.changes


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


def _rate(complete: int, total: int) -> float:
    """Tam maçların yüzdesi, iki ondalık; maç yoksa 0."""
    return round(complete / total * 100, 2) if total else 0.0


@dataclass(frozen=True)
class SeasonCoverage:
    """
    Bir sezonun kapsamı. season_id None: sezonu bilinmeyen maçlar. matches: olay yükü saklanan maçlar;
    complete: eksik dilimi olmayanlar; missing: dilim → o dilimi eksik olan maç sayısı (tablo sırasıyla).
    """

    season_id: Optional[int]
    matches: int = 0
    complete: int = 0
    missing: Mapping[str, int] = field(default_factory=dict)

    @property
    def completion_rate(self) -> float:
        return _rate(self.complete, self.matches)


@dataclass(frozen=True)
class TournamentCoverage:
    """Bir turnuvanın kapsamı (sezonlarının toplamı). tournament_id None: benzersiz turnuvası olmayan maçlar."""

    tournament_id: Optional[int]
    matches: int = 0
    complete: int = 0
    missing: Mapping[str, int] = field(default_factory=dict)
    seasons: Tuple[SeasonCoverage, ...] = ()  # sezon kimliği büyükten küçüğe, sezonu bilinmeyenler en sonda

    @property
    def completion_rate(self) -> float:
        return _rate(self.complete, self.matches)


@dataclass(frozen=True)
class CoverageReport:
    """
    `StatusService.coverage()` sonucu: kapsamdaki, detayı saklanan maçların dilim tamlığı.

    tournaments  maçı olan her turnuva, kimlik sırasıyla; turnuvasız maçların satırı (None) en başta
    catalog_rebuild_reason  None: katalog kullanılabilir. Doluysa katalog dosyaları anlatmıyor ve sayılar eksik
                 ya da sıfır olabilir.
    """

    data_dir: str
    matches: int = 0
    complete: int = 0
    missing: Mapping[str, int] = field(default_factory=dict)
    tournaments: Tuple[TournamentCoverage, ...] = ()
    catalog_rebuild_reason: Optional[str] = None

    @property
    def completion_rate(self) -> float:
        return _rate(self.complete, self.matches)

    def tournament(self, tournament_id: Optional[int]) -> TournamentCoverage:
        """Turnuvanın kapsamı; raporun bilmediği turnuva için hepsi sıfır."""
        for found in self.tournaments:
            if found.tournament_id == tournament_id:
                return found
        return TournamentCoverage(tournament_id)


@dataclass(frozen=True)
class SeasonCounts:
    """
    Bir turnuvanın bir sezonunun sayıları (plan maddesi FX-13, 05-web-ui.md G17). season_id None: sezonu
    bilinmeyen maçlar.

    events       katalogdaki bütün maçlar (bitmemiş program satırları dahil)
    finished     bitmiş olanlar (completed, decided_without_play)
    details      `/event/{id}` yükü saklananlar
    finished_details  bitmiş olup `/event/{id}` yükü saklananlar; tamlık bunlardan sayılır (FX-26)
    complete     bitmiş, detayı saklanıp eksik dilimi olmayanlar (kapsam kuralları modül belgesinde)
    missing      dilim → o dilimi eksik olan bitmiş maç sayısı (tablo sırasıyla)
    schedule_fetched_at  sezon programının en yeni sayfasının alındığı an (epoch saniye); program yoksa None
    """

    season_id: Optional[int]
    events: int = 0
    finished: int = 0
    details: int = 0
    complete: int = 0
    missing: Mapping[str, int] = field(default_factory=dict)
    schedule_fetched_at: Optional[float] = None
    finished_details: int = 0

    @property
    def completion_rate(self) -> float:
        """Detayı saklanan bitmiş maçlardan tam olanların yüzdesi, iki ondalık (FX-26)."""
        return _rate(self.complete, self.finished_details)


class _Tally:
    """Kapsam sayaçları (bir sezon, bir turnuva ya da bütün rapor)."""

    def __init__(self) -> None:
        self.matches = 0
        self.complete = 0
        self.missing: Dict[str, int] = {}

    def add(self, missing: Tuple[str, ...]) -> None:
        self.matches += 1
        if not missing:
            self.complete += 1
        for key in missing:
            self.missing[key] = self.missing.get(key, 0) + 1


def _slice_rank(key: str) -> Tuple[int, str]:
    """Dilimlerin sırası: kayıt defterinin sırası (sofascore_scraper/sports.py), bilinmeyen dilim sonda ve adına göre."""
    from sofascore_scraper.sports import known_slice_names

    names = known_slice_names()
    return (names.index(key), "") if key in names else (len(names), key)


def _season_order(season_id: Optional[int]) -> Tuple[int, int]:
    """Sezonların sırası: kimlik büyükten küçüğe, sezonu bilinmeyen en sonda."""
    return (1, 0) if season_id is None else (0, -season_id)


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


# --- zamanlayıcı ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleStatus:
    """
    Uygulama içi zamanlayıcının durumu.

    enabled     zamanlayıcı bu süreçte çalışıyor
    next_runs   görevlerin durumu, yapılandırma sırasıyla (sofascore_scraper/jobs/scheduler.TaskState); çalışmıyorsa boş
    """

    enabled: bool = False
    next_runs: Tuple[Any, ...] = ()


def schedule_status() -> ScheduleStatus:
    """Bu süreçteki zamanlayıcının görevleri ve sonraki çalışmaları (yoksa kapalı ve boş)."""
    from sofascore_scraper.jobs import scheduler

    running = scheduler.current()
    if running is None:
        return ScheduleStatus()
    return ScheduleStatus(enabled=True, next_runs=running.states())


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
        unfinished = self._unfinished_details(totals)
        matches = self._match_counts(totals, finished_only, unfinished)
        listed, season_total = self._season_counts(table_rows, known, requested)

        wanted: List[Optional[int]] = [row.tournament_id for row in totals]
        wanted += [tid for tid in requested if tid not in known]
        tournaments = tuple(
            self._counts(tid, known.get(tid), matches.get(tid, 0), listed.get(tid, 0), unfinished.get(tid, 0))
            for tid in wanted)

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

    def coverage(self, scope: Optional[Scope] = None, *,
                 threshold: int = planning.DEFAULT_EMPTY_THRESHOLD) -> CoverageReport:
        """
        Detayı saklanan maçların dilim tamlığı, katalogdan (kurallar modül belgesinde). Dosya okunmaz; maçlar ve
        dilim satırları parça parça okunur (`Store.events.states`).

        scope      None: bütün katalog; dolu alanlar birlikte uygulanır (spor, turnuva, sezon, maç)
        threshold  bu kadar kesin "veri yok" yanıtından sonra dilim eksik sayılmaz (planlayıcıyla aynı eşik)

        Depolama hatası (StoreError) çağırana çıkar.
        """
        store = self._store
        info = store.info(sizes=False)
        total = _Tally()
        by_tournament: Dict[Optional[int], _Tally] = {}
        by_season: Dict[Optional[int], Dict[Optional[int], _Tally]] = {}
        if info.rows.get("catalog"):
            for state in store.events.states(scope):
                row = state.event
                if not row.has_event_payload:
                    continue
                missing = planning.unresolved_slice_keys(state, threshold=threshold)
                total.add(missing)
                by_tournament.setdefault(row.tournament_id, _Tally()).add(missing)
                by_season.setdefault(row.tournament_id, {}).setdefault(row.season_id, _Tally()).add(missing)

        def ordered(missing: Mapping[str, int]) -> Dict[str, int]:
            return dict(sorted(missing.items(), key=lambda item: _slice_rank(item[0])))

        tournaments = tuple(
            TournamentCoverage(
                tournament_id=tid,
                matches=tally.matches,
                complete=tally.complete,
                missing=ordered(tally.missing),
                seasons=tuple(
                    SeasonCoverage(sid, part.matches, part.complete, ordered(part.missing))
                    for sid, part in sorted(by_season[tid].items(), key=lambda item: _season_order(item[0]))),
            )
            for tid, tally in sorted(by_tournament.items(), key=lambda item: (item[0] is not None, item[0] or 0)))
        return CoverageReport(
            data_dir=str(store.data_dir),
            matches=total.matches,
            complete=total.complete,
            missing=ordered(total.missing),
            tournaments=tournaments,
            catalog_rebuild_reason=info.catalog_rebuild_reason,
        )

    def season_counts(self, tournament_id: int, *,
                      threshold: int = planning.DEFAULT_EMPTY_THRESHOLD) -> Tuple[SeasonCounts, ...]:
        """
        Turnuvanın sezon başına sayıları (05-web-ui.md G17): sezon listesindeki her sezon (maçı olmasa da) ve maçı
        bilinen her sezon; sezon kimliği büyükten küçüğe, sezonu bilinmeyen maçlar en sonda. Katalogdan okunur;
        turnuvanın maçları ve dilim satırları bir kez gezilir, her sezonun program dilimleri ayrıca okunur.
        """
        from sofascore_scraper.services.listing import SCHEDULE_KEY
        from sofascore_scraper.store import Ref

        store = self._store
        seasons: Dict[Optional[int], Dict[str, Any]] = {
            int(row.id): {} for row in store.entities.seasons(int(tournament_id)) if row.id is not None}
        tallies: Dict[Optional[int], _Tally] = {}
        events: Dict[Optional[int], List[int]] = {}
        for state in store.events.states(Scope(tournament_ids=(int(tournament_id),))):
            row = state.event
            counts = events.setdefault(row.season_id, [0, 0, 0])
            seasons.setdefault(row.season_id, {})
            finished = row.status_class in _FINISHED
            counts[0] += 1
            counts[1] += 1 if finished else 0
            if row.has_event_payload:
                counts[2] += 1
            if row.has_event_payload and finished:
                tallies.setdefault(row.season_id, _Tally()).add(
                    planning.unresolved_slice_keys(state, threshold=threshold))
        out: List[SeasonCounts] = []
        for season_id in sorted(seasons, key=_season_order):
            fetched: Optional[float] = None
            if season_id is not None:
                times = [info.fetched_at.timestamp() for info in store.entities.slices(Ref.season(
                    int(tournament_id), int(season_id))) if info.key == SCHEDULE_KEY and info.fetched_at is not None]
                fetched = max(times) if times else None
            number = events.get(season_id, [0, 0, 0])
            tally = tallies.get(season_id, _Tally())
            out.append(SeasonCounts(
                season_id=season_id, events=number[0], finished=number[1], details=number[2],
                finished_details=tally.matches, complete=tally.complete,
                missing=dict(sorted(tally.missing.items(), key=lambda item: _slice_rank(item[0]))),
                schedule_fetched_at=fetched,
            ))
        return tuple(out)

    # --- sayımlar ---------------------------------------------------------------------------------------

    @staticmethod
    def _counts(tournament_id: Optional[int], row: Optional[TournamentSummary], matches: int,
                listed: int, unfinished_details: int = 0) -> TournamentCounts:
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
            # Özet ve bitmemiş maçların geçişi ayrı anlık görüntülerden okur: sayı sınırlarını aşmasın
            finished_details=max(0, min(row.finished, row.with_payload - unfinished_details)),
        )

    def _unfinished_details(self, totals: List[TournamentSummary]) -> Dict[Optional[int], int]:
        """
        Turnuva başına bitmemiş olduğu halde detayı indirilmiş maç sayısı. Katalogdan tek geçişte okunur ve
        turnuvalarına dağıtılır (turnuvasız maçlar dahil); böyle bir maçı olabilecek turnuva yoksa hiç sorulmaz.
        """
        unfinished: Dict[Optional[int], int] = {}
        if any(row.with_payload and row.events > row.finished for row in totals):
            for event in self._store.events.iter(EventQuery(status_classes=_NOT_FINISHED, has_details=True)):
                unfinished[event.tournament_id] = unfinished.get(event.tournament_id, 0) + 1
        return unfinished

    @staticmethod
    def _match_counts(totals: List[TournamentSummary], finished_only: bool,
                      unfinished: Mapping[Optional[int], int]) -> Dict[Optional[int], int]:
        """
        Turnuva başına `matches`. Ayar kapalıyken bütün maçlar; açıkken bitmiş olanlar ve, bitmemiş olduğu
        halde detayı indirilmiş olanlar (`unfinished`). Sayıları azdır: ayar açıkken bitmemiş bir maçın detayı
        ancak tek maç indirmesiyle, takım ya da maç takibiyle ya da ayar kapalıyken yazılır.
        """
        if not finished_only:
            return {row.tournament_id: row.events for row in totals}
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
    "AREA_CHANGES",
    "AREA_DATASETS",
    "AREA_V3",
    "CoverageReport",
    "SeasonCoverage",
    "TournamentCoverage",
    "AREA_DETAILS",
    "AREA_MATCHES",
    "AREA_SEASONS",
    "SIZES_MAX_AGE",
    "DataSummary",
    "SeasonCounts",
    "DiskUsage",
    "ScheduleStatus",
    "StatusService",
    "TournamentCounts",
    "forget_sizes",
    "only_finished_setting",
    "schedule_status",
]
