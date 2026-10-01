<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, type Settings, type SystemStats } from '@/api/client'
import { setLocale, type Lang } from '@/i18n'
import { themePref, setTheme, type ThemePref } from '@/lib/theme'
import { num } from '@/lib/format'
import { onTabKeydown } from '@/lib/tabs'
import { errorText, toast, toastError } from '@/lib/toast'
import { useLeaguesStore } from '@/stores/leagues'

type Tab = 'general' | 'data' | 'advanced'
const TABS: readonly Tab[] = ['general', 'data', 'advanced']
const { t, locale } = useI18n()
const leagues = useLeaguesStore()
const tab = ref<Tab>('general')

const original = ref<Settings | null>(null)
const form = reactive<Partial<Settings>>({})
const stats = ref<SystemStats | null>(null)
const saving = ref(false)
const backingUp = ref(false)
const backup = ref<{ url: string; name: string } | null>(null)

const themes: { v: ThemePref; k: string }[] = [
  { v: 'light', k: 'settings.themeLight' },
  { v: 'dark', k: 'settings.themeDark' },
  { v: 'system', k: 'settings.themeSystem' },
]
// Bounds mirror SettingsUpdate in src/web/routes/settings.py
const advancedFields: { key: keyof Settings; label: string; step: string; min: number; max: number }[] = [
  { key: 'request_timeout', label: 'settings.timeout', step: '1', min: 1, max: 300 },
  { key: 'max_concurrent', label: 'settings.concurrent', step: '1', min: 1, max: 50 },
  { key: 'wait_time_min', label: 'settings.waitMin', step: '0.5', min: 0, max: 60 },
  { key: 'wait_time_max', label: 'settings.waitMax', step: '0.5', min: 0, max: 60 },
  { key: 'max_retries', label: 'settings.retries', step: '1', min: 0, max: 10 },
  { key: 'refresh_window_hours', label: 'settings.refreshWindow', step: '1', min: 0, max: 720 },
]

/** First out-of-range advanced field as a message, or '' when all are valid. */
function invalidField(): string {
  for (const f of advancedFields) {
    // v-model.number leaves '' in a cleared field
    const v = form[f.key] as unknown
    if (typeof v !== 'number' || !Number.isFinite(v) || v < f.min || v > f.max) {
      return t('settings.invalidNumber', { field: t(f.label), min: f.min, max: f.max })
    }
  }
  return ''
}

const changed = computed(() => {
  const o = original.value
  if (!o) return {}
  const out: Partial<Settings> = {}
  for (const k of Object.keys(form) as (keyof Settings)[]) {
    if (form[k] !== o[k]) (out as any)[k] = form[k]
  }
  return out
})
const dirty = computed(() => Object.keys(changed.value).length > 0)

async function load() {
  try {
    let statsError: unknown = null
    const [s, st] = await Promise.all([
      api.settings(),
      api.stats().catch((e) => {
        statsError = e
        return null
      }),
    ])
    original.value = s
    Object.assign(form, {
      data_dir: s.data_dir,
      request_timeout: s.request_timeout,
      max_concurrent: s.max_concurrent,
      wait_time_min: s.wait_time_min,
      wait_time_max: s.wait_time_max,
      max_retries: s.max_retries,
      fetch_only_finished: s.fetch_only_finished,
      save_empty_rounds: s.save_empty_rounds,
      refresh_window_hours: s.refresh_window_hours,
      log_level: s.log_level,
    })
    stats.value = st
    // The settings loaded; the disk/totals box is just missing, so say why
    if (statsError) toastError(new Error(t('common.statsFailed', { error: errorText(statsError) })))
  } catch (e) {
    toastError(e)
  }
}

async function save() {
  if (!dirty.value) return
  const invalid = invalidField()
  if (invalid) {
    toastError(new Error(invalid))
    return
  }
  saving.value = true
  try {
    const r = await api.saveSettings(changed.value)
    toast(r.status === 'success' ? t('settings.saved') : t('settings.noChange'))
    await load()
  } catch (e) {
    toastError(e)
  } finally {
    saving.value = false
  }
}

async function changeLang(l: Lang) {
  setLocale(l)
  try {
    await api.saveSettings({ language: l })
  } catch {
    /* the UI language already switched; the CLI language is a nicety */
  }
}

async function doBackup() {
  backingUp.value = true
  backup.value = null
  try {
    const r = await api.backup()
    backup.value = { url: r.download_url, name: r.filename }
  } catch (e) {
    toastError(e)
  } finally {
    backingUp.value = false
  }
}

async function clearAll() {
  if (!window.confirm(t('settings.clearConfirm'))) return
  try {
    await api.clearData('all')
    toast(t('settings.cleared'))
    await Promise.all([load(), leagues.load()])
  } catch (e) {
    toastError(e)
  }
}

onMounted(load)
</script>

<template>
  <h1 class="page-title mb-5">{{ t('settings.title') }}</h1>

  <div class="tabs mb-6" role="tablist">
    <button
      v-for="k in TABS"
      :id="`settings-tab-${k}`"
      :key="k"
      type="button"
      role="tab"
      class="tab"
      :class="{ 'is-active': tab === k }"
      :aria-selected="tab === k"
      :aria-controls="`settings-panel-${k}`"
      :tabindex="tab === k ? 0 : -1"
      @click="tab = k"
      @keydown="onTabKeydown($event, TABS, tab, (v) => (tab = v), 'settings')"
    >
      {{ t(`settings.tabs.${k}`) }}
    </button>
  </div>

  <section v-if="tab === 'general'" id="settings-panel-general" role="tabpanel" aria-labelledby="settings-tab-general" class="card p-6 flex flex-col gap-6 max-w-[560px]">
    <div>
      <span class="label">{{ t('settings.language') }}</span>
      <div class="seg" role="group" :aria-label="t('settings.language')">
        <button type="button" :class="{ 'is-active': locale === 'tr' }" @click="changeLang('tr')">Türkçe</button>
        <button type="button" :class="{ 'is-active': locale === 'en' }" @click="changeLang('en')">English</button>
      </div>
    </div>
    <div>
      <span class="label">{{ t('settings.theme') }}</span>
      <div class="seg" role="group" :aria-label="t('settings.theme')">
        <button v-for="th in themes" :key="th.v" type="button" :class="{ 'is-active': themePref === th.v }" @click="setTheme(th.v)">{{ t(th.k) }}</button>
      </div>
    </div>
  </section>

  <section v-else-if="tab === 'data'" id="settings-panel-data" role="tabpanel" aria-labelledby="settings-tab-data" class="card p-6 flex flex-col gap-6 max-w-[560px]">
    <div>
      <label class="label" for="s-dir">{{ t('settings.dataDir') }}</label>
      <div class="flex gap-2">
        <input id="s-dir" v-model="form.data_dir" class="field mono" />
        <button type="button" class="btn btn-primary" :disabled="!dirty || saving" @click="save">{{ t('common.save') }}</button>
      </div>
      <p class="hint">{{ t('settings.dataDirHint') }}</p>
    </div>
    <div v-if="stats" class="soft p-4 flex flex-col gap-1">
      <div class="flex justify-between text-sm"><span style="color: var(--muted)">{{ t('settings.disk') }}</span><span class="mono font-semibold">{{ stats.disk_usage?.formatted_total }}</span></div>
      <div class="text-[13px]" style="color: var(--muted)">{{ t('settings.totals', { leagues: num(stats.leagues), matches: num(stats.matches), details: num(stats.details) }) }}</div>
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <button type="button" class="btn" :disabled="backingUp" @click="doBackup">
        <span v-if="backingUp" class="spinner"></span>{{ backingUp ? t('settings.backingUp') : t('settings.backup') }}
      </button>
      <button type="button" class="btn btn-danger" @click="clearAll">{{ t('settings.clear') }}</button>
    </div>
    <p v-if="backup" class="m-0 text-sm">{{ t('settings.backupReady') }} <a :href="backup.url" download class="font-semibold">{{ backup.name }}</a></p>
  </section>

  <section v-else id="settings-panel-advanced" role="tabpanel" aria-labelledby="settings-tab-advanced" class="card p-6 flex flex-col gap-6 max-w-[640px]">
    <p class="page-sub text-sm">{{ t('settings.advancedNote') }}</p>
    <div class="grid gap-4 sm:grid-cols-2">
      <div v-for="f in advancedFields" :key="f.key">
        <label class="label" :for="`s-${f.key}`">{{ t(f.label) }}</label>
        <input :id="`s-${f.key}`" v-model.number="(form as any)[f.key]" type="number" :step="f.step" :min="f.min" :max="f.max" class="field mono" />
      </div>
      <div>
        <label class="label" for="s-log">{{ t('settings.logLevel') }}</label>
        <select id="s-log" v-model="form.log_level" class="field">
          <option v-for="lv in ['DEBUG', 'INFO', 'WARNING', 'ERROR']" :key="lv" :value="lv">{{ lv }}</option>
        </select>
      </div>
    </div>
    <label class="flex items-center gap-3 text-sm cursor-pointer"><input v-model="form.fetch_only_finished" type="checkbox" class="check" />{{ t('settings.onlyFinished') }}</label>
    <label class="flex items-center gap-3 text-sm cursor-pointer"><input v-model="form.save_empty_rounds" type="checkbox" class="check" />{{ t('settings.emptyRounds') }}</label>
    <p class="m-0 text-xs" style="color: var(--muted)">{{ t('settings.refreshWindowHint') }}</p>
    <div><button type="button" class="btn btn-primary" :disabled="!dirty || saving" @click="save">{{ t('common.save') }}</button></div>
  </section>
</template>
