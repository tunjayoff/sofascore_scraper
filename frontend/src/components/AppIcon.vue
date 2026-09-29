<script setup lang="ts">
/** Stroke icons, drawn in currentColor. */
defineProps<{ name: IconName; size?: number }>()
</script>

<script lang="ts">
const paths = {
  list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  download: 'M12 4v11M7 10l5 5 5-5M5 20h14',
  ball: 'M12 3a9 9 0 100 18 9 9 0 000-18zM12 7l4 3-1.5 4.5h-5L8 10z',
  pulse: 'M3 12h4l3 8 4-16 3 8h4',
  gear: 'M12 9a3 3 0 100 6 3 3 0 000-6zM12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1',
  plus: 'M12 5v14M5 12h14',
  x: 'M6 6l12 12M18 6L6 18',
  search: 'M11 4a7 7 0 100 14 7 7 0 000-14zM20 20l-4-4',
  chevronRight: 'M9 6l6 6-6 6',
  chevronLeft: 'M15 6l-6 6 6 6',
  trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  refresh: 'M20 11a8 8 0 10-2.3 5.7M20 5v6h-6',
  alert: 'M12 8v5M12 16.5h.01M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z',
} as const

export type IconName =
  | 'list'
  | 'download'
  | 'ball'
  | 'pulse'
  | 'gear'
  | 'plus'
  | 'x'
  | 'search'
  | 'chevronRight'
  | 'chevronLeft'
  | 'trash'
  | 'check'
  | 'refresh'
  | 'alert'
</script>

<template>
  <svg
    :width="size ?? 18"
    :height="size ?? 18"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name]" />
  </svg>
</template>
