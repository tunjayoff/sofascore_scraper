<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { apiGet, apiSend } from '@/api/client'
import {
  parseStatistics,
  parseIncidents,
  splitLineupPlayers,
  playerDisplayName,
  shirtOf,
  formatKickoff,
  venueName,
  possessionPair,
  incidentLabel,
  highlightIncidents,
  formSequence,
} from '@/lib/matchDetail'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const matchId = computed(() => String(route.params.id))

const loading = ref(true)
const notFetched = ref(false)
const error = ref('')
const matchData = ref<any>(null)
const fetching = ref(false)
const tab = ref<'overview' | 'stats' | 'events' | 'lineups'>('overview')
const statsPeriod = ref(0)

const basic = computed(() => matchData.value?.basic || null)
const homeName = computed(
  () => basic.value?.homeTeam?.name || basic.value?.homeTeam?.shortName || '-',
)
const awayName = computed(
  () => basic.value?.awayTeam?.name || basic.value?.awayTeam?.shortName || '-',
)
const homeScore = computed(() => basic.value?.homeScore?.current ?? basic.value?.homeScore?.display ?? null)
const awayScore = computed(() => basic.value?.awayScore?.current ?? basic.value?.awayScore?.display ?? null)
const htScore = computed(() => {
  const h = basic.value?.homeScore?.period1
  const a = basic.value?.awayScore?.period1
  if (h == null && a == null) return null
  return `${h ?? '-'}–${a ?? '-'}`
})
const statusText = computed(() => basic.value?.status?.description || '')
const tournamentName = computed(() => basic.value?.tournament?.name || '')
const seasonName = computed(() => basic.value?.season?.name || basic.value?.season?.year || '')
const kickoff = computed(() => formatKickoff(basic.value?.startTimestamp))
const stadium = computed(() => venueName(basic.value))
const referee = computed(() => basic.value?.referee?.name || '')

const statistics = computed(() => parseStatistics(matchData.value?.statistics))
const incidents = computed(() => parseIncidents(matchData.value?.incidents))
const lineups = computed(() => {
  const L = matchData.value?.lineups
  if (!L || typeof L !== 'object') return null
  const home = L.home?.players
  const away = L.away?.players
  if ((!Array.isArray(home) || !home.length) && (!Array.isArray(away) || !away.length)) return null
  return L
})
const homeXi = computed(() => splitLineupPlayers(lineups.value?.home?.players))
const awayXi = computed(() => splitLineupPlayers(lineups.value?.away?.players))
const possession = computed(() => possessionPair(statistics.value))
const highlights = computed(() => highlightIncidents(incidents.value))
const h2h = computed(() => matchData.value?.h2h?.teamDuel || null)
const homeForm = computed(() => formSequence(matchData.value?.pregame_form?.homeTeam))
const awayForm = computed(() => formSequence(matchData.value?.pregame_form?.awayTeam))
const homeFormMeta = computed(() => matchData.value?.pregame_form?.homeTeam || null)
const awayFormMeta = computed(() => matchData.value?.pregame_form?.awayTeam || null)
const streaks = computed(() => {
  const raw = matchData.value?.team_streaks?.general
  return Array.isArray(raw) ? raw.slice(0, 8) : []
})

const hasStats = computed(() => statistics.value.length > 0)
const hasEvents = computed(() => incidents.value.length > 0)
const hasLineups = computed(() => !!lineups.value)

const tabs = computed(() => {
  const list: { id: 'overview' | 'stats' | 'events' | 'lineups'; label: string }[] = [
    { id: 'overview', label: t('match_tab_overview') },
  ]
  if (hasStats.value) list.push({ id: 'stats', label: t('match_tab_stats') })
  if (hasEvents.value) list.push({ id: 'events', label: t('match_tab_events') })
  if (hasLineups.value) list.push({ id: 'lineups', label: t('match_tab_lineups') })
  return list
})

const activePeriod = computed(() => statistics.value[statsPeriod.value] || statistics.value[0] || null)
const activeGroups = computed(() => {
  const p = activePeriod.value
  if (!p) return []
  if (Array.isArray(p.groups) && p.groups.length) return p.groups
  return []
})

watch(tabs, (list) => {
  if (!list.some((x) => x.id === tab.value)) tab.value = 'overview'
})
watch(statistics, () => {
  statsPeriod.value = 0
})

async function load() {
  loading.value = true
  error.value = ''
  notFetched.value = false
  try {
    matchData.value = await apiGet(`/api/matches/${matchId.value}`)
    tab.value = 'overview'
  } catch (e: any) {
    const msg = String(e?.message || e)
    if (msg.toLowerCase().includes('404') || msg.toLowerCase().includes('not found')) {
      notFetched.value = true
      matchData.value = null
    } else {
      error.value = msg
    }
  } finally {
    loading.value = false
  }
}

async function refresh() {
  fetching.value = true
  error.value = ''
  try {
    await apiSend(`/api/matches/${matchId.value}/fetch`, 'POST')
    await load()
  } catch (e: any) {
    error.value = String(e?.message || e)
  } finally {
    fetching.value = false
  }
}

onMounted(load)
watch(matchId, load)
</script>

<template>
  <header class="mb-5">
    <nav class="text-sm text-[var(--muted)] mb-3">
      <button type="button" class="link-accent font-medium" @click="router.push('/matches')">{{ t('nav_matches') }}</button>
      <span class="px-1">/</span>
      <span>{{ t('match_detail_title') }}</span>
    </nav>
  </header>

  <div v-if="loading" class="panel p-10 text-center text-[var(--muted)]">{{ t('fetching_in_progress') }}</div>
  <div v-else-if="error" class="panel p-6 text-red-600">{{ error }}</div>
  <div v-else-if="notFetched" class="panel p-10 text-center space-y-4">
    <h3 class="text-xl font-bold">{{ t('match_details_not_fetched') }}</h3>
    <button type="button" class="btn btn-primary" :disabled="fetching" @click="refresh">
      {{ fetching ? t('fetching_in_progress') : t('refresh_match_data') }}
    </button>
  </div>

  <div v-else class="space-y-4">
    <!-- Scoreboard -->
    <section class="panel overflow-hidden">
      <div class="px-4 pt-4 pb-2 text-center">
        <p class="text-xs font-bold uppercase tracking-[0.14em] link-accent">{{ tournamentName }}</p>
        <p v-if="seasonName" class="text-xs text-[var(--muted)] mt-1">{{ seasonName }}</p>
      </div>
      <div class="grid grid-cols-[1fr_auto_1fr] items-center gap-3 px-4 py-5">
        <div class="text-right">
          <p class="text-lg sm:text-2xl font-extrabold tracking-tight leading-tight">{{ homeName }}</p>
        </div>
        <div class="text-center min-w-[5.5rem]">
          <p class="mono text-3xl sm:text-4xl font-extrabold tabular-nums link-accent leading-none">
            <template v-if="homeScore != null && awayScore != null">{{ homeScore }}–{{ awayScore }}</template>
            <template v-else>vs</template>
          </p>
          <p v-if="htScore" class="text-xs text-[var(--muted)] mt-2 mono">HT {{ htScore }}</p>
          <p class="text-xs font-semibold text-[var(--muted)] mt-1">{{ statusText }}</p>
        </div>
        <div class="text-left">
          <p class="text-lg sm:text-2xl font-extrabold tracking-tight leading-tight">{{ awayName }}</p>
        </div>
      </div>
      <div class="border-t border-[var(--border)] px-4 py-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--muted)] justify-center">
        <span v-if="kickoff"><span class="font-bold text-[var(--text)]">{{ t('match_sidebar_date_label') }}:</span> {{ kickoff }}</span>
        <span v-if="stadium"><span class="font-bold text-[var(--text)]">{{ t('match_sidebar_stadium_label') }}:</span> {{ stadium }}</span>
        <span v-if="referee"><span class="font-bold text-[var(--text)]">{{ t('match_sidebar_referee_label') }}:</span> {{ referee }}</span>
        <span class="mono">ID {{ matchId }}</span>
      </div>
      <div class="border-t border-[var(--border)] px-4 py-3 flex flex-wrap gap-2 justify-end">
        <button type="button" class="btn btn-secondary text-sm min-h-[40px]" @click="router.push('/matches')">{{ t('back_to_schedule') }}</button>
        <button type="button" class="btn btn-primary text-sm min-h-[40px]" :disabled="fetching" @click="refresh">
          {{ fetching ? t('fetching_in_progress') : t('refresh_match_data') }}
        </button>
      </div>
    </section>

    <div class="flex flex-wrap gap-1 border-b border-[var(--border)] pb-2">
      <button
        v-for="tb in tabs"
        :key="tb.id"
        type="button"
        class="tab-btn"
        :class="{ 'is-active': tab === tb.id }"
        @click="tab = tb.id"
      >
        {{ tb.label }}
      </button>
    </div>

    <!-- Overview -->
    <div v-if="tab === 'overview'" class="grid gap-4 lg:grid-cols-2">
      <section v-if="possession || highlights.length" class="panel p-5 space-y-4">
        <h2 class="text-sm font-bold uppercase tracking-wider text-[var(--muted)]">{{ t('match_statistics_title') }}</h2>
        <div v-if="possession" class="space-y-2">
          <div class="flex justify-between text-sm font-bold mono">
            <span>{{ possession.home }}%</span>
            <span class="text-[var(--muted)] font-semibold">Ball possession</span>
            <span>{{ possession.away }}%</span>
          </div>
          <div class="h-2.5 rounded-full overflow-hidden flex bg-[var(--surface-2)]">
            <div class="h-full bg-[var(--accent)]" :style="{ width: possession.home + '%' }" />
            <div class="h-full bg-[var(--muted)] opacity-50" :style="{ width: possession.away + '%' }" />
          </div>
        </div>
        <ul v-if="highlights.length" class="space-y-1.5 text-sm">
          <li
            v-for="(inc, i) in highlights"
            :key="'h'+i"
            class="flex gap-3 border-b border-[var(--border)] py-2"
            :class="inc.isHome === false ? 'flex-row-reverse text-right' : ''"
          >
            <span class="mono text-[var(--muted)] w-10 shrink-0">{{ inc.time }}′</span>
            <span class="flex-1 font-medium">{{ incidentLabel(inc) }}</span>
          </li>
        </ul>
      </section>

      <section class="panel p-5 space-y-5">
        <div v-if="homeForm.length || awayForm.length">
          <h2 class="text-sm font-bold uppercase tracking-wider text-[var(--muted)] mb-3">{{ t('match_section_pregame_form') }}</h2>
          <div class="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p class="font-bold mb-1 truncate">{{ homeName }}</p>
              <p v-if="homeFormMeta?.position" class="text-xs text-[var(--muted)] mb-2">
                {{ t('match_form_position') }} {{ homeFormMeta.position }}
                <span v-if="homeFormMeta.avgRating"> · {{ t('match_form_rating') }} {{ homeFormMeta.avgRating }}</span>
              </p>
              <div class="flex flex-wrap gap-1">
                <span
                  v-for="(f, i) in homeForm"
                  :key="'hf'+i"
                  class="form-chip"
                  :class="{ 'form-w': f==='W', 'form-d': f==='D', 'form-l': f==='L' }"
                >{{ f }}</span>
              </div>
            </div>
            <div>
              <p class="font-bold mb-1 truncate">{{ awayName }}</p>
              <p v-if="awayFormMeta?.position" class="text-xs text-[var(--muted)] mb-2">
                {{ t('match_form_position') }} {{ awayFormMeta.position }}
                <span v-if="awayFormMeta.avgRating"> · {{ t('match_form_rating') }} {{ awayFormMeta.avgRating }}</span>
              </p>
              <div class="flex flex-wrap gap-1">
                <span
                  v-for="(f, i) in awayForm"
                  :key="'af'+i"
                  class="form-chip"
                  :class="{ 'form-w': f==='W', 'form-d': f==='D', 'form-l': f==='L' }"
                >{{ f }}</span>
              </div>
            </div>
          </div>
        </div>

        <div v-if="h2h">
          <h2 class="text-sm font-bold uppercase tracking-wider text-[var(--muted)] mb-3">{{ t('match_section_h2h') }}</h2>
          <div class="grid grid-cols-3 gap-2 text-center">
            <div class="rounded-[var(--radius)] bg-[var(--surface-2)] p-3">
              <p class="mono text-xl font-extrabold">{{ h2h.homeWins ?? 0 }}</p>
              <p class="text-[10px] font-bold uppercase text-[var(--muted)] mt-1">{{ t('match_h2h_home_wins') }}</p>
            </div>
            <div class="rounded-[var(--radius)] bg-[var(--surface-2)] p-3">
              <p class="mono text-xl font-extrabold">{{ h2h.draws ?? 0 }}</p>
              <p class="text-[10px] font-bold uppercase text-[var(--muted)] mt-1">{{ t('match_h2h_draws') }}</p>
            </div>
            <div class="rounded-[var(--radius)] bg-[var(--surface-2)] p-3">
              <p class="mono text-xl font-extrabold">{{ h2h.awayWins ?? 0 }}</p>
              <p class="text-[10px] font-bold uppercase text-[var(--muted)] mt-1">{{ t('match_h2h_away_wins') }}</p>
            </div>
          </div>
        </div>

        <div v-if="streaks.length">
          <h2 class="text-sm font-bold uppercase tracking-wider text-[var(--muted)] mb-3">{{ t('match_section_team_streaks') }}</h2>
          <ul class="space-y-2 text-sm">
            <li v-for="(s, i) in streaks" :key="'st'+i" class="flex justify-between gap-3 border-b border-[var(--border)] py-1.5">
              <span>
                <span class="text-[var(--muted)]">{{ s.team === 'away' ? t('match_streak_team_away') : t('match_streak_team_home') }} · </span>
                {{ s.name }}
              </span>
              <span class="mono font-bold">{{ s.value }}</span>
            </li>
          </ul>
        </div>
      </section>
    </div>

    <!-- Stats -->
    <div v-else-if="tab === 'stats'" class="panel p-5 space-y-4">
      <div v-if="statistics.length > 1" class="flex flex-wrap gap-1">
        <button
          v-for="(p, i) in statistics"
          :key="'sp'+i"
          type="button"
          class="tab-btn text-xs"
          :class="{ 'is-active': statsPeriod === i }"
          @click="statsPeriod = i"
        >
          {{ p.period || i + 1 }}
        </button>
      </div>
      <div v-if="possession && statsPeriod === 0" class="space-y-2 mb-2">
        <div class="flex justify-between text-sm font-bold mono">
          <span>{{ possession.home }}%</span>
          <span class="text-[var(--muted)] font-semibold">Ball possession</span>
          <span>{{ possession.away }}%</span>
        </div>
        <div class="h-2.5 rounded-full overflow-hidden flex bg-[var(--surface-2)]">
          <div class="h-full bg-[var(--accent)]" :style="{ width: possession.home + '%' }" />
          <div class="h-full bg-[var(--muted)] opacity-50" :style="{ width: possession.away + '%' }" />
        </div>
      </div>
      <div class="space-y-4 text-sm max-h-[36rem] overflow-y-auto">
        <template v-for="(grp, gj) in activeGroups" :key="'g'+gj">
          <div>
            <p v-if="grp.groupName" class="text-xs font-bold uppercase tracking-wider text-[var(--muted)] mb-2">
              {{ grp.groupName }}
            </p>
            <div
              v-for="(item, ii) in (grp.statisticsItems || [])"
              :key="'si'+ii"
              class="grid grid-cols-[1fr_auto_1fr] gap-2 py-2 border-b border-[var(--border)] items-center"
            >
              <span class="text-right mono font-semibold">{{ item.home }}</span>
              <span class="text-center text-[var(--muted)] text-xs px-2 min-w-[7rem]">{{ item.name }}</span>
              <span class="mono font-semibold">{{ item.away }}</span>
            </div>
          </div>
        </template>
        <p v-if="!hasStats" class="text-[var(--muted)]">{{ t('no_match_statistics') }}</p>
      </div>
    </div>

    <!-- Events timeline -->
    <div v-else-if="tab === 'events'" class="panel p-5">
      <p class="text-xs text-[var(--muted)] mb-4">{{ t('match_incidents_timeline_hint') }}</p>
      <ul class="space-y-0 max-h-[36rem] overflow-y-auto">
        <li
          v-for="(inc, i) in incidents"
          :key="'e'+i"
          class="relative grid grid-cols-[1fr_3rem_1fr] gap-2 py-2.5 border-b border-[var(--border)] text-sm"
        >
          <div class="text-right pr-2" :class="inc.isHome === true || inc.isHome == null && !inc.playerIn ? '' : 'opacity-40'">
            <template v-if="inc.isHome !== false">
              <span class="font-medium">{{ incidentLabel(inc) }}</span>
            </template>
          </div>
          <div class="text-center mono text-[var(--muted)] font-bold relative">
            <span class="relative z-[1]">{{ inc.time != null ? inc.time + '′' : '·' }}</span>
          </div>
          <div class="text-left pl-2" :class="inc.isHome === false ? '' : 'opacity-40'">
            <template v-if="inc.isHome === false">
              <span class="font-medium">{{ incidentLabel(inc) }}</span>
            </template>
          </div>
        </li>
      </ul>
    </div>

    <!-- Lineups -->
    <div v-else-if="tab === 'lineups' && lineups" class="space-y-4">
      <p class="text-sm text-[var(--muted)]">
        <span v-if="lineups.confirmed">{{ t('match_lineups_confirmed') }}</span>
        <span v-else>{{ t('match_lineups_unconfirmed') }}</span>
      </p>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <section class="panel p-5">
          <div class="flex items-baseline justify-between gap-2 mb-3">
            <h3 class="font-bold">{{ homeName }}</h3>
            <span v-if="lineups.home?.formation" class="chip">{{ t('match_formation_label') }} {{ lineups.home.formation }}</span>
          </div>
          <p class="text-[10px] font-bold uppercase tracking-wider text-[var(--muted)] mb-2">{{ t('match_starting_xi') }}</p>
          <ul class="space-y-1 text-sm mb-4">
            <li v-for="(p, i) in homeXi.starters" :key="'hs'+i" class="flex justify-between gap-2 border-b border-[var(--border)] py-1.5">
              <span><span class="mono text-[var(--muted)] w-6 inline-block">{{ shirtOf(p) }}</span> {{ playerDisplayName(p) }}</span>
              <span class="text-[var(--muted)] text-xs">{{ p.position || p.player?.position || '' }}</span>
            </li>
          </ul>
          <p class="text-[10px] font-bold uppercase tracking-wider text-[var(--muted)] mb-2">{{ t('match_substitutes') }}</p>
          <ul class="space-y-1 text-sm">
            <li v-for="(p, i) in homeXi.subs" :key="'hb'+i" class="flex justify-between gap-2 border-b border-[var(--border)] py-1.5">
              <span><span class="mono text-[var(--muted)] w-6 inline-block">{{ shirtOf(p) }}</span> {{ playerDisplayName(p) }}</span>
              <span class="text-[var(--muted)] text-xs">{{ p.position || p.player?.position || '' }}</span>
            </li>
          </ul>
        </section>
        <section class="panel p-5">
          <div class="flex items-baseline justify-between gap-2 mb-3">
            <h3 class="font-bold">{{ awayName }}</h3>
            <span v-if="lineups.away?.formation" class="chip">{{ t('match_formation_label') }} {{ lineups.away.formation }}</span>
          </div>
          <p class="text-[10px] font-bold uppercase tracking-wider text-[var(--muted)] mb-2">{{ t('match_starting_xi') }}</p>
          <ul class="space-y-1 text-sm mb-4">
            <li v-for="(p, i) in awayXi.starters" :key="'as'+i" class="flex justify-between gap-2 border-b border-[var(--border)] py-1.5">
              <span><span class="mono text-[var(--muted)] w-6 inline-block">{{ shirtOf(p) }}</span> {{ playerDisplayName(p) }}</span>
              <span class="text-[var(--muted)] text-xs">{{ p.position || p.player?.position || '' }}</span>
            </li>
          </ul>
          <p class="text-[10px] font-bold uppercase tracking-wider text-[var(--muted)] mb-2">{{ t('match_substitutes') }}</p>
          <ul class="space-y-1 text-sm">
            <li v-for="(p, i) in awayXi.subs" :key="'ab'+i" class="flex justify-between gap-2 border-b border-[var(--border)] py-1.5">
              <span><span class="mono text-[var(--muted)] w-6 inline-block">{{ shirtOf(p) }}</span> {{ playerDisplayName(p) }}</span>
              <span class="text-[var(--muted)] text-xs">{{ p.position || p.player?.position || '' }}</span>
            </li>
          </ul>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.form-chip {
  @apply inline-flex items-center justify-center w-7 h-7 rounded-md text-xs font-extrabold;
  background: var(--surface-2);
}
.form-w {
  background: rgba(15, 118, 110, 0.18);
  color: var(--accent);
}
.form-d {
  color: var(--muted);
}
.form-l {
  background: rgba(220, 38, 38, 0.12);
  color: var(--danger);
}
</style>
