import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import App from '@/App.vue'
import { routes } from '@/router'
import JobOutput from '@/screens/jobs/JobOutput.vue'
import { codeText, logLine } from '@/screens/jobs/eventText'
import { ageText, jobKindText, jobLabel, jobLeague, jobTarget, noteFollowNames, followNames, rerunBody } from '@/screens/jobs/jobText'
import { resetNames, tournamentNames } from '@/screens/events/eventText'
import { authNeeded } from '@/lib/auth'
import { helpOpen, openHelp } from '@/app/help'
import { tokenInUse } from '@/app/session'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import en from '@/locales/en'
import { flush, mockFetch } from './helpers'
import { axeViolations, job, page, status, useFakeES } from './v1'

/**
 * FX-14b, the job's words: the coded log lines of the download path (FX-13, FX-19) in the reader's
 * language, with ids shown by name; the kinds that have a variant by their spec (a season list, the
 * clean-up of old odds, the deletion of one league's data, a real restore); and where Health is found.
 */
const t = i18n.global.t
let w: VueWrapper | null = null

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
  resetNames()
  followNames.value = new Map()
  helpOpen.value = false
  authNeeded.value = false
  tokenInUse.value = false
  useFakeES()
})
afterEach(() => {
  w?.unmount()
  w = null
  setLocale('en')
})

/** Every code the download path writes (src/services/sync.py, FX-13 and FX-19). */
const CODES: Record<string, Record<string, unknown>> = {
  sync_season_list: { league_id: 17 },
  sync_season_list_failed: { league_id: 17, reason: '404' },
  sync_schedule: { league_id: 17, season_id: 61627 },
  sync_schedule_failed: { league_id: 17, season_id: 61627, reason: 'timeout' },
  sync_season_outdated: { season_id: 52186, resolved: 61627, league_id: 17 },
  sync_follow_skipped: { follow: 'team:42' },
  sync_refreshing: { count: 12 },
  sync_details: { league_id: null, count: 1204 },
  sync_details_checking: {},
  sync_details_selected: { count: 3 },
  sync_breaker_stopped: { reason: '429', what: 'match details' },
  sync_extras: { stored: 5, failed: 1 },
  sync_follow_listing: { follow: 'team:42', name: 'Arsenal' },
  sync_follow_listing_failed: { follow: 'player:7', name: 'Bukayo Saka', reason: 'parse' },
  sync_follow_details: { count: 4 },
  fetch_zero_matches: {},
}

describe('coded job log lines', () => {
  it('every code of the download path has a text in both languages, with no English left in Turkish', () => {
    tournamentNames.value = new Map([[17, { id: 17, name: 'Premier League', sport: 'football', category_id: 1, slug: 'pl' }]])
    for (const lang of ['en', 'tr'] as const) {
      setLocale(lang)
      for (const [code, params] of Object.entries(CODES)) {
        const text = codeText(code, params)
        expect(text, `${lang} ${code}`).toBeTruthy()
        expect(text, `${lang} ${code}`).not.toMatch(/\{[a-z]+\}/)
        if (lang === 'tr') expect(text, code).not.toMatch(/\b(Fetching|Refreshing|could not|skipped|stored|failed|match details|season)\b/)
      }
    }
  })

  it('names leagues, seasons and follows instead of their ids, and translates the reasons', () => {
    tournamentNames.value = new Map([[17, { id: 17, name: 'Premier League', sport: 'football', category_id: 1, slug: 'pl' }]])
    setLocale('tr')
    expect(codeText('sync_season_list', { league_id: 17 })).toBe('Premier League için sezon listesi okunuyor…')
    expect(codeText('sync_season_list_failed', { league_id: 17, reason: '404' })).toBe('Premier League için sezon listesi okunamadı (bulunamadı (404)).')
    expect(codeText('sync_details', { league_id: null, count: 1204 })).toBe('Maç ayrıntıları indiriliyor: Tüm ligler (1.204 maç)…')
    expect(codeText('sync_breaker_stopped', { reason: '429', what: 'match details' })).toBe('İndirme durdu (maç ayrıntıları): SofaScore yavaşlamamızı istedi (429).')
    expect(codeText('sync_follow_listing', { follow: 'team:42', name: 'Arsenal' })).toBe('Arsenal için maç listesi okunuyor…')
    // without a name in the params, a noted follow name, else "Team #42"
    expect(codeText('sync_follow_skipped', { follow: 'team:42' })).toBe('Takım #42 etkin bir takip değil; atlandı.')
    noteFollowNames([{ id: 'team:42', name: 'Arsenal' }])
    expect(codeText('sync_follow_skipped', { follow: 'team:42' })).toBe('Arsenal etkin bir takip değil; atlandı.')
    setLocale('en')
    expect(codeText('sync_schedule', { league_id: 18, season_id: 9 })).toBe('Reading the matches of League #18, season #9…')
  })

  it('a line with an unknown code (or none) shows the server text as it is', () => {
    const line = logLine({ seq: 3, ts_ms: 0, type: 'log', data: { message: 'Something new happened', code: 'sync_brand_new', params: {} } } as never)
    expect(line.raw).toBe(true)
    expect(line.text).toBe('Something new happened')
    const coded = logLine({ seq: 4, ts_ms: 0, type: 'log', data: { message: 'Fetching 4 matches…', code: 'sync_follow_details', params: { count: 4 } } } as never)
    expect(coded.raw).toBeUndefined()
    expect(coded.text).toBe('Downloading 4 matches of team, player and match follows…')
  })

  it('the Job detail log shows the Turkish line, not the English message', async () => {
    setLocale('tr')
    mockFetch({
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/jobs/J1': { data: job({ id: 'J1', state: 'running', finished_at: null, spec: { follows: ['team:42'] } }) },
      'GET /api/v1/tournaments': page([]),
      'GET /api/v1/jobs': page([]),
      'GET /api/v1/settings': { data: { config_file: null, overrides_file: null, settings: [] } },
      'GET /api/v1/follows': page([]),
    })
    setActivePinia(createPinia())
    const router: Router = createRouter({ history: createMemoryHistory(), routes })
    await router.push('/jobs/J1')
    await router.isReady()
    w = mount(App, { global: { plugins: [i18n, router] }, attachTo: document.body })
    await flush()
    const { FakeES } = await import('./v1')
    FakeES.last.emit('log', 5, { message: 'Fetching the match list of team:42 (Arsenal)...', code: 'sync_follow_listing', params: { follow: 'team:42', name: 'Arsenal' } }, 'J1')
    await flush()
    const log = document.body.textContent ?? ''
    expect(log).toContain('Arsenal için maç listesi okunuyor…')
    expect(log).not.toContain('Fetching the match list')
  })
})

describe('job kinds and targets by their spec', () => {
  it('a season list, the clean-up of old odds, one league’s deletion and a real restore have their own names', () => {
    // the body says only: seasons; the recorded spec (GET /jobs) says mode: seasons
    expect(jobKindText('sync', { only: 'seasons', league_id: 52 })).toBe(t('ui.job.kind.seasonList'))
    expect(jobKindText('sync', { mode: 'seasons', league_id: 52, selections: [] })).toBe(t('ui.job.kind.seasonList'))
    expect(jobKindText('clear', { scope: 'history', older_than: '90d' })).toBe('Clean-up of old odds')
    expect(jobKindText('clear', { scope: 'all', tournament_id: 17, confirm: true })).toBe(t('ui.job.kind.clearLeague'))
    expect(jobKindText('restore', { name: 'b.zip', dry_run: false })).toBe('Restore')
    expect(jobKindText('restore', { name: 'b.zip', dry_run: true })).toBe(t('ui.job.kind.restore'))
    expect(jobLabel({ kind: 'sync', spec: {} })).toBe(t('ui.job.kind.sync'))
  })

  it('the scheduled prune job reads "Older than 90 days" in either language', () => {
    const prune = job({ kind: 'clear', spec: { scope: 'history', older_than: '90d' } })
    expect(jobTarget(prune)).toBe('Older than 90 days')
    expect(ageText('1d')).toBe('1 day')
    expect(ageText('12h')).toBe('12 hours')
    expect(ageText('soon')).toBe('soon')
    setLocale('tr')
    expect(jobKindText(prune.kind, prune.spec)).toBe('Eski oranların temizliği')
    expect(jobTarget(prune)).toBe('90 gün öncesine ait olanlar')
  })

  it('a download of named follows shows the follow, a league clear its league and season', () => {
    tournamentNames.value = new Map([[17, { id: 17, name: 'Premier League', sport: 'football', category_id: 1, slug: 'pl' }]])
    expect(jobTarget(job({ spec: { follows: ['team:42'] } }))).toBe('Team #42')
    noteFollowNames([{ id: 'team:42', name: 'Arsenal' }])
    expect(jobTarget(job({ spec: { follows: ['team:42'] } }))).toBe('Arsenal')
    expect(jobTarget(job({ spec: { follows: ['tournament:17'] } }))).toBe('Premier League')
    expect(jobTarget(job({ spec: { follows: ['team:42', 'player:7'] } }))).toBe('2 follows')
    expect(jobTarget(job({ kind: 'fetch', spec: { event_ids: [1, 2, 3] } }))).toBe('3 matches')
    expect(jobTarget(job({ kind: 'clear', spec: { scope: 'all', tournament_id: 17, season_id: 61627 } }))).toBe('Premier League · #61627')
    expect(jobLeague(job({ spec: { follows: ['tournament:17'] } }))).toBe(17)
    expect(jobLeague(job({ spec: { follows: ['team:42'] } }))).toBeNull()
    expect(jobLeague(job({ kind: 'clear', spec: { tournament_id: 17 } }))).toBe(17)
  })

  it('"Run again" keeps the follows, the season-list choice and the event ids', () => {
    expect(rerunBody(job({ spec: { follows: ['team:42'], only: null } }))).toEqual({ kind: 'sync', spec: { follows: ['team:42'], only: null } })
    expect(rerunBody(job({ spec: { mode: 'seasons', league_id: 52, selections: [] } }))).toEqual({ kind: 'sync', spec: { league_id: 52, only: 'seasons' } })
    expect(rerunBody(job({ kind: 'fetch', spec: { event_ids: [5] } }))).toEqual({ kind: 'fetch', spec: { event_ids: [5] } })
    expect(rerunBody(job({ kind: 'refresh', spec: { event_ids: [5] } }))).toEqual({ kind: 'refresh', spec: { event_ids: [5] } })
  })
})

describe('what a finished job produced', () => {
  const output = async (over: Parameters<typeof job>[0]) => {
    setActivePinia(createPinia())
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: { template: '<div />' } }] })
    w = mount(JobOutput, { props: { job: job(over) }, global: { plugins: [i18n, router] }, attachTo: document.body })
    await flush()
    return w
  }

  it('the clean-up of old odds: how old and how many snapshots were removed', async () => {
    await output({ kind: 'clear', spec: { scope: 'history', older_than: '90d' }, result: { prune_history: { older_than: '90d', cutoff_utc: '2026-07-08T00:00:00Z', removed: 1204 } } })
    const text = w!.find('[data-testid="output-prune"]').text()
    expect(text).toContain('90 days')
    expect(text).toContain('1,204')
  })

  it('one league’s deletion: the league, the matches and the schedules removed', async () => {
    tournamentNames.value = new Map([[17, { id: 17, name: 'Premier League', sport: 'football', category_id: 1, slug: 'pl' }]])
    await output({ kind: 'clear', spec: { scope: 'all', tournament_id: 17 }, result: { clear: { scopes: ['tournament'], tournament_id: 17, season_id: null, events: 380, event_dirs: 380, listings: 4, catalog_rebuilt: true } } })
    const text = w!.find('[data-testid="output-clear-league"]').text()
    expect(text).toContain('Premier League')
    expect(text).toContain('380')
    expect(await axeViolations(w!.element)).toEqual([])
  })

  it('a real restore: the index rebuilt and the check after it, with its problems', async () => {
    await output({ kind: 'restore', spec: { name: 'b.zip', dry_run: false, force: true }, result: { restore: { name: 'b.zip', dry_run: false, counts: {}, catalog_rebuilt: true, verify_ok: false, verify_issues: 2 } } })
    expect(w!.text()).toContain(t('ui.jobOutput.restored'))
    expect(w!.text()).toContain(t('ui.jobOutput.verifyIssues', { n: '2' }))
    expect(w!.text()).not.toContain(t('ui.jobOutput.dryRun'))
  })
})

describe('Health is found from the app', () => {
  async function app(path: string) {
    mockFetch({
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/jobs': page([]),
      'GET /api/v1/settings': { data: { config_file: null, overrides_file: null, settings: [] } },
      'GET /api/v1/follows': page([]),
      'GET /api/v1/sinks': page([]),
      'GET /api/v1/changes': page([]),
      'GET /api/v1/tournaments': page([]),
    })
    setActivePinia(createPinia())
    const router = createRouter({ history: createMemoryHistory(), routes })
    await router.push(path)
    await router.isReady()
    w = mount(App, { global: { plugins: [i18n, router] }, attachTo: document.body })
    await flush()
    return router
  }

  it('Ctrl K "health" and "sağlık" lead to the Health screen, in either language', async () => {
    setLocale('tr')
    const router = await app('/jobs')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }))
    await flush()
    for (const q of ['health', 'sağlık']) {
      await w!.find('input[role="combobox"]').setValue(q)
      await flush()
      expect(w!.find('[role="option"][aria-selected="true"]').text()).toContain('Sağlık')
    }
    await w!.find('input[role="combobox"]').trigger('keydown', { key: 'Enter' })
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/system/health'))
  })

  it('Help says where Health is and links it', async () => {
    const router = await app('/jobs')
    openHelp()
    await flush()
    const section = w!.find('[data-testid="help-health"]')
    expect(section.text()).toContain('/system/health')
    await section.find('a').trigger('click')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/system/health'))
  })

  it('Health shows the server’s last check and last failure in a browser that did not run the check (FX-19 connection)', async () => {
    mockFetch({
      'GET /api/v1/status': {
        data: status({
          connection: { state: 'failed', last_success_at: '2026-10-02T09:00:00Z', last_failure_at: '2026-10-02T10:00:00Z', last_failure_reason: 'timeout', last_failure_status: null, last_check: { at: '2026-10-02T10:00:00Z', ok: false, reason: 'network' } },
        }),
      },
    })
    const { mountScreen } = await import('./v1')
    const HealthScreen = (await import('@/screens/HealthScreen.vue')).default
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    const card = w!.find('[data-testid="health-connection"]')
    expect(card.find('[data-status="connection:checkFailed"]').exists()).toBe(true)
    expect(card.find('[data-testid="connection-last-check"]').text()).toContain(t('ui.health.check.failed', { reason: t('ui.health.check.reason.network') }))
    expect(card.find('[data-testid="connection-last-failure"]').text()).toContain(t('ui.job.reason.timeout'))
    expect(await axeViolations(w!.element)).toEqual([])
  })

  it('every new text exists in English', () => {
    expect(Object.keys((en.ui.job as Record<string, Record<string, string>>).code)).toEqual(expect.arrayContaining(Object.keys(CODES)))
  })
})
