"""
Yapılandırma dosyalarını yöneten modül.
Lig bilgilerini okur ve yönetir.

Ayar getter'ları (get_data_dir, get_max_concurrent...) etkin `Settings`ten okur (sofascore_scraper/config; plan maddesi
P09). Yapılandırma dosyası (sofascore.toml) yokken her değer eskisi gibi `.env` ve ortam değişkenlerinden,
aynı kurallarla çözülür; dosya varsa onun değerleri de hesaba katılır. `.env` yazımı ve lig dosyası
(leagues.txt) işlemleri değişmedi.

Lig dosyası doğruluk kaynağı olmayı sürdürür: bugünkü gibi okunur ve yazılır. Her yüklemeden ve her
değişiklikten sonra içeriği (league_sports.json'daki sporlarla birlikte) veri dizininin `follows` tablosuna
"legacy" kaynağıyla yansıtılır (sofascore_scraper/store/follows.py; plan maddesi ST-17). Dosyalar tablodan asla yeniden
yazılmaz; ayna yazılamazsa lig işlemleri etkilenmez.
"""

import os
import dotenv
from typing import TYPE_CHECKING, Dict, List, Tuple, Optional, Set, Any
from dataclasses import dataclass

from sofascore_scraper.config import Settings
from sofascore_scraper.config import loader as settings_loader
from sofascore_scraper.exceptions import ConfigError, StorageError
from sofascore_scraper.config_files import atomic_write_text, file_lock
from sofascore_scraper import redact
from sofascore_scraper.logger import apply_log_level, get_logger
from sofascore_scraper.paths import default_league_config_path, env_file_path
from sofascore_scraper.private_files import PRIVATE_FILE_MODE, create_private_file, restrict_permissions

if TYPE_CHECKING:
    from sofascore_scraper.store import ApplyResult, FollowSpec

# .env dosyasını yükle
dotenv.load_dotenv(env_file_path())
# Etkin ayarları kur. sofascore.toml varsa burada okunur; bozuksa uygulama burada, açık bir ConfigError ile
# durur (yarım uygulanmış bir yapılandırmayla çalışmaz). Dosya yoksa ortama dokunulmaz, log yazılmaz.
settings_loader.active()

# Logger'ı alın
logger = get_logger("ConfigManager")

_SECRET_KEYS = ("PROXY_URL", "SOFA_CAPTCHA_TOKEN")
# Değişince log seviyesi çalışırken yeniden uygulanan anahtarlar
_LOG_LEVEL_KEYS = ("LOG_LEVEL", "DEBUG")


def mask_secret(key: str, value: str) -> str:
    """Log'a yazılacak değeri maskeler (proxy kimlik bilgisi, captcha token, adı gizli görünen her anahtar)."""
    if key in _SECRET_KEYS and value:
        return redact.MASK
    return redact.mask_value(key, value)


@dataclass
class League:
    """Lig bilgilerini içeren veri sınıfı."""
    id: int
    name: str


def read_league_file(path: str) -> Dict[int, str]:
    """
    leagues.txt'in ligleri (kimlik → ad), ConfigManager'ın kuralıyla (`Ad: ID` ya da eski `ID Ad`; BOM, yorum,
    geçersiz ve adsız satırlar atlanır; yinelenen kimlikte sonuncusu). Yalnızca okunur: dosya yoksa boş sözlük;
    ConfigManager gibi örnek dosya yaratmaz ve takipleri `state.db`'ye yansıtmaz (`ssc config init --from-legacy`;
    plan maddesi FX-15).
    """
    leagues: Dict[int, str] = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return leagues
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            name, league_id = ConfigManager._parse_league_line(line)
        except ValueError:
            continue
        if name:
            leagues[league_id] = name
    return leagues


class ConfigManager:
    """Lig yapılandırma dosyalarını yöneten sınıf. Singleton tasarım desenini uygular."""

    # Singleton instance
    _instance = None

    def __new__(cls, *args: Any, **kwargs: Any) -> 'ConfigManager':
        """
        Singleton deseni için yeni instance oluşturma kontrolü.

        Returns:
            ConfigManager: Tek ConfigManager instance'ı
        """
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config_path: Optional[str] = None):
        """
        ConfigManager sınıfını başlatır.

        Args:
            config_path: Yapılandırma dosyası yolu (none ise varsayılan yol kullanılır)
        """
        # Eğer zaten başlatılmışsa tekrar başlatma
        if getattr(self, '_initialized', False):
            return

        # Lig yapılandırma dosyası
        self.league_config_path = config_path or default_league_config_path()

        # Lig ve diğer yapılandırma verilerini tut
        self.leagues: Dict[int, str] = {}
        self.leagues_by_name: Dict[str, int] = {}

        # Yapılandırma dizinlerini kontrol et
        self._ensure_config_dir()

        # Lig listesi yoksa örneğinden oluşturulur, ama yalnızca eski liste takiplerin kaynağıyken (FX-23, F24)
        if not os.path.exists(self.league_config_path) and self._legacy_list_in_use():
            self._create_sample_league_config()

        # Ligleri yükle
        self._load_leagues()

        logger.info(f"Configuration manager started: {len(self.leagues)} leagues loaded ({self.league_config_path})")

        # Başlatma tamamlandı
        self._initialized = True

    def _ensure_config_dir(self) -> None:
        """
        Yapılandırma dizininin var olduğundan emin olur.

        Raises:
            OSError: Dizin oluşturulamazsa
        """
        # Lig yapılandırma dizini
        league_config_dir = os.path.dirname(self.league_config_path)
        if league_config_dir:
            os.makedirs(league_config_dir, exist_ok=True)

    @staticmethod
    def _legacy_list_in_use() -> bool:
        """
        Eski lig listesi (leagues.txt) takiplerin kaynağı mı: yapılandırma dosyası (sofascore.toml) yokken evet
        (docs/design/02-services.md, çözüm 9: dosya yokken eski dosyalar geçerlidir ve tabloya yansıtılır). Dosya
        varsa takipler ondan ve takip tablosundan gelir: yalnızca yorum satırları taşıyan bir örnek yaratılmaz
        (yedeklere giriyordu). Var olan liste her durumda okunur. Ayarlar okunamazsa eski davranış: evet.
        """
        try:
            return not settings_loader.active().config_file
        except Exception:
            return True

    def _create_sample_league_config(self) -> None:
        """
        leagues.txt yoksa config/leagues.example.txt'den (yoksa gömülü örnekten) oluşturur.

        Şablonda lig yoktur: yeni kurulum boş başlar. Eskiden "Premier League: 17" ekiliyordu; o ligin
        sporu league_sports.json'da olmadığı için ilk ekran "spor seçin" kutusuyla açılıyor, Ligler
        sayfasının karşılama durumu hiç görünmüyordu. Lig, arayüzden eklenince sporu da kaydedilir.

        Raises:
            OSError: Dosya oluşturulamazsa
        """
        example = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "leagues.example.txt")
        try:
            if os.path.exists(example):
                with open(example, "r", encoding="utf-8-sig") as f:
                    text = f.read()
            else:
                text = "# League configuration file\n# Format: League Name: ID\n#\n# Premier League: 17\n"
            atomic_write_text(self.league_config_path, text)
            logger.info(f"Example league file created: {self.league_config_path}")
        except OSError as e:
            logger.error(f"The example league file could not be created: {str(e)}")
            raise

    def _load_leagues(self) -> None:
        """
        Lig bilgilerini yapılandırma dosyasından yükler.

        Raises:
            ConfigError: Yapılandırma yüklenemezse
        """
        try:
            # Önce temizle
            self.leagues.clear()
            self.leagues_by_name.clear()

            # Ligleri metin dosyasından yükle
            self._load_leagues_from_text()
            self._mirror_follows()

            logger.info(f"{len(self.leagues)} leagues loaded")
        except Exception as e:
            error_msg = f"The league configuration could not be loaded: {str(e)}"
            logger.error(error_msg)
            raise ConfigError(error_msg) from e

    @staticmethod
    def _parse_league_line(line: str) -> Optional[Tuple[str, int]]:
        """'Ad: ID' (ad ':' içerebilir — son ':' ayırır) veya eski 'ID Ad' biçimi."""
        if ":" in line:
            name, id_str = (part.strip() for part in line.rsplit(":", 1))
            return name, int(id_str)
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise ValueError(line)
        return parts[1].strip(), int(parts[0])

    def _league_file_mtime(self) -> Optional[float]:
        try:
            return os.stat(self.league_config_path).st_mtime_ns
        except OSError:
            return None

    def _load_leagues_from_text(self) -> None:
        """Ligleri metin dosyasından yükler."""
        self._leagues_mtime = self._league_file_mtime()
        if not os.path.exists(self.league_config_path):
            # Yapılandırma dosyası kullanılırken liste olmayabilir (yaratılmaz): uyarı değildir
            level = logger.warning if self._legacy_list_in_use() else logger.debug
            level(f"League file not found: {self.league_config_path}")
            return

        try:
            # utf-8-sig: Windows editörlerinin eklediği BOM ilk lig adına karışmasın
            with open(self.league_config_path, 'r', encoding='utf-8-sig') as f:
                lines = f.readlines()

            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                try:
                    league_name, league_id = self._parse_league_line(line)
                except ValueError:
                    logger.warning(f"Invalid league line skipped: {line}")
                    continue
                if not league_name:
                    logger.warning(f"League line without a name skipped: {line}")
                    continue
                if league_id in self.leagues:
                    logger.warning(
                        f"Duplicate league id {league_id}: '{league_name}' is used instead of '{self.leagues[league_id]}'"
                    )
                    self.leagues_by_name.pop(self.leagues[league_id], None)
                self.leagues[league_id] = league_name
                self.leagues_by_name[league_name] = league_id

            logger.debug(f"{len(self.leagues)} leagues loaded from the text file")
        except Exception as e:
            logger.error(f"The leagues could not be loaded from the text file: {str(e)}")

    def _refresh_if_changed(self, mirror: bool = True) -> bool:
        """
        Başka bir süreç (CLI/web) ya da bir editör leagues.txt'i değiştirdiyse yeniden yükler ve (mirror=True
        ise) `follows` tablosuna yansıtır. Yeniden yüklendiyse True döner.
        """
        if self._league_file_mtime() == getattr(self, "_leagues_mtime", None):
            return False
        self.leagues.clear()
        self.leagues_by_name.clear()
        self._load_leagues_from_text()
        if mirror:
            self._mirror_follows()
        return True

    def _mirror_follows(self, data_dir: Optional[str] = None) -> Optional["ApplyResult"]:
        """
        Bellekteki ligleri ve league_sports.json'daki sporları `follows` tablosuna "legacy" kaynağıyla uygular.

        Yön tektir: dosyalar tabloya. Veri dizininde henüz state.db yoksa hiçbir şeye dokunulmaz (None): ayna
        bir dizini depoya çevirmez; tablo, depo kurulduktan sonraki ilk yüklemede, değişiklikte ya da iş
        başlangıcında (build_context) dolar. Depo okunamıyor ya da meşgulse uyarı yazılır ve lig işlemi
        etkilenmez: ligler dosyadan okunmaya devam eder.
        """
        try:
            # Geç içe aktarma: bu modül Store'un SQLite katmanını kendi yüklenişinde getirmez
            from sofascore_scraper.store import FollowSpec, apply_follows
            from sofascore_scraper.web import league_sports

            def desired() -> List["FollowSpec"]:
                # Yalnızca yazılacak bir tablo varsa çağrılır: depo yokken league_sports.json okunmaz
                sports = league_sports.load(self.league_config_path)
                return [
                    FollowSpec(kind="tournament", entity_id=league_id, name=league_name, sport=sports.get(league_id))
                    for league_id, league_name in list(self.leagues.items())
                ]

            result = apply_follows(data_dir or self.get_data_dir(), desired, origin="legacy")
        except Exception as e:
            # Ayna ikincil bir kayıttır: hangi nedenle olursa olsun (dolu disk, kilitli ya da daha yeni bir
            # state.db, bozuk bir ayar) yazılamaması lig listesini okuyan ya da değiştiren çağrıyı düşürmez
            level = logger.warning if isinstance(e, (StorageError, OSError)) else logger.error
            level(f"Leagues could not be mirrored into the follows table: {e}")
            return None
        if result is not None and (result.changed or result.conflicts):
            logger.debug(
                "Follows mirror (legacy): %d added, %d updated, %d removed, %d not applied",
                len(result.added), len(result.updated), len(result.removed), len(result.conflicts),
            )
            for conflict in result.conflicts:
                # Beklenen durumlar: lig yapılandırma dosyasında da var (satır "config" kaynağının) ya da
                # leagues.txt'de aynı ad iki kimlikte geçiyor (tabloda turnuva adı tekildir)
                logger.debug("Follows mirror (legacy): %s (ID: %s) not applied: %s",
                             conflict.name, conflict.entity_id, conflict.reason)
        return result

    def mirror_follows(self, data_dir: Optional[str] = None) -> Optional["ApplyResult"]:
        """
        leagues.txt'in (elle düzenlenmişse yeniden okunarak) ve league_sports.json'ın güncel halini
        `follows` tablosuna yansıtır. data_dir verilmezse yapılandırmadaki veri dizini kullanılır.
        Tablo zaten güncelse hiçbir şey yazılmaz; ayrıntılar: `_mirror_follows`.
        """
        self._refresh_if_changed(mirror=False)
        return self._mirror_follows(data_dir)

    def get_leagues(self) -> Dict[int, str]:
        """
        Tüm ligleri döndürür.

        Returns:
            Dict[int, str]: Lig ID'leri ve isimleri içeren sözlük
        """
        self._refresh_if_changed()
        return self.leagues.copy()

    def get_league_ids(self) -> Set[int]:
        """
        Tüm lig ID'lerini döndürür.

        Returns:
            Set[int]: Lig ID'leri kümesi
        """
        self._refresh_if_changed()
        return set(self.leagues.keys())

    def get_league_names(self) -> Set[str]:
        """
        Tüm lig isimlerini döndürür.

        Returns:
            Set[str]: Lig isimleri kümesi
        """
        self._refresh_if_changed()
        return set(self.leagues.values())

    def get_league_by_name(self, league_name: str) -> Optional[int]:
        """
        İsme göre lig ID'sini döndürür.

        Args:
            league_name: Lig adı

        Returns:
            Optional[int]: Lig ID'si veya bulunamazsa None
        """
        self._refresh_if_changed()
        return self.leagues_by_name.get(league_name)

    def get_league_by_id(self, league_id: int) -> Optional[str]:
        """
        ID'ye göre lig adını döndürür.

        Args:
            league_id: Lig ID'si

        Returns:
            Optional[str]: Lig adı veya bulunamazsa None
        """
        self._refresh_if_changed()
        return self.leagues.get(league_id)

    def get_league_name_by_id(self, league_id: int) -> Optional[str]:
        """
        ID'ye göre lig adını döndürür. get_league_by_id ile aynı işlevi görür.

        Args:
            league_id: Lig ID'si

        Returns:
            Optional[str]: Lig adı veya bulunamazsa None
        """
        return self.get_league_by_id(league_id)

    def get_league_id_by_name(self, league_name: str) -> Optional[int]:
        """
        İsme göre lig ID'sini döndürür. get_league_by_name ile aynı işlevi görür.

        Args:
            league_name: Lig adı

        Returns:
            Optional[int]: Lig ID'si veya bulunamazsa None
        """
        return self.get_league_by_name(league_name)

    def get_settings(self) -> Settings:
        """Etkin ayarlar (sofascore_scraper/config). Ortam değişkeni değişmişse yeni bir nesne döner."""
        return settings_loader.active_settings()

    def _number(self, key: str, env_name: str) -> Any:
        """
        Sayısal bir ayar. Ortamdaki / .env'deki değer okunamadıysa eskisi gibi her çağrıda uyarı yazılır
        (değer başka bir kaynaktan, ör. yapılandırma dosyasından geliyorsa uyarı yanlış olurdu, yazılmaz).
        """
        loaded = settings_loader.active()
        value = loaded.settings.get(key)
        if key in loaded.invalid_legacy and loaded.source(key).layer == settings_loader.LAYER_DEFAULT:
            logger.warning(f"{env_name} is not valid; using the default {value}.")
        return value

    def get_data_dir(self) -> str:
        """
        Veri dizinini döndürür.

        Returns:
            str: Yapılandırmada tanımlanan veri dizini
        """
        return self.get_settings().storage.data_dir

    def get_match_data_dir(self) -> str:
        """
        Maç verilerinin saklandığı dizini döndürür.

        Returns:
            str: Maç verilerinin saklandığı dizin
        """
        data_dir = self.get_data_dir()
        return os.path.join(data_dir, "matches")

    def get_api_base_url(self) -> str:
        """
        API temel URL'sini döndürür.

        Returns:
            str: API temel URL'si
        """
        return self.get_settings().client.base_url

    def get_use_proxy(self) -> bool:
        """
        Proxy kullanımı ayarını döndürür.

        Returns:
            bool: Proxy kullanılacaksa True, değilse False
        """
        return self.get_settings().client.use_proxy

    def get_proxy_url(self) -> str:
        """
        Proxy URL'sini döndürür.

        Returns:
            str: Proxy URL'si
        """
        return self.get_settings().client.proxy

    def get_use_color(self) -> bool:
        """
        Renk kullanımı ayarını döndürür.

        Returns:
            bool: Renk kullanılacaksa True, değilse False
        """
        return self.get_settings().display.use_color

    def get_date_format(self) -> str:
        """
        Tarih formatını döndürür.

        Returns:
            str: Tarih formatı
        """
        return self.get_settings().display.date_format

    def get_max_concurrent(self) -> int:
        """Maksimum paralel istek sayısını döndürür."""
        return self._number("client.max_concurrent", "MAX_CONCURRENT")

    def get_request_rate_limit(self) -> float:
        """Tüm süreçlerin paylaştığı istek bütçesi (istek/sn); 0 = kapalı. Bkz. sofascore_scraper/throttle.py."""
        loaded = settings_loader.active()
        if "client.rate" in loaded.invalid_legacy:
            # Geçersiz değerin uyarısını (değer başına bir kez) bütçenin kendisi yazar
            from sofascore_scraper.throttle import configured_rate

            configured_rate()
        return loaded.settings.client.rate

    def get_wait_time_min(self) -> float:
        """İstekler arası minimum bekleme süresini döndürür."""
        return self._number("client.wait_time_min", "WAIT_TIME_MIN")

    def get_wait_time_max(self) -> float:
        """İstekler arası maksimum ek bekleme süresini döndürür."""
        return self._number("client.wait_time_max", "WAIT_TIME_MAX")

    def get_request_timeout(self) -> int:
        """İstek zaman aşımı değerini döndürür."""
        return self._number("client.timeout_seconds", "REQUEST_TIMEOUT")

    def get_max_retries(self) -> int:
        """Maksimum yeniden deneme sayısını döndürür."""
        return self._number("client.retries", "MAX_RETRIES")

    def get_rate_limit_threshold_consecutive(self) -> int:
        """Arka arkaya rate-limit hatası eşiğini döndürür."""
        return self._number("breaker.rate_limit_consecutive", "RATE_LIMIT_THRESHOLD_CONSECUTIVE")

    def get_rate_limit_threshold_ratio(self) -> float:
        """Rate-limit hata oranı eşiğini döndürür."""
        return self._number("breaker.rate_limit_ratio", "RATE_LIMIT_THRESHOLD_RATIO")

    def get_server_error_threshold_consecutive(self) -> int:
        """Arka arkaya 5xx hataları için eşik döndürür."""
        return self._number("breaker.server_error_consecutive", "SERVER_ERROR_THRESHOLD_CONSECUTIVE")

    def get_language(self) -> str:
        """
        Uygulama dilini döndürür.

        Returns:
            str: Dil kodu (tr, en, vs.)
        """
        return self.get_settings().display.language

    def set_language(self, lang_code: str) -> bool:
        """
        Uygulama dilini ayarlar.

        Args:
            lang_code: Dil kodu (tr, en)

        Returns:
            bool: Başarılı olursa True
        """
        return self.update_env_variable("APP_LANGUAGE", lang_code)

    def reload_config(self) -> bool:
        """
        Yapılandırmaları yeniden yükler.

        Returns:
            bool: Başarılı olursa True, değilse False
        """
        try:
            # Mevcut ligleri temizle
            self.leagues.clear()
            self.leagues_by_name.clear()

            # Ligleri yeniden yükle
            self._load_leagues()

            try:
                # .env, overrides.json ve yapılandırma dosyası yeniden okunur. .env'deki değerler ortama
                # uygulanır, ama süreç ortamından (kabuk, docker -e, bayrak) gelen bir değerin üzerine
                # yazılmaz: eskiden load_dotenv(override=True) onu .env'deki (boş olabilen) satırla eziyordu.
                # Yapılandırma dosyası bozuksa önceki ayarlar yürürlükte kalır ve hata aşağıda loglanır.
                settings_loader.reload()
            finally:
                # .env değişmiş olabilir: maskelenecek değerler ve log seviyesi hemen güncellensin
                redact.refresh()
                apply_log_level()

            # Debug için ligleri logla
            logger.debug(f"Configuration reloaded: {len(self.leagues)} leagues")
            for league_id, league_name in self.leagues.items():
                logger.debug(f"Loaded: {league_name} (id {league_id})")

            return True
        except Exception as e:
            logger.error(f"The configuration could not be reloaded: {str(e)}")
            return False

    def add_league(self, league_name: str, league_id: int) -> bool:
        """
        Yeni bir ligi yapılandırmaya ekler.

        Args:
            league_name: Lig adı (yeni satır içeremez)
            league_id: Lig ID'si

        Returns:
            bool: Başarılı olursa True; ID/ad zaten varsa veya ad geçersizse False
        """
        league_name = league_name.strip()
        if not league_name or any(c in league_name for c in "\r\n\x00"):
            logger.warning(f"Invalid league name refused: {league_name!r}")
            return False
        changed = False
        try:
            with file_lock(self.league_config_path):
                # Kilit altında diskteki güncel hali oku: başka süreç arada eklemiş olabilir
                changed = self._refresh_if_changed(mirror=False)
                if league_id in self.leagues:
                    logger.warning(f"League id already present: {league_id}")
                    return False
                if league_name in self.leagues_by_name:
                    logger.warning(f"League name already present: {league_name}")
                    return False

                try:
                    with open(self.league_config_path, 'r', encoding='utf-8-sig') as f:
                        text = f.read()
                except FileNotFoundError:
                    text = "# League configuration file\n# Format: League Name: ID\n"
                if text and not text.endswith("\n"):
                    text += "\n"
                atomic_write_text(self.league_config_path, f"{text}{league_name}: {league_id}\n")

                self.leagues[league_id] = league_name
                self.leagues_by_name[league_name] = league_id
                self._leagues_mtime = self._league_file_mtime()
                changed = True
            logger.info(f"League added: {league_name} (id {league_id})")
            return True
        except Exception as e:
            logger.error(f"The league could not be added: {str(e)}")
            return False
        finally:
            if changed:
                # Dosya kilidi bırakıldıktan sonra: dosya yazıldı (ya da yeniden okundu), tablo ona eşitlenir
                self._mirror_follows()

    def remove_league(self, league_id: int) -> bool:
        """
        Bir ligi yapılandırmadan kaldırır.

        Args:
            league_id: Kaldırılacak ligin ID'si

        Returns:
            bool: Başarılı olursa True, değilse False
        """
        changed = False
        try:
            with file_lock(self.league_config_path):
                changed = self._refresh_if_changed(mirror=False)
                if league_id not in self.leagues:
                    logger.warning(f"League to remove not found: {league_id}")
                    return False
                league_name = self.leagues[league_id]

                with open(self.league_config_path, 'r', encoding='utf-8-sig') as f:
                    lines = f.readlines()
                new_lines = []
                for line in lines:
                    stripped = line.strip()
                    if stripped and not stripped.startswith('#'):
                        try:
                            if self._parse_league_line(stripped)[1] == league_id:
                                continue
                        except ValueError:
                            pass
                    new_lines.append(line)
                atomic_write_text(self.league_config_path, "".join(new_lines))

                del self.leagues[league_id]
                self.leagues_by_name.pop(league_name, None)
                self._leagues_mtime = self._league_file_mtime()
                changed = True
            logger.info(f"League removed: {league_name} (id {league_id})")
            return True
        except Exception as e:
            logger.error(f"The league could not be removed: {str(e)}")
            return False
        finally:
            if changed:
                self._mirror_follows()

    def update_env_variable(self, key: str, value: str) -> bool:
        """
        Çevre değişkenini günceller ve .env dosyasına kaydeder.

        Args:
            key: Değişken adı
            value: Yeni değer

        Returns:
            bool: Başarılı olursa True, değilse False
        """
        if any(c in value for c in "\r\n\x00"):
            # .env satır tabanlı: yeni satır başka bir değişken enjekte eder
            logger.error(f"Environment variable refused (control character): {key}")
            return False
        try:
            os.environ[key] = value
            # set_key yalnızca ilgili satırı değiştirir; yorumlar ve diğer satırlar korunur
            env_path = env_file_path()
            # .env gizli değer taşır (proxy parolası, belirteçler): yalnızca sahibince okunur (0600).
            # set_key dosyayı yeniden yazar; python-dotenv sürümüne göre izinler korunmayabilir.
            create_private_file(env_path)
            dotenv.set_key(env_path, key, value)
            settings_loader.note_dotenv_write(key, value)
            restrict_permissions(env_path, PRIVATE_FILE_MODE)
            redact.refresh()
            if key in _LOG_LEVEL_KEYS:
                # Seviye yeniden başlatmayı beklemeden uygulanır
                apply_log_level()
            logger.info(f"Environment variable updated: {key}={mask_secret(key, value)}")
            return True
        except Exception as e:
            logger.error(f"The environment variable could not be updated: {str(e)}")
            return False


def mirror_league_follows(league_config_path: str) -> None:
    """
    league_sports.json değişti (sofascore_scraper/web/league_sports.py): sporlar `follows` tablosuna da yansısın.

    Lig adlarını ConfigManager bilir; bu yüzden ayna onun üzerinden yapılır. Süreçte kurulu bir ConfigManager
    yoksa ya da başka bir lig dosyasına bakıyorsa yapılacak bir şey yoktur: sporlar, o dosyanın
    ConfigManager'ı ligleri bir sonraki kez yansıttığında tabloya girer.
    """
    manager = ConfigManager._instance
    if manager is None or not getattr(manager, "_initialized", False):
        return

    def _normal(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    if _normal(manager.league_config_path) == _normal(league_config_path):
        manager.mirror_follows()
