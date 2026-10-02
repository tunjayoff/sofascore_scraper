<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import UiBadge from '@/ui/UiBadge.vue'
import FactList from '@/ui/FactList.vue'
import TimeText from '@/ui/TimeText.vue'
import ErrorState from '@/ui/ErrorState.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import CodeHint from '@/ui/CodeHint.vue'
import UiIcon from '@/ui/UiIcon.vue'
import { useStatusStore } from '@/app/statusStore'
import { num } from '@/ui/time'

/**
 * Health (6.12): the state of the services, read-only, from today's `/api/v1/status` (connection, request
 * budget, the running job, versions). The live service, the scheduler, the data folder and the
 * connection check come with later routes (P21, P29); their cards say so instead of guessing. The live
 * card never starts or stops anything (R2).
 */
const { t, te } = useI18n()
const store = useStatusStore()
const s = computed(() => store.status)

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
const storage = computed(() => [
  { key: 'version', label: t('ui.health.version'), value: s.value ? `${s.value.version} · API ${s.value.api_version}` : '—', mono: true },
  { key: 'token', label: t('ui.health.token'), value: s.value ? (s.value.auth_required ? t('ui.common.yes') : t('ui.common.no')) : '—' },
])

function errorKind(kind: string | undefined) {
  const key = `ui.health.errorKind.${kind}`
  return kind ? (te(key, 'en') ? t(key) : kind) : ''
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
        <p class="m-0 u-small u-muted">
          {{ t('ui.health.checkLater') }}
          <RouterLink to="/classic/settings">{{ t('ui.health.checkClassic') }}</RouterLink>
        </p>
      </section>

      <section class="u-card p-6 flex flex-col gap-4" data-testid="health-live">
        <header class="flex items-center gap-3"><h2 class="u-h3 flex-1">{{ t('ui.health.live') }}</h2><StatusBadge kind="live" value="unknown" /></header>
        <p class="m-0">{{ t('ui.health.liveLater') }}</p>
        <p class="m-0 u-small u-muted">{{ t('ui.health.liveNote') }}</p>
        <div><CodeHint command="ssc watch" /></div>
      </section>

      <section class="u-card p-6 flex flex-col gap-4">
        <header class="flex items-center gap-3"><h2 class="u-h3 flex-1">{{ t('ui.health.runningJob') }}</h2></header>
        <template v-if="s.active_job">
          <p class="m-0 flex flex-wrap items-center gap-2">
            <StatusBadge kind="job" :value="s.active_job.state" />
            <RouterLink :to="`/jobs/${s.active_job.id}`" class="u-mono">{{ s.active_job.id }}</RouterLink>
          </p>
        </template>
        <p v-else class="m-0 u-muted">{{ t('ui.health.noJob') }}</p>
      </section>

      <section class="u-card p-6 flex flex-col gap-4">
        <header class="flex items-center gap-3"><h2 class="u-h3 flex-1">{{ t('ui.health.scheduler') }}</h2><UiBadge tone="neutral" icon="planned">{{ t('ui.health.later') }}</UiBadge></header>
        <p class="m-0 u-muted">{{ t('ui.health.schedulerLater') }}</p>
      </section>

      <section class="u-card p-6 flex flex-col gap-4">
        <header class="flex items-center gap-3"><h2 class="u-h3 flex-1">{{ t('ui.health.storage') }}</h2></header>
        <FactList :items="storage" />
        <p class="m-0 u-small u-muted">{{ t('ui.health.storageLater') }}</p>
      </section>
    </div>
  </div>
</template>
