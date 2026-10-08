import { i18n } from '@/i18n'

/**
 * Odds in words (6.6; FX-24 F11, FX-26 M20). SofaScore names markets, groups, outcomes and periods in English:
 * the common ones of every sport the end-to-end test met are named in the reader's language, any other keeps
 * SofaScore's name (shown with `lang="en"`).
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

/**
 * Sports that can go to extra time or overtime. SofaScore files some markets of other sports under the period
 * "Extra time" (tennis' "Total games won" in the end-to-end test): there it means nothing and is not shown.
 */
const EXTRA_TIME_SPORTS = new Set(['football', 'basketball', 'ice-hockey', 'handball', 'american-football', 'futsal', 'rugby', 'aussie-rules', 'floorball', 'minifootball', 'waterpolo', 'bandy', 'baseball'])

/** The part of the match a market covers; nothing for the whole match (the market's name says it). */
export function periodName(period: string | null | undefined, sport?: string | null): string {
  if (!period || ['fullTime', 'match', 'fullMatch'].includes(keyOf(period))) return ''
  if (keyOf(period) === 'extraTime' && sport && !EXTRA_TIME_SPORTS.has(sport)) return ''
  return named('period', period)
}

/**
 * The group of a market ("1X2", "Home/Away", "Over/Under") when it tells the reader something the name does
 * not: whether a draw is an outcome. Other groups repeat the market's name and are not shown (FX-26, M20).
 */
export function groupName(group: string | null | undefined, name: string | null | undefined): string {
  if (!group || keyOf(group) === keyOf(name ?? '')) return ''
  const key = `ui.odds.group.${keyOf(group)}`
  return te(key) ? t(key) : ''
}

/** A price as a decimal in the reader's notation ("1.80", "1,80"); "—" without one. */
export function decimalText(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return n.toLocaleString(String(i18n.global.locale.value), { minimumFractionDigits: 2, maximumFractionDigits: 3 })
}
