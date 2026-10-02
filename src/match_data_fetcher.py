from src.i18n import get_i18n
"""
SofaScore API'sinden detaylı maç verilerini çeken modül.
"""

import os
import json
import csv
import time  # testler `src.match_data_fetcher.time.sleep` yolunu yamalar; meşgul depoda bekleme de bununla
import random
import datetime as dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Union, Tuple
from pathlib import Path
import asyncio
from collections import Counter
import pandas as pd
from tqdm import tqdm

from src import breaker as request_breaker
from src.client import base_url
from src.config_manager import ConfigManager
from src.exceptions import ResourceNotFoundError, StorageError
# Maçlar Store'a yazılır (`open_store(...).events.put` / `observe`, docs/design/01-storage.md 2.3 ve 6.2) ve
# Store'dan okunur. Paket kökü üzerinden: cephe ilk çağrıda yüklenir.
from src import store as store_hooks
from src.utils import make_api_request, ensure_directory
from src.match_fetcher import MatchFetcher
from src.sports import DETAIL_SLICES, event_sport_slug, get_slice, slices_for
from src.status import OBSERVATION_KEY, observation_record
# SCORE_CHANGES_FILE: eski düzenin değişiklik günlüğü (yalnızca okunur); eski import'lar için burada da durur
from src.refresh import SCORE_CHANGES_FILE as SCORE_CHANGES_FILE, change_row, diff_basic
from src.paths import league_dir_name
from src.services.export import ExportService, ExportSpec
from src.services import planning
from src.services.query import QueryService, RefreshPolicy
from src.services.status import only_finished_setting
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
# (403/429/5xx/zaman aşımı/ağ/bozuk yanıt) sayılmaz; dilimin hata kaydına yazılır ve sonraki
# çalıştırmada yeniden denenir. Sayaçlar ve hata kayıtları Store'dadır (manifest, `event_slices`).
UNAVAILABLE_AFTER_ATTEMPTS = 2

# Eski düzenin dosya ve dizin adları. Bu modül onları artık yazmaz (maçlar v3 düzenine yazılır); eski düzendeki
# kayıtları Store okur (src/store/legacy.py, aynı adlar; tests/test_store_legacy.py eşitliği denetler).
UNAVAILABLE_FILE = "_unavailable.json"  # {dilim: "yok" sayısı}
SLICE_STATUS_FILE = "_slice_status.json"  # {dilim: {"empty": {...}, "error": {...}}}
NO_TOURNAMENT_DIR = "_no_tournament"  # uniqueTournament.id'si olmayan maçlar: match_details/_no_tournament/<spor>/<id>

# Depo meşgulken (başka bir süreç yazıyor, StoreBusy) bir yazma bu kadar kez, artan beklemeyle yeniden denenir
STORE_BUSY_ATTEMPTS = 4
STORE_BUSY_FIRST_WAIT = 0.5


def _v3_event_dir(data_dir: str, event_id: int) -> str:
    """
    Maçın v3 dizini: `v3/events/<id // 1000000>/<(id // 1000) % 1000, 3 hane>/<id>` (src/store/layout.py
    `event_dir`; bu modül Store'un alt modüllerini içe aktarmaz, tests/test_storage_errors.py eşitliği denetler).
    Yalnızca gösterilir (`_find_match_path`); dosyalara Store dokunur.
    """
    return os.path.join(data_dir, "v3", "events", str(event_id // 1_000_000), f"{(event_id // 1000) % 1000:03d}",
                        str(event_id))


def _parse_utc(value: Any) -> Optional[dt.datetime]:
    """ISO 8601 zaman metni → UTC zaman; okunamıyorsa None. Saat dilimi olmayan zaman UTC sayılır (Store'un kuralı)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=dt.timezone.utc)


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


def _canonical_id(text: str) -> Optional[int]:
    """Kurallı maç kimliği metni ("123") → sayı; "007", "abc", "-1" ve kataloğun sınırını aşan sayı None."""
    if not (text.isascii() and text.isdigit()) or str(int(text)) != text or int(text) > _MAX_EVENT_ID:
        return None
    return int(text)


def _stored_observation(row: "EventRow") -> Optional[Dict[str, Any]]:
    """
    Katalog satırından gözlemin (eski düzende observation.json) karşılığı; kayıt gözlemsizse (eski kayıt) None.
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

    # --- kayıtlı maçların okunması ve yazılması: depo üzerinden (docs/design/01-storage.md 2.3, 5.2, 5.3, 6.2;
    # plan maddeleri RD-1 ve ST-21) ---
    #
    # Bir maçın yeri ve yükleri kataloğa sorulur (`Store.events`); dizin ağacı gezilmez. Yazmalar
    # `Store.events.put` / `observe` ile v3 düzenine gider (`v3/events/.../<id>/`): yeni maç orada kurulur, eski
    # düzende duran maç ilk yazmasında önce v3'e yükseltilir (eski dizine dokunulmaz, silinmez). Katalog her
    # yazmada aynı kritik bölümde güncellenir. Kayıt, olay yükü herhangi bir düzende saklanan maçtır. Eski,
    # dizini dosya dosya okuyan okuyuculardan farklar yalnızca eski biçimli kayıtlarda görünür (01-storage.md
    # 5.1 ve 5.2):
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
        Olay yükü saklanan maçın katalog satırı (v3 ya da eski düzen); öyle bir kayıt yoksa None. Yalnızca bir
        program sayfasından bilinen maç (yükü yok) ve kurallı bir kimlik olmayan metin ("007", "abc") kayıt değildir.
        """
        event_id = _canonical_id(str(match_id))
        if event_id is None:
            return None
        row = self._store().events.get(event_id)
        if row is None or not row.has_event_payload:
            return None
        if row.layout == _LEGACY_LAYOUT and not row.path:
            return None
        return row

    def _record_location(self, row: "EventRow") -> Optional[Tuple[Optional[str], Optional[str], str]]:
        """Kaydın yeri: eski düzende `_legacy_location`, v3'te (None, None, maçın v3 dizini)."""
        if row.layout == _LEGACY_LAYOUT:
            return self._legacy_location(row)
        return (None, None, _v3_event_dir(self.data_dir, row.id))

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
        Kayıtlı maçın yeri: eski düzende (lig dizini adı, sezon dizini adı, maç dizini), düz kayıtta
        (`match_details/<id>`) ilk ikisi None; v3 düzeninde (None, None, maçın v3 dizini). Kayıt yoksa None. Yeri
        katalog söyler (birincil anahtar araması), ağaç gezilmez.
        """
        row = self._stored_event(match_id)
        return self._record_location(row) if row is not None else None

    def _build_match_index(self) -> Dict[str, Tuple[Optional[str], Optional[str], str]]:
        """match_id → konum (`_find_match_path`): detayı kayıtlı bütün maçlar (katalogdan). İşler bunu kullanmaz."""
        index: Dict[str, Tuple[Optional[str], Optional[str], str]] = {}
        for row in self._store().events.iter(store_hooks.EventQuery(has_details=True)):
            if row.layout == _LEGACY_LAYOUT and not row.path:
                continue
            location = self._record_location(row)
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
            yanıtı değildir: sayılmaz, dilimin hata kaydına yazılır ve dilim sonraki
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

    # --- Store'a yazma ---------------------------------------------------------------------------------
    #
    # Dilim sonuçları `Store.events.put`'a verilir; "veri yok" sayaçları ve hata kayıtları manifestte ve
    # katalogda tutulur (eski düzendeki `_unavailable.json` / `_slice_status.json`'ın karşılığı, bölüm 2.3).
    # Bugünkü kurallar çağrının bağımsız değişkenleriyle korunur:
    #   - sayaçlar ve hata kayıtları yalnızca bitmiş maçta ve sporun `required` dilimlerinde tutulur
    #     (`count_empties`); öteki dilimlerin ve bitmemiş maçların yalnızca gövdesi olan yükü saklanır;
    #   - verisi olan dilimin "yok" sayacı ve hata kaydı silinir; 404 ya da içinde veri olmayan 200 sayılır
    #     (gövde varsa o da saklanır); başarısız istek sayılmaz, hata kaydına yazılır; açık devre kesici
    #     yüzünden gönderilmeyen istek yok sayılır (Store `failed` / `breaker` sonucunu atlar).

    def _put_retrying(self, event_id: int, what: str, write: Callable[[], Any]) -> Any:
        """
        Store'a bir yazma. Depo meşgulse (başka bir süreç yazıyor, StoreBusy) STORE_BUSY_ATTEMPTS kez, artan
        beklemeyle yeniden denenir; sonra StoreBusy (kalıcı olmayan bir depolama hatası) çağırana çıkar. Öteki
        depolama hataları (StoreError, bir StorageError) olduğu gibi çıkar; beklenmeyen hata (ör. Store'un
        reddettiği bir yük, ValueError) kalıcı olmayan bir StorageError'a çevrilir: yalnızca o maç başarısızdır.
        """
        wait = STORE_BUSY_FIRST_WAIT
        for attempt in range(STORE_BUSY_ATTEMPTS):
            try:
                return write()
            except store_hooks.StoreBusy:
                if attempt == STORE_BUSY_ATTEMPTS - 1:
                    raise
                logger.warning(f"The data store is busy (another process is writing); retrying {what} "
                               f"(match {event_id}) in {wait:.1f} s")
                time.sleep(wait)
                wait *= 2
            except StorageError:
                raise
            except Exception as e:
                logger.error(f"Could not store {what} (match {event_id}): {e}")
                raise StorageError.from_exception(e, os.path.join(self.data_dir, "v3", "events")) from e
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _slice_outcomes(
        match_data: Mapping[str, Any],
        outcomes: Mapping[str, SliceOutcome],
        counted: Tuple[str, ...],
    ) -> Tuple[Dict[str, SliceOutcome], Tuple[str, ...]]:
        """
        `match_data`'daki dilimlerin `put` sonuçları (`basic` ve gözlem hariç) ve "yok" yanıtı sayılacak dilimler
        (`put`'un `count_empties`'i). counted: sayaçları tutulan dilimler (bitmiş maçta sporun `required`
        dilimleri; bitmemiş maçta boş); yalnızca bu çağrıda sonucu verilenler sayılır.

          - verisi olan dilim (src.slices.match_detail_slice_present): `ok`, bu çağrıda istenmemiş olsa da (eski
            yazıcı onun işaretlerini de siliyordu); yükü değişmediyse Store dosyasını yeniden yazmaz;
          - sayaçları tutulan dilim, sonucu verilmişse: o sonuç (içinde veri olmayan 200'ün gövdesiyle);
          - öteki dilim: içinde veri olmayan bir gövdesi varsa `empty` olarak, sayılmadan saklanır (eski yazıcı her
            gövdeyi dosyaya yazardı); gövdesi olmayan dilim yazmaya girmez (Store'da kaydı açılmaz).
        """
        found: Dict[str, SliceOutcome] = {}
        counts: List[str] = []
        for key, data in match_data.items():
            if key in ("basic", OBSERVATION_KEY):
                continue
            outcome = outcomes.get(key)
            if data is not None and match_detail_slice_present(key, match_data):
                found[key] = SliceOutcome(SLICE_OK, data=data)
            elif key in counted and outcome is not None:
                if outcome.status != SLICE_EMPTY:
                    found[key] = outcome  # başarısız (devre kesici dahil) ya da gönderilmemiş: Store'un kuralı
                else:
                    found[key] = SliceOutcome(SLICE_EMPTY, data=data, reason=outcome.reason,
                                              http_status=outcome.http_status)
                    counts.append(key)
            elif data is not None:
                found[key] = SliceOutcome(SLICE_EMPTY, data=data, reason="empty")
        return found, tuple(counts)

    def _expected_slice_keys(self, event_id: int, sport: Optional[str]) -> List[str]:
        """
        Beklenen dilimler: o sporun `required` dilimleri (src/services/planning.py `expected_slice_keys`),
        yeterince denenip hep boş gelenler hariç (kesin ve doğrulanmamış "yok" sayısının toplamı
        UNAVAILABLE_AFTER_ATTEMPTS'e ulaşmış olanlar). Store'dan okunur.
        """
        settled = {info.key for info in self._store().events.slices(event_id)
                   if not info.sub and info.settled_empty(UNAVAILABLE_AFTER_ATTEMPTS)}
        return [key for key in planning.expected_slice_keys(sport) if key not in settled]

    def reset_unavailable_markers(
        self,
        league_id: Optional[Union[int, str]] = None,
        include_confirmed: bool = False,
    ) -> Dict[str, int]:
        """
        "Bu dilim bu maçta yok" işaretlerini yeniden denetime açar (--recheck-unavailable; web katmanı
        da çağırabilir). Ağ isteği yapmaz: işaretleri geri alır, dilimler sonraki indirmede yeniden istenir.
        İşi Store yapar (`Store.events.reset_empty_markers`; eski düzendeki maç, sayaçları değişecekse önce v3'e
        yükseltilir, eski dizinine dokunulmaz).

        Eski sürümler başarısız isteği de (403/429/5xx/zaman aşımı) "yok" sayıyordu; o işaretlerin hangisinin
        geçici hata olduğu bilinemez. Varsayılan olarak yalnızca kesin yanıtla doğrulanmamış sayımlar geri
        alınır; bu sürümün 404 / boş 200 ile saydıkları kalır. Bu yüzden işlem tekrarlanabilir: yeniden
        denetimden sonra ikinci kez çalıştırmak hiçbir şeyi değiştirmez.

        include_confirmed=True: doğrulanmış işaretler de silinir (ör. SofaScore veriyi sonradan eklediyse).
        league_id: yalnızca bu turnuvanın maçları (maçın turnuvasına bakılır; turnuvası olmayan maç girmez).

        Returns: {"matches": işareti değişen maç, "slices": yeniden istenecek dilim, "scanned": sayacı olan maç}
        """
        scope = None
        if league_id is not None:
            tournament = _canonical_id(str(league_id))
            if tournament is None:
                return {"matches": 0, "slices": 0, "scanned": 0}
            scope = store_hooks.Scope(tournament_ids=(tournament,))
        result = dict(self._put_retrying(
            0, "the marker reset", lambda: self._store().events.reset_empty_markers(
                scope, include_confirmed=include_confirmed, threshold=UNAVAILABLE_AFTER_ATTEMPTS)))
        cache = getattr(self, "_need_cache", None)
        if cache is not None and result.get("matches"):
            cache.clear()  # yeniden beklenen dilimi olan maçların ihtiyacı değişti
        return result

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
        """Bir maçın ihtiyacı, katalogdan (`_compute_detail_needs`)."""
        return self._compute_detail_needs([str(mid)])[str(mid)]

    def _compute_detail_needs(self, match_ids: List[str]) -> Dict[str, str]:
        """
        Maçların ihtiyacı: kural src/services/planning.py'dedir (`compute_need`); maçların katalogdaki durumları
        birkaç sorguyla okunur (`planning.event_needs`), hiçbir dosya okunmaz. Kayıt, olay yükü herhangi bir
        düzende saklanan maçtır (`_stored_event`); kurallı bir kimlik olmayan metin ("007", "abc") kayıt
        değildir. Katalog dosyalarla eşit değilse CatalogNotCurrent (plan yapılmaz).
        """
        ids = {mid: _canonical_id(mid) for mid in match_ids}
        store = self._store()
        QueryService(store).require_current()
        needs = planning.event_needs(store, [event_id for event_id in ids.values() if event_id is not None],
                                     RefreshPolicy.current(), threshold=UNAVAILABLE_AFTER_ATTEMPTS)
        return {mid: needs.get(event_id, "full") if event_id is not None else "full" for mid, event_id in ids.items()}

    def _order_by_need(self, match_ids: List[Any]) -> Tuple[List[Any], int]:
        """İşlenecek maçlar: önce full/refill, sonra refresh (`planning.order_by_need`). İkinci değer yenilenecek maç sayısı."""
        self._prepare_needs([str(mid) for mid in match_ids])
        return planning.order_by_need(match_ids, {str(mid): self._needs_detail_fetch(str(mid)) for mid in match_ids})

    def _prepare_needs(self, match_ids: List[str]) -> None:
        """
        İş önbelleği açıksa (`begin_job_cache`) önbellekte olmayan maçların ihtiyacı tek seferde hesaplanır ve
        önbelleğe yazılır; ardından gelen `_needs_detail_fetch` çağrıları kataloğa tek tek sormaz.
        """
        cache = getattr(self, "_need_cache", None)
        if cache is None:
            return
        wanted = [mid for mid in dict.fromkeys(match_ids) if mid not in cache]
        if wanted:
            cache.update(self._compute_detail_needs(wanted))

    def refresh_match(self, match_id: Union[int, str]) -> Optional[Dict[str, Any]]:
        """
        Geçici kaydı yeniler: yalnızca /event/{id} çekilir ve Store'a gözlem olarak yazılır
        (`Store.events.observe`). Yük aynıysa yalnızca gözlem anı ilerler. Yük değiştiyse saklanır; karşılaştırılan
        alanlardan biri değiştiyse (src/refresh.py `diff_basic`) değişim eski ve yeni değeriyle değişiklik
        günlüğüne (`changes/<yyyy>-<mm>.jsonl`) aynı kritik bölümde eklenir (`change_row`). COMPLETED → VOID olursa
        kayıt silinmez, gözleme yapışkan `status_regressed` bayrağı yazılır. Eski düzendeki kayıt önce v3'e
        yükseltilir (eski dizine dokunulmaz).
        """
        mid = str(match_id)
        row = self._stored_event(mid)
        if row is None:
            return None
        data = self._load_match_data_from_dir("", mid)
        old = data.get("basic")
        if not old:
            return None
        new = self._fetch_match_basic(mid)
        if not new:
            logger.warning(f"Refresh of match {mid}: /event could not be fetched")
            return None

        stored = data.get(OBSERVATION_KEY)
        obs = observation_record(new)
        if isinstance(stored, dict) and stored.get("status_regressed"):
            obs["status_regressed"] = True
        found: Dict[str, Any] = {"changed": {}, "row": None}

        def on_event_change(previous: Optional[Mapping[str, Any]], payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
            # Kritik bölümde çalışır: Store'a yazmaz, yalnızca karşılaştırır
            if not previous:
                return None
            changed = diff_basic(dict(previous), dict(payload))
            found["changed"] = changed
            if not changed:
                return None
            found["row"] = change_row(dict(previous), dict(payload), changed,
                                      _event_sport(dict(payload)) or _event_sport(dict(previous)))
            return found["row"]

        self._put_retrying(row.id, "the refreshed match page", lambda: self._store().events.observe(
            row.id, new, on_event_change=on_event_change))
        changed = found["changed"]
        if changed:
            if found["row"] is not None and found["row"].get("status_regressed"):
                obs["status_regressed"] = True
                logger.warning(f"Match {mid} counted as played is now {new.get('status')}; the record is kept")
            data["basic"] = new
            logger.info(f"Match {mid} refreshed: {len(changed)} fields changed ({', '.join(list(changed)[:5])})")
        data[OBSERVATION_KEY] = obs

        self.last_refresh_changed = bool(changed)
        if getattr(self, "_need_cache", None) is not None:
            self._need_cache.pop(mid, None)
        listener = getattr(self, "refresh_listener", None)
        if listener:
            listener(mid, bool(changed))
        return data

    def refresh_due_ids(self, league_id: Optional[Union[int, str]] = None) -> List[str]:
        """
        Kayıtlı maçlardan yenilenmesi gerekenler (--refresh-only). Eksik dilimli maçlar dahil değil.

        Katalogdan (`planning.refresh_due_events`; karar `compute_need`'in): her düzendeki kayıtlar, eski düzenin düz ve `_no_tournament/`
        dizinlerindekiler de. league_id: maçın turnuvası (dizin adına bakılmaz). Sıra, eski düzen yazıcısının
        dizinlerinin yol sırasıdır (eski ağaç gezintisinin sırası: lig dizini, sezon dizini, maç kimliği metin
        olarak): eski düzendeki kayıtta kendi yolu, v3'e yükseltilmiş kayıtta eski yolu, yalnızca v3'te duran
        kayıtta eski yazıcının o maç için seçeceği adlar (src/services/export.py `legacy_folders`).
        """
        tournament_ids: Tuple[int, ...] = ()
        if league_id is not None:
            tournament = _canonical_id(str(league_id))
            if tournament is None:
                return []
            tournament_ids = (tournament,)
        store = self._store()
        QueryService(store).require_current()
        rows = planning.refresh_due_events(store, RefreshPolicy.current(), tournament_ids=tournament_ids,
                                           threshold=UNAVAILABLE_AFTER_ATTEMPTS)
        ids = [str(row.id) for row in sorted(rows, key=self._legacy_order)]
        cache = getattr(self, "_need_cache", None)
        if cache is not None:
            cache.update((mid, "refresh") for mid in ids)
        return ids

    def _legacy_order(self, row: "EventRow") -> List[str]:
        """Kaydın eski düzen yolu, parça parça (`refresh_due_ids`'in sırası)."""
        from src.services.export import legacy_folders

        if row.layout == _LEGACY_LAYOUT:
            return str(row.path).strip("/").split("/")
        if row.legacy_path:
            return str(row.legacy_path).strip("/").split("/")
        try:
            basic = self._store().events.payload(row.id)
        except StorageError:
            basic = None
        league, season = legacy_folders(row, basic)
        if league is None:
            return [os.path.basename(self.match_details_dir), str(row.id)]
        season_dir = season if league == NO_TOURNAMENT_DIR else f"season_{season}"
        return [os.path.basename(self.match_details_dir), league, str(season_dir), str(row.id)]

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
        Saklanan maçta eksik API dilimlerini tamamlar (yeni maç için fetch_match_data kullanın). Eksik: sporun
        beklenen dilimlerinden (`_expected_slice_keys`) verisi olmayanlar. Maçın sayfası (/event) da yenilenir.

        report verilirse /event isteğinin ve istenen dilimlerin sonucu ona yazılır (SingleFetchReport).
        """
        mid = str(match_id)
        row = self._stored_event(mid)
        if row is None:
            return None
        match_data = self._load_match_data_from_dir("", mid)
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
            for k in self._expected_slice_keys(row.id, _event_sport(basic_live))
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

    def _save_match_data(
        self,
        match_id: str,
        match_data: Dict[str, Any],
        outcomes: Optional[Dict[str, SliceOutcome]] = None,
    ) -> None:
        """
        Maçın verilerini Store'a yazar (`Store.events.put`, v3 düzeni: `v3/events/.../<id>/`). Eski düzende
        duran maç önce v3'e yükseltilir; eski dizine dokunulmaz. Bütün yazma tek bir işlemdir: katalog aynı
        kritik bölümde güncellenir.

        Args:
            match_id: Maç ID'si
            match_data: Kaydedilecek maç verileri: `basic` (/event yükü), varsa gözlem (`observation`;
                `observed_at_utc` yükün alındığı an olarak saklanır, yoksa şimdi) ve dilimler (anahtar → yük ya
                da None).
            outcomes: Bu kayıtta istenen dilimlerin tipli sonuçları. Bitmiş maçta sporun `required` dilimleri
                için yalnızca kesin "yok" yanıtları sayılır; başarısız istekler dilimin hata kaydına yazılır.
                Sonucu verilmeyen boş dilim sayılmaz (istenip istenmediği bilinmiyor). Kurallar:
                `_slice_outcomes`.

        Raises:
            StorageError: veri diske yazılamadı (Store'un hatası, StoreError). Hata yutulmaz: yutulursa hiçbir
                şey yazılmamışken maç "indirildi" sayılır. Çağıran maçı başarısız işaretler; `fatal` ise (disk
                dolu, izin yok) işi durdurur.
        """
        mid = str(match_id)
        basic_data = match_data.get("basic") or {}
        event_id = _canonical_id(mid)
        if event_id is None:
            raise StorageError(f"Match id is not a canonical event id: {mid!r}", detail="invalid match id")
        finished = MatchFetcher._is_finished_event(basic_data)
        sport = _event_sport(basic_data)
        counted = tuple(detail.key for detail in slices_for(sport, required_only=True)) if finished else ()
        observation = match_data.get(OBSERVATION_KEY)
        observed_at = _parse_utc(observation.get("observed_at_utc")) if isinstance(observation, dict) else None

        slices, counts = self._slice_outcomes(match_data, outcomes or {}, counted)
        put: Dict[str, SliceOutcome] = {}
        if basic_data:
            put["event"] = SliceOutcome(SLICE_OK, data=basic_data, fetched_at=observed_at)
        put.update(slices)
        result = self._put_retrying(event_id, "the match details", lambda: self._store().events.put(
            event_id, put, count_empties=counts if counts else False))

        if getattr(self, "_need_cache", None) is not None:
            self._need_cache.pop(mid, None)

        how = "promoted from the old layout and stored" if result.promoted else "stored"
        logger.info(f"Match {mid} {how} ({len(result.written)} files written)")

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
        Saklanan maçları `match_details/processed/` altına CSV olarak yazar (ExportService, `legacy-wide-csv`).

        Args:
            match_ids: Yalnızca bu maçlar (None: detayı saklanan bütün maçlar). Kimlik olamayan değer atlanır.
            separate_by_league: True ise lig başına bir dosya

        Returns:
            Dosyanın yolu (ya da yolları); yazılacak maç yoksa boş metin (ya da boş liste). Depolama hatası
            çağırana çıkar.
        """
        event_ids: Tuple[int, ...] = ()
        if match_ids is not None:
            event_ids = tuple(int(mid) for mid in match_ids if str(mid).strip().isdigit())
            if not event_ids:
                logger.warning("No downloaded match to export to CSV")
                return [] if separate_by_league else ""
        service = ExportService(self._store())
        spec = ExportSpec(event_ids=event_ids)
        if separate_by_league:
            return [r.path for r in service.write_legacy_csv_by_league(self.processed_dir, spec) if r.path]
        result = service.write_legacy_csv(self.processed_dir, spec)
        return result.path if result is not None and result.path else ""

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
        """
        Detayı indirilecek maçların adayları: programlarda ve sezon özetlerinde geçen benzersiz maç kimlikleri,
        katalogdan (QueryService.detail_candidates). Lig başına sezon kimliği büyükten küçüğe, sezon içinde
        başlangıç zamanı sırasıyla; ligler kimlik sırasıyla. "Yalnızca bitmiş maçlar" ayarı okurken uygulanır
        (maç listeleriyle aynı kural). İstenen ligin listelerde hiç maçı yoksa None (eskiden: ligin `matches/`
        dizini yoksa; `matches/` dizini hiç yoksa lig verilmeden de None dönüyordu, şimdi boş liste).
        """
        service = QueryService(self._store())
        service.require_current()
        tournament = _canonical_id(str(league_id)) if league_id else None
        candidates = service.detail_candidates(
            tournament, only_finished=only_finished_setting(), max_seasons=max_seasons,
            only_season_ids=only_season_ids) if tournament is not None or not league_id else {}

        if league_id:
            print(get_i18n().t("details_fetching_league", league_id=league_id))
            if tournament not in candidates:
                print(get_i18n().t("details_league_dir_missing", league_id=league_id))
                return None
        else:
            print(get_i18n().t("details_fetching_all"))

        print(get_i18n().t("details_league_count", count=len(candidates)))

        match_ids: List[str] = []
        for tid, event_ids in candidates.items():
            print("\n" + get_i18n().t("details_league_dir", name=league_dir_name(tid, self._league_name(tid))))
            if event_ids:
                print(get_i18n().t("details_league_ids_found", count=len(event_ids)))
                match_ids.extend(str(event_id) for event_id in event_ids)

        # Tekrarlanan ID'leri temizle (sırayı koru)
        return list(dict.fromkeys(match_ids))

    def _league_name(self, league_id: int) -> Optional[str]:
        """Ligin dizin adındaki adı (yazıcılarla aynı kaynak): yapılandırmadaki ad, yoksa katalogdaki turnuva adı."""
        try:
            name = self.config_manager.get_league_by_id(league_id)
        except Exception:
            name = None
        if isinstance(name, str) and name:
            return name
        found = self._store().entities.tournament(league_id)
        return found.name if found is not None and found.name else None

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

    # CSV: dışa aktarma servisine (src/services/export.py, `legacy-wide-csv` profili) yönlendirilir
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
