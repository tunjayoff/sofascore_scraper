<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiGet, type JobRow } from '@/api/client'
import { useScrapeStore } from '@/stores/scrape'
import { useLeaguesStore } from '@/stores/leagues'
import { jobActionParts, isGenericJobTask } from '@/lib/jobLabel'

const { t } = useI18n()
const scrape = useScrapeStore()
const leagues = useLeaguesStore()
const jobs = ref<JobRow[]>([])
const loading = ref(true)

function leagueName(id?: number) {
  if (id == null) return ''
  return leagues.leagues.find((l) => l.id === id)?.name || `ID ${id}`
}

function jobTitle(j: JobRow) {
  const p = jobActionParts(j)
  const league = leagueName(p.leagueId)
  if (p.kind === 'details') {
    return league
      ? t('home_job_kind_details', { league, n: p.matchCount || '—' })
      : t('home_job_kind_details_short', { n: p.matchCount || '—' })
  }
  if (p.kind === 'seasons') {
    return league
      ? t('home_job_kind_seasons', { league, n: p.seasonCount })
      : t('home_job_kind_seasons_short', { n: p.seasonCount })
  }
  if (p.kind === 'league') return t('home_job_kind_league', { league: league || '—' })
  return t('home_job_kind_generic')
}

function jobStatusText(j: JobRow) {
  const s = String(j.status || '').toLowerCase()
  if (s === 'completed') return t('home_job_status_completed')
  if (s === 'cancelled') return t('home_job_status_cancelled')
  if (s === 'failed') return t('home_job_status_failed')
  if (s === 'running') return t('home_job_status_running')
  return j.status
}

function jobSubtitle(j: JobRow) {
  const task = String(j.current_task || '').trim()
  if (!isGenericJobTask(task)) return `${jobStatusText(j)} · ${task}`
  return `${jobStatusText(j)}${j.progress != null ? ` · ${j.progress}%` : ''}`
}

async function load() {
  loading.value = true
  try {
    await leagues.load()
    const data = await apiGet<{ jobs: JobRow[] }>('/api/jobs?limit=20')
    jobs.value = data.jobs || []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="mb-6 flex flex-wrap items-end justify-between gap-4">
    <div>
      <p class="text-sm font-semibold link-accent mb-1">{{ t('advanced_hint') }}</p>
      <h1 class="page-title mb-1">{{ t('jobs_title') }}</h1>
      <p class="text-[var(--muted)]">{{ t('jobs_subtitle') }}</p>
    </div>
    <button type="button" class="btn btn-secondary text-sm" @click="load">{{ t('stats_refresh') }}</button>
  </div>

  <div v-if="loading" class="text-[var(--muted)]">…</div>
  <div v-else-if="!jobs.length" class="panel p-8 text-[var(--muted)]">{{ t('home_no_jobs') }}</div>
  <div v-else class="panel divide-y divide-[var(--border)]">
    <div v-for="j in jobs" :key="j.id" class="p-4 flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0">
        <div class="font-bold">{{ jobTitle(j) }}</div>
        <div class="text-sm text-[var(--muted)] truncate">{{ jobSubtitle(j) }}</div>
        <div class="text-xs font-mono text-[var(--muted)] mt-1">{{ j.id }}</div>
        <div class="text-xs text-[var(--muted)]">{{ j.started_at }} → {{ j.finished_at || '…' }}</div>
      </div>
      <button
        v-if="j.status === 'running' && scrape.state.job_id === j.id"
        type="button"
        class="btn btn-danger text-sm"
        @click="scrape.cancel()"
      >
        {{ t('home_stop') }}
      </button>
    </div>
  </div>
</template>
