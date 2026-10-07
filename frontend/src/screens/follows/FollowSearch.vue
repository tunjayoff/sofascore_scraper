<script setup lang="ts">
import { computed, nextTick, ref, toRef, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import UiBadge from '@/ui/UiBadge.vue'
import FormError from '@/ui/FormError.vue'
import type { TournamentHit } from '@/api/v1/schema'
import { sportName } from '@/app/sports'
import { MIN_CHARS, normalize, useSuggest, type Suggestion } from '@/app/suggest'
import { hitPlace, kindIcon, playerTeam } from './followText'

/**
 * The follow editor's search (6.3 step 1; FX-20): a combobox that suggests while typing, like the site.
 * Follows and stored names show at once, SofaScore's leagues, teams and players after a short pause
 * (`useSuggest`), all in one list grouped by kind with sport, country and "already added". Arrow keys move,
 * Enter picks (or, with nothing chosen, searches at once), Esc closes the list. Picking emits `pick`; the
 * editor fills step 1 with it. `search(q)` runs the search at once (the quick search's `?q=`).
 */
const props = defineProps<{ sport?: string | null; picked?: string | null }>()
const emit = defineEmits<{ pick: [hit: TournamentHit]; searched: [hits: TournamentHit[]] }>()
const query = defineModel<string>({ default: '' })
const { t } = useI18n()
const uid = useId()
const listId = `${uid}-list`

const s = useSuggest(query, { sport: toRef(() => props.sport ?? null) })
const open = ref(false)
const active = ref(-1)
const input = ref<HTMLInputElement | null>(null)

const expanded = computed(() => open.value && s.groups.value.length > 0)
const optionId = (item: Suggestion) => `${uid}-opt-${item.key.replace(':', '-')}`
const activeItem = computed(() => (active.value >= 0 ? s.flat.value[active.value] : undefined))
const text = computed(() => normalize(query.value))

/** What the line under the field says (also read out: aria-live). */
const status = computed(() => {
  if (!text.value) return ''
  if (text.value.length < MIN_CHARS && !/^\d+$/.test(text.value)) return t('ui.suggest.typeMore', { n: MIN_CHARS })
  if (s.error.value) return ''
  if (s.pending.value) return t('ui.suggest.searching')
  if (s.current.value && !s.items.value.length) return t('ui.suggest.none')
  if (s.current.value) return t('ui.suggest.count', { n: s.items.value.length })
  return ''
})

watch(query, () => {
  open.value = true
  active.value = -1
})
// a list that changed under the cursor keeps the cursor inside it
watch(
  () => s.flat.value.length,
  (n) => {
    if (active.value >= n) active.value = n - 1
  },
)

function pick(item: Suggestion | undefined) {
  if (!item) return
  open.value = false
  active.value = -1
  emit('pick', { ...item.hit, followed: item.followed })
}

/** Search at once, without the pause; one hit not added yet is picked (the quick search's FX-14a rule). */
async function search() {
  open.value = true
  active.value = -1
  const hits = await s.searchNow()
  if (!hits) return
  emit('searched', hits)
  if (hits.length !== 1) return
  const one = s.flat.value.find((x) => x.key === `${hits[0].kind ?? 'tournament'}:${hits[0].id}`)
  if (one && !one.followed) pick(one)
}
defineExpose({ search, focus: () => input.value?.focus() })

function move(step: number) {
  const n = s.flat.value.length
  if (!n) return
  open.value = true
  const next = active.value + step
  active.value = next < 0 ? -1 : Math.min(n - 1, next)
  void nextTick(() => document.getElementById(activeItem.value ? optionId(activeItem.value) : '')?.scrollIntoView?.({ block: 'nearest' }))
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    move(1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    move(-1)
  } else if (e.key === 'Enter' && expanded.value && activeItem.value) {
    e.preventDefault()
    pick(activeItem.value)
  } else if (e.key === 'Escape' && expanded.value) {
    e.preventDefault()
    e.stopPropagation()
    open.value = false
    active.value = -1
  } else if (e.key === 'Tab') open.value = false
}

function onFocusout(e: FocusEvent) {
  const to = e.relatedTarget as Node | null
  if (!to || !(e.currentTarget as HTMLElement).contains(to)) open.value = false
}

const sourceText = (item: Suggestion) => (item.source === 'catalog' ? t('ui.suggest.stored') : '')
</script>

<template>
  <div class="u-suggest flex flex-col gap-2" @focusout="onFocusout">
    <form class="flex flex-wrap items-end gap-3" role="search" @submit.prevent="search">
      <label class="flex flex-col flex-1 min-w-[220px]">
        <span class="u-label">{{ t('ui.suggest.label') }}</span>
        <input
          ref="input"
          v-model="query"
          type="search"
          class="u-field"
          role="combobox"
          autocomplete="off"
          spellcheck="false"
          aria-autocomplete="list"
          :aria-expanded="expanded"
          :aria-controls="listId"
          :aria-activedescendant="expanded && activeItem ? optionId(activeItem) : undefined"
          :aria-describedby="`${uid}-status`"
          :placeholder="t('ui.suggest.placeholder')"
          data-testid="editor-query"
          @keydown="onKeydown"
          @focus="open = true"
        />
      </label>
      <button type="submit" class="u-btn" :disabled="s.loading.value || text.length < MIN_CHARS" data-testid="editor-search">
        <span v-if="s.loading.value" class="u-spinner" aria-hidden="true"></span><UiIcon v-else name="search" :size="16" />{{ t('ui.followEditor.search') }}
      </button>
    </form>
    <p :id="`${uid}-status`" class="m-0 u-small u-muted flex items-center gap-2 min-h-[1.25rem]" aria-live="polite" data-testid="suggest-status">
      <span v-if="s.pending.value" class="u-spinner" aria-hidden="true" style="width: 12px; height: 12px"></span>{{ status }}
    </p>
    <ul
      v-show="expanded"
      :id="listId"
      role="listbox"
      class="u-suggest-list"
      :aria-label="t('ui.suggest.results')"
      :aria-busy="s.pending.value"
      data-testid="editor-hits"
    >
      <li v-for="g in s.groups.value" :key="g.kind" role="none">
        <div role="group" :aria-labelledby="`${uid}-g-${g.kind}`" :data-group="g.kind">
        <div :id="`${uid}-g-${g.kind}`" role="presentation" class="u-suggest-group">{{ t(`ui.suggest.group.${g.kind}`) }}</div>
        <ul role="none" class="m-0 p-0 list-none">
          <li
            v-for="item in g.items"
            :id="optionId(item)"
            :key="item.key"
            role="option"
            class="u-suggest-option"
            :aria-selected="activeItem?.key === item.key"
            :data-hit="item.key"
            :data-source="item.source"
            :data-picked="picked === item.key || undefined"
            @mousedown.prevent
            @mousemove="active = s.flat.value.indexOf(item)"
            @click="pick(item)"
          >
            <UiIcon :name="kindIcon(item.group)" :size="16" />
            <span class="flex-1 min-w-0 flex flex-wrap items-baseline gap-x-2">
              <span class="font-semibold">{{ item.hit.name }}</span>
              <span v-if="item.hit.sport" class="u-small u-muted">{{ sportName(item.hit.sport) }}</span>
              <span v-if="hitPlace(item.hit)" class="u-small u-muted">{{ hitPlace(item.hit) }}</span>
              <span v-if="playerTeam(item.hit.team)" class="u-small u-muted" data-testid="hit-team">{{ t('ui.followEditor.playsFor', { team: playerTeam(item.hit.team) }) }}</span>
              <span v-if="item.twin" class="u-small u-muted u-mono" :title="t('ui.suggest.sameName')" data-testid="hit-number">{{ t('ui.suggest.number', { id: item.hit.id }) }}</span>
              <span v-if="sourceText(item)" class="u-small u-muted">· {{ sourceText(item) }}</span>
            </span>
            <UiBadge v-if="item.followed" tone="ok" icon="check">{{ t('ui.followEditor.alreadyFollowed') }}</UiBadge>
          </li>
        </ul>
        </div>
      </li>
    </ul>
    <FormError v-if="s.error.value" :error="s.error.value" />
    <p class="m-0 u-small u-muted flex items-center gap-2"><UiIcon name="external" :size="14" />{{ t('ui.suggest.note') }}</p>
  </div>
</template>

<style>
.u-suggest-list {
  margin: 0;
  padding: var(--sp-2);
  list-style: none;
  max-height: min(60vh, 420px);
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: var(--r-control);
  background: var(--surface);
}
.u-suggest-group {
  padding: var(--sp-3) var(--sp-3) var(--sp-2);
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--muted);
}
.u-suggest-option {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  min-height: 44px;
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-control);
  cursor: pointer;
}
.u-suggest-option[aria-selected='true'] {
  background: var(--accent-soft);
}
.u-suggest-option[data-picked] {
  box-shadow: inset 0 0 0 1px var(--accent);
}
</style>
