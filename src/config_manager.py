"""
Yapılandırma dosyalarını yöneten modül.
Lig bilgilerini okur ve yönetir.
"""

import os
import dotenv
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set, Any
from dataclasses import dataclass

from src.exceptions import ConfigError
from src.logger import get_logger

# .env dosyasını yükle
dotenv.load_dotenv()

# Logger'ı alın
logger = get_logger("ConfigManager")

# Yapılandırma dizini için çevre değişkeni
CONFIG_DIR = os.getenv("CONFIG_DIR", "config")


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
        self.league_config_path = config_path or "config/leagues.txt"
        
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
        Örnek bir lig yapılandırma dosyası oluşturur.
        
        Raises:
            OSError: Dosya oluşturulamazsa
        """
        try:
            with open(self.league_config_path, 'w', encoding='utf-8') as f:
                f.write("# League configuration file\n")
                f.write("# Format: League Name: ID\n")
                f.write("Premier League: 17\n")
                f.write("LaLiga: 8\n")
                f.write("Serie A: 23\n")
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
    
    def _load_leagues_from_text(self) -> None:
        """Ligleri metin dosyasından yükler."""
        if not os.path.exists(self.league_config_path):
            logger.warning(f"Lig yapılandırma dosyası bulunamadı: {self.league_config_path}")
            return
            
        try:
            # Metin dosyasından ligleri oku
            with open(self.league_config_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            for line in lines:
                line = line.strip()
                
                # Yorum satırlarını ve boş satırları atla
                if not line or line.startswith('#'):
                    continue
                
                # Lig adı ve ID'sini ayır
                try:
                    if ":" in line:
                        league_name, league_id_str = map(str.strip, line.split(':', 1))
                        league_id = int(league_id_str)
                    else:
                        # Alternatif format (ID ad)
                        parts = line.split(None, 1)
                        if len(parts) != 2:
                            logger.warning(f"Geçersiz lig formatı: {line}")
                            continue
                            
                        league_id_str, league_name = parts
                        league_id = int(league_id_str)
                    
                    self.leagues[league_id] = league_name
                    self.leagues_by_name[league_name] = league_id
                    
                except ValueError:
                    # Geçersiz sayı formatı
                    logger.warning(f"Geçersiz lig ID formatı: {line}")
                except Exception as e:
                    # Diğer hatalar
                    logger.warning(f"Lig verisi ayrıştırılırken hata: {str(e)} - {line}")
                    
            logger.debug(f"Metin dosyasından {len(self.leagues)} lig yüklendi")
        except Exception as e:
            logger.error(f"Metin dosyasından ligler yüklenirken hata: {str(e)}")
    
    def save_config(self) -> bool:
        """
        Çevre değişkenlerini .env dosyasına kaydeder.
        
        Returns:
            bool: Başarılı olursa True, değilse False
        """
        try:
            # Mevcut .env dosyasını oku
            env_path = ".env"
            env_vars = {}
            
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        if '=' in line:
                            key, value = line.split('=', 1)
                            env_vars[key.strip()] = value.strip()
            
            # Güncellenmiş değerleri ekle
            env_vars["API_BASE_URL"] = os.getenv("API_BASE_URL", "https://www.sofascore.com/api/v1")
            env_vars["USE_PROXY"] = os.getenv("USE_PROXY", "false")
            env_vars["PROXY_URL"] = os.getenv("PROXY_URL", "")
            env_vars["DATA_DIR"] = os.getenv("DATA_DIR", "data")
            env_vars["USE_COLOR"] = os.getenv("USE_COLOR", "true")
            env_vars["DATE_FORMAT"] = os.getenv("DATE_FORMAT", "%Y-%m-%d %H:%M:%S")
            
            # .env dosyasını yeniden yaz
            with open(env_path, 'w', encoding='utf-8') as f:
                for key, value in env_vars.items():
                    f.write(f"{key}={value}\n")
            
            logger.info(f"Çevre değişkenleri .env dosyasına kaydedildi")
            return True
        except Exception as e:
            logger.error(f"Çevre değişkenleri kaydedilirken hata: {str(e)}")
            return False
    
    def get_leagues(self) -> Dict[int, str]:
        """
        Tüm ligleri döndürür.
        
        Returns:
            Dict[int, str]: Lig ID'leri ve isimleri içeren sözlük
        """
        return self.leagues.copy()
    
    def get_league_ids(self) -> Set[int]:
        """
        Tüm lig ID'lerini döndürür.
        
        Returns:
            Set[int]: Lig ID'leri kümesi
        """
        return set(self.leagues.keys())
    
    def get_league_names(self) -> Set[str]:
        """
        Tüm lig isimlerini döndürür.
        
        Returns:
            Set[str]: Lig isimleri kümesi
        """
        return set(self.leagues.values())
    
    def get_league_by_name(self, league_name: str) -> Optional[int]:
        """
        İsme göre lig ID'sini döndürür.
        
        Args:
            league_name: Lig adı
            
        Returns:
            Optional[int]: Lig ID'si veya bulunamazsa None
        """
        return self.leagues_by_name.get(league_name)
    
    def get_league_by_id(self, league_id: int) -> Optional[str]:
        """
        ID'ye göre lig adını döndürür.
        
        Args:
            league_id: Lig ID'si
            
        Returns:
            Optional[str]: Lig adı veya bulunamazsa None
        """
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
    
    def get_data_dir(self) -> str:
        """
        Veri dizinini döndürür.
        
        Returns:
            str: Yapılandırmada tanımlanan veri dizini
        """
        return os.getenv("DATA_DIR", "data")
    
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
        return os.getenv("API_BASE_URL", "https://www.sofascore.com/api/v1")
    
    def get_use_proxy(self) -> bool:
        """
        Proxy kullanımı ayarını döndürür.
        
        Returns:
            bool: Proxy kullanılacaksa True, değilse False
        """
        return os.getenv("USE_PROXY", "false").lower() == "true"
    
    def get_proxy_url(self) -> str:
        """
        Proxy URL'sini döndürür.
        
        Returns:
            str: Proxy URL'si
        """
        return os.getenv("PROXY_URL", "")
    
    def get_use_color(self) -> bool:
        """
        Renk kullanımı ayarını döndürür.
        
        Returns:
            bool: Renk kullanılacaksa True, değilse False
        """
        return os.getenv("USE_COLOR", "true").lower() == "true"
    
    def get_date_format(self) -> str:
        """
        Tarih formatını döndürür.
        
        Returns:
            str: Tarih formatı
        """
        return os.getenv("DATE_FORMAT", "%Y-%m-%d %H:%M:%S")
    
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
            
            # Çevre değişkenlerini yeniden yükle
            dotenv.load_dotenv(override=True)
            
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
            league_name: Lig adı
            league_id: Lig ID'si
        
        Returns:
            bool: Başarılı olursa True, değilse False
        
        Raises:
            ConfigError: Lig eklenemezse
        """
        try:
            # Lig zaten varsa hata ver
            if league_id in self.leagues:
                logger.warning(f"Lig ID zaten var: {league_id}")
                return False
                
            if league_name in self.leagues_by_name:
                logger.warning(f"Lig adı zaten var: {league_name}")
                return False
            
            # Lig bilgilerini sakla
            self.leagues[league_id] = league_name
            self.leagues_by_name[league_name] = league_id
            
            # Metin dosyasına lig ekle
            try:
                # Var olan dosyayı oku
                lines = []
                try:
                    with open(self.league_config_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                except FileNotFoundError:
                    # Dosya yoksa boş liste ile devam et
                    pass
                
                # Yeni lig satırını oluştur
                new_line = f"\n{league_name}: {league_id}"
                
                # Dosyayı yeniden yaz ve yeni lig ekle
                with open(self.league_config_path, 'w', encoding='utf-8') as f:
                    # Mevcut satırları yaz
                    for line in lines:
                        f.write(line)
                    
                    # Yeni satırı ekle (dosya boşsa başına yorum ekle)
                    if not lines:
                        f.write("# League configuration file\n")
                        f.write("# Format: League Name: ID\n")
                    f.write(new_line)
                
                logger.info(f"Lig eklendi: {league_name} (ID: {league_id})")
                return True
                
            except Exception as e:
                logger.error(f"Lig eklenirken metin dosyası hatası: {str(e)}")
                raise ConfigError(f"Lig eklenirken metin dosyası hatası: {str(e)}") from e
            
        except Exception as e:
            logger.error(f"Lig eklenirken beklenmeyen hata: {str(e)}")
            return False
    
    def remove_league(self, league_id: int) -> bool:
        """
        Bir ligi yapılandırmadan kaldırır.
        
        Args:
            league_id: Kaldırılacak ligin ID'si
        
        Returns:
            bool: Başarılı olursa True, değilse False
        
        Raises:
            ConfigError: Lig kaldırılamazsa
        """
        try:
            # Lig yoksa hata ver
            if league_id not in self.leagues:
                logger.warning(f"Kaldırılacak lig bulunamadı: {league_id}")
                return False
            
            league_name = self.leagues[league_id]
            
            # Metin dosyasından ligi kaldır
            try:
                # Dosyayı oku
                with open(self.league_config_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                # Yeni satırları oluştur (ligi hariç tut)
                new_lines = []
                for line in lines:
                    line_stripped = line.strip()
                    
                    # Yorum satırlarını ve boş satırları tut
                    if not line_stripped or line_stripped.startswith('#'):
                        new_lines.append(line)
                        continue
                    
                    # Mevcut lig satırını kontrol et
                    try:
                        if ":" in line_stripped:
                            name, id_str = map(str.strip, line_stripped.split(':', 1))
                            id_val = int(id_str)
                            
                            # Kaldırılacak ligi atlat
                            if id_val == league_id:
                                continue
                        
                        # Diğer satırları tut
                        new_lines.append(line)
                    except ValueError:
                        # Geçersiz satırları da tut
                        new_lines.append(line)
                
                # Dosyayı güncelle
                with open(self.league_config_path, 'w', encoding='utf-8') as f:
                    f.writelines(new_lines)
                
                # Lig bilgilerini kaldır
                del self.leagues[league_id]
                del self.leagues_by_name[league_name]
                
                logger.info(f"Lig kaldırıldı: {league_name} (ID: {league_id})")
                return True
                
            except Exception as e:
                logger.error(f"Lig kaldırılırken metin dosyası hatası: {str(e)}")
                raise ConfigError(f"Lig kaldırılırken metin dosyası hatası: {str(e)}") from e
            
        except Exception as e:
            logger.error(f"Lig kaldırılırken beklenmeyen hata: {str(e)}")
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
        try:
            # Çevre değişkenini güncelle
            os.environ[key] = value
            
            # .env dosyasını güncelle
            env_path = ".env"
            env_vars = {}
            
            # Mevcut .env dosyasını oku
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        if '=' in line:
                            k, v = line.split('=', 1)
                            env_vars[k.strip()] = v.strip()
            
            # Değişkeni güncelle
            env_vars[key] = value
            
            # .env dosyasını yeniden yaz
            with open(env_path, 'w', encoding='utf-8') as f:
                for k, v in env_vars.items():
                    f.write(f"{k}={v}\n")
            
            logger.info(f"Çevre değişkeni güncellendi: {key}={value}")
            return True
        except Exception as e:
            logger.error(f"Çevre değişkeni güncellenirken hata: {str(e)}")
            return False