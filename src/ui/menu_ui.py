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
from src.logger import get_logger
from src.i18n import get_i18n

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
        self.i18n = get_i18n()
    
    def list_leagues(self) -> None:
        """Yapılandırılmış ligleri listeler."""
        try:
            leagues = self.config_manager.get_leagues()
            
            print(f"\n{self.colors['SUBTITLE']}{self.i18n.t('configured_leagues_list')} ({len(leagues)}):")
            print("-" * 50)
            
            if not leagues:
                print(f"{self.colors['WARNING']}{self.i18n.t('no_configured_leagues')}")
                return
                
            sorted_leagues = sorted(leagues.items(), key=lambda x: x[1])
            
            for league_id, league_name in sorted_leagues:
                print(f"{self.colors['SUCCESS']}{league_name} {self.colors['DIM']}(ID: {league_id})")
                
        except Exception as e:
            logger.error(f"Ligler listelenirken hata: {str(e)}")
    
    def add_new_league(self) -> None:
        """Yeni bir lig ekler."""
        try:
            print(f"\n{self.colors['SUBTITLE']}{self.i18n.t('add_new_league_title')}")
            print("-" * 50)
            
            # Lig adını al
            league_name = input(f"{self.i18n.t('league_name_prompt')} ").strip()
            if not league_name:
                print(f"{self.colors['WARNING']}{self.i18n.t('league_name_empty_error')}")
                return
            
            # Lig ID'sini al
            league_id_str = input(f"{self.i18n.t('league_id_prompt')} ").strip()
            if not league_id_str.isdigit():
                print(f"{self.colors['WARNING']}{self.i18n.t('invalid_id_format')}")
                return
                
            league_id = int(league_id_str)
            
            # Ligi ekle
            success = self.config_manager.add_league(league_name, league_id)
            
            if success:
                print(f"\n{self.colors['SUCCESS']}✅ {self.i18n.t('league_added_success', league_name=league_name, league_id=league_id)}")
            else:
                print(f"\n{self.colors['WARNING']}❌ {self.i18n.t('league_add_error')}")
                
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
                print(f"\n{self.colors['SUCCESS']}✅ {self.i18n.t('league_config_reloaded')} ({len(leagues)} lig)")
            else:
                print(f"\n{self.colors['WARNING']}❌ {self.i18n.t('config_reload_error')}")
                
        except Exception as e:
            logger.error(f"Ligler yeniden yüklenirken hata: {str(e)}")
            print(f"\n{self.colors['WARNING']}Hata: {str(e)}")
    
    def search_leagues(self) -> None:
        """Ligleri arama işlemi."""
        try:
            print(f"\n{self.colors['SUBTITLE']}{self.i18n.t('submenu_league_search').replace(' (Henüz Uygulanmadı)', '').replace(' (Not Implemented)', '')}")
            print("-" * 50)
            
            search_term = input(f"{self.i18n.t('search_prompt')} ").strip().lower()
            
            if not search_term:
                return

            leagues = self.config_manager.get_leagues()
            found_leagues = []
            
            for league_id, league_name in leagues.items():
                if search_term in league_name.lower():
                    found_leagues.append((league_id, league_name))
            
            if found_leagues:
                print(f"\n{self.colors['SUCCESS']}{self.i18n.t('search_results_title', count=len(found_leagues))}")
                for league_id, league_name in found_leagues:
                     print(f"  {self.colors['SUCCESS']}● {league_name} {self.colors['DIM']}(ID: {league_id})")
            else:
                print(f"\n{self.colors['WARNING']}{self.i18n.t('no_matches_found')}")

        except Exception as e:
            logger.error(f"Lig arama işlemi sırasında hata: {str(e)}")
            print(f"\n{self.colors['WARNING']}Hata: {str(e)}")


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
        self.i18n = get_i18n()
    
    def update_all_seasons(self) -> None:
        """Tüm ligler için sezon verilerini günceller."""
        try:
            leagues = self.config_manager.get_leagues()
            
            if not leagues:
                print(f"\n{self.colors['WARNING']}❌ {self.i18n.t('no_leagues_defined')}")
                return
                
            print(f"\n{self.i18n.t('fetching_seasons_for_all')}")
            
            total_seasons = 0
            for league_id, league_name in leagues.items():
                try:
                    print(f"  - {self.i18n.t('fetching_seasons_for', league_name=league_name, league_id=league_id)}")
                    
                    # Tüm sezonları çek
                    all_seasons = self.season_fetcher.fetch_seasons_for_league(league_id)
                    total_seasons += len(all_seasons)
                    
                    print(f"    ✓ {self.i18n.t('seasons_found_count', count=len(all_seasons))}")
                except Exception as e:
                    logger.error(f"{league_name} için sezon verisi çekilirken hata: {str(e)}")
                    print(f"    ✗ Hata: {str(e)}")
            
            print(f"\n{self.colors['SUCCESS']}✅ {self.i18n.t('all_seasons_fetched_success', count=total_seasons)}")
            
        except Exception as e:
            logger.error(f"Tüm sezon verileri güncellenirken hata: {str(e)}")
            print(f"\n{self.colors['WARNING']}❌ Hata: {str(e)}")
    
    def update_league_seasons(self) -> None:
        """Belirli bir lig için sezon verilerini günceller."""
        try:
            leagues = self.config_manager.get_leagues()
            
            if not leagues:
                print(f"\n{self.colors['WARNING']}❌ {self.i18n.t('no_leagues_defined')}")
                return
            
            print(f"\n{self.i18n.t('league_list')}")
            for i, (league_id, league_name) in enumerate(leagues.items(), 1):
                print(f"{i}. {league_name} (ID: {league_id})")
            
            league_choice = input(f"\n{self.i18n.t('select_league_to_update')} ").strip()
            
            if league_choice == "0":
                return
                
            try:
                league_index = int(league_choice) - 1
                if league_index < 0 or league_index >= len(leagues):
                    print(f"\n{self.colors['WARNING']}{self.i18n.t('invalid_league_num')}")
                    return
                    
                # Seçilen ligi al
                league_id = list(leagues.keys())[league_index]
                league_name = leagues[league_id]
                
                print(f"\n{self.colors['SUBTITLE']}{self.i18n.t('fetching_seasons_title', league_name=league_name, league_id=league_id)}")
                
                # Tüm sezonları çek
                all_seasons = self.season_fetcher.fetch_seasons_for_league(league_id)
                
                print(f"\n{self.colors['SUCCESS']}✅ {self.i18n.t('seasons_fetched_success', count=len(all_seasons))}")
                for season in all_seasons:
                    print(f"  - {season.get('name', self.i18n.t('unnamed_season'))} ({season.get('year', self.i18n.t('no_year_info'))})")
                
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
            print(f"\n{self.colors['SUBTITLE']}{self.i18n.t('league_season_list_title')}")
            print("-" * 50)
            
            seasons_data = self.season_fetcher.league_seasons
            leagues = self.config_manager.get_leagues()
            
            # Lig bazında verileri görüntüle
            for league_id, league_seasons in sorted(seasons_data.items(), key=lambda x: leagues.get(x[0], f"{self.i18n.t('unknown_league')} {x[0]}")):
                league_name = leagues.get(league_id, f"{self.i18n.t('unknown_league')} {league_id}")
                print(f"\n{self.colors['INFO']}● {league_name} {self.colors['DIM']}(ID: {league_id}):")
                
                # Sezonları listeye
                if not league_seasons:
                    print(f"  {self.colors['WARNING']}{self.i18n.t('no_seasons_found_for_league')}")
                    continue
                    
                for season in league_seasons:
                    season_id = season.get("id", "?")
                    season_name = season.get("name", self.i18n.t("unknown_season"))
                    season_year = season.get("year", "?")
                    print(f"  {self.colors['SUCCESS']}○ {season_name} {self.colors['DIM']}(ID: {season_id}, Yıl: {season_year})")
            
        except Exception as e:
            logger.error(f"Sezonları listelerken hata: {str(e)}")
            print(f"\n{self.colors['WARNING']}Hata: {str(e)}") 