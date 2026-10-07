// Money is handled as strings of pounds ("1450.00"). Never use floats for amounts.

const PLAIN = /^\d+(\.\d{1,2})?$/
const GROUPED = /^\d{1,3}(,\d{3})+(\.\d{1,2})?$/
const MAX_POUNDS = '1000000000'

/** "£1,450" -> "1450.00". Returns null when the text isn't an amount. String work only. */
export function parsePoundsInput(s: string): string | null {
  const text = s.replace(/[£\s]/g, '')
  if (!PLAIN.test(text) && !GROUPED.test(text)) return null
  const [whole, pence = ''] = text.replace(/,/g, '').split('.')
  const pounds = whole.replace(/^0+(?=\d)/, '')
  if (pounds.length > MAX_POUNDS.length) return null
  if (pounds.length === MAX_POUNDS.length && (pounds > MAX_POUNDS || (pounds === MAX_POUNDS && /[1-9]/.test(pence)))) return null
  return `${pounds}.${pence.padEnd(2, '0')}`
}

/** "1450.5" -> "£1,450.50". Unrecognised text is returned unchanged. */
export function formatGBP(pounds: string): string {
  const m = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(pounds.trim())
  if (!m) return pounds
  const whole = m[2].replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${m[1]}£${whole}.${(m[3] ?? '').padEnd(2, '0')}`
}
