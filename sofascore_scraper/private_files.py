"""
Gizli değer taşıyan dosyaların izinleri.

.env proxy parolasını, captcha ve erişim belirtecini; tarayıcı profili SofaScore cookie'lerini
taşır. İkisi de yalnızca sahibince okunmalı: dosyalar 0600, dizinler 0700 ile oluşturulur ve var
olanlar başlangıçta daraltılır. Windows'ta POSIX izin bitleri yoktur: işlemler orada hiçbir şey
yapmaz (dosyalar kullanıcının profil dizininin ACL'lerini devralır).
"""
import logging
import os
from typing import List

from sofascore_scraper.paths import browser_profile_dir, env_file_path

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
            logging.getLogger(__name__).info(f"Permissions narrowed ({mode:o}, owner only): {path}")
    return changed
