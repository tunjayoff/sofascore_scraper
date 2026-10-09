import { ref } from 'vue'
import { i18n } from '@/i18n'
import { v1 } from '@/api/v1/client'
import type { OddsProviderInfo } from '@/api/v1/schema'

/**
 * The bookmakers whose names the server knows (`GET /api/v1/odds/providers`, B4): a provider id (the odds of a
 * match, an odds slice's sub-key, `client.odds_provider`) is shown by name, none is hard-coded here. Read once
 * per page load; a failure leaves the list empty, ids stay numbered ("Bookmaker 5"), and it is tried again on
 * the next call.
 */
export const oddsProviders = ref<OddsProviderInfo[]>([])
let pending: Promise<OddsProviderInfo[]> | null = null

export function loadOddsProviders(): Promise<OddsProviderInfo[]> {
  if (oddsProviders.value.length) return Promise.resolve(oddsProviders.value)
  if (!pending) {
    pending = v1
      .oddsProviders()
      .then((list) => (oddsProviders.value = list))
      .catch(() => {
        pending = null
        return []
      })
  }
  return pending
}

/** For tests: forget what was read. */
export function resetOddsProviders() {
  oddsProviders.value = []
  pending = null
}

/** The known bookmaker of an id (a number or a slice's sub-key), or null. */
export function oddsProvider(id: number | string | null | undefined): OddsProviderInfo | null {
  if (id == null || String(id).trim() === '') return null
  const n = Number(id)
  return oddsProviders.value.find((p) => p.id === n) ?? null
}

/** "bet365"; an id without a known name stays numbered ("Bookmaker 5"); no id: "Bookmaker unknown". */
export function oddsProviderName(id: number | string | null | undefined): string {
  if (id == null || String(id).trim() === '') return i18n.global.t('ui.odds.providerUnknown')
  return oddsProvider(id)?.name ?? i18n.global.t('ui.odds.provider', { id: String(id) })
}
