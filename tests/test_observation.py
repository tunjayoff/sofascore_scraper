"""observation.json: bizim gözlem anımız + changes.changeTimestamp (kesinlik takibi için)."""
import datetime as dt
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.match_data_fetcher import MatchDataFetcher
from src.status import OBSERVATION_KEY, observation_record, read_observation
from src.store import open_store

FIXTURE = Path(__file__).parent / "fixtures" / "status" / "basketball" / "K6_score_changed_after_finished__17006262.json"


def _event() -> dict:
    event = json.loads(FIXTURE.read_text(encoding="utf-8"))
    event["id"] = event["event_id"]  # SofaScore yükünde kimlik `id`'dir; depo kimliği dizin adına uymayan kaydı maç saymaz
    return event


def _fetcher(tmp_path) -> MatchDataFetcher:
    f = MatchDataFetcher(MagicMock(), data_dir=str(tmp_path))
    for name in ("_fetch_match_statistics", "_fetch_team_streaks", "_fetch_pregame_form",
                 "_fetch_h2h", "_fetch_lineups", "_fetch_incidents"):
        setattr(f, name, MagicMock(return_value=None))
    return f


def test_observation_record_fields():
    event = _event()
    when = dt.datetime(2026, 9, 29, 18, 0, 5, tzinfo=dt.timezone.utc)
    assert observation_record(event, when) == {
        "observed_at_utc": "2026-09-29T18:00:05+00:00",
        "change_ts": event["changes"]["changeTimestamp"],
    }


def test_fetch_saves_observation_next_to_basic(tmp_path):
    f = _fetcher(tmp_path)
    event = _event()
    with patch.object(f, "_fetch_match_basic", return_value=event):
        data = f.fetch_match_data(event["event_id"])
    assert data[OBSERVATION_KEY]["change_ts"] == event["changes"]["changeTimestamp"]
    assert data[OBSERVATION_KEY]["observed_at_utc"].endswith("+00:00")

    saved = list(Path(tmp_path).rglob(f"{OBSERVATION_KEY}.json"))
    assert len(saved) == 1 and (saved[0].parent / "basic.json").exists()
    loaded = f._load_match_data_from_dir(str(saved[0].parent), str(event["event_id"]))
    assert read_observation(loaded) == data[OBSERVATION_KEY]


def test_old_records_without_observation_read_as_none(tmp_path):
    f = _fetcher(tmp_path)
    event = _event()
    with patch.object(f, "_fetch_match_basic", return_value=event):
        f.fetch_match_data(event["event_id"])
    obs = next(Path(tmp_path).rglob(f"{OBSERVATION_KEY}.json"))
    obs.unlink()  # bu PR'dan önce kaydedilmiş maç
    open_store(tmp_path).close()  # dosya deponun arkasından silindi: katalog bir sonraki açılışta uzlaşır
    loaded = f._load_match_data_from_dir(str(obs.parent), str(event["event_id"]))
    assert "basic" in loaded
    assert read_observation(loaded) == {"observed_at_utc": None, "change_ts": None}
    assert read_observation(None) == {"observed_at_utc": None, "change_ts": None}
