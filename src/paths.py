"""
Uygulamanın kullandığı dosya yolları için tek kaynak.

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
