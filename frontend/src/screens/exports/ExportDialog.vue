<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import UiDialog from '@/ui/UiDialog.vue'
import UiIcon from '@/ui/UiIcon.vue'
import FormError from '@/ui/FormError.vue'
import { v1 } from '@/api/v1/client'
import type { ExportJobSpec, Job, TournamentRecord } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { loadSports, sportName, sports } from '@/app/sports'
import { startJob } from '@/screens/jobs/startJob'

/**
 * New export (6.10), with what the API writes today: the wide CSV of version 2 (`legacy-wide-csv`) and the
 * raw payloads as JSONL (events, or every slice). Normalized datasets and the Parquet and SQLite formats
 * are shown, disabled, with the reason (they answer 501 until SC-2; Parquet also needs pyarrow on the
 * server, which `/status.capabilities.parquet` tells). The filter is the set the export spec has: sport,
 * followed tournaments, season ids and event ids. Starting it is a job; a refusal stays in the dialog.
 */
const emit = defineEmits<{ close: []; started: [Job] }>()
const { t } = useI18n()
const status = useStatusStore()

type Kind = 'legacy' | 'raw' | 'normalized'
const kind = ref<Kind>('legacy')
const dataset = ref<'events' | 'slices'>('events')
const sport = ref('')
const chosen = ref<number[]>([])
const seasonText = ref('')
const eventText = ref('')
const tournaments = ref<TournamentRecord[]>([])
const busy = ref(false)
const error = ref<unknown>(null)

const parquet = computed(() => !!status.status?.capabilities?.parquet)
const legacy = computed(() => kind.value === 'legacy')

function ids(text: string): number[] | null {
  const parts = text.split(/[\s,]+/).filter(Boolean)
  const out = parts.map(Number)
  return out.every((n) => Number.isInteger(n) && n > 0) ? out : null
}
const seasonIds = computed(() => ids(seasonText.value))
const eventIds = computed(() => ids(eventText.value))
const valid = computed(() => kind.value !== 'normalized' && seasonIds.value !== null && eventIds.value !== null)

const spec = computed<ExportJobSpec>(() => {
  const filter = {
    sport: legacy.value ? null : sport.value || null,
    tournament_ids: chosen.value,
    season_ids: legacy.value ? [] : (seasonIds.value ?? []),
    event_ids: eventIds.value ?? [],
  }
  if (legacy.value) return { dataset: 'events', format: 'csv', profile: 'legacy-wide-csv', filter }
  return { dataset: dataset.value, format: 'jsonl', schema: 'raw', profile: null, filter }
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
})
</script>

<template>
  <UiDialog :title="t('ui.exports.dialog.title')" :busy="busy" wide @close="emit('close')">
    <form id="export-form" class="flex flex-col gap-5" @submit.prevent="submit">
      <fieldset class="flex flex-col gap-2">
        <legend class="u-label">{{ t('ui.exports.dialog.what') }}</legend>
        <label class="u-option">
          <input v-model="kind" type="radio" name="export-kind" value="legacy" class="u-check" />
          <span class="flex flex-col"><span class="font-semibold">{{ t('ui.exports.kind.legacy') }}</span><span class="u-small u-muted">{{ t('ui.exports.dialog.legacyHint') }}</span></span>
        </label>
        <label class="u-option">
          <input v-model="kind" type="radio" name="export-kind" value="raw" class="u-check" />
          <span class="flex flex-col"><span class="font-semibold">{{ t('ui.exports.kind.raw') }}</span><span class="u-small u-muted">{{ t('ui.exports.dialog.rawHint') }}</span></span>
        </label>
        <label class="u-option">
          <input v-model="kind" type="radio" name="export-kind" value="normalized" class="u-check" disabled />
          <span class="flex flex-col"><span class="font-semibold">{{ t('ui.exports.kind.normalized') }}</span><span class="u-small u-muted">{{ t('ui.exports.dialog.normalizedLater') }}</span></span>
        </label>
      </fieldset>

      <fieldset v-if="kind === 'raw'" class="flex flex-col gap-2">
        <legend class="u-label">{{ t('ui.exports.dialog.dataset') }}</legend>
        <div class="flex flex-wrap gap-4">
          <label class="flex items-center gap-2"><input v-model="dataset" type="radio" name="export-dataset" value="events" class="u-check" />{{ t('ui.exports.dataset.events') }}</label>
          <label class="flex items-center gap-2"><input v-model="dataset" type="radio" name="export-dataset" value="slices" class="u-check" />{{ t('ui.exports.dataset.slices') }}</label>
        </div>
        <p class="m-0 u-small u-muted">{{ dataset === 'events' ? t('ui.exports.dialog.eventsHint') : t('ui.exports.dialog.slicesHint') }}</p>
      </fieldset>

      <div class="flex flex-col gap-1">
        <p class="m-0 u-label">{{ t('ui.exports.dialog.format') }}</p>
        <p class="m-0"><span class="u-mono font-semibold">{{ legacy ? 'CSV' : 'JSONL' }}</span></p>
        <p class="m-0 u-small u-muted">{{ t('ui.exports.dialog.formatsLater') }} {{ parquet ? '' : t('ui.exports.dialog.noParquet') }}</p>
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
          <span id="export-tournaments" class="u-small">{{ t('ui.exports.dialog.tournaments') }}</span>
          <div v-if="tournaments.length" class="flex flex-wrap gap-x-4 gap-y-2" role="group" aria-labelledby="export-tournaments">
            <label v-for="tour in tournaments" :key="tour.id" class="flex items-center gap-2">
              <input v-model="chosen" type="checkbox" :value="tour.id" class="u-check" />{{ tour.name ?? `#${tour.id}` }}
            </label>
          </div>
          <p v-else class="m-0 u-small u-muted">{{ t('ui.exports.dialog.noTournaments') }}</p>
          <p class="m-0 u-small u-muted">{{ t('ui.exports.dialog.allTournaments') }}</p>
        </div>
        <label v-if="!legacy" class="flex flex-col">
          <span class="u-small">{{ t('ui.exports.dialog.seasonIds') }}</span>
          <input v-model="seasonText" class="u-field u-mono" inputmode="numeric" autocomplete="off" :aria-invalid="seasonIds === null" />
        </label>
        <label class="flex flex-col">
          <span class="u-small">{{ t('ui.exports.dialog.eventIds') }}</span>
          <input v-model="eventText" class="u-field u-mono" inputmode="numeric" autocomplete="off" :aria-invalid="eventIds === null" />
        </label>
        <p v-if="seasonIds === null || eventIds === null" class="m-0 u-small" role="alert" style="color: var(--danger)">{{ t('ui.exports.dialog.badIds') }}</p>
        <p v-if="legacy" class="m-0 u-small u-muted">{{ t('ui.exports.dialog.legacyFilter') }}</p>
      </fieldset>

      <p class="m-0 u-notice"><UiIcon name="info" :size="16" />{{ t('ui.exports.dialog.fullSize') }}</p>
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
