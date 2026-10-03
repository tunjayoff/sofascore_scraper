<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import { focusables, trapTab } from '@/ui/focus'

/**
 * A panel from the right for secondary content (4.7): filters on phone, a raw payload, a job's log.
 * 480 px wide, full width on phone; modal like a dialog (focus trapped, Esc closes). The slide is off
 * when the system asks for reduced motion.
 */
defineProps<{ title: string }>()
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()
const root = ref<HTMLElement | null>(null)
const titleId = useId()
let returnTo: HTMLElement | null = null

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.stopPropagation()
    emit('close')
    return
  }
  trapTab(e, root.value)
}

onMounted(() => {
  returnTo = document.activeElement as HTMLElement | null
  void nextTick(() => {
    if (!root.value) return
    const items = focusables(root.value)
    ;(items[1] ?? items[0] ?? root.value).focus()
  })
})
onUnmounted(() => {
  if (returnTo && document.contains(returnTo)) returnTo.focus()
})
</script>

<template>
  <div class="u-overlay u-panel-overlay" @mousedown.self="emit('close')">
    <div ref="root" role="dialog" aria-modal="true" :aria-labelledby="titleId" tabindex="-1" class="u-panel" @keydown="onKeydown">
      <div class="u-panel-head">
        <h2 :id="titleId" class="u-h2 flex-1">{{ title }}</h2>
        <slot name="actions" />
        <button type="button" class="u-btn u-btn-ghost u-btn-sm u-btn-icon" :aria-label="t('ui.common.close')" @click="emit('close')">
          <UiIcon name="x" :size="16" />
        </button>
      </div>
      <div class="u-panel-body"><slot /></div>
    </div>
  </div>
</template>

<style>
.u-panel-overlay {
  padding: 0;
  justify-content: flex-end;
  align-items: stretch;
}
.u-panel {
  width: min(480px, 100vw);
  height: 100%;
  background: var(--surface);
  border-left: 1px solid var(--border);
  box-shadow: var(--shadow-pop);
  display: flex;
  flex-direction: column;
  animation: u-slide 0.18s ease-out;
}
.u-panel:focus {
  outline: none;
}
.u-panel-head {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-5) var(--sp-6);
  border-bottom: 1px solid var(--line);
}
.u-panel-body {
  flex: 1;
  overflow-y: auto;
  padding: var(--sp-6);
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}
@keyframes u-slide {
  from {
    transform: translateX(24px);
    opacity: 0.6;
  }
}
@media (prefers-reduced-motion: reduce) {
  .u-panel {
    animation: none;
  }
}
</style>
