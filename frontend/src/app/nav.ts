import type { UiIconName } from '@/ui/UiIcon.vue'

/** The screens of the new app and their place in the menu (05-web-ui.md 3.1, 3.2). */
export type NavGroup = 'start' | 'data' | 'operations' | 'system'

export type NavItem = {
  key: string
  to: string
  icon: UiIconName
  group: NavGroup
  /** Hidden from the menu (a screen whose route is missing); the address still answers. */
  hidden?: boolean
  /** In the phone's bottom bar (3.3); the others are under "More". */
  bottom?: boolean
  /** g + this key opens the screen (4.9). */
  hotkey?: string
}

export const NAV: readonly NavItem[] = [
  { key: 'overview', to: '/', icon: 'home', group: 'start', bottom: true, hotkey: 'o' },
  { key: 'follows', to: '/follows', icon: 'follows', group: 'data', bottom: true, hotkey: 'f' },
  { key: 'events', to: '/events', icon: 'events', group: 'data', bottom: true, hotkey: 'e' },
  { key: 'corrections', to: '/corrections', icon: 'corrections', group: 'data' },
  { key: 'jobs', to: '/jobs', icon: 'jobs', group: 'operations', bottom: true, hotkey: 'j' },
  { key: 'exports', to: '/exports', icon: 'exports', group: 'operations' },
  { key: 'backups', to: '/backups', icon: 'backups', group: 'operations' },
  { key: 'maintenance', to: '/maintenance', icon: 'maintenance', group: 'operations' },
  { key: 'health', to: '/system/health', icon: 'health', group: 'system' },
  { key: 'sinks', to: '/system/sinks', icon: 'sinks', group: 'system' },
  { key: 'logs', to: '/system/logs', icon: 'logs', group: 'system' },
  { key: 'settings', to: '/settings', icon: 'settings', group: 'system', hotkey: 's' },
]

export const GROUPS: readonly NavGroup[] = ['start', 'data', 'operations', 'system']

export function navItem(key: string): NavItem | undefined {
  return NAV.find((n) => n.key === key)
}

/** The menu entry a path belongs to: the longest matching prefix ("/jobs/01J…" → jobs). */
export function navFor(path: string): NavItem | undefined {
  if (path === '/' || path === '') return NAV[0]
  return NAV.filter((n) => n.to !== '/' && (path === n.to || path.startsWith(`${n.to}/`))).sort((a, b) => b.to.length - a.to.length)[0]
}
