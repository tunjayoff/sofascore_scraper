import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import FollowEditorScreen from '@/screens/follows/FollowEditorScreen.vue'
import SettingsScreen from '@/screens/settings/SettingsScreen.vue'
import { resetSports } from '@/app/sports'
import { resetNames } from '@/screens/events/eventText'
import { i18n, setLocale } from '@/i18n'
import { callsTo, flush, mockFetch } from './helpers'
import { axeViolations, follow, mountScreen, setting, settingsDoc, sport, sportWithOdds, status } from './v1'

/**
 * FX-20, the small gaps FX-14b and FX-19 left: live watching is not offered for a player (`ssc watch` skips
 * player follows); Settings › Data shows the followed sports first and the rest under "Other sports".
 */
const t = i18n.global.t
let w: VueWrapper

const list = <T>(data: T[]) => ({ data, page: { limit: 0, next_cursor: null } })
const body = (f: ReturnType<typeof mockFetch>, key: string, i = 0) => JSON.parse(String(callsTo(f, key)[i][1]!.body))

beforeEach(() => {
  setLocale('en')
  resetSports()
  resetNames()
})
afterEach(() => w?.unmount())
describe('live watching and players', () => {
  const routes = (over: Record<string, unknown> = {}) => ({
    'GET /api/v1/sports': list([sport('football')]),
    'GET /api/v1/sports/football': { data: sport('football') },
    'GET /api/v1/status': { data: status() },
    ...over,
  })

  it('a new player follow is not offered live watching and is added without it; a team is', async () => {
    const f = mockFetch(routes({ 'POST /api/v1/follows': { data: follow({ id: 'player:7', kind: 'player', entity_id: 7, name: 'Bukayo Saka' }) } }))
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/new?kind=player&id=7&name=Bukayo%20Saka&sport=football', '/follows/new'))
    await flush()
    for (let i = 0; i < 3; i++) {
      await w.find('[data-testid="editor-next"]').trigger('click')
      await flush()
    }
    expect(w.find('[data-testid="editor-live"]').exists()).toBe(false)
    expect(w.find('[data-testid="editor-no-live"]').text()).toBe(t('ui.followEditor.liveNoPlayer'))
    expect(await axeViolations(w.find('[data-testid="editor-step"]').element)).toEqual([])
    await w.find('[data-testid="editor-sync-after"]').setValue(false)
    await w.find('[data-testid="editor-save"]').trigger('click')
    await flush()
    expect(body(f, 'POST /api/v1/follows').live).toBe(false)
    w.unmount()
    mockFetch(routes())
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/new?kind=team&id=42&name=Arsenal&sport=football', '/follows/new'))
    await flush()
    for (let i = 0; i < 3; i++) {
      await w.find('[data-testid="editor-next"]').trigger('click')
      await flush()
    }
    expect(w.find('[data-testid="editor-live"]').exists()).toBe(true)
  })

  it('a player follow’s edit page hides it, unless it is on (then it can be turned off)', async () => {
    const saka = follow({ id: 'player:7', kind: 'player', entity_id: 7, name: 'Bukayo Saka', writable: ['name', 'sport', 'seasons', 'slices', 'live', 'enabled'] })
    mockFetch(routes({ 'GET /api/v1/follows/player:7': { data: saka } }))
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/player/7/edit', '/follows/:kind/:id/edit'))
    await flush()
    expect(w.find('[data-testid="edit-live"]').exists()).toBe(false)
    expect(w.find('[data-testid="edit-no-live"]').exists()).toBe(true)
    w.unmount()
    const f = mockFetch(routes({ 'GET /api/v1/follows/player:7': { data: { ...saka, live: true } }, 'PATCH /api/v1/follows/player:7': { data: saka } }))
    ;({ w } = await mountScreen(FollowEditorScreen, '/follows/player/7/edit', '/follows/:kind/:id/edit'))
    await flush()
    await w.find('[data-testid="edit-live"]').setValue(false)
    await w.find('[data-testid="edit-save"]').trigger('click')
    await flush()
    expect(body(f, 'PATCH /api/v1/follows/player:7')).toEqual({ live: false })
  })
})

describe('Settings', () => {
  const sportRows = [
    { sport: 'football', enable: [], disable: [], source: 'default', source_name: '', locked: false, writable: true },
    { sport: 'basketball', enable: [], disable: [], source: 'default', source_name: '', locked: false, writable: true },
    { sport: 'tennis', enable: [], disable: [], source: 'default', source_name: '', locked: false, writable: true },
    { sport: 'darts', enable: ['standings'], disable: [], source: 'overrides', source_name: '', locked: false, writable: true },
  ]
  async function open(follows: unknown[], rows = sportRows) {
    const settings = [setting('defaults.slices', ['core']), setting('fetch.only_finished', true), setting('fetch.save_empty_rounds', false)]
    const f = mockFetch({
      'GET /api/v1/settings': { data: settingsDoc(settings, { slices: rows } as never) },
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/sports': list(['football', 'basketball', 'tennis', 'darts'].map((s) => sportWithOdds(s))),
      'GET /api/v1/follows': list(follows),
    })
    ;({ w } = await mountScreen(SettingsScreen, '/settings?tab=data', '/settings'))
    await flush()
    await flush()
    return f
  }

  it('Data: the followed sports (and one with its own saved change) first, the others closed under "Other sports"', async () => {
    await open([follow({ sport: 'football' }), follow({ id: 'team:3', kind: 'team', entity_id: 3, name: 'Fenerbahçe Beko', sport: 'basketball' })])
    const mine = w.find('[data-testid="slice-sports-mine"]')
    expect(mine.findAll('[data-sport]').map((d) => d.attributes('data-sport'))).toEqual(['football', 'basketball', 'darts'])
    const others = w.find('[data-testid="slice-sports-others"]')
    expect(others.element.tagName).toBe('DETAILS')
    expect((others.element as HTMLDetailsElement).open).toBe(false)
    expect(others.find('summary').text()).toContain(t('ui.sliceDefaults.otherSports'))
    expect(others.find('summary').text()).toContain(t('ui.sliceDefaults.sportCount', { n: 1 }))
    expect(others.findAll('[data-sport]').map((d) => d.attributes('data-sport'))).toEqual(['tennis'])
    expect(await axeViolations(w.find('[data-testid="slice-defaults"]').element)).toEqual([])
  })

  it('Data: without follows or changes every sport is under "All sports", closed', async () => {
    await open([], sportRows.slice(0, 3))
    expect(w.find('[data-testid="slice-sports-mine"]').exists()).toBe(false)
    const others = w.find('[data-testid="slice-sports-others"]')
    expect(others.find('summary').text()).toContain(t('ui.sliceDefaults.allSports'))
    expect(others.findAll('[data-sport]')).toHaveLength(4)
  })
})
