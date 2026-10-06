<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import HelpTip from '@/ui/HelpTip.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import ErrorState from '@/ui/ErrorState.vue'
import { v1 } from '@/api/v1/client'
import type { FollowRecord, Sport, SportSlice } from '@/api/v1/schema'
import { sportName } from '@/app/sports'
import { sliceLabel } from '@/screens/events/eventText'

/**
 * The data a follow downloads (6.3 step 3, decision 4), in one readable line (FX-14a): the data types
 * fetched for each match, whether betting odds are on, and the request cost per match; "Details" opens
 * the grouped list (match data, betting odds once, season and other data). The registry of
 * `GET /sports/{slug}` says what the configured defaults select (`selected`); a follow with its own
 * selection is resolved against it. It is read-only: choosing per follow comes with FX-14b.
 */
const props = defineProps<{ sport: string | null | undefined; selection: FollowRecord['slices'] | null }>()
const { t } = useI18n()
const uid = useId()

const registry = ref<Sport | null>(null)
const loading = ref(false)
const error = ref<unknown>(null)
const open = ref(false)

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
watch(() => props.sport, () => void load(), { immediate: true })

/** Older servers give no group or owner: odds are told by their key, the rest by their path. */
function groupOf(s: SportSlice): string {
  return s.group ?? (s.key.startsWith('odds') || s.key.endsWith('_odds') ? 'odds' : 'core')
}
function ownerOf(s: SportSlice): string {
  return s.owner ?? (s.path.startsWith('/event/') ? 'event' : 'season')
}

/** Whether a slice is downloaded: the configured defaults, or the follow's own selection over them. */
function chosen(s: SportSlice): boolean {
  const base = s.selected ?? s.default_enabled
  const sel = props.selection as { include?: string[]; enable?: string[]; disable?: string[] } | null
  if (!sel) return base
  const names = (list?: string[]) => !!list?.some((x) => x === s.key || x === groupOf(s))
  if (Array.isArray(sel.include)) return s.required || names(sel.include)
  return (base || names(sel.enable)) && !names(sel.disable)
}

const slices = computed(() => registry.value?.slices ?? [])
const matchSlices = computed(() => slices.value.filter((s) => ownerOf(s) === 'event' && groupOf(s) !== 'odds'))
const oddsSlices = computed(() => slices.value.filter((s) => groupOf(s) === 'odds'))
const otherSlices = computed(() => slices.value.filter((s) => ownerOf(s) !== 'event' && groupOf(s) !== 'odds'))
const oddsOn = computed(() => oddsSlices.value.some(chosen))
/** "Match, Statistics, Line-ups, …": the match itself and every chosen data type of a match. */
const summary = computed(() => [t('ui.slice.event'), ...slices.value.filter((s) => ownerOf(s) === 'event' && groupOf(s) !== 'odds' && chosen(s)).map((s) => sliceLabel(s.key))].join(', '))
/** The event itself plus every chosen data type of a match (6.3, "requests per match"). */
const cost = computed(() => 1 + slices.value.filter((s) => ownerOf(s) === 'event' && chosen(s)).length)
</script>

<template>
  <div class="flex flex-col gap-3" data-testid="slice-picker">
    <div class="flex items-center gap-1">
      <h3 class="u-h3">{{ t('ui.slicePicker.mode') }}</h3>
      <HelpTip term="dataType" />
    </div>
    <div v-if="!props.sport" class="u-muted">{{ t('ui.slicePicker.noSport') }}</div>
    <SkeletonBlock v-else-if="loading" :lines="2" />
    <ErrorState v-else-if="error" compact :error="error" @retry="load" />
    <template v-else-if="registry">
      <p class="m-0" data-testid="slice-summary">
        <template v-if="selection">{{ t('ui.slicePicker.summaryCustom') }} </template>{{ t('ui.slicePicker.summary', { list: summary }) }}
        <span :class="oddsOn ? '' : 'u-muted'">{{ oddsOn ? t('ui.slicePicker.oddsOn') : t('ui.slicePicker.oddsOff') }}</span>
      </p>
      <p class="m-0 u-small u-muted" data-testid="slice-cost">{{ t('ui.slicePicker.cost', { n: cost }) }} · {{ sportName(registry.slug) }}</p>
      <div>
        <button type="button" class="u-btn u-btn-sm u-btn-ghost" :aria-expanded="open" :aria-controls="`${uid}-details`" data-testid="slice-details" @click="open = !open">
          <UiIcon :name="open ? 'chevronDown' : 'chevronRight'" :size="14" />{{ t('ui.slicePicker.details') }}
        </button>
      </div>
      <div v-if="open" :id="`${uid}-details`" class="flex flex-col gap-4">
        <section class="flex flex-col gap-2" :aria-labelledby="`${uid}-match`">
          <header class="flex items-baseline gap-3">
            <h4 :id="`${uid}-match`" class="u-caption flex-1">{{ t('ui.slicePicker.group.match') }}</h4>
            <span class="u-small u-muted">{{ t('ui.slicePicker.perMatch') }}</span>
          </header>
          <label class="flex items-center gap-3">
            <input type="checkbox" class="u-check" checked disabled />
            <span class="flex-1">{{ t('ui.slicePicker.match') }}</span>
            <span class="u-small u-muted">{{ t('ui.slicePicker.always') }}</span>
          </label>
          <label v-for="s in matchSlices" :key="s.key" class="flex items-center gap-3" :data-slice="s.key">
            <input type="checkbox" class="u-check" :checked="chosen(s)" disabled />
            <span class="flex-1">{{ sliceLabel(s.key) }}</span>
            <span v-if="s.required" class="u-small u-muted">{{ t('ui.slicePicker.always') }}</span>
          </label>
        </section>

        <section v-if="oddsSlices.length" class="flex flex-col gap-2" :aria-labelledby="`${uid}-odds`" data-testid="odds-group">
          <header class="flex items-baseline gap-3">
            <h4 :id="`${uid}-odds`" class="u-caption flex-1">{{ t('ui.slicePicker.group.odds') }}</h4>
            <span class="u-small u-muted">{{ t('ui.slicePicker.offByDefault') }}</span>
          </header>
          <label v-for="s in oddsSlices" :key="s.key" class="flex items-center gap-3" :data-slice="s.key">
            <input type="checkbox" class="u-check" :checked="chosen(s)" disabled />
            <span class="flex-1">{{ sliceLabel(s.key) }}</span>
          </label>
          <p class="m-0 u-small u-muted flex gap-2"><UiIcon name="info" :size="14" />{{ t('ui.slicePicker.oddsHistory') }}</p>
        </section>

        <section v-if="otherSlices.length" class="flex flex-col gap-2" :aria-labelledby="`${uid}-other`">
          <h4 :id="`${uid}-other`" class="u-caption">{{ t('ui.slicePicker.group.other') }}</h4>
          <label v-for="s in otherSlices" :key="s.key" class="flex items-center gap-3" :data-slice="s.key">
            <input type="checkbox" class="u-check" :checked="chosen(s)" disabled />
            <span class="flex-1">{{ sliceLabel(s.key) }}</span>
          </label>
        </section>
      </div>
      <p class="m-0 u-notice" data-testid="slice-note">
        <UiIcon name="info" :size="16" /><span>{{ t('ui.slicePicker.soon') }} {{ t('ui.slicePicker.defaultsWhere') }} <RouterLink :to="{ path: '/settings', query: { tab: 'data' } }">{{ t('ui.nav.settings') }}</RouterLink></span>
      </p>
    </template>
  </div>
</template>
