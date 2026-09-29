/** Pure helpers for SofaScore-like match detail rendering. */

export type StatPeriod = {
  period?: string
  groups?: Array<{
    groupName?: string
    statisticsItems?: Array<{
      name?: string
      home?: string | number
      away?: string | number
      homeValue?: number
      awayValue?: number
      key?: string
    }>
  }>
}

export function parseStatistics(raw: unknown): StatPeriod[] {
  if (Array.isArray(raw) && raw.length) return raw as StatPeriod[]
  if (raw && typeof raw === 'object') {
    const s = (raw as { statistics?: unknown }).statistics
    if (Array.isArray(s) && s.length) return s as StatPeriod[]
  }
  return []
}

export function parseIncidents(raw: unknown): any[] {
  if (Array.isArray(raw)) return raw
  if (raw && typeof raw === 'object') {
    const list = (raw as { incidents?: unknown }).incidents
    if (Array.isArray(list)) return list
  }
  return []
}

export function splitLineupPlayers(players: any[] | undefined) {
  const list = Array.isArray(players) ? players : []
  const starters = list.filter((p) => !p?.substitute)
  const subs = list.filter((p) => !!p?.substitute)
  return { starters, subs }
}

export function playerDisplayName(p: any): string {
  return p?.player?.name || p?.name || p?.playerName || '—'
}

export function shirtOf(p: any): string | number {
  return p?.jerseyNumber ?? p?.shirtNumber ?? p?.player?.jerseyNumber ?? ''
}

export function venueName(basic: any): string {
  const v = basic?.venue
  if (!v) return ''
  return v.name || v.stadium?.name || ''
}

export function formSequence(team: any): string[] {
  const form = team?.form
  return Array.isArray(form) ? form.map(String) : []
}
