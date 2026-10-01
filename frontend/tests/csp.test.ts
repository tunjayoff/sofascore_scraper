import { spawnSync } from 'node:child_process'
import { describe, expect, it } from 'vitest'
import viteConfigSource from '../vite.config.ts?raw'
import indexHtml from '../index.html?raw'
import en from '@/locales/en'
import tr from '@/locales/tr'

/**
 * The server's Content-Security-Policy has no 'unsafe-eval' (src/web/security.py). vue-i18n compiles
 * messages with `new Function` unless the build turns on __INTLIFY_JIT_COMPILATION__, so the flag,
 * the marker in index.html that tells the server about it, and the messages themselves must agree.
 */

/** Every leaf key of a locale object as "a.b.c". */
function keys(node: unknown, prefix = '', out: string[] = []): string[] {
  if (typeof node === 'string') out.push(prefix)
  else if (node && typeof node === 'object')
    for (const [k, v] of Object.entries(node)) keys(v, prefix ? `${prefix}.${k}` : k, out)
  return out
}

/** The build-time flag as vite.config.ts sets it (`define`), read from the source. */
const jitFlag = /\b__INTLIFY_JIT_COMPILATION__:\s*true\b/.test(viteConfigSource)

// Runs in a separate Node process where eval / new Function throw, as they do under the CSP
const CHILD = `
globalThis.__INTLIFY_JIT_COMPILATION__ = process.env.JIT === 'true'
const { createI18n } = await import('vue-i18n')
let input = ''
for await (const chunk of process.stdin) input += chunk
const { messages, keys } = JSON.parse(input)
const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', missingWarn: false, fallbackWarn: false, messages })
const args = { count: 3, n: 5, error: 'x', field: 'f', min: 1, max: 2, version: '1', leagues: 1, matches: 2, details: 3 }
let done = 0
for (const lang of Object.keys(messages)) {
  i18n.global.locale.value = lang
  for (const key of keys) {
    const text = i18n.global.t(key, args)
    if (typeof text !== 'string' || text === key) throw new Error('not translated: ' + lang + ' ' + key)
    done++
  }
}
process.stdout.write(String(done))
`

function translateWithoutEval(jit: boolean) {
  return spawnSync(process.execPath, ['--disallow-code-generation-from-strings', '--input-type=module', '-e', CHILD], {
    cwd: process.cwd(),
    env: { ...process.env, JIT: String(jit) },
    input: JSON.stringify({ messages: { en, tr }, keys: keys(en) }),
    encoding: 'utf8',
    timeout: 60000,
  })
}

describe('the app runs without eval (Content-Security-Policy)', () => {
  it('the build turns on vue-i18n JIT compilation and tells the server so', () => {
    expect(jitFlag).toBe(true)
    // src/web/security.py: CSP_MARKER. Without it the server keeps 'unsafe-eval' for old builds.
    expect(indexHtml).toContain('<meta name="sofascore-csp" content="no-eval"')
    // No inline script: script-src is 'self' only
    expect([...indexHtml.matchAll(/<script\b[^>]*>/g)].map((m) => m[0])).toEqual(['<script type="module" src="/src/main.ts">'])
  })

  it('every message of both languages renders where eval and new Function are refused', () => {
    const total = keys(en).length * 2
    expect(total).toBeGreaterThan(400)
    const jit = translateWithoutEval(jitFlag)
    expect(jit.status, jit.stderr).toBe(0)
    expect(jit.stdout).toBe(String(total))
    // The check is real: without the flag the same messages need eval
    const legacy = translateWithoutEval(false)
    expect(legacy.status).not.toBe(0)
    expect(legacy.stderr).toContain('EvalError')
  })
})
