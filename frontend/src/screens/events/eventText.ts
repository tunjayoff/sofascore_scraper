import { ref } from 'vue'
import { i18n } from '@/i18n'
import { v1 } from '@/api/v1/client'
import type { Event, EventListItem, Season, SliceSummary, TournamentRecord } from '@/api/v1/schema'

/**
 * Events in words (05-web-ui.md 6.5, 6.6): names, the headline score by score family, slice names, and the
 * names of tournaments and seasons by id (read once from the catalog; a missing name shows the id).
 */
const t = (key: string, args?: Record<string, unknown>) => i18n.global.t(key, args ?? {})
const te = (key: string) => i18n.global.te(key, 'en')

type AnyEvent = Pick<Event, 'participants' | 'score' | 'status'>

export function homeName(e: Pick<Event, 'participants'>) {
  return e.participants.home?.name ?? t('ui.eventDetail.side.home')
}
export function awayName(e: Pick<Event, 'participants'>) {
  return e.participants.away?.name ?? t('ui.eventDetail.side.away')
}
export function eventTitle(e: Pick<Event, 'participants'>) {
  return `${homeName(e)} – ${awayName(e)}`
}

/** The headline score: "2 – 1"; tennis and the other set sports add the sets ("6-4 3-6 7-5"). */
export function scoreText(e: AnyEvent): string {
  const s = e.score
  if (s.home == null && s.away == null) return '—'
  const head = `${s.home ?? '–'} – ${s.away ?? '–'}`
  if (s.family === 'sets' && s.sets.length && (s.format === 'games' || s.format === 'points' || s.format === 'legs'))
    return s.sets.map((x) => `${x.home ?? '–'}-${x.away ?? '–'}`).join(' ')
  return head
}

/** A second line under the score: half time, overtime, penalties, a set count, an aggregate. */
export function scoreDetail(e: Pick<Event, 'score' | 'aggregate'>): string[] {
  const s = e.score
  const out: string[] = []
  const pair = (p: { home: number | null; away: number | null } | null) => (p ? `${p.home ?? '–'} – ${p.away ?? '–'}` : null)
  if (s.family === 'football') {
    if (s.half_time) out.push(t('ui.eventDetail.score.ht', { score: pair(s.half_time) }))
    if (s.after_extra_time) out.push(t('ui.eventDetail.score.aet', { score: pair(s.after_extra_time) }))
    if (s.penalties) out.push(t('ui.eventDetail.score.pens', { score: pair(s.penalties) }))
  } else if (s.family === 'periods') {
    if (s.periods.length) out.push(s.periods.map((p) => `${p.home ?? '–'}-${p.away ?? '–'}`).join(' · '))
    if (s.overtime) out.push(t('ui.eventDetail.score.ot', { score: pair(s.overtime) }))
  } else if (s.family === 'sets') {
    if (s.sets_won) out.push(t('ui.eventDetail.score.sets', { score: pair(s.sets_won) }))
  }
  if (e.aggregate) out.push(t('ui.eventDetail.score.agg', { score: pair(e.aggregate) }))
  return out
}

export function sliceLabel(key: string): string {
  const k = `ui.slice.${key}`
  return te(k) ? t(k) : key
}

/** "6/6", "5/6"; null without a summary. */
export function summaryText(s: SliceSummary | null | undefined): string | null {
  if (!s) return null
  return `${s.ok + s.empty}/${s.selected}`
}

/** True when a selected slice is not stored yet or failed (the event needs "Fetch missing data"). */
export function needsData(e: Pick<EventListItem, 'slices_summary' | 'quality'>): boolean {
  if (e.quality.source === 'listing') return true
  const s = e.slices_summary
  return !!s && (s.error > 0 || s.ok + s.empty < s.selected)
}

// ---- names by id, read once ----
export const tournamentNames = ref<Map<number, TournamentRecord>>(new Map())
let tournamentsLoad: Promise<void> | null = null

export function loadTournaments(): Promise<void> {
  if (!tournamentsLoad) {
    tournamentsLoad = v1
      .tournaments({ limit: 200 })
      .then((r) => {
        tournamentNames.value = new Map(r.data.map((x) => [x.id, x]))
      })
      .catch(() => {
        tournamentsLoad = null
      })
  }
  return tournamentsLoad
}

export function resetNames() {
  tournamentNames.value = new Map()
  tournamentsLoad = null
  seasonNames.value = new Map()
  seasonsLoaded.clear()
}

export function tournamentName(id: number | null | undefined): string {
  if (id == null) return '—'
  return tournamentNames.value.get(id)?.name ?? `#${id}`
}

export const seasonNames = ref<Map<number, Season>>(new Map())
const seasonsLoaded = new Set<number>()

/** The seasons of a tournament, once per tournament. */
export async function loadSeasons(tournamentId: number): Promise<Season[]> {
  if (!seasonsLoaded.has(tournamentId)) {
    const list = await v1.tournamentSeasons(tournamentId)
    seasonsLoaded.add(tournamentId)
    const next = new Map(seasonNames.value)
    for (const s of list) next.set(s.id, s)
    seasonNames.value = next
  }
  return [...seasonNames.value.values()].filter((s) => s.tournament_id === tournamentId)
}

export function seasonName(id: number | null | undefined): string {
  if (id == null) return '—'
  const s = seasonNames.value.get(id)
  return s?.year ?? s?.name ?? `#${id}`
}

/** The fetch job of some events: one selection per tournament; events without a tournament are left out. */
export function fetchSelections(events: Pick<Event, 'id' | 'tournament_id'>[]) {
  const by = new Map<number, number[]>()
  let skipped = 0
  for (const e of events) {
    if (!e.tournament_id) {
      skipped++
      continue
    }
    by.set(e.tournament_id, [...(by.get(e.tournament_id) ?? []), e.id])
  }
  return { selections: [...by.entries()].map(([league_id, match_ids]) => ({ league_id, match_ids })), skipped }
}

/** A friendly view exists for these sports (decision 6); the others show the raw tree. */
export const FRIENDLY_SPORTS = ['football', 'basketball', 'tennis']
