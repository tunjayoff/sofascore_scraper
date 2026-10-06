import { i18n } from '@/i18n'
import type { FollowRecord, Job, TournamentHit } from '@/api/v1/schema'
import { isSeasonList, jobFollows } from '@/screens/jobs/jobText'

/** Follows in words (05-web-ui.md 6.2 to 6.4). */
const t = (key: string, args?: Record<string, unknown>) => i18n.global.t(key, args ?? {})

export const FOLLOW_KINDS = ['tournament', 'team', 'player', 'event'] as const
export type FollowKind = (typeof FOLLOW_KINDS)[number]

/**
 * Team, player and single-match follows. FX-14a kept them as "coming soon" behind this flag until the API
 * could search them and a download could target them; FX-19 built both, so FX-14b turned it on (the owner
 * decision of 2026-10-06: they work in 3.0.0). The flag stays, so a release can switch them off again.
 */
export const MORE_FOLLOW_KINDS = true

/** Whether a kind can be chosen in the editor now. */
export function followKindReady(kind: FollowKind): boolean {
  return kind === 'tournament' || MORE_FOLLOW_KINDS
}

export function followPath(f: Pick<FollowRecord, 'kind' | 'entity_id'>) {
  return `/follows/${f.kind}/${f.entity_id}`
}

/**
 * "current", "last 2", "all", "3 chosen". For a team or a player the same values are a time window
 * (FX-19): "last 12 months", "last 2 years", "as far back as SofaScore lists".
 */
export function seasonsText(seasons: FollowRecord['seasons'] | null | undefined, kind?: string): string {
  const window = kind === 'team' || kind === 'player'
  if (Array.isArray(seasons)) return t('ui.follows.seasons.chosen', { n: seasons.length })
  if (seasons === 'current' || seasons === 'all') return t(window ? `ui.follows.window.${seasons}` : `ui.follows.seasons.${seasons}`)
  const m = typeof seasons === 'string' ? seasons.match(/^last:(\d+)$/) : null
  if (m) return t(window ? 'ui.follows.window.last' : 'ui.follows.seasons.last', { n: Number(m[1]) })
  return seasons ? String(seasons) : '—'
}

/**
 * Where a search hit is from: the country of a team or a player, the category of a league; a bare country
 * code (a stored team, FX-20) in the user's language where the browser knows it.
 */
export function hitPlace(h: Pick<TournamentHit, 'country' | 'category'>): string {
  const code = h.country?.code ?? h.category?.country_code ?? null
  return h.country?.name ?? h.category?.name ?? (code ? regionName(code) : '')
}

function regionName(code: string): string {
  try {
    return new Intl.DisplayNames([String(i18n.global.locale.value)], { type: 'region', fallback: 'code' }).of(code.toUpperCase()) ?? code
  } catch {
    return code
  }
}

/** The icon of a search hit's kind (FX-20): a league, a team, a player. */
export function kindIcon(kind: string | null | undefined): 'trophy' | 'shield' | 'user' {
  return kind === 'team' ? 'shield' : kind === 'player' ? 'user' : 'trophy'
}

const isOdds = (k: unknown) => {
  const key = String(k)
  return key === 'odds' || key.startsWith('odds_') || key.endsWith('_odds')
}

/** Whether a data selection turns on an odds slice (the "+odds" chip, 6.2). */
export function hasOdds(slices: FollowRecord['slices']): boolean {
  if (!slices) return false
  const sel = slices as { include?: unknown; enable?: unknown }
  const names = [...(Array.isArray(sel.include) ? sel.include : []), ...(Array.isArray(sel.enable) ? sel.enable : [])]
  return names.some(isOdds)
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

/** The newest finished download that included this follow: its own, or one of every follow. */
export function lastSyncOf(f: Pick<FollowRecord, 'kind' | 'entity_id'>, jobs: Job[]): Job | null {
  return jobs.find((j) => !!j.finished_at && syncIncludes(f, j)) ?? null
}

/**
 * A download that worked on this follow: one naming it (`follows`, FX-13; for a league also `league_id`
 * or a selection of it), or one of every follow (a download without a target; since FX-19 it includes
 * teams, players and single matches). A season list only (`only: "seasons"`) downloads no match.
 */
export function syncIncludes(f: Pick<FollowRecord, 'kind' | 'entity_id'>, j: Job): boolean {
  if (j.kind !== 'sync') return false
  const spec = (j.spec ?? {}) as { league_id?: number | null; selections?: { league_id?: number }[] | null; only?: string | null }
  if (isSeasonList(spec)) return false
  const follows = jobFollows(j)
  if (follows.length) return follows.includes(`${f.kind}:${f.entity_id}`)
  const selections = Array.isArray(spec.selections) ? spec.selections : []
  if (spec.league_id || selections.length) return f.kind === 'tournament' && (spec.league_id === f.entity_id || selections.some((s) => s.league_id === f.entity_id))
  return true
}
