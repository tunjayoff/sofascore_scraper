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
from src.i18n import get_i18n

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

    parser.add_argument(
        "--web",
        action="store_true",
        help="Web arayüzünü başlatır"
    )

    parser.add_argument(
        "--ignore-rate-limit",
        action="store_true",
        help="Rate-limit circuit breaker mekanizmasını devre dışı bırakır"
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

        if args.ignore_rate_limit:
            os.environ["IGNORE_RATE_LIMIT"] = "true"
            logger.warning("Rate-limit circuit breaker --ignore-rate-limit ile devre dışı bırakıldı.")
        
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
        elif args.web:
            # Web arayüzü modu
            try:
                import uvicorn
                logger.info("Web arayüzü başlatılıyor: http://localhost:8000")
                i18n = get_i18n()
                print(i18n.t('web_server_starting'))
                print(i18n.t('go_to_address'))
                print(i18n.t('press_ctrl_c'))
                
                # Uvicorn ile uygulamayı başlat
                uvicorn.run("src.web.app:app", host="0.0.0.0", port=8000, reload=True)
            except ImportError:
                i18n = get_i18n()
                print(i18n.t('err_web_packages_not_installed'))
                print(i18n.t('run_pip_install'))
                return 1
        else:
            # Normal interaktif mod
            logger.info("İnteraktif mod başlatılıyor")
            ui.run()
            
        forced_exit_code = os.getenv("APP_EXIT_CODE")
        if forced_exit_code and forced_exit_code.isdigit():
            return int(forced_exit_code)
        return 0  # Başarılı çıkış
        
    except KeyboardInterrupt:
        i18n = get_i18n()
        print(i18n.t('prog_terminated_by_user'))
        return 0
        
    except Exception as e:
        i18n = get_i18n()
        logger.exception(f"Beklenmeyen hata: {str(e)}")
        print(i18n.t('unexpected_error_occurred', error=str(e)))
        traceback.print_exc()
        print(i18n.t('check_log_for_details'))
        return 1  # Hata çıkışı


if __name__ == "__main__":
    sys.exit(main())