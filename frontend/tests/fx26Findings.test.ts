import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import SettingsScreen from '@/screens/settings/SettingsScreen.vue'
import EventsScreen from '@/screens/events/EventsScreen.vue'
import { dayText } from '@/ui/time'
import EventDetailScreen from '@/screens/events/EventDetailScreen.vue'
import FollowEditorScreen from '@/screens/follows/FollowEditorScreen.vue'
import { followName } from '@/screens/follows/followText'
import FollowDetailScreen from '@/screens/follows/FollowDetailScreen.vue'
import { followCoverage, leagueCoverage } from '@/screens/follows/followCoverage'
import { resetSports } from '@/app/sports'
import { needsData, resetNames } from '@/screens/events/eventText'
import { authNeeded } from '@/lib/auth'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { callsTo, flush, mockFetch } from './helpers'
import { axeViolations, event, follow, mountScreen, setting, settingsDoc, sportWithOdds, status } from './v1'

/**
 * FX-26: the findings of the live validation of 2026-10-08 outside the match header (M1 to M4, M12 to M17;
 * the header and the odds are in multiSport.test.ts). No test reaches a server or SofaScore.
 */
const t = i18n.global.t
let w: VueWrapper
const list = <T>(data: T[]) => ({ data, page: { limit: 0, next_cursor: null } })

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
  authNeeded.value = false
  resetSports()
  resetNames()
})
afterEach(() => w?.unmount())

describe('follow names with "/" (M4)', () => {
  const DOUBLES = 'L. Andersson / O. Andersson – M. Nilsson / K. Berg'

  it('a match’s or a team’s name keeps its "/"; a league’s loses it; ":" and line breaks never pass', () => {
    expect(followName(DOUBLES, 'event')).toBe(DOUBLES)
    expect(followName('Andersson / Andersson', 'team')).toBe('Andersson / Andersson')
    expect(followName('ATP/WTA Cup', 'tournament')).toBe('ATP – WTA Cup')
    expect(followName('Liga: Apertura\nPlay-offs', 'tournament')).toBe('Liga – Apertura Play-offs')
    expect(followName('A\\B:', 'team')).toBe('A – B')
    expect(followName('x'.repeat(90), 'player')).toHaveLength(80)
  })

  it('"Follow this match" on a doubles match opens the editor with a name the server accepts', async () => {
    const padel = event({
      id: 17260001,
      sport: 'padel',
      participants: { home: { id: 1, name: 'L. Andersson / O. Andersson', slug: null, short_name: null, country_code: null }, away: { id: 2, name: 'M. Nilsson / K. Berg', slug: null, short_name: null, country_code: null } },
    })
    mockFetch({
      'GET /api/v1/sports': list([]),
      'GET /api/v1/events/17260001': { data: padel },
      'GET /api/v1/events/17260001/extra': { data: { note: null, series: null, venue: null, referee: null } },
      'GET /api/v1/events/17260001/slices': list([]),
      'GET /api/v1/events/17260001/odds': list([]),
      'GET /api/v1/changes': list([]),
      'GET /api/v1/tournaments': list([]),
      'GET /api/v1/tournaments/17/seasons': list([]),
    })
    let router
    ;({ w, router } = await mountScreen(EventDetailScreen, '/events/17260001', '/events/:id'))
    await flush()
    await w.find('button[aria-haspopup="menu"]').trigger('click')
    await w.find('[data-key="follow-match"]').trigger('click')
    await flush()
    expect(router.currentRoute.value.query).toEqual({ kind: 'event', id: '17260001', name: DOUBLES, sport: 'padel' })
  })

  it('the editor keeps a match’s "/" and cleans a league name it fills in', async () => {
    const routes = { 'GET /api/v1/sports': list([sportWithOdds('padel'), sportWithOdds('tennis')]), 'GET /api/v1/status': { data: status() }, 'GET /api/v1/follows': list([]) }
    mockFetch(routes)
    ;({ w } = await mountScreen(FollowEditorScreen, `/follows/new?kind=event&id=17260001&name=${encodeURIComponent(DOUBLES)}&sport=padel`, '/follows/new'))
    await flush()
    expect((w.find('[data-testid="editor-name"]').element as HTMLInputElement).value).toBe(DOUBLES)
    w.unmount()
    mockFetch(routes)
    ;({ w } = await mountScreen(FollowEditorScreen, `/follows/new?kind=tournament&id=2391&name=${encodeURIComponent('ATP/WTA United Cup')}&sport=tennis`, '/follows/new'))
    await flush()
    expect((w.find('[data-testid="editor-name"]').element as HTMLInputElement).value).toBe('ATP – WTA United Cup')
  })
})

describe('coverage counts finished matches only, for every kind of follow (M12, M12b)', () => {
  const ev = (id: number, cls: string, source: 'event' | 'listing') =>
    event({ id, status: { type: cls === 'completed' ? 'finished' : 'notstarted', code: cls === 'completed' ? 100 : 0, description: '', class: cls as never }, quality: { ...event().quality, source } })
  // like the Celtics' page: two played matches with details, one played without, three fixtures already read
  const TEAM = [ev(1, 'completed', 'event'), ev(2, 'completed', 'event'), ev(3, 'completed', 'listing'), ev(4, 'not_started', 'event'), ev(5, 'not_started', 'event'), ev(6, 'not_started', 'listing')]

  it('a team: upcoming fixtures are neither covered nor missing', async () => {
    mockFetch({ 'GET /api/v1/events': list(TEAM) })
    expect(await followCoverage({ kind: 'team', entity_id: 3422 })).toEqual({ matches: 3, details: 2, coverage: 66.7, more: false })
  })

  it('a single match not played yet has no finished match; a played one is covered', async () => {
    mockFetch({ 'GET /api/v1/events/7': { data: ev(7, 'not_started', 'event') }, 'GET /api/v1/events/8': { data: ev(8, 'completed', 'event') } })
    expect(await followCoverage({ kind: 'event', entity_id: 7 })).toEqual({ matches: 0, details: 0, coverage: 0, more: false })
    expect(await followCoverage({ kind: 'event', entity_id: 8 })).toEqual({ matches: 1, details: 1, coverage: 100, more: false })
  })

  it('a league: finished_details of finished matches (EHF: 29 finished, 43 detailed of which 14 upcoming)', () => {
    expect(leagueCoverage({ finished: 29, finished_details: 29, matches: 43, details: 43, coverage: 100 })).toEqual({ matches: 29, details: 29, coverage: 100, more: false })
    expect(leagueCoverage({ finished: 29, finished_details: 20, matches: 43, details: 34, coverage: 79.1 }).coverage).toBe(69)
    // an older server without the field: its own numbers
    expect(leagueCoverage({ finished: 29, matches: 43, details: 43, coverage: 100 } as never)).toEqual({ matches: 43, details: 43, coverage: 100, more: false })
  })

  it('a team’s page: the rule in the facts, "not played" in its list; a match not played yet says so', async () => {
    mockFetch({
      'GET /api/v1/sports': list([]),
      'GET /api/v1/tournaments': list([]),
      'GET /api/v1/follows/team:3422': { data: follow({ id: 'team:3422', kind: 'team', entity_id: 3422, name: 'Boston Celtics', sport: 'basketball' }) },
      'GET /api/v1/jobs': list([]),
      'GET /api/v1/events': list(TEAM),
      'GET /api/v1/status': { data: status() },
    })
    ;({ w } = await mountScreen(FollowDetailScreen, '/follows/team/3422', '/follows/:kind/:id'))
    await flush()
    await flush()
    expect(w.find('[data-testid="coverage"]').text()).toContain(t('ui.followDetail.coverageText', { details: '2', matches: '3' }))
    expect(w.findAll('[data-testid="not-played"]').length).toBeGreaterThan(0)
    w.unmount()
    mockFetch({
      'GET /api/v1/sports': list([]),
      'GET /api/v1/follows/event:7': { data: follow({ id: 'event:7', kind: 'event', entity_id: 7, name: 'A – B' }) },
      'GET /api/v1/jobs': list([]),
      'GET /api/v1/events/7': { data: ev(7, 'not_started', 'event') },
      'GET /api/v1/status': { data: status() },
    })
    ;({ w } = await mountScreen(FollowDetailScreen, '/follows/event/7', '/follows/:kind/:id'))
    await flush()
    await flush()
    expect(w.find('[data-testid="coverage-none"]').text()).toBe(t('ui.followDetail.noFinished'))
  })

  it('a match not played yet needs no "Fetch missing data"', () => {
    expect(needsData(ev(4, 'not_started', 'event'))).toBe(false)
    expect(needsData({ ...ev(3, 'completed', 'listing') })).toBe(true)
  })
})

describe('a team’s matches: played first, a switch to the upcoming ones, no sport filter (M15)', () => {
  const routes = () => ({
    'GET /api/v1/sports': list([sportWithOdds('football'), sportWithOdds('basketball')]),
    'GET /api/v1/tournaments': list([]),
    'GET /api/v1/follows/team:3422': { data: follow({ id: 'team:3422', kind: 'team', entity_id: 3422, name: 'Boston Celtics', sport: 'basketball' }) },
    'GET /api/v1/jobs': list([]),
    'GET /api/v1/events': list([event({ id: 1, sport: 'basketball' })]),
    'GET /api/v1/status': { data: status() },
  })
  const query = (f: ReturnType<typeof mockFetch>) => new URL(String(callsTo(f, 'GET /api/v1/events').at(-1)![0]), 'http://x').searchParams

  it('starts with the played matches, newest first; "Upcoming" lists the fixtures, soonest first', async () => {
    const f = mockFetch(routes())
    let router
    ;({ w, router } = await mountScreen(FollowDetailScreen, '/follows/team/3422?tab=events', '/follows/:kind/:id'))
    await flush()
    await flush()
    const when = w.find('[data-testid="events-when"]')
    expect(when.find('[aria-pressed="true"]').attributes('data-when')).toBe('played')
    expect(query(f).getAll('status')).toEqual(['live', 'completed', 'decided_without_play'])
    expect(query(f).get('sort')).toBe('-start_utc')
    expect(query(f).get('participant')).toBe('3422')
    // the status chips say what the switch chose
    expect(w.find('[data-class="all"]').attributes('aria-pressed')).toBe('false')
    expect(w.find('[data-class="completed"]').attributes('aria-pressed')).toBe('true')
    await when.find('[data-when="upcoming"]').trigger('click')
    await flush()
    await flush()
    expect(router.currentRoute.value.query.when).toBe('upcoming')
    expect(query(f).getAll('status')).toEqual(['not_started'])
    expect(query(f).get('sort')).toBe('start_utc')
    await w.find('[data-testid="events-when"] [data-when="all"]').trigger('click')
    await flush()
    await flush()
    expect(query(f).getAll('status')).toEqual([])
    expect(await axeViolations(w.find('[data-testid="events-when"]').element)).toEqual([])
  })

  it('the sport filter is not offered: the team plays one sport, and the list asks for it', async () => {
    const f = mockFetch(routes())
    ;({ w } = await mountScreen(FollowDetailScreen, '/follows/team/3422?tab=events', '/follows/:kind/:id'))
    await flush()
    await flush()
    expect(w.find('[data-filter="sport"]').exists()).toBe(false)
    expect(query(f).get('sport')).toBe('basketball')
  })
})

describe('date fields say the chosen day in the reader’s words (M13)', () => {
  it('a calendar day in words, in both languages; anything else as given', () => {
    setLocale('tr')
    expect(dayText('2026-10-07')).toBe('7 Ekim 2026')
    setLocale('en')
    expect(dayText('2026-10-07')).toBe('October 7, 2026')
    expect(dayText('yesterday')).toBe('yesterday')
    expect(dayText(null)).toBe('')
  })

  it('Matches: the day under the field and in the chip; the field carries the UI language', async () => {
    setLocale('tr')
    mockFetch({ 'GET /api/v1/sports': list([]), 'GET /api/v1/tournaments': list([]), 'GET /api/v1/events': list([]), 'GET /api/v1/status': { data: status() } })
    ;({ w } = await mountScreen(EventsScreen, '/events?from=2026-10-07&to=2026-10-08', '/events'))
    await flush()
    expect(w.find('[data-filter="from"]').attributes('lang')).toBe('tr')
    expect(w.find('[data-testid="date-hint-from"]').text()).toBe('7 Ekim 2026')
    expect(w.find('[data-testid="date-hint-to"]').text()).toBe('8 Ekim 2026')
  })
})

describe('Settings › Data texts (M1, M2, M3)', () => {
  const tennis = () => {
    const base = sportWithOdds('tennis')
    const extra = (key: string, owner: string, over: Record<string, unknown> = {}) => ({
      key, path: `/x/${key}`, required: false, default_enabled: false, selected: false, group: 'core', owner, phases: ['post'], keep_history: false, ...over,
    })
    return { ...base, slices: [...base.slices, extra('point_by_point', 'event'), extra('team_rankings', 'team', { group: 'rankings' })] }
  }

  async function open() {
    mockFetch({
      'GET /api/v1/settings': {
        data: settingsDoc([setting('defaults.slices', ['core']), setting('fetch.only_finished', true)], {
          slices: [{ sport: 'tennis', enable: [], disable: [], source: 'default', source_name: '', locked: false, writable: true }],
        } as never),
      },
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/sports': list([tennis()]),
      'GET /api/v1/follows': list([]),
    })
    ;({ w } = await mountScreen(SettingsScreen, '/settings?tab=data', '/settings'))
    await flush()
    await flush()
  }

  it('the all-sports block says "not available in every sport"; a sport’s block says "for this sport"', async () => {
    await open()
    const blocks = w.findAll('[data-testid="slice-checklist"]')
    const global = blocks[0].find('[data-slice="point_by_point"]')
    expect(global.text()).toContain(t('ui.slicePicker.optionalAny'))
    expect(global.text()).not.toContain(t('ui.slicePicker.optional'))
    const own = blocks[1].find('[data-slice="point_by_point"]')
    expect(own.text()).toContain(t('ui.slicePicker.optional'))
    setLocale('tr')
    await flush()
    expect(blocks[0].find('[data-slice="point_by_point"]').text()).toContain('her sporda bulunmaz')
    expect(blocks[0].text()).not.toContain('bu sporda')
  })

  it('team data: the tennis player’s rankings, not "player rankings"', async () => {
    setLocale('tr')
    await open()
    const team = w.findAll('[data-testid="slice-checklist"]')[0].find('[data-section="team"]')
    expect(team.find('[data-slice="team_rankings"]').text()).toContain('Tenisçinin sıralaması (ATP, WTA)')
    expect(team.text()).not.toContain('Oyuncu sıralamaları')
    expect(await axeViolations(w.find('[data-testid="slice-defaults"]').element)).toEqual([])
  })

  it('"finished matches only" says what it affects: league downloads, Overview counts, old lists; not the Matches screen', async () => {
    await open()
    const row = w.find('[data-setting="fetch.only_finished"]')
    expect(row.text()).toContain('Finished matches only in a league download')
    expect(row.text()).toContain('a league download fetches the details of finished matches only')
    expect(row.text()).toContain('It does not change the Matches screen')
  })
})
