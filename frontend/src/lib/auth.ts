import { ref } from 'vue'

/**
 * True once the server answered 401 `auth_required`: an access token (SOFASCORE_API_TOKEN) is set on
 * the server and this browser has no session yet. App.vue then shows the token prompt over the app.
 */
export const authNeeded = ref(false)

/** Starts the app over so every store, poll and the status stream load with the new session state. */
export function reloadApp() {
  window.location.reload()
}
