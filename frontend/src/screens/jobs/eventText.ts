import { i18n } from '@/i18n'
import type { JobEventMessage } from './jobStream'
import { breakerText, faceText, phaseText } from './jobText'
import { num, pct } from '@/ui/time'

/**
 * One line of a job's log in words (6.9), translated by event type and `code` (#39). A log line without a
 * known code is the server's English text and is shown as it is (`raw`), as log messages are (6.14).
 */
export type LogLine = { seq: number; ts: number; type: string; text: string; raw?: boolean; eventId?: string }

const t = (key: string, args?: Record<string, unknown>) => i18n.global.t(key, args ?? {})

function known(key: string) {
  return i18n.global.te(key, 'en')
}

/** A coded text: `ui.job.code.<code>` with its params, or null when the UI does not know the code. */
export function codeText(code: unknown, params: unknown): string | null {
  if (typeof code !== 'string' || !known(`ui.job.code.${code}`)) return null
  const p = params && typeof params === 'object' ? (params as Record<string, unknown>) : {}
  const args: Record<string, unknown> = { ...p }
  if (typeof p.reason === 'string') args.reason = breakerText(p.reason)
  return t(`ui.job.code.${code}`, args)
}

export function logLine(e: JobEventMessage): LogLine {
  const d = e.data
  const base = { seq: e.seq, ts: e.ts_ms, type: e.type }
  switch (e.type) {
    case 'started': {
      const origin = d.origin && typeof d.origin === 'object' ? (d.origin as Record<string, unknown>) : {}
      return { ...base, text: t('ui.job.event.started', { face: faceText(String(origin.face ?? '')) }) }
    }
    case 'phase':
      return {
        ...base,
        text: t('ui.job.event.phase', { phase: phaseText(String(d.phase ?? '')), i: String(d.phase_index ?? '?'), n: String(d.phase_count ?? '?') }),
      }
    case 'progress':
      return { ...base, text: t('ui.job.event.progress', { percent: pct(Number(d.percent) || 0), done: num(Number(d.done) || 0), total: num(Number(d.total) || 0) }) }
    case 'log': {
      const coded = codeText(d.code, d.params ?? d)
      if (coded) return { ...base, text: coded }
      return { ...base, text: String(d.message ?? ''), raw: true }
    }
    case 'failed': {
      const id = d.match_id ?? d.item
      if (id != null) return { ...base, text: t('ui.job.event.failed', { id: String(id) }), eventId: String(id) }
      return { ...base, text: t('ui.job.event.failedCount', { n: num(Number(d.failed_count) || 0) }) }
    }
    case 'breaker':
      return { ...base, text: t('ui.job.event.breaker', { reason: breakerText(String(d.reason ?? '')) }) }
    case 'cancel_requested':
      return { ...base, text: t('ui.job.event.cancelRequested') }
    case 'finished': {
      const state = String(d.state ?? '')
      const stateText = known(`ui.status.job.${state}`) ? t(`ui.status.job.${state}`) : state
      const coded = codeText(d.code, d.params)
      const error = d.error && typeof d.error === 'object' ? (d.error as Record<string, unknown>) : null
      const errorCode = error && typeof error.code === 'string' && known(`ui.error.${error.code}`) ? t(`ui.error.${error.code}`) : null
      return { ...base, text: t('ui.job.event.finished', { state: stateText }) + (coded ? ` ${coded}` : errorCode ? ` ${errorCode}` : '') }
    }
    default:
      return { ...base, text: e.type, raw: true }
  }
}
