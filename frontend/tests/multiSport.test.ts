import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import EventDetailScreen from '@/screens/events/EventDetailScreen.vue'
import type { Event, EventExtra } from '@/api/v1/schema'
import { lineScore, methodName, scoreDetail, scoreText, setText } from '@/screens/events/scoreText'
import { resetNames, roundName } from '@/screens/events/eventText'
import { resetSports } from '@/app/sports'
import { authNeeded } from '@/lib/auth'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { flush, mockFetch } from './helpers'
import { axeViolations, mountScreen, page, setting, settingsDoc, status } from './v1'

/**
 * FX-26: the score of every sport as the live validation of 2026-10-08 found it (M7 to M11, M19). The events
 * are the API's answers for real SofaScore payloads of finished matches (`fixtures/fx26-events.json`, written
 * by tests/test_fx26_event_header.py from tests/fixtures/fx26/).
 */
const t = i18n.global.t
const FIXTURE = JSON.parse(readFileSync(resolve(__dirname, 'fixtures/fx26-events.json'), 'utf8')) as Record<string, { data: Event; extra: EventExtra }>
const ev = (name: string): Event => structuredClone(FIXTURE[name].data)
const extraOf = (name: string): EventExtra => structuredClone(FIXTURE[name].extra)
let w: VueWrapper

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
  authNeeded.value = false
  resetSports()
  resetNames()
})
afterEach(() => w?.unmount())

describe('score text by sport', () => {
  it('cricket: runs/wickets (overs) per side', () => {
    expect(scoreText(ev('cricket__15884177'))).toBe('172/2 (14.4 ov) – 171/10 (19.1 ov)')
    setLocale('tr')
    expect(scoreText(ev('cricket__15884177'))).toBe('172/2 (14.4 ov) – 171/10 (19.1 ov)')
  })

  it('cricket: several innings of one side are joined (Test match)', () => {
    const e = ev('cricket__15884177')
    if (e.score.family !== 'cricket') throw new Error('family')
    e.score.innings = [
      { side: 'home', number: 1, runs: 250, wickets: 10, overs: 80.2 },
      { side: 'away', number: 1, runs: 300, wickets: 10, overs: 90 },
      { side: 'home', number: 2, runs: 120, wickets: 3, overs: 30.1 },
    ]
    expect(scoreText(e)).toBe('250/10 (80.2 ov) & 120/3 (30.1 ov) – 300/10 (90 ov)')
  })

  it('baseball: the line score with R, H and E, and the series of a postseason', () => {
    const e = ev('baseball__17199139')
    expect(scoreText(e)).toBe('1 – 3')
    const line = lineScore(e.score)!
    expect(line.columns).toEqual(['1', '2', '3', '4', '5', '6', '7', '8', '9', 'R', 'H', 'E'])
    expect(line.home).toEqual(['0', '0', '0', '0', '1', '0', '0', '0', '0', '1', '5', '2'])
    expect(line.away).toEqual(['0', '0', '0', '3', '0', '0', '0', '0', '0', '3', '8', '0'])
    expect(scoreDetail(e)).toEqual([])
    expect(scoreDetail({ ...e, extra: extraOf('baseball__17199139') })).toEqual([t('ui.eventDetail.score.series', { score: '1 – 2' })])
    expect(lineScore(ev('cricket__15884177').score)).toBeNull()
  })

  it('the second line names what is counted: legs, frames, maps (games), sets', () => {
    expect(scoreDetail(ev('darts__17236047'))).toEqual(['Legs 4 – 0'])
    expect(scoreDetail(ev('snooker__17264352'))).toEqual(['Frames 1 – 3'])
    expect(scoreDetail(ev('esports__17264020'))).toEqual(['Maps (games) 1 – 2'])
    expect(scoreDetail(ev('tennis__16385361'))).toEqual(['Sets 3 – 1'])
    setLocale('tr')
    expect(scoreDetail(ev('darts__17236047'))).toEqual(['Leg 4 – 0'])
    expect(scoreDetail(ev('snooker__17264352'))).toEqual(['Frame 1 – 3'])
    expect(scoreDetail(ev('esports__17264020'))).toEqual(['Harita (oyun) 1 – 2'])
    expect(scoreDetail(ev('tennis__16385361'))).toEqual(['Setler 3 – 1'])
  })

  it('a darts match played in sets keeps "Sets"', () => {
    const e = ev('darts__17236047')
    if (e.score.family !== 'sets') throw new Error('family')
    e.score.sets = [{ number: 1, home: 3, away: 1, tiebreak: null }]
    expect(scoreDetail(e)).toEqual(['Sets 4 – 0'])
  })

  it('tennis: the tie-break points of the set loser in brackets', () => {
    expect(scoreText(ev('tennis__16385361'))).toBe('6-7(7) 7-6(2) 6-3 6-4')
    expect(setText({ number: 1, home: 7, away: 6, tiebreak: { home: 7, away: 5 } })).toBe('7-6(5)')
    expect(setText({ number: 1, home: 6, away: 4, tiebreak: null })).toBe('6-4')
  })

  it('MMA: the winner, the method in words and the round', () => {
    const e = ev('mma__17057712')
    expect(scoreText(e)).toBe('UD · R3')
    expect(scoreDetail(e)).toEqual(['Alivia  Bierley won · Unanimous decision · round 3'])
    setLocale('tr')
    expect(scoreText(e)).toBe('UD · 3. raunt')
    expect(scoreDetail(e)).toEqual(['Alivia  Bierley kazandı · Hakem kararı (oybirliği) · 3. raunt'])
    expect(methodName('tko')).toBe('Teknik nakavt')
    expect(methodName('Points')).toBe('Points')
  })

  it('an aggregate without scores is not a line; with scores it is', () => {
    const e = ev('futsal__17256669')
    expect(e.aggregate).toEqual({ home: null, away: null, winner: 'home' })
    expect(scoreDetail(e)).toEqual([])
    e.aggregate = { home: 3, away: 2, winner: 'home' }
    expect(scoreDetail(e)).toEqual([t('ui.eventDetail.score.agg', { score: '3 – 2' })])
  })
})

describe('the match header of every sport', () => {
  const routes = (e: Event, extra: EventExtra) => ({
    'GET /api/v1/sports': { data: [], page: { limit: 0, next_cursor: null } },
    'GET /api/v1/tournaments': page([]),
    [`GET /api/v1/tournaments/${e.tournament_id}/seasons`]: { data: [], page: { limit: 0, next_cursor: null } },
    [`GET /api/v1/events/${e.id}`]: { data: e },
    [`GET /api/v1/events/${e.id}/slices`]: { data: [], page: { limit: 0, next_cursor: null } },
    [`GET /api/v1/events/${e.id}/odds`]: { data: [], page: { limit: 0, next_cursor: null } },
    'GET /api/v1/changes': page([]),
    [`GET /api/v1/events/${e.id}/extra`]: { data: extra },
    'GET /api/v1/status': { data: status() },
  })

  async function open(name: string, over: Partial<EventExtra> = {}) {
    const e = ev(name)
    mockFetch(routes(e, { ...extraOf(name), ...over }))
    ;({ w } = await mountScreen(EventDetailScreen, `/events/${e.id}`, '/events/:id'))
    await flush()
    await flush()
    return w.find('[data-testid="event-score"]')
  }

  it('cricket: runs, wickets, overs and SofaScore’s note; venue and referee in the overview', async () => {
    const score = await open('cricket__15884177', { venue: 'Ekana Stadium', referee: 'P. Bhatt' })
    expect(score.text()).toContain('172/2 (14.4 ov) – 171/10 (19.1 ov)')
    const note = score.find('[data-testid="score-note"]')
    expect(note.text()).toBe('India beat West Indies by 8 wickets')
    expect(note.attributes('lang')).toBe('en')
    expect(w.find('[data-testid="event-overview"]').text()).toContain('Ekana Stadium')
    expect(w.find('[data-testid="event-overview"]').text()).toContain('P. Bhatt')
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('baseball: the line score table and the series', async () => {
    const score = await open('baseball__17199139')
    const table = score.find('[data-testid="line-score"] table')
    expect(table.findAll('thead th').map((x) => x.text()).slice(1)).toEqual(['1', '2', '3', '4', '5', '6', '7', '8', '9', 'R', 'H', 'E'])
    const rows = table.findAll('tbody tr')
    expect(rows[0].find('th').text()).toBe('Atlanta Braves')
    expect(rows[1].findAll('td').map((x) => x.text()).slice(-3)).toEqual(['3', '8', '0'])
    expect(score.find('[data-testid="score-detail"]').text()).toBe('Series 1 – 2')
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('MMA: winner, method and round instead of a dash', async () => {
    setLocale('tr')
    const score = await open('mma__17057712')
    expect(score.text()).toContain('UD · 3. raunt')
    expect(score.find('[data-testid="score-detail"]').text()).toBe('Alivia  Bierley kazandı · Hakem kararı (oybirliği) · 3. raunt')
  })

  it('futsal cup: no empty aggregate line, the side that went through in the overview', async () => {
    const score = await open('futsal__17256669')
    expect(score.find('[data-testid="score-detail"]').exists()).toBe(false)
    expect(score.text()).not.toContain('Agg.')
    expect(w.find('[data-testid="aggregate-through"]').text()).toBe('Boca Juniors')
  })

  it('darts, snooker, e-sports: the second line says legs, frames, maps', async () => {
    expect((await open('darts__17236047')).find('[data-testid="score-detail"]').text()).toBe('Legs 4 – 0')
    w.unmount()
    expect((await open('snooker__17264352')).find('[data-testid="score-detail"]').text()).toBe('Frames 1 – 3')
    w.unmount()
    expect((await open('esports__17264020')).find('[data-testid="score-detail"]').text()).toBe('Maps (games) 1 – 2')
  })

  it('tennis: tie-break points in the set scores', async () => {
    const score = await open('tennis__16385361')
    expect(score.text()).toContain('6-7(7) 7-6(2) 6-3 6-4')
  })
})

describe('round names and the provisional result (M5, M6)', () => {
  it('common round names in the reader’s language; others as SofaScore gives them', () => {
    const names = ['Final', 'Semifinals', 'Quarterfinals', 'Round of 128', 'Round of 32', 'Qualification Round 1', 'Group stage', 'Group B', '1/8 finals', 'Play-offs', 'Kicker Cup R2']
    expect(names.map((name) => roundName({ name, number: null }))).toEqual([
      'Final', 'Semi-finals', 'Quarter-finals', 'Round of 128', 'Round of 32', 'Qualifying round 1', 'Group stage', 'Group B', 'Round of 16', 'Play-offs', 'Kicker Cup R2',
    ])
    setLocale('tr')
    expect(names.map((name) => roundName({ name, number: null }))).toEqual([
      'Final', 'Yarı final', 'Çeyrek final', 'Son 128', 'Son 32', 'Eleme 1. tur', 'Grup aşaması', 'B Grubu', 'Son 16', 'Play-off', 'Kicker Cup R2',
    ])
    expect(roundName({ name: null, number: 5 })).toBe('5. tur')
    expect(roundName(null)).toBeNull()
  })

  const routes = (e: Event, windowHours: number | null) => ({
    'GET /api/v1/sports': { data: [], page: { limit: 0, next_cursor: null } },
    'GET /api/v1/tournaments': page([]),
    [`GET /api/v1/tournaments/${e.tournament_id}/seasons`]: { data: [], page: { limit: 0, next_cursor: null } },
    [`GET /api/v1/events/${e.id}`]: { data: e },
    [`GET /api/v1/events/${e.id}/extra`]: { data: { note: null, series: null, venue: null, referee: null } },
    [`GET /api/v1/events/${e.id}/slices`]: { data: [], page: { limit: 0, next_cursor: null } },
    [`GET /api/v1/events/${e.id}/odds`]: { data: [], page: { limit: 0, next_cursor: null } },
    'GET /api/v1/changes': page([]),
    'GET /api/v1/status': { data: status() },
    'GET /api/v1/settings': { data: settingsDoc(windowHours == null ? [] : [setting('refresh.window_hours', windowHours)]) },
  })

  it('the header shows the round in Turkish and no "Provisional" badge; the facts say why and until when', async () => {
    setLocale('tr')
    const e = ev('futsal__17256669')
    e.quality = { ...e.quality, settlement: 'provisional', provisional: true }
    mockFetch(routes(e, 72))
    ;({ w } = await mountScreen(EventDetailScreen, `/events/${e.id}`, '/events/:id'))
    await flush()
    await flush()
    expect(w.text()).toContain('Çeyrek final')
    expect(w.text()).not.toContain('Quarterfinals')
    expect(w.find('[data-testid="event-score"]').text()).not.toContain(t('ui.status.settlement.provisional'))
    const why = w.find('[data-testid="settlement-why"]')
    expect(why.text()).toContain(t('ui.eventDetail.provisionalWhy', { h: 72 }))
    expect(why.find('time').attributes('datetime')).toBe(new Date(Date.parse(e.start_utc!) + 72 * 3600_000).toISOString())
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('without the window setting the explanation is plain; a final result has none', async () => {
    const e = ev('futsal__17256669')
    e.quality = { ...e.quality, settlement: 'provisional', provisional: true }
    mockFetch(routes(e, null))
    ;({ w } = await mountScreen(EventDetailScreen, `/events/${e.id}`, '/events/:id'))
    await flush()
    await flush()
    expect(w.find('[data-testid="settlement-why"]').text()).toBe(t('ui.eventDetail.provisionalWhyPlain'))
    w.unmount()
    mockFetch(routes(ev('futsal__17256669'), 72))
    ;({ w } = await mountScreen(EventDetailScreen, '/events/17256669', '/events/:id'))
    await flush()
    expect(w.find('[data-testid="settlement"]').text()).toBe(t('ui.eventDetail.settled.final'))
    expect(w.find('[data-testid="settlement-why"]').exists()).toBe(false)
  })
})
