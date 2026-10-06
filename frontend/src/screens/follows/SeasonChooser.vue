<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import ErrorState from '@/ui/ErrorState.vue'
import FormError from '@/ui/FormError.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import { v1, V1Error } from '@/api/v1/client'
import type { Job, Season } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { startJob, waitForJob } from '@/screens/jobs/startJob'
import { jobErrorText } from '@/screens/jobs/jobText'

/**
 * "Choose seasons" of a league (6.3 step 2, G15; FX-14b): the stored season list as checkboxes, by name.
 * A league that was never downloaded has no season list yet: "Get the season list from SofaScore" starts
 * the season-list job (`sync` with `league_id` and `only: "seasons"`, FX-13), on that click only; the
 * dialog follows the job until it ends and then shows the seasons. A refusal (another job holds the data
 * folder) stays here with a link to that job.
 */
const props = defineProps<{ tournamentId: number; modelValue: number[]; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [number[]] }>()
const { t } = useI18n()
const status = useStatusStore()

const seasons = ref<Season[] | null>(null)
const error = ref<unknown>(null)
const job = ref<Job | null>(null)
const getting = ref(false)
const getError = ref<unknown>(null)
let controller: AbortController | null = null

async function load() {
  error.value = null
  try {
    seasons.value = await v1.tournamentSeasons(props.tournamentId)
  } catch (e) {
    if (e instanceof V1Error && e.code === 'not_found') seasons.value = []
    else error.value = e
  }
}
watch(
  () => props.tournamentId,
  () => {
    seasons.value = null
    job.value = null
    void load()
  },
  { immediate: true },
)

async function getList() {
  controller?.abort()
  controller = new AbortController()
  getting.value = true
  getError.value = null
  try {
    const started = await startJob({ kind: 'sync', spec: { league_id: props.tournamentId, only: 'seasons' } })
    job.value = started
    job.value = await waitForJob(started.id, { signal: controller.signal, onUpdate: (j) => (job.value = j) })
    await load()
  } catch (e) {
    if ((e as Error)?.name !== 'AbortError') getError.value = e
  } finally {
    getting.value = false
  }
}
onUnmounted(() => controller?.abort())

function toggle(id: number, on: boolean) {
  const next = props.modelValue.filter((x) => x !== id)
  if (on) next.push(id)
  emit('update:modelValue', next)
}
</script>

<template>
  <div class="flex flex-col gap-2" data-testid="season-chooser">
    <ErrorState v-if="error" compact :error="error" @retry="load" />
    <SkeletonBlock v-else-if="!seasons" :lines="3" />
    <template v-else-if="!seasons.length">
      <p class="m-0 u-small u-muted" data-testid="season-list-missing">{{ t('ui.seasonChooser.missing') }}</p>
      <div class="flex flex-wrap items-center gap-3">
        <button type="button" class="u-btn u-btn-sm" :disabled="getting || disabled" data-testid="season-list-get" @click="getList">
          <span v-if="getting" class="u-spinner" aria-hidden="true"></span><UiIcon v-else name="external" :size="14" />{{ t('ui.seasonChooser.get') }}
        </button>
        <span class="u-small u-muted">{{ t('ui.seasonChooser.getNote') }}</span>
      </div>
      <p v-if="getting" class="m-0 u-small" role="status" data-testid="season-list-running">{{ t('ui.seasonChooser.running') }}</p>
      <p v-else-if="job && job.state !== 'succeeded'" class="m-0 u-small" role="alert" style="color: var(--danger)" data-testid="season-list-failed">
        {{ t('ui.seasonChooser.failed') }} {{ jobErrorText(job) ?? '' }}
        <RouterLink :to="`/jobs/${job.id}`">{{ t('ui.jobs.openJob') }}</RouterLink>
      </p>
      <p v-else-if="job" class="m-0 u-small u-muted" data-testid="season-list-empty">{{ t('ui.seasonChooser.empty') }}</p>
      <FormError v-if="getError" :error="getError" :active-job-id="status.activeJob?.id" />
    </template>
    <fieldset v-else class="flex flex-col gap-2" :disabled="disabled" data-testid="season-list">
      <legend class="u-sr">{{ t('ui.followEditor.seasons') }}</legend>
      <label v-for="s in seasons" :key="s.id" class="flex items-center gap-2" data-testid="editor-season">
        <input type="checkbox" class="u-check" :value="s.id" :checked="modelValue.includes(s.id)" @change="toggle(s.id, ($event.target as HTMLInputElement).checked)" />
        <span>{{ s.name ?? s.year ?? s.id }}</span>
        <span v-if="s.name && s.year" class="u-small u-muted">{{ s.year }}</span>
      </label>
    </fieldset>
  </div>
</template>
