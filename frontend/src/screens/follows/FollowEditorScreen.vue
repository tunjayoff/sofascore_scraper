<script setup lang="ts">
import { computed, onMounted, ref, useId, watch } from 'vue'
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
import type { FollowPatch, FollowRecord, Season, TournamentHit } from '@/api/v1/schema'
import { loadSports, sportName, sports } from '@/app/sports'
import { useStatusStore } from '@/app/statusStore'
import { toast } from '@/ui/toast'
import { toastError } from '@/api/v1/errors'
import { startJob } from '@/screens/jobs/startJob'
import SlicePicker from './SlicePicker.vue'
import { FOLLOW_KINDS, followPath, lockReason, seasonsText, type FollowKind } from './followText'

/**
 * The follow editor (6.3). A new follow in four steps: what (a tournament by search at SofaScore or by id;
 * a team, a player or one event by id), seasons, data selection, review. A change shows the same sections
 * on one page; a field this follow cannot change here (`writable`) is locked with the reason. The data
 * selection is shown read-only while the follows API takes none (P27). Saving can start a sync.
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

// ---- search at SofaScore (tournaments only; one request per click) ----
const query = ref('')
const searching = ref(false)
const hits = ref<TournamentHit[] | null>(null)
const searchError = ref<unknown>(null)
const pickedHit = ref<number | null>(null)

async function searchTournaments() {
  if (!query.value.trim()) return
  searching.value = true
  searchError.value = null
  try {
    hits.value = await v1.searchTournaments({ q: query.value.trim(), sport: sport.value || null })
  } catch (e) {
    hits.value = null
    searchError.value = e
  } finally {
    searching.value = false
  }
}
function pick(hit: TournamentHit) {
  pickedHit.value = hit.id
  entityId.value = String(hit.id)
  name.value = hit.name
  if (hit.sport) sport.value = hit.sport
}

// ---- seasons stored for the tournament ----
const seasons = ref<Season[] | null>(null)
const seasonsError = ref<unknown>(null)
watch([step, entityId, kind], () => {
  if (step.value === 2 && kind.value === 'tournament' && idValue.value) loadSeasons()
})
function loadSeasons() {
  seasons.value = null
  seasonsError.value = null
  v1.tournamentSeasons(idValue.value!)
    .then((s) => (seasons.value = s))
    .catch((e) => {
      if (e instanceof V1Error && e.code === 'not_found') seasons.value = []
      else seasonsError.value = e
    })
}

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

// ---- save ----
const saving = ref(false)
const saveError = ref<unknown>(null)
const exists = computed(() => saveError.value instanceof V1Error && saveError.value.code === 'follow_exists')
let saved = false

async function create() {
  saving.value = true
  saveError.value = null
  try {
    const f = await v1.addFollow({ kind: kind.value, entity_id: idValue.value!, name: name.value.trim(), sport: sport.value || null, seasons: seasonsValue.value, live: live.value })
    saved = true
    toast({ kind: 'ok', text: t('ui.followEditor.added', { name: f.name }), link: { to: followPath(f), label: t('ui.followEditor.open') } })
    if (syncAfter.value && f.kind === 'tournament') await startJob({ kind: 'sync', spec: { league_id: f.entity_id } }).catch((e) => toastError(e, status.activeJob?.id))
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
  if (JSON.stringify(seasonsValue.value) !== JSON.stringify(o.seasons)) p.seasons = seasonsValue.value
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
    if (original.value.kind === 'tournament') loadSeasons()
  } catch (e) {
    loadError.value = e
  } finally {
    loading.value = false
  }
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

onMounted(() => {
  void loadSports().catch(() => {})
  if (editing.value) void loadFollow()
  else {
    const k = String(route.query.kind ?? '')
    if ((FOLLOW_KINDS as readonly string[]).includes(k)) kind.value = k as FollowKind
    if (typeof route.query.id === 'string') entityId.value = route.query.id
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
            <label v-for="k in FOLLOW_KINDS" :key="k" class="u-option">
              <input v-model="kind" type="radio" :name="`${uid}-kind`" :value="k" class="u-check" />{{ t(`ui.follows.kind.${k}`) }}
            </label>
          </fieldset>
          <label class="flex flex-col max-w-[320px]">
            <span class="u-label">{{ t('ui.follows.col.sport') }}</span>
            <select v-model="sport" class="u-field" data-testid="editor-sport">
              <option value="">{{ t('ui.followEditor.anySport') }}</option>
              <option v-for="s in sports" :key="s.slug" :value="s.slug">{{ sportName(s.slug) }}</option>
            </select>
          </label>

          <div v-if="kind === 'tournament'" class="flex flex-col gap-2">
            <form class="flex flex-wrap items-end gap-3" @submit.prevent="searchTournaments">
              <label class="flex flex-col flex-1 min-w-[220px]">
                <span class="u-label">{{ t('ui.followEditor.searchLabel') }}</span>
                <input v-model="query" type="search" class="u-field" autocomplete="off" data-testid="editor-query" />
              </label>
              <button type="submit" class="u-btn" :disabled="searching || !query.trim()" data-testid="editor-search">
                <span v-if="searching" class="u-spinner" aria-hidden="true"></span><UiIcon v-else name="search" :size="16" />{{ t('ui.followEditor.search') }}
              </button>
            </form>
            <p class="m-0 u-small u-muted flex items-center gap-2"><UiIcon name="external" :size="14" />{{ t('ui.followEditor.searchNote') }}</p>
            <FormError v-if="searchError" :error="searchError" />
            <p v-else-if="hits && !hits.length" class="m-0 u-muted" data-testid="editor-no-hits">{{ t('ui.followEditor.noHits') }}</p>
            <fieldset v-else-if="hits" class="flex flex-col gap-1" data-testid="editor-hits">
              <legend class="u-sr">{{ t('ui.followEditor.hits') }}</legend>
              <label v-for="h in hits" :key="h.id" class="u-option">
                <input type="radio" :name="`${uid}-hit`" class="u-check" :checked="pickedHit === h.id" :value="h.id" @change="pick(h)" />
                <span class="flex-1 flex flex-wrap items-baseline gap-x-3">
                  <span class="font-semibold">{{ h.name }}</span>
                  <span class="u-small u-muted">{{ h.category.name ?? '' }}</span>
                  <span class="u-small u-muted u-mono">#{{ h.id }}</span>
                  <span v-if="h.sport" class="u-small u-muted">{{ sportName(h.sport) }}</span>
                </span>
                <UiBadge v-if="h.followed" tone="ok" icon="check">{{ t('ui.followEditor.alreadyFollowed') }}</UiBadge>
              </label>
            </fieldset>
          </div>

          <div class="grid gap-4 sm:grid-cols-2">
            <label class="flex flex-col">
              <span class="u-label">{{ kind === 'tournament' ? t('ui.followEditor.orId') : t('ui.followEditor.id', { kind: t(`ui.follows.kind.${kind}`) }) }}</span>
              <input v-model="entityId" class="u-field u-mono" inputmode="numeric" autocomplete="off" data-testid="editor-id" :aria-invalid="!!entityId && !idValue" />
            </label>
            <label class="flex flex-col">
              <span class="u-label">{{ t('ui.followEditor.name') }}</span>
              <input v-model="name" class="u-field" autocomplete="off" data-testid="editor-name" />
            </label>
          </div>
          <p v-if="kind !== 'tournament'" class="m-0 u-small u-muted">{{ t('ui.followEditor.byIdNote') }}</p>
        </template>

        <!-- 2: seasons -->
        <template v-else-if="step === 2">
          <p v-if="kind !== 'tournament'" class="m-0 u-muted">{{ t('ui.followEditor.seasonsTournamentOnly') }}</p>
          <fieldset v-else class="flex flex-col gap-2" data-testid="editor-seasons">
            <legend class="u-label">{{ t('ui.followEditor.seasons') }}</legend>
            <label class="u-option"><input v-model="seasonMode" type="radio" :name="`${uid}-s`" value="current" class="u-check" />{{ t('ui.follows.seasons.current') }}</label>
            <label class="u-option items-center">
              <input v-model="seasonMode" type="radio" :name="`${uid}-s`" value="last" class="u-check" />
              <span class="inline-flex items-center gap-2">{{ t('ui.followEditor.lastN') }}<input v-model.number="lastN" type="number" min="1" max="50" class="u-field" style="width: 80px; height: 32px" :aria-label="t('ui.followEditor.lastNLabel')" @focus="seasonMode = 'last'" /></span>
            </label>
            <label class="u-option"><input v-model="seasonMode" type="radio" :name="`${uid}-s`" value="all" class="u-check" />{{ t('ui.follows.seasons.all') }}</label>
            <label class="u-option"><input v-model="seasonMode" type="radio" :name="`${uid}-s`" value="choose" class="u-check" />{{ t('ui.followEditor.choose') }}</label>
            <div v-if="seasonMode === 'choose'" class="pl-6 flex flex-col gap-2">
              <ErrorState v-if="seasonsError" compact :error="seasonsError" @retry="loadSeasons" />
              <SkeletonBlock v-else-if="!seasons" :lines="3" />
              <p v-else-if="!seasons.length" class="m-0 u-small u-muted">{{ t('ui.followEditor.noSeasonList') }}</p>
              <label v-for="s in seasons ?? []" :key="s.id" class="flex items-center gap-2">
                <input v-model="chosenSeasons" type="checkbox" class="u-check" :value="s.id" />{{ s.year ?? s.name ?? s.id }} <span class="u-small u-muted u-mono">#{{ s.id }}</span>
              </label>
            </div>
          </fieldset>
        </template>

        <!-- 3: data -->
        <SlicePicker v-else-if="step === 3" :sport="sport || null" :selection="null" />

        <!-- 4: review -->
        <template v-else>
          <dl class="u-result" data-testid="editor-review">
            <dt>{{ t('ui.followEditor.kind') }}</dt>
            <dd>{{ t(`ui.follows.kind.${kind}`) }} <span class="u-mono u-muted">#{{ idValue }}</span></dd>
            <dt>{{ t('ui.follows.col.sport') }}</dt>
            <dd>{{ sport ? sportName(sport) : '—' }}</dd>
            <dt>{{ t('ui.follows.col.seasons') }}</dt>
            <dd>{{ kind === 'tournament' ? seasonsText(seasonsValue) : '—' }}</dd>
            <dt>{{ t('ui.follows.col.data') }}</dt>
            <dd>{{ t('ui.follows.data.defaults') }}</dd>
          </dl>
          <label class="flex flex-col max-w-[480px]">
            <span class="u-label">{{ t('ui.followEditor.name') }}</span>
            <input v-model="name" class="u-field" autocomplete="off" />
          </label>
          <label class="flex items-start gap-3">
            <input v-model="live" type="checkbox" class="u-check mt-1" />
            <span class="flex flex-col"><span class="font-semibold">{{ t('ui.followEditor.live') }}</span><span class="u-small u-muted">{{ t('ui.followEditor.liveHint') }}</span></span>
          </label>
          <label v-if="kind === 'tournament'" class="flex items-start gap-3">
            <input v-model="syncAfter" type="checkbox" class="u-check mt-1" data-testid="editor-sync-after" />
            <span class="flex flex-col"><span class="font-semibold">{{ t('ui.followEditor.syncAfter') }}</span><span class="u-small u-muted">{{ t('ui.jobs.start.sendsRequests') }}</span></span>
          </label>
          <FormError v-if="saveError" :error="saveError" :active-job-id="status.activeJob?.id" />
          <p v-if="exists && idValue" class="m-0"><RouterLink :to="`/follows/${kind}/${idValue}`" class="font-semibold">{{ t('ui.followEditor.openExisting') }}</RouterLink></p>
        </template>

        <div class="flex flex-wrap justify-end gap-3 pt-2" style="border-top: 1px solid var(--line)">
          <RouterLink v-if="step === 1" to="/follows" class="u-btn">{{ t('ui.common.cancel') }}</RouterLink>
          <button v-else type="button" class="u-btn" :disabled="saving" @click="step = (step - 1) as 1 | 2 | 3">{{ t('ui.restore.back') }}</button>
          <button v-if="step < 4" type="button" class="u-btn u-btn-primary" :disabled="!stepValid" data-testid="editor-next" @click="step = (step + 1) as 2 | 3 | 4">{{ t('ui.restore.next') }}</button>
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

        <section v-if="original.kind === 'tournament'" class="u-card p-6 flex flex-col gap-3">
          <h2 class="u-h3">{{ t('ui.followEditor.step2') }}</h2>
          <fieldset class="flex flex-col gap-2" :disabled="!!fieldLock('seasons')">
            <legend class="u-sr">{{ t('ui.followEditor.seasons') }}</legend>
            <label class="u-option"><input v-model="seasonMode" type="radio" :name="`${uid}-es`" value="current" class="u-check" />{{ t('ui.follows.seasons.current') }}</label>
            <label class="u-option items-center">
              <input v-model="seasonMode" type="radio" :name="`${uid}-es`" value="last" class="u-check" />
              <span class="inline-flex items-center gap-2">{{ t('ui.followEditor.lastN') }}<input v-model.number="lastN" type="number" min="1" max="50" class="u-field" style="width: 80px; height: 32px" :aria-label="t('ui.followEditor.lastNLabel')" /></span>
            </label>
            <label class="u-option"><input v-model="seasonMode" type="radio" :name="`${uid}-es`" value="all" class="u-check" />{{ t('ui.follows.seasons.all') }}</label>
            <label class="u-option"><input v-model="seasonMode" type="radio" :name="`${uid}-es`" value="choose" class="u-check" />{{ t('ui.followEditor.choose') }}</label>
            <div v-if="seasonMode === 'choose'" class="pl-6 flex flex-col gap-2">
              <SkeletonBlock v-if="!seasons && !seasonsError" :lines="3" />
              <p v-else-if="!seasons?.length" class="m-0 u-small u-muted">{{ t('ui.followEditor.noSeasonList') }}</p>
              <label v-for="s in seasons ?? []" :key="s.id" class="flex items-center gap-2">
                <input v-model="chosenSeasons" type="checkbox" class="u-check" :value="s.id" />{{ s.year ?? s.name ?? s.id }}
              </label>
            </div>
          </fieldset>
        </section>

        <section class="u-card p-6 flex flex-col gap-3">
          <h2 class="u-h3">{{ t('ui.followEditor.step3') }}</h2>
          <SlicePicker :sport="sport || null" :selection="original.slices ?? null" />
          <p class="m-0 u-small u-muted">{{ t('ui.followEditor.changeNote') }}</p>
        </section>

        <section class="u-card p-6 flex flex-col gap-3">
          <h2 class="u-h3">{{ t('ui.followEditor.more') }}</h2>
          <label class="flex items-start gap-3">
            <input v-model="live" type="checkbox" class="u-check mt-1" :disabled="!!fieldLock('live')" />
            <span class="flex flex-col"><span class="font-semibold">{{ t('ui.followEditor.live') }}</span><span class="u-small u-muted">{{ t('ui.followEditor.liveHint') }}</span></span>
          </label>
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
