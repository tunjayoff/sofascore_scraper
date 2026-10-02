<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter, type RouteLocationRaw } from 'vue-router'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import UiTabs from '@/ui/UiTabs.vue'
import ErrorState from '@/ui/ErrorState.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import FactList from '@/ui/FactList.vue'
import EmptyState from '@/ui/EmptyState.vue'
import { v1, V1Error } from '@/api/v1/client'
import { describeError, fieldErrors } from '@/api/v1/errors'
import type { SettingsDocument } from '@/api/v1/schema'
import { useStatusStore } from '@/app/statusStore'
import { toast } from '@/ui/toast'
import SettingRow from './SettingRow.vue'
import BrowserSettings from './BrowserSettings.vue'
import { DATA_DIR, metaOf, SECTIONS, type Section } from './settingsMeta'

/**
 * Settings (6.16): the server's settings by section, each with where its value comes from; locked and
 * read-only ones in place with the reason (decision 21). Changes are collected and saved all or nothing
 * (`PATCH /api/v1/settings`); a refused save marks the rows it names and saves nothing. "Reset" removes
 * the value written here, so the weaker layer applies. "This browser" holds what only this browser keeps.
 */
type Tab = Section | 'browser'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const status = useStatusStore()

const doc = ref<SettingsDocument | null>(null)
const loading = ref(true)
const loadError = ref<unknown>(null)
const staged = ref<Record<string, unknown>>({})
const saving = ref(false)
const saveError = ref<unknown>(null)
const problems = ref<Record<string, string>>({})
const confirmDir = ref(false)
const leaving = ref<RouteLocationRaw | null>(null)

const count = computed(() => Object.keys(staged.value).length)

const bySection = computed(() => {
  const out: Record<string, SettingsDocument['settings']> = {}
  for (const s of doc.value?.settings ?? []) (out[metaOf(s.key).section] ||= []).push(s)
  return out
})
const tabs = computed(() => {
  const list: { key: Tab; label: string; badge?: string }[] = SECTIONS.filter((s) => s !== 'other' || bySection.value.other?.length).map((s) => {
    const changes = (bySection.value[s] ?? []).filter((x) => x.key in staged.value).length
    return { key: s, label: t(`ui.settings.section.${s}`), badge: changes ? String(changes) : undefined }
  })
  list.push({ key: 'browser', label: t('ui.settings.section.browser') })
  return list
})
const tab = computed<Tab>({
  get: () => {
    const q = String(route.query.tab ?? 'requests')
    return (tabs.value.some((x) => x.key === q) ? q : 'requests') as Tab
  },
  set: (v) => void router.replace({ query: { ...route.query, tab: v === 'requests' ? undefined : v } }),
})
const rows = computed(() => (tab.value === 'browser' ? [] : (bySection.value[tab.value] ?? [])))

async function load() {
  loading.value = true
  try {
    doc.value = await v1.settings()
    loadError.value = null
  } catch (e) {
    loadError.value = e
  } finally {
    loading.value = false
  }
}

function stage(key: string, value: unknown) {
  staged.value = { ...staged.value, [key]: value }
  const { [key]: _gone, ...rest } = problems.value
  void _gone
  problems.value = rest
}
function unstage(key: string) {
  const { [key]: _gone, ...rest } = staged.value
  void _gone
  staged.value = rest
}
function discardAll() {
  staged.value = {}
  problems.value = {}
  saveError.value = null
}

/** The rows a refused save names: `details.locked`, `details.read_only`, or the fields of a 422. */
function markProblems(e: unknown) {
  const out: Record<string, string> = { ...fieldErrors(e) }
  if (e instanceof V1Error && e.code === 'invalid_request') {
    const locked = e.details?.locked
    if (Array.isArray(locked)) for (const l of locked) if (l && typeof l === 'object' && 'key' in l) out[String(l.key)] = t('ui.settings.refusedLocked')
    const readOnly = e.details?.read_only
    if (Array.isArray(readOnly)) for (const k of readOnly) out[String(k)] = t('ui.settings.refusedReadOnly')
  }
  problems.value = out
}

function save() {
  if (!count.value || saving.value) return
  if (DATA_DIR in staged.value && !confirmDir.value) {
    confirmDir.value = true
    return
  }
  void doSave()
}

async function doSave() {
  saving.value = true
  saveError.value = null
  try {
    doc.value = await v1.updateSettings(staged.value)
    staged.value = {}
    problems.value = {}
    confirmDir.value = false
    toast({ kind: 'ok', text: t('ui.settings.saved') })
  } catch (e) {
    saveError.value = e
    markProblems(e)
    confirmDir.value = false
  } finally {
    saving.value = false
  }
}

const saveErrorView = computed(() => (saveError.value ? describeError(saveError.value) : null))

onBeforeRouteLeave((to) => {
  if (!count.value || leaving.value) return true
  leaving.value = to.fullPath
  return false
})
function leave() {
  const to = leaving.value
  discardAll()
  if (to) void router.push(to).finally(() => (leaving.value = null))
}

const serverFacts = computed(() => [
  { key: 'config', label: t('ui.settings.configFile'), value: doc.value?.config_file ?? t('ui.settings.none'), mono: true },
  { key: 'overrides', label: t('ui.settings.overridesFile'), value: doc.value?.overrides_file ?? t('ui.settings.none'), mono: true },
  { key: 'token', label: t('ui.settings.tokenInUse'), value: status.status ? (status.status.auth_required ? t('ui.common.yes') : t('ui.common.no')) : '—' },
  { key: 'version', label: t('ui.settings.version'), value: status.status ? `${status.status.version} · API ${status.status.api_version}` : '—' },
])

onMounted(load)
</script>

<template>
  <div>
    <PageHeader :title="t('ui.nav.settings')" :description="t('ui.settings.description')" />

    <div v-if="loading && !doc" class="u-card p-6"><SkeletonBlock :lines="6" /></div>
    <div v-else-if="loadError && !doc" class="u-card"><ErrorState :error="loadError" @retry="load" /></div>

    <template v-else-if="doc">
      <UiTabs v-model="tab" :tabs="tabs" id-prefix="settings" :label="t('ui.settings.sections')">
        <BrowserSettings v-if="tab === 'browser'" />
        <template v-else>
          <section v-if="tab === 'server'" class="u-card px-6 py-4 mb-4">
            <p class="m-0 mb-2 u-small u-muted">{{ t('ui.settings.serverNote') }}</p>
            <FactList :items="serverFacts" />
          </section>
          <section class="u-card px-6" :aria-label="t(`ui.settings.section.${tab}`)">
            <SettingRow
              v-for="s in rows"
              :key="s.key"
              :setting="s"
              :staged="staged[s.key]"
              :config-file="doc.config_file"
              :problem="problems[s.key]"
              @change="(v) => stage(s.key, v)"
              @reset="stage(s.key, null)"
              @discard="unstage(s.key)"
            />
            <EmptyState v-if="!rows.length" icon="settings" :title="t('ui.settings.emptySection')" />
          </section>
        </template>
      </UiTabs>

      <div v-if="count || saveErrorView" class="u-savebar u-pop" data-testid="save-bar">
        <div class="flex-1 min-w-0">
          <p class="m-0 font-semibold" aria-live="polite">{{ t('ui.settings.changes', { n: count }) }}</p>
          <div v-if="saveErrorView" role="alert" class="u-small" style="color: var(--danger)">
            {{ saveErrorView.text }}<span v-if="saveErrorView.detail" class="u-muted"> {{ saveErrorView.detail }}</span>
            <span v-if="saveErrorView.requestId" class="u-mono u-muted"> · {{ t('ui.error.requestId') }} {{ saveErrorView.requestId }}</span>
            <span class="block u-muted">{{ t('ui.settings.nothingSaved') }}</span>
          </div>
        </div>
        <button type="button" class="u-btn" :disabled="saving || !count" @click="discardAll">{{ t('ui.settings.discard') }}</button>
        <button type="button" class="u-btn u-btn-primary" :disabled="saving || !count" data-testid="save" @click="save">
          <span v-if="saving" class="u-spinner" aria-hidden="true"></span>{{ t('ui.settings.save') }}
        </button>
      </div>
    </template>

    <ConfirmDialog v-if="confirmDir" :title="t('ui.settings.dataDirTitle')" :confirm-label="t('ui.settings.save')" :busy="saving" @confirm="doSave" @close="confirmDir = false">
      <p class="m-0">{{ t('ui.settings.dataDirText') }}</p>
      <p class="m-0 u-mono">{{ String(staged[DATA_DIR] ?? '') }}</p>
    </ConfirmDialog>
    <ConfirmDialog v-if="leaving" danger :title="t('ui.settings.leaveTitle', { n: count })" :confirm-label="t('ui.settings.discard')" @confirm="leave" @close="leaving = null">
      <p class="m-0">{{ t('ui.settings.leaveText') }}</p>
    </ConfirmDialog>
  </div>
</template>

<style>
.u-savebar {
  position: sticky;
  bottom: var(--sp-5);
  z-index: 20;
  margin-top: var(--sp-6);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-4) var(--sp-5);
}
.u-shell.is-phone .u-savebar {
  bottom: 72px;
}
</style>
