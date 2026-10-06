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
import type { FollowRecord, Job, Season, TournamentRecord } from '@/api/v1/schema'
import { sportName } from '@/app/sports'
import { useStatusStore } from '@/app/statusStore'
import { onJobEnded } from '@/app/jobWatch'
import { num, pct } from '@/ui/time'
import StartJobDialog from '@/screens/jobs/StartJobDialog.vue'
import { faceText, jobKindText, jobLeague } from '@/screens/jobs/jobText'
import EventsList from '@/screens/events/EventsList.vue'
import FollowActions from './FollowActions.vue'
import SlicePicker from './SlicePicker.vue'
import { dataText, hasOdds, lastSyncOf, lockReason, seasonsText, syncIncludes } from './followText'

/**
 * Follow detail (6.4): one follow with its seasons, its matches, its data selection and its jobs. The
 * facts panel says what is followed and how; a follow from the config file is locked. The per-season
 * counts are not in the API yet; the tournament's coverage comes from the data summary of `/status`. When a
 * download of this league ends, its last download, the counts and the seasons are read again (FX-14a).
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
const seasons = ref<Season[] | null>(null)
const seasonsError = ref<unknown>(null)
const jobs = ref<Job[] | null>(null)
const loading = ref(true)
const error = ref<unknown>(null)
const syncSeason = ref<Season | null>(null)
const fetchMissing = ref(false)

type Tab = 'seasons' | 'events' | 'data' | 'jobs'
const isTournament = computed(() => kind.value === 'tournament')
const tab = computed<Tab>(() => {
  const q = String(route.query.tab ?? '')
  const allowed: Tab[] = isTournament.value ? ['seasons', 'events', 'data', 'jobs'] : ['data', 'jobs']
  return allowed.includes(q as Tab) ? (q as Tab) : allowed[0]
})
const tabs = computed(() =>
  (isTournament.value ? (['seasons', 'events', 'data', 'jobs'] as Tab[]) : (['data', 'jobs'] as Tab[])).map((k) => ({ key: k, label: t(`ui.followDetail.tab.${k}`) })),
)
function setTab(k: Tab) {
  // the events list keeps its own filters in the address; a new tab starts clean
  void router.replace({ query: { tab: k } })
}

const notFound = computed(() => error.value instanceof V1Error && error.value.code === 'not_found')
const coverage = computed(() => status.status?.summary?.tournaments.find((x) => x.tournament_id === entityId.value) ?? null)
const ownJobs = computed(() => (jobs.value ?? []).filter((j) => follow.value && syncIncludes(follow.value, j)))
const lastSync = computed(() => (follow.value ? lastSyncOf(follow.value, jobs.value ?? []) : null))

async function load() {
  loading.value = true
  error.value = null
  try {
    follow.value = await v1.follow(followId.value)
    if (isTournament.value) {
      v1.tournament(entityId.value)
        .then((x) => (tournament.value = x))
        .catch(() => (tournament.value = null))
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

function loadJobs() {
  v1.jobs({ kind: ['sync'], limit: 50 })
    .then((r) => (jobs.value = r.data))
    .catch(() => (jobs.value = jobs.value ?? []))
}

const stopListening = onJobEnded((job) => {
  const f = follow.value
  if (!f || !(syncIncludes(f, job) || (job.kind === 'fetch' && jobLeague(job) === f.entity_id))) return
  loadJobs()
  void status.refresh().catch(() => {})
  if (isTournament.value) loadSeasons()
})
onUnmounted(stopListening)

function loadSeasons() {
  seasonsError.value = null
  v1.tournamentSeasons(entityId.value)
    .then((s) => (seasons.value = s))
    .catch((e) => (seasonsError.value = e))
}

/** Whether a season is covered by the follow's season rule, as far as the rule tells without dates. */
function followed(s: Season): boolean | null {
  const rule = follow.value?.seasons
  if (Array.isArray(rule)) return rule.includes(s.id)
  if (rule === 'all') return true
  return null
}

const facts = computed(() => {
  const f = follow.value
  if (!f) return []
  return [
    { key: 'seasons', label: t('ui.followDetail.fact.seasons'), value: seasonsText(f.seasons) },
    { key: 'data', label: t('ui.followDetail.fact.data') },
    { key: 'live', label: t('ui.followDetail.fact.live'), value: f.live ? t('ui.follows.liveYes') : t('ui.common.no') },
    { key: 'enabled', label: t('ui.followDetail.fact.enabled'), value: f.enabled ? t('ui.common.yes') : t('ui.common.no') },
    { key: 'lastSync', label: t('ui.followDetail.fact.lastSync') },
    { key: 'coverage', label: t('ui.followDetail.fact.coverage') },
    { key: 'origin', label: t('ui.followDetail.fact.origin') },
    { key: 'created', label: t('ui.followDetail.fact.created') },
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
          <span class="u-small u-muted">{{ [sportName(follow.sport), tournament?.category?.name, `${t(`ui.follows.kind.${follow.kind}`)} #${follow.entity_id}`].filter(Boolean).join(' · ') }}</span>
          <StatusBadge v-if="follow.origin !== 'api'" kind="origin" :value="follow.origin" />
          <UiBadge v-if="!follow.enabled" tone="neutral" icon="pause">{{ t('ui.follows.disabled') }}</UiBadge>
        </template>
        <template #actions>
          <FollowActions :follow="follow" @changed="(f) => (follow = f)" @removed="router.push('/follows')" />
        </template>
      </PageHeader>
      <p v-if="lockReason(follow)" class="m-0 mb-4 u-notice" data-testid="follow-locked"><UiIcon name="lock" :size="16" />{{ lockReason(follow) }}</p>

      <div class="grid gap-6 lg:grid-cols-3">
        <div class="lg:col-span-2 min-w-0">
          <UiTabs :tabs="tabs" :model-value="tab" id-prefix="follow" :label="t('ui.followDetail.tabs')" @update:model-value="setTab">
            <div v-if="tab === 'seasons'" data-testid="follow-seasons">
              <ErrorState v-if="seasonsError" compact :error="seasonsError" @retry="loadSeasons" />
              <SkeletonBlock v-else-if="!seasons" :lines="4" />
              <EmptyState v-else-if="!seasons.length" icon="events" :title="t('ui.followDetail.noSeasons')" :text="t('ui.followDetail.noSeasonsText')" />
              <ul v-else class="m-0 p-0 list-none">
                <li v-for="s in seasons" :key="s.id" class="flex flex-wrap items-center gap-3 py-3" style="border-top: 1px solid var(--line)">
                  <span class="flex-1 min-w-[160px]">
                    <span class="font-semibold">{{ s.year ?? s.name ?? s.id }}</span>
                    <span class="u-small u-muted"> · #{{ s.id }}</span>
                    <UiBadge v-if="followed(s) === false" tone="neutral" class="ml-2">{{ t('ui.followDetail.notFollowed') }}</UiBadge>
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

            <EventsList v-else-if="tab === 'events'" :fixed-tournament="entityId" table-id="follow-events" />

            <div v-else-if="tab === 'data'" class="flex flex-col gap-4">
              <SlicePicker :sport="follow.sport" :selection="follow.slices ?? null" />
            </div>

            <div v-else data-testid="follow-jobs">
              <SkeletonBlock v-if="!jobs" :lines="4" />
              <p v-else-if="!ownJobs.length" class="m-0 u-muted">{{ t('ui.followDetail.noJobs') }}</p>
              <ul v-else class="m-0 p-0 list-none">
                <li v-for="j in ownJobs" :key="j.id" class="flex flex-wrap items-center gap-3 py-2" style="border-top: 1px solid var(--line)">
                  <StatusBadge kind="job" :value="j.state" />
                  <RouterLink :to="`/jobs/${j.id}`" class="flex-1 font-semibold">{{ jobKindText(j.kind) }}</RouterLink>
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
        :title="t('ui.followDetail.syncSeasonTitle', { season: syncSeason.year ?? syncSeason.name ?? syncSeason.id })"
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
    </template>
  </div>
</template>
