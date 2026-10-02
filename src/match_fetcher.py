from src.i18n import get_i18n
"""
Sezon programı için uyumluluk sarmalayıcısı (plan maddesi P14).

Programın kuralları (tur listesi mi olay sayfaları mı, tur önbelleği, "yalnızca bitmiş maçlar", sayfaların Store'a
yazılması) src/services/listing.py'dedir. Bu sınıf onları eski adlarıyla sunar:

  * `list_schedule`: tipli sonuç (`ListingResult`), getirme boru hattında (tek ısıtılmış oturum, yazıcı thread'i).
    Eşitleme servisi bunu kullanır.
  * `fetch_matches_for_season` / `fetch_all_matches_for_season` / `fetch_all_rounds_*`: terminal menüsünün eski
    yüzü (True/False ya da sayfa listesi). İstekleri istek katmanının async işleviyle (src.utils
    make_api_request_async) yapılır; kurallar aynıdır. Terminal ilerleme çubuğu yoktur.

Önceki sezona geçiş yalnızca sezonu kendisi seçilen ("güncel sezon") çağrılarda yapılır: `fetch_matches_for_season`
sezon kimliği verilmeden çağrıldığında. Açıkça seçilen sezonda başka bir sezona geçilmez.
"""

import asyncio
import datetime
import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from src.client import base_url
from src.config_manager import ConfigManager
from src.exceptions import SofaScoreScraperError
from src.season_fetcher import SeasonFetcher
from src.services import listing
from src.slices import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, SLICE_SKIPPED, Outcome
# İstek fonksiyonu ve FETCH_ONLY_FINISHED fonksiyon içinde import edilir: çağrı anındaki değer okunur (testler patch eder)
from src.utils import ensure_directory
# Program sayfaları Store'a yazılır (`EntityStore.put`, plan maddesi ST-22): v3/tournaments/<lig>/seasons/<sezon>/
# schedule/<alt anahtar>.json.gz. Yazma kataloğu kendisi günceller. Paket kökü üzerinden: cephe ilk çağrıda yüklenir.
from src import store as store_api
from src.logger import get_logger

if TYPE_CHECKING:
    from src.store import Store

logger = get_logger("MatchFetcher")

SCHEDULE_KEY = listing.SCHEDULE_KEY  # sezonun program sayfaları dilimi (alt anahtar: round_12, round_3_final, last_0)


def _answer(data: Any) -> Outcome:
    """İstek katmanının döndürdüğü veri → sonuç (src.client.Client ile aynı kural)."""
    if data is None:
        return Outcome(SLICE_FAILED, reason="other")
    if not data:
        return Outcome(SLICE_EMPTY, data=data, reason="empty")
    return Outcome(SLICE_OK, data=data)


def _legacy_get(session: Any) -> listing.Get:
    """Eski istek yolu (src.utils.make_api_request_async, göreli yol) → listing'in istek işlevi."""

    async def get(path: str, retries: Optional[int] = None) -> Outcome:
        from src.utils import make_api_request_async

        try:
            data = await make_api_request_async(session, path, max_retries=retries)
        except SofaScoreScraperError as exc:
            from src.exceptions import CircuitOpenError

            if isinstance(exc, CircuitOpenError):
                return Outcome(SLICE_SKIPPED, reason="breaker")
            return Outcome.from_error(exc)
        return _answer(data)

    return get


async def _inline(fn: Any) -> Any:
    """Eski yolun yazması: çağıranın döngüsünde, doğrudan (eski kod gibi)."""
    return fn()


class MatchFetcher:
    """SofaScore'dan sezon programlarını çeken sınıf (uyumluluk sarmalayıcısı; kurallar src/services/listing.py)."""

    # Eski adlar (testler ve betikler okur)
    _TERMINAL_STATUS_TYPES = listing.TERMINAL_STATUS_TYPES
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
        self.matches_dir = os.path.join(data_dir, "matches")
        self.base_url = base_url()

        # Veri dizinlerinin var olduğundan emin ol
        ensure_directory(self.data_dir)
        ensure_directory(self.matches_dir)

    def _store(self) -> "Store":
        """Veri dizininin deposu (süreçte dizin başına tek nesne); açılamazsa StoreError fırlar."""
        return store_api.open_store(self.data_dir)

    def _concurrency(self) -> int:
        try:
            return max(1, int(self.config_manager.get_max_concurrent()))
        except (TypeError, ValueError):
            return 1

    def _lister(self) -> listing.ScheduleLister:
        from src.utils import FETCH_ONLY_FINISHED, SAVE_EMPTY_ROUNDS

        return listing.ScheduleLister(self._store(), only_finished=bool(FETCH_ONLY_FINISHED),
                                      save_empty_rounds=bool(SAVE_EMPTY_ROUNDS), concurrency=self._concurrency())

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

    @classmethod
    def _round_is_complete(cls, data: Dict[str, Any]) -> bool:
        return listing.round_is_complete(data)

    def _filter_finished_matches(self, data: Dict[str, Any]) -> Tuple[Dict[str, Any], int, int]:
        """Yalnızca bitmiş maçları tutan kopya, toplam ve bitmiş maç sayısı (listing.filter_finished)."""
        if not data or "events" not in data:
            return data, 0, 0
        return listing.filter_finished(data)

    def _format_timestamp_for_terminal(self, timestamp: int, default_format: str = "%Y-%m-%d %H:%M:%S") -> str:
        """Terminal çıktısı için timestamp'i yapılandırılmış formata çevirir."""
        if not timestamp:
            return ""
        dt = datetime.datetime.fromtimestamp(timestamp)
        date_format = self.config_manager.get_date_format()
        try:
            return dt.strftime(date_format)
        except ValueError:
            logger.warning("Invalid DATE_FORMAT %r; using the default format", date_format)
            return dt.strftime(default_format)

    @staticmethod
    def _parse_season_start_year(season_name: str, season_info: Optional[Dict[str, Any]] = None) -> Optional[int]:
        """Parse start year from '26/27', '2024/25', or 'Premier League 26/27'."""
        import re

        texts: List[str] = []
        if season_info:
            texts.append(str(season_info.get("year") or ""))
            texts.append(str(season_info.get("name") or ""))
        texts.append(season_name or "")
        for text in texts:
            if not text:
                continue
            m = re.search(r"(20\d{2})\s*/\s*(?:20)?\d{2}", text)
            if m:
                return int(m.group(1))
            m = re.search(r"\b(\d{2})\s*/\s*(\d{2})\b", text)
            if m:
                return 2000 + int(m.group(1))
            m = re.search(r"(20\d{2})", text)
            if m:
                return int(m.group(1))
        return None

    def _previous_season(self, league_id: int, season_id: int) -> Optional[Dict[str, Any]]:
        seasons = self.season_fetcher.get_seasons_for_league(league_id) or []
        sorted_seasons = sorted(
            seasons,
            key=lambda s: self.season_fetcher._get_sortable_year_value(s.get("year", "0")),
            reverse=True,
        )
        for i, s in enumerate(sorted_seasons):
            if s.get("id") == season_id and i + 1 < len(sorted_seasons):
                return sorted_seasons[i + 1]
        # Stale / unknown id: prefer downloadable season (usually 2nd newest)
        preferred_id = self.season_fetcher.preferred_download_season_id(league_id)
        for s in sorted_seasons:
            if s.get("id") == preferred_id and preferred_id != season_id:
                return s
        if sorted_seasons and sorted_seasons[0].get("id") != season_id:
            # last resort: newest that isn't the bad id
            return sorted_seasons[0] if len(sorted_seasons) == 1 else sorted_seasons[min(1, len(sorted_seasons) - 1)]
        return None

    # --- eski adlar: depo ve istekler -------------------------------------------------------------------

    def _save_schedule_page(self, league_id: int, season_id: int, sub: str, payload: Dict[str, Any], *,
                            meta: Dict[str, Any], empty: bool = False) -> None:
        """Program sayfasını Store'a yazar (listing.ScheduleLister.save_page). Depolama hatası çağırana çıkar."""
        self._lister().save_page(league_id, season_id, sub, payload, meta=meta, empty=empty)

    def _load_cached_round(self, league_id: int, season_id: int, sub: str) -> Optional[Dict[str, Any]]:
        """Saklanan tur sayfası, güncel olmaya devam ediyorsa (listing.ScheduleLister.cached_round)."""
        return self._lister().cached_round(league_id, season_id, sub)

    async def _fetch_and_save_round(self, semaphore: asyncio.Semaphore, session: Any, league_id: int, season_id: int,
                                    round_num: int, slug: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Bir turu çeker ve ham halini Store'a yazar; özete girecek kısmı döndürür (maçı yoksa None)."""
        return await self._lister().round(semaphore, league_id, season_id, round_num, slug, _legacy_get(session),
                                          _inline, listing.ScheduleRun())

    async def fetch_all_rounds_async(self, league_id: int, season_id: int, max_round: int = 50) -> List[Dict[str, Any]]:
        """
        Sezonun programı: haftalık tur listesi varsa turlar, yoksa sayfalı `events/last` + `events/next`. Her sayfa
        çekilir çekilmez Store'a yazılır. Özete giren sayfaları döndürür.
        """
        from src.utils import FETCH_ONLY_FINISHED, SAVE_EMPTY_ROUNDS, create_session_async

        league_name = self.config_manager.get_leagues().get(league_id, f"League {league_id}")
        logger.info("Fetching the schedule of %s, season %s", league_name, season_id)
        lister = listing.ScheduleLister(self._store(), only_finished=bool(FETCH_ONLY_FINISHED),
                                        save_empty_rounds=bool(SAVE_EMPTY_ROUNDS), concurrency=self._concurrency(),
                                        max_round=max_round)
        async with create_session_async() as session:
            result = await lister.list(league_id, season_id, _legacy_get(session), _inline)
        return result.chunks

    def fetch_all_rounds_parallel(self, league_id, season_id, max_round=50):
        """Senkron sarmalayıcı. Sayfalar çekildikçe Store'a yazılır ve kataloğa girer."""
        logger.info(get_i18n().t('fetching_data_up_to_max_rounds', max_round=max_round))
        return asyncio.run(self.fetch_all_rounds_async(league_id, season_id, max_round=max_round))

    def fetch_all_rounds_for_season(self, league_id: int, season_id: int, max_round: int = 50) -> List[Dict[str, Any]]:
        """Belirli bir lig ve sezon için tüm haftaların maç verilerini çeker (özete giren sayfalar)."""
        return self.fetch_all_rounds_parallel(league_id, season_id, max_round)

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

    def fetch_all_matches_for_season(
        self,
        league_id: int,
        season_id: int,
        max_round: int = 50,
        retry_count: int = 0,
        allow_fallback: bool = False,
    ) -> bool:
        """
        Belirli bir lig ve sezon için maç programını çeker; maç listelendiyse True.

        allow_fallback: sezonda bitmiş maç yoksa önceki sezonu bir kez çek. Yalnızca sezonu kendisi seçilen
        ("güncel sezon") çağrılar içindir: kullanıcı sezonu açıkça seçtiyse başka bir sezonun programı
        kaydedilmez.
        """
        league_name = self.config_manager.get_league_by_id(league_id) or f"League {league_id}"
        season_name = self.season_fetcher.get_season_name(league_id, season_id)
        try:
            if retry_count >= 2:
                logger.warning("Maximum number of attempts reached (%s); stopped", retry_count)
                return False

            logger.info(get_i18n().t('fetching_all_matches_for_league_season', league_name=league_name,
                                     season_name=season_name))
            results = self.fetch_all_rounds_for_season(league_id, season_id, max_round)

            if not results:
                # Hiç bitmiş maç yok: yalnızca fikstürü yayımlanmış yeni sezon (örn. PL 26/27) ya da boş sezon
                logger.warning("%s - %s: no finished match found", league_name, season_name)
                if allow_fallback and retry_count == 0:
                    season_info = self.season_fetcher.get_season_info(league_id, season_id)
                    season_year = self._parse_season_start_year(season_name or "", season_info)
                    now = datetime.datetime.now()
                    unstarted = season_year is not None and (
                        season_year > now.year or (season_year == now.year and now.month < 8))
                    prev_season = self._previous_season(league_id, season_id)
                    if prev_season:
                        reason = (f"upcoming/unstarted ({season_year})" if unstarted
                                  else "no finished matches in selected season")
                        logger.warning(f"{league_name} - {season_name}: {reason}. "
                                       f"Falling back to {prev_season.get('name', '')} (ID: {prev_season.get('id')})")
                        return self.fetch_all_matches_for_season(league_id, prev_season.get("id"), max_round,
                                                                 retry_count + 1)
                    logger.warning("%s: no previous season to fall back to", league_name)
                return False

            self._report_season(league_id, season_id, results)
            logger.info("%s - %s: %d schedule pages fetched", league_name, season_name, len(results))
            return True

        except Exception as e:
            logger.error("Schedule of %s - %s could not be fetched: %s", league_name, season_name, e)
            return False

    def fetch_matches_for_season(self, league_id: int, season_id: Optional[int] = None) -> bool:
        """
        Belirli bir lig ve sezon için maç programını çeker/günceller. Sezon kimliği verilmezse güncel sezon
        kullanılır ve yalnızca bu durumda bitmiş maçı olmayan sezon için önceki sezona geçilir.

        Tamamlanmış turlar depodan okunur (listing.ScheduleLister.cached_round); yalnızca bitmemiş maç içeren turlar
        yeniden istenir, bu yüzden bitmiş bir sezonu güncellemek ucuzdur.
        """
        try:
            auto_selected = not season_id
            if auto_selected:
                season_id = self.season_fetcher.get_current_season_id(league_id)
                if not season_id:
                    logger.error("No current season id for league %s", league_id)
                    return False

            logger.info(get_i18n().t('fetching_matches_for_season', season_id=season_id, league_id=league_id))
            return self.fetch_all_matches_for_season(league_id, int(season_id or 0), allow_fallback=auto_selected)

        except Exception as e:
            logger.error("Match schedule could not be fetched: %s", e)
            return False
