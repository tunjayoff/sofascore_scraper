import { createI18n } from 'vue-i18n'
import tr from '@/locales/tr'
import en from '@/locales/en'

export type Lang = 'tr' | 'en'

function storedLang(): Lang | null {
  try {
    const v = localStorage.getItem('ss_lang')
    return v === 'en' || v === 'tr' ? v : null
  } catch {
    return null
  }
}

function savedLang(): Lang {
  return storedLang() ?? 'tr'
}

/**
 * First visit in this browser: follow the language saved on the server (the CLI and the
 * web share APP_LANGUAGE). A choice made in this browser always wins afterwards.
 */
export async function adoptServerLanguage(fetchLang: () => Promise<string | undefined>) {
  if (storedLang()) return
  try {
    const lang = await fetchLang()
    if ((lang === 'en' || lang === 'tr') && lang !== i18n.global.locale.value) {
      i18n.global.locale.value = lang
      document.documentElement.lang = lang
    }
  } catch {
    /* keep the default */
  }
}

export const i18n = createI18n({
  legacy: false,
  locale: savedLang(),
  fallbackLocale: 'en',
  messages: { tr, en },
})

export function setLocale(lang: Lang) {
  i18n.global.locale.value = lang
  document.documentElement.lang = lang
  try {
    localStorage.setItem('ss_lang', lang)
  } catch {
    /* ignore */
  }
}
