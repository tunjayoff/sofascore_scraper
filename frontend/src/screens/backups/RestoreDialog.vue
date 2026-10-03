<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiDialog from '@/ui/UiDialog.vue'
import UiIcon from '@/ui/UiIcon.vue'
import CodeHint from '@/ui/CodeHint.vue'
import FormError from '@/ui/FormError.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import type { BackupRecord, Job } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { num } from '@/ui/time'
import { countLabel, jobErrorText, scopeText } from '@/screens/jobs/jobText'
import { startJob, waitForJob } from '@/screens/jobs/startJob'

/**
 * Restore in three steps (6.11). 1 Check: a dry run of the restore as a job (`restore` with
 * `dry_run: true`) that reports what the archive holds and whether the data folder is empty. 2 Choose:
 * restore into the empty folder, or replace the current data (moved to the trash folder first; there is no
 * merge); replacing can be checked again with `force`. 3 Restore: the API offers only the check (restoring
 * replaces the job history the API itself records jobs in), so the last step gives the server command with
 * the choice made, instead of a button.
 */
const props = defineProps<{ backup: BackupRecord }>()
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()
const status = useStatusStore()

const step = ref<1 | 2 | 3>(1)
const busy = ref(false)
const error = ref<unknown>(null)
const check = ref<Job | null>(null)
const forced = ref<Job | null>(null)
const replace = ref(false)
let controller: AbortController | null = null

type Report = { format?: number; scope?: string | null; counts?: Record<string, number>; occupied?: string[]; replaced?: string[]; restored?: string[]; skipped?: string[] }
const report = (j: Job | null): Report | null => {
  const r = (j?.result as Record<string, unknown> | null)?.restore
  return r && typeof r === 'object' ? (r as Report) : null
}
const checked = computed(() => report(check.value))
const forcedReport = computed(() => report(forced.value))
const occupied = computed(() => checked.value?.occupied ?? [])
const counts = computed(() => Object.entries(checked.value?.counts ?? {}).filter(([, v]) => typeof v === 'number'))
const command = computed(() => `ssc backup restore ${props.backup.name}${replace.value ? ' --force' : ''} --yes`)

async function run(force: boolean) {
  controller?.abort()
  controller = new AbortController()
  busy.value = true
  error.value = null
  try {
    const job = await startJob({ kind: 'restore', spec: { name: props.backup.name, force, dry_run: true } })
    const done = await waitForJob(job.id, { signal: controller.signal })
    if (force) forced.value = done
    else check.value = done
  } catch (e) {
    if ((e as Error)?.name !== 'AbortError') error.value = e
  } finally {
    busy.value = false
  }
}

function toStep2() {
  replace.value = occupied.value.length > 0
  step.value = 2
}

onUnmounted(() => controller?.abort())
</script>

<template>
  <UiDialog :title="t('ui.restore.title', { name: backup.name })" :busy="busy" wide @close="emit('close')">
    <ol class="u-steps" :aria-label="t('ui.restore.steps')">
      <li v-for="i in 3" :key="i" :aria-current="step === i ? 'step' : undefined" :class="{ 'is-done': step > i }">
        <span class="u-step-n">{{ i }}</span>{{ t(`ui.restore.step${i}`) }}
      </li>
    </ol>

    <!-- 1: check -->
    <template v-if="step === 1">
      <p class="m-0">{{ t('ui.restore.checkText') }}</p>
      <p class="m-0 u-small u-muted">{{ t('ui.jobs.start.local') }}</p>
      <div v-if="check" class="flex flex-col gap-3" data-testid="restore-check">
        <p class="m-0 flex items-center gap-2"><StatusBadge kind="job" :value="check.state" /><RouterLink :to="`/jobs/${check.id}`" class="u-small" @click="emit('close')">{{ t('ui.error.openJob') }}</RouterLink></p>
        <p v-if="check.error" class="m-0" style="color: var(--danger)">{{ jobErrorText(check) }}</p>
        <dl v-if="checked" class="u-result">
          <dt>{{ t('ui.backups.col.format') }}</dt>
          <dd>{{ checked.format === 1 ? t('ui.backups.format1') : checked.format }}</dd>
          <dt>{{ t('ui.backups.col.scope') }}</dt>
          <dd>{{ checked.scope ? scopeText(checked.scope) : '—' }}</dd>
          <template v-for="[k, v] in counts" :key="k">
            <dt>{{ countLabel(k) }}</dt>
            <dd class="u-num">{{ num(v) }}</dd>
          </template>
          <dt>{{ t('ui.restore.dataFolder') }}</dt>
          <dd>{{ occupied.length ? t('ui.restore.occupied', { what: occupied.join(', ') }) : t('ui.jobOutput.emptyFolder') }}</dd>
        </dl>
      </div>
    </template>

    <!-- 2: choose -->
    <template v-else-if="step === 2">
      <fieldset class="flex flex-col gap-2">
        <legend class="u-label">{{ t('ui.restore.how') }}</legend>
        <label class="u-option">
          <input v-model="replace" type="radio" name="restore-how" :value="false" class="u-check" :disabled="occupied.length > 0" />
          <span class="flex flex-col"><span class="font-semibold">{{ t('ui.restore.intoEmpty') }}</span><span class="u-small u-muted">{{ occupied.length ? t('ui.restore.notEmpty') : t('ui.restore.intoEmptyHint') }}</span></span>
        </label>
        <label class="u-option">
          <input v-model="replace" type="radio" name="restore-how" :value="true" class="u-check" />
          <span class="flex flex-col"><span class="font-semibold">{{ t('ui.restore.replace') }}</span><span class="u-small u-muted">{{ t('ui.restore.replaceHint') }}</span></span>
        </label>
      </fieldset>
      <p class="m-0 u-small u-muted">{{ t('ui.restore.noMerge') }}</p>
      <div v-if="replace" class="flex flex-col gap-2">
        <button type="button" class="u-btn u-btn-sm self-start" :disabled="busy" @click="run(true)">
          <span v-if="busy" class="u-spinner" aria-hidden="true"></span>{{ t('ui.restore.checkReplace') }}
        </button>
        <p v-if="forcedReport" class="m-0 u-small" data-testid="restore-replaced">
          {{ forcedReport.replaced?.length ? t('ui.restore.wouldMove', { what: forcedReport.replaced.join(', ') }) : t('ui.restore.wouldMoveNothing') }}
        </p>
      </div>
    </template>

    <!-- 3: restore on the server -->
    <template v-else>
      <p class="m-0 u-notice u-notice-warn"><UiIcon name="alert" :size="16" />{{ replace ? t('ui.restore.replaceWarning') : t('ui.restore.emptyWarning') }}</p>
      <p class="m-0">{{ t('ui.restore.apiChecksOnly') }}</p>
      <div><CodeHint :command="command" /></div>
      <p class="m-0 u-small u-muted">{{ t('ui.restore.stopFirst') }}</p>
    </template>

    <FormError v-if="error" :error="error" :active-job-id="status.activeJob?.id" @navigate="emit('close')" />

    <template #actions>
      <button v-if="step > 1" type="button" class="u-btn" :disabled="busy" @click="step = (step - 1) as 1 | 2">{{ t('ui.restore.back') }}</button>
      <button type="button" class="u-btn" :disabled="busy" @click="emit('close')">{{ step === 3 ? t('ui.common.close') : t('ui.common.cancel') }}</button>
      <button v-if="step === 1 && !checked" type="button" class="u-btn u-btn-primary" :disabled="busy" data-testid="run-check" @click="run(false)">
        <span v-if="busy" class="u-spinner" aria-hidden="true"></span>{{ busy ? t('ui.restore.checking') : t('ui.restore.runCheck') }}
      </button>
      <button v-else-if="step === 1" type="button" class="u-btn u-btn-primary" data-testid="next" @click="toStep2">{{ t('ui.restore.next') }}</button>
      <button v-else-if="step === 2" type="button" class="u-btn u-btn-primary" :disabled="busy" data-testid="next" @click="step = 3">{{ t('ui.restore.next') }}</button>
    </template>
  </UiDialog>
</template>
