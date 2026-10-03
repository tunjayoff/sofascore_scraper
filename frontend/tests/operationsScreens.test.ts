import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import ExportsScreen from '@/screens/exports/ExportsScreen.vue'
import BackupsScreen from '@/screens/backups/BackupsScreen.vue'
import MaintenanceScreen from '@/screens/MaintenanceScreen.vue'
import LogsScreen from '@/screens/LogsScreen.vue'
import SinksScreen from '@/screens/SinksScreen.vue'
import JobDetailScreen from '@/screens/jobs/JobDetailScreen.vue'
import { authNeeded } from '@/lib/auth'
import { resetSports } from '@/app/sports'
import { clearToasts, uiToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { callsTo, deferred, flush, json, mockFetch } from './helpers'
import { axeViolations, job, mountScreen, status, summary, useFakeES, v1Error } from './v1'

/**
 * The screens of Operations and System (05-web-ui.md 6.10, 6.11, 6.13, 6.14): each in its states (loading,
 * empty, error by code, the 401 token prompt, the 409 of a running job) with a fake API. No test reaches a
 * server or SofaScore.
 */
const t = i18n.global.t
let w: VueWrapper

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
  authNeeded.value = false
  resetSports()
})
afterEach(() => w?.unmount())

const page = <T>(data: T[], next: string | null = null) => ({ data, page: { limit: 25, next_cursor: next } })
const body = (f: ReturnType<typeof mockFetch>, key: string, i = 0) => JSON.parse(String(callsTo(f, key)[i][1]!.body))

const exportRow = (over = {}) => ({
  id: 'EX1', job_id: 'EX1', state: 'succeeded', dataset: 'events', format: 'csv', schema: 'normalized', profile: 'legacy-wide-csv',
  filter: { sport: null, tournament_ids: [17], season_ids: [], event_ids: [] }, created_at: '2026-09-30T22:10:00Z', finished_at: '2026-09-30T22:11:00Z',
  rows: 4120, events: 4120, bytes: 1258291, skipped: 0, file: 'EX1.csv', media_type: 'text/csv', available: true, ...over,
})
const backup = (over = {}) => ({ name: 'backup_all_20261001_221000.zip', scope: 'all', created_at_utc: '2026-10-01T22:10:00Z', bytes: 2040109465, format: 2, with_env: false, ...over })

describe('Exports', () => {
  it('lists exports with what, filter, rows, size and a download of the file; a running one links to its job', async () => {
    mockFetch({
      'GET /api/v1/exports': page([
        exportRow({ id: 'RUN', job_id: 'RUN', state: 'running', available: false, rows: null, bytes: null, file: null, profile: null, schema: 'raw', dataset: 'slices', format: 'jsonl' }),
        exportRow(),
      ]),
    })
    ;({ w } = await mountScreen(ExportsScreen, '/exports'))
    await flush()
    const rows = w.findAll('tbody tr')
    expect(rows[0].text()).toContain(`${t('ui.exports.schema.raw')} · ${t('ui.exports.dataset.slices')} · JSONL`)
    expect(rows[0].find('[data-status="job:running"]').exists()).toBe(true)
    expect(rows[0].find('a[href="/jobs/RUN"]').exists()).toBe(true)
    expect(rows[1].text()).toContain(t('ui.exports.kind.legacy'))
    expect(rows[1].text()).toContain(t('ui.exports.filter.tournaments', { n: 1 }))
    expect(rows[1].text()).toContain('4,120')
    expect(rows[1].text()).toContain('1.2 MB')
    expect(rows[1].find('a[download]').attributes('href')).toBe('/api/v1/exports/EX1/download')
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('loading, empty and an error by code', async () => {
    const d = deferred<Response>()
    mockFetch({ 'GET /api/v1/exports': () => d.promise })
    ;({ w } = await mountScreen(ExportsScreen, '/exports'))
    await flush()
    expect(w.find('[data-testid="skeleton"]').exists()).toBe(true)
    d.resolve(json(page([])))
    await flush()
    expect(w.find('[data-testid="empty"]').text()).toContain(t('ui.exports.empty'))
    w.unmount()
    mockFetch({ 'GET /api/v1/exports': () => v1Error(507, 'storage_error', { store_message: 'disk full' }) })
    ;({ w } = await mountScreen(ExportsScreen, '/exports'))
    await flush()
    expect(w.find('[data-testid="error-state"]').attributes('data-code')).toBe('storage_error')
    expect(w.find('[data-testid="error-state"]').text()).toContain('disk full')
  })

  it('a 401 asks for the token', async () => {
    mockFetch({ 'GET /api/v1/exports': () => v1Error(401, 'unauthorized') })
    ;({ w } = await mountScreen(ExportsScreen, '/exports'))
    await flush()
    expect(authNeeded.value).toBe(true)
  })

  it('the dialog starts the wide CSV or a raw export with the filter; normalized is shown but disabled', async () => {
    const f = mockFetch({
      'GET /api/v1/exports': page([]),
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/sports': { data: [{ slug: 'football', name: 'Football', i18n_key: 'sport.football', score_family: 'football', slices: [] }], page: { limit: 0 } },
      'GET /api/v1/tournaments': page([{ id: 17, sport: 'football', category_id: 1, name: 'Premier League', slug: 'pl', followed: true }]),
      'POST /api/v1/jobs': { data: job({ id: 'EX9', kind: 'export', state: 'running' }) },
    })
    ;({ w } = await mountScreen(ExportsScreen, '/exports'))
    await flush()
    await w.findAll('button').find((b) => b.text().includes(t('ui.exports.new')))!.trigger('click')
    await flush()
    const dialog = w.find('[role="dialog"]')
    expect(dialog.find('input[value="normalized"]').attributes('disabled')).toBeDefined()
    expect(dialog.text()).toContain(t('ui.exports.dialog.noParquet'))
    expect(dialog.text()).toContain(t('ui.exports.dialog.fullSize'))
    await dialog.find('input[type="checkbox"][value="17"]').setValue(true)
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs')).toEqual({
      kind: 'export',
      spec: { dataset: 'events', format: 'csv', profile: 'legacy-wide-csv', filter: { sport: null, tournament_ids: [17], season_ids: [], event_ids: [] } },
    })
    expect(uiToasts.value[0].link?.to).toBe('/jobs/EX9')

    await w.findAll('button').find((b) => b.text().includes(t('ui.exports.new')))!.trigger('click')
    await flush()
    await w.find('input[value="raw"]').setValue(true)
    await w.find('input[value="slices"]').setValue(true)
    await w.find('[role="dialog"] select').setValue('football')
    const ids = w.findAll('[role="dialog"] input.u-mono')
    await ids[1].setValue('16950622, 16950623')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs', 1).spec).toEqual({
      dataset: 'slices', format: 'jsonl', schema: 'raw', profile: null, filter: { sport: 'football', tournament_ids: [], season_ids: [], event_ids: [16950622, 16950623] },
    })
  })

  it('a refused export (409 job_running, 501) stays in the dialog with its reason', async () => {
    let answer = () => v1Error(409, 'job_running', { holder: { pid: 77, host: 'srv-2' } })
    mockFetch({
      'GET /api/v1/exports': page([]),
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/sports': { data: [], page: { limit: 0 } },
      'GET /api/v1/tournaments': page([]),
      'POST /api/v1/jobs': () => answer(),
    })
    ;({ w } = await mountScreen(ExportsScreen, '/exports'))
    await flush()
    await w.findAll('button').find((b) => b.text().includes(t('ui.exports.new')))!.trigger('click')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    const err = w.find('[role="dialog"] [data-testid="form-error"]')
    expect(err.attributes('data-code')).toBe('job_running')
    expect(err.text()).toContain('pid 77')
    answer = () => v1Error(501, 'not_supported')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(w.find('[role="dialog"] [data-testid="form-error"]').attributes('data-code')).toBe('not_supported')
  })
})

describe('Backups', () => {
  const routes = (over: Record<string, unknown> = {}) => ({
    'GET /api/v1/backups': page([backup(), backup({ name: 'backup_all_with_env_20260920_090000.zip', format: 1, with_env: true, created_at_utc: '2026-09-20T09:00:00Z' })]),
    'GET /api/v1/status': { data: status() },
    ...over,
  })

  it('lists the archives with scope, size, format and the secrets mark; a running job shows a banner', async () => {
    mockFetch(routes({ 'GET /api/v1/status': { data: status({ active_job: job({ id: 'RUN', state: 'running', finished_at: null }) }) } }))
    ;({ w, } = await mountScreen(BackupsScreen, '/backups'))
    const { useStatusStore } = await import('@/app/statusStore')
    await useStatusStore().refresh()
    await flush()
    const rows = w.findAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain(t('ui.scope.all'))
    expect(rows[0].text()).toContain('1.9 GB')
    expect(rows[1].text()).toContain(t('ui.backups.format1'))
    expect(rows[1].text()).toContain(t('ui.backups.withEnv'))
    expect(w.find('[data-testid="busy-banner"] a[href="/jobs/RUN"]').exists()).toBe(true)
    expect(w.text()).toContain(t('ui.backups.restoreNote'))
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('empty, error and 401', async () => {
    mockFetch(routes({ 'GET /api/v1/backups': page([]) }))
    ;({ w } = await mountScreen(BackupsScreen, '/backups'))
    await flush()
    expect(w.find('[data-testid="empty"]').text()).toContain(t('ui.backups.empty'))
    w.unmount()
    mockFetch(routes({ 'GET /api/v1/backups': () => v1Error(500, 'internal') }))
    ;({ w } = await mountScreen(BackupsScreen, '/backups'))
    await flush()
    expect(w.find('[data-testid="error-state"]').attributes('data-code')).toBe('internal')
    w.unmount()
    mockFetch(routes({ 'GET /api/v1/backups': () => v1Error(401, 'unauthorized') }))
    ;({ w } = await mountScreen(BackupsScreen, '/backups'))
    await flush()
    expect(authNeeded.value).toBe(true)
  })

  it('creates a backup with the scope; secrets only when chosen, with the warning; a 409 stays in the dialog', async () => {
    let refuse = false
    const f = mockFetch(
      routes({ 'POST /api/v1/jobs': () => (refuse ? v1Error(409, 'data_operation_running', { holder: { pid: 5 } }) : { data: job({ id: 'BK1', kind: 'backup', state: 'running' }) }) }),
    )
    ;({ w } = await mountScreen(BackupsScreen, '/backups'))
    await flush()
    await w.findAll('button').find((b) => b.text().includes(t('ui.backups.create')))!.trigger('click')
    await w.find('input[value="state"]').setValue(true)
    expect(w.find('[role="dialog"]').text()).not.toContain(t('ui.backups.secretsWarning'))
    await w.find('[data-testid="with-env"]').setValue(true)
    expect(w.find('[role="dialog"]').text()).toContain(t('ui.backups.secretsWarning'))
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs')).toEqual({ kind: 'backup', spec: { scope: 'state', include_env: true } })
    refuse = true
    await w.findAll('button').find((b) => b.text().includes(t('ui.backups.create')))!.trigger('click')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(w.find('[role="dialog"] [data-testid="form-error"]').attributes('data-code')).toBe('data_operation_running')
  })

  it('restore in three steps: the check is a dry run, the choice, then the server command', async () => {
    const report = { name: backup().name, format: 2, scope: 'all', dry_run: true, force: false, restored: [], replaced: [], skipped: [], occupied: ['v3', '.meta/state.db'], counts: { events: 48210 } }
    const f = mockFetch(
      routes({
        'POST /api/v1/jobs': (init?: RequestInit) => {
          const force = JSON.parse(String(init!.body)).spec.force
          return { data: job({ id: force ? 'CHK2' : 'CHK1', kind: 'restore', state: 'running', finished_at: null }) }
        },
        'GET /api/v1/jobs/CHK1': { data: job({ id: 'CHK1', kind: 'restore', state: 'succeeded', result: { restore: report } }) },
        'GET /api/v1/jobs/CHK2': { data: job({ id: 'CHK2', kind: 'restore', state: 'succeeded', result: { restore: { ...report, force: true, replaced: ['v3'] } } }) },
      }),
    )
    ;({ w } = await mountScreen(BackupsScreen, '/backups'))
    await flush()
    await w.find('tbody tr button[aria-haspopup="menu"]').trigger('click')
    await w.find('[data-key="restore"]').trigger('click')
    await flush()
    expect(w.find('.u-steps [aria-current="step"]').text()).toContain(t('ui.restore.step1'))
    await w.find('[data-testid="run-check"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs')).toEqual({ kind: 'restore', spec: { name: backup().name, force: false, dry_run: true } })
    const check = w.find('[data-testid="restore-check"]')
    expect(check.text()).toContain('48,210')
    expect(check.text()).toContain(t('ui.restore.occupied', { what: 'v3, .meta/state.db' }))
    await w.find('[data-testid="next"]').trigger('click')
    // the folder is not empty: only "replace" is possible
    expect(w.find('input[name="restore-how"][value="false"]').attributes('disabled')).toBeDefined()
    await w.findAll('[role="dialog"] button').find((b) => b.text() === t('ui.restore.checkReplace'))!.trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs', 1).spec).toEqual({ name: backup().name, force: true, dry_run: true })
    expect(w.find('[data-testid="restore-replaced"]').text()).toBe(t('ui.restore.wouldMove', { what: 'v3' }))
    await w.find('[data-testid="next"]').trigger('click')
    expect(w.find('[role="dialog"]').text()).toContain(`ssc backup restore ${backup().name} --force --yes`)
    expect(w.find('[role="dialog"]').text()).toContain(t('ui.restore.apiChecksOnly'))
    // no button restores: the API offers only the check
    expect(w.findAll('[role="dialog"] .u-btn-primary')).toHaveLength(0)
    expect(await axeViolations(w.find('[role="dialog"]').element)).toEqual([])
  })
})

describe('Maintenance', () => {
  it('shows the rebuild reason, the old-layout count with the command, and rebuilds after a confirmation', async () => {
    const f = mockFetch({
      'GET /api/v1/status': { data: status({ summary: summary({ catalog_rebuild_reason: 'schema_changed', legacy_events: 1204 }) }) },
      'POST /api/v1/jobs': { data: job({ id: 'RB', kind: 'rebuild', state: 'running' }) },
    })
    ;({ w } = await mountScreen(MaintenanceScreen, '/maintenance'))
    await flush()
    expect(w.find('[data-testid="rebuild-reason"]').text()).toContain('schema_changed')
    const legacy = w.find('[data-testid="card-legacy"]')
    expect(legacy.text()).toContain(t('ui.maintenance.legacy.count', { n: '1,204' }))
    expect(legacy.text()).toContain('ssc migrate --dry-run')
    expect(legacy.findAll('button').every((b) => b.attributes('aria-label') === t('ui.common.copyCommand'))).toBe(true)
    await w.find('[data-testid="card-rebuild"] button').trigger('click')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs')).toEqual({ kind: 'rebuild', spec: { mode: 'auto' } })
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('clears only after the scope is typed; says what is kept; a refusal stays in the dialog', async () => {
    let answer: () => unknown = () => v1Error(409, 'instance_running', { holder: { purpose: 'live', pid: 4121, host: 'srv-1' } })
    const f = mockFetch({ 'GET /api/v1/status': { data: status() }, 'POST /api/v1/jobs': () => answer() })
    ;({ w } = await mountScreen(MaintenanceScreen, '/maintenance'))
    await flush()
    await w.find('[data-testid="clear-scope"]').setValue('seasons')
    await w.find('[data-testid="clear-open"]').trigger('click')
    const dialog = w.find('[role="alertdialog"]')
    expect(dialog.text()).toContain(t('ui.maintenance.clear.kept'))
    expect(w.find('[data-testid="confirm"]').attributes('disabled')).toBeDefined()
    await dialog.find('input').setValue('season')
    expect(w.find('[data-testid="confirm"]').attributes('disabled')).toBeDefined()
    await dialog.find('input').setValue('seasons')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs')).toEqual({ kind: 'clear', spec: { scope: 'seasons', confirm: true } })
    expect(w.find('[role="alertdialog"] [role="alert"]').text()).toContain(t('ui.error.instance_running'))
    expect(w.find('[role="alertdialog"] [role="alert"]').text()).toContain('pid 4121')
    answer = () => ({ data: job({ id: 'CL', kind: 'clear', state: 'running' }) })
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(w.find('[role="alertdialog"]').exists()).toBe(false)
  })
})

describe('Logs and diagnostics', () => {
  const tail = (entries = [{ time: '2026-10-02 10:00:00,000', level: 'INFO', pid: 1, logger: 'WebAPI', message: 'Started' }, { time: '2026-10-02 10:00:01,000', level: 'ERROR', pid: 1, logger: 'sync', message: 'Match 1 failed' }]) => ({
    data: { enabled: true, file: '/srv/logs/app.log', level: 'INFO', min_level: null, count: entries.length, entries },
  })

  it('lists the newest lines first, filters by level on the server and by text within the lines', async () => {
    const f = mockFetch({ 'GET /api/v1/logs': tail() })
    let router
    ;({ w, router } = await mountScreen(LogsScreen, '/system/logs'))
    await flush()
    const rows = w.findAll('tbody tr')
    expect(rows[0].text()).toContain('Match 1 failed')
    expect(rows[1].text()).toContain('Started')
    expect(await axeViolations(w.element)).toEqual([])
    await w.find('[data-filter="level"]').setValue('ERROR')
    await flush()
    expect(router.currentRoute.value.query.level).toBe('ERROR')
    const q = new URL(String(callsTo(f, 'GET /api/v1/logs').at(-1)![0]), 'http://x').searchParams
    expect([q.get('level'), q.get('limit')]).toEqual(['ERROR', '200'])
    await w.find('input[type="search"]').setValue('started')
    expect(w.findAll('tbody tr')).toHaveLength(1)
    await w.find('input[type="search"]').setValue('nothing like this')
    expect(w.find('[data-testid="empty"]').text()).toContain(t('ui.logs.emptyFiltered'))
  })

  it('says when file logging is off; an error by code; 401', async () => {
    mockFetch({ 'GET /api/v1/logs': { data: { enabled: false, file: null, level: 'INFO', count: 0, entries: [] } } })
    ;({ w } = await mountScreen(LogsScreen, '/system/logs'))
    await flush()
    expect(w.text()).toContain(t('ui.logs.off'))
    w.unmount()
    mockFetch({ 'GET /api/v1/logs': () => v1Error(500, 'internal') })
    ;({ w } = await mountScreen(LogsScreen, '/system/logs'))
    await flush()
    expect(w.find('[data-testid="error-state"]').attributes('data-code')).toBe('internal')
    w.unmount()
    mockFetch({ 'GET /api/v1/logs': () => v1Error(401, 'unauthorized') })
    ;({ w } = await mountScreen(LogsScreen, '/system/logs'))
    await flush()
    expect(authNeeded.value).toBe(true)
  })

  it('the diagnostics tab shows the summary and the checks; the bundle is a download', async () => {
    mockFetch({
      'GET /api/v1/diagnostics': {
        data: {
          app: { version: '3.0.0', commit: 'abc123' },
          runtime: { python: '3.14.7', implementation: 'CPython', platform: 'Linux-x86_64', docker: false },
          data_dir: { path: '/srv/data', disk_free_mb: 2048 },
          logging: { level: 'INFO', file: '/srv/logs/app.log' },
          doctor: { status: 'warn', checks: [{ id: 'python', status: 'ok', label: 'Python', summary: '3.14.7' }, { id: 'browser', status: 'warn', label: 'Browser', summary: 'not installed', fix_command: 'python -m playwright install chromium' }] },
        },
      },
    })
    ;({ w } = await mountScreen(LogsScreen, '/system/logs?tab=diagnostics', '/system/logs'))
    await flush()
    const summaryCard = w.find('[data-testid="diag-summary"]')
    expect(summaryCard.find('[data-fact="version"]').text()).toContain('3.0.0 · abc123')
    expect(summaryCard.find('[data-fact="free"]').text()).toContain('2.0 GB')
    const doctor = w.find('[data-testid="diag-doctor"]')
    expect(doctor.text()).toContain(t('ui.logs.diag.status.warn'))
    expect(doctor.text()).toContain('python -m playwright install chromium')
    expect(w.find('[data-testid="bundle"]').attributes('href')).toBe('/api/v1/diagnostics/bundle')
    expect(await axeViolations(w.element)).toEqual([])
  })
})

describe('Sinks', () => {
  const sink = (over = {}) => ({
    name: 'ops', type: 'webhook', target: 'hooks.example.org', events: ['live.*', 'job.finished'], state: 'ok', served: true, cursor: 10, head_seq: 10,
    lag_events: 0, lag_seconds: null, last_delivered_at_utc: '2026-10-02T09:59:00Z', last_error: null, dropped: 0, ...over,
  })

  it('lists each sink with its state and how far behind; who delivers; the last error', async () => {
    mockFetch({
      'GET /api/v1/sinks': page([sink({ state: 'error', lag_events: 1240, lag_seconds: 360, last_error: 'HTTP 503', dropped: 2 }), sink({ name: 'feed', type: 'file', target: 'out/live.ndjson' })]),
      'GET /api/v1/status': { data: status({ leases: [{ name: 'sinks', purpose: 'watch', pid: 4121, host: 'srv-1' }] }) },
    })
    ;({ w } = await mountScreen(SinksScreen, '/system/sinks'))
    const { useStatusStore } = await import('@/app/statusStore')
    await useStatusStore().refresh()
    await flush()
    const rows = w.findAll('tbody tr')
    expect(rows[0].find('[data-status="sink:retrying"]').exists()).toBe(true)
    expect(rows[0].text()).toContain('1,240 · 6 min')
    expect(rows[1].find('[data-status="sink:delivering"]').exists()).toBe(true)
    expect(w.find('[data-testid="sinks-holder"]').text()).toContain('watch · pid 4121 · srv-1')
    expect(w.find('[data-testid="sinks-notes"]').text()).toContain('HTTP 503')
    expect(w.text()).toContain(t('ui.sinks.configNote'))
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('behind after a minute; nobody delivers; empty; error; 401', async () => {
    mockFetch({ 'GET /api/v1/sinks': page([sink({ lag_events: 50, lag_seconds: 120 }), sink({ name: 'b', served: false })]), 'GET /api/v1/status': { data: status() } })
    ;({ w } = await mountScreen(SinksScreen, '/system/sinks'))
    await flush()
    expect(w.find('[data-status="sink:behind"]').exists()).toBe(true)
    w.unmount()
    mockFetch({ 'GET /api/v1/sinks': page([sink({ served: false })]), 'GET /api/v1/status': { data: status() } })
    ;({ w } = await mountScreen(SinksScreen, '/system/sinks'))
    await flush()
    expect(w.find('[data-status="sink:unserved"]').exists()).toBe(true)
    expect(w.find('[data-testid="sinks-holder"]').text()).toContain(t('ui.sinks.nobody'))
    w.unmount()
    mockFetch({ 'GET /api/v1/sinks': page([]) })
    ;({ w } = await mountScreen(SinksScreen, '/system/sinks'))
    await flush()
    expect(w.find('[data-testid="empty"]').text()).toContain(t('ui.sinks.empty'))
    w.unmount()
    mockFetch({ 'GET /api/v1/sinks': () => v1Error(500, 'internal') })
    ;({ w } = await mountScreen(SinksScreen, '/system/sinks'))
    await flush()
    expect(w.find('[data-testid="error-state"]').exists()).toBe(true)
    w.unmount()
    mockFetch({ 'GET /api/v1/sinks': () => v1Error(401, 'unauthorized') })
    ;({ w } = await mountScreen(SinksScreen, '/system/sinks'))
    await flush()
    expect(authNeeded.value).toBe(true)
  })
})

describe('Job detail: the output of a data job', () => {
  it('an export shows its file with a download; a backup its archive; a restore check what it found', async () => {
    useFakeES()
    mockFetch({
      'GET /api/v1/jobs/EX1': { data: job({ id: 'EX1', kind: 'export', spec: { profile: 'legacy-wide-csv', dataset: 'events', format: 'csv' }, result: { export: { rows: 4120, events: 4120, bytes: 1258291, skipped: 0, file: 'EX1.csv' } } }) },
    })
    ;({ w } = await mountScreen(JobDetailScreen, '/jobs/EX1', '/jobs/:id'))
    await flush()
    const out = w.find('[data-testid="job-output"]')
    expect(out.text()).toContain('EX1.csv')
    expect(out.find('a[download]').attributes('href')).toBe('/api/v1/exports/EX1/download')
    expect(w.find('h1').text()).toContain(t('ui.exports.kind.legacy'))
    w.unmount()
    mockFetch({
      'GET /api/v1/jobs/BK1': { data: job({ id: 'BK1', kind: 'backup', spec: { scope: 'all', include_env: true }, result: { backup: { name: 'b.zip', scope: 'all', with_env: true, bytes: 1024, format: 2 } } }) },
    })
    ;({ w } = await mountScreen(JobDetailScreen, '/jobs/BK1', '/jobs/:id'))
    await flush()
    expect(w.find('[data-testid="job-output"] a[download]').attributes('href')).toBe('/api/v1/backups/b.zip')
    expect(w.find('[data-testid="job-output"]').text()).toContain(t('ui.backups.withEnv'))
  })
})
