/** Today as YYYY-MM-DD in the user's own timezone. */
export function todayISO(now: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`
}

/** "2026-10-28" -> "28 Oct 2026". Anything that isn't an ISO date is returned unchanged. */
export function ukDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  if (!m) return iso
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }).format(d)
}

/** "2026-10-01" -> "01/10/2026" (the numeric UK form statements use). */
export function dmyDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  return m ? `${m[3]}/${m[2]}/${m[1]}` : iso
}

/** "01/10/2026" -> "2026-10-01", or null when it isn't a real date. */
export function isoFromDmy(uk: string): string | null {
  const match = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(uk.trim())
  if (!match) return null
  const [, d, m, y] = match
  const iso = `${y}-${m.padStart(2, '0')}-${d.padStart(2, '0')}`
  const parsed = new Date(`${iso}T00:00:00Z`)
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().startsWith(iso) ? iso : null
}
