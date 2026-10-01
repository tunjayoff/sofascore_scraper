import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useScrapeStore } from '@/stores/scrape'
import type { ScrapeState } from '@/api/client'
import { FakeEventSource, callsTo, deferred, flush, json, mockFetch } from './helpers'

const running: ScrapeState = { job_id: 'j1', is_running: true, status: 'Running', progress: 40, current_task: '' }
const finished: ScrapeState = { job_id: 'j1', is_running: false, status: 'Completed', progress: 100, current_task: '' }

// init() adds window listeners per store; remove them so one test's store can't answer another's events
let added: [string, EventListenerOrEventListenerObject][] = []
const addEventListener = window.addEventListener.bind(window)

beforeEach(() => {
  setActivePinia(createPinia())
  FakeEventSource.reset()
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.spyOn(window, 'addEventListener').mockImplementation((type: string, fn: EventListenerOrEventListenerObject) => {
    added.push([type, fn])
    addEventListener(type, fn)
  })
})

afterEach(() => {
  for (const [type, fn] of added) window.removeEventListener(type, fn)
  added = []
  vi.useRealTimers()
})

function pageEvent(type: 'pagehide' | 'pageshow', persisted: boolean) {
  const e = new Event(type)
  Object.defineProperty(e, 'persisted', { value: persisted })
  window.dispatchEvent(e)
}

describe('scrape store', () => {
  it('ignores a poll response that arrives after a newer SSE event', async () => {
    const stalePoll = deferred<Response>()
    mockFetch({ 'GET /api/scrape/status': () => stalePoll.promise, 'POST /api/scrape/cancel': { status: 'ok' } })
    const store = useScrapeStore()
    const finishedCalls = vi.fn()
    store.onFinished(finishedCalls)
    store.state = { ...running }

    const poll = store.check() // in flight, answers late
    await store.cancel() // opens the stream
    FakeEventSource.instances[0].emit('done', finished)
    stalePoll.resolve(json(running))
    await poll

    expect(store.state.status).toBe('Completed')
    expect(store.isRunning).toBe(false)
    expect(finishedCalls).toHaveBeenCalledTimes(1)
  })

  it('stays on polling when the stream fails before its first message (e.g. 501)', async () => {
    vi.useFakeTimers()
    let answer = running
    const fetchMock = mockFetch({ 'GET /api/scrape/status': () => answer })
    const store = useScrapeStore()
    const finishedCalls = vi.fn()
    store.onFinished(finishedCalls)

    store.init()
    await vi.advanceTimersByTimeAsync(0)
    expect(FakeEventSource.instances).toHaveLength(1)
    FakeEventSource.instances[0].fail()
    expect(FakeEventSource.open).toHaveLength(0)

    await vi.advanceTimersByTimeAsync(5000)
    expect(callsTo(fetchMock, 'GET /api/scrape/status')).toHaveLength(2)
    expect(FakeEventSource.instances).toHaveLength(1) // no new stream attempt

    answer = finished
    await vi.advanceTimersByTimeAsync(5000)
    expect(store.state.status).toBe('Completed')
    expect(finishedCalls).toHaveBeenCalledTimes(1)
  })

  it('reconnects the stream after it drops mid-job (it worked before)', async () => {
    mockFetch({ 'GET /api/scrape/status': running })
    const store = useScrapeStore()
    store.init()
    await flush()
    FakeEventSource.instances[0].emit('update', running)
    FakeEventSource.instances[0].fail()
    await store.check()
    expect(FakeEventSource.instances).toHaveLength(2)
  })

  it('cancel marks the job as stopping once the server accepts it', async () => {
    const cancel = deferred<Response>()
    mockFetch({ 'POST /api/scrape/cancel': () => cancel.promise })
    const store = useScrapeStore()
    store.state = { ...running }

    const p = store.cancel()
    expect(store.state.cancel_requested).toBeFalsy() // not before the server answered
    cancel.resolve(json({ status: 'ok' }))
    await p
    expect(store.state.cancel_requested).toBe(true)
    expect(FakeEventSource.open).toHaveLength(1)
  })

  it('cancel leaves the state alone and rejects when the server refuses', async () => {
    mockFetch({ 'POST /api/scrape/cancel': json({ detail: 'No job is running' }, 409) })
    const store = useScrapeStore()
    store.state = { ...running }
    await expect(store.cancel()).rejects.toThrow('No job is running')
    expect(store.state.cancel_requested).toBeFalsy()
  })

  it('refreshes the state when the page comes back from the back/forward cache', async () => {
    vi.useFakeTimers()
    let answer = running
    const fetchMock = mockFetch({ 'GET /api/scrape/status': () => answer })
    const store = useScrapeStore()
    store.init()
    await vi.advanceTimersByTimeAsync(0)
    FakeEventSource.instances[0].fail() // keep the test on polling

    pageEvent('pagehide', true)
    await vi.advanceTimersByTimeAsync(20000)
    const beforeShow = callsTo(fetchMock, 'GET /api/scrape/status').length
    expect(beforeShow).toBe(1) // stopped while hidden

    answer = finished
    pageEvent('pageshow', false) // a normal load re-runs init; nothing to resume here
    await vi.advanceTimersByTimeAsync(0)
    expect(callsTo(fetchMock, 'GET /api/scrape/status')).toHaveLength(beforeShow)

    pageEvent('pageshow', true)
    await vi.advanceTimersByTimeAsync(0)
    expect(callsTo(fetchMock, 'GET /api/scrape/status')).toHaveLength(beforeShow + 1)
    expect(store.state.status).toBe('Completed')
    await vi.advanceTimersByTimeAsync(5000)
    expect(callsTo(fetchMock, 'GET /api/scrape/status')).toHaveLength(beforeShow + 2) // polling resumed
  })
})
