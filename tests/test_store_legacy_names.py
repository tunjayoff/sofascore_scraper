"""
2.x düzeninin ad kuralı (src/store/legacy.py `safe_name`, `league_dir_name`; kökten `src.store.league_dir_name`).

Okuyucu eski dizinleri bu kuralla tanır, eski yanıtlar (dosya raporu, maç listelerinin lig sütunu) ligleri
bu adla anar. Kural 2.x yazıcılarının kuralıdır (eskiden src/paths.py'deydi; ST-28 Store'a taşıdı):
değişirse eski veri dizinleri bulunamaz.
"""
from __future__ import annotations

import os

import pytest

import conftest
import src.store
from src.store import legacy


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
    assert legacy.safe_name(name) == expected


@pytest.mark.parametrize("name", ["a/b", "a\\b", "../../etc/passwd", "..\\..\\x", "/abs", "C:\\abs"])
def test_safe_name_never_yields_a_path_separator(name):
    """Ad tek bir yol bileşeni kalır: lig/sezon adı veri dizininin dışına çıkamaz."""
    result = legacy.safe_name(name)
    assert "/" not in result
    assert "\\" not in result


def test_league_dir_name_carries_the_id_and_a_placeholder_without_a_name():
    assert src.store.league_dir_name is legacy.league_dir_name
    assert legacy.league_dir_name(17, "Premier League") == "17_Premier_League"
    assert legacy.league_dir_name(17, None) == "17_League_17"
    assert legacy.league_dir_name(17, "") == "17_League_17"
    assert legacy.league_dir_name(17, "Serie A: Italy") == "17_Serie_A:_Italy"


def test_the_seeded_test_data_uses_the_same_names():
    """conftest'in elle yazdığı 2.x veri düzeni bu kuralın adlarını taşır."""
    league_dir = legacy.league_dir_name(conftest.LEAGUE_ID, conftest.LEAGUE_NAME)
    season = f"{conftest.SEASON_ID}_{legacy.safe_name(conftest.SEASON_NAME)}"
    assert os.path.isfile(os.path.join(conftest.DATA_DIR, "seasons", f"{league_dir}_seasons.json"))
    assert os.path.isfile(os.path.join(conftest.DATA_DIR, "matches", league_dir, f"{season}_summary.csv"))
