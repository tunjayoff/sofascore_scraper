<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useLeaguesStore } from '@/stores/leagues'
import { SPORTS, type SportKey } from '@/lib/sport'
import { toast, toastError } from '@/lib/toast'

/** Inline "which sport is this league?" for leagues the backend has no sport for. */
const props = defineProps<{ id: number; name: string }>()
const { t } = useI18n()
const leagues = useLeaguesStore()
const saving = ref(false)

async function pick(e: Event) {
  const v = (e.target as HTMLSelectElement).value as SportKey | ''
  if (!v) return
  saving.value = true
  try {
    await leagues.setSport(props.id, v)
    toast(t('sport.saved', { name: props.name, sport: t(`sport.${v}`) }))
  } catch (err) {
    toastError(err)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <select class="field !h-9 !w-auto !py-0 text-[13px]" :aria-label="t('sport.pick') + ': ' + name" :disabled="saving" @change="pick" @click.stop>
    <option value="">{{ t('sport.pick') }}</option>
    <option v-for="s in SPORTS" :key="s" :value="s">{{ t(`sport.${s}`) }}</option>
  </select>
</template>
