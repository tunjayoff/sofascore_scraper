<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import JsonViewer from '@/ui/JsonViewer.vue'
import ErrorState from '@/ui/ErrorState.vue'
import EmptyState from '@/ui/EmptyState.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import TimeText from '@/ui/TimeText.vue'
import { v1, V1Error, type RawPayload } from '@/api/v1/client'
import type { Slice } from '@/api/v1/schema'
import { bytesText } from '@/ui/time'

/**
 * The raw view of one stored payload (6.6, decision 7): exactly as stored, with when it was read, its size
 * and its sha256 (the ETag); a tree with search, copy and a full-size download from the server. A slice
 * without a payload is "No payload stored" (a 404, never an empty JSON). A slice whose last attempt failed
 * but that holds an older payload says so.
 */
const props = defineProps<{ eventId: number; sliceKey: string; sub?: string | null; slice?: Slice | null }>()
const { t } = useI18n()

const raw = ref<RawPayload | null>(null)
const loading = ref(true)
const error = ref<unknown>(null)
let controller: AbortController | null = null

async function load() {
  controller?.abort()
  const mine = (controller = new AbortController())
  loading.value = true
  error.value = null
  raw.value = null
  try {
    raw.value = await v1.raw(props.eventId, props.sliceKey, props.sub, mine.signal)
  } catch (e) {
    if ((e as Error)?.name !== 'AbortError') error.value = e
  } finally {
    if (controller === mine) loading.value = false
  }
}

const missing = computed(() => error.value instanceof V1Error && error.value.code === 'not_found')
const sha = computed(() => raw.value?.etag?.replace(/^W\//, '').replace(/"/g, '') ?? null)
const fileName = computed(() => `event-${props.eventId}-${props.sliceKey || 'event'}${props.sub ? `-${props.sub}` : ''}.json`)
const olderPayload = computed(() => props.slice?.state === 'error' && props.slice.has_payload)

watch(() => [props.eventId, props.sliceKey, props.sub], () => void load(), { immediate: true })
onUnmounted(() => controller?.abort())
</script>

<template>
  <div class="flex flex-col gap-4" data-testid="raw-payload">
    <div v-if="loading"><SkeletonBlock :lines="8" /></div>
    <EmptyState v-else-if="missing" icon="info" :title="t('ui.raw.none')" :text="t('ui.raw.noneText')" />
    <ErrorState v-else-if="error" :error="error" @retry="load" />
    <template v-else-if="raw">
      <p class="m-0 u-small u-muted flex flex-wrap gap-x-3" data-testid="raw-facts">
        <span v-if="raw.fetchedAt">{{ t('ui.raw.fetched') }} <TimeText :value="raw.fetchedAt" /></span>
        <span>{{ bytesText(raw.bytes) }}</span>
        <span v-if="sha" class="u-mono" :title="sha">sha256 {{ sha.slice(0, 12) }}…</span>
      </p>
      <p v-if="olderPayload && slice?.error" class="m-0 u-notice u-notice-warn" role="note">
        {{ t('ui.raw.older', { reason: slice.error.http_status ?? slice.error.reason }) }}
        <TimeText :value="slice.fetched_at_utc" />
      </p>
      <JsonViewer :text="raw.text" :file-name="fileName" :download-url="v1.rawUrl(eventId, sliceKey, sub)" />
    </template>
  </div>
</template>
