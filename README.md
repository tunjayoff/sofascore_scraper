# SofaScore Scraper

[![CI](https://github.com/tunjayoff/sofascore_scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/tunjayoff/sofascore_scraper/actions/workflows/ci.yml)

**Türkçe:** [README.tr.md](README.tr.md)

Python tool to download football, basketball and tennis match data from [SofaScore](https://www.sofascore.com/) public HTTP APIs, store it locally (JSON and CSV), and browse it through a web app or a terminal UI.

This project is not affiliated with SofaScore. Use reasonable request rates and comply with applicable terms and laws.

## Features

- **Three sports** — Football, basketball and tennis. Every league remembers its sport, and the whole web app can be switched to one sport at a time.
- **Leagues** — Add tournaments by searching SofaScore (web) or by ID (`config/leagues.txt`).
- **Seasons & matches** — Pick seasons from one or several leagues and download them in one go; browse matches by league, season, date and whether details are downloaded.
- **Match details** — Statistics (per period), incidents, lineups, H2H and form; score lines per half, quarter or set depending on the sport.
- **Web app** — Leagues, Download, Matches, Activity and Settings pages; live progress (SSE) with a Stop that takes effect immediately; English and Turkish, following your browser's language on the first visit; light, dark or system theme.
- **Terminal UI** — Interactive menu for the same operations without the browser.
- **Automation** — Headless flags for CI/scripts (`--update-all`, `--fetch-mode`, `--league-id`, `--csv-export`, paths).
- **Export** — Processed “all matches” CSV and API export endpoints.

## Screenshots

The web app with two football leagues downloaded. The images follow your GitHub theme (light or dark).

**Leagues — followed leagues with downloaded match counts and detail coverage**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/leagues-en-dark.png">
  <img src="docs/screenshots/leagues-en-light.png" alt="Leagues page">
</picture>

**Download — pick seasons from several leagues into one download list**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/download-en-dark.png">
  <img src="docs/screenshots/download-en-light.png" alt="Download page">
</picture>

**Matches — filter by league, season, date and details**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/matches-en-dark.png">
  <img src="docs/screenshots/matches-en-light.png" alt="Matches page">
</picture>

**Match — score by period, key moments, form and head to head**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/match-en-dark.png">
  <img src="docs/screenshots/match-en-light.png" alt="Match overview">
</picture>

**Match statistics — whole match or per period**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/match-stats-en-dark.png">
  <img src="docs/screenshots/match-stats-en-light.png" alt="Match statistics">
</picture>

## Requirements

- Python **3.10+** (3.11+ recommended).
- **Platforms:** Linux and Docker are the officially supported platforms; Windows and macOS are best-effort (the installers, launchers and CI cover them, but a problem that only occurs there does not block a release).
- **Chromium for patchright** — the browser the app reaches SofaScore through. A one-time download made by the install scripts and by the launcher (`python -m patchright install chromium --no-shell`). A Google Chrome or Chromium already on the machine is **not** used.
- **Node.js 20.19+ or 22.12+ and npm** — to build the web app (`frontend/`). The install scripts and `scripts/start_web.py` build it when Node.js is installed. Without it the terminal modes still work, and the web address shows a help page instead of the app.
- **Git** — required for the one-line `curl | bash` installer (clones this repo); optional if you already extracted or cloned the project manually.
- Network access to SofaScore.

With [Docker](#docker) you need none of these on the host: the image carries Python, the built web app and the browser.

## Installation

Pick one:

| Way | You need | Best for |
|-----|----------|----------|
| [Install script](#quick-install-script) | Git, Python 3.10+, Node.js | A desktop: double-click launcher, terminal UI, easy updates with `git pull` |
| [Docker](#docker) | Docker | A server or NAS, or keeping Python and the browser off the host |
| [Release archive](#release-archive) | Python 3.10+ | A fixed version without Git or Node.js |
| [Manual install](#manual-install) | Git, Python 3.10+, Node.js | Development |

`python main.py --version` (or `GET /health`, or the bottom of the **Settings** page) tells you which version is running. Changes between versions are listed in [CHANGELOG.md](CHANGELOG.md).

### Quick install (script)

Official repository: [github.com/tunjayoff/sofascore_scraper](https://github.com/tunjayoff/sofascore_scraper).

**Linux / macOS / Git Bash**

Already cloned:

```bash
chmod +x scripts/install.sh   # once
./scripts/install.sh
```

**One-liner** (clones [tunjayoff/sofascore_scraper](https://github.com/tunjayoff/sofascore_scraper), creates `.venv`, installs the dependencies and the browser, builds the web app if Node.js is installed, copies `.env`, then runs the [setup check](#check-your-setup-doctor)):

```bash
curl -fsSL https://raw.githubusercontent.com/tunjayoff/sofascore_scraper/main/scripts/install.sh | bash
```

Prefer to read a script before running it? Download it first:

```bash
curl -fsSLo install.sh https://raw.githubusercontent.com/tunjayoff/sofascore_scraper/main/scripts/install.sh
less install.sh && bash install.sh
```

- Optional **first argument**: target folder name (default `sofascore_scraper`), or set `SOFASCORE_SCRAPER_DIR`.
- To use another fork as default clone source: `export SOFASCORE_SCRAPER_REPO=https://github.com/YOU/fork.git` before `curl | bash`, or pass a **full git URL** as the first argument to `bash -s`:  
  `curl ... | bash -s -- https://github.com/YOU/fork.git [folder]`
- Override the built-in default URL only if needed: `SOFASCORE_SCRAPER_DEFAULT_REPO`.

**Windows** — PowerShell (clone is automatic if you are not already inside the repo):

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   # if needed, once
Invoke-RestMethod https://raw.githubusercontent.com/tunjayoff/sofascore_scraper/main/scripts/install.ps1 | Invoke-Expression
```

Or after cloning:

```powershell
.\scripts\install.ps1
```

Explicit clone URL / folder:

```powershell
.\scripts\install.ps1 -RepoUrl https://github.com/tunjayoff/sofascore_scraper.git -InstallDir sofascore_scraper
```

From CMD: `scripts\install.bat`. Environment overrides: `SOFASCORE_SCRAPER_REPO`, `SOFASCORE_SCRAPER_DIR`, `SOFASCORE_SCRAPER_DEFAULT_REPO`.

**Prerequisites:** **Git** (for the one-liner / clone path), **Python 3.10+** on `PATH`. The scripts print clear errors if `git`, `python`, `venv`, or `pip install` fails (e.g. missing `python3-venv` on Debian/Ubuntu). A missing or too old Node.js is reported and is not fatal. The script ends with the setup check and exits with 1 if something the app needs is missing.

### Docker

The image contains the app, the built web app and the headless Chromium that [BrowserBridge](#anti-bot-protection-browserbridge) needs. It runs as a non-root user (uid 1000) and starts the web app by default.

```bash
docker run -d --name sofascore-scraper --shm-size=1g \
  -p 127.0.0.1:8000:8000 \
  -v sofascore-data:/app/data \
  -v sofascore-config:/app/config \
  -v sofascore-browser:/app/browser-profile \
  ghcr.io/tunjayoff/sofascore_scraper:latest
```

Then open `http://127.0.0.1:8000`. With Compose, the repository's [`docker-compose.yml`](docker-compose.yml) does the same:

```bash
docker compose up -d
docker compose logs -f      # the app logs to stdout
```

Images are published to `ghcr.io/tunjayoff/sofascore_scraper` with each tagged release (`latest`, `X.Y.Z`, `X.Y`). If there is no release yet, or you want the current `main`, build the image from a checkout: `docker build -t sofascore-scraper .` (then use `sofascore-scraper` as the image name), or `docker compose up -d --build`. `docker/smoke-test.sh sofascore-scraper` checks a built image without any network access.

| Volume | Holds |
|--------|-------|
| `/app/data` | Everything downloaded (`DATA_DIR`): seasons, matches, details, backups, job history |
| `/app/config` | `leagues.txt`, `league_sports.json` and `.env` (settings saved on the **Settings** page) |
| `/app/browser-profile` | The browser profile with the solved challenge; keeping it makes restarts fast |
| `/app/logs` | The log file (`sofascore_scraper.log`, rotated, about 30 MB at most). The same lines go to stdout (`docker logs`). See [Logs and diagnostics](#logs-and-diagnostics) |

Things to know:

- **There are no user accounts.** The examples publish the port on `127.0.0.1` only. If you publish it to your network (`-p 8000:8000`), set an access token (`-e SOFASCORE_API_TOKEN=<long random value>`): without it anyone who can reach the port can read and delete data and change settings, and the container cannot warn you about it (inside the container the app always listens on every interface). You must also list the name or IP you open it with: `-e SOFASCORE_ALLOWED_HOSTS=localhost,127.0.0.1,my-server.lan` (requests with any other `Host` header are rejected). See [Security model](#security-model).
- **Settings:** every variable in `.env.example` can be passed with `-e` / `environment:`. On every start a variable set that way wins over the value saved on the Settings page, so only set the ones you want fixed (for example `APP_LANGUAGE=en`, `USE_PROXY` / `PROXY_URL`). `PORT` changes the port inside the container.
- **Shared memory:** Chromium needs more than Docker's 64 MB default, hence `--shm-size=1g` (`shm_size` in Compose).
- **Bind mounts** (`-v ./data:/app/data`) work when the folder is writable by uid 1000: `mkdir -p data config && sudo chown -R 1000:1000 data config`. To use another uid, build with `--build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)`.
- **Other commands:** arguments after the image name go to `main.py`, for example `docker run --rm ghcr.io/tunjayoff/sofascore_scraper:latest --version`, or a scheduled download with the same volumes: `docker compose run --rm sofascore-scraper --headless --update-all`. The browser profile can be used by one container at a time, so stop the web container (`docker compose stop`) before running a download this way; a second container on a busy profile logs a warning and cannot open its browser.
- **Updating:** `docker compose pull && docker compose up -d`. Data, configuration, the browser profile and the log files stay in their volumes.

### Release archive

Each tagged release on the [Releases page](https://github.com/tunjayoff/sofascore_scraper/releases) has `sofascore-scraper-X.Y.Z.tar.gz` / `.zip`: the source of that version with the web app already built, so Node.js is not needed. Unpack it and continue with the [manual install](#manual-install) from the `python -m venv` step, or run `./scripts/install.sh` (`scripts\install.ps1` on Windows) inside the folder.

### Manual install

```bash
git clone https://github.com/tunjayoff/sofascore_scraper.git
cd sofascore_scraper
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt -c constraints.txt   # constraints.txt: the exact versions CI tests
python -m patchright install chromium --no-shell   # required: the browser the app drives
```

The browser step is required even when Google Chrome is installed. The bridge starts patchright's own Chromium build (through Scrapling's `StealthySession`), not the system browser. Use `patchright` in this command, not `playwright`: each of the two packages downloads the build its own version expects. `--no-shell` leaves out the separate headless shell, which the bridge never uses.

Build the web app (needs Node.js 20.19+ or 22.12+; skip it if you only use the terminal modes):

```bash
cd frontend && npm install && npm run build && cd ..
```

Copy environment defaults and adjust:

```bash
cp .env.example .env
```

### Check your setup (doctor)

```bash
python main.py --doctor          # readable report
python main.py --doctor --json   # the same as JSON, for scripts and servers
```

The check never contacts SofaScore. Each line is `OK`, `WARN` or `FAIL`, and every problem comes with a one-line fix:

| Check | What it looks at |
|-------|------------------|
| `python` | Python 3.10 or newer. |
| `packages` | Every package in `requirements.txt` can be imported; pinned versions match. |
| `browser` | The Chromium build the bridge starts is installed **and starts** (headless, on `about:blank`, with a temporary profile). |
| `profile` | The browser profile folder is writable and not locked by another machine, a leftover browser or a dead process. |
| `data_dir`, `config_dir` | `DATA_DIR` and `config/` are writable. |
| `frontend` | `frontend/dist/` exists. Missing is a warning: the terminal modes work without it. |
| `env` | `.env` parses and its values are valid (numbers, `true`/`false`, proxy address, language, log level). |

```text
[ OK ] Python: 3.14.0
[ OK ] Packages: all 13 required packages can be imported
[FAIL] Browser: the Chromium build of patchright 1.63.0 is not installed (expected at ...); an installed Google Chrome is not used
       Fix: Run: .venv/bin/python -m patchright install chromium --no-shell
[WARN] Web UI: not built (frontend/dist is missing): the web app shows a help page instead; the terminal modes work without it
       Fix: cd frontend && npm install && npm run build   (or start with scripts/start_web.py, which builds it)
```

- **Exit code:** `0` when nothing failed (warnings allowed), `1` when at least one check failed. `--strict` also exits with `1` on warnings.
- `--only python,browser` / `--skip frontend` choose checks; `--lang en|tr` sets the language (default: the app's language, see [Language](#language)).
- It runs before the app's own imports, so it also works when packages are missing (that is one of the things it reports).
- `--live` additionally makes **one** real request to SofaScore through the browser bridge. It is never made otherwise. Stop the web app first: two processes cannot open the same browser profile.
- The launcher (`scripts/start_web.py`) runs the same check on every start and installs missing packages and the missing browser itself. Other code can call `src.doctor.run_checks()` / `src.doctor.report()`.

## Configuration

### Environment (`.env`)

See `.env.example` for all keys. Common ones:

| Variable | Purpose |
|----------|---------|
| `DATA_DIR` | Root folder for stored data (default `data`). Web app reads this via `ConfigManager`. |
| `APP_LANGUAGE` | `en` or `tr`. Empty (the default) means no language is pinned: the system language is used if it is Turkish, English otherwise. The language switch under **Settings** in the web app writes this value. See [Language](#language). |
| `MAX_CONCURRENT` | Parallel detail requests cap. |
| `REQUEST_RATE_LIMIT` | Requests per second to SofaScore for **all processes together** (web app, CLI, every `--watch`, `--refresh-only`). Default `5`; a higher value or `0` / `off` (no limit) is faster but raises the risk of being blocked. See [Request budget](#request-budget-all-processes). |
| `USE_PROXY` / `PROXY_URL` | Optional proxy: `http://`, `https://` or `socks5://`, e.g. `http://user:password@host:8080`. Also under **Settings → Connection** in the web app; the saved password is never shown again (the form and the API show `***`, and leaving it that way keeps it). The built-in browser uses a changed proxy after the app restarts. |
| `FETCH_ONLY_FINISHED` | Keep only finished matches (`status.type == finished`). Default `true`. Upcoming fixtures are dropped from schedule files. |
| `REFRESH_WINDOW_HOURS` | Hours after kick-off during which a saved match is provisional and gets re-read (default `72`, `0` = off). See [Refresh policy](#refresh-policy). |
| `RATE_LIMIT_*` / `SERVER_ERROR_*` | Circuit breaker thresholds, counted per request across all phases of a job. See [Missing slices, failed requests and the circuit breaker](#missing-slices-failed-requests-and-the-circuit-breaker). |
| `LOG_LEVEL` / `LOG_DIR` / `LOG_TO_FILE` / `LOG_MAX_MB` / `LOG_BACKUP_COUNT` | Log level, log file location and rotation. See [Logs and diagnostics](#logs-and-diagnostics). |
| `SOFASCORE_API_TOKEN` | Optional access token for the web app and its API. Empty (the default) = off. See [Security model](#security-model). |
| `SOFASCORE_ALLOWED_HOSTS` | Host names the web app answers to, comma-separated (default `localhost,127.0.0.1,[::1]`). See [Security model](#security-model). |

Tuning for the web UI (timeouts, retries, logging) is exposed under **Settings**; writing settings updates `.env`.

### Language

The app speaks English and Turkish. The terminal, `--doctor`, the installers, the launcher and the web app all choose between them by one rule:

1. **A language you chose wins.** That is `APP_LANGUAGE=en` or `tr` in `.env` (or in the environment), which is also what the language switch on the **Settings** page writes. A browser additionally remembers the choice made in it.
2. **Otherwise the system language is used.** The terminal side reads `LC_ALL`, `LC_MESSAGES` and `LANG` (on Windows without those: the display language); the web app reads the browser's language list and takes the first language it has.
3. **Otherwise English**, which is also the answer for every language other than Turkish.

A new install pins nothing: `.env.example` ships with `APP_LANGUAGE` empty, so a Turkish system or browser gets Turkish and everyone else gets English. To force one language everywhere, set `APP_LANGUAGE`. An existing `.env` with `APP_LANGUAGE=tr` keeps Turkish.

Log lines (console and log file) are still written in Turkish whatever the language; `argparse`'s own words in `--help` (`usage:`, `options:`) stay English.

### Request budget (all processes)

Every code path limits itself (`MAX_CONCURRENT`, the waits, the watcher's 1 s spacing), but separate processes do not see each other: one `--watch` per sport plus a web job plus a cron `--refresh-only` simply add up. `REQUEST_RATE_LIMIT` is one budget shared by all of them. Every request to SofaScore, through curl or through the browser, first reserves the next free slot in a small state file guarded by an operating-system file lock.

- **Default: `5` requests per second for all processes together**, with up to one second of budget (5 requests) as a burst after idle time. This is deliberately far below what the download path can do on its own (about 20–60 requests/s with default settings; measured offline, reproduce with `python scripts/bench_bulk_rate.py`): it keeps the load on SofaScore low and the risk of being blocked small.
- **How long a download takes.** Match details cost 7 requests per football match (8 for tennis), so a 380-match season is about 2,700 requests: roughly **9 minutes** at the default, where it used to take one to two minutes. While the budget is the limit, raising `MAX_CONCURRENT` does not make a download faster.
- **Raising it or turning it off is your call, and your risk.** Set `REQUEST_RATE_LIMIT=20` in `.env` (four times faster), or change **Shared request budget** under **Settings → Advanced** in the web app, which the running web app applies at once; other running processes read `.env` when they start. `0` or `off` removes the limit entirely: every process is on its own again and sends as fast as it can. Both make it more likely that SofaScore blocks you; the Settings page shows a warning while the value is above 5 or off.
- **Lower it to be gentler**, e.g. `1`. At `1` or below requests are evenly spaced. A request that has to wait long for its turn is not dropped: the wait does not count against the browser bridge's 120 s request timeout.
- **Watchers** share an extra 1 request/s lane, so one `--watch` per sport stays at least 1 s apart in total, not per process.
- **Where the state lives:** `~/.cache/sofascore_scraper/throttle/` (change with `SOFASCORE_THROTTLE_DIR`). Processes share the budget when they share this folder; for containers, point them at one shared volume.
- **Failure behaviour:** the lock is released by the operating system when a process dies, so a crash cannot leave a stale lock. If the folder is not writable or the lock cannot be taken within 1 s, requests are not blocked: that process paces itself, logs one warning and retries the file 30 s later.

### Leagues (`config/leagues.txt`)

One line per league, `Name: ID`, where ID is the numeric SofaScore **unique tournament ID** (it appears in tournament URLs, e.g. `.../premier-league/17` → `17`). The file is yours and is not tracked by git: on first run it is created from `config/leagues.example.txt`, which contains no league. A new install starts empty: add leagues in the web app (**Leagues → Add league**, which also records each league's sport) or in the terminal menu. The app adds and removes lines itself when you manage leagues in the web app or the CLI.

CLI override:

```bash
python main.py --config /path/to/leagues.txt --data-dir /path/to/data
```

`--config` / `--data-dir` apply to **interactive** and **headless** modes. The web server loads the singleton `ConfigManager` from the project `.env` (`DATA_DIR`, etc.); align paths so the web UI and CLI see the same data if you use both.

## Usage

### How to use the app (quick start)

**Web (recommended for most users)**

1. Finish **Installation** and **Configuration** (`pip install`, `cp .env.example .env`). Optionally set `DATA_DIR` if you want data somewhere other than `./data`.
2. Start the app: `./start-sofascore.sh` (or `python scripts/start_web.py`; on Windows double-click `Start SofaScore.bat`, on macOS `Start SofaScore.command`). The launcher creates `.venv` if it is missing, runs the [setup check](#check-your-setup-doctor), installs what is missing (Python packages, the browser), builds the web app when `frontend/dist/` is missing and Node.js is installed, then opens `http://127.0.0.1:8000`. `python main.py --web` starts the server alone and installs nothing.
   After updating the code (`git pull`), rebuild the web app yourself: `cd frontend && npm install && npm run build`. The start script only builds when `frontend/dist/` is missing, so otherwise you keep seeing the old interface.
3. **Sport** — The switch at the top of the sidebar (All / Football / Basketball / Tennis) filters every page. Pick the sport you are working on.
4. **Leagues** — A new install has no leagues; the page opens with an **Add league** button. **Add league** searches SofaScore; filter the results by sport and press **Add**. A league whose sport is unknown (for example one added to `config/leagues.txt` by hand) shows a **Pick sport** box; choose once and it is saved.
5. **Download** — Left column: pick a league. Middle: tick seasons (the season list is fetched automatically the first time; **Latest season** / **Last 3 seasons** are shortcuts). You can pick seasons from several leagues; they collect in the **Download list** on the right. Press **Download N seasons**. Matches and their details (statistics, events, lineups) are downloaded together.
6. While a download runs, the card at the bottom left of the sidebar shows progress; **Stop** takes effect right away: no new requests are sent and retry waits are cut short; a request already in flight can take up to the request timeout (`REQUEST_TIMEOUT`) to return. Only one download runs at a time. **Activity** lists the current and past downloads.
7. **Matches** — Filter by league, season, date and **Details** (with / missing). When a league has matches without details (typically after a stopped download), a banner offers **Download missing**. Click a row to open the match: score by period, overview, statistics, events and lineups.
8. **Settings** — Language and theme; data folder, disk usage, **Back up** and **Delete all data**; **Connection**: a connection check (**Test connection** sends one request to SofaScore, only when you press it, and says what happened) and the proxy; advanced request settings (timeout, concurrency, waits, retries).

> **Delete all data** removes every downloaded season, match and detail and cannot be undone. Take a backup first. A backup is a zip under `data/backups/` (inside your `DATA_DIR`) holding `data/`, `leagues.txt` and `league_sports.json`. `.env` is left out because it can hold proxy credentials and the access token; add `?include_env=true` to `POST /api/data/backup` if you want it (such a backup has `_with_env` in its file name and is readable by its owner only). To restore: stop the app, unzip `data/` into the project folder (or your `DATA_DIR`), and copy `leagues.txt` and `league_sports.json` to `config/` if you want those back too.

> **While a download is running**, **Back up**, **Delete all data**, removing a league and changing the data folder are refused with a message: stop the download or wait for it to finish. A download cannot start while a backup or delete is still in progress either. Changing the data folder takes effect immediately (no restart): downloads and the **Activity** history then use the new folder (each data folder keeps its own history in `.meta/jobs.db`); files in the old folder are not moved.

**Terminal menu**

Run `python main.py` and work through the numbered menus: manage leagues, refresh seasons, fetch match lists, fetch details, run stats, or export CSV. There is no counterpart of the web Download page; use the prompts to choose leagues and options.

**Tips**

- The first download of a big league can take a long time; start with one league and a few recent seasons.
- If you hit rate limits or many errors, lower the **shared request budget** (`REQUEST_RATE_LIMIT`; back to the default `5` if you raised it or turned it off) in **Settings**; avoid `--ignore-rate-limit` unless you know what you are doing.
- For the same dataset in **web** and **CLI/headless**, keep `DATA_DIR` in `.env` aligned with `--data-dir` when you use the command line.
- Prefer a season that already has finished matches. The newest label (e.g. European `26/27`) is often fixtures-only; the scraper can fall back automatically.
- Stopping a download keeps everything fetched so far. Matches whose details were not reached show **Details: No** in Matches; use **Download missing** there (or on the Download page) to complete them.

### Troubleshooting

**Something does not start, or every download fails**

Run `python main.py --doctor`. It names what is missing (most often the browser: `python -m patchright install chromium --no-shell`) and prints the fix. When the browser cannot start, the app does not retry for 5 minutes, so fix the cause and restart the app. See [Check your setup](#check-your-setup-doctor).

**The browser shows "The web interface is not built"**

`frontend/dist/` is missing. Build it with `cd frontend && npm install && npm run build` (Node.js 20.19+ or 22.12+) and reload the page; the server does not need a restart. Without Node.js, use a release archive from the [Releases page](https://github.com/tunjayoff/sofascore_scraper/releases) once one is published (it ships with the web app built), or copy a `frontend/dist/` folder built on another machine. The API and the terminal modes work in the meantime.

**Seasons appear but fetch finds 0 matches** (`İşlenecek maç verisi bulunamadı` / `0it`)

1. Confirm the SofaScore **unique tournament ID** in the URL (MLS is **`242`** — that ID is correct).
2. Not every league exposes a sequential week schedule (`events/round/1..N`). **Premier League**-style competitions do; **MLS** and some others do not:
   - MLS `/rounds` is empty or only playoff-style IDs (e.g. `227`), and `events/round/1` returns nothing.
   - The scraper must use paginated **`events/last` + `events/next`** for those seasons.
   - Older builds that only probed weeks `1..50` therefore downloaded PL fine but returned **zero MLS matches**. Update to a release that includes the event-list fallback, refresh seasons, and re-run the fetch.
3. With `FETCH_ONLY_FINISHED=true` (default), not-yet-played fixtures are ignored. If a brand-new season has no finished games yet, pick the previous season (or wait / set `FETCH_ONLY_FINISHED=false` if you intentionally want fixtures).
4. Stale season IDs (SofaScore retired the ID after a refresh) also yield empty schedules — open the league on the **Download** page, press **Refresh** above its seasons, then download again.

### Logs and diagnostics

Everything the app logs goes to the console **and** to a log file, so the output is still there after the launcher window is closed or an overnight download has failed.

- **Where:** `logs/sofascore_scraper.log` in the project folder. Change the folder with `LOG_DIR` (a relative path is resolved against the project folder, not the current directory). The web app, the terminal UI, `--headless`, `--watch` and `--refresh-only` all write to the same file; each line carries the process id.
- **Size:** the file is rotated at `LOG_MAX_MB` (default 5 MB) and `LOG_BACKUP_COUNT` (default 5) older files are kept as `.1` … `.5`, so logs never take more than about 30 MB. On Windows a file that is open cannot be renamed: while more than one process is writing to the log (for example the web app and a `--watch`), it is not rotated and can grow past the limit; it is rotated again once a single process is left.
- **Level:** `LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`; `DEBUG=true` forces `DEBUG`). Changing it under **Settings** takes effect immediately in the running web app, no restart. Other processes that are already running pick it up on their next start.
- **Secrets are masked** before a line is written (file and console): the captcha token, cookies, `Authorization` headers, proxy credentials (`http://user:password@host` becomes `http://***@host`) and the value of any `.env` key that looks like a secret (`*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*_KEY`, …).
- **Server / Docker:** the console (stdout) is always on and is the primary output in a container; when stdout is not a terminal the lines are plain, timestamped text. In the Docker image the file is written to the `/app/logs` volume; set `LOG_TO_FILE=false` to rely on `docker logs` only. If the folder is not writable the app says so once and continues with the console only.

**Reporting a problem:** attach a diagnostics bundle. It is a small zip with `diagnostics.json` (app version and commit, Python and OS, package versions, settings with secrets masked, bridge health, request budget, the [setup check](#check-your-setup-doctor) results (without starting the browser), the last download jobs) and `log_tail.txt` (the last 1000 log lines). Your home directory is written as `~`; values of `.env` keys the app does not know are left out. Have a look at it before you send it.

```bash
python main.py --diagnostics              # writes logs/sofascore-diagnostics-<time>.zip and prints the path
python main.py --diagnostics ./report.zip # or a path / folder of your choice
```

With the web app running (Docker included), the same bundle downloads from `http://127.0.0.1:8000/api/diagnostics/bundle`, and it is the better one for "SofaScore is blocking us" reports: bridge health is per process, so only the web app's bundle carries the web app's state. `GET /api/logs?limit=200&level=WARNING` returns the most recent log entries as JSON.

### Interactive terminal

```bash
python main.py
```

### Web application

```bash
python main.py --web
```

Default URL: `http://127.0.0.1:8000`. The server only listens on this machine. `--host` opens it to your network; read [Security model](#security-model) first. One address (`--host 192.168.1.5`) works as it is. `--host 0.0.0.0` (every interface) also needs `SOFASCORE_ALLOWED_HOSTS`, and without `SOFASCORE_API_TOKEN` the app warns at startup that anyone who can reach the port can read and delete data and change settings. `--port` changes the port and `--dev` reloads on code changes. Health: `GET /health` (also reports the version).

Background jobs report status via `GET /api/scrape/status` and `GET /api/scrape/stream` (SSE). Heavy API work runs off the asyncio event loop so the UI stays responsive during long fetches.

### Security model

The web app has **no user accounts**. By default it listens on this computer only, and that is the setup it is made for. Opening it to a network is your decision as the administrator, and so is limiting who can reach it (firewall, VPN, reverse proxy). The app does not undo that work, and it defends against attacks that arrive through your own browser, which no firewall stops.

**What the app does**

- **Answers to known host names only** (DNS rebinding). A request is served only if its `Host` header is on the allow-list: `localhost`, `127.0.0.1` and `[::1]` by default. `SOFASCORE_ALLOWED_HOSTS` (comma-separated, in `.env` or the environment) replaces the list and is always used exactly as written. `--host 192.168.1.5` adds that one address by itself. `--host 0.0.0.0` (every interface) does not start until `SOFASCORE_ALLOWED_HOSTS` says which names to answer to. `--allow-any-host` (or `SOFASCORE_ALLOWED_HOSTS=*`) answers to any name; this is **insecure** and turns the protection off.
- **Refuses writes triggered by other sites** (CSRF). No `GET` endpoint changes anything. Every state-changing request (start or stop a download, save settings, delete data, back up, search SofaScore) is answered with `403` when the browser reports that another site sent it (`Sec-Fetch-Site`, `Origin`). Programs that send neither header, such as curl, are not affected.
- **Sends security headers** with every response: a Content-Security-Policy (scripts, styles, fonts and requests only from the app itself; no inline script, no `eval`, no framing), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and `Cross-Origin-Resource-Policy: same-origin`. Two exceptions: a web app build made before this policy existed still needs `eval`, so it is served with `'unsafe-eval'` (and a warning in the log) until you rebuild it; and the API documentation pages `/docs` and `/redoc` load their scripts from a CDN and get a policy of their own.
- **Optional access token.** Off by default. Set `SOFASCORE_API_TOKEN` to a long random value and restart the app; `python -c "import secrets; print(secrets.token_urlsafe(32))"` makes one. From then on every `/api` request needs it. Programs send `Authorization: Bearer <token>`. The web app asks for it once and keeps an `HttpOnly`, `SameSite=Strict` session cookie for 30 days (the live status stream uses the same cookie); **Settings → General → Sign out** ends the session, and changing the token ends all of them. `GET /health` stays open for health checks, but without the token it answers only `{"status": "ok"}`. The token is compared in constant time and never appears in the log, the diagnostics bundle or an API response.
- **Warns once at startup** when it listens on a non-local address without a token.
- **Keeps secrets private on disk.** `.env` (proxy password, tokens) is created with mode `0600` and the browser profile folder (SofaScore cookies) with `0700`; existing ones are tightened at every start. A backup that includes `.env` says so in its file name (`backup_…_with_env_….zip`) and is readable by its owner only. On Windows the files rely on the permissions of your user folder instead. The proxy password is masked in the settings API, the logs, the diagnostics bundle and in error messages returned by the API.

**What you must do before opening it to a network**

- Set `SOFASCORE_API_TOKEN`. Without it, anyone who can reach the port can read and delete your data and change the settings.
- Limit who can reach the port (firewall, VPN). The app does not limit login attempts, so the token must be long and random.
- Put TLS in front of it. The app speaks plain HTTP: without a reverse proxy that terminates TLS, the token and the session cookie cross the network unencrypted. The proxy must pass the original `Host` header on, or the name it sends must be in `SOFASCORE_ALLOWED_HOSTS`.
- With Docker, the container always listens on every interface inside its own network and `-p` decides who can reach it, so the startup warning does not exist there: if you publish the port beyond `127.0.0.1`, set the token and `SOFASCORE_ALLOWED_HOSTS` yourself.

```bash
curl -H "Authorization: Bearer $SOFASCORE_API_TOKEN" http://127.0.0.1:8000/api/leagues
```

### Headless / automation

At least one of `--update-all` or `--csv-export` is required with `--headless`. Otherwise the process exits with code **2**.

| Flag | Meaning |
|------|---------|
| `--headless` | No terminal menu |
| `--update-all` | Run a fetch pipeline |
| `--fetch-mode full` | Seasons + match lists + details (default) |
| `--fetch-mode details` | Match details only (uses existing schedule/summary CSVs) |
| `--league-id ID` | Limit `--update-all` to one configured league |
| `--csv-export` | Build/export processed CSV dataset |
| `--ignore-rate-limit` | Disable circuit breaker (use with care) |
| `--refresh-only` | Only re-read provisional records (no `--headless` needed); see [Refresh policy](#refresh-policy) |
| `--refresh-legacy` | Also refresh records saved before `observation.json` existed, once |
| `--recheck-unavailable [legacy\|all]` | Reopen "this slice does not exist for this match" markers so the next download asks again; sends no requests itself. See [Missing slices](#missing-slices-failed-requests-and-the-circuit-breaker) |
| `--watch` | Live watcher with `--sport` and `--league-ids` or `--event-ids` (`--watch-hours` optional); see [Watch mode](#watch-mode) |
| `--doctor` | Check the environment and exit, `0` = ready, `1` = something failed (no `--headless` needed); see [Check your setup](#check-your-setup-doctor) |

Examples:

```bash
python main.py --headless --update-all
python main.py --headless --update-all --fetch-mode details --league-id 52
python main.py --headless --csv-export --data-dir ./data
```

Exit codes: **0** success, **1** unexpected error or data could not be written, **2** headless with no action, or the circuit breaker stopped the run, **6** another process is already writing to the same data folder.

Only one process writes to a data folder at a time. While a download runs in the web app or in another headless run, `--headless --update-all`, `--refresh-only` and `--recheck-unavailable` do not start: they print who holds the lock (process id, host, purpose, since when) and exit with **6**. The same goes for a second `--watch` of the same sport. `--headless --csv-export` on its own is not affected.

### Command-line help

```bash
python main.py --help
python main.py --version
```

## Data layout (under `DATA_DIR`)

Typical structure:

```text
data/
├── seasons/           # Season metadata per league
├── matches/           # Match list / summary CSVs by league & season
├── match_details/     # Per-match JSON folders (basic, stats, lineups, …)
│   └── processed/     # Aggregated CSV exports
├── datasets/          # Reserved / auxiliary
└── score_changes.jsonl  # Post-finish changes found by refresh (see Refresh policy)
```

Exact paths may vary slightly by league naming and migrations.

The match list reads the per-season summaries under `matches/`; the export CSV in `match_details/processed/` is only a fallback when no summaries exist.

Next to `config/leagues.txt` (the `name: id` list the CLI also reads), `config/league_sports.json` stores each league's sport as `{"<id>": "football" | "basketball" | "tennis"}`. It is filled when a league is added from the web app, when you pick a sport in the UI, or from a downloaded match of that league.

The supported sports are defined in one place, the registry in `src/sports.py`: per sport its score shape, the live watcher's parameters and the match-detail endpoints requested for it. The CLI, the downloader, the watcher and the web API all read it.

### Missing slices, failed requests and the circuit breaker

Each match folder holds one JSON file per detail slice (`statistics`, `lineups`, `incidents`, …). Two bookkeeping files sit next to them:

- `_unavailable.json` counts, per slice, how often SofaScore gave a **definitive** "nothing here" answer for a finished match: HTTP 404, or a 200 response with no data in it. After two such answers the slice is no longer expected for that match (tennis has no lineups, for example) and the match counts as complete.
- `_slice_status.json` records the last **failed** request per slice: `reason` (`403`, `429`, `5xx`, `timeout`, `network`, `parse`), the HTTP status, the UTC time and how many times in a row. A failed request is never counted as "not available": the slice stays expected, the match stays incomplete, and the next download asks for it again.

Earlier releases counted every empty result, including requests that failed during a block or an outage. Those markers cannot be told apart from genuine ones, so they are left alone and are **not** reset automatically: that would re-request every "no lineups" tennis match on the next run. To re-check them:

```bash
python main.py --recheck-unavailable                 # reopen markers not confirmed by a definitive answer
python main.py --recheck-unavailable --league-id 17  # one league
python main.py --recheck-unavailable all             # reopen every marker
python main.py --recheck-unavailable --headless --update-all --fetch-mode details   # reopen, then download
```

The flag itself sends no requests; the reopened slices are requested by the next details download. Markers confirmed by a definitive answer are kept, so running it a second time changes nothing.

**Circuit breaker.** One breaker per job counts the final outcome of every request: season lists, match lists, `/event`, each detail slice and refreshes. It trips after `RATE_LIMIT_THRESHOLD_CONSECUTIVE` failed requests in a row (default 20), when `RATE_LIMIT_THRESHOLD_RATIO` of all requests have failed (default 0.9, after the first 50), or after `SERVER_ERROR_THRESHOLD_CONSECUTIVE` 5xx answers in a row (default 50). It also trips early when the browser bridge turns `blocked` during the job (see [bridge health](#is-sofascore-blocking-us-bridge-health)) and the job's own requests keep ending in 403. A 404 is an answer, not a failure. Once tripped, the job sends no further requests in any phase and says why: the web job card shows it, and `--headless` and `--refresh-only` exit with code 2.

**Storage errors.** A match whose files cannot be written is reported as failed, not as downloaded. If the cause will repeat for every match (disk or quota full, permission denied, read-only file system), the job stops with a message naming the path and the reason.

Matches without a SofaScore unique-tournament id are stored under `match_details/_no_tournament/<sport>/<match id>/`; records already on disk stay where they are.

## Refresh policy

SofaScore keeps editing some results after a match has finished. In the research run (`docs/status-matrix/README.md`, "Geriye dönük"), the final or period score changed after `finished` in 78 of 255 lower-tier basketball matches, 6 of 240 lower-tier football matches and 4 of 145 upper-tier basketball matches. The latest final-score change came 66.4 h after kick-off. A match downloaded once can therefore differ from SofaScore's own final state.

- **When a record is refreshed.** Every saved match has `observation.json` next to `basic.json`, holding when we read it (`observed_at_utc`) and SofaScore's `changes.changeTimestamp`. A record is *provisional* while `observed_at_utc < startTimestamp + REFRESH_WINDOW_HOURS` (default **72**). On the next download (web job, `--update-all`, or `--refresh-only`), provisional records are re-read. Only `/event/{id}` is fetched; the stats and lineups are not. Refreshes run after new and incomplete matches, and the job card counts them separately ("N refreshed (M changed)"). A record is re-read at most once every `REFRESH_MIN_INTERVAL_HOURS` (default 6, advanced `.env` setting), so hourly downloads do not fetch the same match 72 times. Once a read lands after the window, the record is final and never fetched again.
- **Older records.** Matches saved before this feature have no `observation.json`. They count as final, so the default setting adds **no** requests for existing data. `--refresh-legacy` re-reads each of them once.
- **Turning it off.** Set `REFRESH_WINDOW_HOURS=0` (in `.env` or under **Settings**).
- **Change log.** When the status triple, `winnerCode`, any `homeScore`/`awayScore` field or `startTimestamp` differs, `basic.json` is overwritten and one line is appended to `DATA_DIR/score_changes.jsonl` (ignored by git):

```json
{"ts_utc": "2026-09-30T08:00:00+00:00", "event_id": 16950622, "sport": "football",
 "tournament": {"id": 17, "name": "Premier League"}, "tier_hint": true,
 "start_ts": 1789497000, "hours_after_start": 2.1,
 "changed": {"awayScore.penalties": [6, 5]}, "old_change_ts": 1789504592, "new_change_ts": 1789504600,
 "status_class": ["completed", "completed"]}
```

  - `changed` gives the old and new value of each field. `changes` from SofaScore only names the fields, so this file is the first record of *how much* a result changed.
  - `hours_after_start` is the new `changeTimestamp` minus kick-off.
  - `tier_hint` is SofaScore's player-statistics coverage flag (tournament or event level). It shows how much SofaScore covers the league, not the league's real level.
  - If a match that counted as played turns void (`completed` → `void`, e.g. cancelled afterwards), the line carries `"status_regressed": true`. The same flag goes into `observation.json`. The record is **not** deleted; that decision is yours.

```bash
python main.py --refresh-only                 # re-read provisional records only (e.g. a daily cron)
python main.py --refresh-only --league-id 17  # one league
python main.py --refresh-only --refresh-legacy
```

## Watch mode

`python main.py --watch` follows live matches and writes **events**; it does not settle anything. A consumer (a separate service, the web UI, or you with `tail -f`) decides what to do with them.

```bash
python main.py --watch --sport football --league-ids 17,8     # every live match of these leagues
python main.py --watch --sport tennis --event-ids 17196038,17210464 --watch-hours 3
```

- **How it polls.** Every 30 s one request to `/sport/{sport}/events/live`, plus `/event/{id}`:
  - immediately when a tracked live match drops out of the live list (the earliest end signal);
  - every 30 s while a match is near its end;
  - every 5 min for a stuck match.
- **Near the end:**
  - football: 2nd half from minute 80 or once `injuryTime2` appears;
  - basketball: `played ≥ 90%` of regulation, or the last period when there is no clock data;
  - tennis: the deciding set.
- **Rate budget.** At most `WATCH_MAX_EVENT_POLLS` (default 20) match pages per round, requests at least 1 s apart. The spacing is shared by every `--watch` process on the machine (see [Request budget](#request-budget-all-processes)), so one watcher per sport still stays under 1 request/s in total; with several busy watchers a round can take longer than 30 s. If more matches are near the end than that, match pages drop to every 60 s and a warning is logged.
- **Stuck match.** Still live or not started 4 h after kick-off (tennis: 6 h after the real first-set start, since its `startTimestamp` is only the scheduled slot; set durations exclude breaks such as rain delays, so this start can come out late and `stuck` fires a little later): one `stuck` event, then polled every 5 min. If it turns void and its start time has moved (suspended tennis continues the next day with the same id), it stays tracked.
- **Events.** One JSON line per event in `DATA_DIR/watch_events.jsonl`:
  - `status_changed` `{event_id, from, to, at_utc, change_ts, scores}`. `scores` comes from `extract_scores`. The first `completed` carries `provisional: true` until the refresh window closes; see [Refresh policy](#refresh-policy).
  - `score_changed` for live scores `{event_id, from, to, at_utc}`.
  - `stuck`.
- **Restarts.** The last known class per match is kept in `DATA_DIR/watch_state_{sport}.json`, so a restart does not emit the same transition twice. Each sport has its own file, so watchers for different sports running at the same time do not overwrite each other. `watch_state.json` is the old format; it is no longer read and can be deleted. Ctrl+C stops cleanly.
- **When it exits.** With `--event-ids`, the watcher exits when every tracked match is over.

Why these numbers: `events/live` is cached for 5 s at the CDN, and whistle → `finished` took a median of 20 s (max 302 s) in the research (`docs/status-matrix/README.md`). Polling faster than 30 s gains nothing.

## REST API (overview)

All routes are prefixed with `/api` unless noted.

- **Leagues**: list (each with `sport`), create (optional `sport`), `PATCH /api/leagues/{id}` to set the sport, delete, search (local: `GET /api/leagues/search`; remote: `POST /api/leagues/search-remote?q=…`, a `POST` because every call sends a request to SofaScore; remote results carry `sport`), seasons, refresh seasons, missing-details.
  - Remote search and season refresh say why they failed instead of answering with an empty list. The error body is `{"detail": {"reason": "...", "message": "..."}}`, with `reason` one of `blocked` (SofaScore answered 403), `browser` (it asked for the challenge and the built-in browser could not start), `rate_limited` (429/503), `network` (no connection, timeout, proxy), `not_found` (season refresh: no league with that ID) or `upstream` (an answer the app did not expect). The status is 502, except 503 for `rate_limited` and 404 for `not_found`. An empty list with 200 means SofaScore really found nothing. The web app shows each reason with a next step.
- **Sports**: `GET /api/sports` — the supported sports and, for each, the match-detail slices requested for it (read-only view of the registry in `src/sports.py`).
- **Matches**: `GET /api/matches` — paginated, filters `league_id` (one id or several comma-separated, e.g. `17,8`), `season_id`, `date`, `details=present|missing`, `sort=asc|desc`; every row has `has_details`. Also single-match JSON and on-demand fetch for one match.
- **Scraper**: `POST /api/fetch` (body: mode `full` or `details`, `selections: [{league_id, season_ids, match_ids}]`), `POST /api/scrape/cancel` (no new requests after it; retry waits are cut short), status, SSE stream.
- **Dashboard / stats / settings**: JSON for the web UI; settings mirror `.env` keys.
- **Data**: backup zip, clear scopes, CSV export (`GET /api/export/csv` downloads the existing export and answers `404` when there is none; `POST /api/export/csv` creates it first).
- **Access**: no `GET` endpoint changes anything, and state-changing requests that another site triggered are refused with `403`. With `SOFASCORE_API_TOKEN` set, every `/api` request needs `Authorization: Bearer <token>` or the web app's session cookie (`POST /api/auth/login` with `{"token": "..."}`, `POST /api/auth/logout`, `GET /api/auth` for the state); otherwise the answer is `401` with `{"detail": {"code": "auth_required", "message": "..."}}`. See [Security model](#security-model).
- **Refusals while a download runs**: `POST /api/data/clear`, `POST /api/data/backup` (except `scope=config`), `DELETE /api/leagues/{id}` and a `POST /api/settings` that changes `data_dir` answer `409` with `{"detail": {"code": "job_running", "message": "..."}}`. While one of these is in progress, they and `POST /api/fetch` answer `409` with code `data_operation_running`. A successful `data_dir` change answers `"data_dir_changed": true`; a folder that cannot be created answers `400` with code `data_dir_unusable`.
- **Bypass Status**: `GET /api/bypass/status` (with `health`: `ok` / `degraded` / `blocked`, see [Is SofaScore blocking us?](#is-sofascore-blocking-us-bridge-health)) and live test `POST /api/bypass/test`: one request through the browser, answered with `success`, `reason` (as above, when it failed), `browser_ready`, `has_token` / `is_valid` and `health`. Nothing calls it on its own; in the web app it is the **Test connection** button under **Settings → Connection**.
- **Logs / diagnostics** (read-only, see [Logs and diagnostics](#logs-and-diagnostics)): `GET /api/logs` (`limit` 1–2000, `level` = minimum level), `GET /api/diagnostics` (the summary as JSON), `GET /api/diagnostics/bundle` (zip download). None of them takes a file path.
- **Health**: `GET /health` (no `/api` prefix) answers `status`, `version`, `ui`, plus `bridge` (the same health block) and `throttle` (the shared [request budget](#request-budget-all-processes)). With an access token set, a caller without it gets only `{"status": "ok"}`.

OpenAPI: `GET /docs` when the server is running.

## Anti-Bot Protection (BrowserBridge)

SofaScore rejects plain HTTP clients: API requests get `403 {"reason": "challenge"}` whatever TLS fingerprint `curl_cffi` presents. The app therefore makes API calls from inside a real browser page:

1. **BrowserBridge** starts a headless Chromium through [Scrapling](https://github.com/D4Vinci/Scrapling)'s `StealthySession` (patchright with stealth settings) and keeps one page open.
2. **Challenge:** on a 403 challenge it opens `sofascore.com/captcha.html`; Scrapling passes the embedded Cloudflare Turnstile and the resulting `sofa_captcha` cookie unlocks the API. Concurrent 403s share one solve; a failed solve is not retried for 3 minutes.
3. **Browser-first mode:** once curl is blocked and the browser succeeds, requests go straight to the browser for 10 minutes instead of failing through curl first.

The browser always runs **headless**, on a desktop and on a server alike, so the same code path is used everywhere; no display, Xvfb or Google Chrome is needed. Measured on the three sports: full seasons of Premier League (50 matches), Wimbledon (239) and EuroBasket (76) downloaded at 100% coverage without a display. Set `SOFASCORE_BROWSER_HEADED=1` to watch the browser while debugging.

The browser profile (cookies, solved challenge) lives in `~/.cache/sofascore_scraper/chrome_profile`; change it with `SOFASCORE_BROWSER_PROFILE`. A restart with an existing profile answers its first request in about 1–6 s.

### Is SofaScore blocking us? (bridge health)

Everything depends on the browser solving the challenge. When that stops working, jobs used to just fail slowly. The bridge now keeps a health state from the outcome of its requests:

| State | Meaning |
|-------|---------|
| `ok` | The last request got an answer (or none was made yet). |
| `degraded` | `BRIDGE_DEGRADED_AFTER` (default 3) requests in a row failed. |
| `blocked` | `BRIDGE_BLOCKED_AFTER` (default 10) requests in a row failed **and** the streak has lasted `BRIDGE_BLOCKED_MIN_SECONDS` (default 200 s, longer than one challenge retry: ten parallel requests failing on one unlucky solve is not a block yet). |

- **What counts as a failure:** a challenge that could not be solved (or a request still refused after solving), a 403 with no challenge offered, a browser that cannot start. A 403 that is solved and retried is a success. Network errors, 5xx and 429 neither extend nor reset the streak. One answered request returns the state to `ok`.
- **Where you see it:**
  - `GET /health` → `bridge` and `GET /api/bypass/status` → `health`: `state`, `consecutive_failures`, `last_success_at`, `failing_since`, `last_error` (`kind`: `challenge` / `forbidden` / `browser`), `thresholds`. `status` in `/health` stays `ok`: it says the server is up.
  - Web app: a banner at the top of every page while the state is not `ok`. Dismissing hides it for that streak; it returns if the state gets worse or a new streak starts.
  - Web app, **Settings → Connection**: the same state, and **Test connection** to try one request yourself (browser running or not, anti-bot check passed or not, and the reason if it failed).
  - Log: one warning per state change, not per request.
  - Terminal modes (interactive, `--headless`, `--watch`, `--refresh-only`): one line on stderr per state change, in the app language.
- The state is per process: the web app reports its own bridge, each CLI process its own.

### Server setup (Linux / Docker)

The [Docker image](#docker) already contains the browser and its system libraries. On a plain Linux server, or in an image of your own:

```bash
pip install -r requirements.txt -c constraints.txt
python -m patchright install chromium --no-shell
# Debian/Ubuntu only, once: system libraries Chromium needs (uses sudo)
python -m patchright install-deps chromium
# exit code 0 = ready; starts the browser once on about:blank, makes no request to SofaScore
python main.py --doctor --skip frontend
```

## Development

Run the web app with auto-reload:

```bash
python main.py --web --dev
```

The web app is a Vue 3 + TypeScript + Vite project in `frontend/` (Pinia, vue-router, vue-i18n, Tailwind). The server serves the built files from `frontend/dist/`.

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173, proxies /api to 127.0.0.1:8000
npm run build    # type-check (vue-tsc) + production build into frontend/dist/
```

Layout: `src/views/` one file per page, `src/components/` shared pieces, `src/stores/` (leagues, sport filter, running job), `src/api/client.ts` every backend call, `src/locales/{tr,en}.ts` all UI text.

Tests and lint (CI runs the same on Linux with Python 3.10 and 3.14; the Windows and macOS jobs, with Python 3.14, are best-effort: they report their result but do not block a pull request or a release):

```bash
pip install -r requirements-dev.txt -c constraints.txt
ruff check .
python -m pytest -q
python -m pytest -q --cov   # with coverage; fails below the floor set in pyproject.toml
```

`requirements.txt` lists the allowed version ranges; `constraints.txt` pins every package (indirect ones too) to versions that are known to work together. CI, the install scripts, the launcher and the Docker image all install with the constraints, so a new upstream release cannot break CI or a fresh install unnoticed; a weekly workflow installs the newest allowed versions instead and runs the same tests, and Dependabot proposes updates to the pins.

`tests/conftest.py` points `DATA_DIR`, `config/` and `.env` at a temporary folder with a small synthetic data set, so the suite never touches your data or settings. Tests that call SofaScore are marked `live` and skipped by default; run them with `python -m pytest -m live`. Tests marked `browser` start a real Chromium and are skipped by default too: `python -m pytest -m "browser and not live"` runs the BrowserBridge against a local fake site without contacting SofaScore (CI does this on every push).

### Releasing

For the maintainer. The version is written in one place, `pyproject.toml`; the CLI, `/health`, the web app, the release workflow and the Docker image all read it from there.

1. Set `version` in `pyproject.toml` to `X.Y.Z`.
2. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, put a new empty `## [Unreleased]` above it and update the links at the bottom of the file. That section becomes the release notes.
3. Check both locally, then commit and get the change onto `main`:

   ```bash
   python scripts/release.py check-tag vX.Y.Z   # tag ↔ pyproject version
   python scripts/release.py notes              # prints the notes; fails if the section is missing
   ```

4. Tag that commit and push the tag:

   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

The tag starts `.github/workflows/release.yml`: it checks that the tag matches the version, runs the same lint, tests and build as CI, builds and smoke-tests the Docker image, pushes it to `ghcr.io/tunjayoff/sofascore_scraper` (`X.Y.Z`, `X.Y`, `latest`) and creates the GitHub Release with the source + built web app archives. A version with a suffix (`X.Y.Z-rc.1` in `pyproject.toml`, tag `vX.Y.Z-rc.1`) is published as a pre-release and does not move `latest`. Nothing is published before the checks pass, and the publish steps are safe to repeat: if one fails for a passing reason (network, registry), re-run the workflow.

After the very first release, open the package's settings on GitHub once and make it public (GHCR packages start private), otherwise `docker pull` needs a login.

## Contributing

Contributions are welcome. You can help in several ways:

- **Bug reports** — Open an issue with steps to reproduce, expected vs actual behaviour, OS/Python version, and relevant `.env` flags (redact secrets).
- **Feature ideas** — Suggest use cases and constraints; maintainers may triage and discuss scope in the issue.
- **Pull requests** — Fork the repo, use a focused branch, keep changes small and on-topic, and describe *what* and *why* in the PR. Match existing code style; avoid drive-by refactors. If you touch user-visible text, update both languages: `frontend/src/locales/tr.ts` and `en.ts` for the web app, `locales/en.json` and `locales/tr.json` for the terminal UI.
- **Docs & translations** — Improvements to these READMEs or locale strings are appreciated.

By submitting a contribution, you agree that it is licensed under the project's license and that the maintainer may also offer it under other terms (for example, a commercial license). Be respectful in issues and reviews. If you are unsure whether an idea fits, open an issue first.

## License

[PolyForm Noncommercial 1.0.0](LICENSE). You may use, modify and share this software for **noncommercial purposes** — personal use, study, research, hobby projects, and use by charities, educational institutions and public bodies. **Commercial use is not permitted** without a separate license; to ask about one, open an issue or contact [@tunjayoff](https://github.com/tunjayoff).

Versions released before this change remain available under the MIT license.
