<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api, ApiError } from '@/api/client'
import {
  parseStatistics,
  parseIncidents,
  splitLineupPlayers,
  playerDisplayName,
  shirtOf,
  venueName,
  formSequence,
} from '@/lib/matchDetail'
import { sportKey, periodLabel } from '@/lib/sport'
import { matchDate } from '@/lib/format'
import { errorText, toastError } from '@/lib/toast'
import { useScrapeStore } from '@/stores/scrape'
import AppIcon from '@/components/AppIcon.vue'
import SportBadge from '@/components/SportBadge.vue'
import { latestOnly } from '@/lib/latest'
import { onTabKeydown } from '@/lib/tabs'

type Tab = 'overview' | 'stats' | 'events' | 'lineups'

const { t } = useI18n()
const route = useRoute()
const id = computed(() => String(route.params.id))

const data = ref<any>(null)
const loading = ref(true)
const notFetched = ref(false)
const err = ref('')
const fetching = ref(false)
const tab = ref<Tab>('overview')
const period = ref(0)

const basic = computed(() => data.value?.basic || null)
const sport = computed(() =>
  sportKey(basic.value?.tournament?.category?.sport?.slug || basic.value?.tournament?.category?.sport?.name || basic.value?.sport?.slug),
)
const home = computed(() => basic.value?.homeTeam?.name || basic.value?.homeTeam?.shortName || '—')
const away = computed(() => basic.value?.awayTeam?.name || basic.value?.awayTeam?.shortName || '—')
const homeSc = computed(() => basic.value?.homeScore || {})
const awaySc = computed(() => basic.value?.awayScore || {})
const mainScore = computed(() => {
  const h = homeSc.value.display ?? homeSc.value.current
  const a = awaySc.value.display ?? awaySc.value.current
  return h == null && a == null ? null : `${h ?? '–'}–${a ?? '–'}`
})
const periods = computed(() => {
  const keys = new Set<string>()
  for (const s of [homeSc.value, awaySc.value]) for (const k of Object.keys(s)) if (/^period\d+$/.test(k)) keys.add(k)
  const list = [...keys].sort((a, b) => Number(a.slice(6)) - Number(b.slice(6)))
  const cols = list.map((k) => ({ key: k, label: periodLabel(sport.value, Number(k.slice(6)), t) }))
  if (homeSc.value.overtime != null || awaySc.value.overtime != null) cols.push({ key: 'overtime', label: t('match.period.ot') })
  return cols
})
const tournament = computed(() => basic.value?.tournament?.name || '')
const season = computed(() => basic.value?.season?.name || basic.value?.season?.year || '')
const info = computed(() =>
  [
    { k: t('match.info.date'), v: basic.value?.startTimestamp ? matchDate(basic.value.startTimestamp) : '' },
    { k: t('match.info.status'), v: basic.value?.status?.description || '' },
    { k: t('match.info.venue'), v: venueName(basic.value) },
    { k: t('match.info.referee'), v: basic.value?.referee?.name || '' },
  ].filter((x) => x.v),
)

const stats = computed(() => parseStatistics(data.value?.statistics))
const groups = computed(() => stats.value[period.value]?.groups || stats.value[0]?.groups || [])
const incidents = computed(() => parseIncidents(data.value?.incidents).filter((i) => i?.incidentType !== 'injuryTime'))
const lineups = computed(() => {
  const L = data.value?.lineups
  const h = L?.home?.players
  const a = L?.away?.players
  return Array.isArray(h) && h.length && Array.isArray(a) && a.length ? L : null
})
const homeXi = computed(() => splitLineupPlayers(lineups.value?.home?.players))
const awayXi = computed(() => splitLineupPlayers(lineups.value?.away?.players))
const duel = computed(() => data.value?.h2h?.teamDuel || null)
const homeForm = computed(() => formSequence(data.value?.pregame_form?.homeTeam))
const awayForm = computed(() => formSequence(data.value?.pregame_form?.awayTeam))
const keyMoments = computed(() => incidents.value.filter((i) => ['goal', 'card', 'varDecision'].includes(String(i?.incidentType))).slice(0, 12))

const tabs = computed(() => {
  const list: Tab[] = ['overview']
  if (stats.value.length) list.push('stats')
  if (incidents.value.length) list.push('events')
  if (lineups.value) list.push('lineups')
  return list
})
const hasOverview = computed(() => keyMoments.value.length || homeForm.value.length || awayForm.value.length || duel.value || info.value.length)

function periodName(p: any, i: number) {
  const raw = String(p?.period || '')
  if (raw === 'ALL' || i === 0) return t('match.periodAll')
  const m = raw.match(/(\d+)/)
  return m ? periodLabel(sport.value, Number(m[1]), t) : raw
}

function bar(item: any) {
  const h = Number(item.homeValue ?? String(item.home ?? '').replace('%', ''))
  const a = Number(item.awayValue ?? String(item.away ?? '').replace('%', ''))
  const tot = (Number.isFinite(h) ? h : 0) + (Number.isFinite(a) ? a : 0)
  if (!tot) return { h: 50, a: 50, lead: '' }
  const hp = (h / tot) * 100
  return { h: hp, a: 100 - hp, lead: h > a ? 'home' : a > h ? 'away' : '' }
}

function incident(i: any) {
  const type = String(i?.incidentType || '')
  const minute = i?.time != null ? `${i.time}${i.addedTime ? '+' + i.addedTime : ''}′` : ''
  const side = i?.isHome === true ? 'home' : i?.isHome === false ? 'away' : ''
  if (type === 'goal') {
    const sc = i?.homeScore != null ? ` ${i.homeScore}–${i.awayScore}` : ''
    return { minute, side, mark: 'goal', text: `${i?.player?.name || i?.playerName || '—'}${sc}` }
  }
  if (type === 'card') {
    const red = i?.incidentClass === 'red' || i?.incidentClass === 'yellowRed'
    return { minute, side, mark: red ? 'red' : 'yellow', text: i?.player?.name || i?.playerName || '—' }
  }
  if (type === 'substitution') {
    return { minute, side, mark: 'sub', text: `${i?.playerIn?.name || '—'} ↔ ${i?.playerOut?.name || '—'}` }
  }
  if (type === 'period') return { minute: '', side: '', mark: 'period', text: String(i?.text || '') }
  if (type === 'varDecision') return { minute, side, mark: 'var', text: `VAR · ${i?.player?.name || i?.incidentClass || ''}` }
  return { minute, side, mark: 'other', text: String(i?.text || type) }
}

const formTone: Record<string, string> = { W: 'badge badge-ok', D: 'badge badge-neutral', L: 'badge badge-danger' }

const loads = latestOnly()

async function load() {
  const token = loads.next()
  loading.value = true
  err.value = ''
  notFetched.value = false
  try {
    const d = await api.match(id.value)
    if (!loads.isCurrent(token)) return
    data.value = d
    tab.value = 'overview'
    period.value = 0
  } catch (e) {
    if (!loads.isCurrent(token)) return
    data.value = null
    if (e instanceof ApiError && e.status === 404) notFetched.value = true
    else err.value = errorText(e)
  } finally {
    if (loads.isCurrent(token)) loading.value = false
  }
}

async function fetchNow() {
  fetching.value = true
  try {
    await api.fetchMatch(id.value)
    await load()
  } catch (e) {
    toastError(e)
  } finally {
    fetching.value = false
  }
}

watch(id, load, { immediate: true })
// If a running download fetches this match's details, show them as soon as it finishes.
const scrape = useScrapeStore()
onUnmounted(scrape.onFinished(() => notFetched.value && void load()))
</script>

<template>
  <RouterLink to="/matches" class="btn btn-ghost btn-sm mb-3 -ml-3" style="color: var(--muted)"><AppIcon name="chevronLeft" :size="16" />{{ t('match.back') }}</RouterLink>

  <div v-if="loading" class="flex items-center gap-3 py-16" style="color: var(--muted)"><span class="spinner"></span>{{ t('common.loading') }}</div>

  <div v-else-if="err" class="card p-8 flex flex-col items-start gap-3">
    <span style="color: var(--danger)">{{ err }}</span>
    <button type="button" class="btn" @click="load">{{ t('common.retry') }}</button>
  </div>

  <div v-else-if="notFetched" class="card p-12 flex flex-col items-center gap-4 text-center">
    <h1 class="m-0 text-xl font-bold">{{ t('match.notFetched.title') }}</h1>
    <p class="page-sub max-w-[460px]">{{ t('match.notFetched.body') }}</p>
    <p v-if="scrape.isRunning" class="m-0 text-sm max-w-[460px] soft px-4 py-3">{{ t('match.notFetched.running') }}</p>
    <button type="button" class="btn btn-primary" :disabled="fetching" @click="fetchNow">
      <span v-if="fetching" class="spinner"></span><AppIcon v-else name="download" :size="16" />
      {{ fetching ? t('match.notFetched.fetching') : t('match.notFetched.cta') }}
    </button>
  </div>

  <template v-else-if="data">
    <!-- scoreboard -->
    <section class="card p-6 md:p-8 flex flex-col gap-5 mb-5">
      <div class="flex flex-wrap items-center gap-2 text-sm" style="color: var(--muted)">
        <SportBadge :sport="sport" />
        <span>{{ tournament }}</span><span v-if="season">· {{ season }}</span>
      </div>
      <div class="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-4 md:gap-8">
        <h1 class="m-0 text-right text-xl md:text-[26px] font-bold leading-tight">{{ home }}</h1>
        <div class="mono text-4xl md:text-[44px] font-bold tracking-tight">{{ mainScore ?? 'vs' }}</div>
        <div class="text-xl md:text-[26px] font-bold leading-tight">{{ away }}</div>
      </div>
      <div v-if="periods.length" class="overflow-x-auto">
        <table class="mx-auto text-sm mono" style="border-collapse: separate; border-spacing: 12px 4px">
          <thead>
            <tr style="color: var(--muted)">
              <th class="text-left font-sans font-semibold text-xs"></th>
              <th v-for="p in periods" :key="p.key" class="font-sans font-semibold text-xs">{{ p.label }}</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th class="text-left font-sans font-semibold pr-2">{{ home }}</th>
              <td v-for="p in periods" :key="p.key" class="text-center">{{ homeSc[p.key] ?? '–' }}</td>
            </tr>
            <tr>
              <th class="text-left font-sans font-semibold pr-2">{{ away }}</th>
              <td v-for="p in periods" :key="p.key" class="text-center">{{ awaySc[p.key] ?? '–' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <div class="tabs mb-5" role="tablist">
      <button
        v-for="k in tabs"
        :id="`match-tab-${k}`"
        :key="k"
        type="button"
        role="tab"
        class="tab"
        :class="{ 'is-active': tab === k }"
        :aria-selected="tab === k"
        :aria-controls="`match-panel-${k}`"
        :tabindex="tab === k ? 0 : -1"
        @click="tab = k"
        @keydown="onTabKeydown($event, tabs, tab, (v) => (tab = v), 'match')"
      >
        {{ t(`match.tabs.${k}`) }}
      </button>
    </div>

    <!-- overview -->
    <div v-if="tab === 'overview'" id="match-panel-overview" role="tabpanel" aria-labelledby="match-tab-overview" class="grid gap-5 lg:grid-cols-2">
      <p v-if="!hasOverview" class="page-sub">{{ t('match.noOverview') }}</p>
      <section v-if="info.length" class="card p-5 flex flex-col gap-3">
        <div v-for="x in info" :key="x.k" class="flex justify-between gap-4 text-sm">
          <span style="color: var(--muted)">{{ x.k }}</span><span class="text-right font-medium">{{ x.v }}</span>
        </div>
      </section>
      <section v-if="keyMoments.length" class="card p-5 flex flex-col gap-2">
        <h2 class="section-label m-0 mb-1">{{ t('match.keyEvents') }}</h2>
        <div v-for="(i, n) in keyMoments" :key="n" class="flex items-center gap-3 text-sm min-h-[32px]">
          <span class="mono w-12 shrink-0" style="color: var(--muted)">{{ incident(i).minute }}</span>
          <span class="mark" :class="`mark-${incident(i).mark}`"></span>
          <span class="flex-1" :class="incident(i).side === 'away' ? 'text-right' : ''">{{ incident(i).text }}</span>
        </div>
      </section>
      <section v-if="homeForm.length || awayForm.length" class="card p-5 flex flex-col gap-3">
        <h2 class="section-label m-0">{{ t('match.form') }}</h2>
        <div v-for="side in [{ n: home, f: homeForm }, { n: away, f: awayForm }]" :key="side.n" class="flex items-center justify-between gap-3">
          <span class="font-medium text-sm truncate">{{ side.n }}</span>
          <span class="flex gap-1"><span v-for="(r, k) in side.f" :key="k" :class="formTone[r] || 'badge badge-neutral'" class="mono w-7 justify-center !px-0">{{ r }}</span></span>
        </div>
      </section>
      <section v-if="duel" class="card p-5 flex flex-col gap-3">
        <h2 class="section-label m-0">{{ t('match.h2h') }}</h2>
        <div class="grid grid-cols-3 text-center">
          <div><div class="mono text-2xl font-bold">{{ duel.homeWins ?? 0 }}</div><div class="text-xs truncate" style="color: var(--muted)">{{ home }}</div></div>
          <div><div class="mono text-2xl font-bold">{{ duel.draws ?? 0 }}</div><div class="text-xs" style="color: var(--muted)">=</div></div>
          <div><div class="mono text-2xl font-bold">{{ duel.awayWins ?? 0 }}</div><div class="text-xs truncate" style="color: var(--muted)">{{ away }}</div></div>
        </div>
      </section>
    </div>

    <!-- stats -->
    <section v-else-if="tab === 'stats'" id="match-panel-stats" role="tabpanel" aria-labelledby="match-tab-stats" class="card p-5 md:p-6 flex flex-col gap-5">
      <div v-if="stats.length > 1" class="seg self-start flex-wrap" role="group">
        <button v-for="(p, i) in stats" :key="i" type="button" :class="{ 'is-active': period === i }" @click="period = i">{{ periodName(p, i) }}</button>
      </div>
      <div v-for="g in groups" :key="g.groupName" class="flex flex-col gap-4">
        <h2 class="section-label m-0">{{ g.groupName }}</h2>
        <div v-for="it in g.statisticsItems || []" :key="it.key || it.name" class="flex flex-col gap-1.5">
          <div class="flex justify-between text-sm">
            <span class="mono" :class="{ 'font-bold': bar(it).lead === 'home' }">{{ it.home }}</span>
            <span style="color: var(--muted)">{{ it.name }}</span>
            <span class="mono" :class="{ 'font-bold': bar(it).lead === 'away' }">{{ it.away }}</span>
          </div>
          <div class="flex gap-1 h-1.5">
            <div class="rounded" :style="{ flexGrow: bar(it).h, background: bar(it).lead === 'home' ? 'var(--ink)' : 'var(--border-2)' }"></div>
            <div class="rounded" :style="{ flexGrow: bar(it).a, background: bar(it).lead === 'away' ? 'var(--accent)' : 'var(--border-2)' }"></div>
          </div>
        </div>
      </div>
    </section>

    <!-- events -->
    <section v-else-if="tab === 'events'" id="match-panel-events" role="tabpanel" aria-labelledby="match-tab-events" class="card p-5 flex flex-col">
      <div v-for="(i, n) in incidents" :key="n" class="flex items-center gap-3 text-sm min-h-[40px]" style="border-bottom: 1px solid var(--line)">
        <template v-if="incident(i).mark === 'period'">
          <span class="section-label py-2 mx-auto">{{ incident(i).text }}</span>
        </template>
        <template v-else>
          <span class="mono w-12 shrink-0" style="color: var(--muted)">{{ incident(i).minute }}</span>
          <span class="mark" :class="`mark-${incident(i).mark}`"></span>
          <span class="flex-1" :class="incident(i).side === 'away' ? 'text-right' : ''">{{ incident(i).text }}</span>
        </template>
      </div>
    </section>

    <!-- lineups -->
    <div v-else-if="tab === 'lineups' && lineups" id="match-panel-lineups" role="tabpanel" aria-labelledby="match-tab-lineups" class="grid gap-5 md:grid-cols-2">
      <section v-for="side in [{ n: home, x: homeXi }, { n: away, x: awayXi }]" :key="side.n" class="card p-5 flex flex-col gap-3">
        <h2 class="m-0 text-base font-bold">{{ side.n }}</h2>
        <div class="section-label">{{ t('match.starters') }}</div>
        <div v-for="(p, k) in side.x.starters" :key="'s' + k" class="flex gap-3 text-sm"><span class="mono w-7 text-right" style="color: var(--muted)">{{ shirtOf(p) }}</span>{{ playerDisplayName(p) }}</div>
        <template v-if="side.x.subs.length">
          <div class="section-label mt-2">{{ t('match.subs') }}</div>
          <div v-for="(p, k) in side.x.subs" :key="'b' + k" class="flex gap-3 text-sm" style="color: var(--text-2)"><span class="mono w-7 text-right" style="color: var(--muted)">{{ shirtOf(p) }}</span>{{ playerDisplayName(p) }}</div>
        </template>
      </section>
    </div>
  </template>
</template>

<style scoped>
.mark {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  flex-shrink: 0;
  background: var(--border-2);
}
.mark-goal {
  border-radius: 50%;
  background: var(--accent);
}
.mark-yellow {
  width: 8px;
  height: 11px;
  background: #e3b203;
}
.mark-red {
  width: 8px;
  height: 11px;
  background: #c8322a;
}
.mark-sub {
  border-radius: 50%;
  background: var(--info-fg);
}
.mark-var {
  background: var(--ink);
}
</style>
