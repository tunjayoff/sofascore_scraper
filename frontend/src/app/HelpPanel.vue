<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import SidePanel from '@/ui/SidePanel.vue'
import UiIcon from '@/ui/UiIcon.vue'
import GettingStarted from '@/app/GettingStarted.vue'
import { HELP_TERMS, readmeUrl } from '@/app/help'
import { startCardHidden } from '@/ui/prefs'

/**
 * Help (FX-14a): what the app does, the words it uses, the three getting-started steps (and the way back
 * to the Overview card), the keyboard shortcuts and the project's README on GitHub (a link the user
 * opens; the app itself sends nothing there).
 */
const emit = defineEmits<{ close: []; shortcuts: [] }>()
const { t, locale } = useI18n()
</script>

<template>
  <SidePanel :title="t('ui.help.title')" @close="emit('close')">
    <div class="flex flex-col gap-6" data-testid="help-panel">
      <p class="m-0">{{ t('ui.help.intro') }}</p>

      <GettingStarted @navigate="emit('close')" />
      <div v-if="startCardHidden === '1'">
        <button type="button" class="u-btn u-btn-sm" data-testid="help-show-start" @click="startCardHidden = '0'">{{ t('ui.help.showStart') }}</button>
      </div>

      <section class="flex flex-col gap-3" aria-labelledby="help-glossary">
        <h3 id="help-glossary" class="u-h3">{{ t('ui.help.glossary') }}</h3>
        <dl class="u-glossary">
          <template v-for="term in HELP_TERMS" :key="term">
            <dt :data-term="term">{{ t(`ui.help.term.${term}.name`) }}</dt>
            <dd>{{ t(`ui.help.term.${term}.text`) }}</dd>
          </template>
        </dl>
      </section>

      <div class="flex flex-col gap-2 items-start">
        <button type="button" class="u-btn u-btn-sm" @click="emit('shortcuts')"><UiIcon name="keyboard" :size="14" />{{ t('ui.menu.shortcuts') }}</button>
        <a :href="readmeUrl(locale)" target="_blank" rel="noopener noreferrer" class="u-btn u-btn-sm" data-testid="help-readme">
          <UiIcon name="external" :size="14" />{{ t('ui.help.readme') }}
        </a>
        <span class="u-small u-muted">{{ t('ui.help.readmeHint') }}</span>
      </div>
    </div>
  </SidePanel>
</template>

<style>
.u-glossary {
  margin: 0;
}
.u-glossary dt {
  font-weight: 600;
  margin-top: var(--sp-4);
}
.u-glossary dt:first-child {
  margin-top: 0;
}
.u-glossary dd {
  margin: var(--sp-1) 0 0;
  color: var(--text-2);
}
</style>
