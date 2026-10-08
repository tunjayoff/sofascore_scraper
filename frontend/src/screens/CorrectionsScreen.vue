<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import DataTable, { type Column } from '@/ui/DataTable.vue'
import FilterBar from '@/ui/FilterBar.vue'
import EmptyState from '@/ui/EmptyState.vue'
import TimeText from '@/ui/TimeText.vue'
import UiIcon from '@/ui/UiIcon.vue'
import { v1 } from '@/api/v1/client'
import type { Change, Event } from '@/api/v1/schema'
import { queryIds, queryText, usePagedList } from '@/app/pagedList'
import { loadSports, sportName, sports } from '@/app/sports'
import { dayText, duration } from '@/ui/time'
import ChangeFields from './events/ChangeFields.vue'
import { eventTitle, loadTournaments, tournamentName, tournamentNames } from './events/eventText'

/**
 * Corrections (6.7, decision 19): the change log, newest first: results SofaScore corrected after they were
 * stored. Tournament and dates are filtered by the server; sport and "status regressed only" within the
 * page (the route has no such filters). The names of the matches are read for the rows shown.
 */
const { t, locale } = useI18n()
const route = useRoute()

const f = computed(() => ({
  tournament: queryIds(route.query, 'tournament')[0] ?? null,
  from: queryText(route.query, 'from'),
  to: queryText(route.query, 'to'),
  sport: queryText(route.query, 'sport'),
  regressed: queryText(route.query, 'regressed') === '1',
}))

const list = usePagedList<Change>(
  ({ cursor, size, signal }) =>
    v1.changes({ order: 'desc', tournament: f.value.tournament ? [f.value.tournament] : null, from: f.value.from || null, to: f.value.to || null, limit: size, cursor }, signal),
  { serverKeys: ['tournament', 'from', 'to'] },
)
const shown = computed(() => list.rows.value.filter((c) => (!f.value.sport || c.sport === f.value.sport) && (!f.value.regressed || c.status_regressed)))

// The names of the matches on this page, read once each
const names = ref<Map<number, Pick<Event, 'participants'>>>(new Map())
watch(
  () => list.rows.value,
  (rows) => {
    const missing = [...new Set(rows.map((c) => c.event_id))].filter((id) => !names.value.has(id))
    for (const id of missing)
      v1.event(id)
        .then((e) => (names.value = new Map(names.value).set(id, e)))
        .catch(() => {})
  },
)

const columns = computed<Column<Change>[]>(() => [
  { key: 'recorded', label: t('ui.corrections.col.recorded'), card: 'meta', sortable: true, sortValue: (c) => c.recorded_at_utc },
  { key: 'match', label: t('ui.corrections.col.match'), card: 'title' },
  { key: 'sport', label: t('ui.corrections.col.sport'), optional: true, defaultVisible: true },
  { key: 'tournament', label: t('ui.corrections.col.tournament'), card: 'meta' },
  { key: 'fields', label: t('ui.corrections.col.fields') },
  { key: 'after', label: t('ui.corrections.col.after'), align: 'right', sortable: true, sortValue: (c) => c.seconds_after_start ?? -1 },
  { key: 'event', label: t('ui.corrections.col.event'), optional: true, mono: true },
])

const chips = computed(() => {
  const out: { key: string; label: string }[] = []
  if (f.value.sport) out.push({ key: 'sport', label: sportName(f.value.sport) })
  if (f.value.tournament) out.push({ key: 'tournament', label: tournamentName(f.value.tournament) })
  if (f.value.from) out.push({ key: 'from', label: t('ui.events.from', { date: dayText(f.value.from) }) })
  if (f.value.to) out.push({ key: 'to', label: t('ui.events.to', { date: dayText(f.value.to) }) })
  if (f.value.regressed) out.push({ key: 'regressed', label: t('ui.corrections.regressedOnly') })
  return out
})
const filteredInPage = computed(() => !!(f.value.sport || f.value.regressed))

onMounted(() => {
  void loadSports().catch(() => {})
  void loadTournaments()
  void list.load()
})
</script>

<template>
  <div>
    <PageHeader :title="t('ui.nav.corrections')" :description="t('ui.corrections.description')" help="scoreChanges">
      <template #actions>
        <button type="button" class="u-btn" @click="list.load()"><UiIcon name="refresh" :size="16" />{{ t('ui.common.refresh') }}</button>
      </template>
    </PageHeader>

    <FilterBar :active-count="chips.length" :chips="chips" @clear="list.setQuery({ sport: null, tournament: null, from: null, to: null, regressed: null })" @remove="(k) => list.setQuery({ [k]: null })">
      <label class="flex flex-col">
        <span class="u-label">{{ t('ui.corrections.col.sport') }} <span class="font-normal u-muted">({{ t('ui.filter.inPage') }})</span></span>
        <select class="u-field" :value="f.sport" data-filter="sport" data-filter-focus @change="list.setQuery({ sport: ($event.target as HTMLSelectElement).value }, true)">
          <option value="">{{ t('ui.filter.all') }}</option>
          <option v-for="s in sports" :key="s.slug" :value="s.slug">{{ sportName(s.slug) }}</option>
        </select>
      </label>
      <label class="flex flex-col">
        <span class="u-label">{{ t('ui.corrections.col.tournament') }}</span>
        <select class="u-field" :value="f.tournament ?? ''" data-filter="tournament" @change="list.setQuery({ tournament: ($event.target as HTMLSelectElement).value })">
          <option value="">{{ t('ui.filter.all') }}</option>
          <option v-for="tour in tournamentNames.values()" :key="tour.id" :value="tour.id">{{ tour.name ?? `#${tour.id}` }}</option>
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
      <label class="flex items-center gap-2 self-end min-h-[40px]">
        <input type="checkbox" class="u-check" :checked="f.regressed" data-filter="regressed" @change="list.setQuery({ regressed: ($event.target as HTMLInputElement).checked ? '1' : null }, true)" />
        {{ t('ui.corrections.regressedOnly') }} <span class="u-small u-muted">({{ t('ui.filter.inPage') }})</span>
      </label>
    </FilterBar>

    <DataTable
      table-id="corrections"
      :caption="t('ui.nav.corrections')"
      :columns="columns"
      :rows="shown"
      :row-key="(c) => String(c.seq)"
      :row-to="(c) => ({ path: `/events/${c.event_id}`, query: { tab: 'corrections' } })"
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
      <template #cell-recorded="{ row }"><TimeText :value="row.recorded_at_utc" /></template>
      <template #cell-match="{ row }">{{ names.get(row.event_id) ? eventTitle(names.get(row.event_id)!) : `#${row.event_id}` }}</template>
      <template #cell-sport="{ row }">{{ sportName(row.sport) }}</template>
      <template #cell-tournament="{ row }">{{ tournamentName(row.tournament_id) }}</template>
      <template #cell-fields="{ row }"><ChangeFields :change="row" compact /></template>
      <template #cell-after="{ row }"><span class="u-num">{{ row.seconds_after_start != null ? duration(row.seconds_after_start) : '—' }}</span></template>
      <template #cell-event="{ row }">{{ row.event_id }}</template>
      <template #empty>
        <EmptyState v-if="chips.length" icon="filter" :title="filteredInPage ? t('ui.corrections.emptyInPage') : t('ui.corrections.emptyFiltered')" />
        <EmptyState v-else icon="corrections" :title="t('ui.corrections.empty')" :text="t('ui.corrections.emptyText')" />
      </template>
    </DataTable>
  </div>
</template>
