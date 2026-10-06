import type { FollowRecord, SportSlice } from '@/api/v1/schema'

/**
 * The data selection as the server resolves it (P27, `src/sports.py` `resolve_selection`), for the
 * checklists of the follow editor and of Settings › Data (FX-14b). A name in a selection is a slice key or
 * a group (`core`, `odds`, `standings` …), and naming a group selects every slice of it.
 *
 *   defaults   `defaults.slices`; unset = the registry's `default_enabled`
 *   per sport  `slices.<sport>` = {enable, disable}, applied over the defaults (disable wins in a layer)
 *   follow     null = the two above; {include: [...]} = only these; {enable, disable} = changes over them
 *
 * `required` only says whether a slice counts for completeness: every data type can be chosen.
 */
export type Layer = { enable?: readonly string[] | null; disable?: readonly string[] | null }
export type Selection = FollowRecord['slices']

/** Older servers give no group or owner: odds are told by their key, the rest by their path. */
export function groupOf(s: Pick<SportSlice, 'key' | 'group'>): string {
  return s.group ?? (s.key.startsWith('odds') || s.key.endsWith('_odds') ? 'odds' : 'core')
}
export function ownerOf(s: Pick<SportSlice, 'owner' | 'path'>): string {
  return s.owner ?? (s.path?.startsWith('/event/') ? 'event' : 'season')
}

/** Whether a list of names (keys or groups) names this slice. */
export function named(s: Pick<SportSlice, 'key' | 'group'>, names: readonly string[] | null | undefined): boolean {
  return !!names?.some((n) => n === s.key || n === groupOf(s))
}

/** The slices a defaults value plus per-sport layers select: their keys. `base` null = the registry. */
export function resolveDefaults(slices: readonly SportSlice[], base: readonly string[] | null, layers: readonly Layer[] = []): Set<string> {
  const out = new Set<string>()
  for (const s of slices) {
    let on = base ? named(s, base) : s.default_enabled
    for (const l of layers) {
      if (named(s, l.enable)) on = true
      if (named(s, l.disable)) on = false
    }
    if (on) out.add(s.key)
  }
  return out
}

/**
 * The slices a follow downloads: its own `{include}`, its changes over the configured defaults, or the
 * defaults (`selected`, what the server's defaults select for this sport).
 */
export function followChosen(slices: readonly SportSlice[], selection: Selection | null | undefined): Set<string> {
  const sel = (selection ?? null) as { include?: string[]; enable?: string[]; disable?: string[] } | null
  const out = new Set<string>()
  for (const s of slices) {
    const base = s.selected ?? s.default_enabled
    let on: boolean
    if (!sel) on = base
    else if (Array.isArray(sel.include)) on = named(s, sel.include)
    else on = (base || named(s, sel.enable)) && !named(s, sel.disable)
    if (on) out.add(s.key)
  }
  return out
}

/** The keys in registry order; a group whose every slice is chosen is written by its name (`core`, `odds`). */
export function compress(slices: readonly SportSlice[], chosen: ReadonlySet<string>): string[] {
  const groups = new Map<string, SportSlice[]>()
  for (const s of slices) groups.set(groupOf(s), [...(groups.get(groupOf(s)) ?? []), s])
  const out: string[] = []
  const done = new Set<string>()
  for (const s of slices) {
    if (done.has(s.key) || !chosen.has(s.key)) continue
    const members = groups.get(groupOf(s)) ?? [s]
    if (members.length > 1 && members.every((m) => chosen.has(m.key))) {
      out.push(groupOf(s))
      for (const m of members) done.add(m.key)
    } else {
      out.push(s.key)
      done.add(s.key)
    }
  }
  return out
}

/** The per-sport changes that turn `base` into `wanted`: keys only, in registry order. */
export function layerFor(slices: readonly SportSlice[], base: ReadonlySet<string>, wanted: ReadonlySet<string>): { enable: string[]; disable: string[] } {
  return {
    enable: slices.filter((s) => wanted.has(s.key) && !base.has(s.key)).map((s) => s.key),
    disable: slices.filter((s) => !wanted.has(s.key) && base.has(s.key)).map((s) => s.key),
  }
}

/** One slice per key, from every sport of the registry (the global defaults apply to them all). */
export function union(sports: readonly { slices: readonly SportSlice[] }[]): SportSlice[] {
  const by = new Map<string, SportSlice>()
  for (const sp of sports) for (const s of sp.slices) if (!by.has(s.key)) by.set(s.key, s)
  return [...by.values()]
}

/** Requests per finished match: the match itself and every chosen data type of a match (6.3). */
export function costPerMatch(slices: readonly SportSlice[], chosen: ReadonlySet<string>): number {
  return 1 + slices.filter((s) => ownerOf(s) === 'event' && chosen.has(s.key)).length
}

/** The sections of a checklist: match data, betting odds, then the data of a season, a team, a player, a sport. */
export const SECTIONS = ['match', 'odds', 'season', 'team', 'player', 'sport'] as const
export type SectionKey = (typeof SECTIONS)[number]

export function sectionOf(s: SportSlice): SectionKey {
  if (groupOf(s) === 'odds') return 'odds'
  const owner = ownerOf(s)
  if (owner === 'event') return 'match'
  if (owner === 'team' || owner === 'player' || owner === 'sport') return owner
  return 'season'
}
