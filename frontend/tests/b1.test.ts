import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import { isIndividual, loadSports, resetSports } from '@/app/sports'
import { clearSuggestCache, shownKind } from '@/app/suggest'
import { resetNames } from '@/screens/events/eventText'
import { clearToasts } from '@/ui/toast'
import { setLocale } from '@/i18n'
import { mockFetch } from './helpers'
import { sport } from './v1'

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
