"""
src/paths.py: .env / config yolları ve veri dizini adları.

Yazıcılar (fetcher'lar) ve okuyucular (web, istatistik) dizin adlarını bu yardımcılardan alır;
ad kuralı değişirse eski veriler bulunamaz. İlk bölüm mevcut düzeni sabitler, son bölüm
Windows'ta geçersiz karakter içeren lig/sezon adlarını dener.
"""
from __future__ import annotations

import os
import sys

import conftest
import pytest

from src import paths

# Windows (NTFS/FAT/exFAT, SMB paylaşımları) dosya adında bunlara izin vermez
WINDOWS_FORBIDDEN = '<>:"|?*'

# Gerçekçi adlar: leagues.txt "Ad: ID" biçiminde son ':' dan böler, yani adın içinde ':' olabilir
# (tests/test_config_manager.py::test_name_with_colon_uses_last_colon); sezon adları API'den gelir.
AWKWARD_NAMES = [
    "Serie A: Italy",
    "Copa <U20>",
    'Liga "B"',
    "Who Wins?",
    "A|B Cup",
    "All*Stars",
    "U.S. Open Cup.",  # Windows sondaki nokta ve boşluğu sessizce atar
]


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


# --- ad kuralı (mevcut disk düzeni) --------------------------------------------------------

@pytest.mark.parametrize(
    "name, expected",
    [
        ("Premier League", "Premier_League"),
        ("Premier League 26/27", "Premier_League_26_27"),
        ("2. Bundesliga", "2._Bundesliga"),
        ("back\\slash", "back_slash"),
        ("Süper Lig", "Süper_Lig"),
        ("NoChange-1", "NoChange-1"),
        ("", ""),
        (2026, "2026"),  # sezon "adı" sayı gelebilir
    ],
)
def test_safe_name_replaces_spaces_and_path_separators(name, expected):
    assert paths.safe_name(name) == expected


@pytest.mark.parametrize("name", ["a/b", "a\\b", "../../etc/passwd", "..\\..\\x", "/abs", "C:\\abs"])
def test_safe_name_never_yields_a_path_separator(name):
    """Ad tek bir yol bileşeni kalır: lig/sezon adı veri dizininin dışına çıkamaz."""
    result = paths.safe_name(name)
    assert "/" not in result
    assert "\\" not in result


def test_league_label_falls_back_to_placeholder():
    assert paths.league_label(17, "Premier League") == "Premier League"
    assert paths.league_label(17, None) == "League_17"
    assert paths.league_label(17, "") == "League_17"


def test_directory_names_carry_the_id_prefix():
    assert paths.league_dir_name(17, "Premier League") == "17_Premier_League"
    assert paths.league_dir_name(17, None) == "17_League_17"
    assert paths.season_dir_name(96668, "Premier League 26/27") == "96668_Premier_League_26_27"


def test_data_layout_is_composed_from_the_same_names(tmp_path):
    data = str(tmp_path)
    league = (17, "Premier League")
    season = (96668, "Premier League 26/27")

    assert paths.seasons_file(data, *league) == os.path.join(data, "seasons", "17_Premier_League_seasons.json")
    assert paths.matches_league_dir(data, *league) == os.path.join(data, "matches", "17_Premier_League")
    assert paths.matches_season_dir(data, *league, *season) == os.path.join(
        data, "matches", "17_Premier_League", "96668_Premier_League_26_27"
    )
    assert paths.summary_paths(data, *league, *season) == (
        os.path.join(data, "matches", "17_Premier_League", "96668_Premier_League_26_27_summary.json"),
        os.path.join(data, "matches", "17_Premier_League", "96668_Premier_League_26_27_summary.csv"),
    )


def test_helpers_find_the_seeded_test_data():
    """conftest'in elle yazdığı veri düzeni ile yardımcıların ürettiği yollar aynı olmalı."""
    league = (conftest.LEAGUE_ID, conftest.LEAGUE_NAME)
    season = (conftest.SEASON_ID, conftest.SEASON_NAME)

    assert os.path.isfile(paths.seasons_file(conftest.DATA_DIR, *league))
    assert os.path.isfile(paths.summary_paths(conftest.DATA_DIR, *league, *season)[1])


# --- Windows'ta geçersiz karakterler -----------------------------------------------------------

def _valid_windows_component(name: str) -> bool:
    return (
        bool(name)
        and not any(ch in WINDOWS_FORBIDDEN or ord(ch) < 32 for ch in name)
        and not name.endswith((".", " "))
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Bilinen hata: safe_name yalnızca boşluk, '/' ve '\\' değiştirir (src/paths.py); "
        "':' gibi Windows'ta geçersiz karakterler dizin/dosya adına aynen geçer. leagues.txt "
        "adlarda ':' kabul ediyor. Aynı kuralın kopyaları: match_data_fetcher.py, ui/match_ui.py."
    ),
)
@pytest.mark.parametrize("name", AWKWARD_NAMES)
def test_directory_names_are_valid_on_windows(name):
    assert _valid_windows_component(paths.league_dir_name(1, name))
    assert _valid_windows_component(paths.season_dir_name(2, name))


@pytest.mark.xfail(
    sys.platform == "win32",
    strict=True,
    reason=(
        "Bilinen hata (yalnızca Windows'ta görünür): lig/sezon adındaki ':', '?', '*', '\"', '<', '>', '|' "
        "ile dizin oluşturulamaz; sondaki nokta ise sessizce atılır ve okuyucular dizini bulamaz."
    ),
)
@pytest.mark.parametrize("name", AWKWARD_NAMES)
def test_season_directory_and_summary_round_trip_on_the_real_filesystem(tmp_path, name):
    """Yazıcının oluşturduğu dizin, okuyucunun aradığı adla diskte bulunmalı."""
    data = str(tmp_path)
    season_dir = paths.matches_season_dir(data, 1, name, 2, name)
    summary_json, _ = paths.summary_paths(data, 1, name, 2, name)

    os.makedirs(season_dir)
    with open(summary_json, "w", encoding="utf-8") as f:
        f.write("{}")

    league_dir = paths.matches_league_dir(data, 1, name)
    assert os.listdir(os.path.join(data, "matches")) == [os.path.basename(league_dir)]
    assert sorted(os.listdir(league_dir)) == sorted([os.path.basename(season_dir), os.path.basename(summary_json)])
