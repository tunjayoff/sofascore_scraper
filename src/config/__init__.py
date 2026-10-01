"""
Ayarlar: model (settings.py), yükleyici (loader.py) ve yapılandırma dosyasının JSON Schema'sı (schema.py).

`Settings` ayarların tek kaynağıdır (docs/design/02-services.md bölüm 2.3 ve 4.3). Geçiş süresince
ConfigManager getter'ları buradaki etkin ayarlardan okur; `.env` ve bugünkü ortam değişkenleri eski kaynak
olarak okunmaya devam eder.

    from src.config import active_settings
    active_settings().client.rate

Yeniden yükleme (`loader.reload`) ve testler için sıfırlama (`loader.reset`) yükleyici modülündedir.
"""
from src.config.loader import (
    CONFIG_ENV,
    CONFIG_FILE_NAME,
    OVERRIDES_FILE_NAME,
    LoadedSettings,
    Source,
    activate,
    active,
    active_settings,
    find_config_file,
    load_settings,
)
from src.config.schema import config_schema
from src.config.settings import (
    BreakerSettings,
    BridgeSettings,
    ClientSettings,
    DefaultsSettings,
    DisplaySettings,
    FetchSettings,
    FollowSpec,
    LiveSettings,
    LogSettings,
    RefreshSettings,
    ScheduleSettings,
    ScheduleTask,
    ServerSettings,
    Settings,
    SinkSpec,
    SliceOverride,
    StorageSettings,
)

__all__ = [
    "CONFIG_ENV",
    "CONFIG_FILE_NAME",
    "OVERRIDES_FILE_NAME",
    "BreakerSettings",
    "BridgeSettings",
    "ClientSettings",
    "DefaultsSettings",
    "DisplaySettings",
    "FetchSettings",
    "FollowSpec",
    "LiveSettings",
    "LoadedSettings",
    "LogSettings",
    "RefreshSettings",
    "ScheduleSettings",
    "ScheduleTask",
    "ServerSettings",
    "Settings",
    "SinkSpec",
    "SliceOverride",
    "Source",
    "StorageSettings",
    "activate",
    "active",
    "active_settings",
    "config_schema",
    "find_config_file",
    "load_settings",
]
