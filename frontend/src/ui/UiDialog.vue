<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import { focusables, trapTab } from '@/ui/focus'

/**
 * Modal dialog (4.7): title, body, actions. Focus moves in and is trapped; Esc and the backdrop close it
 * unless work is in progress (`busy`); focus returns to where it was. The parent mounts it with v-if.
 */
const props = withDefaults(defineProps<{ title: string; busy?: boolean; wide?: boolean; role?: 'dialog' | 'alertdialog' }>(), { role: 'dialog' })
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()

const root = ref<HTMLElement | null>(null)
const titleId = useId()
const bodyId = useId()
let returnTo: HTMLElement | null = null

function close() {
  if (!props.busy) emit('close')
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.stopPropagation()
    close()
    return
  }
  trapTab(e, root.value)
}

onMounted(() => {
  returnTo = document.activeElement as HTMLElement | null
  void nextTick(() => {
    if (!root.value) return
    const preferred = root.value.querySelector<HTMLElement>('[data-autofocus]')
    ;(preferred ?? focusables(root.value).find((el) => !el.dataset.dialogClose) ?? root.value).focus()
  })
})
onUnmounted(() => {
  if (returnTo && document.contains(returnTo)) returnTo.focus()
})
</script>

<template>
  <div class="u-overlay" @mousedown.self="close">
    <div
      ref="root"
      :role="role"
      aria-modal="true"
      :aria-labelledby="titleId"
      :aria-describedby="bodyId"
      tabindex="-1"
      class="u-pop u-dialog"
      :class="{ 'u-dialog-wide': wide }"
      @keydown="onKeydown"
    >
      <div class="flex items-start gap-3">
        <h2 :id="titleId" class="u-h2 flex-1">{{ title }}</h2>
        <button type="button" class="u-btn u-btn-ghost u-btn-sm u-btn-icon" data-dialog-close="1" :disabled="busy" :aria-label="t('ui.common.close')" @click="close">
          <UiIcon name="x" :size="16" />
        </button>
      </div>
      <div :id="bodyId" class="u-dialog-body"><slot /></div>
      <footer v-if="$slots.actions" class="u-dialog-actions"><slot name="actions" /></footer>
    </div>
  </div>
</template>

<style>
.u-overlay {
  position: fixed;
  inset: 0;
  z-index: 70;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 12vh var(--sp-5) var(--sp-5);
  background: var(--overlay);
  overflow-y: auto;
}
.u-dialog {
  width: 100%;
  max-width: 480px;
  padding: var(--sp-6);
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}
.u-dialog:focus {
  outline: none;
}
.u-dialog-wide {
  max-width: 640px;
}
.u-dialog-body {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}
.u-dialog-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--sp-3);
}
</style>
