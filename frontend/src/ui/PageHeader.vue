<script setup lang="ts">
import { RouterLink, type RouteLocationRaw } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'

/**
 * Title, one-line description and the page's actions (4.7). The primary action is the only filled button
 * of a page; the screen puts it in the `actions` slot with `u-btn-primary`. Detail pages pass a breadcrumb.
 */
defineProps<{ title: string; description?: string; crumbs?: { label: string; to: RouteLocationRaw }[] }>()
const { t } = useI18n()
</script>

<template>
  <header class="u-page-header">
    <div class="min-w-0 flex-1">
      <nav v-if="crumbs?.length" :aria-label="t('ui.shell.breadcrumb')" class="u-small u-muted mb-1">
        <template v-for="c in crumbs" :key="c.label">
          <RouterLink :to="c.to" class="u-crumb">{{ c.label }}</RouterLink>
          <UiIcon name="chevronRight" :size="12" class="inline mx-1" />
        </template>
      </nav>
      <h1 class="u-h1 break-words">{{ title }}</h1>
      <p v-if="description" class="m-0 mt-1 u-muted">{{ description }}</p>
      <div v-if="$slots.meta" class="mt-2 flex flex-wrap items-center gap-2"><slot name="meta" /></div>
    </div>
    <div v-if="$slots.actions" class="u-page-actions"><slot name="actions" /></div>
  </header>
</template>

<style>
.u-page-header {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: var(--sp-5);
  margin-bottom: var(--sp-6);
}
.u-page-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-3);
}
.u-app a.u-crumb {
  color: var(--muted);
  text-decoration: none;
}
.u-app a.u-crumb:hover {
  text-decoration: underline;
}
</style>
