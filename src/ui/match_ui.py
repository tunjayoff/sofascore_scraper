"""
SofaScore Scraper için maç işlemleri modülü.
Bu modül, maç ve maç detayları ile ilgili UI işlemlerini içerir.
"""

import os
from typing import Dict, Any, Optional, List, Union
from colorama import Fore, Style

from src.config_manager import ConfigManager
from src.season_fetcher import SeasonFetcher
from src.match_fetcher import MatchFetcher
from src.match_data_fetcher import MatchDataFetcher
from src.logger import get_logger

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
    
    def fetch_matches_for_league(self) -> None:
        """Belirli bir lig için maç verilerini çeker."""
        try:
            # Ligleri al
            leagues = self.config_manager.get_leagues()
            if not leagues:
                print(f"\nKayıtlı lig bulunamadı. Önce lig ekleyin.")
                return
            
            # Lig listesini görüntüle
            print("\nLig Listesi:")
            for i, (league_id, league_name) in enumerate(leagues.items(), 1):
                print(f"{i}. {league_name} (ID: {league_id})")
            
            # Lig seçimini al
            league_choice = input("\nMaç verilerini çekmek istediğiniz ligin numarasını girin (0: İptal): ").strip()
            
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
                
                # Sezonları al
                seasons = self.season_fetcher.fetch_seasons_for_league(league_id)
                
                if not seasons:
                    print(f"\nBu lig için kayıtlı sezon bulunamadı. Önce sezon verileri çekin.")
                    return
                
                print(f"\n{league_name} (ID: {league_id}) için sezonlar görüntüleniyor...")
                
                # Sezonları tarihe göre sırala (en yeni en üstte)
                sorted_seasons = sorted(seasons, key=lambda s: self.season_fetcher._get_sortable_year_value(s.get("year", "")), reverse=True)
                
                # Sezon filtreleme seçenekleri
                print(f"\nSezon Filtreleme Seçenekleri:")
                print("-" * 50)
                print("1. Tüm Sezonlar")
                print("2. Son N Sezon")
                print("3. Belirli Bir Sezon")
                print("0. İptal")
                
                filter_choice = input("\nSeçiminiz (0-3): ").strip()
                
                if filter_choice == "0":
                    return
                
                selected_seasons = []
                
                # Tüm sezonlar
                if filter_choice == "1":
                    selected_seasons = sorted_seasons
                    print(f"\nTüm sezonlar seçildi ({len(selected_seasons)} sezon)")
                
                # Son N sezon
                elif filter_choice == "2":
                    try:
                        n_seasons = input("\nSon kaç sezonu seçmek istiyorsunuz? ").strip()
                        n_seasons = int(n_seasons)
                        
                        if n_seasons <= 0 or n_seasons > len(sorted_seasons):
                            print(f"\nGeçersiz sayı! 1 ile {len(sorted_seasons)} arasında bir sayı giriniz.")
                            return
                        
                        selected_seasons = sorted_seasons[:n_seasons]
                        print(f"\nSon {n_seasons} sezon seçildi")
                    except ValueError:
                        print(f"\nGeçersiz sayı formatı!")
                        return
                
                # Belirli bir sezon
                elif filter_choice == "3":
                    # Sezon listesini göster
                    print("\nSezon Listesi:")
                    for i, season in enumerate(sorted_seasons, 1):
                        season_name = season.get("name", "Bilinmeyen Sezon")
                        season_year = season.get("year", "Yıl bilgisi yok")
                        print(f"{i}. {season_name} ({season_year})")
                    
                    # Sezon seçimini al
                    season_choice = input("\nMaç verilerini çekmek istediğiniz sezon numarasını girin (0: İptal): ").strip()
                    
                    if season_choice == "0":
                        return
                    
                    try:
                        season_index = int(season_choice) - 1
                        if season_index < 0 or season_index >= len(sorted_seasons):
                            print(f"\nGeçersiz sezon numarası!")
                            return
                        
                        selected_seasons = [sorted_seasons[season_index]]
                        print(f"\n{selected_seasons[0].get('name', 'Sezon')} seçildi")
                    except ValueError:
                        print(f"\nGeçersiz numara formatı!")
                        return
                
                else:
                    print(f"\nGeçersiz seçim!")
                    return
                
                # Seçilen sezonlar için maç verilerini çek
                total_matches = 0
                for season in selected_seasons:
                    season_id = season.get("id")
                    season_name = season.get("name", "Bilinmeyen Sezon")
                    
                    print(f"\n{league_name} - {season_name} için maç verileri çekiliyor...")
                    
                    success = self.match_fetcher.fetch_matches_for_season(league_id, season_id)
                    
                    if success:
                        print(f"  ✓ Maç verileri başarıyla çekildi.")
                        total_matches += 1
                    else:
                        print(f"  Maç verisi bulunamadı.")
                
                if total_matches > 0:
                    print(f"\nToplam {total_matches} maç verisi başarıyla çekildi.")
                else:
                    print(f"\nHiç maç verisi çekilemedi.")
                    
            except ValueError:
                print(f"\nGeçersiz numara formatı!")
                
        except Exception as e:
            logger.error(f"Maç verileri çekilirken hata: {str(e)}")
            print(f"\nHata: {str(e)}")
    
    def fetch_matches_for_all_leagues(self) -> None:
        """Tüm ligler için maç verilerini çeker."""
        try:
            # Ligleri al
            leagues = self.config_manager.get_leagues()
            
            if not leagues:
                print(f"\nKayıtlı lig bulunamadı. Önce lig ekleyin.")
                return
            
            # Kaç sezon çekileceğini kullanıcıya sor
            max_seasons = -1
            while max_seasons < 0:
                try:
                    print(f"\nKaç sezon çekmek istiyorsunuz?")
                    print(f"(Tüm sezonlar için 0 girin, son N sezon için rakam girin)")
                    max_seasons_input = input(f"Sezon sayısı: ")
                    max_seasons = int(max_seasons_input)
                    if max_seasons < 0:
                        print(f"Lütfen geçerli bir sayı girin (0 veya daha büyük)")
                except ValueError:
                    print(f"Lütfen geçerli bir sayı girin")
            
            print(f"\nTüm ligler için maç verileri çekiliyor...")
            if max_seasons > 0:
                print(f"Her lig için son {max_seasons} sezon çekilecek")
            else:
                print(f"Her lig için tüm sezonlar çekilecek")
            
            total_matches = 0
            for league_id, league_name in leagues.items():
                try:
                    print(f"\n  🏆 {league_name} (ID: {league_id})")
                    
                    # Ligi çekmeden önce kontrol et
                    print(f"  ○ Sezonlar kontrol ediliyor...")
                    seasons = self.season_fetcher.fetch_seasons_for_league(league_id)
                    
                    if not seasons:
                        print(f"  Maç bulunamadı. Atlanıyor.")
                        continue
                    
                    # Sezonları tarihe göre sırala (en yeni en üstte)
                    sorted_seasons = sorted(seasons, key=lambda s: self.season_fetcher._get_sortable_year_value(s.get("year", "")), reverse=True)
                    if not sorted_seasons:
                        print(f"  Sezon verisi sıralanamadı. Atlanıyor.")
                        continue
                    
                    # Sezon sayısını sınırla
                    if max_seasons > 0 and len(sorted_seasons) > max_seasons:
                        seasons_to_fetch = sorted_seasons[:max_seasons]
                        print(f"  ℹ️ Toplam {len(sorted_seasons)} sezon arasından son {max_seasons} sezon çekilecek")
                    else:
                        seasons_to_fetch = sorted_seasons
                        print(f"  ℹ️ Toplam {len(sorted_seasons)} sezon çekilecek")
                    
                    league_matches = 0
                    
                    # Her sezon için maç verilerini çek
                    for season in seasons_to_fetch:
                        season_id = season.get("id")
                        season_name = season.get("name", "Bilinmeyen Sezon")
                        
                        print(f"  ○ {season_name} sezonu için maçlar çekiliyor...")
                        
                        try:
                            success = self.match_fetcher.fetch_matches_for_season(league_id, season_id)
                            
                            if success:
                                print(f"    ✓ Maçlar başarıyla çekildi.")
                                league_matches += 1
                            else:
                                print(f"    Maç bulunamadı.")
                        except Exception as e:
                            logger.error(f"{league_name} - {season_name} için maç verisi çekilirken hata: {str(e)}")
                            print(f"    Hata: {str(e)}")
                    
                    total_matches += league_matches
                    print(f"  {league_name} için toplam {league_matches} maç verisi çekildi.")
                    
                except Exception as e:
                    logger.error(f"{league_name} için maç verisi çekilirken hata: {str(e)}")
                    print(f"  Hata: {str(e)}")
            
            print(f"\nTüm ligler için toplam {total_matches} maç verisi başarıyla çekildi.")
            
        except Exception as e:
            logger.error(f"Tüm ligler için maç verileri çekilirken hata: {str(e)}")
            print(f"\nHata: {str(e)}")
    
    def list_matches(self) -> None:
        """Çekilen maçları listeler."""
        try:
            # Maç veri dizinini kontrol et
            match_dir = self.config_manager.get_match_data_dir()
            
            if not os.path.exists(match_dir):
                print(f"\nÇekilen Maçlar:")
                print("-" * 50)
                print("Maç verisi bulunamadı.")
                return
                
            # Maç dosyalarını ara
            if not os.listdir(match_dir):
                print("Maç verisi bulunamadı.")
                return
                
            # Ligi seç
            leagues = self.config_manager.get_leagues()
            
            print(f"\nLigler:")
            for i, (league_id, league_name) in enumerate(leagues.items(), 1):
                print(f"{i}. {league_name} (ID: {league_id or '?'})")
            
            league_choice = input("\nMaçları görüntülemek istediğiniz lig numarası: ").strip()
            
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
                    print(f"\nBu lig için sezon verisi bulunamadı.")
                    return
                
                print(f"\nSezonlar:")
                for i, season in enumerate(seasons, 1):
                    season_id = season.get("id", "?")
                    season_name = season.get("name", "Bilinmeyen Sezon")
                    print(f"{i}. {season_name} (ID: {season_id})")
                
                season_choice = input("\nMaçları görüntülemek istediğiniz sezon numarası: ").strip()
                
                try:
                    season_index = int(season_choice) - 1
                    if season_index < 0 or season_index >= len(seasons):
                        print(f"\nGeçersiz sezon numarası!")
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
                        print(f"\nBu sezon için maç verisi bulunamadı.")
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
                        print(f"\nBu sezon için maç verisi bulunamadı.")
                        return
                    
                    print(f"\nMaç Dosyaları:")
                    for i, (round_num, match_file) in enumerate(sorted(match_files.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0), 1):
                        print(f"{i}. {round_num} {match_file}")
                
                except ValueError:
                    print(f"\nGeçersiz sezon numarası!")
                    
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
    
    def fetch_match_details(self) -> None:
        """Maç detaylarını çeker."""
        try:
            # Giriş yap
            match_ids_str = input("\nMaç ID (birden fazla için virgülle ayırın): ").strip()
            
            if not match_ids_str:
                print(f"\nGeçerli maç ID'si bulunamadı.")
                return
            
            match_ids = [id.strip() for id in match_ids_str.split(",") if id.strip()]
            
            print(f"\nMaç detayları çekiliyor...")
            
            success_count = 0
            for match_id in match_ids:
                try:
                    result = self.match_data_fetcher.fetch_match_details(match_id)
                    
                    if result:
                        print(f"✓ Maç ID {match_id}")
                        success_count += 1
                    else:
                        print(f"✗ Maç ID {match_id}: Detaylar çekilemedi.")
                        
                except Exception as e:
                    logger.error(f"Maç {match_id} için detay çekilirken hata: {str(e)}")
                    print(f"✗ Maç ID {match_id}: Hata: {str(e)}")
            
            print(f"\nMaç detayları çekme işlemi tamamlandı. {success_count}/{len(match_ids)} maç başarılı.")
            
        except Exception as e:
            logger.error(f"Maç detaylarını çekerken hata: {str(e)}")
            print(f"\nHata: {str(e)}")
    
    def fetch_all_match_details(self) -> None:
        """Tüm maçların detaylarını çeker."""
        try:
            print(f"\nTüm Maçlar İçin Detaylar Çekiliyor:")
            
            # Ligleri al
            leagues = self.config_manager.get_leagues()
            if not leagues:
                print(f"\nKayıtlı lig bulunamadı. Önce lig ekleyin.")
                return
            
            # Filtreleme seçenekleri
            print(f"\nFiltreleme Seçenekleri:")
            print("-" * 50)
            print("1. Tüm Ligler")
            print("2. Belirli Bir Lig")
            print("0. İptal")
            
            filter_choice = input("\nSeçiminiz (0-2): ").strip()
            
            if filter_choice == "0":
                return
                
            # Tüm ligler
            if filter_choice == "1":
                # Kaç sezon çekileceğini kullanıcıya sor
                print(f"\nKaç sezon için detayları çekmek istiyorsunuz?")
                print(f"(Tüm sezonlar için 0 girin, son N sezon için rakam girin)")
                max_seasons_input = input(f"Sezon sayısı: ")
                
                try:
                    max_seasons = int(max_seasons_input)
                    if max_seasons < 0:
                        print(f"Lütfen geçerli bir sayı girin (0 veya daha büyük)")
                        return
                except ValueError:
                    print(f"Lütfen geçerli bir sayı girin")
                    return
                
                print(f"\nTüm ligler için maç detayları çekiliyor...")
                if max_seasons > 0:
                    print(f"Her lig için son {max_seasons} sezon çekilecek")
                else:
                    print(f"Her lig için tüm sezonlar çekilecek")
                
                # Bu noktada fetch_all_match_details'ı çağır
                result = self.match_data_fetcher.fetch_all_match_details(max_seasons=max_seasons)
                
                if result:
                    print(f"\n✅ Tüm maçlar için detaylar başarıyla çekildi.")
                else:
                    print(f"\n❌ Maç detayları çekilirken bir hata oluştu.")
                
            # Belirli bir lig
            elif filter_choice == "2":
                # Lig listesini görüntüle
                print("\nLig Listesi:")
                for i, (league_id, league_name) in enumerate(leagues.items(), 1):
                    print(f"{i}. {league_name} (ID: {league_id})")
                
                # Lig seçimini al
                league_choice = input("\nDetaylarını çekmek istediğiniz ligin numarasını girin (0: İptal): ").strip()
                
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
                    print(f"\nKaç sezon için detayları çekmek istiyorsunuz?")
                    print(f"(Tüm sezonlar için 0 girin, son N sezon için rakam girin)")
                    max_seasons_input = input(f"Sezon sayısı: ")
                    
                    try:
                        max_seasons = int(max_seasons_input)
                        if max_seasons < 0:
                            print(f"Lütfen geçerli bir sayı girin (0 veya daha büyük)")
                            return
                    except ValueError:
                        print(f"Lütfen geçerli bir sayı girin")
                        return
                    
                    print(f"\n{league_name} için maç detayları çekiliyor...")
                    if max_seasons > 0:
                        print(f"Son {max_seasons} sezon çekilecek")
                    else:
                        print(f"Tüm sezonlar çekilecek")
                    
                    # Bu noktada fetch_all_match_details'ı çağır
                    result = self.match_data_fetcher.fetch_all_match_details(league_id=league_id, max_seasons=max_seasons)
                    
                    if result:
                        print(f"\n✅ {league_name} için maç detayları başarıyla çekildi.")
                    else:
                        print(f"\n❌ Maç detayları çekilirken bir hata oluştu.")
                        
                except ValueError:
                    print(f"\nGeçersiz numara formatı!")
                    return
            else:
                print(f"\nGeçersiz seçim!")
                return
                
        except Exception as e:
            logger.error(f"Tüm maç detaylarını çekerken hata: {str(e)}")
            print(f"\nHata: {str(e)}")
    
    def convert_to_csv(self) -> None:
        """Maç verilerini CSV formatına dönüştürür."""
        try:
            print(f"\nCSV Dönüştürme:")
            
            # Dönüştürme seçenekleri
            print("1. Tek Maç CSV")
            print("2. Belirli Bir Lig İçin CSV")
            print("3. Tüm Ligler İçin CSV")
            
            option = input("\nSeçiminiz (1-3): ").strip()
            
            # Tek maç CSV
            if option == "1":
                match_id = input("\nCSV'ye dönüştürülecek maç ID: ").strip()
                
                if not match_id:
                    print(f"\n❌ Geçerli maç ID'si bulunamadı")
                    return
                
                result = self.match_data_fetcher.convert_match_to_csv(match_id)
                
                if result:
                    csv_path = result
                    print(f"\n✅ CSV dosyası başarıyla oluşturuldu: {csv_path}")
                else:
                    print(f"\n❌ CSV dosyası oluşturulurken hata oluştu.")
            
            # Belirli bir lig için CSV
            elif option == "2":
                # Ligleri al ve göster
                leagues = self.config_manager.get_leagues()
                if not leagues:
                    print(f"\n❌ Kayıtlı lig bulunamadı. Önce lig ekleyin.")
                    return
                
                print("\nLig Listesi:")
                for i, (league_id, league_name) in enumerate(leagues.items(), 1):
                    print(f"{i}. {league_name} (ID: {league_id})")
                
                # Lig seçimini al
                league_choice = input("\nCSV'ye dönüştürülecek ligin numarasını girin (0: İptal): ").strip()
                
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
                    
                    print(f"\n'{league_name}' (ID: {league_id}) için CSV oluşturuluyor...")
                    
                    result = self.match_data_fetcher.convert_league_matches_to_csv(league_id)
                    
                    if result:
                        csv_paths = result
                        print(f"\n✅ CSV dosyaları başarıyla oluşturuldu:")
                        for csv_path in csv_paths:
                            print(f"  - {csv_path}")
                    else:
                        print(f"\n❌ CSV dosyaları oluşturulurken hata oluştu.")
                        
                except ValueError:
                    print(f"\n❌ Geçersiz numara formatı!")
                    return
            
            # Tüm ligler için CSV
            elif option == "3":
                result = self.match_data_fetcher.convert_all_matches_to_csv()
                
                if isinstance(result, list):
                    print(f"\n✅ {len(result)} lig için CSV dosyaları başarıyla oluşturuldu:")
                    for csv_path in result:
                        print(f"  - {csv_path}")
                elif result:
                    print(f"\n✅ CSV dosyası başarıyla oluşturuldu: {result}")
                else:
                    print(f"\n❌ CSV dosyası oluşturulurken hata oluştu.")
            else:
                print(f"\n❌ Geçersiz seçenek!")
                
        except Exception as e:
            logger.error(f"CSV dönüştürürken hata: {str(e)}")
            print(f"\nHata: {str(e)}")
    
    def show_menu(self) -> None:
        """Menüyü gösterir ve seçimleri işler."""
        while True:
            print("\n" + "=" * 50)
            print("MAÇ DETAYLARI YÖNETİMİ")
            print("=" * 50)
            
            print("\n1. Tek Maç Veri Çekme")
            print("2. ID Listesinden Maç Veri Çekme")
            print("3. Tüm Maç Verilerini CSV'ye Dönüştür")
            print("4. Maç Dosyaları Analiz Raporu Oluştur")
            print("0. Ana Menüye Dön")
            
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
                print("\n❌ Geçersiz seçim!")
    
    def generate_file_report(self) -> None:
        """Maç dosyalarının durumunu analiz eder ve rapor oluşturur."""
        try:
            print("\nMaç Dosyaları Analiz Raporu:")
            
            # Kullanıcıya özel dizin seçeneği sun
            custom_path = input("\nÖzel bir dizin yolu girmek ister misiniz? (varsayılan için boş bırakın): ").strip()
            
            # Rapor oluştur
            if custom_path:
                if not os.path.isdir(custom_path):
                    print(f"\n❌ Geçersiz dizin yolu: {custom_path}")
                    return
                    
                result = self.match_data_fetcher.generate_file_report(custom_path)
            else:
                result = self.match_data_fetcher.generate_file_report()
            
            # Sonuçları göster (fonksiyon zaten ekrana yazdırıyor)
            if result:
                # Opsiyonel olarak CSV ve JSON dosya yollarını göster
                print(f"\nRapor dosyaları:")
                print(f"JSON: {result.get('json_report_path', 'Oluşturulmadı')}")
                print(f"CSV: {result.get('csv_report_path', 'Oluşturulmadı')}")
            else:
                print(f"\n❌ Rapor oluşturulurken bir hata oluştu.")
                
        except Exception as e:
            logger.error(f"Rapor oluştururken hata: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            print(f"\n❌ Hata: {str(e)}") 