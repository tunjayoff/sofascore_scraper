<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/ui/PageHeader.vue'
import UiBadge from '@/ui/UiBadge.vue'
import StatusBadge from '@/ui/StatusBadge.vue'
import DataTable, { type Column, type Sort } from '@/ui/DataTable.vue'
import FilterBar from '@/ui/FilterBar.vue'
import EmptyState from '@/ui/EmptyState.vue'
import ErrorState from '@/ui/ErrorState.vue'
import SkeletonBlock from '@/ui/SkeletonBlock.vue'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import SidePanel from '@/ui/SidePanel.vue'
import UiTabs from '@/ui/UiTabs.vue'
import TimeText from '@/ui/TimeText.vue'
import ProgressBar from '@/ui/ProgressBar.vue'
import FactList from '@/ui/FactList.vue'
import StatTile from '@/ui/StatTile.vue'
import CodeHint from '@/ui/CodeHint.vue'
import UiMenu from '@/ui/UiMenu.vue'
import { V1Error } from '@/api/v1/client'
import { toast } from '@/ui/toast'
import { JOB_STATES, CONNECTION, LIVE, HEALTH } from '@/ui/status'

/**
 * Every part of the design system in its states (05-web-ui.md 4), at `/dev/kitchen-sink` in development
 * builds only (the router adds the route when `import.meta.env.DEV`). The texts here are sample data for
 * developers and are not translated.
 */
const { t } = useI18n()
type Row = { id: string; name: string; n: number }
const rows: Row[] = [
  { id: 'a', name: 'Alpha', n: 3 },
  { id: 'b', name: 'Bravo', n: 1 },
  { id: 'c', name: 'Charlie', n: 2 },
]
const columns: Column<Row>[] = [
  { key: 'name', label: 'Name', sortable: true, card: 'title' },
  { key: 'n', label: 'Count', sortable: true, align: 'right' },
  { key: 'id', label: 'Id', optional: true, mono: true },
]
const sort = ref<Sort | null>(null)
const tab = ref<'one' | 'two'>('one')
const confirm = ref<'' | 'plain' | 'typed'>('')
const panel = ref(false)
const networkError = new V1Error(0, 'network', 'offline', null, 'ui-x')
const sampleError = new V1Error(409, 'job_running', 'A job is running.', { holder: { purpose: 'job', pid: 4121, host: 'srv-1' } }, 'ui-sample-1')
</script>

<template>
  <div class="flex flex-col gap-8">
    <PageHeader title="Kitchen sink" description="Every part of the design system, in every state.">
      <template #actions><button type="button" class="u-btn u-btn-primary">Primary</button><button type="button" class="u-btn">Secondary</button></template>
    </PageHeader>

    <section class="flex flex-col gap-3">
      <h2 class="u-h2">Type</h2>
      <p class="u-display m-0">48,210</p>
      <p class="u-h1">Heading 1</p>
      <p class="u-h2">Heading 2</p>
      <p class="u-h3">Heading 3</p>
      <p class="u-body m-0">Body text</p>
      <p class="u-small m-0">Small text</p>
      <p class="u-caption m-0">Caption</p>
      <p class="u-mono m-0">01J9ZQ3M5XK8 · 16950622</p>
    </section>

    <section class="flex flex-col gap-3">
      <h2 class="u-h2">Buttons</h2>
      <div class="flex flex-wrap gap-2">
        <button type="button" class="u-btn u-btn-primary">Primary</button>
        <button type="button" class="u-btn">Default</button>
        <button type="button" class="u-btn u-btn-ghost">Ghost</button>
        <button type="button" class="u-btn u-btn-danger">Danger</button>
        <button type="button" class="u-btn u-btn-danger-solid">Danger solid</button>
        <button type="button" class="u-btn" disabled>Disabled</button>
        <button type="button" class="u-btn u-btn-sm">Small</button>
        <UiMenu label="Menu" :items="[{ key: 'a', label: 'Item' }, { kind: 'separator', key: 's' }, { kind: 'radio', key: 'r', label: 'Radio', checked: true }]" />
      </div>
    </section>

    <section class="flex flex-col gap-3">
      <h2 class="u-h2">Status vocabulary</h2>
      <div class="flex flex-wrap gap-2"><StatusBadge v-for="(_, k) in JOB_STATES" :key="k" kind="job" :value="k" /></div>
      <div class="flex flex-wrap gap-2"><StatusBadge v-for="(_, k) in CONNECTION" :key="k" kind="connection" :value="k" /></div>
      <div class="flex flex-wrap gap-2"><StatusBadge v-for="(_, k) in LIVE" :key="k" kind="live" :value="k" /></div>
      <div class="flex flex-wrap gap-2"><StatusBadge v-for="(_, k) in HEALTH" :key="k" kind="health" :value="k" /></div>
      <div class="flex flex-wrap gap-2"><UiBadge tone="info" icon="info">Info</UiBadge><UiBadge outline>Outline</UiBadge></div>
    </section>

    <section class="grid gap-4 md:grid-cols-3">
      <StatTile label="Matches" value="48,210" />
      <StatTile label="With details" value="46,903" note="97%" :bar="97" />
      <div class="u-card p-5 flex flex-col gap-3">
        <ProgressBar :value="58" label="Sample" text="812 of 1,400" />
        <ProgressBar :value="null" label="Indeterminate" />
      </div>
    </section>

    <section class="grid gap-4 md:grid-cols-2">
      <div class="u-card p-5"><FactList :items="[{ key: 'a', label: 'Event id', value: '16950622', mono: true }, { key: 'b', label: 'Read at' }]"><template #value-b><TimeText value="2026-09-30T21:12:00Z" /></template></FactList></div>
      <div class="u-card p-5 flex flex-col gap-2"><TimeText value="2026-09-30T21:12:00Z" relative /><CodeHint command="ssc migrate --dry-run" /></div>
    </section>

    <section class="flex flex-col gap-3">
      <h2 class="u-h2">Table</h2>
      <FilterBar :active-count="1" :chips="[{ key: 'x', label: 'Finished' }]">
        <label class="flex flex-col"><span class="u-label">Search</span><input class="u-field" data-filter-focus /></label>
      </FilterBar>
      <DataTable v-model:sort="sort" table-id="sink" caption="Sample" :columns="columns" :rows="rows" :row-key="(r) => r.id" :paged="true" :has-next="true" />
      <DataTable table-id="sink-loading" caption="Loading" :columns="columns" :rows="[]" :row-key="(r) => r.id" loading />
      <DataTable table-id="sink-empty" caption="Empty" :columns="columns" :rows="[]" :row-key="(r) => r.id">
        <template #empty><EmptyState title="Nothing here yet." text="Every list has its own sentence." /></template>
      </DataTable>
      <DataTable table-id="sink-error" caption="Error" :columns="columns" :rows="[]" :row-key="(r) => r.id" :error="sampleError" />
    </section>

    <section class="flex flex-col gap-3">
      <h2 class="u-h2">States</h2>
      <div class="u-card p-5"><SkeletonBlock :lines="3" /></div>
      <div class="u-card"><ErrorState :error="networkError" /></div>
      <UiTabs v-model="tab" :tabs="[{ key: 'one', label: 'One' }, { key: 'two', label: 'Two', badge: '2' }]" id-prefix="sink" label="Sample tabs">
        <p class="m-0">Panel {{ tab }}</p>
      </UiTabs>
      <div class="flex flex-wrap gap-2">
        <button type="button" class="u-btn" @click="toast({ text: 'Job started', link: { to: '/jobs', label: t('ui.jobs.openJob') } })">Toast</button>
        <button type="button" class="u-btn" @click="toast({ kind: 'error', text: 'Something failed', requestId: 'ui-123' })">Error toast</button>
        <button type="button" class="u-btn" @click="confirm = 'plain'">Confirm</button>
        <button type="button" class="u-btn" @click="confirm = 'typed'">Typed confirm</button>
        <button type="button" class="u-btn" @click="panel = true">Side panel</button>
      </div>
    </section>

    <ConfirmDialog v-if="confirm" title="Clear stored matches?" confirm-label="Clear" danger :typed-word="confirm === 'typed' ? 'matches' : undefined" @confirm="confirm = ''" @close="confirm = ''">
      <p class="m-0">48,210 matches will be removed. Follows and job history are kept.</p>
    </ConfirmDialog>
    <SidePanel v-if="panel" title="Raw · statistics" @close="panel = false"><p class="m-0">Panel content</p></SidePanel>
  </div>
</template>
