<script lang="ts">
import type { RouteLocationRaw } from 'vue-router'

/** One column of a DataTable. Screens define them; the user shows or hides the optional ones. */
export type Column<T> = {
  key: string
  label: string
  sortable?: boolean
  /** Hidden unless the user turns it on in "Columns" (or `defaultVisible`). */
  optional?: boolean
  defaultVisible?: boolean
  align?: 'left' | 'right'
  mono?: boolean
  /** On phone each row is a card: `title` is its first line, `meta` the second, `badge` sits at the right. */
  card?: 'title' | 'meta' | 'badge'
  /** The value the page sort uses, when it is not `row[key]`. */
  sortValue?: (row: T) => string | number | null | undefined
}

export type Sort = { key: string; dir: 'asc' | 'desc' }

export const PAGE_SIZES = [25, 50, 100] as const
</script>

<script setup lang="ts" generic="T">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import UiMenu, { type MenuItem } from '@/ui/UiMenu.vue'
import ErrorState from '@/ui/ErrorState.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import { isTyping } from '@/ui/focus'
import { readColumns, writeColumns } from '@/ui/prefs'
import { PHONE, useMedia } from '@/ui/media'

/**
 * The one list component (05-web-ui.md 4.8): real <table> with `scope` headers and `aria-sort`; sorting by
 * the server (`sortMode="server"`, the screen reloads) or within the shown page (the header says so);
 * optional columns stored per table in this browser; Previous / Next by cursor with the page size and no
 * page count (decision 14); the whole row opens the detail (`rowTo`), j / k move between rows and Enter
 * opens; a card list on phone; skeleton rows, the empty state, the error state, and a thin line on top
 * while rows already shown are refreshed.
 */
const props = withDefaults(
  defineProps<{
    tableId: string
    caption: string
    columns: Column<T>[]
    rows: T[]
    rowKey: (row: T) => string
    rowTo?: (row: T) => RouteLocationRaw
    loading?: boolean
    refreshing?: boolean
    error?: unknown
    sort?: Sort | null
    sortMode?: 'server' | 'page'
    hasPrev?: boolean
    hasNext?: boolean
    pageSize?: number
    paged?: boolean
  }>(),
  { sortMode: 'page', pageSize: 25, paged: true, sort: null, rowTo: undefined, error: undefined },
)
const emit = defineEmits<{ 'update:sort': [Sort | null]; 'update:pageSize': [number]; prev: []; next: []; retry: [] }>()
const { t } = useI18n()
const router = useRouter()
const phone = useMedia(PHONE)

// ---- columns ----
const choice = ref<Record<string, boolean>>(readColumns(props.tableId) ?? {})
const optional = computed(() => props.columns.filter((c) => c.optional))
const visible = computed(() =>
  props.columns.filter((c) => !c.optional || (choice.value[c.key] ?? !!c.defaultVisible)),
)
const columnItems = computed<MenuItem[]>(() =>
  optional.value.map((c) => ({ kind: 'check', key: c.key, label: c.label, checked: choice.value[c.key] ?? !!c.defaultVisible })),
)
function toggleColumn(key: string) {
  const col = props.columns.find((c) => c.key === key)
  if (!col) return
  const next = { ...choice.value, [key]: !(choice.value[key] ?? !!col.defaultVisible) }
  choice.value = next
  writeColumns(props.tableId, next)
}

// ---- sorting ----
function cellValue(row: T, col: Column<T>): unknown {
  return col.sortValue ? col.sortValue(row) : (row as Record<string, unknown>)[col.key]
}
const shown = computed(() => {
  if (props.sortMode !== 'page' || !props.sort) return props.rows
  const col = props.columns.find((c) => c.key === props.sort!.key)
  if (!col) return props.rows
  const dir = props.sort.dir === 'asc' ? 1 : -1
  return [...props.rows].sort((a, b) => {
    const x = cellValue(a, col)
    const y = cellValue(b, col)
    if (x == null && y == null) return 0
    if (x == null) return 1
    if (y == null) return -1
    return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))) * dir
  })
})
function toggleSort(col: Column<T>) {
  const current = props.sort
  emit('update:sort', current?.key === col.key ? { key: col.key, dir: current.dir === 'asc' ? 'desc' : 'asc' } : { key: col.key, dir: 'asc' })
}
function ariaSort(col: Column<T>) {
  if (props.sort?.key !== col.key) return col.sortable ? 'none' : undefined
  return props.sort.dir === 'asc' ? 'ascending' : 'descending'
}

// ---- rows: whole row opens; j / k / Enter ----
const active = ref(-1)
const body = ref<HTMLElement | null>(null)
watch(
  () => props.rows,
  () => (active.value = -1),
)

function open(row: T) {
  if (props.rowTo) void router.push(props.rowTo(row))
}
function onRowClick(e: MouseEvent, row: T) {
  // Buttons and links in the row do their own thing; a text selection is not a click
  if ((e.target as HTMLElement).closest('a, button, input, select, label')) return
  if (window.getSelection?.()?.toString()) return
  open(row)
}
function focusRow(i: number) {
  const rows = body.value?.querySelectorAll<HTMLElement>('[data-row]')
  if (!rows?.length) return
  active.value = Math.max(0, Math.min(rows.length - 1, i))
  const target = rows[active.value]
  ;(target.querySelector<HTMLElement>('a[data-row-link]') ?? target).focus()
}
function onKey(e: KeyboardEvent) {
  if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return
  if (document.querySelector('[aria-modal="true"], [role="menu"]')) return
  if (e.key === 'j') focusRow(active.value + 1)
  else if (e.key === 'k') focusRow(active.value - 1)
  else return
  e.preventDefault()
}
onMounted(() => document.addEventListener('keydown', onKey))
onUnmounted(() => document.removeEventListener('keydown', onKey))

const cardTitle = computed(() => visible.value.find((c) => c.card === 'title') ?? visible.value[0])
const cardMeta = computed(() => visible.value.filter((c) => c.card === 'meta'))
const cardBadge = computed(() => visible.value.find((c) => c.card === 'badge'))
const showSkeleton = computed(() => props.loading && !props.rows.length && !props.error)
</script>

<template>
  <div class="u-card u-table-wrap" :data-table="tableId">
    <div v-if="refreshing && rows.length" class="u-refresh-line" role="status" :aria-label="t('ui.common.refreshing')"></div>
    <div v-if="optional.length || (sort && sortMode === 'page')" class="u-table-tools">
      <span v-if="sort && sortMode === 'page'" class="u-small u-muted">{{ t('ui.table.pageSorted') }}</span>
      <span class="flex-1"></span>
      <UiMenu v-if="optional.length" :label="t('ui.table.columns')" icon="columns" button-class="u-btn u-btn-sm u-btn-ghost" :items="columnItems" @select="toggleColumn" />
    </div>

    <ErrorState v-if="error" :error="error" @retry="emit('retry')" />
    <div v-else-if="showSkeleton" class="p-5"><SkeletonBlock :lines="6" :height="20" /></div>
    <div v-else-if="!rows.length && !loading"><slot name="empty" /></div>

    <!-- phone: one card per row with the two or three most important fields -->
    <ul v-else-if="phone" ref="body" class="u-cardlist" :aria-label="caption">
      <li v-for="(row, i) in shown" :key="rowKey(row)" data-row :class="{ 'is-active': i === active }" @click="onRowClick($event, row)">
        <div class="flex items-start gap-3">
          <div class="flex-1 min-w-0">
            <RouterLink v-if="rowTo" :to="rowTo(row)" data-row-link class="u-row-link font-semibold">
              <slot :name="`cell-${cardTitle.key}`" :row="row">{{ cellValue(row, cardTitle) }}</slot>
            </RouterLink>
            <span v-else class="font-semibold"><slot :name="`cell-${cardTitle.key}`" :row="row">{{ cellValue(row, cardTitle) }}</slot></span>
            <div class="u-small u-muted flex flex-wrap gap-x-3">
              <span v-for="c in cardMeta" :key="c.key"><slot :name="`cell-${c.key}`" :row="row">{{ cellValue(row, c) }}</slot></span>
            </div>
          </div>
          <slot v-if="cardBadge" :name="`cell-${cardBadge.key}`" :row="row">{{ cellValue(row, cardBadge) }}</slot>
        </div>
      </li>
    </ul>

    <div v-else class="u-table-scroll">
      <table class="u-table">
        <caption class="u-sr">{{ caption }}</caption>
        <thead>
          <tr>
            <th v-for="c in visible" :key="c.key" scope="col" :aria-sort="ariaSort(c)" :class="{ 'text-right': c.align === 'right' }">
              <button v-if="c.sortable" type="button" class="u-sort" :data-sort="c.key" @click="toggleSort(c)">
                {{ c.label }}
                <UiIcon :name="sort?.key === c.key ? (sort.dir === 'asc' ? 'sortUp' : 'sortDown') : 'sortBoth'" :size="13" />
              </button>
              <template v-else>{{ c.label }}</template>
            </th>
            <th v-if="$slots['row-actions']" scope="col"><span class="u-sr">{{ t('ui.table.actions') }}</span></th>
          </tr>
        </thead>
        <tbody ref="body">
          <tr
            v-for="(row, i) in shown"
            :key="rowKey(row)"
            data-row
            :class="{ 'is-active': i === active, 'is-link': !!rowTo }"
            @click="onRowClick($event, row)"
          >
            <td v-for="(c, ci) in visible" :key="c.key" :class="{ 'text-right': c.align === 'right', 'u-mono': c.mono }">
              <RouterLink v-if="ci === 0 && rowTo" :to="rowTo(row)" data-row-link class="u-row-link" @focus="active = i">
                <slot :name="`cell-${c.key}`" :row="row">{{ cellValue(row, c) }}</slot>
              </RouterLink>
              <slot v-else :name="`cell-${c.key}`" :row="row">{{ cellValue(row, c) ?? '—' }}</slot>
            </td>
            <td v-if="$slots['row-actions']" class="text-right whitespace-nowrap"><slot name="row-actions" :row="row" /></td>
          </tr>
        </tbody>
      </table>
    </div>

    <footer v-if="paged && !error && (rows.length || hasPrev)" class="u-table-foot">
      <label class="flex items-center gap-2 u-small u-muted">
        {{ t('ui.table.pageSize') }}
        <select class="u-field" style="height: 32px; width: auto" :value="pageSize" @change="emit('update:pageSize', Number(($event.target as HTMLSelectElement).value))">
          <option v-for="n in PAGE_SIZES" :key="n" :value="n">{{ n }}</option>
        </select>
      </label>
      <span class="flex-1"></span>
      <button type="button" class="u-btn u-btn-sm" :disabled="!hasPrev || loading" @click="emit('prev')"><UiIcon name="chevronLeft" :size="14" />{{ t('ui.table.prev') }}</button>
      <button type="button" class="u-btn u-btn-sm" :disabled="!hasNext || loading" @click="emit('next')">{{ t('ui.table.next') }}<UiIcon name="chevronRight" :size="14" /></button>
    </footer>
  </div>
</template>

<style>
.u-table-wrap {
  position: relative;
  overflow: hidden;
}
.u-refresh-line {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  background-size: 50% 100%;
  animation: u-line 1.2s linear infinite;
}
@keyframes u-line {
  from {
    background-position: -50% 0;
  }
  to {
    background-position: 150% 0;
  }
}
@media (prefers-reduced-motion: reduce) {
  .u-refresh-line {
    animation: none;
    background: var(--accent);
  }
}
.u-table-tools {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-2) var(--sp-3) var(--sp-2) var(--sp-5);
  border-bottom: 1px solid var(--line);
}
.u-table-scroll {
  overflow-x: auto;
}
.u-table {
  width: 100%;
  border-collapse: collapse;
}
.u-table th {
  height: 36px;
  padding: 0 var(--sp-5);
  background: var(--surface-2);
  text-align: left;
  white-space: nowrap;
  font-size: 0.75rem;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--muted);
}
.u-table td {
  height: var(--row-h);
  padding: 0 var(--sp-5);
  border-top: 1px solid var(--line);
  vertical-align: middle;
}
html[data-density='compact'] .u-table td {
  font-size: 0.8125rem;
}
.u-table tr.is-link {
  cursor: pointer;
}
.u-table tbody tr:hover,
.u-table tbody tr.is-active,
.u-cardlist li.is-active {
  background: var(--surface-2);
}
.u-sort {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  letter-spacing: inherit;
  text-transform: inherit;
  cursor: pointer;
}
.u-app a.u-row-link {
  color: inherit;
  text-decoration: none;
}
.u-app a.u-row-link:hover {
  text-decoration: underline;
}
.u-cardlist {
  margin: 0;
  padding: 0;
  list-style: none;
}
.u-cardlist li {
  padding: var(--sp-4) var(--sp-5);
  border-top: 1px solid var(--line);
  min-height: 44px;
}
.u-cardlist li:first-child {
  border-top: 0;
}
.u-table-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-5);
  border-top: 1px solid var(--line);
}
</style>
