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


# --- Veri dizini düzeni -------------------------------------------------------
# Yazıcılar ve okuyucular aynı adları bu yardımcılardan alır. Mevcut disk düzeniyle
# birebir aynıdır (boşluk ve '/' → '_'), migration gerektirmez.

def safe_name(name: str) -> str:
    """Dizin/dosya adı parçası: boşluk ve yol ayırıcıları '_' olur."""
    return str(name).replace(" ", "_").replace("/", "_").replace("\\", "_")


def league_label(league_id: int, league_name: "str | None") -> str:
    """Config'deki lig adı; yoksa sabit bir yer tutucu (tüm modüllerde aynı)."""
    return league_name or f"League_{league_id}"


def league_dir_name(league_id: int, league_name: "str | None") -> str:
    return f"{league_id}_{safe_name(league_label(league_id, league_name))}"


def season_dir_name(season_id: int, season_name: str) -> str:
    return f"{season_id}_{safe_name(season_name)}"


def seasons_file(data_dir: str, league_id: int, league_name: "str | None") -> str:
    return os.path.join(data_dir, "seasons", f"{league_dir_name(league_id, league_name)}_seasons.json")


def matches_league_dir(data_dir: str, league_id: int, league_name: "str | None") -> str:
    return os.path.join(data_dir, "matches", league_dir_name(league_id, league_name))


def matches_season_dir(data_dir: str, league_id: int, league_name: "str | None", season_id: int, season_name: str) -> str:
    return os.path.join(matches_league_dir(data_dir, league_id, league_name), season_dir_name(season_id, season_name))


def summary_paths(data_dir: str, league_id: int, league_name: "str | None", season_id: int, season_name: str):
    """(summary.json, summary.csv) — sezon özetleri lig dizininde durur."""
    base = os.path.join(
        matches_league_dir(data_dir, league_id, league_name), f"{season_dir_name(season_id, season_name)}_summary"
    )
    return f"{base}.json", f"{base}.csv"
