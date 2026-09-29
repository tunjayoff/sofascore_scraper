import { nextTick } from 'vue'

/**
 * WAI-ARIA tabs keyboard support: ←/→ move between tabs, Home/End jump to the ends.
 * Tab buttons need `id="${prefix}-tab-${key}"` so focus can follow the selection.
 */
export function onTabKeydown<K extends string>(e: KeyboardEvent, keys: readonly K[], current: K, select: (k: K) => void, prefix: string) {
  const i = keys.indexOf(current)
  let next: number
  if (e.key === 'ArrowRight') next = (i + 1) % keys.length
  else if (e.key === 'ArrowLeft') next = (i - 1 + keys.length) % keys.length
  else if (e.key === 'Home') next = 0
  else if (e.key === 'End') next = keys.length - 1
  else return
  e.preventDefault()
  const key = keys[next]
  select(key)
  void nextTick(() => document.getElementById(`${prefix}-tab-${key}`)?.focus())
}

/** Keep Tab/Shift+Tab inside a modal dialog. */
export function trapFocus(e: KeyboardEvent, root: HTMLElement | null) {
  if (e.key !== 'Tab' || !root) return
  const items = Array.from(
    root.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'),
  ).filter((el) => el.offsetParent !== null)
  if (!items.length) return
  const first = items[0]
  const last = items[items.length - 1]
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault()
    last.focus()
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault()
    first.focus()
  }
}
