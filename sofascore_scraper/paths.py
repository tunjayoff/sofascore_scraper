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
    Köprü tarayıcısının profil dizini (cookie'ler, çözülmüş challenge): SOFASCORE_BROWSER_PROFILE,
    yoksa varsayılan. Boş değer de "yok" sayılır: `.env`'deki `SOFASCORE_BROWSER_PROFILE=` satırı
    profil dizinini "" yapıp tarayıcının başlamasını engellemesin.
    """
    raw = (os.getenv("SOFASCORE_BROWSER_PROFILE") or "").strip()
    return os.path.expanduser(raw or DEFAULT_BROWSER_PROFILE_DIR)
