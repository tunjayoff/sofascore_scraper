import { i18n } from '@/i18n'
import { v1, type StartJobBody } from '@/api/v1/client'
import type { Job } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { toast } from '@/ui/toast'
import { isTerminal, jobKindText } from './jobText'

/**
 * Starts a job and says so (05-web-ui.md 3.1): a toast "… started" with a link to its Job detail, and the
 * status is read again so the job pill shows it. A refusal (409 while another job writes, 501, 400) is
 * thrown to the caller, which shows it where the user acted (a dialog keeps its input).
 */
export async function startJob(body: StartJobBody): Promise<Job> {
  const t = i18n.global.t
  const job = await v1.startJob(body)
  toast({ kind: 'ok', text: t('ui.jobs.started', { kind: jobKindText(job.kind) }), link: { to: `/jobs/${job.id}`, label: t('ui.jobs.openJob') } })
  void useStatusStore()
    .refresh()
    .catch(() => {})
  return job
}

/**
 * Reads a job every `everyMs` until it has ended (a dialog that waits for a short job, e.g. the restore
 * check). Resolves with the finished job; rejects when a read fails or `signal` aborts.
 */
export async function waitForJob(id: string, opts: { everyMs?: number; signal?: AbortSignal; onUpdate?: (job: Job) => void } = {}): Promise<Job> {
  const every = opts.everyMs ?? 1000
  for (;;) {
    if (opts.signal?.aborted) throw new DOMException('aborted', 'AbortError')
    const job = await v1.job(id, opts.signal)
    opts.onUpdate?.(job)
    if (isTerminal(job.state)) return job
    await new Promise((resolve) => setTimeout(resolve, every))
  }
}
