# SofaScore Scraper

**Türkçe:** [README.tr.md](README.tr.md)

Python tool to download football match data from [SofaScore](https://www.sofascore.com/) public HTTP APIs, store it locally (JSON and CSV), and browse it through a terminal UI or a web dashboard.

This project is not affiliated with SofaScore. Use reasonable request rates and comply with applicable terms and laws.

## Features

- **Leagues** — Configure tournament IDs; optional remote search when adding leagues (web).
- **Seasons & schedule** — Fetch season lists and match lists; filter by league, season, date.
- **Match details** — Statistics, lineups, incidents, H2H, and related JSON slices; optional parallel fetching with progress and cancel (web).
- **Web UI** — Dashboard, leagues, schedule (with fetch wizard), match view, stats, settings (env-backed, tabs, backup/restore/clear), real-time scraper status (SSE).
- **Terminal UI** — Interactive menu for the same operations without the browser.
- **Automation** — Headless flags for CI/scripts (`--update-all`, `--fetch-mode`, `--league-id`, `--csv-export`, paths).
- **Export** — Processed “all matches” CSV and API export endpoints.

## Requirements

- Python **3.10+** (3.11+ recommended).
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
| `LANGUAGE` | `en` or `tr` for UI strings. |
| `MAX_CONCURRENT` | Parallel detail requests cap. |
| `USE_PROXY` / `PROXY_URL` | Optional HTTP proxy. |
| `FETCH_ONLY_FINISHED` | Prefer finished matches when fetching lists. |
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

1. Finish **Installation** and **Configuration** (`pip install`, `cp .env.example .env`). Optionally set `LANGUAGE=en` or `tr` and `DATA_DIR` if you want data somewhere other than `./data`.
2. Start the server: `python main.py --web` and open `http://127.0.0.1:8000`.
3. **Leagues** — Add at least one tournament: use search (SofaScore) or enter the numeric tournament ID from the SofaScore URL. Save.
4. **Schedule** — Choose a league (and season if needed). Use **Fetch** (wizard) for a guided run—pick leagues, seasons, and whether you want full sync or details only—or use the buttons for broader one-shot updates.
5. While data is downloading, a **progress** card shows status; you can usually still navigate the site. If something stays stuck, check **Settings → performance** (concurrency, timeouts) and logs.
6. Click a **match row** to open the match page (stats, lineups, etc.). If details are missing, use the actions on that page or run another **details** fetch from Schedule.
7. **Stats** summarises disk usage and coverage; **Settings** edits `.env` (tabs for general, network, performance, data tools). Use backup/restore before risky clean operations.

**Terminal menu**

Run `python main.py` and work through the numbered menus: manage leagues, refresh seasons, fetch match lists, fetch details, run stats, or export CSV. The flow matches the web conceptually but without the wizard—use prompts to choose leagues and options.

**Tips**

- First-time **full** fetch for a big league can take a long time; start with one league and a few recent seasons from the wizard.
- If you hit rate limits or many errors, lower **MAX_CONCURRENT** and raise waits slightly in **Settings**; avoid `--ignore-rate-limit` unless you know what you are doing.
- For the same dataset in **web** and **CLI/headless**, keep `DATA_DIR` in `.env` aligned with `--data-dir` when you use the command line.

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

## REST API (overview)

All routes are prefixed with `/api` unless noted.

- **Leagues**: list, create, delete, search (local / remote), seasons, refresh seasons, missing-details.
- **Matches**: paginated schedule, single-match JSON, on-demand fetch for one match.
- **Scraper**: `POST /api/fetch` (body: mode `full` or `details`, optional league and wizard `selections`), cancel, status, SSE stream.
- **Dashboard / stats / settings**: JSON for the web UI; settings mirror `.env` keys.
- **Data**: backup zip, clear scopes, CSV export.

OpenAPI: `GET /docs` when the server is running.

## Development

Run the web app with auto-reload (as started by `main.py --web`):

```bash
uvicorn src.web.app:app --reload --host 0.0.0.0 --port 8000
```

## Contributing

Contributions are welcome. You can help in several ways:

- **Bug reports** — Open an issue with steps to reproduce, expected vs actual behaviour, OS/Python version, and relevant `.env` flags (redact secrets).
- **Feature ideas** — Suggest use cases and constraints; maintainers may triage and discuss scope in the issue.
- **Pull requests** — Fork the repo, use a focused branch, keep changes small and on-topic, and describe *what* and *why* in the PR. Match existing code style; avoid drive-by refactors. If you touch user-visible text, consider `locales/en.json` and `locales/tr.json`.
- **Docs & translations** — Improvements to these READMEs or locale strings are appreciated.

There is no separate contributor agreement beyond the MIT license on your submissions. Be respectful in issues and reviews. If you are unsure whether an idea fits, open an issue first.

## License

MIT — see [LICENSE](LICENSE).
