import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import JobsScreen from '@/screens/jobs/JobsScreen.vue'
import { clearToasts, uiToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { callsTo, deferred, flush, json, mockFetch } from './helpers'
import { axeViolations, job, mountScreen, status, v1Error } from './v1'

const t = i18n.global.t
let w: VueWrapper

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
})
afterEach(() => w?.unmount())

const page = (data = [job()], next: string | null = null) => ({ data, page: { limit: 25, next_cursor: next } })
const query = (call: unknown[]) => new URL(String(call[0]), 'http://x').searchParams

describe('Jobs', () => {
  it('lists jobs of every face with state, target, origin and duration', async () => {
    mockFetch({
      'GET /api/v1/jobs': page([
        job({ id: 'J1', kind: 'sync', state: 'running', progress: { percent: 42 }, finished_at: null }),
        job({ id: 'J2', kind: 'refresh', state: 'partial', origin: { face: 'cli', pid: 9, host: 'srv-9' }, result: { failed_count: 12 } }),
        job({ id: 'J3', kind: 'fetch', state: 'failed', error: { code: 'blocked', message: 'x' }, spec: { selections: [{ league_id: 17, match_ids: [1, 2, 3] }] } }),
      ]),
    })
    ;({ w } = await mountScreen(JobsScreen, '/jobs'))
    await flush()
    const rows = w.findAll('tbody tr')
    expect(rows).toHaveLength(3)
    expect(rows[0].text()).toContain(t('ui.job.kind.sync'))
    expect(rows[0].text()).toContain(t('ui.job.target.all'))
    expect(rows[0].text()).toContain('42%')
    expect(rows[0].find('[data-status="job:running"]').exists()).toBe(true)
    expect(rows[1].text()).toContain(t('ui.jobs.failedCount', { n: 12 }))
    expect(rows[1].text()).toContain(`${t('ui.job.face.cli')} · srv-9`)
    expect(rows[2].text()).toContain(t('ui.job.target.events', { n: 3 }))
    expect(rows[2].text()).toContain(t('ui.error.blocked'))
    expect(rows[1].find('a').attributes('href')).toBe('/jobs/J2')
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('filters by state and kind on the server, keeps them in the address and pages by cursor', async () => {
    const f = mockFetch({ 'GET /api/v1/jobs': (_i?: RequestInit, url?: string) => (url!.includes('cursor=C2') ? page([job({ id: 'J9' })]) : page([job()], 'C2')) })
    let router
    ;({ w, router } = await mountScreen(JobsScreen, '/jobs?state=failed&kind=sync', '/jobs'))
    await flush()
    expect(query(callsTo(f, 'GET /api/v1/jobs')[0]).getAll('state')).toEqual(['failed'])
    expect(query(callsTo(f, 'GET /api/v1/jobs')[0]).getAll('kind')).toEqual(['sync'])
    expect((w.find('[data-filter="state"]').element as HTMLSelectElement).value).toBe('failed')
    expect(w.text()).toContain(t('ui.filter.active', { n: 2 }))

    await w.findAll('footer button').find((b) => b.text().includes(t('ui.table.next')))!.trigger('click')
    await flush()
    expect(router.currentRoute.value.query).toMatchObject({ cursor: 'C2', state: 'failed' })
    expect(w.find('tbody').text()).toContain(t('ui.job.kind.sync'))
    await w.findAll('footer button').find((b) => b.text().includes(t('ui.table.prev')))!.trigger('click')
    await flush()
    expect(router.currentRoute.value.query.cursor).toBeUndefined()

    await w.find('[data-filter="kind"]').setValue('')
    await flush()
    expect(router.currentRoute.value.query).toEqual({ state: 'failed' })
  })

  it('has its empty states: no job at all, and none for the filters', async () => {
    mockFetch({ 'GET /api/v1/jobs': page([]) })
    ;({ w } = await mountScreen(JobsScreen, '/jobs'))
    await flush()
    expect(w.find('[data-testid="empty"]').text()).toContain(t('ui.jobs.empty'))
    w.unmount()
    ;({ w } = await mountScreen(JobsScreen, '/jobs?state=failed', '/jobs'))
    await flush()
    expect(w.find('[data-testid="empty"]').text()).toContain(t('ui.jobs.emptyFiltered'))
  })

  it('shows a load error with the request id and loads again on Retry', async () => {
    let answer = () => v1Error(500, 'internal')
    mockFetch({ 'GET /api/v1/jobs': () => answer() })
    ;({ w } = await mountScreen(JobsScreen, '/jobs'))
    await flush()
    expect(w.find('[data-testid="error-state"]').text()).toContain('req-123abc')
    answer = () => json(page())
    await w.findAll('[data-testid="error-state"] button').find((b) => b.text() === t('ui.common.retry'))!.trigger('click')
    await flush()
    expect(w.findAll('tbody tr')).toHaveLength(1)
  })

  it('starts a job only after saying what it does, then links to it', async () => {
    const started = deferred<Response>()
    const f = mockFetch({
      'GET /api/v1/jobs': page([]),
      'POST /api/v1/jobs': () => started.promise,
      'GET /api/v1/status': { data: status() },
    })
    ;({ w } = await mountScreen(JobsScreen, '/jobs'))
    await flush()
    await w.find('button[aria-haspopup="menu"]').trigger('click')
    await w.find('[data-key="refresh"]').trigger('click')
    const dialog = w.find('[role="alertdialog"]')
    expect(dialog.text()).toContain(t('ui.jobs.start.refresh.text'))
    expect(dialog.text()).toContain(t('ui.jobs.start.sendsRequests'))
    expect(callsTo(f, 'POST /api/v1/jobs')).toHaveLength(0)
    await w.find('[data-testid="confirm"]').trigger('click')
    expect(JSON.parse(String(callsTo(f, 'POST /api/v1/jobs')[0][1]!.body))).toEqual({ kind: 'refresh', spec: {} })
    started.resolve(json({ data: job({ id: 'NEW1', kind: 'refresh', state: 'running' }) }, 202))
    await flush()
    expect(w.find('[role="alertdialog"]').exists()).toBe(false)
    expect(uiToasts.value[0].link?.to).toBe('/jobs/NEW1')
  })

  it('a 409 keeps the dialog open with the reason and a link to the running job', async () => {
    mockFetch({
      'GET /api/v1/jobs': page([]),
      'POST /api/v1/jobs': () => v1Error(409, 'job_running', { holder: { lease: 'writer', purpose: 'headless', pid: 77, host: 'srv-2' } }),
      'GET /api/v1/status': { data: status() },
    })
    ;({ w } = await mountScreen(JobsScreen, '/jobs'))
    await flush()
    await w.find('button[aria-haspopup="menu"]').trigger('click')
    await w.find('[data-key="sync"]').trigger('click')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    const alert = w.find('[role="alertdialog"] [role="alert"]')
    expect(alert.text()).toContain(t('ui.error.job_running'))
    expect(alert.text()).toContain('pid 77')
  })

  it('offers only the kinds the API starts today', async () => {
    mockFetch({ 'GET /api/v1/jobs': page([]) })
    ;({ w } = await mountScreen(JobsScreen, '/jobs'))
    await flush()
    await w.find('button[aria-haspopup="menu"]').trigger('click')
    expect(w.findAll('[role="menuitem"]').map((x) => x.attributes('data-key'))).toEqual(['sync', 'fetch', 'refresh'])
  })
})
