<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiMenu, { type MenuItem } from '@/ui/UiMenu.vue'
import UiIcon from '@/ui/UiIcon.vue'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import { v1 } from '@/api/v1/client'
import type { FollowRecord } from '@/api/v1/schema'
import { toastError } from '@/api/v1/errors'
import { useStatusStore } from '@/app/statusStore'
import { toast } from '@/ui/toast'
import StartJobDialog from '@/screens/jobs/StartJobDialog.vue'
import { followPath, lockReason } from './followText'

/**
 * The actions of one follow (6.2 row menu, 6.4 header): Sync now, Edit, Disable or Enable, Remove. A
 * follow from the config file is locked and says where it is changed; a follow from leagues.txt can only
 * have its sport changed here (the follow's `writable`). Sync now starts a sync of a tournament follow;
 * the job spec has no target for team, player and event follows yet (P13).
 */
const props = defineProps<{ follow: FollowRecord; compact?: boolean }>()
const emit = defineEmits<{ changed: [FollowRecord]; removed: [] }>()
const { t } = useI18n()
const router = useRouter()
const status = useStatusStore()

const syncing = ref(false)
const removing = ref(false)
const busy = ref(false)
const error = ref<unknown>(null)

const lock = computed(() => lockReason(props.follow))
const editLock = computed(() => (props.follow.writable.length ? lock.value : lockReason(props.follow, 'name')))
const enableLock = computed(() => lockReason(props.follow, 'enabled'))
const canSync = computed(() => props.follow.kind === 'tournament' && props.follow.enabled)
const syncHint = computed(() => (props.follow.kind !== 'tournament' ? t('ui.follows.syncTournamentsOnly') : !props.follow.enabled ? t('ui.follows.syncDisabled') : t('ui.jobs.start.sendsRequests')))

const items = computed<MenuItem[]>(() => [
  ...(props.compact
    ? [
        { key: 'sync', label: t('ui.follows.syncNow'), icon: 'jobs' as const, disabled: !canSync.value, hint: syncHint.value },
        { key: 'edit', label: t('ui.follows.edit'), icon: 'settings' as const, disabled: !!editLock.value, hint: editLock.value ?? undefined },
      ]
    : []),
  {
    key: 'toggle',
    label: props.follow.enabled ? t('ui.follows.disable') : t('ui.follows.enable'),
    icon: props.follow.enabled ? ('pause' as const) : ('jobs' as const),
    disabled: !!enableLock.value,
    hint: enableLock.value ?? undefined,
  },
  { key: 'remove', label: t('ui.follows.remove'), icon: 'x' as const, danger: true, disabled: !!lock.value, hint: lock.value ?? undefined },
])

async function toggle() {
  try {
    const f = await v1.updateFollow(props.follow.id, { enabled: !props.follow.enabled })
    toast({ kind: 'ok', text: f.enabled ? t('ui.follows.enabledToast', { name: f.name }) : t('ui.follows.disabledToast', { name: f.name }) })
    emit('changed', f)
  } catch (e) {
    toastError(e, status.activeJob?.id)
  }
}

async function remove() {
  busy.value = true
  error.value = null
  try {
    await v1.removeFollow(props.follow.id)
    removing.value = false
    toast({ kind: 'ok', text: t('ui.follows.removedToast', { name: props.follow.name }) })
    emit('removed')
  } catch (e) {
    error.value = e
  } finally {
    busy.value = false
  }
}

function onSelect(key: string) {
  if (key === 'sync') syncing.value = true
  else if (key === 'edit') void router.push(`${followPath(props.follow)}/edit`)
  else if (key === 'toggle') void toggle()
  else if (key === 'remove') {
    error.value = null
    removing.value = true
  }
}
</script>

<template>
  <span class="inline-flex flex-wrap items-center gap-2">
    <template v-if="!compact">
      <button type="button" class="u-btn" :disabled="!canSync" :title="syncHint" data-testid="follow-sync" @click="syncing = true"><UiIcon name="jobs" :size="16" />{{ t('ui.follows.syncNow') }}</button>
      <RouterLink v-if="!editLock" :to="`${followPath(follow)}/edit`" class="u-btn u-btn-primary" data-testid="follow-edit"><UiIcon name="settings" :size="16" />{{ t('ui.follows.edit') }}</RouterLink>
      <span v-else class="u-btn u-btn-primary" aria-disabled="true" style="opacity: 0.55; cursor: not-allowed" :title="editLock" data-testid="follow-edit-locked"><UiIcon name="lock" :size="16" />{{ t('ui.follows.edit') }}</span>
    </template>
    <UiMenu :label="t('ui.follows.actions', { name: follow.name })" icon="more" icon-only align="right" :button-class="compact ? 'u-btn u-btn-sm u-btn-ghost u-btn-icon' : 'u-btn u-btn-icon'" :items="items" @select="onSelect" />

    <StartJobDialog
      v-if="syncing"
      :body="{ kind: 'sync', spec: { league_id: follow.entity_id } }"
      :title="t('ui.follows.syncTitle', { name: follow.name })"
      :text="t('ui.follows.syncText')"
      @close="syncing = false"
    />
    <ConfirmDialog
      v-if="removing"
      :title="t('ui.follows.removeTitle', { name: follow.name })"
      :confirm-label="t('ui.follows.remove')"
      danger
      :busy="busy"
      :error="error"
      :active-job-id="status.activeJob?.id"
      @confirm="remove"
      @close="removing = false"
    >
      <p class="m-0">{{ t('ui.follows.removeText') }}</p>
    </ConfirmDialog>
  </span>
</template>
