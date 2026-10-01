import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ActivityView from '@/views/ActivityView.vue'
import { jobTitle } from '@/lib/jobLabel'
import { i18n } from '@/i18n'
import { flush, mockFetch } from './helpers'

vi.mock('@/lib/jobLabel', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/lib/jobLabel')>()
  return { ...mod, jobTitle: vi.fn(mod.jobTitle) }
})

beforeEach(() => setActivePinia(createPinia()))

describe('ActivityView', () => {
  it('builds each history row once per render, not once per binding', async () => {
    const jobs = [
      { id: 'a', status: 'Completed', progress: 100, current_task: '', matches_done: 10, matches_total: 10, payload: { mode: 'full', league_id: 17 } },
      { id: 'b', status: 'Failed', progress: 30, current_task: '', matches_failed: 2, payload: { mode: 'details', league_id: 8 } },
    ]
    mockFetch({ 'GET /api/jobs': { jobs } })
    const w = mount(ActivityView, { global: { plugins: [i18n], stubs: { JobCard: true } } })
    await flush()

    expect(w.findAll('.table-row')).toHaveLength(2)
    expect(w.text()).toContain(i18n.global.t('job.unit.details', { done: 10, total: 10 }))
    expect(w.text()).toContain(i18n.global.t('job.failedN', { n: 2 }))
    expect(vi.mocked(jobTitle)).toHaveBeenCalledTimes(jobs.length)
    w.unmount()
  })
})
