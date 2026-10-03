<script setup lang="ts">
import { computed, onMounted, ref, useId, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import ErrorState from '@/ui/ErrorState.vue'
import { v1 } from '@/api/v1/client'
import type { FollowRecord, Sport } from '@/api/v1/schema'
import { sportName } from '@/app/sports'
import { sliceLabel } from '@/screens/events/eventText'

/**
 * Data selection of a follow (6.3 step 3, decision 4): "use the defaults" or "choose", then a grouped
 * checklist with the request cost per match; odds as their own group, off by default, with the note on
 * their history. What the API supports today decides what can be changed: the follows API takes no data
 * selection yet (P27) and the registry has no groups, odds or season data yet (P27, P28), so the picker
 * shows the defaults of the server read-only and says so. Every slice the registry lists for the sport
 * is shown with its default; required ones are marked "always".
 */
const props = defineProps<{ sport: string | null | undefined; selection: FollowRecord['slices'] | null }>()
const { t } = useI18n()
const uid = useId()

const registry = ref<Sport | null>(null)
const loading = ref(false)
const error = ref<unknown>(null)
const defaults = ref<string | null>(null)
const provider = ref<string | null>(null)

/** The odds markets of 6.3; none is in the registry yet, so they are listed off and disabled. */
const ODDS = ['odds_all', 'odds_featured', 'odds_changes', 'odds_winning'] as const

async function load() {
  if (!props.sport) {
    registry.value = null
    return
  }
  loading.value = true
  error.value = null
  try {
    registry.value = await v1.sport(props.sport)
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

const eventSlices = computed(() => (registry.value?.slices ?? []).filter((s) => s.path.startsWith('/event/')))
const otherSlices = computed(() => (registry.value?.slices ?? []).filter((s) => !s.path.startsWith('/event/')))
/** The event itself plus every selected event slice (6.3, "requests per match"). */
const cost = computed(() => 1 + eventSlices.value.filter((s) => s.default_enabled).length)

watch(() => props.sport, () => void load(), { immediate: true })
onMounted(() => {
  v1.settings()
    .then((doc) => {
      const d = doc.settings.find((s) => s.key === 'defaults.slices')?.value
      defaults.value = Array.isArray(d) ? d.join(', ') : d != null ? String(d) : null
      const p = doc.settings.find((s) => s.key === 'client.odds_provider')?.value
      provider.value = p != null ? String(p) : null
    })
    .catch(() => {})
})
</script>

<template>
  <div class="flex flex-col gap-4" data-testid="slice-picker">
    <fieldset class="flex flex-col gap-2">
      <legend class="u-sr">{{ t('ui.slicePicker.mode') }}</legend>
      <label class="u-option">
        <input type="radio" :name="`${uid}-mode`" class="u-check" :checked="!selection" disabled />
        <span class="flex flex-col">
          <span class="font-semibold">{{ t('ui.slicePicker.defaults', { sport: sportName(registry?.slug ?? props.sport) }) }}</span>
          <span v-if="defaults" class="u-small u-muted">{{ t('ui.slicePicker.defaultsAre', { groups: defaults }) }}</span>
        </span>
      </label>
      <label class="u-option">
        <input type="radio" :name="`${uid}-mode`" class="u-check" :checked="!!selection" disabled />
        <span class="flex flex-col">
          <span class="font-semibold">{{ t('ui.slicePicker.choose') }}</span>
          <span class="u-small u-muted">{{ selection ? t('ui.slicePicker.customSet') : t('ui.slicePicker.chooseLater') }}</span>
        </span>
      </label>
    </fieldset>
    <p class="m-0 u-notice" data-testid="slice-note"><UiIcon name="info" :size="16" /><span>{{ t('ui.slicePicker.readOnly') }}</span></p>

    <div v-if="!props.sport" class="u-muted">{{ t('ui.slicePicker.noSport') }}</div>
    <SkeletonBlock v-else-if="loading" :lines="5" />
    <ErrorState v-else-if="error" compact :error="error" @retry="load" />
    <template v-else-if="registry">
      <section class="flex flex-col gap-2" :aria-labelledby="`${uid}-match`">
        <header class="flex items-baseline gap-3">
          <h3 :id="`${uid}-match`" class="u-caption flex-1">{{ t('ui.slicePicker.group.match') }}</h3>
          <span class="u-small u-muted">{{ t('ui.slicePicker.perMatch') }}</span>
        </header>
        <label class="flex items-center gap-3">
          <input type="checkbox" class="u-check" checked disabled />
          <span class="flex-1">{{ t('ui.slicePicker.match') }}</span>
          <span class="u-small u-muted">{{ t('ui.slicePicker.always') }}</span>
        </label>
        <label v-for="s in eventSlices" :key="s.key" class="flex items-center gap-3" :data-slice="s.key">
          <input type="checkbox" class="u-check" :checked="s.default_enabled" disabled />
          <span class="flex-1">{{ sliceLabel(s.key) }}</span>
          <span v-if="s.required" class="u-small u-muted">{{ t('ui.slicePicker.always') }}</span>
        </label>
      </section>

      <section class="flex flex-col gap-2" :aria-labelledby="`${uid}-odds`" data-testid="odds-group">
        <header class="flex items-baseline gap-3">
          <h3 :id="`${uid}-odds`" class="u-caption flex-1">{{ t('ui.slicePicker.group.odds') }}</h3>
          <span class="u-small u-muted">{{ t('ui.slicePicker.offByDefault') }}</span>
        </header>
        <label v-for="o in ODDS" :key="o" class="flex items-center gap-3">
          <input type="checkbox" class="u-check" disabled />
          <span class="flex-1 u-muted">{{ sliceLabel(o) }}</span>
        </label>
        <p class="m-0 u-small u-muted flex gap-2"><UiIcon name="info" :size="14" />{{ t('ui.slicePicker.oddsHistory') }}</p>
        <p class="m-0 u-small u-muted">
          {{ t('ui.slicePicker.provider', { n: provider ?? '—' }) }} <RouterLink to="/settings">{{ t('ui.nav.settings') }}</RouterLink>
        </p>
        <p class="m-0 u-small u-muted">{{ t('ui.slicePicker.oddsLater') }}</p>
      </section>

      <section v-if="otherSlices.length" class="flex flex-col gap-2" :aria-labelledby="`${uid}-other`">
        <h3 :id="`${uid}-other`" class="u-caption">{{ t('ui.slicePicker.group.other') }}</h3>
        <label v-for="s in otherSlices" :key="s.key" class="flex items-center gap-3">
          <input type="checkbox" class="u-check" :checked="s.default_enabled" disabled />
          <span class="flex-1">{{ sliceLabel(s.key) }}</span>
        </label>
      </section>

      <p class="m-0 font-semibold" data-testid="slice-cost">{{ t('ui.slicePicker.cost', { n: cost }) }}</p>
    </template>
  </div>
</template>
