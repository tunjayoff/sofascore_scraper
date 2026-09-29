async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json()
    if (typeof data?.detail === 'string') return data.detail
    return JSON.stringify(data?.detail ?? data)
  } catch {
    return res.statusText || 'Request failed'
  }
}

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new ApiError(await parseError(res), res.status)
  return res.json()
}

export async function apiSend<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new ApiError(await parseError(res), res.status)
  if (res.status === 204) return undefined as T
  return res.json()
}

// ---- shapes returned by src/web/routes/api.py ----

export type League = { id: number; name: string; sport?: string | null }
export type RemoteLeague = { id: number; name: string; country: string; slug?: string | null; sport?: string | null }
export type Season = { id: number; name?: string; year?: string }

export type LeagueBreakdown = { id: number; name: string; matches: number; details: number; coverage: number }
export type SystemStats = {
  leagues: number
  seasons: number
  matches: number
  details: number
  league_breakdown: LeagueBreakdown[]
  disk_usage: { formatted_total: string; total: number }
}

export type MatchRow = {
  match_id: number | string
  round?: number | string
  home_team?: string
  away_team?: string
  home_score?: number | string
  away_score?: number | string
  match_date?: string | number
  status?: string
  tournament?: string
  season?: string
  league_folder?: string
  has_details?: boolean
}
export type MatchList = { items: MatchRow[]; total: number; limit: number; offset: number }

export type FetchSelection = { league_id: number; season_ids?: number[] | null; match_ids?: number[] | null }
export type FetchPayload = { mode?: 'full' | 'details'; league_id?: number | null; selections?: FetchSelection[] | null }

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
  started_at?: string | null
  finished_at?: string | null
  payload?: FetchPayload | null
}

export type JobRow = {
  id: string
  status: string
  progress: number
  current_task: string
  started_at?: string | null
  finished_at?: string | null
  matches_done?: number
  matches_total?: number
  payload?: FetchPayload | null
}

export type Settings = {
  language: string
  data_dir: string
  max_concurrent: number
  wait_time_min: number
  wait_time_max: number
  request_timeout: number
  max_retries: number
  fetch_only_finished: boolean
  save_empty_rounds: boolean
  log_level: string
}

// ---- endpoints ----

export const api = {
  leagues: () => apiGet<League[]>('/api/leagues'),
  addLeague: (id: number, name: string, sport?: string | null) =>
    apiSend<League>('/api/leagues', 'POST', { id, name, sport: sport ?? null }),
  setLeagueSport: (id: number, sport: string | null) => apiSend<League>(`/api/leagues/${id}`, 'PATCH', { sport }),
  removeLeague: (id: number) => apiSend<unknown>(`/api/leagues/${id}`, 'DELETE'),
  searchRemote: (q: string) => apiGet<RemoteLeague[]>(`/api/leagues/search-remote?q=${encodeURIComponent(q)}`),
  seasons: (id: number) => apiGet<{ seasons: Season[]; fetched: boolean }>(`/api/leagues/${id}/seasons`),
  refreshSeasons: (id: number) => apiSend<{ seasons: Season[] }>(`/api/leagues/${id}/seasons/refresh`, 'POST'),
  missingDetails: (id: number, seasonId?: number) =>
    apiGet<{ missing: { match_id: number }[] }>(
      `/api/leagues/${id}/missing-details${seasonId ? `?season_id=${seasonId}` : ''}`,
    ),
  matches: (params: URLSearchParams) => apiGet<MatchList>(`/api/matches?${params}`),
  match: (id: string | number) => apiGet<any>(`/api/matches/${id}`),
  fetchMatch: (id: string | number) => apiSend<unknown>(`/api/matches/${id}/fetch`, 'POST'),
  fetch: (payload: FetchPayload) => apiSend<{ job_id: string }>('/api/fetch', 'POST', payload),
  jobs: (limit = 30) => apiGet<{ jobs: JobRow[] }>(`/api/jobs?limit=${limit}`),
  stats: () => apiGet<SystemStats>('/api/stats/system'),
  settings: () => apiGet<Settings>('/api/settings'),
  saveSettings: (s: Partial<Settings>) => apiSend<{ status: string }>('/api/settings', 'POST', s),
  // The backend writes backups under src/web/static/ but reports a /static/ URL; that folder is
  // mounted at /legacy-static/ (src/web/app.py), so rewrite the prefix or the link 404s.
  backup: () =>
    apiSend<{ download_url: string; filename: string }>('/api/data/backup', 'POST').then((r) => ({
      ...r,
      download_url: r.download_url.replace(/^\/static\//, '/legacy-static/'),
    })),
  clearData: (scope: 'all' | 'matches' | 'match_details' | 'seasons') =>
    apiSend<{ status: string }>('/api/data/clear', 'POST', { scope }),
}

export function seasonLabel(s: Season): string {
  return String(s.name || s.year || s.id)
}
