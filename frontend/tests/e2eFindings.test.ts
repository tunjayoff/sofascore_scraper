/* eslint-disable vue/one-component-per-file -- small host components of the dialog checks */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, nextTick, ref } from 'vue'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import { openModals, setTeleportDialogs } from '@/ui/modal'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { flush } from './helpers'
import { axeViolations } from './v1'

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
