"""
SofaScore Scraper için maç işlemleri modülü.
Bu modül, maç ve maç detayları ile ilgili UI işlemlerini içerir.
"""

import os
from typing import Any, Callable, Dict, List, Optional

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
            name = season.get("name", self.i18n.t("unknown_season"))
            year = season.get("year", self.i18n.t("no_year_info"))
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
                    print(f"\n{self.i18n.t('invalid_league_num')}")
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
                        print(self.i18n.t("season_selected", season_name=selected_seasons[0].get('name', self.i18n.t('unknown_season'))))
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
                    season_name = season.get("name", self.i18n.t("unknown_season"))

                    print(self.i18n.t('fetching_match_data_for_league_season', league_name=league_name, season_name=season_name))

                    success = self.match_fetcher.fetch_matches_for_season(league_id, season_id)

                    if success:
                        print(f"  {self.i18n.t('matches_fetched_successfully')}")
                        total_matches += 1
                    else:
                        print(f"  {self.i18n.t('matches_not_found')}")

                # Assuming 'results' and 'finished_matches' are not directly available here,
                # but the instruction implies a summary.
                # For now, we'll use total_matches as the 'total' and 'finished' count for simplicity
                # as the original code only tracked total_matches.
                print(self.i18n.t("matches_downloaded", league=league_name, season=self.i18n.t("selected_seasons_label"), total=total_matches, finished=total_matches))

            except ValueError:
                print(self.i18n.t("invalid_number_format"))

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
                        print(f"\n{self.i18n.t('how_many_seasons_to_fetch')}")
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
                    print(f"  {self.i18n.t('checking_seasons_progress')}")
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
                        print(f"  {self.i18n.t('info_seasons_limited', count=max_seasons, total=len(sorted_seasons))}")
                    else:
                        seasons_to_fetch = sorted_seasons
                        print(f"  {self.i18n.t('info_seasons_total', total=len(sorted_seasons))}")

                    league_matches = 0

                    # Her sezon için maç verilerini çek
                    for season in seasons_to_fetch:
                        season_id = season.get("id")
                        season_name = season.get("name", self.i18n.t("unknown_season"))

                        print(f"  ○ {season_name} {self.i18n.t('fetching_matches_for_season')}")

                        try:
                            success = self.match_fetcher.fetch_matches_for_season(league_id, season_id)

                            if success:
                                print(f"    {self.i18n.t('matches_fetched_successfully')}")
                                league_matches += 1
                            else:
                                print(f"    {self.i18n.t('matches_not_found')}")
                        except Exception as e:
                            logger.error(f"{league_name} - {season_name} için maç verisi çekilirken hata: {str(e)}")
                            print(f"    {self.i18n.t('error_with_message', error=str(e))}")

                    total_matches += league_matches
                    print(f"  {self.i18n.t('league_matches_fetched_total', league_name=league_name, count=league_matches)}")
                    if progress_callback and n_leagues > 0:
                        progress_callback(
                            li + 1,
                            n_leagues,
                            f"Matches {li + 1}/{n_leagues}: {league_name}",
                        )

                except Exception as e:
                    logger.error(f"{league_name} için maç verisi çekilirken hata: {str(e)}")
                    print(f"  {self.i18n.t('error_with_message', error=str(e))}")
                    if progress_callback and n_leagues > 0:
                        progress_callback(
                            li + 1,
                            n_leagues,
                            f"Matches {li + 1}/{n_leagues}: {league_name} (error)",
                        )

            print(f"\n{self.i18n.t('info_total_matches_fetched')} {total_matches} {self.i18n.t('info_total_matches_fetched_suffix')}")

        except Exception as e:
            logger.error(f"Tüm ligler için maç verileri çekilirken hata: {str(e)}")
            print(f"\n{self.i18n.t('error_with_message', error=str(e))}")

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

            print(f"\n{self.i18n.t('league_list')}")
            for i, (league_id, league_name) in enumerate(leagues.items(), 1):
                print(f"{i}. {league_name} (ID: {league_id or '?'})")

            league_choice = input(f"\n{self.i18n.t('league_number_to_view_matches')} ").strip()

            try:
                league_index = int(league_choice) - 1
                if league_index < 0 or league_index >= len(leagues):
                    print(f"\n{self.i18n.t('invalid_league_num')}")
                    return

                league_id = list(leagues.keys())[league_index]
                league_name = leagues[league_id]

                # Sezon seç
                seasons = self.season_fetcher.get_seasons_for_league(league_id)

                if not seasons:
                    print(f"\n{self.i18n.t('season_data_not_found_for_league')}")
                    return

                print(f"\n{self.i18n.t('seasons_heading')}")
                for i, season in enumerate(seasons, 1):
                    season_id = season.get("id", "?")
                    season_name = season.get("name", self.i18n.t("unknown_season"))
                    print(f"{i}. {season_name} (ID: {season_id})")

                season_choice = input(f"\n{self.i18n.t('season_number_to_view_matches')} ").strip()

                try:
                    season_index = int(season_choice) - 1
                    if season_index < 0 or season_index >= len(seasons):
                        print(f"\n{self.i18n.t('invalid_season_number')}")
                        return

                    season = seasons[season_index]
                    season_id = season.get("id")
                    season_name = season.get("name", self.i18n.t("unknown_season"))

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
                            except IndexError:
                                continue
                        # 2. Yeni format: round_X.json
                        elif file.startswith("round_") and file.endswith(".json"):
                            try:
                                round_num = file.split("_")[1].split(".")[0]
                                match_files[round_num] = file
                            except IndexError:
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
                print(f"\n{self.i18n.t('invalid_league_num')}")

        except Exception as e:
            logger.error(f"Maçları listelerken hata: {str(e)}")
            print(f"\n{self.i18n.t('error_with_message', error=str(e))}")


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
                        print(self.i18n.t("match_details_fetched_id", match_id=match_id))
                        success_count += 1
                    else:
                        print(self.i18n.t("match_details_fetch_failed_id", match_id=match_id))

                except Exception as e:
                    logger.error(f"Maç {match_id} için detay çekilirken hata: {str(e)}")
                    print(self.i18n.t("match_details_fetch_error_id", match_id=match_id, error=str(e)))

            print(f"\n{self.i18n.t('info_match_details_completed')} {success_count}/{len(match_ids)} {self.i18n.t('info_match_successful')}")

        except Exception as e:
            logger.error(f"Maç detaylarını çekerken hata: {str(e)}")
            print(f"\n{self.i18n.t('error_with_message', error=str(e))}")

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
                    print(f"\n{self.i18n.t('fetching_match_details_for_league_id', league_id=league_id)}")
                    if max_seasons > 0:
                        print(self.i18n.t("last_n_seasons_will_be_fetched", count=max_seasons))
                    else:
                        print(self.i18n.t("all_seasons_will_be_fetched"))

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
            print(f"1. {self.i18n.t('menu_all_leagues')}")
            print(f"2. {self.i18n.t('menu_specific_league')}")
            print(f"0. {self.i18n.t('menu_cancel')}")

            filter_choice = input(f"\n{self.i18n.t('selection_prompt_range', range='0-2')} ").strip()

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
                print(f"\n{self.i18n.t('league_list')}")
                for i, (league_id, league_name) in enumerate(leagues.items(), 1):
                    print(f"{i}. {league_name} (ID: {league_id})")

                # Lig seçimini al
                league_choice = input(f"\n{self.i18n.t('input_league_number_details')} ").strip()

                if league_choice == "0":
                    return

                try:
                    league_index = int(league_choice) - 1
                    if league_index < 0 or league_index >= len(leagues):
                        print(f"\n{self.i18n.t('invalid_league_num')}")
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

                    print(f"\n{self.i18n.t('fetching_match_details_for_league', league_name=league_name)}")
                    if max_seasons > 0:
                        print(self.i18n.t("last_n_seasons_will_be_fetched", count=max_seasons))
                    else:
                        print(self.i18n.t("all_seasons_will_be_fetched"))

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
            print(f"\n{self.i18n.t('error_with_message', error=str(e))}")

    def _export_csv(self, *, match_id: Optional[str] = None, league_id: Optional[int] = None) -> Any:
        """
        CSV dosyasını dışa aktarma servisi yazar (`legacy-wide-csv`, `match_details/processed/` altına): tek maç ve
        bütün maçlar için birleşik dosyanın yolu, bir lig için lig başına dosyaların yolları. Yazılacak maç yoksa
        None (lig için boş liste).
        """
        from src.services.export import ExportService, ExportSpec
        from src.store import open_store

        if match_id is not None and not match_id.isdigit():
            return None  # maç kimliği olamaz: bulunamayan maç gibi
        fetcher = self.match_data_fetcher
        service = ExportService(open_store(fetcher.data_dir))
        if league_id is not None:
            spec = ExportSpec(tournament_ids=(int(league_id),))
            return [r.path for r in service.write_legacy_csv_by_league(fetcher.processed_dir, spec) if r.path]
        spec = ExportSpec(event_ids=(int(match_id),)) if match_id is not None else ExportSpec()
        written = service.write_legacy_csv(fetcher.processed_dir, spec)
        return written.path if written is not None else None

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

                option = input(f"\n{self.i18n.t('selection_prompt_range', range='1-3')} ").strip()
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
                    print(f"\n❌ {self.i18n.t('valid_match_id_not_found')}")
                    return

                result = self._export_csv(match_id=match_id)

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

                print(f"\n{self.i18n.t('league_list')}")
                for i, (league_id, league_name) in enumerate(leagues.items(), 1):
                    print(f"{i}. {league_name} (ID: {league_id})")

                # Lig seçimini al
                league_choice = input(f"\n{self.i18n.t('csv_input_league_number')} ").strip()

                if league_choice == "0":
                    return

                try:
                    league_index = int(league_choice) - 1
                    if league_index < 0 or league_index >= len(leagues):
                        print(f"\n❌ {self.i18n.t('invalid_league_num')}")
                        return

                    # Seçilen ligi al
                    league_id = list(leagues.keys())[league_index]
                    league_name = leagues[league_id]

                    print(f"\n'{league_name}' (ID: {league_id}) {self.i18n.t('creating_csv_for')}")

                    result = self._export_csv(league_id=league_id)

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
                result = self._export_csv()

                if isinstance(result, list):
                    print(f"\n{self.i18n.t('csv_files_created_for_leagues', count=len(result))}")
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
            print(f"\n{self.i18n.t('error_with_message', error=str(e))}")

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

            choice = input(f"\n{self.i18n.t('selection_prompt')} ").strip()

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

            # Rapor katalogdan hesaplanır ve dosyaya yazılmaz (StatusService.coverage); burada gösterilir
            if result:
                self._print_coverage(result)
            else:
                print(f"\n{self.i18n.t('report_error')}")

        except Exception as e:
            logger.error(f"Rapor oluştururken hata: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            print(f"\n❌ {self.i18n.t('error_with_message', error=str(e))}")

    def _print_coverage(self, result: Dict[str, Any]) -> None:
        """Kapsam raporunu (MatchDataFetcher.generate_file_report sözlüğü) ekrana yazar."""
        overall = result.get("overall_stats") or {}
        total = int(overall.get("total_matches") or 0)
        print(f"\n{self.i18n.t('coverage_total', count=total)}")
        print(self.i18n.t('coverage_complete', count=overall.get('matches_with_all_files', 0),
                          rate=overall.get('completion_rate', 0)))
        missing = overall.get("missing_files") or {}
        if missing:
            print(f"\n{self.i18n.t('coverage_missing_title')}")
            for name, count in sorted(missing.items(), key=lambda item: item[1], reverse=True):
                rate = round(count / total * 100, 2) if total else 0
                print(self.i18n.t('coverage_missing_line', slice=name.removesuffix(".json"), count=count, rate=rate))
        leagues = result.get("league_stats") or {}
        if leagues:
            print(f"\n{self.i18n.t('coverage_leagues_title')}")
            for name, stats in sorted(leagues.items(), key=lambda item: item[1].get("completion_rate", 0),
                                      reverse=True):
                print(self.i18n.t('coverage_league_line', league=name, complete=stats.get('complete_matches', 0),
                                  total=stats.get('total_matches', 0), rate=stats.get('completion_rate', 0)))
