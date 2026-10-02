import { ref } from 'vue'

/**
 * The browser's session with a server that has an access token (05-web-ui.md 5.3, 6.15). `authNeeded`
 * (lib/auth.ts, shared with the classic views) turns the token prompt on; this module adds what the new
 * prompt needs: the lock after too many wrong tokens and whether a token is in use at all.
 */

/** Epoch ms until which the server refuses new attempts (`too_many_attempts`, `Retry-After`); 0 = none. */
export const authLockedUntil = ref(0)

/** True when `/status.auth_required` said the server has a token: Sign out is offered only then. */
export const tokenInUse = ref(false)

export function noteAuthLock(seconds: number | null | undefined, now = Date.now()) {
  const s = Number(seconds)
  if (Number.isFinite(s) && s > 0) authLockedUntil.value = Math.max(authLockedUntil.value, now + Math.ceil(s) * 1000)
}

/** Seconds left of the lock at `now`, rounded up. */
export function lockSecondsLeft(now: number): number {
  return Math.max(0, Math.ceil((authLockedUntil.value - now) / 1000))
}
