<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useId, watch } from 'vue'
import { useRouter, type RouteLocationRaw } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon, { type UiIconName } from '@/ui/UiIcon.vue'
import { trapTab } from '@/ui/focus'
import { v1 } from '@/api/v1/client'
import type { FollowRecord, Job, TournamentHit } from '@/api/v1/schema'
import { NAV } from '@/app/nav'
import { jobKindText, jobTarget, noteFollowNames } from '@/screens/jobs/jobText'
import { loadTournaments } from '@/screens/events/eventText'
import { MIN_CHARS, noteFollows, normalize, rankLocal, useSuggest } from '@/app/suggest'
import { loadSports, sportName } from '@/app/sports'
import { hitPlace, hitTraits, kindIcon, playerTeam } from '@/screens/follows/followText'

/**
 * Quick search, `Ctrl K` / `⌘ K` (3.3, decision 20). It searches stored data: the actions (Add league,
 * Back up, Export, Settings, Help; FX-14a), the screens, the follows and the stored tournaments by name, an
 * event or job id, and the recent jobs. The follows and stored tournaments are ranked as the follow editor's
 * suggestions are (FX-28, `rankLocal`: names or words that start with the text first, at most five). From 2
 * characters on a "On SofaScore" section suggests SofaScore's
 * leagues, teams and players while typing (FX-20, `useSuggest`: one search after a 350 ms pause, a newer
 * keystroke cancels the older one, answers kept for the page and 10 minutes on the server). Enter on one
 * opens the follow editor with it filled in (its follow page when it is added already); the last entry,
 * "Search SofaScore: '<text>'", opens the editor with the text.
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
const tournaments = ref<TournamentHit[]>([])
let returnTo: HTMLElement | null = null

// Tournaments of the stored catalog by name, asked for while typing (debounced); never SofaScore
// (FX-28: katalog önerileri, sunucu sıralar: adı ya da bir sözcüğü metinle başlayanlar önce; yalnızca turnuvalar)
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
    v1.suggestCatalog({ q, limit: 20 }, controller.signal)
      .then((hits) => (tournaments.value = hits.filter((h) => h.kind === 'tournament')))
      .catch(() => {})
  }, 200)
})

type Hit = { id: string; label: string; hint?: string; icon: UiIconName; to?: RouteLocationRaw; run?: () => void; added?: boolean }
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
  // the address /health is the server's own check (JSON); this screen is /system/health (FX-14b)
  {
    id: 'act-health',
    label: t('ui.nav.health'),
    icon: 'health',
    to: '/system/health',
    words: ['sağlık', 'saglik', 'health', 'durum', 'status', 'bağlantı', 'connection', 'sorun', 'problem', 'hata', 'error'],
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
    if (n.hidden || n.key === 'settings' || n.key === 'health') continue
    const label = t(`ui.nav.${n.key}`)
    if (!q || lower(label).includes(q)) out.push({ id: `nav-${n.key}`, label, hint: t('ui.palette.screen'), icon: n.icon, to: n.to })
  }
  if (/^\d{1,12}$/.test(q)) out.unshift({ id: `event-${q}`, label: t('ui.palette.openEvent', { id: q }), hint: t('ui.nav.events'), icon: 'events', to: `/events/${q}` })
  if (/^[0-9a-hjkmnp-tv-z]{26}$/i.test(q)) out.unshift({ id: `job-${q}`, label: t('ui.palette.openJob', { id: q.toUpperCase() }), hint: t('ui.nav.jobs'), icon: 'jobs', to: `/jobs/${q.toUpperCase()}` })
  if (q) {
    // takipler ve kayıtlı turnuvalar, takip düzenleyicisinin önerileri gibi sıralanır (FX-28): adı ya da bir
    // sözcüğü metinle başlayanlar önce, sözcük ortasında geçenler yalnızca başka yoksa, en çok beş
    const followed = new Set(follows.value.filter((f) => f.kind === 'tournament').map((f) => f.entity_id))
    const names: Hit[] = [
      ...[...follows.value]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((f): Hit => ({ id: `follow-${f.id}`, label: f.name, hint: t('ui.nav.follows'), icon: 'follows', to: `/follows/${f.kind}/${f.entity_id}` })),
      ...tournaments.value
        .filter((tour) => !followed.has(tour.id) && tour.name)
        .map((tour): Hit => ({ id: `tournament-${tour.id}`, label: tour.name, hint: t('ui.palette.tournament'), icon: 'events', to: { path: '/events', query: { tournament: String(tour.id) } } })),
    ]
    out.push(...rankLocal(names, (h) => h.label, q))
  }
  for (const j of jobs.value) {
    const label = `${jobKindText(j.kind, j.spec)} · ${jobTarget(j)}`
    if (q && (lower(label).includes(q) || j.id.toLowerCase().startsWith(q))) out.push({ id: `recent-${j.id}`, label, hint: j.id, icon: 'jobs', to: `/jobs/${j.id}` })
  }
  return out.slice(0, 12)
})

// ---- SofaScore, while typing (FX-20) ----
const remote = useSuggest(query, { local: false, follows })
const asksSofascore = computed(() => {
  const text = normalize(query.value)
  return text.length >= MIN_CHARS && !/^\d+$/.test(text)
})
const remoteHits = computed<Hit[]>(() => {
  if (!asksSofascore.value) return []
  const text = normalize(query.value)
  const found: Hit[] = remote.flat.value.map((s) => {
    const h = s.hit
    const hint = [t(`ui.follows.kind.${s.group}`), h.sport ? sportName(h.sport) : '', hitPlace(h), ...hitTraits(h), playerTeam(h.team) ?? '', s.twin ? t('ui.suggest.number', { id: h.id }) : ''].filter(Boolean).join(' · ')
    const to: RouteLocationRaw = s.followed
      ? `/follows/${s.kind}/${h.id}`
      : { path: '/follows/new', query: { kind: s.kind, id: String(h.id), name: h.name, ...(h.sport ? { sport: h.sport } : {}) } }
    return { id: `ss-${s.kind}-${h.id}`, label: h.name, hint, icon: kindIcon(s.group), to, added: s.followed }
  })
  // every result of the text in the follow editor (where the kept answer costs nothing)
  found.push({ id: 'sofascore', label: t('ui.palette.searchSofascore', { q: text }), hint: t('ui.palette.searchSofascoreHint'), icon: 'external', to: { path: '/follows/new', query: { q: text } } })
  return found
})
const remoteStatus = computed(() => {
  if (!asksSofascore.value) return ''
  if (remote.pending.value) return t('ui.suggest.searching')
  if (remote.error.value) return t('ui.palette.sofascoreFailed')
  if (remote.current.value && !remote.flat.value.length) return t('ui.palette.sofascoreNone')
  return ''
})
/** Every option in the order shown: the stored results, then SofaScore's (the arrow keys walk both). */
const allHits = computed(() => [...hits.value, ...remoteHits.value])

function scrollToActive() {
  const hit = allHits.value[active.value]
  if (hit) void nextTick(() => document.getElementById(`${listId}-${hit.id}`)?.scrollIntoView?.({ block: 'nearest' }))
}
// the list grows when SofaScore answers; a cursor past its end comes back
watch(
  () => allHits.value.length,
  (n) => {
    if (active.value >= n) active.value = Math.max(0, n - 1)
  },
)

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
    active.value = Math.min(allHits.value.length - 1, active.value + 1)
    scrollToActive()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    active.value = Math.max(0, active.value - 1)
    scrollToActive()
  } else if (e.key === 'Enter') {
    e.preventDefault()
    go(allHits.value[active.value])
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
    .then((r) => {
      follows.value = r.data
      noteFollowNames(r.data)
      noteFollows(r.data)
    })
    .catch(() => {})
  // names for the leagues of the recent jobs
  void loadTournaments()
  // the registry says which sports list their players as teams (B1): such a hit is shown as a player
  void loadSports().catch(() => {})
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
          :aria-expanded="allHits.length > 0"
          :aria-controls="listId"
          :aria-activedescendant="allHits[active] ? `${listId}-${allHits[active].id}` : undefined"
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
        <li v-if="asksSofascore" role="none">
          <div role="group" :aria-labelledby="`${listId}-ss`" data-testid="palette-sofascore" :aria-busy="remote.pending.value">
          <div :id="`${listId}-ss`" role="presentation" class="u-palette-group">
            {{ t('ui.palette.sofascore') }}<span v-if="remoteStatus" class="u-palette-group-status"> · {{ remoteStatus }}</span>
          </div>
          <ul role="none" class="m-0 p-0 list-none">
            <li
              v-for="(hit, j) in remoteHits"
              :id="`${listId}-${hit.id}`"
              :key="hit.id"
              role="option"
              :aria-selected="hits.length + j === active"
              class="u-palette-hit"
              :data-hit="hit.id"
              @mousemove="active = hits.length + j"
              @click="go(hit)"
            >
              <UiIcon :name="hit.icon" :size="16" />
              <span class="flex-1 truncate">{{ hit.label }}</span>
              <span v-if="hit.added" class="u-small u-palette-added">{{ t('ui.followEditor.alreadyFollowed') }}</span>
              <span v-if="hit.hint" class="u-small u-muted truncate max-w-[40%]">{{ hit.hint }}</span>
            </li>
          </ul>
          </div>
        </li>
        <li v-if="!allHits.length" class="u-palette-empty u-muted">{{ t('ui.palette.none') }}</li>
      </ul>
      <p class="u-sr" aria-live="polite">{{ remoteStatus }}</p>
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
.u-palette-group {
  padding: var(--sp-4) var(--sp-3) var(--sp-2);
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--muted);
}
.u-palette-group-status {
  font-weight: 400;
  letter-spacing: 0;
  text-transform: none;
}
.u-palette-added {
  color: var(--ok-fg);
  white-space: nowrap;
}
.u-palette-empty {
  padding: var(--sp-4);
}
.u-palette-note {
  padding: var(--sp-3) var(--sp-5);
  border-top: 1px solid var(--line);
}
</style>
