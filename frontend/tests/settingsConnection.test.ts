import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SettingsView from '@/views/SettingsView.vue'
import { useBridgeStore } from '@/stores/bridge'
import type { BridgeHealth, BypassTest } from '@/api/client'
import { toasts } from '@/lib/toast'
import { i18n, setLocale } from '@/i18n'
import { callsTo, deferred, flush, json, mockFetch } from './helpers'

const t = i18n.global.t
const TEST = 'POST /api/bypass/test'
const SAVE = 'POST /api/settings'
const STATS = { leagues: 1, seasons: 1, matches: 0, details: 0, league_breakdown: [], disk_usage: { formatted_total: '1 MB', total: 1 } }
const SETTINGS = {
  language: 'en', data_dir: 'data', max_concurrent: 5, request_rate_limit: 0, wait_time_min: 1, wait_time_max: 2,
  request_timeout: 20, max_retries: 3, fetch_only_finished: true, save_empty_rounds: false, refresh_window_hours: 72,
  log_level: 'INFO', use_proxy: false, proxy_url: '',
}
const MASKED = 'http://scraper:***@proxy.example:8080'

const ok: BridgeHealth = {
  state: 'ok', consecutive_failures: 0, last_success_at: '2026-10-01T12:00:00+00:00', last_failure_at: null,
  failing_since: null, changed_at: null, last_error: null,
}
const degraded: BridgeHealth = {
  ...ok, state: 'degraded', consecutive_failures: 4, last_failure_at: '2026-10-01T12:05:10+00:00',
  failing_since: '2026-10-01T12:05:00+00:00', changed_at: '2026-10-01T12:05:10+00:00',
  last_error: { kind: 'challenge', detail: 'HTTP 403', at: '2026-10-01T12:05:10+00:00' },
}
const passed: BypassTest = {
  success: true, reason: null, events_count: 12, message: 'BrowserBridge connection verified successfully.',
  browser_ready: true, has_token: true, is_valid: true, health: ok,
}
const failed = (reason: BypassTest['reason'], extra: Partial<BypassTest> = {}): BypassTest => ({
  success: false, reason, message: `server says ${reason}`, browser_ready: true, has_token: false, is_valid: false,
  health: degraded, ...extra,
})

type Routes = Parameters<typeof mockFetch>[0]
let w: VueWrapper

async function openConnection(routes: Routes = {}, settings: object = SETTINGS) {
  const fetchMock = mockFetch({ 'GET /api/settings': settings, 'GET /api/stats/system': STATS, ...routes })
  w = mount(SettingsView, { global: { plugins: [i18n] } })
  await flush()
  await w.get('#settings-tab-connection').trigger('click')
  return fetchMock
}

const result = () => w.get('[data-testid="connection-result"]')
const runButton = () => w.get('[data-testid="connection-test"]')
const saveButton = () => w.get('#settings-panel-connection .btn-primary')
const sentSettings = (fetchMock: ReturnType<typeof mockFetch>) =>
  callsTo(fetchMock, SAVE).map(([, init]) => JSON.parse(String(init?.body)))

beforeEach(() => {
  setActivePinia(createPinia())
  setLocale('en')
  toasts.value = []
})

afterEach(() => {
  w?.unmount()
  vi.useRealTimers()
  setLocale('tr')
})

describe('Settings connection check', () => {
  it('never runs the test by itself: not on load, not on opening the tab, not as time passes', async () => {
    vi.useFakeTimers()
    const fetchMock = mockFetch({ 'GET /api/settings': SETTINGS, 'GET /api/stats/system': STATS, [TEST]: passed })
    w = mount(SettingsView, { global: { plugins: [i18n] } })
    await vi.advanceTimersByTimeAsync(0)
    await w.get('#settings-tab-connection').trigger('click')
    await vi.advanceTimersByTimeAsync(30 * 60 * 1000)
    expect(callsTo(fetchMock, TEST)).toHaveLength(0)
    expect(w.find('[data-testid="connection-result"]').exists()).toBe(false)
    expect(runButton().text()).toBe(t('settings.connection.run'))
  })

  it('is the only place in the app that calls the test endpoint', () => {
    const sources = import.meta.glob('/src/**/*.{ts,vue}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>
    expect(Object.keys(sources).length).toBeGreaterThan(10)
    const callers = Object.entries(sources)
      .filter(([path, code]) => !path.endsWith('/api/client.ts') && /bypassTest|bypass\/test/.test(code))
      .map(([path]) => path)
    expect(callers).toEqual(['/src/views/SettingsView.vue'])
    // … and there it is called once, from the click handler
    expect(sources['/src/views/SettingsView.vue'].match(/bypassTest\(/g)).toHaveLength(1)
  })

  it('sends one request per click and shows a passed test with the bridge state', async () => {
    const fetchMock = await openConnection({ [TEST]: passed })
    await runButton().trigger('click')
    await flush()
    expect(callsTo(fetchMock, TEST)).toHaveLength(1)
    expect(result().attributes('role')).toBe('status')
    expect(result().text()).toContain(t('settings.connection.success', { n: 12 }))
    expect(result().text()).toContain(t('settings.connection.browserValue.ready'))
    expect(result().text()).toContain(t('settings.connection.challengeValue.passed'))
    const state = w.get('[data-testid="bridge-state"]').text()
    expect(state).toContain(t('settings.connection.stateValue.ok'))
    expect(state).toContain('2026')
    expect(useBridgeStore().health?.state).toBe('ok')
  })

  it('does not call a missing token a failure when the test passed without a challenge', async () => {
    await openConnection({ [TEST]: { ...passed, has_token: false, is_valid: false } })
    await runButton().trigger('click')
    await flush()
    expect(result().text()).toContain(t('settings.connection.challengeValue.passed'))
    expect(result().text()).not.toContain(t('settings.connection.challengeValue.failed'))
  })

  it('explains a blocked test and shows the degraded bridge state', async () => {
    await openConnection({ [TEST]: failed('blocked') })
    expect(w.get('[data-testid="bridge-state"]').text()).toContain(t('settings.connection.stateValue.unknown'))
    await runButton().trigger('click')
    await flush()
    expect(result().attributes('role')).toBe('alert')
    expect(result().text()).toContain(t('settings.connection.failed'))
    expect(result().text()).toContain(t('upstream.blocked'))
    expect(result().text()).not.toContain(t('upstream.testHint')) // no "go to the connection check" on the check itself
    expect(result().text()).not.toContain('server says blocked')
    expect(result().text()).toContain(t('settings.connection.challengeValue.failed'))
    const state = w.get('[data-testid="bridge-state"]').text()
    expect(state).toContain(t('settings.connection.stateValue.degraded'))
    expect(state).toContain('4')
    expect(w.text()).toContain(t('bridge.reason.challenge'))
    expect(useBridgeStore().health?.state).toBe('degraded') // the banner follows the test
  })

  it('names the browser when it could not start', async () => {
    const health: BridgeHealth = { ...degraded, last_error: { kind: 'browser', detail: 'RuntimeError', at: degraded.changed_at! } }
    await openConnection({ [TEST]: failed('browser', { browser_ready: false, health }) })
    await runButton().trigger('click')
    await flush()
    expect(result().text()).toContain(t('upstream.browser'))
    expect(result().text()).toContain(t('settings.connection.browserValue.down'))
    expect(w.text()).toContain(t('bridge.reason.browser'))
  })

  it('explains a network failure', async () => {
    await openConnection({ [TEST]: failed('network', { health: ok }) })
    await runButton().trigger('click')
    await flush()
    expect(result().text()).toContain(t('upstream.network'))
    expect(result().text()).not.toContain(t('settings.connection.challenge'))
  })

  it('shows the bridge state the app already knows before any test', async () => {
    useBridgeStore().apply({ ...degraded, state: 'blocked', consecutive_failures: 14 })
    await openConnection()
    const state = w.get('[data-testid="bridge-state"]').text()
    expect(state).toContain(t('settings.connection.stateValue.blocked'))
    expect(state).toContain('14')
  })

  it('reports a test that could not be run and keeps the button usable', async () => {
    await openConnection({ [TEST]: () => json({ detail: 'Internal error' }, 500) })
    await runButton().trigger('click')
    await flush()
    expect(w.find('[data-testid="connection-result"]').exists()).toBe(false)
    expect(w.get('#settings-panel-connection [role="alert"]').text()).toContain('Internal error')
    expect(runButton().attributes('disabled')).toBeUndefined()
  })

  it('ignores further clicks while a test is running', async () => {
    const pending = deferred<BypassTest>()
    const fetchMock = await openConnection({ [TEST]: () => pending.promise })
    await runButton().trigger('click')
    expect(runButton().attributes('disabled')).toBeDefined()
    expect(runButton().text()).toContain(t('settings.connection.running'))
    await runButton().trigger('click')
    expect(callsTo(fetchMock, TEST)).toHaveLength(1)
    pending.resolve(passed)
    await flush()
    expect(runButton().attributes('disabled')).toBeUndefined()
    expect(result().text()).toContain(t('settings.connection.success', { n: 12 }))
  })

  it('speaks Turkish too', async () => {
    setLocale('tr')
    await openConnection({ [TEST]: failed('blocked') })
    expect(w.get('#settings-tab-connection').text()).toBe('Bağlantı')
    await runButton().trigger('click')
    await flush()
    expect(result().text()).toContain('Test başarısız oldu.')
    expect(result().text()).toContain('SofaScore isteği reddetti')
  })
})

describe('Settings proxy fields', () => {
  it('fills the form with the masked address and saves only what changed', async () => {
    const fetchMock = await openConnection({ [SAVE]: { status: 'success' } }, { ...SETTINGS, use_proxy: true, proxy_url: MASKED })
    expect((w.get('#s-proxy').element as HTMLInputElement).value).toBe(MASKED)
    expect((w.get('#s-use-proxy').element as HTMLInputElement).checked).toBe(true)
    expect(saveButton().attributes('disabled')).toBeDefined() // nothing changed yet

    await w.get('#s-use-proxy').setValue(false)
    await saveButton().trigger('click')
    await flush()
    expect(sentSettings(fetchMock)).toEqual([{ use_proxy: false }]) // the masked address is not sent back
    expect(toasts.value.map((x) => x.text)).toEqual([t('settings.saved')])
  })

  it('saves a new proxy, trimmed', async () => {
    const fetchMock = await openConnection({ [SAVE]: { status: 'success' } })
    await w.get('#s-use-proxy').setValue(true)
    await w.get('#s-proxy').setValue('  socks5://user:pw@10.0.0.1:1080 ')
    await saveButton().trigger('click')
    await flush()
    expect(sentSettings(fetchMock)).toEqual([{ use_proxy: true, proxy_url: 'socks5://user:pw@10.0.0.1:1080' }])
  })

  it('asks for an address before turning the proxy on', async () => {
    const fetchMock = await openConnection({ [SAVE]: { status: 'success' } })
    await w.get('#s-use-proxy').setValue(true)
    await saveButton().trigger('click')
    await flush()
    expect(callsTo(fetchMock, SAVE)).toHaveLength(0)
    expect(toasts.value.map((x) => [x.kind, x.text])).toEqual([['error', t('settings.proxy.required')]])
  })

  it('rejects an address without a supported scheme before sending it', async () => {
    const fetchMock = await openConnection({ [SAVE]: { status: 'success' } })
    await w.get('#s-proxy').setValue('proxy.example:8080')
    await saveButton().trigger('click')
    await flush()
    expect(callsTo(fetchMock, SAVE)).toHaveLength(0)
    expect(toasts.value.map((x) => x.text)).toEqual([t('settings.proxy.invalid')])
  })

  it('asks for the password again when the server will not reuse the stored one', async () => {
    const retype = json({ detail: { reason: 'proxy_password_required', message: 'Re-enter the proxy password.' } }, 422)
    const fetchMock = await openConnection({ [SAVE]: () => retype }, { ...SETTINGS, use_proxy: true, proxy_url: MASKED })
    await w.get('#s-proxy').setValue('http://scraper:***@other.example:8080')
    await saveButton().trigger('click')
    await flush()
    expect(sentSettings(fetchMock)).toEqual([{ proxy_url: 'http://scraper:***@other.example:8080' }])
    expect(toasts.value.map((x) => [x.kind, x.text])).toEqual([['error', t('settings.proxy.retypePassword')]])
  })

  it('does not let an untouched proxy block saving other settings', async () => {
    // A value written to .env by hand that the form would reject: it is not being changed here
    const fetchMock = await openConnection({ [SAVE]: { status: 'success' } }, { ...SETTINGS, use_proxy: true, proxy_url: '' })
    await w.get('#settings-tab-advanced').trigger('click')
    await w.get('#s-max_retries').setValue(5)
    await w.get('#settings-panel-advanced .btn-primary').trigger('click')
    await flush()
    expect(sentSettings(fetchMock)).toEqual([{ max_retries: 5 }])
  })
})
