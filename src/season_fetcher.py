"""
SofaScore API'sinden lig sezonlarını çeken modül.

Saklanan sezon listeleri ve "bu sezonun maç listesi indirilmiş mi" sorusu deponun kataloğundan okunur
(src/services/tournaments.py; plan maddesi RD-5): bir ligin birden çok sezon listesi dosyası varsa adı ne
olursa olsun en yenisi geçerlidir ve web uç noktaları da aynı listeyi görür. Listeyi yazan hâlâ bu modüldür
(`_save_seasons_json`); yazdıktan sonra kataloğu güncelleyen kancayı çağırır.
"""

import os
from typing import TYPE_CHECKING, Dict, List, Optional, Any, Tuple
import datetime
import re

from src.client import base_url
from src.config_manager import ConfigManager
from src.exceptions import DataParsingError, SofaScoreScraperError
from src.utils import make_api_request, ensure_directory

from src.fsutil import atomic_write_json
# Gölge kip (docs/design/01-storage.md 3.5): her yazmadan sonra Store'un bir `shadow_*` kancası çağrılır ve
# katalog yazılanı diskten yeniden dizinler. Okumalar da aynı depodan yapılır (`open_store`). Paket kökü
# üzerinden: kancalar ve cephe ilk çağrıda yüklenir.
from src import store as store_hooks
from src.logger import get_logger
from src.paths import seasons_file
from src.services import tournaments

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
            league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")
            logger.error(f"{league_name} için sezon verileri çekilemedi: {e}")
            return []

    def fetch_seasons_checked(self, league_id: int, max_retries: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        fetch_seasons_for_league gibi, ama başarısızlığı boş listeyle gizlemez: istek katmanının
        tipli hatasını fırlatır (APIError / RateLimitError / ResourceNotFoundError / NetworkError),
        yanıt sezon listesi içermiyorsa DataParsingError. Boş liste yalnızca SofaScore gerçekten
        boş bir sezon listesi döndürdüğünde gelir. Nedeni kullanıcıya gösteren çağıranlar içindir.
        """
        league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")
        logger.info(f"{league_name} (ID: {league_id}) için sezonlar çekiliyor...")

        url = f"{self.base_url}/unique-tournament/{league_id}/seasons"
        data = make_api_request(url, max_retries=max_retries, raise_errors=True)

        if not isinstance(data, dict) or not isinstance(data.get("seasons"), list):
            raise DataParsingError(f"Sezon yanıtı beklenen biçimde değil: {url}")

        seasons = data.get("seasons", [])

        # Sezon verilerini kaydet
        self._save_seasons_json(league_id, data)

        # Sezon verilerini global sözlüğe kaydet
        self.league_seasons[league_id] = seasons

        logger.info(f"{league_name} için {len(seasons)} sezon bulundu")
        return seasons

    # Senkron wrapper
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
            logger.error(f"Lig ID {league_id} için sezon bulunamadı!")
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
        logger.debug(f"Lig ID {league_id} için sezonlar (sıralı): {[(s.get('id'), s.get('name', ''), s.get('year', '')) for s in sorted_seasons[:5]]}")

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

            logger.debug(f"Sezon {season_name} (ID: {season_id}, Yıl: {season_year_str}): Tür = {'active' if season in active_seasons else 'future' if season in future_seasons else 'past'}")

            # Maç listesi indirilmiş mi: katalogdaki program sayfaları (dizin adına bakılmaz)
            season["match_count"], season["has_matches"] = self._downloaded_matches(league_id, season_id)

            if season["has_matches"]:
                logger.info(f"Sezon {season_name} (ID: {season_id}) için {season['match_count']} maç dosyası bulundu")
            elif season in active_seasons:
                logger.warning(f"Aktif sezon {season_name} (ID: {season_id}) için maç dosyası bulunamadı")

        # En iyi sezon seçimi kriterlerini uygula
        # 1. Aktif sezonlar arasında maç dosyası olan varsa, onu seç
        active_seasons_with_matches = [s for s in active_seasons if s.get("has_matches", False)]
        if active_seasons_with_matches:
            selected_season = active_seasons_with_matches[0]  # En yeni olanı al
            logger.info(f"Aktif sezonlar arasından maç dosyası bulunan en yeni sezon seçildi: {selected_season.get('name')} (ID: {selected_season.get('id')})")
            return selected_season.get("id")

        # 2. Aktif sezon yoksa veya hiçbirinde maç yoksa, en son tamamlanan sezonu seç
        past_seasons_with_matches = [s for s in past_seasons if s.get("has_matches", False)]
        if past_seasons_with_matches:
            selected_season = past_seasons_with_matches[0]  # En yeni olanı al
            logger.info(f"Geçmiş sezonlar arasından maç dosyası bulunan en yeni sezon seçildi: {selected_season.get('name')} (ID: {selected_season.get('id')})")
            return selected_season.get("id")

        # 3. Hiçbir sezonda maç dosyası yoksa, aktif sezonları tercih et
        if active_seasons:
            selected_season = active_seasons[0]  # En yeni aktif sezonu al
            logger.info(f"Hiçbir sezonda maç dosyası bulunamadı, en yeni aktif sezon seçildi: {selected_season.get('name')} (ID: {selected_season.get('id')})")
            return selected_season.get("id")

        # 4. Aktif sezon yoksa, tüm sezonlar içinden en yenisini al
        selected_season = sorted_seasons[0]
        logger.info(f"Aktif veya maç dosyası olan sezon bulunamadı, en yeni sezon seçildi: {selected_season.get('name')} (ID: {selected_season.get('id')})")
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
                logger.warning(f"get_season_name - Lig ID integer değil: {league_id}")
                league_id = int(league_id) if str(league_id).isdigit() else 0

            # Sezon ID'si integer mi kontrol et
            if not isinstance(season_id, int):
                logger.warning(f"get_season_name - Sezon ID integer değil: {season_id}")
                season_id = int(season_id) if str(season_id).isdigit() else 0

            # Lig ve sezon verilerini kontrol et
            for season in self._stored_seasons(league_id):
                if season and isinstance(season, dict) and season.get("id") == season_id:
                    return season.get("name", f"Season_{season_id}")

            return f"Season_{season_id}"

        except Exception as e:
            logger.warning(f"Sezon adı alınırken hata: {str(e)}")
            return f"Season_{season_id}"

    def preferred_download_season_id(self, league_id: int) -> int:
        """Prefer 2nd-newest season — newest is often fixtures-only / not started yet."""
        seasons = self.get_seasons_for_league(league_id) or []
        if not seasons:
            return 0
        sorted_seasons = sorted(
            seasons,
            key=lambda s: self._get_sortable_year_value(s.get("year", "0")),
            reverse=True,
        )
        pick = sorted_seasons[1] if len(sorted_seasons) > 1 else sorted_seasons[0]
        return int(pick.get("id") or 0)

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
        if not seasons:
            return requested_id

        known = {int(s["id"]) for s in seasons if s.get("id") is not None}
        if requested_id in known:
            return requested_id

        preferred = self.preferred_download_season_id(league_id)
        logger.warning(
            f"Stale season id {requested_id} for league {league_id} "
            f"(not in refreshed list). Using {preferred}."
        )
        return preferred or requested_id

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
        Bir lig için çekilen sezon verilerini JSON dosyası olarak kaydeder.

        Args:
            league_id: Lig ID'si
            data: API'den alınan sezon verileri
        """
        file_path = seasons_file(self.data_dir, league_id, self.config_manager.get_league_by_id(league_id))
        try:
            atomic_write_json(file_path, data)
            store_hooks.shadow_season_lists(self.data_dir)
            logger.info(f"Sezon verileri JSON olarak kaydedildi: {file_path}")
        except Exception as e:
            logger.error(f"JSON dosyası kaydedilirken hata: {str(e)}")

    def _get_sortable_year_value(self, year_str: str) -> float:
        """
        Sezon yılı dizesini sıralanabilir bir sayısal değere dönüştürür.
        "24/25", "2024/2025", "2024", "98/99" gibi formatları işler.

        Args:
            year_str: Sezon yılı dizesi

        Returns:
            float: Yılı temsil eden sıralanabilir bir değer (yüksek = daha yeni)
        """
        if not year_str or year_str == '0':
            return 0.0

        # Yıl aralıklarını işle (örn. "24/25" veya "2024/2025")
        if '/' in year_str:
            parts = year_str.split('/')
            start_year = parts[0].strip()
            end_year = parts[1].strip() if len(parts) > 1 else ""

            # 2 basamaklı yılları işle
            if len(start_year) == 2 and len(end_year) == 2:
                start_int = int(start_year)
                end_int = int(end_year)

                # Eğer ilk yıl ikinci yıldan büyükse (örn. 99/00), bu bir yüzyıl geçişidir
                if start_int > end_int:
                    # Yüzyıl geçişi: 99/00 -> 2000 (yeni yüzyılı kullan)
                    return 2000.0 + float(end_int)
                elif start_int < 50:
                    # 2000'ler (örn: 20/21 -> 2020)
                    return 2000.0 + float(start_int)
                else:
                    # 1900'ler (örn: 98/99 -> 1998)
                    return 1900.0 + float(start_int)
            # 4 basamaklı yılları işle
            elif len(start_year) == 4:
                return float(start_year)
            else:
                try:
                    # Diğer formatlar
                    return float(start_year)
                except ValueError:
                    return 0.0

        # Tek yılları işle (örn. "2024")
        try:
            return float(year_str)
        except ValueError:
            return 0.0

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

        logger.warning(f"Sezon bilgisi bulunamadı: League {league_id}, Season {season_id}")
        return None
