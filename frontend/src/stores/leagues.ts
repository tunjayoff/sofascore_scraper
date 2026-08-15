import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiGet, apiSend, type League } from '@/api/client'

export const useLeaguesStore = defineStore('leagues', () => {
  const leagues = ref<League[]>([])
  const loading = ref(false)

  const isEmpty = computed(() => leagues.value.length === 0)
  const asMap = computed(() =>
    Object.fromEntries(leagues.value.map((l) => [String(l.id), l.name])),
  )

  async function load() {
    loading.value = true
    try {
      leagues.value = await apiGet<League[]>('/api/leagues')
    } finally {
      loading.value = false
    }
  }

  async function add(id: number, name: string) {
    await apiSend('/api/leagues', 'POST', { id, name })
    await load()
  }

  async function remove(id: number) {
    await apiSend(`/api/leagues/${id}`, 'DELETE')
    await load()
  }

  return { leagues, loading, isEmpty, asMap, load, add, remove }
})
