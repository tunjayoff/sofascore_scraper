"""
SofaScore Scraper için terminal kullanıcı arayüzü modülü.
Bu modül, terminal üzerinden SofaScore verilerine erişim sağlar.
"""

import os
import json
import csv
import time
import sys
from typing import Any, Callable, Dict, List, Optional, Union

# Colorama renk kütüphanesi
from colorama import init, Fore, Back, Style
init(autoreset=True)  # Terminal renklendirmesi için otomatik sıfırlama

# Proje modülleri
from src.config_manager import ConfigManager
from src.season_fetcher import SeasonFetcher
from src.match_fetcher import MatchFetcher
from src.match_data_fetcher import MatchDataFetcher
from src.ui.cli_shell import (
    CliShell,
    MAIN_MENU_ITEMS,
    LEAGUE_MENU_ITEMS,
    SEASON_MENU_ITEMS,
    MATCH_MENU_ITEMS,
    MATCH_DATA_MENU_ITEMS,
    STATS_MENU_ITEMS,
    SETTINGS_MENU_ITEMS,
)
from src.ui.menu_ui import LeagueMenuHandler, SeasonMenuHandler
from src.ui.match_ui import MatchMenuHandler, MatchDataMenuHandler
from src.ui.stats_ui import StatsMenuHandler
from src.ui.settings_ui import SettingsMenuHandler
from src.logger import get_logger
from src.i18n import get_i18n

# Logger'ı al
logger = get_logger("SofaScoreUI")

# Renk tanımlamaları
COLORS = {
    "TITLE": Fore.CYAN + Style.BRIGHT,
    "SUBTITLE": Fore.YELLOW + Style.BRIGHT,
    "INFO": Fore.WHITE + Style.BRIGHT,
    "SUCCESS": Fore.GREEN + Style.NORMAL,
    "WARNING": Fore.RED + Style.BRIGHT,
    "ERROR": Fore.RED + Style.BRIGHT,
    "DIM": Style.DIM,
    "RESET": Style.RESET_ALL,
}


class SimpleSofaScoreUI:
    """SofaScore için basit terminal kullanıcı arayüzü."""
    
    def __init__(
        self, 
        config_path: str = "config/leagues.txt", 
        data_dir: str = "data",
        config_manager: Optional[ConfigManager] = None,
        season_fetcher: Optional[SeasonFetcher] = None,
        match_fetcher: Optional[MatchFetcher] = None,
        match_data_fetcher: Optional[MatchDataFetcher] = None
    ):
        """
        SimpleSofaScoreUI sınıfını başlatır.
        
        Args:
            config_path: Yapılandırma dosyası yolu
            data_dir: Veri dizini
            config_manager: Yapılandırma yöneticisi (opsiyonel)
            season_fetcher: Sezon veri çekici (opsiyonel)
            match_fetcher: Maç veri çekici (opsiyonel)
            match_data_fetcher: Maç detayları veri çekici (opsiyonel)
        """
        # Dizinlerin varlığını kontrol et ve oluştur
        self._ensure_directory(data_dir)
        self._ensure_directory(os.path.join(data_dir, "seasons"))
        self._ensure_directory(os.path.join(data_dir, "matches"))
        self._ensure_directory(os.path.join(data_dir, "match_details"))
        self._ensure_directory(os.path.join(data_dir, "datasets"))
        
        # Ana sınıfları başlat (dependency injection)
        self.config_manager = config_manager or ConfigManager(config_path)
        self.data_dir = data_dir
        
        # USE_COLOR kontrolü
        global COLORS
        if not self.config_manager.get_use_color():
            COLORS = {k: "" for k in COLORS}
            os.environ["NO_COLOR"] = "1"  # rich.console gibi kütüphaneler için

        self.season_fetcher = season_fetcher or SeasonFetcher(self.config_manager, data_dir)
        self.match_fetcher = match_fetcher or MatchFetcher(self.config_manager, self.season_fetcher, data_dir)
        self.match_data_fetcher = match_data_fetcher or MatchDataFetcher(self.config_manager, data_dir)
        
        # UI bileşenlerini başlat
        self.league_menu = LeagueMenuHandler(self.config_manager, COLORS)
        self.season_menu = SeasonMenuHandler(self.config_manager, self.season_fetcher, COLORS)
        self.match_menu = MatchMenuHandler(self.config_manager, self.season_fetcher, self.match_fetcher, COLORS)
        self.match_data_menu = MatchDataMenuHandler(self.config_manager, self.match_data_fetcher, COLORS)
        self.stats_menu = StatsMenuHandler(self.config_manager, data_dir, COLORS)
        self.settings_menu = SettingsMenuHandler(self.config_manager, data_dir, COLORS)
        
        self.i18n = get_i18n()
        self.shell = CliShell(COLORS, self.i18n)
        logger.info("SofaScore Scraper kullanıcı arayüzü başlatıldı")
    
    def _season_json_count(self) -> int:
        seasons_dir = os.path.join(self.data_dir, "seasons")
        if not os.path.exists(seasons_dir):
            return 0
        return len([f for f in os.listdir(seasons_dir) if f.endswith("_seasons.json")])

    def _main_menu_screen(self) -> str:
        self.shell.clear()
        self.shell.app_header()
        self.shell.status_summary(len(self.config_manager.get_leagues()), self._season_json_count())
        self.shell.section_title(self.i18n.t("main_menu_title"))
        self.shell.menu_options(
            MAIN_MENU_ITEMS,
            back_key="0",
            back_label=self.i18n.t("menu_exit"),
        )
        return self.shell.ask()

    def _submenu_screen(self, section_title_key: str, items, range_hint: str) -> str:
        self.shell.clear()
        self.shell.app_header()
        self.shell.breadcrumb(
            self.i18n.t("main_menu_title"),
            self.i18n.t(section_title_key).rstrip(":"),
        )
        self.shell.status_summary(len(self.config_manager.get_leagues()), self._season_json_count())
        self.shell.section_title(self.i18n.t(section_title_key))
        self.shell.menu_options(
            items,
            back_key="0",
            back_label=self.i18n.t("submenu_back_main"),
        )
        return self.shell.ask("selection_prompt_range", range=range_hint)

    def clear_screen(self) -> None:
        """Terminal ekranını temizler (API uyumluluğu)."""
        self.shell.clear()

    def run(self) -> None:
        """Kullanıcı arayüzünü çalıştırır."""
        try:
            while True:
                choice = self._main_menu_screen()
                
                if choice == "0":
                    print(f"\n{COLORS['INFO']}{self.i18n.t('exit_message')}")
                    break
                elif choice == "1":
                    self.show_league_menu()
                elif choice == "2":
                    self.show_season_menu()
                elif choice == "3":
                    self.show_match_menu()
                elif choice == "4":
                    self.show_match_data_menu()
                elif choice == "5":
                    self.show_stats_menu()
                elif choice == "6":
                    self.show_settings_menu()
                else:
                    self.shell.invalid_choice()
        
        except KeyboardInterrupt:
            print(f"\n\n{COLORS['INFO']}Program kullanıcı tarafından sonlandırıldı.")
        except Exception as e:
            logger.error(f"Kullanıcı arayüzünde hata: {str(e)}")
            print(f"\n{COLORS['ERROR']}Hata: {str(e)}")
            input("Devam etmek için Enter'a basın...")
    
    def show_league_menu(self) -> None:
        """Lig yönetimi menüsünü görüntüler."""
        while True:
            choice = self._submenu_screen("submenu_league_title", LEAGUE_MENU_ITEMS, "0-4")
            
            if choice == "0":
                break
            elif choice == "1":
                self.league_menu.list_leagues()
                self.shell.pause()
            elif choice == "2":
                self.league_menu.add_new_league()
                self.shell.pause()
            elif choice == "3":
                self.league_menu.reload_leagues()
                self.shell.pause()
            elif choice == "4":
                self.league_menu.search_leagues()
                self.shell.pause()
            else:
                self.shell.invalid_choice()
    
    def show_season_menu(self) -> None:
        """Sezon verileri menüsünü görüntüler."""
        while True:
            choice = self._submenu_screen("submenu_season_title", SEASON_MENU_ITEMS, "0-3")
            
            if choice == "0":
                break
            elif choice == "1":
                self.season_menu.update_all_seasons()
                self.shell.pause()
            elif choice == "2":
                self.season_menu.update_league_seasons()
                self.shell.pause()
            elif choice == "3":
                self.season_menu.list_seasons()
                self.shell.pause()
            else:
                self.shell.invalid_choice()
    
    def show_match_menu(self) -> None:
        """Maç verileri menüsünü görüntüler."""
        while True:
            choice = self._submenu_screen("submenu_match_title", MATCH_MENU_ITEMS, "0-3")
            
            if choice == "0":
                break
            elif choice == "1":
                self.match_menu.fetch_matches_for_league()
                self.shell.pause()
            elif choice == "2":
                self.match_menu.fetch_matches_for_all_leagues()
                self.shell.pause()
            elif choice == "3":
                self.match_menu.list_matches()
                self.shell.pause()
            else:
                self.shell.invalid_choice()
    
    def show_match_data_menu(self) -> None:
        """Maç detayları menüsünü görüntüler."""
        while True:
            choice = self._submenu_screen("submenu_match_details_title", MATCH_DATA_MENU_ITEMS, "0-3")
            
            if choice == "0":
                break
            elif choice == "1":
                self.match_data_menu.fetch_match_details()
                self.shell.pause()
            elif choice == "2":
                self.match_data_menu.fetch_all_match_details()
                self.shell.pause()
            elif choice == "3":
                self.match_data_menu.convert_to_csv()
                self.shell.pause()
            else:
                self.shell.invalid_choice()
    
    def show_stats_menu(self) -> None:
        """İstatistikler menüsünü görüntüler."""
        while True:
            choice = self._submenu_screen("submenu_stats_title", STATS_MENU_ITEMS, "0-3")
            
            if choice == "0":
                break
            elif choice == "1":
                self.stats_menu.show_system_stats()
                self.shell.pause()
            elif choice == "2":
                leagues = self.config_manager.get_leagues()
                if not leagues:
                    print(f"{COLORS['WARNING']}{self.i18n.t('no_configured_leagues')}")
                else:
                    for league_id in leagues:
                        self.stats_menu.show_league_stats(league_id)
                self.shell.pause()
            elif choice == "3":
                self.stats_menu.generate_report()
                self.shell.pause()
            else:
                self.shell.invalid_choice()
    
    def show_settings_menu(self) -> None:
        """Ayarlar menüsünü görüntüler."""
        while True:
            choice = self._submenu_screen("submenu_settings_title", SETTINGS_MENU_ITEMS, "0-5")
            
            if choice == "0":
                break
            elif choice == "1":
                self.settings_menu.edit_config()
            elif choice == "2":
                self.settings_menu.backup_data()
                self.shell.pause()
            elif choice == "3":
                self.settings_menu.restore_data()
                self.shell.pause()
            elif choice == "4":
                self.settings_menu.clear_data()
                self.shell.pause()
            elif choice == "5":
                self.settings_menu.show_about()
                self.shell.pause()
            else:
                self.shell.invalid_choice()
    
    def _ensure_directory(self, directory: str) -> None:
        """Dizin yoksa oluşturur."""
        if not os.path.exists(directory):
            os.makedirs(directory)
            logger.info(f"Dizin oluşturuldu: {directory}")

    def run_headless_fetch(
        self,
        league_id: Optional[int] = None,
        mode: str = "full",
        progress_factory: Optional[
            Callable[[int, int], Callable[[int, int, str], None]]
        ] = None,
    ) -> None:
        """
        Otomasyon / headless: web API ile aynı akışlar.

        Args:
            league_id: None ise tüm ligler; verilirse yalnızca o lig.
            mode: "full" → sezonlar + maç listeleri + detaylar;
                  "details" → yalnızca maç detayları (mevcut CSV'lerden).
            progress_factory: Web arka planında kullanılan (lo,hi) callback fabrikası; CLI'da None.
        """
        mode = (mode or "full").strip().lower()
        if mode not in ("full", "details"):
            raise ValueError(f"mode must be 'full' or 'details', got {mode!r}")

        if league_id is None:
            if mode == "full":
                self.update_all_leagues(progress_factory=progress_factory)
            else:
                print(f"\n{COLORS['INFO']}Headless: Tüm ligler için maç detayları çekiliyor...")
                cb = progress_factory(10, 89) if progress_factory else None
                self.match_data_fetcher.fetch_all_match_details(
                    max_seasons=0,
                    progress_callback=cb,
                )
            return

        lid = league_id
        lids = str(lid)
        if mode == "details":
            print(f"\n{COLORS['INFO']}Headless: Lig {lid} maç detayları çekiliyor...")
            cb = progress_factory(60, 89) if progress_factory else None
            self.match_data_fetcher.fetch_all_match_details(
                league_id=lids,
                max_seasons=0,
                progress_callback=cb,
            )
            return

        print(f"\n{COLORS['INFO']}Headless: Lig {lid} tam güncelleme (sezon → maç → detay)...")
        self.season_fetcher.fetch_seasons_for_league(lid)
        seasons = self.season_fetcher.get_seasons_for_league(lid)
        for season in seasons:
            sid = season.get("id")
            if sid is not None:
                self.match_fetcher.fetch_matches_for_season(lid, int(sid))
        cb_d = progress_factory(60, 89) if progress_factory else None
        self.match_data_fetcher.fetch_all_match_details(
            league_id=lids,
            max_seasons=0,
            progress_callback=cb_d,
        )

    def update_all_leagues(
        self,
        progress_factory: Optional[Callable[[int, int], Callable[[int, int, str], None]]] = None,
    ) -> None:
        """Tüm ligler için verileri (sezon, maç, detay) günceller (Headless / web arka plan)."""
        print(f"\n{COLORS['INFO']}Headless Mod: Tüm veriler güncelleniyor...")
        
        # 1. Sezonları güncelle (~%10–28 web bandı)
        print(f"\n{COLORS['SUBTITLE']}1. Sezon Verileri Güncelleniyor...")
        cb_seasons = progress_factory(10, 28) if progress_factory else None
        self.season_menu.update_all_seasons(progress_callback=cb_seasons)
        
        # 2. Maçları çek (~%28–55)
        print(f"\n{COLORS['SUBTITLE']}2. Maç Verileri Çekiliyor...")
        cb_matches = progress_factory(28, 55) if progress_factory else None
        self.match_menu.fetch_matches_for_all_leagues(max_seasons=0, progress_callback=cb_matches)
        
        # 3. Maç detaylarını çek (~%55–89, içte batch ilerlemesi)
        print(f"\n{COLORS['SUBTITLE']}3. Maç Detayları Çekiliyor...")
        cb_details = progress_factory(55, 89) if progress_factory else None
        self.match_data_menu.fetch_all_match_details(max_seasons=0, progress_callback=cb_details)
        
        print(f"\n{COLORS['SUCCESS']}Tüm işlemler tamamlandı.")

    def export_all_to_csv(self) -> None:
        """Tüm verileri CSV'ye aktarır (Headless mod için)."""
        print(f"\n{COLORS['INFO']}Headless Mod: CSV dışa aktarılıyor...")
        self.match_data_menu.convert_to_csv(scope="all")