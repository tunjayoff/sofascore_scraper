// Generated from docs/api/openapi-v1.json by scripts/gen-api-types.mjs. Do not edit by hand:
// run `npm run gen:api` after the document changes (tests/apiTypes.test.ts fails until then).
/* eslint-disable */

/** API version of the document: 1 */
export const API_DOCUMENT_VERSION = "1"

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

/** Cursor pagination of a collection response. */
export interface PageInfo {
  /** Maximum number of items in this page. */
  limit: number
  /** Pass as `cursor` to get the next page; null on the last page. */
  next_cursor?: string | null
}

export interface RefreshJobSpec {
  league_id?: number | null
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

export interface SettingsDocument {
  /** Path of the config file in use, if any. */
  config_file?: string | null
  /** Path of the file PATCH has written, if any. */
  overrides_file?: string | null
  settings: Setting[]
}

export interface SettingsPatch {
  /** `section.key` to the new value; null removes the value written here earlier. */
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
  required: boolean
  default_enabled: boolean
}

/** Event details only, for the schedules that are already stored. */
export interface StartFetchJob {
  kind: "fetch"
  spec?: SyncJobSpec
}

/** Job kinds of the contract that cannot be started through the API yet; answered with 501 `not_supported`. */
export interface StartOtherJob {
  kind: "export" | "backup" | "clear" | "rebuild"
  spec?: Record<string, unknown>
}

/** Re-read the stored events that may still change. */
export interface StartRefreshJob {
  kind: "refresh"
  spec?: RefreshJobSpec
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

/** What to download. Without `selections` and `league_id`: every followed tournament. */
export interface SyncJobSpec {
  /** One tournament; not read when `selections` is given. */
  league_id?: number | null
  selections?: JobSelection[]
}

/** The request budget shared by all processes of this machine. */
export interface ThrottleStatus {
  enabled: boolean
  requests_per_second: number
  shared: boolean
  error?: string | null
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
  /** List jobs */
  "listJobs": {
    method: "GET"
    path: "/api/v1/jobs"
    params: {}
    query: { limit?: number; cursor?: string | null; state?: JobState[] | null; kind?: JobKind[] | null }
    body: never
    response: JobListResponse
  }
  /** Start a job */
  "startJob": {
    method: "POST"
    path: "/api/v1/jobs"
    params: {}
    query: {}
    body: StartSyncJob | StartFetchJob | StartRefreshJob | StartOtherJob
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
