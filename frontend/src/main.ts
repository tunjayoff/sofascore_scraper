import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { adoptServerLanguage, i18n } from './i18n'
import { api } from './api/client'
import { initTheme } from './lib/theme'
// Fonts are bundled (no Google Fonts request): the app works offline and leaks nothing
import '@fontsource/geist/400.css'
import '@fontsource/geist/500.css'
import '@fontsource/geist/600.css'
import '@fontsource/geist/700.css'
import '@fontsource/geist-mono/400.css'
import '@fontsource/geist-mono/500.css'
import '@fontsource/geist-mono/600.css'
import './style.css'

initTheme()
document.documentElement.lang = String(i18n.global.locale.value)

createApp(App).use(createPinia()).use(router).use(i18n).mount('#app')
void adoptServerLanguage(() => api.settings())
