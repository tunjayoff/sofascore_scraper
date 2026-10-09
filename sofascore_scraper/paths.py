"""
Veri dizini dışındaki dosya yolları için tek kaynak: .env, config dizini ve tarayıcı profili.
Veri dizininin (DATA_DIR) düzenini yalnızca Store bilir (sofascore_scraper/store/layout.py; 2.x düzeninin adları
sofascore_scraper/store/legacy.py).

SOFASCORE_ENV_FILE ve SOFASCORE_CONFIG_DIR ortam değişkenleri, .env dosyasını ve
config dizinini başka bir yere yönlendirir (testler bunları geçici dizinlere çevirir).
"""

import os


def env_file_path() -> str:
    """Okunan/yazılan .env dosyasının yolu."""
    return os.getenv("SOFASCORE_ENV_FILE", ".env")


def config_dir() -> str:
    """leagues.txt ve league_sports.json'ın bulunduğu dizin."""
    return os.getenv("SOFASCORE_CONFIG_DIR", "config")


def default_league_config_path() -> str:
    return os.path.join(config_dir(), "leagues.txt")


DEFAULT_BROWSER_PROFILE_DIR = "~/.cache/sofascore_scraper/chrome_profile"


def browser_profile_dir() -> str:
    """
    Köprü tarayıcısının profil dizini (cookie'ler, çözülmüş challenge): `client.browser_profile`, yoksa
    varsayılan. Boş değer de "yok" sayılır. Ayar yükleyicisinden çağrı anında okunur (2.x'in
    SOFASCORE_BROWSER_PROFILE adı 3.1'de okunmaz).
    """
    from sofascore_scraper.config import loader

    raw = (loader.active_settings().client.browser_profile or "").strip()
    return os.path.expanduser(raw or DEFAULT_BROWSER_PROFILE_DIR)
