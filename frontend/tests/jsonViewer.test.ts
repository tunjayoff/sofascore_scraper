import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import JsonViewer from '@/ui/JsonViewer.vue'
import { CHUNK, childPath, search, valueAt } from '@/ui/json'
import { i18n, setLocale } from '@/i18n'
import { clearToasts } from '@/ui/toast'
import { flush } from './helpers'
import { axeViolations } from './v1'

/** The raw view's JSON tree (4.7 JsonViewer, decision 7): paths, search, copy, text, lazy children. */
const t = i18n.global.t
let w: VueWrapper

beforeEach(() => {
  setLocale('en')
  clearToasts()
})
afterEach(() => w?.unmount())

const PAYLOAD = { statistics: [{ period: 'ALL', groups: [{ groupName: 'Match overview', statisticsItems: [{ name: 'Ball possession', home: '41%' }] }] }], 'odd key': true }

describe('paths and search', () => {
  it('writes paths as in JavaScript and reads the value back', () => {
    expect(childPath('$', 'statistics')).toBe('$.statistics')
    expect(childPath('$.statistics', 0)).toBe('$.statistics[0]')
    expect(childPath('$', 'odd key')).toBe('$["odd key"]')
    expect(valueAt(PAYLOAD, '$.statistics[0].period')).toBe('ALL')
    expect(valueAt(PAYLOAD, '$["odd key"]')).toBe(true)
  })

  it('finds keys and values, case-insensitive, and opens their ancestors', () => {
    const r = search(PAYLOAD, 'POSSESSION')
    expect(r.matches).toEqual(['$.statistics[0].groups[0].statisticsItems[0].name'])
    expect([...r.open]).toEqual(expect.arrayContaining(['$', '$.statistics', '$.statistics[0].groups[0].statisticsItems[0]']))
    expect(search(PAYLOAD, '').matches).toEqual([])
  })
})

describe('JsonViewer', () => {
  it('shows a tree, marks the search hits, copies the path of a node and switches to text', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    w = mount(JsonViewer, { props: { text: JSON.stringify(PAYLOAD), fileName: 'x.json', downloadUrl: '/api/v1/events/1/raw' }, global: { plugins: [i18n] }, attachTo: document.body })
    await flush()
    expect(w.find('[data-path="$.statistics"]').exists()).toBe(true)
    await w.find('input[type="search"]').setValue('ball')
    await flush()
    expect(w.find('.u-json-row.is-hit').text()).toContain('Ball possession')
    await w.find('[data-path="$.statistics[0].period"] .u-json-label').trigger('click')
    expect(w.find('.u-json-selected code').text()).toBe('$.statistics[0].period')
    await w.findAll('.u-json-selected button').find((b) => b.text() === t('ui.json.copyValue'))!.trigger('click')
    await flush()
    expect(writeText).toHaveBeenLastCalledWith('ALL')
    await w.find('[data-testid="json-copy"]').trigger('click')
    await flush()
    expect(writeText).toHaveBeenLastCalledWith(JSON.stringify(PAYLOAD))
    expect(w.find('[data-testid="json-download"]').attributes('href')).toBe('/api/v1/events/1/raw')
    expect(await axeViolations(w.element)).toEqual([])
    await w.findAll('.u-seg button').find((b) => b.text() === t('ui.json.text'))!.trigger('click')
    expect(w.find('pre').text()).toContain('"period": "ALL"')
  })

  it('renders a long array a hundred at a time, and a text that is not JSON as text', async () => {
    w = mount(JsonViewer, { props: { text: JSON.stringify({ list: Array.from({ length: 250 }, (_, i) => i) }), fileName: 'x.json' }, global: { plugins: [i18n] } })
    await flush()
    const list = w.find('[data-path="$.list"]')
    expect(list.findAll('.u-json-children > li[data-path]')).toHaveLength(CHUNK)
    await list.find('.u-json-more button').trigger('click')
    expect(list.findAll('.u-json-children > li[data-path]')).toHaveLength(2 * CHUNK)
    w.unmount()
    w = mount(JsonViewer, { props: { text: 'not json', fileName: 'x.json' }, global: { plugins: [i18n] } })
    expect(w.text()).toContain(t('ui.json.notJson'))
    expect(w.find('pre').text()).toBe('not json')
  })
})
