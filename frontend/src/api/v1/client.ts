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

/** A request id the server accepts (src/web/errors.py: [A-Za-z0-9._-]{1,64}). */
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

const enc = encodeURIComponent

/** The routes of `/api/v1` that exist today (05-web-ui.md 7.1). Each resolves with the `data` member. */
export const v1 = {
  health: (signal?: AbortSignal) => request<Op<'getHealth'>['response']>('GET', '/api/v1/health', { signal }).then((r) => r.data),
  status: (signal?: AbortSignal) => request<Op<'getStatus'>['response']>('GET', '/api/v1/status', { signal }).then((r) => r.data),
  sports: () => request<Op<'listSports'>['response']>('GET', '/api/v1/sports').then((r) => r.data),
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
  settings: (): Promise<Data<'getSettings'>> =>
    request<Op<'getSettings'>['response']>('GET', '/api/v1/settings').then((r) => r.data),
  updateSettings: (values: Record<string, unknown>): Promise<Data<'updateSettings'>> =>
    request<Op<'updateSettings'>['response']>('PATCH', '/api/v1/settings', { body: { values } }).then((r) => r.data),
}

/**
 * The session routes. `/api/v1/auth*` come with P21 (05-web-ui.md 7.2); until then the UI uses the
 * routes #43 added, which answer in the legacy shape (errorFrom reads both).
 */
export type AuthState = { required: boolean; authenticated: boolean }

export const session = {
  state: () => request<AuthState>('GET', '/api/auth'),
  /** Sends the token once; the server answers with an HttpOnly cookie, so the page never keeps it. */
  login: (token: string) => request<AuthState>('POST', '/api/auth/login', { body: { token } }),
  logout: () => request<AuthState>('POST', '/api/auth/logout'),
}
