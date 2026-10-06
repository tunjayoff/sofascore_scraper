<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import { startCardHidden } from '@/ui/prefs'

/**
 * "Getting started" in three steps (FX-14a): add a league, download, look at the matches or export
 * them. On Overview it is a card the user can hide (kept in this browser); Help shows the same steps and
 * can bring the card back.
 */
defineProps<{ dismissible?: boolean }>()
const emit = defineEmits<{ navigate: [] }>()
const { t } = useI18n()
</script>

<template>
  <section class="u-start" :class="{ 'u-card p-5': dismissible }" data-testid="getting-started" aria-labelledby="start-title">
    <header class="flex items-center gap-3">
      <h2 id="start-title" class="u-h3 flex-1">{{ t('ui.overview.start.title') }}</h2>
      <button
        v-if="dismissible"
        type="button"
        class="u-btn u-btn-ghost u-btn-sm u-btn-icon"
        :aria-label="t('ui.overview.start.dismiss')"
        data-testid="start-dismiss"
        @click="startCardHidden = '1'"
      >
        <UiIcon name="x" :size="16" />
      </button>
    </header>
    <ol class="u-start-steps">
      <li>
        <span class="u-step-n" aria-hidden="true">1</span>
        <span class="flex flex-col gap-1">
          <RouterLink to="/follows/new" class="font-semibold" @click="emit('navigate')">{{ t('ui.overview.start.step1') }}</RouterLink>
          <span class="u-small u-muted">{{ t('ui.overview.start.step1Text') }}</span>
        </span>
      </li>
      <li>
        <span class="u-step-n" aria-hidden="true">2</span>
        <span class="flex flex-col gap-1">
          <RouterLink to="/jobs" class="font-semibold" @click="emit('navigate')">{{ t('ui.overview.start.step2') }}</RouterLink>
          <span class="u-small u-muted">{{ t('ui.overview.start.step2Text') }}</span>
        </span>
      </li>
      <li>
        <span class="u-step-n" aria-hidden="true">3</span>
        <span class="flex flex-col gap-1">
          <span class="font-semibold">{{ t('ui.overview.start.step3') }}</span>
          <span class="u-small u-muted">{{ t('ui.overview.start.step3Text') }}</span>
          <span class="u-small flex flex-wrap gap-x-3">
            <RouterLink to="/events" @click="emit('navigate')">{{ t('ui.nav.events') }}</RouterLink>
            <RouterLink to="/exports" @click="emit('navigate')">{{ t('ui.nav.exports') }}</RouterLink>
          </span>
        </span>
      </li>
    </ol>
    <p v-if="dismissible" class="m-0 u-small u-muted">{{ t('ui.overview.start.again') }}</p>
  </section>
</template>

<style>
.u-start {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}
.u-start-steps {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: var(--sp-4);
}
@media (min-width: 900px) {
  .u-start.u-card .u-start-steps {
    grid-template-columns: repeat(3, 1fr);
  }
}
.u-start-steps li {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-3);
}
.u-start-steps .u-step-n {
  flex-shrink: 0;
  background: var(--accent);
  border-color: var(--accent);
  color: var(--on-accent);
  font-size: 0.8125rem;
  font-weight: 600;
}
</style>
