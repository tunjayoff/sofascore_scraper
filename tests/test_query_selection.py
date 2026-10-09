"""
Okuma yüzleri planlayıcının seçimini kullanır (plan maddeleri P27 ve P28; P30'da kapandı).

  * `GET /events` dilim özetinin `selected`'ı ve `GET /events/{id}/slices`'ın `not_requested` yer tutucuları
    yapılandırmanın ve takip tablosunun seçimine bakar (`planning.configured_policy(store)`), kayıt defterinin
    varsayılanına değil;
  * alt anahtarlı dilimin (bahis oranları) yer tutucusu yapılandırılan sağlayıcının alt anahtarını taşır;
  * `required_detail_keys`, kapsam raporu ve sezon sayıları da aynı seçimle sayar.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from sofascore_scraper import sports
from sofascore_scraper.config.settings import FollowSpec
from sofascore_scraper.services.query import QueryService, required_detail_keys
from sofascore_scraper.services.status import StatusService
from sofascore_scraper.slices import SLICE_OK, Outcome
from sofascore_scraper.store import Store, open_store
from test_sport_slices import _BODIES, _finished_event

EVENT_ID = 9810001
TOURNAMENT_ID = 77  # _finished_event'in turnuvası
STORED = ("statistics", "team_streaks", "pregame_form", "h2h", "incidents")  # kadro (lineups) yok


@pytest.fixture
def store(tmp_path: Path) -> Store:
    store = open_store(tmp_path / "data")
    outcomes = {"event": Outcome(SLICE_OK, _finished_event(EVENT_ID, "football"))}
    outcomes.update({key: Outcome(SLICE_OK, _BODIES[key]) for key in STORED})
    store.events.put(EVENT_ID, outcomes)
    return store


def _pairs(store: Store) -> List[Tuple[str, str, str]]:
    found = QueryService(store).event_slices(EVENT_ID)
    assert found is not None
    return [(item.key, item.sub or "", item.state) for item in found]


def _summary(store: Store) -> Dict[str, int]:
    page = QueryService(store).events(slices_summary=True)
    assert page.slices is not None
    summary = page.slices[EVENT_ID]
    return {"selected": summary.selected, "ok": summary.ok}


def test_the_registry_defaults_without_a_configuration(store: Store) -> None:
    pairs = _pairs(store)
    assert ("lineups", "", "not_requested") in pairs
    assert not [key for key, _sub, _state in pairs if key.startswith("odds")]
    assert _summary(store) == {"selected": 6, "ok": 5}
    # Yapılandırma yokken kayıt defterinin kuralı (`slices_for(spor, required_only=True)`) ile aynı
    assert required_detail_keys()["football"] == tuple(
        spec.key for spec in sports.slices_for("football", required_only=True))
    coverage = StatusService(store).coverage()
    assert (coverage.matches, coverage.complete, dict(coverage.missing)) == (1, 0, {"lineups": 1})


def test_a_slice_the_configuration_disables_is_neither_a_placeholder_nor_missing(
        store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOFASCORE_SLICES", json.dumps({"football": {"disable": ["lineups"]}}))

    assert "lineups" not in [key for key, _sub, _state in _pairs(store)]
    assert _summary(store) == {"selected": 5, "ok": 5}
    assert "lineups" not in required_detail_keys()["football"]
    coverage = StatusService(store).coverage()
    assert (coverage.matches, coverage.complete, dict(coverage.missing)) == (1, 1, {})
    (season,) = StatusService(store).season_counts(TOURNAMENT_ID)
    assert (season.finished_details, season.complete, dict(season.missing)) == (1, 1, {})
    assert QueryService(store).event_slice(EVENT_ID, "lineups") is None  # seçilmemiş ve satırı yok


def test_odds_placeholders_carry_the_configured_provider(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """P28: alt anahtarlı dilimin yer tutucusu sağlayıcının alt anahtarıyla; boş alt anahtarlı yer tutucu yok."""
    monkeypatch.setenv("SOFASCORE_DEFAULTS__SLICES", json.dumps(["core", "odds"]))
    monkeypatch.setenv("SOFASCORE_CLIENT__ODDS_PROVIDER", "5")

    odds = [(key, sub, state) for key, sub, state in _pairs(store) if key in (
        "odds_featured", "odds_all", "odds_changes", "winning_odds")]
    assert odds and all(sub == "5" and state == "not_requested" for _key, sub, state in odds), odds
    query = QueryService(store)
    found = query.event_slice(EVENT_ID, "odds_featured", "5")
    assert found is not None and found.state == "not_requested"
    assert query.event_slice(EVENT_ID, "odds_featured", "1") is None
    assert query.event_slice(EVENT_ID, "odds_featured") is None


def test_a_follow_of_the_table_selects_for_its_events(store: Store) -> None:
    """API'den eklenen takibin seçimi (takip tablosu) de sayılır: yapılandırmada olmayan seçim."""
    store.follows.add(FollowSpec(kind="tournament", entity_id=TOURNAMENT_ID, name="Cup",
                                 slices={"disable": ["lineups"]}))

    assert "lineups" not in [key for key, _sub, _state in _pairs(store)]
    assert _summary(store) == {"selected": 5, "ok": 5}
    coverage = StatusService(store).coverage()
    assert (coverage.complete, dict(coverage.missing)) == (1, {})
    (season,) = StatusService(store).season_counts(TOURNAMENT_ID)
    assert (season.complete, dict(season.missing)) == (1, {})
    # Takip tablosu sporun seçimini değiştirmez: required_detail_keys yapılandırmanınkini verir
    assert "lineups" in required_detail_keys()["football"]
