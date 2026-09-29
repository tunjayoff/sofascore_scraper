<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useScrapeStore } from '@/stores/scrape'
import { useLeaguesStore } from '@/stores/leagues'
import { jobTitle, jobStatus, hasWarnings } from '@/lib/jobLabel'
import { toastError } from '@/lib/toast'
import { pct as fmtPct, duration } from '@/lib/format'
import AppIcon from '@/components/AppIcon.vue'
import type { JobPhase } from '@/api/client'

/** The running (or just finished) download. `large` is the Activity page version. */
const props = defineProps<{ large?: boolean }>()
const { t } = useI18n()
const scrape = useScrapeStore()
const leagues = useLeaguesStore()

// Ticks the wait countdown and the elapsed time between server updates
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null
onMounted(() => (timer = setInterval(() => (now.value = Date.now()), 1000)))
onUnmounted(() => timer && clearInterval(timer))

const s = computed(() => scrape.state)
const d = computed(() => s.value.detail ?? null)
const running = computed(() => scrape.isRunning)
const title = computed(() => jobTitle(s.value.payload, leagues.nameOf, t))
const warn = computed(() => hasWarnings(s.value))
const status = computed(() => jobStatus(s.value.status, s.value.cancel_requested, warn.value, t))
const pct = computed(() => Math.max(0, Math.min(100, Math.round(s.value.progress || 0))))
const barColor = computed(() =>
  status.value.tone === 'error' ? 'var(--danger)' : status.value.tone === 'neutral' ? 'var(--faint)' : 'var(--accent)',
)

const step = computed(() =>
  running.value && d.value?.phase ? t('job.step', { n: d.value.phase_index, total: d.value.phase_count }) : '',
)

const leagueName = computed(() => {
  const x = d.value
  if (!x) return ''
  return x.league_name || (x.league_id != null ? leagues.nameOf(x.league_id) : '')
})

/** "Maç detayları · LaLiga · LaLiga 24/25" */
const phaseLine = computed(() => {
  if (!running.value) return ''
  const x = d.value
  if (!x) return s.value.current_task || ''
  if (!x.phase) return t('job.preparing')
  const season = x.season_name && x.season_name !== leagueName.value ? x.season_name : ''
  return [t(`job.phase.${x.phase}`), leagueName.value, season].filter(Boolean).join(' · ')
})

const counts = computed(() => {
  const x = d.value
  if (running.value && x?.phase) {
    if (x.phase === 'export') return ''
    if (x.phase === 'details' && !x.total) return t('job.counting')
    return x.total ? t(`job.unit.${x.phase}`, { done: x.done, total: x.total }) : ''
  }
  if (!s.value.matches_total) return ''
  return x
    ? t('job.unit.details', { done: s.value.matches_done ?? 0, total: s.value.matches_total })
    : t('job.matches', { done: s.value.matches_done ?? 0, total: s.value.matches_total })
})

const failed = computed(() => s.value.matches_failed || 0)

const refreshedText = computed(() => {
  const n = d.value?.refreshed || 0
  return n ? t('job.refreshedN', { n, changed: d.value?.refresh_changed ?? 0 }) : ''
})

const eta = computed(() => (running.value && d.value?.eta_seconds ? t('job.eta', { time: duration(d.value.eta_seconds) }) : ''))

const waitText = computed(() => {
  const w = d.value?.wait
  if (!running.value || !w) return ''
  const left = w.until - now.value / 1000
  if (left <= 0) return ''
  const reason = w.reason === 'rate_limit' || w.reason === 'forbidden' ? w.reason : 'other'
  return t(`job.wait.${reason}`, { time: duration(left) })
})

const timeText = computed(() => {
  const start = s.value.started_at ? Date.parse(s.value.started_at) : NaN
  if (Number.isNaN(start)) return ''
  if (running.value) return t('job.elapsed', { time: duration((now.value - start) / 1000) })
  const end = s.value.finished_at ? Date.parse(s.value.finished_at) : NaN
  return Number.isNaN(end) ? '' : t('job.took', { time: duration((end - start) / 1000) })
})

const warnings = computed(() => {
  if (running.value) return []
  const out: string[] = []
  if (s.value.circuit_breaker_triggered) {
    const r = s.value.circuit_breaker_reason
    out.push(t('job.breaker', { code: r && r !== 'other' ? ` (${r})` : '' }))
  }
  if (s.value.schedule_empty_seasons) out.push(t('job.emptySeasons', { n: s.value.schedule_empty_seasons }))
  return out
})

type StepState = 'done' | 'current' | 'stopped' | 'pending'

/** The phase list on the Activity page. */
const steps = computed(() => {
  const x = d.value
  if (!x?.phases?.length) return []
  const cur = x.phase_index - 1
  const completed = String(s.value.status).toLowerCase() === 'completed'
  return x.phases.map((p: JobPhase, i) => {
    let state: StepState = 'pending'
    if (completed || i < cur) state = 'done'
    else if (i === cur) state = running.value ? 'current' : 'stopped'
    const detail =
      state === 'current' && p !== 'export' && x.total ? t(`job.unit.${p}`, { done: x.done, total: x.total }) : ''
    return { key: p, label: t(`job.phase.${p}`), state, detail }
  })
})

const failedList = computed(() =>
  (d.value?.failed || []).map((f) => ({
    id: f.match_id,
    league: f.league_id != null ? leagues.nameOf(f.league_id) : '',
  })),
)
const failedMore = computed(() => Math.max(0, failed.value - failedList.value.length))

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
      <span class="eyebrow truncate" :style="{ color: status.tone === 'error' ? 'var(--danger)' : status.tone === 'warn' ? 'var(--warn-fg)' : undefined }">
        {{ status.text }}<template v-if="step"> · {{ step }}</template>
      </span>
      <span class="mono text-[13px]" style="color: var(--muted)">{{ fmtPct(pct) }}</span>
    </div>
    <div :class="props.large ? 'text-xl font-bold' : 'text-sm font-semibold leading-snug'">{{ title }}</div>
    <div
      class="track"
      :style="{ height: props.large ? '10px' : undefined }"
      role="progressbar"
      :aria-label="title"
      :aria-valuenow="pct"
      :aria-valuetext="phaseLine || status.text"
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <div :style="{ width: pct + '%', background: barColor }"></div>
    </div>

    <div v-if="phaseLine" :class="props.large ? 'text-sm' : 'text-[13px] leading-snug'" style="color: var(--text-2)" aria-live="polite">
      {{ phaseLine }}
    </div>

    <div v-if="waitText" class="flex items-start gap-1.5 text-xs leading-snug" style="color: var(--warn-fg)" role="status">
      <AppIcon name="alert" :size="14" class="shrink-0 mt-px" /><span>{{ waitText }}</span>
    </div>

    <div v-if="counts || failed || refreshedText || eta" class="flex items-baseline justify-between gap-2 text-xs mono" style="color: var(--muted)">
      <span>
        {{ counts }}<template v-if="counts && failed"> · </template><span v-if="failed" style="color: var(--danger)">{{ t('job.failedN', { n: failed }) }}</span>
        <template v-if="refreshedText && (counts || failed)"> · </template>{{ refreshedText }}
      </span>
      <span v-if="eta" class="shrink-0">{{ eta }}</span>
    </div>

    <ol v-if="props.large && steps.length" class="flex flex-col gap-2 text-sm" :aria-label="t('job.step', { n: d?.phase_index ?? 0, total: d?.phase_count ?? 0 })">
      <li v-for="st in steps" :key="st.key" class="flex items-center gap-2.5">
        <span class="step-dot" :class="'is-' + st.state" aria-hidden="true">
          <AppIcon v-if="st.state === 'done'" name="check" :size="12" />
        </span>
        <span :style="{ color: st.state === 'pending' ? 'var(--faint)' : 'var(--text)', fontWeight: st.state === 'current' ? 600 : 400 }">{{ st.label }}</span>
        <span v-if="st.detail" class="mono text-xs ml-auto" style="color: var(--muted)">{{ st.detail }}</span>
      </li>
    </ol>

    <div v-for="w in warnings" :key="w" class="flex items-start gap-1.5 text-xs leading-snug" style="color: var(--warn-fg)">
      <AppIcon name="alert" :size="14" class="shrink-0 mt-px" /><span>{{ w }}</span>
    </div>

    <details v-if="props.large && failedList.length" class="text-sm">
      <summary class="cursor-pointer font-semibold" style="color: var(--danger)">{{ t('job.failedTitle', { n: failed }) }}</summary>
      <p class="mt-2 mb-2 text-xs" style="color: var(--muted)">{{ t('job.failedHint') }}</p>
      <ul class="grid gap-1 text-[13px]" style="grid-template-columns: repeat(auto-fill, minmax(180px, 1fr))">
        <li v-for="f in failedList" :key="f.id" class="truncate">
          <span class="mono">#{{ f.id }}</span><span v-if="f.league" style="color: var(--muted)"> · {{ f.league }}</span>
        </li>
      </ul>
      <p v-if="failedMore" class="mt-1 text-xs" style="color: var(--muted)">{{ t('job.failedMore', { n: failedMore }) }}</p>
    </details>

    <div class="flex items-center justify-between gap-2">
      <span class="text-xs" style="color: var(--muted)">{{ timeText }}</span>
      <button v-if="scrape.isRunning && !s.cancel_requested" type="button" class="btn btn-ghost btn-danger btn-sm" @click="stop">
        {{ t('job.stop') }}
      </button>
      <button v-else-if="!scrape.isRunning" type="button" class="btn btn-ghost btn-sm" @click="scrape.dismiss()">
        {{ t('job.dismiss') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.step-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 999px;
  flex-shrink: 0;
  border: 2px solid var(--border-2);
}
.step-dot.is-done {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--on-accent);
}
.step-dot.is-current {
  border-color: var(--accent);
  background: var(--accent-soft);
  animation: step-pulse 1.4s ease-in-out infinite;
}
.step-dot.is-stopped {
  border-color: var(--faint);
  background: var(--neutral-bg);
}
@keyframes step-pulse {
  50% {
    box-shadow: 0 0 0 4px var(--accent-soft);
  }
}
@media (prefers-reduced-motion: reduce) {
  .step-dot.is-current {
    animation: none;
  }
}
</style>
