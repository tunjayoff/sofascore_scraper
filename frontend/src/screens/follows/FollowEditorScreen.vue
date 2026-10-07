<script setup lang="ts">
import { computed, nextTick, onMounted, ref, useId, watch } from 'vue'
import { RouterLink, onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import UiIcon from '@/ui/UiIcon.vue'
import UiBadge from '@/ui/UiBadge.vue'
import FormError from '@/ui/FormError.vue'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import ErrorState from '@/ui/ErrorState.vue'
import EmptyState from '@/ui/EmptyState.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import { v1, V1Error } from '@/api/v1/client'
import type { FollowPatch, FollowRecord, TournamentHit } from '@/api/v1/schema'
import { loadSports, sportName, sports } from '@/app/sports'
import { useStatusStore } from '@/app/statusStore'
import { toast } from '@/ui/toast'
import { toastError } from '@/api/v1/errors'
import { startJob } from '@/screens/jobs/startJob'
import SlicePicker from './SlicePicker.vue'
import SeasonChooser from './SeasonChooser.vue'
import MoveFollow from './MoveFollow.vue'
import HelpTip from '@/ui/HelpTip.vue'
import { noteFollowAdded } from '@/app/suggest'
import FollowSearch from './FollowSearch.vue'
import { FOLLOW_KINDS, MORE_FOLLOW_KINDS, dataText, followKindReady, followPath, hitPlace, lockReason, seasonsText, type FollowKind } from './followText'

/**
 * The follow editor (6.3), "Add a league or team". A new follow in four steps:
 *  1. What: a league, a team or a player suggested while typing (FX-20, `FollowSearch`): follows and stored
 *     names at once, then SofaScore's leagues, teams and players together (one search after a short pause;
 *     a hit shows its kind, sport, country, a player's team and whether it is followed already). Picking one
 *     fills the kind, the number, the name and the sport. Or anything by its SofaScore number; a single
 *     match by its number (the match page's "Follow this match" fills it in).
 *  2. Seasons: a league's seasons (current, last N, all, or chosen by name; a league never downloaded gets
 *     its season list on an explicit click, FX-13); for a team or a player the same words are a time
 *     window (FX-19); a single match has none.
 *  3. Data: the defaults or a selection of this follow (`slices`, P27).
 *  4. Review, and "Start downloading right away" (a download of this follow, `follows: [id]`).
 * `?q=` (the quick search) fills the search and runs it; `?kind=&id=&name=&sport=` fill step 1. A change
 * shows the same sections on one page; a field this follow cannot change (`writable`) is locked with the
 * reason, and a follow of the old league list can be moved here to make it editable (FX-19).
 */
const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const status = useStatusStore()
const uid = useId()

const editing = computed(() => route.params.kind != null)
const step = ref<1 | 2 | 3 | 4>(1)

// ---- the follow ----
const kind = ref<FollowKind>('tournament')
const entityId = ref<string>('')
const name = ref('')
const sport = ref('')
type SeasonMode = 'current' | 'last' | 'all' | 'choose'
const seasonMode = ref<SeasonMode>('current')
const lastN = ref(2)
const chosenSeasons = ref<number[]>([])
const slices = ref<FollowRecord['slices'] | null>(null)
const live = ref(false)
const enabled = ref(true)
const syncAfter = ref(true)

// ---- editing: the stored follow ----
const original = ref<FollowRecord | null>(null)
const loading = ref(false)
const loadError = ref<unknown>(null)
const locked = computed(() => (original.value ? lockReason(original.value) : null))
function fieldLock(field: string) {
  return original.value ? lockReason(original.value, field) : null
}

// ---- the search: suggestions while typing, every kind at once (FX-20) ----
const searchable = computed(() => kind.value !== 'event')
const query = ref('')
const searchBox = ref<InstanceType<typeof FollowSearch> | null>(null)
const nextButton = ref<HTMLButtonElement | null>(null)
const picked = ref<TournamentHit | null>(null)
const pickedHit = computed(() => (picked.value ? `${picked.value.kind ?? 'tournament'}:${picked.value.id}` : null))
let picking = false

function pick(hit: TournamentHit) {
  const k = (hit.kind ?? 'tournament') as FollowKind
  picking = k !== kind.value
  kind.value = k
  picked.value = hit
  entityId.value = String(hit.id)
  name.value = hit.name
  // the hit's sport comes along: a team or a player without one would not know its data types (FX-19)
  if (hit.sport) sport.value = hit.sport
  // Next is the only step left (FX-14a); the kind watcher has run by then
  void nextTick(() => {
    picking = false
    nextButton.value?.focus()
  })
}
// another kind chosen by hand: the picked hit was of the other kind
watch(kind, () => {
  if (!picking) picked.value = null
  if (kind.value !== 'tournament' && seasonMode.value === 'choose') seasonMode.value = 'current'
})

const idValue = computed(() => {
  const n = Number(entityId.value)
  return Number.isInteger(n) && n > 0 ? n : null
})
const seasonsValue = computed<FollowRecord['seasons']>(() => {
  if (seasonMode.value === 'last') return `last:${Math.max(1, Math.round(lastN.value || 1))}`
  if (seasonMode.value === 'choose') return chosenSeasons.value
  return seasonMode.value
})
const stepValid = computed(() => {
  if (step.value === 1) return !!idValue.value && !!name.value.trim()
  if (step.value === 2) return seasonMode.value !== 'choose' || chosenSeasons.value.length > 0
  return true
})
/** Seasons of a league are seasons; for a team or a player they are a time window (FX-19). */
const isWindow = computed(() => kind.value === 'team' || kind.value === 'player')
/**
 * Live watching (`ssc watch`) covers leagues, teams and single matches; it skips player follows (FX-19), so
 * a player is not offered it (FX-20). A player follow that has it on keeps the box, to turn it off.
 */
const liveOffered = computed(() => kind.value !== 'player' || (!!original.value && original.value.live))

// ---- save ----
const saving = ref(false)
const saveError = ref<unknown>(null)
const exists = computed(() => saveError.value instanceof V1Error && saveError.value.code === 'follow_exists')
let saved = false

async function create() {
  saving.value = true
  saveError.value = null
  try {
    const f = await v1.addFollow({
      kind: kind.value,
      entity_id: idValue.value!,
      name: name.value.trim(),
      sport: sport.value || null,
      seasons: kind.value === 'event' ? 'current' : seasonsValue.value,
      slices: slices.value,
      live: liveOffered.value && live.value,
    })
    saved = true
    noteFollowAdded(f)
    toast({ kind: 'ok', text: t('ui.followEditor.added', { name: f.name }), link: { to: followPath(f), label: t('ui.followEditor.open') } })
    // a download of this follow, with its own season choice (FX-13 `follows`)
    if (syncAfter.value && f.enabled) await startJob({ kind: 'sync', spec: { follows: [f.id] } }).catch((e) => toastError(e, status.activeJob?.id))
    void router.push(followPath(f))
  } catch (e) {
    saveError.value = e
  } finally {
    saving.value = false
  }
}

const patch = computed<FollowPatch>(() => {
  const o = original.value
  if (!o) return {}
  const p: FollowPatch = {}
  if (name.value.trim() !== o.name) p.name = name.value.trim()
  if ((sport.value || null) !== (o.sport ?? null)) p.sport = sport.value || null
  if (o.kind !== 'event' && JSON.stringify(seasonsValue.value) !== JSON.stringify(o.seasons)) p.seasons = seasonsValue.value
  if (JSON.stringify(slices.value ?? null) !== JSON.stringify(o.slices ?? null)) p.slices = slices.value ?? null
  if (live.value !== o.live) p.live = live.value
  if (enabled.value !== o.enabled) p.enabled = enabled.value
  return p
})
const dirty = computed(() => Object.keys(patch.value).length > 0)

async function update() {
  if (!original.value || !dirty.value) return
  saving.value = true
  saveError.value = null
  try {
    const f = await v1.updateFollow(original.value.id, patch.value)
    saved = true
    toast({ kind: 'ok', text: t('ui.followEditor.saved', { name: f.name }) })
    void router.push(followPath(f))
  } catch (e) {
    saveError.value = e
  } finally {
    saving.value = false
  }
}

function fill(f: FollowRecord) {
  kind.value = f.kind
  entityId.value = String(f.entity_id)
  name.value = f.name
  sport.value = f.sport ?? ''
  live.value = f.live
  enabled.value = f.enabled
  slices.value = f.slices ?? null
  const s = f.seasons
  if (Array.isArray(s)) {
    seasonMode.value = 'choose'
    chosenSeasons.value = [...s]
  } else if (typeof s === 'string' && s.startsWith('last:')) {
    seasonMode.value = 'last'
    lastN.value = Number(s.slice(5)) || 2
  } else seasonMode.value = s === 'all' ? 'all' : 'current'
}

async function loadFollow() {
  loading.value = true
  loadError.value = null
  try {
    original.value = await v1.follow(`${String(route.params.kind)}:${String(route.params.id)}`)
    fill(original.value)
  } catch (e) {
    loadError.value = e
  } finally {
    loading.value = false
  }
}
function moved(f: FollowRecord) {
  original.value = f
  fill(f)
}

const notFound = computed(() => loadError.value instanceof V1Error && loadError.value.code === 'not_found')

const leaving = ref<string | null>(null)
let leaveOk = false
onBeforeRouteLeave((to) => {
  if (saved || leaveOk || !editing.value || !dirty.value) return true
  leaving.value = to.fullPath
  return false
})
function leave() {
  const to = leaving.value
  leaveOk = true
  leaving.value = null
  if (to) void router.push(to)
}

const text = (v: unknown) => (typeof v === 'string' ? v.trim() : '')
onMounted(() => {
  void loadSports().catch(() => {})
  if (editing.value) void loadFollow()
  else {
    const k = text(route.query.kind)
    if ((FOLLOW_KINDS as readonly string[]).includes(k) && followKindReady(k as FollowKind)) kind.value = k as FollowKind
    if (text(route.query.id)) entityId.value = text(route.query.id)
    if (text(route.query.name)) name.value = text(route.query.name)
    if (text(route.query.sport)) sport.value = text(route.query.sport)
    // from the quick search: the text is searched at once (free when the quick search already asked it)
    const q = text(route.query.q)
    if (q) {
      if (!searchable.value) kind.value = 'tournament'
      query.value = q
      void nextTick(() => searchBox.value?.search())
    }
  }
})
</script>

<template>
  <div class="max-w-[880px]">
    <PageHeader
      :title="editing ? t('ui.followEditor.editTitle', { name: original?.name ?? '…' }) : t('ui.followEditor.newTitle')"
      :description="editing ? undefined : t('ui.followEditor.newDescription')"
      :crumbs="[{ label: t('ui.nav.follows'), to: '/follows' }, ...(original ? [{ label: original.name, to: followPath(original) }] : [])]"
    />

    <!-- ===== a new follow: four steps ===== -->
    <template v-if="!editing">
      <ol class="u-steps mb-5" :aria-label="t('ui.followEditor.steps')">
        <li v-for="i in 4" :key="i" :aria-current="step === i ? 'step' : undefined" :class="{ 'is-done': step > i }"><span class="u-step-n">{{ i }}</span>{{ t(`ui.followEditor.step${i}`) }}</li>
      </ol>

      <section class="u-card p-6 flex flex-col gap-5" data-testid="editor-step" :data-step="step" :aria-labelledby="`${uid}-step`">
        <h2 :id="`${uid}-step`" class="u-h3">{{ t('ui.followEditor.stepOf', { i: step, n: 4, name: t(`ui.followEditor.step${step}`) }) }}</h2>
        <!-- 1: what -->
        <template v-if="step === 1">
          <fieldset class="flex flex-wrap gap-2">
            <legend class="u-label">{{ t('ui.followEditor.kind') }}</legend>
            <label v-for="k in FOLLOW_KINDS" :key="k" class="u-option" :class="{ 'is-soon': !followKindReady(k) }" :data-kind="k">
              <input v-model="kind" type="radio" :name="`${uid}-kind`" :value="k" class="u-check" :disabled="!followKindReady(k)" />{{ t(`ui.follows.kind.${k}`) }}
              <UiBadge v-if="!followKindReady(k)" tone="neutral">{{ t('ui.follows.soon') }}</UiBadge>
            </label>
          </fieldset>
          <p v-if="!MORE_FOLLOW_KINDS" class="m-0 u-small u-muted" data-testid="editor-soon">{{ t('ui.follows.soonReason') }}</p>
          <label class="flex flex-col max-w-[320px]">
            <span class="u-label">{{ t('ui.follows.col.sport') }}</span>
            <select v-model="sport" class="u-field" data-testid="editor-sport">
              <option value="">{{ t('ui.followEditor.anySport') }}</option>
              <option v-for="s in sports" :key="s.slug" :value="s.slug">{{ sportName(s.slug) }}</option>
            </select>
          </label>

          <template v-if="searchable">
            <FollowSearch ref="searchBox" v-model="query" :sport="sport || null" :picked="pickedHit" @pick="pick" />
            <p v-if="picked" class="m-0 u-notice flex flex-wrap items-center gap-x-3 gap-y-1" data-testid="editor-picked" :data-hit="pickedHit">
              <UiIcon name="check" :size="16" />
              <span>{{ t('ui.suggest.picked') }} <strong>{{ picked.name }}</strong></span>
              <span class="u-small u-muted">{{ t(`ui.follows.kind.${picked.kind ?? 'tournament'}`) }}</span>
              <span v-if="picked.sport" class="u-small u-muted">{{ sportName(picked.sport) }}</span>
              <span v-if="hitPlace(picked)" class="u-small u-muted">{{ hitPlace(picked) }}</span>
              <span v-if="picked.team?.name" class="u-small u-muted">{{ t('ui.followEditor.playsFor', { team: picked.team.name }) }}</span>
              <span class="u-small u-muted u-mono">#{{ picked.id }}</span>
              <UiBadge v-if="picked.followed" tone="ok" icon="check">{{ t('ui.followEditor.alreadyFollowed') }}</UiBadge>
            </p>
          </template>
          <p v-else class="m-0 u-small u-muted" data-testid="editor-event-note">{{ t('ui.followEditor.eventNote') }}</p>

          <div class="grid gap-4 sm:grid-cols-2">
            <label class="flex flex-col">
              <span class="u-label">{{ t(`ui.followEditor.orId.${kind}`) }}</span>
              <input
                v-model="entityId"
                class="u-field u-mono"
                inputmode="numeric"
                autocomplete="off"
                data-testid="editor-id"
                :aria-invalid="!!entityId && !idValue"
                :aria-describedby="`${uid}-idhint`"
              />
              <span :id="`${uid}-idhint`" class="u-small u-muted mt-1">{{ t(`ui.followEditor.idHint.${kind}`) }}</span>
            </label>
            <label class="flex flex-col">
              <span class="u-label">{{ t('ui.followEditor.name') }}</span>
              <input v-model="name" class="u-field" autocomplete="off" data-testid="editor-name" />
            </label>
          </div>
        </template>

        <!-- 2: seasons, or a team's or a player's time window -->
        <template v-else-if="step === 2">
          <p v-if="kind === 'event'" class="m-0 u-muted" data-testid="editor-one-match">{{ t('ui.followEditor.oneMatch') }}</p>
          <fieldset v-else class="flex flex-col gap-2" data-testid="editor-seasons">
            <legend class="u-label">{{ isWindow ? t('ui.followEditor.window') : t('ui.followEditor.seasons') }}</legend>
            <p v-if="isWindow" class="m-0 u-small u-muted" data-testid="editor-window-note">{{ t(`ui.followEditor.windowNote.${kind}`) }}</p>
            <label class="u-option"
              ><input v-model="seasonMode" type="radio" :name="`${uid}-s`" value="current" class="u-check" />{{ isWindow ? t('ui.followEditor.windowCurrent') : t('ui.follows.seasons.current') }}</label
            >
            <label class="u-option items-center">
              <input v-model="seasonMode" type="radio" :name="`${uid}-s`" value="last" class="u-check" />
              <span class="inline-flex items-center gap-2"
                >{{ t('ui.followEditor.lastN')
                }}<input v-model.number="lastN" type="number" min="1" max="50" class="u-field" style="width: 80px; height: 32px" :aria-label="t('ui.followEditor.lastNLabel')" @focus="seasonMode = 'last'" />{{
                  isWindow ? t('ui.followEditor.windowLastSuffix') : t('ui.followEditor.lastNSuffix')
                }}</span
              >
            </label>
            <label class="u-option"
              ><input v-model="seasonMode" type="radio" :name="`${uid}-s`" value="all" class="u-check" />{{ isWindow ? t('ui.followEditor.windowAll') : t('ui.follows.seasons.all') }}</label
            >
            <template v-if="kind === 'tournament'">
              <label class="u-option"><input v-model="seasonMode" type="radio" :name="`${uid}-s`" value="choose" class="u-check" />{{ t('ui.followEditor.choose') }}</label>
              <div v-if="seasonMode === 'choose' && idValue" class="pl-6">
                <SeasonChooser v-model="chosenSeasons" :tournament-id="idValue" />
              </div>
            </template>
          </fieldset>
        </template>

        <!-- 3: data -->
        <SlicePicker v-else-if="step === 3" v-model:selection="slices" :sport="sport || null" editable />

        <!-- 4: review -->
        <template v-else>
          <dl class="u-result" data-testid="editor-review">
            <dt>{{ t('ui.followEditor.kind') }}</dt>
            <dd>{{ t(`ui.follows.kind.${kind}`) }} <span class="u-mono u-muted">#{{ idValue }}</span></dd>
            <dt>{{ t('ui.follows.col.sport') }}</dt>
            <dd>{{ sport ? sportName(sport) : '—' }}</dd>
            <dt>{{ t('ui.follows.col.seasons') }}</dt>
            <dd>{{ kind === 'event' ? '—' : seasonsText(seasonsValue, kind) }}</dd>
            <dt>{{ t('ui.follows.col.data') }}</dt>
            <dd>{{ dataText(slices) }}</dd>
          </dl>
          <label class="flex flex-col max-w-[480px]">
            <span class="u-label">{{ t('ui.followEditor.name') }}</span>
            <input v-model="name" class="u-field" autocomplete="off" />
          </label>
          <div v-if="liveOffered" class="flex items-start gap-1">
            <label class="flex items-start gap-3">
              <input v-model="live" type="checkbox" class="u-check mt-1" data-testid="editor-live" />
              <span class="flex flex-col"><span class="font-semibold">{{ t('ui.followEditor.live') }}</span><span class="u-small u-muted">{{ t('ui.followEditor.liveHint') }}</span></span>
            </label>
            <HelpTip term="live" />
          </div>
          <p v-else class="m-0 u-small u-muted" data-testid="editor-no-live">{{ t('ui.followEditor.liveNoPlayer') }}</p>
          <div class="flex items-start gap-1">
            <label class="flex items-start gap-3">
              <input v-model="syncAfter" type="checkbox" class="u-check mt-1" data-testid="editor-sync-after" />
              <span class="flex flex-col"><span class="font-semibold">{{ t('ui.followEditor.syncAfter') }}</span><span class="u-small u-muted">{{ t('ui.jobs.start.sendsRequests') }}</span></span>
            </label>
            <HelpTip term="download" />
          </div>
          <FormError v-if="saveError" :error="saveError" :active-job-id="status.activeJob?.id" />
          <p v-if="exists && idValue" class="m-0"><RouterLink :to="`/follows/${kind}/${idValue}`" class="font-semibold">{{ t('ui.followEditor.openExisting') }}</RouterLink></p>
        </template>

        <div class="flex flex-wrap justify-end gap-3 pt-2" style="border-top: 1px solid var(--line)">
          <RouterLink v-if="step === 1" to="/follows" class="u-btn">{{ t('ui.common.cancel') }}</RouterLink>
          <button v-else type="button" class="u-btn" :disabled="saving" @click="step = (step - 1) as 1 | 2 | 3">{{ t('ui.restore.back') }}</button>
          <button v-if="step < 4" ref="nextButton" type="button" class="u-btn u-btn-primary" :disabled="!stepValid" data-testid="editor-next" @click="step = (step + 1) as 2 | 3 | 4">{{ t('ui.restore.next') }}</button>
          <button v-else type="button" class="u-btn u-btn-primary" :disabled="saving || !stepValid || !name.trim()" data-testid="editor-save" @click="create">
            <span v-if="saving" class="u-spinner" aria-hidden="true"></span>{{ t('ui.followEditor.follow') }}
          </button>
        </div>
      </section>
    </template>

    <!-- ===== a change: the same sections on one page ===== -->
    <template v-else>
      <div v-if="loading" class="u-card p-6"><SkeletonBlock :lines="6" /></div>
      <div v-else-if="notFound" class="u-card">
        <EmptyState icon="follows" :title="t('ui.followDetail.notFound')" :text="t('ui.followDetail.notFoundText', { id: `${route.params.kind}:${route.params.id}` })">
          <RouterLink to="/follows" class="u-btn">{{ t('ui.followDetail.back') }}</RouterLink>
        </EmptyState>
      </div>
      <div v-else-if="loadError" class="u-card"><ErrorState :error="loadError" @retry="loadFollow" /></div>
      <form v-else-if="original" class="flex flex-col gap-6" data-testid="editor-form" @submit.prevent="update">
        <p v-if="locked" class="m-0 u-notice" data-testid="editor-locked"><UiIcon name="lock" :size="16" />{{ locked }}</p>
        <MoveFollow v-if="original.origin === 'legacy'" :follow="original" @moved="moved" />
        <section class="u-card p-6 flex flex-col gap-4">
          <h2 class="u-h3">{{ t('ui.followEditor.step1') }}</h2>
          <p class="m-0 u-small u-muted">{{ t(`ui.follows.kind.${original.kind}`) }} <span class="u-mono">#{{ original.entity_id }}</span></p>
          <div class="grid gap-4 sm:grid-cols-2">
            <label class="flex flex-col">
              <span class="u-label">{{ t('ui.followEditor.name') }}</span>
              <input v-model="name" class="u-field" :disabled="!!fieldLock('name')" :title="fieldLock('name') ?? undefined" data-testid="edit-name" />
            </label>
            <label class="flex flex-col">
              <span class="u-label">{{ t('ui.follows.col.sport') }}</span>
              <select v-model="sport" class="u-field" :disabled="!!fieldLock('sport')" data-testid="edit-sport">
                <option value="">{{ t('ui.followEditor.anySport') }}</option>
                <option v-for="s in sports" :key="s.slug" :value="s.slug">{{ sportName(s.slug) }}</option>
              </select>
            </label>
          </div>
          <p v-if="fieldLock('name') && !locked" class="m-0 u-small u-muted flex items-center gap-2"><UiIcon name="lock" :size="14" />{{ fieldLock('name') }}</p>
        </section>

        <section v-if="original.kind !== 'event'" class="u-card p-6 flex flex-col gap-3" data-testid="edit-seasons">
          <h2 class="u-h3">{{ original.kind === 'tournament' ? t('ui.followEditor.step2') : t('ui.followEditor.window') }}</h2>
          <p v-if="isWindow" class="m-0 u-small u-muted">{{ t(`ui.followEditor.windowNote.${original.kind}`) }}</p>
          <fieldset class="flex flex-col gap-2" :disabled="!!fieldLock('seasons')">
            <legend class="u-sr">{{ t('ui.followEditor.seasons') }}</legend>
            <label class="u-option"
              ><input v-model="seasonMode" type="radio" :name="`${uid}-es`" value="current" class="u-check" />{{ isWindow ? t('ui.followEditor.windowCurrent') : t('ui.follows.seasons.current') }}</label
            >
            <label class="u-option items-center">
              <input v-model="seasonMode" type="radio" :name="`${uid}-es`" value="last" class="u-check" />
              <span class="inline-flex items-center gap-2"
                >{{ t('ui.followEditor.lastN') }}<input v-model.number="lastN" type="number" min="1" max="50" class="u-field" style="width: 80px; height: 32px" :aria-label="t('ui.followEditor.lastNLabel')" />{{
                  isWindow ? t('ui.followEditor.windowLastSuffix') : t('ui.followEditor.lastNSuffix')
                }}</span
              >
            </label>
            <label class="u-option"
              ><input v-model="seasonMode" type="radio" :name="`${uid}-es`" value="all" class="u-check" />{{ isWindow ? t('ui.followEditor.windowAll') : t('ui.follows.seasons.all') }}</label
            >
            <template v-if="original.kind === 'tournament'">
              <label class="u-option"><input v-model="seasonMode" type="radio" :name="`${uid}-es`" value="choose" class="u-check" />{{ t('ui.followEditor.choose') }}</label>
              <div v-if="seasonMode === 'choose'" class="pl-6">
                <SeasonChooser v-model="chosenSeasons" :tournament-id="original.entity_id" :disabled="!!fieldLock('seasons')" />
              </div>
            </template>
          </fieldset>
        </section>

        <section class="u-card p-6 flex flex-col gap-3">
          <h2 class="u-h3">{{ t('ui.followEditor.step3') }}</h2>
          <SlicePicker v-model:selection="slices" :sport="sport || null" editable :lock-reason="fieldLock('slices')" />
          <p class="m-0 u-small u-muted">{{ t('ui.followEditor.changeNote') }}</p>
        </section>

        <section class="u-card p-6 flex flex-col gap-3">
          <h2 class="u-h3">{{ t('ui.followEditor.more') }}</h2>
          <label v-if="liveOffered" class="flex items-start gap-3">
            <input v-model="live" type="checkbox" class="u-check mt-1" :disabled="!!fieldLock('live')" data-testid="edit-live" />
            <span class="flex flex-col"><span class="font-semibold">{{ t('ui.followEditor.live') }}</span><span class="u-small u-muted">{{ t('ui.followEditor.liveHint') }}</span></span>
          </label>
          <p v-else class="m-0 u-small u-muted" data-testid="edit-no-live">{{ t('ui.followEditor.liveNoPlayer') }}</p>
          <label class="flex items-start gap-3">
            <input v-model="enabled" type="checkbox" class="u-check mt-1" :disabled="!!fieldLock('enabled')" />
            <span class="flex flex-col"><span class="font-semibold">{{ t('ui.followEditor.enabled') }}</span><span class="u-small u-muted">{{ t('ui.followEditor.enabledHint') }}</span></span>
          </label>
        </section>

        <FormError v-if="saveError" :error="saveError" :active-job-id="status.activeJob?.id" />
        <div class="flex flex-wrap justify-end gap-3">
          <RouterLink :to="followPath(original)" class="u-btn">{{ t('ui.common.cancel') }}</RouterLink>
          <button type="submit" class="u-btn u-btn-primary" :disabled="!dirty || saving || !!locked" data-testid="edit-save">
            <span v-if="saving" class="u-spinner" aria-hidden="true"></span>{{ t('ui.settings.save') }}
          </button>
        </div>
      </form>
    </template>

    <ConfirmDialog v-if="leaving" danger :title="t('ui.followEditor.leaveTitle')" :confirm-label="t('ui.settings.discard')" @confirm="leave" @close="leaving = null">
      <p class="m-0">{{ t('ui.settings.leaveText') }}</p>
    </ConfirmDialog>
  </div>
</template>
