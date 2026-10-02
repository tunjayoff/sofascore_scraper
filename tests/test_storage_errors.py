"""
Kayıt hataları ve maç dizini: yazılamayan maç "indirildi" sayılmaz.

Sabitlenen kurallar:
  - _save_match_data hatayı yutmaz, StorageError fırlatır. Maç başarısız olarak bildirilir
    (failed_callback); neden kalıcıysa (disk/kota dolu, izin yok, salt okunur) iş durur ve
    kullanıcıya yolu ve nedeni söyleyen bir mesaj verilir.
  - uniqueTournament.id'si olmayan maç sabit bir dizine yazılır
    (match_details/_no_tournament/<spor>/<maç id>); diskteki mevcut kayıt taşınmaz.

Gerçek ağ yok: istek katmanı sahte; disk hataları atomic_write_json'ın sahtesiyle üretilir.
"""
from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import os
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.match_data_fetcher as mdf
import src.utils as utils
from src.exceptions import StorageError
from src.match_data_fetcher import NO_TOURNAMENT_DIR, MatchDataFetcher

MID = "4242"
BASE = "https://www.sofascore.com/api/v1"
H2H = {"teamDuel": {"homeWins": 1, "awayWins": 0, "draws": 0}}


def _basic(mid: str = MID, unique_tournament: bool = True, sport: str = "football") -> dict:
    tournament: Dict[str, Any] = {"id": 900, "name": "Cup", "category": {"sport": {"slug": sport}}}
    if unique_tournament:
        tournament["uniqueTournament"] = {"id": 77, "name": "Cup"}
    return {
        "id": int(mid),
        "tournament": tournament,
        "season": {"id": 5, "name": "Cup 26", "year": "26"},
        "status": {"code": 100, "description": "Ended", "type": "finished"},
        "homeTeam": {"id": 1, "name": "A"},
        "awayTeam": {"id": 2, "name": "B"},
        "homeScore": {"current": 1},
        "awayScore": {"current": 0},
        "startTimestamp": 1790000000,
    }


def _fetcher(tmp_path) -> MatchDataFetcher:
    cfg = MagicMock()
    cfg.get_rate_limit_threshold_consecutive.return_value = 1000
    cfg.get_rate_limit_threshold_ratio.return_value = 2.0
    cfg.get_server_error_threshold_consecutive.return_value = 1000
    cfg.get_max_concurrent.return_value = 1
    return MatchDataFetcher(cfg, data_dir=str(tmp_path))


# --- kayıt hataları ------------------------------------------------------------------------

def _failing_writer(code: int, only_for: str = ""):
    """atomic_write_json'ın sahtesi: (yalnızca `only_for` geçen yollarda) OSError fırlatır."""
    real = mdf.atomic_write_json

    def write(path, data, **kw):
        if only_for in path:
            raise OSError(code, os.strerror(code), path)
        return real(path, data, **kw)

    return write


@contextlib.asynccontextmanager
async def _fake_session():
    yield MagicMock()


def _run_batch(f: MatchDataFetcher, ids: List[str], writer, failed: List[str]) -> List[str]:
    """fetch_matches_batch_async'i sahte istek katmanıyla çalıştırır; istenen URL'leri döndürür."""
    calls: List[str] = []

    async def fake(session, url, max_retries=None, **_kw):
        calls.append(url)
        mid = url.split("/event/", 1)[1].split("/", 1)[0]
        return {"event": _basic(mid)} if url.endswith(f"/event/{mid}") else {}

    with patch("src.utils.make_api_request_async", new=fake), \
            patch("src.utils.create_session_async", _fake_session), \
            patch.object(mdf, "atomic_write_json", side_effect=writer), \
            patch("src.match_data_fetcher.asyncio.sleep", new=AsyncMock()):
        f.last_results = asyncio.run(
            f.fetch_matches_batch_async(ids, max_concurrent=1, failed_callback=failed.append)
        )
    return calls


def test_save_raises_storage_error_instead_of_swallowing(tmp_path):
    f = _fetcher(tmp_path)
    with patch.object(mdf, "atomic_write_json", side_effect=_failing_writer(errno.EIO)):
        with pytest.raises(StorageError) as info:
            f._save_match_data(MID, {"basic": _basic()})
    assert info.value.errno == errno.EIO and not info.value.fatal
    assert MID in (info.value.path or "")


@pytest.mark.parametrize("code,fatal", [
    (errno.ENOSPC, True), (errno.EACCES, True), (errno.EROFS, True), (errno.EIO, False), (errno.ENAMETOOLONG, False),
])
def test_storage_error_fatality(code, fatal):
    assert StorageError.from_exception(OSError(code, os.strerror(code)), "/data").fatal is fatal


def test_async_save_failure_reports_the_match_as_failed_and_continues(tmp_path):
    f = _fetcher(tmp_path)
    failed: List[str] = []
    # Yalnızca 101 numaralı maçın dizinine yazılamıyor (maça özgü, kalıcı olmayan hata)
    calls = _run_batch(f, ["101", "102"], _failing_writer(errno.EIO, only_for=f"{os.sep}101{os.sep}"), failed)

    assert failed == ["101"]
    assert list(f.last_results) == ["102"]  # yazılamayan maç "indirildi" sayılmadı
    assert calls.count(f"{BASE}/event/101") == 1  # yeniden istemek kaydı düzeltmez: yeniden denenmedi
    assert f.last_status_counts.get("storage") == 1
    assert f.rate_limit_breaker_triggered is False  # depolama hatası istek hatası değildir
    assert (Path(f.match_details_dir) / "77_Cup" / "season_Cup_26" / "102" / "basic.json").exists()


def test_async_enospc_aborts_the_batch(tmp_path):
    f = _fetcher(tmp_path)
    failed: List[str] = []
    ids = [str(n) for n in range(101, 111)]
    with pytest.raises(StorageError) as info:
        _run_batch(f, ids, _failing_writer(errno.ENOSPC), failed)

    assert info.value.fatal and info.value.errno == errno.ENOSPC
    assert failed == ["101"]
    assert not list(Path(f.match_details_dir).rglob("basic.json"))


def test_sync_save_failure_reports_the_match_as_failed_and_continues(tmp_path):
    f = _fetcher(tmp_path)
    failed: List[str] = []

    def fake(url, *a, **kw):
        mid = url.split("/event/", 1)[1].split("/", 1)[0]
        return {"event": _basic(mid)} if url.endswith(f"/event/{mid}") else {}

    with patch("src.match_data_fetcher.make_api_request", new=fake), \
            patch.object(mdf, "atomic_write_json", side_effect=_failing_writer(errno.EIO, f"{os.sep}101{os.sep}")), \
            patch("src.match_data_fetcher.time.sleep"):
        results = f.fetch_matches_batch(["101", "102"], failed_callback=failed.append)
    assert failed == ["101"] and list(results) == ["102"]


def test_sync_enospc_aborts_the_batch(tmp_path):
    f = _fetcher(tmp_path)
    failed: List[str] = []
    requested: List[str] = []

    def fake(url, *a, **kw):
        requested.append(url)
        mid = url.split("/event/", 1)[1].split("/", 1)[0]
        return {"event": _basic(mid)} if url.endswith(f"/event/{mid}") else {}

    with patch("src.match_data_fetcher.make_api_request", new=fake), \
            patch.object(mdf, "atomic_write_json", side_effect=_failing_writer(errno.ENOSPC)), \
            patch("src.match_data_fetcher.time.sleep"):
        with pytest.raises(StorageError):
            f.fetch_matches_batch(["101", "102", "103"], failed_callback=failed.append)
    assert failed == ["101"]
    assert not any("/event/102" in url for url in requested)  # iş durdu: sonraki maç istenmedi


def test_refresh_write_failure_is_a_storage_error(tmp_path):
    f = _fetcher(tmp_path)
    f._save_match_data(MID, {"basic": _basic()})
    with patch.object(f, "_fetch_match_basic", return_value=_basic()), \
            patch.object(mdf, "atomic_write_json", side_effect=_failing_writer(errno.ENOSPC)):
        with pytest.raises(StorageError) as info:
            f.refresh_match(MID)
    assert info.value.fatal


def test_fetch_all_match_details_lets_fatal_storage_errors_through(tmp_path):
    """Headless yol eskiden her hatayı yutup False dönüyordu; disk dolu ise çağıran bunu görmeli."""
    f = _fetcher(tmp_path)
    boom = StorageError.from_exception(OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)), str(tmp_path))
    with patch.object(f, "collect_detail_match_ids", return_value=["1"]), \
            patch.object(f, "pending_detail_ids", return_value=["1"]), \
            patch.object(f, "fetch_matches_batch_parallel", side_effect=boom):
        with pytest.raises(StorageError):
            f.fetch_all_match_details()


def test_headless_cli_reports_a_storage_error_and_exits_1(tmp_path, monkeypatch, capsys):
    import main as cli

    boom = StorageError.from_exception(
        OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)), str(tmp_path / "match_details" / "17_PL")
    )
    monkeypatch.setenv("DATA_DIR", os.environ["DATA_DIR"])  # main --data-dir ortamı değiştirir: test sonunda geri al
    monkeypatch.delenv("APP_EXIT_CODE", raising=False)
    monkeypatch.setattr("sys.argv", [
        "main.py", "--headless", "--update-all", "--fetch-mode", "details", "--league-id", "17",
        "--data-dir", str(tmp_path),
    ])
    # Headless yol SyncService'i çağırır (P10): detay aşamasının ilk indirici çağrısı collect_detail_match_ids'tir
    with patch.object(MatchDataFetcher, "collect_detail_match_ids", side_effect=boom), \
            patch.object(utils.cffi_requests, "get", side_effect=AssertionError("ağ isteği yapılmamalı")):
        assert cli.main() == 1
    err = capsys.readouterr().err
    assert "17_PL" in err and os.strerror(errno.ENOSPC) in err
    assert "Traceback" not in err


# --- web işi: kalıcı depolama hatası işi durdurur ------------------------------------------

def test_web_job_fails_with_a_clear_message_on_enospc(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import src.web.fetch_job as fj
    from src.web.jobs import JobStore
    from src.web.routes.scrape import FetchRequest

    store = JobStore(str(tmp_path / "jobs.db"))
    monkeypatch.setattr(fj, "_job_store", store)
    monkeypatch.setattr(fj, "_refresh_scraper_state", lambda: store.snapshot())
    monkeypatch.setattr(fj.config_manager, "get_leagues", lambda: {17: "Premier League"})
    exported: List[bool] = []

    class FullDisk:
        rate_limit_breaker_triggered = False
        last_status_counts: Dict[str, int] = {}
        refresh_listener = None

        def begin_job_cache(self):
            pass

        def end_job_cache(self):
            pass

        def collect_detail_match_ids(self, league_id=None, max_seasons=0, only_season_ids=None):
            return ["a", "b"]

        def pending_detail_ids(self, ids):
            return list(ids)

        def fetch_detail_ids(self, ids, progress_callback=None, should_cancel=None, failed_callback=None):
            failed_callback("a")
            raise StorageError.from_exception(
                OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)), "/data/match_details/17_PL/season_x/a"
            )

    # Servis bağlamının (ServiceContext) yerini tutar; CSV adımı çağrılırsa `exported`a yazılır
    ctx = SimpleNamespace(config=fj.config_manager, match_data_fetcher=FullDisk())
    monkeypatch.setattr(fj, "build_context", lambda config_manager: ctx)
    monkeypatch.setattr("src.services.sync.export_all_csv", lambda ctx: exported.append(True))
    req = FetchRequest(mode="details", league_id=17)
    fj.run_fetch_job(store.create_running(req.model_dump()), req)

    final = store.snapshot()
    assert final["status"] == "Failed"
    assert "/data/match_details/17_PL/season_x/a" in final["current_task"]
    assert os.strerror(errno.ENOSPC) in final["current_task"]
    assert final["matches_failed"] == 1
    assert final["result"]["error"] == "storage"
    assert exported == []  # iş durdu: dışa aktarma aşamasına geçilmedi


# --- uniqueTournament.id'si olmayan maç ----------------------------------------------------

def test_match_without_unique_tournament_goes_to_the_fallback_directory(tmp_path):
    f = _fetcher(tmp_path)
    f._save_match_data(MID, {"basic": _basic(unique_tournament=False, sport="esports")})

    expected = Path(f.match_details_dir) / NO_TOURNAMENT_DIR / "esports" / MID
    assert (expected / "basic.json").exists()
    assert [p.name for p in Path(f.match_details_dir).iterdir() if p.name != "processed"] == [NO_TOURNAMENT_DIR]


def test_fallback_directory_is_found_by_readers(tmp_path):
    f = _fetcher(tmp_path)
    f._save_match_data(MID, {"basic": _basic(unique_tournament=False, sport="esports")})
    expected = str(Path(f.match_details_dir) / NO_TOURNAMENT_DIR / "esports" / MID)

    assert f._find_match_path(MID) == (NO_TOURNAMENT_DIR, "esports", expected)
    assert f._build_match_index()[MID] == (NO_TOURNAMENT_DIR, "esports", expected)
    assert f._compute_detail_need(MID) == "refill"  # kaydı okunuyor: eksik dilimleri tamamlanabilir


def test_fallback_directory_is_deterministic_without_sport(tmp_path):
    f = _fetcher(tmp_path)
    basic = _basic(unique_tournament=False)
    basic["tournament"] = {"name": "Some / Odd Name"}
    f._save_match_data(MID, {"basic": basic})
    assert (Path(f.match_details_dir) / NO_TOURNAMENT_DIR / "unknown" / MID / "basic.json").exists()


def test_existing_record_without_unique_tournament_is_not_moved(tmp_path):
    f = _fetcher(tmp_path)
    old_dir = Path(f.match_details_dir) / "Cup" / "season_Cup_26" / MID  # eski sürümün ada göre dizini
    old_dir.mkdir(parents=True)
    (old_dir / "basic.json").write_text(json.dumps(_basic(unique_tournament=False)), encoding="utf-8")

    f._save_match_data(MID, {"basic": _basic(unique_tournament=False), "h2h": H2H})
    assert (old_dir / "h2h.json").exists()
    assert not (Path(f.match_details_dir) / NO_TOURNAMENT_DIR).exists()


def test_match_with_unique_tournament_keeps_its_directory(tmp_path):
    f = _fetcher(tmp_path)
    f._save_match_data(MID, {"basic": _basic()})
    assert (Path(f.match_details_dir) / "77_Cup" / "season_Cup_26" / MID / "basic.json").exists()
