"""
Dışa aktarma sonucunun `events` sayacı normalleştirilmiş veri kümelerinde de dolar (FX-26, canlı doğrulama M18:
normalleştirilmiş maç dışa aktarması `"rows": 846` yanında `"events": 0` diyordu). Sayaç dosyanın kapsadığı farklı
maç sayısıdır: maç kümesinde satır sayısı, dilim kümesinde `event_id`'si farklı maçlar; puan durumu maça bağlı
değildir (0). Ham dışa aktarmanın ve geniş CSV'nin sayacı değişmedi.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest

import conftest
from sofascore_scraper.services.data_jobs import ExportRequest, run_export
from sofascore_scraper.slices import SLICE_EMPTY, SLICE_OK, Outcome
from sofascore_scraper.store import Store, open_store

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fx26"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conftest.STORE_BOUNDARY.add_data_dir(str(data_dir))
    found = open_store(data_dir)
    for path in sorted(FIXTURES.glob("*.json")):
        event = json.loads(path.read_text(encoding="utf-8"))["event"]
        found.events.put(int(event["id"]), {"event": Outcome(SLICE_OK, data=event)})
    # iki maçın birer dilim satırı daha: dilim kümesinde 8 maçın 10 satırı
    found.events.put(16385361, {"statistics": Outcome(SLICE_OK, data={"statistics": [{"period": "ALL", "groups": [
        {"groupName": "Service", "statisticsItems": [{"name": "Aces", "home": "10", "away": "12"}]}]}]})})
    found.events.put(17199139, {"lineups": Outcome(SLICE_EMPTY, data=None, reason="404", http_status=404)})
    yield found


def export(store: Store, tmp_path: Path, dataset: str, **fields: object) -> dict:
    dest = tmp_path / f"{dataset}.jsonl"
    request = {"dataset": dataset, "format": "jsonl", "schema": "normalized", **fields}
    return run_export(store, ExportRequest(**request), str(dest))  # type: ignore[arg-type]


def test_normalized_events_count_their_matches(store: Store, tmp_path: Path) -> None:
    result = export(store, tmp_path, "events")
    assert result["rows"] == 8 and result["events"] == 8


def test_normalized_slices_count_distinct_matches(store: Store, tmp_path: Path) -> None:
    result = export(store, tmp_path, "slices")
    assert result["rows"] >= 10
    assert result["events"] == 8


def test_a_filter_narrows_the_count(store: Store, tmp_path: Path) -> None:
    result = export(store, tmp_path, "events", event_ids=(16385361, 17199139))
    assert (result["rows"], result["events"]) == (2, 2)


def test_raw_events_keep_their_count(store: Store, tmp_path: Path) -> None:
    result = export(store, tmp_path, "events", schema="raw")
    assert result["events"] == 8
