import { expect, it } from 'vitest'
import { dmyDate, isoFromDmy, todayISO, ukDate } from './dates'

it('formats ISO dates the UK way', () => {
  expect(ukDate('2026-10-28')).toBe('28 Oct 2026')
  expect(ukDate(null)).toBe('')
  expect(ukDate('soon')).toBe('soon')
})

it('gives today in local time', () => {
  expect(todayISO(new Date(2026, 0, 5, 23, 59))).toBe('2026-01-05')
})

it('converts between ISO and DD/MM/YYYY', () => {
  expect(dmyDate('2026-10-01')).toBe('01/10/2026')
  expect(dmyDate(null)).toBe('')
  expect(isoFromDmy('01/10/2026')).toBe('2026-10-01')
  expect(isoFromDmy('1/2/2026')).toBe('2026-02-01')
  expect(isoFromDmy('31/02/2026')).toBeNull()
  expect(isoFromDmy('2026-10-01')).toBeNull()
})
