<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import RawPayload from './RawPayload.vue'
import { sliceLabel } from './eventText'

/** The raw view as a page of its own, `/events/:id/raw/:key[/:sub]` (3.2): the same view as the side panel. */
const { t } = useI18n()
const route = useRoute()
const id = computed(() => Number(route.params.id))
const key = computed(() => String(route.params.key))
const sub = computed(() => (typeof route.params.sub === 'string' && route.params.sub ? route.params.sub : null))
</script>

<template>
  <div>
    <PageHeader
      :title="t('ui.raw.title', { key: sliceLabel(key) })"
      :crumbs="[
        { label: t('ui.nav.events'), to: '/events' },
        { label: `#${id}`, to: `/events/${id}?tab=data` },
      ]"
    />
    <section class="u-card p-5"><RawPayload :event-id="id" :slice-key="key" :sub="sub" /></section>
  </div>
</template>
