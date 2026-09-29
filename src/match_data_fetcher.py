from src.i18n import get_i18n
"""
SofaScore API'sinden detaylı maç verilerini çeken modül.
"""

import os
import json
import csv
import time
import random
import re
from typing import Any, Callable, Dict, List, Optional, Union, Tuple
from pathlib import Path
import asyncio
from collections import Counter
import pandas as pd
from tqdm import tqdm

from src.config_manager import ConfigManager
from src.fsutil import atomic_write_json
from src.utils import make_api_request, ensure_directory
from src.match_fetcher import MatchFetcher

from src.logger import get_logger

logger = get_logger("MatchDataFetcher")

# Gerekli dosyaların listesini ekleyelim
REQUIRED_FILES = [
    'basic.json',
    'statistics.json',
    'team_streaks.json',
    'pregame_form.json',
    'h2h.json',
    'lineups.json',
    'incidents.json',
]

# UI / dosya tamlığı ile uyumlu alt dilimler (basic hariç)
DETAIL_SLICE_KEYS = (
    "statistics",
    "team_streaks",
    "pregame_form",
    "h2h",
    "lineups",
    "incidents",
)

# Bitmiş bir maçta bu kadar denemede de boş gelen dilim o maç için yok sayılır (ör. tenis
# maçlarında kadro/olay yok). Bir kez daha denemek geçici hataları ayırır.
UNAVAILABLE_AFTER_ATTEMPTS = 2
UNAVAILABLE_FILE = "_unavailable.json"


def _event_sport(basic: Dict[str, Any]) -> str:
    sport = ((basic.get("tournament") or {}).get("category") or {}).get("sport") or {}
    return str(sport.get("slug") or sport.get("name") or "").lower()


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
        index = getattr(self, "_match_index", None)
        if index is not None:
            return index.get(match_id)

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
                if os.path.isdir(match_path) and os.path.exists(os.path.join(match_path, "basic.json")):
                    return (league_name, season_name, match_path)

        # Check old structure as fallback
        old_match_path = os.path.join(self.match_details_dir, match_id)
        if os.path.isdir(old_match_path) and os.path.exists(os.path.join(old_match_path, "basic.json")):
            return (None, None, old_match_path)

        return None

    def _build_match_index(self) -> Dict[str, Tuple[Optional[str], Optional[str], str]]:
        """match_id → konum; bir iş boyunca her maç için dizin ağacını baştan taramamak için."""
        index: Dict[str, Tuple[Optional[str], Optional[str], str]] = {}
        if not os.path.isdir(self.match_details_dir):
            return index
        for league_name in os.listdir(self.match_details_dir):
            league_path = os.path.join(self.match_details_dir, league_name)
            if not os.path.isdir(league_path) or league_name == "processed":
                continue
            if os.path.exists(os.path.join(league_path, "basic.json")):
                index.setdefault(league_name, (None, None, league_path))  # eski düz yapı
                continue
            for season_name in os.listdir(league_path):
                season_path = os.path.join(league_path, season_name)
                if not os.path.isdir(season_path):
                    continue
                for mid in os.listdir(season_path):
                    match_path = os.path.join(season_path, mid)
                    if os.path.exists(os.path.join(match_path, "basic.json")):
                        index.setdefault(mid, (league_name, season_name, match_path))
        return index

    def begin_job_cache(self) -> None:
        """Bir iş boyunca maç konumlarını ve 'eksik mi' sonuçlarını önbelleğe al."""
        self._match_index = self._build_match_index()
        self._need_cache: Dict[str, str] = {}

    def end_job_cache(self) -> None:
        self._match_index = None
        self._need_cache = {}

    async def _fetch_match_data_async(self, session, match_id):
        try:
            from src.utils import make_api_request_async, FETCH_ONLY_FINISHED
            from src.exceptions import ResourceNotFoundError

            # Temel veriyi çek (BrowserBridge / 404 korumalı)
            basic_url = f"{self.base_url}/event/{match_id}"
            try:
                data = await make_api_request_async(session, basic_url, max_retries=2)
            except ResourceNotFoundError:
                return None

            if not data or not isinstance(data, dict):
                return None
            basic_data = data.get("event")
            if not basic_data:
                return None

            # Sadece bitmiş maçları işle (eğer FETCH_ONLY_FINISHED aktifse)
            if FETCH_ONLY_FINISHED and not MatchFetcher._is_finished_event(basic_data):
                status_desc = basic_data.get("status", {}).get("description", "")
                logger.debug(f"Maç ID {match_id} henüz bitmemiş (Durum: {status_desc}), atlanıyor.")
                return None

            match_data = {"basic": basic_data}

            # Spor türüne uygun endpoint'leri çağır (Futbol, Basketbol, Tenis)
            tasks = [
                self._fetch_endpoint_async(session, f"{self.base_url}/event/{match_id}/statistics", "statistics"),
                self._fetch_endpoint_async(session, f"{self.base_url}/event/{match_id}/team-streaks", "team_streaks"),
                self._fetch_endpoint_async(session, f"{self.base_url}/event/{match_id}/pregame-form", "pregame_form"),
                self._fetch_endpoint_async(session, f"{self.base_url}/event/{match_id}/h2h", "h2h"),
                self._fetch_endpoint_async(session, f"{self.base_url}/event/{match_id}/lineups", "lineups"),
                self._fetch_endpoint_async(session, f"{self.base_url}/event/{match_id}/incidents", "incidents"),
            ]
            if _event_sport(basic_data) == "tennis":
                tasks.append(
                    self._fetch_endpoint_async(
                        session, f"{self.base_url}/event/{match_id}/point-by-point", "point_by_point"
                    )
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                # İş iptal edildiyse yarım maçı kaydetme; iptali yukarı taşı
                if isinstance(result, BaseException) and not isinstance(result, Exception):
                    raise result
            for result in results:
                # None da yazılır: _save_match_data boş gelen dilimi sayabilsin
                if isinstance(result, tuple) and len(result) == 2:
                    match_data[result[0]] = result[1]

            # Verileri kaydet
            self._save_match_data(match_id, match_data)
            return match_data
        except Exception as e:
            logger.error(f"Maç ID {match_id} için asenkron veri çekilirken hata: {str(e)}")
            raise

    async def _fetch_endpoint_async(self, session, url, key):
        try:
            from src.utils import make_api_request_async
            from src.exceptions import ResourceNotFoundError
            data = await make_api_request_async(session, url, max_retries=1)
            if data:
                return key, data
        except ResourceNotFoundError:
            return key, None
        except Exception as e:
            logger.debug(f"{url} için asenkron istek hatası: {str(e)}")
        return key, None

    async def fetch_matches_batch_async(self, match_ids, max_concurrent=30, progress_bar=None, progress_callback=None, should_cancel=None):
        """Birden çok maç için veri çeker (circuit breaker destekli)."""
        logger.debug(f"Starting batch fetch for {len(match_ids)} matches")
        ignore_rate_limit = os.getenv("IGNORE_RATE_LIMIT", "false").lower() == "true"

        match_ids_to_process = [id for id in match_ids if self._needs_detail_fetch(str(id)) != "none"]
        skipped = len(match_ids) - len(match_ids_to_process)
        if skipped:
            logger.info(f"{skipped} maç detayları tamam, atlanıyor")
            if progress_bar:
                progress_bar.update(skipped)

        results: Dict[str, Dict[str, Any]] = {}
        status_counts: Counter = Counter()
        recent_headers: List[Dict[str, str]] = []
        consecutive_failures = 0
        consecutive_server_errors = 0
        total_failures = 0
        total_attempts = 0
        breaker_triggered = False
        cancelled = False

        batch_size = 100
        all_batches = [match_ids_to_process[i:i + batch_size] for i in range(0, len(match_ids_to_process), batch_size)]

        total_m = len(match_ids_to_process)
        if progress_callback and total_m > 0:
            progress_callback(0, total_m, f"Starting {total_m} match detail requests…")
        cumulative_done = 0

        from src.utils import create_session_async
        async with create_session_async() as session:
            sem = asyncio.Semaphore(max_concurrent)
            for batch_idx, batch in enumerate(all_batches):
                if breaker_triggered or cancelled:
                    break
                if should_cancel and should_cancel():
                    cancelled = True
                    logger.info("Parallel match fetch cancelled before batch %s", batch_idx + 1)
                    break
                logger.info(f"Processing batch {batch_idx+1}/{len(all_batches)} ({len(batch)} matches)")
                batch_status_counts: Counter = Counter()
                batch_success = 0
                batch_failed = 0

                async def fetch_one(match_id):
                    nonlocal consecutive_failures, consecutive_server_errors, total_failures, total_attempts, breaker_triggered, batch_success, batch_failed, cancelled
                    if cancelled or (should_cancel and should_cancel()):
                        cancelled = True
                        return None
                    max_retries = 3
                    for attempt in range(max_retries):
                        if cancelled or (should_cancel and should_cancel()):
                            cancelled = True
                            return None
                        if breaker_triggered:
                            # Devre kesildi: batch'te sırada bekleyen maçlar istek atmasın
                            return None
                        try:
                            async with sem:
                                if cancelled or (should_cancel and should_cancel()):
                                    cancelled = True
                                    return None
                                if breaker_triggered:
                                    return None
                                total_attempts += 1
                                need = self._needs_detail_fetch(str(match_id))
                                if need == "refill":
                                    result = await asyncio.to_thread(self.refill_missing_match_slices, str(match_id))
                                    if cancelled or (should_cancel and should_cancel()):
                                        cancelled = True
                                        return None
                                    if not (result and "basic" in result):
                                        result = await self._fetch_match_data_async(session, match_id)
                                else:
                                    result = await self._fetch_match_data_async(session, match_id)
                                if cancelled or (should_cancel and should_cancel()):
                                    cancelled = True
                                    return None
                                if result and "basic" in result:
                                    consecutive_failures = 0
                                    consecutive_server_errors = 0
                                    batch_success += 1
                                    if progress_bar:
                                        progress_bar.update(1)
                                    return result
                                status_counts["other"] += 1
                                # fetch_one görevleri bu batch bitmeden tamamlanır/iptal edilir
                                batch_status_counts["other"] += 1  # noqa: B023
                                break
                        except Exception as e:
                            err = str(e)
                            code = getattr(e, "status_code", None)
                            status_key = "other"
                            if code in (403, 404, 429):
                                status_key = str(code)
                            elif isinstance(code, int) and code >= 500:
                                status_key = "5xx"
                            elif "timeout" in err.lower():
                                status_key = "timeout"

                            status_counts[status_key] += 1
                            batch_status_counts[status_key] += 1  # noqa: B023
                            if status_key != "404":
                                total_failures += 1
                                consecutive_failures += 1
                            if status_key == "5xx":
                                consecutive_server_errors += 1
                            else:
                                consecutive_server_errors = 0

                            if status_key in ("403", "429", "5xx"):
                                recent_headers.append({"match_id": str(match_id), "error": err})
                                if len(recent_headers) > 20:
                                    recent_headers.pop(0)

                            if not ignore_rate_limit:
                                threshold_cons = self.config_manager.get_rate_limit_threshold_consecutive()
                                threshold_ratio = self.config_manager.get_rate_limit_threshold_ratio()
                                threshold_5xx = self.config_manager.get_server_error_threshold_consecutive()
                                ratio_triggered = total_attempts > 50 and (total_failures / total_attempts) >= threshold_ratio
                                if consecutive_failures >= threshold_cons or consecutive_server_errors >= threshold_5xx or ratio_triggered:
                                    breaker_triggered = True
                                    return None

                            if attempt < max_retries - 1:
                                await asyncio.sleep(1.0 * (2 ** attempt) + random.uniform(0, 1))
                                continue
                            break

                    batch_failed += 1
                    if progress_bar:
                        progress_bar.update(1)
                    return None

                batch_tasks = [asyncio.create_task(fetch_one(match_id)) for match_id in batch]
                batch_completed = 0
                notify_stride = max(1, min(20, max(total_m // 50, 1)))
                try:
                    for fut in asyncio.as_completed(batch_tasks):
                        if should_cancel and should_cancel():
                            cancelled = True
                            break
                        try:
                            match_data = await fut
                        except asyncio.CancelledError:
                            continue
                        if cancelled:
                            break
                        batch_completed += 1
                        cumulative_done += 1
                        if match_data and isinstance(match_data, dict) and "basic" in match_data:
                            match_id_res = match_data["basic"].get("id")
                            if match_id_res:
                                results[str(match_id_res)] = match_data
                        if progress_callback and total_m > 0:
                            if (
                                cumulative_done % notify_stride == 0
                                or batch_completed == len(batch)
                                or cumulative_done >= total_m
                            ):
                                progress_callback(
                                    min(cumulative_done, total_m),
                                    total_m,
                                    f"Match details {min(cumulative_done, total_m)}/{total_m} (parallel batch {batch_idx + 1}/{len(all_batches)})",
                                )
                except BaseException:
                    # FetchCancelled (iptal) veya beklenmeyen hata: kalan görevleri iptal edip bekle,
                    # oturum kapanmadan ve döngü kapatılmadan önce hiçbiri askıda kalmasın
                    pending = [t for t in batch_tasks if not t.done()]
                    for t in pending:
                        t.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    raise

                if cancelled:
                    # Best-effort: cancel leftovers and don't wait on long sleeps
                    pending = [t for t in batch_tasks if not t.done()]
                    for t in pending:
                        t.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    break

                status_text = ", ".join([f"{v}x {k}" for k, v in batch_status_counts.items()]) if batch_status_counts else "hata yok"
                logger.info(f"Batch {batch_idx+1}/{len(all_batches)}: {batch_success} başarılı, {batch_failed} başarısız ({status_text})")

                if batch_idx < len(all_batches) - 1 and not breaker_triggered:
                    await asyncio.sleep(1.0)

        if progress_callback and total_m > 0 and not cancelled:
            progress_callback(total_m, total_m, "Parallel detail batches finished")

        self.last_status_counts = dict(status_counts)
        self.rate_limit_breaker_triggered = breaker_triggered
        self.last_rate_limit_headers = recent_headers
        return results

    # Main metodunda çağırmak için senkron wrapper
    def fetch_matches_batch_parallel(self, match_ids, max_concurrent=10, progress_callback=None, should_cancel=None):
        """Paralel istekler için senkron wrapper."""
        print(f"Toplam {len(match_ids)} maç paralel olarak işleniyor...")
        progress = tqdm(total=len(match_ids), desc="Maç detayları çekiliyor")

        try:
            # asyncio.run: döngüyü kapatır, kalan görevleri iptal eder ve thread'e kapalı döngü bırakmaz
            return asyncio.run(self.fetch_matches_batch_async(
                match_ids, max_concurrent, progress, progress_callback, should_cancel
            ))
        finally:
            progress.close()


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
        self.rate_limit_breaker_triggered = False
        self.last_rate_limit_headers: List[Dict[str, str]] = []
        self.last_status_counts: Dict[str, int] = {}

        # Veri dizinlerinin var olduğundan emin ol
        ensure_directory(self.data_dir)
        ensure_directory(self.match_details_dir)
        ensure_directory(self.processed_dir)

    def _load_match_data_from_dir(self, match_dir: str, match_id: str) -> Dict[str, Any]:
        """match_details/.../match_id içinden API ile aynı birleşik sözlüğü yükler."""
        mid = str(match_id)
        result: Dict[str, Any] = {}
        full_json_path = os.path.join(match_dir, f"{mid}.json")
        try:
            if os.path.exists(full_json_path):
                with open(full_json_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            for fname in REQUIRED_FILES:
                if not fname.endswith(".json"):
                    fname = f"{fname}.json"
                component = fname[:-5]
                c_path = os.path.join(match_dir, fname)
                if os.path.exists(c_path):
                    with open(c_path, "r", encoding="utf-8") as f:
                        result[component] = json.load(f)
        except Exception as e:
            logger.warning(f"Maç {mid} dizininden yüklenirken hata: {e}")
        return result

    def _statistics_has_data(self, d: Dict[str, Any]) -> bool:
        s = d.get("statistics")
        if s is None:
            return False
        periods = s if isinstance(s, list) else (s.get("statistics") or [])
        all_periods = [p for p in periods if p and p.get("period") == "ALL"]
        if not all_periods and periods:
            all_periods = [periods[0]]
        for p in all_periods:
            for g in p.get("groups") or []:
                if (g.get("statisticsItems") or []):
                    return True
        return False

    def _has_lineups_data_dict(self, d: Dict[str, Any]) -> bool:
        L = d.get("lineups")
        if not L or not isinstance(L, dict):
            return False
        for side in ("home", "away"):
            block = L.get(side)
            if not isinstance(block, dict):
                continue
            players = block.get("players")
            if isinstance(players, list) and len(players) > 0:
                return True
        return False

    def _has_h2h_data_dict(self, d: Dict[str, Any]) -> bool:
        h = d.get("h2h")
        if not h or not isinstance(h, dict):
            return False
        td = h.get("teamDuel") or {}
        if td and any(td.get(x) is not None for x in ("homeWins", "awayWins", "draws")):
            return True
        raw = h.get("matches") or h.get("events") or td.get("matches")
        return isinstance(raw, list) and len(raw) > 0

    def _has_pregame_form_data_dict(self, d: Dict[str, Any]) -> bool:
        p = d.get("pregame_form")
        if not p or not isinstance(p, dict):
            return False

        def chk(t: Any) -> bool:
            if not t or not isinstance(t, dict):
                return False
            form = t.get("form")
            if isinstance(form, list) and len(form) > 0:
                return True
            return any(t.get(x) is not None for x in ("position", "value", "avgRating"))

        return chk(p.get("homeTeam")) or chk(p.get("awayTeam"))

    def _has_team_streaks_data_dict(self, d: Dict[str, Any]) -> bool:
        g = (d.get("team_streaks") or {}).get("general")
        return isinstance(g, list) and len(g) > 0

    def _has_incidents_data_dict(self, d: Dict[str, Any]) -> bool:
        raw = d.get("incidents")
        if raw and isinstance(raw, dict) and not isinstance(raw, list):
            raw = raw.get("incidents")
        return isinstance(raw, list) and len(raw) > 0

    def match_detail_slice_present(self, key: str, d: Dict[str, Any]) -> bool:
        """Web arayüzündeki matchDetailSlicePresent ile aynı anlam."""
        if key == "basic":
            return bool(d.get("basic"))
        if key == "statistics":
            return self._statistics_has_data(d)
        if key == "lineups":
            return self._has_lineups_data_dict(d)
        if key == "h2h":
            return self._has_h2h_data_dict(d)
        if key == "team_streaks":
            return self._has_team_streaks_data_dict(d)
        if key == "pregame_form":
            return self._has_pregame_form_data_dict(d)
        if key == "incidents":
            return self._has_incidents_data_dict(d)
        return bool(d.get(key))

    def _load_unavailable(self, match_dir: str) -> Dict[str, int]:
        try:
            with open(os.path.join(match_dir, UNAVAILABLE_FILE), "r", encoding="utf-8") as f:
                data = json.load(f)
            return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _expected_slices(self, match_dir: str) -> List[str]:
        """Beklenen dilimler: yeterince denenip hep boş gelenler hariç."""
        unavailable = self._load_unavailable(match_dir)
        return [k for k in DETAIL_SLICE_KEYS if unavailable.get(k, 0) < UNAVAILABLE_AFTER_ATTEMPTS]

    def _needs_detail_fetch(self, match_id: str) -> str:
        """
        Returns:
            'none' — beklenen tüm dilimler tamam
            'refill' — basic var, eksik dilim(ler) var
            'full' — kayıt yok veya basic yok
        """
        mid = str(match_id)
        cache = getattr(self, "_need_cache", None)
        if cache is not None and mid in cache:
            return cache[mid]
        need = self._compute_detail_need(mid)
        if cache is not None:
            cache[mid] = need
        return need

    def _compute_detail_need(self, mid: str) -> str:
        path_info = self._find_match_path(mid)
        if not path_info:
            return "full"
        _, _, match_dir = path_info
        data = self._load_match_data_from_dir(match_dir, mid)
        if not data.get("basic"):
            return "full"
        for key in self._expected_slices(match_dir):
            if not self.match_detail_slice_present(key, data):
                return "refill"
        return "none"

    def refill_missing_match_slices(self, match_id: Union[int, str]) -> Optional[Dict[str, Any]]:
        """
        Diskte basic.json olan maçta eksik API dilimlerini tamamlar (yeni maç için fetch_match_data kullanın).
        """
        mid = str(match_id)
        path_info = self._find_match_path(mid)
        if not path_info:
            return None
        _, _, match_dir = path_info
        match_data = self._load_match_data_from_dir(match_dir, mid)
        if not match_data.get("basic"):
            return None

        basic_live = self._fetch_match_basic(mid)
        if not basic_live:
            logger.warning(f"Maç {mid} refill: canlı basic alınamadı")
            return None
        if not MatchFetcher._is_finished_event(basic_live):
            status = basic_live.get("status", {})
            logger.info(f"Maç {mid} bitmemiş ({status.get('description')}/{status.get('type')}), refill atlanıyor.")
            return None

        match_data["basic"] = basic_live
        missing = [
            k for k in self._expected_slices(match_dir) if not self.match_detail_slice_present(k, match_data)
        ]
        if not missing:
            return match_data

        fetchers = {
            "statistics": self._fetch_match_statistics,
            "team_streaks": self._fetch_team_streaks,
            "pregame_form": self._fetch_pregame_form,
            "h2h": self._fetch_h2h,
            "lineups": self._fetch_lineups,
            "incidents": self._fetch_incidents,
        }
        for key in missing:
            try:
                match_data[key] = fetchers[key](mid)
            except Exception as e:
                logger.error(f"Maç {mid} refill {key} hatası: {e}")
                match_data[key] = None
        self._save_match_data(mid, match_data)
        return match_data

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

        # Sadece bitmiş maçları işle (uzatma/penaltı ile bitenler dahil)
        if not MatchFetcher._is_finished_event(basic_data):
            status = basic_data.get("status", {})
            logger.info(
                f"Maç ID {match_id} henüz bitmemiş (Durum: {status.get('description')}/{status.get('type')}), atlanıyor."
            )
            return None

        # Diğer verileri çek
        match_data = {
            "basic": basic_data,
            "statistics": None,
            "team_streaks": None,
            "pregame_form": None,
            "h2h": None,
            "lineups": None,
            "incidents": None,
        }

        # Diğer endpointleri topla
        match_data["statistics"] = self._fetch_match_statistics(match_id)
        match_data["team_streaks"] = self._fetch_team_streaks(match_id)
        match_data["pregame_form"] = self._fetch_pregame_form(match_id)
        match_data["h2h"] = self._fetch_h2h(match_id)
        match_data["lineups"] = self._fetch_lineups(match_id)
        match_data["incidents"] = self._fetch_incidents(match_id)

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
        Takımlar arası karşılaşma geçmişini çeker.

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

    def _fetch_lineups(self, match_id: str) -> Optional[Dict[str, Any]]:
        """
        Maç kadro bilgilerini (lineups) çeker.

        Args:
            match_id: Maç ID'si

        Returns:
            Optional[Dict[str, Any]]: Lineup verisi veya başarısız ise None
        """
        url = f"{self.base_url}/event/{match_id}/lineups"
        try:
            return make_api_request(url)
        except Exception as e:
            logger.error(f"Maç ID {match_id} için lineup verisi çekilirken hata: {str(e)}")
            return None

    def _fetch_incidents(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Maç olaylarını (goller, kartlar, devre vb.) çeker — yanıt genelde {\"incidents\": [...], \"home\": ..., \"away\": ...}."""
        url = f"{self.base_url}/event/{match_id}/incidents"
        try:
            return make_api_request(url)
        except Exception as e:
            logger.error(f"Maç ID {match_id} için incidents verisi çekilirken hata: {str(e)}")
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

            # Güvenli dizin adları oluştur (ID prefix ile standart format)
            safe_tournament_name = f"{tournament_id}_{tournament_name.replace(' ', '_').replace('/', '_')}" if tournament_id else tournament_name.replace(' ', '_').replace('/', '_')

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

            # Her veri türünü ayrı ayrı kaydet
            for data_type, data in match_data.items():
                if data is not None:
                    atomic_write_json(os.path.join(match_dir, f"{data_type}.json"), data)

            # Bitmiş maçta boş gelen dilimleri say; yeterince denenenler bir daha beklenmez
            if MatchFetcher._is_finished_event(basic_data):
                unavailable = self._load_unavailable(match_dir)
                changed = False
                for key in DETAIL_SLICE_KEYS:
                    if key not in match_data:
                        continue  # bu kayıtta istenmedi (refill yalnız eksikleri ister)
                    if self.match_detail_slice_present(key, match_data):
                        changed |= unavailable.pop(key, None) is not None
                    else:
                        unavailable[key] = unavailable.get(key, 0) + 1
                        changed = True
                if changed:
                    atomic_write_json(os.path.join(match_dir, UNAVAILABLE_FILE), unavailable)

            mid = str(match_id)
            if getattr(self, "_match_index", None) is not None:
                self._match_index[mid] = (safe_tournament_name, safe_season_name, match_dir)
            if getattr(self, "_need_cache", None) is not None:
                self._need_cache.pop(mid, None)

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
        # Her veri türü için ayrı yükleme
        try:
            # Determine file path based on provided structure
            if league_dir and season_dir:
                # New structure: match_details/league/season/match_id/
                match_dir = os.path.join(self.match_details_dir, league_dir, season_dir, str(match_id))
            else:
                # Old structure: match_details/match_id/
                match_dir = os.path.join(self.match_details_dir, str(match_id))

            # match_data sözlüğünü başlat
            if match_data is None:
                match_data = {}

                # Her veri dosyasını kontrol et ve yükle
                for file_name in REQUIRED_FILES:
                    file_path = os.path.join(match_dir, file_name)
                    if os.path.exists(file_path):
                        try:
                            with open(file_path, 'r', encoding='utf-8') as f:
                                data_type = file_name.split('.')[0]  # .json uzantısını kaldır
                                match_data[data_type] = json.load(f)
                        except Exception as e:
                            logger.warning(f"Maç ID {match_id} için {file_name} dosyası yüklenirken hata: {str(e)}")

                # En azından basic.json dosyası gerekli
                basic_path = os.path.join(match_dir, 'basic.json')
                if not os.path.exists(basic_path):
                    logger.warning(f"Maç ID {match_id} için basic.json dosyası bulunamadı: {basic_path}")
                    return None

                try:
                    with open(basic_path, 'r', encoding='utf-8') as f:
                        match_data['basic'] = json.load(f)
                except Exception as e:
                    logger.error(f"Maç ID {match_id} için basic.json yüklenirken hata: {str(e)}")
                    return None
        except Exception as e:
            logger.error(f"Maç ID {match_id} için veri hazırlanırken hata: {str(e)}")
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

        # Process lineup data
        if "lineups" in match_data and match_data["lineups"]:
            lineups = match_data["lineups"]
            # Add lineup confirmation status - type check için güncelleme
            processed["lineups_confirmed"] = lineups.get("confirmed", False) if isinstance(lineups, dict) else False

            # Process home team lineup - tip kontrolü eklenmiş
            if isinstance(lineups, dict) and "home" in lineups and isinstance(lineups["home"], dict):
                home_lineup = lineups["home"]
                if "players" in home_lineup and isinstance(home_lineup["players"], list):
                    home_players = home_lineup["players"]
                    # Add starting XI count
                    starting_xi_home = sum(1 for player in home_players if player.get("substitute") is False)
                    processed["home_starting_xi_count"] = starting_xi_home

                    # Add substitutes count
                    subs_home = sum(1 for player in home_players if player.get("substitute") is True)
                    processed["home_substitutes_count"] = subs_home

                # Add formation information if available
                if "formation" in home_lineup and isinstance(home_lineup["formation"], dict):
                    processed["home_formation"] = home_lineup["formation"].get("name")
                else:
                    processed["home_formation"] = None

            # Process away team lineup - tip kontrolü eklenmiş
            if isinstance(lineups, dict) and "away" in lineups and isinstance(lineups["away"], dict):
                away_lineup = lineups["away"]
                if "players" in away_lineup and isinstance(away_lineup["players"], list):
                    away_players = away_lineup["players"]
                    # Add starting XI count
                    starting_xi_away = sum(1 for player in away_players if player.get("substitute") is False)
                    processed["away_starting_xi_count"] = starting_xi_away

                    # Add substitutes count
                    subs_away = sum(1 for player in away_players if player.get("substitute") is True)
                    processed["away_substitutes_count"] = subs_away

                # Add formation information if available
                if "formation" in away_lineup and isinstance(away_lineup["formation"], dict):
                    processed["away_formation"] = away_lineup["formation"].get("name")
                else:
                    processed["away_formation"] = None

        return processed

    def fetch_matches_batch(
        self,
        match_ids: List[Union[int, str]],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Bir grup maç için veri çeker."""
        self.begin_job_cache()
        try:
            return self._fetch_matches_batch(match_ids, progress_callback, should_cancel)
        finally:
            self.end_job_cache()

    def _fetch_matches_batch(
        self,
        match_ids: List[Union[int, str]],
        progress_callback: Optional[Callable[[int, int, str], None]],
        should_cancel: Optional[Callable[[], bool]],
    ) -> Dict[str, Dict[str, Any]]:
        use_tqdm = True
        results = {}

        match_ids_to_process = [id for id in match_ids if self._needs_detail_fetch(str(id)) != "none"]
        skipped = len(match_ids) - len(match_ids_to_process)
        if skipped:
            logger.info(f"{skipped} maç detayları tamam, atlanıyor")

        n = len(match_ids_to_process)
        iterator: Any = tqdm(match_ids_to_process) if use_tqdm else match_ids_to_process

        for idx, match_id in enumerate(iterator):
            if should_cancel and should_cancel():
                logger.info("Match detail batch cancelled after %s/%s", idx, n)
                break
            match_id = str(match_id)

            if use_tqdm:
                iterator.set_description(f"Maç ID {match_id}")
            else:
                logger.info(f"Maç verisi çekiliyor: ID {match_id}")

            if self._needs_detail_fetch(match_id) == "refill":
                match_data = self.refill_missing_match_slices(match_id)
                if not match_data:
                    match_data = self.fetch_match_data(match_id)
            else:
                match_data = self.fetch_match_data(match_id)

            if match_data:
                results[match_id] = match_data

            if progress_callback and n > 0:
                progress_callback(idx + 1, n, f"Match details {idx + 1}/{n}")

            # Sabit kısa bekleme (SofaScore saniyede 5 isteğe izin veriyor)
            if idx < n - 1:  # Son elemandan sonra bekleme yapma
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
                    if os.path.exists(os.path.join(league_path, "basic.json")):
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
                            if os.path.isdir(match_path) and os.path.exists(os.path.join(match_path, "basic.json")):
                                match_infos.append((league_name, season_name, match_id))

                # Also check for matches directly under match_details (old structure)
                for item in os.listdir(self.match_details_dir):
                    direct_path = os.path.join(self.match_details_dir, item)
                    if os.path.isdir(direct_path) and item != "processed" and os.path.exists(os.path.join(direct_path, "basic.json")):
                        # This is likely a match ID from the old structure
                        match_id = item
                        # Try to extract league info
                        try:
                            with open(os.path.join(direct_path, "basic.json"), 'r', encoding='utf-8') as f:
                                basic_data = json.load(f)
                                actual_league = basic_data.get("tournament", {}).get("uniqueTournament", {}).get("name", "Unknown")
                                actual_season = basic_data.get("season", {}).get("name", "Unknown")
                                match_infos.append((actual_league, actual_season, match_id))
                        except (OSError, ValueError, AttributeError):
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
                    if os.path.exists(os.path.join(league_path, "basic.json")):
                        continue

                    # Check each season
                    for season_name in os.listdir(league_path):
                        season_path = os.path.join(league_path, season_name)
                        if not os.path.isdir(season_path):
                            continue

                        # Check if this match exists in this season
                        match_path = os.path.join(season_path, match_id)
                        if os.path.isdir(match_path) and os.path.exists(os.path.join(match_path, "basic.json")):
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
                    if os.path.isdir(direct_path) and os.path.exists(os.path.join(direct_path, "basic.json")):
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
                            if os.path.isdir(item_path) and item == match_id and os.path.exists(os.path.join(item_path, "basic.json")):
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

    @staticmethod
    def _season_summary_files(
        league_path: str, only_season_ids: Optional[List[int]], max_seasons: int
    ) -> List[str]:
        """
        Bir lig dizinindeki sezon özet CSV'leri (`{sid}_..._summary.csv`, eski `_matches.csv`
        dahil), sezon ID'si sayısal olarak büyükten küçüğe. only_season_ids verilirse yalnızca
        onlar; max_seasons > 0 ise en yeni N sezon.
        """
        by_season: Dict[int, List[str]] = {}

        def add(path: str, sid_text: str) -> None:
            if sid_text.isdigit():
                by_season.setdefault(int(sid_text), []).append(path)

        for name in os.listdir(league_path):
            path = os.path.join(league_path, name)
            if os.path.isfile(path) and name.endswith(("_summary.csv", "_matches.csv")):
                add(path, name.split("_", 1)[0])
            elif os.path.isdir(path):
                for inner in os.listdir(path):
                    if inner.endswith(("_summary.csv", "_matches.csv")):
                        add(os.path.join(path, inner), name.split("_", 1)[0])

        season_ids = sorted(by_season, reverse=True)
        if only_season_ids is not None:
            allowed = {int(s) for s in only_season_ids}
            season_ids = [sid for sid in season_ids if sid in allowed]
        if max_seasons > 0:
            season_ids = season_ids[:max_seasons]
        return [path for sid in season_ids for path in by_season[sid]]

    def fetch_all_match_details(
        self,
        league_id: Optional[str] = None,
        max_seasons: int = 0,
        only_season_ids: Optional[List[int]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """
        Tüm maçlar için detaylı verileri çeker.
        UI tarafından çağrılmak üzere tasarlanmıştır.

        Args:
            league_id: Belirli bir lig ID'si (None ise tüm ligler)
            max_seasons: Son kaç sezon işlenecek (0 ise tüm sezonlar)
            only_season_ids: Verilmişse yalnızca bu sezon ID'lerindeki CSV'lerden maçlar alınır.
            progress_callback: İsteğe bağlı (done, total, message) ile ara ilerleme (ör. web UI).

        Returns:
            bool: İşlem başarılı ise True, değilse False
        """
        self.begin_job_cache()
        try:
            return self._fetch_all_match_details(
                league_id, max_seasons, only_season_ids, progress_callback, should_cancel
            )
        finally:
            self.end_job_cache()

    def _fetch_all_match_details(
        self,
        league_id: Optional[str],
        max_seasons: int,
        only_season_ids: Optional[List[int]],
        progress_callback: Optional[Callable[[int, int, str], None]],
        should_cancel: Optional[Callable[[], bool]],
    ) -> bool:
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
                print("Tüm ligler için maç detayları çekiliyor...")
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

                print(f"\nLig dizini: {league_dir}")

                summary_files = self._season_summary_files(league_path, only_season_ids, max_seasons)

                # Özet dosyalarından maç ID'lerini çıkar
                current_ids = []
                for file_path in summary_files:
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

            match_ids_to_process = [
                id for id in match_ids
                if self._needs_detail_fetch(str(id)) != "none"
            ]
            complete_count = len(match_ids) - len(match_ids_to_process)
            if complete_count:
                print(f"{complete_count} maçta tüm detay dilimleri hazır; eksik/kısmi olanlar işlenecek.")

            if not match_ids_to_process:
                print("Tüm maçların detayları tam!")
                return True

            # Maç detaylarını paralel olarak çek
            batch_size = 100  # Her seferde kaç maç işleneceği
            total_success = 0
            total_attempts = len(match_ids_to_process)
            print(f"\nToplam {total_attempts} maç için detaylar çekilecek...")
            if progress_callback:
                progress_callback(0, total_attempts, f"Match details 0/{total_attempts}")

            # İlerleme gösterimi için daha temiz bir format
            for i in range(0, len(match_ids_to_process), batch_size):
                if should_cancel and should_cancel():
                    logger.info("fetch_all_match_details cancelled before batch at index %s", i)
                    break
                batch = match_ids_to_process[i:i+batch_size]
                current_batch = i // batch_size + 1
                total_batches = (len(match_ids_to_process) - 1) // batch_size + 1
                start_index = i + 1
                end_index = min(i + len(batch), total_attempts)

                print(f"\nBatch {current_batch}/{total_batches}: {len(batch)} maç işleniyor ({start_index}-{end_index}/{total_attempts})...")

                nested_cb: Optional[Callable[[int, int, str], None]] = None
                if progress_callback:
                    batch_base = i

                    def nested_cb(
                        done_l: int,
                        total_l: int,
                        _msg: str,
                        _base: int = batch_base,
                        _tb: int = total_batches,
                        _cb: int = current_batch,
                    ) -> None:
                        global_done = min(_base + done_l, total_attempts)
                        progress_callback(
                            global_done,
                            total_attempts,
                            f"Match details {global_done}/{total_attempts} (batch {_cb}/{_tb})",
                        )

                # Paralel katman fetch_matches_batch_async zaten alt batch'lerde ilerleme verir;
                # web UI'da 0/total takılı kalmaması için buraya bağlıyoruz.
                results = self.fetch_matches_batch_parallel(
                    batch,
                    max_concurrent=self.config_manager.get_max_concurrent(),
                    progress_callback=nested_cb,
                    should_cancel=should_cancel,
                )

                if results:
                    success_count = len(results)
                    total_success += success_count
                    print(f"✓ Batch {current_batch}: {success_count}/{len(batch)} başarılı")
                if self.rate_limit_breaker_triggered:
                    i18n = get_i18n()
                    print(i18n.t("error_rate_limit_detected", count=len(results) if results else 0))
                    if self.last_rate_limit_headers:
                        logger.warning("Son başarısız isteklerden header/debug özeti:")
                        for idx, header_info in enumerate(self.last_rate_limit_headers[-20:], start=1):
                            logger.warning(f"{idx}. match_id={header_info.get('match_id')} error={header_info.get('error')}")
                    os.environ["APP_EXIT_CODE"] = "2"
                    break

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

            # Daha önce klasör varsa eksik dilimleri tamamla; yoksa tam çekim
            match_path = self._find_match_path(match_id)
            if match_path:
                match_data = self.refill_missing_match_slices(match_id)
                if match_data:
                    return True
                match_data = self.fetch_match_data(match_id)
                return bool(match_data)

            # Maç verilerini çek (fetch_match_data zaten match_details altına kaydeder)
            match_data = self.fetch_match_data(match_id)
            if not match_data:
                logger.warning(f"Maç ID {match_id} için veri bulunamadı veya maç henüz bitmemiş.")
                return False
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

    def convert_league_matches_to_csv(self, league_id_or_name: Union[int, str]) -> Optional[List[str]]:
        """
        Belirli bir ligin tüm maçlarını CSV formatına dönüştürür.

        Args:
            league_id_or_name: Dönüştürülecek ligin ID'si veya adı

        Returns:
            Optional[List[str]]: Oluşturulan CSV dosyalarının yolları veya işlem başarısız ise None
        """
        try:
            # Lig ID'si veya adını string'e dönüştür
            league_id_or_name = str(league_id_or_name)
            print(f"'{league_id_or_name}' için CSV oluşturuluyor...")

            # Lig dizinini bul
            matches_dir = os.path.join(self.data_dir, "matches")
            league_dir = None

            # ConfigManager'dan lig adı-ID eşleştirmelerini al
            league_id = None
            league_name = None

            # Önce sayısal ID mi kontrol et
            if league_id_or_name.isdigit():
                league_id = league_id_or_name
                league_name = self.config_manager.get_league_name_by_id(int(league_id_or_name))
            else:
                # İsim olarak kontrol et
                league_id = self.config_manager.get_league_id_by_name(league_id_or_name)
                if league_id:
                    league_name = league_id_or_name
                    league_id = str(league_id)

            if league_id:
                print(f"Lig ID: {league_id}, Lig Adı: {league_name or 'Bilinmiyor'}")

            # Eğer ConfigManager'dan bulunamadıysa, dizin isimlerinden bulmaya çalış
            for dir_name in os.listdir(matches_dir):
                # ID ile eşleşme kontrolü
                if league_id and (dir_name.startswith(f"{league_id}_") or dir_name == league_id):
                    league_dir = dir_name
                    break

                # Ad ile eşleşme kontrolü (tam veya kısmi)
                if league_name:
                    safe_league_name = league_name.replace(" ", "_").lower()
                    if safe_league_name in dir_name.lower():
                        league_dir = dir_name
                        break

                # Girilen değer doğrudan dizin ismiyle eşleşiyorsa
                if league_id_or_name.lower() in dir_name.lower():
                    league_dir = dir_name
                    break

            if not league_dir:
                logger.warning(f"'{league_id_or_name}' için dizin bulunamadı.")
                print(f"\n❌ '{league_id_or_name}' için dizin bulunamadı.")
                return None

            print(f"Dizin bulundu: {league_dir}")

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
                        if file_name.endswith('.json'):
                            try:
                                # JSON dosyalarından maç ID'lerini çıkar
                                file_path = os.path.join(season_path, file_name)
                                with open(file_path, 'r', encoding='utf-8') as f:
                                    data = json.load(f)

                                    # round_X.json dosyasından maç ID'lerini çıkar
                                    if "events" in data and isinstance(data["events"], list):
                                        for event in data["events"]:
                                            match_id = event.get("id")
                                            if match_id:
                                                match_ids.append(str(match_id))
                            except Exception as e:
                                logger.debug(f"JSON dosyası {file_path} okunurken hata: {str(e)}")
                                continue
                        elif file_name.endswith('_matches.csv') or file_name.endswith('_summary.csv'):
                            file_path = os.path.join(season_path, file_name)
                            ids_from_csv = self._extract_match_ids_from_csv(file_path)
                            if ids_from_csv:
                                match_ids.extend(ids_from_csv)

            # Tekrarlanan ID'leri temizle
            match_ids = list(set(match_ids))

            if not match_ids:
                logger.warning(f"'{league_id_or_name}' için maç verisi bulunamadı.")
                return None

            print(f"Toplam {len(match_ids)} maç bulundu, CSV'ye dönüştürülüyor...")

            # create_csv_dataset metodunu çağır
            result = self.create_csv_dataset(match_ids=match_ids, separate_by_league=True)

            if result:
                if isinstance(result, list):
                    logger.info(f"Lig ID {league_id_or_name} için {len(result)} CSV dosyası başarıyla oluşturuldu.")
                else:
                    logger.info(f"Lig ID {league_id_or_name} için CSV dosyası başarıyla oluşturuldu: {result}")
                return result if isinstance(result, list) else [result]
            else:
                logger.warning(f"Lig ID {league_id_or_name} için CSV oluşturulamadı.")
                return None

        except Exception as e:
            logger.error(f"Lig ID {league_id_or_name} için CSV dönüştürürken hata: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def convert_all_matches_to_csv(self, match_ids: Optional[List[str]] = None, separate_by_league: bool = False) -> Union[str, List[str]]:
        """Tüm maçları (veya verilenleri; boş liste = hepsi) CSV'ye dönüştürür."""
        return self.create_csv_dataset(match_ids=match_ids or None, separate_by_league=separate_by_league)

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
