import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import en from '@/locales/en'

/**
 * The texts of the new UI (05-web-ui.md 4.10): every key the code names exists, and every key of `ui` is
 * used, by its full name or under a prefix the code completes at run time (`ui.status.job.${state}`).
 * tests/i18n.test.ts checks that both languages have the same keys and placeholders.
 */
function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === 'locales' ? [] : files(path)
    return /\.(ts|vue)$/.test(name) ? [path] : []
  })
}

function flatten(node: unknown, prefix = '', out: string[] = []): string[] {
  if (typeof node === 'string') out.push(prefix)
  else if (node && typeof node === 'object') for (const [k, v] of Object.entries(node)) flatten(v, prefix ? `${prefix}.${k}` : k, out)
  return out
}

const source = files(resolve(process.cwd(), 'src'))
  .map((f) => readFileSync(f, 'utf8'))
  .join('\n')
const keys = flatten({ ui: en.ui })
/** Literal keys in the code: 'ui.a.b' or `ui.a.b` without a placeholder. */
const literal = new Set([...source.matchAll(/['"`](ui\.[A-Za-z0-9_.]+[A-Za-z0-9_])['"`]/g)].map((m) => m[1]))
/** Prefixes completed at run time: `ui.a.${x}` (and `ui.a.${x}.b`). */
const dynamic = [...source.matchAll(/`(ui\.[A-Za-z0-9_.]*)\$\{/g)].map((m) => m[1])

describe('locale keys of the new UI', () => {
  it('every key the code names exists', () => {
    const missing = [...literal].filter((k) => !keys.includes(k) && !keys.some((x) => x.startsWith(`${k}.`)))
    expect(missing).toEqual([])
  })

  it('every key is used', () => {
    const unused = keys.filter((k) => !literal.has(k) && !dynamic.some((p) => k.startsWith(p)))
    expect(unused).toEqual([])
  })
})
