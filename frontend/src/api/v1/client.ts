import { authNeeded } from '@/lib/auth'
import { noteAuthLock } from '@/app/session'
import type { Operations } from './schema'

/**
 * Client of `/api/v1` (docs/design/02-services.md section 6). The new web UI talks to nothing else: no
 * request goes to SofaScore and nothing is loaded from its domains (05-web-ui.md 5.1).
 *
 * Every error is a V1Error with the code of the error table; the screens translate by that code
 * (src/api/v1/errors.ts), never by the server's English `message`. Every request carries its own
 * `X-Request-Id`, so even a request that never got an answer has an id to show and to find in the log.
 */

/** Code of a request that got no answer at all (server down, connection refused, offline). */
export const NETWORK = 'network'
/** Code of an answer that is not the v1 error body (a proxy page, an old server). */
export const BAD_ANSWER = 'bad_answer'

export class V1Error extends Error {
  /** HTTP status; 0 when no answer came. */
  status: number
  code: string
  details: Record<string, unknown> | null
  requestId: string
  /** Seconds from `Retry-After` (or `details.retry_after`), when the server sent one. */
  retryAfter: number | null

  constructor(status: number, code: string, message: string, details: Record<string, unknown> | null, requestId: string, retryAfter: number | null = null) {
    super(message)
    this.name = 'V1Error'
    this.status = status
    this.code = code
    this.details = details
    this.requestId = requestId
    this.retryAfter = retryAfter
  }

  /** `details.reason`, e.g. 'too_many_attempts' on a 401. */
  get reason(): string | null {
    const r = this.details?.reason
    return typeof r === 'string' ? r : null
  }
}

let counter = 0

/** A request id the server accepts (sofascore_scraper/web/errors.py: [A-Za-z0-9._-]{1,64}). */
export function newRequestId(): string {
  counter = (counter + 1) % 1e6
  const random = Math.floor(Math.random() * 36 ** 6).toString(36).padStart(6, '0')
  return `ui-${Date.now().toString(36)}-${random}-${counter}`
}

function seconds(value: unknown): number | null {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? Math.ceil(n) : null
}

/** Reads an error answer of any shape into a V1Error. */
export async function errorFrom(res: Response, sentId: string): Promise<V1Error> {
  const requestId = res.headers.get('X-Request-Id') || sentId
  const retryHeader = seconds(res.headers.get('Retry-After'))
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    /* not JSON */
  }
  const error = (body as { error?: unknown } | null)?.error as Record<string, unknown> | undefined
  if (error && typeof error === 'object' && typeof error.code === 'string') {
    const details = error.details && typeof error.details === 'object' ? (error.details as Record<string, unknown>) : null
    return new V1Error(
      res.status,
      error.code,
      typeof error.message === 'string' ? error.message : '',
      details,
      typeof error.request_id === 'string' && error.request_id ? error.request_id : requestId,
      retryHeader ?? seconds(details?.retry_after),
    )
  }
  // The legacy routes the UI still needs (the session routes) answer {detail: {code, message, retry_after}}
  const detail = (body as { detail?: unknown } | null)?.detail as Record<string, unknown> | string | undefined
  if (detail && typeof detail === 'object' && typeof detail.code === 'string') {
    return new V1Error(res.status, detail.code, String(detail.message ?? ''), detail, requestId, retryHeader ?? seconds(detail.retry_after))
  }
  return new V1Error(res.status, res.status >= 500 ? 'internal' : BAD_ANSWER, res.statusText || '', null, requestId, retryHeader)
}

/** A 401 means this browser has no session: the token prompt covers the app (05-web-ui.md 5.3). */
function noteRefusal(e: V1Error) {
  if (e.status !== 401) return
  authNeeded.value = true
  if (e.reason === 'too_many_attempts' || e.code === 'too_many_attempts') noteAuthLock(e.retryAfter)
}

type Query = Record<string, string | number | boolean | readonly (string | number)[] | null | undefined>

export function queryString(query?: Query): string {
  if (!query) return ''
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v == null || v === '') continue
    if (Array.isArray(v)) for (const item of v) q.append(k, String(item))
    else q.append(k, String(v))
  }
  const s = q.toString()
  return s ? `?${s}` : ''
}

export type RequestOptions = { query?: Query; body?: unknown; signal?: AbortSignal }

/** One request; resolves with the JSON answer or rejects with a V1Error. */
export async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const id = newRequestId()
  const headers: Record<string, string> = { Accept: 'application/json', 'X-Request-Id': id }
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  let res: Response
  try {
    res = await fetch(path + queryString(opts.query), {
      method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      credentials: 'same-origin',
      signal: opts.signal,
    })
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') throw e
    throw new V1Error(0, NETWORK, String((e as Error)?.message || e), null, id)
  }
  if (!res.ok) {
    const e = await errorFrom(res, id)
    noteRefusal(e)
    throw e
  }
  if (res.status === 204) return undefined as T
  try {
    return (await res.json()) as T
  } catch {
    throw new V1Error(res.status, BAD_ANSWER, 'The answer is not JSON.', null, res.headers.get('X-Request-Id') || id)
  }
}

type Op<K extends keyof Operations> = Operations[K]
type Data<K extends keyof Operations> = Op<K>['response'] extends { data: infer D } ? D : never

export type ListJobsQuery = Op<'listJobs'>['query']
export type StartJobBody = Op<'startJob'>['body']
export type ListEventsQuery = Op<'listEvents'>['query']
export type ListChangesQuery = Op<'listChanges'>['query']
export type ListFollowsQuery = Op<'listFollows'>['query']
export type ListTournamentsQuery = Op<'listTournaments'>['query']
export type LogLevel = NonNullable<Op<'listLogs'>['query']['level']>

const enc = encodeURIComponent
/** A follow id `<kind>:<id>` as a path segment; the colon is allowed in a segment and kept readable. */
const encFollow = (id: string) => enc(id).replace(/%3A/gi, ':')

/** A stored payload exactly as the server keeps it, with what its headers say about it (6.6, raw view). */
export type RawPayload = {
  /** The body as text, unchanged: copy and download give exactly what the server sent. */
  text: string
  bytes: number
  etag: string | null
  /** `X-Sofascore-Fetched-At`: when the payload was read from SofaScore. */
  fetchedAt: string | null
}

/** GET of a raw payload; the body is kept as text, so a large payload is parsed once, by the viewer. */
export async function requestRaw(path: string, query?: Query, signal?: AbortSignal): Promise<RawPayload> {
  const id = newRequestId()
  let res: Response
  try {
    res = await fetch(path + queryString(query), {
      headers: { Accept: 'application/json', 'X-Request-Id': id },
      credentials: 'same-origin',
      signal,
    })
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') throw e
    throw new V1Error(0, NETWORK, String((e as Error)?.message || e), null, id)
  }
  if (!res.ok) {
    const e = await errorFrom(res, id)
    noteRefusal(e)
    throw e
  }
  const text = await res.text()
  return { text, bytes: new Blob([text]).size, etag: res.headers.get('ETag'), fetchedAt: res.headers.get('X-Sofascore-Fetched-At') }
}

const rawPath = (id: number, key?: string | null) => (!key || key === 'event' ? `/api/v1/events/${id}/raw` : `/api/v1/events/${id}/slices/${enc(key)}/raw`)

/** The routes of `/api/v1` (02-services.md section 6). Single resources resolve with `data`, lists with the page. */
export const v1 = {
  health: (signal?: AbortSignal) => request<Op<'getHealth'>['response']>('GET', '/api/v1/health', { signal }).then((r) => r.data),
  status: (signal?: AbortSignal) => request<Op<'getStatus'>['response']>('GET', '/api/v1/status', { signal }).then((r) => r.data),
  /** One request to SofaScore through the bridge, on click only (6.12). */
  checkConnection: (): Promise<Data<'checkConnection'>> =>
    request<Op<'checkConnection'>['response']>('POST', '/api/v1/status/check', { body: { target: 'sofascore' } }).then((r) => r.data),
  sports: () => request<Op<'listSports'>['response']>('GET', '/api/v1/sports').then((r) => r.data),
  sport: (slug: string, signal?: AbortSignal): Promise<Data<'getSport'>> =>
    request<Op<'getSport'>['response']>('GET', `/api/v1/sports/${enc(slug)}`, { signal }).then((r) => r.data),
  sinks: (signal?: AbortSignal) => request<Op<'listSinks'>['response']>('GET', '/api/v1/sinks', { signal }).then((r) => r.data),

  // ---- follows and the catalog ----
  follows: (query: ListFollowsQuery = {}, signal?: AbortSignal) =>
    request<Op<'listFollows'>['response']>('GET', '/api/v1/follows', { query, signal }),
  follow: (id: string, signal?: AbortSignal): Promise<Data<'getFollow'>> =>
    request<Op<'getFollow'>['response']>('GET', `/api/v1/follows/${encFollow(id)}`, { signal }).then((r) => r.data),
  addFollow: (body: Op<'addFollow'>['body']): Promise<Data<'addFollow'>> =>
    request<Op<'addFollow'>['response']>('POST', '/api/v1/follows', { body }).then((r) => r.data),
  updateFollow: (id: string, body: Op<'updateFollow'>['body']): Promise<Data<'updateFollow'>> =>
    request<Op<'updateFollow'>['response']>('PATCH', `/api/v1/follows/${encFollow(id)}`, { body }).then((r) => r.data),
  /** `deleteData`: a tournament follow's stored matches go too, by the clear job of the answer (FX-19). */
  removeFollow: (id: string, deleteData = false): Promise<Data<'removeFollow'>> =>
    request<Op<'removeFollow'>['response']>('DELETE', `/api/v1/follows/${encFollow(id)}`, { query: { delete_data: deleteData || null } }).then((r) => r.data),
  tournaments: (query: ListTournamentsQuery = {}, signal?: AbortSignal) =>
    request<Op<'listTournaments'>['response']>('GET', '/api/v1/tournaments', { query, signal }),
  tournament: (id: number, signal?: AbortSignal): Promise<Data<'getTournament'>> =>
    request<Op<'getTournament'>['response']>('GET', `/api/v1/tournaments/${id}`, { signal }).then((r) => r.data),
  /** A competitor the catalog knows from its stored matches: gender, national team, country (B1); 404 before. */
  team: (id: number, signal?: AbortSignal): Promise<Data<'getTeam'>> =>
    request<Op<'getTeam'>['response']>('GET', `/api/v1/teams/${id}`, { signal }).then((r) => r.data),
  /**
   * One request to SofaScore (5.1), unless the server still keeps the answer of the same text (10 minutes,
   * FX-20). Aborting it before the server sent it means it is never sent.
   */
  searchTournaments: (body: Op<'searchTournaments'>['body'], signal?: AbortSignal): Promise<Data<'searchTournaments'>> =>
    request<Op<'searchTournaments'>['response']>('POST', '/api/v1/tournaments/search', { body, signal }).then((r) => r.data),
  /** Stored tournaments and teams by name, for suggestions while typing; never SofaScore (FX-20). */
  suggestCatalog: (query: Op<'suggestCatalog'>['query'], signal?: AbortSignal): Promise<Data<'suggestCatalog'>> =>
    request<Op<'suggestCatalog'>['response']>('GET', '/api/v1/catalog/suggest', { query, signal }).then((r) => r.data),
  /** `counts`: each season with its stored counts (FX-13 `include=counts`). */
  tournamentSeasons: (id: number, signal?: AbortSignal, counts = false): Promise<Data<'listTournamentSeasons'>> =>
    request<Op<'listTournamentSeasons'>['response']>('GET', `/api/v1/tournaments/${id}/seasons`, { query: { include: counts ? ['counts'] : null }, signal }).then((r) => r.data),

  // ---- events ----
  events: (query: ListEventsQuery = {}, signal?: AbortSignal) =>
    request<Op<'listEvents'>['response']>('GET', '/api/v1/events', { query, signal }),
  event: (id: number, signal?: AbortSignal): Promise<Data<'getEvent'>> =>
    request<Op<'getEvent'>['response']>('GET', `/api/v1/events/${id}`, { signal }).then((r) => r.data),
  /** What the stored event payload says beyond the record: result note, series, venue, referee (FX-26). */
  eventExtra: (id: number, signal?: AbortSignal): Promise<Data<'getEventExtra'>> =>
    request<Op<'getEventExtra'>['response']>('GET', `/api/v1/events/${id}/extra`, { signal }).then((r) => r.data),
  eventSlices: (id: number, signal?: AbortSignal): Promise<Data<'listEventSlices'>> =>
    request<Op<'listEventSlices'>['response']>('GET', `/api/v1/events/${id}/slices`, { signal }).then((r) => r.data),
  /** One slice with its stored payload. */
  eventSlice: (id: number, key: string, sub?: string | null, signal?: AbortSignal): Promise<Data<'getEventSlice'>> =>
    request<Op<'getEventSlice'>['response']>('GET', `/api/v1/events/${id}/slices/${enc(key)}`, { query: { sub: sub || null }, signal }).then((r) => r.data),
  eventOdds: (id: number, signal?: AbortSignal): Promise<Data<'listEventOdds'>> =>
    request<Op<'listEventOdds'>['response']>('GET', `/api/v1/events/${id}/odds`, { signal }).then((r) => r.data),
  /** The normalized odds of one odds slice (P28): one record per provider and snapshot; `history: false` the latest only. */
  eventOddsSnapshots: (id: number, key: string, query: Op<'listEventOddsSnapshots'>['query'] = {}, signal?: AbortSignal): Promise<Data<'listEventOddsSnapshots'>> =>
    request<Op<'listEventOddsSnapshots'>['response']>('GET', `/api/v1/events/${id}/odds/${enc(key)}`, { query, signal }).then((r) => r.data),
  /** Address of a stored payload: the event's own (`key` empty or `event`) or a slice's; a full-size download. */
  rawUrl: (id: number, key?: string | null, sub?: string | null) => rawPath(id, key) + queryString({ sub: key && key !== 'event' ? sub || null : null }),
  raw: (id: number, key?: string | null, sub?: string | null, signal?: AbortSignal) =>
    requestRaw(rawPath(id, key), { sub: key && key !== 'event' ? sub || null : null }, signal),
  changes: (query: ListChangesQuery = {}, signal?: AbortSignal) =>
    request<Op<'listChanges'>['response']>('GET', '/api/v1/changes', { query, signal }),

  // ---- jobs ----
  jobs: (query: ListJobsQuery = {}, signal?: AbortSignal) =>
    request<Op<'listJobs'>['response']>('GET', '/api/v1/jobs', { query, signal }),
  job: (id: string, signal?: AbortSignal): Promise<Data<'getJob'>> =>
    request<Op<'getJob'>['response']>('GET', `/api/v1/jobs/${enc(id)}`, { signal }).then((r) => r.data),
  startJob: (body: StartJobBody): Promise<Data<'startJob'>> =>
    request<Op<'startJob'>['response']>('POST', '/api/v1/jobs', { body }).then((r) => r.data),
  cancelJob: (id: string): Promise<Data<'cancelJob'>> =>
    request<Op<'cancelJob'>['response']>('POST', `/api/v1/jobs/${enc(id)}/cancel`).then((r) => r.data),
  /** Address of the job's event stream (SSE); `after` resumes behind that sequence number. */
  jobEventsUrl: (id: string, after = 0) => `/api/v1/jobs/${enc(id)}/events${after > 0 ? `?after=${after}` : ''}`,

  // ---- files: exports, backups, the log, diagnostics ----
  exports: (query: Op<'listExports'>['query'] = {}, signal?: AbortSignal) =>
    request<Op<'listExports'>['response']>('GET', '/api/v1/exports', { query, signal }),
  exportUrl: (id: string) => `/api/v1/exports/${enc(id)}/download`,
  backups: (signal?: AbortSignal) => request<Op<'listBackups'>['response']>('GET', '/api/v1/backups', { signal }).then((r) => r.data),
  backupUrl: (name: string) => `/api/v1/backups/${enc(name)}`,
  logs: (query: Op<'listLogs'>['query'] = {}, signal?: AbortSignal): Promise<Data<'listLogs'>> =>
    request<Op<'listLogs'>['response']>('GET', '/api/v1/logs', { query, signal }).then((r) => r.data),
  diagnostics: (signal?: AbortSignal): Promise<Data<'getDiagnostics'>> =>
    request<Op<'getDiagnostics'>['response']>('GET', '/api/v1/diagnostics', { signal }).then((r) => r.data),
  diagnosticsBundleUrl: '/api/v1/diagnostics/bundle',

  settings: (): Promise<Data<'getSettings'>> =>
    request<Op<'getSettings'>['response']>('GET', '/api/v1/settings').then((r) => r.data),
  updateSettings: (values: Record<string, unknown>): Promise<Data<'updateSettings'>> =>
    request<Op<'updateSettings'>['response']>('PATCH', '/api/v1/settings', { body: { values } }).then((r) => r.data),
}

/** The session routes of v1 (6.15). */
export type AuthState = Data<'getAuth'>

export const session = {
  state: () => request<Op<'getAuth'>['response']>('GET', '/api/v1/auth').then((r) => r.data),
  /** Sends the token once; the server answers with an HttpOnly cookie, so the page never keeps it. */
  login: (token: string) => request<Op<'login'>['response']>('POST', '/api/v1/auth/login', { body: { token } }).then((r) => r.data),
  logout: () => request<Op<'logout'>['response']>('POST', '/api/v1/auth/logout').then((r) => r.data),
}
