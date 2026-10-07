import { i18n } from '@/i18n'

/**
 * Odds in words (6.6; FX-24 F11). SofaScore names markets, outcomes and periods in English: the common ones
 * are named in the reader's language, any other keeps SofaScore's name (shown with `lang="en"`).
 */
const t = (key: string, args?: Record<string, unknown>) => i18n.global.t(key, args ?? {})
const te = (key: string) => i18n.global.te(key, 'en')

/** "Both teams to score" → "bothTeamsToScore", "1st half" → "1stHalf": the key of a name in the locale. */
function keyOf(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+(.)?/g, (_, c: string | undefined) => (c ? c.toUpperCase() : ''))
}

function named(group: string, name: string | null | undefined): string {
  if (!name) return ''
  const key = `ui.odds.${group}.${keyOf(name)}`
  return keyOf(name) && te(key) ? t(key) : name
}

/** A market's name: "Full time" → "Match result" ("Maç sonucu"). */
export function marketName(name: string | null | undefined): string {
  return named('market', name) || t('ui.odds.marketUnknown')
}

/** An outcome: "1", "X", "2" as they are; "Over", "Under", "Yes", "No" in words. */
export function choiceName(name: string): string {
  return named('choice', name)
}

/** The part of the match a market covers; nothing for the whole match (the market's name says it). */
export function periodName(period: string | null | undefined): string {
  if (!period || ['fullTime', 'match', 'fullMatch'].includes(keyOf(period))) return ''
  return named('period', period)
}

/** A price as a decimal in the reader's notation ("1.80", "1,80"); "—" without one. */
export function decimalText(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return n.toLocaleString(String(i18n.global.locale.value), { minimumFractionDigits: 2, maximumFractionDigits: 3 })
}
