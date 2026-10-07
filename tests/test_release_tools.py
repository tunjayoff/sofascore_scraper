"""Yayın yardımcıları: etiket ↔ sürüm kontrolü ve CHANGELOG bölümü (ağ yok, etiket oluşturulmaz)."""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys

import pytest

from sofascore_scraper.version import __version__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "release.py")

_spec = importlib.util.spec_from_file_location("release_tools", SCRIPT)
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

CHANGELOG = """# Changelog

Intro text.

## [Unreleased]

### Added

- Something not released yet.

## [2.1.0] - 2026-10-05

### Added

- Docker image.

### Fixed

- A bug (#12).

## [2.0.0] - 2026-07-27

- First tagged release.

## Earlier history

Not part of any version.

[Unreleased]: https://example.invalid/compare/v2.1.0...HEAD
[2.1.0]: https://example.invalid/compare/v2.0.0...v2.1.0
"""


def test_check_tag_accepts_only_v_plus_version():
    release.check_tag("v2.1.0", "2.1.0")
    for bad in ("2.1.0", "v2.1", "v2.1.1", "V2.1.0", "v2.1.0-rc.1", "release-2.1.0", ""):
        with pytest.raises(release.ReleaseError):
            release.check_tag(bad, "2.1.0")


def test_section_of_a_version_stops_at_the_next_one():
    assert release.changelog_section(CHANGELOG, "2.1.0") == (
        "### Added\n\n- Docker image.\n\n### Fixed\n\n- A bug (#12).\n"
    )


def test_last_version_section_excludes_other_headings_and_link_definitions():
    assert release.changelog_section(CHANGELOG, "2.0.0") == "- First tagged release.\n"


def test_unreleased_section_can_be_read_too():
    assert release.changelog_section(CHANGELOG, "Unreleased") == "### Added\n\n- Something not released yet.\n"


def test_missing_or_empty_section_stops_the_release():
    with pytest.raises(release.ReleaseError, match="no '## \\[9.9.9\\]' section"):
        release.changelog_section(CHANGELOG, "9.9.9")
    # "2.1" 2.1.0 bölümüyle eşleşmemeli
    with pytest.raises(release.ReleaseError):
        release.changelog_section(CHANGELOG, "2.1")
    with pytest.raises(release.ReleaseError, match="empty"):
        release.changelog_section("## [1.0.0] - 2026-01-01\n\n## [0.9.0]\n\n- x\n", "1.0.0")


def test_project_version_fails_without_a_version(tmp_path):
    p = tmp_path / "pyproject.toml"
    p.write_text("[project]\nname = 'x'\n", encoding="utf-8")
    with pytest.raises(release.ReleaseError):
        release.project_version(p)
    assert release.project_version() == __version__


def test_repository_changelog_is_parseable():
    """Gerçek CHANGELOG.md: Unreleased bölümü var ve Keep a Changelog başlıklarını taşıyor."""
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        text = f.read()
    names = [m.group("name") for line in text.splitlines() if (m := release._HEADING_RE.match(line))]
    assert names and names[0] == "Unreleased"
    # Bir sürüm yayımlandıktan sonra en üstteki bölüm boş kalabilir; boş değilse başlıkları geçerli olmalı
    try:
        body = release.changelog_section(text, names[0])
    except release.ReleaseError:
        body = release.changelog_section(text, names[1])
    kinds = [line[4:].strip() for line in body.splitlines() if line.startswith("### ")]
    assert kinds
    assert set(kinds) <= {"Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"}


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, SCRIPT, *args], cwd=ROOT, capture_output=True, text=True, timeout=60)


def test_cli_version_and_check_tag():
    r = _run("version")
    assert (r.returncode, r.stdout.strip()) == (0, __version__)

    assert _run("check-tag", f"v{__version__}").returncode == 0

    r = _run("check-tag", "v0.0.1-not-this")
    assert r.returncode == 1
    assert "does not match" in r.stderr


def test_cli_notes_fails_for_an_unknown_version():
    r = _run("notes", "0.0.0-nope")
    assert r.returncode == 1
    assert r.stdout == ""
    assert "CHANGELOG.md has no" in r.stderr


def _workflow(name: str) -> str:
    with open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8") as f:
        return f.read()


def test_release_workflow_permissions_are_minimal():
    text = _workflow("release.yml")
    # Üst düzey: salt okunur
    top = re.search(r"^permissions:\n((?:[ \t]+.*\n)+)", text, flags=re.M)
    assert top and top.group(1).split() == ["contents:", "read"]
    # Yazma izinleri tek bir işte ve yalnızca bu ikisi
    assert len(re.findall(r"^\s+contents: write\b", text, flags=re.M)) == 1
    assert len(re.findall(r"^\s+packages: write\b", text, flags=re.M)) == 1
    writes = set(re.findall(r"^\s+([a-z-]+): write\b", text, flags=re.M))
    assert writes == {"contents", "packages"}
    publish = text[text.index("\n  publish:"):]
    assert "contents: write" in publish and "packages: write" in publish
    # Yalnızca v* etiketleri tetikler; iş akışı etiketi kendisi oluşturmaz
    assert re.search(r'^on:\n  push:\n    tags: \["v\*"\]\n', text, flags=re.M)
    assert "git tag" not in text and "git push" not in text
    # CI ile aynı kontroller: CI iş akışının kendisi çağrılır
    assert "uses: ./.github/workflows/ci.yml" in text
    assert re.search(r"^  workflow_call:", _workflow("ci.yml"), flags=re.M)


def test_image_smoke_test_is_offline_and_used_by_the_release():
    """Yayın, imajı göndermeden önce duman testini çalıştırır; test hiçbir ağa çıkamaz."""
    path = os.path.join(ROOT, "docker", "smoke-test.sh")
    with open(path, "rb") as f:
        raw = f.read()
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    # Konteyner başlatan her satır ağsız çalışır
    runs = [ln for ln in code if re.search(r"\bdocker run\b", ln)]
    assert runs and all("--network none" in ln for ln in runs)
    assert "sofascore.com" not in "\n".join(code)

    release = _workflow("release.yml")
    assert "docker/smoke-test.sh" in release
    assert release.index("docker/smoke-test.sh") < release.index("push: true"), "smoke test must run before the push"

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash yok")
    r = subprocess.run([bash, "-n", path], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
