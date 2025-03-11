#!/usr/bin/env python3
"""
SofaScore Scraper uygulaması ana giriş noktası.
"""

import sys
import traceback
import os
import argparse
import dotenv
from pathlib import Path

# Çalışma dizinini modülün dizinine ayarla
script_dir = Path(__file__).resolve().parent
os.chdir(script_dir)

# Çevre değişkenlerini yükle
dotenv.load_dotenv()

from src.SofaScoreUi import SimpleSofaScoreUI
from src.logger import get_logger

# Logger'ı al
logger = get_logger("Main")


def parse_arguments() -> argparse.Namespace:
    """
    Komut satırı argümanlarını ayrıştırır.
    
    Returns:
        argparse.Namespace: Ayrıştırılan argümanlar
    """
    parser = argparse.ArgumentParser(
        description="SofaScore'dan futbol maçı verilerini çeken ve analiz eden uygulama."
    )
    
    parser.add_argument(
        "--headless", 
        action="store_true",
        help="Kullanıcı arayüzünü göstermeden toplu veri çekme işlemi yapar"
    )
    
    parser.add_argument(
        "--update-all",
        action="store_true",
        help="Tüm liglerin verilerini günceller"
    )
    
    parser.add_argument(
        "--csv-export",
        action="store_true",
        help="Verileri CSV formatında dışa aktarır"
    )
    
    return parser.parse_args()


def main() -> int:
    """
    Uygulamanın ana giriş noktası.
    
    Returns:
        int: Çıkış kodu (0: başarılı, 1: hata)
    """
    try:
        # Komut satırı argümanlarını ayrıştır
        args = parse_arguments()
        
        # UI nesnesini oluştur
        ui = SimpleSofaScoreUI()
        
        # Headless mod veya normal mod
        if args.headless:
            logger.info("Headless modda çalışılıyor")
            
            if args.update_all:
                logger.info("Tüm liglerin verilerini güncelleme işlemi başlatılıyor")
                ui.update_all_leagues()
                
            if args.csv_export:
                logger.info("CSV dışa aktarma işlemi başlatılıyor")
                ui.export_all_to_csv()
        else:
            # Normal interaktif mod
            logger.info("İnteraktif mod başlatılıyor")
            ui.run()
            
        return 0  # Başarılı çıkış
        
    except KeyboardInterrupt:
        print("\n\nProgram kullanıcı tarafından sonlandırıldı.")
        return 0
        
    except Exception as e:
        logger.exception(f"Beklenmeyen hata: {str(e)}")
        print(f"\nBeklenmeyen bir hata oluştu: {str(e)}")
        traceback.print_exc()
        print("Lütfen detaylar için log dosyasını kontrol edin.")
        return 1  # Hata çıkışı


if __name__ == "__main__":
    sys.exit(main())