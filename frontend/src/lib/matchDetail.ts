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

export function formatKickoff(ts: number | undefined): string {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString()
}

export function venueName(basic: any): string {
  const v = basic?.venue
  if (!v) return ''
  return v.name || v.stadium?.name || ''
}

export function possessionPair(periods: StatPeriod[]): { home: number; away: number } | null {
  for (const period of periods) {
    for (const grp of period.groups || []) {
      for (const item of grp.statisticsItems || []) {
        if (item.key === 'ballPossession' || /possession/i.test(String(item.name || ''))) {
          const h = Number(item.homeValue ?? String(item.home).replace('%', ''))
          const a = Number(item.awayValue ?? String(item.away).replace('%', ''))
          if (Number.isFinite(h) && Number.isFinite(a)) return { home: h, away: a }
        }
      }
    }
  }
  return null
}

export function incidentLabel(inc: any): string {
  const type = String(inc?.incidentType || '')
  if (type === 'goal') {
    const scorer = inc?.player?.name || inc?.playerName || 'Goal'
    const score =
      inc?.homeScore != null && inc?.awayScore != null ? ` ${inc.homeScore}–${inc.awayScore}` : ''
    return `${scorer}${score}`
  }
  if (type === 'card') {
    const who = inc?.player?.name || inc?.playerName || 'Card'
    const kind = inc?.incidentClass === 'red' || inc?.incidentClass === 'yellowRed' ? '🟥' : '🟨'
    return `${kind} ${who}`
  }
  if (type === 'substitution') {
    const inn = inc?.playerIn?.name || '—'
    const out = inc?.playerOut?.name || '—'
    return `↔ ${out} → ${inn}`
  }
  if (type === 'varDecision') {
    return `VAR ${inc?.incidentClass || ''} ${inc?.player?.name || inc?.playerName || ''}`.trim()
  }
  if (type === 'period') return String(inc?.text || 'Period')
  if (type === 'injuryTime') return `+${inc?.length ?? ''}′`
  return type || 'Event'
}

export function highlightIncidents(incs: any[], limit = 12): any[] {
  const keep = new Set(['goal', 'card', 'varDecision'])
  const filtered = incs.filter((i) => keep.has(String(i?.incidentType)))
  return (filtered.length ? filtered : incs.filter((i) => i?.incidentType !== 'injuryTime')).slice(0, limit)
}

export function formSequence(team: any): string[] {
  const form = team?.form
  return Array.isArray(form) ? form.map(String) : []
}
