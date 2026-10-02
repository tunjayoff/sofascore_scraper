import { ref, watch, type Ref } from 'vue'

/**
 * Choices kept in this browser only (05-web-ui.md 6.16, "This browser"): density, the rail, the time
 * display and the columns of each table. Theme and language have their own modules (lib/theme.ts,
 * i18n.ts). Storage can be missing or full (private windows): every access is guarded and the default
 * applies.
 */

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    /* not kept; the choice holds for this page */
  }
}

function stored<T extends string>(key: string, allowed: readonly T[], fallback: T): Ref<T> {
  const v = read(key)
  const r = ref((allowed as readonly string[]).includes(v ?? '') ? (v as T) : fallback) as Ref<T>
  watch(r, (next) => write(key, next === fallback ? null : next))
  return r
}

export type Density = 'comfortable' | 'compact'
export type TimeDisplay = 'local' | 'utc'

/** Comfortable 44 px rows (default) or compact 32 px rows, desktop only (decision 8). */
export const density = stored<Density>('ssui.density', ['comfortable', 'compact'], 'comfortable')
/** The side rail at 64 px (icons only) instead of 232 px. */
export const railCollapsed = stored<'0' | '1'>('ssui.rail', ['0', '1'], '0')
/** Times in the browser's zone (default, decision 13) or in UTC. */
export const timeDisplay = stored<TimeDisplay>('ssui.time', ['local', 'utc'], 'local')

export function applyDensity() {
  document.documentElement.dataset.density = density.value
}
watch(density, applyDensity)

/** The optional columns a user turned on or off for one table, or null for the table's defaults. */
export function readColumns(tableId: string): Record<string, boolean> | null {
  const raw = read(`ssui.columns.${tableId}`)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
    return Object.fromEntries(Object.entries(parsed).filter(([, v]) => typeof v === 'boolean')) as Record<string, boolean>
  } catch {
    return null
  }
}

export function writeColumns(tableId: string, choice: Record<string, boolean> | null) {
  write(`ssui.columns.${tableId}`, choice ? JSON.stringify(choice) : null)
}
