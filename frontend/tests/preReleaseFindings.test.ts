import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import FollowDetailScreen from '@/screens/follows/FollowDetailScreen.vue'
import ExportsScreen from '@/screens/exports/ExportsScreen.vue'
import { useStatusStore } from '@/app/statusStore'
import { resetSports } from '@/app/sports'
import { resetNames } from '@/screens/events/eventText'
import { exportText } from '@/screens/jobs/jobText'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { flush, mockFetch } from './helpers'
import { axeViolations, follow, mountScreen, page, sport, status } from './v1'

/**
 * Two findings of the check before the 3.1.0 release: a player follow's empty Matches tab says what the side
 * panel says (its next download stores the player's matches), and a file `ssc export` named after a league
 * (FX-34) is not listed as "unknown". No request leaves the test.
 */
const t = i18n.global.t
let w: VueWrapper | undefined

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
  resetSports()
  resetNames()
})
afterEach(() => {
  w?.unmount()
  w = undefined
})

const list = <T>(data: T[]) => ({ data, page: { limit: 0, next_cursor: null } })

describe('a player follow not counted yet', () => {
  const counts = (counted: boolean) =>
    status({
      summary: {
        ...status().summary!,
        follows: [{ follow_id: 'player:8', kind: 'player', entity_id: 8, events: 0, finished: 0, finished_details: 0, coverage: 0, counted }] as never,
      },
    })
  const mount = async (counted: boolean) => {
    const s = counts(counted)
    mockFetch({
      'GET /api/v1/sports': list([sport('football')]),
      'GET /api/v1/sports/football': { data: sport('football') },
      'GET /api/v1/status': { data: s },
      'GET /api/v1/follows/player:8': { data: follow({ id: 'player:8', kind: 'player', entity_id: 8, name: 'Victor Osimhen', sport: 'football' }) },
      'GET /api/v1/jobs': page([]),
      'GET /api/v1/events': page([]),
    })
    ;({ w } = await mountScreen(FollowDetailScreen, '/follows/player/8', '/follows/:kind/:id'))
    useStatusStore().status = s
    await flush()
    await flush()
    return w
  }

  it.each(['en', 'tr'] as const)('the empty Matches tab says the next download stores the matches (%s)', async (lang) => {
    setLocale(lang)
    const w = await mount(false)
    expect(w.find('[data-testid="coverage-pending"]').text()).toBe(t('ui.follows.coveragePlayerPending'))
    expect(w.text()).toContain(t('ui.events.empty'))
    expect(w.text()).toContain(t('ui.followDetail.playerMatchesPending'))
    expect(w.text()).not.toContain(t('ui.events.emptyText'))
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('a counted player with no stored match keeps the usual text', async () => {
    const w = await mount(true)
    expect(w.text()).toContain(t('ui.events.empty'))
    expect(w.text()).not.toContain(t('ui.followDetail.playerMatchesPending'))
  })

  it('both languages have the text', () => {
    setLocale('tr')
    const tr = t('ui.followDetail.playerMatchesPending')
    setLocale('en')
    const en = t('ui.followDetail.playerMatchesPending')
    expect(tr).not.toBe(en)
    expect(tr).not.toBe('ui.followDetail.playerMatchesPending')
  })
})

describe('a file of ssc export named after a league', () => {
  const fileRow = (file: string, over: Record<string, unknown>) => ({
    id: `file:${file}`, job_id: null, source: 'file', state: 'succeeded', profile: null,
    filter: { sport: null, tournament_ids: [], season_ids: [], event_ids: [] }, created_at: '2026-10-09T14:25:30Z', finished_at: '2026-10-09T14:25:30Z',
    rows: null, events: null, bytes: 2048, skipped: null, file, media_type: 'application/x-ndjson', available: true, ...over,
  })

  it('reads as its format, not as "unknown"', async () => {
    mockFetch({
      'GET /api/v1/exports': page([
        fileRow('premier-league_2026-10-09_142530.jsonl', { dataset: 'unknown', schema: 'unknown', format: 'jsonl' }),
        fileRow('premier-league-raw_2026-10-09_142531.jsonl', { dataset: 'unknown', schema: 'raw', format: 'jsonl' }),
        fileRow('mine.bin', { dataset: 'unknown', schema: 'unknown', format: 'unknown' }),
      ]),
      'GET /api/v1/status': { data: status() },
    })
    ;({ w } = await mountScreen(ExportsScreen, '/exports'))
    await flush()
    const rows = w.findAll('tbody tr')
    expect(rows).toHaveLength(3)
    for (const row of rows) {
      expect(row.text()).not.toMatch(/unknown/i)
      expect(row.text()).not.toContain(t('ui.exports.dataset.unknown'))
    }
    expect(rows[0].text()).toContain('JSONL')
    expect(rows[1].text()).toContain(`${t('ui.exports.schema.raw')} · JSONL`)
  })

  it('an export job keeps its dataset', () => {
    expect(exportText({ dataset: 'events', schema: 'normalized', format: 'csv' })).toBe(
      `${t('ui.exports.schema.normalized')} · ${t('ui.exports.dataset.events')} · CSV`,
    )
    expect(exportText({ dataset: 'unknown', schema: 'unknown', format: 'jsonl' })).toBe('JSONL')
    expect(exportText({ dataset: 'unknown', schema: 'unknown', format: 'unknown' })).toBe('—')
  })
})
