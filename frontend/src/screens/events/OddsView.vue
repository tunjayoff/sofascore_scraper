<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import UiBadge from '@/ui/UiBadge.vue'
import UiIcon from '@/ui/UiIcon.vue'
import TimeText from '@/ui/TimeText.vue'
import ErrorState from '@/ui/ErrorState.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import { v1 } from '@/api/v1/client'
import type { Odds, OddsChoice, OddsMarket, Slice } from '@/api/v1/schema'
import { choiceName, decimalText, groupName, marketName, periodName } from './oddsText'
import { sliceLabel } from './eventText'

/**
 * The odds of a match in a table (6.6; FX-24 F11): the normalized odds of P28 (`GET /events/{id}/odds/{key}`,
 * the latest read of each provider), one block per market with its outcomes, the price as a decimal and as
 * SofaScore's fraction, the opening price and the direction of the last change. The featured markets and
 * all markets are SofaScore's two odds lists; the one that is stored is shown, the featured one first. A
 * market's group is a line under its name when it says whether a draw is an outcome; a period "Extra time" is
 * left out for a sport without extra time (FX-26, M20).
 */
const props = defineProps<{ eventId: number; slices: Slice[]; sport?: string | null }>()
const { t } = useI18n()

/** The odds lists that have a normalized form, in the order they are offered. */
const KEYS = ['odds_featured', 'odds_all'] as const
type Key = (typeof KEYS)[number]
const keys = computed<Key[]>(() => KEYS.filter((k) => props.slices.some((s) => s.key === k && s.has_payload)))
const key = ref<Key | null>(null)
const records = ref<Odds[] | null>(null)
const error = ref<unknown>(null)
let controller: AbortController | null = null

async function load() {
  if (!key.value) return
  controller?.abort()
  const mine = (controller = new AbortController())
  records.value = null
  error.value = null
  try {
    const found = await v1.eventOddsSnapshots(props.eventId, key.value, { history: false }, mine.signal)
    if (mine === controller) records.value = found
  } catch (e) {
    if ((e as Error)?.name !== 'AbortError' && mine === controller) error.value = e
  }
}

watch(
  keys,
  (list) => {
    if (!key.value || !list.includes(key.value)) key.value = list[0] ?? null
  },
  { immediate: true },
)
watch([key, () => props.eventId], () => void load(), { immediate: true })

/**
 * The markets of a read, each once: SofaScore's featured list names the same market under several labels
 * (`default`, `fullTime`), which would show the same table twice.
 */
function marketsOf(o: Odds): OddsMarket[] {
  const seen = new Set<string>()
  return o.markets.filter((m) => {
    const id = JSON.stringify([m.market_id, m.name, m.period, m.choice_group, m.choices.map((c) => [c.name, c.fractional])])
    if (seen.has(id)) return false
    seen.add(id)
    return true
  })
}

/** "Match goals 2.5", "Asian handicap -0.5": a market with several lines names its line. */
function marketTitle(m: OddsMarket): string {
  return [marketName(m.name), m.choice_group].filter(Boolean).join(' ')
}
const changeIcon = (c: OddsChoice) => (c.change === 1 ? 'sortUp' : c.change === -1 ? 'sortDown' : null)
const changeText = (c: OddsChoice) => t(`ui.odds.change.${c.change === 1 ? 'up' : c.change === -1 ? 'down' : 'none'}`)
</script>

<template>
  <div class="flex flex-col gap-4" data-testid="odds-view">
    <div v-if="keys.length > 1" class="u-seg self-start" role="group" :aria-label="t('ui.odds.list')">
      <button v-for="k in keys" :key="k" type="button" :aria-pressed="k === key" :data-odds-key="k" @click="key = k">{{ sliceLabel(k) }}</button>
    </div>

    <ErrorState v-if="error" compact :error="error" @retry="load" />
    <SkeletonBlock v-else-if="key && !records" :lines="4" />
    <p v-else-if="!records?.length" class="m-0 u-muted" data-testid="odds-none">{{ t('ui.odds.none') }}</p>

    <section v-for="o in records ?? []" :key="`${o.key}:${o.provider_id}`" class="flex flex-col gap-3" :data-provider="o.provider_id">
      <div class="flex flex-wrap items-baseline gap-x-3">
        <h2 class="u-h3">{{ o.provider_id != null ? t('ui.odds.provider', { id: o.provider_id }) : t('ui.odds.providerUnknown') }}</h2>
        <span v-if="o.fetched_at_utc" class="u-small u-muted">{{ t('ui.odds.readAt') }} <TimeText :value="o.fetched_at_utc" /></span>
      </div>
      <div v-for="(m, mi) in marketsOf(o)" :key="mi" class="u-odds-market" data-testid="odds-market">
        <h3 class="u-h3 flex flex-wrap items-center gap-2">
          <span :lang="marketName(m.name) === m.name ? 'en' : undefined">{{ marketTitle(m) }}</span>
          <span v-if="periodName(m.period, sport)" class="u-small u-muted font-normal" data-testid="odds-period">{{ periodName(m.period, sport) }}</span>
          <UiBadge v-if="m.suspended" tone="warn" icon="pause">{{ t('ui.odds.suspended') }}</UiBadge>
          <UiBadge v-if="m.is_live" tone="info">{{ t('ui.odds.live') }}</UiBadge>
        </h3>
        <p v-if="groupName(m.group, m.name)" class="m-0 u-small u-muted" data-testid="odds-group">{{ groupName(m.group, m.name) }}</p>
        <div class="u-table-scroll">
          <table class="u-table u-odds-table">
            <caption class="u-sr">{{ marketTitle(m) }}</caption>
            <thead>
              <tr>
                <th scope="col">{{ t('ui.odds.col.choice') }}</th>
                <th scope="col" class="text-right">{{ t('ui.odds.col.decimal') }}</th>
                <th scope="col" class="text-right">{{ t('ui.odds.col.opening') }}</th>
                <th scope="col">{{ t('ui.odds.col.change') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(c, ci) in m.choices" :key="ci" :data-choice="c.name">
                <td>
                  <span class="inline-flex items-center gap-2"
                    >{{ choiceName(c.name) }}<UiBadge v-if="c.winning" tone="ok" icon="check">{{ t('ui.odds.won') }}</UiBadge></span
                  >
                </td>
                <td class="text-right u-num">
                  <span class="font-semibold" data-testid="odds-decimal">{{ decimalText(c.decimal) }}</span>
                  <span v-if="c.fractional" class="block u-small u-muted" :title="t('ui.odds.col.fractional')" data-testid="odds-fraction">{{ c.fractional }}</span>
                </td>
                <td class="text-right u-num u-muted" :title="c.initial_fractional ?? undefined">{{ decimalText(c.initial_decimal) }}</td>
                <td>
                  <span class="inline-flex items-center gap-1 u-small" :data-change="c.change ?? ''">
                    <UiIcon v-if="changeIcon(c)" :name="changeIcon(c)!" :size="14" /><span class="u-odds-change">{{ changeText(c) }}</span>
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
    <p v-if="records?.length" class="m-0 u-small u-muted">{{ t('ui.odds.note') }}</p>
  </div>
</template>

<style>
.u-odds-market {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  padding-top: var(--sp-3);
  border-top: 1px solid var(--line);
}
.u-odds-table td,
.u-odds-table th {
  white-space: nowrap;
}
/* on a phone the direction of the change is its arrow; the word stays for screen readers */
@media (max-width: 639px) {
  .u-odds-table td,
  .u-odds-table th {
    padding-left: var(--sp-2);
    padding-right: var(--sp-2);
  }
  .u-odds-change {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }
}
</style>
