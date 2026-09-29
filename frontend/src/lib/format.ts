import { i18n } from '@/i18n'

const loc = () => String(i18n.global.locale.value)

export function num(n: number | null | undefined): string {
  return Number(n || 0).toLocaleString(loc())
}

/** Turkish writes the percent sign first ("%72"), English after ("72%"). */
export function pct(n: number | null | undefined): string {
  const v = Math.round(Number(n || 0))
  return loc() === 'tr' ? `%${v}` : `${v}%`
}

/** Match dates come as ISO-ish strings ("2025-04-12 19:00:00") or Unix seconds. */
export function matchDate(v: string | number | null | undefined): string {
  if (v == null || v === '') return '—'
  let d: Date
  if (typeof v === 'number' || /^\d+$/.test(String(v))) {
    const n = Number(v)
    d = new Date(n > 1e12 ? n : n * 1000)
  } else {
    d = new Date(String(v).replace(' ', 'T'))
  }
  if (Number.isNaN(d.getTime())) return String(v)
  return d.toLocaleString(loc(), { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}
