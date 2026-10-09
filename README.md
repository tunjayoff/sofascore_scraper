# SofaScore Scraper

**English** · [Türkçe](README.tr.md)

[![CI](https://github.com/tunjayoff/sofascore_scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/tunjayoff/sofascore_scraper/actions/workflows/ci.yml)
[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

**A self-hosted data platform for SofaScore match data.** Follow leagues, teams, players or single matches in 21 sports, download their history into a local store, and take it out as CSV, JSONL, Parquet or SQLite.

One core, three faces: a **Python library**, the **`ssc` command line** for servers and automation, and a versioned **HTTP API** with a **web app** on top. It runs on your own machine or server.

> **3.1.0 is released from source.** Install it from a checkout (the Docker or pip steps below); each release on GitHub also has a source archive with the built web app, and its Docker image is `ghcr.io/tunjayoff/sofascore_scraper`. There is no package on PyPI. Coming from 3.0: [Upgrading from 3.0](#upgrading-from-30); from 2.x: [Upgrading from 2.x](#upgrading-from-2x).

Unofficial and not affiliated with SofaScore; see the [disclaimer](#disclaimer).

**Contents:** [Features](#features) · [Screenshots](#screenshots) · [Quick start](#quick-start) · [Command line](#command-line-ssc) · [HTTP API](#http-api) · [Python library](#python-library) · [Configuration](#configuration) · [Data and exports](#data-and-exports) · [Live watching](#live-watching) · [Security model](#security-model) · [Upgrading from 3.0](#upgrading-from-30) · [Upgrading from 2.x](#upgrading-from-2x) · [FAQ](#faq) · [Documentation](#documentation) · [Contributing](#contributing-and-development) · [License](#license)

## Features

- **21 sports**: football, basketball, tennis, American football, Aussie rules, ice hockey, handball, rugby, futsal, minifootball, floorball, volleyball, badminton, table tennis, padel, snooker, baseball, cricket, e-sports, darts and MMA, each with its own score shape.
- **Follows**: a league (with its seasons), a team, a player or a single match. Search SofaScore by name or give the id from the address.
- **Choose what is downloaded**: match details, statistics, line-ups, incidents, head to head, form and streaks are on by default; betting odds, standings, season data, leaders, rankings and player statistics are off until you select them, for every sport, per sport or per follow.
- **History downloads within a request budget**: 5 requests per second for all processes together by default, with a circuit breaker that stops a job when SofaScore keeps refusing.
- **Storage**: compressed raw payloads plus a rebuildable catalog (SQLite); every match status is stored, results that SofaScore corrects later are re-read and logged.
- **Exports**: normalized datasets (matches, data types, score changes, odds, standings) as CSV, JSONL, Parquet or SQLite, the raw payloads as they are, and the 2.x wide CSV. Files are named after the league, the team or player, or the dataset, and the date.
- **Backups and restore** of the data folder, from the web app or the command line.
- **Live watching** with `ssc watch`: changes of live matches go to an event log and to sinks (stdout JSON lines, a file, a webhook). The web app has no live view, by design.
- **Automation**: a non-interactive CLI with JSON output and meaningful exit codes, a declarative config file (`sofascore.toml`) with environment overrides, systemd units and a Docker image. An in-app scheduler exists and is off by default.
- **English by default, Turkish** when the system or browser language is Turkish.
- **Platforms**: Linux and Docker are supported; Windows and macOS are best effort. The web app needs Safari 16.4, Chrome 111 or Firefox 128 or newer.

## Screenshots

![Overview with real data: 804 matches stored, 41 follows in 20 sports, the connection to SofaScore, the services, the recent jobs and the latest score change](docs/images/overview.webp)

| | |
|---|---|
| ![Add a league or team: typing "premier" suggests SofaScore's leagues with their sport and country](docs/images/add-league.webp) | ![Paris Saint-Germain against Tottenham Hotspur, the 2025 UEFA Super Cup final, 2-2 and 4-3 on penalties, with its statistics](docs/images/match-statistics.webp) |
| **Add league**: suggestions while you type, or the SofaScore id, then seasons and data. | **A match**: score, statistics by period, line-ups, incidents, odds and the raw data. |
| ![New export dialog: normalized data, the match table or SofaScore's original data, in the CSV, JSONL, Parquet and SQLite formats](docs/images/export-dialog.webp) | ![The match list in the dark theme: played football matches, newest first, with the Played / Upcoming / All switch and filters by sport, tournament, date, team, data and status](docs/images/matches-dark.webp) |
| **Exports**: normalized data, the match table or the raw payloads. | **Matches** in the dark theme. |

The screenshots show real SofaScore data, downloaded on 2026-10-08, in the English UI.

## Quick start

Pick one of the first two tracks, then continue with the web app.

### Docker (Compose)

```bash
git clone https://github.com/tunjayoff/sofascore_scraper.git
cd sofascore_scraper
docker compose up -d          # web app and API on http://127.0.0.1:8000
docker compose logs -f        # the app logs to stdout
```

The image carries Python, the built web app and the headless Chromium the app needs; data, config, the browser profile and logs live in named volumes. The port is published on `127.0.0.1` only. More (volumes, a token, the live service in a container, one-shot commands): [docs/deploy/docker.md](docs/deploy/docker.md).

### pip and a virtual environment

Needs Python 3.10+ and, for the web app, Node.js 20.19+ or 22.12+.

```bash
git clone https://github.com/tunjayoff/sofascore_scraper.git
cd sofascore_scraper
python -m venv .venv
source .venv/bin/activate                            # Windows: .venv\Scripts\activate
pip install -r requirements.txt -c constraints.txt   # constraints.txt: the versions CI tests
pip install -e .                                     # the ssc command
python -m patchright install chromium --no-shell     # the browser the app reaches SofaScore through
cd frontend && npm install && npm run build && cd .. # the web app (skip it for CLI or API only)
ssc doctor                                           # checks the setup; never contacts SofaScore
ssc serve                                            # http://127.0.0.1:8000
```

The browser step is required even when Google Chrome is installed: the app starts patchright's own Chromium, headless, on desktops and servers alike. On a bare Debian or Ubuntu server, `python -m patchright install-deps chromium` adds the system libraries it needs. For Parquet exports: `pip install -e ".[parquet]"`.

Shortcuts for a desktop: `./scripts/install.sh` (Linux, macOS, Git Bash) or `scripts\install.ps1` (Windows) creates `.venv`, installs the packages and the browser and builds the web app, and `./start-sofascore.sh`, `Start SofaScore.bat` or `Start SofaScore.command` starts the app and opens the browser.

### First steps in the web app

1. Open `http://127.0.0.1:8000`. **Overview** shows a getting-started card.
2. **Add league** (top bar): choose a league, team, player or single match, search SofaScore by name (or enter the id from its address, `17` in `.../premier-league/17`), then the seasons and the data to download. **Add** starts the download unless you untick **Start downloading right away**.
3. Follow it in **Jobs** (the pill in the top bar shows the running job). Stopping a job keeps what was fetched.
4. Browse in **Matches** (filters by sport, tournament, date, team, status and stored data) and open a match for its statistics, line-ups, incidents and raw data.
5. Take data out in **Exports** → **New export**, and keep copies in **Backups**.

**Health** shows the connection to SofaScore and the request budget; **Help** (the ⋯ menu or the ? at the bottom of the side menu) explains the words used in the app.

## Command line (`ssc`)

For servers, scripts and agents. It never asks questions: results go to stdout, logs and errors to stderr, and the exit code says what happened. Without `pip install -e .`, use `python -m sofascore_scraper.cli.main <command>` in the project folder.

| Command | What it does |
|---|---|
| `ssc doctor` | Checks Python, packages, the browser, folders, the web app build and `.env` |
| `ssc serve` | Runs the web app and the HTTP API (`--host`, `--port`, `--scheduler`) |
| `ssc follows add tournament 17 --name "Premier League" --sport football --seasons last:2` | Follows a league (also `team`, `player`, `event`); `follows list`, `follows remove` |
| `ssc sync` | Brings every enabled follow up to date: season lists, schedules, then match details |
| `ssc sync --dry-run` | Shows what would be fetched and at least how many requests; sends nothing |
| `ssc fetch event 12345678` | Fetches given matches (or `fetch tournament ID --season ID`), followed or not |
| `ssc status` | Data summary, store health, the running and last job, the live service |
| `ssc export --dataset events --format csv --out events.csv` | Writes a dataset (`events`, `slices`, `changes`, `odds`, `standings`) |
| `ssc backup create` | Backs up the data folder; `backup list`, `backup verify NAME`, `backup restore NAME --yes` |
| `ssc watch --stdout` | Watches the followed live matches and prints their changes as JSON lines |

`ssc --help` and `ssc <command> --help` describe every command and option; `ssc describe` prints the sports, data types, commands, schemas, config keys, error codes and exit codes as JSON for programs. Other commands: `refresh`, `jobs`, `events`, `data`, `config`, `catalog`, `migrate`, `diagnostics`, `version`.

- **JSON output:** `--json` prints one document (`{"ok", "command", "schema", "version", "data" | "error"}`); streams (`ssc events`, `ssc jobs tail`) print one JSON object per line.
- **Exit codes:** `0` success or nothing to do · `1` general error · `2` usage or configuration error · `3` partial success · `4` SofaScore is blocking (the circuit breaker stopped the job) · `5` storage error · `6` another process holds the data folder · `130` / `143` cancelled by Ctrl+C / SIGTERM.
- **One writer per data folder:** a second download exits with `6` and names the holder; `--wait SECONDS` waits instead.
- **As a service:** systemd units for `ssc serve`, `ssc watch` and a timer for `ssc sync` are in [docs/deploy](docs/deploy/README.md).

## HTTP API

`ssc serve` serves the versioned API under `/api/v1`, and the web app uses nothing else. The contract is [docs/api/openapi-v1.json](docs/api/openapi-v1.json); the running server also documents it at `/docs` and `/redoc`. It covers the sports and their data types, follows and the SofaScore search, tournaments, seasons, events with their data slices, odds and standings, raw payloads, score changes, jobs (with progress as server-sent events), exports, backups, settings, logs, diagnostics and status. A resource comes back as `{"data": ...}`, a list with `"page": {"limit", "next_cursor"}`, an error as `{"error": {"code", "message", "details", "request_id"}}`. There is no live endpoint: live data reaches other programs through a webhook sink.

```bash
curl "http://127.0.0.1:8000/api/v1/events?tournament=17&limit=5"
```

## Python library

The CLI and the API are thin layers over the same services, which a Python program can use directly. There is no dedicated library entry point yet; the import package is `sofascore_scraper` (it was `src` before 3.0.0). Reading the stored matches looks like this:

```python
from sofascore_scraper.services.query import EventFilter, QueryService
from sofascore_scraper.store import open_store

store = open_store("data")               # the data folder
query = QueryService(store)
for event in query.events(EventFilter(tournament_ids=(17,)), limit=5).items:
    home, away = event.participants.home, event.participants.away
    print(event.start_utc, home.name, event.score.home, "-", event.score.away, away.name)
store.close()
```

Records follow the data schema v1 ([docs/design/04-schema-v1.md](docs/design/04-schema-v1.md)). Run it from the project folder or after `pip install -e .`.

## Configuration

Everything works without a config file. For a server, write the setup into `sofascore.toml` (in the project folder or in `config/`, or given with `--config` / `SOFASCORE_CONFIG`):

```toml
[client]
rate = 5                  # requests per second, all processes together

[defaults]
slices = ["core"]         # the default data types; add "odds", "standings", ...

[[follow]]
tournament = 17
name = "Premier League"
sport = "football"
seasons = "last:2"
live = true               # watched by ssc watch

[[follow]]
team = 42
name = "Arsenal"
sport = "football"
```

```bash
ssc config validate     # checks the file and the environment; exit code 2 if not valid
ssc config show         # every setting, its value and where it comes from (secrets masked)
ssc config init         # prints a commented starter file
ssc describe config     # every section and key as JSON Schema
```

- **Layers**, the later one wins: built-in default → `config/overrides.json` (what the web app's **Settings** page saves) → `sofascore.toml` → environment variables (`.env` is loaded into the environment) → command-line flags. A value fixed by the file, the environment or a flag shows as locked on the Settings page.
- **Environment overrides**: every key as `SOFASCORE_<SECTION>__<KEY>`, for example `SOFASCORE_CLIENT__RATE=2` or `SOFASCORE_LIVE__SOURCE=poll` (`.env.example` lists them). The variable names of 2.x (`DATA_DIR`, `REQUEST_RATE_LIMIT`, `APP_LANGUAGE`, …) are not read since 3.1.0; four of them (`SOFASCORE_API_TOKEN`, `SOFASCORE_ALLOWED_HOSTS`, `USE_PROXY`, `PROXY_URL`) are deprecated and still read until 3.2; `ssc doctor` and `ssc config show` name the new name of each one still set.
- **Secrets** come from the environment only: the access token `SOFASCORE_SERVER__TOKEN` and webhook secrets (`secret_env` names the variable).
- **Language**: `SOFASCORE_DISPLAY__LANGUAGE=en|tr` (or `[display] language`) pins it; otherwise Turkish systems and browsers get Turkish and everyone else English. `--lang` sets it for one command; JSON output is never translated.

## Data and exports

All data lives in one folder, `data/` by default (`[storage] data_dir`, `SOFASCORE_STORAGE__DATA_DIR`, `--data-dir`):

```text
data/
├── .meta/catalog.db   # the index of the stored files; rebuildable (ssc catalog rebuild)
├── .meta/state.db     # follows added in the app, job history, event log
├── v3/                # compressed payloads: one folder per match, seasons, teams, players
├── changes/           # results SofaScore changed after the finish
├── exports/           # files written by exports
└── backups/           # backups
```

- **Data types** (`ssc describe slices`): `core` (statistics, line-ups, incidents, head to head, form, streaks, and per sport point by point, innings or e-sports games) is on by default. `odds`, `standings`, `season`, `leaders`, `rankings` and `players` are selected in **Settings → Data**, on a follow's page, in `[defaults] slices`, `[slices.<sport>]` or a follow's `slices`. What is not selected is never requested.
- **Every status is stored** (scheduled, live, finished, cancelled); a match is re-read while its result can still change (72 hours after kick-off by default), and each correction is logged.
- **Exports** from the web app's **Exports**, the API or the CLI:

```bash
ssc export --dataset events --format parquet --tournament 17 --out pl.parquet   # needs pyarrow
ssc export --dataset slices --format sqlite --out slices.sqlite
ssc export --dataset events --format csv --team 3071 --player 822471 --out mine.csv   # a team's or a player's matches
ssc export --schema raw --format jsonl --out raw.jsonl     # the stored SofaScore payloads
ssc export --out matches.csv                               # the 2.x wide CSV
```

- **Backups** contain the follows, the job history, the event log, the stored data and the settings saved on the **Settings** page (`config/overrides.json`; it can hold the proxy password, so such a backup is readable by its owner only); `.env` only with `--include-secrets`. **Restore** (web app **Backups**, or `ssc backup restore NAME --yes`) checks the backup, then replaces the data folder; the saved settings come back with it (readable by the owner only) and are reloaded. A backup without them leaves the current settings alone; backups made by 2.x restore too. Schedules for backups and downloads: [docs/deploy](docs/deploy/README.md#scheduled-downloads).

## Live watching

`ssc watch` is the live service: a foreground process that follows live matches and writes their changes (`live.status_changed`, `live.score_changed`, `live.stuck`) to the event log and to the configured sinks. It is not part of the web app. With no follow marked live it exits with code 2; `ssc watch --idle` (used by the Compose example and the systemd unit) waits instead and reads the follows again every 60 seconds. In set sports (tennis, table tennis, volleyball, badminton, padel) a `live.score_changed` is a set won, not a game or a point inside a set.

```bash
ssc watch                                                    # follows marked live
ssc watch --sport football --tournament 17 --stdout          # every live match of a league
ssc events --follow --type 'live.*'                          # read the event log as it grows
```

| Source | How it works | Memory |
|---|---|---|
| `page` (default) | keeps one browser page per watched sport open and listens to the push connection that SofaScore's own page opens | about 1.8 to 2.6 GB per sport |
| `poll` | polling only (every 30 s), no browser | nothing extra |
| `direct` (explicit opt-in) | a light client connects to the push server itself with the credential read from the page's own connection, kept in memory only | about 0.2 GB |

Polling is always the fallback. **`direct` is never chosen for you**: only `--source direct`, `[live] source = "direct"` or `SOFASCORE_LIVE__SOURCE=direct` select it. Before you choose it, know that:

1. it uses SofaScore's own client credential outside the site's client;
2. it may break without notice when the credential or the server changes;
3. it may get your IP address blocked;
4. it is a terms-of-use grey area that you choose knowingly.

Sinks are `[[sink]]` tables in `sofascore.toml` (`stdout`, `file`, or `webhook` with an HMAC signature). Running the service under systemd or Docker, with the memory each source needs: [docs/deploy/watch.md](docs/deploy/watch.md).

## Security model

The web app has **no user accounts** and listens on `127.0.0.1` by default. Opening it to a network is the installer's decision and responsibility:

- set `SOFASCORE_SERVER__TOKEN` to a long random value (every `/api` request then needs `Authorization: Bearer <token>`, or the web app's session cookie);
- list the names it is reached by in `SOFASCORE_SERVER__ALLOWED_HOSTS` (`ssc serve --host 0.0.0.0` refuses to start without it);
- put a firewall or VPN and TLS (a reverse proxy) in front of it.

The app itself answers only to allowed host names, refuses state-changing requests sent by other sites, sends a strict Content-Security-Policy, warns when it is exposed without a token, and keeps `.env`, the settings file and the browser profile readable by their owner only. Details: [docs/deploy](docs/deploy/README.md#access-token).

## Upgrading from 3.0

```bash
git pull
pip install -r requirements.txt -c constraints.txt
pip install -e .
cd frontend && npm install && npm run build && cd ..
ssc doctor                # names every 2.x setting name that is still set
```

With Docker Compose: `git pull`, then `docker compose pull` (or `docker compose build`) and `docker compose up -d`.

- **Rename the 2.x environment names.** 3.1.0 no longer reads `DATA_DIR`, `REQUEST_RATE_LIMIT`, `APP_LANGUAGE`, `LOG_LEVEL` and the other 2.x names, wherever they are set (`.env`, the shell, a service file, the container settings): the default applies instead, so an old `DATA_DIR` leaves the app on the default data folder. Four of them are deprecated instead: `SOFASCORE_API_TOKEN`, `SOFASCORE_ALLOWED_HOSTS`, `USE_PROXY` and `PROXY_URL` still work in 3.1, with a warning, and are removed in 3.2; when the new name is set too, the new one wins. Use `SOFASCORE_<SECTION>__<KEY>` (`SOFASCORE_STORAGE__DATA_DIR`, `SOFASCORE_SERVER__TOKEN`, `SOFASCORE_SERVER__ALLOWED_HOSTS`, `SOFASCORE_CLIENT__PROXY`, …). `ssc doctor` and `ssc config show` list each old name still set with its new name and where it is set; `ssc config init --from-legacy > sofascore.toml` writes the old settings as a config file.
- **The catalog is rebuilt on its first open** (catalog schema 2): `catalog.db` is rebuilt once from the stored files, without a request to SofaScore, so the first start on a large data folder takes longer.
- **Removed**: the `main.py` flags (an old flag is a usage error that names the `ssc` command replacing it), the 2.x routes under `/api/...` (they answer 404; use `/api/v1`) and the backup scopes `config`, `seasons`, `matches` and `match_details` (use `all`, `state` or `data`; old backups still restore). A web app built before 3.0.0 does not work under the strict Content-Security-Policy: build it again.
- **`ssc export` file names**: without `--out`, a file is named like a web export (`exports/premier-league_2026-10-09_142530.jsonl`), and the wide CSV is `match_details/processed/events-wide_<date>_<time>.csv` instead of `all_matches_<epoch>.csv`.

The full list is in [CHANGELOG.md](CHANGELOG.md#310---2026-10-09).

## Upgrading from 2.x

```bash
git pull
pip install -r requirements.txt -c constraints.txt
pip install -e .          # again after every update: the import package is now sofascore_scraper
cd frontend && npm install && npm run build && cd ..
ssc migrate --dry-run     # optional: what would move to the new layout
```

- **Data**: nothing is moved on its own. Old data is read where it is; new writes use the new layout. `ssc migrate` converts and verifies the old folders and keeps them; `ssc migrate --delete-legacy --yes` removes verified old copies later.
- **The terminal menu is gone.** `python main.py` without arguments prints a short help and exits with `2`. Use the web app, or `ssc` for scripts.
- **The import package is `sofascore_scraper`** (it was `src`, and there is no alias): your own systemd units and scripts run `python -m sofascore_scraper.cli.main` instead of `python -m src.cli.main`, and library code imports `sofascore_scraper`.
- **Removed in 3.1.0** (deprecated in 3.0.0): the `main.py` flags (a usage error names the command that replaces each one: `--headless --update-all` is `ssc sync`, `--refresh-only` is `ssc refresh`, `--watch` is `ssc watch --source poll --stdout`, `--web` is `ssc serve`, …), the 2.x routes under `/api/...` (use `/api/v1`), the backup scopes `config`, `seasons`, `matches` and `match_details` (use `all`, `state` or `data`; old backups still restore) and the 2.x environment names (four of them are deprecated and still read until 3.2; see [Upgrading from 3.0](#upgrading-from-30)). Exit codes follow the new table (a breaker stop is `4`, no longer `2`).
- **Settings**: rename the 2.x names in `.env` (`ssc doctor` lists each one with its new name), or run `ssc config init --from-legacy > sofascore.toml`, which writes the old `.env` and `config/leagues.txt` as a config file. Leagues in `config/leagues.txt` are still downloaded; **Move to here** on a league's page moves one into the app.

The full list of changes is in [CHANGELOG.md](CHANGELOG.md).

## FAQ

**Is this an official SofaScore API?**
No. It reads the public data the SofaScore website shows without login, through a browser, as the site does. It is not affiliated with or endorsed by SofaScore. Keep the request budget low and follow SofaScore's terms and the law where you are.

**Why does it need Chromium?**
SofaScore refuses plain HTTP clients, so the app sends its requests from a headless browser page (patchright's Chromium) and solves the site's challenge there. An installed Google Chrome is not used. `ssc doctor` checks that the browser is installed and starts.

**How long does a download take?**
With the default data types a football match costs 7 requests, so a 380-match season is about 2,700 requests, roughly 9 minutes at the default 5 requests per second. Raising the budget (`--rate`, **Settings → Requests**) is faster and makes a block more likely.

**Can I see live scores in the web app?**
No, by design: the web app shows stored data. Live changes are for programs: `ssc watch` with a webhook, file or stdout sink. To watch a match yourself, use SofaScore.

**A season downloads 0 matches. Why?**
Usually the newest season has only fixtures, and match details are downloaded once a match has finished. Pick the previous season, or wait. If SofaScore renumbered the season, `ssc sync --tournament ID --only seasons` reads the season list again.

**Downloads fail or stop with exit code 4. What now?**
SofaScore is refusing requests and the circuit breaker stopped the job. Look at **Health** (or `ssc status`), lower the request budget, and try later. For a bug report, attach the bundle from `ssc diagnostics` or the **Logs** page; secrets are masked, but read it before you send it.

**Can I run it on a NAS or a server and use it from my laptop?**
Yes, with Docker or the systemd units. Set the access token and the allowed host names first, and put TLS in front of it ([Security model](#security-model), [docs/deploy](docs/deploy/README.md)).

## Documentation

| Where | What |
|---|---|
| `ssc --help`, `ssc describe` | Every command, option, data type, config key, error code and exit code |
| [docs/deploy/README.md](docs/deploy/README.md) | Servers: systemd, access token, reverse proxy, scheduled downloads, backups, `ssc migrate` |
| [docs/deploy/docker.md](docs/deploy/docker.md) | The Docker image and the Compose file |
| [docs/deploy/watch.md](docs/deploy/watch.md) | The live service as a service, its sources and memory |
| [docs/api/openapi-v1.json](docs/api/openapi-v1.json) | The HTTP API contract (also `/docs` on a running server) |
| [docs/design/04-schema-v1.md](docs/design/04-schema-v1.md) | The data schema of the API and the exports, field by field |
| [docs/design/README.md](docs/design/README.md) | The design of the 3.0.0 platform |
| [frontend/README.md](frontend/README.md) | The web app's code |
| [CHANGELOG.md](CHANGELOG.md) | Changes per version |

## Contributing and development

Issues and pull requests are welcome; for a larger change, open an issue first. Keep pull requests focused, and update both languages when you change user-visible text (`frontend/src/locales/` for the web app, `locales/en.json` and `locales/tr.json` for the command line, both READMEs).

```bash
pip install -r requirements-dev.txt -c constraints.txt
ruff check .
python -m pytest -q                     # never contacts SofaScore; live tests are opt-in (-m live)
ssc serve --dev                         # reloads on code changes
cd frontend && npm run dev              # http://localhost:5173, proxies /api to 127.0.0.1:8000
```

CI runs ruff and the Python tests (Linux with Python 3.10 and 3.14; Windows and macOS best effort), and the web app's lint, tests and build. The version is set in one place, `pyproject.toml`.

**Releasing** (maintainer): set the version in `pyproject.toml`, rename `## [Unreleased]` in `CHANGELOG.md` to the version and date, check with `python scripts/release.py check-tag vX.Y.Z` and `python scripts/release.py notes`, then push the tag `vX.Y.Z`. The release workflow tests, builds and publishes the Docker image to `ghcr.io/tunjayoff/sofascore_scraper` and the GitHub release with the built web app.

By submitting a contribution, you agree that it is licensed under the project's license and that the maintainer may also offer it under other terms (for example, a commercial license).

## License

[PolyForm Noncommercial 1.0.0](LICENSE): use, change and share it for **noncommercial purposes** (personal use, study, research, hobby projects, charities, education, public bodies). Commercial use needs a separate license; ask in an issue or contact [@tunjayoff](https://github.com/tunjayoff). Versions released before this license remain available under the MIT license.

## Disclaimer

This is an unofficial tool, not affiliated with, endorsed by or connected to SofaScore. It reads data that the SofaScore website shows publicly without login and stores no login data. You are responsible for how you use it: respect SofaScore's terms of use, keep the default request budget unless you accept the risk of being blocked, and do not redistribute data you have no right to share.
