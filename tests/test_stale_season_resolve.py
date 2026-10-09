"""Stale SofaScore season ids must resolve onto the refreshed list (services/sync.py `resolve_season_id`)."""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from sofascore_scraper.services import sync


@pytest.fixture
def stored(monkeypatch: pytest.MonkeyPatch) -> Dict[int, List[Dict[str, Any]]]:
    """Ligin saklanan sezon listesi: katalog yerine bu sözlükten okunur."""
    lists: Dict[int, List[Dict[str, Any]]] = {}
    monkeypatch.setattr(sync, "stored_seasons", lambda _ctx, lid: lists.get(int(lid), []))
    return lists


SEASONS = [
    {"id": 97436, "year": "26/27", "name": "Liga Portugal 26/27"},
    {"id": 77806, "year": "25/26", "name": "Liga Portugal 25/26"},
]


def test_resolve_keeps_valid_id(stored: Dict[int, List[Dict[str, Any]]]) -> None:
    stored[238] = SEASONS
    assert sync.resolve_season_id(MagicMock(), 238, 77806) == 77806


def test_resolve_replaces_stale_id(stored: Dict[int, List[Dict[str, Any]]]) -> None:
    stored[238] = SEASONS
    # Old id no longer published by SofaScore → 2nd-newest (25/26)
    assert sync.resolve_season_id(MagicMock(), 238, 77559) == 77806
