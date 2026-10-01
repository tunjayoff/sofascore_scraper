import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SettingsView from '@/views/SettingsView.vue'
import { toasts } from '@/lib/toast'
import { i18n, setLocale } from '@/i18n'
import { callsTo, flush, mockFetch } from './helpers'

const t = i18n.global.t
const SAVE = 'POST /api/settings'
const STATS = { leagues: 1, seasons: 1, matches: 0, details: 0, league_breakdown: [], disk_usage: { formatted_total: '1 MB', total: 1 } }
const SETTINGS = {
  language: 'en', data_dir: 'data', max_concurrent: 10, request_rate_limit: 5, wait_time_min: 0.2, wait_time_max: 0.5,
  request_timeout: 20, max_retries: 3, fetch_only_finished: true, save_empty_rounds: false, refresh_window_hours: 72,
  log_level: 'INFO', use_proxy: false, proxy_url: '',
}

let w: VueWrapper

async function openAdvanced(rate: number, routes: Parameters<typeof mockFetch>[0] = {}) {
  const fetchMock = mockFetch({
    'GET /api/settings': { ...SETTINGS, request_rate_limit: rate },
    'GET /api/stats/system': STATS,
    ...routes,
  })
  w = mount(SettingsView, { global: { plugins: [i18n] } })
  await flush()
  await w.get('#settings-tab-advanced').trigger('click')
  return fetchMock
}

const field = () => w.get('#s-request_rate_limit')
const warning = () => w.find('[data-testid="rate-warning"]')

beforeEach(() => {
  setActivePinia(createPinia())
  setLocale('en')
  toasts.value = []
})

afterEach(() => {
  w?.unmount()
  setLocale('tr')
})

describe('Settings request budget', () => {
  it('explains the number, the default and the risk, and shows no warning at the default', async () => {
    await openAdvanced(5)
    const hint = w.get('#s-rate-hint').text()
    expect(hint).toBe(t('settings.rateLimitHint', { n: 5 }))
    expect(hint).toContain('requests per second')
    expect(hint).toContain('The default is 5')
    expect(hint).toContain('blocks you')
    expect(field().attributes('aria-describedby')).toBe('s-rate-hint')
    expect(warning().exists()).toBe(false)
    expect(field().classes()).not.toContain('field-warn')
  })

  it.each([1, 0.5, 4.9, 5])('shows no warning at or below the default (%s)', async (rate) => {
    await openAdvanced(rate)
    expect(warning().exists()).toBe(false)
  })

  it.each([5.5, 6, 100])('warns when the saved value is above the default (%s)', async (rate) => {
    await openAdvanced(rate)
    expect(warning().text()).toBe(t('settings.rateLimitWarn.high', { n: 5 }))
    expect(warning().attributes('role')).toBe('status')
    expect(field().classes()).toContain('field-warn')
  })

  it('warns that the limit is off at 0', async () => {
    await openAdvanced(0)
    expect(warning().text()).toBe(t('settings.rateLimitWarn.off', { n: 5 }))
    expect(warning().text()).toContain('off')
    expect(field().classes()).toContain('field-warn')
  })

  it('follows the field as it is edited, before anything is saved', async () => {
    const fetchMock = await openAdvanced(5, { [SAVE]: { status: 'success' } })
    await field().setValue(20)
    expect(warning().text()).toBe(t('settings.rateLimitWarn.high', { n: 5 }))
    await field().setValue(0)
    expect(warning().text()).toBe(t('settings.rateLimitWarn.off', { n: 5 }))
    await field().setValue(2)
    expect(warning().exists()).toBe(false)
    await field().setValue('') // a cleared field is invalid, not risky
    expect(warning().exists()).toBe(false)
    expect(callsTo(fetchMock, SAVE)).toHaveLength(0)
  })

  it('only warns: a value above the default and "off" are both saved without a confirmation', async () => {
    const fetchMock = await openAdvanced(5, { [SAVE]: { status: 'success' } })
    const save = () => w.get('#settings-panel-advanced .btn-primary')
    await field().setValue(30)
    expect(save().attributes('disabled')).toBeUndefined()
    await save().trigger('click')
    await flush()
    await w.get('#s-request_rate_limit').setValue(0)
    await save().trigger('click')
    await flush()
    const sent = callsTo(fetchMock, SAVE).map(([, init]) => JSON.parse(String(init?.body)))
    expect(sent).toEqual([{ request_rate_limit: 30 }, { request_rate_limit: 0 }])
    expect(toasts.value.map((x) => x.kind)).not.toContain('error')
  })

  it('speaks Turkish too', async () => {
    setLocale('tr')
    await openAdvanced(0)
    expect(w.get('#s-rate-hint').text()).toContain('Varsayılan 5')
    expect(w.get('#s-rate-hint').text()).toContain('engelleme olasılığını artırır')
    expect(warning().text()).toContain('Sınır kapalı')
    await field().setValue(12)
    expect(warning().text()).toContain('saniyede 5 isteğin üstünde')
  })
})
