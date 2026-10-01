import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import DownloadView from '@/views/DownloadView.vue'
import { useSportStore } from '@/stores/sport'
import { i18n, setLocale } from '@/i18n'
import { callsTo, flush, json, mockFetch } from './helpers'

const t = i18n.global.t
const LEAGUES = [{ id: 17, name: 'Premier League', sport: 'football' }]
const STATS = { leagues: 1, seasons: 0, matches: 0, details: 0, league_breakdown: [], disk_usage: { formatted_total: '0 B', total: 0 } }
const SEASONS = [{ id: 76986, name: 'Premier League 25/26', year: '25/26' }]
const SAVED = 'GET /api/leagues/17/seasons'
const REFRESH = 'POST /api/leagues/17/seasons/refresh'
const upstream = (reason: string, status = 502) => json({ detail: { reason, message: `server says ${reason}` } }, status)

type Routes = Parameters<typeof mockFetch>[0]
let w: VueWrapper

async function open(routes: Routes) {
  const fetchMock = mockFetch({ 'GET /api/leagues': LEAGUES, 'GET /api/stats/system': STATS, ...routes })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/download', component: DownloadView }, { path: '/:rest(.*)*', component: { template: '<div />' } }],
  })
  await router.push('/download')
  w = mount(DownloadView, { global: { plugins: [i18n, router] } })
  await flush()
  await flush()
  return fetchMock
}

beforeEach(() => {
  setActivePinia(createPinia())
  useSportStore().set('all')
  setLocale('en')
})

afterEach(() => {
  w?.unmount()
  setLocale('tr')
})

describe('DownloadView season refresh', () => {
  it.each([
    ['blocked', 502],
    ['browser', 502],
    ['rate_limited', 503],
    ['network', 502],
    ['not_found', 404],
    ['upstream', 502],
  ])('says why the season list could not be fetched: %s', async (reason, status) => {
    await open({ [SAVED]: { seasons: [], fetched: false }, [REFRESH]: () => upstream(reason, status) })
    const alert = w.get('[role="alert"]')
    expect(alert.text()).toContain(t(`upstream.${reason}`))
    expect(alert.text().includes(t('upstream.testHint'))).toBe(reason === 'blocked') // where the check helps
    expect(alert.text()).not.toContain(`server says ${reason}`)
    expect(w.text()).toContain(t('download.noSeasons'))
    expect(w.text()).not.toContain(t('download.seasonsEmpty'))
  })

  it('retries and shows the seasons once SofaScore answers', async () => {
    let answer: () => unknown = () => upstream('blocked')
    const fetchMock = await open({ [SAVED]: { seasons: [], fetched: false }, [REFRESH]: () => answer() })
    expect(w.find('[role="alert"]').exists()).toBe(true)
    answer = () => ({ status: 'success', seasons: SEASONS })
    const retry = w.findAll('button').find((b) => b.text() === t('download.fetchSeasons'))!
    await retry.trigger('click')
    await flush()
    expect(callsTo(fetchMock, REFRESH)).toHaveLength(2)
    expect(w.find('[role="alert"]').exists()).toBe(false)
    expect(w.text()).toContain('Premier League 25/26')
  })

  it('says a league has no seasons only when SofaScore really answered with none', async () => {
    await open({ [SAVED]: { seasons: [], fetched: false }, [REFRESH]: { status: 'success', seasons: [] } })
    expect(w.find('[role="alert"]').exists()).toBe(false)
    expect(w.text()).toContain(t('download.seasonsEmpty'))
    expect(w.text()).not.toContain(t('download.noSeasons'))
  })

  it('keeps the saved list when a refresh fails and explains the failure', async () => {
    const fetchMock = await open({ [SAVED]: { seasons: SEASONS, fetched: true }, [REFRESH]: () => upstream('network') })
    expect(callsTo(fetchMock, REFRESH)).toHaveLength(0) // a saved list is not refreshed behind the user's back
    await w.get(`button[title="${t('common.refresh')}"]`).trigger('click')
    await flush()
    expect(w.get('[role="alert"]').text()).toBe(t('upstream.network'))
    expect(w.text()).toContain('Premier League 25/26')
  })
})
