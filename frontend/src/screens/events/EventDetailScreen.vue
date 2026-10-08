<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import UiTabs from '@/ui/UiTabs.vue'
import UiMenu, { type MenuItem } from '@/ui/UiMenu.vue'
import UiIcon from '@/ui/UiIcon.vue'
import UiBadge from '@/ui/UiBadge.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import FactList from '@/ui/FactList.vue'
import TimeText from '@/ui/TimeText.vue'
import CopyButton from '@/ui/CopyButton.vue'
import EmptyState from '@/ui/EmptyState.vue'
import ErrorState from '@/ui/ErrorState.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import SidePanel from '@/ui/SidePanel.vue'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import DataTable, { type Column } from '@/ui/DataTable.vue'
import { v1, V1Error } from '@/api/v1/client'
import type { Change, Event, EventExtra, Slice } from '@/api/v1/schema'
import { copyText } from '@/ui/focus'
import { toast } from '@/ui/toast'
import { useStatusStore } from '@/app/statusStore'
import { sportName } from '@/app/sports'
import { startJob, waitForJob } from '@/screens/jobs/startJob'
import { MORE_FOLLOW_KINDS } from '@/screens/follows/followText'
import SliceTab from './SliceTab.vue'
import RawPayload from './RawPayload.vue'
import ChangeFields from './ChangeFields.vue'
import OddsView from './OddsView.vue'
import { awayName, eventTitle, fetchSelections, homeName, loadSeasons, loadTournaments, roundName, seasonName, sliceLabel, tournamentName } from './eventText'
import { aggregateScored, lineScore, scoreDetail, scoreText } from './scoreText'

/**
 * Event detail (6.6): everything stored about one match. The header with the score and the status, the
 * facts panel with the ids and the quality flags; tabs for the overview, the statistics, line-ups and
 * incidents (friendly views for football, basketball and tennis, the raw tree otherwise; decision 6),
 * every slice with its state (Data), the corrections recorded for it and, when an odds slice exists, the
 * odds. Every stored payload opens in the raw view, a side panel (decision 7). "Fetch again" re-reads the
 * event as a `fetch` job. The team names lead to Matches filtered by that team; the status is shown in
 * words, SofaScore's own values only under "Details" (FX-14a). The score follows the sport (scoreText.ts,
 * FX-26): cricket's wickets and overs with SofaScore's note, baseball's line score and series, legs, frames or
 * maps under a count, a fight's winner and method; venue, referee, note and series come from `/extra`.
 */
const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const status = useStatusStore()

const id = computed(() => Number(route.params.id))
const event = ref<Event | null>(null)
/** Header facts of the stored payload (note, series, venue, referee; FX-26); null until read or when it failed. */
const extra = ref<EventExtra | null>(null)
const slices = ref<Slice[]>([])
const loading = ref(true)
const error = ref<unknown>(null)
const odds = ref<Slice[]>([])
const changes = ref<Change[] | null>(null)
const changesError = ref<unknown>(null)
const raw = ref<{ key: string; sub: string | null } | null>(null)
const fetching = ref(false)
const fetchBusy = ref(false)
const fetchError = ref<unknown>(null)
let watcher: AbortController | null = null
onUnmounted(() => watcher?.abort())

type Tab = 'overview' | 'statistics' | 'lineups' | 'incidents' | 'data' | 'corrections' | 'odds'
const tab = computed<Tab>(() => {
  const q = String(route.query.tab ?? '')
  return (['overview', 'statistics', 'lineups', 'incidents', 'data', 'corrections', 'odds'] as Tab[]).includes(q as Tab) ? (q as Tab) : 'overview'
})
function setTab(k: Tab) {
  void router.replace({ query: { ...route.query, tab: k === 'overview' ? undefined : k } })
}

const notFound = computed(() => error.value instanceof V1Error && error.value.code === 'not_found')
const listingOnly = computed(() => event.value?.quality.source === 'listing')
const sliceOf = (key: string) => slices.value.find((s) => s.key === key) ?? null
const tabs = computed(() => {
  const list: { key: Tab; label: string; badge?: string }[] = [{ key: 'overview', label: t('ui.eventDetail.tab.overview') }]
  for (const k of ['statistics', 'lineups', 'incidents'] as const) if (sliceOf(k)) list.push({ key: k, label: t(`ui.eventDetail.tab.${k}`) })
  list.push({ key: 'data', label: t('ui.eventDetail.tab.data'), badge: String(slices.value.length) })
  list.push({ key: 'corrections', label: t('ui.eventDetail.tab.corrections'), badge: changes.value?.length ? String(changes.value.length) : undefined })
  if (odds.value.length) list.push({ key: 'odds', label: t('ui.eventDetail.tab.odds') })
  return list
})

async function load() {
  loading.value = true
  error.value = null
  try {
    const [e, s] = await Promise.all([v1.event(id.value), v1.eventSlices(id.value).catch(() => [] as Slice[])])
    event.value = e
    slices.value = s
    v1.eventExtra(id.value)
      .then((x) => (extra.value = x))
      .catch(() => (extra.value = null))
    if (e.tournament_id) void loadSeasons(e.tournament_id).catch(() => {})
    v1.eventOdds(id.value)
      .then((o) => (odds.value = o))
      .catch(() => (odds.value = []))
    loadChanges()
  } catch (err) {
    event.value = null
    error.value = err
  } finally {
    loading.value = false
  }
}

function loadChanges() {
  v1.changes({ event_id: id.value, order: 'desc', limit: 100 })
    .then((r) => {
      changes.value = r.data
      changesError.value = null
    })
    .catch((e) => (changesError.value = e))
}

/**
 * A provisional result (FX-26, M6): its badge is not in the header any more (next to "Ended" it read as an
 * uncertain result); the facts say why it is provisional and until when. A finished match counts as
 * provisional while it was last read within `refresh.window_hours` of its start (Settings › Requests); the
 * first update after that window reads it once more and it becomes final.
 */
const windowHours = ref<number | null>(null)
let windowAsked = false
watch(
  () => event.value?.quality.settlement,
  (s) => {
    if (s !== 'provisional' || windowAsked) return
    windowAsked = true
    v1.settings()
      .then((doc) => {
        const v = doc.settings.find((x) => x.key === 'refresh.window_hours')?.value
        windowHours.value = typeof v === 'number' && v > 0 ? v : null
      })
      .catch(() => {})
  },
)
const finalAfter = computed(() => {
  const start = event.value?.start_utc ? Date.parse(event.value.start_utc) : NaN
  return windowHours.value && Number.isFinite(start) ? new Date(start + windowHours.value * 3600_000).toISOString() : null
})

/** Baseball's inning-by-inning line (R, H, E); null for every other sport. */
const innings = computed(() => (event.value ? lineScore(event.value.score) : null))
/**
 * The aggregate of a two-legged tie: its score, or, when SofaScore gives only who went through (a cup match
 * decided some other way), that side; null without an aggregate (FX-26).
 */
const aggregate = computed(() => {
  const e = event.value
  const a = e?.aggregate
  if (!e || !a) return null
  if (aggregateScored(a)) return { score: `${a.home ?? '–'} – ${a.away ?? '–'}`, through: null }
  const through = a.winner === 'home' ? homeName(e) : a.winner === 'away' ? awayName(e) : null
  return through ? { score: null, through } : null
})
/** The lines under the score, with the series of the stored payload. */
const detailLines = computed(() => (event.value ? scoreDetail({ ...event.value, extra: extra.value }) : []))

const facts = computed(() => {
  const e = event.value
  if (!e) return []
  return [
    { key: 'id', label: t('ui.eventDetail.fact.id'), value: String(e.id), mono: true },
    { key: 'sport', label: t('ui.eventDetail.fact.sport'), value: sportName(e.sport) },
    { key: 'status', label: t('ui.eventDetail.fact.status') },
    { key: 'settlement', label: t('ui.eventDetail.fact.settlement') },
    { key: 'observed', label: t('ui.eventDetail.fact.observed') },
    { key: 'changed', label: t('ui.eventDetail.fact.changed') },
    { key: 'source', label: t('ui.eventDetail.fact.source'), value: t(`ui.eventDetail.source.${e.quality.source}`) },
    { key: 'raw', label: t('ui.eventDetail.fact.raw') },
  ]
})

const where = computed(() => {
  const e = event.value
  if (!e) return []
  return [
    e.tournament_id ? tournamentName(e.tournament_id) : (e.stage?.name ?? null),
    e.season_id ? seasonName(e.season_id) : null,
    roundName(e.round),
  ].filter(Boolean) as string[]
})

/** SofaScore's own status values ("finished / 100 · Ended"), shown only under Details. */
const statusRaw = computed(() => {
  const s = event.value?.status
  if (!s) return ''
  return [[s.type, s.code].filter((x) => x != null).join(' / '), s.description].filter(Boolean).join(' · ')
})

/** Matches of one team: the Matches list filtered by its name. */
function teamLink(name: string | null | undefined) {
  return name ? { path: '/events', query: { q: name } } : null
}

const sofascoreUrl = computed(() => {
  const e = event.value
  if (!e?.slug || !e.custom_id) return null
  return `https://www.sofascore.com/${encodeURIComponent(e.slug)}/${encodeURIComponent(e.custom_id)}#id:${e.id}`
})
/** "Follow this match", "Follow Chelsea": the follow editor with the kind, number, name and sport filled in. */
const followLinks = computed(() => {
  const e = event.value
  if (!e || !MORE_FOLLOW_KINDS) return {}
  const sport = e.sport ?? undefined
  const out: Record<string, { label: string; to: { path: string; query: Record<string, string | undefined> } }> = {
    'follow-match': { label: t('ui.eventDetail.followIt'), to: { path: '/follows/new', query: { kind: 'event', id: String(e.id), name: eventTitle(e), sport } } },
  }
  for (const side of ['home', 'away'] as const) {
    const p = e.participants[side]
    if (p?.id && p.name) out[`follow-${side}`] = { label: t('ui.eventDetail.followTeam', { name: p.name }), to: { path: '/follows/new', query: { kind: 'team', id: String(p.id), name: p.name, sport } } }
  }
  return out
})
const menu = computed<MenuItem[]>(() => [
  ...Object.entries(followLinks.value).map(([key, l]) => ({ key, label: l.label, icon: 'follows' as const })),
  { key: 'copy-id', label: t('ui.eventDetail.copyId'), icon: 'copy' },
  { key: 'copy-api', label: t('ui.eventDetail.copyApi'), icon: 'link' },
  ...(sofascoreUrl.value ? [{ key: 'sofascore', label: t('ui.eventDetail.openSofascore'), icon: 'external' as const, hint: t('ui.eventDetail.openSofascoreHint') }] : []),
])
async function onMenu(key: string) {
  const follow = followLinks.value[key]
  if (follow) void router.push(follow.to)
  else if (key === 'copy-id' || key === 'copy-api') {
    const text = key === 'copy-id' ? String(id.value) : `${window.location.origin}/api/v1/events/${id.value}`
    const ok = await copyText(text)
    toast({ kind: ok ? 'ok' : 'error', text: ok ? t('ui.json.copied') : t('ui.json.copyFailed') })
  } else if (key === 'sofascore' && sofascoreUrl.value) {
    // The user's own visit, opened only on this click; the app itself sends nothing to SofaScore (5.1)
    window.open(sofascoreUrl.value, '_blank', 'noopener,noreferrer')
  }
}

/**
 * Fetch again: by the match's league (`selections`), or by its number alone (`event_ids`, G16) when it has
 * no league or is not stored at all (the 404 page's "Fetch this match").
 */
async function fetchAgain() {
  const e = event.value
  fetchBusy.value = true
  fetchError.value = null
  try {
    const job = await startJob(e?.tournament_id ? { kind: 'fetch', spec: { selections: fetchSelections([e]).selections } } : { kind: 'fetch', spec: { event_ids: [id.value] } })
    fetching.value = false
    // When the job has ended the page reads the event again (6.6, navigation)
    watcher?.abort()
    watcher = new AbortController()
    void waitForJob(job.id, { everyMs: 3000, signal: watcher.signal })
      .then(() => load())
      .catch(() => {})
  } catch (err) {
    fetchError.value = err
  } finally {
    fetchBusy.value = false
  }
}

const sliceColumns = computed<Column<Slice>[]>(() => [
  { key: 'key', label: t('ui.eventDetail.col.key'), card: 'title' },
  { key: 'state', label: t('ui.eventDetail.col.state'), card: 'badge' },
  { key: 'fetched', label: t('ui.eventDetail.col.fetched'), card: 'meta' },
  { key: 'checked', label: t('ui.eventDetail.col.checked') },
  { key: 'raw', label: t('ui.eventDetail.col.raw') },
])
const missingSlices = computed(() => slices.value.filter((s) => s.state === 'error' || s.state === 'not_requested').length)

watch(id, () => {
  extra.value = null
  changes.value = null
  void load()
})
onMounted(() => {
  void loadTournaments()
  void load()
})
</script>

<template>
  <div>
    <div v-if="loading && !event" class="u-card p-6"><SkeletonBlock :lines="6" /></div>

    <div v-else-if="notFound" class="u-card" data-testid="event-not-found">
      <EmptyState icon="events" :title="t('ui.eventDetail.notFound')" :text="[t('ui.eventDetail.notFoundText', { id }), t('ui.eventDetail.notFoundFetch'), MORE_FOLLOW_KINDS ? t('ui.eventDetail.notFoundFollow') : ''].join(' ').trim()">
        <button type="button" class="u-btn u-btn-primary" data-testid="fetch-unknown" @click="fetching = true"><UiIcon name="exports" :size="16" />{{ t('ui.eventDetail.fetchIt') }}</button>
        <RouterLink v-if="MORE_FOLLOW_KINDS" :to="{ path: '/follows/new', query: { kind: 'event', id: String(id) } }" class="u-btn">{{ t('ui.eventDetail.followIt') }}</RouterLink>
        <RouterLink to="/events" class="u-btn u-btn-ghost">{{ t('ui.eventDetail.backToEvents') }}</RouterLink>
      </EmptyState>
    </div>

    <div v-else-if="error && !event" class="u-card"><ErrorState :error="error" @retry="load" /></div>

    <template v-else-if="event">
      <PageHeader :title="eventTitle(event)" :crumbs="[{ label: t('ui.nav.events'), to: '/events' }]">
        <template #meta>
          <span class="u-small u-muted">{{ where.join(' · ') }}</span>
          <span class="u-small u-muted"><TimeText :value="event.start_utc" /></span>
        </template>
        <template #actions>
          <button type="button" class="u-btn" :title="t('ui.jobs.start.sendsRequests')" @click="fetching = true">
            <UiIcon name="refresh" :size="16" />{{ listingOnly ? t('ui.eventDetail.fetchDetails') : t('ui.eventDetail.fetchAgain') }}
          </button>
          <UiMenu :label="t('ui.eventDetail.more')" icon="more" icon-only button-class="u-btn u-btn-icon" :items="menu" @select="onMenu" />
        </template>
      </PageHeader>

      <section class="u-card p-6 mb-6 flex flex-col items-center gap-2 text-center" data-testid="event-score">
        <div class="flex flex-wrap items-center justify-center gap-x-8 gap-y-2">
          <RouterLink
            v-if="teamLink(event.participants.home?.name)"
            :to="teamLink(event.participants.home?.name)!"
            class="u-h2 u-team-link"
            :title="t('ui.events.teamMatches', { name: homeName(event) })"
            data-testid="team-home"
            >{{ homeName(event) }}</RouterLink
          >
          <span v-else class="u-h2">{{ homeName(event) }}</span>
          <span class="u-display u-num">{{ scoreText(event) }}</span>
          <RouterLink
            v-if="teamLink(event.participants.away?.name)"
            :to="teamLink(event.participants.away?.name)!"
            class="u-h2 u-team-link"
            :title="t('ui.events.teamMatches', { name: awayName(event) })"
            data-testid="team-away"
            >{{ awayName(event) }}</RouterLink
          >
          <span v-else class="u-h2">{{ awayName(event) }}</span>
        </div>
        <p v-if="detailLines.length" class="m-0 u-small u-muted" data-testid="score-detail">{{ detailLines.join(' · ') }}</p>
        <p v-if="extra?.note" class="m-0 u-small" lang="en" data-testid="score-note">{{ extra.note }}</p>
        <div v-if="innings" class="u-table-scroll max-w-full" data-testid="line-score">
          <table class="u-table u-line-score">
            <caption class="u-sr">{{ t('ui.eventDetail.lineScore') }}</caption>
            <thead>
              <tr>
                <th scope="col"><span class="u-sr">{{ t('ui.eventDetail.lineScore') }}</span></th>
                <th v-for="(c, ci) in innings.columns" :key="ci" scope="col" class="text-right" :class="{ 'u-line-total': ci >= innings.columns.length - 3 }">{{ c }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in [{ name: homeName(event), cells: innings.home }, { name: awayName(event), cells: innings.away }]" :key="row.name">
                <th scope="row" class="text-left">{{ row.name }}</th>
                <td v-for="(c, ci) in row.cells" :key="ci" class="text-right u-num" :class="{ 'u-line-total': ci >= row.cells.length - 3 }">{{ c }}</td>
              </tr>
            </tbody>
          </table>
          <p class="m-0 u-small u-muted">{{ t('ui.eventDetail.lineScoreHelp') }}</p>
        </div>
        <p class="m-0 flex flex-wrap items-center justify-center gap-2">
          <StatusBadge kind="event" :value="event.status.class" />
          <UiBadge v-if="event.quality.stale" tone="warn" icon="alert">{{ t('ui.status.quality.stale') }}</UiBadge>
          <UiBadge v-if="event.quality.status_regressed" tone="warn" icon="alert">{{ t('ui.status.quality.regressed') }}</UiBadge>
        </p>
        <p v-if="event.status.class === 'live'" class="m-0 u-small u-muted">{{ t('ui.eventDetail.liveNote') }}</p>
      </section>

      <p v-if="listingOnly" class="m-0 mb-6 u-notice" data-testid="listing-only"><UiIcon name="info" :size="16" />{{ t('ui.eventDetail.listingOnly') }}</p>

      <div class="grid gap-6 lg:grid-cols-3">
        <div class="lg:col-span-2 min-w-0">
          <UiTabs :tabs="tabs" :model-value="tab" id-prefix="event" :label="t('ui.eventDetail.tabs')" @update:model-value="setTab">
            <div v-if="tab === 'overview'" class="flex flex-col gap-4" data-testid="event-overview">
              <dl class="u-result">
                <dt>{{ t('ui.eventDetail.fact.tournament') }}</dt>
                <dd>
                  <RouterLink v-if="event.tournament_id" :to="{ path: '/events', query: { tournament: String(event.tournament_id) } }">{{ tournamentName(event.tournament_id) }}</RouterLink>
                  <span v-else>{{ event.stage?.name ?? '—' }}</span>
                </dd>
                <template v-if="event.stage?.name && event.stage.name !== tournamentName(event.tournament_id)">
                  <dt>{{ t('ui.eventDetail.fact.stage') }}</dt>
                  <dd>{{ event.stage.name }}</dd>
                </template>
                <dt>{{ t('ui.eventDetail.fact.season') }}</dt>
                <dd>{{ seasonName(event.season_id) }}</dd>
                <dt>{{ t('ui.eventDetail.fact.start') }}</dt>
                <dd><TimeText :value="event.start_utc" /></dd>
                <template v-if="event.winner">
                  <dt>{{ t('ui.eventDetail.fact.winner') }}</dt>
                  <dd>{{ event.winner === 'draw' ? t('ui.eventDetail.draw') : event.winner === 'home' ? homeName(event) : awayName(event) }}</dd>
                </template>
                <template v-if="aggregate?.score">
                  <dt>{{ t('ui.eventDetail.fact.aggregate') }}</dt>
                  <dd class="u-num">{{ aggregate.score }}</dd>
                </template>
                <template v-else-if="aggregate?.through">
                  <dt>{{ t('ui.eventDetail.through') }}</dt>
                  <dd data-testid="aggregate-through">{{ aggregate.through }}</dd>
                </template>
                <template v-if="extra?.venue">
                  <dt>{{ t('ui.eventDetail.fact.venue') }}</dt>
                  <dd>{{ extra.venue }}</dd>
                </template>
                <template v-if="extra?.referee">
                  <dt>{{ t('ui.eventDetail.fact.referee') }}</dt>
                  <dd>{{ extra.referee }}</dd>
                </template>
              </dl>
            </div>

            <SliceTab
              v-else-if="tab === 'statistics' || tab === 'lineups' || tab === 'incidents'"
              :event-id="id"
              :slice-key="tab"
              :sport="event.sport"
              :home="homeName(event)"
              :away="awayName(event)"
              @raw="raw = { key: tab, sub: null }"
            />

            <div v-else-if="tab === 'data'" class="flex flex-col gap-3" data-testid="event-data">
              <DataTable table-id="event-slices" :caption="t('ui.eventDetail.tab.data')" :columns="sliceColumns" :rows="slices" :row-key="(s) => `${s.key}:${s.sub ?? ''}`" :paged="false">
                <template #cell-key="{ row }">{{ sliceLabel(row.key) }}<span v-if="row.sub" class="u-small u-muted"> · {{ row.sub }}</span></template>
                <template #cell-state="{ row }">
                  <span class="inline-flex flex-wrap items-center gap-2">
                    <StatusBadge kind="slice" :value="row.state" />
                    <span v-if="row.error" class="u-small u-muted">{{ t('ui.eventDetail.failedBecause', { reason: row.error.http_status ?? row.error.reason, n: row.error.count }) }}</span>
                  </span>
                </template>
                <template #cell-fetched="{ row }"><TimeText :value="row.fetched_at_utc" /></template>
                <template #cell-checked="{ row }"><TimeText :value="row.checked_at_utc" /></template>
                <template #cell-raw="{ row }">
                  <span v-if="row.has_payload" class="inline-flex gap-1">
                    <button type="button" class="u-btn u-btn-sm" :aria-label="t('ui.eventDetail.viewRawOf', { key: sliceLabel(row.key) })" @click="raw = { key: row.key, sub: row.sub }">{{ t('ui.eventDetail.view') }}</button>
                    <a :href="v1.rawUrl(id, row.key, row.sub)" :download="`event-${id}-${row.key}.json`" class="u-btn u-btn-sm u-btn-ghost u-btn-icon" :aria-label="t('ui.eventDetail.downloadOf', { key: sliceLabel(row.key) })"><UiIcon name="exports" :size="14" /></a>
                  </span>
                  <span v-else class="u-muted">—</span>
                </template>
                <template #empty><EmptyState icon="events" :title="t('ui.eventDetail.noSlices')" /></template>
              </DataTable>
              <div v-if="missingSlices && event.tournament_id" class="flex flex-wrap items-center gap-3">
                <button type="button" class="u-btn" data-testid="fetch-missing" @click="fetching = true"><UiIcon name="exports" :size="16" />{{ t('ui.events.bulk.missing') }}</button>
                <span class="u-small u-muted">{{ t('ui.jobs.start.sendsRequests') }}</span>
              </div>
            </div>

            <div v-else-if="tab === 'corrections'" data-testid="event-corrections">
              <ErrorState v-if="changesError" compact :error="changesError" @retry="loadChanges" />
              <p v-else-if="changes && !changes.length" class="m-0 u-muted">{{ t('ui.eventDetail.noChanges') }}</p>
              <ol v-else-if="changes" class="m-0 p-0 list-none flex flex-col">
                <li v-for="c in changes" :key="c.seq" class="flex flex-col gap-1 py-3" style="border-top: 1px solid var(--line)">
                  <span class="u-small u-muted"><TimeText :value="c.recorded_at_utc" /></span>
                  <ChangeFields :change="c" />
                </li>
              </ol>
              <SkeletonBlock v-else :lines="3" />
            </div>

            <div v-else-if="tab === 'odds'" class="flex flex-col gap-6" data-testid="event-odds">
              <OddsView :event-id="id" :slices="odds" :sport="event.sport" />
              <section class="flex flex-col gap-2" data-testid="odds-raw">
                <h2 class="u-h3">{{ t('ui.odds.raw') }}</h2>
                <p class="m-0 u-small u-muted">{{ t('ui.odds.rawNote') }}</p>
                <ul class="m-0 p-0 list-none">
                  <li v-for="o in odds" :key="`${o.key}:${o.sub}`" class="flex flex-wrap items-center gap-3 py-2" style="border-top: 1px solid var(--line)">
                    <span class="flex-1">{{ sliceLabel(o.key) }}<span v-if="o.sub" class="u-muted"> · {{ o.sub }}</span></span>
                    <StatusBadge kind="slice" :value="o.state" />
                    <button v-if="o.has_payload" type="button" class="u-btn u-btn-sm" :aria-label="t('ui.eventDetail.viewRawOf', { key: sliceLabel(o.key) })" @click="raw = { key: o.key, sub: o.sub }">{{ t('ui.eventDetail.view') }}</button>
                  </li>
                </ul>
              </section>
            </div>
          </UiTabs>
        </div>

        <aside class="u-card p-5 min-w-0 self-start" :aria-label="t('ui.eventDetail.facts')" data-testid="event-facts">
          <FactList :items="facts">
            <template #value-id><span class="inline-flex items-center gap-1">{{ event.id }}<CopyButton :text="String(event.id)" :label="t('ui.eventDetail.copyId')" /></span></template>
            <template #value-status>
              <span class="flex flex-col gap-1">
                <span><StatusBadge kind="event" :value="event.status.class" /></span>
                <details v-if="statusRaw" class="u-small u-muted" data-testid="status-details">
                  <summary>{{ t('ui.eventDetail.statusDetails') }}</summary>
                  <span>{{ t('ui.eventDetail.statusRaw') }}: </span><span class="u-mono" lang="en">{{ statusRaw }}</span>
                </details>
              </span>
            </template>
            <template #value-settlement>
              <span class="flex flex-col gap-1" data-testid="settlement">
                <span>{{ t(`ui.eventDetail.settled.${event.quality.settlement}`) }}</span>
                <span v-if="event.quality.settlement === 'provisional'" class="u-small u-muted" data-testid="settlement-why">
                  {{ windowHours ? t('ui.eventDetail.provisionalWhy', { h: windowHours }) : t('ui.eventDetail.provisionalWhyPlain') }}
                  <template v-if="finalAfter"> {{ t('ui.eventDetail.provisionalUntil') }} <TimeText :value="finalAfter" />.</template>
                </span>
              </span>
            </template>
            <template #value-observed><TimeText :value="event.quality.observed_at_utc" /></template>
            <template #value-changed><TimeText :value="event.quality.change_ts ? event.quality.change_ts * 1000 : null" /></template>
            <template #value-raw>
              <span v-if="!listingOnly" class="inline-flex gap-1">
                <button type="button" class="u-btn u-btn-sm" data-testid="raw-event" @click="raw = { key: 'event', sub: null }">{{ t('ui.eventDetail.view') }}</button>
                <a :href="v1.rawUrl(id, 'event')" :download="`event-${id}.json`" class="u-btn u-btn-sm u-btn-ghost u-btn-icon" :aria-label="t('ui.eventDetail.downloadOf', { key: sliceLabel('event') })"><UiIcon name="exports" :size="14" /></a>
              </span>
              <span v-else class="u-muted">—</span>
            </template>
          </FactList>
        </aside>
      </div>

      <SidePanel v-if="raw" :title="t('ui.raw.title', { key: sliceLabel(raw.key) })" @close="raw = null">
        <template #actions>
          <RouterLink :to="`/events/${id}/raw/${raw.key}${raw.sub ? `/${raw.sub}` : ''}`" class="u-btn u-btn-sm u-btn-ghost">{{ t('ui.raw.openPage') }}</RouterLink>
        </template>
        <RawPayload :event-id="id" :slice-key="raw.key" :sub="raw.sub" :slice="raw.key === 'event' ? null : slices.find((s) => s.key === raw!.key && s.sub === raw!.sub)" />
      </SidePanel>
    </template>

    <ConfirmDialog
      v-if="fetching"
      :title="!event ? t('ui.eventDetail.fetchIt') : listingOnly ? t('ui.eventDetail.fetchDetails') : t('ui.eventDetail.fetchAgainTitle')"
      :confirm-label="t('ui.jobs.start.confirm')"
      :busy="fetchBusy"
      :error="fetchError"
      :active-job-id="status.activeJob?.id"
      @confirm="fetchAgain"
      @close="fetching = false"
    >
      <p class="m-0">{{ t('ui.eventDetail.fetchAgainText') }}</p>
      <p class="m-0 flex items-center gap-2 u-small" style="color: var(--warn-fg)"><UiIcon name="external" :size="14" />{{ t('ui.jobs.start.sendsRequests') }}</p>
    </ConfirmDialog>
  </div>
</template>

<style>
/* beyzbolun devre devre skoru: küçük bir tablo, satır başlıkları takım adı (büyük harf ve zemin olmadan) */
.u-table.u-line-score {
  width: auto;
  margin: 0 auto;
}
.u-table.u-line-score th,
.u-table.u-line-score td {
  height: auto;
  padding: var(--sp-1) var(--sp-2);
  white-space: nowrap;
}
.u-table.u-line-score tbody th {
  background: none;
  border-top: 1px solid var(--line);
  text-transform: none;
  letter-spacing: normal;
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--text);
}
.u-table.u-line-score tbody tr:hover {
  background: none;
}
.u-line-score .u-line-total {
  font-weight: 600;
}
.u-app a.u-team-link {
  color: var(--text);
  text-decoration: underline;
  text-decoration-color: var(--border);
  text-underline-offset: 4px;
}
.u-app a.u-team-link:hover {
  text-decoration-color: currentColor;
}
</style>
