# SofaScore Scraper

**Türkçe:** [README.tr.md](README.tr.md)

Python tool to download football, basketball and tennis match data from [SofaScore](https://www.sofascore.com/) public HTTP APIs, store it locally (JSON and CSV), and browse it through a web app or a terminal UI.

This project is not affiliated with SofaScore. Use reasonable request rates and comply with applicable terms and laws.

## Features

- **Three sports** — Football, basketball and tennis. Every league remembers its sport, and the whole web app can be switched to one sport at a time.
- **Leagues** — Add tournaments by searching SofaScore (web) or by ID (`config/leagues.txt`).
- **Seasons & matches** — Pick seasons from one or several leagues and download them in one go; browse matches by league, season, date and whether details are downloaded.
- **Match details** — Statistics (per period), incidents, lineups, H2H and form; score lines per half, quarter or set depending on the sport.
- **Web app** — Leagues, Download, Matches, Activity and Settings pages; live progress (SSE) with a Stop that takes effect immediately; Turkish and English; light, dark or system theme.
- **Terminal UI** — Interactive menu for the same operations without the browser.
- **Automation** — Headless flags for CI/scripts (`--update-all`, `--fetch-mode`, `--league-id`, `--csv-export`, paths).
- **Export** — Processed “all matches” CSV and API export endpoints.

## Requirements

- Python **3.10+** (3.11+ recommended).
- **Node.js 20.19+ or 22.12+ and npm** — to build the web app (`frontend/`). `scripts/start_web.py` builds it on the first run.
- **Git** — required for the one-line `curl | bash` installer (clones this repo); optional if you already extracted or cloned the project manually.
- Network access to SofaScore.

## Installation

### Quick install (script)

Official repository: [github.com/tunjayoff/sofascore_scraper](https://github.com/tunjayoff/sofascore_scraper).

**Linux / macOS / Git Bash**

Already cloned:

```bash
chmod +x scripts/install.sh   # once
./scripts/install.sh
```

**One-liner** (clones [tunjayoff/sofascore_scraper](https://github.com/tunjayoff/sofascore_scraper), creates `.venv`, installs dependencies, copies `.env`):

```bash
curl -fsSL https://raw.githubusercontent.com/tunjayoff/sofascore_scraper/main/scripts/install.sh | bash
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

**Prerequisites:** **Git** (for the one-liner / clone path), **Python 3.10+** on `PATH`. The scripts print clear errors if `git`, `python`, `venv`, or `pip install` fails (e.g. missing `python3-venv` on Debian/Ubuntu).

### Manual install

```bash
git clone https://github.com/tunjayoff/sofascore_scraper.git
cd sofascore_scraper
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Copy environment defaults and adjust:

```bash
cp .env.example .env
```

## Configuration

### Environment (`.env`)

See `.env.example` for all keys. Common ones:

| Variable | Purpose |
|----------|---------|
| `DATA_DIR` | Root folder for stored data (default `data`). Web app reads this via `ConfigManager`. |
| `LANGUAGE` | `en` or `tr`: language of the terminal UI and server messages. The web app has its own switch under **Settings** (changing it there also updates this value). |
| `MAX_CONCURRENT` | Parallel detail requests cap. |
| `USE_PROXY` / `PROXY_URL` | Optional HTTP proxy. |
| `FETCH_ONLY_FINISHED` | Keep only finished matches (`status.type == finished`). Default `true`. Upcoming fixtures are dropped from schedule files. |
| `RATE_LIMIT_*` / `SERVER_ERROR_*` | Circuit breaker thresholds when many errors occur. |

Tuning for the web UI (timeouts, retries, logging) is exposed under **Settings**; writing settings updates `.env`.

### Leagues (`config/leagues.txt`)

One line per league: numeric SofaScore **unique tournament ID** and a display name (format created/maintained by the app). The ID appears in SofaScore tournament URLs (e.g. `.../premier-league/17` → `17`).

CLI override:

```bash
python main.py --config /path/to/leagues.txt --data-dir /path/to/data
```

`--config` / `--data-dir` apply to **interactive** and **headless** modes. The web server loads the singleton `ConfigManager` from the project `.env` (`DATA_DIR`, etc.); align paths so the web UI and CLI see the same data if you use both.

## Usage

### How to use the app (quick start)

**Web (recommended for most users)**

1. Finish **Installation** and **Configuration** (`pip install`, `cp .env.example .env`). Optionally set `DATA_DIR` if you want data somewhere other than `./data`.
2. Start the app: `./start-sofascore.sh` (or `python scripts/start_web.py`). On the first run it builds the web app, then it opens `http://127.0.0.1:8000`. `python main.py --web` starts the server alone.
   After updating the code (`git pull`), rebuild the web app yourself: `cd frontend && npm install && npm run build`. The start script only builds when `frontend/dist/` is missing, so otherwise you keep seeing the old interface.
3. **Sport** — The switch at the top of the sidebar (All / Football / Basketball / Tennis) filters every page. Pick the sport you are working on.
4. **Leagues** — **Add league** searches SofaScore; filter the results by sport and press **Add**. A league whose sport is unknown (for example one added to `config/leagues.txt` by hand) shows a **Pick sport** box; choose once and it is saved.
5. **Download** — Left column: pick a league. Middle: tick seasons (the season list is fetched automatically the first time; **Latest season** / **Last 3 seasons** are shortcuts). You can pick seasons from several leagues; they collect in the **Download list** on the right. Press **Download N seasons**. Matches and their details (statistics, events, lineups) are downloaded together.
6. While a download runs, the card at the bottom left of the sidebar shows progress; **Stop** takes effect right away: no new requests are sent and retry waits are cut short; a request already in flight can take up to the request timeout (`REQUEST_TIMEOUT`) to return. Only one download runs at a time. **Activity** lists the current and past downloads.
7. **Matches** — Filter by league, season, date and **Details** (with / missing). When a league has matches without details (typically after a stopped download), a banner offers **Download missing**. Click a row to open the match: score by period, overview, statistics, events and lineups.
8. **Settings** — Language and theme; data folder, disk usage, **Back up** and **Delete all data**; advanced request settings (timeout, concurrency, waits, retries).

> **Delete all data** removes every downloaded season, match and detail and cannot be undone. Take a backup first. A backup is a zip under `src/web/static/backups/` holding `data/`, `.env` and `leagues.txt`. To restore: stop the app, unzip `data/` into the project folder (or your `DATA_DIR`), and copy `leagues.txt` to `config/leagues.txt` and `.env` to the project root only if you want those back too.

**Terminal menu**

Run `python main.py` and work through the numbered menus: manage leagues, refresh seasons, fetch match lists, fetch details, run stats, or export CSV. There is no counterpart of the web Download page; use the prompts to choose leagues and options.

**Tips**

- The first download of a big league can take a long time; start with one league and a few recent seasons.
- If you hit rate limits or many errors, lower **MAX_CONCURRENT** and raise waits slightly in **Settings**; avoid `--ignore-rate-limit` unless you know what you are doing.
- For the same dataset in **web** and **CLI/headless**, keep `DATA_DIR` in `.env` aligned with `--data-dir` when you use the command line.
- Prefer a season that already has finished matches. The newest label (e.g. European `26/27`) is often fixtures-only; the scraper can fall back automatically.
- Stopping a download keeps everything fetched so far. Matches whose details were not reached show **Details: No** in Matches; use **Download missing** there (or on the Download page) to complete them.

### Troubleshooting

**Seasons appear but fetch finds 0 matches** (`İşlenecek maç verisi bulunamadı` / `0it`)

1. Confirm the SofaScore **unique tournament ID** in the URL (MLS is **`242`** — that ID is correct).
2. Not every league exposes a sequential week schedule (`events/round/1..N`). **Premier League**-style competitions do; **MLS** and some others do not:
   - MLS `/rounds` is empty or only playoff-style IDs (e.g. `227`), and `events/round/1` returns nothing.
   - The scraper must use paginated **`events/last` + `events/next`** for those seasons.
   - Older builds that only probed weeks `1..50` therefore downloaded PL fine but returned **zero MLS matches**. Update to a release that includes the event-list fallback, refresh seasons, and re-run the fetch.
3. With `FETCH_ONLY_FINISHED=true` (default), not-yet-played fixtures are ignored. If a brand-new season has no finished games yet, pick the previous season (or wait / set `FETCH_ONLY_FINISHED=false` if you intentionally want fixtures).
4. Stale season IDs (SofaScore retired the ID after a refresh) also yield empty schedules — open the league on the **Download** page, press **Refresh** above its seasons, then download again.

### Interactive terminal

```bash
python main.py
```

### Web application

```bash
python main.py --web
```

Default URL: `http://127.0.0.1:8000` (bind `0.0.0.0:8000`). Health: `GET /health`.

Background jobs report status via `GET /api/scrape/status` and `GET /api/scrape/stream` (SSE). Heavy API work runs off the asyncio event loop so the UI stays responsive during long fetches.

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

Examples:

```bash
python main.py --headless --update-all
python main.py --headless --update-all --fetch-mode details --league-id 52
python main.py --headless --csv-export --data-dir ./data
```

Exit codes: **0** success (or `APP_EXIT_CODE` if set by scraper), **1** unexpected error, **2** headless with no action.

### Command-line help

```bash
python main.py --help
```

## Data layout (under `DATA_DIR`)

Typical structure:

```text
data/
├── seasons/           # Season metadata per league
├── matches/           # Match list / summary CSVs by league & season
├── match_details/     # Per-match JSON folders (basic, stats, lineups, …)
│   └── processed/     # Aggregated CSV exports
└── datasets/          # Reserved / auxiliary
```

Exact paths may vary slightly by league naming and migrations.

The match list reads the per-season summaries under `matches/`; the export CSV in `match_details/processed/` is only a fallback when no summaries exist.

Next to `config/leagues.txt` (the `name: id` list the CLI also reads), `config/league_sports.json` stores each league's sport as `{"<id>": "football" | "basketball" | "tennis"}`. It is filled when a league is added from the web app, when you pick a sport in the UI, or from a downloaded match of that league.

## REST API (overview)

All routes are prefixed with `/api` unless noted.

- **Leagues**: list (each with `sport`), create (optional `sport`), `PATCH /api/leagues/{id}` to set the sport, delete, search (local / remote, remote results carry `sport`), seasons, refresh seasons, missing-details.
- **Matches**: `GET /api/matches` — paginated, filters `league_id` (one id or several comma-separated, e.g. `17,8`), `season_id`, `date`, `details=present|missing`, `sort=asc|desc`; every row has `has_details`. Also single-match JSON and on-demand fetch for one match.
- **Scraper**: `POST /api/fetch` (body: mode `full` or `details`, `selections: [{league_id, season_ids, match_ids}]`), `POST /api/scrape/cancel` (no new requests after it; retry waits are cut short), status, SSE stream.
- **Dashboard / stats / settings**: JSON for the web UI; settings mirror `.env` keys.
- **Data**: backup zip, clear scopes, CSV export.
- **Bypass Status**: `GET /api/bypass/status` and live test `POST /api/bypass/test`.

OpenAPI: `GET /docs` when the server is running.

## Anti-Bot Protection & Autonomous Bypass (BrowserBridge)

Sofascore API endpoints are protected by Cloudflare Turnstile CAPTCHA and Varnish TLS/JA4 fingerprinting:
1. **Dynamic Hash:** The `X-Requested-With` header is dynamically generated as a SHA-256 hash using 30-minute timestamp intervals.
2. **Turnstile & JWT Token:** Initial API requests trigger `403 {"reason": "challenge"}`.
3. **Autonomous BrowserBridge:** When a challenge is received, a lightweight background Chrome/Playwright persistent session solves Turnstile automatically in 1–2 seconds, exchanges the JWT `sofa_captcha` token, and fulfills API requests directly through the authenticated TLS session at **2–10 ms** speeds.

### Headless Server Setup (Linux / Docker)

When running on a headless Linux server or in a container:
```bash
# Install Playwright browser dependencies:
playwright install chromium
# Or install Google Chrome package directly (recommended):
sudo apt install google-chrome-stable  # Ubuntu/Debian
sudo pacman -S google-chrome           # Arch Linux
```
On servers without a physical display, run inside a virtual display:
```bash
xvfb-run python main.py --headless --update-all
```
On standard desktop environments (Linux X11/Wayland, Windows, macOS), BrowserBridge runs automatically without extra configuration.


## Development

Run the web app with auto-reload (as started by `main.py --web`):

```bash
uvicorn src.web.app:app --reload --host 0.0.0.0 --port 8000
```

The web app is a Vue 3 + TypeScript + Vite project in `frontend/` (Pinia, vue-router, vue-i18n, Tailwind). The server serves the built files from `frontend/dist/`.

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173, proxies /api to 127.0.0.1:8000
npm run build    # type-check (vue-tsc) + production build into frontend/dist/
```

Layout: `src/views/` one file per page, `src/components/` shared pieces, `src/stores/` (leagues, sport filter, running job), `src/api/client.ts` every backend call, `src/locales/{tr,en}.ts` all UI text.

Tests (no network, no writes to your `data/`):

```bash
python -m pytest tests --ignore=tests/live_smoke_test.py --ignore=tests/test_live_api_bypass.py \
  --ignore=tests/test_browser_bridge.py --ignore=tests/test_delivery_api.py --ignore=tests/test_web_api_smoke.py
```

The excluded files talk to SofaScore or a browser, or read and write your real `DATA_DIR` and job database.

## Contributing

Contributions are welcome. You can help in several ways:

- **Bug reports** — Open an issue with steps to reproduce, expected vs actual behaviour, OS/Python version, and relevant `.env` flags (redact secrets).
- **Feature ideas** — Suggest use cases and constraints; maintainers may triage and discuss scope in the issue.
- **Pull requests** — Fork the repo, use a focused branch, keep changes small and on-topic, and describe *what* and *why* in the PR. Match existing code style; avoid drive-by refactors. If you touch user-visible text, update both languages: `frontend/src/locales/tr.ts` and `en.ts` for the web app, `locales/en.json` and `locales/tr.json` for the terminal UI.
- **Docs & translations** — Improvements to these READMEs or locale strings are appreciated.

There is no separate contributor agreement beyond the MIT license on your submissions. Be respectful in issues and reviews. If you are unsure whether an idea fits, open an issue first.

## License

MIT — see [LICENSE](LICENSE).
