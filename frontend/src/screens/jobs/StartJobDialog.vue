<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import UiIcon from '@/ui/UiIcon.vue'
import type { StartJobBody } from '@/api/v1/client'
import type { Job } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { CALLS_SOFASCORE, jobKindText } from './jobText'
import { startJob } from './startJob'

/**
 * Starts a job after saying what it does (4.1 principle 3, 6.8): the kinds that send requests to SofaScore
 * say so before the click; the others say they work on the data folder only. A refusal (409 while another
 * job writes, 501 for a kind the API cannot start) stays in the dialog with a link to the job that holds
 * the data folder. On success a toast links to the new job's detail (3.1).
 */
const props = defineProps<{ body: StartJobBody; again?: boolean; title?: string; text?: string }>()
const emit = defineEmits<{ close: []; started: [Job] }>()
const { t, te } = useI18n()
const status = useStatusStore()
const busy = ref(false)
const error = ref<unknown>(null)

const kind = computed(() => props.body.kind)
const known = computed(() => te(`ui.jobs.start.${kind.value}.title`, 'en'))
const heading = computed(() =>
  props.title ?? (props.again || !known.value ? t('ui.jobs.start.againTitle', { kind: jobKindText(kind.value) }) : t(`ui.jobs.start.${kind.value}.title`)),
)
const callsSofascore = computed(() => CALLS_SOFASCORE.includes(kind.value))

async function start() {
  busy.value = true
  error.value = null
  try {
    const job = await startJob(props.body)
    emit('started', job)
    emit('close')
  } catch (e) {
    error.value = e
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <ConfirmDialog
    :title="heading"
    :confirm-label="t('ui.jobs.start.confirm')"
    :busy="busy"
    :error="error"
    :active-job-id="status.activeJob?.id"
    @confirm="start"
    @close="emit('close')"
  >
    <p v-if="text" class="m-0">{{ text }}</p>
    <p v-else-if="known" class="m-0">{{ t(`ui.jobs.start.${kind}.text`) }}</p>
    <p v-if="callsSofascore" class="m-0 flex items-center gap-2 u-small" style="color: var(--warn-fg)"><UiIcon name="external" :size="14" />{{ t('ui.jobs.start.sendsRequests') }}</p>
    <p v-else class="m-0 u-small u-muted">{{ t('ui.jobs.start.local') }}</p>
    <p class="m-0 u-small u-muted">{{ t('ui.jobs.start.oneAtATime') }}</p>
  </ConfirmDialog>
</template>
