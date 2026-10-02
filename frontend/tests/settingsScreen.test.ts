import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import type { Router } from 'vue-router'
import SettingsScreen from '@/screens/settings/SettingsScreen.vue'
import { clearToasts, uiToasts } from '@/ui/toast'
import { tokenInUse } from '@/app/session'
import { density } from '@/ui/prefs'
import { i18n, setLocale } from '@/i18n'
import { callsTo, flush, json, mockFetch } from './helpers'
import { axeViolations, setting, settingsDoc, status, v1Error } from './v1'

const t = i18n.global.t
let w: VueWrapper
let router: Router

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
  tokenInUse.value = false
})
afterEach(() => {
  w?.unmount()
  density.value = 'comfortable'
})

const SETTINGS = [
  setting('client.rate', 5),
  setting('client.max_concurrent', 3, { source: 'overrides', source_name: '/srv/config/overrides.json' }),
  setting('client.timeout_seconds', 10, { source: 'file', source_name: '/srv/sofascore.toml', locked: true, writable: false }),
  setting('client.use_proxy', true, { source: 'env', source_name: 'SOFASCORE_CLIENT__USE_PROXY', locked: true, writable: false }),
  setting('client.proxy', 'http://user:***@proxy:8080', { source: 'overrides', secret: true }),
  setting('client.base_url', 'https://www.sofascore.com/api/v1'),
  setting('client.captcha_token', '***', { writable: false, secret: true }),
  setting('fetch.only_finished', true),
  setting('log.level', 'INFO'),
  setting('storage.data_dir', '/srv/data', { source: 'flag', locked: true, writable: false }),
  setting('server.port', 8000, { writable: false }),
  setting('brand.new_key', 'x', { writable: false }),
]

async function open(routes: Record<string, unknown> = {}, path = '/settings') {
  const f = mockFetch({ 'GET /api/v1/settings': { data: settingsDoc(SETTINGS) }, 'GET /api/v1/status': { data: status() }, ...routes })
  ;({ w, router } = await mountScreen(path))
  await flush()
  return f
}

async function mountScreen(path: string) {
  const { mountScreen: m } = await import('./v1')
  return m(SettingsScreen, path, '/settings')
}

const row = (key: string) => w.find(`[data-setting="${key}"]`)

describe('Settings', () => {
  it('groups the settings by section; every row says where its value comes from', async () => {
    await open()
    expect(w.findAll('[role="tab"]').map((x) => x.text())).toEqual([
      ...['requests', 'data', 'refresh', 'display', 'logging', 'storage', 'server', 'other'].map((s) => t(`ui.settings.section.${s}`)),
      t('ui.settings.section.browser'),
    ])
    expect(row('client.rate').find('label').text()).toBe(t('ui.setting.client.rate'))
    expect(row('client.rate').find('[data-testid="source"]').text()).toBe(t('ui.settings.source.default'))
    expect(row('client.max_concurrent').find('[data-testid="source"]').text()).toBe(t('ui.settings.source.overrides'))
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('shows locked and read-only settings in place, with the reason', async () => {
    await open()
    expect(row('client.timeout_seconds').find('input').exists()).toBe(false)
    expect(row('client.timeout_seconds').find('[data-testid="lock"]').text()).toBe(t('ui.settings.lock.file', { file: '/srv/sofascore.toml' }))
    expect(row('client.use_proxy').find('[data-testid="lock"]').text()).toBe(t('ui.settings.lock.env', { name: 'SOFASCORE_CLIENT__USE_PROXY' }))
    expect(row('client.captcha_token').text()).not.toContain('***')
    expect(row('client.captcha_token').find('[data-testid="lock"]').text()).toBe(t('ui.settings.lock.readOnly'))
  })

  it('never shows a secret; it can only be replaced', async () => {
    const f = await open({ 'PATCH /api/v1/settings': { data: settingsDoc(SETTINGS) } })
    const proxy = row('client.proxy')
    expect(proxy.find('input').exists()).toBe(false)
    await proxy.findAll('button').find((b) => b.text() === t('ui.settings.replace'))!.trigger('click')
    const input = row('client.proxy').find('input[type="password"]')
    expect((input.element as HTMLInputElement).value).toBe('')
    await input.setValue('http://other:8080')
    await input.trigger('change')
    await w.find('[data-testid="save"]').trigger('click')
    await flush()
    expect(JSON.parse(String(callsTo(f, 'PATCH /api/v1/settings')[0][1]!.body))).toEqual({ values: { 'client.proxy': 'http://other:8080' } })
  })

  it('collects changes and saves them all at once', async () => {
    const f = await open({ 'PATCH /api/v1/settings': () => json({ data: settingsDoc(SETTINGS.map((s) => (s.key === 'client.rate' ? { ...s, value: 3, source: 'overrides' as const } : s))) }) })
    await row('client.rate').find('input').setValue('3')
    await row('client.rate').find('input').trigger('change')
    await row('client.max_concurrent').findAll('button').find((b) => b.text() === t('ui.settings.reset'))!.trigger('click')
    expect(w.find('[data-testid="save-bar"]').text()).toContain(t('ui.settings.changes', { n: 2 }))
    expect(w.find('[role="tab"][aria-selected="true"]').text()).toContain('2')
    await w.find('[data-testid="save"]').trigger('click')
    await flush()
    expect(JSON.parse(String(callsTo(f, 'PATCH /api/v1/settings')[0][1]!.body))).toEqual({ values: { 'client.rate': 3, 'client.max_concurrent': null } })
    expect(w.find('[data-testid="save-bar"]').exists()).toBe(false)
    expect(uiToasts.value[0].text).toBe(t('ui.settings.saved'))
  })

  it('a refused save marks the rows it names and saves nothing', async () => {
    await open({
      'PATCH /api/v1/settings': () =>
        v1Error(400, 'invalid_request', { locked: [{ key: 'client.rate', source: 'file', source_name: '/srv/sofascore.toml' }] }),
    })
    await row('client.rate').find('input').setValue('7')
    await row('client.rate').find('input').trigger('change')
    expect(row('client.rate').text()).toContain(t('ui.settings.rateWarning', { n: 5 }))
    await w.find('[data-testid="save"]').trigger('click')
    await flush()
    expect(row('client.rate').find('[role="alert"]').text()).toBe(t('ui.settings.refusedLocked'))
    const bar = w.find('[data-testid="save-bar"]')
    expect(bar.text()).toContain(t('ui.settings.nothingSaved'))
    expect(bar.text()).toContain('req-123abc')
    expect(bar.text()).toContain(t('ui.settings.changes', { n: 1 }))
  })

  it('a value the server refuses shows its message under the field', async () => {
    await open({
      'PATCH /api/v1/settings': () =>
        v1Error(422, 'invalid_request', { errors: [{ loc: ['body', 'values', 'client.rate'], message: 'must be at most 1000', type: 'value_error' }] }),
    })
    await row('client.rate').find('input').setValue('5000')
    await row('client.rate').find('input').trigger('change')
    await w.find('[data-testid="save"]').trigger('click')
    await flush()
    expect(row('client.rate').find('[role="alert"]').text()).toBe('must be at most 1000')
    expect(row('client.rate').find('input').attributes('aria-invalid')).toBe('true')
  })

  it('Discard drops every change', async () => {
    const f = await open({}, '/settings?tab=data')
    await row('fetch.only_finished').find('input').setValue(false)
    await row('fetch.only_finished').find('input').trigger('change')
    await w.findAll('[data-testid="save-bar"] button').find((b) => b.text() === t('ui.settings.discard'))!.trigger('click')
    expect(w.find('[data-testid="save-bar"]').exists()).toBe(false)
    expect(callsTo(f, 'PATCH /api/v1/settings')).toHaveLength(0)
  })

  it('asks before leaving with unsaved changes', async () => {
    await open()
    await row('client.rate').find('input').setValue('4')
    await row('client.rate').find('input').trigger('change')
    await router.push('/jobs')
    await flush()
    expect(router.currentRoute.value.path).toBe('/settings')
    const dialog = w.find('[role="alertdialog"]')
    expect(dialog.text()).toContain(t('ui.settings.leaveTitle', { n: 1 }))
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(router.currentRoute.value.path).toBe('/jobs')
  })

  it('shows the server section read-only and keys the UI does not know under Other', async () => {
    await open({}, '/settings?tab=server')
    expect(w.text()).toContain('/srv/sofascore/sofascore.toml')
    expect(row('server.port').find('input').exists()).toBe(false)
    w.unmount()
    await open({}, '/settings?tab=other')
    expect(row('brand.new_key').text()).toContain('brand.new_key')
  })

  it('a load error offers Retry', async () => {
    mockFetch({ 'GET /api/v1/settings': () => v1Error(500, 'internal'), 'GET /api/v1/status': { data: status() } })
    ;({ w } = await mountScreen('/settings'))
    await flush()
    expect(w.find('[data-testid="error-state"]').text()).toContain(t('ui.error.internal'))
  })

  it('"This browser" keeps language, theme, density and times here, and offers Sign out with a token', async () => {
    tokenInUse.value = true
    await open({}, '/settings?tab=browser')
    expect(w.text()).toContain(t('ui.prefs.note'))
    const compact = w.find('input[name="pref-density"][value="compact"]')
    await compact.setValue(true)
    await compact.trigger('change')
    expect(localStorage.getItem('ssui.density')).toBe('compact')
    await w.find('input[name="pref-language"][value="tr"]').trigger('change')
    expect(localStorage.getItem('ss_lang')).toBe('tr')
    expect(w.text()).toContain(t('ui.menu.signOut'))
    expect(await axeViolations(w.element)).toEqual([])
  })
})
