import { expect, it } from 'vitest'
import { months } from './calendar'

it('builds Monday-first month grids covering the range', () => {
  const [nov, dec] = months('2026-11-07', '2026-12-31')
  expect(nov.label).toBe('November 2026')
  expect(nov.weeks[0][0]).toEqual({ iso: '2026-10-26', day: 26, inMonth: false })
  expect(nov.weeks[0][6]).toEqual({ iso: '2026-11-01', day: 1, inMonth: true })
  expect(nov.weeks.every((w) => w.length === 7)).toBe(true)
  expect(dec.key).toBe('2026-12')
  expect(months('2026-11-07', '2026-11-08')).toHaveLength(1)
})
