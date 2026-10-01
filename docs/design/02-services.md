# 02 — Service layer and the three faces

Status: design, reconciled with `01-storage.md` on 2026-10-01. Baseline: `origin/main` at `3ae2599`.
Every `file:line` reference below is to that commit, unless it is marked `0aa73b4`. Companion documents:
`00-platform.md` (the owner's platform design), `01-storage.md` (Store and catalog) and
`03-implementation-plan.md` (the one ordered PR list). Section 11 lists what changed in this document during
reconciliation and why.

Revised on 2026-10-01 after the first two implementation batches (PRs #33 to #47) and after the owner's
decisions on live watching: live data is a CLI service with sinks, not part of the web UI and not an HTTP
endpoint, and it has two selectable push sources (`page`, and `direct` as an explicit opt-in) next to polling.
Sections 2.2, 2.7, 4.1, 4.3, 5, 6 and 8 changed for that; section 11 lists every other correction. Main has
moved since the baseline (#23, #24, #32, #33, #43 and the first batches), so line numbers at `3ae2599` are a
starting point, not an address.

Revised again on 2026-10-02 after batches three and four (PRs #48 to #65). The client (P05), the service
context and `SyncService` (P08, P10), the follows mirror (ST-17), the configuration (P09), the throttle's
give-back (FX-6), the single-match route (FX-1), the error table and the first commands of the new CLI
(P18) are described as built, each as the current stage with what later items add: sections 1.3, 1.5 to
1.7, 2.1, 2.3, 2.4, 2.6 to 2.8, 3.4, 4.1 to 4.7, 5.1, 6, 7.1 and 8. References marked `f286723` are to
`origin/main` at that commit. Section 11 lists the corrections.

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

Since P10 (PR #64) one of the three is left in use. The web job and the headless runs of `main.py` both call
`SyncService`; the two CLI orchestrators (`run_headless_fetch`, `update_all_leagues` and `export_all_to_csv`
in `src/SofaScoreUi.py`) have no caller and go with the terminal UI (P26). The exit code of a breaker stop
comes from `SyncResult.breaker` in the service modes; `src/match_data_fetcher.py` still writes
`APP_EXIT_CODE` on a breaker stop (`:2110` at `f286723`, in the web server process too) and only the
interactive branch of `main.py` still reads it.

### 1.4 Where the two detail pipelines diverge

| Aspect | Async batch (`match_data_fetcher.py:192-505`) | Sync (`:985-1149`, `:1448-1533`) |
|---|---|---|
| Reached from | league/season plans: `fetch_job.py:332` → `fetch_detail_ids` (`:2013`) → `:493-504`; CLI headless via `:1886` | matches picked by id in the web UI: `fetch_job.py:299`; single-match route `routes/matches.py:396-418`; TUI `:2114`; and from inside the async batch for refill/refresh through `asyncio.to_thread` (`:355-357`) |
| Slices requested | every `slices_for(sport)` incl. optional ones (`:219`), so tennis gets `point_by_point` | `required_only=True` (`:1046`), so `point_by_point` is never fetched |
| Unfinished events | skipped only when `FETCH_ONLY_FINISHED` is on (`:211`) | always skipped, regardless of the setting (`:1038`, `:1002`) |
| `/event` failure | exception → up to 3 attempts per match with back-off (`:338-413`), each attempt already retried by the request layer (`max_retries=2`, `:200`) | swallowed into `None` (`:1075-1080`); no per-match retry; request-layer default retries |
| Slice retries | `max_retries=1` (`:257`) | request-layer default (`:1117`) |
| Concurrency | matches and slices concurrent (`:320`, `:225`) | strictly sequential |
| Pacing on top of the throttle | at `3ae2599`: 1 s between batches in two places (`:477-478`, `:2105-2107`; the inner one could not run through `fetch_detail_ids`, which hands over at most 100 matches per call). Since PR #33: none | at `3ae2599`: 0.2 s per match (`:1527-1528`); refresh-only 1 s (`:977-978`). Since PR #33: none. The row is no longer a divergence: the shared budget paces both paths (G-01 `test_row07_pacing`) |
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
| `routes/matches.py:396-418` | single-event fetch policy (refill, then full), error-to-status mapping by substring (`:416-417`). At `0aa73b4` the mapping was not reached for a blocked upstream: `_fetch_match_basic` swallowed the error, so the route answered 404 for a blocked `/event` and 200 when only the slices were blocked. FX-1 (PR #60) removed the substring mapping: the route keeps the outcome of its requests and answers the typed upstream error of `src/web/upstream.py` (6) |
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

All of this ended with P08 (PR #57): `src/web` imports neither `src.SofaScoreUi` nor `src.ui`, and
`tests/test_sync_service.py::test_web_imports_nothing_from_the_terminal_ui` keeps it so. Four test modules
patched the name by then (the fourth, `tests/test_web_hardening.py`, came with PR #43); they now replace
`fetch_job.build_context` and `export_all_csv`. The headless paths of `main.py` followed with P10 (PR #64);
the terminal menu keeps its own wiring in `src/SofaScoreUi.py` until P26.

### 1.7 Other findings that shape the design

- **Logs go to stdout.** `src/logger.py:39` installs `RichHandler` with the default console, whose stream is
  `sys.stdout`. `main.py:221` prints watch events to the same stream. Since PR #24 a plain stream handler
  with the file format is used when stdout is not a terminal; the stream is still stdout, so log lines, user
  text and the watcher's JSON event lines share it (pinned by the CLI goldens of G-03).
- **`--config` is a dead flag.** `ConfigManager` is a singleton that ignores arguments after first
  construction (`config_manager.py:46-67`); `src/utils.py:41` constructs it at import, which happens before
  `main.py:296` passes the path. G-03 pins it: a run with `--config` equals the plain full update. The
  `main.py` line numbers of this section and of 4.7 are from `3ae2599` and no longer exist; the behaviour
  they describe is unchanged. Since P10 the flag is dead explicitly: `main.py` constructs `ConfigManager()`
  at import, which the import of the terminal UI used to do as a side effect.
- **Settings are read in four ways**: module constants frozen at import (`utils.py:32-38`), `os.getenv` at
  call time (`refresh.py:27-51`, `config_manager.py:295`), `.env` rewriting, and direct `os.getenv` in routes
  (`routes/settings.py:121`). `FETCH_ONLY_FINISHED` changed in the UI therefore has no effect until restart
  (`utils.py:37`).
- **`API_BASE_URL` is applied inconsistently**: relative URLs honour it (`utils.py:291-292`,
  `match_fetcher.py:171`, `:208`, `:342`), four modules hard-code the absolute base
  (`match_data_fetcher.py:519`, `season_fetcher.py:35`, `watcher.py:32`, `routes/leagues.py:54`). P05 found
  more places: an unused attribute in `match_fetcher.py`, the bridge, which completes relative paths with a
  literal base and has a literal probe URL, and the default in `config_manager.py`. Since P05 (PR #48) and
  P08 (PR #57) every request of the fetchers, the watcher and the league search follows the setting, and
  the default lives in the Settings model (`src/config/settings.py:39` at `f286723`). What is left: the two
  literals in the bridge (`src/challenge_solver.py:43` and `:432` at `f286723`; P24. The transport hands the
  bridge full URLs, so they are not used for requests of the client) and the connection test of
  `src/web/routes/scrape.py` (`:164`), which calls the bridge directly with a relative path and so always
  uses the bridge's literal base (P21).
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
- **Defaults.** At `3ae2599` the default language was Turkish (`src/i18n.py:13`, `:36`) and the default
  request rate was 10 per second per concurrent request, 100 with default settings (`throttle.py:55-57`). Both
  changed since: the rate is 5 requests per second in total (PR #33, `src/throttle.py:54` at `0aa73b4`) and
  the language is English unless the system language is Turkish (PR #39, `src/language.py`).
- **Pull requests merged since the baseline.** #24 (`feat/log-files-diagnostics`), #23
  (`fix/blocked-state-ux`) and #32 (`chore/post-wave2`), which this design assumed, are merged. Together they
  changed `main.py`, `src/config_manager.py`, `src/logger.py`, `src/utils.py`, `src/season_fetcher.py`,
  `src/doctor.py`, `src/web/app.py`, four route modules (`api.py`, `leagues.py`, `scrape.py`, `settings.py`),
  `tests/conftest.py`, the installers, the CI workflow and the changelog, and added `src/diagnostics.py`,
  `src/redact.py`, `src/web/upstream.py` and the routes of `src/web/routes/diagnostics.py`. PR #43 (web
  security hardening) followed; what it changed is in `03-implementation-plan.md` section 1.
- **Settings since P09 (PR #56).** `ConfigManager`'s getters read the active `Settings` (4.3). The module
  constants frozen at import and the modules that read `os.environ` themselves are unchanged; they are
  reached through the environment bridge of 4.3, so `FETCH_ONLY_FINISHED` changed in the UI still needs a
  restart.

---

## 2. Target architecture

### 2.1 Layer rules (enforced by an import-linter test)

1. Faces (`src/cli`, `src/web`, `src/api.py`) import only `src/services`, `src/jobs`, `src/config`, `src/errors`.
   They contain argument parsing, serialisation and nothing else.
   As built so far (P18, PR #65) the new CLI also imports `src.doctor`, `src.diagnostics`, `src.sports`,
   `src.logger` and `src.redact`, and for `config init --from-legacy` `src.config_manager` and
   `src.web.league_sports`: no service exists for these yet. The status and follows services replace them
   (P19, P21, P26). Command modules keep their module-level imports light; a test allows only `src.cli.*`,
   `src.errors`, `src.exceptions`, `src.language`, `src.sports` and `src.version` at import time.
2. Only `src/client` sends requests to SofaScore. Only `src/store` touches `DATA_DIR`, including every SQLite
   file and every lock file in it.
3. Services never print, never read `os.environ`, never call `sys.exit`. They take a `ServiceContext`,
   return typed results and raise `PlatformError` subclasses.
   As built by P08 the services do not print, with three leftovers of the transition: `build_context`
   writes `NO_COLOR` into `os.environ` when `USE_COLOR` is off (carried over from the terminal UI's
   constructor; it goes with the terminal progress bar, P14 and P15), `SyncService` reads one job-log text
   from the locale files (`fetch_zero_matches`, as today; codes replace it in P11), and the storage error
   is `src.exceptions.StorageError` until `src/errors.py` arrives with P18.
4. Domain modules (`sports`, `status`, `refresh`, `slices`, `schema`) are pure and import nothing above them.
5. `src/client` and `src/store` do not import each other. `src/jobs` uses `src/store` for persistence and
   leases and contains no SQL and no access to the data directory. The test enforces what imports can show:
   no face module and no `sqlite3` in `src/jobs` (`tests/test_jobs_model.py`); the manager may use `os` for
   the pid.

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
    live/                   supervisor.py, reducer.py, poll_source.py, push_source.py (page listening, frame
                            parser and merge), direct_source.py (opt-in), arbiter.py
    export.py               ExportService
    backup.py               BackupService
    maintenance.py          MaintenanceService
    status.py               StatusService (health, summary, coverage); doctor stays src/doctor.py
    follows.py              FollowsService
    query.py tournaments.py QueryService (read side), incl. the legacy response shapes
  jobs/
    model.py                Job, JobKind, JobState, Origin, ErrorInfo (P07); JobEvent (P11)
    manager.py              JobManager, JobHandle
    progress.py             JobProgress               (from web/progress.py)
    scheduler.py            optional in-app scheduler
  sinks/
    base.py dispatcher.py stdout.py file.py webhook.py
  cli/
    main.py output.py exit_codes.py signals.py legacy_flags.py commands/*.py
  web/
    app.py errors.py deps.py sse.py (job events only) security.py (PR #43)
    api/v1/*.py             one router per resource; no live router
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
adapter that reads from the active `Settings`, so existing call sites keep working. P09 (PR #56) did that:
every getter reads `loader.active()`, and `ConfigManager.get_settings()` returns the active `Settings`.

The block above is the target. The current stage, as built by P08 (PR #57) and ST-17 (PR #62;
`src/services/context.py` at `f286723`):

```python
@dataclass(frozen=True)
class ServiceContext:
    config: ConfigManager                 # leagues, thresholds, data directory
    data_dir: str
    season_fetcher: SeasonFetcher
    match_fetcher: MatchFetcher
    match_data_fetcher: MatchDataFetcher  # details, refresh and the CSV flattening

def build_context(config_manager: ConfigManager, *, data_dir: str | None = None) -> ServiceContext: ...
    # creates the data directory and its four subdirectories (seasons, matches, match_details, datasets),
    # brings the follows table up to date and builds the three fetchers; raises OSError when a directory
    # cannot be created
```

- It carries the fetchers, because today's flow is theirs. There is no `settings`, `store`, `client`,
  `jobs` or `clock` field, no `readonly` argument, no follows and no health wiring: none of those existed
  when it was written, and opening the Store in a web job would have created `.meta` files, which is not
  behaviour-preserving.
- A context belongs to one job or one request: the fetchers carry state (the job cache, the last request
  counts). `build_context` is called once per job and once per season-refresh or CSV-export request.
- Follows (ST-17, PR #62). `build_context` applies the `[[follow]]` entries of the config file with origin
  `config` and then mirrors the league files, right after the data directories are created. It does not
  open the Store for that and the context has no `store` field: it calls `apply_follows(data_dir, ...)`,
  which updates the table over a short-lived connection (`01-storage.md` 2.3). `ConfigManager` does the same
  for the league files after every load and change.
- What the later items add. P11: the Store, the client with `on_health_change` wired to
  `store.runtime.set("bridge_health", ...)` (and `client.close()` when a context is replaced) and the
  `JobManager`; `apply_follows` then becomes `store.follows.apply`. P13: the pipeline uses the context's
  client. P15: the fetchers leave the context, and `settings` takes the place of `config`. `readonly` and the
  `Platform` class come with the library face.
- Tests fake the context with a `SimpleNamespace` that has `config`, `season_fetcher`, `match_fetcher` and
  `match_data_fetcher` (`test_breaker_phases`, `test_job_progress`, `test_storage_errors`,
  `test_sync_service`), so a new field that a service reads has to be added to those fakes.
- The web imports the fetchers and the request layer inside functions. A module-level import of
  `src.services.context` in a route module would load `curl_cffi`, `rich`, `tqdm` and the three fetchers at
  web start-up; `routes/data.py` and `routes/leagues.py` keep the import at function level.

### 2.4 Client

```python
# src/slices.py — one outcome type for client, pipeline and Store (01-storage.md 2.3)
class Outcome:                 # generalises SliceOutcome (match_data_fetcher.py:65-89)
    status: Literal["ok", "empty", "failed", "skipped"]
    data: Any | None
    reason: str | None       # failed: 403|429|5xx|timeout|network|parse|other
                             # empty: 404|empty   skipped: not_selected|not_applicable|not_due|unavailable|breaker|cancelled
    http_status: int | None
    fetched_at: datetime | None    # None means "now" to the consumer; it is not filled at construction
    via: Literal["curl", "bridge"] | None
    meta: Mapping[str, Any] | None

@dataclass(frozen=True)
class ClientSettings:               # src/client (P05); see "As built" below for its relation to 4.3
    base_url: str = endpoints.DEFAULT_BASE_URL
    retries: int | None = None              # None: the MAX_RETRIES setting, read per request
    timeout_seconds: float | None = None    # None: the REQUEST_TIMEOUT setting, read per request

class Client:
    def __init__(self, settings: ClientSettings | None = None, *, health: HealthSource | None = None,
                 on_health_change: Callable[[BridgeHealthSnapshot], None] | None = None,
                 clock: Callable[[], datetime] = _utc_now): ...
    def url(self, path: str) -> str: ...                         # the one place where the base URL is added
    async def get(self, path: str, *, retries: int | None = None, timeout: float | None = None) -> Outcome: ...
        # never raises for upstream conditions
    def get_sync(self, path: str, *, retries: int | None = None, timeout: float | None = None) -> Outcome: ...
        # for doctor/status and one-off callers; opens no session
    def health(self) -> BridgeHealthSnapshot: ...
    async def aclose(self) -> None: ...     # closes the running event loop's session; the client stays usable
    def close(self) -> None: ...            # detaches the on_health_change callback
    # `async with client:` closes the loop's session at the end of the block

@contextmanager
def request_context(*, cancel: Callable[[], bool] | None, on_wait: Callable[[str, float], None] | None,
                    breaker: CircuitBreaker | None) -> Iterator[RequestContext]: ...
```

The block is the client as built by P05 (PR #48, `src/client/__init__.py` at `f286723`). The first version of
this section gave `Client` a `throttle` argument, `get` a `lane` argument and one `aclose()`, and named
`ClientSettings` without defining it; "As built" at the end of the section says why each differs.

Semantics:
- `get` returns an `Outcome`; the only exceptions it raises are `Cancelled` (today `FetchCancelled`,
  `utils.py:50-55`) and programming errors. The mapping 404 → `empty/404`, breaker open → `skipped/breaker`,
  everything else → `failed/<kind>` is today's `SliceOutcome.from_error` plus `breaker.failure_kind`
  (`breaker.py:70-88`). The client returns `skipped/breaker` for an open breaker since P05. The fetchers do
  not: `Outcome.from_error` and `MatchDataFetcher` still report the open breaker as a *failed* outcome with
  reason `breaker` (`match_data_fetcher.py:71`, `:693-694`), and `_update_slice_markers` relies on the failed
  form, so that switch and that check change together in P13. Today's `from_error` treats only
  `ResourceNotFoundError` as a definitive empty: an `APIError` with status 404 of another class becomes
  `failed/404` (pinned in `tests/test_slices.py`); the transport raises `ResourceNotFoundError` for every
  404, so the client's answer to a 404 is `empty/404`. `Outcome` exists since ST-02 (PR #36) in
  `src/slices.py`; `fetched_at`, `via` and `meta` are left None by `from_error` and filled by the client.
- A 200 whose body is empty (`{}`, `[]`) is `empty` with reason `empty` and the body in `data`. Whether a
  non-empty body carries data for its slice stays with the predicates of `src/slices.py`. A 200 whose body
  is JSON `null` is `failed/other`: it is the reachable form of "the request layer returned None without an
  exception" (the line the first version cited, `utils.py:624`, was `:629` on main and cannot be reached
  with at least one retry).
- Two request bodies remain. The first version said "one transport implementation (async); `get_sync`
  drives it on the bridge's background loop; the duplicated sync body is deleted". P05 moved the sync body
  to `src/client/transport.py` instead: the G-01 goldens pin sync requests as such, and five callers still
  use `make_api_request`. `get_sync` uses that body and opens no session; every request is its own
  connection. The sync body can go when its callers have moved to the client (the fetch paths in P13, the
  watcher's default fetch in P23).
- A curl session belongs to the event loop that opened it, and every job runs its own `asyncio.run`. The
  client therefore keeps one session per loop, opened (with the warm-up request) by the first `get` on that
  loop. `aclose()` closes the running loop's session only; a job opens and closes its session with
  `async with client:` inside its loop. With an open breaker or a cancelled job no session is opened: the
  warm-up is a request too.
- Paths are relative to the API root and start with `/` (`ValueError` otherwise); `endpoints.py` builds them
  (`event_slice(key, id)` reads the path from the slice table of `src/sports.py` and raises `KeyError` for an
  unknown key), and the base is added only in `Client.url`. `ClientSettings` rejects a base that is not
  http(s). `ClientSettings.from_environment()` takes the base from `transport.base_url()`, which is read
  once at import: a base URL changed on the Settings page still needs a restart.
- The throttle stays where it is: every request reserves a slot in the shared budget (`src/throttle.py`; the
  curl path in `src/client/transport.py`, the bridge in `challenge_solver.py`). The default rate is 5 req/s
  since PR #33; `0`/`off` removes the limit. After idle time up to one second of budget goes out at once;
  that stays (decision D14, settled). Since FX-6 (PR #58) a reservation can be given back:
  `throttle.reserve()` returns a `Reservation` (a float subclass whose value is the seconds to wait), and a
  wait that ends without a request (the job was cancelled, an asyncio cancellation, Ctrl+C) returns its slot
  through `throttle.give_back_if_interrupted(delay)`. A slot whose time has already come is not given back:
  if it were, a stopped job's remaining queued requests would be sent with zero wait. The state file
  `<lane>.json` carries an optional `free` list for returned slots; a file without it is read as before.
  `give_back` never raises: when the state file cannot be locked the slot is not returned and one warning
  is logged. Measured offline at 5 requests per second: after a stop the next request waited 12.95 s before
  and 0.15 s after with `MAX_CONCURRENT=10`, 68.94 s and 0.13 s with `MAX_CONCURRENT=50`; a stopped job with
  the default settings used to leave 65 reservations.
- A request that waits for its slot inside the browser bridge stops within 0.25 s when its job is cancelled,
  on the sync path as well (FX-6), and raises `FetchCancelled`. A caller that waits for a shared challenge
  solve (up to 90 s) or for the page's `fetch()` (up to 20 s) still cannot be interrupted on the sync path,
  and with the budget off or not the limit, requests that already wait for the request semaphore are still
  sent after a stop (`03-implementation-plan.md` section 15).
- The client writes nothing under `DATA_DIR`. It reports each bridge-health transition through
  `on_health_change`; `build_context` will store the snapshot with `store.runtime.set("bridge_health", ...)`,
  so another process (`ssc status`, a second server) can read the last known state. That wiring is P11's:
  the context has no Store yet (2.3).

As built (P05), where the first version of this section differed:

- **No `throttle` and no `lane` argument.** The request bodies are module-level functions that read
  `src.throttle` themselves; an argument that changes nothing would mislead. The watcher still does its own
  wait on its throttle lane.
- **`aclose()` and `close()`** are two things, as above.
- **`ClientSettings` exists twice.** P05 defined the class above in `src/client`; proxy, the post-request
  wait, `max_concurrent` and the budget are still read by the request layer from `ConfigManager` and
  `src/throttle.py` per request. P09's model has a `ClientSettings` section class with the same three field
  names and the rest of 4.3; a test builds P05's class from the model (`base_url=effective_base_url`,
  `retries`, `timeout_seconds`). The next owner of the client takes the model's class or keeps building from
  it.
- **Old entry points stay.** `make_api_request` and `make_api_request_async` live in
  `src/client/transport.py`; `src/utils.py` forwards to it through a module class (`_UtilsModule`), so that
  existing patches on `src.utils` keep reaching the moved code. The forwarding is load-bearing: without it
  the suite sleeps through real back-offs. It goes when no test patches `src.utils` (P30). The moved code
  still logs under the logger name `Utils`, so no log line changed.
- `request_context(cancel=..., on_wait=..., breaker=...)` restores the previous values on exit. The web job
  uses it since P08; before, the cancel check stayed set after the job.

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

As built. `src/errors.py` exists since P18 (PR #65) and holds this table as `ERROR_TABLE`: per code the
class, the exit code and the HTTP statuses; `describe errors` prints it and a test compares it with the
table above, row by row.

- `PlatformError(code, message, details=None)` is the base; its subclasses (`UsageError`, `NotFoundError`,
  `ConflictError`, `InstanceRunningError`, `UpstreamError`, `UpstreamBlockedError`, `NotSupportedError`,
  `Cancelled`) take `(message, details=None, *, code=None)`, because the class fixes the code.
- `ConfigError`, `StorageError` and `JobRunningError` in the table are the classes that already existed
  (`src/exceptions.py`, the job store); they are not `PlatformError` subclasses. `to_platform_error()` maps
  them and the Store's `LeaseHeld`, `FollowExists` and `FollowManaged`, Ctrl+C becomes `cancelled`, and
  anything else `internal`. `data_operation_running` therefore has two classes in the code: `ConflictError`
  and the job store's `DataOperationRunningError`.
- `errors.lease_error_code(name, purpose)` gives `job_running`, `data_operation_running` or
  `instance_running` for a held lease, with the holder in `details`. The job store of the web (ST-10) still
  uses its own `conflict_from_lease`, which has no `instance_running`: a `live` or `watcher:<sport>` holder
  that blocks a data operation is reported there as `data_operation_running`, until the job manager takes
  over (P11). `LeaseHeld` is a `StorageError`, so a caller that catches `StorageError` catches `LeaseHeld`
  first, as `main.py` does since P10.
- `message` is meant to be English. The messages of `StorageError` and of the Store's errors are Turkish in
  the code. The mapping builds an English message from the OS reason and the path when they exist and passes
  the Store's text through otherwise.
- The module imports only the standard library and `src/exceptions.py`, so `version` and `doctor` run
  before the packages are installed; it recognises the Store's classes through `sys.modules`.

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

The block above is the target. The current stage, as built by P08 (PR #57, `src/services/sync.py` at
`f286723`), carries today's web flow unchanged (season lists, schedules, details, CSV):

```python
@dataclass(frozen=True)
class SyncSelection:                 # a targeted selection: one league and its seasons or matches
    league_id: int
    season_ids: tuple[int, ...] = () # read in mode "full" only
    match_ids: tuple[int, ...] = ()  # read in mode "details" only

@dataclass(frozen=True)
class SyncSpec:
    mode: Literal["full", "details"] = "full"
    league_id: int | None = None     # one league; None = every configured league; not read with selections
    selections: tuple[SyncSelection, ...] = ()
    job_phases: tuple[str, ...]      # property: the JobProgress phases of this run; not the `phases` field above

class SyncService:
    def __init__(self, ctx: ServiceContext): ...
    def run(self, spec: SyncSpec, *, handle: JobHandle | None = None) -> SyncResult: ...
        # raises StorageError (fatal); never raises for upstream trouble. Without a handle it runs detached:
        # no job record, job-log lines go to the logger, it cannot be cancelled

@dataclass(frozen=True)
class SyncResult:
    state: Literal["succeeded", "partial", "cancelled"]   # the rule of 2.8; "failed" is never returned
    schedule_empty_seasons: int      # seasons whose schedule was empty or could not be fetched (mode "full")
    breaker: str | None              # "403" | "429" | "5xx" | "other" when the breaker stopped the run
    progress: Mapping[str, Any]      # JobProgress.result(): detail counters, failed matches, refresh counts
```

- The spec is today's request, not targets. Today's flow cannot be written as `targets` without changing
  behaviour: full mode reads only the `season_ids` and details mode only the `match_ids` of the same
  selection, and the first job-log line counts selections.
- `plan()` and `fetch_events()` do not exist: today's flow has no plan step. They come with the planner and
  the pipeline (P12, P13), which also replace `mode` by `targets` and `phases`, and `counts`, `failed`,
  `stopped` and `duration_seconds` replace the fields above.
- `ExportService` and `ExportSpec` do not exist. P08 added one function, `export_all_csv(ctx)` in
  `src/services/export.py`, which returns the path of the file or None. It swallows and logs every error,
  as the terminal menu's CSV step did, so an export error does not fail the job that called it: a full disk
  during the export phase ends a web job as Completed without a CSV (pinned in
  `tests/test_sync_service.py`). The class and the typed result come with EX-1, which also removes the
  export phase from `SyncService` (decision D9).
- The service installs the request context itself (`cancel=handle.cancelled`,
  `on_wait=handle.progress.wait`, one breaker) and takes it back when the run ends.
- P10 (PR #64) added what the headless runs of `main.py` need. `SyncSpec.mode` has a third value,
  `"refresh"` (only the stored provisional records; `selections` and `export` are not read), with the call
  order of the old inline code (`begin_job_cache`, `refresh_due_ids`, `refresh_matches`, `end_job_cache`).
  `SyncSpec.export: bool = True` says whether the CSV phase runs; the web passes the default and the CLI
  passes False, because `--csv-export` is its own step. `SyncResult.refresh` is a
  `RefreshCounts(due, refreshed, changed, failed, skipped)` in refresh mode and None otherwise; the state is
  `partial` when a refresh failed. There is no `RefreshService` and no `phases` field yet, and
  `--refresh-legacy` is still the `REFRESH_LEGACY` environment variable that `main.py` sets.
- `src/services/maintenance.py` exists since P10 with one method,
  `MaintenanceService(ctx).recheck_unavailable(league_id=None, *, include_confirmed=False) -> ResetCounts`
  (`matches`, `slices`, `scanned`), a typed wrapper of `MatchDataFetcher.reset_unavailable_markers`. It takes
  a league id, because there is no `Scope` type yet; `clear`, `migrate` and `rebuild_catalog` come with
  their items (ST-19, ST-23).
- The services take no lease and raise no `JobRunningError`: the caller holds the lease. `main.py` takes it
  with `open_store(data_dir).lease(...)` and `LeaseHeld` reaches it unchanged (2.8, 4.6). The job manager
  takes that over (P11).
- `SyncService` does not count a season list that could not be fetched: a run in which every request is
  refused ends `succeeded` (`03-implementation-plan.md` section 15). After a breaker stop the batch fetcher
  returns for the matches in flight without reporting them as failed and still advances the progress to the
  total, so the progress says that every detail is done and none failed.
- `SyncResult.state` already follows the terminal-state rule (`partial` for a breaker stop or a failed
  match). The web adapter (`src/web/fetch_job.py`) still writes Completed with the card text; P11 stores the
  state.

```python
# services/refresh.py
class RefreshService:
    def due(self, scope: Scope | None = None, *, include_legacy: bool = False) -> list[int]: ...
    def run(self, scope: Scope | None = None, *, include_legacy: bool = False, handle=None) -> SyncResult: ...
        # = SyncService.run(SyncSpec(phases={"refresh"})); kept as its own entry point because cron uses it alone

# services/live/supervisor.py
class LiveService:
    def run(self, scope: LiveScope | None = None, *, source: Literal["page", "direct", "poll"] = "page",
            until: float | None = None, stop: StopToken | None = None) -> LiveSummary: ...
        # foreground only (`ssc watch`, or a library caller's own thread); there is no hosting inside `serve`.
        # "direct" is never chosen by the service itself: only an explicit argument selects it (section 8.3)
    def status(self) -> LiveStatus: ...          # lease held or not, leading source per sport, last heartbeat
    def events(self, after: int = 0, *, follow: bool = False, flt: StreamFilter | None = None) -> Iterator[LiveEvent]: ...
        # reads the stream log; works without a running service. Used by `ssc events` and the library, not by HTTP

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
    id: str                    # a plain string; uuid4 today (src/store/jobs.py:278 at 0aa73b4), sortable from P11 on
    kind: JobKind; state: JobState
    origin: Origin             # face: cli|api|scheduler|library, pid, host (pure data; the caller fills pid and host)
    spec: Mapping[str, Any]    # the service spec, JSON
    progress: Mapping[str, Any] | None      # JobProgress.detail()
    result: Mapping[str, Any] | None
    error: ErrorInfo | None    # code from 2.6 + message + details
    created_at: str | None; started_at: str | None; finished_at: str | None   # ISO-8601 UTC, as in today's rows
    heartbeat_at: int | None   # epoch milliseconds
    cancel_requested: bool

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

`JobManager` does not exist yet. Until P11, `src/services/sync.py` declares `JobHandle` as a Protocol with
`id`, `progress`, `cancelled()`, `log(message)` (no fields) and one more method, `publish(fields)`: the flow
writes `schedule_empty_seasons` to the job record in the middle of a run and `JobProgress` does not carry it.
`progress` must be built with `SyncSpec.job_phases`. The web adapter implements the Protocol on the job
store; P11's handle takes its place.

Mechanics:

- **Storage.** The `jobs` and `job_events` tables of `DATA_DIR/.meta/state.db`, reached through `store.jobs`
  (`01-storage.md` 3.3). Today's columns are kept, so old rows stay readable; `.meta/jobs.db` is imported once
  and left in place. State names are normalised on read: a completed row whose `circuit_breaker_triggered`
  column is set becomes `partial` (the column, not the text, because the text is localised). `created_at` has
  no column until migration 0002 (P11); rows written before it read `started_at`. `Origin` and `ErrorInfo`
  are defined in `src/jobs/model.py` (P07, PR #35); `JobEvent` comes with P11.
- **Leases** are the Store's (`01-storage.md` 6.1), taken with `store.lease(name)`:

  | Job kind | Lease |
  |---|---|
  | sync, fetch, refresh | `writer` (purpose `job`; the headless runs of `main.py` use `headless` and `refresh`) |
  | reset of the "unavailable" markers (`--recheck-unavailable`) | `writer` (purpose `recheck-unavailable`): it rewrites marker files of stored matches |
  | backup | `writer`, as a data operation (purpose `op:backup`), so a refused request gets `data_operation_running` |
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
  The sweep is still there (ST-10 kept it): a second web server that starts on the same directory marks the
  first one's running job row `interrupted` until that job's next progress update rewrites it. The leases
  that make the right check possible exist since ST-10: the row says running and
  `store.lease_holder("writer")` is None.
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
- No sleeps outside the client. The four fixed pauses (`:477-478`, `:977-978`, `:1527-1528`, `:2105-2107` at
  `3ae2599`) were already removed by PR #33; the shared throttle is the only governor, and the pipeline keeps
  it that way.
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
- Throttle: the shared budget of 2.4, `[client] rate`; `concurrency` only bounds in-flight requests. Since
  FX-6 a request that is cancelled while it waits for its slot gives the slot back, so a stopped job no
  longer delays the next one. The pipeline wraps every wait for a slot in
  `throttle.give_back_if_interrupted(delay)`, also around an `await`.
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
| `ssc watch` | run the live service in the foreground; the only way to run it | `--sport`, `--tournament ID...`, `--event ID...`, `--for DURATION`, `--source page\|direct\|poll` (default `page`; `direct` is an explicit opt-in with risks, section 8.3), `--stdout` (NDJSON events) |
| `ssc events` | read the stream log (no service needed) | `--stream live\|job\|change\|system`, `--after SEQ`, `--follow`, `--type`, `--event`, `--limit` |
| `ssc export` | write a dataset in an open format | `--dataset`, `--format jsonl\|csv\|parquet\|sqlite\|json`, `--schema normalized\|raw`, `--out PATH\|-`, filters `--sport --tournament --season --from --to --status` |
| `ssc serve` | HTTP API and web UI (no live service) | `--host`, `--port`, `--allowed-hosts`, `--allow-any-host`, `--scheduler`, `--dev` |
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

As built so far (P18, PR #65; `src/cli/` at `f286723`). The entry point is `python -m src.cli.main <command>`,
or `ssc <command>` after `pip install -e .`; only an editable install is supported, because the version, the
locale files and the web UI build are read from the project folder. `main.py` is untouched until P19. The
commands that exist are `version`, `doctor`, `describe`, `config show|validate|init|path` and `diagnostics`.

- A command registers itself with `@command("jobs list", help=<locale key>, configure=fn, settings=True)`
  (and `group("jobs", help=...)` for a parent) and returns a `CommandResult(data, text, exit_code, warnings,
  notes)`; it raises a `PlatformError` or lets `LeaseHeld`, `StorageError` and `ConfigError` through, which
  are mapped. `settings=True` loads the settings and moves the log lines to stderr before the command runs.
- `version` prints the application version and two schema versions, the CLI envelope's and the config
  file's. The Store does not export its layout, catalog and state versions from its root yet.
- `doctor` reads `.env` and the environment, as `main.py --doctor` does, not the config file. `ssc doctor`
  passes the effective request rate (config file and `--rate` included) to a new check, `budget`, which
  warns when the budget is above the default or off. That check runs only in the new CLI
  (`doctor.EXTRA_CHECKS`), because the CLI goldens pin the check list of `main.py --doctor`. A broken config
  file shows as a warning, not as a failed check.
- `describe slices` shows today's registry (`DetailSlice`: key, path, sports, `default_enabled`, and
  `required` as `counts_for_completeness`); `owner` is always `event`. The `SliceSpec` fields come with P12
  and P27. `describe` has no section for the watch sources yet; the choices of `[live] source` and the
  warning of `direct` appear through `describe config`.
- `--version`, `version`, `--help` and `doctor` run with only the standard library. A command that needs a
  missing package ends with `internal` and a pointer to `doctor`.
- The `--help` description names the sports of the registry.

### 4.2 Global flags

`--config PATH`, `--data-dir PATH`, `--json` (same as `--output json`), `--output text|json|ndjson`,
`--quiet`, `--verbose`, `--log-level`, `--log-format text|json`, `--no-color`, `--lang en|tr`,
`--rate N|off`, `--ignore-breaker`, `--wait SECONDS` (wait for a lease instead of exiting 6),
`--progress none|text|ndjson` (stderr), `--version`.

As built (P18): all of these except `--log-format`, `--wait` and `--progress`, which nothing could honour
yet (`[log] format` has no consumer, and no command of P18 takes a lease or runs a job); P19 adds them. The
flags are accepted before or after the command. `main.py` has no `--wait` either: a refused lease fails at
once.

### 4.3 Configuration file

One declarative file, `sofascore.toml` (decision D3), found in this order: `--config`, `SOFASCORE_CONFIG`,
`./sofascore.toml`, `CONFIG_DIR/sofascore.toml`. The application never writes it. A file that is named
explicitly and does not exist is an error. `SOFASCORE_CONFIG=none` turns the search off, so that no file is
read; the test suite sets it, and it helps when a stray file is suspected.

This section describes the model and the loader as built by P09 (PR #56; `src/config/settings.py`,
`loader.py` and `schema.py` at `f286723`). The file is honoured since that pull request: it is read when
`src.config_manager` is imported, once per process. `main.py` does not hand its `--config` to the loader yet
(it is still the dead leagues-file flag of 1.7); an explicit file reaches the loader through
`loader.activate(config_file=..., flags=...)`, which the new CLI calls since P18. TOML is read with
`tomllib`, on Python 3.10 with the `tomli` backport, which is imported only when a file is read.

Both entry points change to the project folder at start, so `./sofascore.toml` is the file in the project
folder, not in the directory the command was run in; `.env`, `config/` and `data/` are the same for both.
The new CLI resolves a relative `--config`, `--data-dir` and `--out` against the directory the command was
run in. `loader.load_settings()` sees only the `.env` values that are already in the process environment (it
expects python-dotenv to have loaded the file); commands that must not touch the process (`config validate`,
`doctor`, `config init`) build that view themselves (`read_settings()` in `src/cli/commands`), and any later
caller of `load_settings` in a fresh process needs the same.

The sample shows the scalar keys with their defaults; `use_proxy` and `proxy_env`, which it names only in a
comment, are described below:

```toml
schema = 1

[storage]
data_dir = "data"
durability = "normal"     # normal | full: "full" fsyncs every file and directory the Store writes

[client]
base_url = "https://www.sofascore.com/api/v1"
rate = 5                  # requests per second across all processes; 0 or "off" removes the limit
max_concurrent = 10
timeout_seconds = 10
retries = 3
wait_time_min = 0.2       # the pause between two requests of one worker, in seconds
wait_time_max = 0.5
proxy = ""                # or proxy_env = "PROXY_URL"; giving either one switches the proxy on (see below)
odds_provider = 1
browser_profile = ""      # empty = the default profile directory of the bridge
browser_headed = false
throttle_dir = ""         # empty = the default directory of the shared budget files

[breaker]
rate_limit_consecutive = 20
rate_limit_ratio = 0.9
server_error_consecutive = 50
ignore = false

[bridge]
degraded_after = 3
blocked_after = 10        # never below degraded_after
blocked_min_seconds = 200

[fetch]
only_finished = true
save_empty_rounds = false

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
include_legacy = false

[live]
source = "page"           # page | direct | poll. Polling is always the fallback of page and direct.
                          # "direct" uses SofaScore's own client credential outside its client, may break
                          # without notice, may get the IP address blocked and is a terms-of-use grey area:
                          # read section 8.3 before choosing it. It is never the default.
poll_interval_seconds = 30
detail_slices = []        # e.g. ["incidents", "statistics"] while an event is live
detail_interval_seconds = 20
max_event_polls = 20      # event pages requested per polling round, at most

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
allowed_hosts = ["localhost", "127.0.0.1", "[::1]"]
token_env = ""            # empty = no other variable: SOFASCORE_API_TOKEN stays in force (PR #43)

[log]
level = "INFO"
debug = false             # true forces the DEBUG level
format = "text"
dir = ""                  # empty = logs/ in the project folder
to_file = true
max_mb = 5
backup_count = 5

[display]
language = "en"           # not set = the system language (Turkish) or English
use_color = true
date_format = "%Y-%m-%d %H:%M:%S"
```

The defaults are the code's defaults of today. The first version of this sample had `timeout_seconds = 20`
and `allowed_hosts` without `[::1]`; the code has 10 and the three loopback names, and the model keeps the
code's values. It also had no place for many of today's settings: the sections `[breaker]`, `[bridge]`,
`[fetch]` and `[display]` and the keys `durability`, `base_url`, `wait_time_min`, `wait_time_max`,
`use_proxy`, `browser_profile`, `browser_headed`, `throttle_dir`, `include_legacy`, `max_event_polls`,
`debug`, `dir`, `to_file`, `max_mb` and `backup_count` were added for them. Two values exist only in the
environment and are rejected in the file: the access token and the captcha token.

**Layers**, weakest to strongest (`loader.LAYERS`):

| Layer | What it is |
|---|---|
| `default` | the defaults in the code |
| `dotenv` | a legacy name (`DATA_DIR`, `MAX_CONCURRENT`, …) whose value this process applied from `.env` |
| `overrides` | `CONFIG_DIR/overrides.json`, machine-written by the web UI (nothing writes it yet); the shape of the config file without `[[follow]]`, `[[sink]]` and `[[schedule.task]]` (decision D11) |
| `file` | `sofascore.toml` |
| `env` | the process environment: the legacy names, then `SOFASCORE_<SECTION>__<KEY>`; when both are given the new name wins |
| `flag` | command-line flags, handed over by the caller as `flags={"storage.data_dir": ...}` |

A value pinned by `file`, `env` or `flag` is shown as locked in the web UI (decision D4; `Source.locked`).
`config show` prints each value with its layer and the name of its source (`loader.active().describe()`,
secrets masked).

The first version put `.env` into the environment layer, above the config file. python-dotenv loads `.env`
into the process environment, and `.env` is the file the installers create and the web UI writes; with that
order every line of an installer-made `.env` would beat the config file. The loader therefore tells the two
apart: a variable whose value in the environment equals the value this process applied from `.env` counts as
`dotenv` and ranks below the overrides file and the config file; any other value counts as `env` and ranks
above them. Consequences, all as built:

- A value given in the shell that equals the `.env` line is labelled `dotenv` and so ranks below a config
  file. Without a config file the result is the same. This is why `main.py --data-dir X`, which passes the
  flag through the environment, loses to a config file that pins `data_dir` when `X` equals the `DATA_DIR`
  line of `.env`; the new CLI passes flags through `loader.activate` and makes relative flag paths absolute
  first (P18, P19).
- A new-style name written into `.env` (`SOFASCORE_CLIENT__RATE=...`) counts as environment and beats the
  config file, unlike a legacy name in `.env`.
- `ConfigManager.reload_config()` no longer overwrites process-environment values with `.env` lines, empty
  ones included (the defect PR #43 reported for every setting but the token). Values that came from `.env`
  are still refreshed, and a value saved on the Settings page still takes effect at once
  (`loader.note_dotenv_write`).
- With a config file that pins a value, the Settings page still reports success when that value is saved:
  the value goes to `.env` and has no effect. The locked fields of the settings API end that (P20).

The position of `.env` is decision D19 in `03-implementation-plan.md`; the owner confirms it.

**Environment.** `SOFASCORE_<SECTION>__<KEY>`, e.g. `SOFASCORE_CLIENT__RATE=5`, `SOFASCORE_SERVER__PORT=9000`,
`SOFASCORE_STORAGE__DATA_DIR=/data`. Lists are JSON: `SOFASCORE_FOLLOWS`, `SOFASCORE_SINKS`,
`SOFASCORE_SLICES` and `SOFASCORE_SCHEDULE__TASKS`. An unknown `SOFASCORE_*__*` variable is an error. An
empty secret in such a variable counts as not set, so an empty `SOFASCORE_SERVER__TOKEN` does not remove a
token given under its legacy name.

**Legacy names.** The current names (`DATA_DIR`, `REQUEST_RATE_LIMIT`, `MAX_CONCURRENT`, `APP_LANGUAGE`,
`USE_PROXY`, `PROXY_URL`, the breaker and bridge thresholds, `REFRESH_*`, `SOFASCORE_ALLOWED_HOSTS`,
`SOFASCORE_BROWSER_PROFILE`, …; the table `loader.LEGACY`, 38 names, and the two language names) are read
until P30 removes them. Each is
parsed with the rule of the code that reads it today, odd ones included (`USE_PROXY=yes` is false,
`REQUEST_TIMEOUT=10.5` falls back to the default, an empty `DATA_DIR` stays empty); a test compares the table
with today's readers and with the settings golden of G-04. A legacy value that does not parse falls back to
the default with a warning, now in English (`<NAME> is not valid; using the default <v>.`). The config file
and the new names are parsed strictly: a rejected value is a `ConfigError`. The deprecation warning for a
legacy name is logged only when a config file is present and the name is set in the process environment
(`<NAME> is a legacy name; use SOFASCORE_<SECTION>__<KEY> or the config file`), so nothing new is logged for
an existing installation. The Docker image sets `SOFASCORE_BROWSER_PROFILE` in its `ENV`, so a container with
a config file logs that warning at every start until P25 switches the image to the new name.

Two settings have two readers with different rules, and the model keeps both. `API_BASE_URL`:
`ConfigManager.get_api_base_url` returns it as written (an empty value stays empty, pinned by G-04), while
the transport turns an empty value into the default and strips a trailing slash; the model has
`client.base_url` for the first and `client.effective_base_url` for the second. `DEBUG`: the logger accepts
`true`, `1`, `yes`, `t`, `y`, `on`, and `log.debug` follows it, while `GET /api/settings` reports `debug`
with its own rule (only the word `true`); the route is unchanged.

**Paths.** A relative path in the config file, in `overrides.json` or in a `SOFASCORE_*__*` variable is
resolved against the config file's directory. Legacy names and flags are left as written: a legacy name stays
relative to the working directory, as today.

**Secrets by name.** `token_env` and `proxy_env` give the name of an environment variable. An empty
`token_env` means "no other variable": `SOFASCORE_API_TOKEN` stays in force. A named variable that is unset
or empty is a `ConfigError`, for both keys, so protection cannot be switched off by a typo. Giving `proxy`
and `proxy_env` in the same layer is an error; `proxy_env` from the same or a stronger layer replaces
`proxy`. The token itself is `settings.server.token`.

**Proxy.** With the legacy names a proxy is used only when `USE_PROXY=true`, as today. In the config file
(and in the new names and flags) giving `proxy` or `proxy_env` switches the proxy on, unless
`use_proxy = false` is set in the same or a stronger layer. The first version said nothing either way.

**Lists.** `[slices.<sport>]` merges sport by sport across layers; `[[follow]]`, `[[sink]]` and
`[[schedule.task]]` are replaced as a whole by the strongest layer that gives them. Sport names are checked
against the registry (`src.sports.sport_slugs()`), so a config that names a sport that is not registered
yet is rejected. Slice names and the `run` names of schedule tasks are not checked against a registry yet
(P12, P27, P29).

- A `[[follow]]` names exactly one of `tournament`, `team`, `player`, `event`, and optionally `name`,
  `sport`, `seasons`, `slices`, `live`, `enabled`. It becomes a `FollowSpec` with the fields of
  `01-storage.md` 2.3; the class is defined in `src/config/settings.py` as well, because the Store may not
  import `src.config`. A follow without a name is named `<kind>-<id>`; without `seasons` it takes
  `[defaults] seasons`. An entity or a name may appear once. `slices` is None (the default selection),
  `{"include": [...]}` for a plain list in the file, or `{"enable": [...], "disable": [...]}` for a table.
- A `[[sink]]` becomes a `SinkSpec`. `secret_env` is a name; the loader does not read its value. Keys the
  model does not know go to `SinkSpec.options` for the sink's own code. The path of a file sink is made
  absolute. A webhook without `secret_env` and without `allow_unsigned = true` is rejected at load
  (decision D12).
- A `[[schedule.task]]` becomes a `ScheduleTask` with `run`, `every`, `every_seconds`, `cron` and `options`;
  exactly one of `every` and `cron` is required.

**The environment bridge.** Many modules still read their setting from `os.environ` themselves (the
throttle, the refresh policy, the logger, the bridge health, the breaker, the web security module, the
watcher, the paths module, `src/store/files.py`, the transport). So that they honour the config file too,
the loader writes the effective value of every setting that did not come from a legacy name back into
`os.environ` under its legacy name (`loader.projection`). Nothing is written when there is neither a config
file nor a `SOFASCORE_*__*` variable, an overrides file or a flag. The bridge is transition code: a module
that switches to `active_settings()` drops its row from `TODAYS_READERS` in `tests/test_config_loader.py`,
and P30 removes the legacy table, the dotenv layer and the bridge. `loader.reset()` undoes what the bridge
wrote (tests).

**What stops the start.** A broken config file, an unknown `SOFASCORE_*__*` variable or an invalid
`overrides.json` raises `ConfigError` (`config_invalid`). Through `main.py` that is a traceback and exit
code 1 today, with the file and the key in the last line; `--version` and `--doctor` still work, because
they run before the imports. The new CLI renders it as exit code 2 (P18, P19). `loader.reload()` re-reads
`.env`, the overrides file and the config file; if one of them is invalid the previous settings stay in
force.

**Warnings.** `loader.active().warnings` are `ConfigWarning(code, message)`, logged at the first load:
`legacy_name` (above) and `live_direct_source`, the four points of 8.3, whenever `[live] source` is
`direct`. With a config file the log also has `Config file loaded: <path>`.

**Cost.** The environment can change at any time (tests and the Settings page rely on that), so every
`ConfigManager` getter compares it: a getter call costs about 40 µs instead of about 0.5 µs. The call sites
are per request and per web request, not per file.

**What uses the model today.** `ConfigManager`'s getters, and through the bridge every module that reads a
legacy name. Modelled and validated but not used by anything yet: follows (ST-17), sinks (P22), schedule
tasks (P29), the slice selection (P27), `[server] host` and `port` (P25), `[log] format` (P18),
`[client] odds_provider` (P28) and `[live]` (P23, P24, P31). There is no `[live] enabled` key and no
`sources` list. `loader.load_settings(config_file=...)` validates a file without touching the process
(`config validate`), `loader.find_config_file()` is `config path`, and `config_schema()` is the JSON Schema
of the file. Constructing the `Settings` creates no file; constructing `ConfigManager` still creates
`config/leagues.txt` (`03-implementation-plan.md` section 15).

Follows have three origins (`01-storage.md` 2.3):

- **Without a config file** (every existing installation): `config/leagues.txt` and `league_sports.json` stay
  the source of truth, are edited by the web UI as today, and are mirrored into the Store as origin `legacy`.
  `.env` is read as settings. Nothing changes for the user.
- **With a config file**: its `[[follow]]` entries are origin `config` (read-only through the API); follows
  added through the API or the web UI are origin `api` and live in `state.db`; `leagues.txt` is not read.
  `ssc config init --from-legacy` prints the TOML equivalent of the legacy files, and `ssc follows export`
  prints every follow as config text for folding API-created ones back into the file.

As built: ST-17 (PR #62) fills the table and nothing reads it yet. `leagues.txt` is still read when a
config file exists, because `ConfigManager` is unchanged, so both origins are in the table then (a league
that is in both is the config row). "`leagues.txt` is not read" becomes true when `sync` reads the follows
(P13, P14) and the follows service exists (P21). `ssc config init` (P18) prints a starter file in which every
line is a comment with the default. `config init --from-legacy` writes paths as absolute paths (legacy names
are relative to the project folder, file values to the file's folder), `seasons = "all"` on every follow
(the file's default is `current`), `proxy_env = "PROXY_URL"` instead of the proxy URL, so that no secret is
printed, and `use_proxy = false` when `USE_PROXY` is not set (a proxy given in the file is otherwise used).
It reads the leagues through `ConfigManager`, so it creates `config/leagues.txt` when that is missing and,
since ST-17, mirrors the follows into an existing `state.db`.

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

As built (P18): the envelope has the JSON Schema that `describe schemas` prints. `warnings` entries are
objects `{code, message}`. The error envelope is written to stdout, like every result; the one-line error
for people goes to stderr. A result state with a non-zero exit code is `ok: true` with the report in `data`,
as for `partial`: `doctor` with a failed check exits 1 and has no error code. `--output ndjson` on a
one-shot command prints the envelope on one line. Text output follows `--lang`, `APP_LANGUAGE` and the
system language; `[display] language` of the config file applies to the text of the commands that load the
settings, not to `--help` and not to errors raised before the settings are loaded. Python 3.14 colours
argparse help on a terminal; the CLI turns that off, so that `error.details.usage` never carries colour
codes. Locale keys of the CLI are flat, with the prefix `ssc_`.

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

Today's codes as pinned by the CLI goldens of G-03 (PR #52): 0 for success, for "nothing to do" and for a run
whose every request was refused; 1 for a storage error, an unexpected error, a refresh in which every match
failed and `--doctor` with a failed check; 2 for a usage error and a breaker stop. Two failures exit with 0
today: `--headless --update-all` when SofaScore answers 403 to everything (the breaker does not trip on one
failed request per league) and `--headless --csv-export` on an empty data directory
(`03-implementation-plan.md` section 15). Since P10 (PR #64) `main.py` uses one code of the table already:
6 for a held lease. Its other codes are still today's (breaker 2, storage 1), with two unpinned changes of
P10: an unexpected error in the detail phase ends a headless run with exit 1 instead of being logged and
swallowed, and an `APP_EXIT_CODE` variable in the calling environment no longer overrides the exit code of
a headless run. The new CLI (P18) has the whole table in `src/cli/exit_codes.py`, read from the error table,
with `combine()` for the precedence.

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
  Until the new CLI has the data commands, `main.py` takes the leases itself (P10, PR #64): `writer` for
  `--headless --update-all`, `--refresh-only` and `--recheck-unavailable` and `watcher:<sport>` for
  `--watch`; a refused lease prints the holder (process id, host, purpose, since when) in the application's
  language on stderr and exits with 6. There is no `--wait`: the run fails at once. `--headless --csv-export`
  alone takes no lease. Ctrl+C during a run that holds a lease is not tested.
- Service operation: `docs/` ships two systemd units (`sofascore-serve.service`, `sofascore-watch.service`,
  `Restart=on-failure`, `KillSignal=SIGTERM`) and a timer example for `ssc sync`. The Docker entrypoint
  becomes `ssc serve --host 0.0.0.0` and passes any other arguments to `ssc`; `serve` never widens
  `allowed_hosts` on its own (at `3ae2599` `main.py:251-257` set `*`; the entrypoint avoids that path,
  `docker/entrypoint.sh:7-12`) and prints a warning when bound to a non-loopback address without a token.
  PR #43 implements this rule for `main.py --web`, so the widening no longer exists on main: a wildcard bind
  exits with 2 before the server starts until the allowed hosts are set (`--allow-any-host` is the explicit
  opt-out; pinned in `usage_errors.golden.json` of G-03), one concrete non-loopback address allows the
  loopback names plus that address, and the warning is printed. `serve` keeps those rules (P25); what the
  Docker entrypoint does is decision D17. The live service is a separate unit (`sofascore-watch.service`);
  its page lists the memory each source needs (section 8.5).

### 4.7 Mapping from today's flags

`main.py` stays as a shim for one release: a known subcommand goes to the new CLI; legacy flags are
translated by `src/cli/legacy_flags.py`, which prints one deprecation line to stderr and runs the new command.

| Today (`main.py`) | New | Fate |
|---|---|---|
| `--version` (`:20-22`) | `ssc version`, `ssc --version` | kept permanently |
| no arguments → interactive menu (`:371-374`) | prints help, exit 2. As built for the new entry point (P18): the help goes to stderr under a usage-error line, and with `--json` the result is an `invalid_request` envelope | removed in 3.0.0 |
| `--web --host --port --dev`, `--allow-any-host` (PR #43) | `ssc serve …` | alias, removed in 3.1 |
| `--headless --update-all` (`:330-353`) | `ssc sync` | alias |
| `… --league-id N` (`:99-105`) | `ssc sync --follow <name of N>` or `ssc fetch tournament N --season all` | alias |
| `… --fetch-mode details` (`:92-97`) | `ssc sync --only events` | alias |
| `--headless --csv-export` (`:355-358`) | `ssc export --dataset events --format csv --profile legacy-wide-csv` | alias |
| `--refresh-only` (`:308-328`), `--refresh-legacy` (`:293-294`) | `ssc refresh [--include-legacy]` | alias |
| `--recheck-unavailable[=legacy\|all]` (`:298-306`) | `ssc data recheck-unavailable [--all]`; combined with a download: `ssc sync --recheck-unavailable` | alias |
| `--watch --sport --league-ids --event-ids --watch-hours` (`:205-230`) | `ssc watch --sport S --tournament … --event … --for 2h --source poll --stdout` (polling only, as today: the alias never starts a browser) | alias |
| `--doctor …` (`:30-33`) | `ssc doctor …` | alias; installers (`scripts/install.sh:144`) switch in the same PR |
| `--diagnostics [PATH]` (PR #24) | `ssc diagnostics [--out PATH]` | alias |
| `--ignore-rate-limit` (`:196-200`) | `--ignore-breaker` (it disables the breaker, not the rate limit) | alias |
| `--data-dir` (`:113-118`) | `--data-dir` | kept; the new CLI passes it to the loader as a flag (4.3), today's `main.py` passes it through the environment |
| `--config` (`:107-111`, leagues file; ignored today, see 1.7) | `--config` = the config file; a `.txt` path is accepted as a legacy leagues file with a warning | meaning changes |

Aliases use the new exit codes and the new output rules (decision D5).

---

## 5. Output sinks and event streams

### 5.1 Streams

Producers never call a sink. They append to a durable stream through `store.streams.append` and get a sequence
number; every consumer (`ssc events`, `watch --stdout`, webhooks, file sinks) reads the same log by
sequence number. No HTTP route serves these streams: live data is delivered by the CLI and by sinks only
(owner decision of 2026-10-01), and a program on another machine uses the webhook sink. The job progress
shown by the web UI comes from the `job_events` table through `/api/v1/jobs/{id}/events`, not from here.

| Stream | Producer | Types |
|---|---|---|
| `live` | live service | `live.status_changed`, `live.score_changed`, `live.stuck`, `live.odds_changed` (later), `live.detail_updated` (optional) |
| `change` | pipeline (refresh), live confirmation | `change.recorded` (announces a change-log row; carries its `change_seq`) |
| `job` | job manager | `job.started`, `job.finished` (with state, counts, error code) |
| `system` | client health, live supervisor, dispatcher | `system.blocked`, `system.recovered`, `system.live_source_changed` (data: sport, `from`, `to`, each one of `page`, `direct`, `poll`), `system.sink_dropped` |

Envelope (schema `sofascore.event/1`):

```json
{"stream": "live", "seq": 1842, "type": "live.status_changed", "ts": "2026-10-01T18:52:04Z",
 "event_id": 17124861, "sport": "football", "tournament_id": 17, "source": "push",
 "data": {"from": "live", "to": "completed", "provisional": true, "change_ts": 1790856421, "score": {}}}
```

`source` is `push` or `poll`; which push source produced an event is not part of the envelope (the
`system.live_source_changed` events say which source leads). `seq` is one sequence across all streams of a data
directory. It identifies a message for as long as the
stream id (`store.streams.head().stream_id`) stays the same; consumers de-duplicate on `seq`. Numbers increase
strictly but are not consecutive within a stream.

As built so far (ST-18, PR #59): the 2.x watcher is the only producer. It appends its events to the `live`
stream with the fields it has always written, as they are: `from`, `to`, `at_utc`, `change_ts`, `source`
(`live` or `event`: which request showed the change), `scores`, `provisional`, `start_ts`, `status_class`.
The envelope's `source` is always `poll`, and no `dedup_key` is set. The `data` shape shown above and the
key are the live service's to settle (P23, 8.1); until then a consumer of the stream sees the 2.x fields.

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
  `Authorization: Bearer <token>` (decision D13). PR #43 implements the token for the existing routes
  (`SOFASCORE_API_TOKEN`; a session cookie set by `POST /api/auth/login` is accepted as well, for the web UI
  and its SSE stream; `/health` stays open), moves the origin check to `src/web/security.py`, and adds the
  response headers and the Content-Security-Policy.
- No live data over HTTP. There is no `/live/*` resource and no live SSE stream (owner decision of
  2026-10-01). `/status` reports whether a live service is running (lease, leading source, last heartbeat),
  which is service health, not live data.

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
| `/settings` | GET, PATCH | settings; locked fields flagged |
| `/health`, `/status`, `/diagnostics`, `/diagnostics/bundle`, `/logs` | GET | status |

SSE (`/jobs/{id}/events` only): each message has `id: <seq>`, `event: <type>`, `data: <JobEvent>`, where
`seq` is the job's own event number. Resume with the standard `Last-Event-ID` header or `?after=`. A comment
line is sent every 15 s. If `after` is older than the retained events of the job (the last 2,000 are kept), the
server sends one `stream.gap` event with `oldest_seq` and closes; the client resynchronises through
`/jobs/{id}`.

Errors: `{"error": {"code": "job_running", "message": "…", "details": {…}, "request_id": "…"}}` with the
status from the table in 2.6. `message` is English; clients translate by `code`. Upstream trouble during a
request that calls SofaScore is 503 `blocked`/`rate_limited` or 502 `upstream_error`, never 429. At
`0aa73b4` the single-match route had a substring match that would answer 429 (`routes/matches.py:416-417`),
but that code was not reached for a blocked `/event`: the route answered 404, and 200 when only the slices
were blocked. FX-1 (PR #60) removed the substring match. The legacy route now answers the typed upstream
error of `src/web/upstream.py`, which the league routes use since PR #23: `{"detail": {"reason", "message"}}`
with reason `blocked`, `browser`, `network` or `upstream` and status 502, or `rate_limited` and status 503.
So the legacy route answers `blocked` with 502, the status `upstream.py` has always used, while v1 answers
it with 503 (the table of 2.6); P13 maps the route to the v1 codes when it moves to the pipeline. The same
route is refused with 409 `job_running` while a job of the same server runs or another process holds the
writer lease.

### 6.1 Existing `/api` routes

They stay for one release as `src/web/api/legacy.py`: same paths, same response shapes (pinned by the
goldens of plan items G-02 and G-04), implemented on the same services, marked `deprecated` in OpenAPI and
answered with `Deprecation: true` and `Link: <…>; rel="successor-version"`. The web UI moves to v1 with the
frontend update.

The line numbers in this table are at `0aa73b4`; P08, FX-2 and FX-1 have moved lines in `routes/data.py`,
`routes/leagues.py`, `routes/scrape.py`, `routes/settings.py` and `routes/matches.py` since.

| Today | v1 successor |
|---|---|
| `GET /api/leagues`, `POST /api/leagues`, `PATCH/DELETE /api/leagues/{id}` (`routes/leagues.py:143-181`) | `/follows` |
| `GET /api/leagues/search` (`:184`) | `/follows?q=` |
| `POST /api/leagues/search-remote` (`:198`; a GET before PR #43) | `/tournaments/search` |
| `GET /api/leagues/{id}/seasons` (`:214`), `POST …/seasons/refresh` (`:231`) | `/tournaments/{id}/seasons`; `POST /jobs {kind:"sync", spec:{phases:["listing"]}}` |
| `GET /api/seasons/{sid}/matches` (`routes/matches.py:421`), `GET /api/matches` (`:435`) | `/events?season=` , `/events` |
| `GET /api/leagues/{id}/missing-details` (`:428`) | `/events?tournament=&has=missing` |
| `GET /api/matches/{id}` (`:462`) | `/events/{id}` + `/events/{id}/slices/{key}`; the legacy shape (dict of raw slices with the `basic` key) is built from the raw payloads |
| `POST /api/matches/{id}/fetch` (`:471`) | `POST /jobs {kind:"fetch", spec:{events:[id]}}`; the alias waits for the job |
| `POST /api/fetch` (`routes/scrape.py:88`) | `POST /jobs {kind:"sync"\|"fetch"}` |
| `GET /api/scrape/status` (`:33`), `GET /api/scrape/stream` (`:53`) | `/jobs?state=running`, `/jobs/{id}/events` |
| `POST /api/scrape/cancel` (`:79`) | `POST /jobs/{id}/cancel` |
| `GET /api/jobs`, `/api/jobs/{id}` (`:39-50`) | `/jobs`, `/jobs/{id}` |
| `GET /api/status` (`:108`), `GET /api/bypass/status` (`:118`), `POST /api/bypass/test` (`:145`) | `/status`; `POST /status/check` |
| `GET/POST /api/settings` (`routes/settings.py:162`, `:199`) | `GET/PATCH /settings` |
| `GET /api/dashboard`, `/api/stats/system` (`routes/data.py:60`, `:71`) | `/status` (summary and coverage) |
| `POST /api/data/backup`, `GET /api/data/backups/{name}`, `POST /api/data/clear` (`:243-285`) | `POST /jobs {kind:"backup"\|"clear"}`, `/backups/{name}` |
| `GET /api/export/csv` (`:291`), `POST /api/export/csv` (`:304`). Since PR #43 the GET only downloads an existing export and the POST creates it | `POST /jobs {kind:"export"}` + `/exports/{id}/download`; the alias streams the `legacy-wide-csv` profile and no longer writes a file into the data directory (EX-1, decision D16) |
| `GET /api/sports` (`routes/sports.py:43`) | `/sports` |
| `GET /api/logs`, `GET /api/diagnostics`, `GET /api/diagnostics/bundle` (`routes/diagnostics.py:18`, `:29`, `:35`; added by PR #24) | `/logs`, `/diagnostics`, `/diagnostics/bundle` |
| `GET /api/auth`, `POST /api/auth/login`, `POST /api/auth/logout` (`routes/auth.py:29`, `:38`, `:63`; PR #43) | the same paths under `/api/v1`; the token check covers both prefixes |

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

State on 2026-10-02: P08 (PR #57) did 2, 6 and the web half of 1 and 3, and P10 (PR #64) the CLI half.
`SyncService` carries the web flow and the headless flow, and `export_all_csv` is the CSV entry; neither the
web nor a headless run imports the terminal UI. `main.py` imports it only on the interactive branch.
`src/SofaScoreUi.py` keeps its own copy of the wiring, and three methods that nothing calls any more, until
P26.

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

## 8. Live: one supervised CLI service

Two owner decisions of 2026-10-01, taken after the push channel was measured
(`docs/push-channel/README.md`, PR #42), shape this section:

1. **Live data is not part of the web UI and is not exposed over HTTP.** A person at a screen can watch live
   scores on SofaScore itself; the value of the live stream is for servers and programs. Live watching is a
   CLI service (`ssc watch`) that delivers events to sinks: stdout as NDJSON, a file, a webhook. A program
   that wants live data over the network uses the webhook sink. The event streams stay in `state.db` and the
   sink design of section 5 is unchanged.
2. **Two selectable push sources, and polling as the fallback that is always there.** `page` (the default)
   listens to the push connection of a real browser page and handles no credential. `direct` (an explicit
   opt-in) connects a lightweight client to the push server itself, with the credential read at runtime from
   the bridge page's own connection. `direct` is never the default and is never enabled implicitly.

### 8.1 Shape

```
            ┌────────── LiveService (supervisor), run by `ssc watch` ──────────┐
 follows →  │ scope      PageSource (default) or DirectSource (opt-in) ─┐      │
 (live=true)│            PollSource (always present) ───────────────────┼→ observations → Reducer → store.streams.append("live") → sinks, `ssc events`
            │ arbiter (which source leads per sport)                    │      │
            │ confirm/detail fetcher ───────────────────────────────────┴→ store.events.observe / put
            └──────────────────────────────────────────────────────────────────┘
```

- **Independent of download jobs.** It holds the `live` lease, never `writer`; it has its own request context
  (no job breaker), its own throttle lane (today `watcher.py:43-44`, `:170-172`), and writes only through
  `store.events.observe/put` and `store.streams.append`. A sync job (in `serve` or in a CLI run) and the live
  service are separate processes; the Store's write protocol serialises their writes per entity, and an older
  observation never replaces a newer one.
- **Sources produce observations, not events.** An observation is `(event_id, partial or full event object,
  source, received_at)`. The three sources are described in 8.2.
- **Reducer** is the pure core of `MatchWatcher._observe` (`watcher.py:232-307`):
  `(LiveState, Observation) → (LiveState, [LiveEvent])`. It emits `status_changed`, `score_changed`, `stuck`,
  sets `provisional` by the refresh window, and de-duplicates across sources with the stream's `dedup_key`
  `(event_id, type, change_ts or state hash)`, so a transition seen by push and by poll is stored once.
  It keeps the last known state per event and derives events from the difference between that state and the
  observation. It must tolerate gaps: a source can miss intermediate states (the push connection is dropped
  about every 30 minutes), so a status may jump and a score may move by more than one step.
- **State** moves from `watch_state_{sport}.json` (`watcher.py:46`, `:204-214`) into `store.watch`, so a
  restart does not re-emit and one service covers all sports (today: one process per sport). For the 2.x
  watcher this is done (ST-18, PR #59): its state is authoritative in the `watch_state` table (watcher name =
  sport), the state file is read once and afterwards written only as a copy, and each event is appended to
  the `live` stream as well as to `watch_events.jsonl`. P23 drops the copy. Two limits of that stage: the
  watcher sets no `dedup_key` (whether `changeTimestamp` differs between two transitions that end in the
  same state cannot be checked offline, and a wrong key would drop a real event), so a crash between an
  event append and the state save at the end of the round still emits that transition again after the
  restart, as 2.x did; and an append that meets `StoreBusy` ends the `--watch` run, as an `OSError` on the
  events file did before. The live service chooses the key and retries a busy append with back-off (P23).
- **Confirmation.** A terminal status from push is emitted immediately, then confirmed by one `/event/{id}`
  request that stores the full payload. That stored event is what a later `sync` completes with post-match
  slices; the live service does not download details unless `live.detail_slices` asks for them.
- **Arbitration and fallback.** Per sport the arbiter tracks push health (connection open, last frame, last
  ping). Push healthy: polling drops to a slow safety interval. Push silent beyond the threshold or
  disconnected: polling returns to `poll_interval` (30 s, `watcher.py:33`). After every reconnect one poll
  round runs, because a transition that happened while the connection was down is not repeated on push.
  Every switch appends `system.live_source_changed`. Events in scope that push never mentions are covered by
  polling. The fallback is polling and only polling: a failing `page` source never makes the service try
  `direct`.
- **Supervision.** The supervisor restarts a crashed source with back-off, records heartbeats and counters
  for `LiveService.status()`, pauses and backs off when the client reports `blocked` (it never "trips and
  stops"), and reloads the scope when follows change or on SIGHUP.

### 8.2 The sources

| | `page` (default) | `direct` (explicit opt-in) | `poll` (the fallback; also selectable alone) |
|---|---|---|---|
| How it works | keeps one real browser page open per watched sport and listens to the frames of the push connection that SofaScore's own page opens | a lightweight client connects to the push server itself (NATS over WebSocket) and subscribes to `sport.{sport}` | requests the live list per sport every poll interval, and event pages for dropped, near-end and stuck events |
| Credential | none handled: the connection is the page's own; the source opens no connection, sends no subscription and never reads the credential | the site's own client credential, read at runtime from the `CONNECT` frame of the bridge page's connection; in memory only | none |
| Delay of a match end, median (measured) | 0.6 to 1.0 s after SofaScore's own change time | 0.7 s | 32 to 52 s at a one-minute interval |
| Coverage (measured) | about 100 %: football 234 of 234 status changes, tennis 92 of 92, basketball 128 of 129 | identical to the page in a shared window of about 30 minutes (225 of 225 frames) | the baseline; a match that finishes between two polls can drop off the live list unseen |
| Memory (measured, RSS) | 1.8 to 2.6 GB per sport page with ads, analytics and images blocked; 2 to 3 GB without blocking | about 0.2 GB for the client; an idle bridge tab, if the browser is kept open, about 1.1 GB | nothing beyond the process |
| Breaks when | the page shows a captcha, the site changes its page, memory runs out | the credential changes, the server starts refusing non-browser clients, the IP address is blocked | SofaScore blocks the requests |

Facts both push sources rest on (`docs/push-channel/README.md`; one evening, one region, three sports):

- Only a sport page (or its live filter) subscribes to `sport.{sport}`, which carries a whole sport. A match
  page subscribes to `event.{id}` for that match only; favourites, news, tournament and trend pages subscribe
  to `event.{id}` for the matches they show. The `page` source therefore opens one sport page per watched
  sport, whatever the scope, and filters.
- A frame carries only the changed fields of an event, as dotted paths with their new values
  (`docs/all-sports/README.md`, "Push kanalı"); it is not a whole event object. The source keeps the last
  known state per event, seeds it from the polling list at start and after every reconnect, and merges each
  frame into it. A finish arrives with status, score and `winnerCode` in one frame.
- The connection is dropped about every 30 minutes and established again (three or four cycles per sport in
  99 minutes). The site's code reconnects and subscribes again by itself; the `direct` client has to do the
  same. Of 36 transitions that fell into such windows, 22 were not seen on push at all (an upper bound: the
  windows were measured approximately). This is why the polling fallback is mandatory and why the reducer
  tolerates gaps.
- The site's client sends a PING every 120 s; the arbiter uses that cadence for its silence threshold.
- Push also carried finished matches that one-minute polling never listed (football 74, basketball 25).

`PollSource` is today's `MatchWatcher.tick` (`watcher.py:337-380`) with the per-sport rules from
`sports.WatcherParams` (`sports.py:40-48`). `PageSource` routes the page's requests through the shared
throttle or aborts them, and blocks ads, analytics, images, media and fonts. Everything downstream of the
reducer is identical for the three sources.

### 8.3 The `direct` source: opt-in, and what the user is told

`direct` exists because it needs a tenth of the memory. It is offered, not recommended. The rules:

- **Opt-in only.** The source is used only when the configuration says `direct` literally: `--source direct`,
  `[live] source = "direct"` or `SOFASCORE_LIVE__SOURCE=direct`. The default is `page`. No code path selects
  `direct` by itself: not a fallback, not an "auto" value, not an error handler. P31 has an acceptance test
  for this.
- **The credential stays in memory.** It is read from the `CONNECT` frame of the connection that the bridge
  page opens itself, kept in the process, and never written to disk, to `state.db`, to a log line, to a
  stream event, to the diagnostics bundle or to the repository. When the server rejects it, it is read again
  from a fresh page; after repeated failures the source reports unhealthy and the service stays on polling.
  The browser is needed for that read only.
- **Minimal behaviour on the wire.** One connection, subscribe only, no publish, no wildcard subjects, the
  site's own PING cadence.
- **Warnings.** Wherever the source is configured or documented (the `--help` text of `--source`, `describe`,
  `config validate` and `config show`, the comment in the config example of 4.3, both READMEs, the page of the
  watch unit under `docs/deploy/`) and in the log at every start with this source, the user is told:
  1. it uses the site's own client credential outside the site's client;
  2. it may break without notice if the credential or the server changes;
  3. it may get the IP address blocked;
  4. it is a terms-of-use grey area that the user chooses knowingly.
- **What was measured, and what was not.** One connection, the single subject `sport.football`, about 38
  minutes, one reconnect, 216 to 226 MB of memory; the plain client was accepted without imitating a
  browser's TLS fingerprint. Not measured: several subjects on one connection, runs of hours, several sports,
  how often the credential changes. The documentation of the source says so.

### 8.4 Hosting

- `ssc watch` is the only host: a foreground process for systemd or a container. There is no live service
  inside `ssc serve` and no `[live] enabled` key; a library caller can run `LiveService.run` in a thread of
  its own.
- A second live service on the same data directory exits 6 (`instance_running`). Until the live service
  exists, the current watcher takes `watcher:<sport>` leases, so one process per sport keeps working. The
  lease is taken by `main.py` around `--watch` since P10 (PR #64): a second `--watch` for the same sport on
  the same directory prints the holder and exits with 6.
- Browser profile: Chromium allows one process per profile (`doctor.py:71-72`, `docker/entrypoint.sh:17-39`).
  The `page` source keeps a browser open permanently, so it uses its own profile directory by default
  (`<profile>-live`) to leave the bridge profile free for jobs in other processes (decision D10). The `direct`
  source uses the bridge page only to read the credential and needs no second profile.
- The legacy `main.py --watch` alias runs `ssc watch --source poll`, so that existing cron and systemd setups
  keep polling and do not start a browser (decision D18).

### 8.5 Operational notes: memory

Measured on 2026-10-01 (`docs/push-channel/README.md` sections 6 and 7); RSS is the sum of all Chrome
processes of the profile.

| Configuration | RSS | Note |
|---|---|---|
| idle bridge tab (`robots.txt`), bridge open | about 1.1 GB | the base cost of keeping Chrome up |
| one sport page, blocked, first minute | 1.8 GB | push connection and `sport.football` subscription being set up |
| the same after 30 minutes | 2.6 GB | grows by about 0.8 GB in 30 minutes |
| the same after the reconnect | 1.6 to 2.0 GB | the 30-minute drop gives the memory back; growth is not monotonic |
| one sport page without blocking | 2 to 3 GB | blocking ads saves 0.5 to 1 GB per page |
| `direct` client | about 0.2 GB | flat; 173 MB after a reconnect |

- Plan for the peak, not the average: `page` needs about 2.6 GB per watched sport. Three sports are about
  8 GB; each further page adds its own renderer processes (14 to 43 renderers in the three-sport run, which is
  not a leak).
- A container or systemd unit for `ssc watch --source page` gets a memory limit above that peak; below it the
  kernel kills the browser at the worst moment, about every 30 minutes.
- `ssc watch --source poll` needs no browser. `ssc watch --source direct` needs the browser only while it
  reads the credential.
- `ssc status` shows the leading source per sport and the last source switch, so a service that silently
  fell back to polling is visible.

### 8.6 Order of delivery

Polling first (P23): supervisor, reducer, state in the Store, sequence-numbered events, `watch`, sinks. The
`page` source and the arbiter second (P24). The `direct` source third (P31), with its warnings and its opt-in
test. Everything downstream of the reducer is identical for all three, so P24 and P31 each add one source.

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

Added to the plan after the drafts: P31 (the `direct` live source, section 8.3) and the fix items FX-1 to FX-7
(`03-implementation-plan.md` sections 10 and 15).

## 10. Testing strategy

- A fake SofaScore transport (`tests/fakes/sofascore.py`, G-01) serves canned payloads, records every request
  and can inject 403/429/5xx/timeouts. It is installed one level below the request functions, at the curl
  calls, so that the request layer's own retry and error handling run in the tests. All golden tests use it;
  none touches the network.
- Goldens before refactoring: request sequence and resulting store state for each flow (G-01), JSON of every
  current route plus the OpenAPI document (G-02, G-04), stdout/stderr/exit code of each current flag (G-03).
  G-03 runs `main.py` in a subprocess with the fake installed through a `sitecustomize` module of the test
  tree; its goldens also hold the requests, the skipped waits and the files a run changed.
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

Changes after the first two implementation batches and the owner's decisions of 2026-10-01:

15. **Live is a CLI service.** The SSE route `/live/stream`, the pull routes `/live/status` and `/live/events`,
    `serve --live` and `[live] enabled` were removed (sections 2.7, 4.1, 4.3, 5.1, 6, 8). Live events leave the
    process through sinks and `ssc events` only.
16. **Two push sources.** `page` (default) and `direct` (explicit opt-in with warnings) replace the single
    "push" source; `[live] sources` became `[live] source`; section 8 was rewritten around the measurements of
    `docs/push-channel/README.md` and carries the memory figures.
17. **Pacing.** The fixed pauses of row "Pacing" in 1.4 were removed by PR #33; the row is no longer a
    divergence.
18. **Single-match route.** It does not answer 429 for a blocked upstream; it answers 404, or 200 when only
    the slices are blocked (1.5, 6). Plan item FX-1.
19. **Outcome.** `fetched_at` is optional (None means now); `from_error` maps only `ResourceNotFoundError` to
    empty; the breaker switch to `skipped` moves together with `_update_slice_markers` (2.4).
20. **Job model.** `Origin` and `ErrorInfo` exist in `src/jobs/model.py`; timestamps are typed; ids are uuid4
    until P11; a breaker-stopped job is recognised by its column, not its text; `created_at` gets a column in
    migration 0002 (2.8).
21. **Layer rule 5** says what the test can enforce (2.1).
22. **Legacy routes.** The table of 6.1 has the three diagnostics routes of PR #24 and the auth routes of
    PR #43, and its line numbers are at `0aa73b4`.
23. **Defaults and security done.** 5 requests per second (PR #33), English by default (PR #39); the access
    token, the Host allow-list rule and the response headers came with PR #43 (1.7, 2.4, 4.6, 6).
24. **Fake transport boundary.** One level below the request functions (10).

Changes after batches three and four (2026-10-02):

25. **Client as built.** No `throttle` and no `lane` argument; `aclose()` closes the running loop's session
    and `close()` detaches the health callback; `ClientSettings` is defined (twice, for now); the sync body
    is kept; a `null` body is `failed/other`; the breaker is `skipped` in the client and still `failed` in
    the fetchers (2.4; P05).
26. **Base URLs.** More than four places hard-coded the base; what is left is named (1.7; P05, P08).
27. **Throttle.** A reservation can be given back, and the bridge's slot wait can be cancelled (2.4, 3.4;
    FX-6). The burst allowance stays (decision D14).
28. **Context and sync as built.** `ServiceContext` carries the fetchers; `SyncSpec` is today's request and
    `SyncResult` today's result; `JobHandle` is a Protocol with `publish`; the export is one function that
    swallows errors (2.1, 2.3, 2.7, 2.8, 7.1; P08).
29. **Configuration as built.** `.env` is a layer of its own below the config file; four more sections and
    fifteen more keys; `token_env` and `proxy_env` semantics; a proxy in the file switches the proxy on;
    `SOFASCORE_CONFIG=none`; the legacy-name warning only with a config file; where relative paths resolve;
    the environment bridge; the defaults of the sample (4.3; P09).
30. **Leases in the error table.** `instance_running` has no class yet; backup takes `writer` as a data
    operation (2.6, 2.8; ST-10).
31. **`main.py --web`** no longer widens the allowed hosts (4.6), and logs share stdout with user text also
    when a plain handler is used (1.7); today's exit codes are listed as pinned (4.5; G-03).
32. **Single-match route.** The substring mapping is gone; the route answers the typed upstream error, with
    502 for `blocked` (1.5, 6; FX-1).
33. **Live events as stored today.** The 2.x fields, source `poll`, no `dedup_key`; the state file is still
    written as a copy; the watcher lease is P10's (5.1, 8.1, 8.4; ST-18).
34. **Follows.** `build_context` and `ConfigManager` update the table without opening the Store; with a
    config file `leagues.txt` is still read (2.3, 4.3; ST-17).
35. **Headless runs on the services.** A refresh mode and an `export` flag on `SyncSpec`, `RefreshCounts`,
    a `MaintenanceService` with one method, no `RefreshService`; leases are taken by `main.py`, not by the
    services; exit code 6 for a held lease; only one of the three orchestrators is still called (1.3, 2.7,
    2.8, 4.5, 4.6, 7.1; P10).
36. **Error table and CLI skeleton as built.** The constructor of the subclasses, the existing classes in
    the table, two classes for `data_operation_running`, Turkish Store messages; what `version`, `doctor`
    and `describe slices` show; three global flags that are not there yet; the project folder as the working
    directory; the envelope's `warnings`, the error envelope on stdout, `ok: true` with a non-zero exit code;
    what the CLI imports beyond rule 1; what `config init --from-legacy` writes (2.1, 2.6, 4.1 to 4.4, 4.7;
    P18).

---

## 12. Risks

- Pull requests merged after the briefs were written changed files that plan items own: #23, #24, #32 and
  #43 (web security hardening: `main.py`, `src/config_manager.py`, three route modules and more). The line
  numbers in the briefs of G-03, P05, P08, P09, ST-10 and FX-6 are older than those changes. #47 (the boundary
  ratchet) makes every later PR that moves a file-system call edit a baseline file, and #41 makes every PR
  that changes a route regenerate the legacy OpenAPI snapshot (`03-implementation-plan.md` section 1).
- Existing tests pin private names of the fetchers (for example `_LEGACY_SLICE_FETCHERS` at
  `match_data_fetcher.py:106-113`, patched in `tests/test_breaker_phases.py` and `tests/test_storage_errors.py`).
  P13 must port these tests; if they are simply deleted, coverage of breaker, storage-error and cancel
  behaviour is lost exactly where the code changes most.
- P13 changes request volume and timing: it removes the triple per-match retry and fetches optional slices
  on every path. The fixed sleeps are already gone and the default rate is 5 req/s (PR #33), so the budget
  bounds the load; what remains is the burst after idle time (decision D14).
- Cross-process leases rely on OS file locks. They work on local file systems and Docker volumes on one host;
  on NFS/SMB or across hosts they may silently not exclude. Windows and macOS are best-effort.
- Chromium allows one process per profile directory. A permanently open push-listening browser plus on-demand
  bridge launches from other processes will conflict unless the `page` source uses its own profile (D10); a
  second profile means a second challenge solve and extra memory.
- The `page` source is expensive: 1.8 to 2.6 GB of memory per watched sport page even with ads, analytics and
  images blocked (measured, `docs/push-channel/README.md`). Three sports need a host with 8 GB to spare. A
  small server runs `--source poll`, or the user chooses `direct` knowingly.
- Both push sources depend on undocumented behaviour of SofaScore's page and server (subjects, frame format,
  the 30-minute reconnect, whether `sport.{sport}` carries all events at all hours). It can change without
  notice; polling stays a complete fallback, but live latency would silently degrade from about 1 s to the poll
  interval. `system.live_source_changed` events and `ssc status` make the degradation visible.
- The `direct` source uses the site's own client credential outside the site's client. The credential can
  change, the server can start refusing non-browser clients, the IP address can be blocked, and the use is a
  terms-of-use grey area. It is opt-in for those reasons; the risk the design must prevent is enabling it by
  accident (a default, a fallback, a copied config line), which is why P31 has an acceptance test for exactly
  that and why the config example carries the warning next to the key.
- Moving CLI logs from stdout to stderr and changing exit codes (breaker 2 → 4, storage 1 → 5, Ctrl+C 0 → 130)
  breaks cron jobs and scripts that parse today's output or test for specific codes. It is a major release,
  but it needs a prominent changelog entry.
- Two configuration sources (declarative file and UI-edited overrides/follows) can confuse users: a value
  edited in the UI that is also pinned in the file will appear not to "stick". The UI must show the lock and
  its source.
  Until the settings API has locked fields (P20) this already happens: with a config file, the Settings
  page reports success for a pinned value and the value has no effect (4.3).
- The environment bridge of 4.3 writes settings into `os.environ`. It is correct only while every direct
  reader of a legacy name is in the loader's table; a module that starts reading a new variable by itself is
  not covered by the config file. `tests/test_config_loader.py` lists today's readers.
- Webhook head-of-line blocking: a receiver that is down blocks its sink for up to `max_age` (24 h by
  default); a low `max_age` loses events. Operators need the lag shown in status.
- Legacy `/api` adapters must reproduce shapes that were by-products of pandas (NaN handling, date strings,
  column names from CSV). Without the goldens these would drift unnoticed and break the current web UI before
  it moves to v1.
- One file chain is long: `src/match_data_fetcher.py` has exactly one owning PR at a time through eleven PRs
  (`03-implementation-plan.md`). A stalled PR in that chain blocks everything behind it.
- asyncio pipeline with a writer thread: a Store write that blocks (the catalog mutex held by the live
  service, a slow disk) backs up the result queue; the queue is bounded, which in turn slows fetching.
