<script setup lang="ts">
import { onMounted, onUnmounted, ref, computed, watch } from 'vue'
import { useRoute, RouterLink, RouterView } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { motion, LayoutGroup } from 'motion-v'
import { useScrapeStore } from '@/stores/scrape'
import { useMotionPrefs } from '@/composables/useMotionPrefs'
import ProgressBar from '@/components/ProgressBar.vue'

const { t } = useI18n()
const route = useRoute()
const scrape = useScrapeStore()
const { enter } = useMotionPrefs()
const pageMotion = enter(8)

const toolsOpen = ref(false)
const toolsWrap = ref<HTMLElement | null>(null)

const isCollect = computed(() => route.path === '/')
const isMatches = computed(() => route.path.startsWith('/matches') || route.path.startsWith('/match'))
const isTools = computed(() => route.path.startsWith('/advanced'))

watch(
  () => route.path,
  () => {
    toolsOpen.value = false
  },
)

onMounted(() => {
  scrape.init()
  document.addEventListener('click', onDocClick)
})

onUnmounted(() => {
  document.removeEventListener('click', onDocClick)
})

function onDocClick(e: MouseEvent) {
  if (!toolsWrap.value) return
  if (!toolsWrap.value.contains(e.target as Node)) toolsOpen.value = false
}

function toggleTheme() {
  const dark = document.documentElement.classList.toggle('dark')
  localStorage.setItem('theme', dark ? 'dark' : 'light')
}
</script>

<template>
  <div class="min-h-screen flex flex-col">
    <header class="header-shell sticky top-0 z-30">
      <div class="max-w-6xl mx-auto px-4 md:px-6 py-3 flex flex-wrap items-center gap-3 md:gap-6">
        <RouterLink to="/" class="flex items-center gap-3 min-w-0">
          <div class="brand-mark">SS</div>
          <div class="min-w-0">
            <div class="display text-lg font-extrabold leading-none truncate">{{ t('brand_name') }}</div>
            <div class="text-[10px] font-bold uppercase tracking-[0.16em] text-[var(--muted)] mt-1">{{ t('brand_tagline') }}</div>
          </div>
        </RouterLink>

        <LayoutGroup>
          <nav class="flex flex-1 flex-wrap items-center gap-1 md:justify-center">
            <RouterLink to="/" class="nav-pill" :class="{ 'is-active': isCollect }">
              <motion.span
                v-if="isCollect"
                layout-id="nav-glow"
                class="nav-glow"
                :transition="{ type: 'spring', stiffness: 420, damping: 34 }"
              />
              <span class="relative">{{ t('nav_home') }}</span>
            </RouterLink>
            <RouterLink to="/matches" class="nav-pill" :class="{ 'is-active': isMatches }">
              <motion.span
                v-if="isMatches"
                layout-id="nav-glow"
                class="nav-glow"
                :transition="{ type: 'spring', stiffness: 420, damping: 34 }"
              />
              <span class="relative">{{ t('nav_matches') }}</span>
            </RouterLink>

            <div ref="toolsWrap" class="relative">
              <button
                type="button"
                class="nav-pill"
                :class="{ 'is-active': isTools || toolsOpen }"
                :aria-expanded="toolsOpen"
                @click.stop="toolsOpen = !toolsOpen"
              >
                <motion.span
                  v-if="isTools"
                  layout-id="nav-glow"
                  class="nav-glow"
                  :transition="{ type: 'spring', stiffness: 420, damping: 34 }"
                />
                <span class="relative">{{ t('nav_tools') }}</span>
              </button>
              <div v-if="toolsOpen" class="tools-menu" role="menu">
                <RouterLink
                  to="/advanced/leagues"
                  class="nav-pill w-full text-sm"
                  :class="{ 'is-active': route.path.includes('/leagues') }"
                  role="menuitem"
                  @click="toolsOpen = false"
                >
                  {{ t('nav_leagues') }}
                </RouterLink>
                <RouterLink
                  to="/advanced/stats"
                  class="nav-pill w-full text-sm"
                  :class="{ 'is-active': route.path.includes('/stats') }"
                  role="menuitem"
                  @click="toolsOpen = false"
                >
                  {{ t('nav_stats') }}
                </RouterLink>
                <RouterLink
                  to="/advanced/jobs"
                  class="nav-pill w-full text-sm"
                  :class="{ 'is-active': route.path.includes('/jobs') }"
                  role="menuitem"
                  @click="toolsOpen = false"
                >
                  {{ t('nav_jobs') }}
                </RouterLink>
                <RouterLink
                  to="/advanced/settings"
                  class="nav-pill w-full text-sm"
                  :class="{ 'is-active': route.path.includes('/settings') }"
                  role="menuitem"
                  @click="toolsOpen = false"
                >
                  {{ t('nav_settings') }}
                </RouterLink>
              </div>
            </div>
          </nav>
        </LayoutGroup>

        <button type="button" class="btn btn-secondary text-sm min-h-[40px] ml-auto" @click="toggleTheme">
          {{ t('ui_theme_toggle') }}
        </button>
      </div>
    </header>

    <main class="flex-1 w-full max-w-6xl mx-auto px-4 md:px-6 py-6 md:py-10 pb-28">
      <!-- Enter-only motion: no exit / out-in (avoids blank main on async routes) -->
      <RouterView v-slot="{ Component, route: r }">
        <motion.div
          :key="r.fullPath"
          :initial="pageMotion.initial"
          :animate="pageMotion.animate"
          :transition="pageMotion.transition"
        >
          <component :is="Component" />
        </motion.div>
      </RouterView>
    </main>
  </div>
  <ProgressBar />
</template>
