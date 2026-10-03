import { vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type RouteRecordRaw } from 'vue-router'
import type { Component } from 'vue'
import axe from 'axe-core'
import { i18n } from '@/i18n'
import type { Change, DataSummary, EventListItem, FollowRecord, Job, Setting, SettingsDocument, Slice, Status } from '@/api/v1/schema'

/**
 * Fakes of the `/api/v1` answers for the tests of the new UI. No test reaches a server or SofaScore:
 * fetch and EventSource are stubbed, and an unexpected request fails the test (helpers.ts: mockFetch).
 */

export const summary = (over: Partial<DataSummary> = {}): DataSummary => ({
  only_finished: true,
  matches: 48210,
  details: 46903,
  seasons: 12,
  legacy_events: 0,
  catalog_rebuild_reason: null,
  tournaments: [],
  disk: { entries: {}, seasons: 1, matches: 2, details: 3, datasets: 0, total: 2254857830, measured_at_utc: '2026-10-02T10:00:00Z' },
  ...over,
})

export const status = (over: Partial<Status> = {}): Status => ({
  version: '3.0.0',
  api_version: 'v1',
  schema_version: 1,
  auth_required: false,
  live: { running: false, pid: null, host: null, source: null, sports: [], heartbeat_at: null, blocked: false, leaders: {}, last_switch: null },
  summary: summary(),
  leases: [],
  capabilities: { parquet: false, sse: true, scheduler: false },
  storage_error: null,
  bridge: {
    state: 'ok',
    consecutive_failures: 0,
    last_success_at: '2026-10-02T10:00:00Z',
    last_failure_at: null,
    failing_since: null,
    changed_at: null,
    last_error: null,
    thresholds: { degraded_after: 3, blocked_after: 10 },
  },
  throttle: { enabled: true, requests_per_second: 5, shared: true, error: null },
  active_job: null,
  ...over,
})

export const job = (over: Partial<Job> = {}): Job => ({
  id: '01J9ZQ3M5XK8A0B1C2D3E4F5G6',
  kind: 'sync',
  state: 'succeeded',
  origin: { face: 'api', pid: 4121, host: 'srv-1' },
  spec: { mode: 'full', league_id: null, selections: [], export: false },
  progress: null,
  result: { details_done: 380, details_total: 380, failed_count: 0, failed: [] },
  error: null,
  created_at: '2026-10-02T09:00:00Z',
  started_at: '2026-10-02T09:00:00Z',
  finished_at: '2026-10-02T09:04:10Z',
  heartbeat_at: null,
  cancel_requested: false,
  ...over,
})

export const event = (over: Partial<EventListItem> = {}): EventListItem => ({
  id: 9100003,
  sport: 'football',
  category_id: 1,
  tournament_id: 17,
  season_id: 61627,
  stage: { id: null, name: 'Premier League' },
  round: { number: 2, name: null, slug: null },
  start_utc: '2024-08-31T11:46:40Z',
  status: { type: 'finished', code: 100, description: 'Ended', class: 'completed' },
  participants: { home: { id: 38, name: 'Chelsea' }, away: { id: 44, name: 'Liverpool' } },
  score: { family: 'football', home: 1, away: 3, half_time: { home: 0, away: 0 }, regulation: { home: 1, away: 3 }, after_extra_time: null, penalties: null },
  winner: 'away',
  aggregate: null,
  slug: 'chelsea-liverpool',
  custom_id: 'abc',
  quality: { source: 'event', observed_at_utc: '2026-10-03T02:01:10Z', change_ts: 1725112000, settlement: 'final', provisional: false, tier_hint: null, stale: false, status_regressed: false },
  slices_summary: { selected: 6, ok: 6, empty: 0, error: 0 },
  ...over,
})

export const slice = (key: string, over: Partial<Slice> = {}): Slice => ({
  owner_kind: 'event',
  owner_id: 9100003,
  key,
  sub: null,
  state: 'ok',
  has_payload: true,
  fetched_at_utc: '2026-10-03T02:01:10Z',
  checked_at_utc: '2026-10-03T02:01:10Z',
  error: null,
  payload: null,
  ...over,
})

export const follow = (over: Partial<FollowRecord> = {}): FollowRecord => ({
  id: 'tournament:17',
  kind: 'tournament',
  entity_id: 17,
  name: 'Premier League',
  sport: 'football',
  seasons: 'current',
  slices: null,
  live: false,
  enabled: true,
  origin: 'api',
  position: 0,
  writable: ['name', 'sport', 'seasons', 'live', 'enabled'],
  created_at_utc: '2026-10-01T10:00:00Z',
  updated_at_utc: '2026-10-01T10:00:00Z',
  ...over,
})

export const change = (over: Partial<Change> = {}): Change => ({
  seq: 1,
  recorded_at_utc: '2026-10-02T10:00:00Z',
  event_id: 9100003,
  sport: 'football',
  tournament_id: 17,
  start_utc: '2024-08-31T11:46:40Z',
  seconds_after_start: 7200,
  old_status_class: 'completed',
  new_status_class: 'completed',
  old_change_ts: 1,
  new_change_ts: 2,
  status_regressed: false,
  tier_hint: null,
  fields: [
    { path: 'awayScore.current', old: 1, new: 2 },
    { path: 'homeScore.current', old: 2, new: 2 },
  ],
  ...over,
})

export const sport = (slug = 'football') => ({
  slug,
  name: slug[0].toUpperCase() + slug.slice(1),
  i18n_key: `sport.${slug}`,
  score_family: 'football',
  slices: ['statistics', 'team_streaks', 'pregame_form', 'h2h', 'lineups', 'incidents'].map((key) => ({ key, path: `/event/{event_id}/${key}`, required: true, default_enabled: true })),
})

export const page = <T>(data: T[], next: string | null = null) => ({ data, page: { limit: 25, next_cursor: next } })

export const setting = (key: string, value: unknown, over: Partial<Setting> = {}): Setting => ({
  key,
  value,
  source: 'default',
  source_name: '',
  locked: false,
  writable: true,
  secret: false,
  ...over,
})

export const settingsDoc = (settings: Setting[], over: Partial<SettingsDocument> = {}): SettingsDocument => ({
  config_file: '/srv/sofascore/sofascore.toml',
  overrides_file: '/srv/sofascore/config/overrides.json',
  settings,
  ...over,
})

/** A v1 error answer. */
export function v1Error(status: number, code: string, details: Record<string, unknown> | null = null, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify({ error: { code, message: `English text for ${code}`, details, request_id: 'req-123abc' } }), {
    status,
    headers: { 'Content-Type': 'application/json', 'X-Request-Id': 'req-123abc', ...headers },
  })
}

/** EventSource stand-in with readyState, so the client can tell a reconnect from a final failure. */
export class FakeES {
  static instances: FakeES[] = []
  url: string
  readyState = 0
  closed = false
  onerror: ((e: Event) => void) | null = null
  private listeners: Record<string, ((e: Event) => void)[]> = {}

  constructor(url: string) {
    this.url = url
    FakeES.instances.push(this)
  }
  addEventListener(type: string, fn: (e: Event) => void) {
    ;(this.listeners[type] ||= []).push(fn)
  }
  close() {
    this.closed = true
    this.readyState = 2
  }
  /** One job event, as the server frames it (`id: <seq>`, `event: <type>`, `data: <JobEvent>`). */
  emit(type: string, seq: number, data: Record<string, unknown> = {}, jobId = '01J9ZQ3M5XK8A0B1C2D3E4F5G6') {
    const e = new MessageEvent(type, { data: JSON.stringify({ job_id: jobId, seq, ts_ms: Date.UTC(2026, 9, 2, 9, 0, seq), type, data }), lastEventId: String(seq) })
    for (const fn of this.listeners[type] || []) fn(e)
  }
  gap(oldest: number) {
    const e = new MessageEvent('stream.gap', { data: JSON.stringify({ job_id: 'x', after: 1, oldest_seq: oldest }) })
    for (const fn of this.listeners['stream.gap'] || []) fn(e)
  }
  /** The connection broke; `final` = the browser gave up (501, 404). */
  fail(final = false) {
    this.readyState = final ? 2 : 0
    this.onerror?.(new Event('error'))
  }
  static reset() {
    FakeES.instances = []
  }
  static get last() {
    return FakeES.instances[FakeES.instances.length - 1]
  }
}

export function useFakeES() {
  FakeES.reset()
  vi.stubGlobal('EventSource', FakeES)
}

/** A router with the given routes in memory, at `path`. */
export async function routerAt(path: string, routes: RouteRecordRaw[]) {
  const r = createRouter({ history: createMemoryHistory(), routes: [...routes, { path: '/:rest(.*)*', component: { template: '<div />' } }] })
  await r.push(path)
  await r.isReady()
  return r
}

/** Mounts a screen at `path` with i18n, Pinia and a router where `pattern` is that screen. */
export async function mountScreen(component: Component, path: string, pattern = path) {
  setActivePinia(createPinia())
  const router = await routerAt(path, [{ path: pattern, component }])
  const w = mount({ template: '<div class="u-app"><RouterView /></div>' }, { global: { plugins: [i18n, router] }, attachTo: document.body })
  return { w, router }
}

/** axe on a mounted part; colour contrast is checked by tests/tokens.test.ts (jsdom has no layout). */
export async function axeViolations(el: Element) {
  const result = await axe.run(el, { rules: { 'color-contrast': { enabled: false }, region: { enabled: false } } })
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)
}
