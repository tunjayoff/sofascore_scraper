from src.i18n import get_i18n
"""
SofaScore API'sinden maç verilerini çeken modül.
"""

import os
import json
import csv
import io
import datetime
import asyncio
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from typing import Dict, List, Optional, Any, Tuple

from src.exceptions import ResourceNotFoundError

from src.config_manager import ConfigManager
from src.season_fetcher import SeasonFetcher
from src.status import StatusClass, classify_status
# İstek fonksiyonu ve FETCH_ONLY_FINISHED fonksiyon içinde import edilir: çağrı anındaki değer okunur (testler patch eder)
from src.utils import ensure_directory
from src.fsutil import atomic_write_json, atomic_write_text
from src.logger import get_logger
from src.paths import matches_season_dir, summary_paths

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

    def _format_timestamp_for_terminal(self, timestamp: int, default_format: str = "%Y-%m-%d %H:%M:%S") -> str:
        """Terminal çıktısı için timestamp'i yapılandırılmış formata çevirir."""
        if not timestamp:
            return ""
        dt = datetime.datetime.fromtimestamp(timestamp)
        date_format = self.config_manager.get_date_format()
        try:
            return dt.strftime(date_format)
        except ValueError:
            logger.warning(f"Geçersiz DATE_FORMAT '{date_format}'. Varsayılan format kullanılacak.")
            return dt.strftime(default_format)

    @staticmethod
    def _is_finished_event(event: Dict[str, Any]) -> bool:
        """SofaScore type finished: oynanıp biten maçlar ve hükmen/çekilme (src/status.py)."""
        return classify_status(event) in (StatusClass.COMPLETED, StatusClass.DECIDED_WITHOUT_PLAY)

    @staticmethod
    def _parse_season_start_year(season_name: str, season_info: Optional[Dict[str, Any]] = None) -> Optional[int]:
        """Parse start year from '26/27', '2024/25', or 'Premier League 26/27'."""
        import re

        texts: List[str] = []
        if season_info:
            texts.append(str(season_info.get("year") or ""))
            texts.append(str(season_info.get("name") or ""))
        texts.append(season_name or "")
        for text in texts:
            if not text:
                continue
            m = re.search(r"(20\d{2})\s*/\s*(?:20)?\d{2}", text)
            if m:
                return int(m.group(1))
            m = re.search(r"\b(\d{2})\s*/\s*(\d{2})\b", text)
            if m:
                return 2000 + int(m.group(1))
            m = re.search(r"(20\d{2})", text)
            if m:
                return int(m.group(1))
        return None

    def _previous_season(self, league_id: int, season_id: int) -> Optional[Dict[str, Any]]:
        seasons = self.season_fetcher.get_seasons_for_league(league_id) or []
        sorted_seasons = sorted(
            seasons,
            key=lambda s: self.season_fetcher._get_sortable_year_value(s.get("year", "0")),
            reverse=True,
        )
        for i, s in enumerate(sorted_seasons):
            if s.get("id") == season_id and i + 1 < len(sorted_seasons):
                return sorted_seasons[i + 1]
        # Stale / unknown id: prefer downloadable season (usually 2nd newest)
        preferred_id = self.season_fetcher.preferred_download_season_id(league_id)
        for s in sorted_seasons:
            if s.get("id") == preferred_id and preferred_id != season_id:
                return s
        if sorted_seasons and sorted_seasons[0].get("id") != season_id:
            # last resort: newest that isn't the bad id
            return sorted_seasons[0] if len(sorted_seasons) == 1 else sorted_seasons[min(1, len(sorted_seasons) - 1)]
        return None

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

        finished_events = [
            event for event in data.get("events", [])
            if self._is_finished_event(event)
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

    @staticmethod
    def build_round_events_url(
        league_id: int, season_id: int, round_num: int, slug: Optional[str] = None
    ) -> str:
        """SofaScore round events path (with optional cup slug)."""
        base = f"/unique-tournament/{league_id}/season/{season_id}/events/round/{round_num}"
        if slug:
            return f"{base}/slug/{slug}"
        return base

    @staticmethod
    def is_week_based_rounds(rounds: List[Dict[str, Any]], max_round: int = 50) -> bool:
        """
        True when /rounds looks like sequential league weeks (1..N), not cup-only IDs.
        MLS playoff listings use large round ids (e.g. 227) and are not week-based.
        """
        if not rounds:
            return False
        nums = [r.get("round") for r in rounds if isinstance(r.get("round"), int)]
        if not nums:
            return False
        return all(1 <= n <= max_round for n in nums)

    def _apply_finished_filter(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Return payload to persist based on FETCH_ONLY_FINISHED."""
        from src.utils import FETCH_ONLY_FINISHED

        if not data:
            return None
        filtered_data, total_events, finished_count = self._filter_finished_matches(data)
        if FETCH_ONLY_FINISHED:
            if finished_count == 0:
                return None
            return filtered_data
        return data if total_events else None

    async def _fetch_rounds_metadata(
        self, session: Any, league_id: int, season_id: int
    ) -> List[Dict[str, Any]]:
        """GET /rounds; empty list on 404/error."""
        from src.utils import make_api_request_async

        url = f"/unique-tournament/{league_id}/season/{season_id}/rounds"
        try:
            data = await make_api_request_async(session, url, max_retries=1)
            if not data:
                return []
            rounds = data.get("rounds") or []
            return rounds if isinstance(rounds, list) else []
        except ResourceNotFoundError:
            return []
        except Exception as e:
            logger.warning(f"Rounds metadata alınamadı ({league_id}/{season_id}): {e}")
            return []

    async def fetch_all_rounds_async(
        self,
        league_id: int,
        season_id: int,
        output_dir: str,
        max_round: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Fetch season schedule: week-based /rounds when available, else paginated events/last+next.
        """
        from src.utils import create_session_async

        league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")
        season_name = self.season_fetcher.get_season_name(league_id, season_id)
        logger.info(f"{league_name}: {season_name} için maç programı çekiliyor...")

        os.makedirs(output_dir, exist_ok=True)
        max_round = max(1, int(max_round or 50))

        async with create_session_async() as session:
            semaphore_limit = self.config_manager.get_max_concurrent()
            logger.info(f"{league_name}: {season_name} için eşzamanlı istek limiti: {semaphore_limit}")
            round_semaphore = asyncio.Semaphore(max(1, semaphore_limit))

            rounds_meta = await self._fetch_rounds_metadata(session, league_id, season_id)
            use_weeks = self.is_week_based_rounds(rounds_meta, max_round=max_round)

            results: List[Dict[str, Any]] = []
            if use_weeks:
                round_specs = []
                for r in rounds_meta:
                    rn = r.get("round")
                    if not isinstance(rn, int) or rn < 1 or rn > max_round:
                        continue
                    round_specs.append((rn, r.get("slug")))
                if not round_specs:
                    # Fallback: sequential 1..max from metadata current or full range
                    round_specs = [(n, None) for n in range(1, max_round + 1)]

                progress_desc = f"[bold cyan]{league_name}[/]: [yellow]{season_name}[/] turlar çekiliyor"
                tasks = [
                    asyncio.create_task(
                        self._fetch_and_save_round(
                            round_semaphore,
                            session,
                            league_id,
                            season_id,
                            round_num,
                            output_dir,
                            slug=slug,
                        )
                    )
                    for round_num, slug in round_specs
                ]
                try:
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                        TimeRemainingColumn(),
                        transient=True,
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

                logger.info(
                    f"{league_name}: {season_name} için {len(results)}/{len(tasks)} tur çekildi"
                )

            if not results:
                if use_weeks:
                    logger.warning(
                        f"{league_name}: {season_name} — round schedule empty, "
                        "falling back to event list (events/last + events/next)"
                    )
                else:
                    logger.info(
                        f"{league_name}: {season_name} — round schedule unavailable "
                        f"(rounds={len(rounds_meta)}), using event list"
                    )
                results = await self._fetch_and_save_event_pages(
                    session, league_id, season_id, output_dir
                )

            if not results and not use_weeks:
                # Invalid/retired season ids 404 on rounds + events — don't spray 50 round requests
                logger.warning(
                    f"{league_name}: {season_name} — no rounds/events for season {season_id}; "
                    "skipping sequential round probe"
                )

            return [r for r in results if r is not None]

    async def _fetch_and_save_event_pages(
        self,
        session: Any,
        league_id: int,
        season_id: int,
        output_dir: str,
    ) -> List[Dict[str, Any]]:
        """Paginate events/last and events/next; dedupe by match id; save pages + return chunks."""
        from src.utils import FETCH_ONLY_FINISHED, make_api_request_async

        league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")
        seen_ids: set = set()
        results: List[Dict[str, Any]] = []

        for kind in ("last", "next"):
            page = 0
            while page < 200:
                url = f"/unique-tournament/{league_id}/season/{season_id}/events/{kind}/{page}"
                try:
                    data = await make_api_request_async(session, url, max_retries=2)
                except ResourceNotFoundError:
                    break
                except Exception as e:
                    logger.error(f"{league_name}: events/{kind}/{page} hatası: {e}")
                    break

                if not data or not data.get("events"):
                    break

                events = []
                for ev in data.get("events") or []:
                    mid = ev.get("id")
                    if mid is None or mid in seen_ids:
                        continue
                    seen_ids.add(mid)
                    events.append(ev)

                page_payload = {
                    "events": events,
                    "hasNextPage": bool(data.get("hasNextPage")),
                    "source": f"{kind}/{page}",
                }
                file_path = os.path.join(output_dir, f"events_{kind}_{page}.json")
                to_save = self._apply_finished_filter(page_payload)
                if to_save and to_save.get("events"):
                    atomic_write_json(file_path, to_save)
                    chunk = dict(to_save)
                    chunk["round"] = f"{kind}_{page}"
                    results.append(chunk)
                elif not FETCH_ONLY_FINISHED and events:
                    atomic_write_json(file_path, page_payload)
                    chunk = dict(page_payload)
                    chunk["round"] = f"{kind}_{page}"
                    results.append(chunk)

                if not data.get("hasNextPage"):
                    break
                page += 1

        logger.info(
            f"{league_name}: event listesinden {len(seen_ids)} benzersiz maç, "
            f"{len(results)} sayfa kaydedildi"
        )
        return results

    # Bitmiş sayılan ve bir daha değişmeyecek durumlar (ertelenen maç yeniden planlanabilir)
    _TERMINAL_STATUS_TYPES = ("finished", "canceled", "cancelled")
    # Tamamlanmamış bir tur dosyası bu süre içinde yeniden kullanılır (aynı işte tekrar istek atmamak için)
    ROUND_CACHE_TTL_SECONDS = 6 * 3600

    @classmethod
    def _round_is_complete(cls, data: Dict[str, Any]) -> bool:
        events = data.get("events") or []
        return bool(events) and all(
            MatchFetcher._is_finished_event(ev) or (ev.get("status") or {}).get("type") in cls._TERMINAL_STATUS_TYPES
            for ev in events
        )

    def _load_cached_round(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Diskteki tur dosyasını yalnızca güncel olmaya devam ediyorsa döndürür.

        Dosya ham API yanıtını tutar (_complete: tüm maçlar bitmiş mi). Bitmemiş maç içeren
        bir tur, TTL dolunca yeniden çekilir — aksi halde sonradan biten maçlar özete hiç girmez.
        Eski sürümlerin yazdığı (yalnız bitmiş maçları süzülmüş, _complete'siz) dosyalar bir kez
        yeniden çekilir.
        """
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            logger.warning(f"Bozuk tur dosyası yeniden çekilecek: {file_path}")
            return None
        if not isinstance(data, dict) or "_complete" not in data:
            return None
        if data["_complete"]:
            return data
        age = datetime.datetime.now().timestamp() - os.path.getmtime(file_path)
        return data if age < self.ROUND_CACHE_TTL_SECONDS else None

    def _round_result(self, data: Dict[str, Any], round_num: int) -> Optional[Dict[str, Any]]:
        """Ham tur verisinden özete girecek kısmı (FETCH_ONLY_FINISHED filtresiyle) üretir."""
        if self._is_empty_round_data(data):
            return None
        result = self._apply_finished_filter({k: v for k, v in data.items() if k != "_complete"})
        if not result:
            return None
        result = dict(result)
        result["round"] = round_num
        return result

    async def _fetch_and_save_round(
        self,
        semaphore: asyncio.Semaphore,
        session: Any,
        league_id: int,
        season_id: int,
        round_num: int,
        output_dir: str,
        slug: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch and save one round (optional cup slug)."""
        from src.utils import SAVE_EMPTY_ROUNDS, make_api_request_async

        url = self.build_round_events_url(league_id, season_id, round_num, slug)
        safe_slug = slug.replace("/", "-").replace("\\", "-") if slug else None
        suffix = f"_{safe_slug}" if safe_slug else ""
        file_path = os.path.join(output_dir, f"round_{round_num}{suffix}.json")
        league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")

        cached = self._load_cached_round(file_path)
        if cached is not None:
            return self._round_result(cached, round_num)

        try:
            async with semaphore:
                data = await make_api_request_async(session, url)

            if self._is_empty_round_data(data):
                logger.debug(f"{league_name}: Tur {round_num} için maç bulunamadı, atlanıyor...")
                if SAVE_EMPTY_ROUNDS and data:
                    atomic_write_json(file_path, {**data, "_complete": False})
                return None

            atomic_write_json(file_path, {**data, "_complete": self._round_is_complete(data)})
            result = self._round_result(data, round_num)
            if result is None:
                logger.info(f"{league_name}: Tur {round_num} için bitmiş maç yok (Toplam: {len(data.get('events') or [])})")
            return result

        except ResourceNotFoundError:
            logger.debug(f"{league_name}: Tur {round_num} bulunamadı")
            return None
        except Exception as e:
            logger.error(f"{league_name}: Tur {round_num} çekilirken hata: {str(e)}")
            return None

    def fetch_all_rounds_parallel(self, league_id, season_id, max_round=50):
        """Paralel istekler için senkron wrapper."""
        output_dir = matches_season_dir(
            self.data_dir,
            league_id,
            self.config_manager.get_league_by_id(league_id),
            season_id,
            self.season_fetcher.get_season_name(league_id, season_id),
        )
        ensure_directory(output_dir)

        logger.info(get_i18n().t('fetching_data_up_to_max_rounds', max_round=max_round))
        return asyncio.run(
            self.fetch_all_rounds_async(league_id, season_id, output_dir, max_round=max_round)
        )

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
        return self.fetch_all_rounds_parallel(league_id, season_id, max_round)

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

        config_name = self.config_manager.get_league_by_id(league_id)
        league_name = config_name or "Unknown_League"
        season_name = self.season_fetcher.get_season_name(league_id, season_id)
        json_summary_file, csv_summary_file = summary_paths(
            self.data_dir, league_id, config_name, season_id, season_name
        )
        ensure_directory(os.path.dirname(json_summary_file))

        # 1. Önce JSON olarak tüm veriyi kaydedelim (sorunsuz bir yedek olarak)
        try:
            atomic_write_json(json_summary_file, results)
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
                            "match_date": datetime.datetime.fromtimestamp(event.get("startTimestamp", 0)).isoformat() if event.get("startTimestamp") else "",
                            "status": self._get_nested_value(event, ["status", "description"], ""),
                            "tournament": self._get_nested_value(event, ["tournament", "name"], ""),
                            "season": self._get_nested_value(event, ["season", "name"], "")
                        }
                        csv_data.append(match_data)
                else:
                    logger.warning("Beklenmedik veri formatı: events bir liste değil")
            except Exception as e:
                logger.error(f"Maç verisi işlenirken hata: {str(e)}")

        try:
            if csv_data:
                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=csv_fields)
                writer.writeheader()
                writer.writerows(csv_data)
                atomic_write_text(csv_summary_file, buf.getvalue())

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

    def fetch_all_matches_for_season(
        self,
        league_id: int,
        season_id: int,
        max_round: int = 50,
        retry_count: int = 0,
        allow_fallback: bool = False,
    ) -> bool:
        """
        Belirli bir lig ve sezon için tüm maç verilerini çeker.

        Args:
            league_id: Lig ID'si
            season_id: Sezon ID'si
            max_round: Maksimum hafta sayısı
            retry_count: Deneme sayısı (iç kullanım için)
            allow_fallback: Sezonda bitmiş maç yoksa önceki sezonu çek. Kullanıcı sezonu
                açıkça seçtiyse False olmalı: aksi halde başka bir sezonun programı kaydedilir,
                istenen sezonun detayları hiç çekilmez ve iş yine de "başarılı" görünür.

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

            logger.info(get_i18n().t('fetching_all_matches_for_league_season', league_name=league_name, season_name=season_name))

            # Tüm haftaları asenkron çek
            results = self.fetch_all_rounds_for_season(league_id, season_id, max_round)

            if not results:
                # Hiç bitmiş maç yok: fixtures-only upcoming (örn. PL 26/27) veya boş sezon
                logger.warning(f"{league_name} - {season_name} için bitmiş maç bulunamadı")

                season_info = self.season_fetcher.get_season_info(league_id, season_id)
                season_year = self._parse_season_start_year(season_name or "", season_info)
                current_date = datetime.datetime.now()
                current_year = current_date.year

                # Short-year seasons like 26/27 often publish full fixtures before kickoff
                is_future_or_unstarted = False
                if season_year is not None:
                    if season_year > current_year:
                        is_future_or_unstarted = True
                    elif season_year == current_year and current_date.month < 8:
                        is_future_or_unstarted = True

                # Açık seçim yoksa, bitmiş maç filtresi programı boşalttığında önceki sezonu bir kez dene
                if allow_fallback and retry_count == 0:
                    prev_season = self._previous_season(league_id, season_id)
                    if prev_season:
                        prev_season_id = prev_season.get("id")
                        prev_season_name = prev_season.get("name", "")
                        reason = (
                            f"upcoming/unstarted ({season_year})"
                            if is_future_or_unstarted
                            else "no finished matches in selected season"
                        )
                        logger.warning(
                            f"{league_name} - {season_name}: {reason}. "
                            f"Falling back to {prev_season_name} (ID: {prev_season_id})"
                        )
                        return self.fetch_all_matches_for_season(
                            league_id, prev_season_id, max_round, retry_count + 1
                        )
                    logger.warning(f"{league_name}: alternatif (önceki) sezon bulunamadı.")

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

    def fetch_matches_for_season(self, league_id: int, season_id: Optional[int] = None) -> bool:
        """
        Belirli bir lig ve sezon için maç programını çeker/günceller.
        Eğer sezon ID'si belirtilmezse, güncel sezon kullanılır.

        Tamamlanmış turlar diskten okunur (bkz. _load_cached_round); yalnızca bitmemiş maç
        içeren turlar yeniden istenir, bu yüzden bitmiş bir sezonu güncellemek ucuzdur.

        Args:
            league_id: Lig ID'si
            season_id: Sezon ID'si (Belirtilmezse güncel sezon kullanılır; yalnızca bu durumda
                bitmiş maçı olmayan sezon için önceki sezona geçilir)

        Returns:
            bool: İşlem başarılı ise True, değilse False
        """
        try:
            auto_selected = not season_id
            if auto_selected:
                season_id = self.season_fetcher.get_current_season_id(league_id)
                if not season_id:
                    logger.error(f"Lig ID {league_id} için güncel sezon ID bulunamadı!")
                    return False

            logger.info(get_i18n().t('fetching_matches_for_season', season_id=season_id, league_id=league_id))
            return self.fetch_all_matches_for_season(league_id, season_id, allow_fallback=auto_selected)

        except Exception as e:
            error_message = str(e)
            logger.error(f"Maç verileri çekilirken hata: {error_message}")
            import traceback
            logger.error(f"Ayrıntılı hata: {traceback.format_exc()}")
            return False
