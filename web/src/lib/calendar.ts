/** Month grids for the Commitments calendar: weeks start on Monday (UK). */

export type Day = { iso: string; day: number; inMonth: boolean }
export type Month = { key: string; label: string; weeks: Day[][] }

const iso = (d: Date) => d.toISOString().slice(0, 10)

export function months(startIso: string, endIso: string): Month[] {
  const out: Month[] = []
  const start = new Date(`${startIso.slice(0, 7)}-01T00:00:00Z`)
  const end = new Date(`${endIso}T00:00:00Z`)
  for (let m = new Date(start); m <= end; m = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 1))) {
    const first = new Date(m)
    const offset = (first.getUTCDay() + 6) % 7
    const cursor = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth(), 1 - offset))
    const weeks: Day[][] = []
    do {
      const week: Day[] = []
      for (let i = 0; i < 7; i++) {
        week.push({ iso: iso(cursor), day: cursor.getUTCDate(), inMonth: cursor.getUTCMonth() === m.getUTCMonth() })
        cursor.setUTCDate(cursor.getUTCDate() + 1)
      }
      weeks.push(week)
    } while (cursor.getUTCMonth() === m.getUTCMonth())
    out.push({
      key: iso(m).slice(0, 7),
      label: m.toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' }),
      weeks,
    })
  }
  return out
}
