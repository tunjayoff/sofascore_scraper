import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiGet, apiSend, type ScrapeState } from '@/api/client'
import { progressBarMode } from '@/lib/progressBarMode'

const idle: ScrapeState = {
  job_id: null,
  is_running: false,
  status: 'Idle',
  progress: 0,
  current_task: '',
}

const TERMINAL = new Set(['Completed', 'Failed', 'Cancelled'])
const AUTO_DISMISS_MS = 6000

export const useScrapeStore = defineStore('scrape', () => {
  const state = ref<ScrapeState>({ ...idle })
  const minimized = ref(false)
  const dismissed = ref(false)
  let sse: EventSource | null = null
  let poll: ReturnType<typeof setInterval> | null = null
  let autoDismissTimer: ReturnType<typeof setTimeout> | null = null

  const isRunning = computed(() => !!state.value.is_running)
  const barMode = computed(() =>
    progressBarMode({
      isRunning: isRunning.value,
      status: String(state.value.status || 'Idle'),
      dismissed: dismissed.value,
      minimized: minimized.value,
    }),
  )
  const showBar = computed(() => barMode.value !== 'hidden')

  function clearAutoDismiss() {
    if (autoDismissTimer) {
      clearTimeout(autoDismissTimer)
      autoDismissTimer = null
    }
  }

  function scheduleAutoDismiss() {
    clearAutoDismiss()
    autoDismissTimer = setTimeout(() => {
      dismissed.value = true
      minimized.value = false
      autoDismissTimer = null
    }, AUTO_DISMISS_MS)
  }

  function applyStatus(s: ScrapeState) {
    const wasRunning = !!state.value.is_running
    const prev = String(state.value.status || 'Idle')
    const next = String(s.status || 'Idle')
    state.value = s

    if (s.is_running) {
      dismissed.value = false
      clearAutoDismiss()
      connectSSE()
      return
    }

    // Only on transition into a finished state (not every 5s poll)
    if (TERMINAL.has(next) && (wasRunning || prev !== next) && !dismissed.value) {
      scheduleAutoDismiss()
    }
  }

  async function checkStatus() {
    try {
      const s = await apiGet<ScrapeState>('/api/scrape/status')
      applyStatus(s)
    } catch {
      /* ignore */
    }
  }

  function connectSSE() {
    if (sse) return
    if (poll) {
      clearInterval(poll)
      poll = null
    }
    try {
      sse = new EventSource('/api/scrape/stream')
      sse.addEventListener('update', (e) => {
        applyStatus(JSON.parse((e as MessageEvent).data))
      })
      sse.addEventListener('done', (e) => {
        applyStatus(JSON.parse((e as MessageEvent).data))
        closeSSE()
      })
      sse.onerror = () => {
        closeSSE()
        poll = setInterval(() => checkStatus(), 3000)
      }
    } catch {
      poll = setInterval(() => checkStatus(), 3000)
    }
  }

  function closeSSE() {
    if (sse) {
      sse.close()
      sse = null
    }
  }

  function wake() {
    void checkStatus().then(() => {
      if (state.value.is_running) connectSSE()
    })
  }

  async function cancel() {
    try {
      await apiSend('/api/scrape/cancel', 'POST')
      state.value = { ...state.value, cancel_requested: true, current_task: 'Cancellation requested...' }
      connectSSE()
    } catch (e) {
      state.value = {
        ...state.value,
        current_task: String(e),
      }
    }
  }

  function minimize() {
    minimized.value = true
  }

  function expand() {
    minimized.value = false
    if (!isRunning.value) clearAutoDismiss()
  }

  function dismiss() {
    clearAutoDismiss()
    dismissed.value = true
    minimized.value = false
  }

  function init() {
    void checkStatus()
    poll = setInterval(() => checkStatus(), 5000)
    window.addEventListener('pagehide', () => {
      closeSSE()
      clearAutoDismiss()
      if (poll) clearInterval(poll)
    })
  }

  return {
    state,
    isRunning,
    showBar,
    barMode,
    minimized,
    checkStatus,
    wake,
    cancel,
    init,
    connectSSE,
    minimize,
    expand,
    dismiss,
  }
})
