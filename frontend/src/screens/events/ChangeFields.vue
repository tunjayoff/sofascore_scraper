<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import UiBadge from '@/ui/UiBadge.vue'
import type { Change } from '@/api/v1/schema'

/**
 * What a correction changed (6.7): the score fields in pairs ("2-1 → 2-2" for `homeScore.current` and
 * `awayScore.current`), the status class in words, every other field as path: old → new.
 */
const props = defineProps<{ change: Change; compact?: boolean }>()
const { t } = useI18n()

const v = (x: unknown) => (x == null ? '–' : typeof x === 'object' ? JSON.stringify(x) : String(x))

const lines = computed(() => {
  const fields = props.change.fields
  const used = new Set<string>()
  const out: { key: string; label: string; text: string }[] = []
  for (const f of fields) {
    const m = f.path.match(/^homeScore\.(.+)$/)
    if (!m) continue
    const away = fields.find((x) => x.path === `awayScore.${m[1]}`)
    used.add(f.path)
    if (away) used.add(away.path)
    const label = m[1] === 'current' || m[1] === 'display' ? t('ui.corrections.score') : m[1]
    if (out.some((o) => o.label === label && label === t('ui.corrections.score'))) continue
    out.push({ key: f.path, label, text: `${v(f.old)}-${v(away?.old)} → ${v(f.new)}-${v(away?.new)}` })
  }
  for (const f of fields) {
    if (used.has(f.path)) continue
    if (f.path.startsWith('status.') && f.path !== 'status.description') continue
    used.add(f.path)
    out.push({ key: f.path, label: f.path, text: `${v(f.old)} → ${v(f.new)}` })
  }
  return out
})
const statusChanged = computed(() => props.change.old_status_class !== props.change.new_status_class)
const statusText = (c: string | null) => (c ? t(`ui.status.event.${c}`) : '–')
</script>

<template>
  <div class="flex flex-wrap items-center gap-x-4 gap-y-1" data-testid="change-fields">
    <span v-if="statusChanged" class="inline-flex items-center gap-1">
      <span class="u-small u-muted">{{ t('ui.corrections.status') }}</span>
      {{ statusText(change.old_status_class) }} → {{ statusText(change.new_status_class) }}
    </span>
    <span v-for="l in compact ? lines.slice(0, 2) : lines" :key="l.key" class="inline-flex items-center gap-1">
      <span class="u-small u-muted">{{ l.label }}</span><span class="u-num">{{ l.text }}</span>
    </span>
    <span v-if="compact && lines.length > 2" class="u-small u-muted">{{ t('ui.corrections.more', { n: lines.length - 2 }) }}</span>
    <UiBadge v-if="change.status_regressed" tone="warn" icon="alert">{{ t('ui.status.quality.regressed') }}</UiBadge>
  </div>
</template>
