<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import UiIcon from '@/ui/UiIcon.vue'
import { v1, type StartJobBody } from '@/api/v1/client'
import type { Job } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { toast } from '@/ui/toast'
import { jobKindText } from './jobText'

/**
 * Starts a job after saying what it does (4.1 principle 3, 6.8): every kind sends requests to SofaScore,
 * so the dialog says so before the click. A refusal (409 while another job writes, 501 for a kind the API
 * cannot start yet) stays in the dialog with a link to the job that holds the data folder. On success a
 * toast links to the new job's detail (3.1).
 */
const props = defineProps<{ body: StartJobBody; again?: boolean }>()
const emit = defineEmits<{ close: []; started: [Job] }>()
const { t } = useI18n()
const status = useStatusStore()
const busy = ref(false)
const error = ref<unknown>(null)

const kind = computed(() => props.body.kind)
const title = computed(() => (props.again ? t('ui.jobs.start.againTitle', { kind: jobKindText(kind.value) }) : t(`ui.jobs.start.${kind.value}.title`)))

async function start() {
  busy.value = true
  error.value = null
  try {
    const job = await v1.startJob(props.body)
    toast({ kind: 'ok', text: t('ui.jobs.started', { kind: jobKindText(job.kind) }), link: { to: `/jobs/${job.id}`, label: t('ui.jobs.openJob') } })
    void status.refresh().catch(() => {})
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
    :title="title"
    :confirm-label="t('ui.jobs.start.confirm')"
    :busy="busy"
    :error="error"
    :active-job-id="status.activeJob?.id"
    @confirm="start"
    @close="emit('close')"
  >
    <p class="m-0">{{ t(`ui.jobs.start.${kind}.text`) }}</p>
    <p class="m-0 flex items-center gap-2 u-small" style="color: var(--warn-fg)"><UiIcon name="external" :size="14" />{{ t('ui.jobs.start.sendsRequests') }}</p>
    <p class="m-0 u-small u-muted">{{ t('ui.jobs.start.oneAtATime') }}</p>
  </ConfirmDialog>
</template>
