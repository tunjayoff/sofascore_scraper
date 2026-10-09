"""
Kayıt hataları ve maçın yeri: yazılamayan maç "indirildi" sayılmaz.

Sabitlenen kurallar:
  - Yazma hatayı yutmaz, StorageError fırlatır (Store'un hatası, StoreError, bir StorageError'dır).
    Maç başarısız olarak bildirilir (detay aşamasının `failed` geri çağrısı); neden kalıcıysa (disk/kota dolu, izin yok, salt okunur)
    iş durur ve kullanıcıya yolu ve nedeni söyleyen bir mesaj verilir.
  - Maçlar Store'a, v3 düzenine yazılır (`v3/events/.../<id>`, plan maddesi ST-21): yerleri kimlikten türer,
    uniqueTournament.id'si olmayan maç da aynı yere gider. Eski düzende duran kayıt taşınmaz: ilk yazmada
    v3'e yükseltilir, eski dizinine dokunulmaz.

Gerçek ağ yok: istek katmanı sahte; disk hataları Store'un dosya yazıcısının (`sofascore_scraper.store.files.write_bytes`)
sahtesiyle üretilir.
"""
from __future__ import annotations

import contextlib
import errno
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from sofascore_scraper.client import transport
from detail_fetch import Details
from sofascore_scraper.web import deps
from sofascore_scraper.exceptions import StorageError
from sofascore_scraper.services.detail_phase import DetailPhase
from sofascore_scraper.store import files as store_files
from sofascore_scraper.store import layout
from sofascore_scraper.store import open_store

MID = "4242"
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


def _fetcher(tmp_path) -> Details:
    return Details(tmp_path)


def _phase(f: Details) -> DetailPhase:
    """Bir işin detay aşaması: maçlar sırayla, devre kesici eşikleri yüksek."""
    cfg = MagicMock()
    cfg.get_rate_limit_threshold_consecutive.return_value = 1000
    cfg.get_rate_limit_threshold_ratio.return_value = 2.0
    cfg.get_server_error_threshold_consecutive.return_value = 1000
    cfg.get_max_concurrent.return_value = 1
    return DetailPhase(f.store, cfg)


# --- kayıt hataları ------------------------------------------------------------------------

def _failing_writer(code: int, only_for: Optional[int] = None):
    """
    Store'un dosya yazıcısının (`sofascore_scraper.store.files.write_bytes`) sahtesi: maç dosyalarını (v3 ağacı ya da hazırlık
    dizini) yazarken, gerçek yazıcı gibi OSError'ı StoreError'a çevirerek düşer. only_for verilirse yalnızca o
    maçın manifesti yazılamaz (yeni maç hazırlık dizininde kurulur: yolunda kimliği yoktur, manifestinde vardır).
    """
    real = store_files.write_bytes

    def write(path, data, **kw):
        text = os.fspath(path)
        # Yol parçalarına bakılır: testin geçici dizini de `/tmp/` altında olabilir (Linux CI'da öyledir)
        parts = Path(text).parts
        staging = any(a == ".meta" and b == "tmp" for a, b in zip(parts, parts[1:], strict=False))
        ours = "v3" in parts or staging
        if ours and only_for is not None:
            ours = os.path.basename(text) == "manifest.json" and json.loads(data).get("id") == only_for
        if ours:
            raise store_files._store_error(OSError(code, os.strerror(code), text), text)
        return real(path, data, **kw)

    return write


def _stored(f: Details, mid: Any) -> bool:
    """Maçın olay yükü saklanıyor mu (Store)."""
    row = open_store(f.data_dir).events.get(int(mid))
    return row is not None and row.has_event_payload


@contextlib.contextmanager
def _world(ids: List[str]):
    """Sahte SofaScore (tests/fakes/sofascore.py): her maçın /event'i, dilimleri boş nesne; istek katmanı gerçek."""
    from fakes.sofascore import SLICE_PATHS, FakeSofaScore

    fake = FakeSofaScore()
    for mid in ids:
        fake.add(f"/event/{mid}", {"event": _basic(mid)})
        for path in SLICE_PATHS.values():
            fake.add(path.format(event_id=mid), {})
    with fake:
        yield fake


def _event_calls(fake) -> List[str]:
    return [r.path for r in fake.requests if r.path.startswith("/event/") and r.path.count("/") == 2]


def _run_batch(phase: DetailPhase, ids: List[str], writer, failed: List[str]) -> List[str]:
    """Seçilen maçları (DetailPhase.fetch_selected) sahte SofaScore'la çalıştırır; istenen /event yollarını döndürür."""
    with _world(ids) as fake, patch.object(store_files, "write_bytes", side_effect=writer):
        try:
            phase.fetch_selected(ids, failed=failed.append)
        finally:
            calls = _event_calls(fake)
    return calls


def test_save_raises_storage_error_instead_of_swallowing(tmp_path):
    f = _fetcher(tmp_path)
    with patch.object(store_files, "write_bytes", side_effect=_failing_writer(errno.EIO)):
        with pytest.raises(StorageError) as info:
            f.save(MID, {"basic": _basic()})
    assert info.value.errno == errno.EIO and not info.value.fatal
    assert str(tmp_path) in (info.value.path or "")  # hatanın yolu veri dizininde (hazırlık dizini)
    assert not _stored(f, MID)  # yarım bir kayıt kalmadı


@pytest.mark.parametrize("code,fatal", [
    (errno.ENOSPC, True), (errno.EACCES, True), (errno.EROFS, True), (errno.EIO, False), (errno.ENAMETOOLONG, False),
])
def test_storage_error_fatality(code, fatal):
    assert StorageError.from_exception(OSError(code, os.strerror(code)), "/data").fatal is fatal


def test_async_save_failure_reports_the_match_as_failed_and_continues(tmp_path):
    f = _fetcher(tmp_path)
    phase = _phase(f)
    failed: List[str] = []
    # Yalnızca 101 numaralı maçın dizinine yazılamıyor (maça özgü, kalıcı olmayan hata)
    calls = _run_batch(phase, ["101", "102"], _failing_writer(errno.EIO, only_for=101), failed)

    assert failed == ["101"]  # yazılamayan maç "indirildi" sayılmadı
    assert calls.count("/event/101") == 1  # yeniden istemek kaydı düzeltmez: yeniden denenmedi
    assert phase.breaker_tripped is False  # depolama hatası istek hatası değildir
    assert phase.status_counts == {}
    assert _stored(f, 102) and not _stored(f, 101)


def test_async_enospc_aborts_the_batch(tmp_path):
    f = _fetcher(tmp_path)
    failed: List[str] = []
    ids = [str(n) for n in range(101, 111)]
    with pytest.raises(StorageError) as info:
        _run_batch(_phase(f), ids, _failing_writer(errno.ENOSPC), failed)

    assert info.value.fatal and info.value.errno == errno.ENOSPC
    assert failed == ["101"]
    assert not any(_stored(f, mid) for mid in ids)


def test_picked_matches_save_failure_reports_the_match_as_failed_and_continues(tmp_path):
    f = _fetcher(tmp_path)
    failed: List[str] = []

    with _world(["101", "102"]), \
            patch.object(store_files, "write_bytes", side_effect=_failing_writer(errno.EIO, only_for=101)):
        stored = _phase(f).fetch_selected(["101", "102"], failed=failed.append)
    assert failed == ["101"] and stored == 1 and _stored(f, 102)


def test_picked_matches_enospc_aborts_the_batch(tmp_path):
    f = _fetcher(tmp_path)
    failed: List[str] = []

    with _world(["101", "102", "103"]) as fake, \
            patch.object(store_files, "write_bytes", side_effect=_failing_writer(errno.ENOSPC)):
        with pytest.raises(StorageError):
            _phase(f).fetch_selected(["101", "102", "103"], failed=failed.append)
        requested = _event_calls(fake)
    assert failed == ["101"]
    assert requested == ["/event/101"]  # iş durdu: sonraki maç istenmedi


def test_refresh_write_failure_is_a_storage_error(tmp_path):
    f = _fetcher(tmp_path)
    f.save(MID, {"basic": _basic()})
    with _world([MID]), patch.object(store_files, "write_bytes", side_effect=_failing_writer(errno.ENOSPC)):
        with pytest.raises(StorageError) as info:
            f.refresh(MID)
    assert info.value.fatal


def test_the_detail_phase_lets_fatal_storage_errors_through(tmp_path):
    """Detay aşaması (SyncService) hatayı yutmaz: disk dolu ise çağıran bunu görmeli."""
    phase = _phase(_fetcher(tmp_path))
    boom = StorageError.from_exception(OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)), str(tmp_path))
    with patch.object(phase, "_run", side_effect=boom):
        with pytest.raises(StorageError):
            phase.fetch(["1"])


def test_cli_sync_reports_a_storage_error_and_exits_5(tmp_path, monkeypatch, capsys, restore_cli_process):
    import main as cli

    boom = StorageError.from_exception(
        OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)), str(tmp_path / "match_details" / "17_PL")
    )
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", os.environ["SOFASCORE_STORAGE__DATA_DIR"])  # main --data-dir ortamı değiştirir: test sonunda geri al
    monkeypatch.delenv("APP_EXIT_CODE", raising=False)
    monkeypatch.setattr("sys.argv", [
        "main.py", "--data-dir", str(tmp_path), "sync", "--only", "events", "--tournament", "17",
    ])
    # `sync` SyncService'i çağırır (P10): detay aşamasının ilk çağrısı adayların okunmasıdır
    with patch.object(DetailPhase, "candidates", side_effect=boom), \
            patch.object(transport.cffi_requests, "get", side_effect=AssertionError("ağ isteği yapılmamalı")):
        assert cli.main() == 5  # P19: depolama hatası 5 (önce 1)
    err = capsys.readouterr().err
    assert "17_PL" in err and os.strerror(errno.ENOSPC) in err
    assert "Traceback" not in err


# --- web işi: kalıcı depolama hatası işi durdurur ------------------------------------------

def test_web_job_fails_with_a_clear_message_on_enospc(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from sofascore_scraper.services import context, sync
    from sofascore_scraper.store import JobStore
    from web_job import run_sync_job

    store = JobStore(str(tmp_path / "jobs.db"))
    monkeypatch.setattr(deps, "job_store", lambda: store)
    monkeypatch.setattr(deps, "refresh_job_mirror", lambda: store.snapshot())
    monkeypatch.setattr(deps.config_manager(), "get_leagues", lambda: {17: "Premier League"})
    class FullDisk:
        """Detay aşamasının sahtesi: ilk maç yazılamıyor, disk dolu."""
        breaker_tripped = False
        status_counts: Dict[str, int] = {}
        refresh_listener = None

        def candidates(self, league_id=None, *, only_season_ids=None):
            return ["a", "b"]

        def pending(self, ids):
            return list(ids)

        def fetch(self, ids, *, progress=None, cancelled=None, failed=None):
            failed("a")
            raise StorageError.from_exception(
                OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)), "/data/match_details/17_PL/season_x/a"
            )

    # Servis bağlamının (ServiceContext) yerini tutar
    ctx = SimpleNamespace(config=deps.config_manager())
    monkeypatch.setattr(context, "build_context", lambda config_manager: ctx)
    monkeypatch.setattr(sync, "detail_phase", lambda _ctx: FullDisk())
    final = run_sync_job(store, {"mode": "details", "league_id": 17})

    assert final["status"] == "Failed"
    assert "/data/match_details/17_PL/season_x/a" in final["current_task"]
    assert os.strerror(errno.ENOSPC) in final["current_task"]
    assert final["matches_failed"] == 1
    from sofascore_scraper.jobs.manager import JobManager

    job = JobManager(store).get(final["job_id"])
    assert job is not None and job.error is not None and job.error.code == "storage_error"


# --- maçın yeri: v3, kimlikten (uniqueTournament.id'si olmasa da) ------------------------------------

def _v3_dir(f: Details, mid: Any) -> Path:
    return Path(f.data_dir, *layout.event_dir(int(mid)).split("/"))


def _legacy_root(f: Details) -> Path:
    return Path(f.data_dir, "match_details")


def test_match_without_unique_tournament_is_stored_by_its_id(tmp_path):
    f = _fetcher(tmp_path)
    f.save(MID, {"basic": _basic(unique_tournament=False, sport="esports")})

    assert (_v3_dir(f, MID) / "event.json.gz").is_file()
    assert not _legacy_root(f).exists()  # eski düzene yazılmaz


def test_a_match_without_unique_tournament_is_found_by_readers(tmp_path):
    f = _fetcher(tmp_path)
    f.save(MID, {"basic": _basic(unique_tournament=False, sport="esports")})
    expected = str(_v3_dir(f, MID))

    assert f.location(MID) == (None, None, expected)
    assert f.need(MID) == "refill"  # kaydı okunuyor: eksik dilimleri tamamlanabilir


def test_existing_record_without_unique_tournament_is_not_moved(tmp_path):
    """Eski sürümün ada göre dizinindeki kayıt v3'e yükseltilir; eski dizine dokunulmaz (karar 4)."""
    f = _fetcher(tmp_path)
    old_dir = _legacy_root(f) / "Cup" / "season_Cup_26" / MID  # eski sürümün ada göre dizini
    old_dir.mkdir(parents=True)
    (old_dir / "basic.json").write_text(json.dumps(_basic(unique_tournament=False)), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in old_dir.iterdir()}

    f.save(MID, {"basic": _basic(unique_tournament=False), "h2h": H2H})
    assert {p.name: p.read_bytes() for p in old_dir.iterdir()} == before
    assert not (_legacy_root(f) / "_no_tournament").exists()
    store = open_store(f.data_dir)
    row = store.events.get(int(MID))
    assert (row.layout, row.legacy_path) == ("v3", f"match_details/Cup/season_Cup_26/{MID}")
    assert store.events.payload(int(MID), "h2h") == H2H


def test_match_with_unique_tournament_is_stored_by_its_id_too(tmp_path):
    f = _fetcher(tmp_path)
    f.save(MID, {"basic": _basic()})
    assert (_v3_dir(f, MID) / "event.json.gz").is_file()
    assert not (_legacy_root(f) / "77_Cup").exists()
