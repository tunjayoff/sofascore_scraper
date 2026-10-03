<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import UiBadge from '@/ui/UiBadge.vue'
import FactList from '@/ui/FactList.vue'
import TimeText from '@/ui/TimeText.vue'
import ErrorState from '@/ui/ErrorState.vue'
import FormError from '@/ui/FormError.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import CodeHint from '@/ui/CodeHint.vue'
import UiIcon from '@/ui/UiIcon.vue'
import { v1 } from '@/api/v1/client'
import type { StatusCheck } from '@/api/v1/schema'
import { diskBytes, useStatusStore } from '@/app/statusStore'
import { sportName } from '@/app/sports'
import { liveState } from '@/ui/status'
import { bytesText, duration, now as clockNow, num, useClock } from '@/ui/time'

/**
 * Health (6.12): the state of the services, read-only, from `/api/v1/status`, with one action: the
 * connection check (one request to SofaScore, on click only). The live service card never starts or stops
 * anything (R2, decision 17); a `direct` source gets its warning. Who holds the data folder comes from
 * the leases; the storage card from the data summary. The scheduler's next runs are not reported yet (P29).
 */
const STALE_LIVE_S = 120
const { t, te } = useI18n()
const store = useStatusStore()
const s = computed(() => store.status)
const stopClock = useClock()
onUnmounted(stopClock)

const connection = computed(() => {
  const b = s.value?.bridge
  if (!b) return []
  return [
    { key: 'lastSuccess', label: t('ui.health.lastSuccess') },
    { key: 'failures', label: t('ui.health.failures'), value: num(b.consecutive_failures) },
    { key: 'failingSince', label: t('ui.health.failingSince') },
    { key: 'lastError', label: t('ui.health.lastError') },
    { key: 'changedAt', label: t('ui.health.changedAt') },
  ]
})
const throttle = computed(() => {
  const th = s.value?.throttle
  if (!th) return []
  return [
    {
      key: 'rate',
      label: t('ui.health.rate'),
      value: th.enabled && th.requests_per_second > 0 ? t('ui.health.rateValue', { n: num(th.requests_per_second) }) : t('ui.health.noLimit'),
    },
    { key: 'shared', label: t('ui.health.shared'), value: th.shared ? t('ui.health.sharedYes') : t('ui.health.sharedNo') },
  ]
})

// ---- live service ----
const live = computed(() => s.value?.live ?? null)
const live_ = computed(() => liveState(live.value))
const heartbeatMs = computed(() => (live.value?.heartbeat_at ? live.value.heartbeat_at * 1000 : null))
const heartbeatAge = computed(() => (heartbeatMs.value ? Math.max(0, Math.round((clockNow.value - heartbeatMs.value) / 1000)) : null))
const leaders = computed(() => Object.entries(live.value?.leaders ?? {}))
const direct = computed(() => live.value?.source === 'direct' || leaders.value.some(([, src]) => src === 'direct'))
type Switch = { sport?: string; from?: string; to?: string; reason?: string; at?: number }
const lastSwitch = computed(() => (live.value?.last_switch ?? null) as Switch | null)
const liveFacts = computed(() => {
  const l = live.value
  if (!l || !l.running) return []
  return [
    { key: 'by', label: t('ui.health.liveBy'), value: ['ssc watch', l.pid != null ? `pid ${l.pid}` : null, l.host].filter(Boolean).join(' · ') },
    { key: 'sports', label: t('ui.health.liveSports'), value: (l.sports ?? []).map(sportName).join(', ') || '—' },
    { key: 'source', label: t('ui.health.liveSource') },
    { key: 'switch', label: t('ui.health.liveSwitch') },
    { key: 'heartbeat', label: t('ui.health.liveHeartbeat') },
  ]
})
function sourceText(src: string | null | undefined) {
  if (!src) return '—'
  const key = `ui.health.source.${src}`
  return te(key, 'en') ? t(key) : src
}

// ---- leases and storage ----
const leases = computed(() => s.value?.leases ?? [])
function leaseName(name: string) {
  if (name.startsWith('watcher:')) return t('ui.health.lease.watcher', { sport: sportName(name.slice(8)) })
  const key = `ui.health.lease.${name}`
  return te(key, 'en') ? t(key) : name
}
const summary = computed(() => s.value?.summary ?? null)
const storage = computed(() => [
  { key: 'size', label: t('ui.health.size'), value: summary.value?.disk ? bytesText(diskBytes(summary.value)) : '—' },
  { key: 'index', label: t('ui.health.index') },
  { key: 'version', label: t('ui.health.version'), value: s.value ? `${s.value.version} · API ${s.value.api_version} · schema ${s.value.schema_version}` : '—', mono: true },
  { key: 'token', label: t('ui.health.token'), value: s.value ? (s.value.auth_required ? t('ui.common.yes') : t('ui.common.no')) : '—' },
])

function errorKind(kind: string | undefined) {
  const key = `ui.health.errorKind.${kind}`
  return kind ? (te(key, 'en') ? t(key) : kind) : ''
}

// ---- connection check: one request to SofaScore, on click only ----
const checking = ref(false)
const check = ref<StatusCheck | null>(null)
const checkError = ref<unknown>(null)
async function runCheck() {
  checking.value = true
  checkError.value = null
  try {
    check.value = await v1.checkConnection()
    void store.refresh().catch(() => {})
  } catch (e) {
    checkError.value = e
    check.value = null
  } finally {
    checking.value = false
  }
}
function reasonText(reason: string | null | undefined) {
  const key = `ui.health.check.reason.${reason}`
  return reason && te(key, 'en') ? t(key) : t('ui.health.check.reason.upstream')
}

// Inside the shell the status is already polled; opened on its own, the screen reads it once
onMounted(() => {
  if (!store.status && !store.loading) void store.refresh().catch(() => {})
})
</script>

<template>
  <div>
    <PageHeader :title="t('ui.nav.health')" :description="t('ui.health.description')">
      <template #actions>
        <button type="button" class="u-btn" :disabled="store.loading" @click="store.refresh().catch(() => {})">
          <UiIcon name="refresh" :size="16" />{{ t('ui.common.refresh') }}
        </button>
      </template>
    </PageHeader>

    <div v-if="!s && store.loading" class="u-card p-6"><SkeletonBlock :lines="6" /></div>
    <div v-else-if="!s && store.error" class="u-card"><ErrorState :error="store.error" @retry="store.refresh().catch(() => {})" /></div>

    <div v-else-if="s" class="grid gap-6 lg:grid-cols-2">
      <section class="u-card p-6 flex flex-col gap-4" data-testid="health-connection">
        <header class="flex items-center gap-3"><h2 class="u-h3 flex-1">{{ t('ui.health.connection') }}</h2><StatusBadge kind="connection" :value="s.bridge.state" /></header>
        <FactList :items="connection">
          <template #value-lastSuccess><TimeText :value="s.bridge.last_success_at" relative /></template>
          <template #value-failingSince><TimeText :value="s.bridge.failing_since" /></template>
          <template #value-changedAt><TimeText :value="s.bridge.changed_at" /></template>
          <template #value-lastError>
            <span v-if="s.bridge.last_error">{{ errorKind(s.bridge.last_error.kind) }} · <TimeText :value="s.bridge.last_error.at" relative /></span>
            <span v-else>—</span>
          </template>
        </FactList>
        <div class="flex flex-col gap-2 pt-2" style="border-top: 1px solid var(--line)">
          <h3 class="u-h3">{{ t('ui.health.requestBudget') }}</h3>
          <FactList :items="throttle" />
          <p v-if="s.throttle.error" class="m-0 u-small" role="alert" style="color: var(--warn-fg)">{{ t('ui.health.budgetError') }}</p>
        </div>
        <div class="flex flex-col gap-2 pt-2" style="border-top: 1px solid var(--line)" data-testid="connection-check">
          <div class="flex flex-wrap items-center gap-3">
            <button type="button" class="u-btn" :disabled="checking" @click="runCheck">
              <span v-if="checking" class="u-spinner" aria-hidden="true"></span><UiIcon v-else name="external" :size="16" />{{ t('ui.health.check.button') }}
            </button>
            <span class="u-small u-muted">{{ t('ui.health.check.note') }}</span>
          </div>
          <p v-if="check?.ok" class="m-0 u-small" role="status" style="color: var(--ok-fg)">
            {{ t('ui.health.check.ok', { n: num(check.events_count ?? 0) }) }} · <TimeText :value="check.checked_at_utc" format="seconds" />
          </p>
          <p v-else-if="check" class="m-0 u-small" role="alert" style="color: var(--danger)">
            {{ t('ui.health.check.failed', { reason: reasonText(check.reason) }) }} · <TimeText :value="check.checked_at_utc" format="seconds" />
          </p>
          <FormError v-if="checkError" :error="checkError" />
        </div>
      </section>

      <section class="u-card p-6 flex flex-col gap-4" data-testid="health-live">
        <header class="flex items-center gap-3">
          <h2 class="u-h3 flex-1">{{ t('ui.health.live') }}</h2>
          <UiBadge v-if="direct" tone="warn" icon="alert">{{ t('ui.health.direct') }}</UiBadge>
          <StatusBadge kind="live" :value="live_" />
        </header>
        <template v-if="live?.running">
          <FactList :items="liveFacts">
            <template #value-source>
              <span v-if="leaders.length" class="flex flex-col">
                <span v-for="[sport, src] in leaders" :key="sport">{{ sportName(sport) }} · {{ sourceText(src) }}</span>
              </span>
              <span v-else>{{ sourceText(live.source) }}</span>
            </template>
            <template #value-switch>
              <span v-if="lastSwitch">{{ sportName(lastSwitch.sport) }} · {{ sourceText(lastSwitch.from) }} → {{ sourceText(lastSwitch.to) }}<template v-if="lastSwitch.at"> · <TimeText :value="lastSwitch.at * 1000" format="time" /></template></span>
              <span v-else>—</span>
            </template>
            <template #value-heartbeat><TimeText :value="heartbeatMs" relative /></template>
          </FactList>
          <p v-if="heartbeatAge != null && heartbeatAge > STALE_LIVE_S" class="m-0 u-small" role="alert" style="color: var(--warn-fg)">{{ t('ui.health.liveStale', { time: duration(heartbeatAge) }) }}</p>
          <p v-if="direct" class="m-0 u-small" style="color: var(--warn-fg)">{{ t('ui.health.directText') }}</p>
        </template>
        <template v-else-if="live">
          <p class="m-0">{{ t('ui.health.liveOff') }}</p>
          <div><CodeHint command="ssc watch" /></div>
          <p v-if="heartbeatMs" class="m-0 u-small u-muted">{{ t('ui.health.liveLast') }} <TimeText :value="heartbeatMs" /></p>
        </template>
        <p v-else class="m-0 u-muted">{{ t('ui.health.liveUnknown') }}</p>
        <p class="m-0 u-small u-muted">{{ t('ui.health.liveNote') }} <RouterLink to="/system/sinks">{{ t('ui.nav.sinks') }}</RouterLink></p>
      </section>

      <section class="u-card p-6 flex flex-col gap-4" data-testid="health-leases">
        <h2 class="u-h3">{{ t('ui.health.leases') }}</h2>
        <ul v-if="leases.length" class="m-0 p-0 list-none flex flex-col">
          <li v-for="l in leases" :key="l.name" class="flex flex-wrap gap-x-4 gap-y-1 py-2" style="border-top: 1px solid var(--line)">
            <span class="font-semibold min-w-[120px]">{{ leaseName(l.name) }}</span>
            <span class="u-small flex-1">{{ [l.purpose, l.pid != null ? `pid ${l.pid}` : null, l.host].filter(Boolean).join(' · ') || '—' }}</span>
            <span v-if="l.since_utc" class="u-small u-muted">{{ t('ui.health.since') }} <TimeText :value="l.since_utc" relative /></span>
          </li>
        </ul>
        <p v-else class="m-0 u-muted">{{ t('ui.health.noLeases') }}</p>
        <template v-if="s.active_job">
          <p class="m-0 flex flex-wrap items-center gap-2 pt-2" style="border-top: 1px solid var(--line)">
            <span class="u-muted">{{ t('ui.health.runningJob') }}</span>
            <StatusBadge kind="job" :value="s.active_job.state" />
            <RouterLink :to="`/jobs/${s.active_job.id}`" class="u-mono">{{ s.active_job.id }}</RouterLink>
          </p>
        </template>
      </section>

      <section class="u-card p-6 flex flex-col gap-4" data-testid="health-scheduler">
        <header class="flex items-center gap-3">
          <h2 class="u-h3 flex-1">{{ t('ui.health.scheduler') }}</h2>
          <UiBadge :tone="s.capabilities.scheduler ? 'ok' : 'neutral'" :icon="s.capabilities.scheduler ? 'okCircle' : 'circle'">{{ s.capabilities.scheduler ? t('ui.common.on') : t('ui.common.off') }}</UiBadge>
        </header>
        <p class="m-0">{{ s.capabilities.scheduler ? t('ui.health.schedulerOn') : t('ui.health.schedulerOff') }}</p>
        <p class="m-0 u-small u-muted">{{ t('ui.health.schedulerLater') }}</p>
      </section>

      <section class="u-card p-6 flex flex-col gap-4 lg:col-span-2" data-testid="health-storage">
        <h2 class="u-h3">{{ t('ui.health.storage') }}</h2>
        <p v-if="s.storage_error" class="m-0 u-notice u-notice-warn" role="alert"><UiIcon name="alert" :size="16" />{{ t('ui.health.storageError') }} <span class="u-mono">{{ s.storage_error }}</span></p>
        <FactList :items="storage">
          <template #value-index>
            <span v-if="summary?.catalog_rebuild_reason" class="flex flex-wrap items-center gap-2">
              <UiBadge tone="warn" icon="alert">{{ t('ui.maintenance.rebuild.needed') }}</UiBadge>
              <RouterLink to="/maintenance">{{ t('ui.nav.maintenance') }}</RouterLink>
            </span>
            <span v-else-if="summary">{{ t('ui.health.indexCurrent') }}</span>
            <span v-else>—</span>
          </template>
        </FactList>
      </section>
    </div>
  </div>
</template>
