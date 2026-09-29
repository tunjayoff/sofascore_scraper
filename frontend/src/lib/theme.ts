import { ref } from 'vue'

export type ThemePref = 'light' | 'dark' | 'system'

const media = window.matchMedia('(prefers-color-scheme: dark)')

function read(): ThemePref {
  try {
    const v = localStorage.getItem('theme')
    return v === 'light' || v === 'dark' ? v : 'system'
  } catch {
    return 'system'
  }
}

export const themePref = ref<ThemePref>(read())

function apply() {
  const dark = themePref.value === 'dark' || (themePref.value === 'system' && media.matches)
  document.documentElement.classList.toggle('dark', dark)
}

export function setTheme(p: ThemePref) {
  themePref.value = p
  try {
    if (p === 'system') localStorage.removeItem('theme')
    else localStorage.setItem('theme', p)
  } catch {
    /* ignore */
  }
  apply()
}

export function initTheme() {
  apply()
  media.addEventListener('change', apply)
}
