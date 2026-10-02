import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// Read from disk: Vitest replaces imported CSS (also with ?raw) by an empty module
const css = readFileSync(resolve(process.cwd(), 'src/ui/tokens.css'), 'utf8')

/**
 * The colour tokens of 05-web-ui.md 4.5, read from src/ui/tokens.css: the values are the design's, and
 * every pair of text on its ground meets WCAG 2.2 AA (4.5:1 for text, 3:1 for the focus ring and large
 * text), in the light and the dark theme. jsdom has no layout, so axe cannot check contrast; this does.
 */
function block(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`)
  const body = css.slice(start, css.indexOf('}', start))
  return Object.fromEntries([...body.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})/gi)].map((m) => [m[1], m[2].toLowerCase()]))
}

const light = block(':root')
const dark = block('html.dark')

function luminance(hex: string) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function ratio(a: string, b: string) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p)
  return (x + 0.05) / (y + 0.05)
}

/** [text, ground, minimum] as the components use them. */
const PAIRS: [string, string, number][] = [
  ['text', 'bg', 4.5],
  ['text', 'surface', 4.5],
  ['text', 'surface-2', 4.5],
  ['text-2', 'surface', 4.5],
  ['text-2', 'bg', 4.5],
  ['muted', 'bg', 4.5],
  ['muted', 'surface', 4.5],
  ['muted', 'surface-2', 4.5],
  ['accent', 'surface', 4.5],
  ['accent', 'bg', 4.5],
  ['on-accent', 'accent', 4.5],
  ['text', 'accent-soft', 4.5],
  ['ok-fg', 'ok-bg', 4.5],
  ['warn-fg', 'warn-bg', 4.5],
  ['warn-fg', 'surface', 4.5],
  ['warn-fg', 'bg', 4.5],
  ['danger', 'danger-bg', 4.5],
  ['danger', 'surface', 4.5],
  ['danger', 'bg', 4.5],
  ['surface', 'danger', 4.5],
  ['info-fg', 'info-bg', 4.5],
  ['neutral-fg', 'neutral-bg', 4.5],
  ['muted', 'neutral-bg', 4.5],
  ['focus', 'bg', 3],
  ['focus', 'surface', 3],
]

describe('colour tokens (4.5)', () => {
  it('are the values of the design in both themes', () => {
    expect(light).toMatchObject({ bg: '#f4f3ee', surface: '#ffffff', accent: '#1d6b45', danger: '#a12a1f', focus: '#1d6b45', muted: '#5a5e67' })
    expect(dark).toMatchObject({ bg: '#121417', surface: '#1a1d21', accent: '#3fa56e', danger: '#f19a8f', focus: '#52b67f', muted: '#a9adb5' })
    expect(Object.keys(dark).sort()).toEqual(Object.keys(light).sort())
  })

  for (const [theme, tokens] of [
    ['light', light],
    ['dark', dark],
  ] as const) {
    it(`meet WCAG 2.2 AA in the ${theme} theme`, () => {
      const failing = PAIRS.filter(([fg, bg, min]) => ratio(tokens[fg], tokens[bg]) < min).map(
        ([fg, bg, min]) => `${fg} on ${bg}: ${ratio(tokens[fg], tokens[bg]).toFixed(2)} < ${min}`,
      )
      expect(failing).toEqual([])
    })
  }
})
