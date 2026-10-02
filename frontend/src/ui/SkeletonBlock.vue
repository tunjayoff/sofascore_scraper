<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

/**
 * Grey blocks in the shape of the content while it loads (4.7). They appear after 300 ms, so a fast
 * answer does not flicker; until then nothing is drawn. `lines` blocks of the given heights.
 */
const SKELETON_DELAY_MS = 300

const props = withDefaults(defineProps<{ lines?: number; height?: number; delay?: number }>(), { lines: 3, height: 16, delay: undefined })
const { t } = useI18n()
const shown = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null

onMounted(() => {
  timer = setTimeout(() => (shown.value = true), props.delay ?? SKELETON_DELAY_MS)
})
onUnmounted(() => {
  if (timer) clearTimeout(timer)
})
</script>

<template>
  <div role="status" :aria-label="t('ui.common.loading')" data-testid="skeleton" class="flex flex-col gap-3">
    <template v-if="shown">
      <div v-for="i in lines" :key="i" class="u-skeleton" :style="{ height: `${height}px`, width: `${100 - ((i * 17) % 40)}%` }"></div>
    </template>
  </div>
</template>

<style>
.u-skeleton {
  border-radius: var(--r-control);
  background: var(--neutral-bg);
  animation: u-pulse 1.4s ease-in-out infinite;
}
@keyframes u-pulse {
  50% {
    opacity: 0.55;
  }
}
@media (prefers-reduced-motion: reduce) {
  .u-skeleton {
    animation: none;
  }
}
</style>
