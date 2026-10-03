<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import UiIcon from '@/ui/UiIcon.vue'
import EventsList from './EventsList.vue'

/** Events (6.5): find stored matches, see their state, fetch what is missing. */
const { t } = useI18n()
const listRef = ref<InstanceType<typeof EventsList> | null>(null)
</script>

<template>
  <div>
    <PageHeader :title="t('ui.nav.events')" :description="t('ui.events.description')">
      <template #actions>
        <button type="button" class="u-btn" @click="listRef?.reload()"><UiIcon name="refresh" :size="16" />{{ t('ui.common.refresh') }}</button>
      </template>
    </PageHeader>
    <p class="m-0 mb-4 u-small u-muted">{{ t('ui.events.notLive') }}</p>
    <EventsList ref="listRef" />
  </div>
</template>
