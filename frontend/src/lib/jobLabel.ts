import type { FetchPayload } from '@/api/client'

type T = (key: string, values?: Record<string, unknown>) => string

/** A human title for a job from its request payload, e.g. "LaLiga · 2 sezon". */
export function jobTitle(payload: FetchPayload | null | undefined, nameOf: (id: number) => string, t: T): string {
  const p = payload || {}
  const sel = Array.isArray(p.selections) ? p.selections : []
  const leagueIds = [...new Set(sel.map((s) => Number(s.league_id)))]
  const seasons = sel.reduce((n, s) => n + (s.season_ids?.length || 0), 0)
  const matches = sel.reduce((n, s) => n + (s.match_ids?.length || 0), 0)

  if (p.mode === 'details' || matches > 0) {
    return leagueIds.length === 1
      ? t('job.label.details', { league: nameOf(leagueIds[0]), n: matches })
      : t('job.label.detailsShort', { n: matches })
  }
  if (seasons > 0) {
    return leagueIds.length === 1
      ? t('job.label.seasons', { league: nameOf(leagueIds[0]), n: seasons })
      : t('job.label.multi', { leagues: leagueIds.length, n: seasons })
  }
  if (p.league_id != null) return t('job.label.league', { league: nameOf(Number(p.league_id)) })
  return t('job.label.all')
}

export type JobTone = 'running' | 'ok' | 'warn' | 'error' | 'neutral'

type WarnFields = { schedule_empty_seasons?: number; circuit_breaker_triggered?: boolean; matches_failed?: number }

/** A finished job worth a second look: empty seasons, failed matches, or stopped early by the breaker. */
export function hasWarnings(j: WarnFields): boolean {
  return !!(j.schedule_empty_seasons || j.circuit_breaker_triggered || j.matches_failed)
}

/** The backend mixes "Running"/"Completed" (live mirror) and "running"/"completed" (job rows). */
export function jobStatus(status: string, cancelRequested: boolean | undefined, warn: boolean, t: T) {
  const s = String(status || '').toLowerCase()
  if (s === 'running') {
    return cancelRequested
      ? { text: t('job.cancelling'), tone: 'neutral' as JobTone }
      : { text: t('job.running'), tone: 'running' as JobTone }
  }
  if (s === 'completed') {
    return warn
      ? { text: t('job.completedWarn'), tone: 'warn' as JobTone }
      : { text: t('job.completed'), tone: 'ok' as JobTone }
  }
  if (s === 'cancelled') return { text: t('job.cancelled'), tone: 'neutral' as JobTone }
  if (s === 'interrupted') return { text: t('job.interrupted'), tone: 'warn' as JobTone }
  if (s === 'failed') return { text: t('job.failed'), tone: 'error' as JobTone }
  return { text: status, tone: 'neutral' as JobTone }
}

export const toneBadge: Record<JobTone, string> = {
  running: 'badge badge-info',
  ok: 'badge badge-ok',
  warn: 'badge badge-warn',
  error: 'badge badge-danger',
  neutral: 'badge badge-neutral',
}

export function formatWhen(iso: string | null | undefined, locale: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(locale, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}
