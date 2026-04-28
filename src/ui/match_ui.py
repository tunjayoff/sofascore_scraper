"""
SofaScore Scraper için maç işlemleri modülü.
Bu modül, maç ve maç detayları ile ilgili UI işlemlerini içerir.
"""

import os
from typing import Any, Callable, Dict, List, Optional, Union
from colorama import Fore, Style

from src.config_manager import ConfigManager
from src.season_fetcher import SeasonFetcher
from src.match_fetcher import MatchFetcher
from src.match_data_fetcher import MatchDataFetcher
from src.logger import get_logger
from rich.console import Console
from rich.table import Table
from src.i18n import get_i18n

# Logger'ı al
logger = get_logger("MatchUI")


class MatchMenuHandler:
    """Maç yönetimi menü işlemleri sınıfı."""
    
    def __init__(
        self, 
        config_manager: ConfigManager, 
        season_fetcher: SeasonFetcher,
        match_fetcher: MatchFetcher,
        colors: Dict[str, str]
    ):
        """
        MatchMenuHandler sınıfını başlatır.
        
        Args:
            config_manager: Konfigürasyon yöneticisi
            season_fetcher: Sezon veri çekici
            match_fetcher: Maç veri çekici
            colors: Renk tanımlamaları sözlüğü
        """
        self.config_manager = config_manager
        self.season_fetcher = season_fetcher
        self.match_fetcher = match_fetcher
        self.colors = colors
        self.console = Console(no_color=not self.config_manager.get_use_color())
        self.i18n = get_i18n()

    def _print_leagues_table(self, leagues: Dict[int, str]) -> None:
        table = Table(title=self.i18n.t("league_list_title"), show_header=True, header_style="bold magenta")
        table.add_column("#", style="dim", width=4)
        table.add_column(self.i18n.t("league_name"), style="cyan")
        table.add_column(self.i18n.t("id"), style="green")

        for i, (league_id, league_name) in enumerate(leagues.items(), 1):
            table.add_row(str(i), league_name, str(league_id))
        
        self.console.print(table)

    def _print_seasons_table(self, seasons: List[Dict[str, Any]]) -> None:
        table = Table(title=self.i18n.t("season_list_title"), show_header=True, header_style="bold magenta")
        table.add_column("#", style="dim", width=4)
        table.add_column(self.i18n.t("season_name"), style="cyan")
        table.add_column(self.i18n.t("year"), style="yellow")
        table.add_column(self.i18n.t("id"), style="green")

        for i, season in enumerate(seasons, 1):
            name = season.get("name", "Bilinmeyen")
            year = season.get("year", "Yok")
            sid = str(season.get("id", ""))
            table.add_row(str(i), name, year, sid)
        
        self.console.print(table)
    
    def fetch_matches_for_league(self) -> None:
        """Belirli bir lig için maç verilerini çeker."""
        try:
            # Ligleri al
            leagues = self.config_manager.get_leagues()
            if not leagues:
                print(self.i18n.t("no_leagues_found"))
                return
            
            # Lig listesini görüntüle
            self._print_leagues_table(leagues)
            
            # Lig seçimini al
            league_choice = input(self.i18n.t("select_league_prompt")).strip()
            
            if league_choice == "0":
                return
                
            try:
                league_index = int(league_choice) - 1
                if league_index < 0 or league_index >= len(leagues):
                    print(f"\nGeçersiz lig numarası!")
                    return
                    
                # Seçilen ligi al
                league_id = list(leagues.keys())[league_index]
                league_name = leagues[league_id]
                
                # Sezonları al - Önce yerel veriyi kontrol et
                seasons = self.season_fetcher.get_seasons_for_league(league_id)
                if not seasons:
                    print(self.i18n.t("checking_seasons"))
                    seasons = self.season_fetcher.fetch_seasons_for_league(league_id)
                
                if not seasons:
                    print(self.i18n.t("no_seasons_found_for_league"))
                    return
                
                print(self.i18n.t("viewing_seasons_for", league_name=league_name, league_id=league_id))
                
                # Sezonları tarihe göre sırala (en yeni en üstte)
                sorted_seasons = sorted(seasons, key=lambda s: self.season_fetcher._get_sortable_year_value(s.get("year", "")), reverse=True)
                
                # Sezon filtreleme seçenekleri
                print(self.i18n.t("season_filter_options"))
                print("-" * 50)
                print(self.i18n.t("all_seasons"))
                print(self.i18n.t("last_n_seasons"))
                print(self.i18n.t("specific_season"))
                print(self.i18n.t("cancel"))
                
                filter_choice = input(self.i18n.t("selection_prompt")).strip()
                
                if filter_choice == "0":
                    return
                
                selected_seasons = []
                
                # Tüm sezonlar
                if filter_choice == "1":
                    selected_seasons = sorted_seasons
                    print(self.i18n.t("all_seasons_selected", count=len(selected_seasons)))
                
                # Son N sezon
                elif filter_choice == "2":
                    try:
                        n_seasons = input(self.i18n.t("how_many_seasons_prompt")).strip()
                        n_seasons = int(n_seasons)
                        
                        if n_seasons <= 0 or n_seasons > len(sorted_seasons):
                            print(self.i18n.t("invalid_number_range", max=len(sorted_seasons)))
                            return
                        
                        selected_seasons = sorted_seasons[:n_seasons]
                        print(self.i18n.t("last_n_seasons_selected", count=n_seasons))
                    except ValueError:
                        print(self.i18n.t("invalid_number_format"))
                        return
                
                # Belirli bir sezon
                elif filter_choice == "3":
                    # Sezon listesini göster
                    self._print_seasons_table(sorted_seasons)
                    
                    # Sezon seçimini al
                    season_choice = input(self.i18n.t("select_season_prompt")).strip()
                    
                    if season_choice == "0":
                        return
                    
                    try:
                        season_index = int(season_choice) - 1
                        if season_index < 0 or season_index >= len(sorted_seasons):
                            print(self.i18n.t("invalid_season_num"))
                            return
                        
                        selected_seasons = [sorted_seasons[season_index]]
                        print(self.i18n.t("season_selected", season_name=selected_seasons[0].get('name', 'Sezon')))
                    except ValueError:
                        print(self.i18n.t("invalid_number_format"))
                        return
                
                else:
                    print(self.i18n.t("invalid_selection"))
                    return
                
                # Seçilen sezonlar için maç verilerini çek
                total_matches = 0
                for season in selected_seasons:
                    season_id = season.get("id")
                    season_name = season.get("name", "Bilinmeyen Sezon")
                    
                    print(self.i18n.t('fetching_match_data_for_league_season', league_name=league_name, season_name=season_name))
                    
                    success = self.match_fetcher.fetch_matches_for_season(league_id, season_id)
                    
                    if success:
                        print(f"  ✓ Maç verileri başarıyla çekildi.")
                        total_matches += 1
                    else:
                        print(f"  {self.i18n.t('matches_not_found')}")
                
                # Assuming 'results' and 'finished_matches' are not directly available here,
                # but the instruction implies a summary.
                # For now, we'll use total_matches as the 'total' and 'finished' count for simplicity
                # as the original code only tracked total_matches.
                print(self.i18n.t("matches_downloaded", league=league_name, season="selected seasons", total=total_matches, finished=total_matches))
                    
            except ValueError:
                print(self.i18n.t("invalid_number_format_error"))
                
        except Exception as e:
            logger.error(f"Error fetching matches: {str(e)}")
            print(self.i18n.t("matches_fetch_error", error=str(e)))
    
    def fetch_matches_for_all_leagues(
        self,
        max_seasons: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> None:
        """
        Tüm ligler için maç verilerini çeker.
        Args:
            max_seasons: Çekilecek sezon sayısı (0: Tümü, None: Kullanıcıya sor)
        """
        try:
            # Ligleri al
            leagues = self.config_manager.get_leagues()
            
            if not leagues:
                print(f"\n{self.i18n.t('error_no_saved_league')}")
                return
            
            league_list = list(leagues.items())
            n_leagues = len(league_list)
            if progress_callback and n_leagues > 0:
                progress_callback(0, n_leagues, f"Matches 0/{n_leagues} leagues (starting)")
            
            # Kaç sezon çekileceğini kullanıcıya sor (eğer parametre olarak gelmediyse)
            if max_seasons is None:
                max_seasons = -1
                while max_seasons < 0:
                    try:
                        print(f"\nKaç sezon çekmek istiyorsunuz?")
                        print(f"{self.i18n.t('all_seasons_0_last_n')}")
                        max_seasons_input = input(f"{self.i18n.t('season_count')} ")
                        max_seasons = int(max_seasons_input)
                        if max_seasons < 0:
                            print(f"{self.i18n.t('enter_valid_number_greater_0')}")
                    except ValueError:
                        print(f"{self.i18n.t('enter_valid_number')}")
            
            print(self.i18n.t('fetching_match_data_for_all_leagues'))
            if max_seasons > 0:
                print(f"{self.i18n.t('info_last_seasons_prefix')} {max_seasons} {self.i18n.t('info_last_seasons_suffix')}")
            else:
                print(f"{self.i18n.t('info_all_seasons_fetched')}")
            
            total_matches = 0
            for li, (league_id, league_name) in enumerate(league_list):
                try:
                    print(f"\n  🏆 {league_name} (ID: {league_id})")
                    
                    # Ligi çekmeden önce kontrol et
                    print(f"  ○ Sezonlar kontrol ediliyor...")
                    # Önce yerel veriyi kontrol et
                    seasons = self.season_fetcher.get_seasons_for_league(league_id)
                    
                    # Yerelde yoksa API'den çek
                    if not seasons:
                        print(f"  ○ {self.i18n.t('no_season_data_found_locally')}")
                        seasons = self.season_fetcher.fetch_seasons_for_league(league_id)
                    
                    if not seasons:
                        print(f"  {self.i18n.t('no_match_found_skipping')}")
                        if progress_callback and n_leagues > 0:
                            progress_callback(
                                li + 1,
                                n_leagues,
                                f"Matches {li + 1}/{n_leagues}: {league_name} (no seasons)",
                            )
                        continue
                    
                    # Sezonları tarihe göre sırala (en yeni en üstte)
                    sorted_seasons = sorted(seasons, key=lambda s: self.season_fetcher._get_sortable_year_value(s.get("year", "")), reverse=True)
                    if not sorted_seasons:
                        print(f"  {self.i18n.t('season_data_not_sorted')}")
                        if progress_callback and n_leagues > 0:
                            progress_callback(
                                li + 1,
                                n_leagues,
                                f"Matches {li + 1}/{n_leagues}: {league_name} (skip)",
                            )
                        continue
                    
                    # Sezon sayısını sınırla
                    if max_seasons > 0 and len(sorted_seasons) > max_seasons:
                        seasons_to_fetch = sorted_seasons[:max_seasons]
                        print(f"  ℹ️ Toplam {len(sorted_seasons)} sezon arasından son {max_seasons} {self.i18n.t('info_last_seasons_suffix')}")
                    else:
                        seasons_to_fetch = sorted_seasons
                        print(f"  ℹ️ Toplam {len(sorted_seasons)} {self.i18n.t('info_last_seasons_suffix')}")
                    
                    league_matches = 0
                    
                    # Her sezon için maç verilerini çek
                    for season in seasons_to_fetch:
                        season_id = season.get("id")
                        season_name = season.get("name", "Bilinmeyen Sezon")
                        
                        print(f"  ○ {season_name} {self.i18n.t('fetching_matches_for_season')}")
                        
                        try:
                            success = self.match_fetcher.fetch_matches_for_season(league_id, season_id)
                            
                            if success:
                                print(f"    {self.i18n.t('matches_fetched_successfully')}")
                                league_matches += 1
                            else:
                                print(f"    Maç bulunamadı.")
                        except Exception as e:
                            logger.error(f"{league_name} - {season_name} için maç verisi çekilirken hata: {str(e)}")
                            print(f"    Hata: {str(e)}")
                    
                    total_matches += league_matches
                    print(f"  {league_name} {self.i18n.t('total_matches_fetched_for_league')} {league_matches} maç verisi çekildi.")
                    if progress_callback and n_leagues > 0:
                        progress_callback(
                            li + 1,
                            n_leagues,
                            f"Matches {li + 1}/{n_leagues}: {league_name}",
                        )
                    
                except Exception as e:
                    logger.error(f"{league_name} için maç verisi çekilirken hata: {str(e)}")
                    print(f"  Hata: {str(e)}")
                    if progress_callback and n_leagues > 0:
                        progress_callback(
                            li + 1,
                            n_leagues,
                            f"Matches {li + 1}/{n_leagues}: {league_name} (error)",
                        )
            
            print(f"\n{self.i18n.t('info_total_matches_fetched')} {total_matches} {self.i18n.t('info_total_matches_fetched_suffix')}")
            
        except Exception as e:
            logger.error(f"Tüm ligler için maç verileri çekilirken hata: {str(e)}")
            print(f"\nHata: {str(e)}")
    
    def list_matches(self) -> None:
        """Çekilen maçları listeler."""
        try:
            # Maç veri dizinini kontrol et
            match_dir = self.config_manager.get_match_data_dir()
            
            if not os.path.exists(match_dir):
                print(f"\n{self.i18n.t('title_fetched_matches')}")
                print("-" * 50)
                print(f"{self.i18n.t('matches_not_found')}")
                return
                
            # Maç dosyalarını ara
            if not os.listdir(match_dir):
                print(f"{self.i18n.t('matches_not_found')}")
                return
                
            # Ligi seç
            leagues = self.config_manager.get_leagues()
            
            print(f"\nLigler:")
            for i, (league_id, league_name) in enumerate(leagues.items(), 1):
                print(f"{i}. {league_name} (ID: {league_id or '?'})")
            
            league_choice = input(f"\n{self.i18n.t('league_number_to_view_matches')} ").strip()
            
            try:
                league_index = int(league_choice) - 1
                if league_index < 0 or league_index >= len(leagues):
                    print(f"\nGeçersiz lig numarası!")
                    return
                    
                league_id = list(leagues.keys())[league_index]
                league_name = leagues[league_id]
                
                # Sezon seç
                seasons = self.season_fetcher.get_seasons_for_league(league_id)
                
                if not seasons:
                    print(f"\n{self.i18n.t('season_data_not_found_for_league')}")
                    return
                
                print(f"\nSezonlar:")
                for i, season in enumerate(seasons, 1):
                    season_id = season.get("id", "?")
                    season_name = season.get("name", "Bilinmeyen Sezon")
                    print(f"{i}. {season_name} (ID: {season_id})")
                
                season_choice = input(f"\n{self.i18n.t('season_number_to_view_matches')} ").strip()
                
                try:
                    season_index = int(season_choice) - 1
                    if season_index < 0 or season_index >= len(seasons):
                        print(f"\n{self.i18n.t('invalid_season_number')}")
                        return
                        
                    season = seasons[season_index]
                    season_id = season.get("id")
                    season_name = season.get("name", "Bilinmeyen Sezon")
                    
                    # Maç dizinini kontrol et - farklı klasör düzeni formatlarını dene
                    possible_dirs = []
                    
                    # 1. Format: lig_id_lig_adı/sezon_id_sezon_adı
                    league_name_safe = league_name.replace(' ', '_').replace('/', '_')
                    season_name_safe = season_name.replace(' ', '_').replace('/', '_')
                    
                    # Olası dizin şekilleri
                    league_dirs = [
                        os.path.join(match_dir, f"{league_id}_{league_name_safe}"),  # ID ile
                        os.path.join(match_dir, f"{league_name_safe}"),              # Sadece ad ile
                        os.path.join(match_dir, str(league_id))                     # Sadece ID ile
                    ]
                    
                    # Her olası lig dizinini kontrol et
                    for league_dir in league_dirs:
                        if os.path.exists(league_dir):
                            # Olası sezon dizinlerini kontrol et
                            season_dirs = [
                                os.path.join(league_dir, f"{season_id}_{season_name_safe}"),  # ID ile
                                os.path.join(league_dir, f"{season_name_safe}"),              # Sadece ad ile
                                os.path.join(league_dir, str(season_id))                     # Sadece ID ile
                            ]
                            
                            for season_dir in season_dirs:
                                if os.path.exists(season_dir):
                                    possible_dirs.append(season_dir)
                    
                    # Hiçbir dizin bulunamadıysa
                    if not possible_dirs:
                        print(f"\n{self.i18n.t('no_match_data_for_season')}")
                        return
                    
                    # Bulunan ilk dizini kullan
                    season_dir = possible_dirs[0]
                    
                    # Maç dosyalarını listele
                    match_files = {}
                    for file in os.listdir(season_dir):
                        # 1. Eski format: X_matches.json
                        if file.endswith("_matches.json"):
                            try:
                                round_num = file.split("_")[0]
                                match_files[round_num] = file
                            except:
                                continue
                        # 2. Yeni format: round_X.json
                        elif file.startswith("round_") and file.endswith(".json"):
                            try:
                                round_num = file.split("_")[1].split(".")[0]
                                match_files[round_num] = file
                            except:
                                continue
                    
                    if not match_files:
                        print(f"\n{self.i18n.t('no_match_data_for_season')}")
                        return
                    
                    print(f"\n{self.i18n.t('match_files')}")
                    for i, (round_num, match_file) in enumerate(sorted(match_files.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0), 1):
                        print(f"{i}. {round_num} {match_file}")
                
                except ValueError:
                    print(f"\n{self.i18n.t('invalid_season_number')}")
                    
            except ValueError:
                print(f"\nGeçersiz lig numarası!")
                
        except Exception as e:
            logger.error(f"Maçları listelerken hata: {str(e)}")
            print(f"\nHata: {str(e)}")


class MatchDataMenuHandler:
    """Maç detayları yönetimi menü işlemleri sınıfı."""
    
    def __init__(
        self, 
        config_manager: ConfigManager, 
        match_data_fetcher: MatchDataFetcher,
        colors: Dict[str, str]
    ):
        """
        MatchDataMenuHandler sınıfını başlatır.
        
        Args:
            config_manager: Konfigürasyon yöneticisi
            match_data_fetcher: Maç detayları veri çekici
            colors: Renk tanımlamaları sözlüğü
        """
        self.config_manager = config_manager
        self.match_data_fetcher = match_data_fetcher
        self.colors = colors
        self.i18n = get_i18n()
    
    def fetch_match_details(self) -> None:
        """Maç detaylarını çeker."""
        try:
            # Giriş yap
            match_ids_str = input(f"\n{self.i18n.t('match_id_comma')} ").strip()
            
            if not match_ids_str:
                print(f"\n{self.i18n.t('valid_match_id_not_found')}")
                return
            
            match_ids = [id.strip() for id in match_ids_str.split(",") if id.strip()]
            
            print(f"\n{self.i18n.t('info_fetching_match_details')}")
            
            success_count = 0
            for match_id in match_ids:
                try:
                    result = self.match_data_fetcher.fetch_match_details(match_id)
                    
                    if result:
                        print(f"✓ Maç ID {match_id}")
                        success_count += 1
                    else:
                        print(f"{self.i18n.t('fetch_details_error')} {match_id}: Detaylar çekilemedi.")
                        
                except Exception as e:
                    logger.error(f"Maç {match_id} için detay çekilirken hata: {str(e)}")
                    print(f"{self.i18n.t('fetch_details_error')} {match_id}: Hata: {str(e)}")
            
            print(f"\n{self.i18n.t('info_match_details_completed')} {success_count}/{len(match_ids)} {self.i18n.t('info_match_successful')}")
            
        except Exception as e:
            logger.error(f"Maç detaylarını çekerken hata: {str(e)}")
            print(f"\nHata: {str(e)}")
    
    def fetch_all_match_details(
        self,
        league_id: Optional[str] = None,
        max_seasons: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> None:
        """
        Tüm maçların detaylarını çeker.
        Args:
            league_id: Belirli bir lig ID'si (None: Tümü)
            max_seasons: Çekilecek sezon sayısı (0: Tümü, None: Kullanıcıya sor)
            progress_callback: Opsiyonel (done, total, msg) web/headless ilerlemesi
        """
        try:
            print(f"\n{self.i18n.t('title_fetching_details_all')}")
            
            # Ligleri al
            leagues = self.config_manager.get_leagues()
            if not leagues:
                print(f"\n{self.i18n.t('error_no_saved_league')}")
                return
            
            # Eğer parametreler geldiyse direkt işlemi yap
            if max_seasons is not None:
                # Belirli bir lig için
                if league_id:
                    print(f"\nLig ID {league_id} {self.i18n.t('fetching_match_details_for')}")
                    if max_seasons > 0:
                        print(f"Son {max_seasons} {self.i18n.t('info_last_seasons_suffix')}")
                    else:
                        print(f"Tüm sezonlar çekilecek")
                    
                    result = self.match_data_fetcher.fetch_all_match_details(
                        league_id=league_id,
                        max_seasons=max_seasons,
                        progress_callback=progress_callback,
                    )
                # Tüm ligler için
                else:
                    print(f"\n{self.i18n.t('fetching_match_details_for_all')}")
                    if max_seasons > 0:
                        print(f"{self.i18n.t('info_last_seasons_prefix')} {max_seasons} {self.i18n.t('info_last_seasons_suffix')}")
                    else:
                        print(f"{self.i18n.t('info_all_seasons_fetched')}")
                    
                    result = self.match_data_fetcher.fetch_all_match_details(
                        max_seasons=max_seasons,
                        progress_callback=progress_callback,
                    )
                
                if result:
                    print(f"\n{self.i18n.t('operation_success')}")
                else:
                    print(f"\n{self.i18n.t('operation_error')}")
                return

            # İnteraktif mod (parametre gelmediyse)
            # Filtreleme seçenekleri
            print(f"\n{self.i18n.t('title_filter_options')}")
            print("-" * 50)
            print(f"{self.i18n.t('menu_all_leagues')}")
            print("2. Belirli Bir Lig")
            print(f"{self.i18n.t('menu_cancel')}")
            
            filter_choice = input("\nSeçiminiz (0-2): ").strip()
            
            if filter_choice == "0":
                return
                
            # Tüm ligler
            if filter_choice == "1":
                # Kaç sezon çekileceğini kullanıcıya sor
                print(f"\n{self.i18n.t('seasons_to_fetch_details')}")
                print(f"{self.i18n.t('all_seasons_0_last_n')}")
                max_seasons_input = input(f"{self.i18n.t('season_count')} ")
                
                try:
                    max_seasons = int(max_seasons_input)
                    if max_seasons < 0:
                        print(f"{self.i18n.t('enter_valid_number_greater_0')}")
                        return
                except ValueError:
                    print(f"{self.i18n.t('enter_valid_number')}")
                    return
                
                print(f"\n{self.i18n.t('fetching_match_details_for_all')}")
                if max_seasons > 0:
                    print(f"{self.i18n.t('info_last_seasons_prefix')} {max_seasons} {self.i18n.t('info_last_seasons_suffix')}")
                else:
                    print(f"{self.i18n.t('info_all_seasons_fetched')}")
                
                # Bu noktada fetch_all_match_details'ı çağır
                result = self.match_data_fetcher.fetch_all_match_details(max_seasons=max_seasons)
                
                if result:
                    print(f"\n{self.i18n.t('match_details_success_all')}")
                else:
                    print(f"\n{self.i18n.t('match_details_error')}")
                
            # Belirli bir lig
            elif filter_choice == "2":
                # Lig listesini görüntüle
                print("\nLig Listesi:")
                for i, (league_id, league_name) in enumerate(leagues.items(), 1):
                    print(f"{i}. {league_name} (ID: {league_id})")
                
                # Lig seçimini al
                league_choice = input(f"\n{self.i18n.t('input_league_number_details')} ").strip()
                
                if league_choice == "0":
                    return
                    
                try:
                    league_index = int(league_choice) - 1
                    if league_index < 0 or league_index >= len(leagues):
                        print(f"\nGeçersiz lig numarası!")
                        return
                        
                    # Seçilen ligi al
                    league_id = list(leagues.keys())[league_index]
                    league_name = leagues[league_id]
                    
                    # Kaç sezon çekileceğini kullanıcıya sor
                    print(f"\n{self.i18n.t('seasons_to_fetch_details')}")
                    print(f"{self.i18n.t('all_seasons_0_last_n')}")
                    max_seasons_input = input(f"{self.i18n.t('season_count')} ")
                    
                    try:
                        max_seasons = int(max_seasons_input)
                        if max_seasons < 0:
                            print(f"{self.i18n.t('enter_valid_number_greater_0')}")
                            return
                    except ValueError:
                        print(f"{self.i18n.t('enter_valid_number')}")
                        return
                    
                    print(f"\n{league_name} {self.i18n.t('fetching_match_details_for')}")
                    if max_seasons > 0:
                        print(f"Son {max_seasons} {self.i18n.t('info_last_seasons_suffix')}")
                    else:
                        print(f"Tüm sezonlar çekilecek")
                    
                    # Bu noktada fetch_all_match_details'ı çağır
                    result = self.match_data_fetcher.fetch_all_match_details(league_id=league_id, max_seasons=max_seasons)
                    
                    if result:
                        print(f"\n✅ {league_name} {self.i18n.t('match_details_success_for')}")
                    else:
                        print(f"\n{self.i18n.t('match_details_error')}")
                        
                except ValueError:
                    print(f"\n{self.i18n.t('invalid_number_format')}")
                    return
            else:
                print(f"\n{self.i18n.t('invalid_selection')}")
                return
                
        except Exception as e:
            logger.error(f"Tüm maç detaylarını çekerken hata: {str(e)}")
            print(f"\nHata: {str(e)}")
    
    def convert_to_csv(self, scope: str = "interactive") -> None:
        """
        Maç verilerini CSV formatına dönüştürür.
        Args:
            scope: İşlem kapsamı ('interactive', 'single', 'league', 'all')
        """
        try:
            print(f"\n{self.i18n.t('title_csv_conversion')}")
            
            option = ""
            
            if scope == "interactive":
                # Dönüştürme seçenekleri
                print(f"{self.i18n.t('single_match_csv')}")
                print(f"{self.i18n.t('specific_league_csv')}")
                print(f"{self.i18n.t('all_leagues_csv')}")
                
                option = input("\nSeçiminiz (1-3): ").strip()
            elif scope == "single":
                option = "1"
            elif scope == "league":
                option = "2"
            elif scope == "all":
                option = "3"
            
            # Tek maç CSV
            if option == "1":
                match_id = input(f"\n{self.i18n.t('csv_match_id')} ").strip()
                
                if not match_id:
                    print(f"\n❌ Geçerli maç ID'si bulunamadı")
                    return
                
                result = self.match_data_fetcher.convert_match_to_csv(match_id)
                
                if result:
                    csv_path = result
                    print(f"\n{self.i18n.t('csv_created_success')} {csv_path}")
                else:
                    print(f"\n{self.i18n.t('csv_created_error')}")
            
            # Belirli bir lig için CSV
            elif option == "2":
                # Ligleri al ve göster
                leagues = self.config_manager.get_leagues()
                if not leagues:
                    print(f"\n❌ {self.i18n.t('error_no_saved_league')}")
                    return
                
                print("\nLig Listesi:")
                for i, (league_id, league_name) in enumerate(leagues.items(), 1):
                    print(f"{i}. {league_name} (ID: {league_id})")
                
                # Lig seçimini al
                league_choice = input(f"\n{self.i18n.t('csv_input_league_number')} ").strip()
                
                if league_choice == "0":
                    return
                    
                try:
                    league_index = int(league_choice) - 1
                    if league_index < 0 or league_index >= len(leagues):
                        print(f"\n❌ Geçersiz lig numarası!")
                        return
                        
                    # Seçilen ligi al
                    league_id = list(leagues.keys())[league_index]
                    league_name = leagues[league_id]
                    
                    print(f"\n'{league_name}' (ID: {league_id}) {self.i18n.t('creating_csv_for')}")
                    
                    result = self.match_data_fetcher.convert_league_matches_to_csv(league_id)
                    
                    if result:
                        csv_paths = result
                        print(f"\n{self.i18n.t('csv_files_created_success')}")
                        for csv_path in csv_paths:
                            print(f"  - {csv_path}")
                    else:
                        print(f"\n{self.i18n.t('csv_files_created_error')}")
                        
                except ValueError:
                    print(f"\n❌ {self.i18n.t('invalid_number_format')}")
                    return
            
            # Tüm ligler için CSV
            elif option == "3":
                result = self.match_data_fetcher.convert_all_matches_to_csv()
                
                if isinstance(result, list):
                    print(f"\n✅ {len(result)} lig için CSV dosyaları başarıyla oluşturuldu:")
                    for csv_path in result:
                        print(f"  - {csv_path}")
                elif result:
                    print(f"\n{self.i18n.t('csv_created_success')} {result}")
                else:
                    print(f"\n{self.i18n.t('csv_created_error')}")
            else:
                print(f"\n{self.i18n.t('error_invalid_option')}")
                
        except Exception as e:
            logger.error(f"CSV dönüştürürken hata: {str(e)}")
            print(f"\nHata: {str(e)}")
    
    def show_menu(self) -> None:
        """Menüyü gösterir ve seçimleri işler."""
        while True:
            print("\n" + "=" * 50)
            print(f"{self.i18n.t('title_match_details_management')}")
            print("=" * 50)
            
            print(f"\n{self.i18n.t('menu_fetch_single_match')}")
            print(f"{self.i18n.t('menu_fetch_match_from_id')}")
            print(f"{self.i18n.t('menu_convert_all_to_csv')}")
            print(f"{self.i18n.t('menu_generate_analysis_report')}")
            print(f"{self.i18n.t('menu_return_main')}")
            
            choice = input("\nSeçiminiz: ").strip()
            
            if choice == "1":
                self.fetch_single_match()
            elif choice == "2":
                self.fetch_from_id_list()
            elif choice == "3":
                self.convert_to_csv()
            elif choice == "4":
                self.generate_file_report()
            elif choice == "0":
                break
            else:
                print(f"\n❌ {self.i18n.t('invalid_selection')}")
    
    def generate_file_report(self) -> None:
        """Maç dosyalarının durumunu analiz eder ve rapor oluşturur."""
        try:
            print(f"\n{self.i18n.t('title_analysis_report')}")
            
            # Kullanıcıya özel dizin seçeneği sun
            custom_path = input(f"\n{self.i18n.t('custom_dir_path')} ").strip()
            
            # Rapor oluştur
            if custom_path:
                if not os.path.isdir(custom_path):
                    print(f"\n{self.i18n.t('invalid_directory_path')} {custom_path}")
                    return
                    
                result = self.match_data_fetcher.generate_file_report(custom_path)
            else:
                result = self.match_data_fetcher.generate_file_report()
            
            # Sonuçları göster (fonksiyon zaten ekrana yazdırıyor)
            if result:
                # Opsiyonel olarak CSV ve JSON dosya yollarını göster
                print(f"\n{self.i18n.t('report_files')}")
                print(f"JSON: {result.get('json_report_path', self.i18n.t('not_created'))}")
                print(f"CSV: {result.get('csv_report_path', self.i18n.t('not_created'))}")
            else:
                print(f"\n{self.i18n.t('report_error')}")
                
        except Exception as e:
            logger.error(f"Rapor oluştururken hata: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            print(f"\n❌ Hata: {str(e)}") 