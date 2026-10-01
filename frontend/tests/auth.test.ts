import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import TokenPrompt from '@/components/TokenPrompt.vue'
import SettingsView from '@/views/SettingsView.vue'
import { api, apiGet, type ApiError } from '@/api/client'
import { authNeeded, reloadApp } from '@/lib/auth'
import { i18n, setLocale } from '@/i18n'
import en from '@/locales/en'
import { callsTo, flush, json, mockFetch } from './helpers'

// The real one navigates; here it only records that the app would start over
vi.mock('@/lib/auth', async (original) => ({ ...(await original<typeof import('@/lib/auth')>()), reloadApp: vi.fn() }))

const t = i18n.global.t
const LOGIN = 'POST /api/auth/login'
const LOGOUT = 'POST /api/auth/logout'
/** The refusal of src/web/app.py when SOFASCORE_API_TOKEN is set and the request carries no session. */
const authRequired = () => json({ detail: { code: 'auth_required', message: 'An access token is required.' } }, 401)
const SETTINGS = {
  language: 'en', data_dir: 'data', max_concurrent: 5, request_rate_limit: 5, wait_time_min: 1, wait_time_max: 2,
  request_timeout: 20, max_retries: 3, fetch_only_finished: true, save_empty_rounds: false, refresh_window_hours: 72,
  log_level: 'INFO',
}
const STATS = { leagues: 0, seasons: 0, matches: 0, details: 0, league_breakdown: [], disk_usage: { formatted_total: '0 B', total: 0 } }

let w: VueWrapper | undefined

beforeEach(() => {
  setActivePinia(createPinia())
  setLocale('en')
  authNeeded.value = false
  vi.mocked(reloadApp).mockClear()
})

afterEach(() => {
  w?.unmount()
  w = undefined
  setLocale('tr')
})

describe('access token: when the server asks for it', () => {
  it('a 401 auth_required from any API call asks for the token; other errors do not', async () => {
    mockFetch({
      'GET /api/leagues': json({ detail: 'League not found' }, 404),
      'GET /api/settings': authRequired(),
      [LOGIN]: json({ detail: { code: 'invalid_token', message: 'The access token is not correct.' } }, 401),
    })
    await apiGet('/api/leagues').catch(() => {})
    await api.login('nope').catch(() => {})
    expect(authNeeded.value).toBe(false)

    const e = (await apiGet('/api/settings').catch((x) => x)) as ApiError
    expect(e).toMatchObject({ status: 401, code: 'auth_required' })
    expect(authNeeded.value).toBe(true)
  })

  it('the search for new leagues is a POST, so the server can check where it comes from', async () => {
    const fetchMock = mockFetch({ 'POST /api/leagues/search-remote': [] })
    await api.searchRemote('premier league')
    expect(fetchMock.mock.calls.map(([u, init]) => [String(u), init?.method])).toEqual([
      ['/api/leagues/search-remote?q=premier%20league', 'POST'],
    ])
  })
})

describe('token prompt', () => {
  const field = () => w!.get('#auth-token')
  const submit = () => w!.get('form').trigger('submit')

  it('is a labelled dialog with a password field and cannot be sent empty', async () => {
    const fetchMock = mockFetch({})
    w = mount(TokenPrompt, { global: { plugins: [i18n] }, attachTo: document.body })
    expect(w.get('[role="dialog"]').attributes('aria-labelledby')).toBe('auth-title')
    expect(w.get('#auth-title').text()).toBe(t('auth.title'))
    expect(field().attributes('type')).toBe('password')
    expect(w.get('label[for="auth-token"]').text()).toBe(t('auth.label'))
    expect(w.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    await field().setValue('   ')
    await submit()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('sends the token once and starts the app over when the server accepts it', async () => {
    const fetchMock = mockFetch({ [LOGIN]: { required: true, authenticated: true } })
    w = mount(TokenPrompt, { global: { plugins: [i18n] } })
    await field().setValue('  correct-horse-battery-staple  ')
    await submit()
    await flush()
    expect(callsTo(fetchMock, LOGIN).map(([, init]) => JSON.parse(String(init?.body)))).toEqual([
      { token: 'correct-horse-battery-staple' },
    ])
    expect(reloadApp).toHaveBeenCalledTimes(1)
    // The page keeps no copy: the session lives in the HttpOnly cookie the server set
    expect(JSON.stringify({ ...localStorage })).not.toContain('correct-horse')
    expect(JSON.stringify({ ...sessionStorage })).not.toContain('correct-horse')
  })

  it('says so when the token is wrong and lets the user try again', async () => {
    mockFetch({ [LOGIN]: json({ detail: { code: 'invalid_token', message: 'The access token is not correct.' } }, 401) })
    w = mount(TokenPrompt, { global: { plugins: [i18n] } })
    await field().setValue('wrong')
    await submit()
    await flush()
    expect(w.get('[data-testid="auth-error"]').text()).toBe(en.errors.invalid_token)
    expect(reloadApp).not.toHaveBeenCalled()
    expect(w.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
  })
})

describe('sign out (Settings)', () => {
  const routes = { 'GET /api/settings': SETTINGS, 'GET /api/stats/system': STATS }
  const button = () => w!.find('[data-testid="sign-out"]')

  it('is hidden when the server has no access token', async () => {
    mockFetch({ ...routes, 'GET /api/auth': { required: false, authenticated: true } })
    w = mount(SettingsView, { global: { plugins: [i18n] } })
    await flush()
    expect(button().exists()).toBe(false)
  })

  it('ends the session and starts the app over', async () => {
    const fetchMock = mockFetch({
      ...routes,
      'GET /api/auth': { required: true, authenticated: true },
      [LOGOUT]: { required: true, authenticated: false },
    })
    w = mount(SettingsView, { global: { plugins: [i18n] } })
    await flush()
    expect(button().text()).toBe(t('auth.signOut'))
    await button().trigger('click')
    await flush()
    expect(callsTo(fetchMock, LOGOUT)).toHaveLength(1)
    expect(reloadApp).toHaveBeenCalledTimes(1)
  })
})
