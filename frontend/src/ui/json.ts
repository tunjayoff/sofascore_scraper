import type { InjectionKey, Ref } from 'vue'

/**
 * Paths and search of the JSON viewer (4.7 JsonViewer). A path is written as in JavaScript:
 * `$.statistics[0].groups`, with `["odd key"]` for keys that are not identifiers.
 */
export const ROOT = '$'

export function childPath(parent: string, key: string | number): string {
  if (typeof key === 'number') return `${parent}[${key}]`
  return /^[A-Za-z_$][\w$]*$/.test(key) ? `${parent}.${key}` : `${parent}[${JSON.stringify(key)}]`
}

export function isContainer(v: unknown): v is Record<string, unknown> | unknown[] {
  return v !== null && typeof v === 'object'
}

export function entriesOf(v: Record<string, unknown> | unknown[]): [string | number, unknown][] {
  return Array.isArray(v) ? v.map((x, i) => [i, x]) : Object.entries(v)
}

/** A one-line preview of a value: `{period, groups}`, `[6]`, `"ALL"`, `12`. */
export function preview(v: unknown): string {
  if (Array.isArray(v)) return `[${v.length}]`
  if (isContainer(v)) {
    const keys = Object.keys(v)
    const shown = keys.slice(0, 4).join(', ')
    return `{${shown}${keys.length > 4 ? ', …' : ''}}`
  }
  if (typeof v === 'string') return JSON.stringify(v.length > 80 ? `${v.slice(0, 80)}…` : v)
  return String(v)
}

export type SearchResult = { matches: string[]; open: Set<string>; truncated: boolean }

/** Paths whose key or plain value contains `query` (case-insensitive), at most `limit`; with their ancestors. */
export function search(value: unknown, query: string, limit = 200): SearchResult {
  const q = query.trim().toLowerCase()
  const matches: string[] = []
  const open = new Set<string>()
  if (!q) return { matches, open, truncated: false }
  let visited = 0
  let truncated = false
  const walk = (v: unknown, path: string, ancestors: string[]) => {
    if (matches.length >= limit || visited > 200000) {
      truncated = true
      return
    }
    visited++
    if (!isContainer(v)) return
    for (const [k, child] of entriesOf(v)) {
      const p = childPath(path, k)
      const hit = String(k).toLowerCase().includes(q) || (!isContainer(child) && String(child).toLowerCase().includes(q))
      if (hit) {
        matches.push(p)
        for (const a of [...ancestors, path]) open.add(a)
        if (matches.length >= limit) {
          truncated = true
          return
        }
      }
      if (isContainer(child)) walk(child, p, [...ancestors, path])
    }
  }
  walk(value, ROOT, [])
  return { matches, open, truncated }
}

/** The value at a path built by childPath (for "Copy value"). */
export function valueAt(root: unknown, path: string): unknown {
  if (path === ROOT) return root
  const parts = [...path.slice(1).matchAll(/\.([A-Za-z_$][\w$]*)|\[(\d+)\]|\[("(?:[^"\\]|\\.)*")\]/g)]
  let v: unknown = root
  for (const m of parts) {
    if (!isContainer(v)) return undefined
    const key: string | number = m[1] ?? (m[2] != null ? Number(m[2]) : JSON.parse(m[3]))
    v = (v as Record<string | number, unknown>)[key]
  }
  return v
}

export type TreeState = {
  open: Ref<Set<string>>
  selected: Ref<string | null>
  hits: Ref<Set<string>>
  toggle: (path: string) => void
  select: (path: string) => void
}

export const TREE: InjectionKey<TreeState> = Symbol('json-tree')

/** Children shown at once under one node; the rest behind "Show more" (large payloads render lazily). */
export const CHUNK = 100
