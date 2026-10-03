import { createRouter, createWebHistory, type LocationQuery, type LocationQueryRaw, type RouteRecordRaw } from 'vue-router'
import AppShell from '@/app/AppShell.vue'

/**
 * The URL map of the new app (05-web-ui.md 3.2) and the classic views beside it. Every screen of the map
 * exists. The classic views stay reachable under `/classic` (menu "⋯ → Classic interface") while one of
 * their functions still has no new home; today's addresses lead to the new screens, for one release.
 */
const shell: RouteRecordRaw[] = [
  { path: '', name: 'overview', component: () => import('@/screens/OverviewScreen.vue') },
  { path: 'follows', name: 'follows', component: () => import('@/screens/follows/FollowsScreen.vue') },
  { path: 'follows/new', name: 'follow-new', component: () => import('@/screens/follows/FollowEditorScreen.vue') },
  { path: 'follows/:kind(tournament|team|player|event)/:id(\\d+)', name: 'follow', component: () => import('@/screens/follows/FollowDetailScreen.vue') },
  { path: 'follows/:kind(tournament|team|player|event)/:id(\\d+)/edit', name: 'follow-edit', component: () => import('@/screens/follows/FollowEditorScreen.vue') },
  { path: 'events', name: 'events', component: () => import('@/screens/events/EventsScreen.vue') },
  { path: 'events/:id(\\d+)', name: 'event', component: () => import('@/screens/events/EventDetailScreen.vue') },
  { path: 'events/:id(\\d+)/raw/:key/:sub?', name: 'event-raw', component: () => import('@/screens/events/RawScreen.vue') },
  { path: 'corrections', name: 'corrections', component: () => import('@/screens/CorrectionsScreen.vue') },
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

/** The filters of the classic match list in the names of Events (league_id → tournament …). */
function eventsQuery(r: { query: LocationQuery }): LocationQueryRaw {
  const q: LocationQueryRaw = {}
  if (r.query.league_id) q.tournament = r.query.league_id
  if (r.query.season_id) q.season = r.query.season_id
  if (r.query.sort === 'asc') q.sort = 'asc'
  if (r.query.details === 'missing') q.has = 'missing'
  if (r.query.details === 'present') q.has = 'details'
  return q
}

export const routes: RouteRecordRaw[] = [
  { path: '/', component: AppShell, children: shell },
  { path: '/classic', component: () => import('@/classic/ClassicLayout.vue'), children: classic },
  // today's addresses (3.2): their nearest new screen, for one release
  { path: '/download', redirect: '/follows' },
  { path: '/matches', redirect: (r) => ({ path: '/events', query: eventsQuery(r) }) },
  { path: '/match/:id', redirect: (r) => ({ path: `/events/${String(r.params.id)}` }) },
  { path: '/activity', redirect: '/jobs' },
  { path: '/schedule', redirect: (r) => ({ path: '/events', query: eventsQuery(r) }) },
  { path: '/leagues', redirect: '/follows' },
  { path: '/advanced/leagues', redirect: '/follows' },
  { path: '/advanced/jobs', redirect: '/jobs' },
  { path: '/advanced/stats', redirect: '/' },
  { path: '/advanced/settings', redirect: '/settings' },
  { path: '/stats', redirect: '/' },
  { path: '/:rest(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: (_to, _from, saved) => saved ?? { top: 0 },
})

export default router
