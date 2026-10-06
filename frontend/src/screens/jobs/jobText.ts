import { i18n } from '@/i18n'
import type { BackupJobSpec, ExportJobSpec, Job, JobKind, JobState } from '@/api/v1/schema'
import type { StartJobBody } from '@/api/v1/client'
import { tournamentNames } from '@/screens/events/eventText'

/**
 * Jobs in words (05-web-ui.md 6.8, 6.9). Everything shown is built from codes and numbers of the job
 * (kind, state, spec, progress); the server's free text is never shown as the label of anything.
 */

const t = (key: string, args?: Record<string, unknown>) => i18n.global.t(key, args ?? {})

export const TERMINAL: readonly JobState[] = ['succeeded', 'partial', 'failed', 'cancelled', 'interrupted']
export const isTerminal = (s: JobState) => TERMINAL.includes(s)

/** The kinds that need no form, offered by "Start a job" (6.8); each asks first in a ConfirmDialog. */
export const STARTABLE: readonly JobKind[] = ['sync', 'fetch', 'refresh', 'rebuild']

/** The kinds that send requests to SofaScore; the others work on the data folder only (4.1, principle 3). */
export const CALLS_SOFASCORE: readonly JobKind[] = ['sync', 'fetch', 'refresh']

export function jobKindText(kind: string): string {
  const key = `ui.job.kind.${kind}`
  return i18n.global.te(key, 'en') ? t(key) : kind
}

export function faceText(face: string): string {
  const key = `ui.job.face.${face}`
  return i18n.global.te(key, 'en') ? t(key) : face
}

type Selection = { league_id?: number | null; season_ids?: number[] | null; match_ids?: number[] | null }
type Spec = {
  mode?: string
  league_id?: number | null
  selections?: Selection[] | null
  // the data jobs
  dataset?: string
  format?: string
  schema?: string
  profile?: string | null
  scope?: string
  name?: string
}

function asSpec(spec: unknown): Spec {
  return spec && typeof spec === 'object' ? (spec as Spec) : {}
}

/**
 * A league by its name (FX-14a): the stored catalog's name (`loadTournaments` in events/eventText.ts, read
 * once by the screens that show jobs), else the name the job's progress reported, else "League #17".
 */
export function leagueName(id: number, progress?: unknown): string {
  const known = tournamentNames.value.get(id)?.name
  if (known) return known
  const p = readProgress(progress)
  return p.leagueName ?? t('ui.job.target.tournament', { id })
}

/** The one league a job works on, or null (all leagues, several, or not a league job). */
export function jobLeague(job: Pick<Job, 'spec'>): number | null {
  const spec = asSpec(job.spec)
  const selections = Array.isArray(spec.selections) ? spec.selections : []
  const ids = new Set(selections.map((s) => s.league_id).filter((x): x is number => typeof x === 'number' && x > 0))
  if (typeof spec.league_id === 'number' && spec.league_id > 0) ids.add(spec.league_id)
  return ids.size === 1 ? [...ids][0] : null
}

/** "All leagues", "Premier League", "2 leagues", "3 matches". */
export function jobTarget(job: Pick<Job, 'kind' | 'spec'> & { progress?: unknown }): string {
  const spec = asSpec(job.spec)
  const selections = Array.isArray(spec.selections) ? spec.selections : []
  if (selections.length) {
    const events = selections.reduce((n, s) => n + (Array.isArray(s.match_ids) ? s.match_ids.length : 0), 0)
    if (events) return t('ui.job.target.events', { n: events })
    if (selections.length === 1 && selections[0].league_id) return leagueName(selections[0].league_id, job.progress)
    return t('ui.job.target.tournaments', { n: selections.length })
  }
  if (spec.league_id) return leagueName(spec.league_id, job.progress)
  if (CALLS_SOFASCORE.includes(job.kind)) return t('ui.job.target.all')
  if (job.kind === 'export') return exportText(spec)
  if ((job.kind === 'backup' || job.kind === 'clear') && spec.scope) return scopeText(spec.scope)
  if (job.kind === 'restore' && spec.name) return spec.name
  if (job.kind === 'rebuild') return t('ui.job.target.index')
  return '—'
}

function textOr(key: string, fallback: string): string {
  return i18n.global.te(key, 'en') ? t(key) : fallback
}

/** "Wide CSV (2.x columns)", "Raw · Slices · JSONL". */
export function exportText(spec: { dataset?: string; format?: string; schema?: string; profile?: string | null }): string {
  if (spec.profile === 'legacy-wide-csv') return t('ui.exports.kind.legacy')
  const parts = [
    spec.schema ? textOr(`ui.exports.schema.${spec.schema}`, spec.schema) : null,
    spec.dataset ? textOr(`ui.exports.dataset.${spec.dataset}`, spec.dataset) : null,
    spec.format ? spec.format.toUpperCase() : null,
  ]
  return parts.filter(Boolean).join(' · ') || '—'
}

/** A backup or clear scope in words. */
export function scopeText(scope: string): string {
  return textOr(`ui.scope.${scope}`, scope)
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

/** "3 / 10", with "matches" while the job fetches match details (FX-14a: counts carry their unit). */
export function countsText(p: Pick<ProgressView, 'phase' | 'done' | 'total'>, num: (n: number) => string = String): string {
  const args = { done: num(p.done ?? 0), total: num(p.total ?? 0) }
  return p.phase === 'details' ? t('ui.job.countsMatches', args) : t('ui.job.counts', args)
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

/**
 * The body that starts the same job again, or null: never for a clear (Maintenance asks for a typed word)
 * or a restore check (Backups), nor for a kind the API cannot start.
 */
export function rerunBody(job: Pick<Job, 'kind' | 'spec'>): StartJobBody | null {
  const spec = asSpec(job.spec)
  const leagueId = typeof spec.league_id === 'number' && spec.league_id > 0 ? spec.league_id : null
  if (job.kind === 'refresh') return { kind: 'refresh', spec: { league_id: leagueId } }
  if (job.kind === 'rebuild') return { kind: 'rebuild', spec: { mode: 'auto' } }
  if (job.kind === 'export') return { kind: 'export', spec: job.spec as ExportJobSpec }
  if (job.kind === 'backup') return { kind: 'backup', spec: job.spec as BackupJobSpec }
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

/** Counts of a backup a user has no use for (the outputs' delivery positions); not shown (FX-14a). */
const HIDDEN_COUNTS: readonly string[] = ['sink_cursors']

/** Whether a count of a backup is shown. */
export function countShown(key: string): boolean {
  return !HIDDEN_COUNTS.includes(key)
}

/** A count of a backup (`backup.json`: follows, jobs, v3_events …) in words, else its key. */
export function countLabel(key: string): string {
  return textOr(`ui.restore.count.${key}`, key)
}
