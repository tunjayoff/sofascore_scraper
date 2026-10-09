import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import type { TeamRecord } from '@/api/v1/schema'
import FollowDetailScreen from '@/screens/follows/FollowDetailScreen.vue'
import { isIndividual, loadSports, resetSports } from '@/app/sports'
import { clearSuggestCache, shownKind } from '@/app/suggest'
import { resetNames } from '@/screens/events/eventText'
import { clearToasts } from '@/ui/toast'
import { setLocale } from '@/i18n'
import { flush, mockFetch } from './helpers'
import { follow, mountScreen, page, sport, status } from './v1'

/**
 * B1 (post-3.0, batch B): the sport registry's `individual` flag (F31), the team record (F5, F26) and the
 * participant filter of exports (F13). No request leaves the test.
 */
let wrappers: VueWrapper[] = []

const list = <T>(data: T[]) => ({ data, page: { limit: 0, next_cursor: null } })

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
  resetSports()
  resetNames()
  clearSuggestCache()
})
afterEach(() => {
  for (const w of wrappers) w.unmount()
  wrappers = []
  document.body.innerHTML = ''
})

describe('individual sports come from the registry (F31)', () => {
  it('a "team" of a sport the registry flags is shown as a player; nothing is known before the registry is read', async () => {
    mockFetch({
      'GET /api/v1/sports': list([sport('tennis'), sport('esports'), { ...sport('curling'), individual: true }]),
    })
    // before GET /sports answered: the word "team" stays
    expect(isIndividual('tennis')).toBe(false)
    await loadSports()
    expect(isIndividual('tennis')).toBe(true)
    expect(isIndividual('esports')).toBe(false)
    // a sport the UI has never heard of follows the flag, no list in the UI
    expect(isIndividual('curling')).toBe(true)
    expect(isIndividual('football')).toBe(false)
    expect(isIndividual(null)).toBe(false)
    expect(shownKind('team', 'curling')).toBe('player')
    expect(shownKind('team', 'esports')).toBe('team')
    expect(shownKind('tournament', 'tennis')).toBe('tournament')
  })
})

describe('the follow header reads the team record (F5, F26)', () => {
  const record = (over: Partial<TeamRecord> = {}): TeamRecord => ({
    id: 36460, sport: 'volleyball', type: 'team', name: 'Fenerbahçe', short_name: null, slug: 'fenerbahce', name_code: null,
    country_code: 'TR', gender: 'F', national: false, followed: true, ...over,
  })
  const routes = {
    'GET /api/v1/sports': list([sport('football'), sport('volleyball'), sport('tennis')]),
    'GET /api/v1/status': { data: status() },
    'GET /api/v1/jobs': page([]),
    'GET /api/v1/events': page([]),
    'GET /api/v1/follows/team:36460': { data: follow({ id: 'team:36460', kind: 'team', entity_id: 36460, name: 'Fenerbahçe', sport: 'volleyball' }) },
    'GET /api/v1/teams/36460': { data: record() },
    'GET /api/v1/follows/team:4700': { data: follow({ id: 'team:4700', kind: 'team', entity_id: 4700, name: 'Türkiye', sport: 'football' }) },
    'GET /api/v1/teams/4700': { data: record({ id: 4700, sport: 'football', name: 'Türkiye', gender: 'M', national: true }) },
    'GET /api/v1/follows/team:206570': { data: follow({ id: 'team:206570', kind: 'team', entity_id: 206570, name: 'Jannik Sinner', sport: null }) },
    'GET /api/v1/teams/206570': { data: record({ id: 206570, sport: 'tennis', type: 'player', name: 'Jannik Sinner', country_code: 'IT', gender: 'M' }) },
    // not stored yet: the server answers 404
    'GET /api/v1/follows/team:3052': { data: follow({ id: 'team:3052', kind: 'team', entity_id: 3052, name: 'Fenerbahçe', sport: 'football' }) },
    'GET /api/v1/teams/3052': () =>
      new Response(JSON.stringify({ error: { code: 'not_found', message: 'x', details: { team_id: 3052 }, request_id: 'r' } }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      }),
  }
  const header = async (id: number) => {
    const { w } = await mountScreen(FollowDetailScreen, `/follows/team/${id}`, '/follows/:kind/:id')
    wrappers.push(w)
    await flush()
    return w.find('[data-testid="follow-header-line"]').text()
  }

  it('gender, national team and country of the stored team, no number; a team not stored yet: sport and kind', async () => {
    const f = mockFetch(routes)
    const turkeyEn = new Intl.DisplayNames(['en'], { type: 'region' }).of('TR')
    expect(await header(36460)).toBe(`Volleyball · ${turkeyEn} · Team · Women`)
    setLocale('tr')
    expect(await header(36460)).toBe('Voleybol · Türkiye · Takım · Kadın')
    expect(await header(4700)).toBe('Futbol · Türkiye · Milli takım')
    // a tennis player SofaScore lists as a team: a player, its sport from the record
    expect(await header(206570)).toBe(`Tenis · ${new Intl.DisplayNames(['tr'], { type: 'region' }).of('IT')} · Oyuncu`)
    expect(await header(3052)).toBe('Futbol · Takım')
    for (const id of [36460, 4700, 206570, 3052]) expect(await header(id)).not.toContain(`#${id}`)
    expect(f.mock.calls.some(([u]) => String(u).includes('/api/v1/teams/36460'))).toBe(true)
  })
})
