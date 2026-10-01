# Platform 3.0.0 — design documents

These documents describe how the SofaScore scraper becomes a data platform: one core with three faces (Python
library, command line, HTTP API), a Store that owns the data directory, a rebuildable catalog, one fetch
pipeline, and a live service. They contain no code. Every statement about today's code cites `file:line` at
`origin/main` commit `3ae2599` (2026-10-01). The documents were revised the same day, after the first two
implementation batches and two owner decisions on live watching; references that the revision added or
corrected are marked `0aa73b4`, the commit they were checked against. They were revised again on 2026-10-02,
after batches three and four; references of that revision are marked `f286723`. Where a section of `01` or
`02` describes something that exists, it says "as built" and names the plan item and the pull request.

## Reading order

| Document | What it is | Read it when |
|---|---|---|
| [00-platform.md](00-platform.md) | The owner's platform design in English: decisions (with their state and the decisions taken after the draft), layers, schema v1, storage, API v1, CLI, sinks, live, security, waves. Its last section lists where the detailed designs differ from the draft | you want the goal and the fixed requirements |
| [01-storage.md](01-storage.md) | The Store: what is on disk today and who touches it, the v3 layout, compression measurements, the catalog and state databases with their DDL, both layouts read side by side, `migrate`, leases and the write protocol, backup and restore | you work on anything under `DATA_DIR` |
| [02-services.md](02-services.md) | The service layer and the faces: what `match_data_fetcher.py` does today, the client, the one pipeline, jobs across processes, the CLI contract, sinks and webhooks, API v1 resources, the live service, removal of the terminal UI | you work on fetching, jobs, the CLI, the API or live |
| [03-implementation-plan.md](03-implementation-plan.md) | The status of the work; one ordered plan of 76 pull requests with lanes, dependencies, owned files, behaviour changes and briefs (each with what earlier pull requests learned about it); the dependency diagram; what can start now; release points; decisions needed; open questions; the known defects that tests pin; follow-ups and what is deliberately not planned | you are about to start or review a pull request |

## How the documents relate

- `00` is the requirement. `01` and `02` were designed in parallel against it and then reconciled with each
  other and with the code; each ends with a section "What changed during reconciliation".
- `03` is the only PR list. The PR ids that appear in `01` and `02` are the ids of `03`.
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

As of 2026-10-02 (details and pull request numbers in the "Status" section at the top of `03`):

- Merged: the safety net (G-01 to G-04: fetch flows, readers, today's CLI, the remaining routes); the slice
  module (ST-02); the Store core, its boundary tests, the legacy reader, the catalog schema, the state
  database with the job store on it, the event and listing indexers with rebuild, reconcile and verify, the
  leases and the Store facade, the stream log and the watcher state (ST-03 to ST-10, ST-18); the job model
  (P07), the client facade (P05), the Settings model and loader (P09), the first services with the web and
  the headless flow on them (P08, P10), the follows mirror (ST-17) and the skeleton of the new CLI (P18);
  five fix items (FX-1, FX-2, FX-3, FX-4, FX-6); and outside the plan's briefs the 5 requests per second
  default (X-01), English by default (X-02) and the web security hardening, which covers X-03 and parts of
  P20, P25 and EX-1.
- In progress: nothing.
- Can start now: ST-30 (the Store's read API), P11 (the job manager) and FX-8 (lock files follow the umask).
- What a user can see so far: a `sofascore.toml` is honoured; `--watch` keeps its state in `state.db`; only
  one process writes a data directory at a time, in the web app and on the command line (a refused run
  exits with 6); the single-match fetch says when SofaScore refuses it; headless downloads run the web
  flow; a second command line, `ssc`, has `version`, `doctor`, `describe`, `config` and `diagnostics`.
  Nothing reads the catalog yet, and the on-disk layout of the data is unchanged.
- Decisions that wait for the owner: section 13 of `03`. New on 2026-10-02: S15, S16 and D19.
