"""
sofascore_scraper/paths.py: veri dizini dışındaki yollar (.env, config dizini, tarayıcı profili).

Veri dizininin adları burada değildir: 3.0 düzeni sofascore_scraper/store/layout.py'de, 2.x düzeninin ad kuralı
sofascore_scraper/store/legacy.py'dedir (tests/test_store_legacy_names.py).
"""
from __future__ import annotations

import os

import conftest

from sofascore_scraper import paths


# --- .env ve config yolları -------------------------------------------------------------------

def test_env_file_path_defaults_to_dotenv_in_working_directory(monkeypatch):
    monkeypatch.delenv("SOFASCORE_ENV_FILE", raising=False)
    assert paths.env_file_path() == ".env"


def test_env_file_path_follows_environment_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SOFASCORE_ENV_FILE", str(tmp_path / "custom.env"))
    assert paths.env_file_path() == str(tmp_path / "custom.env")


def test_config_dir_defaults_to_config(monkeypatch):
    monkeypatch.delenv("SOFASCORE_CONFIG_DIR", raising=False)
    assert paths.config_dir() == "config"
    assert paths.default_league_config_path() == os.path.join("config", "leagues.txt")


def test_config_dir_follows_environment_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SOFASCORE_CONFIG_DIR", str(tmp_path))
    assert paths.config_dir() == str(tmp_path)
    assert paths.default_league_config_path() == os.path.join(str(tmp_path), "leagues.txt")


def test_test_suite_itself_is_redirected_away_from_real_config():
    """conftest ortam değişkenlerini geçici dizine çevirir; gerçek config/ ve .env'e dokunulmaz."""
    assert paths.config_dir() == conftest.CONFIG_DIR
    assert paths.env_file_path() == conftest.ENV_FILE


# --- tarayıcı profili ---------------------------------------------------------------------------

def test_browser_profile_dir_defaults_to_the_user_cache(monkeypatch):
    monkeypatch.delenv("SOFASCORE_CLIENT__BROWSER_PROFILE", raising=False)
    assert paths.browser_profile_dir() == os.path.expanduser(paths.DEFAULT_BROWSER_PROFILE_DIR)
    assert not paths.browser_profile_dir().startswith("~")


def test_browser_profile_dir_follows_the_environment_and_ignores_a_blank_value(monkeypatch, tmp_path):
    monkeypatch.setenv("SOFASCORE_CLIENT__BROWSER_PROFILE", str(tmp_path / "profile"))
    assert paths.browser_profile_dir() == str(tmp_path / "profile")
    monkeypatch.setenv("SOFASCORE_CLIENT__BROWSER_PROFILE", "  ")
    assert paths.browser_profile_dir() == os.path.expanduser(paths.DEFAULT_BROWSER_PROFILE_DIR)


def test_only_the_config_and_profile_helpers_remain():
    """Veri dizini düzeninin yardımcıları (safe_name, league_dir_name, summary_paths ...) Store'a geçti (ST-28)."""
    public = {name for name in vars(paths) if not name.startswith("_") and name != "os"}
    assert public == {"env_file_path", "config_dir", "default_league_config_path", "DEFAULT_BROWSER_PROFILE_DIR",
                      "browser_profile_dir"}
