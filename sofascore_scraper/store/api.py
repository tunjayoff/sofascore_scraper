"""
Store cephesi: bir veri dizinini açan tek giriş noktası (docs/design/01-storage.md, bölüm 2.3 ve 7.1).

`open_store(data_dir)` süreç başına, mutlak veri dizini başına tek bir `Store` verir (kayıt defteri).
Açılışta `.meta/` altında eksik olanlar kurulur: `schema.json`, `state.db` (geçişleriyle), `catalog.db`
(boş şema). Başka hiçbir şeye dokunulmaz: `schema.json`'ı ve `v3/` dizini olmayan bir dizin saf bir 2.x
dizinidir ve bu üç dosya silinince yine öyle olur.

`.meta/schema.json` dizinin sürümlerini taşır:
  * `layout_version` bu kodun bildiğinden büyükse dizine **yazılmaz**, `min_reader_layout` büyükse
    **okunmaz**; ikisi de SchemaTooNew.
  * `store_id` bir kez üretilir; hatırlayan bir tüketici dizinin değiştirildiğini anlar.
  * `state_schema`, `catalog_schema`, `derive_version`, `manifest_format` dizini son yazan kodun
    sürümleridir (bilgi). Yetkili değerler dosyaların kendisindedir ve onlar denetlenir: state.db koddan
    yeniyse SchemaTooNew, katalog farklıysa yeniden kurulur (`StoreInfo.catalog_rebuild_reason`).

Bu adımda cephede kilitler (`Store.lease`), çalışma zamanı bilgileri (`Store.runtime`), takipler
(`Store.follows`), olay akışları (`Store.streams`), izleyici durumu (`Store.watch`), işler (`Store.jobs`),
`Store.info`, kataloğun yönetimi (`Store.catalog`) ve okuma API'leri var: maçlar (`Store.events`), maç dışı
varlıklar (`Store.entities`) ve değişiklik günlüğü (`Store.changes`). Okuma API'leri kataloğa sorar. Yedek
(`Store.backup`, bugünkü zip düzeni) ve temizleme (`Store.clear`, bölüm 9.3) plan maddesi ST-19 ile geldi;
öteki yazma API'leri (history, migrate, export) kendi plan maddeleriyle eklenir.
Store içindeki modüller `_state`, `_catalog` ve `_leases` özniteliklerini kullanır; paket dışındaki kod
yalnızca açık yöntemleri.

Katalog açılışta güncellenir (bölüm 3.4 ve 3.5): kullanılabilir durumda değilse (dosya yok, başka şema ya da
türetme sürümü, bozuk) dosyalardan yeniden kurulur, kullanılabilirse dosyalarla uzlaştırılır
(`Store._sync_catalog`). Yeniden kurma `maintenance` kilidini alır; kilit başkasındaysa (aynı süreçteki bir
iş `writer` tutarken depo ilk kez açılıyorsa böyledir) ve şema uyuyorsa kilitsiz, tek bir yazma işleminde
yerinde kurulur; dosyanın yeniden yaratılması gerekiyorsa beklenir (uyarı, katalog bir sonraki açılışa kadar
kullanılamaz). Uzlaştırma kilit almaz: tek bir yazma işlemidir ve kancalarla aynı kuralla sıralanır.
Kataloğun güncellenememesi `open_store`'u düşürmez; uyarı yazılır. Açılıştaki uzlaştırmanın özet satırı DEBUG
düzeyindedir (komut çıktısı değişmez); kurulum, dizinde veri varsa tek bir INFO satırı yazar. Karar S17:
aynı dizinin bir açılışı eski düzen maç dizinlerini bir dakikadan kısa süre önce taradıysa açılış o taramayı
atlar (`Store._reconcile_on_open`; `STORE_OPEN_RECONCILE_SECONDS`).

Gölge kip (plan maddesi ST-11): eski düzen yazıcıları dosyalarını yazdıktan sonra buradaki kancalarla kataloğu
güncelliyordu. Yazıcıların hepsi Store'a geçti (ST-21, ST-22; terminal menüsü P26 ile gitti) ve çağıranı kalmayan
beş kanca (`shadow_event`, `shadow_schedules`, `shadow_season_lists`, `shadow_changes`, `shadow_cleared`) plan
maddesi FX-15'te kalktı; eski düzende kayıt kuran testler kataloğu kendileri dizinler (tests/catalog_index.py).
Ortak gövde (`_shadow`) `Store.clear`'ın yeniden kurmasını sarar.

`STORE_SHADOW_CHECK=1` (test paketi, tests/conftest.py): Store'un dışında eski düzen köklerine yazan ürün kodu ve
`Store.clear`'ın dokunduğu veri dizinleri not edilir; `shadow_check()` dizinlerin kataloğunu aynı ağacın sıfırdan
kurulmuş haliyle karşılaştırır (`CatalogAdmin.diff_from_rebuild`) ve ardından katalog eşitlenmeyen bir ürün
yazmasını bildirir. Bu kipte beklenmeyen bir dizinleme hatası yutulmaz, testi düşürür.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple, Union

from sofascore_scraper.store import files, layout
from sofascore_scraper.store.backup import BackupManager
from sofascore_scraper.store.catalog import CATALOG_SCHEMA, REBUILD_CORRUPT, Catalog, CatalogState, catalog_path
from sofascore_scraper.store.changes import ChangeLog
from sofascore_scraper.store.derive import DERIVE_VERSION
from sofascore_scraper.store.entities import KEY_SCHEDULE, KEY_SEASONS, EntityStore, clear_v3_listings
from sofascore_scraper.store.errors import CatalogCorrupt, LeaseHeld, PayloadCorrupt, PayloadMissing, SchemaTooNew, StoreError
from sofascore_scraper.store.events import EventStore
from sofascore_scraper.store.export import Exporter
from sofascore_scraper.store.follows import FollowStore
from sofascore_scraper.store.history import HistoryStore
from sofascore_scraper.store.indexer import (
    MODE_AUTO,
    MODE_IN_PLACE,
    MODE_RECREATE,
    CatalogAdmin,
)
from sofascore_scraper.store.jobs import JobStore, import_legacy_jobs
from sofascore_scraper.store.lease import MAINTENANCE, Lease, LeaseInfo, LeaseManager
from sofascore_scraper.store.manifest import MANIFEST_FORMAT
from sofascore_scraper.store.migrate import Migrator
from sofascore_scraper.store.purge import Purger
from sofascore_scraper.store.sqlite import to_store_error
from sofascore_scraper.store.state import RuntimeFacts, StateDb
from sofascore_scraper.store.streams import StreamLog
from sofascore_scraper.store.watch import WatchStateStore
from sofascore_scraper.version import __version__ as APP_VERSION

logger = logging.getLogger("Store")

PathLike = Union[str, "os.PathLike[str]"]

LAYOUT_VERSION = 3  # bu kodun yazdığı düzen (v3 ağacı ve dosya adları)
MIN_READER_LAYOUT = 3  # bu kodun yazdığı dizini okuyabilen en eski düzen sürümü
META_BUILT_AT = "built_at"  # katalogun son kurulduğu an (dizinleyici yazar)

SHADOW_CHECK_ENV = "STORE_SHADOW_CHECK"  # "1": kancaların dokunduğu dizinler not edilir (`shadow_check`)
CATALOG_RETRY_SECONDS = 30.0  # eşitlenemeyen katalog için kancalar en çok bu sıklıkta yeniden dener
# Kataloğun güncellenmesini engelleyen, beklenen hatalar: depolama (dolu disk, izin, meşgul veritabanı)
_CATALOG_ERRORS = (StoreError, sqlite3.Error, OSError)

# Karar S17: açılıştaki uzlaştırmanın sınırı. Aynı dizinin bir açılışı eski düzen maç dizinlerini bundan kısa
# süre önce taradıysa sonraki açılış o taramayı atlar (liste yarısı yine çalışır). `catalog reconcile` her
# zaman tarar. Ortam değişkeni süreyi saniye olarak değiştirir; "0" her açılışta taratır.
OPEN_RECONCILE_SECONDS = 60.0
OPEN_RECONCILE_ENV = "STORE_OPEN_RECONCILE_SECONDS"
META_OPEN_RECONCILED = "open_reconciled_at"  # katalogun `meta`'sı: son tam açılış taramasının bittiği an (epoch)

# `Store.clear` kapsamları ve sildikleri eski düzen ağaçları, silme sırasıyla (bugünkü web API'sinin sırası)
CLEAR_SCOPES: Tuple[str, ...] = ("events", "schedules", "seasons", "all")
_CLEAR_TREES: Tuple[Tuple[str, str], ...] = (("events", "match_details"), ("schedules", "matches"),
                                             ("seasons", "seasons"))
# v3 ağacında aynı kapsamların sildiği liste dilimleri (sofascore_scraper/store/entities.py `clear_v3_listings`)
_CLEAR_V3_LISTINGS = {"schedules": KEY_SCHEDULE, "seasons": KEY_SEASONS}
_CLEAR_PURPOSE = "op:clear"


@dataclass(frozen=True)
class StoreInfo:
    """`Store.info()` sonucu: sürümler, satır sayıları, alan başına bayt, düzen karışımı, tutulan kilitler."""

    data_dir: str
    readonly: bool
    store_id: str
    layout_version: int
    min_reader_layout: int
    manifest_format: int
    created_by: str
    created_at: str
    last_writer: Mapping[str, Any]
    state_schema: int  # state.db'nin şema sürümü (PRAGMA user_version)
    catalog_schema: Optional[int]  # catalog.db'nin şema sürümü; None: dosya yok
    derive_version: Optional[int]  # katalog satırlarının türetme sürümü; None: katalog hiç kurulmamış
    catalog_rebuild_reason: Optional[str]  # None: katalog kullanılabilir; yoksa neden yeniden kurulmalı
    last_rebuild: Optional[str]  # katalogun `meta.built_at` değeri, yazıldığı gibi
    journal_modes: Mapping[str, str]  # {"state": "wal", "catalog": "wal"}; "delete": tek süreç kipi
    rows: Mapping[str, Mapping[str, int]]  # {"state": {tablo: satır}, "catalog": {...}}
    events_by_layout: Mapping[str, int]  # {"v3": n, "legacy": n, "listing": n}
    bytes: Mapping[str, int] = field(default_factory=dict)  # veri dizininin üst düzey girdisi → bayt
    leases: Tuple[LeaseInfo, ...] = ()  # şu an tutulan kilitler (bütün süreçler)


@dataclass(frozen=True)
class ClearReport:
    """
    `Store.clear` sonucu.

    scopes           istenen kapsamlar (CLEAR_SCOPES adlarıyla, `all` açılmadan)
    cleared          silinen eski düzen ağaçları, silindikleri sırayla: "match_details", "matches", "seasons";
                     yalnızca var olanlar (boş dizin olarak yeniden kurulurlar)
    v3_events        `v3/events` vardı ve silindi
    catalog_rebuilt  katalog kalan dosyalardan yeniden kuruldu; False: kurulamadı (uyarı yazıldı, sonraki
                     açılış ya da kanca uzlaştırır)
    """

    scopes: Tuple[str, ...]
    cleared: Tuple[str, ...]
    v3_events: bool = False
    catalog_rebuilt: bool = False


def _open_reconcile_seconds() -> float:
    """Karar S17'nin süresi (saniye): ortam değişkeni ya da OPEN_RECONCILE_SECONDS; geçersiz değer varsayılandır."""
    raw = os.environ.get(OPEN_RECONCILE_ENV, "").strip()
    if not raw:
        return OPEN_RECONCILE_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return OPEN_RECONCILE_SECONDS


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def read_schema(path: PathLike) -> Optional[Dict[str, Any]]:
    """schema.json'ı okur; dosya yoksa None. Ayrıştırılamayan ya da alanları eksik dosya PayloadCorrupt."""
    where = os.fspath(path)
    try:
        raw = files.read_bytes(where)
    except PayloadMissing:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise PayloadCorrupt(f"schema.json okunamıyor ({e}): {where}", path=where, detail=str(e)) from e
    if (not isinstance(data, dict) or not isinstance(data.get("store_id"), str) or not data["store_id"]
            or not _is_int(data.get("layout_version")) or not _is_int(data.get("min_reader_layout"))):
        raise PayloadCorrupt(f"schema.json geçersiz (store_id, layout_version, min_reader_layout gerekli): {where}",
                             path=where)
    return data


def check_layout(schema: Mapping[str, Any], path: PathLike, *, readonly: bool) -> None:
    """Dizin bu kodun okuyabildiğinden (ya da, yazılacaksa, yazabildiğinden) yeni bir düzendeyse SchemaTooNew."""
    where = os.fspath(path)
    if schema["min_reader_layout"] > LAYOUT_VERSION:
        raise SchemaTooNew(path=where, component="layout", found=schema["min_reader_layout"],
                           supported=LAYOUT_VERSION)
    if not readonly and schema["layout_version"] > LAYOUT_VERSION:
        raise SchemaTooNew(path=where, component="layout", found=schema["layout_version"],
                           supported=LAYOUT_VERSION)


def _tree_bytes(path: str) -> int:
    """Dosyanın ya da dizin ağacının bayt toplamı; sembolik bağlar izlenmez, okunamayan girdiler atlanır."""
    total = 0
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            if not os.path.isdir(current) or os.path.islink(current):
                total += os.lstat(current).st_size
                continue
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(entry.path)
                    else:
                        total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return total


class Store:
    """
    Açık bir veri dizini. `open_store` ile alınır; kurucu doğrudan çağrılmaz.

    readonly=True: yük dosyası yazılmaz ve `layout_version` yalnızca okuma kuralıyla denetlenir; iki
    veritabanı yine açılır (state.db'ye sahip bilgisi, katalog şeması gibi küçük kayıtlar yazılabilir;
    katalog türetilmiş bir dizindir ve açılışta yine güncellenir).
    sync_catalog=False: açılışta katalog kurulmaz ve uzlaştırılmaz (bkz. `open_store`).
    """

    def __init__(self, data_dir: PathLike, *, create: bool = True, readonly: bool = False,
                 sync_catalog: bool = True) -> None:
        self.data_dir = Path(os.path.abspath(os.fspath(data_dir)))
        self.readonly = bool(readonly)
        self._closed = False
        self._close_lock = threading.Lock()
        self._schema_path = layout.resolve(self.data_dir, layout.SCHEMA_FILE)
        state_path = layout.resolve(self.data_dir, layout.STATE_DB)

        existing = read_schema(self._schema_path)
        if existing is not None:
            check_layout(existing, self._schema_path, readonly=self.readonly)
        if not create and (existing is None or not os.path.isfile(state_path)):
            raise StoreError(f"Veri dizini henüz bir depo değil (.meta/schema.json ya da state.db yok): "
                             f"{self.data_dir}", path=str(self.data_dir))
        meta_dir = layout.resolve(self.data_dir, layout.META_DIR)
        try:
            os.makedirs(meta_dir, exist_ok=True)
        except OSError as e:
            raise StoreError.from_exception(e, meta_dir) from e

        self._leases = LeaseManager.for_data_dir(self.data_dir)
        self._catalog: Optional[Catalog] = None
        try:
            self._state = StateDb(state_path, migration_guard=self._leases.migration_guard)
        except sqlite3.Error as e:
            raise StoreError(f"state.db açılamadı ({e}): {state_path}", path=state_path, detail=str(e)) from e
        try:
            self._leases.state = self._state
            import_legacy_jobs(self._state, layout.resolve(self.data_dir, layout.LEGACY_JOBS_DB))
            self._catalog = Catalog(catalog_path(self.data_dir), attach={"state": state_path})
            self._catalog.prepare(create=create)
            self.follows = FollowStore(self)
            self._schema = self._sync_schema()
            self.runtime = RuntimeFacts(self._state)
            self.streams = StreamLog(self)
            self.watch = WatchStateStore(self)
            self.events = EventStore(self)
            self.entities = EntityStore(self)
            self.changes = ChangeLog(self)
            self.history = HistoryStore(self)
            self.backup = BackupManager(self)
            self.export = Exporter(self)
            self.migrate = Migrator(self)
            self.purge = Purger(self)
            self._names_used: Dict[int, str] = {}
            self._catalog_ready = False
            self._catalog_warned = False
            self._catalog_retry_at = 0.0
            self.catalog = CatalogAdmin(self.data_dir, self._catalog, league_names=self._league_names)
            _shadow_opened(self)
            if sync_catalog:
                self._sync_catalog(on_open=True)
        except BaseException:
            self.close()
            raise

    # --- schema.json ------------------------------------------------------------------------

    def _code_versions(self) -> Dict[str, int]:
        return {
            "layout_version": LAYOUT_VERSION,
            "min_reader_layout": MIN_READER_LAYOUT,
            "manifest_format": MANIFEST_FORMAT,
            "state_schema": self._state.latest_version,
            "catalog_schema": CATALOG_SCHEMA,
            "derive_version": DERIVE_VERSION,
        }

    def _sync_schema(self) -> Dict[str, Any]:
        """
        schema.json'ı yoksa yaratır; varsa ve dizin yazmak için açıldıysa son yazanın sürümlerini günceller.
        state.db'nin yazma kilidi altında yapılır: aynı anda açılan iki süreç iki ayrı `store_id` üretmez.

        state.db okunabildiği halde yazılamıyorsa (ör. başka bir hesaptan kalmış, yazılamayan `-shm` dosyası)
        SQLite hatası StoreError olarak çıkar (`sqlite.to_store_error`, dosyanın yoluyla): StoreError yakalayan
        çağıranlar depolama iletisini gösterir.
        """
        try:
            return self._sync_schema_locked()
        except sqlite3.Error as e:
            raise to_store_error(e, self._state.path) from e

    def _sync_schema_locked(self) -> Dict[str, Any]:
        with self._state.write():
            schema = read_schema(self._schema_path)
            now = _utc_now()
            if schema is None:
                schema = {"store_id": uuid.uuid4().hex, **self._code_versions(), "created_by": APP_VERSION,
                          "created_at": now, "last_writer": {"app_version": APP_VERSION, "at": now}}
            else:
                check_layout(schema, self._schema_path, readonly=self.readonly)
                versions = self._code_versions()
                writer = schema.get("last_writer")
                same_writer = isinstance(writer, dict) and writer.get("app_version") == APP_VERSION
                if self.readonly or (same_writer and all(schema.get(k) == v for k, v in versions.items())):
                    return schema
                schema = {**schema, **versions, "last_writer": {"app_version": APP_VERSION, "at": now}}
            text = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
            files.write_bytes(self._schema_path, text.encode("utf-8"))
            return schema

    # --- katalog: açılışta kurma ve uzlaştırma (bölüm 3.4, 3.5) -------------------------------

    def _league_names(self) -> Dict[int, str]:
        """
        Sezon listesi dosyalarının adından turnuvayı bulmak için ad eşlemesi (dizinleyici her taramada sorar):
        turnuva takipleri. Depo lig yapılandırmasını okuyamaz; takipler onun aynasıdır (sofascore_scraper/config_manager.py).
        """
        names = self.follows.leagues()
        self._names_used = names
        return names

    def _unclean_writer(self) -> bool:
        """Önceki yazar temiz kapanmadı mı: işaret duruyor ve şu an `writer` kilidini tutan yok (bölüm 6.1)."""
        try:
            return os.path.exists(self._leases.unclean_marker) and self._leases.holder("writer") is None
        except _CATALOG_ERRORS:
            return False

    def _sync_catalog(self, *, on_open: bool = False) -> bool:
        """
        Kataloğu kullanılır hale getirir ve dosyalarla eşitler; başardıysa True. Açılışta ve, katalog
        eşitlenememişse, kancalardan çağrılır.

          * Kullanılabilir katalog uzlaştırılır (bölüm 3.5). Önceki yazar temiz kapanmadıysa önce
            `PRAGMA quick_check` çalışır ve v3 maç dizinleri de imzalarıyla karşılaştırılır.
          * on_open=True (yalnızca deponun kurucusu): karar S17'nin sınırı uygulanır
            (`_reconcile_on_open`). Kancaların ve yeniden denemelerin eşitlemesi her zaman tam taramadır:
            az önce yazılan maç dizini onunla dizinlenir.
          * Kullanılamayan (yok, başka şema ya da türetme sürümü) ya da bozuk katalog yeniden kurulur
            (`_build_catalog`); kurulum zaten bütün ağacı okur, ardından uzlaştırma gerekmez.

        Depolama hatası fırlatmaz: uyarı yazar (depo başına bir kez, sonrakiler DEBUG) ve False döner; katalog
        o zaman güncel sayılmaz ve kancalar ona yazmaz, `CATALOG_RETRY_SECONDS` sonra yeniden denerler.
        """
        assert self._catalog is not None
        self._catalog_retry_at = time.monotonic() + CATALOG_RETRY_SECONDS
        try:
            state = self._catalog.inspect()
            deep = _shadow_take_edited(self)
            if state.usable:
                unclean = self._unclean_writer()
                damage = self._catalog.quick_check() if unclean else []
                if damage:
                    state = CatalogState(exists=True, rebuild_reason=REBUILD_CORRUPT, detail=damage[0])
                else:
                    try:
                        if on_open:
                            self._reconcile_on_open(deep=deep, unclean=unclean)
                        else:
                            self.catalog.reconcile(deep=deep, v3=unclean, quiet=True)
                    except CatalogCorrupt as e:
                        state = CatalogState(exists=True, rebuild_reason=REBUILD_CORRUPT, detail=str(e))
            if not state.usable:
                if not self._build_catalog(state):
                    self._catalog_ready = False
                    return False
                self._stamp_open_reconcile()
        except _CATALOG_ERRORS as e:
            self._catalog_ready = False
            log = logger.debug if self._catalog_warned else logger.warning
            self._catalog_warned = True
            log("The catalog of %s could not be brought up to date; it is retried later: %s", self.data_dir, e)
            return False
        except Exception:
            if _shadow_checking():
                raise
            # Gölge kip: dizinleyicideki bir hata uygulamayı düşürmez; katalog bu süreçte kullanılmaz
            self._catalog_ready = False
            self._catalog_retry_at = float("inf")
            logger.exception("Unexpected error while bringing the catalog of %s up to date", self.data_dir)
            return False
        self._catalog_ready = True
        self._catalog_warned = False
        _shadow_synced(self)
        return True

    def _reconcile_on_open(self, *, deep: bool, unclean: bool) -> None:
        """
        Açılıştaki uzlaştırma, karar S17'nin sınırıyla. Aynı dizinin bir açılışı (bu ya da başka bir süreçte)
        eski düzen maç dizinlerini `_open_reconcile_seconds()` saniyeden kısa süre önce taradıysa, bekleyen
        yazma işareti yoksa, önceki yazar temiz kapandıysa ve dizin elle değiştirilmiş sayılmıyorsa (`deep`)
        o tarama atlanır ve yalnızca liste yarısı çalışır (`sync_listings`: sezon listeleri, programlar,
        değişiklik günlüğü). Öteki her durumda tam uzlaştırma çalışır ve bittiği an katalogun `meta`'sına
        yazılır.

        Bedeli: bir 2.x sürecinin, elle düzenlemenin ya da yazma ile kancası arasında çöken bir sürecin maç
        dizinlerinde yaptığı değişiklik, o süre dolmadan açılan depoda görülmez; süre dolunca ilk açılış ya
        da `catalog reconcile` görür. Kancalı yazıcıların yazdıkları her zaman görülür.
        """
        if not deep and not unclean and self._open_reconciled_recently():
            self.catalog.sync_listings()
            return
        self.catalog.reconcile(deep=deep, v3=unclean, quiet=True)
        self._stamp_open_reconcile()

    def _open_reconciled_recently(self) -> bool:
        assert self._catalog is not None
        window = _open_reconcile_seconds()
        if window <= 0:
            return False
        try:
            stamp = json.loads(self._catalog.get_meta(META_OPEN_RECONCILED) or "null")
            at = float(stamp["at"])
            where = str(stamp["dir"])
        except (ValueError, TypeError, KeyError):
            return False
        if where != self._stamp_dir():
            return False  # dizin kataloğuyla birlikte başka bir yere kopyalanmış: oradaki tarama sayılmaz
        if not 0.0 <= time.time() - at < window:
            return False  # süre doldu ya da saat geri gitti
        with self._catalog.read() as conn:
            return conn.execute("SELECT 1 FROM pending_writes LIMIT 1").fetchone() is None

    def _stamp_dir(self) -> str:
        return os.path.normcase(os.path.realpath(self.data_dir))

    def _stamp_open_reconcile(self) -> None:
        """
        Tam açılış taramasının (ya da kurulumun) bittiği anı ve dizinin yolunu yazar; yazılamaması açılışı
        düşürmez. Yol, kataloğuyla birlikte kopyalanmış bir dizinin (yedekten dönen, taşınan) ilk açılışının
        taramayı atlamamasını sağlar.
        """
        assert self._catalog is not None
        value = json.dumps({"at": time.time(), "dir": self._stamp_dir()})
        try:
            with self._catalog.write():
                self._catalog.set_meta(META_OPEN_RECONCILED, value)
        except _CATALOG_ERRORS as e:
            logger.debug("The time of the reconcile on open could not be stored in %s: %s", self.data_dir, e)

    def _build_catalog(self, state: CatalogState) -> bool:
        """
        Kullanılamayan kataloğu dosyalardan yeniden kurar (bölüm 3.4); kuramadıysa False.

        `maintenance` kilidiyle (amaç `op:rebuild`): şema uyuyorsa yerinde, uymuyorsa ya da dosya bozuksa
        yeniden yaratarak. Kilit başkasındaysa (çalışan bir iş `writer` tutuyor; web işi depoyu ilk kez
        yazar kilidini aldıktan sonra açar) yalnızca yerinde kurulabilir: tek bir yazma işlemidir, öteki
        yazarın kancalarıyla SQLite sıralar ve kanca, dosyasını yazdıktan sonra çalıştığı için sonuç aynıdır.
        Dosyanın yeniden yaratılması gerekiyorsa beklenir: yerine konan dosya, o sırada yazılan satırları
        kaybederdi.
        """
        lease: Optional[Lease] = None
        try:
            lease = self._leases.acquire("maintenance", purpose="op:rebuild")
        except LeaseHeld as held:
            if not state.schema_ok:
                log = logger.debug if self._catalog_warned else logger.warning
                self._catalog_warned = True
                log("The catalog of %s has to be recreated (%s) but the data directory is in use (%s); "
                    "it stays unusable until the directory is opened while nothing else runs",
                    self.data_dir, state.rebuild_reason, held)
                return False
        try:
            if lease is None:
                mode = MODE_IN_PLACE
            else:
                mode = MODE_RECREATE if state.rebuild_reason == REBUILD_CORRUPT else MODE_AUTO
            report = self.catalog.rebuild(mode=mode)
        finally:
            if lease is not None:
                lease.release()
        if report.events or report.listed or report.season_lists or report.changes or report.problems:
            logger.info(
                "Catalog built from the files in %s: %d events with details, %d listed only, %d problems",
                self.data_dir, report.events, report.listed, len(report.problems))
        return report.completed

    # --- açık API ---------------------------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def catalog_current(self) -> bool:
        """
        Katalog bu süreçte dosyalarla eşitlendi ve o günden beri her kanca onu güncelledi mi. False ise
        katalog eşitlenemedi (uyarı yazıldı) ya da bir kanca yazamadı: okuyucular onu güncel saymamalıdır
        (ör. her maçı "eksik" saymak yerine planlamayı reddetmek). Kanca olmadan (elle, 2.x süreciyle)
        değişen dosyaları bilmez; onları açılıştaki uzlaştırma görür (karar S17'nin sınırıyla).
        """
        return not self._closed and self._catalog_ready

    @property
    def jobs(self) -> JobStore:
        """Deponun iş deposu (`JobStore.for_store`): aynı Store için hep aynı nesne; ilk erişimde kurulur."""
        self._require_open()
        return JobStore.for_store(self)

    @property
    def store_id(self) -> str:
        """Dizinin değişmez kimliği (schema.json)."""
        return str(self._schema["store_id"])

    def lease(self, name: str, *, purpose: str = "", wait: float = 0.0) -> Lease:
        """
        Veri dizininin bir kilidini alır (bölüm 6.1): "writer", "watcher:<spor>", "live", "sinks",
        "maintenance". Bağlam yöneticisidir; `release()` ile de bırakılır. Kilit başka bir sahipteyse
        `wait` saniye beklenir, sonra sahibin pid, makine, amaç ve başlangıç bilgisiyle LeaseHeld.
        """
        self._require_open()
        return self._leases.acquire(name, purpose=purpose, wait=wait)

    def lease_holder(self, name: str) -> Optional[LeaseInfo]:
        """Kilit şu an (herhangi bir süreçte) tutuluyorsa sahibi, boştaysa None. Kilidi almaz."""
        self._require_open()
        return self._leases.holder(name)

    def info(self, *, sizes: bool = True) -> StoreInfo:
        """Dizinin o anki hali. sizes=False: `bytes` boş kalır (büyük dizinlerde dosya ağacı gezilmez)."""
        self._require_open()
        assert self._catalog is not None
        catalog_state: CatalogState = self._catalog.inspect()
        rows: Dict[str, Dict[str, int]] = {"state": self._count_rows(self._state.connection(), None), "catalog": {}}
        events_by_layout: Dict[str, int] = {}
        last_rebuild: Optional[str] = None
        if catalog_state.schema_ok:
            with self._catalog.read() as conn:
                rows["catalog"] = self._count_rows(conn, "main")
                for name, count in conn.execute(
                        "SELECT COALESCE(layout, 'listing'), count(*) FROM main.events GROUP BY 1 ORDER BY 1"):
                    events_by_layout[str(name)] = int(count)
            last_rebuild = self._catalog.get_meta(META_BUILT_AT)
        journal_modes = {"state": self._state.journal_mode}
        if catalog_state.exists:
            journal_modes["catalog"] = self._catalog.journal_mode
        schema = self._schema
        return StoreInfo(
            data_dir=str(self.data_dir),
            readonly=self.readonly,
            store_id=self.store_id,
            layout_version=int(schema["layout_version"]),
            min_reader_layout=int(schema["min_reader_layout"]),
            manifest_format=int(schema.get("manifest_format") or MANIFEST_FORMAT),
            created_by=str(schema.get("created_by") or ""),
            created_at=str(schema.get("created_at") or ""),
            last_writer=dict(schema.get("last_writer") or {}),
            state_schema=self._state.schema_version,
            catalog_schema=catalog_state.schema_version,
            derive_version=catalog_state.derive_version,
            catalog_rebuild_reason=catalog_state.rebuild_reason,
            last_rebuild=last_rebuild,
            journal_modes=journal_modes,
            rows=rows,
            events_by_layout=events_by_layout,
            bytes=self._area_bytes() if sizes else {},
            leases=tuple(self._leases.holders()),
        )

    @staticmethod
    def _count_rows(conn: sqlite3.Connection, schema: Optional[str]) -> Dict[str, int]:
        master = f"{schema}.sqlite_master" if schema else "sqlite_master"
        prefix = f"{schema}." if schema else ""
        names: List[str] = [
            str(row[0]) for row in conn.execute(
                f"SELECT name FROM {master} WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        ]
        return {name: int(conn.execute(f'SELECT count(*) FROM {prefix}"{name}"').fetchone()[0]) for name in names}

    def clear(self, scope: Union[str, Iterable[str]]) -> ClearReport:
        """
        Verinin bir kısmını siler (bölüm 9.3): `events` maç detaylarını (`match_details/` ve `v3/events`),
        `schedules` maç listelerini (`matches/` ve v3'teki program sayfaları), `seasons` sezon listelerini
        (`seasons/` ve v3'teki sezon listeleri), `all` üçünü. Birden
        çok kapsam bir dizi olarak verilebilir. Var olan eski düzen ağacı silinir ve boş dizin olarak yeniden
        kurulur (bugünkü web temizlemesi); `state.db`, değişiklik günlüğü (`score_changes.jsonl`,
        `changes/`), `backups/`, `exports/` ve bu üç ağacın dışındaki dosyalar kalır. Maçlar tek tek
        `EventStore.delete` ile değil, ağaç olarak silinir: eski kopyalar çöpe taşınmaz.

        `maintenance` kilidi gerekir: bu süreç tutuyorsa (web'in iş deposu, `JobStore.exclusive("clear")`)
        onun altında çalışır, tutmuyorsa işlem süresince alır (amaç `op:clear`; başka bir süreç dizini
        kullanıyorsa LeaseHeld). Aynı kilit altında, silmeden sonra (silme yarıda kalsa da) katalog kalan
        dosyalardan yerinde yeniden kurulur; bu yüzden temizlenen turnuvaların satırları da gider. Katalog
        kurulamazsa temizleme yine başarılıdır (uyarı; `ClearReport.catalog_rebuilt` False).

        Bilinmeyen kapsam ValueError, salt okunur depo ya da dosya sistemi hatası StoreError.
        """
        self._require_open()
        wanted = (scope,) if isinstance(scope, str) else tuple(scope)
        unknown = [name for name in wanted if name not in CLEAR_SCOPES]
        if not wanted or unknown:
            raise ValueError(f"unknown clear scope: {unknown or wanted!r}")
        if self.readonly:
            raise StoreError(f"Salt okunur açılmış depo temizlenemez: {self.data_dir}", path=str(self.data_dir))
        lease: Optional[Lease] = None
        if not self._leases.held_here(MAINTENANCE):
            lease = self._leases.acquire(MAINTENANCE, purpose=_CLEAR_PURPOSE)
        cleared: List[str] = []
        v3_events = False
        rebuilt = False
        try:
            try:
                for name, tree in _CLEAR_TREES:
                    if "all" not in wanted and name not in wanted:
                        continue
                    if name == "events":
                        v3_events = files.remove_tree(layout.resolve(self.data_dir, layout.EVENTS_DIR))
                    elif name in _CLEAR_V3_LISTINGS:  # v3'teki program sayfaları ya da sezon listeleri (ST-22)
                        clear_v3_listings(self.data_dir, _CLEAR_V3_LISTINGS[name])
                    path = os.path.join(self.data_dir, tree)
                    if os.path.exists(path):
                        files.remove_tree(path)
                        try:
                            os.makedirs(path, exist_ok=True)
                        except OSError as e:
                            raise StoreError.from_exception(e, path) from e
                        cleared.append(tree)
            finally:
                rebuilt = _shadow(self.data_dir, "a clear", _rebuild_in_place, store=self)
        finally:
            if lease is not None:
                lease.release()
        logger.info("Data cleared in %s: scope=%s cleared=%s v3_events=%s catalog_rebuilt=%s",
                    self.data_dir, ",".join(wanted), ",".join(cleared) or "-", v3_events, rebuilt)
        return ClearReport(scopes=wanted, cleared=tuple(cleared), v3_events=v3_events, catalog_rebuilt=rebuilt)

    def _area_bytes(self) -> Dict[str, int]:
        try:
            names = sorted(os.listdir(self.data_dir))
        except OSError as e:
            raise StoreError.from_exception(e, str(self.data_dir), reading=True) from e
        return {name: _tree_bytes(os.path.join(self.data_dir, name)) for name in names}

    def _require_open(self) -> None:
        if self._closed:
            raise StoreError(f"Depo kapatılmış: {self.data_dir}", path=str(self.data_dir))

    def close(self) -> None:
        """
        Veritabanı bağlantılarını kapatır ve depoyu kayıt defterinden çıkarır. Alınmış kilitler sahiplerinde kalır.

        `Store.jobs`'ta bitmekte olan bir iş varsa (satırı bitmiş, son depo erişimleri sürüyor) önce onun bitmesi
        beklenir (en çok 30 s; `JobStore.wait_for_finishing_job`): state.db onu kullanan iş thread'inin altından
        kapatılmaz. Ortasında olan bir işi ya da başka bir thread'in süren okumasını beklemez; kapatan, onların
        bittiğinden emin olmalıdır (docs/design/01-storage.md, bölüm 3.2).
        """
        if self._closed:
            return
        JobStore.wait_for_finishing_job(self)
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        with _registry_lock:
            for key in [k for k, v in _registry.items() if v is self]:
                del _registry[key]
        if self._catalog is not None:
            self._catalog.close()
        self._state.close()

    def __repr__(self) -> str:
        return f"<Store {str(self.data_dir)!r}{' readonly' if self.readonly else ''}>"


# --- kayıt defteri -------------------------------------------------------------------------------

_registry: Dict[Tuple[str, bool], Store] = {}
_registry_lock = threading.RLock()


def open_store(data_dir: Optional[PathLike] = None, *, create: bool = True, readonly: bool = False,
               sync_catalog: bool = True) -> Store:
    """
    Veri dizininin deposunu açar; aynı süreçte aynı dizin (ve aynı `readonly`) için hep aynı nesne döner.

    data_dir=None → DATA_DIR ortam değişkeni, o da yoksa "data". create=True: `.meta/`, `schema.json`,
    `state.db` ve `catalog.db` eksikse kurulur; create=False: bunlar yoksa StoreError. Dizin daha yeni bir
    düzenle ya da state şemasıyla yazılmışsa SchemaTooNew.

    Açılışta katalog dosyalardan güncellenir: kullanılamıyorsa yeniden kurulur, kullanılabiliyorsa
    uzlaştırılır (modül belgesi). Büyük bir dizinin ilk açılışında kurulum sürer (maç başına bir-iki
    milisaniye). sync_catalog=False bunu atlar: kataloğu bulunduğu haliyle görmek isteyen yönetim araçları
    içindir (doğrulama, sayımlar). Öyle açılmış bir depoyu `sync_catalog=True` ile isteyen sonraki çağrı ve
    ilk yazıcı kancası kataloğu günceller.
    """
    root = os.fspath(data_dir) if data_dir is not None else (os.getenv("DATA_DIR") or "data")
    path = os.path.abspath(root)
    key = (os.path.normcase(os.path.realpath(path)), bool(readonly))
    with _registry_lock:
        store = _registry.get(key)
        if store is None or store.closed:
            store = Store(path, create=create, readonly=readonly, sync_catalog=sync_catalog)
            _registry[key] = store
        elif sync_catalog and not store._catalog_ready and time.monotonic() >= store._catalog_retry_at:
            store._sync_catalog()
        return store


# --- gölge kip: denetim ve temizlemenin yeniden kurması ---------------------------------------------

@dataclass
class _ShadowNote:
    """STORE_SHADOW_CHECK: bir veri dizininde, son `shadow_check`'ten bu yana olanlar."""

    data_dir: str
    names: Dict[int, str] = field(default_factory=dict)  # sezon listelerinin son tarandığı ad eşlemesi
    touched: bool = False  # bir kanca çağrıldı: karşılaştırılacak
    failed: bool = False  # bir kanca kataloğu güncelleyemedi (depolama hatası): karşılaştırılmaz
    edited: bool = False  # depo açıkken dosyalar kancasız değişti (testin kendisi yazdı): karşılaştırılmaz
    unhooked: Optional[str] = None  # ürün kodunun yazdığı, ardından henüz kanca çağrılmamış ilk yol


_shadow_lock = threading.Lock()
_shadow_notes: Dict[str, _ShadowNote] = {}  # veri dizini (normalize) → not
_shadow_seen: Set[str] = set()  # bu süreçte açılmış veri dizinleri (normalize)
_shadow_unsynced: Set[str] = set()  # son `shadow_check`'ten beri kancasız değişmiş, henüz eşitlenmemiş dizinler
_shadow_stale: Set[str] = set()  # önceki testlerden eşitlenmeden kalanlar: bir sonraki açılış imzalara güvenmez
_shadow_resyncing = threading.local()  # `shadow_resync` kendi dosya erişimleriyle yeniden tetiklenmesin
_shadow_warned: Dict[str, bool] = {}  # açılamayan veri dizinleri: uyarı bir kez yazılır


def _shadow_checking() -> bool:
    return os.environ.get(SHADOW_CHECK_ENV) == "1"


def _shadow_key(data_dir: PathLike) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(data_dir)))


@functools.lru_cache(maxsize=4096)
def _shadow_real_dir(directory: str) -> str:
    return os.path.normcase(os.path.realpath(directory))


def _shadow_path_key(path: PathLike) -> str:
    """
    Bir dosya yolunun `_shadow_key` biçimi; dizininin çözümü önbelleklidir. Test paketinin denetim kancası
    bunu her dosya yazmasında çağırır (yüz binlerce kez); yazmalar az sayıda dizine gider.
    """
    head, tail = os.path.split(os.path.abspath(os.fspath(path)))
    return os.path.join(_shadow_real_dir(head), os.path.normcase(tail)) if tail else _shadow_real_dir(head)


def _shadow_dir_of(path: PathLike, among: Set[str]) -> Optional[str]:
    """`path`'i içeren, `among` içindeki veri dizini; yol o dizinin `.meta/` ağacındaysa (deponun kendi dosyaları) None."""
    current = _shadow_path_key(path)
    while True:
        if current in among:
            return current
        parent = os.path.dirname(current)
        if parent == current or (os.path.basename(current) == layout.META_DIR and parent in among):
            return None
        current = parent


# Katalogda dizinlenen eski düzen kökleri: ürün kodunun bunların altına yazdığı her şeyi bir kanca izlemelidir.
# `match_details/processed` türetilmiş CSV'lerdir, dizinlenmez.
_SHADOW_ROOT_DIRS = ("match_details", "matches", "seasons")
_SHADOW_ROOT_FILES = ("score_changes.jsonl", "league_seasons.csv")
_SHADOW_UNINDEXED = ("match_details/processed",)


def _shadow_indexed(rel: str) -> bool:
    """
    Veri dizinine göre yol, katalogda dizinlenen bir kök mü ya da onun altında mı. Kökün kendisi de sayılır:
    ağacı silen ya da yerine başkasını koyan çağrı (`shutil.rmtree`, `os.rename`) yalnızca kökün yolunu verir.
    """
    if rel in _SHADOW_ROOT_FILES:
        return True
    if any(rel == skipped or rel.startswith(skipped + "/") for skipped in _SHADOW_UNINDEXED):
        return False
    return any(rel == root or rel.startswith(root + "/") for root in _SHADOW_ROOT_DIRS)


def _shadow_locate(path: PathLike) -> Optional[str]:
    """
    Dizinlenen bir kökün altındaki yolun veri dizini (normalize), yol dizinlenen bir kökte değilse None. Veri
    dizini, bu süreçte açılmış bir depoysa odur; değilse yolun son kök bileşeninin (`match_details`, `matches`,
    `seasons`) üstündeki dizindir: deposu hiç açılmamış bir dizine yazan (yani hiç kanca çağırmayan) yazıcı da
    görülür.
    """
    full = _shadow_path_key(path)
    key = _shadow_dir_of(full, _shadow_seen)
    if key is None:
        parts = full.split(os.sep)
        if parts[-1] in _SHADOW_ROOT_FILES:
            index = len(parts) - 1
        else:
            index = max((i for i, part in enumerate(parts) if part in _SHADOW_ROOT_DIRS), default=0)
        if index < 1:
            return None
        key = os.sep.join(parts[:index]) or os.sep
    rel = os.path.relpath(full, key).replace(os.sep, "/")
    return key if _shadow_indexed(rel) else None


def _shadow_opened(store: Store) -> None:
    """Denetim kipinde açılan depoyu not eder: bu andan sonra dizine kancasız yazılanlar izlenir."""
    if not _shadow_checking():
        return
    key = _shadow_key(store.data_dir)
    with _shadow_lock:
        _shadow_seen.add(key)
        _shadow_notes.setdefault(key, _ShadowNote(str(store.data_dir)))


def _shadow_synced(store: Store) -> None:
    """Denetim kipi: katalog az önce baştan eşitlendi (açılış, yeniden eşitleme): bekleyen kancasız yazma kalmadı."""
    if not _shadow_checking():
        return
    with _shadow_lock:
        note = _shadow_notes.get(_shadow_key(store.data_dir))
        if note is not None:
            note.unhooked = None


def _shadow_take_edited(store: Store) -> bool:
    """
    Denetim kipi: dizin son eşitlemeden beri kancasız değiştiyse True (ve işaret silinir); uzlaştırma o zaman
    imzalara güvenmez (`deep`). Testler dosyaları yerinde, dizinin mtime'ını değiştirmeden düzenleyebilir.
    """
    if not _shadow_checking():
        return False
    key = _shadow_key(store.data_dir)
    with _shadow_lock:
        if key not in _shadow_unsynced and key not in _shadow_stale:
            return False
        _shadow_unsynced.discard(key)
        _shadow_stale.discard(key)
        note = _shadow_notes.get(key)
        if note is not None:
            note.edited = False
        return True


def shadow_watching() -> bool:
    """Denetim kipinde izlenen bir veri dizini var mı (`shadow_edited`'i çağırmaya değer mi)."""
    return bool(_shadow_seen)


def shadow_unsynced() -> bool:
    """
    Denetim kipi: son `shadow_check`'ten beri kancasız değişmiş ve kataloğu henüz yeniden eşitlenmemiş bir
    dizin var mı (`shadow_resync`'i çağırmaya değer mi). Önceki testlerden kalanlar sayılmaz: depoları
    kapanmıştır, onları bir sonraki açılış eşitler.
    """
    return bool(_shadow_unsynced)


def shadow_edited(path: PathLike) -> None:
    """
    Denetim kipi: `path` kancasız yazıldı ya da silindi (test kendi dosyasını yazdı). Yol, bu süreçte açılmış
    bir veri dizininin altındaysa (`.meta/` dışında) o dizin işaretlenir. Katalog, ürün kodu dizine bir daha
    dokunduğunda (`shadow_resync`) ya da depo bir daha açıldığında imzalara bakılmadan uzlaştırılır; o ana
    kadar dizinin karşılaştırması atlanır. tests/conftest.py çağırır.
    """
    if not _shadow_checking():
        return
    with _shadow_lock:
        key = _shadow_dir_of(path, _shadow_seen)
        if key is not None:
            _shadow_unsynced.add(key)
            note = _shadow_notes.get(key)
            if note is not None:
                note.edited = True


def shadow_written(path: PathLike) -> None:
    """
    Denetim kipi: ürün kodu (eski düzen yazıcısı) `path`'i yazmak ya da silmek üzere. Yol dizinlenen bir kökün
    altındaysa veri dizini "karşılaştırılacak" olarak not edilir ve yazmanın ardından bir kanca beklenir:
    test bitene kadar kanca çağrılmazsa `shadow_check` bunu fark olarak bildirir. tests/conftest.py çağırır.
    """
    if not _shadow_checking():
        return
    with _shadow_lock:
        key = _shadow_locate(path)
        if key is None:
            return
        note = _shadow_notes.setdefault(key, _ShadowNote(key))
        note.touched = True
        if note.unhooked is None:
            note.unhooked = os.fspath(path)


def shadow_resync(path: PathLike) -> None:
    """
    Denetim kipi: ürün kodu `path`'e dokunmak üzere. Yol, kancasız değişmiş bir veri dizinindeyse ve deposu
    açıksa katalog şimdi, imzalara bakılmadan uzlaştırılır: testin elle yazdıkları kataloğa girer ve ardından
    gelen ürün yazmasının kancası gerçekten sınanır. Depo kapalıysa iş bir sonraki açılışa kalır.
    tests/conftest.py çağırır (denetim kancasından, işlem yapılmadan önce).
    """
    if getattr(_shadow_resyncing, "on", False) or not _shadow_checking():
        return
    with _shadow_lock:
        key = _shadow_dir_of(path, _shadow_unsynced)
    if key is None:
        return
    store = _registry.get((key, False)) or _registry.get((key, True))
    if store is None or store.closed or not hasattr(store, "catalog"):
        return
    _shadow_resyncing.on = True
    try:
        store._sync_catalog()
    finally:
        _shadow_resyncing.on = False


def shadow_check() -> List[str]:
    """
    Denetim kipi: son çağrıdan beri bir kancanın ya da ürün kodunun dokunduğu her veri dizinini denetler ve
    bulduklarını döndürür (boş liste: hepsi yerinde). Notlar silinir. İki denetim:

      * ürün kodu dizinlenen bir köke yazdı ve ardından kanca çağrılmadı: yazma yerinin kancası eksik;
      * katalog, aynı ağacın sıfırdan kurulmuş haline eşit değil (`CatalogAdmin.diff_from_rebuild`).

    Kancası depolama hatasıyla başarısız olan ve testin kendisinin son ürün çağrısından sonra değiştirdiği
    dizinler atlanır. Depo kapatılmış olabilir: katalog ayrı bir bağlantıyla okunur ve açılıştaki uzlaştırma
    çalışmaz.
    """
    with _shadow_lock:
        notes = list(_shadow_notes.values())
        _shadow_notes.clear()
        _shadow_stale.update(_shadow_unsynced)
        _shadow_unsynced.clear()
    out: List[str] = []
    for note in notes:
        if not note.touched or note.failed or note.edited:
            continue
        if note.unhooked is not None:
            out.append(f"{note.data_dir}: written without a shadow hook afterwards: {note.unhooked}")
            continue
        if not os.path.isfile(catalog_path(note.data_dir)):
            continue
        admin = CatalogAdmin(note.data_dir, league_names=note.names)
        try:
            out.extend(f"{note.data_dir}: {line}" for line in admin.diff_from_rebuild())
        finally:
            admin.close()
    return out


def _shadow(data_dir: PathLike, what: str, action: Callable[[Store], None], *,
            store: Optional[Store] = None) -> bool:
    """
    Kancaların ortak gövdesi: depoyu açar (ya da verilen `store`'u kullanır) ve `action`'ı çalıştırır; katalog
    güncellendiyse True. Katalog ikincil bir kayıttır: açılamayan depo ya da yazılamayan katalog çağıranı
    (yazmayı) düşürmez; uyarı veri dizini başına bir kez yazılır (bir kanca yeniden başarana kadar sonrakiler
    DEBUG). Katalog güncel değilse (açılışta ya da önceki bir kancada eşitlenemedi) `action` yerine baştan
    eşitlenir; o eşitleme az önce yazılanı da kapsar.
    """
    key = _shadow_key(data_dir)
    note: Optional[_ShadowNote] = None
    if _shadow_checking():
        with _shadow_lock:
            note = _shadow_notes.setdefault(key, _ShadowNote(os.path.abspath(os.fspath(data_dir))))
            note.touched = True
            note.unhooked = None  # bu kanca, kendisinden önce yazılanları izliyor
    try:
        if store is None:
            store = open_store(data_dir)
        if store._catalog_ready:
            try:
                action(store)
            except BaseException:
                store._catalog_ready = False  # katalog geride kaldı: sonraki kanca baştan uzlaştırır
                store._catalog_retry_at = 0.0
                raise
        elif time.monotonic() < store._catalog_retry_at or not store._sync_catalog():
            if note is not None:
                note.failed = True
            return False
        _shadow_warned.pop(key, None)
        if note is not None:
            note.names = dict(store._names_used)
        return True
    except _CATALOG_ERRORS as e:
        if note is not None:
            note.failed = True
        log = logger.debug if _shadow_warned.get(key) else logger.warning
        _shadow_warned[key] = True
        log("The catalog of %s was not updated after %s; the next open reconciles it: %s", data_dir, what, e)
        return False
    except Exception:
        if _shadow_checking():
            raise
        logger.exception("Unexpected error while updating the catalog of %s after %s", data_dir, what)
        return False


def _rebuild_in_place(store: Store) -> None:
    """Temizlemeden sonra: uzlaştırma varlık satırlarını silmez, bu yüzden katalog yerinde yeniden kurulur."""
    store.catalog.rebuild(mode=MODE_IN_PLACE)


__all__ = [
    "CLEAR_SCOPES",
    "ClearReport",
    "LAYOUT_VERSION",
    "MIN_READER_LAYOUT",
    "SHADOW_CHECK_ENV",
    "Store",
    "StoreInfo",
    "check_layout",
    "open_store",
    "read_schema",
    "shadow_check",
    "shadow_edited",
    "shadow_resync",
    "shadow_unsynced",
    "shadow_watching",
    "shadow_written",
]
