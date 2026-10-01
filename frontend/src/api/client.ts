import type { MatchDetail } from '@/lib/matchDetail'

type ValidationIssue = { loc?: (string | number)[]; msg?: string }

/** FastAPI 422: [{loc: ['body', 'max_concurrent'], msg: '...'}] → "max_concurrent: ..." */
function formatValidation(issues: ValidationIssue[]): string {
  return issues
    .map((i) => {
      const field = (i.loc || []).filter((p) => p !== 'body' && p !== 'query' && p !== 'path').join('.')
      return field ? `${field}: ${i.msg ?? ''}` : (i.msg ?? '')
    })
    .join('; ')
}

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json()
    if (typeof data?.detail === 'string') return data.detail
    if (Array.isArray(data?.detail)) return formatValidation(data.detail)
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

// ---- shapes returned by src/web/routes/*.py (leagues, matches, scrape, settings, data) ----

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
export type MatchList = { items: MatchRow[]; total: number; limit: number; offset: number; sort: 'asc' | 'desc' }

export type MissingMatch = { match_id: number; home?: string; away?: string; match_date?: string; season_name?: string }
export type MissingDetails = { total_matches: number; missing_count: number; missing: MissingMatch[]; truncated: boolean }

export type FetchSelection = { league_id: number; season_ids?: number[] | null; match_ids?: number[] | null }
export type FetchPayload = { mode?: 'full' | 'details'; league_id?: number | null; selections?: FetchSelection[] | null }

export type BackupScope = 'all' | 'config' | 'seasons' | 'matches' | 'match_details'

export type JobPhase = 'seasons' | 'matches' | 'details' | 'export'

export type FailedMatch = { match_id: string; league_id?: number }

/** Structured progress of the running job (src/web/progress.py). */
export type JobDetail = {
  phases: JobPhase[]
  phase: JobPhase | null
  phase_index: number
  phase_count: number
  done: number
  total: number
  league_id?: number | null
  league_name?: string
  season_name?: string
  eta_seconds: number | null
  /** SofaScore back-off; `until` is Unix seconds. */
  wait: { reason: 'rate_limit' | 'forbidden' | string; until: number } | null
  failed_count: number
  failed: FailedMatch[]
  /** Why detail downloads stopped early: '403', '429', '5xx' or 'other'. */
  breaker: string | null
  /** Provisional records re-read within the refresh window, and how many had changed on SofaScore. */
  refreshed?: number
  refresh_changed?: number
}

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
  matches_failed?: number
  circuit_breaker_triggered?: boolean
  circuit_breaker_reason?: string | null
  started_at?: string | null
  finished_at?: string | null
  payload?: FetchPayload | null
  detail?: JobDetail | null
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
  matches_failed?: number
  schedule_empty_seasons?: number
  circuit_breaker_triggered?: boolean
  payload?: FetchPayload | null
  result?: { refreshed?: number; refresh_changed?: number } | null
}

export type Settings = {
  language: string
  api_base_url?: string
  use_proxy?: boolean
  proxy_url?: string
  use_color?: boolean
  date_format?: string
  rate_limit_threshold_consecutive?: number
  rate_limit_threshold_ratio?: number
  server_error_threshold_consecutive?: number
  debug?: boolean
  data_dir: string
  max_concurrent: number
  wait_time_min: number
  wait_time_max: number
  request_timeout: number
  max_retries: number
  fetch_only_finished: boolean
  save_empty_rounds: boolean
  refresh_window_hours: number
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
    apiGet<MissingDetails>(
      `/api/leagues/${id}/missing-details${seasonId ? `?season_id=${seasonId}` : ''}`,
    ),
  matches: (params: URLSearchParams) => apiGet<MatchList>(`/api/matches?${params}`),
  match: (id: string | number) => apiGet<MatchDetail>(`/api/matches/${id}`),
  fetchMatch: (id: string | number) => apiSend<unknown>(`/api/matches/${id}/fetch`, 'POST'),
  fetch: (payload: FetchPayload) => apiSend<{ job_id: string }>('/api/fetch', 'POST', payload),
  jobs: (limit = 30) => apiGet<{ jobs: JobRow[] }>(`/api/jobs?limit=${limit}`),
  stats: () => apiGet<SystemStats>('/api/stats/system'),
  settings: () => apiGet<Settings>('/api/settings'),
  saveSettings: (s: Partial<Settings>) => apiSend<{ status: string }>('/api/settings', 'POST', s),
  /** Query options of POST /api/data/backup (src/web/routes/data.py); the page uses the defaults. */
  backup: ({ scope = 'all', include_env = false }: { scope?: BackupScope; include_env?: boolean } = {}) =>
    apiSend<{ download_url: string; filename: string }>(
      `/api/data/backup?${new URLSearchParams({ scope, include_env: String(include_env) })}`,
      'POST',
    ),
  clearData: (scope: 'all' | 'matches' | 'match_details' | 'seasons') =>
    apiSend<{ status: string }>('/api/data/clear', 'POST', { scope }),
}

export function seasonLabel(s: Season): string {
  return String(s.name || s.year || s.id)
}
