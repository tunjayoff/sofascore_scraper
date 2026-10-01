"""
Bakım servisi: veri dizininde ağ isteği gerektirmeyen düzeltmeler (docs/design/02-services.md 2.7).

Bugünkü tek işi "bu dilim bu maçta yok" işaretlerinin yeniden denetime açılmasıdır (`main.py
--recheck-unavailable`). İşaretleri okuyup yazan kod hâlâ MatchDataFetcher'dadır
(`reset_unavailable_markers`); servis onu yüzlerden bağımsız, türü belli bir sonuçla sunar. Tasarımdaki
diğer işler (clear, migrate, rebuild_catalog) onları getiren plan maddeleriyle eklenir.

Servis yazdırmaz ve kilit almaz: sonucu kullanıcı metnine çeviren ve veri dizininin yazar kilidini tutan,
çağıran yüzdür (bugün main.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.logger import get_logger
from src.services.context import ServiceContext

logger = get_logger("MaintenanceService")


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

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    def recheck_unavailable(
        self, league_id: Optional[int] = None, *, include_confirmed: bool = False
    ) -> ResetCounts:
        """
        "Yok" işaretlerini geri alır; dilimler sonraki indirmede yeniden istenir. Ağ isteği yapmaz.

        league_id          yalnızca bu ligin kayıtları; None ise bütün ligler
        include_confirmed  False: yalnızca kesin yanıtla doğrulanmamış (eski sürümden kalan) sayımlar geri
                           alınır, bu yüzden işlem tekrarlanabilir. True: doğrulanmış işaretler de silinir.

        Kalıcı depolama hatası çağırana çıkar.
        """
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
