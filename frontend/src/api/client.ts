async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json()
    if (typeof data?.detail === 'string') return data.detail
    return JSON.stringify(data?.detail ?? data)
  } catch {
    return res.statusText || 'Request failed'
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function apiSend<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(await parseError(res))
  if (res.status === 204) return undefined as T
  return res.json()
}

export type League = { id: number; name: string }
export type Season = { id: number; name?: string; year?: string }
export type ScrapeState = {
  job_id?: string | null
  is_running: boolean
  status: string
  progress: number
  current_task: string
  cancel_requested?: boolean
  schedule_empty_seasons?: number
  matches_done?: number
  matches_total?: number
  log?: string[]
  payload?: Record<string, unknown> | null
}
export type JobRow = {
  id: string
  status: string
  progress: number
  current_task: string
  started_at?: string | null
  finished_at?: string | null
  payload?: Record<string, unknown> | null
}
