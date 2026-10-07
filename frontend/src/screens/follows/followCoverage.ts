import { v1, V1Error } from '@/api/v1/client'
import type { EventListItem, FollowRecord } from '@/api/v1/schema'

/**
 * "Matches with details" of a team or a single-match follow (6.2; FX-24 F23). `/status` counts them per
 * tournament only, so a team's are counted here from its stored matches (`GET /events?participant=`, this
 * server only, at most `MAX_PAGES` pages of 200) and a single match's from its record. A player's matches
 * cannot be counted: the stored events do not say who played in them.
 *
 * The count follows the data summary's rule (`summary.only_finished`): with it, a match counts when it has
 * ended or has details, as on Overview; without it every stored match counts.
 */
export const MAX_PAGES = 5

export type FollowCoverage = {
  matches: number
  details: number
  /** details / matches in percent; 0 without matches. */
  coverage: number
  /** More matches are stored than were counted. */
  more: boolean
}

const FINISHED = ['completed', 'decided_without_play']

function tally(events: Pick<EventListItem, 'status' | 'quality'>[], onlyFinished: boolean): { matches: number; details: number } {
  let matches = 0
  let details = 0
  for (const e of events) {
    const detailed = e.quality.source === 'event'
    if (!onlyFinished || detailed || FINISHED.includes(e.status.class)) matches++
    if (detailed) details++
  }
  return { matches, details }
}

function result(matches: number, details: number, more = false): FollowCoverage {
  return { matches, details, coverage: matches ? Math.round((details / matches) * 1000) / 10 : 0, more }
}

/** The coverage of a follow, or null for a kind that cannot be counted here (a league has `/status`, a player none). */
export async function followCoverage(f: Pick<FollowRecord, 'kind' | 'entity_id'>, onlyFinished: boolean, signal?: AbortSignal): Promise<FollowCoverage | null> {
  if (f.kind === 'event') {
    try {
      const e = await v1.event(f.entity_id, signal)
      const t = tally([e], onlyFinished)
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
    const t = tally(r.data, onlyFinished)
    matches += t.matches
    details += t.details
    cursor = r.page.next_cursor ?? null
    if (!cursor) break
  }
  return result(matches, details, !!cursor)
}
