import type { BridgeHealth, JobState } from '@/api/v1/schema'
import type { UiIconName } from '@/ui/UiIcon.vue'

/**
 * One meaning, one look (05-web-ui.md 4.6): every status has one tone, one icon and one word, the same on
 * every screen. The words are locale keys (`ui.status.<thing>.<value>`), never the server's text. The
 * vocabularies of events, slices, sinks and follows join with the screens that show them (P21).
 */
export type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'neutral'

export type StatusLook = { tone: Tone; icon: UiIconName; key: string; spin?: boolean }

export const JOB_STATES: Record<JobState, StatusLook> = {
  queued: { tone: 'neutral', icon: 'clock', key: 'ui.status.job.queued' },
  running: { tone: 'info', icon: 'jobs', key: 'ui.status.job.running', spin: true },
  succeeded: { tone: 'ok', icon: 'okCircle', key: 'ui.status.job.succeeded' },
  partial: { tone: 'warn', icon: 'halfCircle', key: 'ui.status.job.partial' },
  failed: { tone: 'danger', icon: 'error', key: 'ui.status.job.failed' },
  cancelled: { tone: 'neutral', icon: 'stop', key: 'ui.status.job.cancelled' },
  interrupted: { tone: 'warn', icon: 'alert', key: 'ui.status.job.interrupted' },
}

export type ConnectionState = BridgeHealth['state']

export const CONNECTION: Record<ConnectionState, StatusLook> = {
  ok: { tone: 'ok', icon: 'okCircle', key: 'ui.status.connection.ok' },
  degraded: { tone: 'warn', icon: 'alert', key: 'ui.status.connection.degraded' },
  blocked: { tone: 'danger', icon: 'error', key: 'ui.status.connection.blocked' },
}

/** The live service (`ssc watch`); `/status` has no live fields before P21, so the UI shows "unknown". */
export type LiveState = 'running' | 'paused' | 'stopped' | 'unknown'

export const LIVE: Record<LiveState, StatusLook> = {
  running: { tone: 'ok', icon: 'okCircle', key: 'ui.status.live.running' },
  paused: { tone: 'warn', icon: 'pause', key: 'ui.status.live.paused' },
  stopped: { tone: 'neutral', icon: 'circle', key: 'ui.status.live.stopped' },
  unknown: { tone: 'neutral', icon: 'planned', key: 'ui.status.live.unknown' },
}

/** The health pill: one word and a tone for the whole server (3.3). */
export type HealthLevel = 'ok' | 'attention' | 'blocked' | 'unknown'

export const HEALTH: Record<HealthLevel, StatusLook> = {
  ok: { tone: 'ok', icon: 'okCircle', key: 'ui.status.health.ok' },
  attention: { tone: 'warn', icon: 'alert', key: 'ui.status.health.attention' },
  blocked: { tone: 'danger', icon: 'error', key: 'ui.status.health.blocked' },
  unknown: { tone: 'neutral', icon: 'circle', key: 'ui.status.health.unknown' },
}

export const STATUS_KINDS = { job: JOB_STATES, connection: CONNECTION, live: LIVE, health: HEALTH } as const
export type StatusKind = keyof typeof STATUS_KINDS

export function lookOf(kind: StatusKind, value: string): StatusLook {
  const table = STATUS_KINDS[kind] as Record<string, StatusLook>
  return table[value] ?? { tone: 'neutral', icon: 'circle', key: 'ui.status.unknownValue' }
}
