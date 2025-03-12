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
    
    def print_system_status(self) -> None:
        """Sistem durumunu görüntüler."""
        leagues = self.config_manager.get_leagues()
        league_count = len(leagues) if leagues else 0
        
        # Toplam sezon sayısını hesapla
        season_count = 0
        for league_seasons in self.season_fetcher.league_seasons.values():
            season_count += len(league_seasons)
        
        print(f"\n{COLORS['INFO']}Sistem Durumu:")
        print(f"  Yapılandırılmış Lig: {COLORS['SUCCESS']}{league_count}")
        print(f"  Yüklenen Sezon: {COLORS['SUCCESS']}{season_count}")
    
    def print_main_menu(self) -> None:
        """Ana menüyü görüntüler."""
        print(f"\n{COLORS['SUBTITLE']}Ana Menü:")
        print("-" * 50)
        print("1. 🏆 Lig Yönetimi")
        print("2. 📅 Sezon Verileri")
        print("3. 🎮 Maç Verileri")
        print("4. 📈 Maç Detayları")
        print("5. 📊 İstatistikler")
        print("6. ⚙️ Ayarlar")
        print(f"{COLORS['WARNING']}0. ❌ Çıkış")
    
    def run(self) -> None:
        """Kullanıcı arayüzünü çalıştırır."""
        try:
            while True:
                self.clear_screen()
                self.print_header()
                self.print_system_status()
                self.print_main_menu()
                
                choice = input("\nSeçiminiz (0-6): ")
                
                if choice == "0":
                    print(f"\n{COLORS['INFO']}SofaScore Scraper'dan çıkılıyor. Hoşçakalın!")
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
                    input(f"{COLORS['WARNING']}Geçersiz seçim! Devam etmek için Enter'a basın...")
        
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
            
            print(f"\n{COLORS['SUBTITLE']}Lig Yönetimi:")
            print("-" * 50)
            print("1. 📋 Ligleri Listele")
            print("2. ➕ Yeni Lig Ekle")
            print("3. 🔄 Lig Yapılandırmasını Yeniden Yükle")
            print("4. 🔍 Lig Ara (Henüz Uygulanmadı)")
            print(f"{COLORS['WARNING']}0. ⬅️ Ana Menüye Dön")
            
            choice = input("\nSeçiminiz (0-4): ")
            
            if choice == "0":
                break
            elif choice == "1":
                self.league_menu.list_leagues()
                input("\nDevam etmek için Enter'a basın...")
            elif choice == "2":
                self.league_menu.add_new_league()
                input("\nDevam etmek için Enter'a basın...")
            elif choice == "3":
                self.league_menu.reload_leagues()
                input("\nDevam etmek için Enter'a basın...")
            elif choice == "4":
                self.league_menu.search_leagues()
                input("\nDevam etmek için Enter'a basın...")
            else:
                input(f"{COLORS['WARNING']}Geçersiz seçim! Devam etmek için Enter'a basın...")
    
    def show_season_menu(self) -> None:
        """Sezon verileri menüsünü görüntüler."""
        while True:
            self.clear_screen()
            self.print_header()
            
            print(f"\n{COLORS['SUBTITLE']}Sezon Verileri:")
            print("-" * 50)
            print("1. 🔄 Tüm Ligler İçin Sezonları Güncelle")
            print("2. 📋 Tek Lig İçin Sezonları Güncelle")
            print("3. 📊 Sezonları Listele")
            print(f"{COLORS['WARNING']}0. ⬅️ Ana Menüye Dön")
            
            choice = input("\nSeçiminiz (0-3): ")
            
            if choice == "0":
                break
            elif choice == "1":
                self.season_menu.update_all_seasons()
                input("\nDevam etmek için Enter'a basın...")
            elif choice == "2":
                self.season_menu.update_league_seasons()
                input("\nDevam etmek için Enter'a basın...")
            elif choice == "3":
                self.season_menu.list_seasons()
                input("\nDevam etmek için Enter'a basın...")
            else:
                input(f"{COLORS['WARNING']}Geçersiz seçim! Devam etmek için Enter'a basın...")
    
    def show_match_menu(self) -> None:
        """Maç verileri menüsünü görüntüler."""
        while True:
            self.clear_screen()
            self.print_header()
            
            print(f"\n{COLORS['SUBTITLE']}Maç Verileri:")
            print("-" * 50)
            print("1. 🏆 Tek Lig İçin Maçları Çek")
            print("2. 🔄 Tüm Ligler İçin Maçları Çek")
            print("3. 📋 Çekilen Maçları Listele")
            print(f"{COLORS['WARNING']}0. ⬅️ Ana Menüye Dön")
            
            choice = input("\nSeçiminiz (0-3): ")
            
            if choice == "0":
                break
            elif choice == "1":
                self.match_menu.fetch_matches_for_league()
                input("\nDevam etmek için Enter'a basın...")
            elif choice == "2":
                self.match_menu.fetch_matches_for_all_leagues()
                input("\nDevam etmek için Enter'a basın...")
            elif choice == "3":
                self.match_menu.list_matches()
                input("\nDevam etmek için Enter'a basın...")
            else:
                input(f"{COLORS['WARNING']}Geçersiz seçim! Devam etmek için Enter'a basın...")
    
    def show_match_data_menu(self) -> None:
        """Maç detayları menüsünü görüntüler."""
        while True:
            self.clear_screen()
            self.print_header()
            
            print(f"\n{COLORS['SUBTITLE']}Maç Detayları:")
            print("-" * 50)
            print("1. 🏟️ Belirli Maçlar İçin Detayları Çek")
            print("2. 🔄 Tüm Maçlar İçin Detayları Çek")
            print("3. 📊 CSV Veri Seti Oluştur")
            print(f"{COLORS['WARNING']}0. ⬅️ Ana Menüye Dön")
            
            choice = input("\nSeçiminiz (0-3): ")
            
            if choice == "0":
                break
            elif choice == "1":
                self.match_data_menu.fetch_match_details()
                input("\nDevam etmek için Enter'a basın...")
            elif choice == "2":
                self.match_data_menu.fetch_all_match_details()
                input("\nDevam etmek için Enter'a basın...")
            elif choice == "3":
                self.match_data_menu.convert_to_csv()
                input("\nDevam etmek için Enter'a basın...")
            else:
                input(f"{COLORS['WARNING']}Geçersiz seçim! Devam etmek için Enter'a basın...")
    
    def show_stats_menu(self) -> None:
        """İstatistikler menüsünü görüntüler."""
        while True:
            self.clear_screen()
            self.print_header()
            
            print(f"\n{COLORS['SUBTITLE']}İstatistikler:")
            print("-" * 50)
            print("1. 🖥️ Sistem İstatistikleri")
            print("2. 🏆 Lig İstatistikleri")
            print("3. 📃 Rapor Oluştur")
            print(f"{COLORS['WARNING']}0. ⬅️ Ana Menüye Dön")
            
            choice = input("\nSeçiminiz (0-3): ")
            
            if choice == "0":
                break
            elif choice == "1":
                self.stats_menu.show_system_stats()
                input("\nDevam etmek için Enter'a basın...")
            elif choice == "2":
                self.stats_menu.show_league_stats()
                input("\nDevam etmek için Enter'a basın...")
            elif choice == "3":
                self.stats_menu.generate_report()
                input("\nDevam etmek için Enter'a basın...")
            else:
                input(f"{COLORS['WARNING']}Geçersiz seçim! Devam etmek için Enter'a basın...")
    
    def show_settings_menu(self) -> None:
        """Ayarlar menüsünü görüntüler."""
        while True:
            self.clear_screen()
            self.print_header()
            
            print(f"\n{COLORS['SUBTITLE']}Ayarlar:")
            print("-" * 50)
            print("1. ⚙️ Yapılandırma Düzenle")
            print("2. 💾 Veri Yedekle")
            print("3. 📤 Veri Geri Yükle")
            print("4. 🧹 Veri Temizle")
            print("5. ℹ️ Hakkında")
            print(f"{COLORS['WARNING']}0. ⬅️ Ana Menüye Dön")
            
            choice = input("\nSeçiminiz (0-5): ")
            
            if choice == "0":
                break
            elif choice == "1":
                self.settings_menu.edit_config()
                input("\nDevam etmek için Enter'a basın...")
            elif choice == "2":
                self.settings_menu.backup_data()
                input("\nDevam etmek için Enter'a basın...")
            elif choice == "3":
                self.settings_menu.restore_data()
                input("\nDevam etmek için Enter'a basın...")
            elif choice == "4":
                self.settings_menu.clear_data()
                input("\nDevam etmek için Enter'a basın...")
            elif choice == "5":
                self.settings_menu.show_about()
                input("\nDevam etmek için Enter'a basın...")
            else:
                input(f"{COLORS['WARNING']}Geçersiz seçim! Devam etmek için Enter'a basın...")
    
    def _ensure_directory(self, directory: str) -> None:
        """Dizin yoksa oluşturur."""
        if not os.path.exists(directory):
            os.makedirs(directory)
            logger.info(f"Dizin oluşturuldu: {directory}")