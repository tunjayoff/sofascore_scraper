import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import AddLeagueDialog from '@/components/AddLeagueDialog.vue'
import SettingsView from '@/views/SettingsView.vue'
import { useSportStore } from '@/stores/sport'
import { SPORTS } from '@/lib/sport'
import { i18n, setLocale } from '@/i18n'
import { flush, mockFetch } from './helpers'

const t = i18n.global.t

function pressed(w: ReturnType<typeof mount>, group: string) {
  return w
    .find(`[role="group"][aria-label="${group}"]`)
    .findAll('button')
    .map((b) => [b.text(), b.attributes('aria-pressed')])
}

beforeEach(() => {
  setActivePinia(createPinia())
  setLocale('tr')
})
afterEach(() => setLocale('tr'))

describe('aria-pressed', () => {
  it('marks the chosen sport in the Add league dialog', async () => {
    useSportStore().set('all')
    const w = mount(AddLeagueDialog, { global: { plugins: [i18n] }, attachTo: document.body })
    expect(pressed(w, t('sport.all'))).toEqual([
      [t('common.all'), 'true'],
      ...SPORTS.map((s) => [t(`sport.${s}`), 'false']),
    ])
    await w.findAll('button').find((b) => b.text() === t('sport.tennis'))!.trigger('click')
    expect(pressed(w, t('sport.all')).filter(([, p]) => p === 'true')).toEqual([[t('sport.tennis'), 'true']])
    w.unmount()
  })

  it('marks the current language on Settings', async () => {
    mockFetch({
      'GET /api/settings': { language: 'tr', data_dir: 'data', max_concurrent: 5, wait_time_min: 1, wait_time_max: 2, request_timeout: 20, max_retries: 3, fetch_only_finished: true, save_empty_rounds: false, refresh_window_hours: 72, log_level: 'INFO' },
      'GET /api/stats/system': { leagues: 0, seasons: 0, matches: 0, details: 0, league_breakdown: [], disk_usage: { formatted_total: '0 B', total: 0 } },
      'POST /api/settings': { status: 'success' },
    })
    const w = mount(SettingsView, { global: { plugins: [i18n] } })
    await flush()
    expect(pressed(w, t('settings.language'))).toEqual([['Türkçe', 'true'], ['English', 'false']])
    await w.findAll('button').find((b) => b.text() === 'English')!.trigger('click')
    await flush()
    expect(pressed(w, t('settings.language'))).toEqual([['Türkçe', 'false'], ['English', 'true']])
    w.unmount()
  })
})
