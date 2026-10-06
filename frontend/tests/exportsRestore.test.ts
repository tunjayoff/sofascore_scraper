import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import ExportsScreen from '@/screens/exports/ExportsScreen.vue'
import BackupsScreen from '@/screens/backups/BackupsScreen.vue'
import MaintenanceScreen from '@/screens/MaintenanceScreen.vue'
import { resetSports } from '@/app/sports'
import { resetNames } from '@/screens/events/eventText'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { callsTo, flush, mockFetch } from './helpers'
import { axeViolations, job, mountScreen, page, status, v1Error } from './v1'

/**
 * FX-14b: normalized exports as CSV, JSONL, Parquet and SQLite (SC-2, P28), the files `ssc export` wrote
 * (FX-19), the real restore with its confirmation (FX-13), and deleting one league's or one season's data
 * in Data cleanup (FX-19). No request leaves the test.
 */
const t = i18n.global.t
let w: VueWrapper

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
  resetSports()
  resetNames()
})
afterEach(() => w?.unmount())

const body = (f: ReturnType<typeof mockFetch>, key: string, i = 0) => JSON.parse(String(callsTo(f, key)[i][1]!.body))
const exportRow = (over = {}) => ({
  id: 'EX1', job_id: 'EX1', source: 'job', state: 'succeeded', dataset: 'events', format: 'csv', schema: 'normalized', profile: null,
  filter: { sport: null, tournament_ids: [], season_ids: [], event_ids: [] }, created_at: '2026-10-06T10:00:00Z', finished_at: '2026-10-06T10:01:00Z',
  rows: 380, events: 380, bytes: 81920, skipped: 0, file: 'premier-league_2026-10-06_x7k2m9qa.csv', media_type: 'text/csv', available: true, ...over,
})

describe('exports', () => {
  const routes = (parquet: boolean, over: Record<string, unknown> = {}) => ({
    'GET /api/v1/exports': page([]),
    'GET /api/v1/status': { data: status({ capabilities: { parquet, sse: true, scheduler: false } }) },
    'GET /api/v1/sports': { data: [], page: { limit: 0 } },
    'GET /api/v1/tournaments': page([{ id: 17, sport: 'football', category_id: 1, name: 'Premier League', slug: 'pl', followed: true }]),
    'POST /api/v1/jobs': { data: job({ id: 'EX9', kind: 'export', state: 'running' }) },
    ...over,
  })
  async function openDialog(f: ReturnType<typeof mockFetch>) {
    ;({ w } = await mountScreen(ExportsScreen, '/exports?new=1', '/exports'))
    const { useStatusStore } = await import('@/app/statusStore')
    await useStatusStore().refresh()
    await flush()
    return f
  }

  it('normalized data: every dataset of the API, as SQLite; the status and time filters are sent only when set', async () => {
    const f = await openDialog(mockFetch(routes(false)))
    const dialog = () => w.find('[role="dialog"]')
    await dialog().find('input[value="normalized"]').setValue(true)
    expect(dialog().findAll('[data-testid="export-datasets"] input').map((i) => (i.element as HTMLInputElement).value)).toEqual(['events', 'slices', 'changes', 'odds', 'standings'])
    expect(dialog().find('[data-format="parquet"] input').attributes('disabled')).toBeDefined()
    expect(dialog().find('[data-testid="export-no-parquet"]').text()).toBe(t('ui.exports.dialog.noParquet'))
    await dialog().find('input[value="odds"]').setValue(true)
    await dialog().find('[data-format="sqlite"] input').setValue(true)
    expect(dialog().text()).toContain(t('ui.exports.formatHint.sqlite'))
    await dialog().find('input[type="checkbox"][value="completed"]').setValue(true)
    await dialog().findAll('input[type="date"]')[0].setValue('2026-08-01')
    expect(await axeViolations(dialog().element)).toEqual([])
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs')).toEqual({
      kind: 'export',
      spec: { dataset: 'odds', format: 'sqlite', schema: 'normalized', profile: null, filter: { sport: null, tournament_ids: [], season_ids: [], event_ids: [], status_classes: ['completed'], from: '2026-08-01' } },
    })
  })

  it('Parquet when the server has pyarrow; score changes filter by time of record and have no status filter', async () => {
    const f = await openDialog(mockFetch(routes(true)))
    const dialog = () => w.find('[role="dialog"]')
    await dialog().find('input[value="normalized"]').setValue(true)
    expect(dialog().find('[data-testid="export-no-parquet"]').exists()).toBe(false)
    await dialog().find('input[value="changes"]').setValue(true)
    await dialog().find('[data-format="parquet"] input').setValue(true)
    expect(dialog().find('input[type="checkbox"][value="completed"]').exists()).toBe(false)
    expect(dialog().text()).toContain(t('ui.exports.dialog.recordedFrom'))
    const dates = dialog().findAll('input[type="date"]')
    await dates[0].setValue('2026-10-05')
    await dates[1].setValue('2026-10-01')
    expect(dialog().find('[role="alert"]').text()).toBe(t('ui.exports.dialog.badRange'))
    expect(w.find('[data-testid="confirm"]').attributes('disabled')).toBeDefined()
    await dates[1].setValue('2026-10-06')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs').spec).toMatchObject({ dataset: 'changes', format: 'parquet', schema: 'normalized', filter: { from: '2026-10-05', to: '2026-10-06' } })
  })

  it('lists readable file names, and the files ssc export wrote without a job', async () => {
    mockFetch({
      'GET /api/v1/exports': page([
        exportRow(),
        exportRow({ id: 'file:standings_2026-10-05_cli.parquet', job_id: null, source: 'file', dataset: 'standings', format: 'parquet', file: 'standings_2026-10-05_cli.parquet', rows: null }),
      ]),
    })
    ;({ w } = await mountScreen(ExportsScreen, '/exports'))
    await flush()
    const rows = w.findAll('tbody tr')
    expect(rows[0].text()).toContain('premier-league_2026-10-06_x7k2m9qa.csv')
    expect(rows[0].text()).toContain(`${t('ui.exports.schema.normalized')} · ${t('ui.exports.dataset.events')} · CSV`)
    expect(rows[1].text()).toContain(`${t('ui.exports.dataset.standings')} · Parquet`)
    expect(rows[1].find('[data-testid="export-from-cli"]').text()).toBe(t('ui.exports.fromCli'))
    expect(rows[1].find('a[download]').attributes('href')).toBe('/api/v1/exports/file%3Astandings_2026-10-05_cli.parquet/download')
    // no job to open for a file of ssc export
    expect(rows[1].find('a[href^="/jobs/"]').exists()).toBe(false)
    expect(await axeViolations(w.element)).toEqual([])
  })
})

describe('restore', () => {
  const backup = { name: 'backup_all_20261001_221000.zip', scope: 'all', created_at_utc: '2026-10-01T22:10:00Z', bytes: 2040, format: 2, with_env: false }
  const checkReport = (occupied: string[]) => ({ name: backup.name, format: 2, scope: 'all', dry_run: true, force: false, restored: [], replaced: [], skipped: [], occupied, counts: { v3_events: 380 } })
  const restored = { restore: { name: backup.name, format: 2, scope: 'all', dry_run: false, force: false, restored: ['v3'], replaced: [], skipped: [], occupied: [], counts: { v3_events: 380 }, catalog_rebuilt: true, verify_ok: true, verify_issues: 0 } }

  async function toStep3(f: ReturnType<typeof mockFetch>) {
    ;({ w } = await mountScreen(BackupsScreen, '/backups'))
    await flush()
    await w.find('tbody tr button[aria-haspopup="menu"]').trigger('click')
    await w.find('[data-key="restore"]').trigger('click')
    await flush()
    await w.find('[data-testid="run-check"]').trigger('click')
    await flush()
    await w.find('[data-testid="next"]').trigger('click')
    await w.find('[data-testid="next"]').trigger('click')
    await flush()
    return f
  }

  it('restores as a job after the typed word, follows it, and shows the result', async () => {
    const f = await toStep3(
      mockFetch({
        'GET /api/v1/backups': page([backup]),
        'GET /api/v1/status': { data: status() },
        'POST /api/v1/jobs': (init?: RequestInit) => {
          const dry = JSON.parse(String(init!.body)).spec.dry_run
          return { data: job({ id: dry ? 'CHK' : 'RST', kind: 'restore', state: 'running', finished_at: null }) }
        },
        'GET /api/v1/jobs/CHK': { data: job({ id: 'CHK', kind: 'restore', state: 'succeeded', result: { restore: checkReport([]) } }) },
        'GET /api/v1/jobs/RST': { data: job({ id: 'RST', kind: 'restore', state: 'succeeded', spec: { name: backup.name, dry_run: false }, result: restored }) },
      }),
    )
    const run = () => w.find('[data-testid="restore-run"]')
    expect(run().attributes('disabled')).toBeDefined()
    await w.find('[data-testid="restore-word"]').setValue(t('ui.restore.word').toLowerCase())
    expect(run().attributes('disabled')).toBeUndefined()
    await run().trigger('click')
    await flush()
    await flush()
    expect(body(f, 'POST /api/v1/jobs', 1)).toEqual({ kind: 'restore', spec: { name: backup.name, force: false, dry_run: false } })
    expect(w.find('[data-testid="restore-done"]').text()).toBe(t('ui.restore.done'))
    const out = w.find('[data-testid="job-output"]')
    expect(out.text()).toContain(t('ui.jobOutput.restored'))
    expect(out.text()).toContain(t('ui.jobOutput.verifyOk'))
    expect(await axeViolations(w.find('[role="dialog"]').element)).toEqual([])
  })

  it('a folder that is not empty answers confirmation_required: the dialog says what is there and asks before it sends force', async () => {
    let posts = 0
    const f = await toStep3(
      mockFetch({
        'GET /api/v1/backups': page([backup]),
        'GET /api/v1/status': { data: status() },
        'POST /api/v1/jobs': (init?: RequestInit) => {
          posts++
          const spec = JSON.parse(String(init!.body)).spec
          if (spec.dry_run) return { data: job({ id: 'CHK', kind: 'restore', state: 'running' }) }
          if (!spec.force) return v1Error(400, 'confirmation_required', { occupied: ['v3', '.meta/state.db'], name: backup.name })
          return { data: job({ id: 'RST', kind: 'restore', state: 'running' }) }
        },
        'GET /api/v1/jobs/CHK': { data: job({ id: 'CHK', kind: 'restore', state: 'succeeded', result: { restore: checkReport([]) } }) },
        'GET /api/v1/jobs/RST': { data: job({ id: 'RST', kind: 'restore', state: 'succeeded', result: restored }) },
      }),
    )
    await w.find('[data-testid="restore-word"]').setValue(t('ui.restore.word'))
    await w.find('[data-testid="restore-run"]').trigger('click')
    await flush()
    const asked = w.find('[data-testid="restore-occupied"]')
    expect(asked.text()).toContain(t('ui.restore.occupiedNow', { what: 'v3, .meta/state.db' }))
    expect(posts).toBe(2)
    await asked.find('[data-testid="restore-force"]').trigger('click')
    await flush()
    await flush()
    expect(body(f, 'POST /api/v1/jobs', 2).spec).toEqual({ name: backup.name, force: true, dry_run: false })
    expect(w.find('[data-testid="restore-done"]').exists()).toBe(true)
  })

  it('a refused restore (409, a job runs) stays in the dialog with its reason; the server command is still offered', async () => {
    await toStep3(
      mockFetch({
        'GET /api/v1/backups': page([backup]),
        'GET /api/v1/status': { data: status() },
        'POST /api/v1/jobs': (init?: RequestInit) =>
          JSON.parse(String(init!.body)).spec.dry_run ? { data: job({ id: 'CHK', kind: 'restore', state: 'running' }) } : v1Error(409, 'job_running', { holder: { pid: 7 } }),
        'GET /api/v1/jobs/CHK': { data: job({ id: 'CHK', kind: 'restore', state: 'succeeded', result: { restore: checkReport([]) } }) },
      }),
    )
    await w.find('[data-testid="restore-word"]').setValue(t('ui.restore.word'))
    await w.find('[data-testid="restore-run"]').trigger('click')
    await flush()
    expect(w.find('[role="dialog"] [data-testid="form-error"]').attributes('data-code')).toBe('job_running')
    expect(w.find('[data-testid="restore-cli"]').text()).toContain(t('ui.restore.orServer'))
  })
})

describe('Data cleanup: one league’s data', () => {
  it('a league, or one of its seasons, is cleared after the typed word (clear with tournament_id and season_id)', async () => {
    const f = mockFetch({
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/tournaments': page([{ id: 17, sport: 'football', category_id: 1, name: 'Premier League', slug: 'pl', followed: true }]),
      'GET /api/v1/tournaments/17/seasons': { data: [{ id: 61627, tournament_id: 17, name: 'Premier League 24/25', year: '24/25' }], page: { limit: 0 } },
      'POST /api/v1/jobs': { data: job({ id: 'CL1', kind: 'clear', state: 'running', spec: { scope: 'all', tournament_id: 17, season_id: 61627 } }) },
    })
    ;({ w } = await mountScreen(MaintenanceScreen, '/maintenance'))
    await flush()
    const card = w.find('[data-testid="card-clear-league"]')
    expect(card.find('[data-testid="clear-league-open"]').attributes('disabled')).toBeDefined()
    await card.find('[data-testid="clear-league"]').setValue('17')
    await flush()
    await card.find('[data-testid="clear-season"]').setValue('61627')
    await card.find('[data-testid="clear-league-open"]').trigger('click')
    await flush()
    const dialog = w.find('[role="alertdialog"]')
    expect(dialog.text()).toContain(t('ui.maintenance.league.confirmSeason', { name: 'Premier League', season: 'Premier League 24/25' }))
    expect(w.find('[data-testid="confirm"]').attributes('disabled')).toBeDefined()
    await dialog.find('input').setValue(t('ui.maintenance.clear.word'))
    expect(await axeViolations(dialog.element)).toEqual([])
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs')).toEqual({ kind: 'clear', spec: { scope: 'all', tournament_id: 17, season_id: 61627, confirm: true } })
  })
})
