<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiGet } from '@/api/client'

const { t } = useI18n()
const loading = ref(true)
const stats = ref<any>({
  leagues: 0,
  seasons: 0,
  matches: 0,
  details: 0,
  disk_usage: { formatted_total: '0 B', seasons: 0, matches: 0, details: 0, total: 1 },
  league_breakdown: [],
})

async function load() {
  loading.value = true
  try {
    stats.value = await apiGet('/api/stats/system')
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
      <h1 class="page-title mb-1">{{ t('nav_stats') }}</h1>
    </div>
    <div class="flex gap-2">
      <RouterLink to="/" class="btn btn-secondary text-sm">{{ t('nav_home') }}</RouterLink>
      <button type="button" class="btn btn-secondary text-sm" @click="load">{{ t('stats_refresh') }}</button>
    </div>
  </div>

  <div v-if="loading" class="text-[var(--muted)]">…</div>
  <template v-else>
    <div v-if="!stats.leagues && !stats.matches" class="panel p-10 text-center space-y-4">
      <h3 class="text-xl font-bold">{{ t('no_data_yet') }}</h3>
      <RouterLink to="/" class="btn btn-primary inline-flex">{{ t('home_go_download') }}</RouterLink>
    </div>
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
      <div class="panel p-5"><p class="text-xs text-[var(--muted)] uppercase">{{ t('total_leagues') }}</p><p class="text-3xl font-bold">{{ stats.leagues }}</p></div>
      <div class="panel p-5"><p class="text-xs text-[var(--muted)] uppercase">{{ t('total_seasons') }}</p><p class="text-3xl font-bold">{{ stats.seasons }}</p></div>
      <div class="panel p-5"><p class="text-xs text-[var(--muted)] uppercase">{{ t('total_matches') }}</p><p class="text-3xl font-bold">{{ stats.matches }}</p></div>
      <div class="panel p-5"><p class="text-xs text-[var(--muted)] uppercase">{{ t('stats_card_match_details') }}</p><p class="text-3xl font-bold">{{ stats.details }}</p></div>
    </div>
    <div class="panel p-5 mb-6">
      <h2 class="font-bold mb-3">{{ t('disk_usage_title') }} <span class="float-right link-accent">{{ stats.disk_usage?.formatted_total }}</span></h2>
    </div>
    <div v-if="stats.league_breakdown?.length" class="panel p-5 overflow-x-auto">
      <h2 class="font-bold mb-3">{{ t('stats_league_coverage_title') }}</h2>
      <table class="w-full text-sm">
        <thead class="text-[var(--muted)] text-left">
          <tr>
            <th class="py-2">{{ t('stats_col_league') }}</th>
            <th>{{ t('stats_col_matches_found') }}</th>
            <th>{{ t('stats_col_details_scraped') }}</th>
            <th>{{ t('stats_col_coverage') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="lg in stats.league_breakdown" :key="lg.id" class="border-t border-[var(--border)]">
            <td class="py-2 font-medium">{{ lg.name }}</td>
            <td>{{ lg.matches }}</td>
            <td>{{ lg.details }}</td>
            <td>{{ lg.coverage }}%</td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>
</template>
