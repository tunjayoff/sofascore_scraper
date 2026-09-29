import { createI18n } from 'vue-i18n'
import tr from '@/locales/tr'
import en from '@/locales/en'

export type Lang = 'tr' | 'en'

function savedLang(): Lang {
  try {
    const v = localStorage.getItem('ss_lang')
    return v === 'en' ? 'en' : 'tr'
  } catch {
    return 'tr'
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
