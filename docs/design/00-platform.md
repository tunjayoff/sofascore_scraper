# 00 — Platform design (3.0.0)

This is an English rendering of the owner's design draft of 2026-10-01 (`platform-tasarimi-v0`, written in
Turkish). It turns the decisions taken that day into one architecture and contract outline. It describes the
target structure, not code. Sections 1 to 12 follow the draft section by section. The only additions are the
paragraphs marked "Note", which give context from the repository or from the task brief and decide nothing,
and the changes of the revision described below. Section 13 lists where the detailed designs
(`01-storage.md`, `02-services.md`) refine the draft or propose something different; those points are
proposals until the owner confirms them.

Revised on 2026-10-01, later the same day. The owner took two further decisions on live watching after the
push-channel measurements (`docs/push-channel/README.md`), and three decisions of the draft were carried out
by pull requests. Section 1 lists both. Where a decision changed the draft, the section says so with the
words "Changed on 2026-10-01": sections 2, 5, 6, 7, 8, 10 and 11.

Revised again on 2026-10-02, after two more batches of implementation. No decision of the draft changed. The
state column of section 1 follows the merged work, two notes say what exists (sections 4 and 6), and section
13 has one more row (the place of `.env` among the configuration layers) and marks the row on polling as
settled.

Revised a third time, later on 2026-10-02, after batches five to seven. No decision of the draft changed.
The state column of section 1 follows the merged work; a note in section 3 says where the approved schema
document is more specific than the outline of the draft; the notes of sections 4 and 6 say what exists now,
and sections 5 and 7 have a note each; section 13 has one more row (the schema document) and marks the row
on the configuration layers as settled.

## 1. Decisions (Tuncay, 2026-10-01)

| Topic | Decision | State on 2026-10-02 |
|---|---|---|
| Product position | A data foundation: products are built on it, it runs on servers as automation, applications and agents use it | |
| Sports | 21 sports (the current 3 + 13 that need score mapping + 5 with their own logic). Motor sports, cycling, bandy, water polo and beach volleyball are out of scope | |
| Data types | Everything SofaScore shows without login, including betting odds. Every type is selectable; what is not selected is not fetched | |
| History / live | History: by requests. Live: the push channel, with polling as the fallback. How the push channel is used was decided after the draft: see the two rows at the end of this table | measured, PR #42 |
| Interfaces | The web UI is for people (a large update on the current base). The CLI is for servers and automation only; the menu-driven terminal UI is removed. Live watching belongs to the CLI only (row at the end of this table) | |
| Request rate | Default 5 requests per second; the user may take the risk and remove the limit | done, PR #33 |
| Storage | Raw JSON files stay + a rebuildable SQLite catalog | in progress (`03-implementation-plan.md`, Status): the catalog is built on the first open of a data directory and kept current by the writers (PR #75), and the Store has its read API (PR #68); no feature reads the catalog yet (the first reader items are in review), and the layout on disk is unchanged |
| Data format | A fixed, versioned common schema + raw data for those who want it | the schema is fixed: `04-schema-v1.md`, approved on 2026-10-02 (PR #72); nothing serves it yet |
| Match status | Every status is stored (fixture, live, finished, cancelled); filtering happens when reading | |
| Language | Default English; Turkish when the system language is Turkish | done, PR #39 |
| Platform | Linux and Docker are official; Windows and macOS best-effort | |
| Network access | No account system. Local-only by default; exposing it is the responsibility of whoever installs it (see section 9) | done, PR #43 |
| Raw payloads | Stored compressed; the application and the CLI export them at full size (plain JSON) on request | |
| Old data layout | No automatic migration. New writes use the new layout; old data is read in place; whoever wants to moves it with the `migrate` command (manager decision) | |
| Scheduler | In-app automatic updating is optional, off by default | |
| Version | These changes are 3.0.0 (manager decision): storage layout, API and CLI change incompatibly | |
| Merging | The manager session reviews and merges; the release tag is Tuncay's | |
| Live is not a web feature (2026-10-01, after the draft) | Live data is not part of the web UI and is not exposed over an HTTP endpoint (no SSE stream). A person at a screen can watch live scores on SofaScore itself; the value of the live stream is for servers and programs. Live watching is a CLI service (`watch`) that delivers events to sinks: stdout (JSON lines), file, webhook. A program that wants live data over the network uses the webhook. The event stream is still stored with sequence numbers | the sinks and the `events` command exist (PR #73), and no process hosts the sinks yet; planned: P23 |
| Live sources (2026-10-01, after the draft) | Two selectable push sources in the CLI, plus polling as the fallback that is always present. `page` (default): the service keeps a real browser page open and listens to the page's own push connection; no credential is handled. `direct` (explicit opt-in): a lightweight client connects to the push server itself with the credential read at runtime from the bridge page's own connection, kept in memory only. `direct` is never the default, is never enabled implicitly, and carries clear warnings wherever it is configured or documented (section 8) | planned: P24, P31 |

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
        catalog + raw files   bridge + budget     push source (page or
                              + health            direct) + polling fallback
                   └──────── Sport registry (sports, data slices) ────────┘
```

Rules:

- Interface code (web routes, CLI) contains no business logic; it only calls services.
- Only the Store touches the data directory. Today about ten separate places walk the directory, each with
  its own rule.
- Only the Client sends requests to SofaScore; the shared budget and the health state live there (PR #19).
- The 2,200-line `match_data_fetcher.py` is split into these layers in small steps that do not change
  behaviour.
- Changed on 2026-10-01: Live has one face, the CLI. The HTTP API and the web UI carry no live data
  (section 8).

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

Note. The field-level contract of this section is `04-schema-v1.md` (plan item SC-1, PR #72), approved on
2026-10-02; where it is more specific than the outline above, it is the contract. Three points read
differently there. The live event types carry the prefix of their stream, as the envelope of
`02-services.md` 5.1 has them: `live.status_changed`, `live.score_changed` and `live.stuck`, not the short
names of the list above. A Slice is identified by its owner and its key (`owner_kind`, `owner_id`, `key`,
`sub`), not by `(event_id, key)`, because tournaments and seasons have slices too (season lists, schedule
pages). And in version 1 only football events carry an aggregate score, because the score mapping reads
SofaScore's `aggregated` only in the football family; the other sports get it with their own mapping.
Version 1 gives the payload of a slice raw; Odds and non-match data get their models with plan item P28.

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

Note. The leases exist since PR #50 (plan item ST-10). The web application's jobs and data operations take
them, and since PR #64 (P10) so do the headless runs and `--watch` of the command line: a second writer of
a data directory is refused, whichever face it comes from. The catalog and the state database exist as well
(`.meta/catalog.db`, `.meta/state.db`). Since PR #75 (ST-11) the catalog is built from the files on the
first open of a data directory, reconciled on every later open and updated after every write; since PR #69
(P11) the jobs of every process, web or command line, are in the state database. No feature reads the
catalog yet: the first reader items (RD-1, RD-4 and RD-5) are open pull requests.

## 5. HTTP API v1

- Under `/api/v1/...`, every response typed; `openapi.json` is kept in the repository and CI fails when it
  changes unannounced. Frontend types are generated from it. The old `/api` stays as an alias for one release.
- Resources: `sports`, `tournaments`, `seasons`, `events` (filters: sport, tournament, season, date range,
  status, team name), `events/{id}`, `events/{id}/slices/{key}`, `events/{id}/odds`, `changes?since=`,
  `follows`, `jobs`, `exports`, `health`, `diagnostics`.
- Raw data: on every resource, `?raw=1` or `/raw` gives SofaScore's response as it is.
- Live: none. Changed on 2026-10-01: the draft had `GET /api/v1/live/stream?after=<sequence>` (SSE). Live
  data is not exposed over HTTP; programs read it through the CLI or receive it by webhook (sections 7, 8).
  Job progress keeps its own SSE stream.
- Errors: a machine-readable code + a human-readable message (`blocked`, `rate_limited`, `not_found`,
  `job_running`, …).

Note. The foundation of API v1 exists since PR #74 (plan item P20): the error model with request ids,
`health`, `status`, `sports`, `jobs` (with a job's events as an SSE stream) and `settings`, and the
committed `docs/api/openapi-v1.json` with its snapshot test. The old routes are marked deprecated and name
their successor in a `Link` header. The data resources (`tournaments`, `seasons`, `events`, slices,
`changes`, `follows`, `exports`) come with plan item P21.

## 6. CLI (servers and automation)

Principles: it asks no questions; output is JSON on request; logs go to a separate channel; running it again
is safe; exit codes are meaningful.

| Command | Job |
|---|---|
| `sync` | Brings the follow list of the configuration file up to date (fixtures, missing data, refresh). One line for cron |
| `fetch` | Fetches data for a given sport/tournament/season/match |
| `watch` | Watches live; streams events to the chosen targets; runs as a service. Changed on 2026-10-01: it is the only way to watch live, and it has the option `--source page\|direct\|poll` (section 8) |
| `export` | Exports from the catalog (CSV, JSONL, Parquet, SQLite) |
| `serve` | Starts the HTTP API and the web UI (no live service) |
| `status` / `doctor` | Health, blocked state, last success; environment check |
| `describe` | Gives the supported sports, data slices, commands and schemas in machine-readable form (for agents) |
| `diagnostics` | Produces the diagnostics bundle |

Exit codes: 0 success · 1 general error · 2 usage/configuration error · 3 partial success · 4 SofaScore is
blocking · 5 storage error · 6 another copy is running.

Configuration: one declarative file (follow list: sport/tournament/season + data slices; rate; targets;
schedule). Every setting can be overridden by an environment variable (for Docker).

Note. The file is `sofascore.toml`, and it is honoured since PR #56 (plan item P09): settings from it take
effect in today's application, while its follow list, targets and schedule are read and validated and wait
for the items that use them. The first commands of the new CLI exist next to `main.py` since PR #65 (P18):
`version`, `doctor`, `describe`, `config show|validate|init|path` and `diagnostics`, with the exit codes
above. The data commands of the table (`sync`, `fetch`, `watch`, `export`, `serve`, `status`) are still to
come; until then `main.py` does that work. Since PR #73 (P22) there is also `events`, which prints the
stored event stream, and since PR #69 (P11) the headless runs of `main.py` appear in the job history of the
web application and can be stopped from another process.

## 7. Output targets (pluggable)

- **stdout**: JSON line by line (the calling program or agent reads it directly).
- **file**: the current layout + exports in fixed formats.
- **database**: the catalog is already a queryable SQLite file; a PostgreSQL target later.
- **webhook**: live events and job notifications; signed body, retries, safe to repeat thanks to the sequence
  number. Changed on 2026-10-01: this is the way live data reaches a program on another machine; there is no
  HTTP endpoint to pull or stream it from.
- **message queue**: later, a new target on the same interface.

Note. The stdout, file and webhook targets exist as a library since PR #73 (plan item P22). No command
hosts them yet: `watch` (P23), `serve` (P25) and the one-shot commands (P19) do so in their own items.

## 8. Live watching

Changed on 2026-10-01 by the two decisions at the end of the table in section 1, after the endurance test the
draft waited for (`docs/push-channel/README.md`). The draft's six lines are replaced by the following.

- **A CLI service with sinks.** Live watching is `watch`. It is not part of the web UI and has no HTTP
  endpoint: a person at a screen can watch live scores on SofaScore itself, and the value of the live stream
  is for servers and programs. Events go to sinks: stdout (JSON lines), a file, a webhook. A program that
  wants live data over the network uses the webhook.
- **Source `page` (default).** The service keeps a real browser page open and listens to the push connection
  that the page opens itself. No credential is handled.
- **Source `direct` (explicit opt-in).** A lightweight client connects to the push server itself, using the
  credential read at runtime from the bridge page's own connection. The credential is kept in memory only; it
  is never written to disk, to logs or to the repository, and it is read again if the server rejects it. This
  source is never the default and is never enabled implicitly. Wherever it is configured or documented it
  carries these warnings: it uses the site's own client credential outside the site's client; it may break
  without notice if the credential or the server changes; it may get the IP address blocked; it is a
  terms-of-use grey area that the user chooses knowingly.
- **Polling (30 s) is always present** as the fallback of both push sources, and can be chosen alone.
- **Measured** (one evening, three sports): push delivers a match end about 0.6 to 1.0 s after SofaScore's own
  change time, one-minute polling 32 to 52 s after it; push coverage is about 100 %. The push connection is
  dropped about every 30 minutes and established again, so the polling fallback is mandatory and the service
  must tolerate gaps. Frames carry only the changed fields, so the service keeps the last known state of
  every event. Only the subject `sport.{sport}`, which a sport page subscribes to, gives a whole sport; a
  match page subscribes to `event.{id}` only.
- **Cost.** Listening to a page costs about 1.8 to 2.6 GB of memory per sport page even with ads and images
  blocked; an idle bridge tab is about 1.1 GB; the direct client is about 0.2 GB.
- Detail (incidents, statistics, point-by-point) by polling; the site does the same (10–20 s).
- Events are written to the state database with a sequence number; the CLI and the sinks read the same
  stream.
- The watcher is a supervised background service, separate from download jobs.

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

Note. PR #43 (merged on 2026-10-01) implements this section for the current web application: the origin check,
the explicit Host allow-list (a wildcard bind does not start without one), the optional access token, the
security headers with a Content-Security-Policy, the two state-changing GET routes turned into POST, and the
startup warning.

## 10. Work packages and order

Wave 2 (running): data integrity, logs/diagnostics, job guards, doctor, blocked-state messages, CI.

Wave 3 — foundation (in order, because they share the same code):

1. Store layer + catalog (without changing behaviour: first the read paths move to the catalog).
2. Service layer: splitting `match_data_fetcher.py`; web routes and the CLI call services.
3. Default rate 5/s, default language, security fixes (small, parallel). Done: PR #33, PR #39, PR #43.

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
11. Live service: polling, then the `page` push source, then the opt-in `direct` source; sinks (stdout,
    file, webhook). Changed on 2026-10-01: the draft had "push listener + SSE + webhook"; there is no SSE.
12. Web UI update: first the screen design (approval), then the implementation. Changed on 2026-10-01: the
    web UI has no live view.

## 11. Open questions

- Is the push channel robust in production (this evening's test). Changed on 2026-10-01: answered for one
  evening (section 8). Still open: how often the push credential changes, whether the server refuses
  non-browser clients over time, whether `sport.{sport}` carries every event at quiet hours and for the other
  18 sports, and how a direct client behaves over hours and with several subjects
  (`03-implementation-plan.md` section 14).

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
| One writer lease, watchers separate (section 4) | The same two, named `writer` and `live` (`watcher:<sport>` until the live service exists), plus `maintenance` for clear, restore and rebuild and `sinks` for the sink dispatcher | `01-storage.md` 6.1 |
| Compression: candidate zstd, fallback gzip, chosen by measurement (section 12) | Measured: gzip level 6 (10.8× smaller than today; zstd-9 would be 6.5 % smaller and needs a dependency on Python 3.10–3.13) | `01-storage.md` 4.3 |
| `migrate` verifies, then deletes the old copy (section 12) | Two steps: `migrate` converts and verifies and keeps the old copy; `migrate --delete-legacy` removes verified old copies | `01-storage.md` 5.4 |
| "The Store reads both layouts" (section 12) | Also: an old-layout match that is written again (refresh, missing slice) is first copied to the new layout; the old folder is left untouched | `01-storage.md` 5.3 |
| Database target: "the catalog is already a queryable SQLite file" (section 7) | The catalog's schema is internal; the supported database output is the SQLite export in the public schema | `01-storage.md` 4.5, `02-services.md` 5.2 |
| Raw data: "SofaScore's response as it is" (section 5) | The stored payload is the parsed response written again: same values and key order, not the same bytes. This is what is stored today | `01-storage.md` 4.1 |
| Exit codes 0–6 (section 6) | The same, plus 130/143 for a one-shot command stopped by a signal | `02-services.md` 4.5 |
| One sequence of live events (sections 3, 8) | One sequence for four streams (live, change, job, system) | `01-storage.md` 2.3, `02-services.md` 5.1 |
| Push or polling (section 8) | `--source page\|direct\|poll`: polling can also be chosen alone, and the old `--watch` alias keeps polling so that it never starts a browser (decision D18, settled on 2026-10-02) | `02-services.md` 8.2, 8.4 |
| Security: basic headers, optional key (section 9) | Implemented by PR #43 with more than the draft asks: a session cookie next to the bearer token, a full Content-Security-Policy, tightened file modes | `03-implementation-plan.md` X-03, P20, P25 |
| Follow list in the configuration file (section 6) | Three origins of follows: the config file, the API/web UI, and the existing `leagues.txt` for installations without a config file | `02-services.md` 4.3 |
| Every setting can be overridden by an environment variable (section 6) | The same, with one distinction: the existing `.env` file, which the installers create and the web UI writes, is a layer of its own below the configuration file, so that its lines do not beat the file; variables of the process environment are above the file (decision D19, settled on 2026-10-02) | `02-services.md` 4.3 |
| The common schema as an outline (section 3) | A field-level contract, approved on 2026-10-02 (decision P2): live event types with their stream prefix, a Slice identified by owner and key, slice payloads raw in version 1, the aggregate for football only | `04-schema-v1.md` |
| Wave order (section 10) | Kept. Inside wave 3 the Store and the service layer are interleaved PR by PR because they share files; the plan gives the order | `03-implementation-plan.md` |
