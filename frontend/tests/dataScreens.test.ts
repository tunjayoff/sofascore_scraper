import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import FollowsScreen from '@/screens/follows/FollowsScreen.vue'
import FollowEditorScreen from '@/screens/follows/FollowEditorScreen.vue'
import FollowDetailScreen from '@/screens/follows/FollowDetailScreen.vue'
import EventsScreen from '@/screens/events/EventsScreen.vue'
import EventDetailScreen from '@/screens/events/EventDetailScreen.vue'
import RawScreen from '@/screens/events/RawScreen.vue'
import CorrectionsScreen from '@/screens/CorrectionsScreen.vue'
import { authNeeded } from '@/lib/auth'
import { resetSports } from '@/app/sports'
import { resetNames } from '@/screens/events/eventText'
import { clearToasts, uiToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { callsTo, deferred, flush, json, mockFetch } from './helpers'
import { axeViolations, change, event, follow, job, mountScreen, page, slice, sport, sportWithOdds, status, summary, v1Error } from './v1'

/**
 * The Data screens (05-web-ui.md 6.2 to 6.7) in their states with a fake API: loading, empty, an error by
 * its code, the 401 token prompt and the 409 of a running job. No test reaches a server or SofaScore.
 */
const t = i18n.global.t
let w: VueWrapper

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
  authNeeded.value = false
  resetSports()
  resetNames()
})
afterEach(() => w?.unmount())

const body = (f: ReturnType<typeof mockFetch>, key: string, i = 0) => JSON.parse(String(callsTo(f, key)[i][1]!.body))
const params = (f: ReturnType<typeof mockFetch>, key: string, i = -1) => new URL(String(callsTo(f, key).at(i)![0]), 'http://x').searchParams
const list = <T>(data: T[]) => ({ data, page: { limit: 0, next_cursor: null } })
const SPORTS = { 'GET /api/v1/sports': list([sport('football'), sport('basketball')]) }
const TOURNAMENTS = { 'GET /api/v1/tournaments': page([{ id: 17, sport: 'football', category_id: 1, name: 'Premier League', slug: 'pl', followed: true }]) }

// ---------------------------------------------------------------------------------------------------

describe('Follows', () => {
  const routes = (over: Record<string, unknown> = {}) => ({
    ...SPORTS,
    'GET /api/v1/follows': list([
      follow(),
      follow({ id: 'tournament:8', entity_id: 8, name: 'LaLiga', origin: 'config', writable: [] }),
      follow({ id: 'team:2672', kind: 'team', entity_id: 2672, name: 'Team 2672', origin: 'legacy', writable: ['sport'], slices: { include: ['statistics', 'odds_all'] } }),
    ]),
    'GET /api/v1/jobs': page([job({ id: 'S1', kind: 'sync', spec: { league_id: 17 }, finished_at: '2026-10-02T09:04:10Z' })]),
    'GET /api/v1/status': { data: status({ summary: summary({ tournaments: [{ tournament_id: 17, name: 'Premier League', followed: true, matches: 380, details: 371, events: 380, finished: 380, seasons: 2, seasons_with_events: 2, coverage: 97.6 }] }) }) },
    ...over,
  })

  it('lists every follow with sport, seasons, data, coverage, last sync and origin', async () => {
    mockFetch(routes())
    ;({ w } = await mountScreen(FollowsScreen, '/follows'))
    const { useStatusStore } = await import('@/app/statusStore')
    await useStatusStore().refresh()
    await flush()
    const rows = w.findAll('tbody tr')
    expect(rows).toHaveLength(3)
    expect(rows[0].text()).toContain('Premier League')
    expect(rows[0].text()).toContain(t('sport.football'))
    expect(rows[0].text()).toContain(t('ui.follows.seasons.current'))
    expect(rows[0].text()).toContain(t('ui.follows.data.defaults'))
    expect(rows[0].text()).toContain('98%')
    expect(rows[0].find('a').attributes('href')).toBe('/follows/tournament/17')
    expect(rows[1].find('[data-status="origin:config"]').exists()).toBe(true)
    expect(rows[2].text()).toContain(t('ui.follows.data.custom'))
    expect(rows[2].text()).toContain(t('ui.follows.data.odds'))
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('filters name, kind and origin on the server; a config follow is locked in its menu', async () => {
    const f = mockFetch(routes())
    let router
    ;({ w, router } = await mountScreen(FollowsScreen, '/follows?kind=tournament&q=liga', '/follows'))
    await flush()
    expect([params(f, 'GET /api/v1/follows').get('kind'), params(f, 'GET /api/v1/follows').get('q')]).toEqual(['tournament', 'liga'])
    await w.find('[data-filter="origin"]').setValue('config')
    await flush()
    expect(router.currentRoute.value.query.origin).toBe('config')
    expect(params(f, 'GET /api/v1/follows').get('origin')).toBe('config')
    await w.findAll('tbody tr')[1].find('button[aria-haspopup="menu"]').trigger('click')
    const items = w.findAll('[role="menuitem"]')
    expect(items.every((i) => i.attributes('aria-disabled') === 'true' || i.attributes('data-key') === 'sync')).toBe(true)
    expect(w.find('[role="menu"]').text()).toContain(t('ui.follows.lock.config'))
  })

  it('removes a follow after the confirmation; a 409 job_running stays in the dialog', async () => {
    let refuse = true
    const f = mockFetch(routes({ 'DELETE /api/v1/follows/tournament:17': () => (refuse ? v1Error(409, 'job_running', { holder: { pid: 77 } }) : { data: follow() }) }))
    ;({ w } = await mountScreen(FollowsScreen, '/follows'))
    await flush()
    await w.findAll('tbody tr')[0].find('button[aria-haspopup="menu"]').trigger('click')
    await w.find('[data-key="remove"]').trigger('click')
    expect(w.find('[role="alertdialog"]').text()).toContain(t('ui.follows.removeText'))
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(w.find('[role="alertdialog"] [role="alert"]').text()).toContain(t('ui.error.job_running'))
    refuse = false
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(callsTo(f, 'DELETE /api/v1/follows/tournament:17')).toHaveLength(2)
    expect(uiToasts.value.at(-1)?.text).toBe(t('ui.follows.removedToast', { name: 'Premier League' }))
  })

  it('sync now starts a sync of that tournament', async () => {
    const f = mockFetch(routes({ 'POST /api/v1/jobs': { data: job({ id: 'S9', state: 'running' }) } }))
    ;({ w } = await mountScreen(FollowsScreen, '/follows'))
    await flush()
    await w.findAll('tbody tr')[0].find('button[aria-haspopup="menu"]').trigger('click')
    await w.find('[data-key="sync"]').trigger('click')
    expect(w.find('[role="alertdialog"]').text()).toContain(t('ui.jobs.start.sendsRequests'))
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs')).toEqual({ kind: 'sync', spec: { league_id: 17 } })
  })

  it('loading, empty, error by code, 401', async () => {
    const d = deferred<Response>()
    mockFetch(routes({ 'GET /api/v1/follows': () => d.promise }))
    ;({ w } = await mountScreen(FollowsScreen, '/follows'))
    await flush()
    expect(w.find('[data-testid="skeleton"]').exists()).toBe(true)
    d.resolve(json(list([])))
    await flush()
    expect(w.find('[data-testid="empty"]').text()).toContain(t('ui.follows.empty'))
    expect(w.find('[data-testid="empty"] a[href="/follows/new"]').exists()).toBe(true)
    w.unmount()
    mockFetch(routes({ 'GET /api/v1/follows': () => v1Error(507, 'storage_error') }))
    ;({ w } = await mountScreen(FollowsScreen, '/follows'))
    await flush()
    expect(w.find('[data-testid="error-state"]').attributes('data-code')).toBe('storage_error')
    w.unmount()
    mockFetch(routes({ 'GET /api/v1/follows': () => v1Error(401, 'unauthorized') }))
    ;({ w } = await mountScreen(FollowsScreen, '/follows'))
    await flush()
    expect(authNeeded.value).toBe(true)
  })
})

// ---------------------------------------------------------------------------------------------------

describe('Follow editor', () => {
  const routes = (over: Record<string, unknown> = {}) => ({
    ...SPORTS,
    'GET /api/v1/sports/football': { data: sport('football') },
    'GET /api/v1/settings': { data: { config_file: null, overrides_file: null, settings: [{ key: 'defaults.slices', value: ['core'], source: 'default', source_name: '', locked: false, writable: false, secret: false }, { key: 'client.odds_provider', value: 1, source: 'default', source_name: '', locked: false, writable: true, secret: false }] } },
    'GET /api/v1/status': { data: status() },
    ...over,
  })

  it('follows a tournament in four steps: search, seasons, data (one line, odds off), review; then syncs it', async () => {
    const f = mockFetch(
      routes({
        'GET /api/v1/sports/football': { data: sportWithOdds() },
        'POST /api/v1/tournaments/search': list([
          { id: 17, name: 'Premier League', slug: 'pl', sport: 'football', category: { id: 1, name: 'England', country_code: 'EN' }, followed: false },
          { id: 203, name: 'Premier League', slug: 'rpl', sport: 'football', category: { id: 2, name: 'Russia' }, followed: true },
        ]),
        'GET /api/v1/tournaments/17/seasons': list([{ id: 61627, tournament_id: 17, name: 'PL 24/25', year: '24/25' }]),
        'POST /api/v1/follows': { data: follow() },
        'POST /api/v1/jobs': { data: job({ id: 'S1', state: 'running' }) },
      }),
    )
    let router
    ;({ w, router } = await mountScreen(FollowEditorScreen, '/follows/new'))
    await flush()
    expect(w.find('[data-testid="editor-next"]').attributes('disabled')).toBeDefined()
    await w.find('[data-testid="editor-sport"]').setValue('football')
    await w.find('[data-testid="editor-query"]').setValue('premier league')
    expect(callsTo(f, 'POST /api/v1/tournaments/search')).toHaveLength(0)
    expect(w.text()).toContain(t('ui.followEditor.searchNote'))
    await w.find('[data-testid="editor-search"]').trigger('submit')
    await flush()
    expect(body(f, 'POST /api/v1/tournaments/search')).toEqual({ q: 'premier league', sport: 'football' })
    const hits = w.find('[data-testid="editor-hits"]')
    expect(hits.text()).toContain(t('ui.followEditor.alreadyFollowed'))
    await hits.find('input[value="17"]').setValue(true)
    expect((w.find('[data-testid="editor-name"]').element as HTMLInputElement).value).toBe('Premier League')
    expect(await axeViolations(w.element)).toEqual([])
    await w.find('[data-testid="editor-next"]').trigger('click')
    // 2: seasons
    await w.find('input[value="last"]').setValue(true)
    await flush()
    await w.find('[data-testid="editor-next"]').trigger('click')
    await flush()
    // 3: data, in one line; the list under "Details" (FX-14a)
    const picker = w.find('[data-testid="slice-picker"]')
    expect(picker.text()).toContain(t('ui.slicePicker.soon'))
    expect(picker.find('[data-testid="slice-summary"]').text()).toContain(t('ui.slice.statistics'))
    expect(picker.find('[data-testid="slice-summary"]').text()).toContain(t('ui.slicePicker.oddsOff'))
    expect(picker.find('[data-testid="slice-cost"]').text()).toContain(t('ui.slicePicker.cost', { n: 7 }))
    expect(picker.find('[data-testid="odds-group"]').exists()).toBe(false)
    expect(picker.text()).not.toMatch(/\bP2\d\b|\bcore\b/)
    await picker.find('[data-testid="slice-details"]').trigger('click')
    expect(picker.find('[data-testid="slice-details"]').attributes('aria-expanded')).toBe('true')
    // odds once, in their own group, off; every name in words
    expect(picker.findAll('[data-slice="odds_featured"]')).toHaveLength(1)
    expect(picker.find('[data-testid="odds-group"]').findAll('input:checked')).toHaveLength(0)
    expect(picker.find('[data-testid="odds-group"]').text()).toContain(t('ui.slice.winning_odds'))
    expect(picker.find('[data-testid="odds-group"]').text()).toContain(t('ui.slicePicker.oddsHistory'))
    expect(picker.find('[data-slice="standings"]').text()).toBe(t('ui.slice.standings'))
    expect(picker.text()).not.toContain('winning_odds')
    expect(await axeViolations(w.element)).toEqual([])
    await w.find('[data-testid="editor-next"]').trigger('click')
    // 4: review
    expect(w.find('[data-testid="editor-review"]').text()).toContain(t('ui.follows.seasons.last', { n: 2 }))
    await w.find('[data-testid="editor-save"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/follows')).toEqual({ kind: 'tournament', entity_id: 17, name: 'Premier League', sport: 'football', seasons: 'last:2', live: false })
    expect(body(f, 'POST /api/v1/jobs')).toEqual({ kind: 'sync', spec: { league_id: 17 } })
    expect(router.currentRoute.value.path).toBe('/follows/tournament/17')
  })

  it('a refused search says why and leads to Health; no hits says so', async () => {
    let answer: () => unknown = () => v1Error(503, 'blocked')
    mockFetch(routes({ 'POST /api/v1/tournaments/search': () => answer() }))
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/new'))
    await flush()
    await w.find('[data-testid="editor-query"]').setValue('x')
    await w.find('[data-testid="editor-search"]').trigger('submit')
    await flush()
    const err = w.find('[data-testid="form-error"]')
    expect(err.attributes('data-code')).toBe('blocked')
    expect(err.find('a[href="/system/health"]').exists()).toBe(true)
    answer = () => list([])
    await w.find('[data-testid="editor-search"]').trigger('submit')
    await flush()
    expect(w.find('[data-testid="editor-no-hits"]').text()).toBe(t('ui.followEditor.noHits'))
  })

  it('team, player and single match are "coming soon"; a league by its number; 409 follow_exists links to the follow', async () => {
    mockFetch(routes({ 'POST /api/v1/follows': () => v1Error(409, 'follow_exists') }))
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/new?kind=team&id=17', '/follows/new'))
    await flush()
    // FX-14a: the kinds that cannot download yet are shown, disabled, with the reason; ?kind=team is ignored
    for (const k of ['team', 'player', 'event']) {
      expect(w.find(`[data-kind="${k}"] input`).attributes('disabled')).toBeDefined()
      expect(w.find(`[data-kind="${k}"]`).text()).toContain(t('ui.follows.soon'))
    }
    expect(w.find('[data-testid="editor-soon"]').text()).toBe(t('ui.follows.soonReason'))
    expect(w.find('[data-testid="editor-query"]').exists()).toBe(true)
    expect(w.text()).toContain(t('ui.followEditor.orId'))
    expect(w.text()).toContain(t('ui.followEditor.idHint'))
    expect((w.find('[data-testid="editor-id"]').element as HTMLInputElement).value).toBe('17')
    await w.find('[data-testid="editor-name"]').setValue('Premier League')
    await w.find('[data-testid="editor-sport"]').setValue('football')
    for (let i = 0; i < 3; i++) {
      await w.find('[data-testid="editor-next"]').trigger('click')
      await flush()
    }
    expect(w.find('[data-testid="editor-sync-after"]').exists()).toBe(true)
    await w.find('[data-testid="editor-save"]').trigger('click')
    await flush()
    expect(w.find('[data-testid="form-error"]').attributes('data-code')).toBe('follow_exists')
    expect(w.find('a[href="/follows/tournament/17"]').exists()).toBe(true)
  })

  it('a change of a follow from leagues.txt sends only the sport; the other fields are locked', async () => {
    const f = mockFetch(
      routes({
        'GET /api/v1/follows/tournament:17': { data: follow({ origin: 'legacy', writable: ['sport'], seasons: 'all' }) },
        'GET /api/v1/tournaments/17/seasons': list([]),
        'GET /api/v1/sports/basketball': { data: sport('basketball') },
        'PATCH /api/v1/follows/tournament:17': { data: follow({ origin: 'legacy', sport: 'basketball' }) },
      }),
    )
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/tournament/17/edit', '/follows/:kind/:id/edit'))
    await flush()
    expect(w.find('[data-testid="edit-name"]').attributes('disabled')).toBeDefined()
    expect(w.text()).toContain(t('ui.follows.lock.legacy'))
    expect(w.find('[data-testid="edit-save"]').attributes('disabled')).toBeDefined()
    await w.find('[data-testid="edit-sport"]').setValue('basketball')
    await w.find('[data-testid="editor-form"]').trigger('submit')
    await flush()
    expect(body(f, 'PATCH /api/v1/follows/tournament:17')).toEqual({ sport: 'basketball' })
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('a follow from the config file cannot be saved here', async () => {
    mockFetch(routes({ 'GET /api/v1/follows/tournament:17': { data: follow({ origin: 'config', writable: [] }) }, 'GET /api/v1/tournaments/17/seasons': list([]) }))
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/tournament/17/edit', '/follows/:kind/:id/edit'))
    await flush()
    expect(w.find('[data-testid="editor-locked"]').text()).toContain(t('ui.follows.lock.config'))
    expect(w.find('[data-testid="edit-save"]').attributes('disabled')).toBeDefined()
  })
})

// ---------------------------------------------------------------------------------------------------

describe('Follow detail', () => {
  const routes = (over: Record<string, unknown> = {}) => ({
    ...SPORTS,
    'GET /api/v1/follows/tournament:17': { data: follow({ seasons: [61627] }) },
    'GET /api/v1/tournaments/17': { data: { id: 17, sport: 'football', category_id: 1, name: 'Premier League', slug: 'pl', category: { id: 1, sport: 'football', name: 'England', slug: 'england', country_code: 'EN' }, followed: true } },
    'GET /api/v1/tournaments/17/seasons': list([
      { id: 61627, tournament_id: 17, name: 'PL 24/25', year: '24/25' },
      { id: 52186, tournament_id: 17, name: 'PL 23/24', year: '23/24' },
    ]),
    'GET /api/v1/jobs': page([job({ id: 'S1', kind: 'sync', spec: { league_id: 17 } }), job({ id: 'S2', kind: 'sync', spec: { league_id: 99 } })]),
    'GET /api/v1/status': { data: status() },
    ...over,
  })

  it('shows the facts, the seasons with the ones not followed, and syncs one season', async () => {
    const f = mockFetch(routes({ 'POST /api/v1/jobs': { data: job({ id: 'S3', state: 'running' }) } }))
    ;({ w } = await mountScreen(FollowDetailScreen, '/follows/tournament/17', '/follows/:kind/:id'))
    await flush()
    expect(w.find('h1').text()).toBe('Premier League')
    expect(w.text()).toContain('England')
    const seasons = w.findAll('[data-testid="follow-seasons"] li')
    expect(seasons).toHaveLength(2)
    expect(seasons[1].text()).toContain(t('ui.followDetail.notFollowed'))
    expect(w.find('[data-testid="follow-facts"]').text()).toContain(t('ui.follows.seasons.chosen', { n: 1 }))
    expect(await axeViolations(w.element)).toEqual([])
    await seasons[0].findAll('button').find((b) => b.text() === t('ui.followDetail.syncSeason'))!.trigger('click')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs')).toEqual({ kind: 'sync', spec: { selections: [{ league_id: 17, season_ids: [61627] }] } })
    await w.find('[data-testid="fetch-missing-league"]').trigger('click')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs', 1)).toEqual({ kind: 'fetch', spec: { league_id: 17 } })
  })

  it('the jobs tab lists only syncs that included this follow; the data tab shows the picker', async () => {
    mockFetch(routes({ 'GET /api/v1/sports/football': { data: sport('football') }, 'GET /api/v1/settings': { data: { settings: [] } } }))
    let router
    ;({ w, router } = await mountScreen(FollowDetailScreen, '/follows/tournament/17?tab=jobs', '/follows/:kind/:id'))
    await flush()
    expect(w.findAll('[data-testid="follow-jobs"] li')).toHaveLength(1)
    await router.push('/follows/tournament/17?tab=data')
    await flush()
    expect(w.find('[data-testid="slice-picker"]').exists()).toBe(true)
  })

  it('not found, an error, 401', async () => {
    mockFetch(routes({ 'GET /api/v1/follows/tournament:17': () => v1Error(404, 'not_found') }))
    ;({ w } = await mountScreen(FollowDetailScreen, '/follows/tournament/17', '/follows/:kind/:id'))
    await flush()
    expect(w.find('[data-testid="follow-not-found"]').text()).toContain(t('ui.followDetail.notFound'))
    w.unmount()
    mockFetch(routes({ 'GET /api/v1/follows/tournament:17': () => v1Error(500, 'internal') }))
    ;({ w } = await mountScreen(FollowDetailScreen, '/follows/tournament/17', '/follows/:kind/:id'))
    await flush()
    expect(w.find('[data-testid="error-state"]').attributes('data-code')).toBe('internal')
    w.unmount()
    mockFetch(routes({ 'GET /api/v1/follows/tournament:17': () => v1Error(401, 'unauthorized') }))
    ;({ w } = await mountScreen(FollowDetailScreen, '/follows/tournament/17', '/follows/:kind/:id'))
    await flush()
    expect(authNeeded.value).toBe(true)
  })
})

// ---------------------------------------------------------------------------------------------------

describe('Events', () => {
  const routes = (over: Record<string, unknown> = {}) => ({
    ...SPORTS,
    ...TOURNAMENTS,
    'GET /api/v1/tournaments/17/seasons': list([{ id: 61627, tournament_id: 17, name: 'PL 24/25', year: '24/25' }]),
    'GET /api/v1/events': page([
      event(),
      event({ id: 9100002, slices_summary: { selected: 6, ok: 4, empty: 0, error: 2 }, participants: { home: { id: 1, name: 'Sevilla' }, away: { id: 2, name: 'Betis' } } }),
      event({ id: 9100004, quality: { ...event().quality, source: 'listing', settlement: 'open' }, status: { type: 'notstarted', code: 0, description: 'Not started', class: 'not_started' }, score: { family: 'football', home: null, away: null, half_time: null, regulation: null, after_extra_time: null, penalties: null }, slices_summary: { selected: 6, ok: 0, empty: 0, error: 0 } }),
      event({ id: 9500001, tournament_id: null }),
    ], 'C2'),
    'GET /api/v1/status': { data: status() },
    ...over,
  })

  it('lists the stored events with score, status and data; every status by default', async () => {
    const f = mockFetch(routes())
    ;({ w } = await mountScreen(EventsScreen, '/events'))
    await flush()
    const q = params(f, 'GET /api/v1/events')
    // FX-14a: upcoming and in-progress matches are not hidden; "All statuses" is the pressed chip
    expect(q.getAll('status')).toEqual([])
    expect(w.find('[data-class="all"]').attributes('aria-pressed')).toBe('true')
    expect([q.get('sort'), q.get('include')]).toEqual(['-start_utc', 'slices_summary'])
    const rows = w.findAll('tbody tr')
    expect(rows[0].text()).toContain('Chelsea')
    expect(rows[0].text()).toContain('1 – 3')
    expect(rows[0].text()).toContain('Premier League')
    expect(rows[0].text()).toContain('6/6')
    expect(rows[0].find('[data-status="event:completed"]').exists()).toBe(true)
    expect(rows[1].find(`[aria-label="${t('ui.events.failedSlices', { n: 2 })}"]`).exists()).toBe(true)
    expect(rows[2].text()).toContain(t('ui.events.listingOnly'))
    expect(rows[0].find('a').attributes('href')).toBe('/events/9100003')
    expect(w.text()).toContain(t('ui.events.notLive'))
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('filters by the server and keeps them in the address; sort and cursor paging', async () => {
    const f = mockFetch(routes())
    let router
    ;({ w, router } = await mountScreen(EventsScreen, '/events?tournament=17&has=missing&q=Chelsea', '/events'))
    await flush()
    const q = params(f, 'GET /api/v1/events')
    expect([q.get('tournament'), q.get('has'), q.get('q')]).toEqual(['17', 'missing', 'Chelsea'])
    await w.find('[data-class="void"]').trigger('click')
    await flush()
    expect(router.currentRoute.value.query.status).toEqual(['void'])
    expect(params(f, 'GET /api/v1/events').getAll('status')).toEqual(['void'])
    await w.find('[data-sort="start"]').trigger('click')
    await flush()
    expect(params(f, 'GET /api/v1/events').get('sort')).toBe('start_utc')
    await w.findAll('footer button').find((b) => b.text().includes(t('ui.table.next')))!.trigger('click')
    await flush()
    expect(params(f, 'GET /api/v1/events').get('cursor')).toBe('C2')
    expect(w.text()).toContain(t('ui.filter.active', { n: 4 }))
  })

  it('fetch missing data sends only the selected events with something missing, by tournament', async () => {
    const f = mockFetch(routes({ 'POST /api/v1/jobs': { data: job({ id: 'F1', kind: 'fetch', state: 'running' }) } }))
    ;({ w } = await mountScreen(EventsScreen, '/events'))
    await flush()
    await w.find('[data-testid="select-page"]').setValue(true)
    expect(w.find('[data-testid="bulk-bar"]').text()).toContain(t('ui.table.selected', { n: 4 }))
    await w.find('[data-testid="bulk-missing"]').trigger('click')
    expect(w.find('[role="alertdialog"]').text()).toContain(t('ui.jobs.start.sendsRequests'))
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs')).toEqual({ kind: 'fetch', spec: { selections: [{ league_id: 17, match_ids: [9100002, 9100004] }] } })
    expect(w.find('[data-testid="bulk-bar"]').exists()).toBe(false)
  })

  it('fetch again sends every selected event; events without a tournament are left out, and a 409 stays', async () => {
    const f = mockFetch(routes({ 'POST /api/v1/jobs': () => v1Error(409, 'job_running', { holder: { pid: 7 } }) }))
    ;({ w } = await mountScreen(EventsScreen, '/events'))
    await flush()
    await w.find('[data-testid="select-page"]').setValue(true)
    await w.find('[data-testid="bulk-again"]').trigger('click')
    expect(w.find('[role="alertdialog"]').text()).toContain(t('ui.events.bulk.skipped', { n: 1 }))
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs').spec.selections).toEqual([{ league_id: 17, match_ids: [9100003, 9100002, 9100004] }])
    expect(w.find('[role="alertdialog"] [role="alert"]').text()).toContain(t('ui.error.job_running'))
  })

  it('loading; empty without data and with filters; error by code; 401', async () => {
    const d = deferred<Response>()
    mockFetch(routes({ 'GET /api/v1/events': () => d.promise }))
    ;({ w } = await mountScreen(EventsScreen, '/events'))
    await flush()
    expect(w.find('[data-testid="skeleton"]').exists()).toBe(true)
    d.resolve(json(page([])))
    await flush()
    expect(w.find('[data-testid="empty"]').text()).toContain(t('ui.events.empty'))
    w.unmount()
    mockFetch(routes({ 'GET /api/v1/events': page([]) }))
    ;({ w } = await mountScreen(EventsScreen, '/events?sport=football', '/events'))
    await flush()
    expect(w.find('[data-testid="empty"]').text()).toContain(t('ui.events.emptyFiltered'))
    w.unmount()
    mockFetch(routes({ 'GET /api/v1/events': () => v1Error(422, 'invalid_request') }))
    ;({ w } = await mountScreen(EventsScreen, '/events'))
    await flush()
    expect(w.find('[data-testid="error-state"]').attributes('data-code')).toBe('invalid_request')
    w.unmount()
    mockFetch(routes({ 'GET /api/v1/events': () => v1Error(401, 'unauthorized') }))
    ;({ w } = await mountScreen(EventsScreen, '/events'))
    await flush()
    expect(authNeeded.value).toBe(true)
  })
})

// ---------------------------------------------------------------------------------------------------

describe('Event detail', () => {
  const STATS = { statistics: [{ period: 'ALL', groups: [{ groupName: 'Match overview', statisticsItems: [{ name: 'Ball possession', home: '41%', away: '59%', homeValue: 41, awayValue: 59 }] }] }] }
  const routes = (over: Record<string, unknown> = {}) => ({
    ...SPORTS,
    ...TOURNAMENTS,
    'GET /api/v1/tournaments/17/seasons': list([{ id: 61627, tournament_id: 17, name: 'PL 24/25', year: '24/25' }]),
    'GET /api/v1/events/9100003': { data: event() },
    'GET /api/v1/events/9100003/slices': list([
      slice('event'),
      slice('statistics'),
      slice('lineups', { state: 'error', error: { reason: 'rate_limited', http_status: 429, at_utc: null, count: 2 } }),
      slice('h2h', { state: 'empty', has_payload: false, fetched_at_utc: null }),
    ]),
    'GET /api/v1/events/9100003/odds': list([]),
    'GET /api/v1/changes': page([change()]),
    'GET /api/v1/events/9100003/raw': () =>
      new Response(JSON.stringify({ event: { id: 9100003, venue: { name: 'Stamford Bridge' }, referee: { name: 'M. Oliver' } } }), {
        headers: { 'Content-Type': 'application/json', ETag: '"3fa9c0ffee11223344"', 'X-Sofascore-Fetched-At': '2026-10-03T02:01:10Z' },
      }),
    'GET /api/v1/events/9100003/slices/statistics': { data: slice('statistics', { payload: STATS }) },
    'GET /api/v1/events/9100003/slices/statistics/raw': () => new Response(JSON.stringify(STATS), { headers: { 'Content-Type': 'application/json', ETag: '"abcdef0123456789"' } }),
    'GET /api/v1/status': { data: status() },
    ...over,
  })

  it('shows the header, the facts and the overview with venue and referee from the raw event', async () => {
    mockFetch(routes())
    ;({ w } = await mountScreen(EventDetailScreen, '/events/9100003', '/events/:id'))
    await flush()
    await flush()
    expect(w.find('h1').text()).toBe('Chelsea – Liverpool')
    const score = w.find('[data-testid="event-score"]')
    expect(score.text()).toContain('1 – 3')
    expect(score.text()).toContain(t('ui.eventDetail.score.ht', { score: '0 – 0' }))
    expect(score.find('[data-status="event:completed"]').exists()).toBe(true)
    const facts = w.find('[data-testid="event-facts"]')
    expect(facts.text()).toContain('9100003')
    expect(facts.text()).toContain('finished / 100')
    expect(w.find('[data-testid="event-overview"]').text()).toContain('Stamford Bridge')
    expect(w.find('[data-testid="event-overview"]').text()).toContain('M. Oliver')
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('the statistics tab is a friendly view for football; another sport shows the raw tree', async () => {
    mockFetch(routes())
    ;({ w } = await mountScreen(EventDetailScreen, '/events/9100003?tab=statistics', '/events/:id'))
    await flush()
    await flush()
    expect(w.find('[data-testid="view-statistics"]').text()).toContain('Ball possession')
    w.unmount()
    mockFetch(routes({ 'GET /api/v1/events/9100003': { data: event({ sport: 'ice-hockey' }) } }))
    ;({ w } = await mountScreen(EventDetailScreen, '/events/9100003?tab=statistics', '/events/:id'))
    await flush()
    await flush()
    expect(w.find('[data-testid="view-statistics"]').exists()).toBe(false)
    expect(w.find('[data-testid="json-viewer"]').exists()).toBe(true)
    expect(w.text()).toContain(t('ui.eventDetail.rawTreeNote'))
  })

  it('the data tab lists every slice with its state; the raw view opens in a panel with search, copy and download', async () => {
    mockFetch(routes())
    ;({ w } = await mountScreen(EventDetailScreen, '/events/9100003?tab=data', '/events/:id'))
    await flush()
    await flush()
    const rows = w.findAll('[data-testid="event-data"] tbody tr')
    expect(rows).toHaveLength(4)
    expect(rows[2].find('[data-status="slice:error"]').exists()).toBe(true)
    expect(rows[2].text()).toContain(t('ui.eventDetail.failedBecause', { reason: 429, n: 2 }))
    expect(rows[3].find('[data-status="slice:empty"]').exists()).toBe(true)
    await rows[1].find('button').trigger('click')
    await flush()
    const panel = w.find('[role="dialog"]')
    expect(panel.text()).toContain(t('ui.raw.title', { key: t('ui.slice.statistics') }))
    expect(panel.find('[data-testid="raw-facts"]').text()).toContain('sha256 abcdef012345')
    expect(panel.find('[data-testid="json-download"]').attributes('href')).toBe('/api/v1/events/9100003/slices/statistics/raw')
    await panel.find('input[type="search"]').setValue('possession')
    await flush()
    expect(panel.text()).toContain(t('ui.json.matches', { n: 1 }))
    expect(panel.find('.u-json-row.is-hit').text()).toContain('Ball possession')
    expect(await axeViolations(panel.element)).toEqual([])
  })

  it('the corrections tab lists the changes of this event; fetch again starts a fetch job', async () => {
    const f = mockFetch(routes({ 'POST /api/v1/jobs': { data: job({ id: 'F1', kind: 'fetch', state: 'running', finished_at: null }) }, 'GET /api/v1/jobs/F1': { data: job({ id: 'F1', state: 'running', finished_at: null }) } }))
    ;({ w } = await mountScreen(EventDetailScreen, '/events/9100003?tab=corrections', '/events/:id'))
    await flush()
    await flush()
    expect(params(f, 'GET /api/v1/changes').get('event_id')).toBe('9100003')
    expect(w.find('[data-testid="event-corrections"]').text()).toContain('2-1 → 2-2')
    await w.findAll('button').find((b) => b.text().includes(t('ui.eventDetail.fetchAgain')))!.trigger('click')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/jobs')).toEqual({ kind: 'fetch', spec: { selections: [{ league_id: 17, match_ids: [9100003] }] } })
  })

  it('a match that is not stored; one known only from a schedule; a 401', async () => {
    mockFetch(routes({ 'GET /api/v1/events/9100003': () => v1Error(404, 'not_found') }))
    ;({ w } = await mountScreen(EventDetailScreen, '/events/9100003', '/events/:id'))
    await flush()
    expect(w.find('[data-testid="event-not-found"]').text()).toContain(t('ui.eventDetail.notFound'))
    // single-match follows are "coming soon" (FX-14a): no way into a dead end, only back to the list
    expect(w.findAll('[data-testid="event-not-found"] a').map((a) => a.attributes('href'))).toEqual(['/events'])
    w.unmount()
    mockFetch(routes({ 'GET /api/v1/events/9100003': { data: event({ quality: { ...event().quality, source: 'listing' } }) }, 'GET /api/v1/events/9100003/slices': list([]) }))
    ;({ w } = await mountScreen(EventDetailScreen, '/events/9100003', '/events/:id'))
    await flush()
    expect(w.find('[data-testid="listing-only"]').text()).toBe(t('ui.eventDetail.listingOnly'))
    expect(w.text()).toContain(t('ui.eventDetail.fetchDetails'))
    w.unmount()
    mockFetch(routes({ 'GET /api/v1/events/9100003': () => v1Error(401, 'unauthorized') }))
    ;({ w } = await mountScreen(EventDetailScreen, '/events/9100003', '/events/:id'))
    await flush()
    expect(authNeeded.value).toBe(true)
  })

  it('the raw page says when no payload is stored (404), never an empty JSON', async () => {
    mockFetch(routes({ 'GET /api/v1/events/9100003/slices/h2h/raw': () => v1Error(404, 'not_found') }))
    ;({ w } = await mountScreen(RawScreen, '/events/9100003/raw/h2h', '/events/:id/raw/:key/:sub?'))
    await flush()
    expect(w.find('[data-testid="raw-payload"]').text()).toContain(t('ui.raw.none'))
    expect(w.find('[data-testid="json-viewer"]').exists()).toBe(false)
  })
})

// ---------------------------------------------------------------------------------------------------

describe('Corrections', () => {
  const routes = (over: Record<string, unknown> = {}) => ({
    ...SPORTS,
    ...TOURNAMENTS,
    'GET /api/v1/changes': page([change(), change({ seq: 2, event_id: 9100002, status_regressed: true, old_status_class: 'completed', new_status_class: 'void', fields: [{ path: 'status.type', old: 'finished', new: 'postponed' }] })]),
    'GET /api/v1/events/9100003': { data: event() },
    'GET /api/v1/events/9100002': { data: event({ id: 9100002, participants: { home: { id: 1, name: 'Sevilla' }, away: { id: 2, name: 'Betis' } } }) },
    ...over,
  })

  it('lists each correction with the match, what changed and when; newest first', async () => {
    const f = mockFetch(routes())
    ;({ w } = await mountScreen(CorrectionsScreen, '/corrections'))
    await flush()
    await flush()
    expect(params(f, 'GET /api/v1/changes').get('order')).toBe('desc')
    const rows = w.findAll('tbody tr')
    expect(rows[0].text()).toContain('Chelsea – Liverpool')
    expect(rows[0].text()).toContain('2-1 → 2-2')
    expect(rows[0].text()).toContain('2 h')
    expect(rows[1].text()).toContain(`${t('ui.status.event.completed')} → ${t('ui.status.event.void')}`)
    expect(rows[1].text()).toContain(t('ui.status.quality.regressed'))
    expect(rows[0].find('a').attributes('href')).toBe('/events/9100003?tab=corrections')
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('filters tournament and dates on the server, "status regressed" within the page', async () => {
    const f = mockFetch(routes())
    ;({ w } = await mountScreen(CorrectionsScreen, '/corrections?tournament=17&from=2026-10-01', '/corrections'))
    await flush()
    expect([params(f, 'GET /api/v1/changes').get('tournament'), params(f, 'GET /api/v1/changes').get('from')]).toEqual(['17', '2026-10-01'])
    await w.find('[data-filter="regressed"]').setValue(true)
    await flush()
    expect(w.findAll('tbody tr')).toHaveLength(1)
  })

  it('empty, error, 401', async () => {
    mockFetch(routes({ 'GET /api/v1/changes': page([]) }))
    ;({ w } = await mountScreen(CorrectionsScreen, '/corrections'))
    await flush()
    expect(w.find('[data-testid="empty"]').text()).toContain(t('ui.corrections.empty'))
    w.unmount()
    mockFetch(routes({ 'GET /api/v1/changes': () => v1Error(500, 'internal') }))
    ;({ w } = await mountScreen(CorrectionsScreen, '/corrections'))
    await flush()
    expect(w.find('[data-testid="error-state"]').exists()).toBe(true)
    w.unmount()
    mockFetch(routes({ 'GET /api/v1/changes': () => v1Error(401, 'unauthorized') }))
    ;({ w } = await mountScreen(CorrectionsScreen, '/corrections'))
    await flush()
    expect(authNeeded.value).toBe(true)
  })
})
