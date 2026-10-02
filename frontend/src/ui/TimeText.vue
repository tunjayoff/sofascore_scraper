<script setup lang="ts">
import { computed, onUnmounted } from 'vue'
import { formatTime, now, parseTime, relative as relativeText, useClock, utcText, type TimeStyle } from '@/ui/time'

/**
 * A time in the browser's local zone; the exact UTC value is the hover text and is read by screen
 * readers (decision 13). `relative` shows "3 min ago" instead, with the local time on hover.
 */
const props = defineProps<{ value: string | number | null | undefined; format?: TimeStyle; relative?: boolean }>()

const date = computed(() => parseTime(props.value))
const stop = props.relative ? useClock() : null
onUnmounted(() => stop?.())

const text = computed(() => {
  if (!date.value) return '—'
  return props.relative ? relativeText(date.value, now.value) : formatTime(date.value, props.format ?? 'datetime')
})
const hover = computed(() => {
  if (!date.value) return undefined
  return props.relative ? `${formatTime(date.value, 'seconds')} · ${utcText(date.value)}` : utcText(date.value)
})
</script>

<template>
  <time v-if="date" :datetime="date.toISOString()" :title="hover" class="u-num whitespace-nowrap"
    >{{ text }}<span class="u-sr"> ({{ hover }})</span></time
  >
  <span v-else class="u-muted">—</span>
</template>
