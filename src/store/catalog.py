"""
catalog.db: bağlantılar, şema ve sürüm denetimi (docs/design/01-storage.md, bölüm 3.2, 3.3, 6.2, 6.3, 7.2).

Katalog, yük dosyalarının türetilmiş SQLite dizinidir: silinebilir ve dosyalardan yeniden kurulur. Bu
yüzden göç betiği yoktur; dosyanın şema sürümü (PRAGMA user_version) ya da türetme sürümü
(`meta.derive_version`) koddakinden farklıysa katalog yeniden kurulur (kuran: dizinleyici).

Bağlantı kuralları (bölüm 3.2):
  * İş parçacığı başına bir bağlantı (thread-local), `isolation_level=None`, işlemler açıkça başlatılır.
  * Her yeni bağlantıya `configure` PRAGMA'ları uygular: WAL, busy_timeout 5 sn, foreign_keys,
    temp_store MEMORY, journal_size_limit 64 MB, synchronous NORMAL.
  * WAL açılamıyorsa (ağ dosya sistemi) DELETE günlük kipine düşülür ve uyarı yazılır: veri dizinini
    aynı anda yalnızca bir süreç kullanabilir.
  * Yazma işlemleri hep `BEGIN IMMEDIATE` ile başlar (`Catalog.write`): yazar kilidi en başta bekler,
    ortada kilit yükseltirken hata almaz. Süre dolarsa StoreBusy. Katalogun yazma kilidi, veri dizinindeki
    bütün yük yazımlarının süreçler arası kilididir (bölüm 6.2).
  * Okuyucular kilit almaz; WAL her okuma işlemine tutarlı bir anlık görüntü verir (bölüm 6.3).

`configure`, `check_sqlite_version`, `is_busy_error` ve `to_store_error` state.db için de kullanılabilir
(orada synchronous FULL).
"""
from __future__ import annotations

import contextlib
import errno
import functools
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple, Union

from src.store import layout
from src.store.derive import DERIVE_VERSION
from src.store.errors import CatalogCorrupt, StoreBusy, StoreError

logger = logging.getLogger(__name__)

PathLike = Union[str, "os.PathLike[str]"]

CATALOG_SCHEMA = 1  # PRAGMA user_version; src/store/schema/catalog.sql değişince artırılır
APPLICATION_ID = 0x53464331  # "SFC1": dosyanın bir SofaScore kataloğu olduğunu işaretler
MIN_SQLITE: Tuple[int, int, int] = (3, 24, 0)  # UPSERT, satır değerleri, kısmi dizin, WITHOUT ROWID
BUSY_TIMEOUT_MS = 5000
JOURNAL_SIZE_LIMIT = 64 * 1024 * 1024  # denetim noktasından sonra WAL bu boyuta kırpılır
REOPEN_CHECK_SECONDS = 1.0  # dosyanın yerine yenisi konmuş mu: en çok bu sıklıkta bakılır (bölüm 6.3)

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema", "catalog.sql")

META_DERIVE_VERSION = "derive_version"

# Katalogun neden yeniden kurulması gerektiği (CatalogState.rebuild_reason)
REBUILD_MISSING = "missing"  # dosya yok ya da boş
REBUILD_APPLICATION_ID = "application_id"  # başka bir SQLite dosyası
REBUILD_SCHEMA = "schema_version"  # user_version farklı (eski ya da daha yeni)
REBUILD_DERIVE = "derive_version"  # şema uyuyor; satırlar başka bir türetme sürümüyle yazılmış ya da hiç kurulmamış
REBUILD_CORRUPT = "corrupt"  # dosya SQLite veritabanı olarak okunamıyor

_SQLITE_BUSY = 5
_SQLITE_ERRNO = {3: errno.EPERM, 8: errno.EROFS, 13: errno.ENOSPC}  # PERM, READONLY, FULL
_SQLITE_CORRUPT_CODES = frozenset({11, 26})  # CORRUPT, NOTADB
_SCHEMA_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,30}")


# --- bağlantıdan bağımsız yardımcılar -----------------------------------------------------------------

def check_sqlite_version(version: Optional[Tuple[int, ...]] = None) -> None:
    """Python'la gelen SQLite en az 3.24 olmalı; değilse StoreError."""
    found = tuple(version) if version is not None else sqlite3.sqlite_version_info
    if found < MIN_SQLITE:
        need = ".".join(str(n) for n in MIN_SQLITE[:2])
        have = ".".join(str(n) for n in found)
        raise StoreError(f"SQLite {need} ya da daha yenisi gerekli; bu Python'daki sürüm {have}")


def _primary_code(exc: BaseException) -> Optional[int]:
    code = getattr(exc, "sqlite_errorcode", None)  # Python 3.11+
    return code & 0xFF if isinstance(code, int) else None


def is_busy_error(exc: BaseException) -> bool:
    """SQLITE_BUSY: başka bir bağlantı kilidi `busy_timeout` boyunca bırakmadı."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = _primary_code(exc)
    if code is not None:
        return code == _SQLITE_BUSY
    return "database is locked" in str(exc).lower()  # Python 3.10: hata kodu yok


def to_store_error(exc: sqlite3.Error, path: Optional[PathLike] = None) -> StoreError:
    """
    sqlite3 hatası → StoreError. Kilit zaman aşımı StoreBusy, okunamayan dosya CatalogCorrupt olur;
    disk dolu / salt okunur hataları errno taşır, böylece `fatal` özelliği "işi durdur" der.
    """
    where = os.fspath(path) if path is not None else None
    detail = str(exc) or type(exc).__name__
    suffix = f": {where}" if where else ""
    if is_busy_error(exc):
        return StoreBusy(f"Depo meşgul: veritabanı kilidi alınamadı ({detail}){suffix}", path=where, detail=detail)
    code = _primary_code(exc)
    text = detail.lower()
    corrupt = code in _SQLITE_CORRUPT_CODES if code is not None else (
        "not a database" in text or "malformed" in text)
    if corrupt:
        return CatalogCorrupt(f"Veritabanı okunamıyor ({detail}){suffix}", path=where, detail=detail)
    errno_code = _SQLITE_ERRNO.get(code) if code is not None else (
        errno.ENOSPC if "disk is full" in text else errno.EROFS if "readonly database" in text else None)
    return StoreError(f"Veritabanı işlemi başarısız ({detail}){suffix}", path=where, errno_code=errno_code,
                      detail=detail)


def configure(conn: sqlite3.Connection, *, synchronous: str = "NORMAL",
              busy_timeout_ms: int = BUSY_TIMEOUT_MS) -> str:
    """
    Bölüm 3.2'deki PRAGMA'ları yeni bir bağlantıya uygular ve geçerli günlük kipini döndürür: "wal" ya da,
    WAL açılamadıysa, "delete" (bellekteki veritabanında "memory"). synchronous: catalog.db için NORMAL
    (elektrik kesintisinde son işlemler kaybolabilir, uzlaştırma dosyalardan onarır), state.db için FULL.
    """
    if synchronous not in ("NORMAL", "FULL"):
        raise ValueError(f"Geçersiz synchronous değeri: {synchronous!r}")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    mode = _set_journal_mode(conn, "WAL", busy_timeout_ms)
    if mode != "wal":
        mode = _set_journal_mode(conn, "DELETE", busy_timeout_ms) or "delete"
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute(f"PRAGMA journal_size_limit = {JOURNAL_SIZE_LIMIT}")
    conn.execute(f"PRAGMA synchronous = {synchronous}")
    return mode


def _set_journal_mode(conn: sqlite3.Connection, mode: str, busy_timeout_ms: int) -> str:
    """
    Günlük kipini ayarlar ve geçerli kipi döndürür. Dosya henüz o kipte değilse SQLite paylaşımlı kilidi
    özel kilide yükseltir; iki bağlantı bunu aynı anda denerse kilitlenmeyi önlemek için `busy_timeout`'u
    beklemeden SQLITE_BUSY döner (yeni bir veri dizinini aynı anda açan iki süreç). O durumda burada,
    aynı süre sınırıyla, yeniden denenir.
    """
    deadline = time.monotonic() + busy_timeout_ms / 1000.0
    delay = 0.005
    while True:
        try:
            row = conn.execute(f"PRAGMA journal_mode = {mode}").fetchone()
            return str(row[0]).lower() if row and row[0] is not None else ""
        except sqlite3.OperationalError as exc:
            if not is_busy_error(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.1)


def split_statements(script: str) -> List[str]:
    """
    SQL betiğini tek tek çalıştırılabilir deyimlere böler (yorumlar ve metin içindeki `;` sayılmaz).
    `executescript` açık işlemi kendiliğinden bitirdiği için DDL bir işlemin içinde deyim deyim çalıştırılır.
    """
    statements: List[str] = []
    buffer = ""
    for line in script.splitlines():
        buffer += line + "\n"
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip() and any(ln.strip() and not ln.strip().startswith("--") for ln in buffer.splitlines()):
        raise StoreError(f"SQL betiği yarım bir deyimle bitiyor: {buffer.strip()[:80]!r}")
    return statements


@functools.lru_cache(maxsize=1)
def schema_statements() -> Tuple[str, ...]:
    """src/store/schema/catalog.sql içindeki DDL deyimleri."""
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


def _rollback(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:  # bağlantı kullanılamaz durumda: asıl hata zaten yükseliyor
            pass


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

    conn: sqlite3.Connection
    generation: int
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
                raise ValueError(f"Geçersiz ATTACH adı: {name!r}")
            self._attach[name] = os.path.abspath(os.fspath(other))
        self._local = threading.local()
        self._lock = threading.Lock()
        self._connections: Dict[threading.Thread, sqlite3.Connection] = {}
        self._generation = 0
        self._journal_mode: Optional[str] = None
        self._warned_no_wal = False
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
            self._generation += 1
            connections = list(self._connections.values())
            self._connections.clear()
            self._tables.clear()
        for conn in connections:
            self._close_quietly(conn)

    @staticmethod
    def _close_quietly(conn: sqlite3.Connection) -> None:
        try:
            conn.close()
        except sqlite3.Error:
            pass

    def _slot(self) -> _Slot:
        slot: Optional[_Slot] = getattr(self._local, "slot", None)
        if slot is not None and slot.generation != self._generation:
            slot = None  # close() çağrılmış: bağlantı kapalı
        if slot is not None and not slot.conn.in_transaction and self._replaced(slot):
            # Başka bir süreç kataloğu yeniden yaratıp yerine koydu; eski bağlantı silinmiş dosyayı okur
            with self._lock:
                self._connections.pop(threading.current_thread(), None)
                self._tables.clear()
            self._close_quietly(slot.conn)
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
        conn: Optional[sqlite3.Connection] = None
        identity: Optional[Tuple[int, int]] = None
        for _ in range(3):
            before = _file_identity(self.path)
            conn = self._connect()
            identity = _file_identity(self.path)
            if before is None or before == identity:
                break
            # Bağlanırken dosya değiştirildi: hangi dosyanın açıldığı belirsiz, yeniden dene
            self._close_quietly(conn)
            conn = None
        if conn is None:
            conn = self._connect()
            identity = _file_identity(self.path)
        with self._lock:
            generation = self._generation
            for thread in [t for t in self._connections if not t.is_alive()]:
                self._close_quietly(self._connections.pop(thread))  # biten iş parçacıklarının bağlantıları
            self._connections[threading.current_thread()] = conn
        return _Slot(conn=conn, generation=generation, identity=identity, checked_at=time.monotonic())

    def _connect(self) -> sqlite3.Connection:
        directory = os.path.dirname(self.path)
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            raise StoreError.from_exception(exc, directory) from exc
        for name, other in self._attach.items():
            if not os.path.isfile(other):
                raise StoreError(f"ATTACH edilecek veritabanı yok ({name}): {other}", path=other)
        try:
            # check_same_thread=False: close() ve biten iş parçacıklarının temizliği başka iş parçacığından yapılır
            conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000.0, isolation_level=None,
                                   check_same_thread=False)
        except sqlite3.Error as exc:
            raise to_store_error(exc, self.path) from exc
        try:
            conn.row_factory = sqlite3.Row
            mode = configure(conn, synchronous="NORMAL", busy_timeout_ms=self.busy_timeout_ms)
            for name, other in self._attach.items():
                conn.execute(f"ATTACH DATABASE ? AS {name}", (other,))
        except sqlite3.Error as exc:
            self._close_quietly(conn)
            raise to_store_error(exc, self.path) from exc
        self._journal_mode = mode
        if mode != "wal" and not self._warned_no_wal:
            self._warned_no_wal = True
            logger.warning(
                f"catalog.db WAL kipine geçirilemedi (ağ dosya sistemi olabilir); {mode.upper()} günlük kipiyle "
                f"açıldı. Bu veri dizinini aynı anda yalnızca bir süreç kullanmalı: {self.path}"
            )
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
            raise StoreError("Açık bir okuma işleminin içinde yazma işlemi başlatılamaz", path=self.path)
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise to_store_error(exc, self.path) from exc
        slot.depth = 1
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException as exc:
            _rollback(conn)
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
                _rollback(conn)  # salt okuma: COMMIT ile ROLLBACK aynı

    def _require_write(self) -> sqlite3.Connection:
        slot = self._slot()
        if not slot.depth:
            raise StoreError("Bu çağrı Catalog.write() bloğunun içinde yapılmalı", path=self.path)
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
            self._close_quietly(conn)

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
                raise StoreError(f"Katalogda böyle bir tablo yok: {table!r}", path=self.path)
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
            raise ValueError(f"Geçersiz on_conflict: {on_conflict!r}")
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
                    f"{table}: geçersiz satır (bilinmeyen sütun: {unknown}, eksik anahtar sütunu: {missing})",
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
