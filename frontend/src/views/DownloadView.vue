<script setup lang="ts">
import { computed, inject, onMounted, reactive, ref, watch, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api, seasonLabel, type Season } from '@/api/client'
import { useLeaguesStore } from '@/stores/leagues'
import { useScrapeStore } from '@/stores/scrape'
import { useSportStore } from '@/stores/sport'
import { SPORTS } from '@/lib/sport'
import { toast, toastError, errorText } from '@/lib/toast'
import { num } from '@/lib/format'
import AppIcon from '@/components/AppIcon.vue'
import SportBadge from '@/components/SportBadge.vue'
import { latestOnly } from '@/lib/latest'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const leagues = useLeaguesStore()
const scrape = useScrapeStore()
const sport = useSportStore()
const openAdd = inject<() => void>('openAddLeague', () => {})
const lastAdded = inject<Ref<number | null>>('lastAddedLeague', ref(null))

type SeasonState = { list: Season[]; loading: boolean; refreshing: boolean; error: string }
const seasons = reactive<Record<number, SeasonState>>({})
const focusId = ref<number | null>(null)
/** leagueId -> picked season ids */
const picks = reactive<Record<number, number[]>>({})

/** The league column: one sport's leagues, or with "all", every league grouped by sport. */
const groups = computed(() => {
  if (sport.current !== 'all') return [{ key: sport.current as string, title: '', items: leagues.rows }]
  const out = SPORTS.map((s) => ({ key: s as string, title: t(`sport.${s}`), items: leagues.all.filter((l) => l.sport === s) }))
  out.push({ key: 'unknown', title: t('download.unknownGroup'), items: leagues.unknown })
  return out.filter((g) => g.items.length)
})
const visible = computed(() => groups.value.flatMap((g) => g.items))
const focus = computed(() => visible.value.find((l) => l.id === focusId.value) || null)
const focusSeasons = computed(() => (focusId.value != null ? seasons[focusId.value] : undefined))

/**
 * The reactive entry for a league. Not `seasons[id] ||= {...}`: that expression yields the plain
 * object, so later `s.loading = false` never reached the view and it stayed on "Loading seasons".
 */
function seasonState(id: number): SeasonState {
  if (!seasons[id]) seasons[id] = { list: [], loading: false, refreshing: false, error: '' }
  return seasons[id]
}

async function loadSeasons(id: number) {
  const s = seasonState(id)
  s.loading = true
  s.error = ''
  try {
    const r = await api.seasons(id)
    s.list = normalize(r.seasons)
  } catch (e) {
    s.error = errorText(e)
  } finally {
    s.loading = false
  }
  // Nothing on disk yet: fetch the list right away instead of asking for a button press.
  if (!s.list.length && !s.error) await refreshSeasons(id)
}

async function refreshSeasons(id: number) {
  const s = seasonState(id)
  s.refreshing = true
  s.error = ''
  try {
    const r = await api.refreshSeasons(id)
    s.list = normalize(r.seasons)
  } catch (e) {
    s.error = errorText(e)
  } finally {
    s.refreshing = false
  }
}

function normalize(raw: Season[] | undefined): Season[] {
  return (raw || []).map((x) => ({ ...x, id: Number(x.id) })).filter((x) => Number.isFinite(x.id))
}

function select(id: number) {
  focusId.value = id
  missing.value = null
  missingLeague.value = null
  missingChecks.next() // drop any in-flight check for the previous league
  if (!seasons[id]) void loadSeasons(id)
}

function isPicked(sid: number) {
  return focusId.value != null && (picks[focusId.value] || []).includes(sid)
}

function toggle(sid: number) {
  const id = focusId.value
  if (id == null) return
  const cur = picks[id] || []
  const next = cur.includes(sid) ? cur.filter((x) => x !== sid) : [...cur, sid]
  if (next.length) picks[id] = next
  else delete picks[id]
}

function preset(n: number) {
  const id = focusId.value
  if (id == null || !focusSeasons.value) return
  const ids = focusSeasons.value.list.slice(0, n).map((s) => s.id)
  if (ids.length) picks[id] = ids
}

function unpick(lid: number, sid: number) {
  const next = (picks[lid] || []).filter((x) => x !== sid)
  if (next.length) picks[lid] = next
  else delete picks[lid]
}

const basket = computed(() =>
  Object.entries(picks).map(([lid, sids]) => {
    const id = Number(lid)
    const list = seasons[id]?.list || []
    return {
      id,
      name: leagues.nameOf(id),
      seasons: sids.map((sid) => ({ id: sid, label: seasonLabel(list.find((s) => s.id === sid) || { id: sid }) })),
    }
  }),
)
const seasonCount = computed(() => basket.value.reduce((n, b) => n + b.seasons.length, 0))
const pickedIn = (id: number) => (picks[id] || []).length

const starting = ref(false)
async function start() {
  if (!seasonCount.value || scrape.isRunning) return
  starting.value = true
  try {
    await scrape.start({
      mode: 'full',
      selections: basket.value.map((b) => ({ league_id: b.id, season_ids: b.seasons.map((s) => s.id) })),
    })
    for (const k of Object.keys(picks)) delete picks[Number(k)]
    toast(t('download.started'))
  } catch (e) {
    toastError(e)
  } finally {
    starting.value = false
  }
}

// ---- missing details for the focused league ----
// The list remembers which league it belongs to: a slow response for league A must not
// be downloaded as league B after the user switched.
const missing = ref<number[] | null>(null)
const missingLeague = ref<number | null>(null)
const checkingMissing = ref(false)
const missingChecks = latestOnly()
async function checkMissing() {
  const leagueId = focusId.value
  if (leagueId == null) return
  const token = missingChecks.next()
  checkingMissing.value = true
  try {
    const r = await api.missingDetails(leagueId)
    if (!missingChecks.isCurrent(token) || focusId.value !== leagueId) return
    missing.value = (r.missing || []).map((m) => Number(m.match_id)).filter(Number.isFinite)
    missingLeague.value = leagueId
  } catch (e) {
    if (missingChecks.isCurrent(token)) toastError(e)
  } finally {
    if (missingChecks.isCurrent(token)) checkingMissing.value = false
  }
}
async function runMissing() {
  const leagueId = missingLeague.value
  if (leagueId == null || leagueId !== focusId.value || !missing.value?.length || scrape.isRunning) return
  try {
    await scrape.start({ mode: 'details', selections: [{ league_id: leagueId, season_ids: null, match_ids: missing.value }] })
    missing.value = null
    toast(t('download.started'))
  } catch (e) {
    toastError(e)
  }
}

function initialFocus() {
  const q = Number(route.query.league)
  // A league opened from elsewhere may belong to another sport: switch the sidebar to it.
  const target = leagues.all.find((l) => l.id === q)
  if (target && !visible.value.some((l) => l.id === q)) sport.set(target.sport ?? 'all')
  const id = target ? q : visible.value[0]?.id
  if (id != null) select(id)
  else focusId.value = null
}

onMounted(async () => {
  if (!leagues.loaded) await leagues.load().catch(() => {})
  initialFocus()
})
watch(() => route.query.league, () => leagues.loaded && initialFocus())
watch(
  () => sport.current,
  () => {
    if (!visible.value.some((l) => l.id === focusId.value)) {
      const first = visible.value[0]?.id
      if (first != null) select(first)
      else focusId.value = null
    }
  },
)
watch(lastAdded, (id) => {
  if (id == null) return
  const added = leagues.all.find((l) => l.id === id)
  if (added && !visible.value.some((l) => l.id === id)) sport.set(added.sport ?? 'all')
  select(id)
})
</script>

<template>
  <div class="mb-6">
    <h1 class="page-title">{{ t('download.title') }}</h1>
    <p class="page-sub">{{ t('download.sub') }}</p>
  </div>

  <div v-if="leagues.loaded && !visible.length" class="card p-10 flex flex-col items-center gap-4 text-center">
    <p class="page-sub">{{ leagues.isEmpty ? t('download.noLeagues') : t('download.noLeaguesSport') }}</p>
    <button type="button" class="btn btn-primary" @click="openAdd()"><AppIcon name="plus" :size="16" />{{ t('leagues.add') }}</button>
  </div>

  <div v-else class="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)_320px] lg:h-[calc(100vh-190px)] lg:min-h-[520px]">
    <!-- 1 league -->
    <section class="card flex flex-col min-h-0 overflow-hidden" :aria-label="t('download.step1')">
      <div class="col-head"><span class="num">1</span>{{ t('download.step1') }}</div>
      <div class="flex-1 overflow-y-auto p-2 max-h-[320px] lg:max-h-none">
        <div v-for="g in groups" :key="g.key" class="mb-2">
          <div v-if="g.title" class="section-label px-2.5 pt-2 pb-1">{{ g.title }}</div>
          <button
            v-for="l in g.items"
            :key="l.id"
            type="button"
            class="pick-item"
            :class="{ 'is-focus': l.id === focusId }"
            :aria-pressed="l.id === focusId"
            @click="select(l.id)"
          >
            <span class="font-semibold text-sm truncate">{{ l.name }}</span>
            <span v-if="pickedIn(l.id)" class="badge badge-ink">{{ t('download.selectedN', { n: pickedIn(l.id) }) }}</span>
          </button>
        </div>
        <button type="button" class="btn btn-ghost w-full !justify-start mt-1" style="color: var(--accent)" @click="openAdd()"><AppIcon name="plus" :size="16" />{{ t('leagues.add') }}</button>
      </div>
    </section>

    <!-- 2 season -->
    <section class="card flex flex-col min-h-0 overflow-hidden" :aria-label="t('download.step2')">
      <div class="col-head">
        <span class="num">2</span>{{ t('download.step2') }}
        <span v-if="focus" class="ml-auto flex items-center gap-2 min-w-0 text-[13px] font-medium" style="color: var(--muted)"><SportBadge :sport="focus.sport" /><span class="truncate">{{ focus.name }}</span></span>
      </div>
      <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
        <p v-if="!focus" class="page-sub text-sm">{{ t('download.pickLeague') }}</p>
        <div v-else-if="!focusSeasons || focusSeasons.loading || (focusSeasons.refreshing && !focusSeasons.list.length)" class="flex items-center gap-3 text-sm" style="color: var(--muted)">
          <span class="spinner"></span>{{ focusSeasons?.refreshing ? t('download.fetchingSeasons') : t('download.loadingSeasons') }}
        </div>
        <template v-else>
          <p v-if="focusSeasons.error" class="m-0 text-sm" style="color: var(--danger)">{{ focusSeasons.error }}</p>
          <div v-if="!focusSeasons.list.length" class="soft p-5 flex flex-col items-start gap-3">
            <span class="text-sm" style="color: var(--muted)">{{ t('download.noSeasons') }}</span>
            <button type="button" class="btn" :disabled="focusSeasons.refreshing" @click="refreshSeasons(focus.id)">
              <span v-if="focusSeasons.refreshing" class="spinner"></span><AppIcon v-else name="refresh" :size="16" />
              {{ focusSeasons.refreshing ? t('download.fetchingSeasons') : t('download.fetchSeasons') }}
            </button>
          </div>
          <template v-else>
            <div class="flex flex-wrap gap-2">
              <button type="button" class="btn btn-sm" @click="preset(1)">{{ t('download.latest') }}</button>
              <button type="button" class="btn btn-sm" @click="preset(3)">{{ t('download.last3') }}</button>
              <button type="button" class="btn btn-ghost btn-sm ml-auto" :disabled="focusSeasons.refreshing" :title="t('common.refresh')" @click="refreshSeasons(focus.id)">
                <span v-if="focusSeasons.refreshing" class="spinner"></span><AppIcon v-else name="refresh" :size="16" />{{ t('common.refresh') }}
              </button>
            </div>
            <div class="grid gap-1 sm:grid-cols-2 xl:grid-cols-3">
              <label v-for="s in focusSeasons.list" :key="s.id" class="season-item" :class="{ 'is-on': isPicked(s.id) }">
                <input type="checkbox" class="check" :checked="isPicked(s.id)" @change="toggle(s.id)" />
                <span class="mono text-sm font-medium">{{ seasonLabel(s) }}</span>
              </label>
            </div>
          </template>
        </template>

        <div v-if="focus && focus.matches" class="mt-auto pt-4 flex flex-col gap-2" style="border-top: 1px solid var(--line)">
          <span class="font-semibold text-sm">{{ t('download.missing.title') }}</span>
          <span class="text-[13px]" style="color: var(--muted)">{{ t('download.missing.body') }}</span>
          <div class="flex flex-wrap items-center gap-3">
            <button type="button" class="btn btn-sm" :disabled="checkingMissing" @click="checkMissing">
              {{ checkingMissing ? t('download.missing.checking') : t('download.missing.check') }}
            </button>
            <template v-if="missing">
              <span class="text-sm">{{ missing.length ? t('download.missing.found', { n: num(missing.length) }) : t('download.missing.none') }}</span>
              <button v-if="missing.length" type="button" class="btn btn-primary btn-sm" :disabled="scrape.isRunning" @click="runMissing">{{ t('download.missing.run') }}</button>
            </template>
          </div>
        </div>
      </div>
    </section>

    <!-- download list -->
    <section class="card flex flex-col min-h-0 overflow-hidden" :aria-label="t('download.list')">
      <div class="col-head">
        {{ t('download.list') }}
        <button v-if="seasonCount" type="button" class="btn btn-ghost btn-sm ml-auto" @click="Object.keys(picks).forEach((k) => delete picks[Number(k)])">{{ t('download.clear') }}</button>
      </div>
      <div class="flex-1 overflow-y-auto px-4 py-2">
        <p v-if="!seasonCount" class="page-sub text-sm my-2">{{ t('download.listEmpty') }}</p>
        <div v-for="b in basket" :key="b.id" class="py-3 flex flex-col gap-2" style="border-bottom: 1px solid var(--line)">
          <button type="button" class="text-left font-semibold text-sm p-0 border-0 bg-transparent cursor-pointer" style="color: var(--text)" @click="select(b.id)">{{ b.name }}</button>
          <div class="flex flex-wrap gap-1.5">
            <span v-for="s in b.seasons" :key="s.id" class="badge badge-neutral mono !pr-1">
              {{ s.label }}
              <button type="button" class="chip-x" :aria-label="t('common.remove') + ' ' + b.name + ' ' + s.label" @click="unpick(b.id, s.id)"><AppIcon name="x" :size="12" /></button>
            </span>
          </div>
        </div>
      </div>
      <div class="p-4 flex flex-col gap-3" style="border-top: 1px solid var(--line); background: var(--surface-2)">
        <p class="m-0 text-[13px] leading-relaxed" style="color: var(--muted)">{{ t('download.includes') }}</p>
        <p v-if="scrape.isRunning && seasonCount" class="m-0 text-[13px] flex gap-2" style="color: var(--warn-fg)"><AppIcon name="alert" :size="16" />{{ t('job.busy') }}</p>
        <p v-else-if="scrape.isRunning" class="m-0 text-[13px] flex items-center gap-2" style="color: var(--muted)"><span class="spinner"></span>{{ t('job.runningInfo') }}</p>
        <button type="button" class="btn btn-primary btn-lg w-full" :disabled="!seasonCount || scrape.isRunning || starting" @click="start">
          <AppIcon name="download" :size="18" />
          {{ seasonCount === 1 ? t('download.startOne') : t('download.start', { n: seasonCount || 0 }) }}
        </button>
        <button v-if="scrape.visible" type="button" class="btn btn-ghost btn-sm" @click="router.push('/activity')">{{ t('nav.activity') }} →</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.col-head {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 52px;
  padding: 0 16px;
  border-bottom: 1px solid var(--line);
  font-weight: 700;
}
.num {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--ink);
  color: var(--on-ink);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}
.pick-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  min-height: 52px;
  padding: 8px 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.pick-item:hover {
  background: var(--surface-2);
}
.pick-item.is-focus {
  background: var(--surface-3);
}
.season-item {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 44px;
  padding: 0 10px;
  border-radius: 8px;
  cursor: pointer;
}
.season-item:hover {
  background: var(--surface-2);
}
.season-item.is-on {
  background: var(--accent-soft);
}
.chip-x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.chip-x:hover {
  background: var(--hover);
}
</style>
