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
import datetime as dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union, Tuple
from pathlib import Path
import asyncio
import threading
from collections import Counter
import pandas as pd
from tqdm import tqdm

from src import breaker as request_breaker
from src.client import base_url
from src.config_manager import ConfigManager
from src.exceptions import ResourceNotFoundError, StorageError
from src.fsutil import atomic_write_json
# Gölge kip (docs/design/01-storage.md 3.5): her yazmadan sonra Store'un bir `shadow_*` kancası çağrılır ve
# katalog yazılanı diskten yeniden dizinler. Kayıtlı maçlar da aynı kökten okunur (`open_store(...).events`).
# Paket kökü üzerinden: cephe ilk çağrıda yüklenir.
from src import store as store_hooks
from src.utils import make_api_request, ensure_directory
from src.match_fetcher import MatchFetcher
from src.sports import DETAIL_SLICES, event_sport_slug, get_slice, slices_for
from src.status import OBSERVATION_KEY, observation_record
from src.refresh import SCORE_CHANGES_FILE, change_row, diff_basic, refresh_due
from src.services.query import QueryService
# Sonuç tipi ve "veri var mı" yüklemleri src/slices.py'de durur. `X as X` biçimindekiler buradan taşınan
# adlardır: eski import'lar (from src.match_data_fetcher import SliceOutcome, SLICE_*) çalışmaya devam eder.
from src.slices import (
    SLICE_EMPTY as SLICE_EMPTY,
    SLICE_FAILED as SLICE_FAILED,
    SLICE_OK as SLICE_OK,
    SliceOutcome as SliceOutcome,
    has_h2h_data_dict,
    has_incidents_data_dict,
    has_lineups_data_dict,
    has_pregame_form_data_dict,
    has_team_streaks_data_dict,
    match_detail_slice_present,
    statistics_has_data,
)

from src.logger import get_logger

if TYPE_CHECKING:
    from src.store import EventRow, Store

logger = get_logger("MatchDataFetcher")

# Detay dilimleri src/sports.py'deki DETAIL_SLICES tablosundan türer; hangi maçta hangisinin isteneceğini
# slices_for(spor) söyler. Aşağıdaki iki ad spordan bağımsız özetlerdir ve eski import'lar için durur.

# UI / dosya tamlığı ile uyumlu alt dilimler (basic hariç): tablodaki `required` dilimler
DETAIL_SLICE_KEYS = tuple(s.key for s in DETAIL_SLICES if s.required)

# Bir maç dizininden okunan dosyalar
REQUIRED_FILES = ['basic.json'] + [f"{key}.json" for key in DETAIL_SLICE_KEYS]

# Bitmiş bir maçta bu kadar KESİN yanıtta da boş gelen dilim o maç için yok sayılır (ör. tenis
# maçlarında kadro/olay yok). Kesin yanıt: HTTP 404 ya da içinde veri olmayan 200. Başarısız istek
# (403/429/5xx/zaman aşımı/ağ/bozuk yanıt) sayılmaz; _slice_status.json'a yazılır ve sonraki
# çalıştırmada yeniden denenir.
UNAVAILABLE_AFTER_ATTEMPTS = 2
UNAVAILABLE_FILE = "_unavailable.json"
# Dilim başına son durum: {"<dilim>": {"empty": {"count", "at"}, "error": {"reason", "status", "at", "count"}}}
#   empty.count  bu sürümün kesin yanıtla saydığı "yok" sayısı. _unavailable.json'daki sayı bundan
#                büyükse fark eski sürümden kalmadır (geçici hata da olabilir; bkz. reset_unavailable_markers)
#   error        dilimin son başarısız isteği (neden, HTTP kodu, zaman, art arda kaç kez)
SLICE_STATUS_FILE = "_slice_status.json"

# uniqueTournament.id'si olmayan maçların sabit dizini: match_details/_no_tournament/<spor>/<maç id>
NO_TOURNAMENT_DIR = "_no_tournament"

def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _path_part(value: Any) -> str:
    """Kimlikten/slug'dan güvenli dizin adı parçası."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value)).strip(".") or "unknown"

# score_changes.jsonl'a paralel iş parçacıklarından ekleme
_SCORE_CHANGES_LOCK = threading.Lock()


# Tablodan önce de var olan dilim yardımcıları: testler ve dış kod bu adları doğrudan değiştiriyor/çağırıyor.
# Yeni dilimler buraya eklenmez; _fetch_slice onları tablodaki yolla çeker.
_LEGACY_SLICE_FETCHERS = {
    "statistics": "_fetch_match_statistics",
    "team_streaks": "_fetch_team_streaks",
    "pregame_form": "_fetch_pregame_form",
    "h2h": "_fetch_h2h",
    "lineups": "_fetch_lineups",
    "incidents": "_fetch_incidents",
}


def _event_sport(basic: Dict[str, Any]) -> str:
    """Olayın sporu (küçük harfli slug, yoksa ad); bilinmiyorsa ""."""
    return event_sport_slug(basic) or ""


# Eski düzendeki kayıtların katalogdaki `layout` değeri (src/store)
_LEGACY_LAYOUT = "legacy"
_MAX_EVENT_ID = 2 ** 63 - 1  # kataloğun saklayabildiği en büyük kimlik


def _stored_observation(row: "EventRow") -> Optional[Dict[str, Any]]:
    """
    Katalog satırından observation.json'ın karşılığı; kayıt gözlemsizse (eski kayıt) None.
    `status_regressed` yapışkan bayraktır: yalnızca doğruysa yazılır (refresh_match gibi).
    Tarih olarak yazılamayan bir gözlem anı (bozuk kayıt) gözlem yokmuş gibi işlenir.
    """
    observed: Optional[str] = None
    if row.observed_at is not None:
        try:
            observed = dt.datetime.fromtimestamp(row.observed_at, dt.timezone.utc).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            observed = None
    if observed is None and not row.status_regressed:
        return None
    observation: Dict[str, Any] = {"observed_at_utc": observed, "change_ts": row.change_ts}
    if row.status_regressed:
        observation["status_regressed"] = True
    return observation


@dataclass
class SingleFetchReport:
    """
    Tek maç çekiminde (fetch_match_data / refill_missing_match_slices) SofaScore'a giden isteklerin sonucu.

    Bu iki fonksiyon "maç yok", "maç bitmemiş" ve "istek reddedildi" durumlarının hepsinde None döndürür;
    dilimlerin hepsi reddedildiğinde de dolu bir sözlük döndürür. Nedeni kullanıcıya söylemesi gereken
    çağıran (web: POST /api/matches/{id}/fetch) bir rapor verir ve sonuca oradan bakar.
    """

    # /event/{id} isteğinin sonucu; istek gönderilmediyse None
    event: Optional[SliceOutcome] = None
    # Bu çağrıda istenen dilimler, istek sırasıyla (anahtar → sonuç)
    slices: Dict[str, SliceOutcome] = field(default_factory=dict)

    def upstream_failure(self) -> Optional[SliceOutcome]:
        """
        Çekimi SofaScore tarafı engellediyse o başarısız sonuç, aksi halde None:
          - /event isteği başarısız olduysa (403/429/5xx/zaman aşımı/ağ/bozuk yanıt) onun sonucu;
          - dilim istendiyse ve HİÇBİRİ yanıt almadıysa en sık görülen başarısızlık (eşitlikte ilk istenen).
        Kesin "yok" (404, içinde veri olmayan 200) bir yanıttır: tek bir dilim bile yanıt aldıysa None döner.
        """
        if self.event is not None and self.event.failed:
            return self.event
        outcomes = list(self.slices.values())
        if not outcomes or not all(outcome.failed for outcome in outcomes):
            return None
        reason = Counter(outcome.reason for outcome in outcomes).most_common(1)[0][0]
        return next(outcome for outcome in outcomes if outcome.reason == reason)


class MatchDataFetcher:
    """SofaScore API'sinden detaylı maç verilerini çeken ve işleyen sınıf."""

    # --- kayıtlı maçların okunması: depo üzerinden (docs/design/01-storage.md 2.3, 5.2; plan maddesi RD-1) ---
    #
    # Bir maçın yeri ve yükleri kataloğa sorulur (`Store.events`); dizin ağacı gezilmez. Katalog, depo
    # açılırken dosyalarla eşitlenir ve aşağıdaki yazıcıların her yazmasından sonra güncellenir (`shadow_*`
    # kancaları). Eski, dizini dosya dosya okuyan okuyuculardan farklar yalnızca eski biçimli kayıtlarda
    # görünür (01-storage.md 5.1 ve 5.2):
    #
    #   * yalnızca birleşik dosyası (`<id>/<id>.json`) olan dizin de bir maçtır (eskiden bulunamıyordu);
    #   * dilim önce kendi dosyasından, yoksa birleşik dosyadan okunur ve gözlem her zaman okunur (eskiden
    #     birleşik dosya varsa yalnızca o okunuyordu: böyle bir kayıt hiç yenilenmiyordu);
    #   * okunamayan (yarıda kesilmiş) dilim dosyası yalnızca o dilimi düşürür (eskiden ondan sonraki
    #     dilimler de okunmuyordu);
    #   * aynı maç iki dizinde duruyorsa olay yükü en yeni olan kopya geçerlidir (eskiden arama lig/sezon
    #     dizinindekini, iş önbelleği ilk listeleneni seçiyordu);
    #   * adı kimlik olmayan ya da içindeki yükün kimliği adına uymayan dizin maç sayılmaz.

    def _store(self) -> "Store":
        """Veri dizininin deposu. Süreçte dizin başına tek nesnedir; ilk açılış kataloğu kurar ya da uzlaştırır."""
        return store_hooks.open_store(self.data_dir)

    def _stored_event(self, match_id: Union[int, str]) -> Optional["EventRow"]:
        """
        Olay yükü eski düzende duran maçın katalog satırı; öyle bir kayıt yoksa None. Yalnızca bir program
        sayfasından bilinen maç (yükü yok) ve kurallı bir kimlik olmayan metin ("007", "abc") kayıt değildir.
        """
        text = str(match_id)
        if not (text.isascii() and text.isdigit()) or str(int(text)) != text or int(text) > _MAX_EVENT_ID:
            return None
        row = self._store().events.get(int(text))
        if row is None or not row.has_event_payload or row.layout != _LEGACY_LAYOUT or not row.path:
            return None
        return row

    def _legacy_location(self, row: "EventRow") -> Optional[Tuple[Optional[str], Optional[str], str]]:
        """Katalogdaki eski düzen yolu (`match_details/[<lig>/<sezon>/]<id>`) → (lig dizini, sezon dizini, maç dizini)."""
        parts = str(row.path).strip("/").split("/")
        if parts[0] != os.path.basename(self.match_details_dir):
            return None
        if len(parts) == 2:  # eski düz yapı
            return (None, None, os.path.join(self.match_details_dir, parts[1]))
        if len(parts) == 4:
            return (parts[1], parts[2], os.path.join(self.match_details_dir, *parts[1:]))
        return None

    def _find_match_path(self, match_id: str) -> Optional[Tuple[Optional[str], Optional[str], str]]:
        """
        Kayıtlı maçın yeri: (lig dizini adı, sezon dizini adı, maç dizini); düz kayıtta (`match_details/<id>`)
        ilk ikisi None. Kayıt yoksa None. Yeri katalog söyler (birincil anahtar araması), ağaç gezilmez.
        """
        row = self._stored_event(match_id)
        return self._legacy_location(row) if row is not None else None

    def _build_match_index(self) -> Dict[str, Tuple[Optional[str], Optional[str], str]]:
        """match_id → konum: detayı eski düzende kayıtlı bütün maçlar (katalogdan). İşler bunu kullanmaz."""
        index: Dict[str, Tuple[Optional[str], Optional[str], str]] = {}
        for row in self._store().events.iter(store_hooks.EventQuery(has_details=True)):
            if row.layout != _LEGACY_LAYOUT or not row.path:
                continue
            location = self._legacy_location(row)
            if location is not None:
                index[str(row.id)] = location
        return index

    def begin_job_cache(self) -> None:
        """Bir iş boyunca 'eksik mi' sonuçlarını önbelleğe al (maç konumları katalogdan sorulur, önbelleği yok)."""
        self._need_cache: Dict[str, str] = {}

    def end_job_cache(self) -> None:
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

            match_data = {"basic": basic_data, OBSERVATION_KEY: observation_record(basic_data)}

            # Spor türüne uygun endpoint'leri çağır: dilim tablosu src/sports.py'de (DETAIL_SLICES)
            details = slices_for(_event_sport(basic_data))
            tasks = [
                self._fetch_endpoint_async(session, detail.url(self.base_url, match_id), detail.key)
                for detail in details
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                # İş iptal edildiyse yarım maçı kaydetme; iptali yukarı taşı
                if isinstance(result, BaseException) and not isinstance(result, Exception):
                    raise result
            outcomes: Dict[str, SliceOutcome] = {}
            for detail, result in zip(details, results, strict=True):
                if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], SliceOutcome):
                    outcome = result[1]
                elif isinstance(result, Exception):
                    outcome = SliceOutcome.from_error(result)
                else:
                    continue
                outcomes[detail.key] = outcome
                # None da yazılır: dilim istendi ama verisi yok (boş ya da başarısız; ayrım `outcomes`ta)
                match_data[detail.key] = outcome.data

            # Verileri kaydet: yalnızca kesin "yok" yanıtları sayılır, başarısız istekler not edilir
            self._save_match_data(match_id, match_data, outcomes)
            return match_data
        except Exception as e:
            logger.error(f"Maç ID {match_id} için asenkron veri çekilirken hata: {str(e)}")
            raise

    async def _fetch_endpoint_async(self, session, url, key) -> Tuple[str, SliceOutcome]:
        """
        Bir dilimi çeker; (anahtar, tipli sonuç) döndürür. Hata yutulmaz: 404 kesin "yok"tur,
        403/429/5xx/zaman aşımı/ağ/bozuk yanıt ise "başarısız"dır ve devre kesiciye bildirilir.
        """
        from src.utils import make_api_request_async

        try:
            data = await make_api_request_async(session, url, max_retries=1)
        except Exception as e:
            outcome = SliceOutcome.from_error(e)
            if outcome.failed:
                # İstek katmanı bildirdiyse yeniden sayılmaz (aynı hata nesnesi bir kez sayılır)
                request_breaker.report_exception(e)
                logger.warning(f"{url} alınamadı ({outcome.reason}); sonraki çalıştırmada yeniden denenecek")
            return key, outcome
        if data:
            return key, self._answered_outcome(key, data)
        return key, SliceOutcome(SLICE_EMPTY, reason="empty")

    def _answered_outcome(self, key: str, data: Any) -> SliceOutcome:
        """
        Yanıt gelen (hata olmayan) dilim, gövdenin üç yanıtına göre (src.slices.slice_body_state):
          - veri var: "ok"
          - okunabiliyor ama içinde veri yok: kesin "boş" (sayılır; gövde sonuçta durur ve diske yazılır)
          - okunamadı (gövde beklenen JSON türünde değil): başarısız istek, neden "parse". Kesin bir "yok"
            yanıtı değildir: sayılmaz, _slice_status.json'a hata olarak yazılır ve dilim sonraki
            çalıştırmada yeniden istenir. Gövde sonuca konmaz, yani diske yazılmaz.

        Okunamayan gövde devre kesiciye bildirilmez: istek katmanı bu isteği yanıt almış olarak saymıştır
        ve engellenme belirtisi değildir. Yüklemler toplam olmadan önce böyle bir gövdede hata fırlatırdı;
        async hatta o hata da aynı yere varırdı (başarısız dilim, kesiciye bildirilmez, gövde yazılmaz),
        sync hatta ise maçın tamamını düşürürdü.
        """
        # İşlev içinde: bu dosyanın import bloğu başka bir plan maddesinindir (RD-1); P13 eşlemeyi taşır.
        from src.slices import BODY_DATA, BODY_MALFORMED, slice_body_state

        state = slice_body_state(key, data)
        if state == BODY_DATA:
            return SliceOutcome(SLICE_OK, data=data)
        if state == BODY_MALFORMED:
            logger.warning(
                f"Slice {key}: the answer has an unexpected shape ({type(data).__name__}); "
                "treated as a failed request, it will be requested again on the next run"
            )
            return SliceOutcome(SLICE_FAILED, reason=request_breaker.PARSE)
        return SliceOutcome(SLICE_EMPTY, data=data, reason="empty")

    async def fetch_matches_batch_async(self, match_ids, max_concurrent=30, progress_bar=None, progress_callback=None, should_cancel=None, failed_callback=None):
        """Birden çok maç için veri çeker (circuit breaker destekli).

        failed_callback(match_id): denemeleri tükenen ya da kaydı diske yazılamayan her maç için
        çağrılır (devre kesilince hiç denenmeyenler ve iptal edilenler başarısız sayılmaz).

        Devre kesici işin kesicisidir (src/breaker.py): çağıran kurduysa o, yoksa bu çağrı için
        yenisi. İstek katmanı her isteğin sonucunu (alt dilimler ve yenileme dahil) ona bildirir.
        Diske yazılamayan maç başarısız sayılır; disk dolu / izin yok gibi kalıcı hatalarda
        StorageError yukarı fırlatılır ve iş durur.
        """
        with request_breaker.scope(self.config_manager) as breaker:
            return await self._fetch_matches_batch_async(
                breaker, match_ids, max_concurrent, progress_bar, progress_callback, should_cancel, failed_callback
            )

    async def _fetch_matches_batch_async(
        self, breaker, match_ids, max_concurrent, progress_bar, progress_callback, should_cancel, failed_callback
    ):
        logger.debug(f"Starting batch fetch for {len(match_ids)} matches")

        match_ids_to_process, refresh_count = self._order_by_need(match_ids)
        skipped = len(match_ids) - len(match_ids_to_process)
        if skipped:
            logger.info(f"{skipped} maç detayları tamam, atlanıyor")
            if progress_bar:
                progress_bar.update(skipped)
        if refresh_count:
            logger.info(f"{refresh_count} maç yenileniyor (geçici kayıt)")

        results: Dict[str, Dict[str, Any]] = {}
        status_counts: Counter = Counter()
        recent_headers: List[Dict[str, str]] = []
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
                if breaker.tripped or cancelled:
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
                    nonlocal batch_success, batch_failed, cancelled
                    if cancelled or (should_cancel and should_cancel()):
                        cancelled = True
                        return None
                    max_retries = 3
                    for attempt in range(max_retries):
                        if cancelled or (should_cancel and should_cancel()):
                            cancelled = True
                            return None
                        if breaker.tripped:
                            # Devre kesildi: batch'te sırada bekleyen maçlar istek atmasın
                            return None
                        try:
                            async with sem:
                                if cancelled or (should_cancel and should_cancel()):
                                    cancelled = True
                                    return None
                                if breaker.tripped:
                                    return None
                                need = self._needs_detail_fetch(str(match_id))
                                if need == "refresh":
                                    result = await asyncio.to_thread(self.refresh_match, str(match_id))
                                elif need == "refill":
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
                                    batch_success += 1
                                    if progress_bar:
                                        progress_bar.update(1)
                                    return result
                                if breaker.tripped:
                                    # İstekler devre kesildiği için gönderilmedi: maç denenmemiş sayılır
                                    return None
                                status_counts["other"] += 1
                                # fetch_one görevleri bu batch bitmeden tamamlanır/iptal edilir
                                batch_status_counts["other"] += 1  # noqa: B023
                                break
                        except StorageError as e:
                            # Veri çekildi ama diske yazılamadı: istek hatası değil, yeniden istemek çözmez
                            logger.error(f"Maç {match_id} kaydedilemedi: {e}")
                            status_counts["storage"] += 1
                            batch_status_counts["storage"] += 1  # noqa: B023
                            if e.fatal:
                                batch_failed += 1
                                if failed_callback:
                                    failed_callback(str(match_id))
                                raise  # disk dolu / izin yok: kalan maçlar da yazılamaz, iş durur
                            break
                        except Exception as e:
                            err = str(e)
                            status_key = request_breaker.failure_kind(e)
                            if status_key == request_breaker.BREAKER_OPEN:
                                return None  # devre kesik: istek gönderilmedi, maç denenmemiş sayılır

                            status_counts[status_key] += 1
                            batch_status_counts[status_key] += 1  # noqa: B023
                            # İstek katmanından gelen hata zaten sayıldı; başka kaynaklı hata burada sayılır
                            breaker.record_exception(e)

                            if status_key in ("403", "429", "5xx"):
                                recent_headers.append({"match_id": str(match_id), "error": err})
                                if len(recent_headers) > 20:
                                    recent_headers.pop(0)

                            if breaker.tripped:
                                return None

                            if attempt < max_retries - 1:
                                await asyncio.sleep(1.0 * (2 ** attempt) + random.uniform(0, 1))
                                continue
                            break

                    batch_failed += 1
                    if failed_callback:
                        failed_callback(str(match_id))
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
                    # FetchCancelled (iptal), kalıcı depolama hatası veya beklenmeyen hata: kalan görevleri
                    # iptal edip bekle, oturum kapanmadan ve döngü kapatılmadan önce hiçbiri askıda kalmasın
                    pending = [t for t in batch_tasks if not t.done()]
                    for t in pending:
                        t.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    for t in batch_tasks:
                        if not t.cancelled():
                            t.exception()  # aynı anda düşen diğer görevlerin hatası "alınmadı" diye loglanmasın
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
                # Batch'ler arasında ayrıca beklenmez: hızı ortak istek bütçesi belirler (src/throttle.py)

        if progress_callback and total_m > 0 and not cancelled:
            progress_callback(total_m, total_m, "Parallel detail batches finished")

        # Maç düzeyindeki sayım + kesicinin gördüğü istek düzeyindeki hatalar (alt dilimler dahil)
        merged = dict(status_counts)
        for kind, count in breaker.counts().items():
            merged[kind] = max(merged.get(kind, 0), count)
        self.last_status_counts = merged
        self.rate_limit_breaker_triggered = breaker.tripped
        self.last_rate_limit_headers = recent_headers
        return results

    # Main metodunda çağırmak için senkron wrapper
    def fetch_matches_batch_parallel(self, match_ids, max_concurrent=10, progress_callback=None, should_cancel=None, failed_callback=None):
        """Paralel istekler için senkron wrapper."""
        print(get_i18n().t("details_processing_parallel", count=len(match_ids)))
        progress = tqdm(total=len(match_ids), desc=get_i18n().t("details_progress_label"))

        try:
            # asyncio.run: döngüyü kapatır, kalan görevleri iptal eder ve thread'e kapalı döngü bırakmaz
            return asyncio.run(self.fetch_matches_batch_async(
                match_ids, max_concurrent, progress, progress_callback, should_cancel, failed_callback
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
        self.base_url = base_url()
        self.rate_limit_breaker_triggered = False
        self.last_rate_limit_headers: List[Dict[str, str]] = []
        self.last_status_counts: Dict[str, int] = {}
        # refresh_match her yenilemede (match_id, değişti_mi) ile çağırır (web iş kartı sayacı)
        self.refresh_listener: Optional[Callable[[str, bool], None]] = None
        self.last_refresh_changed = False
        # Toplu indirmeyi durduran kalıcı depolama hatası (disk dolu, izin yok); yoksa None
        self.last_storage_error: Optional[StorageError] = None

        # Veri dizinlerinin var olduğundan emin ol
        ensure_directory(self.data_dir)
        ensure_directory(self.match_details_dir)
        ensure_directory(self.processed_dir)

    def _load_match_data_from_dir(self, match_dir: str, match_id: str) -> Dict[str, Any]:
        """
        Kayıtlı maçın birleşik sözlüğü: `basic`, yükü olan `required` dilimler (tablo sırasıyla) ve varsa
        `observation`. Depodan okunur (QueryService.match_detail_legacy); kayıt yoksa ya da okunamıyorsa boş
        sözlük. Dosyası okunamayan dilim sözlükte yer almaz (eksik sayılır ve yeniden istenir).

        match_dir, `_find_match_path`'in verdiği dizindir ve yalnızca eski çağrılar için durur: hangi dizinin
        okunacağını katalog söyler (aynı maçın iki kopyası varsa olay yükü en yeni olan).

        Gözlem katalog satırından kurulur (gözlem anı tam saniye; `change_ts` saklanan olay yükününküdür):
        okuyanlar yalnızca `observed_at_utc` ve `status_regressed` alanlarına bakar.
        """
        mid = str(match_id)
        try:
            row = self._stored_event(mid)
            if row is None:
                return {}
            result = QueryService(self._store()).match_detail_legacy(row.id)
            if result is None:
                return {}
            observation = _stored_observation(row)
            if observation is not None:
                result[OBSERVATION_KEY] = observation
            return result
        except Exception as e:
            logger.warning(f"Match {mid} could not be loaded from the store ({match_dir}): {e}")
            return {}

    # "Bu yanıtta veri var mı" yüklemleri src/slices.py'dedir; metot adları eski çağrılar için durur.

    def _statistics_has_data(self, d: Dict[str, Any]) -> bool:
        return statistics_has_data(d)

    def _has_lineups_data_dict(self, d: Dict[str, Any]) -> bool:
        return has_lineups_data_dict(d)

    def _has_h2h_data_dict(self, d: Dict[str, Any]) -> bool:
        return has_h2h_data_dict(d)

    def _has_pregame_form_data_dict(self, d: Dict[str, Any]) -> bool:
        return has_pregame_form_data_dict(d)

    def _has_team_streaks_data_dict(self, d: Dict[str, Any]) -> bool:
        return has_team_streaks_data_dict(d)

    def _has_incidents_data_dict(self, d: Dict[str, Any]) -> bool:
        return has_incidents_data_dict(d)

    def match_detail_slice_present(self, key: str, d: Dict[str, Any]) -> bool:
        """Dilimin verisi var mı (src.slices.match_detail_slice_present)."""
        return match_detail_slice_present(key, d)

    def _load_unavailable(self, match_dir: str) -> Dict[str, int]:
        try:
            with open(os.path.join(match_dir, UNAVAILABLE_FILE), "r", encoding="utf-8") as f:
                data = json.load(f)
            return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _load_slice_status(self, match_dir: str) -> Dict[str, Dict[str, Any]]:
        """_slice_status.json: dilim başına kesin "yok" sayısı ve son başarısız istek."""
        try:
            with open(os.path.join(match_dir, SLICE_STATUS_FILE), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}

    @staticmethod
    def _confirmed_empty_count(entry: Optional[Dict[str, Any]]) -> int:
        empty = (entry or {}).get("empty")
        count = empty.get("count") if isinstance(empty, dict) else None
        return count if isinstance(count, int) and not isinstance(count, bool) and count > 0 else 0

    def _update_slice_markers(
        self,
        match_dir: str,
        sport: Optional[str],
        match_data: Dict[str, Any],
        outcomes: Dict[str, SliceOutcome],
    ) -> None:
        """
        Bitmiş maçta istenen dilimlerin sonucunu işler:
          - verisi olan dilim: "yok" sayımı ve hata kaydı silinir
          - kesin "yok" yanıtı (404 ya da içinde veri olmayan 200): _unavailable.json'da sayılır;
            UNAVAILABLE_AFTER_ATTEMPTS'e ulaşınca dilim o maç için bir daha beklenmez
          - başarısız istek: SAYILMAZ; neden ve zamanla _slice_status.json'a yazılır, dilim beklenmeye
            devam eder (sonraki çalıştırmada yeniden istenir)
          - sonucu bilinmeyen dilim (bu kayıtta istenmedi): dokunulmaz
        """
        unavailable = self._load_unavailable(match_dir)
        status = self._load_slice_status(match_dir)
        now = _utc_now_iso()
        unavailable_changed = status_changed = False
        for detail in slices_for(sport, required_only=True):
            key = detail.key
            if self.match_detail_slice_present(key, match_data):
                unavailable_changed |= unavailable.pop(key, None) is not None
                status_changed |= status.pop(key, None) is not None
                continue
            outcome = outcomes.get(key)
            if outcome is None:
                continue  # bu kayıtta istenmedi (refill yalnız eksikleri ister) ya da sonucu bilinmiyor
            if outcome.failed and outcome.reason == request_breaker.BREAKER_OPEN:
                continue  # devre kesikti, istek hiç gönderilmedi: önceki hata kaydı (varsa) geçerli kalır
            entry = status.setdefault(key, {})
            if outcome.failed:
                previous = entry.get("error") if isinstance(entry.get("error"), dict) else {}
                entry["error"] = {
                    "reason": outcome.reason or request_breaker.OTHER,
                    "status": outcome.http_status,
                    "at": now,
                    "count": int(previous.get("count") or 0) + 1,
                }
            else:
                unavailable[key] = unavailable.get(key, 0) + 1
                unavailable_changed = True
                entry["empty"] = {"count": self._confirmed_empty_count(entry) + 1, "at": now}
                entry.pop("error", None)  # yanıt geldi: önceki hata geçersiz
            status_changed = True
        if unavailable_changed:
            atomic_write_json(os.path.join(match_dir, UNAVAILABLE_FILE), unavailable)
        if status_changed:
            status_path = os.path.join(match_dir, SLICE_STATUS_FILE)
            if status:
                atomic_write_json(status_path, status)
            elif os.path.exists(status_path):
                os.remove(status_path)

    def reset_unavailable_markers(
        self,
        league_id: Optional[Union[int, str]] = None,
        include_confirmed: bool = False,
    ) -> Dict[str, int]:
        """
        "Bu dilim bu maçta yok" işaretlerini yeniden denetime açar (--recheck-unavailable; web katmanı
        da çağırabilir). Ağ isteği yapmaz: işaretleri geri alır, dilimler sonraki indirmede yeniden istenir.

        Eski sürümler başarısız isteği de (403/429/5xx/zaman aşımı) "yok" sayıyordu; o işaretlerin
        hangisinin geçici hata olduğu dosyadan anlaşılamaz. Varsayılan olarak yalnızca kesin yanıtla
        doğrulanmamış sayımlar geri alınır (_unavailable.json'daki sayı − _slice_status.json'daki
        empty.count); bu sürümün 404 / boş 200 ile saydıkları kalır. Bu yüzden işlem tekrarlanabilir:
        yeniden denetimden sonra ikinci kez çalıştırmak hiçbir şeyi değiştirmez.

        include_confirmed=True: doğrulanmış işaretler de silinir (ör. SofaScore veriyi sonradan eklediyse).
        league_id: yalnızca `{league_id}_*` dizinleri.

        Returns: {"matches": işareti değişen maç, "slices": yeniden istenecek dilim, "scanned": bakılan maç}
        """
        result = {"matches": 0, "slices": 0, "scanned": 0}
        if not os.path.isdir(self.match_details_dir):
            return result
        for league_name in sorted(os.listdir(self.match_details_dir)):
            league_path = os.path.join(self.match_details_dir, league_name)
            if league_name == "processed" or not os.path.isdir(league_path):
                continue
            if league_id is not None and not league_name.startswith(f"{league_id}_"):
                continue
            for season_name in sorted(os.listdir(league_path)):
                season_path = os.path.join(league_path, season_name)
                if not os.path.isdir(season_path):
                    continue
                for mid in sorted(os.listdir(season_path)):
                    match_dir = os.path.join(season_path, mid)
                    if not os.path.isfile(os.path.join(match_dir, UNAVAILABLE_FILE)):
                        continue
                    result["scanned"] += 1
                    reopened = self._reset_match_markers(match_dir, include_confirmed)
                    if reopened:
                        result["matches"] += 1
                        result["slices"] += reopened
                        if getattr(self, "_need_cache", None) is not None:
                            self._need_cache.pop(mid, None)
        return result

    def _reset_match_markers(self, match_dir: str, include_confirmed: bool) -> int:
        """Bir maçın işaretlerini geri alır; yeniden beklenir hale gelen dilim sayısını döndürür."""
        unavailable = self._load_unavailable(match_dir)
        status = self._load_slice_status(match_dir)
        reopened = 0
        changed = status_changed = False
        for key, count in list(unavailable.items()):
            keep = 0 if include_confirmed else min(count, self._confirmed_empty_count(status.get(key)))
            if keep == count:
                continue
            changed = True
            if count >= UNAVAILABLE_AFTER_ATTEMPTS > keep:
                reopened += 1
            if keep:
                unavailable[key] = keep
            else:
                del unavailable[key]
            if include_confirmed and isinstance(status.get(key), dict) and status[key].pop("empty", None) is not None:
                status_changed = True
                if not status[key]:
                    del status[key]
        if not changed:
            return 0
        try:
            unavailable_path = os.path.join(match_dir, UNAVAILABLE_FILE)
            if unavailable:
                atomic_write_json(unavailable_path, unavailable)
            else:
                os.remove(unavailable_path)
            if status_changed:
                status_path = os.path.join(match_dir, SLICE_STATUS_FILE)
                if status:
                    atomic_write_json(status_path, status)
                else:
                    os.remove(status_path)
        except OSError as e:
            raise StorageError.from_exception(e, match_dir) from e
        finally:
            store_hooks.shadow_event(self.data_dir, os.path.basename(match_dir), match_dir)
        return reopened

    def _expected_slices(self, match_dir: str, sport: Optional[str] = None) -> List[str]:
        """Beklenen dilimler: o sporun `required` dilimleri, yeterince denenip hep boş gelenler hariç."""
        unavailable = self._load_unavailable(match_dir)
        return [
            detail.key
            for detail in slices_for(sport, required_only=True)
            if unavailable.get(detail.key, 0) < UNAVAILABLE_AFTER_ATTEMPTS
        ]

    def _needs_detail_fetch(self, match_id: str) -> str:
        """
        Returns:
            'none' — beklenen tüm dilimler tamam
            'refill' — basic var, eksik dilim(ler) var
            'refresh' — dilimler tam ama kayıt geçici (yenileme penceresi kapanmadı; src/refresh.py)
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
        for key in self._expected_slices(match_dir, _event_sport(data["basic"])):
            if not self.match_detail_slice_present(key, data):
                return "refill"
        if refresh_due(data["basic"], data.get(OBSERVATION_KEY) or {}):
            return "refresh"
        return "none"

    def _order_by_need(self, match_ids: List[Any]) -> Tuple[List[Any], int]:
        """İşlenecek maçlar: önce full/refill, sonra refresh. İkinci değer yenilenecek maç sayısı."""
        first, refresh = [], []
        for mid in match_ids:
            need = self._needs_detail_fetch(str(mid))
            if need == "refresh":
                refresh.append(mid)
            elif need != "none":
                first.append(mid)
        return first + refresh, len(refresh)

    def refresh_match(self, match_id: Union[int, str]) -> Optional[Dict[str, Any]]:
        """
        Geçici kaydı yeniler: yalnızca /event/{id} çekilir. Fark yoksa observation.json güncellenir;
        fark varsa basic.json da yazılır ve değişim score_changes.jsonl'a eski/yeni değerle eklenir.
        COMPLETED → VOID olursa kayıt silinmez, observation.json'a status_regressed: true yazılır.
        """
        mid = str(match_id)
        path_info = self._find_match_path(mid)
        if not path_info:
            return None
        _, _, match_dir = path_info
        data = self._load_match_data_from_dir(match_dir, mid)
        old = data.get("basic")
        if not old:
            return None
        new = self._fetch_match_basic(mid)
        if not new:
            logger.warning(f"Maç {mid} yenileme: /event alınamadı")
            return None

        stored = data.get(OBSERVATION_KEY)
        obs = observation_record(new)
        if isinstance(stored, dict) and stored.get("status_regressed"):
            obs["status_regressed"] = True
        changed = diff_basic(old, new)
        try:
            if changed:
                row = change_row(old, new, changed, _event_sport(new) or _event_sport(old))
                if row.get("status_regressed"):
                    obs["status_regressed"] = True
                    logger.warning(f"Maç {mid} oynanmış sayılıyordu, şimdi {new.get('status')}; kayıt silinmedi")
                self._append_score_change(row)
                atomic_write_json(os.path.join(match_dir, "basic.json"), new)
                full_json_path = os.path.join(match_dir, f"{mid}.json")
                if os.path.exists(full_json_path):  # eski tek dosyalı kayıt da güncel kalsın
                    with open(full_json_path, "r", encoding="utf-8") as f:
                        full = json.load(f)
                    full["basic"] = new
                    atomic_write_json(full_json_path, full)
                data["basic"] = new
                logger.info(f"Maç {mid} yenilendi: {len(changed)} alan değişti ({', '.join(list(changed)[:5])})")
            atomic_write_json(os.path.join(match_dir, f"{OBSERVATION_KEY}.json"), obs)
        except OSError as e:
            # Yazılamayan yenileme "yenilendi" sayılmaz; çağıran maçı başarısız işaretler (kalıcıysa iş durur)
            raise StorageError.from_exception(e, match_dir) from e
        finally:
            store_hooks.shadow_event(self.data_dir, mid, match_dir)
        data[OBSERVATION_KEY] = obs

        self.last_refresh_changed = bool(changed)
        if getattr(self, "_need_cache", None) is not None:
            self._need_cache.pop(mid, None)
        listener = getattr(self, "refresh_listener", None)
        if listener:
            listener(mid, bool(changed))
        return data

    def _append_score_change(self, row: Dict[str, Any]) -> None:
        path = os.path.join(self.data_dir, SCORE_CHANGES_FILE)
        with _SCORE_CHANGES_LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        store_hooks.shadow_changes(self.data_dir)

    def refresh_due_ids(self, league_id: Optional[Union[int, str]] = None) -> List[str]:
        """Kayıtlı maçlardan yenilenmesi gerekenler (--refresh-only). Eksik dilimli maçlar dahil değil."""
        ids: List[str] = []
        if not os.path.isdir(self.match_details_dir):
            return ids
        for league_name in sorted(os.listdir(self.match_details_dir)):
            league_path = os.path.join(self.match_details_dir, league_name)
            if league_name == "processed" or not os.path.isdir(league_path):
                continue
            if league_id is not None and not league_name.startswith(f"{league_id}_"):
                continue
            for season_name in sorted(os.listdir(league_path)):
                season_path = os.path.join(league_path, season_name)
                if not os.path.isdir(season_path):
                    continue
                for mid in sorted(os.listdir(season_path)):
                    if os.path.isdir(os.path.join(season_path, mid)) and self._needs_detail_fetch(mid) == "refresh":
                        ids.append(mid)
        return ids

    def refresh_matches(
        self,
        match_ids: List[str],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """
        Yalnızca yenileme (refresh-only): maçlar sırayla okunur. Hızı ortak istek bütçesi
        (src/throttle.py) ve her istekten sonraki WAIT_TIME beklemesi belirler; maçlar arasında
        ayrıca beklenmez.

        Devre kesilirse (SofaScore engelliyor / sürekli hata) kalan maçlar denenmez; sonuçta
        `breaker` (neden: "403" / "429" / "5xx" / "other") ve `skipped` (denenmeyen maç) alanları
        bulunur. Kalıcı depolama hatası (disk dolu, izin yok) StorageError olarak fırlatılır.
        """
        stats: Dict[str, Any] = {"refreshed": 0, "changed": 0, "failed": 0}
        n = len(match_ids)
        if n:
            logger.info(f"{n} maç yenileniyor")
        with request_breaker.scope(self.config_manager) as breaker:
            for idx, mid in enumerate(match_ids):
                if should_cancel and should_cancel():
                    break
                if breaker.tripped:
                    stats["breaker"] = breaker.reason()
                    stats["skipped"] = n - idx
                    logger.warning(f"Çok fazla başarısız istek; yenileme durduruldu, {n - idx} maç denenmedi")
                    break
                try:
                    result = self.refresh_match(mid)
                except StorageError as e:
                    logger.error(f"Maç {mid} yenilemesi kaydedilemedi: {e}")
                    if e.fatal:
                        raise
                    result = None
                if result is None:
                    stats["failed"] += 1
                else:
                    stats["refreshed"] += 1
                    stats["changed"] += int(self.last_refresh_changed)
                if progress_callback:
                    progress_callback(idx + 1, n, f"Refresh {idx + 1}/{n}")
            if breaker.tripped:
                stats.setdefault("breaker", breaker.reason())
                stats.setdefault("skipped", 0)
        self.rate_limit_breaker_triggered = bool(stats.get("breaker"))
        return stats

    def refill_missing_match_slices(
        self, match_id: Union[int, str], *, report: Optional[SingleFetchReport] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Diskte basic.json olan maçta eksik API dilimlerini tamamlar (yeni maç için fetch_match_data kullanın).

        report verilirse /event isteğinin ve istenen dilimlerin sonucu ona yazılır (SingleFetchReport).
        """
        mid = str(match_id)
        path_info = self._find_match_path(mid)
        if not path_info:
            return None
        _, _, match_dir = path_info
        match_data = self._load_match_data_from_dir(match_dir, mid)
        if not match_data.get("basic"):
            return None

        basic_live = self._fetch_event(mid, report)
        if not basic_live:
            logger.warning(f"Maç {mid} refill: canlı basic alınamadı")
            return None
        if not MatchFetcher._is_finished_event(basic_live):
            status = basic_live.get("status", {})
            logger.info(f"Maç {mid} bitmemiş ({status.get('description')}/{status.get('type')}), refill atlanıyor.")
            return None

        match_data["basic"] = basic_live
        match_data[OBSERVATION_KEY] = observation_record(basic_live)
        missing = [
            k
            for k in self._expected_slices(match_dir, _event_sport(basic_live))
            if not self.match_detail_slice_present(k, match_data)
        ]
        if not missing:
            return match_data

        outcomes: Dict[str, SliceOutcome] = {}
        for key in missing:
            outcomes[key] = self._fetch_slice(mid, key)
            match_data[key] = outcomes[key].data
        if report is not None:
            report.slices.update(outcomes)
        self._save_match_data(mid, match_data, outcomes)
        return match_data

    def fetch_match_data(
        self, match_id: Union[int, str], *, report: Optional[SingleFetchReport] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Bir maç için tüm detay verilerini çeker.

        report verilirse /event isteğinin ve istenen dilimlerin sonucu ona yazılır (SingleFetchReport).
        """
        match_id = str(match_id)
        logger.info(f"Maç ID {match_id} için detay verileri çekiliyor...")

        # Önce temel veriyi çek
        basic_data = self._fetch_event(match_id, report)

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

        # Diğer verileri çek: bu yol yalnızca `required` dilimleri ister (src/sports.py, DETAIL_SLICES)
        keys = [detail.key for detail in slices_for(_event_sport(basic_data), required_only=True)]
        match_data = {
            "basic": basic_data,
            OBSERVATION_KEY: observation_record(basic_data),
            **{key: None for key in keys},
        }

        # Diğer endpointleri topla
        outcomes: Dict[str, SliceOutcome] = {}
        for key in keys:
            outcomes[key] = self._fetch_slice(match_id, key)
            match_data[key] = outcomes[key].data
        if report is not None:
            report.slices.update(outcomes)

        # Verileri kaydet: yalnızca kesin "yok" yanıtları sayılır, başarısız istekler not edilir
        self._save_match_data(match_id, match_data, outcomes)

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

    def _fetch_event(self, match_id: str, report: Optional[SingleFetchReport]) -> Optional[Dict[str, Any]]:
        """
        fetch_match_data ve refill_missing_match_slices'ın /event isteği. Rapor yoksa bugünkü yol
        (_fetch_match_basic: hata yutulur, None döner); rapor varsa isteğin tipli sonucu rapora yazılır.
        """
        if report is None:
            return self._fetch_match_basic(match_id)
        report.event = self._fetch_event_outcome(match_id)
        return report.event.data

    def _fetch_event_outcome(self, match_id: str) -> SliceOutcome:
        """
        /event/{id} isteğinin tipli sonucu. _fetch_match_basic "maç yok" ile "istek başarısız"ı aynı None'a
        indirger; burada ayrılır: olay geldiyse SLICE_OK (data = olay), maç yoksa (404 ya da içinde olay
        olmayan yanıt) SLICE_EMPTY, istek başarısızsa SLICE_FAILED (neden ve HTTP koduyla).
        """
        url = f"{self.base_url}/event/{match_id}"
        try:
            data = make_api_request(url, raise_on_failure=True)
            event = data.get("event") if data and "event" in data else None
        except Exception as e:
            outcome = SliceOutcome.from_error(e)
            if outcome.failed:
                logger.error(f"Event request for match {match_id} failed ({outcome.reason}): {e}")
            return outcome
        if not event:
            return SliceOutcome(SLICE_EMPTY, reason="empty")
        return SliceOutcome(SLICE_OK, data=event)

    def _fetch_slice(self, match_id: str, key: str) -> SliceOutcome:
        """
        Bir detay dilimini senkron çeker ve tipli sonucunu döndürür (async yoldaki
        _fetch_endpoint_async'in karşılığı); eski adlı yardımcısı olan dilimde onu kullanır.
        Yardımcının döndürdüğü None / boş yanıt kesin "yok"tur; fırlattığı hata başarısızlıktır.
        """
        legacy = _LEGACY_SLICE_FETCHERS.get(key)
        try:
            data = getattr(self, legacy)(match_id) if legacy else self._fetch_slice_endpoint(match_id, key)
        except Exception as e:
            outcome = SliceOutcome.from_error(e)
            if outcome.failed:
                # İstek katmanı bildirdiyse yeniden sayılmaz (aynı hata nesnesi bir kez sayılır)
                request_breaker.report_exception(e)
            return outcome
        return self._answered_outcome(key, data)

    def _fetch_slice_endpoint(self, match_id: str, key: str, label: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Dilimin uç noktasını (src/sports.py, DETAIL_SLICES) çağırır.

        Args:
            match_id: Maç ID'si
            key: Dilim anahtarı
            label: Hata logunda dilimin adı (varsayılan "{key} verisi")

        Returns:
            Optional[Dict[str, Any]]: Dilim verisi; kaynak yoksa (404) None

        Raises:
            İstek başarısızsa (403/429/5xx/zaman aşımı/ağ/bozuk yanıt) istek katmanının tipli hatası.
            Hata yutulmaz: yutulursa "dilim yok" ile "istek başarısız" ayırt edilemez.
        """
        url = get_slice(key).url(self.base_url, match_id)
        try:
            return make_api_request(url, raise_on_failure=True)
        except ResourceNotFoundError:
            return None
        except Exception as e:
            logger.warning(
                f"Maç ID {match_id} için {label or key + ' verisi'} alınamadı "
                f"({request_breaker.failure_kind(e)}): {str(e)}"
            )
            raise

    def _fetch_match_statistics(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Maç istatistiklerini çeker."""
        return self._fetch_slice_endpoint(match_id, "statistics", "istatistik verisi")

    def _fetch_team_streaks(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Takım serilerini çeker."""
        return self._fetch_slice_endpoint(match_id, "team_streaks", "takım serileri")

    def _fetch_pregame_form(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Maç öncesi form verilerini çeker."""
        return self._fetch_slice_endpoint(match_id, "pregame_form", "form verisi")

    def _fetch_h2h(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Takımlar arası karşılaşma geçmişini çeker."""
        return self._fetch_slice_endpoint(match_id, "h2h", "H2H verisi")

    def _fetch_lineups(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Maç kadro bilgilerini (lineups) çeker."""
        return self._fetch_slice_endpoint(match_id, "lineups", "lineup verisi")

    def _fetch_incidents(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Maç olaylarını (goller, kartlar, devre vb.) çeker — yanıt genelde {\"incidents\": [...], \"home\": ..., \"away\": ...}."""
        return self._fetch_slice_endpoint(match_id, "incidents", "incidents verisi")

    def _match_storage_dir(self, match_id: str, basic_data: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], str]:
        """
        Maçın yazılacağı yer: (lig dizini adı, sezon dizini adı, maç dizini).

        Lig dizini `{uniqueTournament.id}_{ad}` biçimindedir; lig filtreli okuyucular bu id önekine
        bakar. uniqueTournament.id'si olmayan maç (bazı e-spor / hazırlık maçları) eskiden yalnızca
        ada göre ("Unknown_League" ya da turnuva adı) bir dizine düşüyordu. Artık sabit bir yere
        yazılır: match_details/_no_tournament/<spor>/<maç id>. Bu da lig/sezon/maç derinliğindedir,
        yani dizin ağacını gezen okuyucular (_find_match_path, yenileme taraması, CSV dışa aktarımı,
        web'deki "detayı olan maçlar") onu bulur. Böyle bir maç zaten diskteyse yeri değişmez:
        mevcut veri taşınmaz, kaydı olduğu dizinde güncellenir.
        """
        tournament_data = (basic_data.get("tournament") or {}).get("uniqueTournament") or {}
        tournament_id = tournament_data.get("id")
        if not tournament_id:
            existing = self._find_match_path(match_id)
            if existing:
                return existing
            sport_dir = _path_part(_event_sport(basic_data) or "unknown")
            return NO_TOURNAMENT_DIR, sport_dir, os.path.join(
                self.match_details_dir, NO_TOURNAMENT_DIR, sport_dir, match_id
            )

        tournament_name = tournament_data.get("name") or "Unknown_League"
        # Güvenli dizin adı (ID prefix ile standart format)
        safe_tournament_name = f"{tournament_id}_{tournament_name.replace(' ', '_').replace('/', '_')}"

        # Sezon adı için güvenli string oluştur - öncelikle name kullan, yoksa year
        season_data = basic_data.get("season") or {}
        season_id = season_data.get("id")
        season_name = season_data.get("name", "Unknown_Season")
        season_year = season_data.get("year", "Unknown_Year")
        if season_name and season_name != "Unknown_Season":
            safe_season_name = f"season_{season_name.replace(' ', '_').replace('/', '_')}"
        elif season_year and season_year != "Unknown_Year":
            safe_season_name = f"season_{season_year.replace('/', '_')}"
        else:
            safe_season_name = f"season_{season_id}"

        # Dizin yapısı: lig/sezon/maç_id
        match_dir = os.path.join(self.match_details_dir, safe_tournament_name, safe_season_name, match_id)
        return safe_tournament_name, safe_season_name, match_dir

    def _save_match_data(
        self,
        match_id: str,
        match_data: Dict[str, Any],
        outcomes: Optional[Dict[str, SliceOutcome]] = None,
    ) -> None:
        """
        Maç verilerini lig ve sezon bazında organizasyonla JSON olarak kaydeder.

        Args:
            match_id: Maç ID'si
            match_data: Kaydedilecek maç verileri
            outcomes: Bu kayıtta istenen dilimlerin tipli sonuçları. Yalnızca kesin "yok" yanıtları
                _unavailable.json'da sayılır; başarısız istekler _slice_status.json'a yazılır.
                Sonucu verilmeyen boş dilim sayılmaz (istenip istenmediği bilinmiyor).

        Raises:
            StorageError: veri diske yazılamadı. Hata yutulmaz: yutulursa hiçbir şey yazılmamışken
                maç "indirildi" sayılır. Çağıran maçı başarısız işaretler; `fatal` ise (disk dolu,
                izin yok) işi durdurur.
        """
        mid = str(match_id)
        basic_data = match_data.get("basic") or {}
        match_dir = self.match_details_dir
        try:
            league_dir_name, season_dir_name, match_dir = self._match_storage_dir(mid, basic_data)
            os.makedirs(match_dir, exist_ok=True)

            # Her veri türünü ayrı ayrı kaydet
            for data_type, data in match_data.items():
                if data is not None:
                    atomic_write_json(os.path.join(match_dir, f"{data_type}.json"), data)

            # Bitmiş maçta dilim sonuçlarını işle: kesin "yok"lar sayılır, başarısız istekler not edilir
            if MatchFetcher._is_finished_event(basic_data):
                self._update_slice_markers(match_dir, _event_sport(basic_data), match_data, outcomes or {})
        except StorageError:
            raise
        except Exception as e:
            logger.error(f"Maç ID {match_id} için veriler kaydedilemedi ({match_dir}): {str(e)}")
            raise StorageError.from_exception(e, match_dir) from e
        finally:
            store_hooks.shadow_event(self.data_dir, mid, match_dir)

        if getattr(self, "_need_cache", None) is not None:
            self._need_cache.pop(mid, None)

        logger.info(f"{league_dir_name}, {season_dir_name}, Maç ID {match_id} için veriler başarıyla kaydedildi: {match_dir}")

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
        failed_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Bir grup maç için veri çeker."""
        self.begin_job_cache()
        try:
            return self._fetch_matches_batch(match_ids, progress_callback, should_cancel, failed_callback)
        finally:
            self.end_job_cache()

    def _fetch_matches_batch(
        self,
        match_ids: List[Union[int, str]],
        progress_callback: Optional[Callable[[int, int, str], None]],
        should_cancel: Optional[Callable[[], bool]],
        failed_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        use_tqdm = True
        results = {}

        match_ids_to_process, refresh_count = self._order_by_need(match_ids)
        skipped = len(match_ids) - len(match_ids_to_process)
        if skipped:
            logger.info(f"{skipped} maç detayları tamam, atlanıyor")
        if refresh_count:
            logger.info(f"{refresh_count} maç yenileniyor (geçici kayıt)")

        n = len(match_ids_to_process)
        iterator: Any = tqdm(match_ids_to_process) if use_tqdm else match_ids_to_process

        self.rate_limit_breaker_triggered = False
        with request_breaker.scope(self.config_manager) as breaker:
            for idx, match_id in enumerate(iterator):
                if should_cancel and should_cancel():
                    logger.info("Match detail batch cancelled after %s/%s", idx, n)
                    break
                if breaker.tripped:
                    logger.warning(f"Çok fazla başarısız istek; maç detayları durduruldu, {n - idx} maç denenmedi")
                    break
                match_id = str(match_id)

                if use_tqdm:
                    iterator.set_description(f"Maç ID {match_id}")
                else:
                    logger.info(f"Maç verisi çekiliyor: ID {match_id}")

                try:
                    need = self._needs_detail_fetch(match_id)
                    if need == "refresh":
                        match_data = self.refresh_match(match_id)
                    elif need == "refill":
                        match_data = self.refill_missing_match_slices(match_id)
                        if not match_data:
                            match_data = self.fetch_match_data(match_id)
                    else:
                        match_data = self.fetch_match_data(match_id)
                except StorageError as e:
                    # Veri çekildi ama diske yazılamadı: maç başarısız; kalıcı hatada (disk dolu, izin yok) iş durur
                    logger.error(f"Maç {match_id} kaydedilemedi: {e}")
                    if failed_callback:
                        failed_callback(match_id)
                    if e.fatal:
                        raise
                    match_data = None
                else:
                    if match_data:
                        results[match_id] = match_data
                    elif failed_callback and not breaker.tripped:
                        # Devre kesildiği için gönderilmeyen istek "başarısız maç" değildir
                        failed_callback(match_id)

                if progress_callback and n > 0:
                    progress_callback(idx + 1, n, f"Match details {idx + 1}/{n}")
                # Maçlar arasında ayrıca beklenmez: hızı ortak istek bütçesi belirler (src/throttle.py)

            self.rate_limit_breaker_triggered = breaker.tripped
            self.last_status_counts = breaker.counts()

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
            match_ids = self.collect_detail_match_ids(league_id, max_seasons, only_season_ids)
            if match_ids is None:
                return False
            print(get_i18n().t("details_unique_ids_found", count=len(match_ids)))

            if not match_ids:
                print(get_i18n().t("details_no_ids"))
                return False

            match_ids_to_process = self.pending_detail_ids(match_ids)
            complete_count = len(match_ids) - len(match_ids_to_process)
            if complete_count:
                print(get_i18n().t("details_some_complete", count=complete_count))

            if not match_ids_to_process:
                print(get_i18n().t("details_all_complete"))
                return True

            total_success = self.fetch_detail_ids(match_ids_to_process, progress_callback, should_cancel)
            return total_success > 0

        except StorageError:
            raise  # disk dolu / izin yok: "başarısız" deyip geçmek yerine çağırana net hata
        except Exception as e:
            logger.error(f"Tüm maç detayları çekilirken hata: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def collect_detail_match_ids(
        self,
        league_id: Optional[str] = None,
        max_seasons: int = 0,
        only_season_ids: Optional[List[int]] = None,
    ) -> Optional[List[str]]:
        """Sezon özet CSV'lerindeki benzersiz maç ID'leri; maç ya da lig dizini yoksa None."""
        matches_dir = os.path.join(self.data_dir, "matches")
        if not os.path.exists(matches_dir):
            logger.warning("Maç dizini bulunamadı!")
            return None

        league_dirs = []
        # Belirli bir lig seçilmişse sadece o ligi işle
        if league_id:
            print(get_i18n().t("details_fetching_league", league_id=league_id))
            for dir_name in os.listdir(matches_dir):
                if dir_name.startswith(f"{league_id}_"):
                    league_dirs.append(dir_name)
                    break

            if not league_dirs:
                print(get_i18n().t("details_league_dir_missing", league_id=league_id))
                return None
        else:
            print(get_i18n().t("details_fetching_all"))
            league_dirs = [dir_name for dir_name in os.listdir(matches_dir)
                          if os.path.isdir(os.path.join(matches_dir, dir_name))]

        print(get_i18n().t("details_league_count", count=len(league_dirs)))

        match_ids: List[str] = []
        for league_dir in league_dirs:
            league_path = os.path.join(matches_dir, league_dir)
            if not os.path.isdir(league_path):
                continue

            print("\n" + get_i18n().t("details_league_dir", name=league_dir))

            summary_files = self._season_summary_files(league_path, only_season_ids, max_seasons)

            # Özet dosyalarından maç ID'lerini çıkar
            current_ids = []
            for file_path in summary_files:
                if os.path.isfile(file_path):
                    ids_from_csv = self._extract_match_ids_from_csv(file_path)
                    if ids_from_csv:
                        current_ids.extend(ids_from_csv)

            if current_ids:
                print(get_i18n().t("details_league_ids_found", count=len(current_ids)))
                match_ids.extend(current_ids)

        # Tekrarlanan ID'leri temizle (sırayı koru)
        return list(dict.fromkeys(match_ids))

    def pending_detail_ids(self, match_ids: List[str]) -> List[str]:
        """Detay dilimleri eksik ya da kısmi olan maçlar, ardından yenilenecek (geçici) maçlar."""
        return self._order_by_need(match_ids)[0]

    def fetch_detail_ids(
        self,
        match_ids_to_process: List[str],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        failed_callback: Optional[Callable[[str], None]] = None,
    ) -> int:
        """Verilen maçların detaylarını 100'lük paralel batch'lerle çeker; başarılı maç sayısını döndürür.

        Devre kesilirse (rate limit) kalan batch'ler atlanır ve rate_limit_breaker_triggered True kalır.
        Kesici tüm batch'ler boyunca aynıdır (çağıran kurduysa işin kesicisi): sayaçlar batch başına sıfırlanmaz.
        Kalıcı depolama hatasında (disk dolu, izin yok) StorageError fırlatılır.
        """
        self.last_storage_error = None
        try:
            with request_breaker.scope(self.config_manager):
                return self._fetch_detail_ids(match_ids_to_process, progress_callback, should_cancel, failed_callback)
        except StorageError as e:
            # Çağıran hatayı yutsa bile (etkileşimli menüler) neden okunabilsin: main.py çıkışta bildirir
            self.last_storage_error = e
            raise

    def _fetch_detail_ids(
        self,
        match_ids_to_process: List[str],
        progress_callback: Optional[Callable[[int, int, str], None]],
        should_cancel: Optional[Callable[[], bool]],
        failed_callback: Optional[Callable[[str], None]],
    ) -> int:
        self.rate_limit_breaker_triggered = False
        batch_size = 100  # Her seferde kaç maç işleneceği
        total_success = 0
        total_attempts = len(match_ids_to_process)
        print("\n" + get_i18n().t("details_total_to_fetch", count=total_attempts))
        if progress_callback:
            progress_callback(0, total_attempts, f"Match details 0/{total_attempts}")

        for i in range(0, len(match_ids_to_process), batch_size):
            if should_cancel and should_cancel():
                logger.info("fetch_detail_ids cancelled before batch at index %s", i)
                break
            batch = match_ids_to_process[i:i+batch_size]
            current_batch = i // batch_size + 1
            total_batches = (len(match_ids_to_process) - 1) // batch_size + 1
            start_index = i + 1
            end_index = min(i + len(batch), total_attempts)

            print(
                "\n"
                + get_i18n().t(
                    "details_batch_start",
                    current=current_batch,
                    total=total_batches,
                    size=len(batch),
                    start=start_index,
                    end=end_index,
                    all=total_attempts,
                )
            )

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
                failed_callback=failed_callback,
            )

            if results:
                success_count = len(results)
                total_success += success_count
                print(get_i18n().t("details_batch_done", current=current_batch, ok=success_count, size=len(batch)))
            if self.rate_limit_breaker_triggered:
                i18n = get_i18n()
                print(i18n.t("error_rate_limit_detected", count=len(results) if results else 0))
                if self.last_rate_limit_headers:
                    logger.warning("Son başarısız isteklerden header/debug özeti:")
                    for idx, header_info in enumerate(self.last_rate_limit_headers[-20:], start=1):
                        logger.warning(f"{idx}. match_id={header_info.get('match_id')} error={header_info.get('error')}")
                os.environ["APP_EXIT_CODE"] = "2"
                break

        # Genel başarı oranı
        success_rate = (total_success / total_attempts) * 100 if total_attempts > 0 else 0
        print(
            "\n"
            + get_i18n().t("details_finished", ok=total_success, total=total_attempts, rate=f"{success_rate:.1f}")
        )
        return total_success

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
