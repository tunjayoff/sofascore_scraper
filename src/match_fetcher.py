"""
Sezon programı için uyumluluk sarmalayıcısı (plan maddesi P14).

Programın kuralları (tur listesi mi olay sayfaları mı, tur önbelleği, "yalnızca bitmiş maçlar", sayfaların Store'a
yazılması) src/services/listing.py'dedir. Bu sınıf onları eski adlarıyla sunar: `list_schedule` tipli sonuç
(`ListingResult`) döndürür ve getirme boru hattında çalışır (tek ısıtılmış oturum, yazıcı thread'i); eşitleme servisi
bunu kullanır. Terminal menüsünün eski yüzü (`fetch_matches_for_season`, önceki sezona geçiş, istek katmanının async
işleviyle çekim) plan maddesi FX-15'te kalktı; kalan durağan adlar betikler ve eski import'lar içindir.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.client import base_url
from src.config_manager import ConfigManager
from src.season_fetcher import SeasonFetcher
from src.services import listing
# Program sayfaları Store'a yazılır (`EntityStore.put`, plan maddesi ST-22): v3/tournaments/<lig>/seasons/<sezon>/
# schedule/<alt anahtar>.json.gz. Yazma kataloğu kendisi günceller. Paket kökü üzerinden: cephe ilk çağrıda yüklenir.
from src import store as store_api
from src.logger import get_logger

if TYPE_CHECKING:
    from src.store import Store

logger = get_logger("MatchFetcher")

SCHEDULE_KEY = listing.SCHEDULE_KEY  # sezonun program sayfaları dilimi (alt anahtar: round_12, round_3_final, last_0)


class MatchFetcher:
    """SofaScore'dan sezon programlarını çeken sınıf (uyumluluk sarmalayıcısı; kurallar src/services/listing.py)."""

    # Eski adlar (testler ve betikler okur)
    ROUND_CACHE_TTL_SECONDS = listing.ROUND_CACHE_TTL_SECONDS

    def __init__(self, config_manager: ConfigManager, season_fetcher: SeasonFetcher, data_dir: str = "data"):
        """
        Args:
            config_manager: Lig yapılandırmalarını yöneten ConfigManager örneği
            season_fetcher: Sezon verilerini yöneten SeasonFetcher örneği
            data_dir: Verilerin kaydedileceği ana dizin
        """
        self.config_manager = config_manager
        self.season_fetcher = season_fetcher
        self.data_dir = data_dir
        self.base_url = base_url()

    def _store(self) -> "Store":
        """Veri dizininin deposu (süreçte dizin başına tek nesne); açılamazsa StoreError fırlar."""
        return store_api.open_store(self.data_dir)

    def _concurrency(self) -> int:
        try:
            return max(1, int(self.config_manager.get_max_concurrent()))
        except (TypeError, ValueError):
            return 1

    # --- tipli yol (eşitleme servisi) -------------------------------------------------------------------

    def list_schedule(self, league_id: int, season_id: int, *, max_age: Optional[float] = None) -> listing.ListingResult:
        """
        Sezonun programı, getirme boru hattında: tipli sonuç. max_age: sezonun sayfaları bu kadar saniyeden gençse
        (listing.schedule_is_fresh) hiç istek atılmaz ve sonuç `skipped` / `fresh` olur.
        """
        from src.utils import FETCH_ONLY_FINISHED, SAVE_EMPTY_ROUNDS

        service = listing.ListingService(self._store(), only_finished=bool(FETCH_ONLY_FINISHED),
                                         save_empty_rounds=bool(SAVE_EMPTY_ROUNDS), concurrency=self._concurrency())
        result = service.schedule(int(league_id), int(season_id), max_age=max_age)
        if result.chunks:
            self._report_season(int(league_id), int(season_id), result.chunks)
        return result

    # --- eski adlar: saf kurallar -----------------------------------------------------------------------

    @staticmethod
    def schedule_sub(kind: str, number: Any, slug: Optional[str] = None) -> str:
        """Program sayfasının alt anahtarı (listing.schedule_sub)."""
        return listing.schedule_sub(kind, number, slug)

    @staticmethod
    def build_round_events_url(league_id: int, season_id: int, round_num: int, slug: Optional[str] = None) -> str:
        """SofaScore tur yolu (kupada slug'ıyla)."""
        from src.client import endpoints

        return endpoints.round_events(league_id, season_id, round_num, slug)

    @staticmethod
    def is_week_based_rounds(rounds: List[Dict[str, Any]], max_round: int = 50) -> bool:
        """Tur listesi sıralı lig haftalarına benziyor mu (listing.is_week_based_rounds)."""
        return listing.is_week_based_rounds(rounds, max_round=max_round)

    @staticmethod
    def _is_finished_event(event: Dict[str, Any]) -> bool:
        """SofaScore type finished: oynanıp biten maçlar ve hükmen/çekilme (src/status.py)."""
        from src.services.pipeline import is_finished

        return is_finished(event)

    def _report_season(self, league_id: int, season_id: int, results: List[Dict[str, Any]]) -> None:
        """
        Çekilen sezonun özetini günlüğe yazar. Sezon özeti dosyaları (`<sezon>_summary.json` ve `_summary.csv`)
        artık yazılmaz (plan maddesi ST-22, karar S4): maç listeleri, gösterge paneli ve dışa aktarma veri
        klasörünün kataloğunu okur; tablo isteyen CSV dışa aktarmasını kullanır.
        """
        round_counts: Dict[Any, int] = {}
        for result in results:
            round_num = result.get("round")
            if round_num is not None:
                if round_num in round_counts:
                    logger.warning(f"League {league_id}, season {season_id}: round {round_num} was fetched more than once")
                round_counts[round_num] = round_counts.get(round_num, 0) + 1
        matches = sum(len(result.get("events") or []) for result in results)
        logger.info(f"League {league_id}, season {season_id}: {len(results)} schedule pages, {matches} matches listed")
