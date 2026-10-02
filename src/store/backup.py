"""
Veri yedeği (docs/design/01-storage.md bölüm 9.1; plan maddesi ST-19).

Bu adımda yedek, web API'sinin bugüne kadar ürettiği zip'in aynısıdır (biçim 1; biçim 2, geri yükleme ve
eskileri silme ST-24 ile gelir):

  * dosya `backups/backup_<kapsam>[_with_env]_<yyyymmdd>_<hhmmss>.zip` (yerel saat); `.env` pakete giriyorsa
    adı bunu söyler ve dosya yalnızca sahibince okunur (0600);
  * kapsam `all` ya da `config` ise verilen ayar dosyaları zip'in köküne kendi adlarıyla, istenirse `.env`
    dosyası `.env` adıyla; olmayan dosya atlanır;
  * kapsam `all`, `seasons`, `matches` ya da `match_details` ise o eski düzen ağaçları, sırasıyla `seasons/`,
    `matches/`, `match_details/`, üye adı veri dizininin üstüne göre (`<veri dizininin adı>/seasons/...`);
  * `.meta`, `score_changes.jsonl`, `backups/` ve dışa aktarımlar pakete girmez.

Kilit almaz: veri içeren bir yedek sürerken veri dizinini yazan olmamalıdır; `writer` kilidini (amaç
`op:backup`) çağıran tutar (bugün web'in iş deposu, `JobStore.exclusive("backup")`). Yarım kalan zip silinir;
dosya sistemi hatası StoreError olarak çıkar.
"""
from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from src.store import layout
from src.store.errors import StoreError

if TYPE_CHECKING:
    from src.store.api import Store

# Bugünkü kapsamlar (web API'si): `config` yalnızca ayar dosyalarını, ötekiler veri ağaçlarını da alır
BACKUP_SCOPES: Tuple[str, ...] = ("all", "config", "seasons", "matches", "match_details")
CONFIG_SCOPES = ("all", "config")
_TREES: Tuple[str, ...] = ("seasons", "matches", "match_details")  # zip'e giriş sırası
ENV_MEMBER = ".env"
WITH_ENV_TAG = "_with_env"
_TIME_FORMAT = "%Y%m%d_%H%M%S"
_NAME_RE = re.compile(r"backup_(all|config|seasons|matches|match_details)(_with_env)?_(\d{8}_\d{6})\.zip")
_PRIVATE_MODE = 0o600


@dataclass(frozen=True)
class BackupInfo:
    """
    Bir yedek dosyası.

    name        dosya adı (`backups/` içinde)
    path        tam yol
    scope       kapsam (BACKUP_SCOPES)
    with_env    pakette `.env` var (gizli değer taşıyabilir)
    created_at  addaki zaman damgası, ISO biçiminde (yerel saat, saat dilimi yok)
    size        bayt
    """

    name: str
    path: str
    scope: str
    with_env: bool
    created_at: str
    size: int


def backup_info(path: str) -> Optional[BackupInfo]:
    """Yolun adı bir yedek adıysa ve dosya varsa bilgisi; değilse None."""
    name = os.path.basename(path)
    match = _NAME_RE.fullmatch(name)
    if match is None:
        return None
    try:
        size = os.stat(path).st_size
    except OSError:
        return None
    created = datetime.strptime(match.group(3), _TIME_FORMAT).isoformat()
    return BackupInfo(name=name, path=path, scope=match.group(1), with_env=bool(match.group(2)),
                      created_at=created, size=size)


class BackupManager:
    """Veri dizininin yedekleri (`Store.backup`)."""

    def __init__(self, store: "Store") -> None:
        self._store = store

    @property
    def directory(self) -> str:
        """Yedeklerin dizini: `<veri dizini>/backups` (web sunucusunun statik olarak servis etmediği bir yer)."""
        return layout.resolve(self._store.data_dir, layout.BACKUPS_DIR)

    def create(self, scope: str = "all", *, config_files: Sequence[str] = (), env_file: Optional[str] = None,
               now: Optional[datetime] = None) -> BackupInfo:
        """
        Yedeği yazar ve bilgisini döndürür (modül belgesindeki düzen).

        config_files  kapsam `all` ya da `config` ise zip'in köküne dosya adlarıyla eklenecek ayar dosyaları
                      (verildiği sırayla; olmayan atlanır)
        env_file      verilirse ve kapsam `all` ya da `config` ise ve dosya varsa `.env` adıyla eklenir; dosya
                      adı `_with_env` taşır ve izni 0600 olur
        now           dosya adındaki zaman (yerel); verilmezse şimdi

        Bilinmeyen kapsam ValueError; dosya sistemi hatası StoreError (yarım zip silinir).
        """
        if scope not in BACKUP_SCOPES:
            raise ValueError(f"unknown backup scope: {scope!r}")
        data_dir = str(self._store.data_dir)
        stamp = (now or datetime.now()).strftime(_TIME_FORMAT)
        with_env = env_file is not None and scope in CONFIG_SCOPES and os.path.exists(env_file)
        name = f"backup_{scope}{WITH_ENV_TAG if with_env else ''}_{stamp}.zip"
        directory = self.directory
        path = os.path.join(directory, name)
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as e:
            raise StoreError.from_exception(e, directory) from e
        try:
            if with_env:
                # İçinde .env var: dosya baştan yalnızca sahibince okunur yaratılır (zip sonra içini yazar)
                os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _PRIVATE_MODE))
                if os.name == "posix":
                    os.chmod(path, _PRIVATE_MODE)
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                if scope in CONFIG_SCOPES:
                    for config_file in config_files:
                        if os.path.exists(config_file):
                            zf.write(config_file, os.path.basename(config_file))
                    if with_env:
                        assert env_file is not None
                        zf.write(env_file, ENV_MEMBER)
                parent = os.path.dirname(data_dir)
                for tree in _TREES:
                    if scope not in ("all", tree):
                        continue
                    root_dir = os.path.join(data_dir, tree)
                    if not os.path.exists(root_dir):
                        continue
                    for root, _, names in os.walk(root_dir):
                        for file_name in names:
                            file_path = os.path.join(root, file_name)
                            zf.write(file_path, os.path.relpath(file_path, parent))
        except BaseException as e:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            if isinstance(e, OSError):
                raise StoreError.from_exception(e, path) from e
            raise
        info = backup_info(path)
        if info is None:
            raise StoreError(f"Backup was written but cannot be read back: {path}", path=path)
        return info

    def list(self) -> List[BackupInfo]:
        """`backups/` altındaki yedekler, en yeni önce (addaki zamana, eşitse ada göre). Dizin yoksa boş."""
        directory = self.directory
        try:
            names = os.listdir(directory)
        except FileNotFoundError:
            return []
        except OSError as e:
            raise StoreError.from_exception(e, directory, reading=True) from e
        found = [info for info in (backup_info(os.path.join(directory, n)) for n in names) if info is not None]
        return sorted(found, key=lambda info: (info.created_at, info.name), reverse=True)


__all__ = ["BACKUP_SCOPES", "BackupInfo", "BackupManager", "backup_info"]
