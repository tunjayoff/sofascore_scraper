<script setup lang="ts">
import { computed, inject } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useLeaguesStore, type LeagueView } from '@/stores/leagues'
import { useSportStore } from '@/stores/sport'
import { num, pct } from '@/lib/format'
import { toast, toastError } from '@/lib/toast'
import AppIcon from '@/components/AppIcon.vue'
import SportBadge from '@/components/SportBadge.vue'
import SportPicker from '@/components/SportPicker.vue'

const { t } = useI18n()
const router = useRouter()
const leagues = useLeaguesStore()
const sport = useSportStore()
const openAdd = inject<() => void>('openAddLeague', () => {})

const cols = 'grid-template-columns: minmax(0, 2.2fr) minmax(0, 1fr) minmax(0, 1.4fr) auto'
const title = computed(() => (sport.current === 'all' ? t('leagues.title') : `${t(`sport.${sport.current}`)} · ${t('leagues.title')}`))
/** With one sport picked, leagues without a sport get their own box so they can be assigned. */
const unassigned = computed(() => (sport.current === 'all' ? [] : leagues.unknown))

async function remove(l: LeagueView) {
  if (!window.confirm(t('leagues.removeConfirm', { name: l.name }))) return
  try {
    await leagues.remove(l.id)
    toast(t('leagues.remove') + ': ' + l.name)
  } catch (e) {
    toastError(e)
  }
}
</script>

<template>
  <div v-if="!leagues.loaded && !leagues.error" class="flex items-center gap-3 py-16" style="color: var(--muted)"><span class="spinner"></span>{{ t('common.loading') }}</div>

  <div v-else-if="leagues.isEmpty" class="max-w-[640px] mx-auto mt-[12vh] flex flex-col items-center gap-5 text-center">
    <h1 class="m-0 text-[34px] font-bold tracking-tight">{{ t('leagues.empty.title') }}</h1>
    <p class="page-sub text-base">{{ t('leagues.empty.body') }}</p>
    <button type="button" class="btn btn-primary btn-lg" @click="openAdd()"><AppIcon name="plus" :size="16" />{{ t('leagues.empty.cta') }}</button>
  </div>

  <template v-else>
    <div class="flex flex-wrap items-end justify-between gap-4 mb-6">
      <div>
        <h1 class="page-title">{{ title }}</h1>
        <p class="page-sub">{{ t('leagues.sub') }}</p>
      </div>
      <button type="button" class="btn btn-primary" @click="openAdd()"><AppIcon name="plus" :size="16" />{{ t('leagues.add') }}</button>
    </div>

    <div v-if="!leagues.rows.length" class="card p-10 flex flex-col items-center gap-3 text-center">
      <p class="page-sub">{{ t('leagues.noneInSport') }}</p>
      <button type="button" class="btn" @click="openAdd()"><AppIcon name="plus" :size="16" />{{ t('leagues.add') }}</button>
    </div>

    <div v-else class="card overflow-hidden">
      <div class="table-head hidden lg:grid" :style="cols">
        <span>{{ t('leagues.col.league') }}</span>
        <span>{{ t('leagues.col.matches') }}</span>
        <span>{{ t('leagues.col.details') }}</span>
        <span></span>
      </div>
      <div v-for="l in leagues.rows" :key="l.id" class="table-row lg:!grid flex flex-col !items-stretch" :style="cols">
        <div class="min-w-0 flex flex-col gap-1.5">
          <span class="font-semibold truncate">{{ l.name }}</span>
          <span class="flex items-center gap-2 text-[13px]" style="color: var(--muted)">
            <SportBadge v-if="l.sport" :sport="l.sport" />
            <SportPicker v-else :id="l.id" :name="l.name" />
            <span class="mono">#{{ l.id }}</span>
          </span>
        </div>
        <span class="mono text-sm" :style="{ color: l.matches ? 'var(--text)' : 'var(--muted)' }">{{ l.matches ? num(l.matches) : t('leagues.none') }}</span>
        <span class="flex items-center gap-3">
          <span class="track flex-1 max-w-[180px]"><div :style="{ width: (l.matches ? l.coverage : 0) + '%' }"></div></span>
          <span class="mono text-[13px] w-12" style="color: var(--muted)">{{ l.matches ? pct(l.coverage) : '—' }}</span>
        </span>
        <span class="flex items-center justify-end gap-2">
          <button type="button" class="btn btn-sm" :disabled="!l.matches" @click="router.push({ path: '/matches', query: { league_id: l.id } })">{{ t('leagues.view') }}</button>
          <button type="button" class="btn btn-primary btn-sm" @click="router.push({ path: '/download', query: { league: l.id } })">{{ t('leagues.download') }}</button>
          <button type="button" class="btn btn-ghost btn-sm btn-icon" :aria-label="t('leagues.remove') + ': ' + l.name" :title="t('leagues.remove')" @click="remove(l)"><AppIcon name="trash" :size="16" /></button>
        </span>
      </div>
    </div>

    <section v-if="unassigned.length" class="card p-5 mt-6 flex flex-col gap-3">
      <div>
        <h2 class="m-0 text-base font-bold">{{ t('leagues.unknownTitle') }}</h2>
        <p class="page-sub text-sm mt-1">{{ t('leagues.unknownBody') }}</p>
      </div>
      <div v-for="l in unassigned" :key="l.id" class="flex flex-wrap items-center justify-between gap-3 py-2" style="border-top: 1px solid var(--line)">
        <span class="font-medium">{{ l.name }} <span class="mono text-[13px]" style="color: var(--muted)">#{{ l.id }}</span></span>
        <SportPicker :id="l.id" :name="l.name" />
      </div>
    </section>
  </template>
</template>
