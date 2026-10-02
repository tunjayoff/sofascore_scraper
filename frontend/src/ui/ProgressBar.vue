<script setup lang="ts">
import { computed } from 'vue'
import { pct } from '@/ui/time'

/**
 * Determinate bar with percent, or indeterminate when `value` is null (4.7). `label` names the bar for
 * screen readers; `aria-valuenow` carries the number (4.9).
 */
const props = defineProps<{ value: number | null | undefined; label: string; text?: string }>()
const clamped = computed(() => (props.value == null ? null : Math.max(0, Math.min(100, Math.round(props.value)))))
</script>

<template>
  <div class="flex items-center gap-3">
    <div
      class="u-track flex-1"
      role="progressbar"
      :aria-label="label"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-valuenow="clamped ?? undefined"
      :aria-valuetext="clamped == null ? text : `${pct(clamped)}${text ? ` · ${text}` : ''}`"
    >
      <div v-if="clamped != null" class="u-track-fill" :style="{ width: `${clamped}%` }"></div>
      <div v-else class="u-track-fill u-track-indeterminate"></div>
    </div>
    <span v-if="clamped != null" class="u-num font-semibold min-w-[3.5ch] text-right">{{ pct(clamped) }}</span>
  </div>
</template>

<style>
.u-track {
  position: relative;
  height: 8px;
  border-radius: var(--r-pill);
  background: var(--neutral-bg);
  overflow: hidden;
}
.u-track-fill {
  height: 100%;
  border-radius: var(--r-pill);
  background: var(--accent);
  transition: width 0.4s ease;
}
.u-track-indeterminate {
  width: 30%;
  animation: u-indeterminate 1.4s ease-in-out infinite;
}
@keyframes u-indeterminate {
  from {
    transform: translateX(-100%);
  }
  to {
    transform: translateX(340%);
  }
}
@media (prefers-reduced-motion: reduce) {
  .u-track-indeterminate {
    animation: none;
    width: 100%;
    opacity: 0.35;
  }
}
</style>
