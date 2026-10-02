<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import { dismiss, uiToasts } from '@/ui/toast'

const { t } = useI18n()
const icon = { ok: 'okCircle', info: 'info', error: 'error' } as const
</script>

<template>
  <div class="u-toasts" aria-live="polite" aria-relevant="additions" data-testid="toasts">
    <div v-for="item in uiToasts" :key="item.id" class="u-pop u-toast" :class="`u-toast-${item.kind}`" :data-kind="item.kind">
      <span class="u-toast-icon"><UiIcon :name="icon[item.kind]" /></span>
      <div class="flex-1 min-w-0">
        <p class="m-0">{{ item.text }}</p>
        <p v-if="item.detail" class="m-0 u-small u-muted break-words">{{ item.detail }}</p>
        <p v-if="item.requestId" class="m-0 u-mono u-muted" style="font-size: 0.75rem">{{ t('ui.error.requestId') }} {{ item.requestId }}</p>
        <RouterLink v-if="item.link" :to="item.link.to" class="u-small font-semibold" @click="dismiss(item.id)">{{ item.link.label }}</RouterLink>
      </div>
      <button type="button" class="u-btn u-btn-ghost u-btn-sm u-btn-icon" :aria-label="t('ui.common.close')" @click="dismiss(item.id)">
        <UiIcon name="x" :size="14" />
      </button>
    </div>
  </div>
</template>

<style>
.u-toasts {
  position: fixed;
  z-index: 80;
  right: var(--sp-5);
  bottom: var(--sp-5);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  width: min(420px, calc(100vw - 32px));
}
@media (max-width: 767px) {
  .u-toasts {
    left: var(--sp-5);
    right: var(--sp-5);
    width: auto;
    bottom: 76px;
  }
}
.u-toast {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-4);
  padding: var(--sp-4) var(--sp-4) var(--sp-4) var(--sp-5);
}
.u-toast-icon {
  margin-top: 2px;
  color: var(--accent);
}
.u-toast-error {
  border-color: var(--danger);
}
.u-toast-error .u-toast-icon {
  color: var(--danger);
}
.u-toast-info .u-toast-icon {
  color: var(--info-fg);
}
</style>
