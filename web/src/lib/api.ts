export class ApiError extends Error {
  constructor(public status: number, public detail: string, public currentVersion?: number) {
    super(detail)
  }
}

let csrf: string | null = null
let unauthorised: () => void = () => {}

export function setCsrf(token: string | null) { csrf = token }
export function onUnauthorised(handler: () => void) { unauthorised = handler }

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = (opts.method ?? 'GET').toUpperCase()
  const headers = new Headers({ Accept: 'application/json' })
  if (opts.body !== undefined) headers.set('Content-Type', 'application/json')
  if (UNSAFE.has(method) && csrf) headers.set('X-CSRF-Token', csrf)
  const res = await fetch(path, {
    method, headers, credentials: 'same-origin',
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  })
  if (res.status === 204) return undefined as T
  let data: any = null
  try { data = await res.json() } catch { data = null }
  if (!res.ok) {
    if (res.status === 401) unauthorised()
    const detail = typeof data?.detail === 'string' ? data.detail : `Request failed (${res.status})`
    throw new ApiError(res.status, detail, data?.current_version)
  }
  return data as T
}
