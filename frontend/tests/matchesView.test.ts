import { beforeEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import MatchesView from '@/views/MatchesView.vue'
import { i18n } from '@/i18n'
import { useSportStore } from '@/stores/sport'
import { callsTo, deferred, flush, json, mockFetch } from './helpers'

const LEAGUES = [
  { id: 17, name: 'Premier League', sport: 'football' },
  { id: 8, name: 'LaLiga', sport: 'football' },
  { id: 132, name: 'NBA', sport: 'basketball' },
]
const SEASONS: Record<number, { id: number; name: string }[]> = {
  17: [{ id: 76986, name: 'Premier League 25/26' }],
  8: [{ id: 77559, name: 'LaLiga 25/26' }],
  132: [{ id: 80229, name: 'NBA 25/26' }],
}
const emptyPage = { items: [], total: 0, limit: 25, offset: 0, sort: 'desc' }

type Routes = Parameters<typeof mockFetch>[0]

function baseRoutes(): Routes {
  const r: Routes = {
    'GET /api/leagues': LEAGUES,
    'GET /api/stats/system': { leagues: 3, seasons: 0, matches: 0, details: 0, league_breakdown: [], disk_usage: { formatted_total: '0 B', total: 0 } },
    'GET /api/matches': emptyPage,
  }
  for (const id of Object.keys(SEASONS)) r[`GET /api/leagues/${id}/seasons`] = { seasons: SEASONS[Number(id)], fetched: true }
  return r
}

async function mountAt(query = '') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/matches', component: MatchesView },
      { path: '/:rest(.*)*', component: { template: '<div />' } },
    ],
  })
  await router.push(`/matches${query}`)
  const w = mount(MatchesView, { global: { plugins: [i18n, router] } })
  await flush()
  return w
}

/** List requests only: the missing-details banner asks /api/matches with limit=1. */
function listCalls(fetchMock: ReturnType<typeof mockFetch>) {
  return callsTo(fetchMock, 'GET /api/matches').filter(([u]) => new URL(String(u), 'http://x').searchParams.get('limit') === '25')
}

function seasonOptions(w: VueWrapper) {
  return w.findAll('#f-season option').map((o) => o.text()).slice(1) // first is "all seasons"
}

beforeEach(() => {
  setActivePinia(createPinia())
  useSportStore().set('all')
})

describe('MatchesView', () => {
  it('shows the seasons of the last picked league when an earlier answer comes back late', async () => {
    const pl = deferred<Response>()
    const laliga = deferred<Response>()
    mockFetch({ ...baseRoutes(), 'GET /api/leagues/17/seasons': () => pl.promise, 'GET /api/leagues/8/seasons': () => laliga.promise })
    const w = await mountAt()

    await w.find('#f-league').setValue('17')
    await w.find('#f-league').setValue('8')
    laliga.resolve(json({ seasons: SEASONS[8], fetched: true }))
    await flush()
    pl.resolve(json({ seasons: SEASONS[17], fetched: true }))
    await flush()

    expect(seasonOptions(w)).toEqual(['LaLiga 25/26'])
    w.unmount()
  })

  it.each([
    ['league', '', (w: VueWrapper) => w.find('#f-league').setValue('17')],
    ['date', '', (w: VueWrapper) => w.find('#f-date').setValue('2026-09-20')],
    ['sort', '', (w: VueWrapper) => w.find('#f-sort').setValue('asc')],
    ['details', '', (w: VueWrapper) => w.find('#f-details').setValue('missing')],
    ['date on page 2', '?page=2', (w: VueWrapper) => w.find('#f-date').setValue('2026-09-20')],
    ['league on page 2 with a season', '?league_id=17&season_id=76986&page=2', (w: VueWrapper) => w.find('#f-league').setValue('8')],
    ['sport', '', async () => useSportStore().set('football')],
    ['sport on page 2', '?page=2', async () => useSportStore().set('football')],
    ['sport that drops the league', '?league_id=132&page=2', async () => useSportStore().set('football')],
  ])('one %s change makes one list request', async (_name, query, change) => {
    const fetchMock = mockFetch(baseRoutes())
    const w = await mountAt(query)
    const before = listCalls(fetchMock).length
    await change(w)
    await flush()
    expect(listCalls(fetchMock).length - before).toBe(1)
    w.unmount()
  })
})
