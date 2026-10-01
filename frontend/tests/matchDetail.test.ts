import { beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import MatchView from '@/views/MatchView.vue'
import SettingsView from '@/views/SettingsView.vue'
import { api } from '@/api/client'
import { parseIncidents, playerDisplayName, shirtOf, splitLineupPlayers, type MatchDetail } from '@/lib/matchDetail'
import { i18n } from '@/i18n'
import { callsTo, flush, mockFetch } from './helpers'

const t = i18n.global.t

/** Trimmed from a saved Premier League match (basic, incidents, lineups, h2h, pregame_form). */
const DETAIL: MatchDetail = {
  basic: {
    homeTeam: { name: 'Arsenal', shortName: 'ARS' },
    awayTeam: { name: 'Chelsea', shortName: 'CHE' },
    homeScore: { current: 2, display: 2, period1: 1, period2: 1 },
    awayScore: { current: 1, display: 1, period1: 0, period2: 1 },
    tournament: { name: 'Premier League', category: { sport: { name: 'Football', slug: 'football' } } },
    season: { name: 'Premier League 25/26', year: '25/26' },
    startTimestamp: 1758394800,
    status: { description: 'Ended' },
    venue: { stadium: { name: 'Emirates Stadium' } },
    referee: { name: 'Michael Oliver' },
  },
  incidents: {
    incidents: [
      { incidentType: 'goal', time: 77, isHome: true, homeScore: 2, awayScore: 1, player: { name: 'Saka' } },
      { incidentType: 'card', incidentClass: 'yellow', time: 40, isHome: false, player: { name: 'Caicedo' } },
      { incidentType: 'injuryTime', time: 45, addedTime: 2 },
    ],
  },
  lineups: {
    home: { players: [{ player: { name: 'Raya', jerseyNumber: '22' }, shirtNumber: 22, substitute: false }, { player: { name: 'Nwaneri' }, jerseyNumber: '53', substitute: true }] },
    away: { players: [{ player: { name: 'Sánchez' }, jerseyNumber: '1', substitute: false }] },
  },
  h2h: { teamDuel: { homeWins: 5, draws: 3, awayWins: 2 } },
  pregame_form: { homeTeam: { form: ['W', 'W', 'D'] }, awayTeam: { form: ['L'] } },
}

beforeEach(() => setActivePinia(createPinia()))

describe('match detail helpers', () => {
  it('reads incidents in either wrapping and players with any number field', () => {
    expect(parseIncidents(DETAIL.incidents)).toHaveLength(3)
    expect(parseIncidents([{ incidentType: 'goal' }])).toHaveLength(1)
    const { starters, subs } = splitLineupPlayers(DETAIL.lineups?.home?.players)
    expect(starters.map(playerDisplayName)).toEqual(['Raya'])
    expect(subs.map((p) => [playerDisplayName(p), shirtOf(p)])).toEqual([['Nwaneri', '53']])
  })
})

describe('MatchView', () => {
  it('renders a typed match detail: teams, score by half, key events, info', async () => {
    mockFetch({ 'GET /api/matches/14025002': DETAIL })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/match/:id', component: MatchView }, { path: '/matches', component: { template: '<div />' } }] })
    await router.push('/match/14025002')
    const w = mount(MatchView, { global: { plugins: [i18n, router] } })
    await flush()

    const text = w.text()
    expect(text).toContain('Arsenal')
    expect(text).toContain('2–1')
    expect(w.findAll('th').map((th) => th.text())).toEqual(
      expect.arrayContaining([t('match.period.half', { n: 1 }), t('match.period.half', { n: 2 })]),
    )
    expect(text).toContain('Saka 2–1')
    expect(text).toContain('Emirates Stadium')
    expect(text).toContain('Michael Oliver')
    w.unmount()
  })
})

describe('backup', () => {
  it('sends the backend defaults as query options', async () => {
    const fetchMock = mockFetch({ 'POST /api/data/backup': { download_url: '/api/data/backups/b.zip', filename: 'b.zip' } })
    await api.backup()
    await api.backup({ scope: 'config', include_env: true })
    expect(fetchMock.mock.calls.map(([u]) => String(u))).toEqual([
      '/api/data/backup?scope=all&include_env=false',
      '/api/data/backup?scope=config&include_env=true',
    ])
  })

  it('the Settings backup button keeps the defaults', async () => {
    const fetchMock = mockFetch({
      'GET /api/settings': { language: 'tr', data_dir: 'data', max_concurrent: 5, wait_time_min: 1, wait_time_max: 2, request_timeout: 20, max_retries: 3, fetch_only_finished: true, save_empty_rounds: false, refresh_window_hours: 72, log_level: 'INFO' },
      'GET /api/stats/system': { leagues: 0, seasons: 0, matches: 0, details: 0, league_breakdown: [], disk_usage: { formatted_total: '0 B', total: 0 } },
      'POST /api/data/backup': { download_url: '/api/data/backups/b.zip', filename: 'b.zip' },
    })
    const w = mount(SettingsView, { global: { plugins: [i18n] } })
    await flush()
    await w.find('#settings-tab-data').trigger('click')
    await w.findAll('button').find((b) => b.text() === t('settings.backup'))!.trigger('click')
    await flush()
    expect(callsTo(fetchMock, 'POST /api/data/backup').map(([u]) => String(u))).toEqual(['/api/data/backup?scope=all&include_env=false'])
    expect(w.text()).toContain('b.zip')
    w.unmount()
  })
})
