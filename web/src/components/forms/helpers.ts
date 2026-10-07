import { vi } from 'vitest'

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

export type Call = { url: string; method: string; body: any }
/** Stub fetch with a router; every call is recorded. */
export function stubApi(handler: (url: string, method: string, body: any) => Response | Promise<Response> | undefined) {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(init.body as string) : undefined
    calls.push({ url, method, body })
    return handler(url, method, body) ?? json({ detail: `unexpected ${method} ${url}` }, 500)
  }))
  return calls
}
