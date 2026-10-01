#!/usr/bin/env python3
"""Cross-platform click-to-run launcher for the web UI.

Ensures venv + frontend build, runs the environment check (src/doctor.py) and installs what it
can (Python packages, the bridge browser), starts the server, opens the browser.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("SOFASCORE_PORT", "8000"))
URL = f"http://127.0.0.1:{PORT}"
DIST = ROOT / "frontend" / "dist" / "index.html"
MIN_PYTHON = (3, 10)

# src/doctor.py yalnızca standart kütüphaneyi kullanır: sanal ortam kurulmadan da içe aktarılabilir
sys.path.insert(0, str(ROOT))
from src import doctor  # noqa: E402

# Bu denetimler başarısızsa sunucu hiç başlayamaz; diğer sorunlar yazdırılır ve devam edilir
# (ör. tarayıcı kurulamadıysa indirilmiş veriler yine de görüntülenebilir).
BLOCKING_CHECKS = ("python", "packages")


def _venv_python() -> Path:
    if os.name == "nt":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def _ensure_venv() -> Optional[Path]:
    py = _venv_python()
    if py.is_file():
        return py
    if sys.version_info < MIN_PYTHON:
        print(
            f"ERROR: Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required; "
            f"this is Python {sys.version_info[0]}.{sys.version_info[1]}.\n"
            "  Install a newer Python from https://www.python.org/downloads/ and start again.",
            file=sys.stderr,
        )
        return None
    print("Creating virtualenv (.venv)…")
    try:
        subprocess.check_call([sys.executable, "-m", "venv", str(ROOT / ".venv")], cwd=ROOT)
    except (OSError, subprocess.CalledProcessError):
        print(
            "ERROR: could not create the virtualenv (python -m venv .venv).\n"
            "  Debian/Ubuntu: sudo apt install python3-venv   Fedora: sudo dnf install python3-virtualenv",
            file=sys.stderr,
        )
        return None
    # Paketler ve tarayıcı ön denetimde (_preflight) kurulur: yarıda kalmış bir kurulum da böylece tamamlanır
    return _venv_python()


def _ensure_frontend() -> None:
    if DIST.is_file():
        return
    version = doctor.installed_node_version()
    if not doctor.node_is_supported(version):
        found = "Node.js " + ".".join(str(n) for n in version) + " is too old" if version else "Node.js / npm not found"
        print(
            f"WARNING: the web UI is not built and cannot be built here ({found}).\n"
            "  Install Node.js 20.19+ or 22.12+ (https://nodejs.org), then start again; or run:\n"
            "    cd frontend && npm install && npm run build\n"
            "  Continuing anyway: the API works, the browser will show a help page instead of the UI.",
            file=sys.stderr,
        )
        return
    npm = shutil.which("npm")
    print("Building web UI (first run may take a minute)…")
    frontend = ROOT / "frontend"
    try:
        if not (frontend / "node_modules").is_dir():
            subprocess.check_call([npm, "install"], cwd=frontend)
        subprocess.check_call([npm, "run", "build"], cwd=frontend)
    except (OSError, subprocess.CalledProcessError):
        print(
            "WARNING: building the web UI failed (see the output above).\n"
            "  Continuing anyway: the API works, the browser will show a help page instead of the UI.",
            file=sys.stderr,
        )


def _run_doctor(py: Path) -> Optional[Dict[str, Any]]:
    """Run the environment check with the venv's Python; None when its report cannot be read."""
    try:
        proc = subprocess.run(
            # frontend: _ensure_frontend az önce baktı ve gerekeni söyledi
            [str(py), "-m", "src.doctor", "--json", "--skip", "frontend"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            timeout=300,
        )
        report = json.loads(proc.stdout.decode("utf-8", "replace"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return report if isinstance(report, dict) and isinstance(report.get("checks"), list) else None


def _auto_fixes(py: Path) -> Dict[str, Any]:
    """Doctor result code -> (what to tell the user, command). Only these are run without asking."""
    return {
        "packages_missing": (
            "Installing Python packages…",
            [str(py), "-m", "pip", "install", "-r", "requirements.txt"],
        ),
        "browser_missing": (
            "Installing the browser the app drives (patchright's Chromium; a one-time download)…",
            doctor.browser_install_command(str(py)),
        ),
    }


def _preflight(
    py: Path,
    run_doctor: Callable[[Path], Optional[Dict[str, Any]]] = _run_doctor,
    run_fix: Callable[[List[str]], int] = lambda cmd: subprocess.call(cmd, cwd=ROOT),
) -> bool:
    """
    Environment check before the server starts. Installs missing Python packages and the missing
    browser (each tried once), prints what is still wrong with its fix, and returns False only
    when the server cannot start at all.
    """
    fixes = _auto_fixes(py)
    tried = set()
    report = run_doctor(py)
    while report is not None:
        todo = next(
            (
                c for c in report["checks"]
                if c.get("status") == "fail" and c.get("code") in fixes and c.get("code") not in tried
            ),
            None,
        )
        if todo is None:
            break
        tried.add(todo["code"])
        message, command = fixes[todo["code"]]
        print(message)
        try:
            run_fix(command)
        except OSError as e:
            print(f"  could not run {command[0]}: {e}", file=sys.stderr)
        report = run_doctor(py)

    if report is None:
        print(
            "WARNING: the environment check could not run; starting anyway.\n"
            f'  To see why: "{py}" main.py --doctor',
            file=sys.stderr,
        )
        return True

    problems = [c for c in report["checks"] if c.get("status") in ("warn", "fail")]
    for c in problems:
        tag = "ERROR" if c["status"] == "fail" else "WARNING"
        print(f"{tag}: {c.get('label')}: {c.get('summary')}", file=sys.stderr)
        if c.get("fix"):
            print(f"  → {c['fix']}", file=sys.stderr)
    blocking = [c for c in problems if c["status"] == "fail" and c.get("id") in BLOCKING_CHECKS]
    if blocking:
        print("Cannot start until the errors above are fixed.", file=sys.stderr)
        return False
    if any(c["status"] == "fail" for c in problems):
        print(
            "Starting anyway: data already downloaded can be viewed, but new downloads will fail "
            "until the errors above are fixed.",
            file=sys.stderr,
        )
    return True


def _port_open() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}/health", timeout=1.2) as r:
            return 200 <= getattr(r, "status", 200) < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _open_browser(url: str) -> None:
    """Open URL via the desktop handler; never attach Chrome stderr to this TTY."""
    # webbrowser.open() often execs a second google-chrome against a locked profile
    # and dumps "database is locked" into the launcher terminal — avoid it.
    try:
        if sys.platform == "darwin":
            subprocess.Popen(
                ["open", url],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
            return
        if os.name == "nt":
            os.startfile(url)  # type: ignore[attr-defined]
            return
        xdg = shutil.which("xdg-open") or shutil.which("gio")
        if xdg:
            cmd = [xdg, url] if xdg.endswith("xdg-open") else [xdg, "open", url]
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
            return
    except OSError:
        pass
    print(f"Open this URL in your browser:\n  {url}")


def main() -> int:
    os.chdir(ROOT)
    for stream in (sys.stdout, sys.stderr):
        try:
            # errors: eski Windows konsol kod sayfalarında çökmesin. line_buffering: çıktı bir dosyaya
            # yönlendirildiğinde de satırlar, araya giren pip/npm çıktısıyla aynı sırada görünsün
            stream.reconfigure(errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass
    py = _ensure_venv()
    if py is None:
        return 1
    _ensure_frontend()

    if _port_open():
        print(f"Already running — open:\n  {URL}")
        # Do not spawn another browser process; user likely already has a tab.
        return 0

    if not _preflight(py):
        return 1

    env = os.environ.copy()
    # Avoid reload in launcher: double-click sessions should stay one process
    cmd = [
        str(py),
        "-c",
        (
            "import uvicorn; "
            f"uvicorn.run('src.web.app:app', host='127.0.0.1', port={PORT}, reload=False)"
        ),
    ]
    print(f"Starting SofaScore Scraper at {URL}")
    print("Leave this window open. Close it to stop the app.")
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env)

    opened = False
    for _ in range(60):
        if proc.poll() is not None:
            print("Server exited early.", file=sys.stderr)
            return proc.returncode or 1
        if _port_open():
            print(f"Ready: {URL}")
            if os.environ.get("SOFASCORE_NO_BROWSER", "").strip() not in ("1", "true", "yes"):
                _open_browser(URL)
            opened = True
            break
        time.sleep(0.25)
    if not opened:
        print("Server started but /health not ready yet — open manually:", URL)

    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
