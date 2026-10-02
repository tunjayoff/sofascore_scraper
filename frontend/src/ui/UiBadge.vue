<script setup lang="ts">
import UiIcon, { type UiIconName } from '@/ui/UiIcon.vue'
import type { Tone } from '@/ui/status'

/** A pill with an icon, a word and a tone (4.6): the icon keeps the state readable without colour. */
withDefaults(defineProps<{ tone?: Tone; icon?: UiIconName; spin?: boolean; outline?: boolean }>(), { tone: 'neutral', icon: undefined })
</script>

<template>
  <span class="u-badge" :class="[`u-badge-${tone}`, { 'u-badge-outline': outline }]">
    <span v-if="spin" class="u-spinner" aria-hidden="true" style="width: 11px; height: 11px; border-width: 1.5px"></span>
    <UiIcon v-else-if="icon" :name="icon" :size="13" />
    <slot />
  </span>
</template>

<style>
.u-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  padding: 0 9px;
  border-radius: var(--r-pill);
  font-size: 0.75rem;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  border: 1px solid transparent;
}
.u-badge-ok {
  background: var(--ok-bg);
  color: var(--ok-fg);
}
.u-badge-warn {
  background: var(--warn-bg);
  color: var(--warn-fg);
}
.u-badge-danger {
  background: var(--danger-bg);
  color: var(--danger);
}
.u-badge-info {
  background: var(--info-bg);
  color: var(--info-fg);
}
.u-badge-neutral {
  background: var(--neutral-bg);
  color: var(--neutral-fg);
}
.u-badge-outline {
  background: transparent;
  border-color: var(--border);
  color: var(--muted);
}
</style>
