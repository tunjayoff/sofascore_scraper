import { computed, onUnmounted, ref, watch, type Ref } from 'vue'
import { useRoute, useRouter, type LocationQuery, type LocationQueryRaw } from 'vue-router'
import type { PageInfo } from '@/api/v1/schema'

/**
 * A list screen's page of rows (05-web-ui.md 4.8): Previous / Next by cursor with the page size and no page
 * count (decision 14). Filters, the page size and the cursor live in the query string, so a view can be
 * bookmarked; "Previous" walks back through the cursors seen in this visit. The screen names the query keys
 * the server filters by (`serverKeys`): a change of one of them reloads the list, other keys (filters
 * within the page) do not.
 */
export const PAGE_SIZES = [25, 50, 100] as const

export type Page<T> = { data: T[]; page: PageInfo }
export type PageRequest = { cursor: string | null; size: number; signal: AbortSignal }

/** One text value of the query string ('' when missing or repeated). */
export function queryText(q: LocationQuery, key: string): string {
  const v = q[key]
  return typeof v === 'string' ? v : ''
}

/** Every value of a repeated query key (`?status=a&status=b`). */
export function queryList(q: LocationQuery, key: string): string[] {
  const v = q[key]
  if (Array.isArray(v)) return v.filter((x): x is string => typeof x === 'string' && x !== '')
  return typeof v === 'string' && v ? [v] : []
}

/** Positive integer ids of a query key; anything else is dropped. */
export function queryIds(q: LocationQuery, key: string): number[] {
  return queryList(q, key)
    .flatMap((x) => x.split(','))
    .map((x) => Number(x.trim()))
    .filter((n) => Number.isInteger(n) && n > 0)
}

export function usePagedList<T>(
  fetchPage: (req: PageRequest) => Promise<Page<T>>,
  opts: { serverKeys: readonly string[]; defaultSize?: number; enabled?: () => boolean },
) {
  const route = useRoute()
  const router = useRouter()
  const path = route.path
  const defaultSize = opts.defaultSize ?? 25

  const rows = ref([]) as Ref<T[]>
  const next = ref<string | null>(null)
  const loading = ref(true)
  const loadedOnce = ref(false)
  const error = ref<unknown>(null)
  /** Cursors of the pages before this one, for "Previous". */
  const back = ref<(string | null)[]>([])

  const size = computed(() => {
    const n = Number(route.query.size)
    return (PAGE_SIZES as readonly number[]).includes(n) ? n : defaultSize
  })
  const cursor = computed(() => queryText(route.query, 'cursor') || null)

  let controller: AbortController | null = null
  async function load() {
    if (opts.enabled && !opts.enabled()) {
      loading.value = false
      return
    }
    controller?.abort()
    const mine = (controller = new AbortController())
    loading.value = true
    try {
      const page = await fetchPage({ cursor: cursor.value, size: size.value, signal: mine.signal })
      if (mine.signal.aborted) return
      rows.value = page.data
      next.value = page.page.next_cursor ?? null
      error.value = null
      loadedOnce.value = true
    } catch (e) {
      if ((e as Error)?.name === 'AbortError' || mine.signal.aborted) return
      error.value = e
    } finally {
      if (controller === mine) loading.value = false
    }
  }

  /** Changes the query string; a filter change starts again at the first page. */
  function setQuery(patch: Record<string, string | number | boolean | readonly (string | number)[] | null | undefined>, keepCursor = false) {
    const query: LocationQueryRaw = { ...route.query }
    if (!keepCursor) {
      delete query.cursor
      back.value = []
    }
    for (const [k, v] of Object.entries(patch)) {
      const empty = v == null || v === '' || v === false || (Array.isArray(v) && !v.length) || (k === 'size' && v === defaultSize)
      if (empty) delete query[k]
      else query[k] = Array.isArray(v) ? v.map(String) : String(v)
    }
    void router.replace({ query })
  }

  function prev() {
    const stack = [...back.value]
    const c = stack.pop() ?? null
    back.value = stack
    setQuery({ cursor: c }, true)
  }

  function forward() {
    if (!next.value) return
    back.value = [...back.value, cursor.value]
    setQuery({ cursor: next.value }, true)
  }

  const serverState = computed(() => JSON.stringify([...opts.serverKeys.map((k) => route.query[k] ?? null), cursor.value, size.value]))
  watch(serverState, () => {
    if (route.path === path) void load()
  })
  onUnmounted(() => controller?.abort())

  return { rows, next, loading, loadedOnce, error, size, cursor, hasPrev: computed(() => !!cursor.value), load, setQuery, prev, forward }
}
