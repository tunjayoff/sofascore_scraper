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
(`Store.follows`), olay akışları (`Store.streams`), izleyici durumu (`Store.watch`), `Store.info` ve
kataloğun okuma API'leri var: maçlar (`Store.events`) ve maç dışı varlıklar (`Store.entities`). Okuma
API'leri kataloğa sorar; `open_store` kataloğu kurmaz (şeması yaratılır), kuran ve güncel tutan
dizinleyicidir. Değişiklik günlüğü ve yazma API'leri (put, history, migrate, export, backup) kendi plan
maddeleriyle eklenir.
Store içindeki modüller `_state`, `_catalog` ve `_leases` özniteliklerini kullanır; paket dışındaki kod
yalnızca açık yöntemleri.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from src.store import files, layout
from src.store.catalog import CATALOG_SCHEMA, Catalog, CatalogState, catalog_path
from src.store.derive import DERIVE_VERSION
from src.store.entities import EntityStore
from src.store.errors import PayloadCorrupt, PayloadMissing, SchemaTooNew, StoreError
from src.store.events import EventStore
from src.store.follows import FollowStore
from src.store.jobs import import_legacy_jobs
from src.store.lease import Lease, LeaseInfo, LeaseManager
from src.store.manifest import MANIFEST_FORMAT
from src.store.state import RuntimeFacts, StateDb
from src.store.streams import StreamLog
from src.store.watch import WatchStateStore
from src.version import __version__ as APP_VERSION

logger = logging.getLogger("Store")

PathLike = Union[str, "os.PathLike[str]"]

LAYOUT_VERSION = 3  # bu kodun yazdığı düzen (v3 ağacı ve dosya adları)
MIN_READER_LAYOUT = 3  # bu kodun yazdığı dizini okuyabilen en eski düzen sürümü
META_BUILT_AT = "built_at"  # katalogun son kurulduğu an (dizinleyici yazar)


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
    veritabanı yine açılır (state.db'ye sahip bilgisi, katalog şeması gibi küçük kayıtlar yazılabilir).
    """

    def __init__(self, data_dir: PathLike, *, create: bool = True, readonly: bool = False) -> None:
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
        """
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

    # --- açık API ---------------------------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

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
        """Veritabanı bağlantılarını kapatır ve depoyu kayıt defterinden çıkarır. Alınmış kilitler sahiplerinde kalır."""
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


def open_store(data_dir: Optional[PathLike] = None, *, create: bool = True, readonly: bool = False) -> Store:
    """
    Veri dizininin deposunu açar; aynı süreçte aynı dizin (ve aynı `readonly`) için hep aynı nesne döner.

    data_dir=None → DATA_DIR ortam değişkeni, o da yoksa "data". create=True: `.meta/`, `schema.json`,
    `state.db` ve `catalog.db` eksikse kurulur; create=False: bunlar yoksa StoreError. Dizin daha yeni bir
    düzenle ya da state şemasıyla yazılmışsa SchemaTooNew.
    """
    root = os.fspath(data_dir) if data_dir is not None else (os.getenv("DATA_DIR") or "data")
    path = os.path.abspath(root)
    key = (os.path.normcase(os.path.realpath(path)), bool(readonly))
    with _registry_lock:
        store = _registry.get(key)
        if store is None or store.closed:
            store = Store(path, create=create, readonly=readonly)
            _registry[key] = store
        return store


__all__ = [
    "LAYOUT_VERSION",
    "MIN_READER_LAYOUT",
    "Store",
    "StoreInfo",
    "check_layout",
    "open_store",
    "read_schema",
]
