#!/usr/bin/env python3
"""Cross-platform click-to-run launcher for the web UI.

Ensures venv + frontend build, starts the server, opens the browser.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("SOFASCORE_PORT", "8000"))
URL = f"http://127.0.0.1:{PORT}"
DIST = ROOT / "frontend" / "dist" / "index.html"


def _venv_python() -> Path:
    if os.name == "nt":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def _ensure_venv() -> Path:
    py = _venv_python()
    if py.is_file():
        return py
    print("Creating virtualenv (.venv)…")
    subprocess.check_call([sys.executable, "-m", "venv", str(ROOT / ".venv")], cwd=ROOT)
    py = _venv_python()
    pip = [str(py), "-m", "pip", "install", "-r", "requirements.txt"]
    print("Installing Python packages…")
    subprocess.check_call(pip, cwd=ROOT)
    return py


def _ensure_frontend() -> None:
    if DIST.is_file():
        return
    npm = shutil.which("npm")
    if not npm:
        print(
            "WARNING: frontend/dist missing and npm not found.\n"
            "  Install Node.js, then run:  cd frontend && npm install && npm run build\n"
            "  Continuing anyway — API will work, UI may show a build hint.",
            file=sys.stderr,
        )
        return
    print("Building web UI (first run may take a minute)…")
    frontend = ROOT / "frontend"
    if not (frontend / "node_modules").is_dir():
        subprocess.check_call([npm, "install"], cwd=frontend)
    subprocess.check_call([npm, "run", "build"], cwd=frontend)


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
    py = _ensure_venv()
    _ensure_frontend()

    if _port_open():
        print(f"Already running — open:\n  {URL}")
        # Do not spawn another browser process; user likely already has a tab.
        return 0

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
