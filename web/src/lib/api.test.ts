import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, onUnauthorised, setCsrf } from './api'

afterEach(() => { vi.unstubAllGlobals(); setCsrf(null) })

function stubFetch(status: number, body: unknown) {
  const fn = vi.fn(async (_url: string, _init?: RequestInit) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('api', () => {
  it('sends CSRF header on unsafe methods only', async () => {
    const fn = stubFetch(200, { ok: true })
    setCsrf('tok')
    await api('/api/x', { method: 'PATCH', body: { a: 1 } })
    await api('/api/x')
    const patchHeaders = new Headers(fn.mock.calls[0][1]!.headers)
    const getHeaders = new Headers(fn.mock.calls[1][1]!.headers)
    expect(patchHeaders.get('X-CSRF-Token')).toBe('tok')
    expect(getHeaders.get('X-CSRF-Token')).toBeNull()
  })

  it('throws ApiError with detail and current version', async () => {
    stubFetch(409, { detail: 'This was changed somewhere else. Reload and try again.', current_version: 3 })
    await expect(api('/api/x', { method: 'PATCH', body: {} })).rejects.toMatchObject({
      status: 409, detail: 'This was changed somewhere else. Reload and try again.', currentVersion: 3,
    })
  })

  it('calls the unauthorised handler on 401', async () => {
    stubFetch(401, { detail: 'Sign in required.' })
    const handler = vi.fn()
    onUnauthorised(handler)
    await expect(api('/api/x')).rejects.toBeInstanceOf(ApiError)
    expect(handler).toHaveBeenCalledOnce()
  })

  it('returns undefined for 204', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 204 })))
    await expect(api('/api/x', { method: 'POST' })).resolves.toBeUndefined()
  })

  it('builds a readable message from FastAPI validation errors', async () => {
    stubFetch(422, { detail: [{ loc: ['body', 'password'], msg: 'String should have at least 10 characters' }] })
    await expect(api('/api/x', { method: 'POST', body: {} })).rejects.toMatchObject({
      detail: 'password: String should have at least 10 characters',
    })
  })
})
