"""
Kullanıcıya giden metinler bugünkü uygulamayı anlatır (plan maddesi FX-22; kullanıcı belgeleri denetimi #163).

Terminal menüsü 3.0'da kalktı (P26), uygulama 21 spor indirir (SP-1..SP-3), Ayarlar sayfası `overrides.json`'a
yazar. Bu testler o eski iddiaların (terminal modları, üç spor, "config'de kayıtlı olmalı") geri gelmesini önler.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.sports import sport_slugs
from src.web.missing_ui import MISSING_UI_HTML

REPO = Path(__file__).resolve().parents[1]
STALE = re.compile(r"terminal mode|terminal modlar|football match data|futbol maçı verilerini|"
                   r"must be in the config|config'de kayıtlı|without showing the user interface|"
                   r"kullanıcı arayüzünü göstermeden", re.I)


def _locale(lang: str) -> dict:
    return json.loads((REPO / "locales" / f"{lang}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_no_locale_text_repeats_a_removed_claim(lang: str) -> None:
    stale = {key: text for key, text in _locale(lang).items() if isinstance(text, str) and STALE.search(text)}
    assert stale == {}


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_the_main_py_description_counts_every_sport(lang: str) -> None:
    # Sayı metinde sabittir (argparse metni biçimlendirir, yer tutucu olamaz): yeni bir spor onu da değiştirir
    assert re.findall(r"\d+", _locale(lang)["cli_description"]) == [str(len(list(sport_slugs())))]


def test_the_missing_ui_page_points_at_the_command_line() -> None:
    assert not STALE.search(MISSING_UI_HTML)
    assert MISSING_UI_HTML.count("<code>ssc doctor</code>") == 2  # iki dilde de


def test_the_image_label_does_not_list_three_sports() -> None:
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    label = re.search(r'org\.opencontainers\.image\.description="([^"]*)"', dockerfile)
    assert label and f"{len(list(sport_slugs()))} sports" in label.group(1)
    assert "Ayarlar sayfası .env'e yazar" not in dockerfile


@pytest.mark.parametrize("lang", ["en", "tr"])
@pytest.mark.parametrize("key", ["doctor_config_none", "ssc_config_no_file", "ssc_config_valid_no_file"])
def test_the_settings_sources_name_the_settings_page_file(lang: str, key: str) -> None:
    # Yapılandırma dosyası olmadan da Ayarlar sayfasının overrides.json'ı okunur (src/config/loader.py)
    assert "overrides.json" in _locale(lang)[key]
