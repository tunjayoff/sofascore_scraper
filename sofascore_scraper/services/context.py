"""
Servis bağlamı: bir servisin çalışmak için ihtiyaç duyduğu nesneler (docs/design/02-services.md 2.3).

`build_context` bir servisin bağlamını kurar (veri dizinleri, takiplerin eşitlenmesi, istemci); web, CLI ve
zamanlayıcı aynı bağlamı kurar. Eskiden bu iş terminal arayüzünün kurucusundaydı (P08 taşıdı, P26 arayüzü kaldırdı).

Bağlam SofaScore istemcisini ve veri dizininin deposuna giden yolu taşır:

  * `client`  istek katmanının yüzü (sofascore_scraper/client). Köprü sağlığındaki her geçiş, bağlamın veri dizinindeki
              deponun çalışma zamanı bilgilerine yazılır (`store.runtime`, anahtar "bridge_health"); başka bir
              süreç (ör. durum komutu) oradan okur.
  * `store`   veri dizininin deposu. **İlk erişimde açılır**: bağlamı kurmak `.meta/` altında hiçbir şey
              yaratmaz (yalnızca takip girdisi olan bir yapılandırma dosyası state.db'yi kurar, aşağıya bakın).
  * `jobs`    o deponun iş yöneticisi (sofascore_scraper/jobs/manager.py): komut satırı işlerini bununla yürütür.

Tasarımdaki diğer alanlar (Settings, Clock) onları getiren plan maddeleriyle eklenir.

2.x'in indiricileri (`season_fetcher`, `match_fetcher`, `match_data_fetcher`; P15'ten beri eski adlı yüzler) 3.1'de
kalktı (plan maddesi P30): servisler listeleri ListingService ile, detayları DetailPhase ve boru hattıyla çalıştırır.

Bağlam kurulurken takipler de `follows` tablosuna eşitlenir (plan maddesi ST-17): yapılandırma dosyasının
`[[follow]]` girdileri "config" kaynağıyla, lig dosyaları "legacy" kaynağıyla (bkz. `_sync_follows`). Bu eşitleme
depoyu açmaz: tablo kısa ömürlü bir bağlantıyla güncellenir.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Set, Tuple

from sofascore_scraper.client import Client
from sofascore_scraper.config_manager import ConfigManager
from sofascore_scraper.exceptions import StorageError
from sofascore_scraper.jobs.manager import JobManager
from sofascore_scraper.logger import get_logger
from sofascore_scraper.store import FollowSpec, JobStore, Store, apply_follows, open_store

logger = get_logger("Services")

# Veri dizini altında her bağlam kuruluşunda var edilen dizinler. Sezon listeleri ve programlar v3/tournaments/
# altına yazılır (plan maddesi ST-22); boş `seasons/` ve `matches/` artık kurulmaz (plan maddesi FX-15).
DATA_SUBDIRECTORIES = ("match_details", "datasets")


@dataclass(frozen=True)
class ServiceContext:
    """
    Servislerin paylaştığı nesneler.

    config              yapılandırma (ligler, eşikler, veri dizini)
    data_dir            verinin yazıldığı kök dizin
    client              SofaScore istemcisi; köprü sağlığı değişimleri deponun çalışma zamanı bilgilerine yazılır
    store               (özellik) veri dizininin deposu; ilk erişimde açılır
    jobs                (özellik) deponun iş yöneticisi
    """

    config: ConfigManager
    data_dir: str
    client: Optional[Client] = None

    @property
    def store(self) -> Store:
        """
        Veri dizininin deposu (`open_store`: süreçte dizin başına tek nesne). İlk erişim `.meta/` altında eksik
        olanları kurar (schema.json, state.db, catalog.db); açılamazsa StoreError (bir StorageError) fırlar.
        """
        return open_store(self.data_dir)

    @property
    def jobs(self) -> JobManager:
        """Deponun iş yöneticisi. Aynı depo için hep aynı iş deposunu kullanır (çalışan iş onda durur)."""
        return JobManager(JobStore.for_store(self.store))


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


# Köprü sağlığının yazılacağı veri dizini: en son kurulan bağlamınki (süreçte tek köprü, tek sağlık durumu)
RUNTIME_BRIDGE_HEALTH = "bridge_health"
_health_data_dir: Optional[str] = None


def _record_bridge_health(snapshot: Mapping[str, Any]) -> None:
    """
    İstemcinin `on_health_change` geri çağrısı: köprü sağlığının görüntüsünü deponun çalışma zamanı bilgilerine
    yazar (ok → degraded → blocked ve geri dönüş; geçiş başına bir kez). Yazılamaması isteği bozmaz.
    """
    data_dir = _health_data_dir
    if data_dir is None:
        return
    try:
        open_store(data_dir).runtime.set(RUNTIME_BRIDGE_HEALTH, snapshot)
    except Exception as e:  # depo meşgul, daha yeni ya da kapatılmış olabilir: sağlık bilgisi yalnızca bilgidir
        logger.debug("Bridge health could not be stored in %s: %s", data_dir, e)


def _client_for(data_dir: str) -> Optional[Client]:
    """
    Bağlamın istemcisi; köprü sağlığı değişimleri `data_dir`in deposuna yazılır. Geri çağrı modül düzeyinde tek
    bir işlevdir: bağlam her işte ve istekte kurulsa da sağlık kaynağına bir kez eklenir.
    """
    global _health_data_dir
    _health_data_dir = data_dir
    try:
        return Client(on_health_change=_record_bridge_health)
    except ValueError as e:  # API_BASE_URL http(s) değil: istekler zaten başarısız olur, bağlam yine kurulur
        logger.warning("SofaScore client could not be built: %s", e)
        return None


def build_context(config_manager: ConfigManager, *, data_dir: Optional[str] = None) -> ServiceContext:
    """
    Veri dizinlerini var eder, takipleri `follows` tablosuna eşitler ve istemciyi kurar.

    data_dir verilmezse yapılandırmadaki DATA_DIR kullanılır (web ve CLI aynı dizine yazar). Her çağrı yeni bir
    bağlamdır; bir bağlam tek bir işe ya da tek bir isteğe aittir.

    USE_COLOR kapalıyken NO_COLOR'ı süreç başlarken günlükçü kurar (sofascore_scraper/logger.py); bağlam ortama dokunmaz.
    """
    data_dir = data_dir or config_manager.get_data_dir()
    _ensure_directory(data_dir)
    for name in DATA_SUBDIRECTORIES:
        _ensure_directory(os.path.join(data_dir, name))
    _sync_follows(config_manager, data_dir)
    return ServiceContext(config=config_manager, data_dir=data_dir, client=_client_for(data_dir))
