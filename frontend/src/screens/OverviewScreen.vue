<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import StatTile from '@/ui/StatTile.vue'
import EmptyState from '@/ui/EmptyState.vue'
import UiIcon from '@/ui/UiIcon.vue'
import TimeText from '@/ui/TimeText.vue'
import ProgressBar from '@/ui/ProgressBar.vue'
import ErrorState from '@/ui/ErrorState.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import GettingStarted from '@/app/GettingStarted.vue'
import { startCardHidden } from '@/ui/prefs'
import { v1 } from '@/api/v1/client'
import type { Change, FollowRecord, Job, SinkStatus } from '@/api/v1/schema'
import { diskBytes, useStatusStore } from '@/app/statusStore'
import { poll } from '@/app/poll'
import { sportName } from '@/app/sports'
import { liveState, sinkState } from '@/ui/status'
import { toast } from '@/ui/toast'
import { bytesText, duration, num, pct, secondsBetween } from '@/ui/time'
import StartJobDialog from './jobs/StartJobDialog.vue'
import { countsText, faceText, jobKindText, jobPercent, jobTarget, readProgress } from './jobs/jobText'
import ChangeFields from './events/ChangeFields.vue'
import { eventTitle, loadTournaments } from './events/eventText'

/**
 * Overview, the start page (6.1, decision 1): is everything working, what runs, what needs me. The tiles
 * count the stored data (`/status.summary`) and the follows; the attention list is built from the
 * connection, the live service, the sinks, the index, old-layout data and the last job of each kind; the
 * services card, the running job with Open and Stop, the recent jobs. With nothing followed and nothing
 * stored the page is one empty state that leads to the follow editor. "Add league" is always the page's
 * primary action, and a "Getting started" card (hidden by the user, back from Help) shows the three steps
 * (FX-14a). The connection is not called "Connected" before a request has been answered.
 */
const { t } = useI18n()
const store = useStatusStore()
const jobs = ref<Job[]>([])
const jobsError = ref<unknown>(null)
const jobsLoading = ref(true)
const follows = ref<FollowRecord[] | null>(null)
const sinks = ref<SinkStatus[] | null>(null)
const changes = ref<Change[] | null>(null)
const changeNames = ref<Map<number, string>>(new Map())
const syncing = ref(false)
const stopping = ref(false)
const confirmStop = ref(false)
const stopError = ref<unknown>(null)

const s = computed(() => store.status)
const summary = computed(() => s.value?.summary ?? null)
const active = computed(() => store.activeJob)
const activeProgress = computed(() => readProgress(active.value?.progress))
const live = computed(() => s.value?.live ?? null)

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

// Follows and sinks are read once per visit: they change rarely and have their own screens
function loadOnce() {
  v1.follows()
    .then((r) => (follows.value = r.data))
    .catch(() => (follows.value = null))
  v1.sinks()
    .then((r) => (sinks.value = r))
    .catch(() => (sinks.value = null))
  v1.changes({ order: 'desc', limit: 5 })
    .then((r) => {
      changes.value = r.data
      for (const id of new Set(r.data.map((c) => c.event_id)))
        v1.event(id)
          .then((e) => (changeNames.value = new Map(changeNames.value).set(id, eventTitle(e))))
          .catch(() => {})
    })
    .catch(() => (changes.value = null))
}

/** Nothing followed and nothing stored: the page is one empty state (6.1, first run). */
const firstRun = computed(() => follows.value !== null && follows.value.length === 0 && summary.value != null && summary.value.matches === 0)

const tiles = computed(() => {
  const sum = summary.value
  const f = follows.value
  const sportCount = f ? new Set(f.map((x) => x.sport).filter(Boolean)).size : 0
  return [
    { key: 'matches', label: t('ui.overview.tile.matches'), value: sum ? num(sum.matches) : '—', to: '/events' },
    {
      key: 'details',
      label: t('ui.overview.tile.details'),
      value: sum ? num(sum.details) : '—',
      note: sum && sum.matches ? pct((sum.details / sum.matches) * 100) : undefined,
      bar: sum && sum.matches ? (sum.details / sum.matches) * 100 : null,
      to: '/events',
    },
    { key: 'follows', label: t('ui.overview.tile.follows'), value: f ? num(f.length) : '—', note: f ? t('ui.overview.tile.sports', { n: sportCount }) : undefined, to: '/follows' },
    { key: 'disk', label: t('ui.overview.tile.disk'), value: sum?.disk ? bytesText(diskBytes(sum)) : '—', to: '/system/health' },
  ]
})

const sinkLine = computed(() => {
  const list = sinks.value
  if (!list) return null
  if (!list.length) return { text: t('ui.overview.sinksNone'), state: null }
  const behind = list.filter((x) => ['retrying', 'behind'].includes(sinkState(x))).length
  return {
    text: behind ? t('ui.overview.sinksBehind', { n: behind, total: list.length }) : t('ui.overview.sinksOk', { n: list.length }),
    state: behind ? 'behind' : sinkState(list[0]) === 'unserved' ? 'unserved' : 'delivering',
  }
})

type Attention = { key: string; text: string; to: string; link: string }

/** "Needs attention" (6.1); hidden when empty. */
const attention = computed<Attention[]>(() => {
  const out: Attention[] = []
  const st = s.value
  const health = t('ui.nav.health')
  if (st && st.bridge.state !== 'ok')
    out.push({ key: 'bridge', text: t(`ui.overview.attention.${st.bridge.state}`, { n: st.bridge.consecutive_failures }), to: '/system/health', link: health })
  if (st?.throttle.error) out.push({ key: 'budget', text: t('ui.health.budgetError'), to: '/system/health', link: health })
  if (st?.live?.running && st.live.blocked) out.push({ key: 'live', text: t('ui.overview.attention.liveBlocked'), to: '/system/health', link: health })
  if (st?.storage_error) out.push({ key: 'storage', text: t('ui.overview.attention.storage'), to: '/system/health', link: health })
  for (const sink of sinks.value ?? []) {
    const state = sinkState(sink)
    if (state === 'retrying' || state === 'behind')
      out.push({ key: `sink-${sink.name}`, text: t(`ui.overview.attention.sink.${state}`, { name: sink.name, n: num(sink.lag_events) }), to: '/system/sinks', link: t('ui.nav.sinks') })
  }
  if (summary.value?.catalog_rebuild_reason)
    out.push({ key: 'index', text: t('ui.overview.attention.index'), to: '/maintenance', link: t('ui.nav.maintenance') })
  if (summary.value?.legacy_events)
    out.push({ key: 'legacy', text: t('ui.overview.attention.legacy', { n: num(summary.value.legacy_events) }), to: '/maintenance', link: t('ui.nav.maintenance') })
  // The last job of each kind, when it did not end well
  const seen = new Set<string>()
  for (const j of jobs.value) {
    if (seen.has(j.kind)) continue
    seen.add(j.kind)
    if (j.state === 'failed' || j.state === 'partial' || j.state === 'interrupted') {
      const failed = Number((j.result as Record<string, unknown> | null)?.failed_count) || 0
      out.push({
        key: `job-${j.id}`,
        text: t(`ui.overview.attention.job.${j.state}`, { kind: jobKindText(j.kind, j.spec), n: num(failed) }),
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
  loadOnce()
  void loadTournaments()
  stopPoll = poll(loadJobs, () => 10000)
})
onUnmounted(() => stopPoll?.())
</script>

<template>
  <div>
    <PageHeader :title="t('ui.nav.overview')" :description="t('ui.overview.description')">
      <template #actions>
        <button v-if="!firstRun" type="button" class="u-btn" data-testid="sync-all" @click="syncing = true"><UiIcon name="jobs" :size="16" />{{ t('ui.overview.syncAll') }}</button>
        <RouterLink to="/follows/new" class="u-btn u-btn-primary" data-testid="overview-add-league"><UiIcon name="plus" :size="16" />{{ t('ui.shell.addLeague') }}</RouterLink>
      </template>
    </PageHeader>

    <GettingStarted v-if="startCardHidden !== '1'" dismissible class="mb-6" />

    <div v-if="firstRun" class="u-card" data-testid="first-run">
      <EmptyState icon="follows" :title="t('ui.overview.firstRun')" :text="t('ui.overview.firstRunText')">
        <RouterLink to="/follows/new" class="u-btn u-btn-primary"><UiIcon name="plus" :size="16" />{{ t('ui.shell.addLeague') }}</RouterLink>
      </EmptyState>
    </div>

    <template v-else>
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

      <div class="grid gap-4 grid-cols-2 lg:grid-cols-4 mb-6" data-testid="tiles">
        <div v-if="!s && store.loading" class="col-span-2 lg:col-span-4"><SkeletonBlock :lines="2" :height="40" /></div>
        <StatTile v-for="tile in tiles" v-else :key="tile.key" :data-tile="tile.key" :label="tile.label" :value="tile.value" :note="tile.note" :bar="tile.bar" :to="tile.to" />
      </div>

      <div class="grid gap-6 lg:grid-cols-2">
        <section class="u-card p-5 flex flex-col gap-3" data-testid="services">
          <h2 class="u-h3">{{ t('ui.overview.services') }}</h2>
          <div v-if="!s && store.loading"><SkeletonBlock :lines="4" /></div>
          <ErrorState v-else-if="!s && store.error" compact :error="store.error" @retry="store.refresh().catch(() => {})" />
          <dl v-else-if="s" class="u-result">
            <dt>{{ t('ui.health.connection') }}</dt>
            <dd class="flex flex-wrap items-center gap-2">
              <StatusBadge kind="connection" :value="store.connection ?? s.bridge.state" />
              <span v-if="s.bridge.last_success_at" class="u-small u-muted"><TimeText :value="s.bridge.last_success_at" relative /></span>
            </dd>
            <dt>{{ t('ui.health.rate') }}</dt>
            <dd>{{ s.throttle.enabled && s.throttle.requests_per_second > 0 ? t('ui.health.rateValue', { n: num(s.throttle.requests_per_second) }) : t('ui.health.noLimit') }}</dd>
            <dt>{{ t('ui.health.live') }}</dt>
            <dd class="flex flex-wrap items-center gap-2">
              <StatusBadge kind="live" :value="liveState(live)" />
              <span v-if="live?.running" class="u-small u-muted">{{ (live.sports ?? []).map(sportName).join(', ') }}</span>
            </dd>
            <dt>{{ t('ui.nav.sinks') }}</dt>
            <dd class="flex flex-wrap items-center gap-2">
              <StatusBadge v-if="sinkLine?.state" kind="sink" :value="sinkLine.state" />
              <span :class="{ 'u-muted': !sinkLine?.state }">{{ sinkLine?.text ?? '—' }}</span>
            </dd>
            <dt>{{ t('ui.health.scheduler') }}</dt>
            <dd :class="{ 'u-muted': !s.capabilities.scheduler }">{{ s.capabilities.scheduler ? t('ui.common.on') : t('ui.common.off') }}</dd>
          </dl>
          <RouterLink to="/system/health" class="self-end u-btn u-btn-sm">{{ t('ui.nav.health') }}<UiIcon name="chevronRight" :size="14" /></RouterLink>
        </section>

        <section v-if="changes" class="u-card p-5 flex flex-col gap-3 lg:order-last" data-testid="recent-corrections">
          <header class="flex items-center">
            <h2 class="u-h3 flex-1">{{ t('ui.overview.corrections') }}</h2>
            <RouterLink to="/corrections" class="u-small font-semibold">{{ t('ui.overview.allCorrections') }}</RouterLink>
          </header>
          <p v-if="!changes.length" class="m-0 u-muted">{{ t('ui.corrections.empty') }}</p>
          <ul v-else class="m-0 p-0 list-none">
            <li v-for="c in changes" :key="c.seq" class="flex flex-col gap-1 py-2" style="border-top: 1px solid var(--line)">
              <span class="flex flex-wrap items-baseline gap-2">
                <RouterLink :to="{ path: `/events/${c.event_id}`, query: { tab: 'corrections' } }" class="font-semibold">{{ changeNames.get(c.event_id) ?? `#${c.event_id}` }}</RouterLink>
                <span class="u-small u-muted"><TimeText :value="c.recorded_at_utc" relative /></span>
              </span>
              <ChangeFields :change="c" compact />
            </li>
          </ul>
        </section>

        <div class="flex flex-col gap-6">
          <section class="u-card p-5 flex flex-col gap-3" data-testid="running" aria-live="polite">
            <h2 class="u-h3">{{ t('ui.overview.running') }}</h2>
            <template v-if="active">
              <p class="m-0 flex flex-wrap items-center gap-2">
                <span class="font-semibold">{{ jobKindText(active.kind, active.spec) }} · {{ jobTarget(active) }}</span>
                <span class="u-small u-muted">{{ faceText(active.origin.face) }}</span>
              </p>
              <ProgressBar :value="jobPercent(active)" :label="t('ui.job.progressLabel')" />
              <p v-if="activeProgress.total" class="m-0 u-small u-muted">
                {{ countsText(activeProgress, num) }}
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
                <RouterLink :to="`/jobs/${j.id}`" class="flex-1 min-w-0 truncate font-semibold">{{ jobKindText(j.kind, j.spec) }} · {{ jobTarget(j) }}</RouterLink>
                <span class="u-small u-muted">{{ faceText(j.origin.face) }}</span>
                <span class="u-small u-muted"><TimeText :value="j.started_at ?? j.created_at" /></span>
                <span v-if="j.finished_at" class="u-small u-muted u-num">{{ duration(secondsBetween(j.started_at ?? j.created_at, j.finished_at)) }}</span>
              </li>
            </ul>
          </section>
        </div>
      </div>
    </template>

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
