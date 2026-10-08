<script setup lang="ts">
import { computed, onMounted, ref, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import UiBadge from '@/ui/UiBadge.vue'
import UiIcon from '@/ui/UiIcon.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import { v1 } from '@/api/v1/client'
import type { Setting, SettingsDocument, SportSliceSelection } from '@/api/v1/schema'
import { loadSports, sportName, sports } from '@/app/sports'
import SliceChecklist from '@/screens/follows/SliceChecklist.vue'
import { compress, costPerMatch, layerFor, resolveDefaults, union } from '@/screens/follows/sliceSelection'

/**
 * Settings › Data, the data to download (6.16, P27; FX-14b): the default data types of every league as a
 * checklist (`defaults.slices`, a list of keys and groups; a fully ticked group is written by its name),
 * and per sport its changes over them (`slices.<sport>` = {enable, disable}). Unset defaults are the
 * registry's own (`default_enabled`: betting odds and the season, team and player data are off). Locked
 * values (config file, environment) are shown with the reason and cannot be ticked. Changes are staged
 * with the other settings and saved by the same "Save changes". The sports of the follows come first, and
 * those with a saved change of their own; every other sport is under "Other sports", closed (FX-20).
 */
const props = defineProps<{ doc: SettingsDocument; staged: Record<string, unknown>; configFile?: string | null; problems: Record<string, string> }>()
const emit = defineEmits<{ stage: [key: string, value: unknown]; unstage: [key: string] }>()
const { t } = useI18n()
const uid = useId()

const KEY = 'defaults.slices'
const setting = computed<Setting | null>(() => props.doc.settings.find((s) => s.key === KEY) ?? null)
const all = computed(() => union(sports.value))

function lockText(s: { locked: boolean; writable: boolean; source: string; source_name: string }): string | null {
  if (s.locked) {
    if (s.source === 'file') return t('ui.settings.lock.file', { file: s.source_name || props.configFile || 'sofascore.toml' })
    if (s.source === 'env') return t('ui.settings.lock.env', { name: s.source_name })
    return t('ui.settings.lock.other')
  }
  return s.writable ? null : t('ui.settings.lock.readOnly')
}

/** The defaults in force with the unsaved change: null = the registry's (unset, or reset here). */
const base = computed<string[] | null>(() => {
  if (KEY in props.staged) return (props.staged[KEY] as string[] | null) ?? null
  const s = setting.value
  if (!s || s.source === 'default' || !Array.isArray(s.value)) return null
  return s.value.map(String)
})
const globalLock = computed(() => (setting.value ? lockText(setting.value) : t('ui.settings.lock.readOnly')))
const chosen = computed(() => resolveDefaults(all.value, base.value))

function toggleGlobal(key: string, on: boolean) {
  const next = new Set(chosen.value)
  if (on) next.add(key)
  else next.delete(key)
  const list = compress(all.value, next)
  const saved = setting.value && setting.value.source !== 'default' && Array.isArray(setting.value.value) ? setting.value.value.map(String) : null
  if (saved && JSON.stringify(saved) === JSON.stringify(list)) emit('unstage', KEY)
  else emit('stage', KEY, list)
}
function resetGlobal() {
  emit('stage', KEY, null)
}

// ---- per sport ----
type SportRow = { slug: string; row: SportSliceSelection | null; lock: string | null }
/** The sports of the follows (read once; a failure leaves every sport under "Other sports"). */
const followed = ref<Set<string>>(new Set())
const rows = computed<SportRow[]>(() =>
  sports.value.map((sp) => {
    const row = props.doc.slices?.find((r) => r.sport === sp.slug) ?? null
    return { slug: sp.slug, row, lock: row ? lockText(row) : t('ui.settings.lock.readOnly') }
  }),
)
function layerOf(r: SportRow): { enable: string[]; disable: string[] } {
  const key = `slices.${r.slug}`
  if (key in props.staged) return (props.staged[key] as { enable: string[]; disable: string[] } | null) ?? { enable: [], disable: [] }
  return { enable: r.row?.enable ?? [], disable: r.row?.disable ?? [] }
}
function slicesOf(slug: string) {
  return sports.value.find((s) => s.slug === slug)?.slices ?? []
}
function chosenOf(r: SportRow) {
  return resolveDefaults(slicesOf(r.slug), base.value, [layerOf(r)])
}
function changes(r: SportRow): number {
  const l = layerOf(r)
  return l.enable.length + l.disable.length
}
function toggleSport(r: SportRow, key: string, on: boolean) {
  const slices = slicesOf(r.slug)
  const wanted = chosenOf(r)
  if (on) wanted.add(key)
  else wanted.delete(key)
  const layer = layerFor(slices, resolveDefaults(slices, base.value), wanted)
  const name = `slices.${r.slug}`
  const empty = !layer.enable.length && !layer.disable.length
  const saved = { enable: r.row?.enable ?? [], disable: r.row?.disable ?? [] }
  if (JSON.stringify(layer) === JSON.stringify(saved)) emit('unstage', name)
  else if (empty) emit('stage', name, r.row?.source === 'overrides' ? null : layer)
  else emit('stage', name, layer)
}
function resetSport(r: SportRow) {
  emit('stage', `slices.${r.slug}`, null)
}
/** A sport shown first: followed, or with a saved change of its own (a change being made does not move it). */
const first = (r: SportRow) => followed.value.has(r.slug) || !!(r.row?.enable?.length || r.row?.disable?.length)
const mine = computed(() => rows.value.filter(first))
const others = computed(() => rows.value.filter((r) => !first(r)))
const groups = computed(() => [
  { key: 'mine', rows: mine.value },
  { key: 'others', rows: others.value },
])

onMounted(() => {
  void loadSports().catch(() => {})
  v1.follows()
    .then((r) => (followed.value = new Set(r.data.map((f) => f.sport).filter((s): s is string => !!s))))
    .catch(() => {})
})
</script>

<template>
  <section class="u-card p-6 mb-4 flex flex-col gap-5" :aria-labelledby="`${uid}-title`" data-testid="slice-defaults">
    <header class="flex flex-col gap-1">
      <h2 :id="`${uid}-title`" class="u-h3">{{ t('ui.sliceDefaults.title') }}</h2>
      <p class="m-0 u-small u-muted">{{ t('ui.sliceDefaults.text') }}</p>
    </header>
    <SkeletonBlock v-if="!sports.length" :lines="4" />
    <template v-else>
      <div class="flex flex-col gap-3" data-setting="defaults.slices">
        <div class="flex flex-wrap items-center gap-3">
          <h3 class="u-h3 flex-1">{{ t('ui.sliceDefaults.global') }}</h3>
          <UiBadge tone="neutral" data-testid="source">{{ base ? t(`ui.settings.source.${setting?.source ?? 'default'}`) : t('ui.sliceDefaults.builtIn') }}</UiBadge>
          <UiBadge v-if="KEY in staged" tone="info" icon="info">{{ staged[KEY] === null ? t('ui.settings.willReset') : t('ui.settings.changed') }}</UiBadge>
          <button v-if="KEY in staged" type="button" class="u-btn u-btn-ghost u-btn-sm" @click="emit('unstage', KEY)">{{ t('ui.settings.undo') }}</button>
          <button v-else-if="setting?.source === 'overrides' && !globalLock" type="button" class="u-btn u-btn-ghost u-btn-sm" data-testid="slice-defaults-reset" @click="resetGlobal">
            {{ t('ui.sliceDefaults.reset') }}
          </button>
        </div>
        <p v-if="globalLock" class="m-0 u-small u-muted flex items-center gap-2" data-testid="lock"><UiIcon name="lock" :size="13" />{{ globalLock }}</p>
        <SliceChecklist :slices="all" :chosen="chosen" :disabled="!!globalLock" :label="t('ui.sliceDefaults.global')" all-sports @toggle="toggleGlobal" />
        <p v-if="problems[KEY]" class="m-0 u-small" role="alert" style="color: var(--danger)">{{ problems[KEY] }}</p>
      </div>

      <div class="flex flex-col gap-2" data-testid="slice-sports">
        <h3 class="u-h3">{{ t('ui.sliceDefaults.perSport') }}</h3>
        <p class="m-0 u-small u-muted">{{ t('ui.sliceDefaults.perSportText') }}</p>
        <template v-for="g in groups" :key="g.key">
        <component :is="g.key === 'mine' ? 'div' : 'details'" v-if="g.rows.length" :class="g.key === 'mine' ? 'flex flex-col' : 'u-sport-others'" :data-testid="`slice-sports-${g.key}`">
        <summary v-if="g.key === 'others'" class="flex flex-wrap items-center gap-3">
          <span class="font-semibold">{{ mine.length ? t('ui.sliceDefaults.otherSports') : t('ui.sliceDefaults.allSports') }}</span>
          <span class="u-small u-muted">{{ t('ui.sliceDefaults.sportCount', { n: g.rows.length }) }}</span>
        </summary>
        <details v-for="r in g.rows" :key="r.slug" class="u-sport-slices" :data-sport="r.slug">
          <summary class="flex flex-wrap items-center gap-3">
            <span class="font-semibold">{{ sportName(r.slug) }}</span>
            <span class="u-small u-muted">{{ changes(r) ? t('ui.sliceDefaults.changes', { n: changes(r) }) : t('ui.sliceDefaults.asDefaults') }}</span>
            <span class="u-small u-muted">· {{ t('ui.slicePicker.cost', { n: costPerMatch(slicesOf(r.slug), chosenOf(r)) }) }}</span>
            <UiBadge v-if="`slices.${r.slug}` in staged" tone="info" icon="info">{{ t('ui.settings.changed') }}</UiBadge>
            <UiIcon v-if="r.lock" name="lock" :size="13" />
          </summary>
          <div class="flex flex-col gap-3 pt-3">
            <p v-if="r.lock" class="m-0 u-small u-muted flex items-center gap-2"><UiIcon name="lock" :size="13" />{{ r.lock }}</p>
            <SliceChecklist :slices="slicesOf(r.slug)" :chosen="chosenOf(r)" :disabled="!!r.lock" :label="sportName(r.slug)" @toggle="(k, on) => toggleSport(r, k, on)" />
            <div class="flex flex-wrap gap-2">
              <button v-if="`slices.${r.slug}` in staged" type="button" class="u-btn u-btn-ghost u-btn-sm" @click="emit('unstage', `slices.${r.slug}`)">{{ t('ui.settings.undo') }}</button>
              <button v-else-if="r.row?.source === 'overrides' && !r.lock" type="button" class="u-btn u-btn-ghost u-btn-sm" @click="resetSport(r)">{{ t('ui.sliceDefaults.resetSport') }}</button>
            </div>
            <p v-if="problems[`slices.${r.slug}`]" class="m-0 u-small" role="alert" style="color: var(--danger)">{{ problems[`slices.${r.slug}`] }}</p>
          </div>
        </details>
        </component>
        </template>
      </div>
    </template>
  </section>
</template>

<style>
.u-sport-slices {
  border-top: 1px solid var(--line);
  padding: var(--sp-3) 0;
}
.u-sport-slices > summary,
.u-sport-others > summary {
  cursor: pointer;
  min-height: 32px;
}
.u-sport-others {
  border-top: 1px solid var(--line);
  padding: var(--sp-3) 0;
}
.u-sport-others[open] > summary {
  margin-bottom: var(--sp-2);
}
.u-sport-others > .u-sport-slices {
  padding-left: var(--sp-5);
}
</style>
