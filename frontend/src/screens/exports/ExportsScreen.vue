<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import DataTable, { type Column } from '@/ui/DataTable.vue'
import EmptyState from '@/ui/EmptyState.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import TimeText from '@/ui/TimeText.vue'
import UiIcon from '@/ui/UiIcon.vue'
import UiBadge from '@/ui/UiBadge.vue'
import { v1 } from '@/api/v1/client'
import type { ExportFilter, ExportRecord } from '@/api/v1/schema'
import { usePagedList } from '@/app/pagedList'
import { sportName } from '@/app/sports'
import { bytesText, num } from '@/ui/time'
import { exportText, isTerminal } from '@/screens/jobs/jobText'
import ExportDialog from './ExportDialog.vue'

/**
 * Exports (6.10): every export with its file, newest first; the file is downloaded at full size from the
 * server. An export runs as a job: a running one is listed with its state and a link to the job, and the
 * list is read again every 5 s while one runs. "New export" offers what the API writes (normalized data
 * as CSV, JSONL, Parquet or SQLite, the match table, the original data); `?new=1` (the quick search's
 * "Export") opens it at once. Files have readable names (league or dataset and date, FX-19), and the
 * files `ssc export` wrote on the server are listed too (`source: "file"`), with no job to open.
 */
const RUNNING_EVERY_MS = 5000
const { t } = useI18n()
const list = usePagedList<ExportRecord>(({ cursor, size, signal }) => v1.exports({ cursor, limit: size }, signal), { serverKeys: [] })
const creating = ref(false)
const route = useRoute()
const router = useRouter()

function filterText(f: ExportFilter): string {
  const parts: string[] = []
  if (f.sport) parts.push(sportName(f.sport))
  if (f.tournament_ids?.length) parts.push(t('ui.exports.filter.tournaments', { n: f.tournament_ids.length }))
  if (f.season_ids?.length) parts.push(t('ui.exports.filter.seasons', { n: f.season_ids.length }))
  if (f.event_ids?.length) parts.push(t('ui.exports.filter.events', { n: f.event_ids.length }))
  if (f.team_ids?.length) parts.push(t('ui.exports.filter.teams', { n: f.team_ids.length }))
  if (f.player_ids?.length) parts.push(t('ui.exports.filter.players', { n: f.player_ids.length }))
  return parts.join(' · ') || t('ui.exports.filter.all')
}

const columns = computed<Column<ExportRecord>[]>(() => [
  { key: 'created', label: t('ui.exports.col.created'), card: 'meta', sortable: true, sortValue: (r) => r.created_at ?? '' },
  { key: 'what', label: t('ui.exports.col.what'), card: 'title', sortValue: (r) => exportText(r) },
  { key: 'filter', label: t('ui.exports.col.filter'), card: 'meta' },
  { key: 'state', label: t('ui.exports.col.state'), card: 'badge' },
  { key: 'rows', label: t('ui.exports.col.rows'), align: 'right', sortable: true, sortValue: (r) => r.rows ?? -1 },
  { key: 'size', label: t('ui.exports.col.size'), align: 'right', sortable: true, sortValue: (r) => r.bytes ?? -1 },
  { key: 'file', label: t('ui.exports.col.file'), card: 'meta', mono: true },
  { key: 'id', label: t('ui.exports.col.job'), optional: true, mono: true },
])

const running = computed(() => list.rows.value.some((r) => !isTerminal(r.state)))
let timer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  if (route.query.new === '1') {
    creating.value = true
    void router.replace({ query: { ...route.query, new: undefined } })
  }
  void list.load()
  timer = setInterval(() => {
    if (running.value && document.visibilityState !== 'hidden') void list.load()
  }, RUNNING_EVERY_MS)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div>
    <PageHeader :title="t('ui.nav.exports')" :description="t('ui.exports.description')">
      <template #actions>
        <button type="button" class="u-btn" :disabled="list.loading.value" @click="list.load()"><UiIcon name="refresh" :size="16" />{{ t('ui.common.refresh') }}</button>
        <button type="button" class="u-btn u-btn-primary" @click="creating = true"><UiIcon name="plus" :size="16" />{{ t('ui.exports.new') }}</button>
      </template>
    </PageHeader>

    <DataTable
      table-id="exports"
      :caption="t('ui.nav.exports')"
      :columns="columns"
      :rows="list.rows.value"
      :row-key="(r) => r.id"
      :row-to="(r) => (r.job_id ? `/jobs/${r.job_id}` : null)"
      :loading="list.loading.value && !list.loadedOnce.value"
      :refreshing="list.loading.value && list.loadedOnce.value"
      :error="list.error.value"
      :has-prev="list.hasPrev.value"
      :has-next="!!list.next.value"
      :page-size="list.size.value"
      @update:page-size="(n) => list.setQuery({ size: n })"
      @prev="list.prev"
      @next="list.forward"
      @retry="list.load"
    >
      <template #cell-created="{ row }"><TimeText :value="row.created_at" /></template>
      <template #cell-what="{ row }">{{ exportText(row) }}</template>
      <template #cell-filter="{ row }"><span class="u-small">{{ filterText(row.filter) }}</span></template>
      <template #cell-state="{ row }"><StatusBadge kind="job" :value="row.state" /></template>
      <template #cell-rows="{ row }"><span class="u-num">{{ row.rows != null ? num(row.rows) : '—' }}</span></template>
      <template #cell-size="{ row }"><span class="u-num">{{ bytesText(row.bytes) }}</span></template>
      <template #cell-file="{ row }">
        <span class="inline-flex flex-wrap items-center gap-2"
          >{{ row.file ?? '—' }}<UiBadge v-if="row.source === 'file'" tone="neutral" :title="t('ui.exports.fromCliHint')" data-testid="export-from-cli">{{ t('ui.exports.fromCli') }}</UiBadge></span
        >
      </template>
      <template #cell-id="{ row }">{{ row.job_id ?? '—' }}</template>
      <template #row-actions="{ row }">
        <a v-if="row.available" :href="v1.exportUrl(row.id)" download class="u-btn u-btn-sm" :aria-label="t('ui.exports.downloadOne', { name: row.file ?? row.id })">
          <UiIcon name="exports" :size="14" />{{ t('ui.exports.download') }}
        </a>
        <RouterLink v-else-if="row.job_id" :to="`/jobs/${row.job_id}`" class="u-btn u-btn-sm u-btn-ghost">{{ t('ui.exports.openJob') }}</RouterLink>
      </template>
      <template #empty>
        <EmptyState icon="exports" :title="t('ui.exports.empty')" :text="t('ui.exports.emptyText')">
          <button type="button" class="u-btn" @click="creating = true">{{ t('ui.exports.new') }}</button>
        </EmptyState>
      </template>
    </DataTable>

    <ExportDialog v-if="creating" @close="creating = false" @started="list.load()" />
  </div>
</template>
