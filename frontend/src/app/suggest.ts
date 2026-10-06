import { computed, getCurrentScope, onScopeDispose, ref, watch, type Ref } from 'vue'
import { v1 } from '@/api/v1/client'
import type { FollowRecord, TournamentHit } from '@/api/v1/schema'

/**
 * Suggestions while typing (FX-20), for the follow editor's search and the quick search (Ctrl K):
 *
 *  1. Local, at once and without a request to SofaScore: the follows (leagues, teams, players) whose name
 *     holds the text, then the stored tournaments and teams of the catalog (`GET /catalog/suggest`, a request
 *     to this server only, sent shortly after the last keystroke).
 *  2. SofaScore: from 2 characters on and 350 ms after the last keystroke, one search for every kind at once
 *     (`POST /tournaments/search` with `kinds: [tournament, team, player]`, SofaScore's `/search/all`). A newer
 *     keystroke aborts the older request; the server does not send a request whose client left. Every answer
 *     is kept for this page (`remoteCache`, case and spaces ignored), and the server keeps it 10 minutes, so
 *     a text typed again costs nothing. The server counts each request in the shared budget.
 *
 * The result is one list grouped by kind (leagues, teams, players), local names first; a name found twice is
 * shown once. "Already added" is read from the follows, so a kept answer is never stale about it.
 */

export type SuggestKind = 'tournament' | 'team' | 'player'
export const SUGGEST_KINDS: readonly SuggestKind[] = ['tournament', 'team', 'player']
/** SofaScore is asked from this many characters on (the server's minimum too). */
export const MIN_CHARS = 2
/** Milliseconds without typing before SofaScore is asked. */
export const REMOTE_DELAY = 350
/** Milliseconds without typing before the stored catalog is asked (this server only). */
export const LOCAL_DELAY = 120
/** At most this many names per kind. */
export const PER_KIND = 6
const CACHE_SIZE = 100

export type SuggestSource = 'follow' | 'catalog' | 'sofascore'
export type Suggestion = {
  /** `<kind>:<id>`, the follow id it would get. */
  key: string
  kind: SuggestKind
  hit: TournamentHit
  followed: boolean
  source: SuggestSource
}
export type SuggestGroup = { kind: SuggestKind; items: Suggestion[] }

/** The text as it is sent: trimmed, inner spaces as one. */
export function normalize(q: string): string {
  return q.trim().replace(/\s+/g, ' ')
}

/** Case and accents ignored, as the server compares names. */
export function fold(s: string): string {
  return normalize(s)
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/ı/g, 'i')
}

const isDigits = (q: string) => /^\d+$/.test(q)

/** Answers of SofaScore searches, for this page (browser tab session): key → hits. */
const remoteCache = new Map<string, TournamentHit[]>()

export function cacheKey(q: string): string {
  return normalize(q).toLowerCase()
}

function remember(key: string, hits: TournamentHit[]) {
  remoteCache.delete(key)
  remoteCache.set(key, hits)
  while (remoteCache.size > CACHE_SIZE) remoteCache.delete(remoteCache.keys().next().value as string)
}

/** A follow added or removed on this page: "already added" follows it at once. */
export function noteFollows(list: FollowRecord[]) {
  sharedFollows.value = list
}

/** A follow just added on this page. */
export function noteFollowAdded(f: FollowRecord) {
  if (sharedFollows.value) sharedFollows.value = [...sharedFollows.value.filter((x) => x.id !== f.id), f]
}

/** For tests: forget the kept answers. */
export function clearSuggestCache() {
  remoteCache.clear()
  sharedFollows.value = null
  followsPending = null
}

// The follows, read once per page when suggestions are first needed (or handed in by the caller)
const sharedFollows = ref<FollowRecord[] | null>(null)
let followsPending: Promise<unknown> | null = null
function loadFollows() {
  if (sharedFollows.value || followsPending) return
  followsPending = v1
    .follows()
    .then((r) => (sharedFollows.value = r.data))
    .catch(() => (followsPending = null))
}

const isAbort = (e: unknown) => (e as Error)?.name === 'AbortError'

function followHit(f: FollowRecord): TournamentHit {
  return { kind: f.kind as SuggestKind, id: f.entity_id, name: f.name, sport: f.sport ?? null, category: {}, followed: true }
}

/** Fill what one source lacks from another (a follow has no country, the catalog no player's team). */
function merged(a: TournamentHit, b: TournamentHit): TournamentHit {
  return {
    ...a,
    slug: a.slug ?? b.slug,
    sport: a.sport ?? b.sport,
    country: a.country ?? b.country,
    team: a.team ?? b.team,
    category: { ...(b.category ?? {}), ...(a.category ?? {}) },
  }
}

export type SuggestOptions = {
  /** Only hits of this sport (the editor's sport field); filtered here, never sent, so a change asks nothing. */
  sport?: Ref<string | null | undefined>
  /** Also the local suggestions (follows and the catalog); the quick search has its own. */
  local?: boolean
  /** The follows, when the caller has them; else they are read once. */
  follows?: Ref<FollowRecord[]>
}

export function useSuggest(query: Ref<string>, opts: SuggestOptions = {}) {
  const local = opts.local !== false
  const sport = computed(() => opts.sport?.value || null)
  const follows = computed<FollowRecord[]>(() => opts.follows?.value ?? sharedFollows.value ?? [])

  const catalog = ref<TournamentHit[]>([])
  const remote = ref<TournamentHit[]>([])
  /** The text the SofaScore hits belong to ('' = none yet). */
  const remoteFor = ref('')
  /** Waiting for the pause or for SofaScore's answer. */
  const pending = ref(false)
  const loading = ref(false)
  const error = ref<unknown>(null)

  let catalogTimer: ReturnType<typeof setTimeout> | null = null
  let remoteTimer: ReturnType<typeof setTimeout> | null = null
  let catalogCtl: AbortController | null = null
  let remoteCtl: AbortController | null = null
  /** The text as last handled: a keystroke that does not change it ("la" → "la ") asks nothing and aborts nothing. */
  let lastText: string | null = null

  function stopRemote() {
    if (remoteTimer) clearTimeout(remoteTimer)
    remoteTimer = null
    remoteCtl?.abort()
    remoteCtl = null
    loading.value = false
  }

  function askCatalog(q: string) {
    if (catalogTimer) clearTimeout(catalogTimer)
    catalogCtl?.abort()
    if (!local || !q || isDigits(q)) {
      catalog.value = []
      return
    }
    catalogTimer = setTimeout(() => {
      const ctl = (catalogCtl = new AbortController())
      v1.suggestCatalog({ q, limit: 8 }, ctl.signal)
        .then((hits) => {
          if (ctl === catalogCtl) catalog.value = hits
        })
        .catch(() => {})
    }, LOCAL_DELAY)
  }

  /** The search at SofaScore now; kept answers cost nothing. Resolves with the hits (null: failed or aborted). */
  function runRemote(q: string): Promise<TournamentHit[] | null> {
    stopRemote()
    const key = cacheKey(q)
    const kept = remoteCache.get(key)
    if (kept) {
      remote.value = kept
      remoteFor.value = q
      pending.value = false
      error.value = null
      return Promise.resolve(kept)
    }
    const ctl = (remoteCtl = new AbortController())
    pending.value = true
    loading.value = true
    error.value = null
    return v1
      .searchTournaments({ q, sport: null, kinds: [...SUGGEST_KINDS] }, ctl.signal)
      .then((hits) => {
        remember(key, hits)
        if (ctl === remoteCtl) {
          remote.value = hits
          remoteFor.value = q
        }
        return hits
      })
      .catch((e) => {
        if (!isAbort(e) && ctl === remoteCtl) error.value = e
        return null
      })
      .finally(() => {
        if (ctl === remoteCtl) {
          remoteCtl = null
          loading.value = false
          pending.value = false
        }
      })
  }

  function onText() {
    const q = normalize(query.value)
    if (q === lastText) return
    lastText = q
    if (q && local) loadFollows()
    askCatalog(q)
    stopRemote()
    if (q.length < MIN_CHARS || isDigits(q)) {
      remote.value = []
      remoteFor.value = ''
      pending.value = false
      error.value = null
      return
    }
    const kept = remoteCache.get(cacheKey(q))
    if (kept) {
      void runRemote(q)
      return
    }
    pending.value = true
    remoteTimer = setTimeout(() => void runRemote(q), REMOTE_DELAY)
  }

  // the sport only filters what is shown: changing it (picking a hit brings its sport) asks nothing again
  watch(query, onText)

  /** Enter or the search button: ask SofaScore at once, without the pause. */
  function searchNow(): Promise<TournamentHit[] | null> {
    const q = normalize(query.value)
    if (q.length < MIN_CHARS) return Promise.resolve(null)
    return runRemote(q)
  }

  function reset() {
    lastText = null
    stopRemote()
    if (catalogTimer) clearTimeout(catalogTimer)
    catalogCtl?.abort()
    catalog.value = []
    remote.value = []
    remoteFor.value = ''
    pending.value = false
    error.value = null
  }

  if (getCurrentScope()) onScopeDispose(reset)

  const items = computed<Suggestion[]>(() => {
    const q = normalize(query.value)
    if (!q) return []
    const wanted = fold(q)
    const followed = new Set(follows.value.map((f) => f.id))
    const out = new Map<string, Suggestion>()
    const add = (hit: TournamentHit, source: SuggestSource) => {
      const kind = (hit.kind ?? 'tournament') as SuggestKind
      if (!SUGGEST_KINDS.includes(kind)) return
      if (sport.value && hit.sport && hit.sport !== sport.value) return
      const key = `${kind}:${hit.id}`
      const seen = out.get(key)
      if (seen) {
        seen.hit = merged(seen.hit, hit)
        return
      }
      // "already added" from the follows; the server's flag only until they are read (a kept answer may be older)
      const known = source === 'follow' || followed.has(key) || (!follows.value.length && !!hit.followed)
      out.set(key, { key, kind, hit: { ...hit, kind }, followed: known, source })
    }
    if (local) {
      const starts = (name: string) => (fold(name).startsWith(wanted) ? 0 : 1)
      follows.value
        .filter((f) => SUGGEST_KINDS.includes(f.kind as SuggestKind) && fold(f.name).includes(wanted))
        .sort((a, b) => starts(a.name) - starts(b.name) || a.name.localeCompare(b.name))
        .forEach((f) => add(followHit(f), 'follow'))
      catalog.value.forEach((h) => add(h, 'catalog'))
    }
    if (remoteFor.value) {
      // hits of an older text stay while SofaScore is asked again, but only those that still match it
      const fresh = fold(remoteFor.value) === wanted
      const bare = wanted.replace(/ /g, '')
      remote.value.filter((h) => fresh || fold(h.name).replace(/ /g, '').includes(bare)).forEach((h) => add(h, 'sofascore'))
    }
    return [...out.values()]
  })

  const groups = computed<SuggestGroup[]>(() =>
    SUGGEST_KINDS.map((kind) => ({ kind, items: items.value.filter((s) => s.kind === kind).slice(0, PER_KIND) })).filter((g) => g.items.length),
  )
  /** The suggestions in the order they are shown (for the arrow keys). */
  const flat = computed(() => groups.value.flatMap((g) => g.items))
  /** SofaScore answered for the text as it is now (the list is final). */
  const current = computed(() => !!remoteFor.value && fold(remoteFor.value) === fold(query.value))

  return { catalog, remote, remoteFor, pending, loading, error, items, groups, flat, current, searchNow, reset }
}
