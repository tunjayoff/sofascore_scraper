"""
Merkezi loglama yapılandırması için modül.
"""

import os
import logging
from pathlib import Path
import dotenv

# .env dosyasını yükle
dotenv.load_dotenv()

class Logger:
    """Uygulamanın merkezi loglama sınıfı."""
    
    _instance = None
    _loggers = {}
    
    def __new__(cls):
        """Singleton tasarım deseni uygulaması."""
        if cls._instance is None:
            cls._instance = super(Logger, cls).__new__(cls)
            cls._instance._setup_logging()
        return cls._instance
    
    def _setup_logging(self):
        """Temel loglama yapılandırmasını ayarlar."""
        # Log dizinini oluştur
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        
        # Log seviyesini al
        log_level_str = os.getenv("LOG_LEVEL", "INFO")
        log_level = getattr(logging, log_level_str.upper(), logging.INFO)
        
        # Kök logger'ı yapılandır
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        
        # Eğer handler'lar zaten varsa temizle
        if root_logger.hasHandlers():
            root_logger.handlers.clear()
        
        # Dosya handler'ı
        file_handler = logging.FileHandler("logs/sofascore_scraper.log", encoding="utf-8")
        file_handler.setLevel(log_level)
        file_format = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(file_format)
        
        # Konsol handler'ı
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_format = logging.Formatter(
            '%(levelname)s: %(message)s'
        )
        console_handler.setFormatter(console_format)
        
        # Handler'ları ekle
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
    
    def get_logger(self, name: str) -> logging.Logger:
        """
        Belirtilen isimle bir logger döndürür.
        
        Args:
            name: Logger'ın adı (genellikle modül adı)
        
        Returns:
            logging.Logger: Oluşturulan logger 
        """
        if name not in self._loggers:
            self._loggers[name] = logging.getLogger(name)
        return self._loggers[name]


# Dışa aktarılan fonksiyon
def get_logger(name: str) -> logging.Logger:
    """
    İsimlendirilmiş bir logger döndürür.
    
    Args:
        name: Logger'ın adı
    
    Returns:
        logging.Logger: Yapılandırılmış logger nesnesi
    """
    return Logger().get_logger(name) 