<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import HelpTip from '@/ui/HelpTip.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import ErrorState from '@/ui/ErrorState.vue'
import { v1 } from '@/api/v1/client'
import type { Sport } from '@/api/v1/schema'
import { sportName } from '@/app/sports'
import { sliceLabel } from '@/screens/events/eventText'
import SliceChecklist from './SliceChecklist.vue'
import { costPerMatch, followChosen, ownerOf, sectionOf, type Selection } from './sliceSelection'

/**
 * The data a follow downloads (6.3 step 3, decision 4; FX-14b). One readable line says what is fetched for
 * each match, whether betting odds are on, and the request cost per match. Editable (`editable`), the
 * follow chooses between "Use the defaults for <sport>" (`slices: null`) and "Choose for this follow"
 * (`{include: [...]}`, P27), and the grouped checklist of `GET /sports/{slug}` starts from the defaults'
 * ticks (`selected`). Read-only, "Details" opens the same checklist without controls. A follow kept with
 * enable/disable changes (config file, `ssc follows add`) is shown resolved; choosing writes `include`.
 */
const props = defineProps<{ sport: string | null | undefined; selection: Selection | null; editable?: boolean; lockReason?: string | null }>()
const emit = defineEmits<{ 'update:selection': [Selection | null] }>()
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

const slices = computed(() => registry.value?.slices ?? [])
const custom = computed(() => props.selection != null)
const chosen = computed(() => followChosen(slices.value, props.selection))
const defaults = computed(() => followChosen(slices.value, null))
const oddsOn = computed(() => slices.value.some((s) => sectionOf(s) === 'odds' && chosen.value.has(s.key)))
/** "Match, Statistics, Line-ups, …": the match itself and every chosen data type of a match. */
const summary = computed(() => [t('ui.slice.event'), ...slices.value.filter((s) => sectionOf(s) === 'match' && chosen.value.has(s.key)).map((s) => sliceLabel(s.key))].join(', '))
const others = computed(() => slices.value.filter((s) => ownerOf(s) !== 'event' && chosen.value.has(s.key)).map((s) => sliceLabel(s.key)))
const cost = computed(() => costPerMatch(slices.value, chosen.value))
const canEdit = computed(() => !!props.editable && !props.lockReason)

function useDefaults() {
  emit('update:selection', null)
}
function choose() {
  // the checklist starts from what the defaults download now
  if (!custom.value) emit('update:selection', { include: slices.value.filter((s) => defaults.value.has(s.key)).map((s) => s.key) })
  open.value = true
}
function toggle(key: string, on: boolean) {
  const next = new Set(chosen.value)
  if (on) next.add(key)
  else next.delete(key)
  emit('update:selection', { include: slices.value.filter((s) => next.has(s.key)).map((s) => s.key) })
}
</script>

<template>
  <div class="flex flex-col gap-3" data-testid="slice-picker">
    <div class="flex items-center gap-1">
      <h3 class="u-h3">{{ t('ui.slicePicker.mode') }}</h3>
      <HelpTip term="dataType" />
    </div>
    <div v-if="!props.sport" class="u-muted" data-testid="slice-no-sport">{{ t('ui.slicePicker.noSport') }}</div>
    <SkeletonBlock v-else-if="loading" :lines="2" />
    <ErrorState v-else-if="error" compact :error="error" @retry="load" />
    <template v-else-if="registry">
      <fieldset v-if="editable" class="flex flex-col gap-2" :disabled="!canEdit" data-testid="slice-mode">
        <legend class="u-sr">{{ t('ui.slicePicker.mode') }}</legend>
        <label class="u-option">
          <input type="radio" class="u-check" :name="`${uid}-mode`" :checked="!custom" data-testid="slice-mode-defaults" @change="useDefaults" />
          <span class="flex flex-col"
            ><span class="font-semibold">{{ t('ui.slicePicker.useDefaults', { sport: sportName(registry.slug) }) }}</span
            ><span class="u-small u-muted">{{ t('ui.slicePicker.useDefaultsHint') }}</span></span
          >
        </label>
        <label class="u-option">
          <input type="radio" class="u-check" :name="`${uid}-mode`" :checked="custom" data-testid="slice-mode-choose" @change="choose" />
          <span class="flex flex-col"
            ><span class="font-semibold">{{ t('ui.slicePicker.choose') }}</span><span class="u-small u-muted">{{ t('ui.slicePicker.chooseHint') }}</span></span
          >
        </label>
      </fieldset>
      <p v-if="lockReason" class="m-0 u-small u-muted flex items-center gap-2" data-testid="slice-locked"><UiIcon name="lock" :size="14" />{{ lockReason }}</p>

      <p class="m-0" data-testid="slice-summary" aria-live="polite">
        <template v-if="custom && !editable">{{ t('ui.slicePicker.summaryCustom') }} </template>{{ t('ui.slicePicker.summary', { list: summary }) }}
        <span :class="oddsOn ? '' : 'u-muted'">{{ oddsOn ? t('ui.slicePicker.oddsOn') : t('ui.slicePicker.oddsOff') }}</span>
        <template v-if="others.length"> {{ t('ui.slicePicker.othersOn', { list: others.join(', ') }) }}</template>
      </p>
      <p class="m-0 u-small u-muted" data-testid="slice-cost">{{ t('ui.slicePicker.cost', { n: cost }) }} · {{ sportName(registry.slug) }}</p>

      <SliceChecklist v-if="editable && custom && canEdit" :slices="slices" :chosen="chosen" :label="t('ui.slicePicker.mode')" @toggle="toggle" />
      <template v-else>
        <div>
          <button type="button" class="u-btn u-btn-sm u-btn-ghost" :aria-expanded="open" :aria-controls="`${uid}-details`" data-testid="slice-details" @click="open = !open">
            <UiIcon :name="open ? 'chevronDown' : 'chevronRight'" :size="14" />{{ t('ui.slicePicker.details') }}
          </button>
        </div>
        <div v-if="open" :id="`${uid}-details`">
          <SliceChecklist :slices="slices" :chosen="chosen" disabled :label="t('ui.slicePicker.mode')" />
        </div>
      </template>

      <p class="m-0 u-small u-muted" data-testid="slice-note">
        <span v-if="!custom">{{ t('ui.slicePicker.defaultsWhere') }} <RouterLink :to="{ path: '/settings', query: { tab: 'data' } }">{{ t('ui.slicePicker.defaultsLink') }}</RouterLink></span>
      </p>
    </template>
  </div>
</template>
