"""
Yenileme politikası (sofascore_scraper/refresh.py): geçici kayıtlar /event ile yeniden okunur, değişim loglanır.

Kayıtlar Store'dadır (plan maddesi ST-21): yenileme `Store.events.observe` ile yazar, değişiklik satırı
`changes/<yyyy>-<mm>.jsonl` parçasına gider. Gözlemi olmayan kayıt yalnızca eski düzende olabilir (önceki
sürümlerin yazdığı); o yüzden o kayıtlar tests/legacy_writer.py ile eski düzende kurulur.
"""
import contextlib
import copy
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Iterator, List
from unittest.mock import MagicMock

import pytest

import legacy_writer
import store_dump
from sofascore_scraper.match_data_fetcher import (DETAIL_SLICE_KEYS, SLICE_EMPTY, UNAVAILABLE_AFTER_ATTEMPTS, UNAVAILABLE_FILE,
                                    MatchDataFetcher, SliceOutcome)
from sofascore_scraper.refresh import SCORE_CHANGES_FILE, diff_basic
from sofascore_scraper.sports import event_sport_slug, slices_for
from sofascore_scraper.status import OBSERVATION_KEY
from sofascore_scraper.store import open_store
from sofascore_scraper.jobs.progress import JobProgress

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


@contextlib.contextmanager
def _serving(*events: Any) -> Iterator[Any]:
    """Sahte SofaScore (tests/fakes/sofascore.py): yalnızca verilen maçların /event'i var (None: hiçbiri)."""
    from fakes.sofascore import FakeSofaScore

    fake = FakeSofaScore()
    for event in events:
        if event is not None:
            fake.add_event(copy.deepcopy(event))
    with fake:
        yield fake


def _api(fake: Any) -> List[str]:
    from fakes.sofascore import SITE_ROOT

    return [r.path for r in fake.requests if r.path != SITE_ROOT]


def _fetcher(tmp_path) -> MatchDataFetcher:
    return MatchDataFetcher(MagicMock(), data_dir=str(tmp_path))


def _store(f: MatchDataFetcher, basic: dict, observed_after_start_h=None) -> Path:
    """
    Dilimleri tam sayılan (hepsi 'yok' işaretli) bir kayıt; kaydın dizinini döndürür.

    Gözlem anı verilirse kayıt indiricinin yazıcısıyla Store'a (v3) yazılır: gözlem o an, her `required` dilim
    iki kez 404 almış. Verilmezse önceki bir sürümün yazdığı gözlemsiz eski kayıt kurulur (eski düzen,
    `_unavailable.json` doğrulanmamış sayımlarla).
    """
    mid = str(basic["id"])
    if observed_after_start_h is None:
        match_dir = Path(legacy_writer.save_legacy(f.data_dir, mid, {"basic": basic}))
        (match_dir / UNAVAILABLE_FILE).write_text(
            json.dumps({k: UNAVAILABLE_AFTER_ATTEMPTS for k in DETAIL_SLICE_KEYS}))
        return match_dir
    keys = [d.key for d in slices_for(event_sport_slug(basic), required_only=True)]
    data = {"basic": basic, **dict.fromkeys(keys), OBSERVATION_KEY: {
        "observed_at_utc": _iso(basic["startTimestamp"] + observed_after_start_h * 3600),
        "change_ts": basic["changes"]["changeTimestamp"],
    }}
    outcomes = {k: SliceOutcome(SLICE_EMPTY, reason="404", http_status=404) for k in keys}
    for _ in range(UNAVAILABLE_AFTER_ATTEMPTS):
        f._save_match_data(mid, data, outcomes)
    return Path(f._find_match_path(mid)[2])


def _rows(f: MatchDataFetcher) -> list:
    """Değişiklik günlüğünün satırları (Store, iki düzen birlikte), sıra numarasıyla sıralı."""
    return [dict(change.row) for change in open_store(f.data_dir).changes.list()]


def _row(f: MatchDataFetcher, mid: str = MID):
    return open_store(f.data_dir).events.get(int(mid))


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
    _store(f, _fixture("football/F2_penalties__16950622"), observed_after_start_h=2)
    assert f.reset_unavailable_markers(include_confirmed=True)["matches"] == 1  # dilimler yeniden beklenir
    assert f._compute_detail_need(MID) == "refill"


# --- yenileme ---------------------------------------------------------------------------

def test_changed_penalties_are_logged_with_old_and_new(tmp_path):
    f = _fetcher(tmp_path)
    old = _fixture("football/F2_penalties__16950622")
    match_dir = _store(f, old, observed_after_start_h=2)
    new = copy.deepcopy(old)
    new["awayScore"]["penalties"] = 5
    new["changes"] = {"changes": ["awayScore.penalties"], "changeTimestamp": old["changes"]["changeTimestamp"] + 600}

    with _serving(new) as fake:
        data = f.refresh_match(MID)
    assert _api(fake) == [f"/event/{MID}"]  # yalnızca /event, dilim yok

    rows = _rows(f)
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == 16950622
    assert row["changed"] == {"awayScore.penalties": [6, 5]}
    assert row["old_change_ts"] == old["changes"]["changeTimestamp"]
    assert row["new_change_ts"] == new["changes"]["changeTimestamp"]
    assert row["start_ts"] == old["startTimestamp"]
    assert row["hours_after_start"] == round((new["changes"]["changeTimestamp"] - old["startTimestamp"]) / 3600, 2)
    assert set(row) >= {"ts_utc", "sport", "tournament", "tier_hint"}
    store = open_store(f.data_dir)
    assert store.events.payload(int(MID))["awayScore"]["penalties"] == 5
    assert store.events.get(int(MID)).change_ts == new["changes"]["changeTimestamp"]
    assert data[OBSERVATION_KEY]["change_ts"] == new["changes"]["changeTimestamp"]
    # Satır Store'un değişiklik günlüğüne gider (v3 parçası, LF satır sonu); eski dosyaya bir şey eklenmez
    segments = sorted((tmp_path / "changes").glob("*.jsonl"))
    assert len(segments) == 1 and segments[0].read_bytes().count(b"\n") == 1 and b"\r" not in segments[0].read_bytes()
    assert not (tmp_path / SCORE_CHANGES_FILE).exists()
    assert (match_dir / "manifest.json").is_file()


def test_unchanged_refresh_only_updates_observation(tmp_path):
    f = _fetcher(tmp_path)
    old = _fixture("football/F2_penalties__16950622")
    match_dir = _store(f, old, observed_after_start_h=2)
    before = _row(f)
    payload_mtime = (match_dir / "event.json.gz").stat().st_mtime_ns

    with _serving(copy.deepcopy(old)):
        f.refresh_match(MID)

    assert _rows(f) == []
    assert (match_dir / "event.json.gz").stat().st_mtime_ns == payload_mtime  # yük aynı: dosya yazılmadı
    assert _row(f).observed_at > before.observed_at
    # Pencere artık kapandı (şimdi ≥ başlangıç + 72 sa): kayıt kesin
    f.end_job_cache()
    assert f._compute_detail_need(MID) == "none"


def test_status_regression_is_flagged_not_deleted(tmp_path):
    f = _fetcher(tmp_path)
    old = _fixture("basketball/B6_finished_regular__16484334")
    _store(f, old, observed_after_start_h=2)
    new = _fixture("basketball/B9_abandoned__17060394")
    new["id"] = old["id"]

    with _serving(new):
        data = f.refresh_match(str(old["id"]))

    row = _rows(f)[0]
    assert row["status_regressed"] is True
    assert row["status_class"] == ["completed", "void"]
    assert row["changed"]["status.code"] == [100, 90]
    assert _row(f, str(old["id"])).status_regressed is True
    assert data[OBSERVATION_KEY]["status_regressed"] is True
    assert open_store(f.data_dir).events.payload(old["id"]) == new  # kayıt silinmedi, son hali saklandı


def test_failed_fetch_leaves_record_untouched(tmp_path):
    f = _fetcher(tmp_path)
    _store(f, _fixture("football/F2_penalties__16950622"), observed_after_start_h=2)
    before = _row(f)
    with _serving(None):
        assert f.refresh_match(MID) is None
    assert _row(f) == before


def test_a_record_of_the_old_layout_is_promoted_by_its_refresh_and_its_folder_is_kept(tmp_path, monkeypatch):
    """
    Eski düzendeki kayıt yenilenirken önce v3'e yükseltilir (karar S3); eski dizine dokunulmaz (karar 4): mantıksal
    döküm yükseltmeden önceki kaydın aynısıdır, yalnızca gözlem ve olay yükü yenidir.
    """
    monkeypatch.setenv("REFRESH_LEGACY", "true")
    f = _fetcher(tmp_path)
    old = _fixture("football/F2_penalties__16950622")
    match_dir = _store(f, old)
    files_before = {p.name: p.read_bytes() for p in match_dir.iterdir()}
    new = copy.deepcopy(old)
    new["homeScore"]["penalties"] = 4
    before = store_dump.dump(tmp_path)["events"][MID]

    with _serving(new):
        assert f.refresh_match(MID) is not None

    assert {p.name: p.read_bytes() for p in match_dir.iterdir()} == files_before
    row = _row(f)
    assert (row.layout, row.legacy_path) == ("v3", Path(os.path.relpath(match_dir, tmp_path)).as_posix())
    after = store_dump.dump(tmp_path)["events"][MID]
    assert after["slices"].keys() == before["slices"].keys()
    assert {k: v for k, v in after["slices"].items() if k != "event"} == {
        k: v for k, v in before["slices"].items() if k != "event"}
    assert after["observation"] is not None and before["observation"] is None
    assert [r["changed"] for r in _rows(f)] == [{"homeScore.penalties": [old["homeScore"].get("penalties"), 4]}]


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
    with _serving() as fake:
        assert f.fetch_matches_batch([MID]) == {}
    assert fake.requests == []
    assert f.refresh_due_ids() == []


def test_legacy_record_refreshes_once_with_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("REFRESH_LEGACY", "true")
    f = _fetcher(tmp_path)
    old = _fixture("football/F2_penalties__16950622")
    _store(f, old)
    assert f.refresh_due_ids() == [MID]
    with _serving(copy.deepcopy(old)):
        stats = f.refresh_matches([MID])
    assert stats == {"refreshed": 1, "changed": 0, "failed": 0}
    # observation yazıldı ve pencere çoktan kapalı: bir daha yenilenmez
    f.end_job_cache()
    assert f._compute_detail_need(MID) == "none"


def test_batch_refreshes_after_new_matches_and_counts_separately(tmp_path):
    f = _fetcher(tmp_path)
    old = _fixture("football/F2_penalties__16950622")
    _store(f, old, observed_after_start_h=2)
    assert f.pending_detail_ids([MID, "999"]) == ["999", MID]
    with _serving(old) as fake:  # 999 SofaScore'da yok: tam çekim denenir ve başarısız olur
        f.fetch_matches_batch([MID, "999"])
    assert _api(fake) == ["/event/999", f"/event/{MID}"]  # önce yeni maç, sonra yenileme


def test_no_fixed_pauses_between_matches(tmp_path):
    """
    Maçlar arasındaki sabit beklemeler kalktı (tek tek indirmede 0,2 sn, --refresh-only'de 1 sn,
    100'lük gruplar arasında 1 sn; gruplar da P13'te kalktı): hızı ortak istek bütçesi (sofascore_scraper/throttle.py) belirler.
    """
    f = _fetcher(tmp_path)
    old = _fixture("football/F2_penalties__16950622")
    _store(f, old, observed_after_start_h=2)
    others = [dict(copy.deepcopy(old), id=mid) for mid in (997, 998, 999)]
    with _serving(old, *others) as fake:
        assert len(f.fetch_matches_batch(["997", "998", "999"])) == 3
        assert f.refresh_matches([MID, MID, MID])["refreshed"] == 3
        assert f.fetch_detail_ids([str(n) for n in range(1, 251)]) == 0  # 250 maç, hepsi 404: tek oturum
        assert len(fake.sessions) == 3
    assert fake.slept("sofascore_scraper.match_data_fetcher") == [] and fake.slept("sofascore_scraper.services.pipeline") == []


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
    with _serving(copy.deepcopy(old)):
        f.refresh_match(MID)
    assert calls == [(MID, False)]


def test_settings_expose_refresh_window(monkeypatch):
    from fastapi.testclient import TestClient

    from sofascore_scraper.web.app import app

    client = TestClient(app)

    def window() -> Any:
        rows = client.get("/api/v1/settings").json()["data"]["settings"]
        return next(row["value"] for row in rows if row["key"] == "refresh.window_hours")

    assert window() == 72
    assert client.patch("/api/v1/settings", json={"values": {"refresh.window_hours": -1}}).status_code == 422
    monkeypatch.setenv("REFRESH_WINDOW_HOURS", "24")
    assert window() == 24
