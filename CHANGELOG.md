# Changelog

All notable changes to this project are recorded in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The version
number lives in `pyproject.toml`; a release is a git tag `vX.Y.Z` with a matching section
below (see "Releasing" in the README).

## [Unreleased]

Everything since the version number was set to 2.0.0 (2026-07-27, when the Vue web app
replaced the server-rendered pages). No version has been tagged or released yet, so this
section is what the first tagged release will contain.

### Added

- **Basketball and tennis.** League search, fixtures and match details for football, basketball
  and tennis; eight more sports followed in #112. A league's sport is stored in
  `config/league_sports.json`, and the web app has a sport switch that filters every page.
- **BrowserBridge.** SofaScore answers plain HTTP clients with `403 challenge`, so API
  requests are now made from inside a headless Chromium (Scrapling `StealthySession`) that
  passes the Cloudflare Turnstile challenge on its own. It runs headless on a desktop and on
  a display-less server alike. `SOFASCORE_BROWSER_HEADED=1` shows the window,
  `SOFASCORE_BROWSER_PROFILE` moves the browser profile. After a challenge, requests go
  straight to the browser for 10 minutes ("browser-first" mode).
- **Web app rebuilt** around Leagues, Download, Matches, Match, Activity and Settings pages,
  with dark mode, English and Turkish text, error toasts and a job card with a Stop button. The
  first visit follows the browser's language, or `APP_LANGUAGE` when it is set. Since #107 these
  pages are the classic interface under `/classic`; the new web UI is described under Changed
  (#39).
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
  matches and writes status, score and stuck-match events to `DATA_DIR/watch_events.jsonl`
  (#12).
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
  file nothing changes: `.env` and the existing variables are read as before. `[[follow]]`,
  `[[sink]]` and `[live]` are used by the live service (`ssc watch`, #91, #95, #101), and
  `[server]` can name the access token's variable (#74); not used yet, only checked:
  `[schedule]`, `[defaults]`, `[slices.*]` and the host and port in `[server]`. The classic
  Settings page still writes `.env` and does not show which values the file pins; the Settings
  screen of the new web UI saves through `PATCH /api/v1/settings` and shows where each value
  comes from (#74, #107). On Python 3.10 this adds the `tomli` package to `requirements.txt`.
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
- Tests for `src/fsutil.py` and `src/paths.py`. Known issues on Windows are recorded as
  expected failures: the config file lock does nothing, atomic writes fail under contention,
  and league or season names with `: ? * " < > |` or a trailing dot produce invalid directory
  names (#22).
- Frontend test suite (vitest) and ESLint (#13).
- Research notes on SofaScore's data: status taxonomy and finish lag
  (`docs/status-matrix/`, #8) and an overview of all sports (`docs/all-sports/`, #17).
- Web app screenshots in the READMEs.
- A command line for servers and automation next to `main.py`:
  `python -m src.cli.main <command>`, or `ssc <command>` after `pip install -e .`. Commands so
  far: `version`, `doctor`, `describe`, `config show|validate|init|path`, `diagnostics`,
  `events` (#73), `watch` (#91), `backup create|list|verify|restore` (#109), `migrate` and
  `catalog rebuild|verify|reconcile` (#110). The result goes to stdout (`--json` prints one JSON
  document), logs and errors go to stderr, and the exit codes are fixed: 0 success, 1 error, 2
  usage or configuration error, 3 partial success, 4 SofaScore is blocking, 5 storage error, 6
  another instance is running. Downloading and the web app are still started with `main.py`
  (#65).
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
- Optional install extra `parquet` (`pip install -e ".[parquet]"`, installs `pyarrow`) for
  Parquet exports (#108).
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
  help and the installers used to be Turkish only and the launcher English only. Some log lines
  are still Turkish (those of the browser bridge, the request layer, the schedule and season
  downloads, the configuration and the terminal menu among them); those of the data store (#70,
  #85, #100), the match-details downloads (#113), the CSV export (#99) and the season reader's
  catalog lines (#79) are English (#39).
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
- The supported sports are defined in one registry (`src/sports.py`) that the CLI, the
  downloader, the watcher and the web API read (#18).
- Terminal UI text goes through the locale files, so the CLI follows `APP_LANGUAGE`.
- The web app bundles its fonts instead of loading them from Google Fonts.
- One statistics service feeds the web dashboard, `/api/stats/system` and the CLI
  statistics screens.
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
- The interactive season refresh tries at most twice (#23).
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
  `--recheck-unavailable`, `--watch`) take the lock too; the interactive terminal menu does not,
  except for **Clear data** (#104).
- **Store layer: file modes and sub names.** Payload files, manifests and `.meta/schema.json`
  written by the new Store layer get the mode the process umask gives (0644 with the usual umask
  022) instead of 0600, so a Docker bind mount read by another user, or a backup tool running
  under another account, can read the data. The lock files (#70), `state.db` and `catalog.db`
  (#85), the match details, season lists and schedules the Store writes since #98 and #104, the
  CSV export and the files that are appended to (`watch_events.jsonl`, the change log under
  `changes/`) follow the umask too; the files under `config/` written by the older helpers
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
  a sequence number in the durable `live` stream. `watch_events.jsonl` is still written. An
  existing `watch_state_<sport>.json` is imported once, on the first run after the upgrade;
  since #91 the file is no longer written. On Windows `watch_events.jsonl` now gets LF line
  endings like every other data file (#59).
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
- Exit codes of headless runs are otherwise unchanged, with two exceptions: an unexpected error
  while fetching match details ends the run with exit code 1 instead of being logged and
  ignored, and an `APP_EXIT_CODE` variable in the environment no longer overrides the exit code
  (#64).
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
- The dashboard and the statistics (web and terminal) now count from the data folder's index
  instead of walking the files. A match that is listed in two summary files and a league with
  two season-list files are counted once; details that sit outside a league's season folders
  (single-match downloads, folders of old versions) are counted; a downloaded match that no
  schedule lists counts as a match. The match count follows the `FETCH_ONLY_FINISHED` setting
  when the page is read: with the setting on, scheduled matches that are not finished and have
  no details are not counted, also in seasons that were downloaded while the setting was off. On
  folders without these cases the numbers are the same. Disk usage is measured again at most
  once a minute unless a download or a clear changed the data (#78).
- The season list of a league is read from the data folder's index everywhere (web app,
  downloads, terminal menu), so all of them see the same list. When `seasons/` holds several
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
  folder's state database, and an existing file is imported once. It keeps writing its stdout
  lines and `watch_events.jsonl` in the 2.x format for one more release. It no longer stops when
  the store is busy for a moment (#91).
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
  kick-off order. "Only finished matches" (`FETCH_ONLY_FINISHED`) applies when the plan is made,
  as in the match list: a season downloaded with the setting off no longer offers its unfinished
  matches while the setting is on. For data saved by older versions: records in flat or
  `_no_tournament` folders, and in league folders without an id, are refreshed too (also with
  `--league-id`); a match saved only as one combined file counts as downloaded in "missing
  details". If the index cannot be brought up to date, a download stops with a storage error
  instead of treating every match as missing (#97).
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
  `--headless --csv-export` and the terminal menu still write the file to
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
  busy index never failed a save). In the terminal menu, **Clear data** now goes through the
  data store like the web app's clear: it removes seasons, matches and match details from both
  the old folders and `v3/`, and is refused while another process uses the data folder.
  **Backup** and **Restore** in the terminal menu still copy only the old folders, and the disk
  sizes in **Statistics** count only the old folders; each of these menus says so (#104).
- The web UI is new (FE-2a): an application shell with a side rail (bottom bar on phone), a
  health pill and a job pill, quick search (`Ctrl K`) and keyboard shortcuts; the Overview,
  Jobs, Job detail (live log over the job's event stream, Stop for jobs of any process, Run
  again), Health and Settings (where each value comes from, locked values with the reason,
  all-or-nothing save) screens on `/api/v1`. Light, dark and system themes, compact density,
  times in local time with UTC on hover, English and Turkish. Screens that need the next API
  routes are marked "Soon". The previous views remain under `/classic` (menu
  `⋯ → Classic interface`); their old addresses redirect there or to the new screen (#107).
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
  its sport instead of "sport not set" (#112).
- **One download pipeline for match details** (`src/services/pipeline.py`): league and season
  downloads, matches picked by id, the single-match fetch (`POST /api/matches/{id}/fetch`, the
  terminal menu), refills and `--refresh-only` now all run the same code. Picked and single
  matches get the same data as league downloads, including optional slices such as tennis
  point-by-point; "only finished matches" (`FETCH_ONLY_FINISHED`) is honoured everywhere, and an
  unfinished match is reported as skipped instead of failed. Retries are the request layer's
  only (`MAX_RETRIES`, default 3) on every path; the extra per-match retries are gone. A
  single-match fetch runs under its own circuit breaker. Every run uses one warmed session and
  runs its requests concurrently, paced by the shared request budget; the terminal progress bar
  and batch lines of the details phase are gone. Empty answers are counted for optional slices
  too; a body of an unexpected shape (also 0, false or "") is a failed request on every path,
  and a missing slice (404) is recorded with the same reason everywhere. Planning: a record that
  a newer listing shows changed is refreshed first, and a record stored while live is no longer
  refilled or refreshed by downloads (the live service, or a later listing, brings it up to
  date). Changes found by downloads are announced on the `change` event stream
  (`change.recorded`) (#113).

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
- **Terminal menu: clear, restore and "move data directory" keep the data folder's index
  current.** These functions changed the files without telling the index
  (`.meta/catalog.db`), so in the same session the statistics showed the old counts and a
  match lookup could answer for files that were gone, until the program was restarted. The
  index is now rebuilt from the files after each of them, also when the operation stops
  half-way (#86).
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

### Removed

- The retired server-rendered (Jinja) UI, its static files and the `/legacy-static` mount.
- Unreachable fetcher code and the unused `AdaptiveRateLimiter`.
- Unused dependencies (`requests`, `aiohttp`, `playwright-stealth`, `jinja2`,
  `python-multipart`) and about 330 locale keys only the retired UI used.
- `scripts/catalog_tool.py` (replaced by `ssc catalog`) and `scripts/migrate_match_details.py`
  (renamed league folders in place; the new layout does not depend on folder names) (#110).

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

## Earlier history

Versions before this changelog were not tagged. The project started in March 2025 as a
terminal tool; the web interface arrived in April 2026 (reported as 1.0.0 by `/health`) and
was replaced by the Vue app on 2026-07-27, when the number became 2.0.0. See `git log` for
details.

[Unreleased]: https://github.com/tunjayoff/sofascore_scraper/commits/main
