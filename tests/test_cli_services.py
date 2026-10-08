"""
`main.py` ve komutların servisleri nasıl çağırdığı (plan maddeleri P10, P19 ve P30).

`main.py` `ssc`nin yönlendiricisidir (`python main.py sync` = `ssc sync`). 2.x'in bayrakları 3.0.0'da kullanımdan
kalktı ve 3.1'de silindi (P30): onlarla çalıştırma, hiçbir şey yapmadan yerine geçen komutu söyleyen bir kullanım
hatasıdır (sofascore_scraper/cli/removed_flags.py). Buradaki senaryolar o bayrakların çalıştırdığı komutlardır:

  * `sync`, `refresh` ve `data recheck-unavailable` terminal arayüzünü kurmaz; SyncService / MaintenanceService'i
    çağırır;
  * çıkış kodu ortam değişkeninden (APP_EXIT_CODE) değil, servisin türü belli sonucundan okunur
    (sofascore_scraper/cli/exit_codes.py): 3 kısmi, 4 devre kesici, 5 depolama, 6 kilit;
  * veri dizinine yazan komutlar çalışma boyunca dizinin yazar kilidini tutar; kilit başka bir sahipteyse
    sahibi söylenir ve çıkış kodu 6'dır.

`main.main(argv)` bu süreçte çağrılır; servisler sahtedir (ağ yok, indirme yok). Aynı komutların gerçek alt
süreçteki davranışı (çıktı, istekler, dosyalar, iki süreç arasında reddedilen kilit)
tests/characterization/test_cli_goldens.py'de, komutların ayrıntıları tests/test_cli_data_commands.py'dedir.
"""
from __future__ import annotations

import argparse
import ast
import errno
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

import main as cli
from sofascore_scraper.exceptions import StorageError
from sofascore_scraper.services.maintenance import MaintenanceService, ResetCounts
from sofascore_scraper.services.sync import RefreshCounts, SyncResult, SyncService, SyncSpec
from sofascore_scraper.store import open_store
from sofascore_scraper.store.lease import LeaseManager

ROOT = Path(__file__).resolve().parent.parent

RunCli = Callable[..., int]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def run_cli(data_dir: Path, monkeypatch: pytest.MonkeyPatch, restore_cli_process: None) -> RunCli:
    """`main.py --data-dir <geçici dizin> <argv>` çalıştırır ve çıkış kodunu döndürür."""
    monkeypatch.delenv("APP_EXIT_CODE", raising=False)

    def run(*argv: str) -> int:
        return cli.main(["--data-dir", str(data_dir), *argv])

    return run


def lease_holder(data_dir: Path, name: str = "writer") -> Optional[Any]:
    """Kilidin o anki sahibi (LeaseInfo) ya da None."""
    return open_store(data_dir).lease_holder(name)


def initialise_store(data_dir: Path) -> None:
    """Dizini bir kez depo olarak açar (state.db geçişi çalışır); gerçek bir kilit sahibi bunu zaten yapmıştır."""
    open_store(data_dir)


def sync_result(
    *, breaker: Optional[str] = None, empty: int = 0, refresh: Optional[RefreshCounts] = None, **progress: Any
) -> SyncResult:
    fields: Dict[str, Any] = {
        "details_done": 0, "details_total": 0, "failed_count": 0, "failed": [], "breaker": breaker,
        "refreshed": 0, "refresh_changed": 0,
    }
    fields.update(progress)
    failed = fields["failed_count"] or (refresh is not None and refresh.failed)
    state: Any = "partial" if breaker or failed else "succeeded"
    return SyncResult(state=state, schedule_empty_seasons=empty, breaker=breaker, progress=fields, refresh=refresh)


class Recorder:
    """SyncService.run'ın yerine geçer: belirtimi ve o anda yazar kilidini kimin tuttuğunu kaydeder."""

    def __init__(self, data_dir: Path, result: SyncResult) -> None:
        self.data_dir = data_dir
        self.result = result
        self.specs: List[SyncSpec] = []
        self.holders: List[Any] = []
        self.error: Optional[BaseException] = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "Recorder":
        recorder = self

        def run(self: SyncService, spec: SyncSpec, *, handle: Any = None) -> SyncResult:
            recorder.specs.append(spec)
            recorder.holders.append(lease_holder(recorder.data_dir))
            assert handle is not None  # komut satırı işi iş yöneticisiyle çalışır: servise tutamaç verilir (P11)
            if recorder.error is not None:
                raise recorder.error
            return recorder.result

        monkeypatch.setattr(SyncService, "run", run)
        return self


# --- main.py ----------------------------------------------------------------------------------------


def test_main_imports_only_the_cli_package_at_module_level() -> None:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    top_level: List[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.append(node.module)

    assert [name for name in top_level if name.startswith("sofascore_scraper")] == ["sofascore_scraper.cli"]


# --- 2.x'in bayrakları: kullanım hatası, yerine geçen komutla -----------------------------------------


@pytest.mark.parametrize("argv, instead", [
    (["--headless", "--update-all"], "ssc sync"),
    (["--headless", "--update-all", "--league-id", "17", "--fetch-mode", "details"], "ssc sync --only events --tournament"),
    (["--headless", "--csv-export"], "ssc export"),
    (["--refresh-only", "--refresh-legacy"], "ssc refresh --include-legacy"),
    (["--recheck-unavailable", "all"], "ssc data recheck-unavailable"),
    (["--watch", "--sport", "tennis", "--event-ids", "5", "--watch-hours", "2"],
     "ssc watch --source poll --stdout --event --hours"),
    (["--diagnostics", "out/b.zip"], "ssc diagnostics"),
    (["--doctor"], "ssc doctor"),
    (["--web", "--host", "0.0.0.0"], "ssc serve"),
    (["--ignore-rate-limit", "--headless", "--update-all"], "ssc sync --ignore-breaker"),
    (["--headless"], None),
])
def test_an_old_flag_is_a_usage_error_that_names_the_new_command(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    argv: List[str], instead: Optional[str],
) -> None:
    recorder = Recorder(data_dir, sync_result()).install(monkeypatch)

    assert run_cli(*argv) == 2

    err = capsys.readouterr().err
    assert "the flags of `python main.py` were removed in 3.1" in err
    if instead is not None:
        assert f"use `{instead}`" in err
    assert "Run 'ssc --help'" in err
    assert recorder.specs == [] and not data_dir.exists()  # hiçbir şey çalışmadı


def test_the_options_of_a_command_are_not_old_flags(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sync --recheck-unavailable` bir komutun kendi seçeneğidir: yalnızca komuttan önceki argümanlara bakılır."""
    from sofascore_scraper.cli import removed_flags

    assert removed_flags.removed_in(["--json", "sync", "--recheck-unavailable"], ["sync"]) == []
    assert removed_flags.removed_in(["--recheck-unavailable", "sync"], ["sync"]) == ["--recheck-unavailable"]
    assert removed_flags.removed_in(["--league-id=17", "--watch"], ["watch"]) == ["--league-id", "--watch"]


# --- sync ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("argv, expected", [
    ([], SyncSpec(mode="full", league_id=None)),
    (["--tournament", "17"], SyncSpec(mode="full", league_id=17)),
    (["--only", "events"], SyncSpec(mode="details", league_id=None)),
    (["--only", "events", "--tournament", "8"], SyncSpec(mode="details", league_id=8)),
])
def test_sync_calls_the_sync_service_under_the_writer_lease(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    argv: List[str], expected: SyncSpec,
) -> None:
    recorder = Recorder(data_dir, sync_result(details_done=5, details_total=5, failed_count=1, refreshed=2,
                                              refresh_changed=1)).install(monkeypatch)

    assert run_cli("sync", *argv) == 3  # bir maç alınamadı: kısmi (P19'dan önce 0)

    assert recorder.specs == [expected]
    held = recorder.holders[0]
    assert (held.name, held.pid, held.purpose) == ("writer", os.getpid(), "sync")
    assert lease_holder(data_dir) is None  # çalışma bitince kilit bırakılır
    out, _err = capsys.readouterr()
    assert "Matches that needed details: 5 (4 fetched, 1 failed)" in out
    assert "Provisional records refreshed: 2 (1 changed)" in out


def test_sync_reports_seasons_without_a_match_list_on_stderr(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    Recorder(data_dir, sync_result(empty=3)).install(monkeypatch)

    assert run_cli("sync") == 0

    out, err = capsys.readouterr()
    assert "No match list for 3 season(s)" in err
    assert "Download finished." in out


def test_a_breaker_stop_exits_with_4_from_the_typed_result(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Çıkış kodu SyncResult.breaker'dan gelir; yarıda kalan çalışmanın sayıları yazılmaz."""
    Recorder(data_dir, sync_result(breaker="403", details_done=4, details_total=4)).install(monkeypatch)

    assert run_cli("sync") == 4

    out, err = capsys.readouterr()
    assert "too many requests to SofaScore failed (403)" in err
    assert "Download finished" not in out
    assert "APP_EXIT_CODE" not in os.environ  # komut çıkış kodunu ortam üzerinden taşımaz


def test_the_exit_code_variable_in_the_environment_does_not_reach_a_run(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Recorder(data_dir, sync_result()).install(monkeypatch)
    monkeypatch.setenv("APP_EXIT_CODE", "7")

    assert run_cli("sync") == 0


def test_a_storage_error_from_the_service_exits_with_5_and_releases_the_lease(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorder = Recorder(data_dir, sync_result()).install(monkeypatch)
    recorder.error = StorageError.from_exception(
        OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)), str(data_dir / "match_details" / "17_PL")
    )

    assert run_cli("sync") == 5  # P19'dan önce 1

    err = capsys.readouterr().err
    assert "17_PL" in err and os.strerror(errno.ENOSPC) in err and "Traceback" not in err
    assert recorder.holders[0] is not None and lease_holder(data_dir) is None


# --- export: kilit yok ---------------------------------------------------------------------------------


def test_export_takes_no_lease(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from sofascore_scraper.services.export import ExportResult, ExportService

    Recorder(data_dir, sync_result()).install(monkeypatch).error = AssertionError("no download was asked for")
    holders: List[Any] = []

    def write(self: ExportService, directory: str, spec: Any = None) -> ExportResult:
        holders.append(lease_holder(data_dir))
        return ExportResult(rows=1, columns=("match_id",), bytes=10, path="/x/all_matches_1.csv")

    monkeypatch.setattr(ExportService, "prepare", lambda self, spec=None: argparse.Namespace(rows=1))
    monkeypatch.setattr(ExportService, "write_legacy_csv", write)

    assert run_cli("export") == 0

    assert holders == [None]
    assert "Export written: /x/all_matches_1.csv (1 matches)" in capsys.readouterr().out


def test_export_with_nothing_to_export_exits_with_1(
    run_cli: RunCli, data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """P19'dan önce hata satırıyla 0 dönüyordu (bölüm 15); şimdi `not_found`, dosya yazılmaz."""
    assert run_cli("export") == 1

    assert "Not found: there is no downloaded match to export" in capsys.readouterr().err
    assert not (data_dir / "match_details" / "processed").exists() or not any(
        (data_dir / "match_details" / "processed").iterdir())


# --- refresh -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("counts, breaker, exit_code", [
    (RefreshCounts(), None, 0),  # yenilenecek kayıt yok
    (RefreshCounts(due=2, refreshed=2, changed=1), None, 0),  # hepsi yenilendi
    (RefreshCounts(due=3, refreshed=2, changed=1, failed=1), None, 3),  # biri alınamadı: kısmi (önce 0)
    (RefreshCounts(due=2, failed=2), None, 3),  # hepsi başarısız: kısmi (önce 1)
    (RefreshCounts(due=9, failed=2, skipped=7), "403", 4),  # devre kesildi (önce 2)
    (RefreshCounts(due=9, refreshed=1, failed=1, skipped=7), "5xx", 4),
])
def test_refresh_maps_the_result_to_the_exit_codes(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    counts: RefreshCounts, breaker: Optional[str], exit_code: int,
) -> None:
    recorder = Recorder(data_dir, sync_result(breaker=breaker, refresh=counts)).install(monkeypatch)

    assert run_cli("refresh", "--tournament", "17") == exit_code

    assert recorder.specs == [SyncSpec(mode="refresh", league_id=17)]
    assert recorder.holders[0].purpose == "refresh" and lease_holder(data_dir) is None
    out, err = capsys.readouterr()
    assert f"Refresh: {counts.refreshed} matches refreshed, {counts.changed} changed, {counts.failed} failed" in out
    if breaker:
        assert f"({breaker}); {counts.skipped} matches were not attempted" in err
    else:
        assert "stopped" not in err


# --- data recheck-unavailable --------------------------------------------------------------------------


@pytest.fixture
def recheck(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> List[Any]:
    """MaintenanceService.recheck_unavailable'ın yerine geçer; (lig, include_confirmed, kilidin amacı) kaydeder."""
    calls: List[Any] = []

    def recheck_unavailable(
        self: MaintenanceService, league_id: Optional[int] = None, *, include_confirmed: bool = False
    ) -> ResetCounts:
        holder = lease_holder(data_dir)
        calls.append((league_id, include_confirmed, holder.purpose if holder is not None else None))
        return ResetCounts(matches=2, slices=3, scanned=5)

    monkeypatch.setattr(MaintenanceService, "recheck_unavailable", recheck_unavailable)
    return calls


@pytest.mark.parametrize("argv, expected", [
    ([], (None, False, "recheck-unavailable")),
    (["--all", "--tournament", "17"], (17, True, "recheck-unavailable")),
])
def test_recheck_alone_runs_under_the_writer_lease_and_exits(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    recheck: List[Any], argv: List[str], expected: Any,
) -> None:
    recorder = Recorder(data_dir, sync_result()).install(monkeypatch)

    assert run_cli("data", "recheck-unavailable", *argv) == 0

    assert recheck == [expected] and recorder.specs == []
    assert '3 "slice not available" markers reopened in 2 matches (5 matches' in capsys.readouterr().out
    assert lease_holder(data_dir) is None


def test_recheck_then_download_runs_inside_the_download_job(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, recheck: List[Any]
) -> None:
    """İşaretler indirme işinin içinde, aynı kilit altında açılır."""
    recorder = Recorder(data_dir, sync_result()).install(monkeypatch)

    assert run_cli("sync", "--only", "events", "--recheck-unavailable") == 0

    assert recheck == [(None, False, "sync")]
    assert recorder.specs == [SyncSpec(mode="details")]
    assert recorder.holders[0].purpose == "sync"


# --- reddedilen kilit --------------------------------------------------------------------------------


def test_a_held_writer_lease_stops_the_run_with_exit_code_6(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Kilit bu süreçte, sahip kaydı olmayan bir yöneticiyle tutulur: reddedilme gerçektir (işletim sistemi kilidi),
    sahibin bilgisi ise bilinmez ve "?" olarak yazılır. Sahibi bilinen durum goldenlarda (iki süreç) sınanır.
    """
    recorder = Recorder(data_dir, sync_result()).install(monkeypatch)
    initialise_store(data_dir)

    with LeaseManager.for_data_dir(data_dir).acquire("writer", purpose="job"):
        assert run_cli("sync") == 6
        assert run_cli("refresh") == 6
        assert run_cli("data", "recheck-unavailable") == 6
    assert run_cli("sync") == 0  # kilit bırakıldı

    assert len(recorder.specs) == 1
    err = capsys.readouterr().err
    assert err.count("Held by process ? on ? (?) since ?") == 3
    assert "Traceback" not in err


@pytest.mark.parametrize("lang, label, holder", [
    ("en", "A job is already running: A job is already running on this data directory.",
     "Held by process 4242 on box-1 (job) since 2026-10-02T12:00:00Z"),
    ("tr", "Bir iş zaten çalışıyor: A job is already running on this data directory.",
     "Tutan süreç: 4242, makine: box-1 (job); başlangıç: 2026-10-02T12:00:00Z"),
])
def test_the_refusal_names_the_holder_in_the_app_language(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    lang: str, label: str, holder: str,
) -> None:
    from sofascore_scraper.store import JobStoreConflict, LeaseHeld

    held = LeaseHeld("held", name="writer", pid=4242, host="box-1", purpose="job", started_at=1790942400.0)

    def refuse(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise JobStoreConflict("busy") from held

    monkeypatch.setenv("APP_LANGUAGE", lang)
    monkeypatch.setattr("sofascore_scraper.jobs.manager.JobManager.start", refuse)

    assert run_cli("refresh") == 6

    err = capsys.readouterr().err.splitlines()
    assert err[-2:] == [label, holder]


# --- watch --source poll --------------------------------------------------------------------------------


class FakeLiveService:
    """LiveService'in yerine geçer: kapsamı, kaynağı ve süreyi kaydeder; hiçbir istek atmaz."""

    seen: List[Dict[str, Any]] = []

    def __init__(self, store: Any, scope: Any, **options: Any) -> None:
        self.scope = scope
        self.options = options
        self.report = argparse.Namespace(rounds=0, requests=0, events=0, confirmed=0, to_dict=lambda: {})

    def run(self, stop: Any, until_seconds: Optional[float] = None) -> None:
        FakeLiveService.seen.append({
            "sports": [sport.sport for sport in self.scope.sports],
            "source": self.options.get("requested_source"),
            "until": until_seconds,
        })


def test_watch_with_polling_runs_the_live_service_without_a_browser(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Karar D18: 2.x'in `--watch`ının yerine geçen `ssc watch --source poll --stdout` tarayıcı başlatmaz."""
    FakeLiveService.seen = []
    monkeypatch.setattr("sofascore_scraper.services.live.supervisor.LiveService", FakeLiveService)

    assert run_cli("watch", "--source", "poll", "--stdout", "--sport", "tennis", "--event", "1", "--hours", "2") == 0

    assert FakeLiveService.seen == [{"sports": ["tennis"], "source": "poll", "until": 7200.0}]


def test_a_running_watcher_of_the_same_sport_refuses_the_run(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    initialise_store(data_dir)

    with LeaseManager.for_data_dir(data_dir).acquire("watcher:tennis", purpose="watch"):
        assert run_cli("watch", "--source", "poll", "--stdout", "--sport", "tennis", "--event", "1",
                       "--hours", "0.0000001") == 6

    assert "Another instance is running" in capsys.readouterr().err
