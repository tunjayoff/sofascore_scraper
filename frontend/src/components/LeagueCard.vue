<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { motion } from 'motion-v'
import type { League } from '@/api/client'

defineProps<{ league: League; active?: boolean }>()
const emit = defineEmits<{ select: []; download: []; matches: [] }>()
const { t } = useI18n()
</script>

<template>
  <motion.div
    class="panel p-4 cursor-pointer"
    :class="active ? 'ring-accent' : ''"
    :whileHover="{ y: -2, transition: { duration: 0.15 } }"
    @click="emit('select')"
  >
    <div class="flex items-start justify-between gap-3 mb-3">
      <div>
        <h3 class="font-bold tracking-tight">{{ league.name }}</h3>
        <p class="mono text-xs text-[var(--muted)] mt-1">ID {{ league.id }}</p>
      </div>
      <span v-if="active" class="chip">Selected</span>
    </div>
    <div class="flex flex-wrap gap-2">
      <button type="button" class="btn btn-primary text-sm min-h-[40px]" @click.stop="emit('download')">
        {{ t('home_download_btn') }}
      </button>
      <button type="button" class="btn btn-secondary text-sm min-h-[40px]" @click.stop="emit('matches')">
        {{ t('home_open_matches') }}
      </button>
    </div>
  </motion.div>
</template>
