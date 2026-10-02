import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { NETWORK, V1Error, v1 } from '@/api/v1/client'
import type { Status } from '@/api/v1/schema'
import type { HealthLevel } from '@/ui/status'
import { tokenInUse } from '@/app/session'
import { poll } from '@/app/poll'

export const STATUS_EVERY_MS = 15000

/**
 * `/api/v1/status`, read every 15 s while the tab is visible (5.1): the health pill, the job pill, the
 * rail's badges, Overview and Health read it from here. When the server cannot be reached the last answer
 * is kept (greyed by the shell) and the request is retried with back-off (5.2, network failure).
 */
export const useStatusStore = defineStore('v1-status', () => {
  const status = ref<Status | null>(null)
  const error = ref<unknown>(null)
  const loading = ref(false)
  /** Epoch ms of the last good answer. */
  const fetchedAt = ref<number | null>(null)
  let failures = 0
  let stop: (() => void) | null = null

  const offline = computed(() => error.value instanceof V1Error && error.value.code === NETWORK)
  const activeJob = computed(() => status.value?.active_job ?? null)

  /** The health pill (3.3): green, amber (connection degraded, shared budget unreadable), red (blocked). */
  const level = computed<HealthLevel>(() => {
    const s = status.value
    if (!s) return 'unknown'
    if (s.bridge.state === 'blocked') return 'blocked'
    if (s.bridge.state === 'degraded' || s.throttle.error) return 'attention'
    return 'ok'
  })

  async function refresh() {
    loading.value = true
    try {
      const s = await v1.status()
      status.value = s
      tokenInUse.value = s.auth_required
      error.value = null
      fetchedAt.value = Date.now()
      failures = 0
    } catch (e) {
      error.value = e
      failures++
      throw e
    } finally {
      loading.value = false
    }
  }

  /** Back-off after failures: 2, 4, 8 s ... up to the normal 15 s. */
  function nextDelay() {
    return failures ? Math.min(STATUS_EVERY_MS, 1000 * 2 ** failures) : STATUS_EVERY_MS
  }

  function start() {
    if (!stop) stop = poll(refresh, nextDelay)
  }

  function stopPolling() {
    stop?.()
    stop = null
  }

  return { status, error, loading, fetchedAt, offline, activeJob, level, refresh, start, stop: stopPolling }
})
