<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import UiBadge from '@/ui/UiBadge.vue'
import UiIcon from '@/ui/UiIcon.vue'
import { navItem } from '@/app/nav'

/**
 * A screen whose API routes come with P21 (05-web-ui.md 7.2): it says what the screen will do and what
 * covers the need meanwhile, instead of showing a broken page. The route's `meta.screen` names it.
 */
const { t } = useI18n()
const route = useRoute()
const key = computed(() => String(route.meta.screen ?? ''))
const item = computed(() => navItem(key.value))
</script>

<template>
  <div>
    <PageHeader :title="t(`ui.nav.${key}`)" :description="t(`ui.planned.${key}.what`)">
      <template #meta>
        <UiBadge tone="neutral" icon="planned">{{ t('ui.planned.badge', { item: item?.planned ?? 'P21' }) }}</UiBadge>
      </template>
    </PageHeader>
    <section class="u-card p-6 flex flex-col gap-4 max-w-[760px]" data-testid="planned">
      <h2 class="u-h3">{{ t('ui.planned.title') }}</h2>
      <p class="m-0">{{ t('ui.planned.why') }}</p>
      <ul class="m-0 pl-5 flex flex-col gap-1">
        <li v-for="i in 3" :key="i">{{ t(`ui.planned.${key}.p${i}`) }}</li>
      </ul>
      <p v-if="item?.classic" class="m-0 flex flex-wrap items-center gap-3">
        <span class="u-muted">{{ t('ui.planned.meanwhile') }}</span>
        <RouterLink :to="item.classic.to" class="u-btn u-btn-sm"><UiIcon name="classic" :size="14" />{{ t(`ui.planned.classic.${item.classic.label}`) }}</RouterLink>
      </p>
    </section>
  </div>
</template>
