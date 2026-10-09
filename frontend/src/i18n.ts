import { createI18n } from 'vue-i18n'
import tr from '@/locales/tr'
import en from '@/locales/en'

export type Lang = 'tr' | 'en'

export const SUPPORTED_LANGS: readonly Lang[] = ['en', 'tr']
export const DEFAULT_LANG: Lang = 'en'

/** "tr-TR", "tr_TR.UTF-8", "TR" -> 'tr'; anything we have no translation for -> null. */
export function langOf(tag: unknown): Lang | null {
  const code = String(tag ?? '').trim().toLowerCase().split(/[-_.@]/)[0]
  return (SUPPORTED_LANGS as readonly string[]).includes(code) ? (code as Lang) : null
}

/**
 * The language rule, the same one the CLI, the installers and the launcher follow
 * (sofascore_scraper/language.py): explicit choice > detected language > English.
 * `saved` is the choice made in this browser; `languages` is navigator.languages, in the
 * user's order of preference: the first one we have a translation for wins.
 */
export function resolveLang(saved: unknown, languages: readonly string[] = []): Lang {
  if (saved === 'en' || saved === 'tr') return saved
  for (const tag of languages) {
    const lang = langOf(tag)
    if (lang) return lang
  }
  return DEFAULT_LANG
}

function storedLang(): Lang | null {
  try {
    const v = localStorage.getItem('ss_lang')
    return v === 'en' || v === 'tr' ? v : null
  } catch {
    return null
  }
}

function browserLanguages(): readonly string[] {
  if (typeof navigator === 'undefined') return []
  return navigator.languages?.length ? navigator.languages : [navigator.language]
}

/**
 * First visit in this browser: when a language is set on the server (`display.language`:
 * SOFASCORE_DISPLAY__LANGUAGE, sofascore.toml or the Settings page; the CLI shares it), follow it
 * instead of the browser's language.
 * A choice made in this browser always wins afterwards.
 */
export async function adoptServerLanguage(
  fetchSettings: () => Promise<{ language?: string; language_explicit?: boolean } | undefined>,
) {
  if (storedLang()) return
  try {
    const settings = await fetchSettings()
    const lang = settings?.language_explicit ? langOf(settings.language) : null
    if (lang && lang !== i18n.global.locale.value) {
      i18n.global.locale.value = lang
      document.documentElement.lang = lang
    }
  } catch {
    /* keep the detected language */
  }
}

export const i18n = createI18n({
  legacy: false,
  locale: resolveLang(storedLang(), browserLanguages()),
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
