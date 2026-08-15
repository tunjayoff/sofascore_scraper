import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('@/views/HomeView.vue') },
    { path: '/matches', name: 'matches', component: () => import('@/views/MatchesView.vue') },
    { path: '/schedule', redirect: '/matches' },
    { path: '/match/:id', name: 'match', component: () => import('@/views/MatchView.vue') },
    {
      path: '/advanced/leagues',
      name: 'leagues',
      component: () => import('@/views/advanced/LeaguesView.vue'),
    },
    {
      path: '/advanced/stats',
      name: 'stats',
      component: () => import('@/views/advanced/StatsView.vue'),
    },
    {
      path: '/advanced/settings',
      name: 'settings',
      component: () => import('@/views/advanced/SettingsView.vue'),
    },
    {
      path: '/advanced/jobs',
      name: 'jobs',
      component: () => import('@/views/advanced/JobsView.vue'),
    },
    { path: '/leagues', redirect: '/advanced/leagues' },
    { path: '/stats', redirect: '/advanced/stats' },
    { path: '/settings', redirect: '/advanced/settings' },
  ],
})

export default router
