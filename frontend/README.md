# SofaScore Scraper — web app

The browser interface of SofaScore Scraper: Vue 3, TypeScript, Vite, Pinia, vue-router, vue-i18n and Tailwind. The Python server (`python main.py --web`) serves the built files from `dist/`; see the main [README](../README.md) for installing and running the whole app.

## Commands

```bash
npm install
npm run dev      # http://localhost:5173 — /api and /health are proxied to http://127.0.0.1:8000
npm run build    # vue-tsc type check, then a production build into dist/
npm run preview  # serve dist/ locally
```

Run the Python server alongside `npm run dev`; the dev server only proxies to it.

## Pages

| Route | File | What it does |
|---|---|---|
| `/` | `views/LeaguesView.vue` | Followed leagues with match count and detail coverage; add, remove, set a league's sport |
| `/download` | `views/DownloadView.vue` | Pick league → seasons (from several leagues), then start one download; complete missing details |
| `/matches` | `views/MatchesView.vue` | Downloaded matches: league / season / date / details filters, "download missing details" banner |
| `/match/:id` | `views/MatchView.vue` | Score by half, quarter or set; overview, statistics, events, lineups |
| `/activity` | `views/ActivityView.vue` | Running download and history |
| `/settings` | `views/SettingsView.vue` | Language, theme, data folder, backup, delete data, request settings |

Old addresses (`/advanced/*`, `/schedule`, `/stats`) redirect to their new pages.

## Where things live

- `src/api/client.ts` — every backend call and the response types. Add new endpoints here, not in views.
- `src/stores/leagues.ts` — leagues with their sport and download counts. `rows` is already filtered by the sidebar sport; `all` is not.
- `src/stores/sport.ts` — the sidebar sport switch (`all` / `football` / `basketball` / `tennis`), remembered per browser.
- `src/stores/scrape.ts` — the one background job: status over SSE with polling as fallback, `start`, `cancel`, `onFinished`.
- `src/components/` — `JobCard` (progress card), `AddLeagueDialog`, `SportSwitch`, `SportPicker`, `SportBadge`, `BrandMark`, `AppIcon` (stroke icons).
- `src/lib/` — formatting (`format.ts`), sport helpers (`sport.ts`), job titles (`jobLabel.ts`), match detail parsing (`matchDetail.ts`), theme and toasts.
- `src/locales/tr.ts`, `src/locales/en.ts` — all UI text. `en.ts` is typed against `tr.ts`, so a key missing in English fails the build.
- `src/style.css` — colour tokens for light and dark (`:root` / `html.dark`) and the shared classes (`card`, `btn`, `badge`, `field`, `table-row`, …).

## Conventions

- Colours come from the CSS variables in `style.css`; don't hard-code hex values in components.
- Numbers, dates and percentages go through `lib/format.ts` so they follow the chosen language (Turkish writes `%72`, English `72%`).
- User-visible text is always a translation key, in both locale files.
