import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import AddLeagueDialog from '@/components/AddLeagueDialog.vue'
import { useSportStore } from '@/stores/sport'
import { i18n, setLocale } from '@/i18n'
import { callsTo, json, mockFetch } from './helpers'

const t = i18n.global.t
const SEARCH = 'POST /api/leagues/search-remote'
const PREMIER = { id: 17, name: 'Premier League', country: 'England', slug: 'premier-league', sport: 'Football' }

/** The typed error body of src/web/upstream.py. */
const upstream = (reason: string, status = 502) => json({ detail: { reason, message: `server says ${reason}` } }, status)

let w: VueWrapper

/** Types a query and lets the 350 ms debounce, the request and the render finish. */
async function type(text: string) {
  await w.get('#add-q').setValue(text)
  await settle()
}

async function settle() {
  await vi.advanceTimersByTimeAsync(400)
  await nextTick()
}

function open() {
  w = mount(AddLeagueDialog, { global: { plugins: [i18n] }, attachTo: document.body })
}

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  useSportStore().set('all')
  setLocale('en')
})

afterEach(() => {
  w?.unmount()
  vi.useRealTimers()
  setLocale('tr')
})

describe('AddLeagueDialog search states', () => {
  it('lists what SofaScore found', async () => {
    mockFetch({ [SEARCH]: [PREMIER] })
    open()
    await type('premier')
    expect(w.text()).toContain('Premier League')
    expect(w.text()).not.toContain(t('add.noResults'))
    expect(w.find('[data-testid="add-error"]').exists()).toBe(false)
  })

  it('says "No results" when the search succeeded with nothing found', async () => {
    mockFetch({ [SEARCH]: [] })
    open()
    await type('zzzz')
    expect(w.text()).toContain(t('add.noResults'))
    expect(w.find('[data-testid="add-error"]').exists()).toBe(false)
  })

  it.each([
    ['blocked', 502],
    ['browser', 502],
    ['rate_limited', 503],
    ['network', 502],
    ['upstream', 502],
  ])('explains a %s failure with its next step instead of "No results"', async (reason, status) => {
    mockFetch({ [SEARCH]: () => upstream(reason, status) })
    open()
    await type('premier')
    const alert = w.get('[data-testid="add-error"]')
    expect(alert.attributes('role')).toBe('alert')
    expect(alert.text()).toContain(t(`upstream.${reason}`))
    expect(alert.text()).not.toContain(`server says ${reason}`) // the translation, not the server's English
    expect(w.text()).not.toContain(t('add.noResults'))
  })

  it('gives every reason its own message in both languages', () => {
    const reasons = ['blocked', 'browser', 'rate_limited', 'network', 'not_found', 'upstream', 'testHint']
    for (const lang of ['en', 'tr'] as const) {
      setLocale(lang)
      const texts = reasons.map((r) => t(`upstream.${r}`))
      expect(new Set(texts).size).toBe(reasons.length)
      for (const [i, text] of texts.entries()) expect(text).not.toBe(`upstream.${reasons[i]}`)
    }
  })

  it('speaks Turkish too', async () => {
    setLocale('tr')
    mockFetch({ [SEARCH]: () => upstream('blocked') })
    open()
    await type('premier')
    expect(w.get('[data-testid="add-error"]').text()).toContain('SofaScore isteği reddetti')
    expect(w.text()).not.toContain('Sonuç bulunamadı')
  })

  it('retries from the error and shows the results once SofaScore answers', async () => {
    let answer: () => unknown = () => upstream('blocked')
    const fetchMock = mockFetch({ [SEARCH]: () => answer() })
    open()
    await type('premier')
    answer = () => [PREMIER]
    await w.get('[data-testid="add-error"] button').trigger('click')
    await settle()
    expect(callsTo(fetchMock, SEARCH)).toHaveLength(2)
    expect(w.find('[data-testid="add-error"]').exists()).toBe(false)
    expect(w.text()).toContain('Premier League')
  })

  it('does not keep showing old results after a later search failed', async () => {
    let answer: () => unknown = () => [PREMIER]
    mockFetch({ [SEARCH]: () => answer() })
    open()
    await type('premier')
    expect(w.text()).toContain('Premier League')
    answer = () => upstream('network')
    await type('premier l')
    expect(w.get('[data-testid="add-error"]').text()).toContain(t('upstream.network'))
    expect(w.text()).not.toContain('England')
  })

  it('shows an untyped server error as it is, never as "No results"', async () => {
    mockFetch({ [SEARCH]: () => json({ detail: 'Internal error' }, 500) })
    open()
    await type('premier')
    expect(w.get('[data-testid="add-error"]').text()).toContain('Internal error')
    expect(w.text()).not.toContain(t('add.noResults'))
  })

  it('says the app server is down when the request never got there', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    open()
    await type('premier')
    expect(w.get('[data-testid="add-error"]').text()).toContain(t('common.serverDown'))
    expect(w.text()).not.toContain(t('add.noResults'))
  })

  it('clears the error when the query changes', async () => {
    let answer: () => unknown = () => upstream('blocked')
    mockFetch({ [SEARCH]: () => answer() })
    open()
    await type('premier')
    expect(w.find('[data-testid="add-error"]').exists()).toBe(true)
    answer = () => []
    await w.get('#add-q').setValue('premier league')
    expect(w.find('[data-testid="add-error"]').exists()).toBe(false) // gone before the next answer
    await settle()
    expect(w.text()).toContain(t('add.noResults'))
  })
})
