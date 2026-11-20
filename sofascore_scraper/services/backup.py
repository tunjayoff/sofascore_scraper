"""
Yedek servisi (docs/design/02-services.md 2.7; plan maddeleri ST-19 ve ST-24).

Yedeği Store yazar, doğrular ve geri yükler (`Store.backup`, docs/design/01-storage.md bölüm 9); servis
yüzlerden (CLI, web) bağımsız giriş noktasıdır: hangi ayar dosyalarının pakete gireceğini çağıran söyler,
`.env` yalnızca `include_secrets=True` ile girer (proxy parolası, captcha ve erişim belirteci taşıyabilir).
Web arayüzünde kaydedilen ayarlar (`CONFIG_DIR/overrides.json`) ayar taşıyan her kapsamda pakete girer ve
geri yüklemede yerine konur (FX-22): yoksa geri yüklemeden sonra kaybolurlardı. Proxy adresi parola
taşıyabildiği için bu dosyayı taşıyan yedek de geri yüklenen dosya da 0600'dür. Güvenilen sertifikalar
(`CONFIG_DIR/ca/*.pem`, sofascore_scraper/client/trust.py) da aynı kapsamlarda pakete girer ve geri yüklemede
yerine konur (yedek kazanır, yedekte olmayan sertifika silinmez); ardından bu sürecin köprü tarayıcısı yeni
depoyla yeniden başlatılmak üzere kapatılır (`POST /api/v1/trust` ile aynı yol).

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
# Yedekteki güvenilen sertifikaların üye adı öneki (sofascore_scraper/store/backup.py CA_MEMBER_PREFIX):
# `config/ca.<dosya adı>`
CA_MEMBER_PREFIX = "config/ca."

# 2.x'in dört kapsamı (`config`, `seasons`, `matches`, `match_details`) 3.1'de kalktı (plan maddesi P30)
BackupScope = Literal["all", "state", "data"]


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

        `overrides.json` (web arayüzünde kaydedilen ayarlar) ve güvenilen sertifikalar (`CONFIG_DIR/ca/*.pem`)
        aynı kapsamlarda, varsa her zaman girer.
        """
        info = self._store.backup.create(
            scope, config_files=config_files, env_file=env_file_path() if include_secrets else None,
            overrides_file=str(_overrides_path()), ca_dir=str(_ca_dir()), job_id=job_id)
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

        Yedekteki güvenilen sertifikalar (`config/ca.<ad>.pem`) `CONFIG_DIR/ca`'ya yazılır; biri yazıldıysa
        günlüğe sayıları ve parmak izleri yazılır ve bu sürecin köprü tarayıcısı kapatılır (bir sonraki istek
        onu yeni depoyla başlatır). HTTP istemcisi değişikliği bir sonraki istekte kendisi görür.
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
        _apply_certificates(report)
        return report

    def _restore(self, name: str, *, force: bool, dry_run: bool) -> RestoreReport:
        try:
            return self._store.backup.restore(name, force=force, dry_run=dry_run,
                                              overrides_file=str(_overrides_path()), ca_dir=str(_ca_dir()))
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


def _ca_dir() -> Path:
    """Güvenilen sertifikaların deposu (CONFIG_DIR / ca)."""
    from sofascore_scraper.client.trust import ca_dir

    return ca_dir()


def _apply_certificates(report: RestoreReport) -> None:
    """
    Geri yüklenen sertifikalar yürürlüğe girer: birleşik CA dosyası (`trust.bundle_path`) dosyaların değiştiğini
    kendisi görür; tarayıcının NSS veritabanı her başlatmada depodan kurulduğu için bu sürecin köprü tarayıcısı
    kapatılır (`POST /api/v1/trust` ile aynı yol). Günlüğe tek satır: sertifika sayısı ve parmak izleri.
    """
    from sofascore_scraper.client import trust

    written = [member[len(CA_MEMBER_PREFIX):] for member in report.restored if member.startswith(CA_MEMBER_PREFIX)]
    if not written:
        return
    fingerprints = [item.certificate.sha256 for item in trust.stored() if item.file in written]
    logger.warning("Trusted certificates restored from backup %s: %d certificate(s) in %d file(s) of the ca "
                   "folder (sha256 %s)", report.name, len(fingerprints), len(written),
                   ", ".join(fingerprints) or "-")
    from sofascore_scraper.client import bridge

    if not bridge.restart_for_trust_change():
        logger.warning("The built-in browser trusts the restored certificates after the app restarts")


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
