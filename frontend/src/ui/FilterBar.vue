<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import SidePanel from '@/ui/SidePanel.vue'
import { PHONE, useMedia } from '@/ui/media'

/**
 * Filter controls above a table (4.7) with the count of active filters and "Clear filters". On phone the
 * controls move into a side panel and the active filters stay visible as removable chips. The screen keeps
 * the filters in the query string. The control `/` should focus carries `data-filter-focus`.
 */
defineProps<{ activeCount: number; chips: { key: string; label: string }[] }>()
const emit = defineEmits<{ clear: []; remove: [key: string] }>()
const { t } = useI18n()
const phone = useMedia(PHONE)
const panel = ref(false)
</script>

<template>
  <div class="u-filterbar" role="group" :aria-label="t('ui.filter.label')">
    <template v-if="!phone">
      <div class="u-filter-controls"><slot /></div>
    </template>
    <button v-else type="button" class="u-btn u-btn-sm" data-filter-focus @click="panel = true">
      <UiIcon name="filter" :size="14" />{{ t('ui.filter.open') }}<span v-if="activeCount" class="u-small">({{ activeCount }})</span>
    </button>
    <div v-if="phone && chips.length" class="flex flex-wrap gap-2">
      <button v-for="c in chips" :key="c.key" type="button" class="u-chip" :aria-label="t('ui.filter.remove', { name: c.label })" @click="emit('remove', c.key)">
        {{ c.label }}<UiIcon name="x" :size="12" />
      </button>
    </div>
    <div v-if="activeCount" class="flex items-center gap-2 u-small u-muted" :class="{ 'ml-auto': !phone }">
      <span aria-live="polite">{{ t('ui.filter.active', { n: activeCount }) }}</span>
      <button type="button" class="u-btn u-btn-ghost u-btn-sm" @click="emit('clear')">{{ t('ui.filter.clear') }}</button>
    </div>
    <SidePanel v-if="panel" :title="t('ui.filter.title')" @close="panel = false">
      <div class="flex flex-col gap-4"><slot /></div>
    </SidePanel>
  </div>
</template>

<style>
.u-filterbar {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--sp-4);
  margin-bottom: var(--sp-4);
}
.u-filter-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--sp-4);
}
.u-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  min-height: 32px;
  padding: 0 var(--sp-4);
  border: 1px solid var(--border);
  border-radius: var(--r-pill);
  background: var(--accent-soft);
  color: var(--text);
  font: inherit;
  font-size: 0.8125rem;
  cursor: pointer;
}
@media (max-width: 1023px) {
  .u-chip {
    min-height: 44px;
  }
}
</style>
