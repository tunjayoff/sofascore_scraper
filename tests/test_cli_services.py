"""
`main.py`nin servisleri nasıl çağırdığı (plan maddesi P10 ve ST-10'un komut satırı yarısı):

  * --headless --update-all, --refresh-only ve --recheck-unavailable terminal arayüzünü kurmaz; servis bağlamını
    kurar ve SyncService / MaintenanceService'i çağırır;
  * çıkış kodu ortam değişkeninden (APP_EXIT_CODE) değil, servisin türü belli sonucundan okunur;
  * veri dizinine yazan kipler çalışma boyunca dizinin yazar kilidini, --watch `watcher:<spor>` kilidini tutar;
    kilit başka bir sahipteyse sahibi söylenir ve çıkış kodu 6'dır.

`main.main()` bu süreçte çağrılır; servisler sahtedir (ağ yok, indirme yok). Aynı kiplerin gerçek alt süreçteki
davranışı (çıktı, istekler, dosyalar, iki süreç arasında reddedilen kilit) tests/characterization/test_cli_goldens.py'dedir.
"""
from __future__ import annotations

import ast
import contextlib
import errno
import os
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

import pytest

import main as cli
from src.exceptions import StorageError
from src.i18n import I18nManager
from src.services.maintenance import MaintenanceService, ResetCounts
from src.services.sync import RefreshCounts, SyncResult, SyncService, SyncSpec
from src.store import LeaseHeld, open_store
from src.store.lease import LeaseManager

ROOT = Path(__file__).resolve().parent.parent

RunCli = Callable[..., int]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def run_cli(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> RunCli:
    """`main.py <argv> --data-dir <geçici dizin>` çalıştırır ve çıkış kodunu döndürür."""
    monkeypatch.setenv("DATA_DIR", os.environ["DATA_DIR"])  # main --data-dir ortamı değiştirir: test sonunda geri al
    monkeypatch.delenv("APP_EXIT_CODE", raising=False)

    def run(*argv: str) -> int:
        monkeypatch.setattr("sys.argv", ["main.py", *argv, "--data-dir", str(data_dir)])
        return cli.main()

    return run


def lease_holder(data_dir: Path, name: str = "writer") -> Optional[Any]:
    """Kilidin o anki sahibi (LeaseInfo) ya da None. Depo, main.py açmışsa onunkidir; değilse burada kapatılır."""
    was_open = (data_dir / ".meta").exists() and _registered(data_dir)
    store = open_store(data_dir)
    try:
        return store.lease_holder(name)
    finally:
        if not was_open:
            store.close()


def initialise_store(data_dir: Path) -> None:
    """
    Dizini bir kez depo olarak açıp kapatır (state.db geçişi çalışır). Gerçek bir kilit sahibi bunu zaten yapmıştır;
    yapılmamış bir dizinde ilk açılış geçiş için `maintenance` kilidini 5 sn bekler ve test boşuna yavaşlar.
    """
    open_store(data_dir).close()


def _registered(data_dir: Path) -> bool:
    from src.store import api

    return any(not store.closed and Path(store.data_dir) == data_dir for store in api._registry.values())


def sync_result(
    *, breaker: Optional[str] = None, empty: int = 0, refresh: Optional[RefreshCounts] = None, **progress: Any
) -> SyncResult:
    fields: Dict[str, Any] = {
        "details_done": 0, "details_total": 0, "failed_count": 0, "failed": [], "breaker": breaker,
        "refreshed": 0, "refresh_changed": 0,
    }
    fields.update(progress)
    state: Any = "partial" if breaker or fields["failed_count"] else "succeeded"
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


# --- içe aktarma: terminal arayüzü yalnızca etkileşimli dalda -----------------------------------------


def test_main_imports_the_terminal_ui_and_the_services_only_inside_their_branches() -> None:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    top_level: List[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.append(node.module)
    lazy = [m for m in top_level if m.startswith(("src.SofaScoreUi", "src.ui", "src.services", "src.watcher", "src.web"))]

    assert lazy == []
    assert "src.config_manager" in top_level  # yapılandırma her kipte başlangıçta yüklenir
    # Etkileşimli dal dışında hiçbir işlev arayüzü içe aktarmaz
    importers = [
        fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
        for node in ast.walk(fn) if isinstance(node, ast.ImportFrom) and node.module == "src.SofaScoreUi"
    ]
    assert importers == ["main"]


# --- --headless --update-all -------------------------------------------------------------------------


@pytest.mark.parametrize("argv, expected", [
    ([], SyncSpec(mode="full", league_id=None, export=False)),
    (["--league-id", "17"], SyncSpec(mode="full", league_id=17, export=False)),
    (["--fetch-mode", "details"], SyncSpec(mode="details", league_id=None, export=False)),
    (["--fetch-mode", "details", "--league-id", "8"], SyncSpec(mode="details", league_id=8, export=False)),
])
def test_headless_update_calls_the_sync_service_under_the_writer_lease(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    argv: List[str], expected: SyncSpec,
) -> None:
    recorder = Recorder(data_dir, sync_result(details_done=5, details_total=5, failed_count=1, refreshed=2,
                                              refresh_changed=1)).install(monkeypatch)

    assert run_cli("--headless", "--update-all", *argv) == 0

    assert recorder.specs == [expected]
    held = recorder.holders[0]
    assert (held.name, held.pid, held.purpose) == ("writer", os.getpid(), "headless")
    assert lease_holder(data_dir) is None  # çalışma bitince kilit bırakılır
    out, err = capsys.readouterr()
    assert "Matches that needed details: 5 (4 fetched, 1 failed)" in out
    assert "Provisional records refreshed: 2 (1 changed)" in out
    assert err == ""


def test_headless_update_reports_seasons_without_a_match_list_on_stderr(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    Recorder(data_dir, sync_result(empty=3)).install(monkeypatch)

    assert run_cli("--headless", "--update-all") == 0

    out, err = capsys.readouterr()
    assert "No match list for 3 season(s)" in err
    assert "Download finished." in out


def test_a_breaker_stop_exits_with_2_from_the_typed_result(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Çıkış kodu SyncResult.breaker'dan gelir; yarıda kalan çalışmanın sayıları yazılmaz, CSV adımı yine çalışır."""
    Recorder(data_dir, sync_result(breaker="403", details_done=4, details_total=4)).install(monkeypatch)
    exported: List[Any] = []
    monkeypatch.setattr("src.services.export.export_all_csv", lambda ctx: exported.append(ctx) or "/x/all.csv")

    assert run_cli("--headless", "--update-all", "--csv-export") == 2

    out, err = capsys.readouterr()
    assert "too many requests to SofaScore failed (403)" in err
    assert "Download finished" not in out
    assert len(exported) == 1 and "CSV file successfully created: /x/all.csv" in out
    assert "APP_EXIT_CODE" not in os.environ  # main.py çıkış kodunu ortam üzerinden taşımaz


def test_the_exit_code_variable_in_the_environment_does_not_reach_headless_runs(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Recorder(data_dir, sync_result()).install(monkeypatch)
    monkeypatch.setenv("APP_EXIT_CODE", "7")

    assert run_cli("--headless", "--update-all") == 0


def test_a_storage_error_from_the_service_exits_with_1_and_releases_the_lease(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorder = Recorder(data_dir, sync_result()).install(monkeypatch)
    recorder.error = StorageError.from_exception(
        OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)), str(data_dir / "match_details" / "17_PL")
    )

    assert run_cli("--headless", "--update-all") == 1

    err = capsys.readouterr().err
    assert "17_PL" in err and os.strerror(errno.ENOSPC) in err and "Traceback" not in err
    assert recorder.holders[0] is not None and lease_holder(data_dir) is None


# --- --headless --csv-export ve eylemsiz --headless: kilit yok ---------------------------------------


@pytest.mark.parametrize("path, text", [
    ("/x/all_matches_1.csv", "CSV file successfully created: /x/all_matches_1.csv"),
    (None, "Error occurred while creating CSV file."),
])
def test_csv_export_alone_takes_no_lease_and_does_not_open_the_store(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    path: Optional[str], text: str,
) -> None:
    Recorder(data_dir, sync_result()).install(monkeypatch).error = AssertionError("no download was asked for")
    monkeypatch.setattr("src.services.export.export_all_csv", lambda ctx: path)

    assert run_cli("--headless", "--csv-export") == 0  # dosya üretilemese de 0 (bugünkü davranış)

    assert text in capsys.readouterr().out
    assert (data_dir / "match_details").is_dir() and not (data_dir / ".meta").exists()


def test_headless_without_an_action_is_a_usage_error_without_a_lease(
    run_cli: RunCli, data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_cli("--headless") == 2

    assert "--headless needs --update-all and/or --csv-export" in capsys.readouterr().err
    assert not (data_dir / ".meta").exists()


# --- --refresh-only ----------------------------------------------------------------------------------


@pytest.mark.parametrize("counts, breaker, exit_code", [
    (RefreshCounts(), None, 0),  # yenilenecek kayıt yok
    (RefreshCounts(due=3, refreshed=2, changed=1, failed=1), None, 0),  # en az biri yenilendi
    (RefreshCounts(due=2, failed=2), None, 1),  # hepsi başarısız
    (RefreshCounts(due=9, failed=2, skipped=7), "403", 2),  # devre kesildi
    (RefreshCounts(due=9, refreshed=1, failed=1, skipped=7), "5xx", 2),
])
def test_refresh_only_maps_the_result_to_todays_exit_codes(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    counts: RefreshCounts, breaker: Optional[str], exit_code: int,
) -> None:
    recorder = Recorder(data_dir, sync_result(breaker=breaker, refresh=counts)).install(monkeypatch)

    assert run_cli("--refresh-only", "--league-id", "17") == exit_code

    assert recorder.specs == [SyncSpec(mode="refresh", league_id=17)]
    assert recorder.holders[0].purpose == "refresh" and lease_holder(data_dir) is None
    out, err = capsys.readouterr()
    assert f"Refresh: {counts.refreshed} matches refreshed, {counts.changed} changed, {counts.failed} failed" in out
    if breaker:
        assert f"({breaker}); {counts.skipped} matches were not attempted" in err
    else:
        assert err == ""


def test_refresh_only_wins_over_headless_flags(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bugünkü öncelik: --refresh-only verildiyse yalnızca yenileme yapılır."""
    recorder = Recorder(data_dir, sync_result(refresh=RefreshCounts())).install(monkeypatch)

    assert run_cli("--refresh-only", "--headless", "--update-all") == 0

    assert [spec.mode for spec in recorder.specs] == ["refresh"]


# --- --recheck-unavailable ---------------------------------------------------------------------------


@pytest.fixture
def recheck(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> List[Any]:
    """MaintenanceService.recheck_unavailable'ın yerine geçer; (lig, include_confirmed, kilidin amacı) kaydeder."""
    calls: List[Any] = []

    def recheck_unavailable(
        self: MaintenanceService, league_id: Optional[int] = None, *, include_confirmed: bool = False
    ) -> ResetCounts:
        calls.append((league_id, include_confirmed, lease_holder(data_dir).purpose))
        return ResetCounts(matches=2, slices=3, scanned=5)

    monkeypatch.setattr(MaintenanceService, "recheck_unavailable", recheck_unavailable)
    return calls


@pytest.mark.parametrize("argv, expected", [
    (["--recheck-unavailable"], (None, False, "recheck-unavailable")),
    (["--recheck-unavailable", "all", "--league-id", "17"], (17, True, "recheck-unavailable")),
])
def test_recheck_alone_runs_under_the_writer_lease_and_exits(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    recheck: List[Any], argv: List[str], expected: Any,
) -> None:
    recorder = Recorder(data_dir, sync_result()).install(monkeypatch)

    assert run_cli(*argv) == 0

    assert recheck == [expected] and recorder.specs == []
    assert '3 "slice not available" markers reopened in 2 matches (5 matches' in capsys.readouterr().out
    assert lease_holder(data_dir) is None


def test_recheck_then_download_keeps_one_lease_for_both_steps(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, recheck: List[Any]
) -> None:
    recorder = Recorder(data_dir, sync_result()).install(monkeypatch)

    assert run_cli("--recheck-unavailable", "--headless", "--update-all", "--fetch-mode", "details") == 0

    assert recheck == [(None, False, "headless")]
    assert recorder.specs == [SyncSpec(mode="details", export=False)]
    assert recorder.holders[0].purpose == "headless"


def test_recheck_with_headless_but_no_action_is_still_a_usage_error(run_cli: RunCli, recheck: List[Any]) -> None:
    assert run_cli("--recheck-unavailable", "--headless") == 2
    assert len(recheck) == 1  # bugünkü sıra: önce işaretler açılır, sonra eksik eylem fark edilir


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
        assert run_cli("--headless", "--update-all") == cli.EXIT_LEASE_HELD == 6
        assert run_cli("--refresh-only") == 6
        assert run_cli("--recheck-unavailable") == 6
    assert run_cli("--headless", "--update-all") == 0  # kilit bırakıldı

    assert len(recorder.specs) == 1
    err = capsys.readouterr().err
    assert err.count("this data folder is in use by another process (lock writer: pid ?, host ?, purpose ?, since ?)") == 3
    assert "Traceback" not in err and "could not be written" not in err


@pytest.mark.parametrize("lang, expected", [
    ("en", "Stopped: this data folder is in use by another process (lock writer: pid 4242, host box-1, "
           "purpose job, since 2026-10-02 12:00:00 UTC). Wait for it to finish or stop it, then run this again."),
    ("tr", "Durduruldu: bu veri klasörünü başka bir süreç kullanıyor (kilit writer: pid 4242, makine box-1, "
           "amaç job, başlangıç 2026-10-02 12:00:00 UTC). Bitmesini bekleyin ya da onu durdurun, sonra yeniden "
           "çalıştırın."),
])
def test_the_refusal_names_the_holder_in_the_app_language(
    run_cli: RunCli, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], lang: str, expected: str
) -> None:
    held = LeaseHeld("held", name="writer", pid=4242, host="box-1", purpose="job", started_at=1790942400.0)

    @contextlib.contextmanager
    def refuse(data_dir: str, name: str, purpose: str) -> Iterator[None]:
        raise held
        yield  # pragma: no cover

    i18n = I18nManager()
    i18n.set_language(lang)
    monkeypatch.setattr(cli, "get_i18n", lambda: i18n)
    monkeypatch.setattr(cli, "_data_dir_lease", refuse)

    # Yalnızca yeniden denetim hâlâ main.py'nin kendi kilidini alır; indirme ve yenilemenin kilidini iş yöneticisi
    # alır (P11) ve reddedildiğinde aynı LeaseHeld aynı dala düşer (yukarıdaki test ve goldenlar).
    assert run_cli("--recheck-unavailable") == 6

    assert capsys.readouterr().err.strip() == expected


# --- --watch -----------------------------------------------------------------------------------------


class FakeWatcher:
    """MatchWatcher'ın yerine geçer: run() sırasında izleyici kilidinin sahibini kaydeder."""

    seen: List[Any] = []

    def __init__(self, sport: str, *, event_ids: Any, league_ids: Any, on_event: Any, data_dir: str) -> None:
        self.sport = sport
        self.data_dir = data_dir
        self.requests = 0
        self.events_path = os.path.join(data_dir, "watch_events.jsonl")

    def run(self, until_seconds: Optional[float] = None) -> None:
        FakeWatcher.seen.append(lease_holder(Path(self.data_dir), f"watcher:{self.sport}"))


def test_watch_holds_the_watcher_lease_of_its_sport(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    FakeWatcher.seen = []
    monkeypatch.setattr("src.watcher.MatchWatcher", FakeWatcher)

    assert run_cli("--watch", "--sport", "tennis", "--event-ids", "1") == 0

    held = FakeWatcher.seen[0]
    assert (held.name, held.pid, held.purpose) == ("watcher:tennis", os.getpid(), "watch")
    assert lease_holder(data_dir, "watcher:tennis") is None
    assert "Watcher stopped: 0 requests" in capsys.readouterr().err


def test_a_second_watcher_of_the_same_sport_is_refused_but_another_sport_is_not(
    run_cli: RunCli, data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    FakeWatcher.seen = []
    monkeypatch.setattr("src.watcher.MatchWatcher", FakeWatcher)
    initialise_store(data_dir)

    with LeaseManager.for_data_dir(data_dir).acquire("watcher:tennis", purpose="watch"):
        assert run_cli("--watch", "--sport", "tennis", "--event-ids", "1") == 6
        assert run_cli("--watch", "--sport", "football", "--event-ids", "1") == 0

    assert len(FakeWatcher.seen) == 1 and FakeWatcher.seen[0].name == "watcher:football"
    assert "lock watcher:tennis" in capsys.readouterr().err


def test_watch_usage_error_comes_before_the_lease(run_cli: RunCli, data_dir: Path) -> None:
    assert run_cli("--watch") == 2
    assert not data_dir.exists()
