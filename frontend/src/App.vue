<script setup lang="ts">
import { onMounted, provide, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useScrapeStore } from '@/stores/scrape'
import { useLeaguesStore } from '@/stores/leagues'
import { useBridgeStore } from '@/stores/bridge'
import { toasts, dismissToast } from '@/lib/toast'
import AppIcon, { type IconName } from '@/components/AppIcon.vue'
import JobCard from '@/components/JobCard.vue'
import AddLeagueDialog from '@/components/AddLeagueDialog.vue'
import SportSwitch from '@/components/SportSwitch.vue'
import BrandMark from '@/components/BrandMark.vue'
import BridgeBanner from '@/components/BridgeBanner.vue'

const { t } = useI18n()
const route = useRoute()
const scrape = useScrapeStore()
const leagues = useLeaguesStore()
const bridge = useBridgeStore()

const nav: { to: string; label: string; icon: IconName; match: (p: string) => boolean }[] = [
  { to: '/', label: 'nav.leagues', icon: 'list', match: (p) => p === '/' },
  { to: '/download', label: 'nav.download', icon: 'download', match: (p) => p.startsWith('/download') },
  { to: '/matches', label: 'nav.matches', icon: 'ball', match: (p) => p.startsWith('/match') },
  { to: '/activity', label: 'nav.activity', icon: 'pulse', match: (p) => p.startsWith('/activity') },
  { to: '/settings', label: 'nav.settings', icon: 'gear', match: (p) => p.startsWith('/settings') },
]

// One "Add league" dialog for the whole app; pages open it through this.
const addOpen = ref(false)
const lastAdded = ref<number | null>(null)
provide('openAddLeague', () => (addOpen.value = true))
provide('lastAddedLeague', lastAdded)

// A failed load is shown from leagues.error (card below); the rejection itself needs no handling
const loadLeagues = () => void leagues.load().catch(() => {})

onMounted(() => {
  scrape.init()
  bridge.init()
  loadLeagues()
  scrape.onFinished(loadLeagues)
  // A job that just ended is the moment a block shows up or clears: don't wait for the next poll
  scrape.onFinished(() => void bridge.check())
})
</script>

<template>
  <div class="min-h-screen md:flex">
    <aside class="app-side">
      <BrandMark />

      <SportSwitch />

      <nav class="app-nav" :aria-label="t('nav.main')">
        <RouterLink
          v-for="n in nav"
          :key="n.to"
          :to="n.to"
          class="nav-item"
          :class="{ 'is-active': n.match(route.path) }"
          :aria-current="n.match(route.path) ? 'page' : undefined"
        >
          <AppIcon :name="n.icon" />{{ t(n.label) }}
        </RouterLink>
      </nav>

      <div class="hidden md:block flex-1"></div>
      <div class="hidden md:block">
        <JobCard v-if="scrape.visible" />
        <div v-else class="text-[13px] p-3 rounded-[10px]" style="color: var(--muted); border: 1px dashed var(--border-2)">{{ t('job.none') }}</div>
      </div>
    </aside>

    <main class="flex-1 min-w-0 px-4 py-6 md:px-10 md:py-8">
      <div class="md:hidden mb-4" v-if="scrape.visible"><JobCard /></div>
      <BridgeBanner />
      <div v-if="leagues.error" class="card p-6 mb-6 flex flex-col items-start gap-3" role="alert">
        <span style="color: var(--danger)">{{ t('leagues.loadFailed') }} {{ leagues.error }}</span>
        <button type="button" class="btn" :disabled="leagues.loading" @click="loadLeagues">{{ t('common.retry') }}</button>
      </div>
      <RouterView />
    </main>

    <AddLeagueDialog v-if="addOpen" @close="addOpen = false" @added="(id) => (lastAdded = id)" />

    <div class="fixed bottom-4 right-4 z-[60] flex flex-col gap-2 max-w-[420px]" aria-live="polite">
      <div
        v-for="tt in toasts"
        :key="tt.id"
        class="card px-4 py-3 flex items-start gap-3 text-sm"
        :style="{ boxShadow: 'var(--shadow)', borderColor: tt.kind === 'error' ? 'var(--danger)' : undefined }"
      >
        <span :style="{ color: tt.kind === 'error' ? 'var(--danger)' : 'var(--accent)' }"><AppIcon :name="tt.kind === 'error' ? 'alert' : 'check'" /></span>
        <span class="flex-1 leading-snug">{{ tt.text }}</span>
        <button type="button" class="btn btn-ghost btn-sm" style="min-height: 28px; padding: 0 6px" :aria-label="t('common.close')" @click="dismissToast(tt.id)"><AppIcon name="x" :size="14" /></button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.app-side {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
}
.app-nav {
  display: flex;
  gap: 4px;
  overflow-x: auto;
}
@media (min-width: 768px) {
  .app-side {
    position: sticky;
    top: 0;
    width: 248px;
    height: 100vh;
    flex-shrink: 0;
    padding: 24px 16px;
    border-bottom: 0;
    border-right: 1px solid var(--border);
  }
  .app-nav {
    flex-direction: column;
    overflow: visible;
  }
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 44px;
  padding: 0 12px;
  border-radius: 8px;
  color: var(--text-2);
  font-weight: 500;
  text-decoration: none;
  white-space: nowrap;
}
.nav-item:hover {
  background: var(--hover);
}
.nav-item.is-active {
  background: var(--ink);
  color: var(--on-ink);
}
</style>
