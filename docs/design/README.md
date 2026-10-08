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
newcomer pass of the web UI (pull requests #134 to #161), marks its references `b6caf2f`. A seventh, on
2026-10-07 after FX-20, FX-21, FX-22 and REN-1 (pull requests #163 to #168), marks its references `6f79344`,
an eighth, on 2026-10-08 after the end-to-end test against the real site and its fixes FX-23 to FX-25
(pull requests #170 to #172), marks its references `48e4c4c`, and a ninth, later that day after the live
validation against the real site, its fixes FX-26 and FX-27, and FX-16 (pull requests #174 to #176), marks
its references `43ecdfc`.
Since REN-1 the import package is `sofascore_scraper` (it was `src`): the documents write its paths with
the new name, and a `file:line` reference keeps the line it had at the commit its revision names (the rename
moved no line). Paths of files removed before the rename keep the old `src/` form. Where a section
of `01`, `02` or `05` describes something that exists, it says "as built" and names the plan item and the
pull request.

## Reading order

| Document | What it is | Read it when |
|---|---|---|
| [00-platform.md](00-platform.md) | The owner's platform design in English: decisions (with their state and the decisions taken after the draft), layers, schema v1, storage, API v1, CLI, sinks, live, security, waves. Its last section lists where the detailed designs differ from the draft | you want the goal and the fixed requirements |
| [01-storage.md](01-storage.md) | The Store: what is on disk today and who touches it, the v3 layout, compression measurements, the catalog and state databases with their DDL, both layouts read side by side, `migrate`, leases and the write protocol, backup and restore | you work on anything under `DATA_DIR` |
| [02-services.md](02-services.md) | The service layer and the faces: what `match_data_fetcher.py` does today, the client, the one pipeline, jobs across processes, the CLI contract, sinks and webhooks, API v1 resources, the live service, removal of the terminal UI | you work on fetching, jobs, the CLI, the API or live |
| [03-implementation-plan.md](03-implementation-plan.md) | The status of the work; one ordered plan of 97 items with lanes, dependencies, owned files, behaviour changes and briefs (each with what earlier pull requests learned about it); the dependency diagram; what can start now; release points; decisions needed; open questions; the known defects that tests pin; follow-ups and what is deliberately not planned; what remains before the 3.0.0 release (section 18) | you are about to start or review a pull request, or you want to know how far the release is |
| [04-schema-v1.md](04-schema-v1.md) | The normalized schema v1, the public data contract, field by field: every record the platform gives out (Sport, Category, Tournament, Season, Participant, Event with its score by score family, Slice, Change, LiveEvent) with type, unit, null rule, source in SofaScore's payload and meaning; the odds and standings records of P28 (`Odds`, `OddsLine`, `StandingsRow`, in the generated contract since FX-21); the versioning rule; what "raw" means; the 28 decisions taken while it was written. Approved on 2026-10-02 | you consume the platform's data, or you work on the API, the exports, the sinks or a new sport |
| [05-web-ui.md](05-web-ui.md) | The screens of the web UI, designed from scratch on API v1 (plan item FE-1): users, information architecture, design system, every screen with its routes and fields and states, the routes still missing with their owners, and the decisions taken. Approved by the owner on 2026-10-02 with all 22 decisions; built by FE-2 (#107, #132, #133), then reworked after a first-time-user review (FX-14a #154, FX-14b #161; the classic views removed) and given the search as the user types (FX-20 #167); its last section says what changed while it was built, with the words as built | you work on the frontend |

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

As of 2026-10-08, after the ninth revision (details and pull request numbers in the "Status" section at the
top of `03`; what remains before the release in its section 18):

- Merged: 95 of the 97 items; FX-28 is in progress. Since the eighth revision: the live validation against
  the real site (Talimat 07), run by the orchestrator on 2026-10-08: the Docker image (built and
  smoke-tested), a pass over the web UI with one finished match of each of 20 sports, live matches of 13
  sports, both push sources (`direct` approved by the owner and tried for five minutes), odds, follows and
  the type-ahead; its fix items FX-26 (the score of every sport on the match page, coverage over finished
  matches only, one suggestion list in SofaScore's order, "/" in the names of team, player and match
  follows, the server's time left, English client errors, a route `/events/{id}/extra`) and FX-27 (`ssc
  watch --sport`, live codes without a type, a closing browser without pending handlers, a no-data answer of
  an unfinished match, the WTA rankings, `winning_odds`); and FX-16, the owner's decision on the detail
  slices of football, basketball and tennis. FX-28 (three small points of the web UI) is in progress.
  Since the seventh revision: the orchestrator's end-to-end test of every
  feature through the web UI against the real site (2026-10-07, re-tested on 2026-10-08) and its three fix
  items: FX-24 (the web UI: dialogs, search hits, a follow's page, the Odds tab as a table, the export
  filter, Health, Settings), FX-23 (a football match that went straight to penalties no longer gets an
  extra-time score, exports run during a download, searches go ahead of downloads in the request budget, a
  second process gets a temporary browser profile, a backup records its own job, English logs) and FX-25
  (the Store's messages in English, "Women" and "National team" in search hits, no lock file left next to
  the settings). Since the sixth revision: the search as the user types, like the site, one
  search across leagues, teams and players, job names and the small gaps of the web UI (FX-20), the odds and
  standings models of P28 in schema v1 (FX-21), the code follow-ups of the audit of the user documents
  (FX-22, new: the Settings page's settings in backups and restores, the stale texts of `ssc doctor`,
  `main.py --help` and the image, the start scripts that follow the language set in the app, a volume for
  the live browser profile in the Compose example), and the rename of the import package to
  `sofascore_scraper` with `ssc` in the Docker image (REN-1). Two pull requests outside the plan rewrote
  the user documents for 3.0.0: the audit (#163) and the new README (#166). Merged before them: the safety
  net; the whole Store (catalog, state database, leases, the v3 writers, slice history, `migrate`, backup
  format 2 with restore, the raw export); every reader and the planning of downloads on the catalog; the
  one fetch pipeline; the client, the configuration and the job manager across processes; the new CLI with
  `main.py` as a shim and the terminal menu removed; schema v1 and API v1 with the legacy routes as
  adapters; the sinks and the live service with the `page` source (default) and the opt-in `direct` source;
  all 21 sports; every match status stored; user-selectable slices, odds and non-match data; team, player
  and single-match follows; the normalized exports; the optional in-app scheduler; the new web UI, designed
  (`05`) and built from scratch and reworked after a first-time-user review; and the fix items.
- Still to do before 3.0.0 (section 18 of `03`): FX-28; the release pull request (the version bump to 3.0.0,
  since `pyproject.toml` still says 2.0.0 and the web UI shows v2.0.0; the changelog close with the entries
  of #161 to #176 and FX-28; the README with screenshots of real data, without the "in preparation" note,
  and with the rule that a score change of a set sport is a set won; a few stale comments); the Docker
  smoke test on its commit (an image of `137cabe` passed on 2026-10-08); and the tag, which is the owner's.
  P30 follows in 3.1.
- What a user can see: everything a download writes is in the new layout (`v3/`, 8.4 times smaller on the
  owner's data) and the old folders are read in place; `ssc` covers every task of the former terminal menu
  and of `main.py`; the web app is new, runs on `/api/v1`, speaks plain words ("Add league", "Download
  now") and has Help, and the old pages are gone; leagues, teams, players and single matches can be
  followed and downloaded, found by a search that suggests as you type, with seasons and data types chosen
  first; one league's data can be deleted and a backup restored from the browser, the settings saved there
  included; `ssc watch` follows live matches through the site's own page or, if chosen knowingly, the push
  server directly. The library is imported as `sofascore_scraper`, and a checkout needs `pip install -e .`
  again after the rename. The web UI needs Safari 16.4, Chrome 111 or Firefox 128 or newer.
- Decisions: settled on 2026-10-02 and 2026-10-03 are the web UI built from scratch on the same stack, the
  approval of `05` with all its 22 decisions, S18, FX-12, the rule for the Windows CI job, best-effort
  Windows and macOS with merges on a green Linux CI plus a local full run, and the live validation at the
  end of the project. Settled since: D1 (the package is renamed; owner; done by REN-1), team, player and
  single-match follows in 3.0.0 and search as the user types (owner, 2026-10-06), S11 closed, the old backup
  scope names deprecated in 3.0.0 and removed in P30, the two ST-27 rules kept as built, scheduler tasks
  counted from their last run, the odds country only when the user sets it, the odds-history pruning task
  off by default (delegated); on 2026-10-07 the owner's: the raw research files keep the push server's
  address as research records (no masking, no history rewrite; the documents and READMEs stay clean), the
  orchestrator's end-to-end test through the web UI before the live validation, and the repository's new
  description and topics; on 2026-10-08 the owner's: the coder session has ended and every remaining step,
  the live validation included, runs from the orchestrator's session, and 3.0.0 is not published to PyPI
  (the tag, the GitHub Release and the Docker image; installation from source); later on 2026-10-08 the
  owner's: the `direct` step approved in the validation's session (run for five minutes; it worked and
  wrote no credential anywhere) and the three slice proposals of #121 applied (FX-16); the orchestrator's,
  reversible: one type-ahead list in SofaScore's order instead of groups by kind (FX-26), a no-data answer
  of an unfinished match kept as an uncounted `empty` (FX-27), and score changes of set sports kept as sets
  won. Waiting for the owner: the tag; optional, whether the type-ahead list goes back to groups by kind.
  After 3.0.0: P30 and the small items of section 18 of `03`.
