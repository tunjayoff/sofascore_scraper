"""
SofaScore API'sinden maç verilerini çeken modül.
"""

import os
import json
import csv
import logging
import datetime
import asyncio
import aiohttp
import aiohttp
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path

from src.exceptions import ResourceNotFoundError

from src.config_manager import ConfigManager
from src.season_fetcher import SeasonFetcher
from src.utils import make_api_request, make_api_request_async, get_request_headers, ensure_directory, MAX_CONCURRENT, FETCH_ONLY_FINISHED, SAVE_EMPTY_ROUNDS
from src.logger import get_logger

logger = get_logger("MatchFetcher")

class MatchFetcher:
    """SofaScore API'sinden maç verilerini çeken ve yöneten sınıf."""
    
    def __init__(self, config_manager: ConfigManager, season_fetcher: SeasonFetcher, data_dir: str = "data"):
        """
        MatchFetcher sınıfını başlatır.
        
        Args:
            config_manager: Lig yapılandırmalarını yöneten ConfigManager örneği
            season_fetcher: Sezon verilerini yöneten SeasonFetcher örneği
            data_dir: Verilerin kaydedileceği ana dizin
        """
        self.config_manager = config_manager
        self.season_fetcher = season_fetcher
        self.data_dir = data_dir
        self.matches_dir = os.path.join(data_dir, "matches")
        self.base_url = "https://www.sofascore.com/api/v1"
        
        # Veri dizinlerinin var olduğundan emin ol
        ensure_directory(self.data_dir)
        ensure_directory(self.matches_dir)
    
    def _filter_finished_matches(self, data: Dict[str, Any]) -> Tuple[Dict[str, Any], int, int]:
        """
        Veri setinden sadece bitmiş maçları filtreler.
        
        Args:
            data: API'den alınan orijinal veri
            
        Returns:
            Tuple[Dict[str, Any], int, int]: 
                - Sadece bitmiş maçları içeren filtrelenmiş veri
                - Toplam maç sayısı
                - Bitmiş maç sayısı
        """
        if not data or "events" not in data:
            return data, 0, 0
            
        total_events = len(data.get("events", []))
        
        # Bitmiş maçları filtrele
        finished_events = [
            event for event in data.get("events", [])
            if (event.get("status", {}).get("description") == "Ended" and 
                event.get("status", {}).get("type") == "finished")
        ]
        
        # Orijinal veriyi bozmadan yeni obje oluştur
        filtered_data = data.copy()
        filtered_data["events"] = finished_events
        
        # Ensure round information is preserved
        if "roundInfo" in data and "round" in data.get("roundInfo", {}):
            filtered_data["round"] = data["roundInfo"]["round"]
        
        return filtered_data, total_events, len(finished_events)
    
    def _is_empty_round_data(self, data: Optional[Dict[str, Any]]) -> bool:
        """
        Hafta verisinin boş olup olmadığını kontrol eder.
        
        Args:
            data: API'den alınan veri
            
        Returns:
            bool: Veri boşsa True, değilse False
        """
        if not data:
            return True
            
        events = data.get("events", [])
        
        # 1. Hiç events yoksa
        # 2. Events boş ise ve başka sayfa yoksa
        if not events and not data.get("hasNextPage", False):
            return True
            
        return False
    
    def fetch_matches_for_round(self, league_id: int, season_id: int, round_number: int) -> Optional[Dict[str, Any]]:
        """
        Belirli bir lig, sezon ve hafta için maç verilerini çeker.
        
        Args:
            league_id: Lig ID'si
            season_id: Sezon ID'si
            round_number: Hafta numarası
        
        Returns:
            Optional[Dict[str, Any]]: Maç verileri veya bulunamazsa None
        """
        league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")
        season_name = self.season_fetcher.get_season_name(league_id, season_id)
        
        logger.info(f"{league_name}: {season_name}, Hafta {round_number} maçları çekiliyor...")
        
        # URL formatını düzelt
        url = f"{self.base_url}/unique-tournament/{league_id}/season/{season_id}/events/round/{round_number}"
        data = make_api_request(url)
        
        if self._is_empty_round_data(data):
            logger.warning(f"{league_name}: {season_name}, Hafta {round_number} için maç bulunamadı veya boş veri döndü")
            return None
        
        # Veriyi filtrele ve kaydet
        filtered_data, total_events, finished_count = self._filter_finished_matches(data)
        self._save_matches_data(league_id, season_id, round_number, filtered_data, total_events, finished_count)
        
        logger.info(f"{league_name}: {season_name}, Hafta {round_number} için {finished_count} bitmiş maç bulundu")
        
        return filtered_data
    
    async def _fetch_round_async(self, session, league_id, season_id, round_number):
        """Bir haftanın maçlarını asenkron olarak çeker."""
        league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")
        season_name = self.season_fetcher.get_season_name(league_id, season_id)
        logger.info(f"{league_name}: {season_name}, Hafta {round_number} maçları asenkron olarak çekiliyor...")
        
        # URL formatını düzelt
        url = f"{self.base_url}/unique-tournament/{league_id}/season/{season_id}/events/round/{round_number}"
        
        try:
            data = await make_api_request_async(session, url)
            
            if self._is_empty_round_data(data):
                logger.warning(f"{league_name}: {season_name}, Hafta {round_number} için maç bulunamadı veya boş veri döndü")
                return None
            
            # Veriyi filtrele ve kaydet
            filtered_data, total_events, finished_count = self._filter_finished_matches(data)
            self._save_matches_data(league_id, season_id, round_number, filtered_data, total_events, finished_count)
            
            logger.info(f"{league_name}: {season_name}, Hafta {round_number} için {finished_count} bitmiş maç bulundu")
            return filtered_data
        except Exception as e:
            logger.error(f"Hafta {round_number} için asenkron çekme hatası: {str(e)}")
        
        return None
    
    async def fetch_all_rounds_async(
        self, league_id: int, season_id: int, output_dir: str
    ) -> List[Dict[str, Any]]:
        """
        Asenkron olarak bir sezonun tüm turlarındaki maçları çeker.

        Args:
            league_id: Lig kimliği
            season_id: Sezon kimliği
            output_dir: Maç verilerinin kaydedileceği dizin

        Returns:
            Tüm turların maç verilerini içeren liste
        """
        from src.utils import MAX_CONCURRENT, create_session_async
        from src.utils import MAX_CONCURRENT, create_session_async

        league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")
        season_name = self.season_fetcher.get_season_name(league_id, season_id)
        logger.info(f"{league_name}: {season_name} için tüm turlar asenkron çekiliyor...")

        # Maksimum tur sayısı - genellikle bir sezonda bu kadar tur olmaz
        # ancak tüm olası turları taramak için bu değeri kullanıyoruz
        max_rounds = 40  # Mantıklı bir sayıya düşürüldü - tüm turları dolaşmak zaman alıyor
        
        os.makedirs(output_dir, exist_ok=True)
        
        # curl_cffi session oluştur
        async with create_session_async() as session:
            # Eşzamanlı istek sayısını çevre değişkeninden al
            semaphore_limit = MAX_CONCURRENT  # Paralel istek sayısı
            logger.info(f"{league_name}: {season_name} için eşzamanlı istek limiti: {semaphore_limit}")
            
            # Her async task için bir liste oluştur
            tasks = []
            for round_num in range(1, max_rounds + 1):
                # Semaphore'u doğrudan geçiriyoruz
                task = asyncio.create_task(
                    self._fetch_and_save_round(
                        semaphore_limit, session, league_id, season_id, round_num, output_dir
                    )
                )
                tasks.append(task)
            
            # Rich Progress kullanarak işlem takibi
            progress_desc = f"[bold cyan]{league_name}[/]: [yellow]{season_name}[/] için turlar çekiliyor"
            results = []
            
            try:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    TimeRemainingColumn(),
                    transient=True 
                ) as progress:
                    task_id = progress.add_task(progress_desc, total=len(tasks))
                    
                    for coro in asyncio.as_completed(tasks):
                        try:
                            result = await coro
                            if result:
                                results.append(result)
                        except Exception as e:
                            logger.error(f"Task hatası: {e}")
                        finally:
                            progress.advance(task_id)

            except Exception as e:
                logger.error(f"Async işlem hatası: {e}")
            
            # Boş olmayan sonuçları filtrele
            valid_results = [r for r in results if r is not None]
            logger.info(f"{league_name}: {season_name} için toplam {len(valid_results)}/{len(tasks)} tur başarıyla çekildi")
            return valid_results
    
    async def _fetch_and_save_round(
        self,
        semaphore_limit: int,
        session: Any,
        league_id: int,
        season_id: int,
        round_num: int,
        output_dir: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Belirli bir turdaki maçları çeker ve kaydeder.

        Args:
            semaphore_limit: Eşzamanlı istekleri sınırlamak için semaphore limiti
            session: HTTP oturumu
            league_id: Lig kimliği
            season_id: Sezon kimliği
            round_num: Tur numarası
            output_dir: Maç verilerinin kaydedileceği dizin

        Returns:
            Tur verileri veya None (hata durumunda)
        """
        from src.utils import FETCH_ONLY_FINISHED, SAVE_EMPTY_ROUNDS
        
        # URL formatını düzelt: tournament/matches -> unique-tournament/events
        url = f"/unique-tournament/{league_id}/season/{season_id}/events/round/{round_num}"
        file_path = os.path.join(output_dir, f"round_{round_num}.json")
        
        # Dosya zaten varsa ve tekrar kontrol edilmesi istenmiyorsa, onu okuyup döndür
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Yüklenen veri boş mu kontrol et
                    if self._is_empty_round_data(data):
                        logger.debug(f"Tur {round_num} için yerel dosyada veri bulunamadı")
                        return None
                    return data
            except json.JSONDecodeError:
                logger.warning(f"Bozuk JSON dosyası: {file_path}, yeniden çekiliyor...")
                # Dosya bozuksa, silip yeniden çekelim
                os.remove(file_path)
        
        # Semaphore kullanmadan doğrudan işlemi gerçekleştir
        try:
            from src.utils import make_api_request_async
            data = await make_api_request_async(session, url)
            
            # Boş veri kontrolü
            if self._is_empty_round_data(data):
                league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")
                logger.debug(f"{league_name}: Tur {round_num} için maç bulunamadı, atlanıyor...")
                
                # Boş tur verilerinin kaydedilip kaydedilmeyeceğini kontrol et
                if SAVE_EMPTY_ROUNDS and data:
                    logger.info(f"{league_name}: Boş tur {round_num} kaydediliyor (SAVE_EMPTY_ROUNDS=true)")
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                
                return None
            
            # Veriyi filtrele - asenkron modda
            filtered_data, total_events, finished_count = self._filter_finished_matches(data)
            
            # Sadece bitmiş maçları mı yoksa tüm verileri mi kaydedecek?
            data_to_save = filtered_data if FETCH_ONLY_FINISHED else data
            
            # Hiç bitmiş maç yoksa durumu kaydet
            if finished_count == 0:
                league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")
                logger.info(f"{league_name}: Tur {round_num} için bitmiş maç yok (Toplam: {total_events})")
                
                # Sadece bitmiş maçlar isteniyorsa ve hiç bitmiş maç yoksa, sadece log kaydı tutalım
                if FETCH_ONLY_FINISHED:
                    if SAVE_EMPTY_ROUNDS:
                        # Boş sonuç verileri kaydet
                        with open(file_path, "w", encoding="utf-8") as f:
                            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
                        logger.debug(f"Boş filtrelenmiş tur {round_num} kaydedildi (SAVE_EMPTY_ROUNDS=true)")
                    # Bitmiş maç olmadığında None döndür
                    return None 
                else:
                    # Tüm maçları kaydet (bitmiş olmasalar bile)
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(data_to_save, f, ensure_ascii=False, indent=2)
                    return data_to_save
            
            # Bitmiş maçlar varsa onları kaydet
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
            
            # Sadece bitmiş maçlar isteniyorsa, filtrelenmiş veriyi döndür
            # Aksi takdirde orijinal veriyi döndür
            return data_to_save
            
        except ResourceNotFoundError:
            league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")
            # Bu bir hata değil, muhtemelen sezonun bittiği anlamına gelir
            logger.debug(f"{league_name}: Tur {round_num} bulunamadı (Sezon sonuna ulaşılmış olabilir)")
            return None
            
        except Exception as e:
            league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")
            logger.error(f"{league_name}: Tur {round_num} çekilirken hata: {str(e)}")
            return None
    
    # Senkron wrapper
    def fetch_all_rounds_parallel(self, league_id, season_id, max_round=50):
        """Paralel istekler için senkron wrapper."""
        # Sezon dizinini oluştur
        league_name_safe = self.config_manager.get_leagues().get(league_id, f"League_{league_id}").replace(' ', '_')
        season_name_safe = self.season_fetcher.get_season_name(league_id, season_id).replace(' ', '_').replace('/', '_')
        output_dir = os.path.join(self.matches_dir, f"{league_id}_{league_name_safe}", f"{season_id}_{season_name_safe}")
        ensure_directory(output_dir)
        
        # max_round parametresini artık doğrudan burada kullanıyoruz
        logger.info(f"Sezon için {max_round} haftaya kadar veri çekiliyor...")
        
        return asyncio.run(self.fetch_all_rounds_async(league_id, season_id, output_dir))
    
    def fetch_all_rounds_for_season(self, league_id: int, season_id: int, max_round: int = 50) -> List[Dict[str, Any]]:
        """
        Belirli bir lig ve sezon için tüm haftaların maç verilerini çeker.
        
        Args:
            league_id: Lig ID'si
            season_id: Sezon ID'si
            max_round: Maksimum hafta sayısı (varsayılan: 50)
        
        Returns:
            List[Dict[str, Any]]: Her hafta için özet bilgi içeren liste
        """
        # Paralel versiyonu çağır
        return self.fetch_all_rounds_parallel(league_id, season_id, max_round)
    
    def fetch_all_leagues_current_season(self) -> Dict[int, List[Dict[str, Any]]]:
        """
        Tüm ligler için güncel sezonun maç verilerini çeker.
        
        Returns:
            Dict[int, List[Dict[str, Any]]]: Lig ID'si ve hafta sonuçları içeren sözlük
        """
        leagues = self.config_manager.get_leagues()
        results = {}
        
        if not leagues:
            logger.warning("Yapılandırılmış lig bulunamadı. Önce ligler ekleyin.")
            return {}
        
        # İşlenecek ligleri hazırla
        leagues_to_process = list(leagues.items())
        
        # Basit bir iterator kullan, çünkü içerideki metod zaten progress bar gösteriyor
        iterator = leagues_to_process
        
        for league_id, league_name in iterator:
            # Lig adı loglarda görünecek zaten
                
            # Güncel sezon ID'sini bul
            season_id = self.season_fetcher.get_current_season_id(league_id)
            
            if not season_id:
                logger.warning(f"{league_name} için güncel sezon bulunamadı, atlanıyor.")
                continue
            
            # Bu lig ve sezon için tüm haftaları çek
            rounds_data = self.fetch_all_rounds_for_season(league_id, season_id)
            results[league_id] = rounds_data
        
        # Genel özet dosyası oluştur
        self._save_global_summary(results)
        
        return results
    
    def _save_matches_data(self, league_id: int, season_id: int, round_number: int, 
                          data: Dict[str, Any], total_events: int = 0, finished_count: int = 0) -> None:
        """
        Çekilen maç verilerini JSON ve CSV formatlarında kaydeder.
        Sadece bitmiş (finished) maçları kaydeder.
        
        Args:
            league_id: Lig ID'si
            season_id: Sezon ID'si
            round_number: Hafta numarası
            data: Filtrelenmiş maç verileri (sadece bitmiş maçlar)
            total_events: Toplam maç sayısı (istatistik için)
            finished_count: Bitmiş maç sayısı (istatistik için)
        """
        league_name = self.config_manager.get_leagues().get(league_id, "Unknown_League")
        league_name_safe = league_name.replace(' ', '_')
        # Get season name from season_fetcher
        season_name = self.season_fetcher.get_season_name(league_id, season_id)
        season_name_safe = season_name.replace(' ', '_').replace('/', '_')
        
        # Dizin adlarına ID'leri de ekle
        league_dir = os.path.join(self.matches_dir, f"{league_id}_{league_name_safe}")
        season_dir = os.path.join(league_dir, f"{season_id}_{season_name_safe}")
        
        ensure_directory(league_dir)
        ensure_directory(season_dir)
        
        # İstatistikleri göster (eğer dışarıdan verilmemişse hesapla)
        if total_events == 0 and "events" in data:
            # Bu durumda data zaten filtrelenmiş olabilir
            finished_count = len(data.get("events", []))
            logger.info(f"{league_name}: Hafta {round_number} - {finished_count} bitmiş maç kaydediliyor.")
        elif total_events > finished_count:
                logger.info(f"{league_name}: Hafta {round_number} - Toplam {total_events} maçtan {finished_count} bitmiş maç kaydediliyor.")
        
        # JSON dosyası olarak kaydet (tam veri)
        json_file = os.path.join(season_dir, f"round_{round_number}_full.json")
        try:
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"JSON dosyası kaydedilirken hata: {str(e)}")
        
        # CSV dosyası olarak kaydet (basitleştirilmiş veri)
        csv_file = os.path.join(season_dir, f"round_{round_number}_matches.csv")
        
        try:
            matches = []
            if "events" in data:
                for event in data["events"]:
                    match_data = {
                        "match_id": event.get("id"),
                        "home_team": event.get("homeTeam", {}).get("name", "Unknown"),
                        "away_team": event.get("awayTeam", {}).get("name", "Unknown"),
                        "home_team_id": event.get("homeTeam", {}).get("id"),
                        "away_team_id": event.get("awayTeam", {}).get("id"),
                        "home_score": event.get("homeScore", {}).get("current"),
                        "away_score": event.get("awayScore", {}).get("current"),
                        "round": event.get("roundInfo", {}).get("round"),
                        "status": event.get("status", {}).get("description"),
                        "status_type": event.get("status", {}).get("type"),  # status type'ı da ekledik
                        "start_timestamp": event.get("startTimestamp"),
                        "start_time": datetime.datetime.fromtimestamp(event.get("startTimestamp", 0)).strftime('%Y-%m-%d %H:%M:%S') if event.get("startTimestamp") else None,
                        "slug": event.get("slug")
                    }
                    matches.append(match_data)
            
            if matches:
                with open(csv_file, 'w', encoding='utf-8', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=matches[0].keys())
                    writer.writeheader()
                    writer.writerows(matches)
            else:
                logger.warning(f"{league_name}: Hafta {round_number} - Kaydedilecek bitmiş maç bulunamadı.")
        except Exception as e:
            logger.error(f"CSV dosyası kaydedilirken hata: {str(e)}")
    
    def _save_season_summary(self, league_id: int, season_id: int, results: List[Dict[str, Any]]) -> None:
        """
        Bir sezon için özet bilgileri CSV dosyası olarak kaydeder.
        """
        if not results:
            return
        
        # Tekrarlanan hafta kontrolü
        round_counts = {}
        for result in results:
            round_num = result.get("round")
            if round_num is not None:
                if round_num in round_counts:
                    logger.warning(f"Lig {league_id}, Sezon {season_id}: Hafta {round_num} birden fazla kez çekilmiş.")
                round_counts[round_num] = round_counts.get(round_num, 0) + 1
        
        league_name = self.config_manager.get_leagues().get(league_id, "Unknown_League")
        league_name_safe = league_name.replace(' ', '_')
        season_name = self.season_fetcher.get_season_name(league_id, season_id)
        season_name_safe = season_name.replace(' ', '_').replace('/', '_')

        # Dizin yapısı oluştur - ID'leri de ekle
        league_dir = os.path.join(self.matches_dir, f"{league_id}_{league_name_safe}")
        ensure_directory(league_dir)
        
        # 1. Önce JSON olarak tüm veriyi kaydedelim (sorunsuz bir yedek olarak)
        json_summary_file = os.path.join(league_dir, f"{season_id}_{season_name_safe}_summary.json")
        try:
            with open(json_summary_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            logger.info(f"{league_name}: Sezon {season_id} JSON özeti kaydedildi: {json_summary_file}")
        except Exception as e:
            logger.error(f"Sezon JSON özeti kaydedilirken hata: {str(e)}")
        
        # 2. CSV için gerekli alanları çıkaralım - sonuçlar karmaşık nesne yapısına sahip olabilir
        csv_data = []
        csv_fields = ["round", "match_id", "home_team", "away_team", "home_score", "away_score", 
                      "match_date", "status", "tournament", "season"]
        
        for result in results:
            try:
                # Get the round number from the result
                round_number = result.get("round", "")
                
                # Events listesi içindeki herbir maç için basitleştirilmiş veri oluştur
                events = result.get("events", [])
                if isinstance(events, list):
                    for event in events:
                        # Temel maç bilgilerini çıkar
                        # Check multiple sources for round information
                        if not round_number:
                            # Try to get round from roundInfo in the event object
                            round_number = self._get_nested_value(event, ["roundInfo", "round"], "")
                        
                        match_data = {
                            "round": round_number,  # Use the round number we found
                            "match_id": event.get("id", ""),
                            "home_team": self._get_nested_value(event, ["homeTeam", "name"], ""),
                            "away_team": self._get_nested_value(event, ["awayTeam", "name"], ""),
                            "home_score": self._get_nested_value(event, ["homeScore", "current"], 0),
                            "away_score": self._get_nested_value(event, ["awayScore", "current"], 0),
                            "match_date": datetime.datetime.fromtimestamp(event.get("startTimestamp", 0)).strftime("%Y-%m-%d %H:%M"),
                            "status": self._get_nested_value(event, ["status", "description"], ""),
                            "tournament": self._get_nested_value(event, ["tournament", "name"], ""),
                            "season": self._get_nested_value(event, ["season", "name"], "")
                        }
                        csv_data.append(match_data)
                else:
                    logger.warning(f"Beklenmedik veri formatı: events bir liste değil")
            except Exception as e:
                logger.error(f"Maç verisi işlenirken hata: {str(e)}")
        
        # Özet CSV dosyası
        csv_summary_file = os.path.join(league_dir, f"{season_id}_{season_name_safe}_summary.csv")
        
        try:
            if csv_data:
                with open(csv_summary_file, 'w', encoding='utf-8', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=csv_fields)
                    writer.writeheader()
                    writer.writerows(csv_data)
            
                logger.info(f"{league_name}: Sezon {season_id} CSV özeti kaydedildi: {csv_summary_file}")
            else:
                logger.warning(f"{league_name}: Sezon {season_id} için CSV özeti oluşturulamadı - veri bulunamadı")
        except Exception as e:
            logger.error(f"Sezon CSV özeti kaydedilirken hata: {str(e)}")
    
    def _get_nested_value(self, data, keys, default=None):
        """Nested dict/json yapılardan güvenli bir şekilde değer çekmek için yardımcı method"""
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current
    
    def _save_global_summary(self, results: Dict[int, List[Dict[str, Any]]]) -> None:
        """
        Tüm ligler için özet bilgileri CSV dosyası olarak kaydeder.
        
        Args:
            results: Lig ID'si ve hafta sonuçları içeren sözlük
        """
        if not results:
            return
        
        # 1. Önce JSON olarak tüm veriyi kaydedelim (sorunsuz bir yedek olarak)
        json_summary_file = os.path.join(self.data_dir, "fetch_summary.json")
        try:
            with open(json_summary_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            logger.info(f"Genel JSON özeti kaydedildi: {json_summary_file}")
        except Exception as e:
            logger.error(f"Genel JSON özeti kaydedilirken hata: {str(e)}")
        
        # 2. CSV için veri hazırla
        csv_data = []
        csv_fields = ["league_id", "league_name", "season_id", "season_name", "round", 
                      "match_id", "home_team", "away_team", "home_score", "away_score", 
                      "match_date", "status"]
        
        for league_id, league_rounds in results.items():
            league_name = self.config_manager.get_leagues().get(league_id, f"Unknown_League_{league_id}")
            for result in league_rounds:
                try:
                    # Sezon bilgilerini al
                    season_id = result.get("season_id", "")
                    season_name = result.get("season_name", "")
                    round_number = result.get("round", "")
                    
                    # Events listesi içindeki herbir maç için basitleştirilmiş veri oluştur
                    events = result.get("events", [])
                    if isinstance(events, list):
                        for event in events:
                            # Check for round information in the event object if not in the result
                            if not round_number:
                                round_number = self._get_nested_value(event, ["roundInfo", "round"], "")
                            
                            # Temel maç bilgilerini çıkar
                            match_data = {
                                "league_id": league_id,
                                "league_name": league_name,
                                "season_id": season_id,
                                "season_name": season_name,
                                "round": round_number,
                                "match_id": event.get("id", ""),
                                "home_team": self._get_nested_value(event, ["homeTeam", "name"], ""),
                                "away_team": self._get_nested_value(event, ["awayTeam", "name"], ""),
                                "home_score": self._get_nested_value(event, ["homeScore", "current"], 0),
                                "away_score": self._get_nested_value(event, ["awayScore", "current"], 0),
                                "match_date": datetime.datetime.fromtimestamp(event.get("startTimestamp", 0)).strftime("%Y-%m-%d %H:%M"),
                                "status": self._get_nested_value(event, ["status", "description"], "")
                            }
                            csv_data.append(match_data)
                    else:
                        logger.warning(f"Beklenmedik veri formatı: events bir liste değil")
                except Exception as e:
                    logger.error(f"Maç verisi işlenirken hata: {str(e)}")
        
        # Özet CSV dosyası
        csv_summary_file = os.path.join(self.data_dir, "fetch_summary.csv")
        
        try:
            if csv_data:
                with open(csv_summary_file, 'w', encoding='utf-8', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=csv_fields)
                    writer.writeheader()
                    writer.writerows(csv_data)
                
                logger.info(f"Genel CSV özeti kaydedildi: {csv_summary_file}")
            else:
                logger.warning(f"Genel CSV özeti oluşturulamadı - veri bulunamadı")
        except Exception as e:
            logger.error(f"Genel CSV özeti kaydedilirken hata: {str(e)}")

    def fetch_all_matches_for_season(self, league_id: int, season_id: int, max_round: int = 50, retry_count: int = 0) -> bool:
        """
        Belirli bir lig ve sezon için tüm maç verilerini çeker.
        
        Args:
            league_id: Lig ID'si
            season_id: Sezon ID'si
            max_round: Maksimum hafta sayısı
            retry_count: Deneme sayısı (iç kullanım için)
            
        Returns:
            bool: İşlem başarılı ise True, değilse False
        """
        try:
            # Maksimum deneme sayısı kontrolü
            if retry_count >= 2:
                logger.warning(f"Maksimum deneme sayısına ulaşıldı ({retry_count}). İşlem durduruldu.")
                return False
            
            # Lig ve sezon adını al
            league_name = self.config_manager.get_league_by_id(league_id) or f"Bilinmeyen Lig {league_id}"
            season_name = self.season_fetcher.get_season_name(league_id, season_id)
            
            logger.info(f"{league_name} - {season_name} için tüm maçlar çekiliyor...")
            
            # Tüm haftaları asenkron çek
            results = self.fetch_all_rounds_for_season(league_id, season_id, max_round)
            
            if not results:
                # Hiç sonuç bulunamadı (hepsi boş veya hata)
                logger.warning(f"{league_name} - {season_name} için maç bulunamadı")
                
                # Gelecek sezon olabilir mi kontrol et
                current_date = datetime.datetime.now()
                current_year = current_date.year
                
                # Sezon yılını çıkarmaya çalış
                season_year = None
                if season_name:
                    # 1. Direkt sezon adından çıkar (örn. "Premier League 2024/25")
                    import re
                    year_matches = re.findall(r'20\d\d', season_name)
                    if year_matches:
                        try:
                            season_year = int(year_matches[0])
                        except (ValueError, TypeError):
                            pass
                
                # Eğer sezon adından yıl çıkarılamadıysa, sezon bilgisini kontrol et
                if not season_year:
                    season_info = self.season_fetcher.get_season_info(league_id, season_id)
                    if season_info:
                        season_year_str = season_info.get("year", "")
                        if season_year_str and '/' in season_year_str:
                            # "2024/25" veya "2024/2025" formatı
                            start_year = season_year_str.split('/')[0].strip()
                            if len(start_year) == 4:
                                season_year = int(start_year)
                            elif len(start_year) == 2:
                                # 2 haneli yıl (örn. "24/25")
                                season_year = 2000 + int(start_year)
                        elif season_year_str:
                            # Tek yıl formatı: "2024"
                            try:
                                season_year = int(season_year_str)
                            except (ValueError, TypeError):
                                pass
                
                # Gelecek sezon kontrolü
                is_future_season = False
                if season_year:
                    if season_year > current_year:
                        is_future_season = True
                    # Eğer güncel yıldaysak ancak henüz sezon başlamamışsa (örn. Haziran'da futbol sezonları)
                    elif season_year == current_year and current_date.month < 8:  # Çoğu futbol ligi Ağustos'ta başlar
                        # Sezon maçlarının olup olmadığını kontrol et
                        if not results or (isinstance(results, list) and len(results) == 0):
                            is_future_season = True
                
                if is_future_season:
                    logger.warning(f"{league_name} - {season_year} henüz başlamamış veya maç yok. Alternatif sezon deneniyor...")
                    
                    # Bir önceki sezonu deneyelim
                    seasons = self.season_fetcher.get_seasons_for_league(league_id)
                    
                    # Sezonları yıla göre sırala
                    sorted_seasons = sorted(
                        seasons,
                        key=lambda s: self.season_fetcher._get_sortable_year_value(s.get("year", "0")),
                        reverse=True  # En yeni ilk
                    )
                    
                    # Şu anki sezonun indeksini bul
                    current_index = -1
                    for i, s in enumerate(sorted_seasons):
                        if s.get("id") == season_id:
                            current_index = i
                            break
                    
                    # Bir önceki sezonu kullan
                    if current_index >= 0 and current_index + 1 < len(sorted_seasons):
                        prev_season = sorted_seasons[current_index + 1]
                        prev_season_id = prev_season.get("id")
                        prev_season_name = prev_season.get("name", "")
                        
                        logger.info(f"Alternatif sezon deneniyor: {prev_season_name} (ID: {prev_season_id})")
                        return self.fetch_all_matches_for_season(league_id, prev_season_id, max_round, retry_count + 1)
                    else:
                        logger.warning(f"Alternatif sezon bulunamadı.")
                elif results is not None and len(results) == 0:
                    # Bu durumda sezon mevcut ancak hiç bitmiş maç yok
                    # Lig tatilde veya maçlar henüz başlamamış olabilir
                    logger.warning(f"{league_name} - {season_name} için hiç bitmiş maç bulunamadı")
                    
                    # Sezon özetini yine de oluştur
                    self._save_season_summary(league_id, season_id, [])
                
                return False
            
            # Sezon özeti oluştur
            self._save_season_summary(league_id, season_id, results)
            
            logger.info(f"{league_name} - {season_name} için {len(results)} hafta verisi çekildi")
            return True
            
        except Exception as e:
            error_message = str(e)
            logger.error(f"Sezon maçları çekilirken hata: {error_message}")
            # 404 Not Found hatası normaldir, sadece uyarı olarak logla
            if "404" in error_message or "Not Found" in error_message:
                logger.warning(f"{league_name} - {season_name} için veri bulunamadı: {error_message}")
            else:
                # Daha fazla debug bilgisi
                import traceback
                logger.error(f"Ayrıntılı hata: {traceback.format_exc()}")
            return False

    def _infer_rounds_from_dates(self, matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Maç tarihlerine göre tur numaralarını tahmin eder.
        Aynı hafta içinde oynanan maçlar genellikle aynı tura aittir.
        
        Args:
            matches: Maç verileri listesi
            
        Returns:
            List[Dict[str, Any]]: Tur numaraları eklenmiş maç verileri listesi
        """
        if not matches:
            return matches
        
        # Maçları tarihlerine göre grupla
        date_groups = {}
        for match in matches:
            # Tarih bilgisini al
            timestamp = match.get("startTimestamp", 0)
            if timestamp:
                date_str = datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
                if date_str not in date_groups:
                    date_groups[date_str] = []
                date_groups[date_str].append(match)
        
        # Tarihleri sırala
        sorted_dates = sorted(date_groups.keys())
        
        # Her 3 günlük grubu bir tur olarak kabul et (Premier League için genellikle Cuma-Pazar arası)
        current_round = 1
        current_group_start = None
        date_to_round = {}
        
        for date_str in sorted_dates:
            if current_group_start is None:
                current_group_start = date_str
                date_to_round[date_str] = current_round
            else:
                # Tarih farkını hesapla
                date1 = datetime.datetime.strptime(current_group_start, "%Y-%m-%d")
                date2 = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                days_diff = (date2 - date1).days
                
                # 3 günden fazla fark varsa yeni bir tur başlat
                if days_diff > 3:
                    current_round += 1
                    current_group_start = date_str
                
                date_to_round[date_str] = current_round
        
        # Maçlara tur numaralarını ekle
        for match in matches:
            timestamp = match.get("startTimestamp", 0)
            if timestamp:
                date_str = datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
                round_num = date_to_round.get(date_str, "")
                
                # Eğer maçta roundInfo yoksa veya round değeri boşsa, tahmin edilen değeri kullan
                if not match.get("roundInfo", {}).get("round"):
                    if "roundInfo" not in match:
                        match["roundInfo"] = {}
                    match["roundInfo"]["round"] = str(round_num)
        
        return matches

    def fetch_matches_for_season(self, league_id: int, season_id: int = None) -> bool:
        """
        Belirli bir lig ve sezon için maç verilerini çeker.
        Eğer sezon ID'si belirtilmezse, güncel sezon kullanılır.
        
        Args:
            league_id: Lig ID'si
            season_id: Sezon ID'si (Belirtilmezse güncel sezon kullanılır)
            
        Returns:
            bool: İşlem başarılı ise True, değilse False
        """
        try:
            if not season_id:
                # Sezon ID belirtilmezse, güncel sezon ID'sini al
                season_id = self.season_fetcher.get_current_season_id(league_id)
                if not season_id:
                    logger.error(f"Lig ID {league_id} için güncel sezon ID bulunamadı!")
                    return False
                
            logger.info(f"Sezon {season_id} için maçlar çekiliyor (Lig ID: {league_id})...")
            
            # Önce yerel dosyalara bakalım
            league_name = self.config_manager.get_league_by_id(league_id) or f"Unknown_League_{league_id}"
            league_dir = f"{league_id}_{league_name.replace(' ', '_')}"
            season_name = self.season_fetcher.get_season_name(league_id, season_id)
            season_dir = f"{season_id}_{season_name.replace(' ', '_')}"
            matches_dir = os.path.join(self.config_manager.get_data_dir(), "matches", league_dir, season_dir)
            
            # Sezon dizini varsa, dosya sayısını kontrol et
            if os.path.exists(matches_dir):
                files = [f for f in os.listdir(matches_dir) if os.path.isfile(os.path.join(matches_dir, f))]
                logger.info(f"Sezon dizininde {len(files)} dosya bulundu: {matches_dir}")
                
                # Özet dosyası var mı kontrol et
                summary_json_path = os.path.join(self.config_manager.get_data_dir(), "matches", league_dir, f"{season_id}_{season_name.replace(' ', '_')}_summary.json")
                summary_csv_path = os.path.join(self.config_manager.get_data_dir(), "matches", league_dir, f"{season_id}_{season_name.replace(' ', '_')}_summary.csv")
                
                if os.path.exists(summary_json_path) and os.path.exists(summary_csv_path):
                    logger.info(f"Özet dosyaları mevcut: {summary_json_path}, {summary_csv_path}")
                    return True
                elif len(files) > 0:
                    # Özet dosyası yoksa ama round dosyaları varsa, özet dosyası oluştur
                    results = []
                    # Tüm round dosyalarını oku
                    for file in files:
                        if file.startswith(f"{season_id}_") and file.endswith(".json") and "round" in file:
                            file_path = os.path.join(matches_dir, file)
                            try:
                                with open(file_path, 'r', encoding='utf-8') as f:
                                    try:
                                        data = json.load(f)
                                        results.append(data)
                                    except json.JSONDecodeError:
                                        logger.error(f"JSON okuma hatası: {file_path}")
                            except Exception as e:
                                logger.error(f"Dosya okuma hatası: {file_path} - {str(e)}")
                    
                    if results:
                        # Sezon özeti oluştur
                        self._save_season_summary(league_id, season_id, results)
                        logger.info(f"Özet dosyaları oluşturuldu: {summary_json_path}, {summary_csv_path}")
                        return True
            
            # Yerel dosya bulunamadı veya özet dosyası yoktu, maçları çekmeyi dene
            logger.info(f"Yerel dosya bulunamadı, maçları çekmeyi deniyorum...")
                
            # Maçları çek
            success = self.fetch_all_matches_for_season(league_id, season_id)
            return success
            
        except Exception as e:
            error_message = str(e)
            logger.error(f"Maç verileri çekilirken hata: {error_message}")
            import traceback
            logger.error(f"Ayrıntılı hata: {traceback.format_exc()}")
            return False
