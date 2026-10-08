<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import DataTable, { type Column, type Sort } from '@/ui/DataTable.vue'
import FilterBar from '@/ui/FilterBar.vue'
import EmptyState from '@/ui/EmptyState.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import UiBadge from '@/ui/UiBadge.vue'
import UiIcon from '@/ui/UiIcon.vue'
import TimeText from '@/ui/TimeText.vue'
import { dayText } from '@/ui/time'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import { v1, type ListEventsQuery } from '@/api/v1/client'
import type { EventListItem } from '@/api/v1/schema'
import { queryIds, queryList, queryText, usePagedList } from '@/app/pagedList'
import { loadSports, sportName, sports } from '@/app/sports'
import { useStatusStore } from '@/app/statusStore'
import { toast } from '@/ui/toast'
import { startJob } from '@/screens/jobs/startJob'
import {
  eventTitle,
  fetchSelections,
  loadSeasons,
  loadTournaments,
  needsData,
  roundName,
  scoreText,
  seasonName,
  seasonNames,
  summaryText,
  tournamentName,
  tournamentNames,
} from './eventText'

/**
 * The list of stored events (6.5), also embedded in a follow's detail with its tournament (or, for a team
 * follow, the team: `participant`) fixed (6.4). It
 * does not update by itself: an event "in progress" is the state of its last read, not a live score (R2).
 * Filters, sort and the page are in the query string; the server filters and sorts. The Data column comes
 * from `include=slices_summary`. Selected events can be fetched: "Fetch missing data" sends only the
 * events with something missing, "Fetch again" all of them; both are `fetch` jobs by event id, one
 * selection per tournament. Every list (the Matches screen, a league's and a team's or a player's) starts with
 * the played matches, newest first, and switches to the upcoming ones, soonest first, or to every match (`when`,
 * FX-26 M15 for a team, FX-28 M21 for all: a followed league's full fixture list put next May's matches first);
 * a status filter replaces the switch's choice and "All statuses" is the "All" of the switch. With `fixedSport`
 * the sport filter is not offered (a team plays one sport).
 */
const props = defineProps<{ fixedTournament?: number | null; fixedParticipant?: number | null; fixedSport?: string | null; tableId?: string }>()
const { t, locale } = useI18n()
const route = useRoute()
const status = useStatusStore()

const CLASSES = ['not_started', 'live', 'completed', 'decided_without_play', 'void', 'unknown'] as const
type StatusClass = (typeof CLASSES)[number]
const SERVER_KEYS = ['sport', 'tournament', 'season', 'from', 'to', 'status', 'has', 'q', 'sort', 'when'] as const
/** Played (newest first, the default), upcoming (soonest first) or every match (FX-26 M15, FX-28 M21). */
const WHEN = ['played', 'upcoming', 'all'] as const
type When = (typeof WHEN)[number]
const WHEN_CLASSES: Record<When, StatusClass[]> = { played: ['live', 'completed', 'decided_without_play'], upcoming: ['not_started'], all: [] }

const f = computed(() => {
  const q = route.query
  const raw = queryList(q, 'status')
  const classes: StatusClass[] = raw.includes('any') ? [] : (raw.filter((x) => (CLASSES as readonly string[]).includes(x)) as StatusClass[])
  // adreste `when` yoksa oynananlar (FX-28): yalnızca `status=any` ile (eski bağlantılar) bütün maçlar
  const whenText = queryText(q, 'when')
  const when: When = (WHEN as readonly string[]).includes(whenText) ? (whenText as When) : raw.includes('any') ? 'all' : 'played'
  const sortText = queryText(q, 'sort')
  return {
    when,
    /** The status classes asked for: the filter's, else the switch's. */
    asked: classes.length ? classes : WHEN_CLASSES[when],
    sport: props.fixedSport ?? queryText(q, 'sport'),
    tournament: props.fixedTournament ?? queryIds(q, 'tournament')[0] ?? null,
    season: queryIds(q, 'season')[0] ?? null,
    from: queryText(q, 'from'),
    to: queryText(q, 'to'),
    classes,
    has: (['details', 'missing'].includes(queryText(q, 'has')) ? queryText(q, 'has') : '') as '' | 'details' | 'missing',
    team: queryText(q, 'q'),
    asc: sortText ? sortText === 'asc' : when === 'upcoming',
  }
})

const list = usePagedList<EventListItem>(
  ({ cursor, size, signal }) => {
    const query: ListEventsQuery = {
      sport: f.value.sport || null,
      tournament: f.value.tournament ? [f.value.tournament] : null,
      season: f.value.season ? [f.value.season] : null,
      from: f.value.from || null,
      to: f.value.to || null,
      status: f.value.asked.length ? f.value.asked : null,
      has: f.value.has || null,
      q: f.value.team || null,
      participant: props.fixedParticipant ? [props.fixedParticipant] : null,
      sort: f.value.asc ? 'start_utc' : '-start_utc',
      include: ['slices_summary'],
      limit: size,
      cursor,
    }
    return v1.events(query, signal)
  },
  { serverKeys: SERVER_KEYS },
)

const selection = ref<string[]>([])
watch(() => list.rows.value, () => (selection.value = selection.value.filter((id) => list.rows.value.some((r) => String(r.id) === id))))

const sort = computed<Sort>(() => ({ key: 'start', dir: f.value.asc ? 'asc' : 'desc' }))
function onSort(s: Sort | null) {
  list.setQuery({ sort: s?.dir === 'asc' ? 'asc' : f.value.when === 'upcoming' ? 'desc' : null })
}
function setWhen(w: When) {
  list.setQuery({ when: w === 'played' ? null : w, sort: null, status: null })
}

const columns = computed<Column<EventListItem>[]>(() => [
  { key: 'start', label: t('ui.events.col.start'), sortable: true, card: 'meta' },
  { key: 'sport', label: t('ui.events.col.sport'), optional: true, defaultVisible: true },
  ...(props.fixedTournament ? [] : [{ key: 'tournament', label: t('ui.events.col.tournament'), card: 'meta' as const }]),
  { key: 'round', label: t('ui.events.col.round'), optional: true },
  { key: 'match', label: t('ui.events.col.match'), card: 'title' },
  { key: 'status', label: t('ui.events.col.status'), card: 'badge' },
  { key: 'data', label: t('ui.events.col.data'), align: 'right' },
  { key: 'id', label: t('ui.events.col.id'), optional: true, mono: true },
  { key: 'season', label: t('ui.events.col.season'), optional: true },
  { key: 'custom', label: t('ui.events.col.custom'), optional: true, mono: true },
  { key: 'observed', label: t('ui.events.col.observed'), optional: true },
  { key: 'change', label: t('ui.events.col.change'), optional: true },
])

// ---- filters ----
const teamText = ref(f.value.team)
watch(() => f.value.team, (v) => (teamText.value = v))
const followedFirst = computed(() => [...tournamentNames.value.values()].sort((a, b) => Number(!!b.followed) - Number(!!a.followed) || String(a.name).localeCompare(String(b.name))))
const seasonsOfTournament = computed(() => [...seasonNames.value.values()].filter((s) => s.tournament_id === f.value.tournament))
watch(
  () => f.value.tournament,
  (id) => {
    if (id) void loadSeasons(id).catch(() => {})
  },
  { immediate: true },
)

function toggleClass(c: StatusClass) {
  // the played / upcoming switch counts as the starting choice (FX-26)
  const set = new Set(f.value.classes.length ? f.value.classes : f.value.asked)
  if (set.has(c)) set.delete(c)
  else set.add(c)
  list.setQuery({ status: set.size ? [...set] : null })
}

const chips = computed(() => {
  const out: { key: string; label: string }[] = []
  if (f.value.sport && !props.fixedSport) out.push({ key: 'sport', label: sportName(f.value.sport) })
  if (!props.fixedTournament && f.value.tournament) out.push({ key: 'tournament', label: tournamentName(f.value.tournament) })
  if (f.value.season) out.push({ key: 'season', label: seasonName(f.value.season) })
  if (f.value.from) out.push({ key: 'from', label: t('ui.events.from', { date: dayText(f.value.from) }) })
  if (f.value.to) out.push({ key: 'to', label: t('ui.events.to', { date: dayText(f.value.to) }) })
  if (f.value.classes.length) out.push({ key: 'status', label: f.value.classes.map((c) => t(`ui.status.event.${c}`)).join(', ') })
  if (f.value.has) out.push({ key: 'has', label: t(`ui.events.has.${f.value.has}`) })
  if (f.value.team) out.push({ key: 'q', label: `“${f.value.team}”` })
  return out
})
function clearFilters() {
  list.setQuery({ sport: null, tournament: null, season: null, from: null, to: null, status: null, has: null, q: null })
}
function removeFilter(key: string) {
  list.setQuery(key === 'tournament' ? { tournament: null, season: null } : { [key]: null })
}

// ---- bulk: fetch missing data, fetch again ----
const chosen = computed(() => list.rows.value.filter((r) => selection.value.includes(String(r.id))))
const bulk = ref<'missing' | 'again' | null>(null)
const bulkBusy = ref(false)
const bulkError = ref<unknown>(null)
const bulkEvents = computed(() => (bulk.value === 'missing' ? chosen.value.filter(needsData) : chosen.value))
const bulkPlan = computed(() => fetchSelections(bulkEvents.value))

function openBulk(kind: 'missing' | 'again') {
  bulkError.value = null
  if (kind === 'missing' && !chosen.value.some(needsData)) {
    toast({ kind: 'info', text: t('ui.events.bulk.nothingMissing') })
    return
  }
  bulk.value = kind
}

async function runBulk() {
  if (!bulkPlan.value.selections.length) return
  bulkBusy.value = true
  bulkError.value = null
  try {
    await startJob({ kind: 'fetch', spec: { selections: bulkPlan.value.selections } })
    bulk.value = null
    selection.value = []
  } catch (e) {
    bulkError.value = e
  } finally {
    bulkBusy.value = false
  }
}

const emptyFiltered = computed(() => chips.value.length > 0)

onMounted(() => {
  void loadSports().catch(() => {})
  void loadTournaments()
  void list.load()
})

defineExpose({ reload: list.load })
</script>

<template>
  <div>
    <div class="u-seg mb-3" role="group" :aria-label="t('ui.events.when.label')" data-testid="events-when">
      <button v-for="w in WHEN" :key="w" type="button" :aria-pressed="!f.classes.length && f.when === w" :data-when="w" @click="setWhen(w)">{{ t(`ui.events.when.${w}`) }}</button>
    </div>
    <FilterBar :active-count="chips.length" :chips="chips" @clear="clearFilters" @remove="removeFilter">
      <label v-if="!fixedSport" class="flex flex-col">
        <span class="u-label">{{ t('ui.events.col.sport') }}</span>
        <select class="u-field" :value="f.sport" data-filter="sport" data-filter-focus @change="list.setQuery({ sport: ($event.target as HTMLSelectElement).value })">
          <option value="">{{ t('ui.filter.all') }}</option>
          <option v-for="s in sports" :key="s.slug" :value="s.slug">{{ sportName(s.slug) }}</option>
        </select>
      </label>
      <label v-if="!fixedTournament" class="flex flex-col">
        <span class="u-label">{{ t('ui.events.col.tournament') }}</span>
        <select class="u-field" :value="f.tournament ?? ''" data-filter="tournament" @change="list.setQuery({ tournament: ($event.target as HTMLSelectElement).value, season: null })">
          <option value="">{{ t('ui.filter.all') }}</option>
          <option v-for="tour in followedFirst" :key="tour.id" :value="tour.id">{{ tour.name ?? `#${tour.id}` }}{{ tour.followed ? ` · ${t('ui.events.followed')}` : '' }}</option>
        </select>
      </label>
      <label v-if="f.tournament" class="flex flex-col">
        <span class="u-label">{{ t('ui.events.col.season') }}</span>
        <select class="u-field" :value="f.season ?? ''" data-filter="season" @change="list.setQuery({ season: ($event.target as HTMLSelectElement).value })">
          <option value="">{{ t('ui.filter.all') }}</option>
          <option v-for="s in seasonsOfTournament" :key="s.id" :value="s.id">{{ s.year ?? s.name ?? s.id }}</option>
        </select>
      </label>
      <label class="flex flex-col">
        <span class="u-label">{{ t('ui.events.fromLabel') }}</span>
        <input type="date" class="u-field" :lang="locale" :value="f.from" data-filter="from" @change="list.setQuery({ from: ($event.target as HTMLInputElement).value })" />
        <span v-if="f.from" class="u-small u-muted" data-testid="date-hint-from">{{ dayText(f.from) }}</span>
      </label>
      <label class="flex flex-col">
        <span class="u-label">{{ t('ui.events.toLabel') }}</span>
        <input type="date" class="u-field" :lang="locale" :value="f.to" data-filter="to" @change="list.setQuery({ to: ($event.target as HTMLInputElement).value })" />
        <span v-if="f.to" class="u-small u-muted" data-testid="date-hint-to">{{ dayText(f.to) }}</span>
      </label>
      <label class="flex flex-col">
        <span class="u-label">{{ t('ui.events.col.data') }}</span>
        <select class="u-field" :value="f.has" data-filter="has" @change="list.setQuery({ has: ($event.target as HTMLSelectElement).value })">
          <option value="">{{ t('ui.filter.all') }}</option>
          <option value="details">{{ t('ui.events.has.details') }}</option>
          <option value="missing">{{ t('ui.events.has.missing') }}</option>
        </select>
      </label>
      <form class="flex flex-col" @submit.prevent="list.setQuery({ q: teamText.trim() })">
        <label class="u-label" for="events-team">{{ t('ui.events.team') }}</label>
        <input id="events-team" v-model="teamText" type="search" class="u-field" autocomplete="off" :placeholder="t('ui.events.teamPlaceholder')" data-filter="q" @change="list.setQuery({ q: teamText.trim() })" />
      </form>
      <div class="flex flex-col basis-full">
        <span id="events-status" class="u-label">{{ t('ui.events.col.status') }}</span>
        <div class="flex flex-wrap gap-2" role="group" aria-labelledby="events-status">
          <button type="button" class="u-chip u-toggle-chip" :aria-pressed="!f.asked.length" data-class="all" @click="setWhen('all')">{{ t('ui.events.anyStatus') }}</button>
          <button v-for="c in CLASSES" :key="c" type="button" class="u-chip u-toggle-chip" :aria-pressed="f.asked.includes(c)" :data-class="c" @click="toggleClass(c)">
            {{ t(`ui.status.event.${c}`) }}
          </button>
        </div>
      </div>
    </FilterBar>

    <DataTable
      v-model:selection="selection"
      :table-id="tableId ?? 'events'"
      :caption="t('ui.nav.events')"
      :columns="columns"
      :rows="list.rows.value"
      :row-key="(e) => String(e.id)"
      :row-to="(e) => `/events/${e.id}`"
      :row-label="eventTitle"
      :loading="list.loading.value && !list.loadedOnce.value"
      :refreshing="list.loading.value && list.loadedOnce.value"
      :error="list.error.value"
      :sort="sort"
      sort-mode="server"
      :has-prev="list.hasPrev.value"
      :has-next="!!list.next.value"
      :page-size="list.size.value"
      @update:sort="onSort"
      @update:page-size="(n) => list.setQuery({ size: n })"
      @prev="list.prev"
      @next="list.forward"
      @retry="list.load"
    >
      <template #bulk>
        <button type="button" class="u-btn u-btn-sm" data-testid="bulk-missing" @click="openBulk('missing')"><UiIcon name="exports" :size="14" />{{ t('ui.events.bulk.missing') }}</button>
        <button type="button" class="u-btn u-btn-sm" data-testid="bulk-again" @click="openBulk('again')"><UiIcon name="refresh" :size="14" />{{ t('ui.events.bulk.again') }}</button>
      </template>
      <template #cell-start="{ row }"><TimeText :value="row.start_utc" /></template>
      <template #cell-sport="{ row }">{{ sportName(row.sport) }}</template>
      <template #cell-tournament="{ row }">{{ tournamentName(row.tournament_id) }}</template>
      <template #cell-round="{ row }">{{ roundName(row.round) ?? '—' }}</template>
      <template #cell-match="{ row }">
        <span class="inline-flex flex-wrap items-baseline gap-x-2">
          <span>{{ row.participants.home?.name ?? '—' }}</span>
          <span class="u-num font-semibold">{{ scoreText(row) }}</span>
          <span>{{ row.participants.away?.name ?? '—' }}</span>
        </span>
      </template>
      <template #cell-status="{ row }">
        <span class="inline-flex flex-wrap items-center gap-1">
          <StatusBadge kind="event" :value="row.status.class" />
          <UiBadge v-if="row.quality.settlement === 'provisional'" tone="info" icon="clock" :title="t('ui.eventDetail.provisionalWhyPlain')">{{ t('ui.status.settlement.provisional') }}</UiBadge>
          <UiBadge v-if="row.quality.stale" tone="warn" icon="alert">{{ t('ui.status.quality.stale') }}</UiBadge>
          <UiBadge v-if="row.quality.status_regressed" tone="warn" icon="alert">{{ t('ui.status.quality.regressed') }}</UiBadge>
        </span>
      </template>
      <template #cell-data="{ row }">
        <span class="u-num inline-flex items-center gap-1" :title="row.slices_summary ? t('ui.events.dataTitle', { ok: row.slices_summary.ok, empty: row.slices_summary.empty, error: row.slices_summary.error, selected: row.slices_summary.selected }) : undefined">
          <template v-if="row.quality.source === 'listing'"><span class="u-muted">{{ t('ui.events.listingOnly') }}</span></template>
          <template v-else-if="row.status.class === 'not_started'"><span class="u-muted" data-testid="not-played">{{ t('ui.events.notPlayed') }}</span></template>
          <template v-else>{{ summaryText(row.slices_summary) ?? '—' }}</template>
          <span v-if="row.slices_summary?.error" role="img" style="color: var(--warn-fg)" :aria-label="t('ui.events.failedSlices', { n: row.slices_summary.error })"><UiIcon name="alert" :size="14" /></span>
        </span>
      </template>
      <template #cell-id="{ row }">{{ row.id }}</template>
      <template #cell-season="{ row }">{{ seasonName(row.season_id) }}</template>
      <template #cell-custom="{ row }">{{ row.custom_id ?? '—' }}</template>
      <template #cell-observed="{ row }"><TimeText :value="row.quality.observed_at_utc" /></template>
      <template #cell-change="{ row }"><TimeText :value="row.quality.change_ts ? row.quality.change_ts * 1000 : null" /></template>
      <template #empty>
        <EmptyState v-if="emptyFiltered" icon="filter" :title="t('ui.events.emptyFiltered')">
          <button type="button" class="u-btn" @click="clearFilters">{{ t('ui.filter.clear') }}</button>
        </EmptyState>
        <EmptyState v-else icon="events" :title="t('ui.events.empty')" :text="t('ui.events.emptyText')" />
      </template>
    </DataTable>

    <ConfirmDialog
      v-if="bulk"
      :title="bulk === 'missing' ? t('ui.events.bulk.missingTitle', { n: bulkEvents.length }) : t('ui.events.bulk.againTitle', { n: bulkEvents.length })"
      :confirm-label="t('ui.jobs.start.confirm')"
      :busy="bulkBusy"
      :error="bulkError"
      :active-job-id="status.activeJob?.id"
      @confirm="runBulk"
      @close="bulk = null"
    >
      <p class="m-0">{{ bulk === 'missing' ? t('ui.events.bulk.missingText') : t('ui.events.bulk.againText') }}</p>
      <p v-if="bulkPlan.skipped" class="m-0 u-small" style="color: var(--warn-fg)">{{ t('ui.events.bulk.skipped', { n: bulkPlan.skipped }) }}</p>
      <p class="m-0 flex items-center gap-2 u-small" style="color: var(--warn-fg)"><UiIcon name="external" :size="14" />{{ t('ui.jobs.start.sendsRequests') }}</p>
    </ConfirmDialog>
  </div>
</template>

<style>
.u-toggle-chip {
  background: var(--surface);
}
.u-toggle-chip[aria-pressed='true'] {
  background: var(--accent-soft);
  border-color: var(--accent);
  font-weight: 600;
}
</style>
