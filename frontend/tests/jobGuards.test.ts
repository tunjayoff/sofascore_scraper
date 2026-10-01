import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import SettingsView from '@/views/SettingsView.vue'
import LeaguesView from '@/views/LeaguesView.vue'
import { ApiError, apiSend } from '@/api/client'
import { useLeaguesStore } from '@/stores/leagues'
import { useSportStore } from '@/stores/sport'
import { errorText, toasts } from '@/lib/toast'
import { i18n, setLocale } from '@/i18n'
import en from '@/locales/en'
import tr from '@/locales/tr'
import { callsTo, flush, json, mockFetch } from './helpers'

const t = i18n.global.t
const LEAGUES = [{ id: 17, name: 'Premier League', sport: 'football' }]
const STATS = { leagues: 1, seasons: 1, matches: 0, details: 0, league_breakdown: [], disk_usage: { formatted_total: '1 MB', total: 1 } }
const SETTINGS = {
  language: 'tr', data_dir: 'data', max_concurrent: 5, request_rate_limit: 0, wait_time_min: 1, wait_time_max: 2,
  request_timeout: 20, max_retries: 3, fetch_only_finished: true, save_empty_rounds: false, refresh_window_hours: 72, log_level: 'INFO',
}
// What src/web/app.py answers while a download job runs
const jobRunning = () =>
  json({ detail: { code: 'job_running', message: 'A download job is running; stop it or wait until it finishes, then try again.' } }, 409)

const button = (w: ReturnType<typeof mount>, text: string) => w.findAll('button').find((b) => b.text() === text)!
const errorToasts = () => toasts.value.filter((x) => x.kind === 'error').map((x) => x.text)

async function settingsDataTab(routes: Parameters<typeof mockFetch>[0]) {
  const fetchMock = mockFetch({ 'GET /api/settings': SETTINGS, 'GET /api/stats/system': STATS, 'GET /api/leagues': LEAGUES, ...routes })
  const w = mount(SettingsView, { global: { plugins: [i18n] } })
  await flush()
  await w.find('#settings-tab-data').trigger('click')
  return { w, fetchMock }
}

beforeEach(() => {
  setActivePinia(createPinia())
  useSportStore().set('all')
  toasts.value = []
  setLocale('tr')
})
afterEach(() => setLocale('tr'))

describe('coded API refusals', () => {
  it('keeps the code on the error and shows our wording in the current language', async () => {
    mockFetch({ 'POST /api/data/clear': jobRunning })
    const e = (await apiSend('/api/data/clear', 'POST', { scope: 'all' }).catch((x) => x)) as ApiError
    expect(e).toBeInstanceOf(ApiError)
    expect(e).toMatchObject({ status: 409, code: 'job_running' })
    expect(e.message).toContain('A download job is running')

    expect(errorText(e)).toBe(tr.errors.job_running)
    setLocale('en')
    expect(errorText(e)).toBe(en.errors.job_running)
  })

  it('has a message in both languages for every code the server sends', () => {
    for (const code of ['job_running', 'data_operation_running', 'data_dir_unusable'] as const) {
      expect(tr.errors[code]).toBeTruthy()
      expect(en.errors[code]).toBeTruthy()
      expect(tr.errors[code]).not.toBe(en.errors[code])
    }
  })

  it('falls back to the server message for a code it does not know', async () => {
    mockFetch({ 'POST /api/x': json({ detail: { code: 'brand_new_reason', message: 'Server says no.' } }, 409) })
    const e = (await apiSend('/api/x', 'POST').catch((x) => x)) as ApiError
    expect(e.code).toBe('brand_new_reason')
    expect(errorText(e)).toBe('Server says no.')
  })

  it('still stringifies an object detail that is not a coded refusal', async () => {
    mockFetch({ 'POST /api/x': json({ detail: { foo: 1 } }, 400) })
    const e = (await apiSend('/api/x', 'POST').catch((x) => x)) as ApiError
    expect(e.code).toBeUndefined()
    expect(e.message).toBe('{"foo":1}')
  })
})

describe('SettingsView while a download runs', () => {
  it('explains why the data was not deleted', async () => {
    vi.stubGlobal('confirm', () => true)
    const { w } = await settingsDataTab({ 'POST /api/data/clear': jobRunning })
    await button(w, t('settings.clear')).trigger('click')
    await flush()
    expect(errorToasts()).toEqual([tr.errors.job_running])
    expect(toasts.value.map((x) => x.text)).not.toContain(t('settings.cleared'))
    w.unmount()
  })

  it('explains why the backup was not taken', async () => {
    const { w } = await settingsDataTab({ 'POST /api/data/backup': jobRunning })
    await button(w, t('settings.backup')).trigger('click')
    await flush()
    expect(errorToasts()).toEqual([tr.errors.job_running])
    expect(w.text()).not.toContain(t('settings.backupReady'))
    w.unmount()
  })

  it('explains why the data folder was not changed and keeps the edit for a retry', async () => {
    const { w } = await settingsDataTab({ 'POST /api/settings': jobRunning })
    await w.find('#s-dir').setValue('data2')
    await button(w, t('common.save')).trigger('click')
    await flush()
    expect(errorToasts()).toEqual([tr.errors.job_running])
    expect((w.find('#s-dir').element as HTMLInputElement).value).toBe('data2')
    expect(w.text()).toContain(t('settings.dataDirHint'))
    w.unmount()
  })
})

describe('SettingsView data folder', () => {
  it('says what a folder change means and reloads the league counts', async () => {
    let dir = 'data'
    const { w, fetchMock } = await settingsDataTab({
      'GET /api/settings': () => ({ ...SETTINGS, data_dir: dir }),
      'POST /api/settings': (init?: RequestInit) => {
        dir = JSON.parse(String(init?.body)).data_dir
        return { status: 'success', data_dir_changed: true }
      },
    })
    await w.find('#s-dir').setValue('data2')
    await button(w, t('common.save')).trigger('click')
    await flush()
    expect(toasts.value.map((x) => [x.kind, x.text])).toEqual([['ok', t('settings.dataDirChanged')]])
    expect(callsTo(fetchMock, 'GET /api/leagues')).toHaveLength(1)
    expect(useLeaguesStore().all.map((l) => l.name)).toEqual(['Premier League'])
    w.unmount()
  })

  it('keeps the plain "saved" message when the folder did not change', async () => {
    const { w, fetchMock } = await settingsDataTab({ 'POST /api/settings': { status: 'success' } })
    await w.find('#settings-tab-advanced').trigger('click')
    await w.find('#s-max_retries').setValue(4)
    await button(w, t('common.save')).trigger('click')
    await flush()
    expect(toasts.value.map((x) => x.text)).toEqual([t('settings.saved')])
    expect(callsTo(fetchMock, 'GET /api/leagues')).toHaveLength(0)
    w.unmount()
  })
})

describe('LeaguesView while a download runs', () => {
  it('explains why the league was not removed and keeps it listed', async () => {
    vi.stubGlobal('confirm', () => true)
    mockFetch({ 'GET /api/leagues': LEAGUES, 'GET /api/stats/system': STATS, 'DELETE /api/leagues/17': jobRunning })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:rest(.*)*', component: { template: '<div />' } }] })
    await useLeaguesStore().load()
    const w = mount(LeaguesView, { global: { plugins: [i18n, router] } })
    await flush()
    await w.find(`button[aria-label="${t('leagues.remove')}: Premier League"]`).trigger('click')
    await flush()
    expect(errorToasts()).toEqual([tr.errors.job_running])
    expect(w.text()).toContain('Premier League')
    w.unmount()
  })
})
