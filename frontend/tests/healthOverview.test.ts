import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import HealthScreen from '@/screens/HealthScreen.vue'
import OverviewScreen from '@/screens/OverviewScreen.vue'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { callsTo, flush, mockFetch } from './helpers'
import { axeViolations, job, mountScreen, status, v1Error } from './v1'

const t = i18n.global.t
let w: VueWrapper

beforeEach(() => {
  setLocale('en')
  clearToasts()
})
afterEach(() => w?.unmount())

const jobsPage = (data = [job()]) => ({ data, page: { limit: 20, next_cursor: null } })

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
          throttle: { enabled: true, requests_per_second: 5, shared: true, error: null },
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
    expect(w.text()).toContain('3.0.0 · API v1')
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('the live service card is read-only and says what is coming; no start or stop', async () => {
    mockFetch({ 'GET /api/v1/status': { data: status() } })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    const live = w.find('[data-testid="health-live"]')
    expect(live.text()).toContain(t('ui.health.liveLater'))
    expect(live.text()).toContain('ssc watch')
    expect(live.findAll('button').map((b) => b.attributes('aria-label'))).toEqual([t('ui.common.copyCommand')])
  })

  it('a failed /status shows the error with Retry', async () => {
    const f = mockFetch({ 'GET /api/v1/status': () => v1Error(500, 'internal') })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    expect(w.find('[data-testid="error-state"]').text()).toContain(t('ui.error.internal'))
    await w.findAll('[data-testid="error-state"] button').find((b) => b.text() === t('ui.common.retry'))!.trigger('click')
    await flush()
    expect(callsTo(f, 'GET /api/v1/status').length).toBe(2)
  })
})

describe('Overview', () => {
  it('lists what needs attention from /status and the last job of each kind', async () => {
    mockFetch({
      'GET /api/v1/status': { data: status({ throttle: { enabled: true, requests_per_second: 5, shared: true, error: 'no permission' } }) },
      'GET /api/v1/jobs': jobsPage([
        job({ id: 'A', kind: 'sync', state: 'partial', result: { failed_count: 12 } }),
        job({ id: 'B', kind: 'sync', state: 'failed' }),
        job({ id: 'C', kind: 'refresh', state: 'succeeded' }),
      ]),
    })
    ;({ w } = await mountScreen(OverviewScreen, '/'))
    await flush()
    const items = w.findAll('[data-testid="attention"] li').map((li) => li.text())
    expect(items).toEqual([
      expect.stringContaining(t('ui.health.budgetError')),
      expect.stringContaining(t('ui.overview.attention.job.partial', { kind: t('ui.job.kind.sync'), n: '12' })),
    ])
    expect(w.find('[data-testid="attention"] a[href="/jobs/A"]').exists()).toBe(true)
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('hides the attention list when nothing needs attention; shows services and recent jobs', async () => {
    mockFetch({ 'GET /api/v1/status': { data: status() }, 'GET /api/v1/jobs': jobsPage([job({ id: 'X1' })]) })
    ;({ w } = await mountScreen(OverviewScreen, '/'))
    await flush()
    expect(w.find('[data-testid="attention"]').exists()).toBe(false)
    expect(w.find('[data-testid="services"] [data-status="connection:ok"]').exists()).toBe(true)
    expect(w.find('[data-testid="running"]').text()).toContain(t('ui.overview.nothingRunning'))
    expect(w.find('[data-testid="recent"] a[href="/jobs/X1"]').exists()).toBe(true)
    expect(w.text()).toContain(t('ui.overview.tilesLater'))
  })

  it('shows the running job with its progress, Open and Stop', async () => {
    const f = mockFetch({
      'GET /api/v1/status': { data: status({ active_job: job({ id: 'RUN', state: 'running', finished_at: null, progress: { percent: 58, done: 812, total: 1400 } }) }) },
      'GET /api/v1/jobs': jobsPage([]),
      'POST /api/v1/jobs/RUN/cancel': { data: job({ id: 'RUN', state: 'running', cancel_requested: true }) },
    })
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

  it('"Sync all follows" starts a sync after the confirmation', async () => {
    const f = mockFetch({
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/jobs': jobsPage([]),
      'POST /api/v1/jobs': { data: job({ id: 'S1', state: 'running' }) },
    })
    ;({ w } = await mountScreen(OverviewScreen, '/'))
    await flush()
    await w.findAll('button').find((b) => b.text().includes(t('ui.overview.syncAll')))!.trigger('click')
    expect(w.find('[role="alertdialog"]').text()).toContain(t('ui.jobs.start.sendsRequests'))
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(JSON.parse(String(callsTo(f, 'POST /api/v1/jobs')[0][1]!.body))).toEqual({ kind: 'sync', spec: {} })
  })

  it('a failed /status is shown in the services card; the rest of the page still works', async () => {
    mockFetch({ 'GET /api/v1/status': () => v1Error(500, 'internal'), 'GET /api/v1/jobs': jobsPage([job({ id: 'Z' })]) })
    ;({ w } = await mountScreen(OverviewScreen, '/'))
    await flush()
    expect(w.find('[data-testid="services"] [data-testid="error-state"]').exists()).toBe(true)
    expect(w.find('[data-testid="recent"] a[href="/jobs/Z"]').exists()).toBe(true)
  })
})
