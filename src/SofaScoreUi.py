"""
SofaScore Scraper için terminal kullanıcı arayüzü modülü.
Bu modül, terminal üzerinden SofaScore verilerine erişim sağlar.
"""

import os
import json
import csv
import time
import platform
import sys
from typing import Dict, Any, Optional, List, Union

# Colorama renk kütüphanesi
from colorama import init, Fore, Back, Style
init(autoreset=True)  # Terminal renklendirmesi için otomatik sıfırlama

# Proje modülleri
from src.config_manager import ConfigManager
from src.season_fetcher import SeasonFetcher
from src.match_fetcher import MatchFetcher
from src.match_data_fetcher import MatchDataFetcher
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
    "RESET": Style.RESET_ALL
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
        self._ensure_directory(os.path.join(data_dir, "match_data"))
        self._ensure_directory(os.path.join(data_dir, "datasets"))
        
        # Ana sınıfları başlat (dependency injection)
        self.config_manager = config_manager or ConfigManager(config_path)
        self.data_dir = data_dir
        
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
        logger.info("SofaScore Scraper kullanıcı arayüzü başlatıldı")
    
    def clear_screen(self) -> None:
        """Terminal ekranını temizler."""
        if platform.system() == "Windows":
            os.system("cls")
        else:
            os.system("clear")
    
    def print_header(self) -> None:
        """Uygulama başlığını yazdırır."""
        print(f"\n{COLORS['TITLE']}SofaScore Scraper v1.0.0")
        print("==========================================")
    
    def print_menu(self) -> None:
        """Ana menüyü ekrana basar."""
        # Sistem durumunu göster
        self.print_system_status()
        
        print(f"\n{COLORS['TITLE']}{self.i18n.t('main_menu_title')}")
        print("-" * 50)
        print(self.i18n.t("menu_league_management"))
        print(self.i18n.t("menu_season_data"))
        print(self.i18n.t("menu_match_data"))
        print(self.i18n.t("menu_match_details"))
        print(self.i18n.t("menu_stats"))
        print(self.i18n.t("menu_settings"))
        print(self.i18n.t("menu_exit"))
        
    def print_system_status(self) -> None:
        """Sistem durumu özetini gösterir."""
        leagues = self.config_manager.get_leagues()
        # Sezon sayısını hızlıca al (dosyaları saymadan, sadece klasör var mı diye bakılabilir ama
        # şimdilik basit tutalım, detaylı sayım yavaş olabilir)
        
        # Basit istatistikler
        league_count = len(leagues)
        
        # Sezon dosya sayısını bul (yaklaşık)
        seasons_dir = os.path.join(self.data_dir, "seasons")
        season_files = 0
        if os.path.exists(seasons_dir):
            season_files = len([f for f in os.listdir(seasons_dir) if f.endswith("_seasons.json")])
        
        print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('system_status')}")
        print(f"  {self.i18n.t('configured_leagues')} {COLORS['SUCCESS']}{league_count}")
        print(f"  {self.i18n.t('loaded_seasons')} {COLORS['SUCCESS']}{season_files}")  # Bu aslında liglerin sezon dosyası sayısı

    def run(self) -> None:
        """Kullanıcı arayüzünü çalıştırır."""
        try:
            while True:
                self.clear_screen()
                self.print_header()
                self.print_menu()
                
                choice = input(f"\n{self.i18n.t('selection_prompt')}").strip()
                
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
                    input(f"{COLORS['WARNING']}{self.i18n.t('invalid_choice_error')} {self.i18n.t('press_enter_to_continue')}")
        
        except KeyboardInterrupt:
            print(f"\n\n{COLORS['INFO']}Program kullanıcı tarafından sonlandırıldı.")
        except Exception as e:
            logger.error(f"Kullanıcı arayüzünde hata: {str(e)}")
            print(f"\n{COLORS['ERROR']}Hata: {str(e)}")
            input("Devam etmek için Enter'a basın...")
    
    def show_league_menu(self) -> None:
        """Lig yönetimi menüsünü görüntüler."""
        while True:
            self.clear_screen()
            self.print_header()
            
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('submenu_league_title')}")
            print("-" * 50)
            print(self.i18n.t('submenu_league_list'))
            print(self.i18n.t('submenu_league_add'))
            print(self.i18n.t('submenu_league_reload'))
            print(self.i18n.t('submenu_league_search'))
            print(f"{COLORS['WARNING']}{self.i18n.t('submenu_back_main')}")
            
            choice = input(self.i18n.t('selection_prompt_range', range="0-4"))
            
            if choice == "0":
                break
            elif choice == "1":
                self.league_menu.list_leagues()
                input(self.i18n.t('press_enter_to_continue'))
            elif choice == "2":
                self.league_menu.add_new_league()
                input(self.i18n.t('press_enter_to_continue'))
            elif choice == "3":
                self.league_menu.reload_leagues()
                input(self.i18n.t('press_enter_to_continue'))
            elif choice == "4":
                self.league_menu.search_leagues()
                input(self.i18n.t('press_enter_to_continue'))
            else:
                input(f"{COLORS['WARNING']}{self.i18n.t('invalid_choice_error')} {self.i18n.t('press_enter_to_continue')}")
    
    def show_season_menu(self) -> None:
        """Sezon verileri menüsünü görüntüler."""
        while True:
            self.clear_screen()
            self.print_header()
            
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('submenu_season_title')}")
            print("-" * 50)
            print(self.i18n.t('submenu_season_update_all'))
            print(self.i18n.t('submenu_season_update_one'))
            print(self.i18n.t('submenu_season_list'))
            print(f"{COLORS['WARNING']}{self.i18n.t('submenu_back_main')}")
            
            choice = input(self.i18n.t('selection_prompt_range', range="0-3"))
            
            if choice == "0":
                break
            elif choice == "1":
                self.season_menu.update_all_seasons()
                input(self.i18n.t('press_enter_to_continue'))
            elif choice == "2":
                self.season_menu.update_league_seasons()
                input(self.i18n.t('press_enter_to_continue'))
            elif choice == "3":
                self.season_menu.list_seasons()
                input(self.i18n.t('press_enter_to_continue'))
            else:
                input(f"{COLORS['WARNING']}{self.i18n.t('invalid_choice_error')} {self.i18n.t('press_enter_to_continue')}")
    
    def show_match_menu(self) -> None:
        """Maç verileri menüsünü görüntüler."""
        while True:
            self.clear_screen()
            self.print_header()
            
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('submenu_match_title')}")
            print("-" * 50)
            print(self.i18n.t('submenu_match_fetch_one'))
            print(self.i18n.t('submenu_match_fetch_all'))
            print(self.i18n.t('submenu_match_list'))
            print(f"{COLORS['WARNING']}{self.i18n.t('submenu_back_main')}")
            
            choice = input(self.i18n.t('selection_prompt_range', range="0-3"))
            
            if choice == "0":
                break
            elif choice == "1":
                self.match_menu.fetch_matches_for_league()
                input(self.i18n.t('press_enter_to_continue'))
            elif choice == "2":
                self.match_menu.fetch_matches_for_all_leagues()
                input(self.i18n.t('press_enter_to_continue'))
            elif choice == "3":
                self.match_menu.list_matches()
                input(self.i18n.t('press_enter_to_continue'))
            else:
                input(f"{COLORS['WARNING']}{self.i18n.t('invalid_choice_error')} {self.i18n.t('press_enter_to_continue')}")
    
    def show_match_data_menu(self) -> None:
        """Maç detayları menüsünü görüntüler."""
        while True:
            self.clear_screen()
            self.print_header()
            
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('submenu_match_details_title')}")
            print("-" * 50)
            print(self.i18n.t('submenu_match_details_fetch_one'))
            print(self.i18n.t('submenu_match_details_fetch_all'))
            print(self.i18n.t('submenu_match_details_csv'))
            print(f"{COLORS['WARNING']}{self.i18n.t('submenu_back_main')}")
            
            choice = input(self.i18n.t('selection_prompt_range', range="0-3"))
            
            if choice == "0":
                break
            elif choice == "1":
                self.match_data_menu.fetch_match_details()
                input(self.i18n.t('press_enter_to_continue'))
            elif choice == "2":
                self.match_data_menu.fetch_all_match_details()
                input(self.i18n.t('press_enter_to_continue'))
            elif choice == "3":
                self.match_data_menu.convert_to_csv()
                input(self.i18n.t('press_enter_to_continue'))
            else:
                input(f"{COLORS['WARNING']}{self.i18n.t('invalid_choice_error')} {self.i18n.t('press_enter_to_continue')}")
    
    def show_stats_menu(self) -> None:
        """İstatistikler menüsünü görüntüler."""
        while True:
            self.clear_screen()
            self.print_header()
            
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('submenu_stats_title')}")
            print("-" * 50)
            print(self.i18n.t('submenu_stats_system'))
            print(self.i18n.t('submenu_stats_league'))
            print(self.i18n.t('submenu_stats_report'))
            print(f"{COLORS['WARNING']}{self.i18n.t('submenu_back_main')}")
            
            choice = input(self.i18n.t('selection_prompt_range', range="0-3"))
            
            if choice == "0":
                break
            elif choice == "1":
                self.stats_menu.show_system_stats()
                input(self.i18n.t('press_enter_to_continue'))
            elif choice == "2":
                # Tüm liglerin istatistiklerini göster
                leagues = self.config_manager.get_leagues()
                if not leagues:
                    print(f"{COLORS['WARNING']}{self.i18n.t('no_configured_leagues')}")
                else:
                    for league_id in leagues:
                        self.stats_menu.show_league_stats(league_id)
                input(self.i18n.t('press_enter_to_continue'))
            elif choice == "3":
                self.stats_menu.generate_report()
                input(self.i18n.t('press_enter_to_continue'))
            else:
                input(f"{COLORS['WARNING']}{self.i18n.t('invalid_choice_error')} {self.i18n.t('press_enter_to_continue')}")
    
    def show_settings_menu(self) -> None:
        """Ayarlar menüsünü görüntüler."""
        while True:
            self.clear_screen()
            self.print_header()
            
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('submenu_settings_title')}")
            print("-" * 50)
            print(self.i18n.t('submenu_settings_config'))
            print(self.i18n.t('submenu_settings_backup'))
            print(self.i18n.t('submenu_settings_restore'))
            print(self.i18n.t('submenu_settings_clean'))
            print(self.i18n.t('submenu_settings_about'))
            print(f"{COLORS['WARNING']}{self.i18n.t('submenu_back_main')}")
            
            choice = input(self.i18n.t('selection_prompt_range', range="0-5"))
            
            if choice == "0":
                break
            elif choice == "1":
                self.settings_menu.edit_config()
                # Config değişmiş olabilir, tekrar beklemeye gerek yok
            elif choice == "2":
                self.settings_menu.backup_data()
                input(self.i18n.t('press_enter_to_continue'))
            elif choice == "3":
                self.settings_menu.restore_data()
                input(self.i18n.t('press_enter_to_continue'))
            elif choice == "4":
                self.settings_menu.clear_data()
                input(self.i18n.t('press_enter_to_continue'))
            elif choice == "5":
                self.settings_menu.show_about()
                input(self.i18n.t('press_enter_to_continue'))
            else:
                input(f"{COLORS['WARNING']}{self.i18n.t('invalid_choice_error')} {self.i18n.t('press_enter_to_continue')}")
    
    def _ensure_directory(self, directory: str) -> None:
        """Dizin yoksa oluşturur."""
        if not os.path.exists(directory):
            os.makedirs(directory)
            logger.info(f"Dizin oluşturuldu: {directory}")

    def update_all_leagues(self) -> None:
        """Tüm ligler için verileri (sezon, maç, detay) günceller (Headless mod için)."""
        print(f"\n{COLORS['INFO']}Headless Mod: Tüm veriler güncelleniyor...")
        
        # 1. Sezonları güncelle
        print(f"\n{COLORS['SUBTITLE']}1. Sezon Verileri Güncelleniyor...")
        self.season_menu.update_all_seasons()
        
        # 2. Maçları çek
        print(f"\n{COLORS['SUBTITLE']}2. Maç Verileri Çekiliyor...")
        self.match_menu.fetch_matches_for_all_leagues(max_seasons=0)
        
        # 3. Maç detaylarını çek
        print(f"\n{COLORS['SUBTITLE']}3. Maç Detayları Çekiliyor...")
        self.match_data_menu.fetch_all_match_details(max_seasons=0)
        
        print(f"\n{COLORS['SUCCESS']}Tüm işlemler tamamlandı.")

    def export_all_to_csv(self) -> None:
        """Tüm verileri CSV'ye aktarır (Headless mod için)."""
        print(f"\n{COLORS['INFO']}Headless Mod: CSV dışa aktarılıyor...")
        self.match_data_menu.convert_to_csv(scope="all")