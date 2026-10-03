import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import App from '@/App.vue'
import TokenPrompt from '@/app/TokenPrompt.vue'
import { routes } from '@/router'
import { authNeeded, reloadApp } from '@/lib/auth'
import { authLockedUntil, tokenInUse } from '@/app/session'
import { NAV } from '@/app/nav'
import { railCollapsed } from '@/ui/prefs'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { callsTo, flush, json, mockFetch } from './helpers'
import { axeViolations, job, status, useFakeES, v1Error } from './v1'

vi.mock('@/lib/auth', async (original) => ({ ...(await original<typeof import('@/lib/auth')>()), reloadApp: vi.fn() }))

const t = i18n.global.t
let w: VueWrapper
let router: Router

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
  authNeeded.value = false
  authLockedUntil.value = 0
  tokenInUse.value = false
  railCollapsed.value = '0'
  useFakeES()
})
afterEach(() => {
  w?.unmount()
  vi.useRealTimers()
})

/** What the classic views ask for when they open (the legacy routes). */
const LEGACY = {
  'GET /api/leagues': [],
  'GET /api/stats/system': { leagues: 0, seasons: 0, matches: 0, details: 0, league_breakdown: [], disk_usage: { formatted_total: '0 B', total: 0 } },
  'GET /api/scrape/status': { job_id: null, is_running: false, status: 'Idle', progress: 0, current_task: '' },
  'GET /health': { status: 'ok' },
  'GET /api/bypass/status': { status: 'ok', has_token: false, is_valid: false },
  'GET /api/matches': { items: [], total: 0, limit: 25, offset: 0, sort: 'desc' },
}

async function app(path: string, extra: Record<string, unknown> = {}, statusOver = {}) {
  const f = mockFetch({
    'GET /api/v1/status': { data: status(statusOver) },
    'GET /api/v1/jobs': { data: [job()], page: { limit: 20, next_cursor: null } },
    'GET /api/v1/settings': { data: { config_file: null, overrides_file: null, settings: [] } },
    'GET /api/v1/follows': { data: [], page: { limit: 0, next_cursor: null } },
    'GET /api/v1/sinks': { data: [], page: { limit: 0, next_cursor: null } },
    ...extra,
  })
  setActivePinia(createPinia())
  router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(path)
  await router.isReady()
  w = mount(App, { global: { plugins: [i18n, router] }, attachTo: document.body })
  await flush()
  await flush()
  return f
}

describe('the application shell', () => {
  it('has the side rail with the four groups, every screen, and the current page marked', async () => {
    await app('/jobs')
    const rail = w.find('nav.u-rail-nav')
    expect(rail.findAll('.u-rail-label').map((x) => x.text())).toEqual(['data', 'operations', 'system'].map((g) => t(`ui.nav.group.${g}`)))
    const items = rail.findAll('[data-nav]').map((x) => x.attributes('data-nav'))
    expect(items).toEqual(NAV.filter((n) => !n.hidden).map((n) => n.key))
    expect(rail.find('[aria-current="page"]').attributes('data-nav')).toBe('jobs')
    expect(rail.find('[data-nav="sinks"]').exists()).toBe(true)
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('the health pill says one word from /status and opens Health; the rail gets a dot', async () => {
    await app('/', {}, { bridge: { ...status().bridge, state: 'degraded', consecutive_failures: 4 } })
    const pill = w.find('[data-testid="health-pill"]')
    expect(pill.text()).toBe(t('ui.status.health.attention'))
    expect(pill.attributes('href')).toBe('/system/health')
    expect(w.find('[data-nav="health"] .u-rail-dot').exists()).toBe(true)
    expect(w.find('[data-testid="attention"]').text()).toContain(t('ui.overview.attention.degraded', { n: 4 }))
  })

  it('the job pill shows the running job with its percent and opens it; Jobs counts it', async () => {
    await app('/', {}, { active_job: job({ id: 'RUN1', state: 'running', progress: { percent: 42 }, finished_at: null }) })
    const pill = w.find('[data-testid="job-pill"]')
    expect(pill.text()).toBe(`${t('ui.job.kind.sync')} · 42%`)
    expect(pill.attributes('href')).toBe('/jobs/RUN1')
    expect(w.find('[data-nav="jobs"] .u-rail-count').text()).toBe('1')
  })

  it('polls /status every 15 s while the tab is visible', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const f = await app('/system/health')
    const before = callsTo(f, 'GET /api/v1/status').length
    await vi.advanceTimersByTimeAsync(15100)
    expect(callsTo(f, 'GET /api/v1/status').length).toBeGreaterThan(before)
  })

  it('when the server cannot be reached: a banner, the last data kept, retried', async () => {
    let up = true
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await app('/system/health', {
      'GET /api/v1/status': () => (up ? json({ data: status() }) : Promise.reject(new TypeError('Failed to fetch'))),
    })
    expect(w.find('[data-testid="offline-banner"]').exists()).toBe(false)
    up = false
    await vi.advanceTimersByTimeAsync(15100)
    await flush()
    expect(w.find('[data-testid="offline-banner"]').text()).toBe(t('ui.shell.offline'))
    expect(w.find('[data-testid="health-connection"]').exists()).toBe(true)
    up = true
    await vi.advanceTimersByTimeAsync(2100)
    await flush()
    expect(w.find('[data-testid="offline-banner"]').exists()).toBe(false)
  })

  it('the rail collapses to icons and remembers it in this browser', async () => {
    await app('/')
    await w.find(`button[aria-label="${t('ui.shell.collapseRail')}"]`).trigger('click')
    expect(w.find('.u-rail').classes()).toContain('is-collapsed')
    expect(localStorage.getItem('ssui.rail')).toBe('1')
  })

  it('the ⋯ menu has language, theme, density, shortcuts, the classic interface; Sign out only with a token', async () => {
    await app('/', LEGACY)
    const menuButton = w.find(`button[aria-label="${t('ui.menu.label')}"]`)
    await menuButton.trigger('click')
    const keys = w.findAll('[role="menu"] [data-key]').map((x) => x.attributes('data-key'))
    expect(keys).toEqual(expect.arrayContaining(['lang:en', 'lang:tr', 'theme:system', 'density:compact', 'shortcuts', 'classic']))
    expect(keys).not.toContain('signout')
    await w.find('[data-key="lang:tr"]').trigger('click')
    expect(i18n.global.locale.value).toBe('tr')
    setLocale('en')
    await menuButton.trigger('click')
    await w.find('[data-key="classic"]').trigger('click')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/classic'))
  })

  it('offers Sign out when a token is in use', async () => {
    const f = await app('/', { 'POST /api/v1/auth/logout': { data: { required: true, authenticated: false } } }, { auth_required: true })
    await w.find(`button[aria-label="${t('ui.menu.label')}"]`).trigger('click')
    await w.find('[data-key="signout"]').trigger('click')
    await flush()
    expect(callsTo(f, 'POST /api/v1/auth/logout')).toHaveLength(1)
    expect(reloadApp).toHaveBeenCalled()
  })
})

describe('keyboard', () => {
  const key = (k: string, init: KeyboardEventInit = {}) => document.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, ...init }))

  it('g then a letter opens a screen; ? lists the shortcuts; not while typing', async () => {
    await app('/')
    key('g')
    key('j')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/jobs'))
    key('g')
    key('s')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/settings'))
    key('?')
    await flush()
    expect(w.find('[role="dialog"]').text()).toContain(t('ui.shortcuts.title'))
  })

  it('Ctrl K opens the quick search, which finds screens and opens a job or event id', async () => {
    await app('/')
    key('k', { ctrlKey: true })
    await flush()
    const input = w.find('input[role="combobox"]')
    expect(document.activeElement).toBe(input.element)
    await input.setValue('heal')
    expect(w.findAll('[role="option"]').map((o) => o.text())).toEqual([expect.stringContaining(t('ui.nav.health'))])
    await input.trigger('keydown', { key: 'Enter' })
    await flush()
    expect(router.currentRoute.value.path).toBe('/system/health')
    key('k', { metaKey: true })
    await flush()
    await w.find('input[role="combobox"]').setValue('16950622')
    expect(w.find('[role="option"][aria-selected="true"]').text()).toContain(t('ui.palette.openEvent', { id: '16950622' }))
    expect(w.text()).toContain(t('ui.palette.note'))
  })

  it('the quick search finds follows by name and stored tournaments of the catalog, never SofaScore', async () => {
    const f = await app('/', {
      'GET /api/v1/follows': { data: [{ id: 'tournament:17', kind: 'tournament', entity_id: 17, name: 'Premier League', sport: 'football', seasons: 'current', live: false, enabled: true, origin: 'api', position: 0, writable: [] }], page: { limit: 0 } },
      'GET /api/v1/tournaments': { data: [{ id: 17, name: 'Premier League', sport: 'football', category_id: 1, slug: 'pl' }, { id: 8, name: 'LaLiga', sport: 'football', category_id: 2, slug: 'laliga' }], page: { limit: 6 } },
    })
    key('k', { ctrlKey: true })
    await flush()
    await w.find('input[role="combobox"]').setValue('lig')
    await new Promise((r) => setTimeout(r, 260))
    await flush()
    expect(new URL(String(callsTo(f, 'GET /api/v1/tournaments')[0][0]), 'http://x').searchParams.get('q')).toBe('lig')
    const options = w.findAll('[role="option"]').map((o) => o.text())
    expect(options).toEqual(expect.arrayContaining([expect.stringContaining('LaLiga')]))
    await w.find('input[role="combobox"]').setValue('premier')
    await new Promise((r) => setTimeout(r, 260))
    await flush()
    // the followed tournament is offered once, as the follow
    expect(w.findAll('[role="option"]').filter((o) => o.text().includes('Premier League'))).toHaveLength(1)
    await w.find('input[role="combobox"]').trigger('keydown', { key: 'Enter' })
    await flush()
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/follows/tournament/17'), { timeout: 4000 })
    expect(f.mock.calls.every(([u]) => String(u).startsWith('/api/'))).toBe(true)
  })

  it('/ focuses the filter of the table', async () => {
    await app('/jobs', { 'GET /api/v1/jobs': { data: [job()], page: { limit: 25, next_cursor: null } } })
    key('/')
    expect(document.activeElement?.getAttribute('data-filter')).toBe('state')
  })
})

describe('routing', () => {
  it('every path of the URL map has its screen; a path that names no screen goes to Overview', async () => {
    const resolve = (p: string) => router.resolve(p).name
    await app('/')
    expect(['/follows', '/follows/new', '/follows/tournament/17', '/follows/team/2672/edit', '/events', '/events/16950622', '/events/16950622/raw/statistics', '/corrections'].map(resolve)).toEqual([
      'follows', 'follow-new', 'follow', 'follow-edit', 'events', 'event', 'event-raw', 'corrections',
    ])
    expect(['/exports', '/backups', '/maintenance', '/system/sinks', '/system/logs'].map(resolve)).toEqual(['exports', 'backups', 'maintenance', 'sinks', 'logs'])
    // a follow of an unknown kind is not a follow
    expect(resolve('/follows/league/17')).toBeUndefined()
  })

  it('old addresses lead to the new screens; the classic views stay reachable under /classic', async () => {
    const legacy = LEGACY
    await app('/matches?league_id=17&season_id=52186&details=missing', { ...legacy, 'GET /api/v1/events': { data: [], page: { limit: 25, next_cursor: null } } })
    expect(router.currentRoute.value.fullPath).toBe('/events?tournament=17&season=52186&has=missing')
    w.unmount()
    await app('/classic/matches?league_id=17', legacy)
    expect(w.find('[data-testid="new-ui-link"]').attributes('href')).toBe('/')
    w.unmount()
    await app('/activity', legacy)
    expect(router.currentRoute.value.path).toBe('/jobs')
    for (const [from, to] of [['/match/16950622', '/events/16950622'], ['/leagues', '/follows'], ['/stats', '/']]) {
      await router.push(from).catch(() => {})
      expect(router.currentRoute.value.fullPath).toBe(to)
    }
    w.unmount()
    await app('/nowhere/at/all', legacy)
    expect(router.currentRoute.value.path).toBe('/')
  })
})

describe('the token prompt', () => {
  it('covers the app on a 401 from any call', async () => {
    await app('/', { 'GET /api/v1/status': () => v1Error(401, 'unauthorized'), 'GET /api/v1/jobs': () => v1Error(401, 'unauthorized') })
    expect(authNeeded.value).toBe(true)
    const dialog = w.find('[role="dialog"]')
    expect(dialog.text()).toContain(t('ui.token.title'))
    expect(document.activeElement?.id).toBe('token-field')
    expect(await axeViolations(dialog.element)).toEqual([])
  })

  async function prompt() {
    setActivePinia(createPinia())
    w = mount(TokenPrompt, { global: { plugins: [i18n] }, attachTo: document.body })
    await flush()
  }

  it('sends the token once and starts the app over', async () => {
    const f = mockFetch({ 'POST /api/v1/auth/login': { data: { required: true, authenticated: true } } })
    await prompt()
    await w.find('#token-field').setValue('test-value-1')
    await w.find('form').trigger('submit')
    await flush()
    expect(JSON.parse(String(callsTo(f, 'POST /api/v1/auth/login')[0][1]!.body))).toEqual({ token: 'test-value-1' })
    expect(reloadApp).toHaveBeenCalled()
  })

  it('says "Wrong token." and lets the user try again', async () => {
    mockFetch({ 'POST /api/v1/auth/login': () => v1Error(401, 'unauthorized', { reason: 'invalid_token' }) })
    await prompt()
    await w.find('#token-field').setValue('test-value-2')
    await w.find('form').trigger('submit')
    await flush()
    expect(w.find('[data-testid="token-error"]').text()).toBe(t('ui.token.wrong'))
    expect(w.find('button[type="submit"]').attributes('disabled')).toBeUndefined()
  })

  it('after too many wrong tokens it counts down from Retry-After and the button waits', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockFetch({
      'POST /api/v1/auth/login': () => v1Error(401, 'unauthorized', { reason: 'too_many_attempts', retry_after: 27 }, { 'Retry-After': '27' }),
    })
    await prompt()
    await w.find('#token-field').setValue('test-value-3')
    await w.find('form').trigger('submit')
    await flush()
    expect(w.find('[data-testid="token-locked"]').text()).toBe(t('ui.token.locked', { n: 27 }))
    expect(w.find('button[type="submit"]').attributes('disabled')).toBeDefined()
    await vi.advanceTimersByTimeAsync(5000)
    // the countdown ticks every second
    expect([t('ui.token.locked', { n: 22 }), t('ui.token.locked', { n: 23 })]).toContain(w.find('[data-testid="token-locked"]').text())
    await vi.advanceTimersByTimeAsync(23000)
    expect(w.find('[data-testid="token-locked"]').exists()).toBe(false)
    expect(w.find('button[type="submit"]').attributes('disabled')).toBeUndefined()
  })

  it('the show button reveals what was typed', async () => {
    mockFetch({})
    await prompt()
    expect(w.find('#token-field').attributes('type')).toBe('password')
    await w.find(`button[aria-label="${t('ui.token.show')}"]`).trigger('click')
    expect(w.find('#token-field').attributes('type')).toBe('text')
  })
})
