<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, ApiError, type BypassTest, type Settings, type SystemStats } from '@/api/client'
import { setLocale, type Lang } from '@/i18n'
import { themePref, setTheme, type ThemePref } from '@/lib/theme'
import { appVersion } from '@/lib/appVersion'
import { matchDate, num } from '@/lib/format'
import { onTabKeydown } from '@/lib/tabs'
import { reloadApp } from '@/lib/auth'
import { errorText, toast, toastError } from '@/lib/toast'
import { UPSTREAM_REASONS } from '@/lib/upstream'
import { useBridgeStore } from '@/stores/bridge'
import { useLeaguesStore } from '@/stores/leagues'
import AppIcon from '@/components/AppIcon.vue'

type Tab = 'general' | 'data' | 'connection' | 'advanced'
const TABS: readonly Tab[] = ['general', 'data', 'connection', 'advanced']
const { t, locale } = useI18n()
const leagues = useLeaguesStore()
const bridge = useBridgeStore()
const tab = ref<Tab>('general')

const original = ref<Settings | null>(null)
const form = reactive<Partial<Settings>>({})
const stats = ref<SystemStats | null>(null)
const saving = ref(false)
const backingUp = ref(false)
const backup = ref<{ url: string; name: string } | null>(null)
// True when the server asks for an access token (SOFASCORE_API_TOKEN): only then is there a session to end
const tokenAuth = ref(false)

const themes: { v: ThemePref; k: string }[] = [
  { v: 'light', k: 'settings.themeLight' },
  { v: 'dark', k: 'settings.themeDark' },
  { v: 'system', k: 'settings.themeSystem' },
]
// Bounds mirror SettingsUpdate in src/web/routes/settings.py
const advancedFields: { key: keyof Settings; label: string; step: string; min: number; max: number }[] = [
  { key: 'request_timeout', label: 'settings.timeout', step: '1', min: 1, max: 300 },
  { key: 'max_concurrent', label: 'settings.concurrent', step: '1', min: 1, max: 50 },
  { key: 'request_rate_limit', label: 'settings.rateLimit', step: '1', min: 0, max: 1000 },
  { key: 'wait_time_min', label: 'settings.waitMin', step: '0.5', min: 0, max: 60 },
  { key: 'wait_time_max', label: 'settings.waitMax', step: '0.5', min: 0, max: 60 },
  { key: 'max_retries', label: 'settings.retries', step: '1', min: 0, max: 10 },
  { key: 'refresh_window_hours', label: 'settings.refreshWindow', step: '1', min: 0, max: 720 },
]

// Mirrors DEFAULT_RATE_LIMIT in src/throttle.py (tests/test_throttle.py keeps the two equal)
const DEFAULT_RATE_LIMIT = 5

/**
 * Whether the request budget in the form is riskier than the default: 'off' (0, no limit),
 * 'high' (above the default) or '' (at or below it). A warning only: saving is not blocked.
 */
const rateRisk = computed<'' | 'off' | 'high'>(() => {
  const v = form.request_rate_limit as unknown
  if (typeof v !== 'number' || !Number.isFinite(v) || v < 0) return ''
  if (v === 0) return 'off'
  return v > DEFAULT_RATE_LIMIT ? 'high' : ''
})

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

/** Proxy fields as a message, or '' when they can be saved. Mirrors the proxy_url check of SettingsUpdate. */
function invalidProxy(): string {
  const url = String(form.proxy_url ?? '').trim()
  if (form.use_proxy && !url) return t('settings.proxy.required')
  if (url && !/^(https?|socks5h?):\/\/[^\s/]+/i.test(url)) return t('settings.proxy.invalid')
  return ''
}

// ---- connection check: one real request to SofaScore, sent by the button and by nothing else ----
const testing = ref(false)
const test = ref<BypassTest | null>(null)
const testError = ref('')

const health = computed(() => bridge.health)
const healthBadge = computed(
  () => ({ ok: 'badge-ok', degraded: 'badge-warn', blocked: 'badge-danger' })[health.value?.state ?? 'ok'] ?? 'badge-neutral',
)
/** Why the bridge is not healthy, in the banner's words; nothing while it is ok. */
const healthReason = computed(() => {
  const h = health.value
  if (!h || h.state === 'ok' || !h.last_error) return ''
  const k = h.last_error.kind
  return t(`bridge.reason.${k === 'challenge' || k === 'browser' ? k : 'forbidden'}`)
})
const testText = computed(() => {
  const r = test.value
  if (!r || r.success) return ''
  return r.reason && UPSTREAM_REASONS.includes(r.reason) ? t(`upstream.${r.reason}`) : r.message
})
/**
 * Did the request get past the anti-bot check? Judged by the outcome, not by has_token: no token
 * is cached when SofaScore never asked for a challenge. Unknown when the request never got there.
 */
const challengeKey = computed(() => {
  const r = test.value
  if (!r) return ''
  if (r.success) return 'passed'
  return r.reason === 'blocked' ? 'failed' : ''
})

async function runTest() {
  if (testing.value) return
  testing.value = true
  test.value = null
  testError.value = ''
  try {
    const r = await api.bypassTest()
    test.value = r
    bridge.apply(r.health) // the banner and the state above follow the test right away
  } catch (e) {
    testError.value = errorText(e)
  } finally {
    testing.value = false
  }
}

const changed = computed(() => {
  const o = original.value
  if (!o) return {}
  const out: Partial<Settings> = {}
  for (const k of Object.keys(form) as (keyof Settings)[]) {
    if (form[k] !== o[k]) (out as Record<string, unknown>)[k] = form[k]
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
      request_rate_limit: s.request_rate_limit,
      wait_time_min: s.wait_time_min,
      wait_time_max: s.wait_time_max,
      max_retries: s.max_retries,
      fetch_only_finished: s.fetch_only_finished,
      save_empty_rounds: s.save_empty_rounds,
      refresh_window_hours: s.refresh_window_hours,
      log_level: s.log_level,
      // proxy_url arrives with its password masked (***); sent back unchanged, the server keeps the real one
      use_proxy: s.use_proxy,
      proxy_url: s.proxy_url,
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
  const proxyTouched = 'use_proxy' in changed.value || 'proxy_url' in changed.value
  const invalid = invalidField() || (proxyTouched ? invalidProxy() : '')
  if (invalid) {
    toastError(new Error(invalid))
    return
  }
  saving.value = true
  try {
    const r = await api.saveSettings(changed.value)
    toast(r.data_dir_changed ? t('settings.dataDirChanged') : r.status === 'success' ? t('settings.saved') : t('settings.noChange'))
    // The other folder has its own files: league counts come from there now
    await Promise.all([load(), r.data_dir_changed ? leagues.load() : null])
  } catch (e) {
    const retype = e instanceof ApiError && e.reason === 'proxy_password_required'
    toastError(retype ? new Error(t('settings.proxy.retypePassword')) : e)
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

async function signOut() {
  try {
    await api.logout()
    reloadApp()
  } catch (e) {
    toastError(e)
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

onMounted(() => {
  void load()
  // Without this answer the sign-out button stays hidden, which is right when no token is set
  api.authStatus().then(
    (s) => (tokenAuth.value = !!s.required),
    () => {},
  )
})
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
        <button type="button" :class="{ 'is-active': locale === 'tr' }" :aria-pressed="locale === 'tr'" @click="changeLang('tr')">Türkçe</button>
        <button type="button" :class="{ 'is-active': locale === 'en' }" :aria-pressed="locale === 'en'" @click="changeLang('en')">English</button>
      </div>
    </div>
    <div>
      <span class="label">{{ t('settings.theme') }}</span>
      <div class="seg" role="group" :aria-label="t('settings.theme')">
        <button v-for="th in themes" :key="th.v" type="button" :class="{ 'is-active': themePref === th.v }" @click="setTheme(th.v)">{{ t(th.k) }}</button>
      </div>
    </div>
    <div v-if="tokenAuth">
      <span class="label">{{ t('auth.session') }}</span>
      <button type="button" class="btn" data-testid="sign-out" @click="signOut">{{ t('auth.signOut') }}</button>
      <p class="hint">{{ t('auth.signOutHint') }}</p>
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

  <section v-else-if="tab === 'connection'" id="settings-panel-connection" role="tabpanel" aria-labelledby="settings-tab-connection" class="card p-6 flex flex-col gap-6 max-w-[640px]">
    <div class="flex flex-col gap-3">
      <h2 class="m-0 text-base font-bold">{{ t('settings.connection.title') }}</h2>
      <p class="page-sub text-sm">{{ t('settings.connection.note') }}</p>
      <dl class="conn-facts soft p-4 text-sm" data-testid="bridge-state">
        <dt>{{ t('settings.connection.state') }}</dt>
        <dd>
          <span class="badge" :class="health ? healthBadge : 'badge-neutral'">{{ t(`settings.connection.stateValue.${health?.state ?? 'unknown'}`) }}</span>
        </dd>
        <dt>{{ t('settings.connection.lastSuccess') }}</dt>
        <dd>{{ health?.last_success_at ? matchDate(health.last_success_at) : t('settings.connection.never') }}</dd>
        <template v-if="health && health.consecutive_failures > 0">
          <dt>{{ t('settings.connection.failures') }}</dt>
          <dd class="mono">{{ num(health.consecutive_failures) }}</dd>
        </template>
      </dl>
      <p v-if="healthReason" class="m-0 text-sm" style="color: var(--muted)">{{ healthReason }}</p>
      <div>
        <button type="button" class="btn" :disabled="testing" data-testid="connection-test" @click="runTest">
          <span v-if="testing" class="spinner"></span>{{ testing ? t('settings.connection.running') : t('settings.connection.run') }}
        </button>
      </div>
      <div v-if="test" class="soft p-4 flex flex-col gap-2 text-sm" :role="test.success ? 'status' : 'alert'" data-testid="connection-result">
        <p class="m-0 font-semibold" :style="{ color: test.success ? 'var(--ok-fg)' : 'var(--danger)' }">
          {{ test.success ? t('settings.connection.success', { n: num(test.events_count) }) : t('settings.connection.failed') }}
        </p>
        <p v-if="testText" class="m-0">{{ testText }}</p>
        <dl class="conn-facts">
          <dt>{{ t('settings.connection.browser') }}</dt>
          <dd>{{ t(`settings.connection.browserValue.${test.browser_ready ? 'ready' : 'down'}`) }}</dd>
          <template v-if="challengeKey">
            <dt>{{ t('settings.connection.challenge') }}</dt>
            <dd>{{ t(`settings.connection.challengeValue.${challengeKey}`) }}</dd>
          </template>
        </dl>
      </div>
      <p v-else-if="testError" class="m-0 text-sm" role="alert" style="color: var(--danger)">{{ testError }}</p>
    </div>

    <div class="flex flex-col gap-3 pt-6" style="border-top: 1px solid var(--border)">
      <h2 class="m-0 text-base font-bold">{{ t('settings.proxy.title') }}</h2>
      <p class="page-sub text-sm">{{ t('settings.proxy.note') }}</p>
      <label class="flex items-center gap-3 text-sm cursor-pointer"><input id="s-use-proxy" v-model="form.use_proxy" type="checkbox" class="check" />{{ t('settings.proxy.use') }}</label>
      <div>
        <label class="label" for="s-proxy">{{ t('settings.proxy.url') }}</label>
        <input id="s-proxy" v-model.trim="form.proxy_url" class="field mono" placeholder="http://user:password@host:8080" autocomplete="off" autocapitalize="off" spellcheck="false" />
        <p class="hint">{{ t('settings.proxy.hint') }}</p>
      </div>
      <div><button type="button" class="btn btn-primary" :disabled="!dirty || saving" @click="save">{{ t('common.save') }}</button></div>
    </div>
  </section>

  <section v-else id="settings-panel-advanced" role="tabpanel" aria-labelledby="settings-tab-advanced" class="card p-6 flex flex-col gap-6 max-w-[640px]">
    <p class="page-sub text-sm">{{ t('settings.advancedNote') }}</p>
    <div class="grid gap-4 sm:grid-cols-2">
      <div v-for="f in advancedFields" :key="f.key" :class="{ 'sm:col-span-2': f.key === 'request_rate_limit' }">
        <label class="label" :for="`s-${f.key}`">{{ t(f.label) }}</label>
        <template v-if="f.key === 'request_rate_limit'">
          <input :id="`s-${f.key}`" v-model.number="form[f.key]" type="number" :step="f.step" :min="f.min" :max="f.max" class="field mono" :class="{ 'field-warn': rateRisk }" aria-describedby="s-rate-hint" />
          <p id="s-rate-hint" class="hint">{{ t('settings.rateLimitHint', { n: DEFAULT_RATE_LIMIT }) }}</p>
          <p v-if="rateRisk" class="rate-warning" role="status" data-testid="rate-warning">
            <AppIcon name="alert" :size="16" />{{ t(`settings.rateLimitWarn.${rateRisk}`, { n: DEFAULT_RATE_LIMIT }) }}
          </p>
        </template>
        <input v-else :id="`s-${f.key}`" v-model.number="form[f.key]" type="number" :step="f.step" :min="f.min" :max="f.max" class="field mono" />
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

  <p v-if="appVersion" class="app-version mt-6 mb-0 text-xs" style="color: var(--muted)">{{ t('settings.version', { version: appVersion }) }}</p>
</template>

<style scoped>
.field-warn {
  border-color: var(--warn-fg);
}
.rate-warning {
  display: flex;
  gap: 8px;
  margin: 8px 0 0;
  padding: 8px 12px;
  border-radius: var(--radius);
  background: var(--warn-bg);
  color: var(--warn-fg);
  font-size: 13px;
  line-height: 1.45;
}
.rate-warning svg {
  flex-shrink: 0;
  margin-top: 1px;
}
.conn-facts {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 6px 16px;
  align-items: center;
  margin: 0;
}
.conn-facts dt {
  color: var(--muted);
}
.conn-facts dd {
  margin: 0;
}
</style>
