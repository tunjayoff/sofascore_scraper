"""leagues.txt ayrıştırma, atomik yazma, süreçler arası yeniden yükleme ve dil seçimi."""
from __future__ import annotations

import os

import pytest

from sofascore_scraper.config_manager import ConfigManager
from sofascore_scraper.i18n import app_language


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


def test_missing_file_is_created_from_example_without_any_league(make_cm):
    """
    Yeni kurulum boş başlar: eskiden ekilen "Premier League: 17"nin sporu yoktu (league_sports.json
    yazılmıyordu), ilk ekran "spor seçin" kutusuyla açılıyor ve karşılama durumu hiç görünmüyordu.
    """
    cm, path = make_cm(None)
    assert path.exists()
    assert cm.get_leagues() == {}
    text = path.read_text(encoding="utf-8")
    assert "Format: League Name: ID" in text  # biçim hâlâ anlatılıyor
    assert all(line.startswith("#") or not line.strip() for line in text.splitlines())
    assert not (path.parent / "league_sports.json").exists()


def test_with_a_configuration_file_no_league_file_is_created_but_one_is_read(make_cm, tmp_path, monkeypatch, caplog):
    """
    F24 (FX-23): sofascore.toml kullanılırken takipler ondan ve takip tablosundan gelir; sunucu başlarken yalnızca
    yorum taşıyan bir leagues.txt yaratılıp yedeklere giriyordu. Var olan liste yine okunur ve ona lig eklenebilir.
    """
    from sofascore_scraper.config import loader

    toml = tmp_path / "sofascore.toml"
    toml.write_text('[defaults]\nslices = ["core"]\n', encoding="utf-8")
    monkeypatch.setenv(loader.CONFIG_ENV, str(toml))
    loader.reset()
    try:
        with caplog.at_level("DEBUG", logger="ConfigManager"):
            cm, path = make_cm(None)
        assert not path.exists() and cm.get_leagues() == {}
        assert not [r for r in caplog.records if r.name == "ConfigManager" and r.levelname == "WARNING"]
        assert cm.add_league("Premier League", 17) is True  # gerekirse liste o an yaratılır
        assert path.read_text(encoding="utf-8").rstrip().endswith("Premier League: 17")
        cm, _ = make_cm("LaLiga: 8\n")
        assert cm.get_leagues() == {8: "LaLiga"}
    finally:
        monkeypatch.delenv(loader.CONFIG_ENV)
        loader.reset()


def test_first_league_can_be_added_to_the_empty_file(make_cm):
    cm, path = make_cm(None)
    assert cm.add_league("Premier League", 17) is True
    assert cm.get_leagues() == {17: "Premier League"}
    assert path.read_text(encoding="utf-8").rstrip().endswith("Premier League: 17")


def test_example_file_and_embedded_fallback_seed_no_league(make_cm, monkeypatch):
    example = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "leagues.example.txt")
    with open(example, encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert all(line.startswith("#") or not line.strip() for line in lines)
    assert any("Premier League: 17" in line for line in lines)  # örnek satır yorum olarak duruyor

    # Şablon dosyası yoksa kullanılan gömülü metin de lig eklemez
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda p: False if str(p).endswith("leagues.example.txt") else real_exists(p))
    cm, path = make_cm(None)
    assert path.exists() and cm.get_leagues() == {}


def test_existing_league_file_is_left_untouched(make_cm):
    """Var olan kullanıcıların yapılandırmasına dokunulmaz: ekilmiş lig de yerinde kalır."""
    original = "# League configuration file\n# Format: League Name: ID\n\nPremier League: 17\n"
    cm, path = make_cm(original)
    assert cm.get_leagues() == {17: "Premier League"}
    assert path.read_text(encoding="utf-8") == original


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
        ({"LANGUAGE": "tr_TR:tr"}, "en"),  # GNU gettext değeri yok sayılır
        ({"APP_LANGUAGE": "tr", "LANGUAGE": "en"}, "tr"),
        ({}, "en"),  # açık ayar yok, sistem dili desteklenmiyor: İngilizce (kuralın tamamı: test_language.py)
    ],
)
def test_app_language(monkeypatch, env, expected):
    monkeypatch.delenv("APP_LANGUAGE", raising=False)
    monkeypatch.delenv("LANGUAGE", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert app_language() == expected
