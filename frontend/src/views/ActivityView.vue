<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, type JobRow } from '@/api/client'
import { useScrapeStore } from '@/stores/scrape'
import { useLeaguesStore } from '@/stores/leagues'
import { jobTitle, jobStatus, hasWarnings, toneBadge, formatWhen } from '@/lib/jobLabel'
import { errorText } from '@/lib/toast'
import JobCard from '@/components/JobCard.vue'

const { t, locale } = useI18n()
const scrape = useScrapeStore()
const leagues = useLeaguesStore()
const jobs = ref<JobRow[]>([])
const loading = ref(true)
const err = ref('')

async function load() {
  err.value = ''
  try {
    jobs.value = (await api.jobs(40)).jobs || []
  } catch (e) {
    err.value = errorText(e)
  } finally {
    loading.value = false
  }
}

function row(j: JobRow) {
  const st = jobStatus(j.status, false, hasWarnings(j), t)
  return {
    title: jobTitle(j.payload, leagues.nameOf, t),
    status: st.text,
    badge: toneBadge[st.tone],
    when: formatWhen(j.started_at, String(locale.value)),
    detail: [
      j.matches_total ? t('job.unit.details', { done: j.matches_done ?? 0, total: j.matches_total }) : '',
      j.matches_failed ? t('job.failedN', { n: j.matches_failed }) : '',
    ]
      .filter(Boolean)
      .join(' · '),
  }
}

onMounted(load)
onUnmounted(scrape.onFinished(() => void load()))
</script>

<template>
  <div class="mb-6">
    <h1 class="page-title">{{ t('activity.title') }}</h1>
    <p class="page-sub">{{ t('activity.sub') }}</p>
  </div>

  <template v-if="scrape.visible">
    <h2 class="section-label mb-3">{{ t('activity.current') }}</h2>
    <div class="mb-8 max-w-[760px]"><JobCard large /></div>
  </template>

  <h2 class="section-label mb-3">{{ t('activity.history') }}</h2>
  <div v-if="loading" class="flex items-center gap-3 py-10" style="color: var(--muted)"><span class="spinner"></span>{{ t('common.loading') }}</div>
  <div v-else-if="err" class="card p-6 flex flex-col items-start gap-3">
    <span style="color: var(--danger)">{{ err }}</span>
    <button type="button" class="btn" @click="load">{{ t('common.retry') }}</button>
  </div>
  <div v-else-if="!jobs.length" class="card p-8 page-sub">{{ t('activity.empty') }}</div>
  <div v-else class="card overflow-hidden">
    <div
      v-for="j in jobs"
      :key="j.id"
      class="table-row"
      style="grid-template-columns: minmax(0, 1fr) auto auto; border-top-color: var(--line)"
    >
      <div class="min-w-0 flex flex-col gap-1">
        <span class="font-semibold truncate">{{ row(j).title }}</span>
        <span v-if="row(j).detail" class="mono text-[13px]" style="color: var(--muted)">{{ row(j).detail }}</span>
      </div>
      <span :class="row(j).badge">{{ row(j).status }}</span>
      <span class="mono text-[13px] w-[120px] text-right max-sm:hidden" style="color: var(--muted)">{{ row(j).when }}</span>
    </div>
  </div>
</template>
