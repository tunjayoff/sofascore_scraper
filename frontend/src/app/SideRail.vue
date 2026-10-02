<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import { GROUPS, NAV, navFor } from '@/app/nav'
import { useStatusStore } from '@/app/statusStore'
import { railCollapsed } from '@/ui/prefs'

/**
 * The side rail (3.3): four groups, one item per screen; 232 px, or 64 px icons only (tablet, or when the
 * user collapses it; stored per browser). Jobs carries the count of running jobs, Health a dot when
 * anything is not OK. Screens that wait for P21 are marked, not hidden.
 */
const props = defineProps<{ forceCollapsed?: boolean }>()
const emit = defineEmits<{ palette: [] }>()
const { t } = useI18n()
const route = useRoute()
const status = useStatusStore()

const collapsed = computed(() => props.forceCollapsed || railCollapsed.value === '1')
const current = computed(() => navFor(route.path)?.key)
const groups = computed(() =>
  GROUPS.map((g) => ({ key: g, items: NAV.filter((n) => n.group === g && !n.hidden) })),
)
const running = computed(() => (status.activeJob ? 1 : 0))
const healthDot = computed(() => status.level === 'attention' || status.level === 'blocked')
const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '')
</script>

<template>
  <aside class="u-rail" :class="{ 'is-collapsed': collapsed }">
    <RouterLink to="/" class="u-rail-brand" :aria-label="t('ui.shell.home')">
      <svg width="28" height="28" viewBox="0 0 36 36" aria-hidden="true">
        <rect width="36" height="36" rx="9" fill="var(--accent)" />
        <rect x="8" y="9" width="20" height="18" rx="3" fill="none" stroke="var(--on-accent)" stroke-width="1.8" />
        <line x1="18" y1="9" x2="18" y2="27" stroke="var(--on-accent)" stroke-width="1.8" />
        <circle cx="18" cy="18" r="3.4" fill="none" stroke="var(--on-accent)" stroke-width="1.8" />
      </svg>
      <span v-if="!collapsed" class="flex flex-col leading-tight">
        <span class="font-bold">{{ t('ui.shell.brand') }}</span>
        <span class="u-small u-muted">{{ t('ui.shell.brandSub') }}</span>
      </span>
    </RouterLink>

    <nav class="u-rail-nav" :aria-label="t('ui.shell.mainNav')">
      <div v-for="g in groups" :key="g.key" class="u-rail-group">
        <p v-if="g.key !== 'start' && !collapsed" :id="`rail-${g.key}`" class="u-caption u-rail-label">{{ t(`ui.nav.group.${g.key}`) }}</p>
        <ul :aria-labelledby="g.key !== 'start' && !collapsed ? `rail-${g.key}` : undefined">
          <li v-for="n in g.items" :key="n.key">
            <RouterLink
              :to="n.to"
              class="u-rail-item"
              :class="{ 'is-active': current === n.key, 'is-planned': n.planned }"
              :aria-current="current === n.key ? 'page' : undefined"
              :title="collapsed ? t(`ui.nav.${n.key}`) : undefined"
              :data-nav="n.key"
            >
              <UiIcon :name="n.icon" />
              <span :class="collapsed ? 'u-sr' : 'flex-1'">{{ t(`ui.nav.${n.key}`) }}</span>
              <span v-if="n.key === 'jobs' && running" class="u-rail-count" :aria-label="t('ui.shell.runningJobs', { n: running })">{{ running }}</span>
              <span v-if="n.key === 'health' && healthDot" class="u-rail-dot" role="img" :aria-label="t('ui.shell.healthWarning')"></span>
              <span v-if="n.planned && !collapsed" class="u-rail-soon">{{ t('ui.nav.soon') }}</span>
              <span v-else-if="n.planned" class="u-sr">{{ t('ui.nav.soon') }}</span>
            </RouterLink>
          </li>
        </ul>
      </div>
    </nav>

    <div class="u-rail-foot">
      <span v-if="!collapsed && status.status" class="u-mono u-muted" style="font-size: 0.75rem">v{{ status.status.version }}</span>
      <button type="button" class="u-btn u-btn-ghost u-btn-sm" :aria-label="t('ui.shell.search')" @click="emit('palette')">
        <UiIcon name="search" :size="14" /><kbd v-if="!collapsed" class="u-kbd">{{ isMac ? '⌘K' : 'Ctrl K' }}</kbd>
      </button>
      <button
        v-if="!forceCollapsed"
        type="button"
        class="u-btn u-btn-ghost u-btn-sm u-btn-icon"
        :aria-label="collapsed ? t('ui.shell.expandRail') : t('ui.shell.collapseRail')"
        :aria-pressed="collapsed"
        @click="railCollapsed = collapsed ? '0' : '1'"
      >
        <UiIcon name="rail" :size="16" />
      </button>
    </div>
  </aside>
</template>

<style>
.u-rail {
  position: sticky;
  top: 0;
  height: 100vh;
  width: var(--rail-w);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
  padding: var(--sp-5) var(--sp-4);
  border-right: 1px solid var(--border);
  background: var(--bg);
  overflow-y: auto;
}
.u-rail.is-collapsed {
  width: 64px;
  padding: var(--sp-5) var(--sp-3);
  align-items: center;
}
.u-app a.u-rail-brand {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  padding: 0 var(--sp-2);
  color: var(--text);
  text-decoration: none;
}
.u-rail-nav {
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
  flex: 1;
}
.u-rail-nav ul {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.u-rail-label {
  margin: 0 0 var(--sp-2);
  padding: 0 var(--sp-3);
}
.u-app a.u-rail-item {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  min-height: 36px;
  padding: 0 var(--sp-3);
  border-radius: var(--r-control);
  color: var(--text-2);
  font-weight: 500;
  text-decoration: none;
  white-space: nowrap;
}
.u-rail.is-collapsed a.u-rail-item {
  justify-content: center;
  min-height: 44px;
  min-width: 44px;
  padding: 0;
  position: relative;
}
.u-app a.u-rail-item:hover {
  background: var(--surface-2);
}
.u-app a.u-rail-item.is-active {
  background: var(--accent-soft);
  color: var(--text);
  font-weight: 600;
}
.u-app a.u-rail-item.is-planned {
  color: var(--muted);
}
.u-rail-count {
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border-radius: var(--r-pill);
  background: var(--info-bg);
  color: var(--info-fg);
  font-size: 0.75rem;
  font-weight: 600;
  text-align: center;
  line-height: 20px;
}
.u-rail-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--warn-fg);
}
.u-rail.is-collapsed .u-rail-count,
.u-rail.is-collapsed .u-rail-dot {
  position: absolute;
  top: 4px;
  right: 4px;
}
.u-rail-soon {
  font-size: 0.6875rem;
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: var(--r-pill);
  padding: 0 6px;
}
.u-rail-foot {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2);
}
.u-rail.is-collapsed .u-rail-foot {
  flex-direction: column;
}
.u-kbd {
  font-family: 'Geist Mono', ui-monospace, monospace;
  font-size: 0.6875rem;
  padding: 1px 5px;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--muted);
}
</style>
