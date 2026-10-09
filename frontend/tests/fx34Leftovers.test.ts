import { describe, expect, it } from 'vitest'
import en from '@/locales/en'
import tr from '@/locales/tr'

/**
 * FX-34: small leftovers of the docs update after P30. The help text of `fetch.only_finished` names what the
 * setting does today (the league download, the Overview counts and ssc status), not the match lists of the
 * 2.x /api that P30 removed.
 */
describe('fetch.only_finished help text', () => {
  it.each([
    ['en', en.ui.settingHelp.fetch.only_finished],
    ['tr', tr.ui.settingHelp.fetch.only_finished],
  ])('%s names the current effects only', (_lang, text) => {
    expect(text).not.toMatch(/\/api/)
    expect(text).toContain('ssc status')
  })
})
