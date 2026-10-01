<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api, seasonLabel, type MatchRow, type Season } from '@/api/client'
import { useLeaguesStore } from '@/stores/leagues'
import { useScrapeStore } from '@/stores/scrape'
import { useSportStore } from '@/stores/sport'
import { matchDate, num } from '@/lib/format'
import { errorText, toast, toastError } from '@/lib/toast'
import AppIcon from '@/components/AppIcon.vue'
import { latestOnly } from '@/lib/latest'

const PAGE = 25
const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const leagues = useLeaguesStore()
const scrape = useScrapeStore()
const sport = useSportStore()

const league = ref<string>(String(route.query.league_id || ''))
const season = ref<string>(String(route.query.season_id || ''))
const date = ref<string>(String(route.query.date || ''))
const sort = ref<'desc' | 'asc'>(route.query.sort === 'asc' ? 'asc' : 'desc')
const details = ref<'' | 'present' | 'missing'>(route.query.details === 'present' || route.query.details === 'missing' ? route.query.details : '')
const page = ref(Math.max(1, Number(route.query.page) || 1))

const seasons = ref<Season[]>([])
const items = ref<MatchRow[]>([])
const total = ref(0)
const loading = ref(true)
const err = ref('')

const hasFilters = computed(() => !!(league.value || season.value || date.value || details.value))
/** Matches of the chosen league (and season) that have no details; drives the banner. */
const missingCount = ref(0)
const startingMissing = ref(false)
const allLabel = computed(() =>
  sport.current === 'all' ? t('matches.allLeagues') : t('matches.allLeaguesSport', { sport: t(`sport.${sport.current}`) }),
)
/** With a sport picked and no single league chosen, ask for every league of that sport. */
const leagueParam = computed(() => {
  if (league.value) return league.value
  if (sport.current === 'all') return ''
  return leagues.rows.map((l) => l.id).join(',')
})
const sportWithoutLeagues = computed(() => sport.current !== 'all' && leagues.loaded && !leagues.rows.length)
const from = computed(() => (total.value ? (page.value - 1) * PAGE + 1 : 0))
const to = computed(() => Math.min(total.value, page.value * PAGE))
const cols = 'grid-template-columns: 72px 150px minmax(0, 1fr) 84px minmax(0, 1fr) 72px'

const loads = latestOnly()
const missingLoads = latestOnly()
const seasonLoads = latestOnly()

async function loadSeasons() {
  const token = seasonLoads.next()
  seasons.value = []
  if (!league.value) return
  try {
    const list = (await api.seasons(Number(league.value))).seasons || []
    if (seasonLoads.isCurrent(token)) seasons.value = list
  } catch {
    if (seasonLoads.isCurrent(token)) seasons.value = []
  }
}

async function load() {
  const token = loads.next()
  if (sportWithoutLeagues.value) {
    items.value = []
    total.value = 0
    loading.value = false
    return
  }
  loading.value = true
  err.value = ''
  const p = new URLSearchParams({ limit: String(PAGE), offset: String((page.value - 1) * PAGE), sort: sort.value })
  if (leagueParam.value) p.set('league_id', leagueParam.value)
  if (season.value) p.set('season_id', season.value)
  if (date.value) p.set('date', date.value)
  if (details.value) p.set('details', details.value)
  void loadMissingCount()
  try {
    const r = await api.matches(p)
    if (!loads.isCurrent(token)) return
    items.value = r.items || []
    total.value = r.total ?? items.value.length
  } catch (e) {
    if (!loads.isCurrent(token)) return
    err.value = errorText(e)
    items.value = []
    total.value = 0
  } finally {
    if (loads.isCurrent(token)) loading.value = false
  }
}

async function loadMissingCount() {
  const token = missingLoads.next()
  missingCount.value = 0
  if (!league.value) return
  const p = new URLSearchParams({ limit: '1', league_id: league.value, details: 'missing' })
  if (season.value) p.set('season_id', season.value)
  try {
    const n = (await api.matches(p)).total || 0
    if (missingLoads.isCurrent(token)) missingCount.value = n
  } catch {
    if (missingLoads.isCurrent(token)) missingCount.value = 0
  }
}

async function downloadMissing() {
  if (!league.value || scrape.isRunning) return
  startingMissing.value = true
  try {
    const lid = Number(league.value)
    const r = await api.missingDetails(lid, season.value ? Number(season.value) : undefined)
    const ids = (r.missing || []).map((m) => Number(m.match_id)).filter(Number.isFinite)
    if (!ids.length) return
    await scrape.start({ mode: 'details', selections: [{ league_id: lid, season_ids: null, match_ids: ids }] })
    toast(r.truncated ? t('matches.missingTruncated', { n: num(ids.length), total: num(r.missing_count) }) : t('matches.missingStarted'))
  } catch (e) {
    toastError(e)
  } finally {
    startingMissing.value = false
  }
}

function syncQuery() {
  const q: Record<string, string> = {}
  if (league.value) q.league_id = league.value
  if (season.value) q.season_id = season.value
  if (date.value) q.date = date.value
  if (details.value) q.details = details.value
  if (sort.value === 'asc') q.sort = 'asc'
  if (page.value > 1) q.page = String(page.value)
  void router.replace({ query: q })
}

function score(m: MatchRow) {
  const h = m.home_score ?? ''
  const a = m.away_score ?? ''
  return h === '' && a === '' ? '–' : `${h}–${a}`
}

function clearFilters() {
  league.value = ''
  season.value = ''
  date.value = ''
  details.value = ''
}

watch(league, async () => {
  season.value = ''
  await loadSeasons()
})
watch([league, season, date, sort, details], () => {
  page.value = 1
})
watch([league, season, date, sort, details, page], () => {
  syncQuery()
  void load()
})

watch(
  () => sport.current,
  () => {
    // Each branch reloads once: clearing the league or going back to page 1 trips the watchers above
    if (league.value && !leagues.rows.some((l) => String(l.id) === league.value)) league.value = ''
    else if (page.value !== 1) page.value = 1
    else void load()
  },
)

onMounted(async () => {
  if (!leagues.loaded) await leagues.load().catch(() => {})
  // Opened for one league (e.g. from Leagues): make sure the sidebar shows that league's sport.
  const target = leagues.all.find((l) => String(l.id) === league.value)
  if (target && !leagues.rows.some((l) => l.id === target.id)) sport.set(target.sport ?? 'all')
  const keepSeason = season.value
  await loadSeasons()
  season.value = keepSeason
  await load()
})
onUnmounted(scrape.onFinished(() => void load()))
</script>

<template>
  <div class="flex flex-wrap items-end justify-between gap-4 mb-6">
    <div>
      <h1 class="page-title">{{ t('matches.title') }}</h1>
      <p class="page-sub">{{ t('matches.sub') }}</p>
    </div>
    <RouterLink to="/download" class="btn"><AppIcon name="download" :size="16" />{{ t('matches.goDownload') }}</RouterLink>
  </div>

  <div class="flex flex-wrap items-end gap-3 mb-5">
    <div class="w-full sm:w-auto sm:min-w-[240px]">
      <label class="label" for="f-league">{{ t('matches.league') }}</label>
      <select id="f-league" v-model="league" class="field">
        <option value="">{{ allLabel }}</option>
        <option v-for="l in leagues.rows" :key="l.id" :value="String(l.id)">{{ l.name }}</option>
      </select>
    </div>
    <div class="w-full sm:w-auto sm:min-w-[160px]">
      <label class="label" for="f-season">{{ t('matches.season') }}</label>
      <select id="f-season" v-model="season" class="field" :disabled="!league">
        <option value="">{{ t('matches.allSeasons') }}</option>
        <option v-for="s in seasons" :key="s.id" :value="String(s.id)">{{ seasonLabel(s) }}</option>
      </select>
    </div>
    <div class="w-full sm:w-auto">
      <label class="label" for="f-date">{{ t('matches.date') }}</label>
      <input id="f-date" v-model="date" type="date" class="field" />
    </div>
    <div class="w-full sm:w-auto">
      <label class="label" for="f-sort">{{ t('matches.sort') }}</label>
      <select id="f-sort" v-model="sort" class="field">
        <option value="desc">{{ t('matches.newest') }}</option>
        <option value="asc">{{ t('matches.oldest') }}</option>
      </select>
    </div>
    <div class="w-full sm:w-auto">
      <label class="label" for="f-details">{{ t('matches.details') }}</label>
      <select id="f-details" v-model="details" class="field">
        <option value="">{{ t('matches.detailsAll') }}</option>
        <option value="present">{{ t('matches.detailsPresent') }}</option>
        <option value="missing">{{ t('matches.detailsMissing') }}</option>
      </select>
    </div>
    <button v-if="hasFilters" type="button" class="btn btn-ghost" @click="clearFilters">{{ t('matches.clearFilters') }}</button>
  </div>

  <div v-if="missingCount" class="card soft px-5 py-4 mb-5 flex flex-wrap items-center gap-3" style="border-color: var(--warn-bg)">
    <span class="flex-1 min-w-[240px] text-sm">{{ t('matches.missingBanner', { n: num(missingCount) }) }}</span>
    <span v-if="scrape.isRunning" class="text-[13px]" style="color: var(--muted)">{{ t('job.runningInfo') }}</span>
    <button v-else type="button" class="btn btn-primary btn-sm" :disabled="startingMissing" @click="downloadMissing">
      <AppIcon name="download" :size="16" />{{ t('matches.missingRun') }}
    </button>
  </div>

  <div v-if="loading && !items.length" class="flex items-center gap-3 py-16" style="color: var(--muted)"><span class="spinner"></span>{{ t('common.loading') }}</div>

  <div v-else-if="err" class="card p-8 flex flex-col items-start gap-3">
    <span style="color: var(--danger)">{{ err }}</span>
    <button type="button" class="btn" @click="load">{{ t('common.retry') }}</button>
  </div>

  <div v-else-if="!items.length" class="card p-12 flex flex-col items-center gap-3 text-center">
    <h2 class="m-0 text-xl font-bold">{{ t('matches.empty.title') }}</h2>
    <p class="page-sub max-w-[440px]">{{ sportWithoutLeagues ? t('leagues.noneInSport') : t('matches.empty.body') }}</p>
    <div class="flex gap-2 mt-2">
      <button v-if="hasFilters" type="button" class="btn" @click="clearFilters">{{ t('matches.clearFilters') }}</button>
      <RouterLink to="/download" class="btn btn-primary">{{ t('matches.empty.cta') }}</RouterLink>
    </div>
  </div>

  <div v-else class="card overflow-hidden" :style="{ opacity: loading ? 0.6 : 1 }">
    <div class="table-head hidden md:grid" :style="cols">
      <span>{{ t('matches.col.round') }}</span>
      <span>{{ t('matches.col.date') }}</span>
      <span class="text-right">{{ t('matches.col.home') }}</span>
      <span class="text-center">{{ t('matches.col.score') }}</span>
      <span>{{ t('matches.col.away') }}</span>
      <span>{{ t('matches.col.details') }}</span>
    </div>
    <RouterLink
      v-for="m in items"
      :key="String(m.match_id)"
      :to="`/match/${m.match_id}`"
      class="table-row match-row no-underline"
      :style="cols"
    >
      <span class="mono text-[13px] max-md:hidden" style="color: var(--muted)">{{ m.round || '—' }}</span>
      <span class="mono text-[13px] max-md:hidden" style="color: var(--muted)">{{ matchDate(m.match_date) }}</span>
      <span class="text-right font-semibold truncate">{{ m.home_team || '—' }}</span>
      <span class="mono text-center font-semibold rounded-md py-1" style="background: var(--surface-3)">{{ score(m) }}</span>
      <span class="font-semibold truncate">{{ m.away_team || '—' }}</span>
      <span class="max-md:hidden">
        <span v-if="m.has_details" class="badge badge-ok">{{ t('matches.hasDetails') }}</span>
        <span v-else-if="m.has_details === false" class="badge badge-neutral">{{ t('matches.noDetails') }}</span>
      </span>
    </RouterLink>
    <div class="flex flex-wrap items-center justify-between gap-3 px-5 py-3 text-sm" style="border-top: 1px solid var(--line); color: var(--muted)">
      <span class="mono">{{ t('matches.range', { from: num(from), to: num(to), total: num(total) }) }}</span>
      <span class="flex gap-2">
        <button type="button" class="btn btn-sm" :disabled="page <= 1" @click="page--"><AppIcon name="chevronLeft" :size="16" />{{ t('matches.prev') }}</button>
        <button type="button" class="btn btn-sm" :disabled="to >= total" @click="page++">{{ t('matches.next') }}<AppIcon name="chevronRight" :size="16" /></button>
      </span>
    </div>
  </div>
</template>

<style scoped>
.match-row {
  color: var(--text);
}
.match-row:hover {
  background: var(--surface-2);
}
@media (max-width: 767px) {
  .match-row {
    grid-template-columns: minmax(0, 1fr) 72px minmax(0, 1fr) !important;
  }
}
</style>
