import { ref } from 'vue'

/**
 * The time a running download still needs (FX-24 F17). The server's estimate (`progress.eta_seconds`) was the
 * phase's average pace since it began: the first matches of a phase often go faster than the rest (a match
 * already stored is passed over, the request budget starts full), so at a low request rate it was about
 * twice too optimistic in the end-to-end test (33 of 70 matches: "1 min 11 s left"; it took 2.5 min). Since
 * FX-26 (M14) the server takes the longest of that average, the pace of the last minutes and, for team, player
 * and match follows, the requests still to send at the job's measured request rate.
 *
 * The UI also measures the pace of the last minutes itself, from the progress it reads (`/status` every 15 s
 * on Overview, every event on Job detail), and shows the longer of the two estimates: one rule on both sides,
 * so the shown time never drops below the server's.
 */

/** The pace is measured over this much of the recent past. */
export const ETA_WINDOW_MS = 3 * 60_000
/** A measured pace needs at least this span and this many steps. */
export const ETA_MIN_SPAN_MS = 30_000
export const ETA_MIN_STEPS = 2

type Sample = { at: number; done: number }
type Track = { phase: string | null; total: number; samples: Sample[] }

const tracks = new Map<string, Track>()
/** Bumped on every new sample, so that estimates in computed values follow. */
const version = ref(0)

type Progress = { phase: string | null; done: number | null; total: number | null; eta: number | null }

/** Notes the progress of a running job, as read at `at` (epoch ms). A new phase or total starts again. */
export function noteProgress(jobId: string, p: Progress, at = Date.now()) {
  if (p.done == null || !p.total) return
  let track = tracks.get(jobId)
  if (!track || track.phase !== p.phase || track.total !== p.total) {
    track = { phase: p.phase, total: p.total, samples: [] }
    tracks.set(jobId, track)
    // a page follows few jobs; the oldest are forgotten
    while (tracks.size > 8) tracks.delete(tracks.keys().next().value as string)
  }
  const last = track.samples[track.samples.length - 1]
  if (last && last.done === p.done && at - last.at < ETA_WINDOW_MS / 2) return
  if (last && p.done < last.done) track.samples = []
  track.samples.push({ at, done: p.done })
  const from = at - ETA_WINDOW_MS
  while (track.samples.length > 2 && track.samples[1].at <= from) track.samples.shift()
  version.value++
}

/** Seconds left at the pace of the last minutes, or null while too little was measured. */
export function measuredEta(jobId: string, p: Progress): number | null {
  void version.value
  const track = tracks.get(jobId)
  if (!track || track.phase !== p.phase || track.total !== p.total || p.done == null || !p.total) return null
  const s = track.samples
  if (s.length < 2) return null
  const first = s[0]
  const last = s[s.length - 1]
  const span = last.at - first.at
  const steps = last.done - first.done
  if (span < ETA_MIN_SPAN_MS || steps < ETA_MIN_STEPS) return null
  const left = Math.max(0, p.total - p.done)
  return left ? Math.round((left * span) / steps / 1000) : null
}

/** The estimate shown: the longer of the server's and the measured one; either alone when the other is missing. */
export function etaSeconds(jobId: string | null | undefined, p: Progress): number | null {
  const server = p.eta != null && p.eta > 0 ? p.eta : null
  const measured = jobId ? measuredEta(jobId, p) : null
  if (server == null) return measured
  if (measured == null) return server
  return Math.max(server, measured)
}

/** For tests: forget every measurement. */
export function resetEta() {
  tracks.clear()
  version.value++
}
