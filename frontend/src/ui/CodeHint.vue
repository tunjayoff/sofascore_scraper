<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import CopyButton from '@/ui/CopyButton.vue'

/** A CLI command in mono with a copy button (4.7): what the web UI leaves to the server's command line. */
defineProps<{ command: string }>()
const { t } = useI18n()
</script>

<template>
  <span class="u-codehint">
    <code class="u-mono">{{ command }}</code>
    <CopyButton :text="command" :label="t('ui.common.copyCommand')" />
  </span>
</template>

<style>
.u-codehint {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  max-width: 100%;
  padding: 0 0 0 var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--r-control);
  background: var(--surface-2);
}
.u-codehint code {
  overflow-wrap: anywhere;
}
</style>
