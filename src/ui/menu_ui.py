"""
SofaScore Scraper için menü işlemleri modülü.
Bu modül, lig ve sezon ile ilgili UI işlemlerini içerir.
"""

import os
from typing import Dict, Any, Optional, List
from colorama import Fore, Style

from src.config_manager import ConfigManager
from src.season_fetcher import SeasonFetcher
from src.logger import get_logger

# Logger'ı al
logger = get_logger("MenuUI")


class LeagueMenuHandler:
    """Lig yönetimi menü işlemleri sınıfı."""
    
    def __init__(self, config_manager: ConfigManager, colors: Dict[str, str]):
        """
        LeagueMenuHandler sınıfını başlatır.
        
        Args:
            config_manager: Konfigürasyon yöneticisi
            colors: Renk tanımlamaları sözlüğü
        """
        self.config_manager = config_manager
        self.colors = colors
    
    def list_leagues(self) -> None:
        """Yapılandırılmış ligleri listeler."""
        try:
            leagues = self.config_manager.get_leagues()
            
            print(f"\n{self.colors['SUBTITLE']}Yapılandırılmış Ligler ({len(leagues)}):")
            print("-" * 50)
            
            if not leagues:
                print(f"{self.colors['WARNING']}Yapılandırılmış lig bulunamadı.")
                return
                
            sorted_leagues = sorted(leagues.items(), key=lambda x: x[1])
            
            for league_id, league_name in sorted_leagues:
                print(f"{self.colors['SUCCESS']}{league_name} {self.colors['DIM']}(ID: {league_id})")
                
        except Exception as e:
            logger.error(f"Ligler listelenirken hata: {str(e)}")
    
    def add_new_league(self) -> None:
        """Yeni bir lig ekler."""
        try:
            print(f"\n{self.colors['SUBTITLE']}Yeni Lig Ekle:")
            print("-" * 50)
            
            # Lig adını al
            league_name = input("Lig adı: ").strip()
            if not league_name:
                print(f"{self.colors['WARNING']}Lig adı boş olamaz!")
                return
            
            # Lig ID'sini al
            league_id_str = input("Lig ID: ").strip()
            if not league_id_str.isdigit():
                print(f"{self.colors['WARNING']}Geçersiz ID formatı! Sayısal bir değer girilmelidir.")
                return
                
            league_id = int(league_id_str)
            
            # Ligi ekle
            success = self.config_manager.add_league(league_name, league_id)
            
            if success:
                print(f"\n{self.colors['SUCCESS']}✅ {league_name} (ID: {league_id}) başarıyla eklendi!")
            else:
                print(f"\n{self.colors['WARNING']}❌ Lig eklenirken bir hata oluştu. Bu ID zaten kullanılıyor olabilir.")
                
        except Exception as e:
            logger.error(f"Lig eklenirken hata: {str(e)}")
            print(f"\n{self.colors['WARNING']}Hata: {str(e)}")
    
    def reload_leagues(self) -> None:
        """Lig yapılandırmasını yeniden yükler."""
        try:
            success = self.config_manager.reload_config()
            leagues = self.config_manager.get_leagues()
            leagues_count = len(leagues)
            
            if success:
                print(f"\n{self.colors['SUCCESS']}✅ Lig yapılandırması yeniden yüklendi. ({leagues_count} lig)")
            else:
                print(f"\n{self.colors['WARNING']}❌ Yapılandırma yeniden yüklenirken hata oluştu.")
                
        except Exception as e:
            logger.error(f"Ligler yeniden yüklenirken hata: {str(e)}")
            print(f"\n{self.colors['WARNING']}Hata: {str(e)}")
    
    def search_leagues(self) -> None:
        """Ligleri arama işlemi."""
        try:
            print(f"\n{self.colors['SUBTITLE']}Lig Ara (henüz uygulanmadı):")
            print(f"{self.colors['INFO']}Bu özellik ileride eklenecektir.")
        except Exception as e:
            logger.error(f"Lig arama işlemi sırasında hata: {str(e)}")


class SeasonMenuHandler:
    """Sezon yönetimi menü işlemleri sınıfı."""
    
    def __init__(self, config_manager: ConfigManager, season_fetcher: SeasonFetcher, colors: Dict[str, str]):
        """
        SeasonMenuHandler sınıfını başlatır.
        
        Args:
            config_manager: Konfigürasyon yöneticisi
            season_fetcher: Sezon veri çekici
            colors: Renk tanımlamaları sözlüğü
        """
        self.config_manager = config_manager
        self.season_fetcher = season_fetcher
        self.colors = colors
        self.league_menu = LeagueMenuHandler(config_manager, colors)
    
    def update_all_seasons(self) -> None:
        """Tüm ligler için sezon verilerini günceller."""
        try:
            leagues = self.config_manager.get_leagues()
            
            if not leagues:
                print(f"\n{self.colors['WARNING']}❌ Hiç lig tanımlanmamış! Önce lig ekleyiniz.")
                return
                
            print("\nTüm ligler için sezon verileri çekiliyor...")
            
            total_seasons = 0
            for league_id, league_name in leagues.items():
                try:
                    print(f"  - {league_name} (ID: {league_id}) sezonları çekiliyor...")
                    
                    # Tüm sezonları çek
                    all_seasons = self.season_fetcher.fetch_seasons_for_league(league_id)
                    total_seasons += len(all_seasons)
                    
                    print(f"    ✓ {len(all_seasons)} sezon bulundu")
                except Exception as e:
                    logger.error(f"{league_name} için sezon verisi çekilirken hata: {str(e)}")
                    print(f"    ✗ Hata: {str(e)}")
            
            print(f"\n{self.colors['SUCCESS']}✅ Tüm ligler için toplam {total_seasons} sezon başarıyla çekildi.")
            
        except Exception as e:
            logger.error(f"Tüm sezon verileri güncellenirken hata: {str(e)}")
            print(f"\n{self.colors['WARNING']}❌ Hata: {str(e)}")
    
    def update_league_seasons(self) -> None:
        """Belirli bir lig için sezon verilerini günceller."""
        try:
            leagues = self.config_manager.get_leagues()
            
            if not leagues:
                print(f"\n{self.colors['WARNING']}❌ Hiç lig tanımlanmamış! Önce lig ekleyiniz.")
                return
            
            print("\nLig Listesi:")
            for i, (league_id, league_name) in enumerate(leagues.items(), 1):
                print(f"{i}. {league_name} (ID: {league_id})")
            
            league_choice = input("\nSezonlarını güncellemek istediğiniz ligin numarasını girin (0: İptal): ").strip()
            
            if league_choice == "0":
                return
                
            try:
                league_index = int(league_choice) - 1
                if league_index < 0 or league_index >= len(leagues):
                    print(f"\n{self.colors['WARNING']}Geçersiz lig numarası!")
                    return
                    
                # Seçilen ligi al
                league_id = list(leagues.keys())[league_index]
                league_name = leagues[league_id]
                
                print(f"\n{self.colors['SUBTITLE']}{league_name} (ID: {league_id}) için sezonlar çekiliyor...")
                
                # Tüm sezonları çek
                all_seasons = self.season_fetcher.fetch_seasons_for_league(league_id)
                
                print(f"\n{self.colors['SUCCESS']}✅ {len(all_seasons)} sezon başarıyla çekildi:")
                for season in all_seasons:
                    print(f"  - {season.get('name', 'İsimsiz Sezon')} ({season.get('year', 'Yıl bilgisi yok')})")
                
            except ValueError:
                print(f"\n{self.colors['WARNING']}Geçersiz numara formatı!")
            except Exception as e:
                logger.error(f"Lig sezonları güncellenirken hata: {str(e)}")
                print(f"\n{self.colors['WARNING']}❌ Hata: {str(e)}")
                
        except Exception as e:
            logger.error(f"Lig sezonları güncellenirken hata: {str(e)}")
            print(f"\n{self.colors['WARNING']}❌ Hata: {str(e)}")
    
    def list_seasons(self) -> None:
        """Ligler ve sezonları listeler."""
        try:
            print(f"\n{self.colors['SUBTITLE']}Lig ve Sezon Listesi:")
            print("-" * 50)
            
            seasons_data = self.season_fetcher.league_seasons
            leagues = self.config_manager.get_leagues()
            
            # Lig bazında verileri görüntüle
            for league_id, league_seasons in sorted(seasons_data.items(), key=lambda x: leagues.get(x[0], f"Bilinmeyen Lig {x[0]}")):
                league_name = leagues.get(league_id, f"Bilinmeyen Lig {league_id}")
                print(f"\n{self.colors['INFO']}● {league_name} {self.colors['DIM']}(ID: {league_id}):")
                
                # Sezonları listeye
                if not league_seasons:
                    print(f"  {self.colors['WARNING']}Sezon verisi bulunamadı.")
                    continue
                    
                for season in league_seasons:
                    season_id = season.get("id", "?")
                    season_name = season.get("name", "Bilinmeyen Sezon")
                    season_year = season.get("year", "?")
                    print(f"  {self.colors['SUCCESS']}○ {season_name} {self.colors['DIM']}(ID: {season_id}, Yıl: {season_year})")
            
        except Exception as e:
            logger.error(f"Sezonları listelerken hata: {str(e)}")
            print(f"\n{self.colors['WARNING']}Hata: {str(e)}") 