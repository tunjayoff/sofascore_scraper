"""
Yapılandırma dosyalarını yöneten modül.
Lig bilgilerini okur ve yönetir.

Ayar getter'ları (get_data_dir, get_max_concurrent...) etkin `Settings`ten okur (src/config; plan maddesi
P09). Yapılandırma dosyası (sofascore.toml) yokken her değer eskisi gibi `.env` ve ortam değişkenlerinden,
aynı kurallarla çözülür; dosya varsa onun değerleri de hesaba katılır. `.env` yazımı ve lig dosyası
(leagues.txt) işlemleri değişmedi.
"""

import os
import dotenv
from typing import Dict, Tuple, Optional, Set, Any
from dataclasses import dataclass

from src.config import Settings
from src.config import loader as settings_loader
from src.exceptions import ConfigError
from src.fsutil import atomic_write_text, file_lock
from src import redact
from src.logger import apply_log_level, get_logger
from src.paths import default_league_config_path, env_file_path
from src.private_files import PRIVATE_FILE_MODE, create_private_file, restrict_permissions

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

        # Yapılandırma dosyaları yoksa örnek dosyaları oluştur
        if not os.path.exists(self.league_config_path):
            self._create_sample_league_config()

        # Ligleri yükle
        self._load_leagues()

        logger.info(f"Yapılandırma yöneticisi başlatıldı: {len(self.leagues)} lig yüklendi ({self.league_config_path})")

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
            logger.info(f"Örnek lig yapılandırma dosyası oluşturuldu: {self.league_config_path}")
        except OSError as e:
            logger.error(f"Örnek lig yapılandırma dosyası oluşturulamadı: {str(e)}")
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

            logger.info(f"{len(self.leagues)} lig yapılandırması yüklendi")
        except Exception as e:
            error_msg = f"Lig yapılandırması yüklenirken hata: {str(e)}"
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
            logger.warning(f"Lig yapılandırma dosyası bulunamadı: {self.league_config_path}")
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
                    logger.warning(f"Geçersiz lig satırı atlandı: {line}")
                    continue
                if not league_name:
                    logger.warning(f"Adsız lig satırı atlandı: {line}")
                    continue
                if league_id in self.leagues:
                    logger.warning(
                        f"Yinelenen lig ID {league_id}: '{self.leagues[league_id]}' yerine '{league_name}' kullanılıyor"
                    )
                    self.leagues_by_name.pop(self.leagues[league_id], None)
                self.leagues[league_id] = league_name
                self.leagues_by_name[league_name] = league_id

            logger.debug(f"Metin dosyasından {len(self.leagues)} lig yüklendi")
        except Exception as e:
            logger.error(f"Metin dosyasından ligler yüklenirken hata: {str(e)}")

    def _refresh_if_changed(self) -> None:
        """Başka bir süreç (CLI/web) leagues.txt'i değiştirdiyse yeniden yükle."""
        if self._league_file_mtime() != getattr(self, "_leagues_mtime", None):
            self.leagues.clear()
            self.leagues_by_name.clear()
            self._load_leagues_from_text()

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
        """Etkin ayarlar (src/config). Ortam değişkeni değişmişse yeni bir nesne döner."""
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
        """Tüm süreçlerin paylaştığı istek bütçesi (istek/sn); 0 = kapalı. Bkz. src/throttle.py."""
        loaded = settings_loader.active()
        if "client.rate" in loaded.invalid_legacy:
            # Geçersiz değerin uyarısını (değer başına bir kez) bütçenin kendisi yazar
            from src.throttle import configured_rate

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
            logger.debug(f"Yapılandırma yeniden yüklendi: {len(self.leagues)} lig bulundu")
            for league_id, league_name in self.leagues.items():
                logger.debug(f"Yüklendi: {league_name} (ID: {league_id})")

            return True
        except Exception as e:
            logger.error(f"Yapılandırma yeniden yüklenemedi: {str(e)}")
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
            logger.warning(f"Geçersiz lig adı reddedildi: {league_name!r}")
            return False
        try:
            with file_lock(self.league_config_path):
                # Kilit altında diskteki güncel hali oku: başka süreç arada eklemiş olabilir
                self._refresh_if_changed()
                if league_id in self.leagues:
                    logger.warning(f"Lig ID zaten var: {league_id}")
                    return False
                if league_name in self.leagues_by_name:
                    logger.warning(f"Lig adı zaten var: {league_name}")
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
            logger.info(f"Lig eklendi: {league_name} (ID: {league_id})")
            return True
        except Exception as e:
            logger.error(f"Lig eklenirken hata: {str(e)}")
            return False

    def remove_league(self, league_id: int) -> bool:
        """
        Bir ligi yapılandırmadan kaldırır.

        Args:
            league_id: Kaldırılacak ligin ID'si

        Returns:
            bool: Başarılı olursa True, değilse False
        """
        try:
            with file_lock(self.league_config_path):
                self._refresh_if_changed()
                if league_id not in self.leagues:
                    logger.warning(f"Kaldırılacak lig bulunamadı: {league_id}")
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
            logger.info(f"Lig kaldırıldı: {league_name} (ID: {league_id})")
            return True
        except Exception as e:
            logger.error(f"Lig kaldırılırken hata: {str(e)}")
            return False

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
            logger.error(f"Çevre değişkeni reddedildi (kontrol karakteri): {key}")
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
            logger.info(f"Çevre değişkeni güncellendi: {key}={mask_secret(key, value)}")
            return True
        except Exception as e:
            logger.error(f"Çevre değişkeni güncellenirken hata: {str(e)}")
            return False
