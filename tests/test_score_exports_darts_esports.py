"""
Tek setlik dart ve e-spor haritaları normalleştirilmiş dışa aktarmada (post-3.0 B3; DERIVE_VERSION 8).

Fikstürler araştırma örneklerinden gelen gerçek SofaScore olay yükleridir:

  * dart 17236047 (`tests/fixtures/fx26`): bestOfSets 1, bestOfLegs 7, current 4-0, periodN yok. Eski kural
    (bestOfSets > 0) `format: legs` ile kazanılan leg'leri kazanılan set diye verirdi;
  * dart 17099318 (`tests/golden/schema/inputs`, research/all_sports/samples/darts/event-id__1.json): bestOfSets 5,
    set usulü maç; setleri kalır;
  * e-spor 17264020 (`tests/fixtures/fx26`): bitmiş CS2 serisi, periodN haritaların raunt skoru (7-13, 13-9, 11-13);
  * e-spor 17223320 (`tests/golden/schema/inputs`): canlı seri, periodN oyunu kimin aldığı (1 / 0), süren oyun 0-0.

Dışa aktarmanın `score_*` sütunları (JSONL, CSV, SQLite) düzeltilmiş değerleri verir; eski sürümle (7) yazılmış
katalog ilk açılışta dosyalardan yeniden kurulur ve düzelir. Ağ yok.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterator

import conftest
import pytest

from sofascore_scraper.schema import mappers
from sofascore_scraper.services.export import DatasetFilter, DatasetSpec, ExportService
from sofascore_scraper.slices import SLICE_OK, Outcome
from sofascore_scraper.store import Store, open_store
from sofascore_scraper.store.derive import DERIVE_VERSION

TESTS = Path(__file__).resolve().parent
SOURCES = {
    17236047: TESTS / "fixtures" / "fx26" / "darts__17236047.json",
    17264020: TESTS / "fixtures" / "fx26" / "esports__17264020.json",
    17099318: TESTS / "golden" / "schema" / "inputs" / "darts__D1_sets__17099318.json",
    17223320: TESTS / "golden" / "schema" / "inputs" / "esports__L_second_game__17223320.json",
}
SINGLE_SET_DARTS, MAPS_FINISHED, SET_DARTS, MAPS_LIVE = 17236047, 17264020, 17099318, 17223320


def _sets(*pairs: Any) -> list:
    return [{"number": n, "home": h, "away": a, "tiebreak": None} for n, (h, a) in enumerate(pairs, start=1)]


EXPECTED: Dict[int, Dict[str, Any]] = {
    SINGLE_SET_DARTS: {"family": "sets", "home": 4, "away": 0, "format": "legs_won",
                       "sets_won": {"home": 4, "away": 0}, "sets": [], "match_tiebreak": False},
    SET_DARTS: {"family": "sets", "home": 0, "away": 3, "format": "legs",
                "sets_won": {"home": 0, "away": 3}, "sets": _sets((0, 3), (1, 3), (2, 3)), "match_tiebreak": False},
    MAPS_FINISHED: {"family": "sets", "home": 1, "away": 2, "format": "games_won",
                    "sets_won": {"home": 1, "away": 2}, "sets": _sets((7, 13), (13, 9), (11, 13)),
                    "match_tiebreak": False},
    MAPS_LIVE: {"family": "sets", "home": 1, "away": 0, "format": "games_won",
                "sets_won": {"home": 1, "away": 0}, "sets": _sets((1, 0)), "match_tiebreak": False},
}


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Store]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conftest.STORE_BOUNDARY.add_data_dir(str(data_dir))
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(data_dir))
    found = open_store(data_dir)
    for event_id, path in SOURCES.items():
        event = json.loads(path.read_text(encoding="utf-8"))["event"]
        assert event["id"] == event_id
        found.events.put(event_id, {"event": Outcome(SLICE_OK, data=event)})
    yield found
    found.close()


def _spec(fmt: str) -> DatasetSpec:
    return DatasetSpec(dataset="events", format=fmt, filter=DatasetFilter(event_ids=tuple(SOURCES)))


def test_the_records_carry_legs_for_a_single_set_and_the_score_of_each_map(store: Store) -> None:
    scores = {record.id: record.to_dict()["score"] for record in ExportService(store).records("events",
                                                                                            _spec("jsonl").filter)}
    assert scores == EXPECTED


def test_jsonl_export(store: Store, tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    ExportService(store).export(_spec("jsonl"), target)
    found = {row["id"]: row["score"] for row in map(json.loads, target.read_text(encoding="utf-8").splitlines())}
    assert found == EXPECTED


@pytest.mark.parametrize("fmt", ["csv", "sqlite"])
def test_table_exports_have_the_corrected_score_columns(store: Store, tmp_path: Path, fmt: str) -> None:
    target = tmp_path / f"events.{fmt}"
    ExportService(store).export(_spec(fmt), target)
    if fmt == "csv":
        with target.open(encoding="utf-8", newline="") as f:
            rows = {int(row["id"]): row for row in csv.DictReader(f)}
    else:
        conn = sqlite3.connect(target)
        try:
            cursor = conn.execute('SELECT * FROM "events"')
            names = [c[0] for c in cursor.description]
            rows = {int(row[names.index("id")]): dict(zip(names, row, strict=True)) for row in cursor.fetchall()}
        finally:
            conn.close()

    for event_id, score in EXPECTED.items():
        row = rows[event_id]
        assert row["score_format"] == score["format"]
        assert (str(row["score_sets_won_home"]), str(row["score_sets_won_away"])) == \
            (str(score["sets_won"]["home"]), str(score["sets_won"]["away"]))
        assert json.loads(row["score_sets"]) == score["sets"], event_id  # liste: JSON metni
        assert row["score_periods"] in ("", None)  # başka ailenin alanı


def test_a_catalog_of_derive_version_7_is_rebuilt_with_the_new_rule(store: Store) -> None:
    # 3.1 öncesi kural: tek setlik dart `legs`, e-spor haritaları yok
    stale = {
        SINGLE_SET_DARTS: {"family": "sets", "format": "legs", "match_tiebreak": False, "sets": {},
                           "sets_won": [4, 0], "tiebreaks": {}},
        MAPS_FINISHED: {"family": "sets", "format": "games_won", "match_tiebreak": False, "sets": {},
                        "sets_won": [1, 2], "tiebreaks": {}},
    }
    with store._catalog.write() as conn:
        conn.execute("UPDATE meta SET value = '7' WHERE key = 'derive_version'")
        for event_id, sheet in stale.items():
            conn.execute("UPDATE events SET scores_json = ? WHERE id = ?", (json.dumps(sheet), event_id))
    store.close()

    reopened = open_store(store.data_dir)
    try:
        assert DERIVE_VERSION == 8
        assert reopened._catalog.get_meta("derive_version") == "8"
        for event_id in stale:
            assert mappers.score_from_row(reopened.events.get(event_id)).to_dict() == EXPECTED[event_id]
    finally:
        reopened.close()
