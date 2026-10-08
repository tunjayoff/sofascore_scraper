import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import App from '@/App.vue'
import { routes } from '@/router'
import FollowEditorScreen from '@/screens/follows/FollowEditorScreen.vue'
import { clearSuggestCache } from '@/app/suggest'
import { resetSports } from '@/app/sports'
import { resetNames } from '@/screens/events/eventText'
import { authNeeded } from '@/lib/auth'
import { tokenInUse } from '@/app/session'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { flush, mockFetch } from './helpers'
import { axeViolations, follow, job, mountScreen, page, sport, status, useFakeES } from './v1'

/**
 * FX-26, M16 and M17: suggestions follow SofaScore's own relevance order across kinds, one list with the kind of
 * every row (the owner's choice "like the site"; grouped by kind, "sinner" showed six e-sports and football teams
 * before Jannik Sinner); follows and stored names stay on top; Ctrl K the same. A hit without a sport shows
 * none and nothing breaks.
 */
const t = i18n.global.t
let wrappers: VueWrapper[] = []
const list = <T>(data: T[]) => ({ data, page: { limit: 0, next_cursor: null } })
const hit = (kind: string, id: number, name: string, sportSlug: string | null, over: Record<string, unknown> = {}) => ({
  kind, id, name, sport: sportSlug, category: {}, country: null, team: null, followed: false, ...over,
})
// SofaScore's answer for "sinner", in its order: the tennis player first (a "team" of tennis), then the rest
const SINNER = [
  hit('team', 206570, 'Jannik Sinner', 'tennis', { country: { code: 'IT', name: 'Italy' } }),
  hit('team', 364410, 'Sinners', 'esports'),
  hit('team', 402233, 'SINNERS Esports', 'esports'),
  hit('player', 1100201, 'Benoit Sinner', null, { country: { code: 'FR', name: 'France' } }),
  hit('tournament', 2391, 'Sinner Cup', 'football'),
  hit('team', 51121, 'Sinnersdorf', 'football'),
]
const SPORTS = {
  'GET /api/v1/sports': list([sport('football'), sport('tennis'), sport('esports')]),
  'GET /api/v1/status': { data: status() },
}

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
  clearSuggestCache()
  resetSports()
  resetNames()
})
afterEach(() => {
  for (const w of wrappers) w.unmount()
  wrappers = []
  document.body.innerHTML = ''
})

describe('the editor’s suggestions in SofaScore’s order (M17)', () => {
  async function search(follows: unknown[] = []) {
    mockFetch({ ...SPORTS, 'GET /api/v1/follows': list(follows), 'GET /api/v1/catalog/suggest': list([]), 'POST /api/v1/tournaments/search': list(SINNER) })
    const { w } = await mountScreen(FollowEditorScreen, '/follows/new')
    wrappers.push(w)
    await flush()
    await w.find('[data-testid="editor-query"]').setValue('sinner')
    await w.find('[data-testid="editor-search"]').trigger('submit')
    await flush()
    return w
  }

  it('one list: Jannik Sinner first, as SofaScore ranks him; every row names its kind', async () => {
    const w = await search()
    const options = w.findAll('[role="option"]')
    expect(options.map((o) => o.attributes('data-hit'))).toEqual(['team:206570', 'team:364410', 'team:402233', 'player:1100201', 'tournament:2391', 'team:51121'])
    expect(options.map((o) => o.find('[data-testid="hit-kind"]').text())).toEqual(['Player', 'Team', 'Team', 'Player', 'League', 'Team'])
    expect(w.findAll('[role="group"]')).toHaveLength(0)
    expect(await axeViolations(w.find('[data-testid="editor-hits"]').element)).toEqual([])
  })

  it('a follow stays on top; the same entity is not shown twice', async () => {
    const w = await search([follow({ id: 'team:51121', kind: 'team', entity_id: 51121, name: 'Sinnersdorf', sport: 'football' })])
    const options = w.findAll('[role="option"]')
    expect(options[0].attributes('data-hit')).toBe('team:51121')
    expect(options[0].text()).toContain(t('ui.followEditor.alreadyFollowed'))
    expect(options.map((o) => o.attributes('data-hit')).filter((x) => x === 'team:51121')).toHaveLength(1)
    expect(options[1].attributes('data-hit')).toBe('team:206570')
  })

  it('M16: a hit without a sport shows its name, country and kind, and no sport', async () => {
    setLocale('tr')
    const w = await search()
    const benoit = w.find('[data-hit="player:1100201"]')
    expect(benoit.text()).toContain('Benoit Sinner')
    expect(benoit.text()).toContain('Oyuncu')
    expect(benoit.text()).not.toContain('undefined')
    expect(benoit.text()).not.toContain('null')
    await benoit.trigger('click')
    await flush()
    expect(w.find('[data-testid="editor-picked"]').attributes('data-hit')).toBe('player:1100201')
  })
})

describe('Ctrl K in the same order (M17)', () => {
  beforeEach(() => {
    authNeeded.value = false
    tokenInUse.value = false
    useFakeES()
  })

  it('SofaScore’s hits in its order, with the kind first in the hint', async () => {
    mockFetch({
      ...SPORTS,
      'GET /api/v1/jobs': { data: [job()], page: { limit: 20, next_cursor: null } },
      'GET /api/v1/settings': { data: { config_file: null, overrides_file: null, settings: [] } },
      'GET /api/v1/follows': page([]),
      'GET /api/v1/sinks': page([]),
      'GET /api/v1/changes': page([]),
      'GET /api/v1/tournaments': page([]),
      'POST /api/v1/tournaments/search': list(SINNER),
    })
    setActivePinia(createPinia())
    const router = createRouter({ history: createMemoryHistory(), routes })
    await router.push('/jobs')
    await router.isReady()
    const w = mount(App, { global: { plugins: [i18n, router] }, attachTo: document.body })
    wrappers.push(w)
    await flush()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }))
    await flush()
    await w.find('input[role="combobox"]').setValue('sinner')
    await vi.waitFor(() => expect(w.findAll('[data-testid="palette-sofascore"] [role="option"]')).toHaveLength(7), { timeout: 2000 })
    const options = w.findAll('[data-testid="palette-sofascore"] [role="option"]')
    expect(options.slice(0, 6).map((o) => o.attributes('data-hit'))).toEqual(['ss-team-206570', 'ss-team-364410', 'ss-team-402233', 'ss-player-1100201', 'ss-tournament-2391', 'ss-team-51121'])
    expect(options[0].text()).toContain('Player · Tennis')
  })
})
