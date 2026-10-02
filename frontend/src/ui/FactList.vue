<script setup lang="ts">
/**
 * Label/value pairs (4.7) for detail pages and Health. Each item is a label and a value; a value can be
 * given through the slot `value-<key>` instead of `value`.
 */
defineProps<{ items: { key: string; label: string; value?: string | number | null; mono?: boolean }[] }>()
</script>

<template>
  <dl class="u-facts">
    <div v-for="item in items" :key="item.key" class="u-fact" :data-fact="item.key">
      <dt class="u-muted">{{ item.label }}</dt>
      <dd :class="{ 'u-mono': item.mono }">
        <slot :name="`value-${item.key}`">{{ item.value ?? '—' }}</slot>
      </dd>
    </div>
  </dl>
</template>

<style>
.u-facts {
  margin: 0;
  display: flex;
  flex-direction: column;
}
.u-fact {
  display: grid;
  grid-template-columns: minmax(120px, 40%) 1fr;
  gap: var(--sp-4);
  padding: var(--sp-3) 0;
  border-top: 1px solid var(--line);
}
.u-fact:first-child {
  border-top: 0;
}
.u-fact dt,
.u-fact dd {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
