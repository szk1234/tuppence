import { expect, it } from 'vitest'
import { formatGBP, parsePoundsInput } from './money'

it('normalises pounds text exactly', () => {
  expect(parsePoundsInput('£1,450')).toBe('1450.00')
  expect(parsePoundsInput('1450')).toBe('1450.00')
  expect(parsePoundsInput(' 1,450.5 ')).toBe('1450.50')
  expect(parsePoundsInput('0.1')).toBe('0.10')
  expect(parsePoundsInput('007.07')).toBe('7.07')
  expect(parsePoundsInput('0')).toBe('0.00')
  expect(parsePoundsInput('1000000000')).toBe('1000000000.00')
})

it('is exact where floats are not', () => {
  expect(parsePoundsInput('19.99')).toBe('19.99')
  expect(parsePoundsInput('9007199254740993.01')).toBeNull() // too large, and not float-safe
  expect(parsePoundsInput('999999999.99')).toBe('999999999.99')
})

it('rejects things that are not amounts', () => {
  for (const bad of ['', 'abc', '-5', '1.234', '1,45', '1,4500', '£', '1..2', '1000000000.01', '12345678901', '1e3', '.5']) {
    expect(parsePoundsInput(bad), bad).toBeNull()
  }
})

it('formats pounds for display', () => {
  expect(formatGBP('1450')).toBe('£1,450.00')
  expect(formatGBP('1450.5')).toBe('£1,450.50')
  expect(formatGBP('0.07')).toBe('£0.07')
  expect(formatGBP('1234567.89')).toBe('£1,234,567.89')
  expect(formatGBP('-12.5')).toBe('-£12.50')
  expect(formatGBP('nonsense')).toBe('nonsense')
})
