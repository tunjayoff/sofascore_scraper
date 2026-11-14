import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import HealthScreen from '@/screens/HealthScreen.vue'
import { authNeeded } from '@/lib/auth'
import { clearToasts } from '@/ui/toast'
import { i18n, setLocale } from '@/i18n'
import { callsTo, flush, mockFetch } from './helpers'
import { axeViolations, mountScreen, status, v1Error } from './v1'

/**
 * Health: trusting the certificate of a network that re-signs HTTPS (#225). The card appears when the
 * connection check failed with cause `tls`; the server is asked what the network presents; trusting needs a
 * confirmation with a typed word and sends the fingerprint that was shown.
 */
const t = i18n.global.t
let w: VueWrapper

beforeEach(() => {
  setLocale('en')
  clearToasts()
  authNeeded.value = false
})
afterEach(() => w?.unmount())

const SHA = '9eba1e99686bc92a728e9070a0817965d334889a499d532e5f248570d309f23e'
const COLONS = (SHA.toUpperCase().match(/.{2}/g) ?? []).join(':')
const cert = (over = {}) => ({
  subject: { common_name: 'Fake Inspection Root CA', organization: 'Fake Inspection' },
  issuer: { common_name: 'Fake Inspection Root CA', organization: 'Fake Inspection' },
  sha256: SHA, not_before: '2026-01-01T00:00:00Z', not_after: '2036-01-01T00:00:00Z', expired: false, is_ca: true, self_signed: true,
  ...over,
})
const leaf = cert({ subject: { common_name: 'www.sofascore.com', organization: null }, sha256: 'ab'.repeat(32), is_ca: false, self_signed: false })
const probe = (over = {}) => ({
  host: 'www.sofascore.com', port: 443, verified: false, chain_length: 2, leaf, presented: cert(), can_trust: true, reason: null,
  error: 'self-signed certificate in certificate chain', browser_supported: true, ...over,
})
const checkResult = (over = {}) => ({
  ok: false, reason: 'network', cause: 'tls', message: 'x', events_count: null, checked_at_utc: '2026-10-02T10:02:00Z',
  bridge: status().bridge, connection: status().connection, ...over,
})
const list = <T>(data: T[]) => ({ data, page: { limit: data.length, next_cursor: null } })
const stored = { ...cert(), file: 'trusted-9eba1e99686bc92a.pem' }

async function failTheCheck() {
  await w.find('[data-testid="connection-check"] button').trigger('click')
  await flush()
}

describe('Health: trust the certificate of a network that inspects HTTPS', () => {
  it('asks nothing and shows nothing until a connection check fails on TLS', async () => {
    const f = mockFetch({
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/trust': list([]),
      'POST /api/v1/status/check': { data: checkResult({ cause: 'dns' }) },
      'POST /api/v1/trust/probe': { data: probe() },
    })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    expect(w.find('[data-testid="trust-probe"]').exists()).toBe(false)
    expect(w.find('[data-testid="trust-list"]').exists()).toBe(false) // empty list: not shown
    await failTheCheck() // a DNS failure: not a certificate problem
    expect(callsTo(f, 'POST /api/v1/trust/probe')).toHaveLength(0)
    expect(w.find('[data-testid="trust-probe"]').exists()).toBe(false)
  })

  it('shows who signed the certificate and trusts it after a typed confirmation', async () => {
    let trustedNow: unknown[] = []
    const f = mockFetch({
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/trust': () => list(trustedNow),
      'POST /api/v1/status/check': { data: checkResult() },
      'POST /api/v1/trust/probe': { data: probe() },
      'POST /api/v1/trust': () => {
        trustedNow = [stored]
        return { data: { stored, check: checkResult({ ok: true, reason: null, cause: null, events_count: 12 }), browser_supported: true, browser_restart_needed: false } }
      },
    })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    await failTheCheck()
    expect(JSON.parse(String(callsTo(f, 'POST /api/v1/trust/probe')[0][1]!.body))).toEqual({ target: 'sofascore' })
    const card = w.find('[data-testid="trust-probe"]')
    expect(card.text()).toContain(t('ui.health.trust.intro'))
    expect(card.find('[data-fact="subject"]').text()).toContain('Fake Inspection Root CA (Fake Inspection)')
    expect(card.find('[data-fact="issuer"]').text()).toContain('Fake Inspection Root CA (Fake Inspection)')
    expect(card.find('[data-testid="trust-fingerprint"]').text()).toBe(COLONS)
    expect(card.find('[data-fact="expires"]').text()).toContain('2036')
    expect(card.text()).not.toContain(t('ui.health.trust.issuingNote'))
    expect(await axeViolations(w.element)).toEqual([])

    // the confirmation states the risk and wants the word typed
    await card.find('[data-testid="trust-button"]').trigger('click')
    const dialog = w.find('[role="alertdialog"]')
    expect(dialog.text()).toContain(t('ui.health.trust.confirmRisk'))
    expect(dialog.text()).toContain(t('ui.health.trust.confirmWhen'))
    expect(dialog.text()).toContain(t('ui.health.trust.confirmCompare'))
    expect(dialog.text()).toContain(COLONS)
    const confirm = w.find('[data-testid="confirm"]')
    expect(confirm.attributes('disabled')).toBeDefined()
    await confirm.trigger('click')
    expect(callsTo(f, 'POST /api/v1/trust')).toHaveLength(0)
    await dialog.find('input').setValue('trust')
    expect(w.find('[data-testid="confirm"]').attributes('disabled')).toBeUndefined()
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()

    // exactly the fingerprint that was shown is sent; the certificate itself never is
    expect(JSON.parse(String(callsTo(f, 'POST /api/v1/trust')[0][1]!.body))).toEqual({ sha256: SHA })
    expect(w.find('[role="alertdialog"]').exists()).toBe(false)
    expect(w.find('[data-testid="trust-probe"]').exists()).toBe(false)
    expect(w.find('[data-testid="trust-done"]').text()).toContain(t('ui.health.trust.done'))
    expect(w.find('[data-testid="trust-done"]').text()).not.toContain(t('ui.health.trust.restartNeeded'))
    // the connection check the server ran afterwards is shown as the check result
    expect(w.find('[data-testid="connection-check"]').text()).toContain(t('ui.health.check.ok', { n: '12' }))
    const listed = w.find('[data-testid="trust-list"]')
    expect(listed.text()).toContain('Fake Inspection Root CA (Fake Inspection)')
    expect(listed.text()).toContain(COLONS)
  })

  it('shows the refusal when the network presents another certificate than the one shown', async () => {
    const other = 'cd'.repeat(32)
    let presented = cert()
    const f = mockFetch({
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/trust': list([]),
      'POST /api/v1/status/check': { data: checkResult() },
      'POST /api/v1/trust/probe': () => ({ data: probe({ presented }) }),
      'POST /api/v1/trust': () => {
        presented = cert({ sha256: other, subject: { common_name: 'Somebody Else', organization: null } })
        return v1Error(409, 'certificate_changed', { reason: 'changed', presented_sha256: other })
      },
    })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    await failTheCheck()
    await w.find('[data-testid="trust-button"]').trigger('click')
    await w.find('[role="alertdialog"] input').setValue('TRUST')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    const dialog = w.find('[role="alertdialog"]')
    expect(dialog.find('[role="alert"]').text()).toContain(t('ui.error.certificate_changed'))
    expect(w.find('[data-testid="trust-done"]').exists()).toBe(false)
    // the card behind the dialog now shows what the network presents now
    expect(callsTo(f, 'POST /api/v1/trust/probe')).toHaveLength(2)
    expect(w.find('[data-testid="trust-probe"] [data-fact="subject"]').text()).toContain('Somebody Else')
  })

  it('offers no button when the network does not send its CA certificate, and says what to do', async () => {
    mockFetch({
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/trust': list([]),
      'POST /api/v1/status/check': { data: checkResult() },
      'POST /api/v1/trust/probe': {
        data: probe({
          can_trust: false, presented: null, reason: 'root_not_sent', chain_length: 1,
          leaf: { ...leaf, issuer: { common_name: 'Acme Antivirus Root', organization: null } },
        }),
      },
    })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    await failTheCheck()
    const card = w.find('[data-testid="trust-probe"]')
    expect(card.find('[data-testid="trust-button"]').exists()).toBe(false)
    expect(card.find('[data-testid="trust-reason"]').text()).toBe(t('ui.health.trust.reason.root_not_sent', { issuer: 'Acme Antivirus Root' }))
  })

  it.each(['proxy', 'unreachable', 'expired', 'not_enough', 'chain_unavailable', 'not_https'])('explains %s without a button', async (reason) => {
    mockFetch({
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/trust': list([]),
      'POST /api/v1/status/check': { data: checkResult() },
      'POST /api/v1/trust/probe': { data: probe({ can_trust: false, presented: null, reason, error: null }) },
    })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    await failTheCheck()
    expect(w.find('[data-testid="trust-button"]').exists()).toBe(false)
    expect(w.find('[data-testid="trust-reason"]').text()).toBe(t(`ui.health.trust.reason.${reason}`, { issuer: 'Fake Inspection Root CA (Fake Inspection)' }))
  })

  it('says so when an issuing certificate is offered, when the browser is not covered, and when a restart is needed', async () => {
    mockFetch({
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/trust': list([]),
      'POST /api/v1/status/check': { data: checkResult() },
      'POST /api/v1/trust/probe': { data: probe({ presented: cert({ self_signed: false }), browser_supported: false }) },
      'POST /api/v1/trust': { data: { stored, check: checkResult({ ok: true, reason: null, cause: null, events_count: 1 }), browser_supported: false, browser_restart_needed: true } },
    })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    await failTheCheck()
    const card = w.find('[data-testid="trust-probe"]')
    expect(card.text()).toContain(t('ui.health.trust.issuingNote'))
    expect(card.text()).toContain(t('ui.health.trust.browserUnsupported'))
    await card.find('[data-testid="trust-button"]').trigger('click')
    await w.find('[role="alertdialog"] input').setValue('TRUST')
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    const done = w.find('[data-testid="trust-done"]')
    expect(done.text()).toContain(t('ui.health.trust.restartNeeded'))
    expect(done.text()).toContain(t('ui.health.trust.browserUnsupported'))
  })

  it('lists the trusted certificates and removes one after a confirmation', async () => {
    let items = [stored, { ...cert({ sha256: 'ef'.repeat(32), expired: true, subject: { common_name: 'Old CA', organization: null } }), file: 'company.pem' }]
    const f = mockFetch({
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/trust': () => list(items),
      [`DELETE /api/v1/trust/${SHA}`]: () => {
        items = items.slice(1)
        return { data: { sha256: SHA, browser_restart_needed: false } }
      },
    })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    const listed = w.find('[data-testid="trust-list"]')
    expect(listed.findAll('li')).toHaveLength(2)
    expect(listed.find(`[data-trusted="${'ef'.repeat(32)}"]`).text()).toContain(t('ui.health.trust.expired'))
    expect(listed.text()).toContain('company.pem')
    expect(w.find('[data-testid="trust-probe"]').exists()).toBe(false) // no failed check: only the list
    expect(await axeViolations(w.element)).toEqual([])
    await listed.find(`[data-trusted="${SHA}"] button`).trigger('click')
    expect(w.find('[role="alertdialog"]').text()).toContain(t('ui.health.trust.removeWhat', { name: 'Fake Inspection Root CA (Fake Inspection)' }))
    expect(callsTo(f, `DELETE /api/v1/trust/${SHA}`)).toHaveLength(0)
    await w.find('[data-testid="confirm"]').trigger('click')
    await flush()
    expect(callsTo(f, `DELETE /api/v1/trust/${SHA}`)).toHaveLength(1)
    expect(w.find('[data-testid="trust-list"]').findAll('li')).toHaveLength(1)
    expect(w.find(`[data-trusted="${SHA}"]`).exists()).toBe(false)
  })

  it('reads in Turkish too', async () => {
    setLocale('tr')
    mockFetch({
      'GET /api/v1/status': { data: status() },
      'GET /api/v1/trust': list([stored]),
      'POST /api/v1/status/check': { data: checkResult() },
      'POST /api/v1/trust/probe': { data: probe() },
    })
    ;({ w } = await mountScreen(HealthScreen, '/system/health'))
    await flush()
    await failTheCheck()
    expect(w.find('[data-testid="trust-button"]').text()).toBe('Bu sertifikaya güven')
    await w.find('[data-testid="trust-button"]').trigger('click')
    expect(w.find('[role="alertdialog"]').text()).toContain('okuyabilir ve değiştirebilir')
    expect(w.find('[data-testid="trust-list"]').text()).toContain('Güvenilen sertifikalar')
  })
})
