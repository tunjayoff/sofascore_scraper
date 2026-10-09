"""
Store'un hata iletileri İngilizcedir (FX-25): CLI hatasında, işin `error.message`ında, günlükte ve v1 hata
gövdesinin `details.store_message` / `reason` alanlarında kullanıcıya ulaşırlar.

İstisna: kodun kendi yanlış kullanımını bildiren ValueError'lar (sabit argümanlarla çağrılır, kullanıcıya
ulaşmaz; v1 onları `internal` yapar ve metni göndermez) aşağıda adlarıyla bırakıldı.
"""
from __future__ import annotations

import ast
import errno
import re
from pathlib import Path

import pytest

from sofascore_scraper.store import errors as store_errors
from sofascore_scraper.store import manifest

STORE = Path(__file__).resolve().parents[1] / "sofascore_scraper" / "store"
TURKISH = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
# İçe dönük: programlama hatası (sabit argümanlar), kullanıcıya ulaşmaz
INTERNAL_ONLY = {
    ("catalog.py", "ValueError"),
    ("derive.py", "ValueError"),
    ("indexer.py", "ValueError"),
    ("sqlite.py", "ValueError"),
}
_ERROR_NAME = re.compile(r"(Error|Corrupt|Missing|Held|Busy|Exists|Managed|TooNew|Invalid|Refused|NotFound|Event)$")


def _call_name(node: ast.Call) -> str:
    func = node.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


def _turkish_messages() -> list[str]:
    found = []
    for path in sorted(STORE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            # hata kurucuları ve iletiye giren parçalar (manifest sorunları, kilit sahibinin bilgisi); taramaların
            # ve doğrulamanın sorun listeleri (verify, legacy, changes) istisna iletisi değildir, burada denetlenmez
            in_message = path.name in ("manifest.py", "lease.py") and name in ("bad", "append")
            if not (_ERROR_NAME.search(name) or in_message):
                continue
            if (path.name, name) in INTERNAL_ONLY:
                continue
            for part in ast.walk(node):
                if isinstance(part, ast.Constant) and isinstance(part.value, str) and TURKISH.search(part.value):
                    found.append(f"{path.name}:{node.lineno} {name}: {part.value!r}")
    return found


def test_store_error_messages_are_english() -> None:
    assert _turkish_messages() == []


@pytest.mark.parametrize("cls", [getattr(store_errors, name) for name in store_errors.__all__])
def test_default_messages_and_disk_errors_are_english(cls: type) -> None:
    assert not TURKISH.search(cls.default_message)
    written = cls.from_exception(OSError(errno.ENOSPC, "No space left on device", "/data/x"))
    read = cls.from_exception(OSError(errno.EIO, "Input/output error", "/data/x"), reading=True)
    assert str(written).startswith("Data could not be written to disk (No space left on device)")
    assert str(read).startswith("Data could not be read from disk (Input/output error)")


def test_schema_too_new_and_an_invalid_manifest_say_it_in_english() -> None:
    too_new = store_errors.SchemaTooNew(path="/data/.meta/schema.json", component="layout", found=9, supported=3)
    assert str(too_new) == "The data directory was written by a newer version (layout: 9, supported: 3): /data/.meta/schema.json"
    with pytest.raises(store_errors.PayloadCorrupt) as caught:
        manifest.from_dict({"format": 1, "kind": "nope", "id": None, "slices": []}, "/data/m.json")
    assert not TURKISH.search(str(caught.value))
    assert "invalid kind 'nope'" in str(caught.value)


# --- doğrulama ve taramaların sorun metinleri (B2) ---------------------------------------------------------

# Türkçe harf taşıyabilen, metin olmayan sabitler: 2.x'in CSV sütun adları (veri) ve arama katlamasının tablosu
NOT_TEXT = {
    ("legacy.py", "Sezon Adı"), ("legacy.py", "Sezon Yılı"), ("legacy.py", "Liga Adı"), ("derive.py", "ı"),
}


def _docstrings(tree: ast.AST) -> set:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def test_issue_and_problem_texts_are_english() -> None:
    """
    Doğrulamanın (`verify`) sorunları, taramaların (eski düzen, değişiklik günlüğü, varlıklar, dizinleyici)
    sorun kayıtları ve iç hata iletileri İngilizcedir: kodlarıyla birlikte CLI'ın JSON çıktısına, günlüğe ve
    raporlara girerler. Okura gösterilen açıklama yerelleştirme anahtarından gelir (`ssc_catalog_kind_<kod>`).
    Belge dizileri ve yorumlar Türkçe kalır.
    """
    found = []
    for path in sorted(STORE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstrings(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs
                    and TURKISH.search(node.value) and (path.name, node.value) not in NOT_TEXT):
                found.append(f"{path.name}:{node.lineno}: {node.value!r}")
    assert found == []


def test_every_issue_and_problem_kind_has_a_description_in_both_languages() -> None:
    import json

    from sofascore_scraper.store import changes, indexer, legacy, verify

    kinds = {value for name, value in vars(verify).items() if name.startswith("KIND_")}
    problems = {value for module in (legacy, indexer, changes)
                for name, value in vars(module).items() if name.startswith("PROBLEM_")}
    root = STORE.parents[1] / "locales"
    for language in ("en", "tr"):
        texts = json.loads((root / f"{language}.json").read_text(encoding="utf-8"))
        assert sorted(k for k in kinds if f"ssc_catalog_kind_{k}" not in texts) == [], language
        assert sorted(p for p in problems if f"ssc_catalog_problem_{p}" not in texts) == [], language
