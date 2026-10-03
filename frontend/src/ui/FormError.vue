<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { describeError } from '@/api/v1/errors'

/**
 * A refusal shown where the user acted (5.2), in a dialog or a form that keeps its input: the sentence of
 * the error code, the holder of the data folder for a 409, a link to that job (or to the running job) or to
 * Health for an upstream refusal, and the request id.
 */
const props = defineProps<{ error: unknown; activeJobId?: string | null }>()
const emit = defineEmits<{ navigate: [] }>()
const { t } = useI18n()
const view = computed(() => describeError(props.error))
const jobId = computed(() => {
  const v = view.value
  return v.jobId ?? (v.code === 'job_running' || v.code === 'data_operation_running' ? (props.activeJobId ?? null) : null)
})
</script>

<template>
  <div role="alert" class="u-small flex flex-col gap-1" style="color: var(--danger)" data-testid="form-error" :data-code="view.code">
    <p class="m-0 font-semibold">{{ view.text }}</p>
    <p v-if="view.detail" class="m-0 u-muted break-words">{{ view.detail }}</p>
    <p class="m-0 flex flex-wrap gap-3">
      <RouterLink v-if="jobId" :to="`/jobs/${jobId}`" class="font-semibold" @click="emit('navigate')">{{ t('ui.error.openJob') }}</RouterLink>
      <RouterLink v-else-if="view.toHealth" to="/system/health" class="font-semibold" @click="emit('navigate')">{{ t('ui.error.toHealth') }}</RouterLink>
      <span v-if="view.requestId" class="u-mono u-muted" style="font-size: 0.75rem">{{ t('ui.error.requestId') }} {{ view.requestId }}</span>
    </p>
  </div>
</template>
