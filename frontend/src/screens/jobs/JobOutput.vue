<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import { v1 } from '@/api/v1/client'
import type { Job } from '@/api/v1/schema'
import { bytesText, duration, num } from '@/ui/time'
import { countLabel, countShown, scopeText } from './jobText'

/**
 * What a finished data job produced (6.9 "Output"): the export file and the backup with a download
 * button, what a clear removed, what a rebuild of the index found, and what a restore check would do.
 * Everything comes from the job's `result`, read defensively: a field the server leaves out is not shown.
 */
const props = defineProps<{ job: Job }>()
const { t } = useI18n()

type Rec = Record<string, unknown>
const part = (key: string): Rec | null => {
  const v = (props.job.result as Rec | null)?.[key]
  return v && typeof v === 'object' ? (v as Rec) : null
}
const n = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : [])

const exp = computed(() => part('export'))
const backup = computed(() => part('backup'))
const clear = computed(() => part('clear'))
const rebuild = computed(() => part('rebuild'))
const restore = computed(() => part('restore'))
const counts = computed(() => Object.entries((restore.value?.counts as Rec | undefined) ?? {}).filter(([k, v]) => typeof v === 'number' && countShown(k)) as [string, number][])
const succeeded = computed(() => props.job.state === 'succeeded' || props.job.state === 'partial')
</script>

<template>
  <div v-if="exp || backup || clear || rebuild || restore" class="flex flex-col gap-3" data-testid="job-output">
    <template v-if="exp">
      <dl class="u-result">
        <dt>{{ t('ui.jobOutput.file') }}</dt>
        <dd class="u-mono break-all">{{ exp.file ?? '—' }}</dd>
        <dt>{{ t('ui.jobOutput.rows') }}</dt>
        <dd class="u-num">{{ num(n(exp.rows)) }}</dd>
        <template v-if="n(exp.events) != null">
          <dt>{{ t('ui.jobOutput.events') }}</dt>
          <dd class="u-num">{{ num(n(exp.events)) }}</dd>
        </template>
        <dt>{{ t('ui.jobOutput.size') }}</dt>
        <dd class="u-num">{{ bytesText(n(exp.bytes)) }}</dd>
        <template v-if="n(exp.skipped)">
          <dt>{{ t('ui.jobOutput.skipped') }}</dt>
          <dd class="u-num">{{ num(n(exp.skipped)) }}</dd>
        </template>
      </dl>
      <div v-if="succeeded" class="flex gap-2">
        <a :href="v1.exportUrl(job.id)" download class="u-btn u-btn-sm"><UiIcon name="exports" :size="14" />{{ t('ui.jobOutput.download') }}</a>
        <RouterLink to="/exports" class="u-btn u-btn-sm u-btn-ghost">{{ t('ui.nav.exports') }}</RouterLink>
      </div>
    </template>

    <template v-if="backup">
      <dl class="u-result">
        <dt>{{ t('ui.jobOutput.archive') }}</dt>
        <dd class="u-mono break-all">{{ backup.name ?? '—' }}</dd>
        <dt>{{ t('ui.jobOutput.scope') }}</dt>
        <dd>{{ scopeText(String(backup.scope ?? '')) }}</dd>
        <dt>{{ t('ui.jobOutput.size') }}</dt>
        <dd class="u-num">{{ bytesText(n(backup.bytes)) }}</dd>
        <template v-if="backup.with_env">
          <dt>{{ t('ui.jobOutput.secrets') }}</dt>
          <dd style="color: var(--warn-fg)">{{ t('ui.backups.withEnv') }}</dd>
        </template>
      </dl>
      <div v-if="succeeded && typeof backup.name === 'string'" class="flex gap-2">
        <a :href="v1.backupUrl(backup.name)" download class="u-btn u-btn-sm"><UiIcon name="exports" :size="14" />{{ t('ui.jobOutput.download') }}</a>
        <RouterLink to="/backups" class="u-btn u-btn-sm u-btn-ghost">{{ t('ui.nav.backups') }}</RouterLink>
      </div>
    </template>

    <dl v-if="clear" class="u-result">
      <dt>{{ t('ui.jobOutput.cleared') }}</dt>
      <dd>{{ list(clear.cleared).map(scopeText).join(', ') || t('ui.jobOutput.nothing') }}</dd>
      <template v-if="n(clear.v3_events) != null">
        <dt>{{ t('ui.jobOutput.events') }}</dt>
        <dd class="u-num">{{ num(n(clear.v3_events)) }}</dd>
      </template>
      <dt>{{ t('ui.jobOutput.indexRebuilt') }}</dt>
      <dd>{{ clear.catalog_rebuilt ? t('ui.common.yes') : t('ui.common.no') }}</dd>
    </dl>

    <dl v-if="rebuild" class="u-result">
      <dt>{{ t('ui.jobOutput.events') }}</dt>
      <dd class="u-num">{{ num(n(rebuild.events)) }}</dd>
      <template v-if="n(rebuild.events_legacy)">
        <dt>{{ t('ui.jobOutput.legacy') }}</dt>
        <dd class="u-num">{{ num(n(rebuild.events_legacy)) }}</dd>
      </template>
      <dt>{{ t('ui.jobOutput.slices') }}</dt>
      <dd class="u-num">{{ num(n(rebuild.slices)) }}</dd>
      <dt>{{ t('ui.jobOutput.problems') }}</dt>
      <dd class="u-num">{{ num(n(rebuild.problems)) }}</dd>
      <template v-if="n(rebuild.seconds) != null">
        <dt>{{ t('ui.jobOutput.took') }}</dt>
        <dd class="u-num">{{ duration(n(rebuild.seconds)) }}</dd>
      </template>
    </dl>

    <template v-if="restore">
      <p class="m-0 u-small u-muted">{{ t('ui.jobOutput.dryRun') }}</p>
      <dl class="u-result">
        <dt>{{ t('ui.jobOutput.archive') }}</dt>
        <dd class="u-mono break-all">{{ restore.name ?? '—' }}</dd>
        <template v-for="[k, v] in counts" :key="k">
          <dt>{{ countLabel(k) }}</dt>
          <dd class="u-num">{{ num(v) }}</dd>
        </template>
        <dt>{{ t('ui.jobOutput.occupied') }}</dt>
        <dd>{{ list(restore.occupied).join(', ') || t('ui.jobOutput.emptyFolder') }}</dd>
      </dl>
      <RouterLink to="/backups" class="u-btn u-btn-sm self-start">{{ t('ui.nav.backups') }}</RouterLink>
    </template>
  </div>
</template>
