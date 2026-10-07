"""
Köprü tarayıcısının profil kilidi (FX-23, bulgular F34 ve F36).

Chromium bir profil dizinini aynı anda tek bir süreçle açar: POSIX'te dizindeki `SingletonLock` sembolik
bağı `<makine adı>-<pid>`'e işaret eder, Windows'ta `lockfile` açık tutulur. `ssc serve` köprüsünün tarayıcısı
profili tutarken ikinci bir süreç (`ssc sync`, `ssc watch`'ın yedek yoklaması) aynı profille tarayıcı
açamıyordu ("Failed to create a ProcessSingleton ... SingletonLock: File exists"); 403 alan her istek üç
denemeden sonra düşüyordu.

Çözüm: profil başka bir canlı süreçteyse bu süreç kardeş bir geçici profil açar (`<profil>-<pid>`). Geçici
profil boş başlar (çözülmüş challenge'ı yoktur; ilk 403'te bir kez çözülür), köprü kapanınca silinir; çöken
süreçlerin bıraktığı kardeş profiller bir sonraki seçimde süpürülür. Ana profile hiç dokunulmaz: kilidi tutan
tarayıcı onu kullanmaya devam eder.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
from dataclasses import dataclass
from typing import Optional

from sofascore_scraper.logger import get_logger
from sofascore_scraper.private_files import make_private_dir

logger = get_logger("ChallengeSolver")

POSIX_LOCK = "SingletonLock"
WINDOWS_LOCK = "lockfile"
# Chromium'un başlatma hatasında profil kilidini söyleyen parçalar (tarayıcı günlüğünden Playwright hatasına geçer)
_LOCK_ERROR_MARKERS = ("ProcessSingleton", "SingletonLock", "profile appears to be in use",
                       "user data directory is already in use")


@dataclass(frozen=True)
class ProfileOwner:
    """Profili tutan süreç: pid ve makine adı (Windows'ta ve okunamayan kilitte bilinmez)."""

    pid: Optional[int] = None
    host: Optional[str] = None

    def describe(self) -> str:
        if self.pid is None:
            return "another process"
        if self.host and self.host != socket.gethostname():
            return f"another process (pid {self.pid} on {self.host})"
        return f"another process (pid {self.pid})"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # başka kullanıcının süreci: yaşıyor
    except OSError:
        return False
    return True


def profile_owner(profile_dir: str) -> Optional[ProfileOwner]:
    """
    Profili şu an tutan canlı süreç; boşsa ya da kilit bayatsa None. Bayat kilidi (aynı makinede ölmüş süreç)
    Chromium kendisi temizler; başka makinenin kilidini (paylaşılan dizin) Chromium da "kullanımda" sayar.
    """
    if os.name == "nt":
        path = os.path.join(profile_dir, WINDOWS_LOCK)
        if not os.path.exists(path):
            return None
        try:
            os.remove(path)  # tutan tarayıcı yoksa bayat kilit silinir; tutan varsa Windows silmeye izin vermez
        except PermissionError:
            return ProfileOwner()
        except OSError:
            return None
        return None
    try:
        target = os.readlink(os.path.join(profile_dir, POSIX_LOCK))
    except OSError:
        return None
    host, sep, pid_text = target.rpartition("-")
    if not sep or not pid_text.isdigit():
        return None  # tanınmayan kilit: kararı Chromium verir (başlatma hatası yine yakalanır)
    pid = int(pid_text)
    if host == socket.gethostname() and not _pid_alive(pid):
        return None
    return ProfileOwner(pid=pid, host=host)


def is_lock_error(error: BaseException) -> bool:
    """Tarayıcı başlatma hatası profil kilidinden mi (başka süreç profili aynı anda açtı)."""
    text = str(error)
    return any(marker in text for marker in _LOCK_ERROR_MARKERS)


def _sibling_pattern(profile_dir: str) -> "re.Pattern[str]":
    return re.compile(re.escape(os.path.basename(profile_dir.rstrip("/\\"))) + r"-(\d+)")


def sweep_stale(profile_dir: str) -> int:
    """Ölmüş süreçlerin bıraktığı kardeş geçici profilleri siler; silinen sayısını döndürür."""
    base = profile_dir.rstrip("/\\")
    parent = os.path.dirname(base) or "."
    pattern = _sibling_pattern(base)
    removed = 0
    try:
        names = os.listdir(parent)
    except OSError:
        return 0
    for name in names:
        match = pattern.fullmatch(name)
        if match is None or int(match.group(1)) == os.getpid() or _pid_alive(int(match.group(1))):
            continue
        shutil.rmtree(os.path.join(parent, name), ignore_errors=True)
        removed += 1
    return removed


def secondary_profile(profile_dir: str) -> str:
    """
    Bu sürecin geçici profili: `<profil>-<pid>`, yalnızca sahibine açık (0700). Önce bayat kardeşler süpürülür.
    Dizin yaratılamazsa OSError.
    """
    sweep_stale(profile_dir)
    path = f"{profile_dir.rstrip('/' + os.sep)}-{os.getpid()}"
    make_private_dir(path)
    return path


def remove_secondary(path: Optional[str]) -> None:
    """Geçici profili siler (köprü kapanınca); hata yutulur, kalan dizini sonraki süpürme alır."""
    if path:
        shutil.rmtree(path, ignore_errors=True)
