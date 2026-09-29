import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api, apiGet, apiSend, type FetchPayload, type ScrapeState } from '@/api/client'

const idle: ScrapeState = { job_id: null, is_running: false, status: 'Idle', progress: 0, current_task: '' }
const POLL_MS = 5000

/**
 * The one background job the backend runs at a time: status via SSE while it is open,
 * polling otherwise. Every state update carries a version so a slow poll response can't
 * overwrite a newer SSE event (which could revive `is_running` and fire listeners twice).
 */
export const useScrapeStore = defineStore('scrape', () => {
  const state = ref<ScrapeState>({ ...idle })
  const dismissedJob = ref<string | null>(null)
  let sse: EventSource | null = null
  let sseUnavailable = false
  let poll: ReturnType<typeof setInterval> | null = null
  let version = 0
  let notifiedJob: string | null = null
  let initialized = false
  const finishedListeners = new Set<() => void>()

  const isRunning = computed(() => !!state.value.is_running)
  const visible = computed(
    () => !!state.value.job_id && state.value.status !== 'Idle' && dismissedJob.value !== state.value.job_id,
  )

  function apply(s: ScrapeState) {
    const wasRunning = !!state.value.is_running
    state.value = s
    if (s.is_running) {
      connectSSE()
    } else if (wasRunning && s.job_id !== notifiedJob) {
      notifiedJob = s.job_id ?? null
      finishedListeners.forEach((fn) => fn())
    }
  }

  async function check() {
    if (sse) return // the stream is already pushing every change
    const v = ++version
    try {
      const s = await apiGet<ScrapeState>('/api/scrape/status')
      if (v === version) apply(s)
    } catch {
      /* server briefly unavailable: keep last state */
    }
  }

  function connectSSE() {
    if (sse || sseUnavailable) return
    let gotMessage = false
    const onEvent = (e: Event) => {
      gotMessage = true
      version++
      apply(JSON.parse((e as MessageEvent).data))
    }
    try {
      sse = new EventSource('/api/scrape/stream')
      sse.addEventListener('update', onEvent)
      sse.addEventListener('done', (e) => {
        onEvent(e)
        closeSSE()
      })
      sse.onerror = () => {
        // Failing before the first message means the endpoint isn't there (e.g. 501): stay on polling
        if (!gotMessage) sseUnavailable = true
        closeSSE()
      }
    } catch {
      sse = null
      sseUnavailable = true
    }
  }

  function closeSSE() {
    sse?.close()
    sse = null
  }

  function startPolling() {
    if (!poll) poll = setInterval(check, POLL_MS)
  }

  function stopPolling() {
    if (poll) clearInterval(poll)
    poll = null
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
    if (initialized) return
    initialized = true
    void check()
    startPolling()
    window.addEventListener('pagehide', () => {
      closeSSE()
      stopPolling()
    })
    // Back/forward cache restores the page without re-running init: resume updates
    window.addEventListener('pageshow', (e) => {
      if (!(e as PageTransitionEvent).persisted) return
      startPolling()
      void check()
    })
  }

  return { state, isRunning, visible, start, cancel, dismiss, onFinished, init, check }
})
