import { expect, it } from 'vitest'
import { formatGBP, parsePoundsInput } from './money'

// The shared money-text table. The server's parse_pounds is tested against the same table in
// tests/core/test_money.py: keep the two lists identical. null means "rejected".
const SHARED_CASES: Array<[string, string | null]> = [
  ['1450', '1450.00'],
  ['1,450', '1450.00'],
  ['£1,450', '1450.00'],
  ['12,345.67', '12345.67'],
  ['1,234,567.89', '1234567.89'],
  [' 1,450.5 ', '1450.50'],
  ['£ 7', '7.00'],
  ['0.1', '0.10'],
  ['007.07', '7.07'],
  ['0', '0.00'],
  ['19.99', '19.99'],
  ['999999999.99', '999999999.99'],
  ['1000000000', '1000000000.00'],
  ['1,000,000,000', '1000000000.00'],
  ['12,34', null],
  ['1,2,3', null],
  [',5', null],
  ['5,', null],
  ['12,34.56', null],
  ['£1,45,0', null],
  ['1,45', null],
  ['1,4500', null],
  ['0,450', null],
  ['1 450', null],
  ['1 450.00', null],
  ['12.345', null],
  ['1.', null],
  ['.5', null],
  ['1..2', null],
  ['1.2.3', null],
  ['abc', null],
  ['', null],
  ['£', null],
  ['-5', null],
  ['1e3', null],
  ['NaN', null],
  ['١٢٣', null],
  ['1000000000.01', null],
  ['12345678901', null],
  [' '.repeat(28) + '1450', '1450.00'], // 32 characters: the longest text read as money
  [' '.repeat(29) + '1450', null], // 33: refused before any pattern runs
  ['0'.repeat(30) + '1.00', null],
]

it.each(SHARED_CASES)('parses %j exactly as the server does', (raw, pounds) => {
  expect(parsePoundsInput(raw)).toBe(pounds)
})

it.each(['1,'.repeat(5000), '1'.repeat(10000) + 'x', ' '.repeat(10000) + 'x', '£' + ' '.repeat(10000) + '1'])(
  'refuses adversarial text fast (%#)', (raw) => {
    const start = performance.now()
    expect(parsePoundsInput(raw)).toBeNull()
    expect(performance.now() - start).toBeLessThan(50)
  },
)

it('is exact where floats are not', () => {
  expect(parsePoundsInput('9007199254740993.01')).toBeNull() // too large, and not float-safe
})

it('formats pounds for display', () => {
  expect(formatGBP('1450')).toBe('£1,450.00')
  expect(formatGBP('1450.5')).toBe('£1,450.50')
  expect(formatGBP('0.07')).toBe('£0.07')
  expect(formatGBP('1234567.89')).toBe('£1,234,567.89')
  expect(formatGBP('-12.5')).toBe('-£12.50')
  expect(formatGBP('nonsense')).toBe('nonsense')
})
