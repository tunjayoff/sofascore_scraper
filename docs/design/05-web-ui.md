# 05 — Web UI: screens (FE-1)

**State: approved by the owner on 2026-10-02**, with all 22 decisions of section 10 as chosen
(`00-platform.md` section 10, item 12; decision P2, second half, in `03-implementation-plan.md` section 13).
**Built** by FE-2 in two parts: FE-2a (#107: shell, design system, client, Overview, Jobs, Job detail,
Health, Settings) and FE-2b (#132: Exports, Backups, Maintenance, Logs, Sinks, Overview and Health on the
P21 fields; #133: Follows, Follow editor, Follow detail, Events, Event detail with the raw view,
Corrections, quick search). Every screen of the URL map exists. Since the sixth revision it has two more
passes: FX-14a (#154), a newcomer pass on words, entry points and help, and FX-14b (#161), which wires the
routes of FX-13 (#152) and FX-19 (#156) and **removed the classic views**; their old addresses
(`/classic/...`) lead to the new screens (decision 22 is done). Section 11 lists where the built UI and API
differ from this document; the screen sections below are corrected in place.

First written against `origin/main` at `aff0bb0` (2026-10-02), when only P20's (#74) routes of `/api/v1`
existed. Checked again at `b3cb819` (2026-10-03): P21 (#122 to #127) has built the resource routes, P29
(#128) the scheduler fields of `/status` and SC-2 (#130) the normalized exports. A route marked "P21" in
section 6 exists now unless the row says otherwise.

Checked a third time at `b6caf2f` (2026-10-06, the sixth revision of the design documents), after P27
(#134, user-selectable slices), P28 (#140, odds and non-match data), FX-13 (#152, #153, the routes the UI
lacked), FX-19 (#156) and the two frontend passes FX-14a (#154) and FX-14b (#161), and after the dependency
updates Tailwind CSS 4 (#136), vue-i18n 11 (#137), vue-router 5 (#143) and vite 8.3 (#146). The two
frontend passes came from a **first-time-user review** of the new UI (2026-10-06, at `b567409`; its report
and 120 screenshots are kept outside the repository): the owner could not find "add a league". The review
found that the new UI had swapped the user's words for internal ones ("Follow", "Sync"), that team, player
and single-match follows were dead ends, and that seasons, data types, restore and the deletion of one
league's data could not be done in the UI. Four items came out of it: FX-14a (frontend words, entry points,
help), FX-19 (the backend gaps), FX-14b (the screens on the new routes, classic views removed) and FX-20
(in progress: type-ahead search like the site, one search across kinds, job names, small UI gaps). The
owner asked that **the review wins over this document where they disagree**; the screen sections below say
"As built (FX-14a)" or "As built (FX-14b)" where it did. #161 ran the review's twelve newcomer tasks again
on the same offline set-up: all are OK except Settings (still hard) and live watching (hard by design: it is
`ssc watch` on the server, which Help explains).

## Contents

1. What this document decides, and the rules that bind it
2. Who uses the web UI, and for what
3. Information architecture
4. Design system
5. Shared behaviour: loading, errors, the token, refusals
6. Screens
7. API routes the UI needs: what exists, what is planned, what is missing
8. Notes for FE-2 (implementation)
9. What the web UI deliberately does not do
10. Decisions taken (approved 2026-10-02)
11. What changed while it was built

---

## 1. What this document decides, and the rules that bind it

The web UI is designed and built **from scratch** on the same technology stack, for the platform of 3.0.0
(owner decision of 2026-10-02, which replaces the earlier "large update on the existing base"). It is not
an update of today's six views. It keeps the technology: Vue 3, TypeScript, Pinia, vue-router, vue-i18n,
Vite and Tailwind CSS. Section 8 names the pieces of today's code that are worth carrying over; they are an
implementation note, not a limit on the design. As built, the new app lives in `frontend/src/app`,
`frontend/src/ui` and `frontend/src/screens`, and today's views were moved, unchanged in function, under
`/classic` (`frontend/src/classic/ClassicLayout.vue`, `frontend/src/views/`) until FX-14b (#161) removed
them with the modules only they used (`frontend/src/components/`, `frontend/src/stores/`,
`frontend/src/api/client.ts` and eight `lib/` helpers) and their 18 test files.

Binding owner decisions:

| # | Rule | Where it shows in this design |
|---|---|---|
| R1 | The web UI is for people at a screen. The CLI is for servers and automation. | No screen copies a CLI-only task (live watching, sink configuration, migrate). Screens point to the command instead. |
| R2 | **No live view.** People watch live scores on SofaScore itself. | No live score list, no live ticker, no auto-updating scores. The UI shows only whether the live service (`ssc watch`) runs and how healthy it is, from `/api/v1/status` (6.12). |
| R3 | Every data type is selectable per follow. What is not selected is never fetched. Odds are a selectable data type, **off by default**. | The follow editor has a data-selection step (6.3). Odds are their own group with a note on cost and history. As built (FX-14b): per follow, globally and per sport, as checklists (6.3, 6.16); `required` marks a data type that counts for completeness and does not lock its box (P27 owner decision). |
| R4 | 21 sports: football, basketball, tennis now; 13 score-mapping sports and 5 with their own logic later. Motorsport, cycling, bandy, water polo and beach volleyball are out. | Every sport list comes from `/api/v1/sports`. No screen has a hard-coded sport. A new sport appears when the registry has it. |
| R5 | No accounts. An optional access token protects the server (#43). | A token prompt and a sign-out exist (6.15). Nothing else about users. |
| R6 | English by default, Turkish when the system language is Turkish. | Every text goes through the locale files. Section 4.10. |
| R7 | Raw payloads on request; compressed storage; full-size export on demand. | A raw view on every slice (6.5), raw export (6.8). |

## 2. Who uses the web UI, and for what

Three kinds of people use it. One person can be all three.

| Who | What they want | When | Main screens |
|---|---|---|---|
| **Operator** — runs the server for themselves or a small team | Is everything healthy? Is SofaScore blocking us? Did last night's sync work? Is the live service up? Are the webhooks delivered? | A few times a day; after an alert; after an update | Overview, Jobs, Health, Sinks, Logs |
| **Curator** — decides what is collected | Follow leagues in up to 21 sports, choose seasons and data types (odds or not), see coverage, fill gaps | When a season starts; when something is missing | Follows, Follow editor, Events |
| **Data user** — takes data out | Find matches, check one match, look at its raw payload, export a dataset, take a backup | When building something on the data | Events, Event detail, Exports, Backups |

What they do **not** do in the web UI: watch matches live (R2), configure sinks (decision D11: no outbound
URLs through the API), schedule tasks (config file), migrate the old layout (CLI).

## 3. Information architecture

### 3.1 Sections

Four groups and one start page. The names are the English menu labels; the Turkish ones are in the
locale file.

```mermaid
flowchart LR
  OV[Overview]
  subgraph DATA[Data]
    FO[Follows] --> FD[Follow detail]
    FD --> FE[Follow editor]
    EV[Events] --> ED[Event detail]
    ED --> RAW[Raw view]
    CH[Corrections]
  end
  subgraph OPS[Operations]
    JO[Jobs] --> JD[Job detail]
    EX[Exports]
    BK[Backups]
    MA[Maintenance]
  end
  subgraph SYS[System]
    HE[Health]
    SI[Sinks]
    LO[Logs and diagnostics]
    SE[Settings]
  end
  OV --> FO & EV & JO & HE & SI
  FD --> EV
  FD --> JD
  ED --> JD
  JD --> ED
  EX --> JD
  BK --> JD
  MA --> JD
```

**As built (FX-14a, #154): the menu names and a fifth group.** The review's rename table was applied in both
locales. Follows is **Leagues & follows** ("Ligler ve takipler"; "Leagues" / "Ligler" in the phone's bottom
bar), Events is **Matches** ("Maçlar"), Corrections is **Score changes** ("Sonradan değişen skorlar"), Sinks
is **Outputs** ("Bildirim hedefleri") and Maintenance is **Data cleanup** ("Veri bakımı"). Score changes,
Outputs and Data cleanup moved out of Data, Operations and System into a fifth, smaller and muted group
**Advanced** ("Gelişmiş") at the end, so that Leagues & follows and Matches carry the weight
(`frontend/src/app/nav.ts:24-38` at `b6caf2f`). The screen sections below keep the design's names as
headings; the full list of renamed words is in section 11, item 27.

Every action that writes data (sync, fetch, export, backup, restore, clear, rebuild) starts a **job**. The
screen that started it shows a small toast "Job started" with a link to its Job detail. So Jobs is the one
place where every piece of work can be followed, whichever screen or process (web, CLI, scheduler) started
it.

### 3.2 URL map

| Path | Screen | Section |
|---|---|---|
| `/` | Overview | 6.1 |
| `/follows` | Follows | 6.2 |
| `/follows/new` | Follow editor (add) | 6.3 |
| `/follows/:kind/:id` | Follow detail | 6.4 |
| `/follows/:kind/:id/edit` | Follow editor (change) | 6.3 |
| `/events` | Events (filters in the query string) | 6.5 |
| `/events/:id` | Event detail | 6.6 |
| `/events/:id/raw/:key[/:sub]` | Raw view of one slice (also opened as a side panel) | 6.6 |
| `/corrections` | Corrections (the change log) | 6.7 |
| `/jobs` | Jobs | 6.8 |
| `/jobs/:id` | Job detail | 6.9 |
| `/exports` | Exports | 6.10 |
| `/backups` | Backups and restore | 6.11 |
| `/maintenance` | Maintenance | 6.11 |
| `/system/health` | Health (connection, live service, scheduler, storage) | 6.12 |
| `/system/sinks` | Sinks | 6.13 |
| `/system/logs` | Logs and diagnostics | 6.14 |
| `/settings` | Settings | 6.16 |

Filters, sort and the selected tab are in the query string, so a view can be bookmarked and shared.
Unknown paths go to `/`. Today's paths redirect to their nearest new screen for one release. As built
(`frontend/src/router.ts:54-64` at `b6caf2f`): `/download`, `/leagues` and `/advanced/leagues` → Follows;
`/matches` and `/schedule` → Events, with the classic filters carried over (`league_id` → `tournament`);
`/match/:id` → Event detail; `/activity` and `/advanced/jobs` → Jobs; `/stats` and `/advanced/stats` →
Overview; `/advanced/settings` → Settings. FE-2a (#107) first sent `/download`, `/matches` and `/match/:id`
to the classic views, because their new screens came with FE-2b (#133).

Two more kinds of path exist that the map above does not name: `/classic/...` (`/classic`,
`/classic/download`, `/classic/matches`, `/classic/match/:id`, `/classic/activity`, `/classic/settings`),
which were today's views under `⋯ → Classic interface` until FX-14b (#161) removed them, and now redirect to
their new screens (`/classic` and `/classic/download` → Leagues & follows, `/classic/matches` → Matches with
the filters carried over, `/classic/match/:id` → Event detail, `/classic/activity` → Jobs,
`/classic/settings` → Settings; `frontend/src/router.ts:46-52` at `b6caf2f`); and `/dev/kitchen-sink`, the
page of every design-system part in every state, in development builds only (section 4). The address
`/health` is the server's JSON health check, not the Health screen (`/system/health`); since FX-14b quick
search finds Health by "health", "sağlık", "durum" or "bağlantı", and Help says so with a link. No server
route was changed for it.

### 3.3 The application shell

Desktop (from 1024 px):

```
┌──────────────────┬──────────────────────────────────────────────────────────────────────────┐
│ ◆ SofaScore      │ Events                                   [● All systems OK] [⟳ 1 job] [⋯] │
│   Platform       ├──────────────────────────────────────────────────────────────────────────┤
│                  │                                                                          │
│ ⌂ Overview       │   page header: title · short description · primary action                │
│                  │                                                                          │
│ DATA             │   page content                                                           │
│ ☰ Follows        │                                                                          │
│ ▦ Events         │                                                                          │
│ ↺ Corrections    │                                                                          │
│                  │                                                                          │
│ OPERATIONS       │                                                                          │
│ ▶ Jobs        1  │                                                                          │
│ ⇩ Exports        │                                                                          │
│ ⛁ Backups        │                                                                          │
│ ⚒ Maintenance    │                                                                          │
│                  │                                                                          │
│ SYSTEM           │                                                                          │
│ ♥ Health      !  │                                                                          │
│ ⇢ Sinks          │                                                                          │
│ ≡ Logs           │                                                                          │
│ ⚙ Settings       │                                                                          │
│                  │                                                                          │
│ v3.0.0  ⌘K       │                                                                          │
└──────────────────┴──────────────────────────────────────────────────────────────────────────┘
```

- **Side rail** (232 px, can collapse to 64 px icons). Group labels, one item per screen. A count badge on
  Jobs (running jobs) and a warning dot on Health (anything not OK) and Sinks (a sink is failing or lagging).
- **Top bar**: the page title (and a breadcrumb on detail pages); the **health pill**; the **job pill**; the
  menu `⋯` with Language, Theme, Density, Keyboard shortcuts and, when a token is in use, Sign out.
- **As built (FX-14a, #154): "Add league" and Help.** The top bar has a permanent primary button **"+ Add
  league"** ("+ Lig ekle"; a 44 px icon button on a phone), hidden only on the editor itself
  (`frontend/src/app/TopBar.vue:69-77` at `b6caf2f`); it is also the first item of the phone's More sheet.
  The `⋯` menu has **Help**, and the rail's foot a Help icon button. Help opens a side panel
  (`frontend/src/app/HelpPanel.vue`): one line on what the app does, the three getting-started steps, a
  glossary of seven words (league and follow, download and update, data type, score changes, outputs, data
  cleanup, live watching: a separate service started on the server with `ssc watch` that sends live events
  to the outputs; the web UI shows no live scores), the keyboard shortcuts, a paragraph that `/health` is
  the server's own JSON check (FX-14b), and a link to the project README (`README.tr.md` in Turkish;
  `frontend/src/app/help.ts:20-22`). Small **(i) toggletips** (`frontend/src/ui/HelpTip.vue`) explain these
  words where they appear: the titles of Leagues & follows, Jobs, Score changes, Outputs and Data cleanup,
  the data selection, the live service card and two switches of the editor. The "Classic interface" item of
  `⋯` went with the classic views (FX-14b).
- **Health pill**: one word and a colour from `/api/v1/status`: "All systems OK" (green), "Attention"
  (amber: connection degraded, a sink lagging, live service blocked), "Blocked" (red: SofaScore refuses us).
  It opens Health. **As built (FX-14a, FX-14b): a fourth state.** Grey **"Connection not tried"**
  ("Bağlantı denenmedi") while no request to SofaScore has been answered; amber also when the last request
  or the last connection check failed (`frontend/src/app/statusStore.ts:46-60` at `b6caf2f`). FX-14a kept a
  failed check in the browser tab; since FX-14b the state is the server's `/status.connection`
  (`never_tried`, `ok`, `failed`, with `last_check`; FX-19, #156), the same in every browser. The amber
  "sink lagging" case is still not read: `/status.sinks` exists since FX-13 (`sofascore_scraper/web/api/v1/meta.py:284`)
  but the shell does not read it (G22, 7.3).
- **Job pill**: shown while a job runs; "Sync · 42 %"; opens that Job detail. With two or more: "2 jobs".
- **Quick search** (`Ctrl K` / `⌘ K`): finds follows by name, tournaments in the catalog by name, and
  opens an event by its id. It searches stored data only and never sends a request to SofaScore. As built
  (#133) it also finds the screens, a job by its id and the recent jobs; stored tournaments come from
  `GET /tournaments?q=` (debounced), and a followed tournament is offered once, as the follow. **As built
  (FX-14a, #154): actions and one explicit SofaScore search.** The palette has actions (Add league, Back up,
  Export, Settings, Help, and Health since FX-14b), found by their label or by words in either language
  ("lig ekle", "add league", "yedek", "backup", "canlı", "help"); Back up and Export open their dialogs at
  once (`/backups?new=1`, `/exports?new=1`). When nothing stored matches, it offers **"Search SofaScore:
  '<text>'"** (`frontend/src/app/CommandPalette.vue:58-119` at `b6caf2f`), which opens the editor with
  `?q=<text>` and runs that one search there; the palette itself still sends nothing to SofaScore, so
  decision 20 holds for typing. Search as the user types, with SofaScore suggestions after two characters,
  is FX-20 (owner decision of 2026-10-06; 7.3, G29).
- **As built: the Sinks warning dot is not shown** (#132). The shell does not poll `/sinks`, so the rail
  cannot know that a sink fails or lags; Overview and the Sinks screen show it. FX-13 (#152) added the sink
  state to `/status` (`sinks`: `served`, `max_lag_events` and more; `sofascore_scraper/web/api/v1/meta.py:255-284` at
  `b6caf2f`), but no screen reads it yet (G22, 7.3).

Phone (below 768 px):

```
┌──────────────────────────────┐
│ Events             ● ⟳42% ⋯ │   top bar: title, health dot, job pill, menu
├──────────────────────────────┤
│                              │
│  content, one column         │
│  tables become card lists    │
│                              │
├──────────────────────────────┤
│  ⌂     ☰      ▦     ▶    ≡  │   bottom bar
│ Home Follows Events Jobs More│
└──────────────────────────────┘
```

"More" opens a sheet with the remaining screens. Tablet (768 to 1023 px) uses the collapsed rail.

## 4. Design system

One small design system, built in the project (no UI kit; decision 11 in section 10). Every screen uses
only these parts. FE-2 builds them first, with a page that shows each part in every state (a "kitchen
sink" route available in development builds only).

### 4.1 Principles

1. **Data first.** Tables, numbers and states are the content. Chrome is quiet.
2. **One meaning, one look.** A status has one colour and one word everywhere (4.6).
3. **Say what happens.** Every button that sends a request to SofaScore, starts a job or deletes something
   says so before the click.
4. **Nothing moves by itself** except progress. Lists refresh on request or on a slow timer, never in a way
   that moves the row under the pointer.
5. **Every state is designed**: loading, empty, error, locked, refused, signed out.

### 4.2 Layout grid and breakpoints

| Name | Width | Shell | Content |
|---|---|---|---|
| phone | < 640 px | bottom bar | 1 column, 16 px side margins, tables as card lists |
| small | 640–767 px | bottom bar | 1 column, 24 px margins |
| tablet | 768–1023 px | collapsed rail (64 px) | 8-column grid, 24 px margins |
| desktop | 1024–1439 px | full rail (232 px) | 12-column grid, 32 px margins, 24 px gutters |
| wide | ≥ 1440 px | full rail | 12 columns, content up to 1440 px wide, centred |

Detail pages use a 2:1 split on desktop (main content left, facts panel right) and stack on phone.
Side panels (raw view, filters on phone) slide in from the right, 480 px wide, full width on phone.

### 4.3 Typography

Geist for text and Geist Mono for numbers, ids, codes and JSON (both are already dependencies). Numbers in
tables use tabular figures.

| Token | Size / line height | Weight | Use |
|---|---|---|---|
| `display` | 30 / 36 | 700 | Overview numbers |
| `h1` | 24 / 32 | 700 | page title |
| `h2` | 18 / 26 | 600 | section title, dialog title |
| `h3` | 15 / 22 | 600 | card title |
| `body` | 14 / 22 | 400 | default text |
| `small` | 13 / 20 | 400 | secondary text, table cells in compact density |
| `caption` | 12 / 16 | 500 | labels, column headers (upper case, 0.04 em tracking) |
| `mono` | 13 / 20 | 400 | ids, JSON, codes |

The base is 14 px; the browser's font size setting scales everything (sizes are in `rem`). As built
(#107), the 14 px base applies to the new app only (the `.u-app` class of `frontend/src/ui/tokens.css`);
the classic views kept their 15 px until FX-14b (#161) removed them. Since FX-14a (#154), SofaScore's
English data (statistic names and the like) is marked `lang="en"`, so that upper-case captions follow the
text's language ("MATCH OVERVIEW", not the Turkish dotted "MATCH OVERVİEW").

### 4.4 Spacing, radius, elevation

- Spacing scale (px): 2, 4, 8, 12, 16, 24, 32, 48, 64. Components use only these.
- Radius: 6 px for controls, 10 px for cards and dialogs, 999 px for pills and badges.
- Elevation: cards are flat with a 1 px border. Only menus, dialogs, side panels and toasts have a shadow.
- Touch targets are at least 44 × 44 px on phone and tablet; 32 px rows are allowed in compact density on
  desktop only.

### 4.5 Colour tokens

Colours are CSS variables on `:root` and on `html.dark`. Components use tokens only, never hex values.
The direction of today's palette is kept (calm neutral ground, one green accent; decision 10). All text
pairs meet WCAG 2.2 AA (4.5:1 for text, 3:1 for large text and icons); FE-2 checks them with a test.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#f4f3ee` | `#121417` | page background |
| `--surface` | `#ffffff` | `#1a1d21` | cards, tables, dialogs |
| `--surface-2` | `#faf9f5` | `#1f2227` | table header, hovered row, code blocks |
| `--border` | `#e2dfd6` | `#2d3138` | card and control borders |
| `--line` | `#eceae3` | `#262a30` | row separators |
| `--text` | `#17191e` | `#eceae4` | main text |
| `--text-2` | `#3a3e46` | `#d2d0c9` | secondary text |
| `--muted` | `#5a5e67` | `#a9adb5` | hints, captions |
| `--accent` | `#1d6b45` | `#3fa56e` | primary buttons, links, focus ring, selection |
| `--accent-soft` | `#eef6f1` | `#1b2e24` | selected row, selected chip |
| `--ok-bg` / `--ok-fg` | `#ddeee3` / `#14502f` | `#1c3326` / `#8fd4ab` | success |
| `--warn-bg` / `--warn-fg` | `#fbebd2` / `#7a4700` | `#3a2a12` / `#f2c27a` | attention |
| `--danger-bg` / `--danger` | `#fbe4e1` / `#a12a1f` | `#3b1d1a` / `#f19a8f` | error, destructive |
| `--info-bg` / `--info-fg` | `#e2eaf6` / `#1f4478` | `#1b2738` / `#9cbcec` | information, running |
| `--neutral-bg` / `--neutral-fg` | `#ecebe6` / `#4a4e57` | `#262a30` / `#c3c6cc` | neutral states |
| `--focus` | `#1d6b45` | `#52b67f` | 2 px focus outline, 2 px offset |

Theme: Light, Dark, or System (default: System; decision 9). The choice is stored in the browser.

As built (#107): the tokens are in `frontend/src/ui/tokens.css`, and `frontend/tests/tokens.test.ts` checks
the values above and the WCAG AA contrast of 25 text pairs in both themes. The classic stylesheet's extra
tokens (`--surface-3`, `--hover`, `--ink` and others) remained for the classic views; the names both shared
took the values above, so `--muted` and three dark status backgrounds of the classic views changed
slightly. The classic views are gone (FX-14b), but `frontend/src/style.css` still carries the classic
stylesheet's shared names (only its comment changed); pruning it is a separate change with a visual diff
(section 7.3, "Open after FX-14b").

**Browser floor (#136).** Tailwind CSS 4 (cascade layers, `@property`, `color-mix()`) needs Safari 16.4,
Chrome 111 or Firefox 128 or newer; older browsers show the app unstyled or partly styled. The move kept
every screen pixel-identical in 140 screenshots: a compatibility block keeps Tailwind 3's fixed line
heights, table-cell padding, form opacity and the border, placeholder and cursor defaults. The changelog
states the floor.

### 4.6 Status vocabulary (one look per meaning)

A **badge** is a pill with an icon, a word and a tone. The icon makes the state readable without colour.

| Thing | Values → tone |
|---|---|
| Event status class (`Event.status.class`) | `not_started` → neutral "Scheduled"; `live` → info "In progress (when read)"; `completed` → ok "Finished"; `decided_without_play` → ok "Decided without play"; `void` → warn "Postponed / cancelled"; `unknown` → neutral "Unknown". The SofaScore text (`status.description`) is shown beside it in small text. As built (FX-14a): SofaScore's text ("Ended") is no longer shown beside the badge; on Event detail it is under a "Details" disclosure with the type and code. |
| Settlement (`Event.quality.settlement`) | `open` → none; `provisional` → info "Provisional"; `final` → none. `quality.stale` → warn "Stale"; `quality.status_regressed` → warn "Regressed". As built (FX-14a): on Event detail the fact is "Final score?" ("Skor kesin mi") with yes / provisional, may change / not yet. |
| Slice state (`Slice.state`) | `ok` → ok "Stored"; `empty` → neutral "No data at SofaScore"; `error` → danger "Failed" (with `error.reason`); `not_requested` → neutral outline "Not selected". |
| Job state (`Job.state`) | `queued` → neutral; `running` → info with spinner; `succeeded` → ok; `partial` → warn "Partly done"; `failed` → danger; `cancelled` → neutral; `interrupted` → warn. |
| Connection (`bridge.state`) | `ok` → ok "Connected"; `degraded` → warn "Requests refused"; `blocked` → danger "Blocked". As built (FX-14a, FX-14b): neutral "Not tried yet" while nothing was answered, warn "Last request failed" and "Last check failed"; from `/status.connection` (`never_tried`, `ok`, `failed`, `last_check`; FX-19), with the bridge times as the fallback for an older server. |
| Live service | running and not blocked → ok "Running"; running and `blocked` → warn "Paused by a block"; not running → neutral "Not running". |
| Sink | As built (#132), from the API's `state` (`ok`, `error`, `pending`) and `served`: `ok` → ok "Delivering"; `error` → warn "Retrying"; `lag_seconds` over 60 s → warn "Behind"; `pending` → neutral "Nothing delivered yet"; `served` false → neutral "Not delivered now". The design's `disabled` → danger "Stopped" has no API state (6.13). |
| Follow origin | `config` → neutral with lock "From config file"; `api` → none; `legacy` → neutral "From leagues.txt". As built (FX-14a): `api` has a badge "Added here" ("Buradan eklendi"), `legacy` reads "From the old league list" ("Eski lig listesinden"), and the job face `library` reads "Python". Since FX-19 every follow added in the web UI or the API is an `api` row (6.2). |

Word lists are locale keys (`status.event.completed`, `status.job.partial`, …), never the server's text.

### 4.7 Components

| Component | What it is | Notes |
|---|---|---|
| `AppShell`, `SideRail`, `BottomBar`, `TopBar` | the frame of 3.3 | rail state stored per browser |
| `PageHeader` | title, one-line description, primary action, secondary actions | the primary action is the only filled button of the page |
| `DataTable` | the main list component (4.8) | used by Follows, Events, Corrections, Jobs, Exports, Backups, Sinks, Logs |
| `FilterBar` | filter controls above a table, with "Clear filters" and a count of active filters | on phone the filters move into a side panel; active filters stay visible as removable chips |
| `Badge`, `StatusBadge` | the vocabulary of 4.6 | |
| `StatTile` | a number with a label and an optional small bar | Overview |
| `FactList` | label/value pairs | detail pages, Health |
| `ProgressBar` | determinate or indeterminate bar with percent and an ETA text | jobs |
| `Tabs` | keyboard-navigable tabs (arrow keys), state in the query string | |
| `Dialog` | modal with title, body, actions; focus is trapped; Esc closes unless work is in progress | |
| `ConfirmDialog` | for destructive or costly actions. States what will happen, in numbers when known. For irreversible actions the user types a word (the scope name, or "restore") before the button enables | clear, restore with replace, remove follow with data. As built (FX-14a, FX-14b): the word is a shown, localized word, compared without case in the user's language: "SİL" / "DELETE" for a clear or a removal with data, "GERİ YÜKLE" / "RESTORE" for a restore |
| `SidePanel` | a panel from the right for secondary content (raw view, filters on phone, a job's log) | |
| `Toast` | short message, bottom right (bottom on phone), `aria-live="polite"`; errors stay until closed, others close after 6 s | a toast never carries the only copy of important information |
| `EmptyState` | icon, one sentence, one action | every list has its own text |
| `ErrorState` | what failed, in words from the error code, a Retry button, and the request id (`X-Request-Id`) in small mono text with a copy button | 5.2 |
| `Skeleton` | grey blocks in the shape of the content while it loads | shown after 300 ms, so fast answers do not flicker |
| `LockedField` | a setting control that cannot be changed, with a lock icon and the reason (6.16) | |
| `JsonViewer` | collapsible tree of a JSON value, with search, copy path, copy value, copy all, download, and a switch to plain text | raw view; renders large payloads lazily (children when opened, a hundred at a time). As built (#133), a node is selected with a click or Enter, and its path, Copy path and Copy value appear above the tree: one keyboard path instead of hover buttons on every node |
| `SlicePicker` | data-type selection (6.3) | follows, settings defaults. As built (FX-14b): `SliceChecklist.vue` (the grouped checklist) and `sliceSelection.ts` (how a selection resolves) in `frontend/src/screens/follows/`, used by the editor, the edit page and Settings › Data (`SliceDefaults.vue`) |
| `SportBadge`, `SportSelect` | sport icon and name from `/sports` | 21 sports, data-driven |
| `TimeText` | a time in the browser's local zone, the UTC value on hover and for screen readers, relative ("3 min ago") where useful | decision 13 |
| `CommandPalette` | the quick search of 3.3 | |
| `CodeHint` | a CLI command in mono with a copy button | "Do this with `ssc watch`" |
| `FormError` (added by #132) | a refusal inside a form or dialog: the translated reason, the holder of a 409 with the link to its job, or the way to Health, and the request id | every dialog that starts a job |

As built, the parts are in `frontend/src/ui/` and the shell in `frontend/src/app/`. Besides the table:
`UiMenu` (a WAI-ARIA menu button), `CopyButton`, `UiIcon`, and `pagedList` (`frontend/src/app/pagedList.ts`:
cursor paging and filters in the query string for every list). `SlicePicker` is
`frontend/src/screens/follows/SlicePicker.vue`.

### 4.8 Data tables

One `DataTable` for every list.

- **Columns**: each screen defines its columns, which ones are shown by default and which are optional. A
  "Columns" menu lets the user show and hide optional columns; the choice is stored per table in the
  browser.
- **Sorting**: click a sortable header; a second click reverses. Sorting is done by the server where the
  route has a `sort` parameter, else only within the page that is shown (the header says so).
- **Filtering**: through the `FilterBar`; filters are in the query string.
- **Paging**: the API pages by cursor (`page.next_cursor`). The table shows "Previous" and "Next" and the
  page size (25, 50, 100). There is no total page count, because the API does not give one (decision 14).
- **Selection**: tables with bulk actions (Events) have a checkbox column; the bulk bar appears above the
  table with the count and the actions.
- **Row action**: the whole row is a link to the detail (keyboard: Enter). Row buttons are at the right end.
- **Density**: comfortable (44 px rows) or compact (32 px rows, desktop only); one setting per browser.
- **Phone**: each row becomes a card with the two or three most important fields; the rest is in the detail.
- **States**: skeleton rows while loading; the table's empty state; the error state in place of the rows;
  a thin progress line on top while a refresh runs over rows already shown.

### 4.9 Keyboard and accessibility

- Target: WCAG 2.2 AA. FE-2 keeps an automatic accessibility test (axe) on every screen, as
  `frontend/tests/a11y.test.ts` does today.
- Every control is reachable with Tab in reading order; focus is always visible (4.5 `--focus`).
- Shortcuts (listed under `⋯ → Keyboard shortcuts`, and off while typing in a field): `Ctrl K` quick
  search; `g o` Overview, `g f` Follows, `g e` Events, `g j` Jobs, `g s` Settings; `/` focuses the filter of
  the current table; `j` / `k` move in a table, `Enter` opens; `Esc` closes a dialog or panel; `?` shows the
  list.
- Tables are real tables (`<table>`, header cells with `scope`), with `aria-sort` on sorted columns.
- Status is never colour only (icon and word, 4.6). Progress bars have `aria-valuenow`.
- Toasts and job progress use `aria-live="polite"`; errors that block a form use `role="alert"`.
- `prefers-reduced-motion` turns off panel slides and spinners become static icons.

### 4.10 Language, numbers and time

- Every visible text is a key in `frontend/src/locales/en.ts` and `tr.ts`. A test fails when a key is
  missing in one of them or unused.
- Language rule (as today, `frontend/src/i18n.ts`): the choice made in this browser; else the server's
  explicit `display.language`; else the browser's language when it is Turkish; else English.
- Server texts are not shown as they are. The UI translates by code: error codes (`error.code`), job event
  codes (`code` in a log event), status classes, slice states. A server message without a known code is
  shown in small text under a translated general sentence. As built: the job log translates the event
  types and the codes. At `b3cb819` the server sent only two codes and logged the other lines of the sync
  and fetch path as text; FX-13 (#152) and FX-19 (#156) gave those lines a code and parameters, and FX-14b
  (#161) gave every code a text in both locales (`sync_season_list`, `sync_schedule`, `sync_details`,
  `sync_follow_listing` and eleven more, with leagues, seasons and follows by name and the breaker's reason in
  words); an unknown code shows the server's text (G24). The checks of the diagnostics tab have codes but
  still no translated texts, so their labels and summaries are the server's English text, with a note
  (#132, 6.14).
- As built, the new texts are under the key `ui` of both locale files (`frontend/src/locales/ui/en.ts`
  and `tr.ts`), and `frontend/tests/uiLocale.test.ts` fails when a named key is missing or a key is unused.
- Numbers use the locale's grouping (`12,345` / `12.345`). Times: decision 13. Durations: "2 h 5 min".
- Sport names come from the locale (`sport.<slug>`), with the registry's `name` as the fallback, so a sport
  added by SP-1 to SP-3 shows its English name until its translation is added.

## 5. Shared behaviour: loading, errors, the token, refusals

### 5.1 Requests the UI sends

- The UI talks only to `/api/v1` (FE-2 brief). It sends **no request to SofaScore and loads nothing from
  SofaScore's domains**. Images such as team logos are not loaded (they would come from SofaScore).
- A `GET` of the API never makes the server call SofaScore (#43). Only explicit buttons do: tournament
  search, the connection check, and starting a job. Each such button says so in its tooltip or text
  ("Sends a request to SofaScore").
- Polling, only while the browser tab is visible: `/status` every 15 s; the job list every 10 s while Jobs
  or Overview is open; a running job's progress through its event stream (`/jobs/{id}/events`, SSE). Other
  lists load when opened and on "Refresh".

### 5.2 Error codes and what the UI does

Every v1 error has `{"error": {"code", "message", "details", "request_id"}}` (`02-services.md` 2.6, 6).

| HTTP | `code` | What the UI shows and does |
|---|---|---|
| 400 / 422 | `invalid_request` | Form: the message next to the field named in `details` (location); other: an error toast. Settings: per key from `details.locked` / `details.read_only` (6.16). |
| 400 | `confirmation_required` | Should not happen (the UI always sends the confirmation); shown as an error toast. |
| 401 | `unauthorized` | The token prompt (6.15). With `details.reason = "too_many_attempts"`: the prompt shows "Too many wrong tokens. Try again in 27 s" with a countdown from `Retry-After`. The legacy login answers the same case as 429 with `detail.retry_after`; the client reads both shapes (`frontend/src/api/v1/errors.ts`), and since #132 the prompt uses the v1 routes. |
| 403 | `forbidden_origin` | Error page "This page was opened from another site. Open the app from its own address." |
| 404 | `not_found` | Detail pages: "Not found" page with a link back to the list. Raw view: "No payload stored for this slice". |
| 409 | `job_running` | "Another job is writing to the data folder: Sync started by the CLI on host X." with a link to that job (`details`) and a button "Open job". The form keeps its input so it can be sent again. |
| 409 | `data_operation_running` | "A backup / restore / clear is running. Try again when it has finished." Link to the job. |
| 409 | `instance_running` | Restore or clear while the live service runs: "The live service (`ssc watch`, pid 4121 on host X) uses this data folder. Stop it first." |
| 409 | `follow_exists` | Follow editor: "You already follow this tournament." Link to it. |
| 409 | `follow_managed` | "This follow comes from the config file. Change it in sofascore.toml." |
| 501 | `not_supported` | "Not available on this server: Parquet needs the pyarrow package." Controls that will always give 501 are disabled ahead when the server says so (6.10). |
| 502 / 503 | `upstream_error`, `blocked`, `rate_limited` | Only from buttons that call SofaScore. A translated reason and a link to Health → Run connection check. |
| 507 | `storage_error` | "The data folder could not be written: disk full or no permission." Shows `details.store_message` in small text. |
| 500 | `internal` | "Something went wrong on the server." With the request id and a link to Logs. |
| — | network failure | A banner at the top: "Can't reach the server. Retrying…" The app keeps the last data, greys it and retries with back-off. |

Every error state shows the `request_id` in small mono text, so a log line can be found.

### 5.3 The token

When the server has a token (`/status.auth_required` is true) and the browser has no session, every API
call answers 401 and the app shows the token prompt over everything (6.15). After a correct token the server
sets an HttpOnly session cookie; the page does not keep the token. Sign out is in the `⋯` menu and in
Settings, only when a token is in use.

### 5.4 Locked and refused writes

- A setting that the config file, the environment or a command-line flag pins is shown **locked**: visible,
  not editable, with the reason (6.16).
- A follow from the config file is shown with a lock; Edit, Disable and Remove are off, with the reason.
- While a job holds the writer lease, buttons that start a writing job stay enabled. A click that is refused
  with 409 shows the message of 5.2. The UI does not guess the lock ahead, because another process can take
  or release it at any moment; the only exception is a banner on Backups and Maintenance while
  `/status.active_job` is set.

## 6. Screens

Each screen has: purpose, who and when, a wireframe, its elements with the API route and the field they
show, its states, and where it leads. "P21" next to a route meant that the route was planned and did not
exist yet; at `b3cb819` every such route exists (P21, #122 to #127), and the rows where the built route or
screen differs say so ("As built"). Section 7 lists the state of every route and gap.

### 6.1 Overview (start page)

**Purpose.** Answer in five seconds: is everything working, what is running, what needs me.
**Who / when.** The operator, at every visit; the start page (decision 1).

```
┌ Overview ──────────────────────────────────────────────────────────────────── [Sync all follows] ┐
│                                                                                                  │
│ ┌ Needs attention (2) ─────────────────────────────────────────────────────────────────────────┐ │
│ │ ⚠ Sink "ops" is 1,240 events behind (last error: HTTP 503, 4 min ago)            [Sinks →] │ │
│ │ ⚠ Last sync finished partly: 12 matches failed                                    [Job →]  │ │
│ └──────────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                                  │
│ ┌ Matches ────┐ ┌ With details ┐ ┌ Follows ────┐ ┌ Disk ───────┐                                 │
│ │ 48,210      │ │ 46,903  97 % │ │ 14 · 3 sports│ │ 2.1 GB      │                                 │
│ └─────────────┘ └──────────────┘ └─────────────┘ └─────────────┘                                 │
│                                                                                                  │
│ ┌ Services ───────────────────────────────┐ ┌ Running now ───────────────────────────────────┐  │
│ │ Connection     ● Connected   2 min ago  │ │ Sync · Premier League 25/26        web          │  │
│ │ Request rate   5 / s                    │ │ ███████████░░░░░░░  58 %  details 812 / 1,400   │  │
│ │ Live service   ● Running · page · 3     │ │ about 4 min left                 [Open] [Stop]  │  │
│ │ Sinks          ● 1 of 2 behind          │ └─────────────────────────────────────────────────┘  │
│ │ Scheduler      next sync 18:00          │                                                      │
│ │                              [Health →] │ ┌ Recent jobs ──────────────────────────── [All →] ┐ │
│ └─────────────────────────────────────────┘ │ ✓ Refresh        cli        09:10   1 min 12 s  │ │
│                                             │ ◐ Sync           scheduler  06:00   partly done │ │
│ ┌ Recent corrections ─────────── [All →] ┐ │ ✓ Export events  web        yesterday           │ │
│ │ Arsenal – Chelsea  score 2-1 → 2-2  3h │ └─────────────────────────────────────────────────┘ │
│ │ Lakers – Celtics   status → void    1d │                                                      │
│ └────────────────────────────────────────┘                                                      │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Element | Shows / does | Route | Field |
|---|---|---|---|
| Sync all follows | starts a sync of every enabled follow | `POST /api/v1/jobs` `{kind: "sync"}` (target "all follows": P13) | — |
| Needs attention | a list built from the items below: connection not `ok`; live service `blocked`; a sink disabled or behind; the last job of each kind `failed`, `partial` or `interrupted`; `catalog_rebuild_reason` set; old-layout data present. Hidden when empty. | `/status` | `bridge.state`, `live.blocked` (P21), `sinks[]` (proposed, 7.3), `summary.catalog_rebuild_reason` (P21) |
| Matches | stored matches | `/status` (P21: data summary) | `summary.matches` |
| With details | matches with details, and the share | `/status` (P21) | `summary.details`, `summary.matches` |
| Follows | number and sports | `GET /follows` (P21) | count, distinct `sport` |
| Disk | data folder size | `/status` (P21) | design: `summary.disk.total`. As built (#132): the sum of `summary.disk.entries` (every top-level entry, `v3/` and `.meta/` included), because `disk.total` counts only the 2.x trees (seasons, matches, details, datasets; `sofascore_scraper/services/status.py:133-135` at `b3cb819`) and reads 0 for a v3 data folder (G21) |
| Connection | state and last success | `/status` | `bridge.state`, `bridge.last_success_at` |
| Request rate | the limit | `/status` | `throttle.requests_per_second`, `throttle.enabled` |
| Live service | running, source, number of sports | `/status` (P21: live fields) | `live.running`, `live.source`, `live.sports`, `live.blocked` |
| Sinks | one line | `/sinks` (built by P21, #122; read once per visit) | see 6.13 |
| Scheduler | next run | `/status` (P29) | `schedule.next_runs`. As built (#132): on or off from `capabilities.scheduler` only. P29 (#128) has added `schedule.enabled` and `schedule.next_runs[]` to `/status`, but the screen still does not read them at `b6caf2f` (`frontend/src/screens/OverviewScreen.vue:247-248`); FX-14b did not wire them (7.3, open after FX-14b) |
| Running now | the active job with progress; Open, Stop | `/status`; `/jobs/{id}/events`; `POST /jobs/{id}/cancel` | `active_job` (Job), `progress` |
| Recent jobs | last 5 jobs | `GET /jobs?limit=5` | `kind`, `state`, `origin.face`, `started_at`, `finished_at` |
| Recent corrections | last 5 changes | `GET /changes` (P21) | `event_id`, `fields`, `recorded_at_utc` |

**States.** Loading: skeleton tiles. First run (no follows): the whole page is one empty state "Follow
your first league" with the button to the Follow editor and one line on what the platform does. `/status`
fails: the error state in the Services card; the tiles that depend on it show "—". Routes of P21 missing
(FE-2 before P21): the tiles that need them are not shown. As built: FE-2a (#107) showed no data tiles and
one line saying so; FE-2b (#132) added the four tiles, the live service and the sinks in the Services card,
and the attention items for a blocked live service, an unreadable data folder, a sink that retries or is
behind, an index that needs a rebuild and old-layout data; #133 added the Recent corrections card, which
reads the event of each of the five changes for the match names (`/changes` has no names, G19).

**As built (FX-14a, #154).** The primary action is **"+ Add league"**, also on the first run; **"Update
all"** ("Tümünü güncelle", the design's "Sync all follows") is a secondary button once something is
followed. A dismissible **"Getting started"** card ("Nasıl başlanır": Add league → Download → Look at the
matches / Export; `frontend/src/app/GettingStarted.vue`) sits above the content; dismissing it is kept in
this browser, and Help brings it back. Recent jobs name the league ("Download · Premier League") from the
stored catalog, else from the job's `progress.league_name`, else "League #17"; counts have units ("0 / 1
matches"). **Finish toasts:** the shell follows the jobs started in this browser and the running job that
`/status` reports, CLI and scheduler jobs included, until each ends (`frontend/src/app/jobWatch.ts`), and
tells each once: "Premier League downloaded: 4 matches" with "See matches", "Premier League is up to date:
no new matches", a partial or failed download with its job link; a stopped download says nothing. The
count is the job's `result.details_done` (the match details fetched in this run), not a league total.
Since FX-14b the Connection line reads `/status.connection` (3.3).

**Navigation.** Every card links to its screen.

### 6.2 Follows

**Purpose.** What the platform collects: tournaments (and teams, players, single events) with their
seasons and data selection.
**Who / when.** The curator, when the season starts or something should be added.

```
┌ Follows ──────────────────────────────────────────────────────────────── [Sync all] [+ Follow] ┐
│ [Search name…]  Sport [All ▾]  Kind [All ▾]  Origin [All ▾]  [✓ Enabled only]                  │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Name                  Sport       Seasons    Data            Coverage   Last sync   Origin       │
│ ─────────────────────────────────────────────────────────────────────────────────────────────────│
│ Premier League        ⚽ Football  last 2     Custom · +odds  ████ 98 %  2 h ago     🔒 config  ⋯ │
│ LaLiga                ⚽ Football  current    Defaults        ███░ 81 %  2 h ago                ⋯ │
│ NBA                   🏀 Basket.  current    Defaults        ████ 99 %  1 d ago                ⋯ │
│ ATP Wimbledon         🎾 Tennis   all        Custom          ██░░ 52 %  —           legacy     ⋯ │
│ Team #2672            ⚽ Football  current    Defaults        —          —                     ⋯ │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 14 follows                                                                   [‹ Prev] [Next ›] │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
row menu ⋯ : Sync now · Edit · Disable · Remove
```

| Element | Shows / does | Route | Field |
|---|---|---|---|
| Table | one row per follow | `GET /api/v1/follows` (P21), `?q=` for the name search | `kind`, `entity_id`, `name`, `sport`, `seasons`, `slices`, `live`, `enabled`, `origin` |
| Data column | "Defaults" when `slices` is null; "Custom" otherwise; a chip "+odds" when the odds group is on | same | `slices` |
| Live column (optional column) | "Watched by `ssc watch`" when `live` is true | same | `live` |
| Coverage | details / matches of the tournament | `/status` data summary by tournament (P21) | `summary.tournaments[].coverage` |
| Last sync | end of the newest sync job that included this follow | `GET /jobs?kind=sync` (target matching: P13) | `finished_at`. As built (#133): the newest finished sync whose spec names this tournament or every follow |
| Sync now | sync of this follow | `POST /jobs {kind: "sync", spec: {follows: [...]}}` (spec of P13) | — As built (#133): P13 (#113) did not add a spec by follow, so Sync now was offered for tournament follows only (G23). FX-13 (#152) added `sync {follows: [...]}` and FX-19 (#156) made team, player and event follows download; since FX-14b (#161) **"Download now"** sends `sync {follows: [id]}` for every kind, with the follow's own season choice, where a league's used to download every season through `league_id` (`frontend/src/screens/follows/FollowActions.vue:128` at `b6caf2f`) |
| Disable / enable | | `PATCH /follows/{id}` (P21) | `enabled` |
| Remove | ConfirmDialog: "Stop following LaLiga? Stored matches stay; nothing more is fetched." | `DELETE /follows/{id}` (P21) | — As built (FX-14b): the dialog of a league has **"Also delete its stored matches"** ("Kayıtlı maçlarını da sil"), which asks for the typed word "SİL" / "DELETE" and sends `DELETE /follows/{id}?delete_data=true` (FX-19); the answer names the clear job (`data.clear_job`), which the toast links and the shell follows (`frontend/src/screens/follows/FollowActions.vue:76-95`) |
| Move into the app | for a follow of the old league list (`origin: "legacy"`): makes every field editable | `PATCH /follows/{id} {origin: "api"}` (FX-19) | As built (FX-14b): **"Move here"** ("Buraya taşı") on the follow's page, its edit page and its row menu (`frontend/src/screens/follows/MoveFollow.vue:27`) |
| + Follow | opens the Follow editor | — | — As built (FX-14a): **"+ Add league"** ("Lig ekle") |

**States.** Empty: "You follow nothing yet. Follow a league to start collecting." with "+ Follow". Origin
`config`: lock icon; Edit, Disable, Remove disabled with the tooltip "Set in sofascore.toml; change it
there". 409 `follow_managed` if it still happens. Loading, error: 4.8. As built (#133): a follow from
`leagues.txt` can only have its sport changed (the record's `writable` lists the fields PATCH takes). Name,
kind, origin and "enabled only" are filtered by the server; the Sport filter works within the list shown,
because `/follows` has no `sport` parameter (G18). FX-13 added `?sport=` to `/follows`; the screen still
filters the sport within the list at `b6caf2f` (`frontend/src/screens/follows/FollowsScreen.vue:66-72`;
G18 in 7.3).

**As built (FX-14a, FX-14b).** The words: "Add league", "Download now", "Matches with details" (the
design's Coverage), "Last download" and "Added from" (Origin); the `api` origin has an "Added here" badge,
also on the phone's cards, which used to show the raw "api". Since FX-19 (#156) a follow added in the web
UI or the API is always an `api` row of the follows table, with or without a config file; before, without
a config file, a tournament follow went to `config/leagues.txt` and came back as a locked `legacy` row with
only its sport writable (the review's second problem). A `legacy` row is still writable only in `sport`
and `origin`; "Move here" adopts it.

**Navigation.** Row → Follow detail. Sync now → toast with the job link.

### 6.3 Follow editor (add and change), with data selection

**Purpose.** Add a follow or change one, including **which data types are fetched**.
**Who / when.** The curator. A four-step dialog page for a new follow; the same sections on one page for a
change.

Step 1: what to follow.

```
┌ Follow something ─────────────────────────────────────────────── step 1 of 4 ┐
│ ( ● Tournament )  ( ○ Team )  ( ○ Player )  ( ○ Single event )              │
│                                                                             │
│ Sport  [⚽ Football ▾]                                                       │
│ Name   [premier league              ] [Search at SofaScore]                 │
│        Sends one request to SofaScore.                                      │
│                                                                             │
│  ○ Premier League        England    #17                                     │
│  ○ Premier League 2      England    #3060                                   │
│  ○ Premier League        Russia     #203                                    │
│                                                                             │
│ Or enter the id: [      ]                                                   │
│                                                         [Cancel] [Next ›]   │
└─────────────────────────────────────────────────────────────────────────────┘
```

**As built (FX-14a, FX-14b), step 1.** The page is titled **"Add a league or team"** ("Lig ya da takım
ekle"). Team, Player and Single match were shown disabled as "coming soon" behind the flag
`MORE_FOLLOW_KINDS` by FX-14a, while nothing downloaded for them; FX-14b turned the flag on
(`frontend/src/screens/follows/followText.ts:16` at `b6caf2f`), after FX-19 built the search and the
downloads. **Teams and players are searched by name**, one kind per search: `POST /tournaments/search
{q, sport, kinds: [kind]}` (FX-19; tournaments keep SofaScore's tournament search, teams and players use
its general search), one request per search, on the button or Enter only
(`frontend/src/screens/follows/FollowEditorScreen.vue:88`). A hit shows its kind, sport, country, a
player's team and "Already added"; picking it brings its sport along. A single hit that is not added yet
is pre-selected, so Next is the only click left. A newcomer who types a team under "League" gets a hint to
switch the kind. A **single match** is added by its number ("Or enter the SofaScore number (from the URL)",
with an example line), or from the match page (6.6). Searching all kinds at once and suggestions while
typing are FX-20 (G29).

Step 2: seasons. `( ● Current season ) ( ○ Last [2] seasons ) ( ○ All seasons ) ( ○ Choose… )`. "Choose"
lists the seasons that are stored (`GET /tournaments/{id}/seasons`, P21) with a button "Get the season list
from SofaScore", which starts a listing job. As built (#133): there was no listing job kind (G15), so the
button was not there and the step said that the season list is read at the first sync. **As built
(FX-14b, #161):** a league without a stored season list shows **"Get the season list from SofaScore"**
("Sezon listesini SofaScore'dan al"), one click that starts `sync {league_id, only: "seasons"}` (FX-13),
follows the job and then lists the season names as checkboxes; a failed list links its job
(`frontend/src/screens/follows/SeasonChooser.vue:59`). The same button is on the edit page and is the
Seasons tab's empty state. So seasons can be chosen before a league's first download. For a team or a
player, `seasons` is the time window that FX-19 defines, in words: "Last 12 months" (current), "Last 2
years" (`last:2`), "As far back as listed" (all), with a note on what is read; a single match has no
seasons step. The words "Last [2] seasons" and "Choose seasons…" are FX-14a's.

Step 3: **data selection** (the `SlicePicker`).

```
┌ Which data? ───────────────────────────────────────────────────── step 3 of 4 ┐
│ ( ● Use the defaults for Football )   ( ○ Choose for this follow )            │
│                                                                               │
│ MATCH DATA                                       fetched once per match       │
│  [✓] Match (score, status, teams)   always                                    │
│  [✓] Statistics                                                               │
│  [✓] Line-ups                                                                 │
│  [✓] Incidents (goals, cards)                                                 │
│  [✓] Head to head                                                             │
│  [✓] Pre-game form                                                            │
│  [✓] Team streaks                                                             │
│                                                                               │
│ BETTING ODDS                                     off by default               │
│  [ ] All markets                 refetched until kick-off, then once after   │
│  [ ] Featured markets                                                         │
│  [ ] Odds changes                                                             │
│  [ ] Winning odds                                                             │
│  ⓘ Odds history exists only for what is fetched: by repeated syncs before     │
│    kick-off, or by the live service. Provider: 1 (set in Settings).          │
│                                                                               │
│ SEASON DATA                                      fetched once per season      │
│  [ ] Standings   [ ] Season statistics   [ ] Cup tree                         │
│ TEAM AND PLAYER DATA                             fetched once per team        │
│  [ ] Squads                                                                   │
│                                                                               │
│ About 7 requests per finished match; 1 per season for season data.            │
│                                                       [‹ Back] [Next ›]       │
└───────────────────────────────────────────────────────────────────────────────┘
```

**As built (#133), step 3 was read-only.** The follows API took no data selection yet (`FollowCreate` and
`FollowPatch` had no `slices`; P27), and the registry had no groups, odds slices or season data (G9; P27,
P28). The picker showed the defaults read-only; the odds group listed the four markets of the wireframe
unticked and disabled; the season, team and player data groups were not shown.

**As built (FX-14a, #154):** still read-only, the step collapsed to one line ("For each match: Match,
Statistics, … Betting odds: off. About 7 requests per finished match.") with a "Details" disclosure; the
radios were gone, every data type had its name instead of the raw key, odds were listed once from the
registry's groups, and the developer notes ("P27", "P28", "core", "Provider: 1") were removed.

**As built (FX-14b, #161), the choice works.** P27 (#134) added `slices` to `FollowCreate` and
`FollowPatch` and the registry fields `group`, `owner`, `phases` and `selected`; P28 (#140) the odds,
season, team, player and sport data. The step and the edit page offer **"Use the defaults for
<sport>"** (`slices: null`) or **"Choose for this follow"** (`{include: [...]}`), the radios of the design
again, with a grouped checklist from `GET /sports/{slug}`: match data (the match itself always), betting
odds (a header badge "off by default" or "on"), and season, team, player and sport data ("fetched once per
season"). The groups are by section (the slice's `owner`, and odds), not one group per `group` value.
`required` does not lock a box (P27 owner decision: every data type is selectable); it only marks optional
match data as "not always there in this sport". Choosing starts from the defaults' ticks (`selected`), and
the request cost per match follows the ticks. A follow that cannot hold a selection (one of the old league
list, which answers 400 `unsupported: ["slices"]`) says why and offers "Move here" (6.2).

Step 4: review. Name (editable), kind and id, sport, seasons, data ("Defaults" or the chosen list), "Include
in live watching" switch with the hint "Used by `ssc watch` on the server; the web UI shows no live
scores", "Sync now after saving" checkbox (on). Button "Follow". As built (FX-14a, FX-14b): the checkbox is
"Start downloading right away" and starts `sync {follows: [id]}` for every kind
(`frontend/src/screens/follows/FollowEditorScreen.vue:152`); the button is "Add". The live switch is still
offered for a player follow, although `ssc watch` skips player follows (FX-19); hiding it there is FX-20
(G31).

| Element | Shows / does | Route | Field |
|---|---|---|---|
| Kind | tournament, team, player, event | — | `FollowSpec.kind` |
| Sport | the registry's sports | `GET /api/v1/sports` | `slug`, `name`, `i18n_key` |
| Search at SofaScore | tournament search; tournaments only | built as `POST /api/v1/tournaments/search` with the body `{q, sport}` (P21, #124; G5); `kinds` (`tournament`, `team`, `player`) since FX-19 (#156) | hits: `id`, `name`, `category`, `sport`; already followed hits are marked. Since FX-19 hits carry `kind`, country and a player's team (component name still `TournamentHit`) |
| Id field | for team, player and event, and for a known tournament id | — | `entity_id` |
| Seasons | | `GET /tournaments/{id}/seasons` (P21) | `FollowSpec.seasons`: `"current"`, `"last:N"`, `"all"`, ids |
| Defaults / Choose | null selection or a custom one | — | `FollowSpec.slices`: null, or `{"include": [...]}` |
| Slice list | grouped by `group`, with name, phase hint, owner hint, default | `GET /sports/{slug}` (`slices[]`; fields `group`, `owner`, `phases`, `keep_history` from P27/P28, 7.3) | `key`, `default_enabled`, `required` |
| "always" | slices with `required` true cannot be unticked | same | `required`. Changed by the owner's decision of P27: every data type is selectable, `required` only means "counts for completeness", and the box is not locked (FX-14b); only the match itself is always fetched |
| Defaults text | what "defaults" means for this sport | `GET /settings` (P27: `defaults.slices`, `slices.<sport>`) | value |
| Requests per match | the number of selected event slices, plus 1 for the match itself | computed | — |
| Odds provider | read-only here, link to Settings | `GET /settings` | `client.odds_provider` (P28). As built (FX-14a): not shown ("Provider: 1" was developer text); the odds country is recorded only when the user sets `[client] odds_country` (delegated decision of 2026-10-06) |
| Live switch | | — | `FollowSpec.live` |
| Follow (save) | | `POST /api/v1/follows` (P21; `slices` with P27) | — |
| Save (change) | | `PATCH /api/v1/follows/{id}` (P21) | — |
| Sync now after saving | | `POST /jobs {kind: "sync"}` (P13 spec) | — |

**States.** Search: a spinner on the button; 503 `blocked` / `rate_limited`: "SofaScore refused the search"
with a link to Health; no hits: "No tournament found. Check the sport, or enter the id." (since FX-19 a 404
answer of SofaScore is an empty list, and the text is per kind). 409
`follow_exists`: link to the existing follow. A sport whose slices are not all known yet: the groups that
P28 adds are not shown until the registry has them. Changing the selection of an existing follow: a note
"Matches already stored keep their data. Newly selected data is fetched at the next sync. Data you unselect
is not deleted."

**Navigation.** Save → Follow detail (and a job toast when Sync now was on). Cancel → back.

### 6.4 Follow detail

**Purpose.** One follow: its seasons, coverage, data selection, its matches and its jobs.

```
┌ Follows › Premier League ─────────────────────────────── [Sync now] [Edit] [⋯] ┐
│ ⚽ Football · England · tournament #17 · 🔒 from config file                     │
├────────────────────────────────────────────────────────┬────────────────────────┤
│ [Seasons] [Events] [Data selection] [Jobs]            │ Seasons     last 2     │
│                                                        │ Data        Custom     │
│ Season    Matches  With details  Coverage  Listing     │             +odds      │
│ 25/26        380        371      ███░ 98 %  2 h ago    │ Live        watched    │
│ 24/25        380        380      ████ 100 % 3 d ago    │ Enabled     yes        │
│ 23/24 (not followed)                                   │ Last sync   2 h ago ✓  │
│                                                        │ Origin      config     │
└────────────────────────────────────────────────────────┴────────────────────────┘
```

| Element | Shows | Route | Field |
|---|---|---|---|
| Header facts | | `GET /follows/{id}` (P21); `GET /tournaments/{id}` (P21) | follow fields; `Tournament.name`, `category` (schema v1 has `category_id` only; the tournament resource adds the `category` record and `followed`, P21 #123) |
| Seasons tab | per season: matches, details, coverage, age of the season's listing | `GET /tournaments/{id}/seasons` (P21); `/status` summary by tournament (P21) | `Season.id`, `name`, `year`; counts. As built (#133): the API has no per-season counts (G17); the tab lists the stored seasons (those outside a chosen list marked) with Events and **Sync this season** per season, and the tournament's coverage is in the facts panel. Added: **Fetch missing details** for the tournament (`fetch` with `league_id`), the new home of the classic Download view's missing-details fetch |
| Events tab | the Events table (6.5) with `tournament=` fixed | `GET /events?tournament=` (P21) | 6.5 |
| Data selection tab | the SlicePicker, read-only, with "Edit" | as 6.3 | |
| Jobs tab | the jobs that included this follow | `GET /jobs` (filter by target: proposed, 7.3; until then: kind `sync`, newest first) | 6.8. As built (#133): the newest 50 sync jobs, filtered within the page by the tournament in their spec (G12) |

**States.** Not found: 404 page. Config follow: Edit is disabled with the reason.

**As built (FX-14b, #161).** The Seasons tab shows **per-season counts** (G17, `GET
/tournaments/{id}/seasons?include=counts`, FX-13): "4 matches · 3 finished · 3 with details", a completion
bar and the age of the season's schedule ("Fixtures read: 5 min ago" / "not read yet"); the age of the
tournament's own season list has no route (G27). The **Jobs tab** reads `GET /jobs?target=<kind>:<id>`
(FX-13, FX-19) merged with the downloads of every follow, shown as "Download · Arsenal". A **team follow**
has a Matches tab with its stored matches (`/events?participant=`); a single-match follow links to its
match. When a download of the follow ends, the page reads its last download, its counts and its seasons
again (FX-14a).

### 6.5 Events

**Purpose.** Find matches in the stored data, see their state, fetch what is missing.
**Who / when.** The curator and the data user.

This is a list of **stored** events. It does not update by itself, and an event shown as "In progress (when
read)" is the state of the last read, not a live score (R2).

```
┌ Events ──────────────────────────────────────────────────────────────────────── [Refresh] ┐
│ Sport [All ▾] Follow/Tournament [All ▾] Season [All ▾] From [2026-09-01] To [         ]   │
│ Status [Scheduled] [In progress] [Finished ✓] [Decided w/o play] [Postponed/cancelled ✓]  │
│ Data [All ▾ | complete | missing]   Team [            ]                 2 filters · Clear  │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│ ☐ Start ↓           Sport Tournament      Rd  Home             Score  Away       Status    Data │
│ ☐ 30 Sep 21:00      ⚽    Premier League  6   Arsenal          2 – 1  Chelsea    Finished  7/7  │
│ ☐ 30 Sep 19:30      ⚽    LaLiga          7   Sevilla          0 – 0  Betis      Finished  5/7 ⚠│
│ ☐ 30 Sep 18:00      🎾    ATP Tokyo       QF  Alcaraz       6-4 3-6 … Sinner     Prov.     4/4  │
│ ☐ 29 Sep 20:00      ⚽    LaLiga          7   Girona           —      Getafe     Postponed —    │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│ 2 selected:  [Fetch missing data]  [Fetch again]                    [‹ Prev] [Next ›] 50 ▾ │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Element | Shows / does | Route | Field |
|---|---|---|---|
| Filters | sport, tournament (follows first, then other stored tournaments), season, date range, status classes, data complete or missing, team name | `GET /api/v1/events?sport=&tournament=&season=&from=&to=&status=&participant=&has=` (P21; `status` and all statuses stored: ST-27, P27) | — |
| Status chips | multi-select of the six classes; default: Finished and Decided without play, which matches today's "finished only" (`fetch.only_finished`) | same, `status=` | `Event.status.class`. As built (#133): deselecting every chip means any status and is written `status=any` in the address, so the default does not come back |
| Start | | same | `start_utc` (TimeText) |
| Sport | | same | `sport` |
| Tournament | name by id, from a cached tournament list | `GET /tournaments` (P21) | `tournament_id` → `Tournament.name` |
| Rd (optional) | round | same | `round.name` / `round.number` |
| Home, Away | | same | `participants.home.name`, `participants.away.name` |
| Score | the headline score; tennis shows sets | same | `score` by family (`home`, `away`; `periods`; `sets`) |
| Status | badge of 4.6 and quality flags | same | `status.class`, `status.description`, `quality.settlement`, `quality.stale` |
| Data | selected slices stored / selected; ⚠ when one failed | `GET /events?…&include=slices_summary` (G7, built by P21 #123; as built it also shows "schedule only" for a listing) | `Slice.state` counts |
| Optional columns | event id, season, category, custom id, observed at, change time | same | `id`, `season_id`, `category_id`, `custom_id`, `quality.observed_at_utc`, `quality.change_ts` |
| Fetch missing data | fetch the missing selected slices of the selected events | `POST /jobs {kind: "fetch", spec: {events: [...]}}` (P13 spec; today `selections[].match_ids`) | — As built (#133): a `fetch` job with one selection per tournament (`selections[].league_id`, `match_ids`), sent only for the selected events with something missing |
| Fetch again | re-read the selected events | `POST /jobs {kind: "refresh", spec: {events: [...]}}` (P13) | — As built (#133): `refresh` takes only a `league_id`, so this is also a `fetch` job with `selections[].match_ids` (a fetch of explicit ids reads them again). Events without a tournament cannot be fetched by id (G16); they are left out and the dialog says how many |
| Sort | start time (default newest first) | `sort=-start_utc` / `start_utc` (G6, built by P21 #123) | |

**As built (FX-14a, #154).** The default status filter is **every status**, scheduled and live included,
with a visible "All statuses" chip that is pressed while no status is chosen; old `status=any` links still
work (`frontend/src/screens/events/EventsList.vue:58` at `b6caf2f`). The review found that the design's
default (finished only) hid upcoming fixtures. SofaScore's status text ("Ended") is no longer shown beside
the badge, and the Team field has a placeholder. Since FX-14b, events without a tournament can be fetched
by their numbers (`fetch {event_ids}`, FX-13; G16).

**States.** Empty with filters: "No stored match matches these filters." and "Clear filters". Empty without
data: "No matches stored yet. Follow a league and sync it." 409 `job_running` on Fetch: 5.2. A filter that the
server does not support yet (before P27): the control is hidden. As built (#133), every filter of the
wireframe is sent to the server (`has=details|missing` for Data; the Team field is the text filter `q=`, a
participant's name, since `participant=` takes ids).

**Phone.** One card per event: "Arsenal 2 – 1 Chelsea", tournament and time on the second line, status
badge at the right.

**Navigation.** Row → Event detail.

### 6.6 Event detail, with slices and the raw view

**Purpose.** Everything stored about one match, and its raw payloads.

```
┌ Events › Arsenal – Chelsea ────────────────────────────────────────── [Fetch again] [⋯] ┐
│ Premier League · 25/26 · Round 6 · 30 Sep 2026 21:00 (19:00 UTC)                        │
│                                                                                          │
│          Arsenal            2 – 1            Chelsea                                     │
│                        HT 1 – 0  ·  Finished (Ended)  ·  Final                           │
├──────────────────────────────────────────────────────────────┬───────────────────────────┤
│ [Overview] [Statistics] [Line-ups] [Incidents] [Data] [Corrections] [Odds]               │
│                                                              │ Event id    16950622      │
│  (Statistics: friendly view of the stored payload,           │ Sport       Football      │
│   or the raw tree for slices without a view)                 │ Status      finished/100  │
│                                                              │ Settlement  final         │
│                                                              │ Read at     30 Sep 23:12  │
│                                                              │ SofaScore   30 Sep 23:05  │
│                                                              │  changed                  │
│                                                              │ Raw event   [View] [⇩]    │
└──────────────────────────────────────────────────────────────┴───────────────────────────┘
```

The **Data** tab lists every slice of the event:

```
│ Data type        State                 Fetched          Checked          Raw              │
│ Match            ● Stored              30 Sep 23:12     30 Sep 23:12     [View] [⇩]       │
│ Statistics       ● Stored              30 Sep 23:12     30 Sep 23:12     [View] [⇩]       │
│ Line-ups         ● Failed · 429 · ×2   28 Sep 10:00     30 Sep 23:12     [View] [⇩]       │
│ Head to head     ○ No data at SofaScore                 30 Sep 23:12     —                │
│ Odds (all)       ◌ Not selected                                          —                │
│                                                         [Fetch missing data]             │
```

The **raw view** opens in a side panel (or full page at `/events/:id/raw/:key`):

```
┌ Raw · statistics ───────────────────────────────── [Copy] [Download .json] [✕] ┐
│ Fetched 30 Sep 23:12 · 14.2 KB · sha256 3fa9…  [Tree | Text]  [Search…      ]  │
│ ▾ statistics: [3]                                                              │
│   ▾ 0: {period: "ALL", groups: [6]}                                            │
│       period: "ALL"                                                            │
│     ▸ groups: [6]                                                              │
│   ▸ 1: {period: "1ST", groups: [6]}                                            │
└────────────────────────────────────────────────────────────────────────────────┘
```

| Element | Shows / does | Route | Field |
|---|---|---|---|
| Header | names, score, status, tournament, season, round, start | `GET /api/v1/events/{id}` (P21) | `participants`, `score` (by family: `half_time`, `periods[]`, `sets[]`…), `status`, `tournament_id`, `season_id`, `round`, `start_utc`, `winner`, `aggregate` |
| Facts panel | ids and quality | same | `id`, `status.type`, `status.code`, `quality.settlement`, `quality.observed_at_utc`, `quality.change_ts`, `quality.stale`, `quality.status_regressed`, `quality.tier_hint` |
| Overview tab | the key facts, the aggregate of a two-legged tie, the venue and referee when the raw event has them | same; venue and referee from `/events/{id}/raw` | `aggregate`; raw fields |
| Statistics, Line-ups, Incidents tabs | friendly views of these three slices for football, basketball and tennis (decision 6); for other sports the raw tree | `GET /events/{id}/slices/{key}?payload=1` (P21) | `Slice.payload` |
| Data tab | every slice of the event with its state | `GET /events/{id}/slices` (P21) | `key`, `sub`, `state`, `has_payload`, `fetched_at_utc`, `checked_at_utc`, `error.reason`, `error.http_status`, `error.count` |
| View / ⇩ (raw) | the stored payload, exactly as stored; download as a full-size `.json` file | `GET /events/{id}/raw`, `GET /events/{id}/slices/{key}/raw` (P21) | body; `ETag`, `X-Sofascore-Fetched-At` headers |
| Corrections tab | changes recorded for this event | `GET /changes?event_id=` (G8, built by P21 #123) | `Change.fields[]` (`path`, `old`, `new`), `recorded_at_utc` |
| Odds tab | shown only when an odds slice exists: markets, opening and current odds, and the history of snapshots | `GET /events/{id}/odds` (P21 empty; content P28) | Odds model (P28). As built (#133): the route returns the event's odds slices, and the tab lists them with their raw view; there is no markets view until P28 |
| Fetch again | re-read the event and its selected slices | `POST /jobs {kind: "fetch", spec: {events: [id]}}` (P13) | — As built (#133): `fetch` with `selections: [{league_id, match_ids: [id]}]`; offered only for an event with a tournament (G16). **Fetch details** for a listing-only event is the same job |
| ⋯ menu | Copy event id; Copy API link; Open on SofaScore (a normal link that opens a new tab, only when the user clicks it; it is the user's own visit, not a request of the app) | — | `slug`, `custom_id`, `id`. As built (#133): the link is `https://www.sofascore.com/<slug>/<custom_id>#id:<id>` and is offered only when slug and custom id are stored |

**States.** 404: "This match is not stored." with a button "Fetch it" (starts a fetch job by id). As
built (#133): a fetch needs the tournament (G16), so the page offers "Follow this event" instead (the editor
opens with kind event and the id). An event
known only from a listing (`quality.source = "listing"`): "Only the schedule entry is stored. Details have
not been fetched." with "Fetch details". Raw of a slice without a payload: 404 → "No payload stored for this
slice" (never an empty JSON). A slice in state `error` with an older payload: the raw view says "This is
the payload of 28 Sep; the last attempt failed (429)".

**As built (FX-14a, FX-14b).** The Status fact is the class in words ("Finished"), with SofaScore's type,
code and description under "Details"; "Settlement" is **"Final score?"** (yes / provisional, may change /
not yet); "Read from" replaces "Known from"; the tier hint ("Better covered") is hidden; "Raw event" is
**"Original SofaScore data (JSON)"**; the event id is the "Match number". The **team names link** to
Matches filtered by the team (`/events?q=<name>`). The **⋯ menu** has, besides the copy items, **"Follow
this match"** and **"Follow <team>"** for both teams (FX-14b; the editor opens with kind, number, name and
sport filled in; `frontend/src/screens/events/EventDetailScreen.vue:170-177`). **"Fetch again"** of a
match without a tournament sends `fetch {event_ids: [id]}` (`:209`). The **404 page** offers **"Fetch this
match"** (the design's "Fetch it", G16) and "Follow this match"; FX-14a had removed the follow link while
single-match follows did not download. The Odds tab still lists the stored odds slices with the raw view:
P28 added the normalized odds at `/events/{id}/odds/{key}`, which the tab does not read (open after
FX-14b, 7.3).

**Navigation.** Back to the list keeps its filters. A job toast after Fetch again; when it finishes the page
reloads the event.

### 6.7 Corrections

**Purpose.** The change log: results that SofaScore corrected after they were stored.

| Element | Shows | Route | Field |
|---|---|---|---|
| Table | one row per change | `GET /api/v1/changes?since=` (P21) | `recorded_at_utc`, `event_id` → names, `sport`, `tournament_id`, `old_status_class` → `new_status_class`, `fields` (the score fields shown as "2-1 → 2-2"), `seconds_after_start`, `status_regressed` |
| Filters | sport, tournament, date range, "status regressed only" | the same route with filters (proposed, 7.3; until then filtering is done on the page) | As built (#133): tournament and dates are filtered by the server (G8, P21 #123); sport and "status regressed only" within the page, marked "this page" (G19) |

Row → Event detail, Corrections tab. Empty: "No corrections recorded yet." As built (#133): `/changes` has
no match names, so the screen reads the event of each row of the page to show them (G19). FX-13 added
`sport`, `regressed` and `include=names` to `/changes`; the screen does not use them yet at `b6caf2f`
(`frontend/src/screens/CorrectionsScreen.vue:37-47`; G19 in 7.3). The screen is named **Score changes**
since FX-14a.

### 6.8 Jobs

**Purpose.** Every piece of work, from every face: web, CLI, scheduler.
**Who / when.** The operator; after starting something; after an alert.

```
┌ Jobs ─────────────────────────────────────────────────────────────────────── [Start a job ▾] ┐
│ State [All ▾]  Kind [All ▾]  Started by [All ▾]                                               │
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Kind      Target                   State          Progress        Started by     Started  Took │
│ Sync      Premier League 25/26     ◐ Running      ███░ 58 %       web            21:02    4 m  │
│ Refresh   all follows              ✓ Succeeded                    cli · srv-1    09:10    1 m  │
│ Sync      all follows              ◑ Partly done  12 failed       scheduler      06:00   22 m  │
│ Backup    all                      ✓ Succeeded                    web            yest.   12 s  │
│ Fetch     3 events                 ✕ Failed       blocked         web            yest.    3 s  │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
Start a job ▾ : Sync all follows · Refresh finished matches · Rebuild the index …
```

| Element | Shows / does | Route | Field |
|---|---|---|---|
| Table | | `GET /api/v1/jobs?state=&kind=&limit=&cursor=` | `id`, `kind`, `state`, `spec` (target text), `progress`, `origin.face`, `origin.host`, `created_at`, `started_at`, `finished_at`, `error.code` |
| Started by | web, cli, scheduler; the host when it is not this server's | same | `origin.face`, `origin.host` (decision D20 keeps it). As built (#107): `GET /jobs` has no origin filter, so the filter works within the page and says "this page" (G14). FX-13 added `?origin=`; the screen still filters within the page at `b6caf2f` (`frontend/src/screens/jobs/JobsScreen.vue:84`) |
| Progress | percent for a running job; failed count or error code for a finished one | same | `progress.percent`, `progress.detail.failed_count`, `result.failed_count`, `error.code` |
| Start a job | the jobs that need no form; each shows a ConfirmDialog with what it does | `POST /api/v1/jobs` | kinds `sync`, `refresh` (exist), `rebuild` (P21). As built: `sync`, `fetch` and `refresh` (#107), `rebuild` (#132); the confirmation says "sends nothing to SofaScore" for kinds that work on the data folder only |

**As built (FX-14a, FX-14b): names, not ids.** A job's target is the league's name from the stored
catalog, else from `progress.league_name`, else "League #17" (FX-14a). Kinds are named by their spec
(FX-14b): a download is "Download" ("İndirme"), a `sync` with `only: "seasons"` is "Season list", the
prune-history `clear {scope: history, older_than}` of the scheduler is "Old odds clean-up" with the target
"older than 90 days", a `clear` with `tournament_id` is "League data deletion", a restore with `dry_run:
false` is "Restore" and the dry run stays "Backup check". A team or player job reads "Team #42" unless the
follows were read in this tab: the job record carries no follow name (G25, FX-20). The recorded spec is
the service's, not the request's (`only: "seasons"` is recorded as `mode: "seasons"`, `event_ids` as `mode:
"details"` with per-tournament selections); the UI reads both forms (G26).

**States.** Empty: "No jobs yet." Polling every 10 s while open. A job of another process that died is
shown `interrupted` after the server reaps it.

**Navigation.** Row → Job detail.

### 6.9 Job detail

```
┌ Jobs › Sync · Premier League 25/26 ─────────────────────────────────────────────── [Stop] ┐
│ ◐ Running · started by web · 21:02 · 4 min                                                 │
│                                                                                             │
│ Phase 3 of 4 · Details                                                                      │
│ ███████████████████░░░░░░░░░░░░░  58 %     812 of 1,400 matches · about 4 min left          │
│ ⏸ Waiting 12 s: SofaScore asked us to slow down                                             │
├───────────────────────────────────────────────────────┬─────────────────────────────────────┤
│ Log                                     [All ▾] [⇩]   │ Job id       01J9Z…  [copy]          │
│ 21:02:01  Started                                     │ Kind         sync                    │
│ 21:02:03  Seasons: 1 list read                        │ Target       Premier League · 25/26  │
│ 21:02:40  Matches: 380 listed                         │ Data         Custom (+odds)          │
│ 21:03:10  Match 16950622 failed (429)          [→]    │ Started by   web · this server       │
│ 21:05:55  Waiting 12 s (rate limit)                   │ Cancel asked no                      │
│ …                                                     │                                      │
└───────────────────────────────────────────────────────┴─────────────────────────────────────┘
```

| Element | Shows / does | Route | Field |
|---|---|---|---|
| Header | state, origin, times | `GET /api/v1/jobs/{id}` | `state`, `origin`, `started_at`, `finished_at`, `heartbeat_at`, `cancel_requested` |
| Progress | phase, percent, counts, ETA, wait | `GET /jobs/{id}/events` (SSE `phase`, `progress`) | `phase`, `phase_index`, `phase_count`, `done`, `total`, `percent`, `eta_seconds`, `wait` |
| Log | every event, translated by type and `code`; filter by type; download | same | `seq`, `ts_ms`, `type` (`started`, `phase`, `progress`, `log`, `failed`, `breaker`, `cancel_requested`, `finished`), `data` |
| Failed item → | link to the event | same | `failed.match_id` |
| Stop | cancels; works for jobs of other processes too | `POST /api/v1/jobs/{id}/cancel` | then `cancel_requested` |
| Result (finished job) | counts and failures | `GET /jobs/{id}` | `result` (`details_done`, `details_total`, `failed_count`, `failed[]`, `refreshed`, `refresh_changed`), `error` (`code`, `message`) |
| Output (export, backup) | the file with a download button | `GET /jobs/{id}` → `result` names the export or backup (P21) | As built (#132, `frontend/src/screens/jobs/JobOutput.vue`): also what a clear removed, what a rebuild found and what a restore check found; Run again works for export, backup and rebuild. Since FX-14b also the snapshots a prune removed and a real restore's result (index rebuilt, "check after restore: no problems" or the number of problems); Run again keeps follows, the season-list choice and event ids |
| Run again | the same kind and spec, for a finished job | `POST /jobs` | `kind`, `spec` |

**States.** Stream gap (`stream.gap`): "Older log lines were removed; showing from line 1,204." and a reload
of `/jobs/{id}`. Stream unsupported (501, no `sse-starlette`): the page polls `/jobs/{id}` every 2 s. Stop on
a finished job: 404 or no change; the button is hidden for finished jobs. A running job whose heartbeat is
older than 30 s: "No sign of life for 45 s; the process may have stopped."

### 6.10 Exports

**Purpose.** Take data out in open formats, normalized or raw, at full size.

```
┌ Exports ───────────────────────────────────────────────────────────────────── [New export] ┐
│ Created        Dataset   Format   Schema       Filter                  Rows     Size        │
│ 30 Sep 22:10   events    parquet  normalized   Football · 2025-08…     4,120    1.2 MB  [⇩] │
│ 29 Sep 09:00   slices    jsonl    raw          Premier League 25/26    2,660    310 MB  [⇩] │
│ 28 Sep 18:30   legacy    csv      wide (2.x)   all                     48,210   41 MB   [⇩] │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

New export (dialog): Dataset (Events, Slices, Corrections, and after P28 Odds and season data; "Wide CSV
(2.x columns)" as its own choice); Format (JSONL, CSV, Parquet, SQLite; Parquet disabled with "needs pyarrow
on the server" when the server reports it unsupported); Schema (Normalized / Raw, for Slices); Filter (the
same filter controls as Events); a line "Raw exports are written at full size, uncompressed."; button
"Export".

**As built (#132), the dialog offers less than this.** It was built while normalized exports answered 501:
it offers the wide CSV of 2.x (`legacy-wide-csv`) and the raw payloads as JSONL (events, or every slice);
normalized datasets and the Parquet and SQLite formats are shown disabled with the reason, and Parquet
also reads `capabilities.parquet` (`frontend/src/screens/exports/ExportDialog.vue` at `b3cb819`). SC-2
(#130) was merged meanwhile: `POST /jobs {kind: "export"}` now writes the normalized datasets `events`,
`slices` and `changes` as JSONL, CSV, Parquet (with pyarrow) and SQLite, so the disabled choices can be
enabled; FX-14b (#161) wired them (below). The export filter (`ExportFilter`) is not "the same filter controls as Events":
it has sport, tournaments, seasons, events, status classes and from/to, and no participant, text,
followed or has-details filter (SC-2, #130); the dialog offers sport, followed tournaments, season ids and
event ids (the wide CSV takes tournaments and events only, as the API checks).

**As built (FX-14b, #161), the dialog offers what the API writes.** Three kinds
(`frontend/src/screens/exports/ExportDialog.vue:14-20` at `b6caf2f`): the **match table** (the wide CSV of
2.x, "Match table (CSV)" since FX-14a); **normalized data** of schema v1, the datasets matches, data
types, score changes, betting odds (one row per outcome of each odds snapshot, P28) and standings (P28), as
CSV, JSONL, Parquet or SQLite, with Parquet disabled and the reason shown when
`/status.capabilities.parquet` is false; and the **original SofaScore data** (raw, JSONL: the match itself
or every data type). The filter adds **status classes** (not for score changes) and a **date range** (for
score changes "recorded since") to sport, added leagues, season and match numbers. The **list** shows the
readable file names of FX-19 (`<league or dataset>_<UTC date>_<last 8 characters of the job id>.<ext>`,
`sofascore_scraper/services/data_jobs.py:157-174` at `b6caf2f`) and also the files that `ssc export` wrote: `source:
"file"`, a badge "by ssc export", a download and no job link
(`frontend/src/screens/exports/ExportsScreen.vue:107`). Before, a file was named by the job id
(`sofascore-export-01M….csv`), and `/exports` listed export jobs only.

| Element | Shows / does | Route | Field |
|---|---|---|---|
| Table | | `GET /api/v1/exports` (P21) | fields the UI needs: `id`, `dataset`, `format`, `schema`, `filter`, `created_at`, `rows`, `bytes`, `job_id` (G13). Built by P21 (#126) with these and `state`, `profile`, `finished_at`, `events`, `skipped`, `file`, `media_type`, `available`, and by SC-2 (#130) `schema_version` (null for raw and the wide CSV) |
| ⇩ | download | `GET /exports/{id}/download` (P21) | |
| Export | starts the job | `POST /jobs {kind: "export", spec: ExportSpec}` (P21 with SC-2; profile `legacy-wide-csv`: EX-1) | `dataset`, `format`, `schema`, `filter`, `profile` |
| Parquet available | | `/status` or `/sports` capability flag (proposed: `capabilities.parquet` in `/status`, P21) | built: `capabilities.parquet` in `/status` (G11, P21 #122) |

**States.** Empty: "No exports yet." 501 `not_supported`: 5.2. A running export appears at the top with its
progress and a link to the job.

### 6.11 Backups and restore; Maintenance

**Backups** (`/backups`).

```
┌ Backups ───────────────────────────────────────────────────────────────────── [Create backup] ┐
│ ⓘ Restore replaces the data folder. It runs only when no job and no live service is running.   │
├─────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Name                                          Scope   Created        Size    Format             │
│ backup_all_20261001_221000.zip                all     1 Oct 22:10    1.9 GB  2            [⋯] │
│ backup_all_with_env_20260920_090000.zip  🔑   all     20 Sep 09:00   1.7 GB  1 (2.x)      [⋯] │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
row ⋯ : Download · Check (dry run) · Restore…
```

- **Create backup** dialog: Scope (All / State only / Data only); "Include secrets (.env)" switch, off,
  with the warning "The archive will contain your proxy password and tokens. Keep it private."; button
  "Create". → `POST /jobs {kind: "backup", spec: {scope, include_secrets}}` (P21 with ST-24). As built (P21
  #126, #132): the spec is `{scope, include_env}`, and the API has seven scopes (`all`, `state`, `data`,
  `config`, `seasons`, `matches`, `match_details`); the dialog offers the three of the design.
- **Restore…** opens a three-step dialog:
  1. **Check**: runs a dry run (`POST /jobs {kind: "restore", spec: {name, dry_run: true}}`) and shows what
     the archive holds and what would happen ("4 tournaments, 48,210 matches, follows and job history").
  2. **Choose**: if the data folder is empty, "Restore". If not: "Replace the current data. It is moved to
     the trash folder first and kept until the restore has finished." There is no merge (`01-storage.md`
     9.2).
  3. **Confirm**: the user types `restore` to enable the button.
  → `POST /jobs {kind: "restore", spec: {name, force}}` (**not planned yet**: the job kind `restore` is not in
  P21's list; 7.3).

  **As built.** P21 (#126) enabled the kind `restore` as a check only: `dry_run` must be true, and
  `dry_run: false` answers 501 `not_supported`, because a restore replaces `state.db`, which holds the job's
  own row (`sofascore_scraper/web/api/v1/jobs.py:617-620` at `b3cb819`). So the dialog (#132) runs step 1 as a dry run,
  step 2 runs a second dry run with `force` that says what would be moved to the trash folder, and step 3
  gives the exact server command (`ssc backup restore <name> [--force] --yes`) and what must be stopped
  first, instead of a button; there is nothing to confirm by typing. The kind is named "Restore check" in
  the UI, so no toast says "Restore started". A real restore through the API was gap G2 (7.3).

  **As built (FX-13 #152 and #153, FX-14b #161): a real restore, as designed.** FX-13 made `dry_run:
  false` real: it runs under the `maintenance` lease, keeps the running job's own record (the kept rows are
  written into the staged copy, which one backup step then swaps in), and a data folder that is not empty
  answers 400 `confirmation_required` with `details.occupied` unless `force` is sent. The dialog
  (`frontend/src/screens/backups/RestoreDialog.vue:20-30`) runs Check and Choose as designed; its third
  step restores here: the user types the shown word **"RESTORE"** ("GERİ YÜKLE") instead of `restore`, the
  job runs `restore {name, force, dry_run: false}`, a 400 `confirmation_required` shows what is in the
  folder and asks again ("Replace and restore") before it sends `force`, and progress and the result come
  from the job. The server command stays under "Or run it on the server". After a restore the job history
  is the backup's plus the restore job, so the screen reads the status and the jobs again. FX-14a had
  only reworded the note ("only on the server, from the command line"), hidden "Sink positions" in a
  backup's counts and renamed "Event log entries" to "Change records".
- The archive must be in the server's backups folder. The UI has no upload (decision 15).

| Element | Route | Field |
|---|---|---|
| Table | `GET /api/v1/backups` (P21) | `name`, `scope`, `created_at`, `bytes`, `format`, `with_env` (from ST-24's `backup.json`) |
| Download | `GET /backups/{name}` (P21) | the zip itself; there is no metadata route per backup, the list has the fields (P21 #126) |
| Refusals | 409 `job_running`, `data_operation_running`, `instance_running` | 5.2: "Stop `ssc watch` first" for the live service |

**Maintenance** (`/maintenance`). Three cards; a fourth since FX-14b. The screen is named **Data cleanup**
("Veri bakımı") since FX-14a.

| Card | Does | Route |
|---|---|---|
| Rebuild the index | rebuilds `catalog.db` from the files; safe; shown with the reason when `/status` reports one | `POST /jobs {kind: "rebuild"}` (P21) |
| Old data layout | "1,204 matches are stored in the 2.x layout. They are read as they are. Moving them is optional and done on the server:" + CodeHint `ssc migrate --dry-run` (decision 16) | count from `/status` (G10: built as `summary.legacy_events`, P21 #122); as built the card shows `ssc migrate --dry-run` and `ssc migrate` |
| Clear data | scope Matches / Schedules / Season lists / All; ConfirmDialog with typed scope name; states that follows, job history, the change log and backups are kept | `POST /jobs {kind: "clear", spec: {scope, confirm: true}}` (P21). As built (#132): the API's scopes are `match_details` (= `events`), `matches` (= `schedules`), `seasons` and `all`; the dialog offers these four distinct ones, and the typed word was the API's scope name. Since FX-14a the user types the shown, localized word "SİL" / "DELETE", in either case (`frontend/src/screens/MaintenanceScreen.vue:197`) |
| Delete one league's data (FX-14b) | a fourth card, "Delete one league's data" ("Bir ligin verisini sil"): a league and optionally one season, with the same typed word | `POST /jobs {kind: "clear", spec: {scope: "all", tournament_id, season_id, confirm: true}}` (FX-19, `Store.purge`; `frontend/src/screens/MaintenanceScreen.vue:68-95`) |

### 6.12 Health (connection, live service, scheduler, storage)

**Purpose.** The state of the services, read-only, with one action: the connection check.
**Who / when.** The operator, from the health pill or an alert.

```
┌ Health ─────────────────────────────────────────────────────────────────────────────────────┐
│ ┌ Connection to SofaScore ──────────────────┐ ┌ Live service ─────────────────────────────┐ │
│ │ ● Connected                               │ │ ● Running                                 │ │
│ │ Last success     2 min ago                │ │ Started by   ssc watch · pid 4121 · srv-1 │ │
│ │ Failures in a row 0                       │ │ Sports       football, basketball, tennis │ │
│ │ Request rate     5 / s (shared)           │ │ Source       football page ·             │ │
│ │ Last error       403 challenge, 2 d ago   │ │              basketball page ·           │ │
│ │                                           │ │              tennis poll (fallback)       │ │
│ │ [Run connection check]                    │ │ Last switch  tennis page → poll, 21:40    │ │
│ │ Sends one request to SofaScore.           │ │ Heartbeat    3 s ago                      │ │
│ └───────────────────────────────────────────┘ │ The web UI shows no live scores. Live     │ │
│ ┌ Scheduler ────────────────────────────────┐ │ events go to the sinks.  [Sinks →]        │ │
│ │ Off. Turn it on in sofascore.toml          │ └───────────────────────────────────────────┘ │
│ │ ([schedule] enabled) and start            │ ┌ Storage ──────────────────────────────────┐ │
│ │ ssc serve --scheduler.                    │ │ Data folder  /srv/sofascore/data           │ │
│ └───────────────────────────────────────────┘ │ Size         2.1 GB                        │ │
│ ┌ Who holds the data folder ────────────────┐ │ Index        current                       │ │
│ │ writer       sync job · web · this server │ │ Version      3.0.0 · API 1                 │ │
│ │ live         ssc watch · srv-1            │ └───────────────────────────────────────────┘ │
│ │ sinks        ssc watch · srv-1            │                                               │
│ │ maintenance  —                            │                                               │
│ └───────────────────────────────────────────┘                                               │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Element | Shows / does | Route | Field |
|---|---|---|---|
| Connection | | `GET /api/v1/status` | `bridge.state`, `consecutive_failures`, `last_success_at`, `last_failure_at`, `failing_since`, `last_error.kind`, `last_error.at`. As built (FX-14a, FX-14b): "Not tried yet" (grey) while nothing has answered, "Last request failed" and "Last check failed" (amber), from `/status.connection` (`state`: `never_tried`, `ok`, `failed`; `last_check`; FX-19), plus "Last failed request" (the reason in words, the time) and "Last connection check". FX-14a found that `bridge.last_success_at` was set only by the browser bridge, so a download through the direct path still showed "Not tried yet"; FX-19 records every transport. The state is kept per process: requests of `ssc` commands and `ssc watch` do not count in the web server's `/status` (G28) |
| Request rate | | same | `throttle.enabled`, `requests_per_second`, `shared`, `error` |
| Run connection check | one request through the bridge, on click only | `POST /api/v1/status/check` (P21) | result: success, events count, browser ready, challenge. Built (P21 #122): `ok`, `reason`, `message`, `events_count`, `checked_at_utc`, `bridge` |
| Live service | read-only; no start or stop in the UI (R2) | `/status` live fields (P21, from `live_status(store)`) | `running`, `pid`, `host`, `source`, `sports`, `leaders` (per sport: `page`, `direct` or `poll`), `last_switch`, `heartbeat_at`, `blocked`, `last` (the last run when not running) |
| `direct` warning | when any sport's source is `direct`: a warn badge "Direct source (opt-in)" with a one-line summary of the four warnings of `02-services.md` 8.3 | same | `source`, `leaders` |
| Not running | "Not running. Live watching runs on the server: `ssc watch`." + CodeHint; the last run's end time | same | `running`, `last` |
| Scheduler | on/off and next runs | `/status` (P29) | `schedule.enabled`, `schedule.next_runs[]`. Built by P29 (#128): each run has `index`, `run`, `every`, `cron`, `options`, `next_run_at_utc`, `last_run_at_utc`, `last_job_id`, `last_result`. As built, the card (#132) shows only on/off from `capabilities.scheduler` and "The next runs appear here when the server reports them": it was written before P29 merged and is still not wired at `b6caf2f` (`frontend/src/screens/HealthScreen.vue:251-257`; 7.3, open after FX-14b). The wireframe's hint reads as if both the setting and the flag were needed; either one turns the scheduler on, and `--no-scheduler` overrides the setting (P29). Since FX-15 (#155) an `every` task counts from its last run in the job history, not from the server start, and the scheduler has a `prune-history` task (off by default) |
| Who holds the data folder | the four leases and their holders | `/status` (proposed `leases`, 7.3) | lease name, purpose, pid, host, since. Built (G3, P21 #122): `leases[]` with `name` (also `watcher:<sport>`), `purpose`, `pid`, `host`, `since_utc` |
| Storage | | `/status` (P21 data summary) | `summary.data_dir`, `summary.disk.total`, `summary.catalog_rebuild_reason`; `version`, `api_version`. As built (#132): `/status` has no data-folder path (G20), so the card shows the size, the index state with a link to Maintenance, the version and schema version and whether a token is in use, and the Diagnostics tab of Logs shows the path; the size is the sum of `disk.entries`, as on Overview (G21). FX-13 added `summary.data_dir` and `disk.v3` / `disk.changes` (with `total` counting both); the card still uses its fallbacks at `b6caf2f` (`frontend/src/app/statusStore.ts:12-19`), which give the same size |

**States.** A heartbeat older than 2 minutes while `running` is true: "No heartbeat for 3 min; the service
may hang." Connection check refused (503): the translated reason. Before P21: the cards whose fields are
missing show "Available in a later version". As built: FE-2a (#107) showed the live-service, scheduler
and storage cards in that state; FE-2b (#132) filled them from the P21 fields.

### 6.13 Sinks (read-only, with lag)

**Purpose.** Are live events and job notifications reaching their targets?
**Who / when.** The operator, from Overview or an alert.

```
┌ Sinks ──────────────────────────────────────────────────────────────────────────────────────┐
│ ⓘ Sinks are configured in sofascore.toml ([[sink]]). The web UI never adds outbound targets. │
│ Delivered by: ssc watch · pid 4121 · srv-1                                                   │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Name   Type     Target                 Events            State       Behind        Last OK   │
│ ops    webhook  hooks.example.org      live.*, job.fin…  ⚠ Retrying  1,240 · 6 min  6 min ago │
│ feed   file     out/live.ndjson        live.*            ● OK        0              2 s ago  │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ ops: last error HTTP 503 at 21:58 · next try in 40 s · 2 events dropped (older than 24 h)    │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Element | Shows | Route | Field |
|---|---|---|---|
| Delivered by | which process holds the `sinks` lease; "No process delivers right now: start `ssc watch` or `ssc serve`" when none | `GET /api/v1/sinks` (**proposed**, 7.3) | `holder.pid`, `holder.host`, `holder.command` |
| Table | one row per configured sink | same | `name`, `type`, `target` (host only for a webhook, path for a file; never the full URL or the secret), `events` (filters), `state` (`ok`, `retrying`, `disabled`), `cursor`, `head_seq`, `lag_events` (= `head_seq` − `cursor`, counting only accepted events if possible), `lag_seconds` (age of the oldest undelivered event), `last_delivered_at`, `last_error` (status code or class, at), `next_retry_at`, `dropped` (from `system.sink_dropped`) |
| Detail line | for the selected row | same | as above |

"Behind" is warn when `lag_seconds` > 60 s or the sink is retrying; danger when the sink is disabled.
Removed sinks that still have a cursor are listed under "Old cursors" (collapsed).

**States.** No sink configured: "No sinks configured. Add a `[[sink]]` to sofascore.toml to send live
events and job notifications to a file or a webhook." Route missing (before the proposed route exists): the
screen is hidden from the menu.

**As built.** FE-2a (#107) followed the last sentence: Sinks was not in the menu and `/system/sinks` showed
the placeholder. P21 (#122) then built `GET /api/v1/sinks` (G1), and FE-2b (#132) put Sinks back in the
System menu (decision 18), polled every 15 s while open. The route differs from the table above:

- `state` is `ok`, `error` or `pending` (nothing delivered yet), and each sink has `served` (a process holds
  the `sinks` lease and delivers now). There is no `disabled` state and no `next_retry_at`: nothing persists
  the dispatcher's retry time, which lives in the memory of the delivering process. The screen shows
  Delivering, Retrying (`error`), Behind (`lag_seconds` > 60), Nothing delivered yet (`pending`) and Not
  delivered now (`served` false); 4.6.
- `lag_events` counts every event of the log after the cursor, before the sink's filter; `dropped` is
  counted from the retained `system.sink_dropped` events and can undercount after the log is pruned; the
  delivery time is `last_delivered_at_utc`.
- "Delivered by" comes from the `sinks` lease in `/status.leases`, not from the sinks route.
- "Old cursors" of removed sinks are not listed.

### 6.14 Logs and diagnostics

| Element | Does | Route |
|---|---|---|
| Log table | the last lines of the server log: time, level, logger, message; level filter, text filter, Refresh | `GET /api/v1/logs?lines=&level=` (P21) |
| Diagnostics summary | versions, platform, data folder, doctor checks, as facts | `GET /api/v1/diagnostics` (P21) |
| Download diagnostics bundle | a zip with secrets removed, to attach to a bug report | `GET /api/v1/diagnostics/bundle` (P21) |

Log messages are English (rule 8 of `03-implementation-plan.md` section 2) and are shown as they are.

As built (#132): the Log tab reads `/logs` with the level filter on the server, the number of lines and a
text filter within the lines, newest first, and says so when file logging is off. The Diagnostics tab shows
the summary (version and commit, Python, platform, Docker, data folder and free space, log file and level)
and the checks of `ssc doctor` with their fix commands. The checks have codes but no translated texts, so
their labels and summaries are the server's English text, shown with a note, like log lines (4.10). Tab
and filters are in the address.

### 6.15 Token prompt and sign-out

The prompt covers the app whenever an API call answers 401 `unauthorized`.

```
┌──────────────────────────────────────────────┐
│ ◆ SofaScore Platform                          │
│                                               │
│ This server needs its access token            │
│ Ask the person who runs the server. The token │
│ is sent once; the browser keeps a session.    │
│                                               │
│ Access token  [••••••••••••••••••]  [👁]       │
│                                               │
│ [Sign in]                                     │
│                                               │
│ ⚠ Wrong token.                                │
└───────────────────────────────────────────────┘
```

| Element | Does | Route | Field |
|---|---|---|---|
| Need for a token | | `GET /api/v1/status` (open) / `GET /api/v1/auth` (P21) | `auth_required`; `required`, `authenticated`. As built: FE-2a (#107) used the legacy `/api/auth*` routes; since #132 the prompt and Sign out use `/api/v1/auth*` (P21 #122) |
| Sign in | sends the token once; the server sets an HttpOnly cookie; the app reloads its data | `POST /api/v1/auth/login` (P21; today `/api/auth/login`) | — |
| Wrong token | "Wrong token." | 401 `unauthorized` | |
| Too many attempts | "Too many wrong tokens. Try again in 27 s." with a countdown; the button waits | 401 with `details.reason = "too_many_attempts"`, `Retry-After` | `details.retry_after`. The legacy login answers 429 with `detail.retry_after`; the client reads both shapes (#107) |
| Sign out | in `⋯` and in Settings; only when a token is in use | `POST /api/v1/auth/logout` (P21) | — |

The server does not say how many attempts are left, so the prompt does not show a count.

### 6.16 Settings

**Purpose.** Change the server's settings that the API may change, and see all others with where they come
from.

```
┌ Settings ──────────────────────────────────────────────────────────────────────────────────┐
│ [Requests] [Data] [Refresh] [Display] [Logging] [Storage] [Server (read-only)] [This browser]│
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Requests to SofaScore                                                                       │
│                                                                                             │
│ Requests per second   [ 5    ]                       default                                │
│   Above 5 SofaScore may block this server. Your risk.                                       │
│ Parallel requests     [ 3    ]                       overrides.json          [Reset]        │
│ Timeout (s)           [ 10 ] 🔒 Set in /srv/sofascore/sofascore.toml. Change it there.      │
│ Use proxy             [■] 🔒 Set by environment variable SOFASCORE_CLIENT__USE_PROXY.       │
│ Proxy address         [http://user:•••@proxy:8080 ]   overrides.json   secret, masked       │
│ Base address          https://api.sofascore.com  (read-only through the API)                │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ 2 changes                                                        [Discard] [Save changes]   │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

Sections: **Requests** (`client.*`, `breaker.*`), **Data** (`fetch.only_finished`; the default data
selection `defaults.slices` and `slices.<sport>` with the SlicePicker, from P27; the odds provider, P28),
**Refresh** (`refresh.window_hours`), **Display** (`display.language`, `display.date_format`), **Logging**
(`log.level`, `log.debug`), **Storage** (`storage.data_dir`), **Server (read-only)** (bind address, allowed
hosts, token in use yes/no, live source, sinks, schedule, config file path), **This browser** (language of
this browser, theme, density, time display; stored in the browser only; Sign out).

| Element | Shows / does | Route | Field |
|---|---|---|---|
| Every row | value, source chip, lock | `GET /api/v1/settings` | `key`, `value`, `source` (`default`, `dotenv`, `overrides`, `file`, `env`, `flag`), `source_name`, `locked`, `writable`, `secret` |
| Lock reason | `file`: "Set in <config_file>. Change it there."; `env`: "Set by environment variable <source_name>."; `flag`: "Set by a command-line flag when the server started." | same | `source`, `source_name`, document `config_file` |
| Read-only (not locked, not writable) | "Can be changed only in sofascore.toml." | same | `writable` false |
| Secret | masked, with "Replace" (never shows the stored value) | same | `secret` |
| Control type and limits | number with min/max, switch, choice, text | **proposed** setting metadata (7.3); until then a table in FE-2 | As built (#107): the table is `frontend/src/screens/settings/settingsMeta.ts`. P27 (#134) built the metadata as a parallel list `metadata[]` of `GET /settings` (`type`, `minimum`, `maximum`, `choices`, `section`, `restart_needed`), so the existing rows keep their shape (G4); the screen still reads its own table at `b6caf2f` (`settingsMeta.ts:2` still says the API has none) |
| Reset | removes the API's value so the weaker layer applies; only for values from `overrides` | `PATCH /settings {values: {key: null}}` | |
| Save changes | all or nothing | `PATCH /api/v1/settings {values}` | |
| Rate warning | above the default 5 / s, the warning of today's page | — | `client.rate` |
| Data folder | changing it: ConfirmDialog "The server will use the new folder at once. Data is not moved." | same | `storage.data_dir` |

**As built (FX-14b, #161): Settings › Data.** **"Data to download"** ("İndirilecek veriler") is the global
`defaults.slices` as a checklist (it was a free-text box holding "core" until FX-14a gave it a help line):
unset shows the registry's own ticks, marked "built-in", and a fully ticked group is written by its group
name (for example `["core", "odds_featured"]`). **"Changes per sport"** is one collapsible checklist per
sport that writes `slices.<sport> {enable, disable}`. Locked values show where they are set and cannot be
ticked; both are staged and saved with "Save changes" like any other setting
(`frontend/src/screens/settings/SliceDefaults.vue`). The list shows every registered sport (21), collapsed;
shortening it is FX-20 (G32). `fetch.save_empty_rounds` is still a control, and the texts of
`fetch.only_finished` and `fetch.save_empty_rounds` still describe the old write-time meaning
(`settingsMeta.ts:46-47`, `frontend/src/locales/ui/en.ts:1567` and `:1625` at `b6caf2f`; ST-27 retired the
setting and made the other a read filter; G33, FX-20; P30 removes the setting). The review found Settings hard for a newcomer (every field
shows its config key, the API address is the first field, many keys are `sofascore.toml`-only, and the
screen does not say whether a change needs a restart); #161's recheck still rates it hard.

**States.** Save refused 400 `invalid_request` with `details.locked` or `details.read_only`: the key's row
shows the reason and nothing is saved (all or nothing). 409 `job_running` for a data-folder change: 5.2. 507:
the overrides file could not be written. Unsaved changes when leaving: "Discard 2 changes?". A key unknown to
the UI (new in the server): shown in a generic text field under "Other", so nothing is hidden.

## 7. API routes the UI needs: what exists, what is planned, what is missing

The first version of this section (at `aff0bb0`) listed P20's routes as existing, P21's, P27's, P28's,
P29's, P13's and SC-2's as planned, and thirteen gaps G1 to G13 with a proposed owner. The fifth revision
gave the state at `b3cb819` and proposed FX-13 (API) and FX-14 (frontend) for every route still missing.
This version, the sixth, gives the state at `b6caf2f` (checked in `sofascore_scraper/web/api/v1/*.py`,
`docs/api/openapi-v1.json` and `frontend/src`): P27 (#134), P28 (#140), FX-13 (#152, #153) and FX-19 (#156)
built the routes, and FX-14, split on 2026-10-06 into FX-14a (#154) and FX-14b (#161), built the screens on
them. What is still open has an owner in `03-implementation-plan.md` (FX-20, P30, or section 17 there).

### 7.1 Exists

At `b3cb819`:

| Route | Built by | Screens |
|---|---|---|
| `GET /api/v1/health`; `GET`, `PATCH /settings`; `GET /sports`, `/sports/{slug}` | P20 (#74) | shell, Settings, Follow editor, filters |
| `GET /jobs` (`state`, `kind`, cursor), `GET /jobs/{id}`, `POST /jobs/{id}/cancel`, `GET /jobs/{id}/events` (SSE) | P20 (#74) | Jobs, Job detail |
| `POST /jobs` kinds `sync`, `fetch`, `refresh` | P20 (#74) | every "Sync" and "Fetch" button |
| `POST /jobs` kinds `export`, `backup`, `clear`, `rebuild`, and `restore` as a check only (`dry_run: true`) | P21 (#126) | Exports, Backups, Maintenance, Jobs |
| normalized datasets `events`, `slices`, `changes` in JSONL, CSV, Parquet, SQLite for `POST /jobs {kind: "export"}`; `ExportRecord.schema_version` | SC-2 (#130) | Exports (wired by FX-14b) |
| `GET /api/v1/auth`, `POST /auth/login`, `POST /auth/logout` | P21 (#122) | token prompt, sign-out (used since #132) |
| `GET /status` with `version`, `api_version`, `schema_version`, `auth_required`, `bridge`, `throttle`, `active_job`, `live` (with `leaders`, `last_switch`), `summary` (data summary with `tournaments[]`, `disk`, `legacy_events`, `catalog_rebuild_reason`), `leases[]`, `capabilities` (`parquet`, `sse`, `scheduler`), `storage_error` | P20 (#74), P21 (#122) | shell, Overview, Health, Maintenance |
| `/status.schedule` (`enabled`, `next_runs[]`) | P29 (#128) | Overview, Health (not read yet; open after FX-14b) |
| `POST /status/check` | P21 (#122) | Health |
| `GET /sinks` | P21 (#122) | Sinks, Overview |
| `GET /follows` (`kind`, `origin`, `enabled`, `q`), `GET`, `PATCH`, `DELETE /follows/{id}`, `POST /follows` | P21 (#124) | Follows, Follow editor, Follow detail |
| `GET /tournaments` (`sport`, `q`, `followed`, cursor), `/tournaments/{id}`, `/tournaments/{id}/seasons`, `POST /tournaments/search`, `GET /seasons/{id}`, `/seasons/{id}/slices/{key}` | P21 (#123, #124) | Follow editor, Follow detail, Events, quick search |
| `GET /events` (`sport`, `tournament`, `season`, `participant`, `status`, `from`, `to`, `has`, `q`, `followed`, `sort`, `include=slices_summary`), `/events/{id}`, `/events/{id}/slices`, `/events/{id}/slices/{key}`, both raw routes, `/events/{id}/odds` | P21 (#123); every status stored: ST-27 (#129) | Events, Event detail, raw view |
| `GET /changes` (`since`, `event_id`, `tournament`, `from`, `to`, `order`) | P21 (#123) | Corrections, Overview, Event detail |
| `GET /exports`, `/exports/{id}/download`; `GET /backups`, `/backups/{name}` | P21 (#126) | Exports, Backups |
| `GET /logs`, `/diagnostics`, `/diagnostics/bundle` | P21 (#126) | Logs and diagnostics |
| sinks delivered while only the web server runs (`serve` hosts the dispatcher) | P25 (#125) | Sinks |

FE-2a (#107) was built on P20's routes only, before P21 (owner decision of 2026-10-02: the shell, the design
system, Jobs, Job detail, Settings, Health and the token handling may come first). FE-2b (#132, #133) was
built on P21's routes.

Added since, at `b6caf2f`:

| Route or field | Built by | Screens |
|---|---|---|
| `slices` in `FollowCreate` and `FollowPatch`; `defaults.slices` and `slices.<sport>` in `GET`/`PATCH /settings`; setting `metadata[]` (G4); `/sports/{slug}.slices[]` with `group`, `owner`, `phases`, `selected` (G9); `GET /events?status=` with every class | P27 (#134) | Follow editor, Follow detail, Settings › Data (FX-14b) |
| odds slices (`odds_featured`, `odds_all`, `odds_changes`, `winning_odds`, provider as sub) and owner slices (season, team, player, sport data) in the registry, all off by default; `GET /events/{id}/odds/{key}` (normalized odds); `GET /seasons/{id}/slices`, `/seasons/{id}/standings`; export datasets for odds and standings | P28 (#140) | Follow editor groups and Exports (FX-14b); the odds markets view is not built (open after FX-14b) |
| `sync {follows: [...]}` and `sync {only: "seasons"}`; `fetch` and `refresh` with `event_ids`; a real `restore` (`dry_run: false`, 400 `confirmation_required` with `details.occupied`, then `force`); `GET /jobs?origin=&target=`; `GET /follows?sport=`; `GET /tournaments/{id}/seasons?include=counts`; `GET /changes?sport=&regressed=&include=names`; `/status.summary.data_dir`, `last_migration`, `disk.v3` and `disk.changes`, top-level `sinks`; job log lines with codes | FX-13 (#152, #153) | 6.3, 6.4, 6.5, 6.6, 6.9, 6.11 (FX-14b); the filters of 6.2, 6.7 and 6.8 and the fields of 6.12 are not read yet |
| new follows always `api` rows; `PATCH {origin: "api"}` adopts a `legacy` row; `POST /tournaments/search` with `kinds` (`tournament`, `team`, `player`); team, player and event follows downloaded by `sync`; `clear` with `tournament_id` and `season_id`; `DELETE /follows/{id}?delete_data=true` (answers the clear job); `/status.connection` (`never_tried`, `ok`, `failed`, `last_check`) also in `/health` and `/status/check`; readable export file names and `/exports` listing the files of `ssc export` (`source: "file"`); `/status` `followed` from the follows table | FX-19 (#156) | 6.2, 6.3, 6.4, 6.10, 6.11, 6.12 (FX-14b) |

### 7.2 Planned by a remaining plan item

| Need | Plan item | Screens |
|---|---|---|
| Search as the user types, like the site: suggestions from stored data first (followed and catalog tournaments, no request), then SofaScore's search after two characters with a pause of about 350 ms, stale requests cancelled, one answer per query kept for the session, every request counted in the request budget, keyboard navigation in the list; the same in quick search (owner decision of 2026-10-06) | FX-20 (in progress) | Follow editor step 1, quick search |
| One search across kinds (tournaments, teams and players in one request) | FX-20 | Follow editor step 1 |
| A job's follow or target name (for example a `target_name` on `Job`), so that "Team #42" reads "Arsenal" without an extra request | FX-20 | Jobs, Job detail, toasts |
| Small UI gaps: the live switch offered for player follows, which `ssc watch` skips (G31); the long sport list of Settings › Data (G32) | FX-20 | Follow editor, Settings |

FE-2's order proposal (section 8) is complete: part 3 (the slice choice) and part 4 (the odds and
season-data groups of the picker) were built by FX-14b, part 5 (the classic views removed) too.

### 7.3 Gaps: G1 to G13, the ones FE-2 found, and the ones FX-14a and FX-14b found

G1 to G13 are the gaps of the first version; G14 to G24 were found while FE-2 was built (#107, #132,
#133); G25 and later by FX-14a (#154) and FX-14b (#161). "Built" means the route exists at `b6caf2f`;
"UI" says whether a screen uses it.

| # | Need | State at `b6caf2f` | Owner |
|---|---|---|---|
| G1 | Sink status with lag | **Built**: `GET /api/v1/sinks` (P21, #122), with the differences of 6.13: states `ok`, `error`, `pending` and `served`; no `disabled`, no `next_retry_at` (the retry time is not persisted); the `sinks` holder is in `/status.leases`. | — (the missing two fields are not planned, 6.13) |
| G2 | Restore as a job | **Built**: `dry_run: false` (FX-13, #152; the kept job rows go through the staged copy, #153). UI: the Backups dialog restores (FX-14b). | done |
| G3 | Lease holders | **Built**: `/status.leases[]` (P21, #122). | — |
| G4 | Setting metadata (`type`, `min`, `max`, `choices`, `section`, `restart_needed`) | **Built** by P27 (#134) as a parallel list `metadata[]` of `GET /settings`. UI: not read; `frontend/src/screens/settings/settingsMeta.ts` keeps its own table and its comment at `:2` says the API has none. | open, after 3.0.0 (`03-implementation-plan.md` section 17) |
| G5 | Tournament search not a GET | **Built**: `POST /api/v1/tournaments/search` with `{q, sport}` (P21, #124), `kinds` since FX-19. | — |
| G6 | Sort of the event list | **Built**: `sort=-start_utc` / `start_utc` (P21, #123). | — |
| G7 | Slice summary in the event list | **Built**: `include=slices_summary` (P21, #123); `selected` still uses the registry's default selection, not a follow's (P27 note; `03` section 16, P30). | — |
| G8 | Changes of one event, and filters | **Built**: `event_id`, `tournament`, `from`, `to` (P21, #123); sport, regressed and names are G19. | — |
| G9 | Slice registry fields for the picker | **Built**: `group`, `owner`, `phases`, `selected` (P27), the odds and owner slices (P28). UI: the checklists of 6.3 and 6.16 (FX-14b). | done |
| G10 | Old-layout count | **Built**: `/status.summary.legacy_events` (P21, #122). | — |
| G11 | Capabilities | **Built**: `/status.capabilities` `parquet`, `sse`, `scheduler` (P21, #122; `scheduler` true since P29, #128). | — |
| G12 | Jobs of one follow: `GET /jobs?target=` | **Built** (FX-13, FX-19). UI: the Jobs tab of a follow (FX-14b). | done |
| G13 | Export resource fields | **Built**: `ExportRecord` (P21, #126; `schema_version` SC-2, #130; `source` FX-19). | — |
| G14 | Jobs by who started them: `GET /jobs?origin=` | **Built** (FX-13). UI: Jobs still filters "Started by" within the page (`frontend/src/screens/jobs/JobsScreen.vue:84`). | open, after 3.0.0 (`03-implementation-plan.md` section 17) |
| G15 | A season-listing job kind | **Built**: `sync {league_id, only: "seasons"}` (FX-13). UI: "Get the season list from SofaScore" (6.3 step 2, FX-14b). The classic views it kept alive are gone. | done |
| G16 | Fetch one match by its id without its tournament | **Built**: `fetch {event_ids}` (FX-13). UI: "Fetch this match" on 6.6's 404, Fetch again and the bulk fetch (FX-14b). | done |
| G17 | Per-season counts of a tournament | **Built**: `/tournaments/{id}/seasons?include=counts` (FX-13). UI: the Seasons tab (FX-14b). The age of the tournament's own season list is G27. | done |
| G18 | `GET /follows?sport=` | **Built** (FX-13). UI: Leagues & follows still filters the sport within the list (`frontend/src/screens/follows/FollowsScreen.vue:66-72`). | open, after 3.0.0 (`03-implementation-plan.md` section 17) |
| G19 | `/changes` by sport and "status regressed", with the match names | **Built**: `sport`, `regressed`, `include=names` (FX-13; names as an `include`, not as fields of every change). UI: Score changes and Overview still filter within the page and read the event of each row (`frontend/src/screens/CorrectionsScreen.vue:37-47`). | open, after 3.0.0 (`03-implementation-plan.md` section 17) |
| G20 | The data-folder path in `/status` | **Built**: `summary.data_dir` (FX-13). UI: Health still shows the path only in the Diagnostics tab. | open, after 3.0.0 (`03-implementation-plan.md` section 17) |
| G21 | A disk total that counts the v3 tree | **Built**: `disk.v3`, `disk.changes`, and `total` counts both (FX-13). UI: still sums `disk.entries` (`frontend/src/app/statusStore.ts:12-19`), which gives the same size. | — (the fallback is correct) |
| G22 | Sink state in `/status` | **Built**: top-level `sinks` (FX-13; `sofascore_scraper/web/api/v1/meta.py:255-284`). UI: not read; no warning dot on Sinks in the rail and no amber pill for a lagging sink. | open, after 3.0.0 (`03-implementation-plan.md` section 17) |
| G23 | Job spec by target | **Built**: `sync {follows}` for every kind (FX-13, FX-19), `fetch` and `refresh` with `event_ids` (FX-13). UI: Download now and Fetch again (FX-14b). | done |
| G24 | Codes for the job log lines, texts for the diagnostics checks | **Built**: every line of the sync and fetch path has a code (FX-13, FX-19); UI: texts in both locales (FX-14b). The diagnostics checks still have no translated texts. | log lines done; the check texts: open, after 3.0.0 (`03` section 17) |
| G25 | A job's follow name: `sync {follows: ["team:42"]}` reads "Team #42" unless the follows were read in this tab; the job record has only `progress.league_name` while listing | **Missing** (#161). | FX-20 |
| G26 | A stable, documented job-spec shape: the recorded spec is the service's `SyncSpec` (`only: "seasons"` → `mode: "seasons"`; `event_ids` → `mode: "details"` with per-tournament selections), not the request body | **Missing** (#161); the UI reads both forms. | P30 (`03` section 16) |
| G27 | The age of a tournament's own season list (the `seasons` slice's `fetched_at`) | **Missing** (FX-13 Not done, #161); the Seasons tab shows each season's schedule age instead. | after 3.0.0 (`03` section 17) |
| G28 | The connection state across processes: `/status.connection` is per process, so requests of `ssc` commands and `ssc watch` do not count in the web server | **Missing** (FX-19 Not done, #161). | after 3.0.0 (`03` section 17) |
| G29 | One search across kinds; suggestions while typing | **Missing**: one kind per search, on the button or Enter (#161). | FX-20 |
| G30 | Restore of an uploaded archive | Not a gap: decision 15 (no upload) stands; the archive must be in the server's backups folder. | — |
| G31 | The live switch of the editor is offered for player follows, which `ssc watch` skips (`live_follow_skipped`) | **Missing** (#161). | FX-20 |
| G32 | Settings › Data lists all 21 registered sports, collapsed | **Long list** (#161). | FX-20 |
| G33 | The retired `fetch.save_empty_rounds` is still a control, and the texts of it and of `fetch.only_finished` still describe the old write-time meaning (`settingsMeta.ts:46-47`; `frontend/src/locales/ui/en.ts:1567`, `:1625`) | **Stale** (ST-27's note to FX-14, not done by FX-14b). | FX-20 (small UI gap); P30 removes the setting |

**Open after FX-14b, not gaps of the API.** `/status.schedule` (P29) is not read by Overview and Health
(6.1, 6.12; the comment at `frontend/src/screens/HealthScreen.vue:28` still says the next runs are not
reported). The normalized odds of `/events/{id}/odds/{key}` (P28) have no markets view; the Odds tab lists
the stored odds slices with the raw view (6.6). `frontend/src/style.css` still carries the classic
stylesheet's shared names; pruning it needs a visual diff (4.5). FX-14b's script did not take the phone
(390 px) screenshots of the row-menu dialogs (Remove, Restore); the 390 px layouts of the screens were
taken. Each is listed with its owner in `03-implementation-plan.md` section 16.

**The classic views are gone.** FX-14b (#161) removed `frontend/src/classic/`, `frontend/src/views/`, the
`/classic` routes and the "Classic interface" menu item, the legacy client `frontend/src/api/client.ts`, the
modules only they used and the classic locale texts (the sport names stay). Every classic function has a
new home (#161's table: leagues and their search → Leagues & follows and the editor; enable and disable →
the row menu, after "Move here" for an old-list league; downloads and "refresh seasons" → Download now,
Download this season and "Get the season list"; missing details → "Fetch missing details" and the bulk
fetch; one match → Event detail with Fetch again and "Fetch this match"; activity → Jobs, Job detail, the
job pill and the finish toasts; settings, connection test, backup, clear and stats → Settings, Health,
Backups, Data cleanup and Overview; sign out and language → `⋯` and Settings). The legacy `/api` routes
stay on the server until P30 removes them.

## 8. Notes for FE-2 (implementation)

The design is new; some of today's code is still worth carrying over. These are suggestions, not limits.
As built: FE-2a (#107) carried over `lib/theme.ts`, the language rule of `frontend/src/i18n.ts` (the
server language now read from `/api/v1/settings`), the eval-free build and the token flow, and wrote a new
v1 client in `frontend/src/api/v1/` (`client.ts`, `errors.ts`, the generated `schema.ts`); the old client
`frontend/src/api/client.ts` stayed for the classic views until FX-14b (#161) removed both. `axe-core` was
added as a dev dependency for the accessibility checks. The stack at `b6caf2f`: Vue 3.5, vue-router 5
(#143), vue-i18n 11 (#137), Tailwind CSS 4 (#136) and Vite 8.3 (#146) (`frontend/package.json`).

| Today | Worth keeping because | Change |
|---|---|---|
| `frontend/src/api/client.ts` error parsing (`parseError`, `apiError`) | handles three error shapes and the 401 hook | reduce to the v1 shape `{error: {code, message, details, request_id}}`; generate the types from `docs/api/openapi-v1.json` (for example `openapi-typescript`, a dev dependency, with a CI check that the generated file is current). As built (#107): `openapi-typescript` cannot be installed next to TypeScript 6 (its peer is `typescript@^5`), so `frontend/scripts/gen-api-types.mjs` (`npm run gen:api`) generates `frontend/src/api/v1/schema.ts`; it fails loudly on a JSON Schema form it does not know, and `frontend/tests/apiTypes.test.ts` fails when the committed file differs |
| `frontend/src/i18n.ts` (`langOf`, `resolveLang`, server language) | the language rule of R6, already tested | keep; replace the server-language source with `/api/v1/settings` `display.language` |
| `frontend/vite.config.ts` `__INTLIFY_JIT_COMPILATION__` and the `<meta name="sofascore-csp" content="no-eval">` marker (#43) | the server's Content-Security-Policy has no `'unsafe-eval'` | keep as is; `frontend/tests/csp.test.ts` keeps it so. As built: vue-i18n 10 and later compile messages without `eval` always, so #137 (vue-i18n 11) removed the define from `frontend/vite.config.ts`; the marker stays. `frontend/tests/csp.test.ts` now renders messages in a child Node process started with `--disallow-code-generation-from-strings` and scans the built `dist` for `eval` and `new Function` (`:68-82` at `b6caf2f`), and `tests/test_web_hardening.py:1041-1042` asserts vue-i18n 10 or later. The comment above `CSP_MARKER` in `sofascore_scraper/web/security.py` named the define until FX-15 (#155) |
| `TokenPrompt.vue` and `lib/auth.ts` | session cookie flow, no token in page storage | move to the v1 auth routes; add the countdown for `too_many_attempts` |
| `lib/theme.ts` | light / dark / system with storage guarded by try/catch | keep; add density |
| `lib/upstream.ts` | translated reasons of an upstream refusal | keep for the buttons that call SofaScore |
| `lib/matchDetail.ts` | renders statistics, line-ups and incidents from raw payloads | basis for the friendly views of 6.6 |
| `style.css` tokens and the Geist fonts | the palette direction (decision 10) | rename to the token list of 4.5. As built: the classic stylesheet's shared names are still in `frontend/src/style.css` after the classic views went (FX-14b; 4.5, 7.3) |
| `tests/a11y.test.ts`, `tests/i18n.test.ts` | accessibility and locale completeness checks | extend to every new screen |
| the locale files | many texts can be reused | new key tree per screen; delete keys no screen uses |

Order proposal (each part leaves a working UI; decision 22):

1. Shell, design system, token handling, Jobs, Job detail, Settings, Health with the fields that exist.
   Can start right after approval (only P20 routes).
2. After P21: Overview, Follows, Follow editor (without slice choice), Events, Event detail with raw view,
   Corrections, Exports, Backups (without restore until G2), Maintenance, Logs.
3. After P27: data selection in the follow editor and in Settings; status filters.
4. After P28: odds tab and the odds and season-data groups.
5. Remove today's views and the legacy client calls; P30 then removes the legacy routes.

As built: part 1 is FE-2a (#107); part 2 is FE-2b, in two pull requests (#132: Exports, Backups with the
restore check, Maintenance, Logs, Sinks, Overview and Health on the P21 fields; #133: Follows, the editor,
Follow detail, Events, Event detail with the raw view, Corrections, quick search). Parts 3, 4 and 5 are
FX-14b (#161), after P27, P28, FX-13 and FX-19; FX-14 was split on 2026-10-06, and its first part, FX-14a
(#154), is the newcomer pass of the first-time-user review, which this order did not foresee. The odds
markets view of part 4 is not built (7.3).

Tests: one component test per design-system part with all its states; one test per screen for loading,
empty, error and the 401 / 409 paths with a fake API; no test calls a real server or SofaScore. As built:
`frontend/tests/designSystem.test.ts`, `shell.test.ts`, `jobsScreen.test.ts`, `jobDetail.test.ts`,
`settingsScreen.test.ts`, `healthOverview.test.ts` (#107), `operationsScreens.test.ts` (#132),
`dataScreens.test.ts` and `jsonViewer.test.ts` (#133), with axe on every screen, the dialogs and the raw
panel; the fake API is `frontend/tests/v1.ts`. FX-14a added `newcomer.test.ts` (28 tests: every behaviour
of the pass, including that the palette's SofaScore search sends nothing until it is chosen and then
exactly one request, and that a finish toast is told once); FX-14b added `jobWords.test.ts`,
`dataTypes.test.ts`, `followKinds.test.ts` and `exportsRestore.test.ts` and removed the 18 test files of the
classic views. At `b6caf2f` the frontend has 20 test files with 247 tests (329 before the classic tests
went).

## 9. What the web UI deliberately does not do

- No live scores, live list, live ticker or live stream (R2). Only the live service's state.
- No start or stop of the live service; no live-source choice (config file and `ssc watch`).
- No sink configuration (decision D11) and no scheduler configuration (config file).
- No `migrate` button (decision 16), no upload of backups (decision 15; #161 lists it as the one restore
  function the UI lacks, and the decision stands).
- No user accounts, roles or per-user settings (R5).
- No images or other content loaded from SofaScore's domains; the only SofaScore link is "Open on
  SofaScore", which the user clicks.
- No analytics, no charts beyond the small coverage bars (decision 12).

## 10. Decisions taken (approved 2026-10-02)

The owner approved every choice below as chosen on 2026-10-02. Each line keeps the alternative that was
offered, so a later change can start from it. The same day the owner decided that the web UI is designed
and built from scratch on the same stack (Vue 3, TypeScript, Pinia, the same build tooling), which
replaces the earlier "large update on the existing base" (section 1), and that FE-2 is a rewrite built part
by part (decision 22), the old views removed at the end. All 22 decisions were built as chosen (FE-2a #107,
FE-2b #132 and #133); where a screen differs, the reason is the API, not a changed decision (section 11).
Since then the owner changed decisions 5 and 20 (2026-10-06), and the first-time-user review changed the
words and some defaults, which the owner asked to win over this document (FX-14a; section 11).

1. **Start page.** Chosen: Overview (health, numbers, running job, attention list). Alternative: the
   Events list as the start page.
2. **Navigation model.** Chosen: a side rail with four groups on desktop, a bottom bar with "More" on phone.
   Alternative: a top bar with tabs and drop-down menus.
3. **Follows and events.** Chosen: two screens, linked (a follow's detail page embeds its events).
   Alternative: one "Library" screen that drills down sport → tournament → season → events.
4. **Data selection (slices).** Chosen: "Use defaults" or "Choose", then a grouped checklist with the
   request cost per match; odds as their own group, off, with a note on cost and history. Alternative:
   three presets (Basic, Full, Full with odds) with the checklist behind "Advanced". As built: FX-14a
   collapsed the read-only picker to one line with "Details"; FX-14b built the choice as chosen, with the
   groups by section and no locked boxes (6.3).
5. **Team, player and event follows.** Chosen: offered in the follow editor by id; tournaments also by
   search. Alternative: tournaments only in the UI; the rest through the config file. Changed by the owner
   on 2026-10-06: team, player and single-match follows must work in 3.0.0, with search by name and their
   matches downloaded (FX-19, FX-14b); part of the live validation. Teams and players are now searched by
   name; a match is added by its number or from its page (6.3, 6.6).
6. **Event detail content.** Chosen: friendly views for statistics, line-ups and incidents of football,
   basketball and tennis, the raw tree for everything else. Alternative: the raw tree only, until
   normalized slice models exist.
7. **Raw view.** Chosen: a side panel with a collapsible JSON tree, search, copy and download.
   Alternative: open the raw JSON in a new browser tab.
8. **Density.** Chosen: comfortable by default, compact as a per-browser option on desktop. Alternative:
   compact only.
9. **Theme.** Chosen: light, dark and "follow the system" (default). Alternative: light only.
10. **Visual identity.** Chosen: keep today's direction (calm neutral background, one green accent, Geist
    fonts), refined into the tokens of 4.5. Alternative: a new palette and type.
11. **Component library.** Chosen: own components on Tailwind CSS, no UI kit. Alternative: PrimeVue in
    unstyled mode (more ready parts, one more large dependency).
12. **Charts.** Chosen: no chart library in 3.0; small inline bars for coverage. Alternative: a chart
    library (for example Apache ECharts) for coverage and job history over time.
13. **Times.** Chosen: the browser's local time, UTC on hover. Alternative: UTC everywhere.
14. **Paging.** Chosen: Previous / Next by cursor, no page count. Alternative: infinite scrolling.
15. **Restore source.** Chosen: only archives already in the server's backups folder; no upload.
    Alternative: upload a zip through the browser.
16. **Migrate.** Chosen: CLI only; the UI shows a notice with the command. Alternative: a "Migrate" job
    button on Maintenance.
17. **Live service in the UI.** Chosen: a card on Health and a part of the health pill; no start or stop.
    Alternative: the Health card only, not part of the pill.
18. **Sinks.** Chosen: their own read-only screen under System. Alternative: a section of Health.
19. **Corrections.** Chosen: their own screen, plus a tab on each event. Alternative: only the tab on the
    event and the Overview card.
20. **Quick search.** Chosen: `Ctrl K` over stored follows, tournaments and event ids. Alternative: no
    global search; filters only. As built (FX-14a): also actions, and one explicit "Search SofaScore" entry
    when nothing stored matches, which sends one request only when it is picked (3.3). Changed by the owner
    on 2026-10-06: search as the user types, like the site ("la" suggests La Liga), here and in the
    editor: FX-20 (7.2).
21. **Locked settings.** Chosen: shown in place, greyed, with the reason and the source. Alternative:
    hidden behind a "Show locked settings" switch.
22. **FE-2 delivery.** Chosen: build the new app part by part in the existing `frontend/` (section 8),
    remove the old views at the end. Alternative: build it complete on a branch and switch in one pull
    request. As built: three pull requests (FE-2a #107; FE-2b #132 and #133); the old views were under
    `/classic` until FX-14b (#161) removed them, and their addresses redirect to the new screens (3.2, 7.3).

## 11. What changed while it was built

Corrections after batches eleven to nineteen (2026-10-03, the fifth revision; the same list, by document, is
in `03-implementation-plan.md` section 11). Each item says what the document claimed and what is built:

1. **Built, not planned.** The document said "Nothing here is built yet". FE-2 built it in three pull
   requests: FE-2a (#107) the shell, the design system, the v1 client, Overview, Jobs, Job detail, Health
   and Settings on P20's routes; FE-2b (#132) the Operations and System screens and (#133) the Data
   screens on P21's routes. The old views moved under `/classic` (state line, 3.2, 8).
2. **Type generator.** Section 8 suggested `openapi-typescript`. It cannot be installed next to TypeScript 6
   (peer `typescript@^5`); a 160-line script of the project, `frontend/scripts/gen-api-types.mjs`
   (`npm run gen:api`), generates the types, and a test fails when the committed file differs (8; #107).
3. **Old addresses.** 3.2 said today's paths redirect to their nearest new screen. FE-2a sent `/download`,
   `/matches` and `/match/:id` to the classic views because their new screens did not exist yet; FE-2b
   (#133) made them lead to Follows, Events (filters carried over) and Event detail, and added `/schedule`,
   `/leagues`, `/stats` and the `/advanced/...` paths (3.2).
4. **Base size and colour tokens.** The 14 px base applies to the new app only; the classic views keep
   15 px. The classic stylesheet keeps its extra tokens, and the shared names took the values of 4.5, so
   `--muted` and three dark status backgrounds of the classic views changed slightly (4.3, 4.5; #107).
5. **Too many attempts.** 5.2 and 6.15 named only the v1 shape (401, `details.reason`). The legacy login
   answers 429 with `detail.retry_after`; the client reads both. Since #132 the prompt uses
   `/api/v1/auth*` (5.2, 6.15; #107, #132).
6. **Job log codes.** 4.10 said the log is translated by code. The UI has texts for three codes, but the
   server sends only `fetch_stopped_by_breaker` and `refresh_stopped_by_breaker`; `fetch_zero_matches` is
   logged as text without its code, and the other log lines of the sync and fetch path have no code, so
   they are shown as the server's text (4.10, 6.9; #107; G24).
7. **"Started by" filter.** 6.8 filtered jobs by origin; `GET /jobs` has no origin filter, so the filter
   works within the page (6.8; #107; G14, not in the first gap list).
8. **Sinks in the menu.** 6.13 hid Sinks while its route was missing, and 3.3's rail showed it; FE-2a hid
   it. P21 (#122) built `GET /sinks`, and #132 put Sinks back in the menu. The rail's warning dot on Sinks
   is not shown, because `/status` has no sink state and the shell does not poll `/sinks` (3.3, 6.13;
   G22).
9. **Sink states.** 4.6 and 6.13 had `ok`, `retrying`, `disabled`, "behind" and `next_retry_at`. The API
   has `ok`, `error`, `pending` and `served`, no `disabled` and no `next_retry_at`; the UI shows Delivering,
   Retrying, Behind (lag over 60 s), Nothing delivered yet and Not delivered now (4.6, 6.13; #122, #132).
10. **Restore.** 6.11 step 3 had the user type `restore` to enable the button. P21 (#126) enabled the kind
    `restore` as a dry run only, because a restore replaces `state.db`, which holds the job's own row; the
    dialog runs two checks and its last step gives the `ssc backup restore` command. The kind is called
    "Restore check" in the UI (6.11; #132; G2, FX-13).
11. **Clear and backup scopes.** 6.11 named the clear scopes Matches, Schedules, Season lists, All; the
    API's are `match_details` (= `events`), `matches` (= `schedules`), `seasons`, `all`, and the typed word
    is the API's scope name. The backup dialog offers the design's three scopes of the API's seven, and the
    spec field is `include_env` (6.11; #126, #132).
12. **Data-folder path and disk size.** 6.12 read `summary.data_dir` and 6.1 and 6.12 `summary.disk.total`.
    `/status` has no path (the Diagnostics tab shows it), and `disk.total` counts only the 2.x trees and
    reads 0 for a v3 data folder; the UI sums `summary.disk.entries` (6.1, 6.12; #132; G20, G21).
13. **Scheduler.** 6.1 and 6.12 showed the next runs. FE-2b shows on or off from `capabilities.scheduler`
    only; P29 (#128) added `schedule.enabled` and `schedule.next_runs[]` to `/status` in the same batch, and
    the screens do not read them yet. Either the setting or `ssc serve --scheduler` turns the scheduler on,
    not both (6.1, 6.12; FX-14).
14. **Export dialog.** 6.10 offered datasets, four formats and a schema choice. Built while normalized
    exports answered 501, the dialog offers the wide CSV and the raw JSONL and shows normalized data,
    Parquet and SQLite disabled with the reason. SC-2 (#130) has since made them work through
    `POST /jobs {kind: "export"}`; FX-14 enables them. The export filter is not the Events filter: it has
    sport, tournaments, seasons, events, status classes and from/to (6.10; #132, #130).
15. **Diagnostics checks.** 6.14 assumed translated texts; the checks have codes but no texts, so their
    labels are the server's English text, with a note (6.14; #132; G24).
16. **Data selection.** 6.3 step 3 offered a choice per follow. `FollowCreate` and `FollowPatch` have no
    `slices` (P27) and the registry has no groups or odds slices (P27, P28), so the picker is read-only with
    the defaults; the odds group lists the four markets unticked and disabled, and the season, team and
    player groups are not shown (6.3; #133; G9).
17. **Season list.** 6.3 step 2 had a button that starts a listing job. There is no such job kind; the
    step says the list is read at the first sync (6.3; #133; G15).
18. **Sync now and Fetch again.** 6.2, 6.5 and 6.6 used P13's spec by target (`{follows}`, `{events}`) and
    `refresh` for Fetch again. P13 (#113) kept `league_id` / `selections[]`, and `refresh` takes only a
    `league_id`: Sync now is offered for tournament follows only, and both bulk actions of Events and Fetch
    again are `fetch` jobs with `selections[].match_ids`. Events without a tournament cannot be fetched by
    id and are left out (6.2, 6.5, 6.6; #133; G16, G23).
19. **Filters within the page.** 6.2's Sport filter, 6.7's sport and "regressed" filters and 6.4's Jobs tab
    filter what one page holds, because `/follows` has no `sport`, `/changes` has neither filter and
    `/jobs` has no target (6.2, 6.4, 6.7; #133; G12, G18, G19).
20. **Per-season counts.** 6.4's Seasons tab showed matches, details, coverage and the listing's age per
    season. The API counts per tournament only; the tab lists the stored seasons with Events and Sync per
    season, and adds "Fetch missing details" for the tournament, the new home of the classic Download
    view's missing-details fetch (6.4; #133; G17).
21. **Match names in the change log.** 6.7 and 6.1 showed names; `/changes` has ids only, so Corrections
    and Overview read the event of each row (6.1, 6.7; #133; G19).
22. **Event detail.** 6.6's 404 offered "Fetch it" (a fetch by id); a fetch needs the tournament, so the
    page offers "Follow this event". The Odds tab lists the stored odds slices with their raw view (no
    markets view until P28). "Open on SofaScore" is `https://www.sofascore.com/<slug>/<custom_id>#id:<id>`,
    offered only when slug and custom id are stored (6.6; #133).
23. **Status filter.** Deselecting every status chip means any status and is written `status=any`, so the
    default does not come back (6.5; #133).
24. **JsonViewer.** 4.7 had copy path and copy value per node. A node is selected with a click or Enter,
    and its path and the two copy buttons appear above the tree, one keyboard path instead of hover
    buttons (4.7; #133).
25. **The classic views stay.** Decision 22 removes the old views at the end. Two classic functions have
    no home in `/api/v1`: reading a tournament's season list without a sync (no listing job kind) and
    fetching one match by id without its tournament (the fetch job needs `league_id`). The classic views
    stay under `/classic` until FX-13 builds G15 and G16 and FX-14 removes them (7.3, 10).
26. **Gaps.** Of G1 to G13, G1, G3, G5, G6, G7, G8, G10, G11 and G13 are built (P21, SC-2), G2 only as a
    dry run; G4, G9 and G12 are missing. FE-2 found G14 to G24. Each has an owner in 7.3: P27, P28, or the
    new items FX-13 (API) and FX-14 (frontend).

Corrections after P27 to FX-19 and the newcomer pass (2026-10-06, the sixth revision; checked at
`b6caf2f`). The first-time-user review of 2026-10-06 (at `b567409`, offline, against the fake SofaScore of
the CLI goldens) is the reason for most of them: the owner asked that the review wins over this document
where they disagree, and FX-14a (#154), FX-19 (#156) and FX-14b (#161) followed it. Each item says what the
document claimed and what is built:

27. **Words.** The design's words were internal ("Follow", "Sync", "Coverage", "Slice"); the review's
    rename table was applied in both locales (FX-14a). The list is at the end of this section; a test
    (`frontend/tests/newcomer.test.ts`, "plain words") pins the main ones (3.1, 3.3, 6.1 to 6.16).
28. **Menu groups.** 3.1 had Data, Operations and System. Score changes (Corrections), Outputs (Sinks) and
    Data cleanup (Maintenance) moved to a fifth, smaller group "Advanced"; the phone's bottom bar says
    "Leagues" (3.1; FX-14a).
29. **Top bar and Help.** 3.3 had title, health pill, job pill and `⋯`. A permanent "+ Add league" button
    was added, `⋯` has Help, and the rail's foot a Help button. The design had no help at all; the review
    rated the Help task "not possible" (3.3; FX-14a).
30. **Health pill and connection.** 3.3 had three states. A fourth, grey "Connection not tried", was added,
    and amber also covers a failed last request or check. FX-14a kept a failed check in the browser tab and
    found that only the browser bridge set `last_success_at`; FX-19 added `/status.connection` and records
    every transport, and FX-14b reads it. The state is per process (G28) (3.3, 4.6, 6.12).
31. **Quick search.** Decision 20 said it "never sends a request to SofaScore". It has actions, and when
    nothing stored matches it offers "Search SofaScore: '<text>'", which opens the editor and sends one
    request only when picked. The owner decided on 2026-10-06 that search should suggest as the user
    types, like the site: FX-20 (3.3, 10; G29).
32. **Overview.** 6.1 had "Sync all follows" as the primary action and the first run as one empty state.
    "Add league" is primary, also on the first run; "Update all" is secondary; a dismissible "Getting
    started" card sits above the content; a toast tells when a download ends, with the number of match
    details fetched in that run (a league total per job would need the API) (6.1; FX-14a).
33. **Team, player and single-match follows.** Decision 5 offered them by id. FX-14a showed them disabled
    as "coming soon" behind `MORE_FOLLOW_KINDS`, because nothing downloaded for them; after the owner's
    decision of 2026-10-06 FX-19 built their search and downloads and FX-14b turned the flag on. Teams and
    players are searched by name, one kind per search; a match is added by number or from its page (6.3,
    6.6, 10).
34. **Data selection.** 6.3 locked the boxes of `required` slices. The owner decided with P27 that every
    data type is selectable; `required` only means "counts for completeness". FX-14a collapsed the
    read-only picker to one line and removed its radios; FX-14b built the choice, with the radios back,
    groups by section (match, odds, season, team, player, sport) rather than one group per `group` value,
    and no locked box (6.3, 6.16, 10; G9).
35. **Setting metadata.** G4 proposed the metadata on each setting row; P27 built it as a parallel
    `metadata[]` list of `GET /settings`, so the rows keep their shape. The Settings screen does not read it
    yet (6.16; G4).
36. **Seasons before the first download.** 6.3 step 2's listing button exists since FX-14b, on FX-13's
    `sync {only: "seasons"}`; FX-14a had only said that seasons can be narrowed afterwards (6.3; G15).
37. **Download now.** 6.2 and 6.4 sent `sync {follows: [...]}`; until FX-14b a league's button sent
    `league_id`, which downloads every season. Since FX-14b it is `sync {follows: [id]}` for every kind,
    with the follow's own season choice (6.2; G23).
38. **Events default.** 6.5's default status filter was "Finished and Decided without play", which hid
    upcoming fixtures. The default is every status, with an "All statuses" chip; SofaScore's status text
    is no longer shown beside the badge (6.5; FX-14a).
39. **Event detail.** The Status fact is the class in words, with type, code and description under
    Details; "Settlement" is "Final score?"; the tier hint is hidden; team names link to their matches; the
    `⋯` menu follows the match or either team; Fetch again works by number alone; the 404 page offers "Fetch
    this match" (6.6; FX-14a, FX-14b; G16).
40. **Typed confirmation.** 6.11 and 4.7 had the user type the scope key or `restore`. The user types the
    shown, localized word ("SİL" / "DELETE", "GERİ YÜKLE" / "RESTORE"), in either case (4.7, 6.11).
41. **Restore.** Item 10 above described the restore check of #132. FX-13 made the restore real and FX-14b
    built the dialog as designed, with the localized word and a second question before `force`; after a
    restore the job history is the backup's plus the restore job, so the screen reads it again. An upload
    is still not offered (decision 15) (6.11; G2, G30).
42. **Deleting one league's data.** The design had no way to delete one league's data, and a league added
    in the web UI became a locked `leagues.txt` row. FX-19 keeps every new follow in the follows table and
    adds the per-league clear; FX-14b offers "Also delete its stored matches" when removing, a fourth card
    in Data cleanup (a league or one season) and "Move here" for an old-list league (6.2, 6.11).
43. **Exports.** 6.10's dialog has, since FX-14b, the normalized datasets (with odds and standings of
    P28) in four formats, status classes and a date range; the list shows readable file names (FX-19) and
    the files of `ssc export` (6.10).
44. **Follow detail.** Per-season counts (G17), the Jobs tab by `target` plus the downloads of every
    follow (G12), and a Matches tab for a team follow (6.4; FX-14b).
45. **Job names.** The target text of 6.8 is the league's name, the kinds are named by their spec (season
    list, old odds clean-up, league data deletion, restore), and "Team #42" remains for a team or player
    job whose follows were not read in the tab (6.8; FX-14a, FX-14b; G25, FX-20).
46. **Job log codes.** Item 6 above: every line of the sync and fetch path has a code since FX-13 and FX-19
    and a text in both locales since FX-14b; the review found English log lines inside the Turkish UI
    (4.10; G24).
47. **New, not in the design (FX-14a).** The Help panel with a glossary and the README link; (i)
    toggletips; finish toasts and a league page that reads itself again after a download; units on counts;
    `lang="en"` on SofaScore's English data, so that upper-case captions are right in Turkish (3.3, 4.3,
    6.1, 6.4).
48. **The classic views are gone.** Item 25 above kept them while G15 and G16 were missing. FX-13 built
    both and FX-14b (#161) removed the classic views, their client and their tests; `/classic/...`
    redirects to the new screens; decision 22 is done (1, 3.2, 7.3, 8, 10).
49. **`/health`.** A user who types `/health` gets the server's JSON check, not the screen. No route
    changed: quick search finds Health, and Help explains the address (3.2; FX-14b).
50. **Match names of the change log.** G19 asked for names on `/changes`; FX-13 built them as
    `include=names`. Score changes and Overview do not use it yet (6.7; G19).
51. **A dialog inside a table row** inherited `white-space: nowrap` and right alignment from the cell
    (since FE-2b); FX-14b fixed it in the overlay (`UiDialog`).
52. **Build and browsers.** Tailwind CSS 4 (#136) sets the browser floor at Safari 16.4, Chrome 111 and
    Firefox 128; vue-i18n 11 (#137) removed the `__INTLIFY_JIT_COMPILATION__` define, and the eval-free
    check runs the renders where `eval` is refused and scans the build; vue-router 5 (#143) (4.5, 8).
53. **Gaps.** Of G1 to G24, every route is built (P27, P28, FX-13, FX-19); G2, G9, G12, G15, G16, G17 and
    G23 are used by the screens, and G4, G14, G18, G19, G20, G22 and the diagnostics texts of G24 are not
    read yet. FX-14a and FX-14b found G25 to G33. FX-20 owns G25, G29, G31, G32 and G33; G26 goes to P30, and
    the rest are deliberately left for after 3.0.0 (`03-implementation-plan.md` section 17; 7.3).

The words, as built (FX-14a, #154; design word → Turkish → English):

| Where | Design (English) | Turkish, as built | English, as built |
|---|---|---|---|
| menu | Follows | Ligler ve takipler (bottom bar: Ligler) | Leagues & follows (bottom bar: Leagues) |
| menu | Events | Maçlar | Matches |
| menu | Corrections | Sonradan değişen skorlar | Score changes |
| menu | Sinks | Bildirim hedefleri | Outputs |
| menu | Maintenance | Veri bakımı | Data cleanup |
| menu group | (none) | Gelişmiş | Advanced |
| button, title | + Follow; Follow something; Follow (save) | Lig ekle; Lig ya da takım ekle; Ekle | Add league; Add a league or team; Add |
| actions | Sync now; Sync all; Sync this season | Şimdi indir; Tümünü güncelle; Bu sezonu indir | Download now; Update all; Download this season |
| job kind | Sync; Restore check | İndirme; Yedek kontrolü | Download; Backup check |
| editor | Sync now after saving | Ekledikten sonra hemen indirmeye başla | Start downloading right away |
| editor | Or enter the id; Last [2]; Choose… | Ya da SofaScore numarasını girin (URL'deki sayı); Son [2] sezon; Sezonları seç… | Or enter the SofaScore number (from the URL); Last [2] seasons; Choose seasons… |
| columns | Coverage; Last sync; Origin | Ayrıntısı inen maçlar; Son indirme; Nereden eklendi | Matches with details; Last download; Added from |
| data | Data selection; Slice; Defaults | İndirilecek veriler; Veri türü; Varsayılan veriler | Data to download; Data type; Default data |
| raw | Raw payload; Raw event | SofaScore yanıtı (JSON) | Original (SofaScore) data (JSON) |
| match | Settlement; Known from; Better covered; Event id | Skor kesin mi; Okunduğu yer; (hidden); Maç numarası | Final score?; Read from; (hidden); Match number |
| status | `finished / 100`, "Ended" | Bitti (the raw values under Ayrıntılar) | Finished (the raw values under Details) |
| origin | `api`; From leagues.txt; library | Buradan eklendi; Eski lig listesinden; Python'dan | Added here; From the old league list; Python |
| job target | Tournament #17 | Premier League (else Lig #17) | Premier League (else League #17) |
| export | Wide CSV (2.x columns); Raw payloads | Maç tablosu (CSV, maç başına bir satır); SofaScore yanıtları (JSON) | Match table (CSV); Original SofaScore data (JSON) |
| backups | Check (dry run); Event log entries; Sink positions | Yedeği kontrol et; Değişiklik kayıtları; (hidden) | Check backup; Change records; (hidden) |
| clear | type the scope key | Yazın "SİL" | Type "DELETE" |
| token | Access token | Erişim anahtarı | Access key |
| connection | (none) | Henüz denenmedi; Son istek başarısız; Son denetim başarısız; pill: Bağlantı denenmedi | Not tried yet; Last request failed; Last check failed; pill: Connection not tried |
| events | Any status | Tüm durumlar | All statuses |
| outputs | Target (column) | Adres | Address |
