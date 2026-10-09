/**
 * How each setting is shown (05-web-ui.md 6.16). The API has no setting metadata yet (gap G4 of 7.3, P27):
 * until then this table gives every known key its section and control, with the limits of the server's
 * model (sofascore_scraper/config/settings.py) and of its write rules (sofascore_scraper/web/api/v1/settings.py: WRITABLE). Whether a
 * key can be changed always comes from the server (`writable`, `locked`); a key missing here is shown as
 * text under "Other", so nothing the server reports is hidden.
 */
export type Section = 'requests' | 'data' | 'refresh' | 'display' | 'logging' | 'storage' | 'server' | 'other'

export const SECTIONS: readonly Section[] = ['requests', 'data', 'refresh', 'display', 'logging', 'storage', 'server', 'other']

export type Control =
  | { type: 'int' | 'float'; min?: number; max?: number; step?: number }
  | { type: 'bool' }
  | { type: 'choice'; choices: readonly string[] }
  | { type: 'text'; maxLength?: number }
  | { type: 'list' }

/** `advanced`: shown last, under a closed "Advanced" fold (FX-24 F20): rarely changed, risky to change. */
export type Meta = { section: Section; control: Control; advanced?: boolean }

const int = (min?: number, max?: number): Control => ({ type: 'int', min, max, step: 1 })
const float = (min?: number, max?: number, step = 0.1): Control => ({ type: 'float', min, max, step })
const bool: Control = { type: 'bool' }
const text = (maxLength?: number): Control => ({ type: 'text', maxLength })
const list: Control = { type: 'list' }

export const META: Record<string, Meta> = {
  'client.rate': { section: 'requests', control: float(0, 1000, 0.5) },
  'client.max_concurrent': { section: 'requests', control: int(1, 50) },
  'client.timeout_seconds': { section: 'requests', control: int(1, 300) },
  'client.retries': { section: 'requests', control: int(0, 10) },
  'client.wait_time_min': { section: 'requests', control: float(0, 60) },
  'client.wait_time_max': { section: 'requests', control: float(0, 60) },
  'client.use_proxy': { section: 'requests', control: bool },
  'client.proxy': { section: 'requests', control: text(500) },
  'client.proxy_env': { section: 'requests', control: text() },
  'client.base_url': { section: 'requests', control: text(500), advanced: true },
  'client.captcha_token': { section: 'requests', control: text(), advanced: true },
  'client.browser_profile': { section: 'requests', control: text(), advanced: true },
  'client.browser_headed': { section: 'requests', control: bool },
  'client.throttle_dir': { section: 'requests', control: text(), advanced: true },
  'breaker.rate_limit_consecutive': { section: 'requests', control: int(1, 1000) },
  'breaker.rate_limit_ratio': { section: 'requests', control: float(0, 1, 0.05) },
  'breaker.server_error_consecutive': { section: 'requests', control: int(1, 1000) },
  'breaker.ignore': { section: 'requests', control: bool },
  'fetch.only_finished': { section: 'data', control: bool },
  'fetch.confirm_empty_after_seconds': { section: 'data', control: float(0, 86400, 1), advanced: true },
  'defaults.slices': { section: 'data', control: list },
  'defaults.seasons': { section: 'data', control: text() },
  'client.odds_provider': { section: 'data', control: int(1) },
  'refresh.window_hours': { section: 'refresh', control: float(0, 720, 1) },
  'refresh.min_interval_hours': { section: 'refresh', control: float(0) },
  'refresh.include_legacy': { section: 'refresh', control: bool },
  'display.language': { section: 'display', control: { type: 'choice', choices: ['en', 'tr'] } },
  'display.date_format': { section: 'display', control: text(50) },
  'display.use_color': { section: 'display', control: bool },
  'log.level': { section: 'logging', control: { type: 'choice', choices: ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] } },
  'log.debug': { section: 'logging', control: bool },
  'log.format': { section: 'logging', control: { type: 'choice', choices: ['text', 'json'] } },
  'log.dir': { section: 'logging', control: text() },
  'log.to_file': { section: 'logging', control: bool },
  'log.max_mb': { section: 'logging', control: float(0.001) },
  'log.backup_count': { section: 'logging', control: int(0) },
  'storage.data_dir': { section: 'storage', control: text(500) },
  'storage.durability': { section: 'storage', control: { type: 'choice', choices: ['normal', 'full'] } },
  'server.host': { section: 'server', control: text() },
  'server.port': { section: 'server', control: int(1, 65535) },
  'server.allowed_hosts': { section: 'server', control: list },
  'server.token_env': { section: 'server', control: text() },
  'server.token': { section: 'server', control: text() },
  'bridge.degraded_after': { section: 'server', control: int(1) },
  'bridge.blocked_after': { section: 'server', control: int(1) },
  'bridge.blocked_min_seconds': { section: 'server', control: float(0) },
  'live.source': { section: 'server', control: text() },
  'live.poll_interval_seconds': { section: 'server', control: float(0) },
  'live.detail_slices': { section: 'server', control: list },
  'live.detail_interval_seconds': { section: 'server', control: float(0) },
  'live.max_event_polls': { section: 'server', control: int(1) },
  'schedule.enabled': { section: 'server', control: bool },
}

/**
 * Settings the server still reports but that do nothing any more; not shown at all (not even under
 * "Other"). `fetch.save_empty_rounds` retired with ST-27: a round without a match is never stored (FX-20;
 * P30 removes the setting itself).
 */
export const RETIRED: readonly string[] = ['fetch.save_empty_rounds']

/** Whether a setting goes under the section's "Advanced" fold. */
export function isAdvanced(key: string): boolean {
  return !!META[key]?.advanced
}

export function metaOf(key: string): Meta {
  return META[key] ?? { section: 'other', control: text() }
}

/** Above this rate SofaScore may block the server (today's warning on the Settings page). */
export const SAFE_RATE = 5
export const DATA_DIR = 'storage.data_dir'
