<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import { CHUNK, TREE, childPath, entriesOf, isContainer, preview } from '@/ui/json'

/**
 * One node of the JSON tree. Children are rendered only while the node is open, and only the first
 * hundred at a time ("Show more"), so a large payload never builds its whole tree.
 */
const props = defineProps<{ name: string | number | null; value: unknown; path: string }>()
const { t } = useI18n()
const tree = inject(TREE)!
const shownCount = ref(CHUNK)

const container = computed(() => isContainer(props.value))
const open = computed(() => tree.open.value.has(props.path))
const children = computed(() => (container.value ? entriesOf(props.value as Record<string, unknown> | unknown[]) : []))
const hit = computed(() => tree.hits.value.has(props.path))
const selected = computed(() => tree.selected.value === props.path)
const kind = computed(() => {
  const v = props.value
  if (v === null) return 'null'
  if (Array.isArray(v)) return 'array'
  return typeof v
})
const plain = computed(() => (typeof props.value === 'string' ? JSON.stringify(props.value) : String(props.value)))
</script>

<template>
  <li class="u-json-node" :data-path="path">
    <div class="u-json-row" :class="{ 'is-hit': hit, 'is-selected': selected }">
      <button
        v-if="container"
        type="button"
        class="u-json-toggle"
        :aria-expanded="open"
        :aria-label="open ? t('ui.json.collapse', { name: name ?? '$' }) : t('ui.json.expand', { name: name ?? '$' })"
        @click="tree.toggle(path)"
      >
        <UiIcon :name="open ? 'chevronDown' : 'chevronRight'" :size="12" />
      </button>
      <span v-else class="u-json-toggle" aria-hidden="true"></span>
      <button type="button" class="u-json-label" :aria-pressed="selected" @click="tree.select(path)">
        <span v-if="name !== null" class="u-json-key">{{ name }}</span><span v-if="name !== null" class="u-muted">: </span>
        <span v-if="container" class="u-muted">{{ preview(value) }}</span>
        <span v-else :class="`u-json-${kind}`">{{ plain }}</span>
      </button>
    </div>
    <ul v-if="container && open" class="u-json-children">
      <JsonNode v-for="[k, v] in children.slice(0, shownCount)" :key="String(k)" :name="k" :value="v" :path="childPath(path, k)" />
      <li v-if="children.length > shownCount" class="u-json-more">
        <button type="button" class="u-btn u-btn-sm u-btn-ghost" @click="shownCount += CHUNK">{{ t('ui.json.more', { n: children.length - shownCount }) }}</button>
      </li>
    </ul>
  </li>
</template>
