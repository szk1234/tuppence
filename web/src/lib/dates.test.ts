import { expect, it } from 'vitest'
import { todayISO, ukDate } from './dates'

it('formats ISO dates the UK way', () => {
  expect(ukDate('2026-10-28')).toBe('28 Oct 2026')
  expect(ukDate(null)).toBe('')
  expect(ukDate('soon')).toBe('soon')
})

it('gives today in local time', () => {
  expect(todayISO(new Date(2026, 0, 5, 23, 59))).toBe('2026-01-05')
})
