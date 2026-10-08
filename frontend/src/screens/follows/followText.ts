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

/** The server's longest follow name. */
export const MAX_FOLLOW_NAME = 80

/**
 * A name the server accepts for a follow of this kind (FX-26, M4): no line break, ':' or '\\', and for a league
 * no '/' either (its name goes into config/leagues.txt and the 2.x folder names). A team's, a player's or a
 * match's name keeps its '/': doubles in tennis, padel and badminton are "A / B – C / D". Used where the editor
 * fills the name itself (a picked hit, "Follow this match"); what the user types is sent as typed.
 */
export function followName(name: string, kind: string): string {
  const forbidden = kind === 'tournament' ? /\s*[:/\\]+\s*/g : /\s*[:\\]+\s*/g
  return name
    .replace(/[\r\n]+/g, ' ')
    .replace(forbidden, ' – ')
    .replace(/\s+/g, ' ')
    .replace(/^[\s–]+|[\s–]+$/g, '')
    .trim()
    .slice(0, MAX_FOLLOW_NAME)
    .trim()
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
 * Where a search hit is from: the country of a team or a player, the category of a league, in the reader's
 * language (FX-20; FX-24 F12: also SofaScore's regions and own codes). See `placeName`.
 */
export function hitPlace(h: Pick<TournamentHit, 'country' | 'category'>): string {
  return placeName(h.country?.code ?? h.category?.country_code ?? null, h.country?.name ?? h.category?.name ?? null)
}

/**
 * Bir takımın milli takım ve kadın takımı olduğu, okurun dilinde (FX-25 F26): ["Milli takım", "Kadın"].
 * SofaScore'un söylemediği (null) ya da bilinmeyen bir cinsiyet yazılmaz; erkek takımları ayrıca
 * işaretlenmez (SofaScore'da çoğunluk onlar), aynı adlı kadın takımı "Kadın" ile ayrılır.
 */
export function hitTraits(h: Pick<TournamentHit, 'gender' | 'national'> | null | undefined): string[] {
  const out: string[] = []
  if (h?.national === true) out.push(t('ui.suggest.national'))
  if (h?.gender === 'F') out.push(t('ui.suggest.women'))
  return out
}

/**
 * SofaScore's places that are no country of ISO 3166 (regions, the home nations, "World"), by the slug of
 * their English name, to the key of their name under `ui.place` (FX-24 F12). SofaScore writes them in
 * English and gives them no code, or a code of its own (`EN` for England).
 */
const PLACES: Record<string, string> = {
  england: 'england',
  scotland: 'scotland',
  wales: 'wales',
  'northern-ireland': 'northernIreland',
  europe: 'europe',
  'south-america': 'southAmerica',
  'north-central-america': 'northCentralAmerica',
  'north-america': 'northAmerica',
  'central-america': 'centralAmerica',
  asia: 'asia',
  africa: 'africa',
  oceania: 'oceania',
  world: 'world',
  international: 'international',
  'international-clubs': 'internationalClubs',
  'international-youth': 'internationalYouth',
}
/** SofaScore's own country codes (not ISO 3166) to a slug of `PLACES`. */
const OWN_CODES: Record<string, string> = { EN: 'england' }
/** English names SofaScore uses that differ from the browser's English name of the country. */
const ALIASES: Record<string, string> = { turkey: 'TR', usa: 'US', 'czech-republic': 'CZ', 'korea-republic': 'KR', 'chinese-taipei': 'TW' }

function slugOf(name: string): string {
  return name
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
}

/**
 * A country, region or category in the reader's language: a country by its code through the browser's
 * names (`Intl.DisplayNames`); SofaScore's regions and own codes ("Europe", "South America", `EN`) from the
 * locale; a country SofaScore names in English without a code ("Turkey") by that name; anything else as
 * SofaScore writes it.
 */
export function placeName(code: string | null | undefined, name: string | null | undefined): string {
  const upper = code ? code.toUpperCase() : null
  const own = upper ? OWN_CODES[upper] : undefined
  if (own) return t(`ui.place.${PLACES[own]}`)
  const byCode = upper ? regionName(upper) : null
  if (byCode) return byCode
  if (name) {
    const slug = slugOf(name)
    if (PLACES[slug]) return t(`ui.place.${PLACES[slug]}`)
    const found = ALIASES[slug] ?? englishCodes().get(slug)
    const byName = found ? regionName(found) : null
    if (byName) return byName
  }
  return name ?? upper ?? ''
}

function regionName(code: string): string | null {
  try {
    const name = new Intl.DisplayNames([String(i18n.global.locale.value)], { type: 'region', fallback: 'none' }).of(code.toUpperCase())
    return name && name !== code.toUpperCase() ? name : null
  } catch {
    return null
  }
}

/** The browser's English country names to their codes ("spain" → ES), made once. */
let codesByName: Map<string, string> | null = null
function englishCodes(): Map<string, string> {
  if (codesByName) return codesByName
  const found = new Map<string, string>()
  try {
    const names = new Intl.DisplayNames(['en'], { type: 'region', fallback: 'none' })
    for (let i = 0; i < 26; i++)
      for (let j = 0; j < 26; j++) {
        const code = String.fromCharCode(65 + i, 65 + j)
        const name = names.of(code)
        if (name && name !== code) found.set(slugOf(name), code)
      }
  } catch {
    /* this browser has no names of regions */
  }
  codesByName = found
  return found
}

/** SofaScore's placeholder for a player without a club, never shown as a team (FX-24 F32). */
const NO_TEAM = ['no team', 'no-team']

/** The team a player plays for, or null: none, or SofaScore's placeholder "No team". */
export function playerTeam(team: { name?: string | null } | null | undefined): string | null {
  const name = team?.name?.trim()
  return name && !NO_TEAM.includes(name.toLowerCase()) ? name : null
}

/** The icon of a search hit's kind (FX-20): a league, a team, a player (also one SofaScore lists as a team). */
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
