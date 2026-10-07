"""
catalog.db ve state.db'nin ortak SQLite bağlantı katmanı (docs/design/01-storage.md, bölüm 3.2).

İki dosya da aynı kurallarla açılır; fark yalnızca `synchronous` değeridir (catalog.db NORMAL, state.db FULL)
ve her modül kendi şemasını, sürüm denetimini ve işlem sarmalayıcılarını tutar. Burada olanlar:

  * `check_sqlite_version`: Python'la gelen SQLite en az 3.24 olmalı.
  * `connect` ve `Connection`: `isolation_level=None` (işlemler açıkça başlatılır), satırlar `sqlite3.Row`,
    bağlantı başka iş parçacığından kapatılabilir ve çöpe giderken kendini kapatır.
  * `create_database_file`: olmayan veritabanı dosyasını SQLite'tan önce, Store'un öteki dosyalarının
    izniyle oluşturur (karar S15): dosya sürecin umask'ine uyar, `-wal` ve `-shm` dosyaları da onun iznini alır.
  * `configure`: bölüm 3.2'deki PRAGMA'lar. WAL'a geçiş `set_journal_mode` ile yeniden denenir: SQLite kip
    değişimi için `busy_timeout`u beklemez, yeni bir dosyayı aynı anda açan ikinci bağlantıya hemen
    "database is locked" verir. WAL açılamıyorsa (ağ dosya sistemi) DELETE kipine düşülür; `warn_no_wal`
    bunu dosya başına bir kez bildirir.
  * `ThreadConnections`: iş parçacığı başına bir bağlantı.
  * `begin_immediate`, `rollback`: yazma işlemi baştan kilitlenir; kilit alınamazsa StoreBusy.
  * `is_busy_error`, `to_store_error`: sqlite3 hatalarının StoreError ailesine çevrilmesi.
  * `split_statements`: SQL betiğini deyimlere böler (DDL bir işlemin içinde deyim deyim çalıştırılır).

Hangi hatanın çevrileceğine çağıran karar verir: catalog.py bütün sqlite3 hatalarını StoreError'a çevirir,
state.py yalnızca kilit zaman aşımını (StoreBusy); diğerleri orada `sqlite3.Error` olarak çıkar.
"""
from __future__ import annotations

import contextlib
import errno
import logging
import os
import re
import sqlite3
import threading
import time
import weakref
from typing import Callable, List, Optional, Sequence, Set, Tuple, Union

from sofascore_scraper.store.errors import CatalogCorrupt, StoreBusy, StoreError

PathLike = Union[str, "os.PathLike[str]"]

MIN_SQLITE: Tuple[int, int, int] = (3, 24, 0)  # UPSERT, satır değerleri, kısmi dizinler, WITHOUT ROWID
BUSY_TIMEOUT_MS = 5000
JOURNAL_SIZE_LIMIT = 64 * 1024 * 1024  # denetim noktasından sonra WAL en çok 64 MB kalır
WAL_RETRY_PAUSE = 0.005  # saniye; kip değişimi denemeleri arasındaki ilk bekleme, her denemede iki katına çıkar
WAL_RETRY_MAX_PAUSE = 0.1

_SQLITE_BUSY = 5
_SQLITE_ERRNO = {3: errno.EPERM, 8: errno.EROFS, 13: errno.ENOSPC}  # PERM, READONLY, FULL
_SQLITE_CORRUPT_CODES = frozenset({11, 26})  # CORRUPT, NOTADB
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_TRAILING_COMMENT_RE = re.compile(r"[ \t]*--[^\n]*")

# Yeni veritabanı dosyası: yalnızca yoksa oluşturulur (O_EXCL), hiçbir zaman kesilmez; Windows'ta ikili kip
_CREATE_FLAGS = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)

_wal_warned: Set[str] = set()
_wal_warned_lock = threading.Lock()
# Bağlantı kapatmaları sıraya sokulur (bkz. `Connection.close`). Yeniden girilebilir: kapatma sırasında çöp
# toplayıcı aynı iş parçacığında başka bir bağlantının `__del__`ini çalıştırabilir.
_close_lock = threading.RLock()


# --- sürüm ve hata çevirisi ---------------------------------------------------------------------------

def check_sqlite_version(version: Optional[Sequence[int]] = None) -> None:
    """Python'la gelen SQLite en az 3.24 olmalı; değilse StoreError (bölüm 3.2)."""
    found = tuple(version if version is not None else sqlite3.sqlite_version_info)
    if found < MIN_SQLITE:
        need = ".".join(str(n) for n in MIN_SQLITE[:2])
        have = ".".join(str(n) for n in found)
        raise StoreError(f"SQLite {need} or newer is required; this Python has {have}", detail=have)


def _primary_code(exc: BaseException) -> Optional[int]:
    code = getattr(exc, "sqlite_errorcode", None)  # Python 3.11+
    return code & 0xFF if isinstance(code, int) else None


def is_busy_error(exc: BaseException) -> bool:
    """
    SQLITE_BUSY: başka bir bağlantı kilidi `busy_timeout` boyunca bırakmadı. SQLITE_LOCKED ("database table
    is locked") sayılmaz: o, aynı bağlantının kendi içindeki çakışmadır ve beklemekle geçmez.
    """
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
        return StoreBusy(f"The store is busy: the database lock could not be taken ({detail}){suffix}", path=where, detail=detail)
    code = _primary_code(exc)
    text = detail.lower()
    corrupt = code in _SQLITE_CORRUPT_CODES if code is not None else (
        "not a database" in text or "malformed" in text)
    if corrupt:
        return CatalogCorrupt(f"The database cannot be read ({detail}){suffix}", path=where, detail=detail)
    errno_code = _SQLITE_ERRNO.get(code) if code is not None else (
        errno.ENOSPC if "disk is full" in text else errno.EROFS if "readonly database" in text else None)
    return StoreError(f"Database operation failed ({detail}){suffix}", path=where, errno_code=errno_code,
                      detail=detail)


# --- bağlantı ve ayarları -----------------------------------------------------------------------------

class Connection(sqlite3.Connection):
    """
    Açıkça kapatılmadan bırakılan bağlantı (iş parçacığı bitti ya da sahibi çöpe gitti) kendini kapatır.
    Python 3.13+ kapatılmamış bağlantı için ResourceWarning'i bağlantının kendi sonlandırıcısından verir ve
    döngüsel çöpte o sonlandırıcı, sarmalayan nesnenin `__del__`inden önce çalışabilir; bu yüzden kapatma
    sarmalayıcıda değil burada yapılır. Python alt sınıfı olduğu için zayıf başvuruyla da izlenebilir.

    Kapatma sıraya sokulur: aynı bağlantıyı iki iş parçacığı aynı anda kapatabilir. Biten bir iş parçacığının
    bağlantısı o iş parçacığında `__del__` ile kapanırken, `ThreadConnections.close_all` / `close_finished`
    onu zayıf kayıttan (sonlandırıcı sürerken hâlâ görünür) alıp başka bir iş parçacığından da kapatabilir.
    Python 3.10'da `sqlite3.Connection.close`, `sqlite3_close`'u iki çağrı için de çalıştırır ve süreç çöker
    (SIGABRT); 3.11'den beri ikinci çağrı zararsızdır. Kilit ikinci çağrıyı birincinin bitmesine bekletir, o
    zaman bağlantı zaten kapalıdır.
    """

    def close(self) -> None:
        with _close_lock:
            super().close()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()


def create_database_file(path: PathLike) -> bool:
    """
    Veritabanı dosyası yoksa onu boş olarak, Store'un öteki dosyalarının izniyle (`files.STORE_FILE_MODE`)
    oluşturur; oluşturduysa True döner. SQLite'ın dosyayı yaratabileceği her açılıştan önce çağrılır (karar S15).

    SQLite yeni bir veritabanını 0644 eksi umask ile yaratır: umask 002 altında bile grup yazamaz, oysa
    veri dizinindeki öteki her dosya 0664'tür ve aynı gruptan ikinci hesap ilk yazmada hata alır. Var olan
    dosyanın iznine SQLite dokunmaz, `-wal`, `-shm` ve `-journal` dosyalarına da veritabanı dosyasının
    iznini verir; bu yüzden boş dosyayı önceden doğru izinle oluşturmak yeter. İzni çekirdek umask'e göre
    belirler: umask okunmaz, chmod yapılmaz (sofascore_scraper/store/files.py ile aynı kural).

    O_EXCL: dosya tek bir sistem çağrısıyla "yoksa oluştur" diye açılır. Var olan dosya açılmaz, kesilmez
    ve izni değişmez; aynı anda oluşturan iki süreçten biri oluşturur, öteki hazır dosyayı bulur. Boş dosya
    SQLite için geçerli, boş bir veritabanıdır.

    Hata burada bildirilmez (dizin yok, yazılamıyor, salt okunur dosya sistemi, sarkan sembolik bağ): False
    döner ve aynı yolu hemen ardından açan SQLite kendi hatasını verir, yani çağıranın hata yolu değişmez.
    Bellekteki (":memory:"), geçici ("") ve URI ile verilen veritabanları dosya değildir: dokunulmaz.
    """
    where = os.fspath(path)
    if not where or where == ":memory:" or where.startswith("file:"):
        return False
    # İçe aktarma burada: modül yüklenirken yalnızca hata sınıflarına bağlıdır (Store'un geri kalanını yüklemez)
    from sofascore_scraper.store.files import STORE_FILE_MODE

    try:
        fd = os.open(where, _CREATE_FLAGS, STORE_FILE_MODE)
    except OSError:
        return False
    os.close(fd)
    return True


def connect(path: PathLike, *, busy_timeout_ms: int = BUSY_TIMEOUT_MS) -> Connection:
    """
    Dosyaya ayarlanmamış bir bağlantı açar (PRAGMA'lar için `configure`). sqlite3 hataları çevrilmez.
    Dosya yoksa önce `create_database_file` ile oluşturulur: yeni veritabanı sürecin umask'ine uyar.

    isolation_level=None: Python kendiliğinden BEGIN atmaz, işlemler açıkça yönetilir.
    check_same_thread=False: bağlantıyı açan iş parçacığı kullanır, ama `ThreadConnections.close_all` ve
    biten iş parçacıklarının temizliği başka bir iş parçacığından kapatabilmelidir.
    """
    create_database_file(path)
    conn = sqlite3.connect(os.fspath(path), timeout=busy_timeout_ms / 1000.0, isolation_level=None,
                           check_same_thread=False, factory=Connection)
    conn.row_factory = sqlite3.Row
    return conn


def set_journal_mode(conn: sqlite3.Connection, mode: str, busy_timeout_ms: int = BUSY_TIMEOUT_MS, *,
                     pause: float = WAL_RETRY_PAUSE) -> str:
    """
    Günlük kipini ayarlar ve geçerli kipi döndürür. Dosya henüz o kipte değilse SQLite paylaşımlı kilidi
    özel kilide yükseltir; iki bağlantı bunu aynı anda denerse kilitlenmeyi önlemek için `busy_timeout`u
    beklemeden SQLITE_BUSY döner (yeni bir veri dizinini aynı anda açan iki süreç). O durumda burada, aynı
    süre sınırıyla, yeniden denenir; süre dolunca ya da başka bir hatada sqlite3 hatası olduğu gibi çıkar.
    """
    deadline = time.monotonic() + busy_timeout_ms / 1000.0
    delay = pause
    while True:
        try:
            row = conn.execute(f"PRAGMA journal_mode = {mode}").fetchone()
            return str(row[0]).lower() if row and row[0] is not None else ""
        except sqlite3.OperationalError as exc:
            if not is_busy_error(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, WAL_RETRY_MAX_PAUSE)


def configure(conn: sqlite3.Connection, *, synchronous: str = "NORMAL", busy_timeout_ms: int = BUSY_TIMEOUT_MS,
              request_wal: Optional[Callable[[sqlite3.Connection], str]] = None) -> str:
    """
    Bölüm 3.2'deki PRAGMA'ları yeni bir bağlantıya uygular ve geçerli günlük kipini döndürür: "wal" ya da,
    WAL açılamadıysa, "delete" (bellekteki veritabanında "memory"). synchronous: catalog.db için NORMAL
    (elektrik kesintisinde son işlemler kaybolabilir, uzlaştırma dosyalardan onarır), state.db için FULL.

    request_wal: WAL isteğini yapan işlev (SQLite'ın yanıtını döndürür); verilmezse `set_journal_mode`.
    """
    if synchronous not in ("NORMAL", "FULL"):
        raise ValueError(f"Geçersiz synchronous değeri: {synchronous!r}")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    mode = request_wal(conn) if request_wal is not None else set_journal_mode(conn, "WAL", busy_timeout_ms)
    if mode != "wal":
        # Ağ dosya sistemi: WAL paylaşımlı bellek ister. Tek süreç kipine düş (bölüm 3.2, 6.4).
        mode = set_journal_mode(conn, "DELETE", busy_timeout_ms) or "delete"
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute(f"PRAGMA journal_size_limit = {JOURNAL_SIZE_LIMIT}")
    conn.execute(f"PRAGMA synchronous = {synchronous}")
    return mode


def warn_no_wal(log: logging.Logger, path: PathLike, mode: str) -> bool:
    """WAL'a alınamayan dosyayı bildirir: süreç boyunca dosya başına bir kez. Uyarı yazıldıysa True döner."""
    where = os.fspath(path)
    with _wal_warned_lock:
        if where in _wal_warned:
            return False
        _wal_warned.add(where)
    log.warning(
        "%s could not be switched to WAL mode (a network file system?); opened in %s journal mode. "
        "Only one process at a time may use this data directory: %s",
        os.path.basename(where), mode.upper(), where,
    )
    return True


def close_quietly(conn: sqlite3.Connection) -> None:
    """Bağlantıyı kapatır; kapatırken çıkan sqlite3 hatası yutulur."""
    with contextlib.suppress(sqlite3.Error):
        conn.close()


# --- iş parçacığı başına bağlantı ---------------------------------------------------------------------

class ThreadConnections:
    """
    Bir veritabanı dosyasının iş parçacığı başına bağlantıları (bölüm 3.2).

    Bağlantıyı güçlü başvuruyla yalnızca sahibi olan iş parçacığının yerel deposu tutar; kayıt onu zayıf
    başvuruyla izler. İş parçacığı bitince bağlantı da gider ve kendini kapatır (`Connection.__del__`).
    `len()` izlenen (henüz çöpe gitmemiş) bağlantıların sayısıdır.

    `close_all()` bütün iş parçacıklarının bağlantılarını kapatır; ondan sonra `current()` her iş
    parçacığında None döner, böylece çağıran isterse yeniden açar (Catalog) ya da reddeder (StateDb).
    """

    def __init__(self) -> None:
        self._local = threading.local()
        self._lock = threading.Lock()
        self._owners: "weakref.WeakKeyDictionary[Connection, threading.Thread]" = weakref.WeakKeyDictionary()
        self._generation = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._owners)

    def current(self) -> Optional[Connection]:
        """Çağıran iş parçacığının bağlantısı; yoksa ya da `close_all()` ile kapatıldıysa None."""
        bound: Optional[Tuple[int, Connection]] = getattr(self._local, "bound", None)
        if bound is None:
            return None
        if bound[0] != self._generation:
            self._local.bound = None  # close_all() çağrılmış: bağlantı kapalı
            return None
        return bound[1]

    def adopt(self, conn: Connection) -> None:
        """`conn`u çağıran iş parçacığının bağlantısı yapar. Öncekini kapatmaz (`release`)."""
        with self._lock:
            self._owners[conn] = threading.current_thread()
            self._local.bound = (self._generation, conn)

    def release(self) -> Optional[Connection]:
        """Çağıran iş parçacığının bağlantısını kayıttan çıkarır ve döndürür; kapatmak çağıranın işidir."""
        conn = self.current()
        self._local.bound = None
        if conn is not None:
            with self._lock:
                self._owners.pop(conn, None)
        return conn

    def close_finished(self) -> None:
        """
        Bitmiş iş parçacıklarının hâlâ yaşayan bağlantılarını kapatır. Böyle bir bağlantı ancak başka bir
        yerden tutuluyorsa kalır; iş parçacığının kendi başvurusu bitişte düşer.
        """
        with self._lock:
            finished = [conn for conn, thread in self._owners.items() if not thread.is_alive()]
            for conn in finished:
                del self._owners[conn]
        for conn in finished:
            close_quietly(conn)

    def close_all(self) -> None:
        """Bütün iş parçacıklarının bağlantılarını kapatır. Başka bir iş parçacığı o an sorgu çalıştırmamalı."""
        with self._lock:
            self._generation += 1
            connections = list(self._owners)
            self._owners.clear()
        for conn in connections:
            close_quietly(conn)


# --- işlemler -----------------------------------------------------------------------------------------

def begin_immediate(conn: sqlite3.Connection, path: Optional[PathLike] = None) -> None:
    """
    Yazma işlemini `BEGIN IMMEDIATE` ile başlatır: yazar kilidi en başta bekler, ortada kilit yükseltirken
    hata almaz. Kilit `busy_timeout` içinde alınamazsa StoreBusy; diğer sqlite3 hataları olduğu gibi çıkar.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if is_busy_error(exc):
            raise to_store_error(exc, path) from exc
        raise


def rollback(conn: sqlite3.Connection) -> None:
    """Açık işlem varsa geri alır. Bağlantı kullanılamaz durumdaysa susar: asıl hata zaten yükseliyor."""
    if conn.in_transaction:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")


# --- SQL betikleri ------------------------------------------------------------------------------------

def strip_comments(sql: str) -> str:
    """Yorumları atar. Tırnak içini ayırt etmez: yalnızca "boş mu", "neyle başlıyor" sorularında kullanılır."""
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", sql)).strip()


def split_statements(script: str) -> List[str]:
    """
    SQL betiğini tek tek çalıştırılabilir deyimlere böler. `executescript` açık işlemi kendiliğinden
    bitirdiği için DDL bir işlemin içinde deyim deyim çalıştırılır.

    Sınırı `sqlite3.complete_statement` belirler: dize ya da yorum içindeki noktalı virgül ve
    CREATE TRIGGER ... BEGIN ... END gövdesi bölünmez. Deyimden sonra aynı satırda kalan `--` yorumu o
    deyimle birlikte kalır; yalnızca yorumdan ya da tek bir `;`den oluşan parçalar atılır.
    Sonda yarım kalan deyim ValueError.
    """
    statements: List[str] = []
    start = position = 0
    while True:
        semicolon = script.find(";", position)
        if semicolon < 0:
            break
        position = semicolon + 1
        if not sqlite3.complete_statement(script[start:position]):
            continue
        trailing = _TRAILING_COMMENT_RE.match(script, position)
        if trailing is not None:
            position = trailing.end()
        statement = script[start:position].strip()
        if strip_comments(statement) != ";":
            statements.append(statement)
        start = position
    rest = strip_comments(script[start:])
    if rest:
        raise ValueError(f"SQL betiği yarım bir deyimle bitiyor (noktalı virgül eksik): {rest[:80]!r}")
    return statements


__all__ = [
    "MIN_SQLITE",
    "BUSY_TIMEOUT_MS",
    "JOURNAL_SIZE_LIMIT",
    "WAL_RETRY_PAUSE",
    "Connection",
    "ThreadConnections",
    "begin_immediate",
    "check_sqlite_version",
    "close_quietly",
    "configure",
    "connect",
    "create_database_file",
    "is_busy_error",
    "rollback",
    "set_journal_mode",
    "split_statements",
    "strip_comments",
    "to_store_error",
    "warn_no_wal",
]
