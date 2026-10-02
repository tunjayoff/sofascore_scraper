<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import CopyButton from '@/ui/CopyButton.vue'
import { describeError } from '@/api/v1/errors'

/**
 * What failed, in words from the error code, a Retry button and the request id with a copy button, so the
 * server's log line can be found (4.7, 5.2).
 */
const props = defineProps<{ error: unknown; compact?: boolean }>()
const emit = defineEmits<{ retry: [] }>()
const { t } = useI18n()
const view = computed(() => describeError(props.error))
</script>

<template>
  <div class="u-error" :class="{ 'u-error-compact': compact }" role="alert" data-testid="error-state" :data-code="view.code">
    <span class="u-error-icon"><UiIcon name="error" :size="20" /></span>
    <div class="flex-1 min-w-0 flex flex-col gap-1">
      <p class="m-0 font-semibold">{{ view.text }}</p>
      <p v-if="view.detail" class="m-0 u-small u-muted break-words">{{ view.detail }}</p>
      <p v-if="view.requestId" class="m-0 flex items-center gap-1 u-mono u-muted" style="font-size: 0.75rem">
        {{ t('ui.error.requestId') }} {{ view.requestId }}
        <CopyButton :text="view.requestId" :label="t('ui.error.copyRequestId')" />
      </p>
      <div class="flex flex-wrap gap-2 mt-1">
        <button type="button" class="u-btn u-btn-sm" @click="emit('retry')"><UiIcon name="refresh" :size="14" />{{ t('ui.common.retry') }}</button>
        <RouterLink v-if="view.jobId" :to="`/jobs/${view.jobId}`" class="u-btn u-btn-sm">{{ t('ui.error.openJob') }}</RouterLink>
        <RouterLink v-if="view.toHealth" to="/system/health" class="u-btn u-btn-sm">{{ t('ui.error.toHealth') }}</RouterLink>
        <slot />
      </div>
    </div>
  </div>
</template>

<style>
.u-error {
  display: flex;
  gap: var(--sp-4);
  padding: var(--sp-6);
}
.u-error-compact {
  padding: var(--sp-4);
}
.u-error-icon {
  color: var(--danger);
  margin-top: 1px;
}
</style>
