"""leagues.txt ayrıştırma, atomik yazma, süreçler arası yeniden yükleme ve dil seçimi."""
from __future__ import annotations

import os

import pytest

from src.config_manager import ConfigManager
from src.i18n import app_language


@pytest.fixture
def make_cm(tmp_path):
    """Singleton'ı geçici olarak kenara alıp verilen içerikle yeni bir ConfigManager kurar."""
    saved = ConfigManager._instance

    def _make(text: str | None = None, encoding: str = "utf-8"):
        path = tmp_path / "leagues.txt"
        if text is not None:
            path.write_text(text, encoding=encoding)
        ConfigManager._instance = None
        return ConfigManager(str(path)), path

    yield _make
    ConfigManager._instance = saved


def test_bom_is_not_part_of_the_first_name(make_cm):
    cm, _ = make_cm("Premier League: 17\n", encoding="utf-8-sig")
    assert cm.get_leagues() == {17: "Premier League"}


def test_name_with_colon_uses_last_colon(make_cm):
    cm, _ = make_cm("Serie A: Italy: 23\n")
    assert cm.get_leagues() == {23: "Serie A: Italy"}


def test_duplicate_id_keeps_last_and_drops_stale_name(make_cm):
    cm, _ = make_cm("Old Name: 17\nNew Name: 17\n")
    assert cm.get_leagues() == {17: "New Name"}
    assert cm.get_league_by_name("Old Name") is None


def test_invalid_lines_are_skipped(make_cm):
    cm, _ = make_cm("# c\nno id here\nX: abc\nLaLiga: 8\n")
    assert cm.get_leagues() == {8: "LaLiga"}


def test_missing_file_is_created_from_example(make_cm):
    cm, path = make_cm(None)
    assert path.exists()
    assert 17 in cm.get_leagues()


def test_add_rejects_newline_names(make_cm):
    cm, path = make_cm("A: 1\n")
    assert cm.add_league("Evil\nInjected: 99", 5) is False
    assert "Injected" not in path.read_text()
    assert cm.get_leagues() == {1: "A"}


def test_add_and_remove_roundtrip(make_cm):
    cm, path = make_cm("# header\nA: 1")  # no trailing newline
    assert cm.add_league("Serie A: Italy", 23) is True
    assert cm.add_league("B", 23) is False  # duplicate id
    text = path.read_text()
    assert "A: 1\nSerie A: Italy: 23\n" in text
    assert cm.remove_league(23) is True
    assert cm.get_leagues() == {1: "A"}
    assert "# header" in path.read_text()
    assert not [p for p in os.listdir(path.parent) if p.endswith(".tmp")]


def test_external_edit_is_picked_up(make_cm):
    cm, path = make_cm("A: 1\n")
    assert cm.get_leagues() == {1: "A"}
    path.write_text("A: 1\nB: 2\n")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert cm.get_leagues() == {1: "A", 2: "B"}
    # Başka süreç B'yi ekledi; aynı ID ile tekrar ekleme reddedilmeli
    assert cm.add_league("B again", 2) is False


@pytest.mark.parametrize(
    "env,expected",
    [
        ({"APP_LANGUAGE": "en"}, "en"),
        ({"LANGUAGE": "en"}, "en"),
        ({"LANGUAGE": "en_US:en"}, "tr"),  # GNU gettext değeri yok sayılır
        ({"APP_LANGUAGE": "tr", "LANGUAGE": "en"}, "tr"),
        ({}, "tr"),
    ],
)
def test_app_language(monkeypatch, env, expected):
    monkeypatch.delenv("APP_LANGUAGE", raising=False)
    monkeypatch.delenv("LANGUAGE", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert app_language() == expected
