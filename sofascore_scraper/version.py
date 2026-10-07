"""
Uygulama sürümü için tek kaynak: pyproject.toml içindeki [project].version.

CLI (--version), web sunucusu (/health, OpenAPI) ve yayın iş akışı aynı değeri buradan
okur; sürüm başka hiçbir dosyaya elle yazılmaz. Proje bir paket olarak kurulmadığı için
importlib.metadata kullanılamaz; pyproject.toml uygulamayla birlikte dağıtılır (kaynak
arşivi ve Docker imajı dahil).
"""

from __future__ import annotations

import re
from pathlib import Path

PYPROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"
# pyproject.toml okunamazsa (eksik/bozuk dağıtım) uygulama yine açılır; sürüm bilinmiyor görünür
UNKNOWN_VERSION = "0.0.0+unknown"

_TABLE_RE = re.compile(r"^\s*\[+\s*([^\]]+?)\s*\]+\s*(?:#.*)?$")
_VERSION_RE = re.compile(r"""^\s*version\s*=\s*(["'])(?P<version>[^"']+)\1\s*(?:#.*)?$""")


def read_version(pyproject: "str | Path" = PYPROJECT_PATH) -> str:
    """
    pyproject.toml'daki [project] tablosunun version değerini döndürür.

    tomllib Python 3.11 ile geldi; desteklenen en düşük sürüm 3.10 olduğu için tablo
    başlıkları ve version satırı elle okunur (her Python sürümünde aynı kod yolu).
    """
    try:
        text = Path(pyproject).read_text(encoding="utf-8")
    except OSError:
        return UNKNOWN_VERSION

    in_project = False
    for line in text.splitlines():
        table = _TABLE_RE.match(line)
        if table:
            in_project = table.group(1) == "project"
            continue
        if in_project:
            m = _VERSION_RE.match(line)
            if m:
                return m.group("version")
    return UNKNOWN_VERSION


__version__ = read_version()
