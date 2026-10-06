<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import UiMenu, { type MenuItem } from '@/ui/UiMenu.vue'
import UiIcon from '@/ui/UiIcon.vue'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import { v1 } from '@/api/v1/client'
import type { FollowRecord } from '@/api/v1/schema'
import { toastError } from '@/api/v1/errors'
import { useStatusStore } from '@/app/statusStore'
import { watchJob } from '@/app/jobWatch'
import { toast } from '@/ui/toast'
import StartJobDialog from '@/screens/jobs/StartJobDialog.vue'
import { followPath, lockReason } from './followText'

/**
 * The actions of one follow (6.2 row menu, 6.4 header): Download now, Edit, Disable or Enable, Move to
 * here, Remove. A follow from the config file is locked and says where it is changed; a follow of the old
 * league list (leagues.txt) can only have its sport changed until it is moved here (`PATCH {origin: "api"}`,
 * FX-19). Download now downloads this follow with its own seasons (`follows: [id]`; teams, players and
 * single matches too since FX-19). Removing a league can delete its stored matches as well
 * (`delete_data=true`): the answer names the clear job, which is followed like any job.
 */
const props = defineProps<{ follow: FollowRecord; compact?: boolean }>()
const emit = defineEmits<{ changed: [FollowRecord]; removed: [] }>()
const { t, te } = useI18n()
const router = useRouter()
const status = useStatusStore()
const uid = useId()

const syncing = ref(false)
const removing = ref(false)
const deleteData = ref(false)
const busy = ref(false)
const error = ref<unknown>(null)

const lock = computed(() => lockReason(props.follow))
const editLock = computed(() => (props.follow.writable.length ? lock.value : lockReason(props.follow, 'name')))
const enableLock = computed(() => lockReason(props.follow, 'enabled'))
const canSync = computed(() => props.follow.enabled)
const syncHint = computed(() => (!props.follow.enabled ? t('ui.follows.syncDisabled') : t('ui.jobs.start.sendsRequests')))
const canDeleteData = computed(() => props.follow.kind === 'tournament')
const kindKey = computed(() => (te(`ui.follows.syncTextKind.${props.follow.kind}`, 'en') ? props.follow.kind : 'tournament'))

const items = computed<MenuItem[]>(() => [
  ...(props.compact
    ? [
        { key: 'sync', label: t('ui.follows.syncNow'), icon: 'jobs' as const, disabled: !canSync.value, hint: syncHint.value },
        { key: 'edit', label: t('ui.follows.edit'), icon: 'settings' as const, disabled: !!editLock.value, hint: editLock.value ?? undefined },
      ]
    : []),
  ...(props.follow.origin === 'legacy' ? [{ key: 'move', label: t('ui.follows.move.button'), icon: 'follows' as const, hint: t('ui.follows.move.hint') }] : []),
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

async function move() {
  try {
    const f = await v1.updateFollow(props.follow.id, { origin: 'api' })
    toast({ kind: 'ok', text: t('ui.follows.move.done', { name: f.name }) })
    emit('changed', f)
  } catch (e) {
    toastError(e, status.activeJob?.id)
  }
}

async function remove() {
  busy.value = true
  error.value = null
  try {
    const removed = await v1.removeFollow(props.follow.id, canDeleteData.value && deleteData.value)
    removing.value = false
    const job = removed.clear_job
    if (job) {
      watchJob(job)
      void status.refresh().catch(() => {})
      toast({ kind: 'ok', text: t('ui.follows.removedWithData', { name: props.follow.name }), link: { to: `/jobs/${job.id}`, label: t('ui.jobs.openJob') } })
    } else toast({ kind: 'ok', text: t('ui.follows.removedToast', { name: props.follow.name }) })
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
  else if (key === 'move') void move()
  else if (key === 'remove') {
    error.value = null
    deleteData.value = false
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
      :body="{ kind: 'sync', spec: { follows: [follow.id] } }"
      :title="t('ui.follows.syncTitle', { name: follow.name })"
      :text="t(`ui.follows.syncTextKind.${kindKey}`)"
      @close="syncing = false"
    />
    <ConfirmDialog
      v-if="removing"
      :title="t('ui.follows.removeTitle', { name: follow.name })"
      :confirm-label="deleteData ? t('ui.follows.removeWithData') : t('ui.follows.remove')"
      danger
      :typed-word="deleteData ? t('ui.maintenance.clear.word') : undefined"
      :busy="busy"
      :error="error"
      :active-job-id="status.activeJob?.id"
      @confirm="remove"
      @close="removing = false"
    >
      <p class="m-0">{{ deleteData ? t('ui.follows.removeDataText') : t('ui.follows.removeText') }}</p>
      <label v-if="canDeleteData" class="flex items-start gap-3" :for="`${uid}-data`">
        <input :id="`${uid}-data`" v-model="deleteData" type="checkbox" class="u-check mt-1" data-testid="remove-delete-data" />
        <span class="flex flex-col"
          ><span class="font-semibold">{{ t('ui.follows.deleteData') }}</span><span class="u-small u-muted">{{ t('ui.follows.deleteDataHint') }}</span></span
        >
      </label>
    </ConfirmDialog>
  </span>
</template>
