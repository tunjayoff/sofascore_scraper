import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import HealthScreen from '@/screens/HealthScreen.vue'
import OverviewScreen from '@/screens/OverviewScreen.vue'
import { authNeeded } from '@/lib/auth'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { callsTo, flush, mockFetch } from './helpers'
import { axeViolations, job, mountScreen, status, summary, v1Error } from './v1'

const t = i18n.global.t
let w: VueWrapper

beforeEach(() => {
  setLocale('en')
  clearToasts()
  authNeeded.value = false
})
afterEach(() => w?.unmount())

const jobsPage = (data = [job()]) => ({ data, page: { limit: 20, next_cursor: null } })
const list = <T>(data: T[]) => ({ data, page: { limit: 0, next_cursor: null } })
const follow = (id: number, sport = 'football') => ({
  id: `tournament:${id}`, kind: 'tournament', entity_id: id, name: `T${id}`, sport, seasons: 'current', slices: null, live: false,
  enabled: true, origin: 'api', position: id, writable: ['name'], created_at_utc: null, updated_at_utc: null,
})
const sink = (over = {}) => ({
  name: 'ops', type: 'webhook', target: 'hooks.example.org', events: ['live.*'], state: 'ok', served: true, cursor: 10, head_seq: 10,
  lag_events: 0, lag_seconds: null, last_delivered_at_utc: '2026-10-02T09:59:00Z', last_error: null, dropped: 0, ...over,
})
const nowSec = () => Math.floor(Date.now() / 1000)

describe('Health', () => {
  it('shows the connection, the request budget and the server from /status, read-only', async () => {
    mockFetch({
      'GET /api/v1/status': {
        data: status({
          bridge: {
            ...status().bridge,
            state: 'blocked',
            consecutive_failures: 12,
            failing_since: '2026-10-02T08:00:00Z',
            last_error: { kind: 'challenge', at: '2026-10-02T08:10:00Z' },
          },
        }),
      },
    })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    const card = w.find('[data-testid="health-connection"]')
    expect(card.find('[data-status="connection:blocked"]').text()).toBe(t('ui.status.connection.blocked'))
    expect(card.find('[data-fact="failures"]').text()).toContain('12')
    expect(card.find('[data-fact="lastError"]').text()).toContain(t('ui.health.errorKind.challenge'))
    expect(card.find('[data-fact="rate"]').text()).toContain(t('ui.health.rateValue', { n: '5' }))
    const storage = w.find('[data-testid="health-storage"]')
    expect(storage.text()).toContain('3.0.0 · API v1 · schema 1')
    expect(storage.find('[data-fact="size"]').text()).toContain('2.1 GB')
    expect(storage.find('[data-fact="index"]').text()).toContain(t('ui.health.indexCurrent'))
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('the live service card: running with its sources, a direct warning, a stale heartbeat; never start or stop', async () => {
    mockFetch({
      'GET /api/v1/status': {
        data: status({
          live: {
            running: true, pid: 4121, host: 'srv-1', source: 'page', sports: ['football', 'tennis'], heartbeat_at: nowSec() - 900, blocked: false,
            leaders: { football: 'page', tennis: 'direct' }, last_switch: { sport: 'tennis', from: 'page', to: 'direct', reason: 'stale', at: nowSec() - 600 },
          },
        }),
      },
    })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    const live = w.find('[data-testid="health-live"]')
    expect(live.find('[data-status="live:running"]').exists()).toBe(true)
    expect(live.find('[data-fact="by"]').text()).toContain('pid 4121 · srv-1')
    expect(live.find('[data-fact="sports"]').text()).toBe(`${t('ui.health.liveSports')}${t('sport.football')}, ${t('sport.tennis')}`)
    expect(live.find('[data-fact="source"]').text()).toContain(`${t('sport.tennis')} · ${t('ui.health.source.direct')}`)
    expect(live.text()).toContain(t('ui.health.direct'))
    expect(live.text()).toContain(t('ui.health.liveStale', { time: '15 min' }))
    expect(live.findAll('button')).toHaveLength(0)
  })

  it('the live service not running: the command that starts it, paused by a block as attention', async () => {
    mockFetch({ 'GET /api/v1/status': { data: status() } })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    const live = w.find('[data-testid="health-live"]')
    expect(live.find('[data-status="live:stopped"]').exists()).toBe(true)
    expect(live.text()).toContain('ssc watch')
    expect(live.findAll('button').map((b) => b.attributes('aria-label'))).toEqual([t('ui.common.copyCommand')])
  })

  it('runs the connection check only on click: one request, its result, a refusal by code', async () => {
    let answer: () => Response | object = () => ({
      data: { ok: true, reason: null, message: 'ok', events_count: 37, checked_at_utc: '2026-10-02T10:00:00Z', bridge: status().bridge },
    })
    const f = mockFetch({ 'GET /api/v1/status': { data: status() }, 'POST /api/v1/status/check': () => answer() })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    expect(callsTo(f, 'POST /api/v1/status/check')).toHaveLength(0)
    const card = w.find('[data-testid="connection-check"]')
    expect(card.text()).toContain(t('ui.health.check.note'))
    await card.find('button').trigger('click')
    await flush()
    expect(JSON.parse(String(callsTo(f, 'POST /api/v1/status/check')[0][1]!.body))).toEqual({ target: 'sofascore' })
    expect(card.text()).toContain(t('ui.health.check.ok', { n: '37' }))
    answer = () => ({ data: { ok: false, reason: 'blocked', message: 'x', events_count: null, checked_at_utc: '2026-10-02T10:01:00Z', bridge: status().bridge } })
    await card.find('button').trigger('click')
    await flush()
    expect(card.text()).toContain(t('ui.health.check.failed', { reason: t('ui.health.check.reason.blocked') }))
    answer = () => v1Error(503, 'rate_limited')
    await card.find('button').trigger('click')
    await flush()
    expect(card.find('[data-testid="form-error"]').attributes('data-code')).toBe('rate_limited')
    expect(card.find('a[href="/system/health"]').exists()).toBe(true)
  })

  it('lists who holds the data folder and says when the index needs a rebuild', async () => {
    mockFetch({
      'GET /api/v1/status': {
        data: status({
          leases: [{ name: 'writer', purpose: 'job', pid: 9, host: 'srv-1', since_utc: '2026-10-02T09:00:00Z' }, { name: 'watcher:football', pid: 10, host: 'srv-1' }],
          summary: summary({ catalog_rebuild_reason: 'schema_changed' }),
        }),
      },
    })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    const leases = w.find('[data-testid="health-leases"]').text()
    expect(leases).toContain(t('ui.health.lease.writer'))
    expect(leases).toContain('job · pid 9 · srv-1')
    expect(leases).toContain(t('ui.health.lease.watcher', { sport: t('sport.football') }))
    expect(w.find('[data-testid="health-storage"] a[href="/maintenance"]').exists()).toBe(true)
  })

  it('a failed /status shows the error with Retry; a 401 asks for the token', async () => {
    const f = mockFetch({ 'GET /api/v1/status': () => v1Error(500, 'internal') })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    expect(w.find('[data-testid="error-state"]').text()).toContain(t('ui.error.internal'))
    await w.findAll('[data-testid="error-state"] button').find((b) => b.text() === t('ui.common.retry'))!.trigger('click')
    await flush()
    expect(callsTo(f, 'GET /api/v1/status').length).toBe(2)
    w.unmount()
    mockFetch({ 'GET /api/v1/status': () => v1Error(401, 'unauthorized') })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    expect(authNeeded.value).toBe(true)
  })
})

describe('Overview', () => {
  const base = (over: Record<string, unknown> = {}) => ({
    'GET /api/v1/status': { data: status() },
    'GET /api/v1/jobs': jobsPage([]),
    'GET /api/v1/follows': list([follow(17), follow(8), follow(132, 'basketball')]),
    'GET /api/v1/sinks': list([]),
    ...over,
  })

  it('lists what needs attention: status, sinks, the index, old-layout data, the last job of each kind', async () => {
    mockFetch(
      base({
        'GET /api/v1/status': {
          data: status({
            throttle: { enabled: true, requests_per_second: 5, shared: true, error: 'no permission' },
            live: { ...status().live!, running: true, blocked: true },
            summary: summary({ catalog_rebuild_reason: 'missing', legacy_events: 1204 }),
          }),
        },
        'GET /api/v1/sinks': list([sink({ state: 'error', lag_events: 1240 }), sink({ name: 'feed', type: 'file' })]),
        'GET /api/v1/jobs': jobsPage([
          job({ id: 'A', kind: 'sync', state: 'partial', result: { failed_count: 12 } }),
          job({ id: 'B', kind: 'sync', state: 'failed' }),
          job({ id: 'C', kind: 'refresh', state: 'succeeded' }),
        ]),
      }),
    )
    ;({ w } = await mountScreen(OverviewScreen, '/'))
    await flush()
    const items = w.findAll('[data-testid="attention"] li').map((li) => li.text())
    expect(items).toEqual([
      expect.stringContaining(t('ui.health.budgetError')),
      expect.stringContaining(t('ui.overview.attention.liveBlocked')),
      expect.stringContaining(t('ui.overview.attention.sink.retrying', { name: 'ops', n: '1,240' })),
      expect.stringContaining(t('ui.overview.attention.index')),
      expect.stringContaining(t('ui.overview.attention.legacy', { n: '1,204' })),
      expect.stringContaining(t('ui.overview.attention.job.partial', { kind: t('ui.job.kind.sync'), n: '12' })),
    ])
    expect(w.find('[data-testid="attention"] a[href="/jobs/A"]').exists()).toBe(true)
    expect(w.find('[data-testid="attention"] a[href="/system/sinks"]').exists()).toBe(true)
    expect(w.find('[data-testid="services"]').text()).toContain(t('ui.overview.sinksBehind', { n: 1, total: 2 }))
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('the tiles count matches, details, follows and the disk', async () => {
    mockFetch(base())
    ;({ w } = await mountScreen(OverviewScreen, '/'))
    await flush()
    const tile = (k: string) => w.find(`[data-tile="${k}"]`).text()
    expect(tile('matches')).toContain('48,210')
    expect(tile('details')).toContain('46,903')
    expect(tile('details')).toContain('97%')
    expect(tile('follows')).toContain('3')
    expect(tile('follows')).toContain(t('ui.overview.tile.sports', { n: 2 }))
    expect(tile('disk')).toContain('2.1 GB')
    expect(w.find('[data-testid="attention"]').exists()).toBe(false)
    expect(w.find('[data-testid="services"] [data-status="connection:ok"]').exists()).toBe(true)
    expect(w.find('[data-testid="services"]').text()).toContain(t('ui.overview.sinksNone'))
  })

  it('first run: nothing followed and nothing stored makes the page one empty state', async () => {
    mockFetch(base({ 'GET /api/v1/status': { data: status({ summary: summary({ matches: 0, details: 0 }) }) }, 'GET /api/v1/follows': list([]) }))
    ;({ w } = await mountScreen(OverviewScreen, '/'))
    await flush()
    const first = w.find('[data-testid="first-run"]')
    expect(first.text()).toContain(t('ui.overview.firstRun'))
    expect(first.find('a[href="/follows/new"]').exists()).toBe(true)
    expect(w.find('[data-testid="tiles"]').exists()).toBe(false)
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('shows the running job with its progress, Open and Stop', async () => {
    const f = mockFetch(
      base({
        'GET /api/v1/status': { data: status({ active_job: job({ id: 'RUN', state: 'running', finished_at: null, progress: { percent: 58, done: 812, total: 1400 } }) }) },
        'POST /api/v1/jobs/RUN/cancel': { data: job({ id: 'RUN', state: 'running', cancel_requested: true }) },
      }),
    )
    ;({ w } = await mountScreen(OverviewScreen, '/'))
    await flush()
    const running = w.find('[data-testid="running"]')
    expect(running.find('[role="progressbar"]').attributes('aria-valuenow')).toBe('58')
    expect(running.text()).toContain('812 of 1,400')
    expect(running.find('a[href="/jobs/RUN"]').exists()).toBe(true)
    await running.findAll('button').find((b) => b.text() === t('ui.job.stop'))!.trigger('click')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(callsTo(f, 'POST /api/v1/jobs/RUN/cancel')).toHaveLength(1)
  })

  it('"Sync all follows" starts a sync after the confirmation; a 409 job_running stays in the dialog', async () => {
    let refuse = false
    const f = mockFetch(base({ 'POST /api/v1/jobs': () => (refuse ? v1Error(409, 'job_running', { holder: { pid: 77, host: 'srv-2' } }) : { data: job({ id: 'S1', state: 'running' }) }) }))
    ;({ w } = await mountScreen(OverviewScreen, '/'))
    await flush()
    await w.findAll('button').find((b) => b.text().includes(t('ui.overview.syncAll')))!.trigger('click')
    expect(w.find('[role="alertdialog"]').text()).toContain(t('ui.jobs.start.sendsRequests'))
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(JSON.parse(String(callsTo(f, 'POST /api/v1/jobs')[0][1]!.body))).toEqual({ kind: 'sync', spec: {} })
    refuse = true
    await w.findAll('button').find((b) => b.text().includes(t('ui.overview.syncAll')))!.trigger('click')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(w.find('[role="alertdialog"] [role="alert"]').text()).toContain(t('ui.error.job_running'))
  })

  it('a failed /status is shown in the services card and the tiles show "—"; the rest still works', async () => {
    mockFetch(base({ 'GET /api/v1/status': () => v1Error(500, 'internal'), 'GET /api/v1/jobs': jobsPage([job({ id: 'Z' })]) }))
    ;({ w } = await mountScreen(OverviewScreen, '/'))
    await flush()
    expect(w.find('[data-testid="services"] [data-testid="error-state"]').exists()).toBe(true)
    expect(w.find('[data-tile="matches"]').text()).toContain('—')
    expect(w.find('[data-testid="recent"] a[href="/jobs/Z"]').exists()).toBe(true)
  })
})
