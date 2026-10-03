<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import UiTabs from '@/ui/UiTabs.vue'
import FilterBar from '@/ui/FilterBar.vue'
import UiBadge from '@/ui/UiBadge.vue'
import UiIcon from '@/ui/UiIcon.vue'
import DataTable, { type Column } from '@/ui/DataTable.vue'
import EmptyState from '@/ui/EmptyState.vue'
import ErrorState from '@/ui/ErrorState.vue'
import FactList from '@/ui/FactList.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import CodeHint from '@/ui/CodeHint.vue'
import { v1, type LogLevel } from '@/api/v1/client'
import type { LogEntry, LogTail } from '@/api/v1/schema'
import type { Tone } from '@/ui/status'
import { bytesText, num } from '@/ui/time'

/**
 * Logs and diagnostics (6.14). The Log tab reads the newest lines of the server's log file (level filter on
 * the server, text filter within the lines shown, Refresh); log messages are English and shown as they are
 * (rule 8 of the implementation plan). The Diagnostics tab shows the summary the bundle carries (versions,
 * platform, data folder, the doctor's checks) and downloads the bundle, a zip with secrets removed. The tab
 * and the filters are in the query string.
 */
const LEVELS: LogLevel[] = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
const LIMITS = [200, 500, 1000, 2000]
const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const tab = computed<'log' | 'diagnostics'>(() => (route.query.tab === 'diagnostics' ? 'diagnostics' : 'log'))
const level = computed<LogLevel | ''>(() => (LEVELS.includes(route.query.level as LogLevel) ? (route.query.level as LogLevel) : ''))
const limit = computed(() => (LIMITS.includes(Number(route.query.lines)) ? Number(route.query.lines) : 200))
const text = ref(typeof route.query.q === 'string' ? route.query.q : '')

function setQuery(patch: Record<string, string | number | null>) {
  const query = { ...route.query }
  for (const [k, v] of Object.entries(patch)) {
    if (v == null || v === '' || (k === 'lines' && v === 200) || (k === 'tab' && v === 'log')) delete query[k]
    else query[k] = String(v)
  }
  void router.replace({ query })
}

// ---- log ----
const tail = ref<LogTail | null>(null)
const logLoading = ref(false)
const logError = ref<unknown>(null)
async function loadLog() {
  logLoading.value = true
  try {
    tail.value = await v1.logs({ limit: limit.value, level: level.value || null })
    logError.value = null
  } catch (e) {
    logError.value = e
  } finally {
    logLoading.value = false
  }
}

type Row = LogEntry & { n: number }
/** Newest first; the server sends them oldest first. */
const rows = computed<Row[]>(() => {
  const entries = (tail.value?.entries ?? []).map((e, n) => ({ ...e, n }))
  const q = text.value.trim().toLowerCase()
  const shown = q ? entries.filter((e) => e.message.toLowerCase().includes(q) || e.logger.toLowerCase().includes(q)) : entries
  return shown.reverse()
})
const columns = computed<Column<Row>[]>(() => [
  { key: 'time', label: t('ui.logs.col.time'), mono: true, card: 'meta' },
  { key: 'level', label: t('ui.logs.col.level'), card: 'badge' },
  { key: 'logger', label: t('ui.logs.col.logger'), card: 'meta' },
  { key: 'message', label: t('ui.logs.col.message'), card: 'title' },
  { key: 'pid', label: t('ui.logs.col.pid'), optional: true, mono: true },
])
function levelTone(l: string): Tone {
  if (l === 'ERROR' || l === 'CRITICAL') return 'danger'
  if (l === 'WARNING') return 'warn'
  if (l === 'DEBUG') return 'neutral'
  return 'info'
}

// ---- diagnostics ----
const diag = ref<Record<string, unknown> | null>(null)
const diagLoading = ref(false)
const diagError = ref<unknown>(null)
async function loadDiagnostics() {
  diagLoading.value = true
  try {
    diag.value = await v1.diagnostics()
    diagError.value = null
  } catch (e) {
    diagError.value = e
  } finally {
    diagLoading.value = false
  }
}
type Rec = Record<string, unknown>
const obj = (v: unknown): Rec => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Rec) : {})
const str = (v: unknown): string | null => (v == null || v === '' ? null : String(v))
const facts = computed(() => {
  const d = obj(diag.value)
  const app = obj(d.app)
  const rt = obj(d.runtime)
  const dir = obj(d.data_dir)
  const logging = obj(d.logging)
  return [
    { key: 'version', label: t('ui.logs.diag.version'), value: [str(app.version), str(app.commit)].filter(Boolean).join(' · ') || null, mono: true },
    { key: 'python', label: t('ui.logs.diag.python'), value: [str(rt.python), str(rt.implementation)].filter(Boolean).join(' ') || null },
    { key: 'platform', label: t('ui.logs.diag.platform'), value: str(rt.platform), mono: true },
    { key: 'docker', label: t('ui.logs.diag.docker'), value: rt.docker === true ? t('ui.common.yes') : rt.docker === false ? t('ui.common.no') : null },
    { key: 'dataDir', label: t('ui.logs.diag.dataDir'), value: str(dir.path), mono: true },
    { key: 'free', label: t('ui.logs.diag.free'), value: typeof dir.disk_free_mb === 'number' ? bytesText(dir.disk_free_mb * 1024 * 1024) : null },
    { key: 'logFile', label: t('ui.logs.diag.logFile'), value: str(logging.file), mono: true },
    { key: 'logLevel', label: t('ui.logs.diag.logLevel'), value: str(logging.level) },
  ]
})
type Check = { id: string; status: string; label?: string; summary?: string | null; fix_command?: string | null }
const doctor = computed(() => obj(obj(diag.value).doctor))
const checks = computed<Check[]>(() => (Array.isArray(doctor.value.checks) ? (doctor.value.checks as Check[]) : []))
function checkTone(s: string): Tone {
  return s === 'ok' ? 'ok' : s === 'warn' ? 'warn' : s === 'fail' ? 'danger' : 'neutral'
}

const tabs = computed(() => [
  { key: 'log' as const, label: t('ui.logs.tab.log') },
  { key: 'diagnostics' as const, label: t('ui.logs.tab.diagnostics') },
])

const chips = computed(() => {
  const out: { key: string; label: string }[] = []
  if (level.value) out.push({ key: 'level', label: level.value })
  if (text.value.trim()) out.push({ key: 'q', label: `“${text.value.trim()}”` })
  return out
})
function removeFilter(key: string) {
  if (key === 'q') text.value = ''
  else setQuery({ [key]: null })
}
function clearFilters() {
  text.value = ''
  setQuery({ level: null, q: null })
}

function loadTab() {
  if (tab.value === 'log') void loadLog()
  else if (!diag.value) void loadDiagnostics()
}
watch(tab, loadTab)
watch([level, limit], () => void loadLog())
watch(text, (q) => setQuery({ q: q.trim() || null }))
onMounted(loadTab)
</script>

<template>
  <div>
    <PageHeader :title="t('ui.logs.title')" :description="t('ui.logs.description')">
      <template #actions>
        <a :href="v1.diagnosticsBundleUrl" download class="u-btn" data-testid="bundle"><UiIcon name="exports" :size="16" />{{ t('ui.logs.bundle') }}</a>
      </template>
    </PageHeader>

    <UiTabs :tabs="tabs" :model-value="tab" id-prefix="logs" :label="t('ui.logs.title')" @update:model-value="(k) => setQuery({ tab: k })">
      <template v-if="tab === 'log'">
        <FilterBar :active-count="chips.length" :chips="chips" @clear="clearFilters" @remove="removeFilter">
          <label class="flex flex-col">
            <span class="u-label">{{ t('ui.logs.minLevel') }}</span>
            <select class="u-field" :value="level" data-filter="level" @change="setQuery({ level: ($event.target as HTMLSelectElement).value })">
              <option value="">{{ t('ui.filter.all') }}</option>
              <option v-for="l in LEVELS" :key="l" :value="l">{{ l }}</option>
            </select>
          </label>
          <label class="flex flex-col">
            <span class="u-label">{{ t('ui.logs.lines') }}</span>
            <select class="u-field" :value="limit" data-filter="lines" @change="setQuery({ lines: Number(($event.target as HTMLSelectElement).value) })">
              <option v-for="n in LIMITS" :key="n" :value="n">{{ num(n) }}</option>
            </select>
          </label>
          <label class="flex flex-col min-w-[240px]">
            <span class="u-label">{{ t('ui.logs.text') }} <span class="font-normal u-muted">({{ t('ui.filter.inPage') }})</span></span>
            <input v-model="text" class="u-field" type="search" data-filter-focus autocomplete="off" />
          </label>
          <button type="button" class="u-btn" :disabled="logLoading" @click="loadLog"><UiIcon name="refresh" :size="16" />{{ t('ui.common.refresh') }}</button>
        </FilterBar>
        <p v-if="tail" class="m-0 mb-3 u-small u-muted">
          {{ t('ui.logs.summary', { n: num(tail.count), level: tail.level ?? '—' }) }}
          <span v-if="tail.file" class="u-mono"> · {{ tail.file }}</span>
        </p>
        <div v-if="tail && !tail.enabled" class="u-card">
          <EmptyState icon="logs" :title="t('ui.logs.off')" :text="t('ui.logs.offText')" />
        </div>
        <DataTable
          v-else
          table-id="logs"
          :caption="t('ui.logs.tab.log')"
          :columns="columns"
          :rows="rows"
          :row-key="(r) => String(r.n)"
          :loading="logLoading && !tail"
          :refreshing="logLoading && !!tail"
          :error="logError"
          :paged="false"
          @retry="loadLog"
        >
          <template #cell-time="{ row }"><span class="whitespace-nowrap u-small">{{ row.time }}</span></template>
          <template #cell-level="{ row }"><UiBadge :tone="levelTone(row.level)">{{ row.level }}</UiBadge></template>
          <template #cell-logger="{ row }"><span class="u-small u-muted">{{ row.logger }}</span></template>
          <template #cell-message="{ row }"><span class="u-mono u-log-message">{{ row.message }}</span></template>
          <template #cell-pid="{ row }">{{ row.pid }}</template>
          <template #empty>
            <EmptyState icon="logs" :title="text || level ? t('ui.logs.emptyFiltered') : t('ui.logs.empty')" />
          </template>
        </DataTable>
      </template>

      <template v-else>
        <div v-if="diagLoading && !diag" class="u-card p-6"><SkeletonBlock :lines="6" /></div>
        <div v-else-if="diagError && !diag" class="u-card"><ErrorState :error="diagError" @retry="loadDiagnostics" /></div>
        <div v-else-if="diag" class="grid gap-6 lg:grid-cols-2">
          <section class="u-card p-6 flex flex-col gap-3" data-testid="diag-summary">
            <h2 class="u-h3">{{ t('ui.logs.diag.title') }}</h2>
            <FactList :items="facts" />
            <p class="m-0 u-small u-muted">{{ t('ui.logs.diag.bundleNote') }}</p>
          </section>
          <section class="u-card p-6 flex flex-col gap-3" data-testid="diag-doctor">
            <header class="flex items-center gap-3">
              <h2 class="u-h3 flex-1">{{ t('ui.logs.diag.checks') }}</h2>
              <UiBadge v-if="doctor.status" :tone="checkTone(String(doctor.status))">{{ t(`ui.logs.diag.status.${['ok', 'warn', 'fail'].includes(String(doctor.status)) ? doctor.status : 'other'}`) }}</UiBadge>
            </header>
            <ul class="m-0 p-0 list-none flex flex-col">
              <li v-for="c in checks" :key="c.id" class="flex flex-wrap items-start gap-3 py-2" style="border-top: 1px solid var(--line)">
                <UiBadge :tone="checkTone(c.status)" :icon="c.status === 'ok' ? 'okCircle' : 'alert'">{{ t(`ui.logs.diag.status.${['ok', 'warn', 'fail'].includes(c.status) ? c.status : 'other'}`) }}</UiBadge>
                <span class="flex-1 min-w-0 flex flex-col">
                  <span class="font-semibold">{{ c.label ?? c.id }}</span>
                  <span v-if="c.summary" class="u-small u-muted break-words">{{ c.summary }}</span>
                  <span v-if="c.fix_command" class="mt-1"><CodeHint :command="c.fix_command" /></span>
                </span>
              </li>
            </ul>
            <p class="m-0 u-small u-muted">{{ t('ui.logs.diag.checksNote') }}</p>
          </section>
        </div>
      </template>
    </UiTabs>
  </div>
</template>

<style>
.u-log-message {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 0.8125rem;
}
</style>
