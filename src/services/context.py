"""
Servis bağlamı: bir servisin çalışmak için ihtiyaç duyduğu nesneler (docs/design/02-services.md 2.3).

`build_context`, terminal arayüzünün kurucusunda duran bağlama işini (src/SofaScoreUi.py: veri dizinleri ve üç
indirici) arayüzden bağımsız yapar; web ve (P10 ile) CLI aynı bağlamı kurar.

Bugünkü bağlam indiricileri taşır. Tasarımdaki alanlar (Settings, Store, Client, JobManager, Clock) onları
getiren plan maddeleriyle eklenir (P11: istemci sağlığı ve işler).

Bağlam kurulurken takipler de `follows` tablosuna eşitlenir (plan maddesi ST-17): yapılandırma dosyasının
`[[follow]]` girdileri "config" kaynağıyla, lig dosyaları "legacy" kaynağıyla (bkz. `_sync_follows`). Bağlam
bir Store taşımaz: tablo kısa ömürlü bir bağlantıyla güncellenir, süreçte açık bir depo kalmaz.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Set, Tuple

from src.config_manager import ConfigManager
from src.exceptions import StorageError
from src.logger import get_logger
from src.match_data_fetcher import MatchDataFetcher
from src.match_fetcher import MatchFetcher
from src.season_fetcher import SeasonFetcher
from src.store import FollowSpec, apply_follows

logger = get_logger("Services")

# Veri dizini altında her bağlam kuruluşunda var edilen dizinler (bugünkü yerleşim)
DATA_SUBDIRECTORIES = ("seasons", "matches", "match_details", "datasets")


@dataclass(frozen=True)
class ServiceContext:
    """
    Servislerin paylaştığı nesneler.

    config              yapılandırma (ligler, eşikler, veri dizini)
    data_dir            verinin yazıldığı kök dizin
    season_fetcher      sezon listeleri
    match_fetcher       maç listeleri (program)
    match_data_fetcher  maç detayları, yenileme ve CSV düzleştirme
    """

    config: ConfigManager
    data_dir: str
    season_fetcher: SeasonFetcher
    match_fetcher: MatchFetcher
    match_data_fetcher: MatchDataFetcher


def _ensure_directory(directory: str) -> None:
    """Dizin yoksa oluşturur; oluşturulamıyorsa OSError çağırana çıkar (iş başlamadan durur)."""
    if not os.path.exists(directory):
        os.makedirs(directory)
        logger.info("Created directory: %s", directory)


# Uygulanamayan bir [[follow]] girdisi süreç başına bir kez bildirilir (bağlam her işte ve istekte kurulur)
_reported_conflicts: Set[Tuple[str, int, str]] = set()


def _sync_follows(config_manager: ConfigManager, data_dir: str) -> None:
    """
    Veri dizininin `follows` tablosunu kaynaklarıyla eşitler; hiçbir dosyayı yeniden yazmaz.

    Önce yapılandırma dosyasının `[[follow]]` girdileri "config" kaynağıyla uygulanır: dosyada olmayanlar
    silinir, bu yüzden dosya kaldırıldığında ya da boşaltıldığında satırları da gider. Girdi varsa state.db
    gerekirse kurulur; yoksa var olmayan bir state.db'ye dokunulmaz. Sonra lig dosyaları "legacy" kaynağıyla
    yansıtılır (ConfigManager.mirror_follows): yapılandırma dosyasında da geçen bir lig "config" satırı olarak
    kalır, dosyadan çıkarılınca yeniden "legacy" olur.

    Tabloyu bugün hiçbir iş okumaz (ligler hâlâ ConfigManager'dan gelir); bu yüzden yazılamaması işi
    durdurmaz, uyarı olarak loglanır.
    """
    desired = [
        FollowSpec(kind=spec.kind, entity_id=spec.entity_id, name=spec.name, sport=spec.sport,
                   seasons=spec.seasons, slices=spec.slices, live=spec.live, enabled=spec.enabled)
        for spec in config_manager.get_settings().follows
    ]
    try:
        result = apply_follows(data_dir, desired, origin="config", create=bool(desired))
    except (StorageError, ValueError) as e:
        logger.warning("Follows of the config file could not be applied: %s", e)
    else:
        if result is not None:
            if result.changed:
                logger.info("Follows of the config file applied: %d added, %d updated, %d removed",
                            len(result.added), len(result.updated), len(result.removed))
            for conflict in result.conflicts:
                seen = (conflict.kind, conflict.entity_id, conflict.reason)
                if seen not in _reported_conflicts:
                    _reported_conflicts.add(seen)
                    logger.warning("Follow of the config file not applied: %s %s (%s): %s",
                                   conflict.kind, conflict.entity_id, conflict.name, conflict.reason)
    config_manager.mirror_follows(data_dir)


def build_context(config_manager: ConfigManager, *, data_dir: Optional[str] = None) -> ServiceContext:
    """
    Veri dizinlerini var eder, takipleri `follows` tablosuna eşitler ve üç indiriciyi kurar.

    data_dir verilmezse yapılandırmadaki DATA_DIR kullanılır (web ve CLI aynı dizine yazar). Her çağrı yeni
    indiriciler kurar; indiriciler durum taşıdığı için (iş önbelleği, son istek sayımları) bir bağlam tek bir
    işe ya da tek bir isteğe aittir.
    """
    data_dir = data_dir or config_manager.get_data_dir()
    _ensure_directory(data_dir)
    for name in DATA_SUBDIRECTORIES:
        _ensure_directory(os.path.join(data_dir, name))
    _sync_follows(config_manager, data_dir)

    # Terminal arayüzünün kurucusundan taşındı: USE_COLOR kapalıysa rich gibi kitaplıklar da renksiz yazsın
    # (maç listesi aşamasının ilerleme çubuğu sunucu konsoluna yazar). Süreç genelidir ve geri alınmaz.
    if not config_manager.get_use_color():
        os.environ["NO_COLOR"] = "1"

    season_fetcher = SeasonFetcher(config_manager, data_dir)
    return ServiceContext(
        config=config_manager,
        data_dir=data_dir,
        season_fetcher=season_fetcher,
        match_fetcher=MatchFetcher(config_manager, season_fetcher, data_dir),
        match_data_fetcher=MatchDataFetcher(config_manager, data_dir),
    )
