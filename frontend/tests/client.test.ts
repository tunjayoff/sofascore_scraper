import { describe, expect, it, vi } from 'vitest'
import { ApiError, apiGet, apiSend } from '@/api/client'
import { errorText } from '@/lib/toast'
import { upstreamReason, upstreamText } from '@/lib/upstream'
import { i18n } from '@/i18n'
import { json, mockFetch } from './helpers'

describe('api client errors', () => {
  it('turns a FastAPI 422 body into readable field messages', async () => {
    mockFetch({
      'POST /api/settings': json(
        {
          detail: [
            { loc: ['body', 'max_concurrent'], msg: 'Input should be less than or equal to 50' },
            { loc: ['query', 'scope'], msg: "Input should be 'all' or 'config'" },
            { loc: ['body'], msg: 'Field required' },
          ],
        },
        422,
      ),
    })
    const e = (await apiSend('/api/settings', 'POST', { max_concurrent: 99 }).catch((x) => x)) as ApiError
    expect(e).toBeInstanceOf(ApiError)
    expect(e.status).toBe(422)
    expect(e.message).toBe(
      "max_concurrent: Input should be less than or equal to 50; scope: Input should be 'all' or 'config'; Field required",
    )
    expect(errorText(e)).toBe(e.message)
  })

  it('uses a string detail as is', async () => {
    mockFetch({ 'GET /api/leagues/9/seasons': json({ detail: 'League not found' }, 404) })
    await expect(apiGet('/api/leagues/9/seasons')).rejects.toMatchObject({ status: 404, message: 'League not found' })
  })

  it('keeps the reason of a typed error and uses its message as the text', async () => {
    mockFetch({
      'POST /api/leagues/search-remote': json({ detail: { reason: 'blocked', message: 'SofaScore refused the request (HTTP 403).' } }, 502),
    })
    const e = (await apiSend('/api/leagues/search-remote?q=premier', 'POST').catch((x) => x)) as ApiError
    expect(e).toBeInstanceOf(ApiError)
    expect(e).toMatchObject({ status: 502, reason: 'blocked', message: 'SofaScore refused the request (HTTP 403).' })
    expect(upstreamReason(e)).toBe('blocked')
    expect(upstreamText(e)).toBe(`${i18n.global.t('upstream.blocked')} ${i18n.global.t('upstream.testHint')}`)
    // Only a block points at the connection check
    expect(upstreamText(new ApiError('x', 502, 'network'))).toBe(i18n.global.t('upstream.network'))
  })

  it('treats an unknown reason and an untyped error as plain errors', async () => {
    mockFetch({
      'POST /api/settings': json({ detail: { reason: 'proxy_password_required', message: 'Re-enter the proxy password.' } }, 422),
      'GET /api/leagues': json({ detail: { code: 7 } }, 500),
    })
    const typed = (await apiSend('/api/settings', 'POST', {}).catch((x) => x)) as ApiError
    expect(typed.reason).toBe('proxy_password_required')
    expect(upstreamReason(typed)).toBeNull() // not a SofaScore failure
    expect(upstreamText(typed)).toBe('Re-enter the proxy password.')
    const untyped = (await apiGet('/api/leagues').catch((x) => x)) as ApiError
    expect(untyped.reason).toBeUndefined()
    expect(untyped.message).toBe('{"code":7}')
  })

  it('falls back to the status text when the body is not JSON', async () => {
    mockFetch({ 'GET /api/leagues': new Response('<html>bad gateway</html>', { status: 502, statusText: 'Bad Gateway' }) })
    await expect(apiGet('/api/leagues')).rejects.toMatchObject({ status: 502, message: 'Bad Gateway' })
  })

  it('reports a network failure as "server not reachable"', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const e = await apiGet('/api/leagues').catch((x) => x)
    expect(e).toBeInstanceOf(TypeError)
    expect(errorText(e)).toBe(i18n.global.t('common.serverDown'))
    expect(errorText(e)).not.toBe('common.serverDown')
  })
})
