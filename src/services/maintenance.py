"""
Bakım servisi: veri dizininde ağ isteği gerektirmeyen düzeltmeler (docs/design/02-services.md 2.7).

İki iş:
  * "bu dilim bu maçta yok" işaretlerinin yeniden denetime açılması (`main.py --recheck-unavailable`).
    İşaretleri okuyup yazan kod hâlâ MatchDataFetcher'dadır (`reset_unavailable_markers`); servis onu
    yüzlerden bağımsız, türü belli bir sonuçla sunar. Bağlam (`ServiceContext`) ister.
  * verinin temizlenmesi (`clear`, plan maddesi ST-19): işi Store yapar (`Store.clear`,
    docs/design/01-storage.md 9.3); servis bugünkü kapsam adlarını (`match_details`, `matches`, `seasons`,
    `all`) Store'un adlarına çevirir. Yalnızca depo ister: `MaintenanceService(store=...)`.
Tasarımdaki diğer işler (migrate, rebuild_catalog) onları getiren plan maddeleriyle eklenir.

Servis yazdırmaz. Yeniden denetim kilit almaz: veri dizininin yazar kilidini çağıran tutar (bugün main.py).
Temizleme `maintenance` kilidi ister: çağıran tutuyorsa onun altında çalışır, tutmuyorsa Store işlem süresince
alır.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Literal, Optional

from src.logger import get_logger
from src.store import ClearReport, Store

if TYPE_CHECKING:
    from src.services.context import ServiceContext

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
