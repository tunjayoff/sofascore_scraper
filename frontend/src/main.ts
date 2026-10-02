import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { adoptServerLanguage, i18n } from './i18n'
import { v1 } from './api/v1/client'
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
// The design tokens of the new app (05-web-ui.md 4); after style.css, so they win for the shared names
import './ui/tokens.css'

initTheme()
document.documentElement.lang = String(i18n.global.locale.value)

createApp(App).use(createPinia()).use(router).use(i18n).mount('#app')
// First visit: follow the language set on the server (`display.language`, when a layer above the default
// set it), unless this browser made its own choice (i18n.ts)
void adoptServerLanguage(async () => {
  const setting = (await v1.settings()).settings.find((s) => s.key === 'display.language')
  return setting ? { language: String(setting.value), language_explicit: setting.source !== 'default' } : undefined
})
