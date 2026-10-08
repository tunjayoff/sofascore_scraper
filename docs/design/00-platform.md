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

Revised a fourth time, at the end of 2026-10-02, after batches eight to ten. No decision of the draft
changed. The state column of section 1 follows the merged work; the notes of sections 4, 6 and 7 say what
exists now, and section 8 has a note; section 13 has two more rows (a delete that the user asks for, and the
"finished only" filter of the counts and lists).

Revised a fifth time, on 2026-10-03, after batches eleven to nineteen (pull requests #95 to #133). One
decision of the draft changed, by the owner on 2026-10-02: the web UI is designed and built from scratch on
the same technology stack instead of as an update of the current views (section 1, "Interfaces"; section
10, item 12). The state column of section 1 follows the merged work; the notes of sections 3 to 8 and 10 say
what exists now; section 11 records that the live validation against the real site is done once, at the end
of the project; section 13 marks the row on a delete that the user asks for as settled (decision S18) and has
one more row (the detail slices of each sport).

Revised a sixth time, on 2026-10-06, after P27 to FX-19 and the newcomer pass of the web UI (pull requests
#134 to #161; checked at `b6caf2f`). No decision of the draft changed. The owner took three decisions after
it: the import package is renamed to `sofascore_scraper` (decision D1, 2026-10-03; plan item REN-1, when no
other branch is open); team, player and single-match follows work in 3.0.0, with search by name and their
matches downloaded (2026-10-06); and search suggests as the user types, like the site (2026-10-06). Two
technical choices were delegated: the odds country is recorded only when the user sets it (opt-in), and odds
history is pruned by a scheduler task that is off by default. The state column of section 1 follows the
merged work; the notes of sections 3, 5, 6 and 10 say what exists now; section 11 still has the live
validation at the end of the project, now with the team, player and single-match follows and the search;
section 13 has two more rows (where new follows are kept, and the package name).

Revised a seventh time, on 2026-10-07, after FX-20, FX-21, FX-22 and REN-1 (pull requests #164, #165, #167,
#168) and the rewrite of the user documents (#163, #166); checked at `6f79344`. No decision of the draft
changed. The package is now `sofascore_scraper` (REN-1), and paths in these documents use that name. The
owner took three decisions on 2026-10-07: the raw research files keep the push server's address as research
records (no masking, no history rewrite; the design documents and the READMEs stay without it); before the
live validation the orchestrator tests every feature end to end through the web UI against the real site,
in the owner's Chrome, at 1 request per second, with a few small leagues, teams and matches and without the
`direct` source; and the repository's description and topics were updated. The notes of sections 1, 3, 10
and 11 say what changed; section 13 has one more row (the published package).

Revised an eighth time, on 2026-10-08, after the end-to-end test against the real site and its three fix
items FX-24, FX-23 and FX-25 (pull requests #170 to #172); checked at `48e4c4c`. No decision of the draft
changed. The test ran on 2026-10-07 in the orchestrator's own headless browser (not the owner's Chrome) at 1
request per second; it found one wrong football score (an extra-time score for a match that went straight
to penalties), a false warning after a restore, a second process that could not use the browser while the
server held it, and gaps of the web UI, all fixed and re-tested on 2026-10-08. The owner decided on
2026-10-08: the coder session has ended and every remaining step, the live validation included, runs from
the orchestrator's session (the `direct` step still needs the owner's approval given there); the
validation runs that evening from 20:00 to 23:00 Turkish time; and 3.0.0 is not published to PyPI. The notes
of sections 10 and 11 and the installation row of section 13 say so.

Revised a ninth time, later on 2026-10-08, after the live validation against the real site, its fix items
FX-26 and FX-27, and FX-16 (pull requests #174 to #176); checked at `43ecdfc`. No decision of the draft
changed. The orchestrator ran the validation from its own session: the Docker image and, at the owner's
request, a pass over the web UI with one finished match of each of 20 sports in the morning, then live
matches of 13 sports, both push sources, odds, follows and the type-ahead in the evening. Both push sources
worked against the real site for the first time; the owner approved the `direct` step in that session, and
its five-minute run connected in 6 s and wrote no credential anywhere. The owner decided the three slice
proposals of #121 (applied by FX-16). The notes of sections 8, 10 and 11 and the rows "Live sources" of
section 1 and "Every data type is selectable" of section 13 say so.

## 1. Decisions (Tuncay, 2026-10-01)

| Topic | Decision | State on 2026-10-06 |
|---|---|---|
| Product position | A data foundation: products are built on it, it runs on servers as automation, applications and agents use it | |
| Sports | 21 sports (the current 3 + 13 that need score mapping + 5 with their own logic). Motor sports, cycling, bandy, water polo and beach volleyball are out of scope | done: all 21 are in the sport registry with their score mapping (PRs #112, #115, #118). Which detail data each sport requests rests on one match page per sport (PR #121); the live validation checks it on the real site (section 11) |
| Data types | Everything SofaScore shows without login, including betting odds. Every type is selectable; what is not selected is not fetched | done: the selection of data slices end to end, per follow, in the configuration and in the web UI (PRs #134, #161); odds (four slices, the provider id recorded, off by default) and the data of a season, team, player or sport (PR #140). The odds country is recorded only when the user sets `[client] odds_country` (delegated decision of 2026-10-06; PR #155). Which provider ids answer without login, and the shape of two odds slices that have only 404 samples, wait for the live validation (section 11) |
| History / live | History: by requests. Live: the push channel, with polling as the fallback. How the push channel is used was decided after the draft: see the two rows at the end of this table | measured, PR #42; built, PRs #95 and #101 (the last two rows) |
| Interfaces | The web UI is for people (a large update on the current base). The CLI is for servers and automation only; the menu-driven terminal UI is removed. Live watching belongs to the CLI only (row at the end of this table). Changed on 2026-10-02 (owner): the web UI is designed and built from scratch on the same technology stack (Vue 3, TypeScript, Pinia, the same build tooling), as a professional interface for the new platform, not as an incremental change of today's views; only what fits is carried over as code (the API client, the locale files, the eval-free build, the token handling). Added on 2026-10-06 (owner): team, player and single-match follows work in 3.0.0 (search by name, their matches downloaded), and search suggests as the user types, like the site | done for the CLI: the data commands, `serve` and the removal of the terminal menu (PRs #119, #125, #131). The new web UI is built (screen design PR #105, approved with all 22 of its decisions on 2026-10-02; implementation PRs #107, #132, #133). A first-time-user review on 2026-10-06 found it hard for a newcomer; a newcomer pass (PR #154), the backend gaps (PR #156: team, player and match follows that download, per-league delete, the connection state) and the last screens (PR #161) followed, and the old views under `/classic` were removed (PR #161). Search as the user types, one search across kinds and job names are built (PR #167, FX-20). The web UI needs Safari 16.4, Chrome 111 or Firefox 128 or newer (Tailwind CSS 4, PR #136) |
| Request rate | Default 5 requests per second; the user may take the risk and remove the limit | done, PR #33 |
| Storage | Raw JSON files stay + a rebuildable SQLite catalog | done: every download writes the new layout and updates the catalog in the same write (PRs #98, #104); every reader and the planning of downloads read the catalog (PRs #78 to #80, #89, #97); old data is read in place and converted only by `ssc migrate` (PR #110); backups contain the state database and the new layout (PR #109). The transition code is removed and the boundary tests allow no file access outside the Store except a documented list (PR #135) |
| Data format | A fixed, versioned common schema + raw data for those who want it | done: `04-schema-v1.md`, approved on 2026-10-02 (PR #72), is served by API v1 (PRs #122 to #127) and by the normalized exports (PR #130); raw payloads by the raw routes and the raw export (PRs #108, #123) |
| Match status | Every status is stored (fixture, live, finished, cancelled); filtering happens when reading | done, PR #129: the downloads store every status and `FETCH_ONLY_FINISHED` is a filter when the counts, the lists and the planning read (decision D21) |
| Language | Default English; Turkish when the system language is Turkish | done, PR #39 |
| Platform | Linux and Docker are official; Windows and macOS best-effort | the Docker image is built only when a release is tagged and was not yet built or smoke-tested: a step of the live validation before the release (section 11) |
| Network access | No account system. Local-only by default; exposing it is the responsibility of whoever installs it (see section 9) | done, PR #43 |
| Raw payloads | Stored compressed; the application and the CLI export them at full size (plain JSON) on request | done: compressed in the new layout (PR #104); exported by `ssc export` and API v1 (PRs #108, #123, #130) |
| Old data layout | No automatic migration. New writes use the new layout; old data is read in place; whoever wants to moves it with the `migrate` command (manager decision) | done: `ssc migrate` (PR #110) |
| Scheduler | In-app automatic updating is optional, off by default | done, PR #128: `ssc serve --scheduler` or `[schedule] enabled = true`. Since PR #155 a task that runs `every` some time counts from its last run in the job history, not from the start of the server, and a `prune-history` task (off by default) removes old odds snapshots |
| Version | These changes are 3.0.0 (manager decision): storage layout, API and CLI change incompatibly | the version number is still 2.0.0 (`pyproject.toml`; the web UI shows v2.0.0); raising it is a step of the release (`03-implementation-plan.md` section 18). The import package is `sofascore_scraper` since REN-1 (#168; decision D1, owner, 2026-10-03), and the Docker image has the `ssc` command |
| Merging | The manager session reviews and merges; the release tag is Tuncay's | since 2026-10-03 a pull request merges on a green Linux CI plus a local run of the full suite on its merge with main; the Windows and macOS jobs of CI are best-effort (Platform) |
| Live is not a web feature (2026-10-01, after the draft) | Live data is not part of the web UI and is not exposed over an HTTP endpoint (no SSE stream). A person at a screen can watch live scores on SofaScore itself; the value of the live stream is for servers and programs. Live watching is a CLI service (`watch`) that delivers events to sinks: stdout (JSON lines), file, webhook. A program that wants live data over the network uses the webhook. The event stream is still stored with sequence numbers | done: `ssc watch` delivers to the configured sinks (PR #91); no route of API v1 and no screen of the new web UI carries live data |
| Live sources (2026-10-01, after the draft) | Two selectable push sources in the CLI, plus polling as the fallback that is always present. `page` (default): the service keeps a real browser page open and listens to the page's own push connection; no credential is handled. `direct` (explicit opt-in): a lightweight client connects to the push server itself with the credential read at runtime from the bridge page's own connection, kept in memory only. `direct` is never the default, is never enabled implicitly, and carries clear warnings wherever it is configured or documented (section 8) | built: `page`, the default (PR #95), and `direct` (PR #101). Neither has been run against the real site yet; that happens in the live validation at the end of the project, whose `direct` step needs the owner's approval (section 11). Run on 2026-10-08 in the live validation: both worked (section 8) |

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
pages). And an Event carries an aggregate score only where its sport's mapping reads SofaScore's
`aggregated`: football from the start, and handball since PR #112 (SP-1). Version 1 gives the payload of a
slice raw; Odds and non-match data get their models with plan item P28.

Note (2026-10-06). P28 (PR #140) added the models `Odds`, `OddsMarket`, `OddsChoice`, `OddsLine` and
`StandingsRow`; API v1 and the exports use them. Since FX-21 (PR #164, 2026-10-07) they are part of the
generated contract, `Odds`, `OddsLine` and `StandingsRow` as records (`describe schemas` and the JSON Schema
show them; `04-schema-v1.md` section 4). The odds country is not a field of `Odds`; it is in the slice's
meta, and only when the user sets it.

Note (2026-10-03). The score mapping of all 21 sports exists (PRs #112, #115, #118): periods for the eight
period-based sports, sets for the five set-based ones, and an own family for each of the five class B sports
(`04-schema-v1.md`). Schema v1 is served by API v1 (PRs #122 to #127) and written by the normalized exports
in JSONL, CSV, Parquet and SQLite (PR #130).

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
(P11) the jobs of every process, web or command line, are in the state database. Since PRs #78, #79, #80
and #89 (RD-4, RD-5, RD-1, RD-2) the dashboard, the statistics, the match lists, the match detail and the
season lists read the catalog; "only finished matches" is a filter when they read (decision D21), and an
open of a data directory within a minute of the last full check skips the pass over the match folders
(decision S17, PR #90). The writer of the new layout (`v3/`, PR #82) and slice history (PR #92) exist.

Note (2026-10-03). Since batches eleven to nineteen every download writes the new layout: season lists and
schedule pages since PR #98, match details since PR #104, and the catalog is updated inside the write, so a
catalog that stays busy fails that match instead of falling behind. The planning of downloads, refreshes and
"missing details" reads the catalog (PR #97); every status is stored and "only finished matches" is a read
filter (PR #129). `ssc migrate` converts the old layout on request, verifies every copy and deletes old
copies only with `--delete-legacy --yes` (PR #110); `ssc catalog rebuild|verify|reconcile` check the
catalog; backups (format 2) contain `state.db`, the new layout and the change log, and a restore rebuilds
the catalog (PR #109). On the owner's data the same 423 matches take 6.6 MB in the new layout instead of 55
MB. A delete or a clear that the user asks for removes the copies of the old layout as well, which section
12 did not foresee: decision S18, settled on 2026-10-03 (section 13).

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

Note (2026-10-03). The data resources exist since PRs #122 to #127 (plan item P21): tournaments, seasons,
events with their slices and raw payloads, changes, follows, the job kinds for downloads, exports, backups,
clear, rebuild and a restore check, logs and diagnostics, and the `auth` routes; the old routes are adapters
over the same services in `sofascore_scraper/web/api/legacy.py`. Exports write the normalized datasets since PR #130
(SC-2). `events/{id}/odds` comes with odds (P28). The new web UI uses only `/api/v1`; a few routes it still
needs (a season-list job, a fetch of one match by its id alone, a real restore, some filters) are listed in
`05-web-ui.md` section 7 and planned as FX-13.

Note (2026-10-06). Those routes exist (PRs #152, #153): a season-list job, a fetch of matches by id, a real
restore, the filters, and more of `status`. Odds and non-match data have their resources (PR #140,
`events/{id}/odds/{key}`, `seasons/{id}/standings`). A follow added through the API or the web UI is kept in
the state database, team, player and match follows are downloaded, the tournament search finds teams and
players, one league's data can be deleted, `status` says whether SofaScore was ever reached, and export
files have readable names (PR #156). The web UI uses all of it except a few fields (`05-web-ui.md` section
7).

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
web application and can be stopped from another process. Since PR #91 (P23) there is `watch` as well
(below).

Note (2026-10-03). The command line is complete for 3.0.0: `sync`, `fetch event|tournament`, `refresh`,
`status`, `follows`, `jobs`, `data clear|recheck-unavailable` and the signals and single-instance rules (PR
#119), `serve` (PR #125, with the optional scheduler of PR #128), `migrate` and `catalog` (PR #110),
`backup` (PR #109) and `export` (PR #130), with the exit codes above (and 130 for Ctrl+C). `python main.py`
is a thin shim: its old flags map onto these commands for one more release, and without an action it prints
a short help and exits with 2, because the terminal menu is gone (PR #131). The selection of data slices
in the configuration file is wired end to end by P27 (in progress).

Note (2026-10-06). The selection of data slices is wired (PR #134). `ssc sync` reads the follows of the
state database (PR #152), downloads team, player and match follows and takes `--follow KIND:ID` and `--only
seasons` (PRs #152, #156); `ssc status --disk` and `ssc config validate` with the scheduler's tasks exist
(PR #152); `ssc doctor` checks the configuration file (PR #155). New follows are always kept in the state
database; `config/leagues.txt` is read as a legacy list, and a row of it is moved into the database through
the API (PR #156).

## 7. Output targets (pluggable)

- **stdout**: JSON line by line (the calling program or agent reads it directly).
- **file**: the current layout + exports in fixed formats.
- **database**: the catalog is already a queryable SQLite file; a PostgreSQL target later.
- **webhook**: live events and job notifications; signed body, retries, safe to repeat thanks to the sequence
  number. Changed on 2026-10-01: this is the way live data reaches a program on another machine; there is no
  HTTP endpoint to pull or stream it from.
- **message queue**: later, a new target on the same interface.

Note. The stdout, file and webhook targets exist as a library since PR #73 (plan item P22). Since PR #91
(P23) `watch` hosts them. The file target in fixed formats is `ssc export` (PR #130): JSONL, CSV, Parquet
and SQLite in the public schema. Sink cursors and their lag are readable from the Store (PR #109) and shown
by `ssc status` and `GET /api/v1/sinks` (PRs #119, #122).

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

Note. `ssc watch` exists since PR #91 (plan item P23), with polling as its only source: `--source page` and
`--source direct` fall back to polling with a warning until P24 and P31. It is one foreground process for
every sport, takes the live lease, writes the events into the state database with sequence numbers and a
key that stores a transition once, delivers them to the configured sinks, and pauses with back-off when
SofaScore blocks requests. The fields of the live events are settled (`04-schema-v1.md`, LiveEvent).

Note (2026-10-03). Both push sources exist. `page` (PR #95) is the default: one browser page per watched
sport in its own profile, listening to the page's push connection; while push is healthy, polling runs
every 120 s as a safety net, and at the poll interval when the connection is silent or dropped; every switch
is written to the event log as `system.live_source_changed`. `direct` (PR #101) is an explicit opt-in with
the four warnings above, printed by `--help`, `describe`, `config validate`, `config show`, the READMEs and
the log at every start; it reads the credential with its own short-lived browser in the live profile, does
not connect at all when a proxy is configured, and counts as unhealthy after five failures in a row, after
which the service polls. Neither source has been run against the real site: the live validation at the end
of the project does that once, in a busy match window, and its `direct` step needs the owner's approval
(section 11). The old `--watch` runs the live service with `--source poll` since PR #119 and no longer
writes `watch_events.jsonl`.

Note (2026-10-08). The live validation ran both sources against the real site. `page`, next to a running
`ssc serve`, opened its live pages in about 40 s and its push connections in about 2 minutes, then delivered
real score and status changes (handball, basketball, volleyball, minifootball). `direct`, approved by the
owner in the validation's session for one run of five minutes, connected in 6 s with 13 subjects, delivered
score changes while polling filled in the rest, printed its warnings as designed, and left no credential in
any output or log. FX-27 (PR #175) fixed what the run showed: `ssc watch --sport` now limits the followed
sports it watches, and a closing browser no longer leaves pending request handlers. For set sports a score
change is a set won, not a game or a point (as designed; `ssc watch --help` says so).

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
    web UI has no live view. Changed on 2026-10-02 (owner): the web UI is designed and built from scratch on
    the same stack, not as an update of the current views. The design (`05-web-ui.md`) was approved with all
    22 of its decisions on 2026-10-02, and the implementation was split in two parts (FE-2a, FE-2b).

Note (2026-10-03). Waves 3 to 6 are merged except four items: the selection of data slices (P27, in
progress), odds and non-match data (P28), the removal of the transition code (ST-28, in progress) and the
optional rename of the package (REN-1). `03-implementation-plan.md` section 18 lists what remains before the
3.0.0 release.

Note (2026-10-06). Waves 3 to 6 are merged (PRs #134, #135, #140), with the fix items of the batches and a
second pass over the web UI after the first-time-user review (PRs #154, #156, #161). Left before 3.0.0: the
search as the user types (FX-20, in progress), the schema follow-up of P28 (FX-21), the rename of the
package (REN-1, decided by the owner), the live validation, the detail slices of the three main sports
after it (FX-16) and the release steps; `03-implementation-plan.md` section 18 lists them.

Note (2026-10-07). FX-20 (#167), FX-21 (#164) and REN-1 (#168) are merged, and FX-22 (#165), a fix item from
the audit of the user documents. Left before 3.0.0: the orchestrator's end-to-end test through the web UI
and its fixes, the live validation, FX-16, the decision on a published package, the Docker build and smoke
test, real screenshots in the README, the version bump, the changelog close and the tag
(`03-implementation-plan.md` section 18). P30 comes in 3.1.

Note (2026-10-08). The end-to-end test is done and its fixes are merged: FX-24 (#170, the web UI), FX-23
(#171: the football extra-time score, exports during a download, searches ahead of downloads in the request
budget, a temporary browser profile for a second process, English logs) and FX-25 (#172: the Store's
messages in English and small points of the re-test). Left before 3.0.0: the live validation (Talimat 07)
on the evening of 2026-10-08, FX-16, the Docker build and smoke test, real screenshots in the README, the
version bump, the changelog close and the tag (`03-implementation-plan.md` section 18). 3.0.0 is released
as the tag, the GitHub Release and the Docker image, without a PyPI package (owner, 2026-10-08).

Note (2026-10-08, evening). The live validation is done, the Docker image of `137cabe` passed its smoke
test, and its fixes are merged: FX-26 (#174: the score of every sport on the match page, coverage over
finished matches, one suggestion list in SofaScore's order and other points of the web UI) and FX-27 (#175:
`ssc watch --sport`, live codes, the no-data answer of an unfinished match, the WTA rankings); FX-16 (#176)
applied the owner's decision on the slices of football, basketball and tennis. FX-28 (three small UI
points) is in progress. Left before 3.0.0: the release pull request (the version, the changelog close, the
README with screenshots of real data), the Docker smoke test on its commit and the tag
(`03-implementation-plan.md` section 18). P30 comes in 3.1.

## 11. Open questions

- Is the push channel robust in production (this evening's test). Changed on 2026-10-01: answered for one
  evening (section 8). Still open: how often the push credential changes, whether the server refuses
  non-browser clients over time, whether `sport.{sport}` carries every event at quiet hours and for the other
  18 sports, and how a direct client behaves over hours and with several subjects
  (`03-implementation-plan.md` section 14).
- Changed on 2026-10-03 (owner): the live validation against the real site is postponed until the project is
  complete and is done once, in a busy match window, by the coder session. It covers the detail data each
  sport offers (one finished and one live match page per sport), the live status codes of the new sports,
  the ice-hockey shoot-out field, the `page` and `direct` push sources, an end-to-end run of `sync`,
  `export`, `backup` and `serve`, and the Docker image. Its `direct` step needs the owner's approval in that
  session. A scheduled watch run planned for Saturday 2026-10-03 was cancelled.
- Changed on 2026-10-06: the validation is still done once, at the end. It now also covers the odds and
  non-match data (which provider ids answer without login; the slices known only from 404 samples or from
  the catalog), the team, player and single-match follows (SofaScore's general search, the team and player
  match lists, their page sizes and limits), the connection state on the browser path, and the search as
  the user types with short prefixes. `03-implementation-plan.md` section 18 has the full list.
- Changed on 2026-10-07 (owner): before the live validation the orchestrator tests every feature end to end
  through the web UI against the real site, in the owner's Chrome, with a budget of 1 request per second
  and a few small leagues, teams and matches; the `direct` source is excluded from it. What it finds
  becomes fix items before the validation. The validation itself also checks the type-ahead of FX-20:
  `/search/all` with 2-letter prefixes, the real number of requests while typing, and the cancel of a
  search behind a reverse proxy.
- Changed on 2026-10-08 (owner): the end-to-end test is done. It ran in the orchestrator's own headless
  browser, not in the owner's Chrome; its findings are fixed (FX-23 to FX-25), and it answered a 2-letter
  prefix of the type-ahead ("sü" gave leagues and teams). The coder session has ended: the orchestrator
  runs the live validation from its own session, on 2026-10-08 from 20:00 to 23:00 Turkish time, and the
  owner gives the approval of its `direct` step there. Added to the validation: the event flow of the `page`
  source in a busy window, also next to a running server (the test saw no event in 5 minutes on a quiet
  match).
- Changed on 2026-10-08, evening: the live validation is done (`03-implementation-plan.md` sections 14 and
  18). Answered: both push sources work against the real site, also next to a running server; the live
  status codes of 13 sports; which odds providers answer without login (1 and 5); the shapes of the odds
  and non-match answers that only 404 samples had shown; team, player and league follows on real data; the
  type-ahead's 2-letter prefixes. Not answered: the research tool that records a page's requests is broken,
  so the per-sport evidence of #121 was not regenerated (FX-16 decided from the app's own downloads); the
  slices before kick-off, the ice-hockey shoot-out and other rare codes, how long the push credential lives,
  and the cancel of a search behind a reverse proxy remain open, none needed for the release.

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
| Follow list in the configuration file (section 6) | Since PR #156 a follow added through the API or the web UI is always kept in the state database, with or without a config file; `leagues.txt` is a read-only legacy list whose rows can be moved into the database one by one | `02-services.md` 2.7 |
| Every setting can be overridden by an environment variable (section 6) | The same, with one distinction: the existing `.env` file, which the installers create and the web UI writes, is a layer of its own below the configuration file, so that its lines do not beat the file; variables of the process environment are above the file (decision D19, settled on 2026-10-02) | `02-services.md` 4.3 |
| The common schema as an outline (section 3) | A field-level contract, approved on 2026-10-02 (decision P2): live event types with their stream prefix, a Slice identified by owner and key, slice payloads raw in version 1, the aggregate for football only | `04-schema-v1.md` |
| The old layout is never changed outside `migrate` (sections 1 and 12) | Writes, promotion, rebuild and repair never delete old data; a delete or a clear that the user asks for removes every copy of what it names, the old layout's included, because a copy left behind would bring the data back, and says so in its help text (decision S18, settled on 2026-10-03) | `01-storage.md` 0 and 2.3 |
| `FETCH_ONLY_FINISHED` becomes a read filter (section 4) | The same for the counts and the match lists, with one rule: with the setting on, a match counts when it is finished or its details were downloaded (decision D21, settled on 2026-10-02) | `02-services.md` 2.7 |
| Every data type is selectable (section 1) | The registry also says which detail slices a sport offers at all: a slice SofaScore does not have for a sport is not requested, and one it has only sometimes does not count against completeness (PR #121, from one match page per sport). Three proposals for football, basketball and tennis wait for the live validation; the owner decided them on 2026-10-08, and FX-16 (PR #176) applied them: `pregame_form` does not count in the three, tennis requests no `lineups` or `incidents`, and tennis `point_by_point` counts | `docs/all-sports/README.md`, `02-services.md` 3.1 |
| Wave order (section 10) | Kept. Inside wave 3 the Store and the service layer are interleaved PR by PR because they share files; the plan gives the order | `03-implementation-plan.md` |
| The package name (not in the draft) | The import package is renamed from `src` to `sofascore_scraper` (decision D1, the owner's, 2026-10-03), in one pull request when no other branch is open; done by REN-1 (#168), with no `src` alias | `03-implementation-plan.md` REN-1 |
| Installation (not in the draft) | Only a source checkout with `pip install -e .` and the Docker image (which installs the same way) are supported: the wheel lacks the Store's `.sql` files, the locales and the web build, and the version is read from `pyproject.toml` next to the package. Decided by the owner on 2026-10-08: 3.0.0 is not published to PyPI; the release is the git tag, the GitHub Release and the Docker image, and the README tells users to install from source. Publishing to PyPI comes after 3.0.0 | `03-implementation-plan.md` sections 14, 18 |
