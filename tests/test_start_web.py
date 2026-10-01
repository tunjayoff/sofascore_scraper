"""
Tek tıkla başlatıcı (scripts/start_web.py): ön denetimi çalıştırır, eksik paketleri ve köprünün
tarayıcısını kurar. Hiçbir test gerçek kurulum, sunucu ya da tarayıcı başlatmaz.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from src import doctor

REPO = Path(__file__).resolve().parents[1]
PY = Path("/venv/bin/python")


@pytest.fixture(scope="module")
def launcher():
    spec = importlib.util.spec_from_file_location("start_web_under_test", REPO / "scripts" / "start_web.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check(check_id: str, status: str = "ok", code: str | None = None, **extra) -> dict:
    return {
        "id": check_id, "status": status, "code": code or f"{check_id}_{status}", "label": check_id.title(),
        "summary": f"{check_id} is {status}", "fix": None if status == "ok" else f"fix {check_id}", **extra,
    }


def _report(*checks: dict) -> dict:
    return {"checks": list(checks)}


class Doctor:
    """Sırayla verilen raporları döndüren sahte `_run_doctor`."""

    def __init__(self, *reports):
        self.reports = list(reports)
        self.calls = 0

    def __call__(self, py):
        assert py == PY
        self.calls += 1
        return self.reports.pop(0) if len(self.reports) > 1 else self.reports[0]


def test_missing_browser_is_installed_with_patchright_then_rechecked(launcher, capsys):
    run_doctor = Doctor(
        _report(_check("python"), _check("browser", "fail", "browser_missing")),
        _report(_check("python"), _check("browser")),
    )
    fixes = []
    assert launcher._preflight(PY, run_doctor=run_doctor, run_fix=lambda cmd: fixes.append(cmd) or 0) is True
    assert fixes == [doctor.browser_install_command(str(PY))]
    assert fixes[0][1:] == ["-m", "patchright", "install", "chromium", "--no-shell"]
    assert run_doctor.calls == 2
    out = capsys.readouterr()
    assert "Installing the browser" in out.out and "ERROR" not in out.err


def test_missing_packages_then_missing_browser_are_fixed_in_order(launcher):
    run_doctor = Doctor(
        _report(_check("packages", "fail", "packages_missing"), _check("browser", "fail", "browser_driver_missing")),
        _report(_check("packages"), _check("browser", "fail", "browser_missing")),
        _report(_check("packages"), _check("browser")),
    )
    fixes = []
    assert launcher._preflight(PY, run_doctor=run_doctor, run_fix=lambda cmd: fixes.append(cmd) or 0) is True
    assert fixes == [
        [str(PY), "-m", "pip", "install", "-r", "requirements.txt"],
        doctor.browser_install_command(str(PY)),
    ]
    assert run_doctor.calls == 3


def test_nothing_is_installed_when_everything_is_fine(launcher, capsys):
    run_doctor = Doctor(_report(_check("python"), _check("packages"), _check("browser")))
    assert launcher._preflight(PY, run_doctor=run_doctor, run_fix=lambda cmd: pytest.fail("kurulum gerekmez")) is True
    assert run_doctor.calls == 1
    out = capsys.readouterr()
    assert out.err == "" and out.out == ""


def test_browser_install_that_does_not_help_is_tried_once_and_reported(launcher, capsys):
    broken = _report(_check("packages"), _check("browser", "fail", "browser_missing"))
    fixes = []
    # tarayıcı olmadan da sunucu başlar (indirilmiş veriler görüntülenebilir): engellemez, ama söyler
    assert launcher._preflight(PY, run_doctor=Doctor(broken), run_fix=lambda cmd: fixes.append(cmd) or 1) is True
    assert len(fixes) == 1
    err = capsys.readouterr().err
    assert "ERROR: Browser: browser is fail" in err and "→ fix browser" in err and "Starting anyway" in err


def test_packages_that_cannot_be_installed_block_the_start(launcher, capsys):
    broken = _report(_check("packages", "fail", "packages_missing"))
    fixes = []
    assert launcher._preflight(PY, run_doctor=Doctor(broken), run_fix=lambda cmd: fixes.append(cmd) or 1) is False
    assert len(fixes) == 1  # sonsuz döngü yok
    assert "Cannot start" in capsys.readouterr().err


def test_old_python_blocks_and_other_failures_do_not(launcher, capsys):
    assert launcher._preflight(PY, run_doctor=Doctor(_report(_check("python", "fail", "python_too_old")))) is False
    capsys.readouterr()
    report = _report(_check("data_dir", "fail", "data_dir_not_writable"), _check("env", "warn", "env_invalid"))
    assert launcher._preflight(PY, run_doctor=Doctor(report), run_fix=lambda cmd: pytest.fail("otomatik çözüm yok")) is True
    err = capsys.readouterr().err
    assert "ERROR: Data_Dir:" in err and "WARNING: Env:" in err and "→ fix env" in err


def test_unreadable_doctor_report_does_not_block_the_start(launcher, capsys):
    assert launcher._preflight(PY, run_doctor=lambda py: None, run_fix=lambda cmd: pytest.fail("çalışmamalı")) is True
    assert "--doctor" in capsys.readouterr().err


def test_fix_command_that_cannot_be_started_is_survived(launcher, capsys):
    def no_such_program(cmd):
        raise FileNotFoundError(cmd[0])

    broken = _report(_check("browser", "fail", "browser_missing"))
    assert launcher._preflight(PY, run_doctor=Doctor(broken), run_fix=no_such_program) is True
    assert "could not run" in capsys.readouterr().err


def test_launcher_runs_the_doctor_with_the_venv_python_and_reads_its_json(launcher, monkeypatch):
    seen = {}

    class Proc:
        stdout = b'{"status": "ok", "checks": [{"id": "python", "status": "ok"}]}'

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["cwd"] = cmd, kwargs["cwd"]
        return Proc()

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    report = launcher._run_doctor(PY)
    assert report["checks"][0]["id"] == "python"
    assert seen["cmd"][:4] == [str(PY), "-m", "src.doctor", "--json"] and seen["cwd"] == launcher.ROOT

    Proc.stdout = b"Traceback (most recent call last): ..."
    assert launcher._run_doctor(PY) is None
    Proc.stdout = b'{"unexpected": true}'
    assert launcher._run_doctor(PY) is None


def test_ui_is_not_built_without_a_supported_node_and_the_launcher_says_so(launcher, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(launcher, "DIST", tmp_path / "dist" / "index.html")
    monkeypatch.setattr(launcher.subprocess, "check_call", lambda *a, **k: pytest.fail("npm çalıştırılmamalı"))
    for version, expected in [(None, "not found"), ((18, 19, 0), "Node.js 18.19.0 is too old")]:
        monkeypatch.setattr(launcher.doctor, "installed_node_version", lambda v=version: v)
        launcher._ensure_frontend()
        err = capsys.readouterr().err
        assert expected in err and "20.19" in err and "help page" in err


def test_failed_ui_build_does_not_crash_the_launcher(launcher, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(launcher, "DIST", tmp_path / "dist" / "index.html")
    monkeypatch.setattr(launcher.doctor, "installed_node_version", lambda: (22, 12, 0))
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/" + name)

    def failing(cmd, **kwargs):
        raise launcher.subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(launcher.subprocess, "check_call", failing)
    launcher._ensure_frontend()  # istisna yok
    assert "building the web UI failed" in capsys.readouterr().err


def test_venv_is_not_created_with_an_old_python(launcher, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(launcher, "_venv_python", lambda: tmp_path / "no-venv" / "bin" / "python")
    monkeypatch.setattr(launcher, "MIN_PYTHON", (99, 0))
    monkeypatch.setattr(launcher.subprocess, "check_call", lambda *a, **k: pytest.fail("venv oluşturulmamalı"))
    assert launcher._ensure_venv() is None
    assert "Python 99.0 or newer is required" in capsys.readouterr().err


def test_shell_launchers_keep_the_window_open_on_failure():
    """`exec` ile çalıştırılınca hata iletisi, çift tıklamayla açılan pencereyle birlikte kayboluyordu."""
    for name in ("start-sofascore.sh", "Start SofaScore.command"):
        text = (REPO / name).read_text(encoding="utf-8")
        assert "exec python3" not in text and "scripts/start_web.py" in text
        assert 'exit "$status"' in text and "Press Enter" in text


@pytest.mark.parametrize("name", ["scripts/install.sh", "scripts/install.ps1", "README.md", "README.tr.md"])
def test_installers_and_readme_install_the_browser_the_bridge_uses(name):
    text = (REPO / name).read_text(encoding="utf-8-sig")
    assert "-m patchright install chromium" in text
    assert "playwright install" not in text
    # Eski, yanlış iddia: "Chrome kuruluysa gerekmez / yine çalışır"
    for claim in ("Google Chrome varsa yine çalışır", "only needed if Google Chrome", "yalnızca Google Chrome kurulu değilse"):
        assert claim not in text


def test_nothing_else_tells_users_to_run_playwright_install():
    for path in [REPO / "main.py", *sorted((REPO / "src").rglob("*.py")), REPO / "scripts" / "start_web.py"]:
        assert "playwright install" not in path.read_text(encoding="utf-8"), path
