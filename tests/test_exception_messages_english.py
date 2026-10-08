"""
İstemcinin hata sınıflarının iletileri İngilizcedir (FX-26, V2): API ve CLI hatalarında, işin `error.message`ında
ve günlükte kullanıcıya ulaşırlar. Canlı doğrulamada "Not found: … (Durum Kodu: 404)" görülmüştü.
"""
from __future__ import annotations

import ast
import errno
import re
from pathlib import Path

from sofascore_scraper import exceptions as ex

MODULE = Path(__file__).resolve().parents[1] / "sofascore_scraper" / "exceptions.py"
TURKISH = re.compile(r"[çğıöşüÇĞİÖŞÜ]")


def test_no_turkish_text_in_any_message_literal() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body
                  and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
    found = [repr(node.value) for node in ast.walk(tree)
             if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
             and TURKISH.search(node.value)]
    assert found == []


def test_api_error_status_suffix_is_english() -> None:
    assert str(ex.ResourceNotFoundError("Not found: https://x.test/a")) == \
        "Not found: https://x.test/a (status code 404)"
    assert str(ex.APIError("HTTP 500 Server Error: u", status_code=500)) == "HTTP 500 Server Error: u (status code 500)"
    assert str(ex.APIError("plain")) == "plain"


def test_default_messages_are_english() -> None:
    assert str(ex.SofaScoreScraperError()) == "An error occurred in SofaScore Scraper"
    assert str(ex.ConfigError()) == "Configuration error"
    assert str(ex.RateLimitError(wait_time=30, url="https://x.test/b")) == \
        "Rate limited by the API: https://x.test/b, waiting 30 s (status code 429)"
    assert str(ex.DataNotFoundError("Season", "17")) == "Season not found: 17"
    assert str(ex.DataParsingError()) == "The data could not be parsed"
    assert str(ex.NetworkError()) == "A network error occurred"
    assert str(ex.ValidationError("name")) == "name: validation error"
    assert str(ex.CircuitOpenError("https://x.test/c")) == "Circuit breaker open, request not sent: https://x.test/c"


def test_storage_error_from_os_error_is_english() -> None:
    err = ex.StorageError.from_exception(OSError(errno.ENOSPC, "No space left on device", "/data/x.json"))
    assert str(err) == "Data could not be written to disk (No space left on device): /data/x.json"
    assert err.fatal and err.path == "/data/x.json"
