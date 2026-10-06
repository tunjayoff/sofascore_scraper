// Generated from docs/api/openapi-v1.json by scripts/gen-api-types.mjs. Do not edit by hand:
// run `npm run gen:api` after the document changes (tests/apiTypes.test.ts fails until then).
/* eslint-disable */

/** API version of the document: 1 */
export const API_DOCUMENT_VERSION = "1"

/** The aggregate score of a two-legged tie, as shown with this leg. */
export interface Aggregate {
  /** Aggregate score of this event's home side. */
  home: number | null
  /** Aggregate score of this event's away side. */
  away: number | null
  /** Who won the tie. */
  winner: "home" | "away" | "draw" | null
}

/** The error object of every `/api/v1` error response. */
export interface ApiError {
  /** Machine-readable error code; clients translate by this code. */
  code: string
  /** English text for logs and developers; never localised. */
  message: string
  /** Code-specific details. */
  details?: Record<string, unknown> | null
  /** Id of the request; also sent in the X-Request-Id header. */
  request_id: string
}

export interface ApiErrorResponse {
  error: ApiError
}

/** The access token, sent once by the web UI in exchange for a session cookie. */
export interface AuthLogin {
  token: string
}

export interface AuthResponse {
  data: AuthState
}

/** Whether the server asks for an access token, and whether this caller presents a valid one. */
export interface AuthState {
  /** An access token is configured. */
  required: boolean
  /** This request carries the token or a valid session cookie. */
  authenticated: boolean
}

export interface BackupJobSpec {
  scope?: "all" | "state" | "data" | "config" | "seasons" | "matches" | "match_details"
  /** Also `.env` (it can hold secrets); the file name says so. */
  include_env?: boolean
}

export interface BackupListResponse {
  data: BackupRecord[]
  page: PageInfo
}

/** A backup zip in the data directory's `backups/`. */
export interface BackupRecord {
  name: string
  /** all, state, data, config, seasons, matches or match_details. */
  scope: string
  /** The time in the file name, as UTC. */
  created_at_utc?: string | null
  bytes: number
  /** 2 (with backup.json), 1 (2.x), null: unreadable zip. */
  format?: number | null
  /** The zip holds `.env`, which can carry secrets. */
  with_env: boolean
}

/** Whether SofaScore answers the requests of this process. */
export interface BridgeHealth {
  state: "ok" | "degraded" | "blocked"
  consecutive_failures: number
  last_success_at?: string | null
  last_failure_at?: string | null
  failing_since?: string | null
  changed_at?: string | null
  last_error?: BridgeLastError | null
  thresholds: Record<string, number>
}

export interface BridgeLastError {
  /** challenge, forbidden or browser. */
  kind: string
  detail?: string | null
  /** ISO-8601, UTC. */
  at?: string | null
}

/** Optional features this server can offer. */
export interface Capabilities {
  /** Parquet exports (the optional package pyarrow is installed). */
  parquet: boolean
  /** Server-sent event streams of jobs (the package sse-starlette is installed). */
  sse: boolean
  /** The in-app scheduler runs inside this server. */
  scheduler: boolean
}

/** A country or region, or a tour such as ATP, that groups tournaments. */
export interface Category {
  /** SofaScore's category id. */
  id: number
  /** Slug of the sport the category belongs to. */
  sport: string | null
  /** Name of the category, in English. */
  name: string | null
  /** SofaScore's slug of the category. */
  slug: string | null
  /** SofaScore's two-letter code of the category's country, as given (mostly ISO 3166-1 alpha-2; SofaScore uses `EN` for England). Null for a category that is not a country, such as ATP. */
  country_code: string | null
}

/** A change of an already stored event that a later read found. */
export interface Change {
  /** Sequence number of the change log. Increases by one per change; pass the last one seen to read the next changes. */
  seq: number
  /** When the platform found the change. */
  recorded_at_utc: string
  /** Id of the Event. */
  event_id: number
  /** Slug of the sport. */
  sport: string | null
  /** Id of the Tournament. */
  tournament_id: number | null
  /** Scheduled start of the event after the change. */
  start_utc: string | null
  /** How long after the scheduled start SofaScore made the change: its change time, or the time the platform found the change when SofaScore gave none, minus the start. */
  seconds_after_start: number | null
  /** Status class before. */
  old_status_class: "not_started" | "live" | "completed" | "decided_without_play" | "void" | "unknown" | null
  /** Status class after. */
  new_status_class: "not_started" | "live" | "completed" | "decided_without_play" | "void" | "unknown" | null
  /** SofaScore's change time before. */
  old_change_ts: number | null
  /** SofaScore's change time after. */
  new_change_ts: number | null
  /** True when the event went from completed to void. */
  status_regressed: boolean
  /** As `Event.quality.tier_hint`, at the time of the change. */
  tier_hint: boolean | null
  /** The fields that changed, ordered by path. Compared are the status triple, the winner code, the start time and every score field. */
  fields: ChangedField[]
}

export interface ChangeListResponse {
  data: Change[]
  page: PageInfo
}

/** One field that changed between two reads of an event. */
export interface ChangedField {
  /** Path of the field in SofaScore's event object: `status.type`, `status.code`, `status.description`, `winnerCode`, `startTimestamp`, `homeScore.<key>`, `awayScore.<key>`. */
  path: string
  /** Value before, as SofaScore gave it. Null when the field did not exist. */
  old: unknown
  /** Value after, as SofaScore gave it. Null when the field no longer exists. */
  new: unknown
}

export interface ClearJobSpec {
  scope?: "all" | "events" | "schedules" | "seasons" | "match_details" | "matches"
  /** Must be true: the stored data of the scope is deleted. */
  confirm?: boolean
}

/** One innings of one side in cricket. */
export interface CricketInnings {
  /** The side that batted. */
  side: "home" | "away"
  /** Number of the innings of this side, starting at 1. */
  number: number
  /** Runs scored. */
  runs: number | null
  /** Wickets lost. */
  wickets: number | null
  /** Overs bowled, in SofaScore's notation: the digit after the point counts balls, so 68.1 is 68 overs and one ball. */
  overs: number | null
}

/** Score family `cricket`: the innings of both sides with runs, wickets and overs. */
export interface CricketScore {
  /** Always `cricket`. */
  family: "cricket"
  /** Headline score of the home side: runs of all its innings. */
  home: number | null
  /** Headline score of the away side: runs of all its innings. */
  away: number | null
  /** The innings of both sides, the home side's first, each side's in its own order. SofaScore numbers each side's innings separately and does not say which side batted first. */
  innings: CricketInnings[]
}

/** What the data directory holds, counted from its catalog. */
export interface DataSummary {
  /** Matches count only finished events or events with details. */
  only_finished: boolean
  matches: number
  details: number
  seasons: number
  /** Events stored in the old layout; `ssc migrate` moves them. */
  legacy_events: number
  /** Set when the catalog does not describe the files; the counts are then partial. */
  catalog_rebuild_reason?: string | null
  tournaments: TournamentSummary[]
  disk?: DiskSummary | null
}

export interface DiagnosticsResponse {
  /** The diagnostics summary; the same as diagnostics.json of the bundle. */
  data: Record<string, unknown>
}

/** Disk use of the data directory in bytes; a measurement may be up to a minute old. */
export interface DiskSummary {
  /** Every top-level entry of the data directory. */
  entries: Record<string, number>
  seasons: number
  matches: number
  details: number
  datasets: number
  /** seasons + matches + details + datasets. */
  total: number
  measured_at_utc?: string | null
}

/** A match. */
export interface Event {
  /** SofaScore's event id. */
  id: number
  /** Slug of the sport. Null when no stored payload says it. */
  sport: string | null
  /** Id of the Category. */
  category_id: number | null
  /** Id of the Tournament. Null for an event without a unique tournament. */
  tournament_id: number | null
  /** Id of the Season. */
  season_id: number | null
  /** The part of the tournament. Null when neither id nor name is known. */
  stage: Stage | null
  /** The round. Null when the event has no round information. */
  round: Round | null
  /** Scheduled start. For tennis this is the planned time, not the first point. */
  start_utc: string | null
  /** Status: SofaScore's triple and the platform's class. */
  status: EventStatus
  /** The two sides. */
  participants: EventParticipants
  /** The score, in the structure of the sport's score family. */
  score: FootballScore | PeriodsScore | SetsScore | InningsScore | CricketScore | FightScore | PlainScore
  /** Who won. Null while undecided and when SofaScore names no winner. */
  winner: "home" | "away" | "draw" | null
  /** Aggregate of a two-legged tie. Null for every other event. */
  aggregate: Aggregate | null
  /** SofaScore's slug of the event. */
  slug: string | null
  /** SofaScore's short id of the pairing, used in its page addresses. */
  custom_id: string | null
  /** Provenance and reliability of the record. */
  quality: Quality
}

/** An event (schema v1 Event); with `include=slices_summary` also the summary of its slices. */
export interface EventListItem {
  /** SofaScore's event id. */
  id: number
  /** Slug of the sport. Null when no stored payload says it. */
  sport: string | null
  /** Id of the Category. */
  category_id: number | null
  /** Id of the Tournament. Null for an event without a unique tournament. */
  tournament_id: number | null
  /** Id of the Season. */
  season_id: number | null
  /** The part of the tournament. Null when neither id nor name is known. */
  stage: Stage | null
  /** The round. Null when the event has no round information. */
  round: Round | null
  /** Scheduled start. For tennis this is the planned time, not the first point. */
  start_utc: string | null
  /** Status: SofaScore's triple and the platform's class. */
  status: EventStatus
  /** The two sides. */
  participants: EventParticipants
  /** The score, in the structure of the sport's score family. */
  score: FootballScore | PeriodsScore | SetsScore | InningsScore | CricketScore | FightScore | PlainScore
  /** Who won. Null while undecided and when SofaScore names no winner. */
  winner: "home" | "away" | "draw" | null
  /** Aggregate of a two-legged tie. Null for every other event. */
  aggregate: Aggregate | null
  /** SofaScore's slug of the event. */
  slug: string | null
  /** SofaScore's short id of the pairing, used in its page addresses. */
  custom_id: string | null
  /** Provenance and reliability of the record. */
  quality: Quality
  /** Present when the list was asked with `include=slices_summary`; null otherwise. */
  slices_summary?: SliceSummary | null
}

export interface EventListResponse {
  data: EventListItem[]
  page: PageInfo
}

/** A side of an event. */
export interface EventParticipant {
  /** Id of the Participant. */
  id: number | null
  /** Name of the participant at the time the event was read. */
  name: string | null
}

/** Both sides of an event. */
export interface EventParticipants {
  /** The home side (the first-named side). Null when neither its id nor its name is known. */
  home: EventParticipant | null
  /** The away side (the second-named side). Null when neither its id nor its name is known. */
  away: EventParticipant | null
}

export interface EventResponse {
  data: Event
}

export interface EventSliceResponse {
  data: Slice
}

/** The status of an event: SofaScore's triple and the platform's class. */
export interface EventStatus {
  /** SofaScore's status type. */
  type: string | null
  /** SofaScore's status code, for example 100 (ended), 110 (after extra time), 120 (after penalties), 91 (walkover), 92 (retired). */
  code: number | null
  /** SofaScore's status text, in English, for example `Ended`, `2nd half`. */
  description: string | null
  /** The platform's class of the status. `not_started`: not begun. `live`: in progress, breaks included (half time, the night between two days of a cricket match: type `willcontinue`). `completed`: played and finished. `decided_without_play`: finished by walkover or retirement. `void`: postponed, cancelled, interrupted, suspended or abandoned. `unknown`: none of these; never silently treated as completed. */
  class: "not_started" | "live" | "completed" | "decided_without_play" | "void" | "unknown"
}

/**
 * Which records to export; the fields are combined with AND, an empty field filters nothing. The meaning is that
 * of the filters of `GET /events` (and of `GET /changes` for the changes dataset).
 */
export interface ExportFilter {
  sport?: string | null
  tournament_ids?: number[]
  /** Not for the legacy-wide-csv profile or the changes dataset. */
  season_ids?: number[]
  event_ids?: number[]
  /** Only events in these status classes. Not for the legacy-wide-csv profile or the changes dataset. */
  status_classes?: ("not_started" | "live" | "completed" | "decided_without_play" | "void" | "unknown")[]
  /** At or after; ISO 8601 date or date-time (UTC without an offset). The start of the event; for the changes dataset the time the change was recorded. Not for the legacy-wide-csv profile. */
  from?: string | null
  /** At or before, as `from`; a date includes the whole day. */
  to?: string | null
}

/**
 * What to export. Schema `normalized`: the records of data schema v1 (`events`, `slices`, `changes`, `odds`: one
 * row per outcome of each odds snapshot, `standings`: the standings rows of the matched events' seasons) as JSONL
 * (one record per line), CSV, Parquet or SQLite (one column per leaf field, named by its path joined with `_`;
 * lists as JSON text). Parquet needs the optional package pyarrow on the server (501 `not_supported` without
 * it). Schema `raw`: the stored payloads as JSONL (dataset `events`: the event payload only, `slices`: every
 * slice). The profile `legacy-wide-csv` is 2.x's wide CSV (dataset `events`, format `csv`).
 */
export interface ExportJobSpec {
  dataset?: "events" | "slices" | "changes" | "odds" | "standings"
  format?: "csv" | "jsonl" | "parquet" | "sqlite"
  schema?: "normalized" | "raw"
  profile?: "legacy-wide-csv" | null
  filter?: ExportFilter
}

export interface ExportListResponse {
  data: ExportRecord[]
  page: PageInfo
}

/** An export: the job that writes it and, once it has succeeded, its file. */
export interface ExportRecord {
  /** Id of the export, the id of its job. */
  id: string
  job_id: string
  /** State of the job; the file can be downloaded when `succeeded`. */
  state: JobState
  dataset: string
  format: string
  schema: string
  profile?: string | null
  filter: ExportFilter
  /** ISO-8601, UTC. */
  created_at?: string | null
  /** ISO-8601, UTC. */
  finished_at?: string | null
  /** Records (rows or lines) written; for a raw export the payloads written. */
  rows?: number | null
  /** Events with at least one exported payload. */
  events?: number | null
  bytes?: number | null
  /** Payloads that could not be read and were left out. */
  skipped?: number | null
  /** File name in the data directory's `exports/`. */
  file?: string | null
  media_type?: string | null
  /** Version of the data schema of the records; null for a raw export and for the legacy-wide-csv profile. */
  schema_version?: number | null
  /** The file can be downloaded. */
  available: boolean
}

/** Score family `fight`: no score; how the fight was decided and in which round (MMA). The winner is the event's `winner`. */
export interface FightScore {
  /** Always `fight`. */
  family: "fight"
  /** Headline score of the home side; SofaScore gives none for a fight, so null. */
  home: number | null
  /** Headline score of the away side; SofaScore gives none for a fight, so null. */
  away: number | null
  /** How the fight was decided, as SofaScore abbreviates it, for example `UD` (unanimous decision), `SD` (split decision), `TKO`, `SUB` (submission); text, not an enumeration of the platform. Null while undecided. */
  method: string | null
  /** The round in which the fight ended. Null while undecided. */
  final_round: number | null
}

/** A new follow. Without a config file a tournament follow is written to config/leagues.txt (name and sport). */
export interface FollowCreate {
  kind?: "tournament" | "team" | "player" | "event"
  entity_id: number
  name: string
  sport?: string | null
  seasons?: string | number[]
  /** Data selection (slice keys or groups, see GET /sports/{slug}): null = the defaults (`defaults.slices` and `slices.<sport>` of GET /settings); `{"include": [...]}` = only these; `{"enable": [...], "disable": [...]}` = changes to the defaults. What is not selected is never fetched. Not available for a follow kept in config/leagues.txt (no config file). */
  slices?: Record<string, string[]> | null
  live?: boolean
  enabled?: boolean
}

export interface FollowListResponse {
  data: FollowRecord[]
  page: PageInfo
}

/**
 * Fields to change; a field left out stays. `sport: null` clears the stored sport, `slices: null` returns to
 * the defaults.
 */
export interface FollowPatch {
  name?: string | null
  sport?: string | null
  seasons?: string | number[] | null
  /** Data selection (slice keys or groups, see GET /sports/{slug}): null = the defaults (`defaults.slices` and `slices.<sport>` of GET /settings); `{"include": [...]}` = only these; `{"enable": [...], "disable": [...]}` = changes to the defaults. What is not selected is never fetched. */
  slices?: Record<string, string[]> | null
  live?: boolean | null
  enabled?: boolean | null
}

/** Something the platform downloads and watches: a tournament, a team, a player or one event. */
export interface FollowRecord {
  /** `<kind>:<entity_id>`. */
  id: string
  kind: "tournament" | "team" | "player" | "event"
  /** SofaScore's id of the followed entity. */
  entity_id: number
  name: string
  /** Stored sport, else (tournaments) the one its events show. */
  sport?: string | null
  /** `all`, `current`, `last:N` or a list of season ids. */
  seasons: string | number[]
  /** Data selection (slice keys or groups, see GET /sports/{slug}): null = the defaults (`defaults.slices` and `slices.<sport>` of GET /settings); `{"include": [...]}` = only these; `{"enable": [...], "disable": [...]}` = changes to the defaults. What is not selected is never fetched. */
  slices?: Record<string, string[]> | null
  /** The live service watches it (`ssc watch`). */
  live: boolean
  enabled: boolean
  /** legacy: config/leagues.txt; config: the config file (read-only here); api: added here. */
  origin: "legacy" | "config" | "api"
  position: number
  /** Fields PATCH can change on this follow. */
  writable: string[]
  created_at_utc?: string | null
  updated_at_utc?: string | null
}

export interface FollowResponse {
  data: FollowRecord
}

/** Score family `football`: goals by stage of the match. */
export interface FootballScore {
  /** Always `football`. */
  family: "football"
  /** Headline score of the home side: goals including extra time, without the penalty shoot-out. */
  home: number | null
  /** Headline score of the away side: goals including extra time, without the penalty shoot-out. */
  away: number | null
  /** Goals in the first half. */
  half_time: ScorePair | null
  /** Goals after 90 minutes. */
  regulation: ScorePair | null
  /** Goals after extra time (cumulative, without the shoot-out). Null unless the match went to extra time. */
  after_extra_time: ScorePair | null
  /** Goals of the penalty shoot-out alone. */
  penalties: ScorePair | null
}

export interface Health {
  status: "ok"
  version: string
  api_version: "v1"
  bridge: BridgeHealth
  throttle: ThrottleStatus
}

export interface HealthResponse {
  data: Health
}

/** Runs of one inning. */
export interface InningScore {
  /** Number of the inning, starting at 1; extra innings go on after 9. */
  number: number
  /** Runs of the home side in the inning. */
  home: number | null
  /** Runs of the away side in the inning. */
  away: number | null
}

/** Score family `innings`: runs by inning, with hits and errors (baseball). */
export interface InningsScore {
  /** Always `innings`. */
  family: "innings"
  /** Headline score of the home side: runs, extra innings included. */
  home: number | null
  /** Headline score of the away side: runs, extra innings included. */
  away: number | null
  /** Runs of each inning that has a score, in order. SofaScore gives the innings in `innings`; some leagues also give them as `period1` to `period9`, with the same values. An inning missing from `innings` is read from `periodN`. */
  innings: InningScore[]
  /** Runs after the scheduled innings. Null when SofaScore does not give it. */
  regulation: ScorePair | null
  /** Runs scored in extra innings alone. Null without extra innings. */
  extra_innings: ScorePair | null
  /** Hits of each side in the whole game. */
  hits: ScorePair | null
  /** Errors of each side in the whole game. */
  errors: ScorePair | null
}

/** A job: a download, a refresh or a data operation, started by any face in any process. */
export interface Job {
  id: string
  kind: JobKind
  state: JobState
  origin: JobOrigin
  /** The service spec the job was started with. */
  spec: Record<string, unknown>
  /** Phase, counters and failed items; the last progress event for a job of another process. */
  progress?: Record<string, unknown> | null
  result?: Record<string, unknown> | null
  error?: JobError | null
  /** ISO-8601, UTC. */
  created_at?: string | null
  /** ISO-8601, UTC. */
  started_at?: string | null
  /** ISO-8601, UTC. */
  finished_at?: string | null
  /** Epoch milliseconds; written every 5 s while running. */
  heartbeat_at?: number | null
  cancel_requested?: boolean
}

/** The error that stopped the job, or made it partial. */
export interface JobError {
  /** A code of the error table, e.g. blocked, rate_limited, storage_error. */
  code: string
  message: string
  details?: Record<string, unknown> | null
}

/** İşin ne yaptığı. Bugünkü web işi `FETCH` türündedir. */
export type JobKind = "sync" | "fetch" | "refresh" | "export" | "backup" | "restore" | "clear" | "migrate" | "rebuild"

export interface JobListResponse {
  data: Job[]
  page: PageInfo
}

/** Who started the job. */
export interface JobOrigin {
  face: "cli" | "api" | "scheduler" | "library"
  pid?: number | null
  host?: string | null
}

export interface JobResponse {
  data: Job
}

/** One followed tournament and, optionally, the seasons or events of it to work on. */
export interface JobSelection {
  league_id: number
  /** Read by `sync` only. */
  season_ids?: number[]
  /** Read by `fetch` only. */
  match_ids?: number[]
}

/** İşin durumu. `QUEUED` ve `RUNNING` dışındakiler son durumdur. */
export type JobState = "queued" | "running" | "succeeded" | "partial" | "failed" | "cancelled" | "interrupted"

/** A lease of the data directory that is held right now. */
export interface LeaseHolder {
  /** writer, live, sinks, maintenance or watcher:<sport>. */
  name: string
  purpose?: string
  pid?: number | null
  host?: string | null
  since_utc?: string | null
}

/** The live service of the data directory (`ssc watch`): its state only, never live data. */
export interface LiveStatus {
  /** A live service holds the `live` lease right now, in any process. */
  running: boolean
  pid?: number | null
  host?: string | null
  /** page, direct or poll; null when not running. */
  source?: string | null
  /** The sports it watches; empty when not running. */
  sports?: string[]
  /** Epoch seconds of the last heartbeat, also of a run that has ended. */
  heartbeat_at?: number | null
  /** SofaScore refuses the service right now. */
  blocked?: boolean
  /** Sport to the source that leads it now (page, direct or poll). */
  leaders?: Record<string, string>
  /** The last change of a leading source. */
  last_switch?: Record<string, unknown> | null
}

export interface LogEntry {
  time: string
  level: string
  pid: number
  logger: string
  /** With secrets masked; a traceback follows its line. */
  message: string
}

/** The newest entries of the log file, oldest first. */
export interface LogTail {
  /** False when file logging is off; `entries` is then empty. */
  enabled: boolean
  file?: string | null
  /** The level the server logs at. */
  level?: string | null
  min_level?: string | null
  count: number
  entries: LogEntry[]
}

export interface LogTailResponse {
  data: LogTail
}

/** The odds of an event from one provider as read at one moment. */
export interface Odds {
  /** Id of the Event. */
  event_id: number
  /** Odds slice the record comes from: `odds_all` or `odds_featured`. */
  key: string
  /** SofaScore's id of the bookmaker the odds come from. Which bookmakers SofaScore offers depends on the country it sees the request from; the platform stores no address or location of the machine. */
  provider_id: number | null
  /** When the odds were read. A read is a snapshot: odds change until the event ends, and only a later read shows a later price. */
  fetched_at_utc: string | null
  /** The markets, in SofaScore's order. */
  markets: OddsMarket[]
}

/** One outcome of a betting market and its price. */
export interface OddsChoice {
  /** Name of the outcome as SofaScore gives it, for example `1`, `X`, `2`, `Over`. */
  name: string
  /** Current price as a fraction, for example `11/5`. */
  fractional: string | null
  /** Current price as a decimal (1 + the fraction), rounded to three places. */
  decimal: number | null
  /** Opening price as a fraction. */
  initial_fractional: string | null
  /** Opening price as a decimal, rounded to three places. */
  initial_decimal: number | null
  /** Direction of the last change of the price: 1 up, -1 down, 0 none. */
  change: number | null
  /** True for the outcome that won once the event is settled; null while open or when SofaScore does not say. */
  winning: boolean | null
}

export interface OddsListResponse {
  data: Odds[]
  page: PageInfo
}

/** One betting market of an event with its outcomes. */
export interface OddsMarket {
  /** SofaScore's id of the market type (1 is the match result). */
  market_id: number | null
  /** Name of the market, for example `Full time`. */
  name: string | null
  /** Group of the market, for example `1X2` or `Home/Away`. */
  group: string | null
  /** Part of the event the market covers, for example `Full-time`. */
  period: string | null
  /** Line of a market with several lines, for example `2.5` for over/under; null for a market with one line. */
  choice_group: string | null
  /** Name under which the featured odds list the market (`default`, `fullTime`, `asian`); null in the full list. */
  label: string | null
  /** True when the prices were offered during play. */
  is_live: boolean | null
  /** True when the market was closed for bets at the time of the read. */
  suspended: boolean | null
  /** The outcomes of the market, in SofaScore's order. */
  choices: OddsChoice[]
}

/** Cursor pagination of a collection response. */
export interface PageInfo {
  /** Maximum number of items in this page. */
  limit: number
  /** Pass as `cursor` to get the next page; null on the last page. */
  next_cursor?: string | null
}

/** Points of one period. */
export interface PeriodScore {
  /** Position of the period within the format, starting at 1: quarter 1 to 4, half 1 to 2, or period 1 to 3. */
  number: number
  /** Points of the home side in the period (goals in the goal sports). */
  home: number | null
  /** Points of the away side in the period (goals in the goal sports). */
  away: number | null
}

/** Score family `periods`: points by period (basketball, American football, Aussie rules, ice hockey, handball, rugby, futsal, minifootball, floorball). */
export interface PeriodsScore {
  /** Always `periods`. */
  family: "periods"
  /** Headline score of the home side: points including overtime. */
  home: number | null
  /** Headline score of the away side: points including overtime. */
  away: number | null
  /** How regulation time is divided. Null while no period score exists. */
  format: "quarters" | "halves" | "thirds" | null
  /** Points of each period of regulation time that has a score, in order. */
  periods: PeriodScore[]
  /** Points at the end of regulation time. */
  regulation: ScorePair | null
  /** Points scored in overtime alone. Null without overtime. */
  overtime: ScorePair | null
  /** Final points including overtime. */
  final: ScorePair | null
  /** Goals of the penalty shoot-out alone. Null without a shoot-out, and always null for basketball. In the one recorded handball shoot-out, `final` and the headline score include these goals. */
  penalties: ScorePair | null
}

/** The score of an event whose sport has no score family: only the headline score. */
export interface PlainScore {
  /** Always null: the sport has no score mapping. */
  family: null
  /** Headline score of the home side, as SofaScore displays it. */
  home: number | null
  /** Headline score of the away side, as SofaScore displays it. */
  away: number | null
}

/** How much the record can be trusted, and where it comes from. */
export interface Quality {
  /** `event`: the record derives from the stored `/event/{id}` payload. `listing`: the event is known only from a schedule page (a fixture, or a match whose details were never fetched); slices do not exist. */
  source: "event" | "listing"
  /** When the platform read the `/event/{id}` payload the record derives from. Null for a `listing` record and for a record stored by a version that did not note the time. */
  observed_at_utc: string | null
  /** SofaScore's own time of its last change to the event. Carried as given; compare it for equality or order to detect a change. */
  change_ts: number | null
  /** `open`: the event has not reached a terminal status, or has no `/event/{id}` payload. `provisional`: terminal status, but it was last read before start time plus the refresh window, so the result may still be corrected. `final`: it was read after the window closed (or the time of the read or the start time is unknown, or the refresh policy is off); the platform will not read it again by itself. */
  settlement: "open" | "provisional" | "final"
  /** True exactly when `settlement` is `provisional`. */
  provisional: boolean
  /** True when SofaScore offers player statistics for the event or its tournament, which marks the better covered competitions (their results are corrected sooner). False when both flags are false, null when neither is given. */
  tier_hint: boolean | null
  /** True when a schedule page read later than the event payload disagrees with it in status, winner, start time or a score field. The next refresh reads the event again. */
  stale: boolean
  /** True when the event was stored as completed and a later read showed it as void. */
  status_regressed: boolean
}

export interface RebuildJobSpec {
  mode?: "auto" | "in_place" | "recreate"
}

/** Without `event_ids`: the stored records that are due (of one tournament with `league_id`). */
export interface RefreshJobSpec {
  league_id?: number | null
  /** Read `/event` of these events again, whether or not they are due (the Fetch again button). Not with `league_id`. */
  event_ids?: number[]
}

export interface RestoreJobSpec {
  /** A backup of `/backups`. */
  name: string
  /** Move the current data to the trash first (`.meta/trash/`); needed when the data directory is not empty. With `dry_run`: report what that would move. */
  force?: boolean
  /** True: only check (nothing is written). False: restore the backup. */
  dry_run?: boolean
}

/** The round of an event. */
export interface Round {
  /** Number of the round. */
  number: number | null
  /** Name of the round, for example `Quarterfinals`. League rounds have none. */
  name: string | null
  /** SofaScore's slug of the round. */
  slug: string | null
}

/** The in-app scheduler of this server (`ssc serve --scheduler`); off by default. */
export interface ScheduleStatus {
  /** The scheduler runs inside this server. */
  enabled: boolean
  /** Empty when the scheduler is off. */
  next_runs?: ScheduledRun[]
}

/** One task of the in-app scheduler (config file `[[schedule.task]]`) and its next run. */
export interface ScheduledRun {
  /** Position of the task in the config file, from 1. */
  index: number
  /** sync, fetch, refresh or backup. */
  run: string
  /** The interval as written (`6h`); null for a cron task. */
  every?: string | null
  /** The cron expression (local time); null for an interval. */
  cron?: string | null
  /** The task's options (`league_id`, `scope`). */
  options?: Record<string, unknown>
  next_run_at_utc: string
  /** When the task was last due; null before. */
  last_run_at_utc?: string | null
  /** The last job the task started. */
  last_job_id?: string | null
  /** `skipped_running`: its previous run still ran; `skipped_busy`: another job or data operation held the data directory. */
  last_result?: "started" | "skipped_running" | "skipped_busy" | "failed_to_start" | null
}

/** A score of both sides. */
export interface ScorePair {
  /** Value of the home side. */
  home: number | null
  /** Value of the away side. */
  away: number | null
}

/** One edition of a tournament. */
export interface Season {
  /** SofaScore's season id. */
  id: number
  /** Id of the Tournament the season belongs to. */
  tournament_id: number
  /** Name of the season, for example `Premier League 26/27`. */
  name: string | null
  /** The season's year text as SofaScore writes it: `26/27`, `2025`. */
  year: string | null
}

export interface SeasonListResponse {
  data: Season[]
  page: PageInfo
}

export interface SeasonResponse {
  data: Season
}

export interface SeasonSliceListResponse {
  data: Slice[]
  page: PageInfo
}

/** One set. */
export interface SetScore {
  /** Number of the set, starting at 1. */
  number: number
  /** Games the home side won in the set; points when the set is a match tie-break. */
  home: number | null
  /** Games the away side won in the set; points when the set is a match tie-break. */
  away: number | null
  /** Points of the set's tie-break. Null when the set had none. */
  tiebreak: ScorePair | null
}

/** Score family `sets`: sets won and the score of each set. Tennis and padel count games per set; volleyball, badminton and table tennis count points; darts played in sets counts legs per set; snooker, darts played in legs only and e-sports give only the frames, legs or games won and no sets. */
export interface SetsScore {
  /** Always `sets`. */
  family: "sets"
  /** Headline score of the home side: sets won. */
  home: number | null
  /** Headline score of the away side: sets won. */
  away: number | null
  /** What the score counts. `games`, `points`, `legs`: sets won, and each set counts games (tennis, padel), points (volleyball, badminton, table tennis) or legs (darts played in sets). `frames`, `legs_won`, `games_won`: no sets; `sets_won` and the headline score are the frames (snooker), legs (darts played in legs only) or games (e-sports) won. Null when the record has no score sheet. */
  format: "games" | "points" | "frames" | "legs" | "legs_won" | "games_won" | null
  /** Sets won by each side. */
  sets_won: ScorePair | null
  /** The sets that have a score, in order. */
  sets: SetScore[]
  /** True when the deciding set was a match tie-break (first to 10 points) and not a normal set. A heuristic: the last of three or five sets has a side with 10 or more. */
  match_tiebreak: boolean
}

export interface Setting {
  /** `section.key`, as in the config file. */
  key: string
  /** The value in force. Secrets are masked. */
  value: unknown
  /** The layer the value comes from, weakest to strongest in this order. */
  source: "default" | "dotenv" | "overrides" | "file" | "env" | "flag"
  /** The file or the environment variable, when there is one. */
  source_name: string
  /** Pinned by the config file, the environment or a flag: a change made here would have no effect. */
  locked: boolean
  /** Whether PATCH accepts this key right now. */
  writable: boolean
  secret: boolean
}

/** How a setting can be changed (05-web-ui.md G4). One row per setting, in the order of `settings`. */
export interface SettingMetadata {
  /** `section.key`, as in the config file. */
  key: string
  /** The config file section (`[client]`, `[defaults]` ...). */
  section: string
  /** `rate`: requests per second, 0 or "off" for no limit; `seasons`: current, all, last:N or a list of season ids; `string_list`: a list of strings. */
  type: "string" | "path" | "url" | "integer" | "number" | "boolean" | "choice" | "string_list" | "rate" | "seasons"
  /** What the setting does, in English. */
  description: string
  /** Lowest accepted value, when there is one. */
  minimum?: number | null
  /** The value must be above `minimum`, not equal to it. */
  exclusive_minimum?: boolean
  /** Highest accepted value, when there is one. */
  maximum?: number | null
  /** Accepted values of a `choice` setting. */
  choices?: string[] | null
  /** Longest accepted text when changed through the API. */
  max_length?: number | null
  /** A change takes effect only after `ssc serve` or `ssc watch` is started again. */
  restart_needed: boolean
}

export interface SettingsDocument {
  /** Path of the config file in use, if any. */
  config_file?: string | null
  /** Path of the file PATCH has written, if any. */
  overrides_file?: string | null
  settings: Setting[]
  /** Type, limits, choices, section and restart need of each setting, in the order of `settings`. */
  metadata: SettingMetadata[]
  /** Per-sport changes to the default slice selection ([slices.<sport>]), one row per registered sport in registry order. Change one with PATCH `{"values": {"slices.<sport>": {"enable": [...], "disable": [...]}}}`; null removes it. */
  slices: SportSliceSelection[]
}

export interface SettingsPatch {
  /** `section.key` to the new value; null removes the value written here earlier. `slices.<sport>` takes `{"enable": [...], "disable": [...]}`. */
  values: Record<string, unknown>
}

export interface SettingsResponse {
  data: SettingsDocument
}

export interface SinkListResponse {
  data: SinkStatus[]
  page: PageInfo
}

/** A configured output sink and how far it has delivered the event log. */
export interface SinkStatus {
  name: string
  /** stdout, file or webhook. */
  type: string
  /** The file path, or the webhook address with its credentials masked. */
  target?: string | null
  /** Event type patterns the sink takes. */
  events: string[]
  /** `pending`: the sink has delivered nothing yet; `error`: the last delivery failed. */
  state: "ok" | "error" | "pending"
  /** A process holds the `sinks` lease and delivers right now. */
  served: boolean
  /** Sequence number of the last event delivered to the sink. */
  cursor: number
  /** Sequence number of the newest event of the log. */
  head_seq: number
  /** Events of the log after the cursor (all types, before the sink's filter). */
  lag_events: number
  /** Age of the oldest undelivered event; null when none. */
  lag_seconds?: number | null
  last_delivered_at_utc?: string | null
  last_error?: string | null
  /** Undelivered events given up as too old, as far as the retained log tells. */
  dropped: number
}

/** One stored response of SofaScore about an event or another entity, and its state. */
export interface Slice {
  /** What the slice belongs to. */
  owner_kind: string
  /** Id of the owner; for `event` the event id. */
  owner_id: number
  /** Name of the slice, for example `event`, `statistics`, `lineups`, `incidents`. */
  key: string
  /** Sub-key for a slice that has several payloads per owner, for example the round of a schedule page. Null when the slice has one payload. */
  sub: string | null
  /** `ok`: a payload with data is stored. `empty`: SofaScore answered that it has no such data (404, or a response without content). `error`: the last attempt failed and it is unknown whether data exists. `not_requested`: the platform has not asked for it. */
  state: "ok" | "empty" | "error" | "not_requested"
  /** True when a payload is stored. A slice in state `error` can still hold the payload of an earlier successful read. */
  has_payload: boolean
  /** When the stored payload was read. */
  fetched_at_utc: string | null
  /** When the slice was last asked for, whatever the outcome. */
  checked_at_utc: string | null
  /** The last failure. Null unless the state is `error`. */
  error: SliceError | null
  /** The stored SofaScore response, unchanged (see Raw on request). Present only when asked for; null otherwise. */
  payload: unknown
}

/** The last failed attempt to fetch a slice. */
export interface SliceError {
  /** Why it failed. */
  reason: string
  /** HTTP status of the failed response, when there was one. */
  http_status: number | null
  /** When the attempt failed. */
  at_utc: string | null
  /** How many attempts in a row have failed. */
  count: number
}

export interface SliceListResponse {
  data: Slice[]
  page: PageInfo
}

export interface SliceResponse {
  data: Slice
}

/** How much of an event's selected data is stored. */
export interface SliceSummary {
  /** Slices selected for the event's sport and phase. */
  selected: number
  /** Selected slices with data. */
  ok: number
  /** Selected slices SofaScore answered without data. */
  empty: number
  /** Selected slices whose last request failed. */
  error: number
}

export interface Sport {
  slug: string
  /** SofaScore's English name. */
  name: string
  /** Translation key of the name for clients. */
  i18n_key: string
  score_family: string
  /** Every slice that applies to the sport, disabled ones included. */
  slices: SportSlice[]
}

export interface SportListResponse {
  data: Sport[]
  page: PageInfo
}

export interface SportResponse {
  data: Sport
}

export interface SportSlice {
  key: string
  /** Path of the slice in SofaScore's API, with `{event_id}`. */
  path: string
  /** Counts for completeness in this sport: a match missing it is fetched again. False: requested when selected, but SofaScore does not always have it for this sport. */
  required: boolean
  /** Selected when no selection names it (the registry's default). */
  default_enabled: boolean
  /** Selected by the configured defaults for this sport (`defaults.slices` and `slices.<sport>` of GET /settings); a follow can choose otherwise. */
  selected: boolean
  /** Slice group; a selection can name the group instead of the key. */
  group: string
  /** What the payload belongs to; one request per owner. */
  owner: "event" | "season" | "tournament" | "team" | "player" | "sport"
  /** Match phases in which the slice can exist (before, during, after the match). */
  phases: ("pre" | "live" | "post")[]
  /** Changed payloads are kept as a history (odds). */
  keep_history: boolean
  /** Owner slices: fetched again when older than this; null = once. */
  max_age_seconds?: number | null
}

export interface SportSliceSelection {
  /** Registered sport slug. */
  sport: string
  /** Slice keys or groups added to `defaults.slices` for this sport. */
  enable: string[]
  /** Slice keys or groups removed for this sport (applied after `enable`). */
  disable: string[]
  /** The layer the sport's selection comes from; `default` when none is set. */
  source: "default" | "dotenv" | "overrides" | "file" | "env" | "flag"
  source_name: string
  /** Pinned by the config file or the environment ([slices.<sport>]). */
  locked: boolean
  /** Whether PATCH accepts `slices.<sport>` right now. */
  writable: boolean
}

/** The part of a tournament an event belongs to: SofaScore's (non-unique) tournament object. */
export interface Stage {
  /** SofaScore's id of the stage. */
  id: number | null
  /** Name of the stage, for example `UEFA Champions League, Group A` or `Wimbledon, London, GB, Qualifying, 1st - 2nd Round`. */
  name: string | null
}

export interface StandingsResponse {
  data: StandingsRow[]
  page: PageInfo
}

/** One row of a standings table of a season. */
export interface StandingsRow {
  /** Id of the Tournament. */
  tournament_id: number
  /** Id of the Season. */
  season_id: number
  /** Which table: `total` (all matches) or `home` (home matches only). */
  table: string
  /** Name of the table or group, for example `Premier League 26/27` or `Group A`. */
  group_name: string | null
  /** Rank in the table, 1 for the first. */
  position: number | null
  /** Id of the Participant. */
  participant_id: number | null
  /** Name of the Participant. */
  participant_name: string | null
  /** Matches played. */
  matches: number | null
  /** Matches won. */
  wins: number | null
  /** Matches drawn; null in sports without draws. */
  draws: number | null
  /** Matches lost. */
  losses: number | null
  /** Goals or points scored. */
  scores_for: number | null
  /** Goals or points conceded. */
  scores_against: number | null
  /** Table points (a fraction in a few sports). */
  points: number | null
  /** When the table was read. */
  fetched_at_utc: string | null
}

/** Write a backup zip into the data directory's `backups/`; download it through `/backups/{name}`. */
export interface StartBackupJob {
  kind: "backup"
  spec?: BackupJobSpec
}

/** Delete stored data (follows, job history, change log, backups and exports stay). */
export interface StartClearJob {
  kind: "clear"
  spec?: ClearJobSpec
}

/** Write an export file into the data directory's `exports/`; download it through `/exports/{id}/download`. */
export interface StartExportJob {
  kind: "export"
  spec?: ExportJobSpec
}

/** Event details only, for the schedules that are already stored. */
export interface StartFetchJob {
  kind: "fetch"
  spec?: SyncJobSpec
}

/** Rebuild the catalog (`.meta/catalog.db`) from the stored files. */
export interface StartRebuildJob {
  kind: "rebuild"
  spec?: RebuildJobSpec
}

/** Re-read the stored events that may still change. */
export interface StartRefreshJob {
  kind: "refresh"
  spec?: RefreshJobSpec
}

/**
 * Check what restoring a backup would do (`dry_run: true`, the Check step of the UI), or restore it
 * (`dry_run: false`, the Choose step). A restore replaces the data and the job history; this job stays in it.
 */
export interface StartRestoreJob {
  kind: "restore"
  spec: RestoreJobSpec
}

/** Season lists, schedules and event details. */
export interface StartSyncJob {
  kind: "sync"
  spec?: SyncJobSpec
}

export interface Status {
  version: string
  api_version: "v1"
  /** Version of the normalized schema of the records this API returns (`sofascore.data/<n>`). */
  schema_version: number
  /** Whether an access token is configured. */
  auth_required: boolean
  bridge: BridgeHealth
  throttle: ThrottleStatus
  /** The job that runs on the data directory right now, in any process. */
  active_job?: Job | null
  /** The live service; null when the store cannot be read. */
  live?: LiveStatus | null
  /** Null when the store cannot be read. */
  summary?: DataSummary | null
  /** Leases held right now, in any process. */
  leases?: LeaseHolder[]
  /** The in-app scheduler and the next runs of its tasks. */
  schedule: ScheduleStatus
  capabilities: Capabilities
  /** Error code when the data directory could not be read; the fields that need it are empty. */
  storage_error?: string | null
}

/** The outcome of one connection check; a failed check is a result, not an error. */
export interface StatusCheck {
  ok: boolean
  /** Why the check failed; null when it succeeded. */
  reason?: "blocked" | "browser" | "rate_limited" | "network" | "upstream" | null
  /** English text; clients translate by `reason`. */
  message: string
  /** Live events in the answer; null on failure. */
  events_count?: number | null
  checked_at_utc: string
  bridge: BridgeHealth
}

/** What to check. */
export interface StatusCheckRequest {
  /** `sofascore`: one request for the live list of football. */
  target: "sofascore"
}

export interface StatusCheckResponse {
  data: StatusCheck
}

export interface StatusResponse {
  data: Status
}

/**
 * What to download. Without `selections`, `league_id` and `follows`: every enabled tournament follow, each with
 * its own season choice. Only one of `league_id`, `selections` and `follows` may be given.
 */
export interface SyncJobSpec {
  /** One tournament, every season of it. */
  league_id?: number | null
  selections?: JobSelection[]
  /** `sync` only: these follows (`tournament:17`), each with its season choice. Team, player and event follows cannot be synced yet (400). */
  follows?: string[]
  /** `sync` only. `seasons`: read the season lists from SofaScore again now, without schedules or event details (the Get season list button). */
  only?: "seasons" | null
  /** `fetch` only: these events, whether or not their tournament is known or followed. Not with `league_id` or `selections`. */
  event_ids?: number[]
}

/** The request budget shared by all processes of this machine. */
export interface ThrottleStatus {
  enabled: boolean
  requests_per_second: number
  shared: boolean
  error?: string | null
}

/** A tournament SofaScore found. */
export interface TournamentHit {
  id: number
  name: string
  slug?: string | null
  /** Slug of a registered sport; null for others. */
  sport?: string | null
  category: TournamentHitCategory
  /** A follow of any origin names the tournament already. */
  followed: boolean
}

export interface TournamentHitCategory {
  id?: number | null
  name?: string | null
  slug?: string | null
  /** SofaScore's country code, as given. */
  country_code?: string | null
}

export interface TournamentHitListResponse {
  data: TournamentHit[]
  page: PageInfo
}

export interface TournamentListResponse {
  data: TournamentRecord[]
  page: PageInfo
}

/** A tournament (schema v1 Tournament) with its category and whether it is followed. */
export interface TournamentRecord {
  /** SofaScore's unique-tournament id. */
  id: number
  /** Slug of the sport. */
  sport: string | null
  /** Id of the tournament's Category. */
  category_id: number | null
  /** Name of the tournament, in English. */
  name: string | null
  /** SofaScore's slug of the tournament. */
  slug: string | null
  /** The tournament's Category; null when the catalog does not know it. */
  category?: Category | null
  /** A follow of any origin names the tournament. */
  followed?: boolean
}

export interface TournamentResponse {
  data: TournamentRecord
}

/** What to look for on SofaScore. */
export interface TournamentSearch {
  /** Text of the tournament name. */
  q: string
  /** Only tournaments of this sport (slug). */
  sport?: string | null
}

/** Counts of one tournament. `tournament_id` null: the events without a unique tournament. */
export interface TournamentSummary {
  tournament_id: number | null
  /** Name of the follow, else the stored tournament name. */
  name?: string | null
  /** A tournament of the configured leagues. */
  followed: boolean
  /** Events counted as matches (the `only_finished` rule of the summary). */
  matches: number
  /** Events with a stored event payload. */
  details: number
  /** Every stored event, unfinished schedule rows included. */
  events: number
  finished: number
  /** Seasons in the tournament's stored season list. */
  seasons: number
  seasons_with_events: number
  /** details / matches in percent, one decimal; 0 without matches. */
  coverage: number
  /** Newest change of a stored payload. */
  last_update_utc?: string | null
}

/** Every operation of the document by its operationId. */
export interface Operations {
  /** Health of the server */
  "getHealth": {
    method: "GET"
    path: "/api/v1/health"
    params: {}
    query: {}
    body: never
    response: HealthResponse
  }
  /** Status of the service */
  "getStatus": {
    method: "GET"
    path: "/api/v1/status"
    params: {}
    query: {}
    body: never
    response: StatusResponse
  }
  /** Check the connection to SofaScore */
  "checkConnection": {
    method: "POST"
    path: "/api/v1/status/check"
    params: {}
    query: {}
    body: StatusCheckRequest
    response: StatusCheckResponse
  }
  /** List sports */
  "listSports": {
    method: "GET"
    path: "/api/v1/sports"
    params: {}
    query: {}
    body: never
    response: SportListResponse
  }
  /** Get a sport */
  "getSport": {
    method: "GET"
    path: "/api/v1/sports/{slug}"
    params: { slug: string }
    query: {}
    body: never
    response: SportResponse
  }
  /** List the output sinks */
  "listSinks": {
    method: "GET"
    path: "/api/v1/sinks"
    params: {}
    query: {}
    body: never
    response: SinkListResponse
  }
  /** Session state */
  "getAuth": {
    method: "GET"
    path: "/api/v1/auth"
    params: {}
    query: {}
    body: never
    response: AuthResponse
  }
  /** Sign in with the access token */
  "login": {
    method: "POST"
    path: "/api/v1/auth/login"
    params: {}
    query: {}
    body: AuthLogin
    response: AuthResponse
  }
  /** Sign out */
  "logout": {
    method: "POST"
    path: "/api/v1/auth/logout"
    params: {}
    query: {}
    body: never
    response: AuthResponse
  }
  /** List follows */
  "listFollows": {
    method: "GET"
    path: "/api/v1/follows"
    params: {}
    query: { kind?: "tournament" | "team" | "player" | "event" | null; origin?: "legacy" | "config" | "api" | null; enabled?: boolean | null; q?: string | null }
    body: never
    response: FollowListResponse
  }
  /** Follow something */
  "addFollow": {
    method: "POST"
    path: "/api/v1/follows"
    params: {}
    query: {}
    body: FollowCreate
    response: FollowResponse
  }
  /** Get a follow */
  "getFollow": {
    method: "GET"
    path: "/api/v1/follows/{follow_id}"
    params: { follow_id: string }
    query: {}
    body: never
    response: FollowResponse
  }
  /** Change a follow */
  "updateFollow": {
    method: "PATCH"
    path: "/api/v1/follows/{follow_id}"
    params: { follow_id: string }
    query: {}
    body: FollowPatch
    response: FollowResponse
  }
  /** Stop following */
  "removeFollow": {
    method: "DELETE"
    path: "/api/v1/follows/{follow_id}"
    params: { follow_id: string }
    query: {}
    body: never
    response: FollowResponse
  }
  /** List tournaments */
  "listTournaments": {
    method: "GET"
    path: "/api/v1/tournaments"
    params: {}
    query: { sport?: string | null; q?: string | null; followed?: boolean | null; limit?: number; cursor?: string | null }
    body: never
    response: TournamentListResponse
  }
  /** Search tournaments on SofaScore */
  "searchTournaments": {
    method: "POST"
    path: "/api/v1/tournaments/search"
    params: {}
    query: {}
    body: TournamentSearch
    response: TournamentHitListResponse
  }
  /** Get a tournament */
  "getTournament": {
    method: "GET"
    path: "/api/v1/tournaments/{tournament_id}"
    params: { tournament_id: number }
    query: {}
    body: never
    response: TournamentResponse
  }
  /** List the seasons of a tournament */
  "listTournamentSeasons": {
    method: "GET"
    path: "/api/v1/tournaments/{tournament_id}/seasons"
    params: { tournament_id: number }
    query: {}
    body: never
    response: SeasonListResponse
  }
  /** Get a season */
  "getSeason": {
    method: "GET"
    path: "/api/v1/seasons/{season_id}"
    params: { season_id: number }
    query: {}
    body: never
    response: SeasonResponse
  }
  /** List the slices of a season */
  "listSeasonSlices": {
    method: "GET"
    path: "/api/v1/seasons/{season_id}/slices"
    params: { season_id: number }
    query: {}
    body: never
    response: SeasonSliceListResponse
  }
  /** Get the standings of a season */
  "getSeasonStandings": {
    method: "GET"
    path: "/api/v1/seasons/{season_id}/standings"
    params: { season_id: number }
    query: { table?: "total" | "home" | null }
    body: never
    response: StandingsResponse
  }
  /** Get a slice of a season */
  "getSeasonSlice": {
    method: "GET"
    path: "/api/v1/seasons/{season_id}/slices/{key}"
    params: { season_id: number; key: string }
    query: { sub?: string }
    body: never
    response: SliceResponse
  }
  /** List events */
  "listEvents": {
    method: "GET"
    path: "/api/v1/events"
    params: {}
    query: { sport?: string | null; tournament?: number[] | null; season?: number[] | null; participant?: number[] | null; status?: ("not_started" | "live" | "completed" | "decided_without_play" | "void" | "unknown")[] | null; from?: string | null; to?: string | null; has?: "details" | "missing" | null; q?: string | null; followed?: boolean; sort?: "start_utc" | "-start_utc"; include?: "slices_summary"[] | null; limit?: number; cursor?: string | null }
    body: never
    response: EventListResponse
  }
  /** Get an event */
  "getEvent": {
    method: "GET"
    path: "/api/v1/events/{event_id}"
    params: { event_id: number }
    query: {}
    body: never
    response: EventResponse
  }
  /** List the slices of an event */
  "listEventSlices": {
    method: "GET"
    path: "/api/v1/events/{event_id}/slices"
    params: { event_id: number }
    query: {}
    body: never
    response: SliceListResponse
  }
  /** Get a slice of an event */
  "getEventSlice": {
    method: "GET"
    path: "/api/v1/events/{event_id}/slices/{key}"
    params: { event_id: number; key: string }
    query: { sub?: string }
    body: never
    response: EventSliceResponse
  }
  /** Get the stored event payload */
  "getEventRaw": {
    method: "GET"
    path: "/api/v1/events/{event_id}/raw"
    params: { event_id: number }
    query: {}
    body: never
    response: unknown
  }
  /** Get the stored payload of a slice */
  "getEventSliceRaw": {
    method: "GET"
    path: "/api/v1/events/{event_id}/slices/{key}/raw"
    params: { event_id: number; key: string }
    query: { sub?: string }
    body: never
    response: unknown
  }
  /** List the odds of an event */
  "listEventOdds": {
    method: "GET"
    path: "/api/v1/events/{event_id}/odds"
    params: { event_id: number }
    query: {}
    body: never
    response: SliceListResponse
  }
  /** Get the odds of an event, snapshot by snapshot */
  "listEventOddsSnapshots": {
    method: "GET"
    path: "/api/v1/events/{event_id}/odds/{key}"
    params: { event_id: number; key: string }
    query: { sub?: string | null; history?: boolean }
    body: never
    response: OddsListResponse
  }
  /** List recorded changes */
  "listChanges": {
    method: "GET"
    path: "/api/v1/changes"
    params: {}
    query: { since?: number; event_id?: number | null; tournament?: number[] | null; from?: string | null; to?: string | null; order?: "asc" | "desc"; limit?: number; cursor?: string | null }
    body: never
    response: ChangeListResponse
  }
  /** List jobs */
  "listJobs": {
    method: "GET"
    path: "/api/v1/jobs"
    params: {}
    query: { limit?: number; cursor?: string | null; state?: JobState[] | null; kind?: JobKind[] | null; origin?: ("cli" | "api" | "scheduler" | "library")[] | null; target?: string | null }
    body: never
    response: JobListResponse
  }
  /** Start a job */
  "startJob": {
    method: "POST"
    path: "/api/v1/jobs"
    params: {}
    query: {}
    body: StartSyncJob | StartFetchJob | StartRefreshJob | StartExportJob | StartBackupJob | StartClearJob | StartRebuildJob | StartRestoreJob
    response: JobResponse
  }
  /** Get a job */
  "getJob": {
    method: "GET"
    path: "/api/v1/jobs/{job_id}"
    params: { job_id: string }
    query: {}
    body: never
    response: JobResponse
  }
  /** Cancel a job */
  "cancelJob": {
    method: "POST"
    path: "/api/v1/jobs/{job_id}/cancel"
    params: { job_id: string }
    query: {}
    body: never
    response: JobResponse
  }
  /** Follow the events of a job (SSE) */
  "streamJobEvents": {
    method: "GET"
    path: "/api/v1/jobs/{job_id}/events"
    params: { job_id: string }
    query: { after?: number | null }
    body: never
    response: string
  }
  /** List exports */
  "listExports": {
    method: "GET"
    path: "/api/v1/exports"
    params: {}
    query: { limit?: number; cursor?: string | null }
    body: never
    response: ExportListResponse
  }
  /** Download an export */
  "downloadExport": {
    method: "GET"
    path: "/api/v1/exports/{export_id}/download"
    params: { export_id: string }
    query: {}
    body: never
    response: string
  }
  /** List backups */
  "listBackups": {
    method: "GET"
    path: "/api/v1/backups"
    params: {}
    query: {}
    body: never
    response: BackupListResponse
  }
  /** Download a backup */
  "downloadBackup": {
    method: "GET"
    path: "/api/v1/backups/{name}"
    params: { name: string }
    query: {}
    body: never
    response: string
  }
  /** Read the log */
  "listLogs": {
    method: "GET"
    path: "/api/v1/logs"
    params: {}
    query: { limit?: number; level?: "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL" | null }
    body: never
    response: LogTailResponse
  }
  /** Diagnostics summary */
  "getDiagnostics": {
    method: "GET"
    path: "/api/v1/diagnostics"
    params: {}
    query: {}
    body: never
    response: DiagnosticsResponse
  }
  /** Download the diagnostics bundle */
  "downloadDiagnosticsBundle": {
    method: "GET"
    path: "/api/v1/diagnostics/bundle"
    params: {}
    query: { log_lines?: number }
    body: never
    response: string
  }
  /** Get the settings */
  "getSettings": {
    method: "GET"
    path: "/api/v1/settings"
    params: {}
    query: {}
    body: never
    response: SettingsResponse
  }
  /** Change settings */
  "updateSettings": {
    method: "PATCH"
    path: "/api/v1/settings"
    params: {}
    query: {}
    body: SettingsPatch
    response: SettingsResponse
  }
}
