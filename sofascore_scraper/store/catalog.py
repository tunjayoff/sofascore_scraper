"""
catalog.db: bağlantılar, şema ve sürüm denetimi (docs/design/01-storage.md, bölüm 3.2, 3.3, 6.2, 6.3, 7.2).

Katalog, yük dosyalarının türetilmiş SQLite dizinidir: silinebilir ve dosyalardan yeniden kurulur. Bu
yüzden göç betiği yoktur; dosyanın şema sürümü (PRAGMA user_version) ya da türetme sürümü
(`meta.derive_version`) koddakinden farklıysa katalog yeniden kurulur (kuran: dizinleyici).

Bağlantı kuralları (bölüm 3.2); state.db ile ortak olan kısım sofascore_scraper/store/sqlite.py'de durur:
  * İş parçacığı başına bir bağlantı (thread-local), `isolation_level=None`, işlemler açıkça başlatılır.
  * Her yeni bağlantıya `configure` PRAGMA'ları uygular: WAL, busy_timeout 5 sn, foreign_keys,
    temp_store MEMORY, journal_size_limit 64 MB, synchronous NORMAL.
  * WAL açılamıyorsa (ağ dosya sistemi) DELETE günlük kipine düşülür ve uyarı yazılır: veri dizinini
    aynı anda yalnızca bir süreç kullanabilir.
  * Yazma işlemleri hep `BEGIN IMMEDIATE` ile başlar (`Catalog.write`): yazar kilidi en başta bekler,
    ortada kilit yükseltirken hata almaz. Süre dolarsa StoreBusy. Katalogun yazma kilidi, veri dizinindeki
    bütün yük yazımlarının süreçler arası kilididir (bölüm 6.2).
  * Okuyucular kilit almaz; WAL her okuma işlemine tutarlı bir anlık görüntü verir (bölüm 6.3).

`configure`, `check_sqlite_version`, `is_busy_error` ve `to_store_error` sofascore_scraper/store/sqlite.py'den gelir ve
eski adlarıyla buradan da içe aktarılabilir. `split_statements` aynı bölücüdür; yarım betikte ValueError
yerine StoreError verir.
"""
from __future__ import annotations

import contextlib
import functools
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple, Union

from sofascore_scraper.store import layout
from sofascore_scraper.store import sqlite as _sqlite
from sofascore_scraper.store.derive import DERIVE_VERSION
from sofascore_scraper.store.errors import CatalogCorrupt, StoreError
from sofascore_scraper.store.sqlite import (
    BUSY_TIMEOUT_MS,
    JOURNAL_SIZE_LIMIT,
    MIN_SQLITE,
    Connection,
    ThreadConnections,
    begin_immediate,
    check_sqlite_version,
    close_quietly,
    configure,
    connect,
    is_busy_error,
    rollback,
    to_store_error,
    warn_no_wal,
)

logger = logging.getLogger(__name__)

PathLike = Union[str, "os.PathLike[str]"]

CATALOG_SCHEMA = 2  # PRAGMA user_version; sofascore_scraper/store/schema/catalog.sql değişince artırılır (2: P30)
APPLICATION_ID = 0x53464331  # "SFC1": dosyanın bir SofaScore kataloğu olduğunu işaretler
REOPEN_CHECK_SECONDS = 1.0  # dosyanın yerine yenisi konmuş mu: en çok bu sıklıkta bakılır (bölüm 6.3)

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema", "catalog.sql")

META_DERIVE_VERSION = "derive_version"

# Katalogun neden yeniden kurulması gerektiği (CatalogState.rebuild_reason)
REBUILD_MISSING = "missing"  # dosya yok ya da boş
REBUILD_APPLICATION_ID = "application_id"  # başka bir SQLite dosyası
REBUILD_SCHEMA = "schema_version"  # user_version farklı (eski ya da daha yeni)
REBUILD_DERIVE = "derive_version"  # şema uyuyor; satırlar başka bir türetme sürümüyle yazılmış ya da hiç kurulmamış
REBUILD_CORRUPT = "corrupt"  # dosya SQLite veritabanı olarak okunamıyor

_SCHEMA_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,30}")


# --- bağlantıdan bağımsız yardımcılar -----------------------------------------------------------------

_set_journal_mode = _sqlite.set_journal_mode  # ST-06'daki adı; WAL'a geçişi yeniden deneyen ortak işlev


def split_statements(script: str) -> List[str]:
    """
    SQL betiğini tek tek çalıştırılabilir deyimlere böler (sofascore_scraper/store/sqlite.py); yarım kalan betik StoreError.
    `executescript` açık işlemi kendiliğinden bitirdiği için DDL bir işlemin içinde deyim deyim çalıştırılır.
    """
    try:
        return _sqlite.split_statements(script)
    except ValueError as exc:
        raise StoreError(str(exc), detail=str(exc)) from exc


@functools.lru_cache(maxsize=1)
def schema_statements() -> Tuple[str, ...]:
    """sofascore_scraper/store/schema/catalog.sql içindeki DDL deyimleri."""
    try:
        with open(SCHEMA_FILE, encoding="utf-8") as f:
            script = f.read()
    except OSError as exc:
        raise StoreError.from_exception(exc, SCHEMA_FILE, reading=True) from exc
    return tuple(split_statements(script))


def catalog_path(data_dir: PathLike) -> str:
    """Veri dizinindeki katalog dosyası: DATA_DIR/.meta/catalog.db."""
    return layout.resolve(data_dir, layout.CATALOG_DB)


def sidecar_paths(path: PathLike) -> Tuple[str, str]:
    """Veritabanının WAL ve paylaşılan bellek dosyaları (dosya yeniden yaratılırken eskileri silinir)."""
    base = os.fspath(path)
    return base + "-wal", base + "-shm"


def _file_identity(path: str) -> Optional[Tuple[int, int]]:
    """Dosyanın kimliği (aygıt, inode); dosya yoksa ya da dosya sistemi inode vermiyorsa None."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino) if st.st_ino else None


# --- durum ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CatalogState:
    """Katalog dosyasının bulunduğu hali (`Catalog.inspect`)."""

    exists: bool
    application_id: Optional[int] = None
    schema_version: Optional[int] = None  # PRAGMA user_version
    derive_version: Optional[int] = None  # meta.derive_version; None: katalog hiç kurulmamış
    rebuild_reason: Optional[str] = None  # None: olduğu gibi kullanılabilir; yoksa REBUILD_* değerlerinden biri
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.rebuild_reason is None

    @property
    def schema_ok(self) -> bool:
        """Tablolar bu kodun şemasında: yeniden kurma yerinde yapılabilir (dosya değiştirilmeden)."""
        return self.rebuild_reason in (None, REBUILD_DERIVE)


@dataclass
class _Slot:
    """Bir iş parçacığının bağlantısı ve işlem durumu."""

    conn: Connection
    identity: Optional[Tuple[int, int]]
    checked_at: float
    depth: int = 0  # iç içe write() derinliği; 0: yazma işlemi yok


# --- katalog ----------------------------------------------------------------------------------------

class Catalog:
    """
    Bir catalog.db dosyasının bağlantıları. Nesne iş parçacıkları arasında paylaşılır; her iş parçacığı
    kendi bağlantısını alır. `close()` hepsini kapatır; sonraki kullanım yeniden açar.

    attach: {şema adı: dosya yolu}; her bağlantıya ATTACH edilir (ör. takip edilen turnuvalar için
    {"state": ".../state.db"}). Dosya var olmalıdır.
    """

    def __init__(self, path: PathLike, *, busy_timeout_ms: int = BUSY_TIMEOUT_MS,
                 attach: Optional[Mapping[str, PathLike]] = None):
        check_sqlite_version()
        self.path = os.path.abspath(os.fspath(path))
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._attach: Dict[str, str] = {}
        for name, other in (attach or {}).items():
            if not _SCHEMA_NAME_RE.fullmatch(name) or name in ("main", "temp"):
                raise ValueError(f"Invalid ATTACH name: {name!r}")
            self._attach[name] = os.path.abspath(os.fspath(other))
        self._local = threading.local()  # iş parçacığının _Slot'u; bağlantının kendisi _connections'ta
        self._lock = threading.Lock()
        self._connections = ThreadConnections()
        self._journal_mode: Optional[str] = None
        # tablo → (sütunlar, birincil anahtar sütunları, varsayılanı olmayan anahtar sütunları)
        self._tables: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]] = {}

    def __enter__(self) -> "Catalog":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- bağlantılar ---------------------------------------------------------------------------------

    def connection(self) -> sqlite3.Connection:
        """
        Çağıran iş parçacığının bağlantısı (gerekirse açılır). Satırlar `sqlite3.Row`'dur. Bağlantı başka
        bir iş parçacığına verilmez. İşlem dışındaki her deyim kendi anlık görüntüsünü görür; birden çok
        deyimin aynı görüntüyü görmesi için `read()`, yazmak için `write()` kullanılır.
        """
        return self._slot().conn

    @property
    def journal_mode(self) -> str:
        """"wal" ya da WAL açılamadıysa "delete" (tek süreç kipi; doctor bunu bildirir)."""
        self._slot()
        return self._journal_mode or ""

    def close(self) -> None:
        """Bütün iş parçacıklarının bağlantılarını kapatır. Başka bir iş parçacığı o an sorgu çalıştırmamalı."""
        with self._lock:
            self._tables.clear()
        self._connections.close_all()

    def _slot(self) -> _Slot:
        slot: Optional[_Slot] = getattr(self._local, "slot", None)
        if slot is not None and self._connections.current() is not slot.conn:
            slot = None  # close() çağrılmış: bağlantı kapalı
        if slot is not None and not slot.conn.in_transaction and self._replaced(slot):
            # Başka bir süreç kataloğu yeniden yaratıp yerine koydu; eski bağlantı silinmiş dosyayı okur
            self._connections.release()
            with self._lock:
                self._tables.clear()
            close_quietly(slot.conn)
            slot = None
        if slot is None:
            slot = self._open_slot()
            self._local.slot = slot
        return slot

    def _replaced(self, slot: _Slot) -> bool:
        now = time.monotonic()
        if now - slot.checked_at < REOPEN_CHECK_SECONDS:
            return False
        slot.checked_at = now
        current = _file_identity(self.path)
        return current is not None and slot.identity is not None and current != slot.identity

    def _open_slot(self) -> _Slot:
        conn: Optional[Connection] = None
        identity: Optional[Tuple[int, int]] = None
        for _ in range(3):
            before = _file_identity(self.path)
            conn = self._connect()
            identity = _file_identity(self.path)
            if before is None or before == identity:
                break
            # Bağlanırken dosya değiştirildi: hangi dosyanın açıldığı belirsiz, yeniden dene
            close_quietly(conn)
            conn = None
        if conn is None:
            conn = self._connect()
            identity = _file_identity(self.path)
        self._connections.close_finished()  # biten iş parçacıklarının bağlantıları
        self._connections.adopt(conn)
        return _Slot(conn=conn, identity=identity, checked_at=time.monotonic())

    def _connect(self) -> Connection:
        directory = os.path.dirname(self.path)
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            raise StoreError.from_exception(exc, directory) from exc
        for name, other in self._attach.items():
            if not os.path.isfile(other):
                raise StoreError(f"No database to ATTACH ({name}): {other}", path=other)
        try:
            conn = connect(self.path, busy_timeout_ms=self.busy_timeout_ms)
        except sqlite3.Error as exc:
            raise to_store_error(exc, self.path) from exc
        try:
            mode = configure(conn, synchronous="NORMAL", busy_timeout_ms=self.busy_timeout_ms)
            for name, other in self._attach.items():
                conn.execute(f"ATTACH DATABASE ? AS {name}", (other,))
        except sqlite3.Error as exc:
            close_quietly(conn)
            raise to_store_error(exc, self.path) from exc
        self._journal_mode = mode
        if mode != "wal":
            warn_no_wal(logger, self.path, mode)
        return conn

    # -- işlemler ------------------------------------------------------------------------------------

    @contextlib.contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """
        Yazma işlemi: `BEGIN IMMEDIATE` ... `COMMIT`; blok hata fırlatırsa `ROLLBACK`. Kilit `busy_timeout`
        içinde alınamazsa StoreBusy. İç içe kullanım SAVEPOINT olur: içteki blok hata verirse yalnızca
        kendi değişiklikleri geri alınır. Blok içindeki sqlite3 hataları StoreError'a çevrilir.
        """
        slot = self._slot()
        conn = slot.conn
        if slot.depth:
            name = f"catalog_write_{slot.depth}"
            try:
                conn.execute(f"SAVEPOINT {name}")
            except sqlite3.Error as exc:
                raise to_store_error(exc, self.path) from exc
            slot.depth += 1
            try:
                yield conn
                conn.execute(f"RELEASE {name}")
            except BaseException as exc:
                if conn.in_transaction:
                    try:
                        conn.execute(f"ROLLBACK TO {name}")
                        conn.execute(f"RELEASE {name}")
                    except sqlite3.Error:  # dıştaki işlem de bitmiş: geri alınacak bir şey kalmadı
                        pass
                if isinstance(exc, sqlite3.Error):
                    raise to_store_error(exc, self.path) from exc
                raise
            finally:
                slot.depth -= 1
            return

        if conn.in_transaction:
            # Okuma işlemini yazmaya yükseltmek tam da kaçınılan şey: ortada SQLITE_BUSY alınabilir
            raise StoreError("A write cannot start inside an open read transaction", path=self.path)
        try:
            begin_immediate(conn, self.path)
        except sqlite3.Error as exc:
            raise to_store_error(exc, self.path) from exc
        slot.depth = 1
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException as exc:
            rollback(conn)
            if isinstance(exc, sqlite3.Error):
                raise to_store_error(exc, self.path) from exc
            raise
        finally:
            slot.depth = 0

    @contextlib.contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """
        Okuma işlemi: bloktaki bütün sorgular aynı anlık görüntüyü görür (ilk sorguda alınır), o sırada
        başka bir sürecin yaptığı yazmalar görünmez. Açık bir işlemin içinde çağrılırsa ona katılır.
        """
        conn = self._slot().conn
        joined = conn.in_transaction
        try:
            if not joined:
                conn.execute("BEGIN")
            yield conn
        except sqlite3.Error as exc:
            raise to_store_error(exc, self.path) from exc
        finally:
            if not joined:
                rollback(conn)  # salt okuma: COMMIT ile ROLLBACK aynı

    def _require_write(self) -> sqlite3.Connection:
        slot = self._slot()
        if not slot.depth:
            raise StoreError("This call must be made inside a Catalog.write() block", path=self.path)
        return slot.conn

    # -- şema ve sürümler ----------------------------------------------------------------------------

    def inspect(self) -> CatalogState:
        """
        Dosyanın halini okur; hiçbir şey yazmaz, dosya yoksa yaratmaz. Sırayla: dosya var mı, boş mu,
        application_id bizim mi, user_version CATALOG_SCHEMA mı, meta.derive_version DERIVE_VERSION mı.
        """
        if not os.path.exists(self.path):
            return CatalogState(exists=False, rebuild_reason=REBUILD_MISSING)
        try:
            # PRAGMA uygulanmamış yalın bağlantı: yabancı ya da eski bir dosyanın kipi değiştirilmez
            conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000.0, isolation_level=None)
        except sqlite3.Error as exc:
            raise to_store_error(exc, self.path) from exc
        try:
            # Tek okuma işlemi: başka bir süreç o sırada şemayı yaratıyorsa ya hiçbirini ya hepsini görürüz
            conn.execute("BEGIN")
            application_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
            schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            objects = int(conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0])
            derive_version: Optional[int] = None
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone():
                row = conn.execute("SELECT value FROM meta WHERE key = ?", (META_DERIVE_VERSION,)).fetchone()
                if row is not None:
                    try:
                        derive_version = int(row[0])
                    except (TypeError, ValueError):
                        derive_version = None
            conn.execute("ROLLBACK")
        except sqlite3.Error as exc:
            error = to_store_error(exc, self.path)
            if isinstance(error, CatalogCorrupt):
                return CatalogState(exists=True, rebuild_reason=REBUILD_CORRUPT, detail=str(exc))
            raise error from exc
        finally:
            close_quietly(conn)

        if application_id == 0 and schema_version == 0 and objects == 0:
            reason: Optional[str] = REBUILD_MISSING
        elif application_id != APPLICATION_ID:
            reason = REBUILD_APPLICATION_ID
        elif schema_version != CATALOG_SCHEMA:
            reason = REBUILD_SCHEMA
        elif derive_version != DERIVE_VERSION:
            reason = REBUILD_DERIVE
        else:
            reason = None
        return CatalogState(exists=True, application_id=application_id, schema_version=schema_version,
                            derive_version=derive_version, rebuild_reason=reason)

    def prepare(self, *, create: bool = True) -> CatalogState:
        """
        Açılışta çağrılır. Dosyanın **bulunduğu** hali döndürür; dosya yoksa ya da boşsa (ve create=True
        ise) şemayı yaratır. Dönen `rebuild_reason`:

          None             kullanılabilir.
          "missing"        dosya yoktu; şimdi boş bir şema var, dizinleyici doldurup `stamp_derive_version`
                           çağırmalı. O ana kadar sonraki açılışlar "derive_version" görür, yani yarıda
                           kalan bir kurulum kendini kullanılabilir göstermez.
          "derive_version" şema uyuyor: yerinde yeniden kurulur.
          diğerleri        dosyaya dokunulmadı: yeni dosya kurulup bunun yerine konmalı (bölüm 3.4).
        """
        state = self.inspect()
        if state.rebuild_reason == REBUILD_MISSING and create:
            self.create_schema()
        return state

    def create_schema(self) -> bool:
        """
        Boş dosyaya DDL'i, application_id'yi ve user_version'ı tek işlemde yazar. Başka bir süreç araya
        girip şemayı yaratmışsa hiçbir şey yapmaz ve False döner. meta.derive_version yazılmaz.
        """
        with self.write() as conn:
            objects = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
            if objects or conn.execute("PRAGMA user_version").fetchone()[0]:
                return False
            for statement in schema_statements():
                conn.execute(statement)
            conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            conn.execute(f"PRAGMA user_version = {CATALOG_SCHEMA}")
        return True

    def stamp_derive_version(self) -> None:
        """Satırların bu kodun türetme sürümüyle yazıldığını işaretler (yeniden kurmanın son adımı)."""
        self.set_meta(META_DERIVE_VERSION, str(DERIVE_VERSION))

    def get_meta(self, key: str) -> Optional[str]:
        try:
            row = self.connection().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        except sqlite3.Error as exc:
            raise to_store_error(exc, self.path) from exc
        return None if row is None else str(row[0])

    def set_meta(self, key: str, value: str) -> None:
        conn = self._require_write()
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def quick_check(self) -> List[str]:
        """`PRAGMA quick_check`: bulunan sorunlar; boş liste = sağlam. Okunamayan dosya da sorun olarak döner."""
        try:
            rows = self.connection().execute("PRAGMA quick_check").fetchall()
        except CatalogCorrupt as exc:
            return [exc.detail or str(exc)]
        except sqlite3.DatabaseError as exc:
            if is_busy_error(exc):
                raise to_store_error(exc, self.path) from exc
            return [str(exc)]
        return [str(row[0]) for row in rows if str(row[0]).lower() != "ok"]

    # -- satır yazma ---------------------------------------------------------------------------------

    def tables(self) -> List[str]:
        """Katalogdaki tabloların adları, yaratılış sırasıyla."""
        try:
            rows = self.connection().execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
            ).fetchall()
        except sqlite3.Error as exc:
            raise to_store_error(exc, self.path) from exc
        return [str(row[0]) for row in rows]

    def _table_info(self, conn: sqlite3.Connection,
                    table: str) -> Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]:
        info = self._tables.get(table)
        if info is None:
            rows = conn.execute("SELECT name, pk, dflt_value FROM pragma_table_info(?)", (table,)).fetchall()
            if not rows:
                raise StoreError(f"The catalog has no table {table!r}", path=self.path)
            columns = tuple(str(r[0]) for r in rows)
            keyed = sorted((r for r in rows if r[1]), key=lambda r: r[1])
            primary = tuple(str(r[0]) for r in keyed)
            required = tuple(str(r[0]) for r in keyed if r[2] is None)
            info = (columns, primary, required)
            self._tables[table] = info
        return info

    def upsert(self, table: str, rows: Iterable[Mapping[str, Any]], *, on_conflict: str = "update") -> int:
        """
        Satırları birincil anahtara göre ekler ya da günceller; `write()` bloğunun içinde çağrılır.
        Sözlüğün anahtarları sütun adlarıdır. Yalnızca verilen sütunlar yazılır: var olan satırın
        verilmeyen sütunları olduğu gibi kalır, yeni satırda DDL'deki varsayılanı alır. Anahtar sütunları
        verilmelidir; DDL'de varsayılanı olanlar (`sub`, `start_ts`) atlanabilir.

        on_conflict="update": çakışan satırın verilen sütunları güncellenir.
        on_conflict="ignore": çakışan satıra dokunulmaz ("yalnızca yoksa ekle").
        Dönen değer eklenen ya da güncellenen satır sayısıdır.
        """
        if on_conflict not in ("update", "ignore"):
            raise ValueError(f"Invalid on_conflict: {on_conflict!r}")
        conn = self._require_write()
        columns, primary, required = self._table_info(conn, table)
        groups: Dict[Tuple[str, ...], List[Tuple[Any, ...]]] = {}
        for row in rows:
            names = tuple(row)
            groups.setdefault(names, []).append(tuple(row[name] for name in names))
        written = 0
        for names, values in groups.items():
            unknown = [name for name in names if name not in columns]
            missing = [name for name in required if name not in names]
            if unknown or missing:
                raise StoreError(
                    f"{table}: invalid row (unknown columns: {unknown}, missing key columns: {missing})",
                    path=self.path)
            updates = [name for name in names if name not in primary]
            action = "DO NOTHING"
            if on_conflict == "update" and updates:
                action = "DO UPDATE SET " + ", ".join(f'"{name}" = excluded."{name}"' for name in updates)
            column_list = ", ".join(f'"{name}"' for name in names)
            key_list = ", ".join(f'"{name}"' for name in primary)
            placeholders = ", ".join("?" for _ in names)
            sql = (f'INSERT INTO "{table}" ({column_list}) VALUES ({placeholders}) '
                   f"ON CONFLICT({key_list}) {action}")
            try:
                written += conn.executemany(sql, values).rowcount
            except sqlite3.Error as exc:
                raise to_store_error(exc, self.path) from exc
        return written

    def clear(self) -> None:
        """Bütün tabloların bütün satırlarını siler (yerinde yeniden kurmanın ilk adımı); `write()` içinde."""
        conn = self._require_write()
        for table in self.tables():
            conn.execute(f'DELETE FROM "{table}"')


__all__ = [
    "CATALOG_SCHEMA",
    "DERIVE_VERSION",
    "APPLICATION_ID",
    "MIN_SQLITE",
    "BUSY_TIMEOUT_MS",
    "JOURNAL_SIZE_LIMIT",
    "SCHEMA_FILE",
    "META_DERIVE_VERSION",
    "REBUILD_MISSING",
    "REBUILD_APPLICATION_ID",
    "REBUILD_SCHEMA",
    "REBUILD_DERIVE",
    "REBUILD_CORRUPT",
    "Catalog",
    "CatalogState",
    "catalog_path",
    "sidecar_paths",
    "check_sqlite_version",
    "configure",
    "is_busy_error",
    "to_store_error",
    "split_statements",
    "schema_statements",
]
