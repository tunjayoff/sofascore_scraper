"""
Yedek servisi (docs/design/02-services.md 2.7; plan maddeleri ST-19 ve ST-24).

Yedeği Store yazar, doğrular ve geri yükler (`Store.backup`, docs/design/01-storage.md bölüm 9); servis
yüzlerden (CLI, web) bağımsız giriş noktasıdır: hangi ayar dosyalarının pakete gireceğini çağıran söyler,
`.env` yalnızca `include_secrets=True` ile girer (proxy parolası, captcha ve erişim belirteci taşıyabilir).

Yedekler biçim 2'dedir (`backup.json`, `state.db`, v3 ağacı, değişiklik günlüğü); geri yükleme bugünkü
(biçim 1) zip'leri de okur. Geri yükleme yalnızca `backups/` dizinindeki bir yedeği adıyla alır (web arayüzü
kararı 15).

Servis yazdırmaz. `create` kilit almaz: veri içeren bir yedek için veri dizininin `writer` kilidini (amaç
`op:backup`) çağıran tutar (web'in iş deposu, `ssc backup create`). `restore` `maintenance` kilidini kendisi
alır. Store'un hataları kodlu hatalara çevrilir: bilinmeyen ad `not_found`, okunamayan ya da güvensiz yedek
`invalid_request`, boş olmayan hedef `confirmation_required`; dosya sistemi hatası StoreError olarak çıkar.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional, Sequence

from src.errors import NotFoundError, UsageError
from src.logger import get_logger
from src.paths import env_file_path
from src.store import (
    BackupCheck,
    BackupInfo,
    BackupInvalid,
    BackupNotFound,
    RestoreRefused,
    RestoreReport,
    Store,
)

logger = get_logger("BackupService")

BackupScope = Literal["all", "state", "data", "config", "seasons", "matches", "match_details"]


class BackupService:
    """Bir veri dizininin yedekleri."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def create(self, scope: BackupScope = "all", *, config_files: Sequence[str] = (),
               include_secrets: bool = False) -> BackupInfo:
        """
        Yedeği `backups/` altına yazar ve bilgisini döndürür.

        config_files     kapsam `all`, `state` ya da `config` ise pakete girecek ayar dosyaları (lig dosyası,
                         spor eşlemesi, yapılandırma dosyası); olmayan atlanır
        include_secrets  `.env` de pakete girer (aynı kapsamlarda ve dosya varsa); dosyanın adı bunu söyler
                         (`_with_env`) ve yalnızca sahibince okunur
        """
        info = self._store.backup.create(
            scope, config_files=config_files, env_file=env_file_path() if include_secrets else None)
        logger.info("Backup created: %s (scope=%s, with_env=%s, %d bytes)", info.name, info.scope, info.with_env,
                    info.size)
        return info

    def list(self) -> List[BackupInfo]:
        """Var olan yedekler, en yeni önce."""
        return self._store.backup.list()

    def verify(self, name: str) -> BackupCheck:
        """`backups/` altındaki yedeği okur ve denetler; bilinmeyen ad NotFoundError."""
        try:
            check = self._store.backup.verify(name)
        except BackupNotFound as e:
            raise NotFoundError(str(e), {"name": name}) from e
        if check.ok:
            logger.info("Backup checked: %s (format %s, %d members)", name, check.format, check.members)
        else:
            logger.warning("Backup check found %d problem(s) in %s", len(check.problems), name)
        return check

    def restore(self, name: str, *, force: bool = False, dry_run: bool = False) -> RestoreReport:
        """
        `backups/` altındaki yedeği veri dizinine geri yükler (`BackupManager.restore`). force=True: hedefteki
        veri önce çöpe taşınır. dry_run=True: hiçbir şey yazılmaz, rapor olacak olanı söyler.
        """
        try:
            return self._store.backup.restore(name, force=force, dry_run=dry_run)
        except BackupNotFound as e:
            raise NotFoundError(str(e), {"name": name}) from e
        except BackupInvalid as e:
            raise UsageError(str(e), {"name": name}) from e
        except RestoreRefused as e:
            raise UsageError(str(e), {"name": name, "occupied": list(e.reasons)},
                             code="confirmation_required") from e

    def prune(self, keep: Optional[int] = None, max_age_days: Optional[float] = None, *,
              now: Optional[datetime] = None) -> List[BackupInfo]:
        """Eski yedekleri siler (`BackupManager.prune`); varsayılan: hiçbiri silinmez."""
        return self._store.backup.prune(keep, max_age_days, now=now)
