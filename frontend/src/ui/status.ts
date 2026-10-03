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

/** The live service (`ssc watch`) from `/status.live`; "unknown" when the server could not read it. */
export type LiveState = 'running' | 'paused' | 'stopped' | 'unknown'

export function liveState(live: { running: boolean; blocked?: boolean } | null | undefined): LiveState {
  if (!live) return 'unknown'
  if (!live.running) return 'stopped'
  return live.blocked ? 'paused' : 'running'
}

export const LIVE: Record<LiveState, StatusLook> = {
  running: { tone: 'ok', icon: 'okCircle', key: 'ui.status.live.running' },
  paused: { tone: 'warn', icon: 'pause', key: 'ui.status.live.paused' },
  stopped: { tone: 'neutral', icon: 'circle', key: 'ui.status.live.stopped' },
  unknown: { tone: 'neutral', icon: 'planned', key: 'ui.status.live.unknown' },
}

/**
 * A sink (6.13, 4.6): `delivering` (ok), `retrying` (the last delivery failed), `behind` (the oldest
 * undelivered event is older than a minute), `pending` (nothing delivered yet), `unserved` (no process holds
 * the `sinks` lease, so nothing is delivered right now). The API has no `disabled` state yet.
 */
export type SinkState = 'delivering' | 'retrying' | 'behind' | 'pending' | 'unserved'

export const SINK: Record<SinkState, StatusLook> = {
  delivering: { tone: 'ok', icon: 'okCircle', key: 'ui.status.sink.delivering' },
  retrying: { tone: 'warn', icon: 'refresh', key: 'ui.status.sink.retrying' },
  behind: { tone: 'warn', icon: 'clock', key: 'ui.status.sink.behind' },
  pending: { tone: 'neutral', icon: 'circle', key: 'ui.status.sink.pending' },
  unserved: { tone: 'neutral', icon: 'pause', key: 'ui.status.sink.unserved' },
}

/** "Behind" starts when the oldest undelivered event is older than this (6.13). */
export const SINK_BEHIND_S = 60

export function sinkState(s: { state: string; served: boolean; lag_seconds?: number | null }): SinkState {
  if (s.state === 'error') return 'retrying'
  if (!s.served) return 'unserved'
  if ((s.lag_seconds ?? 0) > SINK_BEHIND_S) return 'behind'
  if (s.state === 'pending') return 'pending'
  return 'delivering'
}

/** The platform's class of an event status (`Event.status.class`, 4.6). */
export const EVENT_CLASS: Record<string, StatusLook> = {
  not_started: { tone: 'neutral', icon: 'clock', key: 'ui.status.event.not_started' },
  live: { tone: 'info', icon: 'jobs', key: 'ui.status.event.live' },
  completed: { tone: 'ok', icon: 'okCircle', key: 'ui.status.event.completed' },
  decided_without_play: { tone: 'ok', icon: 'okCircle', key: 'ui.status.event.decided_without_play' },
  void: { tone: 'warn', icon: 'alert', key: 'ui.status.event.void' },
  unknown: { tone: 'neutral', icon: 'circle', key: 'ui.status.event.unknown' },
}

/** The state of a stored slice (`Slice.state`, 4.6). */
export const SLICE_STATE: Record<string, StatusLook> = {
  ok: { tone: 'ok', icon: 'okCircle', key: 'ui.status.slice.ok' },
  empty: { tone: 'neutral', icon: 'circle', key: 'ui.status.slice.empty' },
  error: { tone: 'danger', icon: 'error', key: 'ui.status.slice.error' },
  not_requested: { tone: 'neutral', icon: 'circle', key: 'ui.status.slice.not_requested' },
}

/** Where a follow comes from (4.6): the config file is locked, leagues.txt is legacy, the API's has no badge. */
export const FOLLOW_ORIGIN: Record<string, StatusLook> = {
  config: { tone: 'neutral', icon: 'lock', key: 'ui.status.origin.config' },
  legacy: { tone: 'neutral', icon: 'classic', key: 'ui.status.origin.legacy' },
  api: { tone: 'neutral', icon: 'plus', key: 'ui.status.origin.api' },
}

/** The health pill: one word and a tone for the whole server (3.3). */
export type HealthLevel = 'ok' | 'attention' | 'blocked' | 'unknown'

export const HEALTH: Record<HealthLevel, StatusLook> = {
  ok: { tone: 'ok', icon: 'okCircle', key: 'ui.status.health.ok' },
  attention: { tone: 'warn', icon: 'alert', key: 'ui.status.health.attention' },
  blocked: { tone: 'danger', icon: 'error', key: 'ui.status.health.blocked' },
  unknown: { tone: 'neutral', icon: 'circle', key: 'ui.status.health.unknown' },
}

export const STATUS_KINDS = { job: JOB_STATES, connection: CONNECTION, live: LIVE, health: HEALTH, sink: SINK, event: EVENT_CLASS, slice: SLICE_STATE, origin: FOLLOW_ORIGIN } as const
export type StatusKind = keyof typeof STATUS_KINDS

export function lookOf(kind: StatusKind, value: string): StatusLook {
  const table = STATUS_KINDS[kind] as Record<string, StatusLook>
  return table[value] ?? { tone: 'neutral', icon: 'circle', key: 'ui.status.unknownValue' }
}
