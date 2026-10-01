import { beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import JobCard from '@/components/JobCard.vue'
import { useScrapeStore } from '@/stores/scrape'
import { i18n } from '@/i18n'
import { toasts } from '@/lib/toast'
import { flush, json, mockFetch } from './helpers'

beforeEach(() => {
  setActivePinia(createPinia())
  toasts.value = []
})

describe('JobCard stop', () => {
  it('shows the server error as a toast and keeps the Stop button', async () => {
    mockFetch({ 'POST /api/scrape/cancel': json({ detail: 'No job is running' }, 409) })
    const store = useScrapeStore()
    store.state = { job_id: 'j1', is_running: true, status: 'Running', progress: 10, current_task: '' }
    const w = mount(JobCard, { global: { plugins: [i18n] } })

    const stop = w.findAll('button').find((b) => b.text() === i18n.global.t('job.stop'))!
    await stop.trigger('click')
    await flush()

    expect(toasts.value.map((t) => [t.kind, t.text])).toEqual([['error', 'No job is running']])
    expect(store.state.cancel_requested).toBeFalsy()
    expect(w.findAll('button').some((b) => b.text() === i18n.global.t('job.stop'))).toBe(true)
    w.unmount()
  })
})
