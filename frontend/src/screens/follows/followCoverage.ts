import { v1, V1Error } from '@/api/v1/client'
import type { EventListItem, FollowRecord, FollowSummary, TournamentSummary } from '@/api/v1/schema'

/**
 * "Matches with details" of a follow (6.2; FX-24 F23, FX-26 M12): one rule for every kind. Only finished matches
 * count: a match not played yet cannot have statistics or line-ups, so an upcoming fixture is neither missing
 * nor covered. A league's numbers come from the data summary (`finished`, `finished_details`); `/status` counts
 * per tournament only, so a team's are counted here from its stored matches (`GET /events?participant=`, this
 * server only, at most `MAX_PAGES` pages of 200) and a single match's from its record. A player's matches cannot
 * be counted: the stored events do not say who played in them.
 *
 * Since B2 (G40) the server counts every follow in `/status` (`summary.follows`), a player's from the match ids
 * of its last match list: `serverCoverage` reads that row, and the counting here is only for an older server.
 */
export const MAX_PAGES = 5

export type FollowCoverage = {
  /** Finished matches. */
  matches: number
  /** Finished matches with details. */
  details: number
  /** details / matches in percent; 0 without matches. */
  coverage: number
  /** More matches are stored than were counted. */
  more: boolean
}

const FINISHED = ['completed', 'decided_without_play']

function tally(events: Pick<EventListItem, 'status' | 'quality'>[]): { matches: number; details: number } {
  let matches = 0
  let details = 0
  for (const e of events) {
    if (!FINISHED.includes(e.status.class)) continue
    matches++
    if (e.quality.source === 'event') details++
  }
  return { matches, details }
}

function result(matches: number, details: number, more = false): FollowCoverage {
  return { matches, details, coverage: matches ? Math.round((details / matches) * 1000) / 10 : 0, more }
}

/**
 * A follow's coverage from its row of `/status` (B2): null while it cannot be counted (a player whose match
 * list was not read yet).
 */
export function serverCoverage(row: Pick<FollowSummary, 'finished' | 'finished_details' | 'counted'>): FollowCoverage | null {
  if (row.counted === false) return null
  return result(row.finished, Math.min(row.finished_details, row.finished))
}

/** A league's coverage from its row of the data summary, by the same rule (an older server: its own numbers). */
export function leagueCoverage(t: Pick<TournamentSummary, 'finished' | 'finished_details' | 'matches' | 'details' | 'coverage'>): FollowCoverage {
  if (t.finished_details == null) return { matches: t.matches, details: t.details, coverage: t.coverage, more: false }
  return result(t.finished, Math.min(t.finished_details, t.finished))
}

/** The coverage of a follow, or null for a kind that cannot be counted here (a league has `/status`, a player none). */
export async function followCoverage(f: Pick<FollowRecord, 'kind' | 'entity_id'>, signal?: AbortSignal): Promise<FollowCoverage | null> {
  if (f.kind === 'event') {
    try {
      const e = await v1.event(f.entity_id, signal)
      const t = tally([e])
      return result(t.matches, t.details)
    } catch (e) {
      if (e instanceof V1Error && e.code === 'not_found') return result(0, 0)
      throw e
    }
  }
  if (f.kind !== 'team') return null
  let matches = 0
  let details = 0
  let cursor: string | null = null
  for (let page = 0; page < MAX_PAGES; page++) {
    const r = await v1.events({ participant: [f.entity_id], limit: 200, cursor }, signal)
    const t = tally(r.data)
    matches += t.matches
    details += t.details
    cursor = r.page.next_cursor ?? null
    if (!cursor) break
  }
  return result(matches, details, !!cursor)
}
