<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import FormError from '@/ui/FormError.vue'
import { v1 } from '@/api/v1/client'
import type { FollowRecord } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { toast } from '@/ui/toast'

/**
 * A follow of the old league list (`config/leagues.txt`, origin `legacy`) can change only its sport. "Move
 * to here" moves it into the app's follows (`PATCH {origin: "api"}`, FX-19): it leaves the file, keeps its
 * name, sport and position, and every field becomes editable. Nothing is downloaded or deleted.
 */
const props = defineProps<{ follow: FollowRecord }>()
const emit = defineEmits<{ moved: [FollowRecord] }>()
const { t } = useI18n()
const status = useStatusStore()
const busy = ref(false)
const error = ref<unknown>(null)

async function move() {
  busy.value = true
  error.value = null
  try {
    const f = await v1.updateFollow(props.follow.id, { origin: 'api' })
    toast({ kind: 'ok', text: t('ui.follows.move.done', { name: f.name }) })
    emit('moved', f)
  } catch (e) {
    error.value = e
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="u-notice flex-col items-start gap-2" data-testid="follow-move">
    <p class="m-0 flex gap-2"><UiIcon name="info" :size="16" /><span>{{ t('ui.follows.move.text') }}</span></p>
    <button type="button" class="u-btn u-btn-sm" :disabled="busy" data-testid="follow-move-button" @click="move">
      <span v-if="busy" class="u-spinner" aria-hidden="true"></span>{{ t('ui.follows.move.button') }}
    </button>
    <FormError v-if="error" :error="error" :active-job-id="status.activeJob?.id" />
  </div>
</template>
