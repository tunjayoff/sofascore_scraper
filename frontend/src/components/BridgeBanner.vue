<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { matchDate } from '@/lib/format'
import AppIcon from '@/components/AppIcon.vue'

/** "SofaScore is blocking us": shown while the browser bridge is not healthy; dismissible. */
const { t } = useI18n()
const bridge = useBridgeStore()

const h = computed(() => bridge.health)
const blocked = computed(() => h.value?.state === 'blocked')
const kind = computed(() => {
  const k = h.value?.last_error?.kind
  return k === 'challenge' || k === 'browser' ? k : 'forbidden'
})
const title = computed(() =>
  kind.value === 'browser' ? t('bridge.browserTitle') : t(blocked.value ? 'bridge.blockedTitle' : 'bridge.degradedTitle'),
)
const body = computed(() =>
  t(blocked.value ? 'bridge.blockedBody' : 'bridge.degradedBody', { count: h.value?.consecutive_failures ?? 0 }),
)
const lastSuccess = computed(() =>
  h.value?.last_success_at ? t('bridge.lastSuccess', { time: matchDate(h.value.last_success_at) }) : t('bridge.never'),
)
</script>

<template>
  <div
    v-if="bridge.visible"
    :role="blocked ? 'alert' : 'status'"
    class="bridge-banner mb-6"
    :class="{ 'is-blocked': blocked }"
    data-testid="bridge-banner"
  >
    <span class="shrink-0 mt-[2px]"><AppIcon name="alert" /></span>
    <div class="flex-1 min-w-0 text-sm leading-snug">
      <p class="font-semibold">{{ title }}</p>
      <p class="mt-1">{{ body }}</p>
      <p class="mt-1 opacity-80">{{ t(`bridge.reason.${kind}`) }} {{ lastSuccess }}</p>
    </div>
    <button type="button" class="btn btn-ghost btn-sm shrink-0" :aria-label="t('bridge.dismiss')" @click="bridge.dismiss()">
      <AppIcon name="x" :size="14" />
    </button>
  </div>
</template>

<style scoped>
.bridge-banner {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 16px;
  border-radius: 10px;
  background: var(--warn-bg);
  color: var(--warn-fg);
}
.bridge-banner.is-blocked {
  background: var(--danger-bg);
  color: var(--danger);
}
.bridge-banner .btn {
  min-height: 28px;
  padding: 0 6px;
  color: inherit;
}
</style>
