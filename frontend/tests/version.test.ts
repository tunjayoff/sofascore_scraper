import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import BrandMark from '@/components/BrandMark.vue'
import SettingsView from '@/views/SettingsView.vue'
import { appVersion, noteHealth } from '@/lib/appVersion'
import { i18n, setLocale } from '@/i18n'
import { flush, json, mockFetch } from './helpers'

const t = i18n.global.t
const SETTINGS = {
  language: 'tr', data_dir: 'data', max_concurrent: 5, wait_time_min: 1, wait_time_max: 2, request_timeout: 20,
  max_retries: 3, fetch_only_finished: true, save_empty_rounds: false, refresh_window_hours: 72, log_level: 'INFO',
}
const STATS = { leagues: 0, seasons: 0, matches: 0, details: 0, league_breakdown: [], disk_usage: { formatted_total: '0 B', total: 0 } }

async function mountBrand() {
  const r = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:rest(.*)*', component: { template: '<div />' } }] })
  await r.push('/')
  const w = mount(BrandMark, { global: { plugins: [i18n, r] } })
  await flush()
  return w
}

beforeEach(() => {
  setActivePinia(createPinia())
  setLocale('tr')
  appVersion.value = null
})
afterEach(() => setLocale('tr'))

describe('app version', () => {
  it('is taken from the /health ping the sidebar already makes', async () => {
    const fetchMock = mockFetch({ 'GET /health': { status: 'ok', version: '9.8.7', ui: 'vue-spa' } })
    const w = await mountBrand()
    expect(appVersion.value).toBe('9.8.7')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(w.text()).toContain(t('server.up'))
    w.unmount()
  })

  it('stays unknown, and the server still counts as up, when /health has no readable version', async () => {
    mockFetch({ 'GET /health': () => new Response('ok', { status: 200 }) })
    const w = await mountBrand()
    expect(appVersion.value).toBeNull()
    expect(w.text()).toContain(t('server.up'))
    w.unmount()
  })

  it('keeps the last known version when a later ping fails', async () => {
    noteHealth({ version: '1.2.3' })
    mockFetch({ 'GET /health': () => json({ detail: 'boom' }, 500) })
    const w = await mountBrand()
    expect(appVersion.value).toBe('1.2.3')
    expect(w.text()).toContain(t('server.down'))
    w.unmount()
  })

  it('ignores bodies without a usable version', () => {
    for (const body of [null, undefined, 'x', {}, { version: 2 }, { version: '  ' }]) noteHealth(body)
    expect(appVersion.value).toBeNull()
  })

  it('is shown on Settings in both languages, and hidden while unknown', async () => {
    mockFetch({ 'GET /api/settings': SETTINGS, 'GET /api/stats/system': STATS })
    const w = mount(SettingsView, { global: { plugins: [i18n] } })
    await flush()
    expect(w.find('.app-version').exists()).toBe(false)

    noteHealth({ version: '9.8.7' })
    await flush()
    expect(w.find('.app-version').text()).toBe('Sürüm 9.8.7')

    setLocale('en')
    await flush()
    expect(w.find('.app-version').text()).toBe('Version 9.8.7')
    w.unmount()
  })
})
