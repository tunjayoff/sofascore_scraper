import { i18n } from '@/i18n'
import type { JobEventMessage } from './jobStream'
import { breakerText, faceText, followName, leagueName, phaseText } from './jobText'
import { seasonName, seasonNames, sliceLabel } from '@/screens/events/eventText'
import { num, pct } from '@/ui/time'

/**
 * One line of a job's log in words (6.9), translated by event type and `code` (#39, G24). A log line
 * without a code the UI knows is the server's English text and is shown as it is (`raw`), as log messages
 * are (6.14).
 */
export type LogLine = { seq: number; ts: number; type: string; text: string; raw?: boolean; eventId?: string }

const t = (key: string, args?: Record<string, unknown>) => i18n.global.t(key, args ?? {})

function known(key: string) {
  return i18n.global.te(key, 'en')
}

/** Codes whose `reason` is the circuit breaker's (403, 429, 5xx, other); the others carry a request outcome. */
const BREAKER_CODES: readonly string[] = ['fetch_stopped_by_breaker', 'refresh_stopped_by_breaker', 'sync_breaker_stopped']

/** Why a list could not be read (`404`, `timeout`, `parse` …) in words; an unknown reason as it is. */
export function failureText(reason: string): string {
  const key = `ui.job.reason.${reason}`
  return known(key) ? t(key) : reason
}

/** What the breaker stopped ("match details", "season lists" …): the sync names it in English. */
function whatText(what: string): string {
  const key = `ui.job.what.${what.replace(/[^a-z]+/gi, '_').toLowerCase()}`
  return known(key) ? t(key) : what
}

/** A name the server put next to an id in the params (`season_year`, `league_name` …; G37, F8), else null. */
function named(p: Record<string, unknown>, ...keys: string[]): string | null {
  for (const k of keys) if (typeof p[k] === 'string' && p[k]) return p[k] as string
  return null
}

/**
 * A coded log line (`code` and `params`, G24; the codes of the download path of FX-13 and FX-19) in the
 * reader's language, or null when the UI does not know the code (the server's text is shown then). Ids in
 * the params are shown by name: a league, a season, a follow. The server sends the names of the league and
 * the seasons with their ids (G37, F8: "25/26" instead of "#76138"); a line of an older server is named from
 * the season lists the page has read.
 */
export function codeText(code: unknown, params: unknown): string | null {
  if (typeof code !== 'string' || !known(`ui.job.code.${code}`)) return null
  const p = params && typeof params === 'object' ? (params as Record<string, unknown>) : {}
  const args: Record<string, unknown> = { ...p }
  if (typeof p.reason === 'string') args.reason = BREAKER_CODES.includes(code) ? breakerText(p.reason) : failureText(p.reason)
  if ('league_id' in p) args.league = typeof p.league_id === 'number' ? (named(p, 'league_name') ?? leagueName(p.league_id)) : t('ui.job.target.all')
  if (typeof p.season_id === 'number') args.season = named(p, 'season_year', 'season_name') ?? seasonName(p.season_id)
  if (typeof p.resolved === 'number') args.resolved = named(p, 'resolved_year', 'resolved_name') ?? seasonName(p.resolved)
  if (typeof p.follow === 'string') args.follow = typeof p.name === 'string' && p.name ? p.name : followName(p.follow)
  if (typeof p.what === 'string') args.what = whatText(p.what)
  for (const k of ['count', 'stored', 'failed', 'skipped']) if (typeof p[k] === 'number') args[k] = num(p[k] as number)
  if (code === 'sync_extras_kinds') args.kinds = extrasKindsText(p.saved, p.unavailable)
  return t(`ui.job.code.${code}`, args)
}

/**
 * Maç dışı verilerin türleri (FX-23 F18; `saved` / `unavailable` dilim anahtarlarının listesi) okurun
 * dilinde ve veri türlerinin adlarıyla: "Puan durumu kaydedildi; Sezon oranları SofaScore’da yok". Boş
 * liste yazılmaz; bilinmeyen bir anahtar olduğu gibi kalır.
 */
function extrasKindsText(saved: unknown, unavailable: unknown): string {
  const names = (keys: unknown) => (Array.isArray(keys) ? keys.filter((k): k is string => typeof k === 'string').map(sliceLabel) : [])
  const s = names(saved)
  const u = names(unavailable)
  const parts: string[] = []
  if (s.length) parts.push(t('ui.job.extrasKinds.saved', { kinds: s.join(', ') }))
  if (u.length) parts.push(t('ui.job.extrasKinds.unavailable', { kinds: u.join(', ') }))
  return parts.length ? parts.join('; ') : t('ui.job.extrasKinds.none')
}

/**
 * The league whose season names a log line needs and the page does not know yet (FX-24 F8): a line names
 * a season by its id (`season_id`, `resolved`), and the season list of its league (`league_id`) has its
 * name ("2025", "25/26"); null when none is needed. A line that carries the season's name (G37) needs none.
 */
export function seasonsWanted(e: JobEventMessage): number | null {
  const d = e.data ?? {}
  const p = (d.params && typeof d.params === 'object' ? d.params : d) as Record<string, unknown>
  const league = typeof p.league_id === 'number' && p.league_id > 0 ? p.league_id : null
  const unnamed = (id: unknown, prefix: string) => typeof id === 'number' && !named(p, `${prefix}_year`, `${prefix}_name`) && !seasonNames.value.has(id)
  return league && (unnamed(p.season_id, 'season') || unnamed(p.resolved, 'resolved')) ? league : null
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
