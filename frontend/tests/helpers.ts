import { vi } from 'vitest'

export function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

export function json(body: unknown, status = 200, statusText = ''): Response {
  return new Response(JSON.stringify(body), { status, statusText, headers: { 'Content-Type': 'application/json' } })
}

/** Lets pending promise callbacks (and Vue's scheduler) run. */
export async function flush() {
  for (let i = 0; i < 5; i++) await Promise.resolve()
  await new Promise((r) => setTimeout(r, 0))
}

/** Stand-in for EventSource: tests push `update` / `done` events or fail the stream by hand. */
export class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  closed = false
  onerror: ((e: Event) => void) | null = null
  private listeners: Record<string, ((e: Event) => void)[]> = {}

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, fn: (e: Event) => void) {
    ;(this.listeners[type] ||= []).push(fn)
  }

  close() {
    this.closed = true
  }

  emit(type: string, data: unknown) {
    const e = new MessageEvent(type, { data: JSON.stringify(data) })
    for (const fn of this.listeners[type] || []) fn(e)
  }

  fail() {
    this.onerror?.(new Event('error'))
  }

  static reset() {
    FakeEventSource.instances = []
  }

  static get open() {
    return FakeEventSource.instances.filter((s) => !s.closed)
  }
}

/**
 * fetch mock that answers by "METHOD path" with a value or a function of the call.
 * Unknown routes fail the test loudly instead of reaching the network.
 */
export function mockFetch(routes: Record<string, unknown | ((init?: RequestInit, url?: string) => unknown)>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const path = url.split('?')[0]
    const key = `${init?.method || 'GET'} ${path}`
    if (!(key in routes)) throw new Error(`unexpected request: ${key}`)
    const r = routes[key]
    const v = typeof r === 'function' ? await (r as (i?: RequestInit, u?: string) => unknown)(init, url) : r
    return v instanceof Response ? v : json(v)
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

/** Calls to one route of a mockFetch spy. */
export function callsTo(fetchMock: ReturnType<typeof mockFetch>, key: string) {
  return fetchMock.mock.calls.filter(([input, init]) => `${init?.method || 'GET'} ${String(input).split('?')[0]}` === key)
}
