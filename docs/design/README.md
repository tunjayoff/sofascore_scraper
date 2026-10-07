# Platform 3.0.0 — design documents

These documents describe how the SofaScore scraper becomes a data platform: one core with three faces (Python
library, command line, HTTP API), a Store that owns the data directory, a rebuildable catalog, one fetch
pipeline, and a live service. They contain no code. Every statement about today's code cites `file:line` at
`origin/main` commit `3ae2599` (2026-10-01). The documents were revised the same day, after the first two
implementation batches and two owner decisions on live watching; references that the revision added or
corrected are marked `0aa73b4`, the commit they were checked against. They were revised again on 2026-10-02,
after batches three and four; references of that revision are marked `f286723`. A third revision followed
later that day, after batches five to seven; its references are marked `e0bae0c`. A fourth, at the end of
that day after batches eight to ten, marks its references `9b03c64`, a fifth, on 2026-10-03 after batches
eleven to nineteen, marks its references `b3cb819`, and a sixth, on 2026-10-06 after P27 to FX-19 and the
newcomer pass of the web UI (pull requests #134 to #161), marks its references `b6caf2f`. Where a section
of `01`, `02` or `05` describes something that exists, it says "as built" and names the plan item and the
pull request.

## Reading order

| Document | What it is | Read it when |
|---|---|---|
| [00-platform.md](00-platform.md) | The owner's platform design in English: decisions (with their state and the decisions taken after the draft), layers, schema v1, storage, API v1, CLI, sinks, live, security, waves. Its last section lists where the detailed designs differ from the draft | you want the goal and the fixed requirements |
| [01-storage.md](01-storage.md) | The Store: what is on disk today and who touches it, the v3 layout, compression measurements, the catalog and state databases with their DDL, both layouts read side by side, `migrate`, leases and the write protocol, backup and restore | you work on anything under `DATA_DIR` |
| [02-services.md](02-services.md) | The service layer and the faces: what `match_data_fetcher.py` does today, the client, the one pipeline, jobs across processes, the CLI contract, sinks and webhooks, API v1 resources, the live service, removal of the terminal UI | you work on fetching, jobs, the CLI, the API or live |
| [03-implementation-plan.md](03-implementation-plan.md) | The status of the work; one ordered plan of 90 items with lanes, dependencies, owned files, behaviour changes and briefs (each with what earlier pull requests learned about it); the dependency diagram; what can start now; release points; decisions needed; open questions; the known defects that tests pin; follow-ups and what is deliberately not planned; what remains before the 3.0.0 release (section 18) | you are about to start or review a pull request, or you want to know how far the release is |
| [04-schema-v1.md](04-schema-v1.md) | The normalized schema v1, the public data contract, field by field: every record the platform gives out (Sport, Category, Tournament, Season, Participant, Event with its score by score family, Slice, Change, LiveEvent) with type, unit, null rule, source in SofaScore's payload and meaning; the odds and standings models of P28, which a follow-up (FX-21) moves into the generated contract; the versioning rule; what "raw" means; the 28 decisions taken while it was written. Approved on 2026-10-02 | you consume the platform's data, or you work on the API, the exports, the sinks or a new sport |
| [05-web-ui.md](05-web-ui.md) | The screens of the web UI, designed from scratch on API v1 (plan item FE-1): users, information architecture, design system, every screen with its routes and fields and states, the routes still missing with their owners, and the decisions taken. Approved by the owner on 2026-10-02 with all 22 decisions; built by FE-2 (#107, #132, #133), then reworked after a first-time-user review (FX-14a #154, FX-14b #161; the classic views removed); its last section says what changed while it was built, with the words as built | you work on the frontend |

## How the documents relate

- `00` is the requirement. `01` and `02` were designed in parallel against it and then reconciled with each
  other and with the code; each ends with a section "What changed during reconciliation". `05` was designed
  after them and ends with "What changed while it was built".
- `03` is the only PR list. The PR ids that appear in `01` and `02` are the ids of `03`.
- `04` is the field-level contract for section 3 of `00`. Its field tables are generated from the models in
  `sofascore_scraper/schema`, and its examples are validated against the generated JSON Schema; a test fails when they
  differ. It was written by the pull request of its plan item (SC-1) and approved before that was merged.
- A pull request that runs alone and finds a document wrong corrects it in the same pull request. Pull
  requests of a parallel batch leave these documents and the changelog alone: each lists its mismatches and
  its changelog text in its description, and a docs pull request and a changelog pull request fold them in
  afterwards (`03`, section 2, rules 3 and 7). `01`, `02`, `03` and `05` each end their "what changed"
  section with the corrections made that way.
- Where a point is not settled by the owner's decisions, the documents go ahead with the option that keeps
  existing data safe and the change reversible, and list the point under "Decisions needed" in `03`
  (section 13). Nothing there is final until the owner confirms it.

## The design in ten lines

1. Only `sofascore_scraper/store` touches the data directory; only `sofascore_scraper/client` talks to SofaScore; faces contain no logic.
2. Raw payloads stay as files, one file per slice, gzip-compressed (measured: 10.8 times smaller than today).
3. New writes go to an id-keyed layout under `DATA_DIR/v3/`; the old layout is read in place and never
   changed outside the explicit `migrate` command and a delete or clear that the user asks for.
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

As of 2026-10-06, after the sixth revision (details and pull request numbers in the "Status" section at the
top of `03`; what remains before the release in its section 18):

- Merged: 85 of the 90 items. Since the fifth revision: user-selectable slices (P27), the removal of the
  transition code with strict boundary tests (ST-28), odds and the data of seasons, teams, players and sports
  (P28), the routes the new web UI lacked with a real restore (FX-13), team, player and single-match follows
  that download, per-league delete and the connection state (FX-19), the newcomer pass and the last screens of
  the web UI with the classic views removed (FX-14a, FX-14b; FX-14 was split in two), the clean-up (FX-15),
  the Windows CI split (FX-17) and the last cancel checks of the request path (FX-18). Four new items came out
  of the batch and a first-time-user review: FX-14a, FX-14b, FX-19 and FX-20, and this revision proposes
  FX-21. Merged before them: the safety net; the whole Store (catalog, state database, leases, the v3 writers
  for match details, schedules and season lists, slice history, `migrate`, backup format 2 with restore, the
  raw export); every reader and the planning of downloads on the catalog; the one fetch pipeline; the client,
  the configuration and the job manager across processes; the new CLI (`sync`, `fetch`, `refresh`, `status`,
  `follows`, `jobs`, `data`, `export`, `backup`, `migrate`, `catalog`, `serve`, `watch`, `events`) with
  `main.py` as a shim and the terminal menu removed; schema v1 and API v1 with the legacy routes as adapters;
  the sinks and the live service with the `page` source (default) and the opt-in `direct` source; all 21
  sports; every match status stored; the normalized exports in JSONL, CSV, Parquet and SQLite; the optional
  in-app scheduler; the new web UI, designed (`05`) and built from scratch; twelve fix items; and outside the
  plan's briefs the changes that rule 9 of `03` records.
- In progress: FX-20 (search as the user types, like the site; one search across kinds; job names; small
  UI gaps).
- Still to do before 3.0.0: FX-21 (the odds and standings models of P28 into the generated schema
  contract), REN-1 (the package becomes `sofascore_scraper`, decided by the owner; when no other branch is
  open), the live validation against the real site, done once at the end in a busy match window (with the
  odds, the team, player and match follows, the search and the Docker image build and smoke test), FX-16
  (the detail slices of football, basketball and tennis after it), the version bump to 3.0.0
  (`pyproject.toml` still says 2.0.0, and the web UI shows v2.0.0), the changelog close (`[Unreleased]`
  becomes `[3.0.0]`) and the tag, which is the owner's. P30 follows in 3.1.
- What a user can see: everything a download writes is in the new layout (`v3/`, 8.4 times smaller on the
  owner's data) and the old folders are read in place; `ssc` covers every task of the former terminal menu
  and of `main.py`; the web app is new, runs on `/api/v1`, speaks plain words ("Add league", "Download
  now") and has Help, and the old pages are gone (their addresses lead to the new screens); leagues, teams,
  players and single matches can be followed and downloaded, with seasons and data types chosen first; one
  league's data can be deleted and a backup restored from the browser; `ssc watch` follows live matches
  through the site's own page or, if chosen knowingly, the push server directly. The web UI needs Safari
  16.4, Chrome 111 or Firefox 128 or newer.
- Decisions: settled on 2026-10-02 and 2026-10-03 are the web UI built from scratch on the same stack, the
  approval of `05` with all its 22 decisions, S18 (a delete or clear that the user asks for removes the old
  layout's copies too), FX-12, the rule for the Windows CI job, best-effort Windows and macOS with merges on a
  green Linux CI plus a local full run, and the live validation at the end of the project. Settled since:
  D1 (the package is renamed; owner), team, player and single-match follows in 3.0.0 and search as the user
  types (owner, 2026-10-06), S11 closed, the old backup scope names deprecated in 3.0.0 and removed in P30,
  the two ST-27 rules kept as built, scheduler tasks counted from their last run, the odds country only when
  the user sets it, the odds-history pruning task off by default (delegated; section 13 of `03`). Waiting
  for the owner: the three slice proposals of #121 (after the validation; FX-16) and the tag.
