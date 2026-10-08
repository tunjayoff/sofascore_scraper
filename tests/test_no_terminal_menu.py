"""
Terminal menüsü 3.0'da kaldırıldı (plan maddesi P26; docs/design/02-services.md bölüm 7): insanlar web arayüzünü,
sunucular ve otomasyon komut satırını (`ssc`) kullanır.

  * sofascore_scraper/ui/ ve sofascore_scraper/SofaScoreUi.py yok ve hiçbir modül onları içe aktarmaz;
  * `python main.py` argümansız `ssc` gibi komutların listesini yazar ve 2 ile çıkar; 2.x'in bayrakları 3.1'de kalktı
    (P30): onlarla çalıştırma kullanım hatasıdır.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Dict, Iterator, List

import pytest

import main as cli
from sofascore_scraper.cli import exit_codes

ROOT = Path(__file__).resolve().parents[1]
# `main.py`yi bu süreçte çalıştıran testler süreçte bıraktıklarını geri alır (tests/conftest.py)
pytestmark = pytest.mark.usefixtures("restore_cli_process")
MENU_MODULES = ("sofascore_scraper.ui", "sofascore_scraper.SofaScoreUi")


def _python_files() -> Iterator[Path]:
    yield ROOT / "main.py"
    for folder in ("sofascore_scraper", "scripts", "tests"):
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
    assert not (ROOT / "sofascore_scraper" / "ui").exists() or not any((ROOT / "sofascore_scraper" / "ui").rglob("*.py"))
    assert not (ROOT / "sofascore_scraper" / "SofaScoreUi.py").exists()


def test_no_module_imports_the_terminal_menu() -> None:
    found = {
        str(path.relative_to(ROOT)): menu
        for path in _python_files()
        for menu in [[name for name in _imported_modules(path) if _is_menu_module(name)]]
        if menu
    }
    # tests/test_sync_service.py içe aktarma denetleyicisini bir dize içindeki örnek kaynakla sınar (içe aktarmaz)
    assert found == {}


@pytest.mark.parametrize("argv", [[], ["--data-dir", "elsewhere"]])
def test_main_without_a_command_lists_the_commands_and_exits_2(
    argv: List[str], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_LANGUAGE", "en")

    assert cli.main(argv) == exit_codes.USAGE_ERROR

    out, err = capsys.readouterr()
    assert out == ""
    assert "a command is required" in err and "python main.py serve" in err and "ssc --help" in err
    for name in ("backup", "export", "serve", "status", "sync", "watch"):
        assert name in err
    assert list(tmp_path.iterdir()) == []  # hiçbir şey oluşturulmadı


@pytest.mark.parametrize("argv", [["--refresh-legacy"], ["--ignore-rate-limit"], ["--headless"]])
def test_an_old_flag_without_an_action_is_a_usage_error(
    argv: List[str], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_LANGUAGE", "en")

    assert cli.main(argv) == exit_codes.USAGE_ERROR

    err = capsys.readouterr().err
    assert "the flags of `python main.py` were removed in 3.1" in err
    assert list(tmp_path.iterdir()) == []


def test_the_command_list_is_in_the_app_language(capsys: pytest.CaptureFixture[str],
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_LANGUAGE", "tr")

    assert cli.main([]) == exit_codes.USAGE_ERROR

    assert "Kullanım hatası" in capsys.readouterr().err


# --- çeviri anahtarları -----------------------------------------------------------------------------
#
# locales/*.json'daki her anahtarı ürün kodu kullanır. Menüyle birlikte yalnızca onun kullandığı anahtarlar
# kaldırıldı (P26). Kod bazı anahtarları önek + değişken ile kurar; o önekler aşağıda.

DYNAMIC_PREFIXES = {
    "bridge_reason_": "sofascore_scraper/bridge_health.py",  # t(f"bridge_reason_{kind}")
    "doctor_label_": "sofascore_scraper/doctor.py",  # ctx.t("doctor_label_" + check_id)
    "launcher_": "scripts/start_web.py",  # _messages.t("launcher_" + key)
    "ssc_error_": "sofascore_scraper/cli/output.py",  # "ssc_error_" + error.code
}
# Kodun artık kullanmadığı ama başka testlerin varlığını denetlediği anahtarlar (o testlerle birlikte gidebilir):
# tests/test_diagnostics.py::test_cli_messages_exist_in_both_languages, tests/test_language.py.
KEPT_FOR_OTHER_TESTS = frozenset({
    "check_log_for_details", "check_console_for_details", "diagnostics_failed", "details_finished",
})


def _product_text() -> str:
    paths = [ROOT / "main.py", *sorted((ROOT / "sofascore_scraper").rglob("*.py")), *sorted((ROOT / "scripts").glob("*.py"))]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def _locale(lang: str) -> Dict[str, str]:
    return json.loads((ROOT / "locales" / f"{lang}.json").read_text(encoding="utf-8"))


def test_every_locale_key_is_referenced() -> None:
    text = _product_text()
    for prefix, module in DYNAMIC_PREFIXES.items():
        assert prefix in (ROOT / module).read_text(encoding="utf-8"), (prefix, module)
    for lang in ("en", "tr"):
        unused = sorted(
            key for key in _locale(lang)
            if key not in KEPT_FOR_OTHER_TESTS
            and not key.startswith(tuple(DYNAMIC_PREFIXES))
            and re.search(r"(?<![A-Za-z0-9_])" + re.escape(key) + r"(?![A-Za-z0-9_])", text) is None
        )
        assert unused == [], (lang, unused)


def test_the_menu_keys_are_gone() -> None:
    keys = _locale("en")
    for key in ("main_menu_title", "submenu_settings_backup", "headless_updating_all", "notice_menu_backup_old_layout",
                "dependency_colorama", "web_server_starting"):
        assert key not in keys
