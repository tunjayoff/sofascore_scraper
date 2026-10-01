# Platform 3.0.0 — design documents

These documents describe how the SofaScore scraper becomes a data platform: one core with three faces (Python
library, command line, HTTP API), a Store that owns the data directory, a rebuildable catalog, one fetch
pipeline, and a live service. They contain no code. Every statement about today's code cites `file:line` at
`origin/main` commit `3ae2599` (2026-10-01).

## Reading order

| Document | What it is | Read it when |
|---|---|---|
| [00-platform.md](00-platform.md) | The owner's platform design in English: decisions, layers, schema v1, storage, API v1, CLI, sinks, live, security, waves. Its last section lists where the detailed designs differ from the draft | you want the goal and the fixed requirements |
| [01-storage.md](01-storage.md) | The Store: what is on disk today and who touches it, the v3 layout, compression measurements, the catalog and state databases with their DDL, both layouts read side by side, `migrate`, leases and the write protocol, backup and restore | you work on anything under `DATA_DIR` |
| [02-services.md](02-services.md) | The service layer and the faces: what `match_data_fetcher.py` does today, the client, the one pipeline, jobs across processes, the CLI contract, sinks and webhooks, API v1 resources, the live service, removal of the terminal UI | you work on fetching, jobs, the CLI, the API or live |
| [03-implementation-plan.md](03-implementation-plan.md) | One ordered plan of 67 pull requests with lanes, dependencies, owned files, behaviour changes and briefs; the dependency diagram; the first batch; release points; decisions needed; open questions | you are about to start or review a pull request |

## How the documents relate

- `00` is the requirement. `01` and `02` were designed in parallel against it and then reconciled with each
  other and with the code; each ends with a section "What changed during reconciliation".
- `03` is the only PR list. The PR ids that appear in `01` and `02` are the ids of `03`.
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
9. Live events, changes and job notifications are streams with sequence numbers, read by SSE, the CLI and
   webhooks alike.
10. The public contract is a versioned normalized schema; raw payloads are available on request.

## Status

Design only. Nothing in these documents is implemented yet. The first five pull requests of the plan (G-01,
G-02, ST-02, ST-03, P07) can start at once; the items marked "after open PRs" in the plan wait for the open
pull requests #24, #23 and #32.
