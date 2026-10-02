import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BAD_ANSWER, NETWORK, newRequestId, queryString, request, session, v1, V1Error } from '@/api/v1/client'
import { describeError, errorText, fieldErrors, holderText, KNOWN_CODES, toastError } from '@/api/v1/errors'
import { authNeeded } from '@/lib/auth'
import { authLockedUntil, lockSecondsLeft, noteAuthLock } from '@/app/session'
import { clearToasts, uiToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import en from '@/locales/en'
import { callsTo, json, mockFetch } from './helpers'
import { job, status, v1Error } from './v1'

const t = i18n.global.t

beforeEach(() => {
  setLocale('en')
  authNeeded.value = false
  authLockedUntil.value = 0
  clearToasts()
})
afterEach(() => localStorage.clear())

describe('requests', () => {
  it('go to /api/v1 only, with a request id of their own and the session cookie', async () => {
    const f = mockFetch({ 'GET /api/v1/status': { data: status() } })
    const s = await v1.status()
    expect(s.version).toBe('3.0.0')
    const [url, init] = f.mock.calls[0]
    expect(url).toBe('/api/v1/status')
    const headers = init!.headers as Record<string, string>
    expect(headers['X-Request-Id']).toMatch(/^[A-Za-z0-9._-]{1,64}$/)
    expect(init!.credentials).toBe('same-origin')
  })

  it('make every request id unique and acceptable to the server', () => {
    const ids = new Set(Array.from({ length: 200 }, newRequestId))
    expect(ids.size).toBe(200)
    for (const id of ids) expect(id).toMatch(/^[A-Za-z0-9._-]{1,64}$/)
  })

  it('send repeated query parameters for lists and leave empty ones out', async () => {
    expect(queryString({ state: ['running', 'failed'], kind: null, limit: 25, cursor: '' })).toBe('?state=running&state=failed&limit=25')
    const f = mockFetch({ 'GET /api/v1/jobs': { data: [job()], page: { limit: 5, next_cursor: 'abc' } } })
    const page = await v1.jobs({ limit: 5, state: ['running'] })
    expect(page.page.next_cursor).toBe('abc')
    expect(String(f.mock.calls[0][0])).toBe('/api/v1/jobs?limit=5&state=running')
  })

  it('send JSON bodies for writes', async () => {
    const f = mockFetch({ 'POST /api/v1/jobs': json({ data: job({ state: 'running' }) }, 202), 'PATCH /api/v1/settings': { data: { settings: [] } } })
    await v1.startJob({ kind: 'refresh', spec: {} })
    await v1.updateSettings({ 'client.rate': 3 })
    expect(JSON.parse(String(callsTo(f, 'POST /api/v1/jobs')[0][1]!.body))).toEqual({ kind: 'refresh', spec: {} })
    expect(JSON.parse(String(callsTo(f, 'PATCH /api/v1/settings')[0][1]!.body))).toEqual({ values: { 'client.rate': 3 } })
  })

  it('point the event stream at the job and resume behind a sequence number', () => {
    expect(v1.jobEventsUrl('01J/X')).toBe('/api/v1/jobs/01J%2FX/events')
    expect(v1.jobEventsUrl('A', 41)).toBe('/api/v1/jobs/A/events?after=41')
  })
})

describe('the v1 error model, handled by code', () => {
  it('reads code, details and the request id of a v1 error', async () => {
    mockFetch({ 'POST /api/v1/jobs': () => v1Error(409, 'job_running', { holder: { lease: 'writer', purpose: 'headless', pid: 77, host: 'srv-2' } }) })
    const e = await v1.startJob({ kind: 'sync', spec: {} }).catch((x) => x)
    expect(e).toBeInstanceOf(V1Error)
    expect([e.status, e.code, e.requestId]).toEqual([409, 'job_running', 'req-123abc'])
    expect(holderText(e)).toBe('headless · pid 77 · on srv-2')
  })

  it('turns a refused connection into the network code, with the id it sent', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
    const e = await v1.status().catch((x) => x)
    expect(e.code).toBe(NETWORK)
    expect(e.status).toBe(0)
    expect(describeError(e).text).toBe(t('ui.error.network'))
    expect(describeError(e).requestId).toBeNull()
  })

  it('reads an answer that is not the v1 body without guessing', async () => {
    mockFetch({
      'GET /api/v1/a': () => new Response('<html>proxy</html>', { status: 502, statusText: 'Bad Gateway' }),
      'GET /api/v1/b': () => new Response('nope', { status: 418 }),
      'GET /api/v1/c': () => new Response('not json', { status: 200 }),
    })
    expect((await request('GET', '/api/v1/a').catch((x) => x)).code).toBe('internal')
    expect((await request('GET', '/api/v1/b').catch((x) => x)).code).toBe(BAD_ANSWER)
    expect((await request('GET', '/api/v1/c').catch((x) => x)).code).toBe(BAD_ANSWER)
  })

  it('has a translated sentence for every code of the error table, in both languages', () => {
    for (const code of KNOWN_CODES) {
      const e = new V1Error(400, code, 'English', null, 'r1')
      expect(describeError(e).text, code).toBe(en.ui.error[code])
    }
    setLocale('tr')
    expect(describeError(new V1Error(409, 'job_running', 'x', null, 'r')).text).not.toBe(en.ui.error.job_running)
  })

  it('shows the server text only for a code it does not know, under a general sentence', () => {
    const v = describeError(new V1Error(400, 'brand_new_code', 'Something new happened.', null, 'r9'))
    expect(v.text).toBe(t('ui.error.unknown'))
    expect(v.detail).toBe('Something new happened.')
    expect(v.requestId).toBe('r9')
    // a known code never shows the English message
    expect(errorText(new V1Error(409, 'job_running', 'English only', null, 'r'))).not.toContain('English only')
  })

  it('sends upstream refusals to Health and storage errors with the store text', () => {
    expect(describeError(new V1Error(503, 'blocked', 'x', null, 'r')).toHealth).toBe(true)
    const storage = describeError(new V1Error(507, 'storage_error', 'x', { store_message: 'disk full' }, 'r'))
    expect([storage.text, storage.detail]).toEqual([t('ui.error.storage_error'), 'disk full'])
  })

  it('maps the fields of a refused body', () => {
    const e = new V1Error(422, 'invalid_request', 'x', { errors: [{ loc: ['body', 'values', 'client.rate'], message: 'must be at most 1000', type: 'value_error' }] }, 'r')
    expect(fieldErrors(e)).toEqual({ 'client.rate': 'must be at most 1000' })
    expect(fieldErrors(new V1Error(409, 'job_running', 'x', null, 'r'))).toEqual({})
  })

  it('a conflict toast links to the job that holds the data folder', () => {
    toastError(new V1Error(409, 'job_running', 'x', null, 'r'), 'JOB1')
    expect(uiToasts.value[0].kind).toBe('error')
    expect(uiToasts.value[0].link?.to).toBe('/jobs/JOB1')
    toastError(new V1Error(503, 'rate_limited', 'x', null, 'r'))
    expect(uiToasts.value[1].link?.to).toBe('/system/health')
  })
})

describe('401: the token prompt and the lock after too many attempts', () => {
  it('any 401 asks for the token; other errors do not', async () => {
    mockFetch({ 'GET /api/v1/jobs': () => v1Error(500, 'internal'), 'GET /api/v1/status': () => v1Error(401, 'unauthorized') })
    await v1.jobs().catch(() => {})
    expect(authNeeded.value).toBe(false)
    await v1.status().catch(() => {})
    expect(authNeeded.value).toBe(true)
  })

  it('too_many_attempts on v1 (401 with Retry-After) locks the prompt for that long', async () => {
    mockFetch({ 'GET /api/v1/status': () => v1Error(401, 'unauthorized', { reason: 'too_many_attempts', retry_after: 27 }, { 'Retry-After': '27' }) })
    const before = Date.now()
    const e = await v1.status().catch((x) => x)
    expect(e.retryAfter).toBe(27)
    expect(describeError(e).text).toBe(t('ui.error.tooManyWait', { n: 27 }))
    expect(lockSecondsLeft(before)).toBeGreaterThanOrEqual(27)
    expect(lockSecondsLeft(before + 28000)).toBe(0)
  })

  it('reads the legacy session routes too (429 too_many_attempts, 401 invalid_token)', async () => {
    mockFetch({
      'POST /api/auth/login': () =>
        new Response(JSON.stringify({ detail: { code: 'too_many_attempts', message: 'x', retry_after: 30 } }), { status: 429, headers: { 'Retry-After': '30' } }),
    })
    const e = await session.login('some-value').catch((x) => x)
    expect([e.code, e.retryAfter]).toEqual(['too_many_attempts', 30])
    noteAuthLock(e.retryAfter, 1000)
    expect(lockSecondsLeft(1000)).toBe(30)
  })
})
