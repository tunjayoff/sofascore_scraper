<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '@/api/client'
import { reloadApp } from '@/lib/auth'
import { errorText } from '@/lib/toast'

/**
 * Covers the app when the server asks for its access token (SOFASCORE_API_TOKEN). The token is sent
 * once; the server answers with an HttpOnly session cookie, so nothing is kept in page storage.
 */
const { t } = useI18n()
const token = ref('')
const busy = ref(false)
const err = ref('')
const input = ref<HTMLInputElement | null>(null)

async function submit() {
  const value = token.value.trim()
  if (!value || busy.value) return
  busy.value = true
  err.value = ''
  try {
    await api.login(value)
    reloadApp()
  } catch (e) {
    err.value = errorText(e)
    busy.value = false
  }
}

onMounted(() => void nextTick(() => input.value?.focus()))
</script>

<template>
  <div class="fixed inset-0 z-[70] flex items-start justify-center px-4 pt-[12vh]" style="background: var(--bg)">
    <form
      role="dialog"
      aria-modal="true"
      aria-labelledby="auth-title"
      class="card w-full max-w-[440px] p-6 flex flex-col gap-4"
      style="box-shadow: var(--shadow)"
      @submit.prevent="submit"
    >
      <h2 id="auth-title" class="m-0 text-xl font-bold">{{ t('auth.title') }}</h2>
      <p class="page-sub text-sm">{{ t('auth.note') }}</p>
      <div>
        <label class="label" for="auth-token">{{ t('auth.label') }}</label>
        <input id="auth-token" ref="input" v-model="token" type="password" class="field mono" autocomplete="current-password" />
      </div>
      <p v-if="err" class="m-0 text-sm" role="alert" data-testid="auth-error" style="color: var(--danger)">{{ err }}</p>
      <div>
        <button type="submit" class="btn btn-primary" :disabled="busy || !token.trim()">
          <span v-if="busy" class="spinner"></span>{{ busy ? t('auth.checking') : t('auth.submit') }}
        </button>
      </div>
    </form>
  </div>
</template>
