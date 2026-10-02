<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import UiIcon from '@/ui/UiIcon.vue'
import { setLocale, type Lang } from '@/i18n'
import { setTheme, themePref, type ThemePref } from '@/lib/theme'
import { density, timeDisplay, type Density, type TimeDisplay } from '@/ui/prefs'
import { tokenInUse } from '@/app/session'
import { signOut } from '@/app/auth'

/** "This browser" (6.16): choices kept in this browser only, and Sign out when the server has a token. */
const { t, locale } = useI18n()

const groups = computed(() => [
  {
    key: 'language',
    value: locale.value,
    options: [
      { value: 'en', label: 'English' },
      { value: 'tr', label: 'Türkçe' },
    ],
    set: (v: string) => setLocale(v as Lang),
  },
  {
    key: 'theme',
    value: themePref.value,
    options: (['system', 'light', 'dark'] as const).map((v) => ({ value: v, label: t(`ui.prefs.theme.${v}`) })),
    set: (v: string) => setTheme(v as ThemePref),
  },
  {
    key: 'density',
    value: density.value,
    options: (['comfortable', 'compact'] as const).map((v) => ({ value: v, label: t(`ui.prefs.density.${v}`) })),
    set: (v: string) => (density.value = v as Density),
  },
  {
    key: 'time',
    value: timeDisplay.value,
    options: (['local', 'utc'] as const).map((v) => ({ value: v, label: t(`ui.prefs.time.${v}`) })),
    set: (v: string) => (timeDisplay.value = v as TimeDisplay),
  },
])
</script>

<template>
  <div class="flex flex-col gap-4">
    <p class="m-0 u-muted">{{ t('ui.prefs.note') }}</p>
    <section class="u-card px-6">
      <fieldset v-for="g in groups" :key="g.key" class="u-pref">
        <legend class="font-semibold">{{ t(`ui.prefs.${g.key}.label`) }}</legend>
        <p class="m-0 u-small u-muted">{{ t(`ui.prefs.${g.key}.hint`) }}</p>
        <div class="u-seg">
          <label v-for="o in g.options" :key="o.value" class="u-seg-item" :class="{ 'is-on': g.value === o.value }">
            <input type="radio" :name="`pref-${g.key}`" :value="o.value" :checked="g.value === o.value" class="u-sr" @change="g.set(o.value)" />
            {{ o.label }}
          </label>
        </div>
      </fieldset>
    </section>
    <section v-if="tokenInUse" class="u-card p-6 flex flex-col gap-3 items-start">
      <h2 class="u-h3">{{ t('ui.prefs.session') }}</h2>
      <p class="m-0 u-muted">{{ t('ui.prefs.signOutHint') }}</p>
      <button type="button" class="u-btn u-btn-danger" @click="signOut"><UiIcon name="signOut" :size="16" />{{ t('ui.menu.signOut') }}</button>
    </section>
  </div>
</template>

<style>
.u-pref {
  margin: 0;
  padding: var(--sp-5) 0;
  border: 0;
  border-top: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.u-pref:first-child {
  border-top: 0;
}
.u-pref legend {
  float: left;
  padding: 0;
}
.u-seg {
  display: inline-flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
  padding: var(--sp-2);
  border-radius: var(--r-card);
  background: var(--surface-2);
  border: 1px solid var(--border);
  align-self: flex-start;
}
.u-seg-item {
  display: inline-flex;
  align-items: center;
  min-height: 36px;
  padding: 0 var(--sp-4);
  border-radius: var(--r-control);
  color: var(--text-2);
  font-weight: 600;
  cursor: pointer;
}
.u-seg-item.is-on {
  background: var(--surface);
  color: var(--text);
  box-shadow: inset 0 0 0 1px var(--border);
}
.u-seg-item:focus-within {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}
@media (max-width: 1023px) {
  .u-seg-item {
    min-height: 44px;
  }
}
</style>
