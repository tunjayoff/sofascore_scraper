# Changelog

All notable changes to this project are recorded in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The version
number lives in `pyproject.toml`; a release is a git tag `vX.Y.Z` with a matching section
below (see "Releasing" in the README).

## [Unreleased]

## [3.1.0] - 2026-10-09

3.1.0 removes what 3.0.0 deprecated (the `main.py` flags, the 2.x `/api` routes, the 2.x
environment names and the old backup scopes) and adds what the first use of 3.0.0 asked for:
team records and a team and player filter for exports, counts for every follow, request counters
in the job progress, odds providers by name, single-set darts and e-sports game scores, and slice
rows from new evidence for several sports.

Like 3.0.0 it is installed from source: a checkout of the `v3.1.0` tag with `pip install -e .`,
the release archive (the source with the built web app), or the Docker image
`ghcr.io/tunjayoff/sofascore_scraper:3.1.0`. It is not published on PyPI.

**Upgrading from 3.0** (also in the README, "Upgrading from 3.0"):

- **The 2.x environment names are no longer read.** A value set as `DATA_DIR`,
  `REQUEST_RATE_LIMIT`, `PROXY_URL`, `APP_LANGUAGE`, `SOFASCORE_API_TOKEN`,
  `SOFASCORE_ALLOWED_HOSTS`, `LOG_LEVEL`, … (in `.env`, the shell, a service file or the container
  settings) is ignored, and the default applies: an old `DATA_DIR` leaves the app on the default
  data folder, an old `SOFASCORE_API_TOKEN` leaves the server without an access token. Rename
  each to `SOFASCORE_<SECTION>__<KEY>` (`SOFASCORE_STORAGE__DATA_DIR`, `SOFASCORE_CLIENT__RATE`,
  `SOFASCORE_SERVER__TOKEN`, …). `ssc doctor` and `ssc config show` list every old name still set,
  with its new name and where it is set; `ssc config init --from-legacy > sofascore.toml` writes the
  old `.env` and `config/leagues.txt` as a config file.
- **The catalog is rebuilt on its first open** (catalog schema 2, derive version 8). `catalog.db`
  is rebuilt from the stored files once, without a request to SofaScore; on a large data folder
  the first start takes longer.
- **Rebuild the web app** of a checkout (`cd frontend && npm install && npm run build`). A build
  made before 3.0.0 does not work under the strict Content-Security-Policy: the `'unsafe-eval'`
  policy it needed is gone. The release archive and the Docker image carry a built web app.
- **The `main.py` flags, the 2.x `/api/...` routes and the backup scopes `config`, `seasons`,
  `matches` and `match_details` are removed** (see Removed). An old flag is a usage error (exit 2)
  that names the `ssc` command replacing it; a 2.x route answers 404; backups made with an old
  scope still restore.
- **`ssc export` names its files differently** when `--out` is not given: like a web export
  (`exports/premier-league_2026-10-09_142530.jsonl`), and the wide CSV in
  `match_details/processed/` is `events-wide_<date>_<time>.csv`, no longer
  `all_matches_<epoch>.csv`. Scripts that look for `all_matches_*.csv` must follow, or pass `--out`.
- Run `pip install -e .` again after updating a checkout, as after every update.

### Added

- **Team record.** `GET /api/v1/teams/{team_id}` returns a stored team (or, in tennis, darts,
  MMA, …, a player or pair) with its gender, national-team flag, country and sport, and whether it
  is followed; no request to SofaScore. The follow page of a team shows them in its header
  ("Volleyball · Türkiye · Team · Women") instead of telling same-named teams apart only by
  number, and the catalog suggestions carry gender and national team too (#190).
- The sport registry flag `individual` (`GET /api/v1/sports`): the sports whose players SofaScore
  lists as teams (tennis, badminton, table tennis, padel, snooker, darts, MMA). The web UI reads
  it instead of keeping its own list (#190).
- **Exports by team and player.** `team_ids` and `player_ids` in the export filter (API and
  jobs), `ssc export --team ID --player ID`, and the added teams and players in the web export
  dialog. A player's matches are those of the player follow's stored match list and those whose
  stored line-ups name the player. Teams and players form one filter (a match of any of them),
  combined with the other filters (#190, #194).
- **Counts for every follow.** `/api/v1/status` has `summary.follows[]` (stored, finished and
  detailed events and the coverage of each follow, a player follow's too), and
  `GET /api/v1/events?follow=kind:id` lists a follow's matches. A sync keeps the match ids of a
  player's last match list, so a player follow's page has a Matches tab; before the player's first
  counted download it says that the next download stores the matches of the player's list (#191).
- **Request counters.** The job progress and result carry `requests` (`sent`,
  `budget_wait_seconds`, `backoff_seconds`), and Job detail says how many requests a download sent
  and how long they waited for the request budget (#191).
- Sync log lines carry the league and season names (`league_name`, `season_name`,
  `season_year`) (#191).
- The setting `fetch.confirm_empty_after_seconds` (default 60; see Changed) (#191).
- **E-sports game (map) scores.** `score.sets` of an e-sports match lists each game: the round
  score where SofaScore gives one (finished CS2 series), else 1-0 for the game's winner; games not
  played yet are left out. The match header shows them under the games won, and the normalized
  exports carry them in `score_sets` (#189).
- **Odds providers by name.** The Odds tab, the raw odds list and the Data table name the
  bookmaker ("bet365") instead of "Bookmaker 1"; an id without a known name stays numbered.
  `GET /api/v1/odds/providers` lists the known bookmakers from a built-in table (`id`, `name`,
  `country`, `configured`; no request to SofaScore, no betting link stored), and the Settings row
  of `client.odds_provider` offers them next to the free entry of an id (#192).
- `connection.last_check.superseded` in `/api/v1/status`: an answer came after a failed
  connection check (#191).

### Changed

- **Slice rows from new evidence.** The repaired research explorer recorded the match pages of
  21 sports (finished and not started; run `lv-20261009`), and the slice registry follows it
  (#183, #185):
  - baseball no longer requests incidents, and badminton and table tennis no longer request
    line-ups: their pages never ask for them;
  - badminton and table tennis request point-by-point, which counts for a finished match's
    completeness (as in tennis);
  - pre-game form is optional in American football, badminton, table tennis, baseball and MMA,
    and incidents are optional in futsal: a "no data" answer no longer keeps a match incomplete;
  - e-sports games count for completeness;
  - statistics and point-by-point are no longer requested before kick-off, in any sport.

  A finished baseball match costs one request fewer per download, badminton and table tennis ask
  for point-by-point instead of line-ups, and a not-started match no longer asks for statistics
  (nor, in tennis, badminton and table tennis, for point-by-point).
- **`.env` lines are part of the environment layer**: they win over `sofascore.toml` and pin the
  setting on the Settings page, which writes `config/overrides.json` only. An invalid value in the
  environment stops the app with a configuration error instead of being ignored (#186).
- `STORE_OPEN_RECONCILE_SECONDS` is the setting `storage.open_reconcile_seconds` (#186).
- The catalog schema is 2 (the two unused current-score columns are dropped) and the derive
  version 8 (darts and e-sports, see Fixed and Added): the first open after the upgrade rebuilds
  `catalog.db` from the files (#186, #189).
- Slice summaries, the `not_requested` slices of an event, the coverage report and the season
  counts follow the configured slice selection and the follows table (#186).
- The Docker entrypoint runs `python -m sofascore_scraper.cli.main`; its `web` alias is gone
  (#186).
- The installers and start scripts choose their language from `SOFASCORE_DISPLAY__LANGUAGE` (no
  longer `APP_LANGUAGE` or `LANGUAGE`) and print `python -m sofascore_scraper.cli.main doctor` as
  the check command (#188).
- `ssc doctor` says whether a 2.x name is set in the environment or in `.env`, and points at
  `.env` only for what is there (#188).
- The app no longer creates the empty 2.x folders `match_details/` and `datasets/` in the data
  folder; the Store creates the data folder on its first open (#188).
- Search hits no longer report SofaScore's placeholder team "No team" as a player's team (`team`
  is null) (#190).
- The web export dialog sends chosen teams as `team_ids` instead of their stored match numbers; a
  chosen team and a chosen single match now narrow each other like every other filter (before,
  the team's matches and the match were added together) (#190).
- **The request that confirms a "no data" answer waits** at least
  `fetch.confirm_empty_after_seconds` (60 s; `0` asks at the next download as before). It is a
  planning rule, not a sleep: nothing is sent while it waits. A job resumed right after a stop no
  longer asks again for the matches it just stored (#191).
- The ETA of a league download counts its matches' requests by need (full download, refill or
  refresh) (#191).
- `catalog verify`, the scans and the circuit breaker's cause report in English; the CLI's text
  output adds a description in the user's language (#191).
- **`ssc export` without `--out`** names its file like a web export: the league, the single team
  or player, or the dataset, then the UTC date and time
  (`exports/premier-league_2026-10-09_142530.jsonl`), instead of epoch seconds. The wide CSV in
  `match_details/processed/` is `events-wide_<date>_<time>.csv`, no longer
  `all_matches_<epoch>.csv` (#194). Such a file's name does not say its dataset: the web Exports
  list shows it by its format instead of "unknown", and a raw export's file
  (`events-raw_…`) is listed as the raw dataset.
- A sync that cannot read the follows table logs an error (it still downloads the leagues of
  `leagues.txt`) (#194).
- **Research tooling** (developer scripts, not the app): `scripts/explore_all_sports.py` intercepts
  SofaScore requests over CDP, answers images locally, checks that a held request is still alive
  before and after its slot, runs a self-check (`probe`) at start and has a budget per run
  (`--new-run`, `--max-requests`, `--max-hours`). Everything it writes passes a redaction of the
  client's location and IP addresses and of push server addresses, and a test guards
  `research/**`. The research and push scripts use the 3.1 environment names and keep their own
  browser profile under `~/.cache/sofascore_research/` (#181, #182, #184, #187).

### Fixed

- **Darts played in a single set** (`bestOfSets: 1`) show the legs won: `score.format` is
  `legs_won` with no sets. Before, they were `legs` with the legs presented as sets won. Stored
  matches are corrected on the first open (derive version 8) (#189).
- The connection state read "ok" when a failure followed an answer in the same second; it is now
  the last recorded outcome (#191). `POST /api/v1/status/check` stamps `checked_at_utc` with the
  moment it recorded, so it matches `last_check.at` (#180).
- The web UI help for "Finished matches only in a league download" no longer names the removed
  `/api` match lists (#194).
- The research explorer recorded no SofaScore request (finding V3 of the 3.0.0 live validation):
  Playwright disposed of the held requests while they waited for the research lock, and the
  requests of a page that replaced its document starved the queue (#181, #182).

### Removed

- **The 2.x flags of `python main.py`** (`--headless`, `--update-all`, `--refresh-only`,
  `--watch`, `--web`, `--doctor`, `--diagnostics`, …): an old flag is a usage error (exit 2) that
  names the command that replaces it (#186).
- **The 2.x `/api/...` routes** and their response shapes, `POST /api/export/csv` among them (use
  `/api/v1`; a 2.x path answers 404) (#186).
- **The 2.x environment names** (`DATA_DIR`, `REQUEST_RATE_LIMIT`, `MAX_CONCURRENT`, `PROXY_URL`,
  `APP_LANGUAGE`, `SOFASCORE_API_TOKEN`, `SOFASCORE_ALLOWED_HOSTS`, `LOG_LEVEL`, …): use
  `SOFASCORE_<SECTION>__<KEY>`. Each old name still set gives a `legacy_name` warning that names
  its replacement, in `ssc config show` (also `--json`), `ssc config validate`, `ssc doctor` and
  the diagnostics bundle; a variable named by `proxy_env` or `token_env` is still read. GNU
  `LANGUAGE` no longer chooses the language (#186, #188).
- **The backup scopes `config`, `seasons`, `matches` and `match_details`** (use `all`, `state` or
  `data`); backups made with them still restore (#186).
- The setting `fetch.save_empty_rounds` (a warning when it is still given) (#186).
- The `'unsafe-eval'` Content-Security-Policy for frontend builds made before 3.0.0: every page
  but `/docs` and `/redoc` gets the strict policy; rebuild an old build (#186).
- `sofascore_scraper.utils`, `sofascore_scraper.web.jobs`, `sofascore_scraper.web.progress`, the
  2.x watcher (`watch_events.jsonl` is no longer written) and the fetcher modules (#186); the
  `sofascore_scraper.challenge_solver` alias (use `sofascore_scraper.client.bridge`) (#187);
  `sofascore_scraper/services/stats.py` (`format_size` moved to the `ssc status` command) (#194).

### Security

- **The access token under a root path.** When the app ran with an ASGI root path (uvicorn
  `--root-path`, behind a proxy that strips a path prefix), `/<prefix>/api/v1/…` routes were
  answered without the access token. The security layer and the error handlers now use the path
  the router matches. Default installs were not affected: `ssc serve` and the Docker image never
  set a root path (#191).
- `ssc config show` and the diagnostics bundle print `***` for the value of a schedule task
  option that the task does not define, as they do for sink options; the key stays visible
  (#194).

## [3.0.0] - 2026-10-08

The first tagged release: everything since the version number was set to 2.0.0 (2026-07-27,
when the Vue web app replaced the server-rendered pages).

3.0.0 is installed from source: a checkout of the `v3.0.0` tag with `pip install -e .` (see
"Quick start" in the README), the release archive (the source with the built web app), or the
Docker image `ghcr.io/tunjayoff/sofascore_scraper:3.0.0`. It is not published on PyPI. After
updating an existing checkout, run `pip install -e .` again: the import package is now
`sofascore_scraper` (see Changed). What 3.0.0 deprecates is listed under Deprecated; 3.1
removes it.

### Added

- **Basketball and tennis.** League search, fixtures and match details for football, basketball
  and tennis; eighteen more sports followed in #112, #115 and #118. A league's sport is stored
  in `config/league_sports.json`, and the web app has a sport switch that filters every page.
- **BrowserBridge.** SofaScore answers plain HTTP clients with `403 challenge`, so API
  requests are now made from inside a headless Chromium (Scrapling `StealthySession`) that
  passes the Cloudflare Turnstile challenge on its own. It runs headless on a desktop and on
  a display-less server alike. `SOFASCORE_BROWSER_HEADED=1` shows the window,
  `SOFASCORE_BROWSER_PROFILE` moves the browser profile. After a challenge, requests go
  straight to the browser for 10 minutes ("browser-first" mode).
- **Web app rebuilt** around Leagues, Download, Matches, Match, Activity and Settings pages,
  with dark mode, English and Turkish text, error toasts and a job card with a Stop button. The
  first visit follows the browser's language, or `APP_LANGUAGE` when it is set. Since #107 these
  pages were the classic interface under `/classic`, which #161 removed; the new web UI is
  described under Changed (#39).
- **Job progress** with phases (seasons, matches, details; the export phase was dropped in #99),
  job-wide counters, the list of failed matches, an ETA and SofaScore wait countdowns (#10).
- **Status classification.** `classify_status` separates played, decided-without-play and void
  matches; `extract_scores` reads scores per sport without the ambiguous fields; each saved
  match records the time it was read and SofaScore's change timestamp (in `observation.json`,
  since #104 in the match's `manifest.json`). `docs/settlement-notes.md` describes how to use
  them (#9).
- **Refresh policy.** A finished match stays provisional for `REFRESH_WINDOW_HOURS` (default 72)
  after kick-off and is re-read by later downloads, at most once every
  `REFRESH_MIN_INTERVAL_HOURS` (default 6). Changes are appended to the change log
  (`DATA_DIR/score_changes.jsonl`; since #104 `DATA_DIR/changes/<yyyy>-<mm>.jsonl`).
  `main.py --refresh-only` runs refreshes alone and `--refresh-legacy` also covers records saved
  before `observation.json` existed (#11).
- **Watch mode.** `main.py --watch --sport … --league-ids …|--event-ids …` follows live
  matches and reports status, score and stuck-match events (#12). Since #119 it runs
  `ssc watch --source poll --stdout`: the events are printed and stored in the event log
  (`ssc events`), and `DATA_DIR/watch_events.jsonl` is no longer written.
- **Shared request budget.** `REQUEST_RATE_LIMIT` is one requests-per-second budget for all
  processes on the machine together (web app, CLI, every `--watch`, `--refresh-only`),
  whether a request goes through curl or through the browser. The default is 5 requests
  per second; a higher value is faster and `0` or `off` removes the limit, both at a higher
  risk of being blocked by SofaScore. It is also on the Settings page (Advanced), which
  explains the trade-off and shows a warning while the value is above the default or off,
  and in `/api/settings`. The state is a small locked file under
  `~/.cache/sofascore_scraper/throttle/`; `SOFASCORE_THROTTLE_DIR` moves it (#19, #33).
- **Bridge health.** The browser bridge reports whether SofaScore is answering: `ok`,
  `degraded` after `BRIDGE_DEGRADED_AFTER` (default 3) failed requests in a row, `blocked`
  after `BRIDGE_BLOCKED_AFTER` (10) that span at least `BRIDGE_BLOCKED_MIN_SECONDS` (200).
  It is in `GET /health` (`bridge`, next to a new `throttle` block) and
  `GET /api/bypass/status` (`health`), in a dismissible banner in the web app, and in the
  terminal modes as one line on stderr per state change; the log gets one warning per state
  change (#19).
- **Configuration file, first step.** A `sofascore.toml` is read at start if there is one. It is
  looked for in `SOFASCORE_CONFIG`, then `./sofascore.toml`, then `config/sofascore.toml`. Its
  values win over `.env`; variables set in the process environment win over the file, and every
  setting can be set as `SOFASCORE_<SECTION>__<KEY>` (for example `SOFASCORE_CLIENT__RATE=2`).
  Relative paths in the file are resolved against the file's folder. An unknown key or a value
  of the wrong type stops the start with a message that names the file and the key. Without the
  file nothing changes: `.env` and the existing variables are read as before. `[[follow]]` is
  what downloads and the live service follow (#91, #152), `[[sink]]` and `[live]` are used by
  the live service and `ssc serve` (#91, #95, #101, #125), `[server]` holds the host, the port
  and the access token's variable (#74, #125), `[schedule]` the in-app scheduler (#128), and
  `[defaults]` and `[slices.*]` the choice of what is downloaded (#134, #140). The Settings
  screen of the web UI saves through `PATCH /api/v1/settings` and shows where each value comes
  from (#74, #107); the classic Settings page, which wrote `.env`, went with the classic
  interface (#161). On Python 3.10 this adds the `tomli` package to `requirements.txt`.
- **Setup check.** `python main.py --doctor` checks the environment without contacting
  SofaScore: Python, packages, the browser the bridge starts, the browser profile, the data
  and config folders, the web app build and `.env`. Output is text or `--json`, and the exit
  code is non-zero when a check fails; `--strict`, `--only`, `--skip`, `--lang` and `--live`
  refine it. The launcher runs the check on every start and installs missing Python packages
  and the missing browser (#26).
- A help page (English and Turkish) when the web app is not built, instead of a JSON error
  (#26).
- **Log file.** Everything the app logs is also written to `logs/sofascore_scraper.log`
  (`LOG_DIR`), rotated at `LOG_MAX_MB` (5 MB) with `LOG_BACKUP_COUNT` (5) older files;
  `LOG_TO_FILE=false` turns it off. The web app, the CLI and `--watch` share the file. On
  Windows the file is not rotated while more than one process is writing to it (#24).
- **Diagnostics bundle** for bug reports: `python main.py --diagnostics [PATH]` or
  `GET /api/diagnostics/bundle` (versions, settings with secrets masked, bridge health,
  request budget, setup check results, last jobs, log tail). `GET /api/diagnostics` returns
  the summary as JSON (#24).
- `GET /api/logs`: the most recent log entries, with `limit` and a minimum `level` (#24).
- **Settings → Connection.** A connection check that sends one request to SofaScore when you
  press **Test connection** and shows what happened, together with the request health state;
  and the proxy fields (use a proxy, proxy address) (#23).
- `main.py --recheck-unavailable [legacy|all]` reopens "slice not available" markers written
  by earlier versions so the next download asks again. It sends no requests itself, and
  `--league-id` limits it to one league (#25).
- `_slice_status.json` in match folders: the last failed request per slice and how many "not
  available" counts were confirmed by a definitive answer; since #104 this is kept in the
  match's `manifest.json` (#25).
- `GET /api/sports` lists the supported sports and, for each, the match-detail slices
  requested for it (#18).
- `main.py --web` options `--host`, `--port` and `--dev`.
- **Optional access token.** `SOFASCORE_API_TOKEN` (off by default) protects the web app and
  its API. Once it is set, every `/api` request needs `Authorization: Bearer <token>` or the
  web app's session cookie; the web app asks for the token once (`POST /api/auth/login`, an
  `HttpOnly`, `SameSite=Strict` cookie) and **Settings → General → Sign out** ends the
  session. The live status stream works with the cookie, and `GET /health` answers only
  `{"status": "ok"}` to callers without the token. The token never appears in the log, the
  diagnostics bundle or an API response (#43).
- `main.py --web --allow-any-host`: answer to any `Host` header instead of requiring
  `SOFASCORE_ALLOWED_HOSTS` for `--host 0.0.0.0`. Insecure; see "Security model" in the
  README (#43).
- `POST /api/export/csv`; since #99 it gives the same answer as `GET /api/export/csv` (the
  export, built at the moment of the request) and is kept for one release (#43).
- A "Security model" section in both READMEs: what the app does and what you must do before
  opening it to a network (#43).
- `main.py --version`; the version is also reported by `GET /health` and shown on the
  Settings page. `pyproject.toml` is the single place it is written.
- **Docker image** (`Dockerfile`, `docker-compose.yml`): the web app together with the
  headless browser it needs, running as a non-root user, with data, configuration, the
  browser profile and the log file in volumes.
- **Release workflow** (`.github/workflows/release.yml`): pushing a `v*` tag runs the CI
  checks, pushes the Docker image to GHCR and publishes a GitHub Release with an archive of
  the source plus the built web app.
- This changelog.
- `config/leagues.example.txt`, copied to `config/leagues.txt` on the first run.
- `SOFASCORE_CONFIG_DIR` and `SOFASCORE_ENV_FILE` to move the config folder and the `.env`
  file.
- Continuous integration (GitHub Actions): frontend lint, tests and build; `ruff` and
  `pytest` on Linux with Python 3.10 and 3.14, with a coverage floor (85% since #83); the
  BrowserBridge run against a local fake site with a real Chromium; and `pytest` on Windows and
  macOS with Python 3.14 as best-effort jobs that report but do not block. The test suite runs
  offline against a temporary data set and never touches real data, configuration or `.env`
  (#22).
- `constraints.txt` pins every Python package (indirect ones too) to verified versions. CI,
  the installers, the launcher and the Docker image install with it; `requirements-dev.txt`
  lists the development tools (#22).
- A weekly workflow that tests the newest allowed dependency versions, a `pip-audit` and
  `npm audit` workflow, and Dependabot for pip, npm and GitHub Actions (#22).
- Tests for `sofascore_scraper/fsutil.py` and `sofascore_scraper/paths.py`. Known issues on
  Windows are recorded as expected failures: the config file lock does nothing, atomic writes
  fail under contention, and league or season names with `: ? * " < > |` or a trailing dot
  produce invalid directory names (#22).
- Frontend test suite (vitest) and ESLint (#13).
- Research notes on SofaScore's data: status taxonomy and finish lag
  (`docs/status-matrix/`, #8) and an overview of all sports (`docs/all-sports/`, #17).
- Web app screenshots in the READMEs.
- A command line for servers and automation next to `main.py`:
  `python -m sofascore_scraper.cli.main <command>`, or `ssc <command>` after
  `pip install -e .`. The first
  commands: `version`, `doctor`, `describe`, `config show|validate|init|path`, `diagnostics`,
  `events` (#73), `watch` (#91), `backup create|list|verify|restore` (#109), `migrate` and
  `catalog rebuild|verify|reconcile` (#110). The result goes to stdout (`--json` prints one JSON
  document), logs and errors go to stderr, and the exit codes are fixed: 0 success, 1 error, 2
  usage or configuration error, 3 partial success, 4 SofaScore is blocking, 5 storage error, 6
  another instance is running (#65). Downloads, exports, follows, jobs and status came with
  #119, `ssc serve` (the web app) with #125; their entries are below.
- `ssc doctor` warns when the request budget is above the default of 5 requests per second or
  turned off (#65).
- `ssc config init` prints a starter `sofascore.toml`; `ssc config init --from-legacy` prints
  the equivalent of today's `.env` and `config/leagues.txt` (#65).
- **`ssc events`.** Prints the event log of the data folder as JSON lines: the status and score
  changes that `ssc watch` and `--watch` record, the changes that downloads find
  (`change.recorded`, since #113) and the start and end of every download job, each with a
  sequence number. `--after SEQ` continues where an earlier run stopped, `--follow` keeps
  printing new events, and `--stream`, `--type`, `--event` and `--limit` filter. It only reads,
  so it can run next to a download or a watcher (#73).
- **API v1 (foundation).** A versioned HTTP API under `/api/v1`: `health`, `status`, `sports`,
  `jobs` (list, get, start, cancel, and a job's events as a server-sent event stream that
  resumes with `Last-Event-ID`) and `settings` (read, and change with `PATCH`). Responses use
  one envelope (`{"data": …}`), errors one model
  (`{"error": {"code", "message", "details", "request_id"}}`) with stable codes and English
  messages, and every response carries `X-Request-Id`. Jobs started from the command line are
  listed there and can be cancelled. The access token, the Host allow-list and the cross-origin
  check protect `/api/v1` like the existing routes. The contract is recorded in
  `docs/api/openapi-v1.json` (#74).
- `PATCH /api/v1/settings` stores changes in `config/overrides.json` and refuses a value that
  `sofascore.toml`, the environment or a command-line flag pins, instead of reporting success
  for a change that has no effect (#74).
- `ssc watch`: the live service, one foreground process for every sport. It watches the follows
  with `live = true`, or `--sport` with `--event` or `--tournament`. Events go into the event
  log with sequence numbers and are delivered to the configured sinks (`[[sink]]` or
  `SOFASCORE_SINKS`). `--stdout` also prints them as JSON lines. Only one live service runs per
  data directory: a second one, or a `--watch` running beside it, exits with code 6. Finished
  matches are stored with one `/event` request. When SofaScore blocks requests, the service
  pauses and backs off instead of stopping. Polling was the only source at first and stays the
  fallback of `--source page` (the default since #95) and `--source direct` (#101) (#91).
- `ssc watch --source page`, now the default: for every watched sport the service keeps one
  browser page open, in its own profile `<profile>-live`, and listens to the push connection
  that SofaScore's own page opens. Live events arrive about a second after the change instead of
  up to a poll interval later.
  - Each page needs about 1.8 to 2.6 GB of memory.
  - The service opens no connection of its own and never reads the connection's credential.
  - Polling stays the fallback and is always present. While push is healthy it runs every 120 s.
    When the connection is silent or dropped (it is dropped about every 30 minutes) it runs at
    `poll_interval`, with one round after every reconnect.
  - Every switch between push and polling is written to the event log as
    `system.live_source_changed`.
  - A page that cannot open or crashes is reopened with back-off while polling continues.
  - `--source poll` and the legacy `main.py --watch` start no browser (#95).
- `ssc watch --source direct` (or `[live] source = "direct"`, or
  `SOFASCORE_LIVE__SOURCE=direct`), an explicit opt-in: a light client connects to SofaScore's
  push server itself instead of keeping a browser page open per sport (about 0.2 GB instead of
  1.8 to 2.6 GB per sport).
  - The credential is read at run time from the push connection that the site's own page opens.
    It is kept in memory only and masked if it ever appears in a log line.
  - One connection. The client only subscribes (`sport.<sport>`), never publishes, uses no
    wildcard subjects and pings every 120 s.
  - It reconnects after the drop that happens about every 30 minutes. A rejected credential is
    read again. After repeated failures the source counts as unhealthy and the service keeps
    polling.
  - With a proxy configured it does not connect.
  - It is never chosen for you. `--help`, `describe`, `config validate`, `config show`, the
    READMEs and the log at every start say that it uses SofaScore's own client credential
    outside the site's client, that it may break without notice when the credential or the
    server changes, that it may get your IP address blocked, and that it is a terms-of-use grey
    area that you choose knowingly.
  - Measured on one evening only, with one connection and one subject, for about 38 minutes
    (#101).
- Optional install extra `parquet` (`pip install -e ".[parquet]"`, installs `pyarrow`, version
  16 or newer since #155) for Parquet exports (#108).
- `ssc backup create|list|verify|restore`. A restore takes a backup from the data folder's
  `backups/` by name, checks every member path first, refuses a folder that already holds data
  unless `--force` (which moves that data to the trash first; a restore never merges), rebuilds
  the catalog and checks the result. `--dry-run` shows what would happen. Backups made by 2.x
  (and by earlier 3.0 builds) restore as the old folders. Settings files and `.env` are never
  restored (#109).
- **`ssc migrate`** converts data stored by older versions (`match_details/`, the round and page
  files under `matches/`, `seasons/`, `score_changes.jsonl`) into the new layout (`v3/`,
  `changes/0000-legacy.jsonl`). It is never run automatically. Every new copy is read back and
  compared with the old one before it is put in place, and the old files are kept unless
  `--delete-legacy --yes` is given; that option deletes only old copies whose new copy was
  verified again, and can be run later as a second step. `--dry-run` shows what would be
  converted and the size before and after (`--exact` computes it from every match);
  `--tournament ID` and `--limit N` convert part of the data, and running the command again
  continues. `--purge-derived --yes` deletes the season summaries of seasons that have schedules
  and the CSV exports under `match_details/processed/`. Unrecognised files in a match folder are
  copied unchanged into `_extra/`; a season that has only summary files is listed and left in
  place. The command is refused while a download, `ssc watch` or `--watch` runs on the same data
  folder (exit code 6), and exits with 3 when some items could not be converted (their old copy
  stays). On the owner's data, 423 matches, 90 schedule pages and 6 season lists took 2.7 s and
  7.2 MB instead of 71.7 MB (#110).
- **`ssc catalog rebuild|verify|reconcile`** rebuild, check or reconcile the index of the stored
  files (`.meta/catalog.db`); `verify --repair` re-indexes what does not match.
  `catalog reconcile` always looks at every match folder (#110).
- Eight more sports can be followed: American football, Aussie rules, ice hockey, handball,
  rugby, futsal, minifootball and floorball (`--sport`, the config file, the web app). Their
  scores are read period by period (quarters, halves or thirds), with overtime and, in handball,
  the penalty shoot-out and the aggregate of a two-legged tie. The data folder's index is
  rebuilt once on the first start (#112).
- Five more sports can be followed: volleyball, badminton, table tennis, padel and snooker
  (`--sport`, the config file, the web app). Their scores are read set by set (points per set in
  volleyball, badminton and table tennis; games and tie-breaks in padel; frames won in snooker).
  A retirement or walkover shows in the match status, not in the score. The data folder's index
  is rebuilt once on the first start (#115).
- Five more sports can be followed: baseball, cricket, e-sports, darts and MMA (`--sport`, the
  config file, the web app). Baseball scores are read inning by inning with hits and errors;
  cricket innings by innings (runs, wickets, overs); darts by sets of legs, or by legs in
  matches played without sets; e-sports by games won, and the result of each game is stored from
  SofaScore's games list. MMA has no score: the result is the winner, how the fight was decided
  (for example UD or TKO) and the final round. The data folder's index is rebuilt once on the
  first start (#118).
- Set-based scores in API v1 and in the normalized records say what they count (`score.format`:
  games, points, frames, legs, legs_won, games_won) (#118).
- Cricket matches get the `innings` slice (scorecard), darts matches the `point_by_point` slice
  (#121).
- `ssc describe slices` shows `not_in` and `optional_in`; `GET /api/sports` and `/api/v1/sports`
  report `required` per sport (#121).
- **`ssc sync`, `ssc fetch event|tournament`, `ssc refresh`**: downloads as jobs (in the job
  history, cancellable from the web app or with `ssc jobs cancel`). `--dry-run` sends nothing
  and shows what would be fetched and at least how many requests that takes. Ctrl+C or SIGTERM
  cancels the job, stores it as cancelled, releases the data folder and still prints the result;
  a second Ctrl+C exits at once (#119).
- **`ssc export`** (the wide CSV to the data folder, a file or stdout; the raw stored payloads
  as JSON lines or a folder tree), **`ssc data clear|recheck-unavailable`**,
  **`ssc follows list|add|remove|export`**, **`ssc status`** (`--check` for monitoring,
  `--coverage`) and **`ssc jobs list|show|cancel|tail`** (#119).
- Global flags `--wait SECONDS` (wait for a busy data folder instead of exiting with 6),
  `--progress text|ndjson` (a job's progress on stderr) and `--log-format json` (log lines as
  one JSON object each) (#119).
- The configured `[[sink]]` outputs also receive the events of one-shot jobs (`job.started`,
  `job.finished`); `ssc config validate` checks the sinks (#119).
- `ssc version` prints the data schema and the Store's versions; `ssc describe schemas` includes
  the JSON Schema of the records (#119).
- Streaming commands end an error with a `{"type": "error"}` line, and a reader that closes the
  pipe early (`ssc events | head -1`) no longer makes the command exit with 1 (#119).
- **`ssc serve`**: the web app and the HTTP API, in the foreground until stopped (`--host`,
  `--port`, `--allowed-hosts`, `--allow-any-host`, `--dev`; host and port default to
  `[server] host` and `port`). It keeps the rules of the Host allow-list: names you set are used
  as written, `--host 0.0.0.0` does not start until they are set (exit code 2),
  `--allow-any-host` is the insecure opt-out. Without an access token it warns at start-up when
  it listens beyond this computer. Configured `[[sink]]` outputs are delivered while it runs.
  Ctrl+C or SIGTERM stops it with exit code 0; a server that cannot start (port in use) exits
  with 1. Its log lines and uvicorn's access lines go to stderr (#125).
- **`docs/deploy/`**: systemd units for `ssc serve` and `ssc watch`, a timer for `ssc sync`, and
  pages on the access token, the Host allow-list, running behind a reverse proxy (the limit on
  wrong tokens counts per client address: set `FORWARDED_ALLOW_IPS` for a proxy on another
  machine or container), backups, `ssc migrate`, the memory each live source needs and the
  warnings of the `direct` source (#125).
- The Compose example has an opt-in live service: `docker compose --profile live up -d` also
  runs `ssc watch` (#125).
- **API v1: sessions, status and sinks.** `GET /api/v1/auth`, `POST /api/v1/auth/login` and
  `POST /api/v1/auth/logout` replace the `/api/auth*` routes for the web UI (same cookie, same
  limit on failed attempts). `GET /api/v1/status` now also reports the live service (running,
  source, sports, heartbeat), what the data folder holds (matches, details, seasons, per
  tournament with coverage, disk use, events still in the old layout), the leases held right
  now, the optional features of the server (Parquet, SSE) and the schema version; it still
  answers when the data folder cannot be opened. `POST /api/v1/status/check` tests the
  connection to SofaScore with one request. `GET /api/v1/sinks` shows each configured sink's
  position, lag, last error and dropped events (read-only; sinks are still configured in the
  config file only) (#122).
- **API v1: data.** `GET /api/v1/tournaments`, `/tournaments/{id}`, `/tournaments/{id}/seasons`,
  `/seasons/{id}`, `/seasons/{id}/slices/{key}`, `/events` (filters by sport, tournament,
  season, team, status, date, stored details, name and follows; newest or oldest first;
  optionally a per-event summary of the stored data), `/events/{id}`, `/events/{id}/slices`,
  `/events/{id}/slices/{key}` and `/changes` (score corrections and status changes, oldest or
  newest first, by event, tournament and time). Records follow the normalized schema v1.
  `GET /api/v1/events/{id}/raw` and `/events/{id}/slices/{key}/raw` return the stored SofaScore
  payload unchanged, with `ETag` and `X-Sofascore-Fetched-At` (#123).
- **API v1: follows.** `GET`, `POST /api/v1/follows` and `GET`, `PATCH`,
  `DELETE /api/v1/follows/{kind}:{id}` list, add, change and remove what is downloaded and
  watched: tournaments, and now also teams, players and single events. A follow from the config
  file is shown but can only be changed in the file; where a follow added here is kept is
  described under Changed (#156). `POST /api/v1/tournaments/search` looks a tournament up on
  SofaScore (#124).
- **API v1: exports, backups and maintenance.** `POST /api/v1/jobs` now also starts `export`
  (the 2.x wide CSV, or the stored SofaScore payloads as JSONL, written to the data folder's
  `exports/`), `backup`, `clear` (needs `confirm: true`), `rebuild` (the catalog) and `restore`
  as a check of what a restore would do (a real restore as a job came with #152).
  `GET /api/v1/exports` and `/exports/{id}/download`, `GET /api/v1/backups` and
  `/backups/{name}`, `GET /api/v1/logs`, `/diagnostics` and `/diagnostics/bundle`. Clearing and
  rebuilding hold the data folder for themselves: no download, live service or other data
  operation runs meanwhile (#126).
- **Optional in-app scheduler (off by default).** `ssc serve --scheduler`, or
  `[schedule] enabled = true` in the config file, runs the `[[schedule.task]]` entries of the
  config file inside the web server. A task is `run = "sync"`, `"fetch"`, `"refresh"` or
  `"backup"` (and `"prune-history"`, below), with `every = "6h"` or `cron = "15 */6 * * *"` (the
  machine's local time), and options `league_id` (download tasks) or `scope` and `include_env`
  (backup). Each run is an ordinary job (`origin` scheduler) that shows in the job list and
  waits for no one: if the task's previous run is still going, or another download or data
  operation holds the data folder, the run is skipped and logged, and missed times are not
  repeated. An `every` task counts from its last run in the job history, so restarting
  `ssc serve` does not restart the count; an interval that passed while the server was off runs
  once at start (#155). An unknown task or option stops `serve` before it starts (exit code 2).
  `--no-scheduler` turns it off for one run. The scheduler does not run with `--dev`.
  `GET /api/v1/status` has the new `schedule` field (`enabled`, `next_runs`), and
  `capabilities.scheduler` is true while it runs. On shutdown, a scheduled job that is still
  running is cancelled (#128).
- Scheduler task `run = "prune-history"` with `older_than = "90d"`: deletes older snapshots of
  the kept slice history (odds), keeping the newest one of every slice. Off unless configured
  (#155).
- **Export datasets in an open format.** `ssc export --dataset events|slices|changes` writes the
  records of the data schema (version 1): `events` (matches with status, score, winner and
  record quality), `slices` (the state of each stored response about a match) and `changes`
  (corrections found in stored matches), as JSONL (one record per line, as the API returns it),
  CSV, Parquet or SQLite (one column per field, named by its path such as `score_home`; lists as
  JSON text; Parquet needs the optional `pyarrow`, `pip install -e ".[parquet]"`). Filters
  `--sport`, `--tournament`, `--season`, `--event`, `--status`, `--from`, `--to`; without
  `--out` the file goes to the data folder's `exports/`, `--out -` streams JSONL or CSV,
  `--force` replaces an existing file. The raw export (`--schema raw`) takes
  `--dataset events|slices` and the same filters. The web API starts the same exports as jobs
  (`POST /api/v1/jobs` with `kind: "export"`; `dataset` may now be `changes`, `filter` takes
  `status_classes`, `from` and `to`), and `GET /api/v1/exports` names the `schema_version` of
  each export. `ssc export` without `--dataset` or `--schema` still writes the 2.x wide CSV
  (#130).
- **Choose what is downloaded.** Each data type of a match (statistics, line-ups, incidents,
  head-to-head, form, streaks, point-by-point ...) can be switched on or off: for all sports
  with `[defaults] slices`, per sport with `[slices.<sport>] enable / disable`, and per follow
  with `slices` (a list = only these; `enable` / `disable` = changes to the defaults). A data
  type that is not selected is never requested, and a match is complete when the selected ones
  are stored. Unknown names are a configuration error. Through the API: follows take `slices`,
  `PATCH /api/v1/settings` accepts `defaults.slices` and `slices.<sport>`,
  `GET /api/v1/settings` lists the per-sport selections and each setting's type, limits and
  choices, and `GET /api/v1/sports` shows each data type's group, phases and whether the
  defaults select it. `ssc describe slices` shows the same. Without any of these settings
  nothing changes (#134).
- **Odds and non-match data, off unless chosen.** New data types can be selected like the other
  slices (`[defaults] slices`, `[slices.<sport>]`, a follow's `slices`): `odds` (featured and
  all markets, odds changes, winning odds, from the bookmaker set in `[client] odds_provider`),
  `standings` (total and home tables), `season` (season info, cup tree), `leaders` (top players
  and teams), `rankings` (tennis players' rankings and the ATP list; the WTA list too since
  #175) and `players` (season statistics of followed players). Nothing of this is requested
  unless named. A read of odds is a snapshot: before kick-off they are read again on each sync
  once they are 30 minutes old (in the week before the match), and once more after the match;
  every changed read is kept in the slice's history. Season, team, player and sport data are
  fetched once per owner, not per match, and refreshed while the season is running. New API
  routes: `GET /api/v1/events/{id}/odds/{key}` (normalized odds, snapshot by snapshot),
  `GET /api/v1/seasons/{id}/slices`, `GET /api/v1/seasons/{id}/standings`; new export datasets
  `odds` and `standings` (`ssc export --dataset odds`). `GET /api/v1/sports` and
  `ssc describe slices` list every data type with its owner. Without a selection nothing
  changes (#140).
- `[client] odds_country`: a country code recorded with every odds read; empty (the default)
  records none. It is never derived from the machine (#155).
- API v1: a season-list job (`sync` with `only: "seasons"`), a fetch and a refresh of events by
  id (`event_ids`), a sync of named follows (`follows`), and a real restore as a job (`restore`
  with `dry_run: false`). `GET /jobs` filters by `origin` and `target`, `GET /follows` by
  `sport`, `GET /changes` by `sport` and `regressed` with the participant names; per-season
  counts (`GET /tournaments/{id}/seasons?include=counts`); `/status` shows the data-folder path,
  the last migration and the sinks (#152).
- `ssc sync --only seasons`; `ssc status` shows the last migration and the sink lag, and
  `--disk` the disk use; `ssc config validate` checks the scheduler's tasks (#152).
- **Team, player and match follows download their matches.**
  - A team follow reads the team's recent and upcoming matches from SofaScore. A player follow
    reads the player's recent matches. A match follow is that match.
  - The window comes from the follow's `seasons`: `current` = the last 365 days, `last:N` = N
    years, `all` = up to five pages back, season ids = those seasons.
  - Every match is downloaded with the follow's data selection.
  - This works in `ssc sync` (also `ssc sync --follow team:42`), the scheduler and the web UI's
    sync (`POST /api/v1/jobs` with `follows`) (#156).
- **Search teams and players by name:** `POST /api/v1/tournaments/search` with
  `kinds: ["team", "player"]` returns typed hits with sport, country and a player's team (#156).
- **Delete one league's data.** `POST /api/v1/jobs` `clear` with `tournament_id` (and
  `season_id`) deletes that tournament's (or season's) stored matches, schedules and season
  list. `DELETE /api/v1/follows/{id}?delete_data=true` removes a follow together with its data
  (#156).
- `/api/v1/status`, `/health` and `/status/check` report the connection to SofaScore as
  `never_tried`, `ok` or `failed`, with the time and reason of the last failure and the last
  connection check. `bridge.last_success_at` and `bridge.last_failure_at` count requests of
  every transport (#156).
- `GET /api/v1/exports` lists and serves the files `ssc export` wrote into the data folder's
  `exports/` (#156).
- `ssc doctor` checks the config file (`config`): a broken `sofascore.toml` and a data or log
  folder it names that cannot be written are reported, with the loader's message (#155).
- **The web UI does everything the API does** (#161):
  - **Follow teams, players and single matches**: find teams and players by name, follow a match
    or its teams from the match page; their matches are downloaded.
  - **Choose seasons before the first download**: the season list of a new league is read on a
    click and its seasons can be ticked by name; each season shows its counts.
  - **Choose the data to download** as checklists: per follow ("use the defaults" or "choose"),
    and in Settings › Data for every sport and per sport. Betting odds are off by default and
    marked so.
  - **Exports** of normalized data as CSV, JSONL, Parquet (with `pyarrow` on the server) or
    SQLite, with status and date filters; files written by `ssc export` are listed.
  - **Restore a backup** from the Backups screen, as a job, with a confirmation and its result.
  - **Delete one league's data**, when removing it or in Data cleanup (also one season); move a
    league of the old league list into the app to edit it.
  - Job log lines in Turkish and English; readable names for the season-list and odds clean-up
    jobs.
- `GET /api/v1/events/{event_id}/extra`: SofaScore's result note, the series score, the venue and
  the referee from the stored event payload; `finished_details` in `/api/v1/status` and in the
  season counts (#174).
- The tennis `rankings` data type reads the WTA list (`/rankings/6`) next to the ATP list (#175).
- `ssc watch --idle`: with nothing to watch (no follow with `live = true`) it logs one line and
  waits, reading the follows again every 60 seconds, instead of exiting with code 2. The Compose
  example's `sofascore-watch` service and the systemd unit in `docs/deploy/` use it, so
  `docker compose --profile live` without a live follow no longer restarts the container every few
  seconds. Without `--idle` nothing changes. A usage error of `python main.py` (also inside the
  Docker image, whose entrypoint runs it) now points to `ssc --help`.

### Changed

- **Licence: MIT → [PolyForm Noncommercial 1.0.0](LICENSE).** Commercial use now needs a
  separate licence. Versions published before this change remain available under MIT.
- `main.py --web` listens on `127.0.0.1` only (it used to bind `0.0.0.0` with reload on).
  Opening it to the network is an explicit `--host`, and logs a warning.
- `main.py --web --host 0.0.0.0` (every interface) does not start until
  `SOFASCORE_ALLOWED_HOSTS` lists the names or addresses you open the app with, or
  `--allow-any-host` is given. It used to accept every `Host` header without saying so, and it
  replaced a `SOFASCORE_ALLOWED_HOSTS` you had set; that value is now always used as written.
  `--host <one address>` works as before. On a non-local address without
  `SOFASCORE_API_TOKEN` the app prints and logs one warning: anyone who can reach the port
  can read and delete data and change settings (#43).
- The remote league search is `POST /api/leagues/search-remote` (it was a `GET`), and
  `GET /api/export/csv` no longer creates a missing export file; since #99 it builds the export
  at the moment of the request, writes nothing, and answers `404` only when there is no match to
  export. No `GET` endpoint changes anything any more (#43).
- A backup that includes `.env` (`?include_env=true`) has `_with_env` in its file name (#43).
- The interface language variable is `APP_LANGUAGE`. `LANGUAGE` clashed with GNU gettext;
  a legacy `LANGUAGE` value is only honoured when it is `tr` or `en`.
- **English by default.** One rule chooses the language everywhere (terminal, `--doctor`,
  installers, launcher, web app): a language you set wins (`APP_LANGUAGE`, or the choice
  saved on the Settings page), then the system language (`LC_ALL` / `LC_MESSAGES` / `LANG`,
  on Windows the display language; in the web app the browser's language list), then
  English. Until now everything was Turkish unless `APP_LANGUAGE` said otherwise.
  `.env.example` leaves `APP_LANGUAGE` empty instead of `tr`, and the example Compose file
  no longer sets it. An `.env` that sets `APP_LANGUAGE` keeps its language; one without it
  now follows the system language (#39).
- `main.py --help`, the messages of `--watch`, `--refresh-only` and `--headless`, the progress
  lines of a headless download, the launcher and the installers exist in both languages. The
  help and the installers used to be Turkish only and the launcher English only. Log lines are
  English: those of the data store (#70, #85, #100), the match-details downloads (#113), the CSV
  export (#99), the season reader's catalog lines (#79) and the schedule and season downloads
  first, every other one since #171; the data store's error messages since #172 (#39).
- `GET /api/settings` reports `language_explicit`: whether `APP_LANGUAGE` is set (#39).
- The comments in `.env.example` are in English (#39).
- `config/leagues.txt` and `config/league_sports.json` are user state and no longer tracked
  in git.
- The CLI and the web app use the same data folder (`DATA_DIR`); `--data-dir` only overrides
  it when given.
- Backups are written to `DATA_DIR/backups`, downloaded through
  `/api/data/backups/{name}`, and leave `.env` out unless `include_env=true`.
- `GET /api/matches` accepts several league ids and can filter on whether details are present.
  Since #89 it reads the data folder's index, with no fallback to the export CSV.
- The request layer raises typed errors (`APIError`, `RateLimitError`, `NetworkError`,
  `ResourceNotFoundError`) instead of returning `None`, does not sleep after the final
  attempt, does not retry permanent 4xx responses, and really caps in-flight requests at
  `MAX_CONCURRENT`.
- **Downloads are slower by default.** With `REQUEST_RATE_LIMIT` unset, all processes
  together send at most 5 requests per second, so the match details of a 380-match football
  season (about 2,700 requests) take roughly 9 minutes instead of one to two. `MAX_CONCURRENT`
  no longer changes that total. A value you have set yourself is used as before (#33).
- The fixed pauses that only slowed requests down are gone, because the request budget now sets
  the pace: 1 s between batches of a bulk download, 0.2 s between matches in the one-by-one
  download and 1 s between matches in `--refresh-only`. The wait after each request
  (`WAIT_TIME_MIN` / `WAIT_TIME_MAX`) and the request layer's back-off after errors are
  unchanged; the extra per-match retries and their back-off went with #113 (#33).
- Watch mode: the 1 s spacing between requests is shared by every `--watch` process on the
  machine instead of applying to each process, so one watcher per sport stays at 1 request/s
  in total. With `REQUEST_RATE_LIMIT=0` it applies per process as before (#19).
- Round files (since #98 the schedule pages under `v3/`) store the raw payload; a round with
  unfinished matches is fetched again once it is older than 6 hours.
- The supported sports are defined in one registry (`sofascore_scraper/sports.py`) that the CLI,
  the downloader, the watcher and the web API read (#18).
- Command-line text goes through the locale files, so the CLI follows `APP_LANGUAGE`.
- The web app bundles its fonts instead of loading them from Google Fonts.
- One statistics service feeds the web dashboard and `/api/stats/system`.
- File and SQLite work in web handlers runs in the thread pool instead of blocking the
  event loop.
- Dependencies: `scrapling[fetchers]` pinned to the version the challenge solve was verified
  with, upper bounds on major versions, `pydantic` declared.
- Installers and the README install the browser with
  `python -m patchright install chromium --no-shell` (patchright's own Chromium; an installed
  Google Chrome is not used and never was). The installers also check Node.js, build the web
  app when possible and finish with the setup check (#26).
- A new install starts with no leagues; the example in `config/leagues.example.txt` is a
  comment (#26).
- Launch scripts keep the terminal window open after a failed start (#26).
- **Supported platforms.** Linux and Docker are the officially supported platforms; Windows
  and macOS are best-effort.
- The circuit breaker thresholds (`RATE_LIMIT_THRESHOLD_CONSECUTIVE`,
  `RATE_LIMIT_THRESHOLD_RATIO`, `SERVER_ERROR_THRESHOLD_CONSECUTIVE`) keep their values but
  count requests instead of match attempts, across the whole job. A 404 ends a failure
  streak. The breaker also trips early when the browser bridge turns `blocked` during the
  job (#25).
- Matches without a SofaScore unique-tournament id were saved under
  `match_details/_no_tournament/<sport>/<match id>/`; since #104 they are stored by their id
  like every other match. Existing records are not moved (#25).
- League search and season refresh report why a request to SofaScore failed (blocked,
  browser cannot start, rate limited, network, league not found, unexpected answer) with a
  next step, instead of "No results" or an unexplained failure. API:
  `{"detail": {"reason", "message"}}` with 502 / 503 / 404 (#23).
- `POST /api/bypass/test` always answers 200 with `success`, `reason`, `browser_ready` and
  `health` (#23).
- Changing `LOG_LEVEL` / `DEBUG` on the Settings page takes effect immediately, without a
  restart (#24).
- When stdout is not a terminal (Docker, cron, pipes) console log lines are plain
  timestamped text (#24).
- The crash message names the log file instead of referring to one that did not exist (#24).
- While a download is running, `POST /api/data/backup?scope=config` is the only backup scope
  available. The web app shows a translated message for the refusals listed under Fixed
  instead of a raw server error, and `POST /api/settings` answers `data_dir_changed: true`
  when the data folder changed (#21).
- The job history moved from `DATA_DIR/.meta/jobs.db` to `DATA_DIR/.meta/state.db`. The existing
  history is copied over once, when the new file is first created. `jobs.db` is not written
  any more and is left in place, so an older version started on the same folder still shows
  its own history (but not the jobs run by this version). The diagnostics bundle reads the
  new file.
- `API_BASE_URL` now applies to season lists, match details, refreshes and watch mode as
  well. Until now only the fixture requests (rounds and event pages) used it; the others
  always went to `https://www.sofascore.com/api/v1`. An empty `API_BASE_URL` means the
  default address, and a trailing slash is ignored. The value is read once at start-up.
- One data folder has one writer at a time, across processes too. A running download holds a
  lock file under `DATA_DIR/.meta/locks/`. A second web server on the same folder now answers
  `409 job_running` to `POST /api/fetch`, to clearing data and to a backup that includes data,
  and `409 data_operation_running` while the other server is clearing or backing up; before,
  both would have written the same files. The lock is an operating-system file lock, so it is
  free again when the process ends, however it ends. On a file system without lock support (some
  network shares) a warning is logged and the folder must be used by one process, as before.
  Since #64 the command-line runs (`--headless --update-all`, `--refresh-only`,
  `--recheck-unavailable`, `--watch`) take the lock too (#104).
- **Store layer: file modes and sub names.** Payload files, manifests and `.meta/schema.json`
  written by the new Store layer get the mode the process umask gives (0644 with the usual umask
  022) instead of 0600, so a Docker bind mount read by another user, or a backup tool running
  under another account, can read the data. The lock files (#70), `state.db` and `catalog.db`
  (#85), the match details, season lists and schedules the Store writes since #98 and #104, the
  CSV export and the files that are appended to (the change log under `changes/`) follow the
  umask too; the files under `config/` written by the older helpers
  (`leagues.txt`, `league_sports.json`, `overrides.json`) keep mode 0600, and `.env` and the
  browser profile stay private. Slice sub names are lower-case only (`[a-z0-9_.-]`), because
  Windows and default macOS file systems do not distinguish case. Since #91, `--watch` no longer
  writes `watch_state_<sport>.json` (#53).
- League search in the web app now follows `API_BASE_URL` like every other request. It was
  the one request that still went to `https://www.sofascore.com/api/v1` whatever the setting
  said. With the default setting nothing changes (#57).
- The CSV step of a web download and `POST /api/export/csv` no longer print the terminal menu's
  lines (`Headless Mode: Exporting CSV...`, `CSV Conversion:`,
  `CSV file successfully created: …`) to the server console. Since #99 a web download has no CSV
  step and the web export writes no file (#57).
- `--watch` keeps its state in the data directory's `state.db` and also stores every event with
  a sequence number in the durable `live` stream. An existing `watch_state_<sport>.json` is
  imported once, on the first run after the upgrade; since #91 the file is no longer written,
  and since #119 `watch_events.jsonl` is not written either (#59).
- Headless runs (`--headless --update-all`, with or without `--league-id` and `--fetch-mode`)
  now run the same flow as a download started in the web app. The terminal menu's banners are
  gone from the output; a run ends with one summary line
  (`Download finished. Matches that needed details: …`), and seasons whose match list came back
  empty or could not be fetched are counted and reported on stderr. A league whose season list
  cannot be fetched is asked once per run instead of twice. The data files are the same.
  `--headless --csv-export` prints only the result line (#64).
- Only one process writes to a data folder at a time, on the command line too:
  `--headless --update-all`, `--refresh-only` and `--recheck-unavailable` do not start while a
  download runs in the web app or in another such run. They print who holds the lock (process
  id, host, purpose, since when) and exit with code **6**. A second `--watch` for the same sport
  on the same data folder is refused the same way. `--headless --csv-export` on its own is not
  affected. These runs create `.meta/` in the data folder, as the web app does (#64).
- An `APP_EXIT_CODE` variable in the environment no longer overrides the exit code of a
  headless run; the exit codes themselves are in the entry of #119 (#64).
- The warnings the logger prints to stderr (invalid `LOG_LEVEL`, log file cannot be opened or
  written) are in English (#65).
- Downloads and refreshes started from the command line (`--headless --update-all`,
  `--refresh-only`) now appear in the job history of the web app, with their progress and
  result, and can be stopped from another process: the web app's cancel request stops a
  command-line run, which then ends like after Ctrl+C. Starting a second server or a
  command-line run no longer marks a job that is still running as interrupted; a job whose
  process died is marked interrupted the next time the folder is opened (#69).
- A download that was stopped early because SofaScore kept refusing requests, or in which some
  matches could not be fetched, is now recorded as *partial* instead of completed; the web app
  shows it as before. A job that ends after a stop request is recorded as cancelled (#69).
- The job history keeps the newest 500 jobs. The data folder's `state.db` moves to schema
  version 2 (a copy of the old file is kept as `.meta/state.db.bak-v1`) (#69).
- Lock files under `DATA_DIR/.meta/locks/` are created with the permissions the process umask
  gives (0664 under umask 002) instead of always 0644, so a second account of the same group can
  take a lock in a data folder it shares. Nothing changes under the usual umask 022. Lock files
  that already exist keep their permissions (`chmod g+w DATA_DIR/.meta/locks/*.lock` once, or
  delete them while nothing is running). The log line of a state-database migration is English:
  `Store: state.db migration applied: 0001_initial` (#70).
- The existing `/api/…` routes are deprecated. They keep working for one more release, are
  marked deprecated in `/docs`, and answer with `Deprecation: true` and a `Link` header that
  names the `/api/v1` successor. The deprecation changes nothing else; the limit on wrong access
  tokens in the next entry applies to them too (`429 too_many_attempts`) (#74).
- Access token: `[server] token_env` in `sofascore.toml` can name the environment variable that
  holds the token. Wrong tokens are now limited: after 5 failed attempts from one address
  (sign-in or `Authorization: Bearer`), further attempts from that address are refused for 30
  seconds, doubling up to one hour (`429 too_many_attempts` on the existing routes,
  `401 unauthorized` with `Retry-After` on `/api/v1`). Signed-in browser sessions are not
  affected (#74).
- The data folder now holds an index of what was downloaded (`.meta/catalog.db`). It is built
  from the files the first time the folder is opened (one log line,
  `Catalog built from the files in …`; under a second for a few hundred matches) and updated
  after every download. The dashboard and the statistics (#78), the season lists (#79), stored
  matches (#80) and the match lists (#89) are read from it; it can be deleted at any time and is
  rebuilt (#75).
- The dashboard and the statistics now count from the data folder's index
  instead of walking the files. A match that is listed in two summary files and a league with
  two season-list files are counted once; details that sit outside a league's season folders
  (single-match downloads, folders of old versions) are counted; a downloaded match that no
  schedule lists counts as a match. The match count follows the `FETCH_ONLY_FINISHED` setting
  when the page is read: with the setting on, scheduled matches that are not finished and have
  no details are not counted, also in seasons that were downloaded while the setting was off. On
  folders without these cases the numbers are the same. Disk usage is measured again at most
  once a minute unless a download or a clear changed the data (#78).
- The season list of a league is read from the data folder's index everywhere (web app and
  downloads), so both see the same list. When `seasons/` holds several
  list files for one league (left by older versions or by renaming a league), the newest one is
  used whatever its name; before, the web app preferred a bare `<id>_seasons.json` and downloads
  preferred the file named after the configured league, even when it was the older one. A list
  named after the league alone (`LaLiga_seasons.json`) and a list that starts with a byte order
  mark are now found by the web app too; a list file that cannot be read (cut off, or not a JSON
  object) is skipped in favour of the league's newest readable one. Downloads also find the list
  and the match files of a league that is no longer in `leagues.txt`, and of a season whose
  folder was written under another name (#79).
- `GET /api/leagues` no longer rewrites `config/league_sports.json`: for a league whose sport
  you did not choose, the sport is read from the downloaded matches on every request (a chosen
  sport still wins). `GET /api/leagues/search` returns each league's sport instead of `null`
  (#79).
- The league and season lists of the web app now open the data folder's index: the first such
  request creates `.meta/` in the data folder if it is not there yet (#79).
- Log lines of the season reader are English: `SeasonFetcher started`,
  `Season lists loaded from the catalog: …`, `No stored season list for league …` (#79).
- **Stored matches are read through the data folder's index** (`.meta/catalog.db`):
  `GET /api/matches/{id}` and the check for what a download still has to fetch. Folders written
  by current versions behave as before. For data saved by early versions: a match stored only as
  one combined file (`<id>/<id>.json`) is found (it was "not found" and was downloaded again); a
  match that also has a combined file is refreshed while it is provisional (it never was); a
  damaged slice file counts as that slice missing instead of making the match unreadable (the
  API answered 500); and when a match is stored in two folders the newer copy is used (#80).
- `DATA_DIR/.meta/state.db` and `DATA_DIR/.meta/catalog.db` are created with the permissions the
  process umask gives (0664 under umask 002) instead of always 0644, like the files of the Store
  layer and the lock files (files written by the older helpers stay 0600, see the entry of #53),
  so a second account of the same group can write a data folder it shares. Nothing changes under
  the usual umask 022. Files that already exist keep their permissions: to share a data folder
  that an earlier version created, stop everything that uses it and run
  `chmod g+w DATA_DIR/.meta/state.db* DATA_DIR/.meta/catalog.db*` once. The `*` matters: `-wal`
  and `-shm` files that a failed attempt of the second account left behind need the same change,
  by that account or by root. Five more log lines of the data store are English, among them
  `Store: state.db copied before migration (schema version 1): …`, which an existing data folder
  logs once when its state database is upgraded (#85).
- **The match list (`GET /api/matches`) and a season's match list
  (`GET /api/seasons/{id}/matches`) are read from the data folder's index** (`.meta/catalog.db`)
  and are much faster. For data written by current versions the rows are the same; matches that
  start at the same moment may come in a different order. "Only finished matches"
  (`FETCH_ONLY_FINISHED`) now applies when the list is shown, with the same rule as the
  dashboard: a match is listed when it is finished or its details were downloaded (a season
  downloaded with the setting off no longer shows its unfinished matches while the setting is
  on). A match whose details were downloaded shows the scores and status of its details. For
  data saved by older versions: a season stored only as `_matches.csv` is listed, matches whose
  details sit in a flat or `_no_tournament` folder are listed with "details", "details" no
  longer depends on the league filter, and a match in two summary files appears once. A data
  folder that holds only an export CSV (`match_details/processed/all_matches_*.csv`) no longer
  fills the list from it. A season's match list is ordered by start time (#89).
- **Opening a data folder is faster when it was opened less than a minute ago.** On start, the
  web server and the commands compare the folder's index (`.meta/catalog.db`) with the match
  folders on disk. That pass over every match folder is now skipped when another start did it
  less than 60 seconds earlier; season lists, schedules and the change log are still checked. A
  change made to the match folders by hand or by a 2.x version within that minute shows after
  the next start once the minute is over, or with `ssc catalog reconcile`. Set
  `STORE_OPEN_RECONCILE_SECONDS=0` to check on every start (#90).
- `main.py --watch` no longer writes `watch_state_<sport>.json`. Its state is kept in the data
  folder's state database, and an existing file is imported once. It no longer stops when the
  store is busy for a moment (#91). Since #119 it runs `ssc watch --source poll --stdout`, see
  that entry.
- The `data` of live events in the event log (`ssc events`, the sinks) has its final shape.
  `live.status_changed` has `from`, `to`, `change_ts`, `provisional` and `score`.
  `live.score_changed` has `from`, `to` (each `{home, away}`), `change_ts` and `score`.
  `live.stuck` has `status_class` and `start_utc`. The same transition is stored only once
  (#91).
- A data operation (clear, restore) that a running live service or watcher blocks now answers
  `instance_running` instead of `data_operation_running` (#91).
- `ssc watch` without `--source` no longer falls back to polling with a warning: it starts the
  `page` source (#95).
- A download job that is stopped while it waits for the browser bridge's challenge solve or for
  the page's request now stops waiting at once; the shared solve goes on for the other requests
  (#95).
- The browser bridge uses the configured `API_BASE_URL` for relative paths (the connection test
  of the web app), instead of its own copy of the default address (#95).
- **Deciding what to download is read from the data folder's index** (`.meta/catalog.db`): which
  matches need details, which provisional records are due for a refresh (`--refresh-only`) and a
  league's "missing details" no longer read every saved file, and take milliseconds instead of
  seconds on large folders. For data written by current versions the decisions are the same
  (#113 changed two rules later, see its entry); inside a season, matches are now taken in
  kick-off order. Since #129 "only finished matches" (`FETCH_ONLY_FINISHED`) no longer changes
  what is downloaded: details are fetched once a match has finished (see that entry). For data
  saved by older versions: records in flat or `_no_tournament` folders, and in league folders
  without an id, are refreshed too (also with `--league-id`); a match saved only as one combined
  file counts as downloaded in "missing details". If the index cannot be brought up to date, a
  download stops with a storage error instead of treating every match as missing (#97).
- Season lists and match schedules are now stored in the new data layout, compressed:
  `DATA_DIR/v3/tournaments/<league id>/seasons.json.gz` and
  `DATA_DIR/v3/tournaments/<league id>/seasons/<season id>/schedule/`. Previously they were
  stored as `seasons/<id>_<name>_seasons.json` and as round and page files under
  `matches/<league>/<season>/`. The per-season summary JSON
  (`matches/<league>/<season>_summary.json`) is no longer written. Existing files stay where
  they are and are still read. A newer download of the same list or page takes their place in
  the app. Programs that read those folders directly do not see new downloads. Clearing
  schedules or season lists also clears the new files (#98).
- `.env.example` lists `STORE_DURABILITY` (normal or full) (#98).
- **CSV export is built when you ask for it.** `GET /api/export/csv` (the web app's "Export
  CSV") now creates the CSV from the saved matches at the moment of the request and streams it.
  It no longer needs a previous export, writes nothing into the data folder, and never serves an
  old `match_details/processed/all_matches_*.csv` file. `POST /api/export/csv` gives the same
  answer and is kept for one release. Every saved match appears once: matches stored by older
  versions in `match_details/<id>` used to appear two or three times. A match stored only as a
  combined `<id>/<id>.json` file is now included. Rows are in kick-off order. A download in the
  web app no longer ends with a CSV step: its phases are seasons, matches and details.
  `ssc export` (and `--headless --csv-export`) still writes the file to
  `match_details/processed/`. The progress bar is gone from that step, and its log lines are now
  in English (#99).
- `ssc watch --source direct` no longer falls back to polling with the `live_source_unavailable`
  warning; it runs the direct source (#101).
- An error message that names `secret_env` or `token_env` no longer loses the word that follows
  it in the log and the diagnostics bundle (#101).
- New downloads no longer write `matches/<league>/<season>_summary.csv`. The match lists, the
  dashboard and the export read the data folder's index. Existing files are left in place and
  are still read where they are the only source of a season. The CSV export (`--csv-export`,
  `GET /api/export/csv`) is the way to get a table (#102).
- **Match details are stored in the new data layout, compressed**:
  `DATA_DIR/v3/events/<id / 1,000,000>/<(id / 1,000) mod 1,000>/<id>/` (a `manifest.json` and
  one `.json.gz` per slice) instead of `match_details/<league>/season_<name>/<id>/`. On the
  owner's data the same matches take 6.6 MB instead of 55 MB. Folders written by older versions
  stay where they are and stay readable in the app; when such a match is written again (a
  refill, a refresh, a marker reset), its current state is first copied to `v3/` and the old
  folder is left untouched. Programs that read `match_details/` directly do not see matches
  downloaded by this version. Matches without a SofaScore unique-tournament id are stored by
  their id like every other match (no new `_no_tournament/` folders). Changes found by a refresh
  are appended to `changes/<yyyy>-<mm>.jsonl` (LF line endings on every platform) in the same
  step as the match; `score_changes.jsonl` is kept and still read, but no longer appended to.
  The "not available" counts and failed-request marks of a slice live in the match's manifest
  instead of `_unavailable.json` / `_slice_status.json`. A write that finds the data store busy
  is retried a few times; if the store stays busy, the match is reported as failed (before, a
  busy index never failed a save). Clearing data removes seasons, matches and match details from
  both the old folders and `v3/`, and is refused while another process uses the data folder
  (#104).
- The web UI is new (FE-2a): an application shell with a side rail (bottom bar on phone), a
  health pill and a job pill, quick search (`Ctrl K`) and keyboard shortcuts; the Overview,
  Jobs, Job detail (live log over the job's event stream, Stop for jobs of any process, Run
  again), Health and Settings (where each value comes from, locked values with the reason,
  all-or-nothing save) screens on `/api/v1`. Light, dark and system themes, compact density,
  times in local time with UTC on hover, English and Turkish. The screens that needed later API
  routes came with #132 and #133. The previous views stayed under `/classic` until #161 removed
  them; their old addresses lead to the new screens (#107).
- The token prompt shows a countdown after too many wrong tokens (#107).
- Backups now also contain `state.db` (follows added in the app, job history, event log), the
  new layout (`v3/`) and the change log (`changes/`, `score_changes.jsonl`), plus a
  `backup.json` describing them. Settings files are under `config/` in the zip (`config/.env`
  only when asked for), and data paths no longer start with the data folder's name. New scopes
  `state` and `data` exist next to the old ones (#109).
- The event log keeps events for 7 days and at most 1,000,000 rows (unchanged numbers; they are
  now the Store's defaults) (#109).
- A league whose matches are American football or minifootball is no longer counted as football,
  and an ice hockey, handball, rugby, futsal, Aussie rules or floorball league is shown under
  its sport instead of "sport not set" (#112). A table tennis league is no longer counted as
  tennis (#115), and a darts, baseball, cricket, e-sports or MMA league is shown under its sport
  instead of "sport not set" (#118).
- **One download pipeline for match details** (`sofascore_scraper/services/pipeline.py`): league
  and season downloads, matches picked by id, the single-match fetch
  (`POST /api/matches/{id}/fetch`), refills and `--refresh-only` now all run the same code.
  Picked and single matches get the same data as league downloads, including slices such as
  tennis point-by-point; since #129 an unfinished match picked on purpose is stored as it is.
  Retries are the request layer's only (`MAX_RETRIES`, default 3) on every path; the extra per-match
  retries are gone. A single-match fetch runs under its own circuit breaker. Every run uses one
  warmed session and runs its requests concurrently, paced by the shared request budget; the
  terminal progress bar and batch lines of the details phase are gone. Empty answers are counted
  for optional slices too; a body of an unexpected shape (also 0, false or "") is a failed
  request on every path, and a missing slice (404) is recorded with the same reason everywhere.
  Planning: a record that a newer listing shows changed is refreshed first, and a record stored
  while live is no longer refilled or refreshed by downloads (the live service, or a later
  listing, brings it up to date). Changes found by downloads are announced on the `change` event
  stream (`change.recorded`) (#113).
- A cricket match between two days of play ("End of day 1") is shown as live instead of unknown
  (#118).
- Each sport requests the event detail slices SofaScore's match page offers for it. Darts, MMA,
  padel and snooker no longer request lineups or incidents, and e-sports no longer requests
  incidents. Slices that SofaScore answers with 404 on started matches are still requested but
  no longer count for completeness in cricket, darts, e-sports, futsal, ice hockey,
  minifootball, padel and snooker (statistics, pregame form, lineups as listed in
  `ssc describe slices`), so those matches are no longer refilled (#121). Football, basketball
  and tennis changed with #176 (see that entry).
- **Season lists and schedules are typed download steps**: a season list or a round that
  SofaScore refuses is reported as a failed step in the job log and the job ends "partial"
  instead of looking like "no seasons" or "no matches"; a schedule that could not be fetched is
  no longer counted as an empty one. A download right after another one does not ask for the
  season list again within 6 hours or for a season's schedule within 15 minutes (finished rounds
  were already kept, unfinished ones for 6 hours). The season list of a download now uses the
  same warmed session as the rest of the run. The terminal progress bar of the schedule phase is
  gone (#116).
- `match_files_stats.json` and `match_files_report.csv` (the terminal menu's match file report)
  are no longer written into `match_details/processed/`. Coverage is counted from the data
  folder's index, wherever a match is stored, and shown per tournament by
  `ssc status --coverage` and `GET /api/v1/status`. The detail download logs its progress
  ("Details will be fetched for …", "Done: …") as English log lines instead of printing plain
  text (#117).
- **`python main.py` is a thin wrapper around the new command line.** Its flags keep working for
  one release: each run is translated into a command of the new CLI and prints one line on
  stderr that names it (`--headless --update-all` is `ssc sync`, `--refresh-only` is
  `ssc refresh`, `--headless --csv-export` is `ssc export`, `--recheck-unavailable` is
  `ssc data recheck-unavailable`, `--doctor` is `ssc doctor`, `--diagnostics` is
  `ssc diagnostics`), and it follows that command's rules: the result on stdout, log lines on
  stderr. `python main.py <command>` runs any command of the new CLI (#119).
- **Exit codes of downloads, refreshes and exports.** **3** when the run finished but some
  matches, season lists or schedules could not be fetched (before: 0, or 1 when every refreshed
  match failed), **4** when the circuit breaker stopped the run (before: 2), **5** when data
  could not be written (before: 1), **130** / **143** when Ctrl+C / SIGTERM or a cancel from
  another process stopped it (before: 0), **6** when another process holds the data folder
  (unchanged). A run in which SofaScore refused every request now ends with 3, and a CSV export
  with nothing to export with 1 and no file (both used to end with 0) (#119).
- `--watch` runs `ssc watch --source poll --stdout`: the live service with polling, which never
  starts a browser. Its events are printed on stdout as `sofascore.event/1` lines and stored in
  the event log (`ssc events`); `watch_events.jsonl` is no longer written (#119).
- `--config` names the configuration file (`sofascore.toml`); a broken file stops the run with
  exit code 2 and no traceback. A leagues file (`.txt`) given there is ignored with a warning
  (#119).
- `--headless` without `--update-all` or `--csv-export` (and `--watch` without `--sport` and
  ids) is reported before anything runs; the data folder is no longer created first (#119).
- `python main.py --doctor` runs `ssc doctor`: `--json` prints the CLI envelope with the report
  in `data`, and the request-budget check (`budget`) is part of every check list (#119).
- The log line "log level changed" is English (#119).
- `python main.py --web` runs `ssc serve` (with the `--host`, `--port`, `--dev` and
  `--allow-any-host` it was given; still 127.0.0.1:8000 by default) and prints one deprecation
  line. Its log lines are now on stderr, and `--data-dir` now applies to it (#125).
- The launchers (`start-sofascore.sh`, `Start SofaScore.bat`, `Start SofaScore.command`, the
  `.desktop` file, `scripts/start_web.py`) start `ssc serve` on 127.0.0.1 (#125).
- **Docker:** the image's default command is `ssc serve` (the old `web` still works); other
  arguments go to `main.py`, so `docker run … sync` runs a command of the CLI. When no
  allow-list is set anywhere, the entrypoint uses the loopback names; the Compose example sets
  them explicitly. Without `SOFASCORE_API_TOKEN` the container logs a warning at every start,
  which you can ignore while the port is published on 127.0.0.1 only. The image sets the browser
  profile as `SOFASCORE_CLIENT__BROWSER_PROFILE`, so a config file no longer causes a "legacy
  name" warning for it (#125).
- `ssc --help` names `ssc serve` for the web app instead of `python main.py --web` (#131).
- The deprecated `/api/…` routes now run on the same services as `/api/v1`; their answers are
  unchanged. Starting the web server no longer creates `config/leagues.txt` or the job database
  before the first request (#127).
- **Matches of every status are stored.** Season schedules now keep fixtures, live, postponed
  and cancelled matches as well as finished ones. "Only finished matches"
  (`FETCH_ONLY_FINISHED`) now only decides what the match lists and the dashboard show. With the
  setting on (the default) they show the same matches as before. With it off they show fixtures
  too. Match details are downloaded once a match has finished, whatever the setting, so with the
  setting off downloads no longer fetch the details of fixtures. A match you fetch on purpose (a
  single match, or one picked by id) is stored even if it has not finished. "Keep empty rounds"
  (`SAVE_EMPTY_ROUNDS`) is retired: rounds without matches are not stored (#129).
- **A stored match that a newer schedule disagrees with is read again.** If a later schedule
  shows a different score, status or kick-off time for a finished match, the next download or
  `--refresh-only` run reads that match first, records the change in the change log and clears
  the mark. This also happens with `REFRESH_WINDOW_HOURS=0`. The refresh window now applies only
  to records that are finished, decided without play (walkover or retirement), postponed or
  cancelled. A record that has gone back to "live" or "not started" is kept up to date by the
  schedules and the live service instead (#129).
- `ssc export --schema raw` writes the matches oldest first (it wrote them newest first) (#130).
- Web UI, operations and system: new Exports (download, new export with the wide CSV or raw
  JSONL; the normalized datasets since #161), Backups (create, download, a three-step restore
  whose check is a dry run and whose last step gives the server command), Maintenance (rebuild
  the index, the old-layout count with the migrate command, clear with a typed confirmation),
  Logs and diagnostics (log tail with filters, diagnostics summary and checks, the bundle) and
  Sinks (read-only, with lag) screens. Overview shows the stored-data tiles and more attention
  items; Health runs the connection check and shows the live service, the leases and the
  storage. The token prompt and Sign out use `/api/v1/auth` (#132).
- Web UI, data: new Follows (with sync, edit, disable, remove; config follows locked), Follow
  editor (search at SofaScore or by id; seasons; the data selection with odds as their own
  group, off by default, and the request cost per match, which can be chosen there since #161;
  review with live watching and sync now), Follow detail (seasons, events, data selection,
  jobs), Events (filters in the address, data column, fetch missing data and fetch again for
  selected matches), Event detail (score and quality, statistics, line-ups and incidents for
  football and basketball (tennis has neither since #176), every slice with its state,
  corrections, odds; a raw view with search, copy and full-size download) and Corrections
  screens; quick search over follows, tournaments and event ids. The old addresses lead to the
  new screens; the classic views stayed under `/classic` until #161 removed them (#133).
- The web UI needs Safari 16.4, Chrome 111 or Firefox 128 or newer: it moved to Tailwind CSS 4,
  and the screens look the same (#136).
- Web UI, the newcomer pass:
  - **Add league everywhere:** "Add league" is always one click away, in the Overview header,
    the top bar and the phone's More sheet.
  - **Quick search:** it has actions (Add league, Back up, Export, Settings, Help) and, when
    nothing matches, a "Search SofaScore" entry that runs one search in the editor.
  - **Plain words in Turkish and English:** Leagues & follows, Matches, Score changes, Outputs,
    Data cleanup, download and update, access key. Leagues show by name instead of by id.
  - **Help:** a Help panel with a glossary and the getting-started steps, and (i) tips next to
    its words.
  - **Feedback:** a toast when a download ends, and the league page updating itself.
  - **The connection** is no longer "Connected" before SofaScore has answered.
  - **Matches** shows every status by default, and team names on a match link to that team's
    matches. Since #177 the match lists start with the played matches, newest first, with a
    Played / Upcoming / All switch.
  - **The data a league downloads** is shown in one line with Details.
  - **Team, player and single-match follows** were marked "coming soon" until #161 made them
    work in the web UI (#154).
- Downloads (`ssc sync`, the web UI's sync, the scheduler) now read the follows table: every
  enabled tournament follow is downloaded, whichever way it was added (`config/leagues.txt`,
  `[[follow]]` in the config file, the web UI or `ssc follows add`), each with its season choice
  (`all`, `current`, `last:N` or season ids). `ssc follows remove` of a `leagues.txt` follow
  removes it from the file (#152).
- `/api/v1/status` `summary.disk.total` counts the 3.0 layout (`v3/`) and the change log; it
  read 0 for a data folder written by 3.0 (#152).
- A new data folder no longer gets the empty `seasons/` and `matches/` folders of 2.x (season
  lists and schedules are stored under `v3/tournaments/`) (#155).
- The recorded spec of a download job (`GET /api/v1/jobs`, `ssc jobs`) no longer has the unused
  `export` field (#155).
- A `SOFASCORE_SINKS` value shown by the app masks every option the sinks do not know, as the
  diagnostics bundle already did (#155).
- A follow added in the web UI or through the API is always kept in the follows table and stays
  editable (enable/disable, seasons, data selection), also without a config file. Before,
  without a config file, it was written to `config/leagues.txt` and shown locked.
  `config/leagues.txt` is read as before; a follow of it can be moved into the follows table
  with `PATCH /api/v1/follows/{id}` `{"origin": "api"}`. The legacy `/api/leagues` lists only
  `config/leagues.txt` (#156).
- Export files are named after the league or dataset and the date
  (`premier-league_2026-10-06_x7k2m9qa.csv`), and downloads use that name (#156).
- A SofaScore search that finds nothing answers an empty list, also when SofaScore answers 404
  (#156).
- `/api/v1/status` `summary.tournaments[].followed` counts every follow, including those added
  in the web UI (#156).
- **The import package is `sofascore_scraper`** (it was `src`; decision D1). Library code changes
  `from src.… import …` to `from sofascore_scraper.… import …`, and `python -m src.cli.main`
  becomes `python -m sofascore_scraper.cli.main` (also in your own systemd units). There is no
  `src` alias. After updating a checkout, run `pip install -e .` again: the old `ssc` script
  still imports `src`. Log lines and the JSON logs' `logger` field show `sofascore_scraper.…`
  where they showed `src.…`, and the `x-source` notes of `ssc describe schemas` name
  `sofascore_scraper/…` files (#168).
- The Docker image has the `ssc` command (`docker exec <container> ssc status`) (#168).
- Web UI: "Download now" of a league downloads its chosen seasons (it downloaded every season).
  The connection state comes from the server, the same in every browser. Quick search finds
  Health; Help says that `/health` is the server's own check (#161).
- The published data schema (`ssc describe schemas`, schema id `sofascore.data/1`) now includes
  the odds and standings records (`Odds`, `OddsLine`, `StandingsRow`, with `OddsMarket` and
  `OddsChoice`) that API v1 and the `odds` and `standings` exports already return. The
  descriptions of the set and period score fields explain what they count in each sport (#164).
- **Suggestions while typing.** The follow editor's search suggests while you type, like
  sofascore.com (#167):
  - Typing "la" shows your follows and the leagues and teams already stored at once, without
    asking SofaScore.
  - From two characters and a short pause it adds SofaScore's leagues, teams and players from one
    search. A newer keystroke cancels the older search.
  - Answers are kept for the page and for ten minutes on the server, so the same text again
    costs no request. Every request still counts in the request budget.
  - The list works with the arrow keys, Enter and Esc. The quick search (Ctrl K) shows the same
    suggestions under "On SofaScore".
  - New read-only route `GET /api/v1/catalog/suggest`.
- A download job records the fields of its request body (`only`, `event_ids`) instead of the
  service's `mode`; older records are returned in the new shape. Jobs also record the names of
  their follows and leagues (`spec.names`), so the job screens say "Arsenal" instead of
  "Team #42", even after the follow was removed (#167).
- **An export runs while a download runs.** An export job takes its own `export` lease instead of
  the data folder's writer lease; clear, restore and rebuild wait for it (#171).
- Searches from the web UI go ahead of waiting download requests in the shared request budget,
  without exceeding it (#171).
- Search hits carry `gender` and `national` for teams (#171).
- `ssc watch --help` says which score changes are events. For set sports (tennis, table tennis,
  volleyball, badminton, padel), `live.score_changed` follows the sets won, not the games or
  points inside a set (#175).
- Football, basketball and tennis: the pregame form data type is still requested but no longer
  counts for completeness, so a match whose pregame form SofaScore does not have is no longer
  refilled. Tennis no longer requests line-ups or incidents (SofaScore's tennis page has
  neither), and tennis point-by-point now counts for completeness. `GET /api/sports`,
  `/api/v1/sports` and `ssc describe slices` show the new rows (#176).
- Web UI: local type-ahead suggestions (follows and stored names, in the follow editor and
  Ctrl K) rank names and words that start with the text first, leave out names that hold it only
  inside a word when better ones exist, and show at most five before SofaScore's hits;
  `GET /api/v1/catalog/suggest` does the same. The match lists (Matches screen, league and team
  pages) start with the played matches, newest first, with a Played / Upcoming / All switch kept
  in the address. A score change from an unknown score reads "no score → 4-3" ("skor yok → 4-3")
  (#177).
- The READMEs (English and Turkish) are rewritten for 3.0.0, and the user documents
  (`docs/deploy/`, `.env.example`, `docs/push-channel/`, `docs/settlement-notes.md`, …) describe
  its behaviour (#163, #166). The README screenshots show real SofaScore data in the English UI.

### Deprecated

These keep working in 3.0.0 and are removed in 3.1:

- The flags of `python main.py` (`--headless --update-all`, `--refresh-only`,
  `--headless --csv-export`, `--recheck-unavailable`, `--watch`, `--doctor`, `--diagnostics`,
  `--web`, …). Each run prints the `ssc` command it ran; use that command (#119, #125).
- The 2.x HTTP routes under `/api/…` outside `/api/v1`, `POST /api/export/csv` among them. They
  answer with `Deprecation: true` and a `Link` header that names their `/api/v1` successor; the
  web UI no longer calls them (#74, #161).
- The backup scopes `config`, `seasons`, `matches` and `match_details`
  (`ssc backup create --scope`, the `backup` job of `POST /api/v1/jobs`). Use `all`, `state` or
  `data`.

### Fixed

- Saving settings no longer replaces values that come from the process environment. The save
  re-read `.env` over the environment, so a value given in the shell or with `docker -e` (for
  example `DATA_DIR` or `SOFASCORE_ALLOWED_HOSTS`) was replaced by the line in `.env`, even an
  empty one, until the next start. Values that came from `.env` are still refreshed, and a
  value saved on the Settings page still takes effect at once.
- Results that arrived after a round was first saved never reached the summary or got details;
  seasons that already had a summary were never updated. Since #98 and #102 no summary is
  written; the match lists read the data folder's index.
- Choosing a season explicitly no longer silently falls back to the previous season.
- Matches decided after extra time or penalties were rejected by single-match fetch and
  refill.
- Tennis and basketball matches were re-fetched on every job because lineups and incidents
  never exist for them.
- Stop takes effect immediately: no new request after cancelling, and retry back-offs are
  cut short. A stopped job ends as Cancelled instead of Failed.
- The detail circuit breaker never saw 403/429/5xx responses, so a blocked IP kept being
  hammered.
- **The circuit breaker covers every request and every phase.** Slice requests, refreshes,
  `--refresh-only` and the season and schedule phases feed it and stop when it trips. The
  web job card and the CLI say why the job stopped; `--refresh-only` and `--headless` exit
  with code 2 (#25).
- **Failed slice requests are no longer recorded as "not available".** Only a definitive answer
  (HTTP 404, or a 200 with no data) counts toward `_unavailable.json`. A request that failed
  with 403, 429, 5xx, a timeout, a network error or an unreadable response is recorded in
  `_slice_status.json` with its reason and time (since #104 both are kept in the match's
  `manifest.json`), the match stays incomplete, and the next download retries it (#25).
- **A match that could not be written to disk is reported as failed**, not as downloaded. A
  full disk, an exhausted quota, a permission error or a read-only file system stops the job
  with a message naming the path and the reason (#25).
- A request that waits for its turn in the request budget (a low `REQUEST_RATE_LIMIT`, or
  many requests queued) no longer fails with the browser bridge's 120 s request timeout: the
  time spent waiting for the slot does not count towards it (#33).
- **Delete all data**, **Back up**, removing a league and changing the data folder are
  refused while a download is running (HTTP 409, code `job_running`) instead of racing the
  download; a download cannot start while a backup or delete is in progress (code
  `data_operation_running`) (#21).
- Changing the data folder in Settings moves the job history to the new folder immediately,
  without a restart; files in the old folder are not moved. A data folder that cannot be
  created is rejected (HTTP 400, code `data_dir_unusable`) instead of being saved (#21).
- `.env.example` named `SOFASCORE_CHROME_PROFILE`, which was never read; the setting is
  `SOFASCORE_BROWSER_PROFILE`, and an empty value now means the default folder instead of
  breaking the browser start (#26).
- The launcher no longer leaves a half-installed virtualenv unusable, and no longer crashes
  with a traceback when the web app build fails. Bridge error messages no longer recommend
  `playwright install chromium` (#26).
- Log messages containing square brackets are printed literally; `[/]` in a message no
  longer raises an error in the code that logged it (#24).
- BrowserBridge: status-bearing endpoints are fetched with `cache: "no-store"`, so a
  finished match is no longer reported as in progress from the browser cache (#7, fixes
  #6); the bridge survives its page navigating mid-request, recovers from a stale
  `sofa_captcha` cookie, shares one challenge solve between concurrent 403s, and does not
  leak a browser or a locked profile when start-up fails.
- `leagues.txt`: a UTF-8 BOM no longer becomes part of the first league name, names
  containing `:` survive a reload, and the CLI and the web app can no longer overwrite each
  other's edits (locked, atomic writes).
- CLI statistics counted every JSON file as a match detail and could pick another league's
  folder.
- Web: the dashboard season count was always 0; missing-details ignored `season_id`; CSV
  export with a league id exported every league; a single-match fetch during a running job
  now returns 409.
- Web app: late responses can no longer replace newer ones (matches, match page,
  missing-details, job status); failed league, statistics and season loads are shown with a
  retry instead of an endless spinner or an empty list; tabs, dialogs and toggle buttons are
  keyboard and screen-reader accessible.
- Watch mode: the tennis stuck check measures from the real start of play; each sport keeps
  its own state, now in the data folder's `state.db` (#59, #91).
- `Start SofaScore.bat` could not find the launcher script; `install.ps1` failed on a
  parameter named `$Args`.
- Windows: when a file cannot be replaced because another process or thread has it open,
  the write is retried (up to 10 times, 20 ms apart) instead of failing at the first attempt.
- An unanchored `lib/` rule in `.gitignore` kept `frontend/src/lib/` out of the repository,
  so a fresh clone could not build the web app.
- `GET /api/status` reports the application version; it returned a fixed `1.0.0` (#54).
- Changing the data folder in Settings to a folder the app cannot open (for example one
  written by a newer version, or one whose database is locked by another process) answers
  `400 data_dir_unusable` and changes nothing, instead of an internal error (#54).
- Stopping a job no longer slows down what comes next. A stopped job used to leave the slots it
  had reserved in the shared request budget, so the next request from any process waited behind
  them: about 13 seconds with the default settings, about 70 seconds at `MAX_CONCURRENT=50`.
  Requests that are cancelled before they are sent now give their slot back (#58).
- A request that is waiting for its turn inside the browser bridge now stops within a quarter of
  a second when its job is stopped. It used to wait until its turn came (#58).
- Fetching a single match from the web UI now says why it failed when SofaScore refused the
  request (blocked, rate limited, network, unexpected answer) instead of "Match data could not
  be fetched", and no longer reports success when every detail request was refused (#60).
- A single-match fetch is refused (409 `job_running`) while another process is downloading into
  the same data directory, not only while a job of the same server runs (#60).
- **Odd slice files and round file names.** A stored match slice whose body has an unexpected
  shape no longer breaks the "does it contain data" check; in the index of the data folder such
  a file is marked as unreadable. A downloaded answer of that kind is treated as a failed
  request: the match is saved without that slice, the slice is not counted as "not available for
  this match", and it is requested again on the next run. Before, such an answer stopped a
  download of selected matches and made the single-match fetch answer 500. An empty
  point-by-point answer (`{"pointByPoint": []}`) counts as "no data" instead of data. A round
  file whose name has an upper-case letter (`round_1_Final.json`) is read as a schedule page
  again. The index (`.meta/catalog.db`) is rebuilt once the next time the data folder is opened
  (one log line, `Catalog built from the files in …`; under a second for about a thousand
  matches) (#77).
- On Python 3.10 the application could crash (the process aborted) when a data-folder connection
  of a finished thread was closed twice at the same moment; closes are now serialized (#79).
- Stopping a job now also stops the requests that were still waiting for a free request slot.
  Such a request used to be sent after the stop whenever its place in the request budget had
  already come: almost every waiting request with the request budget off or set high, and about
  one request per interval with the default budget on a slow machine (#83).
- Changing the data folder in Settings right as a download finished could crash the server or
  lose the job's end; the change is now refused (409 `job_running`) until the job is fully done
  (#93).
- The data store no longer loses a recorded match change when a write is interrupted between
  storing the match and writing the change log (for example `ssc watch` stopped at that moment):
  the change is added on the next write of that match or the next start. `Store.close()` waits
  for a background job that is finishing instead of closing the database under it, and a
  `state.db` that cannot be written on open is reported as a storage error. Store log messages
  are now all in English (#100).
- **CSV export: formations and whole numbers.** The `home_formation` and `away_formation`
  columns now hold the formation (for example `4-2-3-1`); they were always empty. The per-league
  download (`GET /api/export/csv?league_id=…`) no longer turns whole numbers into decimals
  (`61.0` is `61` again) and no longer rewrites values such as a rating of `6.50`: each row is
  the same as in the full export (#103).
- A setting saved on the Settings page now takes effect even when the same setting was changed
  earlier through `PATCH /api/v1/settings`; before, the earlier value stayed in force and the
  page reported success (#127).
- Stopping a download now also stops the requests that wait inside the browser bridge: a stopped
  job no longer waits for a challenge solve (up to 90 s) or a page fetch (up to 20 s) to finish,
  and a bridge request is no longer sent after the stop because its turn in the request budget
  had already come. An answer the browser already returned when the stop arrived is kept instead
  of dropped (#139).
- After the data folder is changed in the web UI, the old folder is no longer held open (it can
  be moved or deleted on Windows while the app runs) (#152).
- A restore started from the web UI no longer makes its own job disappear from the job list
  while it runs (seen on Windows) (#153).
- `ssc config init --from-legacy` only reads: it no longer creates a missing
  `config/leagues.txt` or writes the follows into the data folder (#155).
- A live browser (`ssc watch --source page` or `direct`) that cannot start no longer marks the
  bridge health of the process degraded (#155).
- The observation returned with a downloaded match is the moment its page was read, as stored
  (it could be one second later) (#155).
- Backups of the scopes `all`, `state` and `config` include the settings saved on the Settings
  page (`config/overrides.json`); a restore brings them back (readable by the owner only) and
  reloads the settings. Older backups leave the current settings alone (#165).
- The Docker Compose example keeps the live watch service's browser profile on a volume, so a
  recreated container does not solve the challenge again (#165).
- The start scripts follow the language chosen in the app (Settings page or `sofascore.toml`),
  not only `APP_LANGUAGE` (#165).
- `ssc doctor`, `main.py --help`, `ssc config` and the image label no longer describe 2.x
  (football only, terminal modes, settings only in `.env`) (#165).
- Web UI: live watching is no longer offered for player follows, which `ssc watch` skips.
  Settings › Data lists the followed sports first and the others under "Other sports". The
  retired "Keep empty rounds" setting is no longer shown. "Show finished matches only" says that
  it only filters the lists. Health lists the scheduler's next runs with their leagues. A search
  hit's country is named in the reader's language (#167).
- Web UI, findings of the end-to-end test (#170):
  - Dialogs opened from a table row no longer open the row's page when clicked; the page behind
    a dialog is inert.
  - Search hits name regions and home nations in your language ("Avrupa", "Güney Amerika",
    "İngiltere"); tennis, darts, MMA … players that SofaScore lists as teams are shown as
    players; the placeholder "No team" is hidden; same-named teams show their number.
  - A follow's page says "Futbol · Avrupa · Lig" (the number is in the facts), says what
    "complete" measures and which data types are missing, follows a running download, and notes
    matches brought by other follows.
  - The job log names seasons by year; a running download's counts have a unit in every phase;
    its time left follows the recent pace.
  - The Odds tab shows a readable table (markets, outcomes, decimal and fractional prices,
    opening price, change, winner); SofaScore's answer stays below.
  - The export dialog offers added teams and single matches.
  - Health names the running job and says scheduler intervals in words ("20 dakikada bir").
  - Settings › Requests: the SofaScore address and other rare settings are under "Advanced".
  - Teams and single matches show their matches with details in Leagues & follows.
- **A football match that went straight to penalties no longer shows an extra-time score**
  (`after_extra_time`, "UZ"): it is set only for status 110 or when SofaScore sends extra-time
  data. Stored catalogs are re-derived on their first open (derive version 6); export files
  written before must be exported again to be corrected (#171).
- Restoring a backup taken from the web UI or the scheduler no longer reports the backup job as
  interrupted (#171).
- A second process (`ssc sync`, the poll fallback of `ssc watch`) can use the browser bridge while
  `ssc serve` holds the browser profile: it opens a temporary sibling profile. When even that
  fails, the error names the process that holds the profile and points to `ssc doctor` (#171).
- `ssc watch` no longer reads followed matches that start more than six hours from now when it
  starts (#171).
- The job log says when a season list or a match list is fresh and is not read again (#171).
- `ssc doctor` checks the data folder set by `storage.data_dir` in the configuration file (#171).
- Log messages are English, Scrapling's lines appear once, and the download log names the kinds
  of extra data saved and not available (#171).
- `config/leagues.txt` is no longer created when a configuration file is used (#171).
- Completeness counts a finished match's data type that SofaScore answered with "no data" as
  resolved, so a fresh download no longer shows 0 % (#171).
- The download log names the kinds of non-match data by name ("Standings saved; Season odds not
  available on SofaScore") instead of their keys (#172).
- Search suggestions, Ctrl K and a team's follow page say "Women" and "National team";
  same-named teams show their number only when that does not already tell them apart (#172).
- Saving settings no longer leaves `config/overrides.json.lock` (or any other `.lock`) next to the
  file (#172).
- Error messages from the data store are English (CLI errors, job errors, API error details)
  (#172).
- Multi-sport display and the findings of the live validation of 2026-10-08 (#174):
  - The match page shows each sport's score: cricket runs/wickets (overs) and SofaScore's result
    note, baseball's line score (innings, R, H, E) and postseason series, legs / frames / maps
    under darts, snooker and e-sports, an MMA fight's winner, method and round, tennis tie-break
    points; an aggregate without scores is not shown.
  - Round names and common odds markets, groups and periods are in the reader's language;
    "Extra time" is not shown for a sport without extra time; the "Provisional" badge moved from
    the match header to the facts, which say why and until when.
  - A follow's coverage and its seasons' completeness count finished matches only, for leagues,
    teams and matches; upcoming fixtures are "not played", not missing.
  - Team and player pages start with the played matches (switch: upcoming, all) and offer no
    sport filter.
  - Suggestions and Ctrl K follow SofaScore's relevance order in one list with the kind of every
    row.
  - A team, player or match follow may have "/" in its name (tennis, padel and badminton
    doubles); league names still may not.
  - The server's time left of a download follows the job's requests per match and request rate.
  - The normalized export's `events` counts the matches it covers (was 0).
  - The client's error texts are English ("(status code 404)").
  - Settings › Data: the tennis player's rankings, "not available in every sport", and what
    "finished matches only" affects; date fields write the chosen day in words; venue and
    referee show on the match overview again.
- `ssc watch --sport X` (repeatable) watches only those sports. Before, every followed sport was
  watched again after the first read of the follows, and the page source opened one browser page
  for each (#175).
- A `--source direct` or page-source run no longer logs "Task was destroyed but it is pending!"
  for the browser's request handlers when a live page or the credential browser closes (#175).
- Live status codes that arrive without a type (ice-hockey periods 1–3, cricket 22, football 42,
  MMA 58, e-sports games 1003–1005) are classified as live, and a push frame that carries only a
  status code no longer logs "Status could not be classified" (#175).
- For a match that has not finished, a data type that SofaScore answered with "no data" is shown
  as `empty` (uncounted, asked again later) instead of `not_requested`. Such an answer no longer
  counts towards completeness once the match has finished (#175).
- `winning_odds`: an answer with one null side is data; an answer with both sides null is
  "no data". The data type is no longer experimental (#175).
- An export, a clear (also removing a follow with its data), a restore or a catalog rebuild
  started right after an export finished no longer answers 409 `data_operation_running`: the
  finished export let go of the data folder a moment after its end was recorded, and the new job
  now waits for that instead of being refused.

### Removed

- The retired server-rendered (Jinja) UI, its static files and the `/legacy-static` mount.
- Unreachable fetcher code and the unused `AdaptiveRateLimiter`.
- Unused dependencies (`requests`, `aiohttp`, `playwright-stealth`, `jinja2`,
  `python-multipart`) and about 330 locale keys only the retired UI used.
- `scripts/catalog_tool.py` (replaced by `ssc catalog`) and `scripts/migrate_match_details.py`
  (renamed league folders in place; the new layout does not depend on folder names) (#110).
- **The interactive terminal menu is gone.** `python main.py` without arguments (or with only
  `--data-dir`, `--config`, `--refresh-legacy` or `--ignore-rate-limit`) no longer opens a menu:
  it prints a short help and exits with code 2. Use the web app (`ssc serve`) to work with your
  data, and the command line (`ssc --help`; `python main.py <command>` when `ssc` is not
  installed) on servers and in scripts. The README has a table of what replaces each menu entry.
  Every deprecated flag (`--headless`, `--refresh-only`, `--watch`, `--doctor`, `--web`, …)
  keeps working for one more release. Moving the data folder is now done by hand: stop the app,
  move the folder, set `DATA_DIR` (#131).
- `colorama` and `tqdm` are no longer dependencies (#131).
- `pandas` is no longer a dependency (nothing used it since the CSV export was rewritten);
  `ssc doctor` no longer checks it (#155).
- Library code without a caller in the app: the `shadow_*` functions of the store package, the old
  bulk-download, CSV and file-report methods of `MatchDataFetcher`, the old schedule and season
  methods of `MatchFetcher` and `SeasonFetcher`, `QueryService.detail_needs` and `refresh_due`,
  `services.export.export_all_csv`, `web.league_sports.resolve_all` and `SyncSpec.export`
  (#155).
- Web UI: the classic interface (`/classic`); its addresses lead to the new screens (#161).

### Security

- The SPA fallback served files outside `frontend/dist` (`/../../.env` returned the real
  `.env`). It now only serves files that resolve inside `dist`.
- DNS-rebinding and cross-site request protection: unknown `Host` headers are rejected, and
  state-changing requests whose `Origin` does not match are answered with 403.
- Backups were written to a publicly served folder and included `.env`.
- Settings, league names, match ids and backup/clear scopes are validated; `api_base_url`
  is restricted to SofaScore over https and `data_dir` to the project or home folder.
- Tokens, cookies, `Authorization` headers, proxy credentials and the value of any `.env`
  key that looks like a secret are masked (`***`) in the log file, on the console and in the
  diagnostics bundle (#24). 500 responses no longer echo exception text.
- `GET /api/settings` no longer returns the proxy password (`***` instead) (#23).
- Requests that another site triggers through your browser: two `GET` endpoints had side effects
  (the remote league search sends a request to SofaScore, the CSV export wrote a file) and were
  not covered by the cross-site check; they became `POST`. Since #99 the CSV export writes no
  file and is served on `GET` again. The check also reads `Sec-Fetch-Site`, so it no longer
  depends on `Origin` matching `Host` alone (#43).
- Every response carries security headers: a Content-Security-Policy (scripts, styles, fonts
  and requests only from the app itself; no inline script, no `eval`, no framing),
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`
  and `Cross-Origin-Resource-Policy: same-origin`. The web app build no longer needs `eval`;
  a build made before this change is served with `'unsafe-eval'` and a warning in the log
  until you rebuild it (`cd frontend && npm install && npm run build`) (#43).
- `.env` is created with mode `0600` and the browser profile folder with `0700`, and
  existing ones are tightened at every start (POSIX; nothing changes on Windows). A backup
  that includes `.env` is readable by its owner only (#43).
- The error text of a failed download (in `/api/scrape/status` and `/api/jobs`) and the
  bridge's last error detail (in `/health`) are masked like log lines, so a proxy password
  inside an error message is not returned (#43).
- `npm audit fix` for a transitive frontend dependency (nanoid, #15).
- The saved proxy password is put back in place of `***` only when the proxy's user, scheme,
  host and port are all unchanged. Changing the scheme or the port now asks for the password
  again; before, the saved password was sent to the changed address (#54).
- The diagnostics bundle no longer carries the host name of the machine in the job history and
  the setup check. The job columns are selected by name, so a column added later is not
  included by default; of a job's origin only the interface (cli, api), the pid and whether it
  ran on this machine (`same_host`) are kept, and a host name inside a job message or in the
  browser profile lock appears as `***` (#71).
- `ssc config show` and the diagnostics bundle show the address of a webhook sink by its host
  only (`https://hooks.example.org/***`). Before, the path and the query of the address were
  shown, and for services such as Slack or Discord those are the credential. This applies to
  `[[sink]]` in the config file and to `SOFASCORE_SINKS` (#73).
- Three more places where something that should stay on the machine could reach a text that
  is handed to other people. The log tail of the diagnostics bundle (`log_tail.txt`) shows a
  host name as `***`: the name of this machine and the host names recorded in the listed
  jobs, as the job history of the bundle does since #71. The error for a `[[sink]]` entry or
  a `SOFASCORE_SINKS` value of the wrong shape names the type of the rejected value instead
  of quoting it (`url: expected a string, got a list`), so a webhook address or a signing
  secret that was written into the wrong field no longer appears in the error message.
  `ssc config show` and the diagnostics bundle print `***` for the value of a sink option
  that the sinks do not define (a misspelt or invented key); the key itself stays visible (#84).
- Frontend build dependencies updated to clear GHSA-vfj7-8cjw-p6xm (`braces`, which came in
  through Tailwind CSS 3, #136) and GHSA-68fv-2mgg-jv7q (`source-map-js`, #146).

## Earlier history

Versions before this changelog were not tagged. The project started in March 2025 as a
terminal tool; the web interface arrived in April 2026 (reported as 1.0.0 by `/health`) and
was replaced by the Vue app on 2026-07-27, when the number became 2.0.0. See `git log` for
details.

[Unreleased]: https://github.com/tunjayoff/sofascore_scraper/compare/v3.1.0...HEAD
[3.1.0]: https://github.com/tunjayoff/sofascore_scraper/compare/v3.0.0...v3.1.0
[3.0.0]: https://github.com/tunjayoff/sofascore_scraper/releases/tag/v3.0.0
