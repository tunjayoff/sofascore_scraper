import { session } from '@/api/v1/client'
import { reloadApp } from '@/lib/auth'
import { toast } from '@/ui/toast'
import { errorText } from '@/api/v1/errors'

/** Ends this browser's session and starts the app over, so the token prompt asks again. */
export async function signOut() {
  try {
    await session.logout()
    reloadApp()
  } catch (e) {
    toast({ kind: 'error', text: errorText(e) })
  }
}
