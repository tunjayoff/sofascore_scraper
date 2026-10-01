<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, type RemoteLeague } from '@/api/client'
import { useLeaguesStore } from '@/stores/leagues'
import { useSportStore } from '@/stores/sport'
import { SPORTS, sportKey, type SportKey } from '@/lib/sport'
import { toast, toastError, errorText } from '@/lib/toast'
import AppIcon from '@/components/AppIcon.vue'
import SportBadge from '@/components/SportBadge.vue'
import { trapFocus } from '@/lib/tabs'

const emit = defineEmits<{ close: []; added: [id: number] }>()
const { t } = useI18n()
const leagues = useLeaguesStore()

const q = ref('')
const results = ref<RemoteLeague[]>([])
const searching = ref(false)
const searched = ref(false)
const err = ref('')
// Start on the sport picked in the sidebar.
const sport = ref<SportKey | 'all'>(useSportStore().current)
const adding = ref<number | null>(null)
const input = ref<HTMLInputElement | null>(null)
let timer: ReturnType<typeof setTimeout> | null = null
let gen = 0

const shown = computed(() =>
  sport.value === 'all' ? results.value : results.value.filter((r) => sportKey(r.sport) === sport.value),
)
const inList = (id: number) => leagues.leagues.some((l) => l.id === id)

watch(q, (v) => {
  if (timer) clearTimeout(timer)
  err.value = ''
  if (v.trim().length < 2) {
    results.value = []
    searched.value = false
    return
  }
  timer = setTimeout(search, 350)
})

async function search() {
  const my = ++gen
  searching.value = true
  try {
    const r = await api.searchRemote(q.value.trim())
    if (my !== gen) return
    results.value = r
    searched.value = true
  } catch (e) {
    if (my === gen) err.value = errorText(e)
  } finally {
    if (my === gen) searching.value = false
  }
}

async function add(r: RemoteLeague) {
  adding.value = r.id
  try {
    await leagues.add(r)
    toast(t('add.added', { name: r.name }))
    emit('added', r.id)
    emit('close')
  } catch (e) {
    toastError(e)
  } finally {
    adding.value = null
  }
}

const dialog = ref<HTMLElement | null>(null)
// Focus goes back to whatever opened the dialog when it closes
const opener = document.activeElement as HTMLElement | null

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
  else trapFocus(e, dialog.value)
}

onMounted(() => {
  document.addEventListener('keydown', onKey)
  void nextTick(() => input.value?.focus())
})
onUnmounted(() => {
  document.removeEventListener('keydown', onKey)
  opener?.focus?.()
})
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[12vh]" style="background: var(--overlay)" @click.self="emit('close')">
    <div ref="dialog" role="dialog" aria-modal="true" aria-labelledby="add-title" class="card w-full max-w-[580px] p-6 flex flex-col gap-4" style="box-shadow: var(--shadow)">
      <div class="flex items-center justify-between">
        <h2 id="add-title" class="m-0 text-xl font-bold">{{ t('add.title') }}</h2>
        <button type="button" class="btn btn-ghost btn-icon" :aria-label="t('common.close')" @click="emit('close')"><AppIcon name="x" /></button>
      </div>

      <div>
        <label class="label" for="add-q">{{ t('add.label') }}</label>
        <div class="relative">
          <span class="absolute left-3 top-1/2 -translate-y-1/2" style="color: var(--faint)"><AppIcon name="search" :size="16" /></span>
          <input id="add-q" ref="input" v-model="q" class="field" style="padding-left: 36px" :placeholder="t('add.placeholder')" autocomplete="off" />
        </div>
      </div>

      <div class="seg" role="group" :aria-label="t('sport.all')">
        <button type="button" :class="{ 'is-active': sport === 'all' }" :aria-pressed="sport === 'all'" @click="sport = 'all'">{{ t('common.all') }}</button>
        <button v-for="s in SPORTS" :key="s" type="button" :class="{ 'is-active': sport === s }" :aria-pressed="sport === s" @click="sport = s">{{ t(`sport.${s}`) }}</button>
      </div>

      <div class="rounded-[10px] overflow-hidden" style="border: 1px solid var(--border); min-height: 120px">
        <p v-if="q.trim().length < 2" class="m-0 p-4 text-sm" style="color: var(--muted)">{{ t('add.minChars') }}</p>
        <p v-else-if="searching && !results.length" class="m-0 p-4 text-sm flex items-center gap-3" style="color: var(--muted)"><span class="spinner"></span>{{ t('add.searching') }}</p>
        <p v-else-if="err" class="m-0 p-4 text-sm" style="color: var(--danger)">{{ err }}</p>
        <p v-else-if="searched && !shown.length" class="m-0 p-4 text-sm" style="color: var(--muted)">{{ t('add.noResults') }}</p>
        <div v-else class="max-h-[340px] overflow-y-auto">
          <div v-for="r in shown" :key="r.id" class="table-row" style="grid-template-columns: minmax(0, 1fr) auto; min-height: 60px">
            <div class="min-w-0 flex flex-col gap-1">
              <span class="font-semibold truncate">{{ r.name }}</span>
              <span class="flex items-center gap-2 text-[13px]" style="color: var(--muted)">
                <SportBadge :sport="sportKey(r.sport)" />{{ r.country }}
              </span>
            </div>
            <span v-if="inList(r.id)" class="badge badge-neutral">{{ t('add.inList') }}</span>
            <button v-else type="button" class="btn btn-primary btn-sm" :disabled="adding !== null" @click="add(r)">
              {{ adding === r.id ? t('add.adding') : t('add.add') }}
            </button>
          </div>
        </div>
      </div>
      <p class="m-0 text-[13px]" style="color: var(--muted)">{{ t('add.note') }}</p>
    </div>
  </div>
</template>
