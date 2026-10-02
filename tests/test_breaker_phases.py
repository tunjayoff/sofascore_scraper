"""
Devre kesiciye her aşama bakar: toplu detay indirme (yenileme dahil), seçili maçların sync yolu,
CLI --refresh-only döngüsü ve web işinin sezon / maç programı / detay aşamaları.

Eskiden yalnızca toplu detay indirmenin ilk isteği (/event) sayılıyordu; engellenmiş bir IP'den
kalan her maç, sezon ve lig için istek atılmaya devam ediliyordu.

Gerçek ağ yok: curl taşıyıcısı (cffi_requests.get) sahte; istek katmanının kendisi gerçek, çünkü
kesiciyi besleyen odur.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.utils as utils
from src import breaker as request_breaker
from src.match_data_fetcher import DETAIL_SLICE_KEYS, UNAVAILABLE_AFTER_ATTEMPTS, UNAVAILABLE_FILE, MatchDataFetcher
from src.status import OBSERVATION_KEY

CFG = {"max_retries": 3, "request_timeout": 5, "wait_time_min": 0, "wait_time_max": 0}
START = 1790000000


class Resp:
    def __init__(self, code: int, body: Any = None, text: str = ""):
        self.status_code = code
        self.reason = "x"
        self.headers: Dict[str, str] = {}
        self._body = body
        self.text = text

    def json(self):
        return self._body


@contextlib.contextmanager
def _request_layer():
    """Gerçek istek katmanı, beklemesiz ve proxy'siz."""
    async def no_asleep(_sec):
        return None

    with patch.object(utils, "_get_runtime_request_config", return_value=CFG), \
            patch.object(utils, "_get_proxy_config", return_value=(False, "")), \
            patch.object(utils, "_asleep", side_effect=no_asleep), \
            patch.object(utils, "_sleep", side_effect=lambda _sec: None):
        yield


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("IGNORE_RATE_LIMIT", raising=False)
    for key in ("REFRESH_WINDOW_HOURS", "REFRESH_LEGACY", "REFRESH_MIN_INTERVAL_HOURS"):
        monkeypatch.delenv(key, raising=False)


# --- toplu detay indirme ve yenileme --------------------------------------------------------

def _basic(mid: int) -> dict:
    return {
        "id": mid,
        "tournament": {"name": "Cup", "uniqueTournament": {"id": 77, "name": "Cup"},
                       "category": {"sport": {"slug": "football"}}},
        "season": {"id": 5, "name": "Cup 26", "year": "26"},
        "status": {"code": 100, "description": "Ended", "type": "finished"},
        "homeTeam": {"id": 1, "name": "A"},
        "awayTeam": {"id": 2, "name": "B"},
        "homeScore": {"current": 1},
        "awayScore": {"current": 0},
        "startTimestamp": START,
        "changes": {"changeTimestamp": START + 7000},
    }


def _fetcher(tmp_path, threshold: int = 3) -> MatchDataFetcher:
    cfg = MagicMock()
    cfg.get_rate_limit_threshold_consecutive.return_value = threshold
    cfg.get_rate_limit_threshold_ratio.return_value = 2.0  # oran kuralı kapalı
    cfg.get_server_error_threshold_consecutive.return_value = 1000
    cfg.get_max_concurrent.return_value = 1
    return MatchDataFetcher(cfg, data_dir=str(tmp_path))


def _store_provisional(f: MatchDataFetcher, ids: List[int]) -> None:
    """Dilimleri tam sayılan, yenileme penceresi açık kayıtlar: ihtiyaç "refresh"."""
    observed = dt.datetime.fromtimestamp(START + 2 * 3600, dt.timezone.utc).isoformat(timespec="seconds")
    for mid in ids:
        basic = _basic(mid)
        f._save_match_data(str(mid), {
            "basic": basic,
            OBSERVATION_KEY: {"observed_at_utc": observed, "change_ts": basic["changes"]["changeTimestamp"]},
        })
        match_dir = next(p.parent for p in Path(f.match_details_dir).rglob("basic.json") if p.parent.name == str(mid))
        (match_dir / UNAVAILABLE_FILE).write_text(json.dumps({k: UNAVAILABLE_AFTER_ATTEMPTS for k in DETAIL_SLICE_KEYS}))
    assert all(f._compute_detail_need(str(mid)) == "refresh" for mid in ids)


@contextlib.asynccontextmanager
async def _fake_session():
    yield MagicMock()


def test_breaker_trips_on_a_blocked_refresh_batch(tmp_path):
    """Eskiden yenileme başarısızlığı sayılmıyordu: engelliyken kalan her maç için istek atılıyordu."""
    f = _fetcher(tmp_path, threshold=3)
    ids = list(range(1001, 1021))
    _store_provisional(f, ids)
    failed: List[str] = []

    with _request_layer(), \
            patch.object(utils.cffi_requests, "get", return_value=Resp(403, text="no")) as get, \
            patch("src.utils.create_session_async", _fake_session), \
            patch("src.match_data_fetcher.asyncio.sleep", new=AsyncMock()):
        results = asyncio.run(f.fetch_matches_batch_async(ids, max_concurrent=1, failed_callback=failed.append))

    assert results == {}
    assert f.rate_limit_breaker_triggered is True
    assert f.last_status_counts.get("403") == 3
    assert get.call_count == 3 * CFG["max_retries"]  # 3 maç × 3 deneme; kalan 17 maç için istek yok
    assert failed == ["1001", "1002"]  # devreyi kesen ve hiç denenmeyen maçlar "başarısız" sayılmaz


def test_refresh_only_loop_stops_when_the_breaker_trips(tmp_path):
    """CLI --refresh-only döngüsünün eskiden hiç kesicisi yoktu."""
    f = _fetcher(tmp_path, threshold=3)
    ids = [str(n) for n in range(3001, 3013)]
    _store_provisional(f, [int(i) for i in ids])

    with _request_layer(), \
            patch.object(utils.cffi_requests, "get", return_value=Resp(403, text="no")) as get, \
            patch("src.match_data_fetcher.time.sleep"):
        stats = f.refresh_matches(ids)

    assert stats == {"refreshed": 0, "changed": 0, "failed": 3, "breaker": "403", "skipped": 9}
    assert get.call_count == 3 * CFG["max_retries"]
    assert f.rate_limit_breaker_triggered is True
    assert request_breaker.current() is None  # kesici çağrıyla birlikte kapandı


def test_refresh_only_loop_reports_no_breaker_when_requests_succeed(tmp_path):
    f = _fetcher(tmp_path, threshold=3)
    _store_provisional(f, [3001, 3002])

    def get(url, **_kw):
        return Resp(200, {"event": copy.deepcopy(_basic(int(url.rsplit("/", 1)[1])))})

    with _request_layer(), patch.object(utils.cffi_requests, "get", side_effect=get), \
            patch("src.match_data_fetcher.time.sleep"):
        stats = f.refresh_matches(["3001", "3002"])
    assert stats == {"refreshed": 2, "changed": 0, "failed": 0}


def test_sync_detail_loop_stops_when_the_breaker_trips(tmp_path):
    f = _fetcher(tmp_path, threshold=3)
    failed: List[str] = []
    with _request_layer(), \
            patch.object(utils.cffi_requests, "get", return_value=Resp(403, text="no")) as get, \
            patch("src.match_data_fetcher.time.sleep"):
        results = f.fetch_matches_batch([str(n) for n in range(1, 11)], failed_callback=failed.append)
    assert results == {} and f.rate_limit_breaker_triggered is True
    assert get.call_count == 3 * CFG["max_retries"]
    assert failed == ["1", "2"]


def test_cli_refresh_only_exits_with_2_when_the_breaker_trips(tmp_path, monkeypatch, capsys):
    import os

    import main as cli

    f = _fetcher(tmp_path)
    _store_provisional(f, list(range(4001, 4031)))
    monkeypatch.setenv("DATA_DIR", os.environ["DATA_DIR"])  # main --data-dir ortamı değiştirir: test sonunda geri al
    monkeypatch.setenv("RATE_LIMIT_THRESHOLD_CONSECUTIVE", "3")
    monkeypatch.setattr("sys.argv", ["main.py", "--refresh-only", "--data-dir", str(tmp_path)])
    with _request_layer(), \
            patch.object(utils.cffi_requests, "get", return_value=Resp(403, text="no")) as get, \
            patch("src.match_data_fetcher.time.sleep"):
        assert cli.main() == 2
    assert get.call_count == 3 * CFG["max_retries"]
    assert "27" in capsys.readouterr().err  # denenmeyen maç sayısı kullanıcıya söylenir


# --- web işi: her aşama kesiciye bakar -----------------------------------------------------

class FakeDetails:
    """Detay aşaması: çağrılırsa kaydeder (devre önceki aşamada kesildiyse çağrılmamalı)."""

    def __init__(self) -> None:
        self.rate_limit_breaker_triggered = False
        self.last_status_counts: Dict[str, int] = {}
        self.refresh_listener = None
        self.collected: List[Any] = []
        self.fetched: List[List[str]] = []

    def begin_job_cache(self) -> None:
        pass

    def end_job_cache(self) -> None:
        pass

    def collect_detail_match_ids(self, league_id=None, max_seasons=0, only_season_ids=None):
        self.collected.append(league_id)
        return ["m1", "m2"]

    def pending_detail_ids(self, ids):
        return list(ids)

    def fetch_detail_ids(self, ids, progress_callback=None, should_cancel=None, failed_callback=None):
        self.fetched.append(list(ids))
        return len(ids)


@pytest.fixture
def job_env(tmp_path, monkeypatch):
    import src.web.fetch_job as fj
    from src.web.jobs import JobStore

    store = JobStore(str(tmp_path / "jobs.db"))
    monkeypatch.setattr(fj, "_job_store", store)
    monkeypatch.setattr(fj, "_refresh_scraper_state", lambda: store.snapshot())
    monkeypatch.setenv("RATE_LIMIT_THRESHOLD_CONSECUTIVE", "3")
    monkeypatch.setenv("RATE_LIMIT_THRESHOLD_RATIO", "2")
    return fj, store


def _run_job(fj, store, monkeypatch, ui, payload: Dict[str, Any]) -> Dict[str, Any]:
    from src.web.routes.scrape import FetchRequest

    # `ui` servis bağlamının (ServiceContext) yerini tutar; işin CSV aşaması yok (EX-1), dışa aktarma çağrılırsa ona gider
    ui.config = fj.config_manager
    monkeypatch.setattr(fj, "build_context", lambda config_manager: ui)
    monkeypatch.setattr("src.services.export.export_all_csv", lambda ctx: ctx.export_all_to_csv())
    req = FetchRequest(**payload)
    job_id = store.create_running(req.model_dump())
    with _request_layer(), patch.object(utils.cffi_requests, "get", return_value=Resp(403, text="no")) as get:
        fj.run_fetch_job(job_id, req)
    final = store.snapshot()
    final["_gets"] = get.call_count
    return final


def test_breaker_trips_on_a_blocked_schedule_phase(job_env, monkeypatch):
    """Maç programı aşaması eskiden engelliyken de her sezon için istek atmaya devam ediyordu."""
    fj, store = job_env
    monkeypatch.setattr(fj.config_manager, "get_leagues", lambda: {17: "Premier League"})
    seasons = [{"id": sid, "name": f"PL {sid}"} for sid in range(1, 13)]
    schedule_calls: List[int] = []

    def fetch_schedule(lid, sid):
        schedule_calls.append(sid)
        # Gerçek MatchFetcher gibi istek katmanından geçer; engelliyken veri gelmez
        return bool(utils.make_api_request(f"/unique-tournament/{lid}/season/{sid}/rounds"))

    md = FakeDetails()
    ui = SimpleNamespace(
        season_fetcher=SimpleNamespace(
            fetch_seasons_for_league=lambda lid: seasons,  # sezon listesi diskten/önceden: istek yok
            get_seasons_for_league=lambda lid: seasons,
            resolve_season_id=lambda lid, sid: sid,
        ),
        match_fetcher=SimpleNamespace(fetch_matches_for_season=fetch_schedule),
        match_data_fetcher=md,
        export_all_to_csv=lambda: None,
    )
    final = _run_job(fj, store, monkeypatch, ui, {"mode": "full", "league_id": 17})

    assert schedule_calls == [1, 2, 3]  # 12 sezondan 3'ü denendi, devre kesildi
    assert final["_gets"] == 3 * CFG["max_retries"]
    assert md.collected == [] and md.fetched == []  # detay aşaması hiç başlamadı
    assert final["circuit_breaker_triggered"] is True and final["circuit_breaker_reason"] == "403"
    assert final["result"]["breaker"] == "403"
    assert final["status"] == "Completed"
    assert "403" in final["current_task"]  # "erken durduruldu" mesajı, "başarıyla tamamlandı" değil
    assert request_breaker.current() is None  # işin kesicisi işle birlikte kapandı


def test_breaker_trips_on_a_blocked_seasons_phase(job_env, monkeypatch):
    fj, store = job_env
    leagues = {lid: f"League {lid}" for lid in range(1, 11)}
    monkeypatch.setattr(fj.config_manager, "get_leagues", lambda: leagues)
    season_calls: List[int] = []
    schedule_calls: List[int] = []

    def fetch_seasons(lid):
        season_calls.append(lid)
        utils.make_api_request(f"/unique-tournament/{lid}/seasons")
        return []

    md = FakeDetails()
    ui = SimpleNamespace(
        season_fetcher=SimpleNamespace(
            fetch_seasons_for_league=fetch_seasons,
            get_seasons_for_league=lambda lid: [{"id": lid * 10, "name": "S"}],
            resolve_season_id=lambda lid, sid: sid,
        ),
        match_fetcher=SimpleNamespace(fetch_matches_for_season=lambda lid, sid: schedule_calls.append(sid) or True),
        match_data_fetcher=md,
        export_all_to_csv=lambda: None,
    )
    final = _run_job(fj, store, monkeypatch, ui, {"mode": "full"})

    assert season_calls == [1, 2, 3]
    assert final["_gets"] == 3 * CFG["max_retries"]
    assert schedule_calls == [] and md.fetched == []  # sonraki aşamalar istek atmadı
    assert final["circuit_breaker_triggered"] is True and final["circuit_breaker_reason"] == "403"


def test_job_with_working_requests_is_not_stopped(job_env, monkeypatch):
    fj, store = job_env
    monkeypatch.setattr(fj.config_manager, "get_leagues", lambda: {17: "Premier League"})
    seasons = [{"id": sid, "name": f"PL {sid}"} for sid in range(1, 6)]
    md = FakeDetails()
    ui = SimpleNamespace(
        season_fetcher=SimpleNamespace(
            fetch_seasons_for_league=lambda lid: seasons,
            get_seasons_for_league=lambda lid: seasons,
            resolve_season_id=lambda lid, sid: sid,
        ),
        match_fetcher=SimpleNamespace(fetch_matches_for_season=lambda lid, sid: True),
        match_data_fetcher=md,
        export_all_to_csv=lambda: None,
    )
    final = _run_job(fj, store, monkeypatch, ui, {"mode": "full", "league_id": 17})
    assert final["status"] == "Completed" and not final.get("circuit_breaker_triggered")
    assert md.fetched == [["m1", "m2"]]


def test_explicit_match_selection_reports_the_breaker(job_env, monkeypatch, tmp_path):
    """Seçili maçların (sync) indirme yolu da kesiciye bakar ve karta bildirir."""
    fj, store = job_env
    monkeypatch.setattr(fj.config_manager, "get_leagues", lambda: {17: "Premier League"})
    md = MatchDataFetcher(fj.config_manager, data_dir=str(tmp_path / "data"))
    ui = SimpleNamespace(match_data_fetcher=md, export_all_to_csv=lambda: None)
    payload = {"mode": "details", "selections": [{"league_id": 17, "match_ids": list(range(1, 11))}]}
    with patch("src.match_data_fetcher.time.sleep"):
        final = _run_job(fj, store, monkeypatch, ui, payload)

    assert final["_gets"] == 3 * CFG["max_retries"]
    assert final["circuit_breaker_triggered"] is True and final["circuit_breaker_reason"] == "403"
    assert final["matches_failed"] == 2
