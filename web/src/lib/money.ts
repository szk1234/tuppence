// Money is handled as strings of pounds ("1450.00"). Never use floats for amounts.
//
// The text rule matches the server's parse_pounds (src/tuppence/core/money.py), and both are tested against the same
// table: an optional "£" and surrounding spaces; plain digits, or commas only as thousands separators in groups of
// three after the first group ("1,450", "12,345.67"); at most 2 decimal places; at most £1,000,000,000; text longer
// than 32 characters is refused before any pattern runs.

// Longer text is never an amount: refused before any pattern runs, so no input can be slow.
const MAX_TEXT = 32
// One anchored pattern, no nested or overlapping quantifiers (the £ and spaces are stripped with string work first).
const SHAPE = /^(?:[1-9]\d{0,2}(?:,\d{3})+|\d+)(?:\.\d{1,2})?$/
const MAX_POUNDS = '1000000000'

/** "£1,450" -> "1450.00". Returns null when the text isn't an amount. String work only. */
export function parsePoundsInput(s: string): string | null {
  if (s.length > MAX_TEXT) return null
  let text = s.trim()
  if (text.startsWith('£')) text = text.slice(1).trimStart()
  if (!SHAPE.test(text)) return null
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
