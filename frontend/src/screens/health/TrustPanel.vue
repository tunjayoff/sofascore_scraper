<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ConfirmDialog from '@/ui/ConfirmDialog.vue'
import FactList from '@/ui/FactList.vue'
import FormError from '@/ui/FormError.vue'
import TimeText from '@/ui/TimeText.vue'
import UiBadge from '@/ui/UiBadge.vue'
import UiIcon from '@/ui/UiIcon.vue'
import { v1 } from '@/api/v1/client'
import { V1Error } from '@/api/v1/client'
import type { CertificateInfo, StatusCheck, TrustedCertificate, TrustOutcome, TrustProbe } from '@/api/v1/schema'

/**
 * Health, connection card (#225): on a network that re-signs HTTPS (an antivirus that scans HTTPS, a company
 * proxy) the connection check fails with cause `tls`. This panel then asks the server what the network
 * presents (a TLS handshake, no request) and shows who signed it, with one action: trust that certificate.
 * The server stores only what it sees itself and only if the fingerprint is the one shown here (409
 * `certificate_changed` otherwise). The confirmation states the risk and asks for a typed word.
 * The list of trusted certificates is shown only when it is not empty.
 */
const props = defineProps<{ tlsFailed: boolean }>()
const emit = defineEmits<{ checked: [check: StatusCheck] }>()
const { t, te } = useI18n()

const trusted = ref<TrustedCertificate[]>([])
const probe = ref<TrustProbe | null>(null)
const probing = ref(false)
const probeError = ref<unknown>(null)
const outcome = ref<TrustOutcome | null>(null)

const confirming = ref(false)
const busy = ref(false)
const error = ref<unknown>(null)
const removing = ref<TrustedCertificate | null>(null)
const removeBusy = ref(false)
const removeError = ref<unknown>(null)

function who(name: CertificateInfo['subject']): string {
  const cn = name.common_name
  const org = name.organization
  if (cn && org && org !== cn) return `${cn} (${org})`
  return cn || org || '—'
}
/** AB:CD:… in groups, so two fingerprints can be compared by eye. */
function colons(sha256: string): string {
  return (sha256.toUpperCase().match(/.{2}/g) ?? []).join(':')
}
const facts = computed(() => [
  { key: 'subject', label: t('ui.health.trust.subject') },
  { key: 'issuer', label: t('ui.health.trust.issuer') },
  { key: 'fingerprint', label: t('ui.health.trust.fingerprint') },
  { key: 'expires', label: t('ui.health.trust.expires') },
])
const reason = computed(() => {
  const p = probe.value
  if (!p || p.can_trust || !p.reason || p.reason === 'verified') return ''
  const key = `ui.health.trust.reason.${p.reason}`
  const text = te(key, 'en') ? t(key, { issuer: p.leaf ? who(p.leaf.issuer) : '—' }) : t('ui.health.trust.reason.unreachable')
  return p.error && (p.reason === 'unreachable' || p.reason === 'not_enough') ? `${text} (${p.error})` : text
})

async function loadTrusted() {
  try {
    trusted.value = (await v1.trusted()).data
  } catch {
    // the list is an extra: the card stays hidden when it cannot be read
  }
}
async function look() {
  probing.value = true
  probeError.value = null
  try {
    probe.value = await v1.probeTrust()
  } catch (e) {
    probeError.value = e
    probe.value = null
  } finally {
    probing.value = false
  }
}
async function trust() {
  const shown = probe.value?.presented
  if (!shown) return
  busy.value = true
  error.value = null
  try {
    outcome.value = await v1.trustCertificate(shown.sha256)
    confirming.value = false
    probe.value = null
    emit('checked', outcome.value.check)
    void loadTrusted()
  } catch (e) {
    error.value = e
    // what the network presents changed since it was shown: show the new state behind the dialog
    if (e instanceof V1Error && e.code === 'certificate_changed') void look()
  } finally {
    busy.value = false
  }
}
async function remove() {
  const item = removing.value
  if (!item) return
  removeBusy.value = true
  removeError.value = null
  try {
    await v1.removeTrusted(item.sha256)
    removing.value = null
    outcome.value = null
    await loadTrusted()
  } catch (e) {
    removeError.value = e
  } finally {
    removeBusy.value = false
  }
}

watch(
  () => props.tlsFailed,
  (failed) => {
    if (failed) {
      outcome.value = null
      void look()
    }
  },
  { immediate: true },
)
onMounted(loadTrusted)
</script>

<template>
  <div v-if="tlsFailed && (probing || probe || probeError)" class="flex flex-col gap-3 pt-2" style="border-top: 1px solid var(--line)" data-testid="trust-probe">
    <h3 class="u-h3">{{ t('ui.health.trust.title') }}</h3>
    <p v-if="probing" class="m-0 u-small u-muted" role="status"><span class="u-spinner" aria-hidden="true"></span> {{ t('ui.health.trust.looking') }}</p>
    <FormError v-else-if="probeError" :error="probeError" />
    <template v-else-if="probe?.can_trust && probe.presented">
      <p class="m-0 u-small">{{ t('ui.health.trust.intro') }}</p>
      <FactList :items="facts">
        <template #value-subject>{{ who(probe.presented.subject) }}</template>
        <template #value-issuer>{{ who(probe.presented.issuer) }}</template>
        <template #value-fingerprint><span class="u-mono u-small" style="overflow-wrap: anywhere" data-testid="trust-fingerprint">{{ colons(probe.presented.sha256) }}</span></template>
        <template #value-expires><TimeText :value="probe.presented.not_after" format="date" /></template>
      </FactList>
      <p v-if="!probe.presented.self_signed" class="m-0 u-small u-muted">{{ t('ui.health.trust.issuingNote') }}</p>
      <p v-if="!probe.browser_supported" class="m-0 u-small u-muted">{{ t('ui.health.trust.browserUnsupported') }}</p>
      <div>
        <button type="button" class="u-btn" data-testid="trust-button" @click="(error = null), (confirming = true)">
          <UiIcon name="shield" :size="16" />{{ t('ui.health.trust.button') }}
        </button>
      </div>
    </template>
    <p v-else-if="reason" class="m-0 u-small" role="status" data-testid="trust-reason">{{ reason }}</p>
  </div>

  <div v-if="outcome" class="flex flex-col gap-1" data-testid="trust-done">
    <p class="m-0 u-small" role="status" style="color: var(--ok-fg)">{{ t('ui.health.trust.done') }}</p>
    <p v-if="outcome.browser_restart_needed" class="m-0 u-small" role="alert" style="color: var(--warn-fg)">{{ t('ui.health.trust.restartNeeded') }}</p>
    <p v-if="!outcome.browser_supported" class="m-0 u-small u-muted">{{ t('ui.health.trust.browserUnsupported') }}</p>
    <p class="m-0 u-small u-muted">{{ t('ui.health.trust.otherProcesses') }}</p>
  </div>

  <div v-if="trusted.length" class="flex flex-col gap-2 pt-2" style="border-top: 1px solid var(--line)" data-testid="trust-list">
    <h3 class="u-h3">{{ t('ui.health.trust.listTitle') }}</h3>
    <p class="m-0 u-small u-muted">{{ t('ui.health.trust.listNote') }}</p>
    <ul class="m-0 p-0 flex flex-col gap-2" style="list-style: none">
      <li v-for="item in trusted" :key="item.sha256" class="flex flex-wrap items-start gap-3" :data-trusted="item.sha256">
        <span class="flex-1 flex flex-col" style="min-width: 12rem">
          <span class="font-semibold">{{ who(item.subject) }} <UiBadge v-if="item.expired" tone="warn" icon="alert">{{ t('ui.health.trust.expired') }}</UiBadge></span>
          <span class="u-mono u-small u-muted" style="overflow-wrap: anywhere">{{ colons(item.sha256) }}</span>
          <span class="u-small u-muted">{{ item.file }} · {{ t('ui.health.trust.expires') }}: <TimeText :value="item.not_after" format="date" /></span>
        </span>
        <button type="button" class="u-btn" @click="(removeError = null), (removing = item)">{{ t('ui.health.trust.remove') }}</button>
      </li>
    </ul>
  </div>

  <ConfirmDialog
    v-if="confirming && probe?.presented"
    :title="t('ui.health.trust.confirmTitle')"
    :confirm-label="t('ui.health.trust.button')"
    danger
    :typed-word="t('ui.health.trust.word')"
    :busy="busy"
    :error="error"
    @confirm="trust"
    @close="confirming = false"
  >
    <p class="m-0">{{ t('ui.health.trust.confirmWhat', { name: who(probe.presented.subject) }) }}</p>
    <p class="m-0 u-mono u-small" style="overflow-wrap: anywhere">{{ colons(probe.presented.sha256) }}</p>
    <p class="m-0 font-semibold">{{ t('ui.health.trust.confirmRisk') }}</p>
    <p class="m-0 u-small u-muted">{{ t('ui.health.trust.confirmWhen') }}</p>
    <p class="m-0 u-small u-muted">{{ t('ui.health.trust.confirmCompare') }}</p>
  </ConfirmDialog>

  <ConfirmDialog
    v-if="removing"
    :title="t('ui.health.trust.removeTitle')"
    :confirm-label="t('ui.health.trust.remove')"
    danger
    :busy="removeBusy"
    :error="removeError"
    @confirm="remove"
    @close="removing = null"
  >
    <p class="m-0">{{ t('ui.health.trust.removeWhat', { name: who(removing.subject) }) }}</p>
  </ConfirmDialog>
</template>
