# 05 — Web UI: screens (FE-1)

**State: approved by the owner on 2026-10-02**, with all 22 decisions of section 10 as chosen. Nothing
here is built yet; FE-2 implements it (`00-platform.md` section 10, item 12; decision P2, second half, in
`03-implementation-plan.md` section 13).

Checked against `origin/main` at `aff0bb0` (2026-10-02). At that commit P20 (#74) has built the first
routes of `/api/v1`, and P24 (#95), P31 (#101), EX-1 (#99) and FX-12 (#100) are merged. The resource
routes (follows, events, exports, backups and the rest) are P21's and do not exist yet.

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

---

## 1. What this document decides, and the rules that bind it

The web UI is designed **from scratch** for the platform of 3.0.0 (owner decision of 2026-10-02). It is not
an update of today's six views. It keeps the technology: Vue 3, TypeScript, Pinia, vue-router, vue-i18n,
Vite and Tailwind CSS. Section 8 names the pieces of today's code that are worth carrying over; they are an
implementation note, not a limit on the design.

Binding owner decisions:

| # | Rule | Where it shows in this design |
|---|---|---|
| R1 | The web UI is for people at a screen. The CLI is for servers and automation. | No screen copies a CLI-only task (live watching, sink configuration, migrate). Screens point to the command instead. |
| R2 | **No live view.** People watch live scores on SofaScore itself. | No live score list, no live ticker, no auto-updating scores. The UI shows only whether the live service (`ssc watch`) runs and how healthy it is, from `/api/v1/status` (6.12). |
| R3 | Every data type is selectable per follow. What is not selected is never fetched. Odds are a selectable data type, **off by default**. | The follow editor has a data-selection step (6.3). Odds are their own group with a note on cost and history. |
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
Unknown paths go to `/`. Today's paths (`/download`, `/matches`, `/match/:id`, `/activity`) redirect to their
nearest new screen for one release.

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
- **Health pill**: one word and a colour from `/api/v1/status`: "All systems OK" (green), "Attention"
  (amber: connection degraded, a sink lagging, live service blocked), "Blocked" (red: SofaScore refuses us).
  It opens Health.
- **Job pill**: shown while a job runs; "Sync · 42 %"; opens that Job detail. With two or more: "2 jobs".
- **Quick search** (`Ctrl K` / `⌘ K`): finds follows by name, tournaments in the catalog by name, and
  opens an event by its id. It searches stored data only and never sends a request to SofaScore.

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

The base is 14 px; the browser's font size setting scales everything (sizes are in `rem`).

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

### 4.6 Status vocabulary (one look per meaning)

A **badge** is a pill with an icon, a word and a tone. The icon makes the state readable without colour.

| Thing | Values → tone |
|---|---|
| Event status class (`Event.status.class`) | `not_started` → neutral "Scheduled"; `live` → info "In progress (when read)"; `completed` → ok "Finished"; `decided_without_play` → ok "Decided without play"; `void` → warn "Postponed / cancelled"; `unknown` → neutral "Unknown". The SofaScore text (`status.description`) is shown beside it in small text. |
| Settlement (`Event.quality.settlement`) | `open` → none; `provisional` → info "Provisional"; `final` → none. `quality.stale` → warn "Stale"; `quality.status_regressed` → warn "Regressed". |
| Slice state (`Slice.state`) | `ok` → ok "Stored"; `empty` → neutral "No data at SofaScore"; `error` → danger "Failed" (with `error.reason`); `not_requested` → neutral outline "Not selected". |
| Job state (`Job.state`) | `queued` → neutral; `running` → info with spinner; `succeeded` → ok; `partial` → warn "Partly done"; `failed` → danger; `cancelled` → neutral; `interrupted` → warn. |
| Connection (`bridge.state`) | `ok` → ok "Connected"; `degraded` → warn "Requests refused"; `blocked` → danger "Blocked". |
| Live service | running and not blocked → ok "Running"; running and `blocked` → warn "Paused by a block"; not running → neutral "Not running". |
| Sink | delivering → ok; retrying → warn "Retrying"; disabled → danger "Stopped"; lag over threshold → warn "Behind". |
| Follow origin | `config` → neutral with lock "From config file"; `api` → none; `legacy` → neutral "From leagues.txt". |

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
| `ConfirmDialog` | for destructive or costly actions. States what will happen, in numbers when known. For irreversible actions the user types a word (the scope name, or "restore") before the button enables | clear, restore with replace, remove follow with data |
| `SidePanel` | a panel from the right for secondary content (raw view, filters on phone, a job's log) | |
| `Toast` | short message, bottom right (bottom on phone), `aria-live="polite"`; errors stay until closed, others close after 6 s | a toast never carries the only copy of important information |
| `EmptyState` | icon, one sentence, one action | every list has its own text |
| `ErrorState` | what failed, in words from the error code, a Retry button, and the request id (`X-Request-Id`) in small mono text with a copy button | 5.2 |
| `Skeleton` | grey blocks in the shape of the content while it loads | shown after 300 ms, so fast answers do not flicker |
| `LockedField` | a setting control that cannot be changed, with a lock icon and the reason (6.16) | |
| `JsonViewer` | collapsible tree of a JSON value, with search, copy path, copy value, copy all, download, and a switch to plain text | raw view; renders large payloads lazily |
| `SlicePicker` | data-type selection (6.3) | follows, settings defaults |
| `SportBadge`, `SportSelect` | sport icon and name from `/sports` | 21 sports, data-driven |
| `TimeText` | a time in the browser's local zone, the UTC value on hover and for screen readers, relative ("3 min ago") where useful | decision 13 |
| `CommandPalette` | the quick search of 3.3 | |
| `CodeHint` | a CLI command in mono with a copy button | "Do this with `ssc watch`" |

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
  shown in small text under a translated general sentence.
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
| 401 | `unauthorized` | The token prompt (6.15). With `details.reason = "too_many_attempts"`: the prompt shows "Too many wrong tokens. Try again in 27 s" with a countdown from `Retry-After`. |
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
show, its states, and where it leads. "P21" next to a route means the route is planned and does not exist
yet; section 7 lists them all.

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
| Disk | data folder size | `/status` (P21) | `summary.disk.total` |
| Connection | state and last success | `/status` | `bridge.state`, `bridge.last_success_at` |
| Request rate | the limit | `/status` | `throttle.requests_per_second`, `throttle.enabled` |
| Live service | running, source, number of sports | `/status` (P21: live fields) | `live.running`, `live.source`, `live.sports`, `live.blocked` |
| Sinks | one line | `/sinks` (proposed) | see 6.13 |
| Scheduler | next run | `/status` (P29) | `schedule.next_runs` |
| Running now | the active job with progress; Open, Stop | `/status`; `/jobs/{id}/events`; `POST /jobs/{id}/cancel` | `active_job` (Job), `progress` |
| Recent jobs | last 5 jobs | `GET /jobs?limit=5` | `kind`, `state`, `origin.face`, `started_at`, `finished_at` |
| Recent corrections | last 5 changes | `GET /changes` (P21) | `event_id`, `fields`, `recorded_at_utc` |

**States.** Loading: skeleton tiles. First run (no follows): the whole page is one empty state "Follow
your first league" with the button to the Follow editor and one line on what the platform does. `/status`
fails: the error state in the Services card; the tiles that depend on it show "—". Routes of P21 missing
(FE-2 before P21): the tiles that need them are not shown.

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
| Last sync | end of the newest sync job that included this follow | `GET /jobs?kind=sync` (target matching: P13) | `finished_at` |
| Sync now | sync of this follow | `POST /jobs {kind: "sync", spec: {follows: [...]}}` (spec of P13) | — |
| Disable / enable | | `PATCH /follows/{id}` (P21) | `enabled` |
| Remove | ConfirmDialog: "Stop following LaLiga? Stored matches stay; nothing more is fetched." | `DELETE /follows/{id}` (P21) | — |
| + Follow | opens the Follow editor | — | — |

**States.** Empty: "You follow nothing yet. Follow a league to start collecting." with "+ Follow". Origin
`config`: lock icon; Edit, Disable, Remove disabled with the tooltip "Set in sofascore.toml; change it
there". 409 `follow_managed` if it still happens. Loading, error: 4.8.

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

Step 2: seasons. `( ● Current season ) ( ○ Last [2] seasons ) ( ○ All seasons ) ( ○ Choose… )`. "Choose"
lists the seasons that are stored (`GET /tournaments/{id}/seasons`, P21) with a button "Get the season list
from SofaScore", which starts a listing job.

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

Step 4: review. Name (editable), kind and id, sport, seasons, data ("Defaults" or the chosen list), "Include
in live watching" switch with the hint "Used by `ssc watch` on the server; the web UI shows no live
scores", "Sync now after saving" checkbox (on). Button "Follow".

| Element | Shows / does | Route | Field |
|---|---|---|---|
| Kind | tournament, team, player, event | — | `FollowSpec.kind` |
| Sport | the registry's sports | `GET /api/v1/sports` | `slug`, `name`, `i18n_key` |
| Search at SofaScore | tournament search; tournaments only | `/api/v1/tournaments/search?q=&sport=` (P21; must be POST, 7.3) | hits: `id`, `name`, `category`, `sport` |
| Id field | for team, player and event, and for a known tournament id | — | `entity_id` |
| Seasons | | `GET /tournaments/{id}/seasons` (P21) | `FollowSpec.seasons`: `"current"`, `"last:N"`, `"all"`, ids |
| Defaults / Choose | null selection or a custom one | — | `FollowSpec.slices`: null, or `{"include": [...]}` |
| Slice list | grouped by `group`, with name, phase hint, owner hint, default | `GET /sports/{slug}` (`slices[]`; fields `group`, `owner`, `phases`, `keep_history` from P27/P28, 7.3) | `key`, `default_enabled`, `required` |
| "always" | slices with `required` true cannot be unticked | same | `required` |
| Defaults text | what "defaults" means for this sport | `GET /settings` (P27: `defaults.slices`, `slices.<sport>`) | value |
| Requests per match | the number of selected event slices, plus 1 for the match itself | computed | — |
| Odds provider | read-only here, link to Settings | `GET /settings` | `client.odds_provider` (P28) |
| Live switch | | — | `FollowSpec.live` |
| Follow (save) | | `POST /api/v1/follows` (P21; `slices` with P27) | — |
| Save (change) | | `PATCH /api/v1/follows/{id}` (P21) | — |
| Sync now after saving | | `POST /jobs {kind: "sync"}` (P13 spec) | — |

**States.** Search: a spinner on the button; 503 `blocked` / `rate_limited`: "SofaScore refused the search"
with a link to Health; no hits: "No tournament found. Check the sport, or enter the id." 409
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
| Header facts | | `GET /follows/{id}` (P21); `GET /tournaments/{id}` (P21) | follow fields; `Tournament.name`, `category` |
| Seasons tab | per season: matches, details, coverage, age of the season's listing | `GET /tournaments/{id}/seasons` (P21); `/status` summary by tournament (P21) | `Season.id`, `name`, `year`; counts |
| Events tab | the Events table (6.5) with `tournament=` fixed | `GET /events?tournament=` (P21) | 6.5 |
| Data selection tab | the SlicePicker, read-only, with "Edit" | as 6.3 | |
| Jobs tab | the jobs that included this follow | `GET /jobs` (filter by target: proposed, 7.3; until then: kind `sync`, newest first) | 6.8 |

**States.** Not found: 404 page. Config follow: Edit is disabled with the reason.

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
| Status chips | multi-select of the six classes; default: Finished and Decided without play, which matches today's "finished only" (`fetch.only_finished`) | same, `status=` | `Event.status.class` |
| Start | | same | `start_utc` (TimeText) |
| Sport | | same | `sport` |
| Tournament | name by id, from a cached tournament list | `GET /tournaments` (P21) | `tournament_id` → `Tournament.name` |
| Rd (optional) | round | same | `round.name` / `round.number` |
| Home, Away | | same | `participants.home.name`, `participants.away.name` |
| Score | the headline score; tennis shows sets | same | `score` by family (`home`, `away`; `periods`; `sets`) |
| Status | badge of 4.6 and quality flags | same | `status.class`, `status.description`, `quality.settlement`, `quality.stale` |
| Data | selected slices stored / selected; ⚠ when one failed | `GET /events?…&include=slices_summary` (proposed, 7.3) or the Event detail | `Slice.state` counts |
| Optional columns | event id, season, category, custom id, observed at, change time | same | `id`, `season_id`, `category_id`, `custom_id`, `quality.observed_at_utc`, `quality.change_ts` |
| Fetch missing data | fetch the missing selected slices of the selected events | `POST /jobs {kind: "fetch", spec: {events: [...]}}` (P13 spec; today `selections[].match_ids`) | — |
| Fetch again | re-read the selected events | `POST /jobs {kind: "refresh", spec: {events: [...]}}` (P13) | — |
| Sort | start time (default newest first) | `sort=` (proposed, 7.3) | |

**States.** Empty with filters: "No stored match matches these filters." and "Clear filters". Empty without
data: "No matches stored yet. Follow a league and sync it." 409 `job_running` on Fetch: 5.2. A filter that the
server does not support yet (before P27): the control is hidden.

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
| Corrections tab | changes recorded for this event | `GET /changes?event_id=` (filter proposed, 7.3) | `Change.fields[]` (`path`, `old`, `new`), `recorded_at_utc` |
| Odds tab | shown only when an odds slice exists: markets, opening and current odds, and the history of snapshots | `GET /events/{id}/odds` (P21 empty; content P28) | Odds model (P28) |
| Fetch again | re-read the event and its selected slices | `POST /jobs {kind: "fetch", spec: {events: [id]}}` (P13) | — |
| ⋯ menu | Copy event id; Copy API link; Open on SofaScore (a normal link that opens a new tab, only when the user clicks it; it is the user's own visit, not a request of the app) | — | `slug`, `custom_id`, `id` |

**States.** 404: "This match is not stored." with a button "Fetch it" (starts a fetch job by id). An event
known only from a listing (`quality.source = "listing"`): "Only the schedule entry is stored. Details have
not been fetched." with "Fetch details". Raw of a slice without a payload: 404 → "No payload stored for this
slice" (never an empty JSON). A slice in state `error` with an older payload: the raw view says "This is
the payload of 28 Sep; the last attempt failed (429)".

**Navigation.** Back to the list keeps its filters. A job toast after Fetch again; when it finishes the page
reloads the event.

### 6.7 Corrections

**Purpose.** The change log: results that SofaScore corrected after they were stored.

| Element | Shows | Route | Field |
|---|---|---|---|
| Table | one row per change | `GET /api/v1/changes?since=` (P21) | `recorded_at_utc`, `event_id` → names, `sport`, `tournament_id`, `old_status_class` → `new_status_class`, `fields` (the score fields shown as "2-1 → 2-2"), `seconds_after_start`, `status_regressed` |
| Filters | sport, tournament, date range, "status regressed only" | the same route with filters (proposed, 7.3; until then filtering is done on the page) | |

Row → Event detail, Corrections tab. Empty: "No corrections recorded yet."

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
| Started by | web, cli, scheduler; the host when it is not this server's | same | `origin.face`, `origin.host` (decision D20 keeps it) |
| Progress | percent for a running job; failed count or error code for a finished one | same | `progress.percent`, `progress.detail.failed_count`, `result.failed_count`, `error.code` |
| Start a job | the jobs that need no form; each shows a ConfirmDialog with what it does | `POST /api/v1/jobs` | kinds `sync`, `refresh` (exist), `rebuild` (P21) |

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
| Output (export, backup) | the file with a download button | `GET /jobs/{id}` → `result` names the export or backup (P21) | |
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

| Element | Shows / does | Route | Field |
|---|---|---|---|
| Table | | `GET /api/v1/exports` (P21) | fields the UI needs: `id`, `dataset`, `format`, `schema`, `filter`, `created_at`, `rows`, `bytes`, `job_id` (to be fixed by P21 with SC-2) |
| ⇩ | download | `GET /exports/{id}/download` (P21) | |
| Export | starts the job | `POST /jobs {kind: "export", spec: ExportSpec}` (P21 with SC-2; profile `legacy-wide-csv`: EX-1) | `dataset`, `format`, `schema`, `filter`, `profile` |
| Parquet available | | `/status` or `/sports` capability flag (proposed: `capabilities.parquet` in `/status`, P21) | |

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
  "Create". → `POST /jobs {kind: "backup", spec: {scope, include_secrets}}` (P21 with ST-24).
- **Restore…** opens a three-step dialog:
  1. **Check**: runs a dry run (`POST /jobs {kind: "restore", spec: {name, dry_run: true}}`) and shows what
     the archive holds and what would happen ("4 tournaments, 48,210 matches, follows and job history").
  2. **Choose**: if the data folder is empty, "Restore". If not: "Replace the current data. It is moved to
     the trash folder first and kept until the restore has finished." There is no merge (`01-storage.md`
     9.2).
  3. **Confirm**: the user types `restore` to enable the button.
  → `POST /jobs {kind: "restore", spec: {name, force}}` (**not planned yet**: the job kind `restore` is not in
  P21's list; 7.3).
- The archive must be in the server's backups folder. The UI has no upload (decision 15).

| Element | Route | Field |
|---|---|---|
| Table | `GET /api/v1/backups` (P21) | `name`, `scope`, `created_at`, `bytes`, `format`, `with_env` (from ST-24's `backup.json`) |
| Download | `GET /backups/{name}` (P21) | |
| Refusals | 409 `job_running`, `data_operation_running`, `instance_running` | 5.2: "Stop `ssc watch` first" for the live service |

**Maintenance** (`/maintenance`). Three cards.

| Card | Does | Route |
|---|---|---|
| Rebuild the index | rebuilds `catalog.db` from the files; safe; shown with the reason when `/status` reports one | `POST /jobs {kind: "rebuild"}` (P21) |
| Old data layout | "1,204 matches are stored in the 2.x layout. They are read as they are. Moving them is optional and done on the server:" + CodeHint `ssc migrate --dry-run` (decision 16) | count from `/status` (proposed, 7.3) |
| Clear data | scope Matches / Schedules / Season lists / All; ConfirmDialog with typed scope name; states that follows, job history, the change log and backups are kept | `POST /jobs {kind: "clear", spec: {scope, confirm: true}}` (P21) |

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
| Connection | | `GET /api/v1/status` | `bridge.state`, `consecutive_failures`, `last_success_at`, `last_failure_at`, `failing_since`, `last_error.kind`, `last_error.at` |
| Request rate | | same | `throttle.enabled`, `requests_per_second`, `shared`, `error` |
| Run connection check | one request through the bridge, on click only | `POST /api/v1/status/check` (P21) | result: success, events count, browser ready, challenge |
| Live service | read-only; no start or stop in the UI (R2) | `/status` live fields (P21, from `live_status(store)`) | `running`, `pid`, `host`, `source`, `sports`, `leaders` (per sport: `page`, `direct` or `poll`), `last_switch`, `heartbeat_at`, `blocked`, `last` (the last run when not running) |
| `direct` warning | when any sport's source is `direct`: a warn badge "Direct source (opt-in)" with a one-line summary of the four warnings of `02-services.md` 8.3 | same | `source`, `leaders` |
| Not running | "Not running. Live watching runs on the server: `ssc watch`." + CodeHint; the last run's end time | same | `running`, `last` |
| Scheduler | on/off and next runs | `/status` (P29) | `schedule.enabled`, `schedule.next_runs[]` |
| Who holds the data folder | the four leases and their holders | `/status` (proposed `leases`, 7.3) | lease name, purpose, pid, host, since |
| Storage | | `/status` (P21 data summary) | `summary.data_dir`, `summary.disk.total`, `summary.catalog_rebuild_reason`; `version`, `api_version` |

**States.** A heartbeat older than 2 minutes while `running` is true: "No heartbeat for 3 min; the service
may hang." Connection check refused (503): the translated reason. Before P21: the cards whose fields are
missing show "Available in a later version".

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

### 6.14 Logs and diagnostics

| Element | Does | Route |
|---|---|---|
| Log table | the last lines of the server log: time, level, logger, message; level filter, text filter, Refresh | `GET /api/v1/logs?lines=&level=` (P21) |
| Diagnostics summary | versions, platform, data folder, doctor checks, as facts | `GET /api/v1/diagnostics` (P21) |
| Download diagnostics bundle | a zip with secrets removed, to attach to a bug report | `GET /api/v1/diagnostics/bundle` (P21) |

Log messages are English (rule 8 of `03-implementation-plan.md` section 2) and are shown as they are.

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
| Need for a token | | `GET /api/v1/status` (open) / `GET /api/v1/auth` (P21) | `auth_required`; `required`, `authenticated` |
| Sign in | sends the token once; the server sets an HttpOnly cookie; the app reloads its data | `POST /api/v1/auth/login` (P21; today `/api/auth/login`) | — |
| Wrong token | "Wrong token." | 401 `unauthorized` | |
| Too many attempts | "Too many wrong tokens. Try again in 27 s." with a countdown; the button waits | 401 with `details.reason = "too_many_attempts"`, `Retry-After` | `details.retry_after` |
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
| Control type and limits | number with min/max, switch, choice, text | **proposed** setting metadata (7.3); until then a table in FE-2 | |
| Reset | removes the API's value so the weaker layer applies; only for values from `overrides` | `PATCH /settings {values: {key: null}}` | |
| Save changes | all or nothing | `PATCH /api/v1/settings {values}` | |
| Rate warning | above the default 5 / s, the warning of today's page | — | `client.rate` |
| Data folder | changing it: ConfirmDialog "The server will use the new folder at once. Data is not moved." | same | `storage.data_dir` |

**States.** Save refused 400 `invalid_request` with `details.locked` or `details.read_only`: the key's row
shows the reason and nothing is saved (all or nothing). 409 `job_running` for a data-folder change: 5.2. 507:
the overrides file could not be written. Unsaved changes when leaving: "Discard 2 changes?". A key unknown to
the UI (new in the server): shown in a generic text field under "Other", so nothing is hidden.

## 7. API routes the UI needs: what exists, what is planned, what is missing

### 7.1 Exists today (P20, #74)

| Route | Screens |
|---|---|
| `GET /api/v1/health` | shell (server reachable) |
| `GET /api/v1/status` (`version`, `api_version`, `auth_required`, `bridge`, `throttle`, `active_job`) | shell, Overview, Health, token prompt |
| `GET /api/v1/sports`, `/sports/{slug}` | Follow editor, filters |
| `GET /api/v1/jobs`, `GET /jobs/{id}`, `POST /jobs` (`sync`, `fetch`, `refresh`), `POST /jobs/{id}/cancel`, `GET /jobs/{id}/events` (SSE) | Jobs, Job detail, every "start" button |
| `GET`, `PATCH /api/v1/settings` | Settings |

FE-2 can build the shell, the design system, Jobs, Job detail, Settings and the token handling (on the
legacy auth routes until P21) before P21 is merged.

### 7.2 Planned by a plan item

| Route or field | Plan item | Screens |
|---|---|---|
| `/api/v1/auth`, `/auth/login`, `/auth/logout` | P21 | token prompt, sign-out |
| `/status`: live-service fields (`live_status(store)`, which has `leaders` and `last_switch` at `aff0bb0`) | P21 | Overview, Health |
| `/status`: data summary and coverage (`DataSummary`: counts, by tournament, disk, `catalog_rebuild_reason`) | P21 | Overview, Follows, Follow detail, Health |
| `POST /status/check` (connection check) | P21 | Health |
| `/tournaments`, `/tournaments/{id}`, `/tournaments/{id}/seasons`, `/tournaments/search` | P21 | Follow editor, Follow detail, Events |
| `/seasons/{id}` | P21 | Follow detail |
| `/events` with filters, `/events/{id}`, `/events/{id}/slices`, `/events/{id}/slices/{key}`, raw routes | P21 | Events, Event detail, raw view |
| `/events?status=` with every status stored | ST-27, P27 | Events |
| `/events/{id}/odds` | P21 (empty), P28 (content, history) | Event detail, Odds tab |
| `/seasons/{id}/slices/{key}` (standings and other season data) | P21 (route), P28 (content) | Event detail, Follow detail |
| `/changes?since=` | P21 | Corrections, Overview, Event detail |
| `/follows` (GET, POST, PATCH, DELETE) | P21 | Follows, Follow editor, Follow detail |
| Follow `slices` and the default selection in settings | P27 | Follow editor, Settings |
| `POST /jobs` kinds `export`, `backup`, `clear`, `rebuild` | P21 (with EX-1, ST-24, SC-2) | Exports, Backups, Maintenance, Jobs |
| Job spec by target (`follows`, `events`, phases) | P13 | every "Sync", "Fetch" button |
| `/exports`, `/exports/{id}/download` | P21, SC-2 | Exports |
| `/backups`, `/backups/{name}` | P21, ST-24 (format 2) | Backups |
| `/logs`, `/diagnostics`, `/diagnostics/bundle` | P21 | Logs and diagnostics |
| `/status`: scheduler next runs | P29 | Overview, Health |
| Sinks served while only the web server runs | P25 (`serve` hosts the dispatcher) | Sinks |

### 7.3 Needed and not in any brief yet

These are the gaps FE-2 needs closed. Each has a proposed owner; the docs pull request after this approval
should fold them into the briefs.

| # | Need | Proposed shape | Proposed owner | Without it |
|---|---|---|---|---|
| G1 | Sink status with lag | `GET /api/v1/sinks`: per configured sink `name`, `type`, `target` (masked by `redact.mask_webhook_url`), `events`, `state`, `cursor`, `head_seq`, `lag_events`, `lag_seconds`, `last_delivered_at`, `last_error`, `next_retry_at`, `dropped`; plus the `sinks` lease holder. Needs ST-24's cursor listing. | P21 (it depends on ST-24) | The Sinks screen and the sink line of Overview are hidden. |
| G2 | Restore as a job | `POST /jobs {kind: "restore", spec: {name, force, dry_run}}`. `JobKind` already has `restore`; P21's list names only export, backup, clear and rebuild. | P21 (with ST-24's `BackupService.restore`) | No restore in the web UI (CLI only). |
| G3 | Lease holders | `/status.leases[]`: name, purpose, pid, host, since | P21 | The "Who holds the data folder" card is hidden; 409 messages still name the holder from `details`. |
| G4 | Setting metadata | per setting: `type`, `min`, `max`, `choices`, `section`, `restart_needed` | P27 (owns `src/web/api/v1/settings.py`) | FE-2 keeps a local table of the writable keys (21 today). |
| G5 | Tournament search must not be a GET | `POST /api/v1/tournaments/search`. `02-services.md` section 6 lists it as GET, but it calls SofaScore, and #43's rule (a GET never calls SofaScore; the legacy search became POST) applies. | P21 | — (design mismatch to fix in `02-services.md`) |
| G6 | Sort of the event list | `GET /events?sort=start_utc` / `-start_utc` | P21 | Fixed order, newest first. |
| G7 | Slice summary in the event list | `GET /events?include=slices_summary`: per event `{selected, ok, empty, error}` | P21 | The Data column is shown only on the detail page. |
| G8 | Changes of one event, and filters | `GET /changes?event_id=&tournament=&from=&to=` | P21 | The Corrections tab filters the loaded page only. |
| G9 | Slice registry fields for the picker | `/sports/{slug}.slices[]`: `group`, `owner`, `phases`, `keep_history`, `max_age` | P27 (registry fields), P28 (odds and owner slices) | The picker shows one flat list. |
| G10 | Old-layout count | `/status.summary.legacy_events` | P21 (field), ST-23 (meaning) | The Maintenance card shows only the CLI hint. |
| G11 | Capabilities | `/status.capabilities`: `parquet`, `sse`, `scheduler` | P21 | The UI learns by a 501 after the click. |
| G12 | Jobs of one follow | `GET /jobs?target=follow:<kind>:<id>` | P13 (spec) and P21 | The Jobs tab of a follow lists sync jobs newest first, unfiltered. |
| G13 | Export resource fields | the field list of 6.10 | P21 with SC-2 | — (must be fixed when the route is built) |

## 8. Notes for FE-2 (implementation)

The design is new; some of today's code is still worth carrying over. These are suggestions, not limits.

| Today | Worth keeping because | Change |
|---|---|---|
| `frontend/src/api/client.ts` error parsing (`parseError`, `apiError`) | handles three error shapes and the 401 hook | reduce to the v1 shape `{error: {code, message, details, request_id}}`; generate the types from `docs/api/openapi-v1.json` (for example `openapi-typescript`, a dev dependency, with a CI check that the generated file is current) |
| `frontend/src/i18n.ts` (`langOf`, `resolveLang`, server language) | the language rule of R6, already tested | keep; replace the server-language source with `/api/v1/settings` `display.language` |
| `frontend/vite.config.ts` `__INTLIFY_JIT_COMPILATION__` and the `<meta name="sofascore-csp" content="no-eval">` marker (#43) | the server's Content-Security-Policy has no `'unsafe-eval'` | keep as is; `frontend/tests/csp.test.ts` keeps it so |
| `TokenPrompt.vue` and `lib/auth.ts` | session cookie flow, no token in page storage | move to the v1 auth routes; add the countdown for `too_many_attempts` |
| `lib/theme.ts` | light / dark / system with storage guarded by try/catch | keep; add density |
| `lib/upstream.ts` | translated reasons of an upstream refusal | keep for the buttons that call SofaScore |
| `lib/matchDetail.ts` | renders statistics, line-ups and incidents from raw payloads | basis for the friendly views of 6.6 |
| `style.css` tokens and the Geist fonts | the palette direction (decision 10) | rename to the token list of 4.5 |
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

Tests: one component test per design-system part with all its states; one test per screen for loading,
empty, error and the 401 / 409 paths with a fake API; no test calls a real server or SofaScore.

## 9. What the web UI deliberately does not do

- No live scores, live list, live ticker or live stream (R2). Only the live service's state.
- No start or stop of the live service; no live-source choice (config file and `ssc watch`).
- No sink configuration (decision D11) and no scheduler configuration (config file).
- No `migrate` button (decision 16), no upload of backups (decision 15).
- No user accounts, roles or per-user settings (R5).
- No images or other content loaded from SofaScore's domains; the only SofaScore link is "Open on
  SofaScore", which the user clicks.
- No analytics, no charts beyond the small coverage bars (decision 12).

## 10. Decisions taken (approved 2026-10-02)

The owner approved every choice below as chosen on 2026-10-02. Each line keeps the alternative that was
offered, so a later change can start from it.

1. **Start page.** Chosen: Overview (health, numbers, running job, attention list). Alternative: the
   Events list as the start page.
2. **Navigation model.** Chosen: a side rail with four groups on desktop, a bottom bar with "More" on phone.
   Alternative: a top bar with tabs and drop-down menus.
3. **Follows and events.** Chosen: two screens, linked (a follow's detail page embeds its events).
   Alternative: one "Library" screen that drills down sport → tournament → season → events.
4. **Data selection (slices).** Chosen: "Use defaults" or "Choose", then a grouped checklist with the
   request cost per match; odds as their own group, off, with a note on cost and history. Alternative:
   three presets (Basic, Full, Full with odds) with the checklist behind "Advanced".
5. **Team, player and event follows.** Chosen: offered in the follow editor by id; tournaments also by
   search. Alternative: tournaments only in the UI; the rest through the config file.
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
    global search; filters only.
21. **Locked settings.** Chosen: shown in place, greyed, with the reason and the source. Alternative:
    hidden behind a "Show locked settings" switch.
22. **FE-2 delivery.** Chosen: build the new app part by part in the existing `frontend/` (section 8),
    remove the old views at the end. Alternative: build it complete on a branch and switch in one pull
    request.
