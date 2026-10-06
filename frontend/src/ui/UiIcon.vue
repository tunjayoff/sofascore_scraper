<script setup lang="ts">
/** Stroke icons of the design system, drawn in currentColor; decorative (the text next to them carries the meaning). */
defineProps<{ name: UiIconName; size?: number }>()
</script>

<script lang="ts">
export const ICONS = {
  home: 'M3 11l9-7 9 7M5 10v10h5v-6h4v6h5V10',
  follows: 'M6 3h12v18l-6-4-6 4z',
  events: 'M3 5h18v15H3zM3 10h18M8 3v4M16 3v4',
  corrections: 'M4 12a8 8 0 108-8M4 4v5h5M12 8v4l3 2',
  jobs: 'M7 5l12 7-12 7z',
  exports: 'M12 4v11M7 10l5 5 5-5M5 20h14',
  backups: 'M4 7h16v13H4zM3 4h18v3H3zM10 11h4',
  maintenance: 'M14.7 6.3a4 4 0 00-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 005.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z',
  health: 'M12 20s-7-4.4-7-10a4 4 0 017-2.6A4 4 0 0119 10c0 5.6-7 10-7 10z',
  sinks: 'M4 12h12M12 6l6 6-6 6M20 4v16',
  logs: 'M5 6h14M5 10h14M5 14h10M5 18h8',
  settings: 'M12 9a3 3 0 100 6 3 3 0 000-6zM12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1',
  search: 'M11 4a7 7 0 100 14 7 7 0 000-14zM20 20l-4-4',
  x: 'M6 6l12 12M18 6L6 18',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  alert: 'M12 8v5M12 16.5h.01M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z',
  info: 'M12 3a9 9 0 100 18 9 9 0 000-18zM12 11v6M12 7.5h.01',
  error: 'M12 3a9 9 0 100 18 9 9 0 000-18zM9 9l6 6M15 9l-6 6',
  okCircle: 'M12 3a9 9 0 100 18 9 9 0 000-18zM8 12.5l3 3 5-6',
  circle: 'M12 4a8 8 0 100 16 8 8 0 000-16z',
  halfCircle: 'M12 4a8 8 0 100 16 8 8 0 000-16zM12 4v16',
  clock: 'M12 3a9 9 0 100 18 9 9 0 000-18zM12 7v5l3 2',
  pause: 'M8 5v14M16 5v14',
  stop: 'M6 6h12v12H6z',
  chevronDown: 'M6 9l6 6 6-6',
  chevronRight: 'M9 6l6 6-6 6',
  chevronLeft: 'M15 6l-6 6 6 6',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  refresh: 'M20 11a8 8 0 10-2.3 5.7M20 5v6h-6',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  lock: 'M6 11h12v9H6zM8 11V8a4 4 0 018 0v3',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 100 6 3 3 0 000-6z',
  eyeOff: 'M3 3l18 18M10.6 6.1A10 10 0 0112 6c6 0 10 6 10 6a17 17 0 01-3.2 3.8M6.6 6.6C3.8 8.4 2 12 2 12s4 7 10 7a9.5 9.5 0 004.5-1.1M9.9 9.9a3 3 0 004.2 4.2',
  sun: 'M12 8a4 4 0 100 8 4 4 0 000-8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  moon: 'M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z',
  monitor: 'M3 4h18v12H3zM8 20h8M12 16v4',
  columns: 'M4 4h16v16H4zM10 4v16M15 4v16',
  filter: 'M4 5h16l-6 7v6l-4 2v-8z',
  sortUp: 'M12 5v14M6 11l6-6 6 6',
  sortDown: 'M12 5v14M6 13l6 6 6-6',
  sortBoth: 'M8 9l4-4 4 4M8 15l4 4 4-4',
  plus: 'M12 5v14M5 12h14',
  external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
  keyboard: 'M2 6h20v12H2zM6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10',
  signOut: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10',
  classic: 'M4 5h16v14H4zM9 5v14',
  menu: 'M4 6h16M4 12h16M4 18h16',
  rail: 'M4 4h16v16H4zM9 4v16M14 10l-2 2 2 2',
  globe: 'M12 3a9 9 0 100 18 9 9 0 000-18zM3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18',
  density: 'M4 6h16M4 10h16M4 14h16M4 18h16',
  terminal: 'M4 5h16v14H4zM8 10l3 2-3 2M13 15h3',
  link: 'M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1',
  planned: 'M12 3a9 9 0 100 18 9 9 0 000-18zM12 7v5h4',
  help: 'M12 3a9 9 0 100 18 9 9 0 000-18zM9.6 9.4a2.5 2.5 0 114.1 2c-.9.6-1.7 1.1-1.7 2.4M12 17h.01',
} as const

export type UiIconName = keyof typeof ICONS
</script>

<template>
  <svg
    :width="size ?? 18"
    :height="size ?? 18"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
    class="shrink-0"
  >
    <path :d="ICONS[name]" />
  </svg>
</template>
