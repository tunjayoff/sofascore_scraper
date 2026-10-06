"""
Uygulama içi zamanlayıcı (src/jobs/scheduler.py; plan maddesi P29). Tümü çevrimdışı ve sahte saatle.

  * tetikleyiciler: aralık (`every`) ve cron; kaçan anların birleştirilmesi;
  * görevlerin denetimi: bilinmeyen `run` adı ve seçenek reddedilir;
  * zamanlayıcı: zamanı gelen görev `origin=scheduler` ile iş olarak başlatılır, önceki çalışması süren ya da
    kilidi meşgul görev atlanır ve loglanır, durdurma süren işi iptal eder; gerçek iş yöneticisi ve kilitle;
  * durum: `/api/v1/status` `schedule` ve `capabilities.scheduler`;
  * `ssc serve --scheduler`: varsayılan kapalı, görevsiz uyarı, `--dev` ile birlikte kullanım hatası,
    geçersiz görev sunucu başlamadan reddedilir.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional

import conftest
import pytest

import test_cli_skeleton as skeleton
from src.config import ScheduleTask
from src.exceptions import ConfigError
from src.jobs import scheduler as scheduler_mod
from src.jobs.manager import JobManager, JobOutcome, local_origin
from src.jobs.model import JobKind, JobState
from src.jobs.scheduler import (
    FAILED_TO_START,
    SKIPPED_BUSY,
    SKIPPED_RUNNING,
    STARTED,
    CronTrigger,
    EveryTrigger,
    Scheduler,
    TaskPlan,
    TaskRun,
    check_tasks,
    current,
)
from src.store import JobRunningError, JobStore, StoreError
from src.store.jobs import default_db_path
from test_cli_serve import FakeServer, server  # noqa: F401  (fixture)
from test_cli_skeleton import CliRunner

cli = skeleton.cli
box = skeleton.box

UTC = timezone.utc
HOUR = 3600.0


def at(*args: int) -> float:
    return datetime(*args, tzinfo=UTC).timestamp()


def text(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d %H:%M %a")


def task(run: str = "sync", *, every: Optional[str] = None, seconds: Optional[float] = None,
         cron: Optional[str] = None, **options: Any) -> ScheduleTask:
    if every is None and cron is None:
        every, seconds = "6h", 6 * HOUR
    return ScheduleTask(run=run, every=every, every_seconds=seconds, cron=cron, options=MappingProxyType(options))


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeJobs:
    """İş yöneticisinin zamanlayıcının kullandığı yüzü: submit, get, cancel."""

    def __init__(self, *, finish_at_once: bool = False) -> None:
        self.submitted: List[Dict[str, Any]] = []
        self.states: Dict[str, JobState] = {}
        self.cancelled: List[str] = []
        self.raises: Optional[BaseException] = None
        self.finish_at_once = finish_at_once

    def submit(self, kind: Any, spec: Any, fn: Any, **kwargs: Any) -> Any:
        if self.raises is not None:
            raise self.raises
        job_id = f"job-{len(self.submitted) + 1}"
        self.submitted.append({"kind": kind, "spec": spec, "fn": fn, **kwargs})
        self.states[job_id] = JobState.SUCCEEDED if self.finish_at_once else JobState.RUNNING
        return SimpleNamespace(id=job_id, kind=kind)

    def get(self, job_id: str) -> Any:
        state = self.states.get(job_id)
        return None if state is None else SimpleNamespace(id=job_id, state=state)

    def cancel(self, job_id: str) -> bool:
        self.cancelled.append(job_id)
        self.states[job_id] = JobState.CANCELLED
        return True

    def finish(self, job_id: str) -> None:
        self.states[job_id] = JobState.SUCCEEDED


@pytest.fixture(autouse=True)
def _no_scheduler_left() -> Iterator[None]:
    yield
    running = current()
    if running is not None:
        running.stop(timeout=5)
    assert current() is None


def make(tasks: List[ScheduleTask], jobs: Any, clock: Clock, **kwargs: Any) -> Scheduler:
    return Scheduler(tasks, jobs=lambda: jobs, clock=clock, tz=UTC, **kwargs)


# === tetikleyiciler ==================================================================================


def test_an_interval_runs_one_interval_after_the_start_and_then_every_interval() -> None:
    trigger = EveryTrigger(6 * HOUR)
    start = at(2026, 10, 3, 10, 0)
    first = trigger.first(start)
    assert first == start + 6 * HOUR
    assert trigger.next_after(first, first) == first + 6 * HOUR


def test_missed_intervals_are_coalesced_into_one_run() -> None:
    trigger = EveryTrigger(HOUR)
    due = at(2026, 10, 3, 10, 0)
    # Makine üç buçuk saat uyudu: tek çalışma, sonraki an saat başlarının düzeninde ve şimdiden sonra
    now = due + 3.5 * HOUR
    assert trigger.next_after(due, now) == due + 4 * HOUR


@pytest.mark.parametrize("expression, now, expected", [
    ("15 */6 * * *", (2026, 10, 3, 10, 0), "2026-10-03 12:15 Sat"),
    ("15 */6 * * *", (2026, 10, 3, 12, 15), "2026-10-03 18:15 Sat"),  # tam o dakika: bir sonraki
    ("0 0 * * *", (2026, 12, 31, 23, 59), "2027-01-01 00:00 Fri"),
    ("30 6 * * mon-fri", (2026, 10, 3, 10, 0), "2026-10-05 06:30 Mon"),  # cumartesi → pazartesi
    ("0 12 * * 7", (2026, 10, 3, 13, 0), "2026-10-04 12:00 Sun"),  # 7 de pazardır
    ("0 0 29 feb *", (2026, 10, 3, 0, 0), "2028-02-29 00:00 Tue"),
    ("5/20 * * * *", (2026, 10, 3, 10, 6), "2026-10-03 10:25 Sat"),
    ("0 9 1,15 * *", (2026, 10, 3, 0, 0), "2026-10-15 09:00 Thu"),
    ("0 8-10/2 * jan,oct *", (2026, 10, 3, 9, 0), "2026-10-03 10:00 Sat"),
])
def test_cron_finds_the_next_matching_minute(expression: str, now: tuple, expected: str) -> None:
    trigger = CronTrigger(expression, UTC)
    assert text(trigger.first(at(*now))) == expected


def test_cron_day_of_month_and_weekday_together_match_either() -> None:
    # Ayın 13'ü ya da cuma (cron'un bilinen kuralı); 2026-10-03 cumartesi
    trigger = CronTrigger("0 0 13 * fri", UTC)
    first = trigger.first(at(2026, 10, 3, 0, 0))
    assert text(first) == "2026-10-09 00:00 Fri"
    assert text(trigger.next_after(first, first)) == "2026-10-13 00:00 Tue"


def test_a_cron_run_that_was_missed_is_not_repeated() -> None:
    trigger = CronTrigger("0 * * * *", UTC)
    due = at(2026, 10, 3, 10, 0)
    assert text(trigger.next_after(due, due + 5.5 * HOUR)) == "2026-10-03 16:00 Sat"


def test_cron_uses_the_local_time_without_a_time_zone() -> None:
    trigger = CronTrigger("30 7 * * *")
    first = datetime.fromtimestamp(trigger.first(time.time()))
    assert (first.hour, first.minute, first.second) == (7, 30, 0)
    assert first - datetime.now() <= timedelta(days=1, minutes=61)


@pytest.mark.parametrize("expression", [
    "61 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *", "* * * * 8", "*/0 * * * *", "5-1 * * * *",
    "* * * * funday", "* * *", "1,,2 * * * *", "0 0 31 2 *",
])
def test_an_invalid_cron_expression_is_rejected(expression: str) -> None:
    with pytest.raises(ValueError):
        CronTrigger(expression, UTC).first(at(2026, 10, 3, 0, 0))


# === görevlerin denetimi =============================================================================


def test_the_known_tasks_pass_the_check() -> None:
    check_tasks([task("sync"), task("fetch", league_id=17), task("refresh", cron="*/15 * * * *"),
                 task("backup", cron="0 3 * * sun", scope="data", include_env=False),
                 task("prune-history", cron="0 4 * * *", older_than="90d")])


@pytest.mark.parametrize("bad, message", [
    (task("download"),
     "#1 run: 'download' is not a task the scheduler knows (sync, fetch, refresh, backup, prune-history)"),
    (task("prune-history"), "#1 older_than: required, a duration such as \"90d\""),
    (task("prune-history", older_than="soon"), "#1 older_than: expected a duration"),
    (task("prune-history", older_than="90d", scope="all"), "unknown option(s) for run = 'prune-history': scope"),
    (task("sync", season_id=5), "unknown option(s) for run = 'sync': season_id (allowed: league_id)"),
    (task("sync", league_id="17"), "league_id: expected a positive whole number"),
    (task("refresh", league_id=0), "league_id: expected a positive whole number"),
    (task("backup", scope="everything"), "scope: expected one of all, state, data"),
    (task("backup", include_env="yes"), "include_env: expected true or false"),
    (task("sync", cron="61 * * * *"), "#1 cron: '61' is not a value from 0 to 59"),
])
def test_an_unknown_task_or_option_is_rejected(bad: ScheduleTask, message: str) -> None:
    with pytest.raises(ConfigError) as caught:
        check_tasks([bad])
    assert message in str(caught.value)


def test_the_check_names_the_position_of_the_bad_task() -> None:
    with pytest.raises(ConfigError, match=r"\[\[schedule.task\]\] #2 run"):
        check_tasks([task("sync"), task("nope")])


# === zamanlayıcı, sahte iş yöneticisiyle ============================================================


def test_nothing_runs_before_the_first_due_time() -> None:
    clock, jobs = Clock(at(2026, 10, 3, 10, 0)), FakeJobs()
    sched = make([task(every="1h", seconds=HOUR)], jobs, clock)
    clock.now += HOUR - 1
    assert sched.tick() == [] and jobs.submitted == []
    state = sched.states()[0]
    assert (state.index, state.run, state.every, state.next_run_at) == (1, "sync", "1h", at(2026, 10, 3, 11, 0))


def test_a_due_task_is_submitted_as_an_ordinary_background_job_of_the_scheduler(
        caplog: pytest.LogCaptureFixture) -> None:
    clock, jobs = Clock(at(2026, 10, 3, 10, 0)), FakeJobs()
    changes: List[int] = []
    sched = make([task("sync", every="1h", seconds=HOUR, league_id=17)], jobs, clock,
                 on_change=lambda: changes.append(1))
    clock.now = at(2026, 10, 3, 11, 0)
    with caplog.at_level(logging.INFO, logger="Scheduler"):
        handled = sched.tick()
    assert [(s.last_result, s.last_job_id) for s in handled] == [(STARTED, "job-1")]
    submitted = jobs.submitted[0]
    assert submitted["kind"] is JobKind.SYNC and submitted["background"] is True
    assert submitted["origin"].face == "scheduler" and submitted["origin"].pid
    assert submitted["spec"] == {"league_id": 17, "selections": [], "follows": [], "only": None, "event_ids": []}  # the fields of the API body (FX-20)
    assert submitted["payload"] == {"league_id": 17, "mode": "full", "selections": None}
    assert submitted["on_change"] is not None and changes == [1]
    state = sched.states()[0]
    assert (state.last_run_at, state.next_run_at) == (at(2026, 10, 3, 11, 0), at(2026, 10, 3, 12, 0))
    assert "Schedule task #1 (sync) started job job-1" in caplog.text


def test_a_task_whose_previous_run_still_runs_is_skipped_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    clock, jobs = Clock(at(2026, 10, 3, 10, 0)), FakeJobs()
    sched = make([task(every="1h", seconds=HOUR)], jobs, clock)
    clock.now += HOUR
    sched.tick()
    clock.now += HOUR
    with caplog.at_level(logging.WARNING, logger="Scheduler"):
        handled = sched.tick()
    assert [s.last_result for s in handled] == [SKIPPED_RUNNING] and len(jobs.submitted) == 1
    assert handled[0].last_job_id == "job-1"  # süren iş hâlâ bilinir: durdurma onu iptal eder
    assert "skipped: its previous run (job job-1) is still running" in caplog.text
    jobs.finish("job-1")
    clock.now += HOUR
    assert [s.last_result for s in sched.tick()] == [STARTED] and len(jobs.submitted) == 2


def test_a_task_is_skipped_while_another_job_holds_the_writer_lease(caplog: pytest.LogCaptureFixture) -> None:
    clock, jobs = Clock(at(2026, 10, 3, 10, 0)), FakeJobs()
    sched = make([task(every="1h", seconds=HOUR)], jobs, clock)
    jobs.raises = JobRunningError()
    clock.now += HOUR
    with caplog.at_level(logging.WARNING, logger="Scheduler"):
        handled = sched.tick()
    assert [(s.last_result, s.last_job_id) for s in handled] == [(SKIPPED_BUSY, None)]
    assert "the data directory is busy (job_running)" in caplog.text
    assert sched.states()[0].next_run_at == at(2026, 10, 3, 12, 0)


def test_a_task_that_cannot_start_is_logged_and_tried_again_at_its_next_time(
        caplog: pytest.LogCaptureFixture) -> None:
    clock, jobs = Clock(at(2026, 10, 3, 10, 0)), FakeJobs()
    sched = make([task(every="1h", seconds=HOUR)], jobs, clock)
    jobs.raises = StoreError("disk full")
    clock.now += HOUR
    with caplog.at_level(logging.ERROR, logger="Scheduler"):
        assert [s.last_result for s in sched.tick()] == [FAILED_TO_START]
    assert "could not start: StoreError" in caplog.text
    jobs.raises = None
    clock.now += HOUR
    assert [s.last_result for s in sched.tick()] == [STARTED]


def test_a_long_pause_gives_one_run_and_not_one_per_missed_time() -> None:
    clock, jobs = Clock(at(2026, 10, 3, 10, 0)), FakeJobs(finish_at_once=True)
    sched = make([task(every="1h", seconds=HOUR)], jobs, clock)
    clock.now = at(2026, 10, 3, 15, 30)
    sched.tick()
    assert sched.tick() == [] and len(jobs.submitted) == 1
    assert sched.states()[0].next_run_at == at(2026, 10, 3, 16, 0)


def test_tasks_due_together_start_in_config_order_and_the_second_finds_the_lease_taken() -> None:
    clock = Clock(at(2026, 10, 3, 10, 0))

    class OneAtATime(FakeJobs):
        def submit(self, kind: Any, spec: Any, fn: Any, **kwargs: Any) -> Any:
            if any(state is JobState.RUNNING for state in self.states.values()):
                raise JobRunningError()
            return super().submit(kind, spec, fn, **kwargs)

    jobs = OneAtATime()
    sched = make([task("sync", cron="0 * * * *"), task("backup", cron="0 * * * *")], jobs, clock)
    clock.now = at(2026, 10, 3, 11, 0)
    assert [(s.index, s.last_result) for s in sched.tick()] == [(1, STARTED), (2, SKIPPED_BUSY)]


def test_the_loop_runs_due_tasks_and_stops_at_once() -> None:
    jobs = FakeJobs(finish_at_once=True)
    sched = Scheduler([task(every="50ms", seconds=0.05)], jobs=lambda: jobs)
    sched.start()
    try:
        assert current() is sched and sched.running
        deadline = time.monotonic() + 10
        while len(jobs.submitted) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(jobs.submitted) >= 2
    finally:
        started = time.monotonic()
        assert sched.stop(timeout=5) == []
    assert time.monotonic() - started < 5 and not sched.running and current() is None


def test_a_round_that_fails_does_not_stop_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = FakeJobs(finish_at_once=True)
    sched = Scheduler([task(every="50ms", seconds=0.05)], jobs=lambda: jobs)
    calls: List[int] = []
    real_tick = sched.tick

    def flaky() -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return real_tick()

    monkeypatch.setattr(sched, "tick", flaky)
    sched.start()
    try:
        deadline = time.monotonic() + 10
        while not jobs.submitted and time.monotonic() < deadline:
            time.sleep(0.01)
        assert jobs.submitted
    finally:
        sched.stop(timeout=5)


def test_stop_cancels_the_running_scheduled_job_and_waits_for_it() -> None:
    clock, jobs = Clock(at(2026, 10, 3, 10, 0)), FakeJobs()
    sched = make([task(every="1h", seconds=HOUR)], jobs, clock)
    clock.now += HOUR
    sched.tick()
    assert sched.stop(timeout=5) == []
    assert jobs.cancelled == ["job-1"]


def test_stop_reports_a_job_that_does_not_end_in_time() -> None:
    clock = Clock(at(2026, 10, 3, 10, 0))

    class Stubborn(FakeJobs):
        def cancel(self, job_id: str) -> bool:
            self.cancelled.append(job_id)
            return True

    jobs = Stubborn()
    sched = make([task(every="1h", seconds=HOUR)], jobs, clock)
    clock.now += HOUR
    sched.tick()
    assert sched.stop(timeout=0.3) == ["job-1"]


def test_a_stopped_scheduler_starts_nothing() -> None:
    clock, jobs = Clock(at(2026, 10, 3, 10, 0)), FakeJobs()
    sched = make([task(every="1h", seconds=HOUR)], jobs, clock)
    sched.stop()
    clock.now += 2 * HOUR
    assert sched.tick() == [] and jobs.submitted == []


def test_the_scheduler_rejects_unknown_tasks_when_it_is_built() -> None:
    with pytest.raises(ConfigError):
        Scheduler([task("nope")], jobs=FakeJobs)


# === görevlerin işleri ===============================================================================


@pytest.mark.parametrize("run, mode, kind", [
    ("sync", "full", JobKind.SYNC), ("fetch", "details", JobKind.FETCH), ("refresh", "refresh", JobKind.REFRESH),
])
def test_the_download_tasks_run_the_sync_service(monkeypatch: pytest.MonkeyPatch, run: str, mode: str,
                                                 kind: JobKind) -> None:
    from src.services import sync as sync_mod

    seen: Dict[str, Any] = {}

    class FakeService:
        def __init__(self, ctx: Any) -> None:
            seen["ctx"] = ctx

        def run(self, spec: Any, handle: Any) -> Any:
            seen["spec"], seen["handle"] = spec, handle
            return SimpleNamespace(state="partial", schedule_empty_seasons=1, progress={"details_done": 3},
                                   refresh=None, failed_listings=(), breaker="403")

    monkeypatch.setattr(sync_mod, "SyncService", FakeService)
    plan = scheduler_mod.TASK_RUNS[run].plan({"league_id": 8}, lambda: "ctx")
    assert plan.kind is kind and "mode" not in plan.spec and "export" not in plan.spec and plan.spec["league_id"] == 8
    assert plan.phases == sync_mod.SyncSpec(mode=mode).job_phases  # type: ignore[arg-type]
    outcome = plan.body("handle")
    assert seen["ctx"] == "ctx" and seen["handle"] == "handle" and seen["spec"].league_id == 8
    assert outcome.state is JobState.PARTIAL and outcome.code == "fetch_stopped_by_breaker"
    assert outcome.result == {"schedule_empty_seasons": 1, "details_done": 3}


def test_a_cancelled_sync_ends_as_cancelled() -> None:
    result = SimpleNamespace(state="cancelled")
    assert scheduler_mod.sync_outcome(result).state is JobState.CANCELLED  # type: ignore[arg-type]


def test_the_backup_task_writes_a_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services import backup as backup_mod

    seen: Dict[str, Any] = {}

    class FakeBackups:
        def __init__(self, store: Any) -> None:
            seen["store"] = store

        def create(self, scope: str, *, config_files: Any, include_secrets: bool) -> Any:
            seen.update(scope=scope, include_secrets=include_secrets, config_files=list(config_files))
            return SimpleNamespace(name="b.tar.gz", scope=scope, with_env=False, size=10, format="v3")

    monkeypatch.setattr(backup_mod, "BackupService", FakeBackups)
    plan = scheduler_mod.TASK_RUNS["backup"].plan({"scope": "data"}, lambda: SimpleNamespace(store="store"))
    assert (plan.kind, plan.spec) == (JobKind.BACKUP, {"scope": "data", "include_env": False})
    outcome = plan.body(None)
    assert (seen["store"], seen["scope"], seen["include_secrets"]) == ("store", "data", False)
    assert any(path.endswith("league_sports.json") for path in seen["config_files"])
    assert outcome.result == {"backup": {"name": "b.tar.gz", "scope": "data", "with_env": False, "bytes": 10,
                                         "format": "v3"}}


# === gerçek iş yöneticisi ve kilit ==================================================================


@pytest.fixture
def job_store(tmp_path: Path) -> Iterator[JobStore]:
    before = conftest.job_threads()
    jobs = JobStore(default_db_path(str(tmp_path / "data")))
    yield jobs
    conftest.join_job_threads(before)
    jobs.close()


@pytest.fixture
def blocking_task(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """`sync` görevinin işi, olay gelene ya da iptal istenene kadar sürer."""
    release = threading.Event()

    def plan(options: Any, context: Any) -> TaskPlan:
        def body(handle: Any) -> JobOutcome:
            while not release.wait(0.01):
                if handle.cancelled():
                    return JobOutcome(state=JobState.CANCELLED)
            return JobOutcome(result={"ok": True})

        return TaskPlan(kind=JobKind.SYNC, spec={"mode": "full"}, body=body)

    monkeypatch.setattr(scheduler_mod, "TASK_RUNS",
                        {"sync": TaskRun(frozenset(), lambda options, where: None, plan)})
    return release


def wait_until(condition: Any, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_a_scheduled_run_is_a_job_of_the_scheduler_under_the_writer_lease(
        job_store: JobStore, blocking_task: threading.Event) -> None:
    manager = JobManager(job_store, cancel_poll=0.05)
    clock = Clock(at(2026, 10, 3, 10, 0))
    sched = Scheduler([task(every="1h", seconds=HOUR)], jobs=lambda: manager, clock=clock)
    clock.now += HOUR
    (state,) = sched.tick()
    job = manager.get(state.last_job_id or "")
    assert job is not None and job.origin.face == "scheduler" and job.kind is JobKind.SYNC
    assert job.state is JobState.RUNNING and manager.active().id == job.id  # type: ignore[union-attr]
    # Kilit onda: komut satırından ya da web'den ikinci bir iş başlamaz
    with pytest.raises(JobRunningError):
        manager.start(JobKind.FETCH, {}, origin=local_origin("api"))
    clock.now += HOUR
    assert [s.last_result for s in sched.tick()] == [SKIPPED_RUNNING]
    blocking_task.set()
    wait_until(lambda: manager.get(job.id).state.terminal)  # type: ignore[union-attr]
    assert manager.get(job.id).state is JobState.SUCCEEDED  # type: ignore[union-attr]


def test_a_scheduled_run_waits_for_nobody_when_another_job_holds_the_lease(
        job_store: JobStore, blocking_task: threading.Event) -> None:
    manager = JobManager(job_store, cancel_poll=0.05)
    other = manager.start(JobKind.FETCH, {}, origin=local_origin("cli"))
    clock = Clock(at(2026, 10, 3, 10, 0))
    sched = Scheduler([task(every="1h", seconds=HOUR)], jobs=lambda: manager, clock=clock)
    clock.now += HOUR
    assert [s.last_result for s in sched.tick()] == [SKIPPED_BUSY]
    manager.run(other.id, lambda handle: None)
    assert [job.origin.face for job in manager.list()] == ["cli"]  # atlanan çalışma iş kaydı bırakmaz
    clock.now += HOUR
    assert [s.last_result for s in sched.tick()] == [STARTED]
    blocking_task.set()


def test_stopping_the_scheduler_cancels_its_running_job(job_store: JobStore, blocking_task: threading.Event) -> None:
    manager = JobManager(job_store, cancel_poll=0.05)
    clock = Clock(at(2026, 10, 3, 10, 0))
    sched = Scheduler([task(every="1h", seconds=HOUR)], jobs=lambda: manager, clock=clock)
    clock.now += HOUR
    (state,) = sched.tick()
    assert sched.stop(timeout=15) == []
    assert manager.get(state.last_job_id or "").state is JobState.CANCELLED  # type: ignore[union-attr]


# === durum ===========================================================================================


def test_the_status_service_reports_the_scheduler_of_this_process() -> None:
    from src.services.status import ScheduleStatus, schedule_status

    assert schedule_status() == ScheduleStatus(enabled=False, next_runs=())
    sched = Scheduler([task(cron="0 3 * * *")], jobs=FakeJobs)
    sched.start()
    try:
        found = schedule_status()
        assert found.enabled is True and [s.cron for s in found.next_runs] == ["0 3 * * *"]
    finally:
        sched.stop()
    assert schedule_status().enabled is False


def test_status_route_shows_the_scheduler_and_its_next_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from src.web.app import app

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    client = TestClient(app)
    status = client.get("/api/v1/status").json()["data"]
    assert status["schedule"] == {"enabled": False, "next_runs": []}
    assert status["capabilities"]["scheduler"] is False

    jobs = FakeJobs()
    clock = Clock(at(2026, 10, 3, 10, 0))
    sched = Scheduler([task("refresh", cron="0 * * * *", league_id=17), task("backup", every="1d", seconds=86400.0)],
                      jobs=lambda: jobs, clock=clock, tz=UTC)
    clock.now = at(2026, 10, 3, 11, 0)
    sched.tick()
    sched.start()
    try:
        status = client.get("/api/v1/status").json()["data"]
    finally:
        sched.stop(timeout=5)
    assert status["capabilities"]["scheduler"] is True
    assert status["schedule"]["enabled"] is True
    assert status["schedule"]["next_runs"] == [
        {"index": 1, "run": "refresh", "every": None, "cron": "0 * * * *", "options": {"league_id": 17},
         "next_run_at_utc": "2026-10-03T12:00:00Z", "last_run_at_utc": "2026-10-03T11:00:00Z",
         "last_job_id": "job-1", "last_result": "started"},
        {"index": 2, "run": "backup", "every": "1d", "cron": None, "options": {},
         "next_run_at_utc": "2026-10-04T10:00:00Z", "last_run_at_utc": None, "last_job_id": None,
         "last_result": None},
    ]


# === ssc serve --scheduler ==========================================================================


class FakeScheduler:
    def __init__(self, tasks: Any) -> None:
        self.tasks = tuple(tasks)
        self.started = self.stopped = False

    def states(self) -> Any:
        return self.tasks

    def start(self) -> None:
        self.started = True

    def stop(self) -> List[str]:
        self.stopped = True
        return []


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> List[FakeScheduler]:
    from src.cli.commands import serve as serve_command

    made: List[FakeScheduler] = []

    def build(tasks: Any) -> FakeScheduler:
        check_tasks(tasks)
        made.append(FakeScheduler(tasks))
        return made[-1]

    monkeypatch.setattr(serve_command, "build_scheduler", build)
    return made


def config_with(tmp_path: Path, schedule: str) -> str:
    return skeleton.write_config(tmp_path / "sofascore.toml", "schema = 1\n" + schedule)


def scheduler_warnings(run: Any) -> List[str]:
    """Zamanlayıcının uyarıları (yapılandırma dosyası eski ad uyarıları da getirir; onlar sayılmaz)."""
    return [w["code"] for w in run.json["warnings"] if w["code"].startswith("scheduler")]


TASKS = '[[schedule.task]]\nrun = "sync"\nevery = "6h"\n[[schedule.task]]\nrun = "backup"\ncron = "0 3 * * *"\n'


def test_the_scheduler_is_off_by_default_also_with_tasks(cli: CliRunner, server: FakeServer,  # noqa: F811
                                                         built: List[FakeScheduler], tmp_path: Path) -> None:
    run = cli("serve", "--config", config_with(tmp_path, TASKS), "--json")
    assert run.exit_code == 0, run.stderr
    assert built == [] and "scheduler_tasks" not in run.data and scheduler_warnings(run) == []


def test_serve_hosts_the_scheduler_while_the_server_runs(cli: CliRunner, server: FakeServer,  # noqa: F811
                                                         built: List[FakeScheduler], tmp_path: Path) -> None:
    server.during = lambda: seen.append((built[0].started, built[0].stopped))
    seen: List[Any] = []
    run = cli("serve", "--config", config_with(tmp_path, TASKS), "--scheduler")
    assert run.exit_code == 0, run.stderr
    assert seen == [(True, False)] and built[0].stopped
    assert [t.run for t in built[0].tasks] == ["sync", "backup"]
    assert "Scheduler on: 2 task(s)" in run.stderr


def test_the_setting_turns_the_scheduler_on_and_the_flag_off(cli: CliRunner, server: FakeServer,  # noqa: F811
                                                             built: List[FakeScheduler], tmp_path: Path) -> None:
    config = config_with(tmp_path, "[schedule]\nenabled = true\n" + TASKS)
    run = cli("serve", "--config", config, "--json")
    assert run.exit_code == 0, run.stderr
    assert run.data["scheduler_tasks"] == 2 and len(built) == 1
    run = cli("serve", "--config", config, "--no-scheduler", "--json")
    assert run.exit_code == 0 and len(built) == 1 and "scheduler_tasks" not in run.data


def test_a_scheduler_without_tasks_does_not_start(cli: CliRunner, server: FakeServer,  # noqa: F811
                                                  built: List[FakeScheduler]) -> None:
    run = cli("serve", "--scheduler", "--json")
    assert run.exit_code == 0, run.stderr
    assert built == [] and scheduler_warnings(run) == ["scheduler_no_tasks"]
    assert server.calls  # sunucu yine başlar


def test_an_unknown_task_stops_the_start_before_the_server(cli: CliRunner, server: FakeServer,  # noqa: F811
                                                           built: List[FakeScheduler], tmp_path: Path) -> None:
    config = config_with(tmp_path, '[[schedule.task]]\nrun = "download"\nevery = "1h"\n')
    run = cli("serve", "--config", config, "--scheduler", "--json")
    assert (run.exit_code, run.error["code"]) == (2, "config_invalid")
    assert "'download' is not a task the scheduler knows" in run.error["message"]
    assert server.calls == []


def test_the_scheduler_does_not_run_with_dev(cli: CliRunner, server: FakeServer,  # noqa: F811
                                             built: List[FakeScheduler], tmp_path: Path) -> None:
    run = cli("serve", "--config", config_with(tmp_path, TASKS), "--scheduler", "--dev", "--json")
    assert (run.exit_code, run.error["code"]) == (2, "invalid_request")
    assert server.calls == []
    config = config_with(tmp_path, "[schedule]\nenabled = true\n" + TASKS)
    run = cli("serve", "--config", config, "--dev", "--json")
    assert run.exit_code == 0 and built == []
    assert scheduler_warnings(run) == ["scheduler_not_in_dev"]


def test_the_real_scheduler_is_built_on_the_web_job_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.cli.commands import serve as serve_command
    from src.web import deps

    sched = serve_command.build_scheduler((task(),))
    assert isinstance(sched, Scheduler) and sched._jobs is deps.job_manager
    assert sched._on_change is deps.refresh_job_mirror and not sched.running


def test_the_two_scheduler_flags_exclude_each_other(cli: CliRunner, server: FakeServer) -> None:  # noqa: F811
    run = cli("serve", "--scheduler", "--no-scheduler", "--json")
    assert run.exit_code == 2 and server.calls == []


# === `every` iş geçmişinden; geçmişin budanması (plan maddesi FX-15) ====================================


def _history_job(manager: JobManager, spec: Dict[str, Any], *, kind: JobKind = JobKind.SYNC,
                 face: str = "scheduler") -> Any:
    """Geçmişte bitmiş bir iş: verilen kaynaktan, verilen türde ve belirtimle."""
    job = manager.start(kind, spec, origin=local_origin(face))  # type: ignore[arg-type]
    manager.run(job.id, lambda handle: JobOutcome(result={}))
    return manager.get(job.id)


def _started(job: Any) -> float:
    return datetime.fromisoformat(job.started_at.replace("Z", "+00:00")).timestamp()


def test_an_every_task_counts_from_its_last_run_in_the_job_history(job_store: JobStore) -> None:
    """Sunucu yeniden başladığında sayaç baştan başlamaz: son çalışma iş geçmişindedir (sahibin kararı)."""
    manager = JobManager(job_store, cancel_poll=0.05)
    sync_spec = dict(scheduler_mod.TASK_RUNS["sync"].plan({}, lambda: None).spec)
    _history_job(manager, sync_spec, face="cli")  # elle başlatılan aynı iş: sayılmaz
    last = _history_job(manager, sync_spec)
    _history_job(manager, {**sync_spec, "league_id": 17})  # başka bir görevin işi
    started = _started(last)
    clock = Clock(started + 0.5 * HOUR)  # bir aralık dolmadan yeniden başlayan sunucu
    sched = Scheduler([task(every="1h", seconds=HOUR)], jobs=lambda: manager, clock=clock)

    assert sched.tick() == []
    (state,) = sched.states()
    assert (state.next_run_at, state.last_run_at, state.last_job_id) == (started + HOUR, started, last.id)


def test_an_every_task_whose_interval_passed_while_the_server_was_off_runs_once_at_start() -> None:
    jobs = FakeJobs(finish_at_once=True)
    # a record of before FX-20 (the service's spec, `mode`) still counts as the task's last run
    spec = {"mode": "refresh", "league_id": None, "selections": []}
    stamp = "2026-10-03T08:00:00+00:00"
    old = SimpleNamespace(id="old", kind=JobKind.REFRESH, spec=spec, origin=SimpleNamespace(face="scheduler"),
                          started_at=stamp, created_at=stamp)
    jobs.list = lambda limit: [old]  # type: ignore[attr-defined]
    clock = Clock(at(2026, 10, 3, 12, 0))  # dört saat sonra; aralık bir saat
    sched = make([task("refresh", every="1h", seconds=HOUR)], jobs, clock)

    (state,) = sched.tick()
    assert state.last_result == STARTED and len(jobs.submitted) == 1  # kaçanlar biriktirilmez: tek çalışma
    assert state.next_run_at == at(2026, 10, 3, 13, 0)
    clock.now += 60
    assert sched.tick() == []


def test_without_a_readable_history_an_every_task_counts_from_the_start(caplog: pytest.LogCaptureFixture) -> None:
    jobs = FakeJobs()

    def broken(limit: int) -> Any:
        raise StoreError("no state.db")

    jobs.list = broken  # type: ignore[attr-defined]
    clock = Clock(at(2026, 10, 3, 10, 0))
    sched = make([task(every="1h", seconds=HOUR), task(cron="0 3 * * *")], jobs, clock)
    with caplog.at_level(logging.WARNING, logger="Scheduler"):
        assert sched.tick() == []
    assert sched.states()[0].next_run_at == clock.now + HOUR
    assert any("job history could not be read" in r.getMessage() for r in caplog.records)


def test_a_cron_task_ignores_the_job_history() -> None:
    jobs = FakeJobs()
    jobs.list = lambda limit: pytest.fail("cron tasks do not read the history")  # type: ignore[attr-defined]
    clock = Clock(at(2026, 10, 3, 10, 0))
    sched = make([task(cron="0 3 * * *")], jobs, clock)
    assert sched.tick() == []


def test_the_prune_task_is_a_clear_job_of_the_history() -> None:
    calls: List[float] = []
    store = SimpleNamespace(history=SimpleNamespace(prune=lambda *, older_than: calls.append(older_than) or 7))
    plan = scheduler_mod.TASK_RUNS["prune-history"].plan({"older_than": "90d"}, lambda: SimpleNamespace(store=store))
    assert (plan.kind, dict(plan.spec)) == (JobKind.CLEAR, {"scope": "history", "older_than": "90d"})
    before = time.time()
    outcome = plan.body(SimpleNamespace())
    (cutoff,) = calls
    assert before - 90 * 86400 - 1 <= cutoff <= time.time() - 90 * 86400
    result = outcome.result["prune_history"]  # type: ignore[index]
    assert (result["older_than"], result["removed"]) == ("90d", 7) and result["cutoff_utc"].endswith("+00:00")


def test_a_scheduled_prune_runs_under_the_writer_lease_and_removes_old_snapshots(tmp_path: Path) -> None:
    from src.slices import SLICE_OK, Outcome
    from src.store import Ref, open_store

    import store_fixtures as sf

    store = open_store(tmp_path / "data")
    event_id = sf.event_id(sf.PL_ARS)
    old = datetime(2026, 1, 1, tzinfo=UTC)
    for minute, price in enumerate(("1.5", "2.0", "2.5")):
        moment = old + timedelta(minutes=minute)
        outcomes: Dict[Any, Outcome] = {("odds_all", "1"): Outcome(SLICE_OK, {"markets": [{"price": price}]},
                                                                    fetched_at=moment)}
        if minute == 0:
            outcomes["event"] = Outcome(SLICE_OK, sf.basic_payload(sf.PL_ARS), fetched_at=moment)
        store.events.put(event_id, outcomes, keep_history=("odds_all",))
    assert len(store.history.index(Ref.event(event_id), "odds_all", "1")) == 3
    before = conftest.job_threads()
    manager = JobManager(JobStore.for_store(store), cancel_poll=0.05)
    clock = Clock(time.time())
    sched = Scheduler([task("prune-history", every="1d", seconds=86400.0, older_than="30d")], jobs=lambda: manager,
                      clock=clock, context=lambda: SimpleNamespace(store=store))
    clock.now += 86400
    try:
        (state,) = sched.tick()
        assert state.last_result == STARTED
        job_id = state.last_job_id or ""
        wait_until(lambda: manager.get(job_id).state.terminal)  # type: ignore[union-attr]
    finally:
        conftest.join_job_threads(before)
    job = manager.get(job_id)
    assert job is not None and job.state is JobState.SUCCEEDED and job.kind is JobKind.CLEAR
    assert job.result["prune_history"]["removed"] == 2  # type: ignore[index]
    assert len(store.history.index(Ref.event(event_id), "odds_all", "1")) == 1  # en yenisi kalır
    assert store.lease_holder("writer") is None
