<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { apiGet } from '@/api/client'
import { useLeaguesStore } from '@/stores/leagues'

const { t } = useI18n()
const router = useRouter()
const leagues = useLeaguesStore()
const searchQuery = ref('')
const results = ref<{ id: number; name: string; country?: string }[]>([])
const err = ref('')
const ok = ref('')

async function search() {
  if (searchQuery.value.length < 2) {
    results.value = []
    return
  }
  results.value = await apiGet(`/api/leagues/search-remote?q=${encodeURIComponent(searchQuery.value)}`)
}

async function add(id: number, name: string) {
  err.value = ''
  try {
    await leagues.add(id, name)
    ok.value = name
    searchQuery.value = ''
    results.value = []
  } catch (e) {
    err.value = String(e)
  }
}

async function remove(id: number, name: string) {
  if (!confirm(`Remove ${name}?`)) return
  await leagues.remove(id)
}

onMounted(() => leagues.load())
</script>

<template>
  <div class="mb-6">
    <p class="text-sm font-semibold link-accent mb-1">{{ t('advanced_hint') }}</p>
    <h1 class="page-title mb-1">{{ t('nav_leagues') }}</h1>
  </div>

  <div class="panel p-5 mb-6 space-y-3">
    <h2 class="font-bold">{{ t('home_step_add') }}</h2>
    <div v-if="ok" class="p-3 rounded-xl bg-[var(--primary-soft)] link-accent text-sm flex flex-wrap gap-3 items-center">
      <span>{{ ok }}</span>
      <button type="button" class="btn btn-primary text-sm" @click="router.push('/')">{{ t('home_go_download') }}</button>
    </div>
    <div v-if="err" class="text-sm text-red-600">{{ err }}</div>
    <input v-model="searchQuery" class="field-input" :placeholder="t('home_search_placeholder')" @input="search" />
    <div v-if="results.length" class="panel max-h-64 overflow-y-auto">
      <button
        v-for="r in results"
        :key="r.id"
        type="button"
        class="w-full text-left px-4 py-3 border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-2)]"
        @click="add(r.id, r.name)"
      >
        <span class="font-bold">{{ r.name }}</span>
        <span class="block text-xs text-[var(--muted)]">{{ r.country }} · {{ r.id }}</span>
      </button>
    </div>
  </div>

  <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
    <div v-for="lg in leagues.leagues" :key="lg.id" class="panel p-5">
      <h3 class="text-lg font-bold">{{ lg.name }}</h3>
      <p class="text-sm text-[var(--muted)] font-mono mb-4">ID {{ lg.id }}</p>
      <div class="flex flex-wrap gap-2">
        <button type="button" class="btn btn-primary text-sm" @click="router.push('/')">{{ t('home_download_btn') }}</button>
        <button type="button" class="btn btn-secondary text-sm" @click="router.push(`/matches?league_id=${lg.id}`)">{{ t('home_open_matches') }}</button>
        <button type="button" class="btn btn-danger text-sm ml-auto" @click="remove(lg.id, lg.name)">×</button>
      </div>
    </div>
  </div>
</template>
