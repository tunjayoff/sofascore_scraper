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
  /** Whether an access token is configured. */
  auth_required: boolean
  bridge: BridgeHealth
  throttle: ThrottleStatus
  /** The job that runs on the data directory right now, in any process. */
  active_job?: Job | null
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
