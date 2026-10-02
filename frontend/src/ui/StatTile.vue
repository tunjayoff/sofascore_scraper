<script setup lang="ts">
import { RouterLink, type RouteLocationRaw } from 'vue-router'

/** A number with a label and an optional small bar (4.7); a link when `to` is given. */
defineProps<{ label: string; value: string; note?: string; bar?: number | null; to?: RouteLocationRaw }>()
</script>

<template>
  <component :is="to ? RouterLink : 'div'" :to="to" class="u-card u-tile" :class="{ 'u-tile-link': to }">
    <span class="u-caption">{{ label }}</span>
    <span class="u-display">{{ value }}</span>
    <span v-if="note" class="u-small u-muted">{{ note }}</span>
    <span v-if="bar != null" class="u-track" aria-hidden="true" style="height: 4px"><span class="u-track-fill block" :style="{ width: `${Math.max(0, Math.min(100, bar))}%` }"></span></span>
  </component>
</template>

<style>
.u-tile {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  padding: var(--sp-5);
  min-width: 0;
}
.u-app a.u-tile-link {
  color: inherit;
  text-decoration: none;
}
.u-app a.u-tile-link:hover {
  border-color: var(--accent);
}
</style>
