import { i18n } from '@/i18n'
import type { FollowRecord, Job } from '@/api/v1/schema'

/** Follows in words (05-web-ui.md 6.2 to 6.4). */
const t = (key: string, args?: Record<string, unknown>) => i18n.global.t(key, args ?? {})

export const FOLLOW_KINDS = ['tournament', 'team', 'player', 'event'] as const
export type FollowKind = (typeof FOLLOW_KINDS)[number]

/**
 * Team, player and single-match follows: off until the API can search them and a download can target them
 * (FX-19, FX-14b). Until then the editor shows them as "coming soon" with the reason instead of leading to
 * a follow that never downloads anything; their code paths stay, and FX-14b turns them on here.
 */
export const MORE_FOLLOW_KINDS = false

/** Whether a kind can be chosen in the editor now. */
export function followKindReady(kind: FollowKind): boolean {
  return kind === 'tournament' || MORE_FOLLOW_KINDS
}

export function followPath(f: Pick<FollowRecord, 'kind' | 'entity_id'>) {
  return `/follows/${f.kind}/${f.entity_id}`
}

/** "current", "last 2", "all", "3 chosen". */
export function seasonsText(seasons: FollowRecord['seasons'] | null | undefined): string {
  if (Array.isArray(seasons)) return t('ui.follows.seasons.chosen', { n: seasons.length })
  if (seasons === 'current' || seasons === 'all') return t(`ui.follows.seasons.${seasons}`)
  const m = typeof seasons === 'string' ? seasons.match(/^last:(\d+)$/) : null
  if (m) return t('ui.follows.seasons.last', { n: Number(m[1]) })
  return seasons ? String(seasons) : '—'
}

/** Whether a data selection turns on an odds slice (the "+odds" chip, 6.2). */
export function hasOdds(slices: FollowRecord['slices']): boolean {
  if (!slices) return false
  const include = (slices as { include?: unknown }).include
  return Array.isArray(include) && include.some((k) => String(k).startsWith('odds'))
}

export function dataText(slices: FollowRecord['slices']): string {
  return slices ? t('ui.follows.data.custom') : t('ui.follows.data.defaults')
}

/** Why a follow cannot be changed here, or null. */
export function lockReason(f: Pick<FollowRecord, 'origin' | 'writable'>, field?: string): string | null {
  if (f.origin === 'config') return t('ui.follows.lock.config')
  if (field && !f.writable.includes(field)) return f.origin === 'legacy' ? t('ui.follows.lock.legacy') : t('ui.follows.lock.field')
  return null
}

/**
 * The newest finished sync that included a tournament follow: one for that tournament, or one of every
 * follow (a sync without a target). The job list has no target filter yet (05-web-ui.md 7.3 G12).
 */
export function lastSyncOf(f: Pick<FollowRecord, 'kind' | 'entity_id'>, jobs: Job[]): Job | null {
  return jobs.find((j) => !!j.finished_at && syncIncludes(f, j)) ?? null
}

/** A sync job that worked on this tournament follow: its own, or one of every follow. */
export function syncIncludes(f: Pick<FollowRecord, 'kind' | 'entity_id'>, j: Job): boolean {
  if (f.kind !== 'tournament' || j.kind !== 'sync') return false
  const spec = (j.spec ?? {}) as { league_id?: number | null; selections?: { league_id?: number }[] | null }
  const selections = Array.isArray(spec.selections) ? spec.selections : []
  if (spec.league_id === f.entity_id || selections.some((s) => s.league_id === f.entity_id)) return true
  return !spec.league_id && !selections.length
}
