import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'leagues', component: () => import('@/views/LeaguesView.vue') },
    { path: '/download', name: 'download', component: () => import('@/views/DownloadView.vue') },
    { path: '/matches', name: 'matches', component: () => import('@/views/MatchesView.vue') },
    { path: '/match/:id', name: 'match', component: () => import('@/views/MatchView.vue') },
    { path: '/activity', name: 'activity', component: () => import('@/views/ActivityView.vue') },
    { path: '/settings', name: 'settings', component: () => import('@/views/SettingsView.vue') },
    // old addresses
    { path: '/schedule', redirect: '/matches' },
    { path: '/leagues', redirect: '/' },
    { path: '/advanced/leagues', redirect: '/' },
    { path: '/advanced/jobs', redirect: '/activity' },
    { path: '/advanced/stats', redirect: '/settings' },
    { path: '/advanced/settings', redirect: '/settings' },
    { path: '/stats', redirect: '/settings' },
    { path: '/:rest(.*)*', redirect: '/' },
  ],
})

export default router
