import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api, type League, type LeagueBreakdown, type RemoteLeague } from '@/api/client'
import { sportKey, type SportKey } from '@/lib/sport'
import { useSportStore } from '@/stores/sport'

export type LeagueView = { id: number; name: string; sport: SportKey | null; matches: number; details: number; coverage: number }

export const useLeaguesStore = defineStore('leagues', () => {
  const leagues = ref<League[]>([])
  const breakdown = ref<Record<number, LeagueBreakdown>>({})
  const loaded = ref(false)
  const loading = ref(false)
  const sport = useSportStore()

  const all = computed<LeagueView[]>(() =>
    leagues.value.map((l) => {
      const b = breakdown.value[l.id]
      return {
        id: l.id,
        name: l.name,
        sport: sportKey(l.sport),
        matches: b?.matches ?? 0,
        details: b?.details ?? 0,
        coverage: b?.coverage ?? 0,
      }
    }),
  )
  /** Leagues of the sport picked in the sidebar ("all" = every league). */
  const rows = computed(() => (sport.current === 'all' ? all.value : all.value.filter((l) => l.sport === sport.current)))
  /** Leagues nobody has told us the sport of yet. */
  const unknown = computed(() => all.value.filter((l) => !l.sport))
  const countBySport = computed(() => {
    const c: Record<string, number> = { all: all.value.length }
    for (const l of all.value) if (l.sport) c[l.sport] = (c[l.sport] || 0) + 1
    return c
  })
  const isEmpty = computed(() => loaded.value && leagues.value.length === 0)

  function nameOf(id: number | undefined | null) {
    if (id == null) return ''
    return leagues.value.find((l) => l.id === Number(id))?.name || `#${id}`
  }

  function sportOf(id: number | undefined | null): SportKey | null {
    return all.value.find((l) => l.id === Number(id))?.sport ?? null
  }

  async function load() {
    loading.value = true
    try {
      const [list, stats] = await Promise.all([api.leagues(), api.stats().catch(() => null)])
      leagues.value = list
      breakdown.value = Object.fromEntries((stats?.league_breakdown || []).map((b) => [Number(b.id), b]))
      loaded.value = true
    } finally {
      loading.value = false
    }
  }

  async function add(r: RemoteLeague) {
    await api.addLeague(r.id, r.name, sportKey(r.sport))
    await load()
  }

  async function setSport(id: number, s: SportKey | null) {
    const updated = await api.setLeagueSport(id, s)
    leagues.value = leagues.value.map((l) => (l.id === id ? { ...l, sport: updated.sport } : l))
  }

  async function remove(id: number) {
    await api.removeLeague(id)
    await load()
  }

  return { leagues, all, rows, unknown, countBySport, loaded, loading, isEmpty, nameOf, sportOf, load, add, setSport, remove }
})
