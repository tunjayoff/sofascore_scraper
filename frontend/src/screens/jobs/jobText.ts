import { ref } from 'vue'
import { i18n } from '@/i18n'
import type { BackupJobSpec, ExportJobSpec, FollowRecord, Job, JobKind, JobState } from '@/api/v1/schema'
import type { StartJobBody } from '@/api/v1/client'
import { seasonName, tournamentNames } from '@/screens/events/eventText'

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

/**
 * A job's kind in words. Some kinds have a variant by their spec (FX-14b): a download of the season lists
 * only ("Season list"), the clear of one league, a real restore ("Restore") next to the backup check, and
 * the scheduled clean-up of old odds snapshots (`clear` with `scope: history`, the `prune-history` task).
 */
export function jobKindText(kind: string, spec?: unknown): string {
  const s = asSpec(spec)
  if (kind === 'sync' && isSeasonList(s)) return t('ui.job.kind.seasonList')
  if (kind === 'clear' && s.scope === 'history') return t('ui.job.kind.pruneHistory')
  if (kind === 'clear' && typeof s.tournament_id === 'number') return t('ui.job.kind.clearLeague')
  if (kind === 'restore' && s.dry_run === false) return t('ui.job.kind.restoreReal')
  const key = `ui.job.kind.${kind}`
  return i18n.global.te(key, 'en') ? t(key) : kind
}

/**
 * A download of the season lists only: `only: "seasons"`, in the body that starts it and, since FX-20, in the
 * spec the job records; `mode: "seasons"` in the records of a server before FX-20.
 */
export function isSeasonList(spec: unknown): boolean {
  const s = asSpec(spec)
  return s.only === 'seasons' || s.mode === 'seasons'
}

/** The kind of one job in words, with its spec's variant. */
export function jobLabel(job: Pick<Job, 'kind' | 'spec'>): string {
  return jobKindText(job.kind, job.spec)
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
  follows?: string[] | null
  only?: string | null
  event_ids?: number[] | null
  tournament_id?: number | null
  season_id?: number | null
  older_than?: string | null
  dry_run?: boolean
  /** The names of the follows and leagues the job names, when it started (FX-20): `{"team:42": "Arsenal"}`. */
  names?: Record<string, string> | null
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

/** The names a job recorded when it started (FX-20), by follow id. */
export function jobNames(spec: unknown): Record<string, string> {
  const names = asSpec(spec).names
  return names && typeof names === 'object' ? names : {}
}

/**
 * A league by its name (FX-14a): the stored catalog's name (`loadTournaments` in events/eventText.ts, read
 * once by the screens that show jobs), else the name the job recorded (FX-20; a league whose data was
 * deleted is no longer in the catalog), else the name the job's progress reported, else "League #17".
 */
export function leagueName(id: number, progress?: unknown, names: Record<string, string> = {}): string {
  const known = tournamentNames.value.get(id)?.name ?? names[`tournament:${id}`]
  if (known) return known
  const p = readProgress(progress)
  return p.leagueName ?? t('ui.job.target.tournament', { id })
}

/**
 * Names of follows by id (`team:42` → "Arsenal"), noted by the screens that read the follows anyway
 * (Leagues & follows, a follow's page, the quick search): a job naming a team, a player or a match follow
 * shows that name, else "Team #42". Nothing is requested for it.
 */
export const followNames = ref<Map<string, string>>(new Map())

export function noteFollowNames(list: Pick<FollowRecord, 'id' | 'name'>[]) {
  const next = new Map(followNames.value)
  for (const f of list) next.set(f.id, f.name)
  followNames.value = next
}

/**
 * "Arsenal", "Premier League", else "Team #42": a follow id (`<kind>:<id>`) in words; the follows read in
 * this tab first, then the name the job recorded (FX-20).
 */
export function followName(id: string, progress?: unknown, names: Record<string, string> = {}): string {
  const known = followNames.value.get(id) ?? names[id]
  if (known) return known
  const [kind, raw] = id.split(':')
  const n = Number(raw)
  if (kind === 'tournament' && n > 0) return leagueName(n, progress, names)
  const key = `ui.job.target.${kind}`
  return kind !== 'tournament' && i18n.global.te(key, 'en') ? t(key, { id: raw }) : id
}

/** The follow ids of a download's spec (`follows`, FX-13/FX-19). */
export function jobFollows(job: Pick<Job, 'spec'>): string[] {
  const follows = asSpec(job.spec).follows
  return Array.isArray(follows) ? follows.filter((x): x is string => typeof x === 'string') : []
}

/** The one league a job works on, or null (all leagues, several, a team or a player, or not a league job). */
export function jobLeague(job: Pick<Job, 'spec'>): number | null {
  const spec = asSpec(job.spec)
  const selections = Array.isArray(spec.selections) ? spec.selections : []
  const ids = new Set(selections.map((s) => s.league_id).filter((x): x is number => typeof x === 'number' && x > 0))
  if (typeof spec.league_id === 'number' && spec.league_id > 0) ids.add(spec.league_id)
  if (typeof spec.tournament_id === 'number' && spec.tournament_id > 0) ids.add(spec.tournament_id)
  for (const f of jobFollows(job)) {
    const m = /^tournament:(\d+)$/.exec(f)
    if (!m) return null
    ids.add(Number(m[1]))
  }
  return ids.size === 1 ? [...ids][0] : null
}

/** "All leagues", "Premier League", "2 leagues", "3 matches". */
export function jobTarget(job: Pick<Job, 'kind' | 'spec'> & { progress?: unknown }): string {
  const spec = asSpec(job.spec)
  const names = jobNames(spec)
  const selections = Array.isArray(spec.selections) ? spec.selections : []
  if (selections.length) {
    const events = selections.reduce((n, s) => n + (Array.isArray(s.match_ids) ? s.match_ids.length : 0), 0)
    if (events) return t('ui.job.target.events', { n: events })
    if (selections.length === 1 && selections[0].league_id) return leagueName(selections[0].league_id, job.progress, names)
    return t('ui.job.target.tournaments', { n: selections.length })
  }
  if (spec.league_id) return leagueName(spec.league_id, job.progress, names)
  const follows = jobFollows(job)
  if (follows.length === 1) return followName(follows[0], job.progress, names)
  if (follows.length > 1) return t('ui.job.target.follows', { n: follows.length })
  if (Array.isArray(spec.event_ids) && spec.event_ids.length) return t('ui.job.target.events', { n: spec.event_ids.length })
  if (CALLS_SOFASCORE.includes(job.kind)) return t('ui.job.target.all')
  if (job.kind === 'export') return exportText(spec)
  if (job.kind === 'clear' && typeof spec.tournament_id === 'number') {
    const name = leagueName(spec.tournament_id, job.progress, names)
    return typeof spec.season_id === 'number' ? t('ui.job.target.leagueSeason', { name, season: seasonName(spec.season_id) }) : name
  }
  if (job.kind === 'clear' && spec.scope === 'history' && spec.older_than) return t('ui.job.target.olderThan', { age: ageText(spec.older_than) })
  if ((job.kind === 'backup' || job.kind === 'clear') && spec.scope) return scopeText(spec.scope)
  if (job.kind === 'restore' && spec.name) return spec.name
  if (job.kind === 'rebuild') return t('ui.job.target.index')
  return '—'
}

/** "90d" → "90 days", "12h" → "12 hours" (the prune task's `older_than`); anything else as written. */
export function ageText(value: string): string {
  const m = /^(\d+)\s*([smhdw])$/.exec(String(value).trim())
  return m ? i18n.global.t(`ui.job.age.${m[2]}`, { n: Number(m[1]) }, Number(m[1])) : String(value)
}

const SECONDS: Record<string, number> = { s: 1, m: 60, h: 3600, d: 86400 }

/**
 * A scheduler task's interval (`every`: a number and s, m, h or d, as the config file has it) in words
 * (FX-24 F37): "20m" → "every 20 minutes" / "20 dakikada bir", "1d" → "once a day" / "günde bir", "90m"
 * → "every 90 minutes", "1.5h" → "every 90 minutes". Anything else as "every <text>".
 */
export function everyText(every: string): string {
  const m = /^(\d+(?:\.\d+)?)\s*([smhd])$/i.exec(String(every).trim())
  const seconds = m ? Number(m[1]) * SECONDS[m[2].toLowerCase()] : NaN
  if (!Number.isFinite(seconds) || seconds <= 0 || !Number.isInteger(seconds)) return t('ui.health.every', { every })
  const unit = seconds % 86400 === 0 ? 'd' : seconds % 3600 === 0 ? 'h' : seconds % 60 === 0 ? 'm' : 's'
  const n = seconds / SECONDS[unit]
  return i18n.global.t(`ui.health.everyUnit.${unit}`, { n }, n)
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
    spec.format ? textOr(`ui.exports.format.${spec.format}`, spec.format.toUpperCase()) : null,
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
  const eventIds = Array.isArray(spec.event_ids) ? spec.event_ids.filter((x) => typeof x === 'number') : []
  if (job.kind === 'refresh') return { kind: 'refresh', spec: eventIds.length ? { event_ids: eventIds } : { league_id: leagueId } }
  if (job.kind === 'fetch' && eventIds.length) return { kind: 'fetch', spec: { event_ids: eventIds } }
  const follows = jobFollows(job)
  // `ssc sync --only events`: event details only, which the API starts as a fetch (FX-20)
  if (job.kind === 'sync' && spec.only === 'events') return { kind: 'fetch', spec: { league_id: leagueId } }
  if (job.kind === 'sync' && (follows.length || isSeasonList(spec)))
    return { kind: 'sync', spec: { ...(follows.length ? { follows } : leagueId ? { league_id: leagueId } : {}), only: isSeasonList(spec) ? 'seasons' : null } }
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
