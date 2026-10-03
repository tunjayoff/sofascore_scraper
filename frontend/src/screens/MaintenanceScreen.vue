<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import CodeHint from '@/ui/CodeHint.vue'
import UiIcon from '@/ui/UiIcon.vue'
import UiBadge from '@/ui/UiBadge.vue'
import type { ClearJobSpec } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { num } from '@/ui/time'
import { jobKindText, scopeText } from '@/screens/jobs/jobText'
import { startJob } from '@/screens/jobs/startJob'

/**
 * Maintenance (6.11): three cards. Rebuild the index (safe; reads the stored files), shown with the reason
 * when `/status` reports one. The old data layout: how many matches are stored in the 2.x layout and the
 * server command that moves them; there is no migrate button (decision 16). Clear data: a scope and a
 * typed confirmation of that scope; follows, job history, the change log, backups and exports stay. Both
 * jobs work on the data folder only and send nothing to SofaScore.
 */
const { t } = useI18n()
const status = useStatusStore()
type ClearScope = NonNullable<ClearJobSpec['scope']>
const SCOPES: ClearScope[] = ['match_details', 'matches', 'seasons', 'all']

const summary = computed(() => status.status?.summary ?? null)
const reason = computed(() => summary.value?.catalog_rebuild_reason ?? null)
const legacy = computed(() => summary.value?.legacy_events ?? null)
const rebuilding = ref(false)
const rebuildBusy = ref(false)
const rebuildError = ref<unknown>(null)
const clearScope = ref<ClearScope>('match_details')
const clearing = ref(false)
const clearBusy = ref(false)
const clearError = ref<unknown>(null)

async function rebuild() {
  rebuildBusy.value = true
  rebuildError.value = null
  try {
    await startJob({ kind: 'rebuild', spec: { mode: 'auto' } })
    rebuilding.value = false
  } catch (e) {
    rebuildError.value = e
  } finally {
    rebuildBusy.value = false
  }
}

async function clear() {
  clearBusy.value = true
  clearError.value = null
  try {
    await startJob({ kind: 'clear', spec: { scope: clearScope.value, confirm: true } })
    clearing.value = false
  } catch (e) {
    clearError.value = e
  } finally {
    clearBusy.value = false
  }
}

onMounted(() => {
  if (!status.status && !status.loading) void status.refresh().catch(() => {})
})
</script>

<template>
  <div>
    <PageHeader :title="t('ui.nav.maintenance')" :description="t('ui.maintenance.description')" />

    <p v-if="status.activeJob" class="m-0 mb-4 u-notice u-notice-warn" role="status" data-testid="busy-banner">
      <UiIcon name="alert" :size="16" />
      <span>
        {{ t('ui.backups.busy', { kind: jobKindText(status.activeJob.kind) }) }}
        <RouterLink :to="`/jobs/${status.activeJob.id}`">{{ t('ui.error.openJob') }}</RouterLink>
      </span>
    </p>

    <div class="grid gap-6 lg:grid-cols-2">
      <section class="u-card p-6 flex flex-col gap-4" data-testid="card-rebuild">
        <header class="flex items-center gap-3">
          <h2 class="u-h3 flex-1">{{ t('ui.maintenance.rebuild.title') }}</h2>
          <UiBadge v-if="reason" tone="warn" icon="alert">{{ t('ui.maintenance.rebuild.needed') }}</UiBadge>
        </header>
        <p class="m-0">{{ t('ui.maintenance.rebuild.text') }}</p>
        <p v-if="reason" class="m-0 u-notice u-notice-warn" data-testid="rebuild-reason">
          <UiIcon name="alert" :size="16" /><span>{{ t('ui.maintenance.rebuild.reason') }} <span class="u-mono">{{ reason }}</span></span>
        </p>
        <div><button type="button" class="u-btn" @click="rebuilding = true"><UiIcon name="maintenance" :size="16" />{{ t('ui.maintenance.rebuild.button') }}</button></div>
      </section>

      <section class="u-card p-6 flex flex-col gap-4" data-testid="card-legacy">
        <h2 class="u-h3">{{ t('ui.maintenance.legacy.title') }}</h2>
        <p v-if="legacy" class="m-0">{{ t('ui.maintenance.legacy.count', { n: num(legacy) }) }}</p>
        <p v-else-if="legacy === 0" class="m-0">{{ t('ui.maintenance.legacy.none') }}</p>
        <p v-else class="m-0 u-muted">{{ t('ui.maintenance.legacy.unknown') }}</p>
        <p class="m-0 u-small u-muted">{{ t('ui.maintenance.legacy.how') }}</p>
        <div class="flex flex-col gap-2 items-start">
          <CodeHint command="ssc migrate --dry-run" />
          <CodeHint command="ssc migrate" />
        </div>
      </section>

      <section class="u-card p-6 flex flex-col gap-4 lg:col-span-2" data-testid="card-clear">
        <h2 class="u-h3">{{ t('ui.maintenance.clear.title') }}</h2>
        <p class="m-0">{{ t('ui.maintenance.clear.text') }}</p>
        <label class="flex flex-col max-w-[360px]">
          <span class="u-label">{{ t('ui.maintenance.clear.scope') }}</span>
          <select v-model="clearScope" class="u-field" data-testid="clear-scope">
            <option v-for="s in SCOPES" :key="s" :value="s">{{ scopeText(s) }}</option>
          </select>
        </label>
        <p class="m-0 u-small u-muted">{{ t(`ui.maintenance.clear.hint.${clearScope}`) }}</p>
        <p class="m-0 u-small u-muted">{{ t('ui.maintenance.clear.kept') }}</p>
        <div><button type="button" class="u-btn u-btn-danger" data-testid="clear-open" @click="clearing = true"><UiIcon name="x" :size="16" />{{ t('ui.maintenance.clear.button') }}</button></div>
      </section>
    </div>

    <ConfirmDialog
      v-if="rebuilding"
      :title="t('ui.maintenance.rebuild.confirmTitle')"
      :confirm-label="t('ui.maintenance.rebuild.button')"
      :busy="rebuildBusy"
      :error="rebuildError"
      :active-job-id="status.activeJob?.id"
      @confirm="rebuild"
      @close="rebuilding = false"
    >
      <p class="m-0">{{ t('ui.maintenance.rebuild.confirmText') }}</p>
      <p class="m-0 u-small u-muted">{{ t('ui.jobs.start.local') }}</p>
    </ConfirmDialog>

    <ConfirmDialog
      v-if="clearing"
      :title="t('ui.maintenance.clear.confirmTitle', { scope: scopeText(clearScope) })"
      :confirm-label="t('ui.maintenance.clear.button')"
      danger
      :typed-word="clearScope"
      :busy="clearBusy"
      :error="clearError"
      :active-job-id="status.activeJob?.id"
      @confirm="clear"
      @close="clearing = false"
    >
      <p class="m-0">{{ t(`ui.maintenance.clear.hint.${clearScope}`) }}</p>
      <p class="m-0">{{ t('ui.maintenance.clear.kept') }}</p>
      <p class="m-0 u-small u-muted">{{ t('ui.maintenance.clear.irreversible') }}</p>
    </ConfirmDialog>
  </div>
</template>
