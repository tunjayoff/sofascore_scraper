<script setup lang="ts">
import { computed, onUnmounted, ref, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import type { HelpTerm } from '@/app/help'

/**
 * An (i) next to a word of the glossary (FX-14a): a toggletip. The button says which word it explains;
 * a click (or Enter, Space) shows the explanation in a live region next to it, Esc or a click elsewhere
 * hides it. The same texts are in the help panel.
 */
const props = defineProps<{ term: HelpTerm }>()
const { t } = useI18n()
const open = ref(false)
const id = useId()
const root = ref<HTMLElement | null>(null)
const name = computed(() => t(`ui.help.term.${props.term}.name`))

function onOutside(e: MouseEvent) {
  if (root.value && !root.value.contains(e.target as Node)) close()
}
function close() {
  open.value = false
  document.removeEventListener('mousedown', onOutside)
}
function toggle() {
  if (open.value) return close()
  open.value = true
  document.addEventListener('mousedown', onOutside)
}
onUnmounted(() => document.removeEventListener('mousedown', onOutside))
</script>

<template>
  <span ref="root" class="u-tip" :data-term="term" @keydown.esc.stop="close">
    <button
      type="button"
      class="u-tip-button"
      :aria-label="t('ui.help.tipLabel', { term: name })"
      :aria-expanded="open"
      :aria-controls="id"
      data-testid="help-tip"
      @click="toggle"
    >
      <UiIcon name="info" :size="15" />
    </button>
    <span :id="id" role="status" class="u-tip-body-wrap">
      <span v-if="open" class="u-pop u-tip-body"
        ><strong>{{ name }}</strong> {{ t(`ui.help.term.${term}.text`) }}</span
      >
    </span>
  </span>
</template>

<style>
.u-tip {
  position: relative;
  display: inline-flex;
  align-items: center;
  vertical-align: middle;
  font-size: 0.875rem;
  font-weight: 400;
  letter-spacing: normal;
  text-transform: none;
}
.u-tip-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: none;
  color: var(--muted);
  cursor: pointer;
}
.u-tip-button:hover,
.u-tip-button[aria-expanded='true'] {
  color: var(--accent);
  background: var(--accent-soft);
}
.u-tip-body {
  position: absolute;
  z-index: 60;
  top: calc(100% + 4px);
  left: 0;
  width: min(320px, calc(100vw - 48px));
  padding: var(--sp-4);
  color: var(--text);
  line-height: 1.4;
  white-space: normal;
}
@media (max-width: 639px) {
  .u-tip-body {
    position: fixed;
    top: auto;
    bottom: 80px;
    left: 16px;
    right: 16px;
    width: auto;
  }
}
</style>
