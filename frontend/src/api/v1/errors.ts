import { i18n } from '@/i18n'
import { toast } from '@/ui/toast'
import { NETWORK, V1Error } from './client'

/**
 * What the UI says for an error, by its code (05-web-ui.md 5.2). The server's `message` is English and for
 * logs; it is shown only for a code the UI does not know, in small text under a general sentence.
 */

/** Codes with their own text under `ui.error.<code>`. */
export const KNOWN_CODES = [
  'invalid_request',
  'confirmation_required',
  'unauthorized',
  'too_many_attempts',
  'invalid_token',
  'forbidden_origin',
  'not_found',
  'job_running',
  'data_operation_running',
  'instance_running',
  'follow_exists',
  'follow_managed',
  'not_supported',
  'upstream_error',
  'blocked',
  'rate_limited',
  'storage_error',
  'config_invalid',
  'internal',
  'network',
  'bad_answer',
] as const

export type KnownCode = (typeof KNOWN_CODES)[number]

export type ErrorView = {
  code: string
  /** The translated sentence. */
  text: string
  /** The server's message (unknown code) or the store's own text (`storage_error`), shown small. */
  detail: string | null
  requestId: string | null
  /** A job to link to (409 conflicts), when the error names one. */
  jobId: string | null
  /** Upstream refusals point to Health, where the connection is checked (5.2). */
  toHealth: boolean
}

function isKnown(code: string): code is KnownCode {
  return (KNOWN_CODES as readonly string[]).includes(code)
}

function str(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v : null
}

/** The job that holds the data folder, when `details` names it (`job_id` or `holder.job_id`). */
export function conflictJobId(e: V1Error): string | null {
  const d = e.details || {}
  const holder = (d.holder && typeof d.holder === 'object' ? d.holder : {}) as Record<string, unknown>
  return str(d.job_id) ?? str(holder.job_id)
}

/** Who holds the lease, in words: "pid 4121 on srv-1", or null. */
export function holderText(e: V1Error): string | null {
  const holder = e.details?.holder
  if (!holder || typeof holder !== 'object') return null
  const h = holder as Record<string, unknown>
  const t = i18n.global.t
  const parts: string[] = []
  if (h.purpose != null) parts.push(String(h.purpose))
  if (h.pid != null) parts.push(t('ui.error.holderPid', { pid: String(h.pid) }))
  if (h.host != null) parts.push(t('ui.error.holderHost', { host: String(h.host) }))
  return parts.length ? parts.join(' · ') : null
}

export function describeError(e: unknown): ErrorView {
  const t = i18n.global.t
  if (!(e instanceof V1Error)) {
    return { code: 'unknown', text: t('ui.error.unknown'), detail: str((e as Error)?.message), requestId: null, jobId: null, toHealth: false }
  }
  const code = e.reason === 'too_many_attempts' ? 'too_many_attempts' : e.code
  let text: string
  let detail: string | null = null
  if (isKnown(code)) {
    text = t(`ui.error.${code}`)
    if (code === 'too_many_attempts' && e.retryAfter) text = t('ui.error.tooManyWait', { n: e.retryAfter })
    if (code === 'job_running' || code === 'data_operation_running' || code === 'instance_running') detail = holderText(e)
    if (code === 'storage_error') detail = str(e.details?.store_message) ?? str(e.details?.reason)
    if (code === 'not_supported') detail = str(e.message)
  } else {
    text = t('ui.error.unknown')
    detail = str(e.message)
  }
  return {
    code,
    text,
    detail,
    requestId: e.code === NETWORK ? null : e.requestId,
    jobId: conflictJobId(e),
    toHealth: code === 'blocked' || code === 'rate_limited' || code === 'upstream_error',
  }
}

/** The sentence alone, for a toast. */
export function errorText(e: unknown): string {
  const v = describeError(e)
  return v.detail && v.code !== 'storage_error' ? `${v.text} ${v.detail}` : v.text
}

/**
 * Field errors of a refused body (`invalid_request`): `details.errors[].loc` ends with the field, e.g.
 * ['body', 'values', 'client.rate']. Returns field → server message (English; shown under the field).
 */
export function fieldErrors(e: unknown): Record<string, string> {
  if (!(e instanceof V1Error) || e.code !== 'invalid_request') return {}
  const list = e.details?.errors
  if (!Array.isArray(list)) return {}
  const out: Record<string, string> = {}
  for (const item of list) {
    if (!item || typeof item !== 'object') continue
    const loc = (item as { loc?: unknown }).loc
    const field = Array.isArray(loc) && loc.length ? String(loc[loc.length - 1]) : ''
    if (field) out[field] = String((item as { message?: unknown }).message ?? '')
  }
  return out
}

/** An error as a toast, with the job it names (or `fallbackJobId`) or the way to Health as its link. */
export function toastError(e: unknown, fallbackJobId?: string | null) {
  const t = i18n.global.t
  const v = describeError(e)
  const conflict = v.code === 'job_running' || v.code === 'data_operation_running'
  const jobId = v.jobId ?? (conflict ? (fallbackJobId ?? null) : null)
  toast({
    kind: 'error',
    text: v.text,
    detail: v.detail,
    requestId: v.requestId,
    link: jobId
      ? { to: `/jobs/${encodeURIComponent(jobId)}`, label: t('ui.error.openJob') }
      : v.toHealth
        ? { to: '/system/health', label: t('ui.error.toHealth') }
        : null,
  })
}
