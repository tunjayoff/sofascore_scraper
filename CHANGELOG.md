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

- **Basketball and tennis.** League search, fixtures and match details for all three sports.
  A league's sport is stored in `config/league_sports.json`, and the web app has a sport
  switch that filters every page.
- **BrowserBridge.** SofaScore answers plain HTTP clients with `403 challenge`, so API
  requests are now made from inside a headless Chromium (Scrapling `StealthySession`) that
  passes the Cloudflare Turnstile challenge on its own. It runs headless on a desktop and on
  a display-less server alike. `SOFASCORE_BROWSER_HEADED=1` shows the window,
  `SOFASCORE_BROWSER_PROFILE` moves the browser profile. After a challenge, requests go
  straight to the browser for 10 minutes ("browser-first" mode).
- **Web app rebuilt** around Leagues, Download, Matches, Match, Activity and Settings pages,
  with dark mode, Turkish and English text, error toasts and a job card with a Stop button.
  The first visit follows the server's `APP_LANGUAGE`.
- **Job progress** with phases (seasons, matches, details, export), job-wide counters, the
  list of failed matches, an ETA and SofaScore wait countdowns (#10).
- **Status classification.** `classify_status` separates played, decided-without-play and
  void matches; `extract_scores` reads scores per sport without the ambiguous fields; each
  saved match gets an `observation.json` with the time it was read and SofaScore's change
  timestamp. `docs/settlement-notes.md` describes how to use them (#9).
- **Refresh policy.** A finished match stays provisional for `REFRESH_WINDOW_HOURS` (default
  72) after kick-off and is re-read by later downloads, at most once every
  `REFRESH_MIN_INTERVAL_HOURS` (default 6). Changes are appended to
  `DATA_DIR/score_changes.jsonl`. `main.py --refresh-only` runs refreshes alone and
  `--refresh-legacy` also covers records saved before `observation.json` existed (#11).
- **Watch mode.** `main.py --watch --sport … --league-ids …|--event-ids …` follows live
  matches and writes status, score and stuck-match events to `DATA_DIR/watch_events.jsonl`
  (#12).
- **Shared request budget.** `REQUEST_RATE_LIMIT` is one requests-per-second budget for all
  processes on the machine together (web app, CLI, every `--watch`, `--refresh-only`),
  whether a request goes through curl or through the browser. The default is
  `10 × MAX_CONCURRENT` (100 with default settings), which does not slow down a single bulk
  download; `0` turns it off. It is also on the Settings page (Advanced) and in
  `/api/settings`. The state is a small locked file under
  `~/.cache/sofascore_scraper/throttle/`; `SOFASCORE_THROTTLE_DIR` moves it (#19).
- **Bridge health.** The browser bridge reports whether SofaScore is answering: `ok`,
  `degraded` after `BRIDGE_DEGRADED_AFTER` (default 3) failed requests in a row, `blocked`
  after `BRIDGE_BLOCKED_AFTER` (10) that span at least `BRIDGE_BLOCKED_MIN_SECONDS` (200).
  It is in `GET /health` (`bridge`, next to a new `throttle` block) and
  `GET /api/bypass/status` (`health`), in a dismissible banner in the web app, and in the
  terminal modes as one line on stderr per state change; the log gets one warning per state
  change (#19).
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
- `_slice_status.json` in match folders: the last failed request per slice and how many
  "not available" counts were confirmed by a definitive answer (#25).
- `GET /api/sports` lists the supported sports and, for each, the match-detail slices
  requested for it (#18).
- `main.py --web` options `--host`, `--port` and `--dev`.
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
  `pytest` on Linux with Python 3.10 and 3.14, with a coverage floor (53%); the BrowserBridge
  run against a local fake site with a real Chromium; and `pytest` on Windows and macOS with
  Python 3.14 as best-effort jobs that report but do not block. The test suite runs offline
  against a temporary data set and never touches real data, configuration or `.env` (#22).
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

### Changed

- **Licence: MIT → [PolyForm Noncommercial 1.0.0](LICENSE).** Commercial use now needs a
  separate licence. Versions published before this change remain available under MIT.
- `main.py --web` listens on `127.0.0.1` only (it used to bind `0.0.0.0` with reload on).
  Opening it to the network is an explicit `--host`, and logs a warning.
- The interface language variable is `APP_LANGUAGE`. `LANGUAGE` clashed with GNU gettext;
  a legacy `LANGUAGE` value is only honoured when it is `tr` or `en`.
- `config/leagues.txt` and `config/league_sports.json` are user state and no longer tracked
  in git.
- The CLI and the web app use the same data folder (`DATA_DIR`); `--data-dir` only overrides
  it when given.
- Backups are written to `DATA_DIR/backups`, downloaded through
  `/api/data/backups/{name}`, and leave `.env` out unless `include_env=true`.
- `GET /api/matches` reads the per-season summaries (the export CSV is only a fallback),
  accepts several league ids and can filter on whether details are present.
- The request layer raises typed errors (`APIError`, `RateLimitError`, `NetworkError`,
  `ResourceNotFoundError`) instead of returning `None`, does not sleep after the final
  attempt, does not retry permanent 4xx responses, and really caps in-flight requests at
  `MAX_CONCURRENT`.
- Watch mode: the 1 s spacing between requests is shared by every `--watch` process on the
  machine instead of applying to each process, so one watcher per sport stays at 1 request/s
  in total. With `REQUEST_RATE_LIMIT=0` it applies per process as before (#19).
- Round files store the raw payload; a round with unfinished matches is fetched again once
  it is older than 6 hours.
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
- Matches without a SofaScore unique-tournament id are saved under
  `match_details/_no_tournament/<sport>/<match id>/`. Existing records are not moved (#25).
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

### Fixed

- Results that arrived after a round was first saved never reached the summary or got
  details; seasons that already had a summary were never updated.
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
- **Failed slice requests are no longer recorded as "not available".** Only a definitive
  answer (HTTP 404, or a 200 with no data) counts toward `_unavailable.json`. A request that
  failed with 403, 429, 5xx, a timeout, a network error or an unreadable response is recorded
  in `_slice_status.json` with its reason and time, the match stays incomplete, and the next
  download retries it (#25).
- **A match that could not be written to disk is reported as failed**, not as downloaded. A
  full disk, an exhausted quota, a permission error or a read-only file system stops the job
  with a message naming the path and the reason (#25).
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
  its own state file.
- `Start SofaScore.bat` could not find the launcher script; `install.ps1` failed on a
  parameter named `$Args`.
- Windows: when a file cannot be replaced because another process or thread has it open,
  the write is retried (up to 10 times, 20 ms apart) instead of failing at the first attempt.
- An unanchored `lib/` rule in `.gitignore` kept `frontend/src/lib/` out of the repository,
  so a fresh clone could not build the web app.

### Removed

- The retired server-rendered (Jinja) UI, its static files and the `/legacy-static` mount.
- Unreachable fetcher code and the unused `AdaptiveRateLimiter`.
- Unused dependencies (`requests`, `aiohttp`, `playwright-stealth`, `jinja2`,
  `python-multipart`) and about 330 locale keys only the retired UI used.

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
- `npm audit fix` for a transitive frontend dependency (nanoid, #15).

## Earlier history

Versions before this changelog were not tagged. The project started in March 2025 as a
terminal tool; the web interface arrived in April 2026 (reported as 1.0.0 by `/health`) and
was replaced by the Vue app on 2026-07-27, when the number became 2.0.0. See `git log` for
details.

[Unreleased]: https://github.com/tunjayoff/sofascore_scraper/commits/main
