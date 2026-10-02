"""
SofaScore API'sinden lig sezonlarını çeken modül (uyumluluk sarmalayıcısı; plan maddesi P14).

Sezon listesinin çekilmesi ve saklanması src/services/listing.py'dedir: eşitleme servisi `list_seasons` ile
tipli sonucu (`ListingResult`) alır; liste getirme boru hattında çekilir. `fetch_seasons_for_league` (terminal
menüsü; başarısızlıkta boş liste) ve `fetch_seasons_checked` (web'in "sezonları yenile" uç noktası; tipli hata)
eski yüzlerdir ve istek katmanının senkron yolunu kullanır.

Saklanan sezon listeleri ve "bu sezonun maç listesi indirilmiş mi" sorusu deponun kataloğundan okunur
(src/services/tournaments.py; plan maddesi RD-5): bir ligin birden çok sezon listesi dosyası varsa adı ne
olursa olsun en yenisi geçerlidir ve web uç noktaları da aynı listeyi görür. Çekilen liste Store'a yazılır
(`_save_seasons_json`, `EntityStore.put`; plan maddesi ST-22): `v3/tournaments/<lig>/seasons.json.gz`. Yazma
kataloğu kendisi günceller; v3 listesi olan ligin eski `seasons/*_seasons.json` dosyası yerinde kalır ama
okunmaz.
"""

import os
from typing import TYPE_CHECKING, Dict, List, Optional, Any, Tuple
import datetime
import re

from src.client import base_url
from src.config_manager import ConfigManager
from src.exceptions import DataParsingError, SofaScoreScraperError
from src.utils import make_api_request, ensure_directory

# Okumalar ve yazmalar aynı depodan yapılır (`open_store`). Paket kökü üzerinden: cephe ilk çağrıda yüklenir.
from src import store as store_hooks
from src.logger import get_logger
from src.services import listing, tournaments

if TYPE_CHECKING:
    from src.store import Store

logger = get_logger("SeasonFetcher")

class SeasonFetcher:
    """SofaScore API'sinden lig sezonlarını çeken ve yöneten sınıf."""

    def __init__(self, config_manager: ConfigManager, data_dir: str = "data"):
        """
        SeasonFetcher sınıfını başlatır. Saklanan sezon listeleri burada yüklenmez: ilk kullanıldıklarında
        katalogdan okunurlar (`league_seasons`), böylece kurucu depoyu açmaz ve `.meta/` altında bir şey yaratmaz.

        Args:
            config_manager: Lig yapılandırmalarını yöneten ConfigManager örneği
            data_dir: Verilerin kaydedileceği ana dizin
        """
        self.config_manager = config_manager
        self.data_dir = data_dir
        self.seasons_dir = os.path.join(data_dir, "seasons")
        self.base_url = base_url()

        # Veri dizinlerinin var olduğundan emin ol
        ensure_directory(self.data_dir)
        ensure_directory(self.seasons_dir)

        # Sezon verilerini saklamak için sözlük; None: henüz yüklenmedi (bkz. league_seasons)
        self._league_seasons: Optional[Dict[int, List[Dict[str, Any]]]] = None

        logger.info("SeasonFetcher started")

    @property
    def league_seasons(self) -> Dict[int, List[Dict[str, Any]]]:
        """
        {lig kimliği: sezonlar}: saklanan sezon listeleri. İlk erişimde katalogdan yüklenir
        (`_load_existing_season_data`), sonra bu nesnenin çektiği listelerle güncellenir.
        """
        if self._league_seasons is None:
            self._league_seasons = self._load_existing_season_data()
        return self._league_seasons

    @league_seasons.setter
    def league_seasons(self, value: Dict[int, List[Dict[str, Any]]]) -> None:
        self._league_seasons = value

    def _store(self) -> "Store":
        """Veri dizininin deposu (süreçte dizin başına tek nesne); açılamazsa StoreError fırlar."""
        return store_hooks.open_store(self.data_dir)

    def _league_name(self, league_id: int) -> Optional[str]:
        """Ligin yapılandırmadaki adı: diskteki dosya ve dizin adları bu addan kurulur."""
        name = self.config_manager.get_league_by_id(league_id)
        return name if isinstance(name, str) else None

    def _read_seasons(self, league_id: int) -> Optional[List[Dict[str, Any]]]:
        """Ligin saklanan sezon listesi (katalogdan); listesi yoksa None. Depolama hatası çağırana çıkar."""
        return tournaments.seasons_of(self._store(), league_id, name=self._league_name(league_id))

    def _stored_seasons(self, league_id: int) -> List[Dict[str, Any]]:
        """
        Ligin bellekteki sezon listesi. Toplu yüklemede olmayan lig (listesi yüklemeden sonra başka bir süreçte
        yazılmış olabilir) kimliğiyle bir kez daha sorulur; listesi yoksa ya da okunamıyorsa boş liste.
        """
        cache = self.league_seasons
        seasons = cache.get(league_id)
        if seasons is None:
            try:
                seasons = self._read_seasons(league_id)
            except Exception as e:
                logger.debug("Stored season list of league %s could not be read: %s", league_id, e)
                seasons = None
            if seasons:
                cache[league_id] = seasons
        return seasons or []

    def fetch_seasons_for_league(self, league_id: int) -> List[Dict[str, Any]]:
        """
        Belirli bir lig için tüm sezonları çeker.

        Args:
            league_id: Lig ID'si

        Returns:
            List[Dict[str, Any]]: Sezon bilgilerini içeren liste (çekilemediyse boş liste)
        """
        try:
            return self.fetch_seasons_checked(league_id)
        except SofaScoreScraperError as e:
            league_name = self.config_manager.get_leagues().get(league_id, f"League {league_id}")
            logger.error("Season list of %s could not be fetched: %s", league_name, e)
            return []

    def fetch_seasons_checked(self, league_id: int, max_retries: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        fetch_seasons_for_league gibi, ama başarısızlığı boş listeyle gizlemez: istek katmanının
        tipli hatasını fırlatır (APIError / RateLimitError / ResourceNotFoundError / NetworkError),
        yanıt sezon listesi içermiyorsa DataParsingError. Boş liste yalnızca SofaScore gerçekten
        boş bir sezon listesi döndürdüğünde gelir. Nedeni kullanıcıya gösteren çağıranlar içindir.
        """
        league_name = self.config_manager.get_leagues().get(league_id, f"League {league_id}")
        logger.info("Fetching the season list of %s (id %s)", league_name, league_id)

        url = f"{self.base_url}/unique-tournament/{league_id}/seasons"
        data = make_api_request(url, max_retries=max_retries, raise_errors=True)

        seasons = listing.season_list_of(data)
        if seasons is None:
            raise DataParsingError(f"Season list response has an unexpected shape: {url}")

        # Sezon verilerini kaydet
        self._save_seasons_json(league_id, data)

        # Sezon verilerini global sözlüğe kaydet
        self.league_seasons[league_id] = seasons

        logger.info("%s: %d seasons found", league_name, len(seasons))
        return seasons

    def list_seasons(self, league_id: int, *, max_age: Optional[float] = None) -> "listing.ListingResult":
        """
        Ligin sezon listesi, getirme boru hattında: tipli sonuç (`ok`; `failed` ve nedeni; `skipped` / `breaker`
        ya da `fresh`). max_age: saklanan liste bu kadar saniyeden gençse istenmez. Çekilen liste Store'a yazılır
        ve bu nesnenin belleğindeki listeyi de günceller.
        """
        from src.utils import FETCH_ONLY_FINISHED

        service = listing.ListingService(self._store(), only_finished=bool(FETCH_ONLY_FINISHED))
        result = service.season_list(int(league_id), max_age=max_age)
        if result.ok and result.seasons is not None:
            self.league_seasons[int(league_id)] = result.seasons
        return result

    def get_current_season_id(self, league_id) -> int:
        """
        Belirli bir lig için en güncel sezon ID'sini döndürür.
        Eğer en güncel sezon henüz başlamamışsa, bir önceki aktif sezonu döndürür.

        Args:
            league_id: Lig ID'si

        Returns:
            int: Güncel sezon ID'si
        """
        seasons = self.get_seasons_for_league(league_id)
        if not seasons:
            logger.error("No season found for league %s", league_id)
            return 0

        # Şimdiki tarih
        current_date = datetime.datetime.now()
        current_year = current_date.year
        current_month = current_date.month

        # Sezonları yıl değerine göre sırala
        sorted_seasons = sorted(
            seasons,
            key=lambda s: self._get_sortable_year_value(s.get("year", "0")),
            reverse=True  # En yeni sezon en üstte
        )

        # Kontrol için listeyi logla
        logger.debug("Seasons of league %s (sorted): %s", league_id,
                     [(s.get('id'), s.get('name', ''), s.get('year', '')) for s in sorted_seasons[:5]])

        # Aktif veya geçmiş sezonları değerlendir
        active_seasons = []      # Aktif sezonlar (güncel yıl ve önceki yıl bazen)
        past_seasons = []        # Geçmiş sezonlar
        future_seasons = []      # Gelecek sezonlar

        # Önce tüm sezonları kategorize et
        for idx, season in enumerate(sorted_seasons):
            season_id = season.get("id")
            season_name = season.get("name", "")
            season_year_str = season.get("year", "")

            # Sezon yılını çıkar
            season_year = None
            if season_year_str:
                if '/' in season_year_str:
                    # "2024/25" veya "2024/2025" formatı
                    start_year = season_year_str.split('/')[0].strip()
                    if len(start_year) == 4:
                        season_year = int(start_year)
                    elif len(start_year) == 2:
                        # 2 haneli yıl (örn. "24/25")
                        season_year = 2000 + int(start_year)
                else:
                    # Tek yıl formatı: "2024"
                    try:
                        season_year = int(season_year_str)
                    except (ValueError, TypeError):
                        pass

            # Sezon adından yıl çıkarmaya çalış
            if not season_year and season_name:
                year_matches = re.findall(r'20\d\d', season_name)
                if year_matches:
                    try:
                        season_year = int(year_matches[0])
                    except (ValueError, TypeError):
                        pass

            # Sezon tipi belirle (gelecek, aktif, geçmiş)
            if season_year:
                if season_year > current_year:
                    future_seasons.append(season)
                elif season_year == current_year:
                    active_seasons.append(season)
                elif season_year == current_year - 1:
                    if current_month <= 6:  # Yılın ilk yarısındaysak, önceki sezon da aktif olabilir
                        active_seasons.append(season)
                    else:
                        past_seasons.append(season)
                else:
                    past_seasons.append(season)
            else:
                # Yıl belirlenemezse mevcut en yeni sezondur
                if idx < 2:  # İlk iki sıradaki sezonları aktif kabul et
                    active_seasons.append(season)
                else:
                    past_seasons.append(season)

            logger.debug("Season %s (id %s, year %s): %s", season_name, season_id, season_year_str,
                         'active' if season in active_seasons else 'future' if season in future_seasons else 'past')

            # Maç listesi indirilmiş mi: katalogdaki program sayfaları (dizin adına bakılmaz)
            season["match_count"], season["has_matches"] = self._downloaded_matches(league_id, season_id)

            if season["has_matches"]:
                logger.info("Season %s (id %s): %s schedule pages stored", season_name, season_id, season['match_count'])
            elif season in active_seasons:
                logger.warning("Active season %s (id %s) has no stored schedule", season_name, season_id)

        # En iyi sezon seçimi kriterlerini uygula
        # 1. Aktif sezonlar arasında maç dosyası olan varsa, onu seç
        active_seasons_with_matches = [s for s in active_seasons if s.get("has_matches", False)]
        if active_seasons_with_matches:
            selected_season = active_seasons_with_matches[0]  # En yeni olanı al
            logger.info("Chose the newest active season with a stored schedule: %s (id %s)",
                        selected_season.get('name'), selected_season.get('id'))
            return selected_season.get("id")

        # 2. Aktif sezon yoksa veya hiçbirinde maç yoksa, en son tamamlanan sezonu seç
        past_seasons_with_matches = [s for s in past_seasons if s.get("has_matches", False)]
        if past_seasons_with_matches:
            selected_season = past_seasons_with_matches[0]  # En yeni olanı al
            logger.info("Chose the newest past season with a stored schedule: %s (id %s)",
                        selected_season.get('name'), selected_season.get('id'))
            return selected_season.get("id")

        # 3. Hiçbir sezonda maç dosyası yoksa, aktif sezonları tercih et
        if active_seasons:
            selected_season = active_seasons[0]  # En yeni aktif sezonu al
            logger.info("No season has a stored schedule; chose the newest active season: %s (id %s)",
                        selected_season.get('name'), selected_season.get('id'))
            return selected_season.get("id")

        # 4. Aktif sezon yoksa, tüm sezonlar içinden en yenisini al
        selected_season = sorted_seasons[0]
        logger.info("No active season and no stored schedule; chose the newest season: %s (id %s)",
                    selected_season.get('name'), selected_season.get('id'))
        return selected_season.get("id")

    def get_season_name(self, league_id: int, season_id: int) -> str:
        """
        Return season name for a specific league and season ID.

        Args:
            league_id: Lig ID'si
            season_id: Sezon ID'si

        Returns:
            str: Sezon adı veya bulunamazsa varsayılan bir değer
        """
        try:
            # Lig ID'si integer mi kontrol et
            if not isinstance(league_id, int):
                logger.warning("get_season_name: league id is not an integer: %r", league_id)
                league_id = int(league_id) if str(league_id).isdigit() else 0

            # Sezon ID'si integer mi kontrol et
            if not isinstance(season_id, int):
                logger.warning("get_season_name: season id is not an integer: %r", season_id)
                season_id = int(season_id) if str(season_id).isdigit() else 0

            # Lig ve sezon verilerini kontrol et
            for season in self._stored_seasons(league_id):
                if season and isinstance(season, dict) and season.get("id") == season_id:
                    return season.get("name", f"Season_{season_id}")

            return f"Season_{season_id}"

        except Exception as e:
            logger.warning("Season name could not be read: %s", e)
            return f"Season_{season_id}"

    def preferred_download_season_id(self, league_id: int) -> int:
        """Yeniden eskiye ikinci sezon: en yenisi çoğu zaman yalnızca fikstürdür (listing.preferred_season_id)."""
        return listing.preferred_season_id(self.get_seasons_for_league(league_id) or [])

    def resolve_season_id(self, league_id: int, requested_id: int) -> int:
        """
        Map a possibly stale UI season id onto the live SofaScore season list.
        SofaScore occasionally retires season ids (e.g. 77559 → 77806).
        """
        try:
            requested_id = int(requested_id)
            league_id = int(league_id)
        except (TypeError, ValueError):
            return int(requested_id or 0)

        seasons = self.get_seasons_for_league(league_id) or []
        return listing.resolve_season_id(seasons, requested_id, lambda: self.preferred_download_season_id(league_id))

    def _load_existing_season_data(self) -> Dict[int, List[Dict[str, Any]]]:
        """
        Daha önce kaydedilmiş sezon listelerini katalogdan yükler: sezon listesi saklanan her lig için
        {lig kimliği: sezonlar} (yapılandırılmamış ligler dahil). Dosya seçimi `tournaments.seasons_of`
        kuralıdır (en yeni dosya; `league_seasons.csv` yalnızca JSON listesi olmayan lig için). Okunamazsa
        boş sözlük.
        """
        try:
            leagues = self.config_manager.get_leagues()
            names = dict(leagues) if isinstance(leagues, dict) else {}
            loaded = tournaments.season_lists(self._store(), names)
        except Exception as e:
            logger.error("Stored season lists could not be loaded: %s", e)
            return {}

        total_seasons = sum(len(seasons) for seasons in loaded.values())
        if total_seasons > 0:
            logger.info("Season lists loaded from the catalog: %d league(s), %d season(s)", len(loaded), total_seasons)
        else:
            logger.info("No stored season list found")
        return loaded

    def _downloaded_matches(self, league_id: int, season_id: Any) -> Tuple[int, bool]:
        """
        (saklanan program sayfası sayısı, sezonun maç listesi indirilmiş mi), katalogdan. Sezonun dizini hangi
        adla yazılmış olursa olsun görülür; sayfası kalmamış ama özet dosyasından bilinen sezon da "indirilmiş"
        sayılır. Kimliği olmayan sezon öğesi için (0, False).
        """
        if isinstance(season_id, bool) or not isinstance(season_id, int):
            return 0, False
        store = self._store()
        pages = tournaments.schedule_pages(store, league_id, season_id)
        return pages, pages > 0 or tournaments.has_matches(store, league_id, season_id)

    def _save_seasons_json(self, league_id: int, data: Dict[str, Any]):
        """
        Bir lig için çekilen sezon listesini Store'a yazar: turnuvanın `seasons` dilimi
        (`v3/tournaments/<lig>/seasons.json.gz`), SofaScore'un yanıtı olduğu gibi. Boş liste de saklanır
        (durumu `empty`). Kaydedilemezse hata günlüğe yazılır, çağırana çıkmaz (eski davranış).

        Args:
            league_id: Lig ID'si
            data: API'den alınan sezon verileri
        """
        try:
            count = listing.store_season_list(self._store(), int(league_id), data if isinstance(data, dict) else {})
            logger.info(f"Season list of league {league_id} stored ({count} seasons)")
        except Exception as e:
            logger.error(f"Season list of league {league_id} could not be stored: {e}")

    def _get_sortable_year_value(self, year_str: str) -> float:
        """Sezon yılı dizesi → sıralanabilir sayı, yüksek = daha yeni (listing.sortable_year)."""
        return listing.sortable_year(year_str)

    def get_seasons_for_league(self, league_id: int) -> List[Dict[str, Any]]:
        """
        Belirli bir lig için kayıtlı tüm sezonları döndürür: katalogdaki sezon listesi yükü, SofaScore'un
        verdiği sırayla. Ligin birden çok sezon listesi dosyası varsa en yenisi geçerlidir; adında kimlik
        olmayan dosya (`<ad>_seasons.json`) ligin yapılandırmadaki adıyla bulunur.

        Args:
            league_id: Lig ID

        Returns:
            List[Dict[str, Any]]: Sezon verileri listesi (listesi yoksa ya da okunamıyorsa boş liste)
        """
        try:
            seasons = self._read_seasons(league_id)
        except Exception as e:
            logger.error("Stored season list of league %s could not be read: %s", league_id, e)
            return []
        if seasons is None:
            logger.warning("No stored season list for league %s", league_id)
            return []
        return seasons

    def get_season_info(self, league_id: int, season_id: int) -> Optional[Dict[str, Any]]:
        """
        Belirli bir lig ve sezon ID'si için sezon bilgisini döndürür.

        Args:
            league_id: Lig ID
            season_id: Sezon ID

        Returns:
            Optional[Dict[str, Any]]: Sezon bilgisi veya None
        """
        seasons = self.get_seasons_for_league(league_id)

        # Sezon ID'sine göre sezon bilgisini bul
        for season in seasons:
            if season.get("id") == season_id:
                return season

        logger.warning("No season info: league %s, season %s", league_id, season_id)
        return None
