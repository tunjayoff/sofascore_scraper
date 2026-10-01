import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import App from '@/App.vue'
import SettingsView from '@/views/SettingsView.vue'
import MatchesView from '@/views/MatchesView.vue'
import { useLeaguesStore } from '@/stores/leagues'
import { useSportStore } from '@/stores/sport'
import { toasts } from '@/lib/toast'
import { i18n } from '@/i18n'
import { FakeEventSource, flush, json, mockFetch } from './helpers'

const t = i18n.global.t
const LEAGUES = [{ id: 17, name: 'Premier League', sport: 'football' }]
const STATS = { leagues: 1, seasons: 1, matches: 0, details: 0, league_breakdown: [], disk_usage: { formatted_total: '1 MB', total: 1 } }
const SETTINGS = {
  language: 'tr', data_dir: 'data', max_concurrent: 5, wait_time_min: 1, wait_time_max: 2, request_timeout: 20,
  max_retries: 3, fetch_only_finished: true, save_empty_rounds: false, refresh_window_hours: 72, log_level: 'INFO',
}
const down = () => json({ detail: 'Internal error' }, 500)

function router(path: string, component: object) {
  const r = createRouter({
    history: createMemoryHistory(),
    routes: [{ path, component }, { path: '/:rest(.*)*', component: { template: '<div />' } }],
  })
  return r
}

beforeEach(() => {
  setActivePinia(createPinia())
  useSportStore().set('all')
  toasts.value = []
  vi.stubGlobal('EventSource', FakeEventSource)
})

describe('leagues store', () => {
  it('toasts a stats failure but still shows the leagues', async () => {
    mockFetch({ 'GET /api/leagues': LEAGUES, 'GET /api/stats/system': down })
    const leagues = useLeaguesStore()
    await leagues.load()
    expect(leagues.all.map((l) => l.name)).toEqual(['Premier League'])
    expect(leagues.error).toBe('')
    expect(toasts.value.map((x) => [x.kind, x.text])).toEqual([['error', t('common.statsFailed', { error: 'Internal error' })]])
  })

  it('keeps the error for the page and does not add a stats toast when the leagues fail too', async () => {
    mockFetch({ 'GET /api/leagues': down, 'GET /api/stats/system': down })
    const leagues = useLeaguesStore()
    await expect(leagues.load()).rejects.toThrow('Internal error')
    expect(leagues.error).toBe('Internal error')
    expect(toasts.value).toEqual([])
  })
})

describe('App', () => {
  it('shows a failed league load with a retry that clears it', async () => {
    let leaguesAnswer: () => Response = down
    mockFetch({
      'GET /api/leagues': () => leaguesAnswer(),
      'GET /api/stats/system': STATS,
      'GET /api/scrape/status': { job_id: null, is_running: false, status: 'Idle', progress: 0, current_task: '' },
    })
    const r = router('/', { template: '<div />' })
    await r.push('/')
    const w = mount(App, { global: { plugins: [i18n, r] } })
    await flush()

    const card = w.find('[role="alert"]')
    expect(card.text()).toContain(t('leagues.loadFailed'))
    expect(card.text()).toContain('Internal error')

    leaguesAnswer = () => json(LEAGUES)
    await card.find('button').trigger('click')
    await flush()
    expect(w.find('[role="alert"]').exists()).toBe(false)
    expect(useLeaguesStore().all).toHaveLength(1)
    w.unmount()
  })
})

describe('SettingsView', () => {
  it('toasts a stats failure and still fills the form', async () => {
    mockFetch({ 'GET /api/settings': SETTINGS, 'GET /api/stats/system': down })
    const w = mount(SettingsView, { global: { plugins: [i18n] } })
    await flush()
    expect(toasts.value.map((x) => x.text)).toEqual([t('common.statsFailed', { error: 'Internal error' })])
    w.unmount()
  })

  it('does not toast stats when the settings themselves failed (one message, not two)', async () => {
    mockFetch({ 'GET /api/settings': down, 'GET /api/stats/system': down })
    const w = mount(SettingsView, { global: { plugins: [i18n] } })
    await flush()
    expect(toasts.value.map((x) => x.text)).toEqual(['Internal error'])
    w.unmount()
  })
})

describe('MatchesView seasons', () => {
  it('shows a season load failure instead of an empty list, and retries', async () => {
    let seasonsAnswer: () => Response = down
    mockFetch({
      'GET /api/leagues': LEAGUES,
      'GET /api/stats/system': STATS,
      'GET /api/matches': { items: [], total: 0, limit: 25, offset: 0, sort: 'desc' },
      'GET /api/leagues/17/seasons': () => seasonsAnswer(),
    })
    const r = router('/matches', MatchesView)
    await r.push('/matches?league_id=17')
    const w = mount(MatchesView, { global: { plugins: [i18n, r] } })
    await flush()

    const alert = w.find('[role="alert"]')
    expect(alert.text()).toContain(t('matches.seasonsFailed', { error: 'Internal error' }))

    seasonsAnswer = () => json({ seasons: [{ id: 76986, name: 'Premier League 25/26' }], fetched: true })
    await alert.find('button').trigger('click')
    await flush()
    expect(w.find('[role="alert"]').exists()).toBe(false)
    expect(w.findAll('#f-season option').map((o) => o.text())).toContain('Premier League 25/26')
    w.unmount()
  })
})
