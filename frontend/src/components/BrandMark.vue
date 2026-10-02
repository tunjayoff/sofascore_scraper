<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { noteHealth } from '@/lib/appVersion'

/** App identity plus a live "is the backend there?" line, which is what people actually need up here. */
const { t } = useI18n()
const up = ref<boolean | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

async function ping() {
  try {
    const r = await fetch('/health', { cache: 'no-store' })
    up.value = r.ok
    // The body also carries the app version (shown on Settings); a body we can't read is not "down"
    if (r.ok) noteHealth(await r.json().catch(() => null))
  } catch {
    up.value = false
  }
}

onMounted(() => {
  void ping()
  timer = setInterval(ping, 15000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <RouterLink to="/classic" class="brand" :aria-label="t('brand')">
    <svg class="mark" width="36" height="36" viewBox="0 0 36 36" aria-hidden="true">
      <rect width="36" height="36" rx="10" class="mark-bg" />
      <!-- a pitch seen from above: halfway line and centre circle, with a live trace across -->
      <rect x="8" y="9" width="20" height="18" rx="3" fill="none" class="mark-line" stroke-width="1.6" />
      <line x1="18" y1="9" x2="18" y2="27" class="mark-line" stroke-width="1.6" />
      <circle cx="18" cy="18" r="3.4" fill="none" class="mark-line" stroke-width="1.6" />
      <polyline points="5,22 12,22 15,15 20,25 23,18 31,18" fill="none" class="mark-trace" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
    <span class="min-w-0 flex flex-col">
      <span class="name">{{ t('brand') }}</span>
      <span class="status" :class="{ 'is-down': up === false }">
        <span class="dot" aria-hidden="true"></span>
        {{ up === false ? t('server.down') : up ? t('server.up') : t('brandSub') }}
      </span>
    </span>
  </RouterLink>
</template>

<style scoped>
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 6px;
  border-radius: 12px;
  color: var(--text);
  text-decoration: none;
}
.brand:hover .mark-bg {
  fill: var(--accent-hover);
}
.mark {
  flex-shrink: 0;
}
.mark-bg {
  fill: var(--accent);
  transition: fill 0.15s;
}
.mark-line {
  stroke: var(--on-accent);
  opacity: 0.45;
}
.mark-trace {
  stroke: var(--on-accent);
}
.name {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.01em;
  line-height: 1.2;
  white-space: nowrap;
}
.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
  font-size: 12px;
  color: var(--muted);
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.status.is-down {
  color: var(--danger);
}
.status.is-down .dot {
  background: var(--danger);
  box-shadow: 0 0 0 3px var(--danger-bg);
}
</style>
