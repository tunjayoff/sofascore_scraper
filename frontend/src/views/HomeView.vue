<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter, RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { motion, AnimatePresence } from 'motion-v'
import { apiGet, apiSend, type Season, type JobRow } from '@/api/client'
import { useLeaguesStore } from '@/stores/leagues'
import { useScrapeStore } from '@/stores/scrape'
import { useMotionPrefs } from '@/composables/useMotionPrefs'
import LeagueCard from '@/components/LeagueCard.vue'
import { jobActionParts, isGenericJobTask } from '@/lib/jobLabel'

const { t } = useI18n()
const router = useRouter()
const leagues = useLeaguesStore()
const scrape = useScrapeStore()
const { stagger, reduce } = useMotionPrefs()

const step = ref(1)
const searchQuery = ref('')
const searchResults = ref<{ id: number; name: string; country?: string }[]>([])
const searching = ref(false)
const selectedLeague = ref<number | null>(null)
const seasons = ref<Season[]>([])
const selectedSeasons = ref<number[]>([])
const loadingSeasons = ref(false)
const recentJobs = ref<JobRow[]>([])
const statsLine = ref('')
const downloadOpen = ref(false)
const err = ref('')
/** Ignores stale season responses when switching leagues quickly. */
let seasonsLoadGen = 0

const returning = computed(() => !leagues.isEmpty)

async function searchRemote() {
  if (searchQuery.value.trim().length < 2) {
    searchResults.value = []
    return
  }
  searching.value = true
  try {
    searchResults.value = await apiGet(`/api/leagues/search-remote?q=${encodeURIComponent(searchQuery.value.trim())}`)
  } catch (e) {
    err.value = String(e)
  } finally {
    searching.value = false
  }
}

async function addLeague(id: number, name: string) {
  err.value = ''
  try {
    await leagues.add(id, name)
    selectedLeague.value = id
    searchQuery.value = ''
    searchResults.value = []
    step.value = 2
    await loadSeasons(id)
    downloadOpen.value = true
  } catch (e) {
    err.value = String(e)
  }
}

async function loadSeasons(lid: number, opts?: { refreshIfEmpty?: boolean }) {
  const gen = ++seasonsLoadGen
  loadingSeasons.value = true
  seasons.value = []
  selectedSeasons.value = []
  try {
    let data = await apiGet<{ seasons: Season[]; fetched?: boolean }>(`/api/leagues/${lid}/seasons`)
    if (gen !== seasonsLoadGen) return
    let list = normalizeSeasons(data.seasons)
    // Local list empty → pull from SofaScore once so Download can work
    if (!list.length && opts?.refreshIfEmpty !== false) {
      try {
        data = await apiSend<{ seasons: Season[] }>(`/api/leagues/${lid}/seasons/refresh`, 'POST')
        if (gen !== seasonsLoadGen) return
        list = normalizeSeasons(data.seasons)
      } catch (e) {
        if (gen !== seasonsLoadGen) return
        err.value = String(e)
      }
    }
    if (gen !== seasonsLoadGen) return
    seasons.value = list
    // Latest season is often fixtures-only (e.g. PL 26/27); prefer the prior season by default.
    // Preset "latest" still selects seasons[0].
    if (list.length > 1) {
      selectedSeasons.value = [list[1].id]
    } else {
      selectedSeasons.value = list.length ? [list[0].id] : []
    }
  } catch (e) {
    if (gen !== seasonsLoadGen) return
    seasons.value = []
    selectedSeasons.value = []
    err.value = String(e)
  } finally {
    if (gen === seasonsLoadGen) loadingSeasons.value = false
  }
}

function normalizeSeasons(raw: Season[] | undefined): Season[] {
  return (raw || [])
    .map((s) => ({ ...s, id: Number(s.id) }))
    .filter((s) => Number.isFinite(s.id))
}

function preset(kind: 'latest' | 'last3' | 'last5') {
  const n = kind === 'latest' ? 1 : kind === 'last3' ? 3 : 5
  selectedSeasons.value = seasons.value.slice(0, n).map((s) => Number(s.id))
}

function toggleSeason(id: number) {
  const sid = Number(id)
  if (selectedSeasons.value.includes(sid)) {
    selectedSeasons.value = selectedSeasons.value.filter((x) => x !== sid)
  } else {
    selectedSeasons.value = [...selectedSeasons.value, sid]
  }
}

async function startDownload() {
  err.value = ''
  if (!selectedLeague.value) {
    err.value = t('home_select_league_first')
    return
  }
  const seasonIds = selectedSeasons.value.map(Number).filter((n) => Number.isFinite(n))
  if (!seasonIds.length) {
    err.value = t('home_no_seasons')
    return
  }
  try {
    await apiSend('/api/fetch', 'POST', {
      mode: 'full',
      selections: [{ league_id: Number(selectedLeague.value), season_ids: seasonIds }],
    })
    localStorage.setItem(
      'ss_last_fetch',
      JSON.stringify({ league_id: Number(selectedLeague.value), season_ids: seasonIds }),
    )
    scrape.wake()
    scrape.connectSSE()
    step.value = 3
    downloadOpen.value = false
    await loadJobs()
  } catch (e) {
    err.value = String(e)
  }
}

async function repeatLast() {
  const raw = localStorage.getItem('ss_last_fetch')
  if (!raw) return
  try {
    const last = JSON.parse(raw)
    await apiSend('/api/fetch', 'POST', {
      mode: 'full',
      selections: [{ league_id: last.league_id, season_ids: last.season_ids }],
    })
    scrape.wake()
    await loadJobs()
  } catch (e) {
    err.value = String(e)
  }
}

async function missingDetails() {
  if (!selectedLeague.value) {
    err.value = t('home_select_league_first')
    return
  }
  try {
    const data = await apiGet<{ missing: { match_id: number }[] }>(
      `/api/leagues/${selectedLeague.value}/missing-details`,
    )
    const ids = (data.missing || []).map((m) => m.match_id)
    if (!ids.length) {
      err.value = t('home_no_missing_details')
      return
    }
    await apiSend('/api/fetch', 'POST', {
      mode: 'details',
      selections: [{ league_id: selectedLeague.value, season_ids: null, match_ids: ids }],
    })
    scrape.wake()
    await loadJobs()
  } catch (e) {
    err.value = String(e)
  }
}

async function loadJobs() {
  try {
    const data = await apiGet<{ jobs: JobRow[] }>('/api/jobs?limit=5')
    recentJobs.value = data.jobs || []
  } catch {
    recentJobs.value = []
  }
}

async function loadStats() {
  try {
    const s = await apiGet<{
      leagues: number
      matches: number
      details: number
      disk_usage: { formatted_total: string }
    }>('/api/stats/system')
    statsLine.value = t('home_compact_stats', {
      leagues: s.leagues,
      matches: s.matches,
      details: s.details,
      disk: s.disk_usage?.formatted_total || '0',
    })
  } catch {
    statsLine.value = ''
  }
}

function openDownload(lid: number) {
  err.value = ''
  selectedLeague.value = lid
  downloadOpen.value = true
  void loadSeasons(lid, { refreshIfEmpty: true })
}

function leagueName(id?: number) {
  if (id == null) return ''
  return leagues.leagues.find((l) => l.id === id)?.name || `ID ${id}`
}

function jobTitle(j: JobRow) {
  const p = jobActionParts(j)
  const league = leagueName(p.leagueId)
  if (p.kind === 'details') {
    return league
      ? t('home_job_kind_details', { league, n: p.matchCount || '—' })
      : t('home_job_kind_details_short', { n: p.matchCount || '—' })
  }
  if (p.kind === 'seasons') {
    return league
      ? t('home_job_kind_seasons', { league, n: p.seasonCount })
      : t('home_job_kind_seasons_short', { n: p.seasonCount })
  }
  if (p.kind === 'league') return t('home_job_kind_league', { league: league || '—' })
  return t('home_job_kind_generic')
}

function jobStatusText(j: JobRow) {
  const s = String(j.status || '').toLowerCase()
  if (s === 'completed') return t('home_job_status_completed')
  if (s === 'cancelled') return t('home_job_status_cancelled')
  if (s === 'failed') return t('home_job_status_failed')
  if (s === 'running') return t('home_job_status_running')
  return j.status
}

function jobSubtitle(j: JobRow) {
  const task = String(j.current_task || '').trim()
  if (!isGenericJobTask(task)) return `${jobStatusText(j)} · ${task}`
  return `${jobStatusText(j)}${j.progress != null ? ` · ${j.progress}%` : ''}`
}

onMounted(async () => {
  await leagues.load()
  if (!leagues.isEmpty) {
    selectedLeague.value = leagues.leagues[0].id
    await Promise.all([loadJobs(), loadStats(), loadSeasons(leagues.leagues[0].id)])
  }
})

watch(
  () => scrape.state.is_running,
  (running, was) => {
    if (was && !running) {
      void loadJobs()
      void loadStats()
      if (scrape.state.status === 'Completed') step.value = 3
    }
  },
)
</script>

<template>
  <AnimatePresence>
    <motion.div
      v-if="err"
      key="err"
      class="mb-4 p-3.5 rounded-[var(--radius)] bg-rose-50 dark:bg-rose-950/40 border border-rose-200/70 dark:border-rose-500/30 text-rose-700 dark:text-rose-300 text-sm"
      :initial="reduce ? false : { opacity: 0, y: -6 }"
      :animate="{ opacity: 1, y: 0 }"
      :exit="{ opacity: 0 }"
    >
      {{ err }}
    </motion.div>
  </AnimatePresence>

  <!-- First run -->
  <div v-if="!returning" class="space-y-5 max-w-2xl">
    <div>
      <h1 class="page-title mb-2">{{ t('brand_name') }}</h1>
      <p class="text-[var(--muted)] text-base max-w-lg">{{ t('home_subtitle') }}</p>
    </div>

    <motion.div
      class="panel p-5 space-y-4"
      :class="step === 1 ? 'ring-accent' : 'opacity-80'"
      :initial="reduce ? false : { opacity: 0, y: 8 }"
      :animate="{ opacity: 1, y: 0 }"
      :transition="stagger(0)"
    >
      <div class="flex items-center gap-3">
        <span class="step-badge" :class="step === 1 ? '' : 'idle'">1</span>
        <h2 class="display text-lg font-extrabold">{{ t('home_step_add') }}</h2>
      </div>
      <input
        v-model="searchQuery"
        class="field-input text-base"
        :placeholder="t('home_search_placeholder')"
        @input="searchRemote"
      />
      <div v-if="searchResults.length" class="border border-[var(--border)] rounded-[var(--radius)] max-h-64 overflow-y-auto">
        <button
          v-for="r in searchResults"
          :key="r.id"
          type="button"
          class="w-full text-left px-4 py-3 border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-2)]"
          @click="addLeague(r.id, r.name)"
        >
          <span class="font-bold">{{ r.name }}</span>
          <span class="block text-xs text-[var(--muted)] mono mt-0.5">{{ r.country }} · {{ r.id }}</span>
        </button>
      </div>
    </motion.div>

    <motion.div
      class="panel p-5 space-y-4"
      :class="step === 2 ? 'ring-accent' : 'opacity-80'"
      :initial="reduce ? false : { opacity: 0, y: 8 }"
      :animate="{ opacity: 1, y: 0 }"
      :transition="stagger(1)"
    >
      <div class="flex items-center gap-3">
        <span class="step-badge" :class="step >= 2 ? '' : 'idle'">2</span>
        <h2 class="display text-lg font-extrabold">{{ t('home_step_download') }}</h2>
      </div>
      <p v-if="!selectedLeague" class="text-sm text-[var(--muted)]">{{ t('home_select_league_first') }}</p>
      <template v-else>
        <div class="flex flex-wrap gap-2">
          <button type="button" class="btn btn-secondary text-sm" @click="preset('latest')">{{ t('home_preset_latest') }}</button>
          <button type="button" class="btn btn-secondary text-sm" @click="preset('last3')">{{ t('home_preset_last3') }}</button>
        </div>
        <div v-if="loadingSeasons" class="text-sm text-[var(--muted)]">{{ t('home_loading_seasons') }}</div>
        <div v-else class="space-y-1 max-h-56 overflow-y-auto">
          <label
            v-for="s in seasons"
            :key="s.id"
            class="flex items-center gap-3 p-3 rounded-[var(--radius)] hover:bg-[var(--surface-2)] cursor-pointer"
          >
            <input type="checkbox" class="w-4 h-4 accent-[var(--accent)]" :checked="selectedSeasons.includes(Number(s.id))" @change="toggleSeason(Number(s.id))" />
            <span class="font-semibold">{{ s.name || s.year || s.id }}</span>
          </label>
          <p v-if="!seasons.length" class="text-sm text-[var(--muted)]">{{ t('home_no_seasons') }}</p>
        </div>
        <button
          type="button"
          class="btn btn-primary w-full sm:w-auto"
          :disabled="!selectedSeasons.length || scrape.isRunning || loadingSeasons"
          @click="startDownload"
        >
          {{ t('home_download_btn') }}
        </button>
      </template>
    </motion.div>

    <motion.div
      class="panel p-5 space-y-3"
      :class="step >= 3 ? 'ring-accent' : 'opacity-80'"
      :initial="reduce ? false : { opacity: 0, y: 8 }"
      :animate="{ opacity: 1, y: 0 }"
      :transition="stagger(2)"
    >
      <div class="flex items-center gap-3">
        <span class="step-badge" :class="step >= 3 ? '' : 'idle'">3</span>
        <h2 class="display text-lg font-extrabold">{{ t('home_step_done') }}</h2>
      </div>
      <button
        type="button"
        class="btn btn-primary"
        :disabled="step < 3"
        @click="router.push('/matches')"
      >
        {{ t('home_open_matches') }}
      </button>
    </motion.div>
  </div>

  <!-- Returning two-zone desk -->
  <div v-else class="space-y-5">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="page-title mb-2">{{ t('home_workspace_title') }}</h1>
        <p class="text-[var(--muted)]">{{ t('home_workspace_subtitle') }}</p>
      </div>
      <RouterLink v-if="statsLine" to="/advanced/stats" class="chip hover:brightness-95">{{ statsLine }}</RouterLink>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-5 gap-5">
      <!-- Left ~60% -->
      <div class="md:col-span-3 space-y-4">
        <div class="flex flex-wrap gap-2">
          <button type="button" class="btn btn-primary text-sm" :disabled="scrape.isRunning" @click="repeatLast">
            {{ t('home_quick_repeat') }}
          </button>
          <button
            type="button"
            class="btn btn-secondary text-sm"
            :disabled="scrape.isRunning"
            :title="t('home_quick_missing_hint')"
            @click="missingDetails"
          >
            {{ t('home_quick_missing') }}
          </button>
          <button
            type="button"
            class="btn btn-secondary text-sm"
            @click="router.push(selectedLeague ? `/matches?league_id=${selectedLeague}` : '/matches')"
          >
            {{ t('home_quick_matches') }}
          </button>
        </div>

        <h2 class="text-sm font-bold uppercase tracking-wider text-[var(--muted)]">{{ t('home_desk_leagues') }}</h2>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <LeagueCard
            v-for="lg in leagues.leagues"
            :key="lg.id"
            :league="lg"
            :active="selectedLeague === lg.id"
            @select="selectedLeague = lg.id"
            @download="openDownload(lg.id)"
            @matches="router.push(`/matches?league_id=${lg.id}`)"
          />
        </div>

        <AnimatePresence>
          <motion.div
            v-if="downloadOpen"
            key="season-panel"
            class="panel p-5 space-y-4 ring-accent"
            :initial="reduce ? false : { opacity: 0, y: 10 }"
            :animate="{ opacity: 1, y: 0 }"
            :exit="reduce ? undefined : { opacity: 0, y: 6 }"
            :transition="{ type: 'spring', stiffness: 340, damping: 32 }"
          >
            <div class="flex items-center justify-between">
              <div>
                <h2 class="display text-lg font-extrabold">{{ t('home_pick_seasons') }}</h2>
                <p v-if="selectedLeague" class="text-sm text-[var(--muted)] mt-1 mono">
                  {{ leagues.leagues.find((l) => l.id === selectedLeague)?.name || `ID ${selectedLeague}` }}
                </p>
              </div>
              <button type="button" class="btn btn-secondary text-sm min-h-[36px] px-3" @click="downloadOpen = false">×</button>
            </div>
            <div class="flex flex-wrap gap-2">
              <button type="button" class="btn btn-secondary text-sm" @click="preset('latest')">{{ t('home_preset_latest') }}</button>
              <button type="button" class="btn btn-secondary text-sm" @click="preset('last3')">{{ t('home_preset_last3') }}</button>
              <button type="button" class="btn btn-secondary text-sm" @click="preset('last5')">{{ t('home_preset_last5') }}</button>
            </div>
            <div class="space-y-1 max-h-56 overflow-y-auto">
              <label v-for="s in seasons" :key="s.id" class="flex items-center gap-3 p-3 rounded-[var(--radius)] hover:bg-[var(--surface-2)] cursor-pointer">
                <input type="checkbox" class="w-4 h-4 accent-[var(--accent)]" :checked="selectedSeasons.includes(Number(s.id))" @change="toggleSeason(Number(s.id))" />
                <span class="font-semibold">{{ s.name || s.year || s.id }}</span>
              </label>
              <p v-if="!seasons.length && !loadingSeasons" class="text-sm text-[var(--muted)]">{{ t('home_no_seasons') }}</p>
              <p v-if="loadingSeasons" class="text-sm text-[var(--muted)]">{{ t('home_loading_seasons') }}</p>
            </div>
            <button
              type="button"
              class="btn btn-primary"
              :disabled="!selectedSeasons.length || scrape.isRunning || loadingSeasons"
              @click="startDownload"
            >
              {{ t('home_download_btn') }}
            </button>
          </motion.div>
        </AnimatePresence>
      </div>

      <!-- Right ~40% -->
      <div class="md:col-span-2 space-y-4">
        <h2 class="text-sm font-bold uppercase tracking-wider text-[var(--muted)]">{{ t('home_desk_activity') }}</h2>
        <div class="panel p-5">
          <div class="flex items-center justify-between mb-3">
            <h3 class="font-bold">{{ t('home_recent_jobs') }}</h3>
            <RouterLink to="/advanced/jobs" class="text-sm link-accent">{{ t('nav_jobs') }}</RouterLink>
          </div>
          <p v-if="!recentJobs.length" class="text-sm text-[var(--muted)]">{{ t('home_no_jobs') }}</p>
          <ul v-else class="space-y-1">
            <li v-for="j in recentJobs" :key="j.id" class="flex items-center justify-between gap-3 text-sm py-2.5 border-b border-[var(--border)] last:border-0">
              <div class="min-w-0">
                <div class="font-bold truncate">{{ jobTitle(j) }}</div>
                <div class="text-[var(--muted)] truncate">{{ jobSubtitle(j) }}</div>
              </div>
              <button
                v-if="j.status === 'running' && scrape.state.job_id === j.id"
                type="button"
                class="btn btn-danger text-xs min-h-[36px]"
                @click="scrape.cancel()"
              >
                {{ t('home_stop') }}
              </button>
            </li>
          </ul>
        </div>

        <div class="panel p-5 space-y-3">
          <h3 class="font-bold">{{ t('home_step_add') }}</h3>
          <input v-model="searchQuery" class="field-input" :placeholder="t('home_search_placeholder')" @input="searchRemote" />
          <div v-if="searchResults.length" class="border border-[var(--border)] rounded-[var(--radius)] max-h-48 overflow-y-auto">
            <button
              v-for="r in searchResults"
              :key="r.id"
              type="button"
              class="w-full text-left px-4 py-3 border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-2)]"
              @click="addLeague(r.id, r.name)"
            >
              <span class="font-bold">{{ r.name }}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
