"""
Gizli değer taşıyan dosyaların izinleri.

.env proxy parolasını, captcha ve erişim belirtecini; tarayıcı profili SofaScore cookie'lerini
taşır. İkisi de yalnızca sahibince okunmalı: dosyalar 0600, dizinler 0700 ile oluşturulur ve var
olanlar başlangıçta daraltılır. Windows'ta POSIX izin bitleri yoktur: işlemler orada hiçbir şey
yapmaz (dosyalar kullanıcının profil dizininin ACL'lerini devralır).
"""
import contextlib
import logging
import os
from typing import List

from src.paths import browser_profile_dir, env_file_path

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
