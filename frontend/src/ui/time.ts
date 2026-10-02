import { ref } from 'vue'
import { i18n } from '@/i18n'
import { timeDisplay } from '@/ui/prefs'

/**
 * Times and durations (05-web-ui.md 4.10, decision 13): the browser's local time by default, the UTC
 * value on hover and for screen readers; numbers in the locale's grouping.
 */

const loc = () => String(i18n.global.locale.value)

/**
 * An API time: ISO-8601 text (UTC; a value without a zone is read as UTC, as the server writes it) or
 * epoch milliseconds (`heartbeat_at`, `ts_ms`). null for anything else.
 */
export function parseTime(v: string | number | Date | null | undefined): Date | null {
  if (v == null || v === '') return null
  if (v instanceof Date) return Number.isNaN(v.getTime()) ? null : v
  if (typeof v === 'number') {
    const d = new Date(v)
    return Number.isNaN(d.getTime()) ? null : d
  }
  const text = String(v).trim()
  const zoned = /([zZ]|[+-]\d\d:?\d\d)$/.test(text) ? text : `${text.replace(' ', 'T')}Z`
  const d = new Date(zoned)
  return Number.isNaN(d.getTime()) ? null : d
}

export type TimeStyle = 'datetime' | 'time' | 'date' | 'seconds'

const STYLES: Record<TimeStyle, Intl.DateTimeFormatOptions> = {
  datetime: { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' },
  date: { day: 'numeric', month: 'short', year: 'numeric' },
  time: { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' },
  seconds: { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' },
}

/** The time as the user reads it: local zone unless "This browser" chose UTC. */
export function formatTime(d: Date, style: TimeStyle = 'datetime', utc = timeDisplay.value === 'utc'): string {
  const text = new Intl.DateTimeFormat(loc(), { ...STYLES[style], ...(utc ? { timeZone: 'UTC' } : {}) }).format(d)
  return utc ? `${text} UTC` : text
}

/** "2026-10-02 18:04:05 UTC": the exact value, for the hover text and screen readers. */
export function utcText(d: Date): string {
  return `${d.toISOString().slice(0, 19).replace('T', ' ')} UTC`
}

/** A shared clock for relative times; ticks every 30 s while a page shows one. */
export const now = ref(Date.now())
let users = 0
let timer: ReturnType<typeof setInterval> | null = null
export function useClock() {
  users++
  now.value = Date.now()
  if (!timer) timer = setInterval(() => (now.value = Date.now()), 30000)
  return () => {
    users = Math.max(0, users - 1)
    if (!users && timer) {
      clearInterval(timer)
      timer = null
    }
  }
}

/** "3 min ago", "in 2 h", "now". */
export function relative(d: Date, from = now.value): string {
  const seconds = Math.round((d.getTime() - from) / 1000)
  const abs = Math.abs(seconds)
  const rtf = new Intl.RelativeTimeFormat(loc(), { numeric: 'auto', style: 'short' })
  if (abs < 45) return rtf.format(0, 'second')
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), 'minute')
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), 'hour')
  return rtf.format(Math.round(seconds / 86400), 'day')
}

/** "2 h 5 min", "4 min 10 s", "35 s". */
export function duration(seconds: number | null | undefined): string {
  const t = i18n.global.t
  const n = Math.max(0, Math.round(Number(seconds) || 0))
  if (n < 60) return t('ui.time.s', { n })
  const m = Math.floor(n / 60)
  if (m < 60) {
    const s = n % 60
    return m < 10 && s ? t('ui.time.ms', { m, s }) : t('ui.time.m', { n: m })
  }
  const h = Math.floor(m / 60)
  return m % 60 ? t('ui.time.hm', { h, m: m % 60 }) : t('ui.time.h', { n: h })
}

/** Seconds between two API times, or null when one is missing. */
export function secondsBetween(a: string | number | null | undefined, b: string | number | null | undefined): number | null {
  const start = parseTime(a)
  const end = parseTime(b)
  if (!start || !end) return null
  return Math.max(0, (end.getTime() - start.getTime()) / 1000)
}

export function num(n: number | null | undefined): string {
  return Number(n ?? 0).toLocaleString(loc())
}

/** Turkish writes the percent sign first ("%72"), English after ("72%"). */
export function pct(n: number | null | undefined): string {
  const v = Math.round(Number(n) || 0)
  return loc() === 'tr' ? `%${v}` : `${v}%`
}
