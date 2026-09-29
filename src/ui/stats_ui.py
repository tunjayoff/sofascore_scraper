"""
SofaScore Scraper için istatistik işlemleri modülü.
Bu modül, istatistik görüntüleme ve raporlama işlemlerini içerir.
"""

import os
import json
import traceback
from typing import Any, Dict

from src.config_manager import ConfigManager
from src.logger import get_logger
from src.services import stats as stats_service
from src.i18n import get_i18n

# Logger'ı al
logger = get_logger("StatsUI")


class StatsMenuHandler:
    """İstatistik menü işlemleri sınıfı."""

    def __init__(
        self,
        config_manager: ConfigManager,
        data_dir: str,
        colors: Dict[str, str]
    ):
        """
        StatsMenuHandler sınıfını başlatır.

        Args:
            config_manager: Konfigürasyon yöneticisi
            data_dir: Veri dizini
            colors: Renk tanımlamaları sözlüğü
        """
        self.config_manager = config_manager
        self.data_dir = data_dir
        self.colors = colors
        self.i18n = get_i18n()

    def show_system_stats(self) -> None:
        """Sistem durumunu ve istatistiklerini gösterir."""
        COLORS = self.colors  # Kısa erişim için

        try:
            print(f"\n{COLORS['TITLE']}{self.i18n.t('system_stats_title'):^50}\n{'-'*50}")
            stats = stats_service.system_stats(self.data_dir, self.config_manager.get_leagues())
            print(f"{self.i18n.t('total_leagues')} {COLORS['SUCCESS']}{stats['leagues']}")
            print(f"{self.i18n.t('total_seasons')} {COLORS['SUCCESS']}{stats['seasons']}")
            print(f"{self.i18n.t('total_matches')} {COLORS['SUCCESS']}{stats['matches']}")

            disk = stats["disk_usage"]
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('disk_usage_title')}")
            print(f"  {self.i18n.t('disk_seasons')} {COLORS['SUCCESS']}{self._format_size(disk['seasons'])}")
            print(f"  {self.i18n.t('disk_matches')} {COLORS['SUCCESS']}{self._format_size(disk['matches'])}")
            print(f"  {self.i18n.t('disk_match_details')} {COLORS['SUCCESS']}{self._format_size(disk['details'])}")
            print(f"  {self.i18n.t('disk_datasets')} {COLORS['SUCCESS']}{self._format_size(disk['datasets'])}")
            print(f"  {self.i18n.t('disk_total')} {COLORS['SUCCESS']}{self._format_size(disk['total'])}")

        except Exception as e:
            logger.error(f"Sistem istatistikleri görüntülenirken hata: {str(e)}")
            logger.error(traceback.format_exc())
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")

    def show_league_stats(self, league_id):
        """
        Belirli bir lig için ayrıntılı istatistikleri gösterir
        """
        COLORS = self.colors  # Kısa erişim için

        try:
            league_name = self.config_manager.get_leagues().get(league_id, f"Lig {league_id}")
            print(f"\n{COLORS['INFO']}● {league_name} {COLORS['DIM']}(ID: {league_id})")

            st = stats_service.league_stats(self.data_dir, league_id, league_name)
            disk = st["disk"]
            print(f"  {COLORS['INFO']}○ {self.i18n.t('stats_season_count')} {COLORS['SUCCESS']}{st['seasons_fetched']}")
            print(f"  {COLORS['INFO']}○ {self.i18n.t('stats_match_count')} {COLORS['SUCCESS']}{st['matches']}")
            print(f"  {COLORS['INFO']}○ {self.i18n.t('stats_match_details_count')} {COLORS['SUCCESS']}{st['details']}")
            print(f"  {COLORS['INFO']}○ {self.i18n.t('stats_season_data')} {COLORS['SUCCESS']}{self._format_size(disk['seasons'])}")
            print(f"  {COLORS['INFO']}○ {self.i18n.t('stats_match_data')} {COLORS['SUCCESS']}{self._format_size(disk['matches'])}")
            print(f"  {COLORS['INFO']}○ {self.i18n.t('disk_match_details')} {COLORS['SUCCESS']}{self._format_size(disk['details'])}")
            print(f"  {COLORS['INFO']}○ {self.i18n.t('disk_total')} {COLORS['SUCCESS']}{self._format_size(disk['total'])}")

        except Exception as e:
            logger.error(f"Lig istatistikleri görüntülenirken hata: {str(e)}")
            logger.error(traceback.format_exc())
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")


    def generate_report(self) -> None:
        """İstatistik raporu oluşturur."""
        COLORS = self.colors  # Kısa erişim için

        try:
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('report_generation_title')}")
            print("-" * 50)
            print(self.i18n.t('report_system'))
            print(self.i18n.t('report_league'))
            print(self.i18n.t('report_detailed'))

            choice = input(f"\n{self.i18n.t('settings_option_prompt')} ")

            if choice == "1":
                self._generate_system_report()
            elif choice == "2":
                self._generate_league_report()
            elif choice == "3":
                self._generate_detailed_report()
            else:
                print(f"\n{COLORS['WARNING']}{self.i18n.t('error_invalid_option')}")

        except Exception as e:
            logger.error(f"Rapor oluşturulurken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")

    def _system_report_stats(self) -> Dict[str, Any]:
        stats = stats_service.system_stats(self.data_dir, self.config_manager.get_leagues())
        disk = stats["disk_usage"]
        return {
            "league_count": stats["leagues"],
            "season_count": stats["seasons"],
            "match_count": stats["matches"],
            "match_details_count": stats["details"],
            "disk_usage": {
                "seasons": disk["seasons"],
                "matches": disk["matches"],
                "match_details": disk["details"],
                "datasets": disk["datasets"],
                "total": disk["total"],
            },
        }

    def _league_report_entries(self) -> Dict[str, Any]:
        entries: Dict[str, Any] = {}
        for league_id, league_name in self.config_manager.get_leagues().items():
            st = stats_service.league_stats(self.data_dir, league_id, league_name)
            entries[str(league_id)] = {
                "id": league_id,
                "name": league_name,
                "stats": {
                    "season_count": st["seasons_fetched"],
                    "match_count": st["matches"],
                    "match_details_count": st["details"],
                    "disk_usage": {
                        "seasons": st["disk"]["seasons"],
                        "matches": st["disk"]["matches"],
                        "match_details": st["disk"]["details"],
                        "total": st["disk"]["total"],
                    },
                },
            }
        return entries

    def _write_report(self, kind: str, report: Dict[str, Any], message_key: str) -> None:
        reports_dir = os.path.join(self.data_dir, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        report_file = os.path.join(reports_dir, f"{kind}_report_{self._get_timestamp_filename()}.json")
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n{self.colors['SUCCESS']}✅ {self.i18n.t(message_key)} {report_file}")

    def _generate_system_report(self) -> None:
        """Sistem raporu oluşturur."""
        try:
            report = {"timestamp": self._get_timestamp(), "leagues": {}, "stats": self._system_report_stats()}
            self._write_report("system", report, "system_report_created")
        except Exception as e:
            logger.error(f"Sistem raporu oluşturulurken hata: {str(e)}")
            print(f"\n{self.colors['WARNING']}Hata: {str(e)}")


    def _generate_league_report(self) -> None:
        """Lig bazlı rapor oluşturur."""
        try:
            if not self.config_manager.get_leagues():
                print(f"\n{self.colors['WARNING']}{self.i18n.t('warning_no_configured_league')}")
                return
            report = {"timestamp": self._get_timestamp(), "leagues": self._league_report_entries()}
            self._write_report("league", report, "league_report_created")
        except Exception as e:
            logger.error(f"Lig raporu oluşturulurken hata: {str(e)}")
            print(f"\n{self.colors['WARNING']}Hata: {str(e)}")


    def _generate_detailed_report(self) -> None:
        """Detaylı rapor oluşturur (sistem + lig raporu birlikte)."""
        try:
            report = {
                "timestamp": self._get_timestamp(),
                "system": self._system_report_stats(),
                "leagues": self._league_report_entries(),
            }
            self._write_report("detailed", report, "detailed_report_created")
        except Exception as e:
            logger.error(f"Detaylı rapor oluşturulurken hata: {str(e)}")
            print(f"\n{self.colors['WARNING']}Hata: {str(e)}")



    def _format_size(self, size_bytes: int) -> str:
        """Bayt cinsinden boyutu okunabilir formata dönüştürür."""
        return stats_service.format_size(size_bytes)


    def _get_timestamp(self) -> str:
        """Şu anki zamanı ISO formatında döndürür."""
        from datetime import datetime
        return datetime.now().isoformat()

    def _get_timestamp_filename(self) -> str:
        """Dosya adı için uygun zaman damgası oluşturur."""
        from datetime import datetime
        return datetime.now().strftime("%Y%m%d_%H%M%S")
