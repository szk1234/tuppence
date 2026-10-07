export type Person = { id: string; display_name: string; role: 'adult' | 'child' | 'dependent_adult'; birth_year: number | null; status: string; version: number }

export type Provider = { id: string; name: string; kinds: string[] }
export type Account = {
  id: string; provider: string; provider_name: string; kind: 'current' | 'savings' | 'credit_card'; nickname: string; last4: string | null
  owner_ids: string[]; credit_limit: string | null; purchase_apr: number | null; promo_apr: number | null; promo_end: string | null
  statement_day: number | null; status: 'active' | 'closed'; version: number; joint: boolean
}

export type PayRule = Record<string, unknown>
export type Income = {
  id: string; person_id: string; kind: string; name: string; net_amount: string; account_id: string | null; pay_rule: PayRule
  pay_rule_description: string; variable_components: string[]; next_pay_date: string | null; person_left: boolean
  calendar_assumed: boolean; status: 'active' | 'ended'; version: number
}

export type Debt = {
  id: string; kind: string; lender: string; person_id: string | null; balance: string; balance_date: string; apr: number | null
  monthly_payment: string | null; end_date: string | null; student_loan_plan: string | null; details: Record<string, unknown>
  car_finance_redress_window: boolean; details_removed?: string[]; status: 'active' | 'settled'; version: number
}

export type Goal = {
  id: string; name: string; kind: string; target_amount: string | null; saved_amount: string; target_date: string | null
  priority: 1 | 2 | 3; status: 'active' | 'achieved' | 'abandoned'; version: number
}

export type TimelineEntry = {
  id: number; subject_type: string; subject_id: string; attribute: string; value: unknown
  valid_from: string; valid_to: string | null; source: string; version: number
}

/** The value in force on `day` (YYYY-MM-DD) for one attribute, or null. */
export function valueOn(entries: TimelineEntry[], attribute: string, day: string): unknown {
  const hit = entries
    .filter((e) => e.attribute === attribute && e.valid_from <= day && (e.valid_to === null || e.valid_to > day))
    .sort((a, b) => (a.valid_from < b.valid_from ? 1 : -1))[0]
  return hit ? hit.value : null
}

export type OnboardingStep = { id: string; title: string; status: 'todo' | 'done' | 'skipped' }
export type OnboardingState = {
  steps: OnboardingStep[]; next_step: string | null; started: boolean; finished: boolean; completeness: number
  prompts: Array<{ id: string; text: string; unlocks: string; link: string }>
}
export type Household = { nation: string | null; postcode_district: string | null; version: number }
