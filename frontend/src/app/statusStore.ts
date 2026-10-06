import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { NETWORK, V1Error, v1 } from '@/api/v1/client'
import type { Status, StatusCheck } from '@/api/v1/schema'
import { connectionState, type ConnectionState, type HealthLevel } from '@/ui/status'
import { tokenInUse } from '@/app/session'
import { poll } from '@/app/poll'

export const STATUS_EVERY_MS = 15000

/**
 * Size of the data folder: the sum of its top-level entries. `disk.total` counts only the 2.x trees
 * (seasons, matches, details, datasets) and leaves out `v3/` and `.meta/`, so it reads 0 for a new layout.
 */
export function diskBytes(summary: Status['summary'] | null | undefined): number | null {
  const disk = summary?.disk
  if (!disk) return null
  const entries = Object.values(disk.entries ?? {}).filter((n) => typeof n === 'number')
  return entries.length ? entries.reduce((a, b) => a + b, 0) : disk.total
}

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
  /** The connection from `/status` (`connection`, kept by the server for every browser since FX-19). */
  const connection = computed<ConnectionState | null>(() => connectionState(status.value))

  /** A connection check's answer carries the server's new connection and bridge: shown at once. */
  function noteCheck(check: Pick<StatusCheck, 'bridge' | 'connection'>) {
    if (status.value) status.value = { ...status.value, bridge: check.bridge ?? status.value.bridge, connection: check.connection ?? status.value.connection }
  }

  /**
   * The health pill (3.3): green; amber when the connection is degraded, the last request or the last
   * connection check failed, the shared budget is unreadable, the live service is paused by a block or the
   * data folder cannot be read; red when SofaScore blocks us; grey "not tried" while no request to
   * SofaScore has been answered (FX-14a). Sinks are not in `/status`, so a lagging sink shows on Overview
   * and Sinks, not in the pill.
   */
  const level = computed<HealthLevel>(() => {
    const s = status.value
    if (!s) return 'unknown'
    if (s.bridge.state === 'blocked') return 'blocked'
    const c = connection.value
    if (c === 'degraded' || c === 'failing' || c === 'checkFailed' || s.throttle.error || s.live?.blocked || s.storage_error) return 'attention'
    if (c === 'untried') return 'untried'
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

  return { status, error, loading, fetchedAt, offline, activeJob, connection, noteCheck, level, refresh, start, stop: stopPolling }
})
