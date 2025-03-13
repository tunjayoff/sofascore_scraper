"""
SofaScore Scraper için istatistik işlemleri modülü.
Bu modül, istatistik görüntüleme ve raporlama işlemlerini içerir.
"""

import os
import json
import traceback
from typing import Dict, Any, Optional, List
from colorama import Fore, Style

from src.config_manager import ConfigManager
from src.logger import get_logger

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
    
    def show_system_stats(self) -> None:
        """Sistem durumunu ve istatistiklerini gösterir."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['TITLE']}{'Sistem İstatistikleri':^50}\n{'-'*50}")
            
            # Toplam lig sayısı
            leagues = self.config_manager.get_leagues()
            league_count = len(leagues) if leagues else 0
            print(f"Toplam Lig: {COLORS['SUCCESS']}{league_count}")
            
            # Toplam sezon sayısı
            season_count = 0
            seasons_dir = os.path.join(self.data_dir, "seasons")
            if os.path.exists(seasons_dir):
                # Sezonlar doğrudan seasons dizininde <lig_id>_<lig_adı>_seasons.json formatında
                season_files = [f for f in os.listdir(seasons_dir) if f.endswith("_seasons.json")]
                
                for season_file in season_files:
                    file_path = os.path.join(seasons_dir, season_file)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            seasons_data = json.load(f)
                            if isinstance(seasons_data, dict) and "seasons" in seasons_data:
                                # Verinin "seasons" anahtarı altında bir liste olduğunu kontrol et
                                season_count += len(seasons_data["seasons"])
                            elif isinstance(seasons_data, list):
                                # Geriye dönük uyumluluk için direkt liste kontrolü de yap
                                season_count += len(seasons_data)
                    except Exception as e:
                        logger.error(f"Sezon dosyası okuma hatası {file_path}: {str(e)}")
            print(f"Toplam Sezon: {COLORS['SUCCESS']}{season_count}")
            
            # Toplam maç sayısı
            match_count = 0
            matches_dir = os.path.join(self.data_dir, "matches")
            if os.path.exists(matches_dir):
                # Her lig için
                for league_dir in os.listdir(matches_dir):
                    league_path = os.path.join(matches_dir, league_dir)
                    if not os.path.isdir(league_path):
                        continue
                    
                    # Lig dizini içerisinde dolaş
                    for root, dirs, files in os.walk(league_path):
                        # CSV dosyalarını bul ve maç sayısını hesapla
                        for file in files:
                            if file.endswith("_matches.csv") or file.endswith("_summary.csv"):
                                file_path = os.path.join(root, file)
                                try:
                                    with open(file_path, 'r', encoding='utf-8') as f:
                                        lines = f.readlines()
                                        if len(lines) > 1:  # Başlık satırını çıkar
                                            match_count += len(lines) - 1
                                except Exception as e:
                                    logger.error(f"CSV dosyası okuma hatası {file_path}: {str(e)}")
            print(f"Toplam Maç: {COLORS['SUCCESS']}{match_count}")
            
            # Toplam maç detayı sayısı
            match_details_count = 0
            match_details_dir = os.path.join(self.data_dir, "match_details")
            if os.path.exists(match_details_dir):
                # Her bir lig dizinini dolaş
                for league_dir in os.listdir(match_details_dir):
                    league_path = os.path.join(match_details_dir, league_dir)
                    if os.path.isdir(league_path):
                        # Her bir dizin (lig) içindeki JSON dosyalarını say
                        for root, dirs, files in os.walk(league_path):
                            match_details_count += len([f for f in files if f.endswith('.json')])
            
            # Disk kullanımı
            print(f"\n{COLORS['SUBTITLE']}Disk Kullanımı:")
            
            seasons_size = self._get_directory_size(seasons_dir) if os.path.exists(seasons_dir) else 0
            matches_size = self._get_directory_size(matches_dir) if os.path.exists(matches_dir) else 0
            match_details_size = self._get_directory_size(match_details_dir) if os.path.exists(match_details_dir) else 0
            
            datasets_dir = os.path.join(self.data_dir, "datasets")
            datasets_size = self._get_directory_size(datasets_dir) if os.path.exists(datasets_dir) else 0
            
            print(f"  Sezonlar: {COLORS['SUCCESS']}{self._format_size(seasons_size)}")
            print(f"  Maçlar: {COLORS['SUCCESS']}{self._format_size(matches_size)}")
            print(f"  Maç Detayları: {COLORS['SUCCESS']}{self._format_size(match_details_size)}")
            print(f"  Veri Setleri: {COLORS['SUCCESS']}{self._format_size(datasets_size)}")
            
            total_size = seasons_size + matches_size + match_details_size + datasets_size
            print(f"  Toplam: {COLORS['SUCCESS']}{self._format_size(total_size)}")
            
        except Exception as e:
            logger.error(f"Sistem istatistikleri gösterilirken hata: {str(e)}")
            logger.error(traceback.format_exc())
            print(f"\n{COLORS['ERROR']}Hata: {str(e)}")
    
    def show_league_stats(self, league_id):
        """
        Belirli bir lig için ayrıntılı istatistikleri gösterir
        """
        COLORS = self.colors  # Kısa erişim için
        
        try:
            # Lig adını bul
            leagues = self.config_manager.get_leagues()
            league_name = leagues.get(league_id, f"Lig {league_id}")
            
            print(f"\n{COLORS['INFO']}● {league_name} {COLORS['DIM']}(ID: {league_id})")
            
            # Sezon sayısı
            season_count = 0
            # Kesin lig ID'si ile dosya eşleştirmesi yap
            season_file_pattern = f"{league_id}_"
            season_file = None
            season_size = 0
            
            # Tüm sezon dosyalarını tara ve ID ile başlayanı bul
            seasons_dir = os.path.join(self.data_dir, "seasons")
            if os.path.exists(seasons_dir):
                for file_name in os.listdir(seasons_dir):
                    if file_name.startswith(season_file_pattern) and file_name.endswith("_seasons.json"):
                        season_file = os.path.join(seasons_dir, file_name)
                        break
            
            if season_file and os.path.exists(season_file):
                # Dosya boyutunu hesapla
                season_size = os.path.getsize(season_file)
            
            # Maç sayısı
            match_count = 0
            matches_dir = None
            matches_size = 0
            
            # Kesin lig ID'si ile dizin eşleştirmesi yap
            matches_dir_pattern = f"{league_id}_"
            
            # Tüm matches alt dizinlerini tara ve ID ile başlayanı bul
            matches_dir_base = os.path.join(self.data_dir, "matches")
            if os.path.exists(matches_dir_base):
                for dir_name in os.listdir(matches_dir_base):
                    if dir_name.startswith(matches_dir_pattern):
                        matches_dir = os.path.join(matches_dir_base, dir_name)
                        break
            
            if matches_dir and os.path.exists(matches_dir):
                # Maç verilerini oku
                matches_size = self._get_directory_size(matches_dir)
                
                # Bu lig için çekilmiş sezon sayısını hesapla - matches dizini altında
                # her bir alt dizin bir sezonu temsil eder
                try:
                    season_count = len([d for d in os.listdir(matches_dir) if os.path.isdir(os.path.join(matches_dir, d))])
                except Exception as e:
                    logger.error(f"Sezon dizinleri sayılırken hata: {e}")
                    season_count = 0
                
                # CSV dosyalarını bul ve maç sayısını hesapla
                try:
                    for root, dirs, files in os.walk(matches_dir):
                        for file in files:
                            if file.endswith("_matches.csv") or file.endswith("_summary.csv"):
                                file_path = os.path.join(root, file)
                                try:
                                    with open(file_path, 'r', encoding='utf-8') as f:
                                        lines = f.readlines()
                                        if len(lines) > 1:  # Başlık satırını çıkar
                                            match_count += len(lines) - 1
                                except Exception as e:
                                    logger.error(f"Maç dosyası okunurken hata: {e}")
                except Exception as e:
                    logger.error(f"Maç dosyaları taranırken hata: {e}")
            
            print(f"  {COLORS['INFO']}○ Sezon Sayısı: {COLORS['SUCCESS']}{season_count}")
            print(f"  {COLORS['INFO']}○ Maç Sayısı: {COLORS['SUCCESS']}{match_count}")
            
            # Maç detayları sayısı
            match_details_count = 0
            match_details_dir = None
            match_details_size = 0
            
            # Maç detayları dizinini ara - daha akıllı bir algoritma
            match_details_base = os.path.join(self.data_dir, "match_details")
            if os.path.exists(match_details_base):
                all_match_detail_dirs = os.listdir(match_details_base)
                
                # En iyi eşleşmeyi bulmak için puanlama sistemi
                best_dir = None
                best_score = -1
                
                for dir_name in all_match_detail_dirs:
                    score = 0
                    dir_lower = dir_name.lower()
                    
                    # 1. Tam eşleşme - en yüksek puan (100)
                    if dir_name == league_name or dir_name == league_name.replace(" ", "_"):
                        score = 100
                    
                    # 2. ID ile başlayan - yüksek puan (90)
                    elif dir_name.startswith(f"{league_id}_"):
                        score = 90
                    
                    # 3. Lig adının ilk kelimesi dizin adında geçiyor - orta puan (50-80)
                    elif league_name and dir_lower.startswith(league_name.lower().split()[0]):
                        score = 80
                    elif league_name and league_name.lower().split()[0] in dir_lower:
                        score = 70
                    
                    # 4. Lig ID'si dizin adında geçiyor - düşük-orta puan (60)
                    elif str(league_id) in dir_name:
                        score = 60
                    
                    # 5. Türkiye ligi için özel kontrol (ID: 52)
                    elif str(league_id) == "52":
                        if any(keyword in dir_lower for keyword in ["türk", "turk", "türkiye", "turkey", "turkiye"]):
                            score = 85  # Yüksek puan - türkiye ligi için spesifik kontrol
                        elif any(keyword in dir_lower for keyword in ["süper", "super"]):
                            score = 75  # Orta-yüksek puan
                    
                    # Daha yüksek puan bulduysak güncelle
                    if score > best_score:
                        best_score = score
                        best_dir = dir_name
                
                # Eğer bir eşleşme bulunduysa
                if best_dir and best_score > 0:
                    match_details_dir = os.path.join(match_details_base, best_dir)
            
            if match_details_dir and os.path.exists(match_details_dir):
                # Maç detaylarını oku
                match_details_size = self._get_directory_size(match_details_dir)
                
                # JSON dosyalarını say - alt dizinlerde sezonlar ve maç detayları var
                try:
                    for root, dirs, files in os.walk(match_details_dir):
                        match_details_count += len([f for f in files if f.endswith('.json')])
                except Exception as e:
                    logger.error(f"Maç detay dosyaları taranırken hata: {e}")
            
            print(f"  {COLORS['INFO']}○ Maç Detayı Sayısı: {COLORS['SUCCESS']}{match_details_count}")
            
            # Disk kullanımı
            print(f"  {COLORS['INFO']}○ Sezon Verileri: {COLORS['SUCCESS']}{self._format_size(season_size)}")
            print(f"  {COLORS['INFO']}○ Maç Verileri: {COLORS['SUCCESS']}{self._format_size(matches_size)}")
            print(f"  {COLORS['INFO']}○ Maç Detayları: {COLORS['SUCCESS']}{self._format_size(match_details_size)}")
            print(f"  {COLORS['INFO']}○ Toplam: {COLORS['SUCCESS']}{self._format_size(season_size + matches_size + match_details_size)}")
            
        except Exception as e:
            # Eğer Türkiye ligi (ID: 52) için bir hata oluştuysa, match_details dizinindeki tüm klasörleri logla
            if str(league_id) == "52":
                match_details_base = os.path.join(self.data_dir, "match_details")
                if os.path.exists(match_details_base):
                    all_dirs = os.listdir(match_details_base)
                    logger.error(f"Türkiye ligi için match_details dizinindeki tüm klasörler: {all_dirs}")
            
            logger.error(f"Lig istatistikleri görüntülenirken hata: {str(e)}")
            logger.error(traceback.format_exc())
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def generate_report(self) -> None:
        """İstatistik raporu oluşturur."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}İstatistik Raporu Oluşturma:")
            print("-" * 50)
            print("1. 📊 Sistem Raporu")
            print("2. 📈 Lig Bazlı Rapor")
            print("3. 📉 Detaylı Rapor (Tüm İstatistikler)")
            
            choice = input("\nSeçenek (1-3): ")
            
            if choice == "1":
                self._generate_system_report()
            elif choice == "2":
                self._generate_league_report()
            elif choice == "3":
                self._generate_detailed_report()
            else:
                print(f"\n{COLORS['WARNING']}❌ Geçersiz seçenek!")
                
        except Exception as e:
            logger.error(f"Rapor oluşturulurken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _generate_system_report(self) -> None:
        """Sistem raporu oluşturur."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            # Rapor verilerini topla
            report_data = {
                "timestamp": self._get_timestamp(),
                "leagues": {},
                "stats": {
                    "league_count": 0,
                    "season_count": 0,
                    "match_count": 0,
                    "match_details_count": 0,
                    "disk_usage": {
                        "seasons": 0,
                        "matches": 0,
                        "match_details": 0,
                        "datasets": 0,
                        "total": 0
                    }
                }
            }
            
            # Lig sayısı
            leagues = self.config_manager.get_leagues()
            report_data["stats"]["league_count"] = len(leagues) if leagues else 0
            
            # Sezon sayısı
            seasons_dir = os.path.join(self.data_dir, "seasons")
            if os.path.exists(seasons_dir):
                # Sezonlar doğrudan seasons dizininde <lig_id>_<lig_adı>_seasons.json formatında
                season_files = [f for f in os.listdir(seasons_dir) if f.endswith("_seasons.json")]
                
                for season_file in season_files:
                    file_path = os.path.join(seasons_dir, season_file)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            seasons_data = json.load(f)
                            if isinstance(seasons_data, dict) and "seasons" in seasons_data:
                                # Verinin "seasons" anahtarı altında bir liste olduğunu kontrol et
                                report_data["stats"]["season_count"] += len(seasons_data["seasons"])
                            elif isinstance(seasons_data, list):
                                # Geriye dönük uyumluluk için direkt liste kontrolü de yap
                                report_data["stats"]["season_count"] += len(seasons_data)
                    except Exception as e:
                        logger.error(f"Sezon dosyası okuma hatası {file_path}: {str(e)}")
            
            # Maç sayısı
            matches_dir = os.path.join(self.data_dir, "matches")
            if os.path.exists(matches_dir):
                # Her lig için
                for league_dir in os.listdir(matches_dir):
                    league_path = os.path.join(matches_dir, league_dir)
                    if not os.path.isdir(league_path):
                        continue
                    
                    # Lig dizini içerisinde dolaş
                    for root, dirs, files in os.walk(league_path):
                        # CSV dosyalarını bul ve maç sayısını hesapla
                        for file in files:
                            if file.endswith("_matches.csv") or file.endswith("_summary.csv"):
                                file_path = os.path.join(root, file)
                                try:
                                    with open(file_path, 'r', encoding='utf-8') as f:
                                        lines = f.readlines()
                                        if len(lines) > 1:  # Başlık satırını çıkar
                                            report_data["stats"]["match_count"] += len(lines) - 1
                                except Exception as e:
                                    logger.error(f"CSV dosyası okuma hatası {file_path}: {str(e)}")
            
            # Maç detayı sayısı
            match_details_count = 0
            match_details_dir = os.path.join(self.data_dir, "match_details")
            if os.path.exists(match_details_dir):
                # Her bir lig dizinini dolaş
                for league_dir in os.listdir(match_details_dir):
                    league_path = os.path.join(match_details_dir, league_dir)
                    if os.path.isdir(league_path):
                        # Her bir dizin (lig) içindeki JSON dosyalarını say
                        for root, dirs, files in os.walk(league_path):
                            match_details_count += len([f for f in files if f.endswith('.json')])
            
            # Hesaplanan değerleri report_data içerisine atayalım
            report_data["stats"]["match_details_count"] = match_details_count
            
            # Disk kullanımı
            report_data["stats"]["disk_usage"]["seasons"] = self._get_directory_size(seasons_dir) if os.path.exists(seasons_dir) else 0
            report_data["stats"]["disk_usage"]["matches"] = self._get_directory_size(matches_dir) if os.path.exists(matches_dir) else 0
            report_data["stats"]["disk_usage"]["match_details"] = self._get_directory_size(match_details_dir) if os.path.exists(match_details_dir) else 0
            
            datasets_dir = os.path.join(self.data_dir, "datasets")
            report_data["stats"]["disk_usage"]["datasets"] = self._get_directory_size(datasets_dir) if os.path.exists(datasets_dir) else 0
            
            report_data["stats"]["disk_usage"]["total"] = (
                report_data["stats"]["disk_usage"]["seasons"] +
                report_data["stats"]["disk_usage"]["matches"] +
                report_data["stats"]["disk_usage"]["match_details"] +
                report_data["stats"]["disk_usage"]["datasets"]
            )
            
            # Raporu kaydet
            reports_dir = os.path.join(self.data_dir, "reports")
            os.makedirs(reports_dir, exist_ok=True)
            
            report_file = os.path.join(reports_dir, f"system_report_{self._get_timestamp_filename()}.json")
            
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(report_data, f, indent=2)
            
            print(f"\n{COLORS['SUCCESS']}✅ Sistem raporu başarıyla oluşturuldu: {report_file}")
            
        except Exception as e:
            logger.error(f"Sistem raporu oluşturulurken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _generate_league_report(self) -> None:
        """Lig bazlı rapor oluşturur."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            leagues = self.config_manager.get_leagues()
            
            if not leagues:
                print(f"\n{COLORS['WARNING']}Yapılandırılmış lig bulunamadı.")
                return
            
            # Rapor verilerini topla
            report_data = {
                "timestamp": self._get_timestamp(),
                "leagues": {}
            }
            
            for league_id, league_name in leagues.items():
                league_data = {
                    "id": league_id,
                    "name": league_name,
                    "stats": {
                        "season_count": 0,
                        "match_count": 0,
                        "disk_usage": {
                            "seasons": 0,
                            "matches": 0,
                            "total": 0
                        }
                    }
                }
                
                # Sezon sayısı
                season_count = 0
                # Kesin lig ID'si ile dosya eşleştirmesi yap
                season_file_pattern = f"{league_id}_"
                season_file = None
                season_size = 0
                
                # Tüm sezon dosyalarını tara ve ID ile başlayanı bul
                for file_name in os.listdir(os.path.join(self.data_dir, "seasons")):
                    if file_name.startswith(season_file_pattern) and file_name.endswith("_seasons.json"):
                        season_file = os.path.join(self.data_dir, "seasons", file_name)
                        break
                
                if season_file and os.path.exists(season_file):
                    # Dosya boyutunu hesapla
                    season_size = os.path.getsize(season_file)
                
                # Maç sayısı
                match_count = 0
                matches_dir = None
                matches_size = 0
                
                # Kesin lig ID'si ile dizin eşleştirmesi yap
                matches_dir_pattern = f"{league_id}_"
                
                # Tüm matches alt dizinlerini tara ve ID ile başlayanı bul
                for dir_name in os.listdir(os.path.join(self.data_dir, "matches")):
                    if dir_name.startswith(matches_dir_pattern):
                        matches_dir = os.path.join(self.data_dir, "matches", dir_name)
                        break
                
                if matches_dir and os.path.exists(matches_dir):
                    # Maç verilerini oku
                    matches_size = self._get_directory_size(matches_dir)
                    
                    # Bu lig için çekilmiş sezon sayısını hesapla
                    season_count = len([d for d in os.listdir(matches_dir) if os.path.isdir(os.path.join(matches_dir, d))])
                    
                    # Toplanan sezon sayısını kaydet
                    league_data["stats"]["season_count"] = season_count
                    
                    # CSV dosyalarını bul ve maç sayısını hesapla
                    match_count = 0
                    for root, dirs, files in os.walk(matches_dir):
                        for file in files:
                            if file.endswith("_matches.csv") or file.endswith("_summary.csv"):
                                file_path = os.path.join(root, file)
                                try:
                                    with open(file_path, 'r', encoding='utf-8') as f:
                                        lines = f.readlines()
                                        if len(lines) > 1:  # Başlık satırını çıkar
                                            match_count += len(lines) - 1
                                except Exception as e:
                                    logger.error(f"Maç dosyası okunurken hata: {e}")
                    
                    # Toplanan maç sayısını kaydet
                    league_data["stats"]["match_count"] = match_count
                
                # Disk kullanımı
                league_data["stats"]["disk_usage"]["seasons"] = season_size
                league_data["stats"]["disk_usage"]["matches"] = matches_size
                league_data["stats"]["disk_usage"]["total"] = league_data["stats"]["disk_usage"]["seasons"] + league_data["stats"]["disk_usage"]["matches"]
                
                report_data["leagues"][str(league_id)] = league_data
            
            # Raporu kaydet
            reports_dir = os.path.join(self.data_dir, "reports")
            os.makedirs(reports_dir, exist_ok=True)
            
            report_file = os.path.join(reports_dir, f"league_report_{self._get_timestamp_filename()}.json")
            
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(report_data, f, indent=2)
            
            print(f"\n{COLORS['SUCCESS']}✅ Lig raporu başarıyla oluşturuldu: {report_file}")
            
        except Exception as e:
            logger.error(f"Lig raporu oluşturulurken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _generate_detailed_report(self) -> None:
        """Detaylı rapor oluşturur."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            # Sistem raporu ve lig raporunu birleştir
            system_report = {}
            league_report = {"leagues": {}}
            
            # Sistem istatistikleri
            leagues = self.config_manager.get_leagues()
            league_count = len(leagues) if leagues else 0
            
            season_count = 0
            seasons_dir = os.path.join(self.data_dir, "seasons")
            if os.path.exists(seasons_dir):
                # Sezonlar doğrudan seasons dizininde <lig_id>_<lig_adı>_seasons.json formatında
                season_files = [f for f in os.listdir(seasons_dir) if f.endswith("_seasons.json")]
                
                for season_file in season_files:
                    file_path = os.path.join(seasons_dir, season_file)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            seasons_data = json.load(f)
                            if isinstance(seasons_data, dict) and "seasons" in seasons_data:
                                # Verinin "seasons" anahtarı altında bir liste olduğunu kontrol et
                                season_count += len(seasons_data["seasons"])
                            elif isinstance(seasons_data, list):
                                # Geriye dönük uyumluluk için direkt liste kontrolü de yap
                                season_count += len(seasons_data)
                    except Exception as e:
                        logger.error(f"Sezon dosyası okuma hatası {file_path}: {str(e)}")
            
            match_count = 0
            matches_dir = os.path.join(self.data_dir, "matches")
            if os.path.exists(matches_dir):
                # Her lig için
                for league_dir in os.listdir(matches_dir):
                    league_path = os.path.join(matches_dir, league_dir)
                    if not os.path.isdir(league_path):
                        continue
                    
                    # Lig dizini içerisinde dolaş
                    for root, dirs, files in os.walk(league_path):
                        # CSV dosyalarını bul ve maç sayısını hesapla
                        for file in files:
                            if file.endswith("_matches.csv") or file.endswith("_summary.csv"):
                                file_path = os.path.join(root, file)
                                try:
                                    with open(file_path, 'r', encoding='utf-8') as f:
                                        lines = f.readlines()
                                        if len(lines) > 1:  # Başlık satırını çıkar
                                            match_count += len(lines) - 1
                                except Exception as e:
                                    logger.error(f"CSV dosyası okuma hatası {file_path}: {str(e)}")
            
            match_details_count = 0
            match_details_dir = os.path.join(self.data_dir, "match_details")
            if os.path.exists(match_details_dir):
                # Her bir lig dizinini dolaş
                for league_dir in os.listdir(match_details_dir):
                    league_path = os.path.join(match_details_dir, league_dir)
                    if os.path.isdir(league_path):
                        # Her bir dizin (lig) içindeki JSON dosyalarını say
                        for root, dirs, files in os.walk(league_path):
                            match_details_count += len([f for f in files if f.endswith('.json')])
            
            # Disk kullanımı
            seasons_size = self._get_directory_size(seasons_dir) if os.path.exists(seasons_dir) else 0
            matches_size = self._get_directory_size(matches_dir) if os.path.exists(matches_dir) else 0
            match_details_size = self._get_directory_size(match_details_dir) if os.path.exists(match_details_dir) else 0
            
            datasets_dir = os.path.join(self.data_dir, "datasets")
            datasets_size = self._get_directory_size(datasets_dir) if os.path.exists(datasets_dir) else 0
            
            total_size = seasons_size + matches_size + match_details_size + datasets_size
            
            system_report = {
                "timestamp": self._get_timestamp(),
                "stats": {
                    "league_count": league_count,
                    "season_count": season_count,
                    "match_count": match_count,
                    "match_details_count": match_details_count,
                    "disk_usage": {
                        "seasons": seasons_size,
                        "matches": matches_size,
                        "match_details": match_details_size,
                        "datasets": datasets_size,
                        "total": total_size
                    }
                }
            }
            
            # Lig istatistikleri
            if leagues:
                for league_id, league_name in leagues.items():
                    league_data = {
                        "id": league_id,
                        "name": league_name,
                        "stats": {
                            "season_count": 0,
                            "match_count": 0,
                            "disk_usage": {
                                "seasons": 0,
                                "matches": 0,
                                "total": 0
                            }
                        }
                    }
                    
                    # Sezon sayısı
                    season_count = 0
                    # Kesin lig ID'si ile dosya eşleştirmesi yap
                    season_file_pattern = f"{league_id}_"
                    season_file = None
                    
                    # Tüm sezon dosyalarını tara ve ID ile başlayanı bul
                    for file_name in os.listdir(os.path.join(self.data_dir, "seasons")):
                        if file_name.startswith(season_file_pattern) and file_name.endswith("_seasons.json"):
                            season_file = os.path.join(self.data_dir, "seasons", file_name)
                            break
                    
                    if season_file and os.path.exists(season_file):
                        # Dosya boyutunu hesapla
                        season_size = os.path.getsize(season_file)
                    
                    # Maç sayısı
                    matches_dir = None
                    matches_size = 0
                    
                    # Kesin lig ID'si ile dizin eşleştirmesi yap
                    matches_dir_pattern = f"{league_id}_"
                    
                    # Tüm matches alt dizinlerini tara ve ID ile başlayanı bul
                    for dir_name in os.listdir(os.path.join(self.data_dir, "matches")):
                        if dir_name.startswith(matches_dir_pattern):
                            matches_dir = os.path.join(self.data_dir, "matches", dir_name)
                            break
                    
                    if matches_dir and os.path.exists(matches_dir):
                        # Maç verilerini oku
                        matches_size = self._get_directory_size(matches_dir)
                        
                        # Bu lig için çekilmiş sezon sayısını hesapla
                        season_count = len([d for d in os.listdir(matches_dir) if os.path.isdir(os.path.join(matches_dir, d))])
                        
                        # Toplanan sezon sayısını kaydet
                        league_data["stats"]["season_count"] = season_count
                        
                        # CSV dosyalarını bul ve maç sayısını hesapla
                        match_count = 0
                        for root, dirs, files in os.walk(matches_dir):
                            for file in files:
                                if file.endswith("_matches.csv") or file.endswith("_summary.csv"):
                                    file_path = os.path.join(root, file)
                                    try:
                                        with open(file_path, 'r', encoding='utf-8') as f:
                                            lines = f.readlines()
                                            if len(lines) > 1:  # Başlık satırını çıkar
                                                match_count += len(lines) - 1
                                    except Exception as e:
                                        logger.error(f"Maç dosyası okunurken hata: {e}")
                    
                    # Toplanan maç sayısını kaydet
                    league_data["stats"]["match_count"] = match_count
                    
                    # Disk kullanımı
                    league_data["stats"]["disk_usage"]["seasons"] = season_size
                    league_data["stats"]["disk_usage"]["matches"] = matches_size
                    league_data["stats"]["disk_usage"]["total"] = league_data["stats"]["disk_usage"]["seasons"] + league_data["stats"]["disk_usage"]["matches"]
                    
                    league_report["leagues"][str(league_id)] = league_data
            
            # Raporları birleştir
            detailed_report = {
                "timestamp": self._get_timestamp(),
                "system": system_report["stats"],
                "leagues": league_report["leagues"]
            }
            
            # Raporu kaydet
            reports_dir = os.path.join(self.data_dir, "reports")
            os.makedirs(reports_dir, exist_ok=True)
            
            report_file = os.path.join(reports_dir, f"detailed_report_{self._get_timestamp_filename()}.json")
            
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(detailed_report, f, indent=2)
            
            print(f"\n{COLORS['SUCCESS']}✅ Detaylı rapor başarıyla oluşturuldu: {report_file}")
            
        except Exception as e:
            logger.error(f"Detaylı rapor oluşturulurken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _get_directory_size(self, path: str) -> int:
        """Dizin boyutunu bayt cinsinden hesaplar."""
        total_size = 0
        if os.path.exists(path):
            for dirpath, dirnames, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if os.path.exists(fp):
                        total_size += os.path.getsize(fp)
        return total_size
    
    def _format_size(self, size_bytes: int) -> str:
        """Bayt cinsinden boyutu okunabilir formata dönüştürür."""
        if size_bytes == 0:
            return "0 B"
        
        size_names = ("B", "KB", "MB", "GB", "TB")
        i = 0
        while size_bytes >= 1024 and i < len(size_names) - 1:
            size_bytes /= 1024
            i += 1
        
        return f"{size_bytes:.2f} {size_names[i]}"
    
    def _get_timestamp(self) -> str:
        """Şu anki zamanı ISO formatında döndürür."""
        from datetime import datetime
        return datetime.now().isoformat()
    
    def _get_timestamp_filename(self) -> str:
        """Dosya adı için uygun zaman damgası oluşturur."""
        from datetime import datetime
        return datetime.now().strftime("%Y%m%d_%H%M%S") 