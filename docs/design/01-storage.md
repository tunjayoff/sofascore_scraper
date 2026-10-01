# 01 — Storage layer and catalog

Status: design, reconciled with `02-services.md` on 2026-10-01. Scope: everything under `DATA_DIR`.
Baseline: `origin/main` at `3ae2599`. Every `file:line` below refers to that commit.
Measurements were taken on the owner's real data directory (read-only). The scripts and their raw output are
not part of the repository; section 11 lists what was measured and how, and which numbers were measured a
second time during reconciliation.

The PR list that implements this document is in `03-implementation-plan.md`. Section 12 lists what changed in
this document during reconciliation and why.

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

---

## 1. Inventory: what is on disk today and who touches it

### 1.1 Layout under `DATA_DIR` today

`DATA_DIR` comes from the environment, default `data` (`src/config_manager.py:288-295`).

| Path under `DATA_DIR` | Content | Written by | Read by |
|---|---|---|---|
| `seasons/<lid>_<name>_seasons.json` | `{"seasons": [...]}`, pretty JSON | `src/season_fetcher.py:357-370` (name from `src/paths.py:60-61`) | `src/season_fetcher.py:289-316` (scan at start), `:423-463` (three alternate file names at `:441-445`); `src/web/routes/common.py:26-38` (bare `<lid>_seasons.json` first, otherwise the newest `<lid>_*_seasons.json` by mtime); `src/web/routes/leagues.py:173-187`; `src/services/stats.py:89`, `:127-130`; `src/SofaScoreUi.py:109-113` |
| `league_seasons.csv` | pre-JSON season list | nobody on main | `src/season_fetcher.py:291` |
| `matches/<lid>_<name>/<sid>_<name>/round_<n>[_<slug>].json` | round response plus a `_complete` key; **not** filtered by status | `src/match_fetcher.py:452-471` | `src/match_fetcher.py:403-425` (cache with a 6 h TTL on file mtime, `:393`); `src/season_fetcher.py:177-183`; `src/ui/match_ui.py:441-450` |
| `matches/.../events_<last\|next>_<page>.json` | de-duplicated page, filtered to finished events when `FETCH_ONLY_FINISHED` | `src/match_fetcher.py:362-378` | same as above |
| `matches/<lid>_<name>/<sid>_<name>_summary.json` and `.csv` | derived per-season list. Ten columns (`src/match_fetcher.py:547-548`); scores are `homeScore.current` with default 0 (`:570-571`); `match_date` is naive local time (`:572`); `tournament` is `tournament.name` (`:574`) | `src/match_fetcher.py:514-595` (paths from `src/paths.py:72-77`) | `src/match_data_fetcher.py:1855-1884`, `:1953-2007`, `:1822-1852`; `src/web/routes/matches.py:165-216`, `:284-303`, `:316-337`; `src/services/stats.py:73-77`, `:93-95`, `:131`. Old `*_matches.csv` is still accepted (`src/match_data_fetcher.py:1871-1875`) |
| `match_details/<lid>_<name>/season_<name>/<eid>/<slice>.json` | one pretty JSON per slice; `basic.json` is `/event/{id}` | `src/match_data_fetcher.py:1215-1225` (directory computed at `:1151-1192`); refresh rewrites `basic.json` at `:888` | `src/match_data_fetcher.py:124-181`, `:534-557`, `:1535-1730`; `src/web/routes/matches.py:219-242`, `:340-347`, `:361-393`; `src/services/stats.py:80-81`, `:132`; `src/web/league_sports.py:77-89` |
| `.../<eid>/observation.json` | `{observed_at_utc, change_ts[, status_regressed]}` | `src/match_data_fetcher.py:897`, record built at `src/status.py:260-266` | `src/match_data_fetcher.py:551-554`, used by `src/refresh.py:63-81` |
| `.../<eid>/_unavailable.json` | `{slice: count}` | `src/match_data_fetcher.py:710-711`, `:788-793` | `:639-645`, `:804-811` |
| `.../<eid>/_slice_status.json` | `{slice: {empty: {count, at}, error: {reason, status, at, count}}}` | `src/match_data_fetcher.py:712-717`, `:794-799` | `:647-656` |
| `match_details/_no_tournament/<sport>/<eid>/` | events without `uniqueTournament.id` | `src/match_data_fetcher.py:58`, `:1165-1172` | the same walkers |
| `match_details/<eid>/` (flat) and `<eid>/<eid>.json` (one combined file) | two older forms, still read | combined file is still *updated* by refresh at `:889-894` | `src/match_data_fetcher.py:154-157`, `:170-171`, `:538-542`; `src/web/routes/matches.py:375-378` |
| `match_details/processed/all_matches_<ts>.csv`, `<league>_<ts>.csv`, `match_files_stats.json`, `match_files_report.csv` | derived exports and reports | `src/match_data_fetcher.py:518` (directory), `:1535-1820`, `:2323-2482` | `src/web/routes/data.py:185-221`, `src/web/routes/matches.py:134-150` |
| `score_changes.jsonl` | one line per post-finish change | `src/match_data_fetcher.py:911-915` (name at `src/refresh.py:21`) | no reader in `src/` |
| `watch_events.jsonl`, `watch_state_<sport>.json` | watcher event stream and last known state | `src/watcher.py:45-46`, `:177-179`, `:212-222` | `src/watcher.py:204-210` |
| `.meta/jobs.db` | SQLite job history, rollback journal mode, `user_version` 0 (checked on the local file) | `src/web/jobs.py:18-21`, `:100-127`, `:193-223`, `:322-362` | `src/web/jobs.py:388-402` |
| `backups/backup_<scope>_<ts>.zip` | zip of `seasons/`, `matches/`, `match_details/` and optionally the league config and `.env`; does **not** contain `.meta`, `score_changes.jsonl` or watcher files | `src/web/routes/data.py:103-149` | `src/web/routes/data.py:251-260` |
| `datasets/`, `reports/<kind>_report_<ts>.json` | terminal-UI leftovers | `src/SofaScoreUi.py:79-83`, `src/ui/stats_ui.py:157-161` | `src/services/stats.py:139` (size only) |

Also touching `DATA_DIR`:

- Clear: `src/web/routes/data.py:152-182` removes the three data trees with `shutil.rmtree`; it leaves `.meta`,
  `score_changes.jsonl`, watcher files and `backups/`.
- `DATA_DIR` change: `src/web/routes/settings.py:134-167` re-points the job database (`src/web/jobs.py:170-191`);
  files are not moved.
- Terminal UI: backup, restore, clear and "move data directory" with `shutil` (`src/ui/settings_ui.py:297-457`,
  `:459-549`, `:551-669`); match listing from `matches/` (`src/ui/match_ui.py:347-450`).
- `scripts/migrate_match_details.py:33-73` renames id-less league directories in place.
- `src/doctor.py` probes that the directory is writable.
- The web backend builds the terminal-UI object to reach the fetchers (`src/web/fetch_job.py:19`, `:109`;
  `src/web/routes/data.py:193-197`; `src/web/routes/leagues.py:90-93`).
- Research scripts write `data/research_index` through a hard-coded path, not `DATA_DIR`
  (`scripts/discover_status_taxonomy.py:38`). The local directory also holds `finish_lag/`. The Store ignores
  top-level directories it does not know.

Related state outside `DATA_DIR` (not moved by this design, listed for completeness): `config/leagues.txt`
(`src/config_manager.py:471-554`), `config/league_sports.json` (`src/web/league_sports.py:37-74`), the
request-budget files in `~/.cache/sofascore_scraper/throttle` (`src/throttle.py:103-107`), the browser profile
(`src/paths.py:28-35`).

### 1.2 The tree walkers and where their rules differ

Each of these walks the tree with its own rule. The catalog replaces all of them.

| Walker | Rule it applies |
|---|---|
| `MatchDataFetcher._find_match_path` (`src/match_data_fetcher.py:124-159`) | any `<league>/<season>/<id>/basic.json`, then flat `<id>/basic.json` |
| `_build_match_index` (`:161-181`) | same, plus treats a first-level directory containing `basic.json` as a flat event |
| `reset_unavailable_markers` (`:719-763`), `refresh_due_ids` (`:917-935`) | league filter by directory-name prefix `<lid>_` |
| `create_csv_dataset` (`:1535-1730`) | three separate scans, both structures |
| `collect_detail_match_ids` + `_season_summary_files` (`:1855-1884`, `:1953-2007`) | summary CSVs, first league directory whose name starts with `<lid>_` |
| `generate_file_report` (`:2347-2372`) | only `season_*` directories |
| `routes/matches._detail_match_ids` (`src/web/routes/matches.py:219-242`) | three levels, league prefix |
| `routes/matches._get_missing_details_sync` (`:340-347`) | recursive `**/basic.json` over **all** leagues, parent directory name parsed as the id |
| `services/stats._detail_basics` and `system_stats` (`src/services/stats.py:80-81`, `:132`) | only `season_*`: events under `_no_tournament/<sport>/` and flat events are not counted. Match counts are **sums of CSV rows** (`:95`, `:131`) and season counts are sums over every `*_seasons.json` file (`:128-130`), so an event listed in two summary files, or a league with two season-list files, is counted twice |
| `league_sports.infer_from_data` (`src/web/league_sports.py:77-89`) | first `basic.json` under `<lid>_*/*/*` |
| season list readers | `SeasonFetcher` uses the configured league name with three fallbacks (`src/season_fetcher.py:437-451`); the web routes take the bare `<lid>_seasons.json` when it exists and otherwise the newest `<lid>_*_seasons.json` (`src/web/routes/common.py:31-38`). The two can pick different files |

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
(`src/match_data_fetcher.py:1183-1191`), while `matches/` uses `<sid>_<name>`. The season id of a stored event is
only in its payload.

---

## 2. The `Store` module (`src/store/`)

### 2.1 Rule

`src/store/` is the only code that opens, lists, creates, renames or deletes anything under `DATA_DIR`, and
the only code that opens `catalog.db` or `state.db`. Everything else imports from `src.store` (the package
root) and nothing from its submodules.

The Store contains no network code and no policy. It does not know which slices a sport needs, how long the
refresh window is, or what "finished" means for a job. Callers pass those in as arguments. It may import
`src.sports`, `src.status`, `src.slices`, `src.exceptions` and `src.version`, nothing else from `src`.

Services do not wrap the Store in a second interface. `02-services.md` section 2.5 maps what the services need
onto the API below; the API-surface test (2.4) pins it.

### 2.2 Package layout

```
src/store/
  __init__.py     public names only (see 2.3)
  api.py          Store facade, open_store(), per-data-dir registry, clear, info
  errors.py       StoreError and subclasses
  codec.py        canonical JSON bytes, gzip read/write, sha256
  files.py        atomic write/replace/remove, staging dir, retry on Windows (absorbs src/fsutil.py)
  layout.py       v3 path functions (pure)
  manifest.py     manifest.json dataclasses, read, write, validate
  legacy.py       read-only discovery and readers for every legacy form
  catalog.py      catalog.db connections, DDL, upserts, queries
  state.py        state.db connections, migration runner, runtime key/value
  derive.py       payload -> catalog row (pure)
  indexer.py      build, rebuild, reconcile
  verify.py       consistency checks
  events.py  entities.py  history.py  changes.py  streams.py  follows.py  jobs.py  watch.py
  lease.py        OS-lock based leases
  migrate.py      legacy -> v3
  export.py  backup.py
  schema/catalog.sql
  migrations/state/0001_initial.sql ...
```

The adapters that reproduce today's response shapes (summary-row dictionaries, the `basic` key) are not part of
the Store. They live in `src/services/` next to the readers that need them and are deleted with the legacy
`/api` routes.

### 2.3 Public API

All timestamps crossing the API are timezone-aware `datetime` in UTC or epoch seconds as `float`; the catalog
stores epoch seconds as `INTEGER`. All methods are synchronous and thread-safe; async callers use
`asyncio.to_thread` as the routes do today.

```python
# ---- opening --------------------------------------------------------------------------
def open_store(data_dir: str | os.PathLike | None = None, *, create: bool = True, readonly: bool = False) -> Store:
    """One Store per absolute data dir per process (cached). data_dir=None -> DATA_DIR env, default "data".
    Creates .meta/, schema.json, state.db and catalog.db when missing (create=True).
    readonly=True never writes payloads; it still opens both databases.
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
    tournament_id: int | None = None      # required for kind == "season" (the season's directory lives under it)
    # constructors: Ref.event(id), Ref.tournament(id), Ref.season(tournament_id, season_id),
    #               Ref.team(id), Ref.player(id), Ref.sport(sport_id)

# One outcome type for the whole code base, defined in src/slices.py (plan item ST-02). It is today's
# SliceOutcome (src/match_data_fetcher.py:65-89) with two more fields and one more status.
@dataclass(frozen=True)
class Outcome:
    status: Literal["ok", "empty", "failed", "skipped"]
    data: Any = None               # parsed JSON; required for "ok"; optional for "empty" (an empty 200 body)
    reason: str | None = None      # empty: "404" | "empty"; failed: "403" | "429" | "5xx" | "timeout" | "network" | "parse" | "other"
                                   # skipped: "breaker" | "not_selected" | "not_applicable" | "not_due" | "cancelled"
    http_status: int | None = None
    fetched_at: datetime | None = None    # default: now
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
    followed: bool = False          # restrict to enabled follows (joins state.db)

@dataclass(frozen=True)
class EventQuery:
    scope: Scope = Scope()
    status_classes: Sequence[str] = ()      # empty = all; "finished only" is ("completed", "decided_without_play")
    start_from: float | None = None; start_to: float | None = None
    round: int | None = None
    text: str | None = None                 # participant name, accent- and case-insensitive
    has_details: bool | None = None         # has an /event/{id} payload
    updated_after: float | None = None
    sort: Literal["start_desc", "start_asc"] = "start_desc"
    limit: int = 50
    cursor: str | None = None               # opaque keyset cursor (start_ts, id)
    offset: int | None = None               # only for the old /api/matches paging
```

**Events and their slices**

```python
class EventStore:
    # reads -------------------------------------------------------------------------------
    def get(self, event_id: int) -> EventRow | None
    def list(self, q: EventQuery, *, with_total: bool = False) -> Page[EventRow]
    def count(self, q: EventQuery) -> int
    def iter(self, q: EventQuery, *, batch: int = 1000) -> Iterator[EventRow]      # keyset, for exports
    def payload(self, event_id: int, key: str = "event", sub: str = "", *, raw: bool = False) -> Any | bytes | None
        # raw=True returns the stored JSON bytes after decompression, without parsing. None when there is no payload file.
    def payloads(self, event_id: int, keys: Iterable[str] | None = None) -> dict[str, Any]
    def slices(self, event_id: int) -> list[SliceInfo]
    def slice(self, event_id: int, key: str, sub: str = "") -> SliceInfo            # state "not_requested" when unknown
    def states(self, scope: Scope, *, status_classes: Sequence[str] = (), batch: int = 1000) -> Iterator[EventState]
        # For planning: one EventRow plus its SliceInfo rows per event, read with one indexed range query per batch.
    def missing(self, scope: Scope, required: Mapping[str, Sequence[str]], *,
                status_classes: Sequence[str] = ("completed", "decided_without_play"),
                threshold: int = 2, limit: int | None = None) -> Iterator[MissingRow]
        # required: sport slug -> slice keys ("" = any sport). Yields (event_id, sport, has_event_payload, missing_keys).
    def refresh_candidates(self, *, now: float, window_s: float, min_interval_s: float,
                           scope: Scope | None = None, status_classes: Sequence[str] = (),
                           include_unobserved: bool = False) -> list[int]
    def stale(self, scope: Scope | None = None) -> list[int]       # rows a newer listing disagrees with (8.2)
    def open_events(self, *, started_before: float, scope: Scope | None = None) -> list[int]
    def summary(self, scope: Scope) -> list[TournamentSummary]     # counts for dashboards
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
    def reset_empty_markers(self, scope: Scope | None = None, *, include_confirmed: bool = False) -> dict[str, int]
        # same result keys as today: {"matches", "slices", "scanned"} (src/match_data_fetcher.py:719-763)
    def delete(self, event_id: int) -> bool

@dataclass(frozen=True)
class PutResult:
    created: bool                       # the event did not exist before
    event_written: bool                 # the "event" payload changed on disk
    superseded: bool                    # an "event" outcome was ignored because a newer observation is stored
    written: tuple[str, ...]            # slice keys whose payload file changed
    change_seq: int | None              # sequence number of the change-log row, if one was written
    promoted: bool                      # the event was copied from the legacy layout by this call
```

Semantics of `put` (the only way event payloads reach the disk):

1. Keys are validated: `key` matches `[a-z][a-z0-9_]{0,39}`, `sub` matches `[A-Za-z0-9_.-]{0,80}`. An
   `"event"` outcome must be `ok` and its payload's `id` must equal `event_id`.
2. An event is created by the first `put` that carries an `"event"` outcome or by a listing (see
   `EntityStore.put`). A `put` without `"event"` for an id the catalog does not know raises `UnknownEvent`.
3. Per outcome:
   - `ok`: canonical bytes are computed (4.1). If their sha256 equals the manifest's, no file is written and
     only `fetched_at`/`checked_at` move. Otherwise the file is replaced atomically. Empty and error marks of
     that slice are cleared (today: `src/match_data_fetcher.py:686-689`).
   - `empty` with data (an empty 200 body): the payload is stored, as today (`:1223-1225`), state `empty`.
   - `empty` without data (404): no file is written and an existing payload is never removed.
   - For both `empty` forms, `empty_count` is incremented only when `count_empties` covers the key, and a
     previous error is cleared (`:704-708`).
   - `failed`: stored as state `error` with `{reason, status, at, count + 1}`; `empty_count` is not touched
     (`:696-703`). An error never downgrades a slice that is `ok`.
   - `skipped`: ignored, nothing is stored. This covers the open circuit breaker, where no request was sent
     (`:693-694`).
4. An `"event"` outcome whose `fetched_at` is older than the stored observation is ignored
   (`PutResult.superseded`). Two writers (a job and the live service) can therefore never replace a newer
   event payload with an older one.
5. `on_event_change(old_payload, new_payload)` is called inside the critical section, only when the `"event"`
   payload is about to change, with the stored payload (or `None`). It returns one change-log row or `None`.
   The row is appended to the change log in the same critical section. This keeps the comparison atomic with
   the write while the comparison rule (`diff_basic`, `change_row`, `src/refresh.py:95-140`) stays outside the
   Store. `status_regressed=True` is sticky (`src/match_data_fetcher.py:878-885`).
6. For a key listed in `keep_history`, the payload is also appended to the slice's history file when its
   content hash differs from the last stored one.
7. If the event lives in the legacy layout it is promoted first (5.3).
8. The whole call runs under the write protocol of 6.2 and is atomic with respect to the catalog.

Callers keep today's marker rules by what they pass. Today slice markers are updated only for events that are
finished (`src/match_data_fetcher.py:1228-1229`) and only for `required` slices (`:684`); a caller reproduces
that with `count_empties=False` (or the set of required keys) and by leaving failed outcomes of an unfinished
event out of the call.

Slice state as stored and as reported:

| Reported state | Stored condition |
|---|---|
| `ok` | a payload file exists and the caller said it carries data |
| `error` | last attempt failed and the slice is not `ok` |
| `empty` | last answer was definitive "no data" (404 or an empty 200) |
| `not_requested` | no row |

A slice is still **expected** when `state != 'ok'` and `empty_count + unverified_empty_count < threshold`
(threshold 2 today, `src/match_data_fetcher.py:49`, `:804-811`). This replaces `_unavailable.json` and
`_slice_status.json`. Mapping from the legacy files:

- `<key>.json` present: `ok` when the presence predicate says it has data, else `empty` with a payload. The
  predicates are today's `match_detail_slice_present` family (`src/match_data_fetcher.py:559-637`); they move to
  the pure module `src/slices.py` first (plan item ST-02) so the legacy reader can call them.
- `_unavailable.json[key] = c` and `_slice_status.json[key].empty.count = k`: `empty_count = min(c, k)`,
  `unverified_empty_count = c - min(c, k)`. This is exactly the split `--recheck-unavailable` makes (`:772`).
- `_slice_status.json[key].error`: copied to the error fields.

**Non-match entities**

```python
class EntityStore:
    def put(self, ref: Ref, outcomes: Mapping[str | tuple[str, str], Outcome], *,
            index_listed_events: bool = True, count_empties: bool | Collection[str] = True,
            keep_history: Collection[str] = ()) -> PutResult
        # Same rules as EventStore.put. When a payload has an "events" array (round pages, events/last, events/next,
        # team events) and index_listed_events is true, each listed event is upserted as a catalog row (8.2).
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

The `/event/{id}` payload is stored under the key `event`. The name `basic` survives only in the legacy file
name `basic.json` and in the response shape of the legacy `GET /api/matches/{id}` route.

**Odds snapshots and odds changes**

Odds are ordinary slices of the event (`odds_all`, `odds_featured`, `odds_changes`, `winning_odds`, with the
provider id as `sub`). The latest payload is the slice file. In addition, a key written with `keep_history`
appends the payload to the slice's history file whenever its content hash differs from the last stored one.
SofaScore's own change list for the main market (`/event/{id}/odds/{provider}/changes`) is the `odds_changes`
slice.

```python
class HistoryStore:
    def index(self, ref: Ref, key: str, sub: str = "") -> list[SnapshotInfo]          # n, fetched_at, sha256
    def snapshots(self, ref: Ref, key: str, sub: str = "", *, since: float | None = None,
                  raw: bool = False) -> Iterator[Snapshot]                             # n, fetched_at, payload
    def snapshot(self, ref: Ref, key: str, sub: str, n: int, *, raw: bool = False) -> Snapshot | None
    def prune(self, ref: Ref | None = None, *, older_than: float) -> int              # rewrites history files; needs "writer"
```

The catalog does not shred odds payloads into market/choice rows. Normalising a payload (odds, statistics,
lineups, standings) is the job of the schema layer (`src/schema/`, plan item SC-1), which reads payloads through
the Store on request or during export. Reason: those tables would multiply the catalog size and its rebuild
time, and every change of the normalised schema would force a rebuild.

**Change log**

```python
class ChangeLog:
    def append(self, row: Mapping[str, Any]) -> int        # returns seq; normally reached through put(on_event_change=...)
    def list(self, *, after_seq: int = 0, event_id: int | None = None, since: float | None = None,
             limit: int = 1000) -> list[ChangeRow]
    def last_seq(self) -> int
```

**Event streams**

One durable log carries every stream that consumers follow by sequence number: `live` (live service),
`change` (a notification for each change-log row), `job` (job started/finished), `system` (blocked, recovered,
live source switched, sink dropped).

```python
class StreamLog:
    def append(self, stream: str, events: Sequence[StreamEvent]) -> list[int | None]
        # One transaction; returns the seq numbers. An event whose dedup_key already exists in the stream is not
        # stored again and yields None.
    def read(self, *, after: int = 0, limit: int = 500, streams: Sequence[str] = (), types: Sequence[str] = (),
             event_ids: Sequence[int] = (), sport: str | None = None, tournament_ids: Sequence[int] = ()) -> StreamBatch
        # StreamBatch(stream_id, events, last_seq, gap). gap=True: rows after `after` were already pruned.
    def wait(self, *, after: int, timeout: float) -> bool   # true as soon as a row with seq > after exists
    def head(self) -> StreamHead                            # stream_id, first_seq, last_seq
    def prune(self, *, max_age_s: float | None = None, max_rows: int | None = None) -> int
    def cursor(self, sink: str) -> int                      # last delivered seq of a sink (0 = none)
    def set_cursor(self, sink: str, seq: int, *, error: str | None = None) -> None

class WatchStateStore:      # replaces watch_state_{sport}.json (src/watcher.py:204-214)
    def load(self, watcher: str) -> dict[str, dict[str, Any]]
    def save(self, watcher: str, state: Mapping[str, Mapping[str, Any]], *, changed: Iterable[str] | None = None) -> None

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
- `stream_id` is a UUID stored in `state.db`; it changes only if the state db is recreated, which tells a
  consumer that its saved position is no longer meaningful.
- `wait` uses an in-process condition for events appended by the same process and polls `PRAGMA data_version`
  (every 200 ms) for events appended by another process; SQLite has no cross-process notification.

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
(`ConfigManager._refresh_if_changed`, `src/config_manager.py:489`), and a downgrade to 2.x finds its files
current. The uniqueness rules of today's file are kept: one row per id and one row per tournament name
(`src/config_manager.py:490-495`).

**Jobs, administration, export, backup, migrate**

```python
class JobStore:      # today's methods (src/web/jobs.py:49-402), on state.db, plus what the job manager needs
    create_running, update, snapshot, request_cancel, cancel_requested, list_jobs, get_job,
    exclusive, mark_stale_running_interrupted
    # added with the job manager (02-services.md 2.8): heartbeat, cancel by id from another process,
    # append_event / read_events (job_events table), reap_stale (row says running but the lease is free)

class CatalogAdmin:
    def rebuild(self, *, progress: Callable[[str, int, int], None] | None = None,
                should_stop: Callable[[], bool] | None = None) -> RebuildReport                     # 3.4
    def reconcile(self, *, deep: bool = False) -> ReconcileReport                                    # 3.5
    def verify(self, *, deep: bool = False, repair: bool = False) -> VerifyReport                    # 3.6

class Migrator:
    def plan(self, *, scope: Scope | None = None, exact: bool = False) -> MigrationPlan              # dry run
    def run(self, *, scope: Scope | None = None, delete_legacy: bool = False, limit: int | None = None,
            should_stop: Callable[[], bool] | None = None,
            progress: Callable[[MigrationProgress], None] | None = None) -> MigrationReport

class Exporter:
    def raw(self, q: EventQuery, dest: str, *, keys: Sequence[str] | None = None,
            fmt: Literal["tree", "jsonl"] = "tree", pretty: bool = False) -> ExportReport            # 4.5
    def rows(self, rows: Iterable[Mapping[str, Any]], columns: Sequence[str], dest: str | BinaryIO,
             fmt: Literal["jsonl", "csv", "parquet", "sqlite"], *, table: str = "rows") -> ExportReport

class BackupManager:
    def create(self, *, scope: Literal["all", "state", "data"] = "all", include_catalog: bool = False,
               config_paths: Sequence[str] = (), dest: str | None = None) -> BackupInfo
    def list(self) -> list[BackupInfo]
    def verify(self, archive: str) -> BackupReport
    def restore(self, archive: str, *, force: bool = False, dry_run: bool = False) -> RestoreReport
    def prune(self, *, keep: int | None = None, max_age_days: float | None = None) -> int
```

Errors: `StoreError(StorageError)` keeps the `fatal` property of today's `StorageError`
(`src/exceptions.py:109-137`), so callers that stop a job on a full disk keep working. Subclasses: `LeaseHeld`,
`StoreBusy`, `UnknownEvent`, `PayloadMissing`, `PayloadCorrupt`, `CatalogCorrupt`, `SchemaTooNew`, `LayoutError`,
`FollowExists`, `FollowManaged`. The mapping to error codes, exit codes and HTTP statuses is the single table in
`02-services.md` section 2.6: any `StoreError` is `storage_error` (exit code 5), `LeaseHeld` is one of the three
"another instance" codes (exit code 6).

### 2.4 Enforcing "only the Store touches `DATA_DIR`" in CI

Three tests, all in the normal `pytest` job (`.github/workflows/ci.yml:85-93`), no new CI job:

1. **Static check** (`tests/test_store_boundary.py`, uses `ast`). For every module under `src/` outside
   `src/store/` it fails on:
   - any import of a `src.store.<submodule>` (only `from src.store import ...` is allowed);
   - any call to `open`, `os.listdir`, `os.scandir`, `os.walk`, `os.remove`, `os.unlink`, `os.rename`,
     `os.replace`, `os.makedirs`, `os.mkdir`, `os.rmdir`, `os.stat`, `os.path.exists/isfile/isdir/getsize/getmtime/getctime`,
     `glob.glob/iglob`, `shutil.*`, `tempfile.*`, `sqlite3.connect`, `zipfile.ZipFile`, `pandas.read_csv`,
     `DataFrame.to_csv`, and the `pathlib.Path` methods that hit the disk.
   An allowlist names the modules that legitimately touch *other* files, each with a one-line reason:
   `src/config_manager.py` and `src/config/` (config files and `.env`), `src/paths.py`, `src/i18n.py`
   (locales), `src/doctor.py` (environment probes), `src/throttle.py` (budget files), `src/challenge_solver.py`
   (browser profile), `src/logger.py` and `src/diagnostics.py` (log files and the diagnostics bundle, PR #24),
   `src/sinks/file.py` (the file sink's own output path), `src/web/app.py` and `src/web/missing_ui.py` (static
   files).
2. **Runtime check** (`tests/conftest.py`, `sys.addaudithook`). CPython raises audit events for `open`,
   `os.listdir`, `os.scandir`, `os.remove`, `os.rename`, `os.mkdir`, `os.rmdir`, `shutil.rmtree`,
   `sqlite3.connect` and others. The hook looks only at paths inside the test `DATA_DIR`
   (`tests/conftest.py:25`), walks the call stack to the nearest frame under `src/`, and records a violation
   when that frame is not in `src/store/`. Frames in `tests/` are ignored, so tests can still seed fixtures.
   This catches paths that are built dynamically, which the static check cannot see.
3. **Layering check**: `src/store/` imports nothing from `src.web`, `src.ui`, `src.services`, `src.jobs`,
   `src.client`, the fetchers, `src.utils` or `src.challenge_solver`.

Both the static and the runtime check use a **ratchet**: `tests/store_boundary/baseline/<module>.txt`, one
file per offending source module, listing the violations that exist today as `function:call`. A new violation
fails the test. A baseline entry that no longer occurs also fails the test, so entries must be removed in the
PR that fixes them and the lists can only shrink. One file per module means two PRs that clean up different
modules do not edit the same baseline file. The last PR deletes the directory.

A fourth test (`tests/test_store_api_surface.py`) compares `src.store.__all__` and every public signature with
checked-in text files, one per public class under `tests/fixtures/store_api/`, so an API change always shows up
in review and two PRs that extend different classes do not edit the same file.

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
| every table in `catalog.db` | derived | no (optional, to skip the rebuild after a restore) |
| follows of origin `api` | `state.db` | yes |
| follows of origin `legacy` / `config` | the config files; the rows are a mirror | the files, when passed to the backup |
| jobs, job events | `state.db` | yes |
| event streams, sink cursors, watcher state | `state.db` | yes |
| runtime facts, leases | the running processes; the rows are information only | no |
| exports, legacy summaries, `processed/*.csv` | derived | no |

### 3.2 Connection settings

Applied by `catalog.py` and `state.py` on every new connection:

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
  home_score_current INTEGER,                      -- homeScore.current as given (legacy list and CSV shapes)
  away_score_current INTEGER,
  winner_code        INTEGER,
  scores_json        TEXT,                         -- normalised score sheet (src/status.extract_scores)
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

`state.db`. Migration `0001_initial` (plan item ST-09):

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

CREATE TABLE jobs (                                 -- the columns of today's .meta/jobs.db (src/web/jobs.py:105-124)
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

Migration `0002_job_manager` (plan item P11; the job model is in `02-services.md` 2.8):

```sql
ALTER TABLE jobs ADD COLUMN origin_json TEXT;       -- face (cli|api|scheduler|library), pid, host
ALTER TABLE jobs ADD COLUMN spec_json TEXT;         -- the service spec
ALTER TABLE jobs ADD COLUMN error_json TEXT;        -- {code, message, details}
ALTER TABLE jobs ADD COLUMN heartbeat_at INTEGER;
CREATE TABLE job_events (
  job_id    TEXT    NOT NULL,
  seq       INTEGER NOT NULL,
  ts_ms     INTEGER NOT NULL,
  type      TEXT    NOT NULL,
  data_json TEXT    NOT NULL,
  PRIMARY KEY (job_id, seq)
) WITHOUT ROWID;
```

Notes on the event row:

- It is produced by one pure function, `derive.event_row(payload, source, observed_at)`. This is the single
  place where a score is read out of a payload. `home_score`/`away_score`, `status_class` and `scores_json`
  come from `src.status.classify_status` and `extract_scores`.
- `home_score_current`, `away_score_current`, `stage_name` and `listed_in` exist only so that the legacy list
  routes and the legacy CSV can be reproduced from the catalog: today's summary writes `homeScore.current`
  (`src/match_fetcher.py:570-571`; in football that value includes penalties, see the docstring at
  `src/status.py:140`), `tournament.name` (`:574`) and, for seasons fetched as event pages, the page name as
  the round (`:372`). They are not part of the public contract and can be dropped (a catalog rebuild) when the
  legacy `/api` routes are removed.
- When an event has an `/event/{id}` payload, its row always equals `derive(event payload)`. A listing never
  overwrites such a row (8.2).
- `first_seen_at` and `updated_at` are taken from the manifest (`created_at`, `updated_at`) or, for a listing
  row, from the `fetched_at` of the listing payloads, so a rebuild reproduces them. Only `sig` differs.

### 3.4 Rebuild

`CatalogAdmin.rebuild()` needs the `maintenance` lease (6.1). Two modes:

- **In place** (catalog opens, schema matches): one `BEGIN IMMEDIATE` transaction deletes all rows and
  re-inserts them. Readers in other processes keep seeing the old, consistent catalog through WAL snapshot
  isolation until the commit. A failure rolls back to the old catalog. No file is swapped, so this works on
  Windows too.
- **Recreate** (catalog missing, corrupt, or from another schema version): build `catalog.db.build`, then
  `os.replace` it over `catalog.db` and remove stale `-wal`/`-shm` files. On Windows this fails while another
  process has the old file open; the error says so.

Scan order (deterministic, so two rebuilds of the same tree give the same rows):

1. `v3/tournaments/**`: tournament and season manifests, season lists, schedule pages. Listing rows are
   upserted in `fetched_at` order.
2. Legacy `seasons/*.json`, then legacy `matches/**` round and page files in mtime order. Summary CSVs are read
   only for a season directory that has no round or page JSON at all (old `_matches.csv` data).
3. `v3/teams/**`, `v3/players/**`, `v3/sports/**`.
4. `v3/events/**/manifest.json`: event row from `event.json.gz`, slice rows from the manifest, participants,
   and tournament/season rows where none exists yet.
5. Legacy `match_details/**` in all five forms (5.1). An id already found in step 4 is not indexed again; its
   legacy directory is recorded as `legacy_path` (superseded).
6. Change log: legacy `score_changes.jsonl`, then `changes/*.jsonl` in name order.
7. History files: one `slice_history` row per gzip member.
8. `stale` flags (8.2), `ANALYZE`, `meta` (`built_at`, `built_by`, `derive_version`, counts).

A file that cannot be read or parsed does not stop the rebuild. It is listed in the report and its slice row
gets `state = 'error'`, `error_reason = 'corrupt'`, which makes it a re-fetch candidate.

A rebuild runs automatically on open when the catalog is missing, its `user_version` differs, its
`derive_version` in `meta` differs from the code's, or `PRAGMA quick_check` fails after an unclean shutdown.

Measured cost (warm page cache): the local legacy tree (90 schedule files, 423 event directories, 1,051 events
in total) indexes in 0.22 s. Reading one v3 event for the rebuild (manifest + `event.json.gz`) takes 0.11 ms.
From that, 100,000 v3 events are roughly 15–30 s of CPU; with a cold cache the 200,000 file opens dominate and
the time depends on the disk. This was not measured at that scale (open question).

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
| Slices of an export batch | `event_slices WHERE event_id BETWEEN ? AND ? AND has_payload=1` | primary key range | 2.8 ms for 6,547 rows |
| One event with its slices | `event_slices WHERE event_id=?` | primary key | 0.01 ms |
| Migration work list | `WHERE layout='legacy' ORDER BY id` | `events_legacy` | 0.15 ms |
| Changed since (consumer sync) | `WHERE updated_at > ? ORDER BY updated_at, id` | `events_updated`, covering | — |
| Events that should have started | `WHERE status_class IN ('not_started','live','unknown') AND start_ts <= ?` | `events_open` | — |
| Stream read | `stream_events WHERE stream=? AND seq>? ORDER BY seq LIMIT 500` | `stream_events_stream` | — |

The "what is missing" query, as `EventStore.missing` builds it (the `VALUES` list comes from the caller's
`required` mapping):

```sql
WITH req(sport, key) AS (VALUES ('football','statistics'), ('football','lineups'), ...)
SELECT e.id, e.sport, e.has_event_payload, group_concat(r.key)
FROM events e
JOIN req r ON r.sport = e.sport OR r.sport = ''
LEFT JOIN event_slices s ON s.event_id = e.id AND s.key = r.key AND s.sub = ''
WHERE e.tournament_id = :t AND e.season_id = :s
  AND e.status_class IN ('completed', 'decided_without_play')
  AND (e.has_event_payload = 0
       OR s.event_id IS NULL
       OR (s.state != 'ok' AND s.empty_count + s.unverified_empty_count < :threshold))
GROUP BY e.id;
```

Two things the tests must pin, because the planner cannot infer them: a query that wants the partial index
`event_slices_not_ok` must contain the literal `state != 'ok'`, and a refresh query must repeat the two
conditions of `events_unsettled`. `tests/test_store_query_plans.py` asserts the index name in each plan.

Catalog size at that scale: 230 MB for 301,000 events and 2.1 million slice rows, about 0.8 KB per event
(`events` 54 MB, `event_slices` 114 MB, indexes the rest; the indexes and the four columns added after the
measurement add an estimated 15–20 MB). The real data gives 1.1 MB for 1,051 events. Catalog write cost:
0.09 ms per event for the intent transaction plus the data transaction (one event row and eight slice rows).

---

## 4. Payload files

### 4.1 Format

- Bytes stored: `json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")`. Key order is
  the order of the response. This is the parsed response serialised again, as today
  (`src/fsutil.py:33-36`), not the bytes on the wire: escapes and number spelling may differ, values do not.
  "Raw" everywhere in the platform (raw export, the `/raw` API routes) means these bytes.
- Compression: `gzip.compress(data, 6, mtime=0)`. `mtime=0` makes the output deterministic: the same payload
  always gives the same file, which keeps backups and `rsync` quiet. Verified.
- sha256 is taken over the uncompressed bytes and stored in the manifest.
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
  v3/
    events/<id // 1000000>/<(id // 1000) % 1000, 3 digits>/<id>/
        manifest.json
        event.json.gz
        <key>.json.gz
        <key>/<sub>.json.gz
        _history/<key>/<sub or "_">.jsonl.gz
    tournaments/<ut id>/
        manifest.json  tournament.json.gz  seasons.json.gz
        seasons/<season id>/
            manifest.json
            schedule/round_12.json.gz   schedule/last_0.json.gz
            standings/total.json.gz     ...
    teams/<id // 1000>/<id>/        manifest.json  <key>.json.gz ...
    players/<id // 1000>/<id>/      manifest.json  <key>.json.gz ...
    sports/<sport id>/              manifest.json  <key>[/<sub>].json.gz
  changes/<yyyy>-<mm>.jsonl         change log segments, uncompressed, append-only
  exports/                          outputs of export commands
  backups/                          as today
  seasons/  matches/  match_details/  score_changes.jsonl  watch_*.json*     legacy, read in place
```

Example: event 16416346 lives in `v3/events/16/416/16416346/`. The path depends only on the id, so a renamed
league or season never moves anything. No directory holds more than 1,000 event directories; the first level
grows by one directory per million ids (the local ids span 14–17 million and produce 3 first-level
directories). All names are ASCII digits and fixed words, so they are valid on Windows.

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

`observation` keeps the two fields of today's `observation.json` (`src/status.py:260-266`) and the sticky flag.
`migrated_from` is present only for an event that came from the legacy layout.

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
  (`src/fsutil.py:19-30`).
- A new entity directory that is created with several files at once (promotion, migration) is built under
  `.meta/tmp/` and moved into place with one directory rename. It appears complete or not at all.
- `manifest.json` is replaced after the payload files. A crash between the two leaves a payload that is newer
  than its manifest entry; the intent marker (6.2) makes the next open re-index that entity, and the indexer
  trusts the file (hash and size from the file, `fetched_at` from its mtime).
- Durability: by default nothing is `fsync`-ed, as today. After a power cut a file may be empty or old; the
  unclean-shutdown reconcile and `verify(deep=True)` detect it through the manifest hash and mark the slice
  for re-fetching. `STORE_DURABILITY=full` adds `fsync` of each file and its directory. The cost of that was
  not measured (the scratch file system was tmpfs).
- Windows: `os.replace` fails while another process has the target open. Store readers read a file in one
  call and close it; the writer retries a failed replace up to 10 times with 20 ms pauses, then raises a
  non-fatal `StoreError`.

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

---

## 5. Reading both layouts, and `migrate`

### 5.1 Discovery

`legacy.py` is the only module that knows the old path rules. It only reads. It recognises:

| Form | Pattern | Source of the rule |
|---|---|---|
| L1 | `match_details/<lid>_<name>/season_<name>/<eid>/` | `src/match_data_fetcher.py:1174-1192` |
| L2 | `match_details/<name>/season_<name>/<eid>/` (league directory without id) | `scripts/migrate_match_details.py:47-73`, `src/services/stats.py:66-69` |
| L3 | `match_details/<eid>/` (flat) | `src/match_data_fetcher.py:154-157`, `:170-171` |
| L4 | `<event dir>/<eid>.json` holding all slices in one object | `:538-542` |
| L5 | `match_details/_no_tournament/<sport>/<eid>/` | `:58`, `:1165-1172` |
| schedule | `matches/<lid>_<name>/<sid>_<name>/round_*.json`, `events_*.json` | `src/match_fetcher.py:367`, `:452-454` |
| summaries | `matches/<lid>_<name>/*_summary.{json,csv}`, `*_matches.csv` | `src/match_fetcher.py:514-595`, `src/match_data_fetcher.py:1871-1875` |
| season lists | `seasons/<lid>_*_seasons.json`, `<lid>_seasons.json`, `<name>_seasons.json`, `league_seasons.csv` | `src/season_fetcher.py:291`, `:441-445`, `src/web/routes/common.py:31-36` |
| change log | `score_changes.jsonl` | `src/refresh.py:21` |
| watcher | `watch_events.jsonl`, `watch_state_<sport>.json` | `src/watcher.py:45-46` |

An event directory is recognised by `basic.json` (or by L4's combined file with a `basic` key) whose `id`
equals the directory name. `match_details/processed/` is skipped, as every walker does today.

When one event id is found in more than one legacy place, the directory with the newest `basic.json` mtime
wins and the others are reported by `verify`.

When several season-list files exist for one tournament id, the newest by mtime is indexed, whatever its name.
Neither of today's readers does exactly this: the web routes prefer the bare `<lid>_seasons.json` when it
exists (`src/web/routes/common.py:31-33`) and `SeasonFetcher` prefers the file named after the configured
league (`src/season_fetcher.py:436-451`). The difference only shows when a directory holds an old-format file
next to a newer one; the reader PR that switches season lists (plan item RD-5) states it as a behaviour change.

### 5.2 Indexing legacy data without moving it

The indexer fills the same tables from legacy files as from v3 files:

- `events` row from `basic.json`, with `layout = 'legacy'` and `path` set to the directory.
- `observed_at` from `observation.json`; when that file is missing it is `NULL`, which the refresh rules treat
  as "final unless legacy refresh is asked for", exactly as today (`src/refresh.py:72-74`).
- Slice rows from the slice files and the two marker files (mapping in 2.3). `fetched_at` is the file mtime.
- Schedule files give listing rows (8.2) and `entity_slices` rows with `layout = 'legacy'` and the file path.
  The `_complete` key becomes `meta_json = {"complete": true|false}`; a page that was stored filtered (no
  `_complete` key, or an `events_*` page) gets `{"filtered": true}`.
- Legacy rows in `score_changes.jsonl` get `seq` = their line number.

Reads go through the same Store calls: `events.payload(id, key)` looks at `events.layout`; for a legacy event
it opens `<path>/<key>.json` (with `event` → `basic.json`) or takes the key from the combined file.

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
| step 1–3 | leftover staging directory | `.meta/tmp/` is emptied when the writer lease is taken; event still legacy |
| step 4 | v3 directory exists, catalog says legacy | the intent marker triggers a re-index; a rebuild also resolves to v3 + `legacy_path` |
| step 5 | both copies, catalog says v3 with `legacy_path` | nothing to repair; a later `--delete-legacy` continues with step 6 |
| during step 6 | legacy directory is in `.meta/trash/` | trash is emptied; `legacy_path` is cleared because the directory is gone |

**Schedules, season lists, change log.**

- A round or page file becomes `v3/tournaments/<lid>/seasons/<sid>/schedule/<sub>.json.gz`. The stored payload
  is the legacy object without the `_complete` key (which moves to the slice's `meta`); the verification
  applies the same transformation before comparing.
- The newest season-list file per tournament becomes `v3/tournaments/<lid>/seasons.json.gz`.
- `score_changes.jsonl` is copied to `changes/0000-legacy.jsonl` (and removed only with `--delete-legacy`).
  Line numbers, and therefore `seq` values, do not change.
- Summary files and `match_details/processed/*` are derived. They are listed in the dry run as "derived, not
  migrated" and are deleted only with `--purge-derived`. A legacy season that has summary CSVs but no round or
  page JSON cannot be converted; it stays in place, stays readable, and is reported.
- `watch_events.jsonl` and `watch_state_*.json` are not migrated: the watcher imports its state file once when
  it first runs on the Store (plan item ST-18), and the events file is history that no code reads.

**Dry run.** `Migrator.plan()` changes nothing on disk (a test compares a hash of the whole tree before and
after). It reports counts per kind, bytes before, estimated bytes after, unknown files, conflicts, and
unconvertible seasons. The size estimate compresses a sample of 200 events in memory and extrapolates;
`--exact` compresses everything (3.7 ms per event measured).

**Resumable.** `--limit N` and `should_stop` end the run between two events. Running it again continues,
because the work list is recomputed from the catalog. A finished event is never converted twice.

---

## 6. Concurrency

### 6.1 Leases

A lease is an OS file lock on `DATA_DIR/.meta/locks/<name>.lock`, held for as long as the `Lease` object
lives. The kernel releases it when the process dies, so there are no stale locks to clean up. The technique
is the one `src/throttle.py:112-129` already uses (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows).

There is one implementation, `src/store/lease.py`. The job manager, the live service and the sink dispatcher
(`02-services.md`) take their leases through `Store.lease()`; they do not create lock files of their own.

| Lease | Who takes it | Excludes |
|---|---|---|
| `writer` | download and refresh jobs, single-event fetch, `migrate`, history pruning, backup creation | another `writer`; `maintenance` |
| `watcher:<sport>` | the 2.x-style polling watcher, one per sport (`src/watcher.py:10-12`), until the live service replaces it | the same watcher name; `live`; `maintenance` |
| `live` | the live service (one per data directory, all sports) | another `live`; every `watcher:<sport>`; `maintenance`; a running `migrate` |
| `sinks` | the process that dispatches streams to webhooks and file sinks | another `sinks` |
| `maintenance` | clear, restore, catalog rebuild, state-db migrations, data-directory change | everything except `sinks` |

Implementation of the exclusion with `maintenance`: every `writer`, `watcher` and `live` holder also holds a
shared lock on `maintenance.lock`; `maintenance` takes that file exclusively and fails if anyone holds it
shared. On Windows, where `msvcrt.locking` has no shared mode, each holder locks one distinct byte of the file
(the first free one of 64) and `maintenance` locks the whole 64-byte range. `live` and `watcher:<sport>`
exclude each other the same way through `live.lock`; `migrate` holds `writer` and additionally takes
`live.lock` exclusively for its duration.

`Store.lease(name, wait=0)` raises `LeaseHeld` with the holder's pid, host, purpose and start time, read from
the `leases` table. That table is filled when a lease is taken and is only information; the OS lock decides.
The holder information is not written into the lock file itself, because a byte-range lock on Windows makes
the locked bytes unreadable for other processes.

This replaces the in-process slot of `JobStore.exclusive` (`src/web/jobs.py:150-168`), which cannot see a CLI
process writing the same directory. `JobStore.exclusive` keeps its interface and its error classes
(`JobRunningError`, `DataOperationRunningError`, turned into HTTP 409 at `src/web/app.py:59-65`) and is
implemented with these leases. A job is "running" when its row says so **and** its owner holds the `writer`
lease; this is what lets another process tell a crashed job from a live one.

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
  transaction of that write has committed, so a consumer that reacts to the event finds the data.

### 6.3 Readers

Readers take no lease and no lock.

- Catalog and state db: WAL gives each read transaction a consistent snapshot; readers never block writers
  and writers never block readers.
- Payload files: replaced atomically, read in one call.
- A reader may hold a catalog row whose file has just gone (legacy directory removed by `migrate` after the
  event moved to v3; clear). On `FileNotFoundError` the Store re-reads the event's row once and retries with
  the new location; if the file is still missing it raises `PayloadMissing`.
- A process that has `catalog.db` open while another process recreates the file (3.4, recreate mode) would
  keep reading the old, unlinked file. Each Store compares the inode and size of `catalog.db` with those of
  its open connection once per second and reopens when they differ.

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
volumes and bind mounts of local disks are fine.

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
  "state_schema": 1,
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
- A data directory without `schema.json` and without `v3/` is a pure 2.x directory: the Store creates
  `.meta/schema.json`, `state.db` and `catalog.db` and changes nothing else. Removing those three files
  returns the directory to its 2.x state.

Four version numbers, each with its own rule:

| Number | Covers | Changed by | On mismatch |
|---|---|---|---|
| `layout_version` | the v3 tree and file naming | a new on-disk layout | refuse to write / read as above; converting is an explicit command |
| `manifest_format` | fields of `manifest.json` | adding fields does not bump it; changing meaning does | readers accept older formats; a manifest is upgraded when it is next written |
| `catalog_schema`, `derive_version` | catalog DDL; what `derive.py` puts into rows (for example score sheets for new sports) | any change to either | rebuild (3.4); also when the file is *newer* than the code, so downgrading is safe |
| `state_schema` | `state.db` DDL | a numbered migration | forward only; a newer file than the code raises `SchemaTooNew` |

The public data contract has its own `schema_version` (`00-platform.md` section 3). It is not stored in the
data directory, because nothing on disk is in that schema.

### 7.2 Catalog

No migration scripts. `catalog.py` holds `CATALOG_SCHEMA` and `DERIVE_VERSION`; if either differs from the
file, the catalog is recreated from the files. A change that is cheap to apply in place (a new index) may
ship as an optional in-place step, but the rebuild path must always give the same result, and a test checks
that it does.

### 7.3 State db

`src/store/migrations/state/NNNN_<name>.sql`, applied in order on open under the `maintenance` lease:

1. If `user_version` is behind: copy the file with SQLite's online backup API to
   `.meta/state.db.bak-v<old>` (one copy per old version is kept).
2. For each missing migration: `BEGIN IMMEDIATE`, run the script, `PRAGMA user_version = N`, commit.
3. A failing migration rolls back and the open fails with a `StoreError` that names the script.

Migrations are written to be safe to run again after a crash between the script and the version bump
(`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN` guarded by a column check in a small Python hook).

First creation of `state.db` imports the rows of `.meta/jobs.db` once (read only; recorded in `meta`; the old
file stays where it is). Follows of origin `legacy` are not imported once but mirrored (2.3).

---

## 8. Statuses, "provisional", refresh and the change log

### 8.1 All statuses are stored

- The Store accepts an event payload in any status. The write-time filters
  (`src/match_data_fetcher.py:210-214`, `:1038-1043`; `src/match_fetcher.py:189-200`, `:368-378`) are removed by
  plan item ST-27; until then the callers keep applying them and the Store simply stores what it is given.
- Round files already contain every status today, because they are written unfiltered
  (`src/match_fetcher.py:471`). Indexing them gives the catalog its first fixtures: locally 628 events that
  have no detail directory (311 not started, 315 completed, 2 void).
- "Finished only" is `EventQuery.status_classes = ("completed", "decided_without_play")`, the same two classes
  `MatchFetcher._is_finished_event` uses (`src/match_fetcher.py:63-65`). The existing endpoints pass it when
  `FETCH_ONLY_FINISHED` is true, so their output does not change when fixtures start to be listed.
- `status_class` is `classify_status` (`src/status.py:68-103`); the SofaScore triple is kept in three columns.

### 8.2 Rows from listings, and reconciling copies of a score

A schedule page lists events with their status and score. Today those copies (round JSON, summary CSV,
`basic.json`, export CSV) are never compared. In the catalog there is one row per event and these rules:

1. An event that has an `/event/{id}` payload: its row is always derived from that payload. A listing never
   changes it (except `listed_in`).
2. An event without one: its row comes from the listing with the newest `fetched_at` (`row_source = 'listing'`,
   `has_event_payload = 0`, `layout = NULL`). This is how fixtures appear.
3. When a listing is newer than the stored event payload and disagrees with it in status triple, winner code,
   start time or any score field (the fields `src/refresh.py:84-92` compares), the row gets `stale = 1`. The
   refresh service reads `EventStore.stale()` and re-reads `/event/{id}` for those first; that write goes
   through `put`, logs the change and clears the flag.

Rule 3 is new behaviour (extra refresh requests) and ships with plan item ST-27; before that the flag is
computed but nothing reads it.

The catalog row's `home_score`/`away_score` are the normalised values (`display`, else `current`). The summary
CSV of today holds `current`. The two differ for football matches decided on penalties. The legacy list routes
keep returning `current` (from `home_score_current`), so their output does not change; API v1 returns the
normalised score sheet.

### 8.3 What "provisional" means

Today: a stored record is provisional while it was last observed before `startTimestamp + window`
(`src/refresh.py:63-81`); the window is a setting read at call time (`:27-35`). The rule does **not** look at
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

The public field `quality.provisional` is `settlement == 'provisional'`. Whether the contract also exposes
`open` is for the schema design to decide.

### 8.4 Refresh policy on top of the catalog

| Today | With the Store |
|---|---|
| `refresh_due(basic, observation)` per event, after loading its files (`src/match_data_fetcher.py:830-843`) | `EventStore.refresh_candidates(now, window_s, min_interval_s)`: `observed_gap < :window_s AND observed_at <= :now - :min_interval_s` on the `events_unsettled` index |
| `REFRESH_LEGACY` for records without observation (`src/refresh.py:49-51`, `:72-74`) | `include_unobserved=True` adds the rows of `events_unobserved` |
| `refresh_due_ids(league_id)` walks the tree (`src/match_data_fetcher.py:917-935`) | the same call with `Scope(tournament_ids=[...])` |
| `_needs_detail_fetch` → `full / refill / refresh / none` by loading every slice file (`:813-843`) | `full`: `has_event_payload = 0`; `refill`: returned by `missing()`; `refresh`: returned by `refresh_candidates()`; otherwise `none` |
| `refresh_match` writes `basic.json`, `observation.json`, appends to `score_changes.jsonl` (`:856-909`) | the service fetches and calls `events.observe(id, new, on_event_change=...)`; the callback computes `diff_basic` and `change_row` (both stay in `src/refresh.py`) against the payload that is stored at that moment |
| unchanged refresh only updates `observation.json` (`:897`) | `observe` with an identical payload writes no payload file; it updates `observation` in the manifest and `observed_at`, `observed_gap` in the catalog |
| COMPLETED → VOID keeps the record and sets `status_regressed` (`:878-886`) | same; the flag is sticky in the manifest and in `events.status_regressed` |
| `begin_job_cache` / `end_job_cache` (`:183-190`) | not needed; the catalog is the index |

The policy values and functions (`refresh_window_hours`, `refresh_min_interval_hours`, `diff_basic`,
`change_row`, `status_regressed`) stay in `src/refresh.py`. The Store only gets numbers, a callback and rows.

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
- The catalog's `changes` table is an index of these files (`changes?since=` reads it by `seq`).
- The change log has its own gap-free `seq`. The `change` stream (2.3) is a notification: each
  `change.recorded` stream event carries the `change_seq` it announces. Consumers that want every change read
  the change log; consumers that want to be told read the stream.
- The watcher's own `provisional` marker on the first completed status (`src/watcher.py:256-262`) is part of
  the live event payload and is not changed by this design.

---

## 9. Backup, restore, retention

### 9.1 Backup

`BackupManager.create` takes the `writer` lease, so no bulk job changes the data while the archive is written
(today a data backup is refused while a web job runs, `src/web/routes/data.py:239-246`; the lease extends that
to CLI processes). A live service may keep running: every file it writes is replaced atomically, and a payload
that is newer than its manifest is healed by the reconcile after a restore.

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

Until the format-2 PR lands (plan item ST-24), the Store produces today's zip layout unchanged (plan item
ST-19), so the first step is a pure move.

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

### 9.3 Retention

Nothing is deleted unless a setting says so. Defaults:

| Data | Default | Mechanism |
|---|---|---|
| payloads, manifests | kept | `EventStore.delete`, `Store.clear(scope)` on request |
| change log | kept | — |
| slice history (odds snapshots) | kept | `HistoryStore.prune(older_than=...)` |
| stream events | 7 days and at most 1,000,000 rows | `StreamLog.prune`, run by the process that holds `live` or `sinks`, once per hour |
| watcher state | rows of events that are done and older than 7 days are dropped | on `WatchStateStore.save` |
| jobs | newest 500 rows; 2,000 events per job | on job creation |
| backups | kept | `BackupManager.prune(keep=..., max_age_days=...)` |
| `exports/` | kept | the export command can be told to replace its previous output |
| `.meta/tmp`, `.meta/trash` | emptied when the `writer` lease is taken | — |
| `state.db.bak-v*` | one per old schema version | — |

`Store.clear(scope)` replaces `src/web/routes/data.py:152-182`. It needs `maintenance`, removes both the v3 and
the legacy form of the scope, and clears the matching catalog rows in the same critical section. Old scope
names map as `match_details` → `events`, `matches` → `schedules`, `seasons` → `seasons`. It never touches
`state.db`, the change log or backups.

---

## 10. PR sequence

The one ordered list for both tracks is `03-implementation-plan.md`. The ids used in this document map to it
as follows:

| This document | Plan item |
|---|---|
| fixture factory and reader goldens | G-02 |
| presence predicates and the outcome type in `src/slices.py` | ST-02 |
| Store core (errors, codec, files, layout, manifest) | ST-03 |
| boundary, layering and API-surface tests | ST-04 |
| legacy reader | ST-05 |
| catalog schema, connections, derive | ST-06 |
| indexer, rebuild, verify; listings, reconcile | ST-07, ST-08 |
| state db, job store on it | ST-09 |
| leases, facade | ST-10 |
| read API | ST-30 |
| shadow mode | ST-11 |
| readers moved to the catalog | RD-1 … RD-5, EX-1 |
| follows mirror; watcher state and streams; backup and clear | ST-17, ST-18, ST-19 |
| v3 writer; history | ST-20, ST-26 |
| writers switched | ST-21, ST-22 |
| migrate; backup format 2 and restore; raw export | ST-23, ST-24, ST-25 |
| all statuses, stale-listing refresh | ST-27 |
| removal of transition code | ST-28 |

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
  after `rebuild()`.
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

Not measured: `fsync` cost; behaviour on a cold cache or a spinning disk; gzip speed with stock zlib; anything
on Windows or macOS; any scale above 423 real events.

---

## 12. What changed during reconciliation

Claims that did not hold against the code, and what was changed:

1. **Season-list file choice.** The draft said the web routes take the newest file by mtime. They take the bare
   `<lid>_seasons.json` first (`src/web/routes/common.py:31-33`). Sections 1.1, 1.2 and 5.1 now say so, and the
   Store's rule (newest of all names) is stated as a difference from both readers.
2. **Refresh has no status condition.** The draft defined "provisional" over the terminal status classes and
   put that condition into the `events_unsettled` index. `refresh_due` does not look at the status
   (`src/refresh.py:63-81`). The index predicate and section 8.3 were changed so that the default is exactly
   today's rule; the status filter is a parameter.
3. **Legacy list scores.** The draft's test "catalog rows equal the summary CSV" cannot pass with
   `home_score = display`: the summary holds `homeScore.current` (`src/match_fetcher.py:570-571`). Four columns
   were added for the legacy shapes (3.3) and the difference is described in 8.2.
4. **Dashboard counts.** Besides counting only `season_*` directories, today's statistics sum CSV rows and
   season-list files without de-duplicating (`src/services/stats.py:95`, `:128-131`). Added to 1.2 as a second
   known difference for the statistics reader.
5. **Line references.** CI matrix rows for Windows and macOS are `ci.yml:62-63`; the pytest steps are `:85-93`;
   `NO_TOURNAMENT_DIR` is at `match_data_fetcher.py:58`; the terminal UI's restore is
   `settings_ui.py:459-549`.

Changes that come from reconciling with the service design:

6. **`state.db` holds more.** Job events, sink cursors, runtime facts and all event streams (not only live
   events) are in `state.db`; the service design's "extended `jobs.db`" was dropped. `live_events` became
   `stream_events` with a `stream` column, a de-duplication key and one sequence for all streams.
7. **One outcome type.** `Outcome` is defined once in `src/slices.py` with the statuses `ok`, `empty`, `failed`,
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
  recreating `catalog.db` needs exclusive access. The design retries and documents this, but it is untested
  until ST-03 and ST-10 run on the Windows CI job.
- Network file systems. SQLite WAL and `flock` do not work reliably on NFS/SMB. A user who mounts `DATA_DIR`
  from a NAS gets single-process mode at best.
- A 2.x process and a 3.x process on the same directory. 2.x does not take the lease and does not see v3 data;
  after ST-21 a downgrade shows only the data that was never rewritten. The terminal UI has the same blind
  spot until it is removed: its list, statistics, backup, restore and clear menus only see the legacy trees.
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
