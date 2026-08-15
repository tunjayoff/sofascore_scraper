<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { apiGet, type Season } from '@/api/client'
import { useLeaguesStore } from '@/stores/leagues'

type MatchRow = {
  match_id: number | string
  home_team?: string
  away_team?: string
  match_date?: string | number
  home_score?: string | number
  away_score?: string | number
  score?: string
  season_id?: number
}

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const leagues = useLeaguesStore()

const matches = ref<MatchRow[]>([])
const loading = ref(false)
const selectedLeague = ref<string>('all')
const selectedSeason = ref('')
const filterSeasons = ref<Season[]>([])
const dateFilter = ref('')
const pageSize = ref(25)
const currentPage = ref(1)
const totalCount = ref(0)
const showMore = ref(false)

async function loadSeasons() {
  if (selectedLeague.value === 'all') {
    filterSeasons.value = []
    selectedSeason.value = ''
    return
  }
  try {
    const data = await apiGet<{ seasons: Season[] }>(`/api/leagues/${selectedLeague.value}/seasons`)
    filterSeasons.value = data.seasons || []
  } catch {
    filterSeasons.value = []
  }
}

async function fetchMatches() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    params.set('limit', String(pageSize.value))
    params.set('offset', String((currentPage.value - 1) * pageSize.value))
    if (selectedLeague.value !== 'all') params.set('league_id', selectedLeague.value)
    if (selectedSeason.value) params.set('season_id', selectedSeason.value)
    if (dateFilter.value) params.set('date', dateFilter.value)
    const data = await apiGet<{ items: MatchRow[]; total: number }>(`/api/matches?${params}`)
    matches.value = data.items || []
    totalCount.value = data.total ?? matches.value.length
  } catch {
    matches.value = []
    totalCount.value = 0
  } finally {
    loading.value = false
  }
}

function formatDate(v: string | number | undefined) {
  if (v == null || v === '') return '-'
  if (typeof v === 'number' && v > 0 && v < 1e12) {
    return new Date(v * 1000).toLocaleString()
  }
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString()
}

function scoreOf(m: MatchRow) {
  if (m.score) return m.score
  const h = m.home_score ?? ''
  const a = m.away_score ?? ''
  if (h === '' && a === '') return '–'
  return `${h}–${a}`
}

onMounted(async () => {
  await leagues.load()
  const lg = route.query.league_id
  if (typeof lg === 'string' && lg) selectedLeague.value = lg
  await loadSeasons()
  await fetchMatches()
})

watch([selectedLeague, selectedSeason, dateFilter, pageSize, currentPage], async ([lg], [prevLg]) => {
  if (lg !== prevLg) {
    currentPage.value = 1
    await loadSeasons()
  }
  await fetchMatches()
})
</script>

<template>
  <div class="mb-6 flex flex-wrap items-end justify-between gap-4">
    <div>
      <h1 class="page-title mb-2">{{ t('matches_title') }}</h1>
      <p class="text-[var(--muted)]">{{ t('matches_subtitle') }}</p>
    </div>
    <RouterLink to="/" class="btn btn-primary text-sm">{{ t('home_go_download') }}</RouterLink>
  </div>

  <div class="panel p-5 mb-6 space-y-4">
    <div class="flex flex-wrap gap-3 items-end">
      <div>
        <label class="block text-xs font-bold uppercase tracking-wider text-[var(--muted)] mb-1">{{ t('nav_leagues') }}</label>
        <select v-model="selectedLeague" class="field-input min-w-[12rem]">
          <option value="all">All</option>
          <option v-for="lg in leagues.leagues" :key="lg.id" :value="String(lg.id)">{{ lg.name }}</option>
        </select>
      </div>
      <div>
        <label class="block text-xs font-bold uppercase tracking-wider text-[var(--muted)] mb-1">Season</label>
        <select v-model="selectedSeason" class="field-input min-w-[10rem]" :disabled="selectedLeague === 'all'">
          <option value="">All</option>
          <option v-for="s in filterSeasons" :key="s.id" :value="String(s.id)">{{ s.name || s.year || s.id }}</option>
        </select>
      </div>
      <div>
        <label class="block text-xs font-bold uppercase tracking-wider text-[var(--muted)] mb-1">Date</label>
        <input v-model="dateFilter" type="date" class="field-input" />
      </div>
      <button type="button" class="btn btn-secondary text-sm" @click="showMore = !showMore">{{ t('matches_more_filters') }}</button>
      <div v-if="showMore">
        <label class="block text-xs font-bold uppercase tracking-wider text-[var(--muted)] mb-1">Page size</label>
        <select v-model.number="pageSize" class="field-input">
          <option :value="25">25</option>
          <option :value="50">50</option>
          <option :value="100">100</option>
        </select>
      </div>
    </div>
  </div>

  <div v-if="loading" class="text-center py-12 text-[var(--muted)] mono">…</div>
  <div v-else-if="!matches.length" class="panel p-10 text-center space-y-4">
    <p class="text-[var(--muted)]">{{ t('matches_empty_hint') }}</p>
    <RouterLink to="/" class="btn btn-primary inline-flex">{{ t('matches_empty_cta') }}</RouterLink>
  </div>
  <div v-else class="panel overflow-hidden">
    <div class="hidden sm:grid grid-cols-[7.5rem_1fr_auto_1fr] gap-3 px-4 py-3 text-[10px] font-bold uppercase tracking-[0.14em] text-[var(--muted)] border-b border-[var(--border)]">
      <span>Date</span>
      <span class="text-right">Home</span>
      <span class="text-center px-3">Score</span>
      <span>Away</span>
    </div>
    <button
      v-for="m in matches"
      :key="m.match_id"
      type="button"
      class="scoreboard-row w-full text-left hover:bg-[var(--surface-2)] transition-colors"
      @click="router.push(`/match/${m.match_id}`)"
    >
      <span class="text-sm text-[var(--muted)] mono">{{ formatDate(m.match_date) }}</span>
      <span class="font-bold text-right sm:text-right">{{ m.home_team || '-' }}</span>
      <span class="mono text-sm font-bold px-3 py-1 rounded-md bg-[var(--surface-2)] text-center min-w-[3.5rem]">{{ scoreOf(m) }}</span>
      <span class="font-bold">{{ m.away_team || '-' }}</span>
    </button>
    <div class="flex items-center justify-between p-4 text-sm border-t border-[var(--border)]">
      <span class="text-[var(--muted)] mono">{{ totalCount }} total</span>
      <div class="flex gap-2">
        <button type="button" class="btn btn-secondary text-sm min-h-[36px]" :disabled="currentPage <= 1" @click="currentPage--">Prev</button>
        <button type="button" class="btn btn-secondary text-sm min-h-[36px]" :disabled="currentPage * pageSize >= totalCount" @click="currentPage++">Next</button>
      </div>
    </div>
  </div>
</template>
