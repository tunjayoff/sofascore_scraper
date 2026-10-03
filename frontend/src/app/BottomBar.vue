<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import SidePanel from '@/ui/SidePanel.vue'
import { NAV, navFor } from '@/app/nav'
import { useStatusStore } from '@/app/statusStore'

/** The phone's bottom bar (3.3): Home, Follows, Events, Jobs and "More", a sheet with the other screens. */
const { t } = useI18n()
const route = useRoute()
const status = useStatusStore()
const more = ref(false)

const current = computed(() => navFor(route.path)?.key)
const main = NAV.filter((n) => n.bottom)
const rest = NAV.filter((n) => !n.bottom && !n.hidden)
const inMore = computed(() => rest.some((n) => n.key === current.value))
watch(() => route.fullPath, () => (more.value = false))
</script>

<template>
  <nav class="u-bottombar" :aria-label="t('ui.shell.mainNav')">
    <RouterLink
      v-for="n in main"
      :key="n.key"
      :to="n.to"
      class="u-bottom-item"
      :class="{ 'is-active': current === n.key }"
      :aria-current="current === n.key ? 'page' : undefined"
    >
      <span class="relative"
        ><UiIcon :name="n.icon" :size="20" /><span v-if="n.key === 'jobs' && status.activeJob" class="u-bottom-dot" aria-hidden="true"></span
      ></span>
      <span>{{ t(n.key === 'overview' ? 'ui.nav.home' : `ui.nav.${n.key}`) }}</span>
    </RouterLink>
    <button type="button" class="u-bottom-item" :class="{ 'is-active': inMore }" aria-haspopup="dialog" :aria-expanded="more" @click="more = true">
      <UiIcon name="menu" :size="20" /><span>{{ t('ui.nav.more') }}</span>
    </button>
    <SidePanel v-if="more" :title="t('ui.nav.more')" @close="more = false">
      <ul class="m-0 p-0 list-none flex flex-col gap-1">
        <li v-for="n in rest" :key="n.key">
          <RouterLink :to="n.to" class="u-more-item" :aria-current="current === n.key ? 'page' : undefined">
            <UiIcon :name="n.icon" /><span class="flex-1">{{ t(`ui.nav.${n.key}`) }}</span>
          </RouterLink>
        </li>
      </ul>
    </SidePanel>
  </nav>
</template>

<style>
.u-bottombar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 40;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  border-top: 1px solid var(--border);
  background: var(--surface);
  padding-bottom: env(safe-area-inset-bottom);
}
.u-bottom-item,
.u-app a.u-bottom-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  min-height: 56px;
  border: 0;
  background: none;
  color: var(--muted);
  font: inherit;
  font-size: 0.75rem;
  text-decoration: none;
  cursor: pointer;
}
.u-bottom-item.is-active,
.u-app a.u-bottom-item.is-active {
  color: var(--accent);
  font-weight: 600;
}
.u-bottom-dot {
  position: absolute;
  top: -2px;
  right: -4px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--info-fg);
}
.u-app a.u-more-item {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  min-height: 48px;
  padding: 0 var(--sp-3);
  border-radius: var(--r-control);
  color: var(--text);
  text-decoration: none;
}
.u-app a.u-more-item[aria-current='page'] {
  background: var(--accent-soft);
}
</style>
