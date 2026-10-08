<script setup lang="ts">
import { computed, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import UiBadge from '@/ui/UiBadge.vue'
import UiIcon from '@/ui/UiIcon.vue'
import type { SportSlice } from '@/api/v1/schema'
import { sliceLabel } from '@/screens/events/eventText'
import { SECTIONS, sectionOf, type SectionKey } from './sliceSelection'

/**
 * The data types of a sport as a grouped checklist (6.3 step 3, 6.16 Data; FX-14b): match data (fetched
 * once per match, the match itself always), betting odds (off by default, said in the header), then the
 * data of a season, a team, a player or the sport (fetched once per owner). A data type SofaScore does not
 * always have for the sport says so (`allSports`: the defaults of every sport, "not available in every sport";
 * FX-26). `disabled` shows the ticks read-only (the defaults, or a locked value).
 */
const props = defineProps<{ slices: readonly SportSlice[]; chosen: ReadonlySet<string>; disabled?: boolean; label: string; allSports?: boolean }>()
const emit = defineEmits<{ toggle: [key: string, on: boolean] }>()
const { t } = useI18n()
const uid = useId()

const sections = computed(() =>
  SECTIONS.map((key) => ({ key, slices: props.slices.filter((s) => sectionOf(s) === key) })).filter((x) => x.key === 'match' || x.slices.length),
)
const oddsOn = computed(() => props.slices.some((s) => sectionOf(s) === 'odds' && props.chosen.has(s.key)))

function hint(key: SectionKey): string {
  if (key === 'match') return t('ui.slicePicker.perMatch')
  if (key === 'odds') return t('ui.slicePicker.offByDefault')
  return t(`ui.slicePicker.per.${key}`)
}
</script>

<template>
  <div class="flex flex-col gap-4" role="group" :aria-label="label" data-testid="slice-checklist">
    <section
      v-for="sec in sections"
      :key="sec.key"
      class="flex flex-col gap-2"
      :aria-labelledby="`${uid}-${sec.key}`"
      :data-section="sec.key"
      :data-testid="sec.key === 'odds' ? 'odds-group' : undefined"
    >
      <header class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h4 :id="`${uid}-${sec.key}`" class="u-caption flex-1">{{ t(`ui.slicePicker.group.${sec.key}`) }}</h4>
        <UiBadge v-if="sec.key === 'odds'" :tone="oddsOn ? 'info' : 'neutral'" data-testid="odds-state">{{ oddsOn ? t('ui.slicePicker.oddsOnShort') : t('ui.slicePicker.offByDefault') }}</UiBadge>
        <span v-else class="u-small u-muted">{{ hint(sec.key) }}</span>
      </header>
      <label v-if="sec.key === 'match'" class="flex items-center gap-3">
        <input type="checkbox" class="u-check" checked disabled />
        <span class="flex-1">{{ t('ui.slicePicker.match') }}</span>
        <span class="u-small u-muted">{{ t('ui.slicePicker.always') }}</span>
      </label>
      <label v-for="s in sec.slices" :key="s.key" class="flex items-center gap-3" :data-slice="s.key">
        <input
          type="checkbox"
          class="u-check"
          :checked="chosen.has(s.key)"
          :disabled="disabled"
          @change="emit('toggle', s.key, ($event.target as HTMLInputElement).checked)"
        />
        <span class="flex-1">{{ sliceLabel(s.key) }}</span>
        <span v-if="!s.required && sec.key === 'match'" class="u-small u-muted">{{ allSports ? t('ui.slicePicker.optionalAny') : t('ui.slicePicker.optional') }}</span>
      </label>
      <p v-if="sec.key === 'odds'" class="m-0 u-small u-muted flex gap-2"><UiIcon name="info" :size="14" />{{ t('ui.slicePicker.oddsHistory') }}</p>
    </section>
  </div>
</template>
