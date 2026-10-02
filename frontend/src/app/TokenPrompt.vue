<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import { session, V1Error } from '@/api/v1/client'
import { errorText } from '@/api/v1/errors'
import { reloadApp } from '@/lib/auth'
import { lockSecondsLeft, noteAuthLock } from '@/app/session'
import { trapTab } from '@/ui/focus'

/**
 * The token prompt (6.15): covers the app whenever an API call answered 401. The token is sent once; the
 * server answers with an HttpOnly session cookie and the app starts over, so the page never keeps it.
 * After too many wrong tokens the server refuses attempts for a while (`too_many_attempts` with
 * `Retry-After`): the prompt counts down and the button waits. The server does not say how many attempts
 * are left, so the prompt shows no count.
 */
const { t } = useI18n()
const token = ref('')
const visible = ref(false)
const busy = ref(false)
const err = ref('')
const input = ref<HTMLInputElement | null>(null)
const root = ref<HTMLElement | null>(null)
const clock = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | null = null

const left = computed(() => lockSecondsLeft(clock.value))

async function submit() {
  const value = token.value.trim()
  if (!value || busy.value || left.value > 0) return
  busy.value = true
  err.value = ''
  try {
    await session.login(value)
    reloadApp()
  } catch (e) {
    busy.value = false
    if (e instanceof V1Error && (e.code === 'too_many_attempts' || e.reason === 'too_many_attempts')) {
      noteAuthLock(e.retryAfter ?? 30)
      clock.value = Date.now()
    } else if (e instanceof V1Error && (e.code === 'invalid_token' || e.code === 'unauthorized')) {
      err.value = t('ui.token.wrong')
    } else {
      err.value = errorText(e)
    }
  }
}

onMounted(() => {
  ticker = setInterval(() => (clock.value = Date.now()), 1000)
  void nextTick(() => input.value?.focus())
})
onUnmounted(() => {
  if (ticker) clearInterval(ticker)
})
</script>

<template>
  <div class="u-app u-token-cover">
    <div ref="root" role="dialog" aria-modal="true" aria-labelledby="token-title" aria-describedby="token-note" class="u-pop" @keydown="trapTab($event, root)">
      <form class="u-token" @submit.prevent="submit">
        <p class="m-0 flex items-center gap-2 font-bold"><UiIcon name="lock" />{{ t('ui.shell.brand') }}</p>
        <h1 id="token-title" class="u-h2">{{ t('ui.token.title') }}</h1>
        <p id="token-note" class="m-0 u-muted">{{ t('ui.token.note') }}</p>
        <div>
          <label class="u-label" for="token-field">{{ t('ui.token.label') }}</label>
          <div class="flex gap-2">
            <input
              id="token-field"
              ref="input"
              v-model="token"
              :type="visible ? 'text' : 'password'"
              class="u-field u-mono"
              autocomplete="current-password"
              spellcheck="false"
              :aria-invalid="err ? 'true' : undefined"
              :aria-describedby="err ? 'token-error' : undefined"
            />
            <button type="button" class="u-btn u-btn-icon" :aria-label="visible ? t('ui.token.hide') : t('ui.token.show')" :aria-pressed="visible" @click="visible = !visible">
              <UiIcon :name="visible ? 'eyeOff' : 'eye'" />
            </button>
          </div>
        </div>
        <p v-if="left > 0" class="m-0 u-small" role="alert" data-testid="token-locked" style="color: var(--warn-fg)">{{ t('ui.token.locked', { n: left }) }}</p>
        <p v-else-if="err" id="token-error" class="m-0 u-small" role="alert" data-testid="token-error" style="color: var(--danger)">{{ err }}</p>
        <div>
          <button type="submit" class="u-btn u-btn-primary" :disabled="busy || !token.trim() || left > 0">
            <span v-if="busy" class="u-spinner" aria-hidden="true"></span>{{ busy ? t('ui.token.checking') : t('ui.token.submit') }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<style>
.u-token-cover {
  position: fixed;
  inset: 0;
  z-index: 90;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 12vh var(--sp-5) var(--sp-5);
  background: var(--bg);
  overflow-y: auto;
}
.u-token-cover > [role='dialog'] {
  width: 100%;
  max-width: 440px;
}
.u-token {
  padding: var(--sp-6);
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}
</style>
