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
(pull requests #170 to #172), marks its references `48e4c4c`, a ninth, later that day after the live
validation against the real site, its fixes FX-26 and FX-27, and FX-16 (pull requests #174 to #176), marks
its references `43ecdfc`, and a tenth, on 2026-10-09 after the 3.0.0 release, P30 and the first items of
3.1 (pull requests #177 and #179 to #191), marks its references `216c2f9`.
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
| [03-implementation-plan.md](03-implementation-plan.md) | The status of the work; one ordered plan of 97 items with lanes, dependencies, owned files, behaviour changes and briefs (each with what earlier pull requests learned about it); the dependency diagram; what can start now; release points; decisions needed; open questions; the known defects that tests pin; follow-ups and what is deliberately not planned; how 3.0.0 was released (section 18); what 3.1 has and what is open (section 19) | you are about to start or review a pull request, or you want to know how far 3.1 is |
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

As of 2026-10-09, after the tenth revision (details and pull request numbers in the "Status" section at the
top of `03`; the release in its section 18, 3.1 in its section 19):

- **3.0.0 is released** (2026-10-08): the tag `v3.0.0` on `1c5bab2`, the GitHub Release with the source
  archives and checksums, and the image `ghcr.io/tunjayoff/sofascore_scraper:3.0.0`. There is no PyPI
  package; users install from source. The release pull request (#179) set the version, closed the
  changelog, rewrote the READMEs with screenshots of real data, fixed a 409 after a finished export and
  added `ssc watch --idle`.
- **The plan is complete**: all 97 items are merged or done. The last two were FX-28 (#177: local
  suggestions by word start, the match lists start with the played matches) and P30 (#186), the 3.1 clean-up:
  the 2.x flags, `/api` routes, environment names, backup scopes, shims and fetcher faces are gone, `.env`
  lines are the environment layer, and the catalog schema is 2.
- **3.1 so far** (section 19 of `03`): the research explorer repaired and made private by design (FX-29, three
  pull requests), the slice evidence regenerated and the slice rows that follow it (FX-31), the research
  scripts and installers on the 3.1 names (FX-32, FX-33), a Windows timing flake (FX-30), and batch B: single
  set darts and e-sports game scores (B3, `DERIVE_VERSION` 8), a team record route, the sports' individual
  flag and a team and player filter for exports (B1), coverage per follow, a player's matches, request
  counters of a job, the wait before a confirming request and a security fix under a root path (B2).
- **Open for 3.1**: the slice rows of rugby, floorball, volleyball and minifootball (the owner decides after
  more evidence), a rule for team streaks that answer 200 with an empty body, baseball's umpires, weather,
  comments and at-bats, the odds providers' names, live pages of the repaired explorer, a replacement for
  the events columns `stage_name` and `listed_in`, the Store's durability from its caller, the explorer's
  unthrottled live-match-tracker document, and a few small leftovers of P30. Not in 3.1: a localized date
  picker (owner) and the web UI under a path prefix.
- What a user can see: everything a download writes is in the new layout (`v3/`, 8.4 times smaller on the
  owner's data) and the old folders are read in place; `ssc` covers every task of the former terminal menu
  and of `main.py`, whose old flags are now usage errors that name their command; the web app runs on
  `/api/v1`, speaks plain words and has Help; leagues, teams, players and single matches can be followed
  and downloaded, found by a search that suggests as you type, with seasons and data types chosen first;
  exports can be narrowed to teams and players; one league's data can be deleted and a backup restored from
  the browser; `ssc watch` follows live matches through the site's own page or, if chosen knowingly, the push
  server directly, and `--idle` waits for a live follow. The library is imported as `sofascore_scraper`. The
  web UI needs Safari 16.4, Chrome 111 or Firefox 128 or newer.
- Decisions: settled on 2026-10-02 and 2026-10-03 are the web UI built from scratch on the same stack, the
  approval of `05` with all its 22 decisions, S18, FX-12, the rule for the Windows CI job, best-effort
  Windows and macOS with merges on a green Linux CI plus a local full run, and the live validation at the
  end of the project. Settled since: D1 (the package is renamed; owner; done by REN-1), team, player and
  single-match follows in 3.0.0 and search as the user types (owner, 2026-10-06), S11 closed, the old backup
  scope names deprecated in 3.0.0 and removed in P30, the two ST-27 rules kept as built, scheduler tasks
  counted from their last run, the odds country only when the user sets it, the odds-history pruning task
  off by default (delegated); on 2026-10-07 the owner's: the raw research files keep the push server's
  address as research records, the orchestrator's end-to-end test through the web UI before the live
  validation, and the repository's new description and topics; on 2026-10-08 the owner's: every remaining
  step runs from the orchestrator's session, 3.0.0 is not published to PyPI, the `direct` step approved in
  the validation's session, the three slice proposals of #121 applied (FX-16), the tag, and the work after
  3.0.0 started at once with P30; on 2026-10-09 the owner's: FX-31's slice rows kept, the four sports' rows
  wait for more evidence, and no localized date picker now. The orchestrator's, reversible: one type-ahead
  list in SofaScore's order (FX-26), a no-data answer of an unfinished match kept as an uncounted `empty`
  (FX-27), score changes of set sports kept as sets won, and the rules of batch B (a 60 s wait before a
  confirming request, the connection state by the last outcome, a player's matches by the stored line-ups).
  Waiting for the owner: the four sports' slice rows, the team-streaks rule, the baseball proposals, and
  when 3.1 is released.
