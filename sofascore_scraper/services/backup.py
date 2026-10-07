"""
Yedek servisi (docs/design/02-services.md 2.7; plan maddeleri ST-19 ve ST-24).

Yedeği Store yazar, doğrular ve geri yükler (`Store.backup`, docs/design/01-storage.md bölüm 9); servis
yüzlerden (CLI, web) bağımsız giriş noktasıdır: hangi ayar dosyalarının pakete gireceğini çağıran söyler,
`.env` yalnızca `include_secrets=True` ile girer (proxy parolası, captcha ve erişim belirteci taşıyabilir).
Web arayüzünde kaydedilen ayarlar (`CONFIG_DIR/overrides.json`) ayar taşıyan her kapsamda pakete girer ve
geri yüklemede yerine konur (FX-22): yoksa geri yüklemeden sonra kaybolurlardı. Proxy adresi parola
taşıyabildiği için bu dosyayı taşıyan yedek de geri yüklenen dosya da 0600'dür.

Yedekler biçim 2'dedir (`backup.json`, `state.db`, v3 ağacı, değişiklik günlüğü); geri yükleme bugünkü
(biçim 1) zip'leri de okur. Geri yükleme yalnızca `backups/` dizinindeki bir yedeği adıyla alır (web arayüzü
kararı 15).

Servis yazdırmaz. `create` kilit almaz: veri içeren bir yedek için veri dizininin `writer` kilidini (amaç
`op:backup`) çağıran tutar (web'in iş deposu, `ssc backup create`). `restore` `maintenance` kilidini kendisi
alır. Store'un hataları kodlu hatalara çevrilir: bilinmeyen ad `not_found`, okunamayan ya da güvensiz yedek
`invalid_request`, boş olmayan hedef `confirmation_required`; dosya sistemi hatası StoreError olarak çıkar.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional, Sequence

from sofascore_scraper.errors import NotFoundError, UsageError
from sofascore_scraper.logger import get_logger
from sofascore_scraper.paths import env_file_path
from sofascore_scraper.store import (
    BackupCheck,
    BackupInfo,
    BackupInvalid,
    BackupNotFound,
    RestoreRefused,
    RestoreReport,
    Store,
)

logger = get_logger("BackupService")

# Yedekteki ayar belgesinin üye adı (sofascore_scraper/store/backup.py OVERRIDES_MEMBER; rapor bu adla söyler)
OVERRIDES_MEMBER = "config/overrides.json"

BackupScope = Literal["all", "state", "data", "config", "seasons", "matches", "match_details"]


class BackupService:
    """Bir veri dizininin yedekleri."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def create(self, scope: BackupScope = "all", *, config_files: Sequence[str] = (),
               include_secrets: bool = False, job_id: Optional[str] = None) -> BackupInfo:
        """
        Yedeği `backups/` altına yazar ve bilgisini döndürür.

        config_files     kapsam `all`, `state` ya da `config` ise pakete girecek ayar dosyaları (lig dosyası,
                         spor eşlemesi, yapılandırma dosyası); olmayan atlanır
        include_secrets  `.env` de pakete girer (aynı kapsamlarda ve dosya varsa); dosyanın adı bunu söyler
                         (`_with_env`) ve yalnızca sahibince okunur
        job_id           yedeği alan iş; yedekteki iş geçmişinde bitmiş görünür (geri yüklemeden sonra
                         "yarıda kaldı" uyarısı vermesin, FX-23)

        `overrides.json` (web arayüzünde kaydedilen ayarlar) aynı kapsamlarda, dosya varsa her zaman girer.
        """
        info = self._store.backup.create(
            scope, config_files=config_files, env_file=env_file_path() if include_secrets else None,
            overrides_file=str(_overrides_path()), job_id=job_id)
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

        Yedekte `config/overrides.json` varsa (FX-22'den sonra alınan yedekler) bu kurulumun overrides.json'ı
        onunla değiştirilir ve ayarlar yeniden yüklenir; ayarlar arayüzünün yazdığı dosyayla aynı kilit altında.
        Geri yüklenen belgeyle ayarlar kurulamıyorsa (ör. başka bir sürümün bilmediği anahtar) önceki dosya geri
        konur, önceki ayarlar yürürlükte kalır ve rapor dosyayı `skipped` olarak adlandırır.
        """
        if dry_run:
            return self._restore(name, force=force, dry_run=True)
        from sofascore_scraper.config.overrides import current_bytes
        from sofascore_scraper.config_files import file_lock

        with file_lock(str(_overrides_path())):
            before = current_bytes()
            report = self._restore(name, force=force, dry_run=False)
            if OVERRIDES_MEMBER in report.restored:
                report = _reload_settings(report, before)
        return report

    def _restore(self, name: str, *, force: bool, dry_run: bool) -> RestoreReport:
        try:
            return self._store.backup.restore(name, force=force, dry_run=dry_run,
                                              overrides_file=str(_overrides_path()))
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


def _overrides_path() -> Path:
    """Ayar yükleyicisinin okuduğu overrides.json (CONFIG_DIR / overrides.json)."""
    from sofascore_scraper.config.overrides import overrides_path

    return overrides_path()


def _reload_settings(report: RestoreReport, before: Optional[bytes]) -> RestoreReport:
    """
    Geri yüklenen overrides.json'la ayarları yeniden yükler. Kurulamazsa önceki dosya geri konur ve rapor
    dosyayı `restored`dan `skipped`a taşır. Hata iletisi değer taşıyabileceği için günlüğe yalnızca türü yazılır.
    """
    from sofascore_scraper.config.overrides import reload_or_put_back

    error = reload_or_put_back(before)
    if error is None:
        logger.info("Settings reloaded after the restore: %s brought back the settings saved in the web app",
                    report.name)
        return report
    logger.warning("The settings file in backup %s cannot be used (%s); the previous settings file was kept",
                   report.name, error)
    return dataclasses.replace(
        report, restored=tuple(x for x in report.restored if x != OVERRIDES_MEMBER),
        replaced=tuple(x for x in report.replaced if x != OVERRIDES_MEMBER),
        skipped=(*report.skipped, OVERRIDES_MEMBER))
