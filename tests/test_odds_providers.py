"""
Bahis sağlayıcılarının adları (B4; canlı doğrulama M20: arayüz sağlayıcıyı "Bahis şirketi 1" diye yazıyordu).

Kaynak, sofascore_scraper/sports.py'deki yerleşik tablodur (`ODDS_PROVIDERS`); GET /api/v1/odds/providers onu
verir, web arayüzü ve ayar açıklaması oradan okur. Fixture (tests/fixtures/odds_providers/tr_web.json) SofaScore'un
`/odds/providers/TR/web` yanıtının kayıtlı örneğidir; içindeki her adres yer tutucuyla değiştirildi. Bahis kuponu
bağlantıları (ortaklık adresleri) hiçbir yerde saklanmaz ve gösterilmez.

Ağ yok.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest
from fastapi.testclient import TestClient

from sofascore_scraper import sports
from sofascore_scraper.config.settings import ClientSettings
from sofascore_scraper.web.app import app

FIXTURE = Path(__file__).parent / "fixtures" / "odds_providers" / "tr_web.json"


def _listed(body: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """Listenin her sağlayıcı nesnesi: girdinin kendisi, oranlarının kaynağı ve yedeği."""
    for entry in body["providers"]:
        provider = entry["provider"]
        yield provider
        for key in ("oddsFrom", "liveOddsFrom"):
            if isinstance(provider.get(key), dict):
                yield provider[key]
        if isinstance(entry.get("fallbackProvider"), dict):
            yield entry["fallbackProvider"]


def test_the_fixture_carries_no_address() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    assert "http" not in text and "<affiliate-url>" in text


def test_every_provider_of_the_recorded_list_has_its_name_in_the_table() -> None:
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))["body"]
    listed = {(p["id"], p["name"], p["country"]) for p in _listed(body)}
    assert listed == {(1528, "bet365 Türkiye", "TR"), (1, "bet365", "international")}
    for pid, name, country in listed:
        assert sports.odds_provider(pid) == sports.OddsProvider(pid, name, country)


def test_the_table_has_ids_names_and_countries_only() -> None:
    assert [f.name for f in dataclasses.fields(sports.OddsProvider)] == ["id", "name", "country"]
    ids = [p.id for p in sports.ODDS_PROVIDERS]
    assert len(ids) == len(set(ids)) and all(isinstance(i, int) and i >= 1 for i in ids)
    assert sports.ODDS_PROVIDERS[0] == sports.OddsProvider(1, "bet365", "international")
    for p in sports.ODDS_PROVIDERS:
        assert p.name.strip() == p.name and p.name and "http" not in p.name.lower()
        assert p.country == "international" or (len(p.country) == 2 and p.country.isupper())
    assert sports.odds_provider(sports.DEFAULT_ODDS_PROVIDER) is not None


@pytest.mark.parametrize(("given", "label"), [
    (1, "bet365"), ("1", "bet365"), (" 1528 ", "bet365 Türkiye"), (5, "Provider 5"), ("5", "Provider 5"),
])
def test_label_names_a_known_id_and_numbers_an_unknown_one(given: Any, label: str) -> None:
    assert sports.odds_provider_label(given) == label


@pytest.mark.parametrize("given", [None, "", "abc", 5, "999999"])
def test_an_unknown_or_malformed_id_has_no_provider(given: Any) -> None:
    assert sports.odds_provider(given) is None


def test_the_setting_names_the_default_provider() -> None:
    field = next(f for f in dataclasses.fields(ClientSettings) if f.name == "odds_provider")
    default = sports.odds_provider(field.default)
    assert default is not None and f"{default.id} = {default.name}" in field.metadata["doc"]
    assert "/api/v1/odds/providers" in field.metadata["doc"]


def test_api_lists_the_table_and_marks_the_configured_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    r = client.get("/api/v1/odds/providers")
    assert r.status_code == 200 and "http" not in r.text
    body = r.json()
    assert [(p["id"], p["name"], p["country"]) for p in body["data"]] == [
        (p.id, p.name, p.country) for p in sports.ODDS_PROVIDERS]
    assert set(body["data"][0]) == {"id", "name", "country", "configured"}
    assert [p["id"] for p in body["data"] if p["configured"]] == [1]
    assert body["page"] == {"limit": len(sports.ODDS_PROVIDERS), "next_cursor": None}

    monkeypatch.setenv("SOFASCORE_CLIENT__ODDS_PROVIDER", "1528")
    assert [p["id"] for p in client.get("/api/v1/odds/providers").json()["data"] if p["configured"]] == [1528]
    # tabloda olmayan bir kimlik geçerlidir; yalnızca adı bilinmez
    monkeypatch.setenv("SOFASCORE_CLIENT__ODDS_PROVIDER", "5")
    assert not [p for p in client.get("/api/v1/odds/providers").json()["data"] if p["configured"]]
