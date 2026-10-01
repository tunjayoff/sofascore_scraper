import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api, type BridgeHealth } from '@/api/client'

const POLL_MS = 20000

/**
 * Health of the browser bridge every SofaScore request goes through (src/bridge_health.py):
 * ok / degraded / blocked. The banner shows while it is not ok and can be dismissed per
 * episode: it comes back if things get worse (degraded → blocked) or if a new failure streak
 * starts after a recovery, but not on every poll of the same streak.
 */
export const useBridgeStore = defineStore('bridge', () => {
  const health = ref<BridgeHealth | null>(null)
  const dismissed = ref<string | null>(null)
  let poll: ReturnType<typeof setInterval> | null = null
  let listening = false

  /** One failure streak at one severity; null while healthy or before the first answer. */
  const episode = computed(() => {
    const h = health.value
    return h && h.state !== 'ok' ? `${h.state}@${h.failing_since ?? ''}` : null
  })
  const visible = computed(() => episode.value !== null && episode.value !== dismissed.value)

  function apply(h: BridgeHealth | null | undefined) {
    if (!h || !['ok', 'degraded', 'blocked'].includes(h.state)) return // older backend: no health block
    health.value = h
    if (h.state === 'ok') dismissed.value = null
  }

  async function check() {
    try {
      apply((await api.bypassStatus()).health)
    } catch {
      /* server unreachable: the brand line already says so; keep the last known state */
    }
  }

  function dismiss() {
    dismissed.value = episode.value
  }

  function start() {
    if (poll) return
    void check()
    poll = setInterval(check, POLL_MS)
  }

  function stop() {
    if (poll) clearInterval(poll)
    poll = null
  }

  function init() {
    start()
    if (listening) return
    listening = true
    window.addEventListener('pagehide', stop)
    // Back/forward cache restores the page without re-running init: resume updates
    window.addEventListener('pageshow', (e) => {
      if ((e as PageTransitionEvent).persisted) start()
    })
  }

  return { health, visible, episode, apply, check, dismiss, init, stop }
})
