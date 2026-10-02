<script setup lang="ts" generic="K extends string">
import { nextTick } from 'vue'

/**
 * Keyboard-navigable tabs (4.7, WAI-ARIA tabs): ←/→ move, Home/End jump; the selected tab is the only one
 * in the Tab order. The screen keeps the selection in the query string (v-model). The default slot is the
 * panel of the selected tab.
 */
const props = defineProps<{ tabs: readonly { key: K; label: string; badge?: string }[]; modelValue: K; idPrefix: string; label: string }>()
const emit = defineEmits<{ 'update:modelValue': [K] }>()

function select(key: K) {
  emit('update:modelValue', key)
}

function onKeydown(e: KeyboardEvent) {
  const keys = props.tabs.map((x) => x.key)
  const i = keys.indexOf(props.modelValue)
  let next: number
  if (e.key === 'ArrowRight') next = (i + 1) % keys.length
  else if (e.key === 'ArrowLeft') next = (i - 1 + keys.length) % keys.length
  else if (e.key === 'Home') next = 0
  else if (e.key === 'End') next = keys.length - 1
  else return
  e.preventDefault()
  select(keys[next])
  void nextTick(() => document.getElementById(`${props.idPrefix}-tab-${keys[next]}`)?.focus())
}
</script>

<template>
  <div>
    <div role="tablist" :aria-label="label" class="u-tabs" @keydown="onKeydown">
      <button
        v-for="tab in tabs"
        :id="`${idPrefix}-tab-${tab.key}`"
        :key="tab.key"
        type="button"
        role="tab"
        class="u-tab"
        :aria-selected="tab.key === modelValue"
        :aria-controls="`${idPrefix}-panel`"
        :tabindex="tab.key === modelValue ? 0 : -1"
        @click="select(tab.key)"
      >
        {{ tab.label }}<span v-if="tab.badge" class="u-tab-badge">{{ tab.badge }}</span>
      </button>
    </div>
    <div :id="`${idPrefix}-panel`" role="tabpanel" :aria-labelledby="`${idPrefix}-tab-${modelValue}`" tabindex="0" class="u-tabpanel">
      <slot />
    </div>
  </div>
</template>

<style>
.u-tabs {
  display: flex;
  gap: var(--sp-2);
  overflow-x: auto;
  border-bottom: 1px solid var(--border);
}
.u-tab {
  min-height: 44px;
  padding: 0 var(--sp-4);
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.u-tab[aria-selected='true'] {
  color: var(--text);
  border-bottom-color: var(--accent);
}
.u-tab-badge {
  margin-left: var(--sp-2);
  padding: 0 6px;
  border-radius: var(--r-pill);
  background: var(--neutral-bg);
  color: var(--neutral-fg);
  font-size: 0.75rem;
}
.u-tabpanel {
  padding-top: var(--sp-6);
}
.u-tabpanel:focus {
  outline: none;
}
.u-tabpanel:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}
</style>
