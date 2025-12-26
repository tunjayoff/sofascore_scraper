"""
SofaScore API'sinden lig sezonlarını çeken modül.
"""

import os
import csv
import json
import logging
import asyncio
import aiohttp
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
import datetime
import re

from src.config_manager import ConfigManager
from src.utils import make_api_request, make_api_request_async, get_request_headers, ensure_directory

from src.logger import get_logger

logger = get_logger("SeasonFetcher")

class SeasonFetcher:
    """SofaScore API'sinden lig sezonlarını çeken ve yöneten sınıf."""
    
    def __init__(self, config_manager: ConfigManager, data_dir: str = "data"):
        """
        SeasonFetcher sınıfını başlatır ve mevcut sezon verilerini yükler.
        
        Args:
            config_manager: Lig yapılandırmalarını yöneten ConfigManager örneği
            data_dir: Verilerin kaydedileceği ana dizin
        """
        self.config_manager = config_manager
        self.data_dir = data_dir
        self.seasons_dir = os.path.join(data_dir, "seasons")
        self.base_url = "https://www.sofascore.com/api/v1"
        
        # Veri dizinlerinin var olduğundan emin ol
        ensure_directory(self.data_dir)
        ensure_directory(self.seasons_dir)
        
        # Sezon verilerini saklamak için sözlük
        self.league_seasons = {}
        
        # Mevcut sezon verilerini otomatik olarak yükle
        self._load_existing_season_data()
        
        logger.info("SeasonFetcher başlatıldı - mevcut sezon verileri yüklendi")
    
    def fetch_seasons_for_league(self, league_id: int) -> List[Dict[str, Any]]:
        """
        Belirli bir lig için tüm sezonları çeker.
        
        Args:
            league_id: Lig ID'si
        
        Returns:
            List[Dict[str, Any]]: Sezon bilgilerini içeren liste
        """
        league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")
        logger.info(f"{league_name} (ID: {league_id}) için sezonlar çekiliyor...")
        
        url = f"{self.base_url}/unique-tournament/{league_id}/seasons"
        data = make_api_request(url)
        
        if not data or "seasons" not in data:
            logger.error(f"{league_name} için sezon verileri çekilemedi")
            return []
        
        seasons = data.get("seasons", [])
        
        # Sezon verilerini kaydet
        self._save_seasons_json(league_id, data)
        
        # Sezon verilerini global sözlüğe kaydet
        self.league_seasons[league_id] = seasons
        
        logger.info(f"{league_name} için {len(seasons)} sezon bulundu")
        return seasons
    
    async def _fetch_league_seasons_async(self, session, league_id):
        """Bir lig için sezon verilerini asenkron olarak çeker."""
        league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")
        logger.info(f"{league_name} (ID: {league_id}) için sezonlar asenkron olarak çekiliyor...")
        
        url = f"{self.base_url}/unique-tournament/{league_id}/seasons"
        
        try:
            data = await make_api_request_async(session, url)
            
            if not data or "seasons" not in data:
                logger.error(f"{league_name} için sezon verileri çekilemedi")
                return None
            
            seasons = data.get("seasons", [])
            
            # Sezon verilerini kaydet
            self._save_seasons_json(league_id, data)
            
            # Sezon verilerini global sözlüğe kaydet
            self.league_seasons[league_id] = seasons
            
            logger.info(f"{league_name} için {len(seasons)} sezon bulundu")
            return league_id, seasons
        except Exception as e:
            logger.error(f"Lig {league_id} için asenkron sezon çekme hatası: {str(e)}")
            return None
    
    async def fetch_seasons_batch_async(self, league_ids, max_concurrent=10):
        """Birden çok lig için sezon verilerini paralel olarak çeker."""
        from src.utils import create_session_async
        
        results = {}
        
        # İlerleme çubuğu
        try:
            from tqdm.asyncio import tqdm as async_tqdm
            use_tqdm = True
        except ImportError:
            use_tqdm = False
            print("İlerleme çubuğu için 'pip install tqdm' çalıştırabilirsiniz")
        
        # curl_cffi AsyncSession kullanımı
        async with create_session_async() as session:
            tasks = []
            for league_id in league_ids:
                tasks.append(self._fetch_league_seasons_async(session, league_id))
            
            if use_tqdm:
                for task in async_tqdm.as_completed(tasks, total=len(tasks)):
                    result = await task
                    if result:
                        league_id, seasons = result
                        results[league_id] = seasons
            else:
                completed_tasks = await asyncio.gather(*tasks)
                for result in completed_tasks:
                    if result and isinstance(result, tuple):
                        league_id, seasons = result
                        results[league_id] = seasons
        
        return results
    
    # Senkron wrapper
    def fetch_seasons_batch(self, league_ids, max_concurrent=10):
        """Paralel istekler için senkron wrapper."""
        return asyncio.run(self.fetch_seasons_batch_async(league_ids, max_concurrent))
    
    def fetch_all_leagues_seasons(self) -> Dict[int, List[Dict[str, Any]]]:
        """
        Tüm ligler için sezon bilgilerini çeker ve CSV dosyası olarak kaydeder.
        
        Returns:
            Dict[int, List[Dict[str, Any]]]: Lig ID'leri ve sezon listeleri içeren sözlük
        """
        leagues = self.config_manager.get_leagues()
        
        if not leagues:
            logger.warning("Yapılandırılmış lig bulunamadı. Önce ligler ekleyin.")
            return {}
        
        # Tüm liglerin sezon verilerini çek
        league_ids = list(leagues.keys())
        results = self.fetch_seasons_batch(league_ids)
        
        # Sonuçları CSV dosyasına kaydet
        self._save_seasons_csv()
        
        logger.info(f"Toplam {len(leagues)} lig için sezon verileri çekildi")
        return self.league_seasons
    
    def get_current_season_id(self, league_id) -> int:
        """
        Belirli bir lig için en güncel sezon ID'sini döndürür.
        Eğer en güncel sezon henüz başlamamışsa, bir önceki aktif sezonu döndürür.
        
        Args:
            league_id: Lig ID'si
            
        Returns:
            int: Güncel sezon ID'si
        """
        seasons = self.get_seasons_for_league(league_id)
        if not seasons:
            logger.error(f"Lig ID {league_id} için sezon bulunamadı!")
            return 0
        
        # Şimdiki tarih
        current_date = datetime.datetime.now()
        current_year = current_date.year
        current_month = current_date.month
        
        # Sezonları yıl değerine göre sırala
        sorted_seasons = sorted(
            seasons, 
            key=lambda s: self._get_sortable_year_value(s.get("year", "0")),
            reverse=True  # En yeni sezon en üstte
        )
        
        # Kontrol için listeyi logla
        logger.debug(f"Lig ID {league_id} için sezonlar (sıralı): {[(s.get('id'), s.get('name', ''), s.get('year', '')) for s in sorted_seasons[:5]]}")
        
        # Aktif veya geçmiş sezonları değerlendir
        active_seasons = []      # Aktif sezonlar (güncel yıl ve önceki yıl bazen)
        past_seasons = []        # Geçmiş sezonlar
        future_seasons = []      # Gelecek sezonlar
        
        # Önce tüm sezonları kategorize et
        for idx, season in enumerate(sorted_seasons):
            season_id = season.get("id")
            season_name = season.get("name", "")
            season_year_str = season.get("year", "")
            
            # Sezon yılını çıkar
            season_year = None
            if season_year_str:
                if '/' in season_year_str:
                    # "2024/25" veya "2024/2025" formatı
                    start_year = season_year_str.split('/')[0].strip()
                    if len(start_year) == 4:
                        season_year = int(start_year)
                    elif len(start_year) == 2:
                        # 2 haneli yıl (örn. "24/25")
                        season_year = 2000 + int(start_year)
                else:
                    # Tek yıl formatı: "2024"
                    try:
                        season_year = int(season_year_str)
                    except (ValueError, TypeError):
                        pass
            
            # Sezon adından yıl çıkarmaya çalış
            if not season_year and season_name:
                year_matches = re.findall(r'20\d\d', season_name)
                if year_matches:
                    try:
                        season_year = int(year_matches[0])
                    except (ValueError, TypeError):
                        pass
            
            # Sezon tipi belirle (gelecek, aktif, geçmiş)
            if season_year:
                if season_year > current_year:
                    future_seasons.append(season)
                elif season_year == current_year:
                    active_seasons.append(season)
                elif season_year == current_year - 1:
                    if current_month <= 6:  # Yılın ilk yarısındaysak, önceki sezon da aktif olabilir
                        active_seasons.append(season)
                    else:
                        past_seasons.append(season)
                else:
                    past_seasons.append(season)
            else:
                # Yıl belirlenemezse mevcut en yeni sezondur
                if idx < 2:  # İlk iki sıradaki sezonları aktif kabul et
                    active_seasons.append(season)
                else:
                    past_seasons.append(season)
            
            logger.debug(f"Sezon {season_name} (ID: {season_id}, Yıl: {season_year_str}): Tür = {'active' if season in active_seasons else 'future' if season in future_seasons else 'past'}")
            
            # Maç dosyasının varlığını kontrol et
            season["has_matches"] = False
            season["match_count"] = 0
            
            league_name = self.config_manager.get_league_by_id(league_id) or f"Unknown_League_{league_id}"
            league_dir = f"{league_id}_{league_name.replace(' ', '_')}"
            
            # Matches dizini sabit olarak belirle
            matches_dir = os.path.join("data/matches", league_dir)
            match_file_pattern = f"{season_id}_{season_name.replace(' ', '_')}_round_*.json"
            summary_json_path = os.path.join("data/matches", league_dir, f"{season_id}_{season_name.replace(' ', '_')}_summary.json")
            
            if os.path.exists(matches_dir):
                import glob
                match_files = glob.glob(os.path.join(matches_dir, match_file_pattern))
                season["match_count"] = len(match_files)
                season["has_matches"] = season["match_count"] > 0 or os.path.exists(summary_json_path)
            
            if season["has_matches"]:
                logger.info(f"Sezon {season_name} (ID: {season_id}) için {season['match_count']} maç dosyası bulundu")
            elif season in active_seasons:
                logger.warning(f"Aktif sezon {season_name} (ID: {season_id}) için maç dosyası bulunamadı")
        
        # En iyi sezon seçimi kriterlerini uygula
        # 1. Aktif sezonlar arasında maç dosyası olan varsa, onu seç
        active_seasons_with_matches = [s for s in active_seasons if s.get("has_matches", False)]
        if active_seasons_with_matches:
            selected_season = active_seasons_with_matches[0]  # En yeni olanı al
            logger.info(f"Aktif sezonlar arasından maç dosyası bulunan en yeni sezon seçildi: {selected_season.get('name')} (ID: {selected_season.get('id')})")
            return selected_season.get("id")
        
        # 2. Aktif sezon yoksa veya hiçbirinde maç yoksa, en son tamamlanan sezonu seç
        past_seasons_with_matches = [s for s in past_seasons if s.get("has_matches", False)]
        if past_seasons_with_matches:
            selected_season = past_seasons_with_matches[0]  # En yeni olanı al
            logger.info(f"Geçmiş sezonlar arasından maç dosyası bulunan en yeni sezon seçildi: {selected_season.get('name')} (ID: {selected_season.get('id')})")
            return selected_season.get("id")
        
        # 3. Hiçbir sezonda maç dosyası yoksa, aktif sezonları tercih et
        if active_seasons:
            selected_season = active_seasons[0]  # En yeni aktif sezonu al
            logger.info(f"Hiçbir sezonda maç dosyası bulunamadı, en yeni aktif sezon seçildi: {selected_season.get('name')} (ID: {selected_season.get('id')})")
            return selected_season.get("id")
        
        # 4. Aktif sezon yoksa, tüm sezonlar içinden en yenisini al
        selected_season = sorted_seasons[0]
        logger.info(f"Aktif veya maç dosyası olan sezon bulunamadı, en yeni sezon seçildi: {selected_season.get('name')} (ID: {selected_season.get('id')})")
        return selected_season.get("id")

    def get_season_name(self, league_id: int, season_id: int) -> str:
        """
        Return season name for a specific league and season ID.
        
        Args:
            league_id: Lig ID'si
            season_id: Sezon ID'si
            
        Returns:
            str: Sezon adı veya bulunamazsa varsayılan bir değer
        """
        try:
            # Lig ID'si integer mi kontrol et
            if not isinstance(league_id, int):
                logger.warning(f"get_season_name - Lig ID integer değil: {league_id}")
                league_id = int(league_id) if str(league_id).isdigit() else 0
                
            # Sezon ID'si integer mi kontrol et
            if not isinstance(season_id, int):
                logger.warning(f"get_season_name - Sezon ID integer değil: {season_id}")
                season_id = int(season_id) if str(season_id).isdigit() else 0
            
            # Lig ve sezon verilerini kontrol et
            if league_id in self.league_seasons:
                for season in self.league_seasons[league_id]:
                    if season and isinstance(season, dict) and season.get("id") == season_id:
                        return season.get("name", f"Season_{season_id}")
            
            return f"Season_{season_id}"
            
        except Exception as e:
            logger.warning(f"Sezon adı alınırken hata: {str(e)}")
            return f"Season_{season_id}"
    
    def _load_existing_season_data(self):
        """Daha önce kaydedilmiş sezon verilerini yükler."""
        seasons_csv = os.path.join(self.data_dir, "league_seasons.csv")
        json_files = {}
        
        # İlk olarak, her lig için JSON dosyalarını kontrol et
        if os.path.exists(self.seasons_dir):
            for file_name in os.listdir(self.seasons_dir):
                if file_name.endswith('_seasons.json'):
                    try:
                        # Dosya adından lig ID'sini çıkar (örn: "17_Premier_League_seasons.json")
                        league_id_str = file_name.split('_')[0]
                        league_id = int(league_id_str)
                        
                        # JSON dosyasını oku
                        file_path = os.path.join(self.seasons_dir, file_name)
                        with open(file_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            
                        if "seasons" in data:
                            json_files[league_id] = data["seasons"]
                    except (ValueError, KeyError, json.JSONDecodeError) as e:
                        logger.warning(f"JSON sezon dosyası yüklenirken hata: {file_name} - {str(e)}")
        
        # JSON verilerinden league_seasons'ı doldur
        if json_files:
            self.league_seasons = json_files
            logger.info(f"{len(json_files)} lig için JSON sezon verileri yüklendi")
        
        # Eğer JSON dosyaları yoksa veya eksikse, CSV dosyasını kontrol et
        if not self.league_seasons and os.path.exists(seasons_csv):
            # CSV'den lig ve sezon bilgilerini okuyarak sözlüğü oluştur
            try:
                csv_leagues = {}
                
                with open(seasons_csv, 'r', encoding='utf-8', newline='') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        try:
                            league_id = int(row['Lig ID'])
                            season_id = int(row['Sezon ID'])
                            
                            if league_id not in csv_leagues:
                                csv_leagues[league_id] = []
                            
                            csv_leagues[league_id].append({
                                "id": season_id,
                                "name": row['Sezon Adı'],
                                "year": row['Sezon Yılı']
                            })
                        except (ValueError, KeyError) as e:
                            logger.warning(f"CSV satırı işlenirken hata: {row} - {str(e)}")
                
                # CSV verilerinden league_seasons'ı doldur
                if csv_leagues:
                    self.league_seasons = csv_leagues
                    logger.info(f"{len(csv_leagues)} lig için CSV sezon verileri yüklendi")
            
            except Exception as e:
                logger.error(f"Sezon CSV dosyası yüklenirken hata: {str(e)}")
        
        # Toplam yüklenen sezon sayısını logla
        total_seasons = sum(len(seasons) for seasons in self.league_seasons.values())
        if total_seasons > 0:
            logger.info(f"Toplam {len(self.league_seasons)} lig için {total_seasons} sezon yüklendi")
        else:
            logger.info("Mevcut sezon verisi bulunamadı")
    
    def _save_seasons_json(self, league_id: int, data: Dict[str, Any]):
        """
        Bir lig için çekilen sezon verilerini JSON dosyası olarak kaydeder.
        
        Args:
            league_id: Lig ID'si
            data: API'den alınan sezon verileri
        """
        league_name = self.config_manager.get_leagues().get(league_id, f"unknown_league_{league_id}")
        league_name_safe = league_name.replace(' ', '_')
        file_name = f"{league_id}_{league_name_safe}_seasons.json"
        file_path = os.path.join(self.seasons_dir, file_name)
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"Sezon verileri JSON olarak kaydedildi: {file_path}")
        except Exception as e:
            logger.error(f"JSON dosyası kaydedilirken hata: {str(e)}")
    
    def _save_seasons_csv(self):
        """Tüm lig ve sezon bilgilerini CSV formatında kaydeder."""
        file_path = os.path.join(self.data_dir, "league_seasons.csv")
        
        try:
            with open(file_path, 'w', encoding='utf-8', newline='') as f:
                fieldnames = ["Liga Adı", "Lig ID", "Sezon ID", "Sezon Adı", "Sezon Yılı"]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                
                leagues = self.config_manager.get_leagues()
                
                for league_id, seasons in self.league_seasons.items():
                    league_name = leagues.get(league_id, f"Bilinmeyen Lig {league_id}")
                    
                    for season in seasons:
                        writer.writerow({
                            "Liga Adı": league_name,
                            "Lig ID": league_id,
                            "Sezon ID": season.get("id", ""),
                            "Sezon Adı": season.get("name", ""),
                            "Sezon Yılı": season.get("year", "")
                        })
            
            logger.info(f"Tüm ligler için sezon verileri CSV olarak kaydedildi: {file_path}")
        except Exception as e:
            logger.error(f"CSV dosyası kaydedilirken hata: {str(e)}")
    
    def _get_sortable_year_value(self, year_str: str) -> float:
        """
        Sezon yılı dizesini sıralanabilir bir sayısal değere dönüştürür.
        "24/25", "2024/2025", "2024", "98/99" gibi formatları işler.
        
        Args:
            year_str: Sezon yılı dizesi
        
        Returns:
            float: Yılı temsil eden sıralanabilir bir değer (yüksek = daha yeni)
        """
        if not year_str or year_str == '0':
            return 0.0
        
        # Yıl aralıklarını işle (örn. "24/25" veya "2024/2025")
        if '/' in year_str:
            parts = year_str.split('/')
            start_year = parts[0].strip()
            end_year = parts[1].strip() if len(parts) > 1 else ""
            
            # 2 basamaklı yılları işle
            if len(start_year) == 2 and len(end_year) == 2:
                start_int = int(start_year)
                end_int = int(end_year)
                
                # Eğer ilk yıl ikinci yıldan büyükse (örn. 99/00), bu bir yüzyıl geçişidir
                if start_int > end_int:
                    # Yüzyıl geçişi: 99/00 -> 2000 (yeni yüzyılı kullan)
                    return 2000.0 + float(end_int)
                elif start_int < 50:
                    # 2000'ler (örn: 20/21 -> 2020)
                    return 2000.0 + float(start_int)
                else:
                    # 1900'ler (örn: 98/99 -> 1998)
                    return 1900.0 + float(start_int)
            # 4 basamaklı yılları işle
            elif len(start_year) == 4:
                return float(start_year)
            else:
                try:
                    # Diğer formatlar
                    return float(start_year)
                except ValueError:
                    return 0.0
        
        # Tek yılları işle (örn. "2024")
        try:
            return float(year_str)
        except ValueError:
            return 0.0

    def get_seasons_for_league(self, league_id: int) -> List[Dict[str, Any]]:
        """
        Belirli bir lig için kayıtlı tüm sezonları döndürür.
        
        Args:
            league_id: Lig ID
            
        Returns:
            List[Dict[str, Any]]: Sezon verileri listesi
        """
        # Yüklenmiş veri varsa onu kullan
        try:
            # Lig adını alıp güvenli dosya adı oluşturuyoruz
            league_name = self.config_manager.get_league_by_id(league_id)
            league_name_safe = league_name.replace(' ', '_') if league_name else f"League_{league_id}"
            
            # Doğru formatlı dosya adını oluştur - boşluk yerine alt çizgi içeren
            file_path = os.path.join(self.seasons_dir, f"{league_id}_{league_name_safe}_seasons.json")
            
            if not os.path.exists(file_path):
                # Eski format dosyayı dene
                alt_file_paths = [
                    os.path.join(self.seasons_dir, f"{league_id}_{league_name}_seasons.json"),  # Boşluklu eski format
                    os.path.join(self.seasons_dir, f"{league_name}_seasons.json")               # ID içermeyen eski format
                ]
                
                for alt_path in alt_file_paths:
                    if os.path.exists(alt_path):
                        file_path = alt_path
                        logger.info(f"Alternatif sezon dosyası kullanılıyor: {alt_path}")
                        break
                else:
                    logger.warning(f"Sezon verisi bulunamadı: {file_path}")
                    return []
                
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if "seasons" in data and isinstance(data["seasons"], list):
                    return data["seasons"]
        except Exception as e:
            logger.error(f"Sezon verisi okuma hatası: {str(e)}")
            
        return []
        
    def get_season_info(self, league_id: int, season_id: int) -> Optional[Dict[str, Any]]:
        """
        Belirli bir lig ve sezon ID'si için sezon bilgisini döndürür.
        
        Args:
            league_id: Lig ID
            season_id: Sezon ID
            
        Returns:
            Optional[Dict[str, Any]]: Sezon bilgisi veya None
        """
        seasons = self.get_seasons_for_league(league_id)
        
        # Sezon ID'sine göre sezon bilgisini bul
        for season in seasons:
            if season.get("id") == season_id:
                return season
                
        logger.warning(f"Sezon bilgisi bulunamadı: League {league_id}, Season {season_id}")
        return None