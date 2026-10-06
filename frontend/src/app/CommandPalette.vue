<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useId, watch } from 'vue'
import { useRouter, type RouteLocationRaw } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon, { type UiIconName } from '@/ui/UiIcon.vue'
import { trapTab } from '@/ui/focus'
import { v1 } from '@/api/v1/client'
import type { FollowRecord, Job, TournamentRecord } from '@/api/v1/schema'
import { NAV } from '@/app/nav'
import { jobKindText, jobTarget } from '@/screens/jobs/jobText'
import { loadTournaments } from '@/screens/events/eventText'

/**
 * Quick search, `Ctrl K` / `⌘ K` (3.3, decision 20). It searches stored data: the actions (Add league,
 * Back up, Export, Settings, Help; FX-14a), the screens, the follows and the stored tournaments by name, an
 * event or job id, and the recent jobs. It sends nothing to SofaScore by itself: when nothing stored
 * matches, it offers "Search SofaScore: '<text>'", which opens the follow editor with the text and runs
 * that one search, only when the user picks it.
 */
const emit = defineEmits<{ close: []; help: [] }>()
const { t, locale } = useI18n()
const router = useRouter()

const query = ref('')
const input = ref<HTMLInputElement | null>(null)
const root = ref<HTMLElement | null>(null)
const listId = useId()
const active = ref(0)
const jobs = ref<Job[]>([])
const follows = ref<FollowRecord[]>([])
const tournaments = ref<TournamentRecord[]>([])
let returnTo: HTMLElement | null = null

// Tournaments of the stored catalog by name, asked for while typing (debounced); never SofaScore
let timer: ReturnType<typeof setTimeout> | null = null
let controller: AbortController | null = null
watch(query, (value) => {
  if (timer) clearTimeout(timer)
  const q = value.trim()
  if (q.length < 2 || /^\d+$/.test(q)) {
    tournaments.value = []
    return
  }
  timer = setTimeout(() => {
    controller?.abort()
    controller = new AbortController()
    v1.tournaments({ q, limit: 6 }, controller.signal)
      .then((r) => (tournaments.value = r.data))
      .catch(() => {})
  }, 200)
})

type Hit = { id: string; label: string; hint?: string; icon: UiIconName; to?: RouteLocationRaw; run?: () => void }
type Action = Hit & { words: string[] }

const lower = (s: string) => s.toLocaleLowerCase(locale.value)

/** The actions, found by their label or by a word in either language ("lig ekle", "add league", "backup"). */
const actions = computed<Action[]>(() => [
  { id: 'act-add', label: t('ui.shell.addLeague'), icon: 'plus', to: '/follows/new', words: ['lig', 'ekle', 'add', 'league', 'takip', 'follow', 'turnuva', 'tournament', 'yeni', 'new', 'indir', 'download'] },
  { id: 'act-backup', label: t('ui.palette.backup'), icon: 'backups', to: { path: '/backups', query: { new: '1' } }, words: ['yedek', 'backup'] },
  { id: 'act-export', label: t('ui.palette.export'), icon: 'exports', to: { path: '/exports', query: { new: '1' } }, words: ['dışa', 'aktar', 'export', 'csv', 'json'] },
  { id: 'act-settings', label: t('ui.nav.settings'), icon: 'settings', to: '/settings', words: ['ayar', 'setting', 'dil', 'language', 'proxy', 'vekil'] },
  {
    id: 'act-help',
    label: t('ui.menu.help'),
    icon: 'help',
    run: () => emit('help'),
    words: ['yardım', 'help', 'nasıl', 'how', 'sözlük', 'glossary', 'canlı', 'live', 'watch', 'izleme', '?'],
  },
])

/** The label holds the text, or every word typed starts one of the action's words. */
function actionMatches(a: Action, q: string) {
  return lower(a.label).includes(q) || q.split(/\s+/).every((part) => a.words.some((w) => w.startsWith(part)))
}

const hits = computed<Hit[]>(() => {
  const q = lower(query.value.trim())
  const out: Hit[] = []
  for (const a of actions.value) if (!q || actionMatches(a, q)) out.push({ ...a, hint: t('ui.palette.action') })
  for (const n of NAV) {
    if (n.hidden || n.key === 'settings') continue
    const label = t(`ui.nav.${n.key}`)
    if (!q || lower(label).includes(q)) out.push({ id: `nav-${n.key}`, label, hint: t('ui.palette.screen'), icon: n.icon, to: n.to })
  }
  if (/^\d{1,12}$/.test(q)) out.unshift({ id: `event-${q}`, label: t('ui.palette.openEvent', { id: q }), hint: t('ui.nav.events'), icon: 'events', to: `/events/${q}` })
  if (/^[0-9a-hjkmnp-tv-z]{26}$/i.test(q)) out.unshift({ id: `job-${q}`, label: t('ui.palette.openJob', { id: q.toUpperCase() }), hint: t('ui.nav.jobs'), icon: 'jobs', to: `/jobs/${q.toUpperCase()}` })
  if (q) {
    for (const f of follows.value)
      if (lower(f.name).includes(q)) out.push({ id: `follow-${f.id}`, label: f.name, hint: t('ui.nav.follows'), icon: 'follows', to: `/follows/${f.kind}/${f.entity_id}` })
    const followed = new Set(follows.value.filter((f) => f.kind === 'tournament').map((f) => f.entity_id))
    for (const tour of tournaments.value)
      if (!followed.has(tour.id) && lower(tour.name ?? '').includes(q))
        out.push({ id: `tournament-${tour.id}`, label: tour.name ?? `#${tour.id}`, hint: t('ui.palette.tournament'), icon: 'events', to: { path: '/events', query: { tournament: String(tour.id) } } })
  }
  for (const j of jobs.value) {
    const label = `${jobKindText(j.kind)} · ${jobTarget(j)}`
    if (q && (lower(label).includes(q) || j.id.toLowerCase().startsWith(q))) out.push({ id: `recent-${j.id}`, label, hint: j.id, icon: 'jobs', to: `/jobs/${j.id}` })
  }
  // Nothing matches: offer the search at SofaScore, sent only when the user picks it (FX-14a)
  const text = query.value.trim()
  if (!out.length && text.length >= 2 && !/^\d+$/.test(text)) {
    const sofascore: Hit = {
      id: 'sofascore',
      label: t('ui.palette.searchSofascore', { q: text }),
      hint: t('ui.palette.searchSofascoreHint'),
      icon: 'external',
      to: { path: '/follows/new', query: { q: text } },
    }
    return [...out.slice(0, 11), sofascore]
  }
  return out.slice(0, 12)
})

function go(hit: Hit | undefined) {
  if (!hit) return
  emit('close')
  if (hit.run) hit.run()
  else if (hit.to) void router.push(hit.to)
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
  v1.follows()
    .then((r) => (follows.value = r.data))
    .catch(() => {})
  // names for the leagues of the recent jobs
  void loadTournaments()
})
onUnmounted(() => {
  if (timer) clearTimeout(timer)
  controller?.abort()
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
