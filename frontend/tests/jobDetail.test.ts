import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import JobDetailScreen from '@/screens/jobs/JobDetailScreen.vue'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { callsTo, flush, json, mockFetch } from './helpers'
import { axeViolations, FakeES, job, mountScreen, status, useFakeES, v1Error } from './v1'

const t = i18n.global.t
const ID = '01J9ZQ3M5XK8A0B1C2D3E4F5G6'
let w: VueWrapper

beforeEach(() => {
  setLocale('en')
  clearToasts()
  useFakeES()
})
afterEach(() => {
  w?.unmount()
  vi.useRealTimers()
})

const running = (over = {}) =>
  job({
    state: 'running',
    finished_at: null,
    result: null,
    heartbeat_at: Date.now(),
    progress: { phase: 'details', phase_index: 3, phase_count: 3, done: 812, total: 1400, percent: 58, eta_seconds: 250, failed_count: 0, failed: [] },
    ...over,
  })

async function open(answer: () => unknown, extra: Record<string, unknown> = {}) {
  const f = mockFetch({ [`GET /api/v1/jobs/${ID}`]: answer, 'GET /api/v1/status': { data: status() }, ...extra })
  ;({ w } = await mountScreen(JobDetailScreen, `/jobs/${ID}`, '/jobs/:id'))
  await flush()
  return f
}

const log = () => w.findAll('[data-testid="job-log"] li').map((li) => li.text())

describe('Job detail', () => {
  it('shows state, origin, progress with phase, counts and time left, and the facts', async () => {
    await open(() => json({ data: running() }))
    expect(w.find('h1').text()).toBe(`${t('ui.job.kind.sync')} · ${t('ui.job.target.all')}`)
    expect(w.find('[data-status="job:running"]').exists()).toBe(true)
    const progress = w.find('[data-testid="job-progress"]')
    expect(progress.text()).toContain(t('ui.job.phaseOf', { i: 3, n: 3, phase: t('ui.job.phase.details') }))
    expect(progress.text()).toContain('812 of 1,400')
    expect(progress.text()).toContain(t('ui.job.eta', { time: '4 min 10 s' }))
    expect(progress.find('[role="progressbar"]').attributes('aria-valuenow')).toBe('58')
    expect(w.find('[data-fact="id"]').text()).toContain(ID)
    expect(w.find('[data-fact="origin"]').text()).toContain('web · srv-1 · pid 4121')
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('says how many requests a download sent and how long they waited for the request budget (B2, F17)', async () => {
    await open(() => json({ data: running({ progress: { phase: 'details', phase_index: 3, phase_count: 3, done: 33, total: 70, percent: 40, eta_seconds: 71, failed_count: 0, failed: [], requests: { sent: 150, budget_wait_seconds: 412.5, backoff_seconds: 0 } } }) }))
    const line = w.find('[data-testid="job-requests"]')
    expect(line.text()).toBe(`150 requests to SofaScore · ${t('ui.job.requestsWait', { time: '2.8 s' })}`)
    expect(line.attributes('title')).toBe(t('ui.job.requestsHelp'))
    // the stream carries the counters too
    FakeES.last.emit('progress', 9, { percent: 41, done: 34, total: 70, phase: 'details', phase_index: 3, phase_count: 3, requests: { sent: 160, budget_wait_seconds: 440, backoff_seconds: 60 } })
    await flush()
    expect(w.find('[data-testid="job-requests"]').text()).toContain('160 requests to SofaScore')
    expect(w.find('[data-testid="job-requests"]').text()).toContain(t('ui.job.requestsBackoff', { time: '1 min' }))
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('a finished download keeps its request counters in the result; an older job has none', async () => {
    await open(() => json({ data: job({ result: { details_done: 70, details_total: 70, failed_count: 0, failed: [], requests: { sent: 322, budget_wait_seconds: 0, backoff_seconds: 0 } } }) }))
    expect(w.find('[data-testid="job-result-requests"]').text()).toBe(`322 requests to SofaScore · ${t('ui.job.requestsWait', { time: '0 s' })}`)
    w.unmount()
    await open(() => json({ data: job() }))
    expect(w.find('[data-testid="job-result-requests"]').exists()).toBe(false)
  })

  it('follows the event log over SSE, translated by type and code, without duplicates', async () => {
    await open(() => json({ data: running() }))
    const es = FakeES.last
    expect(es.url).toBe(`/api/v1/jobs/${ID}/events`)
    es.emit('started', 1, { kind: 'sync', origin: { face: 'api' } })
    es.emit('phase', 2, { phase: 'matches', phase_index: 2, phase_count: 3, total: 380 })
    es.emit('progress', 3, { percent: 61, done: 10, total: 20, phase: 'matches', phase_index: 2, phase_count: 3 })
    es.emit('failed', 4, { match_id: 16950622, league_id: 17 })
    es.emit('log', 5, { message: 'Fetching matches: league 17, season 61627' })
    es.emit('log', 6, { message: 'x', code: 'fetch_zero_matches' })
    // the browser reconnects with Last-Event-ID; the server may send the last event again
    es.emit('log', 6, { message: 'x', code: 'fetch_zero_matches' })
    await flush()
    expect(log()).toEqual([
      expect.stringContaining(t('ui.job.event.started', { face: 'web' })),
      expect.stringContaining(t('ui.job.event.phase', { i: 2, n: 3, phase: t('ui.job.phase.matches') })),
      expect.stringContaining(t('ui.job.event.failed', { id: '16950622' })),
      expect.stringContaining('Fetching matches: league 17, season 61627'),
      expect.stringContaining(t('ui.job.code.fetch_zero_matches')),
    ])
    expect(w.find('[data-testid="job-log"] a[href="/events/16950622"]').exists()).toBe(true)
    // the live progress comes from the stream
    expect(w.find('[data-testid="job-progress"] [role="progressbar"]').attributes('aria-valuenow')).toBe('61')
    await w.find('[data-testid="job-log"] select').setValue('all')
    expect(log().some((l) => l.includes('61%'))).toBe(true)
  })

  it('closes the stream after the finished event and reloads the job', async () => {
    let current = running()
    const f = await open(() => json({ data: current }))
    const es = FakeES.last
    current = job({ state: 'partial', result: { details_done: 1388, details_total: 1400, failed_count: 12, failed: [{ match_id: 5 }], breaker: '429' } })
    es.emit('finished', 9, { state: 'partial', message: 'x', code: 'fetch_stopped_by_breaker', params: { reason: '429' } })
    await flush()
    expect(es.closed).toBe(true)
    expect(callsTo(f, `GET /api/v1/jobs/${ID}`).length).toBe(2)
    expect(log().at(-1)).toContain(t('ui.job.code.fetch_stopped_by_breaker', { reason: t('ui.job.breaker.429') }))
    const result = w.find('[data-testid="job-result"]')
    expect(result.text()).toContain('1,388 of 1,400')
    expect(result.text()).toContain(t('ui.job.breaker.429'))
    expect(result.find('a[href="/events/5"]').exists()).toBe(true)
    // a finished job is not stopped, it is run again
    expect(w.findAll('button').some((b) => b.text().includes(t('ui.job.stop')))).toBe(false)
    expect(w.findAll('button').some((b) => b.text().includes(t('ui.job.runAgain')))).toBe(true)
  })

  it('a stream gap reloads the job and resumes behind the oldest kept event', async () => {
    const f = await open(() => json({ data: running() }))
    FakeES.last.gap(1204)
    await flush()
    expect(callsTo(f, `GET /api/v1/jobs/${ID}`).length).toBe(2)
    expect(FakeES.last.url).toBe(`/api/v1/jobs/${ID}/events?after=1203`)
    expect(w.text()).toContain(t('ui.job.gap', { n: '1,204' }))
  })

  it('without a stream (501, the browser gives up) it reads the job every 2 s', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const f = await open(() => json({ data: running() }))
    FakeES.last.fail(true)
    await flush()
    expect(w.text()).toContain(t('ui.job.streamPolling'))
    const before = callsTo(f, `GET /api/v1/jobs/${ID}`).length
    await vi.advanceTimersByTimeAsync(2100)
    expect(callsTo(f, `GET /api/v1/jobs/${ID}`).length).toBe(before + 1)
  })

  it('a dropped connection of a finished job is not reopened', async () => {
    await open(() => json({ data: job() }))
    const es = FakeES.last
    es.fail(false)
    expect(es.closed).toBe(true)
    expect(FakeES.instances).toHaveLength(1)
  })

  it('Stop asks first, then cancels, also a job of another process', async () => {
    const f = await open(() => json({ data: running({ origin: { face: 'cli', pid: 1, host: 'srv-2' } }) }), {
      [`POST /api/v1/jobs/${ID}/cancel`]: { data: running({ cancel_requested: true }) },
    })
    await w.findAll('button').find((b) => b.text().includes(t('ui.job.stop')))!.trigger('click')
    expect(w.find('[role="alertdialog"]').text()).toContain(t('ui.job.stopOther', { face: t('ui.job.face.cli') }))
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(callsTo(f, `POST /api/v1/jobs/${ID}/cancel`)).toHaveLength(1)
    expect(w.text()).toContain(t('ui.job.cancelPending'))
  })

  it('Run again starts the same kind and spec and opens the new job', async () => {
    const f = await open(() => json({ data: job({ kind: 'fetch', spec: { mode: 'details', league_id: null, selections: [{ league_id: 17, season_ids: null, match_ids: [1, 2] }] } }) }), {
      'POST /api/v1/jobs': json({ data: job({ id: 'NEXT' }) }, 202),
    })
    await w.findAll('button').find((b) => b.text().includes(t('ui.job.runAgain')))!.trigger('click')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(JSON.parse(String(callsTo(f, 'POST /api/v1/jobs')[0][1]!.body))).toEqual({
      kind: 'fetch',
      spec: { league_id: null, selections: [{ league_id: 17, season_ids: [], match_ids: [1, 2] }] },
    })
  })

  it('warns when a running job shows no sign of life', async () => {
    await open(() => json({ data: running({ heartbeat_at: Date.now() - 45000 }) }))
    expect(w.find('[data-testid="job-progress"] [role="alert"]').text()).toContain(t('ui.job.noHeartbeat', { time: '45 s' }))
  })

  it('an unknown id is a not-found page with the way back', async () => {
    await open(() => v1Error(404, 'not_found', { job_id: ID }))
    expect(w.find('[data-testid="empty"]').text()).toContain(t('ui.job.notFound'))
    expect(w.find('a[href="/jobs"]').exists()).toBe(true)
    expect(FakeES.instances).toHaveLength(0)
  })
})
