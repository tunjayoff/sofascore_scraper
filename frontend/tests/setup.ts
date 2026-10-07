import { beforeEach } from 'vitest'
import { setTeleportDialogs } from '@/ui/modal'

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

// Dialogs are shown at the end of <body> (FX-24). The tests read a screen's dialogs where they were opened,
// so they stay in place here; tests/e2eFindings.test.ts checks the real way.
setTeleportDialogs(false)
