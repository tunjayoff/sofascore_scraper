<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import TimeText from '@/ui/TimeText.vue'
import FactList from '@/ui/FactList.vue'
import ProgressBar from '@/ui/ProgressBar.vue'
import ErrorState from '@/ui/ErrorState.vue'
import EmptyState from '@/ui/EmptyState.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import CopyButton from '@/ui/CopyButton.vue'
import UiIcon from '@/ui/UiIcon.vue'
import { v1, V1Error } from '@/api/v1/client'
import type { Job } from '@/api/v1/schema'
import { toast } from '@/ui/toast'
import { duration, formatTime, num, secondsBetween, now as clockNow, useClock } from '@/ui/time'
import StartJobDialog from './StartJobDialog.vue'
import JobOutput from './JobOutput.vue'
import { JobStream, type JobEventMessage } from './jobStream'
import { logLine, codeText, seasonsWanted, type LogLine } from './eventText'
import { loadSeasons, loadTournaments } from '@/screens/events/eventText'
import { breakerText, countsText, faceText, isTerminal, jobErrorText, jobKindText, jobLeague, jobTarget, phaseText, readProgress, rerunBody, waitText, type ProgressView } from './jobText'

/**
 * Job detail (6.9): state, origin and times; the live progress with phase, counts, ETA and SofaScore's
 * back-off; the event log over SSE (resumed with Last-Event-ID, a gap reloads the job); Stop for a running
 * job (also one of another process) and Run again for a finished one; the result of a finished job.
 */
const POLL_MS = 2000
const REFRESH_MS = 10000
const STALE_HEARTBEAT_S = 30

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const stopClock = useClock()

const id = computed(() => String(route.params.id))
const job = ref<Job | null>(null)
const loading = ref(true)
const error = ref<unknown>(null)
const lines = ref<LogLine[]>([])
const live = ref<ProgressView | null>(null)
const gapFrom = ref<number | null>(null)
const streamState = ref<'open' | 'polling' | 'closed'>('closed')
const filter = ref<'main' | 'all' | 'log' | 'failed'>('main')
const confirmStop = ref(false)
const stopping = ref(false)
const stopError = ref<unknown>(null)
const again = ref(false)

let stream: JobStream | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let refreshTimer: ReturnType<typeof setInterval> | null = null
let eventLog: JobEventMessage[] = []

const notFound = computed(() => error.value instanceof V1Error && error.value.code === 'not_found')
const terminal = computed(() => !!job.value && isTerminal(job.value.state))
const progress = computed(() => live.value ?? readProgress(job.value?.progress))
const result = computed(() => (job.value?.result ?? null) as Record<string, unknown> | null)
const failedList = computed(() => {
  const r = result.value?.failed
  return Array.isArray(r) ? (r as { match_id?: string | number }[]) : progress.value.failed
})
const runAgain = computed(() => (job.value && terminal.value ? rerunBody(job.value) : null))
const heartbeatAge = computed(() => {
  if (!job.value || job.value.state !== 'running' || !job.value.heartbeat_at) return null
  return Math.max(0, Math.round((clockNow.value - job.value.heartbeat_at) / 1000))
})
const title = computed(() => (job.value ? `${jobKindText(job.value.kind, job.value.spec)} · ${jobTarget(job.value)}` : t('ui.nav.jobs')))
const took = computed(() => {
  if (!job.value) return null
  const end = job.value.finished_at ?? (terminal.value ? null : new Date(clockNow.value).toISOString())
  return secondsBetween(job.value.started_at ?? job.value.created_at, end)
})
const shownLines = computed(() => {
  if (filter.value === 'all') return lines.value
  if (filter.value === 'log') return lines.value.filter((l) => l.type === 'log')
  if (filter.value === 'failed') return lines.value.filter((l) => l.type === 'failed' || l.type === 'breaker')
  return lines.value.filter((l) => l.type !== 'progress')
})
const waitLeft = computed(() => {
  const w = progress.value.wait
  if (!w || terminal.value) return null
  const s = Math.round(w.until - clockNow.value / 1000)
  return s > 0 ? s : null
})
const facts = computed(() => {
  const j = job.value
  if (!j) return []
  return [
    { key: 'id', label: t('ui.job.fact.id'), value: j.id, mono: true },
    { key: 'kind', label: t('ui.job.fact.kind'), value: jobKindText(j.kind, j.spec) },
    { key: 'target', label: t('ui.job.fact.target'), value: jobTarget(j) },
    { key: 'origin', label: t('ui.job.fact.origin'), value: [faceText(j.origin.face), j.origin.host, j.origin.pid != null ? `pid ${j.origin.pid}` : null].filter(Boolean).join(' · ') },
    { key: 'created', label: t('ui.job.fact.created') },
    { key: 'started', label: t('ui.job.fact.started') },
    { key: 'finished', label: t('ui.job.fact.finished') },
    { key: 'cancel', label: t('ui.job.fact.cancel'), value: j.cancel_requested ? t('ui.common.yes') : t('ui.common.no') },
  ]
})

async function load() {
  try {
    job.value = await v1.job(id.value)
    error.value = null
    const league = jobLeague(job.value)
    if (league) nameSeasons(league)
    if (terminal.value) live.value = null
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

function addEvent(e: JobEventMessage) {
  eventLog.push(e)
  if (e.type === 'progress') live.value = readProgress(e.data)
  lines.value = [...lines.value, logLine(e)]
  const league = seasonsWanted(e)
  if (league) nameSeasons(league)
  if (e.type === 'finished' || e.type === 'cancel_requested') void load()
}

/**
 * The season names of a league, read once (the stored season list, this server only), so that the log says
 * "season 2025" instead of "season #76138" (FX-24 F8); the lines already shown are written again then.
 */
const seasonsAsked = new Set<number>()
function nameSeasons(league: number) {
  if (seasonsAsked.has(league)) return
  seasonsAsked.add(league)
  loadSeasons(league)
    .then(() => (lines.value = eventLog.map(logLine)))
    .catch(() => {})
}

function openStream(after = 0) {
  stream?.close()
  stream = new JobStream(
    id.value,
    {
      onEvent: addEvent,
      onGap: (oldest) => {
        gapFrom.value = oldest
        void load().then(() => openStream(oldest - 1))
      },
      onError: (final) => {
        // A job that has ended has nothing more to send: the browser must not reconnect for ever
        if (terminal.value) {
          stream?.close()
          streamState.value = 'closed'
          return
        }
        if (final) startPolling()
        else void load()
      },
    },
    after,
  )
  try {
    stream.open()
    streamState.value = 'open'
  } catch {
    startPolling()
  }
}

/** Without a stream (501, no `sse-starlette`): the job is read every 2 s until it ends. */
function startPolling() {
  stream?.close()
  streamState.value = 'polling'
  if (pollTimer) return
  pollTimer = setInterval(() => {
    void load().then(() => {
      if (terminal.value && pollTimer) {
        clearInterval(pollTimer)
        pollTimer = null
        streamState.value = 'closed'
      }
    })
  }, POLL_MS)
}

function teardown() {
  stream?.close()
  stream = null
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
  if (refreshTimer) clearInterval(refreshTimer)
  refreshTimer = null
}

async function start() {
  teardown()
  job.value = null
  lines.value = []
  live.value = null
  gapFrom.value = null
  eventLog = []
  loading.value = true
  error.value = null
  await load()
  if (!job.value) return
  openStream(0)
  // The row itself (heartbeat, cancel flag) is read again every 10 s while the job runs
  refreshTimer = setInterval(() => {
    if (!terminal.value && document.visibilityState !== 'hidden') void load()
  }, REFRESH_MS)
}

async function stop() {
  stopping.value = true
  stopError.value = null
  try {
    job.value = await v1.cancelJob(id.value)
    confirmStop.value = false
    toast({ kind: 'info', text: t('ui.job.stopAsked') })
  } catch (e) {
    stopError.value = e
  } finally {
    stopping.value = false
  }
}

function download() {
  const text = eventLog.map((e) => JSON.stringify(e)).join('\n') + '\n'
  const url = URL.createObjectURL(new Blob([text], { type: 'application/x-ndjson' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `job-${id.value}-events.jsonl`
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// The wait countdown and the heartbeat age need a clock in seconds while the job runs
const ticker = setInterval(() => {
  if (!terminal.value) clockNow.value = Date.now()
}, 1000)

watch(id, () => void start())
onMounted(() => {
  // the league's name for the title and the target
  void loadTournaments()
  void start()
})
onUnmounted(() => {
  teardown()
  stopClock()
  clearInterval(ticker)
})
</script>

<template>
  <div>
    <div v-if="loading && !job" class="u-card p-6"><SkeletonBlock :lines="5" /></div>

    <div v-else-if="notFound" class="u-card">
      <EmptyState icon="jobs" :title="t('ui.job.notFound')" :text="t('ui.job.notFoundText', { id })">
        <RouterLink to="/jobs" class="u-btn">{{ t('ui.job.backToJobs') }}</RouterLink>
      </EmptyState>
    </div>

    <div v-else-if="error && !job" class="u-card"><ErrorState :error="error" @retry="start" /></div>

    <template v-else-if="job">
      <PageHeader :title="title" :crumbs="[{ label: t('ui.nav.jobs'), to: '/jobs' }]">
        <template #meta>
          <StatusBadge kind="job" :value="job.state" />
          <span class="u-small u-muted">{{ t('ui.job.startedBy', { face: faceText(job.origin.face) }) }}</span>
          <span class="u-small u-muted"><TimeText :value="job.started_at ?? job.created_at" /></span>
          <span v-if="took != null" class="u-small u-muted u-num">{{ duration(took) }}</span>
          <span v-if="job.cancel_requested && !terminal" class="u-small" style="color: var(--warn-fg)">{{ t('ui.job.cancelPending') }}</span>
        </template>
        <template #actions>
          <button v-if="!terminal && !job.cancel_requested" type="button" class="u-btn u-btn-danger" @click="confirmStop = true">
            <UiIcon name="stop" :size="14" />{{ t('ui.job.stop') }}
          </button>
          <button v-if="runAgain" type="button" class="u-btn u-btn-primary" @click="again = true"><UiIcon name="refresh" :size="14" />{{ t('ui.job.runAgain') }}</button>
        </template>
      </PageHeader>

      <div v-if="error" class="u-card mb-4"><ErrorState compact :error="error" @retry="load" /></div>

      <section v-if="!terminal" class="u-card p-6 mb-6 flex flex-col gap-3" aria-live="polite" data-testid="job-progress">
        <p class="m-0 u-h3">
          <template v-if="progress.phase">{{ t('ui.job.phaseOf', { i: progress.phaseIndex ?? '?', n: progress.phaseCount ?? '?', phase: phaseText(progress.phase) }) }}</template>
          <template v-else>{{ t('ui.job.waitingStart') }}</template>
        </p>
        <ProgressBar :value="progress.percent" :label="t('ui.job.progressLabel')" :text="progress.total ? countsText(progress, num) : undefined" />
        <p class="m-0 u-small u-muted flex flex-wrap gap-x-4">
          <span v-if="progress.total">{{ countsText(progress, num) }}</span>
          <span v-if="progress.eta">{{ t('ui.job.eta', { time: duration(progress.eta) }) }}</span>
          <span v-if="progress.failedCount" style="color: var(--danger)">{{ t('ui.jobs.failedCount', { n: num(progress.failedCount) }) }}</span>
        </p>
        <p v-if="waitLeft && progress.wait" class="m-0 u-small flex items-center gap-2" style="color: var(--warn-fg)">
          <UiIcon name="pause" :size="14" />{{ t('ui.job.waiting', { n: waitLeft, why: waitText(progress.wait.reason) }) }}
        </p>
        <p v-if="progress.breaker" class="m-0 u-small" style="color: var(--danger)">{{ t('ui.job.event.breaker', { reason: breakerText(progress.breaker) }) }}</p>
        <p v-if="heartbeatAge != null && heartbeatAge > STALE_HEARTBEAT_S" class="m-0 u-small" role="alert" style="color: var(--warn-fg)">
          {{ t('ui.job.noHeartbeat', { time: duration(heartbeatAge) }) }}
        </p>
      </section>

      <section v-else class="u-card p-6 mb-6 flex flex-col gap-3" data-testid="job-result">
        <h2 class="u-h3">{{ t('ui.job.result') }}</h2>
        <p v-if="job.error" class="m-0" style="color: var(--danger)">
          {{ jobErrorText(job) }}<span v-if="codeText(job.error.details?.code, job.error.details)" class="u-muted"> {{ codeText(job.error.details?.code, job.error.details) }}</span>
        </p>
        <p v-if="job.error?.message" class="m-0 u-small u-muted">{{ job.error.message }}</p>
        <dl v-if="result" class="u-result">
          <template v-if="result.details_total != null">
            <dt>{{ t('ui.job.details') }}</dt>
            <dd class="u-num">{{ countsText({ phase: 'details', done: Number(result.details_done) || 0, total: Number(result.details_total) || 0 }, num) }}</dd>
          </template>
          <template v-if="result.failed_count != null">
            <dt>{{ t('ui.job.failedItems') }}</dt>
            <dd class="u-num">{{ num(Number(result.failed_count) || 0) }}</dd>
          </template>
          <template v-if="result.refreshed != null">
            <dt>{{ t('ui.job.refreshed') }}</dt>
            <dd class="u-num">{{ t('ui.job.refreshedText', { n: num(Number(result.refreshed) || 0), changed: num(Number(result.refresh_changed) || 0) }) }}</dd>
          </template>
          <template v-if="result.breaker">
            <dt>{{ t('ui.job.stoppedBy') }}</dt>
            <dd>{{ breakerText(String(result.breaker)) }}</dd>
          </template>
        </dl>
        <p v-else-if="!job.error" class="m-0 u-muted">{{ t('ui.job.noResult') }}</p>
        <JobOutput :job="job" />
        <div v-if="failedList.length" class="flex flex-col gap-1">
          <p class="m-0 u-small u-muted">{{ t('ui.job.failedList') }}</p>
          <ul class="m-0 p-0 list-none flex flex-wrap gap-2">
            <li v-for="f in failedList.slice(0, 50)" :key="String(f.match_id)">
              <RouterLink :to="`/events/${f.match_id}`" class="u-mono">{{ f.match_id }}</RouterLink>
            </li>
          </ul>
        </div>
      </section>

      <div class="grid gap-6 lg:grid-cols-3">
        <section class="u-card lg:col-span-2 min-w-0" data-testid="job-log">
          <header class="flex flex-wrap items-center gap-3 px-5 py-3" style="border-bottom: 1px solid var(--line)">
            <h2 class="u-h3 flex-1">{{ t('ui.job.log') }}</h2>
            <span v-if="streamState === 'open' && !terminal" class="u-small u-muted flex items-center gap-1"><span class="u-spinner" aria-hidden="true" style="width: 10px; height: 10px; border-width: 1.5px"></span>{{ t('ui.job.streamLive') }}</span>
            <span v-else-if="streamState === 'polling'" class="u-small u-muted">{{ t('ui.job.streamPolling') }}</span>
            <label class="flex items-center gap-2 u-small">
              <span class="u-sr">{{ t('ui.job.logFilter') }}</span>
              <select v-model="filter" class="u-field" style="height: 32px; width: auto" :aria-label="t('ui.job.logFilter')">
                <option value="main">{{ t('ui.job.logShow.main') }}</option>
                <option value="all">{{ t('ui.job.logShow.all') }}</option>
                <option value="log">{{ t('ui.job.logShow.log') }}</option>
                <option value="failed">{{ t('ui.job.logShow.failed') }}</option>
              </select>
            </label>
            <button type="button" class="u-btn u-btn-sm" :disabled="!lines.length" @click="download"><UiIcon name="exports" :size="14" />{{ t('ui.job.download') }}</button>
          </header>
          <p v-if="gapFrom" class="m-0 px-5 py-2 u-small" style="background: var(--info-bg); color: var(--info-fg)">{{ t('ui.job.gap', { n: num(gapFrom) }) }}</p>
          <ol class="u-log" :aria-label="t('ui.job.log')">
            <li v-for="line in shownLines" :key="line.seq" :data-type="line.type">
              <time class="u-mono u-muted" :datetime="new Date(line.ts).toISOString()">{{ formatTime(new Date(line.ts), 'seconds') }}</time>
              <span :class="{ 'u-mono': line.raw }" class="flex-1 min-w-0 break-words">{{ line.text }}</span>
              <RouterLink v-if="line.eventId" :to="`/events/${line.eventId}`" class="u-small" :aria-label="t('ui.job.openEvent', { id: line.eventId })">→</RouterLink>
            </li>
            <li v-if="!shownLines.length" class="u-muted">{{ t('ui.job.logEmpty') }}</li>
          </ol>
        </section>
        <aside class="u-card p-5 min-w-0" :aria-label="t('ui.job.facts')">
          <FactList :items="facts">
            <template #value-id><span class="inline-flex items-center gap-1">{{ job.id }}<CopyButton :text="job.id" :label="t('ui.job.copyId')" /></span></template>
            <template #value-created><TimeText :value="job.created_at" /></template>
            <template #value-started><TimeText :value="job.started_at" /></template>
            <template #value-finished><TimeText :value="job.finished_at" /></template>
          </FactList>
        </aside>
      </div>

      <ConfirmDialog
        v-if="confirmStop"
        :title="t('ui.job.stopTitle')"
        :confirm-label="t('ui.job.stop')"
        danger
        :busy="stopping"
        :error="stopError"
        @confirm="stop"
        @close="confirmStop = false"
      >
        <p class="m-0">{{ t('ui.job.stopText') }}</p>
        <p v-if="job.origin.face !== 'api'" class="m-0 u-small u-muted">{{ t('ui.job.stopOther', { face: faceText(job.origin.face) }) }}</p>
      </ConfirmDialog>
      <StartJobDialog v-if="again && runAgain" :body="runAgain" again @close="again = false" @started="(j) => router.push(`/jobs/${j.id}`)" />
    </template>
  </div>
</template>

<style>
.u-log {
  margin: 0;
  padding: var(--sp-3) var(--sp-5) var(--sp-5);
  list-style: none;
  max-height: 520px;
  overflow-y: auto;
}
.u-log li {
  display: flex;
  gap: var(--sp-4);
  padding: var(--sp-2) 0;
  border-top: 1px solid var(--line);
}
.u-log li:first-child {
  border-top: 0;
}
.u-log li[data-type='failed'],
.u-log li[data-type='breaker'] {
  color: var(--danger);
}
</style>
