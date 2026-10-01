"""
Atomik dosya yazma: önce aynı dizinde benzersiz bir geçici dosyaya yazılır, sonra
os.replace ile yerine konur. Yarıda kesilen bir yazma hedef dosyayı bozuk bırakmaz ve
aynı dosyaya aynı anda yazan iki süreç birbirinin geçici dosyasını ezmez.
"""

import contextlib
import json
import logging
import os
import tempfile
from typing import Any, Iterator, List

from src.paths import browser_profile_dir, env_file_path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None


def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def atomic_write_json(path: str, data: Any, **dump_kwargs: Any) -> None:
    dump_kwargs.setdefault("ensure_ascii", False)
    dump_kwargs.setdefault("indent", 2)
    atomic_write_text(path, json.dumps(data, **dump_kwargs))


@contextlib.contextmanager
def file_lock(path: str) -> Iterator[None]:
    """
    Süreçler arası danışma kilidi (CLI ve web aynı config dosyasını düzenlerken).
    Kilit, hedefin yanındaki `<path>.lock` dosyasında tutulur; Windows'ta kilitlenmez.
    """
    if fcntl is None:
        yield
        return
    lock_path = f"{path}.lock"
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    with open(lock_path, "a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


# --- Gizli değer taşıyan dosyaların izinleri --------------------------------------------------
# .env proxy parolası, captcha ve erişim belirteci; tarayıcı profili SofaScore cookie'lerini taşır.
# İkisi de yalnızca sahibince okunmalı. Windows'ta POSIX izin bitleri yoktur: işlemler orada
# hiçbir şey yapmaz (dosyalar kullanıcının profil dizininin ACL'lerini devralır).

PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700


def restrict_permissions(path: str, mode: int) -> bool:
    """
    Var olan dosya/dizinin izinlerini `mode`'dan geniş olmayacak şekilde daraltır (grup ve diğerleri
    için açık bitleri kapatır). İzinler değiştiyse True. Başkasına ait ya da olmayan bir yol hata
    değildir: False döner.
    """
    if os.name != "posix":
        return False
    try:
        current = os.stat(path).st_mode & 0o777
        wanted = current & mode
        if wanted == current:
            return False
        os.chmod(path, wanted)
        return True
    except OSError:
        return False


def create_private_file(path: str) -> None:
    """Dosya yoksa boş ve yalnızca sahibince okunur (0600) oluşturur; varsa içeriğine dokunmaz."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with contextlib.suppress(FileExistsError):
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE))
    restrict_permissions(path, PRIVATE_FILE_MODE)


def make_private_dir(path: str) -> None:
    """Dizini (yoksa) oluşturur ve yalnızca sahibine açar (0700)."""
    os.makedirs(path, mode=PRIVATE_DIR_MODE, exist_ok=True)
    restrict_permissions(path, PRIVATE_DIR_MODE)


def harden_secret_paths() -> List[str]:
    """
    Başlangıçta: var olan .env (0600) ve tarayıcı profili dizini (0700) izinlerini daraltır.
    Daraltılan yolları döndürür ve her biri için tek bir log satırı yazar.
    """
    changed = []
    for path, mode in ((env_file_path(), PRIVATE_FILE_MODE), (browser_profile_dir(), PRIVATE_DIR_MODE)):
        if restrict_permissions(path, mode):
            changed.append(path)
            logging.getLogger(__name__).info(f"İzinler daraltıldı ({mode:o}, yalnızca sahibi): {path}")
    return changed
