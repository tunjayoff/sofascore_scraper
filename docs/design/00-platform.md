# 00 — Platform design (3.0.0)

This is an English rendering of the owner's design draft of 2026-10-01 (`platform-tasarimi-v0`, written in
Turkish). It turns the decisions taken that day into one architecture and contract outline. It describes the
target structure, not code. Sections 1 to 12 follow the draft section by section. The only additions are the
three paragraphs marked "Note", which give context from the repository or from the task brief and decide
nothing. Section 13 lists where the detailed designs (`01-storage.md`, `02-services.md`) refine the draft or propose
something different; those points are proposals until the owner confirms them.

## 1. Decisions (Tuncay, 2026-10-01)

| Topic | Decision |
|---|---|
| Product position | A data foundation: products are built on it, it runs on servers as automation, applications and agents use it |
| Sports | 21 sports (the current 3 + 13 that need score mapping + 5 with their own logic). Motor sports, cycling, bandy, water polo and beach volleyball are out of scope |
| Data types | Everything SofaScore shows without login, including betting odds. Every type is selectable; what is not selected is not fetched |
| History / live | History: by requests. Live: the push channel (by listening to the page), with polling as the fallback |
| Interfaces | The web UI is for people (a large update on the current base). The CLI is for servers and automation only; the menu-driven terminal UI is removed |
| Request rate | Default 5 requests per second; the user may take the risk and remove the limit |
| Storage | Raw JSON files stay + a rebuildable SQLite catalog |
| Data format | A fixed, versioned common schema + raw data for those who want it |
| Match status | Every status is stored (fixture, live, finished, cancelled); filtering happens when reading |
| Language | Default English; Turkish when the system language is Turkish |
| Platform | Linux and Docker are official; Windows and macOS best-effort |
| Network access | No account system. Local-only by default; exposing it is the responsibility of whoever installs it (see section 9) |
| Raw payloads | Stored compressed; the application and the CLI export them at full size (plain JSON) on request |
| Old data layout | No automatic migration. New writes use the new layout; old data is read in place; whoever wants to moves it with the `migrate` command (manager decision) |
| Scheduler | In-app automatic updating is optional, off by default |
| Version | These changes are 3.0.0 (manager decision): storage layout, API and CLI change incompatibly |
| Merging | The manager session reviews and merges; the release tag is Tuncay's |

Note. The sports, by class, as measured in `docs/all-sports/README.md`:

- supported today: football, basketball, tennis;
- class A, score mapping only (13): period-based — American football, Aussie rules, ice hockey, handball,
  rugby, futsal, mini football, floorball; set-based — volleyball, badminton, table tennis, padel, snooker;
- class B, own status or score logic (5): baseball, cricket, e-sports, darts, MMA;
- out of scope: motor sports and cycling (they use a separate `stage` model), and bandy, water polo and beach
  volleyball (no finished sample was found to classify them).

## 2. Layers

```
            Web UI (Vue)             Other applications / agents
                   │                          │
   ┌───────────────┴──────────┬───────────────┴───────────┐
   │        HTTP API v1       │    CLI (automation)       │   Python library
   └───────────────┬──────────┴───────────────┬───────────┘          │
                   └──────────── Services ────┴──────────────────────┘
          (download, refresh, watch, export, backup, status, doctor)
                   │                 │                  │
               Store             Client              Live
        catalog + raw files   bridge + budget     push listener
                              + health            + polling fallback
                   └──────── Sport registry (sports, data slices) ────────┘
```

Rules:

- Interface code (web routes, CLI) contains no business logic; it only calls services.
- Only the Store touches the data directory. Today about ten separate places walk the directory, each with
  its own rule.
- Only the Client sends requests to SofaScore; the shared budget and the health state live there (PR #19).
- The 2,200-line `match_data_fetcher.py` is split into these layers in small steps that do not change
  behaviour.

Note. The file is 2,482 lines at `3ae2599`.

Note. The owner's brief for the detailed designs words the diagram as "one core, three faces": the Python
library, the CLI and the HTTP API sit on the same services, and the web UI sits on the API.

## 3. Common data schema (v1)

The fixed contract offered to the outside. The raw SofaScore JSON is stored separately and given on request.

- **Sport**: slug, name, score family.
- **Tournament** (unique tournament), **Season**, **Category** (country/region).
- **Participant**: a team or a player (a person in tennis, darts, MMA); id, name, type.
- **Event** (match): id, sport, tournament, season, round, participants, `start_utc`,
  - `status`: the SofaScore triple (type, code, description) + our class
    (`not_started | live | completed | decided_without_play | void | unknown`),
  - `score`: a structure by score family (football: half-time / 90 minutes / extra time / penalties; periods;
    sets; innings; e-sports games; MMA result method),
  - `winner`, aggregate score (two-legged ties),
  - `quality`: `observed_at_utc`, `change_ts`, `provisional` (while the refresh window has not closed),
    `tier_hint`.
- **Slice** (data slice): `(event_id, key)` → state (`ok | empty | error | not_requested`), `fetched_at`, raw
  payload. Slices come from the table in the sport registry: statistics, lineups, incidents, h2h, form,
  streaks, point-by-point, innings, e-sports games, odds, …
- **Odds**: market → choice → opening odds, current/closing odds, whether it won; for the main market a
  timestamped list of changes.
- **Non-match data** (league/season/team/player level): standings, season statistics, squads, rankings.
  Fetched once per owning entity, not per match.
- **LiveEvent**: sequence number, time, event type (`status_changed`, `score_changed`, `odds_changed`,
  `stuck`), payload.
- **Change**: old and new value of the fields that changed after the finish (today's `score_changes.jsonl`).

The schema is versioned (`schema_version`). Adding a field is free; removing a field or changing its meaning
bumps the version.

## 4. Storage

- **Raw payloads:** stay as files (the current layout keeps being read; moving is not required).
- **Catalog:** `DATA_DIR/.meta/catalog.db` (SQLite, WAL). Tables: events, slices, tournaments, seasons,
  participants, follows, live_events, changes, jobs. The catalog is **rebuildable** from the raw files; if it
  is damaged it is deleted and produced again.
- Listing, search, "what is missing" and export run from the catalog; today every request reads the whole
  tree from the start.
- `schema.json`: schema version + application version; numbered migrations that can be run again.
- Single-writer rule: one writer lease per data directory; watchers have a separate lease.
- Every match status is stored; `FETCH_ONLY_FINISHED` becomes a read filter.

## 5. HTTP API v1

- Under `/api/v1/...`, every response typed; `openapi.json` is kept in the repository and CI fails when it
  changes unannounced. Frontend types are generated from it. The old `/api` stays as an alias for one release.
- Resources: `sports`, `tournaments`, `seasons`, `events` (filters: sport, tournament, season, date range,
  status, team name), `events/{id}`, `events/{id}/slices/{key}`, `events/{id}/odds`, `changes?since=`,
  `follows`, `jobs`, `exports`, `health`, `diagnostics`.
- Raw data: on every resource, `?raw=1` or `/raw` gives SofaScore's response as it is.
- Live: `GET /api/v1/live/stream?after=<sequence>` (SSE). After a dropped connection it continues from the
  sequence where it stopped.
- Errors: a machine-readable code + a human-readable message (`blocked`, `rate_limited`, `not_found`,
  `job_running`, …).

## 6. CLI (servers and automation)

Principles: it asks no questions; output is JSON on request; logs go to a separate channel; running it again
is safe; exit codes are meaningful.

| Command | Job |
|---|---|
| `sync` | Brings the follow list of the configuration file up to date (fixtures, missing data, refresh). One line for cron |
| `fetch` | Fetches data for a given sport/tournament/season/match |
| `watch` | Watches live; streams events to the chosen targets; runs as a service |
| `export` | Exports from the catalog (CSV, JSONL, Parquet, SQLite) |
| `serve` | Starts the HTTP API and the web UI |
| `status` / `doctor` | Health, blocked state, last success; environment check |
| `describe` | Gives the supported sports, data slices, commands and schemas in machine-readable form (for agents) |
| `diagnostics` | Produces the diagnostics bundle |

Exit codes: 0 success · 1 general error · 2 usage/configuration error · 3 partial success · 4 SofaScore is
blocking · 5 storage error · 6 another copy is running.

Configuration: one declarative file (follow list: sport/tournament/season + data slices; rate; targets;
schedule). Every setting can be overridden by an environment variable (for Docker).

## 7. Output targets (pluggable)

- **stdout**: JSON line by line (the calling program or agent reads it directly).
- **file**: the current layout + exports in fixed formats.
- **database**: the catalog is already a queryable SQLite file; a PostgreSQL target later.
- **webhook**: live events and job notifications; signed body, retries, safe to repeat thanks to the sequence
  number.
- **message queue**: later, a new target on the same interface.

## 8. Live watching

- Source 1: push (the connection of the browser page in the background is listened to; credentials are not
  touched).
- Source 2: polling (30 s), when push stays silent or drops.
- Detail (incidents, statistics, point-by-point) by polling; the site does the same (10–20 s).
- Events are written to the catalog with a sequence number; API, CLI and webhook read the same stream.
- The watcher is a supervised background service, separate from download jobs.
- It is finalised according to the results of this evening's endurance test.

## 9. Network access and security

No account system; access control is in the network layer of whoever installs it (firewall, reverse proxy,
VPN, allowlist). What the application must still do:

- It binds to the local address only by default. The Docker example does the same.
- Allowed host names (`ALLOWED_HOSTS`) are given explicitly; they do not become `*` when it is exposed.
- An optional access key (off by default): for those who cannot use the network layer or want extra assurance.
- When it is started exposed and without a key, a clear warning is printed.
- Attacks that come through the browser get past the firewall (a malicious web page can make the user's
  browser send requests to the local application). Therefore state-changing requests are never GET, the origin
  is checked, and basic security headers are added. This is a fix that does not depend on the allowlist.

## 10. Work packages and order

Wave 2 (running): data integrity, logs/diagnostics, job guards, doctor, blocked-state messages, CI.

Wave 3 — foundation (in order, because they share the same code):

1. Store layer + catalog (without changing behaviour: first the read paths move to the catalog).
2. Service layer: splitting `match_data_fetcher.py`; web routes and the CLI call services.
3. Default rate 5/s, default language, security fixes (small, parallel).

Wave 4 — contract (parallel):

4. API v1 + OpenAPI snapshot + typed errors.
5. Common schema + export formats.
6. Selectable data slices (in the request path and in the settings) + storing every status.

Wave 5 — expansion (parallel, independent thanks to the sport registry):

7. 13 class A sports (8 period-based, 5 set-based).
8. 5 class B sports.
9. New data slices: odds, non-match data (standings, season/player statistics, …).

Wave 6 — faces (parallel, against the same contract):

10. Rewrite of the CLI (the menu UI is removed).
11. Live service: push listener + SSE + webhook.
12. Web UI update: first the screen design (approval), then the implementation.

## 11. Open questions

- Is the push channel robust in production (this evening's test).

## 12. Old data and migration (detail of the decision)

- Every newly written match is stored in the new layout: a directory by id (the path does not change when a
  league is renamed), compressed payload.
- The old layout is not deleted and not moved; the Store reads both layouts and the catalog presents them as
  one list.
- The `migrate` command is optional: it moves match by match, reads each file back and verifies it after
  writing, and only then deletes the old one; if interrupted it continues where it stopped. It first shows
  what it will do with `--dry-run`.
- Reason: an automatic bulk migration puts the user's data at risk during an update; the lazy move removes
  that risk.
- The compression format is chosen by measurement in wave 3 (candidate: zstd; fallback: gzip, because it is
  in the standard library).
- Export is always in open formats: JSON/JSONL (raw or common schema), CSV, Parquet, SQLite.

---

## 13. Where the detailed designs refine or differ from this draft

None of the following is decided by this document. Each point is listed with its reasoning under "Decisions
needed" in `03-implementation-plan.md`.

| Draft | Detailed design | Where |
|---|---|---|
| Catalog tables include follows, live_events and jobs, and the catalog can be deleted and rebuilt (section 4) | Both cannot hold for one file. Follows, jobs and event streams move to a second file, `.meta/state.db`, which is backed up; `catalog.db` stays fully rebuildable | `01-storage.md` 3.1 |
| "Numbered migrations that can be run again" for the schema (section 4) | Numbered migrations for `state.db`; the catalog is rebuilt instead of migrated | `01-storage.md` 7 |
| One writer lease, watchers separate (section 4) | The same two, named `writer` and `live` (`watcher:<sport>` until the live service exists), plus `maintenance` for clear, restore and rebuild and `sinks` for the webhook dispatcher | `01-storage.md` 6.1 |
| Compression: candidate zstd, fallback gzip, chosen by measurement (section 12) | Measured: gzip level 6 (10.8× smaller than today; zstd-9 would be 6.5 % smaller and needs a dependency on Python 3.10–3.13) | `01-storage.md` 4.3 |
| `migrate` verifies, then deletes the old copy (section 12) | Two steps: `migrate` converts and verifies and keeps the old copy; `migrate --delete-legacy` removes verified old copies | `01-storage.md` 5.4 |
| "The Store reads both layouts" (section 12) | Also: an old-layout match that is written again (refresh, missing slice) is first copied to the new layout; the old folder is left untouched | `01-storage.md` 5.3 |
| Database target: "the catalog is already a queryable SQLite file" (section 7) | The catalog's schema is internal; the supported database output is the SQLite export in the public schema | `01-storage.md` 4.5, `02-services.md` 5.2 |
| Raw data: "SofaScore's response as it is" (section 5) | The stored payload is the parsed response written again: same values and key order, not the same bytes. This is what is stored today | `01-storage.md` 4.1 |
| Exit codes 0–6 (section 6) | The same, plus 130/143 for a one-shot command stopped by a signal | `02-services.md` 4.5 |
| One sequence of live events (sections 3, 8) | One sequence for four streams (live, change, job, system) | `01-storage.md` 2.3, `02-services.md` 5.1 |
| Follow list in the configuration file (section 6) | Three origins of follows: the config file, the API/web UI, and the existing `leagues.txt` for installations without a config file | `02-services.md` 4.3 |
| Wave order (section 10) | Kept. Inside wave 3 the Store and the service layer are interleaved PR by PR because they share files; the plan gives the order | `03-implementation-plan.md` |
