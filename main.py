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
        description="SofaScore'dan futbol maçı verilerini çeken ve analiz eden uygulama.",
        epilog=(
            "Headless / CI örnekleri:\n"
            "  %(prog)s --headless --update-all\n"
            "  %(prog)s --headless --update-all --fetch-mode details --league-id 52\n"
            "  %(prog)s --headless --csv-export --data-dir ./data\n"
            "Not: --web modu kendi ConfigManager örneğini kullanır; CLI --config/--data-dir yalnızca "
            "TUI ve headless için geçerlidir (.env / DATA_DIR ile web hizalanabilir)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument(
        "--headless", 
        action="store_true",
        help="Kullanıcı arayüzünü göstermeden toplu veri çekme işlemi yapar"
    )
    
    parser.add_argument(
        "--update-all",
        action="store_true",
        help="Tüm (veya --league-id ile tek) lig verisini headless günceller; --fetch-mode ile kapsam",
    )

    parser.add_argument(
        "--fetch-mode",
        choices=["full", "details"],
        default="full",
        help="--update-all ile: full=sezon+maç+detay, details=sadece maç detayları (web ile aynı)",
    )

    parser.add_argument(
        "--league-id",
        type=int,
        default=None,
        metavar="ID",
        help="--update-all ile yalnız bu SofaScore lig ID'si (config'de kayıtlı olmalı)",
    )

    parser.add_argument(
        "--config",
        default="config/leagues.txt",
        help="Lig listesi dosyası (varsayılan: config/leagues.txt)",
    )

    parser.add_argument(
        "--data-dir",
        default="data",
        dest="data_dir",
        help="Veri kök dizini",
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

        if args.web:
            try:
                import uvicorn
                logger.info("Web arayüzü başlatılıyor: http://localhost:8000")
                i18n = get_i18n()
                print(i18n.t('web_server_starting'))
                print(i18n.t('go_to_address'))
                print(i18n.t('press_ctrl_c'))

                uvicorn.run("src.web.app:app", host="0.0.0.0", port=8000, reload=True)
            except ImportError:
                i18n = get_i18n()
                print(i18n.t('err_web_packages_not_installed'))
                print(i18n.t('run_pip_install'))
                return 1
            return 0

        ui = SimpleSofaScoreUI(config_path=args.config, data_dir=args.data_dir)

        if args.headless:
            logger.info("Headless modda çalışılıyor")
            ran = False

            if args.update_all:
                logger.info(
                    "Headless güncelleme: league_id=%s mode=%s",
                    args.league_id,
                    args.fetch_mode,
                )
                ui.run_headless_fetch(league_id=args.league_id, mode=args.fetch_mode)
                ran = True

            if args.csv_export:
                logger.info("CSV dışa aktarma işlemi başlatılıyor")
                ui.export_all_to_csv()
                ran = True

            if not ran:
                logger.error(
                    "Headless için en az biri gerekli: --update-all ve/veya --csv-export"
                )
                print(
                    "Örnek: python main.py --headless --update-all\n"
                    "        python main.py --headless --update-all --fetch-mode details --league-id 52\n"
                    "        python main.py --headless --csv-export --data-dir ./data",
                    file=sys.stderr,
                )
                return 2
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