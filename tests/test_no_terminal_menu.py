"""
Terminal menüsü 3.0'da kaldırıldı (plan maddesi P26; docs/design/02-services.md bölüm 7): insanlar web arayüzünü,
sunucular ve otomasyon komut satırını (`ssc`) kullanır.

  * src/ui/ ve src/SofaScoreUi.py yok ve hiçbir modül onları içe aktarmaz;
  * `python main.py` argümansız (ya da yalnızca eylemsiz eski bayraklarla) kısa bir yardım yazar ve 2 ile çıkar.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, List

import pytest

import main as cli
from src.cli import exit_codes

ROOT = Path(__file__).resolve().parents[1]
MENU_MODULES = ("src.ui", "src.SofaScoreUi")


def _python_files() -> Iterator[Path]:
    yield ROOT / "main.py"
    for folder in ("src", "scripts", "tests"):
        yield from sorted((ROOT / folder).rglob("*.py"))


def _imported_modules(path: Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _is_menu_module(name: str) -> bool:
    return any(name == menu or name.startswith(menu + ".") for menu in MENU_MODULES)


def test_the_terminal_menu_modules_are_gone() -> None:
    assert not (ROOT / "src" / "ui").exists() or not any((ROOT / "src" / "ui").rglob("*.py"))
    assert not (ROOT / "src" / "SofaScoreUi.py").exists()


def test_no_module_imports_the_terminal_menu() -> None:
    found = {
        str(path.relative_to(ROOT)): menu
        for path in _python_files()
        for menu in [[name for name in _imported_modules(path) if _is_menu_module(name)]]
        if menu
    }
    # tests/test_sync_service.py içe aktarma denetleyicisini bir dize içindeki örnek kaynakla sınar (içe aktarmaz)
    assert found == {}


@pytest.mark.parametrize("argv", [[], ["--data-dir", "elsewhere"], ["--refresh-legacy"], ["--ignore-rate-limit"]])
def test_main_without_an_action_prints_a_short_help_and_exits_2(
    argv: List[str], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_LANGUAGE", "en")

    assert cli.main(argv) == exit_codes.USAGE_ERROR

    out, err = capsys.readouterr()
    assert out == ""
    assert "The terminal menu was removed in version 3.0" in err
    assert "ssc serve" in err and "ssc --help" in err and "python main.py serve" in err
    for name in ("backup", "export", "serve", "status", "sync", "watch"):
        assert name in err.split("Commands: ", 1)[1].split("\n", 1)[0].split(", ")
    assert list(tmp_path.iterdir()) == []  # hiçbir şey oluşturulmadı


def test_the_short_help_is_in_the_app_language(capsys: pytest.CaptureFixture[str],
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_LANGUAGE", "tr")

    assert cli.main([]) == exit_codes.USAGE_ERROR

    err = capsys.readouterr().err
    assert "Terminal menüsü 3.0 sürümünde kaldırıldı" in err and "ssc serve" in err


def test_a_legacy_warning_is_printed_before_the_short_help(capsys: pytest.CaptureFixture[str],
                                                          monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Eylemsiz eski bayrakların uyarıları (ör. `--config` ile bir lig dosyası) kaybolmaz."""
    monkeypatch.setenv("APP_LANGUAGE", "en")
    leagues = tmp_path / "leagues.txt"
    leagues.write_text("", encoding="utf-8")

    assert cli.main(["--config", str(leagues)]) == exit_codes.USAGE_ERROR

    err = capsys.readouterr().err
    assert "leagues.txt" in err.split("The terminal menu was removed", 1)[0]
