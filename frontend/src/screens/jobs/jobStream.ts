import { v1 } from '@/api/v1/client'

/**
 * The event log of one job over SSE (`GET /api/v1/jobs/{id}/events`, 05-web-ui.md 6.9).
 *
 * Every message has `id: <seq>`; the browser resumes a dropped connection by itself and sends the last id
 * as `Last-Event-ID`, which the server honours. Events already seen are dropped by their `seq`, so a
 * resumed stream never shows a line twice. The stream ends after the job's `finished` event, and the client
 * closes it then (otherwise the browser would reconnect for ever). Two more cases:
 *   - `stream.gap`: the position is older than the events the server keeps; the stream ends, the screen
 *     reloads the job and the stream starts again behind `oldest_seq - 1`;
 *   - the stream cannot be opened at all (501 without `sse-starlette`, 404): the EventSource is closed by
 *     the browser, and the screen polls `/jobs/{id}` every 2 s instead (`onUnavailable`).
 */
export type JobEventMessage = { job_id: string; seq: number; ts_ms: number; type: string; data: Record<string, unknown> }

export const EVENT_TYPES = ['started', 'phase', 'progress', 'log', 'failed', 'breaker', 'cancel_requested', 'finished'] as const
export const GAP_EVENT = 'stream.gap'
const CLOSED = 2 // EventSource.CLOSED

export type StreamHandlers = {
  onEvent: (e: JobEventMessage) => void
  onGap: (oldestSeq: number) => void
  /** The connection broke; `final` when the browser gave up (it will not reconnect by itself). */
  onError: (final: boolean) => void
}

export class JobStream {
  private source: EventSource | null = null
  private jobId: string
  private handlers: StreamHandlers
  lastSeq: number
  closed = false

  constructor(jobId: string, handlers: StreamHandlers, after = 0) {
    this.jobId = jobId
    this.handlers = handlers
    this.lastSeq = after
  }

  open() {
    this.closed = false
    const source = new EventSource(v1.jobEventsUrl(this.jobId, this.lastSeq))
    this.source = source
    for (const type of EVENT_TYPES) source.addEventListener(type, (e) => this.onMessage(e as MessageEvent))
    source.addEventListener(GAP_EVENT, (e) => {
      const data = parse((e as MessageEvent).data) as { oldest_seq?: unknown } | null
      this.close()
      this.handlers.onGap(Number(data?.oldest_seq) || 1)
    })
    source.onerror = () => {
      if (this.closed) return
      const final = source.readyState === CLOSED
      if (final) this.source = null
      this.handlers.onError(final)
    }
  }

  private onMessage(e: MessageEvent) {
    const data = parse(e.data) as JobEventMessage | null
    if (!data || typeof data.seq !== 'number') return
    if (data.seq <= this.lastSeq) return
    this.lastSeq = data.seq
    this.handlers.onEvent({ ...data, data: data.data && typeof data.data === 'object' ? data.data : {} })
    if (data.type === 'finished') this.close()
  }

  close() {
    this.closed = true
    this.source?.close()
    this.source = null
  }
}

function parse(text: unknown): unknown {
  try {
    return JSON.parse(String(text))
  } catch {
    return null
  }
}
