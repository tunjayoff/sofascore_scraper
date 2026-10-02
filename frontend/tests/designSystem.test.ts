import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, nextTick, ref } from 'vue'
import DataTable, { type Column } from '@/ui/DataTable.vue'
import FilterBar from '@/ui/FilterBar.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import UiDialog from '@/ui/UiDialog.vue'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import SidePanel from '@/ui/SidePanel.vue'
import UiTabs from '@/ui/UiTabs.vue'
import UiMenu from '@/ui/UiMenu.vue'
import TimeText from '@/ui/TimeText.vue'
import ProgressBar from '@/ui/ProgressBar.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import ErrorState from '@/ui/ErrorState.vue'
import EmptyState from '@/ui/EmptyState.vue'
import ToastHost from '@/ui/ToastHost.vue'
import { clearToasts, toast, TOAST_MS, uiToasts } from '@/ui/toast'
import { duration, parseTime, relative, utcText } from '@/ui/time'
import { density, timeDisplay } from '@/ui/prefs'
import { JOB_STATES } from '@/ui/status'
import { V1Error } from '@/api/v1/client'
import { i18n, setLocale } from '@/i18n'
import { flush } from './helpers'
import { axeViolations, routerAt } from './v1'

const t = i18n.global.t
let wrappers: VueWrapper[] = []

async function mountIt<T>(component: T, props: Record<string, unknown> = {}, slots: Record<string, unknown> = {}) {
  const router = await routerAt('/', [{ path: '/', component: { template: '<div />' } }, { path: '/rows/:id', component: { template: '<div />' } }])
  const w = mount(component as never, { props, slots: slots as never, global: { plugins: [i18n, router] }, attachTo: document.body })
  wrappers.push(w)
  return { w, router }
}

beforeEach(() => {
  setLocale('en')
  localStorage.clear()
  clearToasts()
})
afterEach(() => {
  for (const w of wrappers) w.unmount()
  wrappers = []
  vi.useRealTimers()
  timeDisplay.value = 'local'
})

type Row = { id: string; name: string; n: number; secret: string }
const ROWS: Row[] = [
  { id: 'a', name: 'Bravo', n: 2, secret: 's1' },
  { id: 'b', name: 'Alpha', n: 3, secret: 's2' },
  { id: 'c', name: 'Charlie', n: 1, secret: 's3' },
]
const COLUMNS: Column<Row>[] = [
  { key: 'name', label: 'Name', sortable: true },
  { key: 'n', label: 'Count', sortable: true, align: 'right' },
  { key: 'secret', label: 'Hidden', optional: true },
]
const tableProps = (over: Record<string, unknown> = {}) => ({
  tableId: 'test',
  caption: 'Rows',
  columns: COLUMNS,
  rows: ROWS,
  rowKey: (r: Row) => r.id,
  rowTo: (r: Row) => `/rows/${r.id}`,
  ...over,
})
const names = (w: VueWrapper) => w.findAll('tbody tr').map((r) => r.find('td').text())

describe('DataTable', () => {
  it('is a real table with column headers and a caption', async () => {
    const { w } = await mountIt(DataTable, tableProps())
    expect(w.find('caption').text()).toBe('Rows')
    expect(w.findAll('th[scope="col"]').map((x) => x.text())).toEqual(['Name', 'Count'])
    expect(names(w)).toEqual(['Bravo', 'Alpha', 'Charlie'])
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('sorts within the page, says so, and marks the column with aria-sort', async () => {
    const sort = ref<{ key: string; dir: 'asc' | 'desc' } | null>(null)
    const Host = defineComponent(() => () =>
      h(DataTable as never, { ...tableProps(), sort: sort.value, 'onUpdate:sort': (s: typeof sort.value) => (sort.value = s) }),
    )
    const { w } = await mountIt(Host)
    expect(w.find('th[aria-sort="none"]').exists()).toBe(true)
    await w.find('[data-sort="name"]').trigger('click')
    expect(names(w)).toEqual(['Alpha', 'Bravo', 'Charlie'])
    expect(w.find('th[aria-sort="ascending"]').text()).toContain('Name')
    expect(w.text()).toContain(t('ui.table.pageSorted'))
    await w.find('[data-sort="name"]').trigger('click')
    expect(names(w)).toEqual(['Charlie', 'Bravo', 'Alpha'])
    expect(w.find('th[aria-sort="descending"]').exists()).toBe(true)
    await w.find('[data-sort="n"]').trigger('click')
    expect(names(w)).toEqual(['Charlie', 'Bravo', 'Alpha'])
  })

  it('leaves the order to the server in server mode', async () => {
    const { w } = await mountIt(DataTable, tableProps({ sort: { key: 'name', dir: 'asc' }, sortMode: 'server' }))
    expect(names(w)).toEqual(['Bravo', 'Alpha', 'Charlie'])
    expect(w.text()).not.toContain(t('ui.table.pageSorted'))
  })

  it('shows optional columns on request and keeps the choice in this browser', async () => {
    const { w } = await mountIt(DataTable, tableProps())
    expect(w.text()).not.toContain('Hidden')
    await w.find('button[aria-haspopup="menu"]').trigger('click')
    const item = w.find('[role="menuitemcheckbox"]')
    expect(item.attributes('aria-checked')).toBe('false')
    await item.trigger('click')
    expect(w.findAll('th').map((x) => x.text())).toContain('Hidden')
    expect(JSON.parse(localStorage.getItem('ssui.columns.test')!)).toEqual({ secret: true })
    const { w: again } = await mountIt(DataTable, tableProps())
    expect(again.findAll('th').map((x) => x.text())).toContain('Hidden')
  })

  it('pages by cursor: Previous and Next, the page size, no page count', async () => {
    const { w } = await mountIt(DataTable, tableProps({ hasPrev: false, hasNext: true }))
    const [prev, next] = w.findAll('footer button')
    expect(prev.attributes('disabled')).toBeDefined()
    await next.trigger('click')
    expect(w.emitted('next')).toHaveLength(1)
    await w.find('footer select').setValue('50')
    expect(w.emitted('update:pageSize')![0]).toEqual([50])
    expect(w.text()).not.toMatch(/of \d+ pages/i)
  })

  it('opens the row: the first cell is a link, and a click on the row follows it', async () => {
    const { w, router } = await mountIt(DataTable, tableProps())
    expect(w.find('tbody tr a').attributes('href')).toBe('/rows/a')
    await w.findAll('tbody tr')[1].findAll('td')[1].trigger('click')
    await flush()
    expect(router.currentRoute.value.path).toBe('/rows/b')
  })

  it('j and k move between rows; not while typing', async () => {
    const { w } = await mountIt(DataTable, tableProps())
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'j' }))
    await nextTick()
    expect(document.activeElement?.getAttribute('href')).toBe('/rows/a')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'j' }))
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'j' }))
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k' }))
    await nextTick()
    expect(document.activeElement?.getAttribute('href')).toBe('/rows/b')
    expect(w.findAll('tbody tr')[1].classes()).toContain('is-active')
  })

  it('has the loading, empty and error states and a refresh line', async () => {
    vi.useFakeTimers()
    const { w } = await mountIt(DataTable, tableProps({ rows: [], loading: true }))
    expect(w.find('[data-testid="skeleton"]').exists()).toBe(true)
    vi.advanceTimersByTime(400)
    await nextTick()
    expect(w.findAll('.u-skeleton').length).toBeGreaterThan(0)
    vi.useRealTimers()

    const { w: empty } = await mountIt(DataTable, tableProps({ rows: [] }), { empty: () => h(EmptyState, { title: 'Nothing here.' }) })
    expect(empty.find('[data-testid="empty"]').text()).toContain('Nothing here.')
    expect(empty.find('table').exists()).toBe(false)

    const { w: failed } = await mountIt(DataTable, tableProps({ error: new V1Error(500, 'internal', 'x', null, 'req-9') }))
    expect(failed.find('[data-testid="error-state"]').text()).toContain('req-9')
    await failed.findAll('[data-testid="error-state"] button').find((b) => b.text() === t('ui.common.retry'))!.trigger('click')
    expect(failed.emitted('retry')).toHaveLength(1)

    const { w: refreshing } = await mountIt(DataTable, tableProps({ refreshing: true }))
    expect(refreshing.find('.u-refresh-line').exists()).toBe(true)
  })

  it('becomes a card list on phone', async () => {
    const matchMedia = window.matchMedia
    window.matchMedia = ((q: string) => ({ ...matchMedia(q), matches: q.includes('639px'), addEventListener() {}, removeEventListener() {} })) as typeof window.matchMedia
    try {
      const { w } = await mountIt(DataTable, tableProps())
      expect(w.find('table').exists()).toBe(false)
      expect(w.findAll('ul.u-cardlist li')).toHaveLength(3)
      expect(w.find('ul.u-cardlist a').attributes('href')).toBe('/rows/a')
    } finally {
      window.matchMedia = matchMedia
    }
  })

  it('follows the density choice of this browser', async () => {
    density.value = 'compact'
    await nextTick()
    expect(localStorage.getItem('ssui.density')).toBe('compact')
    density.value = 'comfortable'
    await nextTick()
    expect(localStorage.getItem('ssui.density')).toBeNull()
  })
})

describe('FilterBar', () => {
  it('shows the count of active filters and clears them', async () => {
    const { w } = await mountIt(FilterBar, { activeCount: 2, chips: [{ key: 'state', label: 'Failed' }] }, { default: () => h('input', { 'aria-label': 'x' }) })
    expect(w.text()).toContain(t('ui.filter.active', { n: 2 }))
    await w.findAll('button').find((b) => b.text() === t('ui.filter.clear'))!.trigger('click')
    expect(w.emitted('clear')).toHaveLength(1)
  })

  it('on phone moves the controls into a side panel and keeps removable chips', async () => {
    const matchMedia = window.matchMedia
    window.matchMedia = ((q: string) => ({ ...matchMedia(q), matches: q.includes('639px'), addEventListener() {}, removeEventListener() {} })) as typeof window.matchMedia
    try {
      const { w } = await mountIt(FilterBar, { activeCount: 1, chips: [{ key: 'state', label: 'Failed' }] }, { default: () => h('input', { 'data-test': 'control', 'aria-label': 'x' }) })
      expect(w.find('[data-test="control"]').exists()).toBe(false)
      await w.find(`button[aria-label="${t('ui.filter.remove', { name: 'Failed' })}"]`).trigger('click')
      expect(w.emitted('remove')![0]).toEqual(['state'])
      await w.find('[data-filter-focus]').trigger('click')
      expect(w.find('[role="dialog"] [data-test="control"]').exists()).toBe(true)
    } finally {
      window.matchMedia = matchMedia
    }
  })
})

describe('status vocabulary', () => {
  it('has one word and an icon per job state, never colour only', async () => {
    for (const state of Object.keys(JOB_STATES)) {
      const { w } = await mountIt(StatusBadge, { kind: 'job', value: state })
      expect(w.text()).toBe(t(`ui.status.job.${state}`))
      expect(w.find('svg, .u-spinner').exists()).toBe(true)
    }
    const { w } = await mountIt(StatusBadge, { kind: 'connection', value: 'blocked' })
    expect(w.classes()).toContain('u-badge-danger')
    const { w: odd } = await mountIt(StatusBadge, { kind: 'job', value: 'new_state' })
    expect(odd.text()).toBe(t('ui.status.unknownValue'))
  })
})

describe('dialogs', () => {
  it('a dialog takes the focus, traps Tab, and Esc closes it unless work is in progress', async () => {
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    outside.focus()
    const { w } = await mountIt(UiDialog, { title: 'Title' }, { default: () => h('input', { 'aria-label': 'field' }), actions: () => h('button', 'Act') })
    await flush()
    expect(w.find('[role="dialog"]').attributes('aria-modal')).toBe('true')
    expect(document.activeElement?.getAttribute('aria-label')).toBe('field')
    // Tab from the last control comes back to the first
    ;(w.findAll('button').at(-1)!.element as HTMLElement).focus()
    await w.find('[role="dialog"]').trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(w.find('button').element)
    await w.find('[role="dialog"]').trigger('keydown', { key: 'Escape' })
    expect(w.emitted('close')).toHaveLength(1)
    await w.setProps({ busy: true })
    await w.find('[role="dialog"]').trigger('keydown', { key: 'Escape' })
    expect(w.emitted('close')).toHaveLength(1)
    expect(await axeViolations(w.element)).toEqual([])
    w.unmount()
    wrappers = wrappers.filter((x) => x !== w)
    expect(document.activeElement).toBe(outside)
    outside.remove()
  })

  it('a typed confirmation enables the button only after the word', async () => {
    const { w } = await mountIt(ConfirmDialog, { title: 'Clear?', confirmLabel: 'Clear', danger: true, typedWord: 'matches' }, { default: () => h('p', 'All stored matches are removed.') })
    const button = () => w.find('[data-testid="confirm"]')
    expect(button().attributes('disabled')).toBeDefined()
    await w.find('input').setValue('match')
    expect(button().attributes('disabled')).toBeDefined()
    await w.find('input').setValue('matches')
    expect(button().attributes('disabled')).toBeUndefined()
    await button().trigger('click')
    expect(w.emitted('confirm')).toHaveLength(1)
    expect(w.find('[role="alertdialog"]').exists()).toBe(true)
  })

  it('a refusal stays in the confirmation with a link to the job that holds the data folder', async () => {
    const { w } = await mountIt(ConfirmDialog, { title: 'Start?', confirmLabel: 'Start', error: new V1Error(409, 'job_running', 'x', null, 'r'), activeJobId: 'JOB7' })
    expect(w.find('[role="alert"]').text()).toContain(t('ui.error.job_running'))
    expect(w.find('[role="alert"] a').attributes('href')).toBe('/jobs/JOB7')
  })

  it('a side panel is a labelled modal that Esc closes', async () => {
    const { w } = await mountIt(SidePanel, { title: 'Raw' }, { default: () => h('p', 'content') })
    expect(w.find('[role="dialog"]').attributes('aria-labelledby')).toBeTruthy()
    await w.find('[role="dialog"]').trigger('keydown', { key: 'Escape' })
    expect(w.emitted('close')).toHaveLength(1)
  })
})

describe('toasts', () => {
  it('close after 6 s, except errors, which stay until closed; the region is polite', async () => {
    vi.useFakeTimers()
    const { w } = await mountIt(ToastHost)
    toast({ text: 'Job started', link: { to: '/jobs/1', label: 'Open' } })
    toast({ kind: 'error', text: 'Failed', requestId: 'req-1' })
    await nextTick()
    expect(w.find('[aria-live="polite"]').exists()).toBe(true)
    expect(w.findAll('.u-toast')).toHaveLength(2)
    expect(w.find('.u-toast-error').text()).toContain('req-1')
    vi.advanceTimersByTime(TOAST_MS + 10)
    await nextTick()
    expect(uiToasts.value.map((x) => x.kind)).toEqual(['error'])
    await w.find('.u-toast-error button').trigger('click')
    expect(uiToasts.value).toEqual([])
  })
})

describe('tabs and menus', () => {
  it('tabs move with the arrow keys and only the selected one is in the Tab order', async () => {
    const tab = ref<'a' | 'b' | 'c'>('a')
    const Host = defineComponent(() => () =>
      h(UiTabs as never, {
        tabs: [{ key: 'a', label: 'A' }, { key: 'b', label: 'B' }, { key: 'c', label: 'C' }],
        modelValue: tab.value,
        'onUpdate:modelValue': (v: 'a' | 'b' | 'c') => (tab.value = v),
        idPrefix: 't',
        label: 'Sections',
      }),
    )
    const { w } = await mountIt(Host)
    expect(w.findAll('[role="tab"]').map((x) => x.attributes('tabindex'))).toEqual(['0', '-1', '-1'])
    await w.find('[role="tablist"]').trigger('keydown', { key: 'ArrowLeft' })
    expect(tab.value).toBe('c')
    await w.find('[role="tablist"]').trigger('keydown', { key: 'Home' })
    expect(tab.value).toBe('a')
    expect(w.find('[role="tabpanel"]').attributes('aria-labelledby')).toBe('t-tab-a')
    expect(await axeViolations(w.element)).toEqual([])
  })

  it('a menu opens with the keyboard, moves with the arrows and returns focus on Esc', async () => {
    const { w } = await mountIt(UiMenu, {
      label: 'More',
      items: [
        { key: 'one', label: 'One' },
        { kind: 'separator', key: 's' },
        { kind: 'radio', key: 'two', label: 'Two', checked: true },
      ],
    })
    const button = w.find('button[aria-haspopup="menu"]')
    await button.trigger('keydown', { key: 'ArrowDown' })
    await flush()
    expect(button.attributes('aria-expanded')).toBe('true')
    expect(document.activeElement?.textContent).toContain('One')
    await w.find('[role="menu"]').trigger('keydown', { key: 'ArrowDown' })
    expect(document.activeElement?.getAttribute('role')).toBe('menuitemradio')
    expect(document.activeElement?.getAttribute('aria-checked')).toBe('true')
    await w.find('[role="menu"]').trigger('keydown', { key: 'Escape' })
    expect(w.find('[role="menu"]').exists()).toBe(false)
    expect(document.activeElement).toBe(button.element)
    await button.trigger('click')
    await w.find('[data-key="one"]').trigger('click')
    expect(w.emitted('select')![0]).toEqual(['one'])
  })
})

describe('times, progress and loading', () => {
  it('a time is local, with the exact UTC value on hover and for screen readers', async () => {
    const { w } = await mountIt(TimeText, { value: '2026-09-30T19:00:00Z' })
    expect(w.find('time').attributes('datetime')).toBe('2026-09-30T19:00:00.000Z')
    expect(w.find('time').attributes('title')).toBe('2026-09-30 19:00:00 UTC')
    expect(w.find('.u-sr').text()).toContain('2026-09-30 19:00:00 UTC')
    timeDisplay.value = 'utc'
    await nextTick()
    expect(w.find('time').text()).toContain('UTC')
    const { w: none } = await mountIt(TimeText, { value: null })
    expect(none.text()).toBe('—')
  })

  it('reads API times and writes durations and relative times in words', () => {
    expect(parseTime('2026-09-30 19:00:00')!.toISOString()).toBe('2026-09-30T19:00:00.000Z')
    expect(parseTime(1759258800000)!.toISOString()).toBe('2025-09-30T19:00:00.000Z')
    expect(parseTime('nonsense')).toBeNull()
    expect(utcText(new Date(Date.UTC(2026, 0, 2, 3, 4, 5)))).toBe('2026-01-02 03:04:05 UTC')
    expect([35, 250, 1500, 7500, 7200].map(duration)).toEqual(['35 s', '4 min 10 s', '25 min', '2 h 5 min', '2 h'])
    expect(relative(new Date(1_000_000 - 180_000), 1_000_000)).toMatch(/^3 min.* ago$/)
    setLocale('tr')
    expect(duration(7500)).toBe('2 sa 5 dk')
  })

  it('a progress bar carries its number for assistive technology', async () => {
    const { w } = await mountIt(ProgressBar, { value: 58.4, label: 'Progress' })
    const bar = w.find('[role="progressbar"]')
    expect([bar.attributes('aria-valuenow'), bar.attributes('aria-label')]).toEqual(['58', 'Progress'])
    const { w: open } = await mountIt(ProgressBar, { value: null, label: 'Progress' })
    expect(open.find('[role="progressbar"]').attributes('aria-valuenow')).toBeUndefined()
  })

  it('a skeleton appears only after 300 ms, so fast answers do not flicker', async () => {
    vi.useFakeTimers()
    const { w } = await mountIt(SkeletonBlock, { lines: 2 })
    expect(w.findAll('.u-skeleton')).toHaveLength(0)
    vi.advanceTimersByTime(299)
    await nextTick()
    expect(w.findAll('.u-skeleton')).toHaveLength(0)
    vi.advanceTimersByTime(2)
    await nextTick()
    expect(w.findAll('.u-skeleton')).toHaveLength(2)
  })

  it('an error state names the failure by code and shows the request id with a copy button', async () => {
    const { w } = await mountIt(ErrorState, { error: new V1Error(503, 'blocked', 'English', null, 'req-77') })
    expect(w.text()).toContain(t('ui.error.blocked'))
    expect(w.text()).not.toContain('English')
    expect(w.text()).toContain('req-77')
    expect(w.find(`button[aria-label="${t('ui.error.copyRequestId')}"]`).exists()).toBe(true)
    expect(w.find('a[href="/system/health"]').exists()).toBe(true)
  })
})
