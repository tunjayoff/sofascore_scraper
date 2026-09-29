"""Yenileme politikası (src/refresh.py): geçici kayıtlar /event ile yeniden okunur, değişim loglanır."""
import copy
import datetime as dt
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.match_data_fetcher import DETAIL_SLICE_KEYS, UNAVAILABLE_AFTER_ATTEMPTS, UNAVAILABLE_FILE, MatchDataFetcher
from src.refresh import SCORE_CHANGES_FILE, diff_basic
from src.status import OBSERVATION_KEY
from src.web.progress import JobProgress

FIXTURES = Path(__file__).parent / "fixtures" / "status"
MID = "16950622"


def _fixture(rel: str) -> dict:
    event = json.loads((FIXTURES / f"{rel}.json").read_text(encoding="utf-8"))
    event["id"] = event["event_id"]
    return event


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("REFRESH_WINDOW_HOURS", raising=False)
    monkeypatch.delenv("REFRESH_LEGACY", raising=False)
    monkeypatch.delenv("REFRESH_MIN_INTERVAL_HOURS", raising=False)


def _fetcher(tmp_path) -> MatchDataFetcher:
    return MatchDataFetcher(MagicMock(), data_dir=str(tmp_path))


def _store(f: MatchDataFetcher, basic: dict, observed_after_start_h=None) -> Path:
    """Dilimleri tam sayılan (hepsi 'yok' işaretli) bir kayıt; observation isteğe bağlı."""
    data = {"basic": basic}
    if observed_after_start_h is not None:
        data[OBSERVATION_KEY] = {
            "observed_at_utc": _iso(basic["startTimestamp"] + observed_after_start_h * 3600),
            "change_ts": basic["changes"]["changeTimestamp"],
        }
    f._save_match_data(str(basic["id"]), data)
    match_dir = next(Path(f.match_details_dir).rglob("basic.json")).parent
    (match_dir / UNAVAILABLE_FILE).write_text(json.dumps({k: UNAVAILABLE_AFTER_ATTEMPTS for k in DETAIL_SLICE_KEYS}))
    return match_dir


def _rows(tmp_path) -> list:
    path = Path(tmp_path) / SCORE_CHANGES_FILE
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# --- ihtiyaç hesabı ---------------------------------------------------------------------

def test_need_without_observation_is_none(tmp_path):
    f = _fetcher(tmp_path)
    _store(f, _fixture("football/F2_penalties__16950622"))
    assert f._compute_detail_need(MID) == "none"


def test_need_within_window_is_refresh(tmp_path):
    f = _fetcher(tmp_path)
    _store(f, _fixture("football/F2_penalties__16950622"), observed_after_start_h=2)
    assert f._compute_detail_need(MID) == "refresh"


def test_need_after_window_is_none(tmp_path):
    f = _fetcher(tmp_path)
    _store(f, _fixture("football/F2_penalties__16950622"), observed_after_start_h=72.5)
    assert f._compute_detail_need(MID) == "none"


def test_need_with_policy_off_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("REFRESH_WINDOW_HOURS", "0")
    f = _fetcher(tmp_path)
    _store(f, _fixture("football/F2_penalties__16950622"), observed_after_start_h=2)
    assert f._compute_detail_need(MID) == "none"


def test_window_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("REFRESH_WINDOW_HOURS", "6")
    f = _fetcher(tmp_path)
    _store(f, _fixture("football/F2_penalties__16950622"), observed_after_start_h=7)
    assert f._compute_detail_need(MID) == "none"


@pytest.mark.parametrize("hours_since_observed,min_interval,expected", [
    (1, None, "none"),  # varsayılan 6 sa dolmadı
    (7, None, "refresh"),
    (1, "0", "refresh"),  # alt sınır kapalı
    (1, "0.5", "refresh"),
])
def test_min_interval_between_refreshes(tmp_path, monkeypatch, hours_since_observed, min_interval, expected):
    if min_interval is not None:
        monkeypatch.setenv("REFRESH_MIN_INTERVAL_HOURS", min_interval)
    f = _fetcher(tmp_path)
    basic = _fixture("football/F2_penalties__16950622")
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    basic["startTimestamp"] = int(now - 10 * 3600)  # pencere açık: başlangıçtan 10 sa
    _store(f, basic, observed_after_start_h=10 - hours_since_observed)
    assert f._compute_detail_need(MID) == expected


def test_missing_slices_still_come_before_refresh(tmp_path):
    f = _fetcher(tmp_path)
    match_dir = _store(f, _fixture("football/F2_penalties__16950622"), observed_after_start_h=2)
    (match_dir / UNAVAILABLE_FILE).unlink()
    assert f._compute_detail_need(MID) == "refill"


# --- yenileme ---------------------------------------------------------------------------

def test_changed_penalties_are_logged_with_old_and_new(tmp_path):
    f = _fetcher(tmp_path)
    old = _fixture("football/F2_penalties__16950622")
    match_dir = _store(f, old, observed_after_start_h=2)
    new = copy.deepcopy(old)
    new["awayScore"]["penalties"] = 5
    new["changes"] = {"changes": ["awayScore.penalties"], "changeTimestamp": old["changes"]["changeTimestamp"] + 600}

    with patch.object(f, "_fetch_match_basic", return_value=new) as fetch:
        data = f.refresh_match(MID)
    fetch.assert_called_once_with(MID)  # yalnızca /event, dilim yok

    rows = _rows(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == 16950622
    assert row["changed"] == {"awayScore.penalties": [6, 5]}
    assert row["old_change_ts"] == old["changes"]["changeTimestamp"]
    assert row["new_change_ts"] == new["changes"]["changeTimestamp"]
    assert row["start_ts"] == old["startTimestamp"]
    assert row["hours_after_start"] == round((new["changes"]["changeTimestamp"] - old["startTimestamp"]) / 3600, 2)
    assert set(row) >= {"ts_utc", "sport", "tournament", "tier_hint"}
    assert json.loads((match_dir / "basic.json").read_text())["awayScore"]["penalties"] == 5
    obs = json.loads((match_dir / f"{OBSERVATION_KEY}.json").read_text())
    assert obs["change_ts"] == new["changes"]["changeTimestamp"]
    assert data[OBSERVATION_KEY] == obs


def test_unchanged_refresh_only_updates_observation(tmp_path):
    f = _fetcher(tmp_path)
    old = _fixture("football/F2_penalties__16950622")
    match_dir = _store(f, old, observed_after_start_h=2)
    before_obs = json.loads((match_dir / f"{OBSERVATION_KEY}.json").read_text())
    basic_mtime = (match_dir / "basic.json").stat().st_mtime_ns

    with patch.object(f, "_fetch_match_basic", return_value=copy.deepcopy(old)):
        f.refresh_match(MID)

    assert _rows(tmp_path) == []
    assert (match_dir / "basic.json").stat().st_mtime_ns == basic_mtime
    obs = json.loads((match_dir / f"{OBSERVATION_KEY}.json").read_text())
    assert obs["observed_at_utc"] > before_obs["observed_at_utc"]
    # Pencere artık kapandı (şimdi ≥ başlangıç + 72 sa): kayıt kesin
    f.end_job_cache()
    assert f._compute_detail_need(MID) == "none"


def test_status_regression_is_flagged_not_deleted(tmp_path):
    f = _fetcher(tmp_path)
    old = _fixture("basketball/B6_finished_regular__16484334")
    match_dir = _store(f, old, observed_after_start_h=2)
    new = _fixture("basketball/B9_abandoned__17060394")
    new["id"] = old["id"]

    with patch.object(f, "_fetch_match_basic", return_value=new):
        f.refresh_match(str(old["id"]))

    row = _rows(tmp_path)[0]
    assert row["status_regressed"] is True
    assert row["status_class"] == ["completed", "void"]
    assert row["changed"]["status.code"] == [100, 90]
    obs = json.loads((match_dir / f"{OBSERVATION_KEY}.json").read_text())
    assert obs["status_regressed"] is True
    assert (match_dir / "basic.json").exists()


def test_failed_fetch_leaves_record_untouched(tmp_path):
    f = _fetcher(tmp_path)
    match_dir = _store(f, _fixture("football/F2_penalties__16950622"), observed_after_start_h=2)
    before = (match_dir / f"{OBSERVATION_KEY}.json").read_text()
    with patch.object(f, "_fetch_match_basic", return_value=None):
        assert f.refresh_match(MID) is None
    assert (match_dir / f"{OBSERVATION_KEY}.json").read_text() == before


def test_diff_covers_all_score_subfields_status_winner_and_start():
    old = _fixture("football/F2_penalties__16950622")
    new = copy.deepcopy(old)
    new["homeScore"]["extra1"] = 1
    new["winnerCode"] = 2
    new["startTimestamp"] += 60
    new["status"]["description"] = "Ended"
    assert diff_basic(old, new) == {
        "homeScore.extra1": [None, 1],
        "startTimestamp": [old["startTimestamp"], old["startTimestamp"] + 60],
        "status.description": ["AP", "Ended"],
        "winnerCode": [1, 2],
    }


# --- eski kayıtlar ve toplu çekim ---------------------------------------------------------

def test_legacy_record_is_untouched_without_flag(tmp_path):
    f = _fetcher(tmp_path)
    _store(f, _fixture("football/F2_penalties__16950622"))
    with patch.object(f, "_fetch_match_basic") as fetch, patch.object(f, "fetch_match_data") as full:
        assert f.fetch_matches_batch([MID]) == {}
    fetch.assert_not_called()
    full.assert_not_called()
    assert f.refresh_due_ids() == []


def test_legacy_record_refreshes_once_with_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("REFRESH_LEGACY", "true")
    f = _fetcher(tmp_path)
    old = _fixture("football/F2_penalties__16950622")
    _store(f, old)
    assert f.refresh_due_ids() == [MID]
    with patch.object(f, "_fetch_match_basic", return_value=copy.deepcopy(old)):
        stats = f.refresh_matches([MID])
    assert stats == {"refreshed": 1, "changed": 0, "failed": 0}
    # observation yazıldı ve pencere çoktan kapalı: bir daha yenilenmez
    f.end_job_cache()
    assert f._compute_detail_need(MID) == "none"


def test_batch_refreshes_after_new_matches_and_counts_separately(tmp_path):
    f = _fetcher(tmp_path)
    old = _fixture("football/F2_penalties__16950622")
    _store(f, old, observed_after_start_h=2)
    order = []
    f.fetch_match_data = MagicMock(side_effect=lambda mid: order.append(("full", mid)) or {"basic": {"id": mid}})
    f.refresh_match = MagicMock(side_effect=lambda mid: order.append(("refresh", mid)) or {"basic": old})
    with patch("src.match_data_fetcher.time.sleep"):
        f.fetch_matches_batch([MID, "999"])
    assert order == [("full", "999"), ("refresh", MID)]
    assert f.pending_detail_ids([MID, "999"]) == ["999", MID]


def test_job_progress_counts_refreshes():
    published = []
    p = JobProgress(["details"], published.append)
    p.start_phase("details", 3)
    p.add_refreshed("1", changed=False)
    p.add_refreshed("2", changed=True)
    assert p.detail()["refreshed"] == 2 and p.detail()["refresh_changed"] == 1
    assert p.result()["refreshed"] == 2 and p.result()["refresh_changed"] == 1


def test_refresh_listener_is_called(tmp_path):
    f = _fetcher(tmp_path)
    old = _fixture("football/F2_penalties__16950622")
    _store(f, old, observed_after_start_h=2)
    calls = []
    f.refresh_listener = lambda mid, changed: calls.append((mid, changed))
    with patch.object(f, "_fetch_match_basic", return_value=copy.deepcopy(old)):
        f.refresh_match(MID)
    assert calls == [(MID, False)]


def test_settings_expose_refresh_window(monkeypatch):
    from fastapi.testclient import TestClient

    from src.web.app import app

    client = TestClient(app)
    assert client.get("/api/settings").json()["refresh_window_hours"] == 72
    monkeypatch.setenv("REFRESH_WINDOW_HOURS", "24")
    assert client.get("/api/settings").json()["refresh_window_hours"] == 24
    assert client.post("/api/settings", json={"refresh_window_hours": -1}).status_code == 422
