# SofaScore Scraper — web app

The browser interface of SofaScore Scraper: Vue 3, TypeScript, Vite, Pinia, vue-router, vue-i18n and Tailwind CSS 4. The Python server (`ssc serve`, or `python main.py serve` without installing) serves the built files from `dist/`; see the main [README](../README.md) for installing and running the whole app.

The built app needs Safari 16.4, Chrome 111 or Firefox 128 or newer (Tailwind CSS 4). Building it needs Node.js 20.19+ or 22.12+.

## Commands

```bash
npm install
npm run dev      # http://localhost:5173 — /api and /health are proxied to http://127.0.0.1:8000
npm run build    # vue-tsc type check, then a production build into dist/
npm run preview  # serve dist/ locally
npm test         # vitest (jsdom), axe checks included
npm run lint     # eslint
npm run gen:api  # regenerate src/api/v1/schema.ts from docs/api/openapi-v1.json (--check: fail when it differs)
```

Run the Python server alongside `npm run dev`; the dev server only proxies to it.

## Screens

The app talks to `/api/v1` only. Every list of sports comes from `GET /api/v1/sports`, so a sport added to the server's registry appears without a change here.

| Route | File | What it does |
|---|---|---|
| `/` | `screens/OverviewScreen.vue` | Overview: stored data, running and last jobs, health, the "Getting started" card |
| `/follows` | `screens/follows/FollowsScreen.vue` | Leagues & follows: tournaments, teams, players and single matches; **Add league** |
| `/follows/new`, `/follows/:kind/:id/edit` | `screens/follows/FollowEditorScreen.vue` | Add or edit a follow: search SofaScore, seasons, data types |
| `/follows/:kind/:id` | `screens/follows/FollowDetailScreen.vue` | One follow: its seasons, matches, data types and jobs |
| `/events` | `screens/events/EventsScreen.vue` | Matches: filters in the address, fetch missing data |
| `/events/:id` | `screens/events/EventDetailScreen.vue` | One match: score, statistics, line-ups, incidents, every stored data type and its raw payload |
| `/corrections` | `screens/CorrectionsScreen.vue` | Score changes found after a match finished |
| `/jobs`, `/jobs/:id` | `screens/jobs/` | Jobs of every process: progress, log, stop, run again |
| `/exports` | `screens/exports/ExportsScreen.vue` | Exports (CSV, JSONL, Parquet, SQLite; normalized datasets) and their downloads |
| `/backups` | `screens/backups/BackupsScreen.vue` | Create, download, check and restore backups |
| `/maintenance` | `screens/MaintenanceScreen.vue` | Data cleanup: rebuild the index, the old-layout count with the `ssc migrate` command (no migrate button), delete stored data (by scope, or one league or season) |
| `/system/health` | `screens/HealthScreen.vue` | Connection to SofaScore (a connection check on click), live service, locks, storage |
| `/system/sinks` | `screens/SinksScreen.vue` | Outputs (sinks) and whether they deliver (read-only; set in `sofascore.toml`) |
| `/system/logs` | `screens/LogsScreen.vue` | Log tail; diagnostics summary with the setup check (doctor) and the bundle |
| `/settings` | `screens/settings/SettingsScreen.vue` | Server settings with the place each value comes from, the default data types, and "This browser" (language, theme, Sign out) |

There is no live view: live watching is `ssc watch` on the server, and the UI only shows on Health whether it runs. The classic views of 2.x were removed; their addresses (`/download`, `/matches`, `/match/:id`, `/activity`, `/leagues`, `/stats`, `/schedule`, `/advanced/*`, `/classic/*`) redirect to the nearest screen for one release (`src/router.ts`).

## Where things live

- `src/api/v1/client.ts` — every backend call; `errors.ts` turns each error code into a translated sentence; `schema.ts` is generated (`npm run gen:api`), do not edit it by hand.
- `src/app/` — the shell: side rail and phone bottom bar (`nav.ts`), top bar, quick search (`CommandPalette.vue`, `Ctrl K` / `⌘ K`), keyboard shortcuts, the Help panel (`HelpPanel.vue`, `help.ts`), "Getting started", the token prompt, `/status` polling, the sport registry (`sports.ts`).
- `src/screens/` — one folder or file per screen (table above).
- `src/ui/` — the design system: `DataTable`, `UiDialog`, `ConfirmDialog`, `FilterBar`, `StatusBadge`, toasts, `JsonViewer`, `TimeText`, … and `tokens.css` (colour tokens for light and dark).
- `src/locales/en.ts`, `src/locales/tr.ts` — sport names; `src/locales/ui/en.ts`, `src/locales/ui/tr.ts` — every other UI text. `locales/en.ts` is typed against `tr.ts` and `ui/tr.ts` against `ui/en.ts`, so a key missing in one language fails the build.
- `src/style.css` — Tailwind and the base layer; `tests/` — vitest suites with a fake v1 API (`tests/v1.ts`).

## Conventions

- Colours come from the CSS variables in `src/ui/tokens.css`; don't hard-code colour values in components (`tests/tokens.test.ts` checks the contrast of the text pairs).
- Times and numbers go through `src/ui/time.ts` / `TimeText` so they follow the chosen language and time display.
- User-visible text is always a translation key, in both locale files.
- No `eval` and no inline script in the build (`tests/csp.test.ts`): the server's Content-Security-Policy forbids them.
