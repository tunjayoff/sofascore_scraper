import { ref } from 'vue'
import type { RouteLocationRaw } from 'vue-router'

/**
 * Toasts of the new app (4.7): short messages, bottom right (bottom on phone). Errors stay until closed,
 * the others close after 6 s. A toast never carries the only copy of important information: the screen
 * that raised it shows the state too.
 */
export type ToastKind = 'ok' | 'info' | 'error'

export type ToastInput = {
  kind?: ToastKind
  text: string
  /** Small second line, e.g. the server's text for an unknown error code. */
  detail?: string | null
  link?: { to: RouteLocationRaw; label: string } | null
  requestId?: string | null
}

export type UiToast = ToastInput & { id: number; kind: ToastKind }

export const TOAST_MS = 6000

export const uiToasts = ref<UiToast[]>([])
let seq = 0
const timers = new Map<number, ReturnType<typeof setTimeout>>()

export function toast(input: ToastInput): number {
  const id = ++seq
  const kind = input.kind ?? 'ok'
  uiToasts.value = [...uiToasts.value, { ...input, kind, id }]
  if (kind !== 'error') timers.set(id, setTimeout(() => dismiss(id), TOAST_MS))
  return id
}

export function dismiss(id: number) {
  const timer = timers.get(id)
  if (timer) clearTimeout(timer)
  timers.delete(id)
  uiToasts.value = uiToasts.value.filter((t) => t.id !== id)
}

export function clearToasts() {
  for (const id of [...timers.keys()]) dismiss(id)
  uiToasts.value = []
}
