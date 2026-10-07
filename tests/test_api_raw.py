"""
API v1: ham yükler (plan maddesi P21; docs/design/04-schema-v1.md bölüm 7, docs/design/02-services.md bölüm 6).

  * `/events/{id}/raw` maçın saklanan olay nesnesini, `/events/{id}/slices/{key}/raw` bir dilimin saklanan yükünü
    olduğu gibi verir: zarfsız, alan eklenmeden, `application/json`;
  * `ETag` baytların sha256'sıdır, `X-Sofascore-Fetched-At` yükün alındığı an; `If-None-Match` ile 304;
  * yük saklanmıyorsa (yalnızca listeden bilinen maç, boş ya da istenmemiş dilim) 404 `not_found`: boş bir yük
    uydurulmaz;
  * eski düzen (girintili `basic.json`) ve v3 aynı değerleri verir; okumak hiçbir dosyayı değiştirmez.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

import store_fixtures as sf
from sofascore_scraper.store import EventQuery, Store, open_store
from sofascore_scraper.web.app import app

client = TestClient(app)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    fixture = sf.build_fixture("canonical", tmp_path / "data")
    monkeypatch.setenv("DATA_DIR", str(fixture.data_dir))
    return open_store(fixture.data_dir)


def rows(store: Store) -> List[Any]:
    return list(store.events.list(EventQuery(limit=1000)).items)


def error(response: Any, status: int, code: str) -> Dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code, body
    return body


def test_the_event_payload_as_stored(store: Store) -> None:
    row = next(r for r in rows(store) if r.has_event_payload)
    r = client.get(f"/api/v1/events/{row.id}/raw")
    assert r.status_code == 200 and r.headers["content-type"] == "application/json"
    stored = store.events.payload(row.id, "event", raw=True)
    assert r.content == stored
    assert r.json() == store.events.payload(row.id, "event")
    assert r.json()["id"] == row.id and "event" not in r.json()  # the object, not SofaScore's wrapper
    assert r.headers["etag"] == f'"{hashlib.sha256(stored).hexdigest()}"'
    info = store.events.slice(row.id, "event")
    assert r.headers["x-sofascore-fetched-at"] == info.fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert "x-request-id" in r.headers and "error" not in r.json()


def test_a_slice_payload_as_stored(store: Store) -> None:
    row = next(r for r in rows(store) if r.has_event_payload)
    info = next(i for i in store.events.slices(row.id) if i.has_payload and i.key != "event")
    r = client.get(f"/api/v1/events/{row.id}/slices/{info.key}/raw")
    assert r.status_code == 200
    assert r.content == store.events.payload(row.id, info.key, raw=True)
    assert r.json() == store.events.payload(row.id, info.key)


def test_if_none_match_answers_not_modified(store: Store) -> None:
    row = next(r for r in rows(store) if r.has_event_payload)
    first = client.get(f"/api/v1/events/{row.id}/raw")
    again = client.get(f"/api/v1/events/{row.id}/raw", headers={"if-none-match": f'"other", {first.headers["etag"]}'})
    assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == first.headers["etag"]
    other = client.get(f"/api/v1/events/{row.id}/raw", headers={"if-none-match": '"other"'})
    assert other.status_code == 200 and other.content == first.content


def test_no_payload_is_not_found(store: Store) -> None:
    listed = next(r for r in rows(store) if not r.has_event_payload)
    error(client.get(f"/api/v1/events/{listed.id}/raw"), 404, "not_found")
    error(client.get("/api/v1/events/123/raw"), 404, "not_found")
    stored = next(r for r in rows(store) if r.has_event_payload)
    absent = next(key for key in ("statistics", "lineups", "incidents", "h2h")
                  if not store.events.slice(stored.id, key).has_payload)
    error(client.get(f"/api/v1/events/{stored.id}/slices/{absent}/raw"), 404, "not_found")
    error(client.get(f"/api/v1/events/{stored.id}/slices/Bad/raw"), 422, "invalid_request")
    error(client.get(f"/api/v1/events/{stored.id}/slices/nul/raw"), 400, "invalid_request")


def test_an_empty_slice_has_no_raw_payload(store: Store) -> None:
    for row in rows(store):
        for info in store.events.slices(row.id):
            if info.state == "empty" and not info.has_payload:
                error(client.get(f"/api/v1/events/{row.id}/slices/{info.key}/raw"), 404, "not_found")
                return
    pytest.skip("the fixture has no empty slice without a payload")


def test_old_and_new_layouts_give_the_same_values(store: Store, tmp_path: Path) -> None:
    """Eski düzenin girintili dosyası ile v3'ün kurallı baytları aynı değerleri taşır."""
    row = next(r for r in rows(store) if r.has_event_payload and r.layout == "legacy")
    before = client.get(f"/api/v1/events/{row.id}/raw")
    on_disk = json.loads((store.data_dir / row.path / "basic.json").read_bytes())
    assert before.json() == on_disk


def test_reading_raw_payloads_changes_no_file(store: Store) -> None:
    def digest() -> Dict[str, str]:
        out = {}
        for path in sorted(store.data_dir.rglob("*")):
            if path.is_file() and ".meta" not in path.parts:
                out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return out

    before = digest()
    for row in rows(store)[:10]:
        client.get(f"/api/v1/events/{row.id}/raw")
        client.get(f"/api/v1/events/{row.id}/slices/statistics/raw")
    assert digest() == before
