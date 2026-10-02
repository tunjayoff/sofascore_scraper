<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import UiIcon from '@/ui/UiIcon.vue'
import TimeText from '@/ui/TimeText.vue'
import ProgressBar from '@/ui/ProgressBar.vue'
import ErrorState from '@/ui/ErrorState.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import { v1 } from '@/api/v1/client'
import type { Job } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { poll } from '@/app/poll'
import { toast } from '@/ui/toast'
import { duration, num, secondsBetween } from '@/ui/time'
import StartJobDialog from './jobs/StartJobDialog.vue'
import { faceText, jobKindText, jobPercent, jobTarget, readProgress } from './jobs/jobText'

/**
 * Overview, the start page (6.1, decision 1): is everything working, what runs, what needs me. With the
 * routes of today: the attention list from `/status` and the last jobs, the services card, the running
 * job with Open and Stop, and the recent jobs. The tiles about stored data (matches, details, follows,
 * disk) need P21's data summary and are not shown until then (6.1, states).
 */
const { t } = useI18n()
const store = useStatusStore()
const jobs = ref<Job[]>([])
const jobsError = ref<unknown>(null)
const jobsLoading = ref(true)
const syncing = ref(false)
const stopping = ref(false)
const confirmStop = ref(false)
const stopError = ref<unknown>(null)

const s = computed(() => store.status)
const active = computed(() => store.activeJob)
const activeProgress = computed(() => readProgress(active.value?.progress))

async function loadJobs() {
  try {
    jobs.value = (await v1.jobs({ limit: 20 })).data
    jobsError.value = null
  } catch (e) {
    jobsError.value = e
  } finally {
    jobsLoading.value = false
  }
}

type Attention = { key: string; text: string; to: string; link: string }

/** "Needs attention" (6.1): built from what exists today; hidden when empty. */
const attention = computed<Attention[]>(() => {
  const out: Attention[] = []
  const st = s.value
  if (st && st.bridge.state !== 'ok')
    out.push({ key: 'bridge', text: t(`ui.overview.attention.${st.bridge.state}`, { n: st.bridge.consecutive_failures }), to: '/system/health', link: t('ui.nav.health') })
  if (st?.throttle.error) out.push({ key: 'budget', text: t('ui.health.budgetError'), to: '/system/health', link: t('ui.nav.health') })
  // The last job of each kind, when it did not end well
  const seen = new Set<string>()
  for (const j of jobs.value) {
    if (seen.has(j.kind)) continue
    seen.add(j.kind)
    if (j.state === 'failed' || j.state === 'partial' || j.state === 'interrupted') {
      const failed = Number((j.result as Record<string, unknown> | null)?.failed_count) || 0
      out.push({
        key: `job-${j.id}`,
        text: t(`ui.overview.attention.job.${j.state}`, { kind: jobKindText(j.kind), n: num(failed) }),
        to: `/jobs/${j.id}`,
        link: t('ui.overview.openJob'),
      })
    }
  }
  return out
})

const recent = computed(() => jobs.value.slice(0, 5))

async function stop() {
  if (!active.value) return
  stopping.value = true
  stopError.value = null
  try {
    await v1.cancelJob(active.value.id)
    confirmStop.value = false
    toast({ kind: 'info', text: t('ui.job.stopAsked') })
    void store.refresh().catch(() => {})
  } catch (e) {
    stopError.value = e
  } finally {
    stopping.value = false
  }
}

let stopPoll: (() => void) | null = null
onMounted(() => {
  // Inside the shell the status is already polled; opened on its own, the screen reads it once
  if (!store.status && !store.loading) void store.refresh().catch(() => {})
  stopPoll = poll(loadJobs, () => 10000)
})
onUnmounted(() => stopPoll?.())
</script>

<template>
  <div>
    <PageHeader :title="t('ui.nav.overview')" :description="t('ui.overview.description')">
      <template #actions>
        <button type="button" class="u-btn u-btn-primary" @click="syncing = true"><UiIcon name="jobs" :size="16" />{{ t('ui.overview.syncAll') }}</button>
      </template>
    </PageHeader>

    <section v-if="attention.length" class="u-card mb-6" data-testid="attention" :aria-labelledby="'attention-title'">
      <h2 id="attention-title" class="u-h3 px-5 pt-4">{{ t('ui.overview.attentionTitle', { n: attention.length }) }}</h2>
      <ul class="m-0 p-0 list-none">
        <li v-for="a in attention" :key="a.key" class="flex flex-wrap items-center gap-3 px-5 py-3" style="border-top: 1px solid var(--line)">
          <span style="color: var(--warn-fg)"><UiIcon name="alert" :size="16" /></span>
          <span class="flex-1 min-w-0">{{ a.text }}</span>
          <RouterLink :to="a.to" class="u-btn u-btn-sm">{{ a.link }}<UiIcon name="chevronRight" :size="14" /></RouterLink>
        </li>
      </ul>
    </section>

    <p class="m-0 mb-6 u-small u-muted flex items-center gap-2"><UiIcon name="planned" :size="14" />{{ t('ui.overview.tilesLater') }}</p>

    <div class="grid gap-6 lg:grid-cols-2">
      <section class="u-card p-5 flex flex-col gap-3" data-testid="services">
        <h2 class="u-h3">{{ t('ui.overview.services') }}</h2>
        <div v-if="!s && store.loading"><SkeletonBlock :lines="4" /></div>
        <ErrorState v-else-if="!s && store.error" compact :error="store.error" @retry="store.refresh().catch(() => {})" />
        <dl v-else-if="s" class="u-result">
          <dt>{{ t('ui.health.connection') }}</dt>
          <dd class="flex flex-wrap items-center gap-2">
            <StatusBadge kind="connection" :value="s.bridge.state" />
            <span v-if="s.bridge.last_success_at" class="u-small u-muted"><TimeText :value="s.bridge.last_success_at" relative /></span>
          </dd>
          <dt>{{ t('ui.health.rate') }}</dt>
          <dd>{{ s.throttle.enabled && s.throttle.requests_per_second > 0 ? t('ui.health.rateValue', { n: num(s.throttle.requests_per_second) }) : t('ui.health.noLimit') }}</dd>
          <dt>{{ t('ui.health.live') }}</dt>
          <dd><StatusBadge kind="live" value="unknown" /></dd>
          <dt>{{ t('ui.health.scheduler') }}</dt>
          <dd class="u-muted">{{ t('ui.health.later') }}</dd>
        </dl>
        <RouterLink to="/system/health" class="self-end u-btn u-btn-sm">{{ t('ui.nav.health') }}<UiIcon name="chevronRight" :size="14" /></RouterLink>
      </section>

      <div class="flex flex-col gap-6">
        <section class="u-card p-5 flex flex-col gap-3" data-testid="running" aria-live="polite">
          <h2 class="u-h3">{{ t('ui.overview.running') }}</h2>
          <template v-if="active">
            <p class="m-0 flex flex-wrap items-center gap-2">
              <span class="font-semibold">{{ jobKindText(active.kind) }} · {{ jobTarget(active) }}</span>
              <span class="u-small u-muted">{{ faceText(active.origin.face) }}</span>
            </p>
            <ProgressBar :value="jobPercent(active)" :label="t('ui.job.progressLabel')" />
            <p v-if="activeProgress.total" class="m-0 u-small u-muted">
              {{ t('ui.job.counts', { done: num(activeProgress.done), total: num(activeProgress.total) }) }}
              <template v-if="activeProgress.eta"> · {{ t('ui.job.eta', { time: duration(activeProgress.eta) }) }}</template>
            </p>
            <div class="flex gap-2 justify-end">
              <RouterLink :to="`/jobs/${active.id}`" class="u-btn u-btn-sm">{{ t('ui.overview.open') }}</RouterLink>
              <button v-if="!active.cancel_requested" type="button" class="u-btn u-btn-sm u-btn-danger" @click="confirmStop = true">{{ t('ui.job.stop') }}</button>
            </div>
          </template>
          <p v-else class="m-0 u-muted">{{ t('ui.overview.nothingRunning') }}</p>
        </section>

        <section class="u-card p-5 flex flex-col gap-3" data-testid="recent">
          <header class="flex items-center">
            <h2 class="u-h3 flex-1">{{ t('ui.overview.recent') }}</h2>
            <RouterLink to="/jobs" class="u-small font-semibold">{{ t('ui.overview.all') }}</RouterLink>
          </header>
          <div v-if="jobsLoading && !jobs.length"><SkeletonBlock :lines="4" /></div>
          <ErrorState v-else-if="jobsError && !jobs.length" compact :error="jobsError" @retry="loadJobs" />
          <p v-else-if="!recent.length" class="m-0 u-muted">{{ t('ui.jobs.empty') }}</p>
          <ul v-else class="m-0 p-0 list-none">
            <li v-for="j in recent" :key="j.id" class="flex flex-wrap items-center gap-3 py-2" style="border-top: 1px solid var(--line)">
              <StatusBadge kind="job" :value="j.state" />
              <RouterLink :to="`/jobs/${j.id}`" class="flex-1 min-w-0 truncate font-semibold">{{ jobKindText(j.kind) }}</RouterLink>
              <span class="u-small u-muted">{{ faceText(j.origin.face) }}</span>
              <span class="u-small u-muted"><TimeText :value="j.started_at ?? j.created_at" /></span>
              <span v-if="j.finished_at" class="u-small u-muted u-num">{{ duration(secondsBetween(j.started_at ?? j.created_at, j.finished_at)) }}</span>
            </li>
          </ul>
        </section>
      </div>
    </div>

    <StartJobDialog v-if="syncing" :body="{ kind: 'sync', spec: {} }" @close="syncing = false" @started="loadJobs" />
    <ConfirmDialog
      v-if="confirmStop && active"
      :title="t('ui.job.stopTitle')"
      :confirm-label="t('ui.job.stop')"
      danger
      :busy="stopping"
      :error="stopError"
      @confirm="stop"
      @close="confirmStop = false"
    >
      <p class="m-0">{{ t('ui.job.stopText') }}</p>
    </ConfirmDialog>
  </div>
</template>
