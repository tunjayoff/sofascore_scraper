import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import App from '@/App.vue'
import { routes } from '@/router'
import BottomBar from '@/app/BottomBar.vue'
import HelpTip from '@/ui/HelpTip.vue'
import PageHeader from '@/ui/PageHeader.vue'
import FollowsScreen from '@/screens/follows/FollowsScreen.vue'
import FollowEditorScreen from '@/screens/follows/FollowEditorScreen.vue'
import FollowDetailScreen from '@/screens/follows/FollowDetailScreen.vue'
import FollowActions from '@/screens/follows/FollowActions.vue'
import EventDetailScreen from '@/screens/events/EventDetailScreen.vue'
import EventsScreen from '@/screens/events/EventsScreen.vue'
import JobsScreen from '@/screens/jobs/JobsScreen.vue'
import HealthScreen from '@/screens/HealthScreen.vue'
import OverviewScreen from '@/screens/OverviewScreen.vue'
import BackupsScreen from '@/screens/backups/BackupsScreen.vue'
import { useStatusStore } from '@/app/statusStore'
import { JOB_WATCH_MS, onJobEnded, tellEnd, watchJob, watchJobs } from '@/app/jobWatch'
import { HELP_TERMS, readmeUrl } from '@/app/help'
import { authNeeded } from '@/lib/auth'
import { tokenInUse } from '@/app/session'
import { railCollapsed, startCardHidden } from '@/ui/prefs'
import { connectionState } from '@/ui/status'
import { clearToasts, uiToasts } from '@/ui/toast'
import { resetSports } from '@/app/sports'
import { resetNames } from '@/screens/events/eventText'
import { countsText, jobTarget } from '@/screens/jobs/jobText'
import { MORE_FOLLOW_KINDS } from '@/screens/follows/followText'
import { i18n, setLocale } from '@/i18n'
import { callsTo, flush, mockFetch } from './helpers'
import { axeViolations, event, follow, job, mountScreen, page, slice, sport, status, useFakeES } from './v1'

/**
 * FX-14a, the newcomer pass: where "add a league" is, the quick search's actions and its SofaScore search,
 * the plain words, help, what is said when a download ends, an honest connection state and the small
 * fixes. No test reaches a server or SofaScore (helpers.ts: mockFetch).
 */
const t = i18n.global.t
let w: VueWrapper
let router: Router

const list = <T>(data: T[]) => ({ data, page: { limit: 0, next_cursor: null } })
const SPORTS = { 'GET /api/v1/sports': list([sport('football')]), 'GET /api/v1/sports/football': { data: sport('football') } }
const TOURNAMENTS = { 'GET /api/v1/tournaments': page([{ id: 17, sport: 'football', category_id: 1, name: 'Premier League', slug: 'pl', followed: true }]) }
const key = (k: string, init: KeyboardEventInit = {}) => document.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, ...init }))

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
  resetSports()
  resetNames()
  authNeeded.value = false
  tokenInUse.value = false
  railCollapsed.value = '0'
  startCardHidden.value = '0'
  useFakeES()
})
afterEach(() => {
  w?.unmount()
  vi.useRealTimers()
})

async function app(path: string, extra: Record<string, unknown> = {}, statusOver = {}) {
  const f = mockFetch({
    'GET /api/v1/status': { data: status(statusOver) },
    'GET /api/v1/jobs': { data: [job()], page: { limit: 20, next_cursor: null } },
    'GET /api/v1/settings': { data: { config_file: null, overrides_file: null, settings: [] } },
    'GET /api/v1/follows': { data: [], page: { limit: 0, next_cursor: null } },
    'GET /api/v1/sinks': { data: [], page: { limit: 0, next_cursor: null } },
    'GET /api/v1/changes': { data: [], page: { limit: 5, next_cursor: null } },
    ...TOURNAMENTS,
    ...extra,
  })
  setActivePinia(createPinia())
  router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(path)
  await router.isReady()
  w = mount(App, { global: { plugins: [i18n, router] }, attachTo: document.body })
  await flush()
  await flush()
  return f
}

describe('adding a league is always one click away', () => {
  it('the top bar and the Overview header have "Add league", on every screen with data', async () => {
    await app('/jobs')
    const top = w.find('[data-testid="topbar-add-league"]')
    expect(top.text()).toBe(t('ui.shell.addLeague'))
    expect(top.attributes('href')).toBe('/follows/new')
    await router.push('/')
    await flush()
    await flush()
    const header = w.find('[data-testid="overview-add-league"]')
    expect(header.classes()).toContain('u-btn-primary')
    expect(header.attributes('href')).toBe('/follows/new')
    expect(w.find('[data-testid="sync-all"]').text()).toBe(t('ui.overview.syncAll'))
    // the editor itself does not offer it again
    await router.push('/follows/new')
    await flush()
    expect(w.find('[data-testid="topbar-add-league"]').exists()).toBe(false)
  })

  it('Leagues & follows says "Add league"; the editor is "Add a league or team"', async () => {
    setLocale('tr')
    mockFetch({ ...SPORTS, 'GET /api/v1/follows': list([follow()]), 'GET /api/v1/jobs': page([]), 'GET /api/v1/status': { data: status() } })
    ;({ w } = await mountScreen(FollowsScreen, '/follows'))
    await flush()
    expect(w.find('h1').text()).toBe('Ligler ve takipler')
    expect(w.find('[data-testid="follows-add"]').text()).toBe('Lig ekle')
    // the origin in words, never the raw "api"
    expect(w.find('[data-status="origin:api"]').text()).toBe('Buradan eklendi')
    expect(w.find('tbody').text()).not.toMatch(/\bapi\b/)
    w.unmount()
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/new'))
    await flush()
    expect(w.find('h1').text()).toBe('Lig ya da takım ekle')
  })

  it('the phone\'s "More" sheet starts with "Add league" and Help', async () => {
    setActivePinia(createPinia())
    router = createRouter({ history: createMemoryHistory(), routes })
    await router.push('/jobs')
    await router.isReady()
    w = mount({ components: { BottomBar }, template: '<div class="u-app"><BottomBar @help="help = true" /><p v-if="help" id="helped">help</p></div>', data: () => ({ help: false }) }, { global: { plugins: [i18n, router] }, attachTo: document.body })
    expect(w.find('.u-bottombar').text()).toContain(t('ui.nav.followsShort'))
    await w.find('.u-bottombar > button').trigger('click')
    expect(w.find('[data-testid="more-add-league"]').attributes('href')).toBe('/follows/new')
    await w.find('[data-testid="more-help"]').trigger('click')
    expect(w.find('#helped').exists()).toBe(true)
  })
})

describe('quick search', () => {
  it('finds the actions in either language: "lig ekle" opens the editor', async () => {
    setLocale('tr')
    await app('/')
    key('k', { ctrlKey: true })
    await flush()
    await w.find('input[role="combobox"]').setValue('lig ekle')
    const first = w.find('[role="option"][aria-selected="true"]')
    expect(first.text()).toContain('Lig ekle')
    expect(first.text()).toContain(t('ui.palette.action'))
    await w.find('input[role="combobox"]').trigger('keydown', { key: 'Enter' })
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/follows/new'))
  })

  it('"backup" opens Backups with the dialog; "help" opens Help; the empty list starts with the actions', async () => {
    await app('/', { 'GET /api/v1/backups': list([]) })
    key('k', { ctrlKey: true })
    await flush()
    expect(w.findAll('[role="option"]').slice(0, 5).map((o) => o.text())).toEqual(
      [t('ui.shell.addLeague'), t('ui.palette.backup'), t('ui.palette.export'), t('ui.nav.settings'), t('ui.menu.help')].map((x) => expect.stringContaining(x)),
    )
    await w.find('input[role="combobox"]').setValue('backup')
    await w.find('input[role="combobox"]').trigger('keydown', { key: 'Enter' })
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/backups'))
    await flush()
    await flush()
    expect(w.find('[role="dialog"]').text()).toContain(t('ui.backups.createTitle'))
    await w.find('[role="dialog"] button').trigger('keydown', { key: 'Escape' })
    key('Escape')
    await flush()
    w.unmount()
    await app('/')
    key('k', { ctrlKey: true })
    await flush()
    await w.find('input[role="combobox"]').setValue('canlı')
    expect(w.find('[role="option"]').text()).toContain(t('ui.menu.help'))
    await w.find('input[role="combobox"]').trigger('keydown', { key: 'Enter' })
    await flush()
    expect(w.find('[data-testid="help-panel"]').exists()).toBe(true)
  })

  it('when nothing stored matches it offers "Search SofaScore", and sends nothing until it is chosen', async () => {
    const f = await app('/', {
      ...SPORTS,
      'POST /api/v1/tournaments/search': list([{ id: 17, name: 'Premier League', slug: 'pl', sport: 'football', category: { id: 1, name: 'England' }, followed: false }]),
      'GET /api/v1/tournaments': page([]),
    })
    key('k', { ctrlKey: true })
    await flush()
    await w.find('input[role="combobox"]').setValue('premier')
    await new Promise((r) => setTimeout(r, 260))
    await flush()
    const options = w.findAll('[role="option"]')
    expect(options).toHaveLength(1)
    expect(options[0].text()).toContain(t('ui.palette.searchSofascore', { q: 'premier' }))
    expect(callsTo(f, 'POST /api/v1/tournaments/search')).toHaveLength(0)
    await w.find('input[role="combobox"]').trigger('keydown', { key: 'Enter' })
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/follows/new?q=premier'))
    await flush()
    await flush()
    // the one request the user chose; the only hit is chosen, so Next is the only click left
    expect(callsTo(f, 'POST /api/v1/tournaments/search')).toHaveLength(1)
    expect(JSON.parse(String(callsTo(f, 'POST /api/v1/tournaments/search')[0][1]!.body))).toEqual({ q: 'premier', sport: null })
    expect((w.find('[data-testid="editor-query"]').element as HTMLInputElement).value).toBe('premier')
    expect((w.find('[data-testid="editor-name"]').element as HTMLInputElement).value).toBe('Premier League')
    expect(w.find('[data-testid="editor-next"]').attributes('disabled')).toBeUndefined()
  })
})

describe('the follow editor', () => {
  const routesWith = (hits: unknown[]) => ({ ...SPORTS, 'GET /api/v1/status': { data: status() }, 'POST /api/v1/tournaments/search': list(hits) })
  const hit = (id: number, followed = false) => ({ id, name: 'Premier League', slug: 'pl', sport: 'football', category: { id: 1, name: 'England' }, followed })

  it('does not choose for the user when there are several hits, or when the one hit is already added', async () => {
    mockFetch(routesWith([hit(17), hit(203)]))
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/new'))
    await flush()
    expect(w.find('[data-testid="editor-query"]').attributes('placeholder')).toBe(t('ui.followEditor.searchPlaceholder'))
    await w.find('[data-testid="editor-query"]').setValue('premier')
    await w.find('[data-testid="editor-search"]').trigger('submit')
    await flush()
    expect(w.findAll('[data-testid="editor-hits"] input:checked')).toHaveLength(0)
    w.unmount()
    mockFetch(routesWith([hit(17, true)]))
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/new'))
    await flush()
    await w.find('[data-testid="editor-query"]').setValue('premier')
    await w.find('[data-testid="editor-search"]').trigger('submit')
    await flush()
    expect(w.findAll('[data-testid="editor-hits"] input:checked')).toHaveLength(0)
  })

  it('says "Last [2] seasons" and the review explains its words', async () => {
    mockFetch({ ...SPORTS, 'GET /api/v1/status': { data: status() }, 'GET /api/v1/tournaments/17/seasons': list([]) })
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/new?id=17', '/follows/new'))
    await flush()
    await w.find('[data-testid="editor-name"]').setValue('Premier League')
    await w.find('[data-testid="editor-next"]').trigger('click')
    await flush()
    const last = w.find('input[value="last"]').element.closest('label')!
    expect(last.textContent).toContain(t('ui.followEditor.lastN'))
    expect(last.textContent).toContain(t('ui.followEditor.lastNSuffix'))
    await w.find('[data-testid="editor-next"]').trigger('click')
    await flush()
    await w.find('[data-testid="editor-next"]').trigger('click')
    await flush()
    expect(w.text()).toContain(t('ui.followEditor.syncAfter'))
    expect(w.findAll('[data-testid="help-tip"]').map((b) => b.attributes('aria-label'))).toEqual([
      t('ui.help.tipLabel', { term: t('ui.help.term.live.name') }),
      t('ui.help.tipLabel', { term: t('ui.help.term.download.name') }),
    ])
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('team, player and single-match follows wait behind one flag', () => {
    expect(MORE_FOLLOW_KINDS).toBe(false)
  })

  it('an existing team follow says why it cannot be downloaded yet', async () => {
    mockFetch({ 'GET /api/v1/status': { data: status() } })
    setActivePinia(createPinia())
    router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: { template: '<div />' } }] })
    w = mount(FollowActions, { props: { follow: follow({ id: 'team:42', kind: 'team', entity_id: 42, name: 'Arsenal' }) }, global: { plugins: [i18n, router] } })
    const sync = w.find('[data-testid="follow-sync"]')
    expect(sync.attributes('disabled')).toBeDefined()
    expect(sync.attributes('title')).toBe(t('ui.follows.syncTournamentsOnly'))
  })
})

describe('plain words', () => {
  it('the menu and the Turkish words of the review', () => {
    setLocale('tr')
    expect(['follows', 'events', 'corrections', 'sinks', 'maintenance'].map((k) => t(`ui.nav.${k}`))).toEqual([
      'Ligler ve takipler',
      'Maçlar',
      'Sonradan değişen skorlar',
      'Bildirim hedefleri',
      'Veri bakımı',
    ])
    expect([t('ui.follows.syncNow'), t('ui.follows.syncAll'), t('ui.job.kind.sync'), t('ui.followEditor.syncAfter')]).toEqual([
      'Şimdi indir',
      'Tümünü güncelle',
      'İndirme',
      'Ekledikten sonra hemen indirmeye başla',
    ])
    expect([t('ui.status.origin.api'), t('ui.status.origin.legacy'), t('ui.job.face.library')]).toEqual(['Buradan eklendi', 'Eski lig listesinden', 'Python’dan'])
    expect([t('ui.exports.kind.legacy'), t('ui.job.kind.restore'), t('ui.token.label'), t('ui.maintenance.clear.word')]).toEqual([
      'Maç tablosu (CSV, maç başına bir satır)',
      'Yedek kontrolü',
      'Erişim anahtarı',
      'SİL',
    ])
    setLocale('en')
    expect(['follows', 'events', 'corrections', 'sinks', 'maintenance'].map((k) => t(`ui.nav.${k}`))).toEqual(['Leagues & follows', 'Matches', 'Score changes', 'Outputs', 'Data cleanup'])
  })

  it('no developer text in either language: no plan ids, no "later version"', async () => {
    const tr = (await import('@/locales/ui/tr')).default
    const en = (await import('@/locales/ui/en')).default
    for (const text of [JSON.stringify(tr), JSON.stringify(en)]) {
      expect(text).not.toMatch(/\b(P2\d|SC-\d|FE-\d|FX-\d)\b/)
      expect(text).not.toMatch(/sonraki (bir )?sürüm|later version/)
      expect(text).not.toMatch(/eşitle|belirte/i)
    }
  })

  it('a job names its league, not "Tournament #17"', async () => {
    const sync = job({ spec: { mode: 'full', league_id: 17, selections: [], export: false } })
    expect(jobTarget(sync)).toBe(t('ui.job.target.tournament', { id: 17 }))
    expect(jobTarget({ ...sync, progress: { league_name: 'Premier League' } })).toBe('Premier League')
    mockFetch({ ...TOURNAMENTS, 'GET /api/v1/jobs': page([sync]), 'GET /api/v1/status': { data: status() } })
    ;({ w } = await mountScreen(JobsScreen, '/jobs'))
    await flush()
    await flush()
    expect(w.find('tbody').text()).toContain('Premier League')
    expect(w.find('tbody').text()).not.toContain('#17')
  })

  it('counts carry their unit while match details are fetched', () => {
    expect(countsText({ phase: 'details', done: 0, total: 1 })).toBe(t('ui.job.countsMatches', { done: '0', total: '1' }))
    expect(countsText({ phase: 'seasons', done: 1, total: 2 })).toBe(t('ui.job.counts', { done: '1', total: '2' }))
    setLocale('tr')
    expect(countsText({ phase: 'details', done: 0, total: 1 })).toBe('0 / 1 maç')
  })

  it('the match page: status in words, SofaScore\'s values under Details, team names lead to their matches', async () => {
    mockFetch({
      ...SPORTS,
      ...TOURNAMENTS,
      'GET /api/v1/events/9100003': { data: event() },
      'GET /api/v1/events/9100003/slices': list([slice('statistics')]),
      'GET /api/v1/events/9100003/odds': list([]),
      'GET /api/v1/changes': page([]),
      'GET /api/v1/tournaments/17/seasons': list([]),
      'GET /api/v1/events/9100003/slices/event/raw': { event: {} },
      'GET /api/v1/status': { data: status() },
    })
    ;({ w } = await mountScreen(EventDetailScreen, '/events/9100003', '/events/:id'))
    await flush()
    await flush()
    expect(w.find('[data-testid="team-home"]').attributes('href')).toBe('/events?q=Chelsea')
    expect(w.find('[data-testid="team-away"]').attributes('href')).toBe('/events?q=Liverpool')
    expect(w.find('[data-testid="event-score"]').text()).not.toContain('Ended')
    const status_ = w.find('[data-fact="status"]')
    expect(status_.find('[data-status="event:completed"]').text()).toBe(t('ui.status.event.completed'))
    expect(status_.find('[data-testid="status-details"] summary').text()).toBe(t('ui.eventDetail.statusDetails'))
    expect(status_.find('[data-testid="status-details"]').text()).toContain('finished / 100 · Ended')
    expect(w.find('[data-fact="settlement"]').text()).toContain(t('ui.eventDetail.settled.final'))
    expect(w.find('[data-fact="tier"]').exists()).toBe(false)
    expect(w.find('[data-fact="raw"]').text()).toContain(t('ui.eventDetail.fact.raw'))
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('SofaScore\'s English statistics are upper-cased as English, not with the Turkish İ', async () => {
    setLocale('tr')
    const payload = { statistics: [{ period: 'ALL', groups: [{ groupName: 'Match overview', statisticsItems: [{ name: 'Ball possession', home: '61%', away: '39%' }] }] }] }
    mockFetch({
      ...SPORTS,
      ...TOURNAMENTS,
      'GET /api/v1/events/9100003': { data: event() },
      'GET /api/v1/events/9100003/slices': list([slice('statistics')]),
      'GET /api/v1/events/9100003/odds': list([]),
      'GET /api/v1/changes': page([]),
      'GET /api/v1/tournaments/17/seasons': list([]),
      'GET /api/v1/events/9100003/slices/statistics': { data: slice('statistics', { payload }) },
      'GET /api/v1/status': { data: status() },
    })
    ;({ w } = await mountScreen(EventDetailScreen, '/events/9100003?tab=statistics', '/events/:id'))
    await flush()
    await flush()
    const caption = w.find('[data-testid="view-statistics"] h3')
    expect(caption.text()).toBe('Match overview')
    expect(caption.attributes('lang')).toBe('en')
  })
})

describe('Events', () => {
  it('"All statuses" brings every status back after a chip', async () => {
    const f = mockFetch({ ...SPORTS, ...TOURNAMENTS, 'GET /api/v1/events': page([event()]), 'GET /api/v1/status': { data: status() } })
    ;({ w, router } = await mountScreen(EventsScreen, '/events?status=completed', '/events'))
    await flush()
    expect(w.find('[data-class="all"]').attributes('aria-pressed')).toBe('false')
    expect(w.find('#events-team').attributes('placeholder')).toBe(t('ui.events.teamPlaceholder'))
    await w.find('[data-class="all"]').trigger('click')
    await flush()
    expect(router.currentRoute.value.query.status).toBeUndefined()
    expect(new URL(String(callsTo(f, 'GET /api/v1/events').at(-1)![0]), 'http://x').searchParams.getAll('status')).toEqual([])
  })
})

describe('help', () => {
  it('the ⋯ menu and the rail open Help: the glossary, the steps, the README; axe-clean', async () => {
    await app('/jobs')
    await w.find(`button[aria-label="${t('ui.menu.label')}"]`).trigger('click')
    await w.find('[data-key="help"]').trigger('click')
    await flush()
    const panel = w.find('[data-testid="help-panel"]')
    expect(panel.findAll('dt').map((x) => x.attributes('data-term'))).toEqual([...HELP_TERMS])
    expect(panel.find('[data-term="live"] + dd').text()).toContain('ssc watch')
    expect(panel.find('[data-term="live"] + dd').text()).toContain(t('ui.help.term.live.text'))
    expect(panel.find('[data-testid="getting-started"]').exists()).toBe(true)
    const readme = panel.find('[data-testid="help-readme"]')
    expect(readme.attributes('href')).toBe(readmeUrl('en'))
    expect(readme.attributes('target')).toBe('_blank')
    expect(readme.attributes('rel')).toContain('noopener')
    expect(await axeViolations(document.body)).toEqual([])
    await w.find(`[role="dialog"] .u-panel-head button[aria-label="${t('ui.common.close')}"]`).trigger('click')
    await flush()
    expect(w.find('[data-testid="help-panel"]').exists()).toBe(false)
    await w.find('[data-testid="rail-help"]').trigger('click')
    await flush()
    expect(w.find('[data-testid="help-panel"]').exists()).toBe(true)
    expect(readmeUrl('tr')).toContain('README.tr.md')
  })

  it('an (i) explains its word on click and hides on Esc', async () => {
    w = mount(HelpTip, { props: { term: 'outputs' }, global: { plugins: [i18n] }, attachTo: document.body })
    const button = w.find('button')
    expect(button.attributes('aria-label')).toBe(t('ui.help.tipLabel', { term: t('ui.help.term.outputs.name') }))
    expect(button.attributes('aria-expanded')).toBe('false')
    await button.trigger('click')
    expect(button.attributes('aria-expanded')).toBe('true')
    expect(w.find('[role="status"]').text()).toContain(t('ui.help.term.outputs.text'))
    expect(await axeViolations(w.element)).toEqual([])
    await w.find('.u-tip').trigger('keydown', { key: 'Escape' })
    expect(w.find('[role="status"]').text()).toBe('')
  })

  it('page titles carry the (i) of their word', async () => {
    w = mount(PageHeader, { props: { title: 'Outputs', help: 'outputs' }, global: { plugins: [i18n] } })
    expect(w.find('h1').text()).toBe('Outputs')
    expect(w.find('[data-term="outputs"] button').exists()).toBe(true)
    expect(w.find('.u-page-title').exists()).toBe(true)
  })

  it('the getting-started card can be hidden, and Help brings it back', async () => {
    await app('/')
    const card = w.find('[data-testid="getting-started"]')
    expect(card.findAll('li')).toHaveLength(3)
    expect(card.find('a[href="/follows/new"]').text()).toBe(t('ui.overview.start.step1'))
    await w.find('[data-testid="start-dismiss"]').trigger('click')
    await flush()
    expect(w.find('main [data-testid="getting-started"]').exists()).toBe(false)
    expect(localStorage.getItem('ssui.startCard')).toBe('1')
    await w.find('[data-testid="rail-help"]').trigger('click')
    await flush()
    await w.find('[data-testid="help-show-start"]').trigger('click')
    await flush()
    expect(w.find('main [data-testid="getting-started"]').exists()).toBe(true)
  })
})

describe('when a download ends', () => {
  const synced = (id: string, over = {}) =>
    job({ id, state: 'succeeded', spec: { mode: 'full', league_id: 17, selections: [], export: false }, result: { details_done: 4, details_total: 4, failed_count: 0, failed: [] }, ...over })

  it('a toast says what was downloaded and leads to its matches; once', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let answer = job({ id: 'W1', state: 'running', finished_at: null, spec: { league_id: 17 } })
    const f = mockFetch({ ...TOURNAMENTS, 'GET /api/v1/jobs/W1': () => ({ data: answer }), 'GET /api/v1/status': { data: status() } })
    setActivePinia(createPinia())
    const stop = watchJobs()
    const ended = vi.fn()
    const off = onJobEnded(ended)
    watchJob({ id: 'W1' })
    await vi.advanceTimersByTimeAsync(JOB_WATCH_MS + 50)
    expect(callsTo(f, 'GET /api/v1/jobs/W1')).toHaveLength(1)
    expect(uiToasts.value).toHaveLength(0)
    answer = synced('W1')
    await vi.advanceTimersByTimeAsync(JOB_WATCH_MS + 50)
    await flush()
    expect(ended).toHaveBeenCalledTimes(1)
    expect(uiToasts.value.map((x) => x.text)).toEqual([t('ui.jobDone.synced', { name: 'Premier League', n: 4 })])
    expect(uiToasts.value[0].link).toEqual({ to: { path: '/events', query: { tournament: '17' } }, label: t('ui.jobDone.showMatches') })
    // told once, even when it is named again
    watchJob({ id: 'W1' })
    await vi.advanceTimersByTimeAsync(JOB_WATCH_MS * 2)
    expect(callsTo(f, 'GET /api/v1/jobs/W1')).toHaveLength(2)
    off()
    stop()
  })

  it('the job of /status is followed too; partial, failed and stopped downloads are told as such', async () => {
    setLocale('tr')
    tellEnd(synced('T1', { spec: { league_id: 17, selections: [] }, progress: { league_name: 'Premier League' } }))
    expect(uiToasts.value.at(-1)!.text).toBe('Premier League indirildi: 4 maç')
    tellEnd(synced('T2', { spec: {}, result: { details_done: 12 } }))
    expect(uiToasts.value.at(-1)!.text).toBe('İndirme bitti: 12 maç')
    // a download that found nothing new says so instead of "0 matches"
    tellEnd(synced('T6', { spec: { league_id: 17 }, progress: { league_name: 'Premier League' }, result: { details_done: 0 } }))
    expect(uiToasts.value.at(-1)!.text).toBe('Premier League güncel: yeni maç yok.')
    tellEnd(synced('T3', { state: 'partial', progress: { league_name: 'Premier League' }, result: { details_done: 3, failed_count: 1 } }))
    expect(uiToasts.value.at(-1)).toMatchObject({ kind: 'info', text: 'Premier League kısmen indirildi: 3 maç, 1 başarısız', link: { to: '/jobs/T3' } })
    tellEnd(synced('T4', { state: 'failed', progress: { league_name: 'Premier League' } }))
    expect(uiToasts.value.at(-1)).toMatchObject({ kind: 'error', text: 'Premier League indirilemedi.' })
    const before = uiToasts.value.length
    tellEnd(synced('T5', { state: 'cancelled' }))
    expect(uiToasts.value).toHaveLength(before)
    clearToasts()
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const f = mockFetch({ ...TOURNAMENTS, 'GET /api/v1/jobs/S9': { data: synced('S9') }, 'GET /api/v1/status': { data: status() } })
    setActivePinia(createPinia())
    const store = useStatusStore()
    const stop = watchJobs()
    store.status = status({ active_job: job({ id: 'S9', state: 'running' }) })
    await flush()
    store.status = status({ active_job: null })
    await vi.advanceTimersByTimeAsync(JOB_WATCH_MS + 50)
    await flush()
    expect(callsTo(f, 'GET /api/v1/jobs/S9')).toHaveLength(1)
    expect(uiToasts.value.map((x) => x.text)).toEqual(['Premier League indirildi: 4 maç'])
    stop()
  })

  it('the league\'s page reads its last download and counts again', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const f = mockFetch({
      ...SPORTS,
      ...TOURNAMENTS,
      'GET /api/v1/follows/tournament:17': { data: follow() },
      'GET /api/v1/tournaments/17': { data: { id: 17, name: 'Premier League', sport: 'football', category: { id: 1, name: 'England' } } },
      'GET /api/v1/tournaments/17/seasons': list([]),
      'GET /api/v1/jobs': page([]),
      'GET /api/v1/jobs/D1': { data: synced('D1') },
      'GET /api/v1/status': { data: status() },
    })
    ;({ w } = await mountScreen(FollowDetailScreen, '/follows/tournament/17', '/follows/:kind/:id'))
    await flush()
    const stop = watchJobs()
    const jobsBefore = callsTo(f, 'GET /api/v1/jobs').length
    const statusBefore = callsTo(f, 'GET /api/v1/status').length
    watchJob({ id: 'D1' })
    await vi.advanceTimersByTimeAsync(JOB_WATCH_MS + 50)
    await flush()
    expect(callsTo(f, 'GET /api/v1/jobs').length).toBe(jobsBefore + 1)
    expect(callsTo(f, 'GET /api/v1/status').length).toBe(statusBefore + 1)
    stop()
  })
})

describe('the connection is not "Connected" before anything was answered', () => {
  const bridge = (over = {}) => ({ ...status().bridge, ...over })

  it('connectionState: a server without `connection` (before FX-19) is read from the bridge times', () => {
    expect(connectionState({ bridge: bridge({ last_success_at: null }) })).toBe('untried')
    expect(connectionState({ bridge: bridge({ last_success_at: '2026-10-02T10:00:00Z', last_failure_at: '2026-10-02T10:05:00Z' }) })).toBe('failing')
    expect(connectionState({ bridge: bridge({ last_success_at: '2026-10-02T10:00:00Z' }) })).toBe('ok')
    expect(connectionState({ bridge: bridge({ state: 'blocked', last_success_at: null }) })).toBe('blocked')
    expect(connectionState(null)).toBeNull()
  })

  it('connectionState: the server keeps the state and the last check (FX-19), the same for every browser', () => {
    const at = (state: 'never_tried' | 'ok' | 'failed', over = {}) => ({ bridge: bridge(), connection: { state, last_success_at: null, last_failure_at: null, last_check: null, ...over } })
    expect(connectionState(at('never_tried'))).toBe('untried')
    expect(connectionState(at('failed', { last_success_at: '2026-10-02T10:00:00Z', last_failure_at: '2026-10-02T10:05:00Z' }))).toBe('failing')
    expect(connectionState(at('ok', { last_success_at: '2026-10-02T10:00:00Z' }))).toBe('ok')
    const failedCheck = { ok: false, at: '2026-10-02T10:01:00Z', reason: 'upstream' }
    expect(connectionState(at('failed', { last_success_at: '2026-10-02T10:00:00Z', last_check: failedCheck }))).toBe('checkFailed')
    expect(connectionState(at('ok', { last_success_at: '2026-10-02T10:02:00Z', last_check: failedCheck }))).toBe('ok')
    expect(connectionState(at('failed', { last_check: failedCheck }))).toBe('checkFailed')
    expect(connectionState({ ...at('ok'), bridge: bridge({ state: 'blocked' }) })).toBe('blocked')
  })

  it('Health and the pill say "Not tried yet" before a success, and turn amber after a failed check', async () => {
    const never = { state: 'never_tried', last_success_at: null, last_failure_at: null, last_check: null }
    const failed = { state: 'failed', last_success_at: null, last_failure_at: '2026-10-02T11:00:00Z', last_failure_reason: 'network', last_check: { at: '2026-10-02T11:00:00Z', ok: false, reason: 'upstream' } }
    // the server keeps the check: every later `/status` has it, in this browser and any other
    let checked = false
    const f = await app('/system/health', {
      'GET /api/v1/status': () => ({ data: status({ bridge: bridge({ last_success_at: null }), connection: (checked ? failed : never) as never }) }),
      'POST /api/v1/status/check': () => {
        checked = true
        return { data: { ok: false, reason: 'upstream', message: 'x', events_count: null, checked_at_utc: '2026-10-02T11:00:00Z', bridge: bridge({ last_success_at: null }), connection: failed } }
      },
    })
    const card = w.find('[data-testid="health-connection"]')
    expect(card.find('[data-status="connection:untried"]').text()).toBe(t('ui.status.connection.untried'))
    expect(card.find('[data-testid="connection-note"]').text()).toBe(t('ui.health.untried'))
    expect(w.find('[data-testid="health-pill"]').attributes('data-level')).toBe('untried')
    expect(w.find('[data-testid="health-pill"]').text()).toBe(t('ui.status.health.untried'))
    await card.find('[data-testid="connection-check"] button').trigger('click')
    await flush()
    await flush()
    expect(callsTo(f, 'POST /api/v1/status/check')).toHaveLength(1)
    expect(card.find('[data-status="connection:checkFailed"]').exists()).toBe(true)
    expect(w.find('[data-testid="health-pill"]').attributes('data-level')).toBe('attention')
    await router.push('/')
    await flush()
    await flush()
    expect(w.find('[data-testid="services"] [data-status="connection:checkFailed"]').exists()).toBe(true)
  })

  it('Overview says "Connected" only after an answer', async () => {
    mockFetch({
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/jobs': page([]),
      'GET /api/v1/follows': list([]),
      'GET /api/v1/sinks': { data: [] },
      'GET /api/v1/changes': page([]),
      ...TOURNAMENTS,
    })
    ;({ w } = await mountScreen(OverviewScreen, '/'))
    await flush()
    expect(w.find('[data-testid="services"] [data-status="connection:ok"]').exists()).toBe(true)
    w.unmount()
    mockFetch({ 'GET /api/v1/status': { data: status({ bridge: bridge({ last_success_at: null }) }) } })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    expect(w.find('[data-status="connection:ok"]').exists()).toBe(false)
  })
})

describe('Backups', () => {
  it('?new=1 opens the backup dialog at once', async () => {
    mockFetch({ 'GET /api/v1/backups': list([]), 'GET /api/v1/status': { data: status() } })
    let r
    ;({ w, router: r } = await mountScreen(BackupsScreen, '/backups?new=1', '/backups'))
    await flush()
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain(t('ui.backups.createTitle'))
    expect(r.currentRoute.value.query.new).toBeUndefined()
  })
})
