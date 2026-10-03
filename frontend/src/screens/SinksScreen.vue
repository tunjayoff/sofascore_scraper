<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import DataTable, { type Column } from '@/ui/DataTable.vue'
import EmptyState from '@/ui/EmptyState.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import TimeText from '@/ui/TimeText.vue'
import CodeHint from '@/ui/CodeHint.vue'
import UiIcon from '@/ui/UiIcon.vue'
import { v1 } from '@/api/v1/client'
import type { SinkStatus } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { poll } from '@/app/poll'
import { sinkState } from '@/ui/status'
import { duration, num } from '@/ui/time'

/**
 * Sinks (6.13, decision 18): read-only. Each configured sink with its state, how far behind it is (events
 * and the age of the oldest undelivered one) and its last delivery; which process delivers (the holder of
 * the `sinks` lease in `/status`). Sinks are configured in sofascore.toml; the web UI never adds an
 * outbound target (decision D11). Read every 15 s while the screen is open.
 */
const SINKS_EVERY_MS = 15000
const { t } = useI18n()
const status = useStatusStore()
const rows = ref<SinkStatus[]>([])
const loading = ref(true)
const loaded = ref(false)
const error = ref<unknown>(null)

async function load() {
  loading.value = true
  try {
    rows.value = await v1.sinks()
    error.value = null
    loaded.value = true
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

const holder = computed(() => status.status?.leases?.find((l) => l.name === 'sinks') ?? null)
const holderText = computed(() => {
  const h = holder.value
  if (!h) return null
  return [h.purpose, h.pid != null ? `pid ${h.pid}` : null, h.host].filter(Boolean).join(' · ')
})
const served = computed(() => !!holder.value || rows.value.some((s) => s.served))
const notes = computed(() => rows.value.filter((s) => s.last_error || s.dropped))

const columns = computed<Column<SinkStatus>[]>(() => [
  { key: 'name', label: t('ui.sinks.col.name'), card: 'title', sortable: true },
  { key: 'type', label: t('ui.sinks.col.type'), card: 'meta', sortable: true },
  { key: 'target', label: t('ui.sinks.col.target'), mono: true },
  { key: 'events', label: t('ui.sinks.col.events') },
  { key: 'state', label: t('ui.sinks.col.state'), card: 'badge', sortValue: (s) => sinkState(s) },
  { key: 'behind', label: t('ui.sinks.col.behind'), align: 'right', sortable: true, sortValue: (s) => s.lag_events, card: 'meta' },
  { key: 'last', label: t('ui.sinks.col.last'), sortable: true, sortValue: (s) => s.last_delivered_at_utc ?? '' },
  { key: 'dropped', label: t('ui.sinks.col.dropped'), optional: true, align: 'right' },
  { key: 'cursor', label: t('ui.sinks.col.cursor'), optional: true, align: 'right', mono: true },
])

function typeText(type: string) {
  return ['webhook', 'file', 'stdout'].includes(type) ? t(`ui.sinks.type.${type}`) : type
}

let stop: (() => void) | null = null
onMounted(() => {
  stop = poll(load, () => SINKS_EVERY_MS)
})
onUnmounted(() => stop?.())
</script>

<template>
  <div>
    <PageHeader :title="t('ui.nav.sinks')" :description="t('ui.sinks.description')">
      <template #actions>
        <button type="button" class="u-btn" :disabled="loading" @click="load"><UiIcon name="refresh" :size="16" />{{ t('ui.common.refresh') }}</button>
      </template>
    </PageHeader>

    <div class="flex flex-col gap-3 mb-4">
      <p class="m-0 u-notice"><UiIcon name="info" :size="16" /><span>{{ t('ui.sinks.configNote') }}</span></p>
      <p v-if="loaded && rows.length" class="m-0 flex flex-wrap items-center gap-2" data-testid="sinks-holder">
        <span class="u-muted">{{ t('ui.sinks.deliveredBy') }}</span>
        <span v-if="holderText" class="u-mono">{{ holderText }}</span>
        <span v-else-if="served">{{ t('ui.sinks.someProcess') }}</span>
        <span v-else class="flex flex-wrap items-center gap-2" style="color: var(--warn-fg)">{{ t('ui.sinks.nobody') }} <CodeHint command="ssc watch" /><CodeHint command="ssc serve" /></span>
      </p>
    </div>

    <DataTable
      table-id="sinks"
      :caption="t('ui.nav.sinks')"
      :columns="columns"
      :rows="rows"
      :row-key="(s) => s.name"
      :loading="loading && !loaded"
      :refreshing="loading && loaded"
      :error="error"
      :paged="false"
      @retry="load"
    >
      <template #cell-type="{ row }">{{ typeText(row.type) }}</template>
      <template #cell-target="{ row }"><span class="u-small break-all">{{ row.target ?? '—' }}</span></template>
      <template #cell-events="{ row }"><span class="u-small u-mono">{{ row.events.join(', ') || '*' }}</span></template>
      <template #cell-state="{ row }"><StatusBadge kind="sink" :value="sinkState(row)" /></template>
      <template #cell-behind="{ row }">
        <span class="u-num">{{ num(row.lag_events) }}</span><span v-if="row.lag_seconds" class="u-small u-muted"> · {{ duration(row.lag_seconds) }}</span>
      </template>
      <template #cell-last="{ row }"><TimeText :value="row.last_delivered_at_utc" relative /></template>
      <template #cell-dropped="{ row }"><span class="u-num">{{ num(row.dropped) }}</span></template>
      <template #cell-cursor="{ row }">{{ num(row.cursor) }} / {{ num(row.head_seq) }}</template>
      <template #empty>
        <EmptyState icon="sinks" :title="t('ui.sinks.empty')" :text="t('ui.sinks.emptyText')" />
      </template>
    </DataTable>

    <section v-if="notes.length" class="u-card p-5 mt-4 flex flex-col gap-2" data-testid="sinks-notes">
      <h2 class="u-h3">{{ t('ui.sinks.details') }}</h2>
      <p v-for="s in notes" :key="s.name" class="m-0 u-small">
        <span class="font-semibold">{{ s.name }}:</span>
        <template v-if="s.last_error"> {{ t('ui.sinks.lastError') }} <span class="u-mono">{{ s.last_error }}</span></template>
        <template v-if="s.dropped"> · {{ t('ui.sinks.droppedText', { n: num(s.dropped) }) }}</template>
      </p>
    </section>
  </div>
</template>
