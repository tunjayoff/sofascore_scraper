# 04 — Normalized schema v1 (field-level contract)

Status: **approved on 2026-10-02** (decision P2 in `03-implementation-plan.md`, section 13: the normalized
schema is the public contract, and its field-level document is approved before the code merges). The owner
delegated the approval to the orchestrator, who approved version 1 as written. Plan item SC-1 (PR #72).
API v1 (P21) and the exports (SC-2) will serve these records. `ssc events` and the sinks (P22, PR #73)
already write the LiveEvent envelope, and the live service (P23, PR #91) settled the content of its `data`
(section "LiveEvent data").

This document can be read on its own. It defines every record the platform gives out, field by field: type,
unit, whether it can be null, where the value comes from in SofaScore's payload, and what it means. Section 9
lists every choice that had to be made while writing it, each with the alternative that was not chosen. All
28 were approved as written on 2026-10-02 and are decisions now.

The requirement is `00-platform.md`, section 3. Where this document is more specific than that section, this
document is the contract. `schema_version` is **1**. The schema's id is `sofascore.data/1`; the envelope of
stream events keeps the id `sofascore.event/1` that `02-services.md` 5.1 gave it.

The field tables below are generated from the models in `src/schema/models.py`, and a test fails when a table
and the code differ (`tests/test_schema_v1.py`). The same models produce the JSON Schema that
`ssc describe schemas` will print. So the tables, the JSON Schema and the code cannot drift apart.

Revised on 2026-10-06 (the sixth revision of the design documents, checked against `origin/main` at
`b6caf2f`). P28 (#140) added the models of odds and standings, `Odds`, `OddsMarket`, `OddsChoice`, `OddsLine`
and `StandingsRow` (`src/schema/models.py:708-829`). They are in `models.PENDING_MODELS`, not in `MODELS` and
`RECORDS`: API v1 and the exports use them, but `ssc describe schemas` and the JSON Schema golden do not show
them yet. Section 4, "Odds and standings", describes them with field tables copied from the models; plan item
FX-21 moves them into `MODELS` (and `Odds`, `OddsLine` and `StandingsRow` into `RECORDS`), makes those tables
generated blocks and regenerates `tests/golden/schema/`. FX-15 (#155) added the odds country as an opt-in
setting; it is recorded in the slice's meta, not in the `Odds` record.

## 1. What the schema is, and what it is not

The platform stores SofaScore's responses as they are (one file per response, see `01-storage.md`). The
normalized schema is a second, stable view of the same data:

- **Normalized records** have fixed field names, fixed types and fixed meanings that do not change when
  SofaScore renames or moves something. This document defines them.
- **Raw payloads** are SofaScore's responses, unchanged. They are given on request (section 7). Nothing in
  this document constrains their content.

Records of version 1:

| Record | What it is | Given out by |
|---|---|---|
| [Sport](#sport) | a sport and its score family | `/sports`, `ssc describe sports` |
| [Category](#category) | a country, region or tour that groups tournaments | with tournaments |
| [Tournament](#tournament) | a competition (SofaScore's unique tournament) | `/tournaments` |
| [Season](#season) | one edition of a tournament | `/tournaments/{id}/seasons`, `/seasons/{id}` |
| [Participant](#participant) | a team, a single player or a pair | with events |
| [Event](#event) | a match: status, score, winner, quality | `/events`, export dataset `events` |
| [Slice](#slice) | one stored response about an event (statistics, lineups, …) and its state | `/events/{id}/slices`, export dataset `slices` |
| [Change](#change) | a change of an already stored event that a later read found | `/changes`, export dataset `changes` |
| [LiveEvent](#liveevent) | an event of a stream (live, change, job, system) | `ssc events`, `watch --stdout`, the sinks; no HTTP route |

Not part of version 1:

- **Odds** and **non-match data** (standings, season statistics, squads, rankings). Plan item P28 adds their
  models. Until then their payloads are slices like any other and are available raw.
  P28 (#140) added the models `Odds`, `OddsMarket`, `OddsChoice`, `OddsLine` and `StandingsRow` (section 4,
  "Odds and standings"); they are not in version 1's `MODELS` yet (FX-21). The other non-match slices (season
  info, cup trees, top players and teams, season odds, rankings, player statistics) stay raw slices.
- **Normalized content of slices.** A slice's payload (statistics, lineups, incidents, head-to-head, form,
  streaks, point-by-point) is SofaScore's response, unchanged. Version 1 normalizes the state of a slice, not
  its content (section 9, point 16).
- **Score families of the sports that are not supported yet.** The 13 class A and 5 class B sports get their
  score mapping with plan items SP-1 to SP-3. An event of such a sport is already a valid record: it carries
  the headline score and `score.family` is null.
- **Column names of tabular exports** (CSV, Parquet, SQLite). Plan item SC-2 defines how a record is
  flattened (section 9, point 23).

## 2. Conventions

These hold for every record.

1. **Names** are lower snake case. A record is a JSON object.
2. **Every field is always present.** A field whose value is unknown or does not apply is `null`. It is never
   left out and never an empty string. A list is never `null`; it is empty.
3. **An object whose members would all be null is `null`** where the table says the field can be null
   (`stage`, `round`, `aggregate`, a side of `participants`, a score pair). `status`, `participants`, `score`
   and `quality` are always objects.
4. **Ids** are SofaScore's integers, unchanged. An id is only unique within its kind: an event id, a
   tournament id and a participant id can be the same number.
5. **Times** are strings in ISO 8601, in UTC, with the suffix `Z` and whole seconds:
   `2026-09-15T18:30:00Z`. Every such field ends in `_utc`. Two exceptions:
   - `change_ts` (and `old_change_ts`, `new_change_ts`) is SofaScore's own change time and is carried as the
     integer it is, in **epoch seconds**. It is a version stamp: compare it for equality or order.
   - `LiveEvent.ts` has milliseconds: `2026-10-01T12:00:00.123Z`.
6. **Durations** are in seconds.
7. **Sides.** `home` is the first-named side of SofaScore (`homeTeam`), `away` the second (`awayTeam`), in
   every sport, also where the words make no sense (tennis). A value of both sides is an object
   `{"home": …, "away": …}`.
8. **Enumerations** are lower snake case strings. Each is either closed or open:
   - **closed**: the listed values are all there is. A new value needs a new schema version. Closed are:
     `status.class`, `winner`, `aggregate.winner`, `quality.source`, `quality.settlement`, `Slice.state`,
     `Participant.type`, and the `family` of each score structure.
   - **open**: new values can appear at any time without a new version; a consumer must tolerate a value it
     does not know. The tables mark them "open set". Open are: `status.type` (SofaScore's own),
     `Sport.score_family`, `PeriodsScore.format`, `Participant.gender`, `Slice.owner_kind`, `Slice.key`,
     `SliceError.reason`, and `stream`, `type` and `source` of a LiveEvent.
9. **Text** is in English, as SofaScore gives it. Translations of names are not part of the schema.
10. **Scores** are integers. A value is taken from one SofaScore field and is not rounded or computed from
    others, except where its source says "derived".

## 3. Versioning

`schema_version` is one integer for the whole schema. It is 1.

Free, without a new version:

- adding a field to a record;
- adding a record type;
- adding a score family, with its own structure (a new value of `score.family`);
- adding a value to an open enumeration;
- a field that was always null starting to carry values.

A new version (2) is required for:

- removing or renaming a field;
- changing a field's type, unit or meaning;
- making a field that could not be null nullable;
- adding a value to a closed enumeration, or changing what a value means;
- changing which SofaScore field a normalized field is read from, when that changes its values.

Consequences for consumers: ignore fields you do not know; do not fail on an unknown value of an open
enumeration; treat an event whose `score.family` you do not know by its headline score (`score.home`,
`score.away`), which every family has.

Where the version is stated: the JSON Schema document carries it (`x-schema-version`), and `ssc describe
schemas` and `ssc version` will print it once they are wired to this package. A record does not carry
`schema_version` itself; the container that delivers records does (the API's metadata, an export's manifest;
section 9, point 19). A stream event does not carry a version either: the id `sofascore.event/1` names the
shape of its envelope, and a webhook body names its own shape (`sofascore.webhook/1`, `02-services.md` 5.3).

When version 2 exists, version 1 keeps being served next to it for at least one release (section 9, point
19).

## 4. Entities

How the records refer to each other:

```
Sport ──< Category ──< Tournament ──< Season
                           │             │
                           └──────< Event >──────┐
                                     │  │        │
                     Participant >───┘  │        ├──< Slice
                     (home, away)       │        └──< Change
                                        └──< LiveEvent (by event_id)
```

An Event holds ids (`tournament_id`, `season_id`, `category_id`, the ids of its two sides) and, for the two
sides, the name as well. The full Tournament, Season, Category and Participant records are fetched or exported
separately and joined by id (section 9, point 1).

How to read the tables. "Source" is a path in SofaScore's event object, unless it says otherwise. The event
object is what the `/event/{id}` response holds under `event`, and what a schedule page lists under `events`;
both have the same fields. A path written for `homeTeam` or `homeScore` holds for `awayTeam` and `awayScore`
in the same way. "Derived" means the platform computes the value. "Null" says whether the field can be null.
"Unit" is empty where a value has none.

### Sport

A sport. The supported sports come from the platform's sport registry; today they are football, basketball
and tennis.

<!-- fields:Sport -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `slug` | string | no |  | `tournament.category.sport.slug` | SofaScore's slug of the sport, lower case. The key of a sport everywhere in the schema. |
| `name` | string | yes |  | `tournament.category.sport.name` | English name of the sport. |
| `id` | integer | yes |  | `tournament.category.sport.id` | SofaScore's numeric id of the sport; null when no stored payload has shown it. |
| `score_family` | string, open set: `football`, `periods`, `sets`, `innings`, `cricket`, `fight` | yes |  | sport registry (`src/sports.py`) | Which score structure events of this sport carry (see Score). Null for a sport the platform has no score mapping for. |
<!-- /fields:Sport -->

```json example:Sport
{"slug": "football", "name": "Football", "id": 1, "score_family": "football"}
```

### Category

A country or region, or a tour such as ATP, that groups tournaments. In football and basketball a category is
usually a country and has a `country_code`; in tennis it is a tour (ATP, WTA, Challenger, ITF) and has none.

<!-- fields:Category -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `id` | integer | no |  | `tournament.category.id` | SofaScore's category id. |
| `sport` | string | yes |  | `tournament.category.sport.slug` | Slug of the sport the category belongs to. |
| `name` | string | yes |  | `tournament.category.name` | Name of the category, in English. |
| `slug` | string | yes |  | `tournament.category.slug` | SofaScore's slug of the category. |
| `country_code` | string | yes |  | `tournament.category.alpha2` | SofaScore's two-letter code of the category's country, as given (mostly ISO 3166-1 alpha-2; SofaScore uses `EN` for England). Null for a category that is not a country, such as ATP. |
<!-- /fields:Category -->

```json example:Category
{"id": 1, "sport": "football", "name": "England", "slug": "england", "country_code": "EN"}
```

### Tournament

A competition. This is SofaScore's *unique tournament* (Premier League, Wimbledon Men), the thing a user
follows. SofaScore also has a non-unique "tournament" object for a part of a competition (a group, a
qualifying draw); the schema calls that a [Stage](#stage) and gives it with the event.

<!-- fields:Tournament -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `id` | integer | no |  | `tournament.uniqueTournament.id` | SofaScore's unique-tournament id. |
| `sport` | string | yes |  | `tournament.uniqueTournament.category.sport.slug`, else `tournament.category.sport.slug` | Slug of the sport. |
| `category_id` | integer | yes |  | `tournament.uniqueTournament.category.id`, else `tournament.category.id` | Id of the tournament's Category. |
| `name` | string | yes |  | `tournament.uniqueTournament.name` | Name of the tournament, in English. |
| `slug` | string | yes |  | `tournament.uniqueTournament.slug` | SofaScore's slug of the tournament. |
<!-- /fields:Tournament -->

```json example:Tournament
{"id": 21, "sport": "football", "category_id": 1, "name": "EFL Cup", "slug": "efl-cup"}
```

### Season

One edition of a tournament.

<!-- fields:Season -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `id` | integer | no |  | `season.id`; `seasons[].id` of the season list | SofaScore's season id. |
| `tournament_id` | integer | no |  | `tournament.uniqueTournament.id` of the event that names the season, or the tournament whose season list holds it | Id of the Tournament the season belongs to. |
| `name` | string | yes |  | `season.name`; `seasons[].name` | Name of the season, for example `Premier League 26/27`. |
| `year` | string | yes |  | `season.year`; `seasons[].year` | The season's year text as SofaScore writes it: `26/27`, `2025`. |
<!-- /fields:Season -->

```json example:Season
{"id": 96185, "tournament_id": 21, "name": "EFL Cup 26/27", "year": "26/27"}
```

### Participant

A competitor of an event. SofaScore uses one kind of object, `team`, for a club, a national team, a single
player and a doubles pair; `type` says which it is.

<!-- fields:Participant -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `id` | integer | no |  | `homeTeam.id`, `awayTeam.id` | SofaScore's id of the competitor (the id space of `homeTeam` / `awayTeam`: teams, single players and pairs share it; it is not the person id of a squad player). |
| `sport` | string | yes |  | `homeTeam.sport.slug`, else the sport of the event | Slug of the sport. |
| `type` | string, one of `team`, `player`, `pair`, `other` | yes |  | `homeTeam.type`: 0 is `team`, 1 is `player`, 2 is `pair` | What the competitor is: `team`, `player` (one person, as in tennis singles), `pair` (two persons, as in tennis doubles) or `other` (a type code the platform does not know). Null when SofaScore gave no type. |
| `name` | string | yes |  | `homeTeam.name` | Name, in English. |
| `short_name` | string | yes |  | `homeTeam.shortName` | Short name. |
| `slug` | string | yes |  | `homeTeam.slug` | SofaScore's slug. |
| `name_code` | string | yes |  | `homeTeam.nameCode` | Three-letter code, for example `ARS`. |
| `country_code` | string | yes |  | `homeTeam.country.alpha2` | SofaScore's two-letter country code, as given. |
| `gender` | string, open set: `M`, `F` | yes |  | `homeTeam.gender` | Gender as SofaScore gives it. |
| `national` | boolean | yes |  | `homeTeam.national` | True for a national team. |
<!-- /fields:Participant -->

`type` by sport, as seen in the stored samples (154 events of three sports):

| SofaScore `type` | `type` | Seen in |
|---|---|---|
| 0 | `team` | football and basketball, every event |
| 1 | `player` | tennis singles |
| 2 | `pair` | tennis doubles; the payload names the two persons in `subTeams` |
| any other number | `other` | not seen |

The two persons of a pair are not part of version 1 (section 9, point 13).

```json example:Participant
{"id": 54, "sport": "football", "type": "team", "name": "Peterborough United", "short_name": "Peterborough Utd",
 "slug": "peterborough-united", "name_code": "PET", "country_code": "EN", "gender": "M", "national": false}
```

### Event

A match. One record per SofaScore event id.

<!-- fields:Event -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `id` | integer | no |  | `id` | SofaScore's event id. |
| `sport` | string | yes |  | `tournament.category.sport.slug` | Slug of the sport. Null when no stored payload says it. |
| `category_id` | integer | yes |  | `tournament.category.id` | Id of the Category. |
| `tournament_id` | integer | yes |  | `tournament.uniqueTournament.id` | Id of the Tournament. Null for an event without a unique tournament. |
| `season_id` | integer | yes |  | `season.id` | Id of the Season. |
| `stage` | [Stage](#stage) | yes |  | `tournament` | The part of the tournament. Null when neither id nor name is known. |
| `round` | [Round](#round) | yes |  | `roundInfo` | The round. Null when the event has no round information. |
| `start_utc` | string | yes | ISO 8601 UTC | `startTimestamp` | Scheduled start. For tennis this is the planned time, not the first point. |
| `status` | [Status](#status) | no |  | `status` | Status: SofaScore's triple and the platform's class. |
| `participants` | [EventParticipants](#eventparticipants) | no |  | `homeTeam`, `awayTeam` | The two sides. |
| `score` | [FootballScore](#footballscore) or [PeriodsScore](#periodsscore) or [SetsScore](#setsscore) or [InningsScore](#inningsscore) or [CricketScore](#cricketscore) or [FightScore](#fightscore) or [PlainScore](#plainscore) | no |  | `homeScore`, `awayScore` | The score, in the structure of the sport's score family. |
| `winner` | string, one of `home`, `away`, `draw` | yes |  | `winnerCode`: 1 is `home`, 2 is `away`, 3 is `draw` | Who won. Null while undecided and when SofaScore names no winner. |
| `aggregate` | [Aggregate](#aggregate) | yes |  | `homeScore.aggregated`, `awayScore.aggregated`, `aggregatedWinnerCode` | Aggregate of a two-legged tie. Null for every other event. |
| `slug` | string | yes |  | `slug` | SofaScore's slug of the event. |
| `custom_id` | string | yes |  | `customId` | SofaScore's short id of the pairing, used in its page addresses. |
| `quality` | [Quality](#quality) | no |  | derived | Provenance and reliability of the record. |
<!-- /fields:Event -->

```json example:Event
{"id": 16950622, "sport": "football", "category_id": 1, "tournament_id": 21, "season_id": 96185,
 "stage": {"id": 17, "name": "EFL Cup"},
 "round": {"number": 3, "name": "Round 3", "slug": "round-3"},
 "start_utc": "2026-09-15T18:30:00Z",
 "status": {"type": "finished", "code": 120, "description": "AP", "class": "completed"},
 "participants": {"home": {"id": 54, "name": "Peterborough United"}, "away": {"id": 23, "name": "Barnsley"}},
 "score": {"family": "football", "home": 3, "away": 3,
           "half_time": {"home": 2, "away": 2}, "regulation": {"home": 3, "away": 3},
           "after_extra_time": {"home": 3, "away": 3}, "penalties": {"home": 7, "away": 6}},
 "winner": "home", "aggregate": null, "slug": "peterborough-united-barnsley", "custom_id": "yseb",
 "quality": {"source": "event", "observed_at_utc": "2026-09-29T15:52:47Z", "change_ts": 1789504592,
             "settlement": "final", "provisional": false, "tier_hint": true, "stale": false,
             "status_regressed": false}}
```

The parts of an Event follow.

#### Status

<!-- fields:Status -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `type` | string, open set: `notstarted`, `inprogress`, `finished`, `postponed`, `canceled`, `interrupted`, `suspended`, `willcontinue` | yes |  | `status.type` | SofaScore's status type. |
| `code` | integer | yes |  | `status.code` | SofaScore's status code, for example 100 (ended), 110 (after extra time), 120 (after penalties), 91 (walkover), 92 (retired). |
| `description` | string | yes |  | `status.description` | SofaScore's status text, in English, for example `Ended`, `2nd half`. |
| `class` | string, one of `not_started`, `live`, `completed`, `decided_without_play`, `void`, `unknown` | no |  | derived from `status.type`, then `status.code`, then `status.description` (`src/status.py`) | The platform's class of the status. `not_started`: not begun. `live`: in progress, breaks included (half time, the night between two days of a cricket match: type `willcontinue`). `completed`: played and finished. `decided_without_play`: finished by walkover or retirement. `void`: postponed, cancelled, interrupted, suspended or abandoned. `unknown`: none of these; never silently treated as completed. |
<!-- /fields:Status -->

`class` is decided in this order: by `type`; for `type` `finished` by `code`; without a `type` by `code`;
without both by `description`.

| `status.type` | `status.code` | `class` |
|---|---|---|
| `notstarted` | 0 | `not_started` |
| `inprogress` | any (6–16, 20, 21, 28–31, 1001, 1002 seen) | `live` |
| `willcontinue` | 141 (end of a day of a multi-day cricket match) | `live` |
| `finished` | 100 (ended), 110 (after extra time or overtime), 120 (after penalties) | `completed` |
| `finished` | 91 (walkover), 92 (retired) | `decided_without_play` |
| `finished` | any other code | `unknown` |
| `postponed`, `canceled`, `interrupted`, `suspended` | any (60, 70, 80, 81, 90 seen) | `void` |
| any other type | | `unknown` |

`void` means "there is no result to use now", not "this will never be played": a postponed or suspended match
keeps its id and can move to `not_started` or `live` again. `decided_without_play` has a `winner` but its
score is not the score of a played match; a retired tennis match keeps the games played until then.

Codes of in-progress matches differ by sport: football 6 (first half), 7 (second half), 31 (half-time);
basketball 13 to 16 (quarters), 30 (pause); tennis 8 to 10 (sets); 20 (started) in football and tennis when
SofaScore has no period information. They are given as they are in `code` and `description`; the class is
`live` for all of them.

The sports added by SP-1 to SP-3 (PRs #112, #115, #118) add, as recorded: 11 and 12 (4th and 5th set, table
tennis), 21 (1st inning, cricket), 28 and 29 (8th and 9th inning, baseball), 1001 and 1002 (first and second
game, e-sports), 20 (started) also in darts and snooker, and 30 (pause) also in badminton and e-sports.
These codes are `live` also without a type. Only codes that were seen were added: the inning codes 22 to 27,
other cricket breaks (stumps, lunch, tea, an innings break), the live codes of MMA and of the eight period
sports of SP-1, and e-sports codes beyond 1002 were never recorded, and a code that is not known is
`unknown` without a type. Cricket's `willcontinue` (code 141, "End of day 1") is `live`, like half time:
the match goes on the next day. `status.class` is a closed set (section 2, rule 8), so SP-3 chose an existing
class over a new one such as `paused`, which would need version 2; `void` would have been wrong, because a
result is still to come. Such a match stays `settlement: open`, and the watcher keeps it until cricket's
stuck threshold of 6 days.

#### EventParticipants

<!-- fields:EventParticipants -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `home` | [EventParticipant](#eventparticipant) | yes |  | `homeTeam` | The home side (the first-named side). Null when neither its id nor its name is known. |
| `away` | [EventParticipant](#eventparticipant) | yes |  | `awayTeam` | The away side (the second-named side). Null when neither its id nor its name is known. |
<!-- /fields:EventParticipants -->

#### EventParticipant

<!-- fields:EventParticipant -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `id` | integer | yes |  | `homeTeam.id` / `awayTeam.id` | Id of the Participant. |
| `name` | string | yes |  | `homeTeam.name` / `awayTeam.name` | Name of the participant at the time the event was read. |
<!-- /fields:EventParticipant -->

#### Stage

<!-- fields:Stage -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `id` | integer | yes |  | `tournament.id` | SofaScore's id of the stage. |
| `name` | string | yes |  | `tournament.name` | Name of the stage, for example `UEFA Champions League, Group A` or `Wimbledon, London, GB, Qualifying, 1st - 2nd Round`. |
<!-- /fields:Stage -->

A stage id is not a tournament id: in the example above the EFL Cup is tournament 21 and its stage is 17,
while tournament 17 is the Premier League.

#### Round

<!-- fields:Round -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `number` | integer | yes |  | `roundInfo.round` | Number of the round. |
| `name` | string | yes |  | `roundInfo.name` | Name of the round, for example `Quarterfinals`. League rounds have none. |
| `slug` | string | yes |  | `roundInfo.slug` | SofaScore's slug of the round. |
<!-- /fields:Round -->

By sport, as seen: a football or basketball league match has only `number`; a cup match and every tennis
match has `number`, `name` and `slug` (for example 27, `Quarterfinals`, `quarterfinals`); some events
(friendlies) have no round at all, and `round` is null.

#### Aggregate

<!-- fields:Aggregate -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `home` | integer | yes |  | `homeScore.aggregated` | Aggregate score of this event's home side. |
| `away` | integer | yes |  | `awayScore.aggregated` | Aggregate score of this event's away side. |
| `winner` | string, one of `home`, `away`, `draw` | yes |  | `aggregatedWinnerCode`: 1 is `home`, 2 is `away`, 3 is `draw` | Who won the tie. |
<!-- /fields:Aggregate -->

`home` and `away` are the sides of this event, not of the first leg. Football and, since SP-1 (PR #112),
handball fill the aggregate; no other sport's sheet reads `aggregated` (section 9, point 11). In the one
recorded handball second leg (15986085) the compact research record has no `aggregatedWinnerCode`, so its
`winner` is null.

#### Quality

<!-- fields:Quality -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `source` | string, one of `event`, `listing` | no |  | which stored payload the record derives from | `event`: the record derives from the stored `/event/{id}` payload. `listing`: the event is known only from a schedule page (a fixture, or a match whose details were never fetched); slices do not exist. |
| `observed_at_utc` | string | yes | ISO 8601 UTC | time of the platform's request | When the platform read the `/event/{id}` payload the record derives from. Null for a `listing` record and for a record stored by a version that did not note the time. |
| `change_ts` | integer | yes | epoch seconds | `changes.changeTimestamp` | SofaScore's own time of its last change to the event. Carried as given; compare it for equality or order to detect a change. |
| `settlement` | string, one of `open`, `provisional`, `final` | no |  | derived: status class, `observed_at_utc`, `start_utc` and the refresh window setting | `open`: the event has not reached a terminal status, or has no `/event/{id}` payload. `provisional`: terminal status, but it was last read before start time plus the refresh window, so the result may still be corrected. `final`: it was read after the window closed (or the time of the read or the start time is unknown, or the refresh policy is off); the platform will not read it again by itself. |
| `provisional` | boolean | no |  | derived | True exactly when `settlement` is `provisional`. |
| `tier_hint` | boolean | yes |  | `tournament.uniqueTournament.hasEventPlayerStatistics`, `hasEventPlayerStatistics` | True when SofaScore offers player statistics for the event or its tournament, which marks the better covered competitions (their results are corrected sooner). False when both flags are false, null when neither is given. |
| `stale` | boolean | no |  | derived by comparing stored payloads | True when a schedule page read later than the event payload disagrees with it in status, winner, start time or a score field. The next refresh reads the event again. |
| `status_regressed` | boolean | no |  | derived from the change log | True when the event was stored as completed and a later read showed it as void. |
<!-- /fields:Quality -->

`settlement` in full (`01-storage.md` 8.3). The refresh window is the setting `REFRESH_WINDOW_HOURS`
(default 72 hours); "gap" is `observed_at_utc` minus `start_utc`.

| `settlement` | Condition |
|---|---|
| `open` | the record is known only from a schedule page (`source` is `listing`), or its status class is `not_started`, `live` or `unknown` |
| `provisional` | otherwise, when the read time and the start time are known, the window is not 0, and the gap is smaller than the window |
| `final` | otherwise: the gap is at least the window; or the read time is unknown (a record stored by version 2.x before it noted the time); or the start time is unknown; or the window is 0 (refresh policy off) |

A postponed match whose start moved into the future has a negative gap and stays `provisional` until it is
read after the new start plus the window. `final` does not mean SofaScore will never change the event; it
means the platform's refresh policy will not read it again by itself.

### Score

The field `Event.score` has one of seven structures, chosen by the score family of the event's sport. The
member `family` says which. Every structure begins with the same three fields:

| Field | Meaning in every family |
|---|---|
| `family` | `football`, `periods`, `sets`, `innings`, `cricket`, `fight`, or null for a sport without a score mapping |
| `home`, `away` | the headline score: what SofaScore displays as the result (`homeScore.display`, else `homeScore.current`) |

All score values are read from the two objects `homeScore` and `awayScore` of SofaScore's event; a source
such as `period1` in the tables means `homeScore.period1` for the home value and `awayScore.period1` for the
away value. A pair is null when SofaScore gave neither value.

| Sport | Family | Headline score is | What SofaScore's keys mean |
|---|---|---|---|
| football | `football` | goals including extra time, without the shoot-out | `period1` first half, `normaltime` 90 minutes, `display` the result shown, `current` includes shoot-out goals (not used), `penalties` the shoot-out, `aggregated` the tie |
| basketball | `periods` | points including overtime | `period1`–`period4` quarters, or `period2` and `period4` for a game of two halves; `normaltime` regulation; `overtime` overtime alone; `current` final |
| tennis | `sets` | sets won | `period1`–`period5` games per set (points in a match tie-break), `periodNTieBreak` tie-break points, `current` sets won |
| American football, Aussie rules | `periods` | points including overtime | `period1`–`period4` quarters, `normaltime`, `overtime`, `current` |
| ice hockey, floorball | `periods` | goals including overtime | `period1`–`period3` thirds, `normaltime`, `overtime`, `current` |
| handball, rugby, futsal, minifootball | `periods` | goals or points including overtime; in handball also the shoot-out goals | `period1`, `period2` halves, `normaltime`, `overtime`, `current`; handball also `penalties` and, in a second leg, `aggregated` |
| volleyball, badminton, table tennis | `sets` | sets won | `period1`–`period7` points per set, `current` sets won |
| padel | `sets` | sets won | as tennis: games per set, `periodNTieBreak`, a possible match tie-break |
| snooker | `sets` | frames won | `current` frames won; `period1` repeats `current` and is not a set |
| darts | `sets` | sets won, or legs won when the match has no sets | `periodN` legs won in set N when the event has a positive `bestOfSets`; otherwise `current` legs won |
| e-sports | `sets` | games won | `current` games won; `periodN` only marks the winner of game N (1 or 0, 0-0 for an unplayed game) |
| baseball | `innings` | runs including extra innings | `innings.inningN.run`, else `periodN`; `normaltime`, `overtime`; `inningsBaseball.hits`, `.errors` |
| cricket | `cricket` | runs of all innings | `innings.inningN` with `score`, `wickets`, `overs` per side |
| MMA | `fight` | none (SofaScore gives no score) | `winType`, `finalRound`; the winner in `winnerCode` |
| any other | none (null) | SofaScore's displayed score | not mapped |

#### ScorePair

<!-- fields:ScorePair -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `home` | integer | yes |  | `homeScore.<key>` | Value of the home side. |
| `away` | integer | yes |  | `awayScore.<key>` | Value of the away side. |
<!-- /fields:ScorePair -->

#### FootballScore

<!-- fields:FootballScore -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `family` | constant `football` | no |  | sport registry | Always `football`. |
| `home` | integer | yes | goals | `homeScore.display`, else `homeScore.current` | Headline score of the home side: goals including extra time, without the penalty shoot-out. |
| `away` | integer | yes | goals | `awayScore.display`, else `awayScore.current` | Headline score of the away side: goals including extra time, without the penalty shoot-out. |
| `half_time` | [ScorePair](#scorepair) | yes | goals | `period1` | Goals in the first half. |
| `regulation` | [ScorePair](#scorepair) | yes | goals | `normaltime` | Goals after 90 minutes. |
| `after_extra_time` | [ScorePair](#scorepair) | yes | goals | `display`, only when `status.code` is 110 or 120 | Goals after extra time (cumulative, without the shoot-out). Null unless the match went to extra time. |
| `penalties` | [ScorePair](#scorepair) | yes | goals | `penalties` | Goals of the penalty shoot-out alone. |
<!-- /fields:FootballScore -->

How the stages relate, by status code:

| `status.code` | `regulation` | `after_extra_time` | `penalties` | `home` / `away` |
|---|---|---|---|---|
| 100 (ended) | final | null | null | equal to `regulation` (SofaScore's `display` and `normaltime` agree) |
| 110 (after extra time) | score after 90 minutes | final | null | equal to `after_extra_time` |
| 120 (after penalties) | score after 90 minutes | score after 120 minutes (level) | shoot-out | equal to `after_extra_time`; the winner is in `winner` |

Not mapped in version 1: the goals of each half of extra time (`extra1`, `extra2`) and of extra time alone
(`overtime`).

#### PeriodsScore

<!-- fields:PeriodsScore -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `family` | constant `periods` | no |  | sport registry | Always `periods`. |
| `home` | integer | yes | points | `homeScore.display`, else `homeScore.current` | Headline score of the home side: points including overtime. |
| `away` | integer | yes | points | `awayScore.display`, else `awayScore.current` | Headline score of the away side: points including overtime. |
| `format` | string, open set: `quarters`, `halves`, `thirds` | yes |  | sport registry (`src/sports.py`); basketball: `quarters` when `period1` or `period3` is present, `halves` when only `period2` / `period4` are | How regulation time is divided. Null while no period score exists. |
| `periods` | array of [PeriodScore](#periodscore) | no |  | `period1` to `period4` | Points of each period of regulation time that has a score, in order. |
| `regulation` | [ScorePair](#scorepair) | yes | points | `normaltime` | Points at the end of regulation time. |
| `overtime` | [ScorePair](#scorepair) | yes | points | `overtime` | Points scored in overtime alone. Null without overtime. |
| `final` | [ScorePair](#scorepair) | yes | points | `current` | Final points including overtime. |
| `penalties` | [ScorePair](#scorepair) | yes | goals | `penalties` | Goals of the penalty shoot-out alone. Null without a shoot-out, and always null for basketball. In the one recorded handball shoot-out, `final` and the headline score include these goals. |
<!-- /fields:PeriodsScore -->

```json example:PeriodsScore
{"family": "periods", "home": 100, "away": 101, "format": "quarters",
 "periods": [{"number": 1, "home": 24, "away": 20}, {"number": 2, "home": 21, "away": 26},
             {"number": 3, "home": 25, "away": 22}, {"number": 4, "home": 22, "away": 24}],
 "regulation": {"home": 92, "away": 92}, "overtime": {"home": 8, "away": 9},
 "final": {"home": 100, "away": 101}, "penalties": null}
```

A game of two halves (seen in the French lower leagues) has its two scores in SofaScore's `period2`
and `period4`; the schema numbers them 1 and 2 and says `"format": "halves"`.

`PeriodsScore` is the structure of nine sports since SP-1 (PR #112): basketball, American football, Aussie
rules, ice hockey, handball, rugby, futsal, minifootball and floorball. The field texts above still speak of
basketball and of points; the unit stays `points` because changing a unit would need a new schema version,
and in the goal sports (ice hockey, handball, futsal, minifootball, floorball) the values are goals.
`format` comes from the sport registry (`SportSpec.period_format`): `quarters` for American football and
Aussie rules, `thirds` for ice hockey and floorball (a value SP-1 added; the set is open, so no version
change), `halves` for handball, rugby, futsal and minifootball. Only basketball still detects its format
from the period keys, as before. `periods[].number` is N of SofaScore's `periodN`, except for basketball
halves. `penalties` (added by SP-1) is filled only in handball: in the one recorded handball shoot-out
(15251094) `current` and `display` include the shoot-out goals (31 = 22 + 5 + 4), unlike football, where
the displayed score leaves them out; an ice hockey shoot-out was never seen, so whether it would arrive as
`penalties` is open. Futsal and floorball tournaments often send only `current` and `display` (floorball
sometimes only `normaltime`); such a score has `format` null and an empty `periods` list. No live payload of
these eight sports was recorded, so `format` and the periods during a live game are not verified.

#### PeriodScore

<!-- fields:PeriodScore -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `number` | integer | no |  | `periodN`: N, except for basketball `halves`, where `period2` is 1 and `period4` is 2 | Position of the period within the format, starting at 1: quarter 1 to 4, half 1 to 2, or period 1 to 3. |
| `home` | integer | yes | points | `homeScore.periodN` | Points of the home side in the period (goals in the goal sports). |
| `away` | integer | yes | points | `awayScore.periodN` | Points of the away side in the period (goals in the goal sports). |
<!-- /fields:PeriodScore -->

#### SetsScore

<!-- fields:SetsScore -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `family` | constant `sets` | no |  | sport registry | Always `sets`. |
| `home` | integer | yes | sets | `homeScore.display`, else `homeScore.current` | Headline score of the home side: sets won. |
| `away` | integer | yes | sets | `awayScore.display`, else `awayScore.current` | Headline score of the away side: sets won. |
| `format` | string, open set: `games`, `points`, `frames`, `legs`, `legs_won`, `games_won` | yes |  | sport registry (`src/sports.py`); darts: `legs` when the event has `bestOfSets`, else `legs_won` | What the score counts. `games`, `points`, `legs`: sets won, and each set counts games (tennis, padel), points (volleyball, badminton, table tennis) or legs (darts played in sets). `frames`, `legs_won`, `games_won`: no sets; `sets_won` and the headline score are the frames (snooker), legs (darts played in legs only) or games (e-sports) won. Null when the record has no score sheet. |
| `sets_won` | [ScorePair](#scorepair) | yes | sets | `current` | Sets won by each side. |
| `sets` | array of [SetScore](#setscore) | no |  | `period1` to `period5`, `period1TieBreak` to `period5TieBreak` | The sets that have a score, in order. |
| `match_tiebreak` | boolean | no |  | derived from the set scores (`src/status.py`) | True when the deciding set was a match tie-break (first to 10 points) and not a normal set. A heuristic: the last of three or five sets has a side with 10 or more. |
<!-- /fields:SetsScore -->

```json example:SetsScore
{"family": "sets", "home": 1, "away": 1, "format": "games", "sets_won": {"home": 1, "away": 1},
 "sets": [{"number": 1, "home": 6, "away": 7, "tiebreak": {"home": 6, "away": 8}},
          {"number": 2, "home": 6, "away": 2, "tiebreak": null},
          {"number": 3, "home": 1, "away": 0, "tiebreak": null}],
 "match_tiebreak": false}
```

The example is a retired match (`status.code` 92, class `decided_without_play`, `winner` `home`). Retirement
and walkover are told by the status, not by the score (section 9, point 10). The current point of a live game
(SofaScore's `point`) is not mapped.

`SetsScore` is the structure of eight sports: tennis, and since SP-2 (PR #115) and SP-3 (PR #118)
volleyball, badminton, table tennis, padel, snooker, darts and e-sports. `format` (added by SP-3; an open
set) says what the numbers count, because darts changes its unit from one event to the next and a consumer
cannot know it from the sport alone:

| `format` | Sports | `home`, `away`, `sets_won` | `sets` |
|---|---|---|---|
| `games` | tennis, padel | sets won | games per set, with `tiebreak`; `match_tiebreak` can be true |
| `points` | volleyball, badminton, table tennis | sets won | points per set; no `tiebreak`, `match_tiebreak` always false |
| `legs` | darts played in sets (a positive `bestOfSets`) | sets won | legs per set |
| `frames` | snooker | frames won | empty: `period1` only repeats `current` (all 8 recorded samples) |
| `legs_won` | darts without sets | legs won | empty |
| `games_won` | e-sports | games won | empty: `periodN` only marks who won game N; the games are in the slice `esports_games` |

The field texts of `home`, `away` and `sets_won` above still say "sets won" with the unit `sets`, and those
of `SetScore` say games; for the formats `frames`, `legs_won` and `games_won` the values are frames, legs or
games won, and for `points` a set's values are points. The units stay as they are, because changing a unit
would need a new schema version; `format` is the field to read. For the sports of SP-2 and SP-3 up to
`period7` is read (a table tennis match can have seven sets; tennis keeps its 2.x sheet and `period5`), and
a set keeps its number: a live table tennis payload that carries only the
current set (`period4` alone) gives one set numbered 4. Only `bestOfSets` tells the two darts formats apart
(`bestOfLegs` is present in both); the legs-only rule is not verified on a real legs-only `/event` payload,
because the recorded compact records lost `bestOf*` (the not-started fixture 17099320 therefore maps to
`legs_won`).

#### SetScore

<!-- fields:SetScore -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `number` | integer | no |  | `periodN` | Number of the set, starting at 1. |
| `home` | integer | yes | games | `homeScore.periodN` | Games the home side won in the set; points when the set is a match tie-break. |
| `away` | integer | yes | games | `awayScore.periodN` | Games the away side won in the set; points when the set is a match tie-break. |
| `tiebreak` | [ScorePair](#scorepair) | yes | points | `periodNTieBreak` | Points of the set's tie-break. Null when the set had none. |
<!-- /fields:SetScore -->

#### InningsScore

<!-- fields:InningsScore -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `family` | constant `innings` | no |  | sport registry | Always `innings`. |
| `home` | integer | yes | runs | `homeScore.display`, else `homeScore.current` | Headline score of the home side: runs, extra innings included. |
| `away` | integer | yes | runs | `awayScore.display`, else `awayScore.current` | Headline score of the away side: runs, extra innings included. |
| `innings` | array of [InningScore](#inningscore) | no |  | `innings.inningN.run`, else `periodN` | Runs of each inning that has a score, in order. SofaScore gives the innings in `innings`; some leagues also give them as `period1` to `period9`, with the same values. An inning missing from `innings` is read from `periodN`. |
| `regulation` | [ScorePair](#scorepair) | yes | runs | `normaltime` | Runs after the scheduled innings. Null when SofaScore does not give it. |
| `extra_innings` | [ScorePair](#scorepair) | yes | runs | `overtime` | Runs scored in extra innings alone. Null without extra innings. |
| `hits` | [ScorePair](#scorepair) | yes | hits | `inningsBaseball.hits` | Hits of each side in the whole game. |
| `errors` | [ScorePair](#scorepair) | yes | errors | `inningsBaseball.errors` | Errors of each side in the whole game. |
<!-- /fields:InningsScore -->

#### InningScore

<!-- fields:InningScore -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `number` | integer | no |  | `innings.inningN`, else `periodN`: N | Number of the inning, starting at 1; extra innings go on after 9. |
| `home` | integer | yes | runs | `homeScore.innings.inningN.run`, else `homeScore.periodN` | Runs of the home side in the inning. |
| `away` | integer | yes | runs | `awayScore.innings.inningN.run`, else `awayScore.periodN` | Runs of the away side in the inning. |
<!-- /fields:InningScore -->

#### CricketScore

<!-- fields:CricketScore -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `family` | constant `cricket` | no |  | sport registry | Always `cricket`. |
| `home` | integer | yes | runs | `homeScore.display`, else `homeScore.current` | Headline score of the home side: runs of all its innings. |
| `away` | integer | yes | runs | `awayScore.display`, else `awayScore.current` | Headline score of the away side: runs of all its innings. |
| `innings` | array of [CricketInnings](#cricketinnings) | no |  | `homeScore.innings`, `awayScore.innings` | The innings of both sides, the home side's first, each side's in its own order. SofaScore numbers each side's innings separately and does not say which side batted first. |
<!-- /fields:CricketScore -->

#### CricketInnings

<!-- fields:CricketInnings -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `side` | string, one of `home`, `away` | no |  | `homeScore.innings` or `awayScore.innings` | The side that batted. |
| `number` | integer | no |  | `inningN`: N | Number of the innings of this side, starting at 1. |
| `runs` | integer | yes | runs | `inningN.score` | Runs scored. |
| `wickets` | integer | yes | wickets | `inningN.wickets` | Wickets lost. |
| `overs` | number | yes | overs | `inningN.overs` | Overs bowled, in SofaScore's notation: the digit after the point counts balls, so 68.1 is 68 overs and one ball. |
<!-- /fields:CricketInnings -->

#### FightScore

<!-- fields:FightScore -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `family` | constant `fight` | no |  | sport registry | Always `fight`. |
| `home` | integer | yes |  | `homeScore.display`, else `homeScore.current` | Headline score of the home side; SofaScore gives none for a fight, so null. |
| `away` | integer | yes |  | `awayScore.display`, else `awayScore.current` | Headline score of the away side; SofaScore gives none for a fight, so null. |
| `method` | string | yes |  | `winType` | How the fight was decided, as SofaScore abbreviates it, for example `UD` (unanimous decision), `SD` (split decision), `TKO`, `SUB` (submission); text, not an enumeration of the platform. Null while undecided. |
| `final_round` | integer | yes |  | `finalRound` | The round in which the fight ended. Null while undecided. |
<!-- /fields:FightScore -->

The three families `innings` (baseball), `cricket` and `fight` (MMA) came with SP-3 (PR #118), each from
one full `/event` payload and the research records of its sport. Not mapped, and so only in the raw payload:
cricket's `runRate` (derived) and `targetRunRate` (a target, not a score); the round durations of an MMA
fight (`time.period1` to `period5`), which are durations, and its `totalPeriodCount` disagreed with the
rounds fought in one sample (15962231: 3 scheduled, 5 fought). An MMA event has no score, so it never gives a
`live.score_changed`. Cricket's scorecard is the event slice `innings` (`/event/{id}/innings`, PR #121); a
baseball game's `/at-bats` is not a slice (one recorded answer only). `overs` is in SofaScore's
overs.balls notation and must not be summed as a decimal.

#### PlainScore

<!-- fields:PlainScore -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `family` | null | yes |  | sport registry | Always null: the sport has no score mapping. |
| `home` | integer | yes |  | `homeScore.display`, else `homeScore.current` | Headline score of the home side, as SofaScore displays it. |
| `away` | integer | yes |  | `awayScore.display`, else `awayScore.current` | Headline score of the away side, as SofaScore displays it. |
<!-- /fields:PlainScore -->

### Slice

One stored response of SofaScore about an event, or about another entity, together with its state. For an
event the slices are the event payload itself (key `event`) and its detail endpoints.

<!-- fields:Slice -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `owner_kind` | string, open set: `event`, `tournament`, `season`, `team`, `player`, `sport` | no |  | the request that fetched it | What the slice belongs to. |
| `owner_id` | integer | no |  | the request that fetched it | Id of the owner; for `event` the event id. |
| `key` | string, open set: `event`, `statistics`, `team_streaks`, `pregame_form`, `h2h`, `lineups`, `incidents`, `point_by_point`, `esports_games`, `innings`, `odds_featured`, `odds_all`, `odds_changes`, `winning_odds`, `seasons`, `schedule`, `standings`, `season_info`, `cuptrees`, `top_players`, `top_teams`, `season_odds`, `team_rankings`, `player_statistics`, `rankings` | no |  | slice registry (`src/sports.py`) | Name of the slice, for example `event`, `statistics`, `lineups`, `incidents`. |
| `sub` | string | yes |  | slice registry | Sub-key for a slice that has several payloads per owner, for example the round of a schedule page. Null when the slice has one payload. |
| `state` | string, one of `ok`, `empty`, `error`, `not_requested` | no |  | the platform's bookkeeping | `ok`: a payload with data is stored. `empty`: SofaScore answered that it has no such data (404, or a response without content). `error`: the last attempt failed and it is unknown whether data exists. `not_requested`: the platform has not asked for it. |
| `has_payload` | boolean | no |  | the platform's bookkeeping | True when a payload is stored. A slice in state `error` can still hold the payload of an earlier successful read. |
| `fetched_at_utc` | string | yes | ISO 8601 UTC | time of the platform's request | When the stored payload was read. |
| `checked_at_utc` | string | yes | ISO 8601 UTC | time of the platform's request | When the slice was last asked for, whatever the outcome. |
| `error` | [SliceError](#sliceerror) | yes |  | the platform's bookkeeping | The last failure. Null unless the state is `error`. |
| `payload` | any JSON value | yes |  | the whole response | The stored SofaScore response, unchanged (see Raw on request). Present only when asked for; null otherwise. |
<!-- /fields:Slice -->

Keys of an event today (the slice registry, `ssc describe slices`):

| `key` | SofaScore endpoint | Sports |
|---|---|---|
| `event` | `/event/{id}` | all |
| `statistics` | `/event/{id}/statistics` | all |
| `team_streaks` | `/event/{id}/team-streaks` | all |
| `pregame_form` | `/event/{id}/pregame-form` | all |
| `h2h` | `/event/{id}/h2h` | all |
| `lineups` | `/event/{id}/lineups` | all but darts, MMA, padel and snooker |
| `incidents` | `/event/{id}/incidents` | all but darts, e-sports, MMA, padel and snooker |
| `point_by_point` | `/event/{id}/point-by-point` | tennis, darts |
| `esports_games` | `/event/{id}/esports-games` | e-sports (live and finished matches) |
| `innings` | `/event/{id}/innings` | cricket (live and finished matches) |

"All" includes a sport that is not registered. The sports a slice is not requested in, and those where it is
requested but does not count for completeness, come from PR #121 (`not_in` and `optional_in` of the slice
registry; `docs/all-sports/README.md`, "Maç detay dilimleri, spor başına"), on the evidence of one match page
per sport. The known values of `Slice.key` in the field table above do not list `innings` yet: the field is an
open set, and the list follows `src/schema/models.py`, which PR #121 did not change.

Slices of other owners today: `seasons` of a tournament (the season list), and `schedule` of a season, one
payload per round or page with `sub` such as `round_12` or `last_0`. More keys come with P28 (odds, standings,
…); `key` is an open set.

Keys added by P28 (#140; `src/sports.py:556-608` at `b6caf2f`), all off unless a selection names them and none
counting for completeness. Event slices of group `odds`, with the provider id as `sub` (`[client]
odds_provider`, default `1`): `odds_featured` (`/event/{id}/odds/{provider}/featured`), `odds_all`
(`/event/{id}/odds/{provider}/all`), `odds_changes` (`/event/{id}/odds/{provider}/changes`) and `winning_odds`
(`/event/{id}/provider/{provider}/winning-odds`). Slices of other owners: `owner_kind` `season`: `standings`
(sub `total` or `home`), `season_info`, `cuptrees`, `top_players`, `top_teams`, `season_odds` (provider sub);
`team`: `team_rankings`; `player`: `player_statistics`; `sport`: `rankings` (sub `5`, the ATP list). The known
values of `Slice.key` in the field table above do not list them, nor `innings`; FX-21 adds them. An odds
slice records the provider in its meta (`meta.provider_id`) and, only when the user sets `[client]
odds_country`, the country (`meta.country`, upper case; a decision of 2026-10-06: opt-in, never derived from
the machine). The slice record of API v1 does not carry the meta.

`state` and `has_payload` together:

| `state` | `has_payload` | Meaning |
|---|---|---|
| `ok` | true | a payload with data is stored |
| `empty` | false | SofaScore has no such data for this owner (so far) |
| `error` | false | the last attempt failed; nothing is stored |
| `error` | true | the last attempt failed; the payload of an earlier read is still stored |
| `not_requested` | false | the platform never asked |

```json example:Slice
{"owner_kind": "event", "owner_id": 16837335, "key": "statistics", "sub": null, "state": "ok",
 "has_payload": true, "fetched_at_utc": "2026-09-30T12:00:00Z", "checked_at_utc": "2026-09-30T12:00:00Z",
 "error": null, "payload": null}
```

#### SliceError

<!-- fields:SliceError -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `reason` | string, open set: `403`, `429`, `5xx`, `timeout`, `network`, `parse`, `breaker`, `corrupt`, `other` | no |  | the platform's request | Why it failed. |
| `http_status` | integer | yes |  | the platform's request | HTTP status of the failed response, when there was one. |
| `at_utc` | string | yes | ISO 8601 UTC | time of the platform's request | When the attempt failed. |
| `count` | integer | no |  | the platform's bookkeeping | How many attempts in a row have failed. |
<!-- /fields:SliceError -->

### Change

A change of an already stored event that a later read found. SofaScore corrects results after the final
whistle (a goal reassigned, a match abandoned later); its own `changes` object says which fields changed but
not what they were. The platform compares the stored payload with the new one and records old and new value.

<!-- fields:Change -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `seq` | integer | no |  | the platform's change log | Sequence number of the change log. Increases by one per change; pass the last one seen to read the next changes. |
| `recorded_at_utc` | string | no | ISO 8601 UTC | time of the platform's request | When the platform found the change. |
| `event_id` | integer | no |  | `id` | Id of the Event. |
| `sport` | string | yes |  | `tournament.category.sport.slug` | Slug of the sport. |
| `tournament_id` | integer | yes |  | `tournament.uniqueTournament.id` | Id of the Tournament. |
| `start_utc` | string | yes | ISO 8601 UTC | `startTimestamp` | Scheduled start of the event after the change. |
| `seconds_after_start` | integer | yes | seconds | `changes.changeTimestamp` - `startTimestamp` | How long after the scheduled start SofaScore made the change: its change time, or the time the platform found the change when SofaScore gave none, minus the start. |
| `old_status_class` | string, one of `not_started`, `live`, `completed`, `decided_without_play`, `void`, `unknown` | yes |  | derived from `status` | Status class before. |
| `new_status_class` | string, one of `not_started`, `live`, `completed`, `decided_without_play`, `void`, `unknown` | yes |  | derived from `status` | Status class after. |
| `old_change_ts` | integer | yes | epoch seconds | `changes.changeTimestamp` of the stored payload | SofaScore's change time before. |
| `new_change_ts` | integer | yes | epoch seconds | `changes.changeTimestamp` of the new payload | SofaScore's change time after. |
| `status_regressed` | boolean | no |  | derived | True when the event went from completed to void. |
| `tier_hint` | boolean | yes |  | `tournament.uniqueTournament.hasEventPlayerStatistics`, `hasEventPlayerStatistics` | As `Event.quality.tier_hint`, at the time of the change. |
| `fields` | array of [ChangedField](#changedfield) | no |  | difference of the two payloads | The fields that changed, ordered by path. Compared are the status triple, the winner code, the start time and every score field. |
<!-- /fields:Change -->

```json example:Change
{"seq": 1, "recorded_at_utc": "2026-09-15T13:10:00Z", "event_id": 17099711, "sport": "football",
 "tournament_id": 17, "start_utc": "2026-09-15T04:00:00Z", "seconds_after_start": 32927,
 "old_status_class": "completed", "new_status_class": "completed",
 "old_change_ts": 1789474674, "new_change_ts": 1789477727, "status_regressed": false, "tier_hint": true,
 "fields": [{"path": "awayScore.current", "old": 0, "new": 1}, {"path": "awayScore.display", "old": 0, "new": 1},
            {"path": "awayScore.normaltime", "old": 0, "new": 1}, {"path": "awayScore.period1", "old": 0, "new": 1}]}
```

#### ChangedField

<!-- fields:ChangedField -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `path` | string | no |  | the path itself | Path of the field in SofaScore's event object: `status.type`, `status.code`, `status.description`, `winnerCode`, `startTimestamp`, `homeScore.<key>`, `awayScore.<key>`. |
| `old` | any JSON value | yes |  | the stored payload | Value before, as SofaScore gave it. Null when the field did not exist. |
| `new` | any JSON value | yes |  | the new payload | Value after, as SofaScore gave it. Null when the field no longer exists. |
<!-- /fields:ChangedField -->

`path`, `old` and `new` are in SofaScore's terms, not in the terms of the normalized score, because a change
is evidence of what SofaScore changed (section 9, point 17). The Event record of the same event shows the
normalized state after the change.

### LiveEvent

An event of a stream. Producers append events to a durable log; `ssc events`, `watch --stdout` and the sinks
(stdout, file, webhook) write them out in this shape, one JSON object per line. No HTTP route serves them
(owner decision of 2026-10-01). The envelope is the one of `02-services.md` 5.1, schema `sofascore.event/1`.

<!-- fields:LiveEvent -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `stream` | string, open set: `live`, `change`, `job`, `system` | no |  | the producer | The stream the event belongs to. |
| `seq` | integer | no |  | the platform's stream log | Sequence number, one sequence for all streams of a data directory. Strictly increasing, not consecutive. De-duplicate on it. |
| `type` | string, open set: `live.status_changed`, `live.score_changed`, `live.stuck`, `change.recorded`, `job.started`, `job.finished`, `system.blocked`, `system.recovered`, `system.live_source_changed`, `system.sink_dropped` | no |  | the producer | Type of the event, prefixed with its stream. |
| `ts` | string | no | ISO 8601 UTC | the platform's clock | When the platform recorded the event, with milliseconds. |
| `event_id` | integer | yes |  | `id` | Id of the Event it is about. Null for events about no match. |
| `sport` | string | yes |  | the producer | Slug of the sport. |
| `tournament_id` | integer | yes |  | `tournament.uniqueTournament.id` | Id of the Tournament. |
| `source` | string, open set: `push`, `poll`, `job`, `system` | yes |  | the producer | How the platform learned of it. |
| `data` | object | no |  | the producer | Type-specific content (see LiveEvent data). |
<!-- /fields:LiveEvent -->

```json example:LiveEvent
{"stream": "live", "seq": 1842, "type": "live.status_changed", "ts": "2026-10-01T18:52:04.512Z",
 "event_id": 17124861, "sport": "football", "tournament_id": 17, "source": "poll",
 "data": {"from": "live", "to": "completed", "provisional": true, "change_ts": 1790880721}}
```

`seq` identifies an event for as long as the stream id of the data directory stays the same; the stream id
changes only when the state database is recreated. Numbers are not consecutive within a stream, and after a
connection gap the events of one match may skip intermediate states.

#### LiveEvent data

Version 1 fixes the envelope. The content of `data` depends on `type`. It was not fixed when version 1 was
approved and was left to the live service (plan item P23; section 9, point 18). P23 (PR #91) settled it
(`stream_data` in `src/services/live/reducer.py` and the supervisor in `src/services/live/supervisor.py`).
The model still types `data` as an object, so the field table above and the JSON Schema do not change;
this table is the contract for `data`:

| `type` | `data` as settled by P23 |
|---|---|
| `live.status_changed` | `from`, `to`: status classes. `change_ts`: SofaScore's `changes.changeTimestamp` of the event, an epoch integer, or null. `provisional`: true or false on the first `completed` of the event (true while the start plus `REFRESH_WINDOW_HOURS` has not passed), null on every other transition. `score`: the [Score](#score) structure of this document for the observed event, null when it cannot be built |
| `live.score_changed` | `from`, `to`: each a [ScorePair](#scorepair)-like object `{home, away}` of the headline score. `change_ts` as above. `score`: the [Score](#score) structure |
| `live.stuck` | `status_class`: the class at the time (`live` or `not_started`). `start_utc`: ISO 8601 UTC of the start from which the sport's threshold is counted (the real play start for tennis), or null |
| `change.recorded` | `change_seq`: the `seq` of the [Change](#change) it announces. Produced since P23 by the live service when its confirmation of a finished match wrote a change row, and since P13 (PR #113) by downloads and refreshes (source `job`) |
| `job.started`, `job.finished`, `system.sink_dropped` | as in the table below (unchanged) |
| `system.blocked` | `source` (`poll`), `retry_in_s` (the pause before the next try), `reason` (a short text that names the refusal, such as `RateLimitError` or `APIError 403`); produced by the live service since P23 |
| `system.recovered` | `source` (`poll`), `blocked_for_s`; produced by the live service since P23 |
| `system.live_source_changed` | `sport`; `from`, `to`: the leading source before and after, `page`, `direct` or `poll`; `reason`: `push_connected`, `push_disconnected`, `push_silent` or `push_unavailable`. Produced by the live service since P24 (PR #95) at every switch (`02-services.md` 8.1) |

A `live.status_changed` is emitted only when a previous class is known, so `from` is never null. A
`live.score_changed` is emitted only while the event was live in the previous observation and stays live.
The same transition is stored once: the stream log keeps one event per stream and `dedup_key` (an append
with a key that the stream already holds stores nothing), and the key is
`<id>:<type>:<from>><to>:<change_ts>` for the two change types (`-` for a missing `change_ts`) and
`<id>:stuck:<start_ts>` for `live.stuck`. Without a `change_ts`, a second identical transition with an
identical score is therefore not stored again. The `dedup_key` stays internal to the log and is not a field
of the envelope. The example of the LiveEvent section above shows a `live.status_changed` without its `score`
field; as produced the field is always present. The legacy `main.py --watch` alias appends the same `data`
to the log, and keeps its 2.x fields only in its own stdout lines and in `watch_events.jsonl` for one more
release. Since P19 (PR #119) the alias runs `ssc watch --source poll --stdout`, so it writes the events of
this table and no `watch_events.jsonl`.

`source` of a live event names the source that showed the change: `poll`, or, since P24 and P31 (PRs #95,
#101), `page` or `direct` for an observation that came from push. The value `push` of the field table is
not produced; the field is an open set, so the new values need no new version. `system.*` events carry the
source `system`.

What was stored before P23, and what was proposed when version 1 was approved:

| `type` | `data` before P23 (`live.*`: the 2.x watcher, since PR #59) | `data` proposed for the live service |
|---|---|---|
| `live.status_changed` | `from`, `to` (status classes), `at_utc`, `change_ts`, `source` (`live` or `event`: which request showed it), `scores` (the 2.x score sheet, pairs as two-element arrays), `provisional` (only on the first `completed`) | `from`, `to` (status classes), `change_ts`, `provisional`, `score` (the [Score](#score) structure of this document) |
| `live.score_changed` | `from`, `to` (each `[home, away]` of the headline score), `at_utc`, `change_ts`, `source` | `from`, `to` (each a [ScorePair](#scorepair)), `change_ts`, `score` |
| `live.stuck` | `at_utc`, `start_ts`, `status_class` | `status_class`, `start_utc` |
| `change.recorded` | not produced yet | `change_seq`: the `seq` of the [Change](#change) it announces |
| `job.started` | `job_id`, `kind`, `origin` (an object with `face`, `pid` and `host`); produced by the job manager since PR #69 | the same |
| `job.finished` | `job_id`, `kind`, `state`, `counts` (`details_done`, `details_total`, `failed_count`, `refreshed`, `refresh_changed`), `error_code`; since PR #69 | the same |
| `system.sink_dropped` | `sink`, `reason` (`max_age` or `pruned`), `first_seq`, `last_seq`, `count` (null for `pruned`), and `max_age_seconds` for `max_age`; produced by the sink dispatcher since PR #73 | the same |
| other `system.*` | not produced yet | see `02-services.md` 5.1 |

Job events carry the origin of the job. The `origin` of `job.started` says which interface started the job
(`face`: `cli`, `api`, `scheduler` or `library`) and gives the process id (`pid`) and the host name (`host`)
of the process that started it (`src/jobs/manager.py:319-329` at `e0bae0c`). A sink that is subscribed to
`job.*` therefore delivers a host name and a pid, and API v1 shows the same `origin` on a job. This is kept
on purpose (decision D20 in `03-implementation-plan.md`, section 13, settled on 2026-10-02): a sink and the
API deliver to the operator's own systems. The diagnostics bundle, which is made to be handed to other
people, carries the face and the pid and not the host name (PR #71).

### Odds and standings

Added by P28 (#140). Odds are read from the event's odds slices (`odds_all`, `odds_featured`) and kept as a
history of snapshots (the slice history of `01-storage.md`): a record is one read, and odds change until the
event ends. API v1 gives them at `/events/{id}/odds/{key}` (oldest snapshot first; `?history=` for every
snapshot) and the export dataset `odds` as `OddsLine` rows; standings come from the season slice `standings`
at `/seasons/{id}/standings` and the export dataset `standings`. `odds_featured` gives the values of its
`featured` object, each with its key as `label`; `odds_all` its `markets`. Prices are fractions as SofaScore
gives them, with a decimal derived as 1 + the fraction, rounded to three places (`fraction_decimal`,
`src/schema/mappers.py:632`). The mappers are `odds_from_payload`, `odds_lines` and `standings_rows`
(`:680-742`); `standings_rows` skips rows that have neither a team nor a position. The country SofaScore
answered for is not a field of `Odds`: it is in the slice's meta (`meta.country`) when the user set `[client]
odds_country`, and the provider id is in `meta.provider_id` as well as in the record. `winning_odds` and
`season_odds` have no model (their only samples were 404s); they are raw slices.

These models are in `models.PENDING_MODELS` (`src/schema/models.py:845`), not in `MODELS`: `ssc describe
schemas` and the JSON Schema (`tests/golden/schema/`) do not include them, while API v1 and the exports use
them, and a test applies the field-contract checks of the other models to them. Plan item FX-21 moves them
into `MODELS`, and `Odds`, `OddsLine` and `StandingsRow` into `RECORDS`.

#### Odds

One read of an odds slice of an event: a snapshot.

<!-- fields:Odds -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `event_id` | integer | no |  | the request that fetched it | Id of the Event. |
| `key` | string, open set: `odds_all`, `odds_featured` | no |  | slice registry (`src/sports.py`) | Odds slice the record comes from: `odds_all` or `odds_featured`. |
| `provider_id` | integer | yes |  | the request that fetched it | SofaScore's id of the bookmaker the odds come from. Which bookmakers SofaScore offers depends on the country it sees the request from; the platform stores no address or location of the machine. |
| `fetched_at_utc` | string | yes | ISO 8601 UTC | time of the platform's request | When the odds were read. A read is a snapshot: odds change until the event ends, and only a later read shows a later price. |
| `markets` | array of [OddsMarket](#oddsmarket) | no |  | `markets`, or the values of `featured` | The markets, in SofaScore's order. |
<!-- /fields:Odds -->

```json example:Odds
{"event_id": 17144927, "key": "odds_all", "provider_id": 1, "fetched_at_utc": "2026-09-21T14:13:20Z",
 "markets": [{"market_id": 1, "name": "Full time", "group": "1X2", "period": "Full-time",
  "choice_group": null, "label": null, "is_live": true, "suspended": false,
  "choices": [{"name": "1", "fractional": "11/5", "decimal": 3.2, "initial_fractional": "13/10",
               "initial_decimal": 2.3, "change": 1, "winning": null},
              {"name": "X", "fractional": "8/13", "decimal": 1.615, "initial_fractional": "5/2",
               "initial_decimal": 3.5, "change": -1, "winning": null},
              {"name": "2", "fractional": "11/2", "decimal": 6.5, "initial_fractional": "31/20",
               "initial_decimal": 2.55, "change": 1, "winning": null}]}]}
```

#### OddsMarket

One market of a snapshot (match result, over/under, handicap, ...).

<!-- fields:OddsMarket -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `market_id` | integer | yes |  | `marketId` | SofaScore's id of the market type (1 is the match result). |
| `name` | string | yes |  | `marketName` | Name of the market, for example `Full time`. |
| `group` | string | yes |  | `marketGroup` | Group of the market, for example `1X2` or `Home/Away`. |
| `period` | string | yes |  | `marketPeriod` | Part of the event the market covers, for example `Full-time`. |
| `choice_group` | string | yes |  | `choiceGroup` | Line of a market with several lines, for example `2.5` for over/under; null for a market with one line. |
| `label` | string | yes |  | key of `featured` | Name under which the featured odds list the market (`default`, `fullTime`, `asian`); null in the full list. |
| `is_live` | boolean | yes |  | `isLive` | True when the prices were offered during play. |
| `suspended` | boolean | yes |  | `suspended` | True when the market was closed for bets at the time of the read. |
| `choices` | array of [OddsChoice](#oddschoice) | no |  | `choices` | The outcomes of the market, in SofaScore's order. |
<!-- /fields:OddsMarket -->

#### OddsChoice

One outcome of a market and its price.

<!-- fields:OddsChoice -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `name` | string | no |  | `choices[].name` | Name of the outcome as SofaScore gives it, for example `1`, `X`, `2`, `Over`. |
| `fractional` | string | yes |  | `choices[].fractionalValue` | Current price as a fraction, for example `11/5`. |
| `decimal` | number | yes |  | derived from `choices[].fractionalValue` | Current price as a decimal (1 + the fraction), rounded to three places. |
| `initial_fractional` | string | yes |  | `choices[].initialFractionalValue` | Opening price as a fraction. |
| `initial_decimal` | number | yes |  | derived from `choices[].initialFractionalValue` | Opening price as a decimal, rounded to three places. |
| `change` | integer | yes |  | `choices[].change` | Direction of the last change of the price: 1 up, -1 down, 0 none. |
| `winning` | boolean | yes |  | `choices[].winning` | True for the outcome that won once the event is settled; null while open or when SofaScore does not say. |
<!-- /fields:OddsChoice -->

#### OddsLine

The flat row of the `odds` export dataset: one outcome of one market of one snapshot.

<!-- fields:OddsLine -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `event_id` | integer | no |  | the request that fetched it | Id of the Event. |
| `key` | string, open set: `odds_all`, `odds_featured` | no |  | slice registry (`src/sports.py`) | Odds slice the row comes from. |
| `provider_id` | integer | yes |  | the request that fetched it | As `Odds.provider_id`. |
| `fetched_at_utc` | string | yes | ISO 8601 UTC | time of the platform's request | When the snapshot was read. |
| `market_id` | integer | yes |  | `marketId` | As `OddsMarket.market_id`. |
| `market_name` | string | yes |  | `marketName` | As `OddsMarket.name`. |
| `market_group` | string | yes |  | `marketGroup` | As `OddsMarket.group`. |
| `market_period` | string | yes |  | `marketPeriod` | As `OddsMarket.period`. |
| `choice_group` | string | yes |  | `choiceGroup` | As `OddsMarket.choice_group`. |
| `label` | string | yes |  | key of `featured` | As `OddsMarket.label`. |
| `is_live` | boolean | yes |  | `isLive` | As `OddsMarket.is_live`. |
| `suspended` | boolean | yes |  | `suspended` | As `OddsMarket.suspended`. |
| `choice` | string | no |  | `choices[].name` | As `OddsChoice.name`. |
| `fractional` | string | yes |  | `choices[].fractionalValue` | As `OddsChoice.fractional`. |
| `decimal` | number | yes |  | derived from `choices[].fractionalValue` | As `OddsChoice.decimal`. |
| `initial_fractional` | string | yes |  | `choices[].initialFractionalValue` | As `OddsChoice.initial_fractional`. |
| `initial_decimal` | number | yes |  | derived from `choices[].initialFractionalValue` | As `OddsChoice.initial_decimal`. |
| `change` | integer | yes |  | `choices[].change` | As `OddsChoice.change`. |
| `winning` | boolean | yes |  | `choices[].winning` | As `OddsChoice.winning`. |
<!-- /fields:OddsLine -->

```json example:OddsLine
{"event_id": 17144927, "key": "odds_all", "provider_id": 1, "fetched_at_utc": "2026-09-21T14:13:20Z",
 "market_id": 1, "market_name": "Full time", "market_group": "1X2", "market_period": "Full-time",
 "choice_group": null, "label": null, "is_live": true, "suspended": false, "choice": "1",
 "fractional": "11/5", "decimal": 3.2, "initial_fractional": "13/10", "initial_decimal": 2.3,
 "change": 1, "winning": null}
```

#### StandingsRow

One row of a season's table (`standings`, sub `total` or `home`); the row of the `standings` export dataset.

<!-- fields:StandingsRow -->
| Field | Type | Null | Unit | Source | Meaning |
|---|---|---|---|---|---|
| `tournament_id` | integer | no |  | the request that fetched it | Id of the Tournament. |
| `season_id` | integer | no |  | the request that fetched it | Id of the Season. |
| `table` | string, open set: `total`, `home` | no |  | the request that fetched it | Which table: `total` (all matches) or `home` (home matches only). |
| `group_name` | string | yes |  | `standings[].name` | Name of the table or group, for example `Premier League 26/27` or `Group A`. |
| `position` | integer | yes |  | `rows[].position` | Rank in the table, 1 for the first. |
| `participant_id` | integer | yes |  | `rows[].team.id` | Id of the Participant. |
| `participant_name` | string | yes |  | `rows[].team.name` | Name of the Participant. |
| `matches` | integer | yes |  | `rows[].matches` | Matches played. |
| `wins` | integer | yes |  | `rows[].wins` | Matches won. |
| `draws` | integer | yes |  | `rows[].draws` | Matches drawn; null in sports without draws. |
| `losses` | integer | yes |  | `rows[].losses` | Matches lost. |
| `scores_for` | integer | yes |  | `rows[].scoresFor` | Goals or points scored. |
| `scores_against` | integer | yes |  | `rows[].scoresAgainst` | Goals or points conceded. |
| `points` | number | yes |  | `rows[].points` | Table points (a fraction in a few sports). |
| `fetched_at_utc` | string | yes | ISO 8601 UTC | time of the platform's request | When the table was read. |
<!-- /fields:StandingsRow -->

```json example:StandingsRow
{"tournament_id": 17, "season_id": 96668, "table": "total", "group_name": "Premier League 26/27",
 "position": 1, "participant_id": 17, "participant_name": "Manchester City", "matches": 5,
 "wins": 5, "draws": 0, "losses": 0, "scores_for": 13, "scores_against": 5, "points": 15,
 "fetched_at_utc": "2026-09-21T14:13:20Z"}
```

## 5. What differs by sport

Everything that is not listed here is the same for every sport.

| | football | basketball | tennis |
|---|---|---|---|
| `score.family` | `football` | `periods` | `sets` |
| headline score | goals incl. extra time | points incl. overtime | sets won |
| `winner: "draw"` | possible | not seen | not possible |
| `aggregate` | two-legged cup ties | not mapped (section 9, point 11) | never |
| `Participant.type` | `team` | `team` | `player`, or `pair` in doubles |
| `Category.country_code` | set for countries, null for international categories | as football | null (categories are tours) |
| `round` | league: number only; cup: number, name, slug | as football | number, name, slug |
| `start_utc` | kick-off | tip-off | the planned time on the order of play; the first point can be hours later |
| `decided_without_play` | not seen | walkover (code 91) | walkover (91), retired (92) |
| in-progress codes | 6, 7, 31 | 13–16, 30 | 8, 9, 10 |
| extra slice | | | `point_by_point` |
| `quality.tier_hint` | true for 12 of 48 samples | true for 25 of 54 samples | false for all 52 samples |

The eighteen sports added by SP-1 (PR #112), SP-2 (PR #115) and SP-3 (PR #118) rest on the research
recordings of `docs/all-sports/README.md`: status examples, compact event records and one match page per
sport, and no live payload for most of them. What differs, in short (the score columns are section 4,
"Score"):

| Sport | `score.family`, format | Headline score | `Participant.type` | Extra or missing slices (PR #121) |
|---|---|---|---|---|
| American football, Aussie rules | `periods`, `quarters` | points incl. overtime | `team` | |
| ice hockey, floorball | `periods`, `thirds` | goals incl. overtime | `team` | ice hockey: `pregame_form` optional |
| handball | `periods`, `halves` | goals incl. overtime and the shoot-out | `team` | |
| rugby | `periods`, `halves` | points incl. overtime | `team` | |
| futsal, minifootball | `periods`, `halves` | goals incl. overtime | `team` | futsal: `statistics`, `lineups`, `pregame_form` optional; minifootball: `lineups` optional |
| volleyball, badminton, table tennis | `sets`, `points` | sets won | volleyball `team`; badminton `player` or `pair`; table tennis `player` | |
| padel | `sets`, `games` | sets won | `pair` | no `lineups`, `incidents`; `statistics`, `pregame_form` optional |
| snooker | `sets`, `frames` | frames won | `player` | no `lineups`, `incidents`; `statistics`, `pregame_form` optional |
| darts | `sets`, `legs` or `legs_won` | sets or legs won | `player` | `point_by_point`; no `lineups`, `incidents`; `pregame_form` optional |
| e-sports | `sets`, `games_won` | games won | `team` | `esports_games` (optional); no `incidents`; `statistics`, `pregame_form` optional |
| baseball | `innings` | runs incl. extra innings | `team` | |
| cricket | `cricket` | runs of all innings | `team` | `innings`; `statistics`, `pregame_form` optional |
| MMA | `fight` | none | `player` | no `lineups`, `incidents` |

Codes 91 and 92 give `decided_without_play` in every sport (which of these sports use them was not
checked), and only football and handball fill `aggregate`. The `Participant.type` of darts and MMA (`player`) and of
baseball, cricket and e-sports (`team`) is verified on one full payload each (SP-3, point 13 of section 9);
for the other sports the column gives the types seen in the research samples (`research/all_sports/samples`),
which no test checks.

## 6. JSON Schema

`src/schema/jsonschema.py` produces one JSON Schema document (draft 2020-12) with every record under `$defs`.
`ssc describe schemas` will print it; a copy is kept as a test golden in
`tests/golden/schema/json_schema.json`. How the rules of section 2 appear in it:

- every field is in `required`; a nullable field has `null` among its types;
- `additionalProperties` is not set, because fields may be added;
- a closed enumeration is an `enum`; an open one has no `enum` and lists its known values as `examples`;
- `Event.score` is a `oneOf` of the four score structures, told apart by `family`;
- time fields have `"format": "date-time"`;
- `x-unit` and `x-source` carry the unit and the source column of the tables above.

The JSON Schema document describes version 1 as it is at a given release. It grows with every free change of
section 3; a record that was valid stays valid.

## 7. Raw on request

"Raw" means the stored SofaScore payload: the parsed response serialised again, with the same values and key
order as the response, though not byte for byte (`01-storage.md` 4.1). The normalized records never replace
it.

What a raw request returns:

- **For a slice** (`/events/{id}/slices/{key}/raw`, `?raw=1`, a Slice with its payload asked for,
  `ssc export --schema raw`): the stored payload of exactly that slice, and nothing else. No envelope, no
  `schema_version`, no renamed field, no added field.
- **For an event** (`/events/{id}/raw`): the payload of the slice `event`, that is SofaScore's event object.
  This is the object that SofaScore's `/event/{id}` response holds under its key `event`; the platform stores
  the object, not the wrapper around it (section 9, point 28).
- **Metadata travels beside the payload, not inside it.** Over HTTP: the `ETag` header (the payload's
  sha256) and `X-Sofascore-Fetched-At`. In a raw JSONL export: one line per slice,
  `{"event_id": …, "key": …, "sub": …, "fetched_at": …, "payload": …}`. In a Slice record: the field
  `payload` next to `fetched_at_utc`.
- **When no payload is stored** (state `empty`, `not_requested`, or `error` without an earlier payload), a
  raw request answers "not found" (error code `not_found`); it never invents an empty payload. A Slice record then has
  `"payload": null` and `"has_payload": false`.
- **Raw is not versioned.** Its content is whatever SofaScore sent on the day it was fetched. Two payloads of
  the same key fetched a year apart can differ in structure. `schema_version` says nothing about raw data.
- **Raw is the only place** for everything this schema does not map: translations, team colours, venue,
  referee, the live clock, the current tennis point, player details, and every slice's content.

## 8. Implementation and tests

- `src/schema/models.py`: the records as frozen dataclasses; each field's description, unit and source are
  field metadata. `SCHEMA_VERSION = 1`.
- `src/schema/mappers.py`: pure functions from the Store's rows to the records: `event_from_row` (EventRow),
  `tournament_from_row`, `season_from_row`, `participant_from_row`, `category_from_row`, `sport_from_row`,
  `slice_from_info` (SliceInfo), `change_from_row` (ChangeRow), `live_event_from_record` (StreamRecord). The
  refresh window is passed in; nothing reads a setting, a file or the clock.
- `src/schema/jsonschema.py`: the JSON Schema, generated from the models.
- Since P28 (#140): the models `Odds`, `OddsMarket`, `OddsChoice`, `OddsLine` and `StandingsRow` in
  `models.PENDING_MODELS`, and the mappers `odds_from_payload`, `odds_lines`, `standings_rows` and
  `fraction_decimal`; they are outside the JSON Schema and the generated tables until FX-21 (section 4).
- The package imports only the pure domain modules (`src.sports`, `src.status`, `src.refresh`).
- `tests/test_schema_v1.py`, with goldens under `tests/golden/schema/`:
  - each of the real status payloads maps to a golden Event record (154 of three sports at SC-1; 231 of the
    21 registered sports since SP-3);
  - each field of those records is read a second time, independently, from the SofaScore path this document
    names, and must agree;
  - complete SofaScore event payloads (with teams, tournament, category, season, round; seven at SC-1, twelve
    since SP-3, one for each of the five sports of SP-3) map to golden Event, Sport, Category, Tournament,
    Season and Participant records;
  - records mapped from a real Store (catalog rows, slices, change log, stream log) match a golden;
  - every record validates against the JSON Schema; the schema rejects a missing field, a wrong type and an
    unknown value of a closed enumeration;
  - the field tables of this document equal the models, and every example in this document validates.

## 9. Decisions

None are left. The 28 points of this section were the open questions of the proposal, and the approval of
2026-10-02 settled every one of them as chosen (decision P2). The heading of the section is kept as it was,
because `tests/test_schema_v1.py` asserts that line; what the section holds is the list of decisions. The
heading and its assertion are renamed together by an item that owns the test (P28; P28 did not, so it is
FX-21's now, with the move of the P28 models into `MODELS`). Points 11 and 13 say below
what SP-1 to SP-3 changed.

### Decisions taken (approved on 2026-10-02)

Each point is a choice this document makes, and each stands. "Alternative" is what was not chosen. A change
to one of them is a change of the contract and follows the versioning rule of section 3.

1. **References in an Event.** An Event carries ids (`tournament_id`, `season_id`, `category_id`) and, for its
   two sides, id and name. Alternative: embed the full Tournament, Season and Participant objects. Reason for
   the choice: the catalog row of an event holds exactly these values, so a list of events needs no join; a
   consumer that wants more joins by id.
2. **`quality.settlement`.** `01-storage.md` 8.3 defines three states (open, provisional, final) and left it
   to this document whether `open` is exposed. It is: `settlement` is given next to the boolean `provisional`
   that `00-platform.md` names. The rule already uses the status class (the "from ST-27 on" form of 8.3),
   although the refresh policy itself looks at the status only after ST-27. Alternative: only `provisional`.
3. **Fields of `quality` beyond the four of `00-platform.md`.** Added: `source` (event or listing),
   `settlement`, `stale`, `status_regressed`. All four exist in the catalog today.
4. **A record whose read time is unknown is `final`, not "unknown".** 2.x records stored before observation
   times existed have `observed_at_utc` null and `settlement` `final`, as `01-storage.md` 8.3 says. A consumer
   that wants to treat them with care can test `observed_at_utc` for null. Alternative: a fourth value
   `unknown`, or `provisional: null`.
5. **Time format.** Strings in ISO 8601 UTC for every `_utc` field, epoch seconds for `change_ts`, and no
   second representation of the same time (`start_utc` only, no `start_ts`). `LiveEvent.ts` has milliseconds,
   where the example of `02-services.md` 5.1 shows whole seconds; the stream log stores milliseconds, and a
   consumer that measures delay needs them.
6. **`change_ts` 0 becomes null.** SofaScore sends `changeTimestamp: 0` for an event it has not changed yet.
   The schema gives null, because 0 is not a time.
7. **Shape of the score.** `score` is always an object, also for a match that has not started and for a
   sport without a mapping; the headline score is inside it (`score.home`, `score.away`); pairs are objects
   `{home, away}`, not two-element arrays. Alternative: `score: null` before kick-off; `home_score` and
   `away_score` on the Event.
8. **Football field names.** `half_time`, `regulation`, `after_extra_time`, `penalties`. The goals of each
   half of extra time and of extra time alone are not mapped.
9. **Period numbers.** `periods[].number` counts within the format, so a game of two halves has periods 1
   and 2 although SofaScore's keys are `period2` and `period4`. Alternative: keep SofaScore's key.
10. **No `retired` and `walkover` flags in the tennis score.** The 2.x score sheet has them; they repeat
    `status.code` 92 and 91, and walkover is not a tennis concept (basketball has it too). They are told by
    `status` and by the class `decided_without_play`.
11. **`aggregate` is a field of the Event, filled for football only.** `00-platform.md` lists the aggregate
    with the Event. The code reads `aggregated` only in the football family; SofaScore also sends it for
    handball and for two-legged basketball ties. Those get it when their family reads it (SP-1 for handball).
    As built since SP-1 (PR #112): handball fills `aggregate` too. Its sheet reads `aggregated` and
    `aggregatedWinnerCode`, and the mapper already took the aggregate from any sheet. Basketball still does
    not, and the field stays where it is.
12. **`winner` is `home`, `away` or `draw`,** not SofaScore's codes 1, 2, 3. Any other code is null.
13. **Participant types.** 0, 1, 2 map to `team`, `player`, `pair`, verified on the stored samples only for
    football, basketball and tennis; darts and MMA (persons as well) come with SP-3. The two persons of a pair
    (`subTeams`) and squad players (a different id space) are not in version 1. Since SP-3 (PR #118) the
    mapping is verified on one full payload each for five more sports: darts and MMA type 1 (`player`),
    baseball, cricket and e-sports type 0 (`team`).
14. **The key `status.class`.** `class` is a reserved word in several languages; generated client code may
    need an alias. Alternative: `status_class` on the Event.
15. **The name `stage`** for SofaScore's non-unique tournament object. Alternative: leave it out of the
    contract; but it is the only place that says "Group A" or "Qualifying".
16. **Slices.** A Slice names its owner as `owner_kind` and `owner_id` (not `event_id`), so the same record
    serves season and tournament slices. Its payload is raw: version 1 has no normalized model for
    statistics, lineups, incidents and the other slices (the first version of `01-storage.md` 2.3 gave that
    job to the schema layer; it is corrected). The plan gives SC-1 the entities and the Event, and P28 the
    odds and non-match models; a normalized model per slice is a later, additive item. The counts of empty
    answers and the stored sizes are not exposed.
17. **Change.** `fields[].path` and the old and new values are SofaScore's (`homeScore.normaltime`), not
    normalized names. `seconds_after_start` replaces the 2.x field `hours_after_start` (durations are in
    seconds). The tournament's name, which the 2.x line carries, is dropped (the id is there).
18. **LiveEvent.** Version 1 fixes the envelope and leaves `data` to P23 (table above; P23, PR #91, settled
    it as "LiveEvent data" states). The model is called
    LiveEvent although the same envelope carries the `change`, `job` and `system` streams. `type` keeps its
    stream prefix (`live.status_changed`), where `00-platform.md` writes `status_changed`.
19. **Where `schema_version` appears.** Not in every record; in `describe`, in the JSON Schema, and in the
    container that delivers records. How API v1 and the exports state it is for P21 and SC-2. The id is
    `sofascore.data/1`. A version 2 is served next to version 1 for at least one release; whether it also
    needs a new API prefix is decided by the item that introduces version 2.
20. **Open and closed enumerations** as listed in section 2, rule 8.
21. **No bookkeeping times on entities.** Tournament, Season, Category and Participant have no "updated at",
    and a Season does not say whether it is the current one or where it stands in SofaScore's list.
22. **Null, never omitted.** Every field is always present (section 2, rule 2). Alternative: omit null
    fields to save bytes.
23. **Tabular exports.** Not defined here. SC-2 starts from: a column per leaf field, the path joined with
    `_` (`status_class`, `score_home`, `quality_observed_at_utc`), lists as JSON text or as child tables in
    the SQLite export.
24. **A Category has no read method in the Store.** The catalog has the table, and the mapper takes its row;
    the Store's read API returns tournaments, seasons and participants but no categories. Plan item ST-22
    adds the method, before P21 needs it for `/tournaments`.
25. **Country codes as given.** `EN` for England and whatever SofaScore sends, not converted to ISO 3166.
26. **`Sport.id` can be null.** The registry knows slugs, not SofaScore's numeric ids; the id is filled from
    stored payloads.
27. **English only.** SofaScore's `fieldTranslations` are not exposed.
28. **Raw.** The raw form of an event is the event object, not the `{"event": …}` wrapper: this is what 2.x
    stores in `basic.json`, and the design text does not say which of the two the v3 layout stores. A raw
    request for a slice without a stored payload answers "not found". The Store's writers (ST-20) and P21
    implement both.
