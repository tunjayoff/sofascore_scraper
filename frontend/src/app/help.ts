import { ref } from 'vue'

/**
 * In-app help (FX-14a): the glossary of the words a newcomer meets, the getting-started steps and a link
 * to the project's README. The panel is opened from the ⋯ menu, the rail's foot, the phone's "More"
 * sheet and the quick search; the (i) tips next to the words read the same texts.
 */
export const HELP_TERMS = ['follow', 'download', 'dataType', 'scoreChanges', 'outputs', 'cleanup', 'live'] as const
export type HelpTerm = (typeof HELP_TERMS)[number]

/** Whether the help panel is open; one panel for the whole app (AppShell renders it). */
export const helpOpen = ref(false)

export function openHelp() {
  helpOpen.value = true
}

const REPO = 'https://github.com/tunjayoff/sofascore_scraper'

/** The README in the reader's language: the Turkish one has its own file. */
export function readmeUrl(locale: string): string {
  return locale === 'tr' ? `${REPO}/blob/main/README.tr.md` : `${REPO}#readme`
}
