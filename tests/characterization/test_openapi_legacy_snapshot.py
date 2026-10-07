"""
Karakterizasyon: bugünkü web API'sinin OpenAPI belgesi, "eski sözleşme" olarak (plan maddesi G-04).

`tests/snapshots/openapi-legacy.json`, `app.openapi()` çıktısının kaydıdır: her yol, yöntem, parametre, istek
ve yanıt şeması. API v1 (P20) ve eski yolların uyarlayıcıları (P21) bu belgeyi değiştirmeden geçmeli ya da her
farkı tek tek açıklamalıdır. Kayıt güvenlik sıkılaştırmasından (#43) sonra alınmıştır, yani 2.0'ın yol
listesinden bilerek ayrılır: uzak lig araması POST'tur (GET yok), `GET /api/export/csv` yalnızca indirir ve
`POST /api/export/csv` üretir, `/api/auth`, `/api/auth/login` ve `/api/auth/logout` vardır. v1'in takma ad
olarak sunmayı sürdüreceği yüzey budur. Sözleşme bilerek değiştirildiyse dosya şöyle yeniden üretilir:

    REGEN_OPENAPI_SNAPSHOT=1 python -m pytest tests/characterization/test_openapi_legacy_snapshot.py

Kayıt yalnızca eski sözleşmeyi kapsar: `/api/v1` altındaki yollar ve yalnızca onların andığı şemalar
karşılaştırmaya girmez (`_legacy_view`). Bugün böyle bir yol yok, yani kayıt belgenin tamamıdır; v1 geldiğinde
onun kendi kaydı olur (`docs/api/openapi-v1.json`) ve v1'e eklenen bir yol bu dosyayı değiştirmez.

Karşılaştırma ayrıştırılmış JSON üzerindedir: nesnelerde anahtar sırası sayılmaz, listelerde (`required`,
`enum`, `parameters`, `anyOf`) sıra sayılır. Tek maskelenen alan `info.version`'dır: uygulama sürümü
(pyproject.toml) her yayında değişir ve sözleşmenin parçası değildir; belgenin sürümü gerçekten taşıdığını
`document` fixture'ı denetler.

Belgeyi FastAPI ve pydantic üretir: yanıt gövdeleri aynı kalsa da bu paketlerin sürümü değişince belge
değişebilir. Kayıt `constraints.txt`'deki sürümlerle alınmıştır; fark çıktığında kurulu sürümler de yazılır.
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Set

import fastapi
import pydantic
import pytest
from fastapi.testclient import TestClient

from sofascore_scraper.version import __version__
from sofascore_scraper.web.app import app

ROOT = Path(__file__).resolve().parent.parent.parent
SNAPSHOT = ROOT / "tests" / "snapshots" / "openapi-legacy.json"
REGENERATE = os.getenv("REGEN_OPENAPI_SNAPSHOT") == "1"
VERSION_PLACEHOLDER = "<app version>"
V1_PREFIX = "/api/v1"
SCHEMA_REF = "#/components/schemas/"
MAX_SHOWN = 40


def _legacy_view(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Belgenin eski sözleşmeye ait kısmı: `/api/v1` dışındaki yollar ve onların (dolaylı da olsa) andığı
    şemalar; `info.version` maskelenir. Verilen belge değiştirilmez.
    """
    view = copy.deepcopy(doc)
    view["info"]["version"] = VERSION_PLACEHOLDER
    paths = view.get("paths", {})
    view["paths"] = {p: ops for p, ops in paths.items() if p != V1_PREFIX and not p.startswith(V1_PREFIX + "/")}
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
    view["components"]["schemas"] = {name: schema for name, schema in schemas.items() if name in used}
    return view


@pytest.fixture
def document(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """Şu anki rotalardan taze üretilen belgenin (FastAPI'nin önbelleği atlanır) eski sözleşme görünümü."""
    monkeypatch.setattr(app, "openapi_schema", None)
    doc = app.openapi()
    assert doc["info"]["version"] == __version__, "belge uygulama sürümünü (pyproject.toml) taşımalı"
    return _legacy_view(doc)


def _differences(expected: Any, actual: Any, path: str = "$") -> List[str]:
    """Farklı yollar. Nesnelerde anahtar sırası sayılmaz; tür ve liste sırası sayılır."""
    if type(expected) is not type(actual):
        return [f"{path}: {expected!r} ({type(expected).__name__}) -> {actual!r} ({type(actual).__name__})"]
    if isinstance(expected, dict):
        out = [f"{path}: removed {key!r}" for key in expected if key not in actual]
        out += [f"{path}: added {key!r}" for key in actual if key not in expected]
        for key in expected:
            if key in actual:
                out.extend(_differences(expected[key], actual[key], f"{path} > {key}"))
        return out
    if isinstance(expected, list):
        out = [f"{path}: length {len(expected)} -> {len(actual)}"] if len(expected) != len(actual) else []
        for i in range(min(len(expected), len(actual))):
            out.extend(_differences(expected[i], actual[i], f"{path}[{i}]"))
        return out
    return [] if expected == actual else [f"{path}: {expected!r} -> {actual!r}"]


def _generator_versions() -> str:
    """Kurulu ve `constraints.txt`'de sabitlenmiş FastAPI/pydantic sürümleri (fark iletisinin son satırı)."""
    try:
        constraints = (ROOT / "constraints.txt").read_text(encoding="utf-8")
    except OSError:
        constraints = ""
    pins = dict(re.findall(r"(?m)^(fastapi|pydantic)==([^\s;]+)", constraints))
    installed = {"fastapi": fastapi.__version__, "pydantic": pydantic.VERSION}
    return ", ".join(f"{name} {ver} (constraints.txt: {pins.get(name, '?')})" for name, ver in installed.items())


def test_differences_ignore_key_order_but_not_types_or_list_order() -> None:
    assert not _differences({"a": 1, "b": [1, 2]}, {"b": [1, 2], "a": 1})
    assert _differences({"a": [1, 2]}, {"a": [2, 1]}) == ["$ > a[0]: 1 -> 2", "$ > a[1]: 2 -> 1"]
    assert _differences({"a": 1}, {"a": True}) == ["$ > a: 1 (int) -> True (bool)"]
    assert _differences({"a": 1, "b": 2}, {"b": 2, "c": 3}) == ["$: removed 'a'", "$: added 'c'"]
    assert _differences([1], [1, 2]) == ["$: length 1 -> 2"]


def test_legacy_view_drops_v1_paths_and_the_schemas_only_they_use() -> None:
    def ref(name: str) -> Dict[str, str]:
        return {"$ref": SCHEMA_REF + name}

    doc = {
        "info": {"version": "9.9.9"},
        "paths": {
            "/api/leagues": {"get": {"responses": {"200": {"schema": {"items": ref("League")}}}}},
            "/api/v1": {"get": {"responses": {"200": {"schema": ref("Meta")}}}},
            "/api/v1/events": {"get": {"responses": {"200": {"schema": ref("Event")}, "422": {"schema": ref("Err")}}}},
            "/api/v10": {"get": {"responses": {"422": {"schema": ref("Err")}}}},
        },
        "components": {"schemas": {
            "League": {"properties": {"sport": {"anyOf": [ref("Sport"), {"type": "null"}]}}},
            "Sport": {"type": "string"},
            "Err": {"properties": {"cause": ref("Err")}},
            "Event": {"type": "object"},
            "Meta": {"type": "object"},
        }},
    }
    before = copy.deepcopy(doc)
    view = _legacy_view(doc)
    assert doc == before
    assert list(view["paths"]) == ["/api/leagues", "/api/v10"]
    assert list(view["components"]["schemas"]) == ["League", "Sport", "Err"]
    assert view["info"] == {"version": VERSION_PLACEHOLDER}
    bare = {"info": {"version": "1"}, "paths": {}}
    assert _legacy_view(bare) == {"info": {"version": VERSION_PLACEHOLDER}, "paths": {}}


def test_openapi_document_matches_the_legacy_snapshot(document: Dict[str, Any]) -> None:
    if REGENERATE:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        SNAPSHOT.write_bytes(text.encode("utf-8"))  # her platformda LF
        return
    assert SNAPSHOT.is_file(), f"{SNAPSHOT.name} yok: REGEN_OPENAPI_SNAPSHOT=1 ile üretin"
    diffs = _differences(json.loads(SNAPSHOT.read_text(encoding="utf-8")), document)
    if diffs:
        shown = "\n  ".join(diffs[:MAX_SHOWN])
        more = f"\n  … ve {len(diffs) - MAX_SHOWN} fark daha" if len(diffs) > MAX_SHOWN else ""
        pytest.fail(
            f"{SNAPSHOT.name}: {len(diffs)} fark (kayıt -> şimdiki)\n  {shown}{more}\n"
            f"Kurulu: {_generator_versions()}. Değişiklik bilerek yapıldıysa: REGEN_OPENAPI_SNAPSHOT=1",
            pytrace=False,
        )


def test_openapi_json_route_serves_the_document(document: Dict[str, Any]) -> None:
    """Belge `/openapi.json`'da sunulur; SPA'nın her yolu yakalayan rotası onu gölgelemez."""
    response = TestClient(app).get("/openapi.json")
    assert response.status_code == 200
    assert _legacy_view(response.json()) == document
