import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { adoptServerLanguage, DEFAULT_LANG, i18n, langOf, resolveLang, setLocale } from '@/i18n'
import en from '@/locales/en'
import tr from '@/locales/tr'

/** Every leaf of a locale object as "a.b.c" -> text. */
function flatten(node: unknown, prefix = '', out: Record<string, string> = {}): Record<string, string> {
  if (typeof node === 'string') out[prefix] = node
  else if (node && typeof node === 'object')
    for (const [k, v] of Object.entries(node)) flatten(v, prefix ? `${prefix}.${k}` : k, out)
  return out
}

const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()

describe('language rule: explicit choice > detected language > English', () => {
  it('uses English when nothing is saved and the browser language is not supported', () => {
    expect(DEFAULT_LANG).toBe('en')
    expect(resolveLang(null, [])).toBe('en')
    expect(resolveLang(null, ['de-DE', 'fr'])).toBe('en')
    expect(resolveLang(undefined)).toBe('en')
  })

  it('picks Turkish for a Turkish browser', () => {
    expect(resolveLang(null, ['tr-TR', 'tr', 'en-US', 'en'])).toBe('tr')
    expect(resolveLang(null, ['tr'])).toBe('tr')
    expect(resolveLang(null, ['TR-tr'])).toBe('tr')
  })

  it('takes the first language of the browser list that it has a translation for', () => {
    expect(resolveLang(null, ['en-US', 'tr'])).toBe('en')
    expect(resolveLang(null, ['de', 'tr', 'en'])).toBe('tr')
    expect(resolveLang(null, ['de', 'en-GB', 'tr'])).toBe('en')
  })

  it('lets the saved choice win over the browser language', () => {
    expect(resolveLang('en', ['tr-TR'])).toBe('en')
    expect(resolveLang('tr', ['en-US'])).toBe('tr')
    // an unknown saved value is not a choice
    expect(resolveLang('de', ['tr-TR'])).toBe('tr')
    expect(resolveLang('', ['en-US'])).toBe('en')
  })

  it('reads language tags in the browser and the POSIX spelling', () => {
    expect(['tr', 'tr-TR', 'tr_TR.UTF-8', ' TR ', 'en', 'en-GB'].map(langOf)).toEqual(['tr', 'tr', 'tr', 'tr', 'en', 'en'])
    expect(['', null, undefined, 'de-DE', 'trk', 'C'].map(langOf)).toEqual([null, null, null, null, null, null])
  })

  it('starts in the language the rule gives for this browser', () => {
    // jsdom reports en-US and nothing was saved before the module loaded
    expect(resolveLang(null, navigator.languages)).toBe('en')
  })
})

describe('first visit and the language set on the server', () => {
  beforeEach(() => {
    localStorage.clear()
    i18n.global.locale.value = 'en'
  })
  afterEach(() => {
    localStorage.clear()
    setLocale('tr')
    localStorage.clear()
  })

  it('follows APP_LANGUAGE when it is set', async () => {
    await adoptServerLanguage(async () => ({ language: 'tr', language_explicit: true }))
    expect(i18n.global.locale.value).toBe('tr')
    expect(document.documentElement.lang).toBe('tr')
    // following the server is not a choice made in this browser
    expect(localStorage.getItem('ss_lang')).toBeNull()
  })

  it('keeps the browser language when the server only reports its own system language', async () => {
    await adoptServerLanguage(async () => ({ language: 'tr', language_explicit: false }))
    expect(i18n.global.locale.value).toBe('en')
    await adoptServerLanguage(async () => ({ language: 'tr' }))
    expect(i18n.global.locale.value).toBe('en')
  })

  it('never overrides the choice saved in this browser', async () => {
    setLocale('en')
    expect(localStorage.getItem('ss_lang')).toBe('en')
    let asked = false
    await adoptServerLanguage(async () => {
      asked = true
      return { language: 'tr', language_explicit: true }
    })
    expect(asked).toBe(false)
    expect(i18n.global.locale.value).toBe('en')
  })

  it('keeps the current language when the server cannot be reached or answers nonsense', async () => {
    await adoptServerLanguage(async () => {
      throw new Error('offline')
    })
    expect(i18n.global.locale.value).toBe('en')
    await adoptServerLanguage(async () => ({ language: 'de', language_explicit: true }))
    expect(i18n.global.locale.value).toBe('en')
  })
})

describe('locale files', () => {
  const enFlat = flatten(en)
  const trFlat = flatten(tr)

  it('have the same keys in both languages', () => {
    expect(Object.keys(enFlat).length).toBeGreaterThan(200)
    expect(Object.keys(enFlat).sort()).toEqual(Object.keys(trFlat).sort())
  })

  it('have text and the same placeholders for every key', () => {
    for (const key of Object.keys(enFlat)) {
      expect(enFlat[key].trim(), key).not.toBe('')
      expect(trFlat[key].trim(), key).not.toBe('')
      expect(placeholders(enFlat[key]), key).toEqual(placeholders(trFlat[key]))
    }
  })
})
