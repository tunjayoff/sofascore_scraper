"""
API v1'in OpenAPI belgesi: `docs/api/openapi-v1.json` (docs/design/02-services.md bölüm 6).

Dosya depoya işlenir ve sözleşmenin kaydıdır: `tests/test_openapi_snapshot.py` onu uygulamanın ürettiği
belgeyle karşılaştırır ve bildirilmemiş her değişiklikte düşer; ön yüzün türleri bu dosyadan üretilir.

    python -m sofascore_scraper.web.openapi            belgeyi standart çıktıya yazar
    python -m sofascore_scraper.web.openapi --write    docs/api/openapi-v1.json'ı yeniden üretir
    python -m sofascore_scraper.web.openapi --check    dosya güncel değilse 1 ile çıkar (farkları yazar)

Belge, uygulamanın tam belgesinin (`app.openapi()`) v1 görünümüdür: yalnızca `/api/v1` altındaki yollar ve
onların (dolaylı da olsa) andığı şemalar. Eski yolların kaydı ayrıdır (tests/snapshots/openapi-legacy.json).
`info.version` API'nin sürümüdür ("1"), uygulamanın sürümü değil: uygulama her yayında değişir, sözleşme
değişmedikçe bu dosya değişmez.

Program olarak çalıştırıldığında uygulama geçici bir dizinle yüklenir (`_isolate`): belge yerel ayarlardan
bağımsızdır, uygulamayı içe aktarmak ise veri dizinini, lig dosyasını ve log dosyasını oluşturur. Böylece
komut depo kopyasında dosya bırakmaz, kullanıcının veri dizinini açmaz ve bozuk bir yerel yapılandırma
dosyası yüzünden düşmez. Log satırları stderr'e yazılır; standart çıktıda yalnızca belge vardır.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from sofascore_scraper.web.api import is_v1

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = ROOT / "docs" / "api" / "openapi-v1.json"
SCHEMA_REF = "#/components/schemas/"
TITLE = "SofaScore Scraper API"
API_VERSION = "1"
DESCRIPTION = (
    "Versioned HTTP API of the SofaScore scraper. Single resources are returned as {\"data\": {...}}, "
    "collections as {\"data\": [...], \"page\": {\"limit\", \"next_cursor\"}}, errors as {\"error\": {\"code\", "
    "\"message\", \"details\", \"request_id\"}}. Messages are English; clients translate by code. When an access "
    "token is configured, every request needs Authorization: Bearer <token> or the session cookie."
)


def v1_view(document: Dict[str, Any]) -> Dict[str, Any]:
    """Tam belgenin v1 görünümü: `/api/v1` yolları ve andıkları şemalar. Verilen belge değiştirilmez."""
    view = copy.deepcopy(document)
    view["info"] = {"title": TITLE, "description": DESCRIPTION, "version": API_VERSION}
    view["paths"] = {path: operations for path, operations in view.get("paths", {}).items() if is_v1(path)}
    schemas = view.get("components", {}).get("schemas")
    if schemas is None:
        return view
    used: Set[str] = set()
    queue: List[Any] = [view["paths"]]
    while queue:
        node = queue.pop()
        if isinstance(node, list):
            queue.extend(node)
        elif isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith(SCHEMA_REF) and ref[len(SCHEMA_REF):] not in used:
                used.add(ref[len(SCHEMA_REF):])
                queue.append(schemas.get(ref[len(SCHEMA_REF):]))
            queue.extend(node.values())
    view["components"]["schemas"] = {name: schema for name, schema in sorted(schemas.items()) if name in used}
    return view


def v1_document() -> Dict[str, Any]:
    """Uygulamanın şu anki rotalarından taze üretilen v1 belgesi (FastAPI'nin önbelleği kullanılmaz)."""
    from sofascore_scraper.web.app import app

    cached = app.openapi_schema
    app.openapi_schema = None
    try:
        return v1_view(app.openapi())
    finally:
        app.openapi_schema = cached


def render(document: Dict[str, Any]) -> str:
    """Dosyaya yazılan metin: girintili JSON, sonunda tek satır sonu (her platformda LF)."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def differences(expected: Any, actual: Any, path: str = "$") -> List[str]:
    """İki belge arasındaki farklı yollar. Nesnelerde anahtar sırası sayılmaz; tür ve liste sırası sayılır."""
    if type(expected) is not type(actual):
        return [f"{path}: {expected!r} ({type(expected).__name__}) -> {actual!r} ({type(actual).__name__})"]
    if isinstance(expected, dict):
        out = [f"{path}: removed {key!r}" for key in expected if key not in actual]
        out += [f"{path}: added {key!r}" for key in actual if key not in expected]
        for key in expected:
            if key in actual:
                out.extend(differences(expected[key], actual[key], f"{path} > {key}"))
        return out
    if isinstance(expected, list):
        out = [f"{path}: length {len(expected)} -> {len(actual)}"] if len(expected) != len(actual) else []
        for i in range(min(len(expected), len(actual))):
            out.extend(differences(expected[i], actual[i], f"{path}[{i}]"))
        return out
    return [] if expected == actual else [f"{path}: {expected!r} -> {actual!r}"]


def read_snapshot(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Depodaki kayıt (verilmezse SNAPSHOT_PATH); dosya yoksa None."""
    try:
        text = (path or SNAPSHOT_PATH).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    loaded: Dict[str, Any] = json.loads(text)
    return loaded


def write_snapshot(document: Dict[str, Any], path: Optional[Path] = None) -> None:
    """Kaydı yazar. Dizin (docs/api) depoda vardır; yoksa hata olduğu gibi çıkar."""
    (path or SNAPSHOT_PATH).write_bytes(render(document).encode("utf-8"))


# Uygulamanın içe aktarılırken okuduğu ya da oluşturduğu yerler (tests/conftest.py aynı listeyi kullanır)
_ISOLATED_PATHS = {
    "DATA_DIR": "data",
    "SOFASCORE_CONFIG_DIR": "config",
    "SOFASCORE_ENV_FILE": ".env",
    "LOG_DIR": "logs",
    "SOFASCORE_BROWSER_PROFILE": "browser-profile",
    "SOFASCORE_THROTTLE_DIR": "throttle",
}


def _isolate(directory: str) -> None:
    """
    Uygulama içe aktarılmadan önce: dizinlerini `directory` altına çevirir, yapılandırma dosyasını aratmaz ve
    erişim belirtecini ortamdan çıkarır. Yalnızca bu sürecin ortamını değiştirir. Log satırları stderr'e gider:
    standart çıktı yalnızca belgeyi taşır (`python -m sofascore_scraper.web.openapi > dosya`).
    """
    for name, relative in _ISOLATED_PATHS.items():
        os.environ[name] = os.path.join(directory, relative)
    os.environ["SOFASCORE_CONFIG"] = "none"
    os.environ.pop("SOFASCORE_API_TOKEN", None)
    # Günlükçü içe aktarılırken kurulur ve log dizinini oluşturur: ortam ondan önce hazır olmalı
    from sofascore_scraper.logger import set_console_stream

    set_console_stream("stderr")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sofascore_scraper.web.openapi", description="OpenAPI document of API v1.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="regenerate docs/api/openapi-v1.json")
    group.add_argument("--check", action="store_true", help="exit 1 when docs/api/openapi-v1.json is out of date")
    args = parser.parse_args(argv)

    if "sofascore_scraper.web.app" in sys.modules:
        document = v1_document()  # uygulama zaten yüklü (testler): olduğu gibi kullanılır
    else:
        with tempfile.TemporaryDirectory(prefix="sofascore-openapi-", ignore_cleanup_errors=True) as scratch:
            _isolate(scratch)
            document = v1_document()
    if args.write:
        write_snapshot(document)
        print(f"wrote {SNAPSHOT_PATH.relative_to(ROOT)}", file=sys.stderr)
        return 0
    if args.check:
        recorded = read_snapshot()
        if recorded is None:
            print(f"{SNAPSHOT_PATH.relative_to(ROOT)} is missing; run: python -m sofascore_scraper.web.openapi --write", file=sys.stderr)
            return 1
        found = differences(recorded, document)
        for line in found:
            print(line, file=sys.stderr)
        if found:
            print(f"{len(found)} difference(s); run: python -m sofascore_scraper.web.openapi --write", file=sys.stderr)
        return 1 if found else 0
    sys.stdout.write(render(document))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
