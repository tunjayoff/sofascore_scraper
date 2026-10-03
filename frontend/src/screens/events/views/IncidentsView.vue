<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { parseIncidents, type Incident } from '@/lib/matchDetail'

/**
 * Incidents as a timeline (decision 6): goals and points with the score after them, cards, substitutions,
 * period ends; home on the left, away on the right. Unknown incident types show their type code.
 */
const props = defineProps<{ payload: unknown }>()
const { t, te } = useI18n()

type Row = { minute: string; side: 'home' | 'away' | ''; kind: string; text: string }

function row(i: Incident): Row {
  const type = String(i.incidentType ?? '')
  const minute = i.time != null ? `${i.time}${i.addedTime ? `+${i.addedTime}` : ''}′` : ''
  const side = i.isHome === true ? 'home' : i.isHome === false ? 'away' : ''
  const who = i.player?.name || i.playerName || ''
  if (type === 'goal') return { minute, side, kind: 'goal', text: `${who}${i.homeScore != null ? ` · ${i.homeScore}–${i.awayScore}` : ''}` }
  if (type === 'card') {
    const red = i.incidentClass === 'red' || i.incidentClass === 'yellowRed'
    return { minute, side, kind: red ? 'red' : 'yellow', text: who }
  }
  if (type === 'substitution') return { minute, side, kind: 'sub', text: `${i.playerIn?.name ?? '—'} ↔ ${i.playerOut?.name ?? '—'}` }
  if (type === 'period') return { minute: '', side: '', kind: 'period', text: String(i.text ?? '') }
  if (type === 'varDecision') return { minute, side, kind: 'var', text: who || String(i.incidentClass ?? '') }
  return { minute, side, kind: type || 'other', text: who || String(i.text ?? '') }
}

const rows = computed(() =>
  parseIncidents(props.payload)
    .filter((i) => i?.incidentType !== 'injuryTime')
    .map(row)
    .reverse(),
)
function kindText(kind: string) {
  const key = `ui.eventDetail.incident.${kind}`
  return te(key, 'en') ? t(key) : kind
}
</script>

<template>
  <ol class="m-0 p-0 list-none flex flex-col" data-testid="view-incidents">
    <li v-for="(r, i) in rows" :key="i" class="u-incident" :class="`is-${r.side || 'mid'}`">
      <span class="u-num u-muted w-12">{{ r.minute }}</span>
      <span class="u-incident-kind" :data-kind="r.kind">{{ kindText(r.kind) }}</span>
      <span class="flex-1 min-w-0">{{ r.text }}</span>
      <span v-if="r.side" class="u-small u-muted">{{ t(`ui.eventDetail.side.${r.side}`) }}</span>
    </li>
    <li v-if="!rows.length" class="u-muted">{{ t('ui.eventDetail.noIncidents') }}</li>
  </ol>
</template>

<style>
.u-incident {
  display: flex;
  align-items: baseline;
  gap: var(--sp-3);
  padding: var(--sp-2) 0;
  border-top: 1px solid var(--line);
}
.u-incident.is-mid {
  justify-content: center;
  color: var(--muted);
  font-weight: 600;
}
.u-incident-kind {
  min-width: 96px;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--text-2);
}
.u-incident-kind[data-kind='goal'] {
  color: var(--ok-fg);
}
.u-incident-kind[data-kind='red'] {
  color: var(--danger);
}
.u-incident-kind[data-kind='yellow'] {
  color: var(--warn-fg);
}
</style>
