import { i18n } from '@/i18n'
import type { Job, JobKind, JobState } from '@/api/v1/schema'
import type { StartJobBody } from '@/api/v1/client'

/**
 * Jobs in words (05-web-ui.md 6.8, 6.9). Everything shown is built from codes and numbers of the job
 * (kind, state, spec, progress); the server's free text is never shown as the label of anything.
 */

const t = (key: string, args?: Record<string, unknown>) => i18n.global.t(key, args ?? {})

export const TERMINAL: readonly JobState[] = ['succeeded', 'partial', 'failed', 'cancelled', 'interrupted']
export const isTerminal = (s: JobState) => TERMINAL.includes(s)

/** The kinds `POST /api/v1/jobs` starts today; the others answer 501 until P21 (05-web-ui.md 7.1). */
export const STARTABLE: readonly JobKind[] = ['sync', 'fetch', 'refresh']

export function jobKindText(kind: string): string {
  const key = `ui.job.kind.${kind}`
  return i18n.global.te(key, 'en') ? t(key) : kind
}

export function faceText(face: string): string {
  const key = `ui.job.face.${face}`
  return i18n.global.te(key, 'en') ? t(key) : face
}

type Selection = { league_id?: number | null; season_ids?: number[] | null; match_ids?: number[] | null }
type Spec = { mode?: string; league_id?: number | null; selections?: Selection[] | null }

function asSpec(spec: unknown): Spec {
  return spec && typeof spec === 'object' ? (spec as Spec) : {}
}

/** "All followed tournaments", "Tournament #17", "2 tournaments", "3 events". */
export function jobTarget(job: Pick<Job, 'kind' | 'spec'>): string {
  const spec = asSpec(job.spec)
  const selections = Array.isArray(spec.selections) ? spec.selections : []
  if (selections.length) {
    const events = selections.reduce((n, s) => n + (Array.isArray(s.match_ids) ? s.match_ids.length : 0), 0)
    if (events) return t('ui.job.target.events', { n: events })
    if (selections.length === 1 && selections[0].league_id) return t('ui.job.target.tournament', { id: selections[0].league_id })
    return t('ui.job.target.tournaments', { n: selections.length })
  }
  if (spec.league_id) return t('ui.job.target.tournament', { id: spec.league_id })
  if (STARTABLE.includes(job.kind)) return t('ui.job.target.all')
  return '—'
}

/** The progress object of a job (JobProgress.detail() + percent), read defensively. */
export type ProgressView = {
  percent: number | null
  phase: string | null
  phaseIndex: number | null
  phaseCount: number | null
  done: number | null
  total: number | null
  eta: number | null
  wait: { reason: string; until: number } | null
  failedCount: number
  failed: { match_id?: string | number; league_id?: number }[]
  breaker: string | null
  leagueName: string | null
  seasonName: string | null
}

const n = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const s = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)

export function readProgress(raw: unknown): ProgressView {
  const p = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const wait = p.wait && typeof p.wait === 'object' ? (p.wait as Record<string, unknown>) : null
  return {
    percent: n(p.percent) ?? n(p.progress),
    phase: s(p.phase),
    phaseIndex: n(p.phase_index),
    phaseCount: n(p.phase_count),
    done: n(p.done),
    total: n(p.total),
    eta: n(p.eta_seconds),
    wait: wait && n(wait.until) != null ? { reason: String(wait.reason ?? ''), until: n(wait.until)! } : null,
    failedCount: n(p.failed_count) ?? 0,
    failed: Array.isArray(p.failed) ? (p.failed as ProgressView['failed']) : [],
    breaker: s(p.breaker),
    leagueName: s(p.league_name),
    seasonName: s(p.season_name),
  }
}

/** Percent of a job: the progress for a running one, 100 for a finished one, null when unknown. */
export function jobPercent(job: Pick<Job, 'state' | 'progress'>): number | null {
  if (job.state === 'succeeded' || job.state === 'partial') return 100
  return readProgress(job.progress).percent
}

export function phaseText(phase: string | null): string {
  if (!phase) return ''
  const key = `ui.job.phase.${phase}`
  return i18n.global.te(key, 'en') ? t(key) : phase
}

/** "SofaScore asked us to slow down", "SofaScore refused a request". */
export function waitText(reason: string): string {
  const key = `ui.job.wait.${reason}`
  return i18n.global.te(key, 'en') ? t(key) : t('ui.job.wait.other')
}

/** Why the circuit breaker stopped a job: '403', '429', '5xx' or 'other'. */
export function breakerText(reason: string | null | undefined): string {
  const key = `ui.job.breaker.${reason}`
  return reason && i18n.global.te(key, 'en') ? t(key) : t('ui.job.breaker.other')
}

/** The body that starts the same job again, or null for a kind the API cannot start yet. */
export function rerunBody(job: Pick<Job, 'kind' | 'spec'>): StartJobBody | null {
  const spec = asSpec(job.spec)
  const leagueId = typeof spec.league_id === 'number' && spec.league_id > 0 ? spec.league_id : null
  if (job.kind === 'refresh') return { kind: 'refresh', spec: { league_id: leagueId } }
  if (job.kind !== 'sync' && job.kind !== 'fetch') return null
  const selections = (Array.isArray(spec.selections) ? spec.selections : [])
    .filter((sel) => typeof sel.league_id === 'number' && sel.league_id > 0)
    .map((sel) => ({
      league_id: sel.league_id as number,
      season_ids: Array.isArray(sel.season_ids) ? sel.season_ids : [],
      match_ids: Array.isArray(sel.match_ids) ? sel.match_ids : [],
    }))
  return { kind: job.kind, spec: selections.length ? { league_id: leagueId, selections } : { league_id: leagueId } }
}

/** The error code of a finished job, or of its breaker, in words. */
export function jobErrorText(job: Pick<Job, 'error'>): string | null {
  const code = job.error?.code
  if (!code) return null
  const key = `ui.error.${code}`
  return i18n.global.te(key, 'en') ? t(key) : code
}
