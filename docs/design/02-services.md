# 02 — Service layer and the three faces

Status: design, reconciled with `01-storage.md` on 2026-10-01. Baseline: `origin/main` at `3ae2599`.
Every `file:line` reference below is to that commit. Companion documents: `00-platform.md` (the owner's
platform design), `01-storage.md` (Store and catalog) and `03-implementation-plan.md` (the one ordered PR
list). Section 11 lists what changed in this document during reconciliation and why.

Conventions used here: "event" is a SofaScore match; "slice" is one data type of an owner entity
(event, season, team, player); "face" is one of Python library, CLI, HTTP API.
The CLI executable is written `ssc` below (final name: decision D2).

---

## 1. Current state: responsibility map

### 1.1 File map

| File (lines) | What it does today | Target home |
|---|---|---|
| `main.py` (402) | argparse with 20 flags (`main.py:52-202`), mode dispatch (`:244-374`), exit-code policy (`:327`, `:348`, `:376-378`, `:384`, `:390`), `os.chdir` to the checkout (`:25-26`), web server start incl. widening allowed hosts to `*` (`:251-257`) | `src/cli/` (commands), shim stays one release |
| `src/SofaScoreUi.py` (397) | interactive menu loop **and** the headless orchestration used by cron: `run_headless_fetch` (`:311-368`), `update_all_leagues` (`:370-392`), `export_all_to_csv` (`:394-397`); builds the three fetchers (`:93-95`) and creates data dirs (`:79-83`) | orchestration to `services/sync.py`; rest deleted |
| `src/ui/*.py` (2,199) | menus with `input()`; handler methods double as headless steps (`menu_ui.py`, `match_ui.py`); directory-copy backup/restore/clear (`settings_ui.py:297-669`) | deleted (section 7) |
| `src/season_fetcher.py` (484) | season list request + save (`:49-78`), "current season" heuristic that also looks at files on disk, stale season-id resolution (`:263-287`), reading the list back with three legacy file names (`:423-463`) | `services/listing.py` + Store |
| `src/match_fetcher.py` (735) | schedule fetch: rounds metadata (`:202-219`), week-based vs event-page strategy (`:176-187`, `:325-388`), round cache policy (`:390-425`), finished filter (`:111-142`, `:189-200`), season summary JSON+CSV writer (`:514-595`), previous-season fallback (`:91-109`) | `services/listing.py` + Store |
| `src/match_data_fetcher.py` (2,482) | see 1.2 | split across client, Store, pipeline, export, status |
| `src/utils.py` (696) | the request layer: cancel and wait-notifier ContextVars (`:50-97`), throttle hooks (`:126-135`), sync request (`:299-456`), async request (to `:624`); also module-level settings read at import (`:32-41`) | `src/client/` |
| `src/refresh.py` (147) | pure refresh policy: `refresh_due` (`:63-81`), `diff_basic` (`:95-98`), `change_row` (`:112-140`) | stays (domain); driven by `services/refresh.py` |
| `src/watcher.py` (407) | polling watcher: state reducer `_observe`, tick, JSON state file per sport (`:204-214`), JSONL event append without sequence numbers (`:216-222`), one sport per process (`:146-178`) | `services/live/` |
| `src/web/fetch_job.py` (343) | the web job: its own copy of the full-update orchestration (`:108-232`), breaker activation (`:86-87`), result/exception mapping (`:234-262`), detail phase planning (`:273-343`) | `services/sync.py`; file becomes a small adapter, then disappears |
| `src/web/jobs.py` (417) | SQLite job history plus an **in-memory** mirror, active id and exclusive slot (`:54-58`), i.e. single-process | persistence in `src/store/jobs.py` (on `state.db`), behaviour in `src/jobs/` |
| `src/web/progress.py` (207) | phase/percent/ETA/failed list; phases hard-wired to seasons/matches/details/export (`:12`) | `src/jobs/progress.py` |
| `src/web/routes/*` (1,459) | HTTP plus business logic, see 1.5 | `src/web/api/v1/*` (thin) + services |
| `src/web/league_sports.py` (110) | league→sport sidecar and inference by globbing `match_details` (`:77-89`) | `services/follows.py` + Store |
| `src/services/stats.py` (150) | the only service today; walks the tree for counts and sizes (`:84-147`) | `services/status.py` over the catalog |
| `src/sports.py`, `src/status.py`, `src/throttle.py`, `src/breaker.py`, `src/bridge_health.py`, `src/doctor.py` | already single-purpose | stay; wrapped by `src/client/` and services |

### 1.2 `src/match_data_fetcher.py`: twelve responsibilities in one class

| # | Responsibility | Lines |
|---|---|---|
| 1 | Locating events on disk, per-job index and "need" cache | `:124-190`, `:1151-1192` |
| 2 | Async batch pipeline (retry loop, cancel, breaker, progress) | `:192-505` |
| 3 | Reading a match directory back | `:534-557` |
| 4 | Slice presence predicates ("does this payload contain data") | `:559-637` |
| 5 | Availability markers `_unavailable.json` / `_slice_status.json`, reset | `:639-811` |
| 6 | Need computation and ordering (`full`/`refill`/`refresh`/`none`) | `:813-854` |
| 7 | Refresh of provisional records, `score_changes.jsonl` append | `:856-983` |
| 8 | Sync single-event and refill pipeline, per-slice wrappers | `:985-1149`, `:1448-1533` |
| 9 | Writing a match directory | `:1194-1241` |
| 10 | CSV flattening and export, three variants | `:1243-1446`, `:1535-1820`, `:2152-2321` |
| 11 | Work discovery from season summary CSVs, batch orchestration with `print`, exit code via `os.environ["APP_EXIT_CODE"]` (`:2102`) | `:1822-2112` |
| 12 | Coverage report with pandas, written into `processed/` | `:2323-2482` |

The class talks to the user with 34 `print()` calls and five `tqdm` bars (`:496`, `:1480`, `:1615`, `:1654`,
`:2347`), which is why nothing built on it can produce clean machine output.

### 1.3 Three orchestrators of one flow

The "update everything" flow (season lists → schedules → details → export) exists three times:

1. Web: `src/web/fetch_job.py:108-232`.
2. CLI, all leagues: `SofaScoreUi.py:370-392`, delegating to three TUI handlers.
3. CLI, one league: `SofaScoreUi.py:356-368` inline.

They differ: only the web flow resolves retired season ids (`fetch_job.py:153`); only the web flow counts
empty schedules (`fetch_job.py:182-191`) and always exports CSV at the end (`fetch_job.py:213-218`); the CLI
reports a tripped breaker through an environment variable read back at exit (`main.py:348`, `main.py:376-378`,
`match_data_fetcher.py:2102`).

### 1.4 Where the two detail pipelines diverge

| Aspect | Async batch (`match_data_fetcher.py:192-505`) | Sync (`:985-1149`, `:1448-1533`) |
|---|---|---|
| Reached from | league/season plans: `fetch_job.py:332` → `fetch_detail_ids` (`:2013`) → `:493-504`; CLI headless via `:1886` | matches picked by id in the web UI: `fetch_job.py:299`; single-match route `routes/matches.py:396-418`; TUI `:2114`; and from inside the async batch for refill/refresh through `asyncio.to_thread` (`:355-357`) |
| Slices requested | every `slices_for(sport)` incl. optional ones (`:219`), so tennis gets `point_by_point` | `required_only=True` (`:1046`), so `point_by_point` is never fetched |
| Unfinished events | skipped only when `FETCH_ONLY_FINISHED` is on (`:211`) | always skipped, regardless of the setting (`:1038`, `:1002`) |
| `/event` failure | exception → up to 3 attempts per match with back-off (`:338-413`), each attempt already retried by the request layer (`max_retries=2`, `:200`) | swallowed into `None` (`:1075-1080`); no per-match retry; request-layer default retries |
| Slice retries | `max_retries=1` (`:257`) | request-layer default (`:1117`) |
| Concurrency | matches and slices concurrent (`:320`, `:225`) | strictly sequential |
| Pacing on top of the throttle | 1 s between batches, twice (`:477-478`, `:2105-2107`) | 0.2 s per match (`:1527-1528`); refresh-only 1 s (`:977-978`) |
| HTTP session | warmed session, one TLS profile per session | no session, random TLS profile per request (`utils.py:360`) |
| Refill that returns `None` | falls through to a full fetch, repeating `/event` (`:361-362`) | same (`:1503-1505`) |
| Breaker scope | job breaker via `scope()` (`:286`) | batch has one (`:1483`); the single-match route has none |
| "Not finished" outcome | reported as a failed match (`:368-379`, `:415-417`) | reported as a failed match (`:1519-1521`) |
| Slice markers | both paths: only for `required` slices (`:684`) and only when the event is finished (`:1228-1229`) | same |

Consequence visible to users: the same match downloaded by "select matches" and by "download league" ends up
with different files, and `FETCH_ONLY_FINISHED=false` works on one path only.

### 1.5 Logic in route and UI code that belongs in services

| Location | Logic |
|---|---|
| `routes/matches.py:134-281` | building the match list from summary CSVs with pandas, de-duplication, date parsing, `has_details` by walking `match_details` (`:219-242`) |
| `routes/matches.py:309-358` | "missing details" = CSV ids minus a recursive glob of `basic.json` |
| `routes/matches.py:361-393` | assembling a match from slice files (third copy of `match_data_fetcher.py:534-557`) |
| `routes/matches.py:396-418` | single-event fetch policy (refill, then full), error-to-status mapping by substring (`:416-417`) |
| `routes/data.py:103-149`, `:152-182` | backup (zip) and clear |
| `routes/data.py:185-221` | CSV export; a **GET** that generates files when none exist (`:192-198`) |
| `routes/leagues.py:51-86` | remote tournament search incl. parsing SofaScore's payload, with a hard-coded URL (`:54`) |
| `routes/leagues.py:173-187`, `routes/common.py:26-38` | locating and reading season files |
| `routes/settings.py:134-217` | settings persistence, data-dir switch and job-store rebind |
| `routes/scrape.py:87-104` | job creation and thread start; `:111` returns a hard-coded version `"1.0.0"` |
| `web/league_sports.py:77-89` | sport inference by reading `basic.json` files |
| `ui/settings_ui.py:459-549` | **restore**, which exists only in the TUI and only for its directory-copy backups; the web backup is a zip (`routes/data.py:117`) that nothing can restore |
| `match_data_fetcher.py:2323-2482` | coverage report |

### 1.6 What the web backend imports from the terminal UI

- `src/web/fetch_job.py:19` imports `SimpleSofaScoreUI`, instantiates it at `:109` to reach
  `.season_fetcher`, `.match_fetcher`, `.match_data_fetcher`, and calls `ui.export_all_to_csv()` at `:217`,
  which runs a TUI handler (`SofaScoreUi.py:397`).
- `src/web/routes/data.py:193-197` does the same for CSV export.
- `src/web/routes/leagues.py:90-93` builds the whole UI object to call one method on `season_fetcher`.
- Each instantiation constructs six menu handlers and a shell (`SofaScoreUi.py:98-106`) and may set `NO_COLOR`
  process-wide (`:88-91`).
- Three test modules patch that name in the web job (`tests/test_breaker_phases.py:238`,
  `tests/test_job_progress.py:187`, `tests/test_storage_errors.py:256`).

### 1.7 Other findings that shape the design

- **Logs go to stdout.** `src/logger.py:39` installs `RichHandler` with the default console, whose stream is
  `sys.stdout`. `main.py:221` prints watch events to the same stream.
- **`--config` is a dead flag.** `ConfigManager` is a singleton that ignores arguments after first
  construction (`config_manager.py:46-67`); `src/utils.py:41` constructs it at import, which happens before
  `main.py:296` passes the path.
- **Settings are read in four ways**: module constants frozen at import (`utils.py:32-38`), `os.getenv` at
  call time (`refresh.py:27-51`, `config_manager.py:295`), `.env` rewriting, and direct `os.getenv` in routes
  (`routes/settings.py:121`). `FETCH_ONLY_FINISHED` changed in the UI therefore has no effect until restart
  (`utils.py:37`).
- **`API_BASE_URL` is applied inconsistently**: relative URLs honour it (`utils.py:291-292`,
  `match_fetcher.py:171`, `:208`, `:342`), four modules hard-code the absolute base
  (`match_data_fetcher.py:519`, `season_fetcher.py:35`, `watcher.py:32`, `routes/leagues.py:54`).
- **Jobs are single-process.** The active job, cancel flag and exclusive slot live in memory
  (`web/jobs.py:54-58`, `:239-241`); constructing a second `JobStore` marks every running row interrupted
  (`:129-148`). The CLI does not use the job store at all, so a cron run and a web job can write the same
  data directory concurrently; only the request budget is shared (`throttle.py`).
- **Bridge health is per process** (`bridge_health.py:24-26`), so a fresh `status` process cannot know
  whether the server is blocked.
- **Typed outcomes stop at event slices.** Season lists return `[]` on any failure (`season_fetcher.py:63-67`),
  rounds return `None` on any exception (`match_fetcher.py:480-482`), the watcher's 404 branch is unreachable
  (`watcher.py:188-191` with `utils.py:315-321`).
- **Paths are relative to the working directory** (`paths.py:11-22`, `config_manager.py:295`), held together
  by `os.chdir` in `main.py:25-26`. A library cannot rely on that.
- **Round listings already hold every status.** Round files are saved unfiltered
  (`match_fetcher.py:471`); only the summary and the event pages are filtered. In 184 locally stored football
  events, the object in a round listing lacks only `venue`, `referee`, `attendance`, period defaults and a few
  promo flags compared with `/event/{id}`; status, teams, scores and start time are present.
- **Default language is Turkish today** (`src/i18n.py:13`, `:36`) and the default request rate is 10 per
  second per concurrent request, 100 with default settings (`throttle.py:55-57`). Both defaults change in
  wave 3 (plan items X-01, X-02).
- **Open PRs.** #24 (`feat/log-files-diagnostics`), #23 (`fix/blocked-state-ux`, stacked on #24) and #32
  (`chore/post-wave2`, stacked on both) are open. Together they change `main.py`, `src/config_manager.py`,
  `src/logger.py`, `src/utils.py`, `src/season_fetcher.py`, `src/doctor.py`, `src/web/app.py`, four route
  modules (`api.py`, `leagues.py`, `scrape.py`, `settings.py`), `tests/conftest.py`, the installers, the CI
  workflow and the changelog, and add `src/diagnostics.py`, `src/redact.py`, `src/web/upstream.py`. This design
  assumes all three are merged first; the plan marks which PRs must wait for them.

---

## 2. Target architecture

### 2.1 Layer rules (enforced by an import-linter test)

1. Faces (`src/cli`, `src/web`, `src/api.py`) import only `src/services`, `src/jobs`, `src/config`, `src/errors`.
   They contain argument parsing, serialisation and nothing else.
2. Only `src/client` sends requests to SofaScore. Only `src/store` touches `DATA_DIR`, including every SQLite
   file and every lock file in it.
3. Services never print, never read `os.environ`, never call `sys.exit`. They take a `ServiceContext`,
   return typed results and raise `PlatformError` subclasses.
4. Domain modules (`sports`, `status`, `refresh`, `slices`, `schema`) are pure and import nothing above them.
5. `src/client` and `src/store` do not import each other. `src/jobs` uses `src/store` for persistence and
   leases and contains no SQL and no file access.

### 2.2 Module layout

```
src/
  api.py                    public Python API: Platform, re-exports of result types
  errors.py                 PlatformError hierarchy and the code table (2.6)
  config/
    settings.py             Settings (frozen dataclasses), defaults
    loader.py               file + env + flags merge, validation, legacy .env / leagues.txt import
    schema.py               JSON Schema of the config file (for `describe config`)
  client/
    __init__.py             Client, request_context
    transport.py            curl transport + bridge fallback   (from utils.py:299-624)
    context.py              RequestContext: cancel, wait notifier, breaker (the ContextVars of utils.py:61-97, breaker.py:252-268)
    endpoints.py            every URL template in one place
    bridge.py               BrowserBridge                      (from challenge_solver.py)
    (throttle.py, breaker.py, bridge_health.py stay at src/ and are re-exported; moving them is cosmetic)
  store/                    designed in 01-storage.md; services use its public API directly
  sports.py status.py refresh.py                 domain
  slices.py                 Outcome, presence predicates (match_data_fetcher.py:65-89, :559-637)
  schema/                   normalized schema v1: models, mappers from catalog rows and payloads, JSON Schema
  services/
    context.py              ServiceContext, build_context(settings), Clock
    planning.py             targets → WorkItems; need computation (from match_data_fetcher.py:813-854)
    pipeline.py             the one fetch pipeline (section 3)
    listing.py              season lists and schedules as pipeline work
    sync.py                 SyncService
    refresh.py              RefreshService
    live/                   supervisor.py, reducer.py, poll_source.py, push_source.py, arbiter.py
    export.py               ExportService
    backup.py               BackupService
    maintenance.py          MaintenanceService
    status.py               StatusService (health, summary, coverage); doctor stays src/doctor.py
    follows.py              FollowsService
    query.py tournaments.py QueryService (read side), incl. the legacy response shapes
  jobs/
    model.py                Job, JobKind, JobState, JobEvent
    manager.py              JobManager, JobHandle
    progress.py             JobProgress               (from web/progress.py)
    scheduler.py            optional in-app scheduler
  sinks/
    base.py dispatcher.py stdout.py file.py webhook.py
  cli/
    main.py output.py exit_codes.py signals.py legacy_flags.py commands/*.py
  web/
    app.py errors.py deps.py sse.py
    api/v1/*.py             one router per resource
    api/legacy.py           the old /api paths as adapters
```

`src/` stays the import package during the build-out (decision D1).

### 2.3 Composition

```python
# src/services/context.py
@dataclass(frozen=True)
class ServiceContext:
    settings: Settings
    store: Store            # src.store.Store
    client: Client | None   # None when opened read-only
    jobs: JobManager
    clock: Clock

def build_context(settings: Settings, *, readonly: bool = False) -> ServiceContext: ...
    # opens the Store, applies config follows (store.follows.apply(..., origin="config")), mirrors the legacy
    # league files, and wires client health changes to store.runtime.set("bridge_health", ...)

# src/api.py: the library face
class Platform:
    @classmethod
    def open(cls, config: str | os.PathLike | None = None, *, data_dir: str | None = None,
             overrides: Mapping[str, Any] | None = None, readonly: bool = False) -> "Platform": ...
    sync: SyncService; refresh: RefreshService; live: LiveService; export: ExportService
    backup: BackupService; maintenance: MaintenanceService; status: StatusService
    follows: FollowsService; query: QueryService; jobs: JobManager
    def close(self) -> None: ...
    def __enter__(self) / __exit__(...)
```

`Platform.open` never changes the working directory and resolves every relative path against the config
file's directory (or the given `data_dir`). `readonly=True` opens the Store read-only and builds no client;
query, export and status work, everything else raises `NotSupportedError`.

`Settings` replaces `ConfigManager` as the source of truth. During the transition `ConfigManager` becomes an
adapter that reads from the active `Settings`, so existing call sites keep working.

### 2.4 Client

```python
# src/slices.py — one outcome type for client, pipeline and Store (01-storage.md 2.3)
class Outcome:                 # generalises SliceOutcome (match_data_fetcher.py:65-89)
    status: Literal["ok", "empty", "failed", "skipped"]
    data: Any | None
    reason: str | None       # failed: 403|429|5xx|timeout|network|parse|other
                             # empty: 404|empty   skipped: not_selected|not_applicable|not_due|unavailable|breaker|cancelled
    http_status: int | None
    fetched_at: datetime
    via: Literal["curl", "bridge"] | None
    meta: Mapping[str, Any] | None

class Client:
    def __init__(self, settings: ClientSettings, *, throttle=None, health=None,
                 on_health_change: Callable[[Mapping], None] | None = None): ...
    async def get(self, path: str, *, retries: int | None = None, timeout: float | None = None,
                  lane: str = "api") -> Outcome: ...             # never raises for upstream conditions
    def get_sync(self, path: str, **kw) -> Outcome: ...          # for doctor/status and one-off callers
    def health(self) -> BridgeHealthSnapshot: ...
    async def aclose(self) -> None: ...

@contextmanager
def request_context(*, cancel: Callable[[], bool] | None, on_wait: Callable[[str, float], None] | None,
                    breaker: CircuitBreaker | None) -> Iterator[RequestContext]: ...
```

Semantics:
- `get` returns an `Outcome`; the only exceptions it raises are `Cancelled` (today `FetchCancelled`,
  `utils.py:50-55`) and programming errors. The mapping 404 → `empty/404`, breaker open → `skipped/breaker`,
  everything else → `failed/<kind>` is today's `SliceOutcome.from_error` plus `breaker.failure_kind`
  (`breaker.py:70-88`). Today the open breaker is reported as a *failed* outcome with reason `breaker`
  (`match_data_fetcher.py:71`, `:693-694`); it becomes `skipped`, which the Store ignores in the same way.
- One transport implementation (async); `get_sync` drives it on the bridge's background loop. The duplicated
  sync body `utils.py:326-456` is deleted. The path at the end of the async body that returns `None` without
  an exception (`utils.py:624`) is mapped to `failed/other`.
- Paths are always relative; `endpoints.py` builds them, `ClientSettings.base_url` is applied in one place.
- The throttle stays where it is: every request reserves a slot (`utils.py:126-135`,
  `challenge_solver.py:297`). The default rate becomes 5 req/s in plan item X-01; `0`/`off` removes the limit
  (`throttle.py:83-100` already supports it).
- The client writes nothing under `DATA_DIR`. It reports each bridge-health transition through
  `on_health_change`; `build_context` stores the snapshot with `store.runtime.set("bridge_health", ...)`, so
  another process (`ssc status`, a second server) can read the last known state.

### 2.5 What services need from the Store, and where the Store provides it

The parallel draft of this document proposed its own `Store` and `EventLog` protocols. They were replaced by
the Store's API (`01-storage.md` 2.3); services import `src.store` directly and tests use a real Store in a
temporary directory. The proposed operations map as follows:

| Need of the services | Store API |
|---|---|
| write one event with its slices atomically; an older observation must not win; get back what changed | `store.events.put(event_id, outcomes, on_event_change=..., count_empties=..., keep_history=...)` → `PutResult` (`created`, `event_written`, `superseded`, `written`, `change_seq`) |
| compare old and new event payload under the same lock as the write | the `on_event_change(old, new)` callback of `put`; the service passes a function built from `diff_basic` and `change_row` (`src/refresh.py:95-140`) |
| write a season list, a round or an event page and upsert the listed events | `store.entities.put(Ref.season(...), {("schedule", "round_12"): outcome}, index_listed_events=True)` |
| write a season/team/player/sport slice | `store.entities.put(ref, {key or (key, sub): outcome})` |
| reset "unavailable" marks | `store.events.reset_empty_markers(scope, include_confirmed=...)` |
| per-event state for planning, without a tree walk | `store.events.missing(...)`, `refresh_candidates(...)`, `stale(...)`, `open_events(...)` for the common questions; `store.events.states(scope)` for the general case |
| listing state (complete flag, age) | `store.entities.slice(ref, "schedule", sub)` → `SliceInfo.meta["complete"]`, `fetched_at` |
| event rows and lists for the faces | `store.events.get/list/count/iter` return catalog rows; `src/schema` maps rows and payloads to the normalized v1 records |
| raw payload | `store.events.payload(id, key, sub, raw=True)`, `store.entities.payload(...)`: the stored JSON bytes. They are the parsed response serialised again, value-identical but not byte-identical to the wire (`01-storage.md` 4.1) |
| tournaments, seasons, changes, summary | `store.entities.tournaments/seasons`, `store.changes.list`, `store.events.summary`, `store.info` |
| export rows | `store.events.iter` + `src/schema` mappers + `store.export.rows`; raw export is `store.export.raw` |
| clear, verify, rebuild, migrate | `store.clear`, `store.catalog.verify/rebuild`, `store.migrate.plan/run` |
| durable streams with sequence numbers | `store.streams.append/read/wait/head/prune`; sink positions with `store.streams.cursor/set_cursor` |
| watcher state | `store.watch.load/save` |
| jobs, job events, cancel across processes, leases | `store.jobs`, `store.lease(name)` |

Requirements and how they are met:

- R1. Planning a whole season must cost one indexed query: `missing()` and `refresh_candidates()` are single
  SQL statements on named indexes (`01-storage.md` 3.7); `states()` is one range query per batch.
- R2. The event streams, job history, sink cursors and API-created follows must survive "delete the catalog
  and rebuild": they are in `state.db`, which a rebuild never touches.
- R3. Appending to a stream is safe from several processes and a reader never sees a higher number before a
  lower one is committed: all appends run in `BEGIN IMMEDIATE` transactions on one `AUTOINCREMENT` column.
  Numbers are strictly increasing; they are **not** consecutive within one stream.
- R4. Both layouts are read through the same calls; services never learn which layout an event is in.

### 2.6 Errors: one table for all three faces

`src/errors.py` defines `PlatformError(code, message, details=None)`; the existing request-level exceptions
(`src/exceptions.py:24-88`) stay inside `src/client`.

| `code` | Class | Exit code | HTTP | Meaning |
|---|---|---|---|---|
| `invalid_request` | `UsageError` | 2 | 400/422 | bad arguments or body |
| `config_invalid` | `ConfigError` | 2 | 500 at start-up | config file or env value rejected |
| `confirmation_required` | `UsageError` | 2 | 400 | destructive action without `--yes` / `confirm: true` |
| `unauthorized` | – | – | 401 | access token missing or wrong |
| `forbidden_origin` | – | – | 403 | cross-origin write (`web/app.py:37-47`) |
| `not_found` | `NotFoundError` | 1 | 404 | unknown id or slice |
| `follow_managed` | `ConflictError` | 1 | 409 | the follow comes from the config file (`FollowManaged`) |
| `follow_exists` | `ConflictError` | 1 | 409 | duplicate follow (`FollowExists`) |
| `job_running` | `JobRunningError` | 6 | 409 | a job holds the `writer` lease in this or another process |
| `data_operation_running` | `ConflictError` | 6 | 409 | backup/clear/restore/migrate/rebuild in progress |
| `instance_running` | `InstanceRunningError` | 6 | 409 | live service or dispatcher already running on this data dir |
| `blocked` | `UpstreamBlockedError` | 4 | 503 | SofaScore refuses us (breaker reason 403 or bridge blocked) |
| `rate_limited` | `UpstreamBlockedError` | 4 | 503 | breaker reason 429 |
| `upstream_error` | `UpstreamError` | 4 | 502 | breaker reason 5xx/other |
| `partial` | (result state, not raised) | 3 | 200 | finished with failed items |
| `storage_error` | `StorageError` (`exceptions.py:109`), incl. every `StoreError` | 5 | 507 | disk full, permission, corrupt store, store busy, schema too new |
| `not_supported` | `NotSupportedError` | 2 | 501 | e.g. Parquet without `pyarrow`; write on a read-only platform |
| `cancelled` | `Cancelled` | 130/143 | – | signal or cancel request |
| `internal` | – | 1 | 500 | bug |

The Store raises `LeaseHeld`; the job manager turns it into `job_running`, `data_operation_running` or
`instance_running` from the name and purpose of the held lease. The existing classes `JobRunningError` and
`DataOperationRunningError` (`src/web/jobs.py:30-46`) and their 409 handler (`src/web/app.py:59-65`) are kept.

`describe errors` prints this table; a test asserts that every `PlatformError` subclass has a row.

### 2.7 Public service APIs

All `run`-style methods are blocking. Faces that want a background job pass them to `JobManager.submit`.

```python
# services/sync.py
@dataclass(frozen=True)
class SyncSpec:
    targets: tuple[Target, ...] | None = None        # None = every enabled follow
    phases: frozenset[str] = frozenset({"listing", "events", "non_match", "refresh"})
    slices: SliceSelection | None = None             # None = the follow's / default selection
    force: bool = False                              # refetch slices that are already ok
    recheck_unavailable: Literal["legacy", "all"] | None = None
    limit: int | None = None                         # max work items (for tests and cautious first runs)

Target = TournamentTarget(tournament_id, seasons: SeasonSelector, sport=None) | EventTarget(event_id) | SeasonTarget(tournament_id, season_id)

class SyncService:
    def plan(self, spec: SyncSpec) -> SyncPlan: ...
        # no network; SyncPlan(items, counts by need, estimated_requests, estimated_seconds at the configured rate)
    def run(self, spec: SyncSpec, *, handle: JobHandle | None = None) -> SyncResult: ...
        # raises StorageError (fatal), JobRunningError / InstanceRunningError (lease), ConfigError; never raises for upstream trouble
    def fetch_events(self, event_ids: Sequence[int], *, slices=None, force=False, handle=None) -> SyncResult: ...

@dataclass(frozen=True)
class SyncResult:
    state: Literal["succeeded", "partial", "failed", "cancelled"]
    counts: Mapping[str, int]    # listings_ok/failed, events_stored/unchanged/failed/skipped, slices_ok/empty/failed, refreshed, changed, requests
    failed: tuple[FailedItem, ...]          # capped at 50, with reason per item
    stopped: StopReason | None              # blocked | rate_limited | upstream_error | cancelled | storage_error
    duration_seconds: float
```

```python
# services/refresh.py
class RefreshService:
    def due(self, scope: Scope | None = None, *, include_legacy: bool = False) -> list[int]: ...
    def run(self, scope: Scope | None = None, *, include_legacy: bool = False, handle=None) -> SyncResult: ...
        # = SyncService.run(SyncSpec(phases={"refresh"})); kept as its own entry point because cron uses it alone

# services/live/supervisor.py
class LiveService:
    def run(self, scope: LiveScope | None = None, *, until: float | None = None, stop: StopToken | None = None) -> LiveSummary: ...
    def start(self) -> None ; def stop(self, timeout: float = 10.0) -> None      # background hosting inside `serve`
    def status(self) -> LiveStatus: ...
    def events(self, after: int = 0, *, follow: bool = False, flt: StreamFilter | None = None) -> Iterator[LiveEvent]: ...
        # reads the stream log; works without a running service

# services/export.py
@dataclass(frozen=True)
class ExportSpec:
    dataset: str                 # events | slices | odds | changes | live_events | standings | ...
    format: Literal["jsonl", "csv", "parquet", "sqlite", "json"]
    schema: Literal["normalized", "raw"] = "normalized"
    filter: EventFilter = EventFilter()
    profile: Literal["legacy-wide-csv"] | None = None      # reproduces today's all_matches_*.csv columns
class ExportService:
    def export(self, spec: ExportSpec, dest: Path | BinaryIO, *, handle=None) -> ExportResult: ...   # rows, bytes, path
        # NotSupportedError when the optional dependency of a format is missing

# services/backup.py, services/maintenance.py
class BackupService:
    def create(self, scope: BackupScope = "all", *, include_secrets: bool = False, dest: Path | None = None, handle=None) -> BackupInfo: ...
    def list(self) -> list[BackupInfo]: ...
    def verify(self, path: Path) -> BackupReport: ...
    def restore(self, path: Path, *, force: bool = False, dry_run: bool = False, handle=None) -> RestoreReport: ...
        # into an empty data directory; force=True moves the current content to trash first. No merge mode.
class MaintenanceService:
    def clear(self, scope: DataScope, *, confirm: bool) -> ClearReport: ...
    def recheck_unavailable(self, scope: Scope | None, *, include_confirmed: bool = False) -> ResetCounts: ...
    def migrate(self, *, dry_run: bool, delete_legacy: bool = False, handle=None) -> MigrationReport: ...
    def rebuild_catalog(self, *, handle=None) -> RebuildReport: ...

# services/status.py
class StatusService:
    def health(self) -> Health: ...          # bridge (stored snapshot), throttle, leases, live, last job, last success
    def summary(self) -> DataSummary: ...    # counts, coverage, disk use, by tournament
    def coverage(self, scope: Scope) -> CoverageReport: ...
    def doctor(self, *, only=None, skip=None, live=False) -> DoctorReport: ...   # src/doctor.py run_checks/report
    def diagnostics_bundle(self, dest: Path | None = None) -> Path: ...          # src/diagnostics.py (PR #24)

# services/follows.py
class FollowsService:
    def list(self) -> list[Follow]: ...                      # every origin, each with its origin
    def add(self, spec: FollowSpec) -> Follow ; def update(self, follow_id, patch) -> Follow ; def remove(self, follow_id) -> None
        # origin "config": ConflictError("follow_managed").
        # origin "legacy": the change is written to leagues.txt / league_sports.json first (today's ConfigManager path), then mirrored.
        # With a config file present, new follows get origin "api"; without one they are written to leagues.txt as today.
    def resolve(self, names: Sequence[str] | None = None) -> tuple[Target, ...]: ...
    def search_tournaments(self, query: str, *, sport: str | None = None) -> list[TournamentHit]: ...   # routes/leagues.py:51-86

# services/query.py: thin, typed read access for faces
class QueryService:
    def events(self, flt: EventFilter, page: Page) -> PageOf[Event] ; def event(self, event_id) -> Event
    def slice(self, owner, key, sub="") -> Slice ; def raw(self, owner, key, sub="") -> RawPayload
    def tournaments(...) ; def seasons(...) ; def changes(since, page) ; def sports() -> list[SportInfo]
    # plus the functions that reproduce today's response shapes for the legacy /api routes
```

### 2.8 Jobs: one model for web and CLI, across processes

```python
class JobKind(str, Enum): SYNC, FETCH, REFRESH, EXPORT, BACKUP, RESTORE, CLEAR, MIGRATE, REBUILD
class JobState(str, Enum): QUEUED, RUNNING, SUCCEEDED, PARTIAL, FAILED, CANCELLED, INTERRUPTED

@dataclass(frozen=True)
class Job:
    id: str                    # sortable (ULID)
    kind: JobKind; state: JobState
    origin: Origin             # face: cli|api|scheduler|library, pid, host
    spec: Mapping[str, Any]    # the service spec, JSON
    progress: Mapping[str, Any] | None      # JobProgress.detail()
    result: Mapping[str, Any] | None
    error: ErrorInfo | None    # code from 2.6 + message
    created_at; started_at; finished_at; heartbeat_at; cancel_requested: bool

class JobManager:
    def submit(self, kind: JobKind, spec: Mapping, fn: Callable[[JobHandle], Any], *, origin: Origin,
               background: bool, wait_for_lease: float = 0.0) -> Job: ...
        # background=True: dedicated thread (web, scheduler); False: caller's thread (CLI, library)
        # raises JobRunningError / DataOperationRunningError when the lease is held and the wait expires
    def get(self, job_id) -> Job | None ; def list(self, *, limit=20, kinds=None, states=None) -> list[Job]
    def active(self) -> Job | None
    def cancel(self, job_id) -> bool                       # works from any process
    def events(self, job_id, *, after: int = 0, follow: bool = False) -> Iterator[JobEvent]
    def reap_stale(self) -> int

class JobHandle:                                           # what a service sees
    id: str
    def cancelled(self) -> bool
    progress: JobProgress
    def log(self, message: str, **fields) -> None
```

Mechanics:

- **Storage.** The `jobs` and `job_events` tables of `DATA_DIR/.meta/state.db`, reached through `store.jobs`
  (`01-storage.md` 3.3). Today's columns are kept, so old rows stay readable; `.meta/jobs.db` is imported once
  and left in place. State names are normalised on read (today's `Completed` with a breaker text becomes
  `partial`).
- **Leases** are the Store's (`01-storage.md` 6.1), taken with `store.lease(name)`:

  | Job kind | Lease |
  |---|---|
  | sync, fetch, refresh | `writer` |
  | backup | `writer` |
  | migrate | `writer`, and no live service may be running |
  | clear, restore, rebuild, data-directory change | `maintenance` |
  | export | none; it reads one catalog snapshot |
  | live service | `live` |
  | sink dispatcher | `sinks` |

  This preserves today's rule "one job at a time" (`web/jobs.py:151-168`, `:193-223`) and extends it to all
  processes.
- **Liveness.** A job is running if its row says so **and** the lease is held. A running row whose lease is
  free is marked `interrupted` by whoever notices (`reap_stale`); this replaces the unconditional sweep at
  `web/jobs.py:129-148`, which would be wrong with two processes. `heartbeat_at` is written every 5 s for display.
- **Cancel.** `cancel()` sets `cancel_requested` in the row. The runner keeps an in-memory flag (same
  process: set directly) refreshed from the row every second by a ticker; `JobHandle.cancelled` is the cancel
  check installed in the request context, so waits and retries stop within a fraction of a second as today
  (`fetch_job.py:73-76`).
- **Progress and events.** `JobProgress` publishes into the row (as today) and appends coalesced
  `job_events` (at most two progress events per second, all phase/log/failed/breaker events, last 2,000 kept
  per job). `job.started` and `job.finished` are also appended to the `job` stream for sinks (section 5).
- **Where jobs run.** In the process that created them. There is no daemon and no queue; `queued` exists
  only for the in-app scheduler, which coalesces (a task whose previous run still holds the lease is skipped
  and logged).
- **Terminal state rule.** `failed` if a fatal error aborted the job; else `cancelled` if cancelled; else
  `partial` if the breaker stopped it or any item failed; else `succeeded`. Today a breaker stop is shown as
  "Completed" with a text (`fetch_job.py:223-226`).

---

## 3. One fetch pipeline

### 3.1 Slice registry

`src/sports.py` already has the table and the single selection point (`sports.py:170-218`, whose docstring
reserves the user setting). It is extended, not replaced:

```python
@dataclass(frozen=True)
class SliceSpec:                     # today's DetailSlice (sports.py:170-188) plus:
    key: str
    path: str                        # placeholders: {event_id} {tournament_id} {season_id} {team_id} {player_id} {sub}
    owner: Literal["event", "season", "tournament", "team", "player", "sport"] = "event"
    subs: tuple[str, ...] | Literal["provider"] | None = None   # e.g. ("total", "home", "away"); "provider" = the configured odds provider id
    sports: frozenset[str] | None = None
    phases: frozenset[Literal["pre", "live", "post"]] = frozenset({"post"})   # when the slice can exist (event owners)
    default_enabled: bool = True
    counts_for_completeness: bool = True       # today's `required`
    keep_history: bool = False                 # append changed payloads to the slice history (odds)
    max_age: timedelta | None = None           # owner slices: refetch when older (None = once)
    group: str = "core"                        # core | odds | standings | statistics | squads | rankings | live

def select_slices(owner: str, sport: str | None, selection: SliceSelection, *, phase: str | None = None) -> tuple[SliceSpec, ...]
```

A slice is addressed as owner + `key` + optional `sub`, the Store's addressing (`01-storage.md` 2.3).

`SliceSelection` is resolved per follow: follow `slices` → `[slices.<sport>]` enable/disable → `[defaults]`
→ registry `default_enabled`. Entries are slice keys or group names. A slice that is not selected is never
requested; its state is reported as `not_requested`.

Odds are ordinary event slices in group `odds` with `phases={"pre","live","post"}`, `subs="provider"` (from
`[client] odds_provider`) and `keep_history=True`: `odds_all` (`/event/{id}/odds/{provider}/all`),
`odds_featured`, `odds_changes` (`/event/{id}/odds/{provider}/changes`), `winning_odds`
(`/event/{id}/provider/{provider}/winning-odds`), all seen in `docs/all-sports/endpoints.csv`. Pre-match
odds are refetched on each sync until kick-off (`max_age`), then once after the event is terminal.

Non-match data are slices with another owner, for example `standings` with subs `total`, `home`, `away`
(`/unique-tournament/{tournament_id}/season/{season_id}/standings/{sub}`, owner `season`), `season_info`,
`cuptrees`, `players` (owner `team`), `rankings` (owner `sport`). The planner emits one work item per
owner, not per event; the owner set is derived from the followed seasons and the teams seen in their events.
The exact list and their `max_age` values are wave 5 work; the mechanism lands with the pipeline.

### 3.2 Planning

```python
@dataclass(frozen=True)
class WorkItem:
    owner: Ref                            # Ref.event(123) | Ref.season(17, 61627) | Ref.tournament(17) | Ref.team(42)
    need: Literal["listing", "full", "refill", "refresh", "owner"]
    slices: tuple[tuple[str, str], ...]   # (key, sub), already resolved; () for refresh
    sport: str | None
    reason: str                           # shown by --dry-run

def compute_need(state: EventState, selection, policy: RefreshPolicy, now) -> Need      # pure
```

`planning.py` turns targets into items in phase order: listing → events (full, then refill) → non-match
owners → refresh, the order of `_order_by_need` (`match_data_fetcher.py:845-854`). The inputs are
`store.events.missing/refresh_candidates/stale/states` and `store.entities.slice`; nothing is discovered by
reading summary CSVs (`match_data_fetcher.py:1953-2007`).

Need rules for an event (today's `match_data_fetcher.py:830-843` generalised):

| Stored state | Need |
|---|---|
| unknown, or known from a listing only (no `/event` payload) and terminal | `full` |
| has `/event` payload; a selected, applicable, phase-valid slice is neither `ok` nor confirmed unavailable nor fresh | `refill` |
| complete, still provisional and due (`refresh.py:63-81`) | `refresh` |
| flagged `stale` by a newer listing (wave 4) | `refresh`, first |
| not started or void, no pre-match slice selected | none: the listing keeps it current |
| live | none: owned by the live service |
| otherwise | none |

"Confirmed unavailable" keeps today's rule: two definitive empty answers (404 or empty 200) on a terminal
event (`match_data_fetcher.py:45-49`, `:664-717`). Empty answers on a non-terminal event are recorded but do
not count (`count_empties=False` in the Store call).

### 3.3 Execution

```python
class FetchPipeline:
    def __init__(self, ctx: ServiceContext, *, concurrency: int): ...
    async def run(self, items: Iterable[WorkItem], *, handle: JobHandle,
                  on_result: Callable[[ItemResult], None] | None = None) -> PipelineSummary: ...
```

One asyncio loop per job, started with `asyncio.run` in the job's thread (as `match_data_fetcher.py:500`
does today). `concurrency` workers pull items; a single writer thread applies results to the Store through a
bounded queue, so the event loop never blocks on disk and memory stays bounded when the Store is slow.

Per event item:

1. Stop conditions first: cancelled → stop; breaker tripped → remaining items become `skipped/breaker`.
2. `GET /event/{id}` → `Outcome`. `failed` → item failed with the reason. `empty/404` → recorded as an error
   mark on the event (nothing is deleted).
3. From the payload: `classify_status`, sport slug, phase.
4. Slices to request = planned slices ∩ applicable to the sport ∩ valid in this phase, minus `ok` and fresh
   (unless `force`), minus confirmed unavailable.
5. Slice requests run concurrently; each yields an `Outcome`.
6. One `store.events.put(event_id, {"event": ..., key: ..., ...}, on_event_change=change_fn, ...)` call. The
   Store commits it atomically. When the callback produced a change row (a tracked field of a previously
   stored event changed, today `match_data_fetcher.py:880-887`), the pipeline appends a `change.recorded`
   event to the `change` stream after the write has committed.

`refresh` items do steps 1, 2 and 6 only (`store.events.observe`). `listing` items fetch a season list, the
rounds metadata and then rounds or event pages (the strategy of `match_fetcher.py:176-388`), hand each payload
to `store.entities.put`, and yield new `full` items for events that became terminal, so one job covers
"schedule changed, fetch the new results". `owner` items fetch non-match slices.

What unification settles (each line is a deliberate behaviour change against one of the two old paths):

- One retry policy: the request layer's (`MAX_RETRIES`). The extra per-match loop
  (`match_data_fetcher.py:338-413`) is removed; it multiplied retries by three.
- All selected slices on every path, including optional ones, and slice marks for all of them (today only
  `required` slices are tracked, `:684`).
- One finished-only rule, applied in the planner. Until wave 4 it still honours `FETCH_ONLY_FINISHED`; in
  wave 4 every status is stored and the flag becomes a read filter.
- An event that is not due is `skipped/not_due`, not "failed" (`match_data_fetcher.py:368-379`, `:1519-1521`).
- No sleeps outside the client (`:477-478`, `:977-978`, `:1527-1528`, `:2105-2107` go away). The shared
  throttle is the only governor.
- A refill that cannot proceed does not issue a second `/event` (`:361-362`).
- Every request of a job runs under the job's breaker and cancel check, including single-event fetches.
- Listings have typed outcomes: a failed season list or round is a failed item, not "no seasons"
  (`season_fetcher.py:63-67`, `match_fetcher.py:480-482`).
- The job no longer exports CSV at the end (`fetch_job.py:213-218`); export is on demand (decision D9).

### 3.4 Breaker, throttle, cancel

- Breaker: one per job, activated by `JobManager` through the request context (today `fetch_job.py:86-87`,
  `main.py:344`). When it trips, the pipeline stops, the job ends `partial` with `error.code` =
  `blocked` | `rate_limited` | `upstream_error` (from `CircuitBreaker.reason()`, `breaker.py:241-247`) and the
  CLI exits 4. `--ignore-breaker` maps to today's `IGNORE_RATE_LIMIT` (`breaker.py:107-108`).
- Throttle: unchanged mechanism; `[client] rate`. `concurrency` only bounds in-flight requests.
- Sizing note: at 5 req/s a football event costs 7 requests (1.4 s), a 380-event season about 9 minutes.
  `sync --dry-run` reports `estimated_requests` and `estimated_seconds` from the plan.

### 3.5 Idempotency and resumability

- The catalog is the checkpoint. A job has no private progress file; the plan is recomputed from stored
  state at every start, so re-running after a crash, a cancel or a breaker stop continues where it stopped.
- `put` is atomic per event; a killed process leaves either the old or the new state of an event.
- Listing payloads carry `complete` and `fetched_at`; complete rounds are not refetched, incomplete ones are
  refetched after the TTL (`match_fetcher.py:390-425`).
- Running the same command twice in a row performs zero requests the second time, except listings past
  their TTL and provisional events that are due. A golden test asserts this.

---

## 4. CLI for servers and automation

Principles: never prompts; stdout carries only the result; logs and progress go to stderr; every command
is safe to repeat; exit codes are part of the contract.

### 4.1 Commands

| Command | Purpose | Notable options |
|---|---|---|
| `ssc sync` | bring every follow up to date (listings, missing data, non-match data, refresh) | `--follow NAME...`, `--only listing,events,non-match,refresh`, `--slices`, `--force`, `--recheck-unavailable[=legacy\|all]`, `--limit N`, `--dry-run` |
| `ssc fetch event ID...` / `fetch tournament ID [--season ID\|current\|all\|last:N]` | fetch explicit targets without a follow | `--sport`, `--slices`, `--force`, `--dry-run` |
| `ssc refresh` | re-read provisional events only | `--tournament ID`, `--include-legacy` |
| `ssc watch` | run the live service in the foreground | `--sport`, `--tournament ID...`, `--event ID...`, `--for DURATION`, `--source push,poll`, `--stdout` (NDJSON events) |
| `ssc events` | read the stream log (no service needed) | `--stream live\|job\|change\|system`, `--after SEQ`, `--follow`, `--type`, `--event`, `--limit` |
| `ssc export` | write a dataset in an open format | `--dataset`, `--format jsonl\|csv\|parquet\|sqlite\|json`, `--schema normalized\|raw`, `--out PATH\|-`, filters `--sport --tournament --season --from --to --status` |
| `ssc serve` | HTTP API and web UI | `--host`, `--port`, `--allowed-hosts`, `--live`, `--scheduler`, `--dev` |
| `ssc status` | data summary, health, leases, last job, live state | `--check` (exit code only), `--coverage` |
| `ssc doctor` | environment check (`src/doctor.py`) | `--strict`, `--live`, `--only`, `--skip` |
| `ssc describe [sports\|slices\|commands\|schemas\|config\|errors\|exit-codes]` | machine-readable self-description for agents | always JSON |
| `ssc diagnostics` | write the diagnostics bundle | `--out PATH` |
| `ssc migrate` | convert the old layout (Store) | `--dry-run [--exact]`, `--tournament ID`, `--limit N`, `--delete-legacy`, `--purge-derived`, `--yes` |
| `ssc catalog rebuild\|verify` | rebuild or check the catalog (Store) | `--deep`, `--repair` |
| `ssc jobs list\|show ID\|cancel ID\|tail ID` | job history and control across processes | `--limit`, `--follow` |
| `ssc follows list\|add\|remove\|export` | follows; `export` prints all of them as config text | |
| `ssc backup create\|list\|verify\|restore PATH` | backups | `--scope`, `--include-secrets`, `--force`, `--dry-run`, `--yes` |
| `ssc data clear\|recheck-unavailable` | destructive maintenance | `--scope`, `--all`, `--yes` |
| `ssc config show\|validate\|init\|path` | effective configuration with the source of each value; secrets masked | |
| `ssc version` | version, schema versions | |

`describe` is generated from the argparse tree, the slice registry, the error table and the response models,
so it cannot drift from the implementation. Each command is one module under `src/cli/commands/` that
registers itself, so a PR that adds a command adds a file and does not edit a shared one.

### 4.2 Global flags

`--config PATH`, `--data-dir PATH`, `--json` (same as `--output json`), `--output text|json|ndjson`,
`--quiet`, `--verbose`, `--log-level`, `--log-format text|json`, `--no-color`, `--lang en|tr`,
`--rate N|off`, `--ignore-breaker`, `--wait SECONDS` (wait for a lease instead of exiting 6),
`--progress none|text|ndjson` (stderr), `--version`.

### 4.3 Configuration file

One declarative file, `sofascore.toml` (decision D3), found in this order: `--config`, `SOFASCORE_CONFIG`,
`./sofascore.toml`, `CONFIG_DIR/sofascore.toml`. The application never writes it.

```toml
schema = 1

[storage]
data_dir = "data"

[client]
rate = 5                  # requests per second across all processes; 0 or "off" removes the limit
max_concurrent = 10
timeout_seconds = 20
retries = 3
proxy = ""                # or proxy_env = "PROXY_URL"
odds_provider = 1

[defaults]
slices = ["core"]         # group names or slice keys; see `ssc describe slices`
seasons = "current"       # current | all | last:N | [ids]

[slices.tennis]
enable = ["point_by_point"]
disable = ["lineups", "incidents"]

[[follow]]
name = "premier-league"
sport = "football"
tournament = 17
seasons = "last:2"
slices = ["core", "odds", "standings"]
live = true

[[follow]]
event = 17124861

[refresh]
window_hours = 72
min_interval_hours = 6

[live]
sources = ["push", "poll"]
poll_interval_seconds = 30
detail_slices = []        # e.g. ["incidents", "statistics"] while an event is live
detail_interval_seconds = 20

[[sink]]
name = "ops"
type = "webhook"
url = "https://example.org/hooks/sofascore"
secret_env = "SOFASCORE_HOOK_SECRET"
events = ["live.*", "job.finished"]

[[sink]]
name = "feed"
type = "file"
path = "out/live.ndjson"
events = ["live.*"]

[schedule]
enabled = false
[[schedule.task]]
run = "sync"
every = "6h"              # or cron = "15 */6 * * *"

[server]
host = "127.0.0.1"
port = 8000
allowed_hosts = ["localhost", "127.0.0.1"]
token_env = ""            # name of the env var holding the optional access token

[log]
level = "info"
format = "text"
```

Precedence, lowest to highest: built-in defaults → UI-edited overrides (`CONFIG_DIR/overrides.json`,
machine-written) → config file → environment → command-line flags. A value pinned by file, env or flag is
shown as locked in the web UI (decision D4). `config show` prints each value with its source.

Environment overrides: `SOFASCORE_<SECTION>__<KEY>`, e.g. `SOFASCORE_CLIENT__RATE=5`,
`SOFASCORE_SERVER__PORT=9000`, `SOFASCORE_STORAGE__DATA_DIR=/data`; lists as JSON
(`SOFASCORE_FOLLOWS='[{"sport":"football","tournament":17}]'`). The current names (`DATA_DIR`,
`REQUEST_RATE_LIMIT`, `MAX_CONCURRENT`, `APP_LANGUAGE`, `USE_PROXY`, `PROXY_URL`, the breaker and bridge
thresholds, `REFRESH_*`, `SOFASCORE_ALLOWED_HOSTS`, `SOFASCORE_BROWSER_PROFILE`, …; `.env.example`) are read
for one release with a deprecation warning.

Follows have three origins (`01-storage.md` 2.3):

- **Without a config file** (every existing installation): `config/leagues.txt` and `league_sports.json` stay
  the source of truth, are edited by the web UI as today, and are mirrored into the Store as origin `legacy`.
  `.env` is read as settings. Nothing changes for the user.
- **With a config file**: its `[[follow]]` entries are origin `config` (read-only through the API); follows
  added through the API or the web UI are origin `api` and live in `state.db`; `leagues.txt` is not read.
  `ssc config init --from-legacy` prints the TOML equivalent of the legacy files, and `ssc follows export`
  prints every follow as config text for folding API-created ones back into the file.

`sync` processes every enabled follow regardless of origin.

### 4.4 Output conventions

- stdout: the result only. stderr: logs, progress, human-readable errors.
- Without `--json`: concise text for humans, localised (English default, Turkish when detected).
- With `--json`: exactly one JSON document, never localised:

```json
{"ok": true, "command": "sync", "schema": "sofascore.cli/1", "version": "3.0.0",
 "data": {"job_id": "01J…", "state": "partial", "counts": {"events_stored": 371, "events_failed": 9},
          "stopped": null, "duration_seconds": 512.4},
 "warnings": []}
```

```json
{"ok": false, "command": "sync", "schema": "sofascore.cli/1", "version": "3.0.0",
 "error": {"code": "job_running", "message": "A sync job is already running on this data directory.",
           "details": {"holder": {"pid": 4121, "purpose": "sync", "since": "2026-10-01T18:00:02Z"}}},
 "exit_code": 6}
```

- Streaming commands (`watch --stdout`, `events`, `jobs tail`, `export --out -` with `jsonl`) write NDJSON:
  one object per line, each with `type`; the stream ends with a `{"type":"end", …}` line when it ends by itself.
- Field rules: timestamps ISO-8601 UTC with `Z`; ids are integers; durations in seconds; enums lower snake case;
  absent is `null`, never an empty string.
- `--progress ndjson` writes job events to stderr in the `JobEvent` shape used by the API.

### 4.5 Exit codes

| Code | Meaning | Typical source |
|---|---|---|
| 0 | success; also a service stopped by SIGTERM/SIGINT | |
| 1 | general error; `doctor` with a failed check; `status --check` unhealthy store | `internal`, `not_found` |
| 2 | usage or configuration error | `invalid_request`, `config_invalid`, `confirmation_required`, `not_supported` |
| 3 | partial success: finished, some items failed | job state `partial` without a breaker stop |
| 4 | SofaScore blocks or keeps failing; the breaker stopped the job | `blocked`, `rate_limited`, `upstream_error` |
| 5 | storage error | `storage_error` |
| 6 | another instance holds the lease | `job_running`, `data_operation_running`, `instance_running` |
| 130 / 143 | one-shot command cancelled by SIGINT / SIGTERM | decision D6 |

Codes 0 to 6 are the owner's (`00-platform.md` section 6); 130/143 are an addition that needs confirmation
(D6). Precedence when several apply: 5 > 4 > 6 > 3 > 1. Today's codes differ (breaker 2: `main.py:327`,
`:348`; storage 1: `main.py:390`; Ctrl+C 0: `main.py:381-384`); the change is listed in the 3.0.0 changelog.

### 4.6 Signals, single instance, running as a service

- SIGINT/SIGTERM on a job command: request cancel; in-flight requests abort at the next cancel check, the
  writer finishes or drops the current event atomically, the job row becomes `cancelled`, the lease is
  released, the JSON result is still printed, exit 130/143. A second signal exits immediately; the row is
  reaped as `interrupted` later.
- SIGINT/SIGTERM on `watch`/`serve`: graceful stop (sources closed, sinks flushed up to 10 s, leases
  released), exit 0. SIGHUP: reload the config (follows, sinks, slice selection; not data dir or bind address).
- Windows: Ctrl+C and Ctrl+Break are handled; SIGHUP does not exist (best-effort platform).
- Single instance: leases from 2.8. Default is to fail fast with exit 6 and the holder in `error.details`;
  `--wait SECONDS` waits. Read-only commands (`status`, `events`, `export`, `describe`, `jobs list`) take no lease.
- Service operation: `docs/` ships two systemd units (`sofascore-serve.service`, `sofascore-watch.service`,
  `Restart=on-failure`, `KillSignal=SIGTERM`) and a timer example for `ssc sync`. The Docker entrypoint
  becomes `ssc serve --host 0.0.0.0` and passes any other arguments to `ssc`; `serve` never widens
  `allowed_hosts` on its own (today `main.py:251-257` sets `*`; the entrypoint avoids that path,
  `docker/entrypoint.sh:7-12`) and prints a warning when bound to a non-loopback address without a token.

### 4.7 Mapping from today's flags

`main.py` stays as a shim for one release: a known subcommand goes to the new CLI; legacy flags are
translated by `src/cli/legacy_flags.py`, which prints one deprecation line to stderr and runs the new command.

| Today (`main.py`) | New | Fate |
|---|---|---|
| `--version` (`:20-22`) | `ssc version`, `ssc --version` | kept permanently |
| no arguments → interactive menu (`:371-374`) | prints help, exit 2 | removed in 3.0.0 |
| `--web --host --port --dev` | `ssc serve …` | alias, removed in 3.1 |
| `--headless --update-all` (`:330-353`) | `ssc sync` | alias |
| `… --league-id N` (`:99-105`) | `ssc sync --follow <name of N>` or `ssc fetch tournament N --season all` | alias |
| `… --fetch-mode details` (`:92-97`) | `ssc sync --only events` | alias |
| `--headless --csv-export` (`:355-358`) | `ssc export --dataset events --format csv --profile legacy-wide-csv` | alias |
| `--refresh-only` (`:308-328`), `--refresh-legacy` (`:293-294`) | `ssc refresh [--include-legacy]` | alias |
| `--recheck-unavailable[=legacy\|all]` (`:298-306`) | `ssc data recheck-unavailable [--all]`; combined with a download: `ssc sync --recheck-unavailable` | alias |
| `--watch --sport --league-ids --event-ids --watch-hours` (`:205-230`) | `ssc watch --sport S --tournament … --event … --for 2h --stdout` | alias |
| `--doctor …` (`:30-33`) | `ssc doctor …` | alias; installers (`scripts/install.sh:144`) switch in the same PR |
| `--diagnostics [PATH]` (PR #24) | `ssc diagnostics [--out PATH]` | alias |
| `--ignore-rate-limit` (`:196-200`) | `--ignore-breaker` (it disables the breaker, not the rate limit) | alias |
| `--data-dir` (`:113-118`) | `--data-dir` | kept |
| `--config` (`:107-111`, leagues file; ignored today, see 1.7) | `--config` = the config file; a `.txt` path is accepted as a legacy leagues file with a warning | meaning changes |

Aliases use the new exit codes and the new output rules (decision D5).

---

## 5. Output sinks and event streams

### 5.1 Streams

Producers never call a sink. They append to a durable stream through `store.streams.append` and get a sequence
number; every consumer (SSE, `ssc events`, `watch --stdout`, webhooks, file sinks) reads the same log by
sequence number.

| Stream | Producer | Types |
|---|---|---|
| `live` | live service | `live.status_changed`, `live.score_changed`, `live.stuck`, `live.odds_changed` (later), `live.detail_updated` (optional) |
| `change` | pipeline (refresh), live confirmation | `change.recorded` (announces a change-log row; carries its `change_seq`) |
| `job` | job manager | `job.started`, `job.finished` (with state, counts, error code) |
| `system` | client health, live supervisor, dispatcher | `system.blocked`, `system.recovered`, `system.live_source_changed`, `system.sink_dropped` |

Envelope (schema `sofascore.event/1`):

```json
{"stream": "live", "seq": 1842, "type": "live.status_changed", "ts": "2026-10-01T18:52:04Z",
 "event_id": 17124861, "sport": "football", "tournament_id": 17, "source": "push",
 "data": {"from": "live", "to": "completed", "provisional": true, "change_ts": 1790856421, "score": {}}}
```

`seq` is one sequence across all streams of a data directory. It identifies a message for as long as the
stream id (`store.streams.head().stream_id`) stays the same; consumers de-duplicate on `seq`. Numbers increase
strictly but are not consecutive within a stream.

### 5.2 Sink interface

```python
class Sink(Protocol):
    name: str
    def accepts(self, env: Envelope) -> bool: ...              # compiled from the `events` globs and filters
    def deliver(self, batch: Sequence[Envelope]) -> None: ...  # raise RetryableSinkError / FatalSinkError
    def close(self) -> None: ...

class Dispatcher:
    def run(self, stop: StopToken) -> None: ...                # long-running hosts
    def drain(self, timeout: float) -> DrainReport: ...        # one-shot commands before exit
```

The dispatcher keeps one cursor per sink (`store.streams.cursor(sink)`). It reads after the cursor, delivers
in order, and advances the cursor only on success: at-least-once, ordered, resumable after restart. Exactly
one process dispatches at a time (the `sinks` lease): `serve` and `watch` hold it while they run; a one-shot
command tries it at exit, drains for up to 10 s, and otherwise leaves the backlog to the holder or to the next
run.

| Sink | Behaviour |
|---|---|
| `stdout` | NDJSON lines; no cursor (starts at "now" or `--after`); this is `watch --stdout` and `events --follow` |
| `file` | append NDJSON; rotation by size or day; cursor recovered from the last line of the newest file |
| database | the supported database output is the SQLite export in the public schema (`ssc export --format sqlite`); a PostgreSQL sink later implements `Sink` with an upsert on `seq`. `catalog.db` and `state.db` are internal files (decision S5) |
| `webhook` | below |
| message queue | later, same interface |

Sinks are configured only in the config file or environment, never through the HTTP API: with no accounts,
an API that registers outbound URLs would be an open relay (decision D11).

### 5.3 Webhook contract

- `POST` with `Content-Type: application/json`; body `{"schema":"sofascore.webhook/1","sink":"ops","events":[envelope,…]}`
  (batch size 1–100, default 20, flushed after at most 1 s).
- Headers: `X-Sofascore-Delivery` (UUID per attempt group), `X-Sofascore-Seq-First`,
  `X-Sofascore-Seq-Last`, `Idempotency-Key: <stream id>:<first>-<last>`,
  `X-Sofascore-Signature: t=<unix>,v1=<hex>` where `v1 = HMAC-SHA256(secret, "<t>." + body)`.
  A secret is required unless the sink sets `allow_unsigned = true` (decision D12).
- Success is any 2xx. 410 disables the sink until restart. Other responses and network errors are retried
  with exponential back-off and jitter (1 s → 5 min). Delivery does not skip ahead: the head batch blocks the
  sink. After `max_age` (default 24 h) the head batch is dropped, a `system.sink_dropped` event records the
  range, and delivery continues.
- Receivers de-duplicate on `seq`; replays after a crash are expected.
- `ssc status` shows per sink: cursor, newest sequence, lag, last error.

---

## 6. HTTP API v1 (resource level)

The field-level contract is a later task (plan item SC-1 for the schema, P21 for the routes). Fixed here:
prefix, resources, envelopes, error model, streaming, raw access, aliases.

- Prefix `/api/v1`. `/health` stays unversioned for load balancers and the launcher.
- Every route declares a response model. `docs/api/openapi-v1.json` is committed;
  `tests/test_openapi_snapshot.py` compares it with `app.openapi()` and fails on any undeclared change;
  `python -m src.web.openapi --write` regenerates it. Frontend types are generated from the file.
- Envelopes: single resource `{"data": {…}}`; collection
  `{"data": […], "page": {"limit": 50, "next_cursor": "…"}}` with cursor pagination.
- State-changing requests are never GET. The origin check (`web/app.py:37-47`) and host check
  (`web/app.py:51`) stay. With a token configured, every `/api/*` route requires
  `Authorization: Bearer <token>` (decision D13).

| Resource | Methods | Service |
|---|---|---|
| `/sports`, `/sports/{slug}` | GET | `QueryService.sports` (registry) |
| `/tournaments`, `/tournaments/{id}`, `/tournaments/{id}/seasons` | GET | query |
| `/tournaments/search?q=` | GET (calls SofaScore) | `FollowsService.search_tournaments` |
| `/seasons/{id}`, `/seasons/{id}/slices/{key}` | GET | query (standings etc.) |
| `/events?sport=&tournament=&season=&from=&to=&status=&participant=&has=` | GET | query |
| `/events/{id}`, `/events/{id}/slices`, `/events/{id}/slices/{key}`, `/events/{id}/odds` | GET | query |
| `/events/{id}/raw`, `/events/{id}/slices/{key}/raw` (also `?raw=1`) | GET | `QueryService.raw`: the stored SofaScore payload (same values and key order as the response; see `01-storage.md` 4.1), with `ETag` (the payload's sha256) and `X-Sofascore-Fetched-At` |
| `/changes?since=` | GET | query (change log by its own `seq`) |
| `/follows`, `/follows/{id}` | GET, POST, PATCH, DELETE | follows |
| `/jobs`, `/jobs/{id}`, `/jobs/{id}/cancel`, `/jobs/{id}/events` | GET, POST, POST, GET (SSE) | job manager |
| `/jobs` body `{kind, spec}` | POST | starts `sync`, `fetch`, `refresh`, `export`, `backup`, `clear`, `rebuild` |
| `/exports`, `/exports/{id}/download` | GET | export results |
| `/backups`, `/backups/{name}` | GET | backup |
| `/live/status`, `/live/events?after=&limit=` | GET | live (pull) |
| `/live/stream?after=&types=&sport=&tournament=&event=` | GET (SSE) | live (push) |
| `/settings` | GET, PATCH | settings; locked fields flagged |
| `/health`, `/status`, `/diagnostics`, `/diagnostics/bundle`, `/logs` | GET | status |

SSE (`/live/stream`, `/jobs/{id}/events`): each message has `id: <seq>`, `event: <type>`, `data: <envelope>`.
Resume with the standard `Last-Event-ID` header or `?after=`. A comment line is sent every 15 s. If `after`
is older than the retained range (`StreamBatch.gap`) the server sends one `stream.gap` event with `oldest_seq`
and closes; the client resynchronises through `/live/events` or a fresh list request.

Errors: `{"error": {"code": "job_running", "message": "…", "details": {…}, "request_id": "…"}}` with the
status from the table in 2.6. `message` is English; clients translate by `code`. Upstream trouble during a
request that calls SofaScore is 503 `blocked`/`rate_limited` or 502 `upstream_error`, never 429 (today
`routes/matches.py:416-417` answers 429).

### 6.1 Existing `/api` routes

They stay for one release as `src/web/api/legacy.py`: same paths, same response shapes (pinned by the
goldens of plan items G-02 and G-04), implemented on the same services, marked `deprecated` in OpenAPI and
answered with `Deprecation: true` and `Link: <…>; rel="successor-version"`. The web UI moves to v1 with the
frontend update.

| Today | v1 successor |
|---|---|
| `GET /api/leagues`, `POST /api/leagues`, `PATCH/DELETE /api/leagues/{id}` (`routes/leagues.py:112-150`) | `/follows` |
| `GET /api/leagues/search` (`:153`) | `/follows?q=` |
| `GET /api/leagues/search-remote` (`:167`) | `/tournaments/search` |
| `GET /api/leagues/{id}/seasons` (`:173`), `POST …/seasons/refresh` (`:190`) | `/tournaments/{id}/seasons`; `POST /jobs {kind:"sync", spec:{phases:["listing"]}}` |
| `GET /api/seasons/{sid}/matches` (`routes/matches.py:421`), `GET /api/matches` (`:435`) | `/events?season=` , `/events` |
| `GET /api/leagues/{id}/missing-details` (`:428`) | `/events?tournament=&has=missing` |
| `GET /api/matches/{id}` (`:462`) | `/events/{id}` + `/events/{id}/slices/{key}`; the legacy shape (dict of raw slices with the `basic` key) is built from the raw payloads |
| `POST /api/matches/{id}/fetch` (`:471`) | `POST /jobs {kind:"fetch", spec:{events:[id]}}`; the alias waits for the job |
| `POST /api/fetch` (`routes/scrape.py:87`) | `POST /jobs {kind:"sync"\|"fetch"}` |
| `GET /api/scrape/status` (`:32`), `GET /api/scrape/stream` (`:52`) | `/jobs?state=running`, `/jobs/{id}/events` |
| `POST /api/scrape/cancel` (`:78`) | `POST /jobs/{id}/cancel` |
| `GET /api/jobs`, `/api/jobs/{id}` (`:38-49`) | `/jobs`, `/jobs/{id}` |
| `GET /api/status` (`:107`), `GET /api/bypass/status` (`:117`), `POST /api/bypass/test` (`:133`) | `/status`; `POST /status/check` |
| `GET/POST /api/settings` (`routes/settings.py:101`, `:134`) | `GET/PATCH /settings` |
| `GET /api/dashboard`, `/api/stats/system` (`routes/data.py:59`, `:70`) | `/status` (summary and coverage) |
| `POST /api/data/backup`, `GET /api/data/backups/{name}`, `POST /api/data/clear` (`:234-276`) | `POST /jobs {kind:"backup"\|"clear"}`, `/backups/{name}` |
| `GET /api/export/csv` (`:282`) | `POST /jobs {kind:"export"}` + `/exports/{id}/download`; the alias streams the `legacy-wide-csv` profile and no longer writes a file into the data directory |
| `GET /api/sports` (`routes/sports.py:43`) | `/sports` |

---

## 7. Removing the terminal menu UI

### 7.1 Extract first

1. Headless orchestration (`SofaScoreUi.py:311-397` and the handler methods it calls) → `SyncService` (plan
   items P08, P10).
2. Fetcher wiring and directory creation (`SofaScoreUi.py:79-95`) → `services/context.py` (P08).
3. CSV export entry → `ExportService` legacy profile (P08, EX-1).
4. Restore (`settings_ui.py:459-549`) → `BackupService.restore`, for zip backups (ST-24). Without this the
   product loses restore when the menu is deleted.
5. Coverage report (`match_data_fetcher.py:2323-2482`) → `StatusService.coverage` (P15).
6. Tests that patch `SimpleSofaScoreUI` in the web job (three modules) → patch the service (P08).

Between the PR that starts writing the new layout (ST-21) and the removal of the menu (P26), the menu's list,
statistics, backup, restore and clear items see only the legacy trees. ST-21 adds a one-line notice to those
items; no release is cut in that window without it.

### 7.2 Deletion (plan item P26)

Deletes `src/ui/` and `src/SofaScoreUi.py`; removes `colorama` and `tqdm` from `requirements.txt` and from
`doctor.REQUIRED_MODULES` (`doctor.py:55-69`); removes `rich` if the logger no longer needs it; prunes locale
keys used only by menus (`locales/en.json`, `locales/tr.json`); updates README, installers
(`scripts/install.sh:157`) and launchers. `python main.py` without arguments prints the command list and
exits 2.

### 7.3 Replacement for everything a user could do there

| Menu item (`ui/cli_shell.py`) | Replacement |
|---|---|
| Leagues: list / add / reload / search | web UI Follows page; `ssc follows list\|add`; config file `[[follow]]` |
| Seasons: update all / one / list | `ssc sync --only listing`; `GET /tournaments/{id}/seasons`; web UI |
| Matches: fetch one league / all / list | `ssc fetch tournament ID --season …`; `ssc sync`; `ssc export`, `GET /events`, web UI |
| Match details: fetch by id / fetch all | `ssc fetch event ID…`; `ssc sync --only events` |
| Match details: convert to CSV (one, league, all) | `ssc export --format csv` with filters |
| File analysis report | `ssc status --coverage` |
| Statistics: system / league / report file | `ssc status --json`; web dashboard |
| Settings: API, data dir, display, language | config file, environment, web Settings page; `ssc config show` |
| Settings: backup / restore / clear | `ssc backup create\|restore`, `ssc data clear --yes`; web UI |
| About | `ssc version` |

---

## 8. Live: one supervised service

### 8.1 Shape

```
            ┌──────────────── LiveService (supervisor) ────────────────┐
 follows →  │ scope      PushSource ─┐                                 │
 (live=true)│            PollSource ─┼→ observations → Reducer → store.streams.append("live") → sinks, SSE, CLI
            │ arbiter (which source leads per sport)   │               │
            │ confirm/detail fetcher ──────────────────┴→ store.events.observe / put
            └──────────────────────────────────────────────────────────┘
```

- **Independent of download jobs.** It holds the `live` lease, never `writer`; it has its own request context
  (no job breaker), its own throttle lane (today `watcher.py:43-44`, `:170-172`), and writes only through
  `store.events.observe/put` and `store.streams.append`. A sync job and the live service can run at the same
  time in one process (`serve --live`) or in two (`serve` + `watch`); the Store's write protocol serialises
  their writes per entity, and an older observation never replaces a newer one.
- **Sources produce observations, not events.** An observation is `(event_id, partial or full event object,
  source, received_at)`.
  - `PollSource` is today's `MatchWatcher.tick` (`watcher.py:337-380`): the live list per sport every
    `poll_interval`, event pages for dropped, near-end and stuck events, with the per-sport rules from
    `sports.WatcherParams` (`sports.py:40-48`).
  - `PushSource` listens to the frames of the connection that SofaScore's own page opens (`sport.{sport}`
    and `event.{id}` subjects; `docs/all-sports/README.md`, "Push kanalı"). It opens no connection of its
    own, sends no subscription, and never reads or stores the connection's credentials. Frames carry changed
    fields as dotted paths; the source merges them into the last known event object. Page requests are
    routed through the shared throttle or aborted; images, media and fonts are blocked.
- **Reducer** is the pure core of `MatchWatcher._observe` (`watcher.py:232-307`):
  `(LiveState, Observation) → (LiveState, [LiveEvent])`. It emits `status_changed`, `score_changed`, `stuck`,
  sets `provisional` by the refresh window, and de-duplicates across sources with the stream's `dedup_key`
  `(event_id, type, change_ts or state hash)`, so a transition seen by push and by poll is stored once.
- **State** moves from `watch_state_{sport}.json` (`watcher.py:46`, `:204-214`) into `store.watch`, so a
  restart does not re-emit and one service covers all sports (today: one process per sport).
- **Confirmation.** A terminal status from push is emitted immediately, then confirmed by one `/event/{id}`
  request that stores the full payload. That stored event is what a later `sync` completes with post-match
  slices; the live service does not download details unless `live.detail_slices` asks for them.
- **Arbitration and fallback.** Per sport the arbiter tracks push health (connection open, last frame, last
  ping). Push healthy: polling drops to a slow safety interval. Push silent beyond the threshold or
  disconnected: polling returns to `poll_interval` (30 s, `watcher.py:33`). Every switch appends
  `system.live_source_changed`. Events in scope that push never mentions are covered by polling.
- **Supervision.** The supervisor restarts a crashed source with back-off, records heartbeats and counters
  for `LiveService.status()`, pauses and backs off when the client reports `blocked` (it never "trips and
  stops"), and reloads the scope when follows change or on SIGHUP.

### 8.2 Hosting

- `ssc watch`: foreground process for systemd or a container.
- `ssc serve --live` (or `[live] enabled` with serve): the same supervisor in a background thread with its
  own event loop.
- A second live service on the same data directory exits 6 (`instance_running`). Until the live service
  exists, the current watcher takes `watcher:<sport>` leases, so one process per sport keeps working.
- Browser profile: Chromium allows one process per profile (`doctor.py:71-72`, `docker/entrypoint.sh:17-39`).
  A push listener keeps a browser open permanently, so it uses its own profile directory by default
  (`<profile>-live`) to leave the bridge profile free for jobs in other processes (decision D10).

### 8.3 Order of delivery

Polling first (P23): supervisor, reducer, state in the Store, sequence-numbered events, SSE, `watch`,
sinks. Push second (P24), after the endurance results are in. Everything downstream of the reducer is
identical for both, so P24 adds one source and the arbiter.

---

## 9. PR sequence

The one ordered list for both tracks is `03-implementation-plan.md`. The PR ids of the parallel draft of this
document map to it as follows:

| Draft id | Plan item |
|---|---|
| P01 (design doc) | this pull request |
| P02 | G-01 |
| P03 | G-02 (data readers) and G-04 (OpenAPI snapshot, other routes) |
| P04 | G-03 |
| P05, P07, P08, P09, P10, P11 | same ids; P07 no longer moves the job store (ST-09 does); P11 builds on the Store's leases (ST-10) |
| P06 (storage port, `LegacyLayoutStore`) | dropped; the Store track extracts the storage code (ST-05 … ST-22) |
| P12 | ST-02 (predicates, outcome type) and P12 (need computation, `SliceSpec`) |
| P13, P14, P15 | same ids, after the writers moved to the Store (ST-21, ST-22) |
| P16 (query services) | RD-1 … RD-5: each reader moves once, into a service that reads the Store |
| P17 (backup, maintenance) | ST-19 (backup, clear) and ST-24 (format 2, restore) |
| P18 … P26 | same ids |
| P27 | ST-27 (all statuses, stale-listing refresh) and P27 (slice selection) |
| P28, P29, P30 | same ids |

## 10. Testing strategy

- A fake SofaScore transport (`tests/fakes/sofascore.py`) installed at the client boundary serves canned
  payloads, records every request and can inject 403/429/5xx/timeouts. All golden tests use it; none touches
  the network.
- Goldens before refactoring: request sequence and resulting store state for each flow (G-01), JSON of every
  current route plus the OpenAPI document (G-02, G-04), stdout/stderr/exit code of each current flag (G-03).
- The divergences in 1.4 are pinned as tests first, then flipped one by one in P13 with the change named in
  the PR text.
- Services are tested against a real Store in a temporary directory; there is no second Store implementation
  to keep in step.
- Cross-process tests use `subprocess`: lease contention (exit 6), cancel from another process, stale reaping.
- An import-linter test enforces the layer rules of 2.1.
- The coverage floor (`pyproject.toml`, `fail_under = 53`) applies to every PR; PRs that delete tested code
  must move its tests, not drop them.

---

## 11. What changed during reconciliation

Claims that did not hold against the code, and what was changed:

1. **Raw payloads are not the bytes on the wire.** The draft promised `get_raw` "bytes as received" and a
   `/raw` route returning "SofaScore's response verbatim". The application stores the parsed response
   serialised again (`src/fsutil.py:33-36`) and the Store keeps that. Sections 2.5 and 6 now say what "raw"
   is.
2. **Open PRs.** The draft named #23 and #24 and five files. #23 is stacked on #24, a third open PR (#32) is
   stacked on both, and together they also change `src/config_manager.py`, `src/web/app.py`,
   `src/web/routes/api.py`, `tests/conftest.py`, `src/doctor.py`, the installers and the CI workflow (1.7).
   More PRs of the plan have to wait for them than the draft assumed.
3. **Progress bars.** Five `tqdm` bars, not two (1.2).
4. **Breaker outcome.** Today an open breaker is a *failed* outcome with reason `breaker`
   (`match_data_fetcher.py:71`), not a separate status. The unified `Outcome` makes it `skipped`; noted in 2.4.
5. **Slice marks.** Marks are written only for `required` slices of finished events
   (`match_data_fetcher.py:684`, `:1228-1229`). Added to 1.4 and to the list of behaviour changes in 3.3.
6. **Line references.** Default rate constants are at `throttle.py:55-57`; `DetailSlice` starts at
   `sports.py:170`; directory creation is `SofaScoreUi.py:79-83`.

Changes that come from reconciling with the storage design:

7. **No second Store interface.** `services/ports.py` with `Store` and `EventLog` protocols and the
   `LegacyLayoutStore` extraction (draft P06) were dropped. Section 2.5 maps every need onto the Store API.
   Two of the draft's requirements changed the Store: the change comparison under the write lock
   (`on_event_change`) and "an older observation must not win" (`PutResult.superseded`).
8. **Jobs live in `state.db`**, not in an extended `jobs.db`; `src/jobs` holds no SQL. Leases are the Store's
   (`writer`, `live`, `sinks`, `maintenance`) instead of `data.lock` / `live.lock` / `sinks.lock` in
   `src/jobs/lease.py`.
9. **One sequence for all streams.** The draft had one sequence per stream and a cursor per sink and stream.
   There is now one sequence and one cursor per sink; the envelope is unchanged.
10. **Bridge health** is stored through `store.runtime`, not written by the client to `.meta/health.json`
    (only the Store touches `DATA_DIR`).
11. **Follows** have three origins; without a config file the legacy files stay authoritative (4.3).
12. **Backup and migrate.** Restore has no merge mode. `migrate` keeps the legacy copy unless
    `--delete-legacy` is given (the flag was `--delete-source`).
13. **Slice addressing** is key + sub (`standings` / `total`), not one key per variant (`standings_total`).
14. **"The catalog is a queryable database" sink** was replaced by the SQLite export; the catalog's schema is
    internal (decision S5).

---

## 12. Risks

- Open PRs #23, #24 and #32 edit files that P05, P08, P09, P18 and others need. Starting those before the
  three are merged guarantees large conflicts.
- Existing tests pin private names of the fetchers (for example `_LEGACY_SLICE_FETCHERS` at
  `match_data_fetcher.py:106-113`, patched in `tests/test_breaker_phases.py` and `tests/test_storage_errors.py`).
  P13 must port these tests; if they are simply deleted, coverage of breaker, storage-error and cancel
  behaviour is lost exactly where the code changes most.
- P13 changes request volume and timing: it removes the triple per-match retry and the fixed sleeps and
  fetches optional slices on every path. With today's default rate (100 req/s) this could raise burst load;
  P13 must not merge before the 5 req/s default (X-01).
- Cross-process leases rely on OS file locks. They work on local file systems and Docker volumes on one host;
  on NFS/SMB or across hosts they may silently not exclude. Windows and macOS are best-effort.
- Chromium allows one process per profile directory. A permanently open push-listening browser plus on-demand
  bridge launches from other processes will conflict unless the live service uses its own profile (D10); a
  second profile means a second challenge solve and extra memory.
- The push source depends on undocumented behaviour of SofaScore's page (subjects, frame format, whether
  `sport.{sport}` carries all events). It can change without notice; polling stays a complete fallback, but
  live latency would silently degrade from about 1 s to the poll interval.
- Moving CLI logs from stdout to stderr and changing exit codes (breaker 2 → 4, storage 1 → 5, Ctrl+C 0 → 130)
  breaks cron jobs and scripts that parse today's output or test for specific codes. It is a major release,
  but it needs a prominent changelog entry.
- Two configuration sources (declarative file and UI-edited overrides/follows) can confuse users: a value
  edited in the UI that is also pinned in the file will appear not to "stick". The UI must show the lock and
  its source.
- Webhook head-of-line blocking: a receiver that is down blocks its sink for up to `max_age` (24 h by
  default); a low `max_age` loses events. Operators need the lag shown in status.
- Legacy `/api` adapters must reproduce shapes that were by-products of pandas (NaN handling, date strings,
  column names from CSV). Without the goldens these would drift unnoticed and break the current web UI before
  it moves to v1.
- One file chain is long: `src/match_data_fetcher.py` has exactly one owning PR at a time through eleven PRs
  (`03-implementation-plan.md`). A stalled PR in that chain blocks everything behind it.
- asyncio pipeline with a writer thread: a Store write that blocks (the catalog mutex held by the live
  service, a slow disk) backs up the result queue; the queue is bounded, which in turn slows fetching.
