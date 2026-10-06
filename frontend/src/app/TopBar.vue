<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import UiMenu, { type MenuItem } from '@/ui/UiMenu.vue'
import { HEALTH } from '@/ui/status'
import { useStatusStore } from '@/app/statusStore'
import { tokenInUse } from '@/app/session'
import { signOut } from '@/app/auth'
import { jobKindText, jobPercent } from '@/screens/jobs/jobText'
import { setLocale, type Lang } from '@/i18n'
import { setTheme, themePref, type ThemePref } from '@/lib/theme'
import { density, type Density } from '@/ui/prefs'
import { pct } from '@/ui/time'

/**
 * The top bar (3.3): the page title; "Add league", always there (FX-14a; icon only on phone); the health
 * pill (one word and a colour from `/status`, opens Health); the job pill while a job runs (opens its
 * detail); the menu with language, theme, density, quick search, the keyboard shortcuts, Help and, when a
 * token is in use, Sign out.
 */
defineProps<{ title: string; compact?: boolean }>()
const emit = defineEmits<{ shortcuts: []; palette: []; help: [] }>()
const { t, locale } = useI18n()
const route = useRoute()
const status = useStatusStore()

const health = computed(() => HEALTH[status.level])
const job = computed(() => status.activeJob)
const jobLabel = computed(() => {
  if (!job.value) return ''
  const p = jobPercent(job.value)
  return p == null ? jobKindText(job.value.kind, job.value.spec) : `${jobKindText(job.value.kind, job.value.spec)} · ${pct(p)}`
})

const menu = computed<MenuItem[]>(() => [
  { kind: 'label', key: 'l-lang', label: t('ui.menu.language') },
  { kind: 'radio', key: 'lang:en', label: 'English', checked: locale.value === 'en' },
  { kind: 'radio', key: 'lang:tr', label: 'Türkçe', checked: locale.value === 'tr' },
  { kind: 'separator', key: 's1' },
  { kind: 'label', key: 'l-theme', label: t('ui.menu.theme') },
  ...(['system', 'light', 'dark'] as const).map((p) => ({ kind: 'radio' as const, key: `theme:${p}`, label: t(`ui.prefs.theme.${p}`), checked: themePref.value === p })),
  { kind: 'separator', key: 's2' },
  { kind: 'label', key: 'l-density', label: t('ui.menu.density') },
  ...(['comfortable', 'compact'] as const).map((d) => ({ kind: 'radio' as const, key: `density:${d}`, label: t(`ui.prefs.density.${d}`), checked: density.value === d })),
  { kind: 'separator', key: 's3' },
  { key: 'search', label: t('ui.shell.search'), icon: 'search', hint: 'Ctrl K' },
  { key: 'shortcuts', label: t('ui.menu.shortcuts'), icon: 'keyboard', hint: '?' },
  { key: 'help', label: t('ui.menu.help'), icon: 'help' },
  ...(tokenInUse.value ? [{ key: 'signout', label: t('ui.menu.signOut'), icon: 'signOut' as const, danger: true }] : []),
])

function onMenu(key: string) {
  const [kind, value] = key.split(':')
  if (kind === 'lang') setLocale(value as Lang)
  else if (kind === 'theme') setTheme(value as ThemePref)
  else if (kind === 'density') density.value = value as Density
  else if (key === 'shortcuts') emit('shortcuts')
  else if (key === 'search') emit('palette')
  else if (key === 'help') emit('help')
  else if (key === 'signout') void signOut()
}
</script>

<template>
  <header class="u-topbar">
    <p class="u-h3 flex-1 truncate m-0" aria-hidden="true">{{ title }}</p>
    <RouterLink
      v-if="route.path !== '/follows/new'"
      to="/follows/new"
      class="u-btn u-btn-primary"
      :class="compact ? 'u-btn-icon' : 'u-btn-sm'"
      :aria-label="compact ? t('ui.shell.addLeague') : undefined"
      data-testid="topbar-add-league"
    >
      <UiIcon name="plus" :size="16" /><span v-if="!compact">{{ t('ui.shell.addLeague') }}</span>
    </RouterLink>
    <RouterLink
      to="/system/health"
      class="u-pill"
      :class="`u-pill-${health.tone}`"
      :aria-label="t('ui.shell.healthPill', { state: t(health.key) })"
      data-testid="health-pill"
      :data-level="status.level"
    >
      <UiIcon :name="health.icon" :size="14" /><span v-if="!compact">{{ t(health.key) }}</span>
    </RouterLink>
    <RouterLink v-if="job" :to="`/jobs/${job.id}`" class="u-pill u-pill-info" data-testid="job-pill" :aria-label="t('ui.shell.jobPill', { job: jobLabel })">
      <span class="u-spinner" aria-hidden="true" style="width: 11px; height: 11px; border-width: 1.5px"></span><span>{{ jobLabel }}</span>
    </RouterLink>
    <UiMenu :label="t('ui.menu.label')" icon="more" icon-only button-class="u-btn u-btn-ghost u-btn-sm" :items="menu" @select="onMenu" />
  </header>
</template>

<style>
.u-topbar {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  min-height: 56px;
  padding: 0 var(--sp-5);
  border-bottom: 1px solid var(--border);
  background: var(--bg);
}
@media (min-width: 1024px) {
  .u-topbar {
    padding: 0 var(--sp-7);
  }
}
.u-pill,
.u-app a.u-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  padding: 0 var(--sp-4);
  border-radius: var(--r-pill);
  font-size: 0.8125rem;
  font-weight: 600;
  text-decoration: none;
  white-space: nowrap;
}
@media (max-width: 1023px) {
  .u-pill,
  .u-app a.u-pill {
    min-height: 44px;
    min-width: 44px;
    justify-content: center;
  }
}
.u-app a.u-pill-ok {
  background: var(--ok-bg);
  color: var(--ok-fg);
}
.u-app a.u-pill-warn {
  background: var(--warn-bg);
  color: var(--warn-fg);
}
.u-app a.u-pill-danger {
  background: var(--danger-bg);
  color: var(--danger);
}
.u-app a.u-pill-neutral {
  background: var(--neutral-bg);
  color: var(--neutral-fg);
}
.u-app a.u-pill-info {
  background: var(--info-bg);
  color: var(--info-fg);
}
</style>
