<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import SideRail from '@/app/SideRail.vue'
import BottomBar from '@/app/BottomBar.vue'
import TopBar from '@/app/TopBar.vue'
import CommandPalette from '@/app/CommandPalette.vue'
import ShortcutsDialog from '@/app/ShortcutsDialog.vue'
import TokenPrompt from '@/app/TokenPrompt.vue'
import ToastHost from '@/ui/ToastHost.vue'
import UiIcon from '@/ui/UiIcon.vue'
import { NAV, navFor } from '@/app/nav'
import { useStatusStore } from '@/app/statusStore'
import { authNeeded } from '@/lib/auth'
import { isTyping } from '@/ui/focus'
import { applyDensity } from '@/ui/prefs'
import { BELOW_DESKTOP, BELOW_TABLET, useMedia } from '@/ui/media'

/**
 * The frame of the new app (3.3): side rail on desktop (collapsed on tablet), bottom bar with "More" on
 * phone, the top bar, the banner while the server cannot be reached, toasts, the quick search, the
 * shortcuts and the token prompt. Global keys (4.9) are off while the user types in a field.
 */
const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const status = useStatusStore()
const phone = useMedia(BELOW_TABLET)
const tablet = useMedia(BELOW_DESKTOP)

const palette = ref(false)
const shortcuts = ref(false)
const title = computed(() => {
  const item = navFor(route.path)
  return item ? t(`ui.nav.${item.key}`) : t('ui.shell.brand')
})
watch(
  title,
  (v) => {
    document.title = `${v} · ${t('ui.shell.brand')}`
  },
  { immediate: true },
)

// ---- global keys ----
let pendingG = 0
function onKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    palette.value = !palette.value
    return
  }
  if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return
  if (palette.value || shortcuts.value || authNeeded.value || document.querySelector('[aria-modal="true"], [role="menu"]')) return
  if (e.key === '?') {
    shortcuts.value = true
  } else if (e.key === '/') {
    const target = document.querySelector<HTMLElement>('main [data-filter-focus]')
    if (!target) return
    target.focus()
  } else if (e.key === 'g') {
    pendingG = Date.now()
    return
  } else if (pendingG && Date.now() - pendingG < 1500) {
    const item = NAV.find((n) => n.hotkey === e.key)
    pendingG = 0
    if (!item) return
    void router.push(item.to)
  } else return
  e.preventDefault()
}

// Started here and not in onMounted: the screen inside mounts first and should find the request running
status.start()
onMounted(() => {
  applyDensity()
  document.addEventListener('keydown', onKey)
})
onUnmounted(() => {
  status.stop()
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div class="u-app u-shell" :class="{ 'is-phone': phone }">
    <a href="#main" class="u-skip">{{ t('ui.shell.skip') }}</a>
    <SideRail v-if="!phone" :force-collapsed="tablet" @palette="palette = true" />
    <div class="u-shell-main">
      <TopBar :title="title" :compact="phone" @shortcuts="shortcuts = true" @palette="palette = true" />
      <div v-if="status.offline" class="u-offline" role="status" data-testid="offline-banner">
        <UiIcon name="alert" :size="16" />{{ t('ui.shell.offline') }}
      </div>
      <main id="main" tabindex="-1" class="u-content" :class="{ 'is-stale': status.offline }">
        <RouterView />
      </main>
    </div>
    <BottomBar v-if="phone" />
    <ToastHost />
    <CommandPalette v-if="palette" @close="palette = false" />
    <ShortcutsDialog v-if="shortcuts" @close="shortcuts = false" />
    <TokenPrompt v-if="authNeeded" />
  </div>
</template>

<style>
.u-shell {
  display: flex;
  min-height: 100vh;
}
.u-shell-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.u-content {
  flex: 1;
  width: 100%;
  max-width: 1440px;
  margin: 0 auto;
  padding: var(--sp-6) var(--sp-5) var(--sp-8);
}
.u-content:focus {
  outline: none;
}
@media (min-width: 640px) {
  .u-content {
    padding: var(--sp-6) var(--sp-6) var(--sp-8);
  }
}
@media (min-width: 1024px) {
  .u-content {
    padding: var(--sp-7) var(--sp-7) var(--sp-8);
  }
}
.u-shell.is-phone .u-content {
  padding-bottom: 96px;
}
.u-content.is-stale {
  opacity: 0.6;
}
.u-offline {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-5);
  background: var(--warn-bg);
  color: var(--warn-fg);
  font-weight: 600;
}
.u-skip {
  position: absolute;
  left: -9999px;
  top: 8px;
  z-index: 100;
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface);
  border-radius: var(--r-control);
}
.u-skip:focus {
  left: 8px;
}
</style>
