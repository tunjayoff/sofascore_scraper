import { ApiError, type UpstreamReason } from '@/api/client'
import { i18n } from '@/i18n'
import { errorText } from '@/lib/toast'

/** Reasons the server can give for a failed SofaScore request (src/web/upstream.py). */
export const UPSTREAM_REASONS: readonly UpstreamReason[] = [
  'blocked',
  'browser',
  'rate_limited',
  'network',
  'not_found',
  'upstream',
]

/** The typed cause of a failed SofaScore request, or null for any other error. */
export function upstreamReason(e: unknown): UpstreamReason | null {
  if (!(e instanceof ApiError) || !e.reason) return null
  return (UPSTREAM_REASONS as readonly string[]).includes(e.reason) ? (e.reason as UpstreamReason) : null
}

/**
 * What to tell the user when a request that goes to SofaScore failed: the translated cause
 * with a next step when the server named one, otherwise the plain error text.
 */
export function upstreamText(e: unknown): string {
  const reason = upstreamReason(e)
  if (!reason) return errorText(e)
  const text = i18n.global.t(`upstream.${reason}`)
  // Being blocked is where the connection check in Settings tells the user more
  return reason === 'blocked' ? `${text} ${i18n.global.t('upstream.testHint')}` : text
}
