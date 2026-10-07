/* eslint-disable vue/one-component-per-file -- small host components of the dialog checks */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, nextTick, ref } from 'vue'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import { openModals, setTeleportDialogs } from '@/ui/modal'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import FollowEditorScreen from '@/screens/follows/FollowEditorScreen.vue'
import FollowsScreen from '@/screens/follows/FollowsScreen.vue'
import { hitPlace, placeName, playerTeam } from '@/screens/follows/followText'
import { resetSports } from '@/app/sports'
import { callsTo, flush, mockFetch } from './helpers'
import { axeViolations, follow, mountScreen, page, sport, status } from './v1'

/**
 * FX-24: the findings of the end-to-end test of the web UI against the real SofaScore (F5 to F37 of the
 * orchestrator's list), one block per finding. No request leaves the test.
 */
const t = i18n.global.t
let wrappers: VueWrapper[] = []

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
})
afterEach(() => {
  for (const w of wrappers) w.unmount()
  wrappers = []
  document.body.innerHTML = ''
  vi.useRealTimers()
})

describe('F22: a dialog is modal for real', () => {
  beforeEach(() => setTeleportDialogs(true))
  afterEach(() => setTeleportDialogs(false))

  /** A table row that opens its page on a click, with a remove dialog inside it, as in Leagues & follows. */
  const Row = defineComponent({
    props: { onRow: { type: Function, required: true } },
    setup(props) {
      const open = ref(true)
      return () =>
        h('table', [
          h('tbody', [
            h('tr', { 'data-row': '', onClick: () => (props.onRow as () => void)() }, [
              h('td', 'UEFA Super Cup'),
              h('td', { class: 'text-right whitespace-nowrap' }, [
                open.value
                  ? h(ConfirmDialog, { title: 'Stop following?', confirmLabel: 'Remove', danger: true, typedWord: 'DELETE', onClose: () => (open.value = false) }, () => h('p', { 'data-testid': 'text' }, 'Its matches go too.'))
                  : null,
              ]),
            ]),
          ]),
        ])
    },
  })

  it('is shown at the end of <body>, outside the row; a click in it does not open the row; the page behind is inert', async () => {
    const page = document.createElement('div')
    page.id = 'app'
    document.body.appendChild(page)
    const onRow = vi.fn()
    const w = mount(Row, { props: { onRow }, global: { plugins: [i18n] }, attachTo: page })
    wrappers.push(w)
    await flush()
    const dialog = document.querySelector<HTMLElement>('[role="alertdialog"]')!
    expect(dialog).not.toBeNull()
    // not inside the table row any more
    expect(w.element.contains(dialog)).toBe(false)
    expect(dialog.closest('tr')).toBeNull()
    expect(dialog.parentElement!.parentElement).toBe(document.body)
    // clicking its text or its field does not reach the row (before: the row opened its page)
    dialog.querySelector<HTMLElement>('[data-testid="text"]')!.click()
    dialog.querySelector<HTMLInputElement>('input')!.click()
    expect(onRow).not.toHaveBeenCalled()
    // the page behind is inert while the dialog is open: neither clickable nor focusable nor in the a11y tree
    expect(page.hasAttribute('inert')).toBe(true)
    expect(dialog.closest('[inert]')).toBeNull()
    expect(openModals()).toBe(1)
    // the typed word field is the focused element and the only text field outside the inert page
    expect(document.activeElement).toBe(dialog.querySelector('input'))
    const live = [...document.querySelectorAll('input')].filter((x) => !x.closest('[inert]'))
    expect(live).toHaveLength(1)
    expect(await axeViolations(dialog)).toEqual([])
    // Cancel closes it: the page is live again
    const cancel = [...dialog.querySelectorAll('button')].find((b) => b.textContent?.trim() === t('ui.common.cancel'))!
    cancel.click()
    await nextTick()
    await flush()
    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
    expect(page.hasAttribute('inert')).toBe(false)
    expect(openModals()).toBe(0)
  })

  it('a dialog over a dialog: only the newest is live; closing it gives the first one back', async () => {
    const page = document.createElement('div')
    document.body.appendChild(page)
    const second = ref(true)
    const Two = defineComponent({
      setup: () => () => [
        h(ConfirmDialog, { title: 'First', confirmLabel: 'OK' }, () => h('p', 'one')),
        second.value ? h(ConfirmDialog, { title: 'Second', confirmLabel: 'OK' }, () => h('p', 'two')) : null,
      ],
    })
    const w = mount(Two, { global: { plugins: [i18n] }, attachTo: page })
    wrappers.push(w)
    await flush()
    const [first, top] = [...document.querySelectorAll<HTMLElement>('[role="alertdialog"]')]
    expect(top.closest('[inert]')).toBeNull()
    expect(first.closest('[inert]')).not.toBeNull()
    second.value = false
    await nextTick()
    await flush()
    expect(first.closest('[inert]')).toBeNull()
    expect(page.hasAttribute('inert')).toBe(true)
  })
})

const list = <T>(data: T[]) => ({ data, page: { limit: 0, next_cursor: null } })
const SPORTS = {
  'GET /api/v1/sports': list([sport('football'), sport('tennis')]),
  'GET /api/v1/sports/football': { data: sport('football') },
  'GET /api/v1/sports/tennis': { data: sport('tennis') },
  'GET /api/v1/status': { data: status() },
}
const regionEn = (code: string) => new Intl.DisplayNames(['en'], { type: 'region' }).of(code)

describe('F12, F31, F32: search hits in the reader’s words', () => {
  beforeEach(() => resetSports())

  it('regions, home nations and countries SofaScore writes in English are named in the reader’s language', () => {
    setLocale('tr')
    expect(placeName(null, 'Europe')).toBe('Avrupa')
    expect(placeName(null, 'South America')).toBe('Güney Amerika')
    expect(placeName(null, 'North & Central America')).toBe('Kuzey ve Orta Amerika')
    expect(placeName('EN', 'England')).toBe('İngiltere')
    expect(placeName(null, 'England')).toBe('İngiltere')
    // a country without a code, by SofaScore's English name, also an older one ("Turkey")
    expect(placeName(null, 'Turkey')).toBe('Türkiye')
    expect(placeName(null, 'Spain')).toBe('İspanya')
    expect(placeName('TR', 'Turkey')).toBe('Türkiye')
    // a league's category that is a tour, not a place, stays as SofaScore writes it
    expect(placeName(null, 'ATP')).toBe('ATP')
    expect(hitPlace({ category: { name: 'Europe' }, country: null })).toBe('Avrupa')
    setLocale('en')
    expect(placeName(null, 'Europe')).toBe('Europe')
    expect(placeName('EN', 'England')).toBe('England')
    expect(placeName(null, 'Turkey')).toBe(regionEn('TR'))
    expect(placeName('', null)).toBe('')
  })

  it('SofaScore’s placeholder team “No team” is never shown', () => {
    expect(playerTeam({ id: 0, name: 'No team' })).toBeNull()
    expect(playerTeam({ name: ' no team ' })).toBeNull()
    expect(playerTeam(null)).toBeNull()
    expect(playerTeam({ name: 'Galatasaray' })).toBe('Galatasaray')
  })

  it('a tennis player SofaScore lists as a team is shown with the players and followed as a team; a region in Turkish', async () => {
    setLocale('tr')
    const f = mockFetch({
      ...SPORTS,
      'POST /api/v1/tournaments/search': list([
        { kind: 'team', id: 412345, name: 'Chiara Icardi', slug: 'icardi-chiara', sport: 'tennis', category: { country_code: 'IT' }, country: { code: 'IT', name: 'Italy' }, team: null, followed: false },
        { kind: 'player', id: 70996, name: 'Mauro Icardi', sport: 'football', category: { country_code: 'AR' }, country: { code: 'AR', name: 'Argentina' }, team: { id: 3061, name: 'Galatasaray' }, followed: false },
        { kind: 'player', id: 99001, name: 'Luca Icardi', sport: 'football', category: { country_code: 'IT' }, country: { code: 'IT', name: 'Italy' }, team: { id: 0, name: 'No team' }, followed: false },
        { kind: 'tournament', id: 384, name: 'CONMEBOL Libertadores', sport: 'football', category: { id: 1470, name: 'South America' }, country: null, team: null, followed: false },
      ]),
    })
    const { w } = await mountScreen(FollowEditorScreen, '/follows/new')
    wrappers.push(w)
    await flush()
    await w.find('[data-testid="editor-query"]').setValue('icardi')
    await w.find('[data-testid="editor-search"]').trigger('submit')
    await flush()
    expect(callsTo(f, 'POST /api/v1/tournaments/search')).toHaveLength(1)
    // no "Teams" group: the tennis player is with the players, with the player icon
    expect(w.find('[data-group="team"]').exists()).toBe(false)
    const players = w.find('[data-group="player"]')
    expect(players.findAll('[role="option"]').map((o) => o.attributes('data-hit'))).toEqual(['team:412345', 'player:70996', 'player:99001'])
    expect(w.find('[data-group="tournament"] [data-hit="tournament:384"]').text()).toContain('Güney Amerika')
    // “No team” is not a team
    expect(w.find('[data-hit="player:99001"]').text()).not.toContain('No team')
    expect(w.find('[data-hit="player:99001"] [data-testid="hit-team"]').exists()).toBe(false)
    expect(w.find('[data-hit="player:70996"] [data-testid="hit-team"]').text()).toBe(t('ui.followEditor.playsFor', { team: 'Galatasaray' }))
    expect(await axeViolations(w.find('[data-testid="editor-hits"]').element)).toEqual([])
    // picked: shown as a player, followed as a team (its follow id stays team:412345)
    await w.find('[data-hit="team:412345"]').trigger('click')
    await flush()
    const picked = w.find('[data-testid="editor-picked"]')
    expect(picked.attributes('data-hit')).toBe('team:412345')
    expect(picked.text()).toContain(t('ui.follows.kind.player'))
    expect(w.find('[data-testid="editor-individual"]').text()).toBe(t('ui.suggest.individual', { sport: 'tenis' }))
    expect((w.find('[data-kind="team"] input').element as HTMLInputElement).checked).toBe(true)
  })
})

describe('F26: same-named teams can be told apart', () => {
  const fener = (id: number, sportSlug: string, name = 'Fenerbahçe') => ({ kind: 'team', id, name, sport: sportSlug, category: { country_code: 'TR' }, country: { code: 'TR', name: 'Türkiye' }, team: null, followed: false })

  it('two hits with the same name and sport show their numbers; the others do not', async () => {
    mockFetch({ ...SPORTS, 'POST /api/v1/tournaments/search': list([fener(3052, 'football'), fener(36456, 'volleyball'), fener(36460, 'volleyball'), fener(253261, 'football', 'Fenerbahçe U19')]) })
    const { w } = await mountScreen(FollowEditorScreen, '/follows/new')
    wrappers.push(w)
    await flush()
    await w.find('[data-testid="editor-query"]').setValue('fener')
    await w.find('[data-testid="editor-search"]').trigger('submit')
    await flush()
    const number = (id: number) => w.find(`[data-hit="team:${id}"] [data-testid="hit-number"]`)
    expect(number(36456).text()).toBe(t('ui.suggest.number', { id: 36456 }))
    expect(number(36456).attributes('title')).toBe(t('ui.suggest.sameName'))
    expect(number(36460).text()).toBe(t('ui.suggest.number', { id: 36460 }))
    expect(number(3052).exists()).toBe(false)
    expect(number(253261).exists()).toBe(false)
  })

  it('the list of follows shows the numbers of two follows with the same name', async () => {
    mockFetch({
      ...SPORTS,
      'GET /api/v1/follows': page([
        follow({ id: 'team:36456', kind: 'team', entity_id: 36456, name: 'Fenerbahçe', sport: 'volleyball' }),
        follow({ id: 'team:36460', kind: 'team', entity_id: 36460, name: 'Fenerbahçe', sport: 'volleyball' }),
        follow({ id: 'team:3052', kind: 'team', entity_id: 3052, name: 'Fenerbahçe', sport: 'football' }),
      ]),
      'GET /api/v1/jobs': page([]),
      'GET /api/v1/events': page([]),
    })
    const { w } = await mountScreen(FollowsScreen, '/follows')
    wrappers.push(w)
    await flush()
    expect(w.findAll('[data-testid="follow-number"]').map((x) => x.text())).toEqual([t('ui.suggest.number', { id: 36456 }), t('ui.suggest.number', { id: 36460 })])
  })
})
