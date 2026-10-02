"""
Gözlem: bizim gözlem anımız + changes.changeTimestamp (kesinlik takibi için). Eski düzende `observation.json`;
indirici artık Store'a yazar (plan maddesi ST-21): gözlem manifestte ve katalogda (`observed_at`, `change_ts`).
"""
import datetime as dt
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import legacy_writer
from src.match_data_fetcher import MatchDataFetcher
from src.status import OBSERVATION_KEY, observation_record, read_observation
from src.store import open_store

FIXTURE = Path(__file__).parent / "fixtures" / "status" / "basketball" / "K6_score_changed_after_finished__17006262.json"


def _event() -> dict:
    event = json.loads(FIXTURE.read_text(encoding="utf-8"))
    event["id"] = event["event_id"]  # SofaScore yükünde kimlik `id`'dir; depo kimliği dizin adına uymayan kaydı maç saymaz
    return event


def _fetcher(tmp_path) -> MatchDataFetcher:
    return MatchDataFetcher(MagicMock(), data_dir=str(tmp_path))


def _serving(event: dict) -> Any:
    """Sahte SofaScore (tests/fakes/sofascore.py): yalnızca maçın /event'i; dilimleri 404 (P13: boru hattından)."""
    from fakes.sofascore import FakeSofaScore

    fake = FakeSofaScore()
    fake.add_event(event)
    return fake


def test_observation_record_fields():
    event = _event()
    when = dt.datetime(2026, 9, 29, 18, 0, 5, tzinfo=dt.timezone.utc)
    assert observation_record(event, when) == {
        "observed_at_utc": "2026-09-29T18:00:05+00:00",
        "change_ts": event["changes"]["changeTimestamp"],
    }


def test_fetch_saves_observation_with_the_match(tmp_path):
    f = _fetcher(tmp_path)
    event = _event()
    with _serving(event):
        data = f.fetch_match_data(event["event_id"])
    assert data[OBSERVATION_KEY]["change_ts"] == event["changes"]["changeTimestamp"]
    assert data[OBSERVATION_KEY]["observed_at_utc"].endswith("+00:00")

    row = open_store(tmp_path).events.get(event["event_id"])
    assert row.layout == "v3" and row.change_ts == event["changes"]["changeTimestamp"]
    observed = dt.datetime.fromisoformat(data[OBSERVATION_KEY]["observed_at_utc"]).timestamp()
    assert row.observed_at == int(observed)  # katalog tam saniye tutar
    assert not list(Path(tmp_path).rglob(f"{OBSERVATION_KEY}.json"))  # eski düzene yazılmaz
    loaded = f._load_match_data_from_dir("", str(event["event_id"]))
    assert read_observation(loaded) == data[OBSERVATION_KEY]


def test_old_records_without_observation_read_as_none(tmp_path):
    f = _fetcher(tmp_path)
    event = _event()
    legacy_writer.save_legacy(tmp_path, event["event_id"], {"basic": event})  # gözlemden önceki bir sürümün kaydı
    loaded = f._load_match_data_from_dir("", str(event["event_id"]))
    assert "basic" in loaded
    assert read_observation(loaded) == {"observed_at_utc": None, "change_ts": None}
    assert read_observation(None) == {"observed_at_utc": None, "change_ts": None}
