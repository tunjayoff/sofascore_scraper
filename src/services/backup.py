"""
Yedek servisi (docs/design/02-services.md 2.7; plan maddesi ST-19).

Yedeği Store yazar (`Store.backup`, docs/design/01-storage.md 9.1); servis yüzlerden bağımsız giriş noktasıdır:
hangi ayar dosyalarının pakete gireceğini çağıran söyler, `.env` yalnızca `include_secrets=True` ile girer
(proxy parolası, captcha ve erişim belirteci taşıyabilir). Bu adımda yedek bugünkü zip düzenindedir; biçim 2,
doğrulama, geri yükleme ve eskileri silme ST-24 ile gelir.

Servis yazdırmaz ve kilit almaz: veri içeren bir yedek için veri dizininin `writer` kilidini (amaç
`op:backup`) çağıran tutar (bugün web'in iş deposu). Dosya sistemi hatası StoreError olarak çağırana çıkar.
"""
from __future__ import annotations

from typing import List, Literal, Sequence

from src.logger import get_logger
from src.paths import env_file_path
from src.store import BackupInfo, Store

logger = get_logger("BackupService")

BackupScope = Literal["all", "config", "seasons", "matches", "match_details"]


class BackupService:
    """Bir veri dizininin yedekleri."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def create(self, scope: BackupScope = "all", *, config_files: Sequence[str] = (),
               include_secrets: bool = False) -> BackupInfo:
        """
        Yedeği `backups/` altına yazar ve bilgisini döndürür.

        config_files     kapsam `all` ya da `config` ise pakete girecek ayar dosyaları (lig dosyası, spor
                         eşlemesi); olmayan atlanır
        include_secrets  `.env` de pakete girer (kapsam `all` ya da `config` ise ve dosya varsa); dosyanın adı
                         bunu söyler (`_with_env`) ve yalnızca sahibince okunur
        """
        info = self._store.backup.create(
            scope, config_files=config_files, env_file=env_file_path() if include_secrets else None)
        logger.info("Backup created: %s (scope=%s, with_env=%s, %d bytes)", info.name, info.scope, info.with_env,
                    info.size)
        return info

    def list(self) -> List[BackupInfo]:
        """Var olan yedekler, en yeni önce."""
        return self._store.backup.list()
