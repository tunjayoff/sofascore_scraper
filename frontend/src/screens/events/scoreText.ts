import { i18n } from '@/i18n'
import type { Aggregate, CricketInnings, Event, EventExtra, InningsScore, ScorePair, SetScore, SetsScore } from '@/api/v1/schema'

/**
 * The score of a match in words, by score family (05-web-ui.md 6.5, 6.6; FX-26, the multi-sport findings of
 * the live validation): the headline score, the line under it, the inning-by-inning line of baseball and the
 * result of a fight. The match page's header and the Matches list read the same text.
 *
 *  - cricket: runs, wickets and overs of each side ("172/2 (14.4 ov) – 171/10 (19.1 ov)"); SofaScore's result
 *    note ("India beat West Indies by 8 wickets") comes from the record's `extra`;
 *  - baseball: runs, and under them each inning with R, H and E, and the series of a postseason ("Seri 1 – 2");
 *  - sets: the label of the second line follows `score.format` (sets, legs, frames, games won); tennis and
 *    padel sets show the tie-break points of the set's loser in brackets ("6-7(7) 7-6(2)");
 *  - fight (MMA): the method as SofaScore abbreviates it and the round; under it the winner, the method in
 *    words and the round;
 *  - an aggregate without scores (only the side that went through) is not a score line.
 */
const t = (key: string, args?: Record<string, unknown>) => i18n.global.t(key, args ?? {})
const te = (key: string) => i18n.global.te(key, 'en')

type Scored = Pick<Event, 'participants' | 'score' | 'status'>
type WithExtra = { extra?: EventExtra | null }

const side = (n: number | null | undefined) => (n == null ? '–' : String(n))
const pair = (p: { home: number | null; away: number | null } | null | undefined) => (p ? `${side(p.home)} – ${side(p.away)}` : null)
const hasScore = (p: Pick<ScorePair, 'home' | 'away'> | null | undefined) => !!p && (p.home != null || p.away != null)

/**
 * One side's innings in cricket: "172/2 (14.4 ov)"; several innings joined with "&" (Test matches). Overs keep
 * SofaScore's notation in both languages: 14.4 is 14 overs and 4 balls, not a decimal number.
 */
export function cricketSide(innings: CricketInnings[], which: 'home' | 'away'): string {
  const mine = innings.filter((i) => i.side === which).sort((a, b) => a.number - b.number)
  return mine
    .map((i) => {
      const runs = i.wickets != null ? `${side(i.runs)}/${i.wickets}` : side(i.runs)
      return i.overs != null ? `${runs} ${t('ui.eventDetail.score.overs', { n: String(i.overs) })}` : runs
    })
    .join(' & ')
}

/** "6-7(7)": a tennis or padel set with the tie-break points of the side that lost it. */
export function setText(s: SetScore): string {
  const base = `${side(s.home)}-${side(s.away)}`
  const tb = s.tiebreak
  if (!tb || tb.home == null || tb.away == null) return base
  return `${base}(${Math.min(tb.home, tb.away)})`
}

/** The label of the count under the score: what `score.format` says is counted. */
export function setsLabelKey(s: Pick<SetsScore, 'format' | 'sets'>): string {
  switch (s.format) {
    case 'frames':
      return 'ui.eventDetail.score.frames'
    case 'games_won':
      return 'ui.eventDetail.score.maps'
    case 'legs_won':
      return 'ui.eventDetail.score.legs'
    case 'legs':
      // tek setlik dart maçında (bestOfSets 1) SofaScore set skoru vermez: sayılan leg'lerdir
      return s.sets.length ? 'ui.eventDetail.score.sets' : 'ui.eventDetail.score.legs'
    default:
      return 'ui.eventDetail.score.sets'
  }
}

/** A fight's method in words ("Hakem kararı (oybirliği)"); an unknown abbreviation as SofaScore gives it. */
export function methodName(code: string | null | undefined): string {
  if (!code) return ''
  const key = `ui.eventDetail.method.${code.trim().toUpperCase().replace(/[^A-Z0-9]/g, '')}`
  return te(key) ? t(key) : code
}

/** The headline score: "2 – 1"; set sports the sets; cricket runs/wickets (overs); a fight its method and round. */
export function scoreText(e: Scored): string {
  const s = e.score
  if (s.family === 'fight') {
    if (!s.method) return '—'
    return s.final_round != null ? `${s.method} · ${t('ui.eventDetail.score.roundShort', { n: s.final_round })}` : s.method
  }
  if (s.home == null && s.away == null) return '—'
  if (s.family === 'cricket' && s.innings.length) return `${cricketSide(s.innings, 'home') || side(s.home)} – ${cricketSide(s.innings, 'away') || side(s.away)}`
  if (s.family === 'sets' && s.sets.length && (s.format === 'games' || s.format === 'points' || s.format === 'legs')) return s.sets.map(setText).join(' ')
  return `${side(s.home)} – ${side(s.away)}`
}

/** An aggregate that has a score (a tie decided some other way gives only the winner). */
export function aggregateScored(a: Aggregate | null | undefined): boolean {
  return hasScore(a)
}

/** The lines under the score: half time, overtime, penalties, the count of sets/legs/frames, a series, an aggregate, a fight's result. */
export function scoreDetail(e: Pick<Event, 'score' | 'aggregate' | 'winner' | 'participants'> & WithExtra): string[] {
  const s = e.score
  const out: string[] = []
  if (s.family === 'football') {
    if (s.half_time) out.push(t('ui.eventDetail.score.ht', { score: pair(s.half_time) }))
    if (s.after_extra_time) out.push(t('ui.eventDetail.score.aet', { score: pair(s.after_extra_time) }))
    if (s.penalties) out.push(t('ui.eventDetail.score.pens', { score: pair(s.penalties) }))
  } else if (s.family === 'periods') {
    if (s.periods.length) out.push(s.periods.map((p) => `${side(p.home)}-${side(p.away)}`).join(' · '))
    if (s.overtime) out.push(t('ui.eventDetail.score.ot', { score: pair(s.overtime) }))
    if (s.penalties) out.push(t('ui.eventDetail.score.pens', { score: pair(s.penalties) }))
  } else if (s.family === 'sets') {
    if (s.sets_won) out.push(t(setsLabelKey(s), { score: pair(s.sets_won) }))
  } else if (s.family === 'fight') {
    const winner = e.winner === 'home' ? e.participants.home?.name : e.winner === 'away' ? e.participants.away?.name : null
    const parts = [
      winner ? t('ui.eventDetail.score.won', { name: winner }) : e.winner === 'draw' ? t('ui.eventDetail.draw') : '',
      methodName(s.method),
      s.final_round != null ? t('ui.eventDetail.score.round', { n: s.final_round }) : '',
    ].filter(Boolean)
    if (parts.length) out.push(parts.join(' · '))
  }
  const series = e.extra?.series
  if (hasScore(series)) out.push(t('ui.eventDetail.score.series', { score: pair(series) }))
  if (aggregateScored(e.aggregate)) out.push(t('ui.eventDetail.score.agg', { score: pair(e.aggregate) }))
  return out
}

export type LineScore = { columns: string[]; home: string[]; away: string[] }

/**
 * Baseball's line score: the runs of each inning, then R (runs), H (hits) and E (errors). Null without innings.
 * Innings beyond the ninth are extra innings and keep their number.
 */
export function lineScore(s: Event['score']): LineScore | null {
  if (s.family !== 'innings' || !s.innings.length) return null
  const sc = s as InningsScore
  const innings = [...sc.innings].sort((a, b) => a.number - b.number)
  const columns = [...innings.map((i) => String(i.number)), t('ui.eventDetail.score.runs'), t('ui.eventDetail.score.hits'), t('ui.eventDetail.score.errors')]
  const row = (which: 'home' | 'away') => [
    ...innings.map((i) => side(i[which])),
    side(sc[which]),
    side(sc.hits?.[which]),
    side(sc.errors?.[which]),
  ]
  return { columns, home: row('home'), away: row('away') }
}
