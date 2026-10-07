import { ApiError } from './api'

export const errorText = (err: unknown): string => (err instanceof ApiError ? err.detail : 'Something went wrong.')
export const isConflict = (err: unknown): boolean => err instanceof ApiError && err.status === 409

/** An optional whole number. Blank is "not given" (null), never 0. */
export function parseOptionalInt(text: string, min: number, max: number): { ok: boolean; value: number | null } {
  const t = text.trim()
  if (t === '') return { ok: true, value: null }
  if (!/^\d{1,7}$/.test(t)) return { ok: false, value: null }
  const n = Number(t)
  return n >= min && n <= max ? { ok: true, value: n } : { ok: false, value: null }
}

/** An optional percentage from 0 to `max` with up to 2 decimals. Blank is null, never 0. Cards: 0-100; debts: 0-1000. */
export function parseOptionalPercent(text: string, max = 100): { ok: boolean; value: number | null } {
  const t = text.trim().replace(/%$/, '').trim()
  if (t === '') return { ok: true, value: null }
  if (!/^\d{1,4}(\.\d{1,2})?$/.test(t)) return { ok: false, value: null }
  const n = Number(t)
  return n <= max ? { ok: true, value: n } : { ok: false, value: null }
}

export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false
  if (Array.isArray(a) !== Array.isArray(b)) return false
  const ka = Object.keys(a as object), kb = Object.keys(b as object)
  if (ka.length !== kb.length) return false
  return ka.every((k) => deepEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]))
}

/** The keys whose value differs. A key that was set before and is gone now becomes null. */
export function changes(before: Record<string, unknown>, after: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
    const b = before[key] ?? null, a = after[key] ?? null
    if (!deepEqual(a, b)) out[key] = a
  }
  return out
}
