import { ref } from 'vue'

/**
 * Version of the running backend, as reported by GET /health (the server reads it from
 * pyproject.toml). The sidebar's health ping fills it, so showing it costs no extra request.
 * null until the first successful ping.
 */
export const appVersion = ref<string | null>(null)

/** Takes a /health body of unknown shape; keeps the last known version when it has none. */
export function noteHealth(body: unknown) {
  const v = (body as { version?: unknown } | null)?.version
  if (typeof v === 'string' && v.trim()) appVersion.value = v.trim()
}
