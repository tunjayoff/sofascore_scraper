"""Sürümün tek kaynağı pyproject.toml: CLI, /health ve OpenAPI aynı değeri gösterir."""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from src.version import PYPROJECT_PATH, UNKNOWN_VERSION, __version__, read_version
from src.web.app import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_version_is_read_from_pyproject():
    assert __version__ == read_version(PYPROJECT_PATH)
    assert __version__ != UNKNOWN_VERSION
    assert re.fullmatch(r"\d+\.\d+\.\d+([.\-+][0-9A-Za-z.\-+]+)?", __version__)


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib Python 3.11 ile geldi")
def test_version_matches_a_real_toml_parser():
    import tomllib

    with open(PYPROJECT_PATH, "rb") as f:
        assert __version__ == tomllib.load(f)["project"]["version"]


def test_only_the_project_table_counts(tmp_path):
    p = tmp_path / "pyproject.toml"
    p.write_text(
        '[tool.other]\nversion = "9.9.9"\n\n'
        '[project]\nname = "x"\nversion = "1.2.3"  # yorum\n\n'
        '[tool.later]\nversion = "8.8.8"\n',
        encoding="utf-8",
    )
    assert read_version(p) == "1.2.3"


def test_single_quoted_version(tmp_path):
    p = tmp_path / "pyproject.toml"
    p.write_text("[project]\nversion = '3.0.0-rc.1'\n", encoding="utf-8")
    assert read_version(p) == "3.0.0-rc.1"


@pytest.mark.parametrize("content", [None, "", "[project]\nname = 'x'\n", "[tool.x]\nversion = '1.0.0'\n"])
def test_missing_or_versionless_pyproject_does_not_crash(tmp_path, content):
    p = tmp_path / "pyproject.toml"
    if content is not None:
        p.write_text(content, encoding="utf-8")
    assert read_version(p) == UNKNOWN_VERSION


def test_health_and_openapi_report_the_version():
    client = TestClient(app)
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert app.version == __version__


def test_cli_version_flag():
    # Gerçek giriş noktası: argparse sürümü yazdırıp 0 ile çıkar, hiçbir iş başlatmaz
    r = subprocess.run(
        [sys.executable, "main.py", "--version"], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"SofaScore Scraper {__version__}"
