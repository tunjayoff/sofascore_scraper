<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { playerDisplayName, shirtOf, splitLineupPlayers, type LineupPlayer } from '@/lib/matchDetail'

/** Line-ups of both sides (decision 6): formation, starters and substitutes, with shirt numbers. */
type Side = { players?: LineupPlayer[]; formation?: string }
const props = defineProps<{ payload: unknown; home: string; away: string }>()
const { t } = useI18n()
const p = computed(() => (props.payload && typeof props.payload === 'object' ? (props.payload as { home?: Side; away?: Side }) : {}))
const sides = computed(() =>
  [
    { key: 'home', name: props.home, side: p.value.home },
    { key: 'away', name: props.away, side: p.value.away },
  ].map((s) => ({ ...s, ...splitLineupPlayers(s.side?.players), formation: s.side?.formation ?? null })),
)
</script>

<template>
  <div class="grid gap-6 md:grid-cols-2" data-testid="view-lineups">
    <section v-for="s in sides" :key="s.key" class="flex flex-col gap-2">
      <h3 class="u-h3 flex items-baseline gap-2">{{ s.name }} <span v-if="s.formation" class="u-small u-muted">{{ s.formation }}</span></h3>
      <p class="m-0 u-caption">{{ t('ui.eventDetail.starters') }}</p>
      <ol class="m-0 p-0 list-none">
        <li v-for="(pl, i) in s.starters" :key="i" class="flex gap-3 py-1" style="border-top: 1px solid var(--line)">
          <span class="u-num u-muted w-8 text-right">{{ shirtOf(pl) }}</span><span>{{ playerDisplayName(pl) }}</span>
        </li>
      </ol>
      <template v-if="s.subs.length">
        <p class="m-0 mt-2 u-caption">{{ t('ui.eventDetail.substitutes') }}</p>
        <ol class="m-0 p-0 list-none">
          <li v-for="(pl, i) in s.subs" :key="i" class="flex gap-3 py-1 u-muted" style="border-top: 1px solid var(--line)">
            <span class="u-num w-8 text-right">{{ shirtOf(pl) }}</span><span>{{ playerDisplayName(pl) }}</span>
          </li>
        </ol>
      </template>
    </section>
  </div>
</template>
