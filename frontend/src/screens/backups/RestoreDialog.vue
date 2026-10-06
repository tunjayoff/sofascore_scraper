<script setup lang="ts">
import { computed, onUnmounted, ref, useId } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiDialog from '@/ui/UiDialog.vue'
import UiIcon from '@/ui/UiIcon.vue'
import CodeHint from '@/ui/CodeHint.vue'
import FormError from '@/ui/FormError.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import ProgressBar from '@/ui/ProgressBar.vue'
import { V1Error } from '@/api/v1/client'
import type { BackupRecord, Job } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { num } from '@/ui/time'
import { countLabel, countShown, jobErrorText, readProgress, scopeText } from '@/screens/jobs/jobText'
import { startJob, waitForJob } from '@/screens/jobs/startJob'
import JobOutput from '@/screens/jobs/JobOutput.vue'

/**
 * Restore in three steps (6.11; FX-14b, the real restore of FX-13):
 *  1. Check: a dry run of the restore as a job (`restore` with `dry_run: true`): what the archive holds
 *     and whether the data folder is empty.
 *  2. Choose: restore into the empty folder, or replace the current data (moved to the trash folder
 *     first; there is no merge); replacing can be checked again with `force`.
 *  3. Restore: the user types the shown word, then the restore runs as a job (`dry_run: false`, `force`
 *     when replacing). A data folder that is not empty answers 400 `confirmation_required` with
 *     `details.occupied`: the dialog says what is there and asks again before it sends `force`. The job
 *     is followed to its end; its result says whether the index was rebuilt and what the check after the
 *     restore found. The job history is the backup's afterwards, so the status is read again.
 * The server command stays as the other way (`ssc backup restore … --yes`).
 */
const props = defineProps<{ backup: BackupRecord }>()
const emit = defineEmits<{ close: [] }>()
const { t, locale } = useI18n()
const status = useStatusStore()
const uid = useId()

const step = ref<1 | 2 | 3>(1)
const busy = ref(false)
const error = ref<unknown>(null)
const check = ref<Job | null>(null)
const forced = ref<Job | null>(null)
const replace = ref(false)
const typed = ref('')
const occupiedNow = ref<string[] | null>(null)
const running = ref<Job | null>(null)
let controller: AbortController | null = null

type Report = { format?: number; scope?: string | null; counts?: Record<string, number>; occupied?: string[]; replaced?: string[]; restored?: string[]; skipped?: string[] }
const report = (j: Job | null): Report | null => {
  const r = (j?.result as Record<string, unknown> | null)?.restore
  return r && typeof r === 'object' ? (r as Report) : null
}
const checked = computed(() => report(check.value))
const forcedReport = computed(() => report(forced.value))
const occupied = computed(() => checked.value?.occupied ?? [])
const counts = computed(() => Object.entries(checked.value?.counts ?? {}).filter(([k, v]) => typeof v === 'number' && countShown(k)))
const command = computed(() => `ssc backup restore ${props.backup.name}${replace.value ? ' --force' : ''} --yes`)
const word = computed(() => t('ui.restore.word'))
const upper = (s: string) => s.trim().toLocaleUpperCase(locale.value)
const confirmed = computed(() => upper(typed.value) === upper(word.value))
const done = computed(() => !!running.value && !['queued', 'running'].includes(running.value.state))
const progress = computed(() => readProgress(running.value?.progress).percent)

async function run(force: boolean) {
  controller?.abort()
  controller = new AbortController()
  busy.value = true
  error.value = null
  try {
    const job = await startJob({ kind: 'restore', spec: { name: props.backup.name, force, dry_run: true } })
    const ended = await waitForJob(job.id, { signal: controller.signal })
    if (force) forced.value = ended
    else check.value = ended
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

/** The restore itself; `force` replaces what is in the data folder. */
async function restore(force: boolean) {
  controller?.abort()
  controller = new AbortController()
  busy.value = true
  error.value = null
  occupiedNow.value = null
  try {
    const job = await startJob({ kind: 'restore', spec: { name: props.backup.name, force, dry_run: false } })
    running.value = job
    // the dialog can be closed while the job runs; Jobs shows it too
    busy.value = false
    running.value = await waitForJob(job.id, { signal: controller.signal, onUpdate: (j) => (running.value = j) })
    // the job history and the data are the backup's now
    void status.refresh().catch(() => {})
  } catch (e) {
    if (e instanceof V1Error && e.code === 'confirmation_required') {
      const what = e.details?.occupied
      occupiedNow.value = Array.isArray(what) ? what.map(String) : []
    } else if ((e as Error)?.name !== 'AbortError') error.value = e
  } finally {
    busy.value = false
  }
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

    <!-- 3: restore, here as a job (or on the server) -->
    <template v-else>
      <template v-if="!running">
        <p class="m-0 u-notice u-notice-warn"><UiIcon name="alert" :size="16" />{{ replace ? t('ui.restore.replaceWarning') : t('ui.restore.emptyWarning') }}</p>
        <p class="m-0">{{ t('ui.restore.runsHere') }}</p>
        <p class="m-0 u-small u-muted">{{ t('ui.restore.stopFirst') }}</p>
        <div v-if="occupiedNow" class="flex flex-col gap-2 u-notice u-notice-warn items-start" role="alert" data-testid="restore-occupied">
          <span>{{ t('ui.restore.occupiedNow', { what: occupiedNow.join(', ') || '—' }) }}</span>
          <button type="button" class="u-btn u-btn-sm u-btn-danger" :disabled="busy" data-testid="restore-force" @click="restore(true)">{{ t('ui.restore.replaceAndRestore') }}</button>
        </div>
        <div v-else>
          <label class="u-label" :for="`${uid}-word`">{{ t('ui.confirm.typeWord', { word }) }}</label>
          <input :id="`${uid}-word`" v-model="typed" class="u-field u-mono" autocomplete="off" spellcheck="false" data-testid="restore-word" @keydown.enter.prevent="confirmed && !busy && restore(replace)" />
        </div>
        <details class="u-small" data-testid="restore-cli">
          <summary class="cursor-pointer">{{ t('ui.restore.orServer') }}</summary>
          <div class="flex flex-col gap-2 pt-2">
            <p class="m-0">{{ t('ui.restore.serverText') }}</p>
            <div><CodeHint :command="command" /></div>
          </div>
        </details>
      </template>
      <div v-else class="flex flex-col gap-3" data-testid="restore-running" aria-live="polite">
        <p class="m-0 flex items-center gap-2">
          <StatusBadge kind="job" :value="running.state" />
          <RouterLink :to="`/jobs/${running.id}`" class="u-small" @click="emit('close')">{{ t('ui.error.openJob') }}</RouterLink>
        </p>
        <ProgressBar v-if="!done" :value="progress" :label="t('ui.restore.progress')" />
        <p v-if="!done" class="m-0 u-small u-muted">{{ t('ui.restore.running') }}</p>
        <p v-else-if="running.state === 'succeeded'" class="m-0 font-semibold" data-testid="restore-done">{{ t('ui.restore.done') }}</p>
        <p v-else class="m-0" style="color: var(--danger)" data-testid="restore-failed">{{ t('ui.restore.failed') }} {{ jobErrorText(running) ?? '' }}</p>
        <JobOutput v-if="done" :job="running" />
      </div>
    </template>

    <FormError v-if="error" :error="error" :active-job-id="status.activeJob?.id" @navigate="emit('close')" />

    <template #actions>
      <button v-if="step > 1 && !running" type="button" class="u-btn" :disabled="busy" @click="step = (step - 1) as 1 | 2">{{ t('ui.restore.back') }}</button>
      <button type="button" class="u-btn" :disabled="busy" @click="emit('close')">{{ step === 3 ? t('ui.common.close') : t('ui.common.cancel') }}</button>
      <button v-if="step === 1 && !checked" type="button" class="u-btn u-btn-primary" :disabled="busy" data-testid="run-check" @click="run(false)">
        <span v-if="busy" class="u-spinner" aria-hidden="true"></span>{{ busy ? t('ui.restore.checking') : t('ui.restore.runCheck') }}
      </button>
      <button v-else-if="step === 1" type="button" class="u-btn u-btn-primary" data-testid="next" @click="toStep2">{{ t('ui.restore.next') }}</button>
      <button v-else-if="step === 2" type="button" class="u-btn u-btn-primary" :disabled="busy" data-testid="next" @click="step = 3">{{ t('ui.restore.next') }}</button>
      <button v-else-if="!running && !occupiedNow" type="button" class="u-btn u-btn-danger-solid" :disabled="!confirmed || busy" data-testid="restore-run" @click="restore(replace)">
        <span v-if="busy" class="u-spinner" aria-hidden="true"></span>{{ t('ui.restore.run') }}
      </button>
    </template>
  </UiDialog>
</template>
