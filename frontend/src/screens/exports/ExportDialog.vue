<script setup lang="ts">
import { computed, onMounted, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import UiDialog from '@/ui/UiDialog.vue'
import UiIcon from '@/ui/UiIcon.vue'
import FormError from '@/ui/FormError.vue'
import { v1 } from '@/api/v1/client'
import type { ExportFilter, ExportJobSpec, FollowRecord, Job, TournamentRecord } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { loadSports, sportName, sports } from '@/app/sports'
import { startJob } from '@/screens/jobs/startJob'
import { num } from '@/ui/time'

/**
 * New export (6.10; FX-14b): what the API writes (SC-2, P28). Three kinds:
 *  - the match table of version 2 (`legacy-wide-csv`, CSV; filtered by league and match only);
 *  - normalized data (schema v1): matches, data types, score changes, betting odds (one row per outcome
 *    of each odds snapshot) or standings, as CSV, JSONL, Parquet or SQLite. Parquet needs pyarrow on the
 *    server: `/status.capabilities.parquet` tells, and without it the choice is shown disabled with why;
 *  - SofaScore's original data, unchanged (raw, JSONL): the match itself or every data type.
 * The filter is the export spec's: sport, added leagues, season and match numbers, status classes (not for
 * score changes) and a time range. The file is named after the league or dataset and the date (FX-19).
 * Added teams and single matches can be chosen too (FX-24 F13): the export filter has no team, so a team
 * stands for the matches of it stored now (`GET /events?participant=`), sent with a match as match numbers.
 * Added players cannot be chosen: neither the filter nor the event list knows the matches of a player.
 */
const emit = defineEmits<{ close: []; started: [Job] }>()
const { t } = useI18n()
const status = useStatusStore()
const uid = useId()

type Kind = 'legacy' | 'normalized' | 'raw'
type Dataset = NonNullable<ExportJobSpec['dataset']>
type Format = NonNullable<ExportJobSpec['format']>
const NORMALIZED: readonly Dataset[] = ['events', 'slices', 'changes', 'odds', 'standings']
const RAW: readonly Dataset[] = ['events', 'slices']
const FORMATS: readonly Format[] = ['csv', 'jsonl', 'parquet', 'sqlite']
const CLASSES = ['not_started', 'live', 'completed', 'decided_without_play', 'void', 'unknown'] as const

const kind = ref<Kind>('legacy')
const dataset = ref<Dataset>('events')
const format = ref<Format>('csv')
const sport = ref('')
const chosen = ref<number[]>([])
const seasonText = ref('')
const eventText = ref('')
const classes = ref<string[]>([])
const from = ref('')
const to = ref('')
const tournaments = ref<TournamentRecord[]>([])
const others = ref<FollowRecord[]>([])
const chosenFollows = ref<string[]>([])
/** The stored matches of each chosen team, read when it is chosen; null while reading, 'failed' if refused. */
const teamMatches = ref<Record<string, number[] | null | 'failed'>>({})
/** At most this many pages of a team's matches are read (200 each). */
const TEAM_PAGES = 10
const teamFollows = computed(() => others.value.filter((x) => x.kind === 'team'))
const matchFollows = computed(() => others.value.filter((x) => x.kind === 'event'))
const playerFollows = computed(() => others.value.filter((x) => x.kind === 'player'))
const busy = ref(false)
const error = ref<unknown>(null)

const parquet = computed(() => !!status.status?.capabilities?.parquet)
const legacy = computed(() => kind.value === 'legacy')
const datasets = computed(() => (kind.value === 'raw' ? RAW : NORMALIZED))
const changes = computed(() => kind.value === 'normalized' && dataset.value === 'changes')
watch(kind, () => {
  if (!datasets.value.includes(dataset.value)) dataset.value = 'events'
})
watch(parquet, (on) => {
  if (!on && format.value === 'parquet') format.value = 'csv'
})

function ids(text: string): number[] | null {
  const parts = text.split(/[\s,]+/).filter(Boolean)
  const out = parts.map(Number)
  return out.every((n) => Number.isInteger(n) && n > 0) ? out : null
}
const seasonIds = computed(() => ids(seasonText.value))
const typedEventIds = computed(() => ids(eventText.value))
/** The typed match numbers, the chosen single matches and the stored matches of the chosen teams. */
const eventIds = computed<number[] | null>(() => {
  const typed = typedEventIds.value
  if (typed === null) return null
  const out = new Set(typed)
  for (const id of chosenFollows.value) {
    const f = others.value.find((x) => x.id === id)
    if (f?.kind === 'event') out.add(f.entity_id)
    const found = teamMatches.value[id]
    if (f?.kind === 'team' && Array.isArray(found)) for (const e of found) out.add(e)
  }
  return [...out]
})
const readingTeams = computed(() => chosenFollows.value.some((id) => teamMatches.value[id] === null))

/** The stored matches of a team, page by page (this server only). */
async function readTeam(f: FollowRecord) {
  if (f.id in teamMatches.value) return
  teamMatches.value = { ...teamMatches.value, [f.id]: null }
  const found: number[] = []
  let cursor: string | null = null
  try {
    for (let i = 0; i < TEAM_PAGES; i++) {
      const r = await v1.events({ participant: [f.entity_id], limit: 200, cursor })
      found.push(...r.data.map((e) => e.id))
      cursor = r.page.next_cursor ?? null
      if (!cursor) break
    }
    teamMatches.value = { ...teamMatches.value, [f.id]: found }
    teamMore.value = { ...teamMore.value, [f.id]: !!cursor }
  } catch {
    teamMatches.value = { ...teamMatches.value, [f.id]: 'failed' }
  }
}
const teamMore = ref<Record<string, boolean>>({})
watch(chosenFollows, (list) => {
  for (const id of list) {
    const f = teamFollows.value.find((x) => x.id === id)
    if (f) void readTeam(f)
  }
})
function teamText(id: string): string {
  const found = teamMatches.value[id]
  if (found === undefined) return ''
  if (found === null) return t('ui.exports.dialog.followReading')
  if (found === 'failed') return t('ui.exports.dialog.followFailed')
  return teamMore.value[id] ? t('ui.exports.dialog.followMatchesMore', { n: num(found.length) }) : t('ui.exports.dialog.followMatches', { n: num(found.length) })
}
const rangeOk = computed(() => !from.value || !to.value || from.value <= to.value)
const valid = computed(
  () => seasonIds.value !== null && eventIds.value !== null && !readingTeams.value && rangeOk.value && (format.value !== 'parquet' || parquet.value || kind.value !== 'normalized'),
)

const spec = computed<ExportJobSpec>(() => {
  const filter: ExportFilter = {
    sport: legacy.value ? null : sport.value || null,
    tournament_ids: chosen.value,
    season_ids: legacy.value || changes.value ? [] : (seasonIds.value ?? []),
    event_ids: eventIds.value ?? [],
  }
  if (legacy.value) return { dataset: 'events', format: 'csv', profile: 'legacy-wide-csv', filter }
  // what is left empty is not sent
  if (!changes.value && classes.value.length) filter.status_classes = classes.value as ExportFilter['status_classes']
  if (from.value) filter.from = from.value
  if (to.value) filter.to = to.value
  if (kind.value === 'raw') return { dataset: dataset.value, format: 'jsonl', schema: 'raw', profile: null, filter }
  return { dataset: dataset.value, format: format.value, schema: 'normalized', profile: null, filter }
})

async function submit() {
  if (!valid.value || busy.value) return
  busy.value = true
  error.value = null
  try {
    const job = await startJob({ kind: 'export', spec: spec.value })
    emit('started', job)
    emit('close')
  } catch (e) {
    error.value = e
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  void loadSports().catch(() => {})
  v1.tournaments({ followed: true, limit: 200 })
    .then((r) => (tournaments.value = r.data))
    .catch(() => {})
  v1.follows()
    .then((r) => (others.value = r.data.filter((x) => x.kind !== 'tournament')))
    .catch(() => {})
})
</script>

<template>
  <UiDialog :title="t('ui.exports.dialog.title')" :busy="busy" wide @close="emit('close')">
    <form id="export-form" class="flex flex-col gap-5" @submit.prevent="submit">
      <fieldset class="flex flex-col gap-2">
        <legend class="u-label">{{ t('ui.exports.dialog.what') }}</legend>
        <label v-for="k in ['legacy', 'normalized', 'raw'] as const" :key="k" class="u-option" :data-kind="k">
          <input v-model="kind" type="radio" :name="`${uid}-kind`" :value="k" class="u-check" />
          <span class="flex flex-col"><span class="font-semibold">{{ t(`ui.exports.kind.${k}`) }}</span><span class="u-small u-muted">{{ t(`ui.exports.dialog.hint.${k}`) }}</span></span>
        </label>
      </fieldset>

      <fieldset v-if="!legacy" class="flex flex-col gap-2" data-testid="export-datasets">
        <legend class="u-label">{{ t('ui.exports.dialog.dataset') }}</legend>
        <label v-for="d in datasets" :key="d" class="flex items-start gap-2" :data-dataset="d">
          <input v-model="dataset" type="radio" :name="`${uid}-dataset`" :value="d" class="u-check mt-1" />
          <span class="flex flex-col"><span>{{ t(`ui.exports.dataset.${d}`) }}</span><span class="u-small u-muted">{{ t(`ui.exports.datasetHint.${kind === 'raw' ? 'raw' : 'normalized'}.${d}`) }}</span></span>
        </label>
      </fieldset>

      <fieldset v-if="kind === 'normalized'" class="flex flex-col gap-2" data-testid="export-formats">
        <legend class="u-label">{{ t('ui.exports.dialog.format') }}</legend>
        <div class="flex flex-wrap gap-x-5 gap-y-2">
          <label v-for="fm in FORMATS" :key="fm" class="flex items-center gap-2" :data-format="fm">
            <input v-model="format" type="radio" :name="`${uid}-format`" :value="fm" class="u-check" :disabled="fm === 'parquet' && !parquet" />{{ t(`ui.exports.format.${fm}`) }}
          </label>
        </div>
        <p class="m-0 u-small u-muted">{{ t(`ui.exports.formatHint.${format}`) }}</p>
        <p v-if="!parquet" class="m-0 u-small u-muted" data-testid="export-no-parquet">{{ t('ui.exports.dialog.noParquet') }}</p>
      </fieldset>
      <div v-else class="flex flex-col gap-1">
        <p class="m-0 u-label">{{ t('ui.exports.dialog.format') }}</p>
        <p class="m-0">{{ legacy ? t('ui.exports.format.csv') : t('ui.exports.format.jsonl') }}</p>
      </div>

      <fieldset class="flex flex-col gap-3">
        <legend class="u-label">{{ t('ui.exports.dialog.filter') }}</legend>
        <label v-if="!legacy" class="flex flex-col">
          <span class="u-small">{{ t('ui.exports.dialog.sport') }}</span>
          <select v-model="sport" class="u-field">
            <option value="">{{ t('ui.filter.all') }}</option>
            <option v-for="s in sports" :key="s.slug" :value="s.slug">{{ sportName(s.slug) }}</option>
          </select>
        </label>
        <div class="flex flex-col gap-1">
          <span :id="`${uid}-tournaments`" class="u-small">{{ t('ui.exports.dialog.tournaments') }}</span>
          <div v-if="tournaments.length" class="flex flex-wrap gap-x-4 gap-y-2" role="group" :aria-labelledby="`${uid}-tournaments`">
            <label v-for="tour in tournaments" :key="tour.id" class="flex items-center gap-2">
              <input v-model="chosen" type="checkbox" :value="tour.id" class="u-check" />{{ tour.name ?? `#${tour.id}` }}
            </label>
          </div>
          <p v-else class="m-0 u-small u-muted">{{ t('ui.exports.dialog.noTournaments') }}</p>
          <p class="m-0 u-small u-muted">{{ t('ui.exports.dialog.allTournaments') }}</p>
        </div>
        <div v-if="others.length" class="flex flex-col gap-1" data-testid="export-follows">
          <span :id="`${uid}-follows`" class="u-small">{{ t('ui.exports.dialog.follows') }}</span>
          <div v-if="teamFollows.length || matchFollows.length" class="flex flex-col gap-2" role="group" :aria-labelledby="`${uid}-follows`">
            <label v-for="fl in [...teamFollows, ...matchFollows]" :key="fl.id" class="flex flex-wrap items-center gap-x-2 gap-y-1" :data-follow="fl.id">
              <input v-model="chosenFollows" type="checkbox" :value="fl.id" class="u-check" />{{ fl.name }}
              <span class="u-small u-muted">{{ t(`ui.follows.kind.${fl.kind}`) }}</span>
              <span v-if="fl.kind === 'team' && teamText(fl.id)" class="u-small u-muted" data-testid="export-team-matches">· {{ teamText(fl.id) }}</span>
            </label>
          </div>
          <p class="m-0 u-small u-muted">{{ t('ui.exports.dialog.followsNote') }}</p>
          <p v-if="playerFollows.length" class="m-0 u-small u-muted" data-testid="export-players-note">{{ t('ui.exports.dialog.playersNote') }}</p>
        </div>
        <label v-if="!legacy && !changes" class="flex flex-col">
          <span class="u-small">{{ t('ui.exports.dialog.seasonIds') }}</span>
          <input v-model="seasonText" class="u-field u-mono" inputmode="numeric" autocomplete="off" :aria-invalid="seasonIds === null" />
        </label>
        <label class="flex flex-col">
          <span class="u-small">{{ t('ui.exports.dialog.eventIds') }}</span>
          <input v-model="eventText" class="u-field u-mono" inputmode="numeric" autocomplete="off" :aria-invalid="typedEventIds === null" />
        </label>
        <div v-if="!legacy && !changes" class="flex flex-col gap-1">
          <span :id="`${uid}-classes`" class="u-small">{{ t('ui.exports.dialog.classes') }}</span>
          <div class="flex flex-wrap gap-x-4 gap-y-2" role="group" :aria-labelledby="`${uid}-classes`">
            <label v-for="c in CLASSES" :key="c" class="flex items-center gap-2"><input v-model="classes" type="checkbox" :value="c" class="u-check" />{{ t(`ui.status.event.${c}`) }}</label>
          </div>
        </div>
        <div v-if="!legacy" class="grid gap-3 sm:grid-cols-2">
          <label class="flex flex-col">
            <span class="u-small">{{ changes ? t('ui.exports.dialog.recordedFrom') : t('ui.exports.dialog.from') }}</span>
            <input v-model="from" type="date" class="u-field" :aria-invalid="!rangeOk" />
          </label>
          <label class="flex flex-col">
            <span class="u-small">{{ changes ? t('ui.exports.dialog.recordedTo') : t('ui.exports.dialog.to') }}</span>
            <input v-model="to" type="date" class="u-field" :aria-invalid="!rangeOk" />
          </label>
        </div>
        <p v-if="seasonIds === null || typedEventIds === null" class="m-0 u-small" role="alert" style="color: var(--danger)">{{ t('ui.exports.dialog.badIds') }}</p>
        <p v-if="!rangeOk" class="m-0 u-small" role="alert" style="color: var(--danger)">{{ t('ui.exports.dialog.badRange') }}</p>
        <p v-if="legacy" class="m-0 u-small u-muted">{{ t('ui.exports.dialog.legacyFilter') }}</p>
      </fieldset>

      <p class="m-0 u-notice"><UiIcon name="info" :size="16" />{{ kind === 'raw' ? t('ui.exports.dialog.fullSize') : t('ui.exports.dialog.fileName') }}</p>
      <p class="m-0 u-small u-muted">{{ t('ui.jobs.start.local') }}</p>
      <FormError v-if="error" :error="error" :active-job-id="status.activeJob?.id" @navigate="emit('close')" />
    </form>
    <template #actions>
      <button type="button" class="u-btn" :disabled="busy" @click="emit('close')">{{ t('ui.common.cancel') }}</button>
      <button type="submit" form="export-form" class="u-btn u-btn-primary" :disabled="!valid || busy" data-testid="confirm">
        <span v-if="busy" class="u-spinner" aria-hidden="true"></span>{{ t('ui.exports.dialog.submit') }}
      </button>
    </template>
  </UiDialog>
</template>
