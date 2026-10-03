"""
API v1: okuma kaynakları (plan maddesi P21; docs/design/02-services.md bölüm 6, docs/design/05-web-ui.md 7.2 ve
7.3: G6, G7, G8). Tümü çevrimdışı; veri dizini `tests/store_fixtures.py`'nin `canonical` dizinidir.

  * kayıtlar şema v1'indir: yanıt modelleri dataclass'lardan aynalanır ve aynı JSON'u verir (`status.class`);
  * `/events`: süzgeçler (spor, turnuva, sezon, yarışmacı, durum sınıfı, tarih, detay, ad, takip), iki sıra,
    imleçle sayfalama, istenirse maç başına dilim özeti;
  * `/events/{id}`, dilimleri, tek dilim yüküyle, bahis oranları;
  * `/tournaments`, `/tournaments/{id}`, sezonları, `/seasons/{id}` ve sezon dilimleri;
  * `/changes`: kendi sıra numarasıyla, iki yönde, süzgeçlerle.

Ham yük rotaları tests/test_api_raw.py'dedir.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

import store_fixtures as sf
from src import schema
from src.schema import models as schema_models
from src.services.query import QueryService, refresh_window_seconds
from src.store import EventQuery, FollowSpec, Ref, Store, open_store
from src.web.api.v1 import records
from src.web.app import app

client = TestClient(app)

PL, LALIGA, NBA, WIMBLEDON, FA_CUP = sf.PL.id, sf.LALIGA.id, sf.NBA.id, sf.WIMBLEDON.id, sf.FA_CUP.id


@pytest.fixture
def canonical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sf.LegacyFixture:
    fixture = sf.build_fixture("canonical", tmp_path / "data")
    monkeypatch.setenv("DATA_DIR", str(fixture.data_dir))
    return fixture


@pytest.fixture
def store(canonical: sf.LegacyFixture) -> Store:
    return open_store(canonical.data_dir)


def data(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


def page_of(response: Any) -> Dict[str, Any]:
    assert response.status_code == 200, response.text
    return response.json()


def error(response: Any, status: int, code: str) -> Dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code, body
    return body


def all_rows(store: Store, **query: Any) -> List[Any]:
    return list(store.events.list(EventQuery(limit=1000, **query)).items)


def event_json(row: Any) -> Dict[str, Any]:
    return schema.event_from_row(row, refresh_window_s=refresh_window_seconds()).to_dict()


def walk(path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Bir listeyi imleçle sonuna kadar okur."""
    items: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    for _ in range(100):
        body = page_of(client.get(path, params={**params, **({"cursor": cursor} if cursor else {})}))
        items.extend(body["data"])
        cursor = body["page"]["next_cursor"]
        if cursor is None:
            return items
    raise AssertionError("the list did not end")


# --- kayıtların aynası --------------------------------------------------------------------------------


@pytest.mark.parametrize("record", [m for m in schema_models.MODELS if m is not schema_models.LiveEvent],
                         ids=lambda m: m.__name__)
def test_every_mirrored_record_has_the_fields_of_the_schema(record: type) -> None:
    model = records.mirror(record)
    expected = [schema_models.json_name(f) for f in dataclasses.fields(record)]
    assert [f.alias or name for name, f in model.model_fields.items()] == expected
    documented = model.model_json_schema(by_alias=True)
    contract = schema.json_schema()["$defs"][record.__name__]
    assert list(documented["properties"]) == list(contract["properties"])
    assert documented["required"] == contract["required"]
    for name, field in contract["properties"].items():
        assert documented["properties"][name].get("description") == field.get("description"), name


def test_the_status_class_keeps_its_json_name() -> None:
    assert records.RENAMED == {"Status": "EventStatus"}
    status = records.mirror(schema_models.Status)
    assert status.__name__ == "EventStatus" and "class" in status.model_json_schema(by_alias=True)["properties"]


def test_every_stored_event_validates_and_serialises_unchanged(store: Store) -> None:
    for row in all_rows(store):
        record = event_json(row)
        assert records.Event.model_validate(record).model_dump(by_alias=True, mode="json") == record, row.id


# --- maç listesi --------------------------------------------------------------------------------------


def test_the_event_list_is_newest_first_with_schema_records(store: Store) -> None:
    rows = all_rows(store)
    body = page_of(client.get("/api/v1/events", params={"limit": 200}))
    assert body["page"] == {"limit": 200, "next_cursor": None}
    assert [item["id"] for item in body["data"]] == [row.id for row in rows]
    first = body["data"][0]
    assert first == {**event_json(rows[0]), "slices_summary": None}
    assert first["status"]["class"] in ("not_started", "live", "completed", "decided_without_play", "void", "unknown")


def test_the_event_list_sorts_both_ways_and_pages_with_the_cursor(store: Store) -> None:
    newest = [row.id for row in all_rows(store)]
    oldest = [row.id for row in all_rows(store, sort="start_asc")]
    assert [e["id"] for e in walk("/api/v1/events", {"limit": 4})] == newest
    assert [e["id"] for e in walk("/api/v1/events", {"limit": 3, "sort": "start_utc"})] == oldest
    first = page_of(client.get("/api/v1/events", params={"limit": 4}))
    assert first["page"]["next_cursor"]
    wrong_order = client.get("/api/v1/events", params={"cursor": first["page"]["next_cursor"], "sort": "start_utc"})
    error(wrong_order, 400, "invalid_request")
    error(client.get("/api/v1/events", params={"cursor": "not-a-cursor"}), 400, "invalid_request")
    error(client.get("/api/v1/events", params={"sort": "id"}), 422, "invalid_request")
    error(client.get("/api/v1/events", params={"limit": 201}), 422, "invalid_request")


@pytest.mark.parametrize("params,check", [
    ({"tournament": PL}, lambda r: r.tournament_id == PL),
    ({"tournament": [PL, NBA]}, lambda r: r.tournament_id in (PL, NBA)),
    ({"season": 96668}, lambda r: r.season_id == 96668),
    ({"sport": "tennis"}, lambda r: r.sport == "tennis"),
    ({"status": "void"}, lambda r: r.status_class == "void"),
    ({"status": ["completed", "decided_without_play"]}, lambda r: r.status_class in ("completed", "decided_without_play")),
    ({"has": "details"}, lambda r: r.has_event_payload),
    ({"has": "missing"}, lambda r: not r.has_event_payload),
    ({"sport": "football", "has": "details", "status": "completed"},
     lambda r: r.sport == "football" and r.has_event_payload and r.status_class == "completed"),
])
def test_event_filters(store: Store, params: Dict[str, Any], check: Any) -> None:
    expected = [row.id for row in all_rows(store) if check(row)]
    assert expected, params
    assert [e["id"] for e in walk("/api/v1/events", {**params, "limit": 5})] == expected


def test_the_date_filters_take_dates_and_date_times(store: Store) -> None:
    rows = all_rows(store)
    day = datetime.fromtimestamp(rows[0].start_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    on_day = [row.id for row in rows if row.start_ts is not None
              and datetime.fromtimestamp(row.start_ts, tz=timezone.utc).strftime("%Y-%m-%d") == day]
    assert [e["id"] for e in walk("/api/v1/events", {"from": day, "to": day})] == on_day
    moment = rows[3].start_ts
    stamp = datetime.fromtimestamp(moment, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    after = [row.id for row in rows if row.start_ts is not None and row.start_ts >= moment]
    assert [e["id"] for e in walk("/api/v1/events", {"from": stamp})] == after
    offset = datetime.fromtimestamp(moment, tz=timezone.utc).astimezone().isoformat()
    assert [e["id"] for e in walk("/api/v1/events", {"from": offset})] == after
    body = error(client.get("/api/v1/events", params={"from": "yesterday"}), 422, "invalid_request")
    assert body["details"]["errors"][0]["loc"] == ["query", "from"]


def test_the_participant_and_name_filters(store: Store) -> None:
    row = next(r for r in all_rows(store) if r.home_id is not None and r.home_name)
    with_team = [r.id for r in all_rows(store) if row.home_id in (r.home_id, r.away_id)]
    assert [e["id"] for e in walk("/api/v1/events", {"participant": row.home_id})] == with_team
    by_name = [e["id"] for e in walk("/api/v1/events", {"q": row.home_name[:4].lower()})]
    assert row.id in by_name


def test_only_followed_tournaments(store: Store) -> None:
    assert walk("/api/v1/events", {"followed": "true"}) == []
    store.follows.add(FollowSpec(kind="tournament", entity_id=NBA, name="NBA"), origin="api")
    assert [e["id"] for e in walk("/api/v1/events", {"followed": "true"})] == [
        row.id for row in all_rows(store) if row.tournament_id == NBA]


def test_the_slice_summary_counts_the_selected_slices(store: Store) -> None:
    from src.services import planning
    from src.sports import select_slices

    body = page_of(client.get("/api/v1/events", params={"include": "slices_summary", "limit": 200}))
    states = {s.event.id: s for s in store.events.states()}
    for item in body["data"]:
        state = states[item["id"]]
        specs = select_slices("event", state.event.sport or None, None,
                              phase=planning.phase_of(state.event.status_class))
        found = [state.slice(spec.key).state for spec in specs]
        assert item["slices_summary"] == {"selected": len(specs), "ok": found.count("ok"),
                                          "empty": found.count("empty"), "error": found.count("error")}
    assert any(item["slices_summary"]["ok"] for item in body["data"])
    error(client.get("/api/v1/events", params={"include": "everything"}), 422, "invalid_request")


# --- tek maç ve dilimleri -----------------------------------------------------------------------------


def test_one_event(store: Store) -> None:
    row = all_rows(store)[0]
    assert data(client.get(f"/api/v1/events/{row.id}")) == event_json(row)
    error(client.get("/api/v1/events/123"), 404, "not_found")
    error(client.get("/api/v1/events/x"), 422, "invalid_request")


def test_the_slices_of_an_event(store: Store) -> None:
    from src.sports import select_slices

    row = next(r for r in all_rows(store) if r.has_event_payload and r.sport == "football")
    found = data(client.get(f"/api/v1/events/{row.id}/slices"))
    stored = {(info.key, info.sub): info for info in store.events.slices(row.id)}
    selected = {spec.key for spec in select_slices("event", "football", None)}
    assert [(s["key"], s["sub"] or "") for s in found] == sorted(set(stored) | {(key, "") for key in selected})
    for item in found:
        assert item["payload"] is None and item["owner_kind"] == "event" and item["owner_id"] == row.id
        info = stored.get((item["key"], item["sub"] or ""))
        assert item["state"] == (info.state if info is not None else "not_requested")
    error(client.get("/api/v1/events/123/slices"), 404, "not_found")


def test_one_slice_with_its_payload(store: Store) -> None:
    row = next(r for r in all_rows(store) if r.has_event_payload)
    info = next(i for i in store.events.slices(row.id) if i.has_payload and i.key != "event")
    found = data(client.get(f"/api/v1/events/{row.id}/slices/{info.key}"))
    assert found == schema.slice_from_info(info, payload=store.events.payload(row.id, info.key)).to_dict()
    assert found["payload"] is not None and found["state"] == "ok"


def test_a_selected_slice_that_was_never_asked_for_has_no_payload(store: Store) -> None:
    row = next(r for r in all_rows(store) if r.has_event_payload and r.sport == "football")
    stored = {info.key for info in store.events.slices(row.id)}
    missing = next(key for key in ("statistics", "lineups", "incidents") if key not in stored)
    found = data(client.get(f"/api/v1/events/{row.id}/slices/{missing}"))
    assert (found["state"], found["has_payload"], found["payload"]) == ("not_requested", False, None)


def test_unknown_and_invalid_slices(store: Store) -> None:
    row = next(r for r in all_rows(store) if r.has_event_payload)
    error(client.get(f"/api/v1/events/{row.id}/slices/no_such_slice"), 404, "not_found")
    error(client.get(f"/api/v1/events/{row.id}/slices/statistics", params={"sub": "x"}), 404, "not_found")
    error(client.get(f"/api/v1/events/{row.id}/slices/Bad-Key"), 422, "invalid_request")
    error(client.get(f"/api/v1/events/{row.id}/slices/statistics", params={"sub": "UPPER"}), 422, "invalid_request")
    error(client.get(f"/api/v1/events/{row.id}/slices/nul"), 400, "invalid_request")
    error(client.get("/api/v1/events/123/slices/statistics"), 404, "not_found")


def test_odds_are_empty_until_odds_are_stored(store: Store) -> None:
    row = all_rows(store)[0]
    assert page_of(client.get(f"/api/v1/events/{row.id}/odds")) == {"data": [], "page": {"limit": 0, "next_cursor": None}}
    error(client.get("/api/v1/events/123/odds"), 404, "not_found")


# --- turnuvalar ve sezonlar ---------------------------------------------------------------------------


def test_tournaments_by_name_with_category_and_follow(store: Store) -> None:
    rows = store.entities.tournaments(limit=100)
    listed = data(client.get("/api/v1/tournaments"))
    assert [t["id"] for t in listed] == [row.id for row in rows]
    assert listed[0] == {**schema.tournament_from_row(rows[0]).to_dict(), "category": None, "followed": False}
    store.follows.add(FollowSpec(kind="tournament", entity_id=PL, name="Premier League"), origin="api")
    assert [t["id"] for t in data(client.get("/api/v1/tournaments", params={"followed": "true"}))] == [PL]
    assert PL not in [t["id"] for t in data(client.get("/api/v1/tournaments", params={"followed": "false"}))]
    assert [t["id"] for t in data(client.get("/api/v1/tournaments", params={"sport": "tennis"}))] == [WIMBLEDON]
    assert [t["id"] for t in data(client.get("/api/v1/tournaments", params={"q": "liga"}))] == [LALIGA]
    assert data(client.get(f"/api/v1/tournaments/{PL}"))["followed"] is True


def test_the_tournament_list_pages(store: Store) -> None:
    rows = [row.id for row in store.entities.tournaments(limit=100)]
    assert [t["id"] for t in walk("/api/v1/tournaments", {"limit": 2})] == rows
    error(client.get("/api/v1/tournaments", params={"cursor": "abc"}), 400, "invalid_request")


def test_a_tournament_has_its_category_when_the_catalog_knows_it(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.store import CategoryRow

    real = store.entities.tournament

    def with_category(tournament_id: int) -> Any:
        row = real(tournament_id)
        return dataclasses.replace(row, category_id=1) if row is not None else None

    monkeypatch.setattr(store.entities, "tournament", with_category)
    monkeypatch.setattr(store.entities, "category",
                        lambda cid: CategoryRow(id=cid, sport="football", name="England", slug="england", alpha2="EN"))
    found = data(client.get(f"/api/v1/tournaments/{PL}"))
    assert found["category_id"] == 1
    assert found["category"] == {"id": 1, "sport": "football", "name": "England", "slug": "england", "country_code": "EN"}


def test_one_tournament_and_its_seasons(store: Store) -> None:
    error(client.get("/api/v1/tournaments/999999"), 404, "not_found")
    seasons = data(client.get(f"/api/v1/tournaments/{PL}/seasons"))
    assert seasons == [schema.season_from_row(row).to_dict() for row in store.entities.seasons(PL)]
    assert [s["id"] for s in seasons] == [96668, 76986, 61627]
    error(client.get("/api/v1/tournaments/999999/seasons"), 404, "not_found")


def test_one_season_and_its_slices(store: Store) -> None:
    season = data(client.get("/api/v1/seasons/96668"))
    assert season == schema.season_from_row(store.entities.season(96668)).to_dict()
    error(client.get("/api/v1/seasons/1"), 404, "not_found")
    ref = Ref.season(PL, 96668)
    info = store.entities.slices(ref)[0]
    found = data(client.get("/api/v1/seasons/96668/slices/schedule", params={"sub": info.sub}))
    assert found == schema.slice_from_info(info, payload=store.entities.payload(ref, "schedule", info.sub)).to_dict()
    assert found["owner_kind"] == "season" and found["payload"] is not None
    error(client.get("/api/v1/seasons/96668/slices/standings"), 404, "not_found")
    error(client.get("/api/v1/seasons/1/slices/standings"), 404, "not_found")


# --- değişiklikler ------------------------------------------------------------------------------------


def _more_changes(store: Store, count: int) -> None:
    """Değişiklik günlüğüne satır ekler: turnuvası sırayla PL ve NBA olan maçlar."""
    for i in range(count):
        tournament = PL if i % 2 == 0 else NBA
        moment = datetime.fromtimestamp(1790000000 + i * 60, tz=timezone.utc)
        store.changes.append({
            "ts_utc": moment.isoformat(timespec="seconds"), "event_id": 900000 + i,
            "sport": "football" if tournament == PL else "basketball",
            "tournament": {"id": tournament, "name": None}, "start_ts": 1789990000,
            "changed": {"homeScore.current": [i, i + 1]}, "status_class": ["completed", "completed"],
        })


def test_changes_by_sequence_number(store: Store) -> None:
    _more_changes(store, 6)
    rows = store.changes.list(limit=100)
    found = data(client.get("/api/v1/changes"))
    assert found == [schema.change_from_row(row).to_dict() for row in rows]
    assert [c["seq"] for c in walk("/api/v1/changes", {"limit": 3})] == [row.seq for row in rows]
    assert [c["seq"] for c in walk("/api/v1/changes", {"since": rows[2].seq})] == [row.seq for row in rows[3:]]
    newest = [c["seq"] for c in walk("/api/v1/changes", {"order": "desc", "limit": 3})]
    assert newest == [row.seq for row in reversed(rows)]
    assert [c["seq"] for c in walk("/api/v1/changes", {"order": "desc", "since": rows[4].seq})] == [
        row.seq for row in reversed(rows[5:])]


def test_changes_filters(store: Store) -> None:
    _more_changes(store, 6)
    rows = store.changes.list(limit=100)
    assert [c["seq"] for c in walk("/api/v1/changes", {"tournament": NBA, "limit": 2})] == [
        row.seq for row in rows if row.tournament_id == NBA]
    assert [c["seq"] for c in walk("/api/v1/changes", {"event_id": 900003})] == [
        row.seq for row in rows if row.event_id == 900003]
    moment = datetime.fromtimestamp(1790000000 + 120, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    until = datetime.fromtimestamp(1790000000 + 240, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert [c["event_id"] for c in walk("/api/v1/changes", {"from": moment, "to": until})] == [900002, 900003, 900004]
    assert [c["event_id"] for c in walk("/api/v1/changes", {"from": moment, "to": until, "order": "desc", "limit": 1})] == [
        900004, 900003, 900002]
    error(client.get("/api/v1/changes", params={"cursor": "x"}), 400, "invalid_request")
    error(client.get("/api/v1/changes", params={"order": "random"}), 422, "invalid_request")


def test_an_empty_change_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "empty"))
    assert page_of(client.get("/api/v1/changes")) == {"data": [], "page": {"limit": 50, "next_cursor": None}}
    assert page_of(client.get("/api/v1/changes", params={"order": "desc"}))["data"] == []
    assert page_of(client.get("/api/v1/events"))["data"] == []
    assert data(client.get("/api/v1/tournaments")) == []


def test_the_query_service_answers_none_for_ids_no_store_can_hold(store: Store) -> None:
    service = QueryService(store)
    for huge in (2 ** 63, -(2 ** 63) - 1):
        assert service.event(huge) is None and service.tournament(huge) is None and service.season(huge) is None
        assert service.raw(huge) is None and service.event_slices(huge) is None
