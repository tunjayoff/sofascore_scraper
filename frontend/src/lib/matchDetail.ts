/** Pure helpers for SofaScore-like match detail rendering. */

export type StatItem = {
  name?: string
  home?: string | number
  away?: string | number
  homeValue?: number
  awayValue?: number
  key?: string
}

export type StatPeriod = {
  period?: string
  groups?: Array<{
    groupName?: string
    statisticsItems?: StatItem[]
  }>
}

type Named = { name?: string }

/** homeScore / awayScore: current, display, overtime and period1..N (set, quarter or half). */
export type Score = { current?: number; display?: number; overtime?: number } & Record<string, number | undefined>

/** The SofaScore event as saved in basic.json; only the fields the match page reads. */
export type MatchBasic = {
  homeTeam?: Named & { shortName?: string }
  awayTeam?: Named & { shortName?: string }
  homeScore?: Score
  awayScore?: Score
  tournament?: Named & { category?: { sport?: Named & { slug?: string } } }
  sport?: Named & { slug?: string }
  season?: Named & { year?: string }
  startTimestamp?: number
  status?: { description?: string }
  venue?: Named & { stadium?: Named }
  referee?: Named
}

export type Incident = {
  incidentType?: string
  incidentClass?: string
  time?: number
  addedTime?: number
  isHome?: boolean
  homeScore?: number
  awayScore?: number
  player?: Named
  playerName?: string
  playerIn?: Named
  playerOut?: Named
  text?: string
}

export type LineupPlayer = {
  player?: Named & { jerseyNumber?: string | number }
  name?: string
  playerName?: string
  jerseyNumber?: string | number
  shirtNumber?: string | number
  substitute?: boolean
}

export type TeamForm = { form?: unknown[] }

/**
 * GET /api/matches/{id}: one key per saved file (sofascore_scraper/web/routes/matches.py). Shapes vary a
 * little between SofaScore versions, so statistics and incidents go through the parsers below.
 */
export type MatchDetail = {
  basic?: MatchBasic
  statistics?: unknown
  incidents?: unknown
  lineups?: { home?: { players?: LineupPlayer[] }; away?: { players?: LineupPlayer[] } }
  h2h?: { teamDuel?: { homeWins?: number; draws?: number; awayWins?: number } | null }
  pregame_form?: { homeTeam?: TeamForm; awayTeam?: TeamForm } | null
}

export function parseStatistics(raw: unknown): StatPeriod[] {
  if (Array.isArray(raw) && raw.length) return raw as StatPeriod[]
  if (raw && typeof raw === 'object') {
    const s = (raw as { statistics?: unknown }).statistics
    if (Array.isArray(s) && s.length) return s as StatPeriod[]
  }
  return []
}

export function parseIncidents(raw: unknown): Incident[] {
  if (Array.isArray(raw)) return raw
  if (raw && typeof raw === 'object') {
    const list = (raw as { incidents?: unknown }).incidents
    if (Array.isArray(list)) return list
  }
  return []
}

export function splitLineupPlayers(players: LineupPlayer[] | undefined) {
  const list = Array.isArray(players) ? players : []
  const starters = list.filter((p) => !p?.substitute)
  const subs = list.filter((p) => !!p?.substitute)
  return { starters, subs }
}

export function playerDisplayName(p: LineupPlayer | null | undefined): string {
  return p?.player?.name || p?.name || p?.playerName || '—'
}

export function shirtOf(p: LineupPlayer | null | undefined): string | number {
  return p?.jerseyNumber ?? p?.shirtNumber ?? p?.player?.jerseyNumber ?? ''
}

export function venueName(basic: MatchBasic | null | undefined): string {
  const v = basic?.venue
  if (!v) return ''
  return v.name || v.stadium?.name || ''
}

export function formSequence(team: TeamForm | null | undefined): string[] {
  const form = team?.form
  return Array.isArray(form) ? form.map(String) : []
}
