import { beforeEach } from 'vitest'

// jsdom has no matchMedia; lib/theme.ts reads it when imported. Light scheme, no changes.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList
}

// The answers kept by the suggestions while typing (FX-20) live as long as the page: each test starts
// without them. Imported late, so lib/theme.ts finds matchMedia above.
beforeEach(async () => (await import('@/app/suggest')).clearSuggestCache())
