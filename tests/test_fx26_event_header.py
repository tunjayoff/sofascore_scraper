"""
Maç sayfasının başlığı, spora göre (FX-26; canlı doğrulama 2026-10-08, M7 M8 M9 M10 M11 M19).

Fikstürler `tests/fixtures/fx26/` altındaki gerçek SofaScore olay yükleridir (uçtan uca testin 2026-10-07 tarihli
bitmiş maçları, kısaltılmış; kişisel veri yok). Burada:

  * `GET /events/{id}/extra`: SofaScore'un sonuç notu (kriket), "best of" serisinin skoru (beyzbol play-off'u),
    saha ve hakem, saklanan olay yükünden; olmayan alan null;
  * başlığın okuduğu şema v1 alanları her fikstürde dolu: kriketin wicket ve overları, beyzbolun devreleri ile
    isabet ve hataları, setsiz sporların biçimi (leg, frame, oyun), MMA'nın yöntemi ve son raundu, tenisin
    tie-break sayıları, futsal kupasında skorsuz toplam;
  * `frontend/tests/fixtures/fx26-events.json` bu API'nin bugünkü yanıtıdır (ön yüz testleri onu okur).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest
from fastapi.testclient import TestClient

import conftest
from sofascore_scraper.services.query import event_extra_of
from sofascore_scraper.slices import SLICE_OK, Outcome
from sofascore_scraper.store import Store, open_store
from sofascore_scraper.web.app import app

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "fx26"
FRONTEND_FIXTURE = ROOT / "frontend" / "tests" / "fixtures" / "fx26-events.json"
client = TestClient(app)


def payloads() -> Dict[str, Dict[str, Any]]:
    """Fikstür adı (`cricket__15884177`) → olay nesnesi."""
    return {path.stem: json.loads(path.read_text(encoding="utf-8"))["event"] for path in sorted(FIXTURES.glob("*.json"))}


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Store]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conftest.STORE_BOUNDARY.add_data_dir(str(data_dir))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    found = open_store(data_dir)
    for event in payloads().values():
        found.events.put(int(event["id"]), {"event": Outcome(SLICE_OK, data=event)})
    yield found


def get(event_id: int) -> Dict[str, Any]:
    """Maçın kaydı (`data`) ve başlık bilgileri (`extra`), iki rotadan."""
    record = client.get(f"/api/v1/events/{event_id}")
    extra = client.get(f"/api/v1/events/{event_id}/extra")
    assert record.status_code == 200 and extra.status_code == 200, (record.text, extra.text)
    return {"data": record.json()["data"], "extra": extra.json()["data"]}


NO_EXTRA = {"note": None, "series": None, "venue": None, "referee": None}


def test_cricket_has_the_result_note(store: Store) -> None:
    body = get(15884177)
    assert body["extra"] == {**NO_EXTRA, "note": "India beat West Indies by 8 wickets"}
    score = body["data"]["score"]
    assert score["family"] == "cricket"
    assert score["innings"] == [
        {"side": "home", "number": 1, "runs": 172, "wickets": 2, "overs": 14.4},
        {"side": "away", "number": 1, "runs": 171, "wickets": 10, "overs": 19.1},
    ]


def test_baseball_has_innings_hits_errors_and_the_series(store: Store) -> None:
    body = get(17199139)
    assert body["extra"] == {**NO_EXTRA, "series": {"home": 1, "away": 2}}
    score = body["data"]["score"]
    assert score["family"] == "innings"
    assert [(i["number"], i["home"], i["away"]) for i in score["innings"]][3:5] == [(4, 0, 3), (5, 1, 0)]
    assert len(score["innings"]) == 9
    assert (score["hits"], score["errors"]) == ({"home": 5, "away": 8}, {"home": 2, "away": 0})


def test_formats_without_sets_name_what_they_count(store: Store) -> None:
    formats = {name: get(int(name.split("__")[1]))["data"]["score"]["format"]
               for name in ("darts__17236047", "snooker__17264352", "esports__17264020")}
    # Dart: tek setlik maç (`bestOfSets: 1`) eşleyicide `legs` kalır ve set listesi boştur; başlık onu leg sayısı
    # olarak gösterir (ön yüz `setsLabel`). Eşleyicinin kuralı (bestOfSets > 1) ayrı bir iştir: türetme sürümünü değiştirir
    assert formats == {"darts__17236047": "legs", "snooker__17264352": "frames", "esports__17264020": "games_won"}
    assert get(17236047)["data"]["score"]["sets"] == []


def test_mma_has_method_and_final_round(store: Store) -> None:
    body = get(17057712)
    assert body["data"]["winner"] == "away"
    assert {k: body["data"]["score"][k] for k in ("family", "method", "final_round")} == \
        {"family": "fight", "method": "UD", "final_round": 3}
    assert body["extra"] == NO_EXTRA


def test_tennis_keeps_the_tie_break_points(store: Store) -> None:
    sets = get(16385361)["data"]["score"]["sets"]
    assert [s["tiebreak"] for s in sets[:2]] == [{"home": 7, "away": 9}, {"home": 7, "away": 2}]
    assert sets[2]["tiebreak"] is None


def test_futsal_aggregate_without_scores(store: Store) -> None:
    body = get(17256669)
    assert body["data"]["aggregate"] == {"home": None, "away": None, "winner": "home"}
    assert body["data"]["round"]["name"] == "Quarterfinals"


def test_extra_of_a_payload() -> None:
    assert event_extra_of({"note": "  ", "homeScore": {"series": 1}, "awayScore": {}}) is None
    assert event_extra_of({"event": {"note": "Match abandoned"}}).note == "Match abandoned"  # type: ignore[union-attr]
    found = event_extra_of({"venue": {"stadium": {"name": "Ekana"}}, "referee": {"name": "P. Bhatt"}})
    assert found is not None and (found.venue, found.referee, found.note) == ("Ekana", "P. Bhatt", None)
    assert event_extra_of({"homeScore": {"series": True}, "awayScore": {"series": 0}}) is None
    assert event_extra_of(None) is None and event_extra_of([1]) is None


def test_an_unknown_event_is_not_found(store: Store) -> None:
    response = client.get("/api/v1/events/999999999/extra")
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"


def test_venue_and_referee_come_from_the_payload(store: Store) -> None:
    payload = {"id": 9400001, "status": {"code": 100, "type": "finished"}, "venue": {"name": "Ekana Stadium"},
               "referee": {"name": "Prakash Bhatt"}, "homeTeam": {"id": 1, "name": "A"}, "awayTeam": {"id": 2, "name": "B"}}
    store.events.put(9400001, {"event": Outcome(SLICE_OK, data=payload)})
    assert get(9400001)["extra"] == {**NO_EXTRA, "venue": "Ekana Stadium", "referee": "Prakash Bhatt"}


def frontend_fixture() -> Dict[str, Any]:
    """`GET /events/{id}`in yanıtları, kalitenin zamana bağlı alanları sabitlenmiş (ön yüz testlerinin fikstürü)."""
    out: Dict[str, Any] = {}
    for name, event in payloads().items():
        body = get(int(event["id"]))
        body["data"]["quality"].update(observed_at_utc="2026-10-08T06:15:08Z", settlement="final", provisional=False)
        out[name] = body
    return out


def test_the_frontend_fixture_is_current(store: Store) -> None:
    found = frontend_fixture()
    recorded = json.loads(FRONTEND_FIXTURE.read_text(encoding="utf-8")) if FRONTEND_FIXTURE.exists() else None
    assert recorded == found, (
        "frontend/tests/fixtures/fx26-events.json is not the API's answer; regenerate it: "
        "FX26_WRITE=1 python -m pytest tests/test_fx26_event_header.py -k write")


def test_write_the_frontend_fixture(store: Store) -> None:
    if os.environ.get("FX26_WRITE") != "1":
        pytest.skip("only with FX26_WRITE=1")
    FRONTEND_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FRONTEND_FIXTURE.write_text(json.dumps(frontend_fixture(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
