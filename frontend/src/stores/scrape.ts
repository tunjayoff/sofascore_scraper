import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api, apiGet, apiSend, type FetchPayload, type ScrapeState } from '@/api/client'

const idle: ScrapeState = { job_id: null, is_running: false, status: 'Idle', progress: 0, current_task: '' }

/** The one background job the backend runs at a time: status via SSE, polling as fallback. */
export const useScrapeStore = defineStore('scrape', () => {
  const state = ref<ScrapeState>({ ...idle })
  const dismissedJob = ref<string | null>(null)
  let sse: EventSource | null = null
  let poll: ReturnType<typeof setInterval> | null = null
  const finishedListeners = new Set<() => void>()

  const isRunning = computed(() => !!state.value.is_running)
  const visible = computed(
    () => !!state.value.job_id && state.value.status !== 'Idle' && dismissedJob.value !== state.value.job_id,
  )

  function apply(s: ScrapeState) {
    const wasRunning = !!state.value.is_running
    state.value = s
    if (s.is_running) connectSSE()
    else if (wasRunning) finishedListeners.forEach((fn) => fn())
  }

  async function check() {
    try {
      apply(await apiGet<ScrapeState>('/api/scrape/status'))
    } catch {
      /* server briefly unavailable: keep last state */
    }
  }

  function connectSSE() {
    if (sse) return
    try {
      sse = new EventSource('/api/scrape/stream')
      sse.addEventListener('update', (e) => apply(JSON.parse((e as MessageEvent).data)))
      sse.addEventListener('done', (e) => {
        apply(JSON.parse((e as MessageEvent).data))
        closeSSE()
      })
      sse.onerror = () => closeSSE()
    } catch {
      sse = null
    }
  }

  function closeSSE() {
    sse?.close()
    sse = null
  }

  async function start(payload: FetchPayload) {
    await api.fetch(payload)
    dismissedJob.value = null
    await check()
    connectSSE()
  }

  async function cancel() {
    await apiSend('/api/scrape/cancel', 'POST')
    state.value = { ...state.value, cancel_requested: true }
    connectSSE()
  }

  function dismiss() {
    dismissedJob.value = state.value.job_id ?? null
  }

  function onFinished(fn: () => void) {
    finishedListeners.add(fn)
    return () => finishedListeners.delete(fn)
  }

  function init() {
    void check()
    poll = setInterval(check, 5000)
    window.addEventListener('pagehide', () => {
      closeSSE()
      if (poll) clearInterval(poll)
    })
  }

  return { state, isRunning, visible, start, cancel, dismiss, onFinished, init, check }
})
