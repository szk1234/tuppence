import { formatGBP } from './money'

export const HOUSEHOLD_ID = '1'

export type AttrKind = 'choice' | 'text' | 'money' | 'int'
export type AttrDef = { label: string; kind: AttrKind; options?: Record<string, string> }

export const NATIONS: Record<string, string> = { england: 'England', wales: 'Wales', scotland: 'Scotland', northern_ireland: 'Northern Ireland' }
export const TENURES: Record<string, string> = { renting: 'Renting', mortgage: 'Buying with a mortgage', owned: 'Own outright', living_with_family: 'Living with family' }
export const COUNCIL_TAX_BANDS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I']

/** Council tax bands by nation: England and Scotland A–H, Wales A–I, Northern Ireland has none (domestic rates). Unknown: A–I. */
export function councilTaxBands(nation: string | null | undefined): string[] {
  if (nation === 'northern_ireland') return []
  if (nation === 'england' || nation === 'scotland') return COUNCIL_TAX_BANDS.slice(0, 8)
  return COUNCIL_TAX_BANDS
}

export const PERSON_ATTRIBUTES: Record<string, AttrDef> = {
  employment_status: {
    label: 'Work status', kind: 'choice',
    options: { employed: 'Employed', self_employed: 'Self-employed', both: 'Employed and self-employed', retired: 'Retired', student: 'Student', not_working: 'Not working' },
  },
  income_band: {
    label: 'Income band', kind: 'choice',
    options: { under_12570: 'Under £12,570', '12570_50270': '£12,570 to £50,270', '50270_100000': '£50,270 to £100,000', '100000_125140': '£100,000 to £125,140', over_125140: 'Over £125,140' },
  },
}

export const HOUSEHOLD_ATTRIBUTES: Record<string, AttrDef> = {
  nation: { label: 'Nation', kind: 'choice', options: NATIONS },
  postcode_district: { label: 'Postcode district', kind: 'text' },
  housing_tenure: { label: 'Housing', kind: 'choice', options: TENURES },
  housing_monthly_pence: { label: 'Monthly housing cost', kind: 'money' },
  bedrooms: { label: 'Bedrooms', kind: 'int' },
  council_tax_band: { label: 'Council tax band', kind: 'choice', options: Object.fromEntries(COUNCIL_TAX_BANDS.map((b) => [b, `Band ${b}`])) },
}

export function showValue(def: AttrDef | undefined, value: unknown): string {
  if (value === null || value === undefined) return 'Not set'
  if (def?.kind === 'money') return formatGBP(String(value))
  if (def?.options && typeof value === 'string') return def.options[value] ?? value
  return String(value)
}
