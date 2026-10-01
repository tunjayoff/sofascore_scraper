import { ref } from 'vue'
import { ApiError } from '@/api/client'
import { i18n } from '@/i18n'

export type Toast = { id: number; kind: 'ok' | 'error'; text: string }

export const toasts = ref<Toast[]>([])
let seq = 0

export function toast(text: string, kind: Toast['kind'] = 'ok') {
  const id = ++seq
  toasts.value = [...toasts.value, { id, kind, text }]
  setTimeout(() => dismissToast(id), kind === 'error' ? 8000 : 4000)
}

export function dismissToast(id: number) {
  toasts.value = toasts.value.filter((t) => t.id !== id)
}

/** A readable message for any thrown value; a failed fetch means the server is down. */
export function errorText(e: unknown): string {
  const t = i18n.global.t
  if (e instanceof ApiError) {
    // A refusal with a known code gets our own wording instead of the server's English text
    const key = e.code ? `errors.${e.code}` : ''
    if (key && i18n.global.te(key, 'en')) return t(key)
    return e.message || t('common.unknownError')
  }
  if (e instanceof TypeError) return t('common.serverDown')
  return String((e as Error)?.message || e || t('common.unknownError'))
}

export function toastError(e: unknown) {
  toast(errorText(e), 'error')
}
