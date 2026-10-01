import { describe, expect, it } from 'vitest'
import { latestOnly } from '@/lib/latest'

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

describe('latestOnly', () => {
  it('applies only the newest of two out-of-order responses', async () => {
    const loads = latestOnly()
    const shown: string[] = []
    async function load(response: Promise<string>) {
      const token = loads.next()
      const value = await response
      if (loads.isCurrent(token)) shown.push(value)
    }
    const first = deferred<string>()
    const second = deferred<string>()
    const a = load(first.promise)
    const b = load(second.promise)
    second.resolve('second')
    await b
    first.resolve('first') // the older call answers last
    await a
    expect(shown).toEqual(['second'])
  })

  it('keeps separate counters per instance', () => {
    const a = latestOnly()
    const b = latestOnly()
    const ta = a.next()
    b.next()
    expect(a.isCurrent(ta)).toBe(true)
  })
})
