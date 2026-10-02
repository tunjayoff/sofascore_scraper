/**
 * The sports the platform follows, in the order of the sport registry (src/sports.py).
 * tests/test_sports_registry.py checks that both lists below equal the registry.
 */
export type SportKey = 'football' | 'basketball' | 'tennis' | 'american-football' | 'aussie-rules' | 'ice-hockey' | 'handball' | 'rugby' | 'futsal' | 'minifootball' | 'floorball' | 'volleyball' | 'badminton' | 'table-tennis' | 'padel' | 'snooker' | 'baseball' | 'cricket' | 'esports' | 'darts' | 'mma'

export const SPORTS: SportKey[] = ['football', 'basketball', 'tennis', 'american-football', 'aussie-rules', 'ice-hockey', 'handball', 'rugby', 'futsal', 'minifootball', 'floorball', 'volleyball', 'badminton', 'table-tennis', 'padel', 'snooker', 'baseball', 'cricket', 'esports', 'darts', 'mma']

/** Exact names and slugs, as src/sports.py normalize_sport matches them (lower case, spaces as dashes). */
const EXACT: Record<string, SportKey> = {
  soccer: 'football',
  'american-football': 'american-football',
  'aussie-rules': 'aussie-rules',
  hockey: 'ice-hockey', // Sofascore's name for ice hockey
  'ice-hockey': 'ice-hockey',
  'mini-football': 'minifootball',
  'table-tennis': 'table-tennis', // before the "tennis" substring below
  'e-sports': 'esports', // Sofascore's name for e-sports
  'mixed-martial-arts': 'mma', // Sofascore's name for MMA
}
for (const s of SPORTS) EXACT[s] = s

/**
 * A Sofascore sport name ("Football", "American football", "Hockey") or slug as a registered sport; anything
 * else is unknown. Exact names first, then the substrings of the three original sports, as the server does.
 */
export function sportKey(raw: unknown): SportKey | null {
  const s = String(raw || '').trim().toLowerCase()
  const exact = EXACT[s.replace(/ /g, '-')]
  if (exact) return exact
  if (s.includes('basket')) return 'basketball'
  if (s.includes('tennis')) return 'tennis'
  if (s.includes('football') || s.includes('soccer')) return 'football'
  return null
}

/** How regulation time is divided, as the score's `format` (src/sports.py period_format). */
const QUARTERS: SportKey[] = ['basketball', 'american-football', 'aussie-rules']
const THIRDS: SportKey[] = ['ice-hockey', 'floorball']
/**
 * Sports scored in sets, as the score family `sets` (src/sports.py); darts counts legs per set. Snooker sends
 * only the frames won, e-sports one column per game (who won it).
 */
const SETS: SportKey[] = ['tennis', 'volleyball', 'badminton', 'table-tennis', 'padel', 'darts']

/** Column labels for the per-period score line, by sport. */
export function periodLabel(sport: SportKey | null, n: number, t: (k: string, v?: Record<string, unknown>) => string): string {
  if (sport && SETS.includes(sport)) return t('match.period.set', { n })
  if (sport === 'snooker') return t('match.period.frames', { n })
  if (sport === 'baseball') return t('match.period.inning', { n })
  if (sport === 'esports') return t('match.period.game', { n })
  if (sport && QUARTERS.includes(sport)) return t('match.period.quarter', { n })
  if (sport && THIRDS.includes(sport)) return t('match.period.period', { n })
  return t('match.period.half', { n })
}
