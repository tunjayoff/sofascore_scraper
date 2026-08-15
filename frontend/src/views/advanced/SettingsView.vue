<script setup lang="ts">
import { ref, onMounted, reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiGet, apiSend } from '@/api/client'
import { setLocale } from '@/i18n'

const { t, locale } = useI18n()
const mode = ref<'simple' | 'tech'>('simple')
const techTab = ref('general')
const saving = ref(false)
const msg = ref('')
const err = ref('')
const settings = reactive<Record<string, any>>({})

async function load() {
  const data = await apiGet<Record<string, any>>('/api/settings')
  Object.assign(settings, data)
  if (data.language === 'en' || data.language === 'tr') setLocale(data.language)
}

async function save() {
  saving.value = true
  msg.value = ''
  err.value = ''
  try {
    await apiSend('/api/settings', 'POST', settings)
    if (settings.language === 'en' || settings.language === 'tr') setLocale(settings.language)
    msg.value = t('settings_applied_runtime')
  } catch (e) {
    err.value = String(e)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="mb-6">
    <p class="text-sm font-semibold link-accent mb-1">{{ t('advanced_hint') }}</p>
    <h1 class="page-title mb-1">{{ t('nav_settings') }}</h1>
  </div>

  <div class="flex gap-2 mb-6">
    <button type="button" class="btn text-sm" :class="mode === 'simple' ? 'btn-primary' : 'btn-secondary'" @click="mode = 'simple'">
      {{ t('settings_tab_simple') }}
    </button>
    <button type="button" class="btn text-sm" :class="mode === 'tech' ? 'btn-primary' : 'btn-secondary'" @click="mode = 'tech'">
      {{ t('settings_tab_tech') }}
    </button>
  </div>

  <div v-if="msg" class="mb-4 p-3 rounded-xl bg-[var(--primary-soft)] link-accent text-sm">{{ msg }}</div>
  <div v-if="err" class="mb-4 p-3 rounded-xl bg-red-50 text-red-700 text-sm">{{ err }}</div>

  <div v-show="mode === 'simple'" class="panel p-6 space-y-4">
    <div>
      <label class="block text-sm text-[var(--muted)] mb-2">{{ t('current_language') }}</label>
      <select v-model="settings.language" class="field-input">
        <option value="tr">{{ t('settings_lang_option_tr') }}</option>
        <option value="en">{{ t('settings_lang_option_en') }}</option>
      </select>
      <p class="text-xs text-[var(--muted)] mt-2">UI locale: {{ locale }}</p>
    </div>
    <div class="flex items-center justify-between">
      <span>{{ t('use_color') }}</span>
      <input v-model="settings.use_color" type="checkbox" class="w-5 h-5" />
    </div>
    <button type="button" class="btn btn-primary" :disabled="saving" @click="save">{{ t('settings_save_changes') }}</button>
  </div>

  <div v-show="mode === 'tech'" class="space-y-4">
    <div class="flex gap-2 overflow-x-auto pb-2">
      <button
        v-for="tab in ['general', 'performance', 'rate_limit', 'data_management']"
        :key="tab"
        type="button"
        class="btn text-sm whitespace-nowrap"
        :class="techTab === tab ? 'btn-primary' : 'btn-secondary'"
        @click="techTab = tab"
      >
        {{ tab }}
      </button>
    </div>

    <div v-show="techTab === 'general'" class="panel p-6 space-y-4">
      <div>
        <label class="block text-sm text-[var(--muted)] mb-2">{{ t('base_url') }}</label>
        <input v-model="settings.api_base_url" class="field-input" />
      </div>
      <div>
        <label class="block text-sm text-[var(--muted)] mb-2">{{ t('current_data_dir') }}</label>
        <input v-model="settings.data_dir" class="field-input" />
      </div>
      <div class="flex items-center justify-between">
        <span>{{ t('use_proxy') }}</span>
        <input v-model="settings.use_proxy" type="checkbox" class="w-5 h-5" />
      </div>
      <input v-if="settings.use_proxy" v-model="settings.proxy_url" class="field-input" placeholder="http://user:pass@host:port" />
    </div>

    <div v-show="techTab === 'performance'" class="panel p-6 grid md:grid-cols-2 gap-4">
      <div>
        <label class="block text-sm text-[var(--muted)] mb-2">{{ t('settings_max_concurrent') }}</label>
        <input v-model.number="settings.max_concurrent" type="number" class="field-input" />
      </div>
      <div>
        <label class="block text-sm text-[var(--muted)] mb-2">{{ t('settings_request_timeout') }}</label>
        <input v-model.number="settings.request_timeout" type="number" class="field-input" />
      </div>
      <div>
        <label class="block text-sm text-[var(--muted)] mb-2">{{ t('settings_wait_time_min') }}</label>
        <input v-model.number="settings.wait_time_min" type="number" step="0.1" class="field-input" />
      </div>
      <div>
        <label class="block text-sm text-[var(--muted)] mb-2">{{ t('settings_wait_time_max') }}</label>
        <input v-model.number="settings.wait_time_max" type="number" step="0.1" class="field-input" />
      </div>
    </div>

    <div v-show="techTab === 'rate_limit'" class="panel p-6 grid md:grid-cols-2 gap-4">
      <div>
        <label class="block text-sm text-[var(--muted)] mb-2">{{ t('settings_rate_limit_consecutive') }}</label>
        <input v-model.number="settings.rate_limit_threshold_consecutive" type="number" class="field-input" />
      </div>
      <div>
        <label class="block text-sm text-[var(--muted)] mb-2">{{ t('settings_rate_limit_ratio') }}</label>
        <input v-model.number="settings.rate_limit_threshold_ratio" type="number" step="0.01" class="field-input" />
      </div>
    </div>

    <div v-show="techTab === 'data_management'" class="panel p-6 space-y-4">
      <a href="/api/export/csv" class="btn btn-primary inline-flex" download>{{ t('csv_download') }}</a>
      <p class="text-sm text-[var(--muted)]">Backup / clear remain available via API; use with care.</p>
    </div>

    <button v-if="techTab !== 'data_management'" type="button" class="btn btn-primary" :disabled="saving" @click="save">
      {{ t('settings_save_changes') }}
    </button>
  </div>
</template>
