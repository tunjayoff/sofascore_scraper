<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import DataTable, { type Column } from '@/ui/DataTable.vue'
import FilterBar from '@/ui/FilterBar.vue'
import EmptyState from '@/ui/EmptyState.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import UiBadge from '@/ui/UiBadge.vue'
import UiIcon from '@/ui/UiIcon.vue'
import TimeText from '@/ui/TimeText.vue'
import { v1 } from '@/api/v1/client'
import type { FollowRecord, Job } from '@/api/v1/schema'
import { queryText } from '@/app/pagedList'
import { loadSports, sportName, sports } from '@/app/sports'
import { useStatusStore } from '@/app/statusStore'
import { onJobEnded } from '@/app/jobWatch'
import { num, pct } from '@/ui/time'
import StartJobDialog from '@/screens/jobs/StartJobDialog.vue'
import { noteFollowNames } from '@/screens/jobs/jobText'
import FollowActions from './FollowActions.vue'
import { followCoverage, leagueCoverage, serverCoverage, type FollowCoverage } from './followCoverage'
import { FOLLOW_KINDS, dataText, followPath, hasOdds, lastSyncOf, seasonsText } from './followText'

/**
 * Leagues & follows (6.2): what the platform downloads, one row per follow with its seasons, data
 * selection, matches with details (from the data summary of `/status`) and last download. Name, kind,
 * origin and "enabled only" are filtered by the server; the sport within the list (the route has no sport
 * filter). A follow from the config file is locked: its actions say where to change it. When a download
 * ends, the last downloads and the counts are read again (FX-14a).
 */
const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const status = useStatusStore()

const rows = ref<FollowRecord[]>([])
const loading = ref(true)
const loaded = ref(false)
const error = ref<unknown>(null)
const syncs = ref<Job[]>([])
const syncingAll = ref(false)

const f = computed(() => ({
  q: queryText(route.query, 'q'),
  kind: queryText(route.query, 'kind'),
  origin: queryText(route.query, 'origin'),
  enabled: queryText(route.query, 'enabled') === '1',
  sport: queryText(route.query, 'sport'),
}))
function setQuery(patch: Record<string, string | null>) {
  const query = { ...route.query }
  for (const [k, v] of Object.entries(patch)) {
    if (v) query[k] = v
    else delete query[k]
  }
  void router.replace({ query })
}

let controller: AbortController | null = null
async function load() {
  controller?.abort()
  const mine = (controller = new AbortController())
  loading.value = true
  try {
    const page = await v1.follows(
      {
        q: f.value.q || null,
        kind: (FOLLOW_KINDS as readonly string[]).includes(f.value.kind) ? (f.value.kind as FollowRecord['kind']) : null,
        origin: ['legacy', 'config', 'api'].includes(f.value.origin) ? (f.value.origin as FollowRecord['origin']) : null,
        enabled: f.value.enabled ? true : null,
      },
      mine.signal,
    )
    rows.value = page.data
    noteFollowNames(page.data)
    loadCounts()
    error.value = null
    loaded.value = true
  } catch (e) {
    if ((e as Error)?.name !== 'AbortError') error.value = e
  } finally {
    if (controller === mine) loading.value = false
  }
}

const shown = computed(() => (f.value.sport ? rows.value.filter((x) => x.sport === f.value.sport) : rows.value))
/** Follows with the same kind, sport and name (a men's and a women's team, FX-24 F26): their number is shown. */
const twins = computed(() => {
  const key = (x: FollowRecord) => `${x.kind}|${x.sport ?? ''}|${x.name.trim().toLowerCase()}`
  const count = new Map<string, number>()
  for (const x of rows.value) count.set(key(x), (count.get(key(x)) ?? 0) + 1)
  return new Set(rows.value.filter((x) => (count.get(key(x)) ?? 0) > 1).map((x) => x.id))
})
const coverage = computed(() => new Map((status.status?.summary?.tournaments ?? []).filter((x) => x.tournament_id != null).map((x) => [x.tournament_id as number, x])))
/** Every follow counted by the server (B2, G40: `summary.follows`); an older server has none. */
const perFollow = computed(() => (status.status?.summary?.follows ? new Map(status.status.summary.follows.map((x) => [x.follow_id, x])) : null))
/** Teams and single matches, counted from their stored matches on an older server (FX-24 F23). */
const counted = ref<Map<string, FollowCoverage>>(new Map())
let countCtl: AbortController | null = null
function loadCounts() {
  countCtl?.abort()
  const ctl = (countCtl = new AbortController())
  if (perFollow.value) return
  for (const row of rows.value.filter((x) => x.kind === 'team' || x.kind === 'event'))
    followCoverage(row, ctl.signal)
      .then((c) => {
        if (c && ctl === countCtl) counted.value = new Map(counted.value).set(row.id, c)
      })
      .catch(() => {})
}
/**
 * "Matches with details" of a row: from the server's count of the follow (B2), else a league's from the data
 * summary and a team's or a match's counted here.
 */
function coverageOf(row: FollowRecord): { coverage: number; details: number; matches: number } | null {
  const server = perFollow.value?.get(row.id)
  if (server) return serverCoverage(server)
  if (row.kind === 'tournament') {
    const found = coverage.value.get(row.entity_id)
    return found ? leagueCoverage(found) : null
  }
  return counted.value.get(row.id) ?? null
}

const columns = computed<Column<FollowRecord>[]>(() => [
  { key: 'name', label: t('ui.follows.col.name'), card: 'title', sortable: true },
  { key: 'sport', label: t('ui.follows.col.sport'), card: 'meta', sortable: true, sortValue: (x) => sportName(x.sport) },
  { key: 'kind', label: t('ui.follows.col.kind'), optional: true },
  { key: 'seasons', label: t('ui.follows.col.seasons') },
  { key: 'data', label: t('ui.follows.col.data') },
  { key: 'coverage', label: t('ui.follows.col.coverage'), sortable: true, sortValue: (x) => coverageOf(x)?.coverage ?? -1 },
  { key: 'lastSync', label: t('ui.follows.col.lastSync'), card: 'meta', sortable: true, sortValue: (x) => lastSyncOf(x, syncs.value)?.finished_at ?? '' },
  { key: 'origin', label: t('ui.follows.col.origin'), card: 'badge' },
  { key: 'live', label: t('ui.follows.col.live'), optional: true },
  { key: 'id', label: t('ui.follows.col.id'), optional: true, mono: true },
])

const chips = computed(() => {
  const out: { key: string; label: string }[] = []
  if (f.value.q) out.push({ key: 'q', label: `“${f.value.q}”` })
  if (f.value.sport) out.push({ key: 'sport', label: sportName(f.value.sport) })
  if (f.value.kind) out.push({ key: 'kind', label: t(`ui.follows.kind.${f.value.kind}`) })
  if (f.value.origin) out.push({ key: 'origin', label: t(`ui.status.origin.${f.value.origin}`) })
  if (f.value.enabled) out.push({ key: 'enabled', label: t('ui.follows.enabledOnly') })
  return out
})
const search = ref(f.value.q)
watch(() => f.value.q, (q) => (search.value = q))
watch(() => [f.value.q, f.value.kind, f.value.origin, f.value.enabled], () => void load())

function loadSyncs() {
  v1.jobs({ kind: ['sync'], limit: 50 })
    .then((r) => (syncs.value = r.data))
    .catch(() => {})
}

const stopListening = onJobEnded((job) => {
  if (job.kind !== 'sync' && job.kind !== 'fetch') return
  loadSyncs()
  loadCounts()
  void status.refresh().catch(() => {})
})
onUnmounted(stopListening)

onMounted(() => {
  void loadSports().catch(() => {})
  void load()
  loadSyncs()
})
</script>

<template>
  <div>
    <PageHeader :title="t('ui.nav.follows')" :description="t('ui.follows.description')" help="follow">
      <template #actions>
        <button type="button" class="u-btn" @click="syncingAll = true"><UiIcon name="jobs" :size="16" />{{ t('ui.follows.syncAll') }}</button>
        <RouterLink to="/follows/new" class="u-btn u-btn-primary" data-testid="follows-add"><UiIcon name="plus" :size="16" />{{ t('ui.follows.add') }}</RouterLink>
      </template>
    </PageHeader>

    <FilterBar :active-count="chips.length" :chips="chips" @clear="setQuery({ q: null, sport: null, kind: null, origin: null, enabled: null })" @remove="(k) => setQuery({ [k]: null })">
      <form class="flex flex-col" @submit.prevent="setQuery({ q: search.trim() || null })">
        <label class="u-label" for="follows-q">{{ t('ui.follows.search') }}</label>
        <input id="follows-q" v-model="search" type="search" class="u-field" autocomplete="off" data-filter="q" data-filter-focus @change="setQuery({ q: search.trim() || null })" />
      </form>
      <label class="flex flex-col">
        <span class="u-label">{{ t('ui.follows.col.sport') }}</span>
        <select class="u-field" :value="f.sport" data-filter="sport" @change="setQuery({ sport: ($event.target as HTMLSelectElement).value || null })">
          <option value="">{{ t('ui.filter.all') }}</option>
          <option v-for="s in sports" :key="s.slug" :value="s.slug">{{ sportName(s.slug) }}</option>
        </select>
      </label>
      <label class="flex flex-col">
        <span class="u-label">{{ t('ui.follows.col.kind') }}</span>
        <select class="u-field" :value="f.kind" data-filter="kind" @change="setQuery({ kind: ($event.target as HTMLSelectElement).value || null })">
          <option value="">{{ t('ui.filter.all') }}</option>
          <option v-for="k in FOLLOW_KINDS" :key="k" :value="k">{{ t(`ui.follows.kind.${k}`) }}</option>
        </select>
      </label>
      <label class="flex flex-col">
        <span class="u-label">{{ t('ui.follows.col.origin') }}</span>
        <select class="u-field" :value="f.origin" data-filter="origin" @change="setQuery({ origin: ($event.target as HTMLSelectElement).value || null })">
          <option value="">{{ t('ui.filter.all') }}</option>
          <option v-for="o in ['api', 'legacy', 'config']" :key="o" :value="o">{{ t(`ui.status.origin.${o}`) }}</option>
        </select>
      </label>
      <label class="flex items-center gap-2 self-end min-h-[40px]">
        <input type="checkbox" class="u-check" :checked="f.enabled" data-filter="enabled" @change="setQuery({ enabled: ($event.target as HTMLInputElement).checked ? '1' : null })" />
        {{ t('ui.follows.enabledOnly') }}
      </label>
    </FilterBar>

    <DataTable
      table-id="follows"
      :caption="t('ui.nav.follows')"
      :columns="columns"
      :rows="shown"
      :row-key="(x) => x.id"
      :row-to="followPath"
      :loading="loading && !loaded"
      :refreshing="loading && loaded"
      :error="error"
      :paged="false"
      @retry="load"
    >
      <template #cell-name="{ row }">
        <span class="inline-flex items-center gap-2"
          >{{ row.name
          }}<span v-if="twins.has(row.id)" class="u-small u-muted u-mono" :title="t('ui.follows.sameName')" data-testid="follow-number">{{ t('ui.suggest.number', { id: row.entity_id }) }}</span
          ><UiBadge v-if="!row.enabled" tone="neutral" icon="pause">{{ t('ui.follows.disabled') }}</UiBadge></span
        >
      </template>
      <template #cell-sport="{ row }">{{ sportName(row.sport) }}</template>
      <template #cell-kind="{ row }">{{ t(`ui.follows.kind.${row.kind}`) }}</template>
      <template #cell-seasons="{ row }">{{ row.kind === 'event' ? '—' : seasonsText(row.seasons, row.kind) }}</template>
      <template #cell-data="{ row }">
        <span class="inline-flex items-center gap-2">{{ dataText(row.slices) }}<UiBadge v-if="hasOdds(row.slices)" tone="info">{{ t('ui.follows.data.odds') }}</UiBadge></span>
      </template>
      <template #cell-coverage="{ row }">
        <span
          v-if="coverageOf(row)"
          class="inline-flex items-center gap-2 u-num"
          :title="t('ui.followDetail.coverageText', { details: num(coverageOf(row)!.details), matches: num(coverageOf(row)!.matches) })"
          data-testid="follow-coverage"
        >
          <span class="u-minibar" aria-hidden="true"><span :style="{ width: `${coverageOf(row)!.coverage}%` }"></span></span>
          {{ pct(coverageOf(row)!.coverage) }}
        </span>
        <span v-else-if="row.kind === 'player'" class="u-muted" :title="t(perFollow ? 'ui.follows.coveragePlayerPending' : 'ui.follows.coveragePlayer')" data-testid="follow-coverage-player">—</span>
        <span v-else class="u-muted">—</span>
      </template>
      <template #cell-lastSync="{ row }">
        <TimeText v-if="lastSyncOf(row, syncs)" :value="lastSyncOf(row, syncs)!.finished_at" relative />
        <span v-else class="u-muted">—</span>
      </template>
      <template #cell-origin="{ row }"><StatusBadge kind="origin" :value="row.origin" /></template>
      <template #cell-live="{ row }">{{ row.live ? t('ui.follows.liveYes') : '—' }}</template>
      <template #cell-id="{ row }">{{ row.id }}</template>
      <template #row-actions="{ row }"><FollowActions :follow="row" compact @changed="load" @removed="load" /></template>
      <template #empty>
        <EmptyState v-if="chips.length" icon="filter" :title="t('ui.follows.emptyFiltered')">
          <button type="button" class="u-btn" @click="setQuery({ q: null, sport: null, kind: null, origin: null, enabled: null })">{{ t('ui.filter.clear') }}</button>
        </EmptyState>
        <EmptyState v-else icon="follows" :title="t('ui.follows.empty')" :text="t('ui.follows.emptyText')">
          <RouterLink to="/follows/new" class="u-btn u-btn-primary"><UiIcon name="plus" :size="16" />{{ t('ui.follows.add') }}</RouterLink>
        </EmptyState>
      </template>
    </DataTable>
    <p v-if="loaded && rows.length" class="m-0 mt-3 u-small u-muted">{{ t('ui.follows.count', { n: shown.length }) }}</p>

    <StartJobDialog v-if="syncingAll" :body="{ kind: 'sync', spec: {} }" @close="syncingAll = false" />
  </div>
</template>
