from src.i18n import get_i18n
"""
SofaScore API'sinden maç verilerini çeken modül.
"""

import os
import re
import datetime
import asyncio
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from typing import TYPE_CHECKING, Dict, List, Optional, Any, Tuple

from src.exceptions import ResourceNotFoundError
from src.slices import SLICE_EMPTY, SLICE_OK, Outcome

from src.client import base_url
from src.config_manager import ConfigManager
from src.season_fetcher import SeasonFetcher
from src.status import StatusClass, classify_status
# İstek fonksiyonu ve FETCH_ONLY_FINISHED fonksiyon içinde import edilir: çağrı anındaki değer okunur (testler patch eder)
from src.utils import ensure_directory
# Program sayfaları Store'a yazılır (`EntityStore.put`, plan maddesi ST-22): v3/tournaments/<lig>/seasons/<sezon>/
# schedule/<alt anahtar>.json.gz. Yazma kataloğu kendisi günceller. Paket kökü üzerinden: cephe ilk çağrıda yüklenir.
from src import store as store_api
from src.logger import get_logger

if TYPE_CHECKING:
    from src.store import Store

logger = get_logger("MatchFetcher")

SCHEDULE_KEY = "schedule"  # sezonun program sayfaları dilimi (alt anahtar: round_12, round_3_final, last_0, next_2)
_SUB_UNSAFE = re.compile(r"[^a-z0-9_.-]")

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
        self.base_url = base_url()

        # Veri dizinlerinin var olduğundan emin ol
        ensure_directory(self.data_dir)
        ensure_directory(self.matches_dir)

    def _store(self) -> "Store":
        """Veri dizininin deposu (süreçte dizin başına tek nesne); açılamazsa StoreError fırlar."""
        return store_api.open_store(self.data_dir)

    @staticmethod
    def schedule_sub(kind: str, number: Any, slug: Optional[str] = None) -> str:
        """
        Program sayfasının alt anahtarı: tur `round_<n>` ya da `round_<n>_<slug>`, olay sayfası `last_<n>` /
        `next_<n>`. Eski sürümün dosya adıyla aynı kural (`round_3_final.json` → `round_3_final`): alt anahtar
        küçük harftir (Store büyük harfi reddeder), slug'da alt anahtara giremeyen karakterler `-` olur.
        """
        if kind == "round":
            text = f"round_{number}" + (f"_{slug}" if slug else "")
        elif kind in ("last", "next"):
            text = f"{kind}_{number}"
        else:
            raise ValueError(f"kind: expected 'round', 'last' or 'next', got {kind!r}")
        return _SUB_UNSAFE.sub("-", text.lower())[:80]

    def _save_schedule_page(self, league_id: int, season_id: int, sub: str, payload: Dict[str, Any], *,
                            meta: Dict[str, Any], empty: bool = False) -> None:
        """
        Program sayfasını Store'a yazar (`EntityStore.put`): sezonun `schedule/<sub>` dilimi. meta: tur için
        {"complete": bool}, olay sayfası için {"filtered": True}. empty: maçı olmayan tur (SAVE_EMPTY_ROUNDS).
        Depolama hatası (StoreError) çağırana çıkar.
        """
        outcome = Outcome(SLICE_EMPTY if empty else SLICE_OK, payload, meta=meta)
        ref = store_api.Ref.season(int(league_id), int(season_id))
        self._store().entities.put(ref, {(SCHEDULE_KEY, sub): outcome}, count_empties=False)

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
        max_round: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Fetch season schedule: week-based /rounds when available, else paginated events/last+next.
        Her sayfa çekilir çekilmez Store'a yazılır (`_save_schedule_page`).
        """
        from src.utils import create_session_async

        league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")
        season_name = self.season_fetcher.get_season_name(league_id, season_id)
        logger.info(f"{league_name}: {season_name} için maç programı çekiliyor...")

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
                results = await self._fetch_and_save_event_pages(session, league_id, season_id)

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
    ) -> List[Dict[str, Any]]:
        """Paginate events/last and events/next; dedupe by match id; save pages (Store) + return chunks."""
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
                sub = self.schedule_sub(kind, page)
                to_save = self._apply_finished_filter(page_payload)
                if to_save and to_save.get("events"):
                    self._save_schedule_page(league_id, season_id, sub, to_save, meta={"filtered": True})
                    chunk = dict(to_save)
                    chunk["round"] = f"{kind}_{page}"
                    results.append(chunk)
                elif not FETCH_ONLY_FINISHED and events:
                    self._save_schedule_page(league_id, season_id, sub, page_payload, meta={"filtered": True})
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

    def _load_cached_round(self, league_id: int, season_id: int, sub: str) -> Optional[Dict[str, Any]]:
        """
        Saklanan tur sayfasını yalnızca güncel olmaya devam ediyorsa döndürür (yük, `_complete` anahtarı olmadan).

        Bilgi dilimin katalogdaki kaydından gelir: `meta.complete` (tüm maçlar bitmiş mi) ve `fetched_at`.
        Bitmemiş maç içeren bir tur, TTL dolunca yeniden çekilir — aksi halde sonradan biten maçlar listeye hiç
        girmez. Eski sürümlerin yazdığı (yalnız bitmiş maçları süzülmüş, `_complete`'siz) tur dosyaları bir kez
        yeniden çekilir. Eski düzendeki tur dosyası da (`matches/...`) aynı kuralla kullanılır.
        """
        ref = store_api.Ref.season(int(league_id), int(season_id))
        try:
            entities = self._store().entities
            info = entities.slice(ref, SCHEDULE_KEY, sub)
            if not info.has_payload or "complete" not in info.meta:
                return None
            if not info.meta["complete"]:
                fetched = info.fetched_at
                age = datetime.datetime.now().timestamp() - fetched.timestamp() if fetched is not None else None
                if age is None or age >= self.ROUND_CACHE_TTL_SECONDS:
                    return None
            data = entities.payload(ref, SCHEDULE_KEY, sub)
        except store_api.StoreError as e:
            logger.warning(f"Stored round {sub} of season {season_id} cannot be read and is fetched again: {e}")
            return None
        return data if isinstance(data, dict) else None

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
        slug: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch and save one round (optional cup slug); the raw payload goes to the Store."""
        from src.utils import SAVE_EMPTY_ROUNDS, make_api_request_async

        url = self.build_round_events_url(league_id, season_id, round_num, slug)
        safe_slug = slug.replace("/", "-").replace("\\", "-") if slug else None
        sub = self.schedule_sub("round", round_num, safe_slug)
        league_name = self.config_manager.get_leagues().get(league_id, f"Bilinmeyen Lig {league_id}")

        cached = self._load_cached_round(league_id, season_id, sub)
        if cached is not None:
            return self._round_result(cached, round_num)

        try:
            async with semaphore:
                data = await make_api_request_async(session, url)

            if self._is_empty_round_data(data):
                logger.debug(f"{league_name}: Tur {round_num} için maç bulunamadı, atlanıyor...")
                if SAVE_EMPTY_ROUNDS and data:
                    self._save_schedule_page(league_id, season_id, sub, data, meta={"complete": False}, empty=True)
                return None

            self._save_schedule_page(league_id, season_id, sub, data,
                                     meta={"complete": self._round_is_complete(data)})
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
        """Paralel istekler için senkron wrapper. Sayfalar çekildikçe Store'a yazılır ve kataloğa girer."""
        logger.info(get_i18n().t('fetching_data_up_to_max_rounds', max_round=max_round))
        return asyncio.run(self.fetch_all_rounds_async(league_id, season_id, max_round=max_round))

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

    def _report_season(self, league_id: int, season_id: int, results: List[Dict[str, Any]]) -> None:
        """
        Çekilen sezonun özetini günlüğe yazar. Sezon özeti dosyaları (`<sezon>_summary.json` ve `_summary.csv`)
        artık yazılmaz (plan maddesi ST-22, karar S4): maç listeleri, gösterge paneli ve dışa aktarma veri
        klasörünün kataloğunu okur; tablo isteyen CSV dışa aktarmasını kullanır. Eski klasörlerdeki özet
        dosyaları yerinde kalır ve sezonun tek kaynağı olduklarında katalog onları okumaya devam eder.
        """
        round_counts: Dict[Any, int] = {}
        for result in results:
            round_num = result.get("round")
            if round_num is not None:
                if round_num in round_counts:
                    logger.warning(f"League {league_id}, season {season_id}: round {round_num} was fetched more than once")
                round_counts[round_num] = round_counts.get(round_num, 0) + 1
        matches = sum(len(result.get("events") or []) for result in results)
        logger.info(f"League {league_id}, season {season_id}: {len(results)} schedule pages, {matches} matches listed")

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

                return False

            self._report_season(league_id, season_id, results)

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
