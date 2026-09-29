<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useSportStore, type SportFilter } from '@/stores/sport'
import { useLeaguesStore } from '@/stores/leagues'
import { SPORTS } from '@/lib/sport'

/** Sidebar switch: the sport every page is showing. */
const { t } = useI18n()
const sport = useSportStore()
const leagues = useLeaguesStore()
const options: SportFilter[] = ['all', ...SPORTS]
const label = (s: SportFilter) => (s === 'all' ? t('common.all') : t(`sport.${s}`))
</script>

<template>
  <div role="radiogroup" :aria-label="t('sport.label')" class="sport-switch">
    <button
      v-for="s in options"
      :key="s"
      type="button"
      role="radio"
      :aria-checked="sport.current === s"
      :class="{ 'is-active': sport.current === s }"
      @click="sport.set(s)"
    >
      <span>{{ label(s) }}</span>
      <span class="mono count">{{ leagues.countBySport[s] || 0 }}</span>
    </button>
  </div>
</template>

<style scoped>
.sport-switch {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px;
  padding: 4px;
  background: var(--surface-3);
  border-radius: 10px;
}
@media (max-width: 767px) {
  .sport-switch {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
.sport-switch button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  min-height: 36px;
  padding: 0 10px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--muted);
  font: 600 13px Geist, sans-serif;
  cursor: pointer;
}
.sport-switch button.is-active {
  background: var(--surface);
  color: var(--text);
  box-shadow: 0 1px 3px rgba(23, 25, 30, 0.12);
}
.count {
  font-size: 11px;
  font-weight: 500;
  color: var(--faint);
}
</style>
