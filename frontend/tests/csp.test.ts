import { spawnSync } from 'node:child_process'
import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import indexHtml from '../index.html?raw'
import en from '@/locales/en'
import tr from '@/locales/tr'

/**
 * The server's Content-Security-Policy has no 'unsafe-eval' (src/web/security.py). vue-i18n (10 and later)
 * always compiles messages without eval, so what has to hold is checked directly: every message renders where
 * eval and new Function are refused, and the production bundle contains neither.
 */

/** Every leaf key of a locale object as "a.b.c". */
function keys(node: unknown, prefix = '', out: string[] = []): string[] {
  if (typeof node === 'string') out.push(prefix)
  else if (node && typeof node === 'object')
    for (const [k, v] of Object.entries(node)) keys(v, prefix ? `${prefix}.${k}` : k, out)
  return out
}

// Runs in a separate Node process where eval / new Function throw, as they do under the CSP
const CHILD = `
// The sandbox is real: code generation from strings is refused here
let refused = false
try { new Function('return 1') } catch (e) { refused = e instanceof EvalError }
if (!refused) throw new Error('code generation from strings is allowed in this process')
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

/** `new Function` or a call of `eval` in built JavaScript. */
const CODEGEN = /\bnew\s+Function\b|(?<![\w$.])eval\s*\(/

function builtScripts(dir: string): string[] {
  return readdirSync(dir, { recursive: true, encoding: 'utf8' })
    .filter((f) => f.endsWith('.js'))
    .map((f) => join(dir, f))
}

describe('the app runs without eval (Content-Security-Policy)', () => {
  it('index.html tells the server and has no inline script', () => {
    // src/web/security.py: CSP_MARKER. Without it the server keeps 'unsafe-eval' for old builds.
    expect(indexHtml).toContain('<meta name="sofascore-csp" content="no-eval"')
    // No inline script: script-src is 'self' only
    expect([...indexHtml.matchAll(/<script\b[^>]*>/g)].map((m) => m[0])).toEqual(['<script type="module" src="/src/main.ts">'])
  })

  it('every message of both languages renders where eval and new Function are refused', () => {
    const total = keys(en).length * 2
    expect(total).toBeGreaterThan(400)
    const run = spawnSync(process.execPath, ['--disallow-code-generation-from-strings', '--input-type=module', '-e', CHILD], {
      cwd: process.cwd(),
      input: JSON.stringify({ messages: { en, tr }, keys: keys(en) }),
      encoding: 'utf8',
      timeout: 60000,
    })
    expect(run.status, run.stderr).toBe(0)
    expect(run.stdout).toBe(String(total))
  })

  it('the production build contains no new Function and no eval', () => {
    expect(CODEGEN.test('return new Function("a", body)')).toBe(true)
    expect(CODEGEN.test('x=eval(s)')).toBe(true)
    expect(CODEGEN.test('retrieval(x); obj.eval(s); medieval (y)')).toBe(false)
    const out = mkdtempSync(join(tmpdir(), 'csp-dist-'))
    try {
      const vite = resolve(process.cwd(), 'node_modules/vite/bin/vite.js')
      const run = spawnSync(process.execPath, [vite, 'build', '--outDir', out, '--emptyOutDir', '--logLevel', 'error'], {
        cwd: process.cwd(),
        encoding: 'utf8',
        timeout: 120000,
      })
      expect(run.status, run.stderr).toBe(0)
      const scripts = builtScripts(out)
      expect(scripts.length).toBeGreaterThan(5)
      const hits = scripts.filter((f) => CODEGEN.test(readFileSync(f, 'utf8'))).map((f) => f.slice(out.length + 1))
      expect(hits).toEqual([])
    } finally {
      rmSync(out, { recursive: true, force: true })
    }
  }, 150000)
})
