import { describe, expect, it } from 'vitest'
import { periodLabel, sportKey, SPORTS } from '@/lib/sport'
import en from '@/locales/en'
import tr from '@/locales/tr'

const label = (k: string, v?: Record<string, unknown>) => `${k}:${v?.n}`

describe('sport list (src/sports.py registry)', () => {
  it('has a label for every sport in both languages', () => {
    for (const s of SPORTS) {
      expect((en.sport as Record<string, string>)[s]).toBeTruthy()
      expect((tr.sport as Record<string, string>)[s]).toBeTruthy()
    }
  })

  it('reads the names Sofascore gives before the substrings of the first three sports', () => {
    expect(sportKey('Football')).toBe('football')
    expect(sportKey('soccer')).toBe('football')
    expect(sportKey('Basketball')).toBe('basketball')
    expect(sportKey('Tennis')).toBe('tennis')
    expect(sportKey('American football')).toBe('american-football')
    expect(sportKey('american-football')).toBe('american-football')
    expect(sportKey('Aussie rules')).toBe('aussie-rules')
    expect(sportKey('Hockey')).toBe('ice-hockey')
    expect(sportKey('Ice hockey')).toBe('ice-hockey')
    expect(sportKey('Handball')).toBe('handball')
    expect(sportKey('Rugby')).toBe('rugby')
    expect(sportKey('Futsal')).toBe('futsal')
    expect(sportKey('Minifootball')).toBe('minifootball')
    expect(sportKey('Floorball')).toBe('floorball')
    // not registered yet: the substring rule of the three original sports still applies
    expect(sportKey('Table tennis')).toBe('tennis')
    expect(sportKey('Volleyball')).toBeNull()
    expect(sportKey(null)).toBeNull()
  })

  it('names the periods after the way the sport divides regulation time', () => {
    expect(periodLabel('tennis', 1, label)).toBe('match.period.set:1')
    expect(periodLabel('basketball', 2, label)).toBe('match.period.quarter:2')
    expect(periodLabel('american-football', 4, label)).toBe('match.period.quarter:4')
    expect(periodLabel('ice-hockey', 3, label)).toBe('match.period.period:3')
    expect(periodLabel('floorball', 1, label)).toBe('match.period.period:1')
    expect(periodLabel('handball', 2, label)).toBe('match.period.half:2')
    expect(periodLabel('football', 1, label)).toBe('match.period.half:1')
    expect(periodLabel(null, 1, label)).toBe('match.period.half:1')
  })
})
