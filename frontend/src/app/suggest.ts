import { computed, getCurrentScope, onScopeDispose, ref, watch, type Ref } from 'vue'
import { v1 } from '@/api/v1/client'
import type { FollowRecord, TournamentHit } from '@/api/v1/schema'
import { isIndividual } from '@/app/sports'

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
 * The result is one list: the local names first, then SofaScore's hits in its own
 * order, which is its relevance across kinds (FX-26, M17: grouped by kind, "sinner" showed six e-sports and
 * football teams before Jannik Sinner); each row names its kind. A name found twice is shown once. "Already
 * added" is read from the follows, so a kept answer is never stale about it.
 *
 * The local names (FX-28, M23) are ranked by where the text is: names that start with it, then names with a word
 * that starts with it, follows before stored names within each; a name that holds the text only inside a word
 * ("la" in Atalanta) is left out when some name or word starts with it, and at most `MAX_LOCAL` are shown, so
 * SofaScore's hits stay in view ("la" showed four stored teams and never LaLiga).
 */

export type SuggestKind = 'tournament' | 'team' | 'player'
export const SUGGEST_KINDS: readonly SuggestKind[] = ['tournament', 'team', 'player']
/** SofaScore is asked from this many characters on (the server's minimum too). */
export const MIN_CHARS = 2
/** Milliseconds without typing before SofaScore is asked. */
export const REMOTE_DELAY = 350
/** Milliseconds without typing before the stored catalog is asked (this server only). */
export const LOCAL_DELAY = 120
/** At most this many suggestions are shown. */
export const MAX_SHOWN = 15
/** SofaScore'un sonuçlarından önce en çok bu kadar yerel öneri (takipler ve kayıtlı adlar) gösterilir (FX-28). */
export const MAX_LOCAL = 5
const CACHE_SIZE = 100

export type SuggestSource = 'follow' | 'catalog' | 'sofascore'
export type Suggestion = {
  /** `<kind>:<id>`, the follow id it would get. */
  key: string
  kind: SuggestKind
  /** The kind it is shown as: a "team" of a sport of one against one is a player (FX-24 F31). */
  group: SuggestKind
  hit: TournamentHit
  followed: boolean
  source: SuggestSource
  /**
   * Another suggestion of the same group has the same name and sport (a men's and a women's team, FX-24 F26)
   * and their gender or national-team flag does not tell them apart (FX-25): the number does.
   */
  twin: boolean
}

/**
 * The kind a hit is shown as: SofaScore lists the players of tennis, darts, MMA … as teams (`team`), so such
 * a "team" is shown with the players; it is still followed as a team (its follow id stays `team:<id>`).
 */
export function shownKind(kind: SuggestKind, sport: string | null | undefined): SuggestKind {
  return kind === 'team' && isIndividual(sport) ? 'player' : kind
}

/**
 * Marks the entries whose `same` key another entry has too (`twin`); returns them in the same order. With
 * `apart`, an entry is a twin only when some other entry of its key is not told apart from it by `apart`.
 */
export function markTwins<T extends { twin: boolean }>(list: T[], same: (x: T) => string, apart?: (a: T, b: T) => boolean): T[] {
  const byKey = new Map<string, T[]>()
  for (const x of list) byKey.set(same(x), [...(byKey.get(same(x)) ?? []), x])
  return list.map((x) => ({ ...x, twin: (byKey.get(same(x)) ?? []).some((y) => y !== x && !apart?.(x, y)) }))
}

const knownGender = (g: string | null | undefined) => (g === 'M' || g === 'F' ? g : null)

/**
 * İki aynı adlı sonucu cinsiyet ya da milli takım bilgisi zaten ayırıyor mu (ikisinde de biliniyor ve
 * farklı; FX-25 F26): o zaman numara ("No. 36456") gerekmez. Birinde bilinmiyorsa ayırmaz.
 */
export function traitsApart(a: Pick<TournamentHit, 'gender' | 'national'>, b: Pick<TournamentHit, 'gender' | 'national'>): boolean {
  const ga = knownGender(a.gender)
  const gb = knownGender(b.gender)
  if (ga && gb && ga !== gb) return true
  return a.national != null && b.national != null && a.national !== b.national
}

/** The text as it is sent: trimmed, inner spaces as one. */
export function normalize(q: string): string {
  return q.trim().replace(/\s+/g, ' ')
}

// NFKD ile ayrışmayan harfler, sunucunun ad katlamasındaki gibi (sofascore_scraper/store/derive.py `fold_name`)
const FOLD_EXTRA: Record<string, string> = { ı: 'i', ø: 'o', ł: 'l', đ: 'd', ð: 'd', þ: 'th', æ: 'ae', œ: 'oe', ħ: 'h' }

/** Büyük-küçük harf ve aksan ayrımı yok, sunucunun adları karşılaştırdığı gibi ("İstanbul", "ıstanbul", "Istanbul" aynı). */
export function fold(s: string): string {
  return normalize(s)
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
    .toLowerCase()
    .replace(/[ıøłđðþæœħ]/g, (c) => FOLD_EXTRA[c] ?? c)
}

const isWordChar = (c: string) => /[\p{L}\p{N}]/u.test(c)

/**
 * Metnin adda geçtiği yer, ikisi de katlanmış (FX-28): 0 ad metinle başlıyor, 1 bir sözcüğü metinle başlıyor (harf
 * ya da rakam olmayan bir işaretten sonra: "bodo/glimt"te "glimt"), 2 yalnızca bir sözcüğün ortasında, -1 hiç.
 */
export function matchPlace(name: string, wanted: string): number {
  if (!wanted) return -1
  if (name.startsWith(wanted)) return 0
  let at = name.indexOf(wanted, 1)
  if (at < 0) return -1
  while (at > 0) {
    if (!isWordChar(name[at - 1]!)) return 1
    at = name.indexOf(wanted, at + 1)
  }
  return 2
}

/**
 * Metin için yerel adlar, gösterilecekleri sırayla (FX-28): metinle başlayanlar, sonra bir sözcüğü metinle
 * başlayanlar, her biri verilen sırasıyla; metnin yalnızca sözcük ortasında geçtiği adlar ancak hiçbir ad ya da
 * sözcük metinle başlamıyorsa; en çok `max` tane.
 */
export function rankLocal<T>(list: readonly T[], name: (x: T) => string, text: string, max = MAX_LOCAL): T[] {
  const wanted = fold(text)
  const placed = list.map((x) => ({ x, place: matchPlace(fold(name(x)), wanted) })).filter((p) => p.place >= 0)
  const best = Math.min(3, ...placed.map((p) => p.place))
  return placed
    .filter((p) => best === 2 || p.place < 2)
    .sort((a, b) => a.place - b.place)
    .slice(0, max)
    .map((p) => p.x)
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
  noteHits(hits)
}

/**
 * Bu sayfada görülen takım sonuçları (`team:<id>` → sonuç), cinsiyeti, milli takım bilgisi ya da ülkesi
 * olanlar (FX-25 F26): takip kaydında bunlar yok (API'de takım kaydı yok), takip sayfasının başlığı ve
 * Ctrl K'dan açılan düzenleyici onları buradan okur. Yalnızca bu sekme için; yeni bir istek göndermez.
 */
const seenTeams = new Map<string, TournamentHit>()
const SEEN_SIZE = 500

function noteHits(hits: TournamentHit[]) {
  for (const h of hits) {
    if (h.kind !== 'team' || (h.gender == null && h.national == null && !h.country)) continue
    const key = `team:${h.id}`
    seenTeams.delete(key)
    seenTeams.set(key, h)
  }
  while (seenTeams.size > SEEN_SIZE) seenTeams.delete(seenTeams.keys().next().value as string)
}

/** A team hit seen on this page (`team:<id>`), with its gender, national-team flag and country; else null. */
export function seenTeam(key: string): TournamentHit | null {
  return seenTeams.get(key) ?? null
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
  seenTeams.clear()
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
    gender: a.gender ?? b.gender,
    national: a.national ?? b.national,
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
    const fits = (hit: TournamentHit) => SUGGEST_KINDS.includes((hit.kind ?? 'tournament') as SuggestKind) && !(sport.value && hit.sport && hit.sport !== sport.value)
    const add = (hit: TournamentHit, source: SuggestSource) => {
      const kind = (hit.kind ?? 'tournament') as SuggestKind
      if (!fits(hit)) return
      const key = `${kind}:${hit.id}`
      const seen = out.get(key)
      if (seen) {
        seen.hit = merged(seen.hit, hit)
        return
      }
      // "already added" from the follows; the server's flag only until they are read (a kept answer may be older)
      const known = source === 'follow' || followed.has(key) || (!follows.value.length && !!hit.followed)
      out.set(key, { key, kind, group: shownKind(kind, hit.sport), hit: { ...hit, kind }, followed: known, source, twin: false })
    }
    if (local) {
      // takipler (ada göre) ve katalogdaki adlar (sunucunun sırasıyla) bir arada sıralanır; ikisinde de olan bir
      // ad takip olarak bir kez durur ve katalogdaki bilgisiyle tamamlanır
      const locals = new Map<string, { hit: TournamentHit; source: SuggestSource }>()
      const note = (hit: TournamentHit, source: SuggestSource) => {
        if (!fits(hit)) return
        const key = `${hit.kind ?? 'tournament'}:${hit.id}`
        const seen = locals.get(key)
        if (seen) seen.hit = merged(seen.hit, hit)
        else locals.set(key, { hit, source })
      }
      ;[...follows.value]
        .filter((f) => SUGGEST_KINDS.includes(f.kind as SuggestKind))
        .sort((a, b) => a.name.localeCompare(b.name))
        .forEach((f) => note(followHit(f), 'follow'))
      catalog.value.forEach((h) => note(h, 'catalog'))
      rankLocal([...locals.values()], (x) => x.hit.name, q).forEach((x) => add(x.hit, x.source))
    }
    if (remoteFor.value) {
      // hits of an older text stay while SofaScore is asked again, but only those that still match it
      const fresh = fold(remoteFor.value) === wanted
      const bare = wanted.replace(/ /g, '')
      remote.value.filter((h) => fresh || fold(h.name).replace(/ /g, '').includes(bare)).forEach((h) => add(h, 'sofascore'))
    }
    const found = [...out.values()]
    return markTwins(found, (x) => `${x.group}|${x.hit.sport ?? ''}|${fold(x.hit.name)}`, (a, b) => traitsApart(a.hit, b.hit))
  })

  /** The suggestions in the order they are shown: local first, then SofaScore's relevance order (the arrow keys walk it). */
  const flat = computed(() => items.value.slice(0, MAX_SHOWN))
  /** SofaScore answered for the text as it is now (the list is final). */
  const current = computed(() => !!remoteFor.value && fold(remoteFor.value) === fold(query.value))

  return { catalog, remote, remoteFor, pending, loading, error, items, flat, current, searchNow, reset }
}
