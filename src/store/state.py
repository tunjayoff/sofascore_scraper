"""
state.db: yeniden kurulamayan durumun SQLite dosyası (docs/design/01-storage.md, bölüm 3.1-3.3 ve 7.3).

catalog.db tümüyle dosyalardan türetilir ve silinip yeniden kurulabilir; state.db ise yetkilidir:
takipler, işler, olay akışları, sink imleçleri, izleyici durumu, çalışma zamanı bilgileri. Bu yüzden
`synchronous = FULL` ile açılır ve şeması numaralı, yalnızca ileri giden geçişlerle değişir.

Bu modülde:
  * `StateDb`: dosyayı açar, geçişleri uygular, iş parçacığı başına bir bağlantı verir, yazma işlemlerini
    `BEGIN IMMEDIATE` ile başlatır (kilit `busy_timeout` içinde alınamazsa StoreBusy).
  * Geçiş çalıştırıcısı: `migrations/state/NNNN_<ad>.sql` dosyaları sırayla, her biri tek işlemde.
    Geçişten önce dosya `state.db.bak-v<eski sürüm>` olarak kopyalanır; başarısız geçiş geri alınır ve
    açılış, betiğin adını söyleyen bir StoreError ile biter. Dosya koddan yeniyse SchemaTooNew.
  * `RuntimeFacts`: süreçler arası küçük bilgiler için anahtar/değer API'si (`runtime` tablosu).

Geçişler bölüm 7.3'e göre `maintenance` kilidi altında çalışır; kilitler ST-10 ile gelir. O zamana kadar
süreçler arası sıralamayı geçiş işleminin kendisi sağlar (BEGIN IMMEDIATE, sürüm işlem içinde yeniden okunur).

SQLite hataları (bozuk dosya, açılamayan yol) burada çevrilmez, `sqlite3.Error` olarak çıkar: iş deposunun
bugünkü çağıranları bunları öyle yakalar (src/web/routes/settings.py).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
import weakref
from dataclasses import dataclass
from typing import Any, Iterator, List, Mapping, Optional, Sequence, Set, Tuple, Union

from src.store import files
from src.store.errors import SchemaTooNew, StoreBusy, StoreError

logger = logging.getLogger("Store")

PathLike = Union[str, "os.PathLike[str]"]

APPLICATION_ID = 0x53465331  # "SFS1": dosyanın bir state.db olduğunu işaretler (catalog.db: 0x53464331)
MIN_SQLITE: Tuple[int, int, int] = (3, 24, 0)  # UPSERT, satır değerleri, kısmi dizinler, WITHOUT ROWID
BUSY_TIMEOUT_MS = 5000
JOURNAL_SIZE_LIMIT = 64 * 1024 * 1024  # denetim noktasından sonra WAL en çok 64 MB kalır
MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations", "state")
BACKUP_SUFFIX = ".bak-v"  # state.db.bak-v<eski sürüm>

_MIGRATION_FILE_RE = re.compile(r"(\d{4})_([a-z0-9_]+)\.sql")
_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+[\"`\[]?(\w+)[\"`\]]?\s+ADD\s+(?:COLUMN\s+)?[\"`\[]?(\w+)[\"`\]]?", re.IGNORECASE
)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_SQLITE_BUSY, _SQLITE_LOCKED = 5, 6
_WAL_RETRY_PAUSE = 0.01  # saniye

_wal_warned: Set[str] = set()
_wal_warned_lock = threading.Lock()


def check_sqlite_version(version: Optional[Sequence[int]] = None) -> None:
    """Kütüphanedeki SQLite 3.24'ten eskiyse StoreError (bölüm 3.2)."""
    found = tuple(version if version is not None else sqlite3.sqlite_version_info)
    if found < MIN_SQLITE:
        need = ".".join(str(n) for n in MIN_SQLITE[:2])
        have = ".".join(str(n) for n in found)
        raise StoreError(f"SQLite {need} ya da daha yenisi gerekli; bu Python {have} ile derlenmiş", detail=have)


def is_busy_error(exc: BaseException) -> bool:
    """Hata "veritabanı kilitli" mi (SQLITE_BUSY / SQLITE_LOCKED)? Python 3.10'da hata kodu yok: iletiye bakılır."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return (code & 0xFF) in (_SQLITE_BUSY, _SQLITE_LOCKED)
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def _strip_comments(sql: str) -> str:
    """Yorumları atar. Tırnak içini ayırt etmez: yalnızca "boş mu" ve "ALTER ile mi başlıyor" sorularında kullanılır."""
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", sql)).strip()


def split_statements(script: str) -> List[str]:
    """
    SQL betiğini deyimlere böler. Sınırı `sqlite3.complete_statement` belirler, bu yüzden dize içindeki
    noktalı virgül ve CREATE TRIGGER ... BEGIN ... END gövdesi bölünmez. Sonda yarım kalan deyim ValueError.
    """
    statements: List[str] = []
    pieces = script.split(";")
    buffer = ""
    for piece in pieces[:-1]:
        buffer += piece + ";"
        if sqlite3.complete_statement(buffer):
            if _strip_comments(buffer) != ";":
                statements.append(buffer.strip())
            buffer = ""
    if _strip_comments(buffer + pieces[-1]):
        raise ValueError("betik yarım bir deyimle bitiyor (noktalı virgül eksik)")
    return statements


@dataclass(frozen=True)
class Migration:
    """Bir geçiş betiği: `NNNN_<ad>.sql`. `version`, uygulandıktan sonraki `user_version` değeridir."""

    version: int
    name: str  # uzantısız dosya adı, ör. "0001_initial"
    path: str

    def statements(self) -> List[str]:
        try:
            with open(self.path, encoding="utf-8") as f:
                return split_statements(f.read())
        except OSError as e:
            raise StoreError.from_exception(e, self.path, reading=True) from e
        except ValueError as e:
            raise StoreError(f"state.db geçiş betiği okunamadı: {self.name} ({e})", path=self.path,
                             detail=str(e)) from e


def load_migrations(directory: Optional[PathLike] = None) -> List[Migration]:
    """
    Dizindeki geçişleri sürüm sırasıyla döndürür. Numaralar 1'den başlar ve boşluksuz olmalıdır
    (0001, 0002, ...): eksik ya da yinelenen numara StoreError. Kalıba uymayan dosyalar yok sayılır.
    """
    root = os.fspath(directory) if directory is not None else MIGRATIONS_DIR
    try:
        names = sorted(os.listdir(root))
    except OSError as e:
        raise StoreError.from_exception(e, root, reading=True) from e
    found: List[Migration] = []
    for name in names:
        match = _MIGRATION_FILE_RE.fullmatch(name)
        if match:
            found.append(Migration(int(match.group(1)), name[: -len(".sql")], os.path.join(root, name)))
    found.sort(key=lambda m: m.version)
    for index, migration in enumerate(found, start=1):
        if migration.version != index:
            raise StoreError(
                f"state.db geçişleri sıralı değil: {index:04d} bekleniyordu, {migration.name} bulundu", path=root
            )
    return found


class _Connection(sqlite3.Connection):
    """
    Açıkça kapatılmadan bırakılan bağlantı (iş parçacığı bitti ya da depo çöpe gitti) kendini kapatır.
    Python alt sınıfı olduğu için zayıf başvuruyla izlenebilir: StateDb.close() başka iş parçacıklarının
    bağlantılarını böyle bulur, bitmiş iş parçacıklarınınkini ise canlı tutmaz.
    """

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()


class StateDb:
    """
    Bir state.db dosyası. Kurulurken dosya (yoksa) oluşturulur ve eksik geçişler uygulanır.

    Bağlantılar iş parçacığı başınadır ve nesne yaşadıkça açık kalır; `close()` hepsini kapatır.
    Bütün yöntemler iş parçacığı güvenlidir.
    """

    def __init__(self, path: PathLike, *, migrations_dir: Optional[PathLike] = None) -> None:
        check_sqlite_version()
        self.path = os.fspath(path)
        self.journal_mode = ""  # "wal", ya da WAL kurulamadıysa "delete" (tek süreç kipi)
        self._migrations = load_migrations(migrations_dir)
        self._local = threading.local()
        self._connections: "weakref.WeakSet[_Connection]" = weakref.WeakSet()
        self._connections_lock = threading.Lock()
        self._closed = False
        try:
            self._check_identity()
            self._migrate()
        except BaseException:
            self.close()
            raise

    # --- bağlantılar ------------------------------------------------------------------------

    @property
    def latest_version(self) -> int:
        """Bu kodun bildiği en yeni şema sürümü."""
        return self._migrations[-1].version if self._migrations else 0

    @property
    def schema_version(self) -> int:
        """Dosyanın şema sürümü (`PRAGMA user_version`)."""
        return int(self.connection().execute("PRAGMA user_version").fetchone()[0])

    def _request_wal(self, conn: sqlite3.Connection) -> str:
        """
        WAL kipini ister ve SQLite'ın yanıtını döndürür. Kip değişimi dosyada tek başına olmayı gerektirir ve
        SQLite bu kilit için `busy_timeout`u beklemez: aynı anda açılan başka bir bağlantı varsa hemen
        "database is locked" verir. Bu yüzden bekleme burada yapılır; süre dolarsa StoreBusy.
        """
        deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
        while True:
            try:
                return str(conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
            except sqlite3.OperationalError as e:
                if not is_busy_error(e):
                    raise
                if time.monotonic() >= deadline:
                    raise StoreBusy(f"state.db meşgul: {BUSY_TIMEOUT_MS} ms içinde açılamadı: {self.path}",
                                    path=self.path, detail=str(e)) from e
                time.sleep(_WAL_RETRY_PAUSE)

    def _connect(self) -> _Connection:
        # isolation_level=None: Python kendiliğinden BEGIN atmaz, işlemler açıkça yönetilir.
        # check_same_thread=False: close() başka iş parçacığının bağlantısını kapatabilsin.
        conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None,
                               check_same_thread=False, factory=_Connection)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            answer = mode = self._request_wal(conn)
            if mode != "wal":
                # Ağ dosya sistemi: WAL paylaşımlı bellek ister. Tek süreç kipine düş (bölüm 3.2, 6.4).
                mode = str(conn.execute("PRAGMA journal_mode = DELETE").fetchone()[0]).lower()
                with _wal_warned_lock:
                    first = self.path not in _wal_warned
                    _wal_warned.add(self.path)
                if first:
                    logger.warning(
                        "state.db WAL kipine alınamadı (%s); DELETE kipiyle açıldı: veri dizinini aynı anda "
                        "yalnızca bir süreç kullanmalı: %s", answer, self.path,
                    )
            self.journal_mode = mode
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA temp_store = MEMORY")
            conn.execute(f"PRAGMA journal_size_limit = {JOURNAL_SIZE_LIMIT}")
            conn.execute("PRAGMA synchronous = FULL")  # state.db'yi hiçbir şey onaramaz
        except BaseException:
            conn.close()
            raise
        return conn

    def connection(self) -> sqlite3.Connection:
        """Çağıran iş parçacığının bağlantısı (ilk çağrıda açılır). İşlem dışında otomatik kayıt kipindedir."""
        if self._closed:
            raise StoreError(f"state.db kapatılmış: {self.path}", path=self.path)
        conn: Optional[_Connection] = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
            with self._connections_lock:
                self._connections.add(conn)
        return conn

    def close(self) -> None:
        """Bütün iş parçacıklarının bağlantılarını kapatır. Sonrasında nesne kullanılamaz."""
        self._closed = True
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for conn in connections:
            with contextlib.suppress(sqlite3.Error):
                conn.close()

    def _begin_immediate(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            if is_busy_error(e):
                raise StoreBusy(f"state.db meşgul: yazma kilidi {BUSY_TIMEOUT_MS} ms içinde alınamadı: {self.path}",
                                path=self.path, detail=str(e)) from e
            raise

    @contextlib.contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """
        Yazma işlemi: BEGIN IMMEDIATE ile başlar (yazar baştan bekler, ortada kilit yükseltmede kalmaz),
        gövde hatasız biterse COMMIT, hata verirse ROLLBACK. Kilit alınamazsa StoreBusy.
        Aynı iş parçacığında iç içe kullanılırsa dıştaki işleme katılır.
        """
        conn = self.connection()
        if conn.in_transaction:
            yield conn
            return
        self._begin_immediate(conn)
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
            raise

    # --- kimlik ve geçişler -----------------------------------------------------------------

    def _check_identity(self) -> None:
        """
        Dosya ya boş ya da bir state.db olmalı. Denetim, dosyaya hiçbir şey yazmayan ayrı bir bağlantıyla
        ve WAL'a geçmeden önce yapılır: yanlışlıkla verilen başka bir veritabanı (ör. 2.x jobs.db) değişmez.
        """
        probe = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        try:
            probe.execute("BEGIN")  # üç okuma aynı anlık görüntüden
            version = int(probe.execute("PRAGMA user_version").fetchone()[0])
            app_id = int(probe.execute("PRAGMA application_id").fetchone()[0])
            objects = int(probe.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0])
            probe.execute("COMMIT")
        except sqlite3.OperationalError as e:
            if is_busy_error(e):
                raise StoreBusy(f"state.db meşgul: {BUSY_TIMEOUT_MS} ms içinde okunamadı: {self.path}",
                                path=self.path, detail=str(e)) from e
            raise
        finally:
            probe.close()
        if app_id != APPLICATION_ID and (app_id != 0 or version != 0 or objects != 0):
            raise StoreError(f"Dosya bir state.db değil (application_id {app_id:#x}): {self.path}", path=self.path)
        if version > self.latest_version:
            raise SchemaTooNew(path=self.path, component="state", found=version, supported=self.latest_version)

    def _migrate(self) -> None:
        conn = self.connection()
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > self.latest_version:  # denetimden sonra başka bir süreç yükseltmiş olabilir
            raise SchemaTooNew(path=self.path, component="state", found=version, supported=self.latest_version)
        pending = [m for m in self._migrations if m.version > version]
        if not pending:
            return
        if version > 0:
            self._backup(conn, version)
        for migration in pending:
            self._apply(conn, migration)

    def backup_path(self, version: int) -> str:
        """Şema sürümü `version` iken alınan kopyanın yolu."""
        return f"{self.path}{BACKUP_SUFFIX}{version}"

    def _backup(self, conn: sqlite3.Connection, version: int) -> None:
        """Geçişten önce dosyayı SQLite'ın çevrimiçi yedekleme API'siyle kopyalar; eski sürüm başına bir kopya."""
        target = self.backup_path(version)
        tmp = f"{target}.{uuid.uuid4().hex}.tmp"
        try:
            copy = sqlite3.connect(tmp)
            try:
                conn.backup(copy)
                copied = int(copy.execute("PRAGMA user_version").fetchone()[0])
            finally:
                copy.close()
            if copied != version:
                # Başka bir süreç araya girip geçişi yaptı: bu kopya eski sürüm değil, onun kopyası geçerli
                return
            files.replace(tmp, target)
        except sqlite3.Error as e:
            raise StoreError(f"state.db geçişten önce kopyalanamadı ({e}): {target}", path=target,
                             detail=str(e)) from e
        finally:
            with contextlib.suppress(StoreError):
                files.remove(tmp)
        logger.info("state.db şema sürümü %s kopyalandı: %s", version, target)

    @staticmethod
    def _column_exists(conn: sqlite3.Connection, statement: str) -> bool:
        """`ALTER TABLE t ADD COLUMN c` deyiminin sütunu zaten var mı (betik yeniden çalıştırılabilsin)."""
        match = _ADD_COLUMN_RE.match(_strip_comments(statement))
        if not match:
            return False
        table, column = match.group(1), match.group(2)
        columns = {str(row[1]).lower() for row in conn.execute(f'PRAGMA table_info("{table}")')}
        return column.lower() in columns

    def _apply(self, conn: sqlite3.Connection, migration: Migration) -> bool:
        """Bir geçişi tek işlemde uygular. Başka bir süreç araya girip uyguladıysa False döner."""
        statements = migration.statements()
        self._begin_immediate(conn)
        try:
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current >= migration.version:
                conn.execute("ROLLBACK")
                return False
            for statement in statements:
                if not self._column_exists(conn, statement):
                    conn.execute(statement)
            conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            conn.execute(f"PRAGMA user_version = {migration.version}")
            conn.execute("COMMIT")
        except BaseException as e:
            if conn.in_transaction:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
            # sqlite3.Warning: Python 3.11 ve öncesinde "aynı anda tek deyim" hatası bu sınıfla gelir
            if isinstance(e, (sqlite3.Error, sqlite3.Warning)):
                raise StoreError(f"state.db geçişi başarısız, geri alındı: {migration.name} ({e})",
                                 path=migration.path, detail=str(e)) from e
            raise
        logger.info("state.db geçişi uygulandı: %s", migration.name)
        return True

    # --- meta tablosu -----------------------------------------------------------------------

    def meta_get(self, key: str) -> Optional[str]:
        row = self.connection().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def meta_set(self, key: str, value: str) -> None:
        with self.write() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


@dataclass(frozen=True)
class RuntimeFact:
    """`runtime` tablosunun bir satırı: değeri yazan sürecin kimliği ve yazıldığı an ile birlikte."""

    value: Mapping[str, Any]
    pid: Optional[int]
    updated_at: float  # epoch saniye (UTC)


class RuntimeFacts:
    """
    Süreçler arası küçük bilgiler, ör. "bridge_health" (bölüm 2.3). Satırlar yalnızca bilgidir: yazan süreç
    ölmüş olabilir, okuyan `pid` ve `updated_at` ile ne kadar güveneceğine kendisi karar verir.
    """

    def __init__(self, state: StateDb) -> None:
        self._state = state

    def get(self, key: str) -> Optional[RuntimeFact]:
        row = self._state.connection().execute(
            "SELECT value_json, pid, updated_at FROM runtime WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["value_json"])
        except ValueError:
            return None  # okunamayan satır yok sayılır; sonraki set() üzerine yazar
        if not isinstance(value, dict):
            return None
        return RuntimeFact(value=value, pid=row["pid"], updated_at=float(row["updated_at"]))

    def set(self, key: str, value: Mapping[str, Any]) -> None:
        try:
            value_json = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as e:
            raise StoreError(f"Çalışma zamanı bilgisi JSON'a çevrilemedi: {key} ({e})", detail=str(e)) from e
        with self._state.write() as conn:
            conn.execute(
                """
                INSERT INTO runtime (key, value_json, pid, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json, pid = excluded.pid, updated_at = excluded.updated_at
                """,
                (key, value_json, os.getpid(), int(time.time())),
            )


__all__ = [
    "APPLICATION_ID",
    "MIGRATIONS_DIR",
    "Migration",
    "RuntimeFact",
    "RuntimeFacts",
    "StateDb",
    "check_sqlite_version",
    "is_busy_error",
    "load_migrations",
    "split_statements",
]
