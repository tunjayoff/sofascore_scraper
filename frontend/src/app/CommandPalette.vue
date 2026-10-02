<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useId } from 'vue'
import { useRouter, type RouteLocationRaw } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon, { type UiIconName } from '@/ui/UiIcon.vue'
import { trapTab } from '@/ui/focus'
import { v1 } from '@/api/v1/client'
import type { Job } from '@/api/v1/schema'
import { NAV } from '@/app/nav'
import { jobKindText, jobTarget } from '@/screens/jobs/jobText'

/**
 * Quick search, `Ctrl K` / `⌘ K` (3.3, decision 20). It searches stored data only and never sends a request
 * to SofaScore: today the screens, the recent jobs and an event or job id; follows and tournaments by name
 * join when P21 has their routes (the list says so).
 */
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()
const router = useRouter()

const query = ref('')
const input = ref<HTMLInputElement | null>(null)
const root = ref<HTMLElement | null>(null)
const listId = useId()
const active = ref(0)
const jobs = ref<Job[]>([])
let returnTo: HTMLElement | null = null

type Hit = { id: string; label: string; hint?: string; icon: UiIconName; to: RouteLocationRaw }

const hits = computed<Hit[]>(() => {
  const q = query.value.trim().toLowerCase()
  const out: Hit[] = []
  for (const n of NAV) {
    if (n.hidden) continue
    const label = t(`ui.nav.${n.key}`)
    if (!q || label.toLowerCase().includes(q)) out.push({ id: `nav-${n.key}`, label, hint: t('ui.palette.screen'), icon: n.icon, to: n.to })
  }
  if (/^\d{1,12}$/.test(q)) out.unshift({ id: `event-${q}`, label: t('ui.palette.openEvent', { id: q }), hint: t('ui.nav.events'), icon: 'events', to: `/events/${q}` })
  if (/^[0-9a-hjkmnp-tv-z]{26}$/i.test(q)) out.unshift({ id: `job-${q}`, label: t('ui.palette.openJob', { id: q.toUpperCase() }), hint: t('ui.nav.jobs'), icon: 'jobs', to: `/jobs/${q.toUpperCase()}` })
  for (const j of jobs.value) {
    const label = `${jobKindText(j.kind)} · ${jobTarget(j)}`
    if (q && (label.toLowerCase().includes(q) || j.id.toLowerCase().startsWith(q))) out.push({ id: `recent-${j.id}`, label, hint: j.id, icon: 'jobs', to: `/jobs/${j.id}` })
  }
  return out.slice(0, 12)
})

function go(hit: Hit | undefined) {
  if (!hit) return
  emit('close')
  void router.push(hit.to)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.stopPropagation()
    emit('close')
  } else if (e.key === 'ArrowDown') {
    e.preventDefault()
    active.value = Math.min(hits.value.length - 1, active.value + 1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    active.value = Math.max(0, active.value - 1)
  } else if (e.key === 'Enter') {
    e.preventDefault()
    go(hits.value[active.value])
  } else trapTab(e, root.value)
}

onMounted(() => {
  returnTo = document.activeElement as HTMLElement | null
  void nextTick(() => input.value?.focus())
  // The recent jobs, once per opening; a failure leaves the other results
  v1.jobs({ limit: 20 })
    .then((r) => (jobs.value = r.data))
    .catch(() => {})
})
onUnmounted(() => {
  if (returnTo && document.contains(returnTo)) returnTo.focus()
})
</script>

<template>
  <div class="u-overlay" @mousedown.self="emit('close')">
    <div ref="root" role="dialog" aria-modal="true" :aria-label="t('ui.palette.title')" class="u-pop u-palette" @keydown="onKeydown">
      <div class="u-palette-input">
        <UiIcon name="search" />
        <input
          ref="input"
          v-model="query"
          class="flex-1"
          role="combobox"
          aria-autocomplete="list"
          :aria-expanded="hits.length > 0"
          :aria-controls="listId"
          :aria-activedescendant="hits[active] ? `${listId}-${hits[active].id}` : undefined"
          :aria-label="t('ui.palette.label')"
          :placeholder="t('ui.palette.placeholder')"
          autocomplete="off"
          spellcheck="false"
          @input="active = 0"
        />
        <kbd class="u-kbd">Esc</kbd>
      </div>
      <ul :id="listId" role="listbox" :aria-label="t('ui.palette.results')" class="u-palette-list">
        <li
          v-for="(hit, i) in hits"
          :id="`${listId}-${hit.id}`"
          :key="hit.id"
          role="option"
          :aria-selected="i === active"
          class="u-palette-hit"
          @mousemove="active = i"
          @click="go(hit)"
        >
          <UiIcon :name="hit.icon" :size="16" />
          <span class="flex-1 truncate">{{ hit.label }}</span>
          <span v-if="hit.hint" class="u-small u-muted truncate max-w-[40%]">{{ hit.hint }}</span>
        </li>
        <li v-if="!hits.length" class="u-palette-empty u-muted">{{ t('ui.palette.none') }}</li>
      </ul>
      <p class="m-0 u-small u-muted u-palette-note">{{ t('ui.palette.note') }}</p>
    </div>
  </div>
</template>

<style>
.u-palette {
  width: 100%;
  max-width: 560px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.u-palette-input {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: 0 var(--sp-5);
  min-height: 52px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
}
.u-palette-input input {
  height: 48px;
  border: 0;
  background: transparent;
  color: var(--text);
  font: inherit;
  font-size: 0.9375rem;
}
.u-palette-input input:focus {
  outline: none;
}
.u-palette-list {
  margin: 0;
  padding: var(--sp-2);
  list-style: none;
  max-height: 50vh;
  overflow-y: auto;
}
.u-palette-hit {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  min-height: 40px;
  padding: 0 var(--sp-3);
  border-radius: var(--r-control);
  cursor: pointer;
}
.u-palette-hit[aria-selected='true'] {
  background: var(--accent-soft);
}
.u-palette-empty {
  padding: var(--sp-4);
}
.u-palette-note {
  padding: var(--sp-3) var(--sp-5);
  border-top: 1px solid var(--line);
}
</style>
