<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import UiTabs from '@/ui/UiTabs.vue'
import UiIcon from '@/ui/UiIcon.vue'
import UiBadge from '@/ui/UiBadge.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import FactList from '@/ui/FactList.vue'
import TimeText from '@/ui/TimeText.vue'
import EmptyState from '@/ui/EmptyState.vue'
import ErrorState from '@/ui/ErrorState.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import { v1, V1Error } from '@/api/v1/client'
import type { FollowRecord, Job, SeasonEntry, TournamentRecord } from '@/api/v1/schema'
import { isIndividual, sportName } from '@/app/sports'
import { useStatusStore } from '@/app/statusStore'
import { onJobEnded } from '@/app/jobWatch'
import { num, pct } from '@/ui/time'
import StartJobDialog from '@/screens/jobs/StartJobDialog.vue'
import { faceText, jobKindText, jobLeague, jobTarget, noteFollowNames } from '@/screens/jobs/jobText'
import { sliceLabel } from '@/screens/events/eventText'
import EventsList from '@/screens/events/EventsList.vue'
import FollowActions from './FollowActions.vue'
import MoveFollow from './MoveFollow.vue'
import SlicePicker from './SlicePicker.vue'
import { dataText, hasOdds, lastSyncOf, lockReason, placeName, seasonsText, syncIncludes } from './followText'

/**
 * Follow detail (6.4): one follow with its seasons, its matches, its data selection and its downloads.
 * A league has a Seasons tab with each stored season's counts (`include=counts`, FX-13: matches, finished,
 * with details, complete) and the age of its schedule; a league whose season list was never read gets it
 * on a click. A team's Matches tab lists the stored matches it played; a single match links to it. The
 * Jobs tab lists the jobs that name this follow (`GET /jobs?target=<kind>:<id>`, FX-13/FX-19) and the
 * downloads of every follow. A follow of the old league list can be moved here. When a job of this follow
 * ends, its last download, the counts and the seasons are read again (FX-14a).
 */
const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const status = useStatusStore()

const kind = computed(() => String(route.params.kind))
const entityId = computed(() => Number(route.params.id))
const followId = computed(() => `${kind.value}:${entityId.value}`)

const follow = ref<FollowRecord | null>(null)
const tournament = ref<TournamentRecord | null>(null)
const seasons = ref<SeasonEntry[] | null>(null)
const seasonsError = ref<unknown>(null)
const jobs = ref<Job[] | null>(null)
const loading = ref(true)
const error = ref<unknown>(null)
const syncSeason = ref<SeasonEntry | null>(null)
const fetchMissing = ref(false)
const gettingSeasons = ref(false)

type Tab = 'seasons' | 'events' | 'data' | 'jobs'
const isTournament = computed(() => kind.value === 'tournament')
const TABS: Record<string, Tab[]> = { tournament: ['seasons', 'events', 'data', 'jobs'], team: ['events', 'data', 'jobs'] }
const allowed = computed<Tab[]>(() => TABS[kind.value] ?? ['data', 'jobs'])
const tab = computed<Tab>(() => {
  const q = String(route.query.tab ?? '')
  return allowed.value.includes(q as Tab) ? (q as Tab) : allowed.value[0]
})
const tabs = computed(() => allowed.value.map((k) => ({ key: k, label: t(`ui.followDetail.tab.${k}`) })))
function setTab(k: Tab) {
  // the events list keeps its own filters in the address; a new tab starts clean
  void router.replace({ query: { tab: k } })
}

const notFound = computed(() => error.value instanceof V1Error && error.value.code === 'not_found')
const coverage = computed(() => status.status?.summary?.tournaments.find((x) => x.tournament_id === entityId.value) ?? null)
/** The jobs of this follow and the downloads of every follow, newest first. */
const ownJobs = computed(() => (jobs.value ?? []).filter((j) => (follow.value && syncIncludes(follow.value, j)) || targets(j)))
const lastSync = computed(() => (follow.value ? lastSyncOf(follow.value, jobs.value ?? []) : null))

/** A job the server listed for this follow's target (a clear of the league, a fetch of its events …). */
const targeted = ref<Set<string>>(new Set())
const targets = (j: Job) => targeted.value.has(j.id)

async function load() {
  loading.value = true
  error.value = null
  try {
    follow.value = await v1.follow(followId.value)
    noteFollowNames([follow.value])
    if (isTournament.value) {
      loadTournament()
      loadSeasons()
    }
    loadJobs()
  } catch (e) {
    follow.value = null
    error.value = e
  } finally {
    loading.value = false
  }
}

/** The league's record: its category (country or region) is stored by its first download (FX-24 F5). */
function loadTournament() {
  v1.tournament(entityId.value)
    .then((x) => (tournament.value = x))
    .catch(() => (tournament.value = null))
}

/**
 * The line under the title (FX-24 F5): the sport, the league's country or region in the reader's language
 * and what is followed in one word ("Football · Europe · League"); the number is in the facts.
 */
const headerLine = computed(() => {
  const f = follow.value
  if (!f) return ''
  const category = tournament.value?.category
  const place = category ? placeName(category.country_code, category.name) : ''
  const kindWord = t(`ui.followDetail.kindShort.${f.kind === 'team' && isIndividual(f.sport) ? 'player' : f.kind}`)
  return [sportName(f.sport), place, kindWord].filter(Boolean).join(' · ')
})

function loadJobs() {
  const byTarget = v1.jobs({ target: followId.value, limit: 50 }).then((r) => r.data)
  const syncs = v1.jobs({ kind: ['sync'], limit: 50 }).then((r) => r.data)
  Promise.allSettled([byTarget, syncs]).then(([a, b]) => {
    const mine = a.status === 'fulfilled' ? a.value : []
    targeted.value = new Set(mine.map((j) => j.id))
    const all = new Map<string, Job>()
    for (const j of [...mine, ...(b.status === 'fulfilled' ? b.value : [])]) all.set(j.id, j)
    jobs.value = [...all.values()].sort((x, y) => String(y.created_at ?? '').localeCompare(String(x.created_at ?? '')))
  })
}

const stopListening = onJobEnded((job) => {
  const f = follow.value
  if (!f || !(syncIncludes(f, job) || jobLeague(job) === f.entity_id || targets(job))) return
  void status.refresh().catch(() => {})
  afterJob()
})

/** The end of a job of this follow: its jobs, the league and its seasons again (once, however it was seen). */
let lastAfterJob = 0
function afterJob() {
  if (Date.now() - lastAfterJob < 2000) return
  lastAfterJob = Date.now()
  loadJobs()
  if (isTournament.value) {
    loadTournament()
    loadSeasons()
  }
}
onUnmounted(stopListening)

function loadSeasons() {
  seasonsError.value = null
  v1.tournamentSeasons(entityId.value, undefined, true)
    .then((s) => (seasons.value = s))
    .catch((e) => {
      if (e instanceof V1Error && e.code === 'not_found') seasons.value = []
      else seasonsError.value = e
    })
}

/**
 * What keeps a season's detailed matches from "with all data" (FX-24 F6): each data type with the number of
 * matches that miss it ("Pre-game form (1)"). The share counts a data type SofaScore answered "no data" for
 * once as missing until a second answer confirms it, so a download that just ran can show 0 % with every
 * match detailed; the next download asks again.
 */
function missingText(s: SeasonEntry): string {
  const missing = Object.entries(s.counts?.missing ?? {}).filter(([, n]) => n > 0)
  if (!missing.length) return ''
  return t('ui.followDetail.missing', { list: missing.map(([key, n]) => t('ui.followDetail.missingItem', { name: sliceLabel(key), n: num(n) })).join(', ') })
}

/** A job that works on this follow runs now, in any process (`/status.active_job`). */
const runningHere = computed(() => {
  const j = status.activeJob
  const f = follow.value
  return !!j && !!f && (syncIncludes(f, j) || jobLeague(j) === f.entity_id || targets(j))
})
// While it runs the seasons follow the counts the status shows beside them; when it is gone the page reads
// everything again, also when its end was not seen by the job watch (a job of another process, FX-24 F6)
watch(
  () => status.fetchedAt,
  () => {
    if (runningHere.value && isTournament.value && tab.value === 'seasons') loadSeasons()
  },
)
watch(runningHere, (now, before) => {
  if (before && !now) afterJob()
})

/** Whether a season is covered by the follow's season rule, as far as the rule tells without dates. */
function followed(s: SeasonEntry): boolean | null {
  const rule = follow.value?.seasons
  if (Array.isArray(rule)) return rule.includes(s.id)
  if (rule === 'all') return true
  return null
}

function moved(f: FollowRecord) {
  follow.value = f
}

const facts = computed(() => {
  const f = follow.value
  if (!f) return []
  return [
    ...(f.kind !== 'event' ? [{ key: 'seasons', label: f.kind === 'tournament' ? t('ui.followDetail.fact.seasons') : t('ui.followEditor.window'), value: seasonsText(f.seasons, f.kind) }] : []),
    ...(f.kind === 'event' ? [{ key: 'match', label: t('ui.followDetail.fact.match') }] : []),
    { key: 'data', label: t('ui.followDetail.fact.data') },
    { key: 'live', label: t('ui.followDetail.fact.live'), value: f.live ? t('ui.follows.liveYes') : t('ui.common.no') },
    { key: 'enabled', label: t('ui.followDetail.fact.enabled'), value: f.enabled ? t('ui.common.yes') : t('ui.common.no') },
    { key: 'lastSync', label: t('ui.followDetail.fact.lastSync') },
    ...(f.kind === 'tournament' ? [{ key: 'coverage', label: t('ui.followDetail.fact.coverage') }] : []),
    { key: 'origin', label: t('ui.followDetail.fact.origin') },
    { key: 'created', label: t('ui.followDetail.fact.created') },
    { key: 'number', label: t('ui.followDetail.fact.number'), value: String(f.entity_id), mono: true },
  ]
})

watch(followId, () => void load())
onMounted(() => void load())
</script>

<template>
  <div>
    <div v-if="loading && !follow" class="u-card p-6"><SkeletonBlock :lines="6" /></div>

    <div v-else-if="notFound" class="u-card" data-testid="follow-not-found">
      <EmptyState icon="follows" :title="t('ui.followDetail.notFound')" :text="t('ui.followDetail.notFoundText', { id: followId })">
        <RouterLink to="/follows" class="u-btn">{{ t('ui.followDetail.back') }}</RouterLink>
      </EmptyState>
    </div>

    <div v-else-if="error && !follow" class="u-card"><ErrorState :error="error" @retry="load" /></div>

    <template v-else-if="follow">
      <PageHeader :title="follow.name" :crumbs="[{ label: t('ui.nav.follows'), to: '/follows' }]">
        <template #meta>
          <span class="u-small u-muted" data-testid="follow-header-line">{{ headerLine }}</span>
          <StatusBadge v-if="follow.origin !== 'api'" kind="origin" :value="follow.origin" />
          <UiBadge v-if="!follow.enabled" tone="neutral" icon="pause">{{ t('ui.follows.disabled') }}</UiBadge>
        </template>
        <template #actions>
          <FollowActions :follow="follow" @changed="(f) => (follow = f)" @removed="router.push('/follows')" />
        </template>
      </PageHeader>
      <p v-if="follow.origin === 'config'" class="m-0 mb-4 u-notice" data-testid="follow-locked"><UiIcon name="lock" :size="16" />{{ lockReason(follow) }}</p>
      <MoveFollow v-if="follow.origin === 'legacy'" class="mb-4" :follow="follow" @moved="moved" />

      <div class="grid gap-6 lg:grid-cols-3">
        <div class="lg:col-span-2 min-w-0">
          <UiTabs :tabs="tabs" :model-value="tab" id-prefix="follow" :label="t('ui.followDetail.tabs')" @update:model-value="setTab">
            <div v-if="tab === 'seasons'" data-testid="follow-seasons">
              <ErrorState v-if="seasonsError" compact :error="seasonsError" @retry="loadSeasons" />
              <SkeletonBlock v-else-if="!seasons" :lines="4" />
              <EmptyState v-else-if="!seasons.length" icon="events" :title="t('ui.followDetail.noSeasons')" :text="t('ui.followDetail.noSeasonsText')">
                <button type="button" class="u-btn" data-testid="follow-get-seasons" @click="gettingSeasons = true"><UiIcon name="external" :size="16" />{{ t('ui.seasonChooser.get') }}</button>
              </EmptyState>
              <ul v-else class="m-0 p-0 list-none">
                <li v-for="s in seasons" :key="s.id" class="flex flex-wrap items-center gap-3 py-3" style="border-top: 1px solid var(--line)" :data-season="s.id">
                  <span class="flex-1 min-w-[200px] flex flex-col gap-1">
                    <span>
                      <span class="font-semibold">{{ s.name ?? s.year ?? s.id }}</span>
                      <UiBadge v-if="followed(s) === false" tone="neutral" class="ml-2">{{ t('ui.followDetail.notFollowed') }}</UiBadge>
                    </span>
                    <span v-if="s.counts" class="u-small u-muted u-num inline-flex flex-wrap items-center gap-x-3" data-testid="season-counts">
                      <span>{{ t('ui.followDetail.seasonCounts', { events: num(s.counts.events), finished: num(s.counts.finished), details: num(s.counts.details) }) }}</span>
                      <span v-if="s.counts.details" class="inline-flex items-center gap-2" :title="t('ui.followDetail.completeHelp')" data-testid="season-complete"
                        ><span class="u-minibar" aria-hidden="true"><span :style="{ width: `${s.counts.completion_rate}%` }"></span></span
                        >{{ t('ui.followDetail.complete', { pct: pct(s.counts.completion_rate) }) }}</span
                      >
                      <span v-if="missingText(s)" :title="t('ui.followDetail.missingHelp')" data-testid="season-missing">{{ missingText(s) }}</span>
                      <span v-if="s.counts.schedule_fetched_at_utc">{{ t('ui.followDetail.scheduleRead') }} <TimeText :value="s.counts.schedule_fetched_at_utc" relative /></span>
                      <span v-else>{{ t('ui.followDetail.scheduleNever') }}</span>
                    </span>
                  </span>
                  <RouterLink :to="{ path: '/events', query: { tournament: String(entityId), season: String(s.id) } }" class="u-btn u-btn-sm u-btn-ghost">{{ t('ui.nav.events') }}</RouterLink>
                  <button type="button" class="u-btn u-btn-sm" :disabled="!follow.enabled" @click="syncSeason = s">{{ t('ui.followDetail.syncSeason') }}</button>
                </li>
              </ul>
              <p class="m-0 mt-3 u-small u-muted">{{ t('ui.followDetail.seasonsNote', { rule: seasonsText(follow.seasons) }) }}</p>
              <div class="mt-3 flex flex-wrap items-center gap-3">
                <button type="button" class="u-btn u-btn-sm" :disabled="!follow.enabled" data-testid="fetch-missing-league" @click="fetchMissing = true">
                  <UiIcon name="exports" :size="14" />{{ t('ui.followDetail.fetchMissing') }}
                </button>
                <span class="u-small u-muted">{{ t('ui.followDetail.fetchMissingHint') }}</span>
              </div>
            </div>

            <template v-else-if="tab === 'events'">
              <EventsList v-if="isTournament" :fixed-tournament="entityId" table-id="follow-events" />
              <EventsList v-else :fixed-participant="entityId" table-id="follow-team-events" />
            </template>

            <div v-else-if="tab === 'data'" class="flex flex-col gap-4">
              <SlicePicker :sport="follow.sport" :selection="follow.slices ?? null" />
            </div>

            <div v-else data-testid="follow-jobs">
              <SkeletonBlock v-if="!jobs" :lines="4" />
              <p v-else-if="!ownJobs.length" class="m-0 u-muted">{{ t('ui.followDetail.noJobs') }}</p>
              <ul v-else class="m-0 p-0 list-none">
                <li v-for="j in ownJobs" :key="j.id" class="flex flex-wrap items-center gap-3 py-2" style="border-top: 1px solid var(--line)" :data-job="j.id">
                  <StatusBadge kind="job" :value="j.state" />
                  <RouterLink :to="`/jobs/${j.id}`" class="flex-1 font-semibold">{{ jobKindText(j.kind, j.spec) }} · {{ jobTarget(j) }}</RouterLink>
                  <span class="u-small u-muted">{{ faceText(j.origin.face) }}</span>
                  <span class="u-small u-muted"><TimeText :value="j.started_at ?? j.created_at" /></span>
                </li>
              </ul>
              <p class="m-0 mt-3 u-small u-muted">{{ t('ui.followDetail.jobsNote') }}</p>
            </div>
          </UiTabs>
        </div>

        <aside class="u-card p-5 min-w-0 self-start" :aria-label="t('ui.followDetail.facts')" data-testid="follow-facts">
          <FactList :items="facts">
            <template #value-match>
              <RouterLink :to="`/events/${follow.entity_id}`" data-testid="follow-open-match">{{ t('ui.followDetail.openMatch') }}</RouterLink>
            </template>
            <template #value-data>
              <span class="inline-flex items-center gap-2">{{ dataText(follow.slices) }}<UiBadge v-if="hasOdds(follow.slices)" tone="info">{{ t('ui.follows.data.odds') }}</UiBadge></span>
            </template>
            <template #value-lastSync>
              <span v-if="lastSync" class="inline-flex items-center gap-2"><TimeText :value="lastSync.finished_at" relative /><StatusBadge kind="job" :value="lastSync.state" /></span>
              <span v-else>—</span>
            </template>
            <template #value-coverage>
              <span v-if="coverage" class="inline-flex flex-wrap items-center gap-2 u-num">
                <span class="u-minibar" aria-hidden="true"><span :style="{ width: `${coverage.coverage}%` }"></span></span>
                {{ pct(coverage.coverage) }} · {{ t('ui.followDetail.coverageText', { details: num(coverage.details), matches: num(coverage.matches) }) }}
              </span>
              <span v-else>—</span>
            </template>
            <template #value-origin>{{ t(`ui.status.origin.${follow.origin}`) }}</template>
            <template #value-created><TimeText :value="follow.created_at_utc" /></template>
          </FactList>
        </aside>
      </div>

      <StartJobDialog
        v-if="syncSeason"
        :body="{ kind: 'sync', spec: { selections: [{ league_id: entityId, season_ids: [syncSeason.id] }] } }"
        :title="t('ui.followDetail.syncSeasonTitle', { season: syncSeason.name ?? syncSeason.year ?? syncSeason.id })"
        :text="t('ui.follows.syncText')"
        @close="syncSeason = null"
      />
      <StartJobDialog
        v-if="fetchMissing"
        :body="{ kind: 'fetch', spec: { league_id: entityId } }"
        :title="t('ui.followDetail.fetchMissingTitle', { name: follow.name })"
        :text="t('ui.followDetail.fetchMissingText')"
        @close="fetchMissing = false"
      />
      <StartJobDialog
        v-if="gettingSeasons"
        :body="{ kind: 'sync', spec: { league_id: entityId, only: 'seasons' } }"
        :title="t('ui.seasonChooser.get')"
        :text="t('ui.seasonChooser.getNote')"
        @close="gettingSeasons = false"
      />
    </template>
  </div>
</template>
