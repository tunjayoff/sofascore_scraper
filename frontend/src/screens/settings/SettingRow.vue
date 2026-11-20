<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import UiBadge from '@/ui/UiBadge.vue'
import UiIcon from '@/ui/UiIcon.vue'
import type { Setting } from '@/api/v1/schema'
import { metaOf, SAFE_RATE } from './settingsMeta'
import { loadOddsProviders, oddsProvider, oddsProviders } from '@/app/oddsProviders'

/**
 * One setting (6.16): its control, the source chip (where the value comes from), and when it cannot be
 * changed here the lock with the reason (`LockedField`, decision 21: shown in place, greyed, with the
 * source). A secret is never shown: the row offers "Replace". `staged` is the unsaved value (`null` =
 * reset to the weaker layer); `undefined` means unchanged. An id the server can name (`names`, B4: the odds
 * provider) shows its name, and a list of the known ones sets it; any other id can still be typed.
 */
const props = defineProps<{ setting: Setting; staged?: unknown; configFile?: string | null; problem?: string | null }>()
const emit = defineEmits<{ change: [value: unknown]; reset: []; discard: [] }>()
const { t, te } = useI18n()

const id = useId()
const meta = computed(() => metaOf(props.setting.key))
const editable = computed(() => props.setting.writable && !props.setting.locked)
const changed = computed(() => props.staged !== undefined)
const replacing = ref(false)
/** Whether the row has a form control the label names (a locked value or a hidden secret has none). */
const hasControl = computed(() => editable.value && !(props.setting.secret && !replacing.value && !changed.value))
const value = computed(() => (changed.value && props.staged !== null ? props.staged : props.setting.value))

const namesProviders = computed(() => meta.value.names === 'oddsProviders')
if (namesProviders.value) void loadOddsProviders()
const providers = computed(() => (namesProviders.value ? oddsProviders.value : []))
const knownProvider = computed(() => (namesProviders.value ? oddsProvider(value.value as number | string | null) : null))

const label = computed(() => (te(`ui.setting.${props.setting.key}`, 'en') ? t(`ui.setting.${props.setting.key}`) : props.setting.key))
const help = computed(() => (te(`ui.settingHelp.${props.setting.key}`, 'en') ? t(`ui.settingHelp.${props.setting.key}`) : ''))

const lockReason = computed(() => {
  const s = props.setting
  if (s.locked) {
    if (s.source === 'file') return t('ui.settings.lock.file', { file: s.source_name || props.configFile || 'sofascore.toml' })
    if (s.source === 'env') return t('ui.settings.lock.env', { name: s.source_name })
    if (s.source === 'flag') return t('ui.settings.lock.flag')
    return t('ui.settings.lock.other')
  }
  if (!s.writable) return t('ui.settings.lock.readOnly')
  return ''
})

const shownValue = computed(() => {
  const v = value.value
  if (Array.isArray(v)) return v.length ? v.join(', ') : '—'
  if (typeof v === 'boolean') return v ? t('ui.common.yes') : t('ui.common.no')
  if (v === '' || v == null) return '—'
  return knownProvider.value ? `${String(v)} · ${knownProvider.value.name}` : String(v)
})

const rateWarning = computed(() => props.setting.key === 'client.rate' && (Number(value.value) > SAFE_RATE || Number(value.value) === 0))

function onInput(e: Event) {
  const el = e.target as HTMLInputElement | HTMLSelectElement
  const c = meta.value.control
  let next: unknown = el.value
  if (c.type === 'bool') next = (el as HTMLInputElement).checked
  else if (c.type === 'int' || c.type === 'float') {
    if (el.value.trim() === '') return
    next = Number(el.value)
    if (!Number.isFinite(next as number)) return
  }
  if (next === props.setting.value && !props.setting.secret) emit('discard')
  else emit('change', next)
}

/** A bookmaker picked from the known ones; the empty choice ("another number") leaves the typed id. */
function onPick(e: Event) {
  const picked = (e.target as HTMLSelectElement).value
  if (picked === '') return
  const next = Number(picked)
  if (next === props.setting.value) emit('discard')
  else emit('change', next)
}

function stopReplacing() {
  replacing.value = false
  emit('discard')
}
</script>

<template>
  <div class="u-setting" :class="{ 'is-changed': changed, 'is-locked': !editable }" :data-setting="setting.key">
    <div class="u-setting-head">
      <label v-if="hasControl" :for="id" class="font-semibold">{{ label }}</label>
      <span v-else :id="`${id}-label`" class="font-semibold">{{ label }}</span>
      <code class="u-mono u-muted" style="font-size: 0.75rem">{{ setting.key }}</code>
      <span class="flex-1"></span>
      <UiBadge tone="neutral" :title="setting.source_name || undefined" data-testid="source">
        {{ t(`ui.settings.source.${setting.source}`) }}
      </UiBadge>
      <UiBadge v-if="changed" tone="info" icon="info">{{ staged === null ? t('ui.settings.willReset') : t('ui.settings.changed') }}</UiBadge>
    </div>
    <p v-if="help" class="m-0 u-small u-muted">{{ help }}</p>

    <div class="u-setting-control">
      <!-- a secret is never shown; it can only be replaced -->
      <template v-if="setting.secret && editable && !replacing && !changed">
        <span class="u-mono u-muted">{{ setting.value ? String(setting.value) : t('ui.settings.secretEmpty') }}</span>
        <button type="button" class="u-btn u-btn-sm" @click="replacing = true">{{ t('ui.settings.replace') }}</button>
      </template>
      <template v-else-if="setting.secret && editable">
        <input :id="id" type="password" class="u-field u-mono" autocomplete="off" :value="changed && staged !== null ? String(staged) : ''" @change="onInput" />
        <button type="button" class="u-btn u-btn-sm u-btn-ghost" @click="stopReplacing">{{ t('ui.common.cancel') }}</button>
      </template>

      <template v-else-if="!editable">
        <span class="u-setting-value" :class="{ 'u-mono': meta.control.type !== 'bool' }">{{ setting.secret ? (setting.value ? '••••' : '—') : shownValue }}</span>
      </template>

      <template v-else-if="meta.control.type === 'bool'">
        <input :id="id" type="checkbox" role="switch" class="u-check" :checked="Boolean(value)" @change="onInput" />
        <span class="u-small u-muted" aria-hidden="true">{{ value ? t('ui.common.on') : t('ui.common.off') }}</span>
      </template>
      <select v-else-if="meta.control.type === 'choice'" :id="id" class="u-field max-w-[280px]" :value="String(value)" @change="onInput">
        <option v-for="c in meta.control.choices" :key="c" :value="c">{{ te(`ui.settings.choice.${c}`, 'en') ? t(`ui.settings.choice.${c}`) : c }}</option>
      </select>
      <input
        v-else-if="meta.control.type === 'int' || meta.control.type === 'float'"
        :id="id"
        type="number"
        class="u-field u-num max-w-[180px]"
        :min="meta.control.min"
        :max="meta.control.max"
        :step="meta.control.step ?? 'any'"
        :value="value as number"
        :aria-invalid="problem ? 'true' : undefined"
        :aria-describedby="problem ? `${id}-problem` : undefined"
        @change="onInput"
      />
      <input
        v-else
        :id="id"
        type="text"
        class="u-field u-mono"
        :maxlength="meta.control.type === 'text' ? meta.control.maxLength : undefined"
        :value="Array.isArray(value) ? value.join(', ') : String(value ?? '')"
        :aria-invalid="problem ? 'true' : undefined"
        :aria-describedby="problem ? `${id}-problem` : undefined"
        @change="onInput"
      />
      <!-- the known names beside the free entry: picking one sets the id (B4) -->
      <select
        v-if="hasControl && providers.length"
        class="u-field max-w-[280px]"
        :aria-label="t('ui.settings.knownProviders')"
        :value="knownProvider ? String(knownProvider.id) : ''"
        data-testid="known-providers"
        @change="onPick"
      >
        <option value="">{{ t('ui.settings.otherProvider') }}</option>
        <option v-for="p in providers" :key="p.id" :value="String(p.id)">{{ p.name }} · {{ p.id }}</option>
      </select>

      <button v-if="editable && changed" type="button" class="u-btn u-btn-ghost u-btn-sm" @click="emit('discard')">{{ t('ui.settings.undo') }}</button>
      <button v-else-if="editable && setting.source === 'overrides'" type="button" class="u-btn u-btn-ghost u-btn-sm" :title="t('ui.settings.resetHint')" @click="emit('reset')">
        {{ t('ui.settings.reset') }}
      </button>
    </div>

    <p v-if="lockReason" class="m-0 u-small u-muted flex items-center gap-2" data-testid="lock"><UiIcon name="lock" :size="13" />{{ lockReason }}</p>
    <p v-if="rateWarning && editable" class="m-0 u-small" style="color: var(--warn-fg)">{{ t('ui.settings.rateWarning', { n: SAFE_RATE }) }}</p>
    <p v-if="problem" :id="`${id}-problem`" class="m-0 u-small" role="alert" style="color: var(--danger)">{{ problem }}</p>
  </div>
</template>

<style>
.u-setting {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  padding: var(--sp-5) 0;
  border-top: 1px solid var(--line);
}
.u-setting:first-child {
  border-top: 0;
}
.u-setting-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-3);
}
.u-setting-control {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-3);
}
.u-setting-control .u-field {
  flex: 1;
  min-width: 0;
}
.u-setting.is-locked .u-setting-value {
  color: var(--muted);
}
.u-setting.is-changed {
  box-shadow: inset 3px 0 0 var(--accent);
  padding-left: var(--sp-4);
}
</style>
