#!/usr/bin/env python3
"""
Yayın yardımcıları (.github/workflows/release.yml kullanır; elle de çalıştırılabilir).

    python scripts/release.py version              # pyproject.toml'daki sürüm
    python scripts/release.py check-tag v2.1.0     # etiket sürümle eşleşmiyorsa 1 ile çıkar
    python scripts/release.py notes 2.1.0          # CHANGELOG.md'deki o sürümün bölümü

Yalnızca standart kütüphane ve sofascore_scraper/version.py kullanılır: iş akışı bunu bağımlılık
kurmadan çalıştırır. Hiçbir komut ağa çıkmaz, etiket ya da yayın oluşturmaz.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sofascore_scraper.version import PYPROJECT_PATH, UNKNOWN_VERSION, read_version  # noqa: E402

CHANGELOG_PATH = ROOT / "CHANGELOG.md"

# "## [2.1.0] - 2026-10-05" ya da "## [Unreleased]"
_HEADING_RE = re.compile(r"^##\s+\[(?P<name>[^\]]+)\]")
# Dosya sonundaki bağlantı tanımları ("[2.1.0]: https://...") hiçbir bölüme ait değildir
_LINK_DEF_RE = re.compile(r"^\[[^\]]+\]:\s+\S+")


class ReleaseError(Exception):
    """Yayını durdurması gereken tutarsızlık (mesaj kullanıcıya gösterilir)."""


def project_version(pyproject: "str | Path" = PYPROJECT_PATH) -> str:
    version = read_version(pyproject)
    if version == UNKNOWN_VERSION:
        raise ReleaseError(f"no [project].version found in {pyproject}")
    return version


def check_tag(tag: str, version: str) -> None:
    """Etiket tam olarak 'v' + pyproject sürümü olmalı (v2.1.0 ↔ 2.1.0)."""
    expected = f"v{version}"
    if tag != expected:
        raise ReleaseError(
            f"tag {tag!r} does not match the version in pyproject.toml ({version}); expected tag {expected!r}"
        )


def changelog_section(text: str, version: str) -> str:
    """
    CHANGELOG.md'den verilen sürümün bölüm gövdesini (başlık satırı hariç) döndürür.
    Bölüm yoksa ya da boşsa ReleaseError: notsuz bir yayın çıkmasın.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m and m.group("name").strip() == version:
            start = i + 1
            break
    if start is None:
        raise ReleaseError(f"CHANGELOG.md has no '## [{version}]' section")

    body = []
    for line in lines[start:]:
        # Sonraki sürüm bölümü ya da başka bir ikinci düzey başlık ("## Earlier history")
        if line.startswith("## "):
            break
        if _LINK_DEF_RE.match(line):
            continue
        body.append(line)

    section = "\n".join(body).strip()
    if not section:
        raise ReleaseError(f"the '## [{version}]' section of CHANGELOG.md is empty")
    return section + "\n"


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description="Release helpers: version, tag check, changelog notes.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print the version from pyproject.toml")
    p_tag = sub.add_parser("check-tag", help="fail unless TAG is 'v' + the pyproject version")
    p_tag.add_argument("tag")
    p_notes = sub.add_parser("notes", help="print the CHANGELOG.md section of VERSION")
    p_notes.add_argument("version", nargs="?", default=None, help="default: the pyproject version")
    args = parser.parse_args(argv)

    try:
        version = project_version()
        if args.command == "version":
            print(version)
        elif args.command == "check-tag":
            check_tag(args.tag, version)
            print(f"tag {args.tag} matches version {version}")
        else:
            sys.stdout.write(changelog_section(CHANGELOG_PATH.read_text(encoding="utf-8"), args.version or version))
    except (ReleaseError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
