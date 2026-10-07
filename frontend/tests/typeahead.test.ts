import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import FollowEditorScreen from '@/screens/follows/FollowEditorScreen.vue'
import { resetSports } from '@/app/sports'
import { REMOTE_DELAY } from '@/app/suggest'
import { hitPlace } from '@/screens/follows/followText'
import { i18n, setLocale } from '@/i18n'
import { callsTo, flush, json, mockFetch } from './helpers'
import { axeViolations, follow, mountScreen, sport, status } from './v1'

/**
 * FX-20, suggestions while typing in the follow editor (the quick search's SofaScore section is in
 * newcomer.test.ts): follows and stored names at once, SofaScore from 2 characters after a pause of 350 ms,
 * a newer keystroke aborting the older request, the answers kept for the page, one list grouped by kind,
 * and the keys of a combobox. Fake timers; no request leaves the test.
 */
const t = i18n.global.t
let w: VueWrapper

const list = <T>(data: T[]) => ({ data, page: { limit: 0, next_cursor: null } })
const hit = (kind: 'tournament' | 'team' | 'player', id: number, name: string, extra: Record<string, unknown> = {}) => ({
  kind,
  id,
  name,
  sport: 'football',
  category: kind === 'tournament' ? { id: 32, name: 'Spain', country_code: 'ES' } : {},
  country: { code: 'ES', name: 'Spain' },
  team: null,
  followed: false,
  ...extra,
})
/** What the fake SofaScore search finds for a text. */
const FOUND: Record<string, unknown[]> = {
  la: [hit('tournament', 8, 'LaLiga'), hit('tournament', 34, 'Ligue 1', { category: { name: 'France' }, country: { code: 'FR', name: 'France' } }), hit('team', 2699, 'Lazio', { country: { code: 'IT', name: 'Italy' } }), hit('player', 1402912, 'Lamine Yamal', { team: { id: 2817, name: 'Barcelona' } })],
}

type Search = { q: string; signal: AbortSignal | null | undefined }
let searches: Search[] = []
let answerAfter = 0

function routes(over: Record<string, unknown> = {}) {
  searches = []
  answerAfter = 0
  return mockFetch({
    'GET /api/v1/sports': list([sport('football'), sport('basketball')]),
    'GET /api/v1/status': { data: status() },
    'GET /api/v1/follows': list([follow({ id: 'tournament:8', entity_id: 8, name: 'LaLiga' })]),
    'GET /api/v1/catalog/suggest': (_init?: RequestInit, url?: string) => {
      const q = new URL(String(url), 'http://x').searchParams.get('q') ?? ''
      return list(q.startsWith('l') ? [hit('team', 2817, 'Barcelona', { name: 'Las Palmas', id: 6577, followed: false })] : [])
    },
    'POST /api/v1/tournaments/search': (init?: RequestInit) => {
      const q = JSON.parse(String(init!.body)).q as string
      searches.push({ q, signal: init!.signal })
      const body = list(FOUND[q.toLowerCase()] ?? [])
      if (!answerAfter) return body
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => resolve(json(body)), answerAfter)
        init!.signal?.addEventListener('abort', () => {
          clearTimeout(timer)
          reject(new DOMException('aborted', 'AbortError'))
        })
      })
    },
    ...over,
  })
}

async function open() {
  ;({ w } = await mountScreen(FollowEditorScreen, '/follows/new'))
  await flush()
  vi.useFakeTimers()
  return w.find('[data-testid="editor-query"]')
}

/** Types the text letter by letter, `gap` ms between two letters, then waits `after` ms. */
async function type(input: ReturnType<VueWrapper['find']>, text: string, gap: number, after = 1000) {
  for (let i = 1; i <= text.length; i++) {
    await input.setValue(text.slice(0, i))
    await vi.advanceTimersByTimeAsync(i < text.length ? gap : after)
  }
}

beforeEach(() => {
  setLocale('en')
  resetSports()
})
afterEach(() => {
  w?.unmount()
  vi.useRealTimers()
})

describe('suggestions while typing', () => {
  it('one letter: follows and stored names at once, no request to SofaScore', async () => {
    const f = routes()
    const input = await open()
    await input.setValue('l')
    await vi.advanceTimersByTimeAsync(1)
    // the follow is there at once (read once for the page), before any pause
    expect(w.find('[data-group="tournament"] [data-hit="tournament:8"]').exists()).toBe(true)
    expect(w.find('[data-hit="tournament:8"]').text()).toContain(t('ui.followEditor.alreadyFollowed'))
    await vi.advanceTimersByTimeAsync(2000)
    expect(callsTo(f, 'POST /api/v1/tournaments/search')).toHaveLength(0)
    expect(callsTo(f, 'GET /api/v1/follows')).toHaveLength(1)
    expect(callsTo(f, 'GET /api/v1/catalog/suggest')).toHaveLength(1)
    // a stored team, marked as such
    expect(w.find('[data-group="team"] [data-hit="team:6577"]').text()).toContain(t('ui.suggest.stored'))
    expect(w.find('[data-testid="suggest-status"]').text()).toBe(t('ui.suggest.typeMore', { n: 2 }))
  })

  it('"la": SofaScore after the pause; leagues, teams and players in one list with sport, country and team', async () => {
    const f = routes()
    const input = await open()
    await input.setValue('la')
    await vi.advanceTimersByTimeAsync(REMOTE_DELAY - 50)
    expect(searches).toHaveLength(0)
    expect(w.find('[data-testid="suggest-status"]').text()).toBe(t('ui.suggest.searching'))
    await vi.advanceTimersByTimeAsync(100)
    expect(searches.map((s) => s.q)).toEqual(['la'])
    expect(JSON.parse(String(callsTo(f, 'POST /api/v1/tournaments/search')[0][1]!.body))).toEqual({ q: 'la', sport: null, kinds: ['tournament', 'team', 'player'] })
    const groups = w.findAll('[role="group"]')
    expect(groups.map((g) => g.attributes('data-group'))).toEqual(['tournament', 'team', 'player'])
    expect(groups.map((g) => g.find('[role="presentation"]').text())).toEqual([t('ui.suggest.group.tournament'), t('ui.suggest.group.team'), t('ui.suggest.group.player')])
    // LaLiga is the follow and SofaScore's hit at once: shown once, already added
    expect(w.findAll('[data-hit="tournament:8"]')).toHaveLength(1)
    expect(w.find('[data-hit="tournament:8"]').text()).toContain(t('ui.followEditor.alreadyFollowed'))
    expect(w.find('[data-hit="tournament:34"]').text()).toContain('France')
    expect(w.find('[data-hit="tournament:34"]').text()).toContain(t('sport.football'))
    expect(w.find('[data-hit="team:2699"]').text()).toContain('Italy')
    expect(w.find('[data-hit="player:1402912"] [data-testid="hit-team"]').text()).toBe(t('ui.followEditor.playsFor', { team: 'Barcelona' }))
    expect(w.find('[data-testid="suggest-status"]').text()).toBe(t('ui.suggest.count', { n: 5 }))
    vi.useRealTimers() // axe waits with real timers
    expect(await axeViolations(w.find('[data-testid="editor-step"]').element)).toEqual([])
  })

  it('"la liga" typed quickly costs one request', async () => {
    routes()
    const input = await open()
    await type(input, 'la liga', 100)
    expect(searches.map((s) => s.q)).toEqual(['la liga'])
  })

  it('"la liga" typed slowly: one request per pause, "la " is "la"; typed again: none (kept for the page)', async () => {
    routes()
    const input = await open()
    await type(input, 'la liga', 400)
    expect(searches.map((s) => s.q)).toEqual(['la', 'la l', 'la li', 'la lig', 'la liga'])
    await input.setValue('')
    await vi.advanceTimersByTimeAsync(1000)
    await type(input, 'la liga', 400)
    expect(searches).toHaveLength(5)
    // case and spaces do not matter
    await input.setValue('LA  LIGA ')
    await vi.advanceTimersByTimeAsync(1000)
    expect(searches).toHaveLength(5)
  })

  it('a space after "la" while its answer is on the way aborts nothing and asks nothing again', async () => {
    routes()
    const input = await open()
    answerAfter = 1000
    await input.setValue('la')
    await vi.advanceTimersByTimeAsync(REMOTE_DELAY + 10)
    expect(searches.map((s) => s.q)).toEqual(['la'])
    await input.setValue('la ')
    await vi.advanceTimersByTimeAsync(REMOTE_DELAY + 1500)
    expect(searches.map((s) => s.q)).toEqual(['la'])
    expect(searches[0].signal?.aborted).toBe(false)
    expect(w.find('[data-hit="team:2699"]').exists()).toBe(true)
  })

  it('a newer keystroke aborts the older request; only the newest answer is shown', async () => {
    routes()
    const input = await open()
    answerAfter = 1000
    await input.setValue('la')
    await vi.advanceTimersByTimeAsync(REMOTE_DELAY + 10)
    expect(searches.map((s) => s.q)).toEqual(['la'])
    expect(searches[0].signal?.aborted).toBe(false)
    await input.setValue('laz')
    expect(searches[0].signal?.aborted).toBe(true)
    await vi.advanceTimersByTimeAsync(REMOTE_DELAY + 1500)
    expect(searches.map((s) => s.q)).toEqual(['la', 'laz'])
    expect(searches[1].signal?.aborted).toBe(false)
    // the aborted "la" is not kept: asked again when typed again
    await input.setValue('la')
    await vi.advanceTimersByTimeAsync(REMOTE_DELAY + 1500)
    expect(searches.map((s) => s.q)).toEqual(['la', 'laz', 'la'])
    expect(w.find('[data-hit="team:2699"]').exists()).toBe(true)
  })

  it('arrows move, Enter picks and fills the editor, Esc closes; Enter with nothing chosen searches at once', async () => {
    routes()
    const input = await open()
    await input.setValue('la')
    // Enter with nothing chosen submits the search form
    await input.trigger('keydown', { key: 'Enter' })
    await w.find('form[role="search"]').trigger('submit')
    await vi.advanceTimersByTimeAsync(10)
    // at once, without the pause
    expect(searches.map((s) => s.q)).toEqual(['la'])
    await vi.advanceTimersByTimeAsync(200) // the stored names (this server)
    expect(input.attributes('aria-expanded')).toBe('true')
    const options = w.findAll('[role="option"]')
    expect(options.map((o) => o.attributes('data-hit'))).toEqual(['tournament:8', 'tournament:34', 'team:6577', 'team:2699', 'player:1402912'])
    await input.trigger('keydown', { key: 'ArrowDown' })
    await input.trigger('keydown', { key: 'ArrowDown' })
    await input.trigger('keydown', { key: 'ArrowDown' })
    await input.trigger('keydown', { key: 'ArrowDown' })
    await input.trigger('keydown', { key: 'ArrowUp' })
    const palmas = w.find('[data-hit="team:6577"]')
    expect(input.attributes('aria-activedescendant')).toBe(palmas.attributes('id'))
    expect(palmas.attributes('aria-selected')).toBe('true')
    await input.trigger('keydown', { key: 'ArrowDown' })
    await input.trigger('keydown', { key: 'Enter' })
    await vi.advanceTimersByTimeAsync(10)
    // Lazio, a team: the kind, the number, the name and the sport are filled; the list is closed
    expect(input.attributes('aria-expanded')).toBe('false')
    expect(w.find('[data-testid="editor-picked"]').attributes('data-hit')).toBe('team:2699')
    expect((w.find('[data-kind="team"] input').element as HTMLInputElement).checked).toBe(true)
    expect((w.find('[data-testid="editor-id"]').element as HTMLInputElement).value).toBe('2699')
    expect((w.find('[data-testid="editor-name"]').element as HTMLInputElement).value).toBe('Lazio')
    expect((w.find('[data-testid="editor-sport"]').element as HTMLSelectElement).value).toBe('football')
    expect(w.find('[data-testid="editor-next"]').attributes('disabled')).toBeUndefined()
    expect(document.activeElement).toBe(w.find('[data-testid="editor-next"]').element)
    // picking asked nothing again (the sport only filters what is shown)
    expect(searches).toHaveLength(1)
    // Esc closes an open list and stays in the field
    await input.trigger('focus')
    await input.trigger('keydown', { key: 'ArrowDown' })
    expect(input.attributes('aria-expanded')).toBe('true')
    await input.trigger('keydown', { key: 'Escape' })
    expect(input.attributes('aria-expanded')).toBe('false')
    expect(w.find('[data-testid="editor-picked"]').exists()).toBe(true)
  })

  it('the sport filters the list; a refusal is shown and not kept', async () => {
    const f = routes({ 'POST /api/v1/tournaments/search': () => json({ error: { code: 'blocked', message: 'x', details: { reason: 'blocked' }, request_id: 'r' } }, 503) })
    const input = await open()
    await input.setValue('la')
    await vi.advanceTimersByTimeAsync(REMOTE_DELAY + 10)
    expect(w.find('[data-testid="form-error"]').attributes('data-code')).toBe('blocked')
    await input.setValue('l')
    await input.setValue('la')
    await vi.advanceTimersByTimeAsync(REMOTE_DELAY + 10)
    expect(callsTo(f, 'POST /api/v1/tournaments/search')).toHaveLength(2)
    await w.find('[data-testid="editor-sport"]').setValue('basketball')
    await vi.advanceTimersByTimeAsync(10)
    expect(w.findAll('[role="option"]')).toHaveLength(0)
  })

  it('a country is named in the reader’s language; SofaScore’s own codes and regions too (FX-24 F12)', () => {
    const spain = { category: {}, country: { code: 'ES', name: 'Spain' } }
    const england = { category: { name: 'England', country_code: 'EN' }, country: null }
    expect(hitPlace(spain)).toBe('Spain')
    expect(hitPlace({ category: {}, country: { code: 'TR', name: null } })).toBe('Türkiye')
    setLocale('tr')
    expect(hitPlace(spain)).toBe('İspanya')
    // FX-20 kept SofaScore's English name for its own codes and regions; FX-24 names them in the locale
    expect(hitPlace(england)).toBe('İngiltere')
    expect(hitPlace({ category: { name: 'World' }, country: null })).toBe('Dünya')
    setLocale('en')
  })
})
