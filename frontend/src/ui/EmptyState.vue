<script setup lang="ts">
import UiIcon, { type UiIconName } from '@/ui/UiIcon.vue'

/** Icon, one sentence, one action (4.7). Every list passes its own text. */
withDefaults(defineProps<{ icon?: UiIconName; title: string; text?: string }>(), { icon: 'info', text: '' })
</script>

<template>
  <div class="u-empty" data-testid="empty">
    <span class="u-empty-icon"><UiIcon :name="icon" :size="22" /></span>
    <p class="u-h3">{{ title }}</p>
    <p v-if="text" class="m-0 u-muted max-w-[52ch]">{{ text }}</p>
    <div v-if="$slots.default" class="mt-2 flex flex-wrap justify-center gap-2"><slot /></div>
  </div>
</template>

<style>
.u-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: var(--sp-3);
  padding: var(--sp-8) var(--sp-5);
}
.u-empty-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: var(--r-pill);
  background: var(--surface-2);
  border: 1px solid var(--border);
  color: var(--muted);
}
</style>
