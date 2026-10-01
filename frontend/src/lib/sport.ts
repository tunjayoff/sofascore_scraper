export type SportKey = 'football' | 'basketball' | 'tennis'

export const SPORTS: SportKey[] = ['football', 'basketball', 'tennis']

/** Sofascore names sports "Football", "Basketball", "Tennis" (or slugs); anything else is unknown. */
export function sportKey(raw: unknown): SportKey | null {
  const s = String(raw || '').toLowerCase()
  if (s.includes('basket')) return 'basketball'
  if (s.includes('tennis')) return 'tennis'
  if (s.includes('football') || s.includes('soccer')) return 'football'
  return null
}

/** Column labels for the per-period score line, by sport. */
export function periodLabel(sport: SportKey | null, n: number, t: (k: string, v?: Record<string, unknown>) => string): string {
  if (sport === 'tennis') return t('match.period.set', { n })
  if (sport === 'basketball') return t('match.period.quarter', { n })
  return t('match.period.half', { n })
}
