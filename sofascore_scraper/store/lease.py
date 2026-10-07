"""
Kilitler (lease): veri dizinini aynı anda kimin yazabileceğini süreçler arasında belirler
(docs/design/01-storage.md, bölüm 6.1).

Bir kilit, `DATA_DIR/.meta/locks/<ad>.lock` dosyası üzerindeki işletim sistemi kilididir ve `Lease`
nesnesi yaşadıkça tutulur. Süreç ölünce çekirdek kilidi bırakır; temizlenecek bayat kilit kalmaz.
Teknik sofascore_scraper/throttle.py'dekiyle aynıdır: POSIX'te `fcntl.flock`, Windows'ta `msvcrt.locking`.

Kilit tablosu:

    ad               kendi dosyası   ayrıca paylaşımlı tuttuğu     dışladıkları
    writer           writer.lock     maintenance.lock              başka writer; maintenance
    watcher:<spor>   watcher-<spor>  maintenance.lock, live.lock   aynı ad; live; maintenance
    live             live.lock       maintenance.lock              başka live; her watcher; maintenance
    sinks            sinks.lock      -                             başka sinks
    export           export.lock     maintenance.lock              başka export; maintenance
    maintenance      maintenance     -                             sinks dışında her şey

`export`: web'in dışa aktarma işi (FX-23, bulgu F14). Dışa aktarma veriyi yalnızca okur ve `exports/` ile kendi
hazırlık girdilerine yazar; `writer` ile aynı anda çalışabilir (indirme sürerken), ama silme, geri yükleme ve
katalog yeniden kurma (`maintenance`) okuduğu veriyi değiştireceği için onları dışlar. `ssc export` kilit almaz.

`maintenance` ile dışlama: writer, watcher ve live sahipleri maintenance.lock'u paylaşımlı da tutar;
`maintenance` aynı dosyayı dışlayıcı ister ve paylaşımlı tutan biri varsa alamaz. live ile watcher'lar
live.lock üzerinden aynı yolla birbirini dışlar. Windows'ta `msvcrt.locking`in paylaşımlı kipi yoktur:
paylaşımlı sahip dosyanın 64 baytından boş olan ilkini, dışlayıcı sahip 64 baytın tamamını kilitler.

Sahip bilgisi (pid, makine, amaç, başlangıç) state.db'nin `leases` tablosuna yazılır ve yalnızca
bilgidir; kararı işletim sistemi kilidi verir. Bilgi kilit dosyasına yazılmaz: Windows'ta kilitli baytlar
başka süreçlerce okunamaz. Tablo yazılamazsa kilit yine alınır (uyarı loglanır).

Kilit dosyaları Store'un öteki dosyaları gibi `files.STORE_FILE_MODE` ile oluşturulur; izni çekirdek
sürecin umask'ine göre belirler (022 → 0644, 002 → 0664; karar S15). Böylece veri dizinini paylaşan aynı
gruptan ikinci bir hesap, ilk hesabın oluşturduğu kilit dosyasını açıp kilit alabilir. Var olan bir kilit
dosyasının izni değiştirilmez; açılamayan dosya, yolunu taşıyan bir StoreError'dur.

Dosya sistemi kilit desteklemiyorsa (ağ paylaşımı: ENOLCK, ENOTSUP) kilit yine "alınır" ve bir kez
uyarı yazılır: veri dizinini o zaman yalnızca bir süreç kullanmalıdır (bölüm 6.4, WAL açılamadığındaki
tek süreç kipiyle aynı kural). 2.x'te çalışan bir kurulum bu yüzden iş başlatamaz hale gelmez.

`writer` alınırken `.meta/locks/unclean` işareti konur ve temiz bırakılışta silinir. Kilit alınırken
işaret duruyorsa önceki yazar temiz kapanmamıştır (`Lease.unclean`); v3 dosyalarının katalogla
karşılaştırılması çağıranın işidir (bölüm 3.5).

Hazırlık alanı (karar S16): `.meta/tmp` altındaki girdiler sahibinin adını taşır (`writer.<rastgele>`,
`live.<rastgele>`, `export.<rastgele>`). Bir kilit alındığında yalnızca o sahibin yarıda kalmış girdileri
silinir: `writer` kendi girdilerini ve çöpü (`.meta/trash`), `live` kendi girdilerini. Kilide bağlı olmayan
girdiler (dışa aktarma, kilitsiz yazma) `writer` alınırken, bir günden eskiyse silinir. Temizlik kilidi
düşürmez: silinemeyen girdi uyarı olarak yazılır.
"""
from __future__ import annotations

import contextlib
import errno
import logging
import os
import socket
import sqlite3
import threading
import time
import uuid
import weakref
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from sofascore_scraper.store import files, layout
from sofascore_scraper.store.errors import LayoutError, LeaseHeld, StoreError
from sofascore_scraper.store.state import StateDb

logger = logging.getLogger("Store")

PathLike = Union[str, "os.PathLike[str]"]

WRITER = "writer"
WATCHER = "watcher"  # tam ad: "watcher:<spor>"
LIVE = "live"
SINKS = "sinks"
EXPORT = "export"
MAINTENANCE = "maintenance"

SHARED = "shared"
EXCLUSIVE = "exclusive"

UNCLEAN_NAME = os.path.basename(layout.UNCLEAN_MARKER)
MIGRATION_PURPOSE = "op:state_migration"
MIGRATION_WAIT = 5.0  # saniye: aynı anda açılan başka bir sürecin geçişi bitirmesi beklenir

_LOCK_SUFFIX = ".lock"
_RANGE = 64  # Windows: paylaşımlı sahip başına bir bayt, dışlayıcı sahip hepsini kilitler
_POLL = 0.05  # saniye: `wait` süresince yeniden deneme aralığı
# wait=0 olsa da birkaç kez denenir: sahibini arayan bir yoklama (`_exclusively_held`) dosyayı
# mikrosaniyeler boyunca paylaşımlı tutar ve tek denemelik bir istek boş kilidi dolu sanabilirdi.
_MIN_ATTEMPTS = 3
_RETRY_PAUSE = 0.005
# İşletim sisteminin "kilit başkasında" yanıtları; bunların dışındaki hata gerçek bir dosya hatasıdır
_BUSY_ERRNOS = frozenset(
    code for code in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES, getattr(errno, "EDEADLK", None),
                      getattr(errno, "EDEADLOCK", None))
    if code is not None
)

# Dosya sistemi kilitlemeyi hiç desteklemiyor (kilit sunucusu olmayan NFS, bazı ağ paylaşımları)
_UNSUPPORTED_ERRNOS = frozenset(
    code for code in (errno.ENOLCK, errno.ENOSYS, getattr(errno, "ENOTSUP", None), getattr(errno, "EOPNOTSUPP", None))
    if code is not None
)

_unsupported_warned: Set[str] = set()
_unsupported_warned_lock = threading.Lock()

# Kilide bağlı hazırlık girdilerinin etiketleri (karar S16) ve kilitsiz girdilerin bekletilme süresi
STAGING_HOLDERS: Tuple[str, ...] = (WRITER, LIVE)
STAGING_KEEP_SECONDS = 86400.0

# Bu sürecin tuttuğu kilitler, kilit dizinine göre: aynı dizin için birden çok LeaseManager olabilir (Store'un
# ve iş deposunun kendi yöneticileri). Zayıf başvuru: bırakılmadan çöpe giden kilit kendiliğinden düşer.
_held_here: Dict[str, "weakref.WeakSet[Lease]"] = {}
_held_here_lock = threading.Lock()

Region = Tuple[int, int]  # kilitlenen bayt aralığı (başlangıç, uzunluk); POSIX'te kullanılmaz


# --- işletim sistemi kilidi ----------------------------------------------------------------------

if os.name == "nt":  # pragma: no cover - yalnızca Windows
    import msvcrt

    _OPEN_FLAGS = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)

    def _os_lock(fd: int, mode: str) -> Region:
        """Kilidi beklemeden ister; alınamazsa OSError. Kilit dosya sonunun ötesine de konabilir."""
        if mode == EXCLUSIVE:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, _RANGE)
            return (0, _RANGE)
        for offset in range(_RANGE):
            os.lseek(fd, offset, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                continue  # bu bayt başka bir paylaşımlı sahipte (ya da hepsi dışlayıcı sahipte)
            return (offset, 1)
        raise BlockingIOError(errno.EACCES, "kilit dosyasında boş bayt yok")

    def _os_unlock(fd: int, region: Region) -> None:
        os.lseek(fd, region[0], os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, region[1])
else:
    import fcntl

    _OPEN_FLAGS = os.O_RDWR | os.O_CREAT

    def _os_lock(fd: int, mode: str) -> Region:
        """Kilidi beklemeden ister; alınamazsa BlockingIOError."""
        fcntl.flock(fd, (fcntl.LOCK_EX if mode == EXCLUSIVE else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        return (0, 0)

    def _os_unlock(fd: int, region: Region) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _is_busy(exc: OSError) -> bool:
    return isinstance(exc, (BlockingIOError, PermissionError)) or exc.errno in _BUSY_ERRNOS


def _unlock_all(handles: List[Tuple[int, Region]]) -> None:
    """Kilitleri alınışın tersi sırayla açar ve dosyaları kapatır; liste boşalır."""
    for fd, region in reversed(handles):
        with contextlib.suppress(OSError):
            _os_unlock(fd, region)
        with contextlib.suppress(OSError):
            os.close(fd)
    handles.clear()


# --- kilit tablosu -------------------------------------------------------------------------------

def lock_plan(name: str) -> Tuple[Tuple[str, str], ...]:
    """
    Bir kilidin tuttuğu dosyalar, alınış sırasıyla: ((dosyanın kilit adı, kip), ...). Önce paylaşımlı
    bekçiler, en son kilidin kendi dosyası alınır; böylece yarıda kalan bir deneme yalnızca paylaşımlı
    kilit tutmuş olur ve başka bir isteği yanlışlıkla reddettirmez. Tabloda olmayan ad LayoutError.
    """
    layout.lock_path(name)  # ad biçimi
    base, _, qualifier = name.partition(":")
    if base == WATCHER and qualifier:
        return ((MAINTENANCE, SHARED), (LIVE, SHARED), (name, EXCLUSIVE))
    if not qualifier:
        if base == WRITER:
            return ((MAINTENANCE, SHARED), (WRITER, EXCLUSIVE))
        if base == LIVE:
            return ((MAINTENANCE, SHARED), (LIVE, EXCLUSIVE))
        if base == EXPORT:
            return ((MAINTENANCE, SHARED), (EXPORT, EXCLUSIVE))
        if base in (SINKS, MAINTENANCE):
            return ((base, EXCLUSIVE),)
    raise LayoutError(f"Bilinmeyen kilit adı: {name!r}")


def _name_from_file(filename: str) -> Optional[str]:
    """`layout.lock_path`in tersi: "watcher-tennis.lock" → "watcher:tennis". Kilit dosyası değilse None."""
    if not filename.endswith(_LOCK_SUFFIX):
        return None
    base, dash, qualifier = filename[: -len(_LOCK_SUFFIX)].partition("-")
    name = f"{base}:{qualifier}" if dash else base
    try:
        lock_plan(name)
    except LayoutError:
        return None
    return name


@dataclass(frozen=True)
class LeaseInfo:
    """Tutulan bir kilidin sahibi. `leases` satırı yoksa yalnızca `name` doludur."""

    name: str
    holder: Optional[str] = None  # sahibin kimliği (kilit alınırken üretilir)
    pid: Optional[int] = None
    host: Optional[str] = None
    purpose: str = ""
    acquired_at: Optional[float] = None  # epoch saniye (UTC)


class Lease:
    """
    Alınmış bir kilit. `release()` ile ya da `with` bloğunun sonunda bırakılır; bırakılmadan çöpe giden
    nesne dosyalarını kapatır (işletim sistemi kilidi düşer) ama temiz bırakılmış sayılmaz.
    """

    def __init__(self, name: str, purpose: str, handles: List[Tuple[int, Region]], *,
                 on_release: Optional[Callable[["Lease"], None]] = None) -> None:
        self.name = name
        self.purpose = purpose
        self.holder = uuid.uuid4().hex
        self.pid = os.getpid()
        self.host = socket.gethostname()
        self.acquired_at = float(int(time.time()))  # `leases` tablosu saniye tutar
        self.unclean = False  # yalnızca writer: önceki yazar temiz kapanmamış
        self._on_release = on_release  # temiz bırakılışta, işletim sistemi kilidi hâlâ tutulurken çağrılır
        self._handles = handles
        self._released = False
        self._lock = threading.Lock()

    @property
    def held(self) -> bool:
        return not self._released

    def info(self) -> LeaseInfo:
        return LeaseInfo(self.name, self.holder, self.pid, self.host, self.purpose, self.acquired_at)

    def release(self) -> None:
        """Kilidi bırakır; yinelenen çağrı zararsızdır. Hata fırlatmaz."""
        with self._lock:
            if self._released:
                return
            self._released = True
            # fork ile çoğalmış bir kopya üst sürecin kaydını silmemeli
            if self._on_release is not None and os.getpid() == self.pid:
                self._on_release(self)
            self._close()

    def _close(self) -> None:
        handles, self._handles = self._handles, []
        if os.getpid() == self.pid:
            _unlock_all(handles)
            return
        # fork ile çoğalmış kopya: flock açık dosya tanımına bağlıdır, burada açmak üst sürecin kilidini düşürürdü
        for fd, _region in handles:
            with contextlib.suppress(OSError):
                os.close(fd)

    def __enter__(self) -> "Lease":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.release()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self._close()

    def __repr__(self) -> str:
        return f"<Lease {self.name!r} purpose={self.purpose!r} held={self.held}>"


class LeaseManager:
    """
    Bir kilit dizininin (`DATA_DIR/.meta/locks`) kilitleri. `state` verilirse sahip bilgisi onun `leases`
    tablosuna yazılır ve LeaseHeld o tablodan doldurulur; verilmezse kilitler yine çalışır, bilgi olmaz.
    """

    def __init__(self, locks_dir: PathLike, state: Optional[StateDb] = None) -> None:
        self.locks_dir = os.path.abspath(os.fspath(locks_dir))
        self.state = state
        self._held_key = os.path.normcase(os.path.realpath(self.locks_dir))

    @classmethod
    def for_data_dir(cls, data_dir: PathLike, state: Optional[StateDb] = None) -> "LeaseManager":
        return cls(layout.resolve(data_dir, layout.LOCKS_DIR), state)

    @property
    def data_dir(self) -> Optional[str]:
        """Kilit dizini bir veri dizininin `.meta/locks` dizini ise o veri dizini, değilse None."""
        parts = layout.LOCKS_DIR.split("/")
        root = self.locks_dir
        for name in reversed(parts):
            root, tail = os.path.split(root)
            if tail != name:
                return None
        return root

    def lock_file(self, name: str) -> str:
        return os.path.join(self.locks_dir, os.path.basename(layout.lock_path(name)))

    @property
    def unclean_marker(self) -> str:
        return os.path.join(self.locks_dir, UNCLEAN_NAME)

    # --- alma ve bırakma --------------------------------------------------------------------

    def acquire(self, name: str, *, purpose: str = "", wait: float = 0.0) -> Lease:
        """
        Kilidi alır. Başkasındaysa `wait` saniye boyunca yeniden dener, sonra sahibin bilgisiyle LeaseHeld.
        Tabloda olmayan ad LayoutError; kilit dosyası açılamıyorsa StoreError.
        """
        plan = lock_plan(name)
        deadline = time.monotonic() + max(0.0, float(wait))
        attempt = 0
        while True:
            attempt += 1
            handles, blocked = self._try(plan)
            if blocked is None:
                break
            if attempt >= _MIN_ATTEMPTS and time.monotonic() >= deadline:
                raise self._held(name, *blocked)
            time.sleep(_RETRY_PAUSE if attempt < _MIN_ATTEMPTS else _POLL)
        lease = Lease(name, purpose, handles, on_release=self._forget)
        with _held_here_lock:
            _held_here.setdefault(self._held_key, weakref.WeakSet()).add(lease)
        if name == WRITER:
            lease.unclean = os.path.exists(self.unclean_marker)
            self._mark_unclean(lease)
        self._record(lease)
        self._purge_staging(name)
        return lease

    def held_here(self, name: str) -> bool:
        """Bu süreç `name` kilidini şu an tutuyor mu (bu dizinin herhangi bir yöneticisiyle alınmış olabilir)."""
        with _held_here_lock:
            leases = list(_held_here.get(self._held_key, ()))
        pid = os.getpid()
        return any(lease.name == name and lease.held and lease.pid == pid for lease in leases)

    def _purge_staging(self, name: str) -> None:
        """
        Karar S16: alınan kilidin sahibinden kalan hazırlık girdileri silinir; başkasınınkine dokunulmaz.
        `writer` ayrıca çöpü ve hiçbir kilide bağlı olmayan, bir günden eski girdileri siler (bölüm 9.3).
        """
        data_dir = self.data_dir
        if data_dir is None or name not in STAGING_HOLDERS:
            return
        try:
            files.purge_staging(data_dir, name)
            if name == WRITER:
                files.purge_staging(data_dir, older_than=STAGING_KEEP_SECONDS, skip=STAGING_HOLDERS)
                files.purge_trash(data_dir)
        except StoreError as e:
            logger.warning("Leftover staging entries could not be removed (%s): %s", name, e)

    def migration_guard(self) -> Lease:
        """state.db geçişleri `maintenance` kilidi altında çalışır (bölüm 7.3); StateDb'ye verilir."""
        return self.acquire(MAINTENANCE, purpose=MIGRATION_PURPOSE, wait=MIGRATION_WAIT)

    def _open(self, lock_name: str) -> int:
        path = self.lock_file(lock_name)
        try:
            os.makedirs(self.locks_dir, exist_ok=True)
            return os.open(path, _OPEN_FLAGS, files.STORE_FILE_MODE)
        except OSError as e:
            raise StoreError.from_exception(e, path) from e

    def _try(self, plan: Tuple[Tuple[str, str], ...]) -> Tuple[List[Tuple[int, Region]], Optional[Tuple[str, str]]]:
        """Plandaki bütün dosyaları kilitler. Biri alınamazsa alınanları bırakır ve onu (ad, kip) döndürür."""
        handles: List[Tuple[int, Region]] = []
        try:
            for lock_name, mode in plan:
                fd = self._open(lock_name)
                try:
                    region = _os_lock(fd, mode)
                except OSError as e:
                    os.close(fd)
                    if _is_busy(e):
                        _unlock_all(handles)
                        return [], (lock_name, mode)
                    if e.errno in _UNSUPPORTED_ERRNOS:
                        self._warn_unsupported(e)
                        continue  # tek süreç kipi: bu dosya kilitlenemiyor, kilit yine verilir
                    raise StoreError.from_exception(e, self.lock_file(lock_name)) from e
                handles.append((fd, region))
        except BaseException:
            _unlock_all(handles)
            raise
        return handles, None

    def _warn_unsupported(self, exc: OSError) -> None:
        with _unsupported_warned_lock:
            first = self.locks_dir not in _unsupported_warned
            _unsupported_warned.add(self.locks_dir)
        if first:
            logger.warning(
                "The file system does not support file locks (%s); leases cannot keep other processes out. "
                "Only one process at a time may use this data directory: %s", exc.strerror or exc, self.locks_dir,
            )

    def _mark_unclean(self, lease: Lease) -> None:
        try:
            with open(self.unclean_marker, "w", encoding="utf-8") as f:
                f.write(f"{lease.pid} {lease.host} {int(lease.acquired_at)}\n")
        except OSError as e:
            logger.warning("Could not write the unclean-shutdown marker: %s: %s", self.unclean_marker, e)

    def _record(self, lease: Lease) -> None:
        if self.state is None:
            return
        at = int(lease.acquired_at)
        try:
            with self.state.write() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO leases (name, holder, pid, host, purpose, acquired_at, heartbeat_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (lease.name, lease.holder, lease.pid, lease.host, lease.purpose, at, at),
                )
        except (sqlite3.Error, StoreError) as e:
            logger.warning("Could not record the holder of the lease (%s): %s", lease.name, e)

    def _forget(self, lease: Lease) -> None:
        """Temiz bırakılış: işaret ve sahip satırı, işletim sistemi kilidi hâlâ tutulurken silinir."""
        if lease.name == WRITER:
            with contextlib.suppress(OSError):
                os.remove(self.unclean_marker)
        if self.state is None:
            return
        try:
            with self.state.write() as conn:
                conn.execute("DELETE FROM leases WHERE name = ? AND holder = ?", (lease.name, lease.holder))
        except (sqlite3.Error, StoreError) as e:
            logger.debug("Could not remove the holder record of the lease (%s): %s", lease.name, e)

    # --- kim tutuyor ------------------------------------------------------------------------

    def _row(self, name: str) -> Optional[LeaseInfo]:
        if self.state is None:
            return None
        try:
            row = self.state.connection().execute(
                "SELECT name, holder, pid, host, purpose, acquired_at FROM leases WHERE name = ?", (name,)
            ).fetchone()
        except (sqlite3.Error, StoreError):
            return None
        if row is None:
            return None
        return LeaseInfo(str(row["name"]), str(row["holder"]), int(row["pid"]), str(row["host"]),
                         str(row["purpose"] or ""), float(row["acquired_at"]))

    def _exclusively_held(self, name: str) -> bool:
        """
        Kilidin kendi dosyası şu an dışlayıcı tutuluyor mu? Paylaşımlı bir kilit denenerek yoklanır ve
        hemen bırakılır; yoklamalar birbirini engellemez.
        """
        path = self.lock_file(name)
        if not os.path.exists(path):
            return False
        try:
            fd = os.open(path, _OPEN_FLAGS, files.STORE_FILE_MODE)
        except OSError:
            return False
        try:
            region = _os_lock(fd, SHARED)
        except OSError as e:
            return _is_busy(e)
        else:
            with contextlib.suppress(OSError):
                _os_unlock(fd, region)
            return False
        finally:
            os.close(fd)

    def _names_on_disk(self) -> List[str]:
        try:
            entries = sorted(os.listdir(self.locks_dir))
        except OSError:
            return []
        return [name for name in map(_name_from_file, entries) if name is not None]

    def holders(self) -> List[LeaseInfo]:
        """Şu an tutulan kilitler (bu süreçtekiler dahil). Bayat `leases` satırları listeye girmez."""
        return [self._row(name) or LeaseInfo(name) for name in self._names_on_disk() if self._exclusively_held(name)]

    def holder(self, name: str) -> Optional[LeaseInfo]:
        """Kilit şu an tutuluyorsa sahibi, boştaysa None."""
        lock_plan(name)
        return (self._row(name) or LeaseInfo(name)) if self._exclusively_held(name) else None

    def _blocker(self, lock_name: str, mode: str) -> LeaseInfo:
        """`lock_name` dosyası `mode` kipinde alınamadı: onu tutan kilidi bulur."""
        if mode == EXCLUSIVE:
            # Dosyayı paylaşımlı tutanlar da engeller (maintenance.lock: writer, watcher, live)
            for other in self._names_on_disk():
                if other != lock_name and (lock_name, SHARED) in lock_plan(other) and self._exclusively_held(other):
                    return self._row(other) or LeaseInfo(other)
        return self._row(lock_name) or LeaseInfo(lock_name)

    def _held(self, requested: str, lock_name: str, mode: str) -> LeaseHeld:
        info = self._blocker(lock_name, mode)
        parts = []
        if info.pid is not None:
            parts.append(f"pid {info.pid}")
        if info.host:
            parts.append(f"makine {info.host}")
        if info.purpose:
            parts.append(f"amaç {info.purpose}")
        if info.acquired_at is not None:
            parts.append("başlangıç " + time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(info.acquired_at)))
        who = f" ({', '.join(parts)})" if parts else ""
        return LeaseHeld(
            f"'{requested}' kilidi alınamadı: veri dizini '{info.name}' kilidiyle başka bir sahipte{who}",
            path=self.lock_file(lock_name), name=info.name, pid=info.pid, host=info.host,
            purpose=info.purpose, started_at=info.acquired_at,
        )


__all__ = [
    "EXCLUSIVE",
    "EXPORT",
    "LIVE",
    "MAINTENANCE",
    "SHARED",
    "SINKS",
    "WATCHER",
    "WRITER",
    "Lease",
    "LeaseInfo",
    "LeaseManager",
    "lock_plan",
]
