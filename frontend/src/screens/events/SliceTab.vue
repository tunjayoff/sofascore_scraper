<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import EmptyState from '@/ui/EmptyState.vue'
import ErrorState from '@/ui/ErrorState.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import JsonViewer from '@/ui/JsonViewer.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import UiIcon from '@/ui/UiIcon.vue'
import { v1, V1Error } from '@/api/v1/client'
import type { Slice } from '@/api/v1/schema'
import StatisticsView from './views/StatisticsView.vue'
import LineupsView from './views/LineupsView.vue'
import IncidentsView from './views/IncidentsView.vue'
import { FRIENDLY_SPORTS } from './eventText'

/**
 * The Statistics, Line-ups and Incidents tabs (6.6, decision 6): a friendly view of the stored payload for
 * football, basketball and tennis; the raw tree for every other sport. A slice that is not stored says why
 * (no data at SofaScore, failed, not selected) with its state badge.
 */
const props = defineProps<{ eventId: number; sliceKey: 'statistics' | 'lineups' | 'incidents'; sport: string | null; home: string; away: string }>()
const emit = defineEmits<{ raw: [] }>()
const { t } = useI18n()

const slice = ref<Slice | null>(null)
const loading = ref(true)
const error = ref<unknown>(null)
let controller: AbortController | null = null

async function load() {
  controller?.abort()
  const mine = (controller = new AbortController())
  loading.value = true
  error.value = null
  try {
    slice.value = await v1.eventSlice(props.eventId, props.sliceKey, null, mine.signal)
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') return
    slice.value = null
    error.value = e
  } finally {
    if (controller === mine) loading.value = false
  }
}

const notKnown = computed(() => error.value instanceof V1Error && error.value.code === 'not_found')
const friendly = computed(() => !!props.sport && FRIENDLY_SPORTS.includes(props.sport))
const hasPayload = computed(() => slice.value?.payload != null)
const rawText = computed(() => (hasPayload.value ? JSON.stringify(slice.value!.payload) : ''))

watch(() => [props.eventId, props.sliceKey], () => void load(), { immediate: true })
onUnmounted(() => controller?.abort())
</script>

<template>
  <div class="flex flex-col gap-4" :data-testid="`slice-tab-${sliceKey}`">
    <div v-if="loading"><SkeletonBlock :lines="6" /></div>
    <EmptyState v-else-if="notKnown" icon="info" :title="t('ui.eventDetail.sliceUnknown')" :text="t('ui.eventDetail.sliceUnknownText')" />
    <ErrorState v-else-if="error" :error="error" @retry="load" />
    <template v-else-if="slice">
      <div class="flex flex-wrap items-center gap-3">
        <StatusBadge kind="slice" :value="slice.state" />
        <span v-if="slice.error" class="u-small u-muted">{{ t('ui.eventDetail.failedBecause', { reason: slice.error.http_status ?? slice.error.reason, n: slice.error.count }) }}</span>
        <span class="flex-1"></span>
        <button v-if="slice.has_payload" type="button" class="u-btn u-btn-sm" @click="emit('raw')"><UiIcon name="logs" :size="14" />{{ t('ui.eventDetail.viewRaw') }}</button>
      </div>
      <template v-if="hasPayload">
        <template v-if="friendly">
          <StatisticsView v-if="sliceKey === 'statistics'" :payload="slice.payload" :home="home" :away="away" />
          <LineupsView v-else-if="sliceKey === 'lineups'" :payload="slice.payload" :home="home" :away="away" />
          <IncidentsView v-else :payload="slice.payload" />
        </template>
        <template v-else>
          <p class="m-0 u-small u-muted">{{ t('ui.eventDetail.rawTreeNote') }}</p>
          <JsonViewer :text="rawText" :file-name="`event-${eventId}-${sliceKey}.json`" :download-url="v1.rawUrl(eventId, sliceKey)" />
        </template>
      </template>
      <p v-else class="m-0 u-muted">{{ t(`ui.eventDetail.noPayload.${slice.state}`) }}</p>
    </template>
  </div>
</template>
