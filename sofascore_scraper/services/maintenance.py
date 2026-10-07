"""
Bakım servisi: veri dizininde ağ isteği gerektirmeyen düzeltmeler (docs/design/02-services.md 2.7).

İşler:
  * "bu dilim bu maçta yok" işaretlerinin yeniden denetime açılması (`main.py --recheck-unavailable`).
    İşaretleri okuyup yazan kod hâlâ MatchDataFetcher'dadır (`reset_unavailable_markers`); servis onu
    yüzlerden bağımsız, türü belli bir sonuçla sunar. Bağlam (`ServiceContext`) ister.
  * verinin temizlenmesi (`clear`, plan maddesi ST-19): işi Store yapar (`Store.clear`,
    docs/design/01-storage.md 9.3); servis bugünkü kapsam adlarını (`match_details`, `matches`, `seasons`,
    `all`) Store'un adlarına çevirir. Yalnızca depo ister: `MaintenanceService(store=...)`. Bir turnuvanın (ya da
    sezonunun) verisi `clear_tournament` ile silinir (`store.purge`, plan maddesi FX-19).
  * eski düzenden v3'e taşıma (`migrate`, plan maddesi ST-23): işi Store yapar (`Store.migrate`,
    docs/design/01-storage.md 5.4); kuru çalıştırma (`dry_run=True`) planı, gerçek çalıştırma raporu döndürür.
  * kataloğun yönetimi (`rebuild_catalog`, `verify_catalog`, `reconcile_catalog`; ST-23): `Store.catalog`
    (CatalogAdmin) çağrıları, kilitleriyle.

Servis yazdırmaz. Yeniden denetim kilit almaz: veri dizininin yazar kilidini çağıran tutar (bugün main.py).
Temizleme `maintenance` kilidi ister: çağıran tutuyorsa onun altında çalışır, tutmuyorsa Store işlem süresince
alır. Taşıma `writer` ve `live` kilitlerini kendisi alır (Store). Kataloğu yeniden kurmak, onararak doğrulamak
ve derin uzlaştırmak `maintenance` kilidini ister (bu süreç tutmuyorsa servis işlem süresince alır); hızlı
doğrulama ve uzlaştırma kilit almaz (tek bir okuma ya da yazma işlemidir).
"""
from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, Iterable, Iterator, Literal, Optional, Union

from sofascore_scraper.logger import get_logger
from sofascore_scraper.store import ClearReport, Store

if TYPE_CHECKING:
    from sofascore_scraper.services.context import ServiceContext
    from sofascore_scraper.store import (MigrationPlan, MigrationProgress, MigrationReport, RebuildReport, ReconcileReport,
                           TournamentClearReport, VerifyReport)

logger = get_logger("MaintenanceService")

# Bugünkü kapsam adları (web API'si) ve Store'un adları (docs/design/01-storage.md 9.3)
DataScope = Literal["all", "seasons", "matches", "match_details", "events", "schedules"]
STORE_SCOPES: Dict[str, str] = {
    "all": "all",
    "match_details": "events",
    "matches": "schedules",
    "seasons": "seasons",
    "events": "events",
    "schedules": "schedules",
}

MAINTENANCE_LEASE = "maintenance"
REBUILD_PURPOSE = "op:rebuild"
VERIFY_PURPOSE = "op:verify"
RECONCILE_PURPOSE = "op:reconcile"
REBUILD_MODES = ("auto", "in_place", "recreate")


@dataclass(frozen=True)
class ResetCounts:
    """
    Yeniden denetimin sonucu.

    matches  işareti değişen maç
    slices   sonraki indirmede yeniden istenecek dilim
    scanned  işaret dosyası olan (bakılan) maç
    """

    matches: int = 0
    slices: int = 0
    scanned: int = 0


class MaintenanceService:
    """Veri dizini üzerinde, SofaScore'a istek atmayan bakım işleri."""

    def __init__(self, ctx: Optional["ServiceContext"] = None, *, store: Optional[Store] = None) -> None:
        """ctx: yeniden denetim için (indiricileri taşır); store: verilmezse bağlamın deposu kullanılır."""
        if ctx is None and store is None:
            raise ValueError("MaintenanceService needs a service context or a store")
        self._ctx = ctx
        self._store_given = store

    @property
    def _store(self) -> Store:
        if self._store_given is not None:
            return self._store_given
        assert self._ctx is not None
        return self._ctx.store

    def clear(self, scope: DataScope, *, confirm: bool) -> ClearReport:
        """
        Verinin bir kısmını siler (`Store.clear`): `match_details` (ya da `events`) maç detaylarını, `matches`
        (ya da `schedules`) maç listelerini, `seasons` sezon listelerini, `all` üçünü. Katalog aynı kilit
        altında kalan dosyalardan yeniden kurulur. `ClearReport.cleared` silinen ağaçları bugünkü adlarıyla
        ve silinme sırasıyla verir.

        confirm=False ValueError: yüz, kullanıcının onayını almadan çağırmamalıdır. Bilinmeyen kapsam
        ValueError; başka bir süreç dizini kullanıyorsa LeaseHeld; dosya sistemi hatası StoreError.
        """
        if not confirm:
            raise ValueError("clear needs confirm=True")
        if scope not in STORE_SCOPES:
            raise ValueError(f"unknown clear scope: {scope!r}")
        return self._store.clear(STORE_SCOPES[scope])

    def clear_tournament(self, tournament_id: int, *, season_id: Optional[int] = None,
                         confirm: bool) -> "TournamentClearReport":
        """
        Bir turnuvanın (season_id verildiyse yalnızca o sezonun) saklanan verisini siler (`store.purge.tournament`,
        plan maddesi FX-19): maçları, programları ve turnuvanın tamamında sezon listesini; takipler, değişiklik
        günlüğü, iş geçmişi, yedekler ve dışa aktarmalar kalır. `maintenance` kilidi `clear` gibidir; katalog aynı
        kilit altında yeniden kurulur.

        confirm=False ValueError; geçersiz kimlik ValueError; başka bir süreç dizini kullanıyorsa LeaseHeld;
        dosya sistemi hatası StoreError.
        """
        if not confirm:
            raise ValueError("clear_tournament needs confirm=True")
        return self._store.purge.tournament(tournament_id, season_id=season_id)

    def migrate(
        self,
        *,
        dry_run: bool = False,
        exact: bool = False,
        tournaments: Iterable[int] = (),
        limit: Optional[int] = None,
        delete_legacy: bool = False,
        purge_derived: bool = False,
        confirm: bool = False,
        should_stop: Optional[Callable[[], bool]] = None,
        progress: Optional[Callable[["MigrationProgress"], None]] = None,
    ) -> Union["MigrationPlan", "MigrationReport"]:
        """
        Eski düzeni v3'e taşır (`Store.migrate`, docs/design/01-storage.md 5.4): dönüştürür, doğrular, eski
        kopyayı bırakır. dry_run=True diske dokunmadan planı (`MigrationPlan`) döndürür; `exact` yalnızca kuru
        çalıştırmada anlamlıdır (boyut tahmini bütün maçları sıkıştırır). Gerçek çalıştırma raporu
        (`MigrationReport`) döndürür.

        delete_legacy: doğrulanan eski kopyalar da silinir. purge_derived: türetilmiş dosyalar (sezon özetleri,
        `match_details/processed/`) silinir. İkisi de yıkıcıdır: gerçek çalıştırmada confirm=True ister
        (ValueError). Başka bir süreç yazıyorsa ya da canlı servis / izleyici çalışıyorsa LeaseHeld.
        """
        if exact and not dry_run:
            raise ValueError("exact is a dry-run option")
        if (delete_legacy or purge_derived) and not dry_run and not confirm:
            raise ValueError("delete_legacy and purge_derived need confirm=True")
        if dry_run:
            return self._store.migrate.plan(tournaments=tournaments, limit=limit, exact=exact,
                                            delete_legacy=delete_legacy, purge_derived=purge_derived)
        return self._store.migrate.run(tournaments=tournaments, limit=limit, delete_legacy=delete_legacy,
                                       purge_derived=purge_derived, should_stop=should_stop, progress=progress)

    def rebuild_catalog(self, *, mode: str = "auto",
                        progress: Optional[Callable[[str, int, int], None]] = None) -> "RebuildReport":
        """
        Kataloğu dosyalardan yeniden kurar (`CatalogAdmin.rebuild`; mode: auto | in_place | recreate).
        `maintenance` kilidi altında: başka bir süreç dizini kullanıyorsa LeaseHeld.
        """
        if mode not in REBUILD_MODES:
            raise ValueError(f"unknown rebuild mode: {mode!r}")
        with self._maintenance(REBUILD_PURPOSE):
            report = self._store.catalog.rebuild(mode=mode, progress=progress)
        logger.info("Catalog rebuilt (%s): %d events, %d slices, %.2f s", report.mode, report.events, report.slices,
                    report.seconds)
        return report

    def verify_catalog(self, *, deep: bool = False, repair: bool = False) -> "VerifyReport":
        """
        Kataloğu dosyalarla karşılaştırır (`CatalogAdmin.verify`, bölüm 3.6). deep=True her maçı ve v3
        yüklerinin hepsini okur. repair=True tutmayanları dosyalardan yeniden dizinler: `maintenance` kilidi
        altında (LeaseHeld).
        """
        with self._maintenance(VERIFY_PURPOSE) if repair else contextlib.nullcontext():
            return self._store.catalog.verify(deep=deep, repair=repair)

    def reconcile_catalog(self, *, deep: bool = False) -> "ReconcileReport":
        """
        Kataloğu arkasından değişen dosyalarla eşitler (`CatalogAdmin.reconcile`, bölüm 3.5). Açılışın
        sınırına (karar S17) bakmaz: maç dizinlerinin hepsi imzalarıyla karşılaştırılır. deep=True her şeyi yeniden
        okur, sonra onararak doğrular: `maintenance` kilidi altında (LeaseHeld). v3 maç dizinleri de taranır.
        """
        with self._maintenance(RECONCILE_PURPOSE) if deep else contextlib.nullcontext():
            return self._store.catalog.reconcile(deep=deep, v3=True)

    @contextlib.contextmanager
    def _maintenance(self, purpose: str) -> Iterator[None]:
        """`maintenance` kilidi: bu süreç zaten tutuyorsa onun altında, tutmuyorsa işlem süresince alınır."""
        holder = self._store.lease_holder(MAINTENANCE_LEASE)
        if holder is not None and holder.pid == os.getpid():
            yield
            return
        with self._store.lease(MAINTENANCE_LEASE, purpose=purpose):
            yield

    def recheck_unavailable(
        self, league_id: Optional[int] = None, *, include_confirmed: bool = False
    ) -> ResetCounts:
        """
        "Yok" işaretlerini geri alır; dilimler sonraki indirmede yeniden istenir. Ağ isteği yapmaz.

        league_id          yalnızca bu ligin kayıtları; None ise bütün ligler
        include_confirmed  False: yalnızca kesin yanıtla doğrulanmamış (eski sürümden kalan) sayımlar geri
                           alınır, bu yüzden işlem tekrarlanabilir. True: doğrulanmış işaretler de silinir.

        Kalıcı depolama hatası çağırana çıkar. Bağlam olmadan kurulmuş serviste ValueError.
        """
        if self._ctx is None:
            raise ValueError("recheck_unavailable needs a service context")
        reset = self._ctx.match_data_fetcher.reset_unavailable_markers(
            league_id=league_id, include_confirmed=include_confirmed
        )
        counts = ResetCounts(
            matches=int(reset.get("matches", 0)),
            slices=int(reset.get("slices", 0)),
            scanned=int(reset.get("scanned", 0)),
        )
        logger.info(
            "Unavailable markers rechecked: league_id=%s include_confirmed=%s matches=%s slices=%s scanned=%s",
            league_id, include_confirmed, counts.matches, counts.slices, counts.scanned,
        )
        return counts
