<script setup lang="ts">
import { computed, provide, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import JsonNode from '@/ui/JsonNode.vue'
import { copyText } from '@/ui/focus'
import { toast } from '@/ui/toast'
import { ROOT, TREE, isContainer, search, valueAt } from '@/ui/json'

/**
 * A stored payload as a collapsible tree (4.7 JsonViewer, decision 7): search over keys and values, copy
 * the path or the value of the selected node, copy everything, download, and a switch to plain text. The
 * text is kept exactly as the server sent it: Copy and Download give that text, never a re-serialised
 * copy. `downloadUrl` downloads the full-size file from the server instead of from the page.
 */
const props = defineProps<{ text: string; fileName: string; downloadUrl?: string }>()
const { t } = useI18n()
const searchId = useId()

/** Plain text above this size is shown in part; the download has it all. */
const TEXT_LIMIT = 200_000

const parsed = computed<{ ok: true; value: unknown } | { ok: false }>(() => {
  try {
    return { ok: true, value: JSON.parse(props.text) }
  } catch {
    return { ok: false }
  }
})
const mode = ref<'tree' | 'text'>('tree')
const query = ref('')
const open = ref<Set<string>>(new Set([ROOT]))
const selected = ref<string | null>(null)
const result = computed(() => (parsed.value.ok ? search(parsed.value.value, query.value) : { matches: [], open: new Set<string>(), truncated: false }))
const hits = computed(() => new Set(result.value.matches))

watch(result, (r) => {
  if (r.open.size) open.value = new Set([...open.value, ...r.open])
})

// A small payload starts open one level deeper, so its shape is visible at once
watch(
  parsed,
  (p) => {
    if (!p.ok || !isContainer(p.value)) return
    const keys = Object.keys(p.value)
    if (keys.length <= 12) open.value = new Set([ROOT, ...keys.map((k) => (Array.isArray(p.value) ? `$[${k}]` : /^[A-Za-z_$][\w$]*$/.test(k) ? `$.${k}` : `$[${JSON.stringify(k)}]`))])
  },
  { immediate: true },
)

provide(TREE, {
  open,
  selected,
  hits,
  toggle(path: string) {
    const next = new Set(open.value)
    if (next.has(path)) next.delete(path)
    else next.add(path)
    open.value = next
  },
  select(path: string) {
    selected.value = selected.value === path ? null : path
  },
})

const pretty = computed(() => {
  if (!parsed.value.ok) return props.text.slice(0, TEXT_LIMIT)
  const text = JSON.stringify(parsed.value.value, null, 2)
  return text.length > TEXT_LIMIT ? text.slice(0, TEXT_LIMIT) : text
})
const cut = computed(() => props.text.length > TEXT_LIMIT)

async function copy(what: 'all' | 'path' | 'value') {
  let text = props.text
  if (what === 'path') text = selected.value ?? ROOT
  if (what === 'value' && parsed.value.ok) {
    const v = valueAt(parsed.value.value, selected.value ?? ROOT)
    text = typeof v === 'string' ? v : JSON.stringify(v, null, 2)
  }
  const ok = await copyText(text)
  toast({ kind: ok ? 'ok' : 'error', text: ok ? t('ui.json.copied') : t('ui.json.copyFailed') })
}

function downloadHere() {
  const url = URL.createObjectURL(new Blob([props.text], { type: 'application/json' }))
  const a = document.createElement('a')
  a.href = url
  a.download = props.fileName
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
</script>

<template>
  <div class="u-json" data-testid="json-viewer">
    <div class="u-json-tools">
      <div class="u-seg" role="group" :aria-label="t('ui.json.view')">
        <button type="button" :aria-pressed="mode === 'tree'" :disabled="!parsed.ok" @click="mode = 'tree'">{{ t('ui.json.tree') }}</button>
        <button type="button" :aria-pressed="mode === 'text'" @click="mode = 'text'">{{ t('ui.json.text') }}</button>
      </div>
      <label v-if="mode === 'tree' && parsed.ok" :for="searchId" class="u-sr">{{ t('ui.json.search') }}</label>
      <input v-if="mode === 'tree' && parsed.ok" :id="searchId" v-model="query" type="search" class="u-field u-json-search" :placeholder="t('ui.json.search')" autocomplete="off" />
      <span class="flex-1"></span>
      <button type="button" class="u-btn u-btn-sm" data-testid="json-copy" @click="copy('all')"><UiIcon name="copy" :size="14" />{{ t('ui.json.copyAll') }}</button>
      <a v-if="downloadUrl" :href="downloadUrl" :download="fileName" class="u-btn u-btn-sm" data-testid="json-download"><UiIcon name="exports" :size="14" />{{ t('ui.json.download') }}</a>
      <button v-else type="button" class="u-btn u-btn-sm" data-testid="json-download" @click="downloadHere"><UiIcon name="exports" :size="14" />{{ t('ui.json.download') }}</button>
    </div>
    <p v-if="query.trim() && mode === 'tree'" class="m-0 u-small u-muted" aria-live="polite">
      {{ result.truncated ? t('ui.json.matchesMany', { n: result.matches.length }) : t('ui.json.matches', { n: result.matches.length }) }}
    </p>
    <div v-if="selected && mode === 'tree'" class="u-json-selected">
      <code class="u-mono flex-1 min-w-0 break-all">{{ selected }}</code>
      <button type="button" class="u-btn u-btn-sm u-btn-ghost" @click="copy('path')">{{ t('ui.json.copyPath') }}</button>
      <button type="button" class="u-btn u-btn-sm u-btn-ghost" @click="copy('value')">{{ t('ui.json.copyValue') }}</button>
    </div>
    <p v-if="!parsed.ok" class="m-0 u-small u-notice u-notice-warn"><UiIcon name="alert" :size="14" />{{ t('ui.json.notJson') }}</p>
    <ul v-if="mode === 'tree' && parsed.ok" class="u-json-tree" :aria-label="t('ui.json.tree')">
      <JsonNode :name="null" :value="parsed.value" :path="ROOT" />
    </ul>
    <template v-else>
      <pre class="u-json-text" tabindex="0" :aria-label="t('ui.json.text')">{{ pretty }}</pre>
      <p v-if="cut" class="m-0 u-small u-muted">{{ t('ui.json.cut') }}</p>
    </template>
  </div>
</template>

<style>
.u-json {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  min-width: 0;
}
.u-json-tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-3);
}
.u-json-search {
  height: 32px;
  max-width: 220px;
}
.u-json-selected {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-control);
  background: var(--surface-2);
}
.u-json-tree,
.u-json-children {
  margin: 0;
  padding: 0;
  list-style: none;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.8125rem;
  line-height: 1.6;
}
.u-json-tree {
  padding: var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--r-control);
  background: var(--surface-2);
  overflow-x: auto;
}
.u-json-children {
  padding-left: 18px;
}
.u-json-row {
  display: flex;
  align-items: flex-start;
  gap: 2px;
  border-radius: 4px;
}
.u-json-row.is-hit {
  background: var(--warn-bg);
}
.u-json-row.is-selected {
  background: var(--accent-soft);
}
.u-json-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 20px;
  flex-shrink: 0;
  padding: 0;
  border: 0;
  background: none;
  color: var(--muted);
  cursor: pointer;
}
.u-json-label {
  min-width: 0;
  padding: 0 2px;
  border: 0;
  background: none;
  color: var(--text);
  font: inherit;
  text-align: left;
  overflow-wrap: anywhere;
  cursor: pointer;
}
.u-json-key {
  color: var(--info-fg);
}
.u-json-string {
  color: var(--ok-fg);
}
.u-json-number,
.u-json-boolean {
  color: var(--warn-fg);
}
.u-json-null {
  color: var(--muted);
}
.u-json-text {
  margin: 0;
  padding: var(--sp-4);
  max-height: 70vh;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: var(--r-control);
  background: var(--surface-2);
  font-size: 0.8125rem;
  white-space: pre;
}
</style>
