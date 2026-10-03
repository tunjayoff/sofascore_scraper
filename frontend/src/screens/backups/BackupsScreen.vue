<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import DataTable, { type Column } from '@/ui/DataTable.vue'
import EmptyState from '@/ui/EmptyState.vue'
import TimeText from '@/ui/TimeText.vue'
import UiIcon from '@/ui/UiIcon.vue'
import UiMenu, { type MenuItem } from '@/ui/UiMenu.vue'
import { v1 } from '@/api/v1/client'
import type { BackupRecord } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { bytesText } from '@/ui/time'
import { jobKindText, scopeText } from '@/screens/jobs/jobText'
import CreateBackupDialog from './CreateBackupDialog.vue'
import RestoreDialog from './RestoreDialog.vue'

/**
 * Backups (6.11): the archives in the server's backups folder, newest first, each with Download, Check (a
 * dry run of the restore) and Restore. There is no upload (decision 15): an archive must already be in the
 * backups folder. Restoring itself is done with `ssc backup restore` while the API offers only the check;
 * the restore dialog leads there. While a job holds the data folder a banner says so (5.4).
 */
const { t } = useI18n()
const status = useStatusStore()
const rows = ref<BackupRecord[]>([])
const loading = ref(true)
const loaded = ref(false)
const error = ref<unknown>(null)
const creating = ref(false)
const restoring = ref<BackupRecord | null>(null)

async function load() {
  loading.value = true
  try {
    rows.value = await v1.backups()
    error.value = null
    loaded.value = true
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

function formatText(b: BackupRecord): string {
  if (b.format === 2) return '2'
  if (b.format === 1) return t('ui.backups.format1')
  return t('ui.backups.unreadable')
}

const columns = computed<Column<BackupRecord>[]>(() => [
  { key: 'name', label: t('ui.backups.col.name'), card: 'title', mono: true, sortable: true },
  { key: 'scope', label: t('ui.backups.col.scope'), card: 'meta', sortable: true },
  { key: 'created', label: t('ui.backups.col.created'), card: 'meta', sortable: true, sortValue: (b) => b.created_at_utc ?? '' },
  { key: 'size', label: t('ui.backups.col.size'), align: 'right', sortable: true, sortValue: (b) => b.bytes },
  { key: 'format', label: t('ui.backups.col.format'), sortValue: (b) => b.format ?? -1 },
])

const menu = computed<MenuItem[]>(() => [
  { key: 'download', label: t('ui.backups.download') },
  { key: 'check', label: t('ui.backups.check') },
  { key: 'restore', label: t('ui.backups.restore') },
])

function onMenu(b: BackupRecord, key: string) {
  if (key === 'download') {
    const a = document.createElement('a')
    a.href = v1.backupUrl(b.name)
    a.download = b.name
    document.body.appendChild(a)
    a.click()
    a.remove()
  } else restoring.value = b
}

// A backup job adds a file: the list is read again when the running job ends
let timer: ReturnType<typeof setInterval> | null = null
let lastActive: string | null = null
onMounted(() => {
  void load()
  timer = setInterval(() => {
    const id = status.activeJob?.id ?? null
    if (lastActive && id !== lastActive) void load()
    lastActive = id
  }, 2000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div>
    <PageHeader :title="t('ui.nav.backups')" :description="t('ui.backups.description')">
      <template #actions>
        <button type="button" class="u-btn" :disabled="loading" @click="load"><UiIcon name="refresh" :size="16" />{{ t('ui.common.refresh') }}</button>
        <button type="button" class="u-btn u-btn-primary" @click="creating = true"><UiIcon name="plus" :size="16" />{{ t('ui.backups.create') }}</button>
      </template>
    </PageHeader>

    <div class="flex flex-col gap-3 mb-4">
      <p class="m-0 u-notice"><UiIcon name="info" :size="16" /><span>{{ t('ui.backups.restoreNote') }}</span></p>
      <p v-if="status.activeJob" class="m-0 u-notice u-notice-warn" role="status" data-testid="busy-banner">
        <UiIcon name="alert" :size="16" />
        <span>
          {{ t('ui.backups.busy', { kind: jobKindText(status.activeJob.kind) }) }}
          <RouterLink :to="`/jobs/${status.activeJob.id}`">{{ t('ui.error.openJob') }}</RouterLink>
        </span>
      </p>
    </div>

    <DataTable
      table-id="backups"
      :caption="t('ui.nav.backups')"
      :columns="columns"
      :rows="rows"
      :row-key="(b) => b.name"
      :loading="loading && !loaded"
      :refreshing="loading && loaded"
      :error="error"
      :paged="false"
      @retry="load"
    >
      <template #cell-name="{ row }">
        <span class="inline-flex items-center gap-2 break-all">
          {{ row.name }}
          <span v-if="row.with_env" :title="t('ui.backups.withEnv')" class="inline-flex" style="color: var(--warn-fg)"><UiIcon name="lock" :size="14" /><span class="u-sr">{{ t('ui.backups.withEnv') }}</span></span>
        </span>
      </template>
      <template #cell-scope="{ row }">{{ scopeText(row.scope) }}</template>
      <template #cell-created="{ row }"><TimeText :value="row.created_at_utc" /></template>
      <template #cell-size="{ row }"><span class="u-num">{{ bytesText(row.bytes) }}</span></template>
      <template #cell-format="{ row }">{{ formatText(row) }}</template>
      <template #row-actions="{ row }">
        <UiMenu :label="t('ui.backups.actions', { name: row.name })" icon="more" icon-only align="right" button-class="u-btn u-btn-sm u-btn-ghost u-btn-icon" :items="menu" @select="(k) => onMenu(row, k)" />
      </template>
      <template #empty>
        <EmptyState icon="backups" :title="t('ui.backups.empty')" :text="t('ui.backups.emptyText')">
          <button type="button" class="u-btn" @click="creating = true">{{ t('ui.backups.create') }}</button>
        </EmptyState>
      </template>
    </DataTable>

    <CreateBackupDialog v-if="creating" @close="creating = false" />
    <RestoreDialog v-if="restoring" :backup="restoring" @close="restoring = null" />
  </div>
</template>
