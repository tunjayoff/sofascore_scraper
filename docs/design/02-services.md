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

Revised a third time on 2026-10-02 (the second revision of that day) after batches five to seven (PRs #68
to #75). The job manager and what the service context holds now (P11, #69), the job columns of the
diagnostics bundle (#71), the schema package (SC-1, #72), the sinks and `ssc events` (P22, #73) and the
foundation of API v1 (P20, #74) are described as built, again as the current stage with what later items
add: sections 1.7, 2.1 to 2.8, 3.4, 4.1 to 4.6, 5, 6, 8.1, 8.4, 8.6 and 9 to 12. References marked
`e0bae0c` are to `origin/main` at that commit. Section 11 lists the corrections (items 37 to 59).

Revised a fourth time on 2026-10-02 (the third revision of that day) after batches eight to ten (PRs #77 to
#93). The readers that moved to the Store (RD-1 #80, RD-2 #89, RD-4 #78, RD-5 #79), the three answers of a
slice body (FX-5 #77 with its fix commit), the cancel check after the request semaphore (FX-9 #83), the
redaction fixes (FX-10 #84), backup and clear in the Store (ST-19 #90), the live service and the first host
of the sink dispatcher (P23 #91), and the end of a job (#88, #93) are described as built, again as the
current stage with what later items add: sections 1.4, 2.1 to 2.8, 3.4, 4.1, 4.3, 4.6, 5, 6, 7.1, 8, 9, 10
and 12. References marked `9b03c64` are to `origin/main` at that commit. Decisions S17, P3 and D21 and the
fix item FX-12 of `03-implementation-plan.md` section 13 are referred to by those names. Section 11 lists
the corrections (items 60 to 75).

Revised a fifth time on 2026-10-03 after batches eleven to nineteen (PRs #95 and #97 to #133). The export
service (EX-1 #99, FX-7 #103, SC-2 #130), backup format 2 with restore (ST-24 #109), migrate and the catalog
commands (ST-23 #110), the data commands, signals and the `main.py` shim of the new CLI (P19 #119), `ssc serve`
(P25 #125), API v1 with its resources, data jobs and legacy adapters (P21: #122, #123, #124, #126, #127), the
in-app scheduler (P29 #128) and the removal of the terminal menu (P26 #131) are described as built, again as
the current stage with what later items add: sections 1, 2.1 to 2.8, 4, 5, 6, 7, 9, 10 and 12; the pipeline
and the live sources (sections 3 and 8) were revised in the same pass. References marked `b3cb819` are to
`origin/main` at that commit. The web UI is rebuilt from scratch on the same stack (`05-web-ui.md`; FE-1
#105, FE-2a #107, FE-2b #132 and #133); its old views stay under `/classic` until every function has a home
in `/api/v1`. The routes it still lacks are named in section 6 with their owner, FX-13. Section 11
lists the corrections.

Revised a sixth time on 2026-10-06 after P27 (#134), ST-28 (#135), FX-18 (#139), P28 (#140), FX-13 (#152,
#153), FX-15 (#155) and FX-19 (#156); the web UI items FX-14a (#154) and FX-14b (#161) are `05-web-ui.md`'s.
The slice selection end to end (P27), the odds and non-match slices (P28), the sync from the follows table,
the new job specs and filters and the real restore (FX-13), the clean-up of dead code and the scheduler and
odds decisions (FX-15), the cancel checks of the async bridge path (FX-18), and the follows that always live in
the follows table, team, player and match follows that download, the search by kind, the per-league delete,
the connection state and the readable export names (FX-19) are described as built: sections 1.5, 2.4, 2.7,
2.8, 3.1, 3.2, 3.4, 4.1, 4.3 and 6. The classic views are gone (FX-14b), so every function of the web UI
uses `/api/v1`. References marked `b6caf2f` are to `origin/main` at that commit. Section 11 lists the
corrections (items 102 to 117).

Revised a seventh time on 2026-10-07 after FX-21 (#164), FX-22 (#165), FX-20 (#167) and REN-1 (#168). The
search as the user types and the suggestions from stored data, the recorded spec of a download job with
the body's fields and the names of its follows (FX-20), the settings saved in the web app in a backup and a
restore (FX-22), the P28 models in schema v1 (FX-21) and the package name (REN-1) are described as built:
sections 2.2, 2.7, 4.1 and 6. Since REN-1 the import package is `sofascore_scraper`, and paths of the
package are written with the new name throughout. A `file:line` reference keeps the line it had at the
commit its revision names, when the file was still under `src/`; REN-1 rewrote module paths in place and
kept every file's line count, so the rename itself moved no line. Paths of files removed before the rename
(`src/web/routes/`, `src/ui/`, `src/web/fetch_job.py`, `src/SofaScoreUi.py`, `src/fsutil.py`)
keep the old form. References marked `6f79344` are to `origin/main` at that commit. Section
11 lists the corrections (items 118 to 122).

Revised an eighth time on 2026-10-08 after the end-to-end test against the real SofaScore (2026-10-07 and
2026-10-08, at 1 request per second) and its fix items FX-24 (#170, the web UI), FX-23 (#171, the backend)
and FX-25 (#172, the small follow-ups). The `export` lease, under which an export job runs next to a
download (2.8), the priority lane of the request budget for interactive searches and the per-process
temporary browser profile (2.4), the completeness that counts a finished match's "no data" slice as
resolved while the planner still confirms it (2.7, 3.2), the start reads of `ssc watch` (8.1), the backup
that records its own job as completed (2.7), the gender and national flag of search hits (2.7, 6), the
creation of `config/leagues.txt` and the removed lock file of a settings file (4.3), the English Store
messages and logs (2.4, 2.6, 4.6, 6) and the new job-log codes (2.7) are described as built. References
marked `48e4c4c` are to `origin/main` at that commit. Section 11 lists the corrections from item 123 on.

Revised a ninth time on 2026-10-08 after the live validation against the real SofaScore (the orchestrator's,
2026-10-08: a morning pass over the web UI with one finished match of each sport, and an evening pass with
live matches, both push sources and the CLI next to `ssc serve`) and its fix items FX-26 (#174, the
display of every sport and the morning's findings), FX-27 (#175, the evening's findings) and FX-16 (#176,
the owner's decision on the detail slices of football, basketball and tennis). The route
`GET /events/{id}/extra` (6), completeness and coverage over finished matches only (2.7), the server's time
left of a job (2.8), "/" in the names of team, player and match follows (2.7), what `fetch.only_finished`
decides (2.1), the `events` count of a normalized export (2.7), the English client exceptions (2.6),
the `--sport` filter of `ssc watch` and the closing of a live browser (4.1, 8.1, 8.2), the live status codes
without a type (8.1), the uncounted `empty` row of an unfinished match (3.2, 3.3), the shape and the body
rule of `winning_odds`, the WTA list of `rankings` and the three slice rows of FX-16 (3.1), and what
`live.score_changed` means for a set sport (5.1) are described as built, and the live validation's results
are recorded where the design waited for them (2.7, 3.1, 4.6, 6, 8.1 to 8.4, 8.6, 10, 12). References marked
`43ecdfc` are to `origin/main` at that commit. Section 11 lists the corrections from item 135 on.

Revised a tenth time on 2026-10-09, after the 3.0.0 release (#179: the tag `v3.0.0`, the GitHub Release and
the image `ghcr.io/tunjayoff/sofascore_scraper:3.0.0`; there is no PyPI package) and the first 3.1 work: P30
(#186, the 2.x flags, `/api` routes, environment names and compatibility shims removed), FX-28 (#177), the
repair of the research explorer FX-29 (#181, #182, #184), FX-30 (#180), FX-31 (#185, the slice rows from the
repaired explorer's evidence), FX-32 (#187), FX-33 (#188), B1 (#190), B2 (#191) and B3 (#189). The detail
phase without the fetcher faces (`services/detail_phase.py`, 1.1, 2.2, 2.3, 3.3), the configuration without
the dotenv layer and the 2.x names (4.3), the removed flags and routes (4.7, 6.1), `ssc watch --idle` (4.1,
4.6, 8.4), the team record, the `individual` flag of a sport and the participant filter of an export (2.7, 4.1,
6), the word-start ranking of the stored suggestions (2.7), the per-follow counts of `/status` and a player
follow's match ids (2.7, 3.2, 6), the request counters of a job (2.8), the wait before a confirming "no data"
request (3.2), the connection state by the last recorded outcome (2.7), the access check under an ASGI root
path (6), the slice rows and phases of FX-31 with the evidence and the research explorer they rest on (3.1),
and single-set darts and e-sports game scores (5.1) are described as built.
References marked `216c2f9` are to `origin/main` at that commit. Section 11 lists the corrections from item
148 on.

Conventions used here: "event" is a SofaScore match; "slice" is one data type of an owner entity
(event, season, team, player); "face" is one of Python library, CLI, HTTP API.
The CLI executable is written `ssc` below (final name: decision D2).

---

## 1. Current state: responsibility map

### 1.1 File map

| File (lines) | What it does today | Target home |
|---|---|---|
| `main.py` (402) | argparse with 20 flags (`main.py:52-202`), mode dispatch (`:244-374`), exit-code policy (`:327`, `:348`, `:376-378`, `:384`, `:390`), `os.chdir` to the checkout (`:25-26`), web server start incl. widening allowed hosts to `*` (`:251-257`) | `sofascore_scraper/cli/` (commands), shim stays one release |
| `src/SofaScoreUi.py` (397) | interactive menu loop **and** the headless orchestration used by cron: `run_headless_fetch` (`:311-368`), `update_all_leagues` (`:370-392`), `export_all_to_csv` (`:394-397`); builds the three fetchers (`:93-95`) and creates data dirs (`:79-83`) | orchestration to `services/sync.py`; rest deleted |
| `src/ui/*.py` (2,199) | menus with `input()`; handler methods double as headless steps (`menu_ui.py`, `match_ui.py`); directory-copy backup/restore/clear (`settings_ui.py:297-669`) | deleted (section 7) |
| `sofascore_scraper/season_fetcher.py` (484) | season list request + save (`:49-78`), "current season" heuristic that also looks at files on disk, stale season-id resolution (`:263-287`), reading the list back with three legacy file names (`:423-463`) | `services/listing.py` + Store |
| `sofascore_scraper/match_fetcher.py` (735) | schedule fetch: rounds metadata (`:202-219`), week-based vs event-page strategy (`:176-187`, `:325-388`), round cache policy (`:390-425`), finished filter (`:111-142`, `:189-200`), season summary JSON+CSV writer (`:514-595`), previous-season fallback (`:91-109`) | `services/listing.py` + Store |
| `sofascore_scraper/match_data_fetcher.py` (2,482) | see 1.2 | split across client, Store, pipeline, export, status |
| `sofascore_scraper/utils.py` (696) | the request layer: cancel and wait-notifier ContextVars (`:50-97`), throttle hooks (`:126-135`), sync request (`:299-456`), async request (to `:624`); also module-level settings read at import (`:32-41`) | `sofascore_scraper/client/` |
| `sofascore_scraper/refresh.py` (147) | pure refresh policy: `refresh_due` (`:63-81`), `diff_basic` (`:95-98`), `change_row` (`:112-140`) | stays (domain); driven by `services/refresh.py` |
| `sofascore_scraper/watcher.py` (407) | polling watcher: state reducer `_observe`, tick, JSON state file per sport (`:204-214`), JSONL event append without sequence numbers (`:216-222`), one sport per process (`:146-178`) | `services/live/` |
| `src/web/fetch_job.py` (343) | the web job: its own copy of the full-update orchestration (`:108-232`), breaker activation (`:86-87`), result/exception mapping (`:234-262`), detail phase planning (`:273-343`) | `services/sync.py`; file becomes a small adapter, then disappears |
| `sofascore_scraper/web/jobs.py` (417) | SQLite job history plus an **in-memory** mirror, active id and exclusive slot (`:54-58`), i.e. single-process | persistence in `sofascore_scraper/store/jobs.py` (on `state.db`), behaviour in `sofascore_scraper/jobs/` |
| `sofascore_scraper/web/progress.py` (207) | phase/percent/ETA/failed list; phases hard-wired to seasons/matches/details/export (`:12`) | `sofascore_scraper/jobs/progress.py` |
| `src/web/routes/*` (1,459) | HTTP plus business logic, see 1.5 | `sofascore_scraper/web/api/v1/*` (thin) + services |
| `sofascore_scraper/web/league_sports.py` (110) | league→sport sidecar and inference by globbing `match_details` (`:77-89`) | `services/follows.py` + Store |
| `sofascore_scraper/services/stats.py` (150) | the only service today; walks the tree for counts and sizes (`:84-147`) | `services/status.py` over the catalog |
| `sofascore_scraper/sports.py`, `sofascore_scraper/status.py`, `sofascore_scraper/throttle.py`, `sofascore_scraper/breaker.py`, `sofascore_scraper/bridge_health.py`, `sofascore_scraper/doctor.py` | already single-purpose | stay; wrapped by `sofascore_scraper/client/` and services |

The table is the state at `3ae2599` and is history for most rows. At `b3cb819`: `main.py` (285 lines) is a
shim that hands a subcommand to `sofascore_scraper/cli/main.py`, translates the legacy flags through
`sofascore_scraper/cli/legacy_flags.py` and prints a short help with exit code 2 when no action is given (P19 #119, P25
#125, P26 #131; 4.7). `src/SofaScoreUi.py` and `src/ui/` are deleted (P26). `src/web/fetch_job.py` and
`src/web/routes/*` are deleted: the legacy routes are adapters in `sofascore_scraper/web/api/legacy.py` and the module
state of `routes/common.py` is in `sofascore_scraper/web/deps.py` (P21 #127; 6.1). The CSV flattening and export moved to
`sofascore_scraper/services/export.py` (EX-1 #99, FX-7 #103); `MatchDataFetcher` keeps only forwarding entries for it.

At `216c2f9` the rest of the table is history too (P30 #186). `main.py` (33 lines) only hands its arguments
to `sofascore_scraper.cli.main` with the program name `python main.py`; a 2.x flag is a usage error (exit 2)
that names the command replacing it (`sofascore_scraper/cli/removed_flags.py`; 4.7). `season_fetcher.py`,
`match_fetcher.py`, `match_data_fetcher.py`, `utils.py`, `watcher.py`, `web/jobs.py`, `web/progress.py` and
`web/api/legacy.py` are deleted: the listings run through `services/listing.py` (`ListingService`), the
detail phase through `services/detail_phase.py` (`DetailPhase`, 2.3, 3.3), the request layer is
`sofascore_scraper/client/`, the live service `services/live/`, and the job store and progress are the Store's
and `sofascore_scraper/jobs/progress.py`. `sofascore_scraper/challenge_solver.py`, the alias of the bridge,
went with FX-32 (#187). `services/stats.py` went with FX-34: its one used function, `format_size`, is in
`sofascore_scraper/cli/commands/status.py` (`ssc status`), and the dead legacy-shape functions were deleted.

### 1.2 `sofascore_scraper/match_data_fetcher.py`: twelve responsibilities in one class

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
comes from `SyncResult.breaker` in the service modes; `sofascore_scraper/match_data_fetcher.py` still writes
`APP_EXIT_CODE` on a breaker stop (`:2110` at `f286723`, in the web server process too) and only the
interactive branch of `main.py` still reads it. Both ended with P26 (#131): the terminal UI with its two
orchestrators is deleted, `sofascore_scraper/match_data_fetcher.py` no longer writes `APP_EXIT_CODE`, and no code reads
it. The download ends after the details: the CSV phase left the flow with EX-1 (#99; 2.7).

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
| Falsy body (`0`, `false`, `""`), found by FX-5 (PR #77) | `_fetch_endpoint_async` answers `empty` / `empty` for any falsy body without asking the rule (`if data:`, `sofascore_scraper/match_data_fetcher.py:315` at `9b03c64`), so such a body counts towards "unavailable" | the rule is asked: a falsy body of the wrong shape is `failed` / `parse`. Pinned by `test_falsy_malformed_body_is_still_empty_on_the_async_path`; P13 removes the shortcut |
| Reason of a 404, found by FX-5 | `empty` / `404` | `empty` / `empty` (the helper returns None for a 404). Both count towards "unavailable"; P13 gives a 404 one reason |

Consequence visible to users: the same match downloaded by "select matches" and by "download league" ends up
with different files, and `FETCH_ONLY_FINISHED=false` works on one path only.

### 1.5 Logic in route and UI code that belongs in services

| Location | Logic |
|---|---|
| `routes/matches.py:134-281` | building the match list from summary CSVs with pandas, de-duplication, date parsing, `has_details` by walking `match_details` (`:219-242`) |
| `routes/matches.py:309-358` | "missing details" = CSV ids minus a recursive glob of `basic.json` |
| `routes/matches.py:361-393` | assembling a match from slice files (third copy of `match_data_fetcher.py:534-557`) |
| `routes/matches.py:396-418` | single-event fetch policy (refill, then full), error-to-status mapping by substring (`:416-417`). At `0aa73b4` the mapping was not reached for a blocked upstream: `_fetch_match_basic` swallowed the error, so the route answered 404 for a blocked `/event` and 200 when only the slices were blocked. FX-1 (PR #60) removed the substring mapping: the route keeps the outcome of its requests and answers the typed upstream error of `sofascore_scraper/web/upstream.py` (6) |
| `routes/data.py:103-149`, `:152-182` | backup (zip) and clear |
| `routes/data.py:185-221` | CSV export; a **GET** that generates files when none exist (`:192-198`) |
| `routes/leagues.py:51-86` | remote tournament search incl. parsing SofaScore's payload, with a hard-coded URL (`:54`) |
| `routes/leagues.py:173-187`, `routes/common.py:26-38` | locating and reading season files |
| `routes/settings.py:134-217` | settings persistence, data-dir switch and job-store rebind |
| `routes/scrape.py:87-104` | job creation and thread start; `:111` returns a hard-coded version `"1.0.0"` |
| `web/league_sports.py:77-89` | sport inference by reading `basic.json` files |
| `ui/settings_ui.py:459-549` | **restore**, which exists only in the TUI and only for its directory-copy backups; the web backup is a zip (`routes/data.py:117`) that nothing can restore |
| `match_data_fetcher.py:2323-2482` | coverage report |

At `b3cb819` the route rows are history: every legacy route is an adapter in `sofascore_scraper/web/api/legacy.py` over the
services (P21 #127; 6.1). The CSV export streams `ExportService` and writes no file (EX-1 #99); backup and
clear call `BackupService` and `MaintenanceService` (ST-19 #90); the remote tournament search has a
service, `FollowsService.search_tournaments`, which the v1 route `POST /tournaments/search` uses, while the
legacy `POST /api/leagues/search-remote` keeps its own request. Restore exists only as
`BackupService.restore` behind `ssc backup restore` (ST-24 #109); the TUI and its directory-copy restore are
deleted (P26 #131).

At `b6caf2f`: the search is `FollowsService.search(query, sport=, kinds=)` and finds teams and players as
well (FX-19 #156; `search_tournaments` is a wrapper for tournaments only, 2.7), and a real restore runs as a
v1 job from the Backups screen (FX-13 #152, #153; 6).

At `216c2f9` the legacy adapters are gone with the routes they served (P30 #186): a 2.x `/api/…` path answers
404, and every function of the table above is a v1 route over its service (6, 6.1).

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

All of this ended with P08 (PR #57): `sofascore_scraper/web` imports neither `src.SofaScoreUi` nor `src.ui`, and
`tests/test_sync_service.py::test_web_imports_nothing_from_the_terminal_ui` keeps it so. Four test modules
patched the name by then (the fourth, `tests/test_web_hardening.py`, came with PR #43); they now replace
`fetch_job.build_context` and `export_all_csv`. The headless paths of `main.py` followed with P10 (PR #64);
the terminal menu keeps its own wiring in `src/SofaScoreUi.py` until P26. The section is history at
`b3cb819`: `src/SofaScoreUi.py` and `src/ui/` are deleted (P26 #131) and `tests/test_no_terminal_menu.py`
checks that no module imports them; the web job is `run_fetch_job` in `sofascore_scraper/web/api/legacy.py` (P21 #127),
and tests patch `legacy.build_context` and `sofascore_scraper.services.export.export_all_csv`. Since P30 (#186)
that module is deleted; the web's downloads are v1 jobs (`sofascore_scraper/web/api/v1/jobs.py`) that run
`SyncService` under the job manager.

### 1.7 Other findings that shape the design

- **Logs go to stdout.** `sofascore_scraper/logger.py:39` installs `RichHandler` with the default console, whose stream is
  `sys.stdout`. `main.py:221` prints watch events to the same stream. Since PR #24 a plain stream handler
  with the file format is used when stdout is not a terminal; the stream is still stdout, so log lines, user
  text and the watcher's JSON event lines share it (pinned by the CLI goldens of G-03). Since P19 (#119)
  the log lines of every CLI process, the translated `main.py` runs included, go to stderr and stdout
  carries only the result; P25 (#125) does the same for `serve` and uvicorn's access lines.
- **`--config` is a dead flag.** `ConfigManager` is a singleton that ignores arguments after first
  construction (`config_manager.py:46-67`); `sofascore_scraper/utils.py:41` constructs it at import, which happens before
  `main.py:296` passes the path. G-03 pins it: a run with `--config` equals the plain full update. The
  `main.py` line numbers of this section and of 4.7 are from `3ae2599` and no longer exist; the behaviour
  they describe is unchanged. Since P10 the flag is dead explicitly: `main.py` constructs `ConfigManager()`
  at import, which the import of the terminal UI used to do as a side effect. Since P19 (#119) `--config`
  names the config file for `main.py` as for `ssc` (a broken file exits 2); a `.txt` path is still not
  read as a leagues file, only a warning is new (4.7).
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
  the default lives in the Settings model (`sofascore_scraper/config/settings.py:39` at `f286723`). What is left: the two
  literals in the bridge (`sofascore_scraper/challenge_solver.py:43` and `:432` at `f286723`; P24. The transport hands the
  bridge full URLs, so they are not used for requests of the client) and the connection test of
  `src/web/routes/scrape.py` (`:164`), which calls the bridge directly with a relative path and so always
  uses the bridge's literal base (P21).
- **Jobs are single-process.** The active job, cancel flag and exclusive slot live in memory
  (`web/jobs.py:54-58`, `:239-241`); constructing a second `JobStore` marks every running row interrupted
  (`:129-148`). The CLI does not use the job store at all, so a cron run and a web job can write the same
  data directory concurrently; only the request budget is shared (`throttle.py`). P11 (PR #69) ended this
  for the web job and for the headless downloads and refreshes of `main.py`: they run through the job
  manager, which takes the `writer` lease and reads the cancel flag from the job row (2.8). The terminal
  menu used neither the job store nor a lease until it was deleted (P26 #131); every command of the new CLI
  that writes runs as a job or takes its lease itself (4.6).
- **Bridge health is per process** (`bridge_health.py:24-26`), so a fresh `status` process cannot know
  whether the server is blocked. Since P11 every transition is also written to `state.db` (2.3); no
  command reads the stored value yet. At `b3cb819` that is still so: `ssc status` (P19) has no bridge field, and
  `GET /api/v1/status` reports the snapshot of its own process, not the stored key (section 11).
- **Typed outcomes stop at event slices.** Season lists return `[]` on any failure (`season_fetcher.py:63-67`),
  rounds return `None` on any exception (`match_fetcher.py:480-482`), the watcher's 404 branch is unreachable
  (`watcher.py:188-191` with `utils.py:315-321`).
- **Paths are relative to the working directory** (`paths.py:11-22`, `config_manager.py:295`), held together
  by `os.chdir` in `main.py:25-26`. A library cannot rely on that.
- **Round listings already hold every status.** Round files are saved unfiltered
  (`match_fetcher.py:471`); only the summary and the event pages are filtered. In 184 locally stored football
  events, the object in a round listing lacks only `venue`, `referee`, `attendance`, period defaults and a few
  promo flags compared with `/event/{id}`; status, teams, scores and start time are present.
- **Defaults.** At `3ae2599` the default language was Turkish (`sofascore_scraper/i18n.py:13`, `:36`) and the default
  request rate was 10 per second per concurrent request, 100 with default settings (`throttle.py:55-57`). Both
  changed since: the rate is 5 requests per second in total (PR #33, `sofascore_scraper/throttle.py:54` at `0aa73b4`) and
  the language is English unless the system language is Turkish (PR #39, `sofascore_scraper/language.py`).
- **Pull requests merged since the baseline.** #24 (`feat/log-files-diagnostics`), #23
  (`fix/blocked-state-ux`) and #32 (`chore/post-wave2`), which this design assumed, are merged. Together they
  changed `main.py`, `sofascore_scraper/config_manager.py`, `sofascore_scraper/logger.py`, `sofascore_scraper/utils.py`, `sofascore_scraper/season_fetcher.py`,
  `sofascore_scraper/doctor.py`, `sofascore_scraper/web/app.py`, four route modules (`api.py`, `leagues.py`, `scrape.py`, `settings.py`),
  `tests/conftest.py`, the installers, the CI workflow and the changelog, and added `sofascore_scraper/diagnostics.py`,
  `sofascore_scraper/redact.py`, `sofascore_scraper/web/upstream.py` and the routes of `src/web/routes/diagnostics.py`. PR #43 (web
  security hardening) followed; what it changed is in `03-implementation-plan.md` section 1.
- **Settings since P09 (PR #56).** `ConfigManager`'s getters read the active `Settings` (4.3). The module
  constants frozen at import and the modules that read `os.environ` themselves are unchanged; they are
  reached through the environment bridge of 4.3, so `FETCH_ONLY_FINISHED` changed in the UI still needs a
  restart.

---

## 2. Target architecture

### 2.1 Layer rules (enforced by an import-linter test)

1. Faces (`sofascore_scraper/cli`, `sofascore_scraper/web`, `sofascore_scraper/api.py`) import only `sofascore_scraper/services`, `sofascore_scraper/jobs`, `sofascore_scraper/config`, `sofascore_scraper/errors`.
   They contain argument parsing, serialisation and nothing else.
   As built so far (P18, PR #65) the new CLI also imports `sofascore_scraper.doctor`, `sofascore_scraper.diagnostics`, `sofascore_scraper.sports`,
   `sofascore_scraper.logger` and `sofascore_scraper.redact`, and for `config init --from-legacy` `sofascore_scraper.config_manager` and
   `sofascore_scraper.web.league_sports`: no service exists for these yet. The status and follows services replace them
   (P19, P21, P26). Command modules keep their module-level imports light; a test allows only `sofascore_scraper.cli.*`,
   `sofascore_scraper.errors`, `sofascore_scraper.exceptions`, `sofascore_scraper.language`, `sofascore_scraper.sports` and `sofascore_scraper.version` at import time.
   The `events` command (P22, PR #73) imports `sofascore_scraper.sinks` and `sofascore_scraper.store` inside its function: it reads the
   stream log itself, because there is no `LiveService.events` yet. The v1 routes (P20, PR #74) import
   `sofascore_scraper.sports`, `sofascore_scraper.bridge_health`, `sofascore_scraper.throttle`, `sofascore_scraper.redact` and, for the error classes, `sofascore_scraper.store`
   (`sofascore_scraper/web/errors.py:39`, `sofascore_scraper/web/api/v1/settings.py:46` at `e0bae0c`); the query and status services
   replace the direct reads (P21).
   The `watch` command (P23, PR #91) imports `sofascore_scraper.sinks`, `sofascore_scraper.config.loader`, `sofascore_scraper.store` and
   `sofascore_scraper.services.live.supervisor` inside its function (`sofascore_scraper/cli/commands/watch.py:112-115` at `9b03c64`): it
   opens the Store and hosts the dispatcher itself, because the context has no Store field and no live
   service (2.3). The legacy routes that read through the Store since RD-1, RD-2, RD-4 and RD-5 open it with
   `open_store` and hand it to the service (`src/web/routes/matches.py:101`, `src/web/routes/data.py:34`).
   Two modules below the services import a service, the reverse direction: `sofascore_scraper.match_data_fetcher` imports
   `sofascore_scraper.services.query` (RD-1) and `sofascore_scraper.season_fetcher` imports `sofascore_scraper.services.tournaments` (RD-5). It lasts
   until the fetchers leave the context (P15). Both modules are deleted since P30 (#186), so the reverse
   direction is gone.
   As built at `b3cb819`. The web face has a test of its own since P21 (#127): `tests/test_layers.py`
   allows `sofascore_scraper/web` to import `sofascore_scraper.services`, `sofascore_scraper.jobs`, `sofascore_scraper.config`, `sofascore_scraper.errors` and itself, plus the
   modules of `WEB_ALSO_IMPORTS`, each with its reason: 22 modules, most of them for the legacy adapters
   (`sofascore_scraper/web/api/legacy.py`), which P30 deletes. The list is a ratchet: a new module fails, and so does a
   stale entry. At `216c2f9` it has 16 modules (`tests/test_layers.py:181-198`): P30 (#186) deleted the
   adapters and the entries only they needed. The web layer does not import `sofascore_scraper.sinks` (decision D11's test); `GET /api/v1/sinks` reads
   through `sofascore_scraper/services/sink_status.py`. The CLI face has no such test yet (P21 left `sofascore_scraper/cli` to P19,
   and P19 did not add it): its data commands (P19 #119) import the Store, `sofascore_scraper.sinks`, the job manager
   and the services inside their functions, as `watch` does.
2. Only `sofascore_scraper/client` sends requests to SofaScore. Only `sofascore_scraper/store` touches `DATA_DIR`, including every SQLite
   file and every lock file in it.
3. Services never print, never read `os.environ`, never call `sys.exit`. They take a `ServiceContext`,
   return typed results and raise `PlatformError` subclasses.
   As built by P08 the services do not print, with three leftovers of the transition: `build_context`
   writes `NO_COLOR` into `os.environ` when `USE_COLOR` is off (carried over from the terminal UI's
   constructor; it went with the terminal progress bar: at `b3cb819` the logger sets it at process start
   and the context does not touch the environment), `SyncService` reads one job-log text
   from the locale files (`fetch_zero_matches`, as today), and the storage error
   is `sofascore_scraper.exceptions.StorageError` until `sofascore_scraper/errors.py` arrives with P18.
   P11 (PR #69) did not replace that text by a code: `JobHandle.log` accepts `code=`
   (`sofascore_scraper/jobs/manager.py:153` at `e0bae0c`), but `SyncService` still logs the translated text
   (`sofascore_scraper/services/sync.py:425`), so it reaches the job log in the server's language. P13 passes
   `code="fetch_zero_matches"`.
   RD-4 (PR #78) added a fourth leftover: `sofascore_scraper/services/status.py` reads `FETCH_ONLY_FINISHED` from the
   environment at call time (`only_finished_setting`, `sofascore_scraper/services/status.py:56-61` at `9b03c64`), as the
   Settings route does, and the legacy match lists take it from there too. The writers read
   `sofascore_scraper.utils.FETCH_ONLY_FINISHED` once at import, so after a change in the web UI the counts and lists follow
   at once and the downloads only after a restart. P30 (#186) switched the read to `fetch.only_finished` of
   the Settings (`only_finished_setting`, `sofascore_scraper/services/status.py:91-99` at `216c2f9`), so the
   services read no environment variable for it. Since ST-27 (#129) the setting no longer changes what is stored: matches of every status are
   stored and details are fetched once a match has finished, so the setting applies at read time only (the
   legacy lists and the dashboard), and the restart no longer matters for it. Corrected by FX-26 (#174;
   `43ecdfc`): it also decides what a league download fetches. The sync's detail phase takes its matches
   from `DetailPhase.candidates` (`sofascore_scraper/services/detail_phase.py:80-91` at `216c2f9`; until P30
   `MatchDataFetcher.collect_detail_match_ids`), which calls `QueryService.detail_candidates` with
   `only_finished_setting()`, so with the setting on (the default) only the finished matches of a followed
   league, those with stored details and those of unknown status get their details. Team, player and match
   follows, the v1 event list and the Matches screen are not affected. Its description in
   `sofascore_scraper/config/settings.py:159-163` and the Settings page say so since FX-26.
4. Domain modules (`sports`, `status`, `refresh`, `slices`, `schema`) are pure and import nothing above them.
   As built (SC-1, PR #72): `sofascore_scraper/schema` imports `sofascore_scraper.sports`, `sofascore_scraper.status` and `sofascore_scraper.refresh` at run time
   and names the Store's row classes only under `TYPE_CHECKING` (`sofascore_scraper/schema/mappers.py:53-54` at
   `e0bae0c`), which is how "pure" and "mappers from Store rows" fit together. `tests/test_layers.py`
   checks the Store's imports only; the purity of `sofascore_scraper/schema` is checked in `tests/test_schema_v1.py`
   (`test_package_imports_only_domain_modules` and a subprocess test that pins the exact set of `sofascore_scraper`
   modules that importing the package loads).
5. `sofascore_scraper/client` and `sofascore_scraper/store` do not import each other. `sofascore_scraper/jobs` uses `sofascore_scraper/store` for persistence and
   leases and contains no SQL and no access to the data directory. The test enforces what imports can show:
   no face module and no `sqlite3` in `sofascore_scraper/jobs` (`tests/test_jobs_model.py`); the manager may use `os` for
   the pid.

### 2.2 Module layout

```
sofascore_scraper/          (src/ until REN-1, #168)
  api.py                    public Python API: Platform, re-exports of result types
  errors.py                 PlatformError hierarchy and the code table (2.6)
  config/
    settings.py             Settings (frozen dataclasses), defaults
    loader.py               file + env + flags merge, validation, legacy .env / leagues.txt import
    schema.py               JSON Schema of the config file (for `describe config`)
    overrides.py            the one writer of CONFIG_DIR/overrides.json (P20; 4.3)
  client/
    __init__.py             Client, request_context
    transport.py            curl transport + bridge fallback   (from utils.py:299-624)
    context.py              RequestContext: cancel, wait notifier, breaker (the ContextVars of utils.py:61-97, breaker.py:252-268)
    endpoints.py            every URL template in one place
    bridge.py               BrowserBridge                      (from challenge_solver.py)
    (throttle.py, breaker.py, bridge_health.py stay at the package root and are re-exported; moving them is cosmetic)
  store/                    designed in 01-storage.md; services use its public API directly
  sports.py status.py refresh.py                 domain
  slices.py                 Outcome, presence predicates (match_data_fetcher.py:65-89, :559-637)
  schema/                   normalized schema v1 (SC-1): models.py, mappers.py (from catalog rows and the dicts
                            that store.derive returns, not from payloads), jsonschema.py
  services/
    context.py              ServiceContext, build_context(settings), Clock
    planning.py             targets → WorkItems; need computation (from match_data_fetcher.py:813-854)
    pipeline.py             the one fetch pipeline (section 3)
    listing.py              season lists and schedules as pipeline work
    sync.py                 SyncService
    detail_phase.py         DetailPhase: the detail phase of a job on the pipeline (P30)
    refresh.py              RefreshService
    live/                   supervisor.py, reducer.py, poll_source.py, push_source.py (page listening, frame
                            parser and merge), direct_source.py (opt-in), arbiter.py
    export.py               ExportService
    backup.py               BackupService
    maintenance.py          MaintenanceService
    status.py               StatusService (health, summary, coverage); doctor stays sofascore_scraper/doctor.py
    follows.py              FollowsService
    query.py                QueryService (read side)
    tournaments.py          season lists, schedule pages and sports of tournaments: functions on a Store (RD-5)
  jobs/
    model.py                Job, JobKind, JobState, Origin, ErrorInfo (P07); JobEvent (P11)
    manager.py              JobManager, JobHandle, JobOutcome, JobNotActive (P11)
    progress.py             JobProgress               (from web/progress.py)
    scheduler.py            optional in-app scheduler
  sinks/                    (P22)
    __init__.py             build_sinks, dispatcher_for, drain_at_exit, validation of the sink options
    base.py dispatcher.py stdout.py file.py webhook.py
  cli/
    main.py output.py exit_codes.py signals.py removed_flags.py commands/*.py
  web/
    app.py errors.py deps.py sse.py (job events only) security.py (PR #43)
    openapi.py              the committed OpenAPI document of v1 (P20; section 6)
    api/__init__.py         prefixes, and the v1 successor of every legacy route (6.1)
    api/v1/*.py             one router per resource; no live router (P20: meta.py, jobs.py, settings.py)
```

What of this layout exists at `e0bae0c`: `errors.py`, `config/` (with `overrides.py` since P20, PR #74),
`client/` (the bridge is still `sofascore_scraper/challenge_solver.py`), `store/`, the domain modules, `schema/` (SC-1,
PR #72), `services/context.py`, `sync.py`, `export.py` and `maintenance.py`, `jobs/model.py`, `manager.py`
(P11, PR #69) and `progress.py`, `sinks/` (P22, PR #73), `cli/main.py`, `output.py`, `exit_codes.py` and
the command modules `meta.py` and `events.py`, and in `web/` everything listed except `api/legacy.py`: the
legacy routes are still the modules of `src/web/routes/` (P21 turns them into adapters). Not there yet:
`api.py`, the other service modules, `jobs/scheduler.py`, `cli/signals.py` and `cli/legacy_flags.py`.

What was added by `9b03c64`: `services/query.py` (RD-1, PR #80; the match lists with RD-2, PR #89),
`services/status.py` (RD-4, PR #78), `services/tournaments.py` (RD-5, PR #79), `services/backup.py` and
`clear` in `maintenance.py` (ST-19, PR #90), `services/live/` with `supervisor.py`, `reducer.py` and
`poll_source.py` (P23, PR #91; the push sources and the arbiter are P24 and P31), and the command module
`cli/commands/watch.py`. `tournaments.py` is not a class under `QueryService`, as the layout first said: it
is a module of functions that take a Store (`seasons_of`, `season_lists`, `schedule_pages`, `has_matches`,
`sport_of`; `sofascore_scraper/services/tournaments.py:89-165` at `9b03c64`), because `SeasonFetcher`, a member of the
context, calls it. `services/stats.py` stays as the
forwarder that turns `StatusService.summary()` into the old response keys for the web routes and the
terminal menu (2.7); only its per-league disk sizes, which the terminal menu alone shows, still walk the
league directories.

What was added by `b3cb819`. In `services/`: `export.py` as a class with the legacy profile and the
normalized datasets (EX-1 #99, FX-7 #103, SC-2 #130); `verify`, `restore` and `prune` in `backup.py` (ST-24
#109); `migrate`, `rebuild_catalog`, `verify_catalog` and `reconcile_catalog` in `maintenance.py` (ST-23
#110); `follows.py` (P21 #124); two modules the layout did not name, `data_jobs.py` (the export request of
API v1 and its check, P21 #126, SC-2) and `sink_status.py` (the per-sink state behind `GET /api/v1/sinks`,
P21 #122); and the pipeline modules `planning.py`, `pipeline.py` and `listing.py` (section 3). `jobs/` has
`scheduler.py` (P29 #128). `cli/` has `signals.py` and `legacy_flags.py` and the command modules `sync`,
`export`, `follows`, `jobs`, `status` and `data` (P19 #119), `backup` (ST-24), `migrate` and `catalog`
(ST-23) and `serve` (P25 #125). `web/` has `api/legacy.py` (P21 #127) and `api/proxy.py` (the proxy-password
rule, used by v1 too), and `api/v1/` has `auth.py`, `tournaments.py`, `events.py`, `records.py` (the pydantic
mirrors of the schema records), `follows.py`, `exports.py`, `backups.py`, `downloads.py` and `diagnostics.py`
next to P20's three. `sofascore_scraper/web/jobs.py` and `sofascore_scraper/web/progress.py` are forwarders of a few lines to the Store's
job store and `sofascore_scraper/jobs/progress.py`. Not there yet: `api.py` (the library face) and
`services/refresh.py` (the refresh is `SyncService` with mode `refresh`). `sofascore_scraper/services/stats.py` stays as
the forwarder of the legacy dashboard; its `league_stats` and `system_stats` lost their last caller in the package
with the terminal menu (P26).

What changed by `216c2f9` (P30 #186, FX-32 #187). Removed: `api/legacy.py` and the legacy response shapes of
`QueryService`, `cli/legacy_flags.py` (replaced by `cli/removed_flags.py`, the table that turns a 2.x flag
into a usage error naming its command), the forwarders `web/jobs.py` and `web/progress.py`, the fetcher
faces `season_fetcher.py`, `match_fetcher.py` and `match_data_fetcher.py`, `utils.py`, `watcher.py` and
`challenge_solver.py`, the bridge's alias (FX-32; the research scripts import `client.bridge`). Added:
`services/detail_phase.py`, which the layout did not name: the detail phase of a job (plan from the catalog,
download through the pipeline, the need cache, the breaker result and the refresh listener), called by
`SyncService` and by `MaintenanceService.recheck_unavailable`. Modules the layout does not show and that
exist: `services/follow_sync.py` (team, player and match follows, FX-19), `services/job_spec.py` (the
recorded job spec, FX-20) and `services/owner_data.py` (P28). Still not there: `api.py` and
`services/refresh.py`.

The package was `src/` during the build-out. The owner decided on 2026-10-03 that it becomes
`sofascore_scraper` (decision D1), and REN-1 (#168, `6f79344`) renamed it in one pull request: the folder
moved with its history, every import and module-path string followed, the console script is
`ssc = "sofascore_scraper.cli.main:main"`, and the Docker image runs `pip install --no-deps -e .`, so it has
`ssc` on its PATH. There is no `src` alias: `import src` fails, and a checkout needs `pip install -e .` again
after the update, because the old `ssc` script still imports `src`. `sofascore_scraper` is an implicit
namespace package, as `src` was (no top-level `__init__.py`, so no `sofascore_scraper.__version__`; the
version is read by `sofascore_scraper/version.py`); `api.py`, the library face of this layout, is still to
come. `python -m sofascore_scraper.cli.main` is the same as `ssc`; there is no `python -m sofascore_scraper`.
Paths of files removed before the rename (`src/web/routes/`, `src/ui/`, `src/fsutil.py`, …) keep their old
form in this document.

### 2.3 Composition

```python
# sofascore_scraper/services/context.py
@dataclass(frozen=True)
class ServiceContext:
    settings: Settings
    store: Store            # sofascore_scraper.store.Store
    client: Client | None   # None when opened read-only
    jobs: JobManager
    clock: Clock

def build_context(settings: Settings, *, readonly: bool = False) -> ServiceContext: ...
    # opens the Store, applies config follows (store.follows.apply(..., origin="config")), mirrors the legacy
    # league files, and wires client health changes to store.runtime.set("bridge_health", ...)

# sofascore_scraper/api.py: the library face
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

The block above is the target. The current stage, as built by P08 (PR #57), ST-17 (PR #62), P11 (PR #69),
P30 (#186) and FX-33 (#188; `sofascore_scraper/services/context.py` at `216c2f9`):

```python
@dataclass(frozen=True)
class ServiceContext:
    config: ConfigManager                 # leagues, thresholds, data directory
    data_dir: str
    client: Client | None = None          # P11; None when the base URL is not http(s)

    @property
    def store(self) -> Store: ...         # P11: open_store(self.data_dir), opened at the first access
    @property
    def jobs(self) -> JobManager: ...     # P11: JobManager(JobStore.for_store(self.store))

def build_context(config_manager: ConfigManager, *, data_dir: str | None = None) -> ServiceContext: ...
    # brings the follows table up to date and builds the client; creates no directory: the Store creates
    # the data directory on its first open, and one that cannot be created is a StorageError (StoreError)
    # at the first store access
```

Until P30 the context also carried the three fetchers (`season_fetcher`, `match_fetcher`,
`match_data_fetcher`; fields first, `cached_property` members since P15), and `build_context` created the data
directory with its 2.x subdirectories (four, then `match_details` and `datasets` after FX-15), raising
`OSError` when one could not be created. The bullets below describe that path where they name the fetchers.

- It carries the fetchers, because today's flow is theirs. There is no `settings` and no `clock` field and
  no `readonly` argument. Since P11 it has a `client` field, and `store` and `jobs` as properties.
  At `b3cb819` the three fetchers are no longer fields: they are `cached_property` members, built at the
  first access and kept for the life of the context (P15 #117; `sofascore_scraper/services/context.py:72-89`), so
  building or importing a context loads none of them. `build_context` still creates the data directory and
  its four subdirectories, brings the follows table up to date and builds the client; `NO_COLOR` is set by
  the logger at process start, no longer by the context. At `216c2f9` the fetchers are gone (P30): the sync
  runs the listings through `ListingService` and the details through a `DetailPhase` built per job
  (`sofascore_scraper/services/sync.py:355-357`), and `build_context` creates no directory (FX-33).
- The Store is opened lazily (P11). `store` and `jobs` are properties, not fields
  (`sofascore_scraper/services/context.py:66-77` at `e0bae0c`): building a context opens no Store, and the first access
  to `ctx.store` creates what is missing under `.meta/` (`schema.json`, `state.db`, `catalog.db`). The
  first version of this section had `build_context` open the Store. Two tests of earlier items pin the lazy
  form: a context without follows creates no `.meta/` and survives an unusable `state.db`
  (`tests/test_store_follows.py`, ST-17), and `--headless --csv-export` opens no Store
  (`tests/test_cli_services.py`, P10). What opens it today: the headless downloads and refreshes of
  `main.py` through `ctx.jobs`, a web job, and a bridge-health transition (below).
  The second test no longer holds: since EX-1 (#99) the export reads the catalog, so `--headless
  --csv-export` (since P19 `ssc export`) opens the Store and creates `.meta/` in a folder that never had
  it; the test still passed at EX-1 because it patched `export_all_csv`, and P19 rewrote it to expect the
  open Store. The export does not build a context at all since P19.
- `ctx.jobs` is a new `JobManager` at every access, over the one job store of the open Store
  (`JobStore.for_store`; since ST-11, PR #75, the same object is also the lazy property `Store.jobs`). The
  CLI runs its jobs through it. The web does not: its routes and its job use the process-wide job store of
  `src/web/routes/common.py`, which is created when that module is imported (2.8).
- A web job opens the Store of its data directory, best effort (P11): `fetch_job._open_store`
  (`src/web/fetch_job.py:85-96` at `e0bae0c`) touches `ctx.store` once before the service runs, so
  `.meta/schema.json` and `.meta/catalog.db` appear next to `state.db` at the first job, and a directory
  used only through the web UI is a Store for `open_store(create=False)`. If the Store cannot be opened
  (newer layout, busy, corrupt file) a warning is logged and the job runs as before. The v1 job body does
  the same (`sofascore_scraper/web/api/v1/jobs.py:303-308`).
- Bridge health (P11). `build_context` builds a `Client` with one module-level function as its
  `on_health_change` callback (`_record_bridge_health`, `sofascore_scraper/services/context.py:132-143`). The health
  source keeps a callback once, so the function is added once however many contexts are built, and no
  `client.close()` is needed when a context is replaced (the first version asked for it). The function
  writes the snapshot to `store.runtime` under the key `bridge_health` in the data directory of the latest
  context, which opens the Store there if it was not open; a write that fails is logged at DEBUG and does
  not disturb the request. Nothing reads the key yet (`/api/v1/status` reports this process's own
  snapshot); `ssc status` was to be its reader (P19), and P19 built `status` without a bridge field, so
  the key still has no reader at `b3cb819` (section 11). The fetchers do not send their requests through
  `ctx.client` yet (P13).
- A context belongs to one job or one request: the fetchers carried state (the job cache, the last request
  counts); since P30 that state is the job's `DetailPhase` (its need cache, the breaker result). `build_context` is called once per job and once per season-refresh or CSV-export request.
- Follows (ST-17, PR #62). `build_context` applies the `[[follow]]` entries of the config file with origin
  `config` and then mirrors the league files (until FX-33 right after the data directories were created). It does not
  open the Store for that: it calls `apply_follows(data_dir, ...)`, which updates the table over a
  short-lived connection (`01-storage.md` 2.3). `ConfigManager` does the same for the league files after
  every load and change. P11 left this unchanged: `apply_follows` did not become `store.follows.apply`,
  because that would open the Store in every context. Only a config file with `[[follow]]` entries makes
  `build_context` create `state.db`.
- Services that take a Store, not a context (RD-1, RD-4, ST-19; `9b03c64`). The block above shows every
  service as a member of `Platform` built on the context. As built, the read services and the two data
  operations are constructed on a Store: `QueryService(store)` (`sofascore_scraper/services/query.py:98-102`),
  `StatusService(store)` (`sofascore_scraper/services/status.py:177-181`), `BackupService(store)`
  (`sofascore_scraper/services/backup.py:25-29`) and `MaintenanceService(ctx=None, *, store=None)`
  (`sofascore_scraper/services/maintenance.py:60-65`), where `clear` needs only the Store and `recheck_unavailable` still
  needs the context for its fetcher. The reason is the cost and the side effects of a context: importing
  `sofascore_scraper/services/context.py` loads the three fetchers and the request layer, which a dashboard route must not
  do, and `build_context` creates `seasons/`, `matches/`, `match_details/` and `datasets/`, which would change
  what a clear leaves behind. Since P30 and FX-33 neither holds (no fetchers, no directories), and the
  services are still built on a Store; `recheck_unavailable` still takes a context, for its Store and the
  configuration of its `DetailPhase`. The context still has no `store` field (`ctx.store` is the property above),
  and nothing wires `query`, `status` or `backup` into it; the faces build the service on
  `open_store(data_dir)` at the call. Whether `Platform` (library face) holds services built on its Store or
  on a context is decided with the library face.
  At `b3cb819` the services of this revision follow the same pattern: `ExportService(store)` (EX-1),
  `FollowsService(store, legacy, *, config_file)` (P21 #124), and the functions of `data_jobs` and
  `sink_status` take a Store. The web builds them on `deps.store()`, `open_store` of the configured data
  directory (`sofascore_scraper/web/deps.py:87-94`), and `deps.follows_service()` passes the writer of the legacy league
  files and whether a config file is active. `MaintenanceService(store=...)` serves clear, migrate and the
  catalog operations; only `recheck_unavailable` still needs a context.
- Reading opens the Store. Since RD-1, RD-2, RD-4 and RD-5 the legacy GET routes of match detail, match
  lists, dashboard and statistics, leagues and season lists open the Store of the data directory, so the
  first such request creates `.meta/schema.json` and `.meta/catalog.db` when they are missing and every open
  reconciles the catalog with the files (bounded by decision S17 since ST-19). A route that reads therefore
  may create and update those derived files (6.1).
- What the later items add. P13: the pipeline uses the context's client. P15: the fetchers leave the
  context, and `settings` takes the place of `config`. `readonly` and the `Platform` class come with the
  library face. Whether the Store is ever opened eagerly is decided with the library face; nothing needs
  it today.
- Tests fake the context with a `SimpleNamespace` that has `config`, `season_fetcher`, `match_fetcher` and
  `match_data_fetcher` (`test_breaker_phases`, `test_job_progress`, `test_storage_errors`,
  `test_sync_service`), so a new field that a service reads has to be added to those fakes. Since P30 the
  fakes are `tests/sync_fakes.py` and `tests/detail_fetch.py`, which replace the detail phase and the
  listings through the seams of `SyncService`.
- The web imports the fetchers and the request layer inside functions. A module-level import of
  `sofascore_scraper.services.context` in a route module would load `curl_cffi`, `rich`, `tqdm` and the three fetchers at
  web start-up; `routes/data.py` and `routes/leagues.py` keep the import at function level. Since P21
  (#127) that is `sofascore_scraper/web/api/legacy.py` (`legacy.build_context`, the name tests replace); since P15 the
  context no longer loads the fetchers at import, and `tqdm` is no longer a dependency (P26 #131). Since P30
  the v1 job routes build the context inside the job's function.

### 2.4 Client

```python
# sofascore_scraper/slices.py — one outcome type for client, pipeline and Store (01-storage.md 2.3)
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
class ClientSettings:               # sofascore_scraper/client (P05); see "As built" below for its relation to 4.3
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

The block is the client as built by P05 (PR #48, `sofascore_scraper/client/__init__.py` at `f286723`). The first version of
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
  `sofascore_scraper/slices.py`; `fetched_at`, `via` and `meta` are left None by `from_error` and filled by the client.
- A 200 whose body is empty (`{}`, `[]`) is `empty` with reason `empty` and the body in `data`. Whether a
  non-empty body carries data for its slice is decided by `slices.slice_body_state(key, body)`
  (`sofascore_scraper/slices.py:225` at `9b03c64`; FX-5, PR #77), which has three answers, not the two of a presence
  predicate: `BODY_DATA` (`ok`), `BODY_NO_DATA` (`empty` / `empty`, the body kept; `{"pointByPoint": []}` is
  no data since FX-5) and `BODY_MALFORMED` (a body of an unexpected shape). As built by the fix commit of
  FX-5 (`5456182`), `MatchDataFetcher._answered_outcome` maps a fetched malformed body to `failed` / `parse`
  (`sofascore_scraper/match_data_fetcher.py:319-345`): it is not counted as an empty answer, not written to disk, not
  reported to the circuit breaker, and requested again on the next run. Before FX-5 the presence predicate
  raised on such a body: the async path reached the same result through the exception (reason `other`), the
  async refill retried the match three times and reported it failed, and the sync path ended the whole batch
  (`fetch_matches_batch` caught only `StorageError`) and made the single-match route answer 500; now the
  match is saved without that slice. The first commit of FX-5 reported such a body as `empty` / `empty` for
  a moment; the fix commit replaced that before the merge. P13 keeps the mapping when it moves the code
  (`tests/test_malformed_slice_body.py` pins it on both paths). Two divergences are left for P13 (1.4): the
  async path answers `empty` / `empty` for any falsy body (`0`, `false`, `""`) without asking the rule, and
  a 404 has the reason `404` on the async path and `empty` on the sync path. A 200 whose body
  is JSON `null` is `failed/other`: it is the reachable form of "the request layer returned None without an
  exception" (the line the first version cited, `utils.py:624`, was `:629` on main and cannot be reached
  with at least one retry).
- Two request bodies remain. The first version said "one transport implementation (async); `get_sync`
  drives it on the bridge's background loop; the duplicated sync body is deleted". P05 moved the sync body
  to `sofascore_scraper/client/transport.py` instead: the G-01 goldens pin sync requests as such, and five callers still
  use `make_api_request`. `get_sync` uses that body and opens no session; every request is its own
  connection. The sync body can go when its callers have moved to the client (the fetch paths in P13, the
  watcher's default fetch in P23). P23 (PR #91) kept it: the live service's default fetch is still
  `make_api_request(api_url(path), raise_on_failure=True)` (`sofascore_scraper/services/live/supervisor.py:613-628` at
  `9b03c64`), because the client has no "blocked" report for that path (8.1).
- A curl session belongs to the event loop that opened it, and every job runs its own `asyncio.run`. The
  client therefore keeps one session per loop, opened (with the warm-up request) by the first `get` on that
  loop. `aclose()` closes the running loop's session only; a job opens and closes its session with
  `async with client:` inside its loop. With an open breaker or a cancelled job no session is opened: the
  warm-up is a request too.
- Paths are relative to the API root and start with `/` (`ValueError` otherwise); `endpoints.py` builds them
  (`event_slice(key, id)` reads the path from the slice table of `sofascore_scraper/sports.py` and raises `KeyError` for an
  unknown key), and the base is added only in `Client.url`. `ClientSettings` rejects a base that is not
  http(s). `ClientSettings.from_environment()` takes the base from `transport.base_url()`, which is read
  once at import: a base URL changed on the Settings page still needs a restart.
- The throttle stays where it is: every request reserves a slot in the shared budget (`sofascore_scraper/throttle.py`; the
  curl path in `sofascore_scraper/client/transport.py`, the bridge in `sofascore_scraper/client/bridge.py`). The default rate is 5 req/s
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
  solve (up to 90 s) or for the page's `fetch()` (up to 20 s) still cannot be interrupted on the sync path.
  (Out of date: P24 made the sync path interruptible and FX-18 the async path; see the FX-18 bullet below.)
- Requests that already waited for the request semaphore could still be sent after a stop, until FX-9.
  `_request_async` checked for a cancel before it waited for the semaphore and not after
  (`sofascore_scraper/client/transport.py:553` and `:564` at `e0bae0c`), so a waiter whose reserved slot was already due
  was sent: with the budget off or not the limit every waiter, and with the budget as the limit one request
  per interval for as long as cancelling the queue took. The diagnostics fix (PR #71) found it through
  `tests/test_throttle.py::test_stopped_bulk_job_leaves_no_queue_behind`, which flaked on a slow runner.
  As built by FX-9 (PR #83): one `raise_if_cancelled()` directly after `async with semaphore:`, before
  `breaker.check(url)` and before the reservation, on the curl path and on the browser-first path of
  `_request_async` (`sofascore_scraper/client/transport.py:539-541` and `:566-573` at `9b03c64`). A waiter that gets the
  semaphore after the stop raises `FetchCancelled`, makes no reservation and is not reported to the breaker.
  Measured offline with the budget not the limit and `MAX_CONCURRENT=10`: of 70 requests 70 were sent
  before and 10 (those in flight at the stop) after; browser-first, of 30 requests 30 before and 10 after.
  P13 keeps both checks when it rewrites the request paths.
- What FX-9 left. (1) A request that already holds the semaphore and is inside the bridge call when the job
  stops is still sent if its slot is due: `_wait_for_slot` (`sofascore_scraper/challenge_solver.py:170-189`) looks at the
  cancel only while it sleeps, so a browser-first request that waits for `ensure_ready()` at the stop goes
  on to `fetch_json`. `test_stop_reaches_browser_first_requests_waiting_for_a_request_slot` pins that number
  (10 sent) and expects 0 once the bridge checks the cancel before the reservation (P24, which moves the
  bridge). (2) The two paths disagree about a response that arrives during a stop: the curl path returns
  the data that is in hand, while the browser-first path raises `FetchCancelled` from the pause after the
  bridge's answer (`sofascore_scraper/client/transport.py:549-550`) and drops the data. Not pinned. (3) The sync path and
  the bridge still have no check between the reservation and the send. The FX-6 rule "a slot whose time has
  come is not given back" was made because waiters behind the semaphore did not look at the cancel; on the
  async curl path that reason is gone, and the rule is left as it is.
- Since FX-18 (PR #139; `b6caf2f`) points (1) and (2) are done and the bullet above about a shared solve
  holds for no path. `_wait_for_slot` (`sofascore_scraper/client/bridge.py:188-215`, P24 moved it out of
  `sofascore_scraper/challenge_solver.py`, which stayed an alias until FX-32 removed it) checks the cancel before it reserves a slot
  (`:204-206`), except for the verification request of a shared challenge solve (`_SlotWait.shared`), so a
  request that waited inside the bridge (`ensure_ready`, a shared solve) when the job stopped is not sent.
  The async bridge path has the caller-side check of the sync path: `_run_on_background_loop(...,
  cancellable=True)` (`:626-660`) looks at the caller's cancel every 0.25 s (`_CANCEL_CHECK_SECONDS`, `:170`),
  gives the coroutine 0.5 s to end itself (a slot wait then gives its slot back, and an answer that arrives
  in that time is returned), and otherwise raises `FetchCancelled` and cancels the coroutine; the shared
  solve (`asyncio.shield`) goes on for the other waiters. `fetch_json` and `solve_challenge` are called with
  `cancellable=True`, `ensure_ready` is not (a half-open browser leaves the profile locked; `:660-700`). A
  browser-first answer that arrives during a stop is returned, as on the curl path
  (`sofascore_scraper/client/transport.py:350-355` and `:555-560`). Point (3) holds only for the sync curl path, which only
  the doctor, the status check and the league search use; for the bridge it is the FX-6 rule: a slot whose
  time has come counts as sent, so the check stands before the reservation. P30 kept both checks
  (`tests/test_bridge_cancel.py`).
- **Priority lane (FX-23, PR #171; `48e4c4c`).** The end-to-end test found that a search typed in the web
  UI while a download ran waited behind the download's queued requests (4.5 to 12 s at 1 request per
  second). A block of requests run inside `throttle.interactive()` (`sofascore_scraper/throttle.py:104`) is
  interactive: its request takes the first queued slot that is not due yet instead of the end of the queue
  (`take_priority`, `:288`); that slot, every later one and the returned slots move back one interval, and
  the end of the queue (`tat`) grows by one interval, so the budget is never exceeded, only the order
  changes. The move is written to the shared state file (`seq`, the counter of priority requests; `bumps`,
  the positions moved; `prio`, the position of the last priority request, so that priority requests keep
  their own order). A waiting request looks at the moves when its slot comes (`settle` and `settle_async`,
  `:678` and `:693`, called by the transport, `sofascore_scraper/client/transport.py:122` and `:130`, and by
  the bridge, `sofascore_scraper/client/bridge.py:216`) and waits one more interval if it was moved;
  `give_back` returns a moved reservation at its new place. The users are the search of the follows
  service (`_ask`, `sofascore_scraper/services/follows.py:474`, so the type-ahead and Ctrl K) and, until P30,
  the legacy remote search (`sofascore_scraper/web/api/legacy.py:144`). The state file keeps its old form while no
  priority request was made. Measured in the re-test: suggestions for "galatasaray" within 4 s during a
  running download.
- **The browser profile of a second process (FX-23, PR #171; `48e4c4c`).** Chromium opens a profile
  directory in one process at a time, so while `ssc serve` held the bridge profile, a CLI command next to
  it (`ssc sync`, the poll fallback of `ssc watch`) could not start the bridge after a 403 ("Failed to create
  a ProcessSingleton … SingletonLock: File exists") and failed after three tries. Built:
  `sofascore_scraper/client/profile_lock.py` reads Chromium's lock before the launch (`profile_owner`, `:65`:
  the `SingletonLock` link `host-pid` on POSIX, `lockfile` on Windows). When another live process holds the
  profile, this process launches in a private sibling directory `<profile>-<pid>` (0700,
  `secondary_profile`, `:123`), removed when the bridge closes (`remove_secondary`, `:134`); siblings left
  by dead processes are swept at the next choice (`sweep_stale`, `:104`). A lock error at launch
  ("ProcessSingleton", "SingletonLock") retries once the same way (`_launch_in_free_profile`,
  `sofascore_scraper/client/bridge.py:318-335`). When no temporary profile can be made, the error is English
  and names the holder: "The browser profile … is in use by another process (pid N) and no temporary
  profile could be created …" (`:309-317`). The temporary profile starts without a solved challenge, so it
  costs one Turnstile solve on its first 403; the main profile is never touched. Re-tested against the real
  site: a CLI process next to `ssc serve` opened `<profile>-<pid>`, solved the challenge, got its answers
  and removed the folder afterwards.
- **Logs (FX-23, PR #171).** The log messages of the request layer, the bridge, the bridge health, the
  breaker, the status classifier, the throttle, the config manager and the web start-up warnings are
  English (rule 8 of the plan); the bridge health and the diagnostics README point to `ssc doctor` instead
  of `python main.py --doctor`. Scrapling adds a console handler of its own when it is imported; the bridge
  routes the `scrapling` logger through the application's handlers (`logger.adopt_library_logger`,
  `sofascore_scraper/logger.py:393`), so each of its lines appears once, in the application's format.
- **Request notifier (B2, PR #191; `216c2f9`).** The request context has a fourth value, `on_request`
  (`request_context(..., on_request=)`, `sofascore_scraper/client/context.py:120-143`): the transport and the
  bridge call `notify_request(waited)` (`:97`) once for every request just before it goes to SofaScore,
  every retry, bridge fetch and session warm-up included, with the seconds it waited for its slot in the
  shared budget; a request cancelled while it waited is not reported, and a failing notifier never breaks the
  request. A job's progress counts them (2.8), together with the back-off waits that already reached it
  through `on_wait`.
- The client writes nothing under `DATA_DIR`. It reports each bridge-health transition through
  `on_health_change`; since P11 (PR #69) `build_context` stores the snapshot with
  `store.runtime.set("bridge_health", ...)`, so another process (`ssc status`, a second server) can read
  the last known state (2.3).

As built (P05), where the first version of this section differed:

- **No `throttle` and no `lane` argument.** The request bodies are module-level functions that read
  `sofascore_scraper.throttle` themselves; an argument that changes nothing would mislead. The watcher still does its own
  wait on its throttle lane.
- **`aclose()` and `close()`** are two things, as above.
- **`ClientSettings` exists twice.** P05 defined the class above in `sofascore_scraper/client`; proxy, the post-request
  wait, `max_concurrent` and the budget are still read by the request layer from `ConfigManager` and
  `sofascore_scraper/throttle.py` per request. P09's model has a `ClientSettings` section class with the same three field
  names and the rest of 4.3; a test builds P05's class from the model (`base_url=effective_base_url`,
  `retries`, `timeout_seconds`). The next owner of the client takes the model's class or keeps building from
  it.
- **Old entry points stayed until P30.** `make_api_request` and `make_api_request_async` live in
  `sofascore_scraper/client/transport.py`; `sofascore_scraper/utils.py` forwarded to it through a module class (`_UtilsModule`), so that
  existing patches on `sofascore_scraper.utils` kept reaching the moved code. P30 (#186) ported those tests and
  deleted `utils.py`; callers import `client.transport` and `client.context`. The moved code still logs under
  the logger name `Utils`, so no log line changed.
- `request_context(cancel=..., on_wait=..., breaker=...)` restores the previous values on exit. The web job
  uses it since P08; before, the cancel check stayed set after the job.

### 2.5 What services need from the Store, and where the Store provides it

The parallel draft of this document proposed its own `Store` and `EventLog` protocols. They were replaced by
the Store's API (`01-storage.md` 2.3); services import `sofascore_scraper.store` directly and tests use a real Store in a
temporary directory. The proposed operations map as follows:

| Need of the services | Store API |
|---|---|
| write one event with its slices atomically; an older observation must not win; get back what changed | `store.events.put(event_id, outcomes, on_event_change=..., count_empties=..., keep_history=...)` → `PutResult` (`created`, `event_written`, `superseded`, `written`, `change_seq`) |
| compare old and new event payload under the same lock as the write | the `on_event_change(old, new)` callback of `put`; the service passes a function built from `diff_basic` and `change_row` (`sofascore_scraper/refresh.py:95-140`) |
| write a season list, a round or an event page and upsert the listed events | `store.entities.put(Ref.season(...), {("schedule", "round_12"): outcome}, index_listed_events=True)` |
| write a season/team/player/sport slice | `store.entities.put(ref, {key or (key, sub): outcome})` |
| reset "unavailable" marks | `store.events.reset_empty_markers(scope, include_confirmed=...)` |
| per-event state for planning, without a tree walk | `store.events.missing(...)`, `refresh_candidates(...)`, `stale(...)`, `open_events(...)` for the common questions; `store.events.states(scope)` for the general case |
| listing state (complete flag, age) | `store.entities.slice(ref, "schedule", sub)` → `SliceInfo.meta["complete"]`, `fetched_at` |
| event rows and lists for the faces | `store.events.get/list/count/iter` return catalog rows; `sofascore_scraper/schema` maps the rows to the normalized v1 records (it maps rows, not payloads; see below the table) |
| raw payload | `store.events.payload(id, key, sub, raw=True)`, `store.entities.payload(...)`: the stored JSON bytes. They are the parsed response serialised again, value-identical but not byte-identical to the wire (`01-storage.md` 4.1) |
| tournaments, seasons, changes, summary | `store.entities.tournaments/seasons`, `store.changes.list`, `store.events.summary`, `store.info` |
| export rows | `store.events.iter` + `sofascore_scraper/schema` mappers + `store.export.rows`; raw export is `store.export.raw` |
| clear, verify, rebuild, migrate | `store.clear`, `store.catalog.verify/rebuild`, `store.migrate.plan/run` |
| durable streams with sequence numbers | `store.streams.append/read/wait/head/prune`; sink positions with `store.streams.cursor/set_cursor` |
| watcher state | `store.watch.load/save` |
| jobs, job events, cancel across processes, leases | `store.jobs`, `store.lease(name)` |

As built (SC-1, PR #72; `sofascore_scraper/schema` at `e0bae0c`). The mappers take the Store's row classes (`EventRow`,
`TournamentRow`, `SeasonRow`, `ParticipantRow`, `SliceInfo`, `ChangeRow`, `StreamRecord`) and the dicts that
`store.derive` returns, never a SofaScore payload. The only reader of a status class and a score from a
payload is `sofascore_scraper/store/derive.py`, and `sofascore_scraper/schema` may not import the Store at run time (2.1), so a payload
is mapped as `schema.event_from_row(derive.event_row(payload, "event", observed_at))` by a caller that may
import both. The earlier text of 2.2 and of this table said "rows and payloads". Slice payloads are given
out raw in schema v1 (`schema.slice_from_info(info, payload=...)`); normalized slice models are later,
additive items (`04-schema-v1.md` section 9, approved on 2026-10-02). Categories and sports have no read
method and no row class in the Store yet, so `category_from_row` and `sport_from_row` take a mapping; the
read methods come with ST-22, and P21 needs them for `/tournaments`. `store.jobs` exists since ST-11
(PR #75) as a lazy property. Because the score sheet is derived in one place, FX-23 (PR #171) corrected
the football extra-time score there alone: `after_extra_time` is set for status 110, or for 120 when
SofaScore sends `overtime`, `extra1` or `extra2`, and no longer for a match that went straight to
penalties (the end-to-end test found it on the UEFA Super Cup 2025). `DERIVE_VERSION` is 6
(`sofascore_scraper/store/derive.py:50` at `48e4c4c`), so a catalog written by the old rule is rebuilt from
the files on its first open by any command; export files written before are not rewritten (`01-storage.md`,
`04-schema-v1.md`).

The rows of the table that were still ahead at `9b03c64` are built at `b3cb819`, with these differences.
Export: `store.export.rows(rows, columns, dest, fmt, *, table, overwrite, types)` and `store.export.raw(q,
dest, *, keys, fmt, pretty, overwrite)` (ST-25 #108, `types` added by SC-2 #130); `ExportService` builds the
rows from the `sofascore_scraper/schema` mappers and maps a `StoreError` with detail `pyarrow` to `not_supported`.
Migrate: `store.migrate.plan/run` take `tournaments=` ids, not a `Scope` (ST-23 #110, `01-storage.md` 2.3).
Sink positions: next to `cursor` and `set_cursor` the stream log has `cursors()`, the listing of every
sink's `SinkCursor(sink, seq, updated_at, last_error)` (ST-24 #109), which `ssc status` and
`GET /api/v1/sinks` read. Backup and restore: `store.backup.create/list/verify/restore/prune/path_of`.

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

`sofascore_scraper/errors.py` defines `PlatformError(code, message, details=None)`; the existing request-level exceptions
(`sofascore_scraper/exceptions.py:24-88`) stay inside `sofascore_scraper/client`.

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
`DataOperationRunningError` (`sofascore_scraper/web/jobs.py:30-46`) and their 409 handler (`sofascore_scraper/web/app.py:59-65`) are kept.
Since P30 (#186) they live only in the Store (`sofascore_scraper/store/jobs.py:196-210` at `216c2f9`; the
forwarder `web/jobs.py` is deleted), and the 409 handler takes their base class `JobStoreConflict`
(`sofascore_scraper/web/app.py:174`).

As built. `sofascore_scraper/errors.py` exists since P18 (PR #65) and holds this table as `ERROR_TABLE`: per code the
class, the exit code and the HTTP statuses; `describe errors` prints it and a test compares it with the
table above, row by row.

- `PlatformError(code, message, details=None)` is the base; its subclasses (`UsageError`, `NotFoundError`,
  `ConflictError`, `InstanceRunningError`, `UpstreamError`, `UpstreamBlockedError`, `NotSupportedError`,
  `Cancelled`) take `(message, details=None, *, code=None)`, because the class fixes the code.
- `ConfigError`, `StorageError` and `JobRunningError` in the table are the classes that already existed
  (`sofascore_scraper/exceptions.py`, the job store); they are not `PlatformError` subclasses. `to_platform_error()` maps
  them and the Store's `LeaseHeld`, `FollowExists` and `FollowManaged`, Ctrl+C becomes `cancelled`, and
  anything else `internal`. `data_operation_running` therefore has two classes in the code: `ConflictError`
  and the job store's `DataOperationRunningError`.
- `errors.lease_error_code(name, purpose)` gives `job_running`, `data_operation_running` or
  `instance_running` for a held lease, with the holder in `details`. The job store (ST-10) uses its own
  `conflict_from_lease`, which until P23 had no `instance_running`: a `live` or `watcher:<sport>` holder
  that blocked a data operation was reported there as `data_operation_running`. The previous revision
  expected that to end with the job manager. P11 (PR #69) did not change it (`sofascore_scraper/store/jobs.py:207-218` at
  `e0bae0c`), and the job manager raises the job store's classes. A job cannot meet the case: it takes
  `writer`, which `live` and `watcher` never hold, so the case exists only for data operations. P23 (PR #91) added
  the case with the lease that makes it reachable: a data operation (clear, restore) that a running live
  service or `--watch` blocks raises `InstanceRunningConflict`, a subclass of `DataOperationRunningError`
  with the code `instance_running` (`sofascore_scraper/store/jobs.py:210-238` at `9b03c64`); `.operation` is still the
  holder's purpose and `.lease` the lease name, so callers that catch `DataOperationRunningError` keep
  working. API v1 does not go through that mapping when the conflict comes from a lease: it maps the
  `LeaseHeld` behind the conflict with `lease_error_code`, so v1 can answer `instance_running`
  (`sofascore_scraper/web/errors.py:176-177`). `LeaseHeld` is a `StorageError`, so a caller that catches `StorageError`
  catches `LeaseHeld` first, as `main.py` does since P10.
- `message` is meant to be English. The messages of `StorageError` and of the Store's errors are Turkish in
  the code. The mapping builds an English message from the OS reason and the path when they exist and passes
  the Store's text through otherwise. As built since FX-25 (PR #172; `48e4c4c`): the Store's exception
  messages are English (about 110 messages and 25 manifest problem texts in `sofascore_scraper/store/`,
  translated in place, not through the locales), because they reach users: the CLI prints them, a failed
  job's `error.message` and log lines carry them, v1 sends them in `details.store_message`, and
  `FollowExists` and `FollowManaged` are the `message` of 409 answers. Six `ValueError`s that report a misuse
  by the code itself stay Turkish (`catalog.py`, `derive.py`, `indexer.py`, `sqlite.py`); v1 turns them into
  `internal` without the text, and `tests/test_store_messages_english.py` lists them by name. The issue
  texts of verify and of the scans (`ssc verify`, rebuild reports) are not exception messages and were still
  Turkish; B2 (PR #191) made them English like their codes (the issues I1 to I9 of `catalog verify`, the
  problem records of the legacy, change-log, entity and indexer scans, and the circuit breaker's cause in
  English log lines), and the text output of `ssc catalog verify|rebuild|reconcile` adds a description in
  the user's language per code (`ssc_catalog_kind_<code>`, `ssc_catalog_problem_<code>`,
  `sofascore_scraper/cli/commands/catalog.py:22`). The client's exceptions followed with FX-26 (PR #174;
  finding V2 of the live validation, "Not found: … (Durum Kodu: 404)" in a log): the texts and defaults of
  `sofascore_scraper/exceptions.py` are English (`APIError` adds " (status code 404)", `:29-33` at
  `43ecdfc`), and `tests/test_exception_messages_english.py` scans the module for Turkish literals.
- The module imports only the standard library and `sofascore_scraper/exceptions.py`, so `version` and `doctor` run
  before the packages are installed; it recognises the Store's classes through `sys.modules`.
- HTTP statuses as used by API v1 (P20, PR #74; `sofascore_scraper/web/errors.py:137-144` at `e0bae0c`).
  `invalid_request` is 422 for a rejected value (body, query or header validation; a route raises
  `errors.ValidationFailed`) and 400 for a well-formed request that is refused (a locked or read-only
  setting, an unknown cursor; a route raises `UsageError`). `cancelled` has no HTTP status; if it ever
  leaves a route it is answered with 500.
- The table has no row for too many failed token attempts, and none was added. While a client address is
  locked (section 6), a v1 route answers 401 `unauthorized` with a `Retry-After` header and
  `details = {reason: "too_many_attempts", retry_after}`; a legacy route and `POST /api/auth/login` answer
  429 with the legacy body `{"detail": {"code": "too_many_attempts", "message", "retry_after"}}`
  (`sofascore_scraper/web/app.py:87-101`). `too_many_attempts` is therefore a legacy code and a v1 detail, not a code of
  this table.
- A refused Host header is outside the error model: it is Starlette's plain-text `400 Invalid host header`
  from the outermost middleware, on v1 as on the legacy routes, without a request id.
- The table is unchanged at `b3cb819` (`sofascore_scraper/errors.py` has not changed since `9b03c64`); the items of this
  revision use its codes. A restore into a folder that holds data is `confirmation_required` (exit 2) with
  `details.occupied`, an archive that is not a valid backup `invalid_request`, an unknown backup name
  `not_found` (ST-24 #109; there is no code of its own). A migrate that could not convert some items ends
  with exit 3 like `partial` (ST-23 #110). `POST /api/v1/tournaments/search` answers 503 `blocked` or
  `rate_limited` and 502 `upstream_error`, with `details.reason` in the words of `sofascore_scraper/web/upstream.py`
  (P21 #124). A Parquet export without `pyarrow` is `not_supported` (SC-2 #130).

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

The block above is the target. The current stage, as built by P08 (PR #57, `sofascore_scraper/services/sync.py` at
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
  `sofascore_scraper/services/export.py`, which returns the path of the file or None. It swallows and logs every error,
  as the terminal menu's CSV step did, so an export error does not fail the job that called it: a full disk
  during the export phase ends a web job as Completed without a CSV (pinned in
  `tests/test_sync_service.py`). The class and the typed result come with EX-1, which also removes the
  export phase from `SyncService` (decision D9). EX-1 (#99) did both: `ExportService` exists (below), the
  phases of a sync are seasons, matches and details (`FULL_PHASES`, `DETAILS_PHASES = ("details",)`), and
  `SyncSpec.export` is kept but not read (`sofascore_scraper/services/sync.py:88-95`), because callers and stored job
  specs still pass it; its removal was left to P13, which did not remove it. `export_all_csv(ctx)` survives
  as a forwarder for `MatchDataFetcher.convert_all_matches_to_csv` and has no caller in `src` or `main.py`
  since P19, which writes the CSV through `ExportService.write_legacy_csv`; only tests still use the name.
  FX-15 (PR #155) removed both: `SyncSpec` has no `export` field (`sofascore_scraper/services/sync.py:102-120` at
  `b6caf2f`; a stored job spec is a dict and is not read back), `PHASE_WEIGHTS` has no `export` weight, and
  `export_all_csv` and the two `MatchDataFetcher` CSV forwarders are gone.
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
- `sofascore_scraper/services/maintenance.py` exists since P10 with one method,
  `MaintenanceService(ctx).recheck_unavailable(league_id=None, *, include_confirmed=False) -> ResetCounts`
  (`matches`, `slices`, `scanned`), a typed wrapper of `MatchDataFetcher.reset_unavailable_markers`. It takes
  a league id, because there is no `Scope` type yet; `clear`, `migrate` and `rebuild_catalog` come with
  their items (ST-19, ST-23). `clear` came with ST-19 (PR #90; below the next block). Since P30 (#186) it
  calls `DetailPhase.reset_markers` (`sofascore_scraper/services/maintenance.py:196-212` at `216c2f9`).
- The services take no lease and raise no `JobRunningError`: the caller holds the lease. Since P11 (PR #69)
  that caller is the job manager for a download and a refresh, on the web and in `main.py` (2.8); `main.py`
  still takes the lease itself for `--recheck-unavailable` alone and for `--watch` (4.6). Since P19 the CLI
  commands take it (`ssc data recheck-unavailable` holds `writer`, `ssc watch` holds `live`), and since P30
  `main.py` has no flags of its own. The handle a
  service gets is the manager's `JobHandle`; `DetachedHandle` remains for a caller without a job and is no
  longer used by `main.py`.
- `SyncService` does not count a season list that could not be fetched: a run in which every request is
  refused ends `succeeded` (`03-implementation-plan.md` section 15). After a breaker stop the batch fetcher
  returns for the matches in flight without reporting them as failed and still advances the progress to the
  total, so the progress says that every detail is done and none failed.
- `SyncResult.state` already follows the terminal-state rule (`partial` for a breaker stop or a failed
  match). Since P11 the state is stored: the web adapter (`src/web/fetch_job.py`) and `main.py` hand it to
  the job manager as a `JobOutcome`, and the job row says `partial`. The legacy API and the card still show
  Completed with the card text (2.8).

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
    def doctor(self, *, only=None, skip=None, live=False) -> DoctorReport: ...   # sofascore_scraper/doctor.py run_checks/report
    def diagnostics_bundle(self, dest: Path | None = None) -> Path: ...          # sofascore_scraper/diagnostics.py (PR #24)

# services/follows.py
class FollowsService:
    def list(self) -> list[Follow]: ...                      # every origin, each with its origin
    def add(self, spec: FollowSpec) -> Follow ; def update(self, follow_id, patch) -> Follow ; def remove(self, follow_id) -> None
        # origin "config": ConflictError("follow_managed").
        # origin "legacy": the change is written to leagues.txt / league_sports.json first (today's ConfigManager path), then mirrored.
        # With a config file present, new follows get origin "api"; without one they are written to leagues.txt as today.
        # (Built otherwise since FX-19: a new follow is always origin "api"; leagues.txt is a read-only legacy source.)
    def resolve(self, names: Sequence[str] | None = None) -> tuple[Target, ...]: ...
    def search_tournaments(self, query: str, *, sport: str | None = None) -> list[TournamentHit]: ...   # routes/leagues.py:51-86

# services/query.py: thin, typed read access for faces
class QueryService:
    def events(self, flt: EventFilter, page: Page) -> PageOf[Event] ; def event(self, event_id) -> Event
    def slice(self, owner, key, sub="") -> Slice ; def raw(self, owner, key, sub="") -> RawPayload
    def tournaments(...) ; def seasons(...) ; def changes(since, page) ; def sports() -> list[SportInfo]
    # plus the functions that reproduce today's response shapes for the legacy /api routes
```

The block above is the target. What exists at `9b03c64`, as built by RD-1 (PR #80), RD-2 (PR #89), RD-4
(PR #78), RD-5 (PR #79), ST-19 (PR #90) and P23 (PR #91). Each service takes a Store (2.3), prints nothing,
takes no lease, and serves the legacy routes and the terminal menu; the typed v1 methods above come with
P15 and P21.

```python
# services/status.py (RD-4)
@dataclass(frozen=True)
class TournamentCounts:            # tournament_id None: the events without a unique tournament
    tournament_id: int | None
    matches: int; details: int; events: int; finished: int
    seasons: int                   # seasons in the tournament's season list
    seasons_with_events: int       # distinct seasons among its events
    last_update: int | None        # newest change of an indexed file (epoch s); None without details
    coverage: float                # property: details / matches in percent, one decimal

@dataclass(frozen=True)
class DiskUsage:
    entries: Mapping[str, int]     # Store.info().bytes: every top-level entry of the data directory
    measured_at: float             # epoch seconds of the measurement
    seasons, matches, details, datasets, total: int   # properties; total = the four areas

@dataclass(frozen=True)
class DataSummary:
    data_dir: str; only_finished: bool              # which match rule was applied
    matches: int; details: int; seasons: int         # the whole catalog, unconfigured leagues included
    tournaments: tuple[TournamentCounts, ...]       # every tournament with events, plus the requested ones
    disk: DiskUsage | None                          # None with sizes=False
    catalog_rebuild_reason: str | None              # set: the catalog does not describe the files

class StatusService:
    def __init__(self, store: Store): ...
    def summary(self, *, tournament_ids=(), only_finished: bool | None = None, sizes: bool = True,
                sizes_max_age: float = 60.0) -> DataSummary: ...

# services/query.py (RD-1, RD-2)
class QueryService:
    def __init__(self, store: Store): ...
    def match_detail_legacy(self, event_id: int) -> dict | None: ...    # GET /api/matches/{id}
    def matches_legacy(self, *, tournament_ids=(), season_id=None, date=None, details=None,
                       only_finished=True, sort="desc", offset=0, limit=25,
                       league_names=None) -> LegacyMatchPage: ...
    def season_matches_legacy(self, season_id: int, tournament_id: int, *, only_finished=True) -> list[dict]: ...

# services/tournaments.py (RD-5): functions on a Store
def seasons_of(store, tournament_id, *, name=None) -> list | None: ...      # SofaScore's own season objects
def season_lists(store, names: Mapping[int, str | None]) -> dict[int, list]: ...
def schedule_pages(store, tournament_id, season_id) -> int: ...
def has_matches(store, tournament_id, season_id) -> bool: ...   # stored pages, or events from a listing
def sport_of(store, tournament_id) -> str | None: ...

# services/backup.py, services/maintenance.py (ST-19)
class BackupService:
    def __init__(self, store: Store): ...
    def create(self, scope: BackupScope = "all", *, config_files: Sequence[str] = (),
               include_secrets: bool = False) -> BackupInfo: ...
        # BackupScope: all | config | seasons | matches | match_details
    def list(self) -> list[BackupInfo]: ...
class MaintenanceService:
    def __init__(self, ctx: ServiceContext | None = None, *, store: Store | None = None): ...
    def clear(self, scope: DataScope, *, confirm: bool) -> ClearReport: ...
    def recheck_unavailable(self, league_id: int | None = None, *, include_confirmed=False) -> ResetCounts: ...
```

- **Counting rules (decision D21, settled 2026-10-02).** (a) The match count comes from the catalog's
  events, and `FETCH_ONLY_FINISHED` applies at read time: with the setting on, an event counts when it is
  finished (`completed` or `decided_without_play`) or has stored details; with it off, every event counts.
  Before RD-4 the count came from the rows of the summary CSVs, filtered when they were written. (b) A match
  with stored details that no schedule lists counts as a match. `details` counts every event with a stored
  `/event` payload wherever its directory is (league or season directory, flat, `_no_tournament/`, combined
  file only), so it is always a subset of `matches`. `StatusService._match_counts`
  (`sofascore_scraper/services/status.py:242-257` at `9b03c64`) applies (a) as finished events plus one pass over the
  unfinished events with details. RD-2 applies the same rule to the two match lists, so a list total and
  the dashboard agree. On the owner's data every count equals main's.
- **The setting is read at call time.** `only_finished_setting()` reads the environment at each call
  (2.1, rule 3); P30 moved it to the Settings (`fetch.only_finished`, read at call time).
- **Disk usage.** The walk is inside the Store (`Store.info(sizes=True)`), which has no cache of its own.
  The cache is in the service (`_sizes`, `forget_sizes(store)`; `sofascore_scraper/services/status.py:150-174`): one
  measurement per Store is kept for 60 s (`SIZES_MAX_AGE`) and is dropped at once when the catalog changes
  (its row counts or its newest `updated_at`), that is after a download or a clear. Changes the catalog does
  not see (a CSV export, a backup, files deleted by hand) show after at most a minute. If `Store.info`
  gets a cache of its own, this one goes. The per-league disk sizes of the terminal menu
  (`league_stats(...)["disk"]` in `sofascore_scraper/services/stats.py`) still walk the league directories, because
  `StoreInfo.bytes` has one number per top-level entry; eight file-system calls stay in the boundary
  baseline for them.
- **Cost.** `summary()` takes about 1.9 ms on the owner's data with kept sizes (17 ms when the sizes are
  measured; the file walk of main took 21 ms), 72 ms at 50,000 events and about 470 ms at 300,000 events in
  2,000 tournaments (synthetic, tmpfs, warm cache).
- **What the summary cannot see.** `catalog_rebuild_reason` is set when the catalog does not describe the
  files; the counts are then zero or partial, and the legacy routes do not show it (P21 does). Files
  changed by hand while a process runs are counted after the Store is opened again (the limit of the
  reconcile on open, `01-storage.md` 3.5). `coverage(scope)` goes next to `summary` with P15.
- **Match lists (RD-2).** `matches_legacy` and `season_matches_legacy` build the ten columns of the old
  summary CSV from catalog rows (`LEGACY_LIST_COLUMNS` and `_legacy_row`: round from `listed_in`, scores
  from `*_score_current` with 0 as default, tournament from `stage_name`, `match_date` as local ISO text).
  `EventQuery` joins its conditions with AND, so rule (a) runs as two disjoint queries, finished events and
  unfinished events with details, merged in the catalog's order (`_list_queries`, `_merged`;
  `sofascore_scraper/services/query.py:203-227`); an OR option on `EventQuery` would let the list page in SQL. Both lists
  are ordered by start time with ties broken by event id; the season list used to keep the order of the
  summary files, and ties on the kick-off time are common on real data. The date filter stays what it was,
  a substring of the local ISO text of `match_date`: the service narrows the query to a start-time range
  only when the text is an ISO prefix, and then keeps the substring test. `league_folder` comes from the
  legacy detail directory when there is one and otherwise from `sofascore_scraper/paths.league_dir_name(id, name)` with
  the configured or the catalog's tournament name, because a listing-only row or a v3 event has no path. A
  match with stored details shows the score and status of its stored payload. The export CSV is no longer a
  fallback (decision S14). On the owner's data all 738 rows and every total equal main's; only the order of
  matches with the same kick-off time differs, and the full list takes 2.7 to 9.7 ms instead of 96 to
  208 ms. Missing details (`_get_missing_details_sync`) still walk the files and do not apply
  `FETCH_ONLY_FINISHED` (RD-3).
- **Match detail (RD-1).** `match_detail_legacy` returns `basic` and one key per `required` slice with a
  payload, each read from its own file first, then from the combined file; an unreadable slice file drops
  only that slice. `MatchDataFetcher` forwards its loader to the service.
- **Season lists and sports (RD-5).** Every reader (web route, `SeasonFetcher`, terminal menu) takes the
  newest list file of a league by modification time, whatever its name. `seasons_of` returns SofaScore's
  own season objects from the stored payload, because `SeasonRow` does not carry them. The has-matches
  check of `SeasonFetcher` is `has_matches` (stored schedule pages, or events known from a listing), and
  `schedule_pages` gives the page count. `sofascore_scraper/web/league_sports.sports_for` is the sport lookup that writes
  nothing: `GET /api/leagues` no longer writes `config/league_sports.json`, so a sport learned from the data
  no longer reaches that file, the follows table or `config init --from-legacy` (a sport the user chose
  still wins). `league_sports.resolve_all` has no caller in `src` any more; two tests keep it until P21.
  FX-15 (PR #155) removed it; its tests read `sports_for`.
- **Is the catalog current.** `open_store` does not fail when the catalog could not be synced, and a
  reader then sees a stale or empty catalog; for a need computation an empty catalog would mean "full" for
  every match. Since ST-19 `Store.catalog_current` (`sofascore_scraper/store/api.py:504-512`) says whether the catalog was
  synced in this process and every hook has updated it since. No reader asks it yet. A reader that plans
  work (RD-3, P12) refuses to plan when it is False.
- **Backup (ST-19).** `BackupService(store).create` writes today's zip (five scopes, config files at the
  root of the zip, `.env` only with `include_secrets=True`) through `Store.backup`; there is no `dest` and
  no `handle`. The caller passes the config files, because the path of the sport sidecar lives in
  `sofascore_scraper/web/league_sports.py`, and the caller holds the `writer` lease (the route through
  `JobStore.exclusive("backup")`). `verify`, `restore`, the format 2 and pruning are ST-24's.
- **Clear (ST-19).** `MaintenanceService(store=...).clear(scope, confirm=True)` calls `Store.clear`, which
  takes `maintenance` itself when this process does not hold it and rebuilds the catalog under the same
  lease. `DataScope` accepts today's names (`match_details`, `matches`, `seasons`, `all`) and the Store's
  (`events`, `schedules`). The terminal menu's clear does not call it yet (7.1).
- **Live (P23).** `LiveService(store, scope, ...).run(stop, until_seconds=None)` returns a `LiveReport`, and
  `services.live.live_status(store)` is the status function for any process. `LiveService.status()` gives
  only the report of the service in this process, and `events()` does not exist: `ssc events` reads the
  stream log itself (4.1, section 8).

What was added by `b3cb819`, as built by EX-1 (#99), FX-7 (#103), SC-2 (#130), ST-23 (#110), ST-24 (#109),
P15 (#117), P21 (#122, #123, #124, #126) and P29 (#128). Each service again takes a Store (2.3) and takes no
lease of its own except where noted.

```python
# services/export.py (EX-1, FX-7, SC-2)
@dataclass(frozen=True)
class ExportSpec:                      # the legacy wide CSV only (profile "legacy-wide-csv")
    dataset: str = "events"; format: str = "csv"; profile: str | None = "legacy-wide-csv"
    tournament_ids: tuple[int, ...] = (); event_ids: tuple[int, ...] = ()
    league_id: int | None = None       # GET /api/export/csv?league_id=: a prefix filter on `league_folder`

@dataclass(frozen=True)
class DatasetFilter:                   # the filters of GET /api/v1/events that an export knows
    sport: str | None = None; tournament_ids = (); season_ids = (); event_ids = (); status_classes = ()
    start_from: float | None = None; start_to: float | None = None

@dataclass(frozen=True)
class DatasetSpec:                     # normalized schema v1 records, or the stored payloads
    dataset: str = "events"            # events | slices | changes
    format: str = "jsonl"              # normalized: jsonl | csv | parquet | sqlite; raw: jsonl | tree
    schema: str = "normalized"         # normalized | raw
    filter: DatasetFilter = DatasetFilter()

@dataclass(frozen=True)
class ExportResult:
    rows: int; columns: tuple[str, ...]; bytes: int = 0; path: str | None = None
    events: int = 0; skipped: tuple = (); schema_version: int | None = None   # None: raw and the wide CSV

class ExportService:
    def __init__(self, store: Store): ...
    def export(self, spec: ExportSpec | DatasetSpec, dest: str | PathLike | IO[str] | BinaryIO, *,
               overwrite=False, allow_empty=True) -> ExportResult: ...
    def export_dataset(self, spec: DatasetSpec, dest, *, overwrite=False, allow_empty=True) -> ExportResult: ...
    def records(self, dataset: str, flt: DatasetFilter | None = None) -> Iterator[Model]: ...
    def legacy_table(self, spec=None) -> LegacyTable ; def prepare(self, spec=None) -> PreparedExport
    def write_legacy_csv(self, directory, spec=None, *, now=None, name=None) -> ExportResult | None: ...
        # <directory>/<name>, else all_matches_<epoch>.csv; None when there is nothing to export
    def write_legacy_csv_by_league(self, directory, spec=None, *, now=None) -> list[ExportResult]: ...

# services/backup.py (ST-24)
class BackupService:
    def create(self, scope: BackupScope = "all", *, config_files=(), include_secrets=False) -> BackupInfo: ...
        # BackupScope: all | state | data | config | seasons | matches | match_details
    def list(self) -> list[BackupInfo]: ...
    def verify(self, name: str) -> BackupCheck: ...
    def restore(self, name: str, *, force=False, dry_run=False) -> RestoreReport: ...
    def prune(self, keep=None, max_age_days=None, *, now=None) -> list[BackupInfo]: ...   # default: keep all

# services/maintenance.py (ST-19, ST-23)
class MaintenanceService:
    def clear(self, scope: DataScope, *, confirm: bool) -> ClearReport: ...
    def migrate(self, *, dry_run=False, exact=False, tournaments=(), limit=None, delete_legacy=False,
                purge_derived=False, confirm=False, should_stop=None, progress=None
                ) -> MigrationPlan | MigrationReport: ...
    def rebuild_catalog(self, *, mode="auto", progress=None) -> RebuildReport: ...   # auto | in_place | recreate
    def verify_catalog(self, *, deep=False, repair=False) -> VerifyReport: ...
    def reconcile_catalog(self, *, deep=False) -> ReconcileReport: ...
    def recheck_unavailable(self, league_id=None, *, include_confirmed=False) -> ResetCounts: ...

# services/follows.py (P21 part 3)
class FollowsService:
    def __init__(self, store: Store, legacy: LegacyLeagues, *, config_file: bool): ...
    def list(self, *, kind=None, origin=None, enabled=None, text=None) -> list[Follow]: ...
    def get(self, kind: str, entity_id: int) -> Follow | None: ...
    def add(self, new: NewFollow) -> Follow: ...
    def update(self, kind: str, entity_id: int, changes: Mapping[str, Any]) -> Follow: ...
    def remove(self, kind: str, entity_id: int) -> Follow: ...
    def search_tournaments(self, query: str, *, sport=None) -> list[TournamentHit]: ...   # at most 20 hits

# services/query.py (P21 part 2): the v1 reads, records of schema v1, None for an unknown id
class QueryService:
    def events(self, flt: EventFilter | None = None, *, sort="start_desc", limit=50, cursor=None,
               slices_summary=False) -> EventPage: ...
    def event(id) ; def event_slices(id) ; def event_slice(id, key, sub="") ; def raw(id, key="event", sub="")
    def tournaments(*, sport=None, text=None, followed=None, limit=50, offset=0) ; def tournament(id)
    def seasons(tournament_id) ; def season(id) ; def season_slice(id, key, sub="")
    def changes(*, after=0, before=None, event_id=None, tournament_ids=(), since=None, until=None,
                order="asc", limit=50) -> ChangePage: ...

# services/status.py (P15, P29)
class StatusService:
    def coverage(self, scope: Scope | None = None, *, threshold=...) -> CoverageReport: ...
def schedule_status() -> ScheduleStatus: ...     # enabled, next_runs: the scheduler of this process only

# services/data_jobs.py (P21 part 4, SC-2): the export of API v1
def check_export(req: ExportRequest) -> None ; def run_export(store, req, dest: str) -> dict
# services/sink_status.py (P21 part 1)
def sink_states(store, specs: Sequence[SinkSpec], *, now=None) -> list[SinkState]: ...
```

- **Export (EX-1, FX-7, SC-2).** The target block above had one `ExportSpec` with `filter: EventFilter` and
  `format` including `json`. Built are two specs: the legacy `ExportSpec`, which is the 2.x profile with
  `tournament_ids`, `event_ids` and a legacy `league_id` (a filter on the `league_folder` column), and
  `DatasetSpec` for the normalized datasets and the raw payloads. `export()` takes either; `dest` is a path
  or a text stream for the profile and a path or a binary stream for a dataset (the raw export writes to a
  path only). There is no `handle` argument and no `json` format: JSONL covers it. The wide CSV is built in
  memory from the catalog (`store.events.iter(EventQuery(has_details=True, sort="start_asc"))` and
  `store.events.payloads`), so every saved match appears once, in kick-off order, in both layouts; it was not
  measured on large data. The row rule of `MatchDataFetcher.process_match_for_csv` moved as `legacy_wide_row`.
  FX-7 fixed two defects of the moved code: the formation columns are filled, and the per-league download
  is the full table filtered in memory (`_league_rows`, streamed in batches of 500 rows) instead of a pandas
  pass, so whole numbers are no longer written as `61.0`, number-like text such as a rating `6.50` is no
  longer rewritten, `NA` and `null` cells are no longer emptied, and lines end in `\r\n` on every platform.
  When no row has a `league_folder` (every saved match is a flat `match_details/<id>` record), `league_id`
  returns every row, as the pandas code did (pinned). The service uses no pandas. The file that `ssc export`
  and the legacy `--csv-export` write into `match_details/processed/` is written with plain `open()`, which
  follows the umask as before. SC-2 added the datasets `events`, `slices` and `changes` in JSONL, CSV,
  Parquet and SQLite: JSONL is each record's `to_dict()` as API v1 returns it; the tabular formats have one
  column per leaf field of the model, named by its path joined with `_` (63, 13 and 14 columns), lists as
  JSON text, every column in every row, and Parquet column types taken from the models through the new
  `types` keyword of `Store.export.rows`. SQLite writes one table named after the dataset. `slices` holds
  the catalog's slice rows with `payload` null; `changes` has no raw form. `schema_version` is in the
  result, in the job result and in `ssc export --json`, not in the files (no manifest).
- **Backup (ST-24).** `verify`, `restore` and `prune` were added, and the scopes `state` and `data` next to
  today's five. Everything takes a backup name inside `backups/`, not a path. `restore` maps the Store's
  `BackupNotFound` to `not_found`, `BackupInvalid` to `invalid_request` and `RestoreRefused` to
  `confirmation_required` with `details.occupied`. It takes the `maintenance` lease itself unless this
  process holds it; a dry run takes no lease and writes nothing, and its `RestoreReport` has `occupied`,
  `replaced`, `restored`, `skipped` and counts. Settings files and `.env` are never restored, with one
  exception since FX-22 (#165): the Settings page's `CONFIG_DIR/overrides.json`. `create` passes it to the
  Store for the scopes `all`, `state` and `config` (member `config/overrides.json`, archive 0600, because it
  can hold the proxy password), and `restore` passes its place: the service takes the Settings file's lock
  (`config_files.file_lock` on `overrides.json`, the one `PATCH /api/v1/settings` takes), keeps the current
  bytes, lets the Store restore the member in the same step as the data (0600, atomic, rolled back with the
  rest), and then reloads the settings (`config.overrides.reload_or_put_back`). When the restored document
  cannot be loaded, the previous file is put back, the previous settings stay in force, and the report
  lists the member under `skipped` instead of `restored`; its content and the loader's message are never
  logged (only the error's type). An archive without the member leaves the current settings alone. The
  reload is per process: `ssc backup restore` while a server runs reloads the CLI process only; the server
  reads the restored file at its next reload (a settings write, or a restart). The rule for the archive is
  `01-storage.md` 9.1 and 9.2. Since FX-23 (PR #171; `48e4c4c`) `create` takes `job_id`
  (`sofascore_scraper/services/backup.py:55`): the web and the scheduler's `backup` jobs pass their own id,
  and the archive's copy of `state.db` records that job as `completed` (progress 100, `finished_at`), since
  it is still running while the copy is taken; before, every restore of such an archive marked the backup
  job `interrupted` and the Overview warned about it (finding F30). A restore of an older archive keeps the
  finished live record of a job that is running in the archive but finished in this data folder. A
  settings file's `.lock` is never in an archive: the scopes pass the settings files by name, and the data
  walk covers only the data trees (FX-25, 4.3). `prune` keeps
  everything by default and nothing calls it: there is no `ssc backup prune`. The design's `dest` and
  `handle` arguments are not built, and there is no `path_of` on the service: the download route asks the
  Store (`Store.backup.path_of`). `BackupInfo` has a `format` field read from the zip (1 for 2.x and ST-19
  archives, 2 now), while `with_env` still comes from the file name. What a backup holds and how a restore runs is `01-storage.md` section 9.
- **Maintenance (ST-23).** The target had `migrate(*, dry_run, delete_legacy, handle)` and
  `rebuild_catalog(*, handle)`. Built: `migrate` returns the plan for a dry run and the report for a real
  run, takes `exact`, `tournaments`, `limit`, `purge_derived`, `confirm` (required with `delete_legacy` or
  `purge_derived` in a real run), `should_stop` and `progress`, and no handle. `rebuild_catalog(mode=)` holds
  `maintenance`; `verify_catalog(deep, repair)` and `reconcile_catalog(deep)` are new and take it only for a
  repair or a deep reconcile. The migration itself is `01-storage.md` 5.4.
- **Follows (P21 part 3).** The target had `update(follow_id, patch)`, `remove(follow_id)` and
  `resolve(names)`. Built: `update` and `remove` take `(kind, entity_id)`; `resolve` is not built (no
  caller: the downloads still read `leagues.txt`). Writes go by the row's origin: a `legacy` follow (a
  tournament without a config file) is written to `config/leagues.txt` and the sport sidecar first and
  then mirrored, and only its `sport` can change; a `config` follow is `follow_managed`; an `api` follow
  (a tournament with a config file, every team, player and event follow) changes in `state.db`. `list`
  reloads the league file first, so a file edited by hand is seen. A config follow that took over an `api`
  row deletes that row when it leaves the file; the row does not come back (the case ST-17 left open).
  `slices` is shown but cannot be set (P27). The legacy league routes do not use the service: they keep
  writing `leagues.txt` through `ConfigManager`, also with a config file (6.1). `ssc follows add` does not
  use it either: it writes the follows table directly with origin `api`, with or without a config file, so
  a tournament added there is watched by `ssc watch` but not downloaded by `ssc sync` (4.3). Changed by
  FX-13 and FX-19 (below): the CLI's follows commands use the service, the sync reads the follows table,
  and a new follow is always an `api` row.
- **Coverage and the scheduler.** `StatusService.coverage(scope)` exists since P15 (#117) and reads the
  slice rows of the catalog; its only caller is `MatchDataFetcher.generate_file_report`, the old file
  report. Neither face uses it: `ssc status --coverage` and `/api/v1/status` show the per-tournament
  details over matches of `summary()`, because the slice-level report is too heavy for a route the UI
  polls (P21 part 1).
  `schedule_status()` reports the scheduler of this process only (P29; 2.8).
- **The read methods of API v1 (P21 part 2).** `has_details` of `EventFilter` is the `has=details|missing`
  of the route; `slices_summary` adds, per event, the registry's default selection for its sport and how
  many of those slices are `ok`, `empty` and `error`; the cursor is tied to the order. `raw` returns the
  stored bytes, decompressed, with their sha256 and fetch time.

What was added by `b6caf2f`, as built by P27 (#134), P28 (#140), FX-13 (#152, #153), FX-15 (#155) and
FX-19 (#156). The services still take a Store and no lease of their own, except where noted.

```python
# services/follows.py (FX-13, FX-19)
class FollowsService:
    def __init__(self, store: Store, legacy: LegacyLeagues, *, config_file: bool = True): ...
    def add(self, new: NewFollow) -> Follow: ...            # always an `api` row
    def update(self, kind, entity_id, changes) -> Follow: ...  # changes may hold origin="api": adopt first
    def adopt(self, kind, entity_id) -> Follow: ...          # a leagues.txt row moves into the follows table
    def sync_tournaments(self) -> list[Follow]: ...          # enabled tournament follows of every origin
    def sync_others(self) -> list[Follow]: ...               # enabled team, player and event follows
    def search(self, query, *, sport=None, kinds=("tournament",)) -> list[SearchHit]: ...
    def search_tournaments(self, query, *, sport=None) -> list[SearchHit]: ...   # = search(kinds=("tournament",))

# services/maintenance.py (FX-19)
class MaintenanceService:
    def clear_tournament(self, tournament_id, *, season_id=None, confirm: bool) -> TournamentClearReport: ...

# services/owner_data.py (P28): the v1 reads of odds and season data
class OwnerDataService:
    def odds_slices(event_id) ; def odds(event_id, key, sub, history=False) ; def odds_lines(...)
    def season_slices(season_id) ; def standings(season_id, table="total") ; def standings_rows(...)

# services/data_jobs.py (FX-19)
def export_name(store, job_id, req, *, now=None) -> str: ...   # <label>_<UTC date>_<8 of the job id>.<ext>
```

- **Where follows live (FX-19).** The target block said that without a config file new follows are written
  to `leagues.txt`, and P21 built it so. Built: `FollowsService.add` always writes an `api` row of the
  follows table, with or without a config file (`sofascore_scraper/services/follows.py:278-295` at `b6caf2f`), so every
  field of a follow added through the API, the web UI or `ssc follows add` stays editable. `config/leagues.txt`
  is a read-only legacy source: its rows are mirrored as origin `legacy`, `writable` is `["sport",
  "origin"]` (`:51-52`), and only `sport` can change. `PATCH {"origin": "api"}` moves such a row into the
  table (`adopt`, `:349-372`; `FollowStore.adopt`, `sofascore_scraper/store/follows.py:440`): the row keeps its fields and
  position and becomes `api`, then the league leaves `leagues.txt` and its sport sidecar, and the next mirror
  does not bring it back, because `api` outranks `legacy`; other fields of the same request are written after
  the move. The move is explicit: no rule moves rows on its own, and there is no `ssc follows` command for it
  (only the PATCH). `config_file` no longer decides anything (`:219-226`; kept for callers). A follow added
  while `leagues.txt` names the same league is `follow_exists` (409). Removing a `legacy` follow removes it
  from the file (FX-13), so it no longer comes back with the next mirror. Since FX-13 the CLI's `follows
  list|add|remove` go through the service (`sofascore_scraper/cli/commands/follows.py:82-89`).
- **Search (FX-19).** `search(query, sport=, kinds=)` (`:399-444`) sends one request through the client, on
  the shared budget: tournaments alone ask `/search/unique-tournaments/{q}` (the 2.x endpoint), any other
  choice asks `/search/all?q=…&page=0`, whose answer types hits as `team`, `player` or `uniqueTournament`.
  At most 20 hits in SofaScore's order, each typed by `kind`, with its sport, its country, a player's team
  and `followed` per kind; fewer than 2 characters is `invalid_request`. A 404 answer is an empty list (it
  was 502). The tournament hit of `/search/all` is assumed to have the shape of the tournament search
  (experimental; the live validation checks it; it did: "la" listed the UEFA Champions League, the FIFA
  World Cup and LaLiga among its hits, and the EHF Champions League was followed from a hit). The route
  keeps the path `/tournaments/search` and the component name `TournamentHit`, because the frontend imports
  it; a neutral `/search` could replace both (P30 did not; the names stand in 3.1). One request searches the chosen kinds. Since FX-20
  (#167) the web editor and Ctrl K ask for all
  three kinds at once (`kinds: [tournament, team, player]`, so `/search/all` upstream), as the user types:
  the follows are filtered in the browser with no request, stored tournaments and teams come from
  `QueryService.suggest` (`GET /api/v1/catalog/suggest`, no SofaScore request, debounced 120 ms in the
  browser), and from 2 characters, 350 ms after the last key, one SofaScore search runs. The service keeps
  each answer, a 404 included, for 10 minutes (`SEARCH_CACHE_SECONDS = 600`, at most 256 texts,
  `SEARCH_CACHE_SIZE`; case and spaces ignored; refusals and errors are not kept), so the same text again
  sends nothing. The route is `async` and runs the search under a cancel check tied to the client's
  connection (`run_while_connected`): when the browser aborts the fetch, a request that still waits for
  its turn in the request budget is never sent and gives its turn back, and the route answers 499 (not in
  the OpenAPI document); a request already sent cannot be stopped, and its answer is kept. The browser
  keeps answers while the page is open (a module-level map of up to 100 texts; a reload clears it, and the
  server's cache answers after it). Since FX-23 (PR #171) a search runs in the request budget's priority
  lane (`throttle.interactive()`, 2.4), so a download that is running does not hold it up, and a hit
  (`SearchHit`, `sofascore_scraper/services/follows.py:145-164` at `48e4c4c`; API `TournamentHit`) carries
  `gender` (`"M"` or `"F"`, as `/search/all` gives it for a team) and `national` (whether the team is a
  national team); both are null for tournaments, players and the stored-name suggestions of `suggest`, and
  neither is stored in the catalog (that would need a catalog schema change). The web UI shows them since
  FX-25 (#172) to tell same-named teams apart (`05-web-ui.md` 6.2). `FollowRecord` has neither. Corrected by
  B1 (#190): the catalog's `participants` rows do carry `gender` and `national` from the stored event
  payloads, so the team hits of `suggest` and the team record route `GET /teams/{team_id}` give them (below,
  "As built at `216c2f9`"). Since FX-26 (PR #174; finding
  M16 of the live validation, a tennis player's hit without a sport) a team or player hit takes its sport
  also from the entity's or its team's `category.sport`, `primaryUniqueTournament.category.sport` and
  `tournament.category.sport`, and last from the result's own `sport` (`_entity_sport`,
  `sofascore_scraper/services/follows.py:600-612` at `43ecdfc`); without any the hit has no sport and still
  works. Where SofaScore puts that player's sport is not known (no stored answer), so these are guesses that
  harm nothing. The live validation measured the search on the real site: "la" gave 20 hits in 0.44 s
  (Messi, Juventus, Lamine Yamal, the UEFA Champions League, the FIFA World Cup, LaLiga sixth), "ba" 20 and
  "fe" 19; the same text again took 0.0 s (the server's cache), "la l" to "la liga" put LaLiga first, and
  each settled text sent one SofaScore request. Whether an aborted fetch closes the connection in time
  behind a reverse proxy was not checked; B2 (#191) checked it with a proxy-like client only: the cancel
  works when the proxy closes its upstream connection, which nginx (`proxy_ignore_client_abort off`, its
  default) and Caddy do (6).
- **Follow names (FX-26, PR #174).** A follow's name is 1 to 80 characters without a line break, ":" or
  "\\"; a league's name also refuses "/", because it is a `config/leagues.txt` line (`Name: ID`) and
  becomes a 2.x folder name. The names of team, player and match follows live only in the follows table and
  never become a path, so `check_name(name, kind)` (`sofascore_scraper/services/follows.py:189-205` at
  `43ecdfc`) allows "/" for them: a doubles match of tennis, padel or badminton ("L. Andersson / O.
  Andersson - …") is followed under its own name (finding M4 of the live validation). The error text names
  the rule of the kind.
- **Suggestions from stored data (FX-20).** `QueryService.suggest(text, sport=, limit=8)` returns the
  stored tournaments and teams (competitors) whose name contains the text, in the shape of a search hit
  (`kind` `tournament` or `team`): names that start with the text first, then names with a word that starts
  with it, then the others, followed ones first within each; at most 20. Route: `GET
  /api/v1/catalog/suggest?q=&sport=&limit=` (`suggestCatalog`, read-only: it reads the catalog and the
  follows table only). Case and accents are ignored. As built since FX-28 (#177;
  `sofascore_scraper/services/query.py:547-600` at `216c2f9`): a word starts after any mark that is not a
  letter or a digit (space, `-`, `/`, `.`, `&` …), and when any name starts with the text or has a word that
  does, names that hold it only inside a word are left out ("la" gives LaLiga, no longer Alanyaspor); they
  come only when nothing better exists ("on" gives Everton). The candidate pool (`_SUGGEST_POOL` = 200 per
  kind) is read word-start first (`EntityStore.tournaments` and `participants` with `word_start=True`), so a
  pool full of mid-word names cannot hide a word-start one; the substring pool is the fallback.
- **The sync reads the follows table (FX-13).** `sync_targets(ctx)` (`sofascore_scraper/services/sync.py:168-190`) reads
  the enabled tournament follows of every origin (`sync_tournaments`) after refreshing the mirror of
  `leagues.txt`, and each follow is downloaded with its season choice (`all`, `current`, `last:N`, ids); a
  run for one league (`league_id`, `--tournament`) keeps every season. When the Store cannot be read the
  sync falls back to `ConfigManager.get_leagues()`, every season (the plan had P30 remove the fallback
  with the legacy paths; it stays at `216c2f9`, `sofascore_scraper/services/sync.py:20-25`). Named follows are a `FollowsSyncSpec(follows=(…))` (`:133-140`), a subclass, so the job record of
  an older spec keeps its shape. `mode="seasons"` reads the season lists only, without the freshness limit
  and without schedules or details. The job-log lines of the sync path carry `code` and `params` (G24 of
  `05-web-ui.md`). FX-23 (PR #171; `sofascore_scraper/services/sync.py:596`, `:694`, `:871` at `48e4c4c`)
  added three codes: `sync_season_list_fresh` and `sync_schedule_fresh` say that a season list or a match
  list is within its freshness limit (6 h) and is not read again (the end-to-end test took the old line
  "Reading the season list of …" for an extra request; none was sent, finding F9), and `sync_extras_kinds`
  follows "Odds and non-match data: N stored, M failed" with the slice keys saved (`saved`) and those
  SofaScore did not have (`unavailable`), finding F18; the web UI names them by data type since FX-25.
- **Team, player and match follows download (FX-19; owner decision of 2026-10-06).** `sofascore_scraper/services/follow_sync.py`
  (new). A sync without a target, `ssc sync`, the scheduler and `POST /jobs {"kind": "sync"}` also download
  every enabled team, player and event follow (`sync_others`), in the full mode only; `follows=[…]` takes
  `team:`, `player:` and `event:` ids (it answered 400 `unsupported` after FX-13). The rules of the follow
  sync are in 3.2. Their matches go through the fetch pipeline with the follows' selection (3.1). An
  unreadable list is a `FailedListing` of kind `team_events` or `player_events`, whose `league_id` holds the
  team's or the player's id (`sofascore_scraper/services/sync.py:214-227`; a separate field would have changed the job
  records and the CLI output of every failed listing), and the job ends `partial`.
- **Per-league delete (FX-19).** `MaintenanceService.clear_tournament(tournament_id, season_id=None,
  confirm=True)` (`sofascore_scraper/services/maintenance.py:106-119`) calls `Store.purge.tournament` (`01-storage.md`
  9.3): one tournament's events in both layouts with their history, its schedules and, for the whole
  tournament, its season list; follows, the change log, the job history, backups, exports and the team and
  player directories stay. It holds `maintenance` like `clear` and rebuilds the catalog under the same
  lease. The `clear` job takes `tournament_id` and `season_id`, and `DELETE /follows/{id}?delete_data=true`
  starts that job (6). There is no CLI command for it.
- **Connection state (FX-19).** The request layer reports the end of every request (`sofascore_scraper/breaker.py:298-326`)
  to `ConnectionState` (`sofascore_scraper/bridge_health.py:247-306`): an answer (200 or 404) is success; 403, 429, 5xx,
  a timeout, a network or a parse error is failure; a request the breaker held back counts for neither.
  `state` is `never_tried` (no request has ended), `ok` (the last one was answered) or `failed`, with the
  last success and failure times, reason and HTTP status, and `last_check {at, ok, reason}` of the last
  `POST /status/check`. The bridge's own state, series and last error are unchanged, but the API shows the
  bridge's `last_success_at` and `last_failure_at` over every transport (`public_snapshot`, `:332-344`),
  because curl answers never reach the bridge. The state is per process: the web server does not see the
  requests of `ssc` commands or `ssc watch`. As built since B2 (PR #191; `sofascore_scraper/bridge_health.py:240-345`
  at `216c2f9`): every record takes a sequence number, and `state` is the last recorded outcome by that
  order. Before, `ok` was decided by `success >= failure` on times of second precision, so a failure in the
  same second as an answer still read `ok`. `last_check.superseded` (new) says that an answer came after a
  failed check, by the same order; the web UI uses it. Since FX-30 (PR #180) `record_check()` returns the
  moment it recorded, and `POST /status/check` stamps `checked_at_utc` with it, so the two are equal (they
  were two clock reads).
- **Export names (FX-19).** An export job writes `exports/<label>_<UTC date>_<id8>.<ext>`
  (`sofascore_scraper/services/data_jobs.py:157-174`): the label is the tournament's name (from the follows table, else
  the catalog) when the filter names one tournament, else the dataset, as a lower-case ASCII slug of at most
  40 characters, with `-raw` for a raw export and `-wide` for the 2.x wide CSV; `id8` is the last 8 letters
  or digits of the job id. For example `premier-league_2026-10-06_x7k2m9qa.csv`. Jobs before FX-19 keep
  `<job id>.<ext>`. Since FX-34 `ssc export` without `--out` uses the same label and UTC date with the UTC
  time in place of the job id (`local_export_name`), e.g. `exports/premier-league_2026-10-06_142530.jsonl`;
  `GET /exports` lists those files too (6).
- **Odds and season data (P28).** `OwnerDataService` (`sofascore_scraper/services/owner_data.py`, new) reads odds
  snapshot by snapshot from the slice history (`store.history.snapshots`), falling back to the stored
  payload of a slice without history, and maps them to the schema's `Odds` records; it reads the season
  slices and the standings rows. `ExportService` has two more normalized datasets, `odds` (one row per
  outcome per snapshot) and `standings` (the seasons of the matched events), reachable from `ssc export
  --dataset` and the v1 export job. Their models are in `MODELS` since FX-21 (#164), and `Odds`,
  `OddsLine` and `StandingsRow` in `RECORDS` (`04-schema-v1.md` section 4, "Odds and standings").
- **Counts and the selection (P27, FX-13).** `StatusService.season_counts` (FX-13, `sofascore_scraper/services/status.py:458`)
  gives per season `events`, `finished`, `details`, `complete`, `completion_rate`, `missing` per slice and
  the schedule's `fetched_at`. It and `summary`'s per-tournament coverage count the missing slices with
  `planning.missing_slice_keys` without a Store, that is with the configured selection only
  (`:430`, `:481`): a follow's own selection in the follows table is not applied there, and with a narrower
  selection those counts can call a match incomplete while the planner needs `none`. The same holds for
  `slices_summary` and the `not_requested` placeholders of `/events/{id}/slices` (`sofascore_scraper/services/query.py:496-536`,
  the registry's default selection) and the legacy completeness (`required_detail_keys`, `:129-140`). P30
  (#186) passes `planning.configured_policy(store)` there (`sofascore_scraper/services/query.py:403`, `:464`,
  `sofascore_scraper/services/status.py:467`, `:515` at `216c2f9`), so these counts and placeholders follow the
  configured selection and the follows table; the placeholders of slices with subs (odds) carry
  `spec.sub_keys(policy.provider)`.
  As built since FX-23 (PR #171; `48e4c4c`): the counts and the coverage no longer use the planner's rule
  for a finished match. They call `planning.unresolved_slice_keys`
  (`sofascore_scraper/services/planning.py:374-386`; `sofascore_scraper/services/status.py:432`, `:484`), which
  is `missing_slice_keys` except that on a finished match a slice whose last answer was "no data" counts as
  resolved. The end-to-end test showed "0 % complete" for a UEFA Super Cup season whose every match had its
  details, because a slice that answered "no data" once (a pre-game form, standings of a one-match cup)
  stays missing for the planner until a second answer confirms it (3.2). A slice whose last request failed,
  and every slice of a match that has not finished, still counts as missing. The planner is unchanged
  (3.2), so completeness and the planner's need can differ for such a slice until the confirming request.
  As built since FX-26 (PR #174; `43ecdfc`; findings M12 and M12b of the live validation): completeness
  counts finished matches only, for every kind of follow. The EHF Champions League showed "67 %" after its
  first download because 14 matches with a stored `/event` had not been played yet, and a team's page and
  a league's page counted their fixtures by different rules. `SeasonCounts` and the per-tournament
  `TournamentCounts` gained `finished_details` (finished matches with a stored `/event` payload;
  `sofascore_scraper/services/status.py:94-103`, `:256-282`), `complete` and `missing` are tallied over those,
  and `completion_rate` is `complete / finished_details`; `/status` gives `finished_details` per tournament
  from the pass over the unfinished events with details that `_match_counts` already made
  (`_unfinished_details`, `:532`). The web UI counts a team's and a match's coverage the same way from their
  stored finished matches, and a fixture is "not played", not missing (`05-web-ui.md` 6.4, 6.5).
  `StatusService.coverage()` (`CoverageReport`, no UI or CLI consumer; its tests pin the planner's rule) is
  unchanged.
  As built since FX-27 (PR #175; finding V5): an `empty` slice resolves completeness only when its answer was
  counted (`empty_count + unverified_empty_count > 0`, `_counted_empty`,
  `sofascore_scraper/services/planning.py:390-392` at `43ecdfc`). A "no data" answer taken while the match
  was on opens an uncounted `empty` row (3.3), so after the end the slice stays unresolved until the first
  post-match answer counts it; FX-23's case, a finished match whose answers are all counted, is unchanged.
- **The `events` of an export (FX-26, PR #174; finding M18).** An export result's `events` is the number of
  distinct matches the file covers: for a raw export the matches with at least one payload written (as
  before), for a normalized export the matches of its records (`events`: the rows; `slices`: the owners of
  kind `event`; `changes` and `odds`: `event_id`; `standings`: 0, not tied to a match;
  `_counting_events`, `sofascore_scraper/services/export.py:118-133` at `43ecdfc`). It was 0 for every
  normalized export (846 rows, `events: 0` in the validation). The wide CSV is unchanged.

As built at `216c2f9` (P30 #186, B1 #190, B2 #191). Where the blocks above differ:

- **The detail phase without the fetcher faces (P30).** `SyncService` runs the detail phase, the matches
  picked by id and the refresh-only mode through `DetailPhase` (`sofascore_scraper/services/detail_phase.py`):
  `candidates` (the catalog's detail candidates with `fetch.only_finished`), the plan with a need cache for
  the job, the download through `FetchPipeline`, the breaker result (`breaker_tripped`, `status_counts`) and
  the listener of the refresh counter. It is the work of `MatchDataFetcher`'s entry points
  (`collect_detail_match_ids`, `pending_detail_ids`, `fetch_detail_ids`, `fetch_matches_batch`,
  `refresh_due_ids`, `refresh_matches`, `reset_unavailable_markers`), with the same requests, records, order
  and counts. One pipeline is built per `fetch` or `refresh` call (one per league batch of a sync job), not
  one per job. The listings run through `ListingService` (`list_seasons`, `list_schedule`). The ignored
  `only_finished` and `save_empty_rounds` keywords of the pipeline and the listing are gone, and so is
  `SKIP_NOT_DUE`; `ListingService` keeps `only_finished`, which decides which chunks of a schedule count.
- **Team record (B1).** `QueryService.team(team_id)` (`sofascore_scraper/services/query.py:612-627`) returns a
  `TeamEntry`: the schema v1 `Participant` built from the catalog's `participants` row (sport, type, name,
  short name, slug, name code, country, `gender`, `national`) and whether a `team` follow names it. In the
  individual sports the "team" is the player or the pair. Route `GET /teams/{team_id}` (6); `None` until an
  event of that team is stored. The team hits of `suggest` carry `gender` and `national` from the same rows.
- **The `individual` flag of a sport (B1).** `SportSpec.individual` (`sofascore_scraper/sports.py:95`) and
  `sports.is_individual()`: tennis, badminton, table tennis, padel, snooker, darts and MMA, the sports
  whose players SofaScore lists as teams; e-sports is a team sport. `/sports` reports it (6), and the web UI
  reads it instead of its own list.
- **"No team" (B1).** A search hit of a player whose team is SofaScore's placeholder "No team" (any case,
  also `no-team`; `NO_TEAM_NAMES`, `sofascore_scraper/services/follows.py:592`) has no team (`team_id` and
  `team_name` None); the placeholder's sport is still used for the hit's sport.
- **The participant filter of an export (B1).** `ExportSpec` and `DatasetFilter` have `team_ids` and
  `player_ids` (`sofascore_scraper/services/export.py:105-106`, `:593`). A team selects the events with it on
  either side (the catalog's event participants); a player the events whose stored line-ups name him
  (`lineups`, starters and substitutes; missing players do not count), and since FX-34 first the events of
  the player follow's stored match list (B2, `follow_events:player:<id>`), so a followed player's events
  are found without line-ups. The two are one filter (an event of any of them), combined with the other filters
  (AND). They are resolved to event ids before the export (`ExportService.participant_events`, `:305`),
  reading only the line-ups of the events inside the other filters' scope; no match exports nothing, never
  everything. Every dataset, the raw export and the `legacy-wide-csv` profile take it. The export job's
  `ExportFilter` takes both (ids above 0, else 422), and one team or player without a league names the file
  after the follow (or the team's catalog name). An event downloaded without its line-ups and outside a
  player follow's stored list is not found by a player filter, and with no other filter a large data folder means one payload read per event with
  line-ups (logged as "Player filter: N stored lineups read").
- **Counts per follow and a player's matches (B2).** `StatusService.follow_counts(follows, tournaments=)`
  (`sofascore_scraper/services/status.py:547-580`) gives for every follow its stored events, finished events
  and finished events with a stored event payload, by FX-26's one rule; a tournament follow is counted from
  the summary's row. A player's stored events do not say who played, so a sync keeps the match ids of a
  player follow's last match list in the state database's runtime facts (`store.runtime`, key
  `follow_events:player:<id>`; ids only, no schema change; `remember_listing` and `listed_events`,
  `sofascore_scraper/services/follow_sync.py:301-338`), and the counts and `GET /events?follow=` read them.
  A player that no sync has listed yet is not counted (`counted: false`). `/status` gains three indexed
  catalog counts per follow.
- **The connection state (B2, FX-30)**: above, under "Connection state".
- **Removed by FX-15.** `SyncSpec.export`, `export_all_csv`, `QueryService.detail_needs` and `refresh_due`
  (only tests called them), `league_sports.resolve_all`, and the `MatchDataFetcher`, `MatchFetcher` and
  `SeasonFetcher` methods only the terminal menu used: `MatchFetcher` is `list_schedule` plus the static
  names, `SeasonFetcher` the season-list face (`fetch_seasons_checked`, `list_seasons`, `get_season_name`,
  `resolve_season_id`). `MatchDataFetcher` keeps `fetch_match_data`, `refill_missing_match_slices`,
  `refresh_match`, `SingleFetchReport` and `_save_match_data` for tests only; P30 ports their tests to
  `FetchPipeline` when the module goes. The fetchers and `ServiceContext` no longer create empty `seasons/`
  and `matches/` folders (`DATA_SUBDIRECTORIES` is `("match_details", "datasets")`). P30 (#186) removed the
  three fetcher modules and ported their tests; FX-33 (#188) removed the creation of `match_details/` and
  `datasets/` (2.3).

### 2.8 Jobs: one model for web and CLI, across processes

```python
class JobKind(str, Enum): SYNC, FETCH, REFRESH, EXPORT, BACKUP, RESTORE, CLEAR, MIGRATE, REBUILD
class JobState(str, Enum): QUEUED, RUNNING, SUCCEEDED, PARTIAL, FAILED, CANCELLED, INTERRUPTED

@dataclass(frozen=True)
class Job:
    id: str                    # a plain string; a sortable 26-character id (ULID) since P11, uuid4 in older rows
    kind: JobKind; state: JobState
    origin: Origin             # face: cli|api|scheduler|library, pid, host (pure data; the caller fills pid and host)
    spec: Mapping[str, Any]    # the service spec, JSON
    progress: Mapping[str, Any] | None      # JobProgress.detail(); for another process's job: its last progress event
    result: Mapping[str, Any] | None
    error: ErrorInfo | None    # code from 2.6 + message + details
    created_at: str | None; started_at: str | None; finished_at: str | None   # ISO-8601 UTC, as in today's rows
    heartbeat_at: int | None   # epoch milliseconds
    cancel_requested: bool

@dataclass(frozen=True)
class JobEvent:                # one row of the job's own event log (the job_events table)
    job_id: str; seq: int      # seq starts at 1 for every job
    ts_ms: int                 # epoch milliseconds
    type: str                  # started | phase | progress | log | failed | breaker | cancel_requested | finished
    data: Mapping[str, Any]

@dataclass(frozen=True)
class JobOutcome:              # what a job body may return; anything else: the terminal-state rule decides
    state: JobState | None = None; result: Mapping[str, Any] | None = None; error: ErrorInfo | None = None
    message: str | None = None             # card text and last job-log line (free text, for today's clients)
    code: str | None = None; params: Mapping[str, Any] = {}   # translation key of `message` and its parameters
    percent: int | None = None

class JobNotActive(RuntimeError): ...      # `run` was called for a job that is not the running job of this process

class JobManager:
    def __init__(self, store: JobStore, *, cancel_poll=1.0, heartbeat=5.0, progress_interval=0.5): ...
    def start(self, kind: JobKind, spec: Mapping, *, origin: Origin, wait_for_lease: float = 0.0,
              payload: Mapping | None = None, lease_purpose: str | None = None,
              lease: str | None = None) -> Job: ...
        # takes the `writer` lease (lease="maintenance": clear and rebuild jobs, P21 #126) and writes the
        # row as running; does not execute the job
        # raises JobRunningError / DataOperationRunningError when the lease is held and the wait expires
    def run(self, job_id: str, fn: Callable[[JobHandle], Any], *, phases: Sequence[str] = (),
            on_change: Callable[[], Any] | None = None, on_log: Callable[[str], Any] | None = None) -> Job: ...
        # executes a started job in the caller's thread and returns the finished job
    def submit(self, kind: JobKind, spec: Mapping, fn: Callable[[JobHandle], Any], *, origin: Origin,
               background: bool, wait_for_lease: float = 0.0, phases=(), payload=None, lease_purpose=None,
               lease=None, on_change=None, on_log=None) -> Job: ...
        # start + run. background=True: dedicated thread (web, scheduler); False: caller's thread (CLI, library)
    def get(self, job_id) -> Job | None ; def list(self, *, limit=20, kinds=None, states=None) -> list[Job]
    def active(self) -> Job | None                         # the job running on the data directory, in any process
    def cancel(self, job_id) -> bool                       # works from any process
    def events(self, job_id, *, after: int = 0, follow: bool = False, poll: float = 0.25) -> Iterator[JobEvent]
    def reap_stale(self) -> int

class JobHandle:                                           # what a service sees
    id: str
    def cancelled(self) -> bool
    progress: JobProgress                                  # built by the manager from `phases`
    def log(self, message: str, **fields) -> None          # `code=...` lets a client build the text from the code
    def publish(self, fields: Mapping[str, Any]) -> None   # job fields that JobProgress does not carry
```

The block is the job model and the job manager as built by P07 (PR #35) and P11 (PR #69; `sofascore_scraper/jobs/model.py`
and `sofascore_scraper/jobs/manager.py` at `e0bae0c`). The first version of this section had `submit` as the only entry of
the manager, a handle without `publish`, and neither `JobOutcome` nor `JobNotActive`; "As built" at the end
of the section says why each differs. Both faces use the manager since P11: the web job of
`POST /api/fetch`, `POST /api/v1/jobs` (section 6), and the headless downloads (`--headless --update-all`,
kind `sync`) and refreshes (`--refresh-only`, kind `refresh`) of `main.py`. A headless run therefore appears
in the job history (`/api/jobs`) with its progress, log lines and result. At `b3cb819` the users of the
manager are `ssc sync`, `fetch` and `refresh` (P19 #119; the legacy flags of `main.py` are translated to
them), the web download job of the legacy routes (`sofascore_scraper/web/api/legacy.py`), `POST /api/v1/jobs` with the
kinds `sync`, `fetch`, `refresh`, `export`, `backup`, `clear`, `rebuild` and a `restore` check (P21 #126),
and the in-app scheduler (P29 #128). The other CLI commands that write are not jobs: `ssc export` takes no
lease, `ssc backup create` takes `writer` itself (purpose `op:backup`), `data recheck-unavailable` takes
`writer` itself, and `data clear`, `backup restore`, `migrate` and the catalog commands go through the
Store or the service, which take their lease (2.7).

Mechanics:

- **Storage.** The `jobs` and `job_events` tables of `DATA_DIR/.meta/state.db` (`01-storage.md` 3.3),
  written by the job store (`sofascore_scraper/store/jobs.py`). The manager stands on a job store and holds no state of
  its own: the context builds it on `JobStore.for_store(store)` (2.3; since ST-11, PR #75, the same object
  is `store.jobs`), the web on its process-wide job store. Today's columns are kept, so old rows stay
  readable; `.meta/jobs.db` is imported once and left in place. Migration 0002 (P11) added the columns
  `origin_json`, `spec_json`, `error_json`, `heartbeat_at` and `created_at` and the `job_events` table, so
  `state.db` is at schema version 2 and a build from before P11 refuses the directory afterwards
  (`SchemaTooNew`). A row written before the migration reads `started_at` as its `created_at` and has the
  origin `api`. State names are normalised on read. Success is still written to the `status` column as
  `completed`, the 2.x name, and read as `succeeded`; `partial` is the only new value in the column. A
  `completed` row whose `circuit_breaker_triggered` column is set (a row from before P11) reads as `partial`
  (the column, not the text, because the text is localised), and a status text that is not recognised reads
  as `failed`. `Origin` and `ErrorInfo` are defined in `sofascore_scraper/jobs/model.py` since P07 (PR #35), `JobEvent`
  since P11.
- **Leases** are the Store's (`01-storage.md` 6.1), taken with `store.lease(name)`:

  | Job kind | Lease |
  |---|---|
  | sync, fetch, refresh | `writer` (purpose `job`; the headless runs of `main.py` pass `headless` and `refresh` through `lease_purpose`) |
  | reset of the "unavailable" markers (`--recheck-unavailable`) | `writer` (purpose `recheck-unavailable`): it rewrites marker files of stored matches. Not a job: `main.py` takes the lease itself |
  | backup | `writer`, as a data operation (purpose `op:backup`), so a refused request gets `data_operation_running` |
  | migrate | `writer`, and no live service may be running |
  | clear, restore, rebuild, data-directory change | `maintenance` |
  | export | none; it reads one catalog snapshot (as built since FX-23: the API's export job takes `export`, below; `ssc export` none) |
  | live service | `live` |
  | sink dispatcher | `sinks` |

  As built at `b3cb819`, where the table differs. An export **job** of API v1 takes `writer` (P21 #126):
  every job of the manager holds a lease, and an export without one would need a third kind of job row, so
  an export through the API is refused while a download runs (until FX-23, below). `ssc export` is not a job and takes no lease.
  A clear and a rebuild job hold `maintenance` (`JobStore.create_running(lease="maintenance")`, P21 #126;
  any other name is `ValueError`, a store does not lend its running job's lease to a job of another kind,
  and `reap_stale` leaves the row alone while another process holds `maintenance`); `Store.clear` and
  `MaintenanceService` then run under it. Restore is not a job: `BackupManager.restore` loads the backup's
  `state.db` over the open one, which would replace the running job's own row, so the API runs the check
  (`dry_run`) as a `writer` job and the restore is `ssc backup restore`, which takes `maintenance` itself
  (ST-24 #109). Migrate takes `writer` and `live` itself (ST-23 #110), so a download, `ssc watch` and
  `--watch` refuse it (exit 6). A data-directory change from the Settings is still refused while a job runs
  through the job store, not under `maintenance`.

  As built since FX-23 (PR #171; `48e4c4c`), for the export job. The end-to-end test could not start an
  export while a download ran ("another job is writing to the data folder"), which for a big league is
  hours (finding F14). An export reads the catalog and the payloads (SQLite WAL readers, files written
  atomically) and writes only into `exports/` and its own `export.<random>` staging entries; it held
  `writer` only because every job of the manager holds a lease. Now:
  - a new lease `export` (`sofascore_scraper/store/lease.py:16`, `:78`, `:193-194`): `export.lock` exclusive and
    `maintenance.lock` shared. One export runs at a time; it runs next to `writer`, so during a download;
    clear, restore, rebuild and a data-folder change (all `maintenance`) wait for it, because they would
    change what it reads. A held `export` lease, or a running maintenance job, answers 409
    `data_operation_running`. `ssc export` is still not a job and takes no lease;
  - the API runs an export job in a job store of its own on the same `state.db`, closed when the job ends
    (`_start_export`, `sofascore_scraper/web/api/v1/jobs.py:860-892`), so the download's running row and the
    web's live mirror are untouched; cancel and reads go through the web's job manager as for any job (the
    row's cancel flag). `JOB_LEASES` is `writer`, `maintenance`, `export` (`sofascore_scraper/store/jobs.py:67`);
  - `reap_stale` treats running rows of kind `export` as alive while the `export` lease is held, and an
    export store's lease does not count as "holds writer", so it never marks the download's row
    interrupted (`sofascore_scraper/store/jobs.py:566-577`).
  Queueing the export behind the download was the fallback and would still have made the user wait for the
  whole download; re-tested against the real site, a CSV export finished while a Süper Lig season
  downloaded.

  This preserves today's rule "one job at a time" (`web/jobs.py:151-168`, `:193-223`) and extends it to all
  processes. As built: `JobManager.start` takes `writer` through the job store. `main.py` no longer takes it
  for a download or a refresh; its helper `_data_dir_lease` (`main.py:160` at `e0bae0c`) still opens the
  Store for the lease and closes it afterwards, and is used only by `--recheck-unavailable` alone and by
  `--watch` (`watcher:<sport>`). The P10 tests pin the purposes `headless` and `refresh`, which is why
  `start` has `lease_purpose`; a refused process still reads `purpose headless` in the holder's text.
  `_data_dir_lease` went with the flags of `main.py` (P30 #186); nothing in 3.1 takes `watcher:<sport>`,
  and the Store keeps the name only so that a 3.0 `--watch` process on the same folder is still excluded.
- **Start and run.** `start` takes the lease and writes the row; `run` executes a started job in the
  caller's thread; `submit` is both. The split exists because of the web: the route calls `start`, which
  answers a held lease with 409 in the request itself, and the thread calls `run`
  (`src/web/routes/scrape.py:113`, `src/web/fetch_job.py:179` at `e0bae0c`). Tests pin
  `run_fetch_job(job_id, payload)` for a job that already exists and replace that function as a hook.
  `phases` are the `JobProgress` phases the handle's `progress` is built with (for a sync,
  `SyncSpec.job_phases`). `payload` is the request body the legacy API shows and builds the card title from;
  without it the spec is stored in its place. `lease_purpose` is what a refused process is told (default
  `job`). `on_change` is called after every write to the job row (the web refreshes its live mirror);
  `on_log` is called with every job-log line (`main.py` writes it to the log as before). `wait_for_lease`
  retries the lease every 0.1 s until the time is up. The manager refuses a second job on a job store that
  already runs one (`create_running(replace_running=False)`, `JobRunningError`); a direct call of
  `JobStore.create_running` still reuses the lease, and since P11 it leaves the first row `interrupted`
  instead of running forever. When the body raises, the job is stored as `failed` with the code of the error
  table and a masked message (Ctrl+C: `cancelled`), the lease is released and the exception goes on to the
  caller; in a background thread it is logged. `run` raises `JobNotActive` for a job that is not the running
  job of this process.
- **The end of a job and its lease (#88, #93).** The text above says the lease is released when the job
  ends. Until #93 it was released in the final row update (`update(finished=True)`), while the job thread
  still appended `job.finished` and read the job back. Closing the state database in that window
  (`JobStore.rebind` after a data-folder change in the Settings, or `JobStore.close()` / `Store.close()` by
  a library user while a background job finishes) closed the SQLite connection under the job thread and
  could crash the process; #88 found it while making the tests join the job thread before teardown (4 of
  100 runs crashed before), and a stress run crashed 14 of 60 processes on main. As built since #93:
  `JobManager.run` wraps its last store accesses (the final row, the `job.finished` stream event, the
  read-back) in the job store's private `_job_finishing()` block (`sofascore_scraper/jobs/manager.py:358-375`,
  `sofascore_scraper/store/jobs.py:417-448` at `9b03c64`), and the `writer` lease is released when the block ends. While
  the block lasts, `rebind` and `exclusive` raise `JobRunningError` (409 `job_running`), `JobStore.close`
  waits for it (at most 30 s, then it logs a warning and closes), and `create_running` on the same job store
  waits instead of reusing the lease that is still held. Another process can see the row terminal while the
  lease is held a few milliseconds longer; that window existed between the commit and the release before.
  A direct caller of `update(finished=True)` outside the block releases the lease in the update, as before.
  Not covered: `Store.close()` closes its state database directly and does not wait for a finishing job of
  `store.jobs` (`sofascore_scraper/store/api.py:655-666`); one wait through a small public `JobStore` helper closes that
  last path (plan item FX-12). Closing a Store while a job is in the middle of its run is not covered
  either.
- **Liveness.** A job is running if its row says so **and** the `writer` lease is held. A running row whose
  lease is free is marked `interrupted` by whoever notices (`reap_stale`, `sofascore_scraper/store/jobs.py:449` at
  `e0bae0c`): when a job store is opened or rebound, when a job is created (under the lease) and whenever
  the history is read (`get`, `list`, `active`, `cancel`). As long as another process holds `writer`, no row
  is touched, so starting a second server or a CLI run no longer marks the first one's running job
  interrupted (2.x and ST-10 swept unconditionally, `web/jobs.py:129-148`), and a job whose process died is
  marked interrupted by the next process that opens the directory, lists the history or starts a job. The
  unconditional sweep was kept as `mark_stale_running_interrupted`, an explicit call for a process that
  gives up its own job; nothing calls it by itself, and `tests/characterization/test_api_misc_goldens.py`
  still uses it, so that file is unchanged. `heartbeat_at` is written to the job row every 5 s by the job's
  ticker thread, for display only; `leases.heartbeat_at` is not written.
- **Cancel.** `cancel(job_id)` sets `cancel_requested` in the row and works from any process. The process
  that runs the job keeps the flag in memory (a cancel in the same process sets it directly); the ticker
  reads the row every second (`poll_cancel`) and copies a request of another process into it.
  `JobHandle.cancelled` is the cancel check installed in the request context, so waits and retries stop
  within a fraction of a second as today (`fetch_job.py:73-76`). `POST /api/scrape/cancel` cancels the
  server's own job, and when the server itself runs no job it cancels the job another process runs on the
  data directory (`src/web/routes/scrape.py:80-94`), for example a headless run. A CLI run cancelled that
  way prints `Program terminated by user.`, as after Ctrl+C, without the summary line, and exits with 0
  (`main.py:248-251`). The progress write of the job store used to write `cancel_requested` from its
  in-memory copy and could overwrite a request of another process; it now writes the larger of the row and
  the copy (`sofascore_scraper/store/jobs.py:802`).
- **Progress and events.** `JobProgress` publishes into the row (as today), and the handle derives the job's
  events from two successive progress snapshots: every phase change, log line, failed item and breaker stop
  is appended to `job_events`, progress events at most two per second (the last pending one is written by
  the ticker or at the end). A failed-item event does not carry a progress event with it, so a burst of
  failures stays within that limit. Event types: `started` ({kind, origin}), `phase`, `progress`, `log`
  ({message, plus the fields given to `log`}), `failed`, `breaker` ({reason}), `cancel_requested`,
  `finished` ({state, message, and `error` when there is one}). The `finished` event also carries `code` and
  `params` when the body gave them: the translation key of the card text, today `fetch_stopped_by_breaker`,
  `fetch_completed_with_warning`, `storage_error_abort` and `refresh_stopped_by_breaker`. `Job.error.code`
  is a code of the error table (a breaker stop: `blocked`, `rate_limited` or `upstream_error`).
  `Job.progress` is the live value for a job of this process; for a job of another process it is that job's
  last `progress` event, which lags by up to one ticker round (1 s). `events(follow=True)` yields new events
  until the job ends, also for a job of another process.
- **Time left (FX-26, PR #174; finding M14 of the live validation).** `JobProgress.eta_seconds`, which the
  row and `progress.eta_seconds` of the API carry, is the longest of three estimates
  (`sofascore_scraper/jobs/progress.py:208-245` at `43ecdfc`): the phase's average pace since its start
  times the matches left (the old rule); the pace of the last three minutes (`ETA_WINDOW`, 180 s), because the first
  matches of a phase often go fast (stored matches are skipped, the budget starts full); and, when the job
  planned its matches by cost class and reports each finished match's requests, the requests still to send
  divided by the job's measured request rate. The classes are `event_only` (an upcoming fixture of a team,
  player or match follow, whose `/event` alone is read) and the planner's needs `full`, `refill` and
  `refresh` (`_cost_class`, `_requests_of`, `sofascore_scraper/services/sync.py:318-333`); a class with no
  finished match yet is costed at the dearest class measured. The validation's Celtics download said 69 s
  at 31 of 128 matches, because its first matches were one-request fixtures and the remaining 97 needed
  about 9 requests each; it took about 7 minutes at 2 requests per second. The web UI already showed the
  longer of the server's estimate and its own (`05-web-ui.md` 6.9); the CLI and other clients get the
  corrected value now.
  Since B2 (PR #191) a league download's detail phase also plans its matches by need (`full`, `refill`,
  `refresh`) and reports each match's requests (`DetailPhase.result_listener`,
  `sofascore_scraper/services/sync.py:1092` at `216c2f9`), so the request estimate covers a league whose first
  matches are cheap refills too.
- **Request counters (B2, PR #191; finding F17).** `progress.requests` and `result.requests` of a job are
  `{sent, budget_wait_seconds, backoff_seconds}` (`JobProgress.requests`,
  `sofascore_scraper/jobs/progress.py:197-213` at `216c2f9`): every request the job sent to SofaScore (each
  retry, bridge fetch and session warm-up), the seconds they waited for the shared request budget, and the
  seconds of SofaScore's back-off (429, 403), each summed over the requests, so concurrent waits can add up
  to more than the job's duration; the mean wait is `budget_wait_seconds / sent`. They come from the request
  notifier of 2.4 and the back-off waits. A request does not write the job row by itself (the notice can
  come from the bridge's thread): the counters go out with the next progress event, and stay in the result.
  The CLI goldens of the job results carry the object (`sent` equals the golden's request log).
- **Names in the log parameters (B2, finding F8).** A sync's log line that names a league or a season also
  carries `league_name`, `season_name` and `season_year` (`resolved_name`, `resolved_year` for an outdated
  season id) from the stored season lists (`sofascore_scraper/services/sync.py:520-540`); the English messages
  are unchanged, and the web UI reads a season list only for the lines of an older server.
- **Stream events.** `job.started` and `job.finished` are appended to the `job` stream for sinks (section
  5), with source `job`: `job.started` has the data {job_id, kind, origin}, `job.finished` {job_id, kind,
  state, counts, error_code}, where `counts` are `details_done`, `details_total`, `failed_count`,
  `refreshed` and `refresh_changed`. The appends are best effort: one that fails (a busy `state.db`) is
  logged and not retried, so a sink can miss the start or the end of a job.
- **Origin in job events (decision D20 of 2026-10-02, `03-implementation-plan.md` section 13).** The origin
  is {face, pid, host}, so `job.started` and the `started` event carry the host name and the pid of the
  process that started the job, a sink subscribed to `job.*` delivers them, and API v1 shows `origin.pid`
  and `origin.host`. This is kept: a sink and the
  API deliver to the operator's own systems. The diagnostics bundle is different, because it is made to be
  shared with others, and it no longer carries the host name (below).
- **Where jobs run.** In the process that created them. There is no daemon and no queue; `queued` exists
  only for the in-app scheduler, which coalesces (a task whose previous run still holds the lease is skipped
  and logged). As built, `queued` is still never written, also not while `submit` waits for the lease: the
  row is written after the lease is taken. The scheduler did not change that (P29 #128): a due task either
  starts, its row written after the lease is taken, or is skipped and logged, so `queued` exists in the
  model and is written by nothing; a queued row would also contradict "skipped and logged".
- **The in-app scheduler (P29 #128; `sofascore_scraper/jobs/scheduler.py`).** It runs inside `ssc serve` and is off by
  default: `--scheduler` or `[schedule] enabled = true` turns it on (either one is enough), and
  `--no-scheduler` turns the setting off for one run. It does not run with `--dev` (the reloading server
  runs the app in a child process): `--scheduler --dev` is a usage error, and with the setting alone `--dev`
  gives the warning `scheduler_not_in_dev`. With no task configured it does not start (warning
  `scheduler_no_tasks`). A task is `run = "sync"`, `"fetch"`, `"refresh"` (option `league_id`) or
  `"backup"` (options `scope`, `include_env`), with `every` or `cron`; export, clear, rebuild and migrate
  cannot be scheduled. `check_tasks` rejects an unknown run name, option or value with `config_invalid`
  before the server starts, naming the task by its position (`[[schedule.task]] #2`); tasks have no name.
  `ssc config validate` and the loader do not check run names. `every` fires one interval after the start
  of the server and a restart resets the count (no anchoring on the job history); `cron` takes five fields
  in the machine's local time. A due task is submitted through the web process's job manager
  (`deps.job_manager`, origin `scheduler`, `background=True`), under `writer` like a job started from the
  web. If the task's previous run still runs, the run is skipped (`skipped_running`); if another job or data
  operation holds the lease, it is skipped too (`skipped_busy`). Missed times are not replayed. The loop
  wakes at least every 60 s; on shutdown a scheduled job still running is cancelled and waited for up to
  30 s. `GET /api/v1/status` has `schedule {enabled, next_runs[]}` (per task: `index`, `run`, `every`,
  `cron`, `options`, `next_run_at_utc`, `last_run_at_utc`, `last_job_id`, `last_result`) and
  `capabilities.scheduler`; both describe the scheduler of the answering process only, so `ssc status` and
  any other process see it as off.
  Changed since (`sofascore_scraper/jobs/scheduler.py` at `b6caf2f`): `ssc config validate` runs the same task check and
  fails with `config_invalid` (exit 2) on a task `serve` would refuse, and `describe config` lists
  `schedule_runs` (FX-13). An `every` task counts from its last run in the job history, not from the start
  of the server (FX-15; a delegated decision of 2026-10-03; `:31-35`, `_anchor` at `:515`): the last run is the
  newest job the scheduler started with the same kind and spec (tasks are matched by content, not position,
  among the newest 200 jobs); a restart before the interval is up waits for the rest of it, an interval that
  passed while the server was off runs once in the first round, and without a readable history or on a first
  start the first run is one interval after the start, as before. `cron` tasks are unchanged. A fifth run,
  `prune-history` (FX-15; a delegated decision of 2026-10-06; `:40-43`, `:327-389`), deletes the slice-history
  snapshots older than its required `older_than` (for example `"90d"`) through `Store.history.prune`, which
  keeps the newest snapshot of every slice; no task exists unless one is configured, so pruning is off by
  default. The run is an ordinary job under `writer` (ST-26), of kind `clear` with spec
  `{"scope": "history", "older_than": …}` and result `{"prune_history": {older_than, cutoff_utc, removed}}`;
  the API's `clear` job does not accept that scope, and the web UI names the job by its spec (FX-14b).
- **Terminal state rule.** `failed` if a fatal error aborted the job; else `cancelled` if cancelled; else
  `partial` if the breaker stopped it or any item failed; else `succeeded` (`model.terminal_state`). As
  built (P11): a job stopped by the circuit breaker, or one with a failed match, is stored as `partial`,
  with the breaker's error code in `error`. A job that finishes after a cancel request is stored as
  `cancelled`, including the case where its body returned success while the status was still Running. A
  final status the job store does not recognise is stored as `failed` with a warning instead of verbatim
  (`sofascore_scraper/store/jobs.py:839-858`). At `0aa73b4` a breaker stop was shown as "Completed" with a text
  (`fetch_job.py:223-226`); the legacy API and the live mirror still show a `partial` job as `completed` /
  `Completed`, because the goldens of G-04 pin those shapes.
- **Retention.** When a job is created, finished job rows beyond the newest 500 are removed, with their
  events; each job keeps its newest 2,000 events (`JOB_HISTORY_LIMIT`, `JOB_EVENTS_LIMIT`,
  `sofascore_scraper/store/jobs.py:66-67`). The events of a job are pruned at every hundredth event, so a job can briefly
  hold up to 99 more.
- **Ids.** New jobs get a 26-character sortable id (a ULID: 48 bits of time, 80 random bits, Crockford
  base32; `model.new_job_id`) instead of a UUID; ids made in the same millisecond of one process still
  increase. Rows written before keep their UUID, so an id is an opaque string.
- **A busy `state.db`.** A `state.db` that another writer keeps locked for more than 5 s (`StoreBusy`) no
  longer fails a job on a progress or log write: the write is skipped, the next one completes the row, and
  the first skip is logged as a warning. Event appends and stream appends are best effort in the same way.
  The end of a job is tried three times; if it still cannot be written, the error goes to the caller and the
  next reader marks the row `interrupted`. Other errors of a row write (a full disk, a corrupt file) are
  raised as before.
- **What the legacy routes show.** `/api/jobs` and `/api/jobs/{id}` return the 18 columns of 2.x with
  `partial` shown as `completed`; two jobs started in the same second are now listed newest first (the order
  was undefined). They still show an interrupted job with its last `current_task`, a cancel request without
  a log line, and the live mirror keeps the last finished job; the goldens pin that. In the new model these
  facts are events (`finished` with state `interrupted`, `cancel_requested`), and `JobManager.active()`
  never returns a finished job or a row whose owner died. `/api/scrape/status` and `/api/scrape/stream`
  still show only the server's own job: a headless run is visible in `/api/jobs` and in API v1, not on the
  live card. The web adapter wraps the handle only to print the console line of the CSV phase
  (`_ConsoleHandle`); EX-1 removes it with that phase. EX-1 (#99) did: the last job-log line of a web
  download is `Background update completed.`, and the module state behind these routes (the job store, the
  live mirror) is in `sofascore_scraper/web/deps.py`, created at the first request, not at import (P21 #127).
- **The command line.** `main.py` submits inline with origin `cli`
  (`ctx.jobs.submit(..., background=False)`, `main.py:412`). It catches the job store's conflict and
  re-raises the `LeaseHeld` behind it, so a refused lease still prints the holder and exits with 6. Its exit
  codes are today's: a breaker stop exits 2, and `partial` has no exit code of its own until P19 (4.5).
  `DetachedHandle` of `sofascore_scraper/services/sync.py` is no longer used by `main.py`. For P19:
  `ssc jobs list|show|cancel|tail` are `ctx.jobs.list`, `get`, `cancel` and `events(follow=True)`, and
  `--wait` is `submit(wait_for_lease=)`.
  As built by P19 (#119): `main.py` no longer submits anything; its flags run `ssc sync`, `fetch` or
  `refresh`, which call `ctx.jobs.start(..., origin=cli, wait_for_lease=<--wait>, lease_purpose=<the
  command's name>)` and then `run` in the caller's thread (`sofascore_scraper/cli/commands/sync.py:321-355`), so the
  purposes are `sync`, `fetch` and `refresh` instead of P10's `headless`. The exit code comes from the typed
  result: 3 for `partial` without a breaker stop, 4 for a breaker stop, 5 for a storage error, 6 for a held
  lease, 130 or 143 for a signal and 130 for a cancel from another process (4.5). `ssc jobs list|show|
  cancel|tail` are built as planned; `jobs list` filters by `--kind` and `--state`, and `jobs tail` takes
  `--after` and `--follow`.
- **Job rows in the diagnostics bundle (PR #71).** Since migration 0002 a job row holds the host name of the
  process that ran the job, and `sofascore_scraper/diagnostics.py` read the history with `SELECT *`, so every bundle built
  after a job carried it. The bundle now selects the job columns by name (`_JOB_COLUMNS`,
  `sofascore_scraper/diagnostics.py:63-69` at `e0bae0c`); a column added to the table later is not in the bundle unless it
  is added to that list, and a column the file does not have is skipped, so the 2.x `jobs.db` is read as
  before. Kept: the 18 columns of 2.x, `kind`, `created_at` and `heartbeat_at`. The origin is given as
  {face, pid, same_host} without the host name (`same_host` says whether the job was started on the machine
  that builds the bundle; null when no name was recorded). `spec_json` and `error_json` are decoded and
  redacted like every other section. `owner` (the random id of the lease holder) is dropped. The host names
  recorded in the selected rows (the origin, the lease holder in an error's details) and this machine's name
  are replaced by `***` in every string of a job, where the name stands as a whole word. The setup check's
  profile-lock detail no longer carries the lock's host name either (`"lock": "***-4242"`). One gap was
  left: `log_tail.txt` in the bundle was not masked for the host name, so a `LeaseHeld` text that a caller
  logs carried the name. FX-10 (PR #84) closed it: `log_tail_text()` applies the same masking after the
  secret redaction and the home-directory scrub, for this machine's name and the host names recorded in the
  listed job rows (`_log_hosts`, `sofascore_scraper/diagnostics.py:288-328` at `9b03c64`); if the job history cannot be
  read, the tail is masked for this machine's name alone. The order matters: with the host names first, a
  home directory named like the machine would no longer become `~`. `GET /api/logs` (the log view of the
  web UI) is not masked: it shows the log on the machine itself.

As built (P11), where the first version of this section differed:

- **`start` and `run` next to `submit`**, for the web's route and thread, as above.
- **`JobHandle.publish(fields)`.** The Protocol in `sofascore_scraper/services/sync.py` needs it: the flow writes
  `schedule_empty_seasons` to the job record in the middle of a run and `JobProgress` does not carry it.
  `log(message, **fields)` is as designed; the services do not pass `code=` yet (2.1).
- **More arguments.** `phases`, `payload`, `lease_purpose`, `on_change` and `on_log` on `submit` and `start`
  / `run` were not in the design; `JobOutcome` and `JobNotActive` are new. The job store's `update` takes
  `job_id` (a late write of a finished job is ignored when it is not the running job), `state` and `error`.
- **The Store is not opened by `build_context`** (2.3).
- **`queued`, `completed` and `Job.progress`** as described under the mechanics above.

Not built yet (with the owning item where the plan names one):

- `ssc jobs` commands and `--wait`; exit codes 3 and 4 for `partial` and a breaker stop (P19). Built by
  P19 (#119).
- `instance_running` in the job store's conflict mapping (2.6). Built by P23 (PR #91).
- `Store.close()` waiting for a finishing job of `store.jobs` (above; FX-12). Built by FX-12 (#100):
  `Store.close()` waits for it through `JobStore.wait_for_finishing_job`, at most 30 s.
- A job row written as `queued` (above; nothing writes it, P29 did not either).
- A restore as a job of API v1: it needs a job record that survives the restore, or a restore that keeps
  the `jobs` table (P21 #126; owner FX-13, section 6).
- A committed golden for the job row that a real `main.py` subprocess leaves (section 10).
- The web UI has no Stop button for a job of another process: the button exists only on the live card of the
  server's own job (FE-2).
- The single-match fetch still only asks `writer_busy()` and does not hold the `writer` lease
  (`src/web/routes/matches.py:514` at `e0bae0c`; P13). It went with the legacy routes (P30 #186); its v1
  face, a `fetch` job with `event_ids`, runs under the writer lease.
- The web's process-wide job store is created when `src/web/routes/common.py` is imported, so a web job
  still creates a job database under the data directory even when the job store in use lives elsewhere. P21
  moves the creation out of import time. Done by P21 (#127): `deps.job_store()` and `deps.config_manager()`
  are created at the first request, and `tests/test_web_deps.py` pins that importing the app creates
  neither the league file nor the job store. Importing `sofascore_scraper.match_data_fetcher` still creates both
  (`03-implementation-plan.md` section 15); that module is deleted since P30.

---

## 3. One fetch pipeline

### 3.1 Slice registry

`sofascore_scraper/sports.py` already has the table and the single selection point (`sports.py:170-218`, whose docstring
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

As built (P12, PR #106; per-sport slices, PR #121; `sofascore_scraper/sports.py:383-457` at `b3cb819`): `DetailSlice`
became `SliceSpec` and stays an alias. The fields `owner`, `subs`, `phases`, `group`, `keep_history` and
`max_age` exist and are checked when a row is built; every registered row is still an `event` slice of group
`core` without subs, history or `max_age` (owner slices, odds and the other groups are P28). Where the block
above differs:

- The completeness field keeps its code name `required`. `counts_for_completeness` is a read-only property
  that returns it, and the readers (`/api/sports`, `/api/v1/sports`, `describe slices`, `query.py`) still
  read `.required`.
- The default of `phases` is all three phases, not `{"post"}`: nothing filtered by phase before P12, and a
  `post` default would drop `pregame_form` and `h2h` from a phase-filtered selection. Only `esports_games`
  and `innings` are limited, to `live` and `post` (and, since FX-31, `statistics` and `point_by_point`;
  below).
- `statistics` is both a slice key and a group name in the block above. Such a name is allowed and selects
  both; `known_slice_names()` lists it once.
- `select_slices(owner, sport, selection=None, *, phase=None)` is the one selection point (`slices_for`
  forwards to it). `selection` is `None` (the registry's `default_enabled`), a list of names or a
  `SliceSelection(base, enable, disable)`; an unknown name raises `UnknownSliceName`, a `ValueError`, and
  `check_slice_names()` exposes the same check. No caller passes a selection yet and the config loader does
  not call the check yet; both come with P27.
- `sports` stays the allow-list, and one completeness value per slice was not enough once 21 sports were
  registered. PR #121 adds two per-sport sets: `not_in` (sports that do not offer the slice; it is never
  requested there, while it still applies to an unregistered sport) and `optional_in` (requested but not
  counted). `counts_for_completeness` stays the sport-independent default, and `counts_in(sport)` gives the
  answer for one sport. The registry has nine event slices: the six common ones, `point_by_point` (tennis,
  where it is optional, and darts), `esports_games` (e-sports, optional; SP-3, PR #118) and `innings`
  (cricket; PR #121). The legacy key lists (`DETAIL_SLICE_KEYS`, `REQUIRED_FILES`, `legacy_detail_keys()`)
  are "required and not sport-specific", so the two sport-specific required slices stay out of the 2.x
  layout. In the Store, `events.missing` read the entry `""` as applying to every sport, so a sport could not
  drop a common slice; with the new keyword `exclusive=True` a sport's own entry replaces `""`, and
  `required_detail_keys()` lists every registered sport in full. Which sport requests what, and on how little evidence (one match page per sport), is the section
  "Maç detay dilimleri, spor başına" of `docs/all-sports/README.md`; the proposals for football, basketball
  and tennis waited for the live validation run (section 11) and are applied since FX-16 (below).

As built by P27 (PR #134), P28 (PR #140), FX-15 (PR #155) and FX-19 (PR #156) (`sofascore_scraper/sports.py` and
`sofascore_scraper/services/planning.py` at `b6caf2f`). Where the text above differs:

- **The selection is resolved per event, not per follow** (`SelectionPolicy`, `sofascore_scraper/services/planning.py:104-253`).
  An event can be covered by an event, a tournament, a team or a player follow, or by none, and the narrowest
  follow that gives a selection wins: the event follow, the tournament follow, the home team's, the away
  team's, and last the player follow whose list brought the match (`via_events`, `with_follow_events`,
  `:182-193`; FX-19, because the match payload does not name the players). A follow that gives no selection
  leaves the sport's selection. `for_owner` resolves the selection of a non-match owner: its own follow (team,
  player), else the follow of its tournament (season), then the sport and the defaults; the sport owner takes
  the sport's selection only (`:208-222`).
- **The chain** (`sports.resolve_selection`, `sofascore_scraper/sports.py:712-734`): a follow's `{"include": [...]}` (or a
  plain list) is the base and nothing else applies; otherwise `[defaults] slices` is the base, then
  `[slices.<sport>]` enable/disable, then the follow's enable/disable, each later layer overriding the earlier
  one, and inside one layer disable wins. The arrow notation above left the include case open.
- **An unset `[defaults] slices` is the registry's `default_enabled`,** not the shown default `["core"]`
  (`from_settings`, `:132-160`): `core` applied literally would also select the slices of group `core` that
  are off by default. A value set in any layer is used as written, so naming a group selects every slice of
  it.
- **`required` only counts for completeness.** Every data type is selectable (owner decision): the API and
  the config accept any registered name, and a slice with `required` true can be unselected; it then is not
  requested and counts for nothing. Completeness (`refill`) counts only slices that are selected and count in
  the sport (`counts_in`). An unknown name is a `ConfigError` that names where it is.
- **Where the selection is set.** `[defaults] slices`, `[slices.<sport>]` (also in `overrides.json`, so the
  web UI can write it; a sport set in the config file or the environment is locked), a follow's `slices`
  (config `[[follow]]` and the follows table, through `POST` and `PATCH /follows`), and `PATCH /settings`.
  The job spec has no per-job selection: a job uses the configured selection and the follows'
  (`FetchPipeline(selection=CONFIGURED)` resolves it once per run with the follows table). `ssc sync` has no
  `--slices`.
- **Phases** (as built at `b6caf2f`): every slice had all three phases except `esports_games` and `innings`,
  and narrowing them waited for evidence of which slices exist before kick-off. FX-31 (#185) narrowed
  `statistics` and `point_by_point` to `live` and `post` (below); the need row "not started or void, no
  pre-match slice selected" of 3.2 is still not built.
- **Odds (P28).** `ODDS_SLICES` (`sofascore_scraper/sports.py:556-570`): `odds_featured`, `odds_all`, `odds_changes` and
  `winning_odds`, owner `event`, group `odds`, `subs="provider"`, `keep_history=True`, `max_age` 30 minutes,
  `default_enabled=False`, `required=False`; `winning_odds` is `experimental` (its only sample is a 404;
  no longer since FX-27, below). The
  sub is `[client] odds_provider` (default 1; `DEFAULT_ODDS_PROVIDER`, `:385`). "Pre-match odds are refetched
  on each sync until kick-off" is built with a window: a not-started event's odds are read only within 7 days
  before kick-off (`PREMATCH_WINDOW_S`, `sofascore_scraper/services/planning.py:402`) when never read or older than
  `max_age`, and once more after the event ended when the last read was before kick-off; live and void
  events never. Without the window a season's whole fixture list would be requested on every sync, because
  the empty answer of an unfinished event is not counted. Each odds outcome records its provider in
  `meta.provider_id`; the country goes into `meta.country` only when the user sets `[client] odds_country`
  (FX-15; a delegated decision of 2026-10-06: opt-in, never derived from the machine; 4.3).
- **Non-match slices (P28).** `OWNER_SLICES` (`sofascore_scraper/sports.py:576-608`), all `default_enabled=False`,
  `required=False`, each with a `max_age`: season `standings` (subs `total` and `home`; there is no `away`,
  which the catalog does not have), `season_info`, `cuptrees`, `top_players` and `top_teams` (football),
  `season_odds` (football, provider sub, history, experimental); team `team_rankings` (tennis); player
  `player_statistics` (experimental); sport `rankings` (sub `5`, the ATP list; tennis; experimental; `6`, the
  WTA list, since FX-27). The
  design's `players` (owner team) and the `squads` group's slices are not built: the catalog has no such
  endpoint. `SliceSpec` gained `body_key` (the top-level key that must be non-empty for "data") and
  `experimental`, and the methods `sub_keys(provider)` and `format_path`. New groups `season`, `leaders` and
  `players` (`:382-383`); `standings` and `rankings` are both a group and a key, and naming either selects
  both. Which sports each owner slice exists in, the shapes of the experimental ones and which provider ids
  answer without a login wait for the live validation.

As built after the live validation of 2026-10-08, by FX-27 (PR #175) and FX-16 (PR #176)
(`sofascore_scraper/sports.py` and `sofascore_scraper/services/pipeline.py` at `43ecdfc`). Where the text
above differs:

- **The three proposals of #121 are applied** (FX-16, the owner's decision of 2026-10-08; `DETAIL_SLICES`,
  `sofascore_scraper/sports.py:506-542`). `pregame_form` is requested in football, basketball and tennis
  but no longer counts for completeness (`optional_in` gains the three); tennis no longer requests `lineups`
  or `incidents` (`not_in` gains tennis); tennis `point_by_point` counts (it lost `optional_in`), so it
  counts in tennis and darts. Tennis has five event slices (it had seven). Per finished match, with the
  answers the evidence records, football and basketball go from 1 + 6 requests plus one confirmation round
  for `pregame_form` to 1 + 6, and tennis from 1 + 7 plus a round for `pregame_form`, `lineups` and
  `incidents` to 1 + 5 (worked out from the rules, not measured). `GET /api/sports`, `/api/v1/sports` and
  `ssc describe slices` show the new rows. No slice key, status code or derived value changed, so
  `DERIVE_VERSION` stays 6 (`store/derive.py` does not read the slice table).
- **The evidence of FX-16.** `tests/fixtures/sport_slices/evidence.json` could not be regenerated on
  2026-10-08: the page-traffic tool `scripts/explore_all_sports.py` recorded no SofaScore request on the
  validation's match pages (finding V3; repaired by FX-29 and regenerated by FX-31 the next day, below). The
  validation gathered the evidence through the application instead: one
  single-match follow per sport with every slice selected, one finished match of 2026-10-07 for 20 sports
  (Aussie rules had none that day) and one live match on 2026-10-08 for 13 sports. The two tables are in
  `tests/fixtures/sport_slices/slice-matrix-finished.txt` and `slice-matrix-live.txt`, and
  `tests/test_sport_slices.py` checks the decision against them (`DECIDED` replaces `PROPOSALS`). They show
  tennis `point_by_point` with data on both matches, tennis `lineups` and `incidents` without, and
  `pregame_form` with data in football, without in finished basketball and tennis. They say only whether a
  requested slice brought data, not which endpoints the site's page asks for, which is what `not_in` rests
  on, so no other sport's row changed. In rugby, floorball, volleyball and minifootball most of
  `statistics`, `lineups` and `incidents` had no data on lower-tier matches (FX-31's proposal, below).
- **`legacy_detail_keys()`** listed the slices that are required and not sport-specific, for the legacy
  detail answer (`GET /api/matches/{id}`); P30 (#186) removed both with the 2.x routes. The 2.x layout is
  unchanged on purpose.
- **`winning_odds` is no longer experimental** (FX-27). Its shape was seen live (Süper Lig, provider 1):
  `{"home": null | {"fractionalValue", "expected", "actual", "id"}, "away": …}`, one side often null. It has
  a body rule of its own, `sided_body_state` (`sofascore_scraper/services/pipeline.py:650-662`): data when
  one side is a non-empty object, "no data" when both sides are null, absent or empty, unreadable when the
  body is not an object or a side is neither null nor an object. Before, any non-empty object counted as
  data, so `{"home": null, "away": null}` was stored as `ok`. It stays a raw slice: the normalized odds
  dataset and mapper cover `odds_all` and `odds_featured` only (`mappers.ODDS_KEYS`). The fixture
  `tests/fixtures/p28/winning_odds.json` has the seen shape with illustrative numbers.
- **`rankings` has the subs `5` (ATP) and `6` (WTA)** (FX-27, finding V8; `:609-613`): on the real site
  `/rankings/5` starts with Jannik Sinner and `/rankings/6` with Elena Rybakina (`/rankings/type/5|6`
  exist too, with another shape). It stays `experimental` (no recorded body). Subs are not shown in
  `/api/v1/sports`, `describe` or the OpenAPI document.
- **What the live validation answered for P28's open points.** Odds without a login: providers 1 and 5
  answer `/event/{id}/odds/{provider}/featured` with data on a live football match; 2, 3, 4, 6, 7, 8, 10
  and 15 answer 404, so the default 1 stands. Live football, basketball, tennis, handball and e-sports
  matches had odds (provider 1); live ice hockey, futsal, minifootball, volleyball, badminton, table
  tennis, cricket and MMA matches had none. Odds payloads carry a `liveStreamUrl` key, a third-party
  address: committed fixtures leave it out. `season_odds` (`/odds/season/{id}/provider/1/all`) answered
  with one market, "To Win Outright"; `player_statistics` with `uniqueTournamentSeasons` and `typesMap`;
  standings `total` and `home` both with data. `season_odds` and `player_statistics` stay experimental
  (no recorded body in the fixtures). Not answered: in which sports each owner slice exists beyond these
  samples.
- **Phases were still unchanged** after the validation, which read finished and live matches only; FX-31
  narrowed two of them (below).

As built by FX-31 (PR #185, with the research data of #183; `sofascore_scraper/sports.py:537-577`,
`tests/sport_evidence.py`, `tests/test_sport_slices.py` at `216c2f9`):

- **The evidence is regenerated.** The explorer was repaired (FX-29; "The research explorer" below), and its
  run `lv-20261009` (2026-10-09, 1 request per second) opened 39 match pages: finished matches of 20 sports
  and not-started matches of 19, 1,971 SofaScore requests, no intercept error.
  `tests/fixtures/sport_slices/evidence.json` is `python tests/sport_evidence.py` over both runs, with three
  rules (`tests/sport_evidence.py:1-60`). Answers count from every run: a recorded answer is a real
  SofaScore answer, and the old run (2026-10-01) holds the only finished and live team-streaks and h2h
  answers of football and basketball and the pregame-form 404s behind FX-16. Absence ("the page never asks
  for it") counts only from the pages of a repaired run (`REPAIRED_RUNS = {"lv-20261009"}`), because the old
  explorer lost requests without a trace: its rugby, minifootball and table-tennis pages answered 4 to 6 of
  their own endpoints, the repaired run's 13 to 15. The match state is the state at the request's time, from
  the event's own `/event` observation nearest in time (`seen_at`); three pages opened as "not started" were
  live when they loaded, and one darts match had finished. A `request-gone` row (FX-29b) enters the answers
  as `<state>:gone`: requested and never sent, neither absence nor data. `body_error` and `sample_redacted`
  rows count by their HTTP status. The app-side matrices of FX-16 stay as a second source: every optional or
  absent cell FX-31 set also had no data in the finished matrix (a test checks it).
- **Rows changed** (F, L, N = a finished, live or not-started match, with the HTTP status): `pregame_form`
  optional in American football (F404, F403, N200), badminton and table tennis (F404, N404), baseball (F404,
  L404, N404) and MMA (F200, F404); `lineups` not requested in badminton and table tennis (the complete
  finished page never asked); `incidents` not requested in baseball (two finished pages and a live one never
  asked) and optional in futsal (F200, F404, N404); `point_by_point` requested and counting in badminton and
  table tennis (F200 with per-set data, N404); `esports_games` counts for completeness (F200 with games, L200,
  N404).
- **Phases.** `statistics` and `point_by_point` are `live` and `post`: none of the 15 genuinely not-started
  pages asked for `statistics`, and no run has a not-started answer with data for either. No slice lost the
  live phase (`test_the_phases_follow_the_evidence`). `lineups`, `incidents`, `pregame_form`, `h2h` and
  `team_streaks` keep all three (football answered line-ups and incidents before kick-off). A not-started
  match no longer requests `statistics`.
- **`DERIVE_VERSION`** stayed 6 for FX-31: the catalog reads only the set of slice keys, not `sports`,
  `not_in`, `optional_in`, `phases` or `required`.
- **Open, for the owner.** A proposal for rugby, floorball, volleyball and minifootball (`statistics` and
  `incidents` optional, `lineups` and `pregame_form` optional in most of them, floorball `lineups` not
  requested; requests per finished match from 11 to 7, floorball from 12 to 6) is pinned in `PROPOSALS` of
  `tests/test_sport_slices.py` and described in `docs/all-sports/README.md`; the registry keeps the six
  common slices for these sports. The owner decided on 2026-10-09 to gather more evidence first: a daytime
  run of the explorer over top-league matches of the four sports, finished and live. A 200 is not always
  data: `team-streaks` often answers 200 with an empty body (`no_data` in 11 sports), while the rule reads
  status codes, so `team_streaks` stays required everywhere; a body-aware rule is a decision of its own.
  Baseball's `/umpires`, `/weather`, `/comments` and `/at-bats` answer 200 on finished and live matches;
  they are proposals, not slices.

**The research explorer** (`scripts/explore_all_sports.py`; FX-29 #181, FX-29b #182, FX-29c #184, FX-32
#187). It is research tooling outside the application, and the evidence above rests on it:

- **Interception through CDP Fetch.** The explore page gets its own CDP session with `Fetch.enable` for
  images, media, fonts, XHR, fetch and event streams at request stage. A paused request is an id, not a
  Playwright `Route`, so the driver's garbage collection (10,000 objects per type; the cause of V3: a stream
  of retried, aborted images disposed of the routes that waited for the lock) has nothing to collect. Every
  paused request is answered exactly once: a SofaScore API request after a slot of the shared research lock
  (at most 1 request per second, slots in turn), a third-party request at once, an idle or over-budget
  request failed, images with a 1x1 GIF (no failed image to retry), media and fonts failed.
  `Network.setBypassServiceWorker` keeps a service worker from sending page requests past the interceptor.
  Popups are closed; a captcha solve runs on Scrapling's own page and takes a slot before and after.
- **Liveness check (FX-29b).** SofaScore reloads `/football/match/…` as `/tr/football/match/…` (a client
  redirect, a new document), and Chromium silently drops the requests the old document had paused; a later
  `Fetch.continueRequest` answers "Invalid InterceptionId". The explorer asks `Fetch.getResponseBody`, which
  has no side effect at request stage, before it takes a slot and again after: a dead request takes no slot,
  is not counted and is logged as `request-gone` with its reason (a new document, a detached frame,
  `loadingFailed`). Requests still queued when a step ends are dropped without a slot; a response that
  arrives after its page left is still written, with `body_error`. `goto`, `click` and `fill` report
  `step_gone` and `step_intercept_errors`.
- **Probe self-check.** `serve` runs `probe` once at its start (the result is in its `ready` line): a fetch
  to `https://explorer-probe.invalid/` is held and answered locally; it spends no budget and nothing leaves
  the machine.
- **Budget per run.** `serve CTL --new-run [--run-id ID] [--max-requests N] [--max-hours H] [--out DIR]`
  starts a run at 0 and keeps the earlier ones in `_state.json` (`previous_runs`); every new row carries
  `run_id`. Without `--new-run` the last run continues, and a spent run exits before the browser opens.
- **Privacy (FX-29c).** Everything written passes `redact()` first: the client-location keys (`ip`, `city`,
  `region_code`, `f`, `client_ip`, `postal`, `latitude`, `longitude`, `lat`, `lon`, `geo`) are masked at any
  depth except under `venue` (public stadium data), every IPv4 and IPv6 literal becomes `<redacted>`, and
  the push server's `INFO` frames lose `client_ip` and every address; WebSocket text is masked before it is
  cut. `tests/test_research_privacy.py` scans `research/**` and fails on an address outside the
  documentation and private ranges or on an unmasked location value, naming only the file and the counts.
- **Settings (FX-32).** The research scripts set the 3.1 names `SOFASCORE_LOG__LEVEL` and
  `SOFASCORE_CLIENT__BROWSER_PROFILE` (a research profile under `~/.cache/sofascore_research/`, the shared
  one `chrome_research` by default, never the application's) and import `sofascore_scraper.client.bridge`;
  `sofascore_scraper/challenge_solver.py` is deleted. `tests/test_research_settings.py` checks both.
- **Open.** The explorer still opens the path without the locale and follows the redirect; going to the
  `/tr/` URL directly would save the extra document and its lost first request. The live-match-tracker
  iframe document (`/api/v1/event/{id}/live-match-tracker/…`, one per event page) is a Document request,
  outside the Fetch patterns, so it is sent without a slot and not counted.

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

As built at `48e4c4c` (FX-23, PR #171): the rule stands for the planner (`missing_slice_keys`,
`DEFAULT_EMPTY_THRESHOLD = 2`), so a slice that answered "no data" once is asked once more before it is
treated as absent. The end-to-end test saw this as wasted requests: a download resumed after a cancel asked
again for four matches stored seconds before, and each was "stored (0 files written)" (finding F29). It is
the confirmation, not a refresh (`REFRESH_MIN_INTERVAL_HOURS` governs the re-read of provisional finished
records), and it is pinned by the goldens `job_full_rerun` and `job_idempotency` ("slices that came empty
are tried once more"); it was kept. Completeness no longer follows this rule for finished matches: it
counts such a slice as resolved at once (`unresolved_slice_keys`, 2.7).

As built since B2 (PR #191; finding F29; `sofascore_scraper/services/planning.py:31-40`,
`sofascore_scraper/refresh.py:44-51` at `216c2f9`): the confirming request waits. The setting
`fetch.confirm_empty_after_seconds` (default 60, `RefreshPolicy.confirm_after_s`) makes a finished match's
slice whose one counted "no data" answer (`checked_at`) is younger than that "confirmation pending": it is
not a reason for a refill, and it is not asked along with another slice of a match that is read again for
another reason. It is a planning rule, not a sleep: nothing is sent and no slot of the budget is taken, and
the first download after the wait confirms. A job resumed right after a stop therefore no longer asks the
matches it stored seconds before, and a match that two follows bring into one job is not asked twice. An
uncounted "no data" (a match that had not finished, FX-27) does not wait: the match's first counted answer
comes after its end. A slice confirmed twice is never asked again. 0 is the old rule (the next plan
confirms). The tests pin it to 0 (`tests/conftest.py` and the environment of the CLI goldens), as they pin
`client.rate`, so the goldens `job_full_rerun` and `job_idempotency` are unchanged. 60 s rather than a few
seconds, because a stop and a resume through the web UI take longer than a few seconds.

As built since FX-27 (PR #175; finding V5 of the live validation): "recorded but do not count" now holds for
a body-less answer too. Until then the pipeline stored, for a match that had not finished, only the answers
with a body, so a slice that SofaScore answered with 404 during the match left no row, and the Data tab and
`/events/{id}/slices` reported it as `not_requested` although it had been asked (the statistics of a live
Botola Pro match at half time, the line-ups of an ice-hockey match in a pause). Now that answer opens an
uncounted `empty` row (state `empty`, counter 0), which the Store already defined (`01-storage.md` 2.3). The
planner's rule is unchanged: `slice_missing` is "not `ok` and fewer counted empties than the threshold", so
the slice stays missing and is asked again; the timed odds rule looks only at payloads, so pre-match odds
that answered 404 are still asked at every sync inside the window, as before. Completeness counts such an
`empty` as resolved only once a post-match answer has counted it (2.7). The legacy writer
`MatchDataFetcher._slice_outcomes` (`sofascore_scraper/match_data_fetcher.py:486`), which kept the old rule
off the pipeline's path, went with the fetcher faces in P30 (#186).

As built (P12, PR #106; P13, PR #113; ST-27, PR #129; `sofascore_scraper/services/planning.py` at `b3cb819`):

- `compute_need(state, selection, policy, *, threshold, layout)` (`:163-187`) takes no `now`, because
  `RefreshPolicy` carries it; `threshold` and `layout` are keywords, as in RD-3's `detail_needs`. `WorkItem`
  is as above. `work_item()` gives a refill item every selected slice that is still missing, optional ones
  included (`wanted_slice_keys`); `full` and `refresh` items carry no slices, because a full fetch chooses the
  slices once `/event` has told the sport. `plan_items()` builds the ordered items (full and refill, then
  refresh).
- Inputs: `event_needs` reads `store.events.states()` in chunks of 500 and decides in Python;
  `refresh_due_events` takes its candidates from `store.events.refresh_candidates(status_classes=...)` with
  the settled classes only (completed, decided without play, void; `SETTLED_CLASSES`) and adds
  `store.events.stale()`, stale rows first, each group in id order. `missing()` is no longer used by the
  planners. The Python rule costs about three times the SQL it replaced, because it loads every slice row:
  31.8 ms against 9.4 ms for `event_needs` and 19.9 ms against 5.1 ms for `refresh_due_events` on a copy of
  the owner's data (1,051 events, warm cache), with equal results; a cold cache and larger data were not
  measured. `QueryService.detail_needs` and `refresh_due` forward to the planner since ST-27, so the
  equality tests of `tests/test_planning.py` now compare the planner with itself; the independent oracle is
  the file-based one of `tests/test_need_from_catalog.py`.
- The table has no stated order. As built the rules apply in this order: unknown event → `full`; known
  from a listing only → `full` when its status is finished, decided without play or unknown (a summary row
  without a status), `none` when it is not started, live or void (a newer listing that shows it finished
  makes it `full`); `stale` → `refresh` ("first" is rule precedence, and it holds also with
  `REFRESH_WINDOW_HOURS=0`); a stored record whose status is not settled (not started, live, unknown) →
  `none`; a void record → no slices awaited, `refresh` when due; a finished record → `refill`, then
  `refresh`; otherwise `none`. A stale record with missing slices is refreshed first and refilled on the
  next plan. `phase_of("void")` is `pre`, because the table puts void with not started.
- The row "not started or void, no pre-match slice selected" is not built: with every slice valid in every
  phase it would refill every not-started record on every run (its empty answers are not counted). Open
  records are left to the listings (stale, then refresh) and to the live service; pre-match slices can come
  back with P27 as an explicit selection (section 11).
- Phase order: listing items exist (P14) but the sync runs them before the details phase as separate runs
  (3.3); owner items are not built (P28). `MatchDataFetcher.refresh_due_ids` re-sorts the refresh list by
  legacy path, so "stale first" holds for the planner's list, not for that face. Its successor,
  `DetailPhase.refresh_due`, keeps that order (P30 changed no request order).

As built by P28 (PR #140) and FX-19 (PR #156) (`sofascore_scraper/services/planning.py` and `sofascore_scraper/services/follow_sync.py`
at `b6caf2f`):

- **Odds in the event rules.** `compute_need` returns `refill` for an event whose odds are due by the rule
  of 3.1 (`timed_slices_due`), also for a not-started event known only from a listing; the refill item then
  carries only the due `(key, provider)` pairs. The detail candidates are finished events, so the pre-match
  odds have their own list, `prematch_items(store, policy, tournament_ids=)`.
- **Owner items.** One `owner` work item per owner, not per event (`owner_items`, `:619-676`): the seasons
  of the sync's plan (a league given without seasons uses its newest stored season), the teams seen in
  those seasons' events (read only when a team slice is selected or a team is followed), the followed
  players, and the sports of those seasons (through the catalog's numeric sport id, because `Ref.sport`
  takes an int). A slice is due when it was never read, or when it is older than `max_age` and its owner is
  still active; a season is active while it has an open event or one that started in the last 7 days
  (`SEASON_ACTIVE_S`, `:558`), so a finished season's data is read once; teams, players and sports are
  always active. When nothing selects odds or non-match data (`extras_selected`) the step reads and requests
  nothing. The sync runs it after the details as one run (`run_extras`); when the Store cannot be opened it
  logs a warning and the job still completes. The design's "owner set derived from the followed seasons and
  the teams seen in their events" is this, with the plan's seasons and the player follows.
- **Team, player and match follows (FX-19).** A team follow reads `/team/{id}/events/next/0` (upcoming, one
  page) and `/team/{id}/events/last/{n}` backwards from `n = 0`; a player follow only
  `/player/{id}/events/last/{n}` (the catalog has no `next` page for players, so a player's upcoming matches
  are not read); an event follow is its match. The follow's `seasons` value is a window (`window_of`,
  `sofascore_scraper/services/follow_sync.py:104-115`): `current` the matches that started in the last 365 days,
  `last:N` the last N × 365 days, `all` up to `MAX_LAST_PAGES` = 5 pages back (`:76`), season ids only the
  matches of those seasons. Reading back stops at `hasNextPage: false` or a 404, at the page limit, or when a
  page's oldest match is older than the window; upcoming matches are not cut by the time window. The lists
  are not stored (the Store has no team or player schedule slice), so they are read again on every sync: at
  least 2 requests per team and 1 per player, and a team with `all` up to 6 list requests plus its matches.
  Since B2 (PR #191) a player follow's sync keeps the match ids of its last list as a runtime fact of the
  state database (`follow_events:player:<id>`, 2.7), for the counts of `/status` and `GET /events?follow=`;
  the list itself is still read again.
  The need is the planner's `compute_need` with three more rules (`follow_need`, `sofascore_scraper/services/follow_sync.py:228-242`): a match the list shows as
  ended that the catalog does not hold as ended is `full` (a team's list gives no `stale` mark); an unknown
  match that has not ended is read with its `/event` only (one request, no slices), so it becomes visible
  and is downloaded once a later list shows it ended; an event follow whose stored match should have started
  and has not ended is `full`. Lists run at the end of the match-list phase and the matches at the end of
  the details phase, on the job's counters, the shared budget, the job's breaker and its cancel. The page
  size, `next/0` for a team without fixtures, the player pages and `MAX_LAST_PAGES` wait for the live
  validation. `ssc watch` still skips player follows (`live_follow_skipped`).

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

As built (P13, PR #113; P14, PR #116; P15, PR #117; ST-27, PR #129; `sofascore_scraper/services/pipeline.py` at
`b3cb819`): one pipeline replaces the async and the sync detail paths, and every entry point runs it: league
and season downloads, matches picked by id, the single-match fetch, refills and `--refresh-only`.

- Signature: `FetchPipeline(store, *, client=None, concurrency=5, selection=None, threshold=2,
  writer_queue=16, source="job", listing=None)` (`:239-271`) with `run(items, *, cancelled=None,
  on_result=None)` and `run_sync`, which is `asyncio.run` in the caller's thread. It takes the Store and a
  cancel check, not the context and a job handle; the breaker and the cancel reach the requests through the
  request context, as before. A run's concurrency is `MAX_CONCURRENT` of the fetcher configuration. Every run
  opens one warmed client session (one warm-up GET) and a run with nothing to do opens none; the writer
  thread sits behind a queue of 16.
- Step 2: an empty `/event` (404, or a body without an event object) is a failed item with the reason
  `not_found`, and nothing is written; it is not an error mark on the event. A `/event` body of `null` is a
  failed request.
- Step 4: a refill asks for the slices its item names, which include missing optional slices; after the
  fresh `/event` the pipeline applies the sport and phase filter again.
- Since ST-27 the pipeline has no finished filter: an unfinished event that the planner hands over is stored
  with its `/event` payload and the slices that came back with a body, and its empty answers are not
  counted (since FX-27, PR #175, a body-less "no data" answer opens an uncounted `empty` row, 3.2;
  `sofascore_scraper/services/pipeline.py:448-454` at `43ecdfc`; a failed request is still not written).
  The `only_finished` keyword was accepted and ignored until P30 removed it, and `skipped/not_due` is no
  longer produced: the finished-only rule lives in the planner (3.2), and `fetch.only_finished` filters only
  what is read (and, through `detail_candidates`, which league matches get details; 2.1).
- `change.recorded` is appended to the `change` stream by downloads and refreshes since P13 (before, only
  by the live service).
- Listing items (P14): `planning.season_list_item(tid)` and `schedule_item(tid, sid)`. The pipeline hands
  every `listing` item to its `listing` handler, `ListingFetcher` of `sofascore_scraper/services/listing.py`, with the run's
  session and writer. The schedule strategy moved there from `match_fetcher.py`. A listing result is `ok`,
  `failed` (the request reason, `not_found`, `parse` or `storage`) or `skipped` (`breaker`, or `fresh`, 3.5);
  a failed listing makes the job `partial` and is listed in `SyncResult.failed_listings`. Follow-up `full` or
  `refill` items for finished matches that a schedule reveals exist only with `enqueue_events=True`
  (`ItemResult.follow_up`, appended to the same run). The sync does not use them: its details phase plans
  from the catalog right after the listings.
- Seams left: `SyncService` reaches the detail pipeline through the forwarders of `MatchDataFetcher` and the
  listings through `SeasonFetcher.list_seasons` and `MatchFetcher.list_schedule`; since P15 the three are
  lazy properties of the context, built on first use, and are not deleted. Each listing item of a sync is
  its own pipeline run, so a sync opens one session per season list and per schedule. One session per job
  needs the detail phase moved off `MatchDataFetcher` first. Owner items are not built (P28).
  Since P30 (#186) the seams are `DetailPhase` (the details) and `ListingService` (the listings), built per
  job (`sofascore_scraper/services/sync.py:355-420` at `216c2f9`); the fetchers are deleted. A sync still
  opens one session per listing and one pipeline per `DetailPhase.fetch` or `refresh` call (one per league
  batch), not one per job.
- The single-match fetch (`POST /api/matches/{id}/fetch`) runs the pipeline under its own circuit breaker
  and keeps the `writer_busy()` guard; it is not a job record and does not take the writer lease, because a
  job row for every click is a visible change the item did not name. The route went with the 2.x routes in
  P30 (#186); a single match is fetched by a `fetch` job with `event_ids` (or a match follow), under the
  writer lease.
- The list above holds as built, with two qualifications: the finished-only rule is the planner's since
  ST-27 (above), and an unfinished event is stored, not reported as `not_due`. Measured with the fake
  transport on 306 events (304 finished): the same 2,128 `/event` and slice requests and the same 2,438 files
  as the two old paths, three sessions instead of six.

### 3.4 Breaker, throttle, cancel

- Breaker: one per job, activated by `JobManager` through the request context (today `fetch_job.py:86-87`,
  `main.py:344`). When it trips, the pipeline stops, the job ends `partial` with `error.code` =
  `blocked` | `rate_limited` | `upstream_error` (from `CircuitBreaker.reason()`, `breaker.py:241-247`) and the
  CLI exits 4. `--ignore-breaker` maps to today's `IGNORE_RATE_LIMIT` (`breaker.py:107-108`).
  As built: the breaker is still installed by `SyncService` in its own request context (2.7), not by the job
  manager. Since P11 (PR #69) a job that the breaker stopped is stored as `partial` with that `error.code`
  (2.8). Since P19 (PR #119) a breaker stop exits 4 also through `main.py`, which translates its flags
  into `ssc` commands. The single-match fetch has a breaker of its own since P13 (3.3).
- Throttle: the shared budget of 2.4, `[client] rate`; `concurrency` only bounds in-flight requests. Since
  FX-6 a request that is cancelled while it waits for its slot gives the slot back, so a stopped job no
  longer delays the next one. The pipeline wraps every wait for a slot in
  `throttle.give_back_if_interrupted(delay)`, also around an `await`. A request that still waits for the
  request semaphore when the job stops was a separate defect: it could be sent after the stop, with the
  budget as the limit too. Since FX-9 (PR #83) it is not sent: the async request paths check the cancel
  right after the semaphore, before the breaker check and the reservation (2.4). What is left is inside the
  bridge (`_wait_for_slot` checks the cancel only while it sleeps; P24) and on the sync path, which has no
  check between the reservation and the send (2.4).
  As built (P24, PR #95; P13, PR #113): a sync caller of the bridge checks its own cancel flag every 0.25 s,
  so a job stopped while it waits for a shared challenge solve or for the page's `fetch()` stops waiting
  (`FetchCancelled`), and the shared solve goes on for the other waiters; a coroutine in the slot wait gets
  0.5 s to end itself, so the slot is still given back. Browser start-up (`ensure_ready`) stays
  uncancellable, the async bridge path (`_run_on_background_loop`) has no caller-side check, and the cancel
  check in `_wait_for_slot` before the reservation was not added (it would change a pinned count in
  `tests/test_throttle.py`). Downloads no longer use the sync request path (3.3), so its missing check
  between reservation and send concerns only the doctor, status and league-search callers.
  Since FX-18 (PR #139): the async bridge path has the caller-side check (`cancellable=True`, every 0.25 s,
  0.5 s of grace) and `_wait_for_slot` checks the cancel before the reservation, so the two "not built"
  points of the paragraph above are built; browser start-up stays uncancellable (2.4).
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

As built (P13, PR #113; P14, PR #116; ST-27, PR #129; `sofascore_scraper/services/listing.py:109-112` at `b3cb819`):

- Only round pages carry `complete`. Event pages carry `{filtered: true}`, which since ST-27 means "a page
  of the de-duplicated event list", not "finished matches only": they are stored with every match on them.
  The season list carries nothing, and the rounds list is not stored, so the freshness of a schedule is
  inferred from its pages. A round without matches is never stored (`SAVE_EMPTY_ROUNDS` is retired).
- Freshness: a season list is fresh for 6 h and a schedule for 15 min; a schedule counts as fresh when its
  newest stored page is younger than that and every page is complete or younger than that. Incomplete
  rounds keep their 6 h cache and complete rounds are never fetched again. The TTLs are passed per run
  (`season_max_age`, `schedule_max_age`), so a `--force` can pass `None` (P27). A season whose rounds are all
  complete still costs one rounds-list request per run once the 15 minutes have passed.
- The goldens: `job_idempotency` (picked complete matches run again: no request and no session) and
  `job_full_rerun` (the third run makes no request). With the fake transport, a full league run repeated at
  once made 4 requests instead of 23 (one warm-up and three detail requests of a refill), because no listing
  was requested again. Besides listings past their TTL and due provisional records, a record that a newer
  listing marked `stale` is read again (3.2).

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
| `ssc doctor` | environment check (`sofascore_scraper/doctor.py`) | `--strict`, `--live`, `--only`, `--skip` |
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
so it cannot drift from the implementation. Each command is one module under `sofascore_scraper/cli/commands/` that
registers itself, so a PR that adds a command adds a file and does not edit a shared one.

As built so far (P18, PR #65; `sofascore_scraper/cli/` at `f286723`). The entry point is `python -m sofascore_scraper.cli.main <command>`,
or `ssc <command>` after `pip install -e .`; only an editable install is supported, because the version, the
locale files and the web UI build are read from the project folder. Since REN-1 (#168) the module is
`sofascore_scraper.cli.main` (`ssc = "sofascore_scraper.cli.main:main"`). The wheel that `python -m build`
makes holds the 152 modules and the entry point, but not the Store's `.sql` files
(`store/schema/catalog.sql`, `store/migrations/state/*.sql`), the locales or `frontend/dist`, and
`sofascore_scraper/version.py` reads `pyproject.toml` next to the package: a non-editable install does not
work. The owner decided on 2026-10-08 that 3.0.0 is not published as a package: the release is the tag, the
GitHub Release (with the source archives) and the Docker image, and users install from source
(`03-implementation-plan.md` sections 13 and 18). `main.py` is untouched until P19. The
commands that exist are `version`, `doctor`, `describe`, `config show|validate|init|path`, `diagnostics`
and, since P22 (PR #73), `events`, and since P23 (PR #91), `watch`.

- A command registers itself with `@command("jobs list", help=<locale key>, configure=fn, settings=True)`
  (and `group("jobs", help=...)` for a parent) and returns a `CommandResult(data, text, exit_code, warnings,
  notes)`; it raises a `PlatformError` or lets `LeaseHeld`, `StorageError` and `ConfigError` through, which
  are mapped. `settings=True` loads the settings and moves the log lines to stderr before the command runs.
- `version` prints the application version and two schema versions, the CLI envelope's and the config
  file's. The Store does not export its layout, catalog and state versions from its root yet. The version
  of the data schema is not printed either: `schema.SCHEMA_VERSION` exists since SC-1 (PR #72) and is not
  wired, and `describe schemas` does not include `schema.describe()` (the JSON Schema of the normalized
  records). `sofascore_scraper/cli/commands/meta.py` was not owned by SC-1; P19 wires both.
- `doctor` reads `.env` and the environment, as `main.py --doctor` does, not the config file. `ssc doctor`
  passes the effective request rate (config file and `--rate` included) to a new check, `budget`, which
  warns when the budget is above the default or off. That check runs only in the new CLI
  (`doctor.EXTRA_CHECKS`), because the CLI goldens pin the check list of `main.py --doctor`. A broken config
  file shows as a warning, not as a failed check.
  As built since FX-23 (PR #171): the doctor's data folder (`Context.data_dir`,
  `sofascore_scraper/doctor.py:252-270` at `48e4c4c`) comes from the settings loader with the application's
  layers (process environment, then `storage.data_dir` of the config file, then `overrides.json`, then
  `DATA_DIR` of `.env`, then `data`), as `ssc status` reads it; the end-to-end test found the doctor checking
  `app/data` while the config file named another folder (finding F1). When the loader cannot be imported or
  the settings are invalid, it falls back to the environment and `.env`. Since P30 (#186) the layers are
  the loader's of 4.3 (the environment with `.env`, then the config file, then `overrides.json`), and the
  fallback reads `SOFASCORE_STORAGE__DATA_DIR` (`sofascore_scraper/doctor.py:228-250` at `216c2f9`).
- `describe slices` shows today's registry (`DetailSlice`: key, path, sports, `default_enabled`, and
  `required` as `counts_for_completeness`); `owner` is always `event`. The `SliceSpec` fields come with P12
  and P27. `describe` has no topic for the watch sources, because `tests/test_cli_describe.py` pins the
  topic list: P23 added the key `live_sources` to `describe config` instead (name, default, available,
  opt_in, description and, for `direct`, the warning; `describe_live_sources`,
  `sofascore_scraper/cli/commands/meta.py:217-232` at `9b03c64`). Only `poll` is available today; P24 and P31 update its
  `available` list together with `AVAILABLE_SOURCES` of the supervisor. The `--source` help text carries the
  four warnings of 8.3.
- `--version`, `version`, `--help` and `doctor` run with only the standard library. A command that needs a
  missing package ends with `internal` and a pointer to `doctor`.
- The `--help` description names the sports of the registry.
- `events` (P22, PR #73; `sofascore_scraper/cli/commands/events.py` at `e0bae0c`) reads the stream log with the options of
  the table: `--stream` (repeatable), `--after SEQ`, `--follow`, `--type` (repeatable; shell patterns such
  as `live.*`), `--event` (repeatable) and `--limit N`. It opens the Store read-only, takes no lease and
  writes no cursor and no event, so it can run next to a download or a watcher (opening a Store brings the
  catalog up to date since ST-11, PR #75, also for a read-only open). In text and `ndjson` mode it prints
  one envelope per line (5.1) and, when the stream ends by itself, the line
  `{"type":"end","stream_id":…,"last_seq":…,"count":…,"gap":…}`; `last_seq` is the value to pass as
  `--after` next time. `--json` prints one document whose `data` has `stream_id`, `last_seq`, `count`, `gap`
  and `events`; `--follow --json` is a usage error. `--follow` starts at "now" unless `--after` is given,
  and Ctrl+C ends it with exit code 0 and no `end` line. Events that were pruned before they were read are
  reported as `gap: true` and as the warning `stream_gap`. A data directory that is not a Store yet reads as
  empty and nothing is created; with `--follow` it is a storage error.
- Limits of `events` as built. It reads `store.streams` itself, because `LiveService.events` (2.7) does not
  exist. The output module has no path for streaming commands: the command writes its lines itself, and an
  error in the middle of a stream is printed as the error envelope, which has no `type` field (P19 gives the
  output module that path, for `jobs tail` and `watch --stdout` too). `ssc events | head -1` exits with 1 on
  the closed pipe, without a traceback: the CLI's general handling of a closed stdout, not changed by P22.
- `diagnostics` writes the bundle of `sofascore_scraper/diagnostics.py`. What the bundle takes from a job row is in 2.8
  (PR #71); how it shows a webhook address is in 4.3 (PR #73).
- `watch` (P23, PR #91; `sofascore_scraper/cli/commands/watch.py` at `9b03c64`) runs the live service in the foreground
  (section 8) with the options `--sport` (repeatable), `--event ID` and `--tournament ID` (repeatable; both
  need `--sport` and replace the follows), `--source page|direct|poll`, `--stdout` and `--hours H`. The
  table's `--for DURATION` is `--hours` as built. Without `--event` and `--tournament` it watches the follows
  with `live = true`, narrowed by `--sport`; nothing to watch is a usage error. A second live service, or a
  `--watch` beside it, makes it exit 6. `page` and `direct` fall back to polling with the warning
  `live_source_unavailable`, and `--source direct` adds the warning `live_direct_source`. `--stdout` prints
  the new events as envelope lines (5.1) and the summary goes to stderr; it is a usage error with `--json`.
  SIGTERM stops it like Ctrl+C. The command hosts the sink dispatcher (8.4).
  As built since FX-27 (PR #175; finding V6 of the live validation): `--sport` alone narrows the follows,
  and the narrowing holds for the whole run. Before, the command filtered only the scope it built, while the
  service read the follows again at its start and every 60 s and rebuilt the full scope, so
  `ssc watch --sport football --sport tennis --sport basketball` watched all 13 followed sports and the `page`
  source opened 13 browser pages. The command now hands its `--sport` list to `scope_from_follows(follows,
  sports=)`, which keeps it in `LiveScope.only_sports`, and the service's re-read passes it on
  (`sofascore_scraper/services/live/supervisor.py:225-278`, `:454-457` at `43ecdfc`;
  `sofascore_scraper/cli/commands/watch.py:94-102`). `ssc watch --help` has a description (`ssc_desc_watch`)
  that says which score changes are events: for a set sport a won set, not a game or a point (5.1).
  As built since the release pull request (#179): `--idle` keeps the command running when there is nothing
  to watch. Without it nothing to watch is still a usage error (exit 2, `invalid_request`); with it the
  command logs one English line and the JSON warning `live_nothing_to_watch` and runs the service with the
  empty scope read from the follows, which it reads again every 60 s (`SCOPE_RELOAD_SECONDS`), so it starts
  watching once a follow is marked live. Nothing is sent while it is idle, and Ctrl+C and SIGTERM stop it as
  before. The Compose service and `docs/deploy/sofascore-watch.service` run `watch --idle`, so the live
  container no longer restarts in a loop when no follow is live (4.6).

As built at `b3cb819`: every command of the table exists, from P19 (#119: `sync`, `fetch`, `refresh`,
`export`, `status`, `jobs`, `follows`, `data`), ST-23 (#110: `migrate`, `catalog`), ST-24 (#109: `backup`),
P25 (#125: `serve`), P29 (#128: `serve --scheduler`) and SC-2 (#130: the datasets of `export`). `ssc --help`
names `ssc serve` for the web app since P26 (#131). Where the options differ from the table:

- `sync`: `--tournament ID`, `--only events`, `--recheck-unavailable legacy|all`, `--include-legacy` and
  `--dry-run`. Not built: `--follow NAME…`, `--only listing,non-match,refresh`, `--slices`, `--force` and
  `--limit N`. The download still reads the leagues from the configuration (`ConfigManager.get_leagues()`),
  not from the follows table (FX-13). There is no command that fetches season lists alone.
  At `b6caf2f` (`sofascore_scraper/cli/commands/sync.py:88-90`): the sync reads the follows table (FX-13; 2.7);
  `--only seasons` reads the season lists alone, without the freshness limit (FX-13), next to `--only events`;
  `--follow KIND:ID` (repeatable; `tournament`, `team`, `player`, `event`) syncs named follows, not with
  `--tournament` or `--only events` (exit 2), and `--dry-run` counts the team, player and event follows
  (FX-19). Still not built: `--only listing,non-match,refresh`, `--slices`, `--force` and `--limit N`. `fetch
  tournament --only` keeps `events` only.
- `fetch tournament ID`: `--season ID` (repeatable; without it every season), `--only events` and
  `--dry-run`; `--season current|last:N` is not built. `fetch event ID…`: `--dry-run` only; no `--sport`,
  `--slices` or `--force`.
- `refresh`: `--tournament ID`, `--include-legacy`, `--dry-run`.
- `export`: `--dataset events|slices|changes`, `--profile legacy-wide-csv`, `--schema normalized|raw`,
  `--format csv|jsonl|tree|parquet|sqlite` (no `json`: JSONL covers it), `--out PATH|-`, the filters
  `--sport`, `--tournament`, `--season`, `--event`, `--status`, `--from` and `--to`, and `--force`.
  The mode follows the options: `--schema raw` is the raw export (`--dataset events|slices`; without it every
  payload), `--dataset` or `--schema normalized` a dataset (default `events` as JSONL), and anything else the
  2.x wide CSV, which knows only `--tournament` and `--event`. Without `--out` the wide CSV keeps its old
  place, `match_details/processed/`, and a dataset goes to `DATA_DIR/exports/`; since FX-34 both are named
  like an export job's file with the UTC time in place of the job id (`events-wide_2026-10-06_142530.csv`,
  `events_2026-10-06_142530.jsonl`; 2.7), no longer `all_matches_<epoch>.csv` and `<dataset>_<epoch>`. The
  dataset files were not listed by `GET /api/v1/exports`, which lists jobs; since FX-19 it lists them too,
  with `source: "file"`. Since P28 `--dataset` also takes
  `odds` and `standings` (normalized only). Since B1 (#190) `--team ID` and `--player ID` (repeatable, like
  `--tournament` and `--event`) filter by participant, for every mode including the wide CSV (2.7).
  `--out -` streams JSONL or CSV; Parquet and SQLite on stdout are refused. `--force` replaces
  an existing target. An empty selection of the wide CSV is `not_found` (exit 1). The raw export writes the
  events oldest first since SC-2 (newest first before). `--json` has `schema_version` (null for raw and the
  wide CSV).
- `status`: `--check` prints nothing and exits 1 when the catalog must be rebuilt, 0 otherwise; without it
  `status` exits 0. It shows the data summary, the Store's versions, the leases, the running and the last
  job, the live service (`live_status`) and each sink's cursor and last error (`store.streams.cursors()`).
  `--coverage` adds the matches, details and coverage per tournament. A folder that is not a Store is an
  empty, healthy status and nothing is created. Not built: the last migration (`migration_runs`), the
  stored bridge health, the scheduler (always off from another process) and the lag per sink.
  Since FX-13 the JSON has `last_migration` (`Migrator.last_run`, the newest real run) and `lag_events` per
  sink cursor, with a text line each, and `--disk` adds the disk use with `v3` and `changes` (the walk is
  slow on a large folder, so it runs only on request). The stored bridge health and the scheduler are still
  not shown.
- `jobs list` has `--limit`, `--kind` and `--state`; `jobs tail ID` has `--after` and `--follow` and ends
  with an `end` line.
- `follows add KIND ID` with `--name`, `--sport`, `--seasons`, `--live`, `--disabled`; `follows list
  --kind`; `follows remove KIND ID` (exit 0 with a note when nothing was followed); `follows export`. The
  rows are written with origin `api` (2.7). Since FX-13 the commands go through `FollowsService`, so
  removing a `leagues.txt` follow removes it from the file. No command moves a `leagues.txt` follow into the
  follows table (only `PATCH /follows/{id}` with `origin: "api"`; 2.7).
- `backup create [--scope all|state|data] [--include-secrets]` (the four 2.x scope names `config`, `seasons`,
  `matches` and `match_details` were deprecated in 3.0.0 and removed by P30, #186; archives made with them
  still restore),
  `backup list`, `backup verify NAME` (exit 1 when it finds problems) and `backup restore NAME [--force]
  [--dry-run] [--yes]`. A backup is named, not given as a path: only names inside the data folder's
  `backups/` are accepted. `restore` without `--yes` (and without `--dry-run`) is `confirmation_required`.
  `create` holds `writer` (`op:backup`) for every scope (`config` was the one without it); the config files
  it packs, for `all` and `state`, are `leagues.txt`, `league_sports.json`, the active `sofascore.toml` and
  `config/overrides.json`. There is no `backup prune`.
- `data clear` takes `--scope events|schedules|seasons|all` (the Store's names) or `--all`, and `--yes`;
  `data recheck-unavailable` takes `--all` and `--tournament ID` and holds `writer` (purpose
  `recheck-unavailable`); it is not a job.
- `migrate`: as in the table. It copies `config/leagues.txt` into the follows first, because season lists
  named only after a league resolve through the follows. A dry run on a folder that is not a Store yet
  creates `.meta/` (the command opens the Store); the data folders are untouched. It exits 3 when some items
  could not be converted and 6 while a download, `ssc watch` or `--watch` runs. Details: `01-storage.md` 5.4.
  `migrate` and the catalog commands keep the Store they open and do not close it (harmless in a process
  that ends; in-process tests close it in their fixture).
- `catalog rebuild [--mode auto|in_place|recreate]`, `catalog verify [--deep] [--repair]` and `catalog
  reconcile [--deep]` (new); each exits 1 when the result is not clean. `catalog reconcile` always looks at
  every match folder, whatever the limit of decision S17. They replace `scripts/catalog_tool.py`, which is
  deleted with `scripts/migrate_match_details.py`; the script's `stats` command and its mode for a folder
  that is not a Store have no replacement (`Store.info()` and `ssc status` cover the counts).
- `serve`: `--host`, `--port`, `--allowed-hosts`, `--allow-any-host`, `--dev`, `--scheduler`,
  `--no-scheduler`; no `--token` (a command line is visible in the process list; the token comes from the
  Settings). Details in 4.6.
- `version` prints the application version, the CLI envelope and config schema versions, the data schema
  version and the Store's layout, catalog and state versions; `describe schemas` includes the JSON Schema
  of the normalized records (P19), with the five P28 models since FX-21 (#164; `Odds`, `OddsLine` and
  `StandingsRow` among `data.records`).
- `doctor` has a `config` check since FX-15 (`check_config`, `sofascore_scraper/doctor.py:597-634` at `b6caf2f`): it finds the file the
  app would read (`--config`, `SOFASCORE_CONFIG`, `./sofascore.toml`, `CONFIG_DIR/sofascore.toml`), loads
  it with the loader inside the check (the doctor stays stdlib-only at import) and probes the
  `storage.data_dir` and `log.dir` it names. Codes: `config_ok`, `config_none`, `config_invalid` (a broken
  file, with the loader's message), `config_dir_not_writable` and `config_unchecked` (a warning when the
  packages the loader needs are missing).
- `config init --from-legacy` reads `leagues.txt` itself (`config_manager.read_league_file`) since FX-15; it
  no longer builds `ConfigManager`, so it creates no `config/leagues.txt` and mirrors nothing into
  `state.db` (`sofascore_scraper/cli/commands/meta.py:612-634`).
- `describe slices` lists the whole registry with each slice's `group`, `owner`, `phases`, `keep_history`,
  `max_age_seconds` and `selected_in` (the sports whose configured defaults select it; P27, P28). `describe
  config` lists `schedule_runs` (FX-13).

### 4.2 Global flags

`--config PATH`, `--data-dir PATH`, `--json` (same as `--output json`), `--output text|json|ndjson`,
`--quiet`, `--verbose`, `--log-level`, `--log-format text|json`, `--no-color`, `--lang en|tr`,
`--rate N|off`, `--ignore-breaker`, `--wait SECONDS` (wait for a lease instead of exiting 6),
`--progress none|text|ndjson` (stderr), `--version`.

As built (P18): all of these except `--log-format`, `--wait` and `--progress`, which nothing could honour
yet (`[log] format` has no consumer, and no command of P18 takes a lease or runs a job); P19 adds them. The
flags are accepted before or after the command. `main.py` has no `--wait` either: a refused lease fails at
once. The job manager can wait since P11 (`submit(wait_for_lease=)`, 2.8); no face passes a wait yet.
P19 (#119) added the three: every flag of the list exists at `b3cb819`. `--log-format json` writes one
object per line on stderr (`time`, `level`, `logger`, `pid`, `message`, `exc`; redacted); the log file stays
text, because `sofascore_scraper/diagnostics.py` parses it. `--wait SECONDS` is passed to the job manager by `sync`,
`fetch` and `refresh` and to the lease of `data recheck-unavailable`. `--progress text|ndjson` writes the
job's events to stderr. Through `main.py` the old flags reach the same commands, and since P19 `--data-dir`
and `--config` go to the loader as flags there too.

### 4.3 Configuration file

One declarative file, `sofascore.toml` (decision D3), found in this order: `--config`, `SOFASCORE_CONFIG`,
`./sofascore.toml`, `CONFIG_DIR/sofascore.toml`. The application never writes it. A file that is named
explicitly and does not exist is an error. `SOFASCORE_CONFIG=none` turns the search off, so that no file is
read; the test suite sets it, and it helps when a stray file is suspected.

This section describes the model and the loader as built by P09 (PR #56; `sofascore_scraper/config/settings.py`,
`loader.py` and `schema.py` at `f286723`). The file is honoured since that pull request: it is read when
`sofascore_scraper.config_manager` is imported, once per process. `main.py` does not hand its `--config` to the loader yet
(it is still the dead leagues-file flag of 1.7); an explicit file reaches the loader through
`loader.activate(config_file=..., flags=...)`, which the new CLI calls since P18. Since P19 (#119)
`main.py` translates its flags to the new CLI, so its `--config` names the config file as well. TOML is read with
`tomllib`, on Python 3.10 with the `tomli` backport, which is imported only when a file is read.

Both entry points change to the project folder at start, so `./sofascore.toml` is the file in the project
folder, not in the directory the command was run in; `.env`, `config/` and `data/` are the same for both.
The new CLI resolves a relative `--config`, `--data-dir` and `--out` against the directory the command was
run in. `loader.load_settings()` sees only the `.env` values that are already in the process environment (it
expects python-dotenv to have loaded the file); commands that must not touch the process (`config validate`,
`doctor`, `config init`) build that view themselves (`read_settings()` in `sofascore_scraper/cli/commands`), and any later
caller of `load_settings` in a fresh process needs the same. The application loads `.env` at its start
(`sofascore_scraper/config_manager.py:32`, `sofascore_scraper/web/app.py:20`, `sofascore_scraper/logger.py:357`;
`dotenv.load_dotenv` does not replace a variable that is already set), and the loader reads the file once
more only to say where a 2.x name was set (below).

The sample shows the scalar keys with their defaults; `use_proxy` and `proxy_env`, which it names only in a
comment, are described below:

```toml
schema = 1

[storage]
data_dir = "data"
durability = "normal"     # normal | full: "full" fsyncs every file and directory the Store writes
open_reconcile_seconds = 60   # an open skips the scan of the old folders when the last one is younger

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
odds_country = ""         # recorded with every odds read when set (e.g. "TR"); never derived from the machine (FX-15)
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
confirm_empty_after_seconds = 60   # a finished match's "no data" answer is confirmed no sooner (3.2)

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
token_env = ""            # empty = no other variable: SOFASCORE_SERVER__TOKEN holds the token

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

**Layers**, weakest to strongest (`loader.LAYERS`, `sofascore_scraper/config/loader.py:73-81` at `216c2f9`):

| Layer | What it is |
|---|---|
| `default` | the defaults in the code |
| `overrides` | `CONFIG_DIR/overrides.json`, machine-written: by `PATCH /api/v1/settings` through `sofascore_scraper/config/overrides.py` (below), which the Settings page of the web UI uses. The shape of the config file without `[[follow]]`, `[[sink]]` and `[[schedule.task]]` (decision D11) |
| `file` | `sofascore.toml` |
| `env` | the process environment, `SOFASCORE_<SECTION>__<KEY>` and the JSON lists, with the lines of `.env` loaded into it |
| `flag` | command-line flags, handed over by the caller as `flags={"storage.data_dir": ...}` |

A value pinned by `file`, `env` or `flag` is shown as locked in the web UI (decision D4; `Source.locked`).
`config show` prints each value with its layer and the name of its source (`loader.active().describe()`,
secrets masked).

As built since P30 (#186). `.env` is not a layer of its own: the application loads it into the process
environment at its start without replacing a variable that is set, so a `SOFASCORE_*__*` line of `.env` is
part of the environment layer, ranks above the config file and pins the setting (it is locked on the
Settings page, with the variable as `source_name`; the source enum of `GET /api/v1/settings` is `default`,
`overrides`, `file`, `env`, `flag`). `loader.reload()` re-reads `overrides.json` and the config file, not
`.env`: a changed `.env` line takes effect when the process restarts. The Settings page writes
`config/overrides.json` only; nothing in the application writes `.env` any more (`ConfigManager.update_env_variable`
is gone). An empty `SOFASCORE_*__*` line counts as not given, so `SOFASCORE_CLIENT__RATE=` in `.env` leaves
the default.

Until P30 the loader had a `dotenv` layer below the overrides file: a 2.x name (`DATA_DIR`,
`MAX_CONCURRENT`, …) whose value this process had applied from `.env` ranked below the overrides file and
the config file, so that an installer-made `.env` did not beat a config file, and `loader.note_dotenv_write`
kept a value saved on the classic Settings page in force. That was point (a) of decision D19
(`03-implementation-plan.md` section 13), settled on 2026-10-02 as built by P09 together with its other
three points (a proxy given in the config file switches the proxy on; the legacy-name warning only with a
config file; an empty `token_env` means "no other variable"). P30 removed the layer with the 2.x names it
existed for; points (b) and (d) stand, and (c) is replaced by the `legacy_name` warning below, which is
given with or without a config file.

**The overrides writer** (P20, PR #74; `sofascore_scraper/config/overrides.py` at `e0bae0c`).
`write_overrides({"client.rate": 3, "display.language": None})` is the one writer of
`CONFIG_DIR/overrides.json`, and `PATCH /api/v1/settings` is its one caller. A value of None removes the
key, so that the weaker layer's value is in force again. Every value is checked with the loader's own rule
before it is written; the file is written atomically under a lock file (`overrides.json.lock`) with mode
0600, because a proxy address can carry a password; then the settings are reloaded. Since FX-25 (PR #172)
`config_files.file_lock` (`sofascore_scraper/config_files.py:81-118` at `48e4c4c`) removes `<path>.lock`
while it still holds the lock, so no `overrides.json.lock` stays next to the file; a process that waited on
the removed file checks after `flock` that the path still names the file it locked (same device and inode)
and otherwise opens it again, so two processes never hold the lock on two different files. This covers
every user of `file_lock`: `overrides.json`, `leagues.txt`, `league_sports.json` and the log rotation. The
lock file stays next to its file, not in a temporary folder, because the CLI and `ssc serve` can run with
different `TMPDIR`s (systemd `PrivateTmp`). A process from before the change that waits on the same lock
file while a new one removes it can overlap with it once, during an upgrade only. If the reload fails, the
file is put back and the previous settings stay in force. The writer does not look at locks: refusing a
value that a stronger layer pins is the caller's job.

The route (`sofascore_scraper/web/api/v1/settings.py`) applies a request as a whole or not at all. A key whose value comes
from `sofascore.toml`, the process environment or a flag is refused with 400 `invalid_request` and
`details.locked` (key, layer and source name); removing such a key with `null` is allowed. A key the API may
not change is refused with `details.read_only`. The keys it may change are the table `WRITABLE` of that
module, the settings of today's Settings page: `display.language`, `display.use_color`,
`display.date_format`, `client.base_url` (https on SofaScore's hosts only), `client.use_proxy`,
`client.proxy`, `client.max_concurrent`, `client.rate`, `client.wait_time_min`, `client.wait_time_max`,
`client.timeout_seconds`, `client.retries`, `breaker.rate_limit_consecutive`, `breaker.rate_limit_ratio`,
`breaker.server_error_consecutive`, `fetch.only_finished`, `defaults.slices` (P27, with the `slices.<sport>`
differences), `refresh.window_hours`, `log.level`, `log.debug` and `storage.data_dir`
(`sofascore_scraper/web/api/v1/settings.py:140-164` at `216c2f9`; `fetch.save_empty_rounds` left it with
P30). The table adds upper bounds to the loader's rules. `storage.data_dir` must stay inside the project or the home directory, cannot be removed
with `null`, and a change of it is refused while a job runs; the job store is opened in the new directory
first, and if that fails nothing is written. Everything else (the bind address and the Host list, the token,
directory paths, the live source, follows, sinks) is read-only through the API.

**Shadowing between the two settings routes** (found by P20, pinned, not fixed; history since P30 removed
the legacy route, below). `overrides.json` ranks
above `.env`. Once `PATCH /api/v1/settings` has written a key, the legacy `POST /api/settings` (the current
Settings page) still writes that key to `.env`, reports success, and the value has no effect until the key
is removed through v1 with `null`. It takes someone who uses both routes; the web UI uses only the legacy
one. `tests/test_api_v1_settings.py::test_a_value_written_here_shadows_a_later_save_of_the_legacy_route`
pins it. It ends when P21's legacy adapter writes through `sofascore_scraper.config.overrides`.
It ended with P21 (#127). `POST /api/settings` still writes `.env` and then removes the saved keys from
`overrides.json` through `write_overrides`, so the saved value takes effect; keys it did not save stay, and
a file that does not hold the key is not rewritten. The test was
`test_a_later_save_of_the_legacy_route_replaces_a_value_written_here`. P30 (#186) removed
`POST /api/settings`; `PATCH /api/v1/settings` is the one writer of settings, and `fetch.save_empty_rounds`
left its `WRITABLE` table (a retired setting, below).

**Environment.** `SOFASCORE_<SECTION>__<KEY>`, e.g. `SOFASCORE_CLIENT__RATE=5`, `SOFASCORE_SERVER__PORT=9000`,
`SOFASCORE_STORAGE__DATA_DIR=/data`. Lists are JSON: `SOFASCORE_FOLLOWS`, `SOFASCORE_SINKS`,
`SOFASCORE_SLICES` and `SOFASCORE_SCHEDULE__TASKS`. An unknown `SOFASCORE_*__*` variable is an error. An
empty value in such a variable counts as not set (an empty secret too): it leaves the weaker layer's value.

**Legacy names.** Until 3.0.0 the loader read the 2.x names (`DATA_DIR`, `REQUEST_RATE_LIMIT`,
`MAX_CONCURRENT`, `APP_LANGUAGE`, `USE_PROXY`, `PROXY_URL`, the breaker and bridge thresholds, `REFRESH_*`,
`SOFASCORE_ALLOWED_HOSTS`, `SOFASCORE_BROWSER_PROFILE`, …; the table `loader.LEGACY`, 38 names, and two
language names), each parsed with the rule of the 2.x code that read it, below the new names, and logged a
deprecation warning only when a config file was present. As built since P30 (#186;
`sofascore_scraper/config/loader.py:276-391` at `216c2f9`) they are not read. `LEGACY_NAMES` (40 names) maps
each to its setting key, or to None for a removed setting (`SAVE_EMPTY_ROUNDS`), only to tell the user:

- Every 2.x name that is set, non-empty, in the environment or in `.env` gives a `legacy_name` warning, with
  or without a config file: `DATA_DIR (set in .env) is no longer read since 3.1; use
  SOFASCORE_STORAGE__DATA_DIR (or storage.data_dir in the config file).` The place is `.env` when the name is
  set there and the environment has no other value, else "the environment". It is logged at the first load
  and is in `ssc config show` (also in the `warnings` of `--json`), `ssc config validate`, the doctor's
  env check (WARN, below) and the diagnostics bundle (`legacy_names`).
- A variable named by `proxy_env` or `token_env` is read, whatever its name, and gives no warning (`ssc
  config init --from-legacy` writes `proxy_env = "PROXY_URL"`).
- `STORE_OPEN_RECONCILE_SECONDS` is the setting `storage.open_reconcile_seconds` (decision S17, ST-19), and
  the GNU `LANGUAGE` variable is no longer a language setting: only `display.language`
  (`SOFASCORE_DISPLAY__LANGUAGE`) is, then the system locale. The installers and start scripts follow the
  same rule since FX-33 (#188).
- A retired setting, `fetch.save_empty_rounds` (`RETIRED_SETTINGS`), is a `retired_setting` warning, not an
  error, wherever it is given (the 3.0 Settings page may have written it into `overrides.json`); a round
  without a match is never stored.
- An invalid value under a new name, in the environment or in `.env`, is a `ConfigError` (`config_invalid`):
  the application does not start, and `config validate` and the doctor's `config` check report it. 3.0
  ignored a bad legacy value with a warning. `bridge_health.thresholds()` and the logger, which run on
  error paths, fall back to the defaults instead.
- `ssc config init --from-legacy` writes the TOML equivalent of an old `.env`, with `proxy_env` and
  `token_env` pointing at the secret variables.

The doctor's env check (`check_env`, `sofascore_scraper/doctor.py:935`) is labelled "Settings
(environment and .env)" since FX-33 (#188). It reports an unparsable `.env` line (FAIL), a 2.x name (WARN), a
base URL that is not SofaScore's, `SOFASCORE_CHROME_PROFILE` and a headed browser without a display; values
are the `config` check's. Every problem carries its `origin`, `env_file` or `environment` (by the loader's
rule above), the message says where the name is set, and the fix line points at `.env` only for what is
there; a problem found only in the environment says to remove or rename it where the app's environment is
set (shell profile, service file, container settings).

Two settings had two readers with different rules. `API_BASE_URL`: `ConfigManager.get_api_base_url`
returned it as written (an empty value stayed empty, pinned by G-04), while the transport turns an empty
value into the default and strips a trailing slash; the model has `client.base_url` for the first and
`client.effective_base_url` for the second, and both stay. `DEBUG` had the logger's rule and that of the
legacy settings route; since P30 there is one rule, `log.debug` with the loader's booleans.

**Paths.** A relative path in the config file, in `overrides.json` or in a `SOFASCORE_*__*` variable is
resolved against the config file's directory. Flags are left as written (the CLI makes them absolute first).

**Secrets by name.** `token_env` and `proxy_env` give the name of an environment variable. An empty
`token_env` means "no other variable": `SOFASCORE_SERVER__TOKEN` holds the token. A named variable that is
unset or empty is a `ConfigError`, for both keys, so protection cannot be switched off by a typo. Giving
`proxy` and `proxy_env` in the same layer is an error; `proxy_env` from the same or a stronger layer replaces
`proxy`. The token itself is `settings.server.token`; it comes only from the environment and cannot be
written in the file or on the Settings page.

Since P20 (PR #74) `[server] token_env` takes effect for the web app; until P30 `sofascore_scraper/web/app.py`
read the token through Settings and handed it to `security._startup_token`. Since P30
`sofascore_scraper/web/security.py` reads the token and the allowed hosts from the active settings itself
(`api_token`, `token_variable`, `sofascore_scraper/web/security.py:128-143` at `216c2f9`), the hand-over is gone,
and the warning for a token that is too short names the configured variable. A `token_env` that names an
unset variable stops the start of the web app.

**Proxy.** In the config file, the environment and the flags, giving `proxy` or `proxy_env` switches the
proxy on, unless `use_proxy = false` is set in the same or a stronger layer (2.x used a proxy only with
`USE_PROXY=true`). The first version said nothing either way.

**Lists.** `[slices.<sport>]` merges sport by sport across layers; `[[follow]]`, `[[sink]]` and
`[[schedule.task]]` are replaced as a whole by the strongest layer that gives them. Sport names are checked
against the registry (`sofascore_scraper.sports.sport_slugs()`), so a config that names a sport that is not registered
yet is rejected. Slice names and the `run` names of schedule tasks are not checked against a registry yet
(P12, P27, P29). P29 (#128) checks the tasks when `serve` starts the scheduler (`check_tasks`: run name,
options and their values; `config_invalid`, exit 2); the loader and `ssc config validate` still do not.

- A `[[follow]]` names exactly one of `tournament`, `team`, `player`, `event`, and optionally `name`,
  `sport`, `seasons`, `slices`, `live`, `enabled`. It becomes a `FollowSpec` with the fields of
  `01-storage.md` 2.3; the class is defined in `sofascore_scraper/config/settings.py` as well, because the Store may not
  import `sofascore_scraper.config`. A follow without a name is named `<kind>-<id>`; without `seasons` it takes
  `[defaults] seasons`. An entity or a name may appear once. `slices` is None (the default selection),
  `{"include": [...]}` for a plain list in the file, or `{"enable": [...], "disable": [...]}` for a table.
- A `[[sink]]` becomes a `SinkSpec`. `secret_env` is a name; the loader does not read its value. Keys the
  model does not know go to `SinkSpec.options` for the sink's own code. The path of a file sink is made
  absolute. A webhook without `secret_env` and without `allow_unsigned = true` is rejected at load
  (decision D12).
  The option keys are defined and checked by `sofascore_scraper/sinks` since P22 (PR #73; `TYPE_OPTIONS` and `build_sink`
  in `sofascore_scraper/sinks/__init__.py` at `e0bae0c`):

  | Sink type | Option keys | Default |
  |---|---|---|
  | every type | `sports = ["football"]`, `tournaments = [17]`: only events of these sports or tournaments | no filter |
  | `file` | `rotate_daily` (rotate when the UTC day changes), `rotate_size` (`"50MB"` or a number of bytes, at least 1024), `keep` (rotated files to keep) | no rotation; every rotated file is kept |
  | `webhook` | `batch_size` (1 to 100), `flush_seconds` (0 to 60), `max_age` (`"24h"` or seconds), `timeout_seconds` (above 0, at most 120) | 20, 1 s, 24 h, 10 s |
  | `stdout` | none | |

  They are checked when a sink is built, not when the settings are loaded: an unknown key or a value out of
  range is a `ConfigError` (`config_invalid`) of `sinks.build_sinks`, which nothing called outside the
  tests until `ssc watch` (P23, PR #91) built the sinks at its start; a bad option makes `watch` exit 2.
  `config validate` does not build sinks, so it accepts a wrong option today; P19 makes it call
  `build_sinks`. P19 (#119) did: `config validate` builds the sinks without opening them
  (`_check_sinks`, `sofascore_scraper/cli/commands/meta.py:358-376`), reading a secret from the environment or `.env`, so
  an unknown option, a missing secret or a file sink inside the data folder is reported there. Three more
  rules of `build_sink`: the secret is read from the environment when the sink is
  built, and a named variable that is empty is an error; the name in `secret_env` must look like a secret
  (contain SECRET, TOKEN, KEY, PASSWORD, ...), because the diagnostics bundle and the log masking recognise
  values by the name of their variable; and a file sink whose path is inside the data directory is refused,
  because only the Store touches that directory. Sink names must be unique.
  Where a sink's address is shown. `ssc config show` and the diagnostics bundle show a webhook address by
  its host only (`https://hooks.example.org/***`, `redact.mask_webhook_url`; P22, PR #73): the path and the
  query of such an address are the credential for services such as Slack or Discord. The previous revision
  said this covers `[[sink]]` in the config file and the `url` fields inside `SOFASCORE_SINKS` for both. As
  built, `config show` covers both, and the bundle has no `[[sink]]` at all: it prints the environment
  variable `SOFASCORE_SINKS` through `redact.mask_value`, which masks its `url` fields. The previous revision
  left two places to FX-10, and one of them was described wrongly: a `SOFASCORE_SINKS` value that is a JSON
  list of strings was never quoted (`_table_list` answers `expected a list of tables` and has not quoted the
  value since P09); what did quote the rejected value were the errors of one sink row in `parse_sinks`. As
  built by FX-10 (PR #84; `sofascore_scraper/config/loader.py` at `9b03c64`):
  - The errors of a sink row (`[[sink]]` or an item of `SOFASCORE_SINKS`) name the type of a rejected value,
    never the value: `url: expected a string, got a list`, `type: expected one of stdout, file, webhook`,
    `events: expected a list of strings, got a whole number at #1`. A `secret_env` that is not a variable
    name (the secret itself written there) gets a message worded so that the CLI's output redaction leaves
    it whole. A list of strings in place of tables names the type and the position (`expected a list of
    tables, got a string at #1`), also for `SOFASCORE_FOLLOWS` and `SOFASCORE_SCHEDULE__TASKS`. The errors of
    `[[follow]]` and `[[schedule.task]]` rows still quote the value (they hold no address), and the
    duplicate-name error quotes the sink's name, a label.
  - `config show` masks a sink's options by the sink's type: the value of a key defined for that type
    (`sinks.COMMON_OPTIONS` plus `sinks.TYPE_OPTIONS[type]`) is shown, any other key is shown with `***` as
    its value, so the user still sees which key is wrong; a key of another type (`rotate_size` on a webhook)
    counts as unknown (`loader.mask_sink_options`, `:845-856`). The bundle applies the same rule to each
    table of `SOFASCORE_SINKS` (`loader.mask_sink_table`, `diagnostics._sink_list`) before `mask_value`
    masks the address. `SINK_KEYS` (`:706`) is the list of the modelled keys of a sink table.
- A `[[schedule.task]]` becomes a `ScheduleTask` with `run`, `every`, `every_seconds`, `cron` and `options`;
  exactly one of `every` and `cron` is required.

**The environment bridge** (P09 to P30). Many modules read their setting from `os.environ` themselves (the
throttle, the refresh policy, the logger, the bridge health, the breaker, the web security module, the
watcher, the paths module, `sofascore_scraper/store/files.py`, the transport). So that they honoured the
config file too, the loader wrote the effective value of every setting back into `os.environ` under its
legacy name (`loader.projection`). P30 (#186) removed it with the legacy table and the dotenv layer: the
throttle, the refresh policy, the bridge health, the breaker, the logger, the paths module, the transport,
the bridge, the web security module and `sofascore_scraper/store/files.py` read the active settings, and the
logger follows every rebuild of the settings (`logger.follow_settings`). `store/files.py` does not get its
durability from its caller, as the plan said: the Store may not import the configuration, so it reads
`storage.durability`, `storage.open_reconcile_seconds` and `storage.data_dir` from the loader module through
`sys.modules` and uses the defaults when no loader is loaded (`01-storage.md`).

**What stops the start.** A broken config file, an unknown `SOFASCORE_*__*` variable, an invalid value under
a new name or an invalid `overrides.json` raises `ConfigError` (`config_invalid`); the CLI renders it as exit
code 2 (since P18), with the file or the variable and the key in the message. `loader.reload()` re-reads the
overrides file and the config file (not `.env`); if one of them is invalid the previous settings stay in
force.

**Warnings.** `loader.active().warnings` are `ConfigWarning(code, message)`, logged at the first load:
`legacy_name` and `retired_setting` (above) and `live_direct_source`, the four points of 8.3, whenever
`[live] source` is `direct`. With a config file the log also has `Config file loaded: <path>`.

**Cost.** The environment can change at any time (tests and the Settings page rely on that), so every
`ConfigManager` getter compares it: a getter call costs about 40 µs instead of about 0.5 µs. The call sites
are per request and per web request, not per file.

**What uses the model today.** `ConfigManager`'s getters, and every module that read a legacy name (through
the bridge until P30, directly since). The `[[follow]]` entries are applied to the follows table since
ST-17, and nothing reads the table yet. Modelled and validated but not used by anything: schedule tasks (P29), the slice
selection (P27), `[server] host` and `port` (P25), `[log] format` (P19), `[client] odds_provider` (P28) and
`[live]` (P23, P24, P31). At `b3cb819` three of these have their user: `ssc serve` reads `[server] host`
and `port` (P25 #125), `--log-format` and `[log] format` set the console format (P19 #119), and the
scheduler of `serve` runs `[schedule]` and its tasks (P29 #128). Since P23 `ssc watch` reads `[live] source`,
`poll_interval_seconds` (the
interval of the live list) and `max_event_polls`; `detail_slices` and `detail_interval_seconds` are still
unused. There is no `[live] enabled` key and no `sources` list.
`loader.load_settings(config_file=...)` validates a file without touching the process
(`config validate`), `loader.find_config_file()` is `config path`, and `config_schema()` is the JSON Schema
of the file. Constructing the `Settings` creates no file; constructing `ConfigManager` still creates
`config/leagues.txt` (`03-implementation-plan.md` section 15). As built since FX-23 (PR #171): it creates
the file from `config/leagues.example.txt` only while no configuration file is in use
(`ConfigManager._legacy_list_in_use`, `sofascore_scraper/config_manager.py:151-162` at `48e4c4c`), because
only then is the legacy list the source of follows (resolution 9). With `sofascore.toml` it is not created
(a missing list is logged at debug level), an existing list is read as before, and `add_league` creates it
on demand. The end-to-end test had found a list of comments only, created at the server's start, in every
backup (finding F24).
Two parts of the model got a user in this revision. Since P20 (PR #74) the web app takes its token through
`[server] token_env`, and API v1 reads and writes the settings (section 6). Since P22 (PR #73) `sofascore_scraper/sinks`
builds sinks from `settings.sinks`; since P23 (PR #91) `ssc watch` hosts the dispatcher, so a configured
`[[sink]]` is served while `watch` runs (section 5).

Follows have three origins (`01-storage.md` 2.3):

- **Without a config file** (every existing installation): `config/leagues.txt` and `league_sports.json` stay
  the source of truth, are edited by the web UI as today, and are mirrored into the Store as origin `legacy`.
  `.env` is read as settings. Nothing changes for the user. (Built otherwise since FX-19, below, and since
  P30 a 2.x name in `.env` is only a warning.)
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
Since P30 it also names the token variable in `token_env`, so that the old `.env` keeps working beside the
file it prints.
It reads the leagues through `ConfigManager`, so it creates `config/leagues.txt` when that is missing and,
since ST-17, mirrors the follows into an existing `state.db`.

`sync` processes every enabled follow regardless of origin.

As built at `b3cb819` (P21 #124, P19 #119). The follows service exists, and with a config file a follow
added through API v1 is origin `api` in `state.db`, as above; without one a tournament follow is still
written to `config/leagues.txt` (2.7). But "`leagues.txt` is not read" and "`sync` processes every enabled
follow" do not hold yet: the downloads still read the leagues through `ConfigManager.get_leagues()`
(`sofascore_scraper/services/sync.py:337`), which is the content of `leagues.txt` only, not the follows table; a
tournament `[[follow]]` of the config file is downloaded by `ssc sync` only when it is also in
`leagues.txt`. A tournament follow added through the API while a config file is in use, and any tournament added
with `ssc follows add` (which always writes origin `api`), is therefore watched by `ssc watch` when `live`
is set but **not downloaded**, until the sync reads the follows table (FX-13). Without a config file the web
path changes nothing for the user, because the follow is written to `leagues.txt`.

As built at `b6caf2f` (FX-13 #152, FX-19 #156). "`sync` processes every enabled follow regardless of
origin" holds: the sync reads the enabled tournament follows of the follows table, every origin, each
with its season choice, and, in a sync without a target, the team, player and event follows (2.7, 3.2);
`ConfigManager.get_leagues()` is only the fallback when the Store cannot be read. The first bullet above no
longer holds: without a config file a follow added through the API, the web UI or `ssc follows add` is an
`api` row too, and `leagues.txt` is a read-only legacy source whose rows can change only `sport` and move
into the table with `PATCH {"origin": "api"}` (2.7). `config init --from-legacy` reads `leagues.txt` without
`ConfigManager` since FX-15 (4.1), so the last sentence of the ST-17 paragraph above is history.

`[client] odds_provider` got its user with P28 (the sub of the odds slices, default 1; 3.1), and FX-15
added `[client] odds_country` (`sofascore_scraper/config/settings.py:114-118`): empty by default, and when the user sets it
(for example `"TR"`, stripped and upper-cased) it is recorded as `meta.country` of every odds read; it is
never derived from the machine (a delegated decision of 2026-10-06). The provider id is always recorded
(`meta.provider_id`). `[defaults] slices` and `[slices.<sport>]` have their user since P27 (3.1), the
`prune-history` schedule task since FX-15 (2.8). `[live] detail_slices` and `detail_interval_seconds` are
still read by nothing.

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
one-shot command prints the envelope on one line. Text output follows `--lang`, `APP_LANGUAGE` (since P30
`SOFASCORE_DISPLAY__LANGUAGE`) and the system language; `[display] language` of the config file applies to the text of the commands that load the
settings, not to `--help` and not to errors raised before the settings are loaded. Python 3.14 colours
argparse help on a terminal; the CLI turns that off, so that `error.details.usage` never carries colour
codes. Locale keys of the CLI are flat, with the prefix `ssc_`.

As built (SC-1, PR #72 and P22, PR #73). The first streaming command is `events` (4.1): its lines are the
envelopes of 5.1 and its `end` line is `{"type":"end","stream_id","last_seq","count","gap"}`. Two points of
the field rules are settled by the schema (`04-schema-v1.md`). A timestamp written by the stream log has
milliseconds, always (`2026-10-01T12:00:00.123Z`), so that a parser sees one format. And the rule
"timestamps ISO-8601 UTC with `Z`" has one exception: `change_ts`, which is SofaScore's own change time and
stays an epoch integer, as the envelope of 5.1 and `00-platform.md` name it; the schema follows 5.1 and
states the exception. "Ids are integers" is about SofaScore's ids; a job id is a string (2.8).

As built by P19 (#119). The output module has a path for streaming commands (`Output.begin_stream()` and
`line()`), used by `events`, `jobs tail` and `export --out -`; `watch --stdout` still switches the mode
itself. An error in the middle of a stream is a `{"type":"error", …}` line. A reader that closes the pipe
was left open by this section; chosen: exit 0 for a streaming command (`ssc events | head -1`, which exited
1 under P22) and 1 for a one-shot command, without a traceback. `--progress text|ndjson` writes the job's
events to stderr in the `JobEvent` shape. Log lines of every CLI process go to stderr, so stdout carries the
result only, also for the translated `main.py` runs and for `serve` (uvicorn's access lines included, P25).

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
a headless run. The new CLI (P18) has the whole table in `sofascore_scraper/cli/exit_codes.py`, read from the error table,
with `combine()` for the precedence.
P11 (PR #69) moved the headless download and refresh to the job manager and changed no exit code: a breaker
stop still exits 2, a download stored as `partial` because of a failed match still exits 0 (a refresh in
which every match failed: 1), and the held lease still exits 6. One case is new: a run that is cancelled
from another process (2.8) prints `Program terminated by user.` and exits 0, as after Ctrl+C. Codes 3 and 4
come with P19.

As built by P19 (#119): the table is in force for every command and for `main.py`, whose flags run the new
commands. Against the G-03 goldens: a breaker stop 2 → 4, a storage error 1 → 5 (a data folder that is a
file is now a storage error without a traceback), Ctrl+C 0 → 130 and SIGTERM 143, a lease held 6 as
before. Partial is 3: a download with failed matches or failed listings (0 before), a refresh with some
failures (0) or in which every match failed (1). The two "failure exits 0" cases end non-zero: a run in which
every request was refused is 3, not 4, because the failed season list makes the job `partial` while the
breaker did not trip (one request per league; 4 stays reserved for a breaker stop), and a CSV export with
nothing to export is 1 (`not_found`). A cancel from another process (the web's Stop, `ssc jobs cancel`) is
130, the `cancelled` row of 2.6 ("signal or cancel request"). `status --check` exits 1 for a catalog that
must be rebuilt. `serve` exits 0 on SIGINT and SIGTERM and 1 when the server cannot start (P25 #125);
`backup verify` and the catalog commands exit 1 for a result that is not clean; `migrate` exits 3 when items
are left (ST-23 #110). The codes 130 and 143 are built as decision D6 chose them; D6 still
waits for the owner's confirmation.

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
  Since P11 (PR #69) the job manager takes `writer` for `--headless --update-all` and `--refresh-only`
  (2.8); `main.py` takes a lease itself only for `--recheck-unavailable` alone and for `--watch`. What is
  printed and the exit code are unchanged. A Ctrl+C that reaches the job body ends the job as `cancelled`
  and releases the lease (tested on the manager, not through `main.py`). Since P19 the commands of `ssc`
  take the leases (below), and since P30 `main.py` has no flags left.
- Not built: the SIGHUP reload. `sofascore_scraper/sinks` has no reload of its sinks; the reload comes with the hosts
  (P23, P25). The 10 s flush of the sinks at a graceful stop is built into `Dispatcher.run` (5.2). P23
  (PR #91) did not add it: `ssc watch` re-reads the follows every 60 s instead
  (`SCOPE_RELOAD_SECONDS`, `sofascore_scraper/services/live/supervisor.py:64` at `9b03c64`), and a changed `[[sink]]` or
  `[live]` value needs a restart. `watch` handles SIGINT and SIGTERM alike: the service stops after its
  round, saves its state, releases `live`, and the host stops the sink threads within bounded times (8.4);
  the exit code is 0. The signal handling of the other commands is P19's.
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

As built at `b3cb819` (P19 #119, P25 #125):

- **Signals of a job command** (`sofascore_scraper/cli/signals.py`). As designed: the first SIGINT or SIGTERM requests the
  cancel, the row becomes `cancelled`, the lease is released, the JSON result is printed, and the exit code
  is 130 or 143; a second signal exits at once and the row is reaped as `interrupted` later. A read-only
  command stopped by SIGTERM (`status`) prints the `cancelled` envelope with 143; `events --follow` exits 0.
  On Windows Ctrl+Break is treated as Ctrl+C. The signal tests (`tests/test_cli_signals.py`, real
  subprocesses) run on POSIX only.
- **The drain at exit.** A one-shot command that runs a job registers the configured sinks before the job
  and drains them for up to 10 s at exit (`sinks.drain_at_exit`), so a sink receives `job.started` and
  `job.finished` of a CLI run; a bad sink is `config_invalid` before the job starts.
- **`serve`** (`sofascore_scraper/cli/commands/serve.py`). Host and port default to `[server] host` and `port`. The Host
  allow-list rules of #43 are kept as they are (`security.allowed_hosts_for_bind`): names the user set are
  used as written, wherever they come from (`--allowed-hosts` goes into the loader's flag layer, so a config
  file cannot override it); a loopback bind changes nothing; one concrete address gets the loopback names
  plus that address; `0.0.0.0` or `::` is `invalid_request` (exit 2) until names are set; `--allow-any-host`
  gives `*`. A non-loopback bind without a token logs one English line and the warning
  `exposed_without_token` (printed on stderr in the app's language when the log level hides the line). The
  access token comes from the Settings; there is no `--token`. `serve` hosts the sink dispatcher as `watch`
  does (`run(stop)` in the daemon thread `serve-sinks`, a join of 15 s, then `drain_at_exit`, or `close()`
  when the thread hung; without sinks no Store is opened), so a configured sink is delivered while the web
  server runs. SIGINT and SIGTERM end it with 0; a server that cannot start (a port in use) ends it with 1
  and the warning `server_failed`. It writes the final allow-list to `SOFASCORE_ALLOWED_HOSTS` before
  uvicorn imports `sofascore_scraper.web.app`, which reads it at import. Since P30 (#186) the web app reads
  `server.allowed_hosts` from the settings; `--allowed-hosts` goes into the flag layer, and a list derived
  from the bind address or given by the flag is also written to `SOFASCORE_SERVER__ALLOWED_HOSTS` for the
  reloading child process of `--dev` (`_apply_allowed_hosts`, `sofascore_scraper/cli/commands/serve.py:165-182`
  at `216c2f9`). The `*` warning of `sofascore_scraper/web/app.py` is still
  logged in Turkish (rule 8 of the plan; P30). English since FX-23 (PR #171; `sofascore_scraper/web/app.py:61`
  at `48e4c4c`), with the other start-up warnings of the web app.
- **No SIGHUP reload.** Neither `watch` nor `serve` reloads anything on SIGHUP; a changed `[[sink]]`,
  `[live]` or `[schedule]` value needs a restart. `watch` re-reads its follows every 60 s (8.1).
- **Single instance and `--wait`.** As designed, with the leases of 2.8; `--wait SECONDS` waits for
  `writer` in `sync`, `fetch`, `refresh` and `data recheck-unavailable`. The read-only commands take no
  lease, and `export` takes none either.
- **Service operation** (`docs/deploy/`, P25). The systemd units `sofascore-serve.service`,
  `sofascore-watch.service` (with `MemoryMax` and `RestartPreventExitStatus=2 6`; `watch --idle` since #179) and a timer
  `sofascore-sync.service`/`.timer` (`ssc --wait 900 sync`, `SuccessExitStatus=3`) ship with pages on the
  token, the Host allow-list, reverse proxies, sinks, backups and `ssc migrate`, and `docs/deploy/watch.md`
  with the memory each source needs and the four warnings of `direct`. None of the units was installed or run
  anywhere, and the proxy snippets are untested. The attempt limit behind a proxy is in section 6.
- **Docker** (decision D17). The image's default command is `serve` (`CMD ["serve"]`), and the entrypoint
  runs `python -m sofascore_scraper.cli.main serve --host ${HOST:-0.0.0.0} --port ${PORT:-8000}` for no arguments,
  `serve …` or the old `web`. Other arguments went to `python main.py "$@"`, not to `ssc` as this section said,
  because `main.py` both dispatched commands and kept the old flags for one release. Since P30 (#186) the
  fallback is `python -m sofascore_scraper.cli.main "$@"` and the `web` alias is gone
  (`docker/entrypoint.sh` at `216c2f9`). Until REN-1 the image had no `ssc` console script;
  since REN-1 (#168) it runs `pip install --no-deps -e .` after copying the code (editable, because the
  version, the locales and the web build are read from `/app`), so `docker exec <container> ssc status`
  works; the entrypoint is unchanged. D17 said
  only that the Compose example sets the allowed hosts. As built the entrypoint also exports the loopback
  names (`SOFASCORE_SERVER__ALLOWED_HOSTS=localhost,127.0.0.1,[::1]`) when no allow-list is set anywhere
  (no `SOFASCORE_SERVER__ALLOWED_HOSTS` in the environment or the env file, no config file found; until P30
  the 2.x `SOFASCORE_ALLOWED_HOSTS` counted too), so a plain `docker run`
  keeps working; a config file disables that default. The Compose example adds an opt-in `ssc watch`
  service (`profiles: ["live"]`). The image creates `/app/browser-profile-live` for the app user, because
  `ssc watch` uses `<profile>-live` and `/app` belongs to root (not a volume; 8.4 and D10 did not mention
  Docker). Since FX-22 (#165) the Compose `sofascore-watch` service mounts the volume
  `watch-browser-profile-live` there, and the entrypoint clears a stale Chromium lock of that profile for
  `watch` under the same rule as the main profile, so a recreated container neither solves the challenge
  again nor fails to open its browser (exit 21). The image was not built or smoke-tested: `release.yml` builds it only on a tag, so the
  Dockerfile and `docker/smoke-test.sh` are checked in the live validation run before the release.
  Built and smoke-tested on 2026-10-08 (the morning of the live validation), from `137cabe`, as a local image
  of 1.77 GB: `docker/smoke-test.sh` passed offline (`--version`, `--help`, `serve --help`,
  `version --json`, `serve` with the HEALTHCHECK healthy as uid 1000, `/health`, the web app's index, the
  Host check, a headless Chromium start), `ssc` is on the PATH of the image, and no local configuration went
  into it (`.dockerignore` keeps only `leagues.example.txt`). The check of the release candidate found the
  Compose `sofascore-watch` service in a restart loop when no follow was live (`watch` exited 2, "nothing to
  watch"); the release pull request (#179) added `ssc watch --idle` (4.1), and the Compose service and
  `docs/deploy/sofascore-watch.service` run `watch --idle`. On the tag `v3.0.0` (2026-10-08) `release.yml`
  built the image, ran `docker/smoke-test.sh` on it offline and pushed it as
  `ghcr.io/tunjayoff/sofascore_scraper:3.0.0`, with the GitHub Release and its source archives
  (`03-implementation-plan.md` section 18).
- **Launchers.** `scripts/start_web.py` starts `python -m sofascore_scraper.cli.main serve --host 127.0.0.1`, and the
  `.sh`, `.bat`, `.command` and `.desktop` launchers go through it.

### 4.7 Mapping from today's flags

`main.py` stays as a shim for one release: a known subcommand goes to the new CLI; legacy flags are
translated by `sofascore_scraper/cli/legacy_flags.py`, which prints one deprecation line to stderr and runs the new command.
(That release was 3.0.0; since 3.1 a legacy flag is a usage error, below.)

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

As built at `b3cb819` (P19 #119, P25 #125, P26 #131; `sofascore_scraper/cli/legacy_flags.py`). A subcommand
(`python main.py sync …`, also after global flags) goes to `sofascore_scraper.cli.main.main(argv, prog="python main.py")`.
Legacy flags are parsed by the unchanged legacy parser (help texts, choices and argparse errors as before),
one line `main.py flags are deprecated and will be removed; this run is: ssc …` goes to stderr, and the
translated commands run one after the other, each in a restored process state; the next runs only when the
previous one ended with 0, 3 or 4. Usage errors of the legacy combinations (`--headless` without an action,
`--watch` without `--sport` or ids) are reported before anything runs or is created. `--version` is still
answered before any import, and `--doctor` is translated before anything heavy is imported. The table as
built, where it differs from the one above:

| `python main.py …` | runs |
|---|---|
| `--headless --update-all [--fetch-mode details] [--league-id N]` | `ssc sync [--only events] [--tournament N]` (not `--follow <name>`, which does not exist) |
| `… --recheck-unavailable[=all]` with `--update-all` | `ssc sync --recheck-unavailable legacy\|all` |
| `--headless --csv-export` | `ssc export` (the wide CSV, to `match_details/processed/` as before) |
| `--refresh-only [--league-id N] [--refresh-legacy]` | `ssc refresh [--tournament N] [--include-legacy]` |
| `--recheck-unavailable[=all] [--league-id N]` alone, or after `--refresh-only` / `--csv-export` | `ssc data recheck-unavailable [--all] [--tournament N]` |
| `--watch --sport S --league-ids A,B --event-ids X --watch-hours H` | `ssc watch --source poll --stdout --sport S --tournament A --tournament B --event X --hours H` (decision D18: the live service with polling; `watch_events.jsonl` is no longer written) |
| `--web --host H --port P [--dev] [--allow-any-host]` | `ssc serve --host H --port P …`; the legacy defaults are always passed, so `main.py --web` still opens on 127.0.0.1:8000 whatever the config file says (P25) |
| `--doctor …`, `--diagnostics [PATH]` | `ssc doctor …` (the `budget` check is now in every list), `ssc diagnostics [--out PATH]` |
| `--data-dir`, `--config`, `--ignore-rate-limit` | `--data-dir`, `--config`, `--ignore-breaker` |
| no arguments, or only `--data-dir`, `--config`, `--refresh-legacy` or `--ignore-rate-limit` | a short help on stderr (the web app `ssc serve`, `ssc --help`, the commands, and `python main.py <command>` when `ssc` is not installed), exit 2 (P26) |

Two rows of the design table differ in substance. `--config` with a `.txt` path prints a warning, and the
file is still not read as a leagues file (`ConfigManager` has no way to read another leagues file); only the
warning is new. "No arguments → prints help, exit 2" is built by P26 for `main.py` (`print_no_menu_help`,
locale key `cli_no_menu`); P19 had left the menu in place. P30 removes the legacy parser, and then `python
main.py` without arguments can hand over to `sofascore_scraper.cli.main`.

As built since P30 (#186; `main.py`, `sofascore_scraper/cli/removed_flags.py` at `216c2f9`). The flags were
deprecated in 3.0.0 and are removed in 3.1, as the table's "Fate" column said. `main.py` hands every argument
to `sofascore_scraper.cli.main.main(argv, prog="python main.py")`: `python main.py <command>` is `ssc
<command>`, `python main.py` alone prints the command list as a usage error (exit 2), and `--version` still
works. A 2.x flag before the command (`--headless`, `--update-all`, `--fetch-mode`, `--league-id`,
`--csv-export`, `--refresh-only`, `--refresh-legacy`, `--recheck-unavailable`, `--watch`, `--league-ids`,
`--event-ids`, `--watch-hours`, `--doctor`, `--diagnostics`, `--web`, `--ignore-rate-limit`) runs nothing: it is a usage error (exit
2) whose English message names the command of the second table above, for example
`--headless --update-all: the flags of python main.py were removed in 3.1 (deprecated in 3.0.0); use ssc
sync (ssc --help lists the commands)` (`removed_flags.message`, raised in `sofascore_scraper/cli/main.py:462-465`).
Only the arguments before the command are looked at, so a command's own options (`ssc sync
--recheck-unavailable`) are not affected. A usage error through `main.py` ends with `Run 'ssc --help'`
(#179). The Docker entrypoint, the systemd units of `docs/deploy/` and the installers run
`python -m sofascore_scraper.cli.main` (FX-33 #188: the installers print `… -m sofascore_scraper.cli.main
doctor`).

---

## 5. Output sinks and event streams

As built (P22, PR #73; `sofascore_scraper/sinks/` at `e0bae0c`): the sink library, its dispatcher and the command
`ssc events` (4.1) exist. **No process hosts the dispatcher yet**: nothing outside the tests calls
`Dispatcher.run`, `register` or `drain`, so the options of a configured `[[sink]]` are checked by nothing
and the sink is served by nothing after P22 alone. The hosts are `ssc watch` (P23), `ssc serve` (P25) and
the one-shot commands with their drain at exit (P19). Until then the stream log is read with `ssc events`.

Since P23 (PR #91) `ssc watch` is the first host (8.4): a configured sink is served while `watch` runs, and
`watch --stdout` follows the log without the lease. `ssc serve` (P25) and the one-shot commands (P19) are
still to come, so a sink is not served while only the web server or a download runs.

At `b3cb819` every host exists: `ssc serve` runs the dispatcher while the web server runs (P25 #125), and
the one-shot commands that run a job register the sinks before it and drain them at exit (P19 #119; 4.6).
A sink is therefore served while `watch` or `serve` runs, and gets the events of a CLI download at its end.
The events of a download started from the web are delivered by whichever process holds the `sinks` lease,
which is `serve` itself when nothing else holds it.

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
{"stream": "live", "seq": 1842, "type": "live.status_changed", "ts": "2026-10-01T18:52:04.123Z",
 "event_id": 17124861, "sport": "football", "tournament_id": 17, "source": "push",
 "data": {"from": "live", "to": "completed", "provisional": true, "change_ts": 1790856421, "score": {}}}
```

`source` is `push` or `poll`; which push source produced an event is not part of the envelope (the
`system.live_source_changed` events say which source leads). `seq` is one sequence across all streams of a data
directory. It identifies a message for as long as the
stream id (`store.streams.head().stream_id`) stays the same; consumers de-duplicate on `seq`. Numbers increase
strictly but are not consecutive within a stream.

As built by ST-18 (PR #59): the 2.x watcher was the only producer of the `live` stream. It appended its
events with the fields it has always written, as they are: `from`, `to`, `at_utc`, `change_ts`, `source`
(`live` or `event`: which request showed the change), `scores`, `provisional`, `start_ts`, `status_class`.
The envelope's `source` was always `poll`, and no `dedup_key` was set. The `data` shape shown above and the
key were the live service's to settle (P23, 8.1).

As built by P23 (PR #91; `sofascore_scraper/services/live/reducer.py:306-327` at `9b03c64`). The live service and the
legacy `--watch` alias share one reducer, so both append the settled `data`; the 2.x fields remain only in
the alias's own stdout lines and `watch_events.jsonl`. The `data` per type (also in `04-schema-v1.md`,
"LiveEvent data"):

| `type` | `data` |
|---|---|
| `live.status_changed` | `from`, `to` (status classes), `change_ts`, `provisional` (true or false on the first `completed` of the event, null otherwise), `score` (the schema's Score structure, null when it cannot be built) |
| `live.score_changed` | `from`, `to` (each `{home, away}`, the headline score), `change_ts`, `score` |
| `live.stuck` | `status_class`, `start_utc` (ISO 8601 UTC of the start from which the threshold is counted) |
| `change.recorded` | `change_seq`, the sequence of the change-log row it announces |
| `system.blocked` | `source`, `retry_in_s`, `reason` |
| `system.recovered` | `source`, `blocked_for_s` |

`live.score_changed` follows the headline score (`score_key`: `homeScore.display`, else `current`). For a
set sport (tennis, table tennis, volleyball, badminton, padel) that is the sets won, so a game or a point
inside a set is not an event; the `score` field of every live event carries all set scores. The live
validation saw no tennis event in about 5 minutes of a live third set and asked whether game changes should
be events (finding V7). FX-27 (PR #175) kept the rule as designed (also `04-schema-v1.md`, "LiveEvent data")
and wrote it into `ssc watch --help`; a test checks that a game change in tennis and in table tennis emits
nothing and a won set emits `live.score_changed`.

As built since B3 (PR #189; `sofascore_scraper/status.py` at `216c2f9`): the same rule for darts and e-sports.
A darts match played in sets (`bestOfSets` above 1) has `format: legs`, so a won set is an event and a leg
inside a set is not; a single-set match (`bestOfSets: 1`) has `format: legs_won` with no sets, and its
headline is the legs, so every leg won is an event (before, its legs were presented as sets won). An
e-sports match (`format: games_won`) carries its games in `score.sets` from `periodN`, as SofaScore sends
them: the game's own score (a finished CS2 series, 7-13, 13-9, 11-13) or the winner's 1-0, with an unplayed
or running 0-0 game left out, and nothing converted. The reducer needed no change. Stored catalogs are
re-derived on their first open (`DERIVE_VERSION` 8; `01-storage.md` 7.2), and the normalized exports carry
the corrected `score_format`, `score_sets_won_*` and `score_sets`.

Every live event carries a `dedup_key`, so the same transition is stored once:
`<id>:<type>:<from>><to>:<change_ts>` for `status_changed` and `score_changed`, with `-` for a missing
`change_ts`, and `<id>:stuck:<start_ts>` for `stuck` (`change.recorded` uses `change:<change_seq>`). 8.1
named `(event_id, type, change_ts or state hash)`; there is no state hash, so without a `change_ts` a
repeated identical transition with an identical score is a duplicate. The envelope's `source` is `poll`
for every live event today (`push` comes with P24).

What is produced today (`9b03c64`). The live event types carry the stream's prefix (`live.status_changed`,
not `status_changed`): the stream log, the sinks and the schema use these names. The `live` stream has
`live.status_changed`, `live.score_changed` and `live.stuck` from the live service and from the legacy
alias. The job manager appends `job.started` and `job.finished` since P11 (PR #69; data in 2.8). The
dispatcher appends `system.sink_dropped` (5.2). Since P23 the live service appends `change.recorded` after a
confirmation wrote a change-log row (8.1), and `system.blocked` and `system.recovered` (source `system`).
`system.live_source_changed` has no producer yet (the arbiter, P24), and the pipeline's `change.recorded`
after a refresh comes with P13.

The envelope as built (SC-1, PR #72 and P22, PR #73):

- `ts` has milliseconds, always: `2026-10-01T12:00:00.123Z`. The first version of the example showed whole
  seconds; the stream log stores milliseconds (`StreamRecord.ts`) and one fixed format is easier to parse.
  The example above was changed.
- The `data` of the example (`from`, `to`, `provisional`, `change_ts`, `score`) was not what was stored
  until P23: a consumer got the 2.x watcher's fields listed above, with `scores` as the 2.x score sheet.
  P23 settled the shape per event type (table above). The schema still types `data` as an object; the
  section "LiveEvent data" of `04-schema-v1.md` states the settled shape. The sinks pass `data` through as
  stored.
- `change_ts` inside `data` is an epoch integer, the one exception to the timestamp rule of 4.4.
- A field that does not apply is `null`: a `job.started` event has `event_id`, `sport` and `tournament_id`
  null, and its `source` is `job`. `dedup_key` is internal to the log and not part of the envelope.
- Every sink writes the same bytes: one line of ASCII JSON (`ensure_ascii`) per envelope, in the field order
  of the example.
- Two functions build the envelope. `schema.live_event_from_record(record).to_dict()` is the schema's form
  (SC-1), and SC-1 asked the sinks to write the envelope with it. The sinks and `ssc events` do not call it:
  they use their own `Envelope.from_record(record).to_dict()` (`sofascore_scraper/sinks/base.py:103-122` at `e0bae0c`),
  because nothing outside `sofascore_scraper/schema` imports the schema package yet. The two give the same document for
  the same record. P23 (PR #91) pinned that with a test for live, change and job records rather than
  building `Envelope.to_dict` from the schema, so that `sofascore_scraper/sinks/base.py` stays standard-library only.

### 5.2 Sink interface

```python
class Sink(Protocol):                                          # sofascore_scraper/sinks/base.py
    name: str
    def accepts(self, env: Envelope) -> bool: ...              # compiled from the `events` globs and filters
    def deliver(self, batch: Sequence[Envelope]) -> None: ...  # raise RetryableSinkError / FatalSinkError
    def close(self) -> None: ...

class Dispatcher:                                              # sofascore_scraper/sinks/dispatcher.py
    def __init__(self, store: Store, sinks: Sequence[Sink], *, clock: Clock | None = None,
                 jitter: Callable[[], float] | None = None, read_limit: int = 500): ...
    def register(self) -> None: ...                            # records new sinks at "now"; needs no lease
    def step(self, *, flush: bool = False, until: Callable[[], bool] | None = None) -> float: ...
        # one round over all sinks; returns the seconds until the next work (inf: nothing pending)
    def run(self, stop: StopToken, *, flush_timeout: float = 10.0) -> None: ...   # long-running hosts
    def follow(self, stop: StopToken) -> None: ...             # sinks without a cursor (stdout), no lease
    def drain(self, timeout: float = 10.0) -> DrainReport: ... # one-shot commands before exit
    def status(self) -> tuple[SinkStatus, ...]: ...            # per sink, in this process
    def close(self) -> None: ...                               # closes the sinks

# sofascore_scraper/sinks/__init__.py
def build_sinks(specs: Sequence[SinkSpec], *, environ=None, data_dir=None, clock=None) -> list[Sink]: ...
def dispatcher_for(store: Store, specs: Sequence[SinkSpec], *, environ=None, clock=None) -> Dispatcher | None: ...
def drain_at_exit(dispatcher: Dispatcher | None, timeout: float | None = None) -> DrainReport | None: ...
```

The block is the interface as built by P22 (PR #73, `sofascore_scraper/sinks/` at `e0bae0c`). The `Sink` protocol is the
one of the first version. The first version's `Dispatcher` had `run` and `drain` only; `register`,
`step(until=)`, `follow`, `status` and `close` were added, and the three functions of the package root are
what a host calls. `StopToken` (anything with `is_set()` and `wait(timeout)`, for example a
`threading.Event`) and `Clock` are defined in `sofascore_scraper/sinks/base.py`, because `sofascore_scraper/services/context.py` has no
`Clock` yet. Besides the protocol the dispatcher reads four optional attributes of a sink (`uses_cursor`,
`batch_size`, `linger_seconds`, `max_age_seconds`) and an optional `last_delivered()`; `BaseSink` carries
their defaults.

The dispatcher keeps one cursor per sink (`store.streams.cursor(sink)`). It reads after the cursor, delivers
in order, and advances the cursor only on success: at-least-once, ordered, resumable after restart. Exactly
one process dispatches at a time (the `sinks` lease). How that is built:

- **A new sink starts at "now".** The design did not say where a sink without a cursor starts; starting at
  the beginning would send up to seven days of old events to a webhook the first time it is configured. A
  sink is known once `store.runtime` holds the key `sink:<name>`; a known sink resumes at its cursor, also
  when that cursor is 0. A sink without a cursor (a configured `type = "stdout"`) starts at "now" at every
  start.
- **Long-running hosts: `run(stop)`.** It takes the `sinks` lease, dispatches until `stop` is set, then
  flushes for up to 10 s and releases the lease. The first version said that `serve` and `watch` hold the
  lease while they run. They cannot both hold it, so `run()` waits when another process holds the lease (it
  tries again every 5 s) and takes over when the lease is free: the two services can run side by side on one
  data directory, and one of them dispatches. `run()` does not close the sinks; the host calls `close()`.
- **One-shot commands: `register()`, then `drain(timeout)`.** `register()` records new sinks without the
  lease, before the job, so that the job's own events are delivered at exit; without it a sink that is first
  seen at exit starts after those events. `drain` tries the lease, delivers the backlog for up to `timeout`
  seconds and returns a `DrainReport` (`complete`, `timed_out`, `lease_held`, `holder_pid`, per-sink
  status). When the lease is held elsewhere it delivers nothing and says so: the backlog is left to the
  holder. `sinks.drain_at_exit(dispatcher)` wraps it for a command: 10 s by default, it never raises, closes
  the sinks, and does nothing for `None`. `sinks.dispatcher_for(store, settings.sinks)` returns `None` when
  no sink is configured; it builds the sinks with `build_sinks`, which raises `ConfigError` for a bad option
  or a missing secret (4.3).
- **`follow(stop)`** serves sinks without a cursor and takes no lease, does not prune and writes nothing to
  `state.db`. `watch --stdout` is `Dispatcher(store, [StdoutSink()]).follow(stop)` (P23).
- **Stopping.** A stop request and the time limit of `drain` are checked before every sink and after every
  delivery (`step(until=)`). A stop or a drain can therefore overrun by the one delivery in progress, for a
  webhook at most its request time-out (10 s by default); no further delivery is started after the time is
  up.
- **One thread delivers for all sinks.** Deliveries are made one after the other in the dispatcher's thread.
  A `deliver()` that never returns (stdout into a pipe nobody reads, a file on a hung network mount) blocks
  the other sinks and the shutdown; the webhook is bounded by its time-out. A host runs the dispatcher in a
  daemon thread and joins it with a time-out. As built by the first host, `ssc watch` (P23, PR #91): the
  `--stdout` follower runs in a thread of its own, so a reader of stdout that stops does not hold up the
  configured sinks; the configured sinks still share one dispatcher thread. The shutdown is bounded: both
  threads are daemon threads, joined for at most 15 s (sinks) and 5 s (stdout), then `drain_at_exit`
  delivers for up to 10 s, which is skipped and replaced by `close()` when the sink thread hung
  (`sofascore_scraper/cli/commands/watch.py:157-174` at `9b03c64`).
- **Retries.** A `RetryableSinkError`, and any unexpected exception of a sink, is retried with exponential
  back-off and jitter, 1 s doubling to 5 min, each delay multiplied by a random factor between 0.5 and 1; a
  receiver's `Retry-After` is honoured up to the 5 min cap. The head batch blocks its sink, and the other
  sinks continue. A `FatalSinkError` disables the sink until restart; its cursor stays.
- **Max age is the age of the event.** The first version dropped "the head batch" after `max_age`. As built
  the age is `now - ts` of an event, checked after a failed attempt: a receiver that is up gets a backlog of
  any age, and one that is down loses the events older than the sink's `max_age`. All expired events are
  dropped in one go instead of one failed request per batch, and the range is recorded as one
  `system.sink_dropped` event with the data {sink, reason, first_seq, last_seq, count, max_age_seconds},
  reason `max_age`. Only the webhook has a `max_age` (24 h by default); a file sink never drops and waits at
  its head batch.
- **Events pruned before delivery** (`StreamBatch.gap`) count as lost and are recorded the same way, with
  reason `pruned` and `count: null`, once per incident.
- **Store errors.** The design did not say that Store calls can raise plain `sqlite3.Error`. The Store turns
  only a lock time-out into `StoreBusy`; other SQLite errors (a locked database when WAL is not available,
  an I/O error) leave it as `sqlite3.Error`. A long-running caller has to catch both (`STORE_ERRORS`,
  `sofascore_scraper/sinks/dispatcher.py:63`). In the dispatcher either one on a read or a cursor write delays the sink
  and does not end the dispatcher; a cursor that could not be written is written at the next step; `run()`
  logs any other unexpected error and goes on after 5 s.
- **A replaced log.** The dispatcher remembers the stream id and starts its positions again when a read
  reports another one (`state.db` was recreated). With today's Store this cannot happen inside one process;
  it is a guard. Since ST-24 (#109) a restore also writes a new `stream_id`, so a consumer of the log (a
  dispatcher, a receiver that de-duplicates on `seq`) sees that its stored positions no longer apply.
- **Pruning is the lease holder's.** While `run()` holds the `sinks` lease it prunes the stream log once per
  hour, to 7 days and 1,000,000 rows (`01-storage.md` 9.3). `drain` and `follow` do not prune. The two
  limits are constants of the dispatcher (`PRUNE_MAX_AGE_SECONDS`, `PRUNE_MAX_ROWS`) until ST-24 makes them
  the defaults of `StreamLog.prune`. When no sink is configured there is no dispatcher, so the live service
  prunes itself: once per hour while it holds `live`, with the same two limits, which it imports from
  `sofascore_scraper.sinks.dispatcher` (P23; ST-24 moves the constants for both). ST-24 (#109) did: the limits are
  `DEFAULT_PRUNE_MAX_AGE_SECONDS` (7 days) and `DEFAULT_PRUNE_MAX_ROWS` (1,000,000), exported from
  `sofascore_scraper.store` and the defaults of `StreamLog.prune()`, which before removed nothing when called without
  arguments; `None` still turns a limit off. The dispatcher's constants are gone; it and the live service
  pass the Store's defaults explicitly.
- **No reload.** A SIGHUP reload of the sinks (4.6) is absent; a changed `[[sink]]` needs a restart of the
  host.

Delivery guarantees as built, in one place: at least once (a crash between a delivery and the cursor write
replays the batch, and receivers de-duplicate on `seq`); in order per sink; the head batch blocks its sink
and nothing is skipped, except events dropped by `max_age` or lost to pruning, and both are recorded in the
`system` stream; resumable at the cursor after a restart; one dispatching process per data directory.

| Sink | Behaviour |
|---|---|
| `stdout` | NDJSON lines; no cursor (starts at "now" or `--after`); this is `watch --stdout` and `events --follow`. As built: `ssc events` reads the log itself and uses `StdoutSink` only to write its lines; a configured `type = "stdout"` sink is served by the lease holder and starts at "now" at every start; a closed stdout disables the sink |
| `file` | append NDJSON; rotation by size or day; cursor recovered from the last line of the newest file. As built: LF line ends, one `fsync` per batch before the cursor moves; the active file is always `path`, a rotated file is renamed to `<stem>.<time of its last write><ext>` (`live.20261001T235958Z.ndjson`), and `keep` limits the rotated files; an incomplete last line left by a crash is removed at the next open; the cursor is recovered from the last line only when the stored cursor is behind that line and the line equals the event in the log, so a file left over from another log is ignored |
| database | the supported database output is the SQLite export in the public schema (`ssc export --format sqlite`); a PostgreSQL sink later implements `Sink` with an upsert on `seq`. `catalog.db` and `state.db` are internal files (decision S5) |
| `webhook` | below |
| message queue | later, same interface |

One limit of the file sink: a batch that is retried in the same process does not repeat the lines it has
written, but that holds only for a failure after the write. A write that fails in the middle (a full disk)
can leave the complete lines of that batch in the file, and the retry writes the whole batch again. It is at
least once, as designed.

Sinks are configured only in the config file or environment, never through the HTTP API: with no accounts,
an API that registers outbound URLs would be an open relay (decision D11). P22 added no route, and a test
pins it. The option keys of each sink type are in 4.3. Since P21 (#122) one read-only route exists,
`GET /api/v1/sinks` (gap G1 of `05-web-ui.md`, approved with that document); the test
(`tests/test_sinks.py::test_no_http_route_and_no_web_module_knows_sinks`) allows exactly that route and its
two schemas, no write method and no web module that imports `sofascore_scraper.sinks`. D11 holds: sinks are still
configured in the config file or the environment only.

Secrets and addresses. The signing secret and the webhook address (its user, password, path and query can be
the credential) never reach a log line, `sink_cursors.last_error`, a stream event, `repr()` or a status
object: error texts are built from the status code or the exception class, and the address, its parts and
the secret are scrubbed from every text as a second line of defence. In logs a webhook is named by its sink
name and host. `ssc config show` and the diagnostics bundle show the address by its host only (4.3).

Not built, with the item that owns it: a host for the dispatcher in `serve` (P25) and the drain at exit of
one-shot commands (P19; `watch` hosts it since P23);
`ssc status` per sink, which needs the cursor listing of ST-24
(`Dispatcher.status()` covers one process only); `config validate` for sink options (P19); the SIGHUP reload
(with the hosts); a message-queue and a database sink (later).
At `b3cb819` the hosts, the drain at exit, the cursor listing and `config validate` for sinks are built
(P25, P19, ST-24). The state per sink is read from any process: `store.streams.cursors()` (ST-24) gives
each sink's cursor, the time it was written and the last error, and `sink_status.sink_states` (P21 #122)
adds the head of the log, the lag in events and in seconds, `served` (a process holds `sinks`), the state
(`ok`, `error`, `pending`) and `dropped`, the sum of the retained `system.sink_dropped` events, which can
undercount after the log was pruned. `GET /api/v1/sinks` shows that; `ssc status` shows the cursor and the
last error only, not the lag. Not built: the dispatcher's next retry time (it lives in the memory of the
delivering process; `05-web-ui.md` G1 named `next_retry_at`), a disabled state (a `FatalSinkError` is
visible only in the process that hit it), the SIGHUP reload, and a message-queue and a database sink.

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

As built (P22, PR #73; `sofascore_scraper/sinks/webhook.py` at `e0bae0c`, with `tests/test_webhook_contract.py` against a
real HTTP server on 127.0.0.1). The request is as above. What the list does not say:

- The delivery id stays the same for the retries of one batch, and the signature is made again with the
  current time at every attempt. "Per attempt group" holds within one process: after a restart the same
  events get a new delivery id, and a new `Idempotency-Key` if more events joined the batch.
- A 410 raises `FatalSinkError`: the sink is disabled until the process restarts, its cursor stays where it
  is, and the other sinks go on.
- A redirect is not followed (the signed body is not sent to an address that is not in the configuration);
  it counts as another response and is retried. `Retry-After` is honoured (5.2).
- `max_age` is per event age, and all expired events are dropped together (5.2).
- A user and password in the address (`https://user:pass@host/…`) are taken out of the address and sent as
  `Authorization: Basic`; `urllib` does not accept them in the address.
- The request is made with `urllib` and an opener built from its default handlers, so the `http_proxy` /
  `https_proxy` variables of the environment apply to it. The time-out (`timeout_seconds`, 10 s by default)
  covers the connection and every read; it does not cover name resolution.
- Errors are recorded without the address. A network error is recorded by its class and the operating
  system's message, a refused delivery by its HTTP status, and an unexpected error of the transport by its
  class only, because its text could carry the address or a header.
- `webhook.verify_signature(secret, header, body, tolerance=300)` is the receiver's side of the contract: it
  rejects a missing header, a wrong or forged signature and a timestamp older than the tolerance. A forged
  header with non-ASCII characters is rejected, not an error.
- `ssc status` per sink is not built (ST-24). The last error of a sink is stored with its cursor
  (`sink_cursors.last_error`). At `b3cb819` `ssc status` lists each sink's cursor and last error and
  `GET /api/v1/sinks` adds the newest sequence and the lag (5.2); the lag is not in `ssc status`.

---

## 6. HTTP API v1 (resource level)

The field-level contract is a later task (plan item SC-1 for the schema, P21 for the routes). Fixed here:
prefix, resources, envelopes, error model, streaming, raw access, aliases.

State at `e0bae0c`. The schema half exists: `04-schema-v1.md` and `sofascore_scraper/schema` (SC-1, PR #72), approved on
2026-10-02 (decision P2). The foundation of the API exists (P20, PR #74): the error model with request ids,
the router, the access token with its attempt limit, the committed OpenAPI document, and the routes for
health, status, sports, jobs and settings. The resource routes of the table below (tournaments, seasons,
events, changes, follows, exports, backups, diagnostics, logs) are P21's. "As built" after the error
paragraph describes the foundation.

- Prefix `/api/v1`. `/health` stays unversioned for load balancers and the launcher.
- Every route declares a response model. `docs/api/openapi-v1.json` is committed;
  `tests/test_openapi_snapshot.py` compares it with `app.openapi()` and fails on any undeclared change;
  `python -m sofascore_scraper.web.openapi --write` regenerates it. Frontend types are generated from the file.
- Envelopes: single resource `{"data": {…}}`; collection
  `{"data": […], "page": {"limit": 50, "next_cursor": "…"}}` with cursor pagination.
- State-changing requests are never GET. The origin check (`web/app.py:37-47`) and host check
  (`web/app.py:51`) stay. With a token configured, every `/api/*` route requires
  `Authorization: Bearer <token>` (decision D13). PR #43 implements the token for the existing routes
  (`SOFASCORE_API_TOKEN`, since P30 only `SOFASCORE_SERVER__TOKEN` or the variable `token_env` names; a
  session cookie set by `POST /api/auth/login`, since P21 `/api/v1/auth/login`, is accepted as well, for the
  web UI and its SSE stream; `/health` stays open), moves the origin check to `sofascore_scraper/web/security.py`, and adds the
  response headers and the Content-Security-Policy.
- No live data over HTTP. There is no `/live/*` resource and no live SSE stream (owner decision of
  2026-10-01). `/status` reports whether a live service is running (lease, leading source, last heartbeat),
  which is service health, not live data.

| Resource | Methods | Service |
|---|---|---|
| `/sports`, `/sports/{slug}` | GET | `QueryService.sports` (registry) |
| `/tournaments`, `/tournaments/{id}`, `/tournaments/{id}/seasons` | GET | query |
| `/tournaments/search`, body `{q, sport, kinds}` | POST (calls SofaScore; the first version said GET, which #43's rule forbids) | `FollowsService.search` (tournaments, teams, players; FX-19); since FX-20 `async`, a 10-minute server cache and a cancel check tied to the connection (499 when the client left) |
| `/catalog/suggest?q=&sport=&limit=` | GET (no SofaScore request) | `QueryService.suggest`: stored tournaments and teams for the type-ahead (FX-20) |
| `/seasons/{id}`, `/seasons/{id}/slices`, `/seasons/{id}/slices/{key}`, `/seasons/{id}/standings` | GET | query, `OwnerDataService` (P28) |
| `/events?sport=&tournament=&season=&from=&to=&status=&participant=&has=` | GET | query |
| `/events/{id}`, `/events/{id}/slices`, `/events/{id}/slices/{key}`, `/events/{id}/odds`, `/events/{id}/odds/{key}` | GET | query, `OwnerDataService` (P28) |
| `/events/{id}/extra` | GET | `QueryService.event_extra` (FX-26): SofaScore's result note, the series score, the venue and the referee from the stored event payload; every field null without a payload, 404 for an unknown event |
| `/events/{id}/raw`, `/events/{id}/slices/{key}/raw` (no `?raw=1` form) | GET | `QueryService.raw`: the stored SofaScore payload (same values and key order as the response; see `01-storage.md` 4.1), with `ETag` (the payload's sha256) and `X-Sofascore-Fetched-At` |
| `/changes?since=` | GET | query (change log by its own `seq`) |
| `/follows`, `/follows/{id}` | GET, POST, PATCH, DELETE | follows |
| `/jobs`, `/jobs/{id}`, `/jobs/{id}/cancel`, `/jobs/{id}/events` | GET, POST, POST, GET (SSE) | job manager |
| `/jobs` body `{kind, spec}` | POST | starts `sync`, `fetch`, `refresh`, `export`, `backup`, `clear`, `rebuild` and `restore` (a check with `dry_run`, a real restore since FX-13) |
| `/exports`, `/exports/{id}/download` | GET | export results |
| `/backups`, `/backups/{name}` | GET | backup |
| `/settings` | GET, PATCH | settings; locked fields flagged |
| `/health`, `/status`, `/diagnostics`, `/diagnostics/bundle`, `/logs` | GET | status |
| `/status/check`, body `{target: "sofascore"}` | POST (one request to SofaScore) | the connection check through the client |
| `/sinks` | GET (read-only; added by P21) | `sink_status.sink_states` (5.2) |
| `/auth`, `/auth/login`, `/auth/logout` | GET, POST, POST (open without a token) | the session cookie of PR #43 |

SSE (`/jobs/{id}/events` only): each message has `id: <seq>`, `event: <type>`, `data: <JobEvent>`, where
`seq` is the job's own event number. Resume with the standard `Last-Event-ID` header or `?after=`. A comment
line is sent every 15 s. If `after` is older than the retained events of the job (the last 2,000 are kept), the
server sends one `stream.gap` event with `oldest_seq` and closes; the client resynchronises through
`/jobs/{id}`.

SSE as built (P20; `sofascore_scraper/web/sse.py` at `e0bae0c`): as designed, with three additions. The first bytes of a
stream are the comment line `: stream open`, so that the client sees the connection open. The `data` of a
message is `{job_id, seq, ts_ms, type, data}`, and the data of `stream.gap` is
`{job_id, after, oldest_seq}`. The stream ends after the job's last event, when the client leaves and when
the server shuts down. `Last-Event-ID` wins over `?after=`, and a value that is not a sequence number is
422. The stream works for a job that another process runs. Without the `sse-starlette` package the route
answers 501 `not_supported`.

Errors: `{"error": {"code": "job_running", "message": "…", "details": {…}, "request_id": "…"}}` with the
status from the table in 2.6. `message` is English; clients translate by `code`. Upstream trouble during a
request that calls SofaScore is 503 `blocked`/`rate_limited` or 502 `upstream_error`, never 429. At
`0aa73b4` the single-match route had a substring match that would answer 429 (`routes/matches.py:416-417`),
but that code was not reached for a blocked `/event`: the route answered 404, and 200 when only the slices
were blocked. FX-1 (PR #60) removed the substring match. The legacy route now answers the typed upstream
error of `sofascore_scraper/web/upstream.py`, which the league routes use since PR #23: `{"detail": {"reason", "message"}}`
with reason `blocked`, `browser`, `network` or `upstream` and status 502, or `rate_limited` and status 503.
So the legacy route answers `blocked` with 502, the status `upstream.py` has always used, while v1 answers
it with 503 (the table of 2.6); P13 maps the route to the v1 codes when it moves to the pipeline. The same
route is refused with 409 `job_running` while a job of the same server runs or another process holds the
writer lease.

As built: the foundation (P20, PR #74; `sofascore_scraper/web/app.py`, `errors.py`, `deps.py`, `sse.py`, `openapi.py` and
`sofascore_scraper/web/api/` at `e0bae0c`).

- **Routes that exist.** `GET /api/v1/health`, `/status`, `/sports` and `/sports/{slug}`; `GET /jobs`
  (filters `state` and `kind`, cursor paging), `GET /jobs/{id}`, `POST /jobs`, `POST /jobs/{id}/cancel` and
  `GET /jobs/{id}/events` (SSE, resuming on `Last-Event-ID`); `GET /settings` and `PATCH /settings`. There
  is no live route and no live stream, and a test asserts that on the whole OpenAPI document. The
  unversioned `/health` stays as it is.
- **Jobs.** The routes read and write through `JobManager` on the web's process-wide job store
  (`sofascore_scraper/web/deps.py`), so jobs of other processes are listed and can be cancelled, and a job started through
  v1 is the job the legacy UI shows. `POST /jobs` answers 202 with a `Location` header and starts the job in
  a background thread. It starts only `sync`, `fetch` and `refresh`, on `SyncService` without the CSV phase;
  the spec is today's `SyncSpec` shape (`league_id`, `selections` with `season_ids` or `match_ids`) and
  changes when the pipeline moves to targets and phases (P13). `export`, `backup`, `clear` and `rebuild` are
  in the request schema and answer 501 `not_supported` until their services stand behind the job manager
  (P21, with EX-1 and ST-24). A held lease is 409 with the holder in `details`. The job in a response has
  the fields of the job model with the new state names; it shows `origin.pid` and `origin.host`, which is
  kept (decision of 2026-10-02, 2.8).
- **`/status`** has `version`, `api_version`, `auth_required`, `bridge`, `throttle` and `active_job`. It has
  no live-service fields yet (lease, leading source, last heartbeat). P23 was to add them to `Status` in
  `sofascore_scraper/web/api/v1/meta.py` and did not, because that would have needed `docs/api/openapi-v1.json`
  regenerated, which its batch could not edit. `services.live.live_status(store)` gives them (`running`,
  `pid`, `host`, `source`, `sports`, `heartbeat_at`, `blocked`, `last`;
  `sofascore_scraper/services/live/supervisor.py:631-649` at `9b03c64`), ready for P21 here and P19 for `ssc status`. It
  has no data summary and no coverage yet (P21).
- **Settings.** `GET /settings` lists every setting of the model with its value, layer, source name,
  `locked`, `writable` and `secret`; secrets are masked. `PATCH /settings` writes
  `CONFIG_DIR/overrides.json` and refuses locked and read-only keys (4.3).
- **Error model and request ids.** Every v1 error has the body above with the status of the table in 2.6;
  `invalid_request` is 422 for a rejected value and 400 for a refused well-formed request (2.6). The
  framework's own errors on v1 paths (unknown path, wrong method, validation) use the same body; validation
  details carry location, message and type, never the submitted value. A storage error gets an English
  message and the Store's own (Turkish) text in `details.store_message` (English too since FX-25, PR #172;
  2.6); the text of an unexpected error is
  logged with the request id and not returned. Every v1 response carries `X-Request-Id`; an id sent by the
  client is used when it is 1 to 64 characters of letters, digits, `.`, `_` and `-`. The legacy routes are
  untouched by all of this: the handlers look at the path. A refused Host is outside the model (2.6).
- **Access token.** A missing or wrong token on `/api/v1` is 401 `unauthorized`; the legacy routes keep 401
  `auth_required`. The token, Origin and Host checks and the response headers of PR #43 apply to `/api/v1`
  as to the legacy routes, because they hang on the `/api` prefix. The token comes from Settings
  (`[server] token_env`); the hand-over to `security._startup_token` and the warning for a short token are
  described in 4.3.
- **Attempt limit.** Wrong tokens are limited (`deps.AttemptLimiter`). After 5 failed attempts from one
  address further attempts from that address are refused for 30 s, and every failed attempt after a lock
  doubles the lock, up to one hour. `POST /api/auth/login` and wrong `Authorization: Bearer` headers share
  one counter, because a limit on the sign-in alone could be bypassed by guessing through any other route. A
  request without credentials and a request with a valid session cookie are neither counted nor locked.
  While an address is locked its attempts are not evaluated, the right token included: a v1 route answers
  401 `unauthorized` with `Retry-After`, a legacy route and the sign-in answer 429 `too_many_attempts`
  (2.6). The right token resets the counter, and it is forgotten after 15 minutes without a failed attempt.
  The limit is per remote address and in memory only: it ends with the process, there is no trusted-proxy
  setting, and behind a reverse proxy every client has the proxy's address, so five wrong Bearer tokens lock
  all Bearer clients (browser sessions keep working). The plan puts that into the deploy documentation of
  P25. The web UI has no text for `too_many_attempts` yet (FE-2).
  P25 (#125) found that this was inexact. uvicorn's `ProxyHeadersMiddleware` is on by default and trusts
  `X-Forwarded-For` from the addresses in `FORWARDED_ALLOW_IPS` (default `127.0.0.1,::1`), so behind a
  reverse proxy on the same host the limit already counts per client (verified with a real server: client
  A locked after five wrong tokens, client B still answered 401). A proxy on another machine or in another
  container needs `FORWARDED_ALLOW_IPS` set to its address; without that its clients share one lock. The
  deploy page (`docs/deploy/README.md`) documents both and warns against `*`. The limit is still in memory
  and ends with the process.
- **OpenAPI document.** `docs/api/openapi-v1.json` is committed. It is the v1 view of the application's
  document: the paths under `/api/v1` and the schemas they use, with `info.version` "1".
  `tests/test_openapi_snapshot.py` compares it with what the application generates and fails on any
  undeclared change; `python -m sofascore_scraper.web.openapi --write` regenerates it and `--check` exits 1 when it is out
  of date. Run as a program, the module loads the application with temporary directories and writes log
  lines to stderr. Frontend types are not generated from the file yet; the frontend still uses the legacy
  routes.
- **Import-time state.** Importing `sofascore_scraper.web.app` creates the job store, `config/leagues.txt` and the log
  file, because `src/web/routes/common.py` builds its `ConfigManager` and the process-wide job store when it
  is imported. `sofascore_scraper/web/deps.py` reads those objects at call time. P21 removes the module state, which is
  also the remaining cause of the job database that a web job creates under the data directory (2.8).
  Done by P21 (#127): the module state is in `sofascore_scraper/web/deps.py` (`config_manager()`, `job_store()`,
  `job_manager()`, `refresh_job_mirror()`, `store()`), created at the first request.

As built at `b3cb819`: the resources (P21 in five pull requests, #122, #123, #124, #126 and #127; the
routes in `sofascore_scraper/web/api/v1/`, the OpenAPI document regenerated each time, and the generated frontend types
`frontend/src/api/v1/schema.ts` with it). Every route of the table exists, with these differences:

- **Session (#122).** `GET /api/v1/auth`, `POST /api/v1/auth/login` and `POST /api/v1/auth/logout` set and
  delete the session cookie of the legacy routes (HttpOnly, SameSite=Strict, Secure behind TLS) and answer
  without a token: `security.AUTH_OPEN_PATHS` names the three v1 paths. A wrong token is 401
  `unauthorized` with `details.reason = "invalid_token"`; a locked address 401 with `details.reason =
  "too_many_attempts"`, `details.retry_after` and `Retry-After`. The counter is the one the legacy sign-in
  and wrong Bearer headers share.
- **`/status` (#122, #128).** Next to P20's fields: `schema_version` (1; how API v1 states the version of
  the data schema, decision 19 of `04-schema-v1.md`), `live` (`live_status(store)`: `running`, `pid`,
  `host`, `source`, `sports`, `heartbeat_at`, `blocked`, `leaders`, `last_switch`; state only), `summary`
  (`StatusService.summary` with the configured leagues: counts, `legacy_events`, `catalog_rebuild_reason`,
  per tournament the counts, coverage, name, `followed` and `last_update_utc`, and `disk`), `leases[]`
  (every lease held in any process, with purpose, pid, host and `since_utc`), `capabilities` (`parquet`,
  `sse`, `scheduler`), `schedule` (P29; 2.8) and `storage_error` (the code when the data directory cannot be
  opened: `/status` still answers, with `live` and `summary` null). The slice-level coverage is not there
  (2.7). `summary.disk.total` counts the four 2.x areas only and reads 0 for a folder in the v3 layout; the
  UI sums `summary.disk.entries` instead (`05-web-ui.md` G21).
- **`POST /status/check` (#122)** needs the body `{"target": "sofascore"}`: every v1 route is called
  without a body by the security tests, and an empty POST must not reach SofaScore. It sends one request for
  football's live list through the client (one try, 10 s, the shared budget). A failed check is 200 with
  `ok: false` and a `reason` in the words of `sofascore_scraper/web/upstream.py`, plus the bridge snapshot. Because it goes
  through the client and not the browser bridge, it can report `blocked` where the legacy
  `POST /api/bypass/test` (which keeps the browser path, pinned by `tests/test_bypass_test.py`) succeeds.
- **`GET /sinks` (#122)** per configured sink in configuration order: `name`, `type`, `target` (file path
  or masked webhook address), `events`, `state`, `served`, `cursor`, `head_seq`, `lag_events`,
  `lag_seconds`, `last_delivered_at_utc`, `last_error` and `dropped` (5.2).
- **Read resources (#123).** The records are pydantic mirrors of the `sofascore_scraper/schema` dataclasses
  (`sofascore_scraper/web/api/v1/records.py`; `Status` is published as `EventStatus`, because `/status` owns the component
  name), checked against `schema.json_schema()` by a test. `/tournaments` takes `sport`, `q`, `followed`,
  `limit` and `cursor`, and each record adds `category` (the Category record) and `followed` to the
  schema's fields. `/events` takes `sport`, `tournament`, `season`, `participant`, `status` (every status
  class, ST-27 #129), `from`, `to` (a date `to` includes the day), `has=details|missing` (the values the
  design left open), `q`, `followed`, `sort=-start_utc|start_utc`, `include=slices_summary`, `limit` (at
  most 200) and `cursor` (tied to the order: a cursor of the other order is 400). There is no "changed
  since" order; a consumer that syncs uses `/changes?since=`. `/events/{id}/slices` lists every slice row
  and the selected slices that were never requested (`not_requested`). `/events/{id}/odds` lists the stored
  slices whose key starts with `odds`, without payloads (empty until P28). `/changes` takes `since`,
  `event_id`, `tournament`, `from`, `to` (the recorded time), `order=asc|desc`, `limit` and `cursor`.
- **Raw routes (#123).** As in the table, without `?raw=1`: a second form would make one route's response
  schema depend on a query parameter. The payload is always decompressed (the open question of
  `03-implementation-plan.md` section 14, decided; the Store has no method for the stored gzip bytes); a
  legacy event gives its file's bytes (indented JSON), with the same values and key order as the v3 form.
  `ETag` is the sha256 of the bytes, 304 answers `If-None-Match`, and 404 means no payload is stored.
- **Follows (#124).** `GET /follows` (`kind`, `origin`, `enabled`, `q`: a case-insensitive part of the
  name), `GET /follows/{id}`, `POST /follows` (201 with `Location`), `PATCH` and `DELETE`. The id is
  `<kind>:<entity_id>` (`tournament:17`). A record has `origin` and `writable` (the fields PATCH may change).
  Removing is refused while a job runs (409). 409 `follow_exists` for an entity or a tournament name that
  is followed, `follow_managed` for a config follow. `slices` is shown and cannot be set (P27).
- **Data jobs (#126, #130).** `POST /jobs` with `export` (spec `dataset`, `format`, `schema`, `profile`,
  `filter` with `sport`, `tournament_ids`, `season_ids`, `event_ids`, `status_classes`, `from`, `to`;
  written to `DATA_DIR/exports/<job id>.<ext>`, since FX-19 to a readable name, 2.7; Parquet without
  `pyarrow` is 501), `backup` (`scope`,
  `include_env`), `clear` (`scope`, `confirm`; 400 `confirmation_required` without it), `rebuild` (`mode`)
  and `restore` (`name`, `force`; `dry_run: false` was 501 until FX-13). An invalid spec is 422 and starts no job. The
  leases are in 2.8; since FX-23 (#171) an export job runs under the `export` lease in a job store of its
  own, next to a running download, and a held `export` lease or a running maintenance job is 409
  `data_operation_running`. The legacy wide CSV of an export job is written by the Store's row writer (UTF-8,
  `\n` line ends, empty cell for null), so it differs in line ends from the legacy streaming download
  (`\r\n`). `GET /exports` lists the export jobs (`id` is the job id; `dataset`, `format`, `schema`,
  `profile`, `filter`, `rows`, `events`, `bytes`, `skipped`, `file`, `media_type`, `available`,
  `schema_version`), and `/exports/{id}/download` serves the file of a succeeded export from a path built
  from the job id. `GET /backups` lists `name`, `scope`, `created_at_utc`, `bytes`, `format` and
  `with_env`; `GET /backups/{name}` is the zip (there is no metadata route per backup). The downloads are a
  `FileResponse` subclass (`sofascore_scraper/web/api/v1/downloads.py`), so the web layer makes no file-system call and a
  file that has gone answers a v1 404.
- **Logs and diagnostics (#126).** `GET /logs` (`limit`, `level`), `/diagnostics` and `/diagnostics/bundle`
  (`log_lines`) give what the legacy routes give.
- **What the new web UI still lacks** (FE-1 #105, FE-2b #132 and #133; `05-web-ui.md` 7.3, G2, G4, G12
  and G14 to G24). Not built, owned by FX-13 (after P27): a season-listing job kind, so a
  tournament's season list can be read without a sync (G15); a fetch job for event ids without their
  tournament (a `fetch` selection needs `league_id`, G16), and a job spec by target (named follows, an
  event list) for sync, fetch and refresh (G23); a real restore job, today only the dry run (G2); per-season
  counts of a tournament (G17); the filters `GET /jobs?target=` and `?origin=` (G12, G14), `/changes` by
  sport and "status regressed" with the match names (G19), and `/follows?sport=` (G18); the data-folder
  path (G20), a disk total that counts the v3 tree (G21) and the sink state (G22) in `/status`; the setting
  metadata (G4); codes for the job-log lines of the sync path (G24). The slice-registry fields the Follow
  editor needs are P27's and P28's (G9). Until FX-13 and the screens wired to it (FX-14), the old views stay
  under `/classic` and use the legacy routes, among them `POST /api/leagues/{id}/seasons/refresh` and
  `POST /api/matches/{id}/fetch`.
  Built by FX-13 (#152), with G4 already built by P27 (#134), the slice fields of G9 by P27 and P28, and the
  rest wired by FX-14b (#161), which removed the classic views (below).

As built at `b6caf2f`: P27 (#134), P28 (#140), FX-13 (#152, #153) and FX-19 (#156) changed the routes and
bodies below; each regenerated `docs/api/openapi-v1.json` and `frontend/src/api/v1/schema.ts`. Component
names the frontend imports were kept (`TournamentHit`, `FollowRecord`, `ExportRecord`, `Change`).

- **Settings and sports (P27).** `GET /settings` has `metadata[]` (G4: `type`, `section`, `description`,
  `minimum`, `exclusive_minimum`, `maximum`, `choices`, `max_length`, `restart_needed`) and `slices[]`, one
  row per registered sport with `enable`, `disable`, `source`, `source_name`, `locked` and `writable`.
  `PATCH /settings` writes `defaults.slices` and `slices.<sport>` (`{"enable", "disable"}` or null), and
  refuses a sport set in the config file or the environment with `details.locked`. Each slice of
  `/sports/{slug}` adds `selected` (the configured defaults for that sport), `group`, `owner`, `phases`,
  `keep_history` and `max_age_seconds` (G9); since P28 the list holds every registered slice that applies
  to the sport, the odds and non-match slices included.
- **Follows (P27, FX-13, FX-19).** `POST` and `PATCH /follows` take `slices` (a list, `{"include"}` or
  `{"enable", "disable"}`; `null` returns a follow to the defaults; an invalid value is 400 with
  `details.field = "slices"`). A new follow is always an `api` row, so `writable` lists every field;
  `slices`, `seasons`, `live` and `enabled` are accepted without a config file (P27 answered 400
  `unsupported` for a `leagues.txt` follow). `PATCH` takes `origin: "api"`: on a `legacy` follow it moves the
  row into the follows table, then applies the other fields; on an `api` follow it changes nothing; on a
  `config` follow it is 409 `follow_managed`; any other value is 422. A `legacy` follow's `writable` is
  `["sport", "origin"]`. `GET /follows` takes `sport` (FX-13: the follow's own sport, else the catalog's for a
  tournament; an unknown sport is 400). `DELETE /follows/{id}?delete_data=true` (FX-19) on a tournament
  follow first starts a `clear` job with `tournament_id`, which takes `maintenance` (409 when it cannot),
  then removes the follow and runs the job; the answer is `FollowRemoveResponse` with the follow and
  `clear_job`. `delete_data` on another kind is 400, on a config follow 409 `follow_managed`.
- **Search (FX-19).** `POST /tournaments/search` takes `kinds` (`tournament`, `team`, `player`; 1 to 3,
  default `["tournament"]`). `TournamentHit` adds `kind`, `country {code, name}` and a player's `team {id,
  name}`; `category` stays required and holds only `country_code` for a team or a player; `followed` is per
  kind. A 404 from SofaScore is `[]` (it was 502). FX-20 (#167): the answer of a text is kept for 10
  minutes, and a client that closes the connection before the upstream request was sent gets 499 (the
  request is not sent; not in the OpenAPI document). New `GET /catalog/suggest` (2.7, "Suggestions from
  stored data"). FX-23 (#171): `TournamentHit` has `gender` (`"M"` or `"F"`) and `national` (boolean), both
  nullable, filled for team hits of `/search/all` and null otherwise (2.7); `docs/api/openapi-v1.json` and
  `frontend/src/api/v1/schema.ts` regenerated. The search runs in the priority lane of the request budget
  (2.4).
- **Jobs (FX-13, FX-19).** `sync`: `follows` (`tournament|team|player|event:<id>`, at most 200), `only:
  "seasons"` (season lists only; G15); `fetch`: `event_ids` (at most 500; events whose tournament is unknown
  or not followed, grouped per tournament from the catalog; G16); `refresh`: `event_ids` (only those events'
  `/event`, due or not; G23). Only one of `league_id`, `selections`, `follows` and `event_ids` may be given
  (`sofascore_scraper/web/api/v1/jobs.py:499`; else 400 with `details.fields`); a field the kind does not read is 400;
  an unknown follow is 404, a disabled one is skipped with `sync_follow_skipped`. Without a target a `sync`
  downloads every enabled follow, the team, player and event follows included. `clear` takes `tournament_id`
  and `season_id` (FX-19; `scope` must be `all`; result `clear: {scopes: ["tournament"], tournament_id,
  season_id, events, event_dirs, listings, catalog_rebuilt}`). `restore` with `dry_run: false` (FX-13; G2)
  restores under `maintenance`: a data folder that is not empty needs `force: true`, else 400
  `confirmation_required` with `details.occupied` and `details.name`, checked before any job record is
  written; the result adds `catalog_rebuilt`, `verify_ok` and `verify_issues`; the job history is the
  backup's afterwards, with the restore job's own row kept (since #153 written into the staged copy that one
  backup step swaps in, `01-storage.md` 9.2). The sync result has `failed_listings` (`kind` `seasons`,
  `schedule`, `team_events` or `player_events`). Job-log lines of the sync path carry `code` and `params`
  (G24; `sync_season_list`, `sync_schedule`, `sync_follow_listing`, `sync_follow_details`, `sync_extras`,
  `fetch_zero_matches` and others). `GET /jobs` takes `origin` (repeatable; G14) and `target`
  (`tournament:`, `event:`, `team:`, `player:`; G12): a job matches when its spec names it; a job over every
  follow names none. Since FX-20 (#167) the recorded spec of a download job has the fields of the request
  body, not the service's `SyncSpec` (`sofascore_scraper/services/job_spec.py`, `record` and `body`):
  `sync` and `fetch` record `{league_id, selections: [{league_id, season_ids, match_ids}], follows, only,
  event_ids}`, where `only` is `seasons`, `events` (`ssc sync --only events`; the API calls that job a
  `fetch`) or null, and `refresh` records `{league_id, event_ids}`. A job by `event_ids` also keeps the
  per-tournament `selections` the server sorted the matches into (league 0 for an event that is not
  stored), so that `target=tournament:` still finds a download of that league's matches (G12). There is no
  `mode`. The optional `names` (`{"team:42": "Arsenal", "tournament:17": "Premier League"}`) holds the
  names of the follows and leagues when the job starts (a league `clear` records the league's name too), so
  every screen names the job without a request, also after the follow was removed or the league's data
  cleared; a body that starts the same job again does not carry it. Records written before FX-20 (`mode`,
  per-tournament selections plus `event_ids`) are converted on read: `GET /jobs` always returns the body
  shape, the scheduler compares its own earlier runs in that shape, and "Run again" of an `ssc sync --only
  events` job starts a `fetch`. The legacy job card payload still shows `mode`.
- **Read routes (FX-13, P28).** `/tournaments/{id}/seasons?include=counts` (G17) gives each season with
  `counts {events, finished, details, complete, completion_rate, missing, schedule_fetched_at_utc}` (and
  `finished_details` since FX-26, which `complete`, `missing` and `completion_rate` count over; 2.7); the
  age of the tournament's own season list (the `seasons` slice's `fetched_at`) is in no route. `/changes`
  takes `sport` and `regressed`, and `include=names` adds `home_name` and `away_name` (G19); without it a
  change is exactly the schema record, as the `changes` export writes it. `/events/{id}/odds` lists every
  slice of the registry's `odds` group (it missed `winning_odds`); `/events/{id}/odds/{key}?sub=&history=`
  gives `Odds` records, oldest snapshot first, for `odds_all` and `odds_featured` (another key is 404);
  `/seasons/{id}/slices` and `/seasons/{id}/standings?table=total|home` are new. `/events/{id}/slices` still
  shows the registry's default selection in its `not_requested` placeholders (2.7); since P30 the
  configured selection and the follows table (2.7).
- **`/status`, `/health`, `/status/check` (FX-13, FX-19).** `summary.data_dir` (G20); `summary.disk.v3`
  and `.changes`, and `total` = seasons + matches + details + datasets + v3 + changes (G21);
  `summary.last_migration` (or null); top-level `sinks {configured, ok, error, pending, served,
  max_lag_events, max_lag_seconds}` (G22; null when the Store cannot be read). `summary.tournaments[].followed`
  is true when a follow of any origin names the tournament (FX-19, `followed_tournaments`,
  `sofascore_scraper/web/api/v1/meta.py:518-523`; it read the configured leagues, so an API follow was downloaded and not
  shown as followed). All three carry `connection` (2.7: `state`, the last success and failure, reason,
  status and `last_check`), and `bridge.last_success_at` / `last_failure_at` cover every transport.
- **Exports (FX-19).** `GET /exports` lists the export jobs and, merged newest first, the files of
  `exports/` that no job wrote (`ssc export`'s): `source: "file"`, id `file:<name>`, `job_id` null, `state:
  succeeded`, `dataset` and `format` read from the name (else `unknown`); `/exports/{id}/download` takes
  `file:<name>` too, and serves the stored, readable file name (it was `sofascore-export-<job id>.<ext>`).
  `ExportJobSpec.dataset` takes `odds` and `standings` (P28).
- **`PATCH /settings` with a new `storage.data_dir`** closes this process's Stores of the old folder
  (FX-13, #120), unless this process holds one of its leases (the sink dispatcher of `serve`), which is
  logged.
- **The legacy routes** are unchanged; no web screen calls them since FX-14b removed the classic views, and
  P30 removes them. The legacy single-match fetch (`sofascore_scraper/web/api/legacy.py`) still asks only `writer_busy()`;
  its v1 face is the `fetch` job with `event_ids`, which runs under the writer lease. P30 (#186) removed them
  (6.1).

As built at `43ecdfc` (FX-26, #174; the live validation of 2026-10-08). `docs/api/openapi-v1.json` and
`frontend/src/api/v1/schema.ts` were regenerated with additions only:

- **`GET /events/{event_id}/extra`** (`getEventExtra`, `sofascore_scraper/web/api/v1/events.py:310-333`)
  answers `EventExtraResponse {data: EventExtra {note, series: {home, away} | null, venue, referee}}` from
  the stored event payload (`QueryService.event_extra` and `event_extra_of`,
  `sofascore_scraper/services/query.py:561-573`, `:1040-1064`): SofaScore's result note (cricket's "India
  beat West Indies by 8 wickets"), the series score of a play-off (`homeScore.series`, `awayScore.series`),
  the venue and the referee. None of them is in schema v1 (`04-schema-v1.md` section 7: raw is the place
  for venue and referee). Every field is null without a payload (a match known from a schedule only), and an
  unknown event is 404. It is a route of its own and not a field of `Event`, because the single-resource
  envelope is `{"data"}` only (`tests/test_openapi_snapshot.py`) and a field there would have replaced the
  `Event` component by a flattened copy. The web UI read venue and referee from the raw route and expected
  an `{"event": …}` wrapper that the raw route does not return (its unit test mocked the wrapper), so the
  overview never showed them with real data; it reads them from `/extra` now (`05-web-ui.md` 6.6).
- **`finished_details`** in `/status` (`summary.tournaments[]`) and in the season counts of
  `/tournaments/{id}/seasons?include=counts`; `completion_rate` is `complete / finished_details` (2.7).
- **Follow names.** `POST` and `PATCH /follows` accept "/" in the name of a team, player or event follow;
  a league's name still refuses it (`invalid_request` with `details.field = "name"`; 2.7).
- **`GET /exports`** and the export job's result: `events` counts the matches of a normalized export (2.7).
- **What the live validation saw of the routes.** `/status` before any request had `connection.state`
  `never_tried`; after requests that went through the browser it was `ok` with `last_success_at` set and the
  bridge `ok`, because a curl 403 that the browser then answered is not a failure (FX-19, confirmed). A
  download of 132 matches on the browser path (curl blocked for 600 s) was cancelled through
  `POST /jobs/{id}/cancel` and was `cancelled` 0.6 s later (FX-18, confirmed with a real browser).

As built at `216c2f9` (P30 #186, FX-30 #180, B1 #190, B2 #191). `docs/api/openapi-v1.json` and
`frontend/src/api/v1/schema.ts` were regenerated with their commands:

- **`GET /teams/{team_id}`** (`getTeam`, `sofascore_scraper/web/api/v1/tournaments.py:418-433`) answers
  `TeamResponse {data: TeamRecord}`: the schema v1 `Participant` fields (`id, sport, type, name, short_name,
  slug, name_code, country_code, gender, national`) and `followed`, from the catalog only (no SofaScore
  request; 2.7). 404 `not_found` with `details.team_id` until an event of the team is stored. It is among the
  reviewed read-only GETs of `tests/test_web_hardening.py`, because its first read may open the catalog.
- **`/sports` and `/sports/{slug}`**: a required boolean `individual` per sport (2.7).
- **Export filter.** `ExportFilter.team_ids` and `player_ids` (ints above 0, else 422; default `[]`), echoed
  by the records of `GET /exports` (2.7). `TournamentHit` describes `team` (null for "No team"), `gender`
  and `national` (now also filled for the stored team hits of `/catalog/suggest`).
- **`/status`**: `summary.follows[]`, `FollowSummary {follow_id, kind, entity_id, events, finished,
  finished_details, coverage, counted}` for every follow in the order of the follows list
  (`sofascore_scraper/web/api/v1/meta.py:173-195`, `:568-605`); `coverage` is `finished_details / finished` in
  percent. `connection.last_check.superseded` (2.7). `POST /status/check` stamps `checked_at_utc` with the
  moment of `last_check.at` (FX-30).
- **`GET /events?follow=kind:id`** lists the stored events of one follow (a tournament's, a team's as a
  participant, the event itself, or a player's last listed matches); the follow need not exist
  (`sofascore_scraper/web/api/v1/events.py:240-292`).
- **Jobs.** `progress.requests` and `result.requests` (2.8); the export filter above in the export job spec.
- **The access token under a root path** (B2, a security fix). With an ASGI root path (uvicorn
  `--root-path /prefix`, a proxy that strips a prefix) `scope["path"]` includes the prefix, so the security
  layer did not see `/prefix/api/v1/…` as an API path and answered it without the token while the router
  ran the route. The security middleware and the v1 error handlers now take the routed path
  (`route_path`, `sofascore_scraper/web/api/__init__.py:24-35`, Starlette's rule: the root is stripped only as
  a whole path segment; `sofascore_scraper/web/app.py:150-151`). The 3.0.0 defaults were not affected:
  `ssc serve` and the Docker image never set a root path. Checked with a proxy-like client (X-Forwarded
  headers, with and without a root path): cancel and the job event stream work, and the stream carries
  `X-Accel-Buffering: no` and `Cache-Control: no-store` (from sse-starlette), so nginx does not buffer it.
  The web UI itself cannot be served under a path prefix (it loads `/assets` and calls `/api/v1` from the
  root; `docs/deploy/README.md` says so). An aborted search is cancelled only when the proxy closes its
  upstream connection (nginx's default `proxy_ignore_client_abort off`, Caddy); that is not testable offline
  beyond the ASGI disconnect.
- **Removed** (P30): the 2.x `/api/…` routes (6.1) and the `'unsafe-eval'` Content-Security-Policy that pages
  of a frontend build without the `sofascore-csp` marker got (#43): every page except `/docs` and `/redoc`
  gets the strict policy, and the marker left `index.html` with FX-33 (#188).
- **`GET /odds/providers`** (`listOddsProviders`, B4 #192, merged after `216c2f9`;
  `sofascore_scraper/web/api/v1/meta.py:797-813` at `ff6fd7c`) answers `OddsProviderListResponse` with
  `OddsProviderInfo {id, name, country, configured}` for every entry of the built-in table
  `sports.ODDS_PROVIDERS` (`sofascore_scraper/sports.py:428`; `odds_provider(id)` and `odds_provider_label(id)`,
  "Provider N" for an unknown id). `configured` marks the id of `client.odds_provider`. The route reads only
  the table and sends no SofaScore request. The table holds an id, a name and a country, nothing else: 1 is
  bet365 (international), the odds source behind most country affiliates, and the others are the ids of the
  country listings recorded on 2026-10-09 (1528 bet365 Türkiye, …); provider 5 answers odds but is in no
  listing, so it has no name. SofaScore's provider listing (`/odds/providers/{cc}/web`) is per country and
  carries affiliate links (`defaultBetSlipLink`, `betSlipLink`, `impressionCostEncrypted`, campaign fields,
  colours); none of them is stored or given out. Why a table and not a fetch: the Store has no place for such
  reference data, and the platform never derives a country from the machine (`client.odds_country` comes
  only from the user). Open (`03-implementation-plan.md` section 19): an optional reference fetch of the
  listing, at most weekly, only when `client.odds_country` is set, through the client and the throttle and
  never part of a match download, with every link stripped and the result merged over the table; and a
  `provider_name` field in schema v1 (`Odds.provider_id` and `OddsLine.provider_id` are unchanged; an
  additive change with the generated tables of `04-schema-v1.md`). The help text of `client.odds_provider`
  (`ssc describe config`, the config JSON Schema) names `1 = bet365 (international)` and the route.

### 6.1 Existing `/api` routes

As built since P30 (#186; `216c2f9`): the 2.x `/api/…` routes are removed, as planned for 3.1 after one
release of deprecation. `sofascore_scraper/web/api/legacy.py`, the legacy response shapes of `QueryService`,
`LEGACY_SUCCESSORS` with its `Deprecation` and `Link` headers, the legacy OpenAPI snapshot
(`tests/snapshots/openapi-legacy.json`), `tests/snapshots/api/`, the API goldens of G-02 and G-04 and
`JobStore.mark_stale_running_interrupted` are gone. A 2.x path answers 404; `/health` and the web app's
paths are unchanged, and the v1 successor of each route is the right column of the table below. The rest of
this section is the record of the one release they lived through.

They stay for one release as `sofascore_scraper/web/api/legacy.py`: same paths, same response shapes (pinned by the
goldens of plan items G-02 and G-04), implemented on the same services, marked `deprecated` in OpenAPI and
answered with `Deprecation: true` and `Link: <…>; rel="successor-version"`. The web UI moves to v1 with the
frontend update.

As built (P20, PR #74). The legacy routes are still the modules of `src/web/routes/`; the adapters on the
services are P21's. The deprecation marks exist: each of the 38 legacy operations is `deprecated: true` in
OpenAPI (`GET /health` and the SPA fallback are not legacy routes and are not marked), and every legacy
response carries `Deprecation: true` and `Link: </api/v1/…>; rel="successor-version"`. Bodies, status codes
and the route list are unchanged, with the one exception of the 429 of the attempt limit (section 6).
`Deprecation: true` is the form of the Internet-Draft; RFC 9745 defines the field as a date
(`Deprecation: @<unix time>`). It was built as this section says and is not changed here. The successors are
the table `LEGACY_SUCCESSORS` (`sofascore_scraper/web/api/__init__.py:31-68` at `e0bae0c`), one row per legacy route, with
the successors of the table below; the `Link` value is the successor's path only, without a query and
without the job body.
`tests/test_openapi_snapshot.py::test_every_legacy_operation_has_a_successor_and_the_table_has_no_stale_row`
fails for a legacy operation without a row and for a row without an operation. Most successors do not exist
yet: the header says where to go once P21 has added the route.

Reading routes write derived files (RD-1, RD-2, RD-4, RD-5). The rule of #43, pinned by
`tests/test_web_hardening.py::test_no_get_route_writes_files_or_sends_requests`, is that a GET route writes
nothing. Since the readers moved to the Store, the GET routes of match detail, match lists, dashboard,
statistics, leagues, league search and season lists open the Store of the data directory, so the first such
request of a process creates `.meta/schema.json` and `.meta/catalog.db` when they are missing (also when the
configured data directory did not exist yet) and every open reconciles the catalog with the files: about
15 ms on a copy of the owner's data, 0.75 s when the catalog has to be built. The test leaves those derived
files out of its digest (`_STORE_DERIVED`) and names the routes in `GETS_THAT_MAY_WRITE_A_CACHE` with the
reason: the catalog is an index derived from the downloaded files, the caller does not choose its content,
and the operation is idempotent; data and settings files do not change. In the other direction, `GET
/api/leagues` no longer writes `config/league_sports.json` (RD-5, 2.7). The match-detail route no longer
creates `match_details/` and `match_details/processed/` by building a fetcher (RD-1).

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

The auth row is not built. `/api/v1/auth`, `/api/v1/auth/login` and `/api/v1/auth/logout` do not exist,
although the `Link` header of the three legacy routes already points there. No brief owned them when P20 was
written; the plan now gives them to P21, together with `security.AUTH_OPEN_PATHS` (`sofascore_scraper/web/security.py:123`
at `e0bae0c`), which has to name the v1 paths so that they can be reached without a token. Built by P21
(#122; section 6).

As built at `b3cb819` (P21 #127). The 38 legacy operations of `src/web/routes/{leagues,matches,scrape,
settings,data,sports,diagnostics,auth}.py` and the web download job of `src/web/fetch_job.py` are one
adapter module, `sofascore_scraper/web/api/legacy.py`; `src/web/routes/` and `src/web/fetch_job.py` are deleted. Paths,
function names (so operation ids), response models, bodies and status codes are unchanged, and the G-02 and
G-04 goldens pass unchanged; the OpenAPI snapshot of the legacy routes differs in 27 descriptions only,
because docstrings and log lines of the moved code are English. The adapters call the same services as
before (`QueryService`, `StatusService`, `services.tournaments`, the pipeline, `SyncService` under the job
manager, `BackupService`, `MaintenanceService`, `ExportService`), and the auth routes share the cookie code
of the v1 routes. Where the text above does not hold:

- **"Implemented on the same services."** The leagues routes still write `leagues.txt` through
  `ConfigManager`, not through `FollowsService`, because the legacy routes must keep writing that file also
  when a config file is in use, where `FollowsService` writes `api` rows. `POST /api/leagues/search-remote`
  keeps its own request, and `POST /api/bypass/test` keeps the browser path (the P05 note asked for the
  client; `tests/test_bypass_test.py` pins the browser path, and the client-based check is
  `POST /api/v1/status/check`).
- **`Deprecation: true`** stays the draft's form (the open question of `03-implementation-plan.md` section
  14, decided by P21): RFC 9745's form is a date, and the deprecation is tied to the 3.0.0 release, whose
  date is not known (`sofascore_scraper/web/api/__init__.py`).
- **Successors.** `LEGACY_SUCCESSORS` keeps one row per legacy route, and every successor path exists now.
  Two rows of the table name a successor whose path exists but whose job spec does not: `POST
  …/seasons/refresh` → `POST /jobs {kind:"sync", spec:{phases:["listing"]}}` (there is no listing phase in
  the spec and no season-listing job kind, G15), `POST /api/matches/{id}/fetch` → `POST /jobs {kind:"fetch",
  spec:{events:[id]}}` (a `fetch` spec is `league_id` and `selections[].match_ids`, so a match without its
  tournament cannot be fetched by id, G16). Both are FX-13's (section 6). `GET /api/export/csv` streams the
  export computed at the request and
  writes nothing (EX-1 #99); `POST /api/export/csv` is an alias for one release. `GET /api/leagues/search`
  → `/follows?q=` is built so (`q` is a case-insensitive part of the name).
- **Settings shadowing** ended: a save on the legacy Settings page replaces a value that `PATCH
  /api/v1/settings` wrote (4.3).
- **`GET /api/sports`** reports `required` per sport, as #121 (per-sport detail slices) changed it in the
  deleted `routes/sports.py`; the adapter took that change over when #127 was rebased
  (`required=s.counts_in(spec.slug)` in `_sport_model`), and `tests/snapshots/api/sports.json` is
  unchanged.
- **Import time.** Importing the web app creates neither `config/leagues.txt` nor the job store; they are
  created at the first request (`tests/test_web_deps.py`). Importing `sofascore_scraper.match_data_fetcher` still creates
  both (`03-implementation-plan.md` section 15).
- **The legacy backup route** accepts its five scopes only; `state` and `data` exist through `POST
  /api/v1/jobs` (2.7). Its download asks the Store for the path (`Store.backup.path_of`).
- Comments in `sofascore_scraper/services/sync.py`, `sofascore_scraper/store/errors.py` and some tests still name
  `src/web/fetch_job.py`, and comments in `sofascore_scraper/sports.py`, `sofascore_scraper/diagnostics.py`, `sofascore_scraper/store/state.py` and
  `sofascore_scraper/client/context.py` still name `src/web/routes/*`.

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

State at `9b03c64`. The menu's statistics count from the catalog through `StatusService` since RD-4 (2.7);
only their per-league disk sizes still walk the league directories. Its clear, restore and data-folder move
call the catalog hooks since #86. Its clear does not call `MaintenanceService.clear` yet (ST-19 left it,
because `tests/test_settings_ui_catalog.py` makes the menu's clear fail half-way by replacing the module's
own `shutil.rmtree`, which a clear inside the Store does not use): it still takes no lease and also empties
`datasets/` and `reports/`, while the web clear takes `maintenance` through the Store. ST-21 moves it onto
`MaintenanceService(store=...).clear` under the `maintenance` lease, changes that test to make the Store's
delete fail, and keeps `datasets/` and `reports/` in the menu, since they are not Store trees.

Between the PR that starts writing the new layout (ST-21) and the removal of the menu (P26), the menu's list,
statistics, backup, restore and clear items see only the legacy trees. ST-21 adds a one-line notice to those
items; no release is cut in that window without it.

State at `b3cb819`: the six points are done and the menu is gone (P26 #131). Point 3 by EX-1 (#99):
`ExportService` with the `legacy-wide-csv` profile; the menu called it until it was deleted. Point 4 by
ST-24 (#109): `BackupService.restore` for zip backups of format 1 (2.x and ST-19) and format 2, behind `ssc
backup restore`; the menu's directory-copy backups have no restore. Point 5 by P15 (#117):
`StatusService.coverage`, which only the old file report calls (2.7).

### 7.2 Deletion (plan item P26)

Deletes `src/ui/` and `src/SofaScoreUi.py`; removes `colorama` and `tqdm` from `requirements.txt` and from
`doctor.REQUIRED_MODULES` (`doctor.py:55-69`); removes `rich` if the logger no longer needs it; prunes locale
keys used only by menus (`locales/en.json`, `locales/tr.json`); updates README, installers
(`scripts/install.sh:157`) and launchers. `python main.py` without arguments prints the command list and
exits 2.

As built (P26 #131). `src/ui/` and `src/SofaScoreUi.py` are deleted with their store-boundary baselines; the
interactive branch of `main.py` and its read of `APP_EXIT_CODE` are gone, and `sofascore_scraper/match_data_fetcher.py` no
longer writes that variable. `python main.py` without arguments, or with only `--data-dir`, `--config`,
`--refresh-legacy` or `--ignore-rate-limit`, prints a short help on stderr and exits 2 (4.7). `colorama`
and `tqdm` left `requirements.txt`, `constraints.txt` and `doctor.REQUIRED_MODULES`; `tests/test_doctor.py`
checks the doctor list against `requirements.txt` in both directions. colorama is still a Windows-only
transitive dependency of click (through uvicorn) and of pytest, so on Windows it is installed unpinned, as
the constraints file says for such packages. `rich` stays (`sofascore_scraper/logger.py`). 350 locale keys that no code
used were removed (324 of the menu, 7 of the old `--web` branch, 19 progress texts the fetchers no longer
used); every remaining key must be referenced by code (`tests/test_no_terminal_menu.py`). The installers
say "command line" and point at `python -m sofascore_scraper.cli.main --help`. Left: `sofascore_scraper/store/api.py`'s
`shadow_cleared` has no product caller (ST-28 can remove it with its tests); `league_stats` and
`system_stats` of `sofascore_scraper/services/stats.py` lost their last caller; menu-only code in the fetchers
(`fetch_all_match_details`, `fetch_match_details`, `generate_file_report`, `_season_summary_files`) stays;
the docstrings of `shadow_cleared` and of `sofascore_scraper/services/context.py` still name the deleted modules. The
doctor's `config` check (a broken `sofascore.toml`, directories from the Settings) was not added: it changes
the doctor's output and goldens and needs its own item.

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

As built (P26 #131), where the table differs. The README has the table of what replaced each entry.

| Menu entry | Home at `b3cb819` |
|---|---|
| Leagues: list / add / reload / search | web Leagues & follows (the classic Leagues view until FX-14b removed it); `ssc follows list`, `ssc follows add tournament ID`, `ssc follows remove`. Reload is not needed: every command reads the configuration at its start |
| Seasons: update all / one / list | `ssc sync`, `ssc fetch tournament ID` (season lists together with schedules and details); season lists alone: `ssc sync --only seasons` and the v1 `sync` job with `only: "seasons"` (FX-13; the classic Download view until FX-14b); `GET /api/v1/tournaments/{id}/seasons`. `ssc sync --only listing` does not exist |
| Matches: one league / all / list | `ssc fetch tournament ID --season ID`, `ssc sync [--tournament ID]`; web Events; `GET /api/v1/events` |
| Match details: by id / all | `ssc fetch event ID…`, `ssc sync --only events` |
| Match details: CSV (one match / league / all) | `ssc export --event ID`, `ssc export --tournament ID`, `ssc export`; `GET /api/export/csv`; since FE-2b the web Exports screen (wide CSV and raw JSONL; since FX-14b also the normalized datasets as CSV, JSONL, Parquet and SQLite) |
| File analysis report | `ssc status --coverage`, which shows matches, details and coverage per tournament, not the slice report. The menu entry could not be reached (`MatchDataMenuHandler.show_menu` was never called); only a test reached it |
| Statistics: system / leagues / report file | `ssc status [--coverage] [--json]`; web Overview |
| Settings: API, data folder, display, language | web Settings (`/api/settings` covers every key the menu wrote); `.env`, `sofascore.toml`; `ssc config show` |
| Settings: move the data folder (the menu copied the trees, then set `DATA_DIR`) | **no replacement**: stop the app, move the folder by hand, then set `DATA_DIR` (web Settings, `.env`) or pass `--data-dir`. This is the weakest replacement; the README describes the manual move. The Store is not closed before the data folder is changed (FX-13) |
| Settings: backup / restore / clear | `ssc backup create`, `ssc backup restore NAME --yes`, `ssc data clear --all --yes`; web Backups (create, check; the restore itself is the CLI command) and Maintenance |
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
  `(LiveState, Observation) → (LiveState, [LiveEvent])`. It emits `live.status_changed`,
  `live.score_changed` and `live.stuck` (the type names carry the stream's prefix, 5.1),
  sets `provisional` by the refresh window, and de-duplicates across sources with the stream's `dedup_key`
  `(event_id, type, change_ts or state hash)`, so a transition seen by push and by poll is stored once.
  As built (P23): `reduce(state, Observation, sport)` in `sofascore_scraper/services/live/reducer.py`, checked against
  goldens recorded from `_observe` before the move (`tests/golden/live/reducer.json`, 14 scenarios with the
  gap cases). The key is `<id>:<type>:<from>><to>:<change_ts>`, with `-` when there is no `change_ts`, and
  `<id>:stuck:<start_ts>` for `stuck`; there is no state hash (5.1).
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
  As built (P23): the copy is gone (`mirror_legacy_state` was removed), and the 2.x file is imported once
  (`store.watch.import_legacy`). The service keeps its state under the sport's name, the same watcher name as
  the 2.x watcher, not under a name of its own as the plan's brief said: the `live` and `watcher:<sport>`
  leases exclude each other, and sharing the state means that switching between `--watch` and `ssc watch`
  neither emits a transition again nor loses one. Both set the key, and both retry an append that meets
  `StoreBusy` with back-off (0.5 s up to 30 s; three attempts while stopping), so a busy store no longer
  ends `--watch`. Since P30 (#186) `sofascore_scraper/watcher.py` and `WatchStateStore.append_legacy_events`
  are gone, and nothing writes `watch_events.jsonl`; the state stays under the sport's name.
- **Sinks.** P22 (PR #73) changed nothing in what was stored, and nothing hosted the dispatcher, so until
  P23 a live event left the process through `--watch`'s own stdout line and `watch_events.jsonl`, as in
  2.x, and could be read from the stream log with `ssc events`. Since P23 the events carry the settled
  `data` (5.1), `ssc watch` delivers them to the configured sinks and to `--stdout` (8.4), and `ssc events`
  reads them as before. The legacy alias still writes its 2.x stdout lines and `watch_events.jsonl` for one
  release. Superseded by P19 (PR #119): `main.py --watch` runs `ssc watch --source poll --stdout` and no
  longer writes `watch_events.jsonl` (8.4).
- **Confirmation.** A terminal status from push is emitted immediately, then confirmed by one `/event/{id}`
  request that stores the full payload. That stored event is what a later `sync` completes with post-match
  slices; the live service does not download details unless `live.detail_slices` asks for them.
  As built (P23; `_confirm_terminal`, `sofascore_scraper/services/live/supervisor.py:507-534` at `9b03c64`): a
  `status_changed` to `completed` or `decided_without_play` is confirmed by one `/event/{id}` request, or by
  none when the observation already came from the event page, and stored with `store.events.observe`. The
  design did not say when that writes a change row. As built, it follows the refresh rule of
  `sofascore_scraper/refresh.py`: a row is written only when the stored payload was already terminal and the new one
  differs (`_change_rule`, `:590-605`); a first stored payload, or an older payload of a match that had not
  finished, is not a change. When a row was written, `change.recorded` is appended with its `change_seq`.
  `live.detail_slices` and `detail_interval_seconds` are not used yet. Since the service calls `observe`,
  the defect of `EventStore._apply` that writes the change-log line last (a write killed between the payload
  and that line loses the row; `01-storage.md` 6.2) is reachable through `ssc watch`; FX-12 changes the
  order.
  As built for push (P24, PR #95): a terminal status that arrives on push is confirmed the same way. When
  the event page (served by the CDN) still shows the match unfinished, nothing is stored and the request is
  repeated every 20 s, at most 4 attempts in all (`CONFIRM_RETRY_SECONDS`, `CONFIRM_ATTEMPTS`,
  `sofascore_scraper/services/live/supervisor.py:119-120` at `b3cb819`). The live events and the `change.recorded` of a
  push observation carry the source `page` or `direct`, not `push`.
- **Arbitration and fallback.** Per sport the arbiter tracks push health (connection open, last frame, last
  ping). Push healthy: polling drops to a slow safety interval. Push silent beyond the threshold or
  disconnected: polling returns to `poll_interval` (30 s, `watcher.py:33`). After every reconnect one poll
  round runs, because a transition that happened while the connection was down is not repeated on push.
  Every switch appends `system.live_source_changed`. Events in scope that push never mentions are covered by
  polling. The fallback is polling and only polling: a failing `page` source never makes the service try
  `direct`.
  As built (P24, PR #95; P31, PR #101; `sofascore_scraper/services/live/arbiter.py:31-33`, `supervisor.py:116-120` at
  `b3cb819`): the arbiter is a pure state machine, one per sport. The design gave no numbers; these were
  chosen:

  | Constant | Value | Meaning |
  |---|---|---|
  | `PUSH_DRAIN_SECONDS` | 1 s | in push mode the service loop wakes every second, drains each sport's frames and runs a poll round only for the sports whose round is due; it adds up to 1 s to the delay |
  | `SAFETY_POLL_SECONDS` | 120 s | the poll interval while push is healthy |
  | `SILENCE_SECONDS` | 180 s | push is healthy while its connection is open and a sign of life (open, frame, PING or PONG) arrived within this time; 1.5 times the site's 120 s PING |
  | `UNKNOWN_LOOKUPS_PER_MINUTE` | 6 per sport | `/event` lookups of events that polling never saw (below) |
  | `CONFIRM_RETRY_SECONDS`, `CONFIRM_ATTEMPTS` | 20 s, 4 | the confirmation of a push finish (above) |
  | `HEARTBEAT_SECONDS` | 30 s | the heartbeat is written at most this often while push leads, and after every poll round |

  Every (re)connect, and every switch to polling, runs one poll round. A switch appends
  `system.live_source_changed` with `{sport, from, to, reason}`; `from` and `to` are `page`, `direct` or
  `poll`, and the reason is `push_connected`, `push_disconnected`, `push_silent` or `push_unavailable`.
  Seeding and merging: every event of the live list, in scope or not, and every event page seeds the last
  known state of the push source. A frame for an event in the service's state is merged and observed; a
  frame for a listed event not yet in state first builds the state from the listed version, so its
  transition is visible. A status frame for an event that polling never saw is looked up with one `/event`
  request, only when the scope has tournaments or teams, at most 6 per minute per sport, and an out-of-scope
  id is remembered. Such an event emits no transition, because its previous state is unknown (as at
  polling's first sighting); the note from #42 in P24's brief (state from the frame plus one `/event`
  request) did not say so. In push mode a poll observation that is older than the last push frame (by
  `changes.changeTimestamp`, for example a list the CDN still serves) is ignored; the design states "older never replaces newer" only for the Store, and
  poll-only mode is unchanged. `LiveReport` and `live_status()` gain `leaders` (sport to leading source),
  `last_switch`, `push_frames` and `source_switches`; the `watch --json` output has the same keys. The
  unhealthy `direct` source has no field of its own: it shows as leader `poll`, the reason
  `push_unavailable` and one log line.
- **Supervision.** The supervisor restarts a crashed source with back-off, records heartbeats and counters
  for `LiveService.status()`, pauses and backs off when the client reports `blocked` (it never "trips and
  stops"), and reloads the scope when follows change or on SIGHUP.
  As built (P23): a crashed source is built again with back-off (5 s up to 5 min). The client has no
  "blocked" report on the path the service uses (2.4), so a `RateLimitError` (429), an `APIError` with
  status 403 and a `CircuitOpenError` from `make_api_request(raise_on_failure=True)` count as blocked
  (`_default_fetch`, `sofascore_scraper/services/live/supervisor.py:613-628`): the service appends `system.blocked`, waits
  1 min doubling up to 10 min, and appends `system.recovered` after the first good round. The heartbeat and
  the counters go to `store.runtime["live"]` every round, and `live_status(store)` reads them with the
  holder of the `live` lease; `LiveService.status()` gives only this process's report. SIGHUP is not
  handled: the scope is re-read from the follows every 60 s. The service runs in its own request context
  without a job breaker (`request_context(breaker=None)`) and takes its turns from the `watch` lane of the shared budget. The scope comes from follows with `live = true` (tournament, event and
  team follows; a player follow is skipped with the warning `live_follow_skipped`) or from `--sport` with
  `--event` or `--tournament`.
  As built since FX-23 (PR #171; `48e4c4c`): at its start the service reads the match page of every event
  in its scope, but no longer of a followed single match whose stored record says *not started* and whose
  kick-off is more than 6 h away (`FOLLOW_START_WINDOW_SECONDS`,
  `sofascore_scraper/services/live/supervisor.py:121`; `_start_ids`, `:756-787`); the run logs how many it
  left out. Such a match is picked up from the live list when it starts, or read at a later start once it
  is near. A followed match without a stored record is read once (its kick-off is unknown), and events given
  on the command line are always read. The end-to-end test found `ssc watch` reading a match months ahead at
  every start (event 16483843, May 2027) and running into 403s (finding F35). There was no configured near
  window to reuse, so the 6 h are a constant, not a setting.
  As built since FX-27 (PR #175; `43ecdfc`): a scope read from follows keeps the `--sport` narrowing of the
  command across every re-read (`LiveScope.only_sports`; 4.1, finding V6). The live validation's `direct`
  run saw the 6 h window work ("Not reading 1 followed event(s) at start", event 16483843).
- **Status codes on the live path (FX-27, PR #175; finding V4).** The live validation recorded the live
  status codes of 13 sports on 2026-10-08 (football 6, 7, 20, 31, 42; basketball 13, 14, 16, 30, 31; tennis
  8 to 10; ice hockey 1, 2, 3, 30; handball 6, 7; futsal and minifootball 7; volleyball 9, 10; badminton 8,
  9; table tennis 8 to 12; cricket 22 "2nd Inning"; e-sports 1001 "First game" and 1003 "Third game"; MMA 58
  "Awaiting announcement", type `inprogress`). With `status.type` every one is classified live. Without the
  type, the code-only fallback did not know 1, 2, 3, 22, 42, 58 and 1003, and a push frame carries only the
  changed code, so the merge logged "Status could not be classified: {'code': 2}". `_LIVE_CODES` gains 1, 2,
  3, 22, 42, 58 and 1003 to 1005 (`sofascore_scraper/status.py:42-43` at `43ecdfc`; 1004 and 1005 unseen, an
  e-sports series has at most five games; 23 to 27 and 41 stay out because they were not seen). The probe of
  a bare frame code in `push_source.merge_frame` calls the new `status.classify_code()` (`:84-91`), which
  does not log; the classification of the merged event still logs a truly unknown status. The 34 (sport,
  code, description, type) rows are a fixture, `tests/fixtures/live_validation/live-statuses-2026-10-08.json`
  (no match ids). An MMA event of July 2024 still "in progress" on SofaScore became `live.stuck` as
  designed.

### 8.2 The sources

| | `page` (default) | `direct` (explicit opt-in) | `poll` (the fallback; also selectable alone) |
|---|---|---|---|
| How it works | keeps one real browser page open per watched sport and listens to the frames of the push connection that SofaScore's own page opens | a lightweight client connects to the push server itself (NATS over WebSocket) and subscribes to `sport.{sport}` | requests the live list per sport every poll interval, and event pages for dropped, near-end and stuck events |
| Credential | none handled: the connection is the page's own; the source opens no connection, sends no subscription and never reads the credential | the site's own client credential, read at runtime from the `CONNECT` frame of the connection that a sport page opens (as built, in a browser of the live profile that is closed after the read, 8.4); in memory only | none |
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

As built (P23, PR #91): `PollSource` is in `sofascore_scraper/services/live/poll_source.py` and works through a tracker
protocol; a source implements `start(tracker, event_ids)` and `tick(tracker)` and is passed to the service
as `LiveService(source_factory=...)`. Polling is the only source: `AVAILABLE_SOURCES` of the supervisor is
`("poll",)` (`sofascore_scraper/services/live/supervisor.py:55` at `9b03c64`), and `--source page`, `--source direct` and
`[live] source = "page"` (the default) fall back to polling with the warning `live_source_unavailable`. P24
and P31 add their source to `AVAILABLE_SOURCES` and to the `available` list of `describe config`
(`live_sources`), merge a partial push frame into the last event object before they build an
`Observation`, and raise `Blocked` from a fetch to pause the service.

As built (P24, PR #95; P31, PR #101; `sofascore_scraper/services/live/push_source.py` at `b3cb819`): all three sources run.
`AVAILABLE_SOURCES` is `("page", "direct", "poll")` and `PUSH_SOURCES` is `("page", "direct")`
(`supervisor.py:103-105`); `describe config` lists all three as available, and `ssc watch` without
`--source` runs `page` without a warning. A requested value other than the three (`auto`, `DIRECT`, a padded
value) becomes `poll` inside the service, never `direct`; the flag and the environment refuse `auto`.

- **The page.** `BrowserPageOpener` is a `BrowserBridge` instance of its own with the profile
  `<profile>-live` (mode 0700, decision D10). The browser's own tab waits on `robots.txt`; each watched sport
  gets one page at `HOME_URL/{sport}`. A page that lands on the captcha page has the challenge solved once
  and is reloaded. `PageSource` reopens a page that closes, crashes or cannot open with back-off (5 s up to
  5 min) while polling goes on. The live browser reports start-up failures to the process-wide
  `bridge_health`, like the main bridge, so a failing live browser can mark the bridge degraded inside the
  `watch` process.
- **What is blocked.** Not the categories of the table above, but the measured light configuration
  (`route_action`, `:482-506`): every host outside SofaScore's domains is aborted except
  `challenges.cloudflare.com`, and so are images, media and fonts; a first-party xhr, fetch or event-source
  request takes a slot from the shared budget during a 45 s load window (`LOAD_WINDOW_SECONDS`) and is
  aborted after it, except `/token/` and `/config/`, which a reconnect may need; the page itself, scripts and
  styles pass.
- **What the source reads.** A WebSocket counts as the push connection only when its first received frame
  is `INFO`, so its address is never consulted. Only `framereceived` and `close` have listeners; no
  `framesent` listener is attached, so the page source never sees the `CONNECT` frame. A streaming NATS
  reader (`NatsReader`) handles operations split across frames, several in one frame, and `HMSG`. The
  `INFO` body (the client address, server details) is dropped unread, and a `-ERR` keeps only a known NATS
  error text (`NATS_ERRORS`), otherwise `other`. Frames reach the service thread through a bounded,
  thread-safe queue (`PushFeed`); an overflow drops the oldest signals and reports a gap.
- **Not done.** No watchdog reloads a page that loads but never opens a push connection (a captcha loop,
  for example); the service keeps polling then. No test starts a real Chromium: the opener is tested against
  a fake bridge and fake Playwright pages, because the batch rules forbid a browser. Not verified against
  the real site: whether the abort rules keep the site's push code working, whether Scrapling's
  `max_pages=2` limits `context.new_page()` for several sport pages, and whether `HOME_URL/{sport}` is the
  right page for sports other than football, tennis and basketball. These are steps of the live validation
  run.
- **Checked against the real site** (the live validation, 2026-10-08, 19:56 to 20:03 Turkish time, with
  `ssc serve` running). `ssc watch --source page` on the followed live matches opened its sport pages in
  about 40 s and their push connections in about 2 minutes (polling led until then), then delivered real
  events: six score changes of a handball match, basketball points, a volleyball set, a minifootball goal
  and that match's finish (status live to completed, provisional, one finished match stored); 71 requests
  in 17 rounds. So the abort rules keep the site's push code working, several sport pages open in one
  browser, and `HOME_URL/{sport}` serves the sports it was tried on. The page source runs next to
  `ssc serve`, with its own live profile; the empty result of the end-to-end test's re-test on 2026-10-07 was
  a quiet match. Still not checked: hours of running, quiet hours, and the sports that had no live match that
  evening.
- **Closing a page or the browser (FX-27, PR #175; finding V9).** The `direct` run logged twelve
  "Task was destroyed but it is pending!" errors for patchright's `BrowserContext._on_route`. The route
  handlers of the page source and of the credential reader swallowed every error of `route.continue_()` and
  `route.abort()`; when one fails because the page or the browser is closing, Playwright does not mark the
  route handled, its task waits for ever and is collected with that error. As built: a failed
  continue or abort is handed back with `route.fallback()` (`release_route`,
  `sofascore_scraper/services/live/push_source.py:509-518` at `43ecdfc`), and before the credential page or
  the live browser closes, `stop_routing` calls `context.unroute_all(behavior="ignoreErrors")` (`:522-535`;
  `direct_source.py:261`, `push_source.py:703`). A single sport page that closes keeps the context's rules,
  because the other pages need them. `"wait"` was not chosen, because a handler waiting for a budget slot
  would hold up the close. A guard test checks that patchright still offers both calls.

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

As built (P31, PR #101; `sofascore_scraper/services/live/direct_source.py:71-96` at `b3cb819`):

- **Reading the credential.** `BrowserCredentialReader` does not use the bridge page: it starts a
  `BrowserBridge` instance of its own with the live profile `<profile>-live`, opens one sport page (with the
  page source's request rules), takes the WebSocket whose first received frame is `INFO`, reads that
  connection's address and the options of its outgoing `CONNECT` frame, and closes the page and the browser
  (up to 240 s for the read, 60 s for the `CONNECT` after the load). It never takes the bridge profile that
  `serve` or a CLI job may hold. The credential's `repr` and `str` are `***`; every string option except
  `lang`, `version`, `name` and `protocol`, and the address, are handed to `sofascore_scraper/redact.py`
  (`add_runtime_secret`), which masks them, also JSON-escaped and percent-encoded, wherever they would appear.
- **What goes on the wire.** A WebSocket client written with the standard library (no new dependency):
  certificates verified, outgoing frames masked, a 4 MB message limit. The handshake sends `Origin` (the
  page's origin), no User-Agent and no subprotocol, and imitates no browser TLS fingerprint. `CONNECT`
  carries the page's options unchanged, then one `SUB sport.<sport>` per watched sport and a `PING`; a sport
  added later gets a `SUB`, a removed one an `UNSUB`. Nothing is ever published and no subject is a
  wildcard. A server `PING` is answered with `PONG`, and the client sends its own `PING` every 120 s. One
  connection and one thread serve every watched sport, and the frames are re-serialised per sport into the
  feed of the page source, so merge, last known state, arbiter and reducer are shared. The server's `INFO`
  body is never passed on, and a `-ERR` keeps only the known NATS error text. The address is read at run
  time and is not in the code or in a log line.
- **Numbers.** The design gave none; these were chosen:

  | Constant | Value | Meaning |
  |---|---|---|
  | `AUTH_WAIT_SECONDS` | 30 s | after `CONNECT` and `PING`, the first `PONG`, or a `MSG` before it, means the credential was accepted; only then does each sport's feed report the connection open (and the arbiter run a poll round) |
  | `STALE_FACTOR` | 2.5 × the 120 s PING | a connection that sends nothing for 300 s counts as dropped |
  | `RECONNECT_FIRST_SECONDS`, `RECONNECT_MAX_SECONDS` | 2 s to 300 s | reconnect back-off after a drop, doubling |
  | `STABLE_SESSION_SECONDS` | 60 s | a session at least this long resets the failure counter |
  | `READ_RETRY_FIRST_SECONDS`, `READ_RETRY_MAX_SECONDS` | 60 s to 1800 s | back-off of reading the credential again after an auth `-ERR` or a 401 or 403 handshake |
  | `UNHEALTHY_AFTER` | 5 | after this many failed attempts in a row the source counts as unhealthy, logs it once, and the service keeps polling while the attempts go on |

  A `MSG` that arrives between `SUB` and the first `PONG` counts as acceptance, so frames sent then are not
  lost (the page source drops frames that arrive before its session is accepted). A `-ERR` lost when the
  server closes the socket is read on the next attempt with the same credential.
- **Proxies.** The design did not mention them. With a proxy configured, `direct` never connects, because
  the connection would bypass the proxy and show the real address; the source counts as unavailable and the
  service polls. Tunnelling through the proxy is not built.
- **Warnings.** The four warnings are in `watch --help`, `describe config`, `config validate --json`, the
  `config init` example and both READMEs. The P09 note has the warning logged at the first load; it is also
  logged by the service at every start with `direct` and by `ssc watch` when the flag selects `direct`, so a
  `--source direct` run logs it twice. `config show --json` has no `live_direct_source` entry in its `warnings` array: the warning reaches
  the user there only as the loader's log line on stderr (`meta.config_show` adds only
  `legacy_value_ignored`; section 11). The page of the watch unit under `docs/deploy/` is P25's.
- **Not verified against the real server**, by rule: whether it accepts this client (standard-library TLS,
  the `Origin` header, no User-Agent; the measured client was aiohttp), whether the unchanged `CONNECT`
  options hold over hours, the real credential reader in Chromium, several subjects on one connection, and
  how often the credential rotates. The tests run against an in-process fake NATS-over-WebSocket server and
  credential-free recorded frames; the `direct` step of the live validation run needs the owner's approval.
- **Checked against the real server, once** (the live validation; the owner approved one 5-minute trial in
  the orchestrator's session at 20:07 on 2026-10-08). `ssc watch --source direct` ran from 20:20:50 to
  20:25:57. The four warnings were printed as designed, by the CLI and by the service. The credential was
  read in the live profile's browser, and the push connection opened in 6 s with 13 subjects on one
  connection (the `page` source took about 2 minutes); the server accepted this client (standard-library TLS,
  `Origin`, no User-Agent). Through it came five score changes of a handball match and three of a
  basketball match, while polling filled in the status of badminton and tennis matches and the scores of
  ice-hockey, handball and basketball matches; two finished matches were stored, with 58 requests in 16
  rounds. No user, password, `CONNECT` or token text was in the stream log's NDJSON, the watch log or the
  application log. The run showed V9 (above, fixed by FX-27). Still not measured: runs of hours, how often
  the credential rotates, and whether the server keeps accepting the client.

### 8.4 Hosting

- `ssc watch` is the only host: a foreground process for systemd or a container. There is no live service
  inside `ssc serve` and no `[live] enabled` key; a library caller can run `LiveService.run` in a thread of
  its own.
- A second live service on the same data directory exits 6 (`instance_running`). Until the live service
  exists, the current watcher takes `watcher:<sport>` leases, so one process per sport keeps working. The
  lease is taken by `main.py` around `--watch` since P10 (PR #64): a second `--watch` for the same sport on
  the same directory prints the holder and exits with 6. Since P30 nothing takes `watcher:<sport>`; the
  live service still refuses to start beside a holder of that name (a 3.0 process).
- With nothing to watch, `ssc watch` exits 2 unless it runs with `--idle` (#179; 4.1): then it stays up,
  sends nothing, and starts watching when a follow is marked live, at the next re-read of the follows
  (60 s). The Compose `sofascore-watch` service and `docs/deploy/sofascore-watch.service` use it, so a live
  container without a live follow no longer restarts in a loop.
- Browser profile: Chromium allows one process per profile (`doctor.py:71-72`, `docker/entrypoint.sh:17-39`).
  The `page` source keeps a browser open permanently, so it uses its own profile directory by default
  (`<profile>-live`) to leave the bridge profile free for jobs in other processes (decision D10). The `direct`
  source uses the bridge page only to read the credential and needs no second profile. Not as built (P31,
  PR #101): `direct` reads the credential in a browser of its own with the live profile `<profile>-live` and
  closes it right after the read, so it never waits for, or takes, the bridge profile that `serve` or a CLI
  job may hold (8.3). Since FX-23 (PR #171) the bridge of any process opens a temporary sibling profile
  `<profile>-<pid>` when another live process holds the bridge profile (2.4), so the poll fallback of
  `ssc watch` and a CLI download next to `ssc serve` can solve a challenge; before, they failed after three
  403s with the profile locked (findings F34, F36). The re-test ran `ssc watch --source page` next to
  `ssc serve`: the push connection opened, but a live U16 friendly gave no event in 5 minutes, so the event
  flow next to a server is left to the live validation (`03-implementation-plan.md` section 18). The live
  validation of 2026-10-08 settled it: with `ssc serve` running, `ssc watch --source page` delivered real
  events (8.2), and `ssc export` (standings as SQLite, odds as JSONL to stdout) and `ssc backup create` ran
  next to the server too.
- The legacy `main.py --watch` alias runs `ssc watch --source poll`, so that existing cron and systemd setups
  keep polling and do not start a browser (decision D18). Not yet as built: after P23 (PR #91) the alias
  still runs `MatchWatcher` under `watcher:<sport>`, because `main.py` was not P23's file. `sofascore_scraper/watcher.py`
  is now a wrapper on the shared reducer and `PollSource`, keeps its public names and its 2.x outputs, and
  shares the state with the service (watcher name = sport), so `--watch` and `ssc watch` exclude each other
  and either one continues where the other stopped. P19, which owns `main.py` and `sofascore_scraper/cli/legacy_flags.py`
  and translates the legacy flags (4.7), completes the mapping; P30 removes `sofascore_scraper/watcher.py`,
  `WatchStateStore.append_legacy_events` and `watch_events.jsonl`. As built since P19 (PR #119):
  `main.py --watch` is translated to `ssc watch --source poll --stdout --sport S` (`sofascore_scraper/cli/legacy_flags.py`)
  and runs the live service; no product code calls `sofascore_scraper/watcher.py` any more, and nothing writes
  `watch_events.jsonl`. P30 (#186) removed the alias with the other flags (4.7) and deleted
  `sofascore_scraper/watcher.py`: `python main.py --watch …` is a usage error that names
  `ssc watch --source poll --stdout`.
- Hosting the sink dispatcher (P22 built it; P23 hosts it). `ssc watch` hosts it with P23:
  `sinks.dispatcher_for(store, settings.sinks)`, which is `None` without sinks, then `dispatcher.run(stop)`
  in a daemon thread next to the live service; on shutdown the host sets `stop`, joins the thread with a
  time-out and calls `dispatcher.close()`. As built (`sofascore_scraper/cli/commands/watch.py:132-174` at `9b03c64`):
  `register()` before the service starts, so that a new sink starts before the first event; `run(stop)` in
  the thread `watch-sinks`; `--stdout` as a lease-free follower
  (`Dispatcher(store, [StdoutSink(...)]).follow(stop)`) in a thread of its own, positioned at "now" with one
  `step()` before the service starts; on shutdown the threads are joined for at most 15 s and 5 s, and
  `drain_at_exit` (up to 10 s) replaces `close()` unless the sink thread hung (5.2). P25 hosts it in `serve`
  the same way. `ssc serve` hosts it the same way with P25. The two can run on
  one data directory: `run()` waits for the `sinks` lease, so one of them dispatches (5.2). `watch --stdout`
  needs no lease (`follow`). A sink is therefore served whenever `watch` or `serve` runs, not only while the
  live service runs.

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
  fell back to polling is visible. Not built: `ssc status` is P19's, and the live fields of `/api/v1/status`
  are P21's; both read `services.live.live_status(store)` (P23), which gives the lease holder, the source,
  the sports, the last heartbeat and whether the service is blocked. As built: since P24 `live_status()`
  also gives `leaders` and `last_switch`; the text of `ssc status` (P19) names the leading source per sport,
  its `--json` output carries the whole live report, and `/api/v1/status` (P21) has `leaders` and
  `last_switch` (`sofascore_scraper/web/api/v1/meta.py:104-107` at `b3cb819`).

### 8.6 Order of delivery

Polling first (P23): supervisor, reducer, state in the Store, sequence-numbered events, `watch`, sinks. The
`page` source and the arbiter second (P24). The `direct` source third (P31), with its warnings and its opt-in
test. Everything downstream of the reducer is identical for all three, so P24 and P31 each add one source.
The sink library and `ssc events` came before the live service (P22, PR #73); P23 hosts the dispatcher in
`watch` and settles the `data` of the live events. P23 is merged (PR #91): polling, the supervisor, the
reducer, the state in the Store, sequenced and de-duplicated events, `ssc watch` and the dispatcher host.
Left by P23: `system.live_source_changed` and the arbiter (P24), `live.detail_slices`, the SIGHUP reload,
the live fields of `ssc status` and `/api/v1/status` (P19, P21), and the alias mapping of D18 (P19).
P24 (PR #95) and P31 (PR #101) are merged: the `page` source, the arbiter and `system.live_source_changed`,
then the `direct` source with its warnings and opt-in tests; P19 and P21 delivered the live fields and the
alias mapping. Still open: `live.detail_slices` and `detail_interval_seconds`, the SIGHUP reload, a watchdog
for a page that never opens a push connection, and every check against the real site and push server (the
live validation run, done once at the end of the project; its `direct` step needs the owner's approval).
The live validation ran on 2026-10-08 (8.2, 8.3): both push sources delivered real events, `page` also next
to `ssc serve`, and FX-27 (PR #175) fixed what it found (the `--sport` filter, the route handlers at a
close, the status codes without a type). Still open after 3.0.0: `live.detail_slices` and
`detail_interval_seconds`, the SIGHUP reload, the page watchdog, and runs of hours.

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

Added to the plan after the drafts: P31 (the `direct` live source, section 8.3) and the fix items FX-1 to
FX-11 (`03-implementation-plan.md` sections 10 and 15). Three of them come from this revision: FX-9 (the
cancel check after the request semaphore, 2.4), FX-10 (redaction: the host name in the log tail of the
diagnostics bundle, sink addresses in a `ConfigError` text and sink options in `config show`; 2.8, 4.3)
and FX-11 (the SQLite files of the Store follow the umask; `01-storage.md`).

State on 2026-10-02 (`e0bae0c`): of the items with a draft id, P05, P07 to P11, P18, P20 and P22 are
merged. `03-implementation-plan.md` has the state of every item.

State at `9b03c64`: P23 is merged as well (PR #91), four of the five readers of the draft's P16 (RD-1 #80,
RD-2 #89, RD-4 #78, RD-5 #79; RD-3 is in progress), the backup and clear half of the draft's P17 (ST-19
#90), and the fix items FX-5 (#77), FX-9 (#83), FX-10 (#84) and FX-11 (#85). A new fix item, FX-12, comes
from this batch: a write keeps its change-log row (the order of `01-storage.md` 6.2) and `Store.close()`
waits for a finishing job (2.8).

State at `b3cb819`: of the items with a draft id, P12 to P15, P19, P21 (five pull requests), P24, P25, P26
and P29 are merged, and P31 (#101), so are both halves of the draft's P17 (ST-24 #109 for format 2 and
restore) and the last
reader of P16 (RD-3 #97), ST-27 (#129) of the draft's P27, and the fix items FX-7 (#103) and FX-12 (#100).
In progress: P27 and ST-28. Still to do: P28, REN-1 and P30, and the items proposed in this revision:
FX-13 (the API routes and fields the new web UI lacks, section 6, and closing the Store before the data
folder is changed or removed), FX-14 (the screens wired to them, and the classic views removed), FX-15
(cleanup of product-dead helpers and stale comments) and FX-16 (the per-sport slice proposals after the live
validation). `03-implementation-plan.md` has the state and the owner of every item.

State at `b6caf2f`: P27, ST-28, P28, FX-13, FX-15, FX-17, FX-18 and FX-19 are merged, and FX-14 was split
into FX-14a and FX-14b, both merged (`05-web-ui.md`). In progress: FX-20 (type-ahead search, one search
across kinds, job names, small UI gaps). To do: FX-21 (the P28 models in schema v1), REN-1, FX-16 after the
live validation, and P30 after 3.0.0.

State at `6f79344` (2026-10-07): FX-21 (#164), FX-22 (#165, a new fix item from the audit of the user
documents, #163), FX-20 (#167) and REN-1 (#168) are merged. To do: FX-16 after the live validation, and P30
after 3.0.0; `03-implementation-plan.md` section 18 has what remains before the tag.

State at `48e4c4c` (2026-10-08): the orchestrator's end-to-end test against the real SofaScore (1 request
per second, 2026-10-07 and 2026-10-08) is done, and its three fix items FX-24 (#170), FX-23 (#171) and
FX-25 (#172) are merged. To do: the live validation, FX-16 after it, the release, and P30 after 3.0.0;
`03-implementation-plan.md` section 18 has what remains before the tag.

State at `43ecdfc` (2026-10-08): the live validation against the real SofaScore is done (the orchestrator's,
on 2026-10-08), and FX-26 (#174), FX-27 (#175) and FX-16 (#176) are merged. In progress: FX-28 (small fixes
of the web UI). To do: the release pull request, and P30 after 3.0.0; `03-implementation-plan.md` section
18 has what remains before the tag.

State at `216c2f9` (2026-10-09): 3.0.0 is released (#179; the tag `v3.0.0` on 2026-10-08, the GitHub
Release and the GHCR image; no PyPI package). FX-28 (#177) and P30 (#186), the last items of the draft's
list, are merged, and so are the first 3.1 items: FX-29 (#181, #182, #184; the research explorer), FX-30
(#180), FX-31 (#185), FX-32 (#187), FX-33 (#188), B1 (#190), B2 (#191) and B3 (#189).
`03-implementation-plan.md` section 19 has what is done and what is still open.

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
  As built (P11, PR #69): `tests/test_jobs_cross_process.py` runs a job in a helper process and covers lease
  contention and waiting, cancel from another process, a row that is reaped only after its owner was killed,
  the heartbeat, and the web API against another process's job. A test simulates a crash with
  `store.close()`: the lease drops and the row stays running; opening a second job store no longer
  interrupts anything. There is no committed golden for the job row that a real `main.py` subprocess leaves:
  the in-process tests run `main.main()` with a fake `SyncService`, and the real runs were checked by hand
  through the G-03 harness (`--headless --update-all --league-id 17` leaves a `sync` row `completed` with 14
  events and both stream events; the 403 scenario leaves a `partial` row with the error code `blocked`).
- An import-linter test enforces the layer rules of 2.1.
  As built there is no single such test. `tests/test_layers.py` checks the Store's imports only; the other
  rules are checked next to the code they are about: `tests/test_jobs_model.py` (rule 5),
  `tests/test_client.py` (client and Store do not import each other), `tests/test_schema_v1.py` (the purity
  of `sofascore_scraper/schema`, rule 4) and the import-time test of the CLI (rule 1).
- The webhook contract is tested against a real HTTP server on 127.0.0.1 (`tests/test_webhook_contract.py`),
  the dispatcher with a fake clock, so that no back-off is really waited for (`tests/test_sinks.py`);
  nothing is sent to an address outside the machine.
- API v1 has its record: `docs/api/openapi-v1.json` with `tests/test_openapi_snapshot.py` (section 6). The
  per-route security tests of `tests/test_api_v1_errors.py` run for every v1 operation of that document, so
  a new v1 route joins them by itself.
- One test was timing-dependent for a reason in the product:
  `tests/test_throttle.py::test_stopped_bulk_job_leaves_no_queue_behind` could fail on a slow runner until
  FX-9 added the cancel check after the request semaphore (2.4). It no longer fails at random: since FX-9
  (PR #83) it makes 6 reservations in the suite's environment (`MAX_CONCURRENT=5`, seeded by
  `tests/conftest.py`) where it made 70. One timing limit is left by design: with a real budget of one
  request per second it fails if a single reservation takes a full second (measured: it passes at 800 ms per
  reservation and fails at 1,100 ms); removing that would need a fake clock, which would hide the defect.
  The four tests FX-9 added do not depend on the clock: the stop runs in the same loop turn as the first
  step of every request, and the `request_slots` fixture pins the width of the semaphore to 10.
- Tests that close a job store join the job thread first (#88): a background job thread used the store
  after its row was finished, and closing it in that window crashed the test session at random
  (`tests/test_job_manager.py::test_events_can_be_followed_until_the_job_ends`). #93 fixed the product side
  (2.8).
- The readers of RD-1 to RD-5 are tested in process against a real Store; there is no test with a writer
  in one process and a reader in another. A test that writes files while the Store is open has to reopen
  the Store before a reader sees them, because a reader that goes through the catalog touches no file and
  the shadow recorder of `tests/conftest.py` therefore does not resync; a test that edits event directories
  and reopens the same path within a minute sets `STORE_OPEN_RECONCILE_SECONDS=0` (decision S17; since P30
  the setting `storage.open_reconcile_seconds`, `SOFASCORE_STORAGE__OPEN_RECONCILE_SECONDS`).
- The live service is tested with a fake clock and a fake fetch (`tests/test_live_service.py`); the reducer
  against goldens recorded from the 2.x watcher (`tests/golden/live/reducer.json`). Nothing ran against
  SofaScore.
- The coverage floor (`pyproject.toml`, `fail_under`) applies to every PR; PRs that delete tested code
  must move its tests, not drop them. The floor was 53; it is 85 since FX-9 (PR #83; decision P3, settled at
  85 on 2026-10-02), and CI measured 91.10 % on that pull request, so a PR that deletes code has about six
  points of room.
- What batches eleven to nineteen added (`b3cb819`). The layer rule of the web face is a test since P21
  (`tests/test_layers.py`, with the ratchet list `WEB_ALSO_IMPORTS`; 2.1); the CLI face has none yet.
  Signals are tested with real subprocesses and a fake service (`tests/test_cli_signals.py`, POSIX only),
  `ssc serve` with one real server on 127.0.0.1 (POSIX), and the Docker entrypoint as a shell script with a
  fake `python` (`tests/test_packaging.py`); the image itself is built only on a release tag. The scheduler
  is tested offline with a fake clock and a fixed UTC zone, against a fake and a real job manager
  (`tests/test_scheduler.py`). Parquet round trips need `pyarrow`, which CI does not install, so they are
  skipped there (verified by hand with pyarrow 25.0.1). The G-03 CLI goldens were regenerated for the new
  exit codes and the stdout/stderr split (P19) and pin the short help that replaces the menu (P26); the
  G-02 and G-04 goldens passed unchanged through the move of the legacy routes (P21 #127). The migrate crash
  tests raise a `BaseException` at each of the seven checkpoints and reopen the Store; they do not kill a
  process. Nothing of this ran against SofaScore, and Windows and macOS ran in CI only (best-effort
  platforms; a pull request is merged on a green Linux CI plus a local full-suite run). The end-to-end check
  of `ssc sync`, `export`, `backup` and `serve` and of the Docker image against the real site is part of the
  live validation at the end of the project. Done on 2026-10-08 by the orchestrator, at 2 requests per
  second: league, team, player and single-match follows downloaded through the web UI (the Boston Celtics,
  128 matches in 8.5 minutes; Jannik Sinner, 69 matches in 6.4 minutes; the EHF Champions League, 72
  matches), a normalized events export as Parquet across 21 sports (846 rows) while a download ran, CLI
  exports and a backup next to `ssc serve`, both push sources, and the Docker image built and smoke-tested
  (4.6). Its findings became FX-26, FX-27 and FX-16 (and FX-28, in progress). The fixes themselves were
  tested offline only.
- What the 3.1 items added (`216c2f9`). The connection-state tests run on a frozen clock (FX-30:
  `bridge_health.reset(clock=)`; the Windows run of the release had failed on a whole-second comparison), and
  B2's tie rule is tested on it. The detail phase's tests use `tests/detail_fetch.py` and
  `tests/sync_fakes.py` (P30), and the reader goldens are gone with the legacy readers; the factory checks
  are in `tests/test_store_fixtures_factory.py`. The tests pin `fetch.confirm_empty_after_seconds` to 0, as
  they pin `client.rate` (B2). The research explorer has unit tests without a browser
  (`tests/test_research_explorer.py`, skipped where `fcntl` is missing) and browser tests against a local
  fake site with `.test` hosts in the offline browser job, so nothing leaves the runner;
  `tests/test_research_privacy.py` guards `research/**` against addresses and location values, and
  `tests/test_research_settings.py` checks the research scripts' settings in a clean subprocess.
  `tests/test_sport_slices.py` checks the registry against the regenerated evidence (`DECIDED`, `APPLIED`,
  `PROPOSALS`) and the phases against it. Nothing of this sent a request to SofaScore; the explorer's live
  run `lv-20261009` was the orchestrator's, at 1 request per second.

---

## 11. What changed during reconciliation

Claims that did not hold against the code, and what was changed:

1. **Raw payloads are not the bytes on the wire.** The draft promised `get_raw` "bytes as received" and a
   `/raw` route returning "SofaScore's response verbatim". The application stores the parsed response
   serialised again (`src/fsutil.py:33-36`) and the Store keeps that. Sections 2.5 and 6 now say what "raw"
   is.
2. **Open PRs.** The draft named #23 and #24 and five files. #23 is stacked on #24, a third open PR (#32) is
   stacked on both, and together they also change `sofascore_scraper/config_manager.py`, `sofascore_scraper/web/app.py`,
   `src/web/routes/api.py`, `tests/conftest.py`, `sofascore_scraper/doctor.py`, the installers and the CI workflow (1.7).
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
8. **Jobs live in `state.db`**, not in an extended `jobs.db`; `sofascore_scraper/jobs` holds no SQL. Leases are the Store's
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
20. **Job model.** `Origin` and `ErrorInfo` exist in `sofascore_scraper/jobs/model.py`; timestamps are typed; ids are uuid4
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

Changes after batches five to seven (2026-10-02, second revision of that day; references at
`e0bae0c`):

37. **Job manager entries.** The document had `submit` as the only entry. Built: `start` (lease and row),
    `run` (execute a started job) and `submit` (both), because the web starts the job in the route and runs
    it in a thread, and tests pin `run_fetch_job(job_id, payload)` (2.8; P11, PR #69).
38. **Job handle and arguments.** The document gave the handle `log(message, **fields)` only and `submit` no
    further arguments. Built: `publish(fields)` on the handle; `phases`, `payload`, `lease_purpose`,
    `on_change` and `on_log` on `submit`, `start` and `run`; `JobOutcome` and `JobNotActive`; `JobEvent` as
    printed now (2.8; P11, PR #69).
39. **What the context holds.** The document said P11 would open the Store in `build_context`, turn
    `apply_follows` into `store.follows.apply` and call `client.close()` when a context is replaced. Built:
    a `client` field, `store` and `jobs` as lazy properties, `apply_follows` unchanged, and one module-level
    health callback that is added once and writes the runtime key `bridge_health` into the data directory of
    the latest context. A web job opens the Store of its data directory best effort (2.3, 2.4; P11, PR #69).
40. **Job states as stored.** The document said state names are normalised on read and left open what is
    written. Built: success is still written as `completed` and read as `succeeded`, `partial` is the only
    new value in the column, `queued` is still never written, and `Job.progress` of another process's job is
    its last `progress` event (2.8; P11, PR #69).
41. **Liveness.** The previous revision said the unconditional sweep is still there and a second server
    interrupts the first one's job. Built: `reap_stale` marks a running row interrupted only while the
    `writer` lease is free, at open, at job creation and at every read of the history;
    `mark_stale_running_interrupted` stays as an explicit call. The heartbeat goes to the job row every 5 s,
    not to the lease (2.8; P11, PR #69).
42. **Cancel across processes.** The document described the mechanism; built with it:
    `POST /api/scrape/cancel` cancels a headless run when the server itself runs no job, the CLI run then
    prints `Program terminated by user.` and exits 0, and the progress write no longer overwrites a cancel
    request of another process (2.8, 4.5; P11, PR #69).
43. **Terminal state, ids, retention, a busy store.** The document said a breaker stop is shown as Completed
    and that ids are uuid4 until P11. Built: `partial` is stored for a breaker stop or a failed match, a job
    that finishes after a cancel request is `cancelled`, an unrecognised final status is stored as `failed`;
    the legacy API still shows `completed`. Ids are ULIDs; the newest 500 jobs and the newest 2,000 events
    per job are kept; a `state.db` locked for more than 5 s no longer fails a job (2.7, 2.8; P11, PR #69).
44. **Who takes the lease in `main.py`.** The previous revision said `main.py` takes `writer` for the
    headless download and the refresh. Built: the job manager takes it; `_data_dir_lease` still opens and
    closes the Store and serves only `--recheck-unavailable` alone and `--watch`. Exit codes are unchanged,
    and `partial` has none of its own until P19 (2.7, 2.8, 4.5, 4.6; P11, PR #69).
45. **Job events and their origin.** The document listed `job.started` and `job.finished` "with state,
    counts, error code". Built: `job.started` is {job_id, kind, origin}, `job.finished` {job_id, kind,
    state, counts, error_code}, source `job`, appended best effort. The origin carries the host name and the
    pid; the decision of 2026-10-02 keeps that for sinks and for API v1 (2.8, 5.1, 6; P11, P22, P20).
46. **Lease conflicts and the error table.** The previous revision said `conflict_from_lease` lacks
    `instance_running` "until the job manager takes over (P11)". Built: it still lacks it; a job cannot meet
    the case. The table has no row for too many failed attempts: v1 answers 401 `unauthorized` with
    `Retry-After`, the legacy routes 429 `too_many_attempts`. `invalid_request` is 422 for a rejected value
    and 400 for a refused well-formed request; a refused Host is plain-text 400 outside the model (2.6; P11,
    PR #69 and P20, PR #74).
47. **The job-log text.** The document said codes replace the translated `fetch_zero_matches` text in P11.
    Built: `JobHandle.log` accepts `code=`, the service still logs the text; P13 passes the code (2.1; P11,
    PR #69).
48. **Requests behind the semaphore after a stop.** The previous revision said they are sent only with the
    budget off or not the limit. Found: also with the budget as the limit, whenever cancelling the queue
    takes longer than one interval, because the cancel check stands before the semaphore and not after it.
    Plan item FX-9 (2.4, 3.4; PR #71). No longer true since FX-9 (PR #83): the check stands after the
    semaphore too, and a waiter is not sent after a stop on the async paths (item 62).
49. **Diagnostics bundle.** The document did not say what the bundle takes from a job row, and since P11 the
    bundle carried the host name. Built: the job columns are selected by name, the origin is {face, pid,
    same_host}, `spec_json` and `error_json` are decoded and redacted, `owner` is dropped, host names are
    masked in every job string and in the profile-lock detail. The log tail is not masked (FX-10) (2.8;
    PR #71).
50. **The schema maps rows.** The document said `sofascore_scraper/schema` maps "catalog rows and payloads". Built: rows
    and the dicts `store.derive` returns; the only payload reader for status class and score is
    `sofascore_scraper/store/derive.py`, and `sofascore_scraper/schema` names the Store only under `TYPE_CHECKING`. Its purity is
    checked in `tests/test_schema_v1.py`; `tests/test_layers.py` checks the Store only (2.1, 2.2, 2.5, 10;
    SC-1, PR #72).
51. **The envelope.** The example showed `ts` in whole seconds; it has milliseconds. The example's `data` is
    not what is stored today (the 2.x watcher's fields; P23). `change_ts` is an epoch integer, the one
    exception to the timestamp rule of 4.4. 8.1 named the live event types without the `live.` prefix. The
    sinks build the envelope with their own `Envelope.to_dict()`, not with the schema's mapper (4.4, 5.1,
    8.1; SC-1, PR #72 and P22, PR #73).
52. **Sinks as built.** The document did not say where a new sink starts (at "now"); it dropped "the head
    batch" after `max_age` (the age is per event, and all expired events go together); it had `serve` and
    `watch` both hold the `sinks` lease (`run()` waits and takes over); it had a dispatcher with `run` and
    `drain` only (also `register`, `step(until=)`, `follow`, `status`, `close`); it did not say that Store
    calls can raise plain `sqlite3.Error`. Pruning is the lease holder's, with 7 days and 1,000,000 rows as
    the dispatcher's constants until ST-24. Nothing hosts the dispatcher yet, and the SIGHUP reload is
    absent (4.6, 5, 5.2, 5.3, 8.4; P22, PR #73).
53. **Sink options and addresses.** 4.3 did not list the option keys of the sink types; the table is there
    now. `config show` and the diagnostics bundle showed the path and the query of a webhook address; they
    show the host only. A wrong-shaped `SOFASCORE_SINKS` value in a `ConfigError` text and a sink's
    `options` in `config show` are left to FX-10 (4.3; P22, PR #73).
54. **`ssc events`.** The command exists, with the options of 4.1. It reads `store.streams` itself, has its
    own `end` line, and exits 1 on a closed pipe. The pull request said it writes nothing; since ST-11 every
    open of a Store, a read-only one included, brings the catalog up to date (2.1, 4.1, 4.4; P22, PR #73).
55. **The overrides file has a writer.** 4.3 said nothing writes `overrides.json` yet. Built:
    `PATCH /api/v1/settings` writes it through `sofascore_scraper/config/overrides.py`, for the keys of the `WRITABLE`
    table, and refuses a value pinned by `sofascore.toml`, the environment or a flag. `[server] token_env`
    takes effect for the web app. A value written through v1 shadows a later save of the legacy settings
    route until P21 (4.3, 12; P20, PR #74).
56. **Decision D19.** 4.3 said the owner confirms the position of `.env`. D19 was settled on 2026-10-02 as
    built by P09, with all four points (4.3).
57. **API v1 foundation.** Section 6 had `POST /jobs` start seven kinds and `/status` report the live
    service. Built: `sync`, `fetch` and `refresh` start, and `export`, `backup`, `clear` and `rebuild`
    answer 501 `not_supported` (P21 with EX-1 and ST-24); `/status` has no live-service fields (P23) and no
    data summary (P21). New in the document: the attempt limit and its limits behind a proxy, request ids,
    the SSE details, the committed OpenAPI document, the import-time state of `routes/common.py` (6; P20,
    PR #74).
58. **Deprecation headers and the auth successors.** 6.1 said the legacy routes are answered with
    `Deprecation: true` and a `Link` header; that is built, from the table `LEGACY_SUCCESSORS`, and
    `Deprecation: true` is the draft form (RFC 9745 defines a date). 6.1 named `/api/v1/auth*` as
    successors: those routes do not exist although the header points there; the plan gives them to P21 (6.1;
    P20, PR #74).
59. **Module layout.** 2.2 listed `sinks/`, `schema/`, `web/api/v1` and the job manager as planned and had
    no `config/overrides.py`, `web/openapi.py` or `web/api/__init__.py`. They exist; the paragraph under the
    layout says what does and what does not (2.2; P11, SC-1, P22, P20).
60. **Services that take a Store.** 2.2 listed `tournaments.py` as part of `QueryService`, and 2.3 and 2.7
    showed every service built on the context, which held a Store. Built: `QueryService(store)`,
    `StatusService(store)`, `BackupService(store)` and `MaintenanceService(ctx=None, *, store=None)`;
    `tournaments.py` is a module of functions on a Store, because `SeasonFetcher` calls it. The context has
    no `store` field (`ctx.store` is a property) and nothing wires `query`, `status` or `backup` into it;
    importing the context would load the fetchers and the request layer, and `build_context` creates data
    directories (2.1, 2.2, 2.3, 2.7; RD-1, PR #80, RD-4, PR #78, RD-5, PR #79, ST-19, PR #90).
61. **Three answers for a slice body.** 2.4 said that whether a body carries data stays with the presence
    predicates. Built: `slices.slice_body_state` has three answers (data, no data, malformed), and since the
    fix commit of FX-5 a fetched malformed body is `failed` / `parse`: not counted as empty, not written,
    not reported to the breaker, requested again; it no longer aborts the sync batch or makes the
    single-match route answer 500. Two divergences of the async path stay for P13: a falsy body is `empty`
    without asking the rule, and a 404 has two reasons (1.4, 2.4; FX-5, PR #77).
62. **The cancel check after the semaphore.** 2.4 and 3.4 said that a request waiting for the request
    semaphore can be sent after a stop (FX-9). Built: one check right after the semaphore on the curl and
    the browser-first path of `_request_async`, before the breaker check and the reservation. Left: a
    request already inside the bridge is sent if its slot is due (`_wait_for_slot`, P24), the browser-first
    path drops a response that arrives during a stop, and the sync path has no check between reservation
    and send. Item 48 is history (2.4, 3.4, 10, 11; FX-9, PR #83). The first two "left" points were
    built by FX-18 (item 103).
63. **The data summary as built.** 2.7 named `StatusService.summary() -> DataSummary` without defining it.
    Built: `DataSummary`, `TournamentCounts` and `DiskUsage`, counted from the catalog under decision D21
    (a match is finished or has stored details while `FETCH_ONLY_FINISHED` is on, every event while it is
    off; a match with details that no schedule lists counts). The disk sizes are cached in the service for
    60 s and dropped when the catalog changes; `Store.info` has no cache. The setting is read from the
    environment at call time, a leftover of rule 3 until P30. The terminal menu's per-league sizes still
    walk the files (2.1, 2.7; RD-4, PR #78).
64. **Match and season lists from the catalog.** The document did not say how the legacy lists are read.
    Built: rule D21 as two disjoint queries merged in order, because `EventQuery` joins conditions with AND;
    both lists ordered by start time with ties by event id; the date filter kept as a substring of local
    ISO text; `league_folder` derived for rows without a legacy path; no fallback to the export CSV (S14).
    Season lists: the newest file of a league whatever its name, for every reader; `GET /api/leagues` no
    longer writes `league_sports.json` (2.7; RD-2, PR #89, RD-5, PR #79).
65. **A legacy GET route may write `.meta/`.** The rule of #43 is that a GET route writes nothing, and
    neither design document said otherwise. Since RD-1, RD-2, RD-4 and RD-5 the reading routes open the
    Store, so the first read creates `.meta/schema.json` and `.meta/catalog.db` and every open reconciles
    the catalog; the hardening test excludes those derived files and names the routes (2.3, 6.1; RD-4,
    PR #78, RD-5, PR #79).
66. **Is the catalog current.** The read API had no public way to ask it, and `open_store` does not fail
    when the catalog could not be synced. Built by ST-19: `Store.catalog_current`. No reader asks it yet; a
    reader that plans work refuses to plan when it is False (2.7; RD-1, PR #80, ST-19, PR #90).
67. **Backup and clear.** 2.7 gave `BackupService.create(scope, *, include_secrets, dest, handle)` and a
    `MaintenanceService` on the context. Built: `BackupService(store).create(scope, *, config_files=(),
    include_secrets=False)` with today's five scopes and no `dest` or `handle`; the caller holds `writer`.
    `MaintenanceService(store=...).clear` calls `Store.clear`, which takes `maintenance` itself;
    `DataScope` takes today's names and the Store's. The terminal menu's clear does not use it yet (ST-21)
    (2.7, 7.1; ST-19, PR #90).
68. **The end of a job.** 2.8 said the lease is released when the job ends. Built since #93: after the
    job's last store access (`job.finished` and the read-back), inside a private finishing block during
    which `rebind` and `exclusive` raise `JobRunningError`, `JobStore.close` waits up to 30 s and
    `create_running` waits. Found by #88 (a job store closed under a finishing job thread crashed the
    process). `Store.close()` does not wait yet (FX-12) (2.8, 10, 12; #88, #93).
69. **Redaction as built.** 2.8 left the log tail of the bundle unmasked, and 4.3 said a wrong-shaped
    `SOFASCORE_SINKS` value is quoted in a `ConfigError` text and that the bundle shows `[[sink]]` by host.
    Built: the log tail masks this machine's name and the host names of the listed jobs; the errors of a
    sink row name the type, never the value (the list-of-strings case was never quoted); `config show`
    masks option keys the sink's type does not define; the bundle has no `[[sink]]` and prints
    `SOFASCORE_SINKS` through `mask_value` after the same option rule (2.8, 4.3, 12; FX-10, PR #84).
70. **The live service as built.** Section 8 described the target. Built: polling only; the scope from
    follows with `live = true`, re-read every 60 s (no SIGHUP); state under the sport's name, shared with
    the alias; the key `id:type:from>to:change_ts` (`-` without a `change_ts`) and `id:stuck:start_ts`;
    a change row on confirmation only when the stored payload was already terminal; 429, 403 and an open
    breaker count as blocked; `--source page` and `direct` fall back to polling with a warning until P24
    and P31; `live_status(store)` for P19 and P21; `describe config` has `live_sources` (4.1, 4.6, 5.1, 6,
    8; P23, PR #91).
71. **The first host of the dispatcher.** Sections 5 and 8.4 said nothing hosts the dispatcher. Built:
    `ssc watch` hosts it; `--stdout` runs in its own thread, the configured sinks share one dispatcher
    thread, and the shutdown is bounded (joins of 15 s and 5 s, then a drain of up to 10 s, skipped when the
    thread hung). The live service prunes the log hourly itself. `serve` (P25) and the one-shot commands
    (P19) are still to come (4.3, 5, 5.2, 8.4, 12; P23, PR #91).
72. **The live data settled.** 5.1 and `04-schema-v1.md` gave the 2.x fields as stored and a proposal.
    Built: `live.status_changed` {from, to, change_ts, provisional, score}, `live.score_changed` {from, to
    as {home, away}, change_ts, score}, `live.stuck` {status_class, start_utc}; `change.recorded`,
    `system.blocked` and `system.recovered` have a producer; a test pins that the sinks' envelope equals the
    schema's (5.1; P23, PR #91).
73. **The legacy `--watch` alias.** 8.4 said, after D18, that the alias runs `ssc watch --source poll`.
    Built: it still runs `MatchWatcher` under `watcher:<sport>`, now a wrapper on the shared reducer and
    poll source; it no longer writes `watch_state_<sport>.json` and no longer stops when the store is busy.
    P19 completes the mapping (4.7, 8.4; P23, PR #91).
74. **`instance_running` in the job store.** 2.6 and 2.8 said `conflict_from_lease` has no
    `instance_running`. Built by P23: `InstanceRunningConflict`, a subclass of `DataOperationRunningError`,
    for a data operation that a live service or `--watch` blocks (2.6, 2.8; P23, PR #91).
75. **Coverage floor and the timing test.** Section 10 gave the floor as 53 and the throttle test as
    timing-dependent until FX-9. Built: the floor is 85 (decision P3; CI 91.10 %), and the test no longer
    fails at random; one timing limit of one second per reservation remains by design (10; FX-9, PR #83,
    #88).

Corrections after batches eleven to nineteen (2026-10-03, the fifth revision; the same list, by document,
is in `03-implementation-plan.md` section 11). Each item says what the document claimed and what is built:

76. **The export service as built.** 2.7 gave one `ExportSpec(dataset, format, schema, filter:
    EventFilter, profile)` with `json` among the formats, and `export(spec, dest: Path | BinaryIO, *,
    handle)`. Built: two specs, the legacy `ExportSpec` (the 2.x profile with `tournament_ids`, `event_ids`
    and a legacy `league_id` filter on `league_folder`) and `DatasetSpec(dataset, format, schema, filter:
    DatasetFilter)`; `export` takes either, a path or a stream, no handle; no `json` format;
    `ExportResult(rows, columns, bytes, path, events, skipped, schema_version)`. FX-7 fixed the formation
    columns and the per-league pandas pass. The version is in the result, not in the files (2.5, 2.7;
    EX-1, PR #99, FX-7, PR #103, SC-2, PR #130).
77. **The export opens the Store.** 2.3 said `--headless --csv-export` opens no Store, pinned by a P10
    test. Built: the export reads the catalog, so it creates `.meta/` in a folder that never had it; the
    test passed at EX-1 only because it patched `export_all_csv`, which has no caller in `src` since P19.
    `SyncSpec.export` is kept and not read; P13 did not remove it (2.3, 2.7; EX-1, PR #99, P19, PR #119).
78. **Backup and restore.** 2.7 had `verify(path)` and `restore(path, *, force, dry_run, handle)` and
    `create(..., dest, handle)`. Built: `verify`, `restore` and `prune` take backup names inside
    `backups/`; seven scopes; restore errors map to `not_found`, `invalid_request` and
    `confirmation_required` with `details.occupied`, no code of their own; `prune` keeps everything by
    default and nothing calls it (no `ssc backup prune`); no `dest`, `handle` or `path_of` on the service;
    `BackupInfo.format` (2.6, 2.7, 4.1; ST-24, PR #109).
79. **Migrate and the catalog.** 2.7 had `migrate(*, dry_run, delete_legacy, handle)` and
    `rebuild_catalog(*, handle)`; 4.1 `catalog rebuild|verify` with `--deep`, `--repair`. Built: `migrate`
    returns a plan or a report and takes `exact`, `tournaments`, `limit`, `purge_derived`, `confirm`,
    `should_stop`, `progress`; `rebuild_catalog(mode=)`, `verify_catalog`, `reconcile_catalog`; `catalog
    reconcile [--deep]` and `rebuild --mode`; the script's `stats` has no replacement; a dry run on a
    folder that is not a Store creates `.meta/`; the migrate CLI copies `leagues.txt` into the follows
    first (2.7, 4.1, 4.5; ST-23, PR #110).
80. **The follows service, and follows that are not downloaded.** 2.7 had `update(follow_id, patch)`,
    `remove(follow_id)` and `resolve(names)`; 4.3 said that with a config file `leagues.txt` is not read and
    `sync` processes every enabled follow. Built: `FollowsService(store, legacy, *, config_file)`,
    `update`/`remove` by `(kind, entity_id)`, no `resolve`, writes by origin; the config file wins over an
    `api` row. But `SyncService` still reads `ConfigManager.get_leagues()`, the content of `leagues.txt`:
    a tournament followed through the API or `ssc follows add` (always origin `api`), or a config
    `[[follow]]` that is not in `leagues.txt`, is watched but not downloaded until P27 (2.7, 4.3, 12; P21,
    PR #124, P19, PR #119).
81. **The CLI commands as built.** 4.1 listed `sync --follow --only listing,… --slices --force --limit`,
    `fetch tournament --season current|last:N`, `fetch event --sport --slices --force`, `export --format
    json`, `backup … restore PATH`. Built: `sync --tournament --only events --recheck-unavailable
    --include-legacy --dry-run`; `fetch tournament --season ID`; `fetch event --dry-run`; `export` with
    `--dataset`, `--profile`, `--schema`, `--format csv|jsonl|tree|parquet|sqlite`, the filters, `--event`
    and `--force`; `data clear --scope events|schedules|seasons|all`; `follows add KIND ID`; backups by name;
    `serve` without `--token`; `ssc status` without the last migration, the bridge or the sink lag (4.1;
    P19, PR #119, ST-23, PR #110, ST-24, PR #109, P25, PR #125, SC-2, PR #130).
82. **Exit codes and output.** 4.4 left the closed pipe open, and 4.5 gave today's codes until P19. Built:
    breaker 4, storage 5, partial 3 (also a fully blocked run and a refresh in which every match failed),
    held lease 6, SIGINT 130, SIGTERM 143, a cancel from another process 130, an empty CSV export 1; a
    closed pipe is 0 for a streaming command and 1 for a one-shot command; an error in a stream is a
    `{"type":"error"}` line; logs on stderr (4.4, 4.5, 1.7; P19, PR #119).
83. **Signals, the drain at exit and `serve`.** 4.6 had a SIGHUP reload on `watch` and `serve` and a
    Docker entrypoint that passes other arguments to `ssc`. Built: no SIGHUP reload anywhere; one-shot
    jobs drain the sinks at exit; `serve` hosts the dispatcher, exits 0 on a signal and 1 when it cannot
    start, keeps the allow-list rules of #43; the entrypoint passes other arguments to `python main.py`
    (the image has no `ssc` script) and supplies the loopback names when no allow-list is set anywhere;
    the image creates `/app/browser-profile-live`; the image was not built (4.6, 5; P19, PR #119, P25,
    PR #125).
84. **The attempt limit behind a proxy.** Section 6 and 12 said the limit is per remote address with no
    trusted-proxy setting, so every client behind a proxy shares one lock. Built: uvicorn trusts
    `X-Forwarded-For` from `FORWARDED_ALLOW_IPS` (default loopback), so a proxy on the same host already
    counts per client; a remote proxy needs the variable set (6, 12; P25, PR #125).
85. **`main.py` as a shim.** 4.7 said a `.txt` `--config` is accepted as a leagues file, and that
    no arguments print help. Built: the `.txt` file is still not read, only a warning is new; the flags
    are translated as listed (`--league-id` to `--tournament`, `--watch` to `watch --source poll
    --stdout`, `--web` to `serve` with the legacy defaults); no arguments print a short help with exit 2
    since P26 (4.7; P19, PR #119, P25, PR #125, P26, PR #131).
86. **The leases of data jobs, and `queued`.** (Export: changed by FX-23, item 123.) 2.8 gave export no lease, restore `maintenance` as a job,
    and `queued` to the in-app scheduler. Built: an export job holds `writer` (so it is refused while a
    download runs; `ssc export` takes none); clear and rebuild jobs hold `maintenance` through
    `create_running(lease=)`; restore is not a job, because it loads `state.db` over the job's own row
    (the API runs a dry run); migrate takes `writer` and `live`; `queued` is still never written, also
    not by the scheduler (2.8; P21, PR #126, ST-23, PR #110, P29, PR #128).
87. **The in-app scheduler.** 2.8 and 4.3 named it without rules. Built: off by default, `--scheduler`
    or `[schedule] enabled` (either), `--no-scheduler`; not with `--dev`; tasks `sync`, `fetch`,
    `refresh`, `backup` with their options, checked only when `serve` starts; `every` counts from the
    start of the server, `cron` in local time; skip and log when busy, no replay; status of this process
    only (2.8, 4.3, 6; P29, PR #128).
88. **Sinks as built.** Section 5 waited for the hosts and ST-24 and had no route (D11). Built: `serve`
    and the one-shot jobs host the dispatcher; `StreamLog.prune()` defaults to 7 days and 1,000,000 rows;
    `streams.cursors()`; a read-only `GET /api/v1/sinks` with lag, `served`, `state` and `dropped` (no
    `next_retry_at`, no disabled state); a restore writes a new `stream_id`; `config validate` builds the
    sinks (5, 5.2, 5.3; ST-24, PR #109, P21, PR #122, P19, PR #119, P25, PR #125).
89. **API v1 resources.** Section 6 listed `GET /tournaments/search?q=`, `POST /status/check` without a
    body, `?raw=1`, `has=` without values. Built: the search is POST with `{q, sport}` (#43's rule);
    the check needs `{"target": "sofascore"}` and goes through the client; no `?raw=1`; `has=details|
    missing`; raw payloads always decompressed; `/status` with `schema_version`, `live`, `summary`,
    `leases`, `capabilities`, `schedule` and `storage_error`; session routes open without a token
    (6; P21, PRs #122, #123, #124, #126).
90. **The legacy adapters.** 6.1 said the legacy routes are implemented on the same services. Built:
    `sofascore_scraper/web/api/legacy.py` with unchanged answers, but the leagues routes write `leagues.txt` through
    `ConfigManager`, the remote search keeps its own request, and `POST /api/bypass/test` keeps the
    browser path; `Deprecation: true` kept (RFC 9745's date form not used); every successor path exists,
    two successor job specs do not (G15, G16); nothing is created at import; the settings shadowing ended
    (4.3, 6.1, 2.8; P21, PR #127).
91. **What the new web UI still lacks.** FE-1 listed gaps G1 to G13; FE-2b added more. Built: G1, G3,
    G5 to G8, G10, G11, G13; partly G2 (restore check only). Not built, owner FX-13: a
    season-listing job, a fetch by event id without the tournament and a job spec by target, a real
    restore job, per-season counts, `/jobs?target=` and `?origin=`, `/changes` by sport and regression
    with names, `/follows?sport=`, the data-folder path, a disk total that counts `v3/` and the sink state
    in `/status`, setting metadata, job-log codes (6, 2.8; FE-1, PR #105, FE-2b, PRs #132, #133).
92. **The terminal menu removed.** 7.2 and 7.3 named `ssc sync --only listing`, "web UI" for CSV export
    and no row for the data-folder move. Built: the menu, colorama and tqdm and 350 locale keys are gone;
    season lists alone are fetched by the classic Download view; CSV export is `ssc export` and `GET
    /api/export/csv` (the web Exports screen since FE-2b); the file report was unreachable; moving the
    data folder has no replacement (a manual move plus `DATA_DIR`) (1, 7; P26, PR #131).
93. **The context's fetchers and the stored bridge health.** 2.3 said the context carries the three
    fetchers as fields and that `ssc status` reads the stored bridge health (P19). Built: the fetchers are
    `cached_property` members built at first access (P15), the context does not touch `NO_COLOR`; no
    command or route reads the stored `bridge_health` key (1.7, 2.1, 2.3; P15, PR #117, P19, PR #119).
94. **The slice registry as built.** 3.1 gave `counts_for_completeness` as a field with the default phases
    `{"post"}` and one completeness value per slice. Built: the field keeps its code name `required` and
    `counts_for_completeness` is a property; `phases` defaults to all three; a name such as `statistics`
    may be a key and a group; an unknown name raises `UnknownSliceName`; per-sport `not_in` and
    `optional_in` with `counts_in(sport)`; nine event slices, `esports_games` and `innings` limited to live
    and post (3.1; P12, PR #106; SP-3, PR #118; PR #121).
95. **The need rules as built.** 3.2 gave `compute_need(state, selection, policy, now)`, an unordered table
    and the inputs `missing/refresh_candidates/stale/states`. Built: no `now` (the policy carries it); the
    planner reads `states()` and decides in Python (about three times the SQL's time); the rules apply in a
    fixed order with stale first; open records need nothing, a void record is refresh-only, and the row "not
    started or void, no pre-match slice selected" is not built; `QueryService.detail_needs` and `refresh_due`
    forward to the planner (3.2; P12, PR #106; P13, PR #113; ST-27, PR #129).
96. **One fetch pipeline.** 3.3 gave `FetchPipeline(ctx, *, concurrency)` with `run(items, *, handle)`, an
    error mark for an empty `/event`, and listing items that yield `full` items. Built: `FetchPipeline(store,
    ...)` with `run(items, *, cancelled, on_result)`, one warmed session per run; an empty `/event` is a
    failed `not_found` item and nothing is written; a refill asks for missing optional slices; no finished
    filter at write time since ST-27; follow-up items only with `enqueue_events`; `SyncService` still goes
    through the fetcher faces, one session per listing run (3.3; P13, PR #113; P14, PR #116; P15, PR #117;
    ST-27, PR #129).
97. **Listing freshness.** 3.5 said listing payloads carry `complete` and `fetched_at`. Built: only round
    pages carry `complete`; event pages carry `{filtered: true}` (since ST-27 a page of the de-duplicated
    list, stored unfiltered); the season list carries nothing and the rounds list is not stored. Season lists
    are fresh for 6 h and schedules for 15 min; empty rounds are never stored (3.5; P14, PR #116; ST-27, PR
    #129).
98. **Cancel on the bridge, and the breaker's exit code.** 3.4 left the bridge's slot wait to P24 and said
    `main.py` exits 2. Built: a sync caller of the bridge checks its cancel every 0.25 s; the async bridge
    path and a check before the reservation in `_wait_for_slot` are not built; the sync request path is no
    longer used by downloads; a breaker stop exits 4 through `main.py` since P19 (3.4; P24, PR #95; P13, PR
    #113; P19, PR #119). The async bridge path's check and the check before the reservation were built
    by FX-18 (item 103).
99. **The `page` source and the arbiter as built.** 8.1 and 8.2 gave no numbers and said ads, analytics,
    images, media and fonts are blocked. Built: drain 1 s, safety poll 120 s, silence 180 s, 6 lookups per
    minute and sport, confirmation retried every 20 s up to 4 attempts, heartbeat 30 s; every non-SofaScore
    host except `challenges.cloudflare.com` aborted, first-party API requests aborted after a 45 s load window
    except `/token/` and `/config/`; a looked-up event emits no transition; an older poll observation is
    ignored in push mode; live events carry the source `page` (8.1, 8.2; P24, PR #95).
100. **The `direct` source as built.** 8.3 and 8.4 said the credential is read from the bridge page and that
    `direct` needs no second profile, and gave no numbers. Built: its own browser with the live profile,
    closed after the read; a standard-library WebSocket client with `Origin`, no User-Agent, no subprotocol;
    `CONNECT` options unchanged; auth wait 30 s, stale after 2.5 pings, reconnect 2 s to 5 min, stable session
    60 s, re-read 1 min to 30 min, unhealthy after 5; a configured proxy disables it (8.2, 8.3, 8.4; P31, PR
    #101).
101. **Live status surfaces and the alias.** 8.5 and 8.4 said `ssc status` and `/api/v1/status` lack the
    live fields and the alias still runs `MatchWatcher`. Built: `live_status()` gives `leaders` and
    `last_switch`; `ssc status` and `/api/v1/status` show them; `main.py --watch` runs `ssc watch --source poll
    --stdout` and writes no `watch_events.jsonl` (8.1, 8.4, 8.5, 8.6; P24, PR #95; P19, PR #119; P21).
102. **The selection is per event.** 3.1 said `SliceSelection` is resolved per follow, with an arrow chain
    that left the include case open, and `[defaults] slices` defaults to `["core"]`. Built: `SelectionPolicy`
    resolves it per event, the narrowest covering follow wins (event, tournament, home team, away team, and
    since FX-19 the player follow that brought the match); a follow's include is the whole selection,
    otherwise defaults, sport and follow layers in that order; an unset `[defaults] slices` is the registry's
    `default_enabled`; `required` only counts for completeness and does not lock a slice; there is no per-job
    selection (3.1; P27, PR #134; FX-19, PR #156).
103. **Cancel on the async bridge path.** 2.4 and 3.4 said that a waiter of a shared solve cannot be
    interrupted, that the async bridge path has no caller-side check and that `_wait_for_slot` has no check
    before the reservation. Built: both checks (0.25 s, 0.5 s of grace; the shared solve shielded), a
    browser-first answer that arrives during a stop is returned as on the curl path, and `ensure_ready` stays
    uncancellable; the FX-6 rule stays for a due slot (2.4, 3.4; FX-18, PR #139).
104. **Odds and non-match slices.** 3.1 said pre-match odds are refetched on each sync until kick-off,
    listed a `players` slice and an `away` standings sub, and derived the owners from the followed seasons
    and their teams. Built: a 7-day pre-match window and one read after the end; no `players` or squads slice
    and no `away` sub (not in the catalog); the owners are the plan's seasons, their teams only when a team
    slice is selected, the followed players and the seasons' sports; new groups `season`, `leaders` and
    `players`, with `standings` and `rankings` both a group and a key; `body_key` and `experimental` on
    `SliceSpec` (3.1, 3.2; P28, PR #140).
105. **The odds routes and the season routes.** 6 listed `/events/{id}/odds` as the odds. Built: it lists the
    `odds` slices; the normalized records are at `/events/{id}/odds/{key}`; `/seasons/{id}/slices` and
    `/seasons/{id}/standings` are additions; the five new models are not in schema v1's `MODELS` yet
    (`04-schema-v1.md` section 4; FX-21) (6; P28, PR #140).
106. **The sync reads the follows table.** 2.7, 4.1 and 4.3 said the downloads read `leagues.txt` through
    `ConfigManager`. Built: the enabled tournament follows of every origin, each with its season choice;
    the configured leagues only as a fallback when the Store cannot be read; named follows and a
    season-lists-only mode; the CLI's follows commands through `FollowsService` (2.7, 4.1, 4.3; FX-13, PR
    #152).
107. **Job specs and filters for the new web UI.** 6 said what the UI lacks (owner FX-13). Built: the job
    specs `follows`, `only: "seasons"` and `event_ids`, exactly one target field, `/jobs?origin=&target=`,
    `/follows?sport=`, `include=counts`, `/changes?sport=&regressed=&include=names`, the data-folder path, a
    disk total with `v3/` and `changes/`, the last migration and the sinks in `/status`, job-log codes; not
    built: `sync --only listing,non-match,refresh`, a route for a tournament's season-list age, and a recorded
    job spec equal to the request body (6, 4.1; FX-13, PR #152).
108. **A real restore.** 6 said a restore through the API is a check only (501). Built: `dry_run: false`
    restores under `maintenance`, with `confirmation_required` and `details.occupied` before any job record,
    and the restore job keeps its own row through the swap (6; FX-13, PRs #152 and #153; `01-storage.md` 9.2).
109. **Where new follows go.** 2.7 and 4.3 said that without a config file new follows are written to
    `leagues.txt`, and the "Follows (P21 part 3)" paragraph described it. Built: a new follow is always an
    `api` row; `leagues.txt` is a read-only legacy source whose rows change only `sport` and move into the
    table with `PATCH {"origin": "api"}`; no CLI command moves them (2.7, 4.3, 6; FX-19, PR #156).
110. **Search by kind.** 2.7 named `search_tournaments`. Built: `FollowsService.search(query, sport=,
    kinds=)`, one request per search (`/search/unique-tournaments/{q}` for tournaments alone, else
    `/search/all`), hits typed by kind, 404 as no hit; the route path and `TournamentHit` kept (2.7, 6;
    FX-19, PR #156).
111. **Team, player and match follows download.** 03's open question (section 14) asked whether they can be
    synced at all; FX-13 answered 400 `unsupported`. Built: lists by team and player within a window from the
    follow's seasons (`MAX_LAST_PAGES` = 5, 365 days per season step), upcoming matches by `/event` only, an
    event follow as its match; the lists are not stored; `FailedListing` kinds `team_events` and
    `player_events` with `league_id` reused; `ssc sync --follow KIND:ID`; `ssc watch` skips player follows (2.7,
    3.2, 4.1; FX-19, PR #156; owner decision of 2026-10-06).
112. **Per-league delete.** 2.7 knew only scope clears. Built: `MaintenanceService.clear_tournament` over
    `Store.purge.tournament` under `maintenance`, the `clear` job with `tournament_id` and `season_id`, and
    `DELETE /follows/{id}?delete_data=true` (2.7, 6; FX-19, PR #156; `01-storage.md` 9.3).
113. **Connection state.** `/status` had only the bridge state, which reads `ok` before any request and saw
    only the browser bridge's answers. Built: `connection {state: never_tried|ok|failed, last_check}` in
    `/status`, `/health` and `/status/check`, fed by the request layer; the bridge's times over every
    transport; per process (2.7, 6; FX-19, PR #156).
114. **Export names and the export list.** 6 said exports are written to `exports/<job id>.<ext>`, and
    `/exports` lists jobs only. Built: `<label>_<UTC date>_<id8>.<ext>` and the files of `ssc export` in the
    list with `source: "file"` (2.7, 4.1, 6; FX-19, PR #156).
115. **The scheduler's `every` and `prune-history`.** 2.8 said `every` counts from the server start and only
    `serve` checks task names. Built: `every` counts from the last run in the job history (FX-15),
    `prune-history` with `older_than` as a fifth run, off unless configured (FX-15), and `ssc config
    validate` checks the tasks (FX-13) (2.8; FX-13, PR #152; FX-15, PR #155).
116. **The odds country and the selection settings.** 4.3 listed `[client] odds_provider` as modelled and
    unused and had no country. Built: the provider is the odds sub (P28), `[client] odds_country` is an
    opt-in setting recorded as `meta.country` (FX-15), and `[defaults] slices` and `[slices.<sport>]` are
    read (P27) (3.1, 4.3; P27, PR #134; P28, PR #140; FX-15, PR #155).
117. **Dead code and the doctor.** 2.7 still described `SyncSpec.export`, `export_all_csv`, `resolve_all`
    and the menu-only fetcher methods, and 4.1 had no doctor check of the config file. Built: all removed;
    the doctor's `config` check with five codes; `config init --from-legacy` without `ConfigManager`;
    pandas is no longer installed and the `parquet` extra needs `pyarrow>=16` (2.7, 4.1; FX-15, PR #155).

Corrections after FX-20 to REN-1 (2026-10-07, the seventh revision):

118. **Search as the user types.** 2.7 and 6 described one search per button press and one kind at a time
     in the editor, and said the search across kinds as the user types was still to come. Built: the
     editor and Ctrl K search all three kinds while the user types; `GET /catalog/suggest` gives stored
     tournaments and teams without a SofaScore request; `POST /tournaments/search` keeps each answer for
     10 minutes on the server and does not send a request whose client has left (499). The brief said
     "answers are cached per query for the session": the browser keeps them for the page (a reload clears
     them), and the server's cache answers after a reload (2.7, 6; FX-20, PR #167).
119. **The recorded spec of a download job.** 6 said that the job record has no target name and records
     the service's `SyncSpec` (`mode`), not the request body. Built: the body's fields (`only`,
     `event_ids`) and `names`; a job by `event_ids` keeps its per-tournament `selections` for the `target`
     filter; older records are converted on read (6; FX-20, PR #167).
120. **The settings saved in the web app come back with a restore.** 2.7 said settings files and `.env`
     are never restored. Built: `config/overrides.json` is in `all`, `state` and `config` backups and is
     restored under the Settings file lock, with a reload and a rollback; the reload is per process (2.7;
     FX-22, PR #165).
121. **The P28 models are in schema v1.** 2.7 and 4.1 said they wait in `models.PENDING_MODELS`; FX-21
     moved them into `MODELS` and three of them into `RECORDS` (PR #164).
122. **The package name.** 2.2 said `src/` stays the import package during the build-out; REN-1 renamed it
     to `sofascore_scraper`, retargeted the console script and put `ssc` into the Docker image (2.2; PR
     #168). Paths of this document follow; see the header for line references.

Corrections after FX-23 to FX-25 (2026-10-08, the eighth revision; checked at `48e4c4c`). FX-24 (#170)
changed only the web UI (`05-web-ui.md`); the items below come from FX-23 (#171) and FX-25 (#172):

123. **An export job next to a download.** 2.8 said that an export job of API v1 takes `writer` and is
     refused while a download runs (item 86). Built: a new lease `export` (`export.lock` exclusive,
     `maintenance.lock` shared): one export at a time, next to `writer`; clear, restore, rebuild and a
     data-folder change wait for it; a held lease is 409 `data_operation_running`; the API runs the export in
     a job store of its own on the same `state.db`, and `reap_stale` treats a running export row as alive
     while the lease is held (2.8, 6; `01-storage.md` 6.1).
124. **A priority lane in the request budget.** 2.4 and 3.4 knew one queue. Built: `throttle.interactive()`
     lets the search of the web UI take the first queued slot that is not due yet; later slots move back one
     interval, the budget is never exceeded, and the moves are in the shared state file (`seq`, `bumps`,
     `prio`; `settle`, `settle_async`) (2.4).
125. **Completeness and the planner differ for a finished match.** 2.7 said the counts and the coverage
     count missing slices with `planning.missing_slice_keys`. Built: they use `unresolved_slice_keys`, which
     counts a finished match's slice whose last answer was "no data" as resolved; the planner still asks once
     more to confirm (threshold 2; finding F29 kept by design) (2.7, 3.2).
126. **The start reads of `ssc watch`.** 8.1 read every event of the scope at the start. Built: a followed
     single match that has not started and begins more than 6 h later is not read at the start
     (`FOLLOW_START_WINDOW_SECONDS`); a match without a record and events of the command line are (8.1).
127. **`config/leagues.txt` is created only without a config file.** 4.3 said constructing `ConfigManager`
     creates it. Built: only while no configuration file is in use; `add_league` creates it on demand (4.3).
128. **New job-log codes.** 2.7 named the code and parameters of the sync's log lines but not these:
     `sync_season_list_fresh`, `sync_schedule_fresh` (a fresh list is not read again) and
     `sync_extras_kinds` (the slice keys saved and not available) (2.7; `05-web-ui.md` G24).
129. **Store messages are English.** 2.6 and 6 said the Store's messages are Turkish and
     `details.store_message` carries Turkish text. Built: English, except six internal `ValueError`s; the
     issue texts of verify and the scans are still Turkish (2.6, 6).
130. **The lock file of a settings file.** 4.3 described `overrides.json.lock` as left next to the file.
     Built: `config_files.file_lock` removes it after use, with a same-inode check for a waiter; a backup
     never holds a `.lock` (2.7, 4.3).
131. **A backup records its own job.** 2.7 did not say how the job that takes a backup appears in the
     archive. Built: `create(job_id=)` writes that job as `completed` in the archive's `state.db`, and a
     restore of an older archive keeps the finished live record of a job that is running in the archive
     (2.7; `01-storage.md` 9).
132. **The browser profile of a second process.** 8.4 and section 12 said that the bridge of another
     process conflicts with the profile `ssc serve` holds. Built: `client/profile_lock.py` opens a temporary
     sibling `<profile>-<pid>` (0700, removed on close, stale siblings swept, one retry on a lock error), and
     the error that remains names the holder's pid in English (2.4, 8.4, 12).
133. **Logs, the doctor and search hits.** 2.4, 4.1, 4.6 and 6 still had Turkish log lines (the request
     layer, the `*` warning of the web app), a doctor that read the data folder from `.env` and the
     environment only, and hits without gender. Built: English logs with Scrapling's lines once; the
     doctor's data folder from the loader's layers; `TournamentHit.gender` and `national` (2.4, 2.7, 4.1,
     4.6, 6).
134. **The football extra-time score.** 2.5 said nothing of the score rule. Built: `after_extra_time` only
     for status 110, or 120 with `overtime`, `extra1` or `extra2`; `DERIVE_VERSION` 6 rebuilds a catalog of
     the old rule on its first open (2.5; `04-schema-v1.md`).

Corrections after the live validation and FX-26, FX-27 and FX-16 (2026-10-08, the ninth revision; checked at
`43ecdfc`):

135. **`fetch.only_finished` decides what a league download fetches.** 2.1 said that since ST-27 the setting
     applies at read time only. Built and kept: a league download takes its matches from
     `QueryService.detail_candidates` with the setting, so with it on only finished matches (and those with
     details or of unknown status) get their details; team, player and match follows are not affected
     (2.1; FX-26 corrected the texts, `01-storage.md` 8.1).
136. **Completeness counts finished matches only.** 2.7 counted every match with details. Built:
     `finished_details` in `SeasonCounts`, `TournamentCounts` and `/status`, `completion_rate = complete /
     finished_details`, one rule for every kind of follow; `coverage()` unchanged (2.7, 6; FX-26).
137. **`GET /events/{id}/extra`.** The route table had no place for the note, the series score, the venue
     and the referee of a match. Built: a route of its own from the stored event payload (6; FX-26).
138. **The time left of a job.** 2.8 did not say how `eta_seconds` is estimated. Built: the longest of the
     phase's pace, the last three minutes' pace and the requests still to send by cost class at the job's
     request rate (2.8; FX-26).
139. **Follow names, export `events`, search-hit sports, client exceptions.** 2.7 had one name rule for every
     kind, an export's `events` 0 for normalized datasets, a hit's sport only from the entity, and 2.6
     Turkish texts in `sofascore_scraper/exceptions.py`. Built: "/" allowed for team, player and match
     follows; `events` counts the matches; more places for the sport; English client exceptions (2.6, 2.7;
     FX-26).
140. **`ssc watch --sport` and the re-read of the follows.** 4.1 and 8.1 said `--sport` narrows the follows;
     the 60 s re-read dropped the narrowing. Built: `LiveScope.only_sports`, kept on every re-read (4.1, 8.1;
     FX-27).
141. **Closing a live browser.** 8.2 and 8.3 did not say what happens to the request rules when a page or the
     credential browser closes. Built: `route.fallback()` for a failed continue or abort, and
     `unroute_all(behavior="ignoreErrors")` before a close (8.2; FX-27).
142. **Live status codes without a type.** 8.1 relied on `status.type`; a push frame carries only the code.
     Built: codes 1 to 3, 22, 42, 58 and 1003 to 1005 are live by code, and the frame probe uses
     `classify_code()` without a warning (8.1; FX-27).
143. **A "no data" answer of an unfinished match.** 3.2 and 3.3 said only answers with a body are stored for
     an unfinished match, so such a slice showed `not_requested`. Built: an uncounted `empty` row; it
     resolves completeness only once counted (2.7, 3.2, 3.3; FX-27).
144. **`winning_odds` and `rankings`.** 3.1 had `winning_odds` experimental with any non-empty body as data,
     and `rankings` with the ATP list only. Built: `winning_odds` with its seen shape and the rule
     `sided_body_state`, not experimental; `rankings` subs `5` and `6` (3.1; FX-27).
145. **`live.score_changed` of a set sport.** 5.1 did not say that the headline score of a set sport is the
     sets won, so games and points are not events. Kept as designed and written into `ssc watch --help` (4.1,
     5.1; FX-27).
146. **The detail slices of football, basketball and tennis.** 3.1 kept them unchanged until the live
     validation. Built: `pregame_form` optional in the three, tennis without `lineups` and `incidents`, tennis
     `point_by_point` counting; the evidence is the application's own (two matrices), because the research
     tool is broken (3.1; FX-16).
147. **The live validation's results.** 2.7, 3.1, 4.6, 8.2 to 8.4, 8.6, 10 and 12 waited for checks against
     the real site: the search, the odds providers and owner slices, the Docker image, both push sources
     next to a server, the stop on the browser path, the connection state. Recorded as checked, with what is
     still open (each section).

Corrections after the 3.0.0 release, P30 and the first 3.1 items (2026-10-09, the tenth revision; checked
at `216c2f9`):

148. **The fetcher faces and the transition modules.** 1.1, 2.2, 2.3 and 3.3 described `MatchDataFetcher`,
     `SeasonFetcher` and `MatchFetcher` as the sync's seams, the context as their holder, and `utils.py`,
     `web/jobs.py`, `web/progress.py`, `watcher.py` and `web/api/legacy.py` as forwarders that stay. Built
     (P30): all deleted; the sync's details run through `services/detail_phase.py` (`DetailPhase`), its
     listings through `ListingService`; the context holds the configuration, the data folder and the client
     (1.1, 2.1 to 2.4, 2.7, 3.2, 3.3).
149. **Configuration layers.** 4.3 had a `dotenv` layer below the overrides file, the 2.x names read until
     P30 with their deprecation warning only beside a config file, and the environment bridge. Built: no
     `dotenv` layer; `.env` lines are part of the environment layer and lock the setting; `reload` does not
     re-read `.env`; a 2.x name is only a `legacy_name` warning, with or without a config file, in `config
     show`, `config validate`, the doctor and the diagnostics bundle; an invalid `SOFASCORE_*` value stops the
     start; `fetch.save_empty_rounds` is a retired setting; `storage.open_reconcile_seconds` replaces
     `STORE_OPEN_RECONCILE_SECONDS`; GNU `LANGUAGE` is not read; the bridge is gone (4.3).
150. **The flags of `main.py` and the 2.x routes.** 4.7 and 6.1 described the flags as translated and the
     routes as adapters for one release. Built (P30): a 2.x flag is a usage error that names its command
     (`cli/removed_flags.py`), a 2.x route answers 404, and the backup scopes `config`, `seasons`,
     `matches` and `match_details` are gone; the Docker entrypoint falls back to `python -m
     sofascore_scraper.cli.main` (4.1, 4.6, 4.7, 6, 6.1).
151. **The data directory at context build.** 2.3 had `build_context` create the data directory and the
     2.x subdirectories. Built (FX-33): it creates none; the Store creates the data directory on its first
     open, and one that cannot be created is a `StorageError` at the first store access (2.3).
152. **`ssc watch` with nothing to watch.** 4.1 and 8.4 had only the usage error (exit 2). Built (#179):
     `--idle`, used by the Compose service and the systemd unit, so the live container does not restart in a
     loop (4.1, 4.6, 8.4).
153. **The team record, the `individual` flag and the export's participants.** 2.7 and 6 had no team route,
     a sport without a flag for the sports whose players SofaScore lists as teams, an export filter by
     league, season, event and status only, and search hits without stored gender. Built (B1): `GET
     /teams/{team_id}`, `SportSpec.individual` in `/sports`, `team_ids` and `player_ids` in the export
     filter (players through stored line-ups; one filter combined with the others), `ssc export --team
     --player`, "No team" as no team (2.7, 4.1, 6).
154. **Counts per follow, request counters, names in the log and the confirming request.** 2.7, 2.8, 3.2
     and 6 had no counts per follow, no request counter of a job, no league or season name in the log
     parameters, and a confirming "no data" request at the very next plan. Built (B2): `summary.follows[]`,
     `GET /events?follow=`, a player's listed match ids as a runtime fact; `progress.requests` and
     `result.requests`; `league_name`, `season_name`, `season_year`; `fetch.confirm_empty_after_seconds`
     (default 60, a planning rule); the league detail phase planned by need for the time left (2.4, 2.7,
     2.8, 3.2, 6).
155. **The connection state and the access check under a root path.** 2.7 said `ok` means "the last
     request was answered", while the code compared times of second precision; 6 said nothing of an ASGI
     root path. Built: the last recorded outcome by sequence, `last_check.superseded`, one clock read for
     `checked_at_utc` (B2, FX-30); the security layer and the error handlers use the routed path, so
     `/<prefix>/api/v1` needs the token (B2; 2.7, 6).
156. **The slice rows, the phases and their evidence.** 3.1 said the evidence could not be regenerated and
     every slice had all three phases. Built (FX-29, FX-31): the repaired explorer's run `lv-20261009`, the
     evidence over both runs (answers from both, absence from the repaired run only, the state at request
     time), the rows of FX-31 and `statistics` and `point_by_point` in `live` and `post`; the four-sport
     proposal waits for more evidence (3.1).
157. **The research explorer and its scripts.** 3.1 and 12 described the explorer as broken and the
     bridge alias as present. Built: CDP Fetch interception, the liveness check, the probe, the budget per
     run, redaction before every write with a guard test (FX-29, FX-29b, FX-29c), and the 3.1 environment
     names in the research scripts with the alias deleted (FX-32) (2.2, 2.4, 3.1, 12).
158. **Single-set darts and e-sports games.** 5.1 described the headline score of set sports only. Built
     (B3): darts with `bestOfSets` 1 is `legs_won`, e-sports games are in `score.sets`, `DERIVE_VERSION` 8
     (5.1).
159. **English issue texts.** 2.6 said the issue texts of verify and of the scans were still Turkish.
     Built (B2): English, with a localized description per code in the CLI's text output (2.6).

---

## 12. Risks

- Pull requests merged after the briefs were written changed files that plan items own: #23, #24, #32 and
  #43 (web security hardening: `main.py`, `sofascore_scraper/config_manager.py`, three route modules and more). The line
  numbers in the briefs of G-03, P05, P08, P09, ST-10 and FX-6 are older than those changes. #47 (the boundary
  ratchet) makes every later PR that moves a file-system call edit a baseline file, and #41 makes every PR
  that changes a route regenerate the legacy OpenAPI snapshot (`03-implementation-plan.md` section 1).
- Existing tests pin private names of the fetchers (for example `_LEGACY_SLICE_FETCHERS` at
  `match_data_fetcher.py:106-113`, patched in `tests/test_breaker_phases.py` and `tests/test_storage_errors.py`).
  P13 must port these tests; if they are simply deleted, coverage of breaker, storage-error and cancel
  behaviour is lost exactly where the code changes most. P13 and P30 ported them (about 60 test files moved
  to `DetailPhase` through `tests/detail_fetch.py` and `tests/sync_fakes.py`), and the coverage floor held.
- P13 changes request volume and timing: it removes the triple per-match retry and fetches optional slices
  on every path. The fixed sleeps are already gone and the default rate is 5 req/s (PR #33), so the budget
  bounds the load; what remains is the burst after idle time (decision D14).
- Cross-process leases rely on OS file locks. They work on local file systems and Docker volumes on one host;
  on NFS/SMB or across hosts they may silently not exclude. Windows and macOS are best-effort.
- Chromium allows one process per profile directory. A permanently open push-listening browser plus on-demand
  bridge launches from other processes will conflict unless the `page` source uses its own profile (D10); a
  second profile means a second challenge solve and extra memory. Since FX-23 (#171) the bridge of a second
  process no longer conflicts: it opens a temporary sibling profile `<profile>-<pid>` (2.4). What is left is
  the cost: every such process solves the challenge once, starts a browser of its own (memory) and copies
  nothing of the main profile; a process killed hard leaves its sibling until the next bridge sweeps it.
- The `page` source is expensive: 1.8 to 2.6 GB of memory per watched sport page even with ads, analytics and
  images blocked (measured, `docs/push-channel/README.md`). Three sports need a host with 8 GB to spare. A
  small server runs `--source poll`, or the user chooses `direct` knowingly.
- Both push sources depend on undocumented behaviour of SofaScore's page and server (subjects, frame format,
  the 30-minute reconnect, whether `sport.{sport}` carries all events at all hours). It can change without
  notice; polling stays a complete fallback, but live latency would silently degrade from about 1 s to the poll
  interval. `system.live_source_changed` events and `ssc status` make the degradation visible. The live
  validation of 2026-10-08 found both sources working on that evening (8.2, 8.3), which says nothing about
  next month. One undocumented route had already changed: the sport-level
  `/sport/{slug}/scheduled-events/{date}` answers 404 now (the site uses
  `/sport/{slug}/scheduled-tournaments/{date}/page/1` and `/unique-tournament/{id}/scheduled-events/{date}`);
  the application does not use it (finding V1). The research tool `scripts/explore_all_sports.py` that would
  find the next such change records page traffic again since FX-29 (#181, #182; 3.1), and a run on
  2026-10-09 recorded 39 match pages. It rests on Chromium's CDP behaviour (paused requests, the
  "Invalid InterceptionId" of a replaced document), which a browser update can change; its startup probe
  and the `intercept_errors` and `step_gone` counts are the signs to read before trusting a run.
- The `direct` source uses the site's own client credential outside the site's client. The credential can
  change, the server can start refusing non-browser clients, the IP address can be blocked, and the use is a
  terms-of-use grey area. It is opt-in for those reasons; the risk the design must prevent is enabling it by
  accident (a default, a fallback, a copied config line), which is why P31 has an acceptance test for exactly
  that and why the config example carries the warning next to the key.
- Moving CLI logs from stdout to stderr and changing exit codes (breaker 2 → 4, storage 1 → 5, Ctrl+C 0 → 130)
  breaks cron jobs and scripts that parse today's output or test for specific codes. It is a major release,
  but it needs a prominent changelog entry. P19 (#119) made the change, also for the translated `main.py`
  flags, and added partial runs (3) that used to exit 0; its changelog text is in the pull request.
- Two configuration sources (declarative file and UI-edited overrides/follows) can confuse users: a value
  edited in the UI that is also pinned in the file will appear not to "stick". The UI must show the lock and
  its source.
  Until the settings API has locked fields (P20) this already happens: with a config file, the Settings
  page reports success for a pinned value and the value has no effect (4.3).
  API v1 has the locked fields since P20 (PR #74), but the Settings page still uses the legacy route, so
  the user still sees it. A second form came with P20: a value written through `PATCH /api/v1/settings`
  shadows a later save of the same key on the Settings page, until P21's legacy adapter writes through
  `sofascore_scraper.config.overrides` (4.3). P21 (#127) ended the second form. The first stays while the classic
  Settings page is in use. FX-14b (#161) removed the classic Settings page; the new Settings screen uses
  `/api/v1/settings`, which shows the lock.
- The environment bridge of 4.3 wrote settings into `os.environ` until P30 removed it; every reader takes
  the active settings now. What P30 left: an installation upgraded from 2.x whose `.env` or service file
  still sets 2.x names silently falls back to the defaults for them (for example the data folder or the
  request rate), and only the `legacy_name` warnings in the log, `ssc config show` and `ssc doctor` say so.
  A value in `.env` under a new name pins the setting above `sofascore.toml` and locks it on the Settings
  page, which a user who edits the config file may not expect; the lock shows its source.
  `sofascore_scraper/store/files.py` reads the storage settings through `sys.modules` (4.3), so a process
  that never loads the settings loader writes with the default durability.
- Webhook head-of-line blocking: a receiver that is down blocks its sink for up to `max_age` (24 h by
  default); a low `max_age` loses events. Operators need the lag shown in status.
  As built (P22) the lag is not shown anywhere yet (`ssc status` per sink is ST-24's), and one thread
  delivers for all sinks: a `deliver()` that never returns blocks the other sinks and the shutdown. The
  webhook is bounded by its time-out; stdout into a pipe nobody reads and a file on a hung network mount
  are not. Since P23 `ssc watch` runs `--stdout` in a thread of its own, so a stopped reader of stdout no
  longer holds up the configured sinks, and its shutdown does not wait for a hung sink thread beyond 15 s;
  the configured sinks still block each other. Since P21 (#122) `GET /api/v1/sinks` shows the lag of each
  sink from any process; `ssc status` shows the cursor and the last error but not the lag.
- A configured sink that nothing serves. Until P23, P25 and P19 host the dispatcher, a `[[sink]]` in the
  config file is accepted and delivers nothing, and `config validate` does not check its options (section 5,
  4.3). The item that adds the first host adds the changelog entry for sinks. Since P23 a sink is served
  while `ssc watch` runs and a bad option stops `watch` at its start; while only `serve` or a download runs,
  a sink still delivers nothing until P25 and P19, and `config validate` still does not check it. Both
  ended: `serve` hosts the dispatcher (P25 #125), the one-shot jobs drain at exit, and `config validate`
  builds the sinks (P19 #119).
- Job events carry the host name and the pid of the process that started the job, to every sink subscribed
  to `job.*` and through API v1 (decision of 2026-10-02, 2.8). An operator who forwards job events to a
  third party forwards them too. The diagnostics bundle does not carry the host name in its job rows since
  PR #71, and since FX-10 (PR #84) its log tail is masked for it too.
- The attempt limit of the access token is per remote address and in memory. Behind a reverse proxy all
  Bearer clients share one address and therefore one lock, and a restart clears it (section 6); the deploy
  documentation of P25 has to say so. P25 (#125) found it narrower: a proxy on the same host already counts
  per client through `FORWARDED_ALLOW_IPS`; a remote or containerized proxy needs that variable set, and
  setting it to `*` lets any client choose its address. `docs/deploy/README.md` says so.
- Since P11 the liveness of a job rests on the `writer` lease (2.8). Where the lease holder cannot be seen,
  a running row of another process reads as stale and the next process that opens the directory marks it
  interrupted, as 2.x swept at start.
- Legacy `/api` adapters must reproduce shapes that were by-products of pandas (NaN handling, date strings,
  column names from CSV). Without the goldens these would drift unnoticed and break the current web UI before
  it moves to v1. The adapters exist since P21 (#127) and the G-02 and G-04 goldens passed unchanged; the
  shapes now matter for the classic views under `/classic` only, until FX-14 removes them. FX-14b (#161)
  removed them; the legacy routes stayed for one release for other clients, and P30 (#186) removed them in
  3.1, so a 2.x client gets 404.
- Follows that are not downloaded. The follows service and `ssc follows add` write the follows table, but
  `ssc sync` still reads `leagues.txt` (4.3): with a config file, a tournament followed through the API or
  the CLI, or given as `[[follow]]` in the file, is watched by `ssc watch` but not downloaded. The UI and the
  CLI give no hint of it. FX-13 makes the sync read the follows table. Done by FX-13 (#152), and FX-19
  (#156) makes team, player and match follows download as well.
- An image that was never built. The Docker image of P25 (entrypoint, `CMD`, the live profile directory)
  is tested only as a script; `release.yml` builds it on a tag. The live validation run builds and
  smoke-tests it before the release. Done on 2026-10-08 from `137cabe` (4.6), and on the tag `v3.0.0`
  `release.yml` built, smoke-tested and pushed the image to GHCR; the release-candidate check found the
  restart loop of the `live` profile, which `ssc watch --idle` ended (4.6).
- Restore only from the command line. The API checks a restore but does not run it (2.8); a user of the web
  UI must run `ssc backup restore` on the server. Moving the data folder is a manual step since the menu is
  gone (7.3), and the Store is not closed before the data folder is changed or removed, which matters on
  Windows (FX-13).
  Since FX-13 (#152, #153) a real restore runs as a v1 job from the Backups screen (FX-14b), and a change
  of the data folder through `PATCH /settings` closes the old folder's Stores; an archive must still already
  be in the server's `backups/` folder (no upload; decision 15 of `05-web-ui.md`).
- One file chain was long: `sofascore_scraper/match_data_fetcher.py` had exactly one owning PR at a time through eleven PRs
  (`03-implementation-plan.md`). P30 deleted the module, so the chain ended.
- A path prefix behind a reverse proxy. Until B2 (#191) an ASGI root path let `/<prefix>/api/v1/…` through
  without the access token (6); the default deployments never set one. The fix is tested with a proxy-like
  client, not behind a real proxy, and the web UI still cannot run under a prefix.
- Slice rows on thin evidence. The registry's rows rest on one finished and one not-started page per sport
  of one repaired run, plus the 2026-10-01 answers (3.1). Rugby, floorball, volleyball and minifootball keep
  six counting slices that their lower-tier matches did not answer, so each finished match of theirs costs a
  confirmation round, until the owner decides on more evidence. A 200 with an empty body (team-streaks in
  11 sports) counts as an answer with data by the status-code rule.
- The player filter of an export reads the stored line-ups of every event inside the other filters' scope
  (2.7): on a large data folder without another filter that is one payload read per event with line-ups.
- Closing a Store under a job. Since #93 a job store does not close the state database under a finishing
  job thread, but `Store.close()` closes it directly, so a library user who closes the Store while a
  background job of `store.jobs` finishes or runs can still crash the process (2.8; FX-12). FX-12 (#100)
  closed the finishing case; closing a Store while a job is in the middle of its run is still not covered.
- A lost change row through `ssc watch`. The live confirmation calls `store.events.observe` (8.1), and a
  write killed between the stored payload and the change-log line loses the row for good, because a
  repeated write sees an identical payload (`01-storage.md` 6.2). FX-12 changes the order before ST-21
  makes the downloads write through the Store.
- asyncio pipeline with a writer thread: a Store write that blocks (the catalog mutex held by the live
  service, a slow disk) backs up the result queue; the queue is bounded, which in turn slows fetching.
