import { createRouter, createWebHistory, type RouteRecordRaw, type RouteRecordRedirectOption } from 'vue-router'
import AppShell from '@/app/AppShell.vue'

/**
 * The URL map of the new app (05-web-ui.md 3.2) and the classic views beside it.
 *
 * The new app is built part by part (decision 22). Screens whose API routes come with P21 answer with a
 * placeholder that says what is coming (`meta.screen`); their sub-paths too, so a bookmark never breaks.
 * Today's views stay reachable under `/classic` (menu "⋯ → Classic interface") until FE-2b replaces them;
 * their old addresses lead to the new screen where one exists and to the classic view otherwise.
 */
const planned = (screen: string, path: string): RouteRecordRaw => ({
  path,
  component: () => import('@/screens/PlannedScreen.vue'),
  meta: { screen },
})

const shell: RouteRecordRaw[] = [
  { path: '', name: 'overview', component: () => import('@/screens/OverviewScreen.vue') },
  planned('follows', 'follows'),
  planned('follows', 'follows/new'),
  planned('follows', 'follows/:kind/:id'),
  planned('follows', 'follows/:kind/:id/edit'),
  planned('events', 'events'),
  planned('events', 'events/:id'),
  planned('events', 'events/:id/raw/:key/:sub?'),
  planned('corrections', 'corrections'),
  { path: 'jobs', name: 'jobs', component: () => import('@/screens/jobs/JobsScreen.vue') },
  { path: 'jobs/:id', name: 'job', component: () => import('@/screens/jobs/JobDetailScreen.vue') },
  { path: 'exports', name: 'exports', component: () => import('@/screens/exports/ExportsScreen.vue') },
  { path: 'backups', name: 'backups', component: () => import('@/screens/backups/BackupsScreen.vue') },
  { path: 'maintenance', name: 'maintenance', component: () => import('@/screens/MaintenanceScreen.vue') },
  { path: 'system/health', name: 'health', component: () => import('@/screens/HealthScreen.vue') },
  { path: 'system/sinks', name: 'sinks', component: () => import('@/screens/SinksScreen.vue') },
  { path: 'system/logs', name: 'logs', component: () => import('@/screens/LogsScreen.vue') },
  { path: 'settings', name: 'settings', component: () => import('@/screens/settings/SettingsScreen.vue') },
]

// Every part of the design system in its states, in development builds only (4)
if (import.meta.env.DEV) shell.push({ path: 'dev/kitchen-sink', component: () => import('@/screens/KitchenSink.vue') })

const classic: RouteRecordRaw[] = [
  { path: '', name: 'classic-leagues', component: () => import('@/views/LeaguesView.vue') },
  { path: 'download', name: 'classic-download', component: () => import('@/views/DownloadView.vue') },
  { path: 'matches', name: 'classic-matches', component: () => import('@/views/MatchesView.vue') },
  { path: 'match/:id', name: 'classic-match', component: () => import('@/views/MatchView.vue') },
  { path: 'activity', name: 'classic-activity', component: () => import('@/views/ActivityView.vue') },
  { path: 'settings', name: 'classic-settings', component: () => import('@/views/SettingsView.vue') },
]

/** An old address to its new place, keeping the query (filters of the old views). */
const to =
  (path: string): RouteRecordRedirectOption =>
  (r) => ({ path, query: r.query, hash: r.hash })

export const routes: RouteRecordRaw[] = [
  { path: '/', component: AppShell, children: shell },
  { path: '/classic', component: () => import('@/classic/ClassicLayout.vue'), children: classic },
  // today's addresses (3.2): the new screen where it exists, else the classic view, for one release
  { path: '/download', redirect: to('/classic/download') },
  { path: '/matches', redirect: to('/classic/matches') },
  { path: '/match/:id', redirect: (r) => ({ path: `/classic/match/${String(r.params.id)}`, query: r.query }) },
  { path: '/activity', redirect: '/jobs' },
  { path: '/schedule', redirect: to('/classic/matches') },
  { path: '/leagues', redirect: '/classic' },
  { path: '/advanced/leagues', redirect: '/classic' },
  { path: '/advanced/jobs', redirect: '/jobs' },
  { path: '/advanced/stats', redirect: '/classic/settings' },
  { path: '/advanced/settings', redirect: '/settings' },
  { path: '/stats', redirect: '/classic/settings' },
  { path: '/:rest(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: (_to, _from, saved) => saved ?? { top: 0 },
})

export default router
