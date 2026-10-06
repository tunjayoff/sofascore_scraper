<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiDialog from '@/ui/UiDialog.vue'
import { describeError } from '@/api/v1/errors'

/**
 * For destructive or costly actions (4.7): the body states what will happen. With `typedWord` (an
 * irreversible action) the button enables only after the user typed that word, in either case of the
 * user's language ("sil" for "SİL"). A refusal is shown in the dialog, so the user can read it and try
 * again.
 */
const props = defineProps<{
  title: string
  confirmLabel: string
  danger?: boolean
  typedWord?: string
  busy?: boolean
  error?: unknown
  /** The running job to link to when the refusal is a 409 that names none (from `/status.active_job`). */
  activeJobId?: string | null
}>()
const emit = defineEmits<{ confirm: []; close: [] }>()
const { t, locale } = useI18n()

const typed = ref('')
const upper = (s: string) => s.trim().toLocaleUpperCase(locale.value)
const inputId = useId()
const ready = computed(() => !props.typedWord || upper(typed.value) === upper(props.typedWord))
const errorView = computed(() => (props.error ? describeError(props.error) : null))
const errorJob = computed(() => {
  const v = errorView.value
  if (!v) return null
  return v.jobId ?? (v.code === 'job_running' || v.code === 'data_operation_running' ? (props.activeJobId ?? null) : null)
})

function confirm() {
  if (ready.value && !props.busy) emit('confirm')
}
</script>

<template>
  <UiDialog :title="title" :busy="busy" role="alertdialog" @close="emit('close')">
    <slot />
    <div v-if="typedWord">
      <label class="u-label" :for="inputId">{{ t('ui.confirm.typeWord', { word: typedWord }) }}</label>
      <input :id="inputId" v-model="typed" class="u-field u-mono" autocomplete="off" spellcheck="false" data-autofocus @keydown.enter.prevent="confirm" />
    </div>
    <div v-if="errorView" role="alert" class="u-small" style="color: var(--danger)">
      <p class="m-0">{{ errorView.text }}</p>
      <p v-if="errorView.detail" class="m-0 u-muted">{{ errorView.detail }}</p>
      <RouterLink v-if="errorJob" :to="`/jobs/${errorJob}`" class="font-semibold" @click="emit('close')">{{ t('ui.error.openJob') }}</RouterLink>
      <RouterLink v-else-if="errorView.toHealth" to="/system/health" class="font-semibold" @click="emit('close')">{{ t('ui.error.toHealth') }}</RouterLink>
    </div>
    <template #actions>
      <!-- the safe choice has the focus first: Cancel for a destructive action, the field when a word is asked -->
      <button type="button" class="u-btn" :disabled="busy" :data-autofocus="danger && !typedWord ? '1' : undefined" @click="emit('close')">{{ t('ui.common.cancel') }}</button>
      <button
        type="button"
        class="u-btn"
        :class="danger ? 'u-btn-danger-solid' : 'u-btn-primary'"
        :disabled="!ready || busy"
        data-testid="confirm"
        :data-autofocus="typedWord || danger ? undefined : '1'"
        @click="confirm"
      >
        <span v-if="busy" class="u-spinner" aria-hidden="true"></span>{{ confirmLabel }}
      </button>
    </template>
  </UiDialog>
</template>
