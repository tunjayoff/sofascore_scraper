"""
SofaScore API'sinden detaylı maç verilerini çeken modül.
"""

import os
import json
import csv
import logging
import time
import random
import datetime
import re
from typing import Dict, List, Optional, Any, Union, Tuple
from pathlib import Path
import asyncio
import aiohttp
import urllib.parse
from collections import Counter
import pandas as pd
from tqdm import tqdm

from src.config_manager import ConfigManager
from src.utils import make_api_request, ensure_directory
from src.season_fetcher import SeasonFetcher

# Loglama yapılandırması
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("MatchDataFetcher")

# Gerekli dosyaların listesini ekleyelim
REQUIRED_FILES = [
    'full_data.json',
    'basic.json',
    'statistics.json',
    'team_streaks.json',
    'pregame_form.json',
    'h2h.json'
]

class MatchDataFetcher:
    """SofaScore API'sinden detaylı maç verilerini çeken ve işleyen sınıf."""

    def _find_match_path(self, match_id: str) -> Optional[Tuple[str, str, str]]:
        """
        Find the full path information for a match ID in the new folder structure.
        
        Args:
            match_id: Match ID to search for
        
        Returns:
            Optional[Tuple[str, str, str]]: Tuple of (league_dir, season_dir, full_path) if found, None otherwise
        """
        match_id = str(match_id)
        
        # Search through the directory structure
        for league_name in os.listdir(self.match_details_dir):
            league_path = os.path.join(self.match_details_dir, league_name)
            if not os.path.isdir(league_path) or league_name == "processed":
                continue
            
            for season_name in os.listdir(league_path):
                season_path = os.path.join(league_path, season_name)
                if not os.path.isdir(season_path):
                    continue
                
                match_path = os.path.join(season_path, match_id)
                if os.path.isdir(match_path) and os.path.exists(os.path.join(match_path, "full_data.json")):
                    return (league_name, season_name, match_path)
        
        # Check old structure as fallback
        old_match_path = os.path.join(self.match_details_dir, match_id)
        if os.path.isdir(old_match_path) and os.path.exists(os.path.join(old_match_path, "full_data.json")):
            return (None, None, old_match_path)
        
        return None

    async def _fetch_match_data_async(self, session, match_id):
        try:
            # Temel veriyi çek
            basic_url = f"{self.base_url}/event/{match_id}"
            async with session.get(basic_url) as response:
                if response.status == 200:
                    data = await response.json()
                    basic_data = data.get("event")
                    
                    if not basic_data:
                        return None
                    
                    # Maçın durumunu kontrol et
                    status = basic_data.get("status", {})
                    status_desc = status.get("description", "")
                    status_type = status.get("type", "")
                    
                    # Sadece bitmiş maçları işle
                    if status_desc != "Ended" or status_type != "finished":
                        logger.debug(f"Maç ID {match_id} henüz bitmemiş (Durum: {status_desc}/{status_type}), atlanıyor.")
                        return None
                    
                    # Diğer verileri toplamak için görevleri hazırla, başarısız olanlara rağmen devam et
                    match_data = {"basic": basic_data}
                    
                    # Tüm endpoint'lere eşzamanlı istekler gönder
                    tasks = [
                        self._fetch_endpoint_async(session, f"{self.base_url}/event/{match_id}/statistics", "statistics"),
                        self._fetch_endpoint_async(session, f"{self.base_url}/event/{match_id}/team-streaks", "team_streaks"),
                        self._fetch_endpoint_async(session, f"{self.base_url}/event/{match_id}/pregame-form", "pregame_form"),
                        self._fetch_endpoint_async(session, f"{self.base_url}/event/{match_id}/h2h", "h2h")
                    ]
                    
                    # Tüm görevleri topluca çalıştır, hata veren görevleri atla
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    # Sonuçları işle
                    for result in results:
                        if isinstance(result, Exception):
                            # Hata durumunda loglayıp devam et
                            logger.debug(f"Maç ID {match_id} için endpoint isteğinde hata: {str(result)}")
                            continue
                        
                        if result and isinstance(result, tuple) and len(result) == 2:
                            key, data = result
                            match_data[key] = data
                    
                    # Verileri kaydet (veri toplama ile dosya yazma işlemlerini ayır)
                    self._save_match_data(match_id, match_data)
                    return match_data
                elif response.status == 429:  # Rate limit
                    logger.warning(f"Maç ID {match_id} için rate limit aşıldı. Status: {response.status}")
                    # Sunucuya biraz nefes aldır
                    await asyncio.sleep(2.0 + random.uniform(0, 1))
                    raise Exception("Rate limit exceeded")
                else:
                    logger.debug(f"Maç ID {match_id} için başarısız API yanıtı. Status: {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Maç ID {match_id} için asenkron veri çekilirken hata: {str(e)}")
            raise  # Yeniden deneme mekanizmasının çalışması için hatayı yeniden fırlat

    async def _fetch_endpoint_async(self, session, url, key):
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    return key, await response.json()
        except Exception as e:
            logger.debug(f"{url} için asenkron istek hatası: {str(e)}")
        return key, None

    async def fetch_matches_batch_async(self, match_ids, max_concurrent=30, progress_bar=None):
        """Birden çok maç için veri çeker (optimize edilmiş versiyon)."""
        logger.debug(f"Starting batch fetch for {len(match_ids)} matches")
        
        # Zaten işlenmiş maçları kontrol et
        processed_ids = set()
        details_dir = os.path.join(self.data_dir, "match_details")
        if os.path.exists(details_dir):
            processed_ids = {d for d in os.listdir(details_dir) 
                            if os.path.isdir(os.path.join(details_dir, d))}
        
        # İşlenmemiş maçları filtrele
        match_ids_to_process = [id for id in match_ids if str(id) not in processed_ids]
        
        if len(match_ids_to_process) < len(match_ids):
            already_processed = len(match_ids) - len(match_ids_to_process)
            logger.info(f"{already_processed} maç zaten işlenmiş, atlanıyor")
            # Update progress for already processed matches
            if progress_bar:
                progress_bar.update(already_processed)
        
        results = {}
        
        # Optimize edilmiş HTTP oturum ayarları
        conn = aiohttp.TCPConnector(limit=max_concurrent, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_connect=10, sock_read=30)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.sofascore.com/",
            "Connection": "keep-alive"
        }
        
        # Daha büyük batch'ler halinde işle
        batch_size = 100  # Her seferde 100 maç
        all_batches = [match_ids_to_process[i:i+batch_size] for i in range(0, len(match_ids_to_process), batch_size)]
        
        for batch_idx, batch in enumerate(all_batches):
            logger.info(f"Processing batch {batch_idx+1}/{len(all_batches)} ({len(batch)} matches)")
            
            async with aiohttp.ClientSession(connector=conn, timeout=timeout, headers=headers) as session:
                # Semaphore kullanarak eşzamanlı istek sayısını kontrol et
                sem = asyncio.Semaphore(max_concurrent)
                
                async def fetch_with_retry(match_id):
                    max_retries = 3
                    retry_delay = 1.0
                    
                    for attempt in range(max_retries):
                        try:
                            async with sem:
                                result = await self._fetch_match_data_async(session, match_id)
                                # İlerleme çubuğunu güncelle
                                if progress_bar:
                                    progress_bar.update(1)
                                return result
                        except Exception as e:
                            if attempt < max_retries - 1:
                                # Üstel geri çekilme
                                wait_time = retry_delay * (2 ** attempt) + random.uniform(0, 1)
                                logger.warning(f"Maç ID {match_id} için hata, {wait_time:.2f}s sonra tekrar deneniyor: {str(e)}")
                                await asyncio.sleep(wait_time)
                            else:
                                logger.error(f"Maç ID {match_id} için maksimum deneme sayısı aşıldı: {str(e)}")
                                if progress_bar:
                                    progress_bar.update(1)  # Başarısız olsa bile ilerlemeyi güncelle
                                return None
                
                # Görevleri oluştur
                tasks = [fetch_with_retry(match_id) for match_id in batch]
                
                # Tüm görevleri çalıştır
                completed_tasks = await asyncio.gather(*tasks, return_exceptions=False)
                
                # Sonuçları işle
                for match_data in completed_tasks:
                    if match_data and "basic" in match_data:
                        match_id = match_data["basic"].get("id")
                        if match_id:
                            results[str(match_id)] = match_data
            
            # Batch'ler arasında API sunucusunun nefes alması için kısa bekleme
            if batch_idx < len(all_batches) - 1:
                await asyncio.sleep(1.0)
        
        return results

    # Main metodunda çağırmak için senkron wrapper
    def fetch_matches_batch_parallel(self, match_ids, max_concurrent=10):
        """Paralel istekler için senkron wrapper."""
        print(f"Toplam {len(match_ids)} maç paralel olarak işleniyor...")
        progress = tqdm(total=len(match_ids), desc="Maç detayları çekiliyor")
        
        # Create a new event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Run the async function with progress bar
            results = loop.run_until_complete(self.fetch_matches_batch_async(
                match_ids, max_concurrent, progress
            ))
        finally:
            # Close the loop
            loop.close()
            progress.close()
        
        return results

    
    def __init__(self, config_manager: ConfigManager, data_dir: str = "data"):
        """
        MatchDataFetcher sınıfını başlatır.
        
        Args:
            config_manager: Lig yapılandırmalarını yöneten ConfigManager örneği
            data_dir: Verilerin kaydedileceği ana dizin
        """
        self.config_manager = config_manager
        self.data_dir = data_dir
        self.match_details_dir = os.path.join(data_dir, "match_details")
        self.processed_dir = os.path.join(self.match_details_dir, "processed")
        self.base_url = "https://www.sofascore.com/api/v1"
        
        # Veri dizinlerinin var olduğundan emin ol
        ensure_directory(self.data_dir)
        ensure_directory(self.match_details_dir)
        ensure_directory(self.processed_dir)
    
    def fetch_match_data(self, match_id: Union[int, str]) -> Optional[Dict[str, Any]]:
        """Bir maç için tüm detay verilerini çeker."""
        match_id = str(match_id)
        logger.info(f"Maç ID {match_id} için detay verileri çekiliyor...")
        
        # Önce temel veriyi çek
        basic_data = self._fetch_match_basic(match_id)
        
        # Temel veri yoksa işleme devam etme
        if not basic_data:
            logger.warning(f"Maç ID {match_id} için temel veri bulunamadı")
            return None
        
        # Maçın durumunu kontrol et
        status = basic_data.get("status", {})
        status_code = status.get("code", 0)
        status_desc = status.get("description", "")
        status_type = status.get("type", "")
        
        # Oynanmamış maçları atla
        if status_code == 0 or status_type == "scheduled" or status_type == "notstarted":
            logger.info(f"Maç ID {match_id} henüz oynanmamış, atlanıyor.")
            return None
        
        # Sadece bitmiş maçları işle
        if status_desc != "Ended" or status_type != "finished":
            logger.info(f"Maç ID {match_id} henüz bitmemiş (Durum: {status_desc}/{status_type}), atlanıyor.")
            return None
        
        # Diğer verileri çek
        match_data = {
            "basic": basic_data,
            "statistics": None,
            "team_streaks": None,
            "pregame_form": None,
            "h2h": None
        }
        
        # Diğer endpointleri topla
        match_data["statistics"] = self._fetch_match_statistics(match_id)
        match_data["team_streaks"] = self._fetch_team_streaks(match_id)
        match_data["pregame_form"] = self._fetch_pregame_form(match_id)
        match_data["h2h"] = self._fetch_h2h(match_id)
        
        # Verileri kaydet
        self._save_match_data(match_id, match_data)
        
        return match_data
    
    def _fetch_match_basic(self, match_id: str) -> Optional[Dict[str, Any]]:
        """
        Temel maç bilgilerini çeker.
        
        Args:
            match_id: Maç ID'si
            
        Returns:
            Optional[Dict[str, Any]]: Temel maç verisi veya başarısız ise None
        """
        url = f"{self.base_url}/event/{match_id}"
        try:
            data = make_api_request(url)
            return data.get("event") if data and "event" in data else None
        except Exception as e:
            logger.error(f"Maç ID {match_id} için temel veri çekilirken hata: {str(e)}")
            return None
    
    def _fetch_match_statistics(self, match_id: str) -> Optional[Dict[str, Any]]:
        """
        Maç istatistiklerini çeker.
        
        Args:
            match_id: Maç ID'si
            
        Returns:
            Optional[Dict[str, Any]]: İstatistik verisi veya başarısız ise None
        """
        url = f"{self.base_url}/event/{match_id}/statistics"
        try:
            return make_api_request(url)
        except Exception as e:
            logger.error(f"Maç ID {match_id} için istatistik verisi çekilirken hata: {str(e)}")
            return None
    
    def _fetch_team_streaks(self, match_id: str) -> Optional[Dict[str, Any]]:
        """
        Takım serilerini çeker.
        
        Args:
            match_id: Maç ID'si
            
        Returns:
            Optional[Dict[str, Any]]: Takım serileri verisi veya başarısız ise None
        """
        url = f"{self.base_url}/event/{match_id}/team-streaks"
        try:
            return make_api_request(url)
        except Exception as e:
            logger.error(f"Maç ID {match_id} için takım serileri çekilirken hata: {str(e)}")
            return None
    
    def _fetch_pregame_form(self, match_id: str) -> Optional[Dict[str, Any]]:
        """
        Maç öncesi form verilerini çeker.
        
        Args:
            match_id: Maç ID'si
            
        Returns:
            Optional[Dict[str, Any]]: Form verisi veya başarısız ise None
        """
        url = f"{self.base_url}/event/{match_id}/pregame-form"
        try:
            return make_api_request(url)
        except Exception as e:
            logger.error(f"Maç ID {match_id} için form verisi çekilirken hata: {str(e)}")
            return None
    
    def _fetch_h2h(self, match_id: str) -> Optional[Dict[str, Any]]:
        """
        Head-to-head verilerini çeker.
        
        Args:
            match_id: Maç ID'si
            
        Returns:
            Optional[Dict[str, Any]]: H2H verisi veya başarısız ise None
        """
        url = f"{self.base_url}/event/{match_id}/h2h"
        try:
            return make_api_request(url)
        except Exception as e:
            logger.error(f"Maç ID {match_id} için H2H verisi çekilirken hata: {str(e)}")
            return None
    
    def _save_match_data(self, match_id: str, match_data: Dict[str, Any]) -> None:
        """
        Maç verilerini lig ve sezon bazında organizasyonla JSON olarak kaydeder.
        
        Args:
            match_id: Maç ID'si
            match_data: Kaydedilecek maç verileri
        """
        try:
            # Temel veriyi al
            basic_data = match_data.get("basic", {})
            
            # Lig bilgisini çıkar
            tournament_data = basic_data.get("tournament", {}).get("uniqueTournament", {})
            tournament_id = tournament_data.get("id")
            tournament_name = tournament_data.get("name", "Unknown_League")
            
            # Sezon bilgisini çıkar
            season_data = basic_data.get("season", {})
            season_id = season_data.get("id")
            season_name = season_data.get("name", "Unknown_Season")
            season_year = season_data.get("year", "Unknown_Year")
            
            # Güvenli dizin adları oluştur
            safe_tournament_name = tournament_name.replace(' ', '_').replace('/', '_')
            
            # Sezon adı için güvenli string oluştur - öncelikle name kullan, yoksa year
            if season_name and season_name != "Unknown_Season":
                safe_season_name = f"season_{season_name.replace(' ', '_').replace('/', '_')}"
            elif season_year and season_year != "Unknown_Year":
                safe_season_name = f"season_{season_year.replace('/', '_')}"
            else:
                safe_season_name = f"season_{season_id}"
            
            # Dizin yapısını oluştur: lig/sezon/maç_id
            league_dir = os.path.join(self.match_details_dir, safe_tournament_name)
            ensure_directory(league_dir)
            
            season_dir = os.path.join(league_dir, safe_season_name)
            ensure_directory(season_dir)
            
            match_dir = os.path.join(season_dir, str(match_id))
            ensure_directory(match_dir)
            
            # Ana veriyi kaydet
            full_path = os.path.join(match_dir, "full_data.json")
            with open(full_path, 'w', encoding='utf-8') as f:
                json.dump(match_data, f, ensure_ascii=False, indent=2)
                
            # Her veri türünü ayrıca kaydet
            for data_type, data in match_data.items():
                if data is not None:
                    type_path = os.path.join(match_dir, f"{data_type}.json")
                    with open(type_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                        
            logger.info(f"{safe_tournament_name}, {safe_season_name}, Maç ID {match_id} için veriler başarıyla kaydedildi: {match_dir}")
        except Exception as e:
            logger.error(f"Maç ID {match_id} için veriler kaydedilirken hata: {str(e)}")
            # Hata detayını yazdır
            import traceback
            logger.error(traceback.format_exc())
    
    def process_match_for_csv(self, match_id: str, match_data: Optional[Dict[str, Any]] = None, league_dir: Optional[str] = None, season_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Process match data into a format suitable for CSV export, supporting both old and new folder structures.
        
        Args:
            match_id: Match ID string
            match_data: Pre-loaded match data (if None, will load from file)
            league_dir: League directory name (for new folder structure)
            season_dir: Season directory name (for new folder structure)
        
        Returns:
            Optional[Dict[str, Any]]: Processed data or None if an error occurred
        """
        # If no match data provided, load from file
        if match_data is None:
            # Determine file path based on provided structure
            if league_dir and season_dir:
                # New structure: match_details/league/season/match_id/
                match_dir = os.path.join(self.match_details_dir, league_dir, season_dir, str(match_id))
            else:
                # Old structure: match_details/match_id/
                match_dir = os.path.join(self.match_details_dir, str(match_id))
            
            full_path = os.path.join(match_dir, "full_data.json")
            
            if not os.path.exists(full_path):
                logger.warning(f"Maç ID {match_id} için veri bulunamadı: {full_path}")
                return None
                
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    match_data = json.load(f)
            except Exception as e:
                logger.error(f"Maç ID {match_id} için veri yüklenirken hata: {str(e)}")
                return None
        
        # Extract folder information (if provided)
        league_name = os.path.basename(league_dir) if league_dir else None
        season_name = os.path.basename(season_dir).replace("season_", "") if season_dir else None
        
        # Initialize processed data dictionary with all potential fields set to None
        processed = {
            # Basic match info
            "match_id": match_id,
            "tournament_id": None,
            "tournament_name": None,
            "season_id": None,
            "season_name": None,
            "season_year": None,
            "round": None,
            "home_team_id": None,
            "home_team_name": None,
            "away_team_id": None,
            "away_team_name": None,
            "home_score_ht": None,
            "away_score_ht": None,
            "home_score_ft": None,
            "away_score_ft": None,
            "match_date": None,
            "venue": None,
            "referee": None,
            "status": None,
        }
        
        # Add folder information if available
        if league_name:
            processed["league_folder"] = league_name
        if season_name:
            processed["season_folder"] = season_name
        
        # Extract basic match information
        if "basic" in match_data and match_data["basic"]:
            basic = match_data["basic"]
            processed.update({
                "tournament_id": basic.get("tournament", {}).get("uniqueTournament", {}).get("id"),
                "tournament_name": basic.get("tournament", {}).get("uniqueTournament", {}).get("name"),
                "season_id": basic.get("season", {}).get("id"),
                "season_name": basic.get("season", {}).get("name"),
                "season_year": basic.get("season", {}).get("year"),
                "round": basic.get("roundInfo", {}).get("round"),
                "home_team_id": basic.get("homeTeam", {}).get("id"),
                "home_team_name": basic.get("homeTeam", {}).get("name"),
                "away_team_id": basic.get("awayTeam", {}).get("id"),
                "away_team_name": basic.get("awayTeam", {}).get("name"),
                "home_score_ht": basic.get("homeScore", {}).get("period1"),
                "away_score_ht": basic.get("awayScore", {}).get("period1"),
                "home_score_ft": basic.get("homeScore", {}).get("normaltime"),
                "away_score_ft": basic.get("awayScore", {}).get("normaltime"),
                "match_date": basic.get("startTimestamp"),
                "venue": basic.get("venue", {}).get("name"),
                "referee": basic.get("referee", {}).get("name"),
                "status": basic.get("status", {}).get("description")
            })
        
        # Process statistics data
        if "statistics" in match_data and match_data["statistics"]:
            stats = match_data["statistics"]
            # Find the "ALL" period statistics
            for period in stats.get("statistics", []):
                if period.get("period") == "ALL":
                    for group in period.get("groups", []):
                        for item in group.get("statisticsItems", []):
                            key = item.get("key")
                            if key:
                                processed[f"home_{key}"] = item.get("homeValue")
                                processed[f"away_{key}"] = item.get("awayValue")
        
        # Process team streaks data
        if "team_streaks" in match_data and match_data["team_streaks"]:
            for streak in match_data["team_streaks"].get("general", []):
                team = streak.get("team", "")
                if team in ["home", "away"]:
                    name = f"{team}_streak_{streak.get('name', '').lower().replace(' ', '_')}"
                    processed[name] = streak.get("value")
                    processed[f"{name}_continued"] = streak.get("continued", False)
        
        # Process form data
        if "pregame_form" in match_data and match_data["pregame_form"]:
            home_form = match_data["pregame_form"].get("homeTeam", {})
            away_form = match_data["pregame_form"].get("awayTeam", {})
            
            processed.update({
                "home_position": home_form.get("position"),
                "away_position": away_form.get("position"),
                "home_points": home_form.get("value"),
                "away_points": away_form.get("value"),
                "home_rating": home_form.get("avgRating"),
                "away_rating": away_form.get("avgRating"),
                "home_form": "_".join(home_form.get("form", [])) if home_form.get("form") else None,
                "away_form": "_".join(away_form.get("form", [])) if away_form.get("form") else None
            })
        
        # Process H2H data
        if "h2h" in match_data and match_data["h2h"]:
            h2h = match_data["h2h"].get("teamDuel", {})
            processed.update({
                "h2h_home_wins": h2h.get("homeWins"),
                "h2h_away_wins": h2h.get("awayWins"),
                "h2h_draws": h2h.get("draws")
            })
        
        return processed
    
    def fetch_matches_batch(self, match_ids: List[Union[int, str]]) -> Dict[str, Dict[str, Any]]:
        """Bir grup maç için veri çeker."""
        # tqdm modülünü ekleyelim
        try:
            use_tqdm = True
        except ImportError:
            use_tqdm = False
            print("İlerleme çubuğu için 'pip install tqdm' çalıştırabilirsiniz")
        
        results = {}
        
        # Zaten işlenmiş maçları kontrol et
        processed_ids = set()
        details_dir = os.path.join(self.data_dir, "match_details")
        if os.path.exists(details_dir):
            processed_ids = {d for d in os.listdir(details_dir) 
                            if os.path.isdir(os.path.join(details_dir, d))}
        
        # İşlenmemiş maçları filtrele
        match_ids_to_process = [id for id in match_ids if str(id) not in processed_ids]
        
        if len(match_ids_to_process) < len(match_ids):
            logger.info(f"{len(match_ids) - len(match_ids_to_process)} maç zaten işlenmiş, atlanıyor")
        
        # İlerleme çubuğu
        iterator = tqdm(match_ids_to_process) if use_tqdm else match_ids_to_process
        
        for match_id in iterator:
            match_id = str(match_id)
            
            if use_tqdm:
                iterator.set_description(f"Maç ID {match_id}")
            else:
                logger.info(f"Maç verisi çekiliyor: ID {match_id}")
            
            match_data = self.fetch_match_data(match_id)
            
            if match_data:
                results[match_id] = match_data
            
            # Sabit kısa bekleme (SofaScore saniyede 5 isteğe izin veriyor)
            if match_id != match_ids_to_process[-1]:  # Son elemandan sonra bekleme yapma
                time.sleep(0.2)  # Saniyede 5 istek için
        
        return results
    
    def create_csv_dataset(self, match_ids: Optional[List[Union[int, str]]] = None, 
                        separate_by_league: bool = False) -> Union[str, List[str]]:
        """
        Convert match data to CSV format, with option to create separate files by league.
        
        Args:
            match_ids: List of match IDs to process (if None, all matches will be processed)
            separate_by_league: If True, creates separate CSV files for each league
            
        Returns:
            Union[str, List[str]]: Path(s) to created CSV file(s), or empty string/list if an error occurred
        """
        import re  # For safe filename creation
        
        # Collection for processed match data - will hold all matches
        all_processed_matches = []
        # Dictionary to group matches by league when creating separate CSVs
        league_matches = {}  # {league_name: [match_data, ...], ...}
        
        # If no specific match IDs are provided, process all matches in the directory structure
        if match_ids is None:
            match_infos = []  # Will hold tuples of (league_name, season_name, match_id)
            
            try:
                # Scan league directories
                for league_name in os.listdir(self.match_details_dir):
                    league_path = os.path.join(self.match_details_dir, league_name)
                    
                    # Skip non-directories and the "processed" directory
                    if not os.path.isdir(league_path) or league_name == "processed":
                        continue
                    
                    # Check if this is the old structure where match IDs are direct subdirectories
                    if os.path.exists(os.path.join(league_path, "full_data.json")):
                        # This is a match folder in the old structure
                        match_id = league_name
                        # Try to extract league name from the data
                        try:
                            with open(os.path.join(league_path, "basic.json"), 'r', encoding='utf-8') as f:
                                basic_data = json.load(f)
                                actual_league = basic_data.get("tournament", {}).get("uniqueTournament", {}).get("name", "Unknown")
                                actual_season = basic_data.get("season", {}).get("name", "Unknown")
                                match_infos.append((actual_league, actual_season, match_id))
                        except Exception as e:
                            logger.warning(f"Eski yapıdaki {match_id} maçı için veri okunamadı: {str(e)}")
                            # Fall back to "Unknown" if we can't extract league name
                            match_infos.append(("Unknown", "Unknown", match_id))
                        continue
                    
                    # Scan season directories in the new structure
                    for season_name in os.listdir(league_path):
                        season_path = os.path.join(league_path, season_name)
                        if not os.path.isdir(season_path):
                            continue
                        
                        # Scan match directories
                        for match_id in os.listdir(season_path):
                            match_path = os.path.join(season_path, match_id)
                            if os.path.isdir(match_path) and os.path.exists(os.path.join(match_path, "full_data.json")):
                                match_infos.append((league_name, season_name, match_id))
                
                # Also check for matches directly under match_details (old structure)
                for item in os.listdir(self.match_details_dir):
                    direct_path = os.path.join(self.match_details_dir, item)
                    if os.path.isdir(direct_path) and item != "processed" and os.path.exists(os.path.join(direct_path, "full_data.json")):
                        # This is likely a match ID from the old structure
                        match_id = item
                        # Try to extract league info
                        try:
                            with open(os.path.join(direct_path, "basic.json"), 'r', encoding='utf-8') as f:
                                basic_data = json.load(f)
                                actual_league = basic_data.get("tournament", {}).get("uniqueTournament", {}).get("name", "Unknown")
                                actual_season = basic_data.get("season", {}).get("name", "Unknown")
                                match_infos.append((actual_league, actual_season, match_id))
                        except:
                            match_infos.append(("Unknown", "Unknown", match_id))
                
                # Log summary of found matches
                logger.info(f"Toplam {len(match_infos)} maç CSV'ye dönüştürülüyor...")
                
                # Process each match
                for league_name, season_name, match_id in tqdm(match_infos, desc="Maçlar işleniyor"):
                    # For league directory, we may need to use the folder name or league name from data
                    league_dir = league_name if os.path.isdir(os.path.join(self.match_details_dir, league_name)) else None
                    season_dir = season_name if league_dir and os.path.isdir(os.path.join(self.match_details_dir, league_dir, season_name)) else None
                    
                    # Process the match
                    processed = self.process_match_for_csv(
                        match_id=match_id,
                        league_dir=league_dir,
                        season_dir=season_dir
                    )
                    
                    if processed:
                        # Add the match to the combined list
                        all_processed_matches.append(processed)
                        
                        # If creating separate files by league, organize by league
                        if separate_by_league:
                            # Use either the folder name or the tournament name from the data
                            league_key = processed.get("league_folder", 
                                        processed.get("tournament_name", "Unknown"))
                            
                            if league_key not in league_matches:
                                league_matches[league_key] = []
                            
                            league_matches[league_key].append(processed)
                        
            except Exception as e:
                logger.error(f"Klasör yapısı taranırken hata: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                return "" if not separate_by_league else []
                
        else:
            # Process specific match IDs provided by the user
            match_ids = [str(mid) for mid in match_ids]  # Convert all IDs to strings
            logger.info(f"Belirtilen {len(match_ids)} maç CSV'ye dönüştürülüyor...")
            
            # Process each specified match ID
            for match_id in tqdm(match_ids, desc="Belirtilen maçlar işleniyor"):
                # Search for this match in the directory structure
                match_found = False
                
                # First check new structure
                for league_name in os.listdir(self.match_details_dir):
                    league_path = os.path.join(self.match_details_dir, league_name)
                    
                    if not os.path.isdir(league_path) or league_name == "processed":
                        continue
                    
                    # Skip if this is a match folder (old structure)
                    if os.path.exists(os.path.join(league_path, "full_data.json")):
                        continue
                    
                    # Check each season
                    for season_name in os.listdir(league_path):
                        season_path = os.path.join(league_path, season_name)
                        if not os.path.isdir(season_path):
                            continue
                        
                        # Check if this match exists in this season
                        match_path = os.path.join(season_path, match_id)
                        if os.path.isdir(match_path) and os.path.exists(os.path.join(match_path, "full_data.json")):
                            # Process with new structure parameters
                            processed = self.process_match_for_csv(
                                match_id=match_id,
                                league_dir=league_name,
                                season_dir=season_name
                            )
                            
                            if processed:
                                all_processed_matches.append(processed)
                                
                                # Organize by league if needed
                                if separate_by_league:
                                    league_key = processed.get("league_folder", 
                                                processed.get("tournament_name", "Unknown"))
                                    
                                    if league_key not in league_matches:
                                        league_matches[league_key] = []
                                    
                                    league_matches[league_key].append(processed)
                            
                            match_found = True
                            break
                        
                    if match_found:
                        break
                
                # If not found in new structure, check old structure
                if not match_found:
                    # Check direct match folder
                    direct_path = os.path.join(self.match_details_dir, match_id)
                    if os.path.isdir(direct_path) and os.path.exists(os.path.join(direct_path, "full_data.json")):
                        # Process with old structure
                        processed = self.process_match_for_csv(match_id=match_id)
                        
                        if processed:
                            all_processed_matches.append(processed)
                            
                            # Organize by league if needed
                            if separate_by_league:
                                league_key = processed.get("tournament_name", "Unknown")
                                
                                if league_key not in league_matches:
                                    league_matches[league_key] = []
                                
                                league_matches[league_key].append(processed)
                    else:
                        # Also check if it might be a league folder name (old structure)
                        for item in os.listdir(self.match_details_dir):
                            item_path = os.path.join(self.match_details_dir, item)
                            if os.path.isdir(item_path) and item == match_id and os.path.exists(os.path.join(item_path, "full_data.json")):
                                # This is a match with a league name as its ID (unusual but possible)
                                processed = self.process_match_for_csv(match_id=match_id)
                                
                                if processed:
                                    all_processed_matches.append(processed)
                                    
                                    if separate_by_league:
                                        league_key = processed.get("tournament_name", "Unknown")
                                        
                                        if league_key not in league_matches:
                                            league_matches[league_key] = []
                                        
                                        league_matches[league_key].append(processed)
                                
                                match_found = True
                                break
                
                if not match_found:
                    logger.warning(f"Maç ID {match_id} için veri bulunamadı")
        
        # Check if we have processed any matches
        if not all_processed_matches:
            logger.warning("İşlenecek maç verisi bulunamadı")
            return "" if not separate_by_league else []
        
        # Generate timestamp for filenames
        timestamp = int(time.time())
        
        # Create separate CSV files by league if requested
        if separate_by_league:
            csv_paths = []
            
            for league_name, matches in league_matches.items():
                if not matches:
                    continue
                
                # Create a safe filename from the league name
                safe_league_name = re.sub(r'[^\w]', '_', league_name)
                csv_path = os.path.join(self.processed_dir, f"{safe_league_name}_{timestamp}.csv")
                
                # Write the CSV file
                if self._write_matches_to_csv(matches, csv_path):
                    csv_paths.append(csv_path)
                    logger.info(f"{league_name} ligi için CSV dosyası oluşturuldu: {csv_path}")
            
            if not csv_paths:
                logger.warning("Hiçbir lig için CSV dosyası oluşturulamadı")
            
            return csv_paths
        else:
            # Create a single combined CSV file
            csv_path = os.path.join(self.processed_dir, f"all_matches_{timestamp}.csv")
            
            if self._write_matches_to_csv(all_processed_matches, csv_path):
                logger.info(f"Tüm maçlar için CSV dosyası oluşturuldu: {csv_path}")
                return csv_path
            else:
                return ""

    def _write_matches_to_csv(self, matches: List[Dict[str, Any]], csv_path: str) -> bool:
        """Helper function to write matches to a CSV file with prioritized columns."""
        try:
            # Determine all columns from the data
            all_columns = set()
            for match in matches:
                all_columns.update(match.keys())
            
            # Define priority columns to appear first in the CSV
            priority_columns = ["match_id", "league_folder", "season_folder", "tournament_name", 
                            "season_name", "round", "home_team_name", "away_team_name", 
                            "home_score_ft", "away_score_ft", "match_date"]
            
            # Create final column order: priority columns first, then all others alphabetically
            fieldnames = [col for col in priority_columns if col in all_columns]
            
            for col in sorted(all_columns):
                if col not in fieldnames:
                    fieldnames.append(col)
            
            # Write CSV file
            with open(csv_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                
                for match in matches:
                    writer.writerow(match)
            
            return True
        except Exception as e:
            logger.error(f"CSV yazılırken hata: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def fetch_all_match_data(self) -> bool:
        """
        Tüm maçlar için detaylı verileri çeker.
        Mevcut maç listesinden henüz detayları çekilmemiş maçları bulup işler.
        
        Returns:
            bool: İşlem başarılı ise True, değilse False
        """
        try:
            logger.info("Tüm maçlar için detaylı veri çekme işlemi başlatılıyor...")
            match_ids = []
            
            # "matches" dizini içindeki tüm maç ID'lerini bul
            matches_dir = os.path.join(self.data_dir, "matches")
            if not os.path.exists(matches_dir):
                logger.warning("Maç dizini bulunamadı!")
                return False
            
            # Tüm ligleri ve sezonları tara
            for league_dir in os.listdir(matches_dir):
                league_path = os.path.join(matches_dir, league_dir)
                if not os.path.isdir(league_path):
                    continue
                
                # Doğrudan lig dizinindeki CSV dosyalarını kontrol et
                for file_name in os.listdir(league_path):
                    if file_name.endswith('_matches.csv') or file_name.endswith('_summary.csv'):
                        file_path = os.path.join(league_path, file_name)
                        if os.path.isfile(file_path):
                            ids_from_csv = self._extract_match_ids_from_csv(file_path)
                            if ids_from_csv:
                                match_ids.extend(ids_from_csv)
                                logger.debug(f"{file_path} dosyasından {len(ids_from_csv)} maç ID'si alındı.")
                
                # Sezonları kontrol et
                for season_dir in os.listdir(league_path):
                    season_path = os.path.join(league_path, season_dir)
                    
                    # Sadece dizinleri işle
                    if not os.path.isdir(season_path):
                        continue
                    
                    # Sezon dizinindeki match CSV dosyalarını bul
                    for file_name in os.listdir(season_path):
                        if file_name.endswith('_matches.csv') or file_name.endswith('_summary.csv'):
                            file_path = os.path.join(season_path, file_name)
                            ids_from_csv = self._extract_match_ids_from_csv(file_path)
                            if ids_from_csv:
                                match_ids.extend(ids_from_csv)
                                logger.debug(f"{file_path} dosyasından {len(ids_from_csv)} maç ID'si alındı.")
            
            # Tekrarlanan ID'leri temizle
            match_ids = list(set(match_ids))
            logger.info(f"Toplam {len(match_ids)} maç ID'si bulundu.")
            
            if not match_ids:
                logger.warning("İşlenecek maç bulunamadı!")
                return False
            
            # Zaten çekilmiş detayları kontrol et ve sadece eksik olanları çek
            match_details_dir = os.path.join(self.data_dir, "match_data")
            existing_details = set()
            
            # match_data dizini içindeki tüm JSON dosyalarını kontrol et
            if os.path.exists(match_details_dir):
                for root, dirs, files in os.walk(match_details_dir):
                    for file in files:
                        if file.endswith('.json'):
                            # Dosya adından maç ID'sini çıkar
                            match_id = os.path.splitext(file)[0]
                            try:
                                # Sayısal ID ise ekle
                                if match_id.isdigit():
                                    existing_details.add(match_id)
                            except:
                                continue
            
            # Sadece eksik detayları çek
            missing_match_ids = [match_id for match_id in match_ids if match_id not in existing_details]
            logger.info(f"{len(missing_match_ids)} maç için detaylar çekilecek.")
            
            if not missing_match_ids:
                logger.info("Tüm maçların detayları zaten çekilmiş!")
                return True
            
            # Maç detaylarını paralel olarak çek
            # Büyük miktarda maç varsa, daha küçük gruplara bölelim
            batch_size = 100  # Her seferde kaç maç işleneceği
            
            total_success = 0
            total_attempts = len(missing_match_ids)
            
            print(f"Toplam {total_attempts} maç detayı çekilecek...")
            
            for i in range(0, len(missing_match_ids), batch_size):
                batch = missing_match_ids[i:i+batch_size]
                current_batch = f"Batch {i//batch_size + 1}/{(len(missing_match_ids)-1)//batch_size + 1}"
                print(f"{current_batch}: {len(batch)} maç işleniyor...")
                
                # fetch_matches_batch_parallel metodunu kullan (bu metot zaten paralel işlem yapıyor ve ilerleme çubuğu gösterir)
                results = self.fetch_matches_batch_parallel(batch, max_concurrent=30)
                
                if results:
                    total_success += len(results)
                    # Her batch arasında kısa bir bekleme
                    if i + batch_size < len(missing_match_ids):
                        time.sleep(1.0)
            
            success_rate = (total_success / total_attempts) * 100 if total_attempts > 0 else 0
            print(f"\nSonuç: Toplam {total_success}/{total_attempts} maç (% {success_rate:.1f}) başarıyla işlendi.")
            
            return total_success > 0
            
        except Exception as e:
            logger.error(f"Tüm maç detayları çekilirken hata: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def _extract_match_ids_from_csv(self, csv_path: str) -> List[str]:
        """
        CSV dosyasından maç ID'lerini çıkarır.
        
        Args:
            csv_path: CSV dosyasının yolu
            
        Returns:
            List[str]: Maç ID'leri listesi
        """
        match_ids = []
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                csv_reader = csv.DictReader(f)
                for row in csv_reader:
                    # Farklı sütun adlarını kontrol et
                    match_id = None
                    id_columns = ['match_id', 'matchId', 'id', 'match-id', 'matchid']
                    
                    for column in id_columns:
                        if column in row and row[column]:
                            match_id = row[column]
                            break
                    
                    # Sayısal ID ise ekle
                    if match_id and str(match_id).isdigit():
                        match_ids.append(str(match_id))
        except Exception as e:
            logger.error(f"CSV dosyasından maç ID'leri çıkarılırken hata: {str(e)} - {csv_path}")
        
        return match_ids

    def fetch_all_match_details(self, league_id: Optional[str] = None, max_seasons: int = 0) -> bool:
        """
        Tüm maçlar için detaylı verileri çeker.
        UI tarafından çağrılmak üzere tasarlanmıştır.
        
        Args:
            league_id: Belirli bir lig ID'si (None ise tüm ligler)
            max_seasons: Son kaç sezon işlenecek (0 ise tüm sezonlar)
            
        Returns:
            bool: İşlem başarılı ise True, değilse False
        """
        try:
            match_ids = []
            
            # "matches" dizini içindeki tüm maç ID'lerini bul
            matches_dir = os.path.join(self.data_dir, "matches")
            if not os.path.exists(matches_dir):
                logger.warning("Maç dizini bulunamadı!")
                return False
            
            # Tüm ligleri ve sezonları tara
            league_dirs = []
            
            # Belirli bir lig seçilmişse sadece o ligi işle
            if league_id:
                print(f"Lig ID {league_id} için maç detayları çekiliyor...")
                for dir_name in os.listdir(matches_dir):
                    if dir_name.startswith(f"{league_id}_"):
                        league_dirs.append(dir_name)
                        break
                
                if not league_dirs:
                    print(f"Lig ID {league_id} için maç dizini bulunamadı!")
                    return False
            else:
                print(f"Tüm ligler için maç detayları çekiliyor...")
                # Tüm ligleri işle
                league_dirs = [dir_name for dir_name in os.listdir(matches_dir) 
                              if os.path.isdir(os.path.join(matches_dir, dir_name))]
            
            # Toplam işlenecek lig sayısını göster
            print(f"Toplam {len(league_dirs)} lig işlenecek...")
            
            # Her lig için işlem yap
            for league_dir in league_dirs:
                league_path = os.path.join(matches_dir, league_dir)
                if not os.path.isdir(league_path):
                    continue
                
                current_league_id = league_dir.split('_')[0] if '_' in league_dir else None
                print(f"\nLig dizini: {league_dir}")
                
                # Doğrudan lig dizinindeki CSV dosyalarını kontrol et
                summary_files = []
                for file_name in os.listdir(league_path):
                    if file_name.endswith('_matches.csv') or file_name.endswith('_summary.csv'):
                        summary_files.append((os.path.join(league_path, file_name), file_name))
                
                # Sezonları kontrol et
                season_dirs = []
                for season_dir in os.listdir(league_path):
                    season_path = os.path.join(league_path, season_dir)
                    if os.path.isdir(season_path):
                        # Sezon ID'sini ve adını çıkar
                        season_parts = season_dir.split('_', 1)
                        season_id = season_parts[0] if len(season_parts) > 0 else None
                        season_name = season_parts[1] if len(season_parts) > 1 else season_dir
                        
                        summary_file_path = os.path.join(league_path, f"{season_id}_{season_name}_summary.csv")
                        if os.path.exists(summary_file_path):
                            summary_files.append((summary_file_path, os.path.basename(summary_file_path)))
                        
                        # Sezon dizini için dosyaları da kontrol et
                        for file_name in os.listdir(season_path):
                            if file_name.endswith('_matches.csv') or file_name.endswith('_summary.csv'):
                                summary_files.append((os.path.join(season_path, file_name), file_name))
                        
                        season_dirs.append((season_path, season_dir, season_id, season_name))
                
                # Sezonları tarihe göre sırala (en yeni en üstte)
                season_dirs.sort(key=lambda x: x[2] if x[2] and x[2].isdigit() else '0', reverse=True)
                
                # Son N sezonu seç
                if max_seasons > 0 and len(season_dirs) > max_seasons:
                    print(f"Son {max_seasons} sezon seçiliyor (toplam {len(season_dirs)} sezon mevcut)")
                    season_dirs = season_dirs[:max_seasons]
                
                # Seçilen sezonlar için özet dosyalarını al
                for season_path, season_dir, season_id, season_name in season_dirs:
                    summary_file_path = os.path.join(league_path, f"{season_id}_{season_name}_summary.csv")
                    if os.path.exists(summary_file_path):
                        summary_files.append((summary_file_path, os.path.basename(summary_file_path)))
                    
                    # Sezon dizini için dosyaları da kontrol et
                    for file_name in os.listdir(season_path):
                        if file_name.endswith('_matches.csv') or file_name.endswith('_summary.csv'):
                            summary_files.append((os.path.join(season_path, file_name), file_name))
                
                # Özet dosyalarından maç ID'lerini çıkar
                current_ids = []
                for file_path, file_name in summary_files:
                    if os.path.isfile(file_path):
                        ids_from_csv = self._extract_match_ids_from_csv(file_path)
                        if ids_from_csv:
                            current_ids.extend(ids_from_csv)
                
                if current_ids:
                    print(f"Lig için {len(current_ids)} maç ID'si bulundu.")
                    match_ids.extend(current_ids)
            
            # Tekrarlanan ID'leri temizle
            match_ids = list(set(match_ids))
            print(f"Toplam {len(match_ids)} benzersiz maç ID'si bulundu.")
            
            if not match_ids:
                print("Hiç maç ID'si bulunamadı!")
                return False
            
            # Zaten işlenmiş maçları kontrol et ve filtrele
            match_details_dir = os.path.join(self.data_dir, "match_details")
            existing_details = set()
            
            # match_details dizini içindeki tüm işlenmiş maçları kontrol et
            if os.path.exists(match_details_dir):
                for league_name in os.listdir(match_details_dir):
                    league_path = os.path.join(match_details_dir, league_name)
                    if not os.path.isdir(league_path) or league_name == "processed":
                        continue
                    
                    for season_name in os.listdir(league_path):
                        season_path = os.path.join(match_details_dir, league_name, season_name)
                        if not os.path.isdir(season_path):
                            continue
                        
                        for match_id in os.listdir(season_path):
                            if os.path.isdir(os.path.join(season_path, match_id)) and \
                               os.path.exists(os.path.join(season_path, match_id, "full_data.json")):
                                existing_details.add(match_id)
                
                # Eski yapıdaki maçları da kontrol et
                for item in os.listdir(match_details_dir):
                    item_path = os.path.join(match_details_dir, item)
                    if os.path.isdir(item_path) and item != "processed" and \
                       os.path.exists(os.path.join(item_path, "full_data.json")):
                        existing_details.add(item)
            
            # İşlenmemiş maçları filtrele
            match_ids_to_process = [id for id in match_ids if str(id) not in existing_details]
            
            if len(match_ids_to_process) < len(match_ids):
                already_processed = len(match_ids) - len(match_ids_to_process)
                print(f"{already_processed} maç zaten işlenmiş, atlanıyor")
            
            if not match_ids_to_process:
                print("Tüm maçların detayları zaten çekilmiş!")
                return True
            
            # Maç detaylarını paralel olarak çek
            batch_size = 100  # Her seferde kaç maç işleneceği
            total_success = 0
            total_attempts = len(match_ids_to_process)
            
            print(f"\nToplam {total_attempts} maç için detaylar çekilecek...")
            
            # İlerleme gösterimi için daha temiz bir format
            for i in range(0, len(match_ids_to_process), batch_size):
                batch = match_ids_to_process[i:i+batch_size]
                current_batch = i // batch_size + 1
                total_batches = (len(match_ids_to_process) - 1) // batch_size + 1
                start_index = i + 1
                end_index = min(i + len(batch), total_attempts)
                
                print(f"\nBatch {current_batch}/{total_batches}: {len(batch)} maç işleniyor ({start_index}-{end_index}/{total_attempts})...")
                
                # Paralel işleme ile maç detaylarını çek
                results = self.fetch_matches_batch_parallel(batch, max_concurrent=30)
                
                if results:
                    success_count = len(results)
                    total_success += success_count
                    print(f"✓ Batch {current_batch}: {success_count}/{len(batch)} başarılı")
                    
                    # Her batch arasında kısa bir bekleme
                    if i + batch_size < len(match_ids_to_process):
                        time.sleep(1.0)
            
            # Genel başarı oranı
            success_rate = (total_success / total_attempts) * 100 if total_attempts > 0 else 0
            print(f"\nİşlem tamamlandı: {total_success}/{total_attempts} maç (% {success_rate:.1f}) başarıyla işlendi.")
            
            return total_success > 0
            
        except Exception as e:
            logger.error(f"Tüm maç detayları çekilirken hata: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def fetch_match_details(self, match_id: Union[int, str]) -> bool:
        """
        Bir maç için detay verilerini çeker ve kaydeder.
        UI tarafından çağrılmak üzere tasarlanmıştır.
        
        Args:
            match_id: Maç ID'si
            
        Returns:
            bool: İşlem başarılı ise True, değilse False
        """
        try:
            match_id = str(match_id)
            logger.info(f"Maç ID {match_id} için detaylar çekiliyor...")
            
            # Daha önce çekilmiş mi kontrol et
            match_path = self._find_match_path(match_id)
            if match_path:
                logger.info(f"Maç ID {match_id} daha önce işlenmiş, atlanıyor.")
                return True
            
            # Maç verilerini çek
            match_data = self.fetch_match_data(match_id)
            
            if not match_data:
                logger.warning(f"Maç ID {match_id} için veri bulunamadı veya maç henüz bitmemiş.")
                return False
            
            # Maç verisini işle ve kaydet
            # Temel bilgileri al
            basic_data = match_data.get("basic", {})
            tournament = basic_data.get("tournament", {})
            season = basic_data.get("season", {})
            
            # Liga ve sezon adları
            tournament_name = tournament.get("name", "Unknown")
            tournament_id = tournament.get("id", "0")
            season_name = season.get("name", "Unknown")
            season_id = season.get("id", "0")
            
            # Dizin yapısı oluştur
            league_dir_name = f"{tournament_id}_{tournament_name.replace(' ', '_').replace('/', '_')}"
            season_dir_name = f"{season_id}_{season_name.replace(' ', '_').replace('/', '_')}"
            
            league_dir = os.path.join(self.match_details_dir, league_dir_name)
            ensure_directory(league_dir)
            
            season_dir = os.path.join(league_dir, season_dir_name)
            ensure_directory(season_dir)
            
            # Maç verilerini kaydet
            match_file = os.path.join(season_dir, f"{match_id}.json")
            
            with open(match_file, "w", encoding="utf-8") as f:
                json.dump(match_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Maç ID {match_id} detayları başarıyla kaydedildi: {match_file}")
            return True
            
        except Exception as e:
            logger.error(f"Maç ID {match_id} için detay çekilirken hata: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    # CSV Dönüştürme metodları - UI'dan çağrılan metodlar
    def convert_match_to_csv(self, match_id: Union[int, str]) -> Optional[str]:
        """
        Tek bir maçın verilerini CSV formatına dönüştürür.
        
        Args:
            match_id: Dönüştürülecek maçın ID'si
            
        Returns:
            Optional[str]: Oluşturulan CSV dosyasının yolu veya işlem başarısız ise None
        """
        try:
            match_id = str(match_id)
            print(f"Maç ID {match_id} için CSV oluşturuluyor...")
            
            # Maç bilgisini bul
            match_path = self._find_match_path(match_id)
            if not match_path:
                logger.warning(f"Maç ID {match_id} için veri bulunamadı.")
                return None
            
            # create_csv_dataset metodunu tek bir maç için çağır
            result = self.create_csv_dataset(match_ids=[match_id], separate_by_league=False)
            
            if result:
                logger.info(f"Maç ID {match_id} için CSV başarıyla oluşturuldu: {result}")
                return result
            else:
                logger.warning(f"Maç ID {match_id} için CSV oluşturulamadı.")
                return None
                
        except Exception as e:
            logger.error(f"Maç ID {match_id} için CSV dönüştürürken hata: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def convert_league_matches_to_csv(self, league_id: Union[int, str]) -> Optional[List[str]]:
        """
        Belirli bir ligin tüm maçlarını CSV formatına dönüştürür.
        
        Args:
            league_id: Dönüştürülecek ligin ID'si
            
        Returns:
            Optional[List[str]]: Oluşturulan CSV dosyalarının yolları veya işlem başarısız ise None
        """
        try:
            league_id = str(league_id)
            print(f"Lig ID {league_id} için CSV oluşturuluyor...")
            
            # Lig dizinini bul
            matches_dir = os.path.join(self.data_dir, "matches")
            league_dir = None
            
            # Lig ID'sine göre dizini bul
            for dir_name in os.listdir(matches_dir):
                if dir_name.startswith(f"{league_id}_") or dir_name == league_id:
                    league_dir = dir_name
                    break
            
            if not league_dir:
                logger.warning(f"Lig ID {league_id} için dizin bulunamadı.")
                return None
            
            # Bu lige ait tüm maç ID'lerini topla
            match_ids = []
            league_path = os.path.join(matches_dir, league_dir)
            
            # Doğrudan lig dizinindeki CSV dosyalarını kontrol et
            for file_name in os.listdir(league_path):
                if file_name.endswith('_matches.csv') or file_name.endswith('_summary.csv'):
                    file_path = os.path.join(league_path, file_name)
                    if os.path.isfile(file_path):
                        ids_from_csv = self._extract_match_ids_from_csv(file_path)
                        if ids_from_csv:
                            match_ids.extend(ids_from_csv)
            
            # Sezon dizinlerini kontrol et
            for season_dir in os.listdir(league_path):
                season_path = os.path.join(league_path, season_dir)
                if os.path.isdir(season_path):
                    for file_name in os.listdir(season_path):
                        if file_name.endswith('_matches.csv') or file_name.endswith('_summary.csv'):
                            file_path = os.path.join(season_path, file_name)
                            ids_from_csv = self._extract_match_ids_from_csv(file_path)
                            if ids_from_csv:
                                match_ids.extend(ids_from_csv)
            
            # Tekrarlanan ID'leri temizle
            match_ids = list(set(match_ids))
            
            if not match_ids:
                logger.warning(f"Lig ID {league_id} için maç verisi bulunamadı.")
                return None
            
            print(f"Toplam {len(match_ids)} maç bulundu, CSV'ye dönüştürülüyor...")
            
            # create_csv_dataset metodunu çağır
            result = self.create_csv_dataset(match_ids=match_ids, separate_by_league=True)
            
            if result:
                if isinstance(result, list):
                    logger.info(f"Lig ID {league_id} için {len(result)} CSV dosyası başarıyla oluşturuldu.")
                else:
                    logger.info(f"Lig ID {league_id} için CSV dosyası başarıyla oluşturuldu: {result}")
                return result if isinstance(result, list) else [result]
            else:
                logger.warning(f"Lig ID {league_id} için CSV oluşturulamadı.")
                return None
                
        except Exception as e:
            logger.error(f"Lig ID {league_id} için CSV dönüştürürken hata: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def convert_all_matches_to_csv(self) -> Union[str, List[str], None]:
        """
        Tüm maçların verilerini CSV formatına dönüştürür.
        
        Returns:
            Union[str, List[str], None]: Oluşturulan CSV dosyasının/dosyalarının yolu veya işlem başarısız ise None
        """
        try:
            print(f"Tüm maçlar için CSV oluşturuluyor...")
            
            # Ligleri kontrol et
            matches_dir = os.path.join(self.data_dir, "matches")
            if not os.path.exists(matches_dir):
                logger.warning("Maç dizini bulunamadı!")
                return None
            
            # Kullanıcıya sorma - doğrudan tüm verileri işle
            # create_csv_dataset metodunu parametresiz çağırmak tüm verileri işleyecektir
            result = self.create_csv_dataset(separate_by_league=True)
            
            if result:
                if isinstance(result, list):
                    logger.info(f"Tüm maçlar için {len(result)} CSV dosyası başarıyla oluşturuldu.")
                else:
                    logger.info(f"Tüm maçlar için CSV dosyası başarıyla oluşturuldu: {result}")
                return result
            else:
                logger.warning("CSV oluşturulamadı.")
                return None
                
        except Exception as e:
            logger.error(f"Tüm maçlar için CSV dönüştürürken hata: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def generate_file_report(self, base_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Maç dosyalarının durumunu analiz eden ve rapor üreten fonksiyon.
        
        Args:
            base_path: İncelenecek dizin yolu. Eğer None ise, varsayılan match_details dizini kullanılır.
            
        Returns:
            Dict[str, Any]: Rapor sonuçlarını içeren sözlük
        """
        # Varsayılan dizini kullan
        if base_path is None:
            base_path = self.match_details_dir
        
        base_path = Path(base_path)
        print(f"Maç dosyaları analiz ediliyor: {base_path}")
        
        # Sonuçları başlat
        results = {}
        missing_files_counter = Counter()
        total_matches = 0
        matches_with_all_files = 0
        league_stats = {}
        
        # Tüm ligleri döngüyle incele
        for league_dir in tqdm(list(base_path.iterdir()), desc="Ligler işleniyor"):
            if not league_dir.is_dir():
                continue
                
            league_name = league_dir.name
            league_stats[league_name] = {
                "total_matches": 0,
                "complete_matches": 0,
                "missing_files": Counter(),
                "seasons": {}
            }
            
            # Tüm sezonları döngüyle incele
            for season_dir in league_dir.glob("season_*"):
                if not season_dir.is_dir():
                    continue
                    
                season_name = season_dir.name
                league_stats[league_name]["seasons"][season_name] = {
                    "total_matches": 0,
                    "complete_matches": 0,
                    "missing_files": Counter()
                }
                
                # Tüm maçları döngüyle incele
                for match_dir in season_dir.iterdir():
                    if not match_dir.is_dir():
                        continue
                        
                    match_id = match_dir.name
                    total_matches += 1
                    league_stats[league_name]["total_matches"] += 1
                    league_stats[league_name]["seasons"][season_name]["total_matches"] += 1
                    
                    # Gerekli dosyaları kontrol et
                    missing_files = []
                    for req_file in REQUIRED_FILES:
                        file_path = match_dir / req_file
                        if not file_path.exists():
                            missing_files.append(req_file)
                    
                    # İstatistikleri güncelle
                    if not missing_files:
                        matches_with_all_files += 1
                        league_stats[league_name]["complete_matches"] += 1
                        league_stats[league_name]["seasons"][season_name]["complete_matches"] += 1
                    else:
                        for missing_file in missing_files:
                            missing_files_counter[missing_file] += 1
                            league_stats[league_name]["missing_files"][missing_file] += 1
                            league_stats[league_name]["seasons"][season_name]["missing_files"][missing_file] += 1
        
        # Genel istatistikleri hesapla
        overall_stats = {
            "total_matches": total_matches,
            "matches_with_all_files": matches_with_all_files,
            "completion_rate": round(matches_with_all_files / total_matches * 100, 2) if total_matches > 0 else 0,
            "missing_files": dict(missing_files_counter),
        }
        
        # Her lig için tamamlanma oranını hesapla
        for league in league_stats:
            total = league_stats[league]["total_matches"]
            complete = league_stats[league]["complete_matches"]
            league_stats[league]["completion_rate"] = round(complete / total * 100, 2) if total > 0 else 0
            
            # Her sezon için tamamlanma oranını hesapla
            for season in league_stats[league]["seasons"]:
                season_total = league_stats[league]["seasons"][season]["total_matches"]
                season_complete = league_stats[league]["seasons"][season]["complete_matches"]
                league_stats[league]["seasons"][season]["completion_rate"] = round(season_complete / season_total * 100, 2) if season_total > 0 else 0
        
        # Raporu ekrana yazdır
        print("=" * 80)
        print("MAÇ DOSYALARI ANALİZ RAPORU")
        print("=" * 80)
        
        print(f"\nToplam analiz edilen maç: {overall_stats['total_matches']}")
        print(f"Tüm gerekli dosyaları olan maçlar: {overall_stats['matches_with_all_files']} ({overall_stats['completion_rate']}%)")
        
        # En sık eksik olan dosyalar
        print("\nEksik dosya dağılımı:")
        for file, count in sorted(overall_stats['missing_files'].items(), key=lambda x: x[1], reverse=True):
            percentage = round(count / overall_stats['total_matches'] * 100, 2)
            print(f"  - {file}: {count} maçta eksik ({percentage}%)")
        
        # Lig istatistikleri
        print("\nLig istatistikleri:")
        league_data = []
        for league, stats in league_stats.items():
            league_data.append({
                'Lig': league,
                'Toplam Maç': stats['total_matches'],
                'Tam Maç': stats['complete_matches'],
                'Tamamlanma Oranı': f"{stats['completion_rate']}%"
            })
        
        if league_data:
            league_df = pd.DataFrame(league_data)
            print(league_df.sort_values('Tamamlanma Oranı', ascending=False).to_string(index=False))
        
        # Detaylı istatistikleri JSON olarak dışa aktar
        json_file_path = os.path.join(self.processed_dir, 'match_files_stats.json')
        with open(json_file_path, 'w', encoding='utf-8') as f:
            json.dump({
                'league_stats': league_stats,
                'overall_stats': overall_stats
            }, f, ensure_ascii=False, indent=2)
        
        print(f"\nDetaylı istatistikler '{json_file_path}' dosyasına kaydedildi")
        
        # CSV raporu oluştur
        csv_file_path = os.path.join(self.processed_dir, 'match_files_report.csv')
        with open(csv_file_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Lig', 'Sezon', 'Toplam Maç', 'Tam Maç', 'Tamamlanma Oranı', 'Eksik Dosyalar'])
            
            for league, league_data in league_stats.items():
                for season, season_data in league_data['seasons'].items():
                    missing_str = "; ".join([f"{file}: {count}" for file, count in season_data['missing_files'].items()])
                    writer.writerow([
                        league,
                        season,
                        season_data['total_matches'],
                        season_data['complete_matches'],
                        f"{season_data['completion_rate']}%",
                        missing_str
                    ])
        
        print(f"CSV raporu '{csv_file_path}' dosyasına kaydedildi")
        
        return {
            'league_stats': league_stats,
            'overall_stats': overall_stats,
            'json_report_path': json_file_path,
            'csv_report_path': csv_file_path
        }