<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import UiDialog from '@/ui/UiDialog.vue'
import UiIcon from '@/ui/UiIcon.vue'
import FormError from '@/ui/FormError.vue'
import type { BackupJobSpec, Job } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { startJob } from '@/screens/jobs/startJob'

/**
 * Create backup (6.11): the scope (everything, the state only, the data only) and whether the archive also
 * takes `.env`, off by default and with the warning that it then holds secrets. The backup is a job; a
 * refusal (another job writes, a data operation runs) stays in the dialog.
 */
const emit = defineEmits<{ close: []; started: [Job] }>()
const { t } = useI18n()
const status = useStatusStore()
const SCOPES = ['all', 'state', 'data'] as const
const scope = ref<(typeof SCOPES)[number]>('all')
const withEnv = ref(false)
const busy = ref(false)
const error = ref<unknown>(null)

async function submit() {
  busy.value = true
  error.value = null
  try {
    const spec: BackupJobSpec = { scope: scope.value, include_env: withEnv.value }
    const job = await startJob({ kind: 'backup', spec })
    emit('started', job)
    emit('close')
  } catch (e) {
    error.value = e
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <UiDialog :title="t('ui.backups.createTitle')" :busy="busy" @close="emit('close')">
    <form id="backup-form" class="flex flex-col gap-4" @submit.prevent="submit">
      <fieldset class="flex flex-col gap-2">
        <legend class="u-label">{{ t('ui.backups.col.scope') }}</legend>
        <label v-for="s in SCOPES" :key="s" class="u-option">
          <input v-model="scope" type="radio" name="backup-scope" :value="s" class="u-check" />
          <span class="flex flex-col"><span class="font-semibold">{{ t(`ui.scope.${s}`) }}</span><span class="u-small u-muted">{{ t(`ui.backups.scopeHint.${s}`) }}</span></span>
        </label>
      </fieldset>
      <label class="flex items-start gap-3">
        <input v-model="withEnv" type="checkbox" class="u-check mt-1" data-testid="with-env" />
        <span class="flex flex-col">
          <span class="font-semibold">{{ t('ui.backups.includeEnv') }}</span>
          <span class="u-small u-muted">{{ t('ui.backups.includeEnvHint') }}</span>
        </span>
      </label>
      <p v-if="withEnv" class="m-0 u-notice u-notice-warn" role="alert"><UiIcon name="alert" :size="16" />{{ t('ui.backups.secretsWarning') }}</p>
      <p class="m-0 u-small u-muted">{{ t('ui.jobs.start.local') }}</p>
      <FormError v-if="error" :error="error" :active-job-id="status.activeJob?.id" @navigate="emit('close')" />
    </form>
    <template #actions>
      <button type="button" class="u-btn" :disabled="busy" @click="emit('close')">{{ t('ui.common.cancel') }}</button>
      <button type="submit" form="backup-form" class="u-btn u-btn-primary" :disabled="busy" data-testid="confirm">
        <span v-if="busy" class="u-spinner" aria-hidden="true"></span>{{ t('ui.backups.createSubmit') }}
      </button>
    </template>
  </UiDialog>
</template>
