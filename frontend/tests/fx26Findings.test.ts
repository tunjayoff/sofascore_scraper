import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import SettingsScreen from '@/screens/settings/SettingsScreen.vue'
import EventDetailScreen from '@/screens/events/EventDetailScreen.vue'
import FollowEditorScreen from '@/screens/follows/FollowEditorScreen.vue'
import { followName } from '@/screens/follows/followText'
import { resetSports } from '@/app/sports'
import { resetNames } from '@/screens/events/eventText'
import { authNeeded } from '@/lib/auth'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { flush, mockFetch } from './helpers'
import { axeViolations, event, mountScreen, setting, settingsDoc, sportWithOdds, status } from './v1'

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
