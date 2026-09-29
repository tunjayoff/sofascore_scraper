import { defineStore } from 'pinia'
import { ref } from 'vue'
import { SPORTS, type SportKey } from '@/lib/sport'

export type SportFilter = SportKey | 'all'

function read(): SportFilter {
  try {
    const v = localStorage.getItem('ss_sport')
    return (SPORTS as string[]).includes(String(v)) ? (v as SportKey) : 'all'
  } catch {
    return 'all'
  }
}

/** The sport picked in the sidebar; every page shows leagues and matches of this sport only. */
export const useSportStore = defineStore('sport', () => {
  const current = ref<SportFilter>(read())

  function set(s: SportFilter) {
    current.value = s
    try {
      localStorage.setItem('ss_sport', s)
    } catch {
      /* per-browser convenience only */
    }
  }

  return { current, set }
})
