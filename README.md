# SofaScore Scraper

[![CI](https://github.com/tunjayoff/sofascore_scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/tunjayoff/sofascore_scraper/actions/workflows/ci.yml)

**Türkçe:** [README.tr.md](README.tr.md)

Python tool to download match data of 21 sports from [SofaScore](https://www.sofascore.com/) public HTTP APIs and store it locally in a data folder of its own. One core with three faces: a web app for people, a command line (`ssc`) for servers and scripts, and a versioned HTTP API (`/api/v1`) that the web app is built on.

This project is not affiliated with SofaScore. Use reasonable request rates and comply with applicable terms and laws.

## Features

- **21 sports** — Football, basketball, tennis, American football, Aussie rules, ice hockey, handball, rugby, futsal, minifootball, floorball, volleyball, badminton, table tennis, padel, snooker, baseball, cricket, e-sports, darts and MMA, each with its own score shape (`ssc describe sports`).
- **Leagues and follows** — Follow a league, a team, a player or a single match: search SofaScore by name in the web app or give the SofaScore id. Each follow has its season choice (current, last N, all or chosen seasons) and its data selection.
- **Choose what is downloaded** — Statistics, line-ups, incidents, head to head, form, streaks and the rest are on by default; betting odds, standings, season, leader, ranking and player data are off until you select them, for all sports, per sport or per follow.
- **Web app** — Overview, Leagues & follows, Matches, Score changes, Jobs, Exports, Backups, Health, Logs, Settings, Outputs and Data cleanup; **Add league** is always one click away and a **Help** panel explains the words. English and Turkish; light, dark or system theme. It shows stored data; there is no live score view.
- **Command line** — `ssc` for servers and automation (sync, fetch, refresh, export, backup, follows, jobs, status, watch, serve, …); the terminal menu of 2.x was removed in 3.0 ([what replaces it](#from-the-terminal-menu-removed-in-30)), the old `main.py` flags keep working for one more release.
- **Export** — Datasets of matches, data types, score changes, odds and standings in a stable, documented schema as JSONL, CSV, Parquet or SQLite; the stored payloads as they are; the 2.x “all matches” CSV. Files are named after the league or dataset and the date. From the web app's **Exports**, `ssc export` or the HTTP API.
- **Backup and restore** — Backups of the data folder, restored from the web app's **Backups** or with `ssc backup restore`.
- **Live watching** — `ssc watch` watches followed matches while they are played and writes their changes to an event log and to outputs (webhook, file, stdout).
- **Scheduling** — An optional in-app scheduler (off by default) runs downloads, refreshes and backups inside the web server; a systemd timer or cron does the same from outside.

## Requirements

- Python **3.10+** (3.11+ recommended).
- **Platforms:** Linux and Docker are the officially supported platforms; Windows and macOS are best-effort (the installers, launchers and CI cover them, but a problem that only occurs there does not block a release).
- **Chromium for patchright** — the browser the app reaches SofaScore through. A one-time download made by the install scripts and by the launcher (`python -m patchright install chromium --no-shell`). A Google Chrome or Chromium already on the machine is **not** used.
- **Node.js 20.19+ or 22.12+ and npm** — to build the web app (`frontend/`). The install scripts and `scripts/start_web.py` build it when Node.js is installed. Without it the command line and the HTTP API still work, and the web address shows a help page instead of the app. A [release archive](#release-archive) comes with the web app built.
- **A current browser** for the web app: Safari 16.4, Chrome 111, Firefox 128 or newer.
- **Git** — required for the one-line `curl | bash` installer (clones this repo); optional if you already extracted or cloned the project manually.
- Network access to SofaScore.
- Optional: **pyarrow 16 or newer** for Parquet exports (`pip install -e ".[parquet]"`). Without it every other export format works.

With [Docker](#docker) you need none of these on the host: the image carries Python, the built web app and the browser.

## Installation

Pick one:

| Way | You need | Best for |
|-----|----------|----------|
| [Install script](#quick-install-script) | Git, Python 3.10+, Node.js | A desktop: double-click launcher, easy updates with `git pull` |
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

The image contains the app, the built web app and the headless Chromium that [BrowserBridge](#anti-bot-protection-browserbridge) needs. It runs as a non-root user (uid 1000) and starts `ssc serve` (the web app and the HTTP API) by default. [docs/deploy/docker.md](docs/deploy/docker.md) has the details, including the live service in a container.

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
| `/app/data` | Everything downloaded (`DATA_DIR`): seasons, matches, details, follows added in the app, job history, event log, exports, backups |
| `/app/config` | `overrides.json` (settings saved on the **Settings** page), `.env`, an optional `sofascore.toml`, and the 2.x league list `leagues.txt` / `league_sports.json` |
| `/app/browser-profile` | The browser profile with the solved challenge; keeping it makes restarts fast |
| `/app/logs` | The log file (`sofascore_scraper.log`, rotated, about 30 MB at most). The same lines go to stdout (`docker logs`). See [Logs and diagnostics](#logs-and-diagnostics) |

Things to know:

- **There are no user accounts.** The examples publish the port on `127.0.0.1` only. If you publish it to your network (`-p 8000:8000`), set an access token (`-e SOFASCORE_API_TOKEN=<long random value>`): without it anyone who can reach the port can read and delete data and change settings. Inside the container the app listens on every interface and cannot see how the port is published, so without a token it logs a warning at every start; with the port on `127.0.0.1` only you can ignore it. You must also list the name or IP you open it with: `-e SOFASCORE_ALLOWED_HOSTS=localhost,127.0.0.1,my-server.lan` (requests with any other `Host` header are rejected; keep `127.0.0.1`, the health check uses it). When no allow-list is set anywhere, the entrypoint uses the loopback names. See [Security model](#security-model).
- **Settings:** every variable in `.env.example` can be passed with `-e` / `environment:`, and so can every setting of the config file as `SOFASCORE_<SECTION>__<KEY>` (see [Configuration](#configuration)). A variable set that way wins over the value saved on the Settings page, which then shows the setting as locked; so only set the ones you want fixed (for example `APP_LANGUAGE=en`, `USE_PROXY` / `PROXY_URL`). `PORT` changes the port inside the container.
- **Shared memory:** Chromium needs more than Docker's 64 MB default, hence `--shm-size=1g` (`shm_size` in Compose).
- **Bind mounts** (`-v ./data:/app/data`) work when the folder is writable by uid 1000: `mkdir -p data config && sudo chown -R 1000:1000 data config`. To use another uid, build with `--build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)`.
- **Other commands:** `serve [options]` goes to `ssc serve`; any other arguments after the image name go to `main.py`, which runs a command of the [command line](#command-line-ssc) or, for one more release, the old flags. For example `docker run --rm ghcr.io/tunjayoff/sofascore_scraper:latest --version`, or a scheduled download with the same volumes: `docker compose run --rm sofascore-scraper sync`. The browser profile can be used by one container at a time, so stop the web container (`docker compose stop`) before running a download this way; a second container on a busy profile logs a warning and cannot open its browser.
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

Optional, in the same environment:

```bash
pip install -e .              # the ssc command (otherwise: python main.py <command>)
pip install -e ".[parquet]"   # the same plus pyarrow, for Parquet exports
```

Only this editable install is supported: the version, the translations and the web app are read from the project folder.

Build the web app (needs Node.js 20.19+ or 22.12+; skip it if you only use the command line or the HTTP API):

```bash
cd frontend && npm install && npm run build && cd ..
```

Copy environment defaults and adjust:

```bash
cp .env.example .env
```

### Check your setup (doctor)

```bash
python main.py --doctor          # readable report (the same as: ssc doctor)
python main.py --doctor --json   # the same as JSON, for scripts and servers (the report is the envelope's "data")
```

The check never contacts SofaScore. Each line is `OK`, `WARN` or `FAIL`, and every problem comes with a one-line fix:

| Check | What it looks at |
|-------|------------------|
| `python` | Python 3.10 or newer. |
| `packages` | Every package in `requirements.txt` can be imported; pinned versions match. |
| `browser` | The Chromium build the bridge starts is installed **and starts** (headless, on `about:blank`, with a temporary profile). |
| `profile` | The browser profile folder is writable and not locked by another machine, a leftover browser or a dead process. |
| `data_dir`, `config_dir` | `DATA_DIR` and `config/` are writable. |
| `config` | The config file (`sofascore.toml`), when there is one, loads, and the data and log folders it names can be written. |
| `frontend` | `frontend/dist/` exists. Missing is a warning: the command line and the HTTP API work without it. |
| `env` | `.env` parses and its values are valid (numbers, `true`/`false`, proxy address, language, log level). |
| `budget` | The shared request budget: a warning when it is above the default (5 requests/s) or off. |

```text
[ OK ] Python: 3.14.0
[ OK ] Packages: all 11 required packages can be imported
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

A setting can come from several places. The later one in this list wins:

1. the built-in default;
2. `.env`, with the variable names of 2.x (the common ones are in `.env.example`; `ssc describe config` names each setting's variable as `x-legacy-env`);
3. `config/overrides.json`, which the web app's **Settings** page writes;
4. the config file `sofascore.toml`;
5. the environment: the 2.x names, and every setting as `SOFASCORE_<SECTION>__<KEY>` (for example `SOFASCORE_CLIENT__RATE=2`);
6. a command-line flag (`--data-dir`, `--rate`, …).

`ssc config show` prints every setting with its value and where it comes from (secrets masked). A setting that the config file, the environment or a flag sets is shown as locked on the **Settings** page: a change there would have no effect, so it is refused.

### Config file (`sofascore.toml`)

Optional: without it the app reads `.env` and the environment as before. It is looked for in `--config FILE`, then `SOFASCORE_CONFIG`, then `./sofascore.toml`, then `config/sofascore.toml`. An unknown key or a value of the wrong type stops the start with a message that names the file and the key. Relative paths in it are resolved against the file's folder.

```bash
ssc config init > sofascore.toml                # a starter file; the command only prints it
ssc config init --from-legacy > sofascore.toml  # today's .env and config/leagues.txt as a config file
ssc config validate                             # checks the file and the environment (exit code 2 if not valid)
ssc config path                                 # which file is in use
ssc describe config                             # every section and key, as JSON Schema
```

Sections: `[storage]`, `[client]`, `[breaker]`, `[bridge]`, `[fetch]`, `[defaults]`, `[refresh]`, `[live]`, `[schedule]`, `[server]`, `[log]`, `[display]`, `[slices.<sport>]`, and the lists `[[follow]]`, `[[sink]]` and `[[schedule.task]]`. Two values are read from the environment only, never from the file: the captcha token (`SOFA_CAPTCHA_TOKEN`) and the access token (`SOFASCORE_API_TOKEN`, or the variable that `[server] token_env` names).

```toml
[client]
rate = 5                  # requests per second, all processes together
odds_country = ""         # empty: no country is recorded with odds

[defaults]
slices = ["core"]         # data types of every sport; add "odds" for betting odds

[slices.football]
enable = ["standings"]

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

[[sink]]
name = "events-file"
type = "file"
path = "events.jsonl"
```

A follow of the config file is shown in the web app and by `ssc follows list`, but it is changed in the file only. Sinks (`stdout`, `file`, `webhook`) are configured in the file or in `SOFASCORE_SINKS`; the web app's **Outputs** page only shows whether they deliver.

### Environment (`.env`)

The 2.x variable names keep working, in `.env` and in the environment. Common ones:

| Variable | Purpose |
|----------|---------|
| `DATA_DIR` | Root folder for stored data (default `data`), for the web app and the command line alike; `--data-dir` overrides it for one run. |
| `APP_LANGUAGE` | `en` or `tr`. Empty (the default) means no language is pinned: the system language is used if it is Turkish, English otherwise. **Server language** on the **Settings** page sets the same thing. See [Language](#language). |
| `MAX_CONCURRENT` | Parallel detail requests cap. |
| `REQUEST_RATE_LIMIT` | Requests per second to SofaScore for **all processes together** (web app, CLI, `ssc watch`, every legacy `--watch` and `--refresh-only`). Default `5`; a higher value or `0` / `off` (no limit) is faster but raises the risk of being blocked. See [Request budget](#request-budget-all-processes). |
| `USE_PROXY` / `PROXY_URL` | Optional proxy: `http://`, `https://` or `socks5://`, e.g. `http://user:password@host:8080`. Also on the **Settings** page (**Requests**); the saved password is never shown again. The built-in browser uses a changed proxy after the app restarts. |
| `FETCH_ONLY_FINISHED` | Show only finished matches (`status.type == finished`) in the match lists. Default `true`. Every listed match is stored whatever its status; the setting filters what the lists show, not what is downloaded. |
| `REFRESH_WINDOW_HOURS` | Hours after kick-off during which a saved match is provisional and gets re-read (default `72`, `0` = off). See [Refresh policy](#refresh-policy). |
| `RATE_LIMIT_*` / `SERVER_ERROR_*` | Circuit breaker thresholds, counted per request across all phases of a job. See [Missing slices, failed requests and the circuit breaker](#missing-slices-failed-requests-and-the-circuit-breaker). |
| `LOG_LEVEL` / `LOG_DIR` / `LOG_TO_FILE` / `LOG_MAX_MB` / `LOG_BACKUP_COUNT` | Log level, log file location and rotation. See [Logs and diagnostics](#logs-and-diagnostics). |
| `SOFASCORE_API_TOKEN` | Optional access token for the web app and its API. Empty (the default) = off. See [Security model](#security-model). |
| `SOFASCORE_ALLOWED_HOSTS` | Host names the web app answers to, comma-separated (default `localhost,127.0.0.1,[::1]`). See [Security model](#security-model). |

The **Settings** page of the web app changes the request, data, refresh, display, logging and storage settings and writes them to `config/overrides.json`, not to `.env`. The server's address, the allowed host names, the access token, the live service and the scheduler are shown there but set in the config file or the environment only.

### Language

The app speaks English and Turkish. The command line, `ssc doctor`, the installers, the launcher and the web app all choose between them by one rule:

1. **A language you chose wins.** That is `APP_LANGUAGE=en` or `tr` in `.env` or in the environment, `[display] language` in the config file, or **Server language** on the **Settings** page. In the web app, the language picked in the top-bar menu is kept in that browser and wins there.
2. **Otherwise the system language is used.** The terminal side reads `LC_ALL`, `LC_MESSAGES` and `LANG` (on Windows without those: the display language); the web app reads the browser's language list and takes the first language it has.
3. **Otherwise English**, which is also the answer for every language other than Turkish.

A new install pins nothing: `.env.example` ships with `APP_LANGUAGE` empty, so a Turkish system or browser gets Turkish and everyone else gets English. To force one language everywhere, set `APP_LANGUAGE`. An existing `.env` with `APP_LANGUAGE=tr` keeps Turkish. `--lang en|tr` sets it for one command; JSON output is never translated.

Some log lines (console and log file) are still written in Turkish whatever the language; `argparse`'s own words in `--help` (`usage:`, `options:`) stay English.

### Request budget (all processes)

Every code path limits itself (`MAX_CONCURRENT`, the waits, the watcher's 1 s spacing), but separate processes do not see each other: a live service, a web job and a cron `ssc refresh` simply add up. `REQUEST_RATE_LIMIT` (`[client] rate`) is one budget shared by all of them. Every request to SofaScore, through curl or through the browser, first reserves the next free slot in a small state file guarded by an operating-system file lock.

- **Default: `5` requests per second for all processes together**, with up to one second of budget (5 requests) as a burst after idle time. This is deliberately far below what the download path can do on its own (about 20–60 requests/s with default settings; measured offline, reproduce with `python scripts/bench_bulk_rate.py`): it keeps the load on SofaScore low and the risk of being blocked small.
- **How long a download takes.** Match details cost 7 requests per football match (8 for tennis) with the default data selection, so a 380-match season is about 2,700 requests: roughly **9 minutes** at the default, where it used to take one to two minutes. While the budget is the limit, raising `MAX_CONCURRENT` does not make a download faster.
- **Raising it or turning it off is your call, and your risk.** Set `REQUEST_RATE_LIMIT=20` in `.env` (four times faster), `--rate 20` for one command, or change **Requests per second** under **Settings → Requests** in the web app, which the running web app applies at once; other running processes read the new value when they start. `0` or `off` removes the limit entirely: every process is on its own again and sends as fast as it can. Both make it more likely that SofaScore blocks you; the Settings page shows a warning while the value is above 5 or off, and `ssc doctor` warns too.
- **Lower it to be gentler**, e.g. `1`. At `1` or below requests are evenly spaced. A request that has to wait long for its turn is not dropped: the wait does not count against the browser bridge's 120 s request timeout.
- **Watchers** share an extra 1 request/s lane, so the polling of several watchers stays at least 1 s apart in total, not per process.
- **Where the state lives:** `~/.cache/sofascore_scraper/throttle/` (change with `SOFASCORE_THROTTLE_DIR`). Processes share the budget when they share this folder; for containers, point them at one shared volume. The **Health** page shows the budget in use.
- **Failure behaviour:** the lock is released by the operating system when a process dies, so a crash cannot leave a stale lock. If the folder is not writable or the lock cannot be taken within 1 s, requests are not blocked: that process paces itself, logs one warning and retries the file 30 s later.

### Leagues and follows

What the app downloads is a list of **follows**: leagues (tournaments), teams, players and single matches. Each follow has a sport, a season choice (`current`, `last:N`, `all` or season ids; for a team or a player the same words choose a time window) and optionally its own data selection, and can be marked `live` for `ssc watch`. A new install has none.

- **Web app:** **Add league** (in the top bar, on Overview and on **Leagues & follows**) searches SofaScore for a league, a team or a player, or takes the SofaScore id from the address (`.../premier-league/17` → `17`), then asks for the seasons and the data to download. A follow added there can be edited, disabled or removed later; removing can also delete its stored matches.
- **Command line:** `ssc follows add tournament 17 --name "Premier League" --sport football --seasons last:2 [--live]`, `ssc follows list`, `ssc follows remove KIND ID`, `ssc follows export` (prints them as `[[follow]]` tables).
- **Config file:** `[[follow]]` entries, see above.

Follows added in the web app, through the API or with `ssc follows add` are kept in the data folder (`.meta/state.db`), so they travel with the data and its backups.

**`config/leagues.txt` (2.x).** The league list of 2.x (`Name: ID` per line, plus each league's sport in `config/league_sports.json`) is still read: its leagues are followed and downloaded like any other. The app no longer adds lines to it. Such a follow shows as coming from the old list; only its sport can be changed until you **Move to here** (web app) or `PATCH /api/v1/follows/{id}` with `{"origin": "api"}`, which takes the line out of the file and makes every field editable. Removing such a follow removes its line. On a first run `config/leagues.txt` is created from `config/leagues.example.txt`, which contains no league.

`--config` names the config file. A leagues file given there (`--config leagues.txt`, the 2.x meaning) is ignored with a warning.

## Usage

### How to use the app (quick start)

**Web (recommended for most users)**

1. Finish **Installation** (`pip install`, the browser, optionally `cp .env.example .env`). Optionally set `DATA_DIR` if you want data somewhere other than `./data`.
2. Start the app: `./start-sofascore.sh` (or `python scripts/start_web.py`; on Windows double-click `Start SofaScore.bat`, on macOS `Start SofaScore.command`). The launcher creates `.venv` if it is missing, runs the [setup check](#check-your-setup-doctor), installs what is missing (Python packages, the browser), builds the web app when `frontend/dist/` is missing and Node.js is installed, then opens `http://127.0.0.1:8000`. `ssc serve` (or `python -m src.cli.main serve`) starts the server alone and installs nothing.
   After updating the code (`git pull`), rebuild the web app yourself: `cd frontend && npm install && npm run build`. The start script only builds when `frontend/dist/` is missing, so otherwise you keep seeing the old interface.
3. **Overview** — What runs, what needs attention (SofaScore refusing requests, a failed job, an output that cannot deliver) and how much is stored. A new install shows a getting-started card.
4. **Add league** — In the top bar, on Overview and on **Leagues & follows**. Search SofaScore by name (a league, a team or a player; filter by sport) or enter the SofaScore id from its address, choose the seasons and the data to download (betting odds are off unless you tick them), then **Add**. The download starts right away unless you untick that.
5. **Jobs** — Every download, export, backup and restore is a job, whether the web app, the command line or the scheduler started it. The pill in the top bar shows the running one; a job's page shows its progress and log, and **Stop** takes effect right away: no new requests are sent and retry waits are cut short; a request already in flight can take up to the request timeout (`REQUEST_TIMEOUT`) to return. Only one job writes to the data folder at a time.
6. **Leagues & follows** — Every follow with its seasons, data and how many matches have details. A follow's page has its seasons (download one season), its matches, its data selection and its jobs, and **Fetch missing details** for matches whose details are missing (typically after a stopped download). **Update all** downloads every enabled follow.
7. **Matches** — Filter by sport, league, season, team, status, date and whether details are stored; select matches to **Fetch missing data** or **Fetch again**. Open a match for its score by period, statistics, line-ups, incidents, odds (when downloaded), score changes and the raw SofaScore data. A match "in progress" shows the state of its last read: the web app has no live view.
8. **Exports**, **Backups**, **Data cleanup** — New exports (see [Exporting datasets](#command-line-ssc)) and their downloads; backups (create, download, check, restore); rebuilding the index, clearing stored data of one kind or deleting one league's data.
9. **Health**, **Logs**, **Settings** — The connection to SofaScore (**Run connection check** sends one request, only when you press it), the request budget, the live service and who holds the data folder; the log tail and the diagnostics bundle; the settings with where each value comes from.
10. **Help** (top-bar menu) explains the words used in the app; the (i) tips next to them do the same.

> **Clearing data** (**Data cleanup**, `ssc data clear`) removes stored match details, schedules or season lists and cannot be undone. Take a backup first. A backup is a zip under `backups/` in your `DATA_DIR`: the follows, the job history and the event log (`state.db`), the stored data and the change log, and `leagues.txt` / `league_sports.json` (from the command line also the config file in use). `.env` is left out because it can hold proxy credentials and the access token; **Include secrets** (web) or `ssc backup create --include-secrets` adds it, and such a backup has `_with_env` in its file name and is readable by its owner only. **Restore** on the **Backups** page, or `ssc backup restore NAME --yes`, first checks the backup and then replaces the data folder (the current data is moved to the trash first; nothing is merged). Settings files and `.env` are never restored. Backups made by 2.x can be restored too. More in [docs/deploy](docs/deploy/README.md#backups).

> **While a job holds the data folder**, backups, restores, clearing and changing the data folder are refused with a message naming it: stop the job or wait for it to finish. Changing the data folder (**Settings → Storage**) takes effect immediately (no restart): jobs and their history then use the new folder (each data folder keeps its own in `.meta/state.db`); files in the old folder are not moved.

**Command line**

The terminal menu was removed in 3.0: `python main.py` without arguments prints a short help and exits with code 2. Use the web app, or the [command line](#command-line-ssc) for scripts and servers; [this table](#from-the-terminal-menu-removed-in-30) says where each menu entry went.

**Tips**

- The first download of a big league can take a long time; start with one league and a few recent seasons.
- If you hit rate limits or many errors, lower the **request budget** (`REQUEST_RATE_LIMIT`; back to the default `5` if you raised it or turned it off) in **Settings**; avoid `--ignore-breaker` (the old `--ignore-rate-limit`) unless you know what you are doing.
- The web app and the command line use the same data folder (`DATA_DIR`); `--data-dir` changes it for one command only.
- Prefer a season that already has finished matches. The newest label (e.g. European `26/27`) is often fixtures-only.
- Stopping a download keeps everything fetched so far. Matches whose details were not reached show as missing in **Matches** (filter **Details missing**); **Fetch missing data** there, or **Fetch missing details** on the league's page, completes them.

### Troubleshooting

**Something does not start, or every download fails**

Run `ssc doctor` (or `python main.py doctor`). It names what is missing (most often the browser: `python -m patchright install chromium --no-shell`) and prints the fix. When the browser cannot start, the app does not retry for 5 minutes, so fix the cause and restart the app. See [Check your setup](#check-your-setup-doctor).

**The browser shows "The web interface is not built"**

`frontend/dist/` is missing. Build it with `cd frontend && npm install && npm run build` (Node.js 20.19+ or 22.12+) and reload the page; the server does not need a restart. Without Node.js, use a release archive from the [Releases page](https://github.com/tunjayoff/sofascore_scraper/releases) once one is published (it ships with the web app built), or copy a `frontend/dist/` folder built on another machine. The HTTP API and the command line work in the meantime.

**Seasons appear but a download finds 0 matches**

1. Confirm the SofaScore **unique tournament ID** in the URL (MLS is **`242`** — that ID is correct).
2. Not every league exposes a sequential week schedule (`events/round/1..N`). **Premier League**-style competitions do; **MLS** and some others do not:
   - MLS `/rounds` is empty or only playoff-style IDs (e.g. `227`), and `events/round/1` returns nothing.
   - The scraper must use paginated **`events/last` + `events/next`** for those seasons.
   - The app does this by itself when a season has no usable rounds. Builds of 2.x that only probed weeks `1..50` downloaded PL fine but returned **zero MLS matches**; with this version, read the season list again (item 4) and download again.
3. Matches of every status are stored, but match details are downloaded only once a match has finished, and with `FETCH_ONLY_FINISHED=true` (default) the match lists show finished matches only. If a brand-new season has no finished games yet, pick the previous season (or wait, or set `FETCH_ONLY_FINISHED=false` to list fixtures too).
4. Stale season IDs (SofaScore retired the ID after a refresh) also yield empty schedules. A download reads a league's season list again at most every 6 hours; to read it now, run `ssc sync --tournament ID --only seasons`, then download again.

### Logs and diagnostics

Everything the app logs goes to the console **and** to a log file, so the output is still there after the launcher window is closed or an overnight download has failed.

- **Where:** `logs/sofascore_scraper.log` in the project folder. Change the folder with `LOG_DIR` (a relative path is resolved against the project folder, not the current directory). The web app, the command line (`ssc`, and the deprecated `main.py` flags) and `ssc watch` all write to the same file; each line carries the process id.
- **Size:** the file is rotated at `LOG_MAX_MB` (default 5 MB) and `LOG_BACKUP_COUNT` (default 5) older files are kept as `.1` … `.5`, so logs never take more than about 30 MB. On Windows a file that is open cannot be renamed: while more than one process is writing to the log (for example the web app and `ssc watch`), it is not rotated and can grow past the limit; it is rotated again once a single process is left.
- **Level:** `LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`; `DEBUG=true` forces `DEBUG`). Changing it under **Settings → Logging** takes effect immediately in the running web app, no restart (the log folder, rotation and format need a restart). Other processes that are already running pick it up on their next start.
- **Secrets are masked** before a line is written (file and console): the captcha token, cookies, `Authorization` headers, proxy credentials (`http://user:password@host` becomes `http://***@host`) and the value of any `.env` key that looks like a secret (`*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*_KEY`, …).
- **Server / Docker:** the console (stdout) is always on and is the primary output in a container; when stdout is not a terminal the lines are plain, timestamped text. In the Docker image the file is written to the `/app/logs` volume; set `LOG_TO_FILE=false` to rely on `docker logs` only. If the folder is not writable the app says so once and continues with the console only.

**Reporting a problem:** attach a diagnostics bundle. It is a small zip with `diagnostics.json` (app version and commit, Python and OS, package versions, settings with secrets masked, bridge health, request budget, the [setup check](#check-your-setup-doctor) results (without starting the browser), the last download jobs) and `log_tail.txt` (the last 1000 log lines). Your home directory is written as `~`; values of `.env` keys the app does not know are left out. Have a look at it before you send it.

```bash
ssc diagnostics                    # writes logs/sofascore-diagnostics-<time>.zip and prints the path
ssc diagnostics --out ./report.zip # or a path / folder of your choice (python main.py --diagnostics PATH still works)
```

With the web app running (Docker included), the same bundle downloads from the **Logs** page or `http://127.0.0.1:8000/api/v1/diagnostics/bundle`, and it is the better one for "SofaScore is blocking us" reports: bridge health is per process, so only the web app's bundle carries the web app's state. `GET /api/v1/logs?limit=200&level=WARNING` returns the most recent log entries as JSON.

### Web application

```bash
ssc serve                   # or: python -m src.cli.main serve
```

Default URL: `http://127.0.0.1:8000` (`[server] host` and `port` in the config file change the defaults). The server only listens on this machine. `--host` opens it to your network; read [Security model](#security-model) first. One address (`--host 192.168.1.5`) works as it is. `--host 0.0.0.0` (every interface) also needs the allowed host names (`--allowed-hosts`, `[server] allowed_hosts` or `SOFASCORE_ALLOWED_HOSTS`) and exits with code 2 without them, and without `SOFASCORE_API_TOKEN` the app warns at startup that anyone who can reach the port can read and delete data and change settings. `--port` changes the port and `--dev` reloads on code changes. `--scheduler` / `--no-scheduler` turn the in-app scheduler on or off (below). Ctrl+C or SIGTERM stops it with exit code 0; a server that cannot start (port in use) exits with 1; configured sinks are delivered while it runs. Health: `GET /health` (also reports the version). The web app shows stored data only; live watching is `ssc watch`, a process of its own ([Watch mode](#watch-mode)). `python main.py --web` still works for one release and runs `ssc serve`. Running it as a systemd service, behind a reverse proxy, with backups: [docs/deploy/](docs/deploy/README.md).

**Scheduled downloads (optional, off by default):** `ssc serve --scheduler`, or `[schedule] enabled = true` in the config file, runs the `[[schedule.task]]` entries of the config file inside the web server: `run = "sync"`, `"fetch"`, `"refresh"` (option `league_id`), `"backup"` (options `scope`, `include_env`) or `"prune-history"`, with `every = "6h"` or `cron = "15 */6 * * *"` (the machine's local time). No task exists until you write one. Each run is an ordinary job that shows in the job list; if the previous run of the task or another download still holds the data folder, the run is skipped and logged. An `every` task counts from its last run in the job history, so restarting the server does not restart the count; missed runs are not queued up. `prune-history` needs `older_than` (for example `"90d"`) and deletes older snapshots of the kept slice history (odds), keeping the newest one of every slice. `ssc config validate` checks the tasks; `--no-scheduler` turns the scheduler off for one run, and it never runs with `--dev`. The **Health** page and `GET /api/v1/status` show the next runs. A systemd timer or cron running `ssc sync` does the same from outside the server ([docs/deploy](docs/deploy/README.md#scheduled-downloads)).

Jobs report their state through `GET /api/v1/jobs` and `GET /api/v1/jobs/{id}/events` (SSE). Heavy API work runs off the asyncio event loop so the UI stays responsive during long fetches.

### Security model

The web app has **no user accounts**. By default it listens on this computer only, and that is the setup it is made for. Opening it to a network is your decision as the administrator, and so is limiting who can reach it (firewall, VPN, reverse proxy). The app does not undo that work, and it defends against attacks that arrive through your own browser, which no firewall stops.

**What the app does**

- **Answers to known host names only** (DNS rebinding). A request is served only if its `Host` header is on the allow-list: `localhost`, `127.0.0.1` and `[::1]` by default. `SOFASCORE_ALLOWED_HOSTS` (comma-separated, in `.env` or the environment) replaces the list and is always used exactly as written. `--host 192.168.1.5` adds that one address by itself. `--host 0.0.0.0` (every interface) does not start until `SOFASCORE_ALLOWED_HOSTS` says which names to answer to. `--allow-any-host` (or `SOFASCORE_ALLOWED_HOSTS=*`) answers to any name; this is **insecure** and turns the protection off.
- **Refuses writes triggered by other sites** (CSRF). No `GET` endpoint changes anything. Every state-changing request (start or stop a download, save settings, delete data, back up, search SofaScore) is answered with `403` when the browser reports that another site sent it (`Sec-Fetch-Site`, `Origin`). Programs that send neither header, such as curl, are not affected.
- **Sends security headers** with every response: a Content-Security-Policy (scripts, styles, fonts and requests only from the app itself; no inline script, no `eval`, no framing), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and `Cross-Origin-Resource-Policy: same-origin`. Two exceptions: a web app build made before this policy existed still needs `eval`, so it is served with `'unsafe-eval'` (and a warning in the log) until you rebuild it; and the API documentation pages `/docs` and `/redoc` load their scripts from a CDN and get a policy of their own.
- **Optional access token.** Off by default. Set `SOFASCORE_API_TOKEN` to a long random value and restart the app; `python -c "import secrets; print(secrets.token_urlsafe(32))"` makes one. From then on every `/api` request needs it. Programs send `Authorization: Bearer <token>`. The web app asks for it once and keeps an `HttpOnly`, `SameSite=Strict` session cookie for 30 days (the job event streams use the same cookie); **Sign out** in the top-bar menu ends the session, and changing the token ends all of them. `GET /health` stays open for health checks, but without the token it answers only `{"status": "ok"}`. The token is compared in constant time and never appears in the log, the diagnostics bundle or an API response.
- **Warns once at startup** when it listens on a non-local address without a token.
- **Keeps secrets private on disk.** `.env` and `config/overrides.json` (proxy password, tokens) are created with mode `0600` and the browser profile folder (SofaScore cookies) with `0700`; existing ones are tightened at every start. A backup that includes `.env` says so in its file name (`backup_…_with_env_….zip`) and is readable by its owner only. On Windows the files rely on the permissions of your user folder instead. The proxy password is masked in the settings API, the logs, the diagnostics bundle and in error messages returned by the API.

**What you must do before opening it to a network**

- Set `SOFASCORE_API_TOKEN`. Without it, anyone who can reach the port can read and delete your data and change the settings.
- Limit who can reach the port (firewall, VPN). Wrong tokens are limited per client address and only in memory (behind a reverse proxy see [docs/deploy](docs/deploy/README.md#behind-a-reverse-proxy)), so the token must still be long and random.
- Put TLS in front of it. The app speaks plain HTTP: without a reverse proxy that terminates TLS, the token and the session cookie cross the network unencrypted. The proxy must pass the original `Host` header on, or the name it sends must be in `SOFASCORE_ALLOWED_HOSTS`.
- With Docker, the container always listens on every interface inside its own network and `-p` decides who can reach it, so the startup warning appears even when the port is published on `127.0.0.1` only (then it can be ignored): if you publish the port beyond `127.0.0.1`, set the token and `SOFASCORE_ALLOWED_HOSTS` yourself.

```bash
curl -H "Authorization: Bearer $SOFASCORE_API_TOKEN" http://127.0.0.1:8000/api/v1/follows
```

### Command line (`ssc`)

The command line for servers and automation. It never asks questions: the result goes to stdout, logs and errors go to stderr, and the exit code tells what happened. `ssc <command>` after `pip install -e .`, or `python main.py <command>` / `python -m src.cli.main <command>` without installing.

```bash
ssc sync                                   # every enabled follow: season lists, schedules, then the matches that need details
ssc sync --tournament 17 --only events     # one league, match details only
ssc sync --follow team:42                  # one follow (tournament:ID, team:ID, player:ID, event:ID)
ssc sync --tournament 17 --only seasons    # read the season list again now
ssc sync --dry-run                         # sends nothing: what would be fetched and at least how many requests
ssc fetch event 12345678 12345679          # these matches, configured or not
ssc fetch tournament 17 --season 61627     # one tournament (or every season of it)
ssc refresh [--tournament 17] [--include-legacy]   # re-read provisional records only (a daily cron)
ssc export --dataset events --format csv --out events.csv   # a dataset (events, slices, changes, odds, standings)
ssc export --out matches.csv               # the wide CSV (legacy-wide-csv); without --out: match_details/processed/
ssc export --schema raw --format jsonl --out raw.jsonl   # the stored payloads as they are
ssc data recheck-unavailable [--all]       # reopen "slice not available" markers (no request)
ssc data clear --scope events --yes        # delete match details, old and new layout alike
ssc follows list | add | remove | export   # what is downloaded (and, with --live, watched); export prints [[follow]] tables
ssc status [--coverage] [--disk] [--check] # data summary, store, locks, running and last job, live service
ssc jobs list | show ID | cancel ID | tail ID [--follow]   # job history and control, across processes
ssc serve [--host H] [--port P] [--scheduler]   # the web app and the HTTP API (see Web application)
ssc watch / ssc events                     # live service and the event log (see Watch mode)
ssc backup create | list | verify NAME | restore NAME --yes   # backups of the data folder
ssc doctor | describe | config | version | diagnostics | migrate | catalog
```

- **Machine-readable output.** `--json` prints exactly one JSON document (`{"ok", "command", "schema": "sofascore.cli/1", "version", "data" | "error", ...}`; `ssc describe schemas` has its schema). Streaming commands (`events`, `jobs tail`) write one JSON object per line, each with a `type`, and end with a `{"type": "end", ...}` line; an error in the middle of a stream is a `{"type": "error", ...}` line. A reader that closes the pipe early (`ssc events | head -1`) is not an error.
- **Global flags** (before or after the command): `--config FILE`, `--data-dir DIR`, `--json` / `--output text|json|ndjson`, `--quiet`, `--verbose`, `--log-level`, `--log-format text|json` (log lines on stderr as one JSON object each), `--no-color`, `--lang en|tr`, `--rate N|off`, `--ignore-breaker`, `--wait SECONDS` (wait for a busy data folder instead of exiting with 6), `--progress none|text|ndjson` (a job's progress on stderr).
- **Exit codes:** **0** success or nothing to do, **1** general error (also `export` with nothing to export, an unknown job id, `status --check` on an unhealthy store), **2** usage or configuration error, **3** partial success (the job finished but some matches or lists could not be fetched), **4** SofaScore is blocking: the circuit breaker stopped the job, **5** storage error (disk full, no permission), **6** another process holds the data folder (the error names it), **130** / **143** cancelled by Ctrl+C / SIGTERM.
- **Stopping a job.** Ctrl+C or SIGTERM cancels a running `sync`, `fetch` or `refresh`: requests stop at the next check, the match being written is written whole or not at all, the job is stored as `cancelled`, the lock is released and the result is still printed. A second Ctrl+C exits at once. `ssc jobs cancel ID` cancels a job from any other process (the web app's jobs too).
- **One writer per data folder.** Downloads and `data recheck-unavailable` hold the folder's writer lock; while it is held elsewhere they exit with **6** and the holder (process, host, purpose, since when). Reading commands (`status`, `events`, `export`, `jobs list`) take no lock.
- **Sinks.** The `[[sink]]` outputs of `sofascore.toml` (stdout, file, webhook) also receive the events of one-shot jobs (`job.started`, `job.finished`): they are registered before the job and drained for up to 10 s after it. `ssc config validate` checks them.
- **Exporting datasets.** `ssc export --dataset events|slices|changes|odds|standings` writes the records of the data schema (version 1; field by field in [`docs/design/04-schema-v1.md`](docs/design/04-schema-v1.md), as JSON Schema in `ssc describe schemas`): `events` are the matches with status, score, winner and how reliable the record is, `slices` the state of each stored response about a match (statistics, lineups, …; the payloads themselves are in the raw export), `changes` the corrections found in stored matches, `odds` one row per outcome of each stored odds snapshot, `standings` the standings rows of the seasons of the chosen matches (odds and standings exist only when those data types were downloaded). `--format jsonl` (the default) writes one record per line exactly as the API returns it; `csv`, `parquet` and `sqlite` write one column per field, named by its path (`status_class`, `score_home`, `quality_observed_at_utc`), with lists as JSON text and an empty cell for a missing value. Parquet needs the optional package `pyarrow` (`pip install -e ".[parquet]"`). Filters: `--sport`, `--tournament`, `--season`, `--event`, `--status` (status class), `--from` / `--to` (ISO dates, UTC; for `changes` the time the correction was recorded). Without `--out` the file goes to the data folder's `exports/` (`<dataset>_<time>.<format>`), where the web app's **Exports** page lists it too; `--out -` writes JSONL or CSV to stdout; an existing file is replaced only with `--force`. `--schema raw` writes the stored SofaScore payloads (`--dataset events`: the match payload only; without `--dataset`: every payload). The `--json` result names the `schema_version` of the records. Without `--dataset` or `--schema`, `ssc export` still writes the 2.x wide CSV (to `match_details/processed/` unless `--out` says otherwise). The web app's **Exports** page starts the same exports as jobs; their files are named after the league or dataset and the date (`premier-league_2026-10-06_x7k2m9qa.csv`).

### From the terminal menu (removed in 3.0)

`python main.py` no longer opens a menu: people use the web app, and the command line is for servers and automation. Without arguments it prints a short help (the web app is `ssc serve`, the commands are in `ssc --help`) and exits with code **2**. Every menu entry has a home:

| Menu entry | Now |
|------------|-----|
| Leagues: list, add, reload, search | Web app **Add league** and **Leagues & follows** (search SofaScore, add, edit, remove); `ssc follows list`, `ssc follows add tournament ID --name NAME --sport SPORT`, `ssc follows remove tournament ID`; `[[follow]]` in the config file. Every command reads the configuration when it starts, so there is nothing to reload. |
| Seasons: update all, update one league, list | `ssc sync` (season lists, schedules, then match details), `ssc sync --tournament ID --only seasons`, `ssc fetch tournament ID`; web app **Leagues & follows** (a league's page lists its seasons); `GET /api/v1/tournaments/{id}/seasons` |
| Matches: fetch one league, all leagues, list | `ssc fetch tournament ID --season ID`, `ssc sync --tournament ID`, `ssc sync`; web app **Leagues & follows** (**Download now**, **Update all**) and **Matches**; `GET /api/v1/events` |
| Match details: fetch by id, fetch all | `ssc fetch event ID…`, `ssc sync --only events`; web app **Matches** (**Fetch missing data**, **Fetch again**) and **Jobs → Start a job → Fetch missing details** |
| Match details: CSV of one match, one league, all | `ssc export --event ID`, `ssc export --tournament ID`, `ssc export` (`--out PATH` picks the file); web app **Exports** (match table CSV) |
| Statistics: system, leagues, report file | `ssc status` (`--coverage` adds matches and details per tournament; `--json > report.json` writes a report file); web app **Overview** |
| Settings: API, data folder, display, language | Web app **Settings**; `sofascore.toml` or `.env` (`ssc config show` lists every value and where it comes from); `--lang` |
| Settings: move the data folder | Stop the app, move the folder, then point `DATA_DIR` (web app **Settings**, `.env` or `--data-dir`) at the new place. |
| Settings: backup, restore, clear | `ssc backup create`, `ssc backup restore NAME --yes`, `ssc data clear --all --yes`; web app **Backups** (create, download, check, restore) and **Data cleanup** |
| Settings: about | `ssc version` |

### Headless / automation (deprecated flags)

The flags of `python main.py` keep working for one release. Each run is translated into a command of the [command line](#command-line-ssc) and prints one line on stderr that names it (`--headless --update-all` is `ssc sync`, `--refresh-only` is `ssc refresh`, `--headless --csv-export` is `ssc export`, `--recheck-unavailable` is `ssc data recheck-unavailable`, `--watch` is `ssc watch --source poll --stdout`, `--doctor` is `ssc doctor`, `--diagnostics` is `ssc diagnostics`, `--web` is `ssc serve --host 127.0.0.1 --port 8000` with the `--host`, `--port`, `--dev` and `--allow-any-host` it was given); it uses that command's output rules and exit codes. The terminal menu is gone: `python main.py` without flags prints a short help and exits with code **2** ([what replaces the menu](#from-the-terminal-menu-removed-in-30)). At least one of `--update-all` or `--csv-export` is required with `--headless`. Otherwise the process exits with code **2** before anything runs.

| Flag | Meaning |
|------|---------|
| `--headless` | Run an action (the 2.x flag that skipped the terminal menu) |
| `--update-all` | Run a download (`ssc sync`) |
| `--fetch-mode full` | Seasons + match lists + details (default) |
| `--fetch-mode details` | Match details only, for the schedules already stored (`ssc sync --only events`) |
| `--league-id ID` | Limit `--update-all`, `--refresh-only` or `--recheck-unavailable` to one league (`--tournament ID`) |
| `--csv-export` | Write the 2.x wide CSV (`ssc export`) |
| `--ignore-rate-limit` | Disable the circuit breaker (`--ignore-breaker`; use with care) |
| `--refresh-only` | Only re-read provisional records (no `--headless` needed); see [Refresh policy](#refresh-policy) |
| `--refresh-legacy` | Also refresh records saved before `observation.json` existed, once |
| `--recheck-unavailable [legacy\|all]` | Reopen "this slice does not exist for this match" markers so the next download asks again; sends no requests itself. See [Missing slices](#missing-slices-failed-requests-and-the-circuit-breaker) |
| `--watch` | Live watcher with `--sport` and `--league-ids` or `--event-ids` (`--watch-hours` optional); see [Watch mode](#watch-mode) |
| `--doctor` | Check the environment and exit, `0` = ready, `1` = something failed (no `--headless` needed); see [Check your setup](#check-your-setup-doctor) |
| `--diagnostics [PATH]` | Write the diagnostics bundle (`ssc diagnostics`) |
| `--web [--host H] [--port P] [--dev] [--allow-any-host]` | The web app (`ssc serve`), always on 127.0.0.1:8000 unless `--host` / `--port` say otherwise |

Examples:

```bash
python main.py --headless --update-all
python main.py --headless --update-all --fetch-mode details --league-id 52
python main.py --headless --csv-export --data-dir ./data
```

Exit codes are those of the [command line](#command-line-ssc): **0** success, **1** general error, **2** usage error, **3** partial success, **4** the circuit breaker stopped the run, **5** data could not be written, **6** another process is already writing to the same data folder, **130** / **143** cancelled. Before 3.0 a breaker stop was **2**, a storage error **1**, Ctrl+C **0**, and a run in which every request was refused or a CSV export with nothing to export ended with **0**.

Only one process writes to a data folder at a time. While a download runs in the web app or in another headless run, `--headless --update-all`, `--refresh-only` and `--recheck-unavailable` do not start: they print who holds the lock (process id, host, purpose, since when) and exit with **6**. The same goes for `--watch` while another live service or watcher runs. `--headless --csv-export` on its own is not affected. `--config` now names the configuration file (`sofascore.toml`); a leagues file (`.txt`) given there is ignored with a warning, as it always was.

### Command-line help

```bash
python main.py --help            # the deprecated flags
python main.py sync --help       # a command of the new command line (or: ssc sync --help)
python main.py --version
```

## Data layout (under `DATA_DIR`)

Typical structure:

```text
data/
├── .meta/
│   ├── catalog.db     # The index of the stored files (rebuilt with ssc catalog rebuild)
│   ├── state.db       # Follows added in the app, job history, event log, live state
│   ├── locks/         # Who holds the data folder (writer, live service, maintenance)
│   └── trash/         # Data moved aside by a restore, until it has finished
├── v3/
│   ├── events/        # Match details, one folder per match: manifest.json + compressed slices (*.json.gz)
│   ├── tournaments/   # Season lists, match schedules and season data per league and season (compressed)
│   └── teams/, players/, sports/   # Team, player and sport data, when those data types are selected
├── changes/           # Post-finish changes found by refresh, one file per month (see Refresh policy)
├── exports/           # Files written by exports (web app, API, ssc export)
├── backups/           # Backups (web app, API, ssc backup create, the scheduler)
├── match_details/     # Per-match JSON folders written by 2.x (basic, stats, lineups, …)
│   └── processed/     # The 2.x wide CSV export
├── seasons/, matches/ # Season lists and schedules written by 2.x (a new data folder does not have them)
├── datasets/          # Reserved / auxiliary
└── score_changes.jsonl  # Change log of 2.x (kept and read, no longer appended to)
```

Match details are stored under `v3/events/<id / 1,000,000>/<(id / 1,000) mod 1,000>/<id>/`, compressed. A path depends only on the id, so renaming a league moves nothing. Folders under `match_details/` written by older versions stay where they are and stay readable in the app; when such a match is written again (a refill, a refresh, a marker reset), its current state is first copied to `v3/` and the old folder is left untouched. Programs that read `match_details/` directly do not see matches downloaded by this version.

### Moving old data to the new layout (`ssc migrate`)

Data written by older versions is never moved on its own. `ssc migrate` converts it when you ask for it: match folders under `match_details/`, the round and page files under `matches/`, the season lists under `seasons/` and `score_changes.jsonl` (copied to `changes/0000-legacy.jsonl`, with the same sequence numbers). Every new copy is read back and compared with the old one before it is put in place. The old files are kept unless you also ask to delete them; that can be done in the same run or later.

```bash
ssc migrate --dry-run                   # what would be converted, the size before and after; changes nothing
ssc migrate                             # convert and verify; the old files stay where they are
ssc migrate --tournament 17 --limit 200 # one league, at most 200 matches; run it again to continue
ssc migrate --delete-legacy --yes       # also delete every old copy whose new copy was verified again
ssc migrate --purge-derived --yes       # delete season summaries of seasons that have schedules, and match_details/processed/
```

- Files in a match folder that are not match data (your own notes, for example) are copied unchanged into `_extra/` of the new folder.
- A season that has only summary CSV files (no round or page file) cannot be converted: it stays where it is, stays readable and is listed. So are folders and files that are not recognised.
- The command is refused while a download, `ssc watch` or a `--watch` runs on the same data folder (exit code **6**). If some items fail, the run finishes, those items keep their old copy, and the exit code is **3**. `--json` prints the full result.
- Before it starts, the command mirrors `config/leagues.txt` into the follows (as the app does on every start): a season list named after the league only (`<name>_seasons.json`) is resolved that way.
- On the owner's data (423 matches with details, 90 schedule pages, 6 season lists) the converted files take 7.2 MB instead of 71.7 MB; the run took about 3 seconds.

`ssc catalog verify [--deep] [--repair]`, `ssc catalog reconcile [--deep]` and `ssc catalog rebuild` check, update or rebuild the index of the stored files (`.meta/catalog.db`). They replace `scripts/catalog_tool.py`; `scripts/migrate_match_details.py` (which renamed league folders in place) is gone as well, since the new layout no longer depends on folder names.

The match list reads the index of the data folder (`.meta/catalog.db`), which covers the 3.0 layout and the files of 2.x alike; the export CSV in `match_details/processed/` is never read back.

Next to `config/leagues.txt` (the 2.x `name: id` list, see [Leagues and follows](#leagues-and-follows)), `config/league_sports.json` stores the sport of each of its leagues as `{"<id>": "<sport>"}` (`football`, `ice-hockey`, `table-tennis`, …; `ssc describe sports` lists them). It is filled when you pick a sport for such a league in the web app or from a downloaded match of that league.

The supported sports are defined in one place, the registry in `src/sports.py`: per sport its score shape, the live watcher's parameters and the data types (slices) that can be requested for it. The CLI, the downloader, the watcher and the web API all read it; `ssc describe sports` and `ssc describe slices` print it as JSON.

### Data types (slices)

Each part downloaded for a match, a season, a team, a player or a sport is a data type. `ssc describe slices` and `GET /api/v1/sports` list every one with its group, its owner and the sports it applies to.

- **On by default** (group `core`): statistics, team streaks, pregame form, head to head, line-ups and incidents where the sport has them, tennis and darts point by point, cricket innings and e-sports games.
- **Off until you select them:** betting odds (`odds`: featured and all markets, odds changes, winning odds, from the bookmaker set in `[client] odds_provider`), `standings`, `season` (season info, cup tree), `leaders` (top players and teams), `rankings` and `players` (season statistics of followed players). Season, team, player and sport data are fetched once per owner, not per match.
- **Where to choose:** for every sport with `[defaults] slices` (or **Settings → Data** in the web app), per sport with `[slices.<sport>] enable / disable`, per follow with its own selection (**Data to download** on the follow's page, or `slices` in `[[follow]]`). A data type that is not selected is never requested, and a match is complete when the selected ones are stored.
- **Odds** are snapshots: before kick-off they are read again on each download once they are 30 minutes old (in the week before the match), and once more after the match; every changed read is kept in the slice's history. `[client] odds_country` (empty by default) records a country code with every odds read; it is never derived from the machine. The `prune-history` scheduler task deletes old snapshots.

### Missing slices, failed requests and the circuit breaker

Each match folder holds one file per detail slice (`statistics`, `lineups`, `incidents`, …) and, per slice, two kinds of bookkeeping (in the match's `manifest.json`; folders of older versions keep them in `_unavailable.json` and `_slice_status.json`):

- an "empty" count: how often SofaScore gave a **definitive** "nothing here" answer for a finished match: HTTP 404, or a 200 response with no data in it. After two such answers the slice is no longer expected for that match (tennis has no lineups, for example) and the match counts as complete.
- an error mark: the last **failed** request for the slice: `reason` (`403`, `429`, `5xx`, `timeout`, `network`, `parse`), the HTTP status, the UTC time and how many times in a row. A failed request is never counted as "not available": the slice stays expected, the match stays incomplete, and the next download asks for it again.

Earlier releases counted every empty result, including requests that failed during a block or an outage. Those markers cannot be told apart from genuine ones, so they are left alone and are **not** reset automatically: that would re-request every "no lineups" tennis match on the next run. To re-check them:

```bash
ssc data recheck-unavailable                   # reopen markers not confirmed by a definitive answer
ssc data recheck-unavailable --tournament 17   # one league
ssc data recheck-unavailable --all             # reopen every marker
ssc sync --only events --recheck-unavailable   # reopen, then download (all: --recheck-unavailable all)
```

(`python main.py --recheck-unavailable [all] [--league-id 17]` still works for one release.) The command itself sends no requests; the reopened slices are requested by the next details download. Markers confirmed by a definitive answer are kept, so running it a second time changes nothing.

**Circuit breaker.** One breaker per job counts the final outcome of every request: season lists, match lists, `/event`, each detail slice and refreshes. It trips after `RATE_LIMIT_THRESHOLD_CONSECUTIVE` failed requests in a row (default 20), when `RATE_LIMIT_THRESHOLD_RATIO` of all requests have failed (default 0.9, after the first 50), or after `SERVER_ERROR_THRESHOLD_CONSECUTIVE` 5xx answers in a row (default 50). It also trips early when the browser bridge turns `blocked` during the job (see [bridge health](#is-sofascore-blocking-us-bridge-health)) and the job's own requests keep ending in 403. A 404 is an answer, not a failure. Once tripped, the job sends no further requests in any phase and says why: the job's page in the web app shows it, and `ssc sync`, `ssc fetch`, `ssc refresh` (and the deprecated `--headless` and `--refresh-only`) exit with code 4. `--ignore-breaker` turns the breaker off for one run.

**Storage errors.** A match whose files cannot be written is reported as failed, not as downloaded. If the cause will repeat for every match (disk or quota full, permission denied, read-only file system), the job stops with a message naming the path and the reason.

Every match is stored by its id, also one without a SofaScore unique-tournament id (older versions put those under `match_details/_no_tournament/<sport>/<match id>/`; such folders stay where they are).

## Refresh policy

SofaScore keeps editing some results after a match has finished. In the research run (`docs/status-matrix/README.md`, "Geriye dönük"), the final or period score changed after `finished` in 78 of 255 lower-tier basketball matches, 6 of 240 lower-tier football matches and 4 of 145 upper-tier basketball matches. The latest final-score change came 66.4 h after kick-off. A match downloaded once can therefore differ from SofaScore's own final state.

- **When a record is refreshed.** Every saved match records when we read it (`observed_at_utc`) and SofaScore's `changes.changeTimestamp` (in `manifest.json`; older folders: `observation.json` next to `basic.json`). A record is *provisional* while `observed_at_utc < startTimestamp + REFRESH_WINDOW_HOURS` (default **72**). On the next download (a web job, `ssc sync`, or `ssc refresh`), provisional records are re-read. Only `/event/{id}` is fetched; the stats and lineups are not. Refreshes run after new and incomplete matches, and the job counts them separately ("N refreshed (M changed)"). A record is re-read at most once every `REFRESH_MIN_INTERVAL_HOURS` (default 6, advanced `.env` setting), so hourly downloads do not fetch the same match 72 times. Once a read lands after the window, the record is final and never fetched again.
- **Older records.** Matches saved before this feature have no `observation.json`. They count as final, so the default setting adds **no** requests for existing data. `ssc refresh --include-legacy` (the old `--refresh-legacy`) re-reads each of them once.
- **Turning it off.** Set `REFRESH_WINDOW_HOURS=0` (in `.env` or under **Settings**).
- **Change log.** When the status triple, `winnerCode`, any `homeScore`/`awayScore` field or `startTimestamp` differs, the stored match page is replaced and one line is appended to `DATA_DIR/changes/<yyyy>-<mm>.jsonl` (ignored by git; older versions appended to `score_changes.jsonl`, which stays and is still read). The line is written in the same step as the match, so an interrupted write cannot lose it: it is added on the next write of that match or the next start.

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
  - If a match that counted as played turns void (`completed` → `void`, e.g. cancelled afterwards), the line carries `"status_regressed": true`. The same flag is kept with the match's observation and never cleared. The record is **not** deleted; that decision is yours.

```bash
ssc refresh                      # re-read provisional records only (e.g. a daily cron)
ssc refresh --tournament 17      # one league
ssc refresh --include-legacy     # also old records without an observation, once
```

`python main.py --refresh-only [--league-id 17] [--refresh-legacy]` still works for one release. The web app lists the recorded changes on **Score changes**; `GET /api/v1/changes` and `ssc export --dataset changes` return them.

## Watch mode

`ssc watch` is the live service: one foreground process for every watched sport. It follows live matches and writes **events**; it does not settle anything. The events are stored in the data folder's event log, where `ssc events` and the configured sinks (`[[sink]]`: stdout, file, webhook) read them; `--stdout` also prints them, one JSON envelope (`sofascore.event/1`) per line. A consumer decides what to do with them. The web app has no live view: its **Health** page only shows whether the service runs.

```bash
ssc watch                                        # the follows marked live (ssc follows add ... --live, live = true in [[follow]])
ssc watch --sport football --tournament 17 --tournament 8 --stdout   # every live match of these leagues
ssc watch --sport tennis --event 17196038 --hours 3 --source poll
ssc events --follow --type 'live.*'              # read the event log as it grows
```

The legacy `python main.py --watch --sport S --league-ids A,B | --event-ids X [--watch-hours H]` keeps working for one release as `ssc watch --source poll --stdout` (polling only: it never starts a browser). Only one live service runs per data folder (exit code **6** for a second one).

The polling described here is what `--source poll` does, and the fallback of the other sources (see [Live sources](#live-sources-of-ssc-watch)):

- **How it polls.** Every `[live] poll_interval_seconds` (default 30 s) one request to `/sport/{sport}/events/live`, plus `/event/{id}`:
  - immediately when a tracked live match drops out of the live list (the earliest end signal);
  - every 30 s while a match is near its end;
  - every 5 min for a stuck match.
- **Near the end:**
  - football: 2nd half from minute 80 or once `injuryTime2` appears;
  - basketball: `played ≥ 90%` of regulation, or the last period when there is no clock data;
  - tennis: the deciding set.
- **Rate budget.** At most `[live] max_event_polls` (`WATCH_MAX_EVENT_POLLS`, default 20) match pages per round, requests at least 1 s apart. The spacing is shared by every watcher on the machine (see [Request budget](#request-budget-all-processes)), so one watcher per sport still stays under 1 request/s in total; with several busy watchers a round can take longer than 30 s. If more matches are near the end than that, match pages drop to every 60 s and a warning is logged.
- **Stuck match.** Still live or not started 4 h after kick-off (tennis: 6 h after the real first-set start, since its `startTimestamp` is only the scheduled slot; set durations exclude breaks such as rain delays, so this start can come out late and `stuck` fires a little later): one `stuck` event, then polled every 5 min. If it turns void and its start time has moved (suspended tennis continues the next day with the same id), it stays tracked.
- **Events.** `live.status_changed` (`from`, `to`, `change_ts`, `score`; the first `completed` carries `provisional: true` until the refresh window closes, see [Refresh policy](#refresh-policy)), `live.score_changed` and `live.stuck`, each with `event_id`, `sport`, `tournament_id`, `seq` and `ts`. `DATA_DIR/watch_events.jsonl` and `watch_state_{sport}.json` are no longer written; old files can be deleted.
- **Restarts.** The last known state per match is kept in the data folder's store (`.meta/state.db`), so a restart does not emit the same transition twice. Ctrl+C or SIGTERM stops cleanly with exit code 0.
- **When it exits.** With `--event` only (legacy `--event-ids`), the service exits when every tracked match is over; `--hours` stops it after that many hours.

Why these numbers: `events/live` is cached for 5 s at the CDN, and whistle → `finished` took a median of 20 s (max 302 s) in the research (`docs/status-matrix/README.md`). Polling faster than 30 s gains nothing.

### Live sources of `ssc watch`

`ssc watch`, the live service of the new command line, picks its source with `--source`, with `[live] source` in `sofascore.toml` or with `SOFASCORE_LIVE__SOURCE` (the legacy `main.py --watch` above always polls):

| Source | How it works | Memory |
|---|---|---|
| `page` (default) | keeps one browser page per watched sport open and listens to the push connection that SofaScore's own page opens; it never reads that connection's credential | about 1.8 to 2.6 GB per sport |
| `poll` | polling only, as described above; no browser | nothing extra |
| `direct` (explicit opt-in) | a light client connects to the push server itself, with the credential read from the page's own connection | about 0.2 GB |

Polling is the fallback of every source and is always running: slowly while push is healthy, every `poll_interval` when the connection is silent or dropped, and once after every reconnect. When `page` cannot open or crashes, the service keeps polling; it never switches to `direct`.

**`direct` is never chosen for you.** It is used only when you write `direct` yourself (`--source direct`, `[live] source = "direct"` or `SOFASCORE_LIVE__SOURCE=direct`); there is no automatic value. Before you choose it, know that:

1. it uses SofaScore's own client credential outside the site's client;
2. it may break without notice when the credential or the server changes;
3. it may get your IP address blocked;
4. it is a terms-of-use grey area that you choose knowingly.

The same four points are printed by `ssc watch --help`, `ssc describe config`, `ssc config validate` and `ssc config show`, and logged every time the service starts with this source.

How `direct` behaves:

- **Credential.** A browser (profile `<profile>-live`) opens one sport page only to read the credential from the `CONNECT` frame of that page's own connection, then closes. The credential is kept in memory only: it is never written to disk, `state.db`, the log, an event or the diagnostics bundle, and it is masked if it ever appears in a log line. When the server rejects it, it is read again from a fresh page; after five failed attempts in a row the source counts as unhealthy, the service keeps polling, and the source retries at most every 30 minutes.
- **On the wire.** One connection; it only subscribes (`sport.<sport>` for each watched sport), never publishes and never uses wildcard subjects; it sends a PING every 120 s, as the site's client does. The server drops the connection about every 30 minutes; the client reconnects with back-off and subscribes again.
- **Proxy.** With a proxy configured (`USE_PROXY` / `PROXY_URL`), `direct` does not connect, because the connection would bypass the proxy; the service polls instead.

What was measured and what was not (`docs/push-channel/README.md`, section 7): one evening, one region, one connection, the single subject `sport.football`, about 38 minutes and one reconnect; the client used 216 to 226 MB and a plain client was accepted. Not measured: several subjects on one connection, runs of hours, several sports, and how often the credential changes.

Running `ssc watch` as a systemd service or in a container, with the memory each source needs: [docs/deploy/watch.md](docs/deploy/watch.md).

## HTTP API (overview)

The versioned API is under `/api/v1`; the web app uses nothing else. Its contract is recorded in [`docs/api/openapi-v1.json`](docs/api/openapi-v1.json), and the running server documents it at `GET /docs` and `/redoc`. A single resource is answered as `{"data": {...}}`, a list as `{"data": [...], "page": {"limit", "next_cursor"}}`, an error as `{"error": {"code", "message", "details", "request_id"}}` with a stable code and an English message; every response carries `X-Request-Id`.

- **Service**: `GET /api/v1/health`; `GET /api/v1/status` (the connection to SofaScore, the bridge health, the request budget, the live service, the scheduler's next runs, what the data folder holds, who holds it); `POST /api/v1/status/check` (one request to SofaScore, only when called); `GET /api/v1/sports`, `/sports/{slug}` (the sports and their data types); `GET /api/v1/sinks` (read-only).
- **Follows**: `GET`, `POST /api/v1/follows`; `GET`, `PATCH`, `DELETE /api/v1/follows/{kind}:{id}` (`?delete_data=true` also deletes the stored matches of a league). `POST /api/v1/tournaments/search` searches SofaScore for tournaments, and with `kinds: ["team", "player"]` for teams and players (a `POST` because every call sends a request to SofaScore). A follow from the config file is listed but can only be changed in the file (`409 follow_managed`).
- **Data**: `GET /api/v1/tournaments`, `/tournaments/{id}`, `/tournaments/{id}/seasons` (`?include=counts`), `/seasons/{id}`, `/seasons/{id}/slices`, `/seasons/{id}/slices/{key}`, `/seasons/{id}/standings`, `/events` (filters by sport, tournament, season, team, status, date, stored details, name and follows), `/events/{id}`, `/events/{id}/slices`, `/events/{id}/slices/{key}`, `/events/{id}/odds`, `/events/{id}/odds/{key}`, `/changes`. Records follow the normalized schema v1; `/events/{id}/raw` and `/events/{id}/slices/{key}/raw` return the stored SofaScore payload unchanged.
- **Jobs**: `GET`, `POST /api/v1/jobs` with `kind` `sync`, `fetch`, `refresh`, `export`, `backup`, `clear` (needs `confirm: true`), `rebuild` or `restore` (`dry_run: true` by default; `false` restores); `GET /api/v1/jobs/{id}`, `POST /api/v1/jobs/{id}/cancel`, `GET /api/v1/jobs/{id}/events` (server-sent events, resumable with `Last-Event-ID`). Jobs started from the command line or the scheduler are listed too and can be cancelled. While another job holds the data folder, a job that needs it is refused with `409`, naming the holder.
- **Exports and backups**: an export is a job, for example `{"kind": "export", "spec": {"dataset": "events", "format": "parquet", "filter": {"tournament_ids": [17]}}}` (a Parquet export without `pyarrow` on the server answers `501 not_supported`); then `GET /api/v1/exports` and `/exports/{id}/download`. `GET /api/v1/backups` and `/backups/{name}` (download).
- **Settings**: `GET /api/v1/settings` (each value, where it comes from, whether it is locked); `PATCH /api/v1/settings` writes `config/overrides.json` and refuses a value that the config file, the environment or a flag pins.
- **Logs and diagnostics** (read-only, see [Logs and diagnostics](#logs-and-diagnostics)): `GET /api/v1/logs` (`limit` 1–2000, `level` = minimum level), `GET /api/v1/diagnostics`, `GET /api/v1/diagnostics/bundle` (zip). None of them takes a file path.
- **Access**: no `GET` endpoint changes anything, and state-changing requests that another site triggered are refused with `403`. With `SOFASCORE_API_TOKEN` set, every `/api` request needs `Authorization: Bearer <token>` or the web app's session cookie (`POST /api/v1/auth/login` with `{"token": "..."}`, `POST /api/v1/auth/logout`, `GET /api/v1/auth` for the state); otherwise the answer is `401`. See [Security model](#security-model).
- **Health**: `GET /health` (no `/api` prefix) answers `status`, `version`, `ui`, plus `bridge` (the [bridge health](#is-sofascore-blocking-us-bridge-health)) and `throttle` (the shared [request budget](#request-budget-all-processes)). With an access token set, a caller without it gets only `{"status": "ok"}`.

**The 2.x routes** (`/api/leagues`, `/api/matches`, `/api/fetch`, `/api/scrape/*`, `/api/settings`, `/api/data/*`, `/api/export/csv`, `/api/bypass/*`, `/api/logs`, `/api/diagnostics*`, `/api/auth*`, `/api/sports`, …) still answer for one more release, with their old shapes. They are marked deprecated in the OpenAPI document, and every answer carries a `Deprecation` header and a `Link` to its successor under `/api/v1`. Move programs to `/api/v1`.

## Anti-Bot Protection (BrowserBridge)

SofaScore rejects plain HTTP clients: API requests get `403 {"reason": "challenge"}` whatever TLS fingerprint `curl_cffi` presents. The app therefore makes API calls from inside a real browser page:

1. **BrowserBridge** starts a headless Chromium through [Scrapling](https://github.com/D4Vinci/Scrapling)'s `StealthySession` (patchright with stealth settings) and keeps one page open.
2. **Challenge:** on a 403 challenge it opens `sofascore.com/captcha.html`; Scrapling passes the embedded Cloudflare Turnstile and the resulting `sofa_captcha` cookie unlocks the API. Concurrent 403s share one solve; a failed solve is not retried for 3 minutes.
3. **Browser-first mode:** once curl is blocked and the browser succeeds, requests go straight to the browser for 10 minutes instead of failing through curl first.

The browser always runs **headless**, on a desktop and on a server alike, so the same code path is used everywhere; no display, Xvfb or Google Chrome is needed. Measured on three sports: full seasons of Premier League (50 matches), Wimbledon (239) and EuroBasket (76) downloaded at 100% coverage without a display. Set `SOFASCORE_BROWSER_HEADED=1` to watch the browser while debugging.

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
  - `GET /health` → `bridge` and `GET /api/v1/status`: `state`, `consecutive_failures`, `last_success_at`, `failing_since`, `last_error` (`kind`: `challenge` / `forbidden` / `browser`), `thresholds`. `status` in `/health` stays `ok`: it says the server is up.
  - Web app: the health pill in the top bar and **Needs attention** on Overview while the state is not `ok`.
  - Web app, **Health**: the same state, when SofaScore last answered and last failed, and **Run connection check** to try one request yourself (`POST /api/v1/status/check`), with the reason if it failed.
  - Log: one warning per state change, not per request.
  - Command line (`ssc` and the deprecated `main.py` flags): one line on stderr per state change, in the app language.
- The state is per process: the web app reports its own bridge, each CLI process its own.

### Server setup (Linux / Docker)

The [Docker image](#docker) already contains the browser and its system libraries. On a plain Linux server, or in an image of your own:

```bash
pip install -r requirements.txt -c constraints.txt
python -m patchright install chromium --no-shell
# Debian/Ubuntu only, once: system libraries Chromium needs (uses sudo)
python -m patchright install-deps chromium
# exit code 0 = ready; starts the browser once on about:blank, makes no request to SofaScore
ssc doctor --skip frontend      # or: python main.py doctor --skip frontend
```

## Development

Run the web app with auto-reload:

```bash
ssc serve --dev             # or: python -m src.cli.main serve --dev
```

The web app is a Vue 3 + TypeScript + Vite project in `frontend/` (Pinia, vue-router, vue-i18n, Tailwind). The server serves the built files from `frontend/dist/`.

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173, proxies /api and /health to 127.0.0.1:8000
npm run build    # type-check (vue-tsc) + production build into frontend/dist/
npm test         # vitest
npm run lint     # ESLint
```

Layout: `src/screens/` one folder or file per screen, `src/app/` the shell (menu, top bar, quick search, Help), `src/ui/` the shared components, `src/api/v1/` every backend call (types generated from `docs/api/openapi-v1.json` with `npm run gen:api`), `src/locales/` all UI text in English and Turkish. More in [frontend/README.md](frontend/README.md).

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
- **Pull requests** — Fork the repo, use a focused branch, keep changes small and on-topic, and describe *what* and *why* in the PR. Match existing code style; avoid drive-by refactors. If you touch user-visible text, update both languages: `frontend/src/locales/ui/en.ts` and `tr.ts` (and `frontend/src/locales/en.ts` / `tr.ts`) for the web app, `locales/en.json` and `locales/tr.json` for the command line.
- **Docs & translations** — Improvements to these READMEs or locale strings are appreciated.

By submitting a contribution, you agree that it is licensed under the project's license and that the maintainer may also offer it under other terms (for example, a commercial license). Be respectful in issues and reviews. If you are unsure whether an idea fits, open an issue first.

## License

[PolyForm Noncommercial 1.0.0](LICENSE). You may use, modify and share this software for **noncommercial purposes** — personal use, study, research, hobby projects, and use by charities, educational institutions and public bodies. **Commercial use is not permitted** without a separate license; to ask about one, open an issue or contact [@tunjayoff](https://github.com/tunjayoff).

Versions released before this change remain available under the MIT license.
