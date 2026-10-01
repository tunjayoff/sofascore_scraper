import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import BridgeBanner from '@/components/BridgeBanner.vue'
import { useBridgeStore } from '@/stores/bridge'
import type { BridgeHealth } from '@/api/client'
import { i18n, setLocale } from '@/i18n'
import { callsTo, json, mockFetch } from './helpers'

const t = i18n.global.t

const ok: BridgeHealth = {
  state: 'ok',
  consecutive_failures: 0,
  last_success_at: '2026-10-01T12:00:00+00:00',
  last_failure_at: null,
  failing_since: null,
  changed_at: null,
  last_error: null,
}
const degraded: BridgeHealth = {
  ...ok,
  state: 'degraded',
  consecutive_failures: 3,
  last_failure_at: '2026-10-01T12:05:10+00:00',
  failing_since: '2026-10-01T12:05:00+00:00',
  changed_at: '2026-10-01T12:05:10+00:00',
  last_error: { kind: 'challenge', detail: 'HTTP 403: {"error":{"reason":"challenge"}}', at: '2026-10-01T12:05:10+00:00' },
}
const blocked: BridgeHealth = { ...degraded, state: 'blocked', consecutive_failures: 14 }
const status = (health?: BridgeHealth) => ({ status: 'ready', has_token: true, is_valid: true, health })

// init() adds window listeners per store; remove them so one test's store can't answer another's events
let added: [string, EventListenerOrEventListenerObject][] = []
const addEventListener = window.addEventListener.bind(window)

beforeEach(() => {
  setActivePinia(createPinia())
  setLocale('en')
  vi.spyOn(window, 'addEventListener').mockImplementation((type: string, fn: EventListenerOrEventListenerObject) => {
    added.push([type, fn])
    addEventListener(type, fn)
  })
})

afterEach(() => {
  for (const [type, fn] of added) window.removeEventListener(type, fn)
  added = []
  vi.useRealTimers()
  setLocale('tr')
})

describe('bridge store', () => {
  it('shows nothing before the first answer and while the bridge is ok', () => {
    const store = useBridgeStore()
    expect(store.visible).toBe(false)
    store.apply(ok)
    expect(store.visible).toBe(false)
    expect(store.episode).toBeNull()
  })

  it('shows the banner when the bridge is degraded and hides it once dismissed', () => {
    const store = useBridgeStore()
    store.apply(degraded)
    expect(store.visible).toBe(true)
    store.dismiss()
    expect(store.visible).toBe(false)
  })

  it('stays dismissed while the same streak is polled again', () => {
    const store = useBridgeStore()
    store.apply(degraded)
    store.dismiss()
    store.apply({ ...degraded, consecutive_failures: 7, last_failure_at: '2026-10-01T12:06:00+00:00' })
    expect(store.visible).toBe(false)
  })

  it('comes back when a dismissed degraded streak becomes blocked', () => {
    const store = useBridgeStore()
    store.apply(degraded)
    store.dismiss()
    store.apply(blocked)
    expect(store.visible).toBe(true)
  })

  it('comes back for a new streak after a recovery', () => {
    const store = useBridgeStore()
    store.apply(degraded)
    store.dismiss()
    store.apply(ok)
    expect(store.visible).toBe(false)
    store.apply(degraded) // even the very same payload: the recovery cleared the dismissal
    expect(store.visible).toBe(true)
    store.dismiss()
    store.apply({ ...degraded, failing_since: '2026-10-01T15:00:00+00:00' })
    expect(store.visible).toBe(true)
  })

  it('reads the health block of /api/bypass/status', async () => {
    mockFetch({ 'GET /api/bypass/status': status(blocked) })
    const store = useBridgeStore()
    await store.check()
    expect(store.health?.state).toBe('blocked')
    expect(store.visible).toBe(true)
  })

  it('keeps the last known state when the server cannot be reached or sends no health', async () => {
    let answer: () => unknown = () => status(degraded)
    mockFetch({ 'GET /api/bypass/status': () => answer() })
    const store = useBridgeStore()
    await store.check()
    answer = () => json({ detail: 'boom' }, 500)
    await store.check()
    expect(store.health?.state).toBe('degraded')
    answer = () => status(undefined) // an older backend without the health block
    await store.check()
    expect(store.health?.state).toBe('degraded')
    answer = () => status({ ...ok, state: 'weird' as BridgeHealth['state'] })
    await store.check()
    expect(store.health?.state).toBe('degraded')
  })

  it('polls every 20 s after init, once, and stops on pagehide', async () => {
    vi.useFakeTimers()
    const fetchMock = mockFetch({ 'GET /api/bypass/status': status(ok) })
    const store = useBridgeStore()
    store.init()
    store.init() // a second init must not double the polling
    await vi.advanceTimersByTimeAsync(0)
    expect(callsTo(fetchMock, 'GET /api/bypass/status')).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(40000)
    expect(callsTo(fetchMock, 'GET /api/bypass/status')).toHaveLength(3)
    window.dispatchEvent(new Event('pagehide'))
    await vi.advanceTimersByTimeAsync(60000)
    expect(callsTo(fetchMock, 'GET /api/bypass/status')).toHaveLength(3)
  })
})

describe('BridgeBanner', () => {
  const mountBanner = () => mount(BridgeBanner, { global: { plugins: [i18n] } })

  it('renders nothing while the bridge is ok', () => {
    useBridgeStore().apply(ok)
    expect(mountBanner().find('[data-testid="bridge-banner"]').exists()).toBe(false)
  })

  it('announces a degraded bridge politely with the count, the reason and the last success', () => {
    useBridgeStore().apply(degraded)
    const banner = mountBanner().get('[data-testid="bridge-banner"]')
    expect(banner.attributes('role')).toBe('status')
    expect(banner.text()).toContain(t('bridge.degradedTitle'))
    expect(banner.text()).toContain(t('bridge.degradedBody', { count: 3 }))
    expect(banner.text()).toContain(t('bridge.reason.challenge'))
    expect(banner.text()).toContain('2026')
  })

  it('raises a blocked bridge as an alert', () => {
    useBridgeStore().apply({ ...blocked, last_success_at: null, last_error: { ...blocked.last_error!, kind: 'forbidden' } })
    const banner = mountBanner().get('[data-testid="bridge-banner"]')
    expect(banner.attributes('role')).toBe('alert')
    expect(banner.text()).toContain(t('bridge.blockedTitle'))
    expect(banner.text()).toContain(t('bridge.blockedBody', { count: 14 }))
    expect(banner.text()).toContain(t('bridge.reason.forbidden'))
    expect(banner.text()).toContain(t('bridge.never'))
  })

  it('does not blame SofaScore when the browser itself cannot start', () => {
    useBridgeStore().apply({ ...blocked, last_error: { kind: 'browser', detail: 'RuntimeError', at: blocked.changed_at! } })
    const text = mountBanner().get('[data-testid="bridge-banner"]').text()
    expect(text).toContain(t('bridge.browserTitle'))
    expect(text).not.toContain(t('bridge.blockedTitle'))
    expect(text).toContain(t('bridge.reason.browser'))
  })

  it('is dismissed with its button and speaks Turkish too', async () => {
    setLocale('tr')
    useBridgeStore().apply(blocked)
    const w = mountBanner()
    expect(w.text()).toContain('SofaScore bizi engelliyor')
    await w.get(`button[aria-label="${t('bridge.dismiss')}"]`).trigger('click')
    expect(w.find('[data-testid="bridge-banner"]').exists()).toBe(false)
    expect(useBridgeStore().visible).toBe(false)
  })
})
