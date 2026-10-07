/* eslint-disable vue/one-component-per-file -- small host components of the dialog checks */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, nextTick, ref } from 'vue'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import { openModals, setTeleportDialogs } from '@/ui/modal'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import FollowEditorScreen from '@/screens/follows/FollowEditorScreen.vue'
import FollowsScreen from '@/screens/follows/FollowsScreen.vue'
import FollowDetailScreen from '@/screens/follows/FollowDetailScreen.vue'
import { JOB_WATCH_MS, watchJob, watchJobs } from '@/app/jobWatch'
import { useStatusStore } from '@/app/statusStore'
import JobDetailScreen from '@/screens/jobs/JobDetailScreen.vue'
import EventDetailScreen from '@/screens/events/EventDetailScreen.vue'
import ExportsScreen from '@/screens/exports/ExportsScreen.vue'
import HealthScreen from '@/screens/HealthScreen.vue'
import { everyText } from '@/screens/jobs/jobText'
import { resetNames } from '@/screens/events/eventText'
import { hitPlace, placeName, playerTeam } from '@/screens/follows/followText'
import { resetSports } from '@/app/sports'
import { callsTo, flush, mockFetch } from './helpers'
import { axeViolations, event, FakeES, follow, job, mountScreen, page, slice, sport, status, useFakeES } from './v1'

/**
 * FX-24: the findings of the end-to-end test of the web UI against the real SofaScore (F5 to F37 of the
 * orchestrator's list), one block per finding. No request leaves the test.
 */
const t = i18n.global.t
let wrappers: VueWrapper[] = []

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
})
afterEach(() => {
  for (const w of wrappers) w.unmount()
  wrappers = []
  document.body.innerHTML = ''
  vi.useRealTimers()
})

describe('F22: a dialog is modal for real', () => {
  beforeEach(() => setTeleportDialogs(true))
  afterEach(() => setTeleportDialogs(false))

  /** A table row that opens its page on a click, with a remove dialog inside it, as in Leagues & follows. */
  const Row = defineComponent({
    props: { onRow: { type: Function, required: true } },
    setup(props) {
      const open = ref(true)
      return () =>
        h('table', [
          h('tbody', [
            h('tr', { 'data-row': '', onClick: () => (props.onRow as () => void)() }, [
              h('td', 'UEFA Super Cup'),
              h('td', { class: 'text-right whitespace-nowrap' }, [
                open.value
                  ? h(ConfirmDialog, { title: 'Stop following?', confirmLabel: 'Remove', danger: true, typedWord: 'DELETE', onClose: () => (open.value = false) }, () => h('p', { 'data-testid': 'text' }, 'Its matches go too.'))
                  : null,
              ]),
            ]),
          ]),
        ])
    },
  })

  it('is shown at the end of <body>, outside the row; a click in it does not open the row; the page behind is inert', async () => {
    const page = document.createElement('div')
    page.id = 'app'
    document.body.appendChild(page)
    const onRow = vi.fn()
    const w = mount(Row, { props: { onRow }, global: { plugins: [i18n] }, attachTo: page })
    wrappers.push(w)
    await flush()
    const dialog = document.querySelector<HTMLElement>('[role="alertdialog"]')!
    expect(dialog).not.toBeNull()
    // not inside the table row any more
    expect(w.element.contains(dialog)).toBe(false)
    expect(dialog.closest('tr')).toBeNull()
    expect(dialog.parentElement!.parentElement).toBe(document.body)
    // clicking its text or its field does not reach the row (before: the row opened its page)
    dialog.querySelector<HTMLElement>('[data-testid="text"]')!.click()
    dialog.querySelector<HTMLInputElement>('input')!.click()
    expect(onRow).not.toHaveBeenCalled()
    // the page behind is inert while the dialog is open: neither clickable nor focusable nor in the a11y tree
    expect(page.hasAttribute('inert')).toBe(true)
    expect(dialog.closest('[inert]')).toBeNull()
    expect(openModals()).toBe(1)
    // the typed word field is the focused element and the only text field outside the inert page
    expect(document.activeElement).toBe(dialog.querySelector('input'))
    const live = [...document.querySelectorAll('input')].filter((x) => !x.closest('[inert]'))
    expect(live).toHaveLength(1)
    expect(await axeViolations(dialog)).toEqual([])
    // Cancel closes it: the page is live again
    const cancel = [...dialog.querySelectorAll('button')].find((b) => b.textContent?.trim() === t('ui.common.cancel'))!
    cancel.click()
    await nextTick()
    await flush()
    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
    expect(page.hasAttribute('inert')).toBe(false)
    expect(openModals()).toBe(0)
  })

  it('a dialog over a dialog: only the newest is live; closing it gives the first one back', async () => {
    const page = document.createElement('div')
    document.body.appendChild(page)
    const second = ref(true)
    const Two = defineComponent({
      setup: () => () => [
        h(ConfirmDialog, { title: 'First', confirmLabel: 'OK' }, () => h('p', 'one')),
        second.value ? h(ConfirmDialog, { title: 'Second', confirmLabel: 'OK' }, () => h('p', 'two')) : null,
      ],
    })
    const w = mount(Two, { global: { plugins: [i18n] }, attachTo: page })
    wrappers.push(w)
    await flush()
    const [first, top] = [...document.querySelectorAll<HTMLElement>('[role="alertdialog"]')]
    expect(top.closest('[inert]')).toBeNull()
    expect(first.closest('[inert]')).not.toBeNull()
    second.value = false
    await nextTick()
    await flush()
    expect(first.closest('[inert]')).toBeNull()
    expect(page.hasAttribute('inert')).toBe(true)
  })
})

const list = <T>(data: T[]) => ({ data, page: { limit: 0, next_cursor: null } })
const SPORTS = {
  'GET /api/v1/sports': list([sport('football'), sport('tennis')]),
  'GET /api/v1/sports/football': { data: sport('football') },
  'GET /api/v1/sports/tennis': { data: sport('tennis') },
  'GET /api/v1/status': { data: status() },
}
const regionEn = (code: string) => new Intl.DisplayNames(['en'], { type: 'region' }).of(code)

describe('F12, F31, F32: search hits in the reader’s words', () => {
  beforeEach(() => resetSports())

  it('regions, home nations and countries SofaScore writes in English are named in the reader’s language', () => {
    setLocale('tr')
    expect(placeName(null, 'Europe')).toBe('Avrupa')
    expect(placeName(null, 'South America')).toBe('Güney Amerika')
    expect(placeName(null, 'North & Central America')).toBe('Kuzey ve Orta Amerika')
    expect(placeName('EN', 'England')).toBe('İngiltere')
    expect(placeName(null, 'England')).toBe('İngiltere')
    // a country without a code, by SofaScore's English name, also an older one ("Turkey")
    expect(placeName(null, 'Turkey')).toBe('Türkiye')
    expect(placeName(null, 'Spain')).toBe('İspanya')
    expect(placeName('TR', 'Turkey')).toBe('Türkiye')
    // a league's category that is a tour, not a place, stays as SofaScore writes it
    expect(placeName(null, 'ATP')).toBe('ATP')
    expect(hitPlace({ category: { name: 'Europe' }, country: null })).toBe('Avrupa')
    setLocale('en')
    expect(placeName(null, 'Europe')).toBe('Europe')
    expect(placeName('EN', 'England')).toBe('England')
    expect(placeName(null, 'Turkey')).toBe(regionEn('TR'))
    expect(placeName('', null)).toBe('')
  })

  it('SofaScore’s placeholder team “No team” is never shown', () => {
    expect(playerTeam({ id: 0, name: 'No team' })).toBeNull()
    expect(playerTeam({ name: ' no team ' })).toBeNull()
    expect(playerTeam(null)).toBeNull()
    expect(playerTeam({ name: 'Galatasaray' })).toBe('Galatasaray')
  })

  it('a tennis player SofaScore lists as a team is shown with the players and followed as a team; a region in Turkish', async () => {
    setLocale('tr')
    const f = mockFetch({
      ...SPORTS,
      'POST /api/v1/tournaments/search': list([
        { kind: 'team', id: 412345, name: 'Chiara Icardi', slug: 'icardi-chiara', sport: 'tennis', category: { country_code: 'IT' }, country: { code: 'IT', name: 'Italy' }, team: null, followed: false },
        { kind: 'player', id: 70996, name: 'Mauro Icardi', sport: 'football', category: { country_code: 'AR' }, country: { code: 'AR', name: 'Argentina' }, team: { id: 3061, name: 'Galatasaray' }, followed: false },
        { kind: 'player', id: 99001, name: 'Luca Icardi', sport: 'football', category: { country_code: 'IT' }, country: { code: 'IT', name: 'Italy' }, team: { id: 0, name: 'No team' }, followed: false },
        { kind: 'tournament', id: 384, name: 'CONMEBOL Libertadores', sport: 'football', category: { id: 1470, name: 'South America' }, country: null, team: null, followed: false },
      ]),
    })
    const { w } = await mountScreen(FollowEditorScreen, '/follows/new')
    wrappers.push(w)
    await flush()
    await w.find('[data-testid="editor-query"]').setValue('icardi')
    await w.find('[data-testid="editor-search"]').trigger('submit')
    await flush()
    expect(callsTo(f, 'POST /api/v1/tournaments/search')).toHaveLength(1)
    // no "Teams" group: the tennis player is with the players, with the player icon
    expect(w.find('[data-group="team"]').exists()).toBe(false)
    const players = w.find('[data-group="player"]')
    expect(players.findAll('[role="option"]').map((o) => o.attributes('data-hit'))).toEqual(['team:412345', 'player:70996', 'player:99001'])
    expect(w.find('[data-group="tournament"] [data-hit="tournament:384"]').text()).toContain('Güney Amerika')
    // “No team” is not a team
    expect(w.find('[data-hit="player:99001"]').text()).not.toContain('No team')
    expect(w.find('[data-hit="player:99001"] [data-testid="hit-team"]').exists()).toBe(false)
    expect(w.find('[data-hit="player:70996"] [data-testid="hit-team"]').text()).toBe(t('ui.followEditor.playsFor', { team: 'Galatasaray' }))
    expect(await axeViolations(w.find('[data-testid="editor-hits"]').element)).toEqual([])
    // picked: shown as a player, followed as a team (its follow id stays team:412345)
    await w.find('[data-hit="team:412345"]').trigger('click')
    await flush()
    const picked = w.find('[data-testid="editor-picked"]')
    expect(picked.attributes('data-hit')).toBe('team:412345')
    expect(picked.text()).toContain(t('ui.follows.kind.player'))
    expect(w.find('[data-testid="editor-individual"]').text()).toBe(t('ui.suggest.individual', { sport: 'tenis' }))
    expect((w.find('[data-kind="team"] input').element as HTMLInputElement).checked).toBe(true)
  })
})

describe('F26: same-named teams can be told apart', () => {
  const fener = (id: number, sportSlug: string, name = 'Fenerbahçe') => ({ kind: 'team', id, name, sport: sportSlug, category: { country_code: 'TR' }, country: { code: 'TR', name: 'Türkiye' }, team: null, followed: false })

  it('two hits with the same name and sport show their numbers; the others do not', async () => {
    mockFetch({ ...SPORTS, 'POST /api/v1/tournaments/search': list([fener(3052, 'football'), fener(36456, 'volleyball'), fener(36460, 'volleyball'), fener(253261, 'football', 'Fenerbahçe U19')]) })
    const { w } = await mountScreen(FollowEditorScreen, '/follows/new')
    wrappers.push(w)
    await flush()
    await w.find('[data-testid="editor-query"]').setValue('fener')
    await w.find('[data-testid="editor-search"]').trigger('submit')
    await flush()
    const number = (id: number) => w.find(`[data-hit="team:${id}"] [data-testid="hit-number"]`)
    expect(number(36456).text()).toBe(t('ui.suggest.number', { id: 36456 }))
    expect(number(36456).attributes('title')).toBe(t('ui.suggest.sameName'))
    expect(number(36460).text()).toBe(t('ui.suggest.number', { id: 36460 }))
    expect(number(3052).exists()).toBe(false)
    expect(number(253261).exists()).toBe(false)
  })

  it('the list of follows shows the numbers of two follows with the same name', async () => {
    mockFetch({
      ...SPORTS,
      'GET /api/v1/follows': page([
        follow({ id: 'team:36456', kind: 'team', entity_id: 36456, name: 'Fenerbahçe', sport: 'volleyball' }),
        follow({ id: 'team:36460', kind: 'team', entity_id: 36460, name: 'Fenerbahçe', sport: 'volleyball' }),
        follow({ id: 'team:3052', kind: 'team', entity_id: 3052, name: 'Fenerbahçe', sport: 'football' }),
      ]),
      'GET /api/v1/jobs': page([]),
      'GET /api/v1/events': page([]),
    })
    const { w } = await mountScreen(FollowsScreen, '/follows')
    wrappers.push(w)
    await flush()
    expect(w.findAll('[data-testid="follow-number"]').map((x) => x.text())).toEqual([t('ui.suggest.number', { id: 36456 }), t('ui.suggest.number', { id: 36460 })])
  })
})

describe('F5: the follow page says where the league is from, not its number', () => {
  const supercup = (category: unknown) => ({ data: { id: 465, sport: 'football', category_id: 1465, name: 'UEFA Super Cup', slug: 'uefa-super-cup', category, followed: true } })
  const europe = { id: 1465, sport: 'football', name: 'Europe', slug: 'europe', country_code: null }

  it('"Football · Europe · League", in Turkish "Futbol · Avrupa · Lig"; the number is in the facts; a download that brings the category shows it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let category: unknown = null
    const f = mockFetch({
      ...SPORTS,
      'GET /api/v1/follows/tournament:465': { data: follow({ id: 'tournament:465', entity_id: 465, name: 'UEFA Super Cup', seasons: [76138] }) },
      'GET /api/v1/tournaments/465': () => supercup(category),
      'GET /api/v1/tournaments/465/seasons': list([]),
      'GET /api/v1/tournaments': page([]),
      'GET /api/v1/jobs': page([]),
      'GET /api/v1/jobs/D1': { data: job({ id: 'D1', spec: { follows: ['tournament:465'] } }) },
    })
    const { w } = await mountScreen(FollowDetailScreen, '/follows/tournament/465', '/follows/:kind/:id')
    wrappers.push(w)
    await flush()
    // before the first download the catalog has no category: no number in its place
    expect(w.find('[data-testid="follow-header-line"]').text()).toBe('Football · League')
    expect(w.find('[data-testid="follow-facts"]').text()).toContain(t('ui.followDetail.fact.number'))
    expect(w.find('[data-testid="follow-facts"]').text()).toContain('465')
    // the download ends: the league's record is read again and has its category now
    category = europe
    const stop = watchJobs()
    watchJob({ id: 'D1' })
    await vi.advanceTimersByTimeAsync(JOB_WATCH_MS + 50)
    await flush()
    expect(callsTo(f, 'GET /api/v1/tournaments/465')).toHaveLength(2)
    expect(w.find('[data-testid="follow-header-line"]').text()).toBe('Football · Europe · League')
    setLocale('tr')
    await flush()
    expect(w.find('[data-testid="follow-header-line"]').text()).toBe('Futbol · Avrupa · Lig')
    expect(w.find('[data-testid="follow-header-line"]').text()).not.toContain('#')
    stop()
  })

  it('a country by its code, in the reader’s language', async () => {
    setLocale('tr')
    mockFetch({
      ...SPORTS,
      'GET /api/v1/follows/tournament:52': { data: follow({ id: 'tournament:52', entity_id: 52, name: 'Trendyol Süper Lig' }) },
      'GET /api/v1/tournaments/52': { data: { id: 52, sport: 'football', category_id: 46, name: 'Trendyol Süper Lig', slug: 'x', category: { id: 46, sport: 'football', name: 'Turkey', slug: 'turkey', country_code: 'TR' } } },
      'GET /api/v1/tournaments/52/seasons': list([]),
      'GET /api/v1/jobs': page([]),
    })
    const { w } = await mountScreen(FollowDetailScreen, '/follows/tournament/52', '/follows/:kind/:id')
    wrappers.push(w)
    await flush()
    expect(w.find('[data-testid="follow-header-line"]').text()).toBe('Futbol · Türkiye · Lig')
  })
})

describe('F6: the Seasons tab follows a download and says what "complete" means', () => {
  const season = (counts: Record<string, unknown> | null) => ({ id: 76138, tournament_id: 465, name: 'UEFA Super Cup 2025', year: '2025', counts })
  const empty = { events: 0, finished: 0, details: 0, complete: 0, completion_rate: 0, missing: {}, schedule_fetched_at_utc: null }
  const done = { events: 1, finished: 1, details: 1, complete: 0, completion_rate: 0, missing: { pregame_form: 1 }, schedule_fetched_at_utc: '2026-10-07T19:00:00Z' }

  function mountLeague(seasons: () => unknown) {
    const f = mockFetch({
      ...SPORTS,
      'GET /api/v1/follows/tournament:465': { data: follow({ id: 'tournament:465', entity_id: 465, name: 'UEFA Super Cup', seasons: [76138] }) },
      'GET /api/v1/tournaments/465': { data: { id: 465, sport: 'football', category_id: null, name: 'UEFA Super Cup', slug: 'x', category: null } },
      'GET /api/v1/tournaments/465/seasons': () => list([seasons()]),
      'GET /api/v1/jobs': page([]),
    })
    return f
  }

  it('a detailed match that misses a data type: "with all data: 0 %" and what is missing, with why', async () => {
    mountLeague(() => season(done))
    const { w } = await mountScreen(FollowDetailScreen, '/follows/tournament/465', '/follows/:kind/:id')
    wrappers.push(w)
    await flush()
    const row = w.find('[data-season="76138"]')
    expect(row.find('[data-testid="season-complete"]').text()).toBe(t('ui.followDetail.complete', { pct: '0%' }))
    expect(row.find('[data-testid="season-complete"]').attributes('title')).toBe(t('ui.followDetail.completeHelp'))
    expect(row.find('[data-testid="season-missing"]').text()).toBe('still missing: Pre-game form (1)')
    expect(row.find('[data-testid="season-missing"]').attributes('title')).toBe(t('ui.followDetail.missingHelp'))
    setLocale('tr')
    await flush()
    expect(row.find('[data-testid="season-complete"]').text()).toBe('tüm verisi inen: %0')
    expect(row.find('[data-testid="season-missing"]').text()).toBe(`eksik: ${t('ui.slice.pregame_form')} (1)`)
  })

  it('reads the seasons while a download of the follow runs, and again when it is gone, without the job watch', async () => {
    let counts: Record<string, unknown> = empty
    const f = mountLeague(() => season(counts))
    const { w } = await mountScreen(FollowDetailScreen, '/follows/tournament/465', '/follows/:kind/:id')
    wrappers.push(w)
    await flush()
    const store = useStatusStore()
    const seasonsRead = () => callsTo(f, 'GET /api/v1/tournaments/465/seasons').length
    expect(seasonsRead()).toBe(1)
    expect(w.find('[data-testid="season-counts"]').text()).toContain(t('ui.followDetail.seasonCounts', { events: '0', finished: '0', details: '0' }))
    // a download of this follow runs (started anywhere): each status read brings the seasons again
    store.status = status({ active_job: job({ id: 'R1', state: 'running', spec: { follows: ['tournament:465'] } }) })
    store.fetchedAt = Date.now()
    await flush()
    expect(seasonsRead()).toBe(2)
    // it is gone: the seasons, the league and the jobs are read again, though no job watch told the page
    counts = done
    store.status = status({ active_job: null })
    store.fetchedAt = Date.now() + 1
    await flush()
    expect(seasonsRead()).toBe(3)
    expect(callsTo(f, 'GET /api/v1/tournaments/465')).toHaveLength(2)
    expect(w.find('[data-testid="season-counts"]').text()).toContain(t('ui.followDetail.seasonCounts', { events: '1', finished: '1', details: '1' }))
    // a download of another league changes nothing here
    store.status = status({ active_job: job({ id: 'R2', state: 'running', spec: { follows: ['tournament:17'] } }) })
    store.fetchedAt = Date.now() + 2
    await flush()
    expect(seasonsRead()).toBe(3)
  })
})

describe('F8: the job log names seasons, not their numbers', () => {
  const ID = '01M4BYD0TMM9QB9Y4MY79JVB4V'
  beforeEach(() => {
    resetNames()
    useFakeES()
  })

  it('"Reading the matches of UEFA Super Cup, season 2025", in Turkish "2025 sezonunun"; an unknown season keeps its number', async () => {
    setLocale('tr')
    const f = mockFetch({
      [`GET /api/v1/jobs/${ID}`]: { data: job({ id: ID, state: 'running', finished_at: null, result: null, spec: { follows: ['tournament:465'], names: { 'tournament:465': 'UEFA Super Cup' } } }) },
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/tournaments': page([{ id: 465, sport: 'football', category_id: 1465, name: 'UEFA Super Cup', slug: 'x' }]),
      'GET /api/v1/tournaments/465/seasons': list([
        { id: 76138, tournament_id: 465, name: 'UEFA Super Cup 2025', year: '2025' },
        { id: 61644, tournament_id: 465, name: 'UEFA Super Cup 2024', year: '2024' },
      ]),
    })
    const { w } = await mountScreen(JobDetailScreen, `/jobs/${ID}`, '/jobs/:id')
    wrappers.push(w)
    await flush()
    const es = FakeES.last
    es.emit('log', 1, { code: 'sync_schedule', params: { league_id: 465, season_id: 76138 }, message: 'Fetching matches: league 465, season 76138' }, ID)
    es.emit('log', 2, { code: 'sync_season_outdated', params: { league_id: 465, season_id: 61644, resolved: 99999 }, message: 'x' }, ID)
    await flush()
    const lines = w.findAll('[data-testid="job-log"] li').map((li) => li.text())
    expect(lines.some((l) => l.includes('UEFA Super Cup için 2025 sezonunun maçları okunuyor…'))).toBe(true)
    expect(lines.some((l) => l.includes('#76138'))).toBe(false)
    // a season the stored list does not have keeps its number
    expect(lines.some((l) => l.includes('2024') && l.includes('#99999'))).toBe(true)
    // the season list is read once, from this server
    expect(callsTo(f, 'GET /api/v1/tournaments/465/seasons')).toHaveLength(1)
  })
})

describe('F11: the odds of a match as a table', () => {
  const choice = (name: string, fractional: string, decimal: number, initial: string, initialDecimal: number, change: number, winning: boolean | null = null) => ({
    name, fractional, decimal, initial_fractional: initial, initial_decimal: initialDecimal, change, winning,
  })
  const fullTime = {
    market_id: 1, name: 'Full time', group: '1X2', period: 'Full-time', choice_group: null, label: 'default', is_live: false, suspended: false,
    choices: [choice('1', '4/5', 1.8, '17/20', 1.85, -1, true), choice('X', '3/1', 4, '14/5', 3.8, 1, false), choice('2', '10/3', 4.333, '3/1', 4, 0, false)],
  }
  const goals = {
    market_id: 9, name: 'Match goals', group: 'Over/Under', period: 'Full-time', choice_group: '2.5', label: null, is_live: false, suspended: true,
    choices: [choice('Over', '8/11', 1.727, '4/5', 1.8, -1), choice('Under', '1/1', 2, '10/11', 1.909, 1)],
  }
  const odds = (key: string, markets: unknown[]) => list([{ event_id: 9100003, key, provider_id: 1, fetched_at_utc: '2026-10-07T19:45:45Z', markets }])
  const routes = (over: Record<string, unknown> = {}) => ({
    ...SPORTS,
    'GET /api/v1/tournaments': page([]),
    'GET /api/v1/tournaments/17/seasons': list([]),
    'GET /api/v1/events/9100003': { data: event() },
    'GET /api/v1/events/9100003/slices': list([slice('event')]),
    'GET /api/v1/events/9100003/odds': list([slice('odds_featured', { sub: '1' }), slice('odds_all', { sub: '1' })]),
    'GET /api/v1/events/9100003/odds/odds_featured': odds('odds_featured', [fullTime]),
    'GET /api/v1/events/9100003/odds/odds_all': odds('odds_all', [fullTime, goals]),
    'GET /api/v1/changes': page([]),
    ...over,
  })

  it('markets with their outcomes, decimal and fractional prices, the opening price, the change and the winner; the raw answer stays', async () => {
    setLocale('tr')
    const f = mockFetch(routes())
    const { w } = await mountScreen(EventDetailScreen, '/events/9100003?tab=odds', '/events/:id')
    wrappers.push(w)
    await flush()
    await flush()
    // the latest read only, of the featured list first
    expect(String(callsTo(f, 'GET /api/v1/events/9100003/odds/odds_featured')[0][0])).toContain('history=false')
    const view = w.find('[data-testid="odds-view"]')
    expect(view.text()).toContain(t('ui.odds.provider', { id: 1 }))
    const markets = view.findAll('[data-testid="odds-market"]')
    expect(markets).toHaveLength(1)
    expect(markets[0].find('h3').text()).toBe('Maç sonucu')
    const home = markets[0].find('[data-choice="1"]')
    expect(home.findAll('td').map((td) => td.text())).toEqual([`1${t('ui.odds.won')}`, '1,80', '4/5', '1,85', t('ui.odds.change.down')])
    expect(markets[0].find('[data-choice="X"]').text()).toContain(t('ui.odds.change.up'))
    expect(markets[0].find('[data-choice="2"]').text()).toContain('4,333')
    expect(markets[0].find('[data-choice="2"]').text()).toContain(t('ui.odds.change.none'))
    // the other list: a line market in words, suspended
    await view.find('[data-odds-key="odds_all"]').trigger('click')
    await flush()
    const all = w.findAll('[data-testid="odds-market"]')
    expect(all.map((m) => m.find('h3 span').text())).toEqual(['Maç sonucu', 'Toplam gol 2.5'])
    expect(all[1].text()).toContain(t('ui.odds.suspended'))
    expect(all[1].findAll('tbody tr').map((r) => r.find('td').text())).toEqual(['Üst', 'Alt'])
    // SofaScore's answer is still there, with its raw view
    const raw = w.find('[data-testid="odds-raw"]')
    expect(raw.find('h2').text()).toBe('SofaScore yanıtı')
    expect(raw.findAll('button')).toHaveLength(2)
    expect(await axeViolations(w.find('[data-testid="event-odds"]').element)).toEqual([])
  })

  it('an unknown market keeps SofaScore’s name, marked as English; no odds in a normalized form says so', async () => {
    mockFetch(routes({
      'GET /api/v1/events/9100003/odds': list([slice('odds_featured', { sub: '1' })]),
      'GET /api/v1/events/9100003/odds/odds_featured': odds('odds_featured', [{ ...fullTime, name: 'Penalty in match' }]),
    }))
    const { w } = await mountScreen(EventDetailScreen, '/events/9100003?tab=odds', '/events/:id')
    wrappers.push(w)
    await flush()
    await flush()
    expect(w.find('[data-odds-key]').exists()).toBe(false)
    const title = w.find('[data-testid="odds-market"] h3 span')
    expect(title.text()).toBe('Penalty in match')
    expect(title.attributes('lang')).toBe('en')
    expect(w.find('[data-choice="1"]').text()).toContain('1.80')
  })
})

describe('F13: the export filter offers the added teams and matches', () => {
  it('a team stands for its stored matches and a match for itself, sent as match numbers; players are explained', async () => {
    const f = mockFetch({
      ...SPORTS,
      'GET /api/v1/exports': page([]),
      'GET /api/v1/tournaments': page([{ id: 17, sport: 'football', category_id: 1, name: 'Premier League', slug: 'pl', followed: true }]),
      'GET /api/v1/follows': page([
        follow(),
        follow({ id: 'team:3071', kind: 'team', entity_id: 3071, name: 'Göztepe' }),
        follow({ id: 'event:9100003', kind: 'event', entity_id: 9100003, name: 'Chelsea – Liverpool' }),
        follow({ id: 'player:7', kind: 'player', entity_id: 7, name: 'Bukayo Saka' }),
      ]),
      'GET /api/v1/events': (_init?: RequestInit, url?: string) => {
        const q = new URL(String(url), 'http://x').searchParams
        expect(q.get('participant')).toBe('3071')
        return q.get('cursor') ? page([{ id: 14000003 }]) : page([{ id: 14000001 }, { id: 14000002 }], 'c2')
      },
      'POST /api/v1/jobs': { data: job({ id: 'EX1', kind: 'export', state: 'running' }) },
    })
    const { w } = await mountScreen(ExportsScreen, '/exports')
    wrappers.push(w)
    await flush()
    await w.findAll('button').find((b) => b.text().includes(t('ui.exports.new')))!.trigger('click')
    await flush()
    const box = w.find('[data-testid="export-follows"]')
    // teams and single matches, not leagues (they have their own list) and not players
    expect(box.findAll('[data-follow]').map((x) => x.attributes('data-follow'))).toEqual(['team:3071', 'event:9100003'])
    expect(w.find('[data-testid="export-players-note"]').text()).toBe(t('ui.exports.dialog.playersNote'))
    await box.find('[data-follow="team:3071"] input').setValue(true)
    await flush()
    expect(box.find('[data-testid="export-team-matches"]').text()).toBe(`· ${t('ui.exports.dialog.followMatches', { n: 3 })}`)
    await box.find('[data-follow="event:9100003"] input').setValue(true)
    await w.findAll('[role="dialog"] input.u-mono')[0].setValue('16950622')
    expect(await axeViolations(w.find('[role="dialog"]').element)).toEqual([])
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    const sent = JSON.parse(String(callsTo(f, 'POST /api/v1/jobs')[0][1]!.body))
    expect(sent.spec.profile).toBe('legacy-wide-csv')
    expect([...sent.spec.filter.event_ids].sort()).toEqual([14000001, 14000002, 14000003, 16950622, 9100003])
    expect(callsTo(f, 'GET /api/v1/events')).toHaveLength(2)
  })
})

describe('F15: Health names the running job', () => {
  it('"Download · Göztepe" with a link to the job, not its id', async () => {
    setLocale('tr')
    const running = job({ id: '01M4BVXYZ', state: 'running', spec: { follows: ['team:3071'], names: { 'team:3071': 'Göztepe' } } })
    mockFetch({ 'GET /api/v1/status': { data: status({ active_job: running }) }, 'GET /api/v1/tournaments': page([]) })
    const { w } = await mountScreen(HealthScreen, '/system/health')
    wrappers.push(w)
    await flush()
    const link = w.find('[data-testid="health-running-job"]')
    expect(link.text()).toBe(`${t('ui.job.kind.sync')} · Göztepe`)
    expect(link.attributes('href')).toBe('/jobs/01M4BVXYZ')
    expect(link.attributes('title')).toBe('01M4BVXYZ')
    expect(link.classes()).not.toContain('u-mono')
  })
})

describe('F37: scheduler intervals in words', () => {
  it('"20 dakikada bir", "günde bir"; "every 20 minutes", "once a day"; odd values as written', () => {
    setLocale('tr')
    expect(everyText('20m')).toBe('20 dakikada bir')
    expect(everyText('1d')).toBe('günde bir')
    expect(everyText('1m')).toBe('dakikada bir')
    expect(everyText('6h')).toBe('6 saatte bir')
    expect(everyText('1h')).toBe('saatte bir')
    expect(everyText('2d')).toBe('2 günde bir')
    expect(everyText('90m')).toBe('90 dakikada bir')
    expect(everyText('1.5h')).toBe('90 dakikada bir')
    expect(everyText('120m')).toBe('2 saatte bir')
    expect(everyText('30s')).toBe('30 saniyede bir')
    expect(everyText('weekly')).toBe('her weekly')
    setLocale('en')
    expect(everyText('20m')).toBe('every 20 minutes')
    expect(everyText('1d')).toBe('once a day')
    expect(everyText('1h')).toBe('every hour')
    expect(everyText('6h')).toBe('every 6 hours')
    expect(everyText('0.5s')).toBe('every 0.5s')
  })

  it('Health lists the scheduler’s tasks with the interval in words', async () => {
    setLocale('tr')
    const run = (index: number, run: string, every: string) => ({ index, run, every, cron: null, options: {}, next_run_at_utc: '2026-10-07T20:00:00Z', last_run_at_utc: null, last_job_id: null, last_result: null })
    mockFetch({
      'GET /api/v1/status': { data: status({ capabilities: { parquet: false, sse: true, scheduler: true }, schedule: { enabled: true, next_runs: [run(0, 'refresh', '20m'), run(1, 'backup', '1d')] } }) },
      'GET /api/v1/tournaments': page([]),
    })
    const { w } = await mountScreen(HealthScreen, '/system/health')
    wrappers.push(w)
    await flush()
    expect(w.findAll('[data-testid="task-every"]').map((x) => x.text())).toEqual(['20 dakikada bir', 'günde bir'])
  })
})
