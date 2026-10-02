# Platform 3.0.0 — design documents

These documents describe how the SofaScore scraper becomes a data platform: one core with three faces (Python
library, command line, HTTP API), a Store that owns the data directory, a rebuildable catalog, one fetch
pipeline, and a live service. They contain no code. Every statement about today's code cites `file:line` at
`origin/main` commit `3ae2599` (2026-10-01). The documents were revised the same day, after the first two
implementation batches and two owner decisions on live watching; references that the revision added or
corrected are marked `0aa73b4`, the commit they were checked against. They were revised again on 2026-10-02,
after batches three and four; references of that revision are marked `f286723`. A third revision followed
later that day, after batches five to seven; its references are marked `e0bae0c`. A fourth, at the end of
that day after batches eight to ten, marks its references `9b03c64`. Where a section of `01`
or `02` describes something that exists, it says "as built" and names the plan item and the pull request.

## Reading order

| Document | What it is | Read it when |
|---|---|---|
| [00-platform.md](00-platform.md) | The owner's platform design in English: decisions (with their state and the decisions taken after the draft), layers, schema v1, storage, API v1, CLI, sinks, live, security, waves. Its last section lists where the detailed designs differ from the draft | you want the goal and the fixed requirements |
| [01-storage.md](01-storage.md) | The Store: what is on disk today and who touches it, the v3 layout, compression measurements, the catalog and state databases with their DDL, both layouts read side by side, `migrate`, leases and the write protocol, backup and restore | you work on anything under `DATA_DIR` |
| [02-services.md](02-services.md) | The service layer and the faces: what `match_data_fetcher.py` does today, the client, the one pipeline, jobs across processes, the CLI contract, sinks and webhooks, API v1 resources, the live service, removal of the terminal UI | you work on fetching, jobs, the CLI, the API or live |
| [03-implementation-plan.md](03-implementation-plan.md) | The status of the work; one ordered plan of 80 pull requests with lanes, dependencies, owned files, behaviour changes and briefs (each with what earlier pull requests learned about it); the dependency diagram; what can start now; release points; decisions needed; open questions; the known defects that tests pin; follow-ups and what is deliberately not planned | you are about to start or review a pull request |
| [04-schema-v1.md](04-schema-v1.md) | The normalized schema v1, the public data contract, field by field: every record the platform gives out (Sport, Category, Tournament, Season, Participant, Event with its score by score family, Slice, Change, LiveEvent) with type, unit, null rule, source in SofaScore's payload and meaning; the versioning rule; what "raw" means; the 28 decisions taken while it was written. Approved on 2026-10-02 | you consume the platform's data, or you work on the API, the exports, the sinks or a new sport |
| [05-web-ui.md](05-web-ui.md) | The screens of the web UI, designed from scratch on API v1 (plan item FE-1): users, information architecture, design system, every screen with its routes and fields and states, the routes still missing, and the decisions for the owner. Waits for the owner's approval | you work on the frontend (FE-2), or you approve the screens |

## How the documents relate

- `00` is the requirement. `01` and `02` were designed in parallel against it and then reconciled with each
  other and with the code; each ends with a section "What changed during reconciliation".
- `03` is the only PR list. The PR ids that appear in `01` and `02` are the ids of `03`.
- `04` is the field-level contract for section 3 of `00`. Its field tables are generated from the models in
  `src/schema`, and its examples are validated against the generated JSON Schema; a test fails when they
  differ. It was written by the pull request of its plan item (SC-1) and approved before that was merged.
- A pull request that runs alone and finds a document wrong corrects it in the same pull request. Pull
  requests of a parallel batch leave these documents and the changelog alone: each lists its mismatches and
  its changelog text in its description, and a docs pull request and a changelog pull request fold them in
  afterwards (`03`, section 2, rules 3 and 7). `01`, `02` and `03` each end their "what changed" section with
  the corrections made that way.
- Where a point is not settled by the owner's decisions, the documents go ahead with the option that keeps
  existing data safe and the change reversible, and list the point under "Decisions needed" in `03`
  (section 13). Nothing there is final until the owner confirms it.

## The design in ten lines

1. Only `src/store` touches the data directory; only `src/client` talks to SofaScore; faces contain no logic.
2. Raw payloads stay as files, one file per slice, gzip-compressed (measured: 10.8 times smaller than today).
3. New writes go to an id-keyed layout under `DATA_DIR/v3/`; the old layout is read in place and never
   changed outside the explicit `migrate` command.
4. `.meta/catalog.db` is a derived index that can be deleted and rebuilt; `.meta/state.db` holds what cannot be
   rebuilt (jobs, event streams, sink cursors, API-created follows) and is part of every backup.
5. Listing, search, "what is missing" and refresh selection are single indexed queries instead of tree walks.
6. One fetch pipeline replaces the two that behave differently today; a job is resumable because the catalog
   is its checkpoint.
7. One writer per data directory across processes (OS file locks); the live service has its own lease.
8. Every match status is stored; "finished only" is a filter when reading.
9. Live events, changes and job notifications are streams with sequence numbers, read by the CLI and delivered
   by sinks (stdout, file, webhook). Live watching is a CLI service, not a web feature and not an HTTP
   endpoint; it listens to a browser page by default (`page`), can connect to the push server directly as an
   explicit opt-in with stated risks (`direct`), and always has polling as the fallback.
10. The public contract is a versioned normalized schema; raw payloads are available on request.

## Status

As of 2026-10-02, after the third revision of that day (details and pull request numbers in the "Status"
section at the top of `03`):

- Merged: the safety net (G-01 to G-04: fetch flows, readers, today's CLI, the remaining routes); the slice
  module (ST-02) and its total presence rules (FX-5); the Store core, its boundary tests, the legacy reader,
  the catalog schema, the state database with the job store on it, the event and listing indexers with
  rebuild, reconcile and verify, the leases and the Store facade, the stream log and the watcher state
  (ST-03 to ST-10, ST-18), the Store's read API (ST-30), the shadow mode, in which the existing writers keep
  the catalog current (ST-11), the v3 writer with promotion and the change-log segments (ST-20), slice
  history (ST-26), and backup and clear through the Store with a bounded reconcile on open (ST-19); four of
  the five readers that move to the catalog (RD-1, RD-2, RD-4, RD-5: match detail, match lists, dashboard
  and statistics, season lists and league sports); the job model (P07), the client facade (P05), the
  Settings model and loader (P09), the first services with the web and the headless flow on them (P08,
  P10), the follows mirror (ST-17), the job manager across processes (P11) and the skeleton of the new CLI
  (P18); schema v1 (SC-1), the foundation of API v1 (P20), the sinks with the `events` command (P22) and
  the live service with polling, `ssc watch` (P23); eleven fix items (FX-1 to FX-11); and outside the
  plan's briefs the 5 requests per second default (X-01), English by default (X-02), the web security
  hardening, which covers X-03 and parts of P20, P25 and EX-1, a fix of the diagnostics bundle, the
  shadow hooks of the terminal menu, a longer CI time limit, two fixes of the test suite and a fix of the
  end of a job.
- In progress: RD-3 (missing details and the need computation from the catalog), ST-22 (the schedule and
  season writers through the Store) and P24 (the `page` push source; #95, open), and the changelog entries of batches
  eight to ten (#94, open).
- Can start now: FX-12 (a write of the v3 writer keeps its change row when it is interrupted;
  `Store.close()` waits for a job that is finishing). Everything else waits for one of the items in
  progress.
- What a user can see so far: a `sofascore.toml` is honoured; only one process writes a data directory at
  a time, in the web app and on the command line (a refused run exits with 6); the single-match fetch says
  when SofaScore refuses it; headless downloads run the web flow, appear in the job history of the web app
  and can be stopped from another process; a second command line, `ssc`, has `version`, `doctor`,
  `describe`, `config`, `diagnostics`, `events` and `watch`, the live service that delivers to the
  configured sinks; the web server has the first routes of `/api/v1` (health, status, sports, jobs,
  settings), and the old routes name their successors; the dashboard, the statistics, the match lists,
  the match detail and the season lists read the data folder's index (`.meta/catalog.db`), which corrects
  double counts and finds data of older versions; the SQLite files and lock files follow the umask. The
  downloads still write the old layout: no feature writes the v3 layout yet, except `ssc watch` for the
  finished matches it confirms.
- Decisions: settled on 2026-10-02 are P2 (schema v1 is approved as written), S15, S16, D19 and D20, and
  with the third revision S17 (the reconcile on open is bounded), P3 (the coverage floor is 85) and D21
  (how the dashboard and the match lists count matches) (section 13 of `03`). One new point waits for the
  owner: S18 (a delete that the user asks for also removes the old layout's copies, against decision 4 of
  `01`).
