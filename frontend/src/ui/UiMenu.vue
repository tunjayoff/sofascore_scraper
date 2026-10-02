<script lang="ts">
import type { UiIconName as IconName } from '@/ui/UiIcon.vue'

export type MenuItem =
  | { kind?: 'item'; key: string; label: string; icon?: IconName; hint?: string; disabled?: boolean; danger?: boolean }
  | { kind: 'radio' | 'check'; key: string; label: string; checked: boolean; icon?: IconName; disabled?: boolean; hint?: undefined; danger?: undefined }
  | { kind: 'label'; key: string; label: string }
  | { kind: 'separator'; key: string }
</script>

<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, useId } from 'vue'
import UiIcon, { type UiIconName } from '@/ui/UiIcon.vue'

/**
 * A menu button (WAI-ARIA menu): Enter, Space or ↓ opens it, ↑/↓ move, Home/End jump, Esc closes and
 * returns focus to the button, a click outside closes it. Items are data, so the keyboard handling has
 * one place: plain items, radio items (one of a group), check items, group labels and separators.
 */
const props = withDefaults(defineProps<{ label: string; items: MenuItem[]; icon?: UiIconName; iconOnly?: boolean; align?: 'left' | 'right'; buttonClass?: string }>(), {
  align: 'right',
  buttonClass: 'u-btn',
  icon: undefined,
})
const emit = defineEmits<{ select: [key: string] }>()

const open = ref(false)
const button = ref<HTMLButtonElement | null>(null)
const list = ref<HTMLElement | null>(null)
const menuId = useId()

const actionable = computed(() => props.items.filter((i) => i.kind !== 'label' && i.kind !== 'separator' && !('disabled' in i && i.disabled)))

function itemEls(): HTMLElement[] {
  return Array.from(list.value?.querySelectorAll<HTMLElement>('[data-menu-item]:not([aria-disabled="true"])') ?? [])
}

function focusItem(index: number) {
  const els = itemEls()
  if (!els.length) return
  els[(index + els.length) % els.length].focus()
}

function onOutside(e: Event) {
  const target = e.target as Node
  if (!list.value?.contains(target) && !button.value?.contains(target)) close(false)
}

function show(focusLast = false) {
  if (open.value) return
  open.value = true
  document.addEventListener('mousedown', onOutside)
  void nextTick(() => focusItem(focusLast ? -1 : 0))
}

function close(returnFocus = true) {
  if (!open.value) return
  open.value = false
  document.removeEventListener('mousedown', onOutside)
  if (returnFocus) button.value?.focus()
}

function toggle() {
  if (open.value) close()
  else show()
}

function onButtonKey(e: KeyboardEvent) {
  if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    show()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    show(true)
  }
}

function onListKey(e: KeyboardEvent) {
  const els = itemEls()
  const i = els.indexOf(document.activeElement as HTMLElement)
  if (e.key === 'ArrowDown') focusItem(i + 1)
  else if (e.key === 'ArrowUp') focusItem(i - 1)
  else if (e.key === 'Home') focusItem(0)
  else if (e.key === 'End') focusItem(-1)
  else if (e.key === 'Escape') close()
  else if (e.key === 'Tab') close(false)
  else return
  if (e.key !== 'Tab') e.preventDefault()
  e.stopPropagation()
}

function choose(key: string) {
  if (!actionable.value.some((i) => i.key === key)) return
  close()
  emit('select', key)
}

onUnmounted(() => document.removeEventListener('mousedown', onOutside))

function role(item: MenuItem) {
  return item.kind === 'radio' ? 'menuitemradio' : item.kind === 'check' ? 'menuitemcheckbox' : 'menuitem'
}
</script>

<template>
  <div class="relative inline-flex">
    <button
      ref="button"
      type="button"
      :class="[buttonClass, { 'u-btn-icon': iconOnly }]"
      aria-haspopup="menu"
      :aria-expanded="open"
      :aria-controls="open ? menuId : undefined"
      :aria-label="iconOnly ? label : undefined"
      :title="iconOnly ? label : undefined"
      @click="toggle"
      @keydown="onButtonKey"
    >
      <UiIcon v-if="icon" :name="icon" :size="16" />
      <template v-if="!iconOnly">{{ label }}<UiIcon name="chevronDown" :size="14" /></template>
    </button>
    <div v-if="open" :id="menuId" ref="list" role="menu" :aria-label="label" class="u-pop u-menu" :class="align === 'right' ? 'right-0' : 'left-0'" @keydown="onListKey">
      <template v-for="item in items" :key="item.key">
        <div v-if="item.kind === 'separator'" role="separator" class="u-menu-sep"></div>
        <div v-else-if="item.kind === 'label'" role="presentation" class="u-caption u-menu-label">{{ item.label }}</div>
        <button
          v-else
          type="button"
          data-menu-item
          tabindex="-1"
          class="u-menu-item"
          :class="{ 'u-menu-danger': item.kind !== 'radio' && item.kind !== 'check' && item.danger }"
          :role="role(item)"
          :aria-checked="item.kind === 'radio' || item.kind === 'check' ? item.checked : undefined"
          :aria-disabled="item.disabled ? 'true' : undefined"
          :data-key="item.key"
          @click="choose(item.key)"
        >
          <span class="u-menu-mark" aria-hidden="true">
            <UiIcon v-if="(item.kind === 'radio' || item.kind === 'check') && item.checked" name="check" :size="14" />
            <UiIcon v-else-if="item.icon" :name="item.icon" :size="15" />
          </span>
          <span class="flex-1 text-left">{{ item.label }}</span>
          <span v-if="item.kind !== 'radio' && item.kind !== 'check' && item.hint" class="u-small u-muted">{{ item.hint }}</span>
        </button>
      </template>
    </div>
  </div>
</template>

<style>
.u-menu {
  position: absolute;
  top: calc(100% + 4px);
  z-index: 60;
  min-width: 220px;
  max-width: min(320px, calc(100vw - 32px));
  padding: var(--sp-2);
  display: flex;
  flex-direction: column;
}
.u-menu-item {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  min-height: 36px;
  padding: 0 var(--sp-3);
  border: 0;
  border-radius: var(--r-control);
  background: transparent;
  color: var(--text);
  font: inherit;
  cursor: pointer;
}
.u-menu-item:hover,
.u-menu-item:focus {
  background: var(--surface-2);
  outline: none;
}
.u-menu-item:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: -2px;
}
.u-menu-item[aria-disabled='true'] {
  opacity: 0.5;
  cursor: not-allowed;
}
.u-menu-danger {
  color: var(--danger);
}
.u-menu-mark {
  display: inline-flex;
  width: 16px;
  justify-content: center;
}
.u-menu-sep {
  height: 1px;
  margin: var(--sp-2) 0;
  background: var(--line);
}
.u-menu-label {
  padding: var(--sp-3) var(--sp-3) var(--sp-2);
}
@media (max-width: 1023px) {
  .u-menu-item {
    min-height: 44px;
  }
}
</style>
