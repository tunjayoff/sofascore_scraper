"""
Servis bağlamı: bir servisin çalışmak için ihtiyaç duyduğu nesneler (docs/design/02-services.md 2.3).

`build_context`, terminal arayüzünün kurucusunda duran bağlama işini (src/SofaScoreUi.py: veri dizinleri ve üç
indirici) arayüzden bağımsız yapar; web ve (P10 ile) CLI aynı bağlamı kurar.

Bugünkü bağlam indiricileri taşır. Tasarımdaki alanlar (Settings, Store, Client, JobManager, Clock) onları
getiren plan maddeleriyle eklenir: P09 (Settings), ST-17 (Store), P11 (istemci sağlığı ve işler).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from src.config_manager import ConfigManager
from src.logger import get_logger
from src.match_data_fetcher import MatchDataFetcher
from src.match_fetcher import MatchFetcher
from src.season_fetcher import SeasonFetcher

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


def build_context(config_manager: ConfigManager, *, data_dir: Optional[str] = None) -> ServiceContext:
    """
    Veri dizinlerini var eder ve üç indiriciyi kurar.

    data_dir verilmezse yapılandırmadaki DATA_DIR kullanılır (web ve CLI aynı dizine yazar). Her çağrı yeni
    indiriciler kurar; indiriciler durum taşıdığı için (iş önbelleği, son istek sayımları) bir bağlam tek bir
    işe ya da tek bir isteğe aittir.
    """
    data_dir = data_dir or config_manager.get_data_dir()
    _ensure_directory(data_dir)
    for name in DATA_SUBDIRECTORIES:
        _ensure_directory(os.path.join(data_dir, name))

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
