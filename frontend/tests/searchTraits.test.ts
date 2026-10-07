import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import App from '@/App.vue'
import { routes } from '@/router'
import FollowEditorScreen from '@/screens/follows/FollowEditorScreen.vue'
import FollowDetailScreen from '@/screens/follows/FollowDetailScreen.vue'
import { markTwins, seenTeam, traitsApart } from '@/app/suggest'
import { hitTraits } from '@/screens/follows/followText'
import { resetSports } from '@/app/sports'
import { resetNames } from '@/screens/events/eventText'
import { authNeeded } from '@/lib/auth'
import { tokenInUse } from '@/app/session'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { flush, mockFetch } from './helpers'
import { axeViolations, follow, job, mountScreen, page, sport, status, useFakeES } from './v1'

/**
 * FX-25 (F26 gösterimi): arama sonuçları takımın cinsiyetini (`gender`) ve milli takım olduğunu (`national`,
 * FX-23) taşır; öneriler, Ctrl K ve takip başlığı bunları yazar, aynı adlı iki sonucu cinsiyet zaten
 * ayırıyorsa numara gösterilmez. Hiçbir istek testten çıkmaz.
 */
const t = i18n.global.t
let wrappers: VueWrapper[] = []

const list = <T>(data: T[]) => ({ data, page: { limit: 0, next_cursor: null } })
const SPORTS = {
  'GET /api/v1/sports': list([sport('football'), sport('volleyball')]),
  'GET /api/v1/sports/football': { data: sport('football') },
  'GET /api/v1/sports/volleyball': { data: sport('volleyball') },
  'GET /api/v1/status': { data: status() },
}
const team = (id: number, sportSlug: string, gender: string | null, over: Record<string, unknown> = {}) => ({
  kind: 'team',
  id,
  name: 'Fenerbahçe',
  sport: sportSlug,
  category: { country_code: 'TR' },
  country: { code: 'TR', name: 'Turkey' },
  team: null,
  followed: false,
  gender,
  national: false,
  ...over,
})

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
  resetSports()
  resetNames()
})
afterEach(() => {
  for (const w of wrappers) w.unmount()
  wrappers = []
  document.body.innerHTML = ''
})

describe('gender and national team in words', () => {
  it('a women’s team says so, a national team says so; men’s teams and unknown values say nothing', () => {
    setLocale('tr')
    expect(hitTraits({ gender: 'F', national: false })).toEqual(['Kadın'])
    expect(hitTraits({ gender: 'M', national: true })).toEqual(['Milli takım'])
    expect(hitTraits({ gender: 'F', national: true })).toEqual(['Milli takım', 'Kadın'])
    expect(hitTraits({ gender: null, national: null })).toEqual([])
    expect(hitTraits({ gender: 'X' })).toEqual([])
    expect(hitTraits(null)).toEqual([])
    setLocale('en')
    expect(hitTraits({ gender: 'F', national: true })).toEqual(['National team', 'Women'])
  })

  it('gender or the national flag tells two hits apart only when both are known and differ', () => {
    expect(traitsApart({ gender: 'M' }, { gender: 'F' })).toBe(true)
    expect(traitsApart({ gender: 'F' }, { gender: 'F' })).toBe(false)
    expect(traitsApart({ gender: 'F' }, { gender: null })).toBe(false)
    expect(traitsApart({ gender: 'M', national: true }, { gender: 'M', national: false })).toBe(true)
    expect(traitsApart({ national: true }, { national: null })).toBe(false)
    // üç aynı adlı sonuç: kadın takımı tek, iki erkek takımı birbirinden ayrılmıyor
    const marked = markTwins(
      [
        { id: 1, gender: 'M', twin: false },
        { id: 2, gender: 'F', twin: false },
        { id: 3, gender: 'M', twin: false },
      ],
      () => 'same',
      traitsApart,
    )
    expect(marked.map((x) => x.twin)).toEqual([true, false, true])
  })
})

describe('the editor’s suggestions', () => {
  async function search(hits: unknown[]) {
    mockFetch({ ...SPORTS, 'POST /api/v1/tournaments/search': list(hits) })
    const { w } = await mountScreen(FollowEditorScreen, '/follows/new')
    wrappers.push(w)
    await flush()
    await w.find('[data-testid="editor-query"]').setValue('fener')
    await w.find('[data-testid="editor-search"]').trigger('submit')
    await flush()
    return w
  }

  it('"Fenerbahçe · Volleyball · Türkiye · Women": the men’s and the women’s team without numbers', async () => {
    setLocale('tr')
    const w = await search([team(3052, 'football', 'M'), team(36456, 'volleyball', 'M'), team(36460, 'volleyball', 'F')])
    const option = (id: number) => w.find(`[data-hit="team:${id}"]`)
    expect(option(36460).findAll('[data-testid="hit-trait"]').map((x) => x.text())).toEqual(['Kadın'])
    expect(option(36460).text()).toMatch(/Fenerbahçe\s*Voleybol\s*Türkiye\s*Kadın/)
    expect(option(36456).find('[data-testid="hit-trait"]').exists()).toBe(false)
    // cinsiyet ayırıyor: numara yok
    expect(w.find('[data-testid="hit-number"]').exists()).toBe(false)
    expect(await axeViolations(w.find('[data-testid="editor-hits"]').element)).toEqual([])
    // seçilen satır da söyler
    await option(36460).trigger('click')
    await flush()
    expect(w.findAll('[data-testid="picked-trait"]').map((x) => x.text())).toEqual(['Kadın'])
  })

  it('the number stays when gender does not tell them apart (both women, or one unknown)', async () => {
    const w = await search([team(36460, 'volleyball', 'F'), team(36470, 'volleyball', 'F'), team(3052, 'football', 'M'), team(3060, 'football', null)])
    const number = (id: number) => w.find(`[data-hit="team:${id}"] [data-testid="hit-number"]`)
    expect(number(36460).text()).toBe(t('ui.suggest.number', { id: 36460 }))
    expect(number(36470).text()).toBe(t('ui.suggest.number', { id: 36470 }))
    expect(number(3052).exists()).toBe(true)
    expect(number(3060).exists()).toBe(true)
  })

  it('a national team says "National team"', async () => {
    const w = await search([team(4700, 'football', 'M', { name: 'Türkiye', national: true }), team(4701, 'football', 'F', { name: 'Türkiye', national: true })])
    expect(w.find('[data-hit="team:4700"]').findAll('[data-testid="hit-trait"]').map((x) => x.text())).toEqual(['National team'])
    expect(w.find('[data-hit="team:4701"]').findAll('[data-testid="hit-trait"]').map((x) => x.text())).toEqual(['National team', 'Women'])
    expect(w.find('[data-testid="hit-number"]').exists()).toBe(false)
  })
})

describe('Ctrl K', () => {
  beforeEach(() => {
    authNeeded.value = false
    tokenInUse.value = false
    useFakeES()
  })

  it('the hint of a SofaScore hit names the gender; the number only when gender does not separate', async () => {
    setLocale('tr')
    mockFetch({
      ...SPORTS,
      'GET /api/v1/jobs': { data: [job()], page: { limit: 20, next_cursor: null } },
      'GET /api/v1/settings': { data: { config_file: null, overrides_file: null, settings: [] } },
      'GET /api/v1/follows': page([]),
      'GET /api/v1/sinks': page([]),
      'GET /api/v1/changes': page([]),
      'GET /api/v1/tournaments': page([]),
      'POST /api/v1/tournaments/search': list([team(36456, 'volleyball', 'M'), team(36460, 'volleyball', 'F'), team(4700, 'football', 'M', { name: 'Fenerbahçe U19', national: true })]),
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
    await w.find('input[role="combobox"]').setValue('fener')
    await vi.waitFor(() => expect(w.findAll('[data-testid="palette-sofascore"] [role="option"]')).toHaveLength(4), { timeout: 2000 })
    const section = w.find('[data-testid="palette-sofascore"]')
    expect(section.find('[data-hit="ss-team-36460"]').text()).toContain('Takım · Voleybol · Türkiye · Kadın')
    expect(section.find('[data-hit="ss-team-36456"]').text()).not.toContain('Kadın')
    expect(section.find('[data-hit="ss-team-4700"]').text()).toContain('Futbol · Türkiye · Milli takım')
    expect(section.text()).not.toContain(t('ui.suggest.number', { id: 36460 }))
  })
})

describe('the follow header', () => {
  it('a team seen in a search on this page: "Volleyball · Türkiye · Team · Women"; unknown: as before', async () => {
    mockFetch({
      ...SPORTS,
      'POST /api/v1/tournaments/search': list([team(36460, 'volleyball', 'F'), team(4700, 'football', 'M', { name: 'Türkiye', national: true })]),
      'GET /api/v1/follows/team:36460': { data: follow({ id: 'team:36460', kind: 'team', entity_id: 36460, name: 'Fenerbahçe', sport: 'volleyball' }) },
      'GET /api/v1/follows/team:4700': { data: follow({ id: 'team:4700', kind: 'team', entity_id: 4700, name: 'Türkiye', sport: 'football' }) },
      'GET /api/v1/follows/team:3052': { data: follow({ id: 'team:3052', kind: 'team', entity_id: 3052, name: 'Fenerbahçe', sport: 'football' }) },
      'GET /api/v1/jobs': page([]),
      'GET /api/v1/events': page([]),
    })
    // önce bir arama (düzenleyicide): sonuçlar bu sayfada bilinir
    const editor = await mountScreen(FollowEditorScreen, '/follows/new')
    await flush()
    await editor.w.find('[data-testid="editor-query"]').setValue('fener')
    await editor.w.find('[data-testid="editor-search"]').trigger('submit')
    await flush()
    editor.w.unmount()
    expect(seenTeam('team:36460')?.gender).toBe('F')

    const header = async (id: number) => {
      const { w } = await mountScreen(FollowDetailScreen, `/follows/team/${id}`, '/follows/:kind/:id')
      wrappers.push(w)
      await flush()
      return w.find('[data-testid="follow-header-line"]').text()
    }
    const turkeyEn = new Intl.DisplayNames(['en'], { type: 'region' }).of('TR')
    expect(await header(36460)).toBe(`Volleyball · ${turkeyEn} · Team · Women`)
    setLocale('tr')
    expect(await header(4700)).toBe('Futbol · Türkiye · Milli takım')
    // aramada görülmeyen takım: yalnızca spor ve tür
    expect(await header(3052)).toBe('Futbol · Takım')
  })
})
