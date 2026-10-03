<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { parseStatistics, type StatItem } from '@/lib/matchDetail'

/**
 * Statistics of football, basketball and tennis as SofaScore stores them (decision 6): one block per
 * period, groups of items with the two values and a bar. The item names are SofaScore's (English data).
 */
const props = defineProps<{ payload: unknown; home: string; away: string }>()
const { t, te } = useI18n()
const periods = computed(() => parseStatistics(props.payload))
const index = ref(0)
const groups = computed(() => periods.value[index.value]?.groups ?? [])

function periodName(code: string | undefined, i: number) {
  const raw = String(code ?? '')
  const key = `ui.eventDetail.statPeriod.${raw}`
  if (raw && te(key, 'en')) return t(key)
  return raw || t('ui.eventDetail.statPeriod.n', { n: i + 1 })
}

function share(item: StatItem) {
  const h = Number(item.homeValue ?? String(item.home ?? '').replace('%', ''))
  const a = Number(item.awayValue ?? String(item.away ?? '').replace('%', ''))
  const total = (Number.isFinite(h) ? h : 0) + (Number.isFinite(a) ? a : 0)
  if (!total) return 50
  return (h / total) * 100
}
</script>

<template>
  <div class="flex flex-col gap-4" data-testid="view-statistics">
    <div v-if="periods.length > 1" class="u-seg self-start" role="group" :aria-label="t('ui.eventDetail.period')">
      <button v-for="(p, i) in periods" :key="i" type="button" :aria-pressed="i === index" @click="index = i">{{ periodName(p.period, i) }}</button>
    </div>
    <section v-for="(g, gi) in groups" :key="gi" class="flex flex-col gap-2">
      <h3 v-if="g.groupName" class="u-caption">{{ g.groupName }}</h3>
      <div v-for="(item, ii) in g.statisticsItems ?? []" :key="ii" class="u-stat">
        <div class="flex items-baseline gap-3">
          <span class="u-num font-semibold w-16">{{ item.home ?? '—' }}</span>
          <span class="flex-1 text-center u-small">{{ item.name }}</span>
          <span class="u-num font-semibold w-16 text-right">{{ item.away ?? '—' }}</span>
        </div>
        <div class="u-stat-bar" aria-hidden="true"><span :style="{ width: `${share(item)}%` }"></span></div>
      </div>
    </section>
    <p class="m-0 u-small u-muted">{{ t('ui.eventDetail.sides', { home, away }) }}</p>
  </div>
</template>

<style>
.u-stat {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--sp-2) 0;
  border-top: 1px solid var(--line);
}
.u-stat-bar {
  display: flex;
  height: 4px;
  border-radius: var(--r-pill);
  background: var(--neutral-bg);
  overflow: hidden;
}
.u-stat-bar span {
  background: var(--accent);
}
</style>
