"""
Yeni CLI'nin veri komutları (plan maddesi P19; docs/design/02-services.md bölüm 4.1, 4.4-4.6):

  sync / fetch / refresh   SyncService bir iş olarak: belirtim, kilit, iş satırı, çıkış kodları, --dry-run,
                           --wait, --progress, sink'ler
  export                   legacy-wide-csv (veri klasörüne, dosyaya, stdout'a), ham dışa aktarma, boş seçim
  data                     clear (onay, kapsam), recheck-unavailable (yazar kilidi)
  follows                  list, add, remove, export
  status                   boş dizin, depo, --check
  jobs                     list, show, cancel, tail (akış satırları)
  genel                    --log-format json, akış komutlarının hata satırı ve kapanan boru

Komutlar aynı süreçte, `sofascore_scraper.cli.main.main()` çağrılarak çalışır (`cli` fixture'ı tests/test_cli_skeleton.py'de:
süreç durumunu geri alır). Servisler sahtedir: hiçbir test SofaScore'a bağlanmaz. Ayrı süreçte gerçek bir indirme
tests/characterization/test_cli_goldens.py'dedir.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import test_cli_skeleton as skeleton
from sofascore_scraper.cli import output, removed_flags
from sofascore_scraper.cli import main as cli_main
from sofascore_scraper.cli.commands import sync as sync_command
from sofascore_scraper.jobs.manager import JobManager
from sofascore_scraper.jobs.model import JobKind, JobState
from sofascore_scraper.services.sync import RefreshCounts, SyncResult, SyncService, SyncSpec
from sofascore_scraper.store import JobStore, open_store
from sofascore_scraper.store.lease import LeaseManager
from test_cli_skeleton import CliRunner, Run, Sandbox

cli = skeleton.cli
box = skeleton.box

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


def jobs_of(data_dir: Path) -> List[Any]:
    return JobManager(JobStore.for_store(open_store(data_dir))).list()


class FakeSync:
    """SyncService.run'ın yerine geçer: belirtimi, iş kimliğini ve o anki ortamı kaydeder; sonucu `outcome` belirler."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.specs: List[SyncSpec] = []
        self.handles: List[Any] = []
        self.env: List[Optional[str]] = []
        self.breaker: Optional[str] = None
        self.failed = 0
        self.failed_listings = 0
        self.refresh: Optional[RefreshCounts] = None
        self.wait_for_cancel = False
        self.on_run: Optional[Any] = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "FakeSync":
        fake = self

        def run(self: SyncService, spec: SyncSpec, *, handle: Any = None) -> SyncResult:
            from sofascore_scraper.services.sync import FailedListing

            fake.specs.append(spec)
            fake.handles.append(handle)
            fake.env.append(os.environ.get("REFRESH_LEGACY"))
            handle.progress.start_phase("details", 2)
            handle.log("Checking which matches need details...")
            if fake.on_run is not None:
                fake.on_run(handle)
            if fake.wait_for_cancel:
                deadline = time.monotonic() + 10
                while not handle.cancelled() and time.monotonic() < deadline:
                    time.sleep(0.01)
                return SyncResult(state="cancelled", schedule_empty_seasons=0, breaker=None,
                                  progress=handle.progress.result())
            for n in range(fake.failed):
                handle.progress.add_failed(str(n))
            if fake.breaker:
                handle.progress.breaker(fake.breaker)
            handle.progress.advance(2)
            progress = handle.progress.result()
            listings = tuple(FailedListing("seasons", 17, None, "403") for _ in range(fake.failed_listings))
            partial = progress["breaker"] or progress["failed_count"] or listings or (
                fake.refresh is not None and fake.refresh.failed)
            return SyncResult(state="partial" if partial else "succeeded", schedule_empty_seasons=0,
                              breaker=progress["breaker"], progress=progress, refresh=fake.refresh,
                              failed_listings=listings)

        monkeypatch.setattr(SyncService, "run", run)
        return self


@pytest.fixture
def fake_sync(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> FakeSync:
    return FakeSync(data_dir).install(monkeypatch)


def run_in(cli: CliRunner, data_dir: Path, *argv: str, cwd: Optional[Path] = None) -> Run:
    return cli("--data-dir", str(data_dir), *argv, cwd=cwd)


# === sync, fetch, refresh =======================================================================


def test_sync_runs_the_service_as_a_job_and_prints_the_envelope(cli: CliRunner, data_dir: Path,
                                                                 fake_sync: FakeSync) -> None:
    run = run_in(cli, data_dir, "sync", "--tournament", "17", "--json")

    assert run.exit_code == 0, run.stderr
    data = run.data
    assert fake_sync.specs == [SyncSpec(mode="full", league_id=17)]
    (job,) = jobs_of(data_dir)
    assert (job.kind, job.state, job.origin.face) == (JobKind.SYNC, JobState.SUCCEEDED, "cli")
    assert data["job_id"] == job.id and data["state"] == "succeeded" and data["kind"] == "sync"
    assert data["counts"]["events_needed"] == 2 and data["counts"]["events_stored"] == 2
    assert data["stopped"] is None and data["failed_listings"] == [] and data["cancelled_by_signal"] is None
    assert isinstance(data["duration_seconds"], float)
    assert run.stderr == ""  # --json: tek belge, log satırları stderr'e (burada yok: pytest yakalar)


@pytest.mark.parametrize("setup, exit_code, state", [
    ({}, 0, "succeeded"),
    ({"failed": 1}, 3, "partial"),
    ({"failed_listings": 1}, 3, "partial"),
    ({"breaker": "403"}, 4, "partial"),
    ({"breaker": "429", "failed": 2}, 4, "partial"),
])
def test_sync_exit_codes_follow_the_result(cli: CliRunner, data_dir: Path, fake_sync: FakeSync,
                                           setup: Dict[str, Any], exit_code: int, state: str) -> None:
    for key, value in setup.items():
        setattr(fake_sync, key, value)

    run = run_in(cli, data_dir, "sync", "--json")

    assert run.exit_code == exit_code
    assert run.data["state"] == state
    assert exit_code == 0 or run.json["ok"] is True  # sonucun durumu çıkış kodunda; komut çalıştı


def test_sync_text_output_and_notes(cli: CliRunner, data_dir: Path, fake_sync: FakeSync) -> None:
    fake_sync.failed_listings = 2
    run = run_in(cli, data_dir, "sync")
    assert run.exit_code == 3
    assert run.stdout.startswith("Download finished. Matches that needed details: 2 (2 fetched, 0 failed).")
    assert "2 season list(s) or schedule(s) could not be fetched" in run.stderr

    fake_sync.failed_listings, fake_sync.breaker = 0, "403"
    stopped = run_in(cli, data_dir, "sync")
    assert (stopped.exit_code, stopped.stdout) == (4, "")
    assert "too many requests to SofaScore failed (403)" in stopped.stderr


def test_sync_options_become_the_spec(cli: CliRunner, data_dir: Path, fake_sync: FakeSync) -> None:
    assert run_in(cli, data_dir, "sync", "--only", "events").exit_code == 0
    assert run_in(cli, data_dir, "fetch", "tournament", "8", "--season", "61627", "--season", "52186").exit_code == 0
    assert run_in(cli, data_dir, "fetch", "tournament", "8", "--only", "events").exit_code == 0
    assert run_in(cli, data_dir, "fetch", "event", "9100001", "9100002", "9100001").exit_code == 0

    from sofascore_scraper.services.sync import SyncSelection

    assert fake_sync.specs == [
        SyncSpec(mode="details", league_id=None),
        SyncSpec(mode="full", league_id=8, selections=(SyncSelection(8, season_ids=(61627, 52186)),)),
        SyncSpec(mode="details", league_id=8),
        # Katalogda bilinmeyen maçların turnuvası 0'dır (yalnızca iş kartında görünür)
        SyncSpec(mode="details", selections=(SyncSelection(0, match_ids=(9100001, 9100002)),)),
    ]
    assert [job.kind for job in jobs_of(data_dir)] == [JobKind.FETCH, JobKind.FETCH, JobKind.FETCH, JobKind.SYNC]


def test_fetch_tournament_rejects_seasons_with_only_events(cli: CliRunner, data_dir: Path,
                                                          fake_sync: FakeSync) -> None:
    run = run_in(cli, data_dir, "fetch", "tournament", "8", "--season", "1", "--only", "events", "--json")
    assert (run.exit_code, run.error["code"]) == (2, "invalid_request")
    assert fake_sync.specs == []


def test_refresh_and_its_include_legacy_switch(cli: CliRunner, data_dir: Path, fake_sync: FakeSync,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REFRESH_LEGACY", raising=False)
    fake_sync.refresh = RefreshCounts(due=3, refreshed=2, changed=1, failed=1)

    run = run_in(cli, data_dir, "refresh", "--tournament", "17", "--include-legacy")

    assert run.exit_code == 3
    assert run.stdout.strip() == "Refresh: 2 matches refreshed, 1 changed, 1 failed"
    assert fake_sync.specs == [SyncSpec(mode="refresh", league_id=17)]
    assert fake_sync.env == ["true"]  # bu çalıştırma için REFRESH_LEGACY
    (job,) = jobs_of(data_dir)
    assert job.kind is JobKind.REFRESH and job.result["refresh"]["failed"] == 1


def test_recheck_inside_sync_runs_before_the_service_under_the_same_job(
    cli: CliRunner, data_dir: Path, fake_sync: FakeSync, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sofascore_scraper.services.maintenance import MaintenanceService, ResetCounts

    order: List[str] = []
    monkeypatch.setattr(MaintenanceService, "recheck_unavailable",
                        lambda self, league_id=None, include_confirmed=False: order.append(
                            f"recheck {league_id} {include_confirmed}") or ResetCounts(1, 2, 3))
    fake_sync.on_run = lambda handle: order.append("sync")

    run = run_in(cli, data_dir, "sync", "--tournament", "17", "--recheck-unavailable", "all", "--json")

    assert run.exit_code == 0 and order == ["recheck 17 True", "sync"]
    assert run.data["recheck"] == {"matches": 1, "slices": 2, "scanned": 3}
    assert run_in(cli, data_dir, "sync", "--dry-run", "--recheck-unavailable").exit_code == 2


def test_a_held_writer_lease_is_exit_6_with_the_holder_and_wait_waits_for_it(
    cli: CliRunner, data_dir: Path, fake_sync: FakeSync
) -> None:
    open_store(data_dir)
    lease = LeaseManager.for_data_dir(data_dir).acquire("writer", purpose="job")
    refused = run_in(cli, data_dir, "sync", "--json")
    assert (refused.exit_code, refused.error["code"]) == (6, "job_running")
    assert refused.error["details"]["holder"]["purpose"] is None or "holder" in refused.error["details"]
    assert fake_sync.specs == []

    timer = threading.Timer(0.3, lease.release)
    timer.start()
    try:
        waited = run_in(cli, data_dir, "--wait", "10", "sync")
    finally:
        timer.join()
    assert waited.exit_code == 0 and len(fake_sync.specs) == 1


def test_a_job_cancelled_from_another_process_exits_130_and_still_prints_the_result(
    cli: CliRunner, data_dir: Path, fake_sync: FakeSync
) -> None:
    fake_sync.wait_for_cancel = True

    def cancel_soon(handle: Any) -> None:
        other = JobStore(os.path.join(str(data_dir), ".meta", "state.db"))
        try:
            assert other.cancel(handle.id) is True
        finally:
            other.close()

    fake_sync.on_run = cancel_soon
    run = run_in(cli, data_dir, "sync", "--json")

    assert run.exit_code == 130
    assert run.data["state"] == "cancelled" and run.data["cancelled_by_signal"] is None
    (job,) = jobs_of(data_dir)
    assert job.state is JobState.CANCELLED


def test_progress_ndjson_writes_job_events_to_stderr(cli: CliRunner, data_dir: Path, fake_sync: FakeSync) -> None:
    run = run_in(cli, data_dir, "--progress", "ndjson", "sync", "--json")

    assert run.exit_code == 0
    lines = [json.loads(line) for line in run.stderr.splitlines() if line.startswith("{")]
    types = [line["type"] for line in lines]
    assert types[0] == "job.started" and types[-1] == "job.finished"
    assert "job.phase" in types and "job.log" in types
    assert all(line["job_id"] == run.data["job_id"] and line["ts"].endswith("Z") for line in lines)
    assert [line["seq"] for line in lines] == sorted(line["seq"] for line in lines)


def test_progress_text_writes_readable_lines(cli: CliRunner, data_dir: Path, fake_sync: FakeSync) -> None:
    run = run_in(cli, data_dir, "--progress", "text", "sync")
    assert run.exit_code == 0
    assert "Checking which matches need details..." in run.stderr
    assert "Job finished: succeeded" in run.stderr


def test_configured_sinks_get_the_events_of_a_one_shot_job(cli: CliRunner, data_dir: Path, fake_sync: FakeSync,
                                                         tmp_path: Path) -> None:
    feed = tmp_path / "out" / "jobs.ndjson"
    config = tmp_path / "sofascore.toml"
    config.write_text(f'schema = 1\n[[sink]]\nname = "feed"\ntype = "file"\npath = {json.dumps(str(feed))}\n'
                      f'events = ["job.*"]\n', encoding="utf-8")

    run = run_in(cli, data_dir, "--config", str(config), "sync", "--json")

    assert run.exit_code == 0
    events = [json.loads(line) for line in feed.read_text(encoding="utf-8").splitlines()]
    # Dağıtıcı işten önce kaydedildi: işin kendi `job.started` olayı da teslim edildi; çıkışta boşaltıldı
    assert [event["type"] for event in events] == ["job.started", "job.finished"]
    assert events[1]["data"]["state"] == "succeeded"


def test_a_broken_sink_stops_the_command_before_the_job(cli: CliRunner, data_dir: Path, fake_sync: FakeSync,
                                                        tmp_path: Path) -> None:
    config = tmp_path / "sofascore.toml"
    config.write_text(f'schema = 1\n[[sink]]\nname = "feed"\ntype = "file"\npath = '
                      f'{json.dumps(str(data_dir / "inside.ndjson"))}\n', encoding="utf-8")

    run = run_in(cli, data_dir, "--config", str(config), "sync", "--json")

    assert (run.exit_code, run.error["code"]) == (2, "config_invalid")
    assert "inside the data directory" in run.error["message"] and fake_sync.specs == []


# --- kuru çalıştırma ------------------------------------------------------------------------------


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kuru çalıştırma hiçbir istek atmaz ve iş başlatmaz."""
    from sofascore_scraper import utils

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a dry run must not send a request")

    monkeypatch.setattr(utils.cffi_requests, "get", refuse)
    monkeypatch.setattr(SyncService, "run", refuse)
    monkeypatch.setattr(JobManager, "start", refuse)


def test_dry_runs_plan_without_requests_jobs_or_locks(cli: CliRunner, data_dir: Path, no_network: None) -> None:
    sync = run_in(cli, data_dir, "sync", "--dry-run", "--json")
    assert sync.exit_code == 0, sync.stderr
    plan = sync.data
    assert plan["dry_run"] is True and plan["requests_is_minimum"] is True
    assert plan["listings"]["season_lists"] == len(plan["tournaments"]) >= 1  # yapılandırmadaki lig(ler)
    assert plan["requests"] >= plan["listings"]["season_lists"]

    event = run_in(cli, data_dir, "fetch", "event", "123", "--dry-run", "--json")
    assert event.exit_code == 0 and event.data["events"]["full"] == 1 and event.data["requests"] >= 2

    refresh = run_in(cli, data_dir, "refresh", "--dry-run")
    assert refresh.exit_code == 0 and refresh.stdout.strip() == (
        "Matches that need requests: 0 without a record, 0 with missing slices, 0 to refresh.\nRequests: 0.")
    assert jobs_of(data_dir) == []


# === export =====================================================================================


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    """Testlerin veri dizininin kopyası (tests/conftest.py): bir maçın detayı eski düzende saklı."""
    target = tmp_path / "seeded"
    shutil.copytree(os.environ["DATA_DIR"], target, ignore=shutil.ignore_patterns(".meta"))
    return target


def test_export_writes_the_wide_csv_to_the_legacy_place_a_file_or_stdout(cli: CliRunner, seeded: Path,
                                                                         tmp_path: Path) -> None:
    default = run_in(cli, seeded, "export", "--json")
    assert default.exit_code == 0, default.stderr
    path = Path(default.data["path"])
    assert path.parent == seeded / "match_details" / "processed" and path.name.startswith("all_matches_")
    assert default.data["rows"] == 1 and path.read_text(encoding="utf-8").startswith("match_id,")

    out = run_in(cli, seeded, "export", "--out", "x.csv", cwd=tmp_path)
    assert out.exit_code == 0 and (tmp_path / "x.csv").read_bytes() == path.read_bytes()
    assert out.stdout.strip() == f"Export written: {tmp_path / 'x.csv'} (1 matches)"

    streamed = run_in(cli, seeded, "export", "--out", "-")
    assert streamed.exit_code == 0 and streamed.stdout == path.read_bytes().decode("utf-8")  # satır sonu \r\n
    assert "Export written to stdout: 1 matches" in streamed.stderr


def test_export_with_nothing_to_export_is_not_found_and_writes_nothing(cli: CliRunner, data_dir: Path,
                                                                      seeded: Path, tmp_path: Path) -> None:
    empty = run_in(cli, data_dir, "export", "--json")
    assert (empty.exit_code, empty.error["code"]) == (1, "not_found")
    filtered = run_in(cli, seeded, "export", "--tournament", "999", "--out", "none.csv", cwd=tmp_path)
    assert filtered.exit_code == 1 and not (tmp_path / "none.csv").exists()


def test_raw_export_and_its_usage_rules(cli: CliRunner, seeded: Path, tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    raw = run_in(cli, seeded, "export", "--schema", "raw", "--out", str(tmp_path / "raw.jsonl"), "--json")
    assert raw.exit_code == 0, raw.stderr
    lines = [json.loads(line) for line in (tmp_path / "raw.jsonl").read_text(encoding="utf-8").splitlines()]
    assert raw.data["events"] == 1 and {line["key"] for line in lines} >= {"event"}

    again = run_in(cli, seeded, "export", "--schema", "raw", "--out", str(tmp_path / "raw.jsonl"), "--json")
    assert (again.exit_code, again.error["code"]) == (5, "storage_error")  # hedef var; --force yok
    forced = run_in(cli, seeded, "export", "--schema", "raw", "--out", str(tmp_path / "raw.jsonl"), "--force")
    assert forced.exit_code == 0

    # SC-2: normalleştirilmiş veri kümeleri yazılır (tests/test_export_datasets.py); `pyarrow` olmadan Parquet
    # yazılamaz
    monkeypatch.setattr("sofascore_scraper.services.export.parquet_available", lambda: False)
    for argv, code in ((["--schema", "raw"], "invalid_request"), (["--schema", "raw", "--out", "-"], "invalid_request"),
                       (["--schema", "normalized", "--format", "parquet"], "not_supported"),
                       (["--format", "jsonl"], "invalid_request"),
                       (["--out", "-", "--json"], "invalid_request")):
        run = run_in(cli, seeded, "export", *argv, "--json")
        assert (run.exit_code, run.error["code"]) == (2, code), argv


# === data =======================================================================================


def test_data_clear_needs_a_scope_and_a_confirmation(cli: CliRunner, seeded: Path) -> None:
    assert run_in(cli, seeded, "data", "clear", "--json").error["code"] == "invalid_request"
    unconfirmed = run_in(cli, seeded, "data", "clear", "--scope", "events", "--json")
    assert (unconfirmed.exit_code, unconfirmed.error["code"]) == (2, "confirmation_required")
    assert (seeded / "match_details").is_dir() and any((seeded / "match_details").iterdir())

    cleared = run_in(cli, seeded, "data", "clear", "--scope", "events", "--yes", "--json")
    assert cleared.exit_code == 0, cleared.stderr
    assert cleared.data["scope"] == "events" and "match_details" in cleared.data["cleared"]
    assert open_store(seeded).events.get(9000001) is None or not open_store(seeded).events.get(9000001).has_event_payload


def test_data_clear_is_refused_while_a_job_runs(cli: CliRunner, seeded: Path) -> None:
    open_store(seeded)
    with LeaseManager.for_data_dir(seeded).acquire("writer", purpose="job"):
        run = run_in(cli, seeded, "data", "clear", "--all", "--yes", "--json")
    assert run.exit_code == 6


def test_data_recheck_unavailable_holds_the_writer_lease(cli: CliRunner, data_dir: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from sofascore_scraper.services.maintenance import MaintenanceService, ResetCounts

    seen: List[Any] = []

    def recheck(self: MaintenanceService, league_id: Optional[int] = None, *, include_confirmed: bool = False) -> Any:
        holder = open_store(data_dir).lease_holder("writer")
        seen.append((league_id, include_confirmed, holder.purpose if holder is not None else None))
        return ResetCounts(matches=1, slices=2, scanned=3)

    monkeypatch.setattr(MaintenanceService, "recheck_unavailable", recheck)

    run = run_in(cli, data_dir, "data", "recheck-unavailable", "--all", "--tournament", "17", "--json")

    assert run.exit_code == 0
    assert seen == [(17, True, "recheck-unavailable")]
    assert run.data == {"tournament": 17, "include_confirmed": True, "matches": 1, "slices": 2, "scanned": 3}


# === follows ====================================================================================


def test_follows_add_list_export_and_remove(cli: CliRunner, data_dir: Path) -> None:
    assert run_in(cli, data_dir, "follows", "list", "--json").data == {"follows": []}  # depo yok: boş

    added = run_in(cli, data_dir, "follows", "add", "tournament", "8", "--name", "LaLiga", "--sport", "football",
                   "--seasons", "last:2", "--live", "--json")
    assert added.exit_code == 0, added.stderr
    assert {key: added.data["follow"][key] for key in ("kind", "entity_id", "name", "seasons", "live", "origin")} == {
        "kind": "tournament", "entity_id": 8, "name": "LaLiga", "seasons": "last:2", "live": True, "origin": "api"}

    again = run_in(cli, data_dir, "follows", "add", "tournament", "8", "--json")
    assert (again.exit_code, again.error["code"]) == (1, "follow_exists")
    bad = run_in(cli, data_dir, "follows", "add", "team", "5", "--sport", "curling", "--json")
    assert (bad.exit_code, bad.error["code"]) == (2, "invalid_request") and "curling" in bad.error["message"]

    listed = run_in(cli, data_dir, "follows", "list")
    assert "tournament 8: LaLiga (football, seasons last:2, from api) [live]" in listed.stdout

    exported = run_in(cli, data_dir, "follows", "export")
    assert exported.exit_code == 0
    from sofascore_scraper.config import loader

    assert exported.stdout.startswith("[[follow]]\ntournament = 8\nname = \"LaLiga\"\n")
    import tomllib  # noqa: PLC0415 (Python 3.11+)

    parsed = loader.parse_follows(tomllib.loads(exported.stdout)["follow"], "all", "export")
    assert [(f.kind, f.entity_id, f.seasons, f.live) for f in parsed] == [("tournament", 8, "last:2", True)]

    assert run_in(cli, data_dir, "follows", "remove", "tournament", "8", "--json").data["removed"] is True
    gone = run_in(cli, data_dir, "follows", "remove", "tournament", "8")
    assert gone.exit_code == 0 and "is not followed" in gone.stderr


if sys.version_info < (3, 11):  # tomllib 3.11 ile geldi; dışa aktarmanın ayrıştırılması orada sınanır
    test_follows_add_list_export_and_remove = pytest.mark.skip(reason="tomllib needs Python 3.11")(  # type: ignore
        test_follows_add_list_export_and_remove)


# === status =====================================================================================


def test_status_of_a_folder_that_is_not_a_store_creates_nothing(cli: CliRunner, data_dir: Path) -> None:
    run = run_in(cli, data_dir, "status", "--json")
    assert run.exit_code == 0 and run.data == {"data_dir": str(data_dir), "store": None, "healthy": True}
    assert not data_dir.exists()


def test_status_shows_the_store_the_jobs_and_the_live_service(cli: CliRunner, data_dir: Path,
                                                             fake_sync: FakeSync) -> None:
    assert run_in(cli, data_dir, "sync").exit_code == 0
    run = run_in(cli, data_dir, "status", "--json", "--coverage")
    assert run.exit_code == 0
    data = run.data
    assert data["healthy"] is True and data["store"]["layout_version"] == 3
    assert data["jobs"]["running"] is None and data["jobs"]["last"]["state"] == "succeeded"
    assert data["live"]["running"] is False and data["leases"] == [] and isinstance(data["coverage"], list)
    text = run_in(cli, data_dir, "status")
    assert "Running job: none" in text.stdout and "Last job: " in text.stdout and "Live service: not running" in text.stdout
    check = run_in(cli, data_dir, "status", "--check")
    assert (check.exit_code, check.stdout) == (0, "")


def test_status_check_fails_when_the_catalog_must_be_rebuilt(cli: CliRunner, data_dir: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses

    from sofascore_scraper.store.api import Store

    store = open_store(data_dir)
    real = Store.info
    monkeypatch.setattr(Store, "info", lambda self, sizes=True: dataclasses.replace(
        real(self, sizes=sizes), catalog_rebuild_reason="schema_mismatch"))
    assert store is not None
    run = run_in(cli, data_dir, "status", "--check")
    assert (run.exit_code, run.stdout) == (1, "")


# === jobs =======================================================================================


def test_jobs_list_show_cancel_and_tail(cli: CliRunner, data_dir: Path, fake_sync: FakeSync) -> None:
    assert run_in(cli, data_dir, "jobs", "list").stdout.strip() == "No jobs."
    job_id = run_in(cli, data_dir, "sync", "--json").data["job_id"]

    listed = run_in(cli, data_dir, "jobs", "list", "--json").data["jobs"]
    assert [job["id"] for job in listed] == [job_id] and listed[0]["state"] == "succeeded"
    assert run_in(cli, data_dir, "jobs", "list", "--kind", "refresh", "--json").data["jobs"] == []

    shown = run_in(cli, data_dir, "jobs", "show", job_id, "--json").data["job"]
    assert (shown["kind"], shown["origin"]["face"]) == ("sync", "cli")
    missing = run_in(cli, data_dir, "jobs", "show", "nope", "--json")
    assert (missing.exit_code, missing.error["code"]) == (1, "not_found")

    cancel = run_in(cli, data_dir, "jobs", "cancel", job_id, "--json")
    assert cancel.exit_code == 0 and cancel.data == {"job_id": job_id, "cancelled": False, "state": "succeeded"}

    tail = run_in(cli, data_dir, "jobs", "tail", job_id)
    lines = [json.loads(line) for line in tail.stdout.splitlines()]
    assert lines[0]["type"] == "job.started" and lines[-2]["type"] == "job.finished"
    assert lines[-1] == {"type": "end", "job_id": job_id, "last_seq": lines[-2]["seq"], "count": len(lines) - 1,
                         "state": "succeeded"}
    after = run_in(cli, data_dir, "jobs", "tail", job_id, "--after", str(lines[-4]["seq"]), "--json").data
    assert after["count"] == 2 and [event["type"] for event in after["events"]][-1] == "job.finished"
    assert run_in(cli, data_dir, "jobs", "tail", job_id, "--follow", "--json").exit_code == 2


def test_jobs_cancel_reaches_a_job_of_another_process(cli: CliRunner, box: Sandbox, data_dir: Path,
                                                     fake_sync: FakeSync) -> None:
    """
    `jobs cancel` (ayrı bir süreçte) iş satırına yazar; işi çalıştıran (bu süreçte, ayrı bir thread) onu görür,
    iş `cancelled` biter ve komut 130 ile çıkar.
    """
    fake_sync.wait_for_cancel = True
    started = threading.Event()
    fake_sync.on_run = lambda handle: started.set()
    results: Dict[str, int] = {}

    def run_job() -> None:
        results["code"] = cli_main.main(["--data-dir", str(data_dir), "--quiet", "sync"], prog="ssc")

    worker = threading.Thread(target=run_job)
    worker.start()
    try:
        assert started.wait(10)
        (job,) = jobs_of(data_dir)
        cancel = box.run("--data-dir", str(data_dir), "jobs", "cancel", job.id, "--json")
    finally:
        worker.join(30)
    assert cancel.exit_code == 0, cancel.stderr
    assert cancel.data == {"job_id": job.id, "cancelled": True, "state": "running"}
    assert results["code"] == 130
    assert jobs_of(data_dir)[0].state is JobState.CANCELLED


# === genel ======================================================================================


def test_log_format_json_writes_one_object_per_line(box: Sandbox) -> None:
    run = box.run("--log-format", "json", "--data-dir", str(box.root / "data"), "export", "--json")
    assert run.exit_code == 1  # dışa aktarılacak maç yok
    lines = [json.loads(line) for line in run.stderr.splitlines()]
    assert lines and all(sorted(line) == ["level", "logger", "message", "pid", "time"] for line in lines)
    assert all(line["time"].endswith("Z") for line in lines)


def test_a_stream_error_is_a_typed_line_and_the_result_envelope_is_not_printed() -> None:
    import io

    from sofascore_scraper.errors import PlatformError

    stdout, stderr = io.StringIO(), io.StringIO()
    out = output.Output(stdout=stdout, stderr=stderr)
    out.line({"type": "job.log", "seq": 1})
    out.error("jobs tail", PlatformError("storage_error", "disk full"), 5)
    out.result("jobs tail", output.CommandResult(data={"ignored": True}, text="ignored",
                                                 notes=["a note"]))
    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert lines == [{"type": "job.log", "seq": 1},
                     {"type": "error", "error": {"code": "storage_error", "message": "disk full", "details": None},
                      "exit_code": 5}]
    assert "storage_error: disk full" in stderr.getvalue() and "a note" in stderr.getvalue()


@pytest.mark.skipif(os.name == "nt", reason="kapanan boru POSIX'te EPIPE'dir")
def test_a_reader_that_closes_a_stream_early_is_not_an_error(box: Sandbox) -> None:
    """`ssc events | head -1`: akış komutunda okuyanın kapanması başarıdır (P19'dan önce çıkış kodu 1)."""
    data = box.root / "data"
    open_store(data).streams.append("system", [_system_event(n) for n in range(2000)])
    proc = subprocess.Popen(
        [sys.executable, "-m", "sofascore_scraper.cli.main", "--data-dir", str(data), "events"], cwd=box.cwd,
        env=box.environ(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None and proc.stderr is not None
    first = proc.stdout.readline()
    proc.stdout.close()
    stderr = proc.stderr.read().decode("utf-8")
    proc.stderr.close()
    assert json.loads(first)["type"] == "system.test"
    assert proc.wait(timeout=120) == 0, stderr
    assert "Traceback" not in stderr


def _system_event(number: int) -> Any:
    from sofascore_scraper.store import StreamEvent

    return StreamEvent(type="system.test", source="test", data={"n": number, "pad": "x" * 200})


def test_the_new_commands_are_registered_and_described(cli: CliRunner) -> None:
    from sofascore_scraper.cli import commands as registry

    names = {command.name for command in registry.commands()}
    assert {"sync", "fetch event", "fetch tournament", "refresh", "export", "data clear", "data recheck-unavailable",
            "follows list", "follows add", "follows remove", "follows export", "status", "jobs list", "jobs show",
            "jobs cancel", "jobs tail"} <= names
    described = {command["name"]: command for command in cli("describe", "commands").data["commands"]["commands"]}
    assert [option["flags"][0] for option in described["sync"]["options"]] == [
        "--tournament", "--follow", "--only", "--recheck-unavailable", "--include-legacy", "--dry-run"]


def test_main_py_passes_a_subcommand_through(box: Sandbox) -> None:
    proc = subprocess.run([sys.executable, str(REPO / "main.py"), "status", "--json"], cwd=box.cwd,
                          env=box.environ(), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["command"] == "status"
    assert "removed" not in proc.stderr  # bir alt komut kaldırılmış bir bayrak değildir


def test_the_removed_flags_name_the_new_command() -> None:
    assert removed_flags.replacement(["--headless", "--update-all", "--league-id"]) == "sync --tournament"
    assert removed_flags.replacement(["--refresh-only", "--update-all"]) == "refresh"  # 2.x'in önceliği
    assert removed_flags.replacement(["--headless"]) is None
    message = removed_flags.message(["--headless", "--csv-export"], "ssc")
    assert message.startswith("--headless --csv-export: the flags of `python main.py` were removed in 3.1")
    assert "use `ssc export`" in message


def test_sync_module_helpers() -> None:
    assert sync_command.iso_ms(1790942400123) == "2026-10-02T12:00:00.123Z"
    assert sync_command.iso_ms(None) is None

