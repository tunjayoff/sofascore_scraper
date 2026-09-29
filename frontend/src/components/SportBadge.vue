<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { SportKey } from '@/lib/sport'

defineProps<{ sport: SportKey | null }>()
const { t } = useI18n()
const tone: Record<SportKey, string> = {
  football: 'badge badge-ok',
  basketball: 'badge badge-warn',
  tennis: 'badge badge-info',
}
</script>

<template>
  <span v-if="sport" :class="tone[sport]">{{ t(`sport.${sport}`) }}</span>
</template>
