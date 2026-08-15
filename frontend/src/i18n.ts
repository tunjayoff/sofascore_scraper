import { createI18n } from 'vue-i18n'
import en from '@locales/en.json'
import tr from '@locales/tr.json'

const saved = localStorage.getItem('ss_lang')
const initial = saved === 'en' || saved === 'tr' ? saved : 'tr'

export const i18n = createI18n({
  legacy: false,
  locale: initial,
  fallbackLocale: 'en',
  messages: { en, tr },
})

export function setLocale(lang: 'en' | 'tr') {
  i18n.global.locale.value = lang
  localStorage.setItem('ss_lang', lang)
  document.documentElement.lang = lang
}
