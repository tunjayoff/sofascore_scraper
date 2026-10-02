<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import UiDialog from '@/ui/UiDialog.vue'

/** The keyboard shortcuts (4.9), under `⋯ → Keyboard shortcuts` and on `?`. */
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()

const rows: [string, string][] = [
  ['Ctrl K', 'ui.shortcuts.search'],
  ['g o', 'ui.shortcuts.overview'],
  ['g f', 'ui.shortcuts.follows'],
  ['g e', 'ui.shortcuts.events'],
  ['g j', 'ui.shortcuts.jobs'],
  ['g s', 'ui.shortcuts.settings'],
  ['/', 'ui.shortcuts.filter'],
  ['j / k', 'ui.shortcuts.rows'],
  ['Enter', 'ui.shortcuts.open'],
  ['Esc', 'ui.shortcuts.close'],
  ['?', 'ui.shortcuts.help'],
]
</script>

<template>
  <UiDialog :title="t('ui.shortcuts.title')" @close="emit('close')">
    <p class="m-0 u-muted">{{ t('ui.shortcuts.note') }}</p>
    <table class="w-full">
      <caption class="u-sr">{{ t('ui.shortcuts.title') }}</caption>
      <tbody>
        <tr v-for="[keys, label] in rows" :key="keys" style="border-top: 1px solid var(--line)">
          <th scope="row" class="text-left py-2 pr-4 font-normal"><kbd class="u-kbd">{{ keys }}</kbd></th>
          <td class="py-2">{{ t(label) }}</td>
        </tr>
      </tbody>
    </table>
  </UiDialog>
</template>
