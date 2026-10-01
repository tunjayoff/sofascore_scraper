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
- `GET /api/sports` lists the supported sports and, for each, the match-detail slices
  requested for it (#18).
- `main.py --web` options `--host`, `--port` and `--dev`.
- `main.py --version`; the version is also reported by `GET /health` and shown on the
  Settings page. `pyproject.toml` is the single place it is written.
- **Docker image** (`Dockerfile`, `docker-compose.yml`): the web app together with the
  headless browser it needs, running as a non-root user, with data, configuration and the
  browser profile in volumes.
- **Release workflow** (`.github/workflows/release.yml`): pushing a `v*` tag runs the CI
  checks, pushes the Docker image to GHCR and publishes a GitHub Release with an archive of
  the source plus the built web app.
- This changelog.
- `config/leagues.example.txt`, copied to `config/leagues.txt` on the first run.
- `SOFASCORE_CONFIG_DIR` and `SOFASCORE_ENV_FILE` to move the config folder and the `.env`
  file.
- Continuous integration (GitHub Actions): frontend lint, tests and build; `ruff` and
  `pytest` on Python 3.10 and 3.14. The test suite runs offline against a temporary data
  set and never touches real data, configuration or `.env`.
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
- Installers run `playwright install chromium` and point to the launcher that builds the
  web app.

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
- Proxy credentials and captcha tokens are masked in logs; 500 responses no longer echo
  exception text.
- `npm audit fix` for a transitive frontend dependency (nanoid, #15).

## Earlier history

Versions before this changelog were not tagged. The project started in March 2025 as a
terminal tool; the web interface arrived in April 2026 (reported as 1.0.0 by `/health`) and
was replaced by the Vue app on 2026-07-27, when the number became 2.0.0. See `git log` for
details.

[Unreleased]: https://github.com/tunjayoff/sofascore_scraper/commits/main
