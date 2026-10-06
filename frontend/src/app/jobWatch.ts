import { watch } from 'vue'
import { i18n } from '@/i18n'
import { v1 } from '@/api/v1/client'
import type { Job } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { toast } from '@/ui/toast'
import { loadTournaments } from '@/screens/events/eventText'
import { isTerminal, jobLeague, jobTarget } from '@/screens/jobs/jobText'

/**
 * The end of a job, told wherever the user is (FX-14a). The shell (`watchJobs`) follows the jobs started
 * here and the running job `/status` reports (started here, from the command line or by the scheduler);
 * each is read every few seconds until it has ended. Then the screens that asked (`onJobEnded`) are told,
 * e.g. a follow's page reads its last download and counts again, and a download says so in a toast:
 * "Premier League downloaded: 4 matches" with "See the matches".
 */
export const JOB_WATCH_MS = 3000

/** The download kinds whose end is told in a toast. */
const TOLD: readonly string[] = ['sync', 'fetch']

type Listener = (job: Job) => void
const listeners = new Set<Listener>()
const watched = new Set<string>()
/** Jobs already told, so a job is told once even when `/status` names it again. */
const told = new Set<string>()
let active = false
let timer: ReturnType<typeof setTimeout> | null = null

/** Calls `fn` with every watched job that has ended; returns the function that stops it. */
export function onJobEnded(fn: Listener): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

/** Follows one job until it has ended (only while the shell watches; see `watchJobs`). */
export function watchJob(job: Pick<Job, 'id'>) {
  if (told.has(job.id)) return
  watched.add(job.id)
  schedule()
}

function schedule() {
  if (!active || timer || !watched.size) return
  timer = setTimeout(() => {
    timer = null
    void tick()
  }, JOB_WATCH_MS)
}

async function tick() {
  for (const id of [...watched]) {
    try {
      const job = await v1.job(id)
      if (!isTerminal(job.state)) continue
      watched.delete(id)
      told.add(id)
      await ended(job)
    } catch {
      // a job that cannot be read any more (removed, server gone) is not followed further
      watched.delete(id)
    }
  }
  schedule()
}

async function ended(job: Job) {
  for (const fn of [...listeners]) fn(job)
  if (TOLD.includes(job.kind)) {
    await loadTournaments().catch(() => {})
    tellEnd(job)
  }
}

/** The toast at the end of a download; a stopped job says nothing (the user asked for it). */
export function tellEnd(job: Job) {
  const t = i18n.global.t
  const result = (job.result ?? {}) as Record<string, unknown>
  const n = Number(result.details_done) || 0
  const failed = Number(result.failed_count) || 0
  const league = jobLeague(job)
  const name = jobTarget(job)
  const matches = { to: league ? { path: '/events', query: { tournament: String(league) } } : '/events', label: t('ui.jobDone.showMatches') }
  const openJob = { to: `/jobs/${job.id}`, label: t('ui.jobs.openJob') }
  if (job.state === 'succeeded') {
    const text =
      job.kind === 'fetch'
        ? t('ui.jobDone.fetched', { n })
        : !n
          ? t('ui.jobDone.upToDate', { name })
          : league
            ? t('ui.jobDone.synced', { name, n })
            : t('ui.jobDone.syncedAll', { n })
    toast({ kind: 'ok', text, link: matches })
  } else if (job.state === 'partial') {
    toast({ kind: 'info', text: t('ui.jobDone.partial', { name, n, failed }), link: openJob })
  } else if (job.state === 'failed' || job.state === 'interrupted') {
    toast({ kind: 'error', text: t('ui.jobDone.failed', { name }), link: openJob })
  }
}

/**
 * Started by the shell: follows the running job of `/status` (and the one before it, which may just have
 * ended between two reads) and turns the polling on. Returns the function that stops it.
 */
export function watchJobs(): () => void {
  active = true
  const status = useStatusStore()
  const stop = watch(
    () => status.activeJob?.id ?? null,
    (id, before) => {
      if (id) watchJob({ id })
      if (before && before !== id) watchJob({ id: before })
    },
    { immediate: true },
  )
  schedule()
  return () => {
    stop()
    active = false
    if (timer) clearTimeout(timer)
    timer = null
    watched.clear()
  }
}
