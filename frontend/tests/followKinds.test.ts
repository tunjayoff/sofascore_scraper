import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import FollowEditorScreen from '@/screens/follows/FollowEditorScreen.vue'
import FollowDetailScreen from '@/screens/follows/FollowDetailScreen.vue'
import FollowsScreen from '@/screens/follows/FollowsScreen.vue'
import FollowActions from '@/screens/follows/FollowActions.vue'
import EventDetailScreen from '@/screens/events/EventDetailScreen.vue'
import { resetSports } from '@/app/sports'
import { resetNames } from '@/screens/events/eventText'
import { followNames } from '@/screens/jobs/jobText'
import { clearToasts, uiToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { pct } from '@/ui/time'
import { callsTo, flush, mockFetch } from './helpers'
import { axeViolations, event, follow, job, mountScreen, page, sport, status, v1Error } from './v1'

/**
 * FX-14b, every kind of follow: teams and players found by name (`kinds`, FX-19), a single match from its
 * page, the season list of a league read before its first download (FX-13 `only: "seasons"`), a follow's
 * page with per-season counts and its jobs by target, removing a league together with its data, and
 * moving a follow of the old league list here. No request leaves the test.
 */
const t = i18n.global.t
let w: VueWrapper

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
  resetSports()
  resetNames()
  followNames.value = new Map()
})
afterEach(() => {
  w?.unmount()
  vi.useRealTimers()
})

const list = <T>(data: T[]) => ({ data, page: { limit: 0, next_cursor: null } })
const body = (f: ReturnType<typeof mockFetch>, key: string, i = 0) => JSON.parse(String(callsTo(f, key)[i][1]!.body))
const SPORTS = { 'GET /api/v1/sports': list([sport('football')]), 'GET /api/v1/sports/football': { data: sport('football') }, 'GET /api/v1/status': { data: status() } }

describe('teams and players by name', () => {
  const arsenal = { kind: 'team', id: 42, name: 'Arsenal', slug: 'arsenal', sport: 'football', category: { country_code: 'EN' }, country: { code: 'EN', name: 'England' }, team: null, followed: false }
  const saka = { kind: 'player', id: 7, name: 'Bukayo Saka', slug: 'saka', sport: 'football', category: { country_code: 'EN' }, country: { code: 'EN', name: 'England' }, team: { id: 42, name: 'Arsenal' }, followed: true }

  it('a team: one search of every kind; the hit shows its kind, sport and country and brings kind and sport; Add downloads it', async () => {
    const f = mockFetch({
      ...SPORTS,
      'POST /api/v1/tournaments/search': list([arsenal]),
      'POST /api/v1/follows': { data: follow({ id: 'team:42', kind: 'team', entity_id: 42, name: 'Arsenal' }) },
      'POST /api/v1/jobs': { data: job({ id: 'T1', state: 'running', spec: { follows: ['team:42'] } }) },
    })
    let router
    ;({ w, router } = await mountScreen(FollowEditorScreen, '/follows/new'))
    await flush()
    expect(w.find('[data-testid="editor-query"]').attributes('placeholder')).toBe(t('ui.suggest.placeholder'))
    await w.find('[data-testid="editor-query"]').setValue('arsenal')
    await w.find('[data-testid="editor-search"]').trigger('submit')
    await flush()
    expect(callsTo(f, 'POST /api/v1/tournaments/search')).toHaveLength(1)
    expect(body(f, 'POST /api/v1/tournaments/search')).toEqual({ q: 'arsenal', sport: null, kinds: ['tournament', 'team', 'player'] })
    expect(w.find('[data-hit="team:42"][data-hit-kind="team"]').exists()).toBe(true)
    // the one hit is chosen: its kind, number, name and sport come along
    const picked = w.find('[data-testid="editor-picked"]')
    expect(picked.attributes('data-hit')).toBe('team:42')
    expect(picked.text()).toContain(t('ui.follows.kind.team'))
    expect(picked.text()).toContain('England')
    expect(picked.text()).toContain(t('sport.football'))
    expect((w.find('[data-kind="team"] input').element as HTMLInputElement).checked).toBe(true)
    expect((w.find('[data-testid="editor-id"]').element as HTMLInputElement).value).toBe('42')
    expect((w.find('[data-testid="editor-sport"]').element as HTMLSelectElement).value).toBe('football')
    expect(await axeViolations(w.element)).toEqual([])
    await w.find('[data-testid="editor-next"]').trigger('click')
    await flush()
    // a team's seasons are a time window (FX-19); no season list to choose from
    expect(w.find('[data-testid="editor-window-note"]').text()).toBe(t('ui.followEditor.windowNote.team'))
    expect(w.find('input[value="choose"]').exists()).toBe(false)
    await w.find('input[value="last"]').setValue(true)
    await w.find('[data-testid="editor-next"]').trigger('click')
    await flush()
    await w.find('[data-testid="editor-next"]').trigger('click')
    await flush()
    expect(w.find('[data-testid="editor-review"]').text()).toContain(t('ui.follows.window.last', { n: 2 }))
    await w.find('[data-testid="editor-save"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/follows')).toEqual({ kind: 'team', entity_id: 42, name: 'Arsenal', sport: 'football', seasons: 'last:2', slices: null, live: false })
    expect(body(f, 'POST /api/v1/jobs')).toEqual({ kind: 'sync', spec: { follows: ['team:42'] } })
    expect(router.currentRoute.value.path).toBe('/follows/team/42')
  })

  it('a player: the hit shows the player’s team and “already added”; nothing is chosen for the user', async () => {
    const f = mockFetch({ ...SPORTS, 'POST /api/v1/tournaments/search': list([saka]) })
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/new'))
    await flush()
    await w.find('[data-testid="editor-query"]').setValue('saka')
    await w.find('[data-testid="editor-search"]').trigger('submit')
    await flush()
    expect(body(f, 'POST /api/v1/tournaments/search').kinds).toEqual(['tournament', 'team', 'player'])
    const hit = w.find('[data-hit="player:7"][data-hit-kind="player"]')
    expect(hit.find('[data-testid="hit-team"]').text()).toBe(t('ui.followEditor.playsFor', { team: 'Arsenal' }))
    expect(hit.text()).toContain(t('ui.followEditor.alreadyFollowed'))
    expect(w.find('[data-testid="editor-picked"]').exists()).toBe(false)
  })

  it('a single match has no search and no seasons; it is added by its number', async () => {
    const f = mockFetch({ ...SPORTS, 'POST /api/v1/follows': { data: follow({ id: 'event:9100003', kind: 'event', entity_id: 9100003, name: 'Chelsea – Liverpool' }) }, 'POST /api/v1/jobs': { data: job({ id: 'E1', state: 'running' }) } })
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/new?kind=event&id=9100003&name=Chelsea%20%E2%80%93%20Liverpool&sport=football', '/follows/new'))
    await flush()
    expect(w.find('[data-testid="editor-query"]').exists()).toBe(false)
    expect(w.find('[data-testid="editor-event-note"]').exists()).toBe(true)
    await w.find('[data-testid="editor-next"]').trigger('click')
    await flush()
    expect(w.find('[data-testid="editor-one-match"]').text()).toBe(t('ui.followEditor.oneMatch'))
    await w.find('[data-testid="editor-next"]').trigger('click')
    await w.find('[data-testid="editor-next"]').trigger('click')
    await flush()
    await w.find('[data-testid="editor-save"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/follows')).toMatchObject({ kind: 'event', entity_id: 9100003, name: 'Chelsea – Liverpool', seasons: 'current' })
    expect(body(f, 'POST /api/v1/jobs')).toEqual({ kind: 'sync', spec: { follows: ['event:9100003'] } })
  })
})

describe('seasons before the first download', () => {
  it('a league without a season list: the list is read on a click (one season-list job), then its seasons are ticked by name', async () => {
    let read = false
    const f = mockFetch({
      ...SPORTS,
      'GET /api/v1/tournaments/52/seasons': () =>
        list(read ? [{ id: 77805, tournament_id: 52, name: 'Süper Lig 25/26', year: '25/26' }, { id: 63814, tournament_id: 52, name: 'Süper Lig 24/25', year: '24/25' }] : []),
      'POST /api/v1/jobs': () => {
        read = true
        return { data: job({ id: 'SL1', state: 'running', spec: { mode: 'seasons', league_id: 52, selections: [] }, finished_at: null }) }
      },
      'GET /api/v1/jobs/SL1': { data: job({ id: 'SL1', state: 'succeeded', spec: { mode: 'seasons', league_id: 52, selections: [] } }) },
      'POST /api/v1/follows': { data: follow({ id: 'tournament:52', entity_id: 52, name: 'Süper Lig' }) },
    })
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/new?id=52&name=S%C3%BCper%20Lig&sport=football', '/follows/new'))
    await flush()
    await w.find('[data-testid="editor-next"]').trigger('click')
    await flush()
    await w.find('input[value="choose"]').setValue(true)
    await flush()
    expect(w.find('[data-testid="season-list-missing"]').text()).toBe(t('ui.seasonChooser.missing'))
    // nothing is asked of SofaScore until the click
    expect(callsTo(f, 'POST /api/v1/jobs')).toHaveLength(0)
    expect(w.find('[data-testid="editor-next"]').attributes('disabled')).toBeDefined()
    await w.find('[data-testid="season-list-get"]').trigger('click')
    await flush()
    await flush()
    expect(body(f, 'POST /api/v1/jobs')).toEqual({ kind: 'sync', spec: { league_id: 52, only: 'seasons' } })
    expect(uiToasts.value.at(-1)?.text).toBe(t('ui.jobs.started', { kind: t('ui.job.kind.seasonList') }))
    const seasons = w.findAll('[data-testid="editor-season"]')
    expect(seasons.map((s) => s.text())).toEqual([expect.stringContaining('Süper Lig 25/26'), expect.stringContaining('Süper Lig 24/25')])
    await seasons[1].find('input').setValue(true)
    expect(await axeViolations(w.element)).toEqual([])
    await w.find('[data-testid="editor-next"]').trigger('click')
    await w.find('[data-testid="editor-next"]').trigger('click')
    await flush()
    expect(w.find('[data-testid="editor-review"]').text()).toContain(t('ui.follows.seasons.chosen', { n: 1 }))
    await w.find('[data-testid="editor-sync-after"]').setValue(false)
    await w.find('[data-testid="editor-save"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/follows').seasons).toEqual([63814])
  })

  it('a season list that cannot be read says so and links the job', async () => {
    mockFetch({
      ...SPORTS,
      'GET /api/v1/tournaments/52/seasons': list([]),
      'POST /api/v1/jobs': { data: job({ id: 'SL2', state: 'running', spec: { mode: 'seasons', league_id: 52, selections: [] } }) },
      'GET /api/v1/jobs/SL2': { data: job({ id: 'SL2', state: 'partial', spec: { mode: 'seasons', league_id: 52, selections: [] } }) },
    })
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/new?id=52&name=X&sport=football', '/follows/new'))
    await flush()
    await w.find('[data-testid="editor-next"]').trigger('click')
    await w.find('input[value="choose"]').setValue(true)
    await flush()
    await w.find('[data-testid="season-list-get"]').trigger('click')
    await flush()
    await flush()
    const failed = w.find('[data-testid="season-list-failed"]')
    expect(failed.text()).toContain(t('ui.seasonChooser.failed'))
    expect(failed.find('a').attributes('href')).toBe('/jobs/SL2')
  })
})

describe('a follow’s page', () => {
  it('a league: each season with its counts (include=counts) and the age of its schedule', async () => {
    const f = mockFetch({
      ...SPORTS,
      'GET /api/v1/follows/tournament:17': { data: follow({ seasons: 'all' }) },
      'GET /api/v1/tournaments/17': { data: { id: 17, sport: 'football', category_id: 1, name: 'Premier League', slug: 'pl' } },
      'GET /api/v1/tournaments/17/seasons': list([
        { id: 61627, tournament_id: 17, name: 'PL 24/25', year: '24/25', counts: { events: 380, finished: 370, details: 360, complete: 350, completion_rate: 97.22, missing: {}, schedule_fetched_at_utc: '2026-10-02T08:00:00Z' } },
        { id: 52186, tournament_id: 17, name: 'PL 23/24', year: '23/24', counts: { events: 0, finished: 0, details: 0, complete: 0, completion_rate: 0, missing: {}, schedule_fetched_at_utc: null } },
      ]),
      'GET /api/v1/jobs': page([]),
    })
    ;({ w } = await mountScreen(FollowDetailScreen, '/follows/tournament/17', '/follows/:kind/:id'))
    await flush()
    expect(String(callsTo(f, 'GET /api/v1/tournaments/17/seasons')[0][0])).toContain('include=counts')
    const counts = w.findAll('[data-testid="season-counts"]')
    expect(counts[0].text()).toContain(t('ui.followDetail.seasonCounts', { events: '380', finished: '370', details: '360' }))
    expect(counts[0].text()).toContain(t('ui.followDetail.complete', { pct: pct(97.22) }))
    expect(counts[1].text()).toContain(t('ui.followDetail.scheduleNever'))
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('a team: its stored matches by participant, its jobs by target, no seasons tab', async () => {
    const f = mockFetch({
      ...SPORTS,
      'GET /api/v1/follows/team:42': { data: follow({ id: 'team:42', kind: 'team', entity_id: 42, name: 'Arsenal', seasons: 'last:2' }) },
      'GET /api/v1/jobs': (_: unknown, url?: string) =>
        String(url).includes('target=') ? page([job({ id: 'T1', spec: { follows: ['team:42'] } })]) : page([job({ id: 'T1', spec: { follows: ['team:42'] } }), job({ id: 'X', spec: { follows: ['team:7'] } })]),
      'GET /api/v1/events': page([event()]),
      'GET /api/v1/tournaments': page([]),
    })
    let router
    ;({ w, router } = await mountScreen(FollowDetailScreen, '/follows/team/42', '/follows/:kind/:id'))
    await flush()
    expect(w.findAll('[role="tab"]').map((x) => x.text())).toEqual([t('ui.followDetail.tab.events'), t('ui.followDetail.tab.data'), t('ui.followDetail.tab.jobs')])
    expect(new URL(String(callsTo(f, 'GET /api/v1/events').at(-1)![0]), 'http://x').searchParams.get('participant')).toBe('42')
    expect(w.find('[data-testid="follow-facts"]').text()).toContain(t('ui.follows.window.last', { n: 2 }))
    await router.push('/follows/team/42?tab=jobs')
    await flush()
    expect(callsTo(f, 'GET /api/v1/jobs').some(([u]) => String(u).includes('target=team%3A42'))).toBe(true)
    expect(w.findAll('[data-testid="follow-jobs"] li').map((li) => li.attributes('data-job'))).toEqual(['T1'])
    expect(w.find('[data-testid="follow-jobs"]').text()).toContain('Arsenal')
  })

  it('a single match links to it', async () => {
    mockFetch({ ...SPORTS, 'GET /api/v1/follows/event:9100003': { data: follow({ id: 'event:9100003', kind: 'event', entity_id: 9100003, name: 'Chelsea – Liverpool' }) }, 'GET /api/v1/jobs': page([]) })
    ;({ w } = await mountScreen(FollowDetailScreen, '/follows/event/9100003', '/follows/:kind/:id'))
    await flush()
    expect(w.find('[data-testid="follow-open-match"]').attributes('href')).toBe('/events/9100003')
  })
})

describe('removing a league with its data, and the old league list', () => {
  const mountActions = (f: ReturnType<typeof follow>) => {
    setActivePinia(createPinia())
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:p(.*)*', component: { template: '<div />' } }] })
    w = mount(FollowActions, { props: { follow: f }, global: { plugins: [i18n, router] }, attachTo: document.body })
  }
  const dialog = () => document.querySelector('[role="alertdialog"]') as HTMLElement

  it('"Also delete its stored matches" asks for the typed word, sends delete_data=true and links the clear job', async () => {
    const f = mockFetch({
      'GET /api/v1/status': { data: status() },
      'DELETE /api/v1/follows/tournament:17': { data: { ...follow(), clear_job: job({ id: 'C1', kind: 'clear', state: 'running', spec: { scope: 'all', tournament_id: 17, confirm: true } }) } },
    })
    mountActions(follow())
    await w.find('button[aria-haspopup="menu"]').trigger('click')
    await w.find('[data-key="remove"]').trigger('click')
    await flush()
    expect(dialog().textContent).toContain(t('ui.follows.removeText'))
    const box = dialog().querySelector('[data-testid="remove-delete-data"]') as HTMLInputElement
    box.click()
    await flush()
    expect(dialog().textContent).toContain(t('ui.follows.removeDataText'))
    const confirm = dialog().querySelector('[data-testid="confirm"]') as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    const word = dialog().querySelector('input[spellcheck="false"]') as HTMLInputElement
    word.value = t('ui.maintenance.clear.word')
    word.dispatchEvent(new Event('input'))
    await flush()
    expect(confirm.disabled).toBe(false)
    expect(await axeViolations(dialog())).toEqual([])
    confirm.click()
    await flush()
    expect(String(callsTo(f, 'DELETE /api/v1/follows/tournament:17')[0][0])).toContain('delete_data=true')
    expect(uiToasts.value.at(-1)?.text).toBe(t('ui.follows.removedWithData', { name: 'Premier League' }))
    expect(uiToasts.value.at(-1)?.link?.to).toBe('/jobs/C1')
    expect(w.emitted('removed')).toHaveLength(1)
  })

  it('a dialog opened from a table row wraps its text and reads left to right (the row-actions cell is nowrap, right-aligned)', async () => {
    const { readFileSync } = await import('node:fs')
    const css = readFileSync(`${process.cwd()}/src/ui/UiDialog.vue`, 'utf8').match(/\.u-overlay \{[^}]*\}/)![0]
    expect(css).toContain('white-space: normal')
    expect(css).toContain('text-align: start')
  })

  it('a team follow has no data to delete with it; a plain remove sends no delete_data', async () => {
    const f = mockFetch({ 'GET /api/v1/status': { data: status() }, 'DELETE /api/v1/follows/team:42': { data: { ...follow({ id: 'team:42', kind: 'team', entity_id: 42 }), clear_job: null } } })
    mountActions(follow({ id: 'team:42', kind: 'team', entity_id: 42, name: 'Arsenal' }))
    await w.find('button[aria-haspopup="menu"]').trigger('click')
    await w.find('[data-key="remove"]').trigger('click')
    await flush()
    expect(dialog().querySelector('[data-testid="remove-delete-data"]')).toBeNull()
    ;(dialog().querySelector('[data-testid="confirm"]') as HTMLButtonElement).click()
    await flush()
    expect(String(callsTo(f, 'DELETE /api/v1/follows/team:42')[0][0])).not.toContain('delete_data')
  })

  it('a follow of the old league list: "Move to here" sends origin api, and every field is editable afterwards', async () => {
    const legacy = follow({ origin: 'legacy', writable: ['sport', 'origin'] })
    const f = mockFetch({
      ...SPORTS,
      'GET /api/v1/follows': list([legacy]),
      'GET /api/v1/jobs': page([]),
      'PATCH /api/v1/follows/tournament:17': { data: follow({ origin: 'api' }) },
      'GET /api/v1/follows/tournament:17': { data: legacy },
      'GET /api/v1/tournaments/17/seasons': list([]),
    })
    ;({ w } = await mountScreen(FollowsScreen, '/follows'))
    await flush()
    await w.findAll('tbody tr')[0].find('button[aria-haspopup="menu"]').trigger('click')
    expect(w.find('[data-key="move"]').text()).toContain(t('ui.follows.move.button'))
    w.unmount()
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/tournament/17/edit', '/follows/:kind/:id/edit'))
    await flush()
    expect(w.find('[data-testid="edit-name"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-testid="follow-move"]').text()).toContain(t('ui.follows.move.text'))
    await w.find('[data-testid="follow-move-button"]').trigger('click')
    await flush()
    expect(body(f, 'PATCH /api/v1/follows/tournament:17')).toEqual({ origin: 'api' })
    expect(w.find('[data-testid="follow-move"]').exists()).toBe(false)
    expect(w.find('[data-testid="edit-name"]').attributes('disabled')).toBeUndefined()
    expect(uiToasts.value.at(-1)?.text).toBe(t('ui.follows.move.done', { name: 'Premier League' }))
  })
})

describe('the match page', () => {
  const routes = (over: Record<string, unknown> = {}) => ({
    ...SPORTS,
    'GET /api/v1/events/9100003': { data: event() },
    'GET /api/v1/events/9100003/slices': list([]),
    'GET /api/v1/events/9100003/odds': list([]),
    'GET /api/v1/tournaments': page([]),
    'GET /api/v1/tournaments/17/seasons': list([]),
    ...over,
  })

  it('"Follow this match" and "Follow <team>" open the editor filled in', async () => {
    mockFetch(routes())
    let router
    ;({ w, router } = await mountScreen(EventDetailScreen, '/events/9100003', '/events/:id'))
    await flush()
    await w.find('button[aria-haspopup="menu"]').trigger('click')
    const items = w.findAll('[role="menuitem"]').map((m) => m.text())
    expect(items).toEqual(expect.arrayContaining([t('ui.eventDetail.followIt'), t('ui.eventDetail.followTeam', { name: 'Chelsea' }), t('ui.eventDetail.followTeam', { name: 'Liverpool' })]))
    await w.find('[data-key="follow-home"]').trigger('click')
    await flush()
    expect(router.currentRoute.value.path).toBe('/follows/new')
    expect(router.currentRoute.value.query).toEqual({ kind: 'team', id: '38', name: 'Chelsea', sport: 'football' })
  })

  it('a match that is not stored can be fetched by its number (event_ids)', async () => {
    const f = mockFetch(
      routes({
        'GET /api/v1/events/9100003': () => v1Error(404, 'not_found'),
        'POST /api/v1/jobs': { data: job({ id: 'F1', kind: 'fetch', state: 'running' }) },
        'GET /api/v1/jobs/F1': { data: job({ id: 'F1', kind: 'fetch', state: 'running' }) },
      }),
    )
    ;({ w } = await mountScreen(EventDetailScreen, '/events/9100003', '/events/:id'))
    await flush()
    await w.find('[data-testid="fetch-unknown"]').trigger('click')
    await flush()
    ;(document.querySelector('[data-testid="confirm"]') as HTMLButtonElement).click()
    await flush()
    expect(body(f, 'POST /api/v1/jobs')).toEqual({ kind: 'fetch', spec: { event_ids: [9100003] } })
  })
})
