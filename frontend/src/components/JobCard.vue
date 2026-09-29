<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useScrapeStore } from '@/stores/scrape'
import { useLeaguesStore } from '@/stores/leagues'
import { jobTitle, jobStatus } from '@/lib/jobLabel'
import { toastError } from '@/lib/toast'
import { pct as fmtPct } from '@/lib/format'

/** The running (or just finished) download. `large` is the Activity page version. */
const props = defineProps<{ large?: boolean }>()
const { t } = useI18n()
const scrape = useScrapeStore()
const leagues = useLeaguesStore()

const s = computed(() => scrape.state)
const title = computed(() => jobTitle(s.value.payload, leagues.nameOf, t))
const status = computed(() =>
  jobStatus(s.value.status, s.value.cancel_requested, s.value.schedule_empty_seasons, t),
)
const pct = computed(() => Math.max(0, Math.min(100, Math.round(s.value.progress || 0))))
const counts = computed(() =>
  s.value.matches_total
    ? t('job.matches', { done: s.value.matches_done ?? 0, total: s.value.matches_total })
    : '',
)
const barColor = computed(() =>
  status.value.tone === 'error' ? 'var(--danger)' : status.value.tone === 'neutral' ? 'var(--faint)' : 'var(--accent)',
)

async function stop() {
  try {
    await scrape.cancel()
  } catch (e) {
    toastError(e)
  }
}
</script>

<template>
  <div class="card" :class="props.large ? 'p-6 flex flex-col gap-4' : 'p-3.5 flex flex-col gap-2.5'">
    <div class="flex items-center justify-between gap-2">
      <span class="eyebrow" :style="{ color: status.tone === 'error' ? 'var(--danger)' : undefined }">{{ status.text }}</span>
      <span class="mono text-[13px]" style="color: var(--muted)">{{ fmtPct(pct) }}</span>
    </div>
    <div :class="props.large ? 'text-xl font-bold' : 'text-sm font-semibold leading-snug'">{{ title }}</div>
    <div class="track" :style="{ height: props.large ? '10px' : undefined }" role="progressbar" :aria-valuenow="pct" aria-valuemin="0" aria-valuemax="100">
      <div :style="{ width: pct + '%', background: barColor }"></div>
    </div>
    <div v-if="props.large && s.current_task" class="text-sm" style="color: var(--muted)">{{ s.current_task }}</div>
    <div class="flex items-center justify-between gap-2">
      <span class="text-xs mono" style="color: var(--muted)">{{ counts }}</span>
      <button v-if="scrape.isRunning && !s.cancel_requested" type="button" class="btn btn-ghost btn-danger btn-sm" @click="stop">
        {{ t('job.stop') }}
      </button>
      <button v-else-if="!scrape.isRunning" type="button" class="btn btn-ghost btn-sm" @click="scrape.dismiss()">
        {{ t('job.dismiss') }}
      </button>
    </div>
  </div>
</template>
