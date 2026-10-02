from src.i18n import get_i18n
"""
SofaScore API'sinden detaylı maç verilerini çeken modül.

P13'ten beri maç detaylarını indiren tek yol src/services/pipeline.py'deki FetchPipeline'dır; buradaki eski adlı
giriş noktaları (toplu indirme, seçilen maçlar, tek maç, refill, yenileme) yalnızca iş birimlerini kurar. Kayıtların
okunması, yazıcı (`_save_match_data`), ihtiyaç hesabının önbelleği ve CSV / rapor yardımcıları P15'e kadar burada durur.
"""

import os
import json
import csv
import time  # noqa: F401  testler `src.match_data_fetcher.time.sleep` yolunu yamalar (time modülünün kendisi)
import datetime as dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Union, Tuple
from pathlib import Path
from collections import Counter
import pandas as pd
from tqdm import tqdm

from src import breaker as request_breaker
from src.client import base_url
from src.config_manager import ConfigManager
from src.exceptions import StorageError
# Maçlar Store'a yazılır (`open_store(...).events.put` / `observe`, docs/design/01-storage.md 2.3 ve 6.2) ve
# Store'dan okunur. Paket kökü üzerinden: cephe ilk çağrıda yüklenir.
from src import store as store_hooks
from src.utils import ensure_directory
from src.match_fetcher import MatchFetcher
from src.sports import DETAIL_SLICES, event_sport_slug, slices_for
from src.status import OBSERVATION_KEY, observation_record
# SCORE_CHANGES_FILE: eski düzenin değişiklik günlüğü (yalnızca okunur); eski import'lar için burada da durur
from src.refresh import SCORE_CHANGES_FILE as SCORE_CHANGES_FILE
from src.paths import league_dir_name
from src.services.export import ExportService, ExportSpec
from src.services import pipeline, planning
from src.services.planning import WorkItem
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
# (src/services/pipeline.py `put_retrying`)
STORE_BUSY_ATTEMPTS = pipeline.STORE_BUSY_ATTEMPTS
STORE_BUSY_FIRST_WAIT = pipeline.STORE_BUSY_FIRST_WAIT


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
        Çekimi SofaScore tarafı engellediyse o başarısız sonuç, aksi halde None (src/services/pipeline.py
        `upstream_failure`): /event isteği başarısız olduysa onun sonucu; dilim istendiyse ve hiçbiri yanıt
        almadıysa en sık görülen başarısızlık. Kesin "yok" (404, içinde veri olmayan 200) bir yanıttır.
        """
        return pipeline.upstream_failure(self.event, self.slices)


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

    def _answered_outcome(self, key: str, data: Any) -> SliceOutcome:
        """Yanıt gelen dilim gövdesinin sonucu: boru hattının kuralı (src/services/pipeline.py `answered_outcome`)."""
        return pipeline.answered_outcome(key, SliceOutcome(SLICE_OK, data=data))

    # --- boru hattı (src/services/pipeline.py) ---------------------------------------------------------
    #
    # Maç detaylarını indiren tek yol FetchPipeline'dır; aşağıdaki eski giriş noktaları (toplu indirme, seçilen
    # maçlar, tek maç, refill, yenileme) yalnızca iş birimlerini kurar ve boru hattına verir. Eşitleme servisi ve
    # terminal menüsü onları bu adlarla çağırır; sınıf P15'te kalkar.

    def _pipeline(self, concurrency: Optional[int] = None) -> pipeline.FetchPipeline:
        """Bu veri dizininin boru hattı; "yalnızca bitmiş maçlar" ayarı çağrı anında okunur (src.utils)."""
        from src import utils

        return pipeline.FetchPipeline(
            self._store(),
            concurrency=concurrency if concurrency is not None else self.config_manager.get_max_concurrent(),
            only_finished=bool(utils.FETCH_ONLY_FINISHED),
        )

    def _after_result(self, result: pipeline.ItemResult) -> None:
        """Her sonuçtan sonra: işin önbelleği, yenileme dinleyicisi (web iş kartı sayacı)."""
        cache = getattr(self, "_need_cache", None)
        if cache is not None and result.put is not None:
            cache.pop(str(result.event_id), None)
        if result.item.need == "refresh" and result.ok:
            self.last_refresh_changed = bool(result.changed)
            listener = getattr(self, "refresh_listener", None)
            if listener:
                listener(str(result.event_id), bool(result.changed))

    def _run_one(self, item: WorkItem) -> pipeline.ItemResult:
        """Tek bir iş birimi; çağıranın istek bağlamıyla (iptal, devre kesici)."""
        found: List[pipeline.ItemResult] = []

        def on_result(result: pipeline.ItemResult) -> None:
            self._after_result(result)
            found.append(result)

        self._pipeline(concurrency=1).run_sync([item], on_result=on_result)
        if not found:  # iptal: birim başlamadı
            return pipeline.ItemResult(item, pipeline.ITEM_SKIPPED, pipeline.SKIP_CANCELLED)
        return found[0]

    @staticmethod
    def _match_data(result: pipeline.ItemResult) -> Dict[str, Any]:
        """Sonucun eski sözlük biçimi: `basic`, gözlem ve bu çağrıda istenen dilimlerin yükleri."""
        payload = result.payload or {}
        data: Dict[str, Any] = {"basic": payload, OBSERVATION_KEY: observation_record(payload)}
        data.update((key, outcome.data) for key, outcome in result.slices.items())
        return data

    def _batch_items(self, match_ids: List[Any]) -> List[WorkItem]:
        """Maçların iş birimleri (işin önbelleğindeki kararlarla); tamam olanlar düşer."""
        mids = [str(mid) for mid in match_ids]
        self._prepare_needs(mids)
        needs: Dict[int, str] = {}
        for mid in mids:
            event_id = _canonical_id(mid)
            if event_id is not None:
                needs[event_id] = self._needs_detail_fetch(mid)
        store = self._store()
        items = planning.plan_items(store, list(needs), RefreshPolicy.current(),
                                    threshold=UNAVAILABLE_AFTER_ATTEMPTS, needs=needs)
        skipped = len(dict.fromkeys(mids)) - len(items)
        refresh = sum(1 for item in items if item.need == "refresh")
        if skipped:
            logger.info(f"{skipped} matches are complete; skipped")
        if refresh:
            logger.info(f"Refreshing {refresh} provisional records")
        return items

    def _batch(
        self,
        match_ids: List[Any],
        progress_callback: Optional[Callable[[int, int, str], None]],
        failed_callback: Optional[Callable[[str], None]],
        *,
        progress_bar: Any = None,
    ) -> Tuple[List[WorkItem], Callable[[pipeline.ItemResult], None], Dict[str, Dict[str, Any]]]:
        """Toplu indirmenin parçaları: iş birimleri, sonuç geri çağrısı ve sonuç sözlüğü."""
        items = self._batch_items(match_ids)
        results: Dict[str, Dict[str, Any]] = {}
        total = len(items)
        done = 0
        if progress_bar is not None and len(match_ids) > total:
            progress_bar.update(len(match_ids) - total)
        if progress_callback and total:
            progress_callback(0, total, f"Starting {total} match detail requests…")

        def on_result(result: pipeline.ItemResult) -> None:
            nonlocal done
            done += 1
            self._after_result(result)
            breaker = request_breaker.current()
            if result.ok:
                results[str(result.event_id)] = self._match_data(result)
            elif result.failed and failed_callback is not None:
                # Devre kesildiyse istek hatası "başarısız maç" sayılmaz (eski iki hattın kuralı); depolama hatası sayılır
                if result.reason == pipeline.FAIL_STORAGE or breaker is None or not breaker.tripped:
                    failed_callback(str(result.event_id))
            if progress_bar is not None:
                progress_bar.update(1)
            if progress_callback and total:
                progress_callback(min(done, total), total, f"Match details {min(done, total)}/{total}")

        return items, on_result, results

    def _batch_finished(self, breaker: request_breaker.CircuitBreaker) -> None:
        self.rate_limit_breaker_triggered = breaker.tripped
        self.last_status_counts = breaker.counts()

    def fetch_matches_batch(
        self,
        match_ids: List[Union[int, str]],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        failed_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Bir grup maçın detayları, boru hattıyla (kimliğiyle seçilen maçlar; lig planlarıyla aynı yol). Döndürür:
        yazılan maçlar (kimlik → eski sözlük biçimi).

        failed_callback(match_id): /event'i alınamayan, SofaScore'da olmayan ya da yazılamayan her maç için
        çağrılır. Bitmemiş maç ("yalnızca bitmiş maçlar" açıkken) atlanır, başarısız sayılmaz; devre kesilince
        denenmeyen ve iptal edilen maçlar da. Kalıcı depolama hatasında (disk dolu, izin yok) StorageError.
        """
        self.begin_job_cache()
        try:
            return self._run_batch(match_ids, progress_callback, should_cancel, failed_callback)
        finally:
            self.end_job_cache()

    def _run_batch(
        self,
        match_ids: List[Any],
        progress_callback: Optional[Callable[[int, int, str], None]],
        should_cancel: Optional[Callable[[], bool]],
        failed_callback: Optional[Callable[[str], None]],
    ) -> Dict[str, Dict[str, Any]]:
        """`fetch_matches_batch`'in gövdesi; işin önbelleğine dokunmaz (çağıranınkini kullanır)."""
        self.rate_limit_breaker_triggered = False
        items, on_result, results = self._batch(match_ids, progress_callback, failed_callback)
        with request_breaker.scope(self.config_manager) as breaker:
            try:
                self._pipeline().run_sync(items, cancelled=should_cancel, on_result=on_result)
            finally:
                self._batch_finished(breaker)
        return results

    async def fetch_matches_batch_async(self, match_ids, max_concurrent=30, progress_bar=None, progress_callback=None,
                                        should_cancel=None, failed_callback=None):
        """
        `fetch_matches_batch`'in eşyordamı (çağıranın döngüsünde; scripts/bench_bulk_rate.py). Devre kesici işin
        kesicisidir: çağıran kurduysa o, yoksa bu çağrı için yenisi.
        """
        self.rate_limit_breaker_triggered = False
        items, on_result, results = self._batch(list(match_ids), progress_callback, failed_callback,
                                                progress_bar=progress_bar)
        with request_breaker.scope(self.config_manager) as breaker:
            try:
                await self._pipeline(max_concurrent).run(items, cancelled=should_cancel, on_result=on_result)
            finally:
                self._batch_finished(breaker)
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
        """Store'a bir yazma, depo meşgulse yeniden denenerek (src/services/pipeline.py `put_retrying`)."""
        return pipeline.put_retrying(self._store(), event_id, what, write, log=logger)

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
        Geçici kaydı yeniler: boru hattının `refresh` birimi (yalnızca /event çekilir ve Store'a gözlem olarak
        yazılır, `Store.events.observe`). Yük aynıysa yalnızca gözlem anı ilerler. Yük değiştiyse saklanır;
        karşılaştırılan alanlardan biri değiştiyse (src/refresh.py `diff_basic`) değişim değişiklik günlüğüne
        aynı kritik bölümde eklenir ve `change` akışına change.recorded yazılır. COMPLETED → VOID olursa kayıt
        silinmez, yapışkan `status_regressed` bayrağı kurulur. Eski düzendeki kayıt önce v3'e yükseltilir.

        Kayıt yoksa ya da /event alınamadıysa None; yoksa kaydın yeni hali (eski sözlük biçimi).
        """
        mid = str(match_id)
        row = self._stored_event(mid)
        if row is None:
            return None
        result = self._run_one(WorkItem(store_hooks.Ref.event(row.id), "refresh", (), row.sport, "refresh"))
        if result.error is not None:
            raise result.error
        if not result.ok:
            return None
        return self._load_match_data_from_dir("", mid) or None

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
        Yalnızca yenileme (refresh-only): her maç için boru hattının `refresh` birimi (yalnızca /event). Hızı ortak
        istek bütçesi (src/throttle.py) belirler; maçlar arasında ayrıca beklenmez.

        Devre kesilirse (SofaScore engelliyor / sürekli hata) kalan maçlar denenmez; sonuçta `breaker` (neden:
        "403" / "429" / "5xx" / "other") ve `skipped` (denenmeyen maç) alanları bulunur. Kalıcı depolama hatası
        (disk dolu, izin yok) StorageError olarak fırlatılır.
        """
        stats: Dict[str, Any] = {"refreshed": 0, "changed": 0, "failed": 0}
        n = len(match_ids)
        if n:
            logger.info(f"Refreshing {n} matches")
        items: List[WorkItem] = []
        for mid in match_ids:
            event_id = _canonical_id(str(mid))
            if event_id is None:
                stats["failed"] += 1
                continue
            items.append(WorkItem(store_hooks.Ref.event(event_id), "refresh", (), None, "refresh"))
        done = n - len(items)
        skipped = 0

        def on_result(result: pipeline.ItemResult) -> None:
            nonlocal done, skipped
            done += 1
            self._after_result(result)
            if result.ok:
                stats["refreshed"] += 1
                stats["changed"] += int(bool(result.changed))
            elif result.failed:
                stats["failed"] += 1
            else:
                skipped += 1
            if progress_callback:
                progress_callback(done, n, f"Refresh {done}/{n}")

        with request_breaker.scope(self.config_manager) as breaker:
            self._pipeline().run_sync(items, cancelled=should_cancel, on_result=on_result)
            if breaker.tripped:
                stats["breaker"] = breaker.reason()
                stats["skipped"] = skipped
                logger.warning(f"Too many failed requests; the refresh stopped, {skipped} matches were not tried")
        self.rate_limit_breaker_triggered = bool(stats.get("breaker"))
        return stats

    def refill_missing_match_slices(
        self, match_id: Union[int, str], *, report: Optional[SingleFetchReport] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Saklanan maçta eksik dilimleri tamamlar (yeni maç için fetch_match_data kullanın): boru hattının `refill`
        birimi. Maçın sayfası (/event) yeniden okunur ve yazılır; eksik: seçilen dilimlerden (isteğe bağlılar
        dahil) verisi olmayan ve yeterince "veri yok" yanıtı almamış olanlar. Maç artık bitmemiş görünüyorsa
        ("yalnızca bitmiş maçlar" açıkken) hiçbir şey yazılmaz ve None döner; /event ikinci kez istenmez.

        report verilirse /event isteğinin ve istenen dilimlerin sonucu ona yazılır (SingleFetchReport).
        """
        row = self._stored_event(str(match_id))
        if row is None:
            return None
        states = list(self._store().events.states(store_hooks.Scope(event_ids=(row.id,))))
        item = planning.work_item(row.id, states[0] if states else None, "refill",
                                  threshold=UNAVAILABLE_AFTER_ATTEMPTS)
        assert item is not None
        return self._single(item, report)

    def fetch_match_data(
        self, match_id: Union[int, str], *, report: Optional[SingleFetchReport] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Bir maçın bütün detayları: boru hattının `full` birimi (/event, sonra sporun seçilen bütün dilimleri).
        Maç yoksa, bitmemişse ("yalnızca bitmiş maçlar" açıkken), /event alınamadıysa ya da yazılamadıysa None.

        report verilirse /event isteğinin ve istenen dilimlerin sonucu ona yazılır (SingleFetchReport).
        """
        event_id = _canonical_id(str(match_id))
        if event_id is None:
            return None
        return self._single(WorkItem(store_hooks.Ref.event(event_id), "full", (), None, "full"), report)

    def _single(self, item: WorkItem, report: Optional[SingleFetchReport]) -> Optional[Dict[str, Any]]:
        result = self._run_one(item)
        if report is not None:
            report.event = result.event
            report.slices.update(result.slices)
        if result.error is not None:
            raise result.error
        return self._match_data(result) if result.ok else None


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
            if self.rate_limit_breaker_triggered:
                # Terminal menüsünün çıkış kodu (main.py'nin etkileşimli dalı okur); web ve servis yolları bunu yazmaz
                os.environ["APP_EXIT_CODE"] = "2"
            return total_success > 0

        except StorageError:
            raise  # disk dolu / izin yok: "başarısız" deyip geçmek yerine çağırana net hata
        except Exception as e:
            logger.error(f"Fetching every match detail failed: {e}")
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
        """Verilen maçların detaylarını boru hattıyla çeker; başarılı maç sayısını döndürür.

        Devre kesilirse (rate limit) kalan maçlar denenmez ve rate_limit_breaker_triggered True kalır. Kesici,
        çağıran kurduysa işin kesicisidir. Kalıcı depolama hatasında (disk dolu, izin yok) StorageError fırlatılır.
        """
        self.last_storage_error = None
        total_attempts = len(match_ids_to_process)
        print("\n" + get_i18n().t("details_total_to_fetch", count=total_attempts))
        if progress_callback:
            progress_callback(0, total_attempts, f"Match details 0/{total_attempts}")

        def progress(done: int, _total: int, _msg: str) -> None:
            if progress_callback:
                progress_callback(min(done, total_attempts), total_attempts,
                                  f"Match details {min(done, total_attempts)}/{total_attempts}")

        try:
            results = self._run_batch(match_ids_to_process, progress, should_cancel, failed_callback)
        except StorageError as e:
            # Çağıran hatayı yutsa bile (etkileşimli menüler) neden okunabilsin: main.py çıkışta bildirir
            self.last_storage_error = e
            raise
        total_success = len(results)
        if self.rate_limit_breaker_triggered:
            print(get_i18n().t("error_rate_limit_detected", count=total_success))
        success_rate = (total_success / total_attempts) * 100 if total_attempts > 0 else 0
        print(
            "\n"
            + get_i18n().t("details_finished", ok=total_success, total=total_attempts, rate=f"{success_rate:.1f}")
        )
        return total_success

    def fetch_match_details(self, match_id: Union[int, str]) -> bool:
        """
        Bir maçın detayları (terminal menüsü): kayıtlıysa eksik dilimleri tamamlanır (`refill`), değilse tam çekim.
        Başarılıysa True; hata yutulur ve loglanır.
        """
        match_id = str(match_id)
        try:
            logger.info(f"Fetching the details of match {match_id}")
            if self._find_match_path(match_id):
                return bool(self.refill_missing_match_slices(match_id))
            if not self.fetch_match_data(match_id):
                logger.warning(f"No data for match {match_id}, or the match is not finished yet")
                return False
            return True
        except Exception as e:
            logger.error(f"Fetching the details of match {match_id} failed: {e}")
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
