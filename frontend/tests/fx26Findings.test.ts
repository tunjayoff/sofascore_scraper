import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import SettingsScreen from '@/screens/settings/SettingsScreen.vue'
import { resetSports } from '@/app/sports'
import { resetNames } from '@/screens/events/eventText'
import { authNeeded } from '@/lib/auth'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { flush, mockFetch } from './helpers'
import { axeViolations, mountScreen, setting, settingsDoc, sportWithOdds, status } from './v1'

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
