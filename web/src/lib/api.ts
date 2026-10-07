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
    let detail = `Request failed (${res.status})`
    if (typeof data?.detail === 'string') detail = data.detail
    else if (Array.isArray(data?.detail) && data.detail.length) {
      const first = data.detail[0]
      const field = Array.isArray(first?.loc) ? first.loc[first.loc.length - 1] : undefined
      const msg = typeof first?.msg === 'string' ? first.msg : detail
      detail = field !== undefined && field !== 'body' ? `${field}: ${msg}` : msg
    }
    throw new ApiError(res.status, detail, data?.current_version)
  }
  return data as T
}
