# 01 — Storage layer and catalog

Status: design, reconciled with `02-services.md` on 2026-10-01. Scope: everything under `DATA_DIR`.
Baseline: `origin/main` at `3ae2599`. Every `file:line` below refers to that commit.
Measurements were taken on the owner's real data directory (read-only). The scripts and their raw output are
not part of the repository; section 11 lists what was measured and how, and which numbers were measured a
second time during reconciliation.

The PR list that implements this document is in `03-implementation-plan.md`. Section 12 lists what changed in
this document during reconciliation and why.

Revised on 2026-10-01 after the first Store pull requests were merged (ST-02 #36, ST-03 #37, ST-04 #47,
ST-05 #45, ST-06 #46, ST-09 #44). Where they found the design wrong or silent, the text below was corrected
and says "as built"; section 12 lists the corrections. References marked `0aa73b4` are to `origin/main` at
that commit; all others are still at `3ae2599`, and main has moved since.

Revised again on 2026-10-02 after the next Store pull requests were merged (ST-07 #49, ST-10 #50, FX-4 #53,
FX-3 #55, ST-18 #59, ST-08 #61, ST-17 #62, and FX-1 #60 and P10 #64 for the leases outside the Store). The
leases, the shared SQLite module, the file modes, the stream log, the watcher state, the follows table, the
event and listing indexers, reconcile and verify are described as built; section 12 lists the corrections.
References marked `f286723` are to `origin/main` at that commit.

Revised once more on 2026-10-02 (the second revision of that day) after batches five to seven were merged
(ST-30 #68, P11 #69, FX-8 #70, ST-11 #75, and SC-1 #72 for the schema layer's side of 2.3 and 8.3). The read
API, the job store with state migration 0002, the catalog that is built and reconciled on open, the hooks of
the legacy writers, the comparison rule of the shadow check and the modes of the lock files are described as
built; section 12 lists the corrections. References marked `e0bae0c` are to `origin/main` at that commit.

Revised a fourth time on 2026-10-02 (the third revision of that day) after batches eight to ten were merged
(FX-5 #77, RD-1 #80, RD-4 #78, RD-5 #79, ST-20 #82, FX-11 #85, ST-19 #90, ST-26 #92, P23 #91 for the
watcher state and the conflict mapping, and #86, #88 and #93, three fixes outside the plan: the hooks of the
terminal menu, the job threads of the tests, and a job that keeps its lease until its last store access). The
v3 writer (`put`, `observe`, `reset_empty_markers`, `delete`, `ChangeLog.append`), the history files and
`HistoryStore`, `Store.clear`, `BackupManager` in today's zip format, the bound of the reconcile on open
(decision S17), `Store.catalog_current`, the three answers of the presence rules, the modes of the two SQLite
files, the serialized close of a connection, the readers of the statistics, the season lists and the match
detail that go through the catalog, and the end of the watcher's state file are described as built; section
12 lists the corrections. Two points are open: decision S18 (whether a delete the user asks for may remove
legacy copies, section 0) and the new fix item FX-12 (the lost change row of 6.2, and `Store.close` while a
job finishes, 3.2). References marked `9b03c64` are to `origin/main` at that commit.

Revised a fifth time on 2026-10-03 after batches eleven to nineteen were merged: the Store items RD-3 #97,
ST-22 #98, FX-12 #100, ST-21 #104, ST-25 #108, ST-24 #109, ST-23 #110 and ST-27 #129; the Store-side parts
of P13 #113 (`change.recorded` from downloads), P14 #116 (listings through `EntityStore.put`), SP-1 #112,
SP-2 #115 and SP-3 #118 (the derive version), P21 #126 (a job that holds `maintenance`) and P26 #131 (the
terminal menu is gone); and #102 (the summary CSV is no longer written), #111, #120 and #121, four pull
requests outside the plan. The writers of matches, schedules and season lists use the Store, the change
row of an interrupted write is kept, `Store.close()` waits for a finishing job, raw and row exports,
backup format 2 with restore, and `migrate` are described as built; section 12 lists the corrections,
from item 107 on. Both points the fourth revision left open are closed: decision S18 is settled as
proposed (section 0), and FX-12 is merged. References marked `b3cb819` are to `origin/main` at that
commit.

Revised a sixth time on 2026-10-06 after ST-28 #135 (the transition code removed; the boundary tests
strict), and the Store-side parts of P28 #140 (odds and non-match slices, entity history), FX-13 #152 and
#153 (a real restore through the API that keeps the running job's record; the old data folder closed after
a data-folder change; the last migration read), FX-15 #155 (the five shadow hooks removed; the first caller
of `HistoryStore.prune`; the `parquet` extra at `pyarrow>=16`) and FX-19 #156 (`Store.purge`: one
tournament's or one season's data deleted). `src/fsutil.py` is gone; the 2.x file helpers live in
`sofascore_scraper/config_files.py`, outside the Store, and the Store root exports `league_dir_name`. The strict boundary
checks, the purge, the restore step that keeps the job record and the prune caller are described as built;
section 12 lists the corrections, from item 128 on. References marked `b6caf2f` are to `origin/main` at
that commit.

Revised a seventh time on 2026-10-07 after FX-22 #165 (the settings saved on the Settings page,
`config/overrides.json`, go into a backup and come back with a restore; 9.1 and 9.2) and REN-1 #168 (the
import package `src` is now `sofascore_scraper`). Paths of the package are written with the new name
throughout. A `file:line` reference keeps the line it had at the commit its revision names (`3ae2599`,
`b3cb819`, `b6caf2f`, …), when the file was still under `src/`; REN-1 rewrote module paths in place
and kept every file's line count, so the rename itself moved no line. Paths of files removed before the rename (`src/fsutil.py`,
`src/ui/`, `src/web/routes/`, `src/web/fetch_job.py`, `src/SofaScoreUi.py`) keep the old form. References
marked `6f79344` are to `origin/main` at that commit. Section 12 lists the corrections from item 137 on.

Revised an eighth time on 2026-10-08 after the end-to-end test against the real SofaScore (2026-10-07 and
2026-10-08) and its fix items FX-24 #170, FX-23 #171 and FX-25 #172. The Store-side changes: a new lease
`export`, so that an export runs while a download runs (6.1); a backup records its own job as completed in
the archive, and a restore of an older archive keeps the finished record of a job that was running when the
archive was taken (9.1, 9.2); the catalog's derived values are at `DERIVE_VERSION` 6 (a football score rule,
`04-schema-v1.md`; 7.2); the Store's exception messages are English (7.3); the lock file of a settings file
is removed after use (2.1). References marked `48e4c4c` are to `origin/main` at that commit. Section 12
lists the corrections from item 139 on.

Revised a ninth time on 2026-10-08 after the live validation against the real SofaScore (the orchestrator's,
2026-10-08) and its fix items FX-26 #174, FX-27 #175 and FX-16 #176. No Store module changed. What changed
around the Store: for an unfinished event the pipeline now passes a body-less "no data" answer to `put`
uncounted, so the slice has a row in state `empty` (2.3, 8.1); `FETCH_ONLY_FINISHED` still decides which
matches of a league download get their details, which 8.1 called redundant (8.1); the frozen 2.x writer of
the twin tests differs from `put` for slices that do not count in a sport (2.3); `DERIVE_VERSION` stays 6
(7.2). References marked `43ecdfc` are to `origin/main` at that commit. Section 12 lists the corrections
from item 144 on.

Terms used throughout:

- **payload**: one SofaScore JSON response, stored as a file.
- **slice**: one kind of payload for one owner, for example `statistics` of event 16416346.
- **entity**: the owner of slices: an event, a tournament, a season, a team, a player, a sport.
- **v3 layout**: the new id-keyed, compressed tree under `DATA_DIR/v3/`.
- **legacy layout**: everything the 2.x code writes today (`seasons/`, `matches/`, `match_details/`, ...).
- **catalog**: `DATA_DIR/.meta/catalog.db`, a derived SQLite index.
- **state db**: `DATA_DIR/.meta/state.db`, the SQLite file for things that cannot be rebuilt from payloads.

---

## 0. Decisions this design makes (summary)

| # | Decision | Why (details in the section) |
|---|---|---|
| 1 | One payload file per slice, plus one small `manifest.json` per entity | Two processes can write different slices of one event without a read-modify-write of shared data; a refresh rewrites 1.4 KB instead of the whole event (4.3) |
| 2 | gzip level 6 from the standard library, file suffix `.json.gz` | Measured: zstd-3 is 3 % *larger* than gzip-6 on this data, zstd-9 is 6.5 % smaller; neither justifies a dependency on Python 3.10–3.13 (4.3) |
| 3 | Two SQLite files: `catalog.db` (100 % derived) and `state.db` (authoritative: follows, jobs, job events, event streams, sink cursors, watcher state) | "Delete the catalog and rebuild" stays safe; backups carry a small `state.db` instead of the whole index (3.1) |
| 4 | A legacy event that receives a new write is first copied to v3 ("promotion"); the legacy copy stays until `migrate --delete-legacy` removes it | One writer code path, an event is always wholly in one layout, nothing old is deleted outside `migrate` (5.3) |
| 5 | The catalog write transaction is the cross-process mutex for all payload writes | Works the same on Linux, macOS, Windows; no per-event lock files (6.2) |
| 6 | Catalog schema changes are handled by rebuilding; `state.db` has numbered forward-only migrations | The catalog is derived, so a rebuild is always correct, also on downgrade (7) |
| 7 | `migrate` converts and verifies but keeps the legacy copy by default; deleting is a second, explicit step | The safest default for existing data; the delete step can run later and is resumable (5.4) |
| 8 | The Store owns the leases (`.meta/locks/`) and every SQLite file; the job manager, the live service and the sink dispatcher use them through `Store.lease()` | One lock implementation for both tracks (6.1) |

Items 2, 3, 4, 7 and several smaller points are listed in the "Decisions needed" section of
`03-implementation-plan.md` because the owner's draft does not settle them.

Decision 4 and a delete, as built (ST-20 #82, ST-19 #90, ST-22 #98, ST-21 #104; decision S18, settled as
chosen on 2026-10-03). Decision 4 governs writes and maintenance: a write, a promotion, a refresh, a
rebuild, a reconcile and a repair never delete legacy data. A delete or a clear that the user asks for
removes every form of what it names, legacy copies included, because a legacy copy left behind would
bring the data back at the next reconcile, and its help text says so. `EventStore.delete` removes the v3
directory and every legacy copy of the event, each moved to `.meta/trash` first (`sofascore_scraper/store/events.py:1456`
at `b3cb819`); it still has no caller. `Store.clear` removes whole trees of both layouts: `v3/events` and
`match_details/` for `events`, since ST-22 also the v3 schedule pages and season lists for `schedules` and
`seasons` (9.3). The callers of the clear are the web clear (ST-19), the terminal menu's clear (ST-21, until
P26 removed the menu) and `ssc data clear`, whose help says that both layouts are removed (P19). `migrate
--delete-legacy` (5.4) is the other way an old copy goes, and the only one for data the user did not ask to
delete.

Decision S13 (subs are lower-case; `layout.validate_sub` rejects an upper-case sub and never folds it) has
one exception since FX-5 (#77): `legacy.schedule_sub` folds the slug of a 2.x round file name to lower case
when it derives the sub (`sofascore_scraper/store/legacy.py:396-418` at `9b03c64`). The fold applies only to reading 2.x
names; the file keeps its name on disk, and two files that fold to one sub are one page, the newest wins and
the other is reported as superseded, not merged (5.1).

---

## 1. Inventory: what is on disk today and who touches it

### 1.1 Layout under `DATA_DIR` today

`DATA_DIR` comes from the environment, default `data` (`sofascore_scraper/config_manager.py:288-295`).

| Path under `DATA_DIR` | Content | Written by | Read by |
|---|---|---|---|
| `seasons/<lid>_<name>_seasons.json` | `{"seasons": [...]}`, pretty JSON | `sofascore_scraper/season_fetcher.py:357-370` (name from `sofascore_scraper/paths.py:60-61`) | `sofascore_scraper/season_fetcher.py:289-316` (scan at start), `:423-463` (three alternate file names at `:441-445`); `src/web/routes/common.py:26-38` (bare `<lid>_seasons.json` first, otherwise the newest `<lid>_*_seasons.json` by mtime); `src/web/routes/leagues.py:173-187`; `sofascore_scraper/services/stats.py:89`, `:127-130`; `src/SofaScoreUi.py:109-113` |
| `league_seasons.csv` | pre-JSON season list; header `Liga Adı,Lig ID,Sezon ID,Sezon Adı,Sezon Yılı` | nobody on main | `sofascore_scraper/season_fetcher.py:291` |
| `matches/<lid>_<name>/<sid>_<name>/round_<n>[_<slug>].json` | round response plus a `_complete` key; **not** filtered by status | `sofascore_scraper/match_fetcher.py:452-471` | `sofascore_scraper/match_fetcher.py:403-425` (cache with a 6 h TTL on file mtime, `:393`); `sofascore_scraper/season_fetcher.py:177-183`; `src/ui/match_ui.py:441-450` |
| `matches/.../events_<last\|next>_<page>.json` | de-duplicated page, filtered to finished events when `FETCH_ONLY_FINISHED` | `sofascore_scraper/match_fetcher.py:362-378` | same as above |
| `matches/<lid>_<name>/<sid>_<name>_summary.json` and `.csv` | derived per-season list. Ten columns (`sofascore_scraper/match_fetcher.py:547-548`); scores are `homeScore.current` with default 0 (`:570-571`); `match_date` is naive local time (`:572`); `tournament` is `tournament.name` (`:574`) | `sofascore_scraper/match_fetcher.py:514-595` (paths from `sofascore_scraper/paths.py:72-77`) | `sofascore_scraper/match_data_fetcher.py:1855-1884`, `:1953-2007`, `:1822-1852`; `src/web/routes/matches.py:165-216`, `:284-303`, `:316-337`; `sofascore_scraper/services/stats.py:73-77`, `:93-95`, `:131`. Old `*_matches.csv` is still accepted (`sofascore_scraper/match_data_fetcher.py:1871-1875`). The first version wrote `round_<n>_matches.csv` (13 other columns) and `round_<n>_full.json` inside the season directory; `_season_summary_files` and the statistics accept both places (`sofascore_scraper/match_data_fetcher.py:1790-1797` at `0aa73b4`), while `/api/matches` and missing-details read neither nested file |
| `match_details/<lid>_<name>/season_<name>/<eid>/<slice>.json` | one pretty JSON per slice; `basic.json` is `/event/{id}` | `sofascore_scraper/match_data_fetcher.py:1215-1225` (directory computed at `:1151-1192`); refresh rewrites `basic.json` at `:888` | `sofascore_scraper/match_data_fetcher.py:124-181`, `:534-557`, `:1535-1730`; `src/web/routes/matches.py:219-242`, `:340-347`, `:361-393`; `sofascore_scraper/services/stats.py:80-81`, `:132`; `sofascore_scraper/web/league_sports.py:77-89` |
| `.../<eid>/observation.json` | `{observed_at_utc, change_ts[, status_regressed]}` | `sofascore_scraper/match_data_fetcher.py:897`, record built at `sofascore_scraper/status.py:260-266` | `sofascore_scraper/match_data_fetcher.py:551-554`, used by `sofascore_scraper/refresh.py:63-81` |
| `.../<eid>/_unavailable.json` | `{slice: count}` | `sofascore_scraper/match_data_fetcher.py:710-711`, `:788-793` | `:639-645`, `:804-811` |
| `.../<eid>/_slice_status.json` | `{slice: {empty: {count, at}, error: {reason, status, at, count}}}` | `sofascore_scraper/match_data_fetcher.py:712-717`, `:794-799` | `:647-656` |
| `match_details/_no_tournament/<sport>/<eid>/` | events without `uniqueTournament.id` | `sofascore_scraper/match_data_fetcher.py:58`, `:1165-1172` | the same walkers |
| `match_details/<eid>/` (flat) and `<eid>/<eid>.json` (one combined file) | two older forms, still read | combined file is still *updated* by refresh at `:889-894` | `sofascore_scraper/match_data_fetcher.py:154-157`, `:170-171`, `:538-542`; `src/web/routes/matches.py:375-378` |
| `match_details/processed/all_matches_<ts>.csv`, `<league>_<ts>.csv`, `match_files_stats.json`, `match_files_report.csv` | derived exports and reports | `sofascore_scraper/match_data_fetcher.py:518` (directory), `:1535-1820`, `:2323-2482` | `src/web/routes/data.py:185-221`, `src/web/routes/matches.py:134-150` |
| `score_changes.jsonl` | one line per post-finish change | `sofascore_scraper/match_data_fetcher.py:911-915` (name at `sofascore_scraper/refresh.py:21`) | no reader in `sofascore_scraper/` |
| `watch_events.jsonl`, `watch_state_<sport>.json` | watcher event stream and last known state. Since ST-18 (PR #59) the state is authoritative in the `watch_state` table of `state.db`: the state file is read once, on the first run, and afterwards written as a copy; each event is also appended to the `live` stream. Since P23 (#91) the state file is no longer written: `--watch` and `ssc watch` keep the state only in the table, under the sport as watcher name, and `watch_events.jsonl` is still written by `--watch` until P30 | `sofascore_scraper/watcher.py:45-46`, `:177-179`, `:212-222`; since PR #59 through `store.watch` (`sofascore_scraper/store/watch.py`) | `sofascore_scraper/watcher.py:204-210`; since PR #59 only the one-time import |
| `.meta/jobs.db` | SQLite job history, rollback journal mode, `user_version` 0 (checked on the local file). Since ST-09 (PR #44) the history is in `.meta/state.db`; `jobs.db` is imported once and left in place | `sofascore_scraper/web/jobs.py:18-21`, `:100-127`, `:193-223`, `:322-362` | `sofascore_scraper/web/jobs.py:388-402`; the diagnostics bundle (`sofascore_scraper/diagnostics.py:389-398` at `0aa73b4`, read-only; `state.db` first, `jobs.db` for a directory 3.x has not opened) |
| `backups/backup_<scope>_<ts>.zip` | zip of `seasons/`, `matches/`, `match_details/` and optionally the league config and `.env`; does **not** contain `.meta`, `score_changes.jsonl` or watcher files | `src/web/routes/data.py:103-149` | `src/web/routes/data.py:251-260` |
| `datasets/`, `reports/<kind>_report_<ts>.json` | terminal-UI leftovers | `src/SofaScoreUi.py:79-83`, `src/ui/stats_ui.py:157-161` | `sofascore_scraper/services/stats.py:139` (size only) |

Also touching `DATA_DIR`:

- Clear: `src/web/routes/data.py:152-182` removes the three data trees with `shutil.rmtree`; it leaves `.meta`,
  `score_changes.jsonl`, watcher files and `backups/`. Since ST-19 (#90) the web clear and the web backup go
  through `Store.clear` and `BackupManager` (2.3, 9.1, 9.3).
- `DATA_DIR` change: `src/web/routes/settings.py:134-167` re-points the job database (`sofascore_scraper/web/jobs.py:170-191`);
  files are not moved.
- Terminal UI: backup, restore, clear and "move data directory" with `shutil` (`src/ui/settings_ui.py:297-457`,
  `:459-549`, `:551-669`); match listing from `matches/` (`src/ui/match_ui.py:347-450`). Since #86 its clear,
  restore and move are followed by the hook `shadow_cleared` (3.5); they still delete and copy with `shutil`.
  Since ST-21 (#104) its clear goes through `Store.clear`; P26 (#131) removed the menu with `src/ui/` and
  `src/SofaScoreUi.py`, so none of these paths exists at `b3cb819`.
- `DATA_DIR` change, as built at `b3cb819`: `sofascore_scraper/web/api/v1/settings.py:334` and the legacy route
  (`sofascore_scraper/web/api/legacy.py:1051`) rebind the job store; neither closes the Store that `open_store` keeps for
  the old directory for the life of the process (2.3). On Windows a data directory can be removed or moved
  only after `Store.close()` (#120, 6.4). As built since FX-13 (#152) the v1 route closes the old
  directory's Stores after the rebind (`close_data_dir`, `sofascore_scraper/web/api/v1/settings.py:489` and `:492` at
  `b6caf2f`), unless this process holds one of that directory's leases; the legacy route still only
  rebinds, until P30 removes it.
- `scripts/migrate_match_details.py:33-73` renames id-less league directories in place.
- `sofascore_scraper/doctor.py` probes that the directory is writable.
- The web backend builds the terminal-UI object to reach the fetchers (`src/web/fetch_job.py:19`, `:109`;
  `src/web/routes/data.py:193-197`; `src/web/routes/leagues.py:90-93`).
- Research scripts write `data/research_index` through a hard-coded path, not `DATA_DIR`
  (`scripts/discover_status_taxonomy.py:38`). The local directory also holds `finish_lag/`. The Store ignores
  top-level directories it does not know.

Related state outside `DATA_DIR` (not moved by this design, listed for completeness): `config/leagues.txt`
(`sofascore_scraper/config_manager.py:471-554`), `config/league_sports.json` (`sofascore_scraper/web/league_sports.py:37-74`), the
request-budget files in `~/.cache/sofascore_scraper/throttle` (`sofascore_scraper/throttle.py:103-107`), the browser profile
(`sofascore_scraper/paths.py:28-35`).

### 1.2 The tree walkers and where their rules differ

Each of these walks the tree with its own rule. The catalog replaces all of them.

| Walker | Rule it applies |
|---|---|
| `MatchDataFetcher._find_match_path` (`sofascore_scraper/match_data_fetcher.py:124-159`) | any `<league>/<season>/<id>/basic.json`, then flat `<id>/basic.json` |
| `_build_match_index` (`:161-181`) | same, plus treats a first-level directory containing `basic.json` as a flat event |
| `reset_unavailable_markers` (`:719-763`), `refresh_due_ids` (`:917-935`) | league filter by directory-name prefix `<lid>_` |
| `create_csv_dataset` (`:1535-1730`) | three separate scans, both structures |
| `collect_detail_match_ids` + `_season_summary_files` (`:1855-1884`, `:1953-2007`) | summary CSVs, first league directory whose name starts with `<lid>_` |
| `generate_file_report` (`:2347-2372`) | only `season_*` directories |
| `routes/matches._detail_match_ids` (`src/web/routes/matches.py:219-242`) | three levels, league prefix |
| `routes/matches._get_missing_details_sync` (`:340-347`) | recursive `**/basic.json` over **all** leagues, parent directory name parsed as the id |
| `services/stats._detail_basics` and `system_stats` (`sofascore_scraper/services/stats.py:80-81`, `:132`) | only `season_*`: events under `_no_tournament/<sport>/` and flat events are not counted. Match counts are **sums of CSV rows** (`:95`, `:131`) and season counts are sums over every `*_seasons.json` file (`:128-130`), so an event listed in two summary files, or a league with two season-list files, is counted twice |
| `league_sports.infer_from_data` (`sofascore_scraper/web/league_sports.py:77-89`) | first `basic.json` under `<lid>_*/*/*` |
| season list readers | `SeasonFetcher` uses the configured league name with three fallbacks (`sofascore_scraper/season_fetcher.py:437-451`); the web routes take the bare `<lid>_seasons.json` when it exists and otherwise the newest `<lid>_*_seasons.json` (`src/web/routes/common.py:31-38`). The two can pick different files |

The table describes `3ae2599`. As built at `9b03c64`, several of these readers go through the catalog:

- **Match detail** (RD-1, #80): `_find_match_path`, `_build_match_index`, `_load_match_data_from_dir` and
  `GET /api/matches/{id}` read the catalog, with the two corrections of 5.1 and the order of 5.2. Two more
  visible differences come with them: an event stored in two places resolves to the copy with the newest
  event payload, and a directory whose payload id is not its name is not a record.
- **Statistics** (RD-4, #78): the dashboard, `GET /api/stats/system` and the terminal statistics count from
  the catalog through `StatusService(store)` (`sofascore_scraper/services/status.py`), so the double counting and the
  `season_*` glob of the row above are gone. Two more differences follow from counting catalog events
  instead of summary CSV rows, accepted as decision D21: (a) `FETCH_ONLY_FINISHED` applies when the count is
  read (with the setting on an event counts when it is finished or has stored details; with it off, every
  event), where it applied when the summary was written; (b) a match with stored details that no schedule
  lists counts as a match. The per-league disk size of the terminal UI still walks the league directories,
  because `StoreInfo.bytes` has one number per top-level entry.
- **Season lists and sports** (RD-5, #79): the web routes, `SeasonFetcher` and the terminal menu read season
  lists through `sofascore_scraper/services/tournaments.py` (functions on a Store), and `league_sports.infer_from_data`
  asks `tournaments.sport_of`. Every reader now takes the newest file of a tournament by mtime, whatever its
  name, so the row "season list readers" is history (5.1). Matches stored only under `_no_tournament/` still
  give no league a sport: their payloads name no unique tournament, so the catalog cannot attribute them
  either; the correction holds for a league directory without id.
- **Match lists** (RD-2, #89): `GET /api/matches` and a season's match list read the catalog with the rule of
  D21.
- **What to download** (RD-3, #97). The detail planners (`collect_detail_match_ids`, the need computation,
  `refresh_due_ids`, `pending_detail_ids`) and a league's "missing details" read the catalog through
  `QueryService` (`detail_needs`, `refresh_due`, `listed_events`, `detail_candidates`), so the rows
  `collect_detail_match_ids`, `refresh_due_ids`, `routes/matches._detail_match_ids` and
  `_get_missing_details_sync` of the table above are history; so is `reset_unavailable_markers` since ST-21
  (#104), which selects through the Store by the match's tournament (5.3). Where the design and the brief
  said otherwise: "missing details" does not use `Store.events.missing`; it takes the catalog's listed events
  that have no event payload (a read like RD-2's lists, which does not refuse an out-of-date catalog). A
  planner refuses: `QueryService.require_current` raises `CatalogNotCurrent`, a `StoreError`, when
  `Store.catalog_current` is False (`sofascore_scraper/services/query.py:166`, `:387` at `b3cb819`), so a download stops
  with a storage error instead of calling every match missing. A flat or `_no_tournament/` record has no
  tournament, so a league filter never selects it; only the unfiltered `refresh_due_ids()` reaches it. An
  event listed only by a summary row whose round label is not recognised has `listed_in` NULL (8.2) and is
  no candidate (old forms only). Inside a season the candidates come in kick-off order, not in the summary
  CSV's row order, which the catalog does not keep. `FETCH_ONLY_FINISHED` applies when the plan is made,
  as in the lists, so a season stored with the setting off no longer offers its unfinished matches while the
  setting is on; a summary row without a status is still a candidate. On the owner's data the sets were
  equal to main's (section 11); the reader goldens changed only where these rules say. P12 (#106), P13
  (#113) and ST-27 (#129) then moved the decision into `sofascore_scraper/services/planning.py`, which reads
  `Store.events.states()` and computes the need in Python; `QueryService.detail_needs` and `refresh_due`
  forward to it since ST-27, and nothing in `sofascore_scraper/` calls `Store.events.missing` at `b3cb819` (8.4).

### 1.3 Measured size of the local data

`data/match_details`: 423 event directories, 2,532 files (2,522 JSON + 10 export CSVs), 66.8 MB apparent,
71–74 MB on disk depending on the tool. The 2,522 JSON files are 64.6 MB. Per slice:

| File | Count | Total bytes | Average |
|---|---|---|---|
| `lineups.json` | 181 | 22,233,885 | 122,839 |
| `statistics.json` | 420 | 18,155,921 | 43,228 |
| `incidents.json` | 181 | 9,849,730 | 54,418 |
| `point_by_point.json` | 239 | 9,544,727 | 39,936 |
| `basic.json` | 423 | 4,444,824 | 10,508 |
| `team_streaks.json` | 420 | 296,492 | 706 |
| `h2h.json` | 420 | 50,493 | 120 |
| `pregame_form.json` | 157 | 48,241 | 307 |
| `observation.json` | 69 | 5,451 | 79 |
| `_unavailable.json` | 12 | 276 | 23 |

`data/matches`: 98 files, 12.8 MB (90 round/page JSON files = 7.0 MB). `data/seasons`: 6 files, about 45 KB.
`.meta/jobs.db`: 63 job rows. Locally there is no `_slice_status.json` (PR #25 is recent), no
`score_changes.jsonl`, no watcher file and no `backups/` directory. Those forms are therefore covered only by
the fixture factory (plan item G-02), not by real data.

Note for the layout design: the detail tree's season directory is `season_<name>` and carries **no season id**
(`sofascore_scraper/match_data_fetcher.py:1183-1191`), while `matches/` uses `<sid>_<name>`. The season id of a stored event is
only in its payload.

---

## 2. The `Store` module (`sofascore_scraper/store/`)

### 2.1 Rule

`sofascore_scraper/store/` is the only code that opens, lists, creates, renames or deletes anything under `DATA_DIR`, and
the only code that opens `catalog.db` or `state.db`. Everything else imports from `sofascore_scraper.store` (the package
root) and nothing from its submodules.

One import of a submodule exists during the transition: the shim `src/fsutil.py` imports `sofascore_scraper.store.files`
until ST-28 deletes it. `sofascore_scraper/web/jobs.py` imported `sofascore_scraper.store.jobs` until FX-1 (PR #60) switched it to the
root; its ratchet file is gone. As built since ST-28 (#135) no module outside `sofascore_scraper/store/` imports a
submodule: `src/fsutil.py` is deleted, and the 2.x file helpers it re-exported (`atomic_write_text`,
`atomic_write_json`, `file_lock`) are in `sofascore_scraper/config_files.py`, which writes only the configuration files
outside `DATA_DIR` (`config/leagues.txt`, `config/league_sports.json`, `overrides.json`, and the lock of
the log rotation). They write atomically with mode 0600 and, after the Windows retries, raise the plain
`PermissionError` (`sofascore_scraper/config_files.py:44-55` at `b6caf2f`); before, `ReplaceBusy`, a subclass of
`PermissionError`, so callers see no difference. Since FX-25 (#172) `file_lock` removes its `<path>.lock`
file while it still holds the lock, and a waiter checks after `flock` that the path still names the file
it locked (same device and inode) and opens it again if not, so two processes never hold the lock on two
different files (`sofascore_scraper/config_files.py:81-111` at `48e4c4c`); this covers `overrides.json`,
`leagues.txt`, `league_sports.json` and the log rotation. A backup names the settings files it takes, so a
`.lock` file was never a member. A process of an older version that waits on the same lock file during an
upgrade can overlap once with a new one that removes it. The Store keeps no 2.x helper; `ReplaceBusy` stays inside
`sofascore_scraper/store/files.py` (`:48`) as the internal signal of its own retry, which `_store_error` turns into a
non-fatal `StoreError` (`:132-139`).

The root as built (`sofascore_scraper/store/__init__.py` at `e0bae0c`) exports the error classes, the facade (`open_store`,
`Store`, `StoreInfo`), `Lease` and `LeaseInfo`, the job store names (`JobStore`, `JobStoreConflict`,
`JobRunningError`, `DataOperationRunningError`, `default_db_path`, `get_job_store`), since ST-18 the stream
types and `WatchStateStore`, and since ST-17 the follows names (`FollowStore`, `Follow`, `FollowSpec`,
`FollowConflict`, `ApplyResult`, `apply_follows`). Since ST-30 (#68) it exports the 17 names of the read
API: `EventStore`, `EntityStore`, `ChangeLog`, the query types `Ref`, `Scope` and `EventQuery`, and the
result types `Page`, `EventRow`, `EventState`, `MissingRow`, `SliceInfo`, `SliceError`, `TournamentSummary`,
`TournamentRow`, `SeasonRow`, `ParticipantRow` and `ChangeRow`. Since ST-11 (#75) it exports `CatalogAdmin`
with its reports (`RebuildReport`, `ReconcileReport`, `IndexProblem`, `SupersededDir`, `VerifyReport`,
`VerifyIssue`) and the five hooks of the legacy writers (`shadow_event`, `shadow_schedules`,
`shadow_season_lists`, `shadow_changes`, `shadow_cleared`; 3.5). At `9b03c64` it also exports `PutResult`
(ST-20), `HistoryStore`, `Snapshot` and `SnapshotInfo` (ST-26), and `ClearReport`, `BackupManager` and
`BackupInfo` (ST-19). `InstanceRunningConflict` (P23) is not exported; it is a subclass of
`DataOperationRunningError` in `sofascore_scraper/store/jobs.py` (6.1). At `b3cb819` the root also exports `CategoryRow`
and `SportRow` (ST-22), `Exporter`, `ExportReport` and `ExportSkip` (ST-25), `BackupCheck`, `RestoreReport`,
`BackupNotFound`, `BackupInvalid`, `RestoreRefused`, `SinkCursor`, `DEFAULT_PRUNE_MAX_AGE_SECONDS` and
`DEFAULT_PRUNE_MAX_ROWS` (ST-24), and `Migrator`, `MigrationPlan`, `MigrationReport`, `MigrationProgress` and
`MigrationIssue` (ST-23). The first version said that no version number is exported from the root; since
P19 (#119) `LAYOUT_VERSION`, `CATALOG_SCHEMA` and `load_migrations` are, for `ssc version`, while
`DERIVE_VERSION` and `MIN_READER_LAYOUT` stay inside, and a caller reads the versions of a directory from
`StoreInfo`. At `b6caf2f` the five hooks are no longer exported: FX-15 (#155) removed them, because no
product code called them after P26 (3.5). ST-28 (#135) added `league_dir_name`, the 2.x folder name of a
league (`sofascore_scraper/store/legacy.py:344`), which `sofascore_scraper/paths.py` held before and `QueryService` now imports from
the root; FX-19 (#156) added `Purger` and `TournamentClearReport` (9.3). Every name except the error
classes is loaded on first use (a module `__getattr__`, with a `TYPE_CHECKING` block for static tools and
the API snapshot). The reason: importing a submodule runs the package root first, `src/fsutil.py` imported
`sofascore_scraper.store.files` from the lowest layers of the application until ST-28, and `tests/test_store_catalog.py`
pins the exact set of modules that importing a store submodule loads. A new public name therefore goes
into three places of that file: the `TYPE_CHECKING` import, `_LAZY` and `__all__`.

The Store contains no network code and no policy. It does not know which slices a sport needs, how long the
refresh window is, or what "finished" means for a job. Callers pass those in as arguments. It may import
`sofascore_scraper.sports`, `sofascore_scraper.status`, `sofascore_scraper.slices`, `sofascore_scraper.exceptions` and `sofascore_scraper.version`, nothing else from `src`.

Services do not wrap the Store in a second interface. `02-services.md` section 2.5 maps what the services need
onto the API below; the API-surface test (2.4) pins it.

### 2.2 Package layout

```
sofascore_scraper/store/
  __init__.py     public names only (see 2.3), loaded on first use (2.1)
  api.py          Store facade, open_store(), per-data-dir registry, clear, info; since ST-11 the catalog
                  sync on open and the hooks of the legacy writers (3.4, 3.5)
  errors.py       StoreError and subclasses
  codec.py        canonical JSON bytes, gzip read/write, sha256
  files.py        atomic write/replace/remove, staging dir, retry on Windows (absorbed src/fsutil.py; since
                  ST-28 the 2.x helpers are in sofascore_scraper/config_files.py, outside the Store)
  purge.py        one tournament's or one season's data deleted (FX-19, #156; 9.3)
  layout.py       v3 path functions (pure)
  manifest.py     manifest.json dataclasses, read, write, validate
  legacy.py       read-only discovery and readers for every legacy form
  catalog.py      catalog.db connections, DDL, upserts, queries
  state.py        state.db connections, migration runner, runtime key/value
  sqlite.py       connection handling shared by catalog.py and state.py (FX-3, PR #55; see 3.2)
  derive.py       payload -> catalog row (pure)
  indexer.py      events and slices from files, rebuild, reconcile; orders the other sources (3.4, 3.5)
  verify.py       consistency checks (3.6)
  events.py  entities.py  history.py  changes.py  streams.py  follows.py  jobs.py  watch.py
                  (as built so far: events.py holds the read half of EventStore and the shared types of
                  the read API (ST-30); entities.py and changes.py hold the listing and change-log
                  indexers of ST-08 and the read halves of EntityStore and ChangeLog (ST-30); streams.py,
                  watch.py and follows.py are complete, jobs.py since P11; at 9b03c64 events.py also
                  holds the v3 writer and the promotion (ST-20), changes.py the append and the
                  incremental index (ST-20), history.py the history files and HistoryStore (ST-26);
                  at b3cb819 entities.py also holds EntityStore.put and the v3 entity indexers (ST-22),
                  events.py the change intents of FX-12 (6.2), changes.py the copy of the legacy log
                  (ST-23))
  lease.py        OS-lock based leases
  migrate.py      legacy -> v3 (ST-23, #110; 5.4)
  export.py  backup.py   (backup.py since ST-19, format 2 and restore since ST-24 #109; export.py since
                  ST-25 #108)
  schema/catalog.sql
  migrations/state/0001_initial.sql  0002_job_manager.sql  (the next one is 0003)
```

The adapters that reproduce today's response shapes (summary-row dictionaries, the `basic` key) are not part of
the Store. They live in `sofascore_scraper/services/` next to the readers that need them and are deleted with the legacy
`/api` routes.

### 2.3 Public API

All timestamps crossing the API are timezone-aware `datetime` in UTC or epoch seconds as `float`; the catalog
stores epoch seconds as `INTEGER`. As built (ST-30) the row types of the read API return the catalog's
integers unchanged, and `SliceInfo` returns `datetime` (see "The read API as built" below). All methods are
synchronous and thread-safe; async callers use `asyncio.to_thread` as the routes do today.

```python
# ---- opening --------------------------------------------------------------------------
def open_store(data_dir: str | os.PathLike | None = None, *, create: bool = True, readonly: bool = False,
               sync_catalog: bool = True) -> Store:
    """One Store per absolute data dir per process (cached). data_dir=None -> DATA_DIR env, default "data".
    Creates .meta/, schema.json, state.db and catalog.db when missing (create=True).
    Brings the catalog up to date with the files: built when it is not usable, reconciled otherwise (3.4, 3.5).
    sync_catalog=False leaves the catalog as found (admin tools, tests of a hand-built catalog).
    readonly=True never writes payloads; it still opens both databases and syncs the catalog.
    Raises SchemaTooNew when the directory was written by a newer layout or state schema."""

class Store:
    data_dir: Path
    events: EventStore
    entities: EntityStore
    history: HistoryStore          # odds snapshots and any other versioned slice
    changes: ChangeLog
    streams: StreamLog             # durable event streams with sequence numbers (live, change, job, system)
    watch: WatchStateStore
    follows: FollowStore
    jobs: JobStore
    runtime: RuntimeFacts          # small cross-process facts, e.g. the last bridge health snapshot
    catalog: CatalogAdmin
    migrate: Migrator
    export: Exporter
    backup: BackupManager
    def lease(self, name: str, *, purpose: str = "", wait: float = 0.0) -> Lease: ...   # context manager, see 6.1
    def clear(self, scope: Literal["events", "schedules", "seasons", "all"]) -> ClearReport: ...  # needs "maintenance"
    def info(self) -> StoreInfo: ...   # versions, row counts, bytes per area, legacy/v3 mix, last rebuild
    def close(self) -> None: ...

# ---- shared types ----------------------------------------------------------------------
@dataclass(frozen=True)
class Ref:
    kind: Literal["event", "tournament", "season", "team", "player", "sport"]
    id: int
    tournament_id: int | None = None      # kind == "season": writers need it (the season's directory lives under it); reads do not
    # constructors: Ref.event(id), Ref.tournament(id), Ref.season(tournament_id, season_id),
    #               Ref.team(id), Ref.player(id), Ref.sport(sport_id)

# One outcome type for the whole code base, defined in sofascore_scraper/slices.py (plan item ST-02). It is today's
# SliceOutcome (sofascore_scraper/match_data_fetcher.py:65-89) with two more fields and one more status.
@dataclass(frozen=True)
class Outcome:
    status: Literal["ok", "empty", "failed", "skipped"]
    data: Any = None               # parsed JSON; required for "ok"; optional for "empty" (an empty 200 body)
    reason: str | None = None      # empty: "404" | "empty"; failed: "403" | "429" | "5xx" | "timeout" | "network" | "parse" | "other"
                                   # skipped: "breaker" | "not_selected" | "not_applicable" | "not_due" | "unavailable" | "cancelled"
    http_status: int | None = None
    fetched_at: datetime | None = None    # None means "now" to the consumer; it is not filled at construction,
                                          # so two otherwise equal outcomes stay equal
    via: Literal["curl", "bridge"] | None = None
    meta: Mapping[str, Any] | None = None # small JSON kept next to the state (e.g. {"complete": true} for a round)

SliceState = Literal["ok", "empty", "error", "not_requested"]   # as stored and reported; "failed" outcomes are stored as "error"

@dataclass(frozen=True)
class SliceInfo:
    ref: Ref; key: str; sub: str
    state: SliceState
    has_payload: bool
    fetched_at: datetime | None     # when the stored payload was obtained
    checked_at: datetime | None     # last attempt of any outcome
    empty_count: int                # definitive "no data" answers
    unverified_empty_count: int     # legacy counts that no definitive answer backs
    error: SliceError | None        # reason, http_status, at, count
    stored_bytes: int | None; raw_bytes: int | None
    history_count: int
    meta: Mapping[str, Any]
    def settled_empty(self, threshold: int = 2) -> bool: ...

@dataclass(frozen=True)
class Scope:
    sport: str | None = None
    tournament_ids: Sequence[int] = ()
    season_ids: Sequence[int] = ()
    event_ids: Sequence[int] = ()
    participant_ids: Sequence[int] = ()
    followed: bool = False          # restrict to enabled tournament follows (state.db); other follow kinds do not widen it

@dataclass(frozen=True)
class EventQuery:
    scope: Scope = Scope()
    status_classes: Sequence[str] = ()      # empty = all; "finished only" is ("completed", "decided_without_play")
    start_from: float | None = None; start_to: float | None = None
    round: int | None = None
    text: str | None = None                 # participant name, accent- and case-insensitive
    has_details: bool | None = None         # has an /event/{id} payload
    updated_after: float | None = None      # a filter only; there is no "changed since" order (see below)
    sort: Literal["start_desc", "start_asc"] = "start_desc"
    limit: int = 50
    cursor: str | None = None               # opaque keyset cursor (sort, start_ts, id)
    offset: int | None = None               # only for the old /api/matches paging; not together with cursor
```

The facade as built so far (ST-10 #50, ST-18 #59, ST-17 #62, ST-30 #68, ST-11 #75; `sofascore_scraper/store/api.py` at
`e0bae0c`, as are the line references of this list). The block above is the target; this is the part of it
that exists:

- `Store` has `data_dir`, `readonly`, `store_id`, `closed`, `lease(name, purpose=, wait=)`,
  `lease_holder(name)` (the holder of a lease in any process, without taking it), `info(sizes=True)`,
  `runtime`, `streams`, `watch`, `follows` and `close()`; since ST-30 `events`, `entities` and `changes`
  with their read methods; since ST-11 `catalog` and `jobs`. Since ST-20 (#82) `events` has `put`,
  `observe`, `reset_empty_markers` and `delete`, and `changes` has `append`; since ST-26 (#92) `history`;
  since ST-19 (#90) `backup`, `clear` and `catalog_current` (`sofascore_scraper/store/api.py:282-283`, `:504-512`,
  `:590-642` at `9b03c64`). `EntityStore.put` exists since ST-22 (#98), `export` since ST-25 (#108) and
  `migrate` since ST-23 (#110); `backup` gained format 2, `verify`, `restore`, `prune` and `path_of` with
  ST-24 (#109) and P21 (#126). At `b3cb819` the facade is complete: `backup`, `export` and `migrate` are
  attribute lines of `Store.__init__` (`sofascore_scraper/store/api.py:288-290`). FX-19 (#156) added `purge`, the
  `Purger` of 9.3 (`sofascore_scraper/store/api.py:287` at `b6caf2f`).
- `catalog_current` (ST-19) answers whether the catalog is in sync: True when the Store is open and the
  last sync of this process, and every hook since, succeeded. A reader that plans from the catalog refuses
  to plan when it is False, instead of calling every match missing (`open_store` does not fail when the
  sync fails, 3.4). It does not know of files changed without a hook (by hand, by a 2.x process); the
  reconcile on open finds those, within the bound of decision S17 (3.5).
- `clear(scope)` takes one scope name or several (`events`, `schedules`, `seasons`, `all`) and returns a
  `ClearReport` (`scopes`, `cleared`, `v3_events`, `catalog_rebuilt`); it is described in 9.3. `backup` is
  the `BackupManager` of 9.1.
- `catalog` is the `CatalogAdmin` on the Store's shared `Catalog` (`sofascore_scraper/store/api.py:237`). Its
  `league_names` is a function that returns `store.follows.leagues()` and is asked on every scan (`:282`).
- `jobs` is a property, not an attribute line in `__init__` (`sofascore_scraper/store/api.py:391-395`). It returns
  `JobStore.for_store(store)` (P11; `sofascore_scraper/store/jobs.py:315-331`): one job store per open Store, which uses
  the Store's `state.db` connection, its lease manager and its stream log, and is kept in a weak cache
  (`_by_store`, `sofascore_scraper/store/jobs.py:985`) because the mirror of the running job and its writer lease live in
  that object. It is created on first access: `for_store` runs `reap_stale()` when it creates the object,
  which an attribute line would do on every open. A closed Store raises `StoreError`; a Store that is opened
  again gets a new job store. The service context builds its `JobManager` on the same call
  (`sofascore_scraper/services/context.py:77`). The web server still uses the process-wide object that rebinds between
  directories (`get_job_store`), which a per-directory property cannot express.
- The registry is keyed by the real path of the directory **and** `readonly`, so a read-only and a writable
  Store of one directory are two objects. `close()` closes the connections and removes the Store from the
  registry; leases that were taken stay with their holders. The registry keeps a Store open for the life of
  the process, so on Windows a data directory can be deleted or moved only after `Store.close()` (#120;
  6.4); the tests that remove a temporary data directory close the registry's Store first, and
  `tests/conftest.py` fails a test that leaves an open Store pointing at a deleted directory. Since FX-12
  (#100) `close()` first waits for a job of `store.jobs` that is inside its finishing block (at most 30 s,
  `JobStore.wait_for_finishing_job`; 3.2).
- `open_store` brings the catalog up to date (ST-11; `Store._sync_catalog`, `sofascore_scraper/store/api.py:298-346`): a
  catalog that is not usable is built from the files, a usable one is reconciled, on every open (3.4, 3.5).
  A read-only Store syncs as well, because the catalog is derived. A catalog that cannot be synced never
  fails the open; since ST-19 `catalog_current` says so. Since ST-19 the reconcile on open skips the pass
  over the legacy event directories when an open of the same directory finished one less than a minute ago
  (decision S17, 3.5). `open_store(..., sync_catalog=False)` leaves the catalog as found; the next
  `open_store` call for that directory without the keyword, or the first hook of a legacy writer, syncs it.
  After an open with the sync, `info().catalog_rebuild_reason` is None and `last_rebuild` is set, also for
  an empty directory.
- `info()` returns `StoreInfo`: the versions of `schema.json`, the schema versions of both databases,
  `catalog_rebuild_reason`, `last_rebuild` (the catalog's `meta.built_at` as written), `journal_modes`
  (`delete` means single-process mode, 6.4), row counts per table, `events_by_layout`, bytes per top-level
  entry of the data directory, and the leases held by any process. Summing the bytes walks the whole
  directory; `info(sizes=False)` skips the walk (15 ms against 0.5 ms on a copy of the owner's data).
  `Store.info` keeps no cache. The dashboard's cache of the sizes (at most 60 s old per data directory,
  dropped when the catalog changes) is in `sofascore_scraper/services/status.py` (RD-4, #78), so a change the catalog
  does not see (a CSV export, a backup, files deleted by hand) shows in the sizes up to a minute late. There
  is no byte count per league; the terminal UI's per-league size still walks the league directories.
- Inside the package a Store gives `_state` (the `StateDb`), `_catalog` (the shared `Catalog` with `state`
  attached) and `_leases`. The sub-APIs take the Store in their constructors (`StreamLog(store)`,
  `WatchStateStore(store)`, `FollowStore(store)`), not the `StateDb`: with `StateDb` in a public signature
  the API snapshot would start tracking `StateDb` itself, and with it the internal `Connection` class (the
  snapshot's scanner matches the identifier `Connection` in `sqlite3.Connection` to
  `sofascore_scraper.store.sqlite.Connection`). A new attribute is one line in `Store.__init__`, after `_state` exists;
  the read APIs (`EventStore(store)`, `EntityStore(store)`, `ChangeLog(store)`) are created after `_catalog`.
  The export of `CatalogAdmin` did widen the snapshot in that way: it now tracks `Catalog`, `CatalogState`,
  `Connection`, `LegacyEventDir` and `LegacySuperseded`, which public signatures of the admin reach.
- `open_store(create=False)` refuses a directory that has no `schema.json`. The web application's job store
  creates `.meta/state.db` alone, without `schema.json` and `catalog.db`, so a web installation's directory
  is "not a store yet" for that call. Code that only wants to know whether a `state.db` exists checks the
  file itself (`apply_follows` does). Since P11 (#69) a web job opens the Store of its data directory before
  it runs (best effort: a Store that cannot be opened is a warning and the job runs without it), so
  `schema.json` and `catalog.db` appear at the first job; a bridge-health transition opens it too (the
  runtime key `bridge_health`), and since ST-11 so does every hook of a legacy writer (3.5). A web
  installation that has done none of these is still "not a store yet".
- `main.py` opens the Store only to take a lease and closes it when the block ends, so a test that calls
  `main.main()` in process and holds the same Store object finds it closed. Since P11 that path
  (`_data_dir_lease`) serves only `--watch` and a `--recheck-unavailable` run without a download or refresh;
  downloads and refreshes go through the job manager, which opens the Store of the service context and
  leaves it open.

**Events and their slices**

```python
class EventStore:
    # reads -------------------------------------------------------------------------------
    def get(self, event_id: int) -> EventRow | None
    def list(self, q: EventQuery, *, with_total: bool = False) -> Page[EventRow]
    def count(self, q: EventQuery) -> int
    def iter(self, q: EventQuery, *, batch: int = 1000) -> Iterator[EventRow]      # keyset, for exports
    def payload(self, event_id: int, key: str = "event", sub: str = "", *, raw: bool = False) -> Any | bytes | None
        # raw=True returns the stored JSON bytes after decompression, without parsing. None when the catalog
        # shows no payload for the slice (the catalog decides; see "The read API as built").
    def payloads(self, event_id: int, keys: Iterable[str] | None = None) -> dict[str, Any]
        # keyed by the manifest's slice names: "statistics", or "odds_all/1" for a slice with a sub
    def slices(self, event_id: int) -> list[SliceInfo]
    def slice(self, event_id: int, key: str, sub: str = "") -> SliceInfo            # state "not_requested" when unknown
    def states(self, scope: Scope | None = None, *, status_classes: Sequence[str] = (),
               batch: int = 1000) -> Iterator[EventState]
        # For planning: one EventRow plus its SliceInfo rows per event, in id order; two statements per batch.
    def missing(self, scope: Scope | None, required: Mapping[str, Sequence[str]], *,
                status_classes: Sequence[str] = ("completed", "decided_without_play"),
                threshold: int = 2, limit: int | None = None, exclusive: bool = False) -> Iterator[MissingRow]
        # required: sport slug -> slice keys ("" = any sport). Yields (event_id, sport, has_event_payload, missing_keys).
        # exclusive=True (#121): a sport's own entry replaces the "" entry instead of adding to it.
    def refresh_candidates(self, *, now: float, window_s: float, min_interval_s: float,
                           scope: Scope | None = None, status_classes: Sequence[str] = (),
                           include_unobserved: bool = False) -> list[int]
    def stale(self, scope: Scope | None = None) -> list[int]       # rows a newer listing disagrees with (8.2)
    def open_events(self, *, started_before: float, scope: Scope | None = None) -> list[int]
    def summary(self, scope: Scope | None = None) -> list[TournamentSummary]     # counts for dashboards
    # writes ------------------------------------------------------------------------------
    def put(self, event_id: int, outcomes: Mapping[str | tuple[str, str], Outcome], *,
            count_empties: bool | Collection[str] = True,
            keep_history: Collection[str] = (),
            on_event_change: Callable[[Mapping | None, Mapping], Mapping | None] | None = None,
            status_regressed: bool | None = None) -> PutResult
    def observe(self, event_id: int, payload: Mapping[str, Any], *, observed_at: datetime | None = None,
                on_event_change: Callable[[Mapping | None, Mapping], Mapping | None] | None = None,
                status_regressed: bool | None = None) -> PutResult
        # = put(event_id, {"event": Outcome("ok", payload, fetched_at=observed_at)}, on_event_change=..., ...)
    def reset_empty_markers(self, scope: Scope | None = None, *, include_confirmed: bool = False,
                            threshold: int = 2) -> dict[str, int]
        # same result keys as today: {"matches", "slices", "scanned"} (sofascore_scraper/match_data_fetcher.py:719-763)
    def delete(self, event_id: int) -> bool

@dataclass(frozen=True)
class PutResult:
    created: bool                       # the event had no stored payload (a promotion is not a creation)
    event_written: bool                 # the "event" payload changed on disk
    superseded: bool                    # an "event" outcome was ignored because a newer observation is stored
    written: tuple[str, ...]            # slice names whose payload file changed ("statistics", "odds_all/1")
    change_seq: int | None              # sequence number of the change-log row, if one was written
    promoted: bool                      # the event was copied from the legacy layout by this call
    history: tuple[str, ...] = ()       # slice names that got a history snapshot (ST-26)
```

The write half as built (ST-20 #82, ST-26 #92; `sofascore_scraper/store/events.py:1217-1339` at `9b03c64`). The signatures
above are the built ones; `keep_history` came with ST-26, which ST-20 left out rather than accept and ignore
it. Where the numbered rules below say less, or something else:

- **Unknown events.** A `put` without an `"event"` outcome raises `UnknownEvent` for any id that has no
  stored event payload, also for an event the catalog knows from a listing: a v3 directory without an event
  payload is not a valid event for the rebuild (3.4), so its slices would not be indexed.
- **States.** A 404 for a slice that is `ok` with a payload leaves it `ok` and counts the answer; an error
  on an `ok` slice records the error mark and leaves it `ok`. An uncounted `empty` without data still
  creates a slice row (state `empty`, count 0, a previous error cleared); today's writer records nothing for
  a slice outside the required set or for an unfinished event, so ST-21 leaves those outcomes out (since
  FX-27, #175, the pipeline passes an unfinished event's body-less "no data" answers uncounted, so they open
  that row; 8.1). `ok` is
  what the caller passes, not what the presence rule says: the caller passes `empty` with data for a 200
  body without content. `failed` with reason `breaker` is treated as `skipped`, because today's callers
  report the open breaker that way (P13 switches them).
- **`on_event_change`** is called with `None` for an event without a stored payload, under the lock and
  before any file is written. An exception, or a row without `ts_utc` and `event_id`, leaves everything as
  it was (a legacy event is not even promoted). The sticky flag is set by `status_regressed=True` or by a
  true `status_regressed` field of the returned row. The callback must not write to the Store.
- **Superseded** compares the outcome's `fetched_at` with the stored observation's time, or with the
  `fetched_at` of the event slice when the record has no observation.
- **A damaged v3 directory.** When the manifest of an existing v3 directory is missing or unreadable, a
  `put` with an `"event"` outcome writes a new manifest and registers the readable payload files again;
  counters and marks are lost. Without an `"event"` outcome it raises `PayloadCorrupt`. A manifest of a newer
  format is never overwritten (`SchemaTooNew`).
- **No check of the catalog per call.** `put` does not call `Catalog.inspect()`; rows written into a catalog
  that was not usable at the open are rebuilt at the next open.
- **`reset_empty_markers`** has the keyword `threshold` (default 2, as in `missing()`). `scanned` counts the
  events that have any counter; a slice whose counters are reset and that has neither a payload nor an
  error loses its row (`not_requested`). A legacy event whose counters change is promoted first.
- **`delete`** removes the v3 directory and every legacy copy of the event, found by listing
  `match_details/` once, so it is linear in legacy event directories; each directory is moved to
  `.meta/trash` first. With only the catalog's `path` and `legacy_path` an older duplicate would bring the
  event back at the next reconcile. Decision S18 (settled on 2026-10-03, section 0) allows this for a
  delete the user asks for. Nothing calls `delete` at `b3cb819`; `Store.clear` deletes whole trees and
  moves nothing to the trash.
- **`keep_history`** takes slice keys; a key covers all of its subs. Only an outcome with data (`ok`, or
  `empty` with a 200 body) is kept: a 404, a failure, a skip and a superseded event outcome append nothing.
  The comparison is with the last snapshot (`history.last_sha256` of the manifest entry), not with the
  stored payload: a slice that has a payload but no history gets its first snapshot on the first kept `put`,
  even when its file does not change; and when A is kept, B is then written without `keep_history`, and A
  is put again with `keep_history`, nothing is appended. `PutResult.history` names the slices that got a
  snapshot.
- **Callers.** At `9b03c64` only the live service of P23 called a write method (`observe` for a finished
  match, `sofascore_scraper/services/live/supervisor.py:521` there). Since ST-21 (#104) the detail writers call `put`
  for a download and `observe(id, new, on_event_change=...)` for a refresh, whose callback is built from
  `diff_basic` and `change_row` and only compares; `reset_unavailable_markers(league_id)` calls
  `reset_empty_markers(Scope(tournament_ids=(league_id,)), threshold=2)`. The marker rules of today are kept
  through the arguments, as the paragraph after the numbered rules says. Since P13 (#113) every download,
  refill, refresh and single-match fetch goes through `FetchPipeline` (`sofascore_scraper/services/pipeline.py`), which
  calls `put` and `observe` from one writer thread. A write that meets `StoreBusy` is tried four times in
  all, waiting 0.5, 1 and 2 s in between (`put_retrying`, `sofascore_scraper/services/pipeline.py:568` at `b3cb819`); a
  store that stays busy fails that match with the non-fatal `StoreBusy`, and any other unexpected error
  fails only that match, as a non-fatal `StorageError`. The catalog is updated inside `put`, so the
  shadow-mode rule that
  "a failed catalog update never fails a write" (3.4, 3.5) no longer holds for the downloads. Matches
  without a unique tournament are stored by their id like every other match; no new `_no_tournament/`
  directory is written. `EntityStore.put` has its callers since ST-22 and P14 (below); `delete` has none.
- **The change row of an interrupted write** (FX-12, #100). The row is kept through an intent file (6.2).
  A write that recovers the row of an earlier interrupted write of the same event and records none of its
  own returns that row's seq in `PutResult.change_seq`, so a caller announces it as it would a new one; the
  `dedup_key` `change:<seq>` of the `change.recorded` event keeps it from being announced twice. A change
  row that cannot be serialised to JSON raises `StoreError` before the disk is touched; before FX-12 the
  payload was written and the row lost.

Semantics of `put` (the only way event payloads reach the disk):

1. Keys are validated: `key` matches `[a-z][a-z0-9_]{0,39}`, `sub` matches `[a-z0-9_.-]{0,80}`. As built
   (ST-03 and FX-4, `sofascore_scraper/store/layout.py` at `f286723`) two more rules apply: Windows device names (`con`,
   `nul`, `aux`, `prn`, `com1`-`com9`, `lpt1`-`lpt9`) are rejected as a key or as the stem of a sub, and the
   sub `_` is rejected, because `_history/<key>/_.jsonl.gz` is the history file of the slice without a sub.
   Subs are lower-case only (decision S13, FX-4, PR #53), because Windows and default macOS file systems do
   not distinguish case: a sub with an upper-case letter is rejected with `LayoutError`, it is not folded
   (the one exception is the reading of 2.x round file names since FX-5, section 0 and 5.1). A
   caller that builds a sub from a SofaScore slug lowers it first; a provider id as sub is its decimal
   digits. A manifest whose slice name carries an upper-case sub (`odds_all/A`) is invalid. An `"event"`
   outcome must be `ok` and its payload's `id` must equal `event_id`.
2. An event is created by the first `put` that carries an `"event"` outcome or by a listing (see
   `EntityStore.put`). A `put` without `"event"` for an id the catalog does not know raises `UnknownEvent`;
   as built, for any id without a stored event payload, a listed event included (see "The write half as
   built" above).
3. Per outcome:
   - `ok`: canonical bytes are computed (4.1). If their sha256 equals the manifest's, no file is written and
     only `fetched_at`/`checked_at` move. Otherwise the file is replaced atomically. Empty and error marks of
     that slice are cleared (today: `sofascore_scraper/match_data_fetcher.py:686-689`).
   - `empty` with data (an empty 200 body): the payload is stored, as today (`:1223-1225`), state `empty`.
   - `empty` without data (404): no file is written and an existing payload is never removed.
   - For both `empty` forms, `empty_count` is incremented only when `count_empties` covers the key, and a
     previous error is cleared (`:704-708`).
   - `failed`: stored as state `error` with `{reason, status, at, count + 1}`; `empty_count` is not touched
     (`:696-703`). An error never downgrades a slice that is `ok`.
   - `skipped`: ignored, nothing is stored. This covers the open circuit breaker, where no request was sent
     (`:693-694`). The client returns `skipped`/`breaker` since P05 (PR #48). `Outcome.from_error` and
     `MatchDataFetcher` still report the open breaker as failed with reason `breaker`, and
     `_update_slice_markers` relies on that; P13 switches both together. Until then `put` treats `failed`
     with reason `breaker` as `skipped` as well (ST-20). As built, ST-21 removed `_update_slice_markers`, and
     since P13 (#113) the pipeline reports a request the open breaker stopped as `skipped` / `breaker` on
     every path; `put` still accepts `failed` / `breaker` as `skipped`.
4. An `"event"` outcome whose `fetched_at` is older than the stored observation is ignored
   (`PutResult.superseded`). Two writers (a job and the live service) can therefore never replace a newer
   event payload with an older one.
5. `on_event_change(old_payload, new_payload)` is called inside the critical section, only when the `"event"`
   payload is about to change, with the stored payload (or `None`). It returns one change-log row or `None`.
   The row is appended to the change log in the same critical section; since FX-12 (#100) it is first
   written to an intent file, so an interrupted write does not lose it (6.2). This keeps the comparison
   atomic with the write while the comparison rule (`diff_basic`, `change_row`, `sofascore_scraper/refresh.py:95-140`)
   stays outside the Store. `status_regressed=True` is sticky (`sofascore_scraper/match_data_fetcher.py:878-885`).
6. For a key listed in `keep_history`, the payload is also appended to the slice's history file when its
   content hash differs from the last stored one. As built (ST-26) "the last stored one" is the last
   snapshot in the history file, not the stored payload (see "The write half as built" above).
7. If the event lives in the legacy layout it is promoted first (5.3).
8. The whole call runs under the write protocol of 6.2 and is atomic with respect to the catalog.

Callers keep today's marker rules by what they pass. Today slice markers are updated only for events that are
finished (`sofascore_scraper/match_data_fetcher.py:1228-1229`) and only for `required` slices (`:684`); a caller reproduces
that with `count_empties=False` (or the set of required keys) and by leaving failed outcomes of an unfinished
event out of the call.

The frozen 2.x writer of the twin tests (`tests/legacy_writer.py`) reproduces those rules: it writes empty and
error markers only for the slices that count in the match's sport, while `put` records them for every
requested slice. Since #121 the two differ for every slice in a sport's `optional_in`; FX-16 (#176) found it
when the random outcome sequences of `tests/test_store_put.py` met football's `pregame_form`, which no longer
counts, and the test now samples only the slices that count. Not a defect: `put` keeps more, and
completeness ignores those rows.

Slice state as stored and as reported:

| Reported state | Stored condition |
|---|---|
| `ok` | a payload file exists and the caller said it carries data; as built a later 404 or error leaves it `ok` |
| `error` | last attempt failed and the slice is not `ok` |
| `empty` | last answer was definitive "no data" (404 or an empty 200) |
| `not_requested` | no row |

A slice is still **expected** when `state != 'ok'` and `empty_count + unverified_empty_count < threshold`
(threshold 2 today, `sofascore_scraper/match_data_fetcher.py:49`, `:804-811`). This replaces `_unavailable.json` and
`_slice_status.json`. Mapping from the legacy files:

- `<key>.json` present: `ok` when the presence predicate says it has data, else `empty` with a payload. The
  predicates are today's `match_detail_slice_present` family (`sofascore_scraper/match_data_fetcher.py:559-637`); they move to
  the pure module `sofascore_scraper/slices.py` first (plan item ST-02) so the legacy reader can call them. As built
  (FX-5, #77) there are three answers, not two: `slices.slice_body_state(key, body)`
  (`sofascore_scraper/slices.py:225` at `9b03c64`) returns `BODY_DATA` (state `ok`), `BODY_NO_DATA` (`empty` with a
  payload, or `error` when `_slice_status.json` holds an error mark) or `BODY_MALFORMED` (state `error`,
  reason `corrupt`, with a `malformed` problem in the report).
  The third existed since ST-05 as a caught exception of the predicate. The legacy reader imports
  `BODY_DATA`, `BODY_MALFORMED` and `slice_body_state`; `_has_data` is gone. The rule per slice:

  | Slice | data | no data | malformed |
  |---|---|---|---|
  | `statistics` | a filled `statisticsItems` in a group of the `ALL` period (the first period when there is no `ALL`) | `null`, `{}`, `[]`, no filled group | a body that is neither object nor list; a filled `statistics` or `groups` that is not a list; a period or a group that is not an object |
  | `lineups` | a non-empty `players` list on one side | everything else, wrong types included | never |
  | `h2h` | one of the `teamDuel` counts (0 included) or a non-empty match list | everything else | a filled `teamDuel` that is not an object |
  | `pregame_form` | a `form` list or `position` / `value` / `avgRating` on one team | everything else | never |
  | `team_streaks` | a non-empty `general` list | an empty body, no `general` list | a filled body that is not an object |
  | `incidents` | a non-empty list, wrapped or bare | everything else | never |
  | `point_by_point` | a non-empty `pointByPoint` list | `null`, `{}`, `[]`, an object without the key, an empty or null `pointByPoint` | any other body that is not an object; a `pointByPoint` that is not a list |
  | any other key (`basic`, slices outside the registry) | a truthy body | a falsy body | never |

  So `{"pointByPoint": []}` is an `empty` slice with a payload (it was `ok`). On the owner's data the three
  answers changed nothing (section 11). The predicates of `match_detail_slice_present` return False for a
  malformed body and no longer raise.
- `_unavailable.json[key] = c` and `_slice_status.json[key].empty.count = k`: `empty_count = min(c, k)`,
  `unverified_empty_count = c - min(c, k)`. This is exactly the split `--recheck-unavailable` makes (`:772`).
- `_slice_status.json[key].error`: copied to the error fields.

**Non-match entities**

```python
class EntityStore:
    def put(self, ref: Ref, outcomes: Mapping[str | tuple[str, str], Outcome], *,
            count_empties: bool | Collection[str] = True, keep_history: Collection[str] = ()) -> PutResult
        # Same rules as EventStore.put. The events listed by a season's schedule page become catalog rows (8.2).
        # As built (ST-22) there is no index_listed_events: the rows are derived from the stored pages.
    def payload(self, ref: Ref, key: str, sub: str = "", *, raw: bool = False) -> Any | bytes | None
    def slices(self, ref: Ref) -> list[SliceInfo]
    def slice(self, ref: Ref, key: str, sub: str = "") -> SliceInfo
    def tournament(self, tournament_id: int) -> TournamentRow | None
    def tournaments(self, *, sport: str | None = None, text: str | None = None, limit: int = 100) -> list[TournamentRow]
    def seasons(self, tournament_id: int) -> list[SeasonRow]        # newest first; replaces every seasons-file reader
    def season(self, season_id: int) -> SeasonRow | None
    def participants(self, *, text: str | None = None, sport: str | None = None,
                     ids: Sequence[int] = (), limit: int = 50) -> list[ParticipantRow]
    def sport_of_tournament(self, tournament_id: int) -> str | None  # replaces league_sports.infer_from_data
```

`EntityStore.put` as built (ST-22, #98; `sofascore_scraper/store/entities.py:1594` at `b3cb819`). The signature is
`put(ref, outcomes, *, count_empties=True, keep_history=()) -> PutResult`, without `index_listed_events`:

- **Listing rows come from the stored pages.** A rebuild derives the listing rows from the stored schedule
  pages, so a page always gives them and there is nothing to switch off. Only the pages of a season
  (`Ref.season(...)`, key `schedule`) give listing rows; a team's events page does not.
- **Rules and protocol.** The outcome rules are those of `EventStore.put` (the two empty forms, `failed`,
  `skipped` and `breaker`, `count_empties`, `Outcome.meta`, `keep_history`), and so is the write protocol of
  6.2: a `pending_writes` marker of kind `ref.kind`, the catalog write lock, the payload files, the history,
  the manifest, then a re-index in the same critical section. A tournament goes to
  `v3/tournaments/<ut>/` (`manifest.json`, `seasons.json.gz`), a season to
  `v3/tournaments/<ut>/seasons/<sid>/` (`manifest.json`, `schedule/<sub>.json.gz`); teams, players and
  sports to their own directories (4.2). `tournament.json.gz` is not written, because nothing fetches
  `/unique-tournament/{id}` yet.
- **Refusals.** `Ref.event` and a season without `tournament_id` are refused, and so is a season id that
  already has a v3 directory under another tournament (`ValueError`): one season id lives under one
  tournament, the rule the ST-08 note asked for. A tree built otherwise (by hand, or two legacy directories
  of one season id) keeps "the last one applied wins" (3.5), and an event listed under two seasons keeps the
  old rule too; no new rule was added for either.
- **Both layouts are read together.** A v3 season list replaces the legacy list file of its tournament
  (`apply_season_lists`, the v3 lists first). A v3 page replaces the legacy page with the same sub
  (`read_season` / `apply_season`), the other legacy pages of the season are still read, and the pages are
  applied in time order (the manifest's `fetched_at` for v3, the mtime for legacy; ties by path). Nothing
  legacy is deleted (decision 4): the superseded legacy file stays on disk unchanged.
- **Cost.** Each schedule-page `put` re-indexes its whole season from both layouts and lists `matches/`:
  about 2 s of indexing for a 38-round season during a download (section 11).
- **Writers.** Since ST-22 `SeasonFetcher` stores a season list with `put(Ref.tournament(id), {"seasons":
  ...})` (an empty list with state `empty`), and `MatchFetcher` stores round pages with
  `meta={"complete": bool}` and event pages with `meta={"filtered": True}` (5.2). Since P14 (#116) both
  run in `sofascore_scraper/services/listing.py`, which reads the freshness of a listing from the slice rows
  (`SliceInfo.fetched_at` and the `complete` mark of a round): a season list is fresh for 6 h, a schedule for
  15 min while its newest page is that young and every page is complete or that young. The rounds list
  itself is not stored, so its freshness is inferred from the pages. A round cached by 2.x is found through
  its catalog row in any directory of that season.
- **Categories and sports.** `category(id)`, `categories(*, sport=, ids=, limit=)`, `sport(slug)` and
  `sports()` read the two tables, with the row classes `CategoryRow` and `SportRow` (for P21).
- **No listing of the tournaments with a season list.** `EntityStore` has no query for "tournaments with a
  `seasons` slice", so `tournaments.season_lists` still misses a v3-only list of a tournament that is not
  configured, not followed and has no events. When the configured league name differs from the follows
  name, a legacy `<name>_seasons.json` can still win over a newer v3 list in `seasons_of` and
  `season_lists` (`_named_only_lists` of `sofascore_scraper/services/tournaments.py`), and `index_v3_entity` of a
  tournament without a v3 list falls back to the legacy files scanned without league names, so a name-only
  list file is found only by a rebuild or a reconcile, which have the names.

Slice keys for non-match data are defined by the sport registry, not here. The storage contract is only:
owner + key + optional sub. Examples of how the known endpoints map:

| Data | Owner (`Ref`) | key / sub |
|---|---|---|
| season list | `tournament(ut)` | `seasons` |
| round page | `season(ut, sid)` | `schedule` / `round_12`, `round_3_final`, `last_0`, `next_2` |
| standings | `season(ut, sid)` | `standings` / `total`, `home`, `away` |
| season statistics | `season(ut, sid)` | `statistics`, ... |
| squad | `team(id)` | `players` |
| a player's season statistics | `player(id)` | `season_statistics` / `<ut>-<sid>` |
| rankings | `sport(id)` | `rankings` / `<ranking type>` |

As built (P28, #140; `OWNER_SLICES`, `sofascore_scraper/sports.py:576-607` at `b6caf2f`), all off by default: season
slices `standings` with the subs `total` and `home` (no `away` in the endpoint catalog), `season_info`,
`cuptrees`, `top_players`, `top_teams` and `season_odds` (provider as sub); the team slice `team_rankings`;
the player slice `player_statistics` (no sub, `/player/{id}/statistics/seasons`); the sport slice
`rankings` with the sub `5`, under the catalog's numeric sport id (`Ref.sport` takes an int). The squad
row (`team` / `players`) is not built: no `/team/{id}/players` endpoint is documented. A team's or a
player's match list is not stored either: FX-19 (#156) reads `/team/{id}/events/last|next/{n}` and
`/player/{id}/events/last/{n}` on every sync of a team or player follow, because the Store has no team or
player schedule slice (team `events` pages give no listing rows, 4.2 and the note of ST-22).

The `/event/{id}` payload is stored under the key `event`. The name `basic` survives only in the legacy file
name `basic.json` and in the response shape of the legacy `GET /api/matches/{id}` route.

**Odds snapshots and odds changes**

Odds are ordinary slices of the event (`odds_all`, `odds_featured`, `odds_changes`, `winning_odds`, with the
provider id as `sub`). The latest payload is the slice file. In addition, a key written with `keep_history`
appends the payload to the slice's history file whenever its content hash differs from the last stored one.
SofaScore's own change list for the main market (`/event/{id}/odds/{provider}/changes`) is the `odds_changes`
slice. As built (P28 #140, FX-15 #155) the slice's `meta` records where the payload came from: always the
provider id (`meta.provider_id`, the sub, `[client] odds_provider`, default 1) and, only when the user sets
`[client] odds_country`, the country (`meta.country`); the country is never derived from the machine
(decision of 2026-10-06; `with_provenance`, `sofascore_scraper/services/pipeline.py:627` at `b6caf2f`). The normalized
`Odds` record carries no country.

```python
class HistoryStore:
    def index(self, ref: Ref, key: str, sub: str = "") -> list[SnapshotInfo]          # n, fetched_at, sha256
    def snapshots(self, ref: Ref, key: str, sub: str = "", *, since: float | None = None,
                  raw: bool = False) -> Iterator[Snapshot]                             # n, fetched_at, payload
    def snapshot(self, ref: Ref, key: str, sub: str, n: int, *, raw: bool = False) -> Snapshot | None
    def prune(self, ref: Ref | None = None, *, older_than: float) -> int              # rewrites history files; needs "writer"
```

`HistoryStore` as built (ST-26, #92; `sofascore_scraper/store/history.py:360-500` at `9b03c64`), with the signatures above:

- `index` gives `fetched_at` in whole seconds, from the catalog's `slice_history` rows; `snapshots` and
  `snapshot` give the precise time from the history line. `n` is the position of the member in the file,
  so `prune` renumbers the snapshots: `n` is not stable across a prune.
- `prune` needs the writer lease held by the calling process (a `StoreError` otherwise). It handles events
  only, because no other entity has history before ST-22 (`EntityStore.put`): a ref of another kind returns
  0. When it rewrites a file it also drops a torn tail.
- Reads already resolve the directories of other entities (a season without `tournament_id` is looked up
  in the `seasons` table), but no entity history is written, indexed by a rebuild or pruned until ST-22.
  As built since ST-22 (#98) `EntityStore.put(keep_history=...)` writes entity history and the v3 entity
  scan indexes it (3.4); `prune` still handles events only (`sofascore_scraper/store/history.py` was not ST-22's file),
  so a ref of another kind still returns 0. Nothing keeps entity history at `b3cb819`.
- A reader retries once when a member's sha256 does not match the catalog (a prune ran meanwhile); no test
  runs a prune against concurrent readers.
- The first caller is P28, which registers the odds slices and passes them as `keep_history`; nothing in the
  application keeps history at `9b03c64`. As built (P28, #140): the four odds slices of an event
  (`odds_featured`, `odds_all`, `odds_changes`, `winning_odds`) and the season slice `season_odds` have
  `keep_history` (`sofascore_scraper/sports.py:558-569` and `:594-596` at `b6caf2f`); the pipeline passes the selected
  keys to `EventStore.put` and to `EntityStore.put` (`sofascore_scraper/services/pipeline.py:452`, `:528`). So entity
  history is written now (`season_odds`, off by default and experimental), while `prune` still handles
  event history only (`sofascore_scraper/store/history.py:486`: `kind = 'event'`); the history of a season is never
  pruned.
- The first caller of `prune` is FX-15 (#155), not P28: the scheduler task `prune-history` with a required
  `older_than` (for example `"90d"`) runs a `clear` job with the spec `{scope: history, older_than}` that
  calls `store.history.prune(older_than=...)` under the job's writer lease and reports `prune_history`
  (`sofascore_scraper/jobs/scheduler.py:327-358` at `b6caf2f`). No task exists by default, so nothing is pruned unless
  the user configures one (decision of 2026-10-06). The concurrency test against readers that ST-26 asked
  of the first caller was not added.

The catalog does not shred odds payloads into market/choice rows. Normalising a payload is the job of the
schema layer (`sofascore_scraper/schema/`), on request or during export, never of the catalog. Reason: those tables would
multiply the catalog size and its rebuild time, and every change of the normalised schema would force a
rebuild. The first version of this paragraph gave the normalising of odds, statistics, lineups and standings
to plan item SC-1. As built (SC-1, #72) schema v1 covers the entities, `Event`, `Slice`, `Change` and
`LiveEvent`, and gives slice payloads raw; P28 adds odds and the non-match models; no plan item normalises
statistics, lineups or incidents. Schema v1 was approved as written on 2026-10-02 (decision P2 is settled;
all 28 choices of `04-schema-v1.md` section 9 stand), so normalised slice models are later, additive items.
`sofascore_scraper/schema` does not import the Store at run time: its mappers take the rows of the read API, and the
caller reads the payload through the Store.

**Change log**

```python
class ChangeLog:
    def append(self, row: Mapping[str, Any]) -> int        # returns seq; normally reached through put(on_event_change=...)
    def list(self, *, after_seq: int = 0, event_id: int | None = None, since: float | None = None,
             limit: int = 1000) -> list[ChangeRow]
    def last_seq(self) -> int
```

**The read API as built**

As built (ST-30, #68; `sofascore_scraper/store/events.py`, `sofascore_scraper/store/entities.py` and `sofascore_scraper/store/changes.py` at
`e0bae0c`). The read methods of `EventStore`, `EntityStore` and `ChangeLog` exist with the signatures
printed above, as `Store.events`, `Store.entities` and `Store.changes`. The write methods (`put`, `observe`,
`reset_empty_markers`, `delete`, `EntityStore.put`, `ChangeLog.append`) did not exist then; all but
`EntityStore.put` (ST-22) exist since ST-20 (#82), described above under "The write half as built".
Every question is one or two SQL statements on `catalog.db`; no method walks the tree. The points on which
the first version of this section was silent, or from which the code differs:

- **Result types.** The first version named them without defining them. `Page` has `items`, `next_cursor`
  and `total`. `EventRow` mirrors the `events` table column for column, in the order of 3.3 (a test compares
  it with `PRAGMA table_info`); its three flags are `bool`, and `scores()` parses `scores_json`. `EventState`
  is `event` plus `slices`, with `slice(key, sub)`. `MissingRow` is `event_id`, `sport`, `has_event_payload`
  and `missing_keys`. `TournamentSummary` is `tournament_id`, `events`, `with_payload`, `finished`,
  `seasons`, `by_status`, `first_start_ts`, `last_start_ts` and `updated_at`; the events without a unique
  tournament form one row whose `tournament_id` is None. `TournamentRow`, `SeasonRow` and `ParticipantRow`
  are the columns of their tables without `name_folded`. `ChangeRow` is `seq`, `ts`, `event_id`, `sport`,
  `tournament_id`, `status_regressed`, `fields` (a tuple), `row` (the log line, parsed) and `segment`.
  `SliceError` is `reason`, `http_status`, `at` and `count`. The row types carry epoch seconds as integers,
  as the catalog stores them; `SliceInfo` and `SliceError` carry `datetime`, as the block above prints.
- **The catalog decides whether a payload exists.** `payload()` trusts the catalog: when the event has no
  slice row for the key, or the row has `has_payload = 0`, it returns None without touching the disk
  (`sofascore_scraper/store/events.py:706-713`). Three consequences. A truncated legacy slice file, which the indexer
  records as state `error` with reason `corrupt` (3.4), reads as "no payload" instead of raising. `basic` and
  `observation` are not slice keys; the event payload is `event`. A v3 payload file that is damaged while
  the catalog says `ok` raises `PayloadCorrupt`. A writer therefore has to keep `has_payload` true exactly
  when the file exists; the writer of ST-20 re-indexes the event from its files after every write.
- **The catalog has to be current.** ST-30 shipped on a catalog that `open_store` did not build, so on a
  real directory every method answered from an empty catalog. Since ST-11 (#75) the catalog is current after
  `open_store` and after every write of the legacy writers (3.5); only a Store that was opened with
  `sync_catalog=False` on a catalog that was never built still answers empty. A catalog that could not be
  synced is read as if it were current; for the need computation an empty catalog means `full` for every
  match, which a download would fetch again. RD-1 (#80) found that this could not be detected from outside
  the Store; since ST-19 (#90) `Store.catalog_current` tells (the facade above).
- **The observation is the event row.** The read API has no call for the observation, and `observation` is
  not a slice key. A reader rebuilds it from `EventRow` (RD-1 does, in `QueryService`): `observed_at` in
  whole seconds, `status_regressed`, and `change_ts`. In the catalog `change_ts` is the stored event
  payload's `changes.changeTimestamp`, not the value of `observation.json`; the two differ after a refresh
  that found no difference. Today's consumers read only `observed_at_utc` and `status_regressed`. A naive
  `observed_at_utc` (no offset; no code ever wrote one) is UTC for the Store and was local time for
  `refresh._parse_utc`.
- **`EventRow.path`** is set only for an event with a legacy detail directory. A listing-only row and a v3
  event have no path, and `SliceInfo` carries none either, so a caller that needs the league folder of such
  an event derives it (RD-2 uses `sofascore_scraper/paths.league_dir_name`; since ST-28, #135, the function is
  `sofascore_scraper.store.league_dir_name`, and `sofascore_scraper/paths.py` keeps only the configuration and browser-profile paths).
- **`EventQuery`** joins its conditions with AND. RD-2 (#89) runs the counting rule of decision D21
  ("finished, or has stored details") as two disjoint queries merged in order; an OR option would let it
  page in SQL. There is no date-substring filter: the legacy date filter, a substring of local ISO text,
  stays in the service, which narrows to a start-time range only for ISO prefixes.
- **Snapshots.** One call reads one snapshot of the catalog. `iter` and `states` take a new snapshot for
  every batch, so that a long export does not hold the WAL; a row that is written between two batches can be
  missed or returned in its older form.
- **`payloads()`** returns a dictionary keyed by the manifest's slice names: the key itself for a slice
  without a sub (`event`, `statistics`), `key/sub` for a slice with one (`odds_all/1`). `keys=` selects by
  key, with all subs. An unknown event gives an empty dictionary. A slice the catalog marks corrupt is
  absent; a legacy file whose content is the JSON value `null` is returned as None under its key.
- **`Scope.followed`** restricts to the enabled follows of kind `tournament`, the one shape 3.7 pins
  (`sofascore_scraper/store/events.py:82-83`). Team, player and event follows do not widen the scope. This still holds
  at `b6caf2f` (`_FOLLOWED_SQL`, `sofascore_scraper/store/events.py:123-124`) after FX-19 (#156): the matches of a
  team, player or event follow are downloaded, but `GET /events?followed=true` does not list them unless
  their tournament is followed too.
- **`EventQuery.text`** searches `participants.name_folded`, as 3.7 prescribes. A listing row that was built
  from a summary CSV has no participant ids (3.4), so it cannot be found by name.
- **`EventQuery.sort`** has only `start_desc` and `start_asc`. The "changed since" shape of 3.7
  (`ORDER BY updated_at, id`) cannot be expressed: `updated_after` works as a filter only, and no query of
  the API uses the index `events_updated`. An order for consumer sync needs a cursor shape of its own.
- **Keyset paging and rows without a start time.** The row-value comparison `(start_ts, id) < (?, ?)` never
  matches a row whose `start_ts` is NULL. Such rows form a second region, last in `start_desc` and first in
  `start_asc`, which is entered with a second query (`_keyset_page`, `sofascore_scraper/store/events.py:935-958`). The
  cursor is opaque and carries the sort, so a cursor of the other order is refused; `offset` and `cursor`
  cannot be combined, and in the offset mode `next_cursor` stays None. `with_total` counts in the same
  snapshot.
- **Errors.** An argument error is a `ValueError`, as in `follows.py`: a non-integer or a `bool` where an
  integer is expected, an integer outside SQLite's 64-bit range (`check_int`, `sofascore_scraper/store/events.py:336-345`),
  an unknown status class or sort, a cursor of another query. An invalid slice key or sub is `LayoutError`.
- **`states()`** runs two statements per batch, not "one indexed range query": the events in id order, then
  their slices by primary key (`sofascore_scraper/store/events.py:767-785`). A `BETWEEN` over the id range would read the
  slices of every foreign event that lies between two ids of a sparse scope.
- **`missing()`** joins the requirement with `LEFT JOIN`, so an event without a payload is returned even
  when nothing is required for its sport; the inner join of the first version would drop it
  (`sofascore_scraper/store/events.py:818-827`; the query is in 3.7). `missing_keys` come back in the order of the
  caller's `required`; a key listed under `""` and under a sport counts once; an empty `status_classes`
  means no status filter. The method returns an iterator over a list that is already complete. With `""`
  covering every sport, a sport could not drop a common slice; since #121 the keyword `exclusive=True`
  makes a sport's own entry replace the `""` entry, and `""` then applies only to the sports without an
  entry (`sofascore_scraper/store/events.py:1205-1243` at `b3cb819`). `QueryService.required_detail_keys()` lists every
  registered sport in full for use with it. Nothing in `sofascore_scraper/` calls `missing()` at `b3cb819`: the planners
  read `states()` (8.4).
- **`refresh_candidates()`** returns nothing for `window_s <= 0`, also with `include_unobserved`, as
  today's `refresh_due` does (`sofascore_scraper/store/events.py:853-854`). `observed_at` is whole seconds in the catalog.
- **`Ref.season`** does not need `tournament_id` for a read: `entity_slices` is keyed by kind and id. The
  writers need it for the directory.
- **`EntityStore.payload` on legacy files.** A round file is returned without the `_complete` key that 2.x
  adds (it is in `SliceInfo.meta`), and `raw=True` then gives canonical bytes instead of the bytes of the
  file. A season list that exists only in `league_seasons.csv` is returned as `{"seasons": [...]}`. A `Ref`
  of kind `event` is passed on to `Store.events` (`sofascore_scraper/store/entities.py:853-895`).
- **`sport_of_tournament`** returns the slug as stored, not normalised: the web layer's `normalize_sport`
  is code the Store may not import, so RD-5 applies it. The slug is the tournament row's, else the most
  frequent sport among the tournament's events. Since RD-5 (#79) `league_sports.infer_from_data` asks it
  through `tournaments.sport_of`. The tournament is the one the payload names, so a league directory
  without id is found; an event under `_no_tournament/` names no unique tournament and gives no league a
  sport.
- **Season lists through the read API** (RD-5, #79; `sofascore_scraper/services/tournaments.py` at `9b03c64`).
  `seasons_of` reads the stored payload, `store.entities.payload(Ref.tournament(id), "seasons")`, because the
  legacy response is SofaScore's own objects (key order, extra keys), which `SeasonRow` does not carry.
  `EntityStore` has no method that lists the tournaments having a season list (a tournament with a list and
  no event has no tournament row), so `season_lists` finds them by scanning the legacy list files with
  `store.catalog.reader.season_lists` (the legacy reader inside the Store) and reads each payload from the
  catalog. When the lists move to v3 (ST-22) `EntityStore` gets that listing. A season-list file named
  after the league alone is resolved by the service with the `leagues.txt` name when the catalog's map
  (from the follows) carries another one (5.1). As built, ST-22 (#98) moved the lists to v3 without adding
  that listing (`EntityStore.put` as built, above).
- **`summary(scope)`** has gaps that RD-4 (#78) works around with the existing read API:
  `TournamentSummary` has no count of unfinished events with a payload and no season-list count, `Scope`
  cannot select the events without a tournament, and `EntityStore` cannot list the tournaments that have a
  season list. `StatusService` makes one pass over the unfinished events with details and derives the
  season total from the row count of `seasons` minus the unlisted seasons. `summary()` runs two `GROUP BY`
  scans (228 ms at 300,000 synthetic events), which could be one.
- **Categories and sports have no read method.** `EntityStore` returns tournaments, seasons and
  participants; the `categories` and `sports` tables have no read method and no row class, and the schema
  mappers take such a row as a mapping. P21 needs the method before `/tournaments` can show a category; the
  plan gives it to ST-22, the next owner of `sofascore_scraper/store/entities.py`. ST-22 (#98) added them (above).
- **`ChangeLog`** reads the index in the catalog: `list` returns rows in `seq` order, a consumer passes the
  last `seq` it saw as `after_seq`, and `last_seq()` is 0 for an empty log. `append` exists since ST-20
  (#82): it writes the line to the month's segment and indexes it under the catalog's write lock (8.5); a
  `put` reaches it through `on_event_change`. `ChangeRow.row` of a v3 line contains `seq`.

The measurements of ST-30 on the owner's data are in section 11.

**Event streams**

One durable log carries every stream that consumers follow by sequence number: `live` (live service),
`change` (a notification for each change-log row), `job` (job started/finished), `system` (blocked, recovered,
live source switched, sink dropped).

```python
@dataclass(frozen=True)
class StreamEvent:          # what a producer appends
    type: str                                   # "live.status_changed", "job.finished", ...
    data: Mapping[str, Any] = {}                # must be JSON-serialisable
    event_id: int | None = None; sport: str | None = None; tournament_id: int | None = None
    source: str | None = None                   # push | poll | job | system
    dedup_key: str | None = None                # None = no de-duplication
    ts: float | None = None                     # epoch seconds; None = the moment of the append

@dataclass(frozen=True)
class StreamRecord:         # a stored event: the fields of StreamEvent plus
    seq: int; stream: str; ts: float            # ts in epoch seconds, millisecond resolution

@dataclass(frozen=True)
class StreamBatch:
    stream_id: str; events: tuple[StreamRecord, ...]; last_seq: int; gap: bool

@dataclass(frozen=True)
class StreamHead:
    stream_id: str; first_seq: int; last_seq: int

class StreamLog:            # StreamLog(store)
    def append(self, stream: str, events: Sequence[StreamEvent]) -> list[int | None]
        # One transaction; returns the seq numbers. An event whose dedup_key already exists in the stream is not
        # stored again and yields None. Raises StoreBusy when the write lock is not free within busy_timeout.
    def read(self, *, after: int = 0, limit: int = 500, streams: Sequence[str] = (), types: Sequence[str] = (),
             event_ids: Sequence[int] = (), sport: str | None = None, tournament_ids: Sequence[int] = ()) -> StreamBatch
    def wait(self, *, after: int, timeout: float) -> bool   # true as soon as a row with seq > after exists
    def head(self) -> StreamHead
    def prune(self, *, max_age_s: float | None = 7 * 86400, max_rows: int | None = 1_000_000) -> int
        # the two defaults since ST-24 (DEFAULT_PRUNE_MAX_AGE_SECONDS, DEFAULT_PRUNE_MAX_ROWS)
    def cursor(self, sink: str) -> int                      # last delivered seq of a sink (0 = none)
    def set_cursor(self, sink: str, seq: int, *, error: str | None = None) -> None
    def cursors(self) -> list[SinkCursor]                   # since ST-24: sink, seq, updated_at, last_error

class WatchStateStore:      # WatchStateStore(store); replaces watch_state_{sport}.json (sofascore_scraper/watcher.py:204-214)
    def load(self, watcher: str) -> dict[str, dict[str, Any]]
    def save(self, watcher: str, state: Mapping[str, Mapping[str, Any]], *, changed: Iterable[str] | None = None) -> None
    # for the 2.x files, because only the Store touches DATA_DIR (removed with the 2.x watcher, P30):
    def import_legacy(self, sport: str, *, watcher: str | None = None) -> int | None   # once; None = done before
    def append_legacy_events(self, lines: Sequence[str]) -> None       # bytes, LF on every platform
    # mirror_legacy_state (the copy of the state file) was removed by P23 (#91)

class RuntimeFacts:         # last known facts of other processes, e.g. "bridge_health"
    def get(self, key: str) -> RuntimeFact | None           # value, pid, updated_at
    def set(self, key: str, value: Mapping[str, Any]) -> None
```

Sequence numbers:

- There is **one sequence for all streams**. It comes from `INTEGER PRIMARY KEY AUTOINCREMENT`, so it is
  strictly increasing and never reused after pruning. Within one stream the numbers increase but are not
  consecutive; consumers must not assume `n + 1` follows `n`. (Verified: a de-duplicated insert also consumes
  a number.)
- Every append runs in a `BEGIN IMMEDIATE` transaction, so numbers are assigned in commit order: a reader
  never sees a row while a lower-numbered row is still uncommitted.
- `stream_id` is a UUID stored in `state.db` (meta key `stream_id`); it changes only if the state db is
  recreated, which tells a consumer that its saved position is no longer meaningful. It is created by the
  first `head()` or `read()`, not when the database is created. As built since ST-24 (#109) a restore also
  writes a new `stream_id` (9.2).
- `StreamBatch.last_seq` is the position to resume from: pass it as `after` to the next read. With a filter
  it moves past the rows the filter skipped; when the limit was reached it stops at the last returned row; it
  is never below `after`.
- `StreamBatch.gap` is kept per stream. `prune` writes the highest deleted number of each stream to the meta
  key `stream_pruned`; a read reports `gap` when `after` is below that mark for one of the requested streams
  (for any stream when the read is unfiltered). The other filters (`types`, `event_ids`, `sport`,
  `tournament_ids`) are not considered, so a consumer of one event type can be told of a gap that held none of
  its events. `types` is an exact match; glob patterns are the sink's job.
- `head()` on an empty log gives `first_seq` 0, and `last_seq` stays at the last number that was handed out
  (`sqlite_sequence`).
- `wait` uses an in-process condition for events appended by the same process and polls `PRAGMA data_version`
  (every 200 ms) for events appended by another process; SQLite has no cross-process notification.
- The sink cursors with their `last_error`, which the status command needs, are listed by `cursors()` since
  ST-24 (#109): `[SinkCursor(sink, seq, updated_at, last_error)]`, sorted by name; the lag of a sink is
  `head().last_seq - seq`. Since ST-24 `prune()` without arguments prunes at 7 days and 1,000,000 rows
  (`DEFAULT_PRUNE_MAX_AGE_SECONDS`, `DEFAULT_PRUNE_MAX_ROWS`, exported from `sofascore_scraper.store`; before, it removed
  nothing), `None` still turns a limit off, and the dispatcher's own constants are gone. A restore gives
  `state.db` a new `stream_id` (9.2), so `stream_id` no longer changes only when the state db is
  recreated. The first caller of `prune` is the sink
  dispatcher of P22 (#73); since P23 (#91) `ssc watch` hosts it, and the live service prunes the log itself
  once per hour (9.3).
- `EventStore.put` appends no `change.recorded` event (ST-20): `PutResult.change_seq` is what the caller
  announces, after `put` returned and so after the catalog transaction committed (6.2). The live service of
  P23 does so (`sofascore_scraper/services/live/supervisor.py:528-532` at `9b03c64`), and since P13 (#113) so do the
  downloads and refreshes: `FetchPipeline` appends `change.recorded` with the `change_seq` after the write,
  with the `dedup_key` `change:<seq>`, best effort (`sofascore_scraper/services/pipeline.py:530-542` at `b3cb819`). A row
  that a reconcile recovers at open (FX-12, 6.2) gets no `change.recorded` event; one that the next write
  of the event recovers is announced by that write's caller.
- The watcher state (P23, #91). The live service and the 2.x `--watch` both use the sport as the watcher
  name, so the state is shared: switching between `--watch` and `ssc watch` neither re-emits nor loses state,
  and the `live` and `watcher:<sport>` leases keep them from running together. Each imports its legacy
  state file once (`import_legacy`); neither writes it again.
- Measured (ST-18; tmpfs, so without fsync cost; a log of 200,200 rows in four streams): an append 0.04 ms, a
  read of 500 rows 2.7 ms whether one, two or all streams are asked for, `wait` woke 0.5 ms after an append of
  the same process, pruning 100,201 rows 165 ms. Watcher state of 2,000 rows: a full save 9.5 ms, one changed
  row 0.9 ms, a load 5 ms.

**Follows**

```python
@dataclass(frozen=True)
class FollowSpec:
    kind: Literal["tournament", "team", "player", "event"]
    entity_id: int
    name: str
    sport: str | None = None
    seasons: str | Sequence[int] = "all"        # "all" | "current" | "last:N" | season ids
    slices: Mapping[str, Any] | None = None     # None = defaults
    live: bool = False
    enabled: bool = True

class FollowStore:
    def list(self, *, kind: str | None = None, enabled: bool | None = None, origin: str | None = None) -> list[Follow]
    def get(self, kind: str, entity_id: int) -> Follow | None
    def add(self, spec: FollowSpec, *, origin: str = "api") -> Follow            # FollowExists on a duplicate id or tournament name
    def update(self, kind: str, entity_id: int, **changes: Any) -> Follow        # FollowManaged for origin "config"
    def remove(self, kind: str, entity_id: int) -> bool                          # FollowManaged for origin "config"
    def apply(self, desired: Sequence[FollowSpec], *, origin: str, prune: bool = True) -> ApplyResult
        # Makes the follows of that origin equal to `desired`; idempotent.
    def leagues(self) -> dict[int, str]          # the shape ConfigManager.get_leagues() returns today
```

Three origins:

| Origin | Source of truth | Writable through the API |
|---|---|---|
| `legacy` | `config/leagues.txt` and `config/league_sports.json`, as today. The table is a mirror: `apply(..., origin="legacy")` runs whenever the files change | yes, by writing the two files first (today's `ConfigManager` code path), then mirroring |
| `config` | the `[[follow]]` entries of the declarative config file; `apply(..., origin="config")` on every start and reload | no (`follow_managed`) |
| `api` | `state.db` | yes |

The legacy files are never rewritten from the table. An installation without a config file therefore keeps
working exactly as today, a hand edit of `leagues.txt` is picked up as today
(`ConfigManager._refresh_if_changed`, `sofascore_scraper/config_manager.py:489`), and a downgrade to 2.x finds its files
current. The uniqueness rules of today's file are kept: one row per id and one row per tournament name
(`sofascore_scraper/config_manager.py:490-495`).

As built (ST-17, PR #62; `sofascore_scraper/store/follows.py` at `f286723`). `FollowStore(store)` is `store.follows`; the
table mirrors the two league files. Two things inside the Store read it since: `Scope.followed` (ST-30) and
the name map of `store.catalog` (ST-11). No feature of the application reads it yet.

- **Types.** `FollowSpec` is what is asked for. P09 defined a class with the same fields in
  `sofascore_scraper/config/settings.py`, because the Store may not import `sofascore_scraper.config`; both exist, `apply` reads the
  fields, and `build_context` converts explicitly. `Follow` is a row: `id`, `kind`, `entity_id`, `name`,
  `sport`, `seasons`, `slices`, `live`, `enabled`, `origin`, `position`, `created_at`, `updated_at`, and
  `spec()`. `ApplyResult` has `origin`, `added`, `updated`, `removed`, `unchanged`, `conflicts` and
  `changed`; `FollowConflict(kind, entity_id, name, reason)` is one request that was not applied.
- **`apply` is idempotent and cheap.** When the table already equals the list it takes no write lock and
  writes nothing (0.68 ms measured), so the signature of the files that the first version kept in `meta`
  (`legacy_follows_sig`) is not needed and is not written.
- **Two origins ask for the same entity:** `config` > `api` > `legacy`. The stronger origin takes the row
  over (its id and `created_at` are kept); the weaker request is not applied and is reported in
  `ApplyResult.conflicts` (`owned_by_config`, `owned_by_api`). A league that is in `leagues.txt` and in
  `[[follow]]` is one row, the config row; when it leaves the config file it is pruned, and the mirror of
  the league files, which runs right after, adds it back as `legacy`. A config follow that took over an
  `api` row deletes it when it later leaves the config file; the row the API created is not restored.
- **A tournament name that another row holds:** the request is reported (`name_taken`) and not applied; no
  origin deletes another origin's row for its name. `apply` never raises for this; `add` and `update` raise
  `FollowExists`. A list that repeats an id or a tournament name keeps the later entry (`duplicate_id`,
  `duplicate_name`), as `leagues.txt` does today. A hand-edited `leagues.txt` can carry one name under two
  ids: `get_leagues()` returns both, the table keeps the later one, and `FollowStore.leagues()` then has one
  entry fewer.
- **`update` and `remove`.** `update` raises `KeyError` for a row that does not exist and `FollowManaged`
  for a config row; `remove` returns False for a missing row. Both accept `legacy` rows, but the next mirror
  overwrites what they did: a legacy follow is changed through `ConfigManager.add_league` / `remove_league`
  and `league_sports.set_sport`, and the mirror follows by itself.
- **`position`** is the index in the origin's own list; `add` appends after the highest position; `list()`
  orders by `(position, id)`, so rows of different origins interleave.
- **`leagues()`** includes every origin and disabled follows. With a config file, a tournament that is in
  both sources carries the config name (`premier-league`), not the `leagues.txt` name. Directory names on
  disk are built from the `leagues.txt` name, so a reader that resolves `<name>_seasons.json` should take
  the names from `ConfigManager`. As built (ST-11, #75) the catalog does not: its league names come from
  `store.follows.leagues()`, not from `ConfigManager`, because the Store may not import it and the reconcile
  on open also runs where no `ConfigManager` exists (the lease of `main.py`, the watcher, the bridge-health
  callback). The difference shows only for a season-list file named after the league alone together with a
  `sofascore.toml` whose name for that tournament differs from `leagues.txt`: that file is then reported as
  `unresolved_tournament` and not indexed. The owner's directory has no such file. RD-5 (#79) fixed row 74
  of `03-implementation-plan.md` section 15 on the reader side only: the season-list service passes the
  `ConfigManager` name and resolves such a file itself (2.3, "Season lists through the read API"). The
  catalog still reports the file as `unresolved_tournament`; the durable fix needs the `leagues.txt` name in
  what `Store._league_names` returns (`sofascore_scraper/store/api.py:333-340` at `9b03c64`), which ST-19 did not add.
- **When the mirror runs.** `ConfigManager` mirrors the leagues (origin `legacy`, with the sports of
  `league_sports.json`) after every load, including a hand edit of `leagues.txt` that it notices on the next
  read, and after `add_league` / `remove_league`; `league_sports.set_sport` and `resolve_all` mirror after
  they wrote the sidecar. A hand edit of `league_sports.json` is not noticed by itself, as today; it reaches
  the table with the next mirror. The `[[follow]]` entries (origin `config`) are applied by every
  `build_context` call, that is by each job, season refresh and CSV export request, and since P10 by each
  headless run; not at process start and not by `ConfigManager.reload_config()`. `build_context` then
  mirrors the league files, so both origins are in the table when a config file exists.
- **No open Store is needed, and none is left open.** `ConfigManager` and `build_context` call
  `apply_follows(data_dir, desired, origin=, prune=, create=False)`, which runs the same `FollowStore.apply`
  over a short-lived connection. Opening the Store there would keep a Store open for the life of the
  process and create `schema.json` and `catalog.db` in every directory a `ConfigManager` is constructed
  for. The mirror writes only into a data directory that already has a `state.db`; otherwise it does
  nothing (the sidecar is not even read) and the table fills at the next load, change or `build_context`.
  Only `build_context` with `[[follow]]` entries creates `.meta/state.db`. `desired` may be a function,
  which is called only when there is a table.
- **Failures do not stop the caller.** A mirror that cannot be written (a locked, newer or damaged
  `state.db`) is logged as a warning and the league operation or the job continues; with a config file that
  has follows, `build_context` then logs two warnings per call. An entry that cannot be applied is logged
  once per process.
- Measured on a copy of the owner's `jobs.db` (63 job rows, imported into a new `state.db`): a
  `ConfigManager` start with the first mirror 1.6 ms, a repeat without changes 0.68 ms, `add_league` with its
  mirror 1.1 ms; the job rows were unchanged afterwards.

**Jobs, administration, export, backup, migrate**

```python
class JobStore:      # today's methods (sofascore_scraper/web/jobs.py:49-402), on state.db, plus what the job manager needs
    create_running, update, snapshot, request_cancel, cancel_requested, list_jobs, get_job,
    exclusive, mark_stale_running_interrupted
    writer_busy          # ST-10: a job of this store runs, or another process holds the writer lease (6.1)
    # added by the job manager's item (P11, #69; 02-services.md 2.8), described below:
    for_store, reap_stale, cancel, poll_cancel, heartbeat, append_event, read_events, announce,
    get_record, list_records, active_record
    wait_for_finishing_job   # FX-12: classmethod, (store) -> bool; Store.close() calls it (3.2)
    # create_running(..., lease="writer" | "maintenance") since P21 (#126): a job may hold maintenance (6.1)

class CatalogAdmin:      # as built (ST-07, ST-08, ST-11)
    def __init__(self, data_dir, catalog: Catalog | None = None, *, clock=time.time, league_names=None)
    def ensure(self, *, progress=None) -> RebuildReport | None        # rebuild only if the catalog is not usable
    def rebuild(self, *, progress: Callable[[str, int, int], None] | None = None,
                should_stop: Callable[[], bool] | None = None,
                mode: Literal["auto", "in_place", "recreate"] = "auto") -> RebuildReport             # 3.4
    def index_event(self, event_id: int, *, paths: Sequence[str] = (), ...) -> str | None   # one event
    def reconcile(self, *, deep: bool = False, v3: bool = False, quiet: bool = False) -> ReconcileReport  # 3.5
    def sync_listings(self, kinds: Iterable[str] = LISTING_KINDS) -> ReconcileReport       # 3.5, for the hooks
    def diff_from_rebuild(self) -> list[str]                          # 3.5, the comparison of the shadow check
    def verify(self, *, deep: bool = False, repair: bool = False) -> VerifyReport                    # 3.6
    def stats(self) -> dict[str, Any]                                 # counts and meta keys, for Store.info

class Migrator:      # as built (ST-23, #110; sofascore_scraper/store/migrate.py:379)
    def plan(self, *, tournaments: Iterable[int] = (), limit: int | None = None, exact: bool = False,
             delete_legacy: bool = False, purge_derived: bool = False) -> MigrationPlan             # dry run
    def run(self, *, tournaments: Iterable[int] = (), limit: int | None = None, delete_legacy: bool = False,
            purge_derived: bool = False, should_stop: Callable[[], bool] | None = None,
            progress: Callable[[MigrationProgress], None] | None = None) -> MigrationReport

class Exporter:      # as built (ST-25 #108, SC-2 #130; sofascore_scraper/store/export.py:447)
    def raw(self, q: EventQuery, dest: str, *, keys: Sequence[str] | None = None,
            fmt: Literal["tree", "jsonl"] = "tree", pretty: bool = False,
            overwrite: bool = False) -> ExportReport                                                # 4.5
    def rows(self, rows: Iterable[Mapping[str, Any]], columns: Sequence[str], dest: str | BinaryIO,
             fmt: Literal["jsonl", "csv", "parquet", "sqlite"], *, table: str = "rows",
             overwrite: bool = False, types: Mapping[str, str] | None = None) -> ExportReport

@dataclass(frozen=True)
class ExportReport:
    fmt: str; dest: str                 # dest: the absolute path written; "" for a stream
    events: int                         # raw: events with at least one slice written; rows: 0
    items: int                          # files of a tree, lines of JSONL, rows of rows()
    bytes: int                          # bytes written to the target
    skipped: tuple[ExportSkip, ...] = ()

@dataclass(frozen=True)
class ExportSkip:
    event_id: int; key: str; sub: str
    reason: str                         # "missing" | "corrupt"
    detail: str

class BackupManager:  # as built (ST-24 #109, P21 #126; sofascore_scraper/store/backup.py:379)
    def create(self, scope: str = "all", *, config_files: Sequence[str] = (), env_file: str | None = None,
               now: datetime | None = None) -> BackupInfo
        # scope: all | state | data | config | seasons | matches | match_details (BACKUP_SCOPES)
    def list(self) -> list[BackupInfo]
    def path_of(self, name: str) -> str                      # a name inside backups/; BackupNotFound otherwise
    def verify(self, name: str) -> BackupCheck
    def restore(self, name: str, *, force: bool = False, dry_run: bool = False) -> RestoreReport
    def prune(self, keep: int | None = None, max_age_days: float | None = None, *,
              now: datetime | None = None) -> list[BackupInfo]    # the removed backups; keeps all by default
```

`BackupManager` as built (ST-19, #90; `sofascore_scraper/store/backup.py` at `9b03c64`) is the first step of the block
above, not the block: it writes today's zip unchanged (9.1). It has `directory`, `create(scope="all", *,
config_files=(), env_file=None, now=None) -> BackupInfo` and `list()`. The scopes are today's five (`all`,
`config`, `seasons`, `matches`, `match_details`), not `all` / `state` / `data`; config files go to the root
of the zip, not under `config/`; `.env` is the separate argument `env_file`; there is no `dest` and no
`include_catalog`. Format 2, `verify`, `restore` and `prune` remain ST-24's; `backup_info()` parses today's
names and `list()` ignores other files.

As built since ST-24 (#109) the block above is the manager: format 2 (9.1), `verify`, `restore` (9.2) and
`prune` (9.3), and since P21 (#126) `path_of`, which the download routes ask for a backup's path. Where it
differs from the first version of the block: the seven scopes are the design's three next to today's four
web scopes; `include_catalog` and `dest` do not exist (a catalog member is accepted on restore and
skipped, and the catalog is always rebuilt); the config files are still passed as `config_files` and
`env_file`; `verify` and `restore` take the name of a backup inside `backups/`, not a path; `prune` returns
the removed backups and keeps everything by default, and nothing calls it. `BackupInfo` gained `format`,
which is read from the zip, while `with_env` still comes from the file name. `BackupCheck` has `name`,
`ok`, `format`, `scope`, `manifest` (the `backup.json`), `members`, `bytes` and `problems`;
`RestoreReport` has `name`, `format`,
`scope`, `dry_run`, `force`, `restored`, `replaced`, `skipped`, `occupied`, `counts`, `catalog_rebuilt`,
`verify_ok` and `verify_issues`. The errors are `BackupNotFound`, `BackupInvalid` and `RestoreRefused`
(with `reasons`), all `StoreError`s; the services map them to `not_found`, `invalid_request` and
`confirmation_required`.

`Exporter` as built (ST-25, #108; SC-2, #130): the details are in 4.5. `Migrator` as built (ST-23, #110):
`plan` and `run` take tournament ids, not a `Scope`; `plan` also takes `limit`, `delete_legacy` and
`purge_derived`, so that its counts are those of the run with the same options, and `run` takes
`purge_derived`; the details are in 5.4.

`JobStore` as built (P11, #69; `sofascore_scraper/store/jobs.py` at `e0bae0c`, as are the line references of this list;
the job manager that uses it is `sofascore_scraper/jobs/manager.py`, `02-services.md` 2.8):

- **`for_store(store)`** returns the job store of an open Store (see the facade above). A `JobStore(db_path)`
  that opens its own `state.db` still exists for the web server's process-wide object.
- **Liveness is the row plus the lease.** A row that says `running` (or `queued`) belongs to a live job only
  while someone holds `writer`. `reap_stale()` (`sofascore_scraper/store/jobs.py:449-471`) marks such rows `interrupted`
  when the lease is free, and writes a `finished` job event for each. It never touches the store's own
  running job, and it touches nothing while another process holds `writer`. It runs when a job store is
  created or rebound, when a job is created (under the lease) and whenever the history is read (`list_jobs`,
  `get_job`, `get_record`, `list_records`, `active_record`, `cancel`); with no active row it is one SELECT.
  It replaces the unconditional sweep at open, so starting a second server or CLI process no longer marks
  the running job of the first one interrupted; a job whose process died is marked by the next process that
  opens the directory, lists the history or starts a job. `mark_stale_running_interrupted()` stays as the
  explicit, unconditional variant.
- **`create_running`** gained the keywords `job_id`, `kind`, `origin`, `spec`, `wait`, `purpose` (default
  `job`) and `replace_running` (default True). It takes `writer` with the given purpose (a purpose that
  starts with `op:` is a `ValueError`) and writes the row with `kind`, `owner` (the holder id of the lease),
  `origin_json`, `spec_json`, `created_at` and `heartbeat_at`. `wait` retries a refused lease every 0.1 s.
  With `replace_running=True`, the behaviour before P11 that `tests/test_store_lease.py` pins, a store that
  already runs a job reuses its lease and orphans the first row; that row is now marked `interrupted` by
  the next `reap_stale` instead of staying `running` forever. The job manager passes `replace_running=False`
  and gets `JobRunningError` (`sofascore_scraper/store/jobs.py:575-576`).
- **Cancel across processes.** `cancel(job_id)` writes the flag into the row of a job that any process
  runs; the running process reads it with `poll_cancel()` (the manager does so once per second).
  `cancel_requested(job_id=None)` reads the mirror. `update()` writes
  `cancel_requested = MAX(cancel_requested, ?)` (`sofascore_scraper/store/jobs.py:802`): before P11 a progress write
  wrote the mirror's value and could erase a cancel request that another process had just written. The
  final write reads the row's flag once more.
- **`heartbeat()`** writes `jobs.heartbeat_at` (epoch milliseconds) of the running job, every 5 s from the
  manager. It is for display only: liveness is the lease. The heartbeat is written to the job row only;
  `leases.heartbeat_at` is still written once, when the lease is taken (`sofascore_scraper/store/lease.py:361-373`).
- **`update(..., job_id=, state=, error=)`.** `job_id` makes a late write of a finished job a no-op when
  another job runs by then; `state` is the terminal state and `error` goes to `error_json`. The column holds
  `running`, `completed`, `partial`, `failed`, `cancelled` or `interrupted`: success is still written as
  `completed` (the 2.x name), `partial` is new, a job that ends after a cancel request is `cancelled`, and
  a final status the store does not recognise is stored as `failed` with a warning. `list_jobs`, `get_job`
  and the live mirror show `partial` as completed.
- **Job events.** `append_event(job_id, type, data)` adds a row to `job_events` and returns its `seq`, which
  starts at 1 for each job; `read_events(job_id, after=0, limit=500)` returns the events after that `seq`
  in order, each with `job_id`, `seq`, `ts_ms`, `type` and `data`. `announce(type, data)` appends
  `job.started` and `job.finished` to the `job` stream.
- **Records.** `get_record`, `list_records` and `active_record` return the full row for the job manager,
  with the JSON columns decoded, `live` (the job runs in this process) and `detail` (the live mirror for a
  job of this process, otherwise the job's last `progress` event). `list_jobs` and `get_job` keep the 18
  columns and the status names of 2.x.
- Retention is in 9.3. A copy of the owner's `jobs.db` was measured (section 11).

`CatalogAdmin` as built. It is constructed with the Store's shared `Catalog` (an attached `state.db` is fine)
or opens its own, which `close()` then closes. `league_names` is a mapping from tournament id to name, or a
function that returns one and is asked on every scan; it is needed only to resolve a season-list file that is
named after the league alone (5.1), and the caller supplies it because the Store may not read the league
configuration (the follows' map since ST-17; see the note on names under "Follows" above). Without it such
a file is reported as `unresolved_tournament`. `RebuildReport` carries the mode, the reason, the counts per
table, the problems (`IndexProblem`: layout, path, kind, detail), the superseded directories and files and,
since ST-08, `season_lists`, `schedules`, `listed` and `changes`. `ReconcileReport` is described in 3.5.
Since ST-11 (#75) the admin is on the facade as `store.catalog`, and `CatalogAdmin`, `RebuildReport`,
`ReconcileReport`, `IndexProblem`, `SupersededDir`, `VerifyReport` and `VerifyIssue` are exported from the
root. `sync_listings`, `diff_from_rebuild` and the `quiet` keyword of `reconcile` are described in 3.5.

Errors: `StoreError(StorageError)` keeps the `fatal` property of today's `StorageError`
(`sofascore_scraper/exceptions.py:109-137`), so callers that stop a job on a full disk keep working. Subclasses: `LeaseHeld`,
`StoreBusy`, `UnknownEvent`, `PayloadMissing`, `PayloadCorrupt`, `CatalogCorrupt`, `SchemaTooNew`, `LayoutError`,
`FollowExists`, `FollowManaged`. The mapping to error codes, exit codes and HTTP statuses is the single table in
`02-services.md` section 2.6: any `StoreError` is `storage_error` (exit code 5), `LeaseHeld` is one of the three
"another instance" codes (exit code 6).

Error types as built (ST-03, ST-06, ST-09), where the first version of this section was silent:

| Situation | Error |
|---|---|
| a payload or manifest file is missing | `PayloadMissing` (not `FileNotFoundError`) |
| a file is truncated, is not valid gzip or JSON, or decompresses to nothing (`gzip.decompress(b"")` returns `b""` without an error) | `PayloadCorrupt` |
| an invalid manifest | `PayloadCorrupt` on read, `LayoutError` on write (`write_manifest` refuses it) |
| a manifest of a format newer than 1; a `state.db` newer than the code | `SchemaTooNew(component=, found=, supported=)` |
| an unknown file suffix, or a payload write to anything but `.json.gz` | `LayoutError` |
| a `.json.zst` file while no zstd module can be imported | a plain `StoreError` that names `backports.zstd` |
| a payload that cannot be serialised | a non-fatal `StoreError` |
| a replace that still fails after the Windows retries | Store-layer functions: a non-fatal `StoreError`; the 2.x helpers of `src/fsutil.py`: `ReplaceBusy`, a `PermissionError` subclass, which 2.x callers treat as fatal (4.4). Since ST-28 (#135) the Store has no 2.x helpers; `sofascore_scraper/config_files.py`, outside the Store, raises the plain `PermissionError` for the configuration files (2.1) |
| a SQLite lock that outlasts `busy_timeout`, including the switch to WAL | `StoreBusy` |
| the SQLite library is older than 3.24 | a plain `StoreError`, one wording for both files, with the found version in `detail` (FX-3) |
| a `sub` with an upper-case letter (FX-4) | `LayoutError` |
| no free temporary name after 100 attempts (`.<name>.<8 random characters>.tmp` is opened with `os.open` since FX-4) | a non-fatal `StoreError` (EEXIST) |
| a lease that another holder has | `LeaseHeld`, with the holder found by probing the OS locks (6.1) |
| a lock file that the caller cannot open for writing (FX-8) | a `StoreError` that is not `LeaseHeld`, with `EACCES` and the path of the lock file (6.1) |
| an invalid argument of the read API (ST-30) | `ValueError`; an invalid slice key or sub is `LayoutError` |
| an unreadable database file | `CatalogCorrupt` |
| SQLite reports 'disk full' or 'read-only' | a `StoreError` with the errno, so that `fatal` is true |

Every subclass keeps the base constructor (message, path, errno_code, detail); extra fields are keyword-only,
so `Class.from_exception(exc, path, reading=True)` works on each.

### 2.4 Enforcing "only the Store touches `DATA_DIR`" in CI

Four tests, all in the normal `pytest` job (`.github/workflows/ci.yml:85-93`), no new CI job. This section
describes them as built by plan item ST-04 (PR #47).

1. **Static check** (`tests/test_store_boundary.py`, uses `ast`). For every module under `sofascore_scraper/` outside
   `sofascore_scraper/store/` it fails on:
   - any import of a `sofascore_scraper.store.<submodule>` (only `from sofascore_scraper.store import ...` is allowed), whether absolute,
     relative, inside a function, through `importlib.import_module` or by attribute access through the root;
   - any call to `open`, `os.listdir`, `os.scandir`, `os.walk`, `os.remove`, `os.unlink`, `os.rename`,
     `os.replace`, `os.makedirs`, `os.mkdir`, `os.rmdir`, `os.stat`, `os.path.exists/isfile/isdir/getsize/getmtime/getctime`,
     `glob.glob/iglob`, `shutil.*`, `tempfile.*`, `sqlite3.connect`, `zipfile.ZipFile`, `pandas.read_csv`,
     `DataFrame.to_csv`, and the `pathlib.Path` methods that hit the disk. The list as built is a superset:
     also `io.open`, `os.open`, `gzip.open` and similar openers, `os.lstat`, `os.access`, `os.chmod`,
     `os.utime`, the remaining `os.path` probes, every `pandas.read_*` and the other `DataFrame.to_*` file
     writers. `shutil.get_terminal_size` is exempt; `Path.resolve`, `absolute` and `expanduser` compute a path
     and are not flagged. Names are resolved through the module's imports, and a function that is only
     mentioned (`map(os.remove, paths)`) counts too.
   - The scanner is heuristic for `pathlib`: ambiguous method names (`exists`, `open`, `stat`, `glob`,
     `rename`, `replace`) are flagged only when the receiver is inferred to be a `Path`. What it misses is
     left to the runtime check.
   An allowlist names the modules that legitimately touch *other* files, each with a one-line reason:
   `sofascore_scraper/config_manager.py` and `sofascore_scraper/config/` (config files and `.env`), `sofascore_scraper/paths.py`, `sofascore_scraper/i18n.py`
   (locales), `sofascore_scraper/doctor.py` (environment probes), `sofascore_scraper/throttle.py` (budget files), `sofascore_scraper/challenge_solver.py`
   (browser profile), `sofascore_scraper/logger.py` and `sofascore_scraper/diagnostics.py` (log files and the diagnostics bundle, PR #24),
   `sofascore_scraper/sinks/file.py` (the file sink's own output path), `sofascore_scraper/web/app.py` and `sofascore_scraper/web/missing_ui.py` (static
   files). The allowlist exempts a module from the file-system rule only; the import rule and the runtime
   check still apply to it. As built since ST-28 (#135; `FS_ALLOWLIST`, `tests/test_store_boundary.py:55-76`
   at `b6caf2f`) it also names seven modules that sat in the baseline before: `sofascore_scraper/config_files.py` (the
   atomic writes and the lock of the configuration files), `sofascore_scraper/private_files.py` (modes of `.env` and the
   browser profile), `sofascore_scraper/redact.py` (when `.env` changed), `sofascore_scraper/version.py` (`pyproject.toml`; the Store
   may import it), `sofascore_scraper/web/security.py` (the UI's `index.html`, for the CSP decision),
   `sofascore_scraper/web/league_sports.py` (`league_sports.json`) and `sofascore_scraper/web/openapi.py` (the committed OpenAPI
   document, a developer tool).
2. **Runtime check** (`tests/conftest.py`, `sys.addaudithook`). CPython raises audit events for `open`,
   `os.listdir`, `os.scandir`, `os.remove`, `os.rename`, `os.mkdir`, `os.rmdir`, `shutil.rmtree`,
   `sqlite3.connect` and others. The hook looks at paths inside the test data directory, walks the call stack
   to the nearest frame under `sofascore_scraper/`, and records a violation when that frame is not in `sofascore_scraper/store/`. Frames
   in `tests/` are ignored, so tests can still seed fixtures. This catches paths that are built dynamically,
   which the static check cannot see. As built:
   - The hook follows the current value of the `DATA_DIR` environment variable, not only the directory of
     `tests/conftest.py`, because most tests that exercise readers and routes point `DATA_DIR` at their own
     temporary directory. Tests that pass `data_dir=` as an argument are observed only when they register the
     directory with `conftest.STORE_BOUNDARY.add_data_dir(path)`.
   - A record is named after the function the `sofascore_scraper/` frame called directly (`shutil.rmtree`, `os.makedirs`,
     `pandas`), not after the raw audit events: one `shutil.rmtree` raises different events on Linux, on
     Windows and across Python versions. System calls made inside a library generator that is being iterated
     (`os.walk`, whose frame is `os._walk` on Python 3.10 and 3.11) are attributed to the library's own call.
     Records keep the on-disk case of module paths.
   - The hook cannot be removed once installed. Later recorders are added to `conftest.BOUNDARY_RECORDERS`.
   - A write that goes through the `src/fsutil.py` shim has its nearest `sofascore_scraper/` frame in `sofascore_scraper/store/files.py`
     and is therefore not a violation, although the caller built the path; the static check does not flag
     `from src.fsutil import ...` either. Those call sites become visible when the shim is removed (ST-28).
     As built (ST-28, #135) the shim is gone; its writers moved to `sofascore_scraper/config_files.py` (allowlisted, and
     writing outside `DATA_DIR` only), so no hidden call site appeared. The shadow recorder of 3.5
     (`ShadowEdits`) still skips frames of `sofascore_scraper/store/files.py` and looks at their caller
     (`tests/conftest.py:361` at `b6caf2f`), which mattered only for the shim; it is harmless now and keeps
     tests that call `files.remove_tree` themselves counted as test writes.
   - On Windows with Python 3.12 or later `shutil.copy2` copies through `_winapi.CopyFile2`, which raises only
     the audit event `_winapi.CopyFile2` and no `shutil.copyfile` or `open` event. #86 found that such a copy
     into a data directory was invisible to this check and to the shadow check; since #88 both event sets of
     `tests/conftest.py` list it (`tests/conftest.py:167`, `:368` at `9b03c64`).
3. **Layering check** (`tests/test_layers.py`): `sofascore_scraper/store/` imports only what 2.1 allows (`sofascore_scraper.sports`,
   `sofascore_scraper.status`, `sofascore_scraper.slices`, `sofascore_scraper.exceptions`, `sofascore_scraper.version`). It is checked as that allow-list, which is
   stricter than a list of forbidden packages, and it includes what the allowed modules pull in: every Store
   module is loaded in a fresh interpreter, and the test fails if another `src` module or an HTTP client, the
   browser bridge or the web framework is loaded with it. `socket` is allowed, so that a lease can record the
   host name.
4. **API-surface snapshot** (`tests/test_store_api_surface.py`): compares `sofascore_scraper.store.__all__` and every public
   signature with checked-in text files under `tests/fixtures/store_api/`, one per public name (classes,
   functions, constants and type aliases), so an API change always shows up in review and two PRs that extend
   different classes do not edit the same file. The text is rendered from the source with `ast`, so it is the
   same on Python 3.10 to 3.14. A Store class that appears in a public signature without being exported gets a
   file too. Every public name bound in `sofascore_scraper/store/__init__.py` must be in `__all__`.

Until ST-28 (#135; "The strict checks as built" below), both the static and the runtime check used a
**ratchet**: `tests/store_boundary/baseline/<module>.txt`, one
file per offending source module. A new violation fails the test. A baseline entry that no longer occurs also
fails the test, so entries must be removed in the PR that fixes them and the lists can only shrink. One file
per module means two PRs that clean up different modules do not edit the same baseline file. The last PR
(ST-28) deletes the directory, which makes both checks strict without a code change. Format as built:

```
[static]
MatchFetcher._load_cached_round:open
_clear_data_sync:shutil.rmtree x3
[runtime]
_dir_state:tempfile.mkstemp
```

- `[static]` entries are `function:call`, with a count when the call occurs more than once. `[runtime]` holds
  only accesses in functions that the static list does not already track.
- The stale-entry check for `[runtime]` entries runs only when the whole suite runs, and not on Windows or
  under pytest-xdist: a run narrowed by path, `-k` or `-m` (the CI browser job is one) proves nothing about an
  entry it did not observe. New runtime violations fail everywhere.
- `STORE_BOUNDARY_UPDATE=prune python -m pytest` removes entries that no longer occur and lowers counts; it
  never adds. `STORE_BOUNDARY_UPDATE=rewrite python -m pytest` regenerates the files and can add entries; it is
  for code that moves between modules or for a renamed function, and the added lines show in the diff.
  `STORE_API_UPDATE=1 python -m pytest tests/test_store_api_surface.py` regenerates the API snapshot.
- The debt at `0aa73b4`: 23 files, 166 static entries covering 291 calls in 21 modules, 3 runtime entries
  (`sofascore_scraper/match_data_fetcher.py` 105 calls, `src/ui/settings_ui.py` 67, `src/web/routes/matches.py` 26,
  `src/web/routes/data.py` 24, `sofascore_scraper/services/stats.py` 17, `sofascore_scraper/season_fetcher.py` 11, the rest 6 or fewer).
  At `f286723`: 22 files, 163 static entries covering 288 calls in 20 modules, 3 runtime entries. The file of
  `sofascore_scraper/watcher.py` is gone (ST-18 moved its four accesses into the Store), the file of `sofascore_scraper/web/jobs.py` is
  gone (FX-1), and `sofascore_scraper/services/context.py` has a new one with the two directory calls that moved there
  from the terminal UI's constructor (P08).

Open points that ST-28 has to settle before the baseline directory can be deleted:

- Three modules touch files outside `DATA_DIR` and are not on the allowlist, so they sit in the baseline and
  look like debt: `sofascore_scraper/version.py` (`read_version` reads `pyproject.toml`; the Store is allowed to import this
  module), `sofascore_scraper/redact.py` (`_env_file_values` stats the `.env` file) and `sofascore_scraper/web/league_sports.py` (`load`
  reads `league_sports.json` next to the league list). The two modules PR #43 added, `sofascore_scraper/private_files.py`
  (modes of `.env` and the browser profile) and `sofascore_scraper/web/security.py`, are in the same position.
- The runtime check has no allowlist, but two allowlisted modules do touch `DATA_DIR`: `sofascore_scraper/doctor.py`
  (`_dir_state` creates and removes a probe file in the data directory) and `sofascore_scraper/diagnostics.py` (`_jobs`
  opens the job database read-only). Each needs a Store method or a named exception.

**The strict checks as built (ST-28, #135; `tests/test_store_boundary.py` at `b6caf2f`).** The two lists
above and the ratchet are history. `tests/store_boundary/baseline/` is deleted and a test asserts that it
stays deleted (`test_the_ratchet_baseline_is_gone`, `:584`); there are no `STORE_BOUNDARY_UPDATE` modes and
no counts. Both open points are settled: the five modules are on the allowlist (with `sofascore_scraper/config_files.py`
and `sofascore_scraper/web/openapi.py`, item 1 above), and every remaining access to `DATA_DIR` from outside the Store is
a **named exception**: one function, its reason and the item that removes it (`NAMED_EXCEPTIONS`,
`:80-95`). There are five:

| Function | Why it touches `DATA_DIR` | Removed by |
|---|---|---|
| `sofascore_scraper/doctor.py::_dir_state` | the writability probe opens and removes a file; the doctor runs without opening the Store | whoever adds a Store method for the probe |
| `sofascore_scraper/diagnostics.py::_jobs` | the diagnostics bundle opens `state.db` (or the 2.x `jobs.db`) read-only, without a lease or a reconcile | whoever adds a Store method for that read |
| `sofascore_scraper/services/context.py::_ensure_directory` | the context creates the data directory and the 2.x folders it still needs (`DATA_SUBDIRECTORIES`, `match_details` and `datasets` since FX-15) | P30, with the 2.x directory creation |
| `sofascore_scraper/utils.py::ensure_directory` | the 2.x fetchers and the CSV export create their directories | P30, with the fetcher faces |
| `sofascore_scraper/services/export.py::_write_file` | the export's output file at the path the user chose, which may lie inside the data directory (like the file sink) | — (kept) |

An exception covers every file call of that function, static and at run time, never an import. A new access
outside the Store now fails at once: a new file outside `DATA_DIR` needs an allowlist entry with a reason,
an unavoidable access to `DATA_DIR` a named exception with a reason and its owner, each stated in the pull
request. An exception that no longer occurs fails the test too: statically from the source
(`test_static_named_exceptions_are_still_needed`, `:558`), at run time only when the whole suite runs
(`test_runtime_named_exceptions_are_still_needed`, `:635`), for the reason the stale-entry check of the
ratchet had. So P30 deletes the rows of `ensure_directory` and `_ensure_directory` in the pull request that
removes the 2.x directory creation, and whoever moves the doctor's probe or the diagnostics read into the
Store deletes theirs.

---

## 3. The catalog

### 3.1 Two SQLite files

| File | Content | If it is lost |
|---|---|---|
| `.meta/catalog.db` | index of payload files: events, slices, tournaments, seasons, participants, change-log index, history index | rebuild from files; nothing is lost |
| `.meta/state.db` | follows, jobs and job events, event streams, sink cursors, watcher state, runtime facts, lease info, migration history | API-created follows and job history are gone; streams restart with a new `stream_id`; sinks start from "now" |

The owner's draft lists follows, live events and jobs as catalog tables and also says the catalog can be
deleted and rebuilt. Both cannot hold for the same file, so they are separated here. Queries that need both
(for example "events of followed tournaments") attach `state.db` to the catalog connection (`ATTACH`); measured
at 1.8 ms on 300,000 events.

`.meta/jobs.db` is not modified. Its rows are imported once into `state.db` and the file stays where it is, so
a 2.x process started on the same directory still finds its history.

What is authoritative where:

| Data | Authority | In a backup? |
|---|---|---|
| payloads, manifests, history files | files under `v3/` and the legacy trees | yes |
| slice state (ok, empty counts, last error), observation, sticky `status_regressed` | `manifest.json` of the entity (v3) or the three legacy side files | yes (they are files) |
| change log | `changes/*.jsonl` and legacy `score_changes.jsonl` | yes |
| every table in `catalog.db` | derived | no (optional, to skip the rebuild after a restore; as built, ST-24, never: a restore always rebuilds) |
| follows of origin `api` | `state.db` | yes |
| follows of origin `legacy` / `config` | the config files; the rows are a mirror | the files, when passed to the backup |
| jobs, job events | `state.db` | yes |
| event streams, sink cursors, watcher state | `state.db` | yes |
| runtime facts, leases | the running processes; the rows are information only | no |
| exports, legacy summaries, `processed/*.csv` | derived | no |

### 3.2 Connection settings

Applied on every new connection by the shared module `sofascore_scraper/store/sqlite.py` (FX-3, PR #55), which
`catalog.py` and `state.py` both use:

```sql
PRAGMA journal_mode = WAL;        -- persistent; verified after setting (see below)
PRAGMA busy_timeout = 5000;       -- ms
PRAGMA foreign_keys = ON;
PRAGMA temp_store = MEMORY;
PRAGMA journal_size_limit = 67108864;   -- truncate the WAL to 64 MB after a checkpoint
PRAGMA synchronous = NORMAL;      -- catalog.db
PRAGMA synchronous = FULL;        -- state.db
```

- One connection per thread per file, kept in thread-local storage, `isolation_level=None` with explicit
  transactions. Write transactions always start with `BEGIN IMMEDIATE`, so a writer waits up to
  `busy_timeout` at the start and never fails in the middle on a lock upgrade. Exceeding the timeout raises
  `StoreBusy`.
- `catalog.db` uses `synchronous = NORMAL`: after a power cut the newest transactions may be missing, and the
  reconcile step (3.5) repairs that from the files. `state.db` uses `FULL` because nothing can repair it;
  writers of high-volume rows (live events) batch one transaction per poll round.
- If `PRAGMA journal_mode = WAL` does not return `wal` (network file systems), the Store falls back to
  `DELETE` journal mode, logs a warning, and the doctor reports it: only one process may use the directory.
  The warning is logged once per file per process, in English (`<file> could not be switched to WAL mode (a
  network file system?); opened in DELETE journal mode. Only one process at a time may use this data
  directory: <path>`), through the logger of the module that opened the file.
- `busy_timeout` does not cover the switch to WAL. When two connections switch a new file at once, SQLite
  returns `SQLITE_BUSY` immediately instead of waiting (found independently by ST-06 and ST-09: 'database is
  locked' in at least 14 of 300 and in 11 of 100 stress rounds; with eight threads and no retry, 16 of 100
  rounds on Python 3.14.7 and SQLite 3.53.4, 0 of 100 with the shared code). `sqlite.configure()` retries
  the switch for up to `busy_timeout`, pausing 5 ms and doubling up to 100 ms, and at the deadline lets the
  `sqlite3.OperationalError` out; the callers turn it into `StoreBusy` (`Catalog._connect` through
  `to_store_error`, `StateDb._request_wal`). The fall-back to `DELETE` mode goes through the same retried
  call. Reading a file's identity (`application_id`, `user_version`, table count) happens in one read
  transaction, so that a concurrent schema creation is never seen half done.
- Connections close themselves. `sqlite3.Connection` emits a ResourceWarning from its own finalizer on
  Python 3.13+, and for cyclic garbage that finalizer can run before a wrapper's `__del__`; the thread-local
  connections are therefore instances of a Connection subclass that closes itself. They are kept in a
  `ThreadConnections` object per file; the connection of a thread that has ended is closed when the thread
  ends, not when the next thread opens one.
- Closes are serialized (RD-5, #79). One connection can be closed by two threads at once: its own thread's
  finalizer (`Connection.__del__`) and `ThreadConnections.close_all` or `close_finished` in another thread,
  because the weak registry still shows the connection while the finalizer runs. On Python 3.10
  `sqlite3.Connection.close` then runs `sqlite3_close` twice and the process aborts; since 3.11 the second
  call is harmless. `Connection.close` takes a re-entrant module lock, `_close_lock`
  (`sofascore_scraper/store/sqlite.py:61` and `:132-134` at `9b03c64`), so the second close waits and finds the
  connection closed; it is re-entrant because the garbage collector can run another connection's finalizer
  inside a close. The readers that RD-1, RD-4 and RD-5 moved onto the Store read from short-lived route
  threads, which made the case common: the first CI run of #79 died on it. With a fixed stress script on
  CPython 3.10.21 the lock survived 100,000 rounds in both modes, while the code without it crashed in the
  `close_all` mode every time (an earlier 320-minute hang was a bug of the script, not a deadlock). The lock
  covers `StatusService` and `QueryService`, whose reads go through `Catalog.read()` on the thread's own
  connection.
- What the lock does not cover. `close_all` requires that no other thread is running a statement on the
  connection it closes; `Store.close()` from one thread while another thread is inside a read still breaks
  that and can crash Python 3.10. Nothing in the web process calls `Store.close()`; `main.py` does at the end
  of a CLI run. `Store.close()` also closes `state.db` (`sofascore_scraper/store/api.py:655-666` at `9b03c64`) without
  waiting for a job of `store.jobs` that is finishing: since #93 the job manager does its last store accesses
  inside a finishing block of the job store, and `JobStore.close` and `rebind` wait for that block (at most
  30 s; 6.1), but `Store.close` does not. Closing a store while a job is mid-run is not covered either. Item
  (2) of the fix item FX-12 adds the wait through a small public `JobStore` helper. As built (FX-12, #100;
  `sofascore_scraper/store/api.py:675-697` at `b3cb819`) `Store.close()` first calls the classmethod
  `JobStore.wait_for_finishing_job(store)`: it looks up the job store of that Store without creating one,
  waits up to 30 s while a job of it is inside its finishing block, does not wait when called from the
  finishing thread itself, and on a timeout logs one warning and returns False, after which the Store is
  closed anyway. A job in the middle of its run and a read in another thread are still not waited for; the
  docstring says that the caller must make sure they are done. Without the wait, a stress loop that closed
  the Store at the moment a job's row was finished logged a `StoreError` for the closed `state.db` in every
  one of its 15 rounds.
- `sofascore_scraper/store/sqlite.py` had no owner in the plan's chain table when RD-5 changed it; the lock is pinned by
  `tests/test_store_sqlite.py`.
- Error mapping differs between the two files, as before FX-3. `catalog.py` converts every sqlite3 error
  inside a read or write helper to `StoreError` (table in 2.3). `state.py` converts only the lock timeout
  (to `StoreBusy`, with SQLite's own text `database is locked` in `detail`); other sqlite3 errors leave it
  as `sqlite3.Error`, because the job store's callers catch them as such. One gap of the facade follows from
  that (FX-11, #85): `open_store` wraps only the construction of `StateDb` into a `StoreError`, so when
  `state.db` can be read but its WAL index cannot be written (leftover read-only `-shm` files, 4.4)
  `Store._sync_schema` (`sofascore_scraper/store/api.py:308-329` at `9b03c64`) lets a bare `sqlite3.OperationalError` out,
  which a caller that catches `StoreError` does not catch. Item (3) of FX-12 wraps it. As built (FX-12,
  #100; `sofascore_scraper/store/api.py:315-327` at `b3cb819`) `_sync_schema` turns a `sqlite3.Error` into a `StoreError`
  with `sqlite.to_store_error(e, <state.db path>)`; a "readonly database" message also gets `errno=EROFS`,
  so `fatal` is true. The body moved unchanged into `_sync_schema_locked`.
- Only `SQLITE_BUSY` counts as busy (on Python 3.10, which has no error codes, the text `database is
  locked`). `SQLITE_LOCKED` (code 6, 'database table is locked') is a plain error for both files since FX-3;
  no path of `state.py` or `jobs.py` produces it.
- One statement splitter for the catalog DDL and the state migrations: it splits at semicolons, so several
  statements may share a line, and a `--` comment after a statement on the same line stays with that
  statement. (The two old splitters disagreed on both points; the SQL executed from the two shipped scripts
  is unchanged.)
- Minimum SQLite: 3.24 (UPSERT, row values, partial indexes, `WITHOUT ROWID`). Checked on open. No `STRICT`
  tables, no generated columns, no FTS, so the SQLite bundled with every supported Python works.
- `PRAGMA application_id` marks the two files (`0x53464331` catalog, `0x53465331` state); `PRAGMA user_version`
  is the schema version.

### 3.3 DDL

`catalog.db` (schema version 1). This DDL was executed (SQLite 3.53.4) in the form printed here, including the
columns and the index predicate that changed during reconciliation.

```sql
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE sports (
  slug TEXT PRIMARY KEY,
  id   INTEGER,
  name TEXT
) WITHOUT ROWID;

CREATE TABLE categories (
  id     INTEGER PRIMARY KEY,
  sport  TEXT NOT NULL,
  name   TEXT,
  slug   TEXT,
  alpha2 TEXT
);

CREATE TABLE tournaments (
  id          INTEGER PRIMARY KEY,          -- uniqueTournament.id
  sport       TEXT,
  category_id INTEGER,
  name        TEXT,
  name_folded TEXT,                         -- casefolded, accents stripped (search)
  slug        TEXT,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX tournaments_sport ON tournaments(sport, name_folded);

CREATE TABLE seasons (
  id            INTEGER PRIMARY KEY,        -- season.id
  tournament_id INTEGER NOT NULL,
  name          TEXT,
  year          TEXT,
  sort_key      REAL NOT NULL DEFAULT 0,    -- as SeasonFetcher._get_sortable_year_value(year)
  listed        INTEGER NOT NULL DEFAULT 0, -- 1: present in the tournament's season list payload
  position      INTEGER,                    -- order in the season list payload (0 = first)
  updated_at    INTEGER NOT NULL
);
CREATE INDEX seasons_tournament ON seasons(tournament_id, sort_key DESC, id DESC);

CREATE TABLE participants (
  id          INTEGER PRIMARY KEY,          -- team.id (competitor id space: teams, single players, pairs)
  sport       TEXT,
  name        TEXT,
  name_folded TEXT,
  short_name  TEXT,
  slug        TEXT,
  name_code   TEXT,
  country     TEXT,                         -- alpha2
  gender      TEXT,
  type        INTEGER,                      -- SofaScore team.type as given
  national    INTEGER,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX participants_name ON participants(name_folded);

CREATE TABLE players (
  id          INTEGER PRIMARY KEY,          -- player.id (person id space; filled from squad and player payloads)
  name        TEXT,
  name_folded TEXT,
  slug        TEXT,
  team_id     INTEGER,
  position    TEXT,
  country     TEXT,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX players_name ON players(name_folded);

CREATE TABLE events (
  id                 INTEGER PRIMARY KEY,
  sport              TEXT    NOT NULL DEFAULT '',  -- slug; '' when the payload does not say
  category_id        INTEGER,
  tournament_id      INTEGER,                      -- uniqueTournament.id; NULL when the event has none
  stage_id           INTEGER,                      -- tournament.id (the non-unique "tournament" object)
  stage_name         TEXT,                         -- tournament.name (the `tournament` column of today's lists)
  season_id          INTEGER,
  round              INTEGER,
  round_name         TEXT,
  round_slug         TEXT,
  start_ts           INTEGER,                      -- epoch seconds, UTC
  status_type        TEXT,
  status_code        INTEGER,
  status_description TEXT,
  status_class       TEXT    NOT NULL,             -- not_started|live|completed|decided_without_play|void|unknown
  home_id            INTEGER,
  away_id            INTEGER,
  home_name          TEXT,
  away_name          TEXT,
  home_score         INTEGER,                      -- homeScore.display, else .current
  away_score         INTEGER,
  winner_code        INTEGER,
  scores_json        TEXT,                         -- normalised score sheet (sofascore_scraper/status.extract_scores)
  slug               TEXT,
  custom_id          TEXT,
  -- quality
  observed_at        INTEGER,                      -- when the payload this row derives from was read; NULL = unknown (legacy)
  change_ts          INTEGER,                      -- changes.changeTimestamp
  observed_gap       INTEGER,                      -- observed_at - start_ts (NULL if either is NULL)
  status_regressed   INTEGER NOT NULL DEFAULT 0,
  tier_hint          INTEGER,
  stale              INTEGER NOT NULL DEFAULT 0,   -- a newer listing disagrees with the stored event payload
  -- provenance / storage
  row_source         TEXT    NOT NULL,             -- 'event' | 'listing'
  listed_in          TEXT,                         -- sub of the newest schedule slice that lists the event ('round_12', 'last_0')
  has_event_payload  INTEGER NOT NULL DEFAULT 0,
  layout             TEXT,                         -- 'v3' | 'legacy' | NULL (listing only)
  path               TEXT,                         -- legacy event directory relative to DATA_DIR; NULL for v3 (derived from id)
  legacy_path        TEXT,                         -- superseded legacy directory still on disk
  sig                TEXT,                         -- change signature of the indexed files (not reproduced by a rebuild)
  first_seen_at      INTEGER NOT NULL,
  updated_at         INTEGER NOT NULL
);
CREATE INDEX events_tournament_season ON events(tournament_id, season_id, start_ts, id);
CREATE INDEX events_sport_start       ON events(sport, start_ts, id);
CREATE INDEX events_start             ON events(start_ts, id);
CREATE INDEX events_season_round      ON events(season_id, round, start_ts);
CREATE INDEX events_live              ON events(sport, start_ts) WHERE status_class = 'live';
CREATE INDEX events_unsettled         ON events(observed_gap)
  WHERE has_event_payload = 1 AND observed_at IS NOT NULL;
CREATE INDEX events_unobserved        ON events(tournament_id, id)
  WHERE has_event_payload = 1 AND observed_at IS NULL;
CREATE INDEX events_legacy            ON events(id) WHERE layout = 'legacy' OR legacy_path IS NOT NULL;
CREATE INDEX events_stale             ON events(id) WHERE stale = 1;
CREATE INDEX events_open              ON events(start_ts, id) WHERE status_class IN ('not_started', 'live', 'unknown');
CREATE INDEX events_updated           ON events(updated_at, id);

CREATE TABLE event_participants (
  participant_id INTEGER NOT NULL,
  start_ts       INTEGER NOT NULL DEFAULT 0,
  event_id       INTEGER NOT NULL,
  side           INTEGER NOT NULL,                 -- 1 home, 2 away
  PRIMARY KEY (participant_id, start_ts, event_id)
) WITHOUT ROWID;
CREATE INDEX event_participants_event ON event_participants(event_id);

CREATE TABLE event_slices (
  event_id               INTEGER NOT NULL,
  key                    TEXT    NOT NULL,
  sub                    TEXT    NOT NULL DEFAULT '',
  state                  TEXT    NOT NULL CHECK (state IN ('ok', 'empty', 'error')),
  has_payload            INTEGER NOT NULL DEFAULT 0,
  fetched_at             INTEGER,
  checked_at             INTEGER,
  empty_count            INTEGER NOT NULL DEFAULT 0,
  unverified_empty_count INTEGER NOT NULL DEFAULT 0,
  error_reason           TEXT,
  error_status           INTEGER,
  error_at               INTEGER,
  error_count            INTEGER NOT NULL DEFAULT 0,
  stored_bytes           INTEGER,
  raw_bytes              INTEGER,
  history_count          INTEGER NOT NULL DEFAULT 0,
  meta_json              TEXT,
  PRIMARY KEY (event_id, key, sub)
) WITHOUT ROWID;
CREATE INDEX event_slices_not_ok ON event_slices(state, key, event_id) WHERE state != 'ok';

CREATE TABLE entity_slices (
  kind                   TEXT    NOT NULL,         -- tournament|season|team|player|sport
  entity_id              INTEGER NOT NULL,
  key                    TEXT    NOT NULL,
  sub                    TEXT    NOT NULL DEFAULT '',
  state                  TEXT    NOT NULL CHECK (state IN ('ok', 'empty', 'error')),
  has_payload            INTEGER NOT NULL DEFAULT 0,
  fetched_at             INTEGER,
  checked_at             INTEGER,
  empty_count            INTEGER NOT NULL DEFAULT 0,
  unverified_empty_count INTEGER NOT NULL DEFAULT 0,
  error_reason           TEXT,
  error_status           INTEGER,
  error_at               INTEGER,
  error_count            INTEGER NOT NULL DEFAULT 0,
  stored_bytes           INTEGER,
  raw_bytes              INTEGER,
  history_count          INTEGER NOT NULL DEFAULT 0,
  meta_json              TEXT,
  layout                 TEXT    NOT NULL,         -- 'v3' | 'legacy'
  path                   TEXT    NOT NULL,         -- payload file (legacy) or entity directory (v3), relative to DATA_DIR
  PRIMARY KEY (kind, entity_id, key, sub)
) WITHOUT ROWID;

CREATE TABLE slice_history (
  kind       TEXT    NOT NULL,                     -- 'event' or an entity kind
  entity_id  INTEGER NOT NULL,
  key        TEXT    NOT NULL,
  sub        TEXT    NOT NULL DEFAULT '',
  n          INTEGER NOT NULL,                     -- 1-based position in the history file
  fetched_at INTEGER NOT NULL,
  sha256     TEXT    NOT NULL,
  offset     INTEGER NOT NULL,                     -- byte offset of the gzip member
  length     INTEGER NOT NULL,                     -- byte length of the gzip member
  PRIMARY KEY (kind, entity_id, key, sub, n)
) WITHOUT ROWID;

CREATE TABLE changes (
  seq              INTEGER PRIMARY KEY,            -- stable: stored in the change-log line itself
  ts               INTEGER NOT NULL,
  event_id         INTEGER NOT NULL,
  sport            TEXT,
  tournament_id    INTEGER,
  status_regressed INTEGER NOT NULL DEFAULT 0,
  fields           TEXT    NOT NULL,               -- comma-separated changed field names
  row_json         TEXT    NOT NULL,               -- the log line as written
  segment          TEXT    NOT NULL                -- file it came from, relative to DATA_DIR
);
CREATE INDEX changes_event ON changes(event_id, seq);
CREATE INDEX changes_ts    ON changes(ts);

CREATE TABLE pending_writes (                      -- write-intent markers (crash recovery, 6.2)
  kind       TEXT    NOT NULL,
  entity_id  INTEGER NOT NULL,
  started_at INTEGER NOT NULL,
  PRIMARY KEY (kind, entity_id)
) WITHOUT ROWID;

CREATE TABLE legacy_roots (                        -- legacy directories seen by the last scan (incremental re-scan)
  path       TEXT PRIMARY KEY,                     -- relative to DATA_DIR
  kind       TEXT    NOT NULL,                     -- season_dir | league_dir | schedule_dir | seasons_file | flat_event | changes_file
  sig        TEXT    NOT NULL,                     -- "<mtime_ns>:<entry count or size>"
  scanned_at INTEGER NOT NULL
) WITHOUT ROWID;
```

`legacy_roots` as built (ST-08). Four kinds are written: `league_dir` (`matches/<league>`: its season
directories and summary files), `schedule_dir` (a season directory with round and page files), `seasons_file`
(each season-list file and `league_seasons.csv`) and `changes_file` (`score_changes.jsonl`). `season_dir` and
`flat_event` are not used: the signature of an event directory is `events.sig`. `sig` formats are in 3.5.
`scanned_at` is the clock, like `meta.built_at`, so a rebuild does not reproduce it.

The catalog's `meta` table holds `derive_version` and, written by a build, `built_at` (epoch seconds),
`built_by` (the application version), `build_mode` and `counts` (JSON, rows per table). `built_at` and
`counts` describe the build: single-event writes (`index_event`, a repair, a reconcile) do not update them.

`state.db`. Migration `0001_initial` (plan item ST-09). The file as built writes every statement as
`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`, as 7.3 requires; the DDL is otherwise as printed.
The keys of `meta` as built are `imported_jobs_db` (7.3), `stream_id` and `stream_pruned` (2.3) and
`imported_watch_state:<sport>` (ST-18). `legacy_follows_sig`, which the comment below still names, is not
written: ST-17 needs no signature of the league files, because a repeated `apply` is one SELECT and no
write (2.3):

```sql
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
) WITHOUT ROWID;                                    -- holds stream_id, imported_jobs_db, legacy_follows_sig

CREATE TABLE follows (
  id          INTEGER PRIMARY KEY,
  kind        TEXT    NOT NULL CHECK (kind IN ('tournament', 'team', 'player', 'event')),
  entity_id   INTEGER NOT NULL,
  sport       TEXT,                                 -- registry slug; NULL = not known yet
  name        TEXT    NOT NULL,                     -- display label (the name column of leagues.txt)
  seasons     TEXT    NOT NULL DEFAULT 'all',       -- 'all' | 'current' | 'last:N' | JSON array of season ids
  slices_json TEXT,                                 -- NULL = defaults; else a JSON selection override
  live        INTEGER NOT NULL DEFAULT 0,
  enabled     INTEGER NOT NULL DEFAULT 1,
  origin      TEXT    NOT NULL DEFAULT 'api' CHECK (origin IN ('legacy', 'config', 'api')),
  position    INTEGER NOT NULL,                     -- display and file order
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  UNIQUE (kind, entity_id)
);
CREATE UNIQUE INDEX follows_tournament_name ON follows(name) WHERE kind = 'tournament';
CREATE INDEX follows_position ON follows(position, id);

CREATE TABLE jobs (                                 -- the columns of today's .meta/jobs.db (sofascore_scraper/web/jobs.py:105-124)
  id                        TEXT PRIMARY KEY,
  status                    TEXT    NOT NULL,
  progress                  INTEGER NOT NULL DEFAULT 0,
  current_task              TEXT    NOT NULL DEFAULT '',
  payload_json              TEXT,
  log_json                  TEXT    NOT NULL DEFAULT '[]',
  result_json               TEXT,
  started_at                TEXT,
  finished_at               TEXT,
  cancel_requested          INTEGER NOT NULL DEFAULT 0,
  matches_total             INTEGER NOT NULL DEFAULT 0,
  matches_done              INTEGER NOT NULL DEFAULT 0,
  matches_failed            INTEGER NOT NULL DEFAULT 0,
  schedule_empty_seasons    INTEGER NOT NULL DEFAULT 0,
  circuit_breaker_triggered INTEGER NOT NULL DEFAULT 0,
  circuit_breaker_reason    TEXT,
  eta_seconds               REAL,
  current_batch             TEXT    NOT NULL DEFAULT '',
  kind                      TEXT    NOT NULL DEFAULT 'fetch',   -- new in state.db
  owner                     TEXT                                -- new: lease holder id of the process running it
);
CREATE INDEX jobs_started ON jobs(started_at DESC);

CREATE TABLE stream_events (                        -- every durable stream: live, change, job, system
  seq           INTEGER PRIMARY KEY AUTOINCREMENT,  -- one sequence for all streams
  stream        TEXT    NOT NULL,
  ts_ms         INTEGER NOT NULL,                   -- when we recorded it (epoch ms, UTC)
  type          TEXT    NOT NULL,                   -- live.status_changed | change.recorded | job.finished | ...
  event_id      INTEGER,
  sport         TEXT,
  tournament_id INTEGER,
  source        TEXT,                               -- push | poll | job | system
  dedup_key     TEXT,
  payload_json  TEXT    NOT NULL
);
CREATE INDEX stream_events_stream ON stream_events(stream, seq);
CREATE INDEX stream_events_event  ON stream_events(event_id, seq);
CREATE INDEX stream_events_ts     ON stream_events(ts_ms);
CREATE UNIQUE INDEX stream_events_dedup ON stream_events(stream, dedup_key) WHERE dedup_key IS NOT NULL;

CREATE TABLE sink_cursors (
  sink       TEXT PRIMARY KEY,
  seq        INTEGER NOT NULL,                      -- last delivered stream_events.seq
  updated_at INTEGER NOT NULL,
  last_error TEXT
) WITHOUT ROWID;

CREATE TABLE watch_state (
  watcher    TEXT    NOT NULL,
  event_id   INTEGER NOT NULL,
  state_json TEXT    NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (watcher, event_id)
) WITHOUT ROWID;

CREATE TABLE runtime (                              -- small cross-process facts (last bridge health snapshot)
  key        TEXT PRIMARY KEY,
  value_json TEXT    NOT NULL,
  pid        INTEGER,
  updated_at INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE leases (
  name         TEXT PRIMARY KEY,
  holder       TEXT    NOT NULL,
  pid          INTEGER NOT NULL,
  host         TEXT    NOT NULL,
  purpose      TEXT    NOT NULL DEFAULT '',
  acquired_at  INTEGER NOT NULL,
  heartbeat_at INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE migration_runs (
  id            INTEGER PRIMARY KEY,
  started_at    INTEGER NOT NULL,
  finished_at   INTEGER,
  dry_run       INTEGER NOT NULL,
  delete_legacy INTEGER NOT NULL,
  events_done   INTEGER NOT NULL DEFAULT 0,
  events_failed INTEGER NOT NULL DEFAULT 0,
  bytes_before  INTEGER NOT NULL DEFAULT 0,
  bytes_after   INTEGER NOT NULL DEFAULT 0,
  report_json   TEXT
);
```

Migration `0002_job_manager` as shipped (P11, #69; `sofascore_scraper/store/migrations/state/0002_job_manager.sql` at
`e0bae0c`; the job model is in `02-services.md` 2.8). The statements are the file's; its comments are
Turkish and are given here in English:

```sql
ALTER TABLE jobs ADD COLUMN origin_json TEXT;       -- face (cli|api|scheduler|library), pid, host
ALTER TABLE jobs ADD COLUMN spec_json TEXT;         -- the service spec
ALTER TABLE jobs ADD COLUMN error_json TEXT;        -- {code, message, details}
ALTER TABLE jobs ADD COLUMN heartbeat_at INTEGER;   -- epoch ms; for display only (liveness is the row plus the lease)
ALTER TABLE jobs ADD COLUMN created_at TEXT;        -- ISO-8601 UTC; NULL for rows written before 0002 (readers use started_at)

CREATE TABLE IF NOT EXISTS job_events (
  job_id    TEXT    NOT NULL,
  seq       INTEGER NOT NULL,                       -- starts at 1 for each job
  ts_ms     INTEGER NOT NULL,
  type      TEXT    NOT NULL,                       -- started | phase | progress | log | failed | breaker | cancel_requested | finished
  data_json TEXT    NOT NULL,
  PRIMARY KEY (job_id, seq)
) WITHOUT ROWID;
```

With it `state.db` is at schema version 2 and `.meta/schema.json` says `state_schema: 2`. The migration can
be run again: the runner skips an `ADD COLUMN` whose column exists (7.3). Since P11 the two columns that
0001 added are written as well: `kind` is the kind of the job, `owner` the 32-character holder id of its
writer lease (it was NULL before). The first version of this section printed the table without
`IF NOT EXISTS` and without the unit of `heartbeat_at`. What the upgrade of an existing directory does is
in 7.3. The next state migration is 0003.

`migration_runs` as used (ST-23, #110): every real run of `migrate` leaves one row with its counts and a
JSON summary in `report_json` (`report_summary`); a dry run writes nothing, `state.db` included, so the
`dry_run` column is always 0. No item added a state migration in batches eleven to nineteen; the next one
is still 0003. Since FX-13 (#152) `Migrator.last_run()` (`sofascore_scraper/store/migrate.py:390` at `b6caf2f`) gives the
newest real run without `report_json`; `ssc status` and `/api/v1/status` show it. No item of this revision
added a state migration either.

Notes on the event row:

- It is produced by one pure function, `derive.event_row(payload, source="event", observed_at=None, *,
  sport=None)`. `sport` is a fallback for a payload that names no sport (a listing row of a known tournament);
  the payload's own sport wins. The function returns exactly `derive.EVENT_DERIVED_COLUMNS`; the other columns
  (`status_regressed`, `stale`, `listed_in`, `layout`, `path`, `legacy_path`, `sig`, `first_seen_at`,
  `updated_at`) are the indexer's. A payload without an integer id raises `PayloadCorrupt`. This is the single
  place where a score is read out of a payload. `status_class` comes from `sofascore_scraper.status.classify_status` and
  `scores_json` from `extract_scores`. `home_score`/`away_score` are what the column comment says,
  `homeScore.display`, else `.current`: no field of the score sheet is that value for all three sports.
  `scores_json` holds `{"family": ..., <sport-specific fields>}` with sorted keys; the five common fields of
  the score sheet are not repeated in it, and it is NULL for a sport without a score family.
- `seasons.sort_key` is `derive.season_sort_key(year)`. It equals `SeasonFetcher._get_sortable_year_value`
  wherever that function returns a number, and returns 0.0 where that function raises (`'ab/cd'`, a
  non-string) or returns NaN (`'nan'`).
- `home_score_current`, `away_score_current`, `stage_name` and `listed_in` existed so that the legacy list
  routes and the legacy CSV could be reproduced from the catalog: the 2.x summary wrote `homeScore.current`
  (in football that value includes penalties, see the docstring at `sofascore_scraper/status.py:140`),
  `tournament.name` and, for seasons fetched as event pages, the page name as the round. 3.1 (P30) dropped the
  two score columns with the legacy `/api` routes (catalog schema 2, a rebuild). `stage_name` and `listed_in`
  stay: schema v1 prints `stage.name` from the first, and the listing rules of 8.2 (`stale`, the reconcile of a
  season's pages, the "listed" events of the planner) read the second.
- When an event has an `/event/{id}` payload, its row always equals `derive(event payload)`. A listing never
  overwrites such a row (8.2).
- `first_seen_at` and `updated_at` are taken from the manifest (`created_at`, `updated_at`) or, for a listing
  row, from the `fetched_at` of the listing payloads, so a rebuild reproduces them. A legacy event has no
  manifest: its `first_seen_at` and `updated_at` are the oldest and the newest mtime of its payload files.
  For a legacy slice, `fetched_at` is the file's mtime, `checked_at` is the newest of the payload's mtime,
  the empty mark's time and the error mark's time, `raw_bytes` is NULL, and `stored_bytes` is the size of the
  `.json` file (NULL for a slice that lives in the combined file); the slice's `path` is the event directory
  and its `sub` is empty. For a v3 row `path` is NULL and `legacy_path` names a superseded legacy directory.
  What a rebuild does not reproduce: `sig` when files were touched, `legacy_roots.scanned_at` and the build
  facts in `meta`.
- Values SQLite cannot store would abort a build: a lone surrogate from a JSON escape (UnicodeEncodeError
  when the value is bound) and integers beyond 64 bits (for example a huge count in `_unavailable.json`). The
  indexer replaces the former with `?` and clamps the latter; `verify` derives the same values, so such a
  row is not reported as a mismatch.

### 3.4 Rebuild

`CatalogAdmin.rebuild()` needs the `maintenance` lease (6.1). As built the indexer takes no lease itself:
leases belong to the facade, and the caller holds `store.lease('maintenance', purpose='op:rebuild')` around
the call. Since ST-11 (#75) two callers do: the build on open (below) and `scripts/catalog_tool.py`, which
ST-23 (#110) replaced by `ssc catalog rebuild [--mode]` (`MaintenanceService.rebuild_catalog`). Two
calls run without the lease, both in place: the build on open when another holder has the directory, and
the hook after a clear, whose caller holds `maintenance` already (3.5). Two modes, chosen by `mode="auto"`
or forced with `"in_place"` / `"recreate"`:

- **In place** (catalog opens, schema matches): one `BEGIN IMMEDIATE` transaction deletes all rows and
  re-inserts them. Readers in other processes keep seeing the old, consistent catalog through WAL snapshot
  isolation until the commit. A failure rolls back to the old catalog. No file is swapped, so this works on
  Windows too.
- **Recreate** (catalog missing, corrupt, or from another schema version): build `catalog.db.build`, close
  every connection of the catalog, remove the old `-wal`/`-shm` files, then `os.replace` the build over
  `catalog.db`. The sidecars go first: SQLite opens an existing `-wal` file whichever database it was written
  for, so a stale one must never sit next to the new file (reasoned from SQLite's behaviour, not reproduced).
  On Windows the replace fails while another process has the old file open; the error says so. A shared
  catalog reopens on its next use.

`ensure()` rebuilds only when the catalog is not usable and otherwise does nothing. It was meant as the
call for an open; as built the facade does not call it, because it chooses the mode itself (below).
`should_stop` ends a build early and leaves the old catalog as it was (`RebuildReport.completed` is
false). `progress(stage, done, total)` reports the stages `scan`, `listings`, `v3_events`, `legacy_events`
and `finish`.

Scan order (deterministic, so two rebuilds of the same tree give the same rows):

1. `v3/tournaments/**`: tournament and season manifests, season lists, schedule pages.
2. Legacy `seasons/*.json`, then legacy `matches/**` round and page files. Summary CSVs are read only for a
   season that has no round or page JSON at all (old `_matches.csv` data).
3. `v3/teams/**`, `v3/players/**`, `v3/sports/**`.
4. `v3/events/**/manifest.json`: event row from `event.json.gz`, slice rows from the manifest, participants,
   and tournament/season rows where none exists yet.
5. Legacy `match_details/**` in all five forms (5.1). An id already found in step 4 is not indexed again; its
   legacy directory is recorded as `legacy_path` (superseded).
6. Change log: legacy `score_changes.jsonl`, then `changes/*.jsonl` in name order.
7. History files: one `slice_history` row per gzip member. As built they are read with the v3 event of
   step 4 (below).
8. `stale` flags (8.2), `ANALYZE`, `meta` (`built_at`, `built_by`, `build_mode`, `derive_version`, counts).

As built (ST-07 #49, ST-08 #61) the order is: season lists; the listings of the seasons that have round or
page files; v3 events; legacy events; the seasons that have only a summary CSV; the change log. Details that
the list above does not say:

- **Only what has a writer is indexed.** Steps 1 and 3 (v3 entity directories) have no writer yet and are
  not scanned; they come with ST-22. ST-20 (#82) already wires them: the indexer looks the two entry points up
  by name in `entities.py` (`V3_ENTITY_SCAN = "apply_v3_entities"`, `V3_ENTITY_INDEX = "index_v3_entity"`,
  `sofascore_scraper/store/indexer.py:197-198` and `:1203-1215` at `9b03c64`), because the functions do not exist until
  ST-22; a missing function means that source does not exist yet. The `changes/<yyyy>-<mm>.jsonl` segments of
  step 6 are indexed since ST-20 (8.5). As built since ST-22 (#98) both functions exist in
  `sofascore_scraper/store/entities.py`, and the order differs from steps 1 to 3: `apply_v3_entities` indexes the slices
  of v3 entity directories that are not listings, and their history files; the season lists and schedule
  pages of both layouts are applied together, in `apply_season_lists` and in `read_season` /
  `apply_season`, where the precedence of a v3 copy over a legacy one is decided (`EntityStore.put` as
  built, 2.3). The listing scan also takes the seasons that have v3 pages as paged seasons
  (`entities.v3_schedule_seasons`), and the stale check reads a v3 page through the season directory of its
  row. `index_v3_entity` re-indexes one tournament's season list or one season's pages, so pending markers of
  every kind are now recovered (3.5).
- **History rows are not a step of their own** (ST-26, #92). They come with each v3 event record
  (`read_v3_event`, `sofascore_scraper/store/indexer.py:470-500`), so the re-index of one event and a reconcile keep them
  right as well as a rebuild. A history file that cannot be read does not make the event invalid; it has no
  rows and is not reported. Indexing a member means decompressing and parsing it: about 0.15 to 0.2 s more
  per rebuild for 3,000 events with 5,000 snapshots.
- **Listings are written per season, before the events.** Tournament and season rows from an event payload
  are written 'only where none exists', so the listings, which are their real source, go first. Inside a
  season the pages are applied in the reader's order (mtime, then path); a global `fetched_at` order over
  the whole tree could not hold together with re-listing one season in a reconcile. Rows from listings
  follow 'the newest page wins' for tournaments and for seasons that are not in a season list; participants
  and participant links are written for listing rows too.
- **Summary-only seasons come after the events**, not in step 2: their rows do not name a sport and take it
  from the tournament row. "No round or page JSON" means no such file for that season in any directory (the
  fixture's season has no directory at all). Only the CSV is read, not `*_summary.json`. A summary row
  carries no team ids, no status type or code and no score detail, and its `match_date` is read back as
  local time (the inverse of today's writer), so its `start_ts` is right only when the process runs in the
  time zone the file was written in. Since ST-22 (#98) the per-season summary JSON and since #102 the
  summary CSV are no longer written (decision S4), so these files are only what 2.x and earlier 3.x builds
  left; the indexer and the legacy reader still read them where they are the only source.
- **A valid v3 event** is a directory whose manifest can be read, whose `event` slice is `ok` and whose
  `event.json.gz` can be read and carries the same id. A directory that fails this is reported as a problem
  and the legacy copy, if there is one, is indexed instead: there is no event row to hang an `error` /
  `corrupt` slice on. A rebuild reads only the manifest and the event payload of a v3 event, so a broken
  payload of another v3 slice is found by `verify(deep=True)`, not by a rebuild.
- **Several legacy directories of one id.** The one the duplicate rule prefers (5.1) is indexed; when a v3
  copy exists, `legacy_path` is that preferred directory. The choice is made from `stat` only, without
  reading the files, so that quick verify can repeat it. The rule is repeated in `indexer.legacy_order`
  (the reader's `iter_events` cannot give it without reading); a test pins that the two agree on every
  fixture and on a tree with ties and unreadable copies.
- **Participants.** A participant row is replaced only by an event payload that is at least as new
  (`updated_at`, which for a v3 event is the `fetched_at` of its `event` slice), so the result does not
  depend on the scan order. Sport, category, tournament and season rows are inserted only where none exists.

A file that cannot be read or parsed does not stop the rebuild. It is listed in the report and, for a legacy
slice file, its slice row gets `state = 'error'`, `error_reason = 'corrupt'`, which makes it a re-fetch
candidate.

A rebuild runs automatically on open when the catalog is missing, its `user_version` differs, its
`derive_version` in `meta` differs from the code's, or `PRAGMA quick_check` fails after an unclean shutdown.
`Catalog.prepare()` (ST-06) only reports that state and never rebuilds; the facade does. A schema that was
created but never filled keeps reporting `derive_version` until `stamp_derive_version()` is called as the
last step of a build, so an interrupted build never looks usable. The final `ANALYZE` of a build is
`ANALYZE main`: with `state.db` attached, a plain `ANALYZE` would also write statistics for `state.db` (see
3.7).

The build on open, as built (ST-11, #75; `Store._sync_catalog` and `Store._build_catalog`,
`sofascore_scraper/store/api.py:298-383` at `e0bae0c`):

- **When.** `open_store` builds the catalog when it is not usable: the file is missing, its schema or
  derive version is another one, it cannot be read, `quick_check` reports damage after an unclean writer
  (3.5), or the reconcile of a usable catalog raises `CatalogCorrupt`. A usable catalog is reconciled
  instead (3.5). After a build no reconcile follows; the build has read the whole tree.
- **Lease and mode.** Only the rebuild takes a lease: `maintenance` with purpose `op:rebuild`, without a
  wait. Under the lease the mode is `auto` (in place when the file opens and its schema fits, recreate
  otherwise), and `recreate` for a corrupt file.
- **When another holder has the directory.** A web job takes `writer` first and opens the Store afterwards,
  so its open cannot get `maintenance`. If the schema fits, the catalog is then built in place without the
  lease: it is one write transaction, SQLite's write lock orders it against the hooks of the other writer,
  and a hook runs after its file is written, so the result is the same. If the file has to be recreated,
  nothing is built: a replaced file would lose the rows written meanwhile. One warning is logged, and the
  catalog stays unusable until the directory is opened while nothing else runs.
- **A catalog that cannot be synced never fails the open**, and never fails a write. A storage error
  (`StoreError`, `sqlite3.Error`, `OSError`) is one warning per Store, which says that the catalog could
  not be brought up to date and is retried later; further ones are DEBUG. The hooks leave such a catalog
  alone and try the sync again after 30 s (`CATALOG_RETRY_SECONDS`, `sofascore_scraper/store/api.py:95`), and so does a
  later `open_store` call for the open Store. Any other exception of the indexer is logged with its
  traceback and that Store does not use its catalog again; in the check mode of the test suite it is raised.
  The "never fails a write" half described the shadow mode, where the 2.x writers wrote the files and a
  hook indexed them afterwards. Since ST-21 (#104) and ST-22 (#98) the writers call `put`, which updates the
  catalog inside the write: a catalog that stays locked fails that write with `StoreBusy` (2.3, "Callers").
- **Logging.** The summary line of a rebuild in the indexer is DEBUG and English (`Catalog rebuilt ...`;
  it was INFO and Turkish, and nothing called it). The facade writes the one INFO line, once per build and
  only when the directory has data:
  `Store: Catalog built from the files in <dir>: 423 events with details, 628 listed only, 0 problems`.
- **Cost.** The first open of a directory that has data pays for the build once: 0.75 to 0.8 s on a copy
  of the owner's data (1.3 ms per event directory; the same open took 17 ms before #75), 5.6 s with 4,230
  event directories, and by extrapolation about 2 minutes with 100,000 (section 11).

Measured cost (warm page cache). The prototype indexed the local legacy tree (90 schedule files, 423 event
directories, 1,051 events in total) in 0.22 s and read one v3 event (manifest + `event.json.gz`) in 0.11 ms,
from which 100,000 v3 events were estimated at 15–30 s of CPU. As built the numbers are higher. The 423 legacy
event directories alone take 0.56–0.69 s, because the state of a legacy slice needs the file's content (the
presence predicate), so every slice file is read; with the listings the whole tree takes 0.7–1.0 s. A
synthetic v3 tree of 20,000 events with 7 slice entries each rebuilds in 7.0 s in either mode, 0.35 ms per
event, which is about 35 s per 100,000; about a third of that is manifest validation and derive. With a cold
cache the 200,000 file opens dominate and the time depends on the disk. That was not measured (open question).

### 3.5 Reconcile (incremental)

Cheaper than a rebuild; brings the catalog up to date with files that changed behind its back.

- **Pending writes**: on every open, each row of `pending_writes` is re-indexed from disk and removed.
- **Legacy trees**: for each legacy season directory and each legacy event directory the signature
  (`mtime_ns` and entry count; `os.replace` of a file inside a directory updates the directory's mtime) is
  compared with `legacy_roots.sig` and `events.sig`. Changed directories are re-indexed. Run on open while any
  legacy data exists, because a 2.x process, the terminal UI or a user may still add or remove files there.
- **v3 after an unclean shutdown**: the writer lease creates `.meta/locks/unclean` when it is taken and
  removes it on a clean release. If the file exists when a lease is taken, every v3 manifest is `stat`-ed and
  compared with `events.sig`. Measured: 4 µs per event with a warm cache, so 0.4 s per 100,000 events.
- `reconcile(deep=True)` also compares payload hashes (same work as `verify(deep=True)`).

As built (ST-08 #61): `CatalogAdmin.reconcile(*, deep=False, v3=False) -> ReconcileReport`, one write
transaction.

- **What it does, in order.** Pending writes of kind `event` are re-indexed and removed (rows of other kinds
  are counted and left for the v3 entity writer). Legacy event directories whose signature or preferred
  directory changed, that are new or that are gone are re-indexed. If any season-list file changed, all
  season lists are rewritten. Every season whose schedule directory changed is re-listed, and so are the
  summary-only seasons of a league directory that changed. A season that has listing rows without a sport is
  re-listed when its tournament has become known. `score_changes.jsonl` is indexed again from the start when
  it changed (line numbers, and so `seq`, stay the same). As built at `b3cb819` two things changed. Since
  FX-12 (#100) the event pass first closes the change intents of interrupted writes (`recover_changes`,
  `sofascore_scraper/store/indexer.py:1541`; 6.2), before the pending markers. Since ST-22 (#98) pending markers of every
  kind are re-indexed (`index_v3_entity` for a tournament or a season), so `pending_skipped` stays 0 where a
  season marker was skipped before. v3 entity directories have no signature in `legacy_roots`: a change
  behind the catalog in `v3/tournaments` is found through a pending marker or a deep reconcile, not by a
  quick reconcile.
- **`v3`.** The comparison of every v3 manifest with `events.sig` is asked for by the caller with `v3=True`.
  The design's trigger was a writer lease that reports an unclean predecessor (`Lease.unclean`, 6.1); the
  open, which takes no writer lease, uses another rule (see "On open" below). Without `v3=True` only the
  `legacy_path` column of v3 events is checked. `deep=True` implies it, ignores signatures, reads every event
  and every listing again, and then runs `verify(deep=True, repair=True)`, whose report is in
  `ReconcileReport.verify`.
- **Signatures.** A legacy event directory: `<mtime_ns>:<entry count>` in `events.sig`. A v3 event: the
  manifest file, `<mtime_ns>:<size>`. A root in `legacy_roots`: for a file `<mtime_ns>:<size>`; for a
  directory `<mtime_ns>:<entry count>`, where the time is the newest of the directory's own mtime and the
  mtimes of the source files in it, so that a page rewritten in place is seen. A season-list file that was
  resolved through the name map has `:<tournament id>` appended, so a changed map re-lists it.
- **Signatures are coarse.** A file of a legacy event directory that is edited in place does not change the
  directory's mtime, and two changes within one clock tick can leave the same signature. A quick reconcile
  and a quick verify do not see such a change; the deep forms do. A test pins this limit. Tests that change a
  tree bump the mtimes explicitly, with a value that never repeats for a path.
- **`ReconcileReport`** has `events_checked`, `events_indexed`, `events_removed`, `pending`,
  `pending_skipped`, `season_lists` and `changes` (None when unchanged), `seasons` (the re-listed seasons),
  `problems`, `verify`, `seconds` and the property `changed`. An event whose newest legacy copy cannot be read
  cannot be called unchanged by signature (the catalog points at the second copy), so it is read again on
  every reconcile and counted in `events_indexed`: `changed` is true every time for such a tree.
- **Entity tables only grow.** A reconcile never deletes a tournament, season, participant, sport or
  category row, and rows written 'only where none exists' keep the first writer's values. After files were
  deleted these tables can hold rows a rebuild would not write, and a summary-only listing row keeps a sport
  learned from a tournament whose files are gone (pinned by a test). Apart from that the result equals a
  rebuild of the same tree. ST-11 settled what the comparison with a rebuild does with such rows: it leaves
  them out, and the reconcile keeps them (the comparison rule below).
- **Not handled.** An event listed under two different seasons, or two tournament directories that hold the
  same season id: the last one applied wins, and a reconcile of one season can then differ from a rebuild.
  Neither occurs in the owner's data (1,051 listed events checked). Since ST-22 (#98) `EntityStore.put`
  refuses a season id that already has a v3 directory under another tournament, so the Store's own writer
  cannot make the second case; a tree built otherwise, and an event listed under two seasons, keep the rule
  above.
- `reconcile()` raises `StoreError` when the catalog is not usable. Cost on the owner's data: 13 ms when
  ST-08 measured it, 11.5 ms inside the open (ST-11).
- After a legacy event write, `index_event(event_id, paths=[directory])` re-indexes that event and maintains
  `listed_in` and `stale`. After a legacy schedule or season-list write there is no single-season entry
  point: `sync_listings(kinds)` (ST-11, below) is per kind of source, and the listing scan walks the whole
  `matches/` tree (0.08 s for 3,800 pages).

On open, as built (ST-11, #75; `sofascore_scraper/store/api.py:291-346` at `e0bae0c`). The first version of this section
said that the open calls `ensure()` and then `reconcile()` under the `maintenance` lease.

- **Every open reconciles.** `open_store` reconciles a usable catalog on every open, and builds one that is
  not usable (3.4). Since ST-19 the event half of that reconcile is bounded (decision S17, below). The
  call is `reconcile(deep=False, v3=<unclean>, quiet=True)`: with `quiet` the summary line
  (`Catalog reconciled: ...`) is DEBUG, so the output of a command and the CLI goldens do not change
  when files changed behind the catalog; `reconcile()` called directly still logs it at INFO. A read-only
  Store reconciles as well. `sync_catalog=False` skips the step.
- **The reconcile on open takes no lease.** It is one write transaction, and SQLite's write lock orders it
  against the hooks. Taking `maintenance` on every open would refuse the open of a web job, which holds
  `writer` first, and of any process that opens the directory while another one holds a lease there. Only
  the rebuild takes `maintenance` (3.4).
- **The unclean rule.** `v3=True` is passed when the marker `.meta/locks/unclean` exists and no process
  holds `writer` (`Store._unclean_writer`, `sofascore_scraper/store/api.py:291-296`). The open then runs
  `PRAGMA quick_check` first (2 ms on the owner's data); damage means a rebuild that recreates the file. The
  rule repeats on every open until a writer lease is taken and released cleanly, because only a clean
  release removes the marker (6.1).
- **A catalog that cannot be synced never fails the open** (3.4): one warning, and the hooks try again
  after 30 s.
- **Linear cost.** The reconcile on open stats every legacy event directory: 26 µs each, 11.5 ms of the
  15 ms that a later open of the owner's data takes (1.2 ms before #75), 111 ms with 4,230 event
  directories, and by extrapolation about 2.6 s for every open at 100,000. The plan recorded this as the
  open decision S17; it was settled on 2026-10-02 as chosen, and ST-19 (#90) bounds the pass (next bullet).
- **The bound of decision S17, as built** (ST-19, #90; `Store._reconcile_on_open` and
  `_open_reconciled_recently`, `sofascore_scraper/store/api.py:407-459` at `9b03c64`). An open skips the pass over the
  legacy event directories when an open of the same directory, in this or another process, finished one
  less than `STORE_OPEN_RECONCILE_SECONDS` ago (default 60; `0` checks on every open; an invalid value is
  the default). The half for season lists, schedules and the change log (`sync_listings`) runs on every
  open. The stamp is the catalog's `meta.open_reconciled_at`, `{"at": <epoch>, "dir": <real path>}`,
  written after each full pass and after a build. It holds the real path of the directory, so a directory
  copied together with its catalog (from a backup, by a move, or the five CLI goldens that copy a seeded
  directory and edit it) never skips. The pass is not skipped while a `pending_writes` row exists, after an
  unclean writer, or when the shadow check of the test suite says the directory was edited. Only the open in
  the Store's constructor applies the bound: the re-sync of a hook, the retry in `open_store` and
  `CatalogAdmin.reconcile()` always run the full pass, so `catalog reconcile` (ST-23) must call
  `reconcile()` directly, because `open_store` alone may skip; as built it does (ST-23, #110: `ssc catalog
  reconcile [--deep]` opens the Store with `sync_catalog=False` and calls `reconcile()`). Measured on a
  copy of the owner's data (median of 7 opens): 21.8 ms with the pass on every open, 5.9 ms with the
  bound; nothing was measured at
  100,000 event directories. P30 moves the variable into the Settings, next to `STORE_DURABILITY` and
  `STORE_SHADOW_CHECK`.
- **What the bound hides.** A change that a 2.x process, a hand edit or a process that died between a write
  and its hook makes to the match folders is not seen by an open within that minute; the first open after
  it, or `scripts/catalog_tool.py reconcile` (since ST-23 `ssc catalog reconcile`), sees it. Writers with a
  hook, and since ST-21 and ST-22 the Store's own writers, are always seen. A test or
  harness that edits event directories out of the shadow check's sight (for example in a subprocess) and
  reopens the same path within a minute sets `STORE_OPEN_RECONCILE_SECONDS=0`, or bumps the mtime and calls
  `catalog.reconcile()`; three tests of `tests/test_store_shadow.py` set it through the fixture
  `every_open_reconciles`.
- **Changes while a process runs.** A process that has the Store open sees files changed without a hook
  only at its next open: a league directory deleted by hand next to a running web server stays in the
  statistics until then (RD-4, #78; the file walk used to see it at once), and a season-list file edited by
  hand is seen only after the next season-list write of that process or a restart (RD-5, #79). When a hook
  cannot update the catalog (`catalog.db` locked by another process for more than 5 s), readers see the
  previous state until the retry; before RD-5, `SeasonFetcher` read the file directly.
- **In-place rewrites are not seen by the reconcile on open.** This is the signature limit above, and it
  has a first known case: `_make_provisional` in `tests/characterization/test_fetch_flows.py` rewrites
  `observation.json` with `write_text`, and in the CLI sandboxes the catalog then keeps the old
  `observed_at` (4 of 39 runs that leave a store). Today's writers replace files atomically, which changes
  the directory's mtime. It matters as soon as `refresh_due` reads the catalog (RD-3): a test that edits a
  file in place and then starts a subprocess must replace the file atomically or bump the directory's mtime.
  As built (RD-3, #97) `_make_provisional` (RD-1 #80) is enough under decision S17: the CLI-golden
  sandboxes are copies of a seeded directory, so the stamp `open_reconciled_at` names another folder and
  their first open always runs the full pass.

The hooks of the legacy writers, as built (ST-11, #75; `sofascore_scraper/store/api.py:773-867` at `e0bae0c`). While the
2.x writers still write the files, each write is followed by a hook that re-indexes from disk what was
written, so the catalog is current after `open_store` and after every legacy write:

| Hook | Called after | What it runs |
|---|---|---|
| `shadow_event(data_dir, event_id, directory)` | a match save with its markers (`sofascore_scraper/match_data_fetcher.py:1244`), a marker reset (`:763`), a refresh (`:864`) | `index_event(id, paths=[directory])` |
| `shadow_schedules(data_dir)` | the round and page fetch of a season (`sofascore_scraper/match_fetcher.py:505`) and its summary files (`:603`) | `sync_listings(LISTING_SCHEDULES)` |
| `shadow_season_lists(data_dir)` | a season-list save (`sofascore_scraper/season_fetcher.py:386`) | `sync_listings(LISTING_SEASON_LISTS)` |
| `shadow_changes(data_dir)` | an append to `score_changes.jsonl` (`sofascore_scraper/match_data_fetcher.py:880`) | `sync_listings(LISTING_CHANGES)` |
| `shadow_cleared(data_dir)` | the web API's clear (`src/web/routes/data.py:191` at `e0bae0c`); since ST-19 only the terminal menu's clear, restore and data-directory move (#86) | `rebuild(mode="in_place")`, without a lease |

As built at `b3cb819` none of the five hooks has a caller in the application. ST-21 (#104) removed the
calls of `shadow_event` and `shadow_changes` with the 2.x detail writer (`_append_score_change` and the
marker code went with it), ST-22 (#98) those of `shadow_schedules` and `shadow_season_lists` with the
round, page and season-list writers, #102 the last summary writer, and P26 (#131) the last calls of
`shadow_cleared` with the terminal menu. The writers write through `put` (2.3), `Store.clear` rebuilds the
catalog itself (9.3), and a restore rebuilds it through the same internal path (9.2). The hooks stay as
transition code with their own tests (`tests/test_shadow_cleared_hook.py` since P26) until ST-28 removes
them; `shadow_schedules` is still in the API snapshot and used by `tests/test_data_correctness.py`. The
shadow check of the whole suite (below) still compares every touched directory with a rebuild. ST-28 (#135)
left them because tests called them; FX-15 (#155) removed all five with their tests
(`tests/test_shadow_cleared_hook.py` is gone), so the table above and the list below are history at
`b6caf2f`. Tests that build records in the old layout index the catalog themselves (`tests/catalog_index.py`;
`tests/schedule_runner.py` replaces the menu path), and the shared body `_shadow` survives only as the
wrapper of `Store.clear`'s in-place rebuild (`sofascore_scraper/store/api.py:653`). The list below describes the hooks as
ST-11 built them:

- **Eight call sites**, not "one line per site": five of them sit in a `finally` (the three of
  `shadow_event`, the round fetch and the clear), so that a write that fails half way, or a fetch that is
  cut short, still indexes what reached the disk; `fetch_all_rounds_parallel` got a `try` around its one
  statement for that.
- **A hook opens the Store** of the directory (the registry gives the open one) and never fails the write.
  A storage error is one warning per data directory until a hook succeeds again; the next hook or the next
  open reconciles. When the catalog is not in sync, a hook runs the whole sync instead of its own step, at
  most every 30 s. A hook waits up to 5 s, the busy timeout, when another process holds the write lock of
  `catalog.db`, for example during that process's first build.
- **`sync_listings(kinds)`** is the listing half of a reconcile (the season lists, the schedule directories
  and the change log) without the pass over the event directories; only roots whose signature in
  `legacy_roots` changed are indexed again (`sofascore_scraper/store/indexer.py:1387-1410`). It is per kind of source
  (`LISTING_SCHEDULES`, `LISTING_SEASON_LISTS`, `LISTING_CHANGES`), not per season: a schedule hook lists the
  whole `matches/` tree without reading a file (1.6 ms on the owner's data) and re-indexes the seasons whose
  signature changed. `index_event` also re-lists a season whose summary rows can take a sport from the
  tournament that the event just brought; the comparison with a rebuild found that difference.
- **`shadow_cleared` rebuilds in place.** A reconcile never deletes entity rows, so the tournaments of the
  cleared trees would stay. It took no lease because the clear route held `maintenance`. As built since
  ST-19 (#90) the web clear no longer calls it: the route calls `Store.clear`, which deletes inside the
  Store and rebuilds the catalog in place under its own `maintenance` lease (9.3), so it needs no outside
  hook. `shadow_cleared` now follows only the terminal menu's clear, restore and data-directory move
  (`sofascore_scraper/store/api.py:1041-1049` at `9b03c64`). The menu takes no lease, so these rebuilds run without one,
  as the menu's downloads already write without one. On a directory that was never opened in the process
  (the usual case of a move) the hook builds the catalog on open and then rebuilds it, so the tree is read
  twice. Its warning still says "after a clear", which `tests/test_settings_ui_catalog.py` pins.
- **`shadow_changes` re-indexed the whole change log** on every append, because `changes.index_legacy`
  started at the first line: 13 ms at 1,000 lines, 170 ms at 10,000, 2.2 s at 100,000 (the owner's directory
  has no such file). Since ST-20 (#82) the index is incremental (8.5): an append to a 100,000-line
  `score_changes.jsonl` with its hook costs 0.57 to 0.75 ms. The first open after the upgrade indexes the
  log once from the start, quietly.
- **The terminal UI's hooks** (#86). The first version of this list said that the terminal UI's clear and
  restore (`_clear_all_data`, `_clear_selected_data` and `restore_data` in `src/ui/settings_ui.py`) change
  the indexed trees without a hook and that ST-19 would move them onto the Store. #86 added the hook instead:
  the two clears, the restore and a fourth function, `_change_data_directory` with "move data", which copies
  the indexed trees into another data directory, call `shadow_cleared`, also when they stop half way; tests
  run them now. Before #86 the statistics of the same terminal session showed the old counts after a clear
  or a restore until a restart (RD-4). The menu still deletes and copies with `shutil`, takes no lease and
  also empties `datasets/` and `reports/`; ST-21 moves its clear onto `MaintenanceService(store=...).clear`
  under the maintenance lease and keeps `datasets/` and `reports/` in the menu. ST-19 could not: the test of
  #86 makes the clear fail half way by replacing the module's own `shutil.rmtree`, which a clear inside the
  Store does not use. As built, ST-21 (#104) did so: the menu's two clears called
  `MaintenanceService(store=...).clear` under `maintenance` (one `all` clear when all three trees were
  selected, otherwise one clear and one catalog rebuild per selected scope), removed both layouts, and
  were refused with `LeaseHeld` while another process used the directory; `datasets/` and `reports/` stayed
  the menu's. `Store.clear` removes a legacy tree and creates it again empty, where the menu emptied its
  contents; the visible result is the same. The restore and the move kept `shadow_cleared` until P26
  (#131) removed the menu.
- **Cost of the hooks** on a copy of the owner's data: a match save +1.3 ms, a season's schedule fetch
  +82 ms when the season's directory changed and +1.6 ms when it did not, a season-list save +4 ms.
- **Reads raise no file-system event.** The shadow recorder of `tests/conftest.py` reacts to file-system
  events of product frames. A reader that goes through the Store raises none (RD-1, #80), so a test that
  edits files by hand while the Store is open and then calls such a reader sees the catalog as it was
  before its edit; ST-11's note that such a test needs nothing in process held only while the product read
  files itself. A test reopens the Store (`open_store(dir).close()`, the `reopened` helper of
  `tests/test_tournaments_service.py`), or the next owner of `tests/conftest.py` adds a resync on the
  Store's read entry. An in-place edit before a subprocess needs an atomic replace or a directory mtime bump
  (the in-place limit above). As built (RD-3, #97) the resync is narrowed to the planners: under
  `STORE_SHADOW_CHECK`, `tests/conftest.py` wraps `QueryService.require_current` (installed by a
  session-scoped autouse fixture), so a directory a test edited by hand is reconciled when a planner
  starts. A hook on every Store read broke another test. A test that edits files by hand and then calls a
  reader that is no planner (the match detail, the lists, "missing details") still reopens the Store.

The comparison rule of the shadow check, as built (ST-11, #75; `CatalogAdmin.diff_from_rebuild`,
`sofascore_scraper/store/indexer.py:1414-1442`, and the constants at `:167-169`, at `e0bae0c`). `diff_from_rebuild()`
builds a catalog of the same tree in a temporary file and returns the differences:

- The comparison is by rows, not by `ReconcileReport.changed`: an event whose newest copy cannot be read is
  read again on every reconcile, and its rows do not change.
- `meta` and `legacy_roots.scanned_at` are excluded; they hold the clock of the build and of the scan.
- The five entity tables (`sports`, `categories`, `tournaments`, `seasons`, `participants`) only grow:
  every row that a rebuild writes must be in the catalog; extra rows and differing values are allowed.
- Every other table must be equal row by row.
- A clear rebuilds in place, so after a clear the entity tables equal a rebuild as well.

The whole test suite runs with `STORE_SHADOW_CHECK=1` (`tests/conftest.py`). After every test, for each
data directory that a writer touched, two things are checked: a product write under an indexed root
(`match_details/` without `processed/`, `matches/`, `seasons/`, `score_changes.jsonl`,
`league_seasons.csv`) that no hook followed is reported as "written without a shadow hook afterwards", and
the catalog must equal a fresh rebuild by the rule above. The audit hook of 2.4 has a second recorder for
this; it also tells the Store what the test wrote itself, so that the catalog is reconciled without
trusting signatures before the next product write, and a directory that the test edited after the last
product call is not compared. A full run makes 182 comparisons in 17 test files. The comparison does not
run inside the subprocesses of the CLI goldens, whose environment is an allow-list; checked by hand, all
39 stores those runs leave equal a rebuild once the harness's own in-place edits are reconciled. As built
since FX-15 (#155), with no hooks left, the check notes two things: a product write under a legacy root
from outside the Store (`shadow_written`, `sofascore_scraper/store/api.py:902` at `b6caf2f`; no product code does that
any more, so such a write is reported at the end of the test unless the catalog was synced after it, even
in a directory whose Store was never opened), and the data directories `Store.clear` touched; the report
still says "written without a shadow hook afterwards" (`:965`).

By hand the same calls are reached through `scripts/catalog_tool.py` (`rebuild`, `reconcile [--deep] [--v3]`,
`verify`, `stats`), until the `catalog` commands of ST-23 replace it. On a directory that is a store the
script opens it with `open_store(create=False, sync_catalog=False)`, so that it sees the catalog as found,
passes the league names of the follows, and holds `maintenance` for `rebuild` (`op:rebuild`),
`reconcile --deep` (`op:reconcile`) and `verify --repair` (`op:verify`); while a job or a watcher holds
the directory these are refused with exit code 3. As built ST-23 (#110) removed the script; `ssc catalog
rebuild [--mode] | verify [--deep] [--repair] | reconcile [--deep]` open the Store with
`sync_catalog=False`, take `maintenance` for the calls that write (rebuild, `--repair`, `--deep`; through
`MaintenanceService`), and exit with 1 when an inconsistency is left; a held lease follows the common
error mapping (`02-services.md` 2.6). The script's `stats` command and its mode for a directory that is
not a store have no replacement; `Store.info()` and `ssc status` (P19) cover the counts.

### 3.6 Consistency checks (`verify`)

Invariants:

- I1. Every `events` row with `has_event_payload = 1` has an event directory, and in v3 a manifest whose
  `event` slice is `ok`.
- I2. Every slice row with `has_payload = 1` has its file; the file decompresses, parses, and (v3) its sha256
  and sizes equal the manifest's.
- I3. Every v3 event directory has a catalog row with the same slices and counters as its manifest.
- I4. An event row with `row_source = 'event'` equals `derive(event payload)`.
- I5. No event is `layout = 'v3'` in the catalog while only a legacy directory exists, and the reverse.
- I6. `pending_writes` is empty when no writer is running.
- I7. `changes.seq` is gap-free and equals the `seq` stored in each v3 log line.
- I8. Each `slice_history` row points at a readable gzip member with that sha256.
- I9. No file exists in a v3 entity directory that the manifest does not name (leftover temporary files are
  reported separately).

`verify()` checks I1, I3 (by signature), I5, I6, I7 and `PRAGMA quick_check` on both files. `verify(deep=True)`
checks all of them by reading every payload. `repair=True` re-indexes mismatching entities from the files,
removes leftover temporary files, and marks unreadable payloads as `error/corrupt`. It never deletes a payload.

As built (ST-07 #49, `sofascore_scraper/store/verify.py` at `f286723`):

- The quick form checks `quick_check` on both files, I1, I3, I5 and I6; the deep form adds I2, I4 and I9.
  `VerifyReport.checked` names what was checked. I7 and I8 were not checked then: the change-log index and
  the history index were not filled when the module was written, and with only the legacy change log `seq`
  has legitimate gaps. Listing state (`listed_in`, `stale`, listing rows) is not verified.
- **I7 as built** (ST-20, #82). It applies to the v3 segments `changes/<yyyy>-<mm>.jsonl` only, not to the
  legacy file. The quick form uses the index and the file sizes: it reads the `changes` table once and
  compares each segment's size with the indexed size (60 ms at 100,000 rows; there is no index on
  `segment`); its kinds are `seq_gap` and `seq_unindexed` (an unindexed tail, or a segment changed after it
  was indexed). The deep form reads the segments and adds `seq_mismatch`. A repair indexes the log again
  from its files; a gap in the files themselves cannot be repaired.
- **I8 as built** (ST-26, #92). The quick form stats the history files and reads none; the deep form reads
  each member. Besides the member check (`history_member`, deep) there are two kinds: `history_file` (the
  file is missing or shorter than the rows say, or `n` does not run from 1) and `history_rows` (the catalog
  rows differ from the rows derived from the files). A repair re-indexes the event, history rows included.
  A slice entry whose manifest history mark says count N while the history file is missing is not
  reported: I8 checks catalog rows only, and those are rebuilt from the files.
- I3 by signature covers legacy events as well as v3 ones: an event whose `sig` equals the signature of its
  directory (legacy) or of its manifest file (v3), and whose preferred directory did not change, counts as
  unchanged and none of its files is read. An event whose signature differs is derived again from its files
  and compared with its rows. The limits of signatures (3.5) apply.
- An unreadable file (`problems`) and an unused copy (`superseded`) are information in the report, not
  inconsistencies. An event whose newest legacy copy is unreadable fails the signature comparison every time
  and is read again on every quick verify; it is reported as a problem.
- `repair=True` also needs `deep=True` to mark unreadable v3 payloads. `verify.mark_corrupt` sets the slice
  to `error` with reason `corrupt`, drops its sha256 and both sizes and keeps the file; I9 tolerates the file
  of a slice marked that way. When the marked payload is the event payload of a v3-only event, the directory
  stays on disk, is no valid v3 event any more (3.4) and is reported as a problem by every rebuild and verify
  until the event is fetched again.
- In a v3 event directory any file the manifest does not name, outside `_history/`, is `unknown_file`;
  `.<name>.<random>.tmp` is a leftover that a repair removes. Since ST-23 (#110) `directory_files` skips
  `_extra/` as well (the unrecognised files of a migrated legacy directory, 5.4). Deep verify does not check
  `_extra/`, because the manifest does not name its files; `migrate` checks it when it writes it (read
  back byte for byte) and again before it deletes a legacy copy. I7 exempts both line-numbered change-log
  files, `score_changes.jsonl` and its copy `changes/0000-legacy.jsonl` (8.5).
- A damaged database is not repaired: the catalog is rebuilt.
- Measured on the owner's data (423 events, all legacy; 2,453 slice rows, 2,342 `ok` and 111 `empty`; 69 with
  an observation; no problems, no duplicates): quick verify 0.02 s without reading a file, deep verify 0.59 s,
  catalog 676 KB. On the synthetic tree of 20,000 v3 events: quick verify 0.26 s (13 µs per event), catalog
  18.7 MB.

### 3.7 Query plans for the hot paths

Taken with `EXPLAIN QUERY PLAN`, SQLite 3.53.4, catalog filled with the 1,051 real events plus 300,000
synthetic events and 2.08 million slice rows (200 tournaments × 5 seasons × 300 events, 4 sports), after
`ANALYZE`. Times are best of five, warm cache. The refresh-candidates row was measured again during
reconciliation with the changed index (200,000 synthetic rows).

| Hot path | Query shape | Plan | Time |
|---|---|---|---|
| List a tournament season, newest first | `WHERE tournament_id=? AND season_id=? ORDER BY start_ts DESC, id DESC LIMIT 25` | `SEARCH events USING INDEX events_tournament_season` | 0.03 ms |
| Next page (keyset) | `... AND (start_ts, id) < (?, ?)` | same index, covering | 0.02 ms |
| All events, newest first | `ORDER BY start_ts DESC, id DESC LIMIT 25` | `SCAN events USING COVERING INDEX events_start` | 0.01 ms |
| Same with `OFFSET 50000` (old paging) | | same | 0.34 ms |
| Sport + date range + status | `WHERE sport=? AND start_ts BETWEEN ? AND ? AND status_class IN (...)` | `SEARCH events USING INDEX events_sport_start` | 0.08 ms |
| Several tournaments, finished only | `WHERE tournament_id IN (...) AND status_class IN (...) ORDER BY start_ts DESC` | `events_tournament_season` + temp B-tree for the sort | 0.60 ms |
| Team-name search | `participants.name_folded LIKE ?` → `event_participants` → `events` | `SCAN p USING COVERING INDEX participants_name`, then two primary-key searches | 1.9 ms |
| Events of one participant | `event_participants WHERE participant_id=? ORDER BY start_ts DESC` | primary key | 0.01 ms |
| Events of followed tournaments (`ATTACH state.db`) | `follows JOIN events ON tournament_id` | `follows` unique index, then `events_tournament_season` | 1.8 ms |
| Dashboard card for one tournament | `SELECT count(*), sum(has_event_payload) WHERE tournament_id=?` | `events_tournament_season` | 0.12 ms |
| Dashboard, all tournaments | `GROUP BY tournament_id` | index scan | 41 ms |
| What is missing, one season | events ⋈ required(sport, key) ⟕ `event_slices` | `events_tournament_season`, then primary-key search per (event, key) | 0.83 ms |
| What is missing, whole catalog | same without the season filter | table scan + primary-key searches | 740 ms |
| Slices in error for one key | `WHERE state != 'ok' AND state = 'error' AND key = ?` | `event_slices_not_ok`, covering | 0.18 ms |
| Refresh candidates | `WHERE has_event_payload=1 AND observed_at IS NOT NULL AND observed_gap < ? AND observed_at <= ?` (+ optional `status_class IN (...)`) | `SEARCH events USING INDEX events_unsettled (observed_gap<?)` | 14 ms for 13,686 hits |
| Live events of a sport | `WHERE status_class='live' AND sport=?` | `events_live`, covering | 0.09 ms |
| Export scan, one batch | `WHERE sport=? AND (start_ts, id) > (?, ?) ORDER BY start_ts, id LIMIT 1000` | `events_sport_start` | 0.52 ms |
| Slices of an export batch | `event_slices WHERE event_id BETWEEN ? AND ? AND has_payload=1` (as built: `event_id IN (<page ids>)`, 4.5) | primary key range (as built: primary-key lookups) | 2.8 ms for 6,547 rows (the range form) |
| One event with its slices | `event_slices WHERE event_id=?` | primary key | 0.01 ms |
| Migration work list | `WHERE layout='legacy' ORDER BY id` | `events_legacy` | 0.15 ms |
| Changed since (consumer sync) | `WHERE updated_at > ? ORDER BY updated_at, id` | `events_updated`, covering | — |
| Events that should have started | `WHERE status_class IN ('not_started','live','unknown') AND start_ts <= ?` | `events_open` | — |
| Stream read | `stream_events WHERE stream=? AND seq>? ORDER BY seq LIMIT 500` | `stream_events_stream` | — |

The "what is missing" query, as `EventStore.missing` builds it (ST-30, `sofascore_scraper/store/events.py:818-827` at
`e0bae0c`; the `VALUES` list comes from the caller's `required` mapping, with the position of each key, so
that the keys come back in the caller's order):

```sql
WITH req(sport, key, ord) AS (VALUES ('football','statistics',0), ('football','lineups',1), ...)
SELECT e.id, e.sport, e.has_event_payload, group_concat(r.ord)
FROM events e
LEFT JOIN req r ON r.sport = e.sport OR r.sport = ''
LEFT JOIN event_slices s ON s.event_id = e.id AND s.key = r.key AND s.sub = ''
WHERE e.tournament_id = :t AND e.season_id = :s
  AND e.status_class IN ('completed', 'decided_without_play')
  AND (e.has_event_payload = 0
       OR (r.key IS NOT NULL AND (s.event_id IS NULL
           OR (s.state != 'ok' AND s.empty_count + s.unverified_empty_count < :threshold))))
GROUP BY e.id ORDER BY e.id;
```

The first version printed an inner join with `req`. With it an event that has no payload would not be
returned when nothing is required for its sport; the `LEFT JOIN` keeps it (2.3).

Three spelling rules the tests must pin, because the planner cannot infer them: a query that wants the
partial index `event_slices_not_ok` must contain the literal `state != 'ok'`; a refresh query must repeat the
two conditions of `events_unsettled`; and `events_open` is used only when the query writes
`status_class IN ('not_started', 'live', 'unknown')` as three literals in that order (a reordered list, a
subset or bound parameters do not reach it; likewise `status_class IN ('live')` does not reach `events_live`,
`status_class = 'live'` does). `tests/test_store_query_plans.py` asserts the index name in each plan. Its
negative assertions (a query without the literal does not reach the partial index) will fail if a future
SQLite planner gets smarter; the rule can then be relaxed and the assertion deleted.

One condition on the stream read: the plan uses `stream_events_stream` only while `state.db` has no
statistics. After `ANALYZE` with evenly filled streams, SQLite 3.53.4 picks the rowid range and filters on
`stream`. No code runs `ANALYZE` on `state.db`, and the test pins the index under that condition. Since FX-3
its fixture builds `state.db` from the migration file and fails if the file has `sqlite_stat` tables;
`tests/test_store_streams.py` asserts the same for the single-stream read.

The read API against this table, as built (ST-30, #68). `tests/test_store_read_api.py` captures the
statements that the API really executes, with their bound parameters, and explains them on the synthetic
catalog of `tests/test_store_query_plans.py` (7,200 events, after `ANALYZE`); 14 tests assert the index of
the table for each hot path. Status classes are written into the SQL as literals taken from the
`StatusClass` enum, in the spelling of the partial indexes, and id lists as validated integer literals, so
a list longer than SQLite's limit for bound variables works. Three rows of the table differ from what the
API does:

- **Changed since.** The shape `WHERE updated_at > ? ORDER BY updated_at, id` cannot be expressed:
  `EventQuery.sort` has only the two start orders. `updated_after` is a filter of a start-ordered query, and
  no query of the API uses `events_updated`.
- **Next page (keyset).** The row-value comparison never matches a row whose `start_ts` is NULL, so rows
  without a start time form a second region of the paging that a second query reads (2.3).
- **Events of followed tournaments.** For the ordered listing the planner picks a scan of `events_start`
  with the follow list as a filter, not the join of the table. The count and the unordered forms (`count`,
  `states`) use `events_tournament_season` and the unique index of `follows`.

`EventStore.summary()` and an unscoped `missing()` are full scans by design (the 41 ms and 740 ms of the
table). ST-30 did not repeat the measurements on the 300,000-event catalog and measured nothing with a cold
cache; its plans are pinned on the 7,200 synthetic events. Its times on the owner's data are in section 11.

Catalog size at that scale: 230 MB for 301,000 events and 2.1 million slice rows, about 0.8 KB per event
(`events` 54 MB, `event_slices` 114 MB, indexes the rest; the indexes and the four columns added after the
measurement add an estimated 15–20 MB). The real data gives 1.1 MB for 1,051 events. Catalog write cost:
0.09 ms per event for the intent transaction plus the data transaction (one event row and eight slice rows).

---

## 4. Payload files

### 4.1 Format

- Bytes stored: `json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")`. Key order is
  the order of the response. This is the parsed response serialised again, as today
  (`src/fsutil.py:33-36` at `3ae2599`; `sofascore_scraper/store/codec.py` since ST-03), not the bytes on the wire:
  escapes and number spelling may differ, values do not.
  "Raw" everywhere in the platform (raw export, the `/raw` API routes) means these bytes.
- Compression: `gzip.compress(data, 6, mtime=0)`. `mtime=0` makes the output deterministic on one machine:
  the same payload gives the same file, which keeps backups and `rsync` quiet. Across machines the files are
  not guaranteed identical: the header's OS byte differs between Python versions (3.11 and 3.12 delegate to
  zlib) and the deflate stream may differ between zlib builds, so the manifest's `bytes` may differ too. Tests
  and `migrate` never compare compressed bytes produced elsewhere.
- sha256 is taken over the uncompressed bytes and stored in the manifest; it is the same on every machine.
- An empty `.json.gz` is corrupt: `gzip.decompress(b"")` returns `b""` without an error, and the codec
  treats an empty or whitespace-only result as `PayloadCorrupt`.
- File suffix names the codec: `.json.gz`. The reader dispatches on the suffix and also accepts `.json`
  (legacy) and, when `compression.zstd` or `backports.zstd` can be imported, `.json.zst`. The writer writes
  gzip only in 3.0.

### 4.2 Directory layout

```
DATA_DIR/
  .meta/
    schema.json                     layout and schema versions (7.1)
    catalog.db  state.db            (+ -wal, -shm)
    jobs.db                         2.x job history; read once, never written by 3.x
    locks/                          writer.lock, maintenance.lock, live.lock, watcher-<sport>.lock, sinks.lock, unclean
    tmp/                            staging for promotions, migrations, rebuilds, exports (same file system)
    trash/                          legacy directories on their way out
    pending_changes/<event id>.json change intents of unfinished writes, normally empty (FX-12, 6.2)
  v3/
    events/<id // 1000000>/<(id // 1000) % 1000, 3 digits>/<id>/
        manifest.json
        event.json.gz
        <key>.json.gz
        <key>/<sub>.json.gz
        _history/<key>/<sub or "_">.jsonl.gz
        _extra/...                  unrecognised files of a migrated legacy directory, unchanged (ST-23, 5.4)
    tournaments/<ut id>/
        manifest.json  tournament.json.gz  seasons.json.gz     (tournament.json.gz: not written yet, ST-22)
        seasons/<season id>/
            manifest.json
            schedule/round_12.json.gz   schedule/last_0.json.gz
            standings/total.json.gz     ...
    teams/<id // 1000>/<id>/        manifest.json  <key>.json.gz ...
    players/<id // 1000>/<id>/      manifest.json  <key>.json.gz ...
    sports/<sport id>/              manifest.json  <key>[/<sub>].json.gz
  changes/<yyyy>-<mm>.jsonl         change log segments, uncompressed, append-only
  changes/0000-legacy.jsonl         the copy of score_changes.jsonl that migrate makes (ST-23, 8.5)
  exports/                          outputs of export commands
  backups/                          as today
  seasons/  matches/  match_details/  score_changes.jsonl  watch_*.json*     legacy, read in place
```

Example: event 16416346 lives in `v3/events/16/416/16416346/`. The path depends only on the id, so a renamed
league or season never moves anything. No directory holds more than 1,000 event directories; the first level
grows by one directory per million ids (the local ids span 14–17 million and produce 3 first-level
directories). All names are ASCII digits and fixed words, so they are valid on Windows.

As built at `b3cb819`: the detail writers write `v3/events` since ST-21 (#104), the schedule and
season-list writers `v3/tournaments` since ST-22 (#98); teams, players and sports have no writer yet.
`.meta/pending_changes/` is new with FX-12 (#100; the constant `CHANGE_INTENT_DIR` is in
`sofascore_scraper/store/events.py:648`, not in `layout.py`). It is transient: a catalog rebuild does not read it, only a
reconcile and the next write of the event do (6.2), and a backup leaves it out (9.1). `_extra/` is new
with ST-23 (#110; `layout.EXTRA_DIR_NAME`).

`manifest.json` (uncompressed, pretty, written atomically; measured average 1.2 KB with six slices):

```json
{
  "format": 1,
  "kind": "event",
  "id": 16416346,
  "created_at": "2026-09-29T17:55:14+00:00",
  "updated_at": "2026-10-01T12:00:03+00:00",
  "migrated_from": "match_details/8_LaLiga/season_LaLiga_26_27/16416346",
  "observation": {"observed_at_utc": "2026-10-01T12:00:03+00:00", "change_ts": 1789594338, "status_regressed": false},
  "slices": {
    "event":        {"state": "ok", "fetched_at": "...", "checked_at": "...", "bytes": 1432, "raw_bytes": 7187, "sha256": "..."},
    "pregame_form": {"state": "empty", "checked_at": "...", "empty": {"count": 2, "unverified": 0, "reason": "404", "at": "..."}},
    "statistics":   {"state": "error", "checked_at": "...", "empty": {"count": 1, "unverified": 0},
                     "error": {"reason": "429", "status": 429, "at": "...", "count": 3}},
    "odds_all/1":   {"state": "ok", "fetched_at": "...", "bytes": 911, "raw_bytes": 5120, "sha256": "...",
                     "history": {"count": 12, "last_sha256": "..."}}
  }
}
```

`observation` keeps the two fields of today's `observation.json` (`sofascore_scraper/status.py:260-266`) and the sticky flag.
`migrated_from` is present only for an event that came from the legacy layout.

As built (ST-03), the manifest goes beyond the example in three ways. A slice entry may carry `meta` (the
small JSON that `Outcome.meta` brings, for example `{"complete": true}` of a round). Fields this version does
not know are preserved at every level and written back, which is what lets "adding fields does not bump the
format" (7.1) hold for an older writer. Timestamps keep sub-second precision when they have it.
`manifest.validate()` requires `bytes`, `raw_bytes` and `sha256` together, a payload for state `ok` and an
error mark for state `error`; `write_manifest` refuses an invalid manifest and always writes format 1.

History file: one gzip member per snapshot, each member holding one line
`{"fetched_at": ..., "sha256": ..., "payload": ...}`. Members are appended; the file as a whole is a valid
multi-member gzip stream that `zcat` prints as JSON Lines. The catalog keeps `(offset, length)` per member, so
snapshot `n` is read by decompressing that range only. A member cut off by a crash is detected (`EOFError`)
and ignored by a member-by-member reader; the next append truncates the file to the last good member first.
All of this was checked with the standard library.

### 4.3 Compression and bundling: measurements

Input: the 2,522 slice JSON files of the 423 local events. Python 3.14.7, `compression.zstd` (zstd 1.5.7),
zlib 1.3.1 **zlib-ng** (this build is faster than stock zlib; expect lower gzip compress speeds elsewhere).
The scratch directory was tmpfs, so times are CPU time and `fsync` cost is not included.

Pretty JSON as stored today: 64,630,040 bytes. Compact JSON: 36,280,272 bytes (1.78× smaller before any
compression). These two numbers and the gzip-6 total were measured again during reconciliation and matched
to the byte.

One file per slice:

| Codec | Bytes | vs today | vs compact | On 4 KiB blocks | Compress MB/s | Decompress MB/s |
|---|---|---|---|---|---|---|
| gzip-1 | 8,820,611 | 7.3× | 4.1× | 15.8 MB | 494 | 1,046 |
| **gzip-6** | **5,985,526** | **10.8×** | **6.1×** | **13.3 MB** | **151** | **1,028** |
| gzip-9 | 5,837,196 | 11.1× | 6.2× | 13.2 MB | 86 | 1,014 |
| zstd-1 | 6,378,438 | 10.1× | 5.7× | 13.3 MB | 600 | 1,419 |
| zstd-3 | 6,155,984 | 10.5× | 5.9× | 13.3 MB | 464 | 1,397 |
| zstd-9 | 5,597,113 | 11.5× | 6.5× | 13.2 MB | 87 | 1,538 |
| zstd-19 | 5,316,880 | 12.2× | 6.8× | 12.9 MB | 3 | 1,408 |
| xz-6 | 5,098,292 | 12.7× | 7.1× | 12.5 MB | 6 | 222 |

One bundle per event (all slices in one JSON object):

| Codec | Bytes | vs today | On 4 KiB blocks |
|---|---|---|---|
| gzip-6 | 5,642,293 | 11.5× | 6.6 MB |
| zstd-3 | 5,413,617 | 11.9× | 6.4 MB |
| zstd-9 | 4,754,313 | 13.6× | 5.7 MB |
| zstd-19 | 4,479,444 | 14.4× | 5.6 MB |

End to end for all 423 events (serialise + compress + atomic write; read + decompress + parse):

| Layout | Codec | Write | Read everything | Read only the event payload of every event |
|---|---|---|---|---|
| per slice | none | 0.41 s | 0.34 s | 0.031 s |
| per slice | gzip-6 | 0.68 s | 0.37 s | 0.039 s |
| per slice | zstd-3 | 0.50 s | 0.36 s | 0.037 s |
| per slice | zstd-9 | 0.86 s | 0.36 s | 0.036 s |
| bundle | gzip-6 | 0.58 s | 0.38 s | 0.374 s |
| bundle | zstd-3 | 0.40 s | 0.36 s | 0.364 s |
| today (pretty files, in place) | — | — | 0.36 s | 0.033 s |

Per slice kind, gzip-6 against compact JSON: `point_by_point` 23×, `statistics` 9.5×, `incidents` 6.0×,
`lineups` 5.2×, `basic` 2.8×, `team_streaks` 2.8×, `pregame_form` 1.4×, `h2h` 1.0× (median 69 bytes).
The 90 schedule files: 7.0 MB pretty → 0.43 MB with gzip-6 (16×).

A full conversion of the 423 events to the v3 layout in the scratch directory (parse, compact, gzip-6, write,
read back, compare, manifest, directory rename) took 1.56 s, 3.7 ms per event, and produced 6,470,560 bytes
in 2,864 files: 10.0× smaller in bytes, 4.9× smaller on 4 KiB blocks (14.7 MB against 71.5 MB).

Conclusions:

1. **Compression pays, the codec barely matters.** Reading is not slower than today: parsing dominates and
   decompression runs at about 1 GB/s. Writing costs 1.6 ms per event with gzip-6 against 1.0 ms uncompressed;
   at 5 requests per second that is noise.
2. **gzip-6.** zstd-3 is 2.8 % larger than gzip-6 here, zstd-9 is 6.5 % smaller at the compress speed of
   gzip-9, zstd-19 is 11 % smaller at 3 MB/s. A 6.5 % gain does not justify adding `backports.zstd` for Python
   3.10–3.13 and making every data directory depend on it. Because the suffix selects the codec, zstd can be
   added later without a layout change. (The owner's draft named zstd as the candidate and gzip as the
   fallback, "to be chosen by measurement"; this is that measurement.)
3. **Trained zstd dictionaries** would cut another 29 % overall (64 % on `basic`), but the dictionaries would
   be authoritative files that every reader and every export needs. Rejected.
4. **Per-slice files, not bundles.** A bundle saves 6 % in bytes and halves the block footprint (6.6 MB
   against 13.3 MB), and cuts the file count by six. Against that:
   - every write of one slice becomes a read-modify-write of the whole event. A refresh rewrites 86 KB of
     JSON instead of one 1.4 KB file, and a live service and a download job writing different slices of the
     same event could lose each other's update;
   - reading only the event payload is ten times slower (0.37 s against 0.039 s for 423 events);
   - "export one slice uncompressed" stops being "decompress one file".
   The footprint argument is real on file systems that do not pack small files: 35 KB per event on 4 KiB
   blocks, of which roughly half is block rounding. If per-player slices come into scope, the file count per
   event grows a lot and this decision should be revisited for those slices only (open question).

### 4.4 Atomicity

- A payload file is written to `.<name>.<random>.tmp` in the same directory and moved into place with
  `os.replace`. A reader sees the old file or the new file, never a partial one. This is today's technique
  (`src/fsutil.py:19-30` at `3ae2599`; the shim is gone since ST-28).
- A new entity directory that is created with several files at once (promotion, migration) is built under
  `.meta/tmp/` and moved into place with one directory rename. It appears complete or not at all.
- `manifest.json` is replaced after the payload files. A crash between the two leaves a payload that is newer
  than its manifest entry; the intent marker (6.2) makes the next open re-index that entity, and the indexer
  trusts the file (hash and size from the file, `fetched_at` from its mtime). As built (ST-20, #82) a
  rebuild still takes the slice rows from the manifest (3.4); the file is trusted only where an intent
  marker says a write was unfinished. Then `indexer.heal_v3_event` (`sofascore_scraper/store/indexer.py:548` at
  `9b03c64`), under the catalog's write lock, takes hash and sizes from each readable payload file, its
  `fetched_at` from the file's mtime, registers a payload file the manifest does not name, renews the
  observation when the event payload changed (its time is the file's mtime, so a repeated `put` of the same
  payload with its original, older `fetched_at` is superseded), removes leftover temporary files and
  rewrites the manifest. It leaves alone a directory without a readable manifest, an unreadable payload file
  and a payload the manifest names but the disk lacks; verify reports those. Since ST-26 (#92) it also sets
  each slice's history mark (count and last sha256) from the intact members of the history file, because
  a write interrupted after the append and before the manifest leaves one member more than the mark says. A
  history file without a slice entry in the manifest is left alone and not reported.
- Durability: by default nothing is `fsync`-ed, as today. After a power cut a file may be empty or old; the
  unclean-shutdown reconcile and `verify(deep=True)` detect it through the manifest hash and mark the slice
  for re-fetching. `STORE_DURABILITY=full` adds `fsync` of each file and its directory. The cost of that was
  not measured (the scratch file system was tmpfs). As built, the setting is read from the environment and
  honoured by the Store-layer functions only, not by the 2.x helpers, so today's writers never fsync; it gets
  its line in `.env.example` and its place in the config model when the first writer uses the Store (ST-22,
  P09). ST-22 (#98) added the line to `.env.example` (`normal` or `full`); since ST-21 and ST-22 the
  writers of matches, schedules and season lists honour it, because they write through the Store.
- Windows: `os.replace` fails while another process has the target open. Store readers read a file in one
  call and close it; the writer retries a failed replace up to 10 times with 20 ms pauses, then raises a
  non-fatal `StoreError`. That holds for the Store-layer functions (`write_bytes`, `replace`, `publish_dir`,
  `move_to_trash`). The 2.x helpers that `src/fsutil.py` re-exports must keep raising `OSError`; they raise
  `ReplaceBusy`, a `PermissionError` subclass, which 2.x callers turn into a fatal `StorageError` exactly as
  before the retry existed. One sharing violation can therefore still stop a 2.x job, only more rarely, until
  the writers use the Store (ST-21, ST-22). Since ST-21 (#104) and ST-22 (#98) they do, so a download meets
  the Store's retry and its non-fatal `StoreError`; the 2.x helpers of `src/fsutil.py` remain for the
  callers that ST-28 removes. As built (ST-28, #135) they left the Store: `sofascore_scraper/config_files.py` keeps the
  same atomic write and retry for the configuration files outside `DATA_DIR` and raises the plain
  `PermissionError` when the retries are spent (2.1); nothing under `DATA_DIR` is written by them.
- The retries do not help against a reader that holds the file open in a tight loop: the test
  `test_reader_never_sees_a_partial_file_while_it_is_rewritten` still fails on the Windows runner (an expected
  failure). The six-concurrent-writers test of `tests/test_fsutil.py`, which shares the marker, passes there
  since the retry exists; the marker stays shared and non-strict, with a reason that says so (FX-4). The
  design relies on the first sentence above: Store readers read in one call and close. Since ST-28 (#135)
  both tests are in `tests/test_config_files.py` (`:193`, `:220` at `b6caf2f`) and test the configuration
  helpers, with the Windows markers (`xfail`, non-strict) kept.
- `files.publish_dir` refuses an existing target, even an empty directory, on every platform.
- File mode (decision S12, FX-4, PR #53). Files written by the Store layer follow the process umask: 0644
  under 022, 0600 under 077, 0664 under 002. That covers `files.write_bytes` and so `codec.write_payload`,
  `manifest.write_manifest` and `.meta/schema.json`; from ST-18 until P23 (#91) also
  `watch_state_<sport>.json`, which was written through the Store and is no longer written. The mode is set
  when the temporary file is created (`os.open` with 0666, `files.STORE_FILE_MODE`); there is no `chmod`, and
  the umask is never read, because reading it is only possible by setting it, which is not thread-safe. A
  rewrite gives the file the umask mode again; the old file's mode is not kept. The 2.x helpers
  (`atomic_write_*`) keep 0600, as `tempfile.mkstemp` creates them, and files that hold secrets (`.env`, the
  browser profile) are not written through the Store. Since ST-28 (#135) those helpers are
  `sofascore_scraper/config_files.py`, still 0600, and `sofascore_scraper/store/files.py` has no 0600 path left: every file the Store
  writes follows the umask.
- `publish_dir` cannot normalise a directory that was staged by other means (`mkdtemp`, `mkstemp`, the
  `atomic_write_*` helpers, a copy): such a tree is published with the modes it has (pinned by a test). A
  writer builds an entity directory with `files.new_staging_dir` and fills it through the Store-layer
  functions, so that the published tree follows the umask at every level. A file appended with a plain
  `open(path, 'ab')` follows the umask too; with `os.open`, pass `files.STORE_FILE_MODE`.
- Lock files follow the umask too since FX-8 (#70; decision S15, settled as chosen on 2026-10-02). Until
  then `sofascore_scraper/store/lease.py` created them with mode 0644 whatever the umask, so under umask 002 a second
  account of the group could not take a lease on a lock file the first account had created. Both places
  that open a lock file, the acquire and the probe behind `holder()` and `holders()`, now pass
  `files.STORE_FILE_MODE` (`sofascore_scraper/store/lease.py:317` and `:413` at `e0bae0c`): a new lock file is 0644 under
  022, 0664 under 002, 0600 under 077 and 0640 under 027 (measured by the tests of FX-8). Existing lock
  files keep their mode, so a directory created before the change still refuses a second account until
  `chmod g+w DATA_DIR/.meta/locks/*.lock` is run once, or the lock files are deleted while nothing uses the
  directory (the next lease creates them again). The `unclean` marker is written with a plain `open` and
  followed the umask before FX-8 already.
- The two SQLite files follow the umask since FX-11 (#85; decision S15). Until then SQLite created
  `state.db` and `catalog.db`, and their `-wal` and `-shm` files, with 0644 less the umask, so under umask
  002 they were 0644 while everything else in a fresh directory was 0664 or 0775 (measured in #70). A data
  directory shared by two accounts of one group therefore failed on database writes: with the two files
  read-only for the second account the Store opened and the lease was granted, but the holder row was not
  written (a warning; `lease_holder` then gave a `LeaseInfo` without pid and host), and every write to
  `state.db` or `catalog.db` (a job row, the index) failed; since ST-11 every process that opens the
  directory writes the catalog. As built, `sqlite.create_database_file(path)` (`sofascore_scraper/store/sqlite.py:141` at
  `9b03c64`) creates a missing database file empty, with `files.STORE_FILE_MODE` and `O_EXCL`, before SQLite
  opens it; SQLite keeps the mode of an existing file and gives `-wal`, `-shm` and `-journal` the mode of
  the database file. A new `state.db` and `catalog.db` are therefore 0664 under umask 002, 0600 under 077,
  0640 under 027, and 0644 under 022 as before. Three places call it: `sqlite.connect()` for `catalog.db`,
  the identity probe of `StateDb._check_identity`, a bare `sqlite3.connect` that runs before `connect()` and
  creates a new `state.db` (`sofascore_scraper/store/state.py:228-229`), and the copy `state.db.bak-v<N>` before a
  migration (`:275-276`). `sofascore_scraper/store/sqlite.py` imports `sofascore_scraper.store.files` inside the function, because two
  tests pin the modules that importing it loads. A new place that may create a SQLite file of the data
  directory calls `create_database_file` first; `tests/test_store_sqlite.py` lists the creating places.
- Existing database files keep their mode, with two exceptions that were new files before FX-11 as well and
  follow the umask of the writing process, not the mode of the file they replace or copy: a recreated
  `catalog.db` (`catalog.db.build` put in place of the old file, or a rebuild after deletion) and the copy
  `state.db.bak-v<N>`. `Catalog.inspect` still checks `os.path.exists` and then opens with a bare
  `sqlite3.connect` (`sofascore_scraper/store/catalog.py:372` at `9b03c64`); if the file disappears between the two calls,
  SQLite creates it with its own mode. That is not reachable in normal use.
- Repair of a directory created before FX-11, next to FX-8's line for the lock files above: stop everything
  that uses the directory and run `chmod g+w DATA_DIR/.meta/state.db* DATA_DIR/.meta/catalog.db*` once, by the
  owner of each file or by root; `catalog.db` may instead be deleted while nothing runs (the next open
  rebuilds it). The `*` matters. A process that can read but not write the database files leaves `-wal` and
  `-shm` files behind on every open, because SQLite cannot remove them without write access; such leftovers of
  the second account's failed attempt get the mode of the database file, and with only the two database files
  repaired the next `open_store` fails with 'attempt to write a readonly database' (measured on one account,
  SQLite 3.53.4). Owned by the second account and without group write, they would also stop the first account
  from writing until they are removed or made group-writable; that was derived from the single-account
  measurement, not seen with two real accounts. The deployment pages of P25 should carry both repair lines.
  Nothing was verified with two real accounts of one group; the second account was simulated on one account.
- A `.meta/schema.json` written by a build from before FX-4 stays 0600 until it is next rewritten; nothing
  changes the mode of existing files.
- Temporary files are named `.<name>.<8 random characters>.tmp` and opened with `os.open`; after 100 taken
  names the write fails with a non-fatal `StoreError`.

### 4.5 Raw export, uncompressed

`Exporter.raw` streams; it never loads more than one payload.

- `fmt="tree"`: `<dest>/events/<id>/<key>.json` (and `<key>/<sub>.json`), each file the stored bytes after
  decompression, copied with `shutil.copyfileobj` from `gzip.open`. With `pretty=True` the payload is parsed
  and re-indented. Legacy events are exported through the legacy reader in the same shape.
- `fmt="jsonl"`: one line per slice,
  `{"event_id": ..., "key": ..., "sub": ..., "fetched_at": ..., "payload": ...}`. The payload bytes are spliced
  into the line without parsing.
- The work list comes from the catalog by keyset pagination (`events_sport_start` or
  `events_tournament_season`, then `event_slices` by primary-key range), in one read transaction so the export
  is a consistent snapshot. A long read transaction does not block writers in WAL mode.
- Without the Store, the data stays usable with standard tools: `zcat v3/events/16/416/16416346/statistics.json.gz`.

Normalised exports (JSONL, CSV, Parquet, SQLite) take their rows from the schema layer and are written by
`Exporter.rows`. Parquet needs `pyarrow`, which stays an optional extra; without it the call raises a
`StoreError` that names the package (the services map it to `not_supported`). The SQLite export writes a new
file with the contract's tables; it is not a copy of `catalog.db`, whose schema is internal.

The legacy wide CSV (`all_matches_*.csv`) is not a Store function. It is the `legacy-wide-csv` profile of
`ExportService` (`02-services.md` 2.7), which reads events through the Store.

As built (ST-25, #108; `sofascore_scraper/store/export.py` at `b3cb819`; signatures and report types in 2.3). Where it
differs from the text above:

- **The work list.** One catalog read transaction (`Catalog.read()`) covers the whole export, which pages
  through the events with the keyset pages of `EventStore` in the order of the query (`q.cursor` is
  honoured, `limit` and `offset` are ignored, as in `iter`). The slices of a page are looked up with
  `event_id IN (<page ids>)`, which are primary-key lookups, not a primary-key range: a page sorted by start
  time does not hold a contiguous id range (3.7).
- **v3 payloads are streamed in both formats.** A tree file is unpacked with `gzip.open` in 64 KiB chunks,
  and a JSONL line gets its payload copied in chunks too, not read whole: `codec.read_raw` measured about
  four times the payload size at its peak, because `gzip.decompress` builds and joins buffers. A JSONL line
  whose payload turns out to be corrupt is truncated back. `fetched_at` is the catalog's time as UTC
  ISO 8601.
- **Legacy payloads are re-encoded.** "Each file the stored bytes after decompression" holds for v3. A
  legacy file is indented 2.x JSON, so a legacy payload is read through the legacy reader (combined files
  included) and written as the canonical bytes v3 stores (4.1); otherwise the legacy and v3 copies of an
  event could not give identical output. They do: tree, pretty tree and JSONL are byte-identical before
  and after `promote_legacy` on both legacy fixtures (`fetched_at` agrees because promotion keeps the
  whole-second file times), and after `store.migrate.run()` (SC-2, #130). A legacy export holds one
  payload at a time while it is parsed and re-encoded (section 11).
- **Skip, do not fail.** A payload that is missing or corrupt after the snapshot (an empty gzip included) is
  skipped with a warning and listed in `ExportReport.skipped` (`ExportSkip`, reason `missing` or
  `corrupt`), and the export goes on, leaving no partial file or line. The design did not say.
- **Writing to a path.** The output is built under a hidden name next to the target and renamed into place
  at the end, not in `.meta/tmp`: `.meta/tmp` is on the same file system only as a target inside
  `DATA_DIR`, so the final rename into an outside target could fail. A target that exists raises
  `StoreError` unless `overwrite=True` (an empty directory does not count as existing; on failure the old
  target is kept), because callers outside the Store may not delete files (2.4) and 9.3 says that an export
  can replace its previous output. Only a SQLite or Parquet export to a stream stages under
  `.meta/tmp/export.<random>` (the `export` label of decision S16), is copied into the stream, and the
  staging directory is removed; JSONL and CSV go straight into a stream, which is never closed.
- **Rows.** `rows` reads its rows once, as a stream, in `columns` order; a missing key is None, other keys
  are ignored, dates and times are ISO text. CSV is UTF-8 with `\n`, None as an empty cell, booleans
  `true` / `false`, dicts and lists as canonical JSON. SQLite is a new file with one table (`table`, names
  quoted), 1,000 rows per insert in one transaction, integers beyond 64 bits as text. Parquet infers the
  column types (bool, int64, float64, string) from the first 10,000 rows, or takes them from `types`
  (SC-2, #130), so an empty export has the same schema as a full one; a later value that does not fit
  raises a `StoreError` that names the column. Without `pyarrow` the call raises
  `StoreError(..., detail="pyarrow")` before a row is read; the optional extra is `parquet = ["pyarrow>=14"]`
  in `pyproject.toml`. Parquet was verified with pyarrow 25.0.1 only, and CI does not install it. As built
  since FX-17 (#145) and FX-15 (#155): the Linux job on Python 3.10 installs `pyarrow==16.0.0` and imports
  it, so the Parquet tests run in CI; pyarrow 14 fails to import with the numpy 2 of `constraints.txt` and 15
  does not install, so the extra is `parquet = ["pyarrow>=16"]` (`pyproject.toml:18` at `b6caf2f`).
- **Callers.** SC-2 (#130) and P19's `ssc export` expose both functions; ST-25 itself had none.

---

## 5. Reading both layouts, and `migrate`

### 5.1 Discovery

`legacy.py` is the only module that knows the old path rules. It only reads. It recognises:

| Form | Pattern | Source of the rule |
|---|---|---|
| L1 | `match_details/<lid>_<name>/season_<name>/<eid>/` | `sofascore_scraper/match_data_fetcher.py:1174-1192` |
| L2 | `match_details/<name>/season_<name>/<eid>/` (league directory without id) | `scripts/migrate_match_details.py:47-73`, `sofascore_scraper/services/stats.py:66-69` |
| L3 | `match_details/<eid>/` (flat) | `sofascore_scraper/match_data_fetcher.py:154-157`, `:170-171` |
| L4 | `<event dir>/<eid>.json` holding all slices in one object | `:538-542` |
| L5 | `match_details/_no_tournament/<sport>/<eid>/` | `:58`, `:1165-1172` |
| schedule | `matches/<lid>_<name>/<sid>_<name>/round_*.json`, `events_*.json` | `sofascore_scraper/match_fetcher.py:367`, `:452-454` |
| schedule, first version | `matches/<lid>_<name>/<sid>_<name>/round_<n>_full.json` | matches the `round_*.json` pattern but is the older file and has no `_complete` key; read as a round page with sub `round_<n>_full` and meta `{filtered: true}` |
| summaries | `matches/<lid>_<name>/*_summary.{json,csv}`, `*_matches.csv` in the league directory; `round_<n>_matches.csv` of the first version (13 other columns) inside the season directory | `sofascore_scraper/match_fetcher.py:514-595`, `sofascore_scraper/match_data_fetcher.py:1871-1875`; both places are accepted by `_season_summary_files` (`:1790-1797` at `0aa73b4`) |
| season lists | `seasons/<lid>_*_seasons.json`, `<lid>_seasons.json`, `<name>_seasons.json`, `league_seasons.csv` (header `Liga Adı,Lig ID,Sezon ID,Sezon Adı,Sezon Yılı`) | `sofascore_scraper/season_fetcher.py:291`, `:441-445`, `src/web/routes/common.py:31-36` |
| change log | `score_changes.jsonl` | `sofascore_scraper/refresh.py:21` |
| watcher | `watch_events.jsonl`, `watch_state_<sport>.json` | `sofascore_scraper/watcher.py:45-46` |

An event directory is recognised by `basic.json` (or by L4's combined file with a `basic` key) whose `id`
equals the directory name. `match_details/processed/` is skipped, as every walker does today. Two parts of
this rule are corrections of today's behaviour, not descriptions of it (ST-05):

- A directory that holds only the combined file `<id>.json` is an event directory for the reader. No walker
  on main finds it: `/api/matches/{id}` answers 404 for it and the need is `full`.
- No walker on main compares the payload's id with the directory name; they take the directory name as the
  id. The reader reports a mismatch as a problem and does not yield the directory.

Since RD-1 (#80) both corrections are visible in the application, because `_find_match_path`,
`_build_match_index`, the loader and `GET /api/matches/{id}` read the catalog: a combined-file-only
directory is a record (200 instead of 404, need `none` instead of `full`), and a directory whose payload id
is not its name, or whose name is not a canonical id, is not one. So is the rule for an event stored in two
places below: it resolves to the copy with the newest event payload, with and without the job cache (before:
the league/season copy without the cache, the first-listed copy with it). On the owner's data nothing
changed. One writer does not follow these rules yet (RD-1's finding; ST-21):
`refill_missing_match_slices` writes to the directory computed from the live payload, so a flat record, a
combined-only record or a record whose league or season was renamed gets a second directory, and the Store
then prefers the newer copy. Since ST-21 (#104) every writer goes through `put`, which finds a record by
its id in the catalog and promotes it (5.3), so no second directory is written.

A round file named `<n>_matches.json`, which only the terminal UI accepts (`src/ui/match_ui.py:442-448`), is
not a form the Store reads: the reader reports it as `unknown_name`.

From FX-4 until FX-5 a legacy round file whose slug has an upper-case letter (`round_1_Final.json`) was not
a schedule page for the reader either: `legacy.schedule_sub` validated the file's stem as a v3 sub, which is
lower-case only, and returned None, and the indexer reported the file as `unknown_name`. SofaScore's slugs
are lower-case and neither the fixtures nor the owner's data (38 distinct `round_*.json` names; 90 schedule
pages, none with an upper-case name, when FX-5 measured) have such a file. As built since FX-5 (#77;
`sofascore_scraper/store/legacy.py:396-418` at `9b03c64`) `schedule_sub` folds the stem to lower case when it derives the
sub (`round_1_final`); the file keeps its name, and the `round_` and `events_` prefixes must be lower-case as
the writer wrote them. This is the one exception to decision S13 (section 0): the fold applies only to
reading 2.x names. Two files in one directory that differ only in case are one page: the newest wins and
the other is reported as superseded, not merged. The sub of a legacy page is therefore always lower-case
while its path keeps the name on disk: ST-22 and `migrate` (ST-23) read such a page through the path of its
catalog row, never through a name rebuilt from the sub, and the superseded file must not overwrite the
winner when the page moves to v3. The rule is pinned by hand-built trees in `tests/test_store_legacy.py`;
no shared fixture has such a file.

When one event id is found in more than one legacy place, the directory with the newest `basic.json` mtime
wins and the others are reported by `verify`. When one schedule page is stored in two directories of the same
season (for example `96668_Premier_League_26_27/` and `96668_Season_96668/`), the newest mtime wins and ties
go to the smaller path.

When several season-list files exist for one tournament id, the newest by mtime is indexed, whatever its name.
`league_seasons.csv` takes part only for a tournament that has no JSON list at all (as `SeasonFetcher` uses
it). A file named after the league only (`<name>_seasons.json`) is resolved through an id-to-name map that
the caller supplies, because the Store may not read the league configuration; without the map the file is
reported as `unresolved_tournament`.
Neither of the readers of `3ae2599` did exactly this: the web routes preferred the bare `<lid>_seasons.json`
when it existed (`src/web/routes/common.py:31-33`) and `SeasonFetcher` preferred the file named after the
configured league (`sofascore_scraper/season_fetcher.py:436-451`). The difference only shows when a directory holds an
old-format file next to a newer one. Since RD-5 (#79) this is history: every reader (web route,
`SeasonFetcher`, terminal menu) takes the newest list by mtime whatever its name, through
`sofascore_scraper/services/tournaments.py`, and RD-5 listed it as a behaviour change. The id-to-name map of the catalog
comes from the follows table (2.3, "Follows"), which holds one name per tournament and not the `leagues.txt`
name when a config row exists; RD-5 compensates on the reader side only, and the catalog still reports such
a name-only file as `unresolved_tournament`. In the first server session on a fresh data directory the
follows table is empty, so the names differ and each season read also scans the legacy list files (a
handful of small files).

### 5.2 Indexing legacy data without moving it

The indexer fills the same tables from legacy files as from v3 files:

- `events` row from `basic.json`, with `layout = 'legacy'` and `path` set to the directory.
- `observed_at` from `observation.json`; when that file is missing it is `NULL`, which the refresh rules treat
  as "final unless legacy refresh is asked for", exactly as today (`sofascore_scraper/refresh.py:72-74`).
- Slice rows from the slice files and the two marker files (mapping in 2.3). `fetched_at` is the file mtime.
  A marker file that holds a value Python's `json` accepts but the counters cannot use (`Infinity`) is
  reported as malformed; today's `_load_unavailable` raises `OverflowError` on it.
- Schedule files give listing rows (8.2) and `entity_slices` rows with `layout = 'legacy'` and the file path.
  The `_complete` key becomes `meta_json = {"complete": true|false}`; a page that was stored filtered (no
  `_complete` key, or an `events_*` page) gets `{"filtered": true}`. An `events_*` page that was written with
  `FETCH_ONLY_FINISHED=false` is in fact unfiltered, but nothing in the file says so; it gets
  `{"filtered": true}` as well and is fetched again once. The v3 writer keeps the mark (ST-22, #98): an
  event page stored through `EntityStore.put` carries `meta={"filtered": true}` whatever the setting, as the
  legacy reader reports every page, so that the logical dump stays equal. Since ST-27 (#129) no event page is
  filtered any more (8.1), so the mark means "a page of the de-duplicated event list", not "finished
  matches only".
- Legacy rows in `score_changes.jsonl` get `seq` = their line number.

Reads go through the same Store calls: `events.payload(id, key)` looks at `events.layout`; for a legacy event
it opens `<path>/<key>.json` (with `event` → `basic.json`) or takes the key from the combined file.

The separate file comes first and `observation.json` is always read. Today's loader does the opposite: when
the combined file exists, `_load_match_data_from_dir` returns it and reads neither the separate files nor
`observation.json`. The Store's order is the right one, because only `refresh_match` updates the combined
file and it writes `basic.json` too. Moving the readers onto the Store therefore changes three things on old
data, which the reader PRs list as differences (plan items RD-1 and RD-3): a directory that holds only the
combined file becomes visible; a record with a combined file becomes due for refresh (today it never is); and
a truncated slice file affects only that slice instead of the whole match. The tests of ST-30 (#68) pin
these on the `legacy` fixture, where the catalog and the file-based readers differ in exactly these cases
and one more: the directory that holds only the combined file is an event with a payload (today the need is
`full`); the record with a combined file is due for refresh (today its observation is not read); the flat
directories are refresh candidates when `REFRESH_LEGACY` is on; and the truncated slice file makes only that
slice missing (today the reader drops every file of the match and reports all six slices). RD-1 (#80)
made the three changes visible in `GET /api/matches/{id}` and the need computation: the combined-only
directory answers 200 with need `none`; the record with a combined file has its observation read, so its
need is `refresh` and `refresh_due_ids` returns it where its walk reaches (the flat directories stay for
RD-3; since RD-3, #97, `refresh_due_ids()` reaches them as well, with `REFRESH_LEGACY` on the `legacy`
fixture, in path order, and a match stored only as
one combined file counts as downloaded in "missing details"); and a truncated slice file drops only that
slice (200 without the key instead of 500). For a
combined-file record the route now returns `basic` plus the required slices, each from its own file
first, where it returned the combined file as it was, unknown keys included.

A legacy tree can still change (a 2.x process, the terminal UI, a user copying files). The reconcile step on
open (3.5) picks that up. While 3.x writers and 2.x writers run on the same directory at the same time,
results are undefined; this is not supported and the writer lease cannot prevent it because 2.x does not take
it.

### 5.3 Writing to an event that lives in the legacy layout

`EventStore.put` promotes the event first: it reads all of its legacy files, builds the complete v3 directory
in `.meta/tmp/`, verifies it (as in step 3 below), renames it into place, updates the catalog
(`layout = 'v3'`, `legacy_path` = the old directory), and then applies the write to the v3 directory.

The legacy directory is not modified and not deleted. From then on the event is read from v3 only; a rebuild
applies the same precedence (3.4 step 5). The legacy copy is removed by `migrate --delete-legacy`. The extra
disk use is limited to events that were written again: provisional records being refreshed and events whose
missing slices are filled.

Rejected alternative: keep writing such events in place in the old format. It would keep the old writer, the
pretty-JSON format and the three marker files alive for as long as any legacy data exists.

Promotion as built (ST-20, #82; `events.promote_legacy` and `events.manifest_from_legacy`,
`sofascore_scraper/store/events.py:781-872` at `9b03c64`). It covers steps 1 to 4 of 5.4 for one event, staged under the
label of 9.3, and leaves the legacy tree byte- and mtime-identical (checked in all 51 interrupted states of
the crash tests). Where it differs from 5.4:

- Files that are not a slice, `observation.json` or a marker file (`LegacyEvent.extra_files`) and the
  unknown keys of a combined file (`combined_extra_keys`) are not copied: there is no `_extra/` yet, and
  deep verify would report any file the manifest does not name as I9, `_extra/` included. ST-23 copies them
  and teaches `verify.directory_files` about `_extra/` before it deletes a promoted event's legacy
  directory. As built (ST-23, #110) promotion still copies no extras; `migrate --delete-legacy` copies the
  missing extras of an event that `put` promoted before it deletes the legacy copy (5.4).
- `created_at` and `updated_at` of the promoted manifest are the oldest and newest legacy payload mtimes in
  whole seconds, so the time the event was first seen survives. A legacy `change_ts` or error status that
  is negative becomes null.
- Measured on a read-only scratch copy of the owner's data (3,460 files, 423 event directories): all 423
  events promoted, the legacy tree identical, the logical dump unchanged, `diff_from_rebuild` empty, deep
  verify clean; `v3/` takes 6.8 MB next to 66.8 MB of `match_details/`. Promotion of a real event takes
  5.8 ms on tmpfs.

Until ST-21 moves the detail writers onto `put`, the fetcher's need computation knows only legacy
directories (`MatchDataFetcher._stored_event`): for `_find_match_path` a v3 event would be `full` and be
fetched again. ST-21 switches the need computation together with the v3 writer. `QueryService`'s detail
read already reads both layouts.

As built (ST-21, #104). New matches are written to `v3/events/...` by `put`; a match of the legacy layout is
promoted by its next write (a refill, a refresh, a marker reset), and its `match_details/` directory is
neither modified nor deleted. `_stored_event` covers both layouts, `_find_match_path` returns
`(None, None, <v3 directory>)` for a v3 record, and the expected slices of a refill come from the Store's
slice rows (`_expected_slice_keys(event_id, sport)`, from `settled_empty`). The helpers that read or wrote
the marker files (`_expected_slices`, `_load_unavailable`, `_load_slice_status`, `_update_slice_markers`,
`_reset_match_markers`) are gone, against RD-3's note that two of them stay for the writers. The refresh
order of `refresh_due_ids` stays the legacy writer's path order; a v3-only record uses the names that
`legacy_folders` gives it. On a copy of the owner's tree a full download promotes every match once:
4.71 s against 2.70 s for main's rewrite in place (section 11). The frozen 2.x writer is
`tests/legacy_writer.py`, which builds legacy fixtures from then on.

### 5.4 The `migrate` command

CLI surface (the CLI is in `02-services.md` section 4; this is what the engine supports):

```
migrate [--dry-run [--exact]] [--tournament ID ...] [--limit N] [--delete-legacy] [--purge-derived] [--yes] [--json]
```

It needs the `writer` lease and refuses to start while a live service holds its lease on the same directory
(6.1). Default behaviour: **convert and verify, keep the legacy copy**. With `--delete-legacy` (which requires
`--yes`) each legacy directory is removed after its v3 copy has been verified again. The two steps can be run
at different times: a later `migrate --delete-legacy` only does step 6 for events that are already converted.

**Work list.** From the catalog: events with `layout = 'legacy'` (to convert) and events with
`legacy_path IS NOT NULL` (converted or promoted, legacy copy still on disk), then legacy schedule
directories, season-list files and the change log. There is no separate journal: the state of the migration
*is* the state of the files, and the catalog can be rebuilt from them at any point.

**Per event** (each step leaves a state from which the next run continues):

1. Read every file of the legacy directory. Files that are not a known slice, `observation.json`,
   `_unavailable.json` or `_slice_status.json` are copied unchanged into `_extra/` of the new directory and
   listed in the report.
2. Build the v3 directory in `.meta/tmp/migrate/<id>/`: one `.json.gz` per slice and the manifest, with
   `migrated_from`.
3. Verify the staged directory: read each written file back from disk, decompress, parse, and compare the
   object with the legacy object (deep equality); recompute the sha256 and compare with the manifest. Any
   mismatch aborts this event: the staging directory is removed, the legacy directory is untouched, the
   event is counted as failed and the run continues.
4. Publish: rename the staging directory to its final path. If a v3 directory already exists (promoted
   earlier, or a previous run stopped after this step), nothing is overwritten: the legacy slices are compared
   with the existing v3 ones, and a legacy slice may differ only if the v3 slice is newer.
5. Catalog transaction: `layout = 'v3'`, `legacy_path` = old directory, slice rows.
6. Only with `--delete-legacy`: verify the published files once more against their manifest, rename the legacy
   directory into `.meta/trash/`, delete it there, and clear `legacy_path`. Renaming first makes the directory
   disappear from the legacy tree in one step, so no walker ever sees a half-deleted event. Empty season and
   league directories are removed at the end of the run.

**Crash safety.**

| Crash after | State on disk | Next run or next open |
|---|---|---|
| step 1–3 | leftover staging directory | the writer's staging area under `.meta/tmp/` is emptied when the writer lease is taken (9.3); event still legacy |
| step 4 | v3 directory exists, catalog says legacy | the intent marker triggers a re-index; a rebuild also resolves to v3 + `legacy_path` |
| step 5 | both copies, catalog says v3 with `legacy_path` | nothing to repair; a later `--delete-legacy` continues with step 6 |
| during step 6 | legacy directory is in `.meta/trash/` | trash is emptied; `legacy_path` is cleared because the directory is gone |

**Schedules, season lists, change log.**

- A round or page file becomes `v3/tournaments/<lid>/seasons/<sid>/schedule/<sub>.json.gz`. The stored payload
  is the legacy object without the `_complete` key (which moves to the slice's `meta`); the verification
  applies the same transformation before comparing. The file is read through the path of its catalog row,
  never through a name rebuilt from the sub: since FX-5 two legacy files can fold to one sub, and the
  superseded one must not overwrite the winner (5.1).
- The newest season-list file per tournament becomes `v3/tournaments/<lid>/seasons.json.gz`.
- `score_changes.jsonl` is copied to `changes/0000-legacy.jsonl` (and removed only with `--delete-legacy`).
  Line numbers, and therefore `seq` values, do not change. As built (ST-20) the indexer takes only
  `<yyyy>-<mm>.jsonl` as a segment and reports any other file in `changes/`, `0000-legacy.jsonl` included,
  as `unknown_name`; ST-23 teaches `sofascore_scraper/store/changes.py` that file, and it has no other owner.
- Summary files and `match_details/processed/*` are derived. They are listed in the dry run as "derived, not
  migrated" and are deleted only with `--purge-derived`. A legacy season that has summary CSVs but no round or
  page JSON cannot be converted; it stays in place, stays readable, and is reported.
- `watch_events.jsonl` and `watch_state_*.json` are not migrated: the watcher imports its state file once when
  it first runs on the Store (ST-18, PR #59; the import is recorded under the meta key
  `imported_watch_state:<sport>`), and the events file is history that no code reads. Until the live service
  replaces the 2.x watcher (P23) the watcher keeps writing both files through the Store: the state file as a
  copy of the table, the events file with LF line endings on every platform. As built since P23 (#91) the
  state file is no longer written: `--watch` and `ssc watch` keep the state only in the table, under the
  sport as watcher name, and each imports an existing file once. `--watch` still writes its stdout lines and
  `watch_events.jsonl` in the 2.x format for one more release; P30 removes `sofascore_scraper/watcher.py` and
  `append_legacy_events`.
- `_history/` of a v3 event travels with the directory when it is staged or rebuilt; legacy events have no
  history, and `verify.directory_files` skips `_history/` (ST-26).
- `migrate` takes `writer` and then `live`, each with a purpose; no extra kind of lease is needed (6.1).

**Dry run.** `Migrator.plan()` changes nothing on disk (a test compares a hash of the whole tree before and
after). It reports counts per kind, bytes before, estimated bytes after, unknown files, conflicts, and
unconvertible seasons. The size estimate compresses a sample of 200 events in memory and extrapolates;
`--exact` compresses everything (3.7 ms per event measured).

**Resumable.** `--limit N` and `should_stop` end the run between two events. Running it again continues,
because the work list is recomputed from the catalog. A finished event is never converted twice.

**As built** (ST-23, #110; `sofascore_scraper/store/migrate.py`, `sofascore_scraper/cli/commands/migrate.py` at `b3cb819`). `migrate`
is `Store.migrate` and the command `ssc migrate`; it is never run automatically. `plan()` takes no lease
and writes nothing; `run()` takes `writer`, then `live`, both with purpose `op:migrate`, so a download, a
live service or a `--watch` watcher refuses it (exit 6), and then reconciles the catalog, which closes
unfinished writes and the change intents of FX-12, before it builds the work list. Where the steps above
say less, or something else:

- **Staging** follows decision S16: `.meta/tmp/writer.<random>`, because `migrate` holds the writer lease,
  not `.meta/tmp/migrate/<id>/` (step 2). The events go through steps 1 to 5 inside
  `EventStore._entity_write` (pending marker, catalog write lock, recovery of an earlier unfinished write).
- **`_extra/` (step 1).** The unknown files of an event directory, subdirectories included, unreadable slice
  files and a combined file that has unknown keys (the whole file, which is how `combined_extra_keys` is
  kept) are copied unchanged into `_extra/`; leftover `.tmp` files are not. Step 3 compares the `_extra/`
  files byte for byte.
- **Step 6 has two checkpoints**, trashed and deleted, so the crash tests count seven, not six: they raise a
  `BaseException` at each checkpoint and reopen the store; no real process is killed. Before a legacy copy
  goes, every v3 payload must match its manifest, every legacy slice must equal the v3 one or be older, and
  `_extra/` must be complete; for an event that `put` promoted, the missing extras are copied at this
  point. Every legacy copy of the event goes, duplicates included. An old directory named after a match id
  that holds another match's payload stays the catalog's `legacy_path`, so every delete run reports it as
  kept and exits with 3.
- **Empty folders.** A run with `delete_legacy` or `purge_derived` removes every empty directory of the
  legacy trees except the roots and `processed/`, not only those emptied in this run; otherwise a run
  killed during step 6 would leave empty league and season directories for good.
- **Schedules and season lists** are written with `EntityStore.put` (one call per season), each page read
  from the path of its catalog row, with `meta` and `fetched_at` taken from the legacy file; the newest list
  per tournament. With `delete_legacy`, schedule and season-list files are deleted one file at a time
  through the trash once their copy is verified; `league_seasons.csv` only when every tournament in it has a
  verified v3 list, and only in a run without `--tournament`. A season list named after the league alone
  (`<name>_seasons.json`) resolves only through the follows, so the command mirrors `config/leagues.txt`
  into the follows first; an unresolved file is reported and kept.
- **Change log.** `score_changes.jsonl` is copied byte for byte to `changes/0000-legacy.jsonl`, and removed
  with `delete_legacy` only when the copy is identical to it. While both files exist the copy takes the
  place of the old file in the index (8.5). Lines that a 2.x process appends after the copy stay invisible
  until the next `migrate` extends the copy; a copy that is not a prefix of the old file is a conflict, and
  nothing is written.
- **Derived files.** `--purge-derived` deletes the summaries of seasons that have schedule pages and
  `match_details/processed/*`, never the summaries of a summary-only season, which are its only source. A
  first-version `round_<n>_matches.csv` inside a season that has schedules counts as derived and is purged,
  where ST-05's note said "reported and left in place".
- **Unrecognised entries** (a directory without an event payload, a corrupt `basic.json`, which makes the
  match invisible to the catalog, an unknown schedule file name) are reported as `unrecognised` and left in
  place, also with `--delete-legacy`. After `ssc migrate --delete-legacy --purge-derived --yes` the old
  trees hold only such entries.
- **`--limit`** counts matches. When it cuts the work list, the schedules, season lists, change log and
  purge are left for a later run. `--tournament` selects matches, pages, lists and summaries; the change log
  and `processed/` only move in a run without it.
- **The plan.** `plan()` takes the same `limit`, `delete_legacy` and `purge_derived` as the run, so that its
  counts match; `legacy_copies` counts directories, not events, because duplicates of a match count. A dry
  run on a directory that is not yet a store creates `.meta/` (`state.db`, `catalog.db`), because the CLI
  opens the store; the data trees are untouched.
- **Exit codes and runs.** 2 with `confirmation_required` without `--yes`, 3 when an item could not be
  converted (its old copy stays), 6 when a lease is held. Each real run leaves a row in `migration_runs`
  (3.3). `scripts/migrate_match_details.py`, which renamed league directories in place, is removed: the
  new layout does not depend on directory names.
- Not supported and not tested: a 2.x process and 3.x writing the same directory at the same time, beyond
  the change-log cases above.

---

## 6. Concurrency

### 6.1 Leases

A lease is an OS file lock on `DATA_DIR/.meta/locks/<name>.lock`, held for as long as the `Lease` object
lives. The kernel releases it when the process dies, so there are no stale locks to clean up. The technique
is the one `sofascore_scraper/throttle.py:112-129` already uses (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows).

There is one implementation, `sofascore_scraper/store/lease.py`. The job manager, the live service and the sink dispatcher
(`02-services.md`) take their leases through `Store.lease()`; they do not create lock files of their own.

| Lease | Who takes it | Excludes |
|---|---|---|
| `writer` | download and refresh jobs, single-event fetch (target; today the route only asks, see below), `migrate`, history pruning, backup creation, the reset of the "unavailable" markers (`--recheck-unavailable`; it rewrites the marker files of stored matches) | another `writer`; `maintenance` |
| `watcher:<sport>` | the 2.x-style polling watcher, one per sport (`sofascore_scraper/watcher.py:10-12`), until the live service replaces it | the same watcher name; `live`; `maintenance` |
| `live` | the live service (one per data directory, all sports) | another `live`; every `watcher:<sport>`; `maintenance`; a running `migrate` |
| `sinks` | the process that dispatches streams to webhooks and file sinks | another `sinks` |
| `export` | the export job of the API and the scheduler (since FX-23, #171) | another `export`; `maintenance` |
| `maintenance` | clear, restore, catalog rebuild, state-db migrations, data-directory change | everything except `sinks` |

Implementation of the exclusion with `maintenance`: every `writer`, `watcher`, `live` and `export` holder
also holds a shared lock on `maintenance.lock`; `maintenance` takes that file exclusively and fails if anyone holds it
shared. On Windows, where `msvcrt.locking` has no shared mode, each holder locks one distinct byte of the file
(the first free one of 64) and `maintenance` locks the whole 64-byte range. `live` and `watcher:<sport>`
exclude each other the same way through `live.lock`; `migrate` holds `writer` and additionally takes
`live.lock` exclusively for its duration.

`Store.lease(name, wait=0)` raises `LeaseHeld` with the holder's pid, host, purpose and start time, read from
the `leases` table. That table is filled when a lease is taken and is only information; the OS lock decides.
The holder information is not written into the lock file itself, because a byte-range lock on Windows makes
the locked bytes unreadable for other processes.

As built (ST-10 #50, `sofascore_scraper/store/lease.py` at `f286723`):

- **Who blocks is found by probing, not from the table.** The table cannot tell which of several shared
  holders blocks `maintenance`, and a row outlives a killed process. `LeaseHeld`, `Store.lease_holder(name)`
  and `StoreInfo.leases` probe the OS locks (a shared lock that is taken and released at once) and use a
  `leases` row only for a lock that is really held. For a moment after a lock is taken the row can be
  missing or still show the previous, killed holder; `LeaseHeld` then carries no pid, or the old one.
- **Order and attempts.** A lease takes its shared guards first (`maintenance.lock`, and `live.lock` for a
  watcher) and its own file last, so an attempt that fails half way has held only shared locks and cannot
  make another request fail. A refusal is reported only after three attempts 5 ms apart; with `wait`, the
  attempts continue every 50 ms until the time is over.
- **Purposes.** A job takes `writer` with purpose `job`. A data operation takes its lease with purpose
  `op:<name>`; backup is such an operation and takes `writer` (the table above), everything else
  `maintenance`. `JobStore.exclusive` and `create_running` map a refused lease to the two existing conflict
  classes through `conflict_from_lease`: a holder with a purpose `op:<name>` gives
  `DataOperationRunningError` for that operation, any other `writer` holder gives `JobRunningError`
  (`job_running`), and any other holder `DataOperationRunningError` with its purpose or lease name as the
  operation. The last case is a `live` or `watcher:<sport>` holder that blocks a data operation:
  `InstanceRunningError` (`02-services.md` 2.6) did not exist when the job store was written. It exists
  since P18 (`sofascore_scraper/errors.py`, where `lease_error_code(name, purpose)` gives the three codes). The first
  revision expected the job manager to replace the job store's own mapping; it did not: after P11 (#69)
  `conflict_from_lease` still has no `instance_running` (`sofascore_scraper/store/jobs.py:207-218` at `e0bae0c`). A job
  takes `writer`, which `live` and `watcher` do not exclude, so a job cannot be blocked by them; the case
  exists only for data operations. Since P23 (#91) that case maps to `instance_running`:
  `conflict_from_lease` returns `InstanceRunningConflict` for a `live` or `watcher:<sport>` holder
  (`sofascore_scraper/store/jobs.py:210-238` at `9b03c64`). It is a subclass of `DataOperationRunningError`, so the old
  callers still catch it; its code is `instance_running`, `.operation` is still the holder's purpose (or the
  lease name) and `.lease` the lease name. A clear or restore that a running live service or watcher blocks
  therefore answers `instance_running` instead of `data_operation_running`, and a second `ssc watch`, or a
  `--watch` beside it, exits with code 6. Since P11 a job can carry another purpose than `job`:
  `JobManager.start(lease_purpose=)` passes it to `create_running(purpose=)`, which keeps the purposes
  `headless` and `refresh` of the command line; a web job's purpose stays `job`.
- **Not re-entrant.** A second `store.lease('writer')` in the same process is refused with `LeaseHeld`, like
  one from another process. Whoever adds a second acquirer to a process removes the first: since P11 the
  job manager takes the writer lease for a CLI download or refresh, and `main.py` no longer does.
- **Unclean marker.** Taking `writer` creates `.meta/locks/unclean` and a clean release removes it;
  `Lease.unclean` on a new writer lease says that the previous writer did not release cleanly. Nothing reads
  `Lease.unclean` yet: the open, which takes no writer lease, looks at the marker file itself and at who
  holds `writer` (ST-11; 3.5). The marker is written with a plain `open(path, "w")`
  (`sofascore_scraper/store/lease.py:354-359` at `e0bae0c`), so it followed the umask before FX-8 already.
- **Fork.** A forked child inherits the lease's file descriptors, and with `flock` an unlock in the child
  would drop the parent's lock. `Lease.release` in a forked copy only closes its descriptors (a test pins
  this); the lock stays held until both processes have closed.
- **Lock files** follow the umask since FX-8 (#70; 4.4): 0664 under 002, 0600 under 077, 0640 under 027,
  and 0644 under 022 as before. Existing lock files keep their mode. Two things that FX-8 found and left as
  they are (`sofascore_scraper/store/lease.py` at `e0bae0c`). `holder()` and `holders()` report "not held" for a lock file
  that the caller cannot open, because the probe swallows the `OSError` (`:412-415`), while `acquire` fails
  with a `StoreError` that carries `EACCES` and the path of the file; a test pins both. And a shared lock is
  also taken on a descriptor opened `O_RDWR` (`:129`), so read-only access to `maintenance.lock` is not
  enough to take `writer`: `flock(LOCK_SH)` would work on a read-only descriptor on POSIX, the byte-range
  lock of Windows would not.
- **What takes a lease today.** The job store of the web server: a job takes `writer`, the data operations
  take `maintenance` (backup: `writer`), and a state-db migration takes `maintenance` (7.3). The single-match
  fetch of the web API does not take `writer`: since FX-1 (PR #60) it asks `JobStore.writer_busy()` before it
  starts and is refused with 409 `job_running` while a job of the same server runs or another process holds
  `writer`. A job that starts just after the check can still write alongside it; a `maintenance` holder
  (clear, data-directory change) is not seen by the check; and during a backup the answer is `job_running`,
  not `data_operation_running`. P11 did not change this: the single-match fetch still only checks
  `writer_busy()` (`src/web/routes/matches.py:514` at `e0bae0c`). All three end when the single fetch runs
  as a job under the lease (P13). Since P10 (PR #64) a command-line run holds a lease: `writer` for
  `--headless --update-all`, `--refresh-only` and `--recheck-unavailable` (purposes `headless`, `refresh`,
  `recheck-unavailable`; one lease covers a combined run) and `watcher:<sport>` for `--watch` (purpose
  `watch`). Since P11 (#69) the job manager takes the first two, when it creates the job row of a download
  or a refresh, and a recheck that is combined with one runs inside that job; `main.py` itself takes only
  the lease of `--watch` and of a `--recheck-unavailable` run on its own. A refused run prints the holder
  (pid, host, purpose, since when) on stderr and exits with 6. None of these purposes starts with `op:`, so
  the web API answers `job_running` while such a run is active, the single-match guard included.
  `--headless --csv-export` alone takes no lease (an export takes none, `02-services.md` 2.8). The services
  take no lease themselves; the caller does. The interactive terminal menu takes none either: a download
  started from the menu and a web job can still write the same directory at the same time, until the menu
  is removed (P26; done, #131). Since ST-11 (#75) the Store itself takes one lease: `maintenance` with purpose
  `op:rebuild` for the build of the catalog on open (3.4). The reconcile on open and the hooks take none
  (3.5). Since ST-19 (#90) `Store.clear` takes `maintenance` (purpose `op:clear`) itself when this process
  does not hold it already; the web clear holds it through `JobStore.exclusive("clear")`. `BackupManager`
  takes no lease: its caller holds `writer` (the web backup through `JobStore.exclusive("backup")`, 9.1).
  `HistoryStore.prune` (ST-26) refuses unless this process holds `writer`. Since P23 (#91) `ssc watch`
  takes `live` (one live service per data directory). The terminal menu's clear, restore and move still
  take none (3.5).
- **What takes a lease at `b3cb819`**, beyond the list above. ST-21 (#104) put the menu's clear under
  `maintenance`, while its downloads still took no lease, so their staging was labelled `put` (9.3); P26
  (#131) removed the menu, so the unleased downloads are gone. `ssc backup create` holds `writer`
  (`op:backup`) for every scope except `config`, and a restore takes `maintenance` (`op:restore`) itself
  unless this process holds it (ST-24, #109; 9.2). `ssc migrate` takes `writer` and then `live`
  (`op:migrate`), and the `ssc catalog` commands take `maintenance` for the calls that write (ST-23, #110;
  3.5, 5.4). Since P21 (#126) a job can hold `maintenance` instead of `writer`:
  `JobStore.create_running(..., lease="writer" | "maintenance")`, passed by `JobManager.start` and
  `submit`, with `ValueError` for any other name. A store does not lend its running job's lease to a job of
  another kind (`JobRunningError`), `reap_stale` leaves a running row alone while another process holds
  `maintenance`, as it does for `writer`, and while a maintenance job runs another job of the same process
  gets `job_running` and one of another process `data_operation_running`, with the job kind as purpose.
  `Store.clear`, the restore and `MaintenanceService` see the lease as held here and run under it. The
  single-match fetch still only asks `writer_busy()`: P13 (#113) runs it through the pipeline but not as a
  job, so it takes no lease and stages under `put`.
- **Liveness of a job** (P11, #69). A job is live while its row says `running` and someone holds `writer`;
  `JobStore.reap_stale()` marks the other running rows `interrupted` (2.3). The check is whether anyone
  holds `writer`, not whether the holder is the row's `owner`. On a file system without lock support no
  holder can be seen (the probe's lock call fails with an error that is not "busy"), so every running row
  of another process reads as stale: single-process mode, as 2.x swept at start (6.4).
- **A job releases its lease after its last store access** (#93). The job manager used to release `writer`
  and the running flag in the final `store.update(finished=True)`, and then still wrote the `job.finished`
  stream event and read the job back. Closing the `StateDb` in that window (`JobStore.rebind` after a
  data-directory change in the settings, or `Store.close()` / `JobStore.close()` by a library user while a
  background job finishes) closed the SQLite connection under the job thread and could crash the process
  (4 of 100 test runs before the test fix of #88; 14 of 60 stress processes on main). As built the job does
  every store access of its end inside a finishing block of the job store (`JobStore._job_finishing`,
  `sofascore_scraper/store/jobs.py:418`) and releases the lease when the block ends. During the block `rebind` and
  `exclusive` raise `JobRunningError` (409 `job_running`), `JobStore.close` waits (at most 30 s), and
  `create_running` on the same job store waits; 0 of 60 stress processes crashed. Another process can see
  the row finished while the lease is held a few milliseconds longer; that window existed before, between
  the commit and the release. `Store.close()` does not wait for the block yet (3.2; FX-12). #88 joins the job
  threads before the test fixtures close the store. Since FX-12 (#100) `Store.close()` waits for the block
  as well, through `JobStore.wait_for_finishing_job` (3.2).
- **Measured.** Taking and releasing a lease 0.12 ms on tmpfs, a refusal 10.5 ms (the three attempts). Eight
  processes taking `writer` and `maintenance` 300 times each never overlapped (2,393 acquisitions, checked
  with an `O_EXCL` flag file; run outside the test suite).
- **The `export` lease** (FX-23, #171; finding F14 of the end-to-end test). An export job took `writer`,
  so it was refused for the whole length of a download ("another job is writing to the data folder"),
  although an export only reads the catalog and the payloads (SQLite WAL readers, files replaced atomically)
  and writes only into `exports/` and its own `export.<random>` staging entries; the only reason was the
  job model, one running job per job store. As built: `export` is a lease of its own (`export.lock`
  exclusive, `maintenance.lock` shared; `EXPORT`, `sofascore_scraper/store/lease.py:78` at `48e4c4c`), so
  one export runs at a time, next to `writer`, and clear, restore, rebuild and a data-folder change
  (`maintenance`) wait for it; a held `export` lease maps to `data_operation_running` (409). The API runs an
  export job in a job store of its own on the same `state.db`, closed when the job ends, so the download's
  running row and its mirror are untouched (`JOB_LEASES`, `EXPORT_KIND`,
  `sofascore_scraper/store/jobs.py:67-68`). `reap_stale` treats rows of kind `export` as alive while someone
  holds `export`, and the export store's lease does not count as holding `writer`, so it never marks the
  download's row stale (`:567-574`). Cancel works through the row's flag as for any job. `ssc export` takes
  no lease. Queueing the export behind the download was the alternative and was not chosen: the user would
  still wait for the whole download.

This replaces the in-process slot of `JobStore.exclusive` (`sofascore_scraper/web/jobs.py:150-168`), which cannot see a CLI
process writing the same directory. `JobStore.exclusive` keeps its interface and its error classes
(`JobRunningError`, `DataOperationRunningError`, turned into HTTP 409 at `sofascore_scraper/web/app.py:59-65`) and is
implemented with these leases. A job is "running" when its row says so **and** its owner holds the `writer`
lease; this is what lets another process tell a crashed job from a live one. P11 built it as `reap_stale`
("Liveness of a job" above).

### 6.2 The write protocol

The lease does not serialise individual writes; a job and the live service may write at the same time. Every
write of an entity runs like this:

1. Transaction A on `catalog.db`: `INSERT OR REPLACE INTO pending_writes (kind, entity_id, started_at)`. Commit.
2. Transaction B: `BEGIN IMMEDIATE`. From here until the commit, no other process can write the catalog, so
   the catalog's write lock is the mutex for the whole data directory.
3. Read the entity's `manifest.json` from disk (or promote the legacy event, 5.3).
4. Write the changed payload files (temporary file + `os.replace`), append history members.
5. Write the new manifest (temporary file + `os.replace`).
6. Upsert the catalog rows, append the change-log line if any, delete the `pending_writes` row. Commit.

As built (ST-20 #82, ST-26 #92; `EventStore._entity_write` and `_apply`, `sofascore_scraper/store/events.py:1358-1397` and
`:1460-1628` at `9b03c64`) the order is:

1. Transaction A: `INSERT OR IGNORE INTO pending_writes`. A row that is already there marks an unfinished
   earlier write, and this write recovers it (the manifest is healed from the files first, 4.4).
2. Transaction B, `BEGIN IMMEDIATE`. The marker is checked again under the lock: a reconcile on open in
   another process can run between the two transactions and remove it (nothing is half written at that
   moment; it re-indexes the event). When it is gone the write starts again from step 1; after five rounds
   it raises `StoreBusy`. A `put` that fails before it touches the disk (a validation error, an unknown
   event) removes its marker.
3. Read the manifest, or promote the legacy event (5.3); a new event is built in a staging directory and
   published with one rename.
4. Write the changed payload files, then append the history members (ST-26 confirmed this order).
5. Write the manifest.
6. Append the change-log line, if `on_event_change` returned one: to the file first, then to the `changes`
   index (8.5).
7. Re-index the event's catalog rows from its files, delete the marker, commit.

**The lost change row (a known defect until FX-12, #100; fixed as described below).** A process that
dies, or a write that fails, after
the event payload was replaced (step 4) and before the change-log line was appended (step 6) loses that
line for good: the payload on disk is new, the log has no row, and a repeated refresh sees an identical
payload and no difference. The intent marker re-indexes the event but cannot bring the row back. The crash
tests pin it (`CHANGE_LOST`). Appending the line before the payload would turn the loss into a possible
duplicate. It was not reachable while nothing called `put`; since P23 the live service calls `observe`
(`sofascore_scraper/services/live/supervisor.py:521` at `9b03c64`), so `ssc watch` can meet it now. Item (1) of the fix
item FX-12 changes the order of this section so that the line is recoverable, before ST-21 makes the
downloads call `put`.

**The change row as built** (FX-12, #100; `write_change_intent`, `recover_change` and `recover_changes`,
`sofascore_scraper/store/events.py:916-990` at `b3cb819`). The defect above is fixed; the crash tests no longer have
`CHANGE_LOST`, and every interrupted state ends with the change log equal to the expected one, without a
duplicate.

- **The intent.** In step 4, when the event payload changes and `on_event_change` returned a row, the
  Store first writes `{"event_id", "sha256" of the new event payload, "row"}` atomically to
  `.meta/pending_changes/<event_id>.json`, before a promotion, a staging directory or any payload file is
  touched (the new crash checkpoint `change_intent`). After the line is appended in step 6 the intent file is
  removed. The intent is written and removed only under the catalog write lock, so a holder of the lock that
  finds one knows that its writer died or failed; a write that fails before the event's files were touched
  removes it while it still holds the lock. A row that cannot be serialised to JSON fails before the disk is
  touched.
- **Recovery.** `recover_change` runs under the catalog write lock: at the start of every write of that
  event (right after the lock), and in every reconcile, before the pending markers (3.5), where
  `recover_changes` closes every intent file and removes the leftover temporary files of a killed intent
  write. When the stored event payload has the intent's sha256, the row is appended unless the change log
  already holds it (the last 20 rows of the event are compared without `seq`, after a sync of the index,
  which covers a crash between the append and the removal of the intent); when the payload never landed,
  the intent is dropped and the repeated write sees the difference again and writes the row itself; an
  unreadable or invalid intent is logged and removed. The recovered row goes through `append_row`
  (`max(seq) + 1` under the lock), so I7 holds.
- **Why a file.** The row is computed inside transaction B, which the crash it must survive rolls back, so a
  column of `pending_writes` would be lost exactly when it is needed. A catalog rebuild does not read the
  intent files (4.2).
- **Announcing.** A recovered row's seq is returned in `PutResult.change_seq` when the write records no row
  of its own (2.3); a row that a reconcile recovers at open gets no `change.recorded` event.
- **Cost.** A write that records a change row: 1.54 ms against 1.45 ms before (one more small atomic write
  and one remove); a write with an unchanged payload: 1.01 ms against 1.02 ms (section 11).

Consequences:

- The manifest is read and rewritten under the mutex, so two processes writing different slices of one event
  cannot lose each other's manifest entry. The manifest file is the authority; the catalog rows mirror it.
- A process killed anywhere between 1 and 6 leaves the `pending_writes` row; the next open re-indexes that
  entity from its files (3.5).
- The mutex is held for the file writes of one entity: about 1–3 ms. At the default 5 requests per second
  this is irrelevant; with the rate limit removed it still allows hundreds of event writes per second.
- A writer waits at most `busy_timeout` (5 s) for the mutex and then raises `StoreBusy`. Only an in-place
  rebuild holds the mutex for long, and it holds the `maintenance` lease, so no writer is running then.
- Stream events and job rows live in `state.db` and are written in their own transactions. A stream event
  that announces a write (`change.recorded`, a live status change) is appended **after** the catalog
  transaction of that write has committed, so a consumer that reacts to the event finds the data. As built
  `put` appends no stream event itself: the caller announces `PutResult.change_seq` after `put` returned
  (ST-20; the live service of P23 does, and since P13 the download pipeline, 2.3). A `put` in a process
  that holds `live` stages under that label (9.3).
- A writer of `state.db` can be starved past the busy timeout. SQLite's busy handler polls at growing
  intervals, and a writer that never pauses takes the lock again at once: on the Windows runner a test
  process got `StoreBusy` on `BEGIN IMMEDIATE` while two helper processes appended in a tight loop.
  `StreamLog.append` raises `StoreBusy` in that case. No producer today writes without pausing, but a
  long-running producer must not end on it: the live service retries an append with back-off (P23). The job
  manager decided it as follows (P11, #69): a `state.db` that stays locked for more than 5 s does not fail
  a job on a progress or log write, the final write of a job is tried three times
  (`FINAL_WRITE_ATTEMPTS`, `sofascore_scraper/jobs/manager.py:65` at `e0bae0c`), and an append to the `job` stream is
  best effort and not retried.

### 6.3 Readers

Readers take no lease and no lock.

- Catalog and state db: WAL gives each read transaction a consistent snapshot; readers never block writers
  and writers never block readers.
- Payload files: replaced atomically, read in one call.
- A reader may hold a catalog row whose file has just gone (legacy directory removed by `migrate` after the
  event moved to v3; clear). On `FileNotFoundError` the Store re-reads the event's row once and retries with
  the new location; if the file is still missing it raises `PayloadMissing`. As built (ST-30, #68;
  `read_with_retry`, `sofascore_scraper/store/events.py:552-568` at `e0bae0c`) the missing file surfaces as
  `PayloadMissing` from the codec, not as `FileNotFoundError`. The retry re-reads the row once: if the row
  no longer shows a payload (the event was cleared meanwhile) the call returns None, otherwise the read is
  repeated with the new location and a second miss raises `PayloadMissing`. `EventStore` and `EntityStore`
  share the function.
- A process that has `catalog.db` open while another process recreates the file (3.4, recreate mode) would
  keep reading the old, unlinked file. Each Store compares the device and inode of `catalog.db` with those of
  its open connection once per second and reopens when they differ (not the size, which changes with every
  write).

### 6.4 Platforms

| Aspect | Linux, Docker | macOS | Windows |
|---|---|---|---|
| Leases | `flock` | `flock` | `msvcrt.locking` byte ranges |
| Atomic file replace | `os.replace` | `os.replace` | `os.replace`, retried when the target is open |
| Directory rename into place | atomic | atomic | works when the target does not exist (always the case here) |
| SQLite WAL | yes | yes | yes |
| Recreate catalog by replacing the file | yes | yes | only when no other process has it open |

Network file systems (NFS, SMB) are not supported: WAL needs shared memory and `flock` is unreliable there.
The Store detects a failed switch to WAL and degrades to single-process mode with a warning. Docker named
volumes and bind mounts of local disks are fine. As built (ST-10) the same fallback exists for a file system
on which the lock call itself is not supported (`ENOLCK`, `ENOTSUP`): the lease is granted, one warning is
logged per locks directory, and only one process may use the data directory. Without it an installation that
runs on such a share with 2.x could no longer start a job.

On Windows at most 64 processes can hold one lock file shared (one byte each); a 65th shared holder is
refused as if the file were held exclusively.

On Windows a data directory that a process has open cannot be deleted or moved, and `open_store` keeps one
Store per directory open for the life of the process (2.3). Deleting or moving a data directory while the
application runs therefore needs `Store.close()` first (#120, a finding of a test fix: the tests that
remove a temporary data directory now close the registry's Store first). The data-directory change of the
settings routes rebinds the job store but does not close the Store of the old directory (1.1); closing it
there is left to a follow-up item of the plan (FX-13 of `03-implementation-plan.md`). As built (FX-13,
#152) `PATCH /api/v1/settings` closes the writable and read-only Stores of the old directory after the
rebind (`close_data_dir`, `sofascore_scraper/web/api/v1/settings.py:492` at `b6caf2f`); it leaves the Store open, with a
warning, while this process holds one of that directory's leases (for example the `sinks` lease of
`serve`'s dispatcher), because a thread still uses it. The legacy settings route only rebinds; it goes with
P30. Neither was run on a Windows machine: CI's Windows jobs are best-effort.

CI runs the Python tests on Windows and macOS already, with Python 3.14 only
(`.github/workflows/ci.yml:62-63`; Python 3.10 runs on Linux only, `:59`). The Store's tests run there too,
including the lease and replace tests. The SQLite of Python 3.10 on Windows and macOS is therefore not
exercised by CI.

---

## 7. Versions and migrations

### 7.1 `.meta/schema.json`

```json
{
  "store_id": "5f0c...",
  "layout_version": 3,
  "min_reader_layout": 3,
  "manifest_format": 1,
  "state_schema": 2,
  "catalog_schema": 1,
  "derive_version": 1,
  "created_by": "3.0.0",
  "created_at": "2026-10-05T10:00:00+00:00",
  "last_writer": {"app_version": "3.0.0", "at": "2026-10-05T10:00:00+00:00"}
}
```

- A process refuses to **write** when `layout_version` is greater than the one it implements, and refuses to
  **read** when `min_reader_layout` is greater. Both raise `SchemaTooNew`.
- `store_id` is created once. A consumer that remembers it can tell that a directory was replaced.
- As built (ST-10): the file is created and rewritten under the write lock of `state.db`, so two processes
  that open a new directory together agree on one `store_id`. `state_schema`, `catalog_schema`,
  `derive_version` and `manifest_format` are the versions of the code that last wrote the directory; they
  are information, the authoritative values are in the files themselves. The file is rewritten only by a
  writable open that finds another application version in `last_writer` or other code versions; a read-only
  open never rewrites it.
- A data directory without `schema.json` and without `v3/` is a pure 2.x directory: the Store creates
  `.meta/schema.json`, `state.db` and `catalog.db` and changes nothing else. Removing those three files
  returns the directory to its 2.x state.
- `state_schema` is 2 since P11 (#69, migration `0002_job_manager`; 7.3). Since ST-11 (#75) `catalog.db` is
  filled by the open, not only created (3.4), and `.meta/` appears after any write of the legacy writers
  (a single-match fetch, a download from the terminal UI, a season refresh), because their hooks open the
  Store. Since RD-1 (#80), RD-4 (#78) and RD-5 (#79) a read does too: the first request of a server process
  to `GET /api/matches/{id}`, `/api/dashboard`, `/api/stats/system` or a league or season route opens the
  Store, which creates `.meta/schema.json` and `.meta/catalog.db` when they are missing (also when the
  configured data directory does not exist yet) and reconciles or builds the catalog: about 15 ms on a copy
  of the owner's data, 0.75 s when the catalog must be built. Neither design document said that a legacy
  GET route may create `.meta/`; the rule of #43 that "a GET route writes nothing" now leaves the Store's
  derived files out (`tests/test_web_hardening.py`). In exchange `GET /api/matches/{id}` no longer creates
  `match_details/` and `match_details/processed/` by building a fetcher.

Four version numbers, each with its own rule:

| Number | Covers | Changed by | On mismatch |
|---|---|---|---|
| `layout_version` | the v3 tree and file naming | a new on-disk layout | refuse to write / read as above; converting is an explicit command |
| `manifest_format` | fields of `manifest.json` | adding fields does not bump it; changing meaning does | readers accept older formats; a manifest is upgraded when it is next written |
| `catalog_schema`, `derive_version` | catalog DDL; what `derive.py` puts into rows (for example score sheets for new sports), and since FX-5 any rule that changes the rows the same files give (7.2) | any change to either | rebuild (3.4); also when the file is *newer* than the code, so downgrading is safe |
| `state_schema` | `state.db` DDL | a numbered migration | forward only; a newer file than the code raises `SchemaTooNew` |

The public data contract has its own `schema_version` (`00-platform.md` section 3). It is not stored in the
data directory, because nothing on disk is in that schema.

### 7.2 Catalog

No migration scripts. `catalog.py` holds `CATALOG_SCHEMA`, and `derive.py` holds `DERIVE_VERSION` (re-exported
by `catalog.py`); if either differs from the
file, the catalog is recreated from the files. A change that is cheap to apply in place (a new index) may
ship as an optional in-place step, but the rebuild path must always give the same result, and a test checks
that it does.

As built, `DERIVE_VERSION` is the version of the rows that the same files give, not only of `derive.py`'s
output: it is also bumped when a rule outside that module changes them, such as the presence rules of
`sofascore_scraper/slices.py` or the legacy names of `sofascore_scraper/store/legacy.py`. FX-5 (#77) did so: it is 2 at `9b03c64`
(`sofascore_scraper/store/derive.py:41`), and the next bump is 3. With the bump every catalog is rebuilt once on the next
open (one INFO line, `Catalog built from the files in ...`; 0.73 s on a copy of the owner's data). The
version is recorded in nine goldens (`tests/golden/catalog/*`, `tests/golden/catalog_listings/*`,
`tests/golden/derive/event_rows.json`), which a bump regenerates; the tests that held the number follow the
constant.

At `b3cb819` it is 5 (`sofascore_scraper/store/derive.py:47`), after three bumps for new sports in the registry, each of
which rebuilds every catalog once: 3 with SP-1 (#112; American football, Aussie rules, ice hockey,
handball, rugby, futsal, minifootball and floorball get a score sheet in `scores_json`), 4 with SP-2
(#115; volleyball, badminton, table tennis, padel and snooker), 5 with SP-3 (#118; baseball, cricket,
esports, darts and MMA, and cricket's `willcontinue` status, the end of a day's play, is in the class
`live`, not `unknown`). Each bump changed only the `derive_version` of the catalog goldens and added rows
to `tests/golden/derive/event_rows.json`. The next bump is 6. `ssc version` (P19) does not print the
derive version, because a root export would make its API snapshot a file that every bump changes.

At `48e4c4c` it is 6 (`sofascore_scraper/store/derive.py:50`): FX-23 (#171) changed the football rule for
`after_extra_time` (finding F10 of the end-to-end test: a match that went straight to penalties showed an
extra-time score; `04-schema-v1.md`, `FootballScore`). Every catalog is rebuilt once on its next open, so
stored data is corrected without a command (`tests/test_score_rederive.py` opens a version-5 catalog with
the stale value). Export files written before are not rewritten; the user exports again. The bump changed
one row of the derive and catalog goldens (event 16950622). The next bump is 7.

At `43ecdfc` it is still 6. FX-16 (#176) changed the slice rows of football, basketball and tennis and only
the `Source` text of `FootballScore.after_extra_time` in the schema models; `store/derive.py` does not read
the slice table, and no derived value changed. FX-26 (#174) and FX-27 (#175) changed none either. The next
bump is still 7.

### 7.3 State db

`sofascore_scraper/store/migrations/state/NNNN_<name>.sql`, applied in order on open under the `maintenance` lease:

1. If `user_version` is behind: copy the file with SQLite's online backup API to
   `.meta/state.db.bak-v<old>` (one copy per old version is kept).
2. For each missing migration: `BEGIN IMMEDIATE`, run the script, `PRAGMA user_version = N`, commit.
3. A failing migration rolls back and the open fails with a `StoreError` that names the script.

Migrations are written to be safe to run again after a crash between the script and the version bump
(`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN` guarded by a column check in a small Python hook).

As built (ST-09, `sofascore_scraper/store/state.py`):

- Since ST-10 (PR #50) the migration step runs under the `maintenance` lease (purpose `op:state_migration`),
  taken with a wait of 5 s: without the wait, two processes that open a new directory together would fail.
  Before ST-10 the migration transaction itself (`BEGIN IMMEDIATE`, with the version read again inside it)
  was the only cross-process mutex. A data-directory change can therefore meet a held lease: `rebind` raises
  `LeaseHeld` when the new directory's `state.db` needs a migration while someone writes there. The same
  wait shows when the Store is opened in a directory that has no `state.db` while another process holds a
  lease there: the open waits the 5 s before `LeaseHeld`. A real holder has always opened the Store first;
  only a test that holds a bare `LeaseManager` lease reaches it, and avoids it by opening the Store once
  beforehand.
- Scripts are split into statements and run one by one inside the transaction, because `executescript`
  commits an open transaction first. Since FX-3 the splitter is the shared one of 3.2. The runner skips an
  `ADD COLUMN` whose column already exists. Script numbers run 0001, 0002, ... without gaps.
- A `state.db` newer than the code raises `SchemaTooNew`. Since FX-2 (PR #54) `src/web/routes/settings.py`
  catches `StoreError` around the job-store rebind, so a data-directory change to a folder the Store cannot
  open (a newer file, a SQLite file that is not a state database, a failed migration, `StoreBusy`,
  `LeaseHeld`) answers 400 `data_dir_unusable` and changes nothing.
- The first open of a directory logs one INFO line per applied script. Since FX-8 (#70) the line is English:
  `Store: state.db migration applied: 0001_initial` (`sofascore_scraper/store/state.py:317` at `e0bae0c`; before, the text
  was Turkish). Since P11 a fresh directory logs two such lines, for `0001_initial` and `0002_job_manager`;
  the pair is in seven places of five CLI goldens. A command that starts opening the Store therefore prints
  these lines on a fresh directory, and its CLI golden changes (`03-implementation-plan.md` section 7).
- Migration `0002_job_manager` as shipped (P11, #69; DDL in 3.3). `state.db` is at schema version 2. The
  first open of an existing directory after the upgrade copies the file to `.meta/state.db.bak-v1` and then
  applies 0002; a fresh directory gets no copy, because nothing existed before. A build from before #69
  then refuses the directory with `SchemaTooNew`, as the forward-only rule says. 2.x is not affected:
  `jobs.db` is still not written. The next state migration is 0003. Migration-runner tests that need a
  world at version 1 use the fixture `first_migration_only` of `tests/test_store_state.py`.
- Other log lines of the two modules are still Turkish: the line of the copy in step 1 (INFO; since P11
  every existing data directory logs it once, when it moves from schema 1 to 2; the CLI goldens start from
  fresh directories and do not show it) and three warnings of `sofascore_scraper/store/lease.py` (the holder row could not
  be written, the unclean marker could not be written, the file system does not support locks). The texts
  of `StoreError` and `LeaseHeld` are Turkish too (an open question of the plan). The plan gives the log
  lines of `state.py` and `lease.py` to the new item FX-11. As built (FX-11, #85) there were five such
  lines, not four: `sofascore_scraper/store/lease.py` also had a DEBUG line (the holder record could not be removed). All
  five are English: INFO `state.db copied before migration (schema version %s): %s`
  (`sofascore_scraper/store/state.py:292` at `9b03c64`), which an existing data directory logs once when it moves to
  schema 2, and in `lease.py` the WARNING lines `The file system does not support file locks (%s); leases
  cannot keep other processes out. Only one process at a time may use this data directory: %s`, `Could not
  write the unclean-shutdown marker: %s: %s` and `Could not record the holder of the lease (%s): %s`, and the
  DEBUG line `Could not remove the holder record of the lease (%s): %s`. The texts of `StoreError` and
  `LeaseHeld` are unchanged. Seven log lines of other Store modules are still Turkish
  (`sofascore_scraper/store/indexer.py:1043` and `:1051`, `sofascore_scraper/store/jobs.py:282` and `:296`, `sofascore_scraper/store/verify.py:547`
  and `:587`, `sofascore_scraper/store/derive.py:202` at `9b03c64`); item (4) of FX-12 translates them. As built (FX-12,
  #100) all seven are English, `sofascore_scraper/store/jobs.py` has one more English warning (a Store closed while a
  finishing job still uses it), and the log-language test scans every module of `sofascore_scraper/store` instead of
  three, with a second test that makes sure the scan finds log calls. The texts of `StoreError` and its
  subclasses are still partly Turkish (for example `Depo kapatılmış` of a closed Store). As built (FX-25,
  #172) they are English: the `default_message` of every Store error class, `from_exception` ("Data could
  not be written to disk (…)"), `SchemaTooNew`, `LeaseHeld` ("The 'writer' lease could not be taken: another
  owner holds the '…' lease of the data directory (pid …, host …, purpose …, since …)") and about 110
  messages of 23 Store modules, with the manifest's validation problems; they reach users as the CLI's
  error text, a failed job's message and `details.store_message` of a v1 error. Six `ValueError`s that
  report a misuse by the code itself stay Turkish (v1 turns them into `internal` without the text), and
  `tests/test_store_messages_english.py` lists them by name. The issue texts of `verify` and of the scans
  (`ssc verify`, rebuild reports) are still Turkish (a follow-up after 3.0.0).
- `open_store` imports the 2.x job rows but does not sweep them: a row that 2.x left as `running` stays
  `running` in `state.db` until a job store looks at that directory. Since P11 that is `reap_stale`, which
  replaces the unconditional sweep (2.3, 6.1): it runs when a `JobStore` is constructed on the directory,
  at the first access to `Store.jobs`, and whenever the job history is read.
- `StateDb.write()` joins an outer transaction on the same thread instead of nesting; `connection()` outside
  `write()` is in autocommit mode.

First creation of `state.db` imports the rows of `.meta/jobs.db` once (read only; recorded in `meta`; the old
file stays where it is). The record `meta.imported_jobs_db` is JSON, `{file, found, rows, imported, at}`. It is
written even when no `jobs.db` exists, so a `jobs.db` that appears later is not imported; an unreadable
`jobs.db` is not recorded and is tried again on the next open. Follows of origin `legacy` are not imported
once but mirrored (2.3).

---

## 8. Statuses, "provisional", refresh and the change log

### 8.1 All statuses are stored

- The Store accepts an event payload in any status. The write-time filters
  (`sofascore_scraper/match_data_fetcher.py:210-214`, `:1038-1043`; `sofascore_scraper/match_fetcher.py:189-200`, `:368-378`) are removed by
  plan item ST-27; until then the callers keep applying them and the Store simply stores what it is given.
  As built (ST-27, #129) they are removed. By then they lived in the pipeline (`FetchPipeline._fetch`) and
  in the listing's event pages, not in the planner: event pages (`events/last/N`, `events/next/N`) are
  stored with every match on them, and an unfinished event that the planner hands over is stored with its
  `/event` payload and the slices that come back with a body, without counting empty answers. The
  `only_finished` keyword of the pipeline and of the listing classes is accepted and ignored for callers
  outside the item; `only_finished` still filters the listing's `chunks`, the old callers' "did this season
  list a match" answer. A round without matches is never stored (`SAVE_EMPTY_ROUNDS` is retired, a
  constant `False`). `FETCH_ONLY_FINISHED` now applies only when data is read; `QueryService.detail_candidates`
  still applies it, which is redundant for planning and harmless. What the planner downloads is in 8.3.
  Corrected on 2026-10-08 (the ninth revision; FX-26, #174, finding M3 of the live validation): it is not
  redundant. A league download takes its candidates from `detail_candidates` through
  `MatchDataFetcher.collect_detail_match_ids` (`sofascore_scraper/services/sync.py:935`,
  `sofascore_scraper/match_data_fetcher.py:846-847` at `43ecdfc`), so with the setting on, the default,
  only finished matches (and those with details or of unknown status) of a followed league get their
  details; unfinished ones are stored from the listings only. Team, player and match follows are not
  affected. The setting's description says so since FX-26 (`02-services.md` 2.1).
- Since FX-27 (#175, finding V5 of the live validation) a slice of an unfinished event that SofaScore
  answered with "no data" (a 404 without a body) is passed to `put` too, uncounted, so it opens a slice row
  in state `empty` with counter 0 (2.3, rule 3); before, the pipeline passed only answers with a body, and
  the slice had no row and was reported as `not_requested` although it had been asked. A failed request is
  still not written, and only answers with a body are stored as payloads
  (`sofascore_scraper/services/pipeline.py:448-454` at `43ecdfc`). Such a slice stays missing for the
  planner (its counter is 0), and completeness counts it as resolved only once a post-match answer has
  counted it (`planning._counted_empty`). The legacy writer `MatchDataFetcher._slice_outcomes` keeps the old
  rule; it is not on the pipeline's path and goes in P30.
- Round files already contain every status today, because they are written unfiltered
  (`sofascore_scraper/match_fetcher.py:471`). Indexing them gives the catalog its first fixtures: locally 628 events that
  have no detail directory (311 not started, 315 completed, 2 void).
- "Finished only" is `EventQuery.status_classes = ("completed", "decided_without_play")`, the same two classes
  `MatchFetcher._is_finished_event` uses (`sofascore_scraper/match_fetcher.py:63-65`). The existing endpoints pass it when
  `FETCH_ONLY_FINISHED` is true, so their output does not change when fixtures start to be listed. As built
  the readers that count or list matches from the catalog apply decision D21 (settled 2026-10-02 with RD-4,
  #78; RD-2, #89 applies it to the two match lists): with `FETCH_ONLY_FINISHED` on, an event counts when it
  is finished or has stored details, with it off every event counts; and a match with stored details that
  no schedule lists counts as a match. The setting is read from the environment when the page is read
  (`os.getenv`), while the writers read it once at import, so after a change in the web UI the counts follow
  at once and the writers only after a restart (P30 moves it into the Settings).
- `status_class` is `classify_status` (`sofascore_scraper/status.py:68-103`); the SofaScore triple is kept in three columns.

### 8.2 Rows from listings, and reconciling copies of a score

A schedule page lists events with their status and score. Today those copies (round JSON, summary CSV,
`basic.json`, export CSV) are never compared. In the catalog there is one row per event and these rules:

1. An event that has an `/event/{id}` payload: its row is always derived from that payload. A listing never
   changes it (except `listed_in`).
2. An event without one: its row comes from the listing with the newest `fetched_at` (`row_source = 'listing'`,
   `has_event_payload = 0`, `layout = NULL`). This is how fixtures appear.
3. When a listing is newer than the stored event payload and disagrees with it in status triple, winner code,
   start time or any score field (the fields `sofascore_scraper/refresh.py:84-92` compares), the row gets `stale = 1`. The
   refresh service reads `EventStore.stale()` and re-reads `/event/{id}` for those first; that write goes
   through `put`, logs the change and clears the flag.

Rule 3 is new behaviour (extra refresh requests) and ships with plan item ST-27; before that the flag is
computed but nothing reads it. As built, P13 (#113) already refreshed a `stale` record in downloads (the
need rule "stale → refresh" comes first). ST-27 (#129) adds the stale rows to `--refresh-only`:
`planning.refresh_due_events` returns `Store.events.stale()` first and then the due provisional rows, each
group in id order (`sofascore_scraper/services/planning.py:255-275` at `b3cb819`). `MatchDataFetcher.refresh_due_ids`
sorts by the legacy path again, so "first" holds for the planner's list only. A stale record is refreshed
also with `REFRESH_WINDOW_HOURS=0`, because a listing correction is not the provisional policy. A stale
refresh does not queue the refill of the record's slices in the same run.

As built (ST-08 #61, `sofascore_scraper/store/entities.py`):

- The time of "the stored event payload" in rule 3 is its `observed_at`, else the `fetched_at` of the event
  slice (`EventRecord.compared_at`). The compared fields are kept as a digest (`entities.compare_digest`,
  `EventRecord.digest`), and `entities.is_stale` is the rule, so a writer can set the flag without reading
  the page. `stale` is maintained by a rebuild, a reconcile and `index_event`.
- A page lists events of its own season. A listing row takes `tournament_id` and `season_id` from its
  directory; an object on the page that names another tournament or season is indexed and reported as
  `season_mismatch`. An event that has a payload is attached to a page (`listed_in`, `stale`) only when its
  payload names the same tournament and season.
- A listing-only row has `layout`, `path` and `sig` NULL, the tournament and season of its directory, and
  `listed_in` set (NULL only for a summary row whose round is neither a number nor a page name). A row from
  a summary CSV has `status_type`, `status_code` and the team ids NULL (3.4).
- `entity_slices` of a legacy season list is `('tournament', <id>, 'seasons', '')`, of a page
  `('season', <season id>, 'schedule', <sub>)`; `path` is the file.
- `changes.fields` is the comma-joined key list of the line's `changed` object and `row_json` is the line as
  written. `ChangeLog.list` and `last_seq` exist since ST-30 (#68; 2.3); `ChangeRow.fields` is that list
  as a tuple and `ChangeRow.row` the parsed line.
- On the fixtures and on the owner's data (738 of 738 rows) `home_score_current`, `away_score_current`,
  `stage_name` and `listed_in` reproduce the summary CSV; `match_date` is
  `datetime.fromtimestamp(start_ts).isoformat()`. The CSV's `round` of a round file is the requested round
  number, while the catalog has `roundInfo.round` in `round` and the page name in `listed_in`.

The catalog row's `home_score`/`away_score` are the normalised values (`display`, else `current`). The summary
CSV of today holds `current`. The two differ for football matches decided on penalties. The legacy list routes
keep returning `current` (from `home_score_current`), so their output does not change; API v1 returns the
normalised score sheet.

### 8.3 What "provisional" means

Today: a stored record is provisional while it was last observed before `startTimestamp + window`
(`sofascore_scraper/refresh.py:63-81`); the window is a setting read at call time (`:27-35`). The rule does **not** look at
the status. It only ever sees records that were finished when they were first stored, because nothing else is
stored today.

Because the window is a setting, the catalog does not store a provisional flag. It stores the two facts,
`observed_at` and `start_ts`, plus `observed_gap = observed_at - start_ts`. The settlement of an event is
computed with the window as a parameter:

| Settlement | Condition |
|---|---|
| `open` | the event has no `/event/{id}` payload; from ST-27 on also: its `status_class` is `not_started`, `live` or `unknown` |
| `provisional` | the event has a payload, an observation, a start time, and `observed_gap < window` (from ST-27 on also: a terminal status class) |
| `final` | `observed_gap >= window`; or `observed_at IS NULL` (legacy record without observation); or `start_ts IS NULL`; or `window = 0` |

`EventStore.refresh_candidates` therefore applies no status condition by default, which is exactly today's
rule: a record that a refresh turned from completed into void, or back into a non-terminal status, keeps being
checked until the window closes. A postponed event whose start moved into the future has a negative gap and
stays provisional until it is observed after the new start plus the window. When fixtures and live events get
their own `/event/{id}` payloads (ST-27 and the live service), the refresh service passes
`status_classes=("completed", "decided_without_play", "void")` plus the ids of `stale` rows, so that open
events are not polled by the refresh policy. From then on a record that went back to a non-terminal status is
`open`: the next listing that disagrees with it marks it `stale` and it is re-read, and the live service
follows it while it is live.

The public field `quality.provisional` is `settlement == 'provisional'`. The first version left it to the
schema design whether the contract also exposes `open`. It does (SC-1, #72; `04-schema-v1.md`, approved on
2026-10-02): the contract has `quality.settlement` with the values `open`, `provisional` and `final`, next
to the boolean `quality.provisional`. The mapper (`settlement`, `sofascore_scraper/schema/mappers.py:361-375` at
`e0bae0c`) already applies the "from ST-27 on" rows of the table, so a non-terminal status class is `open`
today and the contract does not change its meaning when ST-27 merges. The Store's own planning call,
`refresh_candidates`, keeps today's rule without a status condition until then (the paragraph above).

As built (ST-27, #129). The planner passes `status_classes=SETTLED_CLASSES` (completed, decided without
play, void; `sofascore_scraper/services/planning.py:71` at `b3cb819`) plus the ids of `stale` rows, as the paragraph
above says, so the "from ST-27 on" rows of the table now hold for planning too; `refresh_candidates`
itself still applies no status condition by default. In the planner (`compute_need`; `02-services.md`
3.2) an open record that has a payload (not started, live, unknown) needs nothing: it is left to the
listing, which marks it `stale` when a newer page disagrees, and to the live service. A void record awaits
no slices and is only refreshed when due. A row known from a listing only is downloaded when its status is
finished, decided without play or unknown (a summary row without a status), and not when it is not
started, live or void, with the setting on or off; once a newer listing shows it finished it becomes
`full`. A record that went back to live is no refresh candidate. Matches of every status are stored, so a
match fetched on purpose before its end (a single match, an id) is stored and is then open.

### 8.4 Refresh policy on top of the catalog

| Today | With the Store |
|---|---|
| `refresh_due(basic, observation)` per event, after loading its files (`sofascore_scraper/match_data_fetcher.py:830-843`) | `EventStore.refresh_candidates(now, window_s, min_interval_s)`: `observed_gap < :window_s AND observed_at <= :now - :min_interval_s` on the `events_unsettled` index |
| `REFRESH_LEGACY` for records without observation (`sofascore_scraper/refresh.py:49-51`, `:72-74`) | `include_unobserved=True` adds the rows of `events_unobserved` |
| `refresh_due_ids(league_id)` walks the tree (`sofascore_scraper/match_data_fetcher.py:917-935`) | the same call with `Scope(tournament_ids=[...])` |
| `_needs_detail_fetch` → `full / refill / refresh / none` by loading every slice file (`:813-843`) | `full`: `has_event_payload = 0`; `refill`: returned by `missing()`; `refresh`: returned by `refresh_candidates()`; otherwise `none` |
| `refresh_match` writes `basic.json`, `observation.json`, appends to `score_changes.jsonl` (`:856-909`) | the service fetches and calls `events.observe(id, new, on_event_change=...)`; the callback computes `diff_basic` and `change_row` (both stay in `sofascore_scraper/refresh.py`) against the payload that is stored at that moment |
| unchanged refresh only updates `observation.json` (`:897`) | `observe` with an identical payload writes no payload file; it updates `observation` in the manifest and `observed_at`, `observed_gap` in the catalog |
| COMPLETED → VOID keeps the record and sets `status_regressed` (`:878-886`) | same; the flag is sticky in the manifest and in `events.status_regressed` |
| `begin_job_cache` / `end_job_cache` (`:183-190`) | not needed; the catalog is the index |

The policy values and functions (`refresh_window_hours`, `refresh_min_interval_hours`, `diff_basic`,
`change_row`, `status_regressed`) stay in `sofascore_scraper/refresh.py`. The Store only gets numbers, a callback and rows.

As built (ST-30, #68) the read half of this table is pinned against today's code. On the four fixture
directories `missing()` returns the set that `_needs_detail_fetch` calls `refill`, with the same keys, and
`refresh_candidates()` minus the ids that `missing()` returns equals `refresh_due_ids()`, with and without
`REFRESH_LEGACY`; a property test with 12 seeds compares both per event. The differences on the `legacy`
fixture are those of 5.2. The write half (`observe`, the callback) exists since ST-20 (#82; 2.3). Its first
caller is the live service of P23 (#91), which stores a finished match with `observe` and a callback that
follows the refresh rule of `sofascore_scraper/refresh.py`: a change row is written only when the stored payload was
already terminal; a first stored payload, or an older payload of a match that had not finished, is not a
change. This document did not say which. The refresh of the detail writers moves onto `observe` with ST-21.

As built at `b3cb819` the table's right column holds with three differences. The refresh of the detail
writers calls `observe` since ST-21 (#104), and since P13 (#113) through `FetchPipeline` (2.3). The need is
not computed from `missing()` and `refresh_candidates()`: `planning.event_needs` reads `states()` in chunks
of 500 and applies `compute_need` in Python (P12, #106), and only `refresh_due_events` asks
`refresh_candidates()` and `stale()` for its candidates (8.2, 8.3); nothing in `sofascore_scraper/` calls `missing()`.
And `refresh_due_ids` reaches a record without a tournament (a flat or `_no_tournament/` directory) only
when it is called without a league (RD-3, 1.2).

Events with settlement `open` are not covered by this policy. `EventStore.open_events(started_before=now)`
gives the service layer the fixtures and live events whose start has passed; what it does with them is in
`02-services.md` (planner need rules and the live service).

### 8.5 Change log

- New rows go to `changes/<yyyy>-<mm>.jsonl` (month of `ts_utc`), one JSON object per line: the row
  `change_row` builds today plus `"seq"`.
- `seq` is assigned under the write mutex as `max(seq) + 1` and written into the line, so a rebuild reproduces
  it. Legacy lines have no `seq`; theirs is the line number in `score_changes.jsonl`, which stays stable
  because 3.x never appends to that file once the detail writers use the Store (plan item ST-21).
- The line is appended and flushed before the catalog row is inserted. If the process dies in between, the
  next open finds the file longer than the indexed length recorded in `meta` and indexes the tail. A last line
  without a newline is a torn write; it is skipped and reported, and the next append starts on a fresh line.
- As built (ST-20, #82; `sofascore_scraper/store/changes.py` at `9b03c64`). `meta.changes_indexed` holds per file the
  indexed offset, the line count, the size, the mtime and a digest of the last indexed bytes. The index is
  incremental: a file that grew has only its tail read, so an append to a 100,000-line
  `score_changes.jsonl` with its hook costs 0.57 to 0.75 ms (2.2 to 3.0 s before), and the first index of a
  100,000-line log takes 1.2 s on tmpfs; a file that changed in any other way (shorter, rewritten, removed)
  is indexed again from the start. The first open after the upgrade indexes the change log once from the
  start. The indexing order is the legacy file, then the segments by name; a `seq` that two lines carry
  belongs to the first, and the other is reported as `duplicate_seq` and not indexed. That happens only
  when a 2.x writer appends to `score_changes.jsonl` after v3 lines exist, and I7 then reports the gap.
  Since ST-21 (#104) 3.x appends to `changes/<yyyy>-<mm>.jsonl` only (LF line endings on every platform);
  `score_changes.jsonl` is kept and read but never appended to. Since ST-23 (#110) `changes/0000-legacy.jsonl`,
  the copy that `migrate` makes, is a numbered segment (`seq` is the line number): when it exists it takes
  the place of `score_changes.jsonl` in the index, its rows carry it as their `segment`, and the old file's
  rows are not indexed twice (`changes.NUMBERED_SEGMENTS`, `sofascore_scraper/store/changes.py:61-63` at `b3cb819`). A v3
  line is written with `json.dumps(row, ensure_ascii=False)` and `seq` as its last key; `ChangeLog.append`
  writes it under the catalog's write lock (the `max(seq) + 1` above), after it has synced the index with the
  files. `ReconcileReport.changes` is the number of rows in the index after a sync that found a change.
- `Store.clear` never removes the change log, and the in-place rebuild rewrites `meta`, so ST-20's note that
  a clear which removes `changes/` must also drop `meta.changes_indexed` does not apply (ST-19).
- The catalog's `changes` table is an index of these files (`changes?since=` reads it by `seq`).
- The change log has its own gap-free `seq`. The `change` stream (2.3) is a notification: each
  `change.recorded` stream event carries the `change_seq` it announces. Consumers that want every change read
  the change log; consumers that want to be told read the stream.
- The watcher's own `provisional` marker on the first completed status (`sofascore_scraper/watcher.py:256-262`) is part of
  the live event payload and is not changed by this design.

---

## 9. Backup, restore, retention

### 9.1 Backup

`BackupManager.create` takes the `writer` lease, so no bulk job changes the data while the archive is written
(today a data backup is refused while a web job runs, `src/web/routes/data.py:239-246`; the lease extends that
to CLI processes). A live service may keep running: every file it writes is replaced atomically, and a payload
that is newer than its manifest is healed by the reconcile after a restore. As built (ST-19, #90) the caller
holds the lease, not `BackupManager`: the web route takes `writer` with purpose `op:backup` through
`JobStore.exclusive("backup")` and calls the manager inside it (`sofascore_scraper/store/backup.py:15-17` at `9b03c64`).

Archive: a zip in `backups/` named `backup_<scope>_<yyyymmdd>_<hhmmss>.zip`, the pattern the download route
already validates (`src/web/routes/data.py:95`).

| Member | `all` | `state` | `data` |
|---|---|---|---|
| `backup.json` (format 2, app version, layout version, state schema, scope, counts) | yes | yes | yes |
| `.meta/schema.json` | yes | yes | yes |
| `.meta/state.db`, taken with SQLite's online backup API into a temporary file first | yes | yes | no |
| `v3/**`, `changes/**` | yes | no | yes |
| legacy trees and `score_changes.jsonl`, if present | yes | no | yes |
| `.meta/catalog.db` | only with `include_catalog=True` | no | no |
| files passed in `config_paths` (the config file, `leagues.txt`, `league_sports.json`, `.env`), under `config/` | if passed | if passed | no |

`.json.gz` members are stored without a second compression (`ZIP_STORED`); other members are deflated.
`exports/`, `backups/`, `.meta/tmp`, `.meta/trash` and the lock files are never included. Whether `.env` is
passed is the caller's decision (it can hold a proxy password); the service exposes it as `include_secrets`.

Compared with today this adds `state.db` and the change log. Today's backup contains neither `.meta/jobs.db`
nor `score_changes.jsonl` (`src/web/routes/data.py:129-134`).

Format 2 as built (ST-24, #109; `sofascore_scraper/store/backup.py` at `b3cb819`). Where it differs from the table:

- **Scopes.** The design's three (`all`, `state`, `data`) sit next to today's four web scopes, because the
  web route pins them: `config`, and three partial scopes that take their v3 counterpart, `match_details`
  with `v3/events`, `matches` with `v3/tournaments/*/seasons/**`, and `seasons` with the rest of
  `v3/tournaments`. The design did not say what a partial scope holds. The legacy web route still offers its
  five scopes only (P21 can expose `state` and `data`).
- **`backup.json`** holds the format, the app version, the layout version, the state schema, the scope,
  `with_env`, the local and UTC time, `store_id` and counts (files, v3 events, legacy events, tournaments,
  follows, API follows, jobs, stream events, sink cursors). Member paths are relative to the data directory
  (they no longer start with its name).
- **`state.db`** is taken with SQLite's online backup API into `.meta/tmp/backup.<random>`, set to journal
  mode DELETE, and its `leases` rows are dropped.
- **Config files** go under `config/`, and `config/.env` only on request; a backup with `.env` keeps
  `_with_env` in its name and mode 0600. `ssc backup create` passes `leagues.txt`, `league_sports.json` and
  the active `sofascore.toml`.
- **Compression.** Every member whose name ends in `.gz` is stored (`ZIP_STORED`), the history files
  (`*.jsonl.gz`) included, not only `.json.gz`; all others are deflated.
- **Never included**, beyond the list above: `catalog.db` (`include_catalog` is not built),
  `.meta/pending_changes/`, `state.db.bak-v*`, leftover `.*.tmp` files and symbolic links. Files of the 2.x
  watcher at the root of the data directory (`watch_events.jsonl`, `watch_state_*.json`) are not backed up,
  as before.
- **Verify.** `verify(name)` reads every member to check its CRC, checks that member paths are safe, that
  the layout and state versions are not newer than the code, and that `state.db`, when present, passes
  `quick_check` and has the right `application_id`.
- **Prune.** `prune(keep=, max_age_days=)` removes nothing by default; each limit removes what it names, as
  `StreamLog.prune` does. No command calls it (the CLI has `create`, `list`, `verify` and `restore`).

Until the format-2 PR lands (plan item ST-24), the Store produces today's zip layout unchanged (plan item
ST-19), so the first step is a pure move. As built (ST-19, #90) the archive is
`backups/backup_<scope>[_with_env]_<yyyymmdd>_<hhmmss>.zip` in local time, with today's five scopes (`all`,
`config`, `seasons`, `matches`, `match_details`); for `all` and `config` the config files the caller passes go
to the root of the zip under their own names and the `.env` file, when `env_file` is given and exists, as
`.env`, and then the archive is created 0600; the legacy trees go in as `<data dir name>/seasons/...`;
`.meta`, `score_changes.jsonl`, `backups/` and exports stay out. A zip left half written is removed, and a
file-system error is a `StoreError`. The members and the name are the same as before (pinned by
`tests/golden/backup/members.json`); one edge differs: when `backups/` cannot be created the route answers 500
`Backup failed` instead of an unhandled 500. Format 2 (the table above, with `backup.json`, `state.db`,
`config/` and `include_catalog`), `verify`, `restore` and `prune` remain ST-24's, in `sofascore_scraper/store/backup.py`.
ST-24 (#109) built them, without `include_catalog` ("Format 2 as built" above); the five scopes and their
old member names survive as the partial scopes, renamed without the data directory's name. The four old
partial scopes (`config`, `seasons`, `matches`, `match_details`; `BACKUP_SCOPES`, `sofascore_scraper/store/backup.py:74`
at `b6caf2f`) are deprecated in 3.0.0 and removed with the legacy aliases in P30 (decision of 2026-10-03);
`all`, `state` and `data` stay.

Since FX-22 (#165, `1c20fb9`) the settings saved on the web app's Settings page go into the archive too.
`BackupManager.create(..., overrides_file=)` adds `CONFIG_DIR/overrides.json` as the member
`config/overrides.json` in the scopes that take config files (`all`, `state` and `config`; `CONFIG_SCOPES`),
when the caller passes it and the file exists; `BackupService.create` and therefore `ssc backup create` and
the v1 backup job always pass it. The file can hold the proxy address with its password, so such an
archive is created 0600 from the start, as one with `.env` is; its name does not change (only `.env` adds
`_with_env`). `data` never takes it. Archives made before FX-22 have no such member. Only the settings files
are taken by name; a `.lock` file next to them is never a member (FX-25, #172).

Since FX-23 (#171; finding F30 of the end-to-end test) a backup records the job that makes it. The web and
scheduler `backup` jobs pass their id (`BackupManager.create(..., job_id=)`,
`sofascore_scraper/store/backup.py:448` at `48e4c4c`), and the archive's copy of `state.db` holds that job
as `completed` (progress 100, `finished_at`). Before, the copy was taken while the job ran, so every restore
of such an archive brought the job back as running, marked it interrupted and showed a false "the last
backup was interrupted" warning on the Overview. `ssc backup create`, which runs as no job, passes none.

### 9.2 Restore

`BackupManager.restore` needs the `maintenance` lease.

1. Validate the archive: `backup.json` present and its layout and state versions not newer than the code; every
   member path is relative and stays inside the data directory (no `..`, no absolute paths, no drive letters).
2. The target must contain no `v3/`, no legacy trees and no API-created follows. With `force=True` the existing
   content is moved to `.meta/trash/restore-<timestamp>/` first and kept until the restore has finished.
3. Extract to `.meta/tmp/restore/`, then move the top-level entries into place.
4. Open `state.db` (running state migrations if the archive is older), rebuild the catalog unless it was in
   the archive, run `verify()`.

An archive without `backup.json` is a 2.x backup. Its members are `<data dir name>/seasons/...` and so on
(`src/web/routes/data.py:141`); they are restored as legacy trees, which the Store reads in place.

The web UI has no restore today (only the terminal UI copies directories back,
`src/ui/settings_ui.py:459-549`, and it cannot read the web's zip backups), so restore is new functionality.
A merge of an archive into a non-empty directory is not offered: `force` replaces, it never mixes.

As built (ST-24, #109; `BackupManager.restore`, `sofascore_scraper/store/backup.py:649` at `b3cb819`; `ssc backup restore
NAME [--force] [--dry-run] [--yes]`):

- **By name.** It takes only the name of a backup inside `backups/`; another name, a path or `../` gives
  `BackupNotFound`. The archive is read and checked before anything is written: a member that is absolute,
  has a drive letter, a backslash, or a `..`, `.` or empty component, or is unknown, is `BackupInvalid`, and
  so is an unknown format; a newer layout or state schema is `SchemaTooNew`.
- **Step 2, the empty target**, is applied literally (no non-empty `v3/`, no non-empty legacy tree, no API
  follows), and in addition a non-empty `changes/` or `score_changes.jsonl` blocks a restore when the archive
  brings its own. Otherwise `RestoreRefused` with `reasons` (`confirmation_required`, exit 2, with
  `details.occupied` in the services) unless `force`. With `force`, every data entry goes to
  `.meta/trash/restore-<ts>.<random>` only when the archive carries data; a state-only archive replaces only
  `state.db` and keeps the data.
- **Lease.** It takes `maintenance` (`op:restore`) unless this process holds it, and the catalog rebuild and
  the verify after the restore run under the same lease, as `Store.clear` does.
- **Step 3.** The archive is extracted to `.meta/tmp/restore.<random>`, where an older staged `state.db` is
  migrated; the entries that will be replaced go to the trash and the staged entries are renamed into place.
  `state.db` cannot be renamed under an open Store (WAL, and the catalog attaches it): the live file is saved
  to the trash with SQLite's backup API, the staged state is copied over the open database with the same
  API, the lease rows of the running process are put back, and a new `stream_id` is written (2.3). The
  archive's `.meta/schema.json` is not restored: the target keeps its own, and its `store_id`.
- **Rollback.** Any failure removes the placed entries, moves the old data back and reloads the old state;
  if the rollback fails too, the trash is kept and the error names it.
- **Step 4.** On success the catalog is rebuilt in place (the internal path `Store.clear` uses) and
  `catalog.verify()` runs; staging and trash are then removed. There is no `include_catalog`: a
  `catalog.db` member is accepted and skipped.
- **Config files and `.env`** are never restored; they are listed in `RestoreReport.skipped`. One exception
  since FX-22 (#165): `config/overrides.json`, the settings saved on the Settings page, which would
  otherwise be lost with a restore. It is restored only when the caller passes its place
  (`restore(..., overrides_file=)`) and the archive has the member: it is written last in step 3, atomically
  and with mode 0600, and a failure of any step puts the previous file back (or removes it when there was
  none) with the rest of the rollback. A member that is not a JSON object, is larger than 1 MB or cannot be
  read is not restored (`skipped`, a warning without the content). An archive without the member (every
  archive older than FX-22, and every `data` archive) leaves the current file alone. The content is never
  logged. `BackupService.restore` holds the Settings file's lock (`config_files.file_lock` on
  `overrides.json`, the lock `PATCH /api/v1/settings` takes) around the whole restore, then reloads the
  settings; when the restored document cannot be loaded (for example a key this version does not know), it
  puts the previous file back, the previous settings stay in force, and the report moves the member from
  `restored` to `skipped`. The reload is per process: `ssc backup restore` while `ssc serve` runs reloads
  the settings of the CLI process only, and the server sees the restored file at its next reload (`02-services.md`
  2.7).
- **`dry_run`** takes no lease, writes nothing and reports `restored`, `replaced`, `occupied`, `skipped` and
  `counts`.
- **Format 1** (2.x and ST-19 zips, without `backup.json`): the members `<one root>/<seasons|matches|
  match_details>/...` are restored as legacy trees, and files at the root (`leagues.txt`, `.env`, ...) are
  skipped; a zip with two different roots is rejected. No real 2.x zip was available; format 1 was tested
  with zips built the way the 2.x and ST-19 code builds them.
- A migrated directory keeps `changes/0000-legacy.jsonl` through a backup and a restore, because the index
  reads it in place of `score_changes.jsonl` (8.5).

As built since FX-13 (#152) and its Windows fix (#153), a restore also runs as a job of the API
(`restore` with `dry_run: false`, under the job's `maintenance` lease; a non-empty target is refused with
`confirmation_required` and `details.occupied` before any job record is written, and `force` replaces). The
job runs inside the `state.db` it replaces, so the step that loads the backup's state keeps more than the
lease rows (`BackupManager._load_state`, `sofascore_scraper/store/backup.py:817-862` at `b6caf2f`):

- It reads from the open database the lease rows, the rows of every running or queued job and their
  `job_events`, and writes them, with a new `stream_id`, into the **staged copy** in one transaction.
- Then one SQLite backup step copies the staged state over the open database. Another connection (the web
  request that polls the restore job) sees either the old or the new content, and the running job is in
  both. Until #153 the rows went back in a second transaction after the backup step; between the two
  commits another connection saw a history without the job, which on Windows (slow `FlushFileBuffers`)
  lasted long enough for a poll to get 404 `not_found`.
- The rollback loads the saved state through the same function.
- After the restore the job history is the backup's plus the jobs that were running, among them the restore
  job, which finishes in its own row.
- Since FX-23 (#171) a job that is running in the archive but finished in this data folder (the job that
  took an older archive, made before FX-23) keeps its finished row and events from the open database
  (`BackupManager._finished_here`), instead of coming back as running and being marked interrupted.
- One edge is left, unchanged in kind: a write by another thread to a kept job row between the read and the
  backup step (a heartbeat, a cancel request) is not carried over. The restore job writes its own progress
  on the restoring thread, so nothing of its own is lost. `tests/test_restore_keeps_the_running_job.py`
  reads `state.db` through a second connection right after each backup step, for a restore and for a
  rolled-back one.

### 9.3 Retention

Nothing is deleted unless a setting says so. Defaults:

| Data | Default | Mechanism |
|---|---|---|
| payloads, manifests | kept | `EventStore.delete`, `Store.clear(scope)` on request; since FX-19 (#156) also `Store.purge.tournament(id, season_id=)` for one tournament or one season (below) |
| change log | kept | — |
| slice history (odds snapshots) | kept | `HistoryStore.prune(older_than=...)`. As built since FX-15 (#155) the scheduler task `prune-history` with `older_than` calls it as a `clear` job under the writer lease; no task exists by default, so history is kept unless the user adds one. It prunes event history only; the history of `season_odds` (an entity slice, P28) is never pruned (2.3) |
| stream events | 7 days and at most 1,000,000 rows | `StreamLog.prune`, run by the process that holds `live` or `sinks`, once per hour. Since ST-24 (#109) these are the defaults of `prune()` itself (2.3). `prune` exists (ST-18). Since P22 (#73) the sink dispatcher calls it with these values, once per hour (`sofascore_scraper/sinks/dispatcher.py:79-81`, `:586-592` at `e0bae0c`), but no process hosts the dispatcher yet (P23, P25, P19); the live service's call comes with P23. Since P23 (#91) `ssc watch` hosts the dispatcher, and the live service prunes with the same values once per hour (`sofascore_scraper/services/live/supervisor.py:561-572` at `9b03c64`) |
| watcher state | rows of events that are done and older than 7 days are dropped | on `WatchStateStore.save`. As built the age is the row's `updated_at` (its last content change); the check runs on every save, for the saved watcher only, and reads `done` in Python, because SQLite's JSON functions are optional before 3.38 and the minimum is 3.24 |
| jobs | newest 500 rows; 2,000 events per job | As built (P11, #69; `sofascore_scraper/store/jobs.py:616-630` and `:163-172` at `e0bae0c`): job rows beyond the newest 500 are removed when a job is created, with their events; a running or queued row is never removed. Each job keeps its newest 2,000 events: the check runs at every 100th event of a job, so up to 2,099 exist in between |
| backups | kept | `BackupManager.prune(keep=..., max_age_days=...)`; as built (ST-24) it keeps everything by default and nothing calls it |
| `exports/` | kept | the export command can be told to replace its previous output; as built (ST-25) through `overwrite=True` of `Exporter.raw` and `rows` (4.5) |
| `.meta/tmp`, `.meta/trash` | the writer's own staging area and the trash are emptied when the `writer` lease is taken (see below); as built (ST-20) also every entry of no lease that is older than a day | — |
| `state.db.bak-v*` | one per old schema version | — |

`Store.clear(scope)` replaces `src/web/routes/data.py:152-182`. It needs `maintenance`, removes both the v3 and
the legacy form of the scope, and clears the matching catalog rows in the same critical section. Old scope
names map as `match_details` → `events`, `matches` → `schedules`, `seasons` → `seasons`. It never touches
`state.db`, the change log or backups. Until ST-19 builds it, the clear of the web API removes the trees
itself and the hook `shadow_cleared` then rebuilds the catalog in place (ST-11; 3.5); the terminal UI's
clear and restore have no hook yet, and ST-19 moves them onto the Store as well.

`Store.clear` as built (ST-19, #90; `sofascore_scraper/store/api.py:590-642` at `9b03c64`):

- It takes one scope or several (`events`, `schedules`, `seasons`, `all`); an unknown scope is a
  `ValueError`, a read-only Store a `StoreError`. The web route maps today's names through `DataScope`,
  which accepts both.
- It takes `maintenance` (purpose `op:clear`) itself when this process does not hold it, and runs under
  the caller's lease when it does (the web route through `JobStore.exclusive("clear")`).
- "Both the v3 and the legacy form": as built only `v3/events` (for `events` and `all`) of the v3 forms.
  The v3 entity trees under `v3/tournaments` have no writer yet; ST-22 adds them for the `schedules` and
  `seasons` scopes. A legacy tree that exists is removed and created again empty, as the web clear did.
  As built since ST-22 (#98) `schedules` also removes the v3 schedule pages and `seasons` the v3 season
  lists (`entities.clear_v3_listings`, `sofascore_scraper/store/api.py:119-120` and `:640-646` at `b3cb819`), so the
  ST-19 finding that only `v3/events` is removed no longer holds.
- It deletes whole trees itself and moves nothing to `.meta/trash`; it does not use `EventStore.delete`
  (S18, section 0). It keeps `state.db`, `score_changes.jsonl`, `changes/`, `backups/`, `exports/` and every
  file outside the three trees. Decision S18, settled on 2026-10-03, makes this the rule: a clear the user
  asks for removes every form of what it names, and the help of `ssc data clear` says that both layouts are
  removed.
- Under the same lease, after the delete and also when the delete stops half way, it rebuilds the catalog
  in place from the remaining files, so the cleared tournaments leave the catalog too; a rebuild that
  fails leaves the clear successful (a warning, `ClearReport.catalog_rebuilt` False). It needs no outside
  hook.
- The terminal menu's clear still deletes with `shutil` and is followed by `shadow_cleared` (#86, 3.5);
  ST-21 moves it onto `MaintenanceService(store=...).clear` under the maintenance lease, keeping
  `datasets/` and `reports/` in the menu. As built ST-21 (#104) did so (3.5), and P26 (#131) removed the
  menu; the callers of the clear at `b3cb819` are the legacy web clear, the `clear` job of API v1 (under a
  `maintenance` job lease since P21, #126; 6.1) and `ssc data clear`.

`Store.purge`, one tournament's data, as built (FX-19, #156; `Purger.tournament`, `sofascore_scraper/store/purge.py:83-182`
at `b6caf2f`). The first-time-user review found no way to delete one league's data; `Store.clear` deletes
by scope for every tournament. `store.purge.tournament(tournament_id, season_id=None)` deletes what one
tournament, or one of its seasons, has stored, and returns a `TournamentClearReport` (`events`,
`event_dirs`, `listings`, `catalog_rebuilt`):

- **Matches.** Every event the catalog links to the tournament (or season): its v3 directory with its
  history files, and every legacy copy (a directory under `match_details/` named by the event id). Legacy
  league and season folders left empty are removed; the empty bucket directories of `v3/events/` (the two
  levels above an event directory) are left.
- **Schedules.** The v3 season directory (`v3/tournaments/<ut>/seasons/<sid>/`: schedule pages and the
  season's non-match slices) and the legacy `matches/<league>/<season>/` with the season's summary files.
- **The season list,** only when the whole tournament is purged: the v3 tournament directory and, in the
  legacy layout, `matches/<league>/` and the JSON season list under `seasons/`.
- **What stays:** the follows, the change log (`changes/`, `score_changes.jsonl`), the job history,
  backups, exports, and the team and player directories (matches of other tournaments use them too).
- **Lease and catalog.** It refuses a read-only Store, runs under `maintenance` (taken with purpose
  `op:clear` when this process does not hold it, as `Store.clear` does) and then, also when the delete
  stops half way, rebuilds the catalog in place from the remaining files under the same lease; a failed
  rebuild is a warning and `catalog_rebuilt` False.
- **Callers.** `MaintenanceService.clear_tournament` (`sofascore_scraper/services/maintenance.py:106-119`), the API's
  `clear` job with `tournament_id` (and optionally `season_id`), and `DELETE /api/v1/follows/{id}?
  delete_data=true`, which removes a tournament follow and starts that job (`clear_job` in the answer).
- It deletes whole directories itself, like `Store.clear`, and does not use `EventStore.delete`; decision
  S18 (section 0) covers it: the user asked for the delete, so every form of what it names goes.

Staging under `.meta/tmp` (decision S16, settled as chosen on 2026-10-02). The first version said that
`.meta/tmp/` is emptied when the writer lease is taken. That cannot hold: an export stages files there
without any lease (4.5, `02-services.md` 2.8) and the live service writes under the `live` lease, so a job
that starts at that moment would delete their staging files. ST-10 therefore did not wire
`files.purge_staging` to the lease. The rule: a staging entry carries the name of its holder.
`files.new_staging_dir(data_dir, label)` already creates
`.meta/tmp/<label>.<random>`; the labels are `writer` for everything that runs under the writer lease
(promotion and `migrate`, whose area 5.4 writes as `.meta/tmp/migrate/`), `live` for the live service and
`export` for exports. Taking `writer` removes only the `writer` entries, taking `live` only the `live`
entries. Export staging has no lease to hang the clean-up on; an `export` entry that is older than a day is
removed when the writer lease is taken. The item that first stages files implements this together with its
writer: ST-20 (#82) implemented the rule for the writer's entries, ST-23 does it for migrate. As built
ST-23 (#110) needed nothing of its own: `migrate` holds `writer` and stages under that label
(`.meta/tmp/writer.<random>`), not under `.meta/tmp/migrate/` (5.4). ST-25 (#108) stages under
`.meta/tmp/export.<random>` only for a SQLite or Parquet export to a stream; an export to a path stages
next to its target (4.5). ST-24 (#109) uses `.meta/tmp/backup.<random>` for the copy of `state.db` and
`.meta/tmp/restore.<random>` for a restore, which holds `maintenance`.

As built (ST-20, #82; `files.staging_holder` and `files.purge_staging`, `sofascore_scraper/store/files.py:339` and `:345`,
and `LeaseManager._purge_staging`, `sofascore_scraper/store/lease.py:347-359` at `9b03c64`). There is a third label, `put`,
for a write by a process that holds neither `writer` nor `live` (the single-match fetch until P13, the 2.x
watcher); `EventStore` picks the label from the leases this process holds. A granted `writer` lease removes
the `writer` entries, every entry that belongs to no lease (`export`, `put`, an unknown label) once it is
older than a day, and empties `.meta/trash`; a granted `live` lease removes the `live` entries. The rule
needed `holder`, `skip` and `older_than` in `purge_staging` and `LeaseManager.held_here`, which ST-20 added to
`files.py` and `lease.py`.

---

## 10. PR sequence

The one ordered list for both tracks is `03-implementation-plan.md`. The ids used in this document map to it
as follows:

| This document | Plan item |
|---|---|
| fixture factory and reader goldens | G-02 |
| presence predicates and the outcome type in `sofascore_scraper/slices.py` | ST-02 |
| Store core (errors, codec, files, layout, manifest) | ST-03 |
| boundary, layering and API-surface tests | ST-04 |
| legacy reader | ST-05 |
| catalog schema, connections, derive | ST-06 |
| indexer, rebuild, verify; listings, reconcile | ST-07, ST-08 |
| state db, job store on it | ST-09 |
| job store methods for the job manager, state migration 0002 | P11 (merged, #69) |
| leases, facade | ST-10 |
| read API | ST-30 (merged, #68) |
| shadow mode | ST-11 (merged, #75) |
| readers moved to the catalog | RD-1 … RD-5, EX-1 (all merged: RD-1 #80, RD-2 #89, RD-3 #97, RD-4 #78, RD-5 #79, EX-1 #99) |
| follows mirror; watcher state and streams; backup and clear | ST-17, ST-18, ST-19 (merged, #90) |
| v3 writer; history | ST-20, ST-26 (merged, #82 and #92) |
| writers switched | ST-21, ST-22 (merged, #104 and #98; the summary CSV bridge removed by #102) |
| migrate; backup format 2 and restore; raw export | ST-23, ST-24, ST-25 (merged, #110, #109 and #108, with the test fix #111) |
| all statuses, stale-listing refresh | ST-27 (merged, #129) |
| removal of transition code | ST-28 |
| fixes found by the Store pull requests | FX-3 (one connection module; merged), FX-4 (file mode, lower-case subs; merged), FX-5 (presence predicates, upper-case legacy round files; merged, #77), FX-8 (mode of the lock files, the English migration log line; merged, #70), FX-11 (mode of the two SQLite files, the remaining Turkish log lines of `state.py` and `lease.py`; merged, #85), FX-12 (a write keeps its change row, 6.2; `Store.close` waits for a finishing job, 3.2; the `sqlite3.Error` of `Store._sync_schema` as a `StoreError`; the seven remaining Turkish log lines, 7.3; merged, #100) |

Testing tools shared by many of these PRs:

- **Legacy fixture factory** (`tests/store_fixtures.py`): builds a data directory in every legacy form of 5.1
  from the event payloads in `tests/fixtures/status/`, with all combinations of observation and marker files,
  round files with and without `_complete`, event pages, summaries, old `_matches.csv`, the four season-list
  file names, a change log and watcher files.
- **Reader goldens** (`tests/golden/readers/*.json`): the current output of the data-reading endpoints and of
  `_needs_detail_fetch`, `refresh_due_ids`, `collect_detail_match_ids`, `pending_detail_ids`,
  `reset_unavailable_markers` on the fixture directories. Recorded before any reader moves; every reader PR
  must leave them byte-identical or list each difference in its description.
- **Logical dump** (`tests/store_dump.py`): a layout-independent description of a data directory (per event:
  slices with state, counters and payload hash; observation; per season: schedule pages; change log rows). It
  can be produced from a legacy tree and from a v3 tree. Writer PRs must keep the dump equal before and after.
- **Rebuild equivalence**: after any sequence of Store writes, the catalog rows (without `sig`) equal the rows
  after `rebuild()`. As built for the legacy writers (ST-11, #75) this is the shadow check of the whole
  suite, `STORE_SHADOW_CHECK=1`, with the comparison rule of 3.5; `CatalogAdmin.diff_from_rebuild` is the
  comparison that ST-20 extends for v3 sources.
- **Crash injection**: a fixture that makes the Nth `os.replace`, `os.rename` or SQLite commit raise, and one
  that kills a subprocess at a named point; after reopening, `verify(deep=True)` must pass.

---

## 11. Measured facts used in this document

| Fact | Value | How |
|---|---|---|
| Local detail tree | 423 events, 2,522 JSON files, 64.6 MB; 71–74 MB on disk with the export CSVs | `find`, `du`; counted again during reconciliation |
| Compact JSON | 36.3 MB (1.78× smaller) | re-serialised in memory; measured again, identical |
| gzip-6 per slice | 5.99 MB (10.8× smaller than today), 13.3 MB on 4 KiB blocks | section 4.3; byte total measured again, identical |
| Full v3 conversion incl. manifests | 6.47 MB, 2,864 files, 14.7 MB on 4 KiB blocks, 3.7 ms per event with read-back verification | converted into a scratch directory |
| Read all slices of all events | 0.37 s (v3 gzip) against 0.36 s today | section 4.3 |
| Catalog build from the legacy tree | 0.22 s for 1,051 events (423 with details, 628 from listings only: 315 completed, 311 not started, 2 void) | prototype |
| Catalog size | 1.1 MB for the real data; 230 MB at 301,000 events and 2.1 M slice rows | prototype |
| Catalog write | 0.09 ms per event (intent + data transaction) | prototype, tmpfs |
| Manifest | 1.2 KB average, 1.5 KB maximum | conversion above |
| Stat of one manifest | 4 µs | conversion above |
| DDL of section 3.3 | executes on SQLite 3.53.4; plans for refresh candidates, unobserved, open events, season list and stream read use the named indexes | executed in memory during reconciliation |
| Codec round trip on the real data (ST-03) | all 3,389 `.json` files under the owner's `data/` read and round-trip in memory; 84.0 MB as stored, 47.2 MB canonical, 7.5 MB with gzip-6 (11.2 times smaller) | read-only, in memory; the directory has grown since the first measurement |
| Legacy reader on the real data (ST-05) | 423 event directories (all form L1, none combined), 69 observations, 12 unverified empty counts, 90 schedule files listing 1,051 events (628 without a detail directory), 6 season lists, 8 summary files, no change log, no watcher files, no problems, no duplicates. Discovery 0.03 s, full read with payloads 0.85 s, schedule files 0.06 s, logical dump 0.8 s | read-only, warm cache |
| Concurrent first open (ST-06, ST-09) | without a retry around the switch to WAL: 'database is locked' in at least 14 of 300 rounds (catalog) and 11 of 100 rounds with six threads (state db); with the retry 300 of 300 clean | stress tests of the two PRs |
| File-system calls outside the Store (ST-04) | 291 calls in 21 modules, 166 baseline entries in 23 files, at `0aa73b4` (287 calls in 19 modules before PR #43 was merged) | the static check |
| Boundary hook cost (ST-04) | none measurable: 18.2 s for the suite with the hook, 19.1 s on main before it | one run each |
| Catalog from the real data, as built (ST-07, ST-08) | 423 events, all legacy; 2,453 slice rows (2,342 ok, 111 empty); 69 with an observation; 3 tournaments, 4 seasons, 280 participants, 3 categories; no problems, no duplicates. Event directories alone 0.56–0.69 s, the whole tree with listings 0.7–1.0 s, quick verify 0.02 s, deep verify 0.59 s, reconcile 13 ms, catalog 676 KB. No listed event names a tournament or season other than its directory (1,051 checked); no superseded page or season list; nothing stale; the summary CSV is reproduced for 738 of 738 rows | read-only; the catalog was written to a scratch directory |
| Synthetic v3 tree (ST-07) | 20,000 events with 7 slice entries each: rebuild 7.0 s in either mode (0.35 ms per event, about 35 s per 100,000), quick verify 0.26 s (13 µs per event), catalog 18.7 MB; about a third of the rebuild is manifest validation and derive | cProfile, warm cache, tmpfs |
| Listing scan (ST-08) | 0.08 s for 3,800 pages | synthetic; not measured beyond 38,000 listed events |
| Leases and the facade (ST-10) | on a scratch copy of the owner's data (3,460 files): open 16 ms, `info()` 15 ms, `info(sizes=False)` 0.5 ms; lease take and release 0.12 ms, a refusal 10.5 ms; files outside `.meta/` unchanged, `jobs.db` kept its SHA-256 and its 63 rows were imported. Eight processes taking `writer` and `maintenance` 300 times each never overlapped (2,393 acquisitions) | tmpfs; the eight-process run was outside the test suite |
| WAL switch, shared code (FX-3) | eight threads switching a new file at once: 16 of 100 rounds fail without the retry, 0 of 100 with it | Python 3.14.7, SQLite 3.53.4 |
| Stream log and watcher state (ST-18) | 200,200 rows in four streams: append 0.04 ms, read of 500 rows 2.7 ms, `wait` woke after 0.5 ms, prune of 100,201 rows 165 ms; watcher state of 2,000 rows: full save 9.5 ms, one changed row 0.9 ms, load 5 ms. A Store keeps about 8 file descriptors open | tmpfs, no fsync cost |
| Follows mirror (ST-17) | on a copy of the owner's `jobs.db` (63 job rows imported into a new `state.db`): a `ConfigManager` start with the first mirror 1.6 ms, a repeat without changes 0.68 ms (one SELECT, no write), `add_league` with its mirror 1.1 ms; job rows unchanged | scratch copy |

| Read API on the real data (ST-30) | a scratch copy of the owner's data, 1,051 events, 423 with a payload: the refill set from the catalog equals the file-based one (266 = 266), `refresh` and `full` agree, all 2,441 slice payloads and every schedule and season-list payload are readable through the API. `missing()` for the whole catalog 3.7 ms against 376 ms for the file-based need check; `states()` for everything 23 ms; the first list page with its total 0.4 ms; rebuild 0.72 s | read-only scratch copy, warm cache |
| Job store (P11) | a copy of the owner's `jobs.db` (63 rows): import and both migrations 11.6 ms; the rows read as 42 cancelled, 14 succeeded, 7 interrupted; list 1.5 ms; `reap_stale` 0.007 ms; an empty job 1.4 ms; `jobs.db` byte-identical afterwards | tmpfs |
| Lock-file and database modes (FX-8) | a fresh directory opened under umask 002: lock files 0664, the other files and directories 0664 and 0775, `state.db` and `catalog.db` 0644 (0664 since FX-11, row below). New lock files are 0644 under 022, 0600 under 077, 0640 under 027; an existing lock file keeps its mode | POSIX; one account (a lock file that cannot be opened is simulated with a read-only file) |
| Catalog on open (ST-11) | a scratch copy of the owner's data (3,461 files, 423 event directories, 1,051 catalog events): first open 0.75 to 0.8 s (17 ms before #75), every later open 15 ms (1.2 ms before), of which the reconcile 11.5 ms; `quick_check` 2 ms. With every event directory copied ten times (4,230 event directories, 725 MB): first open 5.6 s, every later open 111 ms. Extrapolated to 100,000 legacy event directories: about 2 minutes for the first open and about 2.6 s for every later open (the reconcile is linear, 26 µs per event directory) | tmpfs, warm cache; the original was never opened; the figures for 100,000 are extrapolated |
| Hooks of the legacy writers (ST-11) | on the same copy: a match save +1.3 ms (2.7 ms worst, the same with 4,230 directories); a season's schedule fetch +82 ms when the season's directory changed (141 ms worst) and +1.6 ms when it did not; a season-list save +4 ms; the re-index of `score_changes.jsonl` after a refresh that found a change 13 ms at 1,000 lines, 170 ms at 10,000, 2.2 s at 100,000 | tmpfs, warm cache; the owner's directory has no change log |
| Test suite with the shadow check (ST-11) | 103 s on main (102.8 and 103.4 s; 6,237 tests) against 119 s with #75 (118.2 and 119.1 s; 6,287 tests); 182 comparisons in 17 test files | same machine, alternating runs; runs at a load above 2 left out |
| Presence rules (FX-5) | on the owner's data 2,018 stored slice bodies, none answered differently (1,919 data, 99 no data, 0 malformed; all 239 point-by-point files have data); 90 schedule pages, none with an upper-case name. First open 0.72 s; the open of a catalog stamped with derive version 1 0.73 s (the rebuild); later opens 15 ms | read-only; `open_store` only on a scratch copy; the upgrade from main's catalog was simulated by setting the stamp back to 1 |
| Match detail through the catalog (RD-1) | need computation for 1,051 ids inside a job 494 ms against 359 ms (+0.13 ms per id; 0.26 s of it is JSON decoding of slice files, as before) until RD-3 removes payload loading from it; `_find_match_path` 37 µs per call (`open_store` on an open Store 26 µs, a `realpath`); the route's worker function 568 ms against 594 ms for 423 events | copy of the owner's data, tmpfs, warm cache, best of three |
| Statistics from the catalog (RD-4) | `events.summary()` 228 ms and `info(sizes=False)` 64 ms at 300,000 synthetic events; `StatusService.summary` about 470 ms at 300,000 events in 2,000 tournaments, 72 ms at 50,000, 1.9 ms on the owner's data with kept sizes (17 ms when the sizes are measured; main's dashboard 21 ms) | tmpfs, warm cache; the 300,000-event catalog is synthetic, without files |
| Season lists from the catalog (RD-5) | `get_seasons_for_league` about 1.5 ms per call (a file read before), `get_current_season_id(17)` about 10 ms (0.7 ms before), the sports of six leagues 0.3 ms (0.9 ms before); the first league or season request of a server process 15 ms, 0.75 s when the catalog must be built. Close lock: 100,000 rounds without a crash on CPython 3.10.21 in both modes, while the code without the lock crashed in the `close_all` mode every time | copy of the owner's data; stress script outside the test suite |
| v3 writer and change log (ST-20) | on tmpfs: a `put` of one slice 1.3 ms, a new event 1.2 ms, a promotion of a real event 5.8 ms, `ChangeLog.append` 0.24 ms, the first index of a 100,000-line log 1.2 s; an append to a 100,000-line `score_changes.jsonl` with its hook 0.75 ms (3.0 s on main), the first open of a folder with such a log 1.95 s (8.0 s on main); quick verify reads the `changes` table once for I7, 60 ms at 100,000 rows. All 423 events of a scratch copy of the owner's data (3,460 files) promoted: legacy tree identical, logical dump unchanged, `diff_from_rebuild` empty, deep verify clean, `v3/` 6.8 MB next to 66.8 MB of `match_details/`; the legacy tree byte- and mtime-identical in all 51 interrupted states checked | tmpfs; the original directory was not opened |
| History files (ST-26) | the history scan of a rebuild about 0.15 to 0.2 s more for 3,000 events with 5,000 snapshots | synthetic |
| SQLite file modes (FX-11) | new `state.db` and `catalog.db` 0664 under umask 002, 0600 under 077, 0640 under 027, 0644 under 022, with their `-wal`, `-shm` and `-journal` files; with `connect()` fixed alone `state.db` stayed 0644 under 002 (the identity probe creates it); with only the two database files repaired, leftover read-only `-shm` / `-wal` files make the next `open_store` fail ('attempt to write a readonly database') | POSIX, one account, SQLite 3.53.4; the second account simulated by putting the group bits in place of the owner bits |
| Bound of the reconcile on open (ST-19, S17) | a later open of a copy of the owner's data 21.8 ms with the pass on every open, 5.9 ms with the bound | median of 7 opens |
| A job that finishes while its store is closed (#93) | 14 of 60 stress processes segfaulted on main, 0 of 60 with the fix | stress script outside the test suite |
| Planning from the catalog (RD-3) | a read-only copy of the owner's data, 1,051 events: the needs, the pending ids, `refresh_due_ids`, the collected candidate set (738) and "missing details" equal main's; only the candidate order differs (kick-off order). Pending ids 478 ms → 10 ms, `refresh_due_ids` 461 ms → 0.1 ms, "missing details" 56 ms → 10 ms, the candidate collection 1.7 ms → 8.8 ms | warm cache, best of three |
| Schedule and season-list writer (ST-22) | a read-only scratch copy of the owner's `matches/`, `match_details/` and `seasons/`: first open 1.11 s; all 90 legacy schedule pages and 6 season lists written again through `EntityStore.put`: the schedules and season lists of the logical dump identical, `diff_from_rebuild()` empty (0.8 s), `events_by_layout` still 423 legacy and 628 listing; one `put` 60.6 ms on average, 87 ms at most, because each page re-indexes its whole season from both layouts and lists `matches/` (about 2 s of indexing for a 38-round season) | no network |
| Change intents and the closing wait (FX-12) | a write that records a change row 1.54 ms against 1.45 ms on main, a write with an unchanged payload 1.01 ms against 1.02 ms (300 `observe` calls each, home disk); without the wait, a 15-round stress loop that closed the Store as a job's row finished logged a `StoreError` for the closed `state.db` in every round | the loop runs in a subprocess of the test suite |
| Detail writer on the Store (ST-21) | a read-only copy of the owner's data, 423 matches with details, the writes of a full download replayed: into an empty directory 1.28–1.29 s against main's 1.30–1.31 s (median about 2.1 ms per match on both); a second pass with the same data 1.18 s against 1.59 s (unchanged payloads are not rewritten); over a copy of the owner's tree 4.71 s (median 5.96 ms, p95 14 ms; every match promoted once) against 2.70 s for main's rewrite in place, about 1.1 s of each being the first open. Disk for the same matches: `v3/events` 6.55 MB apparent in 2,625 files, 13.4 MB allocated, against `match_details/` 55.19 MB in 2,803 files, 62.1 MB allocated (8.4 times smaller); `.meta/` 0.81 MB against 0.95 MB. `diff_from_rebuild()` empty, deep verify clean (423 superseded old directories after promotion), `match_details/` byte-identical after promotion, the CSV export (424 lines) of the v3-only and of the promoted copy equal to the legacy export | same machine, idle, warm cache, two runs; no network |
| Raw export memory (ST-25, #111) | `tracemalloc`: a raw export of 12 v3 payloads of 2 MB each (24 MB written) peaks at about 0.75 MB in tree and JSONL; reading a whole payload with `codec.read_raw` (`gzip.decompress`) peaks at about 3.9 times its size; a legacy export peaks at 3.33 payloads on Linux and 3.58 on Windows (CPython over-allocates its unicode writer by 25 % there and by 50 % on Windows), so the test bound is 4 payloads; keeping one more payload would give 4.33 and 4.58. Tree, pretty tree and JSONL byte-identical before and after promotion on both legacy fixtures | synthetic payloads; not measured on the owner's data |
| Backup format 2 and restore (ST-24) | a copy of the owner's data (2,636 data files, 1,051 events; `datasets/` and `reports/` are outside the backup): a format-2 backup in 0.6 s, 8.77 MB, verified in 0.1 s; a format-1 zip built the 2.x way in 0.6 s, 8.78 MB; each restored into an empty directory in 1.0 to 1.1 s with verify clean, identical file hashes, the same event ids, an empty logical-dump difference and an empty rebuild difference | the owner has no `backups/`, so no real 2.x zip was available |
| `migrate` (ST-23) | a copy of the owner's data: first open 1.10 s; dry run 0.87 s (1.20 s with `--exact`); conversion of 423 matches, 90 pages and 6 lists 2.72 s; the delete 2.11 s; CLI wall times 2.1, 2.9 and 2.3 s for the dry run, the conversion and the delete with the purge. Before: `match_details/` 66.78 MB, `matches/` 12.81 MB, `seasons/` 0.04 MB apparent; after the conversion `v3/` 7.23 MB (15.3 MB on disk, 2,970 files), estimated at 7.17 MB from the sample and 7.20 MB with `--exact`; after the delete and the purge the old trees hold nothing and `.meta/` is 1.35 MB. Read-through check after the conversion and after the delete: 0 differences over every event row (storage columns excluded), 2,441 payloads, 2,453 slice states, 90 schedule pages and 6 season lists; the logical dump equal, the catalog equal to a rebuild and deep verify clean each time | warm cache; the owner has no `score_changes.jsonl`, so the change-log copy was not measured |

Not measured: `fsync` cost; behaviour on a cold cache or a spinning disk; gzip speed with stock zlib; anything
on Windows or macOS; any scale above 423 real events. Not measured by the batches of this revision either:
the read API on the 300,000-event catalog of 3.7 (its plans are pinned on 7,200 synthetic events) or with a
cold cache; the open of a directory with more than 4,230 event directories; a data directory that two real
accounts of one group share. Not measured by batches eight to ten: anything on a cold cache or a spinning
disk (every number above is from tmpfs or a warm cache); `STORE_DURABILITY=full`; the bound of S17 at
100,000 event directories; a writer and a reader in two real processes (RD-1, RD-5 cover it only through a
reopen); two real accounts of one group (FX-11 simulated the second account); a prune of the history against
concurrent readers; the network file system fallback of FX-11 on a real mount (the `-journal` mode is tested
with the WAL request refused). Windows, macOS and Python 3.10 were verified only by CI for these items, and
no run used SofaScore. Not measured by batches eleven to nineteen: anything on a cold cache; any data
beyond the owner's 423 matches with details and 90 schedule pages (nothing at 300,000 events for the
planners of RD-3); the export on the owner's data (ST-25's memory figures come from synthetic payloads)
and Parquet with a pyarrow older than 25.0.1 (the floor is 14; CI does not install pyarrow); the
change-log copy of `migrate` on real data; a real 2.x backup zip; a crash test that kills a real process
during `migrate` (the seven checkpoints raise an exception); the planner and the pipeline of ST-27 on real
data. Windows and macOS were verified by CI only, and some of these pull requests were reported before
their Windows job had finished; the Windows peak of the legacy export was found that way (#111). Since
FX-17 (#145) the Linux job on Python 3.10 installs pyarrow 16.0.0, the floor FX-15 set, so Parquet runs in
CI at the floor. The pull requests of the sixth revision (ST-28, P28, FX-13, FX-15, FX-19) measured nothing
on the Store: no timing of `Store.purge` or of the restore that keeps the job record on real data, no prune
of a real odds history, and no odds or non-match payload beyond the recorded samples. The restore fix
(#153) was found and checked by CI's Windows job; the old data folder's close (FX-13) was not run on
Windows.

---

## 12. What changed during reconciliation

Claims that did not hold against the code, and what was changed:

1. **Season-list file choice.** The draft said the web routes take the newest file by mtime. They take the bare
   `<lid>_seasons.json` first (`src/web/routes/common.py:31-33`). Sections 1.1, 1.2 and 5.1 now say so, and the
   Store's rule (newest of all names) is stated as a difference from both readers.
2. **Refresh has no status condition.** The draft defined "provisional" over the terminal status classes and
   put that condition into the `events_unsettled` index. `refresh_due` does not look at the status
   (`sofascore_scraper/refresh.py:63-81`). The index predicate and section 8.3 were changed so that the default is exactly
   today's rule; the status filter is a parameter.
3. **Legacy list scores.** The draft's test "catalog rows equal the summary CSV" cannot pass with
   `home_score = display`: the summary holds `homeScore.current` (`sofascore_scraper/match_fetcher.py:570-571`). Four columns
   were added for the legacy shapes (3.3) and the difference is described in 8.2.
4. **Dashboard counts.** Besides counting only `season_*` directories, today's statistics sum CSV rows and
   season-list files without de-duplicating (`sofascore_scraper/services/stats.py:95`, `:128-131`). Added to 1.2 as a second
   known difference for the statistics reader.
5. **Line references.** CI matrix rows for Windows and macOS are `ci.yml:62-63`; the pytest steps are `:85-93`;
   `NO_TOURNAMENT_DIR` is at `match_data_fetcher.py:58`; the terminal UI's restore is
   `settings_ui.py:459-549`.

Changes that come from reconciling with the service design:

6. **`state.db` holds more.** Job events, sink cursors, runtime facts and all event streams (not only live
   events) are in `state.db`; the service design's "extended `jobs.db`" was dropped. `live_events` became
   `stream_events` with a `stream` column, a de-duplication key and one sequence for all streams.
7. **One outcome type.** `Outcome` is defined once in `sofascore_scraper/slices.py` with the statuses `ok`, `empty`, `failed`,
   `skipped`. The Store stores `failed` as state `error` and ignores `skipped`. `keep_history` moved from the
   outcome to an argument of `put`.
8. **Atomic change detection.** `put(change=row)` became `put(on_event_change=callback)`, so the comparison
   with the stored payload happens under the write mutex. An older observation can no longer replace a newer
   one (`PutResult.superseded`). Both were requirements of the service design.
9. **Follows.** Three origins; the legacy files stay authoritative for legacy follows and are mirrored, not
   replaced. The draft's "state.db becomes the source of truth, the files are exported after each change" was
   dropped because a hand edit could be lost.
10. **Leases.** One implementation in the Store for both tracks; `live` and `sinks` added; holder information
    lives in the `leases` table only.
11. **`migrate` default.** Keeps the legacy copy; `--delete-legacy` is explicit. The draft deleted by default.
12. **Adapters for legacy shapes** moved out of the Store into the services; `compat.py` is gone, and the
    legacy wide CSV is an `ExportService` profile instead of `Exporter.legacy_all_matches_csv`.
13. **`EventStore.states`** was added for the planner, and the ratchet baseline is one file per module.

Corrections after the first Store pull requests (2026-10-01; the same list, by document, is in
`03-implementation-plan.md` section 11):

14. **Old files of the first version.** `round_<n>_matches.csv` and `round_<n>_full.json` live inside the
    season directory; `league_seasons.csv` has a header; the terminal UI accepts `<n>_matches.json`. Sections
    1.1 and 5.1 (G-02, ST-05).
15. **What today's walkers find.** A directory with only the combined file is found by no walker, and no
    walker checks the payload's id. Section 5.1 now states both as corrections the reader makes (G-02, ST-05).
16. **Separate file before combined file**, and `observation.json` always read: the opposite of today's
    loader. Section 5.2, with the three visible effects (ST-05).
17. **Season-list and schedule duplicates.** Rules for `league_seasons.csv`, name-only files and a page stored
    in two directories of a season. Section 5.1 (ST-05).
18. **Outcome.** `fetched_at` None means now; `unavailable` added to the skipped reasons; the breaker is
    still a failed outcome in today's code. Section 2.3 (ST-02).
19. **Validation, manifest, error types** as built. Sections 2.3 and 4.2 (ST-03).
20. **Replace failures and durability.** Non-fatal only in the Store layer; `STORE_DURABILITY` only there;
    file mode 0600; the tight-loop reader on Windows. Section 4.4 (ST-03).
21. **gzip output** is deterministic per machine, not across machines. Section 4.1 (ST-03).
22. **Connections.** The switch to WAL is retried; identity is read in one transaction; connections close
    themselves; errors are mapped. Section 3.2 (ST-06, ST-09).
23. **Event row.** The signature of `derive.event_row`, the source of `home_score`, the shape of
    `scores_json`, `season_sort_key`. Section 3.3 (ST-06).
24. **Query plans.** A third spelling rule, and no `ANALYZE` on `state.db`. Section 3.7 (ST-06).
25. **Replaced catalog** is detected by device and inode, not by size. Section 6.3 (ST-06).
26. **State migrations.** `IF NOT EXISTS` in 0001; the transaction as mutex until leases exist; statements run
    one by one; the import record. `DERIVE_VERSION` lives in `derive.py`. Sections 3.3, 7.2, 7.3 (ST-09,
    ST-06).
27. **`created_at`** of a job gets a column in migration 0002. Section 3.3 (P07).
28. **Job retention** is P11's, not ST-09's. Section 9.3 (ST-09).
29. **Boundary tests** described as built: baseline format, the hook that follows `DATA_DIR`, the stale-entry
    conditions, the update variables, the allow-list form of the layering rule, and the modules that need a
    decision. Section 2.4 (ST-04).
30. **Diagnostics** reads the job history. Section 1.1 (ST-09).

Corrections after batches three and four (2026-10-02; the same list, by document, is in
`03-implementation-plan.md` section 11):

31. **The package root** loads its public names on first use, and exports the facade, the leases, the job
    store, the stream types and the follows names; `sofascore_scraper/web/jobs.py` imports from the root. Section 2.1
    (ST-10, ST-18, ST-17, FX-1).
32. **The facade as built**: no `jobs` attribute (a property since ST-11, item 59), a registry keyed by
    directory and `readonly`, `lease_holder`, `info(sizes=)`, constructors that take the Store. Section 2.3
    (ST-10, ST-18).
33. **Subs are lower-case**; an upper-case sub is rejected, not folded. Sections 2.3 and 5.1 (FX-4).
34. **Stream types.** `StreamEvent`, `StreamRecord`, `StreamBatch` and `StreamHead` are defined; `last_seq`,
    `gap`, the creation of `stream_id` and `head()` on an empty log are specified; `WatchStateStore` has three
    helpers for the 2.x files. Section 2.3 (ST-18).
35. **`CatalogAdmin`** has `ensure`, `index_event`, `stats`, `rebuild(mode=)`, `reconcile(v3=)` and the
    `league_names` argument; `ReconcileReport` is specified. Sections 2.3, 3.5 (ST-07, ST-08).
36. **One SQLite module.** Who raises `StoreBusy`, which errors are converted in which file, the single
    busy rule and splitter, the warning once per file. Section 3.2 (FX-3).
37. **Rebuild as built.** Sidecars are removed before the replace; what a valid v3 event is and the fallback
    to the legacy copy; the participants rule; legacy timestamps; which legacy directory becomes
    `legacy_path`; the scan order with listings per season and summary-only seasons after the events; the
    meta keys; no lease taken by the indexer; the measured times. Sections 3.3, 3.4 (ST-07, ST-08).
38. **Reconcile as built.** The `v3` keyword, the root signatures, `legacy_roots` kinds, the limits of
    signatures, entity tables that only grow. Section 3.5 (ST-08).
39. **Verify as built.** Deep verify and repair exist; I7 and I8 are not checked; quick verify covers legacy
    events by signature. Section 3.6 (ST-07).
40. **File modes** follow the umask in the Store layer; lock files did not at that time (they do since
    FX-8, item 65). Section 4.4 (FX-4).
41. **Watcher files** are still written, as copies, until P23. Sections 1.1, 5.4, 9.3 (ST-18).
42. **Leases as built.** The holder is found by probing the OS locks; three attempts; purposes; backup under
    `writer` as a data operation; the unclean marker; fork; the single-match fetch checks and does not take
    the lease. Section 6.1 (ST-10, FX-1).
43. **Platforms.** The single-process fallback also covers a file system without lock support; 64 shared
    holders on Windows. Section 6.4 (ST-10).
44. **`schema.json`** is created under the state db's write lock and rewritten only by a writable open that
    finds other versions. Section 7.1 (ST-10).
45. **State migrations** run under `maintenance` with a 5 s wait. Section 7.3 (ST-10).
46. **Listing rules.** The payload's time, attribution to the season directory, the newest page for
    tournaments. Section 8.2 (ST-08).
47. **`.meta/tmp`** is not emptied as a whole when the writer lease is taken; staging areas are per holder.
    Sections 5.4, 9.3 (ST-10; decision S16).
48. **Retention of watcher state** uses `updated_at` and no SQLite JSON functions. Section 9.3 (ST-18).
49. **Follows as built.** `Follow`, `ApplyResult` and `FollowConflict` are defined; the precedence of the
    origins, the rules for a taken name and for repeated entries, `update` and `remove` on a missing row,
    `position`, when the mirror runs, `apply_follows` without an open Store, and no `legacy_follows_sig`.
    Sections 2.3 and 3.3 (ST-17).
50. **Leases of the command line.** `main.py` takes `writer` and `watcher:<sport>`; the reset of the
    unavailable markers is a writer; a lease is not re-entrant; the interactive menu takes none. Section 6.1
    (P10).
51. **`open_store(create=False)`** refuses a directory in which only the web job store created `state.db`.
    Section 2.3 (ST-17).

Corrections after batches five to seven (2026-10-02, the second revision of that day; the same list, by
document, is in `03-implementation-plan.md` section 11). Each item says what the document claimed and what
is built:

52. **Result types of the read API.** The document named `Page`, `EventRow`, `EventState`, `MissingRow`,
    `TournamentSummary`, the three entity rows, `ChangeRow` and `SliceError` without defining them, and said
    that timestamps cross the API as `datetime` or float. They are defined; `EventRow` mirrors the `events`
    table; row types carry integer epoch seconds and `SliceInfo` carries `datetime`. Sections 2.1, 2.3
    (ST-30, #68).
53. **The catalog decides whether a payload exists.** The document said `payload()` returns None "when
    there is no payload file" and read as if the catalog were current after `open_store`. `payload()`
    returns None without touching the disk when the catalog shows no payload, and the catalog was not built
    on open until ST-11. Section 2.3 (ST-30, #68).
54. **Query arguments.** The document gave `payloads()` only a return type, let `followed` mean "enabled
    follows", and was silent on the search limit, the orders, rows without a start time and the errors. The
    keys are the manifest's slice names; `followed` is the enabled tournament follows; summary-CSV rows
    cannot be found by name; there are two start orders and no "changed since" order; rows without a start
    time are a second region of the keyset paging; argument errors are `ValueError`, an invalid key or sub
    `LayoutError`. Sections 2.3, 3.7 (ST-30, #68).
55. **Planning calls.** The document said `states()` reads "one indexed range query per batch" and printed
    an inner join for `missing()`. `states()` runs two statements per batch; `missing()` uses a `LEFT JOIN`
    and returns an event without a payload whatever is required; `refresh_candidates()` returns nothing for
    `window_s <= 0`. Sections 2.3, 3.7 (ST-30, #68).
56. **Entities.** The document said `tournament_id` is required for `Ref.season`, and was silent on legacy
    entity payloads and on the form of the sport. Reads do not need `tournament_id`; a legacy round file
    comes without `_complete`, a CSV-only season list as `{"seasons": [...]}`; `sport_of_tournament` is not
    normalised; categories and sports have no read method (ST-22). Section 2.3 (ST-30, #68; SC-1, #72).
57. **Reader retry.** The document said "on `FileNotFoundError`". The missing file surfaces as
    `PayloadMissing`; the row is read once more, and the call returns None when the row no longer shows a
    payload. Section 6.3 (ST-30, #68).
58. **Today's readers against the catalog.** The document listed three differences on old data. Four are
    pinned on the legacy fixture, and `missing()` and `refresh_candidates()` are pinned against
    `_needs_detail_fetch` and `refresh_due_ids`. Sections 5.2, 8.4 (ST-30, #68).
59. **The facade.** The document said that `jobs` is not attached, that `catalog` arrives later and that
    `CatalogAdmin` is not exported. `Store.catalog` exists, `Store.jobs` is a lazy property on
    `JobStore.for_store`, `open_store` has the keyword `sync_catalog`, a read-only Store syncs too, and the
    root exports the admin, its reports and the five hooks. Sections 2.1, 2.2, 2.3 (P11, #69; ST-11, #75).
60. **The job store.** The document listed the additions "with the job manager" in one comment. As built
    there are eleven methods, new arguments of `create_running`, `cancel_requested` and `update`,
    `replace_running=False` for the manager, `MAX(row, mirror)` for the cancel flag, a heartbeat that is
    written to the job row only, and `reap_stale` instead of the sweep at open. Sections 2.3, 6.1, 7.3 (P11,
    #69).
61. **Migration 0002.** The document printed `CREATE TABLE job_events` and `heartbeat_at` without a unit.
    The shipped file has `IF NOT EXISTS` and epoch milliseconds; `state.db` is at version 2, an upgrade
    writes `state.db.bak-v1`, older builds refuse the directory, and the next migration is 0003. Sections
    3.3, 7.1, 7.3 (P11, #69).
62. **Job retention.** The document said it "comes with the job manager". It is built: 500 job rows, 2,000
    events per job. Section 9.3 (P11, #69).
63. **Conflict mapping and the leases of the command line.** The document said the job store keeps its own
    mapping "until the job manager replaces it" and that `main.py` takes `writer`. `conflict_from_lease`
    still has no `instance_running`; the job manager takes `writer` for downloads and refreshes with the
    purposes `headless` and `refresh`; the single-match fetch still only checks; without lock support every
    running row of another process reads as stale. Section 6.1 (P11, #69).
64. **`StoreBusy` in a job.** The document left the decision to P11. A busy `state.db` does not fail a job
    on a progress or log write, and the final write is tried three times. Section 6.2 (P11, #69).
65. **Lock files follow the umask.** The document said in 4.4, 6.1, item 40 and 13 that they are created
    with 0644 whatever the umask. New lock files follow the umask; existing ones keep their mode. Sections
    4.4, 6.1, 13 (FX-8, #70; decision S15 settled).
66. **The SQLite files do not.** The document did not say how `state.db` and `catalog.db` are created.
    SQLite creates them 0644 under umask 002, so a directory shared by two accounts still fails on database
    writes. Sections 4.4, 13 (found by FX-8, #70; plan item FX-11).
67. **Migration log line.** The document quoted the Turkish line. It is English since FX-8; a fresh
    directory logs two lines since P11; other Turkish log lines remain. Section 7.3 (FX-8, #70; FX-11).
68. **Probe and shared locks.** The document was silent. `holder()` reports "not held" for a lock file it
    cannot open while `acquire` raises; shared locks need a writable descriptor; the unclean marker followed
    the umask already. Sections 2.3, 6.1 (FX-8, #70).
69. **The catalog is built and reconciled on open.** The document said that `open_store` never builds or
    reconciles, that nothing calls the reconcile, and that ST-11 would call `ensure()` and `reconcile()`
    under `maintenance`. The open builds an unusable catalog and reconciles a usable one; only the rebuild
    takes the lease, with an in-place build when another holder has the directory; the reconcile takes
    none; a catalog that cannot be synced never fails the open. Sections 2.3, 3.4, 3.5, 6.1 (ST-11, #75).
70. **The unclean rule.** The document tied `v3=True` to `Lease.unclean` of a new writer lease. The open
    takes no writer lease: the rule is that the marker exists and nobody holds `writer`, with `quick_check`
    first, repeated until a writer lease is released cleanly. Sections 3.5, 6.1 (ST-11, #75).
71. **Hooks and `sync_listings`.** The document said that after a schedule or season-list write
    `reconcile()` does the work. Five hooks at eight call sites keep the catalog current; `sync_listings` is
    per kind of source, not per season. Sections 2.3, 3.5 (ST-11, #75).
72. **Comparison rule of the shadow check.** The document left open whether the comparison leaves the extra
    entity rows out. It does: by rows, `meta` and `legacy_roots.scanned_at` excluded, the five entity tables
    only grow, everything else equal, clear rebuilds in place. Sections 3.5, 10 (ST-11, #75).
73. **Logging of the catalog.** The document did not say what is logged. The rebuild summary of the indexer
    is DEBUG and English, the facade writes one INFO line per build, and the reconcile on open is quiet.
    Sections 3.4, 3.5 (ST-11, #75).
74. **Limits of the open.** New: a file rewritten in place is not seen; the reconcile is linear in legacy
    event directories (open decision S17, ST-19); the terminal UI's clear and restore have no hook (ST-19);
    league names come from the follows, not from `ConfigManager` (row 74, RD-5). Sections 2.3, 3.5, 13
    (ST-11, #75).
75. **The schema layer.** The document gave the normalising of odds, statistics, lineups and standings to
    SC-1 and left open whether `open` is exposed. Schema v1 gives slice payloads raw (P28 adds odds), and
    `quality.settlement` exposes `open`. Sections 2.3, 8.3 (SC-1, #72; decision P2 settled).
76. **Staging under `.meta/tmp`.** Decision S16 (a staging entry carries the name of its holder) was open
    when the document wrote the rule down. It is settled as chosen, and ST-20 (in progress) implements it.
    Section 9.3.
77. **Stream pruning.** The document said nothing calls `StreamLog.prune`. The sink dispatcher does, and no
    process hosts the dispatcher yet. Sections 2.3, 9.3 (P22, #73).
78. **Measurements** of ST-30, P11, FX-8 and ST-11 were added, with what was not measured. Section 11.

Corrections after batches eight to ten (2026-10-02, the third revision of that day; the same list, by
document, is in `03-implementation-plan.md` section 11). Each item says what the document claimed and what
is built:

79. **Three answers of the presence rules.** The document mapped a legacy slice file to two states, "`ok`
    when the presence predicate says it has data, else `empty` with a payload", through the
    `match_detail_slice_present` family. As built `slices.slice_body_state` gives three answers: data
    (`ok`), no data (`empty` with a payload) and malformed (`error` / `corrupt` with a `malformed` problem);
    the rule per slice is printed, and `{"pointByPoint": []}` is no data. Sections 2.3, 11 (FX-5, #77).
80. **The derive version.** The document called it the version of `derive.py`'s output. It is also bumped
    when a rule outside that module changes the rows the same files give (the presence rules, the legacy
    names); it is 2, and the bump rebuilt every catalog once. Sections 7.1, 7.2 (FX-5, #77).
81. **Legacy round names are folded.** Decision S13 said that an upper-case sub is rejected and never
    folded, and 5.1 that such a round file is `unknown_name`. Since FX-5 `legacy.schedule_sub` folds the
    slug of a 2.x round file name to lower case; two names that fold to one sub are one page, the other is
    superseded; the file keeps its name, so later items read a page by its path. Sections 0, 2.3, 5.1, 5.4,
    13 (FX-5, #77).
82. **Readers through the catalog.** The document described the walkers of `3ae2599` and named two
    differences of the statistics reader; 5.1 said that neither season-list reader takes the newest file.
    The match detail, the statistics, the season lists, the sports and the match lists read the catalog;
    the statistics differ in two more ways, settled as decision D21; the id rule and the two-places rule of
    5.1 are visible; and a read through the Store raises no file-system event for the shadow recorder.
    Sections 1.2, 3.5, 5.1, 5.2, 8.1 (RD-1 #80, RD-2 #89, RD-4 #78, RD-5 #79).
83. **Gaps of the read API.** The document was silent on how a reader gets the observation, whether the
    catalog is current, and what `summary()` and `EntityStore` cannot answer. The event row is the
    observation (its `change_ts` is the payload's); `Store.catalog_current` exists since ST-19; `summary()`
    has no count of unfinished events with a payload and no season-list count; no call lists the
    tournaments with a season list, so `season_lists` scans the legacy list files and `seasons_of` reads the
    payload; `EventRow.path` is legacy-only; `EventQuery` has no OR and no date-substring filter;
    `Store.info` has no cache. Section 2.3 (RD-1 #80, RD-2 #89, RD-4 #78, RD-5 #79, ST-19 #90).
84. **A GET route may create `.meta/`.** The documents did not say so. The first read of the match detail,
    the statistics or a league or season route opens the Store, which creates `.meta/schema.json` and
    `catalog.db`. Section 7.1 (RD-1 #80, RD-4 #78, RD-5 #79).
85. **Closing a connection.** The document said that connections close themselves and was silent on two
    threads closing one connection. On Python 3.10 that aborts the process; `Connection.close` takes the
    re-entrant module lock `_close_lock` since RD-5. `Store.close()` while another thread reads still
    breaks the precondition of `close_all`, it does not wait for a finishing job (FX-12), and
    `Store._sync_schema` lets a bare `sqlite3.OperationalError` out (FX-12). Sections 3.2, 13 (RD-5 #79,
    FX-11 #85, #93).
86. **The write half as built.** The document printed `put` with rules for "an id the catalog does not
    know", "ok when the caller said so" and a `PutResult` whose `created` means "did not exist". As built a
    `put` without an event outcome needs a stored event payload; a 404 or an error leaves an `ok` slice
    `ok`; `failed` / `breaker` counts as skipped; an uncounted empty creates a row; `on_event_change` gets
    `None` before any file is written; superseded compares with the observation or the event slice's
    `fetched_at`; `reset_empty_markers` has `threshold`; `created` means "had no stored payload" and
    `written` holds slice names; a damaged manifest is replaced only by a write with an event outcome; the
    catalog is not inspected per call. Section 2.3 (ST-20, #82).
87. **History as built.** The document described `keep_history` against "the last stored" hash and gave
    `HistoryStore` without details. The comparison is with the last snapshot, only outcomes with data are
    kept, `PutResult` has the field `history`, `index` gives whole seconds, `n` changes with a prune, and
    `prune` needs this process's writer lease and handles events only. Section 2.3 (ST-26, #92).
88. **The write protocol as built, and the lost change row.** The document gave six steps with the
    change-log line among the catalog work of the last one. As built the marker is inserted with
    `INSERT OR IGNORE` and checked again under the lock (`StoreBusy` after five rounds), and the line is
    appended after the payload and the manifest; a write that dies between the new payload and the line
    loses the change row for good. That is a known defect; the new fix item FX-12 changes the order, and
    since P23 `ssc watch` can meet it. Sections 2.3, 6.2, 13 (ST-20 #82, ST-26 #92, P23 #91).
89. **The indexer trusts the file only under a marker.** The document said that after a crash between
    payload and manifest "the indexer trusts the file". A rebuild takes the slice rows from the manifest;
    `heal_v3_event` trusts the file only where an intent marker says a write was unfinished, and since
    ST-26 also sets the history mark from the file. Section 4.4 (ST-20 #82, ST-26 #92).
90. **Promotion as built.** The document's promotion copied every legacy file. Extra files and the unknown
    keys of a combined file are not copied until ST-23 adds `_extra/`; the manifest's times are the legacy
    mtimes; a negative `change_ts` or status becomes null; the fetcher's need path still knows only legacy
    directories. Sections 5.3, 5.4 (ST-20 #82; RD-1 #80).
91. **The change log as built.** The document described the indexed length in `meta` and the hook that
    re-indexed the whole file on every append. `meta.changes_indexed` holds offset, lines, size, mtime and
    a tail digest; the index is incremental; a `seq` carried twice is `duplicate_seq`;
    `ReconcileReport.changes` is the row count after a sync that found a change;
    `changes/0000-legacy.jsonl` is not a segment; a clear never removes the log. Sections 3.5, 5.4, 8.5
    (ST-20 #82, ST-19 #90).
92. **Rebuild steps 1, 3 and 7.** The document listed the v3 entity scan and the history files as steps of
    their own and said they come later. The entity scan is looked up by name until ST-22 provides it;
    history rows come with each v3 event record. Section 3.4 (ST-20 #82, ST-26 #92).
93. **Verify checks I7 and I8.** The document said neither is checked. I7 applies to the v3 segments
    (`seq_gap`, `seq_unindexed`, deep `seq_mismatch`); I8 stats the history files in the quick form and
    reports `history_file`, `history_rows` and, deep, `history_member`. Section 3.6 (ST-20 #82, ST-26 #92).
94. **Staging labels.** Decision S16 named the labels `writer`, `live` and `export`, and an `export` entry
    older than a day was the only one the writer lease removed. There is a third label, `put`, and the
    writer lease removes every entry of no lease after a day and empties the trash. Section 9.3 (ST-20,
    #82).
95. **The SQLite files follow the umask.** The document said in 4.4, 11 and 13 that `state.db` and
    `catalog.db` are created 0644 whatever the umask. Since FX-11 a new database file is created with the
    Store's mode before SQLite opens it, in three places; a recreated catalog and the copy before a
    migration follow the writer's umask; the repair line for an old directory is
    `chmod g+w DATA_DIR/.meta/state.db* DATA_DIR/.meta/catalog.db*`, next to FX-8's line for the lock files.
    Sections 4.4, 11, 13 (FX-11, #85).
96. **Log lines of the Store.** The document said that four Turkish log lines of `state.py` and `lease.py`
    remain for FX-11. There were five; all are English, and seven lines of `indexer.py`, `jobs.py`,
    `verify.py` and `derive.py` remain for FX-12. Section 7.3 (FX-11, #85).
97. **The bound of the reconcile on open.** The document said that the full pass stays on every open and
    that decision S17 is open. S17 is settled and built: an open skips the event pass within
    `STORE_OPEN_RECONCILE_SECONDS` (60 s) of a full pass of the same real path; only the constructor's open
    applies it; the listing half always runs; hand edits to match folders can stay unseen for that long.
    Sections 2.3, 3.5, 11, 13 (ST-19, #90).
98. **`shadow_cleared` and the terminal menu.** The document said that `shadow_cleared` follows the web
    clear and that the terminal UI's clear and restore have no hook until ST-19. The web clear deletes
    inside `Store.clear` and needs no hook; since #86 the menu's clear, restore and data-directory move call
    `shadow_cleared`, without a lease. Sections 1.1, 3.5, 13 (ST-19 #90, #86).
99. **Clear and backup as built.** The document said that `BackupManager.create` takes `writer`, gave the
    format-2 signature, and said that a clear removes the v3 and the legacy form. The caller holds the
    backup's lease; the backup writes today's zip with today's five scopes, config files at the root and
    `env_file` apart; a clear removes the legacy trees and `v3/events` only, takes `maintenance` itself when
    needed, moves nothing to the trash and rebuilds the catalog under its lease. Sections 2.3, 6.1, 9.1, 9.3
    (ST-19, #90).
100. **A delete that removes legacy copies.** Decision 4 says that nothing old is deleted outside `migrate`.
     `EventStore.delete` removes every legacy copy of the event through `.meta/trash`. The question is the
     open decision S18, with the proposal that decision 4 governs writes and a delete the user asks for
     removes every form. Sections 0, 2.3, 9.3, 13 (ST-20 #82, ST-19 #90).
101. **The watcher's state file.** The document said that the watcher writes `watch_state_<sport>.json` as a
     copy until P23. P23 removed the copy and `mirror_legacy_state`; `--watch` and `ssc watch` share the
     state in the table under the sport's name. Sections 1.1, 2.3, 4.4, 5.4 (P23, #91).
102. **`instance_running`.** The document said that `conflict_from_lease` has no `instance_running`. A
     `live` or `watcher:<sport>` holder that blocks a data operation gives `InstanceRunningConflict`, a
     subclass of `DataOperationRunningError` with that code. Section 6.1 (P23, #91).
103. **A job keeps its lease until its last store access.** The document was silent on closing a job store
     while a job finishes. A job does its last store accesses inside a finishing block and releases the
     lease after it; `rebind`, `exclusive` and `JobStore.close` respect the block, `Store.close` does not
     yet. Sections 3.2, 6.1 (#93, #88).
104. **Who announces a change.** The document said that a stream event that announces a write is appended
     after the commit, and did not say when the live confirmation writes a change row. `put` appends no
     stream event: the caller announces `PutResult.change_seq`; the live service writes a change row only
     when the stored payload was already terminal. Sections 2.3, 6.2, 8.4 (ST-20 #82, P23 #91).
105. **The `_winapi.CopyFile2` audit event.** The runtime check of 2.4 did not list it, so a `shutil.copy2`
     on Windows was invisible to it. #86 found it and #88 added it to both event sets. Section 2.4.
106. **Measurements** of FX-5, RD-1, RD-4, RD-5, ST-20, ST-26, FX-11, ST-19 (S17) and #93 were added, with
     what was not measured. Section 11.

Corrections after batches eleven to nineteen (2026-10-03, the fifth revision; the same list, by document, is
in `03-implementation-plan.md` section 11). Each item says what the document claimed and what is built:

107. **Decision S18 is settled.** The document called S18 open and proposed that decision 4 governs writes
     while a delete the user asks for removes every form. The owner settled it as proposed on 2026-10-03: a
     write, promotion, refresh, rebuild, reconcile or repair never deletes legacy data; a delete or a clear
     that the user asks for removes the legacy copies too and says so in its help text. Sections 0, 2.3,
     9.3, 13 (ST-20 #82, ST-19 #90, ST-21 #104, ST-22 #98).
108. **The change row of an interrupted write is kept.** The document described the lost change row as a
     known defect. Since FX-12 the row is written to `.meta/pending_changes/<event_id>.json`, with the
     sha256 of the new event payload, before any payload file is touched, and removed after the line is
     appended; the next write of the event and every reconcile recover it; a rebuild does not read the
     intents; `PutResult.change_seq` can carry a recovered row's seq; a row that is not JSON fails before
     the disk is touched; the new crash checkpoint is `change_intent`. Sections 2.3, 3.5, 4.2, 6.2, 13
     (FX-12, #100).
109. **`Store.close()` and `_sync_schema`.** The document said that `Store.close()` does not wait for a
     finishing job and that `_sync_schema` lets a bare `sqlite3.OperationalError` out. `close()` waits up to
     30 s through the classmethod `JobStore.wait_for_finishing_job`, and `_sync_schema` raises a
     `StoreError` (with `EROFS` for a read-only database). The seven Turkish log lines are English, and the
     log test covers every Store module. Sections 2.3, 3.2, 6.1, 7.3, 13 (FX-12, #100).
110. **What to download is read from the catalog.** The document listed the walkers of the planners and said
     that "missing details" uses `Store.events.missing` and that the planners refuse an out-of-date catalog.
     The planners read the catalog and raise `CatalogNotCurrent`; "missing details" takes the listed events
     without a payload and does not refuse; a flat or `_no_tournament/` record is reached only without a
     league filter; a row whose summary round is not recognised is no candidate; the order inside a season
     is kick-off order; `FETCH_ONLY_FINISHED` applies when the plan is made. Since P12, P13 and ST-27 the
     planner reads `states()` and nothing calls `missing()`. Sections 1.2, 3.5, 5.2, 8.4 (RD-3 #97, P12 #106,
     ST-27 #129).
111. **`EntityStore.put` as built.** The document printed `put` with `index_listed_events` and said that
     the entity scan is looked up by name until ST-22. There is no such keyword (listing rows come from the
     stored season pages only); a season id that has a v3 directory under another tournament is refused;
     `tournament.json.gz` is not written; season lists and schedules of both layouts are applied together
     with v3 first; v3 entity directories have no signature, so a change behind the catalog there is found by
     a pending marker or a deep reconcile; categories and sports have read methods; entity history is
     written and indexed, but `HistoryStore.prune` still handles events only; there is still no listing of
     the tournaments that have a season list. Sections 2.3, 3.4, 3.5, 4.2 (ST-22, #98).
112. **A clear removes the v3 listings.** The document, following ST-19, said that a clear removes only
     `v3/events` of the v3 forms. `schedules` and `seasons` also remove the v3 schedule pages and season
     lists. Section 9.3 (ST-22, #98).
113. **The writers use the Store.** The document described the detail, schedule and season-list writers as
     2.x code with hooks, and said in 3.4 that a catalog update never fails a write. They call `put`,
     `observe`, `reset_empty_markers` and `EntityStore.put`; the catalog is updated inside the write, so a
     store that stays busy fails that match after four attempts; no `_no_tournament/` directory is written;
     a refresh appends to `changes/<yyyy>-<mm>.jsonl` and never to `score_changes.jsonl`; the summary JSON
     (ST-22) and the summary CSV (#102, decision S4) are no longer written, and the indexer and the legacy
     reader still read existing ones; `STORE_DURABILITY` is in `.env.example`. Sections 2.3, 3.4, 4.4, 5.1,
     5.3, 8.5 (ST-21 #104, ST-22 #98, #102, P13 #113, P14 #116).
114. **The hooks have no caller.** The document listed eight call sites of the five hooks of the legacy
     writers. None is left: the writers moved onto the Store, the last summary writer went with #102, and
     the menu's `shadow_cleared` calls went with P26. The hooks and their tests stay until ST-28; the
     resync of the test suite runs when a planner starts. Sections 1.1, 3.5, 13 (ST-21 #104, ST-22 #98, #102,
     RD-3 #97, P26 #131).
115. **The terminal menu.** The document said that the menu's clear deletes with `shutil` and that its
     downloads take no lease. ST-21 moved the clear onto `MaintenanceService.clear` under `maintenance`, for
     both layouts, with notices on the backup, the restore and the disk sizes; P26 removed the menu. Sections
     1.1, 3.5, 6.1, 9.3, 13 (ST-21 #104, P26 #131).
116. **Raw and row exports as built.** The document printed `Exporter` without `overwrite` and without its
     report types, staged exports in `.meta/tmp`, looked up an export batch's slices by a primary-key range,
     copied the stored bytes of legacy events and spliced whole JSONL payloads. `raw` and `rows` take
     `overwrite`, `rows` also `types` (SC-2); `ExportReport` and `ExportSkip` are specified; an export to a
     path stages next to its target and only a SQLite or Parquet export to a stream uses
     `.meta/tmp/export.*`; the slices are looked up by `event_id IN (...)`; legacy payloads are re-encoded to
     canonical bytes; JSONL payloads are streamed too; a missing or corrupt payload is skipped and reported.
     Sections 2.3, 3.7, 4.5, 9.3 (ST-25 #108, #111, SC-2 #130).
117. **Backup format 2 and restore as built.** The document gave three scopes, `include_catalog`, `dest`,
     restore by archive path and `prune` returning a count, and did not say whether config files are
     restored. There are seven scopes (the partial ones take their v3 part); `include_catalog` is not built;
     every `.gz` member is stored; config files and `.env` are never restored; restore takes a name inside
     `backups/`, refuses a non-empty `changes/` when the archive brings one, loads `state.db` into the open
     database with the backup API, keeps the target's `schema.json`, writes a new `stream_id` and rebuilds the
     catalog under the maintenance lease; `force` with a state-only archive keeps the data; `prune` returns
     the removed backups, keeps all by default and has no caller; `BackupInfo` has `format`. Sections 2.3,
     9.1, 9.2, 9.3 (ST-24, #109; P21 #126 for `path_of`).
118. **Stream log defaults and cursors.** The document said that `prune()` without arguments removes
     nothing, that a method listing the sink cursors is missing, and that `stream_id` changes only when
     `state.db` is recreated. `prune()` defaults to 7 days and 1,000,000 rows, `cursors()` returns
     `SinkCursor` rows, and a restore writes a new `stream_id`. Section 2.3 (ST-24, #109).
119. **`migrate` as built.** The document staged in `.meta/tmp/migrate/<id>/`, took a `Scope`, and was silent
     on how unknown keys of a combined file, unrecognised entries, empty folders, the copy of the change log
     and the derived files of the first version are handled. Staging is `.meta/tmp/writer.*`; `plan` and
     `run` take tournament ids, `plan` the options of the run; a combined file with unknown keys is copied
     whole into `_extra/`; deep verify skips `_extra/`; unrecognised entries stay; every empty legacy
     directory is removed; season lists named after the league need the follows; the copy of
     `score_changes.jsonl` is a numbered segment that replaces the old file in the index; `--limit` counts
     matches; step 6 has two checkpoints; `migration_runs.dry_run` is always 0; `scripts/catalog_tool.py` is
     replaced by `ssc catalog`. Sections 2.3, 3.3, 3.5, 3.6, 4.2, 5.3, 5.4, 8.5, 9.3 (ST-23, #110).
120. **All statuses are stored.** The document said that the write-time filters live in the callers until
     ST-27, that rule 3 ships with ST-27 and that `refresh_candidates` keeps today's rule until then. Event
     pages keep every match and the pipeline stores unfinished events; `SAVE_EMPTY_ROUNDS` is retired;
     stale records are refreshed by downloads since P13 and first in `--refresh-only` since ST-27; the
     planner passes the settled classes, while the Store call still has no status default; open records need
     nothing and listing-only unfinished rows are not downloaded; a v3 event page keeps `{filtered: true}`,
     which now means "a page of the de-duplicated event list". Sections 5.2, 8.1, 8.2, 8.3 (ST-27 #129,
     P13 #113, ST-22 #98).
121. **The derive version is 5.** The document said that it is 2 and the next bump 3. SP-1, SP-2 and SP-3
     bumped it to 3, 4 and 5 for the score sheets of eighteen new sports and cricket's day-end status; each
     bump rebuilt every catalog once. Section 7.2 (SP-1 #112, SP-2 #115, SP-3 #118).
122. **`missing(exclusive=True)`.** The document printed `missing()` with `""` always added to a sport's
     keys. The keyword `exclusive` lets a sport's own entry replace `""`. Section 2.3 (#121).
123. **Downloads announce their changes.** The document said that the live service announces
     `change.recorded`. Since P13 the download pipeline does too, with the same `dedup_key`; a row recovered
     at open is not announced. Sections 2.3, 6.2 (P13 #113, FX-12 #100).
124. **Leases at `b3cb819`.** The document said that a job holds `writer` only and that the menu takes no
     lease. A job can hold `maintenance` (`create_running(lease=)`), the backup, restore, `migrate` and
     `catalog` commands take their leases as listed, the menu is gone, and the single-match fetch still only
     asks `writer_busy()`. Sections 6.1, 9.3 (P21 #126, ST-24 #109, ST-23 #110, P26 #131, P13 #113).
125. **A data directory on Windows.** The document did not say that the registry's open Store keeps a data
     directory busy. On Windows it can be deleted or moved only after `Store.close()`, and the settings
     routes do not close it yet. Sections 1.1, 2.3, 6.4, 13 (#120).
126. **The root exports.** The document said that no version number is exported from `sofascore_scraper.store`. Since P19
     `LAYOUT_VERSION`, `CATALOG_SCHEMA` and `load_migrations` are, and the items of these batches added their
     types. Section 2.1 (P19 #119; ST-22, ST-23, ST-24, ST-25).
127. **Measurements** of RD-3, ST-22, FX-12, ST-21, ST-25 with #111, ST-24 and ST-23 were added, with what was
     not measured. Section 11.
128. **No submodule import, no 2.x helper in the Store.** The document said that `src/fsutil.py` imports
     `sofascore_scraper.store.files` and re-exports the 2.x helpers, which keep 0600 and raise `ReplaceBusy`. ST-28
     deleted the shim; the helpers are `sofascore_scraper/config_files.py`, outside the Store (0600, the plain
     `PermissionError` after the Windows retries), and the Store root exports `league_dir_name`. Sections
     2.1, 2.2, 2.3 (error table), 4.4 (ST-28 #135).
129. **The boundary checks are strict.** The document described the ratchet, the `STORE_BOUNDARY_UPDATE`
     modes, the open points ST-28 had to settle and the writes the shim hid. The baseline is gone; the
     allowlist names seven more modules; five functions are named exceptions, each with its reason and the
     item that removes it, and an exception that no longer occurs fails. Section 2.4 (ST-28 #135).
130. **The shadow hooks are gone.** The document listed the five hooks among the root's exports and in the
     table of 3.5. FX-15 removed them with their tests; `_shadow` wraps only `Store.clear`'s rebuild, and
     the shadow check covers product writes under legacy roots and the directories `Store.clear` touched.
     Sections 2.1, 3.5, 13 (FX-15 #155).
131. **The first caller of `HistoryStore.prune`** is FX-15's scheduler task `prune-history`, off by default,
     not P28; entity history is written since P28 (`season_odds`) and is not pruned; no concurrency test was
     added. Sections 2.3, 9.3, 13 (FX-15 #155, P28 #140).
132. **Non-match slices as built.** The document's examples had a standings sub `away` and a squad slice
     `players`; neither exists in the endpoint catalog. Team and player match lists are not stored. The odds
     slices record the provider always and the country only when the user sets it. Section 2.3 (P28 #140,
     FX-15 #155, FX-19 #156).
133. **The restore keeps the running job.** The document said that the backup's `state.db` is loaded over
     the open one and the lease rows are kept. The running and queued jobs are kept too, and the kept rows go
     into the staged copy before the one backup step, so no reader sees a history without the restore job;
     a write to a kept row between the read and the step is lost. Section 9.2 (FX-13 #152, #153).
134. **Deleting one tournament's data.** The document knew only the clear by scope. `Store.purge.tournament`
     deletes one tournament's or one season's matches, schedules and (for the whole tournament) season
     list under `maintenance`, rebuilds the catalog, and keeps follows, changes, jobs, backups, exports and
     the team and player directories; empty `v3/events/` buckets stay. Sections 2.2, 2.3, 9.3, 13 (FX-19
     #156).
135. **The old data folder is closed.** The document said that the settings routes do not close the Store
     of the old data folder. The v1 route does since FX-13, unless this process holds one of its leases.
     Sections 1.1, 6.4, 13 (FX-13 #152).
136. **Smaller corrections.** `Scope.followed` still means tournament follows only, although team, player
     and event follows download their matches (2.3, FX-19); `Migrator.last_run()` feeds the status pages
     (3.3, FX-13); the four old backup scope names are deprecated (9.1, decision of 2026-10-03); the
     `parquet` extra needs pyarrow 16 and CI installs it (4.5, FX-15, FX-17).
137. **The Settings page's settings in a backup.** The document said that config files and `.env` are never
     restored, and the backup took only the files the caller named (`leagues.txt`, `league_sports.json`,
     the active `sofascore.toml`), so the settings saved in the web app were lost with a restore. Since
     FX-22 `config/overrides.json` goes into `all`, `state` and `config` archives (0600) and comes back with
     the restore, under the Settings file lock, with a reload and a rollback to the previous file when it
     cannot be loaded; the other config files and `.env` are still never restored. Sections 9.1, 9.2 (FX-22
     #165).
138. **The package name.** `src/` is `sofascore_scraper/` since REN-1; the paths of this document follow
     (header). The Store's modules, its public names and the boundary rules are unchanged; the boundary
     tables of `tests/test_store_boundary.py` (`FS_ALLOWLIST`, `NAMED_EXCEPTIONS`) are keyed by the new
     paths (REN-1 #168).

Corrections after the end-to-end test and FX-23 to FX-25 (2026-10-08, the eighth revision; checked at
`48e4c4c`):

139. **An export runs while a download runs.** The lease table had no `export`; an export job took
     `writer` and was refused during a download. Since FX-23 an export job holds the lease `export`
     (exclusive among exports, shared on `maintenance.lock`), runs next to `writer` in a job store of its
     own, and makes clear, restore, rebuild and a data-folder change wait; `reap_stale` keeps export rows
     alive while the lease is held. Section 6.1 (FX-23 #171).
140. **A backup records its own job.** The archive's `state.db` was copied while the backup job ran, so a
     restore marked that job interrupted. The web and scheduler backup jobs pass their id and the copy holds
     the job as `completed`; a restore of an older archive keeps the finished live record of such a job.
     Sections 9.1, 9.2 (FX-23 #171).
141. **Store messages in English.** Section 7.3 said that the texts of `StoreError` and its subclasses were
     partly Turkish. They are English since FX-25, except six internal `ValueError`s; the issue texts of
     `verify` and the scans are still Turkish. Section 7.3 (FX-25 #172).
142. **The lock file of a settings file.** `file_lock` left `<path>.lock` next to `overrides.json` and the
     other settings files. It removes it after use, with an inode check for a waiter; a backup never took
     it. Sections 2.1, 9.1 (FX-25 #172).
143. **Derived values at version 6.** `DERIVE_VERSION` is 6: the football `after_extra_time` of a match that
     went straight to penalties is null (`04-schema-v1.md`), and a catalog written at version 5 is
     re-derived from the files on its first open; export files written before are not rewritten. Section
     7.2 (FX-23 #171).

Corrections after the live validation and FX-26, FX-27 and FX-16 (2026-10-08, the ninth revision; checked
at `43ecdfc`):

144. **"No data" of an unfinished event has a row.** Sections 2.3 and 8.1 said that an unfinished event is
     stored with the slices that came back with a body, so a 404 left no row and the slice was reported
     `not_requested`. Since FX-27 the pipeline passes it to `put` uncounted (state `empty`, counter 0); it
     stays missing for the planner and resolves completeness only once counted. Sections 2.3, 8.1 (FX-27
     #175).
145. **`FETCH_ONLY_FINISHED` is not redundant for downloads.** Section 8.1 called its use in
     `QueryService.detail_candidates` redundant for planning; a league download takes its matches from
     there, so the setting decides which of them get details. Section 8.1 (FX-26 #174).
146. **The frozen writer and `put` differ for slices that do not count.** `tests/legacy_writer.py` writes
     markers only for slices that count in the match's sport, `put` for every requested slice; harmless,
     found by FX-16. Section 2.3 (FX-16 #176).
147. **`DERIVE_VERSION` stays 6.** FX-16 changed slice rows and a `Source` text, no derived value; the next
     bump is still 7. Section 7.2 (FX-16 #176).

---

## 13. Risks

- Reader moves may not be byte-identical. Today's walkers disagree with each other (1.2). The goldens will
  surface this, and each difference needs a decision, which can stall RD-2 to RD-5.
- Two layouts at once. Until `migrate` has run, a promoted event exists twice. The v3-over-legacy precedence
  must be identical in `put`, reconcile and rebuild; a mistake shows old data or hides slices. Mitigation:
  rebuild-equivalence and promotion tests in ST-20.
- Manifest and catalog drift. The manifest is the authority and the catalog mirrors it; a bug in either
  direction gives wrong "what is missing" answers and therefore wrong request volume. Mitigation: shadow check
  in the whole test suite, the verify command, intent markers.
- File count and block overhead. Per-slice files cost about 35 KB per event on 4 KiB blocks, roughly half of
  it rounding. If per-player or per-team slices are added, files per event could grow from about 7 to 50 or
  more.
- Measurements cover 423 events of three sports on one machine (btrfs data, tmpfs scratch, zlib-ng). Rebuild
  time on a cold cache, fsync cost, stock-zlib speed and all Windows/macOS timings are estimates.
- Windows: `os.replace` fails while a reader has the file open; shared locks are emulated with byte ranges;
  recreating `catalog.db` needs exclusive access. The design retries and documents this. ST-03 runs on the
  Windows CI job: the retry works for readers that read and close, and does not for a reader that holds the
  file open in a tight loop (4.4). The lease tests of ST-10 pass on the Windows and macOS runners.
- Case-insensitive file systems. Two subs that differ only by case would share a file on Windows and on
  default macOS volumes; subs are lower-case only since FX-4 (decision S13). A legacy round file with an
  upper-case slug was ignored by the reader until FX-5; since #77 its name is folded on reading, and two
  files that fold to one sub are one page (5.1). Settled.
- File modes. Store-layer files follow the umask since FX-4 (decision S12), and new lock files since FX-8
  (#70; decision S15). The two SQLite files did not: `state.db` and `catalog.db` were created 0644 under
  umask 002, so a second account of the group could read the data and take a lease but failed on every
  database write, and since ST-11 every open writes the catalog. Since FX-11 (#85) they follow the umask
  too (4.4). What remains: lock files created before FX-8 and database files created before FX-11 keep
  0644 until they are changed by hand (the two repair lines of 4.4), and leftover `-wal` / `-shm` files of
  a second account's failed attempt block writers until they are made group-writable or removed.
- Signatures are coarse (3.5). A file edited in place inside a legacy event directory is not seen by the
  quick reconcile on open; only the deep forms find it. Since ST-11 the reconcile on open depends on this:
  the CLI test harness edits `observation.json` in place, and the catalog then keeps the old `observed_at`.
  It becomes a wrong answer as soon as `refresh_due` reads the catalog (RD-3). Since RD-3 (#97) it reads
  the catalog; the harness's copies always get a full pass on their first open (3.5), and the Store's own
  writers update the catalog inside the write, so the risk is left to hand edits and 2.x processes.
- Cost of the open. The reconcile on open is linear in legacy event directories: 15 ms on the owner's data,
  about 2.6 s for every Store open at 100,000 directories (extrapolated), and the first open builds the
  catalog (about 2 minutes at that size). Every process that opens the Store pays it at each open. The
  plan keeps the full pass for now (open decision S17); ST-19 bounds it. S17 is settled and built (3.5): an
  open within a minute of a full pass skips it (21.8 ms against 5.9 ms on a copy of the owner's data), at
  the price of hiding hand edits to match folders for up to that minute. The first open, the build, and
  `EventStore.delete` stay linear in legacy event directories; nothing was measured at 100,000.
- Writers without a hook. The catalog is current only while every writer of the legacy trees is followed by
  a hook. The suite's check fails for a product write that no hook follows, but the terminal UI's clear and
  restore are run by no test and have no hook (3.5; ST-19). Since #86 they and the data-directory move have
  the hook `shadow_cleared` and tests; the menu still takes no lease (ST-21). Readers through the Store do
  not see files changed without a hook until the next open (3.5). Since ST-21 and ST-22 the writers go
  through the Store, and P26 (#131) removed the menu, so no product code writes the indexed trees outside
  the Store at `b3cb819` and no hook has a caller; the risk is left to 2.x processes and hand edits.
  FX-15 (#155) removed the hooks; the suite's shadow check still reports a product write under a legacy
  root from outside the Store (3.5).
- Two names for one tournament. With a config file, `FollowStore.leagues()` gives the config name of a
  tournament that is in both sources, while the directories on disk carry the `leagues.txt` name (2.3). A
  reader that resolves legacy file names from the follows would miss them. The catalog does so since ST-11:
  a season-list file named after the league alone is not indexed when the two names differ (row 74 of the
  plan's section 15, open; RD-5). RD-5 (#79) fixed it on the reader side; the catalog still reports such a
  file as `unresolved_tournament` until `Store._league_names` carries the `leagues.txt` name (5.1).
- A lost change row (6.2). A write that dies between the new event payload and the change-log line loses
  the row for good. Since P23 `ssc watch` writes through `observe`, so this is reachable; FX-12 changes the
  order before ST-21 makes the downloads use `put`. Settled by FX-12 (#100): the row is kept in an intent
  file and recovered by the next write of the event or the next reconcile. A row recovered at open is not
  announced on the `change` stream; consumers that need every change read the change log (8.5).
- Closing a Store under a running thread (3.2). `Store.close()` neither waits for a finishing job of
  `store.jobs` nor for a reader in another thread; on Python 3.10 the second can abort the process. Nothing
  in the web process closes a Store; FX-12 adds the wait for the job. Since FX-12 (#100) it waits for a
  finishing job (at most 30 s); a job in the middle of its run and a reader in another thread are still
  outside the contract.
- Deleting a data directory on Windows (6.4). The registry keeps a Store open per directory for the life
  of the process, so a directory can be deleted or moved only after `Store.close()` (#120); the settings
  routes' data-directory change does not close it yet. Since FX-13 (#152) the v1 route closes it unless
  this process holds one of the old directory's leases; the legacy route does not (P30), and neither was
  tried on a Windows machine.
- A delete that removes legacy copies (section 0). `EventStore.delete` contradicts decision 4 as written;
  decision S18 is open. Nothing calls `delete` yet. Settled on 2026-10-03 (S18): decision 4 governs writes
  and maintenance, and a delete or a clear that the user asks for removes every form of what it names and
  says so in its help text. Nothing calls `delete` at `b3cb819`.
- Network file systems. SQLite WAL and `flock` do not work reliably on NFS/SMB. A user who mounts `DATA_DIR`
  from a NAS gets single-process mode at best.
- A 2.x process and a 3.x process on the same directory. 2.x does not take the lease and does not see v3 data;
  after ST-21 a downgrade shows only the data that was never rewritten. The terminal UI has the same blind
  spot until it is removed: its list, statistics, backup, restore and clear menus only see the legacy trees.
  As built since ST-21 and ST-22 every new match, schedule page and season list is in `v3/` only, so a 2.x
  process or a program that reads `match_details/` directly does not see them; ST-21 made the menu's clear
  cover both layouts and printed notices on its backup, restore and disk sizes, and P26 (#131) removed the
  menu. A 2.x process that appends to `score_changes.jsonl` after `migrate` copied it is not seen until the
  next `migrate` (5.4).
- The catalog write lock is the global write mutex. An in-place rebuild holds it for the whole rebuild;
  anything that writes without the maintenance exclusion would time out after 5 s. A slow disk during a normal
  write delays every other writer.
- Change-log sequence numbers for legacy rows are line numbers. If anything appends to `score_changes.jsonl`
  after 3.x has written its own segments, sequence numbers collide.
- Catalog size grows with slices: about 0.8 KB per event at 7 slices. Many more slice kinds per event increase
  `event_slices` proportionally (54 bytes per row measured).
- Default durability is "no fsync, repair on reconcile". After a power cut some recently written payloads are
  re-fetched. On a server with the rate limit removed this could be thousands of requests.
- `state.db` is written by up to three processes (a job, the live service, the server). Its writes are small,
  but `synchronous = FULL` makes each transaction cost an `fsync`; high-volume writers must batch.
- Growth of the odds history. Odds snapshots are kept until a `prune-history` task removes them, and no
  task exists by default (FX-15). With odds selected, every sync inside the pre-match window can add a
  snapshot per provider and slice. `prune` handles event history only, so `season_odds` history grows
  without a bound; it is off by default and experimental (P28).
- A purge leaves directories. `Store.purge` removes the event directories but not the empty bucket
  directories above them under `v3/events/`, and it keeps the team and player directories, which matches
  of other tournaments share (9.3). They cost inodes, not data.
