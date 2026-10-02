import { onUnmounted, ref, type Ref } from 'vue'

/** Breakpoints of 05-web-ui.md 4.2. */
export const PHONE = '(max-width: 639px)'
export const BELOW_TABLET = '(max-width: 767px)'
export const BELOW_DESKTOP = '(max-width: 1023px)'

/** A media query as a ref that follows the window; false where the browser has no matchMedia. */
export function useMedia(query: string): Ref<boolean> {
  const list = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query) : null
  const matches = ref(!!list?.matches)
  const update = (e: MediaQueryListEvent) => (matches.value = e.matches)
  list?.addEventListener?.('change', update)
  onUnmounted(() => list?.removeEventListener?.('change', update))
  return matches
}
