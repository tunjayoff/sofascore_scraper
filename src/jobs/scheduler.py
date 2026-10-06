"""
Uygulama içi zamanlayıcı (docs/design/02-services.md 2.8 ve 4.1; docs/design/00-platform.md bölüm 1; plan maddesi
P29). İsteğe bağlıdır ve varsayılan olarak kapalıdır: yalnızca `ssc serve` barındırır (`--scheduler` ya da
`[schedule] enabled = true`). Görevler yapılandırma dosyasındandır:

    [schedule]
    enabled = true
    [[schedule.task]]
    run = "sync"
    every = "6h"                 # ya da cron = "15 */6 * * *"
    league_id = 17               # isteğe bağlı: görevin seçenekleri

Görevler (`run`) ve seçenekleri (`TASK_RUNS`):

    sync      sezon listeleri, programlar ve detaylar (web'in "sync" işi)       league_id
    fetch     yalnızca detaylar, kayıtlı programlar için                         league_id
    refresh   değişebilecek kayıtlı maçların yeniden okunması                    league_id
    backup    `backups/` altına yedek                                            scope, include_env

Bilinmeyen bir `run` adı ya da seçenek, sunucu başlamadan reddedilir (`check_tasks`: ConfigError, çıkış kodu 2).

Kurallar:

  * Zamanlanmış bir çalışma sıradan bir iştir (P11): iş yöneticisiyle (`JobManager.submit`, arka planda)
    `origin=scheduler` olarak başlatılır, `writer` kilidine uyar ve iş listesinde, `job.*` olaylarında görünür.
  * Birleştirme (coalescing): görevin bir önceki çalışması hâlâ sürüyorsa yeni çalışma başlatılmaz, atlanır ve
    loglanır. Kilit başka bir işte ya da süreçteyse (CLI'den bir indirme, bir yedek) de atlanır. Kaçan
    çalışmalar biriktirilmez: sunucu kapalıyken ya da makine uykudayken geçen anlar için tek çalışma yapılır,
    sonraki an şimdiden sonraki ilk andır.
  * `every`: ilk çalışma zamanlayıcının başlamasından bir aralık sonradır (sunucu başlarken indirme yapılmaz);
    sonrakiler önceki anın üstüne aralık eklenerek. Sunucu yeniden başlarsa sayaç baştan başlar.
  * `cron`: beş alan (dakika saat gün ay haftanın-günü), makinenin yerel saatiyle; `*`, `*/n`, `a-b`, `a-b/n`,
    `a/n`, virgülle listeler, ay ve gün adları (jan, mon). Gün ve haftanın günü ikisi de kısıtlıysa biri
    tutması yeter (cron'un bilinen kuralı). 0 ve 7 pazardır.
  * `queued` durumu yazılmaz: bir çalışma ya başlar ya atlanır (iş deposu satırı kilit alındıktan sonra yazar).
  * Durdurma (`stop`): döngü durur; zamanlayıcının başlattığı ve hâlâ süren iş iptal edilir ve en çok
    `timeout` saniye beklenir.
  * Sonraki çalışmalar `/api/v1/status`'ta görünür (`schedule`): süreçte çalışan zamanlayıcı `current()`'tir.

Saat (`clock`, epoch saniye) ve iş yöneticisi dışarıdan verilir: testler sahte saatle `tick()`'i çağırır.

Bu modül yüz modüllerini (src.web, src.cli, ...) ve sqlite3'ü içe aktarmaz (tests/test_jobs_model.py); işlerin
gövdeleri servisleri çalıştırır ve onları ilk kullanımda içe aktarır.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, tzinfo
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from src.errors import to_platform_error
from src.exceptions import ConfigError
from src.jobs.manager import JobBody, JobManager, JobOutcome, local_origin
from src.jobs.model import Job, JobKind, JobState

if TYPE_CHECKING:
    from src.config import ScheduleTask
    from src.services.context import ServiceContext
    from src.services.sync import SyncResult

logger = logging.getLogger("Scheduler")

MAX_WAIT_SECONDS = 60.0  # döngü en geç bu aralıkla uyanır: saat ileri atlarsa (uyku, yaz saati) gecikme sınırlı
STOP_TIMEOUT_SECONDS = 30.0  # durdurmada zamanlayıcının başlattığı işin bitmesi en çok bu kadar beklenir
CRON_SEARCH_DAYS = 366 * 5  # cron ifadesinin sonraki anı bu kadar gün içinde aranır (29 şubat dahil)

# Son çalışmanın sonucu (`TaskState.last_result`)
STARTED = "started"
SKIPPED_RUNNING = "skipped_running"  # görevin önceki çalışması sürüyor
SKIPPED_BUSY = "skipped_busy"        # kilit başka bir işte ya da süreçte
FAILED_TO_START = "failed_to_start"  # iş başlatılamadı (depo hatası ve benzeri)

_BUSY_CODES = frozenset({"job_running", "data_operation_running", "instance_running"})


# --- tetikleyiciler -------------------------------------------------------------------------------


@dataclass(frozen=True)
class EveryTrigger:
    """Sabit aralık (saniye)."""

    seconds: float

    def first(self, now: float) -> float:
        return now + self.seconds

    def next_after(self, due: float, now: float) -> float:
        """`due` anından sonraki, `now`dan sonra gelen ilk an (kaçanlar biriktirilmez)."""
        upcoming = due + self.seconds
        if upcoming <= now:
            missed = int((now - due) // self.seconds)
            upcoming = due + (missed + 1) * self.seconds
            if upcoming <= now:  # kayan nokta payı
                upcoming = now + self.seconds
        return upcoming


_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_DAYS = {name: number for number, name in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}
_ITEM = re.compile(r"^(\*|[a-z0-9]+(?:-[a-z0-9]+)?)(?:/(\d+))?$")


def _cron_value(text: str, low: int, high: int, names: Mapping[str, int]) -> int:
    value = names.get(text) if text in names else (int(text) if text.isdigit() else None)
    if value is None or not low <= value <= high:
        raise ValueError(f"{text!r} is not a value from {low} to {high}")
    return value


def _cron_field(text: str, low: int, high: int, names: Mapping[str, int] = MappingProxyType({})) -> FrozenSet[int]:
    """Bir cron alanı → izin verilen değerler."""
    values: set = set()
    for item in text.lower().split(","):
        match = _ITEM.match(item)
        if not match:
            raise ValueError(f"{item!r} is not a cron field item")
        span, step_text = match.group(1), match.group(2)
        step = int(step_text) if step_text is not None else 1
        if step < 1:
            raise ValueError(f"the step of {item!r} must be at least 1")
        if span == "*":
            start, end = low, high
        elif "-" in span:
            first, last = span.split("-", 1)
            start, end = _cron_value(first, low, high, names), _cron_value(last, low, high, names)
            if start > end:
                raise ValueError(f"the range {span!r} runs backwards")
        else:
            start = _cron_value(span, low, high, names)
            end = high if step_text is not None else start  # "5/15": 5'ten sona 15'er
        values.update(range(start, end + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class CronTrigger:
    """
    Beş alanlı cron ifadesi. tz None: makinenin yerel saati; testler sabit bir saat dilimi verir.
    Geçersiz ifade ValueError'dır.
    """

    expression: str
    tz: Optional[tzinfo] = None
    minutes: FrozenSet[int] = field(init=False)
    hours: FrozenSet[int] = field(init=False)
    days: FrozenSet[int] = field(init=False)
    months: FrozenSet[int] = field(init=False)
    weekdays: FrozenSet[int] = field(init=False)  # 0 pazar ... 6 cumartesi
    day_restricted: bool = field(init=False)
    weekday_restricted: bool = field(init=False)

    def __post_init__(self) -> None:
        parts = self.expression.split()
        if len(parts) != 5:
            raise ValueError(f"expected five fields, got {len(parts)}")
        weekdays = _cron_field(parts[4], 0, 7, _DAYS)
        object.__setattr__(self, "minutes", _cron_field(parts[0], 0, 59))
        object.__setattr__(self, "hours", _cron_field(parts[1], 0, 23))
        object.__setattr__(self, "days", _cron_field(parts[2], 1, 31))
        object.__setattr__(self, "months", _cron_field(parts[3], 1, 12, _MONTHS))
        object.__setattr__(self, "weekdays", frozenset(0 if d == 7 else d for d in weekdays))
        object.__setattr__(self, "day_restricted", not parts[2].startswith("*"))
        object.__setattr__(self, "weekday_restricted", not parts[4].startswith("*"))

    def _day_matches(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        in_days = day.day in self.days
        in_weekdays = (day.isoweekday() % 7) in self.weekdays
        if self.day_restricted and self.weekday_restricted:
            return in_days or in_weekdays
        return in_days and in_weekdays

    def _timestamp(self, day: date, hour: int, minute: int) -> float:
        moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=self.tz)
        return moment.timestamp()  # tz None: yerel saat (mktime)

    def first(self, now: float) -> float:
        return self.next_after(now, now)

    def next_after(self, due: float, now: float) -> float:
        """`now`dan sonraki ilk eşleşen dakika (kaçan anlar biriktirilmez)."""
        start = datetime.fromtimestamp(max(due, now), tz=self.tz).replace(second=0, microsecond=0)
        start += timedelta(minutes=1)
        hours, minutes = sorted(self.hours), sorted(self.minutes)
        day = start.date()
        for _ in range(CRON_SEARCH_DAYS):
            if self._day_matches(day):
                for hour in hours:
                    if day == start.date() and hour < start.hour:
                        continue
                    for minute in minutes:
                        if day == start.date() and hour == start.hour and minute < start.minute:
                            continue
                        moment = self._timestamp(day, hour, minute)
                        if moment > now:  # yaz saati geçişinde var olmayan saat ileri kayar
                            return moment
            day += timedelta(days=1)
        raise ValueError(f"the cron expression {self.expression!r} matches no time in {CRON_SEARCH_DAYS} days")


Trigger = Any  # EveryTrigger | CronTrigger (Python 3.10'da Union yazımı yerine)


# --- görevler -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskPlan:
    """Bir görev çalışmasının işi: türü, belirtimi, gövdesi ve iş yöneticisine verilecekler."""

    kind: JobKind
    spec: Mapping[str, Any]
    body: JobBody
    phases: Tuple[str, ...] = ()
    payload: Optional[Mapping[str, Any]] = None


ContextFactory = Callable[[], "ServiceContext"]


def _default_context() -> "ServiceContext":
    from src.config_manager import ConfigManager
    from src.services.context import build_context

    return build_context(ConfigManager())


def sync_outcome(result: "SyncResult") -> JobOutcome:
    """Eşitleme servisinin sonucu → işin bitişi (komut satırının ve API'nin kuralıyla aynı)."""
    import dataclasses

    if result.state == "cancelled":
        return JobOutcome(state=JobState.CANCELLED)
    summary: Dict[str, Any] = {"schedule_empty_seasons": result.schedule_empty_seasons, **result.progress}
    if result.refresh is not None:
        summary["refresh"] = dataclasses.asdict(result.refresh)
    if result.failed_listings:
        summary["failed_listings"] = [dataclasses.asdict(item) for item in result.failed_listings]
    if result.breaker:
        code, params = "fetch_stopped_by_breaker", {"reason": result.breaker}
        if result.refresh is not None:
            code, params = "refresh_stopped_by_breaker", {"reason": result.breaker, "skipped": result.refresh.skipped}
        return JobOutcome(state=JobState(result.state), result=summary, code=code, params=params,
                          message=f"Stopped by the circuit breaker ({result.breaker})")
    return JobOutcome(state=JobState(result.state), result=summary)


def _league_id(options: Mapping[str, Any], where: str) -> Optional[int]:
    value = options.get("league_id")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{where} league_id: expected a positive whole number, got {value!r}")
    return value


def _sync_plan(mode: str) -> Callable[[Mapping[str, Any], ContextFactory], TaskPlan]:
    def plan(options: Mapping[str, Any], context: ContextFactory) -> TaskPlan:
        import dataclasses

        from src.services.sync import SyncService, SyncSpec

        spec = SyncSpec(mode=mode, league_id=_league_id(options, "schedule task"))  # type: ignore[arg-type]

        def body(handle: Any) -> JobOutcome:
            ctx = context()
            return sync_outcome(SyncService(ctx).run(spec, handle=handle))

        kind = {"full": JobKind.SYNC, "details": JobKind.FETCH, "refresh": JobKind.REFRESH}[mode]
        return TaskPlan(kind=kind, spec=dataclasses.asdict(spec), body=body, phases=spec.job_phases,
                        payload={"league_id": spec.league_id, "mode": spec.mode, "selections": None})

    return plan


BACKUP_SCOPES: Tuple[str, ...] = ("all", "state", "data", "config", "seasons", "matches", "match_details")


def _backup_config_files() -> List[str]:
    """Yedeğe giren ayar dosyaları (`ssc backup create` ile aynı): lig listesi, spor eşlemesi, yapılandırma."""
    import os

    from src.config import loader
    from src.paths import config_dir, default_league_config_path

    found = [default_league_config_path(), os.path.join(config_dir(), "league_sports.json")]
    config_file = loader.active().config_file
    if config_file:
        found.append(config_file)
    return found


def _backup_plan(options: Mapping[str, Any], context: ContextFactory) -> TaskPlan:
    scope = str(options.get("scope", "all"))
    include_env = bool(options.get("include_env", False))
    spec = {"scope": scope, "include_env": include_env}

    def body(handle: Any) -> JobOutcome:
        from src.services.backup import BackupService

        info = BackupService(context().store).create(
            scope, config_files=_backup_config_files(), include_secrets=include_env)  # type: ignore[arg-type]
        return JobOutcome(result={"backup": {"name": info.name, "scope": info.scope, "with_env": info.with_env,
                                             "bytes": info.size, "format": info.format}})

    return TaskPlan(kind=JobKind.BACKUP, spec=spec, body=body)


def _check_league(options: Mapping[str, Any], where: str) -> None:
    _league_id(options, where)


def _check_backup(options: Mapping[str, Any], where: str) -> None:
    scope = options.get("scope", "all")
    if scope not in BACKUP_SCOPES:
        raise ConfigError(f"{where} scope: expected one of {', '.join(BACKUP_SCOPES)}, got {scope!r}")
    if not isinstance(options.get("include_env", False), bool):
        raise ConfigError(f"{where} include_env: expected true or false, got {options.get('include_env')!r}")


@dataclass(frozen=True)
class TaskRun:
    """Bir `run` adının seçenekleri, denetimi ve işi."""

    options: FrozenSet[str]
    check: Callable[[Mapping[str, Any], str], None]
    plan: Callable[[Mapping[str, Any], ContextFactory], TaskPlan]


TASK_RUNS: Mapping[str, TaskRun] = MappingProxyType({
    "sync": TaskRun(frozenset({"league_id"}), _check_league, _sync_plan("full")),
    "fetch": TaskRun(frozenset({"league_id"}), _check_league, _sync_plan("details")),
    "refresh": TaskRun(frozenset({"league_id"}), _check_league, _sync_plan("refresh")),
    "backup": TaskRun(frozenset({"scope", "include_env"}), _check_backup, _backup_plan),
})


def trigger_of(task: "ScheduleTask", tz: Optional[tzinfo] = None) -> Trigger:
    """Görevin tetikleyicisi; geçersizse ValueError."""
    if task.cron is not None:
        return CronTrigger(task.cron, tz)
    if task.every_seconds is None or task.every_seconds <= 0:
        raise ValueError("every must be a positive duration")
    return EveryTrigger(float(task.every_seconds))


def check_tasks(tasks: Sequence["ScheduleTask"], tz: Optional[tzinfo] = None) -> None:
    """
    Görevlerin `run` adlarını, seçeneklerini ve cron ifadelerini denetler; ilk hatada ConfigError
    (`[[schedule.task]] #<sıra>` ile). Yükleyici biçimi denetler; bu, zamanlayıcının bildiklerini.
    """
    known = ", ".join(TASK_RUNS)
    for index, task in enumerate(tasks, start=1):
        where = f"[[schedule.task]] #{index}"
        run = TASK_RUNS.get(task.run)
        if run is None:
            raise ConfigError(f"{where} run: {task.run!r} is not a task the scheduler knows ({known})")
        unknown = sorted(set(task.options) - run.options)
        if unknown:
            allowed = ", ".join(sorted(run.options)) or "none"
            raise ConfigError(f"{where}: unknown option(s) for run = {task.run!r}: {', '.join(unknown)} "
                              f"(allowed: {allowed})")
        run.check(task.options, where)
        try:
            trigger_of(task, tz)
        except ValueError as e:
            raise ConfigError(f"{where} {'cron' if task.cron is not None else 'every'}: {e}") from None


# --- zamanlayıcı ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskState:
    """Bir görevin durumu (`/api/v1/status` `schedule.next_runs`). Zamanlar epoch saniyedir."""

    index: int  # yapılandırmadaki sırası, 1'den
    run: str
    every: Optional[str]
    cron: Optional[str]
    options: Mapping[str, Any]
    next_run_at: float
    last_run_at: Optional[float] = None
    last_job_id: Optional[str] = None
    last_result: Optional[str] = None  # started, skipped_running, skipped_busy, failed_to_start


_current: Optional["Scheduler"] = None
_current_lock = threading.Lock()


def current() -> Optional["Scheduler"]:
    """Bu süreçte çalışan zamanlayıcı (`start` ile `stop` arası) ya da None."""
    with _current_lock:
        return _current


class Scheduler:
    """
    Görevleri zamanı gelince iş olarak başlatır. `jobs`: işleri başlatan iş yöneticisini veren işlev (web
    sürecininki: aynı iş deposu, aynı `writer` kilidi). `context`: işlerin servis bağlamını kurar (işin
    thread'inde). `on_change`: iş yöneticisine verilir (web: eski arayüzün iş yansısı).
    """

    def __init__(self, tasks: Sequence["ScheduleTask"], *, jobs: Callable[[], JobManager],
                 context: ContextFactory = _default_context, clock: Callable[[], float] = time.time,
                 tz: Optional[tzinfo] = None, on_change: Optional[Callable[[], Any]] = None) -> None:
        check_tasks(tasks, tz)
        self._tasks = tuple(tasks)
        self._triggers = tuple(trigger_of(task, tz) for task in self._tasks)
        self._jobs = jobs
        self._context = context
        self._clock = clock
        self._on_change = on_change
        self._lock = threading.Lock()
        now = clock()
        self._states: List[TaskState] = [
            TaskState(index=i, run=task.run, every=task.every, cron=task.cron, options=dict(task.options),
                      next_run_at=trigger.first(now))
            for i, (task, trigger) in enumerate(zip(self._tasks, self._triggers, strict=True), start=1)
        ]
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # --- durum ---------------------------------------------------------------------------------------

    def states(self) -> Tuple[TaskState, ...]:
        """Görevlerin durumu, yapılandırma sırasıyla."""
        with self._lock:
            return tuple(self._states)

    def next_due(self) -> Optional[float]:
        with self._lock:
            return min((s.next_run_at for s in self._states), default=None)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --- yürütme -------------------------------------------------------------------------------------

    def tick(self) -> List[TaskState]:
        """
        Zamanı gelen görevleri çalıştırır (ya da atlar) ve sonraki anlarını hesaplar; bu turda ele alınan
        görevlerin yeni durumlarını döndürür. Aynı anda gelen görevler yapılandırma sırasıyla başlatılır: ilki
        kilidi alırsa sonrakiler `skipped_busy` olur.
        """
        now = self._clock()
        handled: List[TaskState] = []
        for position, trigger in enumerate(self._triggers):
            with self._lock:
                state = self._states[position]
            if state.next_run_at > now or self._stop.is_set():
                continue
            result, job_id = self._fire(position, state)
            updated = replace(
                state, next_run_at=trigger.next_after(state.next_run_at, now), last_run_at=now,
                last_result=result, last_job_id=job_id if job_id is not None else state.last_job_id,
            )
            with self._lock:
                self._states[position] = updated
            handled.append(updated)
            logger.info("Schedule task #%d (%s): next run at %s", updated.index, updated.run,
                        _iso(updated.next_run_at))
        return handled

    def _previous_running(self, manager: JobManager, job_id: Optional[str]) -> bool:
        if job_id is None:
            return False
        job = manager.get(job_id)
        return job is not None and not job.state.terminal

    def _fire(self, position: int, state: TaskState) -> Tuple[str, Optional[str]]:
        """Görevin bir çalışması: (sonuç, başlatılan işin kimliği)."""
        task = self._tasks[position]
        try:
            manager = self._jobs()
            if self._previous_running(manager, state.last_job_id):
                logger.warning("Schedule task #%d (%s) skipped: its previous run (job %s) is still running",
                               state.index, task.run, state.last_job_id)
                return SKIPPED_RUNNING, None
            plan = TASK_RUNS[task.run].plan(task.options, self._context)
            job: Job = manager.submit(
                plan.kind, plan.spec, plan.body, origin=local_origin("scheduler"), background=True,
                phases=plan.phases, payload=plan.payload, on_change=self._on_change,
            )
        except Exception as e:
            code = to_platform_error(e).code
            if code in _BUSY_CODES:
                logger.warning("Schedule task #%d (%s) skipped: the data directory is busy (%s)",
                               state.index, task.run, code)
                return SKIPPED_BUSY, None
            logger.error("Schedule task #%d (%s) could not start: %s: %s", state.index, task.run,
                         type(e).__name__, code)
            return FAILED_TO_START, None
        logger.info("Schedule task #%d (%s) started job %s", state.index, task.run, job.id)
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception as e:  # yansı tazelenemedi: iş yine de çalışır
                logger.debug("Job mirror refresh failed: %s", e)
        return STARTED, job.id

    def run(self) -> None:
        """Döngü: `stop()` gelene kadar zamanı gelen görevleri çalıştırır (`start` bunu kendi thread'inde çağırır)."""
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # bir tur hatası zamanlayıcıyı durdurmaz
                logger.error("Scheduler round failed: %s: %s", type(e).__name__, e)
            due = self.next_due()
            wait = MAX_WAIT_SECONDS if due is None else min(max(due - self._clock(), 0.0), MAX_WAIT_SECONDS)
            if self._stop.wait(max(wait, 0.01)):
                break

    def start(self) -> None:
        """Döngüyü kendi thread'inde başlatır ve süreçteki zamanlayıcı olarak kaydeder (`current`)."""
        global _current
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self.run, name="scheduler", daemon=True)
        with _current_lock:
            _current = self
        self._thread.start()
        for state in self.states():
            logger.info("Schedule task #%d (%s, %s): first run at %s", state.index, state.run,
                        f"every {state.every}" if state.every else f"cron {state.cron}", _iso(state.next_run_at))

    def stop(self, timeout: float = STOP_TIMEOUT_SECONDS) -> List[str]:
        """
        Döngüyü durdurur, zamanlayıcının başlattığı ve hâlâ süren işlerin iptalini ister ve onları en çok
        `timeout` saniye bekler. Hâlâ süren işlerin kimliklerini döndürür (boş: hepsi bitti).
        """
        global _current
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(MAX_WAIT_SECONDS)
        with _current_lock:
            if _current is self:
                _current = None
        job_ids = [s.last_job_id for s in self.states() if s.last_job_id is not None]
        if not job_ids:
            return []
        try:
            manager = self._jobs()
            running = [job_id for job_id in job_ids if self._previous_running(manager, job_id)]
            for job_id in running:
                logger.info("Cancelling scheduled job %s", job_id)
                manager.cancel(job_id)
            deadline = time.monotonic() + max(0.0, timeout)
            while running and time.monotonic() < deadline:
                time.sleep(0.2)
                running = [job_id for job_id in running if self._previous_running(manager, job_id)]
        except Exception as e:
            logger.warning("Scheduled jobs could not be stopped cleanly: %s: %s", type(e).__name__, e)
            return job_ids
        if running:
            logger.warning("Scheduled job(s) still running after %.0f s: %s", timeout, ", ".join(running))
        return running


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


__all__ = [
    "BACKUP_SCOPES",
    "FAILED_TO_START",
    "SKIPPED_BUSY",
    "SKIPPED_RUNNING",
    "STARTED",
    "TASK_RUNS",
    "CronTrigger",
    "EveryTrigger",
    "Scheduler",
    "TaskPlan",
    "TaskState",
    "check_tasks",
    "current",
    "sync_outcome",
    "trigger_of",
]
