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
        
        # Genel yapılandırma dosyası
        self.config_path = os.path.splitext(self.league_config_path)[0] + ".json"
        if config_path and config_path.endswith(".json"):
            self.config_path = config_path
        elif not os.path.exists(self.config_path):
            self.config_path = "config/config.json"
        
        # Lig ve diğer yapılandırma verilerini tut
        self.leagues: Dict[int, str] = {}
        self.leagues_by_name: Dict[str, int] = {}
        self.config: Dict[str, Any] = {
            "api": {
                "base_url": os.getenv("API_BASE_URL", "https://sofascore.com/api/v1"),
                "use_proxy": os.getenv("USE_PROXY", "false").lower() == "true",
                "proxy_url": os.getenv("PROXY_URL", "")
            },
            "general": {
                "data_dir": os.getenv("DATA_DIR", "data")
            },
            "display": {
                "use_color": True,
                "date_format": "%Y-%m-%d %H:%M:%S"
            }
        }
        
        # Yapılandırma dizinlerini kontrol et
        self._ensure_config_dir()
        
        # Yapılandırma dosyaları yoksa örnek dosyaları oluştur
        if not os.path.exists(self.league_config_path):
            self._create_sample_league_config()
        if not os.path.exists(self.config_path):
            self._create_sample_config()
        
        # Ligleri ve diğer yapılandırmaları yükle
        self._load_leagues()
        self._load_config()
        
        logger.info(f"Yapılandırma yöneticisi başlatıldı: {len(self.leagues)} lig yüklendi ({self.league_config_path})")
        logger.info(f"Genel yapılandırma yüklendi: {self.config_path}")
        
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
        
        # Genel yapılandırma dizini
        config_dir = os.path.dirname(self.config_path)
        if config_dir:
            os.makedirs(config_dir, exist_ok=True)
    
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
    
    def _create_sample_config(self) -> None:
        """
        Örnek bir genel yapılandırma dosyası oluşturur.
        
        Raises:
            OSError: Dosya oluşturulamazsa
        """
        try:
            # Varsayılan yapılandırma
            config = {
                "api": {
                    "base_url": "https://api.sofascore.com/api/v1",
                    "use_proxy": False,
                    "proxy_url": ""
                },
                "general": {
                    "data_dir": "data"
                },
                "display": {
                    "use_color": True,
                    "date_format": "%Y-%m-%d %H:%M:%S"
                }
            }
            
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4)
            
            logger.info(f"Örnek genel yapılandırma dosyası oluşturuldu: {self.config_path}")
        except OSError as e:
            logger.error(f"Örnek genel yapılandırma dosyası oluşturulamadı: {str(e)}")
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
            
            # Ligleri JSON dosyasından yükle ve birleştir (varsa)
            self._load_leagues_from_json()
            
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
    
    def _load_leagues_from_json(self) -> None:
        """Ligleri JSON dosyasından yükler."""
        if not os.path.exists(self.config_path):
            logger.warning(f"JSON yapılandırma dosyası bulunamadı: {self.config_path}")
            return
            
        try:
            # JSON dosyasından yapılandırmayı oku
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            # Ligleri yükle
            if "leagues" in config_data:
                for league_id_str, league_name in config_data["leagues"].items():
                    try:
                        league_id = int(league_id_str)
                        self.leagues[league_id] = league_name
                        self.leagues_by_name[league_name] = league_id
                    except ValueError:
                        logger.warning(f"Geçersiz lig ID formatı: {league_id_str}")
                        
                logger.debug(f"JSON dosyasından {len(config_data.get('leagues', {}))} lig yüklendi")
        except Exception as e:
            logger.error(f"JSON dosyasından ligler yüklenirken hata: {str(e)}")
    
    def _load_config(self) -> None:
        """
        Genel yapılandırma dosyasını yükler.
        
        Raises:
            ConfigError: Yapılandırma yüklenemezse
        """
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    
                    # Mevcut yapılandırmayı güncelle
                    for section in ["api", "general", "display"]:
                        if section in loaded_config:
                            if section not in self.config:
                                self.config[section] = {}
                            
                            # Alt bölümleri güncelle
                            for key, value in loaded_config[section].items():
                                self.config[section][key] = value
        except Exception as e:
            logger.error(f"Genel yapılandırma dosyası yüklenirken hata: {str(e)}")
            # Hata durumunda varsayılan yapılandırmayı kullan
            pass

    def save_config(self) -> bool:
        """
        Genel yapılandırmayı dosyaya kaydeder.
        
        Returns:
            bool: Başarılı olursa True, değilse False
        """
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4)
            
            logger.info(f"Genel yapılandırma kaydedildi: {self.config_path}")
            return True
        except Exception as e:
            logger.error(f"Genel yapılandırma kaydedilirken hata: {str(e)}")
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
    
    def get_data_dir(self) -> str:
        """
        Veri dizinini döndürür.
        
        Returns:
            str: Yapılandırmada tanımlanan veri dizini
        """
        return self.config.get("general", {}).get("data_dir", "data")
    
    def get_match_data_dir(self) -> str:
        """
        Maç verilerinin saklandığı dizini döndürür.
        
        Returns:
            str: Maç verilerinin saklandığı dizin
        """
        data_dir = self.get_data_dir()
        return os.path.join(data_dir, "matches")
    
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
            
            # Genel yapılandırmayı yeniden yükle
            self._load_config()
            
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
            except Exception as e:
                logger.error(f"Lig eklenirken metin dosyası hatası: {str(e)}")
                raise ConfigError(f"Lig eklenirken metin dosyası hatası: {str(e)}") from e
            
            # JSON yapılandırmasına lig ekle
            try:
                # Leagues bölümü yoksa oluştur
                if "leagues" not in self.config:
                    self.config["leagues"] = {}
                
                # Lig ID'sini string olarak ekle
                self.config["leagues"][str(league_id)] = league_name
                
                # Yapılandırmayı kaydet
                success = self.save_config()
                if not success:
                    logger.warning("JSON yapılandırması kaydedilemedi, ancak metin dosyası güncellendi")
            except Exception as e:
                logger.error(f"Lig eklenirken JSON yapılandırma hatası: {str(e)}")
                # Metin dosyası başarılı olduğu için devam et
            
            return True
            
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
                
                logger.info(f"Lig kaldırıldı: {league_name} (ID: {league_id})")
            except Exception as e:
                logger.error(f"Lig kaldırılırken metin dosyası hatası: {str(e)}")
                raise ConfigError(f"Lig kaldırılırken metin dosyası hatası: {str(e)}") from e
            
            # JSON yapılandırmasından ligi kaldır
            try:
                if "leagues" in self.config and str(league_id) in self.config["leagues"]:
                    del self.config["leagues"][str(league_id)]
                    
                    # Yapılandırmayı kaydet
                    success = self.save_config()
                    if not success:
                        logger.warning("JSON yapılandırması kaydedilemedi, ancak metin dosyası güncellendi")
            except Exception as e:
                logger.error(f"Lig kaldırılırken JSON yapılandırma hatası: {str(e)}")
                # Metin dosyası başarılı olduğu için devam et
            
            # Hafızadaki ligi kaldır
            del self.leagues_by_name[league_name]
            del self.leagues[league_id]
            
            return True
            
        except Exception as e:
            logger.error(f"Lig kaldırılırken beklenmeyen hata: {str(e)}")
            return False