"""
Arama sonucunun sporu (FX-26, canlı doğrulama M16): "Benoit Sinner · Fransa" sporsuz göründü. Takım ve oyuncu
sonucunun sporu varlığın ya da oyuncunun takımının `sport`'undan okunuyordu; artık SofaScore'un başka alanlarından
da (kategori, ana turnuva, sonucun kendisi). Hiçbirinde yoksa spor boş kalır; sonuç yine gösterilir.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from sofascore_scraper.services.follows import _search_hit

TENNIS = {"id": 5, "slug": "tennis", "name": "Tennis"}


def hit_sport(item: Dict[str, Any]) -> Optional[str]:
    found = _search_hit(item, set(), typed=True)
    assert found is not None
    return found.sport


@pytest.mark.parametrize("item", [
    {"type": "player", "entity": {"id": 1, "name": "A", "sport": TENNIS}},
    {"type": "player", "entity": {"id": 1, "name": "A", "team": {"id": 9, "name": "T", "sport": TENNIS}}},
    {"type": "player", "entity": {"id": 1, "name": "A", "team": {"id": 9, "name": "T", "category": {"sport": TENNIS}}}},
    {"type": "player", "entity": {"id": 1, "name": "A", "category": {"sport": TENNIS}}},
    {"type": "team", "entity": {"id": 1, "name": "A", "primaryUniqueTournament": {"category": {"sport": TENNIS}}}},
    {"type": "player", "entity": {"id": 1, "name": "A", "team": {"id": 9, "tournament": {"category": {"sport": TENNIS}}}}},
    {"type": "player", "sport": TENNIS, "entity": {"id": 1, "name": "A"}},
])
def test_the_sport_is_found_where_sofascore_puts_it(item: Dict[str, Any]) -> None:
    assert hit_sport(item) == "tennis"


def test_a_hit_without_any_sport_is_kept_without_one() -> None:
    item = {"type": "player", "entity": {"id": 7, "name": "Benoit Sinner", "country": {"alpha2": "FR", "name": "France"}}}
    found = _search_hit(item, set(), typed=True)
    assert found is not None and (found.name, found.sport, found.country_code) == ("Benoit Sinner", None, "FR")


def test_the_entity_s_own_sport_wins() -> None:
    football = {"id": 1, "slug": "football", "name": "Football"}
    item = {"type": "player", "entity": {"id": 1, "name": "A", "team": {"id": 2, "name": "T", "sport": football},
                                         "category": {"sport": TENNIS}}}
    assert hit_sport(item) == "football"
