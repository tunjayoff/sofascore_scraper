<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import DataTable, { type Column, type Sort } from '@/ui/DataTable.vue'
import FilterBar from '@/ui/FilterBar.vue'
import EmptyState from '@/ui/EmptyState.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import TimeText from '@/ui/TimeText.vue'
import UiMenu, { type MenuItem } from '@/ui/UiMenu.vue'
import { v1, type StartJobBody } from '@/api/v1/client'
import type { Job, JobKind, JobState } from '@/api/v1/schema'
import { poll } from '@/app/poll'
import { duration, pct, secondsBetween } from '@/ui/time'
import StartJobDialog from './StartJobDialog.vue'
import { faceText, jobErrorText, jobKindText, jobTarget, readProgress, STARTABLE } from './jobText'

/**
 * Jobs (6.8): every piece of work from every face (web, CLI, scheduler) and every process. State and kind
 * are filtered by the server; "started by" within the shown page (the API has no such filter). Cursor
 * paging, the page size and the filters live in the query string. The list refreshes every 10 s while
 * the screen is open and the tab is visible (5.1).
 */
const JOBS_EVERY_MS = 10000

const STATES: JobState[] = ['queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled', 'interrupted']
const KINDS: JobKind[] = ['sync', 'fetch', 'refresh', 'export', 'backup', 'restore', 'clear', 'migrate', 'rebuild']
const FACES = ['api', 'cli', 'scheduler', 'library'] as const

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const rows = ref<Job[]>([])
const next = ref<string | null>(null)
const loading = ref(true)
const error = ref<unknown>(null)
const loadedOnce = ref(false)
const sort = ref<Sort | null>(null)
/** Cursors of the pages before this one, for "Previous". */
const back = ref<(string | null)[]>([])
const starting = ref<StartJobBody | null>(null)

const q = computed<{ state: JobState | ''; kind: JobKind | ''; face: string; size: number; cursor: string | null }>(() => ({
  state: typeof route.query.state === 'string' && STATES.includes(route.query.state as JobState) ? (route.query.state as JobState) : '',
  kind: typeof route.query.kind === 'string' && KINDS.includes(route.query.kind as JobKind) ? (route.query.kind as JobKind) : '',
  face: typeof route.query.face === 'string' && (FACES as readonly string[]).includes(route.query.face) ? route.query.face : '',
  size: [25, 50, 100].includes(Number(route.query.size)) ? Number(route.query.size) : 25,
  cursor: typeof route.query.cursor === 'string' ? route.query.cursor : null,
}))

function setQuery(patch: Record<string, string | number | null>, keepCursor = false) {
  const query: Record<string, string> = {}
  const merged = { ...q.value, ...(keepCursor ? {} : { cursor: null }), ...patch }
  for (const [k, v] of Object.entries(merged)) if (v !== '' && v != null && !(k === 'size' && v === 25)) query[k] = String(v)
  if (!keepCursor) back.value = []
  void router.replace({ query })
}

let controller: AbortController | null = null
async function load() {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  try {
    const page = await v1.jobs(
      { limit: q.value.size, cursor: q.value.cursor, state: q.value.state ? [q.value.state] : null, kind: q.value.kind ? [q.value.kind] : null },
      controller.signal,
    )
    rows.value = page.data
    next.value = page.page.next_cursor ?? null
    error.value = null
    loadedOnce.value = true
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') return
    error.value = e
  } finally {
    loading.value = false
  }
}

const shown = computed(() => (q.value.face ? rows.value.filter((j) => j.origin.face === q.value.face) : rows.value))

const columns = computed<Column<Job>[]>(() => [
  { key: 'kind', label: t('ui.jobs.col.kind'), sortable: true, card: 'title' },
  { key: 'target', label: t('ui.jobs.col.target'), card: 'meta', sortValue: (j) => jobTarget(j) },
  { key: 'state', label: t('ui.jobs.col.state'), sortable: true, card: 'badge' },
  { key: 'progress', label: t('ui.jobs.col.progress') },
  { key: 'origin', label: t('ui.jobs.col.origin'), sortable: true, sortValue: (j) => j.origin.face },
  { key: 'started', label: t('ui.jobs.col.started'), sortable: true, card: 'meta', sortValue: (j) => j.started_at ?? j.created_at ?? '' },
  { key: 'took', label: t('ui.jobs.col.took'), align: 'right', sortable: true, sortValue: (j) => took(j) ?? -1 },
  { key: 'id', label: t('ui.jobs.col.id'), optional: true, mono: true },
  { key: 'finished', label: t('ui.jobs.col.finished'), optional: true, sortValue: (j) => j.finished_at ?? '' },
  { key: 'error', label: t('ui.jobs.col.error'), optional: true, sortValue: (j) => j.error?.code ?? '' },
])

function took(j: Job): number | null {
  return j.finished_at ? secondsBetween(j.started_at ?? j.created_at, j.finished_at) : null
}

function progressText(j: Job): string {
  const p = readProgress(j.progress)
  if (j.state === 'running' || j.state === 'queued') return p.percent != null ? pct(p.percent) : '…'
  const failed = Number((j.result as Record<string, unknown> | null)?.failed_count) || p.failedCount
  if (j.error?.code) return jobErrorText(j) ?? j.error.code
  if (failed) return t('ui.jobs.failedCount', { n: failed })
  return ''
}

const startItems = computed<MenuItem[]>(() => STARTABLE.map((k) => ({ key: k, label: t(`ui.jobs.start.${k}.menu`) })))

const filterChips = computed(() => {
  const chips: { key: string; label: string }[] = []
  if (q.value.state) chips.push({ key: 'state', label: t(`ui.status.job.${q.value.state}`) })
  if (q.value.kind) chips.push({ key: 'kind', label: jobKindText(q.value.kind) })
  if (q.value.face) chips.push({ key: 'face', label: faceText(q.value.face) })
  return chips
})

function prev() {
  const stack = [...back.value]
  const cursor = stack.pop() ?? null
  back.value = stack
  setQuery({ cursor }, true)
}
function forward() {
  if (!next.value) return
  back.value = [...back.value, q.value.cursor]
  setQuery({ cursor: next.value }, true)
}

watch(
  () => [q.value.state, q.value.kind, q.value.size, q.value.cursor],
  () => void load(),
)

let stop: (() => void) | null = null
onMounted(() => {
  stop = poll(load, () => JOBS_EVERY_MS)
})
onUnmounted(() => {
  stop?.()
  controller?.abort()
})
</script>

<template>
  <div>
    <PageHeader :title="t('ui.nav.jobs')" :description="t('ui.jobs.description')">
      <template #actions>
        <UiMenu :label="t('ui.jobs.startMenu')" icon="jobs" align="right" button-class="u-btn u-btn-primary" :items="startItems" @select="(k) => (starting = { kind: k as 'sync' | 'fetch' | 'refresh', spec: {} })" />
      </template>
    </PageHeader>

    <FilterBar :active-count="filterChips.length" :chips="filterChips" @clear="setQuery({ state: '', kind: '', face: '' })" @remove="(k) => setQuery({ [k]: '' })">
      <label class="flex flex-col">
        <span class="u-label">{{ t('ui.jobs.col.state') }}</span>
        <select class="u-field" data-filter-focus :value="q.state" data-filter="state" @change="setQuery({ state: ($event.target as HTMLSelectElement).value })">
          <option value="">{{ t('ui.filter.all') }}</option>
          <option v-for="s in STATES" :key="s" :value="s">{{ t(`ui.status.job.${s}`) }}</option>
        </select>
      </label>
      <label class="flex flex-col">
        <span class="u-label">{{ t('ui.jobs.col.kind') }}</span>
        <select class="u-field" :value="q.kind" data-filter="kind" @change="setQuery({ kind: ($event.target as HTMLSelectElement).value })">
          <option value="">{{ t('ui.filter.all') }}</option>
          <option v-for="k in KINDS" :key="k" :value="k">{{ jobKindText(k) }}</option>
        </select>
      </label>
      <label class="flex flex-col">
        <span class="u-label">{{ t('ui.jobs.col.origin') }} <span class="font-normal u-muted">({{ t('ui.filter.inPage') }})</span></span>
        <select class="u-field" :value="q.face" data-filter="face" @change="setQuery({ face: ($event.target as HTMLSelectElement).value }, true)">
          <option value="">{{ t('ui.filter.all') }}</option>
          <option v-for="f in FACES" :key="f" :value="f">{{ faceText(f) }}</option>
        </select>
      </label>
    </FilterBar>

    <DataTable
      v-model:sort="sort"
      table-id="jobs"
      :caption="t('ui.nav.jobs')"
      :columns="columns"
      :rows="shown"
      :row-key="(j) => j.id"
      :row-to="(j) => `/jobs/${j.id}`"
      :loading="loading && !loadedOnce"
      :refreshing="loading && loadedOnce"
      :error="error"
      :has-prev="!!q.cursor"
      :has-next="!!next"
      :page-size="q.size"
      @update:page-size="(n) => setQuery({ size: n })"
      @prev="prev"
      @next="forward"
      @retry="load"
    >
      <template #cell-kind="{ row }">{{ jobKindText(row.kind) }}</template>
      <template #cell-target="{ row }">{{ jobTarget(row) }}</template>
      <template #cell-state="{ row }"><StatusBadge kind="job" :value="row.state" /></template>
      <template #cell-progress="{ row }"><span class="u-small u-num">{{ progressText(row) }}</span></template>
      <template #cell-origin="{ row }">
        {{ faceText(row.origin.face) }}<span v-if="row.origin.face !== 'api' && row.origin.host" class="u-muted"> · {{ row.origin.host }}</span>
      </template>
      <template #cell-started="{ row }"><TimeText :value="row.started_at ?? row.created_at" /></template>
      <template #cell-took="{ row }"><span class="u-num">{{ took(row) != null ? duration(took(row)) : '—' }}</span></template>
      <template #cell-id="{ row }">{{ row.id }}</template>
      <template #cell-finished="{ row }"><TimeText :value="row.finished_at" /></template>
      <template #cell-error="{ row }">{{ row.error?.code ?? '—' }}</template>
      <template #empty>
        <EmptyState v-if="filterChips.length" icon="filter" :title="t('ui.jobs.emptyFiltered')">
          <button type="button" class="u-btn" @click="setQuery({ state: '', kind: '', face: '' })">{{ t('ui.filter.clear') }}</button>
        </EmptyState>
        <EmptyState v-else icon="jobs" :title="t('ui.jobs.empty')" :text="t('ui.jobs.emptyText')" />
      </template>
    </DataTable>

    <StartJobDialog v-if="starting" :body="starting" @close="starting = null" @started="load" />
  </div>
</template>
