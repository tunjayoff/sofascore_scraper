<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { motion, AnimatePresence } from 'motion-v'
import { useScrapeStore } from '@/stores/scrape'
import { useMotionPrefs } from '@/composables/useMotionPrefs'

const { t } = useI18n()
const scrape = useScrapeStore()
const { reduce } = useMotionPrefs()
</script>

<template>
  <AnimatePresence>
    <!-- Compact chip -->
    <motion.div
      v-if="scrape.barMode === 'mini'"
      key="progress-mini"
      class="fixed bottom-3 right-3 z-50 md:bottom-5 md:right-6"
      :initial="reduce ? false : { opacity: 0, y: 12, scale: 0.96 }"
      :animate="{ opacity: 1, y: 0, scale: 1 }"
      :exit="reduce ? undefined : { opacity: 0, y: 8, scale: 0.96 }"
      :transition="{ type: 'spring', stiffness: 400, damping: 32 }"
    >
      <div class="panel ring-accent px-3 py-2 flex items-center gap-2 shadow-lg">
        <button
          type="button"
          class="text-sm font-bold tracking-tight flex items-center gap-2 min-h-[36px]"
          :title="t('progress_expand')"
          @click="scrape.expand()"
        >
          <span
            class="inline-block w-2 h-2 rounded-full"
            :class="scrape.isRunning ? 'bg-[var(--accent)]' : scrape.state.status === 'Failed' ? 'bg-[var(--danger)]' : 'bg-[var(--muted)]'"
          />
          <span class="mono tabular-nums link-accent">{{ scrape.state.progress || 0 }}%</span>
          <span class="text-[var(--muted)] font-semibold max-w-[9rem] truncate">
            <template v-if="scrape.isRunning">{{ t('progress_title') }}</template>
            <template v-else>{{ t('home_job_done') }}</template>
          </span>
        </button>
        <button
          v-if="scrape.isRunning && !scrape.state.cancel_requested"
          type="button"
          class="btn btn-danger text-sm min-h-[36px] px-3"
          @click="scrape.cancel()"
        >
          {{ t('home_stop') }}
        </button>
        <button
          v-else-if="!scrape.isRunning"
          type="button"
          class="btn btn-secondary text-sm min-h-[36px] px-3"
          :title="t('progress_dismiss')"
          @click="scrape.dismiss()"
        >
          ×
        </button>
      </div>
    </motion.div>

    <!-- Full dock -->
    <motion.div
      v-else-if="scrape.barMode === 'full'"
      key="progress-dock"
      class="fixed inset-x-0 bottom-0 z-50 px-3 pb-3 md:px-6 md:pb-5 pointer-events-none"
      :initial="reduce ? false : { opacity: 0, y: 20 }"
      :animate="{ opacity: 1, y: 0 }"
      :exit="reduce ? undefined : { opacity: 0, y: 12 }"
      :transition="{ type: 'spring', stiffness: 380, damping: 34 }"
    >
      <div class="pointer-events-auto max-w-3xl mx-auto panel ring-accent p-4 flex flex-col sm:flex-row sm:items-center gap-3">
        <div class="flex-1 min-w-0">
          <div class="flex items-center justify-between gap-3 mb-2">
            <span class="text-sm font-bold tracking-tight">
              <template v-if="scrape.state.cancel_requested && scrape.isRunning">{{ t('home_stop') }}…</template>
              <template v-else-if="scrape.isRunning">{{ t('progress_title') }}</template>
              <template v-else-if="scrape.state.status === 'Failed'">!</template>
              <template v-else>{{ t('home_job_done') }}</template>
            </span>
            <span class="mono text-sm font-bold tabular-nums link-accent">
              {{ scrape.state.progress || 0 }}%
            </span>
          </div>
          <div class="h-2 rounded-full bg-[var(--surface-2)] overflow-hidden">
            <motion.div
              class="h-full rounded-full"
              :class="scrape.state.status === 'Failed' ? 'bg-[var(--danger)]' : 'bg-[var(--accent)]'"
              :animate="{ width: `${scrape.state.progress || 0}%` }"
              :transition="{ type: 'spring', stiffness: 120, damping: 24 }"
            />
          </div>
          <p class="text-xs text-[var(--muted)] mt-2 truncate">{{ scrape.state.current_task }}</p>
        </div>
        <div class="flex flex-wrap gap-2 shrink-0">
          <button
            type="button"
            class="btn btn-secondary text-sm min-h-[40px] px-3"
            :title="t('progress_minimize')"
            @click="scrape.minimize()"
          >
            {{ t('progress_minimize') }}
          </button>
          <button
            v-if="scrape.isRunning && !scrape.state.cancel_requested"
            type="button"
            class="btn btn-danger"
            @click="scrape.cancel()"
          >
            {{ t('home_stop') }}
          </button>
          <button
            v-else-if="!scrape.isRunning"
            type="button"
            class="btn btn-secondary"
            @click="scrape.dismiss()"
          >
            {{ t('progress_dismiss') }}
          </button>
        </div>
      </div>
    </motion.div>
  </AnimatePresence>
</template>
