<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import UiBadge from '@/ui/UiBadge.vue'
import { lookOf, type StatusKind } from '@/ui/status'

/** The badge of one status value from the vocabulary of 4.6, e.g. `<StatusBadge kind="job" value="partial" />`. */
const props = defineProps<{ kind: StatusKind; value: string }>()
const { t } = useI18n()
const look = computed(() => lookOf(props.kind, props.value))
</script>

<template>
  <UiBadge :tone="look.tone" :icon="look.icon" :spin="look.spin" :data-status="`${kind}:${value}`">{{ t(look.key) }}</UiBadge>
</template>
