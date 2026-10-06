import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import FollowEditorScreen from '@/screens/follows/FollowEditorScreen.vue'
import SettingsScreen from '@/screens/settings/SettingsScreen.vue'
import { compress, costPerMatch, followChosen, layerFor, resolveDefaults, union } from '@/screens/follows/sliceSelection'
import type { SportSlice } from '@/api/v1/schema'
import { resetSports } from '@/app/sports'
import { resetNames } from '@/screens/events/eventText'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { callsTo, flush, mockFetch } from './helpers'
import { axeViolations, follow, job, mountScreen, setting, settingsDoc, sportWithOdds, status } from './v1'

/**
 * FX-14b, the data types as real checklists: the follow editor's "Use the defaults / Choose for this
 * follow" (`slices` null or `{include}`, P27), the edit page, and Settings › Data with the global
 * `defaults.slices` and the per-sport `slices.<sport>` changes, locks respected. No request leaves the test.
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

const list = <T>(data: T[]) => ({ data, page: { limit: 0, next_cursor: null } })
const body = (f: ReturnType<typeof mockFetch>, key: string, i = 0) => JSON.parse(String(callsTo(f, key)[i][1]!.body))

describe('the selection as the server resolves it', () => {
  const s = (key: string, group = 'core', over: Partial<SportSlice> = {}) =>
    ({ key, path: `/event/{event_id}/${key}`, required: true, default_enabled: group === 'core', selected: group === 'core', group, owner: 'event', phases: ['post'], keep_history: false, ...over }) as SportSlice
  const slices = [s('statistics'), s('lineups'), s('odds_all', 'odds'), s('standings', 'standings', { owner: 'season' })]

  it('unset defaults are the registry’s; a group selects all of it; disable wins in a layer', () => {
    expect([...resolveDefaults(slices, null)]).toEqual(['statistics', 'lineups'])
    expect([...resolveDefaults(slices, ['core', 'odds'])]).toEqual(['statistics', 'lineups', 'odds_all'])
    expect([...resolveDefaults(slices, ['core'], [{ enable: ['standings'], disable: ['lineups'] }])]).toEqual(['statistics', 'standings'])
    expect([...resolveDefaults(slices, ['core'], [{ enable: ['odds'], disable: ['odds_all'] }])]).toEqual(['statistics', 'lineups'])
  })

  it('a follow: null = the defaults, include = only these, enable/disable = changes', () => {
    expect([...followChosen(slices, null)]).toEqual(['statistics', 'lineups'])
    expect([...followChosen(slices, { include: ['statistics', 'odds'] })]).toEqual(['statistics', 'odds_all'])
    expect([...followChosen(slices, { enable: ['standings'], disable: ['lineups'] })]).toEqual(['statistics', 'standings'])
  })

  it('a fully ticked group is written by its name; per-sport changes are keys', () => {
    expect(compress(slices, new Set(['statistics', 'lineups', 'odds_all']))).toEqual(['core', 'odds_all'])
    expect(compress(slices, new Set(['statistics']))).toEqual(['statistics'])
    expect(layerFor(slices, new Set(['statistics', 'lineups']), new Set(['statistics', 'standings']))).toEqual({ enable: ['standings'], disable: ['lineups'] })
    expect(union([{ slices }, { slices: [s('statistics'), s('innings')] }]).map((x) => x.key)).toEqual(['statistics', 'lineups', 'odds_all', 'standings', 'innings'])
    expect(costPerMatch(slices, new Set(['statistics', 'standings']))).toBe(2)
  })
})

describe('the follow editor’s data step', () => {
  const routes = (over: Record<string, unknown> = {}) => ({
    'GET /api/v1/sports': list([sportWithOdds('football')]),
    'GET /api/v1/sports/football': { data: sportWithOdds('football') },
    'GET /api/v1/status': { data: status() },
    'POST /api/v1/follows': { data: follow({ slices: { include: ['statistics'] } }) },
    'POST /api/v1/jobs': { data: job({ id: 'S1', state: 'running' }) },
    ...over,
  })

  it('"Use the defaults" sends null; "Choose for this follow" a checklist that starts from the defaults, odds off and visibly so', async () => {
    const f = mockFetch(routes())
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/new?id=17&name=Premier%20League&sport=football', '/follows/new'))
    await flush()
    await w.find('[data-testid="editor-next"]').trigger('click')
    await flush()
    await w.find('[data-testid="editor-next"]').trigger('click')
    await flush()
    const picker = () => w.find('[data-testid="slice-picker"]')
    expect((picker().find('[data-testid="slice-mode-defaults"]').element as HTMLInputElement).checked).toBe(true)
    expect(picker().find('[data-testid="slice-mode"]').text()).toContain(t('ui.slicePicker.useDefaults', { sport: 'Football' }))
    expect(picker().find('[data-testid="slice-cost"]').text()).toContain(t('ui.slicePicker.cost', { n: 7 }))
    await picker().find('[data-testid="slice-mode-choose"]').setValue(true)
    await flush()
    const checklist = picker().find('[data-testid="slice-checklist"]')
    expect(checklist.exists()).toBe(true)
    expect(checklist.find('[data-testid="odds-group"] [data-testid="odds-state"]').text()).toBe(t('ui.slicePicker.offByDefault'))
    expect(checklist.findAll('[data-testid="odds-group"] input:checked')).toHaveLength(0)
    expect((checklist.find('[data-slice="statistics"] input').element as HTMLInputElement).checked).toBe(true)
    // untick one, tick an odds market: the summary and the cost follow
    await checklist.find('[data-slice="statistics"] input').setValue(false)
    await checklist.find('[data-slice="odds_featured"] input').setValue(true)
    await flush()
    expect(picker().find('[data-testid="slice-summary"]').text()).toContain(t('ui.slicePicker.oddsOn'))
    expect(picker().find('[data-testid="slice-summary"]').text()).not.toContain(t('ui.slice.statistics'))
    expect(picker().find('[data-testid="odds-state"]').text()).toBe(t('ui.slicePicker.oddsOnShort'))
    expect(picker().find('[data-testid="slice-cost"]').text()).toContain(t('ui.slicePicker.cost', { n: 7 }))
    expect(await axeViolations(w.element)).toEqual([])
    await w.find('[data-testid="editor-next"]').trigger('click')
    await flush()
    expect(w.find('[data-testid="editor-review"]').text()).toContain(t('ui.follows.data.custom'))
    await w.find('[data-testid="editor-save"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/follows').slices).toEqual({ include: ['team_streaks', 'pregame_form', 'h2h', 'lineups', 'incidents', 'odds_featured'] })
  })

  it('the edit page changes the selection, and goes back to the defaults with null', async () => {
    const f = mockFetch(
      routes({
        'GET /api/v1/follows/tournament:17': { data: follow({ slices: { include: ['statistics', 'lineups'] }, writable: ['name', 'sport', 'seasons', 'slices', 'live', 'enabled'] }) },
        'GET /api/v1/tournaments/17/seasons': list([]),
        'PATCH /api/v1/follows/tournament:17': { data: follow() },
      }),
    )
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/tournament/17/edit', '/follows/:kind/:id/edit'))
    await flush()
    const checklist = w.find('[data-testid="slice-checklist"]')
    expect((checklist.find('[data-slice="lineups"] input').element as HTMLInputElement).checked).toBe(true)
    expect((checklist.find('[data-slice="h2h"] input').element as HTMLInputElement).checked).toBe(false)
    await checklist.find('[data-slice="h2h"] input').setValue(true)
    await w.find('[data-testid="editor-form"]').trigger('submit')
    await flush()
    // in the registry's order
    expect(body(f, 'PATCH /api/v1/follows/tournament:17')).toEqual({ slices: { include: ['statistics', 'h2h', 'lineups'] } })
    w.unmount()
    const g = mockFetch(
      routes({
        'GET /api/v1/follows/tournament:17': { data: follow({ slices: { include: ['statistics'] }, writable: ['slices'] }) },
        'GET /api/v1/tournaments/17/seasons': list([]),
        'PATCH /api/v1/follows/tournament:17': { data: follow() },
      }),
    )
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/tournament/17/edit', '/follows/:kind/:id/edit'))
    await flush()
    await w.find('[data-testid="slice-mode-defaults"]').setValue(true)
    await w.find('[data-testid="editor-form"]').trigger('submit')
    await flush()
    expect(body(g, 'PATCH /api/v1/follows/tournament:17')).toEqual({ slices: null })
  })

  it('a follow that cannot hold a selection says why and offers no choice', async () => {
    mockFetch(
      routes({
        'GET /api/v1/follows/tournament:17': { data: follow({ origin: 'legacy', writable: ['sport', 'origin'] }) },
        'GET /api/v1/tournaments/17/seasons': list([]),
      }),
    )
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/tournament/17/edit', '/follows/:kind/:id/edit'))
    await flush()
    expect(w.find('[data-testid="slice-locked"]').text()).toBe(t('ui.follows.lock.legacy'))
    expect(w.find('[data-testid="slice-mode"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-testid="slice-checklist"]').exists()).toBe(false)
  })
})

describe('Settings › Data: the data types as checklists', () => {
  const sports = () => list([sportWithOdds('football'), { ...sportWithOdds('tennis'), slices: sportWithOdds('tennis').slices.filter((s) => s.key !== 'lineups') }])
  const sportRows = (over: Record<string, unknown> = {}) => [
    { sport: 'football', enable: [], disable: [], source: 'default', source_name: '', locked: false, writable: true, ...over },
    { sport: 'tennis', enable: ['odds'], disable: [], source: 'file', source_name: '/srv/sofascore.toml', locked: true, writable: false },
  ]
  async function open(defaults: ReturnType<typeof setting>, rows = sportRows()) {
    const f = mockFetch({
      'GET /api/v1/settings': { data: settingsDoc([defaults, setting('fetch.only_finished', true)], { slices: rows } as never) },
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/sports': sports(),
      'PATCH /api/v1/settings': { data: settingsDoc([defaults], { slices: rows } as never) },
    })
    ;({ w } = await mountScreen(SettingsScreen, '/settings?tab=data', '/settings'))
    await flush()
    await flush()
    return f
  }

  it('the defaults of every sport: unset = built-in ticks; unticking writes the rest, a full group by name', async () => {
    const f = await open(setting('defaults.slices', ['core']))
    const global = w.find('[data-setting="defaults.slices"]')
    expect(global.find('[data-testid="source"]').text()).toBe(t('ui.sliceDefaults.builtIn'))
    expect((global.find('[data-slice="statistics"] input').element as HTMLInputElement).checked).toBe(true)
    expect(global.find('[data-testid="odds-state"]').text()).toBe(t('ui.slicePicker.offByDefault'))
    expect(w.find('input[type="text"]').exists()).toBe(false)
    await global.find('[data-slice="odds_featured"] input').setValue(true)
    await flush()
    expect(w.find('[data-testid="save-bar"]').text()).toContain(t('ui.settings.changes', { n: 1 }))
    await w.find('[data-testid="save"]').trigger('click')
    await flush()
    expect(body(f, 'PATCH /api/v1/settings')).toEqual({ values: { 'defaults.slices': ['core', 'odds_featured'] } })
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('per sport: a tick away from the defaults is a change ({enable, disable}); a locked sport says where', async () => {
    const f = await open(setting('defaults.slices', ['core'], { source: 'overrides' }))
    const football = w.find('[data-sport="football"]')
    expect(football.text()).toContain(t('ui.sliceDefaults.asDefaults'))
    await football.find('summary').trigger('click')
    await football.find('[data-slice="lineups"] input').setValue(false)
    await football.find('[data-slice="standings"] input').setValue(true)
    await flush()
    expect(football.text()).toContain(t('ui.sliceDefaults.changes', { n: 2 }))
    const tennis = w.find('[data-sport="tennis"]')
    expect(tennis.text()).toContain('/srv/sofascore.toml')
    expect(tennis.find('[data-slice="statistics"] input').attributes('disabled')).toBeDefined()
    // tennis enables the odds group in its file: shown ticked
    expect((tennis.find('[data-slice="odds_featured"] input').element as HTMLInputElement).checked).toBe(true)
    await w.find('[data-testid="save"]').trigger('click')
    await flush()
    expect(body(f, 'PATCH /api/v1/settings')).toEqual({ values: { 'slices.football': { enable: ['standings'], disable: ['lineups'] } } })
  })

  it('a locked defaults value cannot be ticked, and says where it is set', async () => {
    await open(setting('defaults.slices', ['core', 'odds'], { source: 'env', source_name: 'SOFASCORE_DEFAULTS__SLICES', locked: true, writable: false }))
    const global = w.find('[data-setting="defaults.slices"]')
    expect(global.find('[data-testid="lock"]').text()).toContain('SOFASCORE_DEFAULTS__SLICES')
    expect(global.find('[data-slice="statistics"] input').attributes('disabled')).toBeDefined()
    expect((global.find('[data-slice="odds_featured"] input').element as HTMLInputElement).checked).toBe(true)
  })
})
