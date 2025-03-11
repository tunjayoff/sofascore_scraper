"""
SofaScore Scraper için istatistik işlemleri modülü.
Bu modül, istatistik görüntüleme ve raporlama işlemlerini içerir.
"""

import os
import json
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
        """Sistem istatistiklerini görüntüler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Sistem İstatistikleri:")
            print("-" * 50)
            
            # Lig sayısı
            leagues = self.config_manager.get_leagues()
            league_count = len(leagues) if leagues else 0
            
            # Sezon sayısı
            season_count = 0
            seasons_dir = os.path.join(self.data_dir, "seasons")
            if os.path.exists(seasons_dir):
                for league_dir in os.listdir(seasons_dir):
                    league_path = os.path.join(seasons_dir, league_dir)
                    if os.path.isdir(league_path):
                        season_files = [f for f in os.listdir(league_path) if f.endswith(".json")]
                        season_count += len(season_files)
            
            # Maç sayısı
            match_count = 0
            matches_dir = os.path.join(self.data_dir, "matches")
            if os.path.exists(matches_dir):
                for league_dir in os.listdir(matches_dir):
                    league_path = os.path.join(matches_dir, league_dir)
                    if os.path.isdir(league_path):
                        for season_dir in os.listdir(league_path):
                            season_path = os.path.join(league_path, season_dir)
                            if os.path.isdir(season_path):
                                match_files = [f for f in os.listdir(season_path) if f.endswith("_matches.csv")]
                                for match_file in match_files:
                                    file_path = os.path.join(season_path, match_file)
                                    with open(file_path, 'r', encoding='utf-8') as f:
                                        # İlk satır başlık, geri kalanı veri
                                        lines = f.readlines()
                                        if len(lines) > 1:  # Başlık satırını çıkar
                                            match_count += len(lines) - 1
            
            # Maç detayı sayısı
            match_data_count = 0
            match_data_dir = os.path.join(self.data_dir, "match_data")
            if os.path.exists(match_data_dir):
                match_data_files = [f for f in os.listdir(match_data_dir) if f.endswith(".json")]
                match_data_count = len(match_data_files)
            
            # İstatistikleri görüntüle
            print(f"{COLORS['INFO']}Yapılandırılmış Lig Sayısı: {COLORS['SUCCESS']}{league_count}")
            print(f"{COLORS['INFO']}Yüklenen Sezon Sayısı: {COLORS['SUCCESS']}{season_count}")
            print(f"{COLORS['INFO']}Çekilen Maç Sayısı: {COLORS['SUCCESS']}{match_count}")
            print(f"{COLORS['INFO']}Çekilen Maç Detayı Sayısı: {COLORS['SUCCESS']}{match_data_count}")
            
            # Disk kullanımı
            total_size = 0
            
            # Sezon verileri boyutu
            seasons_size = self._get_directory_size(seasons_dir) if os.path.exists(seasons_dir) else 0
            total_size += seasons_size
            
            # Maç verileri boyutu
            matches_size = self._get_directory_size(matches_dir) if os.path.exists(matches_dir) else 0
            total_size += matches_size
            
            # Maç detayları boyutu
            match_data_size = self._get_directory_size(match_data_dir) if os.path.exists(match_data_dir) else 0
            total_size += match_data_size
            
            # CSV veri setleri boyutu
            datasets_dir = os.path.join(self.data_dir, "datasets")
            datasets_size = self._get_directory_size(datasets_dir) if os.path.exists(datasets_dir) else 0
            total_size += datasets_size
            
            print(f"\n{COLORS['SUBTITLE']}Disk Kullanımı:")
            print(f"{COLORS['INFO']}Sezon Verileri: {COLORS['SUCCESS']}{self._format_size(seasons_size)}")
            print(f"{COLORS['INFO']}Maç Verileri: {COLORS['SUCCESS']}{self._format_size(matches_size)}")
            print(f"{COLORS['INFO']}Maç Detayları: {COLORS['SUCCESS']}{self._format_size(match_data_size)}")
            print(f"{COLORS['INFO']}CSV Veri Setleri: {COLORS['SUCCESS']}{self._format_size(datasets_size)}")
            print(f"{COLORS['INFO']}Toplam: {COLORS['SUCCESS']}{self._format_size(total_size)}")
            
        except Exception as e:
            logger.error(f"Sistem istatistikleri görüntülenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def show_league_stats(self) -> None:
        """Lig bazında istatistikleri görüntüler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            leagues = self.config_manager.get_leagues()
            
            if not leagues:
                print(f"\n{COLORS['WARNING']}Yapılandırılmış lig bulunamadı.")
                return
            
            print(f"\n{COLORS['SUBTITLE']}Lig İstatistikleri:")
            print("-" * 50)
            
            for league_id, league_name in leagues.items():
                print(f"\n{COLORS['INFO']}● {league_name} {COLORS['DIM']}(ID: {league_id})")
                
                # Sezon sayısı
                season_count = 0
                season_file = os.path.join(self.data_dir, "seasons", f"{league_id}_{league_name}_seasons.json")
                season_size = 0
                
                if os.path.exists(season_file):
                    # Sezon verisini oku
                    try:
                        with open(season_file, 'r', encoding='utf-8') as f:
                            seasons_data = json.load(f)
                            if isinstance(seasons_data, list):
                                season_count = len(seasons_data)
                    except Exception as e:
                        logger.error(f"Sezon verisi okunurken hata: {e}")
                    
                    # Dosya boyutu
                    season_size = os.path.getsize(season_file)
                
                # Maç sayısı
                match_count = 0
                matches_dir = os.path.join(self.data_dir, "matches", f"{league_name}")
                matches_size = 0
                
                if not os.path.exists(matches_dir):
                    # League ID ile birleşik dizin adı da deneyelim
                    matches_dir = os.path.join(self.data_dir, "matches", f"{league_id}_{league_name}")
                
                if os.path.exists(matches_dir):
                    # Maç verilerini oku
                    matches_size = self._get_directory_size(matches_dir)
                    
                    # CSV dosyalarını bul ve maç sayısını hesapla
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
                
                print(f"  {COLORS['INFO']}○ Sezon Sayısı: {COLORS['SUCCESS']}{season_count}")
                print(f"  {COLORS['INFO']}○ Maç Sayısı: {COLORS['SUCCESS']}{match_count}")
                
                print(f"  {COLORS['INFO']}○ Sezon Verileri: {COLORS['SUCCESS']}{self._format_size(season_size)}")
                print(f"  {COLORS['INFO']}○ Maç Verileri: {COLORS['SUCCESS']}{self._format_size(matches_size)}")
                print(f"  {COLORS['INFO']}○ Toplam: {COLORS['SUCCESS']}{self._format_size(season_size + matches_size)}")
                
        except Exception as e:
            logger.error(f"Lig istatistikleri görüntülenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
            import traceback
            traceback.print_exc()
    
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
                    "match_data_count": 0,
                    "disk_usage": {
                        "seasons": 0,
                        "matches": 0,
                        "match_data": 0,
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
                for league_dir in os.listdir(seasons_dir):
                    league_path = os.path.join(seasons_dir, league_dir)
                    if os.path.isdir(league_path):
                        season_files = [f for f in os.listdir(league_path) if f.endswith(".json")]
                        report_data["stats"]["season_count"] += len(season_files)
            
            # Maç sayısı
            matches_dir = os.path.join(self.data_dir, "matches")
            if os.path.exists(matches_dir):
                for league_dir in os.listdir(matches_dir):
                    league_path = os.path.join(matches_dir, league_dir)
                    if os.path.isdir(league_path):
                        for season_dir in os.listdir(league_path):
                            season_path = os.path.join(league_path, season_dir)
                            if os.path.isdir(season_path):
                                match_files = [f for f in os.listdir(season_path) if f.endswith("_matches.csv")]
                                for match_file in match_files:
                                    file_path = os.path.join(season_path, match_file)
                                    with open(file_path, 'r', encoding='utf-8') as f:
                                        lines = f.readlines()
                                        if len(lines) > 1:  # Başlık satırını çıkar
                                            report_data["stats"]["match_count"] += len(lines) - 1
            
            # Maç detayı sayısı
            match_data_dir = os.path.join(self.data_dir, "match_data")
            if os.path.exists(match_data_dir):
                match_data_files = [f for f in os.listdir(match_data_dir) if f.endswith(".json")]
                report_data["stats"]["match_data_count"] = len(match_data_files)
            
            # Disk kullanımı
            report_data["stats"]["disk_usage"]["seasons"] = self._get_directory_size(seasons_dir) if os.path.exists(seasons_dir) else 0
            report_data["stats"]["disk_usage"]["matches"] = self._get_directory_size(matches_dir) if os.path.exists(matches_dir) else 0
            report_data["stats"]["disk_usage"]["match_data"] = self._get_directory_size(match_data_dir) if os.path.exists(match_data_dir) else 0
            
            datasets_dir = os.path.join(self.data_dir, "datasets")
            report_data["stats"]["disk_usage"]["datasets"] = self._get_directory_size(datasets_dir) if os.path.exists(datasets_dir) else 0
            
            report_data["stats"]["disk_usage"]["total"] = (
                report_data["stats"]["disk_usage"]["seasons"] +
                report_data["stats"]["disk_usage"]["matches"] +
                report_data["stats"]["disk_usage"]["match_data"] +
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
                seasons_dir = os.path.join(self.data_dir, "seasons", f"{league_id}_{league_name}")
                if os.path.exists(seasons_dir):
                    season_files = [f for f in os.listdir(seasons_dir) if f.endswith(".json")]
                    league_data["stats"]["season_count"] = len(season_files)
                
                # Maç sayısı
                matches_dir = os.path.join(self.data_dir, "matches", f"{league_id}_{league_name}")
                if os.path.exists(matches_dir):
                    for season_dir in os.listdir(matches_dir):
                        season_path = os.path.join(matches_dir, season_dir)
                        if os.path.isdir(season_path):
                            for match_file in os.listdir(season_path):
                                if match_file.endswith("_matches.csv"):
                                    file_path = os.path.join(season_path, match_file)
                                    with open(file_path, 'r', encoding='utf-8') as f:
                                        lines = f.readlines()
                                        if len(lines) > 1:  # Başlık satırını çıkar
                                            league_data["stats"]["match_count"] += len(lines) - 1
                
                # Disk kullanımı
                league_data["stats"]["disk_usage"]["seasons"] = self._get_directory_size(seasons_dir) if os.path.exists(seasons_dir) else 0
                league_data["stats"]["disk_usage"]["matches"] = self._get_directory_size(matches_dir) if os.path.exists(matches_dir) else 0
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
                for league_dir in os.listdir(seasons_dir):
                    league_path = os.path.join(seasons_dir, league_dir)
                    if os.path.isdir(league_path):
                        season_files = [f for f in os.listdir(league_path) if f.endswith(".json")]
                        season_count += len(season_files)
            
            match_count = 0
            matches_dir = os.path.join(self.data_dir, "matches")
            if os.path.exists(matches_dir):
                for league_dir in os.listdir(matches_dir):
                    league_path = os.path.join(matches_dir, league_dir)
                    if os.path.isdir(league_path):
                        for season_dir in os.listdir(league_path):
                            season_path = os.path.join(league_path, season_dir)
                            if os.path.isdir(season_path):
                                match_files = [f for f in os.listdir(season_path) if f.endswith("_matches.csv")]
                                for match_file in match_files:
                                    file_path = os.path.join(season_path, match_file)
                                    with open(file_path, 'r', encoding='utf-8') as f:
                                        lines = f.readlines()
                                        if len(lines) > 1:  # Başlık satırını çıkar
                                            match_count += len(lines) - 1
            
            match_data_count = 0
            match_data_dir = os.path.join(self.data_dir, "match_data")
            if os.path.exists(match_data_dir):
                match_data_files = [f for f in os.listdir(match_data_dir) if f.endswith(".json")]
                match_data_count = len(match_data_files)
            
            # Disk kullanımı
            seasons_size = self._get_directory_size(seasons_dir) if os.path.exists(seasons_dir) else 0
            matches_size = self._get_directory_size(matches_dir) if os.path.exists(matches_dir) else 0
            match_data_size = self._get_directory_size(match_data_dir) if os.path.exists(match_data_dir) else 0
            
            datasets_dir = os.path.join(self.data_dir, "datasets")
            datasets_size = self._get_directory_size(datasets_dir) if os.path.exists(datasets_dir) else 0
            
            total_size = seasons_size + matches_size + match_data_size + datasets_size
            
            system_report = {
                "timestamp": self._get_timestamp(),
                "stats": {
                    "league_count": league_count,
                    "season_count": season_count,
                    "match_count": match_count,
                    "match_data_count": match_data_count,
                    "disk_usage": {
                        "seasons": seasons_size,
                        "matches": matches_size,
                        "match_data": match_data_size,
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
                    seasons_dir = os.path.join(self.data_dir, "seasons", f"{league_id}_{league_name}")
                    if os.path.exists(seasons_dir):
                        season_files = [f for f in os.listdir(seasons_dir) if f.endswith(".json")]
                        league_data["stats"]["season_count"] = len(season_files)
                    
                    # Maç sayısı
                    matches_dir = os.path.join(self.data_dir, "matches", f"{league_id}_{league_name}")
                    if os.path.exists(matches_dir):
                        for season_dir in os.listdir(matches_dir):
                            season_path = os.path.join(matches_dir, season_dir)
                            if os.path.isdir(season_path):
                                for match_file in os.listdir(season_path):
                                    if match_file.endswith("_matches.csv"):
                                        file_path = os.path.join(season_path, match_file)
                                        with open(file_path, 'r', encoding='utf-8') as f:
                                            lines = f.readlines()
                                            if len(lines) > 1:  # Başlık satırını çıkar
                                                league_data["stats"]["match_count"] += len(lines) - 1
                    
                    # Disk kullanımı
                    league_data["stats"]["disk_usage"]["seasons"] = self._get_directory_size(seasons_dir) if os.path.exists(seasons_dir) else 0
                    league_data["stats"]["disk_usage"]["matches"] = self._get_directory_size(matches_dir) if os.path.exists(matches_dir) else 0
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