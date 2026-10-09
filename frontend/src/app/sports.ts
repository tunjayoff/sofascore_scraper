import { ref } from 'vue'
import { i18n } from '@/i18n'
import { v1 } from '@/api/v1/client'
import type { Sport } from '@/api/v1/schema'

/**
 * The sport registry as the server has it (`GET /api/v1/sports`, R4): every sport list of the UI comes from
 * here, none is hard-coded, so a sport added to the registry appears without a change of the UI. Names come
 * from the locale (`sport.<slug>`) with the registry's English name as the fallback (4.10). Read once per
 * page load; a failure leaves the list empty and is tried again on the next call.
 */
export const sports = ref<Sport[]>([])
let pending: Promise<Sport[]> | null = null

export function loadSports(): Promise<Sport[]> {
  if (sports.value.length) return Promise.resolve(sports.value)
  if (!pending) {
    pending = v1
      .sports()
      .then((list) => (sports.value = list))
      .catch((e) => {
        pending = null
        throw e
      })
  }
  return pending
}

/** For tests: forget what was read. */
export function resetSports() {
  sports.value = []
  pending = null
}

/**
 * A sport of one against one (or a pair), whose players SofaScore lists as teams: a "team" of it is a player
 * for the reader (FX-24 F31). The registry says which sports these are (`GET /sports`, `individual`; B1); until
 * it is read (`loadSports`), or for a sport it does not know, the word "team" stays.
 */
export function isIndividual(slug: string | null | undefined): boolean {
  return !!slug && sports.value.some((s) => s.slug === slug && s.individual)
}

/** A sport's name inside a sentence: lower case unless it is an abbreviation ("tennis", but "MMA"). */
export function sportInText(slug: string | null | undefined): string {
  const name = sportName(slug)
  return name === name.toLocaleUpperCase() ? name : name.toLocaleLowerCase(String(i18n.global.locale.value))
}

export function sportName(slug: string | null | undefined): string {
  if (!slug) return '—'
  const key = `sport.${slug}`
  if (i18n.global.te(key, 'en')) return i18n.global.t(key)
  return sports.value.find((s) => s.slug === slug)?.name ?? slug
}
