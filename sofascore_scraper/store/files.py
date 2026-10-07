"""
Store'un dosya ilkelleri (docs/design/01-storage.md, bölüm 4.4): atomik yazma, yerine koyma, silme,
hazırlık (.meta/tmp) ve çöp (.meta/trash) dizinleri.

Atomik yazma: içerik önce aynı dizinde `.<ad>.<rastgele>.tmp` adlı geçici dosyaya yazılır, sonra
os.replace ile yerine konur. Okuyan taraf ya eski ya yeni dosyayı görür, yarım dosyayı görmez; aynı
dosyaya aynı anda yazan iki süreç birbirinin geçici dosyasını ezmez.

Her OSError bir StoreError'a çevrilir (`fatal` errno'dan hesaplanır) ve STORE_DURABILITY=full ise dosya ve
dizini fsync edilir. Veri dizini dışındaki yapılandırma dosyaları (leagues.txt, league_sports.json,
overrides.json) bu modülle değil sofascore_scraper/config_files.py ile yazılır.

Dosya izinleri (karar S12): Store katmanının yazdığı dosyalar (yükler, manifestler, .meta/schema.json)
sürecin umask'ine uyar: umask 022 ile 0644, 077 ile 0600. Böylece Docker bağlama noktasını başka bir
kullanıcıyla okuyan ya da başka hesapla çalışan bir yedekleme aracı veriyi okuyabilir (iki SQLite dosyası
zaten böyledir). Dizinler os.makedirs ile açılır, yani onlar da umask'e uyar. Gizli bilgi taşıyan dosyalar
(.env, tarayıcı profili) bu işlevlerle yazılmaz; onlara sofascore_scraper/private_files.py bakar. POSIX izinleri
Windows'ta yoktur: orada bu ayrımın etkisi olmaz.

Windows: hedef başka bir süreçte açıkken os.replace PermissionError verir. Yerine koyma en çok
REPLACE_RETRIES kez, REPLACE_RETRY_PAUSE aralıkla yeniden denenir.
"""
from __future__ import annotations

import contextlib
import errno
import os
import shutil
import time
import uuid
from typing import Callable, Collection, Optional, Tuple, Union

from sofascore_scraper.store import layout
from sofascore_scraper.store.errors import PayloadMissing, StoreError

PathLike = Union[str, "os.PathLike[str]"]

REPLACE_RETRIES = 10
REPLACE_RETRY_PAUSE = 0.02  # saniye
DURABILITY_ENV = "STORE_DURABILITY"
# Store katmanının dosyaları bu izinle açılır; çekirdek sürecin umask'ini kendisi düşer (022 → 0644)
STORE_FILE_MODE = 0o666
TMP_NAME_ATTEMPTS = 100

_WINDOWS = os.name == "nt"


class ReplaceBusy(PermissionError):
    """Yerine koyma, yeniden denemelere rağmen başarısız: hedef başka bir süreçte açık (Windows)."""


def durability_full() -> bool:
    """STORE_DURABILITY=full: her dosya ve dizini fsync edilir. Varsayılan: fsync yok (bugünkü davranış)."""
    return os.environ.get(DURABILITY_ENV, "").strip().lower() == "full"


def _retry_while_busy(operation: Callable[[str, str], None], src: str, dst: str) -> None:
    """
    os.replace / os.rename; Windows'ta PermissionError (hedef açık) alınırsa kısa aralıklarla yeniden dener.
    Denemeler tükenirse ReplaceBusy (PermissionError'ın alt sınıfı) fırlatır. Diğer platformlarda ve diğer
    hatalarda ilk hata aynen çıkar.
    """
    retries = REPLACE_RETRIES if _WINDOWS else 0
    for attempt in range(retries + 1):
        try:
            operation(src, dst)
            return
        except PermissionError as e:
            if not retries:
                raise
            if attempt == retries:
                raise ReplaceBusy(e.errno, e.strerror, e.filename, getattr(e, "winerror", None), e.filename2) from e
            time.sleep(REPLACE_RETRY_PAUSE)


def fsync_dir(directory: PathLike) -> None:
    """Dizin girdisini diske yazdırır (yeniden adlandırma kalıcı olsun). Windows'ta dizin açılamaz: atlanır."""
    if _WINDOWS:
        return
    with contextlib.suppress(OSError):  # bazı dosya sistemleri dizinde fsync'i desteklemez
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


# --- geçici dosya ve atomik yazma ------------------------------------------------------------------

# mkstemp'in bayrakları, okuma dışında: yalnızca yeni dosya (O_EXCL), bağ izlenmez, Windows'ta ikili kip
_TMP_OPEN_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)

def _open_store_tmp(directory: str, name: str, mode: int = STORE_FILE_MODE) -> Tuple[int, str]:
    """
    Store katmanının geçici dosyası: mkstemp ile aynı ad biçimi (`.<ad>.<rastgele>.tmp`), ama
    STORE_FILE_MODE (ya da verilen `mode`) ile açılır ve izni çekirdek umask'e göre belirler (karar S12).

    umask burada okunmaz: os.umask değeri yalnızca değiştirerek döndürür, bu da o anda başka bir iş
    parçacığının açtığı dosyanın iznini bozar. İzin açılışta verilir; sonradan chmod yapılmaz.
    """
    for _ in range(TMP_NAME_ATTEMPTS):
        tmp = os.path.join(directory, f".{name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            return os.open(tmp, _TMP_OPEN_FLAGS, mode), tmp
        except FileExistsError:
            continue  # ad dolu: var olan dosyaya dokunulmadı (O_EXCL), başka adla dene
    raise FileExistsError(errno.EEXIST, "Kullanılmayan geçici dosya adı bulunamadı", directory)


def _atomic_write(path: PathLike, data: bytes, *, durable: bool, mode: int = STORE_FILE_MODE) -> None:
    target = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(target))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = _open_store_tmp(directory, os.path.basename(target), mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            if durable:
                f.flush()
                os.fsync(f.fileno())
        _retry_while_busy(os.replace, tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
    if durable:
        fsync_dir(directory)


# --- Store katmanı: OSError → StoreError ---------------------------------------------------------

def _store_error(exc: OSError, path: Optional[PathLike], *, reading: bool = False) -> StoreError:
    where = os.fspath(path) if path is not None else None
    if isinstance(exc, ReplaceBusy):
        # Geçici durum (dosya başka süreçte açık): errno verilmez ki hata `fatal` sayılıp işi durdurmasın
        detail = exc.strerror or str(exc)
        return StoreError(f"Dosya yerine konamadı, başka bir süreçte açık ({detail}): {where}",
                          path=where, detail=detail)
    return StoreError.from_exception(exc, where, reading=reading)


def read_bytes(path: PathLike) -> bytes:
    """Dosyayı tek çağrıda okur ve kapatır (açık kalan okuyucu Windows'ta yazarı engeller)."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError as e:
        raise PayloadMissing(f"Dosya bulunamadı: {os.fspath(path)}", path=os.fspath(path)) from e
    except OSError as e:
        raise _store_error(e, path, reading=True) from e


def write_bytes(path: PathLike, data: bytes, *, durable: Optional[bool] = None,
                mode: int = STORE_FILE_MODE) -> None:
    """
    Atomik yazma. durable=None → STORE_DURABILITY ortam değişkeni karar verir.

    Dosyanın izni sürecin umask'ine uyar (karar S12), hedef daha önce başka bir izinle var olsa bile:
    yerine konan dosya yeni dosyadır. `mode` umask'ten önceki izindir: 0o600 dosyayı yalnızca sahibinin
    okuyabileceği biçimde yaratır (gizli değer taşıyan ayar dosyası; Windows'ta etkisizdir).
    """
    try:
        _atomic_write(path, data, durable=durability_full() if durable is None else durable, mode=mode)
    except OSError as e:
        raise _store_error(e, path) from e


def replace(src: PathLike, dst: PathLike) -> None:
    """Dosyayı atomik olarak yerine koyar (hedef varsa ezilir)."""
    try:
        _retry_while_busy(os.replace, os.fspath(src), os.fspath(dst))
    except OSError as e:
        raise _store_error(e, dst) from e


def remove(path: PathLike) -> bool:
    """Dosyayı siler. Dosya yoksa False döner (hata değildir)."""
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    except OSError as e:
        raise _store_error(e, path) from e
    return True


def remove_tree(path: PathLike) -> bool:
    """Dizini içindekilerle siler (sembolik bağın kendisini siler, hedefine girmez). Yoksa False döner."""
    try:
        if os.path.islink(path) or not os.path.isdir(path):
            os.remove(path)
        else:
            shutil.rmtree(path)
    except FileNotFoundError:
        return False
    except OSError as e:
        raise _store_error(e, path) from e
    return True


# --- hazırlık ve çöp dizinleri -------------------------------------------------------------------

def _unique_name(label: str) -> str:
    return f"{label}.{uuid.uuid4().hex}" if label else uuid.uuid4().hex


def new_staging_dir(data_dir: PathLike, label: str = "") -> str:
    """
    DATA_DIR/.meta/tmp altında yeni, boş bir dizin açar ve yolunu döndürür. Birden çok dosyadan oluşan
    bir varlık dizini burada kurulur, sonra `publish_dir` ile tek yeniden adlandırmayla yerine taşınır.
    Dizin os.makedirs ile açılır (tempfile.mkdtemp ile değil), yani izni umask'e uyar (karar S12).
    """
    path = os.path.join(layout.resolve(data_dir, layout.TMP_DIR), _unique_name(label))
    try:
        os.makedirs(path)
    except OSError as e:
        raise _store_error(e, path) from e
    return path


def publish_dir(staged: PathLike, final: PathLike, *, durable: Optional[bool] = None) -> None:
    """
    Hazırlık dizinini tek yeniden adlandırmayla yerine taşır: dizin ya tam görünür ya hiç görünmez.
    Hedef zaten varsa (boş bile olsa) StoreError; hiçbir şeyin üzerine yazılmaz.

    İzinler (karar S12): yeniden adlandırma izinlere dokunmaz, yayımlanan ağaç hazırlandığı izinlerle
    görünür. Dizin `new_staging_dir` ile açılıp içi `write_bytes` ile doldurulduysa (alt dizinler dahil)
    her şey umask'e uyar; burada açılan üst dizinler de öyle. Başka yoldan (mkdtemp, mkstemp, kopyalama)
    hazırlanmış bir dizin kendi izinleriyle yayımlanır.
    """
    source, target = os.fspath(staged), os.fspath(final)
    parent = os.path.dirname(os.path.abspath(target))
    try:
        if os.path.lexists(target):
            raise FileExistsError(errno.EEXIST, "Hedef dizin zaten var", target)
        os.makedirs(parent, exist_ok=True)
        _retry_while_busy(os.rename, source, target)
    except OSError as e:
        raise _store_error(e, target) from e
    if durability_full() if durable is None else durable:
        fsync_dir(parent)


def move_to_trash(data_dir: PathLike, path: PathLike) -> str:
    """
    Dosya ya da dizini DATA_DIR/.meta/trash altına taşır ve yeni yolunu döndürür. Önce taşımak, silme
    yarıda kalsa bile eski yerinde yarım bir dizin bırakmaz; çöp sonradan `purge_trash` ile boşaltılır.
    """
    source = os.fspath(path)
    trash = layout.resolve(data_dir, layout.TRASH_DIR)
    target = os.path.join(trash, _unique_name(os.path.basename(os.path.normpath(source))))
    try:
        os.makedirs(trash, exist_ok=True)
        _retry_while_busy(os.rename, source, target)
    except OSError as e:
        raise _store_error(e, source) from e
    return target


def _purge(directory: str, keep: Optional[Callable[[str], bool]] = None) -> int:
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        return 0
    except OSError as e:
        raise _store_error(e, directory, reading=True) from e
    removed, first_error = 0, None
    for name in names:
        try:
            if keep is not None and keep(name):
                continue
            removed += remove_tree(os.path.join(directory, name))
        except StoreError as e:  # kalanları da dene; ilk hatayı sonda bildir
            first_error = first_error or e
    if first_error is not None:
        raise first_error
    return removed


def staging_holder(name: str) -> str:
    """Hazırlık girdisinin sahibi (karar S16): `new_staging_dir`'in verdiği `<etiket>.<rastgele>` adındaki etiket."""
    label, dot, _ = name.rpartition(".")
    return label if dot else ""


def purge_staging(data_dir: PathLike, holder: Optional[str] = None, *, older_than: Optional[float] = None,
                  skip: Collection[str] = ()) -> int:
    """
    .meta/tmp'deki yarıda kalmış hazırlıkları siler; silinen girdi sayısını döndürür.

    Girdiler sahibinin adını taşır (karar S16; `new_staging_dir(data_dir, etiket)` → `<etiket>.<rastgele>`):
    bir kilit alındığında yalnızca o sahibin girdileri silinir, çünkü başka bir sahip (dışa aktarma, canlı
    servis) aynı anda orada hazırlık yapıyor olabilir.

    holder=None: sahibine bakılmaz. holder verilirse yalnızca o etiketi taşıyan girdiler silinir.
    skip: bu etiketleri taşıyan girdilere dokunulmaz.
    older_than: yalnızca son değişikliği (mtime) bu kadar saniyeden eski girdiler silinir.
    Bağımsız değişkensiz çağrı dizinin tamamını boşaltır.
    """
    directory = layout.resolve(data_dir, layout.TMP_DIR)
    if holder is None and older_than is None and not skip:
        return _purge(directory)
    deadline = None if older_than is None else time.time() - older_than

    def keep(name: str) -> bool:
        label = staging_holder(name)
        if (holder is not None and label != holder) or label in skip:
            return True
        if deadline is None:
            return False
        try:
            return os.lstat(os.path.join(directory, name)).st_mtime > deadline
        except FileNotFoundError:
            return True  # o arada başkası sildi
        except OSError as e:
            raise _store_error(e, os.path.join(directory, name), reading=True) from e

    return _purge(directory, keep)


def purge_trash(data_dir: PathLike) -> int:
    """.meta/trash'i boşaltır; silinen girdi sayısını döndürür."""
    return _purge(layout.resolve(data_dir, layout.TRASH_DIR))
