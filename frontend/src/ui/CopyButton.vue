<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import { copyText } from '@/ui/focus'

/** A small button that copies `text`; says "Copied" for a moment. `label` names what is copied. */
const props = defineProps<{ text: string; label: string }>()
const { t } = useI18n()
const done = ref(false)

async function copy() {
  done.value = await copyText(props.text)
  if (done.value) setTimeout(() => (done.value = false), 1500)
}
</script>

<template>
  <button type="button" class="u-btn u-btn-ghost u-btn-sm u-btn-icon" :aria-label="done ? t('ui.common.copied') : label" :title="label" @click="copy">
    <UiIcon :name="done ? 'check' : 'copy'" :size="14" />
  </button>
</template>
